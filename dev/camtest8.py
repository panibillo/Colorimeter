#!/usr/bin/env python3
"""
camtest8.py — Multi-exposure ROI capture for colorimetric analysis.

Performs automatic exposure bracketing for each LED color and saves only
the ROI_BOUND crop (all 4 Bayer channels) for each of 5 exposures, plus
a focused full-image JPEG at the median exposure (e3).

Three-phase workflow:
  Phase 0: White LED on — find a calibrated unsaturated starting exposure.
           Target Bayer channel is determined per-LED in Phase 2 (not here).
  Phase 1: Camera focus (manual diopter or autofocus).
  Phase 2: Per-LED: determine target channel, compute 5 exposures, capture and save.

Exposure bracketing targets (50% of 10-bit saturation = 512):
  e1: 89th-percentile pixel in ROI_SAMPLE → 512
  e2: 66th-percentile pixel in ROI_SAMPLE → 512
  e3: 50th-percentile pixel in ROI_SAMPLE → 512  (median)
  e4: 33rd-percentile pixel in ROI_SAMPLE → 512
  e5: 11th-percentile pixel in ROI_SAMPLE → 512

Output filenames (saved to OUT_DIR/<timestamp>/):
  camtest8_BOUND_{color}_{e_label}_{ms}ms_{ch}.npy   — lit ROI_BOUND per channel
  camtest8_BOUND_black_{e_label}_{ms}ms_{ch}.npy     — dark ROI_BOUND per channel
  camtest8_{color}_e3.jpg                            — focused full-image JPEG at e3
  camtest8_{color}_metadata.json                     — focus, channel, exposure analysis

Usage:
    python dev/camtest8.py [exposure_ms [led_start [led_end [focus_diopter]]]]

    exposure_ms   : initial exposure in ms  (default 10)
    led_start     : first LED channel 1-10  (default 1)
    led_end       : last  LED channel 1-10  (default led_start)
    focus_diopter : manual lens position in diopters; omit for autofocus
                    Higher = closer focus. IMX708 range ~0.0 (inf) to ~10+ (macro).

Examples:
    python dev/camtest8.py 10 1 1          # LED 1, autofocus, 10 ms initial
    python dev/camtest8.py 10 1 10 8.0     # all 10 LEDs, 8 diopters manual focus
    python dev/camtest8.py 2  1  1 10.0    # violet, close focus (minimum distance)
"""

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np

from bayer_cam import BayerCam
from lighting_cal8 import LEDBank
from utils import install_exit_signals

try:
    import libcamera as _libcamera  # type: ignore[import-untyped]
except ImportError:
    _libcamera = None

# ---------------------------------------------------------------------------
# Logging setup  (DEBUG goes to console; reduce to INFO for quieter runs)
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)-7s  %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("camtest8")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_EXPOSURE_MS  = 10
LED_WARMUP_S         = 2.0          # seconds after LED on before capture
MAX_SETTLE_FRAMES    = 15           # max captures while waiting for exposure to settle
SETTLE_TOLERANCE     = 0.05         # 5 % tolerance for exposure-settle check

OUT_DIR  = Path("/home/bill/pi/Colorimeter/dev/camtest8")
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
PERCENTILES     = [89, 66, 50, 33, 11]    # e1…e5 percentile targets
EXP_LABELS      = ["e0", "e1", "e2", "e3", "e4", "e5", "e6"]  # ordered capture sequence

WHITE_LED_CH   = 10    # LEDBank channel for white LED
MAX_AF_DIOPTER = 7.5   # cap autofocus: if result exceeds this, set manually to this

# e3 convergence: iterate until |p50 - HALF_SAT| / HALF_SAT < E3_TOLERANCE
E3_TOLERANCE = 0.10    # 10 % tolerance for p50 convergence
E3_MAX_ITER  = 5       # maximum refinement iterations
E3_RATIO     = 1.5     # e0 = e3 / E3_RATIO;  e6 = e3 * E3_RATIO

# ---------------------------------------------------------------------------
# ROI helpers
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
    Compute all 5 capture exposures from the pixel distribution at e3.

    e1, e3, e5 are percentile-scaled; e0 and e6 are fixed-ratio brackets.
    Returns (exposures_us, pcts) where pcts = [p89, p50, p11].
    exposures_us order matches EXP_LABELS = ["e0","e1","e3","e5","e6"].
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


