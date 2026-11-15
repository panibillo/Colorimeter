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
import exposure_log_repo
import exposure_rule
import hotpixel_repo
import layermodel_repo
import padfind
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

# Colour gains take effect in the ISP some frames after they are set, and the
# frame's own metadata says which gains it was rendered with.  So a change of
# rendering is confirmed the way an exposure change is: by capturing until a
# frame reports the gains asked for (_settle_frames), within the same frame
# budget.  Going in, the white frame waits for the photo's gains.  Coming out,
# nothing waits: the neutral gains are requested as the white LED goes off,
# and the next run's first lit frame is the one that checks they arrived —
# by then they have had the whole gap between runs to do so.
GAIN_TOLERANCE       = 0.01         # 1 % tolerance for the gains check
NEUTRAL_GAINS        = tuple(BayerCam.OBJECTIVE_RENDERING["ColourGains"])

# The dark correction is one number per Bayer layer per sweep (see the comment
# above capture_h5.dark_level), so a run keeps two dark frames, as statistics
# only: at the shortest exposure the dark analysis tested, and at the run's
# longest lit exposure, so that any dark current growing with exposure would
# show up as a difference between the two.
DARK_SHORT_US        = 1000
WAIT_TICK_S          = 0.05         # pump interval while waiting out a warmup

OUT_STEM = "camtest8"

# ROI definitions live in the `roi` table and are loaded per run by
# _load_rois() once the camera has reported its stream size — see roi.py.
# db_bootstrap seeds that table at every launch, so a role still unfilled by
# the time _load_rois() runs is a real bug, not a condition to work around —
# it raises rather than falling back to a Python copy of the seed geometry.

# The roles this module cannot run without are commissioned data, not a
# constant: roi.required_roles() reads them from the config table's
# roi.required_roles (editable=0).  Read per run rather than at import, so a
# unit re-commissioned for a different card does not need this module reloaded.

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
#   "pre"         — the pre-image: the same machinery as "normal", run before
#                   the strip has met any reagent, and deliberately starved of
#                   light. See below.
MODE_CALIBRATION = "calibration"
MODE_NORMAL      = "normal"
MODE_PRE         = "pre"

# How a pre-image or a measurement sets each LED's exposure_us
# (CaptureConfig.exposure_rule; the calibration path ignores it):
#   "strip"  (default, 2026-11-14) — from the strip itself: re-aimed on the
#            fitted pad and gray chips until nothing there clips, then
#            protocol.multiple_exposure_count frames at that exposure
#            (Tus), plus a longer Bus frame when an analysis channel falls
#            below the noise floor.  See src/exposure_rule.py.
#   "group"  — the exposures stored in the `exposure` table for the chosen
#            group, as every run did before 2026-11-14.  Kept so the two can
#            be compared, and as a fallback if the strip rule misbehaves.
RULE_STRIP = "strip"
RULE_GROUP = "group"

# The pre-image path
# ------------------
# A reagent pad is not white. It carries a colour of its own before anything
# is done to it, and that colour varies from strip to strip by roughly as much
# as 5 ppb of arsenic develops — so at the bottom of the range the blank is
# not a constant to be assumed but a thing to be measured, on the very strip
# being read. That is all a pre-image is: the same sweep, taken early, so the
# measurement can be divided by the pad instead of by an average pad.
#
# It differs from a normal capture in three ways, and every one of them is
# about not putting light on an unreacted pad:
#
#   Fewer LEDs.  Only the illuminants the measurement actually uses need a
#     blank. capture.preimage_led_order holds them (royal_blue and red).
#   The flagged exposures only.  Calibration marks which of an LED's tested
#     exposures the pre-image takes (the `preimage` column of the exposure
#     table; one, the middle of the usable range, unless it is told otherwise).
#     Every one of them was tested not to clip the white patch of the test pad.
#   No photograph.  The closing colour photo is taken under the white LED,
#     which is exactly the broadband flash this path exists to avoid.
#
# What it does NOT do is skip the LED warm-up: a blank must be measured to the
# same standard as the reading it corrects, and the warm-up is what makes the
# two comparable. That leaves ~2.6 s of royal blue on the pad against the
# ~3.8 s the measurement sweep itself delivers.
#
# The images land in the same archive as the measurement, under the "/pre"
# section — see capture_h5's module docstring. The pre-image run creates the
# file; the measurement run reopens it with mode="a", by way of
# CaptureConfig.archive_path.
PRE_SECTION = "/pre"

# Pad finding  (detour stage 1, 2026-11-14; see src/padfind.py)
# ------------
# Before a sweep takes the photos it keeps, the pad is found from one frame
# of its own, lit by capture.padfind_led.  That frame need not be a good
# exposure, so the LED gets no warm-up: it is switched on, one fully lit
# frame is taken, and it is switched off again before the fit is worked
# out.  The fit is
# stored in the sweep's `pad` group (capture_h5.add_pad), and a post-image is
# guided by the pre-image's fit, read back from the same archive.
#
# Three settings are config rows, read once per sweep in run_prefetch
# (_read_sweep_settings):
#   capture.padfind_led   The LED the frame is lit by (photo_red).  photo_red
#                         changes the pad least and its dye barely absorbs it,
#                         so the pad's edges look the same before and after
#                         arsine.
#   capture.padfind_exposure_us  The frame's exposure_us (10000).  About a
#                         quarter of photo_red's safe pre-image exposure
#                         (~38 ms), which puts the gray chips near 120 DN
#                         above dark: plenty for edges, and well clear of
#                         clipping on any pad.
#   capture.padfind_offset_pre / _post  The photo_red offsets
#                         (padfind.OFFSET_PRE / OFFSET_POST explain them).
# The other two are constants:
#   PADFIND_MAX_FRAMES   Most frames taken with the LED on while waiting for
#                        one that began after the LED was switched on.
#   PADFIND_DARK_DN      Dark level subtracted before dividing by the chips,
#                        DN.  The measured dark level is 64.00-64.05 in every
#                        layer (C1038-C1047), and pad finding uses only ratios
#                        and half-way levels, so a constant is enough; the
#                        post-image's own dark frames come after its sweep.
PADFIND_MAX_FRAMES  = 4
PADFIND_DARK_DN     = 64.0

# TODO(undecided): consider moving exposure calibration WHOLLY to the
# Menu → Advanced → Calibrate Exposures screen as a standalone operation, run
# outside the context of a sample reading — rather than exposing "calibration" as a capture path that a
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
    # Which capture path to run, and (normal/pre only) which stored exposure
    # group to use — its name is the number of images per LED ('1'..'5').
    mode:           str = MODE_CALIBRATION
    exposure_group: str = "1"
    # RULE_STRIP or RULE_GROUP: see the comment above RULE_STRIP.  With the
    # strip rule the group is still read, for the seed of an LED that has no
    # other (its group-1 exposure) and for the dark frames' estimate.
    # Read from capture.exposure_rule when a config is built; the Capture
    # Suite passes RULE_GROUP.
    exposure_rule:  str = field(default_factory=config.capture_exposure_rule)
    # An existing archive to add this run to, instead of creating one. Set by
    # the measurement run of a sample whose pre-image already opened the file,
    # so both sweeps of one strip land together. None (the default) creates a
    # new archive, which is what every run did before pre-images existed and
    # what a run still does when no pre-image was taken.
    archive_path:   "Path | None" = None
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
        image_index: On the step that stores an image, which one it is
                   within its own kind: 1..image_total for this LED's lit
                   frames, or for the run's dark frames.  0 on other steps.
        image_total: How many images of that kind there are: this LED's lit
                   frames, or the dark frames.  0 on other steps.
    """

    phase:     str
    message:   str
    transient: bool = False
    led_index: int = 0
    led_total: int = 0
    color:     str = ""
    step:      str = ""
    fraction:  float = 0.0
    image_index: int = 0
    image_total: int = 0


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
    runs every required role is guaranteed to have an active row.
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
        {name: Rect} in stream space, guaranteed to fill every
        required role.

    Raises:
        roi.RoiMissing: a required role has no active row.
    """
    size  = getattr(cam, "config_size", None) or (None, None)
    model = getattr(cam, "model", None)
    rois  = roi.load_active(size[0], size[1], model)

    missing = roi.missing_roles(rois, roi.required_roles())
    if missing:
        raise roi.RoiMissing(
            f"the roi table has no active row for role(s) {missing} — "
            f"expected db_bootstrap.ensure() to have seeded it")

    # Bayer-safety and containment in `bound` — see roi.validate for what each
    # severity costs.  Reported here rather than raised: the run measures
    # against whole-frame layers, so what a bad ROI costs is the archive, and
    # refusing to capture at all would cost more.
    for severity, message in roi.validate(rois):
        emit = log.error if severity == roi.ERROR else log.warning
        emit("capture: %s", message)

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
    rgb, bayer, meta, _n, _ok = _settle_frames(cam, exposure_us, prof, max_frames)
    return rgb, bayer, meta


