"""
padfind.py  --  Find the indicator pad in one photo_red frame, on the Pi.
-------------------------------------------------------------------------
A capture needs to know where the pad is *before* it takes the photos it
keeps, so that it can test the pad itself for clipping (and later, set its
exposure_us from the pad).  This module finds the pad from a single photo
lit by the photo_red LED.

It is a port of the cassette part of the desktop pad finder,
`desktop/padanalysis/padrect.py` (class PadRect, "window" layout).  The
arithmetic is the same, step for step, so that a pad found here and a pad
found on the desktop from the same frame agree exactly.  What changed:

  * It takes arrays, not an .h5 archive: the photo_red R Bayer layer of the
    bound crop, its dark level, and three rectangles.
  * It handles only the cassette holders (cap_style F, G1, H1: a dark frame
    with a small window).  The older open-channel holder is not supported.
  * It needs only numpy.  The desktop version imports scipy, but only for
    the open-channel holder and for the bare-paper mask, neither of which
    is used here.

Why photo_red alone (Bill, 2026-11-14): photo_red light changes the pad's
color least of all the LEDs, and the pad's dye barely absorbs it, so the
pad's edges show the same way before and after exposure to arsine.  A frame
for pad finding does not need a good exposure, so the LED is lit only long
enough to take one frame.

Why an offset is added: a pad found on photo_red alone sits a fraction of a
pixel inside the pad the desktop finds from the average of every stored LED
and layer, which is the fit the analysis chain was built on.  The measured
difference (desktop `color_study.fit_basis_offsets`, 30 F/G/H files,
2026-11-13) is added after fitting, so the result lands where the desktop's
usual fit would.  The fit *before* the offset is the one a post-image is
guided by, so both are kept.

Coordinates
-----------
Everything is in the Bayer-layer pixels of the bound crop: column x counts
from the crop's left edge, row y from its top edge, one layer pixel being
two sensor pixels.  These are the coordinates of the arrays a capture
stores, and of `roi.to_layer_rect_within(rect, bound)`.

How the pad is found  (field = photo_red R signal / gray-chip signal)
--------------------------------------------------------------------
  1. The field.  The R layer, dark level removed, divided by the mean
     signal on the two gray chips.  A surface as bright as the chips reads
     1.0, whatever the exposure_us.
  2. A profile down the strip.  The median of the field over 17 columns
     centered on the sample ROI gives one value per row.
  3. The window.  Rows brighter than half the profile's 95th percentile are
     the cassette window (pad plus a little white paper above and below).
     Its median is the "pad level".
  4. The bottom edge.  The pad's lower edge casts a thin shadow, so the
     profile rises steeply where pad gives way to paper below.  The bottom
     is the steepest rise 40-78 rows below the window's top.
  5. The top edge.  The steepest fall within TOP_SEARCH_ROWS rows of one
     pad height above the bottom, where the backing strip above gives way
     to the pad.  Only when no fall there is steep enough is the top placed
     one pad height above the bottom.  (Until 2026-11-15 the top was always
     placed, and a steep fall anywhere above the bottom could "reseat" the
     bottom one pad height below it; a dark mark on the pad could set that
     off.  It never fired on a cassette sweep, and it was removed.)
  6. The sides, first pass.  On each pad row, the outermost columns brighter
     than half the pad level, refined to a fraction of a pixel.
  7. The sides, final.  On each pad row, the column where the field changes
     most steeply within 2 px of the first pass's side (a pre-image), or of
     the pre-image's side (a post-image).  Both sides are fitted as two
     parallel lines (the strip is rigid), and rows that disagree badly are
     dropped and the lines refitted.

Public surface
--------------
    PadFit                         the fitted pad (see its docstring)
    PadFindError                   raised when the pad cannot be found
    reflectance_field(layer, dark_dn, gray1, gray2) -> ndarray
    fit_pad(field, sample, prior=None) -> PadFit     no offset applied
    find_pad(layer, dark_dn, rects, section, prior=None, offset=None)
        -> (fit_before_offset, fit_after_offset)
    OFFSET_PRE, OFFSET_POST        the photo_red offsets (layer px)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field as dc_field, replace
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

from rect import Rect

# ---------------------------------------------------------------------------
# Constants.  Each is the desktop padrect.py value unless noted.
# ---------------------------------------------------------------------------

#: Fraction of the pad level taken as the edge between pad and frame
#: (steps 3 and 6).  Half-way between dark frame and pad.
EDGE_FRACTION = 0.5

#: Smallest change in the field per row (field units per row) that counts
#: as the pad's top edge in step 5.  A shallower fall is not trusted.
MIN_EDGE_SLOPE = 0.05

#: Layer px kept clear of the window's top edge when building the pad mask:
#: the edge's own brightness ramp is about a pixel and a half wide.
MASK_MARGIN = 1.5

#: Side fitting (step 7): up to REFIT_PASSES rounds of dropping rows whose
#: edge lies more than OUTLIER_SIGMA robust standard deviations from the
#: fitted lines, then refitting.
REFIT_PASSES = 4
OUTLIER_SIGMA = 4.0

#: The pad's width in layer px, pooled over the 2026-08-10 and 2026-08-13
#: captures (desktop padrect.pooled_size).  Not used to place the pad: the
#: width is measured, and checked against this (PadFit.qc).
WIDTH_FIXED = 41.35

#: The pad's height in the cassette holders, layer px: the mean distance
#: from the steepest fall at the pad's top to the steepest rise at its
#: bottom, each measured on its own in photo_red, over 68 sweeps (the 30
#: F/G/H files' pre- and post-images and 8 Pi frames; sd 0.13, range
#: 52.03-52.72; review of 2026-11-15).  It says where to look for the top
#: (step 5) and places the top only when the top cannot be measured.
#: Until 2026-11-15 this was 53.34, the height on the old open-channel
#: holder, and the top was always placed from it, about one row too high.
HEIGHT_FIXED = 52.3

#: Step 5: the top is looked for this many whole rows either side of one
#: pad height above the bottom.  The measured heights stay within 0.4 rows
#: of HEIGHT_FIXED, so 2 rows leaves room, while keeping a dark mark on
#: the pad (a dot, a blotch) from being taken for the top.  The limit: a
#: mark whose upper edge lies 1-3 rows below the top, and falls more
#: steeply than the step from backing to pad, moves the top by up to
#: 0.8 px, because the lens blur spreads its edge into the search (unit
#: test test_dark_mark_is_not_the_top; a synthetic pad 1.2 on backing
#: 1.6, mark 0.5).  In photo_red the dye darkens the pad too little for
#: that: over the 120 fits of 2026-11-15 the height stayed within
#: 52.11-52.68 rows.
TOP_SEARCH_ROWS = 2

#: Step 4: the bottom edge is looked for this many rows below the window's
#: top edge (first and one-past-last row of the search).
BOTTOM_SEARCH_ROWS = (40, 78)

#: top_obs, a record only: the steepest fall from the window's top down to
#: this many rows above the bottom edge.
TOP_SEARCH_GAP = 30

#: Step 2: half the number of columns the row profile is taken over
#: (17 columns = the strip column and 8 either side).
PROFILE_HALF_WIDTH = 8

#: Steps 6 and 7: pad rows within this many rows of the top or bottom edge
#: are not used to find the sides (the end shadows would disturb them).
END_CLEARANCE = 3

#: Step 6: how far either side of the strip column (layer px) the sides are
#: looked for.  Half the pad width plus the side band, with room to spare.
SIDE_SEARCH = 40

#: Step 6: a row whose bright run is narrower than this (layer px) is not a
#: full pad row and is skipped.
MIN_PAD_RUN = 20

#: Step 7: how far either side of the guiding side (layer px) the edge is
#: looked for.  Between a pre-image and the post-image of the same file the
#: strip moved at most 1.23 px (26 cassette files), so 2 px covers it.
SIDE_SEARCH_PX = 2.0

#: Fewest rows that must give both sides for a fit to be made.
MIN_ROWS = 8

#: A gray-chip mean signal below this (DN above the dark level) is too dark
#: to divide by (desktop fields.MIN_CHIP_DN).
MIN_CHIP_DN = 50.0

#: Step 3 check: the window layout is confirmed when the last FLOOR_ROWS
#: rows of the profile read below EDGE_FRACTION of its 95th percentile
#: (the cassette's dark frame closes the window above the crop floor).
FLOOR_ROWS = 4

#: The photo_red offsets, (left, right, top, bottom) in layer px, + meaning
#: right or down.  Added to a photo_red-only fit so it lands where the
#: desktop's all-channel fit would.
#:   OFFSET_PRE:  mean over the 30 F/G/H pre-images (sd 0.14, 0.07, 0.14,
#:                0.08); desktop chain_samples.DEFAULT_PRE_OFFSET (Bill,
#:                2026-11-13).
#:   OFFSET_POST: OFFSET_PRE plus the mean difference between a post-image's
#:                offset and its own pre-image's, (+0.03, -0.09, -0.10,
#:                -0.22), sd (0.09, 0.05, 0.18, 0.07), over the same 30
#:                files.  The desktop applies only the top and bottom terms
#:                (chain_samples.POST_OFFSET_STEADY); the Pi applies all
#:                four, because the side terms are as steady as the others
#:                (Bill, 2026-11-14).
#: Re-measured 2026-11-15, when the top began to be measured (step 5) on
#: both the Pi and the desktop: only the top terms changed, pre 0.33 ->
#: 0.43 and post 0.11 -> 0.33.  The top sits 0.43 px lower in the
#: all-channel fit than in photo_red because the blue LEDs see the pad's
#: ends about half a row to a row lower than red does.  Before 2026-11-15
#: the values were (0.10, 0.44, 0.33, 0.33) and (0.13, 0.35, 0.11, 0.11).
#: These are the factory values of the config rows capture.padfind_offset_pre
#: and capture.padfind_offset_post, which the capture uses, so they can be
#: re-measured on the final device (work plan, stage 4a).
OFFSET_PRE = (0.10, 0.44, 0.43, 0.33)
OFFSET_POST = (0.10 + 0.03, 0.44 - 0.09, 0.43 - 0.10, 0.33 - 0.22)

#: Quality limits (PadFit.qc).  A fit outside any of these is reported, and
#: the capture falls back to the seeded sample rectangle.  Set generously;
#: the 30 F/G/H files are checked against them by the desktop parity script.
QC_WIDTH_TOLERANCE = 2.0      # layer px from WIDTH_FIXED
QC_MAX_TILT_DEG = 2.0         # degrees from the sensor's columns
QC_MAX_EDGE_RMS = 0.5         # layer px, scatter of the side edges

SECTION_PRE = "pre"
SECTION_POST = "post"


class PadFindError(RuntimeError):
    """The pad could not be found in the frame (the message says why)."""


# ---------------------------------------------------------------------------
# The result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PadFit:
    """
    The pad as a rectangle, tilted by at most a degree or two.

    All positions are layer px in the bound crop (see the module docstring).
    The rectangle's left and right sides are two parallel lines: at row y,
    the left side is at column  aL + m * (y - yc)  and the right side at
    aR + m * (y - yc).  Its top and bottom are horizontal rows.

    Attributes:
        aL, aR:       Column of the left and right side at row `yc`.
        m:            Slope of the sides: columns moved per row down (the
                      tilt; 0 means upright).
        yc:           The row aL and aR are given at: the mean of the rows
                      the sides were measured on.
        top, bot:     Row of the pad's top and bottom edge (fractional).
        top_source:   'measured' when the top is the steepest fall near one
                      pad height above the bottom (step 5; the normal case),
                      'height' when it was too faint and the top was placed
                      HEIGHT_FIXED above the bottom.
        shape:        (rows, columns) of the layer the fit was made on.
        strip_top:    First row of the cassette window (field above half
                      the pad level).  The pad mask starts MASK_MARGIN below.
        window_rows:  (first, last) row of the window (step 3).
        pad_level:    Median field over the window, the "pad level" whose
                      half marks an edge.  About 1-2 (pad brighter than the
                      gray chips).
        top_obs:      A record, not used by the fit: the row of the
                      steepest fall anywhere from the window's top to
                      TOP_SEARCH_GAP rows above the bottom, or NaN when it
                      was too faint.  Far from `top` means something on the
                      pad falls more steeply than its top edge.
        bot_slope:    Steepness of the bottom edge, field units per row.
        bot_source:   Always 'window' (step 4 placed the bottom).  Archives
                      before 2026-11-15 may hold 'height': the bottom was
                      then reseated one pad height below `top_obs`.
        side_rule:    'guided' (step 7; the normal case) or 'half-level'
                      (step 6 only).
        n_rows:       Rows used for the final side fit.
        n_rejected:   Rows dropped as outliers before the final fit.
        edge_rms:     Scatter of the measured side edges about the fitted
                      lines, layer px.
        offset:       (left, right, top, bottom) shift already added, layer
                      px; (0, 0, 0, 0) for a fit before the offset.
    """
    aL: float
    aR: float
    m: float
    yc: float
    top: float
    bot: float
    shape: Tuple[int, int]
    strip_top: float
    window_rows: Tuple[int, int]
    pad_level: float
    top_obs: float
    bot_slope: float
    bot_source: str
    side_rule: str
    n_rows: int
    n_rejected: int
    edge_rms: float
    offset: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    top_source: str = "height"

    # -- sizes ---------------------------------------------------------------
    @property
    def width(self) -> float:
        """Pad width across the strip, layer px."""
        return self.aR - self.aL

    @property
    def height(self) -> float:
        """Pad height down the strip, layer px."""
        return self.bot - self.top

    @property
    def angle_deg(self) -> float:
        """Tilt of the sides from the sensor's columns, degrees."""
        return math.degrees(math.atan(self.m))

    @property
    def top_residual(self) -> float:
        """Measured top minus placed top, layer px (NaN if not measured)."""
        return self.top_obs - self.top

    # -- the mask ------------------------------------------------------------
    def depth(self) -> np.ndarray:
        """
        Distance of every pixel to the nearest pad edge, positive inside.

        The pad is a rectangle, so this is the smallest of four distances:
        from the left side, the right side, the top and the bottom.

        Returns:
            Array of `shape`, layer px.
        """
        return np.minimum.reduce(list(self.edge_distances().values()))

    def edge_distances(self) -> "dict[str, np.ndarray]":
        """
        Every pixel's distance in from each of the four edges.

        The same frame as the desktop's `white_study.edge_distances`, so
        the bands it defines are the bands of the edge study: band k of an
        edge is the pixels with k <= distance < k + 1.

        Returns:
            {'L', 'R', 'T', 'B': array of `shape`}, layer px, positive
            inside the pad, negative outside.
        """
        rows, cols = self.shape
        yy, xx = np.mgrid[0:rows, 0:cols]
        # u: distance across the strip from the left side, measured along
        # the row; v: the row itself.  Same frame as desktop padrect.
        u = xx - (self.aL + self.m * (yy - self.yc))
        v = yy.astype(np.float64)
        return {"L": u, "R": self.width - u, "T": v - self.top,
                "B": self.bot - v}

    def mask(self, erode: float = 0.0) -> np.ndarray:
        """
        The pad pixels: at least `erode` px inside every edge, and in view.

        Rows above the window's top edge (plus MASK_MARGIN) are left out even
        if the rectangle reaches them, because there the pad is hidden by the
        cassette frame.

        Args:
            erode: Layer px to stay inside the edges.  0 keeps the whole pad,
                rim included.

        Returns:
            Boolean array of `shape`, True on the pad.

        Example:
            >>> pad_pixels = layer[fit.mask()]          # doctest: +SKIP
        """
        rows, _ = self.shape
        in_view = (np.arange(rows)[:, None]
                   >= self.strip_top + MASK_MARGIN)
        return (self.depth() > erode) & in_view

    def corners(self):
        """The four corners as (column, row), clockwise from the top left."""
        def at(u, v):
            return (self.aL + u + self.m * (v - self.yc), v)
        return [at(0, self.top), at(self.width, self.top),
                at(self.width, self.bot), at(0, self.bot)]

    # -- checks and summaries ----------------------------------------------
    def qc(self) -> str:
        """
        Check the fit against the QC_ limits.

        Returns:
            '' when the fit looks like a pad, else a short reason, e.g.
            'width 37.9 px (expected 41.35 +/- 2.0)'.
        """
        problems = []
        if abs(self.width - WIDTH_FIXED) > QC_WIDTH_TOLERANCE:
            problems.append(f"width {self.width:.1f} px (expected "
                            f"{WIDTH_FIXED} +/- {QC_WIDTH_TOLERANCE})")
        if abs(self.angle_deg) > QC_MAX_TILT_DEG:
            problems.append(f"tilt {self.angle_deg:+.2f} deg (limit "
                            f"{QC_MAX_TILT_DEG})")
        if self.edge_rms > QC_MAX_EDGE_RMS:
            problems.append(f"edge scatter {self.edge_rms:.2f} px (limit "
                            f"{QC_MAX_EDGE_RMS})")
        return "; ".join(problems)

    def shifted(self, offset: Sequence[float]) -> "PadFit":
        """
        A copy with its edges moved by `offset`, added to any offset already
        applied.

        Args:
            offset: (left, right, top, bottom) shifts, layer px, + meaning
                right or down.

        Returns:
            A new PadFit; this one is unchanged.
        """
        dL, dR, dT, dB = (float(v) for v in offset)
        total = tuple(a + b for a, b in zip(self.offset, (dL, dR, dT, dB)))
        return replace(self, aL=self.aL + dL, aR=self.aR + dR,
                       top=self.top + dT, bot=self.bot + dB, offset=total)

    def to_attrs(self) -> Dict[str, object]:
        """
        The fit as plain numbers and strings, for HDF5 attributes or JSON.

        Returns:
            {name: value}; every dataclass field, with tuples as lists.
            `from_attrs` rebuilds the PadFit.
        """
        out = {}
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            out[name] = list(value) if isinstance(value, tuple) else value
        return out

    @classmethod
    def from_attrs(cls, attrs: Dict[str, object]) -> "PadFit":
        """
        Rebuild a PadFit from `to_attrs` output.

        Extra keys are ignored.  A field missing from `attrs` takes its
        default if it has one (`top_source` is absent from archives written
        before 2026-11-15); a missing field without a default raises
        KeyError.
        """
        from dataclasses import MISSING
        kwargs = {}
        for name, spec in cls.__dataclass_fields__.items():
            if name not in attrs and spec.default is not MISSING:
                continue
            value = attrs[name]
            if isinstance(value, (list, np.ndarray)):
                value = tuple(v.item() if hasattr(v, "item") else v
                              for v in value)
            elif hasattr(value, "item"):              # numpy scalar
                value = value.item()
            if isinstance(value, bytes):
                value = value.decode()
            kwargs[name] = value
        return cls(**kwargs)

    def describe(self) -> str:
        """One line summarizing the fit, for logs."""
        tr = ("  -  " if math.isnan(self.top_residual)
              else f"{self.top_residual:+5.2f}")
        return (f"L={self.aL:6.2f} R={self.aR:6.2f} W={self.width:5.2f} | "
                f"top={self.top:6.2f} bot={self.bot:6.2f} | "
                f"tilt={self.angle_deg:+5.2f} deg rms={self.edge_rms:.3f} px "
                f"n={self.n_rows}-{self.n_rejected} top_resid={tr} "
                f"{self.side_rule}/{self.bot_source}/top {self.top_source}")


