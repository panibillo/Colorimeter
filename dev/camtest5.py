#!/usr/bin/env python3
"""
camtest5.py - Multi-color LED image capture for colorimeter proof-of-concept.

Captures a Bayer + JPG + metadata set for each of 5 LED colors at one or more
exposures. Camera is fixed at Config 5 (1640x1232 dual-stream SBGGR10).

Auto-exposure uses the highest-SNR channel as the reference and iterates until
the recommended exposure is within 1 % of the applied exposure.

Output per color per exposure (up to 3 files):
    {test_name}/{test_name}_{color}_{ms}ms[_over|_under].npy   (Bayer uint16)
    {test_name}/{test_name}_{color}_{ms}ms[_over|_under].jpg   (RGB JPEG)
    {test_name}/{test_name}_{color}_{ms}ms[_over|_under].txt   (metadata)

Usage:
    python camtest5.py <test_name> [--over PCT ...] [--under PCT ...]
                       [-w] [-j]

Options:
    -w / --white_only      Capture White LED only (skip Red, Green, Blue, Violet)
    -j / --jpeg_only       Save JPEG only (skip .npy Bayer file)
    -e / --exposure_ms MS  Use fixed exposure in ms; skip auto-exposure routine
    -s / --save_exposures  Write converged exposures to cam5exp.csv
                           (also written automatically when cam5exp.csv is absent)

Exposure file (cam5exp.csv):
    If cam5exp.csv exists in the working directory, per-color exposures are
    loaded and used as the starting point for auto-exposure, reducing
    convergence iterations. Written automatically on first run or with -s.

Examples:
    python camtest5.py sample1
    python camtest5.py vial_A --over 50 100 --under 50
    python camtest5.py test1  --over 25 50 75 --under 25 50
    python camtest5.py test1  -w -j
    python camtest5.py test1  -e 250
    python camtest5.py test1  -w -e 500 --over 50 --under 50
    python camtest5.py test1  -s
"""

import argparse
import os
import signal
import sys
import time
from pathlib import Path

# Add src/ to path so mock_Picamera2 and other src modules are importable
# when running this script directly from the dev/ directory.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np

try:
    from picamera2 import Picamera2
    import libcamera
    PLATFORM = "rpi"
except ImportError:
    print("picamera2 not available — loading mock_Picamera2 (desktop)")
    from mock_Picamera2 import Picamera2, libcamera
    PLATFORM = "desktop"

try:
    from gpiozero import LED as GpioLED, Device as GpioDevice
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERSION    = "1.3"     # increment each time a fix is applied

RAW_FORMAT = "SBGGR10"

# Camera config 5: dual-stream RGB + Bayer, medium resolution
CAMERA_SIZE    = (1640, 1232)
CAMERA_COMMENT = "Config 5: Medium dual-stream RGB + Bayer (1640x1232)"

# Starting exposure before auto-expose loop
INITIAL_EXPOSURE_US = 100_000   # 100 ms
ANALOGUE_GAIN       = 1.0
WB_GAINS_RB         = (1.0, 1.0)   # Neutral (R-gain, B-gain)

# Auto-exposure parameters
# IMX219 hardware maximum is 1,238,765 µs across all modes and resolutions.
# Using 1,200,000 as the ceiling leaves a small margin below the hard cap.
TARGET_FRACTION     = 0.70     # Target 70 % of full-scale
BAYER_MAX           = 1023     # 10-bit raw pixel maximum (2^10 - 1)
MAX_EXPOSURE_US     = 1_200_000  # Hardware ceiling ~1,238,765 µs
MIN_EXPOSURE_US     = 1_000      # 1 ms floor
EXPOSURE_TOLERANCE  = 0.01       # Converge when change < 1 %

# LED GPIO pin numbers, matching blink.py
LED_PINS = {
    "White":  22,
    "Red":     5,
    "Green":   6,
    "Blue":   26,
    "Violet": 27,
}

# Capture order
LED_ORDER = ["White", "Red", "Green", "Blue", "Violet"]


# ---------------------------------------------------------------------------
# LED controller
# ---------------------------------------------------------------------------

