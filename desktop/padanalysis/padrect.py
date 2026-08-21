"""padrect.py -- geometry-constrained pad finder.

`segment.py` grows the pad from a B/G threshold and takes what it gets.  Two
things defeat that.  On the original stage the strip lay in a channel whose
floor is a dull olive material (B/G ~0.33) that is *less* blue than the white
paper (0.47) and only a little bluer than the pad (0.25), so a threshold
placed between paper and pad swallowed however much channel floor happened to
be showing beside the strip.  And on a weakly stained or unexposed strip the
pad is not yellow at all, so there is no colour step to threshold anywhere.

This module replaces the free-form region grow with the four things the
hardware guarantees:

  1. the pad is a rectangle;
  2. it is rotated no more than about a degree from the sensor axes;
  3. its width and height are fixed -- only the origin moves;
  4. its left and right edges are the left and right edges of the strip.

Everything is measured on the composite reflectance of `fields.py` -- the mean
over every stored (LED, Bayer layer) of signal divided by gray-chip level --
rather than on one illuminant.  Two consequences:

  * the finder does not need the white LED, which the 2026-08-13 captures do
    not carry;
  * the field is nearly dye-blind, because averaging across the visible range
    washes out a colorant that absorbs in only part of it.  The pad's edges
    then appear as *physical* features rather than chemical ones.

Method, all in bound-crop Bayer-layer coordinates:

  sides      per-row subpixel crossings of half the paper level, over the
             band of bare strip below the pad, fitted jointly as two
             *parallel* lines.  One shared slope for both edges is the
             rigid-strip constraint from (2), and it drops the tilt
             uncertainty to about 0.1 deg.

  bottom     the pad's lower edge is a narrow dark line -- the shadow where
             the laminated pad stands proud of the paper -- and it is present
             whatever the pad's colour.  It is taken as the steepest *rise*
             in the composite down the strip's interior, which is the last
             row of pad before the paper below.

  top        the same feature at the upper edge is weaker, and on a strip
             whose dye is graded down the pad it can be absent entirely.
             The top is therefore placed at `bottom - HEIGHT_FIXED`, and the
             independently measured descent, where one is sharp enough to
             trust, is kept as a check (`top_obs`, `top_residual`).

  rect       (u, v) coordinates in the strip frame -- u across, v down -- so
             `depth` is an exact distance to the rectangle boundary rather
             than a distance transform of a ragged blob.

Constants below are for bound roi 39 with the strip upright in the channel.
"""
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import Capture, open_capture  # noqa: E402
from fields import composite, strip_column  # noqa: E402

#: Fraction of the bare-paper level taken as the strip's edge.
EDGE_FRACTION = 0.5

#: Composite units per row.  A descent shallower than this is not an edge.
MIN_EDGE_SLOPE = 0.05

#: Layer px kept clear of the stage mask's own edge, whose brightness ramp
#: spans about a pixel and a half.
MASK_MARGIN = 1.5

#: Robust side fit: passes of reject-and-refit, and the rejection cut in
#: robust standard deviations of the row residual.
REFIT_PASSES = 4
OUTLIER_SIGMA = 4.0

#: Pooled over the 2026-08-10 and 2026-08-13 sets.  See `pooled_size()`.
WIDTH_FIXED = 41.35
HEIGHT_FIXED = 53.34

#: Re-seating (see `PadRect._reseat`).  A fitted top that misses the observed
#: one by more than RESEAT_TOLERANCE px, or a lower edge shallower than
#: WEAK_EDGE_SLOPE, means the search window found something that is not the
#: pad's lower edge.  RESEAT_SEARCH is how far either side of the pad-height
#: prediction the real edge is then looked for.  The tolerance is an order of
#: magnitude above the 0.64 px worst case over the 15 captures that predate
#: the geometry change, so it cannot fire on them.
RESEAT_TOLERANCE = 3.0
WEAK_EDGE_SLOPE = 0.05
RESEAT_SEARCH = 6


def _cross(v, i, thr):
    """Subpixel crossing of `thr` on the interval [i, i+1], either direction."""
    a, b = float(v[i]), float(v[i + 1])
    if a == b:
        return i + 0.5
    return i + (thr - a) / (b - a)


