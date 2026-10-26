"""
show_plot.py  —  Draw the sorted test-pad signal curves for the Show screen.
---------------------------------------------------------------------------
Pure drawing: takes sorted signal arrays and returns a PIL image of exactly the
requested size, so the screen's canvas can blit it as it is.

Each curve is one Bayer layer at one exposure: the pad pixels sorted from
darkest to brightest, drawn left to right.  Horizontal lines mark the
calibration window's limits.

Public surface
--------------
    render(curves, size, low_dn, high_dn, max_dn=960) -> PIL.Image
"""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw

Curve = Tuple[Tuple[int, int, int], np.ndarray]     # (rgb, sorted signal)

_BG, _AXIS, _LIMIT = (255, 255, 255), (120, 120, 120), (200, 0, 200)
_MARGIN_L, _MARGIN_R, _MARGIN_T, _MARGIN_B = 28, 4, 4, 10


def render(curves: Iterable[Curve], size: Tuple[int, int], low_dn: float,
           high_dn: float, max_dn: float = 960) -> Image.Image:
    """
    Plot sorted signal curves, rank on the x axis and DN on the y axis.

    Args:
        curves: (rgb, values) pairs; values are ascending, in DN with the
                black level removed.  Any length: each is resampled to the
                plot's width.
        size:   (width, height) of the image.
        low_dn, high_dn: The window limits, drawn as horizontal lines.
        max_dn: The top of the y axis.
    """
    w, h = size
    img = Image.new("RGB", size, _BG)
    d = ImageDraw.Draw(img)
    x0, x1 = _MARGIN_L, w - _MARGIN_R
    y0, y1 = h - _MARGIN_B, _MARGIN_T            # y0 is the bottom (0 DN)

    def y_of(dn: float) -> float:
        dn = min(max(dn, 0.0), max_dn)
        return y0 + (y1 - y0) * dn / max_dn

    d.rectangle([x0, y1, x1, y0], outline=_AXIS)
    for dn in (0, max_dn / 2, max_dn):
        d.text((1, y_of(dn) - 5), f"{dn:.0f}", fill=_AXIS)
    for dn in (low_dn, high_dn):
        d.line([(x0, y_of(dn)), (x1, y_of(dn))], fill=_LIMIT)
    for rgb, values in curves:
        values = np.asarray(values, dtype=float)
        if values.size == 0:
            continue
        xs = np.arange(x0, x1 + 1)
        idx = np.round(np.linspace(0, values.size - 1, xs.size)).astype(int)
        d.line([(float(x), y_of(float(v))) for x, v in zip(xs, values[idx])],
               fill=tuple(rgb), width=1)
    return img