# ---------------------------------------------------------------------------
# Step 1: the field
# ---------------------------------------------------------------------------

def reflectance_field(layer: np.ndarray, dark_dn: float,
                      gray1: Rect, gray2: Rect) -> np.ndarray:
    """
    The photo_red R layer as a fraction of the gray chips' brightness.

    signal = layer - dark_dn, at every pixel.
    field  = signal / (average of the two chips' mean signal).
    A surface as bright as the chips reads 1.0, at any exposure_us.

    Args:
        layer:   The R Bayer layer of the bound crop, raw DN (0-1023).
        dark_dn: The sweep's dark level for this layer, DN (about 64).
        gray1, gray2: The gray chips, layer px in the bound crop.

    Returns:
        The field, float64, same shape as `layer`.

    Raises:
        PadFindError: a chip is empty or reads below MIN_CHIP_DN.

    Example:
        >>> f = reflectance_field(R, 64.0, g1, g2)       # doctest: +SKIP
    """
    signal = layer.astype(np.float64) - float(dark_dn)
    chip_means = []
    for chip in (gray1, gray2):
        pixels = signal[chip.y:chip.y + chip.h, chip.x:chip.x + chip.w]
        if pixels.size == 0:
            raise PadFindError(f"gray chip {chip.name or chip} has no pixels")
        chip_means.append(float(pixels.mean()))
    chip_level = 0.5 * (chip_means[0] + chip_means[1])
    if chip_level < MIN_CHIP_DN:
        raise PadFindError(f"gray chips too dark to use ({chip_level:.0f} DN "
                           f"above dark; need {MIN_CHIP_DN:.0f})")
    return signal / chip_level