def _parallel_fit(rows, L, R, yc):
    """
    Least squares fit of two parallel lines to the strip's left and right
    edges.

    Args:
        rows: Row indices the crossings were measured on.
        L, R: Subpixel column of the left and right edge on each row.
        yc: Row the intercepts are referred to.

    Returns:
        ((slope, left_intercept, right_intercept), residuals).
    """
    nr = len(rows)
    A = np.zeros((2 * nr, 3))
    A[:nr, 0] = rows - yc
    A[:nr, 1] = 1.0
    A[nr:, 0] = rows - yc
    A[nr:, 2] = 1.0
    obs = np.concatenate([L, R])
    sol, *_ = np.linalg.lstsq(A, obs, rcond=None)
    return sol, obs - A @ sol


def _peak(v, i):
    """Subpixel abscissa of an extremum of `v` bracketed at index `i`."""
    a, b, c = float(v[i - 1]), float(v[i]), float(v[i + 1])
    d = a - 2.0 * b + c
    return i + (0.0 if d == 0 else 0.5 * (a - c) / d)


class PadRect:
    """The pad as a rotated rectangle, plus the edge fits behind it."""

    def __init__(self, c: Capture, rung: str = "e1", fixed_size: bool = False,
                 label: str = None):
        """
        Args:
            c: The capture to segment.
            rung: Exposure rung to segment on.  Captures that lack it fall
                back to whichever rung they hold.
            fixed_size: Snap the rectangle's width to WIDTH_FIXED, keeping the
                measured centre and tilt.  Use this when a strip is pushed
                against a channel wall and one edge may be clipped.
            label: Accepted as a synonym for `rung`, for callers written
                against the earlier signature.
        """
        self.capture = c
        self.comp, self.cells = composite(c, label or rung)
        self.tot = self.comp                 # name kept for older reports
        h, w = self.comp.shape

        self.xseed = strip_column(c)
        self.rowprof = np.median(
            self.comp[:, self.xseed - 8:self.xseed + 9], axis=1)
        self.paper_level = float(np.median(self.rowprof[95:118]))

        self._find_ends()
        self._find_sides(w)

        if fixed_size:
            cu = 0.5 * (self.aL + self.aR)
            self.aL, self.aR = cu - WIDTH_FIXED / 2, cu + WIDTH_FIXED / 2
            self.width = WIDTH_FIXED
        self.fixed_size = fixed_size

        yy, xx = np.mgrid[0:h, 0:w]
        self.u = xx - (self.aL + self.m * (yy - self.yc))
        self.v = yy.astype(np.float64)

    # -- ends: the pad's own edges, found without reference to its colour ---
    def _find_ends(self):
        p = self.rowprof
        g = np.gradient(p)
        thr = EDGE_FRACTION * self.paper_level
        self.strip_top = float(np.argmax(p > thr))

        lo = int(self.strip_top) + 40
        hi = min(int(self.strip_top) + 78, len(p) - 2)
        ib = lo + int(np.argmax(g[lo:hi]))
        self.bot = _peak(g, ib)
        self.bot_slope = float(g[ib])
        self.bot_source = "window"

        tlo = int(self.strip_top) + 1
        thi = max(tlo + 2, int(self.bot) - 30)
        it = tlo + int(np.argmin(g[tlo:thi]))
        self.top_obs = _peak(g, it) if -g[it] >= MIN_EDGE_SLOPE else float("nan")
        self.top_slope = float(g[it])

        self.top = self.bot - HEIGHT_FIXED
        self.height = HEIGHT_FIXED
        self.top_residual = self.top_obs - self.top

        self._reseat(g)

        # How much of the pad's upper end the stage mask hides.  Positive
        # means the whole pad is in view; negative means the first
        # |top_clearance| rows of the rectangle are mask, not pad, and the
        # erosion has to exceed that before the region is all pad.
        self.top_clearance = self.top - self.strip_top

    def _reseat(self, g):
        """
        Re-place the rectangle when the search window found the wrong rise.

        The window above looks for the pad's lower edge 40 to 78 rows below
        the strip's end, which assumes the pad sits a fixed distance from
        that end.  From C1014 that stopped being true: the pad was set
        farther down the backing strip and the bound ROI was moved up, which
        slides the window clean off the pad's lower edge and leaves it to
        latch onto the strongest gradient it can see -- on C1014, the flank
        of the stain itself, 16 px high.

        What did *not* change is the pad's height, so that is what re-seats
        it.  A top edge disagreeing with the fitted top by more than
        RESEAT_TOLERANCE is evidence the window found the wrong feature; so
        is a lower edge too weak to be an edge at all when a far stronger
        one is in view.  In both cases the rectangle is moved onto the rise
        that lies one pad-height below the observed top.

        The 15 captures taken before C1014 agree to within 0.64 px and have
        lower edges of slope 0.15 or better, so neither test can fire on
        them and their geometry is untouched.  `bot_source` records which
        rule placed the rectangle.

        Args:
            g: Row-gradient of the composite profile down the strip.
        """
        weak = self.bot_slope < WEAK_EDGE_SLOPE
        disagrees = (not np.isnan(self.top_residual)
                     and abs(self.top_residual) > RESEAT_TOLERANCE)
        if not (weak or disagrees):
            return

        if disagrees:
            target = self.top_obs + HEIGHT_FIXED
        else:
            # No usable top edge: take the strongest rise anywhere a pad of
            # this height still fits below the strip's end.
            lo = int(self.strip_top) + int(HEIGHT_FIXED) - 4
            hi = len(g) - 2
            if hi - lo < 2:
                return
            target = lo + int(np.argmax(g[lo:hi]))

        w0 = max(int(target) - RESEAT_SEARCH, int(self.strip_top) + 2)
        w1 = min(int(target) + RESEAT_SEARCH + 1, len(g) - 2)
        if w1 - w0 < 2:
            return
        ib = w0 + int(np.argmax(g[w0:w1]))
        slope = float(g[ib])
        if slope <= self.bot_slope and not disagrees:
            return                      # nothing better is out there

        self.bot = _peak(g, ib)
        self.bot_slope = slope
        self.top = self.bot - HEIGHT_FIXED
        self.top_residual = self.top_obs - self.top
        self.bot_source = "height"

    # -- sides: the strip's edges below the pad, as two parallel lines ------
    def _find_sides(self, w):
        thr = EDGE_FRACTION * self.paper_level
        rows, L, R = [], [], []
        y0 = int(np.ceil(self.bot)) + 4
        y1 = min(len(self.rowprof) - 2, 122)
        for y in range(y0, y1):
            lab, n = ndimage.label(self.comp[y] > thr)
            k = lab[self.xseed]
            if k == 0:
                continue
            idx = np.flatnonzero(lab == k)
            a, b = int(idx[0]), int(idx[-1])
            if a == 0 or b >= w - 1 or b - a < 20:
                continue
            rows.append(y)
            L.append(_cross(self.comp[y], a - 1, thr))
            R.append(_cross(self.comp[y], b, thr))
        if len(rows) < 8:
            raise RuntimeError(f"{self.capture.name}: only {len(rows)} usable "
                               f"strip rows below the pad")
        rows = np.asarray(rows, dtype=np.float64)
        L, R = np.asarray(L), np.asarray(R)
        self.yc = float(rows.mean())
        keep = np.ones(len(rows), dtype=bool)

        # A hand-written mark on the paper below the pad reads as a dark run
        # and pulls one edge in by several px.  Reject such rows on their
        # residual and refit, rather than let them tilt the whole strip.
        for _ in range(REFIT_PASSES):
            sol, resid = _parallel_fit(rows[keep], L[keep], R[keep], self.yc)
            r = np.abs(np.concatenate([
                L - (sol[1] + sol[0] * (rows - self.yc)),
                R - (sol[2] + sol[0] * (rows - self.yc))]))
            scale = max(1.4826 * float(np.median(r[np.tile(keep, 2)])), 0.05)
            bad = (r > OUTLIER_SIGMA * scale).reshape(2, -1).any(axis=0)
            if not (bad & keep).any() or (keep & ~bad).sum() < 8:
                break
            keep &= ~bad

        self.rows_all, self.L_all, self.R_all = rows, L, R
        self.rows_used, self.L_obs, self.R_obs = rows[keep], L[keep], R[keep]
        self.n_rejected = int((~keep).sum())
        sol, resid = _parallel_fit(self.rows_used, self.L_obs, self.R_obs,
                                   self.yc)
        self.m, self.aL, self.aR = (float(v) for v in sol)
        self.width = self.aR - self.aL
        self.angle_deg = float(np.degrees(np.arctan(self.m)))
        self.edge_rms = float(np.std(resid))

    # -- the mask ----------------------------------------------------------
    @property
    def depth(self) -> np.ndarray:
        """Distance to the nearest rectangle edge, positive inside.

        Exact, unlike `segment.Pad.depth`, which is a distance transform of a
        ragged blob: the pad is a rectangle, so its distance field is just the
        smallest of the four edge distances.
        """
        return np.minimum.reduce([self.u, self.width - self.u,
                                  self.v - self.top, self.bot - self.v])

    def mask(self, erode: float = 0.0) -> np.ndarray:
        """
        Pad pixels at least `erode` px inside every edge, and in view.

        A strip pushed too far up the channel has the upper end of its pad
        behind the stage mask (`top_clearance` negative).  Those rows are
        rectangle but not pad, so they are dropped here rather than averaged
        in.  `n_hidden` counts them.
        """
        m = (self.depth > erode) & (self.v >= self.strip_top + MASK_MARGIN)
        self.n_hidden = int(((self.depth > erode) & ~m).sum())
        return m

    # -- summary attributes -------------------------------------------------
    @property
    def area(self) -> int:
        return int(self.mask(0).sum())

    @property
    def bbox(self):
        ys, xs = np.nonzero(self.mask(0))
        return (int(xs.min()), int(ys.min()),
                int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))

    @property
    def centroid(self):
        ys, xs = np.nonzero(self.mask(0))
        return (float(xs.mean()), float(ys.mean()))

    def paper_mask(self, collar: float = 5.0, bottom_trim: int = 4,
                   blemish: float = 0.75):
        """
        The bare paper below the pad -- the control region.

        Same strip, same height above the stage, same illumination, no
        chemistry.  Whatever this region does at its own edges is geometry,
        not dye, and its reflectance is the strip's own substrate, which is
        what lets one session be compared with another whose gray card is not
        the same object.

        The region is taken from the fitted rectangle rather than by
        thresholding.  On the repainted stage the gray background reads within
        a few percent of the strip's paper, so a brightness threshold there
        selects the whole field; the strip frame does not have that failure
        mode.  Pixels far below the region's own median are dropped, which
        removes the hand-written mark that appears on some strips.

        Args:
            collar: Layer px of clearance kept below the pad's bottom edge.
            bottom_trim: Rows dropped at the crop floor, where the strip runs
                out of frame.
            blemish: Fraction of the region median below which a pixel is
                treated as a mark on the paper rather than paper.

        Returns:
            (mask, depth) -- the region and its own distance-from-edge map,
            where `depth` is the distance to the nearest side or to the top
            of the region.  The crop floor is treated as interior, so the
            artificial cut there does not read as an edge.
        """
        h = self.comp.shape[0]
        v_top = self.bot + collar
        v_bot = h - bottom_trim
        m = (self.u >= 0) & (self.u <= self.width) & \
            (self.v >= v_top) & (self.v < v_bot)
        if m.any():
            med = float(np.median(self.comp[m]))
            m &= self.comp > blemish * med
            m = ndimage.binary_opening(m, np.ones((3, 3)))
        depth = np.minimum.reduce([self.u, self.width - self.u,
                                   self.v - v_top,
                                   np.full_like(self.v, 1e3)])
        return m, depth

    def corners(self):
        """The four rectangle corners as (x, y), clockwise from top-left."""
        def xy(u, v):
            return (self.aL + u + self.m * (v - self.yc), v)
        return [xy(0, self.top), xy(self.width, self.top),
                xy(self.width, self.bot), xy(0, self.bot)]

    def describe(self) -> str:
        tr = ("   -  " if np.isnan(self.top_residual)
              else f"{self.top_residual:+6.2f}")
        return (f"{self.capture.name:>10}  L={self.aL:6.2f} R={self.aR:6.2f} "
                f"W={self.width:5.2f} | top={self.top:5.2f} bot={self.bot:5.2f} "
                f"| tilt={self.angle_deg:+5.2f} deg  rms={self.edge_rms:.3f} px "
                f" n={len(self.rows_used):2d}-{self.n_rejected:<2d} "
                f"top_resid={tr}  clr={self.top_clearance:+5.2f}")


