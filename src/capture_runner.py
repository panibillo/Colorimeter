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

The Normal path is a different shape — see the section comment above
run_prefetch(). It reads its exposures from the `exposure` table instead of
searching for them, and takes every dark frame in one batch after the sweep.

Exposure bracketing targets (50% of 10-bit saturation = 512):
  e0: e3 / E3_RATIO                                    (fixed ratio)
  e1: 89th-percentile pixel in the sample ROI → 512
  e2: 66th-percentile pixel in the sample ROI → 512
  e3: 50th-percentile pixel in the sample ROI → 512  (median)
  e4: 33rd-percentile pixel in the sample ROI → 512
  e5: 11th-percentile pixel in the sample ROI → 512
  e6: e3 * E3_RATIO                                    (fixed ratio)

Hardware is injected, never constructed here: the caller owns the lifetime of
the camera and the LED bank. The runner turns off whichever LED it switched on
when the generator finishes or is abandoned, but does not close either device.

Usage:
    from capture_runner import CaptureRunner, CaptureConfig

    runner = CaptureRunner(cam, leds, CaptureConfig())
    for progress in runner.run():
        if not progress.transient:
            print(progress.message)
    print(runner.out_path, runner.results)

Output: a single HDF5 archive per capture set. Where it is written and what
it is called are rows of the database's `config` table under role `capture`,
reached through config_repo — this module contains no paths or filename
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

import capture_meta
import config_repo as config
import exposure_repo
import layermodel_repo
import roi
import sample_repo
from bayer_cam import BayerCam
from capture_h5 import CaptureSuiteWriter
from capture_profile import CaptureProfiler
from rect import Rect

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

# ROI definitions live in the `roi` table and are loaded per run by
# _load_rois() once the camera has reported its stream size — see roi.py.
# db_bootstrap seeds that table at every launch, so a role still unfilled by
# the time _load_rois() runs is a real bug, not a condition to work around —
# it raises rather than falling back to a Python copy of the seed geometry.

# The roles this module cannot run without: ROLE_WHITE drives exposure
# calibration, ROLE_SAMPLE drives the exposure ladder, ROLE_BOUND is the crop
# the archive stores. Roles, not names — a name is an operator-editable label
# ('black1'/'black2' are already 'chip1'/'chip2'), so matching on one would
# stop working silently. See the note in roi.py.
REQUIRED_ROLES = (roi.ROLE_SAMPLE, roi.ROLE_WHITE, roi.ROLE_BOUND)

# Exposure analysis constants (10-bit sensor: 0–1023)
HALF_SAT        = 512      # target: 50% of full scale
SAT_THRESH      = 950      # p90 > this → oversaturated
MAX_DESAT_TRIES = 3
PERCENTILES     = [89, 66, 50, 33, 11]    # e1, e2, e3, e4, e5 percentile targets
EXP_LABELS      = ["e0", "e1", "e2", "e3", "e4", "e5", "e6"]  # ordered capture sequence

WHITE_LED_CH   = 10    # LEDBank channel for white LED
MAX_AF_DIOPTER = 12.0  # cap autofocus: if result exceeds this, set manually to this.
                        # 12.0 is the IMX708 AF algorithm's own "normal" range
                        # ceiling (imx708.json rpi.af.ranges.normal.max) — not
                        # an arbitrary guess, so a real close-focus result
                        # (e.g. 8.1 diopters for this rig's cuvette distance)
                        # is never silently clipped to a shallower focus.

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
    # The LEDs this run sweeps, by color name, in the order it sweeps them.
    # default_factory so the list is read when a config is built rather than
    # baked in at import, same as out_dir below.
    led_colors:     "list[str]" = field(
        default_factory=config.capture_led_color_order)
    focus_diopter:  "float | None" = None
    # Which capture path to run, and (normal only) which stored exposure group
    # to use — its name is the number of images per LED ('1'..'5').
    mode:           str = MODE_CALIBRATION
    exposure_group: str = "1"
    # A config-table row under role `capture`. default_factory,
    # not a plain default, so the location is resolved when a config is built
    # rather than baked in when this module is first imported.
    out_dir:       Path = field(default_factory=config.capture_dir)
    # `samples.rowid` this capture belongs to, recorded in the archive's
    # metadata. None for a menu-launched capture, which belongs to no
    # protocol run — a normal state, not an error.
    sample_rowid:  "int | None" = None

    @property
    def initial_us(self) -> int:
        """Initial exposure in microseconds."""
        return int(self.exposure_ms * 1000)

    @property
    def led_count(self) -> int:
        """Number of LEDs this run will visit."""
        return len(self.led_colors)


