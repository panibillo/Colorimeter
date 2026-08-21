"""segment.py -- find the reagent pad in a bound crop, without assuming a rect.

The seeded `sample` ROI is only approximate: the strip shifts and tilts between
placements, and the pad's stained area does not fill the seeded 34x34 box the
same way twice.  This finds the pad itself.

Method, all in bound-crop Bayer-layer coordinates:

  1. white LED, one exposure rung -> tot = R+Gr+Gb+B  and  blue = B / mean(Gr,Gb)
  2. strip  = Otsu(tot), connected component containing the seeded sample centre
              (separates the strip from the two gray chips, which the black
              gaps keep disconnected)
  3. pad    = strip & (blue < Otsu(blue restricted to strip)); largest component,
              holes filled.  The pad is yellow/orange, the paper below it white,
              so B/G is the discriminator that does not care about illumination.
  4. depth  = Euclidean distance transform of pad -> every pixel knows how far
              it is from the nearest non-pad pixel.

`depth` replaces "come in from the edge a bit": a measurement at erosion k is
just `depth >= k`, which follows a tilted or rounded pad instead of assuming it
is an axis-aligned rectangle.
"""
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import Capture, open_capture  # noqa: E402


def disk(radius: int) -> np.ndarray:
    """Boolean disk structuring element of the given radius."""
    r = int(radius)
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    return (y * y + x * x) <= r * r + 0.5


def otsu(values: np.ndarray) -> float:
    """Otsu's threshold on a 1-D sample, via a 256-bin histogram."""
    v = values[np.isfinite(values)]
    lo, hi = float(v.min()), float(v.max())
    if hi <= lo:
        return lo
    hist, edges = np.histogram(v, bins=256, range=(lo, hi))
    p = hist.astype(np.float64) / hist.sum()
    centres = 0.5 * (edges[:-1] + edges[1:])
    w0 = np.cumsum(p)
    w1 = 1.0 - w0
    m0 = np.cumsum(p * centres) / np.maximum(w0, 1e-12)
    mt = (p * centres).sum()
    m1 = (mt - np.cumsum(p * centres)) / np.maximum(w1, 1e-12)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(centres[int(np.argmax(between))])


def white_fields(c: Capture, label: str = "e1"):
    """
    Return (tot, blue, layers) for the white LED at one exposure rung.

    Captures that do not hold the requested rung fall back to the one they
    do hold; captures that stored no white illuminant at all cannot be
    segmented this way and raise.
    """
    if "white" not in c.leds:
        raise KeyError(f"{c.name}: no white illuminant was stored")
    labels = c.labels("white")
    label = label if label in labels else labels[0]
    lay = {}
    for ch in ("R", "Gr", "Gb", "B"):
        got = c.layer("white", label, ch)
        if got is None:
            raise KeyError(f"{c.name}: white/{label}/{ch} not stored")
        lay[ch] = got[0]
    tot = lay["R"] + lay["Gr"] + lay["Gb"] + lay["B"]
    g = 0.5 * (lay["Gr"] + lay["Gb"])
    blue = lay["B"] / np.maximum(g, 1.0)
    return tot, blue, lay


class Pad:
    """The segmented pad, its distance map, and the QC numbers behind them."""

    def __init__(self, c: Capture, label: str = "e1"):
        self.capture = c
        tot, blue, _ = white_fields(c, label)
        self.tot = tot
        self.blue = ndimage.median_filter(blue, size=3)

        # -- strip -------------------------------------------------------
        self.tot_thr = otsu(tot.ravel())
        bright = tot > self.tot_thr
        bright = ndimage.binary_opening(bright, np.ones((3, 3)))
        lab, n = ndimage.label(bright)
        sx, sy, sw, sh = c.rects["sample"]
        seed = lab[sy + sh // 2, sx + sw // 2]
        if seed == 0:                       # seed landed in a gap: take nearest
            ys, xs = np.nonzero(lab)
            d = (ys - (sy + sh // 2)) ** 2 + (xs - (sx + sw // 2)) ** 2
            seed = lab[ys[np.argmin(d)], xs[np.argmin(d)]]
        self.strip = ndimage.binary_fill_holes(lab == seed)

        # -- pad ---------------------------------------------------------
        inside = self.blue[self.strip]
        self.blue_thr = otsu(inside)
        raw_pad = self.strip & (self.blue < self.blue_thr)
        # A plain opening by a disk, not an opening by reconstruction: the
        # thing being removed is the strip's own dark edge line, which is as
        # un-blue as the pad and *touches* it, so reconstruction would grow
        # straight back down it.  The disk costs ~3 px of corner rounding on a
        # pad that is measured 3+ px inside its edge anyway.
        core = ndimage.binary_opening(raw_pad, disk(3))
        lab, n = ndimage.label(core)
        if n == 0:
            raise RuntimeError(f"{c.name}: no pad found")
        sizes = ndimage.sum(core, lab, range(1, n + 1))
        self.pad = ndimage.binary_fill_holes(lab == (int(np.argmax(sizes)) + 1))

        # -- depth map ---------------------------------------------------
        self.depth = ndimage.distance_transform_edt(self.pad)

        # -- QC ----------------------------------------------------------
        lo = float(np.median(inside[inside < self.blue_thr]))
        hi = float(np.median(inside[inside >= self.blue_thr]))
        self.blue_pad, self.blue_paper = lo, hi
        self.separation = (hi - lo) / max(np.std(inside[inside < self.blue_thr]),
                                          1e-9)
        ys, xs = np.nonzero(self.pad)
        self.bbox = (int(xs.min()), int(ys.min()),
                     int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
        self.area = int(self.pad.sum())
        self.centroid = (float(xs.mean()), float(ys.mean()))
        self.max_depth = float(self.depth.max())

    def mask(self, erode: float) -> np.ndarray:
        """
        Pad pixels with `erode` or more rings of pad between them and the edge.

        erode=0 is the whole pad; the distance transform gives a boundary pixel
        depth 1.0, so the test is strictly greater than.
        """
        return self.depth > erode

    def describe(self) -> str:
        x, y, w, h = self.bbox
        return (f"{self.capture.name:>6}  pad bbox=({x:3d},{y:3d},{w:3d},{h:3d}) "
                f"area={self.area:5d}  centroid=({self.centroid[0]:6.1f},"
                f"{self.centroid[1]:6.1f})  maxdepth={self.max_depth:4.1f}  "
                f"B/G pad={self.blue_pad:.3f} paper={self.blue_paper:.3f} "
                f"thr={self.blue_thr:.3f}")


if __name__ == "__main__":
    from probe import FILES
    for name in FILES:
        c = open_capture(name)
        p = Pad(c)
        print(p.describe())
        for k in (0, 2, 4, 6, 8, 10):
            print(f"        erode {k:2d}: n={int(p.mask(k).sum()):5d}")
        c.close()