def _save_roi_bound(
    out_dir: Path, stem: str, bayer: np.ndarray, pattern: str
) -> None:
    """
    Crop ROI_BOUND from bayer array, split into 4 channels, save as NPY files.

    Writes: {stem}_B.npy, {stem}_Gb.npy, {stem}_Gr.npy, {stem}_R.npy
    Each channel array has shape (ROI_BOUND_h//2, ROI_BOUND_w//2) = (315, 80).
    """
    x, y, w, h = ROI_BOUND
    crop   = bayer[y:y + h, x:x + w]
    layers = BayerCam.extract_bayer_layers(crop, pattern)
    for ch_name, arr in layers.items():
        np.save(str(out_dir / f"{stem}_{ch_name}.npy"), arr)
    log.debug(f"    saved {stem}_{{B,Gb,Gr,R}}.npy")
    print(f"    saved {stem}_{{B,Gb,Gr,R}}.npy")


# ---------------------------------------------------------------------------
# Exposure settling
# ---------------------------------------------------------------------------

def _set_and_settle(
    cam: BayerCam, exposure_us: int
) -> "tuple[np.ndarray, np.ndarray, dict]":
    """
    Set exposure and capture frames until metadata confirms it has been applied.

    After calling set_exposure(), the Picamera2 pipeline takes at least one
    frame cycle to adopt the new ExposureTime.  This function keeps capturing
    until the metadata ExposureTime is within SETTLE_TOLERANCE of the request,
    or MAX_SETTLE_FRAMES is exhausted.
    """
    cam.set_exposure(exposure_us)
    last = None
    for frame_num in range(MAX_SETTLE_FRAMES):
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


# ---------------------------------------------------------------------------
# Focus
# ---------------------------------------------------------------------------

def _set_manual_lens(cam: BayerCam, diopters: float) -> None:
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


def _set_focus(
    cam: BayerCam, leds: LEDBank, focus_diopter: "float | None"
) -> "tuple[float, str, float | None]":
    """
    Set camera focus and return (final_pos, mode, raw_af_pos).

    If focus_diopter is given → set that position directly (mode='manual').
    Otherwise → run autofocus; if result > MAX_AF_DIOPTER, cap it at
    MAX_AF_DIOPTER (mode='autofocus_capped'), else accept as-is (mode='autofocus').

    raw_af_pos is the unmodified autofocus result, or None for manual.
    """
    if focus_diopter is not None:
        if not cam.has_autofocus:
            log.warning("Manual focus requested but camera has no VCM — ignored")
            print("  Focus : no VCM on this camera, skipped")
            return 0.0, "no_vcm", None
        _set_manual_lens(cam, focus_diopter)
        log.info(f"Focus: manual  {focus_diopter:.2f} diopters")
        print(f"  Focus : {focus_diopter:.2f} diopters (manual)")
        return float(focus_diopter), "manual", None

    # Autofocus path
    raw_pos = cam.focus(force=False, leds=leds)
    if not cam.has_autofocus:
        return 0.0, "no_vcm", None

    if raw_pos > MAX_AF_DIOPTER:
        log.info(f"Focus: autofocus returned {raw_pos:.4f} diopters "
                 f"> cap {MAX_AF_DIOPTER} — capping")
        print(f"  Focus : autofocus={raw_pos:.4f} diopters "
              f"> cap {MAX_AF_DIOPTER} — setting to {MAX_AF_DIOPTER}")
        _set_manual_lens(cam, MAX_AF_DIOPTER)
        return MAX_AF_DIOPTER, "autofocus_capped", raw_pos

    log.info(f"Focus: autofocus  {raw_pos:.4f} diopters (within cap)")
    print(f"  Focus : {raw_pos:.4f} diopters (autofocus)")
    return raw_pos, "autofocus", raw_pos


# ---------------------------------------------------------------------------
# Per-LED metadata
# ---------------------------------------------------------------------------