def _resolve_color_order(leds, colors) -> "list[tuple[int, str]]":
    """
    Map the configured color names onto this LED bank's channels.

    Both capture paths sweep `capture.led_color_order`, which an operator can
    edit, so a name the bank does not have has to be caught somewhere.  Doing
    it once up front means a typo costs an error message instead of a run that
    dies partway through with the archive already open.

    Args:
        leds:   The LEDBank the run will drive.
        colors: Color names, in sweep order.

    Returns:
        [(channel, canonical color name)] — the name as the bank spells it, so
        the archive records one spelling however the list was typed.

    Raises:
        ValueError: A name is not one of this bank's LEDs.
    """
    resolved = []
    for name in colors:
        try:
            ch = leds.channel_of(name)
        except ValueError:
            raise ValueError(
                f"capture.led_color_order names {name!r}, which is not an LED "
                f"on this bank") from None
        resolved.append((ch, leds.color_name(ch)))
    return resolved


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

def _load_rois(cam) -> "dict[str, Rect]":
    """
    Load the ROIs in force for this run, in stream space.

    Called once per run, after setup_camera(), so the camera's actual stream
    size and model can be passed to roi.load_active() — a ROI stored against a
    different stream is then rescaled or loudly refused rather than silently
    misapplied.

    db_bootstrap seeds the `roi` table at every launch, so by the time this
    runs every REQUIRED_ROLES entry is guaranteed to have an active row.
    Raises roi.RoiMissing if one is unfilled anyway, rather than quietly
    substituting a Python copy of the seed geometry — that substitution is
    exactly the kind of value-lurking-in-Python this table was seeded to
    replace, and a run using it would archive a crop the operator never chose.

    Also validates what it returns, because these are the assumptions the
    Bayer-layer conversions rest on (see roi.py's traps A and B):
      - every ROI even on all four values, so layer extraction keeps its phase
      - every other ROI inside the ROLE_BOUND one, since that is the crop
        the archive stores

    Args:
        cam: The camera, already set up (config_size populated).

    Returns:
        {name: Rect} in stream space, guaranteed to fill REQUIRED_ROLES.

    Raises:
        roi.RoiMissing: a required role has no active row.
    """
    size  = getattr(cam, "config_size", None) or (None, None)
    model = getattr(cam, "model", None)
    rois  = roi.load_active(size[0], size[1], model)

    missing = roi.missing_roles(rois, REQUIRED_ROLES)
    if missing:
        raise roi.RoiMissing(
            f"the roi table has no active row for role(s) {missing} — "
            f"expected db_bootstrap.ensure() to have seeded it")

    for name, r in sorted(rois.items()):
        if not roi.is_bayer_safe(r):
            log.error("capture: ROI %r is not Bayer-safe (%s) — layer "
                      "coordinates will be off by up to one mosaic pixel",
                      name, r.xywh())
    # Containment in `bound` matters at two different severities. The run
    # itself measures `white` and `sample` against whole-frame layers, so an
    # overhang costs this run nothing; what it costs is the archive, which
    # stores only the bound crop. Losing part of `sample` would make the
    # reading unrecoverable, so that is an error; losing part of a reference
    # patch degrades later re-analysis, so that is a warning.
    bound = roi.by_role(rois, roi.ROLE_BOUND)
    for name, r in sorted(rois.items()):
        if r is bound or roi.contains_rect(bound, r):
            continue
        lost = "  ".join(
            f"{edge} by {px}px" for edge, px in (
                ("left",   bound.x - r.x),   ("right",  r.x2() - bound.x2()),
                ("top",    bound.y - r.y),   ("bottom", r.y2() - bound.y2()))
            if px > 0)
        emit = (log.error if getattr(r, "role", None) == roi.ROLE_SAMPLE
                else log.warning)
        emit("capture: ROI %r %s extends outside bound %s (%s) — that part is "
             "not stored in the archive and cannot be re-measured from it",
             name, r.xywh(), bound.xywh(), lost)

    log.info("capture: ROIs " + "  ".join(
        f"{n}={r.xywh()}" for n, r in sorted(rois.items())))
    return rois


def _roi_pixels(layer: np.ndarray, lr: Rect) -> np.ndarray:
    """
    Extract and flatten one ROI's pixels from a single Bayer layer.

    `lr` must already be in *layer* coordinates — half the stream size — as
    produced by roi.to_layer_rect() (whole-frame layers) or
    roi.to_layer_rect_within() (layers of a cropped region). Passing a
    stream-space rect here reads roughly four times too large an area.
    """
    return layer[lr.xyslice()].ravel()


def _channel_means(layers: dict, lr: Rect) -> dict:
    """Return {ch_name: mean} for all channels over one layer-space ROI."""
    return {ch: float(_roi_pixels(layers[ch], lr).mean()) for ch in layers}


def _select_target_channel(layers: dict, white_lr: Rect) -> str:
    """Return the channel key ('B','Gb','Gr','R') with highest mean in white."""
    means = _channel_means(layers, white_lr)
    chosen = max(means, key=means.__getitem__)
    log.debug(f"    target channel: {chosen}  (means: "
              + "  ".join(f"{k}={v:.1f}" for k, v in sorted(means.items()))
              + ")")
    return chosen