class LedController:
    """
    Controls the 5-color LED lighting board via gpiozero.

    All LEDs are initialized to OFF and driven individually.
    Falls back to a console-logging stub when gpiozero is unavailable,
    so the rest of the code runs unchanged on a desktop.
    """

    def __init__(self) -> None:
        """
        Initialize all LED GPIO pins and ensure they start OFF.

        If any pin reports 'GPIO busy' (left claimed by a previous run that
        did not clean up), the gpiozero pin factory is closed to release
        stale lgpio handles and the initialization is retried once.

        Raises:
            RuntimeError: If any GPIO pin fails to initialize after retry
        """
        self._leds: dict = {}
        if GPIO_AVAILABLE:
            self._init_leds()
            print("  LED controller ready (GPIO)")
        else:
            print("  LED controller ready (stub — no GPIO detected)")

    def _init_leds(self) -> None:
        """Claim all LED pins, retrying once if any pin reports GPIO busy."""
        try:
            for name, pin in LED_PINS.items():
                led = GpioLED(pin)
                led.off()
                self._leds[name] = led
        except Exception as exc:
            if "GPIO busy" not in str(exc):
                raise
            # A previous run left one or more pins claimed without cleanup.
            # Close the gpiozero pin factory (which releases the lgpio
            # chip handle and all its claims) then retry.
            print("  WARNING: GPIO busy — closing pin factory and retrying "
                  "(previous run may not have exited cleanly)")
            try:
                GpioDevice.pin_factory.close()
                for led in self._leds.values():
                    led.close()
            except Exception:
                pass
            self._leds.clear()
            for name, pin in LED_PINS.items():
                led = GpioLED(pin)
                led.off()
                self._leds[name] = led

    def on(self, color: str) -> None:
        """
        Turn on the named LED.

        Args:
            color: One of "White", "Red", "Green", "Blue", "Violet"

        Raises:
            KeyError: If color is not a recognized LED name
        """
        if color not in LED_PINS:
            raise KeyError(f"Unknown LED color: {color!r}")
        if GPIO_AVAILABLE:
            self._leds[color].on()
            print(f"{color} LED ON")
        else:
            print(f"    [stub] {color} LED ON")

    def off(self, color: str) -> None:
        """
        Turn off the named LED.

        Args:
            color: One of "White", "Red", "Green", "Blue", "Violet"
        """
        if GPIO_AVAILABLE:
            self._leds[color].off()
            print(f"{color} LED OFF")
        else:
            print(f"    [stub] {color} LED OFF")

    def all_off(self) -> None:
        """Turn off all LEDs unconditionally."""
        if GPIO_AVAILABLE:
            for led in self._leds.values():
                led.off()
        else:
            print("    [stub] All LEDs OFF")

    def close(self) -> None:
        """Turn off all LEDs and release GPIO resources."""
        self.all_off()
        if GPIO_AVAILABLE:
            for led in self._leds.values():
                led.close()


# ---------------------------------------------------------------------------
# Camera helpers
# ---------------------------------------------------------------------------

def build_camera() -> "Picamera2":
    """
    Create and configure Picamera2 for Config 5 (dual-stream, 1640x1232).

    Camera is set up with:
    - Auto-exposure and AWB disabled (manual control)
    - Fixed analogue gain and neutral colour gains
    - Noise reduction and sharpening disabled (preserve raw signal)
    - Initial exposure INITIAL_EXPOSURE_US
    - Raw stream in SBGGR10 (unpacked uint16, range 0–1023)

    Returns:
        Picamera2 instance (started, ready for capture)

    Raises:
        RuntimeError: If the camera cannot be initialized
    """
    picam2 = Picamera2()
    config = picam2.create_still_configuration(
        main={"size": CAMERA_SIZE, "format": "RGB888"},
        raw= {"size": CAMERA_SIZE, "format": RAW_FORMAT},
        buffer_count=1,
    )
    picam2.configure(config)
    picam2.set_controls({
        "AeEnable":           False,
        "ExposureTime":       INITIAL_EXPOSURE_US,
        "AnalogueGain":       ANALOGUE_GAIN,
        "AwbEnable":          False,
        "ColourGains":        WB_GAINS_RB,
        "NoiseReductionMode": libcamera.controls.draft.NoiseReductionModeEnum.Off,
        "Sharpness":          0.0,
        "Contrast":           1.0,
        "Saturation":         1.0,
    })
    picam2.start()
    time.sleep(3.0)     # Allow sensor to stabilize after start
    return picam2


def _bayer_channel_stats(
    bayer: np.ndarray,
) -> tuple[dict[str, float], dict[str, float]]:
    """
    Extract per-channel means and SNRs from a BGGR Bayer array.

    The BGGR layout assigns one colour per pixel in a 2×2 repeating tile:

        B  G  B  G ...     (even rows)
        G  R  G  R ...     (odd rows)

    Both G sub-arrays (even-row and odd-row) are merged into a single G
    channel, giving twice as many G samples as B or R.

    Args:
        bayer: uint16 array (H, W), 10-bit BGGR values (range 0–1023)

    Returns:
        Tuple (means, snr) where each is a dict mapping "B", "G", "R"
        to a float.  SNR is mean/std; channels with std == 0 return 0.0.
    """
    # Extract per-colour sub-arrays (already float32 for arithmetic)
    b_px = bayer[0::2, 0::2].astype(np.float32).ravel()
    g_px = np.concatenate([bayer[0::2, 1::2].ravel(),
                           bayer[1::2, 0::2].ravel()]).astype(np.float32)
    r_px = bayer[1::2, 1::2].astype(np.float32).ravel()

    means: dict[str, float] = {}
    snr:   dict[str, float] = {}
    for name, px in (("B", b_px), ("G", g_px), ("R", r_px)):
        m = float(px.mean())
        s = float(px.std())
        means[name] = m
        snr[name]   = m / s if s > 0.0 else 0.0
    return means, snr