def _save_led_metadata(out_dir: Path, color: str, data: dict) -> None:
    path = out_dir / f"{OUT_STEM}_{color}_metadata.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"    saved metadata: {path.name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    install_exit_signals()

    exposure_ms   = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXPOSURE_MS
    led_start     = int(sys.argv[2])   if len(sys.argv) > 2 else 1
    led_end       = int(sys.argv[3])   if len(sys.argv) > 3 else led_start
    focus_diopter = float(sys.argv[4]) if len(sys.argv) > 4 else None

    initial_us = int(exposure_ms * 1000)
    ts_dir = OUT_DIR / datetime.now().strftime("%Y%m%d_%H%M")

    print("=" * 60)
    print("camtest8 — ROI multi-exposure capture")
    print("=" * 60)
    print(f"  Initial exp   : {exposure_ms:.0f} ms  ({initial_us:,} µs)")
    print(f"  Output        : {ts_dir}")
    print(f"  LEDs          : {led_start} to {led_end}")
    if focus_diopter is not None:
        print(f"  Focus         : {focus_diopter:.2f} diopters (manual)")
    else:
        print(f"  Focus         : autofocus")
    print()

    ts_dir.mkdir(parents=True, exist_ok=True)

    white_croi  = _channel_roi(ROI_WHITE)
    sample_croi = _channel_roi(ROI_SAMPLE)

    with LEDBank() as leds:
        cam = BayerCam()
        try:
            # ── Camera init ───────────────────────────────────────────────
            cam.set_exposure(initial_us)
            cam.setup_camera()
            pattern = cam.bayer_pattern
            assert cam.config_size is not None, "setup_camera() must set config_size"
            cam_w, cam_h = cam.config_size
            print(f"  Camera  : {cam.model_description}")
            print(f"  Pattern : {pattern}")
            print(f"  Size    : {cam_w}×{cam_h}")
            print(f"  Max exp : {cam.MAX_EXPOSURE_US / 1e6:.1f} s")
            print()

            # ================================================================
            # Phase 0 — White LED: find a calibrated unsaturated exposure
            # Target channel is NOT selected here; it is per-LED in Phase 2.
            # ================================================================
            print("Phase 0: white LED exposure calibration")
            leds.set_channel(WHITE_LED_CH, True)
            print(f"  White LED ON — warming up {LED_WARMUP_S:.0f} s ...")
            time.sleep(LED_WARMUP_S)

            current_us          = initial_us
            phase0_retries      = 0

            for attempt in range(MAX_DESAT_TRIES):
                log.debug(f"Phase 0 attempt {attempt + 1}: exposure={current_us}µs")
                _, bayer, _ = _set_and_settle(cam, current_us)
                layers = BayerCam.extract_bayer_layers(bayer, pattern)
                means  = _channel_means(layers, white_croi)
                log.debug(f"  ROI_WHITE means: "
                          + "  ".join(f"{k}={v:.1f}" for k, v in sorted(means.items())))
                if not _check_saturated_any(layers, white_croi):
                    log.debug(f"  Not saturated — accepting {current_us}µs as calibrated exposure")
                    break
                new_us = max(current_us // 2, 1000)
                log.debug(f"  Saturated — halving {current_us}µs → {new_us}µs")
                print(f"  ROI_WHITE saturated — halving {current_us:,} → {new_us:,} µs")
                current_us = new_us
                phase0_retries += 1

            calibrated_us = current_us
            leds.set_channel(WHITE_LED_CH, False)
            print(f"  Calibrated initial exposure: {calibrated_us:,} µs  ({calibrated_us // 1000} ms)")
            print()

            # ================================================================
            # Phase 1 — Focus
            # ================================================================
            print("Phase 1: focus")
            focus_pos, focus_mode, raw_af_pos = _set_focus(cam, leds, focus_diopter)
            print()

            # ================================================================
            # Phase 2 — Per-LED multi-exposure capture and save
            # ================================================================
            print("Phase 2: per-LED multi-exposure capture")
            summary = []

            for led in range(led_start, led_end + 1):
                color = leds.color_name(led)
                print(f"\n--- {leds.label(led)} ---")
                log.info(f"=== LED {led}: {color} ===")

                leds.set_channel(led, True)
                print(f"  LED ON — warming up {LED_WARMUP_S:.0f} s ...")
                time.sleep(LED_WARMUP_S)

                # Per-LED desat loop + target channel selection
                current_us     = calibrated_us
                target_ch: str = ""
                ref_means: dict = {}
                per_led_retries = 0

                for attempt in range(MAX_DESAT_TRIES):
                    log.debug(f"  {color} attempt {attempt + 1}: exposure={current_us}µs")
                    _, bayer, _ = _set_and_settle(cam, current_us)
                    layers    = BayerCam.extract_bayer_layers(bayer, pattern)
                    ref_means = _channel_means(layers, white_croi)
                    target_ch = _select_target_channel(layers, white_croi)
                    if not _check_saturated(layers, target_ch, white_croi):
                        log.debug(f"  {color}: not saturated — target_ch={target_ch}  "
                                  f"exposure={current_us}µs accepted")
                        break
                    new_us = max(current_us // 2, 1000)
                    log.debug(f"  {color}: saturated — halving {current_us}µs → {new_us}µs")
                    print(f"  Saturated — halving {current_us:,} → {new_us:,} µs")
                    current_us = new_us
                    per_led_retries += 1

                if not target_ch:
                    raise RuntimeError(
                        f"Failed to determine target channel for LED '{color}'"
                    )

                means_str = "  ".join(f"{k}={v:.1f}" for k, v in sorted(ref_means.items()))
                print(f"  Target channel : {target_ch}  (ROI_WHITE: {means_str})")
                log.info(f"  {color}: target_ch={target_ch}  ref_exposure={current_us}µs")

                # ── Stage 1: estimate e3 from reference capture ───────────
                pix_ref = _roi_pixels(layers[target_ch], sample_croi)
                e3_us   = _estimate_e3(pix_ref, current_us)
                log.info(f"  {color}: initial e3 estimate = {e3_us}µs ({e3_us // 1000}ms)")

                # ── Stage 2: refine e3 until p50 ≈ HALF_SAT ──────────────
                e3_history: list = []
                layers_e3 = layers     # fallback if loop never runs

                for iteration in range(E3_MAX_ITER):
                    log.info(f"  {color} e3 refine [{iteration + 1}/{E3_MAX_ITER}]: "
                             f"capturing at {e3_us}µs ({e3_us // 1000}ms)")
                    _, bayer_e3, _ = _set_and_settle(cam, e3_us)
                    layers_e3  = BayerCam.extract_bayer_layers(bayer_e3, pattern)
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
                        print(f"  e3 converged : {e3_us}µs ({e3_us // 1000}ms)  "
                              f"p50={actual_p50:.1f}  ({iteration + 1} iter)")
                        break
                    new_e3 = max(1000, int(e3_us * HALF_SAT / max(actual_p50, 1.0)))
                    log.debug(f"    adjusting e3: {e3_us}µs → {new_e3}µs")
                    e3_us = new_e3
                else:
                    log.warning(f"  {color}: e3 did not converge in {E3_MAX_ITER} iterations")
                    print(f"  e3 not converged after {E3_MAX_ITER} iterations — "
                          f"using {e3_us}µs")

                # ── Stage 3: compute e0,e1,e3,e5,e6 from e3 capture spread ──
                pix_e3       = _roi_pixels(layers_e3[target_ch], sample_croi)
                exposures_us, pcts = _compute_exposures_from_e3(pix_e3, e3_us)
                e0_us, e1_us, e2_us, _, e4_us, e5_us, e6_us = exposures_us
                exp_summary = ", ".join(
                    f"{lbl}={exp // 1000}ms"
                    for lbl, exp in zip(EXP_LABELS, exposures_us)
                )
                print(f"  Exposures      : {exp_summary}")

                # Build metadata
                meta_json: dict = {
                    "camtest8_version": "4",
                    "timestamp": ts_dir.name,
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

                # Lit frames: e0, e1, e3, e5, e6
                print("  Lit captures:")
                for exp_label, exp_us in zip(EXP_LABELS, exposures_us):
                    log.info(f"  {color} {exp_label}: setting exposure {exp_us}µs "
                             f"({exp_us // 1000}ms)")
                    rgb, bayer, meta = _set_and_settle(cam, exp_us)
                    applied = int(meta.get("ExposureTime", 0))
                    meta_json["applied_exposures_us"][exp_label] = applied
                    log.info(f"  {color} {exp_label}: applied={applied}µs  "
                             f"delta={applied - exp_us:+d}µs")

                    stem = f"{OUT_STEM}_BOUND_{color}_{exp_label}_{exp_us // 1000}ms"
                    _save_roi_bound(ts_dir, stem, bayer, pattern)

                    if exp_label == "e3":    # e3 — focused full-image JPEG
                        jpeg_stem = f"{OUT_STEM}_{color}_e3"
                        BayerCam.save_frame(
                            ts_dir, jpeg_stem, rgb, bayer, meta,
                            jpeg_only=True, lens_position=cam.lens_position,
                        )
                        print(f"    saved JPEG: {jpeg_stem}.jpg")

                # LED off, then dark frames at the same exposures
                leds.set_channel(led, False)
                print(f"  LED OFF — waiting {LED_WARMUP_S:.0f} s ...")
                time.sleep(LED_WARMUP_S)

                print("  Dark captures:")
                for exp_label, exp_us in zip(EXP_LABELS, exposures_us):
                    log.info(f"  {color} dark {exp_label}: exposure {exp_us}µs")
                    _, bayer, _ = _set_and_settle(cam, exp_us)
                    stem = f"{OUT_STEM}_BOUND_black_{exp_label}_{exp_us // 1000}ms"
                    _save_roi_bound(ts_dir, stem, bayer, pattern)

                _save_led_metadata(ts_dir, color, meta_json)
                summary.append((color, target_ch, exposures_us))

            # ── Summary table ─────────────────────────────────────────────
            print("\n" + "=" * 60)
            print("Summary")
            print("=" * 60)
            hdr = (f"{'LED':<12} {'Ch':<4}"
                   + "".join(f"  {lbl:>8}" for lbl in EXP_LABELS))
            print(hdr)
            print("-" * 60)
            for color, ch, exps in summary:
                vals = "".join(f"  {e // 1000:>6}ms" for e in exps)
                print(f"{color:<12} {ch:<4}{vals}")
            print(f"\nOutput: {ts_dir}")

        finally:
            cam.stop()
            print("\nCamera stopped.")

    print("Done.")


if __name__ == "__main__":
    main()
