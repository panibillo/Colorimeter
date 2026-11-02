"""
exposure_measure.py  —  Measure the test pad for the exposure fit.
-------------------------------------------------------------------
exposure_fit.py decides where the brackets go; it needs a `measure` function
that answers "at this exposure, how bright are the patches?".  This module
builds that function.

For one exposure it reads the pad's white, yellow and brown patches and the
gray chips out of a frame, and reports each one's low and high percentile
signal with the sensor's black level removed.  Only the Bayer channels the
final analysis uses for the LED are measured (required_cells.py), so a channel
that is allowed to run too dark or too bright can never fail the fit.

The camera is not touched here.  The caller passes `grab`, a function that
sets an exposure, takes a frame, and returns its four Bayer layers:

    grab(exposure_us) -> {'R': ndarray, 'Gr': ndarray, 'Gb': ndarray, 'B': ndarray}

with the layers cut from the whole frame, so a patch's position is its
stream-space rectangle halved (roi.to_layer_rect).

Public surface
--------------
    calibration_patches(rois, pad) -> dict[name, Rect]   layer-space patches
    gray_chips(rois) -> dict[name, Rect]                 just the gray chips
    patch_levels(layers, patches, channels, low_pctl, high_pctl, black_dn)
    white_balance(layers, chips, high_dn, high_pctl) -> WhiteBalance
                                                         colour gains for the photo
    gain_exposure(layers, chips, exposure_us, high_dn, high_pctl) -> float
                                                         where the chips read best
    make_measure(grab, patches, channels, low_pctl, high_pctl, black_dn)
    measure_for_led(led, grab, rois, pad) -> Measure     everything from config
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, Tuple

import numpy as np

import roi
from exposure_fit import Levels, Measure
from rect import Rect

# Mean dark level of every Bayer layer at every exposure, measured on 2564
# dark frames (see the comment above capture_h5.dark_level).  Subtracting it
# leaves the signal the window limits are written in.
BLACK_DN = 64.0

# The least signal (DN above black) a gray chip channel must show for a colour
# gain to be measured from it.  The chips read 140-350 DN under the white LED
# at its calibrated exposure (C1038), so this only refuses a frame that is
# badly underexposed in some channel.
GAIN_MIN_SIGNAL_DN = 50.0

# Where gain_exposure() aims the brightest gray-chip channel, as a fraction of
# the window top: well inside it, so the dimmest channel (blue, about 2.4x
# darker than green under the white LED) stays well clear of
# GAIN_MIN_SIGNAL_DN.
GAIN_TARGET_FRACTION = 0.7

Grab = Callable[[int], Dict[str, np.ndarray]]


def calibration_patches(rois: Dict[str, Rect], pad: Dict[str, Rect]) -> Dict[str, Rect]:
    """
    Gather every patch calibration checks, in layer coordinates.

    That is the test pad's white, yellow and brown patches (keyed by their
    role, e.g. 'pad_white') and every gray chip (keyed by its ROI name).

    Args:
        rois: The active ROIs (roi.load_active()), where the chips live.
        pad:  The test pad (roi.load_test_pad()).

    Raises:
        ValueError: if a pad patch or every gray chip is missing, naming what
                    is missing so the operator knows what to set up.
    """
    missing = roi.missing_test_pad_roles(pad)
    if missing:
        raise ValueError("test pad is missing: " + ", ".join(missing))
    chips = gray_chips(rois)
    patches = {role: roi.to_layer_rect(pad[role]) for role in roi.TEST_PAD_ROLES}
    patches.update(chips)
    return patches


def patch_levels(layers: Dict[str, np.ndarray], patches: Dict[str, Rect],
                 channels: Iterable[str], low_pctl: float, high_pctl: float,
                 black_dn: float = BLACK_DN) -> Levels:
    """
    Measure every patch in every wanted channel of one frame.

    Returns:
        {'<patch>.<channel>': (low, high)}: the low and high percentile of the
        patch's pixels, minus `black_dn`.

    Raises:
        ValueError: if a patch covers no pixels in the layer.
    """
    out: Levels = {}
    for name, rect in patches.items():
        for ch in channels:
            pixels = layers[ch][rect.xyslice()]
            if pixels.size == 0:
                raise ValueError(f"patch {name!r} covers no pixels in layer {ch}")
            low, high = np.percentile(pixels, (low_pctl, high_pctl))
            out[f"{name}.{ch}"] = (float(low) - black_dn, float(high) - black_dn)
    return out


def sample_rect(rois: Dict[str, Rect]) -> Rect:
    """
    The whole pad, in layer coordinates: the sample region the capture's
    analysis reads, not the test pad's three patches.

    Raises:
        ValueError: if no sample region is defined.
    """
    sample = roi.by_role(rois, roi.ROLE_SAMPLE)
    if sample is None:
        raise ValueError("no sample region is defined")
    return roi.to_layer_rect(sample)


def pad_samples(layers: Dict[str, np.ndarray], rect: Rect, layer: str,
                black_dn: float = BLACK_DN) -> np.ndarray:
    """
    Every pixel of the pad (`rect`, from sample_rect) in one Bayer layer,
    black level removed and sorted ascending.  The curve shows how the pad's
    brightness spreads, darkest pixel to lightest.

    Raises:
        ValueError: if `rect` covers no pixels in the layer.
    """
    pixels = layers[layer][rect.xyslice()]
    if pixels.size == 0:
        raise ValueError(f"the sample region covers no pixels in layer {layer}")
    return np.sort(pixels.astype(float).ravel()) - black_dn


def gray_chips(rois: Dict[str, Rect]) -> Dict[str, Rect]:
    """
    Every gray chip (white_balance region), keyed by name, in layer coordinates.

    Raises:
        ValueError: if no gray chip is defined.
    """
    chips = roi.all_by_role(rois, roi.ROLE_WHITE_BALANCE)
    if not chips:
        raise ValueError("no gray chip (white_balance) region is defined")
    return {chip.name: roi.to_layer_rect(chip) for chip in chips}


@dataclass(frozen=True)
class WhiteBalance:
    """
    Colour gains (red, blue) that make the gray chips gray under the white LED.

    `red` is green over red and `blue` is green over blue, black level removed
    and green being the mean of Gr and Gb: the gains the camera's image
    processor multiplies red and blue by (BayerCam.set_color_rendering).
    `chips` holds each chip's own pair; `red` and `blue` are their mean.
    `spread` is how far the chips disagree, as the larger of the two gains'
    max/min - 1.  `problem` is empty when the gains can be used, and otherwise
    says why not (a chip too dark or too bright to measure), in which case
    `red` and `blue` are 0.
    """
    red: float
    blue: float
    chips: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    spread: float = 0.0
    problem: str = ""

    @property
    def ok(self) -> bool:
        return not self.problem


def white_balance(layers: Dict[str, np.ndarray], chips: Dict[str, Rect],
                  high_dn: float, high_pctl: float,
                  black_dn: float = BLACK_DN) -> WhiteBalance:
    """
    Measure the colour gains from the gray chips in one white-LED frame.

    Each chip's channel median, black level removed, is the signal.  A gain is
    a ratio of two medians, so a chip channel must be far enough above black
    for the ratio to mean something (GAIN_MIN_SIGNAL_DN) and must not reach
    the window's top at its `high_pctl` percentile, where the sensor stops
    being linear.

    Args:
        layers:    The frame's Bayer layers, {'R', 'Gr', 'Gb', 'B'}.
        chips:     {name: layer-space Rect}, as gray_chips() returns.
        high_dn:   The white LED's window top, black level removed.
        high_pctl: The percentile held to `high_dn`.
        black_dn:  The black level to remove.

    Returns:
        WhiteBalance.  Check `.ok`.
    """
    per_chip: Dict[str, Tuple[float, float]] = {}
    for name, rect in chips.items():
        median = {}
        for ch in ("R", "Gr", "Gb", "B"):
            pixels = layers[ch][rect.xyslice()]
            if pixels.size == 0:
                return WhiteBalance(0.0, 0.0, problem=f"{name} covers no pixels")
            med, top = np.percentile(pixels, (50.0, high_pctl)) - black_dn
            if med < GAIN_MIN_SIGNAL_DN:
                return WhiteBalance(0.0, 0.0, problem=f"{name}.{ch} too dark")
            if top > high_dn:
                return WhiteBalance(0.0, 0.0, problem=f"{name}.{ch} too bright")
            median[ch] = float(med)
        green = (median["Gr"] + median["Gb"]) / 2.0
        per_chip[name] = (green / median["R"], green / median["B"])
    reds = [r for r, _ in per_chip.values()]
    blues = [b for _, b in per_chip.values()]
    spread = max(max(reds) / min(reds), max(blues) / min(blues)) - 1.0
    return WhiteBalance(float(np.mean(reds)), float(np.mean(blues)),
                        per_chip, spread)


def gain_exposure(layers: Dict[str, np.ndarray], chips: Dict[str, Rect],
                  exposure_us: float, high_dn: float, high_pctl: float,
                  black_dn: float = BLACK_DN) -> float:
    """
    The exposure that would put the gray chips where white_balance() can read
    them, scaled from a frame taken at `exposure_us`.

    The sensor is linear, so the brightest chip channel's `high_pctl`
    percentile is scaled to GAIN_TARGET_FRACTION of `high_dn`.  A clipped
    channel reads low, so one rescale may land short of the target; it still
    lands inside the window, which is all the gains need.

    Gains are a ratio of channels and do not depend on exposure, which is why
    they may be measured at an exposure of their own: the photo's exposure
    suits the pad, and in a spread fit it can clip the chips.
    """
    brightest = max(float(np.percentile(layers[ch][rect.xyslice()], high_pctl))
                    - black_dn
                    for rect in chips.values() for ch in ("R", "Gr", "Gb", "B"))
    if brightest <= 0:
        return exposure_us * 4.0
    return exposure_us * GAIN_TARGET_FRACTION * high_dn / brightest


def make_measure(grab: Grab, patches: Dict[str, Rect], channels: Iterable[str],
                 low_pctl: float, high_pctl: float,
                 black_dn: float = BLACK_DN) -> Measure:
    """Return measure(exposure_us) for exposure_fit, reading frames from `grab`."""
    channels = tuple(channels)

    def measure(exposure_us: int) -> Levels:
        return patch_levels(grab(exposure_us), patches, channels,
                            low_pctl, high_pctl, black_dn)

    return measure


def measure_for_led(led: str, grab: Grab, rois: Dict[str, Rect],
                    pad: Dict[str, Rect],
                    percentiles: "tuple[float, float] | None" = None,
                    channels: "Iterable[str] | None" = None) -> Measure:
    """
    Build the measure function for one LED, taking every setting from config.

    The channels come from required_cells (the final analysis's cells, or the
    nearest channel for a LED it does not use), and the percentiles from the
    calibration window rows unless `percentiles` (low, high) is given, and the channels likewise from `channels`.  Turning the LED on is the caller's job, around
    the whole fit.
    """
    import config_repo as config
    import required_cells

    low_pctl, high_pctl = percentiles or (
        config.calibration_window_low_pctl(),
        config.calibration_window_high_pctl())
    return make_measure(
        grab,
        calibration_patches(rois, pad),
        channels if channels is not None else required_cells.required_channels(led),
        low_pctl, high_pctl,
    )