def _set_exposure_and_settle(picam2, exposure_us: int) -> None:
    """
    Apply a new ExposureTime and wait for the pipeline to fully adopt it.

    Sets the exposure, sleeps for at least one full frame duration, then
    discards two frames so the next probe sees only the new setting.

    Args:
        picam2:      Picamera2 instance (started)
        exposure_us: Desired exposure in microseconds

    The initial sleep must be at least as long as one full frame at the new
    exposure so that any in-progress old-exposure frame completes before we
    start draining dummy frames.  The IMX219 hardware maximum is ~1.24 s, so
    MAX_EXPOSURE_US is set well within that limit.
    """
    frame_secs   = exposure_us / 1_000_000
    initial_wait = max(2.0, frame_secs + 1.0)
    print(f"      → set {exposure_us:,} µs ({frame_secs:.2f} s)  "
          f"settle {initial_wait:.1f} s + 2 frames")
    picam2.set_controls({"ExposureTime": exposure_us})

    # Wait for at least one full frame at the new exposure before draining.
    time.sleep(initial_wait)

    # Discard two frames so the next probe sees only the new setting.
    picam2.capture_array()
    time.sleep(0.5)
    picam2.capture_array()
    time.sleep(0.5)



def _probe_exposure(picam2) -> dict:
    """
    Capture a probe Bayer frame and return exposure estimation data.

    Captures one raw Bayer frame atomically with its metadata so that the
    reported ExposureTime and the pixel values are guaranteed to be from
    the same frame.  Computes per-channel means and SNRs from the raw
    10-bit Bayer data, and recommends a new exposure to reach
    TARGET_FRACTION of BAYER_MAX using the highest-SNR channel as guide.

    Using raw Bayer (rather than the ISP-processed RGB888 stream) gives a
    linear pixel-vs-exposure relationship, so a single scale factor
    (target / mean) converges in one step without overrelaxation.

    Using capture_request() is critical: separate calls to capture_metadata()
    and capture_array() return different frames, causing the reported
    ExposureTime to lag behind the actual image brightness and making the
    convergence loop oscillate.

    Args:
        picam2: Picamera2 instance (started, dual-stream configured)

    Returns:
        Dict with keys:
            "applied_us":     exposure time reported by the camera (µs)
            "recommended_us": exposure to reach TARGET_FRACTION (µs, clamped)
            "best_channel":   which channel was used as guide ("B", "G", "R")
            "channel_means":  dict of per-channel raw means (float, 0–1023)
            "channel_snr":    dict of per-channel SNR values (float)
            "scale_factor":   recommended / applied ratio
            "clamped":        True if min/max clamp was applied
    """
    # Atomic capture: metadata and Bayer data are from the same frame.
    t0         = time.monotonic()
    request    = picam2.capture_request()
    probe_ms   = (time.monotonic() - t0) * 1000
    bayer      = request.make_array("raw")
    meta       = request.get_metadata()
    request.release()
    applied_us = int(meta["ExposureTime"])
    frame_dur  = meta.get("FrameDuration", 0)

    means, snr = _bayer_channel_stats(bayer)

    # Highest-SNR channel is the most reliable for exposure scaling
    best_ch      = max(snr, key=snr.get)
    current_mean = means[best_ch]
    target_mean  = TARGET_FRACTION * BAYER_MAX

    if current_mean < 1.0:
        print("    WARNING: image nearly black — check illumination and lens")
        return {
            "applied_us":     applied_us,
            "recommended_us": applied_us,
            "best_channel":   best_ch,
            "channel_means":  means,
            "channel_snr":    snr,
            "scale_factor":   1.0,
            "clamped":        False,
        }

    scale  = target_mean / current_mean
    rec_us = int(applied_us * scale)

    clamped = False
    if rec_us > MAX_EXPOSURE_US:
        rec_us  = MAX_EXPOSURE_US
        clamped = True
        print(f"    WARNING: clamped to {MAX_EXPOSURE_US:,} µs "
              f"(illumination may be too dim)")
    elif rec_us < MIN_EXPOSURE_US:
        rec_us  = MIN_EXPOSURE_US
        clamped = True
        print(f"    WARNING: clamped to {MIN_EXPOSURE_US:,} µs "
              f"(illumination may be very bright)")

    return {
        "applied_us":     applied_us,
        "recommended_us": rec_us,
        "best_channel":   best_ch,
        "channel_means":  means,
        "channel_snr":    snr,
        "scale_factor":   scale,
        "clamped":        clamped,
        "probe_ms":       probe_ms,
        "frame_dur_us":   frame_dur,
    }