def _check_saturated(layers: dict, target_ch: str, white_lr: Rect) -> bool:
    """Return True if p90 of the target channel in white exceeds SAT_THRESH."""
    pix = _roi_pixels(layers[target_ch], white_lr)
    p90 = float(np.percentile(pix, 90))
    log.debug(f"    saturation check: p90({target_ch})={p90:.1f}  threshold={SAT_THRESH}")
    return p90 > SAT_THRESH


def _check_saturated_any(layers: dict, white_lr: Rect) -> bool:
    """Return True if p90 of ANY channel in white exceeds SAT_THRESH."""
    return any(
        float(np.percentile(_roi_pixels(layers[ch], white_lr), 90)) > SAT_THRESH
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
    log.debug(f"    sample ROI at e3 ({e3_us}µs): "
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

def _roi_bound_layers(bayer: np.ndarray, pattern: str, bound: Rect) -> dict:
    """
    Crop `bound` from a full bayer frame and split it into 4 channels.

    Cropping *before* extracting means the layers' origin is `bound`'s corner,
    not the frame's — so a ROI indexed into these must be converted with
    roi.to_layer_rect_within(r, bound), never roi.to_layer_rect(r). That is
    trap C in roi.py, and it is why this returns the crop's layers under a
    distinct name.

    `bound` must be even on x and y: an odd origin shifts the Bayer phase, so
    every layer would be relabelled and the dict key 'R' would not hold red.
    _load_rois() checks that once per run.

    Args:
        bayer:   Full mosaic frame, shape (H, W).
        pattern: "BGGR" or "RGGB" — pass cam.bayer_pattern.
        bound:   The bounding ROI in stream space.

    Returns:
        {ch_name: ndarray}, each of shape (bound.h // 2, bound.w // 2)
        — (315, 98) for the commissioned bound.
    """
    crop = bayer[roi.to_slices(bound)]
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
                    max_frames: int = MAX_SETTLE_FRAMES,
                    ) -> "tuple[np.ndarray, np.ndarray, dict]":
    """
    Set exposure and capture frames until metadata confirms it has been applied.

    After calling set_exposure(), the Picamera2 pipeline takes at least one
    frame cycle to adopt the new ExposureTime.  This function keeps capturing
    until the metadata ExposureTime is within SETTLE_TOLERANCE of the request,
    or `max_frames` is exhausted.

    The returned metadata carries the exposure the frame was *actually* taken
    at, which is what a caller must use when it scales an exposure from what it
    measured — the request and the frame can still disagree when the budget
    runs out.

    Args:
        cam:         Camera to drive.
        exposure_us: Requested exposure in microseconds.
        prof:        Optional profiler; when given, this counts one settle-run
                     and times every capture_frame() call it makes.
        max_frames:  Frame budget.  A capture spends the full
                     MAX_SETTLE_FRAMES because its numbers are the result; the
                     image viewer's preview passes a much smaller budget,
                     because there a slow screen is worse than a frame taken
                     slightly off the requested exposure.
    """
    cam.set_exposure(exposure_us)
    if prof is not None:
        prof.count("camera.settle")
    last = None
    for frame_num in range(max_frames):
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
        log.debug(f"    settling [{frame_num + 1}/{max_frames}]: "
                  f"requested={exposure_us}µs  applied={applied}µs")
    applied = int(last[2].get("ExposureTime", 0))  # type: ignore[index]
    log.warning(f"    exposure did not settle after {max_frames} frames; "
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

        # ROIs in force for this run, in stream space, keyed by name. Empty
        # until _load_rois() fills it, once the camera reports its stream
        # size — nothing reads this before then, and an edit made in the
        # viewer takes effect on the next run without a restart.
        self.rois: "dict[str, Rect]" = {}

        # The archive being written. Unlike the camera and LED bank, this is
        # created and owned by the runner, so the runner also closes it.
        self._writer: "CaptureSuiteWriter | None" = None

        # Progress bookkeeping.
        self._led_index      = 0
        self._captures_done  = 0
        self._current_color  = ""
        self._lit_on_channel: "int | None" = None

        # When things happened, to the millisecond, as text — see
        # capture_meta.timestamp_ms(). Each is overwritten as the run moves
        # on, so they are copied into the archive as soon as they are taken:
        #   _led_on_dt / _led_off_dt   set by _led_on() / _led_off(), copied
        #                              into that LED's metadata record
        #   _frame_captured_dt         set by _settle(), copied onto the
        #                              image datasets it produced
        self._led_on_dt         = ""
        self._led_off_dt        = ""
        self._frame_captured_dt = ""
        # Captures per LED, for the completion estimate. The calibration path
        # does a fixed lit+dark bracket; the normal path does lit of the
        # chosen group only (darks are batched separately), set when that
        # run starts.
        self._captures_per_led = _CAPTURES_PER_LED

        # Normal-path prefetch/capture state, populated by run_prefetch() and
        # read by run_capture() — see the "Normal path" section below for how
        # the two share a single instance across an arbitrary-length pause.
        self._pattern: str = ""
        self._cam_w: int = 0
        self._cam_h: int = 0
        self._focus_pos: float = 0.0
        self._focus_mode: str = ""
        self._raw_af_pos: "float | None" = None
        self._group: str = ""
        self._color_order: "list[str]" = []
        self._color_exposures: "dict[str, list[int]]" = {}
        self._color_labels: "dict[str, list[str]]" = {}
        self._color_fallback: "dict[str, bool]" = {}
        self._distinct_dark_us: "list[int]" = []
        # {exposure_us: channels some lit frame at that exposure kept}, filled
        # in during the sweep and read by the dark batch that follows it.
        self._dark_keep: "dict[int, set]" = {}
        # {(color, exposure_us): channels to keep}, so a repeated exposure does
        # not re-query the database.
        self._keep_cache: dict = {}

        # Wall-clock profiler (on unless COLORIMETER_PROFILE is false-y). Records
        # every frame, wait, numpy block, and archive write; its summary is
        # logged when run() finishes. See capture_profile.CaptureProfiler.
        self.prof = CaptureProfiler()

    # ── Progress helpers ─────────────────────────────────────────────

    def _led_total(self) -> int:
        """LED count for progress reporting: the length of the sweep list."""
        return self.config.led_count

    def _fraction(self) -> float:
        """Monotonic completion estimate over the whole run."""
        total = self._led_total()
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
            led_total=self._led_total(),
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
        """
        set_exposure + capture until the pipeline adopts it, timing frames.

        Also notes when the frame arrived, in self._frame_captured_dt. Every
        photograph this class stores comes through here, so stamping the
        clock in this one place is what guarantees each stored image carries
        a capture time — a new capture site cannot forget to do it.

        The clock is read after _set_and_settle() returns, not before it is
        called: that function may take several frames to talk the pipeline
        into the requested exposure, and only the last of them is the one
        kept. Reading the clock on the way out therefore lands within a few
        milliseconds of the stored frame, where reading it on the way in
        could be off by the whole settling period.
        """
        frame = _set_and_settle(self._cam, exposure_us, self.prof)
        self._frame_captured_dt = capture_meta.timestamp_ms()
        return frame

    def _extract(self, bayer: np.ndarray, pattern: str) -> dict:
        """Split a full bayer frame into channel layers, timed as cpu.extract."""
        with self.prof.time("cpu.extract"):
            return BayerCam.extract_bayer_layers(bayer, pattern)

    def _bound_layers(self, bayer: np.ndarray, pattern: str) -> dict:
        """Crop the bound ROI and split into channel layers, timed as cpu.extract."""
        with self.prof.time("cpu.extract"):
            return _roi_bound_layers(bayer, pattern,
                                     roi.by_role(self.rois, roi.ROLE_BOUND))

    def _keep_channels(self, color: str, exposure_us: int,
                       layers: dict) -> tuple:
        """
        Return the Bayer layers worth storing for one LED at one exposure.

        Asks layermodel_repo, which knows what the flat-field analysis measured
        for each LED colour and layer; see keep_channels() there for the rules,
        the most important of which is that an un-analysed combination is kept.

        Two ways this refuses to drop anything, and both are deliberate. A LED
        with no rows keeps every layer, because "not analysed" must never look
        like "no good". And a failure to reach the database at all keeps every
        layer and logs it, because losing image data to a configuration problem
        is far worse than storing a few dark frames — the archive is the only
        copy, and this decision is not reversible.

        Args:
            color: LED colour name.
            exposure_us: Exposure about to be stored, in microseconds.
            layers: The extracted layers, whose keys bound the answer.

        Returns:
            The subset of `layers`' keys to store.
        """
        cached = self._keep_cache.get((color, exposure_us))
        if cached is not None:
            return cached

        channels = tuple(layers)
        try:
            keep = layermodel_repo.keep_channels(color, exposure_us, channels)
        except Exception:                             # noqa: BLE001
            log.exception("could not read layermodels for %s at %dµs — "
                          "storing every layer", color, exposure_us)
            keep = channels
        self._keep_cache[(color, exposure_us)] = keep
        return keep

    def _choose_out_path(self) -> None:
        """
        Decide where this run's archive goes, and make sure the directory
        exists. Sets self.suite_id and self.out_path.

        The file is named after the sample it holds — "ZP1007.h5" — because
        that is the name an operator has in hand when they come looking for
        it. A capture launched from the menu belongs to no sample, so it falls
        back to the capture time, which is what every archive used to be
        called. config.capture_archive_path() handles the (unlikely) case of
        that name already being taken; see its comments.

        self.suite_id keeps its old meaning either way — the capture time,
        stored inside the archive as the suite_id attribute and repeated in
        each LED's metadata as "timestamp". It is no longer necessarily part
        of the filename, so do not use one to work out the other.
        """
        cfg = self.config
        self.suite_id = config.capture_suite_id()
        cfg.out_dir.mkdir(parents=True, exist_ok=True)
        stem = sample_repo.samp_no_for(cfg.sample_rowid) or self.suite_id
        self.out_path = config.capture_archive_path(cfg.out_dir, stem)

    def _write_metadata(self, w: CaptureSuiteWriter) -> None:
        """
        Stamp the archive with its ROI geometry, camera, device and sample.

        Called immediately after the writer is opened, before any image data,
        so an aborted run still leaves an interpretable file — the layers are
        a crop of `bound`, and without `bound`'s x and y nothing else in the
        file can be located. capture_meta.collect() never raises, so this
        cannot cost a capture.

        Writing this early has one cost worth knowing about: most of the
        sample record does not exist yet. The operator has not finished the
        reading, so capture_end, manual_read and the results are all still
        empty here, and they stay that way in the file until somebody calls
        capture_meta.update_archive_sample() on it afterwards. The arbiter
        does that when the capture finishes and again when analysis lands.

        Args:
            w: The freshly opened CaptureSuiteWriter.

        Returns:
            None
        """
        meta = capture_meta.collect(cam=self._cam, cfg=self.config,
                                    sample_rowid=self.config.sample_rowid)
        w.add_metadata(meta, capture_meta.root_attrs(meta))

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
                # Chained fallback: a caller that never split the run across
                # the reagent2 pause (camera contention, a short debug timer)
                # still gets the new capture order and dark-frame dedup, just
                # without the latency-hiding — see run_prefetch()/run_capture().
                yield from self.run_prefetch()
                yield from self.run_capture()
            else:
                yield from self._run()
        finally:
            self._all_off()
            self._close_writer()
            if self.prof.enabled:
                log.info("capture profile:\n%s", self.prof.summary())

    def close_prefetch(self) -> None:
        """
        Close a parked prefetch's archive/LED state without resuming it.

        Only meaningful after run_prefetch() has returned normally — parked,
        waiting for run_capture() — since there is then no live generator
        left to abandon: run_prefetch()'s own GeneratorExit handler only
        fires while it is still suspended on a yield, not after it has
        already returned. A caller that wants to cancel a parked prefetch
        (rather than resume it) calls this directly instead. Safe to call
        even if nothing is open — both underlying calls are no-ops then.
        """
        self._all_off()
        self._close_writer()

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
        """
        Switch an LED on, note the time, and remember it for cleanup.

        The clock is read immediately after the switch returns, which is the
        closest this code can get to the moment the LED actually lit. The
        time is kept in self._led_on_dt for whoever is running the sweep to
        copy into that LED's metadata; it is overwritten by the next LED, so
        read it before switching anything else on.
        """
        self._leds.set_channel(channel, True)
        self._led_on_dt = capture_meta.timestamp_ms()
        self._lit_on_channel = channel

    def _led_off(self, channel: int) -> None:
        """
        Switch an LED off, note the time, and clear the cleanup record.

        Companion to _led_on() — see there for how the time is used.
        """
        self._leds.set_channel(channel, False)
        self._led_off_dt = capture_meta.timestamp_ms()
        self._lit_on_channel = None

    def _run(self) -> Iterator[Progress]:
        cfg  = self.config
        cam  = self._cam
        leds = self._leds

        # Naming lives in the config table under role `capture`.
        self._choose_out_path()
        self.results = []

        # ── Camera init ──────────────────────────────────────────────
        cam.set_exposure(cfg.initial_us)
        with self.prof.time("camera.setup"):
            cam.setup_camera()
        pattern = cam.bayer_pattern
        assert cam.config_size is not None, "setup_camera() must set config_size"
        cam_w, cam_h = cam.config_size

        # ROIs come from the DB, and only now is the stream size known. Both
        # analysis ROIs are converted once, to coordinates in the *whole-frame*
        # layers that _extract() produces — not the bound-crop layers
        # _bound_layers() produces. See roi.py, trap C.
        self.rois   = _load_rois(cam)
        white_lr    = roi.to_layer_rect(roi.by_role(self.rois, roi.ROLE_WHITE))
        sample_lr   = roi.to_layer_rect(roi.by_role(self.rois, roi.ROLE_SAMPLE))

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
        # Kept in a local because _led_on_dt is overwritten by the first
        # colored LED further down, long before the metadata is assembled.
        phase0_led_on_dt = self._led_on_dt
        yield from self._wait(LED_ON_WARMUP_S, "White LED on, warming up")

        current_us     = cfg.initial_us
        phase0_retries = 0

        for attempt in range(MAX_DESAT_TRIES):
            log.debug(f"Phase 0 attempt {attempt + 1}: exposure={current_us}µs")
            _, bayer, _ = self._settle(current_us)
            layers = self._extract(bayer, pattern)
            with self.prof.time("cpu.analyze"):
                means     = _channel_means(layers, white_lr)
                saturated = _check_saturated_any(layers, white_lr)
            log.debug("  white ROI means: "
                      + "  ".join(f"{k}={v:.1f}" for k, v in sorted(means.items())))
            if not saturated:
                log.debug(f"  Not saturated — accepting {current_us}µs "
                          f"as calibrated exposure")
                break
            new_us = max(current_us // 2, 1000)
            log.debug(f"  Saturated — halving {current_us}µs → {new_us}µs")
            yield self._progress(
                "calibrate",
                f"White ROI saturated — halving {current_us:,} → {new_us:,} µs")
            current_us = new_us
            phase0_retries += 1

        calibrated_us = current_us
        self._led_off(WHITE_LED_CH)
        phase0_led_off_dt = self._led_off_dt
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
        self._write_metadata(w)

        for led, color in _resolve_color_order(leds, cfg.led_colors):
            self._led_index     += 1
            self._captures_done  = 0
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
                    ref_means = _channel_means(layers, white_lr)
                    target_ch = _select_target_channel(layers, white_lr)
                    saturated = _check_saturated(layers, target_ch, white_lr)
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
                "capture", f"Target channel : {target_ch}  (white ROI: {means_str})")

            # ── Stage 1: estimate e3 from reference capture ───────────
            with self.prof.time("cpu.analyze"):
                pix_ref = _roi_pixels(layers[target_ch], sample_lr)
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
                    pix_e3     = _roi_pixels(layers_e3[target_ch], sample_lr)
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
                pix_e3 = _roi_pixels(layers_e3[target_ch], sample_lr)
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
                # When this LED was lit and put out again, to the millisecond.
                # led_off_dt is filled in further down, once it has been: the
                # dark frames are photographed after the LED goes out, so the
                # record cannot be finished until then.
                "led_on_dt":  self._led_on_dt,
                "led_off_dt": "",
                "phase0_calibrated_exposure_us": calibrated_us,
                # The white LED of Phase 0 is lit once for the whole run, not
                # once per color, so these two repeat identically in every
                # LED's record — the same as the two phase0 values above them.
                "phase0_led_on_dt":  phase0_led_on_dt,
                "phase0_led_off_dt": phase0_led_off_dt,
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
                    w.add_lit(color, exp_label, layers_out, exposure_us=exp_us,
                              captured_dt=self._frame_captured_dt)

                if exp_label == "e3":    # keep the median frame for the JPEG
                    e3_frame = (rgb, bayer, meta)

                self._captures_done += 1
                yield self._progress(
                    "capture",
                    f"    stored lit {exp_label} ({exp_us // 1000}ms)",
                    step=exp_label)

            # LED off, then dark frames at the same exposures
            self._led_off(led)
            meta_json["led_off_dt"] = self._led_off_dt
            yield from self._wait(LED_OFF_SETTLE_S, f"{color} LED off, settling")

            yield self._progress("capture", "Dark captures:")
            for exp_label, exp_us in zip(EXP_LABELS, exposures_us):
                log.info(f"  {color} dark {exp_label}: exposure {exp_us}µs")
                _, bayer, _ = self._settle(exp_us)

                # Dark frames depend only on exposure, so an exposure already
                # captured for an earlier LED is reused rather than restored.
                layers_out = self._bound_layers(bayer, pattern)
                with self.prof.time("io.hdf5"):
                    stored = w.add_dark(layers_out, exposure_us=exp_us,
                                        captured_dt=self._frame_captured_dt)
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
    #
    # Split into two generators sharing this instance's state, so a caller
    # (CaptureSession/Arbiter) can run run_prefetch() unattended during the
    # reagent2 countdown — nothing there needs an LED lit — then pause for an
    # arbitrary, user-controlled length of time before resuming run_capture()
    # when CAPTURE is pressed. What that buys is the camera: setup and focus
    # are the slow part of a run, and doing them while the operator is waiting
    # out reagent2 takes them off the clock. A caller that never splits the run
    # (camera contention, a short debug timer) loses only the overlap: run()
    # chains the two back-to-back for MODE_NORMAL.
    #
    # Dark frames are all taken in ONE batch, after the LED sweep ends. They
    # used to be taken twice, "before" (here) and "after" (at the end of
    # run_capture()), so a reader could measure the sensor's drift across the
    # run by differencing them; devdesk/roi_darkdiff.py is that reader, and it
    # did its job — the drift is not what limits this instrument. Paying for it
    # every run cost 30 settled captures and 5.3 MB per archive, so the
    # "before" batch is gone. The schema still names the batch ("after_<us>us")
    # and readers still prefer it, so archives holding both remain readable.

    def run_prefetch(self) -> Iterator[Progress]:
        """
        Normal-path stage 1: camera init, focus, and opening the archive.

        No photographs are taken here — this is the part of a run that can
        happen before the operator is ready, so that when CAPTURE is pressed
        the only work left is the work that needs the sample in place.

        On successful completion, deliberately leaves the camera, LED bank,
        and archive open — NOT cleaned up — so run_capture() can pick up
        exactly where this left off after however long the caller pauses.
        Abandonment (GeneratorExit) or a failure DOES clean up: there is
        nothing left to hand off in either case.
        """
        cfg  = self.config
        cam  = self._cam
        try:
            self._group = group = str(cfg.exposure_group)

            self._choose_out_path()
            self.results = []
            self._dark_keep = {}
            self._keep_cache = {}

            # ── Camera init ──────────────────────────────────────────
            cam.set_exposure(cfg.initial_us)
            with self.prof.time("camera.setup"):
                cam.setup_camera()
            self._pattern = cam.bayer_pattern
            assert cam.config_size is not None, "setup_camera() must set config_size"
            self._cam_w, self._cam_h = cam.config_size

            # ROIs come from the DB, and only now is the stream size known —
            # same as run()'s calibration path. Without this, the normal path
            # silently kept __init__'s seed fallback instead of picking up
            # on-instrument edits to the active row.
            self.rois = _load_rois(cam)

            yield self._progress(
                "setup",
                f"Camera {cam.model_description}  pattern={self._pattern}  "
                f"{self._cam_w}×{self._cam_h}  (normal, group {group})")

            # ── Focus (still needed; exposures are stored, focus is not) ──
            self.prof.set_section("focus")
            yield self._progress("focus", "Phase 1: focus")
            with self.prof.time("focus"):
                self._focus_pos, self._focus_mode, self._raw_af_pos = self._set_focus()
            yield self._progress(
                "focus", f"Focus: {self._focus_pos:.2f} diopters ({self._focus_mode})")

            # ── Resolve the color order and each color's exposures ────
            # Same list as the calibration path sweeps, resolved the same way,
            # so an unknown name fails here rather than mid-capture.
            self._color_order = [c for _, c in
                                 _resolve_color_order(self._leds, cfg.led_colors)]
            self._color_exposures = {}
            self._color_labels    = {}
            self._color_fallback  = {}
            distinct_us: set = set()

            for color in self._color_order:
                exposures_us = exposure_repo.read_exposures(color, group)
                used_fallback = not exposures_us
                if used_fallback:
                    exposures_us = [cfg.initial_us]
                    log.warning("LED %s has no exposures for group %s — "
                                "falling back to %d µs", color, group, cfg.initial_us)
                self._color_exposures[color] = exposures_us
                self._color_labels[color]    = [f"e{i}" for i in range(len(exposures_us))]
                self._color_fallback[color]  = used_fallback
                distinct_us.update(exposures_us)

            self._distinct_dark_us = sorted(distinct_us)

            # Open the archive and write /meta now, before any photograph.
            # The ROI geometry is what makes the stored arrays locatable, so a
            # run that dies partway still leaves an interpretable file.
            w = self._writer = CaptureSuiteWriter(self.out_path, suite_id=self.suite_id)
            self._write_metadata(w)

            log.info(f"=== Prefetch done: {len(self._distinct_dark_us)} "
                     f"distinct dark exposure(s) to take at the end — "
                     f"{self._distinct_dark_us} µs ===")
            yield self._progress(
                "capture", "Ready — press CAPTURE to begin the LED sweep.",
                step="prefetch-done")

        except (GeneratorExit, Exception):
            self._all_off()
            self._close_writer()
            raise

    def run_capture(self) -> Iterator[Progress]:
        """
        Normal-path stage 2: the lit-LED sweep, then the dark batch.

        Assumes run_prefetch() already ran on this same instance — the
        archive is open, the camera is set up and focused, and the color
        order / per-color exposures / distinct dark exposure set are
        resolved. Always cleans up (LED off, archive closed) on completion,
        abandonment, or failure — unlike run_prefetch(), there is no pause
        left to preserve after this.

        Every dark frame in the archive is taken here, at the end, with the
        LED bank off — one settle wait for the whole run instead of one per
        LED, and one batch instead of the two this used to take.
        """
        leds = self._leds
        w    = self._writer
        try:
            yield self._progress(
                "capture", f"Normal capture — exposure group {self._group}")

            for color in self._color_order:
                self._led_index    += 1
                self._captures_done = 0
                self._current_color = color
                self.prof.set_section(color)

                exposures_us  = self._color_exposures[color]
                labels        = self._color_labels[color]
                used_fallback = self._color_fallback[color]
                self._captures_per_led = max(1, len(exposures_us))
                led = leds.channel_of(color)

                log.info(f"=== LED {led}: {color} ({self._group} exposure(s)"
                         f"{', fallback' if used_fallback else ''}) ===")

                note = "  [no calibration — fallback]" if used_fallback else ""
                yield self._progress("capture", f"--- {leds.label(led)} ---{note}")

                self._led_on(led)
                yield from self._wait(LED_ON_WARMUP_S, f"{color} LED on, warming up")

                # ── Lit frames ────────────────────────────────────────
                yield self._progress("capture", "Lit captures:")
                mid_idx = len(exposures_us) // 2   # keep the middle frame's JPEG
                mid_frame: "tuple | None" = None
                applied_us: dict = {}

                kept_layers: dict = {}
                omitted_layers: dict = {}

                for i, (label, exp_us) in enumerate(zip(labels, exposures_us)):
                    rgb, bayer, meta = self._settle(exp_us)
                    applied_us[label] = int(meta.get("ExposureTime", 0))
                    layers_out = self._bound_layers(bayer, self._pattern)

                    keep = self._keep_channels(color, exp_us, layers_out)
                    omit = [ch for ch in layers_out if ch not in keep]
                    kept_layers[label] = list(keep)
                    omitted_layers[label] = omit
                    # Remember what this exposure's darks will be needed for.
                    # The same exposure can be reached by more than one LED, so
                    # union rather than assign: a dark layer is worth storing
                    # if ANY lit frame at that exposure kept it.
                    self._dark_keep.setdefault(exp_us, set()).update(keep)

                    with self.prof.time("io.hdf5"):
                        w.add_lit(color, label, layers_out, exposure_us=exp_us,
                                  captured_dt=self._frame_captured_dt,
                                  omit=omit)
                    if i == mid_idx:
                        mid_frame = (rgb, bayer, meta)
                    self._captures_done += 1
                    dropped = f", dropped {'+'.join(omit)}" if omit else ""
                    yield self._progress(
                        "capture",
                        f"    stored lit {label} ({exp_us // 1000}ms){dropped}",
                        step=label)

                # Darks are all taken in one batch at the end of the sweep —
                # no per-LED settle wait needed here.
                self._led_off(led)

                # ── Per-LED metadata, statistics and JPEG ──────────────
                meta_json: dict = {
                    "mode": MODE_NORMAL,
                    "timestamp": self.suite_id,
                    "led_channel": led,
                    "led_color": color,
                    "led_label": leds.label(led),
                    "camera_model": self._cam.model_description,
                    "bayer_pattern": self._pattern,
                    "config_size": [self._cam_w, self._cam_h],
                    "focus_diopters": self._focus_pos,
                    "focus_mode": self._focus_mode,
                    "autofocus_raw_diopters": self._raw_af_pos,
                    # Both available here, unlike the calibration path: this
                    # record is assembled after the LED has been put out.
                    "led_on_dt":  self._led_on_dt,
                    "led_off_dt": self._led_off_dt,
                    "exposure_group": self._group,
                    "exposure_source": "fallback" if used_fallback else "exposure_table",
                    "exposures_us": {lbl: int(us) for lbl, us in zip(labels, exposures_us)},
                    "applied_exposures_us": applied_us,
                    # Which Bayer layers reached the file, per exposure, and
                    # which were stored as empty markers instead. Recorded here
                    # as well as on the datasets themselves so the decision can
                    # be read off one JSON record rather than by walking forty
                    # datasets' attributes.
                    "layers_kept": kept_layers,
                    "layers_omitted": omitted_layers,
                }
                with self.prof.time("io.hdf5"):
                    w.add_led(color, meta_json, led_channel=led)
                if mid_frame is not None:
                    rgb_m, bayer_m, meta_m = mid_frame
                    info = _build_capture_info(
                        rgb_m, bayer_m, meta_m, lens_position=self._cam.lens_position)
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

            # ── Dark batch — LED stays off throughout ─────────────────
            # One settle wait for the whole run. It was not needed while the
            # "before" batch existed, because that one ran with the bank never
            # yet lit; now that this is the only batch, the last LED's
            # afterglow is the only thing between the sweep and every dark
            # frame the archive will hold, so it is waited out explicitly
            # rather than left to however long the last JPEG took to encode.
            yield from self._wait(LED_OFF_SETTLE_S,
                                  "LEDs off, settling before dark frames")

            log.info(f"=== Dark batch: {len(self._distinct_dark_us)} "
                     f"distinct exposure(s) — {self._distinct_dark_us} µs ===")
            yield self._progress(
                "capture",
                f"Dark frames: {len(self._distinct_dark_us)} exposure(s)")
            for exp_us in self._distinct_dark_us:
                _, bayer, _ = self._settle(exp_us)
                layers_out = self._bound_layers(bayer, self._pattern)
                # A dark layer is only ever used to be subtracted from a lit
                # one, so store exactly the channels some lit frame at this
                # exposure kept. The sweep has finished, so that is known.
                needed = self._dark_keep.get(exp_us, set(layers_out))
                omit = [ch for ch in layers_out if ch not in needed]
                with self.prof.time("io.hdf5"):
                    stored = w.add_dark(layers_out, exposure_us=exp_us, when="after",
                                        captured_dt=self._frame_captured_dt,
                                        omit=omit)
                dropped = f", dropped {'+'.join(omit)}" if omit else ""
                yield self._progress(
                    "capture",
                    f"    {'stored' if stored else 'reused'} dark "
                    f"({exp_us}µs){dropped}", step="dark")

            for color in self._color_order:
                for label, exp_us in zip(self._color_labels[color],
                                         self._color_exposures[color]):
                    w.link_dark(color, label, exposure_us=exp_us, when="after")

            self._current_color = ""
            yield Progress(
                phase="done",
                message=f"Done. Output: {self.out_path}",
                led_index=self._led_index,
                led_total=self._led_total(),
                fraction=1.0,
            )
        finally:
            self._all_off()
            self._close_writer()
