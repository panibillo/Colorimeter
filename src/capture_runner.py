"""
capture_runner.py — multi-exposure ROI capture workflow, driveable step by step.
--------------------------------------------------------------------------------
Holds the capture workflow previously embedded in dev/camtest8.py's main().
The workflow is exposed as a generator so a caller can pump it one step at a
time and render progress between steps, instead of blocking for the several
minutes a full ten-LED suite takes.

Three-phase workflow:
  Phase 0: White LED on — find a calibrated unsaturated starting exposure.
           Target Bayer channel is determined per-LED in Phase 2 (not here).
  Phase 1: Camera focus (manual diopter or autofocus).
  Phase 2: Per-LED: determine target channel, compute 7 exposures, capture
           and save lit frames, then LED-off dark frames at the same exposures.

Exposure bracketing targets (50% of 10-bit saturation = 512):
  e0: e3 / E3_RATIO                                    (fixed ratio)
  e1: 89th-percentile pixel in ROI_SAMPLE → 512
  e2: 66th-percentile pixel in ROI_SAMPLE → 512
  e3: 50th-percentile pixel in ROI_SAMPLE → 512  (median)
  e4: 33rd-percentile pixel in ROI_SAMPLE → 512
  e5: 11th-percentile pixel in ROI_SAMPLE → 512
  e6: e3 * E3_RATIO                                    (fixed ratio)

Hardware is injected, never constructed here: the caller owns the lifetime of
the camera and the LED bank. The runner turns off whichever LED it switched on
when the generator finishes or is abandoned, but does not close either device.

Usage:
    from capture_runner import CaptureRunner, CaptureConfig

    runner = CaptureRunner(cam, leds, CaptureConfig(led_start=1, led_end=10))
    for progress in runner.run():
        if not progress.transient:
            print(progress.message)
    print(runner.out_path, runner.results)

Output: a single HDF5 archive per capture set. Where it is written and what
it is called are declared in config/instrument.yaml under `capture:`, and
reached through config_manager — this module contains no paths or filename
patterns of its own. A caller can override the directory per run through
CaptureConfig.out_dir. The archive holds the
ROI crops, per-LED metadata, capture statistics, and the e3 JPEG. Nothing is
staged on disk first — frames go from the camera into the archive in memory,
which keeps a ten-LED suite to one file instead of several hundred.

The archive layout is defined by capture_h5.CaptureSuiteWriter, shared with
the converter that rebuilds an archive from a legacy camtest8 directory.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

import numpy as np

import config_manager as config
import exposure_repo
from bayer_cam import BayerCam
from capture_h5 import CaptureSuiteWriter
from capture_profile import CaptureProfiler

try:
    import libcamera as _libcamera  # type: ignore[import-untyped]
except ImportError:
    _libcamera = None

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_EXPOSURE_MS  = 10

# Exactly two LED-state-change waits per color: once after switching it on,
# before any lit exposures, and once after switching it off, before any dark
# exposures. All exposures for a state (every lit e0-e6, every dark e0-e6)
# are captured back to back within that one wait — the LED is not toggled
# again in between.
LED_ON_WARMUP_S      = 2.0          # seconds after LED on, before lit captures
LED_OFF_SETTLE_S     = 2.0          # seconds after LED off, before dark captures

MAX_SETTLE_FRAMES    = 15           # max captures while waiting for exposure to settle
SETTLE_TOLERANCE     = 0.05         # 5 % tolerance for exposure-settle check
WAIT_TICK_S          = 0.05         # pump interval while waiting out a warmup

OUT_STEM = "camtest8"

# ROI definitions — image space (x, y, w, h) for 2304×1296 capture
ROI_SAMPLE = (1150, 790,  68, 68)
ROI_WHITE  = (1150, 496, 136, 68)
ROI_BLACK  = ((1150, 380, 68, 68), (1150, 900, 68, 68))
ROI_BOUND  = (1090, 360, 160, 630)

# Exposure analysis constants (10-bit sensor: 0–1023)
HALF_SAT        = 512      # target: 50% of full scale
SAT_THRESH      = 950      # p90 > this → oversaturated
MAX_DESAT_TRIES = 3
PERCENTILES     = [89, 66, 50, 33, 11]    # e1, e2, e3, e4, e5 percentile targets
EXP_LABELS      = ["e0", "e1", "e2", "e3", "e4", "e5", "e6"]  # ordered capture sequence

WHITE_LED_CH   = 10    # LEDBank channel for white LED
MAX_AF_DIOPTER = 7.5   # cap autofocus: if result exceeds this, set manually to this

# e3 convergence: iterate until |p50 - HALF_SAT| / HALF_SAT < E3_TOLERANCE
E3_TOLERANCE = 0.10    # 10 % tolerance for p50 convergence
E3_MAX_ITER  = 5       # maximum refinement iterations
E3_RATIO     = 1.5     # e0 = e3 / E3_RATIO;  e6 = e3 * E3_RATIO

# Captures performed per LED, used only to estimate progress fraction.
_CAPTURES_PER_LED = 2 * len(EXP_LABELS)


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------

# Capture paths (CaptureConfig.mode):
#   "calibration" — the adaptive path: find the ideal exposure e* per LED and
#                   bracket it (the historical workflow). The caller persists the
#                   discovered e* as exposure groups via exposure_repo.
#   "normal"      — capture only the exposures stored in the `exposure` table for
#                   the chosen group (images-per-LED), no e* search.
MODE_CALIBRATION = "calibration"
MODE_NORMAL      = "normal"

# TODO(undecided): consider moving exposure calibration WHOLLY to the
# Menu → Capture screen as a standalone operation, run outside the context of a
# sample reading — rather than exposing "calibration" as a capture path that a
# sample's ReadPhase could invoke. Launching a calibration run from the Menu
# (see the arbiter's capture screen) has proven a cleaner fit than bolting a
# second capture path onto a regular reading. Not yet decided — both paths stay
# for now.


@dataclass
class CaptureConfig:
    """Parameters for one capture run."""

    exposure_ms:    float = DEFAULT_EXPOSURE_MS
    led_start:      int = 1
    led_end:        int = 1
    focus_diopter:  "float | None" = None
    # Which capture path to run, and (normal only) which stored exposure group
    # to use — its name is the number of images per LED ('1'..'5').
    mode:           str = MODE_CALIBRATION
    exposure_group: str = "1"
    # Declared in config/instrument.yaml under `capture:`. default_factory,
    # not a plain default, so the location is resolved when a config is built
    # rather than baked in when this module is first imported.
    out_dir:       Path = field(default_factory=config.capture_dir)

    @property
    def initial_us(self) -> int:
        """Initial exposure in microseconds."""
        return int(self.exposure_ms * 1000)

    @property
    def led_count(self) -> int:
        """Number of LEDs this run will visit."""
        return max(0, self.led_end - self.led_start + 1)


@dataclass(frozen=True)
class Progress:
    """
    One observable step of a capture run.

    Attributes:
        phase:     "setup" | "calibrate" | "focus" | "capture" | "done".
        message:   Human-readable one-line description of the current step.
        transient: True for repeated updates during a wait, which a caller
                   may refresh in place or skip entirely rather than log.
        led_index: 1-based position of the current LED within the run, or 0.
        led_total: Total LEDs in the run.
        color:     Current LED color name, or "".
        step:      Short label of the current operation (e.g. "e3", "dark e3").
        fraction:  Monotonic estimate of overall completion, 0.0–1.0.
    """

    phase:     str
    message:   str
    transient: bool = False
    led_index: int = 0
    led_total: int = 0
    color:     str = ""
    step:      str = ""
    fraction:  float = 0.0


@dataclass
class LedResult:
    """Outcome of capturing one LED color."""

    color:          str
    led_channel:    int
    target_channel: str
    exposures_us:   list = field(default_factory=list)
    metadata:       dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ROI helpers  (pure functions — no hardware)
# ---------------------------------------------------------------------------

def _channel_roi(roi: tuple) -> tuple:
    """Convert image-space (x, y, w, h) to bayer-channel space (÷2 each coord)."""
    x, y, w, h = roi
    return (x // 2, y // 2, w // 2, h // 2)


def _roi_pixels(layer: np.ndarray, croi: tuple) -> np.ndarray:
    """Extract and flatten pixels from a channel layer using channel-space ROI."""
    cx, cy, cw, ch = croi
    return layer[cy:cy + ch, cx:cx + cw].ravel()


def _channel_means(layers: dict, croi: tuple) -> dict:
    """Return {ch_name: mean} for all channels in the given channel-space ROI."""
    return {ch: float(_roi_pixels(layers[ch], croi).mean()) for ch in layers}


def _select_target_channel(layers: dict, white_croi: tuple) -> str:
    """Return the channel key ('B','Gb','Gr','R') with highest mean in ROI_WHITE."""
    means = _channel_means(layers, white_croi)
    chosen = max(means, key=means.__getitem__)
    log.debug(f"    target channel: {chosen}  (means: "
              + "  ".join(f"{k}={v:.1f}" for k, v in sorted(means.items()))
              + ")")
    return chosen


def _check_saturated(layers: dict, target_ch: str, white_croi: tuple) -> bool:
    """Return True if p90 of target channel in ROI_WHITE exceeds SAT_THRESH."""
    pix = _roi_pixels(layers[target_ch], white_croi)
    p90 = float(np.percentile(pix, 90))
    log.debug(f"    saturation check: p90({target_ch})={p90:.1f}  threshold={SAT_THRESH}")
    return p90 > SAT_THRESH


def _check_saturated_any(layers: dict, white_croi: tuple) -> bool:
    """Return True if p90 of ANY channel in ROI_WHITE exceeds SAT_THRESH."""
    return any(
        float(np.percentile(_roi_pixels(layers[ch], white_croi), 90)) > SAT_THRESH
        for ch in layers
    )


def _estimate_e3(pix: np.ndarray, current_us: int) -> int:
    """
    Estimate e3 exposure from p50 of a reference pixel array.

    Scales current_us so the median pixel would land at HALF_SAT.
    Returns the estimated e3 in microseconds (floor at 1000 µs).
    """
    p50 = float(np.percentile(pix, 50))
    p50 = max(p50, 1.0)   # guard divide-by-zero for very dark frames
    e3_us = max(1000, int(current_us * HALF_SAT / p50))
    log.debug(f"    e3 estimate: p50={p50:.1f} at {current_us}µs → {e3_us}µs "
              f"({e3_us // 1000}ms)")
    return e3_us


def _compute_exposures_from_e3(pix_e3: np.ndarray, e3_us: int) -> "tuple[list, list]":
    """
    Compute all 7 capture exposures from the pixel distribution at e3.

    e1, e2, e4, e5 are percentile-scaled; e0 and e6 are fixed-ratio brackets
    around e3, which is passed in already refined.

    Returns:
        (exposures_us, pcts) where exposures_us is ordered to match
        EXP_LABELS and pcts is [p89, p66, p50, p33, p11].
    """
    pcts = np.percentile(pix_e3, PERCENTILES)    # [p89, p66, p50, p33, p11]
    p89, p66, p50, p33, p11 = [max(float(v), 1.0) for v in pcts]
    log.debug(f"    ROI_SAMPLE at e3 ({e3_us}µs): "
              f"p89={p89:.1f}  p66={p66:.1f}  p50={p50:.1f}  p33={p33:.1f}  p11={p11:.1f}")

    e0_us = max(1000, int(e3_us / E3_RATIO))
    e1_us = max(1000, int(e3_us * HALF_SAT / p89))
    e2_us = max(1000, int(e3_us * HALF_SAT / p66))
    e4_us = max(1000, int(e3_us * HALF_SAT / p33))
    e5_us = max(1000, int(e3_us * HALF_SAT / p11))
    e6_us = int(e3_us * E3_RATIO)

    log.debug(f"    e0 = e3/{E3_RATIO} = {e3_us}/{E3_RATIO:.1f} = {e0_us}µs ({e0_us // 1000}ms)")
    log.debug(f"    e1: p89={p89:.1f}  →  {e1_us}µs ({e1_us // 1000}ms)")
    log.debug(f"    e2: p66={p66:.1f}  →  {e2_us}µs ({e2_us // 1000}ms)")
    log.debug(f"    e3: {e3_us}µs ({e3_us // 1000}ms)  [p50={p50:.1f}]")
    log.debug(f"    e4: p33={p33:.1f}  →  {e4_us}µs ({e4_us // 1000}ms)")
    log.debug(f"    e5: p11={p11:.1f}  →  {e5_us}µs ({e5_us // 1000}ms)")
    log.debug(f"    e6 = e3×{E3_RATIO} = {e3_us}×{E3_RATIO:.1f} = {e6_us}µs ({e6_us // 1000}ms)")

    return [e0_us, e1_us, e2_us, e3_us, e4_us, e5_us, e6_us], pcts.tolist()


# ---------------------------------------------------------------------------
# Frame preparation  (in memory — nothing reaches disk except the archive)
# ---------------------------------------------------------------------------

def _roi_bound_layers(bayer: np.ndarray, pattern: str) -> dict:
    """
    Crop ROI_BOUND from a full bayer frame and split it into 4 channels.

    Returns:
        {ch_name: ndarray}, each of shape
        (ROI_BOUND_h//2, ROI_BOUND_w//2) = (315, 80).
    """
    x, y, w, h = ROI_BOUND
    crop = bayer[y:y + h, x:x + w]
    return BayerCam.extract_bayer_layers(crop, pattern)


def _encode_jpeg(rgb: np.ndarray) -> bytes:
    """
    Encode a BGR frame as JPEG bytes, without going through a file.

    Uses the same cv2 encoder and default quality as BayerCam.save_frame(),
    so an in-memory JPEG is identical to one that script would have written.
    """
    try:
        import cv2
    except ImportError:
        raise ImportError("cv2 is required to encode the e3 JPEG; "
                          "install opencv-python")
    ok, buf = cv2.imencode(".jpg", rgb)
    if not ok:
        raise RuntimeError("cv2.imencode failed to encode the e3 frame")
    return buf.tobytes()


def _build_capture_info(
    rgb: np.ndarray, bayer: np.ndarray, meta: dict,
    lens_position: "float | None" = None,
) -> dict:
    """
    Build the image-statistics record for one frame.

    Mirrors the .txt sidecar BayerCam.save_frame() writes, including its
    rounding, so an archive written live matches one rebuilt from a legacy
    capture directory by the converter.

    Returns:
        {"image_stats": {...}, "picamera2_metadata": {...}}
    """
    image_stats = {
        "image_width":  int(rgb.shape[1]),
        "image_height": int(rgb.shape[0]),
        "bgr_mean_B":   round(float(rgb[:, :, 0].mean()), 3),
        "bgr_mean_G":   round(float(rgb[:, :, 1].mean()), 3),
        "bgr_mean_R":   round(float(rgb[:, :, 2].mean()), 3),
        "bgr_std_B":    round(float(rgb[:, :, 0].std()), 3),
        "bgr_std_G":    round(float(rgb[:, :, 1].std()), 3),
        "bgr_std_R":    round(float(rgb[:, :, 2].std()), 3),
        "bayer_dtype":  str(bayer.dtype),
        "bayer_shape":  f"{bayer.shape[0]}x{bayer.shape[1]}",
        "bayer_min":    int(bayer.min()),
        "bayer_max":    int(bayer.max()),
        "bayer_mean":   round(float(bayer.mean()), 3),
    }
    if lens_position is not None:
        image_stats["lens_position"] = round(float(lens_position), 4)

    return {"image_stats": image_stats, "picamera2_metadata": dict(meta)}


# ---------------------------------------------------------------------------
# Camera helpers
# ---------------------------------------------------------------------------

def _set_and_settle(cam, exposure_us: int,
                    prof: "CaptureProfiler | None" = None,
                    ) -> "tuple[np.ndarray, np.ndarray, dict]":
    """
    Set exposure and capture frames until metadata confirms it has been applied.

    After calling set_exposure(), the Picamera2 pipeline takes at least one
    frame cycle to adopt the new ExposureTime.  This function keeps capturing
    until the metadata ExposureTime is within SETTLE_TOLERANCE of the request,
    or MAX_SETTLE_FRAMES is exhausted.

    Args:
        cam:         Camera to drive.
        exposure_us: Requested exposure in microseconds.
        prof:        Optional profiler; when given, this counts one settle-run
                     and times every capture_frame() call it makes.
    """
    cam.set_exposure(exposure_us)
    if prof is not None:
        prof.count("camera.settle")
    last = None
    for frame_num in range(MAX_SETTLE_FRAMES):
        if prof is not None:
            _t0 = time.perf_counter()
            rgb, bayer, meta = cam.capture_frame()
            prof.record_frame(time.perf_counter() - _t0,
                              int(meta.get("ExposureTime", 0)))
        else:
            rgb, bayer, meta = cam.capture_frame()
        applied = int(meta.get("ExposureTime", 0))
        last = (rgb, bayer, meta)
        if applied > 0:
            delta = abs(applied - exposure_us) / max(exposure_us, 1)
            if delta <= SETTLE_TOLERANCE:
                log.debug(f"    settled: {applied}µs ≈ {exposure_us}µs "
                          f"after {frame_num + 1} frame(s)")
                return rgb, bayer, meta
        log.debug(f"    settling [{frame_num + 1}/{MAX_SETTLE_FRAMES}]: "
                  f"requested={exposure_us}µs  applied={applied}µs")
    applied = int(last[2].get("ExposureTime", 0))  # type: ignore[index]
    log.warning(f"    exposure did not settle after {MAX_SETTLE_FRAMES} frames; "
                f"last applied={applied}µs  requested={exposure_us}µs")
    return last  # type: ignore[return-value]


def _set_manual_lens(cam, diopters: float) -> None:
    """Apply a manual lens position directly via libcamera controls."""
    if _libcamera is None:
        raise RuntimeError("libcamera not importable — cannot set manual focus")
    if cam.camera is None:
        raise RuntimeError("Camera not initialized — call setup_camera() first")
    cam.camera.set_controls({
        "AfMode":       _libcamera.controls.AfModeEnum.Manual,
        "LensPosition": float(diopters),
    })
    cam.lens_position = float(diopters)
    time.sleep(0.5)   # allow VCM to settle


# ---------------------------------------------------------------------------
# CaptureRunner
# ---------------------------------------------------------------------------

class CaptureRunner:
    """
    Drives a multi-exposure capture suite one observable step at a time.

    Hardware is injected: the caller creates and owns the camera and LED bank.
    Call run() and iterate the returned generator; each yielded Progress marks
    a point at which the caller may repaint a display or service input.
    """

    def __init__(self, cam, leds, config: "CaptureConfig | None" = None):
        """
        Args:
            cam:    A BayerCam (or compatible) instance, not yet set up.
            leds:   An LEDBank (or compatible) instance, already open.
            config: Run parameters; defaults to a single-LED run.
        """
        self._cam    = cam
        self._leds   = leds
        self.config  = config or CaptureConfig()

        # Populated as the run proceeds.
        self.out_path: "Path | None" = None   # the .h5 archive being written
        self.suite_id: str = ""
        self.results: list = []

        # The archive being written. Unlike the camera and LED bank, this is
        # created and owned by the runner, so the runner also closes it.
        self._writer: "CaptureSuiteWriter | None" = None

        # Progress bookkeeping.
        self._led_index      = 0
        self._captures_done  = 0
        self._current_color  = ""
        self._lit_on_channel: "int | None" = None
        # Captures per LED, for the completion estimate. The calibration path
        # does a fixed lit+dark bracket; the normal path does lit+dark of the
        # chosen group, set when that run starts.
        self._captures_per_led = _CAPTURES_PER_LED

        # Wall-clock profiler (on unless COLORIMETER_PROFILE is false-y). Records
        # every frame, wait, numpy block, and archive write; its summary is
        # logged when run() finishes. See capture_profile.CaptureProfiler.
        self.prof = CaptureProfiler()

    # ── Progress helpers ─────────────────────────────────────────────

    def _fraction(self) -> float:
        """Monotonic completion estimate over the whole run."""
        total = self.config.led_count
        if total <= 0:
            return 0.0
        done = (self._led_index - 1) + (self._captures_done / self._captures_per_led)
        return max(0.0, min(1.0, done / total))

    def _progress(self, phase: str, message: str, **kwargs) -> Progress:
        """Build a Progress carrying the current LED and completion context."""
        color = kwargs.pop("color", None)
        if color is None:
            color = self._current_color
        return Progress(
            phase=phase,
            message=message,
            led_index=self._led_index,
            led_total=self.config.led_count,
            color=color,
            fraction=self._fraction(),
            **kwargs,
        )

    def _wait(self, seconds: float, what: str) -> Iterator[Progress]:
        """
        Sleep for `seconds`, yielding transient progress so the caller can
        keep a display responsive instead of freezing for the whole wait.

        The countdown is rendered in whole seconds: ticks come round every
        WAIT_TICK_S, and a caller that repaints only on a changed message
        then redraws about once a second instead of twenty times.
        """
        t0 = time.perf_counter()
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.prof.add("wait.led", time.perf_counter() - t0)
                return
            yield self._progress(
                "capture", f"{what} — {remaining:.0f}s", transient=True, step="wait")
            time.sleep(min(WAIT_TICK_S, remaining))

    # ── Profiled camera / numpy helpers ──────────────────────────────────

    def _settle(self, exposure_us: int) -> "tuple[np.ndarray, np.ndarray, dict]":
        """set_exposure + capture until the pipeline adopts it, timing frames."""
        return _set_and_settle(self._cam, exposure_us, self.prof)

    def _extract(self, bayer: np.ndarray, pattern: str) -> dict:
        """Split a full bayer frame into channel layers, timed as cpu.extract."""
        with self.prof.time("cpu.extract"):
            return BayerCam.extract_bayer_layers(bayer, pattern)

    def _bound_layers(self, bayer: np.ndarray, pattern: str) -> dict:
        """Crop ROI_BOUND and split into channel layers, timed as cpu.extract."""
        with self.prof.time("cpu.extract"):
            return _roi_bound_layers(bayer, pattern)

    # ── Focus ────────────────────────────────────────────────────────

    def _set_focus(self) -> "tuple[float, str, float | None]":
        """
        Set camera focus and return (final_pos, mode, raw_af_pos).

        If config.focus_diopter is given → set that position directly
        (mode='manual'). Otherwise → run autofocus; if the result exceeds
        MAX_AF_DIOPTER, cap it there (mode='autofocus_capped'), else accept
        it as-is (mode='autofocus').

        raw_af_pos is the unmodified autofocus result, or None for manual.
        """
        cam = self._cam
        focus_diopter = self.config.focus_diopter

        if focus_diopter is not None:
            if not cam.has_autofocus:
                log.warning("Manual focus requested but camera has no VCM — ignored")
                return 0.0, "no_vcm", None
            _set_manual_lens(cam, focus_diopter)
            log.info(f"Focus: manual  {focus_diopter:.2f} diopters")
            return float(focus_diopter), "manual", None

        raw_pos = cam.focus(force=False, leds=self._leds)
        if not cam.has_autofocus:
            return 0.0, "no_vcm", None

        if raw_pos > MAX_AF_DIOPTER:
            log.info(f"Focus: autofocus returned {raw_pos:.4f} diopters "
                     f"> cap {MAX_AF_DIOPTER} — capping")
            _set_manual_lens(cam, MAX_AF_DIOPTER)
            return MAX_AF_DIOPTER, "autofocus_capped", raw_pos

        log.info(f"Focus: autofocus  {raw_pos:.4f} diopters (within cap)")
        return raw_pos, "autofocus", raw_pos

    # ── Run ──────────────────────────────────────────────────────────

    def run(self) -> Iterator[Progress]:
        """
        Execute the capture suite, yielding a Progress at each observable step.

        The generator switches LEDs on and off as it goes; abandoning it
        (via close(), an exception, or garbage collection) turns off any LED
        still lit and closes the archive, leaving whatever was captured so
        far readable. The camera and LED bank are left open for the caller
        to dispose of.

        Yields:
            Progress values. Those with transient=True are repeated updates
            during a wait and carry no new information beyond the countdown.
        """
        try:
            if str(self.config.mode).lower() == MODE_NORMAL:
                yield from self._run_normal()
            else:
                yield from self._run()
        finally:
            self._all_off()
            self._close_writer()
            if self.prof.enabled:
                log.info("capture profile:\n%s", self.prof.summary())

    def _close_writer(self) -> None:
        """Close the archive if one is open. Never raises."""
        if self._writer is None:
            return
        try:
            self._writer.close()
        except Exception:
            log.exception("CaptureRunner: failed to close %s", self.out_path)
        finally:
            self._writer = None

    def _all_off(self) -> None:
        """Turn off whichever LED this run switched on. Never raises."""
        if self._lit_on_channel is None:
            return
        try:
            self._leds.set_channel(self._lit_on_channel, False)
        except Exception:
            log.exception("CaptureRunner: failed to turn off LED "
                          f"channel {self._lit_on_channel}")
        finally:
            self._lit_on_channel = None

    def _led_on(self, channel: int) -> None:
        """Switch an LED on and remember it for cleanup."""
        self._leds.set_channel(channel, True)
        self._lit_on_channel = channel

    def _led_off(self, channel: int) -> None:
        """Switch an LED off and clear the cleanup record."""
        self._leds.set_channel(channel, False)
        self._lit_on_channel = None

    def _run(self) -> Iterator[Progress]:
        cfg  = self.config
        cam  = self._cam
        leds = self._leds

        # Naming lives in config/instrument.yaml under `capture:`.
        self.suite_id = config.capture_suite_id()
        cfg.out_dir.mkdir(parents=True, exist_ok=True)
        self.out_path = cfg.out_dir / config.capture_archive_name(self.suite_id)
        self.results = []

        white_croi  = _channel_roi(ROI_WHITE)
        sample_croi = _channel_roi(ROI_SAMPLE)

        # ── Camera init ──────────────────────────────────────────────
        cam.set_exposure(cfg.initial_us)
        with self.prof.time("camera.setup"):
            cam.setup_camera()
        pattern = cam.bayer_pattern
        assert cam.config_size is not None, "setup_camera() must set config_size"
        cam_w, cam_h = cam.config_size

        yield self._progress(
            "setup",
            f"Camera {cam.model_description}  pattern={pattern}  {cam_w}×{cam_h}")

        # ================================================================
        # Phase 0 — White LED: find a calibrated unsaturated exposure
        # Target channel is NOT selected here; it is per-LED in Phase 2.
        # ================================================================
        self.prof.set_section("phase0")
        yield self._progress("calibrate", "Phase 0: white LED exposure calibration")

        self._led_on(WHITE_LED_CH)
        yield from self._wait(LED_ON_WARMUP_S, "White LED on, warming up")

        current_us     = cfg.initial_us
        phase0_retries = 0

        for attempt in range(MAX_DESAT_TRIES):
            log.debug(f"Phase 0 attempt {attempt + 1}: exposure={current_us}µs")
            _, bayer, _ = self._settle(current_us)
            layers = self._extract(bayer, pattern)
            with self.prof.time("cpu.analyze"):
                means     = _channel_means(layers, white_croi)
                saturated = _check_saturated_any(layers, white_croi)
            log.debug("  ROI_WHITE means: "
                      + "  ".join(f"{k}={v:.1f}" for k, v in sorted(means.items())))
            if not saturated:
                log.debug(f"  Not saturated — accepting {current_us}µs "
                          f"as calibrated exposure")
                break
            new_us = max(current_us // 2, 1000)
            log.debug(f"  Saturated — halving {current_us}µs → {new_us}µs")
            yield self._progress(
                "calibrate",
                f"ROI_WHITE saturated — halving {current_us:,} → {new_us:,} µs")
            current_us = new_us
            phase0_retries += 1

        calibrated_us = current_us
        self._led_off(WHITE_LED_CH)
        yield self._progress(
            "calibrate",
            f"Calibrated initial exposure: {calibrated_us:,} µs "
            f"({calibrated_us // 1000} ms)")

        # ================================================================
        # Phase 1 — Focus
        # ================================================================
        self.prof.set_section("focus")
        yield self._progress("focus", "Phase 1: focus")
        with self.prof.time("focus"):
            focus_pos, focus_mode, raw_af_pos = self._set_focus()
        yield self._progress("focus", f"Focus: {focus_pos:.2f} diopters ({focus_mode})")

        # ================================================================
        # Phase 2 — Per-LED multi-exposure capture and save
        # ================================================================
        yield self._progress("capture", "Phase 2: per-LED multi-exposure capture")

        # Opened here rather than at the top of the run so a failure during
        # calibration or focus leaves no empty archive behind.
        w = self._writer = CaptureSuiteWriter(self.out_path, suite_id=self.suite_id)

        for led in range(cfg.led_start, cfg.led_end + 1):
            self._led_index     += 1
            self._captures_done  = 0
            color = leds.color_name(led)
            self._current_color = color
            self.prof.set_section(color)
            log.info(f"=== LED {led}: {color} ===")

            yield self._progress("capture", f"--- {leds.label(led)} ---")

            self._led_on(led)
            yield from self._wait(LED_ON_WARMUP_S, f"{color} LED on, warming up")

            # Per-LED desat loop + target channel selection
            current_us      = calibrated_us
            target_ch: str  = ""
            ref_means: dict = {}
            layers: dict    = {}
            per_led_retries = 0

            for attempt in range(MAX_DESAT_TRIES):
                log.debug(f"  {color} attempt {attempt + 1}: exposure={current_us}µs")
                _, bayer, _ = self._settle(current_us)
                layers = self._extract(bayer, pattern)
                with self.prof.time("cpu.analyze"):
                    ref_means = _channel_means(layers, white_croi)
                    target_ch = _select_target_channel(layers, white_croi)
                    saturated = _check_saturated(layers, target_ch, white_croi)
                if not saturated:
                    log.debug(f"  {color}: not saturated — target_ch={target_ch}  "
                              f"exposure={current_us}µs accepted")
                    break
                new_us = max(current_us // 2, 1000)
                log.debug(f"  {color}: saturated — halving {current_us}µs → {new_us}µs")
                yield self._progress(
                    "capture", f"Saturated — halving {current_us:,} → {new_us:,} µs")
                current_us = new_us
                per_led_retries += 1

            if not target_ch:
                raise RuntimeError(
                    f"Failed to determine target channel for LED '{color}'")

            means_str = "  ".join(f"{k}={v:.1f}" for k, v in sorted(ref_means.items()))
            log.info(f"  {color}: target_ch={target_ch}  ref_exposure={current_us}µs")
            yield self._progress(
                "capture", f"Target channel : {target_ch}  (ROI_WHITE: {means_str})")

            # ── Stage 1: estimate e3 from reference capture ───────────
            with self.prof.time("cpu.analyze"):
                pix_ref = _roi_pixels(layers[target_ch], sample_croi)
                e3_us   = _estimate_e3(pix_ref, current_us)
            log.info(f"  {color}: initial e3 estimate = {e3_us}µs ({e3_us // 1000}ms)")

            # ── Stage 2: refine e3 until p50 ≈ HALF_SAT ──────────────
            e3_history: list = []
            layers_e3 = layers     # fallback if loop never runs

            for iteration in range(E3_MAX_ITER):
                log.info(f"  {color} e3 refine [{iteration + 1}/{E3_MAX_ITER}]: "
                         f"capturing at {e3_us}µs ({e3_us // 1000}ms)")
                yield self._progress(
                    "capture",
                    f"e3 refine [{iteration + 1}/{E3_MAX_ITER}]: "
                    f"{e3_us}µs ({e3_us // 1000}ms)",
                    step="e3-refine")
                _, bayer_e3, _ = self._settle(e3_us)
                layers_e3  = self._extract(bayer_e3, pattern)
                with self.prof.time("cpu.analyze"):
                    pix_e3     = _roi_pixels(layers_e3[target_ch], sample_croi)
                    actual_p50 = float(np.percentile(pix_e3, 50))
                error      = abs(actual_p50 - HALF_SAT) / HALF_SAT
                e3_history.append({
                    "iteration":      iteration + 1,
                    "exposure_us":    e3_us,
                    "measured_p50":   round(actual_p50, 2),
                    "error_fraction": round(error, 4),
                    "converged":      error <= E3_TOLERANCE,
                })
                log.debug(f"    p50={actual_p50:.1f}  error={error:.3f}  "
                          f"target={HALF_SAT}  "
                          f"{'CONVERGED' if error <= E3_TOLERANCE else 'refining'}")
                if error <= E3_TOLERANCE:
                    yield self._progress(
                        "capture",
                        f"e3 converged : {e3_us}µs ({e3_us // 1000}ms)  "
                        f"p50={actual_p50:.1f}  ({iteration + 1} iter)",
                        step="e3-refine")
                    break
                new_e3 = max(1000, int(e3_us * HALF_SAT / max(actual_p50, 1.0)))
                log.debug(f"    adjusting e3: {e3_us}µs → {new_e3}µs")
                e3_us = new_e3
            else:
                log.warning(f"  {color}: e3 did not converge in {E3_MAX_ITER} iterations")
                yield self._progress(
                    "capture",
                    f"e3 not converged after {E3_MAX_ITER} iterations — using {e3_us}µs",
                    step="e3-refine")

            # ── Stage 3: compute e0…e6 from e3 capture spread ─────────
            with self.prof.time("cpu.analyze"):
                pix_e3 = _roi_pixels(layers_e3[target_ch], sample_croi)
                exposures_us, pcts = _compute_exposures_from_e3(pix_e3, e3_us)
            e0_us, e1_us, e2_us, _, e4_us, e5_us, e6_us = exposures_us
            exp_summary = ", ".join(
                f"{lbl}={exp // 1000}ms"
                for lbl, exp in zip(EXP_LABELS, exposures_us))
            yield self._progress("capture", f"Exposures      : {exp_summary}")

            meta_json: dict = {
                "camtest8_version": "4",
                "timestamp": self.suite_id,
                "led_channel": led,
                "led_color": color,
                "led_label": leds.label(led),
                "camera_model": cam.model_description,
                "bayer_pattern": pattern,
                "config_size": [cam_w, cam_h],
                "focus_diopters": focus_pos,
                "focus_mode": focus_mode,
                "focus_max_af_diopter": MAX_AF_DIOPTER,
                "autofocus_raw_diopters": raw_af_pos,
                "target_bayer_channel": target_ch,
                "roi_white_channel_means": ref_means,
                "phase0_calibrated_exposure_us": calibrated_us,
                "phase0_desat_retries": phase0_retries,
                "per_led_desat_retries": per_led_retries,
                "ref_exposure_us": current_us,
                "e3_refinement": e3_history,
                "e3_final_us": e3_us,
                "e3_ratio": E3_RATIO,
                "roi_sample_percentiles_at_e3": {
                    str(p): round(v, 2) for p, v in zip(PERCENTILES, pcts)
                },
                "computed_exposures": {
                    "e0": {"type": "fixed_ratio", "ratio": f"e3/{E3_RATIO}",
                           "exposure_us": e0_us, "exposure_ms": e0_us // 1000},
                    "e1": {"type": "percentile", "target_percentile": PERCENTILES[0],
                           "percentile_value": round(pcts[0], 2),
                           "exposure_us": e1_us, "exposure_ms": e1_us // 1000},
                    "e2": {"type": "percentile", "target_percentile": PERCENTILES[1],
                           "percentile_value": round(pcts[1], 2),
                           "exposure_us": e2_us, "exposure_ms": e2_us // 1000},
                    "e3": {"type": "percentile", "target_percentile": PERCENTILES[2],
                           "percentile_value": round(pcts[2], 2),
                           "exposure_us": e3_us, "exposure_ms": e3_us // 1000},
                    "e4": {"type": "percentile", "target_percentile": PERCENTILES[3],
                           "percentile_value": round(pcts[3], 2),
                           "exposure_us": e4_us, "exposure_ms": e4_us // 1000},
                    "e5": {"type": "percentile", "target_percentile": PERCENTILES[4],
                           "percentile_value": round(pcts[4], 2),
                           "exposure_us": e5_us, "exposure_ms": e5_us // 1000},
                    "e6": {"type": "fixed_ratio", "ratio": f"e3*{E3_RATIO}",
                           "exposure_us": e6_us, "exposure_ms": e6_us // 1000},
                },
                "applied_exposures_us": {},
            }

            # ── Lit frames ────────────────────────────────────────────
            yield self._progress("capture", "Lit captures:")
            e3_frame: "tuple | None" = None

            for exp_label, exp_us in zip(EXP_LABELS, exposures_us):
                log.info(f"  {color} {exp_label}: setting exposure {exp_us}µs "
                         f"({exp_us // 1000}ms)")
                rgb, bayer, meta = self._settle(exp_us)
                applied = int(meta.get("ExposureTime", 0))
                meta_json["applied_exposures_us"][exp_label] = applied
                log.info(f"  {color} {exp_label}: applied={applied}µs  "
                         f"delta={applied - exp_us:+d}µs")

                layers_out = self._bound_layers(bayer, pattern)
                with self.prof.time("io.hdf5"):
                    w.add_lit(color, exp_label, layers_out, exposure_us=exp_us)

                if exp_label == "e3":    # keep the median frame for the JPEG
                    e3_frame = (rgb, bayer, meta)

                self._captures_done += 1
                yield self._progress(
                    "capture",
                    f"    stored lit {exp_label} ({exp_us // 1000}ms)",
                    step=exp_label)

            # LED off, then dark frames at the same exposures
            self._led_off(led)
            yield from self._wait(LED_OFF_SETTLE_S, f"{color} LED off, settling")

            yield self._progress("capture", "Dark captures:")
            for exp_label, exp_us in zip(EXP_LABELS, exposures_us):
                log.info(f"  {color} dark {exp_label}: exposure {exp_us}µs")
                _, bayer, _ = self._settle(exp_us)

                # Dark frames depend only on exposure, so an exposure already
                # captured for an earlier LED is reused rather than restored.
                layers_out = self._bound_layers(bayer, pattern)
                with self.prof.time("io.hdf5"):
                    stored = w.add_dark(exp_label, layers_out, exposure_us=exp_us)
                    w.link_dark(color, exp_label, exposure_us=exp_us)

                self._captures_done += 1
                yield self._progress(
                    "capture",
                    f"    {'stored' if stored else 'reused'} dark {exp_label} "
                    f"({exp_us // 1000}ms)",
                    step=f"dark {exp_label}")

            # ── Per-LED metadata, statistics and JPEG ─────────────────
            with self.prof.time("io.hdf5"):
                w.add_led(color, meta_json, led_channel=led)
            if e3_frame is not None:
                rgb_e3, bayer_e3, meta_e3 = e3_frame
                info = _build_capture_info(
                    rgb_e3, bayer_e3, meta_e3, lens_position=cam.lens_position)
                with self.prof.time("io.jpeg"):
                    jpeg = _encode_jpeg(rgb_e3)
                with self.prof.time("io.hdf5"):
                    w.add_capture_info(color, info)
                    w.add_jpeg(color, jpeg,
                               filename=f"{OUT_STEM}_{color}_e3.jpg")
            yield self._progress("capture", f"    stored metadata for {color}")

            self.results.append(LedResult(
                color=color,
                led_channel=led,
                target_channel=target_ch,
                exposures_us=exposures_us,
                metadata=meta_json,
            ))

        self._close_writer()
        self._current_color = ""
        yield Progress(
            phase="done",
            message=f"Done. Output: {self.out_path}",
            led_index=self._led_index,
            led_total=cfg.led_count,
            fraction=1.0,
        )

    # ── Normal path ────────────────────────────────────────────────────
    #
    # "Use only what calibration stored." No white-LED calibration and no e*
    # search: for each LED the exposures come straight from the `exposure`
    # table for the chosen group, so the strip is lit the minimum number of
    # times. Focus still runs (it is not stored). If a LED has no rows for the
    # group, one fallback exposure is used so the run still completes.

    def _run_normal(self) -> Iterator[Progress]:
        cfg  = self.config
        cam  = self._cam
        leds = self._leds
        group = str(cfg.exposure_group)

        self.suite_id = config.capture_suite_id()
        cfg.out_dir.mkdir(parents=True, exist_ok=True)
        self.out_path = cfg.out_dir / config.capture_archive_name(self.suite_id)
        self.results = []

        # ── Camera init ──────────────────────────────────────────────
        cam.set_exposure(cfg.initial_us)
        with self.prof.time("camera.setup"):
            cam.setup_camera()
        pattern = cam.bayer_pattern
        assert cam.config_size is not None, "setup_camera() must set config_size"
        cam_w, cam_h = cam.config_size
        yield self._progress(
            "setup",
            f"Camera {cam.model_description}  pattern={pattern}  {cam_w}×{cam_h}  "
            f"(normal, group {group})")

        # ── Focus (still needed; exposures are stored, focus is not) ──
        self.prof.set_section("focus")
        yield self._progress("focus", "Phase 1: focus")
        with self.prof.time("focus"):
            focus_pos, focus_mode, raw_af_pos = self._set_focus()
        yield self._progress("focus", f"Focus: {focus_pos:.2f} diopters ({focus_mode})")

        # ── Per-LED capture of the stored exposures ──────────────────
        yield self._progress(
            "capture", f"Normal capture — exposure group {group}")
        w = self._writer = CaptureSuiteWriter(self.out_path, suite_id=self.suite_id)

        for led in range(cfg.led_start, cfg.led_end + 1):
            self._led_index    += 1
            self._captures_done = 0
            color = leds.color_name(led)
            self._current_color = color
            self.prof.set_section(color)

            exposures_us = exposure_repo.read_exposures(color, group)
            used_fallback = not exposures_us
            if used_fallback:
                exposures_us = [cfg.initial_us]
                log.warning("LED %s has no exposures for group %s — "
                            "falling back to %d µs", color, group, cfg.initial_us)
            labels = [f"e{i}" for i in range(len(exposures_us))]
            self._captures_per_led = max(1, 2 * len(exposures_us))
            log.info(f"=== LED {led}: {color} ({group} exposure(s)"
                     f"{', fallback' if used_fallback else ''}) ===")

            note = "  [no calibration — fallback]" if used_fallback else ""
            yield self._progress("capture", f"--- {leds.label(led)} ---{note}")

            self._led_on(led)
            yield from self._wait(LED_ON_WARMUP_S, f"{color} LED on, warming up")

            # ── Lit frames ────────────────────────────────────────────
            yield self._progress("capture", "Lit captures:")
            mid_idx = len(exposures_us) // 2       # keep the middle frame's JPEG
            mid_frame: "tuple | None" = None
            applied_us: dict = {}

            for i, (label, exp_us) in enumerate(zip(labels, exposures_us)):
                rgb, bayer, meta = self._settle(exp_us)
                applied_us[label] = int(meta.get("ExposureTime", 0))
                layers_out = self._bound_layers(bayer, pattern)
                with self.prof.time("io.hdf5"):
                    w.add_lit(color, label, layers_out, exposure_us=exp_us)
                if i == mid_idx:
                    mid_frame = (rgb, bayer, meta)
                self._captures_done += 1
                yield self._progress(
                    "capture", f"    stored lit {label} ({exp_us // 1000}ms)",
                    step=label)

            # ── Dark frames at the same exposures ─────────────────────
            self._led_off(led)
            yield from self._wait(LED_OFF_SETTLE_S, f"{color} LED off, settling")

            yield self._progress("capture", "Dark captures:")
            for label, exp_us in zip(labels, exposures_us):
                _, bayer, _ = self._settle(exp_us)
                layers_out = self._bound_layers(bayer, pattern)
                with self.prof.time("io.hdf5"):
                    stored = w.add_dark(label, layers_out, exposure_us=exp_us)
                    w.link_dark(color, label, exposure_us=exp_us)
                self._captures_done += 1
                yield self._progress(
                    "capture",
                    f"    {'stored' if stored else 'reused'} dark {label} "
                    f"({exp_us // 1000}ms)",
                    step=f"dark {label}")

            # ── Per-LED metadata, statistics and JPEG ─────────────────
            meta_json: dict = {
                "mode": MODE_NORMAL,
                "timestamp": self.suite_id,
                "led_channel": led,
                "led_color": color,
                "led_label": leds.label(led),
                "camera_model": cam.model_description,
                "bayer_pattern": pattern,
                "config_size": [cam_w, cam_h],
                "focus_diopters": focus_pos,
                "focus_mode": focus_mode,
                "autofocus_raw_diopters": raw_af_pos,
                "exposure_group": group,
                "exposure_source": "fallback" if used_fallback else "exposure_table",
                "exposures_us": {lbl: int(us) for lbl, us in zip(labels, exposures_us)},
                "applied_exposures_us": applied_us,
            }
            with self.prof.time("io.hdf5"):
                w.add_led(color, meta_json, led_channel=led)
            if mid_frame is not None:
                rgb_m, bayer_m, meta_m = mid_frame
                info = _build_capture_info(
                    rgb_m, bayer_m, meta_m, lens_position=cam.lens_position)
                with self.prof.time("io.jpeg"):
                    jpeg = _encode_jpeg(rgb_m)
                with self.prof.time("io.hdf5"):
                    w.add_capture_info(color, info)
                    w.add_jpeg(color, jpeg,
                               filename=f"{OUT_STEM}_{color}_{labels[mid_idx]}.jpg")
            yield self._progress("capture", f"    stored metadata for {color}")

            self.results.append(LedResult(
                color=color,
                led_channel=led,
                target_channel="",
                exposures_us=list(exposures_us),
                metadata=meta_json,
            ))

        self._close_writer()
        self._current_color = ""
        yield Progress(
            phase="done",
            message=f"Done. Output: {self.out_path}",
            led_index=self._led_index,
            led_total=cfg.led_count,
            fraction=1.0,
        )