def converge_exposure(
    picam2,
    initial_us: int = INITIAL_EXPOSURE_US,
    over_relax: float = 1.0,
) -> int:
    """
    Auto-expose iteratively until the recommended exposure is stable.

    Sets the camera to initial_us, then probes and applies the recommendation
    repeatedly until the change is within EXPOSURE_TOLERANCE or 5 iterations
    are exhausted.

    The camera's ISP applies a gamma curve to the RGB888 output so pixel
    values grow as exposure^(1/γ) rather than linearly.  A plain linear
    recommendation (over_relax=1.0) therefore always undershoots the target
    mean.  over_relax raises the scale factor to a power > 1, compensating:

        rec_us = applied × (target_mean / current_mean) ^ over_relax

    With γ ≈ 0.55–0.65 (observed), over_relax ≈ 1.5 is close to ideal.

    Args:
        picam2:     Picamera2 instance (started)
        initial_us: Starting exposure in microseconds (default INITIAL_EXPOSURE_US).
                    Pass a saved value from a previous session to reduce
                    the number of iterations needed.
        over_relax: Exponent applied to the linear scale factor (default 1.5).
                    1.0 = no overrelaxation (original behaviour).
                    Values above 1.0 accelerate convergence when the
                    pixel-vs-exposure response is sub-linear.

    Returns:
        Converged nominal exposure in microseconds

    Example:
        >>> nominal_us = converge_exposure(picam2, initial_us=312_000,
        ...                               over_relax=1.5)
        >>> print(f"Nominal: {nominal_us:,} µs")
    """
    MAX_ITER = 5
    print(f"    Auto-exposure (v{VERSION}):")
    rec_us      = initial_us   # Fallback if loop never executes
    prev_set_us = None         # What we requested in the previous iteration

    for i in range(MAX_ITER):
        result  = _probe_exposure(picam2)
        applied = result["applied_us"]
        best_ch = result["best_channel"]
        means   = result["channel_means"]
        snr     = result["channel_snr"]

        # Show whether the camera accepted the exposure we set last iteration.
        if prev_set_us is not None:
            delta_pct = (applied - prev_set_us) / max(prev_set_us, 1) * 100
            if abs(delta_pct) <= 2.0:
                accept_tag = f"  accept Δ{delta_pct:+.1f}%"
            else:
                accept_tag = f"  REJECTED set={prev_set_us:,} Δ{delta_pct:+.1f}%"
        else:
            accept_tag = ""

        # Linear recommendation from _probe_exposure
        linear_rec_us = result["recommended_us"]

        # Apply overrelaxation: raise the scale factor to over_relax.
        # Clamped to [MIN, MAX] in case the power pushes past the ceiling.
        linear_scale = linear_rec_us / max(applied, 1)
        rec_us = int(applied * (linear_scale ** over_relax))
        rec_us = max(MIN_EXPOSURE_US, min(MAX_EXPOSURE_US, rec_us))

        probe_ms    = result["probe_ms"]
        frame_dur   = result["frame_dur_us"]
        relax_tag   = f"  ×{over_relax:.2g}" if over_relax != 1.0 else ""
        print(f"      [{i+1}] applied={applied:,} µs{accept_tag}  "
              f"recommended={rec_us:,} µs{relax_tag}  "
              f"guide={best_ch} "
              f"(SNR {snr[best_ch]:.1f})  "
              f"means B={means['B']:.1f} G={means['G']:.1f} R={means['R']:.1f}  "
              f"probe={probe_ms:.0f} ms  FrameDur={frame_dur:,} µs")

        change = abs(rec_us - applied) / max(applied, 1)
        if change <= EXPOSURE_TOLERANCE:
            print(f"      Converged: {rec_us:,} µs "
                  f"({rec_us / 1000:.1f} ms)")
            return rec_us

        # Apply recommendation and re-probe on next iteration
        _set_exposure_and_settle(picam2, rec_us)
        prev_set_us = rec_us

    print(f"      Did not fully converge after {MAX_ITER} iterations; "
          f"using {rec_us:,} µs")
    return rec_us


def load_exposures_csv(fpath: Path) -> dict[str, int]:
    """
    Load per-color initial exposures from a CSV file.

    Expected format (header + one row per color):
        color,exposure_us
        White,312000
        Red,198000
        ...

    Unknown color names are ignored. Missing colors fall back to
    INITIAL_EXPOSURE_US at runtime.

    Args:
        fpath: Path to the CSV file

    Returns:
        Dict mapping color name to exposure in microseconds.
        Empty dict if the file cannot be parsed.

    Example:
        >>> exps = load_exposures_csv(Path("cam5exp.csv"))
        >>> print(exps["Blue"])
        483000
    """
    exposures: dict[str, int] = {}
    try:
        with open(fpath, newline="") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("color"):
                    continue   # Skip header and blank lines
                parts = line.split(",")
                if len(parts) != 2:
                    continue
                color, val = parts[0].strip(), parts[1].strip()
                if color in LED_PINS:
                    exposures[color] = int(val)
        print(f"  Loaded {len(exposures)} initial exposures from {fpath}")
    except OSError as exc:
        print(f"  WARNING: could not read {fpath}: {exc}")
    return exposures


