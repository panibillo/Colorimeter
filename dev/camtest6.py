#!/usr/bin/env python3
"""
camtest6.py — Minimal single-exposure capture test.

Goal: capture one image at 4-second exposure under the White LED,
save as camtest6/camtest6.jpg, camtest6/camtest6.npy, camtest6/metadata.txt,
and confirm that the camera actually used the requested exposure.

No auto-exposure, no bracketing, no CSV — just one shot with maximum debug output.

Usage:
    python dev/camtest6.py
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np

try:
    from picamera2 import Picamera2
    import libcamera
    PLATFORM = "rpi"
except ImportError:
    sys.exit("picamera2 not available — this script must run on the Raspberry Pi")

try:
    from gpiozero import LED as GpioLED
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("WARNING: gpiozero not available — LED will not be turned on")

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    print("WARNING: cv2 not available — JPEG save will be skipped")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TARGET_EXPOSURE_US = 4_000_000     # 4 seconds
WHITE_LED_PIN      = 22
RAW_FORMAT         = "SBGGR10"
CAMERA_SIZE        = (1640, 1232)
ANALOGUE_GAIN      = 1.0
WB_GAINS_RB        = (1.0, 1.0)

OUT_DIR  = Path("camtest6")
OUT_STEM = "camtest6"

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("camtest6 — 4-second exposure capture test")
    print("=" * 60)
    print(f"  Platform        : {PLATFORM}")
    print(f"  Target exposure : {TARGET_EXPOSURE_US:,} µs  ({TARGET_EXPOSURE_US/1e6:.1f} s)")
    print(f"  Camera size     : {CAMERA_SIZE}")
    print(f"  Raw format      : {RAW_FORMAT}")
    print()

    # ------------------------------------------------------------------
    # Output directory
    # ------------------------------------------------------------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {OUT_DIR.resolve()}")
    print()

    # ------------------------------------------------------------------
    # LED
    # ------------------------------------------------------------------
    led = None
    if GPIO_AVAILABLE:
        try:
            led = GpioLED(WHITE_LED_PIN)
            led.off()
            print(f"White LED (GPIO {WHITE_LED_PIN}) initialized OFF")
        except Exception as exc:
            print(f"WARNING: could not initialize White LED: {exc}")
    else:
        print("Skipping LED (gpiozero unavailable)")
    print()

    # ------------------------------------------------------------------
    # Camera init
    # ------------------------------------------------------------------
    print("Initializing Picamera2 ...")
    picam2 = Picamera2()

    # --- sensor_modes ---------------------------------------------------
    print()
    print("--- sensor_modes ---")
    for i, mode in enumerate(picam2.sensor_modes):
        print(f"  [{i}] {mode}")
    print()

    # --- camera_controls (before configure) ----------------------------
    print("--- camera_controls (before configure) ---")
    for k, v in sorted(picam2.camera_controls.items()):
        print(f"  {k}: {v}")
    print()

    # --- configure ------------------------------------------------------
    # FrameDurationLimits set to (TARGET, TARGET) in the configuration
    # object.  This is required for exposures longer than the default
    # ~1.24 s cap: setting it only in set_controls() is overridden at
    # start(); using (MIN, MAX) lets the driver pick a default within the
    # range (still ~1.24 s).  Pinning both limits to TARGET forces the
    # exact frame duration.
    print("Configuring camera ...")
    config = picam2.create_still_configuration(
        main={"size": CAMERA_SIZE, "format": "RGB888"},
        raw= {"size": CAMERA_SIZE, "format": RAW_FORMAT},
        buffer_count=1,
        controls={
            "FrameDurationLimits": (TARGET_EXPOSURE_US, TARGET_EXPOSURE_US),
        },
    )
    print(f"  Configuration object controls: {config.get('controls', {})}")
    picam2.configure(config)

    # --- controls before start -----------------------------------------
    picam2.set_controls({
        "AeEnable":           False,
        "ExposureTime":       TARGET_EXPOSURE_US,
        "AnalogueGain":       ANALOGUE_GAIN,
        "AwbEnable":          False,
        "ColourGains":        WB_GAINS_RB,
        "NoiseReductionMode": libcamera.controls.draft.NoiseReductionModeEnum.Off,
        "Sharpness":          0.0,
        "Contrast":           1.0,
        "Saturation":         1.0,
    })
    print("  Controls set (AE off, AWB off, manual exposure)")

    # --- start ---------------------------------------------------------
    print("Starting camera ...")
    t_start = time.monotonic()
    picam2.start()
    print(f"  camera.start() returned in {(time.monotonic()-t_start)*1000:.0f} ms")

    # --- camera_controls after start ------------------------------------
    print()
    print("--- camera_controls (after start) ---")
    for k, v in sorted(picam2.camera_controls.items()):
        print(f"  {k}: {v}")
    print()

    # --- settle --------------------------------------------------------
    settle_s = TARGET_EXPOSURE_US / 1e6 + 2.0
    print(f"Settling {settle_s:.1f} s ...")
    time.sleep(settle_s)

    # --- flush two dummy frames ----------------------------------------
    print("Flushing 2 dummy frames ...")
    for n in (1, 2):
        t0 = time.monotonic()
        picam2.capture_array()
        elapsed_ms = (time.monotonic() - t0) * 1000
        print(f"  dummy frame {n}: {elapsed_ms:.0f} ms")
        time.sleep(0.5)

    # --- re-assert controls before live capture ------------------------
    # Re-assert every time: some drivers reset FrameDurationLimits when
    # ExposureTime is set without it.
    print()
    print("Re-asserting controls before capture ...")
    picam2.set_controls({
        "ExposureTime":        TARGET_EXPOSURE_US,
        "FrameDurationLimits": (TARGET_EXPOSURE_US, TARGET_EXPOSURE_US),
    })
    time.sleep(0.5)

    # ------------------------------------------------------------------
    # Turn on LED and wait for warm-up
    # ------------------------------------------------------------------
    if led is not None:
        led.on()
        print("White LED ON")
    else:
        print("[stub] White LED ON")
    time.sleep(0.5)

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------
    print()
    print(f"Capturing (target {TARGET_EXPOSURE_US/1e6:.1f} s) ...")
    t0 = time.monotonic()
    request = picam2.capture_request()
    capture_ms = (time.monotonic() - t0) * 1000
    print(f"  capture_request() took {capture_ms:.0f} ms")

    bgr   = request.make_array("main")
    bayer = request.make_array("raw")
    meta  = request.get_metadata()
    request.release()

    # ------------------------------------------------------------------
    # LED off immediately
    # ------------------------------------------------------------------
    if led is not None:
        led.off()
        led.close()
        print("White LED OFF")
    else:
        print("[stub] White LED OFF")

    # ------------------------------------------------------------------
    # Metadata summary
    # ------------------------------------------------------------------
    applied_us  = int(meta.get("ExposureTime", 0))
    frame_dur   = int(meta.get("FrameDuration", 0))
    analogue_g  = meta.get("AnalogueGain", "?")
    lux         = meta.get("Lux", "?")

    print()
    print("--- capture metadata ---")
    print(f"  Requested ExposureTime : {TARGET_EXPOSURE_US:,} µs  "
          f"({TARGET_EXPOSURE_US/1e6:.3f} s)")
    print(f"  Applied   ExposureTime : {applied_us:,} µs  "
          f"({applied_us/1e6:.3f} s)")
    delta_pct = (applied_us - TARGET_EXPOSURE_US) / max(TARGET_EXPOSURE_US, 1) * 100
    print(f"  Delta                  : {delta_pct:+.1f}%  "
          f"({'OK' if abs(delta_pct) <= 2.0 else 'REJECTED'})")
    print(f"  FrameDuration          : {frame_dur:,} µs  ({frame_dur/1e6:.3f} s)")
    print(f"  AnalogueGain           : {analogue_g}")
    print(f"  Lux                    : {lux}")
    print(f"  capture wall time      : {capture_ms:.0f} ms")
    print()

    # Bayer channel means
    b_mean = float(bayer[0::2, 0::2].mean())
    g_mean = float(np.concatenate([bayer[0::2,1::2].ravel(),
                                   bayer[1::2,0::2].ravel()]).mean())
    r_mean = float(bayer[1::2, 1::2].mean())
    print(f"  Bayer means  B={b_mean:.1f}  G={g_mean:.1f}  R={r_mean:.1f}  "
          f"(max={bayer.max()}  dtype={bayer.dtype})")
    bgr_means = bgr.astype(float).mean(axis=(0,1))
    print(f"  RGB888 means B={bgr_means[0]:.1f}  G={bgr_means[1]:.1f}  "
          f"R={bgr_means[2]:.1f}")
    print()

    # ------------------------------------------------------------------
    # Save files
    # ------------------------------------------------------------------
    jpg_path  = OUT_DIR / f"{OUT_STEM}.jpg"
    npy_path  = OUT_DIR / f"{OUT_STEM}.npy"
    txt_path  = OUT_DIR / "metadata.txt"

    # JPEG
    if CV2_AVAILABLE:
        cv2.imwrite(str(jpg_path), bgr)
        print(f"Saved: {jpg_path}")
    else:
        print(f"SKIPPED: {jpg_path} (cv2 unavailable)")

    # Bayer NPY
    np.save(str(npy_path), bayer)
    print(f"Saved: {npy_path}  (shape {bayer.shape}, dtype {bayer.dtype})")

    # Metadata TXT
    lines = [
        f"requested_exposure_us: {TARGET_EXPOSURE_US}",
        f"applied_exposure_us: {applied_us}",
        f"delta_pct: {delta_pct:.2f}",
        f"frame_duration_us: {frame_dur}",
        f"capture_wall_ms: {capture_ms:.1f}",
        f"analogue_gain: {analogue_g}",
        f"lux: {lux}",
        f"bayer_mean_B: {b_mean:.3f}",
        f"bayer_mean_G: {g_mean:.3f}",
        f"bayer_mean_R: {r_mean:.3f}",
        f"bayer_max: {int(bayer.max())}",
        f"bayer_dtype: {bayer.dtype}",
        f"bayer_shape: {bayer.shape[0]}x{bayer.shape[1]}",
        f"rgb888_mean_B: {bgr_means[0]:.3f}",
        f"rgb888_mean_G: {bgr_means[1]:.3f}",
        f"rgb888_mean_R: {bgr_means[2]:.3f}",
        "--- picamera2_metadata ---",
    ]
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    txt_path.write_text("\n".join(lines) + "\n")
    print(f"Saved: {txt_path}")

    # ------------------------------------------------------------------
    # Done
    # ------------------------------------------------------------------
    print()
    print("Stopping camera ...")
    picam2.stop()
    picam2.close()
    print("Done.")


if __name__ == "__main__":
    main()