# ---------------------------------------------------------------------------
# Small numeric helpers (identical to desktop padrect.py)
# ---------------------------------------------------------------------------

def _cross(v: np.ndarray, i: int, thr: float) -> float:
    """Fractional position where `v` crosses `thr` between i and i + 1."""
    a, b = float(v[i]), float(v[i + 1])
    if a == b:
        return i + 0.5
    return i + (thr - a) / (b - a)


def _peak(v: np.ndarray, i: int) -> float:
    """
    Fractional position of the peak (or dip) of `v` at index i, from a
    parabola through v[i-1], v[i], v[i+1].
    """
    a, b, c = float(v[i - 1]), float(v[i]), float(v[i + 1])
    d = a - 2.0 * b + c
    return i + (0.0 if d == 0 else 0.5 * (a - c) / d)


def _parallel_fit(rows, left, right, yc):
    """
    Least-squares fit of two parallel lines to the left and right edges.

    Solves for (m, aL, aR) in  left = aL + m (row - yc),
    right = aR + m (row - yc), all rows at once.

    Returns:
        ((m, aL, aR), residuals of left then right).
    """
    nr = len(rows)
    a = np.zeros((2 * nr, 3))
    a[:nr, 0] = rows - yc
    a[:nr, 1] = 1.0
    a[nr:, 0] = rows - yc
    a[nr:, 2] = 1.0
    obs = np.concatenate([left, right])
    sol, *_ = np.linalg.lstsq(a, obs, rcond=None)
    return sol, obs - a @ sol