def pooled_size(names, max_softness: float = 2.5):
    """
    Estimate the constant width and height from a set of captures.

    A strip pushed against a channel wall can have its near edge clipped by
    the wall's lip, or thrown into the wall's shadow, and either displaces the
    half-level crossing inward.  Only captures whose two edges are both sharp
    contribute to the width.  The height pools over the captures whose upper
    edge is sharp enough to measure independently.

    Args:
        names: Capture names to pool over.
        max_softness: Largest 20-80% edge transition width, in layer px,
            that a capture may have and still contribute to the width.

    Returns:
        (width, width_sd, w_used, height, height_sd, h_used).
    """
    W, H, wu, hu = [], [], [], []
    for name in names:
        c = open_capture(name)
        try:
            p = PadRect(c)
            if max(edge_softness(p)) <= max_softness:
                W.append(p.width)
                wu.append(name)
            if not np.isnan(p.top_obs):
                H.append(p.bot - p.top_obs)
                hu.append(name)
        finally:
            c.close()
    return (float(np.mean(W)), float(np.std(W, ddof=1)), wu,
            float(np.mean(H)), float(np.std(H, ddof=1)), hu)


def edge_softness(p: PadRect, span: int = 14, window: int = 8):
    """
    Width of the strip's left and right edge transitions, in layer px.

    A strip edge imaged square-on crosses from dark surround to full paper in
    about a pixel and a half.  An edge that is clipped by a channel wall, or
    that lies in the wall's shadow, ramps over four or more, and its
    half-level crossing is displaced inward.  This is the quantity that says
    whether a capture's measured width can be trusted.

    Args:
        p: The fitted rectangle.
        span: Rows of bare strip below the pad to average over.
        window: Layer px either side of the fitted edge to search in.

    Returns:
        (left, right) 20%-to-80% transition widths.
    """
    y0 = int(np.ceil(p.bot)) + 6
    band = p.comp[y0:y0 + span].mean(axis=0)
    yc = y0 + 0.5 * span
    out = []
    for x0, sign in ((p.aL + p.m * (yc - p.yc), +1),
                     (p.aR + p.m * (yc - p.yc), -1)):
        lo = int(round(x0)) - window
        hi = int(round(x0)) + window
        seg = band[max(lo, 0):min(hi, len(band))]
        if sign < 0:
            seg = seg[::-1]                      # both edges now rise
        c20 = _first_crossing(seg, 0.2 * p.paper_level)
        c80 = _first_crossing(seg, 0.8 * p.paper_level)
        out.append(abs(c80 - c20) if not (np.isnan(c20) or np.isnan(c80))
                   else float("nan"))
    return out[0], out[1]


def _first_crossing(v, thr):
    """Subpixel abscissa where `v` first rises through `thr`, else NaN."""
    idx = np.flatnonzero((v[:-1] < thr) & (v[1:] >= thr))
    return _cross(v, int(idx[0]), thr) if len(idx) else float("nan")


if __name__ == "__main__":
    from samples import NAMES

    for name in NAMES:
        c = open_capture(name)
        try:
            p = PadRect(c)
            sl, sr = edge_softness(p)
            print(p.describe() + f"  soft=({sl:4.2f},{sr:4.2f})")
        except Exception as exc:                       # noqa: BLE001
            print(f"{name:>10}  FAILED: {type(exc).__name__}: {exc}")
        c.close()

    w, wsd, wu, hgt, hsd, hu = pooled_size(NAMES)
    print(f"\npooled width  {w:.2f} +/- {wsd:.2f} px  from {len(wu)}: "
          f"{', '.join(wu)}")
    print(f"pooled height {hgt:.2f} +/- {hsd:.2f} px  from {len(hu)}: "
          f"{', '.join(hu)}")