def save_exposures_csv(fpath: Path, exposures: dict[str, int]) -> None:
    """
    Write per-color nominal exposures to a CSV file.

    Output format:
        color,exposure_us
        White,312000
        Red,198000
        ...

    Colors are written in LED_ORDER sequence.

    Args:
        fpath:     Path to write (overwritten if exists)
        exposures: Dict mapping color name to exposure in microseconds

    Raises:
        OSError: If the file cannot be written
    """
    lines = ["color,exposure_us"]
    for color in LED_ORDER:
        if color in exposures:
            lines.append(f"{color},{exposures[color]}")
    fpath.write_text("\n".join(lines) + "\n")
    print(f"  Saved {len(exposures)} exposures to {fpath}")


def _capture_dual_stream(picam2) -> tuple[np.ndarray, np.ndarray, dict]:
    """
    Capture one synchronized RGB + Bayer frame pair atomically.

    Args:
        picam2: Picamera2 instance (started, dual-stream configured)

    Returns:
        Tuple of:
            bgr:   uint8 array (H, W, 3), BGR channel order
            bayer: uint16 array (H, W), BGGR Bayer pattern (10-bit values)
            meta:  dict of Picamera2 metadata for this frame
    """
    request = picam2.capture_request()
    bgr     = request.make_array("main")
    bayer   = request.make_array("raw")
    meta    = request.get_metadata()
    request.release()
    return bgr, bayer, meta


# ---------------------------------------------------------------------------
# File saving
# ---------------------------------------------------------------------------

def _bracket_suffix(is_over: bool | None) -> str:
    """
    Return the filename bracket suffix for an exposure category.

    Args:
        is_over: True = over-exposure bracket, False = under-exposure bracket,
                 None = nominal (auto-exposed)

    Returns:
        "_over", "_under", or ""
    """
    if is_over is True:
        return "_over"
    if is_over is False:
        return "_under"
    return ""


def save_capture(
    out_dir: Path,
    test_name: str,
    color: str,
    exposure_us: int,
    bgr: np.ndarray,
    bayer: np.ndarray,
    meta: dict,
    is_over: bool | None,
    jpeg_only: bool = False,
) -> dict[str, Path]:
    """
    Save one capture as JPEG, Bayer NPY, and metadata TXT.

    File naming:
        {test_name}_{color}_{ms}ms[_over|_under].{ext}

    Args:
        out_dir:     Directory to save files into (must already exist)
        test_name:   Test session identifier
        color:       LED color name (e.g. "Blue")
        exposure_us: Exposure time applied for this frame (µs)
        bgr:         uint8 array (H, W, 3), BGR channel order
        bayer:       uint16 array (H, W), BGGR Bayer pattern
        meta:        Picamera2 metadata dict for this frame
        is_over:     True / False / None for over / under / nominal bracket
        jpeg_only:   If True, skip .npy Bayer file (default False)

    Returns:
        Dict mapping saved extensions ("jpg", optionally "npy", "txt")
        to their Path objects

    Raises:
        IOError: If any file write fails
    """
    ms     = round(exposure_us / 1000)
    suffix = _bracket_suffix(is_over)
    stem   = f"{test_name}_{color}_{ms}ms{suffix}"

    paths: dict[str, Path] = {}

    # JPEG
    jpg_path = out_dir / f"{stem}.jpg"
    if CV2_AVAILABLE:
        cv2.imwrite(str(jpg_path), bgr)
    else:
        jpg_path.write_bytes(b"")
        print(f"    WARNING: cv2 not available; {jpg_path.name} is a placeholder")
    paths["jpg"] = jpg_path

    # Bayer NPY (uint16, 10-bit BGGR values) — skipped when jpeg_only
    if not jpeg_only:
        npy_path = out_dir / f"{stem}.npy"
        np.save(str(npy_path), bayer)
        paths["npy"] = npy_path

    # Metadata TXT
    txt_path = out_dir / f"{stem}.txt"
    _write_metadata(txt_path, test_name, color, exposure_us, is_over, bgr, bayer, meta)
    paths["txt"] = txt_path

    saved_exts = " / ".join(f".{k}" for k in paths)
    print(f"    Saved: {stem}  ({saved_exts})")
    return paths


