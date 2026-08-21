"""fields.py -- illuminant-agnostic image fields for pad finding.

`segment.white_fields` reads the white LED, which every capture up to C1009
carries.  The 2026-08-13 captures do not: white and violet were switched off,
leaving eight narrow-band illuminants.  Anything that locates the pad
therefore has to work from whichever illuminants are present, and has to give
the same numbers whichever set that is.

The field used here is a **composite reflectance**:

    reflectance(led, rung, layer) = signal / mean(gray chip signal)
    composite                     = mean over every stored (led, layer)

Dividing by the chips first puts every illuminant on one scale -- a surface
reading 1.0 is as bright as the gray chips -- so the mean over illuminants is
a well-posed average rather than a sum of arbitrary exposures.  Two properties
matter downstream:

  * it is insensitive to which illuminants a capture happens to hold, because
    every term is already normalised;
  * it is nearly insensitive to the dye, because averaging over the whole
    visible range washes out a colorant that absorbs in part of it.  A blank
    pad and a heavily stained one differ by roughly a factor of two in the
    blue, but their *edges* appear in the composite at the same place.

That second property is what the end-finding in `padrect` relies on.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import BAYER, Capture, open_capture  # noqa: E402

MIN_CHIP_DN = 50.0     # below this the cell carries no usable signal


def rung_for(c: Capture, led: str, rung: str) -> str:
    """The requested exposure rung, or the capture's only one if it lacks it."""
    labels = c.labels(led)
    return rung if rung in labels else labels[0]


def chip_levels(c: Capture, led: str, rung: str, layer: str):
    """Mean dark-subtracted signal on each gray chip, or None if not stored."""
    got = c.layer(led, rung_for(c, led, rung), layer)
    if got is None:
        return None
    s = got[0]
    out = []
    for k in ("gray1", "gray2"):
        x, y, w, h = c.rects[k]
        out.append(float(s[y:y + h, x:x + w].mean()))
    return tuple(out)


def reflectance(c: Capture, led: str, rung: str, layer: str):
    """
    Signal divided by the mean gray-chip level, over the whole bound crop.

    This is a coarse reflectance: it uses the flat average of the two chips
    rather than the fitted illumination surface, so it is right to a percent
    or so across the crop.  Good enough to find edges; `measure.py` uses the
    fitted surface for the numbers that are reported.

    Returns:
        The field, or None if the cell was not stored or carries no signal.
    """
    got = c.layer(led, rung_for(c, led, rung), layer)
    if got is None:
        return None
    lv = chip_levels(c, led, rung, layer)
    ref = 0.5 * (lv[0] + lv[1])
    if ref < MIN_CHIP_DN:
        return None
    return got[0] / ref


def composite(c: Capture, rung: str = "e1"):
    """
    Mean reflectance over every stored (LED, Bayer layer) of one rung.

    Returns:
        (comp, cells) -- the field, and the list of (led, layer) that went
        into it.

    Raises:
        RuntimeError: if the capture holds no usable cell at this rung.
    """
    acc, cells = [], []
    for led in c.leds:
        for layer in BAYER:
            r = reflectance(c, led, rung, layer)
            if r is not None:
                acc.append(r)
                cells.append((led, layer))
    if not acc:
        raise RuntimeError(f"{c.label()}: no usable cell at rung {rung}")
    return np.mean(acc, axis=0), cells


def strip_column(c: Capture) -> int:
    """Layer column through the middle of the seeded sample rect."""
    x, y, w, h = c.rects["sample"]
    return x + w // 2


if __name__ == "__main__":
    from samples import NAMES

    print(f"{'capture':<11}{'cells':>6}{'paper':>8}{'pad':>8}{'surround':>10}"
          f"{'contrast':>10}")
    for n in NAMES:
        c = open_capture(n)
        comp, cells = composite(c)
        xs = strip_column(c)
        col = comp[:, xs - 8:xs + 9]
        paper = float(np.median(col[95:118]))
        pad = float(np.median(col[40:60]))
        sur = float(np.median(comp[95:118, 58:65]))
        print(f"{n:<11}{len(cells):>6}{paper:>8.3f}{pad:>8.3f}{sur:>10.3f}"
              f"{paper / max(sur, 1e-6):>10.1f}")
        c.close()
