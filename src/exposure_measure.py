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
    patch_levels(layers, patches, channels, low_pctl, high_pctl, black_dn)
    make_measure(grab, patches, channels, low_pctl, high_pctl, black_dn)
    measure_for_led(led, grab, rois, pad) -> Measure     everything from config
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable

import numpy as np

import roi
from exposure_fit import Levels, Measure
from rect import Rect

# Mean dark level of every Bayer layer at every exposure, measured on 2564
# dark frames (see the comment above capture_h5.dark_level).  Subtracting it
# leaves the signal the window limits are written in.
BLACK_DN = 64.0

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
    chips = roi.all_by_role(rois, roi.ROLE_WHITE_BALANCE)
    if not chips:
        raise ValueError("no gray chip (white_balance) region is defined")
    patches = {role: roi.to_layer_rect(pad[role]) for role in roi.TEST_PAD_ROLES}
    for chip in chips:
        patches[chip.name] = roi.to_layer_rect(chip)
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