def _write_metadata(
    fpath: Path,
    test_name: str,
    color: str,
    exposure_us: int,
    is_over: bool | None,
    bgr: np.ndarray,
    bayer: np.ndarray,
    cam_meta: dict,
) -> None:
    """
    Write capture metadata to a text file in 'key: value' format.

    Includes test identification, exposure parameters, per-channel image
    statistics, Bayer array statistics, and the full Picamera2 metadata block.

    Args:
        fpath:       Path of the output file
        test_name:   Test session identifier
        color:       LED color name
        exposure_us: Requested exposure time (µs)
        is_over:     True / False / None for over / under / nominal
        bgr:         uint8 BGR image array
        bayer:       uint16 Bayer array
        cam_meta:    Raw Picamera2 metadata dict
    """
    bracket = {True: "over", False: "under", None: "nominal"}[is_over]
    ms      = round(exposure_us / 1000)

    lines = [
        f"test_name: {test_name}",
        f"led_color: {color}",
        f"bracket: {bracket}",
        f"requested_exposure_us: {exposure_us}",
        f"requested_exposure_ms: {ms}",
        f"image_width: {bgr.shape[1]}",
        f"image_height: {bgr.shape[0]}",
        f"bgr_mean_B: {bgr[:, :, 0].mean():.3f}",
        f"bgr_mean_G: {bgr[:, :, 1].mean():.3f}",
        f"bgr_mean_R: {bgr[:, :, 2].mean():.3f}",
        f"bgr_std_B: {bgr[:, :, 0].std():.3f}",
        f"bgr_std_G: {bgr[:, :, 1].std():.3f}",
        f"bgr_std_R: {bgr[:, :, 2].std():.3f}",
        f"bayer_dtype: {bayer.dtype}",
        f"bayer_shape: {bayer.shape[0]}x{bayer.shape[1]}",
        f"bayer_min: {int(bayer.min())}",
        f"bayer_max: {int(bayer.max())}",
        f"bayer_mean: {bayer.mean():.3f}",
        "--- picamera2_metadata ---",
    ]
    for key, val in cam_meta.items():
        lines.append(f"{key}: {val}")

    fpath.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Per-color capture sequence
# ---------------------------------------------------------------------------