# ---------------------------------------------------------------------------
# Steps 2-7: the fit
# ---------------------------------------------------------------------------

class _Fitter:
    """
    Working state for one fit.  Internal; `fit_pad` is the public entry.

    Attributes (set as the steps run):
        f:          The field (step 1), rows x columns.
        xseed:      The strip column: middle column of the sample ROI.
        rowprof:    Step 2 profile, one value per row.
        pad_level, window_rows, strip_top:            step 3.
        bot, bot_slope, bot_source, top, top_obs, top_source:  steps 4-5.
        aL, aR, m, yc, n_rows, n_rejected, edge_rms:  steps 6-7.
    """

    def __init__(self, field: np.ndarray, sample: Rect):
        self.f = np.asarray(field, dtype=np.float64)
        rows, cols = self.f.shape
        self.xseed = int(sample.x + sample.w // 2)
        if not (PROFILE_HALF_WIDTH <= self.xseed < cols - PROFILE_HALF_WIDTH):
            raise PadFindError(f"sample ROI column {self.xseed} is too close "
                               f"to the crop edge ({cols} columns)")
        x0 = self.xseed - PROFILE_HALF_WIDTH
        x1 = self.xseed + PROFILE_HALF_WIDTH + 1
        self.rowprof = np.median(self.f[:, x0:x1], axis=1)

    # -- step 3 --------------------------------------------------------------
    def find_window(self) -> None:
        p = self.rowprof
        bright_level = float(np.percentile(p, 95))
        floor = float(np.median(p[-FLOOR_ROWS:]))
        if floor >= EDGE_FRACTION * bright_level:
            # The strip runs off the bottom of the crop: the old open
            # channel, not a cassette.  Not supported on the Pi.
            raise PadFindError("no cassette window: the crop's bottom rows "
                               "are bright (open-channel holder?)")
        bright = np.flatnonzero(p > EDGE_FRACTION * bright_level)
        self.window_rows = (int(bright[0]), int(bright[-1]))
        self.pad_level = float(np.median(p[bright[0]:bright[-1] + 1]))

    # -- steps 4 and 5 ---------------------------------------------------------
    def find_ends(self) -> None:
        p = self.rowprof
        g = np.gradient(p)                    # change in the field per row
        thr = EDGE_FRACTION * self.pad_level
        self.strip_top = float(np.argmax(p > thr))

        # Step 4: the bottom is the steepest rise in the search rows.
        lo = int(self.strip_top) + BOTTOM_SEARCH_ROWS[0]
        hi = min(int(self.strip_top) + BOTTOM_SEARCH_ROWS[1], len(p) - 2)
        if hi - lo < 3:
            raise PadFindError("crop too short to hold the pad below the "
                               "window's top edge")
        ib = lo + int(np.argmax(g[lo:hi]))
        self.bot = _peak(g, ib)
        self.bot_slope = float(g[ib])
        self.bot_source = "window"

        # A record only (PadFit.top_obs): the steepest fall above the bottom.
        tlo = int(self.strip_top) + 1
        thi = max(tlo + 2, int(self.bot) - TOP_SEARCH_GAP)
        it = tlo + int(np.argmin(g[tlo:thi]))
        self.top_obs = (_peak(g, it) if -g[it] >= MIN_EDGE_SLOPE
                        else float("nan"))
        self.top = self.bot - HEIGHT_FIXED
        self._measure_top(g)

    def _measure_top(self, g: np.ndarray) -> None:
        """
        Step 5, the top: the steepest fall near one pad height above the
        bottom, where the backing strip gives way to the pad.

        The search covers whole rows within TOP_SEARCH_ROWS of the predicted
        row, and never reaches above the window's top (there the profile
        rises, from frame to backing).  The fall is refined to a fraction of
        a row as the bottom is, so both ends mean the same thing: the middle
        of the step between the last row of backing and the first of pad.
        A fall shallower than MIN_EDGE_SLOPE (field units per row) is not
        trusted, and the top stays where the height put it.

        Args:
            g: Change in the row profile per row (np.gradient of rowprof).
        """
        target = int(round(self.bot - HEIGHT_FIXED))
        lo = max(target - TOP_SEARCH_ROWS, int(self.strip_top) + 1)
        hi = min(target + TOP_SEARCH_ROWS + 1, len(g) - 1)
        self.top_source = "height"
        if hi - lo < 1:
            return
        it = lo + int(np.argmin(g[lo:hi]))
        if -g[it] >= MIN_EDGE_SLOPE:
            self.top = _peak(g, it)
            self.top_source = "measured"

    def _pad_rows(self) -> range:
        """Rows between the ends, END_CLEARANCE clear of each."""
        y0 = max(int(np.ceil(self.top)) + END_CLEARANCE, 0)
        y1 = min(int(np.floor(self.bot)) - END_CLEARANCE + 1, self.f.shape[0])
        return range(y0, y1)

    # -- step 6 ----------------------------------------------------------------
    def find_sides_half_level(self) -> None:
        """
        Sides from the outermost pixels brighter than half the pad level.

        On each pad row, look from SIDE_SEARCH px either side of the strip
        column inward; the first pixel above half the pad level is the
        edge, refined to a fraction of a pixel between it and its neighbor.
        """
        cols = self.f.shape[1]
        thr = EDGE_FRACTION * self.pad_level
        lo = max(self.xseed - SIDE_SEARCH, 1)
        hi = min(self.xseed + SIDE_SEARCH, cols - 2)
        rows, left, right = [], [], []
        for y in self._pad_rows():
            above = np.flatnonzero(self.f[y, lo:hi + 1] > thr) + lo
            if len(above) == 0:
                continue
            a, b = int(above[0]), int(above[-1])
            if a <= lo or b >= hi or b - a < MIN_PAD_RUN:
                continue                     # an edge ran into the limit
            rows.append(y)
            left.append(_cross(self.f[y], a - 1, thr))
            right.append(_cross(self.f[y], b, thr))
        if len(rows) < MIN_ROWS:
            raise PadFindError(f"only {len(rows)} pad rows showed both sides "
                               f"(need {MIN_ROWS})")
        self._fit_sides(rows, left, right)
        self.side_rule = "half-level"

    # -- step 7 ----------------------------------------------------------------
    def find_sides_guided(self, guide: "PadFit | _Fitter") -> None:
        """
        Sides at the steepest change within SIDE_SEARCH_PX of a guide's sides.

        For each pad row y, the guide's side is predicted at the same depth
        below its own top (the pad may have moved up or down), the steepest
        rise (left side: frame to pad) or fall (right side) of the field
        along the row is found within SIDE_SEARCH_PX of it, and refined to
        a fraction of a pixel.

        Args:
            guide: Anything with aL, aR, m, yc and top: this same fit after
                step 6 (a pre-image), or the pre-image's fit before its
                offset (a post-image).
        """
        cols = self.f.shape[1]
        gaL, gaR, gm, gyc = guide.aL, guide.aR, guide.m, guide.yc
        dtop = self.top - guide.top              # how far the pad moved down
        rows, left, right = [], [], []
        for y in self._pad_rows():
            g = np.gradient(self.f[y])           # change per column
            y_guide = y - dtop
            found = []
            # sign +1: left side (rising to the right); -1: right side.
            for x0, sign in ((gaL + gm * (y_guide - gyc), 1.0),
                             (gaR + gm * (y_guide - gyc), -1.0)):
                i0 = int(np.floor(x0 - SIDE_SEARCH_PX))
                i1 = int(np.ceil(x0 + SIDE_SEARCH_PX)) + 1
                if i0 < 1 or i1 > cols - 1:
                    break                        # search runs off the crop
                i = i0 + int(np.argmax(sign * g[i0:i1]))
                found.append(_peak(g, i))
            if len(found) == 2:
                rows.append(y)
                left.append(found[0])
                right.append(found[1])
        if len(rows) < MIN_ROWS:
            raise PadFindError(f"only {len(rows)} pad rows gave both guided "
                               f"sides (need {MIN_ROWS})")
        self._fit_sides(rows, left, right)
        self.side_rule = "guided"

    def _fit_sides(self, rows, left, right) -> None:
        """Fit both sides as parallel lines, dropping outlier rows."""
        rows = np.asarray(rows, dtype=np.float64)
        left, right = np.asarray(left), np.asarray(right)
        self.yc = float(rows.mean())
        keep = np.ones(len(rows), dtype=bool)
        # A mark on the paper reads as a dark run and pulls one edge in by
        # several px.  Drop such rows by their distance from the lines and
        # refit, rather than let them tilt the whole pad.
        for _ in range(REFIT_PASSES):
            sol, _ = _parallel_fit(rows[keep], left[keep], right[keep],
                                   self.yc)
            dist = np.abs(np.concatenate([
                left - (sol[1] + sol[0] * (rows - self.yc)),
                right - (sol[2] + sol[0] * (rows - self.yc))]))
            # 1.4826 x median distance = a standard deviation that a few
            # wild rows cannot inflate.  0.05 px floor: never divide by ~0.
            scale = max(1.4826 * float(np.median(dist[np.tile(keep, 2)])),
                        0.05)
            bad = (dist > OUTLIER_SIGMA * scale).reshape(2, -1).any(axis=0)
            if not (bad & keep).any() or (keep & ~bad).sum() < MIN_ROWS:
                break
            keep &= ~bad
        sol, resid = _parallel_fit(rows[keep], left[keep], right[keep],
                                   self.yc)
        self.m, self.aL, self.aR = (float(v) for v in sol)
        self.n_rows = int(keep.sum())
        self.n_rejected = int((~keep).sum())
        self.edge_rms = float(np.std(resid))

    def result(self) -> PadFit:
        return PadFit(aL=self.aL, aR=self.aR, m=self.m, yc=self.yc,
                      top=self.top, bot=self.bot,
                      shape=tuple(int(n) for n in self.f.shape),
                      strip_top=self.strip_top,
                      window_rows=self.window_rows,
                      pad_level=self.pad_level, top_obs=self.top_obs,
                      bot_slope=self.bot_slope, bot_source=self.bot_source,
                      side_rule=self.side_rule, n_rows=self.n_rows,
                      n_rejected=self.n_rejected, edge_rms=self.edge_rms,
                      top_source=self.top_source)


def fit_pad(field: np.ndarray, sample: Rect,
            prior: Optional[PadFit] = None) -> PadFit:
    """
    Fit the pad in a field (steps 2-7), with no offset applied.

    Without `prior` (a pre-image) the sides are found in two passes: half
    the pad level (step 6), then the steepest change near those sides
    (step 7).  With `prior` (a post-image) step 7 is guided by the
    pre-image's sides directly.

    Args:
        field:  From `reflectance_field`, rows x columns.
        sample: The sample ROI, layer px in the bound crop; only its middle
            column is used, as the strip column.
        prior:  The same file's pre-image fit *before* its offset
            (`find_pad`'s first result), or None.

    Returns:
        The PadFit, offset (0, 0, 0, 0).

    Raises:
        PadFindError: the window, an edge or the sides could not be found.

    Example:
        >>> fit = fit_pad(reflectance_field(R, 64.0, g1, g2), sample)  # doctest: +SKIP
    """
    work = _Fitter(field, sample)
    work.find_window()
    work.find_ends()
    if prior is None:
        work.find_sides_half_level()
        guide = work.result()            # a frozen copy of the first pass
        work.find_sides_guided(guide)
    else:
        work.find_sides_guided(prior)
    return work.result()


def find_pad(layer: np.ndarray, dark_dn: float, rects: Dict[str, Rect],
             section: str, prior: Optional[PadFit] = None,
             offset: Optional[Tuple[float, float, float, float]] = None
             ) -> Tuple[PadFit, PadFit]:
    """
    Find the pad in one photo_red frame: the whole job, steps 1-7 + offset.

    Args:
        layer:   The photo_red frame's R Bayer layer, bound crop, raw DN.
        dark_dn: The dark level for the R layer, DN (about 64).
        rects:   Layer-px rectangles in the bound crop, keyed 'sample',
                 'gray1' and 'gray2'.
        section: SECTION_PRE or SECTION_POST; chooses the offset.
        prior:   For a post-image, the pre-image's fit before its offset
                 (the first item this function returned for the pre-image).
        offset:  (left, right, top, bottom) shift, layer px, to apply; None
                 uses OFFSET_PRE or OFFSET_POST for `section`.  The capture
                 passes the config rows capture.padfind_offset_pre/_post,
                 whose factory values are those constants.

    Returns:
        (before, after): the fit before the offset (keep it: a post-image is
        guided by it) and after the offset (use it: it is where the desktop
        analysis would put the pad).

    Raises:
        PadFindError: the pad could not be found.
        KeyError:     a needed rectangle is missing from `rects`.
        ValueError:   `section` is not 'pre' or 'post'.

    Example:
        >>> before, pad = find_pad(R, 64.0, rects, "pre")   # doctest: +SKIP
        >>> pad_pixels = R[pad.mask()]                        # doctest: +SKIP
    """
    if section not in (SECTION_PRE, SECTION_POST):
        raise ValueError(f"section must be 'pre' or 'post', got {section!r}")
    if offset is None:
        offset = OFFSET_PRE if section == SECTION_PRE else OFFSET_POST
    offset = tuple(float(v) for v in offset)
    field = reflectance_field(layer, dark_dn, rects["gray1"], rects["gray2"])
    before = fit_pad(field, rects["sample"], prior)
    return before, before.shifted(offset)