def _gains_match(meta: dict, want) -> "bool | None":
    """
    Whether one frame was rendered with the colour gains `want`.

    Returns None when the frame does not report its gains at all, so a caller
    can tell "could not check" from "checked, and they differ".
    """
    got = meta.get("ColourGains")
    if got is None:
        return None
    return all(abs(float(g) - float(w)) <= GAIN_TOLERANCE * max(abs(float(w)), 1e-6)
               for g, w in zip(got, want))


def _settle_frames(cam, exposure_us: int,
                   prof: "CaptureProfiler | None" = None,
                   max_frames: int = MAX_SETTLE_FRAMES,
                   want_gains=None,
                   ) -> "tuple[np.ndarray, np.ndarray, dict, int, bool | None]":
    """
    _set_and_settle(), also waiting for colour gains, and saying how it went.

    With `want_gains`, a frame is accepted only when its exposure has settled
    AND its metadata reports those gains.  That is how the first frame after a
    rendering change is checked rather than assumed: the frames it takes are
    the evidence, and the caller records them.

    Returns:
        (rgb, bayer, meta, frames_captured, gains_confirmed).
        gains_confirmed is True when the kept frame reports `want_gains`,
        False when the budget ran out first, None when no gains were asked
        for or the camera does not report them.
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
        gains_ok = None if want_gains is None else _gains_match(meta, want_gains)
        if applied > 0:
            delta = abs(applied - exposure_us) / max(exposure_us, 1)
            if delta <= SETTLE_TOLERANCE and gains_ok is not False:
                log.debug(f"    settled: {applied}µs ≈ {exposure_us}µs "
                          f"after {frame_num + 1} frame(s)")
                return rgb, bayer, meta, frame_num + 1, gains_ok
        log.debug(f"    settling [{frame_num + 1}/{max_frames}]: "
                  f"requested={exposure_us}µs  applied={applied}µs"
                  f"{'' if gains_ok is not False else '  gains not yet applied'}")
    applied = int(last[2].get("ExposureTime", 0))  # type: ignore[index]
    gains_ok = None if want_gains is None else _gains_match(last[2], want_gains)
    gains_note = ("" if want_gains is None else
                  f"  gains reported={last[2].get('ColourGains')} "
                  f"requested={tuple(want_gains)}")
    log.warning(f"    frame did not settle after {max_frames} frames; "
                f"last applied={applied}µs  requested={exposure_us}µs{gains_note}")
    return (*last, max_frames, gains_ok)  # type: ignore[misc]


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
        # (frames, gains_confirmed) from the last _settle() — see
        # _settle_frames.  gains_confirmed is None unless gains were asked for.
        self._settle_evidence: "tuple[int, bool | None]" = (0, None)
        # The normal path's first lit frame confirms the neutral rendering is
        # back (a white frame or a preview may have left the photo's gains
        # requested); once it has, the rest of the run does not ask again.
        self._neutral_unchecked = True
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
        self._color_group:    "dict[str, str]"  = {}   # group each LED actually used
        # Strip rule only: {color: (seed exposure_us, where it came from)},
        # the first exposure_us tried for each LED (_seed_exposure).
        self._color_seed: "dict[str, tuple[int, str]]" = {}
        # Strip rule only: the test region every frame of this sweep is
        # judged on (_test_region), built once the pad is found.
        self._region: "exposure_rule.TestRegion | None" = None
        # This sweep's settings, read from the config table at the start of
        # run_prefetch (_read_sweep_settings).  The factory values until
        # then.  _rule: the strip rule's numbers.  _padfind_*: the LED,
        # exposure_us and (left, right, top, bottom) offset of the pad-
        # finding frame.
        self._rule: "exposure_rule.RuleSettings" = exposure_rule.DEFAULTS
        self._padfind_led: str = "photo_red"
        self._padfind_us: int = 10_000
        self._padfind_offset: "tuple | None" = None
        # Strip rule: this sweep's exposure_log rows (exposure_log_repo),
        # gathered as each LED finishes and written once, after the last
        # coloured LED (_flush_exposure_log).  _samp_no: the sample they
        # belong to, or None.
        self._log_rows: "list[dict]" = []
        self._samp_no: "str | None" = None
        # The archive's root attributes (capture_meta.root_attrs), kept from
        # _write_metadata: the camera model, frame size and serial that the
        # hot-pixel lookup needs.
        self._root_attrs: dict = {}
        # Strip rule: every lit exposure_us the current sweep stored.
        self._lit_us_taken: "list[int]" = []
        # Normal/pre path: the run's two dark exposures (DARK_SHORT_US and the
        # longest lit exposure), and the white LED's one frame — see
        # run_prefetch().  _white_us is None when no white frame is taken.
        self._dark_us: "list[int]" = []
        self._white_us: "int | None" = None
        self._white_measure: bool = False
        self._white_source: str = ""
        # time.monotonic() when the last LED went out, so the dark frames can
        # wait out only what is left of LED_OFF_SETTLE_S.
        self._dark_since: float = 0.0
        # {(color, exposure_us): channels to keep}, so a repeated exposure does
        # not re-query the database.
        self._keep_cache: dict = {}

        # Where this sweep found the pad (_find_pad), in layer px of the bound
        # crop.  pad: the fit after the photo_red offset, the one to measure
        # on.  pad_before: the fit before it, which a post-image is guided by.
        # Both None when pad finding failed or was not run; pad_problem then
        # says why ('' when it worked and passed its quality check).
        self.pad: "padfind.PadFit | None" = None
        self.pad_before: "padfind.PadFit | None" = None
        self.pad_problem: str = ""

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

    def _settle(self, exposure_us: int,
                want_gains=None) -> "tuple[np.ndarray, np.ndarray, dict]":
        """
        set_exposure + capture until the pipeline adopts it, timing frames.

        With `want_gains`, also until a frame reports those colour gains (see
        _settle_frames).  How many frames it took, and whether the gains were
        confirmed, are left in self._settle_evidence.

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
        rgb, bayer, meta, n, ok = _settle_frames(
            self._cam, exposure_us, self.prof, want_gains=want_gains)
        self._frame_captured_dt = capture_meta.timestamp_ms()
        self._settle_evidence = (n, ok)
        return rgb, bayer, meta

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

        A run handed cfg.archive_path skips the naming entirely and writes
        into that file: it is joining a strip's pre-image rather than opening
        an archive of its own, and picking a free name is the last thing it
        wants. The name is only chosen once, by whichever run gets there
        first.
        """
        cfg = self.config
        self.suite_id = config.capture_suite_id()
        cfg.out_dir.mkdir(parents=True, exist_ok=True)
        if cfg.archive_path is not None:
            self.out_path = Path(cfg.archive_path)
            return
        stem = sample_repo.samp_no_for(cfg.sample_rowid) or self.suite_id
        self.out_path = config.capture_archive_path(cfg.out_dir, stem)

    def _open_writer(self) -> CaptureSuiteWriter:
        """
        Open this run's archive and remember it for cleanup.

        Where the run writes inside the file, and whether the file is created
        or added to, are both decided here so the two capture paths cannot
        answer them differently.

        A pre-image writes under PRE_SECTION; everything else writes at the
        root. Appending is decided by what is on disk rather than by the mode,
        because the two facts that matter are "somebody named this file for me"
        and "it is already there" — a measurement run resuming a pre-imaged
        sample meets both, and a pre-image whose archive somehow already exists
        must add to it rather than destroy a capture that cannot be retaken.

        Returns:
            The open writer, also stored in self._writer.
        """
        section = PRE_SECTION if self._is_pre() else ""
        appending = (self.config.archive_path is not None
                     and self.out_path.exists())
        w = self._writer = CaptureSuiteWriter(
            self.out_path, suite_id=self.suite_id,
            section=section, mode="a" if appending else "w")
        log.info("archive %s %s%s", "appending to" if appending else "creating",
                 self.out_path, f" (section {section})" if section else "")
        return w

    def _is_pre(self) -> bool:
        """True when this run is a pre-image — see MODE_PRE."""
        return str(self.config.mode).lower() == MODE_PRE

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
        self._root_attrs = capture_meta.root_attrs(meta)
        w.add_metadata(meta, self._root_attrs)

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

    # ── Final colour photograph ──────────────────────────────────────

    def _photo_wanted(self) -> bool:
        """
        Whether this run takes the colour photograph.

        Never on a pre-image: the white LED is broadband, so this one frame
        would put more blue on an unreacted pad than the whole rest of that
        path, and there is nothing to look at yet anyway.  Otherwise as config's
        capture.photo_enabled says, provided there is a ROLE_BOUND crop to
        store.  Reading config can fail on an instrument whose config predates
        the photo; that costs the photo, never the run.
        """
        if self._is_pre():
            return False
        try:
            if not config.capture_photo_enabled():
                return False
            bound = roi.by_role(self.rois, roi.ROLE_BOUND)
        except Exception:                                  # noqa: BLE001
            log.exception("photo: cannot tell whether to photograph — skipping")
            return False
        if bound is None:
            log.warning("photo: no active ROLE_BOUND ROI — skipping the photo")
            return False
        return True

    def _white_frame(self, exposure_us: int, gains) -> Iterator[Progress]:
        """
        Light the white LED once and take one frame; return it with the evidence.

        With `gains`, the frame is rendered colour-balanced for the photograph.
        The raw Bayer stream is tapped off upstream of every control involved
        (BayerCam.set_color_rendering), so the same frame also serves as the
        white LED's measurement — one exposure of the pad, not two.

        The change of rendering is not assumed to take effect at once: frames
        are captured until one reports the gains asked for (_settle_frames),
        and how many that took is returned, so the archive records whether the
        first frame would have been good enough.

        Coming out, the LED is put out and the neutral rendering requested,
        without waiting for a frame to report it.  Nothing in this run reads
        the colour image again, and the next run's first lit frame checks the
        neutral gains have arrived (run_capture), so frames spent here would
        only make every run longer.

        Use as `frame, evidence = yield from self._white_frame(...)`.

        Returns:
            ((rgb, bayer, meta), evidence) where evidence holds
            gain_frames / gains_confirmed.
        """
        evidence: dict = {}
        self._led_on(WHITE_LED_CH)
        applied = False
        try:
            yield from self._wait(LED_ON_WARMUP_S, "White LED on, warming up")
            if gains is not None:
                self._cam.set_color_rendering(gains)
                applied = True
            rgb, bayer, meta, n, ok = _settle_frames(
                self._cam, exposure_us, self.prof, want_gains=gains)
            self._frame_captured_dt = capture_meta.timestamp_ms()
            evidence["gain_frames"] = n
            evidence["gains_confirmed"] = ok
            if gains is not None and ok is False:
                log.warning("white: colour gains %s not reported after %d frames "
                            "— photo may be untinted", tuple(gains), n)
        finally:
            self._led_off(WHITE_LED_CH)
            if applied:
                try:
                    self._cam.set_objective_rendering()
                except Exception:                          # noqa: BLE001
                    log.exception("white: requesting the neutral rendering failed")
        return (rgb, bayer, meta), evidence

    def _store_photo(self, rgb, exposure_us: int, meta: dict, gains,
                     evidence: dict) -> Iterator[Progress]:
        """Encode the colour-balanced frame's ROLE_BOUND crop into /photo."""
        w = self._writer
        bound = roi.by_role(self.rois, roi.ROLE_BOUND)
        try:
            with self.prof.time("io.jpeg"):
                jpeg = _encode_jpeg(BayerCam.crop_rgb(rgb, bound))
            attrs = {
                "exposure_us":   int(meta.get("ExposureTime", exposure_us)),
                "led_color":     "white",
                "colour_gain_r": float(gains[0]),
                "colour_gain_b": float(gains[1]),
                "captured_dt":   self._frame_captured_dt,
            }
            # The first-frame evidence: how many frames it took for the gains
            # to reach a frame.  None ("could not tell") is left out rather
            # than written as an HDF5 attribute.
            attrs.update({k: (int(v) if isinstance(v, int) and not isinstance(v, bool)
                              else bool(v))
                          for k, v in evidence.items() if v is not None})
            reported = meta.get("ColourGains")
            if reported is not None:
                attrs["reported_gain_r"] = float(reported[0])
                attrs["reported_gain_b"] = float(reported[1])
            with self.prof.time("io.hdf5"):
                w.add_photo(jpeg, filename=f"{OUT_STEM}_photo.jpg", crop=bound,
                            attrs=attrs)
            log.info("photo: stored %d bytes, cropped to %s  (%s)",
                     len(jpeg), bound.xywh(), evidence)
            yield self._progress(
                "capture", f"    stored photo ({len(jpeg) // 1024} kB)",
                color="white", step="photo")
        except Exception:                                  # noqa: BLE001
            log.exception("photo: failed — the run's measurements are unaffected")
            yield self._progress("capture", "    photo failed (measurements intact)",
                                 color="white", step="photo")

    def _capture_photo(self, exposure_us: int) -> Iterator[Progress]:
        """
        Calibration path: photograph the sample under the white LED, last of all.

        Every frame before this one is deliberately colour-neutral, because the
        measurement is the Bayer data.  This frame exists for a person to look
        at, so it is rendered with the white LED's commissioned colour gains.
        Only the ROLE_BOUND crop is stored.  Any failure is logged and
        swallowed: the run's measurements are already written.

        The normal path takes its photograph in _capture_white() instead, from
        the same frame as its white measurement.

        Args:
            exposure_us: A white-LED exposure the run already established.
        """
        if self._writer is None or not self._photo_wanted():
            return
        self.prof.set_section("photo")
        yield self._progress("capture", "Photo: colour-balanced, white LED",
                             color="white", step="photo")
        try:
            gains = config.capture_photo_wb_gains()
            (rgb, _, meta), evidence = yield from self._white_frame(exposure_us, gains)
        except Exception:                                  # noqa: BLE001
            log.exception("photo: failed — the run's measurements are unaffected")
            yield self._progress("capture", "    photo failed (measurements intact)",
                                 color="white", step="photo")
            return
        yield from self._store_photo(rgb, exposure_us, meta, gains, evidence)

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
            if str(self.config.mode).lower() in (MODE_NORMAL, MODE_PRE):
                # Chained fallback: a caller that never split the run across
                # the reagent2 pause (camera contention, a short debug timer)
                # still gets the new capture order and dark-frame dedup, just
                # without the latency-hiding — see run_prefetch()/run_capture().
                #
                # A pre-image always comes through here rather than being
                # split: there is no pause to hide latency behind. The operator
                # is standing at the instrument having just seated the strip,
                # and the whole run is two LEDs long.
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
        self._dark_since = time.monotonic()
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
        white_lr    = roi.to_layer_rect(roi.by_role(self.rois, roi.ROLE_WHITE_BALANCE))
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
        w = self._open_writer()
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

            for k, (exp_label, exp_us) in enumerate(zip(EXP_LABELS, exposures_us), 1):
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
                    step=exp_label, image_index=k, image_total=len(exposures_us))

            # LED off, then dark frames at the same exposures
            self._led_off(led)
            meta_json["led_off_dt"] = self._led_off_dt
            yield from self._wait(LED_OFF_SETTLE_S, f"{color} LED off, settling")

            yield self._progress("capture", "Dark captures:")
            for k, (exp_label, exp_us) in enumerate(zip(EXP_LABELS, exposures_us), 1):
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
                    step=f"dark {exp_label}", image_index=k,
                    image_total=len(exposures_us))

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

        # The white LED's Phase 0 exposure was measured unsaturated on this
        # very scene, so the photograph reuses it rather than searching again.
        yield from self._capture_photo(calibrated_us)

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
    # Dark frames: two per sweep, statistics only (see _take_darks). Once a
    # whole ladder of them was kept, one per distinct lit exposure, with every
    # LED linked to its own; the dark analysis (comment above
    # capture_h5.dark_level) found the level the same at every exposure and a
    # dark frame's pixels to be fresh noise, so the analysis subtracts one mean
    # per layer and nothing else needs the ladder. A pre-image takes its pair
    # "before" its first LED; a measurement takes its pair "after" its last
    # coloured LED and before the white one. The per-LED dark links are gone
    # with the ladder: dark_level reads the section's /dark directly.

    def _stored_exposures(self, color: str, group: str) -> "tuple[list[int], str]":
        """
        The exposures to shoot for one LED, and the group they came from.

        A pre-image takes the ones flagged for it; if none are flagged, the
        middle of group 1.  Any other run takes the whole stored group, or,
        when calibration could not fit that group for this LED (its usable
        range too narrow for that many brackets), the largest smaller group
        that it did fit: 3 -> 2 -> 1.  A narrow LED then still gets as many
        exposures as it can hold, rather than dropping straight to the one
        default exposure.  Empty when the LED has no usable calibration at all.

        Returns:
            (exposures_us ascending, group used).  The group is `group`
            itself for a pre-image and when nothing was found.
        """
        if self._is_pre():
            flagged = exposure_repo.read_preimage_exposures(color)
            if flagged:
                return flagged, group
            middle = exposure_repo.middle_exposure(color, "1")
            return ([middle] if middle else []), group
        tries = ([str(g) for g in range(int(group), 0, -1)]
                 if str(group).isdigit() else [str(group)])
        for g in tries:
            exposures = exposure_repo.read_exposures(color, g)
            if exposures:
                if g != str(group):
                    log.warning("LED %s has no exposures for group %s — "
                                "using group %s", color, group, g)
                return exposures, g
        return [], group

    def _white_exposure(self) -> "tuple[int, str]":
        """
        The one exposure the white LED is taken at, and where it came from.

        Group 1's exposure: the one calibration centred in the range that keeps
        the test pad and the gray chips inside the window.  The run's default
        when white has no calibration.
        """
        us = exposure_repo.middle_exposure("white", "1")
        if us:
            return int(us), "exposure_table group 1"
        log.warning("white LED has no group-1 exposure — falling back to %d µs",
                    self.config.initial_us)
        return int(self.config.initial_us), "fallback"

    # ── Pad finding ──────────────────────────────────────────────────

    def _find_pad(self) -> Iterator[Progress]:
        """
        Find the pad from one capture.padfind_led frame, and store where
        it is.

        Sets self.pad, self.pad_before and self.pad_problem, and writes the
        sweep's `pad` group.  Never raises for a pad it cannot find, and
        never stops the run: a failure is logged, stored and reported in
        pad_problem, and the sweep goes on without a pad.

        Timings, in seconds, go to the archive and to the profile (section
        'padfind').  Libraries are imported when this module loads, so no
        timer includes loading them.
          total_s    from the start of this step to the end of the fit
                     (the archive write after it is timed as io.hdf5)
          led_on_s   from switching the LED on to switching it off
          fit_s      the fit alone, with the LED already off
        """
        section = (padfind.SECTION_PRE if self._is_pre()
                   else padfind.SECTION_POST)
        self.pad = self.pad_before = None
        self.pad_problem = ""
        try:
            channel = self._leds.channel_of(self._padfind_led)
        except ValueError:
            self.pad_problem = f"no {self._padfind_led} LED on this bank"
            log.warning("pad finding skipped: %s", self.pad_problem)
            return

        self.prof.set_section("padfind")
        yield self._progress("capture",
                             f"Finding the pad ({self._padfind_led})",
                             step="padfind")
        # Attributes for the archive: plain numbers and text only.
        info = {"led": self._padfind_led, "section": section,
                "exposure_us": self._padfind_us,
                "dark_dn": PADFIND_DARK_DN,
                "offset": list(self._padfind_offset or ())}
        layer = before = after = None
        t_start = time.perf_counter()
        try:
            layer, frame_info = self._padfind_frame(channel)
            info.update(frame_info)
            prior = self._padfind_prior(layer.shape) if section == \
                padfind.SECTION_POST else None
            info["guided_by_pre"] = int(prior is not None)
            t_fit = time.perf_counter()
            with self.prof.time("padfind.fit"):
                before, after = padfind.find_pad(
                    layer, PADFIND_DARK_DN, self._padfind_rects(), section,
                    prior, offset=self._padfind_offset)
            info["fit_s"] = round(time.perf_counter() - t_fit, 4)
            self.pad, self.pad_before = after, before
            self.pad_problem = after.qc()
            info["qc"] = self.pad_problem
            info["describe"] = after.describe()
        except (padfind.PadFindError, KeyError, ValueError) as exc:
            # KeyError/ValueError: a ROI the fit needs is missing or bad.
            self.pad_problem = f"pad not found: {exc}"
            info["error"] = str(exc)
        info["total_s"] = round(time.perf_counter() - t_start, 4)
        self.prof.add("padfind.total", info["total_s"])
        if layer is not None and self._strip_rule():
            self._log_padfind_frame(layer, info, section)

        with self.prof.time("io.hdf5"):
            self._writer.add_pad(
                layer, before.to_attrs() if before else None,
                after.to_attrs() if after else None, info)
        if self.pad is not None and not self.pad_problem:
            log.info("pad (%s): %s", section, self.pad.describe())
        else:
            log.warning("pad (%s): %s", section, self.pad_problem)
        log.info("pad finding (%s): total %.3f s, LED on %.3f s, fit %s s, "
                 "%s lit frame(s), rule %s", section, info["total_s"],
                 info.get("led_on_s", float("nan")), info.get("fit_s", "-"),
                 info.get("frames_lit", 0), info.get("lit_frame_rule", "-"))
        yield self._progress(
            "capture", f"    pad: {self.pad_problem or 'found'}",
            step="padfind")

    def _log_padfind_frame(self, layer: np.ndarray, info: dict,
                           section: str) -> None:
        """
        Measure the pad-finding frame and queue its exposure_log row.

        Measured like every strip-rule frame, over the test region; the
        region is built here, from the fit just made, and kept for the
        sweep's frames (_strip_frame would build the same one).

        Args:
            layer:   The frame's R layer, bound crop, raw DN.
            info:    The pad record (_find_pad): captured_dt, applied_us.
            section: 'pre' or 'post'.
        """
        self._region = self._test_region(layer.shape)
        with self.prof.time("cpu.measure"):
            stats = exposure_rule.measure({"R": layer}, self._region,
                                          self._rule)
        self._log_rows += exposure_log_repo.layer_rows(
            {ch: st.to_dict() for ch, st in stats.items()},
            stored_layers=("R",),                 # in the sweep's pad group
            add_dt=info.get("captured_dt"), samp_no=self._samp_no,
            archive=self.out_path.name, section=section,
            purpose=exposure_log_repo.PURPOSE_PADFIND,
            LED=self._padfind_led, exposure_us=self._padfind_us,
            applied_us=info.get("applied_us"),
            dark_dn=exposure_rule.DARK_DN)

    def _queue_led_log_rows(self, color: str, lit: dict) -> None:
        """
        Queue one LED's exposure_log rows (strip rule), from what
        _store_strip_frames returned: every try and every stored frame.
        """
        frames = {lb: {"exposure_us": us,
                       "applied_us": lit["applied_us"].get(lb),
                       "captured_dt": lit["record"]["captured_dt"].get(lb),
                       "stored": lit["kept"].get(lb, [])}
                  for lb, us in zip(lit["labels"], lit["exposures_us"])}
        self._log_rows += exposure_log_repo.rows_for_led(
            color, lit["record"], frames, samp_no=self._samp_no,
            archive=self.out_path.name,
            section="pre" if self._is_pre() else "post",
            dark_dn=exposure_rule.DARK_DN)

    def _flush_exposure_log(self) -> None:
        """
        Write the queued exposure_log rows, once, and empty the queue.

        Called after the last coloured LED is off, and again (with nothing
        left to write) when the run ends however it ends.  A failure is
        logged and dropped: the archive holds the same numbers, and a log
        write must never cost a capture.
        """
        rows, self._log_rows = self._log_rows, []
        if not rows:
            return
        try:
            with self.prof.time("io.exposure_log"):
                n = exposure_log_repo.write_rows(rows)
            log.info("exposure_log: %d rows written", n)
        except Exception:                             # noqa: BLE001
            log.exception("exposure_log: %d rows not written (the archive "
                          "has the same numbers)", len(rows))

    def _padfind_frame(self, channel: int) -> "tuple[np.ndarray, dict]":
        """
        Take the pad-finding frame: settle in the dark, flash, frame, off.

        1. With every LED off, set capture.padfind_exposure_us and capture
           until the camera reports it (_settle_frames).  Those frames are dark and are
           thrown away; doing this first keeps the settling out of the LED's
           on-time.
        2. Switch the LED on and capture until a frame *began* after that
           moment.  A frame already being read out when the LED came on is
           partly dark (the sensor reads its rows one after another), so it
           must not be used.  Each frame's SensorTimestamp (libcamera: when
           its first row started, in ns on the CLOCK_BOOTTIME clock) is
           compared with the switch-on time on the same clock.  A camera that
           reports no SensorTimestamp gets the second frame instead.
           Frames are held unconverted (BayerCam.capture_held), so waiting
           out a rejected frame costs only the frame itself.
        3. Switch the LED off, in a `finally`, so it goes off however this
           ends, and only then convert the kept frame to its raw Bayer array
           (BayerCam.bayer_of).  On the Pi the conversion is the slow part.

        Args:
            channel: The LED bank channel of capture.padfind_led.

        Returns:
            (R layer of the bound crop in raw DN, {name: value} for the
            archive: applied_us, frames_dark, frames_lit, lit_frame_rule,
            led_on_s, captured_dt).
        """
        cam = self._cam
        _, _, _, frames_dark, _ = _settle_frames(
            cam, self._padfind_us, self.prof)

        boottime = getattr(time, "CLOCK_BOOTTIME", None)
        frames_lit, meta, rule = 0, {}, "frame budget ran out"
        request = None          # the held frame not yet converted or dropped
        try:
            t_on = time.perf_counter()
            self._led_on(channel)
            on_ns = (time.clock_gettime_ns(boottime) if boottime is not None
                     else None)
            try:
                # Frames are held, not converted, so a rejected frame costs
                # only its wait, and the LED goes off before the slow part.
                while frames_lit < PADFIND_MAX_FRAMES:
                    if request is not None:
                        cam.release_held(request)
                        request = None
                    request, meta = cam.capture_held()
                    frames_lit += 1
                    started_ns = meta.get("SensorTimestamp")
                    if started_ns is not None and on_ns is not None:
                        if int(started_ns) >= on_ns:
                            rule = "SensorTimestamp"
                            break
                    elif frames_lit >= 2:
                        rule = "second frame"
                        break
            finally:
                self._led_off(channel)
            led_on_s = time.perf_counter() - t_on
            held, request = request, None
            bayer = cam.bayer_of(held)          # releases the frame
        finally:
            if request is not None:             # an error left one held
                cam.release_held(request)
        self.prof.add("padfind.led_on", led_on_s)
        if rule == "frame budget ran out":
            log.warning("pad finding: no frame began after the LED came on "
                        "within %d frames; using the last", frames_lit)
        layer = self._bound_layers(bayer, self._pattern)["R"]
        return layer, {"applied_us": int(meta.get("ExposureTime", 0)),
                       "frames_dark": int(frames_dark),
                       "frames_lit": frames_lit, "lit_frame_rule": rule,
                       "led_on_s": round(led_on_s, 4),
                       "captured_dt": capture_meta.timestamp_ms()}

    def _padfind_rects(self) -> "dict[str, Rect]":
        """
        The sample ROI and the two gray chips, in layer px of the bound crop.

        The chips are the white_balance ROIs, taken in name order as gray1
        and gray2 (their names in every commissioned database).

        Raises:
            ValueError: there is no sample ROI or fewer than two gray chips.
        """
        bound = roi.by_role(self.rois, roi.ROLE_BOUND)
        sample = roi.by_role(self.rois, roi.ROLE_SAMPLE)
        chips = sorted(roi.all_by_role(self.rois, roi.ROLE_WHITE_BALANCE),
                       key=lambda r: r.name)
        if sample is None or len(chips) < 2:
            raise ValueError("pad finding needs a sample ROI and two gray "
                             "chips (white_balance ROIs)")
        return {"sample": roi.to_layer_rect_within(sample, bound),
                "gray1": roi.to_layer_rect_within(chips[0], bound),
                "gray2": roi.to_layer_rect_within(chips[1], bound)}

    def _padfind_prior(self, shape) -> "padfind.PadFit | None":
        """
        The pre-image's pad fit before its offset, to guide a post-image.

        Read from the archive's /pre/pad group.  None when the archive holds
        no pre-image fit, or when it was made on a crop of another size (the
        bound ROI was edited between the two sweeps), in which case the
        post-image is fitted on its own.

        Args:
            shape: (rows, columns) of this sweep's pad-finding layer.
        """
        stored = self._writer.read_pad(PRE_SECTION)
        if not stored or not stored.get("before_offset"):
            return None
        prior = padfind.PadFit.from_attrs(stored["before_offset"])
        if tuple(prior.shape) != tuple(shape):
            log.warning("pad finding: the pre-image was fitted on a %s crop, "
                        "this sweep is %s; fitting without it",
                        prior.shape, tuple(shape))
            return None
        return prior

    # ── The strip exposure rule (detour stage 3; src/exposure_rule.py) ───

    def _strip_rule(self) -> bool:
        """True when this pre-image or measurement uses RULE_STRIP."""
        return (str(self.config.exposure_rule).lower() == RULE_STRIP
                and str(self.config.mode).lower() in (MODE_PRE, MODE_NORMAL))

    def _read_sweep_settings(self) -> None:
        """
        Read this sweep's settings from the config table, once.

        Sets _padfind_led, _padfind_us and _padfind_offset (both rules find
        the pad), and, for the strip rule, _rule (exposure_rule.
        RuleSettings.from_config()).  Logged, and stored in each LED's
        strip-rule record, so an archive says what it was taken with.

        Raises:
            config_repo.ConfigMissing: a row is missing.
            ValueError: a value makes no sense (named in the message).
        """
        self._padfind_led = config.capture_padfind_led()
        self._padfind_us = config.capture_padfind_exposure_us()
        self._padfind_offset = config.capture_padfind_offset(
            padfind.SECTION_PRE if self._is_pre() else padfind.SECTION_POST)
        if self._strip_rule():
            self._rule = exposure_rule.RuleSettings.from_config()
            log.info("strip rule settings: %s", self._rule.to_dict())

    def _seed_exposure(self, color: str) -> "tuple[int, str]":
        """
        The first exposure_us the strip rule tries for one LED, and its source.

        In order of preference:
          1. a measurement: this strip's own pre-image Tus, read from the
             `tus_us` attribute of /pre/leds/<color> in the same archive.
             Dye only darkens the pad, so it should not clip there.
          2. exposure_rule.SEED_US (the D1 study's safe values).
          3. the LED's group-1 exposure from the `exposure` table.
          4. the run's default exposure_us (CaptureConfig.exposure_ms).

        Returns:
            (exposure_us, source), source one of 'pre-image Tus', 'D1 seed',
            'group 1', 'fallback'.
        """
        if not self._is_pre():
            tus = self._writer.led_attrs(color, PRE_SECTION).get("tus_us")
            if tus:
                return int(tus), "pre-image Tus"
        if color in exposure_rule.SEED_US:
            return int(exposure_rule.SEED_US[color]), "D1 seed"
        middle = exposure_repo.middle_exposure(color, "1")
        if middle:
            return int(middle), "group 1"
        return int(self.config.initial_us), "fallback"

    def _hot_mask(self, shape) -> np.ndarray:
        """
        Hot pixels on this sweep's layer grid (any Bayer layer), True where hot.

        Read from the `hotpixels` table for this camera (model, frame size
        and serial from the archive's root attributes), and moved from
        whole-sensor layer coordinates onto the bound crop.  The union over
        the four layers, as the desktop analysis uses (probe.hot_mask).  All
        False, with a log line, when the table has nothing for this camera
        or cannot be read: a missing hot-pixel map must not stop a capture.

        Args:
            shape: (rows, cols) of the crop's Bayer layers.
        """
        mask = np.zeros(shape, dtype=bool)
        a = self._root_attrs
        try:
            rows = hotpixel_repo.read_active(
                str(a.get("sensor_model", "")), int(a.get("frame_w", 0)),
                int(a.get("frame_h", 0)), hardware_sn=a.get("hardware_sn"))
        except Exception:                             # noqa: BLE001
            log.exception("hot pixels could not be read; none left out")
            return mask
        if not rows:
            log.warning("no hot pixels on record for %s %sx%s %s; none left "
                        "out of the test region", a.get("sensor_model"),
                        a.get("frame_w"), a.get("frame_h"),
                        a.get("hardware_sn"))
            return mask
        bound = roi.by_role(self.rois, roi.ROLE_BOUND)
        oy, ox = bound.y // 2, bound.x // 2
        for r in rows:
            y, x = r.y - oy, r.x - ox
            if 0 <= y < shape[0] and 0 <= x < shape[1]:
                mask[y, x] = True
        return mask

    def _test_region(self, shape) -> "exposure_rule.TestRegion":
        """
        The pixels every frame of this sweep is judged on.

        The fitted pad (not eroded) when pad finding worked and passed its
        checks, otherwise the sample ROI; plus the gray1 and gray2 chips;
        hot pixels left out.  If the ROI table lacks the sample ROI or the
        chips, the whole crop, with a warning.

        Args:
            shape: (rows, cols) of the crop's Bayer layers.
        """
        hot = self._hot_mask(shape)
        try:
            rects = self._padfind_rects()
        except ValueError as exc:
            log.warning("test region: %s; judging the whole crop", exc)
            return exposure_rule.TestRegion(
                parts={"pad": np.ones(shape, dtype=bool)}, hot=hot,
                pad_source="whole crop")
        dist = None
        if (self.pad is not None and not self.pad_problem
                and tuple(self.pad.shape) == tuple(shape)):
            pad_mask, source = self.pad.mask(), "fit"
            # For the edge rule of the noise-floor test (exposure_rule.
            # edge_left_out): only a fitted pad has edges to measure from.
            dist = self.pad.edge_distances()
        else:
            s = rects["sample"]
            pad_mask = np.zeros(shape, dtype=bool)
            pad_mask[max(s.y, 0):s.y + s.h, max(s.x, 0):s.x + s.w] = True
            source = "sample ROI"
            log.warning("test region: using the sample ROI (%s)",
                        self.pad_problem or "no pad fit")
        return exposure_rule.TestRegion.from_rects(
            shape, pad_mask, rects["gray1"], rects["gray2"], hot, source,
            dist)

    def _strip_frame(self, exposure_us: int):
        """
        One frame at `exposure_us`, measured over the test region.

        Also does the run's one neutral-rendering check on its first lit
        frame, exactly as the group rule does (see run_capture).

        Returns:
            dict with rgb, bayer, meta, layers (bound crop, raw DN),
            stats ({layer: LayerStats}), exposure_us (requested),
            applied_us, captured_dt, and render_evidence (non-empty only on
            the frame that did the check).
        """
        want = NEUTRAL_GAINS if self._neutral_unchecked else None
        rgb, bayer, meta = self._settle(exposure_us, want_gains=want)
        evidence: dict = {}
        if want is not None:
            self._neutral_unchecked = False
            n, ok = self._settle_evidence
            evidence = {"neutral_frames": n, "neutral_confirmed": ok}
            if ok is False:
                log.warning("neutral rendering not reported after %d frames "
                            "— this LED's JPEG may be tinted", n)
        layers = self._bound_layers(bayer, self._pattern)
        if self._region is None:
            self._region = self._test_region(next(iter(layers.values())).shape)
        with self.prof.time("cpu.measure"):
            stats = exposure_rule.measure(layers, self._region, self._rule)
        self._captures_done += 1
        return {"rgb": rgb, "bayer": bayer, "meta": meta, "layers": layers,
                "stats": stats, "exposure_us": int(exposure_us),
                "applied_us": int(meta.get("ExposureTime", 0)) or int(exposure_us),
                "captured_dt": self._frame_captured_dt,
                "render_evidence": evidence}

    def _strip_lit_frames(self, color: str) -> Iterator[Progress]:
        """
        Take one LED's frames by the strip rule.  The LED is already lit and
        warmed up; this does not switch it, and stores nothing.

        Every number comes from this sweep's settings (self._rule).

        1. From the seed, take up to max_exposure_tries frames,
           re-aiming after each (exposure_rule.judge), until one is accepted.
           The last frame is kept whatever it shows.  Rejected frames are
           measured and recorded, not stored.
        2. Take multiple_exposure_count frames at Tus in all, the kept frame
           being the first.
        3. If an analysis channel has a test-region pixel below the noise
           floor at Tus, take bus_exposure_count frames at Bus.
        4. Count the pixels that are usable at neither (exposure_rule.coverage),
           over the decision layers stored at Tus: one that is not stored
           (red.B under a red LED, always dark) is not counted.

        Returns (as the generator's return value, via `yield from`):
            {"frames": [(role, frame dict), ...] in the order taken,
             "record": the strip rule's JSON record for led_metadata}
        """
        R, s = exposure_rule, self._rule
        seed_us, seed_source = self._color_seed[color]
        attempts: list = []
        us = seed_us
        for attempt in range(1, s.max_exposure_tries + 1):
            frame = self._strip_frame(us)
            decision = R.decision_layers(color, frame["layers"], s)
            verdict, next_us, why = R.judge(frame["stats"], decision,
                                            frame["applied_us"], s)
            attempts.append({
                "attempt": attempt, "exposure_us": frame["exposure_us"],
                "applied_us": frame["applied_us"], "verdict": verdict,
                "decided_by": why, "next_us": next_us,
                "captured_dt": frame["captured_dt"],
                "stats": {ch: st.to_dict()
                          for ch, st in frame["stats"].items()}})
            st = frame["stats"][why]
            yield self._progress(
                "capture",
                f"    try {attempt}: {frame['exposure_us']} µs, {verdict} "
                f"({why} max {st.max_dn:.0f}, {st.n_clip_top} clipped)",
                step="tus")
            if (verdict == R.ACCEPT or attempt == s.max_exposure_tries
                    or next_us == frame["applied_us"]):
                break
            us = next_us
        tus_status = "ok" if verdict == R.ACCEPT else verdict
        tus_us = frame["exposure_us"]
        if tus_status == R.CLIPPED:
            log.warning("%s: still clipped after %d frames at %d µs — LED "
                        "marked failed", color, attempt, tus_us)
            yield self._progress(
                "capture", f"    {color}: STILL CLIPPED after {attempt} "
                           f"tries — LED marked failed", step="tus")
        elif tus_status == R.DIM:
            log.warning("%s: brightest pixel still under %.0f %% of %d after "
                        "%d frames at %d µs; kept", color,
                        100 * s.top_floor_fraction, s.clip_top_dn, attempt,
                        tus_us)

        frames = [("tus", frame)]
        first = frame
        for _ in range(s.multiple_exposure_count - 1):
            frames.append(("tus", self._strip_frame(tus_us)))
            yield self._progress("capture", f"    Tus frame {len(frames)} "
                                 f"({tus_us} µs)", step="tus")

        bus_us, bus_layer, bus_min = R.bus_exposure(
            first["stats"], color, first["applied_us"], s)
        bus_first = None
        if bus_us is not None:
            for _ in range(s.bus_exposure_count):
                f = self._strip_frame(bus_us)
                bus_first = bus_first or f
                frames.append(("bus", f))
            yield self._progress(
                "capture", f"    Bus {bus_us} µs ({color}.{bus_layer} darkest "
                           f"{bus_min:.0f} at Tus), {s.bus_exposure_count} "
                           f"frame(s)", step="bus")

        decision = R.decision_layers(color, first["layers"], s)
        kept_at_tus, _ = R.keep_layers(color, first["stats"], s)
        cover = R.coverage(first["layers"],
                           bus_first["layers"] if bus_first else None,
                           self._region,
                           [ch for ch in decision if ch in kept_at_tus], s)
        short = {ch: c["n_below_floor_both"] for ch, c in cover.items()
                 if c["n_below_floor_both"]}
        if short:
            log.warning("%s: test-region pixels below the floor at Tus and "
                        "Bus: %s", color, short)

        record = {
            "exposure_rule": RULE_STRIP,
            "seed_us": seed_us, "seed_source": seed_source,
            "tus_us": tus_us, "tus_applied_us": first["applied_us"],
            "tus_status": tus_status, "tus_tries": attempt,
            "bus_us": bus_us, "bus_layer": bus_layer or None,
            "bus_min_dn": None if bus_us is None else round(bus_min, 2),
            "frames_at_tus": s.multiple_exposure_count,
            "frames_at_bus": s.bus_exposure_count if bus_us else 0,
            "decision_layers": decision,
            "analysis_layers": R.analysis_layers(color, first["layers"], s),
            "test_region": {"pad_source": self._region.pad_source,
                            "n_pixels": int(self._region.mask.sum()),
                            "n_hot_left_out": self._region.n_hot_inside},
            "attempts": attempts,
            "coverage": cover,
            "limits_dn": {"clip_top": s.clip_top_dn,
                          "clip_bottom": s.clip_bottom_dn,
                          "noise_floor": s.noise_floor_dn,
                          "dark": R.DARK_DN},
            # Every setting this sweep used (config rows; stage 4a).
            "settings": s.to_dict(),
            # The floor test's edge rule (exposure_rule.edge_left_out);
            # LayerStats.n_edge_left_out counts what it left out.
            "edge_rule": {"factor": s.edge_darkening_factor,
                          "sides": list(R.EDGE_RULE_SIDES),
                          "applied": self._region.dist is not None},
        }
        return {"frames": frames, "record": record}

    def _group_lit_frames(self, color: str, labels, exposures_us
                          ) -> Iterator[Progress]:
        """
        Take and store one LED's frames at its stored exposure group
        (RULE_GROUP, the rule before 2026-11-14).  The LED is lit and warmed
        up already; this does not switch it off.

        Returns (as the generator's return value, via `yield from`):
            {labels, exposures_us, applied_us, kept, omitted,
             render_evidence, mid_frame, mid_idx}, as _store_strip_frames.
        """
        w = self._writer
        # ── Lit frames ────────────────────────────────────────
        yield self._progress("capture", "Lit captures:")
        mid_idx = len(exposures_us) // 2   # keep the middle frame's JPEG
        mid_frame: "tuple | None" = None
        applied_us: dict = {}

        kept_layers: dict = {}
        omitted_layers: dict = {}
        render_evidence: dict = {}

        for i, (label, exp_us) in enumerate(zip(labels, exposures_us)):
            # The run's first lit frame also confirms the neutral
            # rendering is in force (see _white_frame): a frame whose
            # colour image is kept as this LED's JPEG must not carry
            # the photo's gains.  Free when they are already neutral.
            want = NEUTRAL_GAINS if self._neutral_unchecked else None
            rgb, bayer, meta = self._settle(exp_us, want_gains=want)
            if want is not None:
                self._neutral_unchecked = False
                n, ok = self._settle_evidence
                render_evidence = {"neutral_frames": n,
                                   "neutral_confirmed": ok}
                if ok is False:
                    log.warning("%s %s: neutral rendering not reported "
                                "after %d frames — its JPEG may be "
                                "tinted", color, label, n)
            applied_us[label] = int(meta.get("ExposureTime", 0))
            layers_out = self._bound_layers(bayer, self._pattern)

            keep = self._keep_channels(color, exp_us, layers_out)
            omit = [ch for ch in layers_out if ch not in keep]
            kept_layers[label] = list(keep)
            omitted_layers[label] = omit

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
                step=label, image_index=i + 1,
                image_total=len(exposures_us))

        return {"labels": list(labels), "exposures_us": list(exposures_us),
                "applied_us": applied_us, "kept": kept_layers,
                "omitted": omitted_layers, "render_evidence": render_evidence,
                "mid_frame": mid_frame, "mid_idx": mid_idx, "record": None}

    def _store_strip_frames(self, color: str, result: dict) -> dict:
        """
        Store the strip rule's frames for one LED, with the LED already off.

        Labels run e0, e1, ... in the order the frames were taken: the Tus
        frames first, then any Bus frames.  Each label's group says its role
        ('tus' or 'bus') and the exposure_us the camera applied.  Which
        Bayer layers reach the file is the strip rule's own choice
        (exposure_rule.keep_layers): every analysis channel, and any other
        layer that is unclipped and above the noise floor on the test
        region.  The layermodel screen (_keep_channels) is not used here.

        Args:
            color:  LED color name.
            result: _strip_lit_frames()'s return value.

        Returns:
            {labels, exposures_us, applied_us, kept, omitted,
             render_evidence, mid_frame, mid_idx, record}: the same keys as
            _group_lit_frames, plus the strip rule's record.
        """
        w = self._writer
        labels, exposures_us = [], []
        applied_us, kept_layers, omitted_layers = {}, {}, {}
        render_evidence: dict = {}
        roles, frame_stats, layer_why, captured = {}, {}, {}, {}
        for i, (role, f) in enumerate(result["frames"]):
            label = f"e{i}"
            keep, layer_why[label] = exposure_rule.keep_layers(
                color, f["stats"], self._rule)
            omit = [ch for ch in f["layers"] if ch not in keep]
            with self.prof.time("io.hdf5"):
                w.add_lit(color, label, f["layers"],
                          exposure_us=f["exposure_us"],
                          captured_dt=f["captured_dt"], omit=omit,
                          attrs={"role": role,
                                 "applied_us": f["applied_us"]})
            labels.append(label)
            exposures_us.append(f["exposure_us"])
            applied_us[label] = f["applied_us"]
            kept_layers[label] = list(keep)
            omitted_layers[label] = omit
            roles[label] = role
            captured[label] = f["captured_dt"]
            frame_stats[label] = {ch: st.to_dict()
                                  for ch, st in f["stats"].items()}
            render_evidence = render_evidence or f["render_evidence"]
        first = result["frames"][0][1]
        # roles: {label: 'tus'|'bus'}; frame_stats: {label: {layer: the
        # test-region statistics of that stored frame}}, every layer, kept
        # or not (the rows stage 4 writes to exposure_log); layers_why:
        # {label: {layer: why it was kept or left out}}; captured_dt:
        # {label: when the frame was taken}.
        record = dict(result["record"], roles=roles, frame_stats=frame_stats,
                      layers_why=layer_why, captured_dt=captured)
        return {"labels": labels, "exposures_us": exposures_us,
                "applied_us": applied_us, "kept": kept_layers,
                "omitted": omitted_layers, "render_evidence": render_evidence,
                "mid_frame": (first["rgb"], first["bayer"], first["meta"]),
                "mid_idx": 0, "record": record}

    def _take_darks(self, when: str) -> Iterator[Progress]:
        """
        Take the run's dark frames and store their statistics only.

        Waits out whatever is left of LED_OFF_SETTLE_S since the last LED (or
        the autofocus light) went out, then takes one frame at each of
        self._dark_us.  Each layer is stored as an empty placeholder carrying
        its statistics (capture_h5's _omitted_attrs: min, mean, max and two
        percentiles): one number per layer is what the analysis subtracts
        (capture_h5.dark_level reads the mean), and a dark frame's pixels are
        fresh noise.

        Args:
            when: "before" (a pre-image, ahead of its first LED) or "after"
                  (a measurement, after its last coloured LED).
        """
        w = self._writer
        left = LED_OFF_SETTLE_S - (time.monotonic() - self._dark_since)
        if left > 0:
            yield from self._wait(left, "LEDs off, settling before dark frames")
        self.prof.set_section("dark")
        log.info(f"=== Dark frames ({when}): {self._dark_us} µs, statistics only ===")
        yield self._progress("capture", f"Dark frames: {len(self._dark_us)} exposure(s)")
        for k, exp_us in enumerate(self._dark_us, 1):
            _, bayer, _ = self._settle(exp_us)
            layers_out = self._bound_layers(bayer, self._pattern)
            with self.prof.time("io.hdf5"):
                w.add_dark(layers_out, exposure_us=exp_us, when=when,
                           captured_dt=self._frame_captured_dt,
                           omit=list(layers_out))
            yield self._progress(
                "capture", f"    stored dark statistics ({exp_us}µs)", step="dark",
                image_index=k, image_total=len(self._dark_us))

    def _capture_white(self) -> Iterator[Progress]:
        """
        Normal path: the white LED's one frame — measurement and photograph.

        Taken last, after the dark frames (see run_prefetch).  The raw layers
        are stored as /leds/white/lit/e0 when white is in the sweep list; the
        same frame, colour-balanced, is the photograph when that is enabled.
        """
        w = self._writer
        photo = self._photo_wanted()
        if self._white_us is None or not (self._white_measure or photo):
            return
        exp_us = self._white_us
        led = WHITE_LED_CH
        if self._white_measure:
            self._led_index += 1
            self._captures_done = 0
            self._captures_per_led = 1
        self._current_color = "white"
        self.prof.set_section("white")
        yield self._progress(
            "capture", f"--- {self._leds.label(led)} --- one frame"
                       f"{' + photo' if photo else ''}", color="white")

        gains = None
        if photo:
            try:
                gains = config.capture_photo_wb_gains()
            except Exception:                              # noqa: BLE001
                log.exception("photo: no colour gains — skipping the photo")
                photo = False
                if not self._white_measure:
                    return
        (rgb, bayer, meta), evidence = yield from self._white_frame(exp_us, gains)

        if self._white_measure:
            layers_out = self._bound_layers(bayer, self._pattern)
            keep = self._keep_channels("white", exp_us, layers_out)
            omit = [ch for ch in layers_out if ch not in keep]
            with self.prof.time("io.hdf5"):
                w.add_lit("white", "e0", layers_out, exposure_us=exp_us,
                          captured_dt=self._frame_captured_dt, omit=omit)
            self._captures_done = 1
            meta_json = {
                "mode": str(self.config.mode).lower(),
                "timestamp": self.suite_id,
                "led_channel": led,
                "led_color": "white",
                "led_label": self._leds.label(led),
                "camera_model": self._cam.model_description,
                "bayer_pattern": self._pattern,
                "config_size": [self._cam_w, self._cam_h],
                "focus_diopters": self._focus_pos,
                "focus_mode": self._focus_mode,
                "autofocus_raw_diopters": self._raw_af_pos,
                "led_on_dt":  self._led_on_dt,
                "led_off_dt": self._led_off_dt,
                # White always takes group 1's one exposure, whatever the run
                # asked for: it is a single frame by design, not a bracket.
                "exposure_group": "1",
                "exposure_group_requested": self._group,
                "exposure_source": self._white_source,
                "exposures_us": {"e0": int(exp_us)},
                "applied_exposures_us": {"e0": int(meta.get("ExposureTime", 0))},
                "layers_kept": {"e0": list(keep)},
                "layers_omitted": {"e0": omit},
                # The frame was rendered colour-balanced when it is also the
                # photograph; the raw layers above are unaffected by that.
                "rendering": "colour-balanced" if gains is not None else "objective",
                "render_evidence": evidence,
            }
            info = _build_capture_info(rgb, bayer, meta,
                                       lens_position=self._cam.lens_position)
            with self.prof.time("io.hdf5"):
                w.add_led("white", meta_json, led_channel=led)
                w.add_capture_info("white", info)
            dropped = f", dropped {'+'.join(omit)}" if omit else ""
            yield self._progress(
                "capture", f"    stored lit e0 ({exp_us // 1000}ms){dropped}",
                step="e0", image_index=1, image_total=1)
            self.results.append(LedResult(
                color="white", led_channel=led, target_channel="",
                exposures_us=[exp_us], metadata=meta_json))

        if photo:
            yield from self._store_photo(rgb, exp_us, meta, gains, evidence)

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
            # Before the camera starts: a bad settings edit stops the run
            # here, during the countdown, and not halfway through the sweep.
            self._read_sweep_settings()

            self._choose_out_path()
            self.results = []
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
                f"{self._cam_w}×{self._cam_h}  "
                f"({str(cfg.mode).lower()}, group {group})")

            # ── Focus (still needed; exposures are stored, focus is not) ──
            self.prof.set_section("focus")
            yield self._progress("focus", "Phase 1: focus")
            with self.prof.time("focus"):
                self._focus_pos, self._focus_mode, self._raw_af_pos = self._set_focus()
            # Autofocus lights an LED of its own; a pre-image's dark frames,
            # taken next, wait out its afterglow from here.
            self._dark_since = time.monotonic()
            yield self._progress(
                "focus", f"Focus: {self._focus_pos:.2f} diopters ({self._focus_mode})")

            # ── Resolve the color order and each color's exposures ────
            # Same list as the calibration path sweeps, resolved the same way,
            # so an unknown name fails here rather than mid-capture.
            self._color_order = [c for _, c in
                                 _resolve_color_order(self._leds, cfg.led_colors)]
            # White is never part of a measurement's sweep: it is taken once,
            # last, after the dark frames — its phosphor can keep glowing after
            # the LED is put out, so nothing that needs the dark may follow it —
            # and that one frame is also the colour photograph.
            self._white_measure = (not self._is_pre()
                                   and "white" in self._color_order)
            if self._white_measure:
                self._color_order.remove("white")
            self._white_us = None
            if self._white_measure or self._photo_wanted():
                self._white_us, self._white_source = self._white_exposure()
            self._color_exposures = {}
            self._color_labels    = {}
            self._color_fallback  = {}
            self._color_group     = {}

            for color in self._color_order:
                exposures_us, used_group = self._stored_exposures(color, group)
                used_fallback = not exposures_us
                if used_fallback:
                    exposures_us = [cfg.initial_us]
                    log.warning("LED %s has no exposures for group %s or any "
                                "smaller group — falling back to %d µs",
                                color, group, cfg.initial_us)
                self._color_group[color] = used_group
                self._color_exposures[color] = exposures_us
                self._color_labels[color]    = [f"e{i}" for i in range(len(exposures_us))]
                self._color_fallback[color]  = used_fallback

            # Open the archive and write /meta now, before any photograph.
            # The ROI geometry is what makes the stored arrays locatable, so a
            # run that dies partway still leaves an interpretable file.  It is
            # opened before the seeds because a measurement reads its seeds
            # from its pre-image, in this same archive.
            w = self._open_writer()
            self._write_metadata(w)

            if self._strip_rule():
                self._color_seed = {c: self._seed_exposure(c)
                                    for c in self._color_order}
                # Until the sweep has run, the seeds are the best guess at
                # its longest exposure.  A pre-image takes its darks before
                # its sweep, so its pair uses this; a measurement's pair is
                # worked out again from what it took (run_capture).  The dark
                # level does not depend on exposure_us (64.02-64.04 DN from 1
                # to 51 ms), so the guess costs nothing in accuracy.
                for c in self._color_order:
                    self._color_exposures[c] = [self._color_seed[c][0]]

            lit_us = [us for c in self._color_order for us in self._color_exposures[c]]
            if self._white_measure:
                lit_us.append(self._white_us)
            # A set: when the longest lit exposure is DARK_SHORT_US itself,
            # one frame serves as both.
            self._dark_us = sorted({DARK_SHORT_US, max(lit_us, default=DARK_SHORT_US)})

            log.info(f"=== Prefetch done: dark frames at {self._dark_us} µs, "
                     f"white {self._white_us} µs ({self._white_source}) ===")
            yield self._progress(
                "capture", "Ready — press CAPTURE to begin the LED sweep.",
                step="prefetch-done")

        except (GeneratorExit, Exception):
            self._all_off()
            self._close_writer()
            raise

    def run_capture(self) -> Iterator[Progress]:
        """
        Normal-path stage 2: the lit-LED sweep, the dark frames, then white.

        Assumes run_prefetch() already ran on this same instance — the
        archive is open, the camera is set up and focused, and the color
        order / per-color exposures / distinct dark exposure set are
        resolved. Always cleans up (LED off, archive closed) on completion,
        abandonment, or failure — unlike run_prefetch(), there is no pause
        left to preserve after this.

        The two dark frames are taken here too: first, on a pre-image; after
        the coloured LEDs and before white, on a measurement.  White is one
        frame, last, which is also the colour photograph (_capture_white).
        """
        leds = self._leds
        w    = self._writer
        try:
            rule = ("exposures from the strip" if self._strip_rule()
                    else f"exposure group {self._group}")
            yield self._progress(
                "capture",
                f"{'Pre-image' if self._is_pre() else 'Normal capture'} — "
                f"{rule}")
            # Every lit exposure_us this sweep stores, for a measurement's
            # dark pair (taken after its sweep).
            self._lit_us_taken = []
            # The test region is built from this sweep's own pad fit, at its
            # first strip-rule frame (_strip_frame), or by the pad-finding
            # frame's log row (_log_padfind_frame).
            self._region = None
            self._log_rows = []
            self._samp_no = (sample_repo.samp_no_for(self.config.sample_rowid)
                             or None)

            # A pre-image takes its dark frames first, before any LED of its
            # own has lit; a measurement takes them after its last coloured
            # LED and before white (see _capture_white).
            if self._is_pre():
                yield from self._take_darks("before")

            # Find the pad before any photo this sweep keeps.
            yield from self._find_pad()

            for color in self._color_order:
                self._led_index    += 1
                self._captures_done = 0
                self._current_color = color
                self.prof.set_section(color)

                exposures_us  = self._color_exposures[color]
                labels        = self._color_labels[color]
                used_fallback = self._color_fallback[color]
                used_group    = self._color_group[color]
                self._captures_per_led = max(1, len(exposures_us))
                led = leds.channel_of(color)

                if self._strip_rule():
                    seed_us, seed_source = self._color_seed[color]
                    self._captures_per_led = self._rule.multiple_exposure_count
                    log.info(f"=== LED {led}: {color} (strip rule, seed "
                             f"{seed_us} µs from {seed_source}) ===")
                else:
                    log.info(f"=== LED {led}: {color} (group {used_group}"
                             f"{f' for {self._group}' if used_group != self._group else ''}"
                             f"{', fallback' if used_fallback else ''}) ===")

                if self._strip_rule():
                    note = ""
                elif used_fallback:
                    note = "  [no calibration — fallback]"
                elif used_group != self._group:
                    note = f"  [group {used_group}: {self._group} not calibrated]"
                else:
                    note = ""
                yield self._progress("capture", f"--- {leds.label(led)} ---{note}")

                self._led_on(led)
                yield from self._wait(LED_ON_WARMUP_S, f"{color} LED on, warming up")

                if self._strip_rule():
                    result = yield from self._strip_lit_frames(color)
                    # Everything is stored after the LED is off, so file
                    # writing is not LED on-time.
                    self._led_off(led)
                    lit = self._store_strip_frames(color, result)
                    self._queue_led_log_rows(color, lit)
                else:
                    lit = yield from self._group_lit_frames(
                        color, labels, exposures_us)
                    # The dark frames come once, at the start or end of the
                    # run — no per-LED settle wait needed here.
                    self._led_off(led)
                applied_us     = lit["applied_us"]
                kept_layers    = lit["kept"]
                omitted_layers = lit["omitted"]
                render_evidence = lit["render_evidence"]
                mid_frame      = lit["mid_frame"]
                labels         = lit["labels"]
                exposures_us   = lit["exposures_us"]
                mid_idx        = lit["mid_idx"]
                self._lit_us_taken.extend(exposures_us)

                # ── Per-LED metadata, statistics and JPEG ──────────────
                meta_json: dict = {
                    # The path's own name, not the path it shares code with:
                    # a reader must be able to tell a pre-image's frames from
                    # a measurement's without inferring it from the group.
                    "mode": str(self.config.mode).lower(),
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
                    # The group this LED was shot with, and the one the run
                    # asked for: they differ when calibration could not fit
                    # the asked-for group for this LED (_stored_exposures).
                    # With the strip rule no group sets the exposures:
                    # exposure_group is then None and exposure_source says
                    # so; the rule's own record is under "strip_rule".
                    "exposure_group": (None if lit["record"] else used_group),
                    "exposure_group_requested": self._group,
                    "exposure_source": ("strip rule" if lit["record"] else
                                        "fallback" if used_fallback
                                        else "exposure_table"),
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
                if render_evidence:
                    # Only on the run's first LED: the frames its first lit
                    # exposure took to report the neutral colour gains.
                    meta_json["render_evidence"] = render_evidence
                led_attrs = {}
                if lit["record"]:
                    meta_json["strip_rule"] = lit["record"]
                    # As attributes too, where a measurement reads its
                    # pre-image's Tus back (_seed_exposure).  HDF5
                    # attributes can't hold None, so bus_us only if taken.
                    led_attrs["tus_us"] = lit["record"]["tus_us"]
                    led_attrs["tus_status"] = lit["record"]["tus_status"]
                    if lit["record"]["bus_us"] is not None:
                        led_attrs["bus_us"] = lit["record"]["bus_us"]
                with self.prof.time("io.hdf5"):
                    w.add_led(color, meta_json, led_channel=led,
                              attrs=led_attrs)
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

            # Every coloured LED is off.  For a measurement this write
            # falls inside the dark frames' settling wait, which is timed
            # from the last LED going out.
            self._flush_exposure_log()

            if not self._is_pre():
                if self._strip_rule() and self._lit_us_taken:
                    # The pair is the shortest tested dark and the longest
                    # exposure this sweep actually took (Bus can be longer
                    # than any seed).
                    self._dark_us = sorted({DARK_SHORT_US,
                                            max(self._lit_us_taken)})
                yield from self._take_darks("after")
                yield from self._capture_white()

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
            # Rows of a run cut short (nothing, if already written).
            self._flush_exposure_log()
            self._close_writer()