def capture_color(
    picam2,
    leds: LedController,
    out_dir: Path,
    test_name: str,
    color: str,
    over_pcts: list[float],
    under_pcts: list[float],
    jpeg_only: bool = False,
    fixed_exposure_us: int | None = None,
    initial_exposure_us: int = INITIAL_EXPOSURE_US,
    over_relax: float = 1.0,
) -> tuple[list[dict[str, Path]], int]:
    """
    Capture a complete bracketed image set under one LED color.

    Sequence:
      1. Turn LED on; wait 0.5 s for warm-up.
      2. Determine nominal exposure: auto-expose (default) or use
         fixed_exposure_us directly (skips convergence loop).
      3. Capture and save nominal image.
      4. Capture and save each over-exposure bracket (sorted, lowest first).
      5. Capture and save each under-exposure bracket (sorted, lowest first).
      6. Turn LED off immediately.

    Args:
        picam2:            Picamera2 instance (started)
        leds:              LedController instance (all LEDs off on entry)
        out_dir:           Directory to save files into
        test_name:         Test session identifier
        color:             LED color name ("White", "Red", "Green", "Blue", "Violet")
        over_pcts:         Over-exposure percentages (e.g. [50.0, 100.0])
        under_pcts:        Under-exposure percentages (e.g. [50.0])
        jpeg_only:         If True, skip .npy Bayer file (default False)
        fixed_exposure_us: If set, use this exposure in µs and skip auto-exposure
                           (default None → run convergence loop)
        initial_exposure_us: Starting point for the auto-exposure loop in µs.
                           Ignored when fixed_exposure_us is set.
                           (default INITIAL_EXPOSURE_US)
        over_relax:        Overrelaxation exponent passed to converge_exposure.
                           (default 1.5)

    Returns:
        Tuple of:
            results:    List of save-result dicts (one per exposure)
            nominal_us: Converged (or fixed) nominal exposure in microseconds

    Raises:
        KeyError: If color is not a recognized LED name
    """
    results: list[dict[str, Path]] = []
    nominal_us = initial_exposure_us   # Populated below; pre-set for finally

    try:
        # Turn on LED and allow it to warm up before any capture
        print(f"  Turning on {color} LED (0.5 s warm-up)...")
        leds.on(color)
        time.sleep(0.5)

        if fixed_exposure_us is not None:
            # Use caller-specified exposure; skip auto-expose loop
            nominal_us = fixed_exposure_us
            print(f"    Fixed exposure: {nominal_us:,} µs "
                  f"({nominal_us / 1000:.1f} ms) — auto-expose skipped")
            _set_exposure_and_settle(picam2, nominal_us)
        else:
            # Start auto-exposure from the provided initial value
            _set_exposure_and_settle(picam2, initial_exposure_us)

            # Auto-expose
            nominal_us = converge_exposure(picam2, initial_exposure_us,
                                           over_relax)

        # Apply nominal and capture
        print(f"    Capturing nominal ({nominal_us:,} µs / "
              f"{nominal_us / 1000:.1f} ms)...")
        _set_exposure_and_settle(picam2, nominal_us)
        bgr, bayer, meta = _capture_dual_stream(picam2)
        results.append(
            save_capture(out_dir, test_name, color, nominal_us,
                         bgr, bayer, meta, None, jpeg_only)
        )

        # Over-exposure brackets
        for pct in sorted(over_pcts):
            exp_us = int(min(nominal_us * (1.0 + pct / 100.0), MAX_EXPOSURE_US))
            print(f"    Capturing over +{pct:.0f}% ({exp_us:,} µs / "
                  f"{exp_us / 1000:.1f} ms)...")
            _set_exposure_and_settle(picam2, exp_us)
            bgr, bayer, meta = _capture_dual_stream(picam2)
            results.append(
                save_capture(out_dir, test_name, color, exp_us,
                             bgr, bayer, meta, True, jpeg_only)
            )

        # Under-exposure brackets
        for pct in sorted(under_pcts):
            exp_us = int(max(nominal_us * (1.0 - pct / 100.0), MIN_EXPOSURE_US))
            print(f"    Capturing under -{pct:.0f}% ({exp_us:,} µs / "
                  f"{exp_us / 1000:.1f} ms)...")
            _set_exposure_and_settle(picam2, exp_us)
            bgr, bayer, meta = _capture_dual_stream(picam2)
            results.append(
                save_capture(out_dir, test_name, color, exp_us,
                             bgr, bayer, meta, False, jpeg_only)
            )

    finally:
        # LED off immediately when done with this color, even on error
        leds.off(color)
        print(f"  {color} LED OFF")

    return results, nominal_us


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """
    Parse CLI arguments and run the multi-color capture session.

    Creates output directory, initializes camera and LEDs, then captures
    each LED color in order. Guarantees LEDs are off and camera is stopped
    on exit (normal or error).

    Returns:
        0 on success, 1 on error or user interrupt
    """
    exp_csv = Path("cam5exp.csv")

    parser = argparse.ArgumentParser(
        description="Colorimeter POC: multi-color LED image capture.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python camtest5.py sample1\n"
            "  python camtest5.py vial_A --over 50 100 --under 50\n"
            "  python camtest5.py test1  --over 25 50 75 --under 25 50\n"
            "  python camtest5.py test1  -w -j\n"
        ),
    )
    parser.add_argument(
        "test_name",
        help="Session identifier used in filenames and output directory name",
    )
    parser.add_argument(
        "--over",
        type=float, nargs="+", default=[], metavar="PCT",
        help="Over-exposure bracket percentages (e.g. --over 50 100)",
    )
    parser.add_argument(
        "--under",
        type=float, nargs="+", default=[], metavar="PCT",
        help="Under-exposure bracket percentages (e.g. --under 50)",
    )
    parser.add_argument(
        "-w", "--white_only",
        action="store_true",
        help="Capture White LED only (skip Red, Green, Blue, Violet)",
    )
    parser.add_argument(
        "-j", "--jpeg_only",
        action="store_true",
        help="Save JPEG and metadata only; skip .npy Bayer file",
    )
    parser.add_argument(
        "-e", "--exposure_ms",
        type=float, default=None, metavar="MS",
        help="Fixed exposure in milliseconds; skips auto-exposure routine",
    )
    parser.add_argument(
        "-s", "--save_exposures",
        action="store_true",
        help="Save converged exposures to cam5exp.csv (also saved automatically "
             "when cam5exp.csv does not exist)",
    )
    parser.add_argument(
        "-r", "--relax",
        type=float, default=1.0, metavar="ω",
        help="Overrelaxation exponent for auto-exposure (default 1.0). "
             "1.0 = linear, exact for raw Bayer data. "
             ">1 may help if ISP-processed streams are used instead.",
    )
    parser.add_argument(
        "-u", "--use_saved_exposures",
        action="store_true",
        help="Skip auto-exposure; use per-color exposures from cam5exp.csv. "
             "Falls back to 500 ms per color if the file does not exist.",
    )
    args = parser.parse_args()

    test_name  = args.test_name
    over_pcts  = sorted(args.over)
    under_pcts = sorted(args.under)
    white_only = args.white_only
    jpeg_only  = args.jpeg_only
    fixed_exposure_us = (int(args.exposure_ms * 1000)
                         if args.exposure_ms is not None else None)
    save_exposures      = args.save_exposures
    over_relax          = args.relax
    use_saved_exposures = args.use_saved_exposures

    colors      = ["White"] if white_only else LED_ORDER
    n_exposures = 1 + len(over_pcts) + len(under_pcts)
    n_captures  = len(colors) * n_exposures
    files_per   = 2 if jpeg_only else 3   # jpg + txt, or jpg + npy + txt
    n_files     = n_captures * files_per

    print(f"\nColorimeter Capture Session  (camtest5 v{VERSION})")
    print(f"{'='*52}")
    print(f"  Test name   : {test_name}")
    print(f"  Platform    : {PLATFORM}")
    print(f"  Camera      : {CAMERA_COMMENT}")
    print(f"  Colors      : {', '.join(colors)}")
    print(f"  Exposures   : 1 nominal", end="")
    if over_pcts:
        pct_str = ", ".join(f"+{p:.0f}%" for p in over_pcts)
        print(f"  +  {len(over_pcts)} over  ({pct_str})", end="")
    if under_pcts:
        pct_str = ", ".join(f"-{p:.0f}%" for p in under_pcts)
        print(f"  +  {len(under_pcts)} under ({pct_str})", end="")
    print()
    print(f"  Captures    : {n_captures}  ({len(colors)} colors × {n_exposures} exposures)")
    print(f"  Files       : {n_files}  ({n_captures} captures × {files_per} file types)")
    if white_only:
        print(f"  Mode        : white LED only")
    if jpeg_only:
        print(f"  Mode        : JPEG only (no .npy)")
    if fixed_exposure_us is not None:
        print(f"  Exposure    : fixed {fixed_exposure_us / 1000:.1f} ms "
              f"(auto-expose skipped)")
    elif use_saved_exposures:
        print(f"  Exposure    : saved values from {exp_csv} "
              f"(auto-expose skipped; fallback 500 ms)")
    else:
        print(f"  Relax ω     : {over_relax:.2g}")

    # Load per-color initial exposures from CSV if it exists
    csv_existed = exp_csv.exists()
    saved_exposures: dict[str, int] = load_exposures_csv(exp_csv) if csv_existed else {}
    if csv_existed:
        print(f"  Exp file    : {exp_csv} (loaded)")
    else:
        print(f"  Exp file    : {exp_csv} not found — will save after session")

    out_dir = Path(test_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Output dir  : {out_dir.resolve()}")
    print()

    leds   = LedController()
    picam2 = None

    # Handle Ctrl-Z (SIGTSTP): run GPIO/camera cleanup before suspending so
    # the next run does not encounter "GPIO busy" on restart.
    def _sigtstp_handler(*_):
        try:
            leds.all_off()
            leds.close()
            if picam2 is not None:
                picam2.stop()
        except Exception:
            pass
        signal.signal(signal.SIGTSTP, signal.SIG_DFL)   # restore default
        os.kill(os.getpid(), signal.SIGTSTP)             # re-raise to suspend

    signal.signal(signal.SIGTSTP, _sigtstp_handler)

    try:
        print("Initializing camera...")
        picam2 = build_camera()
        print(f"  Camera ready ({CAMERA_COMMENT})")
        print()

        all_results: dict[str, list] = {}
        nominal_exposures: dict[str, int] = {}   # Collected for CSV save

        for color in colors:
            print(f"\n{'─'*52}")
            print(f"Color: {color}    (camtest5 v{VERSION})")
            print(f"{'─'*52}")
            initial_us = saved_exposures.get(color, INITIAL_EXPOSURE_US)
            # -u: use saved exposure as a fixed value; fall back to 500 ms
            # if this color has no saved entry.  -e overrides -u.
            if use_saved_exposures and fixed_exposure_us is None:
                color_fixed_us = saved_exposures.get(color, 500_000)
            else:
                color_fixed_us = fixed_exposure_us
            results, nominal_us = capture_color(
                picam2, leds, out_dir, test_name,
                color, over_pcts, under_pcts, jpeg_only,
                color_fixed_us, initial_us, over_relax,
            )
            all_results[color] = results
            nominal_exposures[color] = nominal_us

        # Save exposures if file was absent or -s flag given.
        # Skipped when -e is active: a fixed override is not a measured
        # auto-exposure and should not overwrite saved values.
        if (fixed_exposure_us is None and not use_saved_exposures
                and (not csv_existed or save_exposures)):
            save_exposures_csv(exp_csv, nominal_exposures)

        # Session summary
        total_captures = sum(len(v) for v in all_results.values())
        total_files    = sum(
            len(paths) for results in all_results.values()
            for paths in results
        )
        print(f"\n{'='*52}")
        print(f"Session complete: {test_name}")
        print(f"{'='*52}")
        print(f"  Captures : {total_captures}")
        print(f"  Files    : {total_files}")
        print(f"  Directory: {out_dir.resolve()}")
        print()
        print("To copy files to desktop:")
        print(f"  scp -r pi@<rpi-ip>:~/{test_name}/ images/")
        print(f"  rsync -avz pi@<rpi-ip>:~/{test_name}/ images/{test_name}/")

        return 0

    except KeyboardInterrupt:
        print("\n\nInterrupted by user (Ctrl-C)")
        return 1

    except Exception as exc:
        import traceback
        print(f"\nFatal error: {exc}")
        traceback.print_exc()
        return 1

    finally:
        # Guarantee all LEDs off and camera stopped, regardless of exit path
        leds.all_off()
        leds.close()
        if picam2 is not None:
            try:
                picam2.stop()
                picam2.close()
            except Exception:
                pass
        print("\nCamera stopped. All LEDs off.")


if __name__ == "__main__":
    sys.exit(main())
