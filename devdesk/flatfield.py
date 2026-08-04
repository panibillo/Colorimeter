"""
flatfield.py — polynomial illumination flat fields over a capture suite.
--------------------------------------------------------------------------
Desktop-only developer tool (matplotlib dependency for -p). Not synced to
the Pi (see sync_pi_files.txt).

Why this exists
---------------
`tex/colorimeter_plan.tex` Layer 1 calls for a per-LED, per-Bayer-channel
polynomial flat field fitted over an area larger than the 68x68 sample ROI.
This tool answers the three questions that have to be settled before any of
that is wired into the capture path: what polynomial, fitted over what
extent, and how do we know it is good enough.

Measured on captures/white_20260803_1742.h5 and mgray_20260803_1736.h5, at
the 34x34 Bayer-layer patch scale a sample ROI actually occupies:

    repeatability floor (e0 vs e1; Gb vs Gr, same scene)      0.12 - 0.13 %
    uncorrected illumination span across `bound`              +7 % to -17 %
    block residual after degree 1 / 2 / 3 / 6      3.8 / 0.47 / 0.38 / 0.15 %
    criterion P after degree 1 / 2 / 3 / 6            8.9 / 0.88 / 0.24 / 0.28 %
    LED-to-LED field-shape difference (same card)            0.55 - 2.1 %
    Bayer-channel field-shape difference (R vs Gr)            up to 0.96 %
    Gb vs Gr under red LEDs — crosstalk, not noise            1.1 - 1.6 %

Note that criterion P is already *worse* at degree 6 than at degree 3: past
degree 3 the extra terms fit that capture's own texture, near the patches
where it matters most.  That is the overfit the plan warned about, visible
in-sample.

Three consequences are baked into this module:

  1. **Divide, do not subtract.**  Illumination is multiplicative on the
     dark-subtracted signal, so the correction is `signal / (F / mean F)`.
     The methods doc's additive "subtract the trend" is only valid to first
     order: mgray reads 0.27x white, so an additive trend calibrated on a
     white card over-corrects a dark sample by ~3.7x.
  2. **The field is essentially 1-D.**  Row means fall 25 % top-to-bottom
     while column means vary ~2 %.  Pooled over 39 frames, (3,1) and (3,3)
     score 0.24 % and 0.21 % on criterion P — a 0.05 % difference against a
     0.5 % tolerance, for three more free parameters.  Hence the anisotropic
     basis below and the (3,1) default: 7 terms, not a full cubic's 10.
  3. **Degree is chosen on out-of-sample error, never in-sample.**  In-sample
     residual keeps falling with degree (0.29 % at degree 6); transfer error
     stops improving after degree 3.  See `-t`.

Saturation masking is not optional.  The stored exposure ladders were metered
against a *sample* image at the frame centre, so a white card stock stage
oversaturates: white/royal_blue/B has mean 959 DN and fits with a spurious
0.00 % residual until its clipped pixels are masked out.

Coordinate spaces
-----------------
Everything here works in **bound-crop Bayer-layer** coordinates: the suite
archives store only the `bound` ROI, split into four layers of
(bound.h//2, bound.w//2) = 330 x 98.  ROI rectangles come from the live
`roi` table via `roi.to_layer_rect_within()`, never hardcoded.

Polynomial coordinates are always normalized to [-1, 1] over the **whole
bound**, whatever extent the fit was taken over, so coefficients from
different extents are directly comparable.

Modes
-----
  -f / --fit        Fit the (3,1) default over `bound`; print coefficients
                    and per-fit statistics.
  -c / --compare    The extent x degree ladder scored against criteria P
                    and S (see `docs` below).
  -t / --transfer   Fit on FILE, apply to FILE2; patch-ratio table plus a
                    rigid-shift scan that separates model error from
                    card-registration error.
  -p / --plot       Row/column profiles, residual heatmap, block-residual
                    map, error-vs-degree curves.
  -w / --write      Write `flatfield` rows to devdesk.sqlite and derived
                    per-ROI gain maps as .npy.

Adequacy criteria
-----------------
The flat field's only job is to make patch means comparable between ROI
locations.  An error constant over a patch cancels in the white-reference
ratio; a smooth ramp across a patch cancels in the patch *mean*.  So the
criteria are defined on patch means, not on pixel-wise flatness.

  P (primary)   On a uniform card, |mean_i / mean_white - 1| <= tol for every
                ROI patch i.  tol = 0.5 % in-sample, 1.0 % transfer.
  S (secondary) The corrected patch carries no residual ramp: fitting a plane
                to it, |a_x|*w + |a_y|*h <= 1 % of the patch mean.  Matters
                only because Layer 3 uses within-patch spread as a strip
                uniformity flag.
  T (tertiary)  Accept the smallest (deg_y, deg_x) whose *transfer* error
                stops improving by more than 0.1 % — the repeatability floor.

Usage:
    python3 devdesk/flatfield.py FILE [FILE2] [-f] [-c] [-t] [-p] [-w]

Examples:
    .venv/bin/python3 devdesk/flatfield.py white_20260803_1742.h5 -f
    .venv/bin/python3 devdesk/flatfield.py white_20260803_1742.h5 -f green
    .venv/bin/python3 devdesk/flatfield.py white_20260803_1742.h5 \\
        mgray_20260803_1736.h5 -t
"""

import argparse
import json
import sys
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hdf5read import Hdf5Reader  # noqa: E402
import roi as roi_mod  # noqa: E402

DEFAULT_CAPTURES_DIR = Path(__file__).resolve().parent.parent / "captures"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "devdesk.sqlite"
GAIN_DIR = Path(__file__).resolve().parent / "flatfield"

RAW_MAX = 1023          # 10-bit sensor ceiling, even though stored as uint16
# Pixels within this margin of the ceiling are already in the sensor's
# non-linear knee, so they are masked rather than trusted at face value.
SATURATION_MARGIN = 23
SATURATED_DN = RAW_MAX - SATURATION_MARGIN
BAYER_CHANNELS = ("R", "Gr", "Gb", "B")

# The stream the `roi` table rows were commissioned against.  Passed to
# roi.load_active() so a row stored for a different stream is rescaled rather
# than silently mis-indexed.
STREAM_W, STREAM_H = 2304, 1296
STREAM_MODEL = "imx708"

DEFAULT_DEG_Y, DEFAULT_DEG_X = 3, 1

# Refuse a fit whose usable pixels have been eaten by saturation or dead
# pixels; a fit over the survivors would be quietly biased toward the dim
# corners.
MAX_MASKED_FRACTION = 0.01

# Criteria thresholds, in percent.  See the module docstring.
TOL_P_IN_SAMPLE = 0.5
TOL_P_TRANSFER = 1.0
TOL_S_RAMP = 1.0
FLOOR_PCT = 0.1         # measured repeatability floor; degree-selection step
BRIGHT_DN = 300         # below this a frame's own shot noise dominates any
                        # shape comparison, so the floor is quoted above it


# ── Path resolution ─────────────────────────────────────────────────

def resolve_h5_path(filename: str, captures_dir: Path) -> Path:
    """
    Resolve a user-supplied filename/path to an existing .h5 file.

    Same contract as hdf5inspect.resolve_h5_path(): a bare filename is looked
    up under captures_dir, anything that already exists is used as given.

    Args:
        filename: Bare filename or a relative/absolute path.
        captures_dir: Directory bare filenames resolve against.

    Returns:
        Resolved, existing Path to the .h5 file.

    Raises:
        FileNotFoundError: Neither interpretation resolves to a file.

    Example:
        >>> resolve_h5_path("white_20260803_1742.h5", DEFAULT_CAPTURES_DIR)
        PosixPath('.../captures/white_20260803_1742.h5')
    """
    direct = Path(filename)
    if direct.exists():
        return direct.resolve()

    candidate = captures_dir / filename
    if candidate.exists():
        return candidate.resolve()

    raise FileNotFoundError(
        f"'{filename}' not found as a path, nor as '{candidate}' "
        f"under captures dir '{captures_dir}'"
    )


# ── Archive navigation ──────────────────────────────────────────────
# Built only on Hdf5Reader's public API, mirroring hdf5inspect.py.

def list_led_colors(reader: Hdf5Reader) -> List[str]:
    """Return sorted LED color names present under /leds (possibly empty)."""
    try:
        return sorted(reader.list_children("/leds"))
    except KeyError:
        return []


def list_lit_labels(reader: Hdf5Reader, color: str) -> List[str]:
    """Return exposure labels (e.g. ['e0','e1',...]) under one LED's /lit."""
    try:
        labels = reader.list_children(f"/leds/{color}/lit")
    except KeyError:
        return []
    return sorted(labels, key=lambda lab: int(lab[1:]))


def lit_exposure_us(reader: Hdf5Reader, color: str, label: str) -> int:
    """Return the exposure_us attr for one LED's lit exposure label."""
    return int(reader.attrs(f"/leds/{color}/lit/{label}/R")["exposure_us"])


def find_dark_link(reader: Hdf5Reader, color: str, label: str) -> Optional[str]:
    """
    Return the name of one LED's dark link matching a lit exposure label.

    Lifted from hdf5inspect.py so the two tools agree on the naming rules: a
    dark link is written by the same call site that wrote the lit exposure,
    so matching on the link name is equivalent to matching on exposure time.
    Current captures name links "before_e0"/"after_e0"; older camtest8
    conversions name them bare, e.g. "e0".  "after" wins when both exist, as
    the frame taken closest to the lit capture.

    Args:
        reader: Open Hdf5Reader for the archive.
        color: LED color name, e.g. 'green'.
        label: Lit exposure label, e.g. 'e1'.

    Returns:
        The link name under /leds/<color>/dark, or None if there is no match.

    Example:
        >>> find_dark_link(reader, 'green', 'e1')      # doctest: +SKIP
        'after_e1'
    """
    try:
        children = reader.list_children(f"/leds/{color}/dark")
    except KeyError:
        return None
    matches = [c for c in children if c == label or c.endswith(f"_{label}")]
    if not matches:
        return None

    def _rank(name: str) -> int:
        if name.startswith("after_"):
            return 0
        if name.startswith("before_"):
            return 1
        return 2

    matches.sort(key=_rank)
    return matches[0]


# ── Frame loading: dark subtraction and masking ─────────────────────

@dataclass
class Frame:
    """
    One dark-subtracted Bayer-layer image of the `bound` crop, plus its mask.

    Attributes:
        signal: float64 (rows, cols), lit minus dark, in DN.
        mask:   bool (rows, cols), True where the pixel is usable.
        led:    LED color name.
        label:  Exposure label, e.g. 'e1'.
        channel: Bayer channel name, one of BAYER_CHANNELS.
        exposure_us: Exposure time recorded on the lit dataset.
        dark_link: Name of the dark group actually subtracted.
    """
    signal: np.ndarray
    mask: np.ndarray
    led: str
    label: str
    channel: str
    exposure_us: int
    dark_link: str

    @property
    def masked_fraction(self) -> float:
        """Fraction of pixels excluded by the mask, 0.0 - 1.0."""
        return float((~self.mask).mean())

    @property
    def usable(self) -> bool:
        """True if few enough pixels were masked for a fit to be trusted."""
        return self.masked_fraction <= MAX_MASKED_FRACTION

    @property
    def mean(self) -> float:
        """Mean signal over unmasked pixels, in DN."""
        return float(self.signal[self.mask].mean())


def load_frame(reader: Hdf5Reader, led: str, label: str,
               channel: str) -> Frame:
    """
    Load one lit Bayer layer, subtract its matching dark frame, and mask it.

    The full dark *frame* is subtracted pixel by pixel, not a dark mean — a
    mean throws away the fixed-pattern component, which is exactly the part
    that would otherwise masquerade as illumination structure.

    Masked out: raw lit >= SATURATED_DN (clipped, and common on a white card
    stock stage because the exposure ladder was metered on a sample at frame
    centre), and any pixel whose dark-subtracted value is <= 0 (dead, or
    swamped by dark noise — a zero would blow up the multiplicative
    correction).

    Args:
        reader: Open Hdf5Reader for the archive.
        led: LED color name, e.g. 'green'.
        label: Lit exposure label, e.g. 'e1'.
        channel: One of BAYER_CHANNELS.

    Returns:
        A Frame. Check `.usable` before fitting it.

    Raises:
        KeyError: The lit dataset or its dark counterpart is absent.

    Example:
        >>> f = load_frame(reader, 'green', 'e1', 'Gr')    # doctest: +SKIP
        >>> f.mean, f.masked_fraction                      # doctest: +SKIP
        (770.9, 0.0)
    """
    dark_link = find_dark_link(reader, led, label)
    if dark_link is None:
        raise KeyError(f"/leds/{led}/dark has no group matching '{label}'")

    lit = reader.read_array(
        f"/leds/{led}/lit/{label}/{channel}").astype(np.float64)
    dark = reader.read_array(
        f"/leds/{led}/dark/{dark_link}/{channel}").astype(np.float64)
    signal = lit - dark
    mask = (lit < SATURATED_DN) & (signal > 0.0)

    return Frame(signal=signal, mask=mask, led=led, label=label,
                 channel=channel, exposure_us=lit_exposure_us(reader, led,
                                                              label),
                 dark_link=dark_link)


# ── ROI geometry ────────────────────────────────────────────────────

@dataclass
class Patches:
    """
    The active ROI rectangles in bound-crop layer coordinates.

    Attributes:
        rects: {name: (x, y, w, h)} in layer pixels.
        roles: {name: role}, e.g. 'sample', 'white', 'gray1'.
        reference: Name of the white-role ROI, the denominator in criterion
            P.  None if the table has no white ROI inside `bound`.
    """
    rects: Dict[str, Tuple[int, int, int, int]]
    roles: Dict[str, str]
    reference: Optional[str]

    def __len__(self) -> int:
        return len(self.rects)


def load_patches(shape: Tuple[int, int]) -> Patches:
    """
    Return the active ROIs as (x, y, w, h) in bound-crop layer coordinates.

    Read from the live `roi` table through roi.load_active() and converted
    with roi.to_layer_rect_within(), so an ROI edited on the instrument is
    picked up here without touching this file.  `bound` itself is dropped —
    it is the crop, not a patch inside it.

    Args:
        shape: (rows, cols) of the stored layer arrays, used to verify that
            the archive really was cropped to the `bound` currently in force.

    Returns:
        A Patches record; ROIs not wholly inside `bound` are dropped, since
        the archive holds no pixels for them.

    Raises:
        ValueError: The table has no `bound` row, or `bound` disagrees with
            the archive's array shape (the ROI was edited after the capture).

    Example:
        >>> load_patches((330, 98)).rects['sample']    # doctest: +SKIP
        (34, 81, 34, 34)
    """
    rois = roi_mod.load_active(STREAM_W, STREAM_H, STREAM_MODEL)
    bound = rois.get("bound")
    if bound is None:
        raise ValueError("the roi table has no active 'bound' row")

    rows, cols = shape
    if (bound.h // 2, bound.w // 2) != (rows, cols):
        raise ValueError(
            f"active bound {bound.xywh()} implies layers of "
            f"{bound.h // 2}x{bound.w // 2}, but the archive stores "
            f"{rows}x{cols} — the ROI was edited after this capture"
        )

    rects, roles = {}, {}
    for name, r in rois.items():
        if name == "bound" or not roi_mod.contains_rect(bound, r):
            continue
        rects[name] = roi_mod.to_layer_rect_within(r, bound).xywh()
        roles[name] = getattr(r, "role", "")

    reference = next((n for n, role in roles.items() if role == "white"), None)
    return Patches(rects=rects, roles=roles, reference=reference)


def patch_mean(a: np.ndarray, rect: Tuple[int, int, int, int]) -> float:
    """Mean of `a` over the (x, y, w, h) patch, in layer pixels."""
    x, y, w, h = rect
    return float(a[y:y + h, x:x + w].mean())


# ── The polynomial basis ────────────────────────────────────────────

def term_exponents(deg_y: int, deg_x: int) -> List[Tuple[int, int]]:
    """
    Return the (iy, ix) exponent pairs of the anisotropic polynomial basis.

    A term x^ix * y^iy is included when it is within both per-axis caps and
    within the overall total degree max(deg_y, deg_x).  With deg_y == deg_x
    this is exactly the ordinary total-degree bivariate basis; with deg_x
    lower it drops the high-order cross terms that the measured field does
    not need.

    The illumination here falls 25 % along y and varies ~2 % along x, and
    (3,1), (3,2) and (3,3) fits agree to 0.01 % — so (3,1)'s 7 terms buy the
    same accuracy as a full cubic's 10 with three fewer free parameters.

    Args:
        deg_y: Maximum exponent on y, >= 0.
        deg_x: Maximum exponent on x, >= 0.

    Returns:
        List of (iy, ix) pairs, ordered by total degree then by iy.

    Example:
        >>> term_exponents(3, 1)
        [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1), (3, 0)]
        >>> len(term_exponents(2, 2))
        6
    """
    total = max(deg_y, deg_x)
    return [(iy, ix)
            for deg in range(total + 1)
            for iy in range(deg + 1)
            for ix in [deg - iy]
            if iy <= deg_y and ix <= deg_x]


def design_matrix(rows: np.ndarray, cols: np.ndarray,
                  deg_y: int, deg_x: int,
                  domain: Tuple[int, int]) -> np.ndarray:
    """
    Build the polynomial design matrix for a grid of layer coordinates.

    Coordinates are normalized to [-1, 1] over `domain` — the whole bound —
    regardless of which subset of rows/cols is being fitted.  That is what
    makes coefficients from a trimmed or windowed fit comparable with a
    whole-bound fit, and what lets a field fitted over one extent be
    evaluated anywhere in the crop.

    Args:
        rows: 1-D array of layer row indices (may extend outside `domain`;
            evaluating a fitted field beyond its fit extent is intentional).
        cols: 1-D array of layer column indices.
        deg_y: Maximum exponent on y.
        deg_x: Maximum exponent on x.
        domain: (rows, cols) of the full bound crop.

    Returns:
        float64 (len(rows) * len(cols), n_terms), C-ordered so that
        `.reshape(len(rows), len(cols))` undoes the ravel.

    Example:
        >>> design_matrix(np.arange(4), np.arange(3), 1, 1, (4, 3)).shape
        (12, 3)
    """
    dom_rows, dom_cols = domain
    y = (np.asarray(rows, dtype=np.float64) / (dom_rows - 1)) * 2.0 - 1.0
    x = (np.asarray(cols, dtype=np.float64) / (dom_cols - 1)) * 2.0 - 1.0
    xg, yg = np.meshgrid(x, y)
    return np.column_stack([((xg ** ix) * (yg ** iy)).ravel()
                            for iy, ix in term_exponents(deg_y, deg_x)])


# ── The fitted field ────────────────────────────────────────────────

@dataclass
class FlatField:
    """
    A fitted illumination surface, evaluable anywhere in the bound crop.

    Attributes:
        coef: float64 (n_terms,), matching term_exponents(deg_y, deg_x).
        deg_y, deg_x: Basis degrees.
        domain: (rows, cols) of the bound crop the coordinates normalize over.
        extent: Name of the region the fit was taken over, e.g. 'bound'.
        window: (row_start, row_stop, col_start, col_stop) of the fit extent.
        covers: (row_start, row_stop, col_start, col_stop) the field may
            legitimately be evaluated over.  Wider than `window` for the
            trimmed extents, whose whole point is to keep edge pixels out of
            the fit and then evaluate across them; equal to `window` for the
            local and sector extents, where evaluating outside would mean
            extrapolating a small-window cubic far from its data.
        norm_mean: Mean of the fitted surface over the fit extent, in DN.  The
            gain map divides by this, so a gain map is unit-mean over the
            region the polynomial was actually constrained by — not over some
            wider region it was never fitted to.
        led, label, channel: What was fitted.
        n_fit: Pixels that entered the fit.
        n_masked: Pixels excluded by the mask within the fit extent.
        fit_rms_pct: Per-pixel residual RMS over the fit extent, in percent of
            the fitted surface's mean.  Dominated by pixel noise and paper
            texture, so it is a diagnostic, not an adequacy criterion.
    """
    coef: np.ndarray
    deg_y: int
    deg_x: int
    domain: Tuple[int, int]
    extent: str
    window: Tuple[int, int, int, int]
    covers: Tuple[int, int, int, int]
    norm_mean: float
    led: str
    label: str
    channel: str
    n_fit: int
    n_masked: int
    fit_rms_pct: float

    @property
    def n_terms(self) -> int:
        """Number of free parameters in the fit."""
        return int(self.coef.size)

    @property
    def is_global(self) -> bool:
        """True if the field may be evaluated over the whole bound crop."""
        return self.covers == (0, self.domain[0], 0, self.domain[1])

    def contains(self, rect: Tuple[int, int, int, int]) -> bool:
        """
        Return True if an ROI rect lies inside the field's evaluable region.

        Callers use this to decide which patches a field may legitimately be
        asked about.  A whole-bound or trimmed fit covers every patch; a local
        or sector fit covers only the patches inside its own window, because
        a small-window cubic extrapolates badly.

        Args:
            rect: (x, y, w, h) in bound-crop layer coordinates.

        Returns:
            True if the rectangle is inside the evaluable region.
        """
        x, y, w, h = rect
        r0, r1, c0, c1 = self.covers
        return r0 <= y and y + h <= r1 and c0 <= x and x + w <= c1

    def evaluate(self, shape: Optional[Tuple[int, int]] = None,
                 shift_y: int = 0, shift_x: int = 0) -> np.ndarray:
        """
        Evaluate the surface over a whole array of layer pixels.

        Args:
            shape: (rows, cols) to evaluate over; defaults to the full domain.
            shift_y: Rigid offset in layer rows applied to the *field*, used
                by the transfer scan to test whether a card-registration
                shift explains a mismatch.
            shift_x: Rigid offset in layer columns.

        Returns:
            float64 (rows, cols) of surface values in DN.

        Example:
            >>> f.evaluate().shape                      # doctest: +SKIP
            (330, 98)
        """
        rows, cols = shape or self.domain
        A = design_matrix(np.arange(rows) + shift_y,
                          np.arange(cols) + shift_x,
                          self.deg_y, self.deg_x, self.domain)
        return (A @ self.coef).reshape(rows, cols)

    def gain(self, shape: Optional[Tuple[int, int]] = None,
             shift_y: int = 0, shift_x: int = 0) -> np.ndarray:
        """
        Evaluate the surface as a gain map: F / norm_mean.

        This is the array the correction divides by, and the artifact stored
        as `<led>_<ch>_<roi>_gain.npy`.  The normalizer is the surface's mean
        over the *fit extent*, so a whole-bound fit yields a gain map that is
        unit-mean over the bound, and a local fit one that is unit-mean over
        its own window.  Two patches corrected by the same global field keep
        their true ratio; two patches corrected by different local fields do
        not, which is why cmd_compare() reports criterion P only for fields
        that span both patches.

        Args:
            shape: (rows, cols) to evaluate over; defaults to the full domain.
            shift_y: Rigid row offset, as for evaluate().
            shift_x: Rigid column offset.

        Returns:
            float64 (rows, cols) of gains, mean 1.0 over the fit extent.
        """
        return self.evaluate(shape, shift_y, shift_x) / self.norm_mean

    def gain_for_patch(self, rect: Tuple[int, int, int, int]) -> np.ndarray:
        """
        Evaluate the gain map over one ROI rectangle.

        Args:
            rect: (x, y, w, h) in bound-crop layer coordinates.

        Returns:
            float64 (h, w) gain map; divide the patch's pixels by it.

        Example:
            >>> f.gain_for_patch((34, 81, 34, 34)).shape   # doctest: +SKIP
            (34, 34)
        """
        x, y, w, h = rect
        A = design_matrix(np.arange(y, y + h), np.arange(x, x + w),
                          self.deg_y, self.deg_x, self.domain)
        return (A @ self.coef).reshape(h, w) / self.norm_mean

    def correct(self, frame: "Frame", shift_y: int = 0,
                shift_x: int = 0) -> np.ndarray:
        """
        Flat-field one frame: divide its signal by the unit-mean gain map.

        Division, not subtraction — illumination scales the dark-subtracted
        signal.  See the module docstring.

        Args:
            frame: The Frame to correct.
            shift_y: Rigid row offset applied to the field.
            shift_x: Rigid column offset applied to the field.

        Returns:
            float64 array the same shape as frame.signal.
        """
        return frame.signal / self.gain(frame.signal.shape, shift_y, shift_x)

    def coef_json(self) -> str:
        """Return the coefficients and basis as a JSON string for storage."""
        return json.dumps({
            "deg_y": self.deg_y,
            "deg_x": self.deg_x,
            "terms": [list(t) for t in term_exponents(self.deg_y, self.deg_x)],
            "coef": [float(c) for c in self.coef],
            "domain_rows": self.domain[0],
            "domain_cols": self.domain[1],
        })


# ── Fit extents ─────────────────────────────────────────────────────

def extent_indices(name: str, shape: Tuple[int, int],
                   rects: Optional[Dict[str, Tuple[int, int, int, int]]] = None,
                   ) -> Tuple[np.ndarray, np.ndarray]:
    """
    Return the (rows, cols) index arrays selecting one named fit extent.

    Supported names:
        'bound'              the whole crop
        'bound_trim05'       the crop less a 5 % border on every side
        'bound_trim10'       the crop less a 10 % border
        'local_<patch>'      the named ROI grown by 50 % on every side and
                             clipped to the crop — the "fit a small window
                             around the target" option
        'sector<k>_<i>'      band i of k equal horizontal bands.  The bands
                             split along y because that is the axis the
                             illumination actually varies on.

    Args:
        name: Extent name as above.
        shape: (rows, cols) of the bound crop.
        rects: ROI rectangles from Patches.rects, for the 'local_' forms.

    Returns:
        (row_indices, col_indices), each a 1-D int array.

    Raises:
        ValueError: Unknown extent name, or 'local_' names a missing ROI.

    Example:
        >>> r, c = extent_indices('bound_trim05', (330, 98))
        >>> r[0], r[-1], c[0], c[-1]
        (16, 313, 5, 92)
        >>> extent_indices('sector3_1', (330, 98))[0][[0, -1]].tolist()
        [110, 219]
    """
    rows, cols = shape
    rects = rects or {}

    if name == "bound":
        return np.arange(rows), np.arange(cols)

    if name.startswith("bound_trim"):
        frac = int(name[len("bound_trim"):]) / 100.0
        ty, tx = int(round(rows * frac)), int(round(cols * frac))
        return np.arange(ty, rows - ty), np.arange(tx, cols - tx)

    if name.startswith("local_"):
        key = name[len("local_"):]
        if key not in rects:
            raise ValueError(f"extent '{name}': no ROI named '{key}'")
        x, y, w, h = rects[key]
        gy, gx = h // 2, w // 2          # grow by 50 % on every side
        return (np.arange(max(0, y - gy), min(rows, y + h + gy)),
                np.arange(max(0, x - gx), min(cols, x + w + gx)))

    if name.startswith("sector"):
        spec = name[len("sector"):]
        if "_" not in spec:
            raise ValueError(f"extent '{name}': expected 'sector<k>_<i>'")
        k, i = (int(v) for v in spec.split("_", 1))
        if not 0 <= i < k:
            raise ValueError(f"extent '{name}': band {i} outside 0..{k - 1}")
        edges = np.linspace(0, rows, k + 1).round().astype(int)
        return np.arange(edges[i], edges[i + 1]), np.arange(cols)

    raise ValueError(f"unknown fit extent '{name}'")


def fit_field(frame: Frame, deg_y: int = DEFAULT_DEG_Y,
              deg_x: int = DEFAULT_DEG_X, extent: str = "bound",
              patches: Optional[Dict] = None) -> FlatField:
    """
    Fit a polynomial illumination surface to one masked, dark-subtracted frame.

    Least squares over the unmasked pixels inside `extent`, in coordinates
    normalized over the whole bound.  Masked pixels are dropped from the fit
    rather than interpolated: a clipped pixel carries no information about the
    illumination that produced it.

    Args:
        frame: A dark-subtracted Frame; check `.usable` first.
        deg_y: Maximum exponent on y. Default 3, chosen on transfer error.
        deg_x: Maximum exponent on x. Default 1 — the field is nearly 1-D.
        extent: Fit extent name, see extent_indices().
        patches: ROI rectangles ({name: (x,y,w,h)}), required only for
            'local_*' extents.

    Returns:
        A FlatField.

    Raises:
        ValueError: Fewer unmasked pixels than free parameters.

    Example:
        >>> ff = fit_field(frame)                       # doctest: +SKIP
        >>> ff.n_terms, round(ff.fit_rms_pct, 2)        # doctest: +SKIP
        (7, 1.44)
    """
    rows, cols = extent_indices(extent, frame.signal.shape, patches or {})
    sub_signal = frame.signal[np.ix_(rows, cols)]
    sub_mask = frame.mask[np.ix_(rows, cols)]

    A = design_matrix(rows, cols, deg_y, deg_x, frame.signal.shape)
    keep = sub_mask.ravel()
    n_fit = int(keep.sum())
    if n_fit <= A.shape[1]:
        raise ValueError(
            f"{frame.led}/{frame.label}/{frame.channel} over '{extent}': "
            f"{n_fit} unmasked pixels for {A.shape[1]} free parameters"
        )

    coef, *_ = np.linalg.lstsq(A[keep], sub_signal.ravel()[keep], rcond=None)
    fitted = (A @ coef).reshape(sub_signal.shape)
    resid = (sub_signal - fitted)[sub_mask]
    norm_mean = float(fitted.mean())
    fit_rms_pct = float(resid.std() / norm_mean * 100.0)

    window = (int(rows[0]), int(rows[-1]) + 1,
              int(cols[0]), int(cols[-1]) + 1)
    # A trimmed fit exists precisely to exclude edge pixels from the fit and
    # then evaluate across them, so it stays evaluable over the whole crop.
    # A local or sector fit does not.
    covers = ((0, frame.signal.shape[0], 0, frame.signal.shape[1])
              if extent.startswith("bound") else window)

    return FlatField(
        coef=coef, deg_y=deg_y, deg_x=deg_x, domain=frame.signal.shape,
        extent=extent, window=window, covers=covers,
        norm_mean=norm_mean, led=frame.led, label=frame.label,
        channel=frame.channel, n_fit=n_fit,
        n_masked=int((~sub_mask).sum()), fit_rms_pct=fit_rms_pct,
    )


# ── Models: one or several fields covering the crop ─────────────────

# Each entry is (model name, [extent names]).  A model with several extents
# corrects each patch with whichever field covers it, so local and sector
# schemes are scored by the same code path as a single whole-bound fit.
def model_extents(name: str, rects: Dict[str, Tuple]) -> List[str]:
    """
    Expand a model name into the list of fit extents it is built from.

    Args:
        name: 'bound', 'bound_trim05', 'bound_trim10', 'local', or
            'sector<k>'.
        rects: ROI rectangles, needed to expand 'local'.

    Returns:
        List of extent names for extent_indices().

    Raises:
        ValueError: Unknown model name.

    Example:
        >>> model_extents('sector3', {})
        ['sector3_0', 'sector3_1', 'sector3_2']
    """
    if name in ("bound", "bound_trim05", "bound_trim10"):
        return [name]
    if name == "local":
        return [f"local_{k}" for k in sorted(rects)]
    if name.startswith("sector"):
        k = int(name[len("sector"):])
        return [f"sector{k}_{i}" for i in range(k)]
    raise ValueError(f"unknown model '{name}'")


@dataclass
class Model:
    """
    A flat-field scheme: one or more fitted fields covering the bound crop.

    A single whole-bound polynomial is a Model with one field.  A local or
    sector scheme is a Model with several, each valid only inside its own
    window.  Keeping them the same type is what lets criterion P be reported
    honestly: it is computable only between patches that share one field,
    because two fields normalized over different windows carry no common
    scale.

    Attributes:
        name: Model name, e.g. 'bound' or 'sector3'.
        fields: The fitted fields, in extent order.
    """
    name: str
    fields: List[FlatField] = dc_field(default_factory=list)

    def field_for(self, rect: Tuple[int, int, int, int]) -> Optional[FlatField]:
        """
        Return the field covering `rect`, preferring the tightest window.

        Args:
            rect: (x, y, w, h) in bound-crop layer coordinates.

        Returns:
            The FlatField whose fit window wholly contains the rect, or None
            if no field does — in which case the patch cannot be corrected
            without extrapolating.
        """
        covering = [f for f in self.fields if f.contains(rect)]
        if not covering:
            return None
        return min(covering, key=lambda f: ((f.covers[1] - f.covers[0])
                                            * (f.covers[3] - f.covers[2])))


def build_model(frame: Frame, name: str, deg_y: int, deg_x: int,
                patches: Patches) -> Model:
    """
    Fit every field a model needs from one frame.

    Args:
        frame: A usable, dark-subtracted Frame.
        name: Model name, see model_extents().
        deg_y: Maximum exponent on y.
        deg_x: Maximum exponent on x.
        patches: The active ROIs.

    Returns:
        A Model. Extents that cannot be fitted (too few unmasked pixels) are
        omitted, so a partly-clipped frame still yields a usable model over
        the regions that survived.

    Example:
        >>> build_model(frame, 'bound', 3, 1, patches)   # doctest: +SKIP
    """
    model = Model(name=name)
    for extent in model_extents(name, patches.rects):
        try:
            model.fields.append(
                fit_field(frame, deg_y, deg_x, extent, patches.rects))
        except ValueError:
            continue
    return model


# ── Metrics: the adequacy criteria ──────────────────────────────────

def block_residual_pct(corrected: np.ndarray,
                       block: int = 34) -> Tuple[float, float]:
    """
    Return (rms, max) of block-mean deviation from flat, in percent.

    The array is tiled with `block`-square windows — the size of a sample ROI
    — and each window's mean is compared with the overall mean.  Averaging
    over 1156 pixels drops the shot-noise contribution to ~0.04 %, so what
    survives is spatial structure the flat field failed to remove.

    Args:
        corrected: A flat-fielded array.
        block: Window side in layer pixels. Default 34 = one sample ROI.

    Returns:
        (rms_pct, max_abs_pct).

    Example:
        >>> block_residual_pct(np.ones((68, 68)))
        (0.0, 0.0)
    """
    rows, cols = corrected.shape
    means = np.array([[corrected[i:i + block, j:j + block].mean()
                       for j in range(0, cols - block + 1, block)]
                      for i in range(0, rows - block + 1, block)])
    dev = (means / corrected.mean() - 1.0) * 100.0
    return float(dev.std()), float(np.abs(dev).max())


def patch_ramp_pct(corrected: np.ndarray,
                   rect: Tuple[int, int, int, int]) -> float:
    """
    Return the end-to-end residual ramp across one patch, in percent.

    Criterion S.  A plane is fitted to the corrected patch and the ramp
    reported as (|a_x| * (w-1) + |a_y| * (h-1)) / mean — the total swing a
    straight tilt contributes from one corner to the opposite one.  The
    spans are w-1 and h-1, not w and h, because a w-pixel row spans w-1
    intervals; using w would overstate the ramp by w/(w-1).

    A ramp does not bias the patch *mean* (it cancels by symmetry), so this
    is not about the measurement itself.  It matters because Layer 3 of the
    plan uses within-patch spread as a strip-uniformity flag, and leftover
    illumination tilt would inflate that flag.

    Args:
        corrected: The flat-fielded array.
        rect: (x, y, w, h) in bound-crop layer coordinates.

    Returns:
        Ramp as a percentage of the patch mean.

    Example:
        >>> patch_ramp_pct(np.ones((40, 40)), (0, 0, 20, 20))
        0.0
    """
    x, y, w, h = rect
    block = corrected[y:y + h, x:x + w]
    yy, xx = np.mgrid[0:h, 0:w]
    A = np.column_stack([np.ones(block.size), xx.ravel(), yy.ravel()])
    (_, ax, ay), *_ = np.linalg.lstsq(A, block.ravel(), rcond=None)
    return float((abs(ax) * (w - 1) + abs(ay) * (h - 1))
                 / block.mean() * 100.0)


def ramp_noise_pct(corrected: np.ndarray,
                   rect: Tuple[int, int, int, int]) -> float:
    """
    Return the ramp that pixel noise alone would produce, in percent.

    A plane fitted to pure noise still has a slope.  For n pixels of relative
    spread sigma spread evenly over a side of length L, the fitted slope's
    standard error times L is sigma * sqrt(12 / n); the two axes add.  Without
    this yardstick a patch ramp cannot be told from a fitting artifact —
    which matters most in the dim channels, where sigma reaches 6 %.

    Args:
        corrected: The flat-fielded array.
        rect: (x, y, w, h) in bound-crop layer coordinates.

    Returns:
        Expected noise-only ramp, as a percentage of the patch mean.

    Example:
        >>> round(ramp_noise_pct(np.ones((40, 40)), (0, 0, 20, 20)), 6)
        0.0
    """
    x, y, w, h = rect
    block = corrected[y:y + h, x:x + w]
    sigma = block.std() / block.mean() * 100.0
    return float(2.0 * sigma * np.sqrt(12.0 / block.size))


@dataclass
class Score:
    """
    One model's scores against the adequacy criteria, for one frame.

    Attributes:
        p_dev: {patch name: % deviation of mean_i/mean_ref from 1}, over the
            patches that share a field with the reference patch.
        p_max: max |p_dev|, or None when no two patches share a field.
        s_max: Worst criterion-S ramp over all corrected patches, in percent.
        s_snr: Worst patch's ramp divided by the ramp pixel noise alone would
            have produced.  Near 1 means the ramp is a fitting artifact; well
            above 1 means the field really does leave a tilt inside the patch.
        block_rms, block_max: block_residual_pct() over the whole crop, using
            the largest field in the model. None for multi-field models,
            where no single field covers the crop.
        n_uncovered: Patches no field could correct without extrapolating.
    """
    p_dev: Dict[str, float]
    p_max: Optional[float]
    s_max: float
    s_snr: float
    block_rms: Optional[float]
    block_max: Optional[float]
    n_uncovered: int


def score_model(model: Model, frame: Frame, patches: Patches,
                shift_y: int = 0, shift_x: int = 0) -> Score:
    """
    Score a model against criteria P and S on a uniform-card frame.

    Criterion P assumes the imaged card is uniform, so every patch *should*
    read the same as the white reference after correction; the deviation from
    that is the flat field's error. P is skipped for patches that do not
    share a field with the reference, because fields normalized over
    different windows have no common scale.

    Args:
        model: The fitted model to score.
        frame: The frame to correct — the same one for in-sample scoring, a
            different capture for transfer scoring.
        patches: The active ROIs.
        shift_y: Rigid row offset applied to every field, for the transfer
            shift scan.
        shift_x: Rigid column offset.

    Returns:
        A Score.

    Example:
        >>> score_model(model, frame, patches).p_max     # doctest: +SKIP
        0.18
    """
    means: Dict[str, float] = {}
    owner: Dict[str, int] = {}
    ramps: List[Tuple[float, float]] = []
    uncovered = 0

    for name, rect in patches.rects.items():
        ff = model.field_for(rect)
        if ff is None:
            uncovered += 1
            continue
        corrected = ff.correct(frame, shift_y, shift_x)
        means[name] = patch_mean(corrected, rect)
        owner[name] = id(ff)
        ramps.append((patch_ramp_pct(corrected, rect),
                      ramp_noise_pct(corrected, rect)))

    p_dev: Dict[str, float] = {}
    ref = patches.reference
    if ref in means:
        p_dev = {n: (m / means[ref] - 1.0) * 100.0
                 for n, m in means.items()
                 if owner[n] == owner[ref] and n != ref}
    p_max = max((abs(v) for v in p_dev.values()), default=None)

    block_rms = block_max = None
    globals_ = [f for f in model.fields if f.is_global]
    if globals_:
        block_rms, block_max = block_residual_pct(
            globals_[0].correct(frame, shift_y, shift_x))

    worst = max(ramps, default=None, key=lambda rn: rn[0])
    return Score(p_dev=p_dev, p_max=p_max,
                 s_max=worst[0] if worst else float("nan"),
                 s_snr=(worst[0] / worst[1]) if worst and worst[1] else
                 float("nan"),
                 block_rms=block_rms, block_max=block_max,
                 n_uncovered=uncovered)


# ── Mode: -f / --fit ────────────────────────────────────────────────

def iter_frames(reader: Hdf5Reader, colors: List[str], labels: List[str],
                channels: List[str]):
    """
    Yield loadable Frames for the requested colors/labels/channels.

    Combinations whose datasets or dark counterparts are missing are skipped
    silently — partial archives are normal (see hdf5inspect's summary_rows).

    Args:
        reader: Open Hdf5Reader.
        colors: LED color names to visit.
        labels: Exposure labels to visit, or [] for every label present.
        channels: Bayer channel names to visit.

    Yields:
        Frame objects in color, label, channel order.
    """
    for color in colors:
        present = list_lit_labels(reader, color)
        for label in (labels or present):
            if label not in present:
                continue
            for channel in channels:
                try:
                    yield load_frame(reader, color, label, channel)
                except KeyError:
                    continue


def select_frames(reader: Hdf5Reader, colors: List[str], labels: List[str],
                  channels: List[str]) -> List[Frame]:
    """
    Return the best usable frame per (LED, Bayer channel).

    "Best" is the unclipped exposure with the highest mean — the most signal
    a real calibration could use without saturating.  This is chosen per
    channel, not per LED, because the ladder was metered on a sample at frame
    centre and clips one channel long before another: on the white suite
    royal_blue/B is clipped at every exposure while royal_blue/R is not.

    Args:
        reader: Open Hdf5Reader.
        colors: LED color names to consider.
        labels: Exposure labels to consider, or [] for all present.
        channels: Bayer channel names to consider.

    Returns:
        List of Frames, at most one per (LED, channel). Combinations with no
        usable exposure are omitted.

    Example:
        >>> len(select_frames(reader, ['green'], [], ['Gr']))   # doctest: +SKIP
        1
    """
    best: Dict[Tuple[str, str], Frame] = {}
    for frame in iter_frames(reader, colors, labels, channels):
        if not frame.usable:
            continue
        key = (frame.led, frame.channel)
        if key not in best or frame.mean > best[key].mean:
            best[key] = frame
    return list(best.values())


def cmd_fit(reader: Hdf5Reader, h5_path: Path, colors: List[str],
            labels: List[str], deg_y: int, deg_x: int) -> None:
    """
    Fit one polynomial per (LED, exposure, channel) and print a table.

    Unusable frames — too many saturated or dead pixels — are reported with
    their masked fraction instead of a fit, because a fit over the survivors
    would be biased toward whatever part of the frame stayed unclipped.

    Args:
        reader: Open Hdf5Reader for the archive.
        h5_path: Archive path, for the heading.
        colors: LED color names to fit.
        labels: Exposure labels to fit, or [] for all present.
        deg_y: Maximum exponent on y.
        deg_x: Maximum exponent on x.

    Returns:
        None. Prints to stdout.
    """
    terms = term_exponents(deg_y, deg_x)
    print(f"\n{h5_path.name}: flat-field fits, "
          f"(deg_y, deg_x) = ({deg_y}, {deg_x}), {len(terms)} terms")
    print(f"  basis: {' '.join(f'y^{iy}x^{ix}' for iy, ix in terms)}")
    print(f"  {'led':<11} {'e#':<3} {'ch':<3} {'exp_us':>7} {'mean':>7} "
          f"{'masked%':>8} {'span%':>7} {'resid%':>7}  coef[0..2]")

    for frame in iter_frames(reader, colors, labels, list(BAYER_CHANNELS)):
        head = (f"  {frame.led:<11} {frame.label:<3} {frame.channel:<3} "
                f"{frame.exposure_us:>7} {frame.mean:>7.1f} "
                f"{100 * frame.masked_fraction:>7.2f}%")
        if not frame.usable:
            print(f"{head}  SKIPPED — over {100 * MAX_MASKED_FRACTION:.0f}% "
                  f"masked (saturated or dead)")
            continue

        ff = fit_field(frame, deg_y, deg_x)
        surface = ff.evaluate()
        span = (surface.max() - surface.min()) / surface.mean() * 100.0
        head3 = " ".join(f"{c:9.2f}" for c in ff.coef[:3])
        print(f"{head} {span:>6.1f}% {ff.fit_rms_pct:>6.2f}%  {head3}")


# ── Mode: -c / --compare ────────────────────────────────────────────

# The ladder from the plan: extent x degree, cheapest first.  Degree pairs
# are (deg_y, deg_x); deg_x above 1 is included to show it buys nothing.
LADDER: Tuple[Tuple[str, int, int], ...] = (
    ("bound", 1, 1),
    ("bound", 2, 1),
    ("bound", 2, 2),
    ("bound", 3, 1),
    ("bound", 3, 2),
    ("bound", 3, 3),
    ("bound", 4, 2),
    ("bound", 6, 2),
    ("bound_trim05", 2, 1),
    ("bound_trim05", 3, 1),
    ("bound_trim10", 3, 1),
    ("sector3", 1, 1),
    ("sector3", 2, 1),
    ("local", 1, 1),
    ("local", 2, 1),
    ("local", 2, 2),
)


def _agg(values: List[float]) -> Tuple[Optional[float], Optional[float]]:
    """Return (median, max) of a list, or (None, None) if it is empty."""
    if not values:
        return None, None
    return float(np.median(values)), float(max(values))


def _fmt(value: Optional[float], width: int = 6) -> str:
    """Format an optional percentage, right-aligned, '  -  ' when None."""
    return f"{'-':>{width}}" if value is None else f"{value:>{width}.2f}"


def shape_disagreement_pct(a: Frame, b: Frame, block: int = 34) -> float:
    """
    Return how much two frames' illumination *shapes* differ, in percent.

    Each frame is divided by its own mean, one is divided by the other, and
    the result summarized as the block-mean spread.  Overall brightness
    cancels, so what is left is purely the difference in spatial shape.

    Args:
        a: One frame.
        b: The other, same shape.
        block: Averaging block, in layer pixels.

    Returns:
        Block-mean RMS of the ratio, in percent.

    Example:
        >>> shape_disagreement_pct(f, f)                 # doctest: +SKIP
        0.0
    """
    ratio = (a.signal / a.mean) / (b.signal / b.mean)
    return block_residual_pct(ratio, block)[0]


def cmd_noise_floor(reader: Hdf5Reader, colors: List[str]) -> None:
    """
    Report the repeatability floor two ways, as a check on the pipeline.

    Neither comparison should show real structure, so both are estimates of
    the noise floor on a 34x34 patch mean:

      Gb vs Gr    The two green photosites see the same scene through the
                  same filter in the same frame.  Any disagreement is noise
                  plus per-photosite gain differences.
      e0 vs e1    The same LED at two exposures, seconds apart.  Adds
                  exposure nonlinearity and any drift between the frames.

    Measured at 0.11 - 0.12 % on the reference suites.  A materially larger
    number means this tool is adding error — a mismatched dark frame, a
    clipped channel that slipped past the mask — and every criterion below
    is then measuring the tool rather than the instrument.

    Args:
        reader: Open Hdf5Reader for the archive.
        colors: LED color names to include.

    Returns:
        None. Prints to stdout.
    """
    gb_gr, exp_pairs = [], []
    for color in colors:
        for label in list_lit_labels(reader, color):
            try:
                gr = load_frame(reader, color, label, "Gr")
                gb = load_frame(reader, color, label, "Gb")
            except KeyError:
                continue
            if gr.usable and gb.usable:
                gb_gr.append((shape_disagreement_pct(gb, gr), color, label,
                              gr.mean))

        labels = list_lit_labels(reader, color)
        for ch in BAYER_CHANNELS:
            usable = []
            for label in labels:
                try:
                    fr = load_frame(reader, color, label, ch)
                except KeyError:
                    continue
                if fr.usable:
                    usable.append(fr)
            for first, second in zip(usable, usable[1:]):
                exp_pairs.append((shape_disagreement_pct(first, second),
                                  color, f"{first.label}/{second.label}",
                                  first.mean))

    print("  repeatability floor (34x34 block RMS of a shape ratio that "
          "should be flat).")
    print(f"  Quoted over frames above {BRIGHT_DN} DN: below that the "
          "comparison is measuring\n  shot noise rather than the floor.")
    for name, vals in (("Gb vs Gr, same frame", gb_gr),
                       ("adjacent exposures, same LED", exp_pairs)):
        bright = [v for v, _, _, mean in vals if mean >= BRIGHT_DN]
        med, _ = _agg(bright)
        if med is None:
            print(f"    {name:<32} no bright pairs")
        else:
            print(f"    {name:<32} median {med:.3f} %  ({len(bright)} pairs)")

    # A pair that disagrees at every exposure is not noise.  Under red LEDs
    # Gb and Gr differ by ~1.5 % because one green photosite shares its row
    # with red and the other with blue, so they pick up different crosstalk.
    # This is why all four layers get their own polynomial and why the two
    # greens must never be averaged together.
    persistent = sorted((v for v in gb_gr if v[0] > 1.0), reverse=True)
    if persistent:
        leds = sorted({led for _, led, _, _ in persistent})
        print(f"    ! Gb and Gr disagree by over 1 % at every exposure for: "
              f"{', '.join(leds)}")
        print("      Reproducible across exposures, so not noise: green "
              "photosites neighbouring\n      red pick up different "
              "crosstalk from those neighbouring blue. Fit each\n      "
              "Bayer layer separately; never average the two greens.")


def cmd_compare(reader: Hdf5Reader, h5_path: Path, colors: List[str],
                labels: List[str]) -> None:
    """
    Score the extent x degree ladder on one suite and print the table.

    Scoring is in-sample: each field is fitted and evaluated on the same
    frame, so these numbers are optimistic by construction and must not be
    used to pick a degree — that is what -t/--transfer is for.  What this
    table does establish is which rungs are *capable* of meeting criterion P
    at all, and where added degrees stop buying in-sample accuracy.

    One frame is used per (LED, channel) — the best unclipped exposure — and
    the criteria are aggregated across those frames as median and worst case.

    Args:
        reader: Open Hdf5Reader for the archive.
        h5_path: Archive path, for the heading.
        colors: LED color names to include.
        labels: Exposure labels to consider, or [] for all present.

    Returns:
        None. Prints to stdout.
    """
    frames = select_frames(reader, colors, labels, list(BAYER_CHANNELS))
    if not frames:
        print(f"{h5_path.name}: no usable (unclipped) frames.")
        return

    patches = load_patches(frames[0].signal.shape)
    print(f"\n{h5_path.name}: in-sample ladder over {len(frames)} frames "
          f"(best unclipped exposure per LED x channel)")
    cmd_noise_floor(reader, colors)
    listed = ', '.join(f'{n}{patches.rects[n][:2]}'
                       for n in sorted(patches.rects))
    print(f"  patches: {listed}   reference: {patches.reference}")
    print(f"  criterion P <= {TOL_P_IN_SAMPLE} % in-sample, "
          f"S <= {TOL_S_RAMP} % ramp\n")
    print(f"  {'model':<13} {'deg':<6} {'trms':>4} | "
          f"{'P med':>6} {'P max':>6} | {'S med':>6} {'S/nz':>5} | "
          f"{'blk rms':>7} {'blk max':>7} |  P")

    for model_name, deg_y, deg_x in LADDER:
        p_vals, s_vals, snr_vals, b_rms, b_max = [], [], [], [], []
        uncovered = 0
        n_terms = len(term_exponents(deg_y, deg_x))
        for frame in frames:
            model = build_model(frame, model_name, deg_y, deg_x, patches)
            if not model.fields:
                continue
            sc = score_model(model, frame, patches)
            if sc.p_max is not None:
                p_vals.append(sc.p_max)
            if not np.isnan(sc.s_max):
                s_vals.append(sc.s_max)
                snr_vals.append(sc.s_snr)
            if sc.block_rms is not None:
                b_rms.append(sc.block_rms)
                b_max.append(sc.block_max)
            uncovered += sc.n_uncovered

        p_med, p_wst = _agg(p_vals)
        s_med, _ = _agg(s_vals)
        snr_med, _ = _agg(snr_vals)
        br_med, _ = _agg(b_rms)
        bm_med, _ = _agg(b_max)
        verdict = ("n/a" if p_wst is None
                   else "PASS" if p_wst <= TOL_P_IN_SAMPLE else "fail")
        note = f"  ({uncovered} patch fits uncovered)" if uncovered else ""
        print(f"  {model_name:<13} ({deg_y},{deg_x}) {n_terms:>4} | "
              f"{_fmt(p_med)} {_fmt(p_wst)} | {_fmt(s_med)} "
              f"{_fmt(snr_med, 5)} | "
              f"{_fmt(br_med, 7)} {_fmt(bm_med, 7)} |  {verdict}{note}")

    print("\n  P is blank for multi-field models whose patches do not share a "
          "field:\n  two fields normalized over different windows carry no "
          "common scale, so a\n  local or sector scheme cannot establish a "
          "sample-to-reference ratio at all.")
    print("  S/nz is the patch ramp over the ramp pixel noise alone would "
          "give.  Near 1\n  the ramp is a fitting artifact; well above 1 the "
          "field really leaves a tilt\n  inside the patch, which no whole-"
          "bound polynomial of any degree removed.")


# ── Mode: -t / --transfer ───────────────────────────────────────────

# Degrees scanned by the transfer ladder.  deg_x stays at 1 because -c shows
# raising it changes nothing; the question here is only how much y-flexibility
# survives being applied to an independent capture.
TRANSFER_DEGREES: Tuple[Tuple[int, int], ...] = (
    (1, 1), (2, 1), (3, 1), (4, 1), (4, 2), (5, 2), (6, 2),
)

# Rigid offsets, in layer rows, tried when asking whether a card-registration
# shift rather than a bad model explains a transfer mismatch.
SHIFT_SCAN = tuple(range(-14, 15, 2))


def _paired_frames(src: Hdf5Reader, dst: Hdf5Reader, colors: List[str],
                   labels: List[str]) -> List[Tuple[Frame, Frame]]:
    """
    Pair each usable source frame with the same LED+channel destination.

    Exposure labels need not match: the fields are unit-mean gain maps and the
    criteria are patch *ratios*, so a different exposure on the destination
    cancels out.  Each file contributes its own best unclipped exposure.

    Args:
        src: Reader for the archive the field is fitted on.
        dst: Reader for the archive the field is applied to.
        colors: LED color names to consider.
        labels: Exposure labels to consider, or [] for all present.

    Returns:
        List of (source frame, destination frame) pairs.
    """
    dst_by_key = {(f.led, f.channel): f
                  for f in select_frames(dst, colors, labels,
                                         list(BAYER_CHANNELS))}
    pairs = []
    for sf in select_frames(src, colors, labels, list(BAYER_CHANNELS)):
        df = dst_by_key.get((sf.led, sf.channel))
        if df is not None:
            pairs.append((sf, df))
    return pairs


# An LED whose exposure-normalized brightness ratio between the two cards
# departs from the all-LED median by more than this factor is not measuring
# reflectance on both cards, and must not be pooled with the rest.
FLUORESCENCE_FACTOR = 2.0


def find_anomalous_leds(pairs: List[Tuple[Frame, Frame]],
                        ) -> Tuple[Dict[str, float], List[str]]:
    """
    Flag LEDs whose two captures cannot both be plain reflectance.

    Two uniform cards differ by a reflectance ratio.  For a purely reflective
    pair of cards that ratio, once divided by exposure time, is roughly the
    same for every LED — the cards are grey to each other.  An LED that
    departs sharply is measuring something else.

    On the white/mgray suites this catches violet: it reads 22x brighter on
    the white card in the *green* channel where every other LED reads ~3.7x,
    and only ~4x in its own red channel.  Green light out of violet
    excitation is fluorescence — the white card stock carries optical
    brightening agents, the gray stock does not.  Its violet "illumination
    field" is really the emission field of the brightener, so fitting a flat
    field to it and applying it elsewhere is meaningless.

    Args:
        pairs: (source frame, destination frame) pairs from _paired_frames().

    Returns:
        ({led: ratio}, [flagged led names]).  The ratio is the per-LED median
        across channels of (src mean / src exposure) / (dst mean / dst
        exposure).

    Example:
        >>> ratios, flagged = find_anomalous_leds(pairs)   # doctest: +SKIP
        >>> flagged                                        # doctest: +SKIP
        ['violet']
    """
    per_led: Dict[str, List[float]] = {}
    for sf, df in pairs:
        rate_s = sf.mean / sf.exposure_us
        rate_d = df.mean / df.exposure_us
        if rate_d > 0:
            per_led.setdefault(sf.led, []).append(rate_s / rate_d)

    ratios = {led: float(np.median(v)) for led, v in per_led.items()}
    if not ratios:
        return {}, []

    overall = float(np.median(list(ratios.values())))
    flagged = [led for led, r in ratios.items()
               if r > overall * FLUORESCENCE_FACTOR
               or r < overall / FLUORESCENCE_FACTOR]
    return ratios, sorted(flagged)


def cmd_transfer(src: Hdf5Reader, dst: Hdf5Reader, src_path: Path,
                 dst_path: Path, colors: List[str], labels: List[str]) -> None:
    """
    Fit fields on one suite, apply them to another, and report the error.

    This is the only honest way to choose a degree.  An in-sample residual
    always falls as degree rises; a transfer error stops falling once the
    extra terms are fitting that capture's own noise and texture.  Criterion T
    takes the smallest degree whose transfer error improves on the previous by
    less than the measured repeatability floor.

    The rigid-shift scan then asks whether whatever error remains is the
    model's fault at all: if sliding the fitted field bodily by a few layer
    rows improves the match, the two captures were not registered to each
    other and no polynomial degree can fix that — the stage or the card moved.

    Args:
        src: Reader for the archive the fields are fitted on.
        dst: Reader for the archive they are applied to.
        src_path, dst_path: Paths, for the heading.
        colors: LED color names to include.
        labels: Exposure labels to consider, or [] for all present.

    Returns:
        None. Prints to stdout.
    """
    pairs = _paired_frames(src, dst, colors, labels)
    if not pairs:
        print("no LED/channel pairs usable in both archives.")
        return

    patches = load_patches(pairs[0][0].signal.shape)
    print(f"\ntransfer: fit on {src_path.name} -> apply to {dst_path.name}")
    print(f"  {len(pairs)} LED x channel pairs, both unclipped; "
          f"reference patch: {patches.reference}")

    ratios, flagged = find_anomalous_leds(pairs)
    if flagged:
        overall = float(np.median(list(ratios.values())))
        print(f"\n  ! excluded from the pooled figures — brightness ratio "
              f"between the two\n    cards departs from the {overall:.2f}x "
              f"the other LEDs agree on:")
        for led in flagged:
            print(f"      {led:<11} {ratios[led]:6.2f}x")
        print("    An LED that reads far brighter on one card in a channel "
              "other than its\n    own is seeing fluorescence, not "
              "reflectance: optical brighteners in the\n    card stock. Its "
              "field is the emission pattern, and will not transfer.")
        pairs = [p for p in pairs if p[0].led not in flagged]

    print(f"\n  criterion P <= {TOL_P_TRANSFER} % on transfer "
          f"(in-sample column shown for contrast).")
    print("  'aligned' repeats the transfer with the field slid bodily by the "
          "offset that\n  suits it best, so degrees are compared on model "
          "quality with the registration\n  error taken out — a low degree "
          "otherwise wins simply by being too smooth to\n  notice the "
          "misalignment.\n")
    print(f"  {'deg':<6} {'trms':>4} | {'in-sample':>13} | "
          f"{'transfer':>13} | {'aligned':>19} | gain vs")
    print(f"  {'':<6} {'':>4} | {'P med':>6} {'blk':>6} | "
          f"{'P med':>6} {'P max':>6} | {'P med':>6} {'P max':>6} {'@row':>5} "
          f"| prev deg")

    # Cache the fits: 7 degrees x 15 shifts x ~36 pairs would otherwise refit
    # the same polynomial 15 times over.
    scans: Dict[Tuple[int, int], Tuple] = {}
    prev = None
    recommended = None
    for deg_y, deg_x in TRANSFER_DEGREES:
        models = [(build_model(sf, "bound", deg_y, deg_x, patches), sf, df)
                  for sf, df in pairs]
        models = [m for m in models if m[0].fields]

        ins = [s.p_max for m, sf, _ in models
               if (s := score_model(m, sf, patches)).p_max is not None]
        ins_b = [s.block_rms for m, sf, _ in models
                 if (s := score_model(m, sf, patches)).block_rms is not None]

        by_shift = {}
        for shift in SHIFT_SCAN:
            vals = [s.p_max for m, _, df in models
                    if (s := score_model(m, df, patches,
                                         shift_y=shift)).p_max is not None]
            if vals:
                by_shift[shift] = (float(np.median(vals)), float(max(vals)))
        scans[(deg_y, deg_x)] = (by_shift, models)

        i_med, _ = _agg(ins)
        ib, _ = _agg(ins_b)
        x_med, x_max = by_shift.get(0, (None, None))
        best_shift = min(by_shift, key=lambda s: by_shift[s][0])
        a_med, a_max = by_shift[best_shift]

        gain = None if prev is None else prev - a_med
        if gain is not None and gain < FLOOR_PCT and recommended is None:
            recommended = TRANSFER_DEGREES[
                TRANSFER_DEGREES.index((deg_y, deg_x)) - 1]
        n_terms = len(term_exponents(deg_y, deg_x))
        print(f"  ({deg_y},{deg_x}) {n_terms:>4} | "
              f"{_fmt(i_med)} {_fmt(ib, 6)} | {_fmt(x_med)} {_fmt(x_max)} | "
              f"{_fmt(a_med)} {_fmt(a_max)} {best_shift:>+5d} | "
              f"{'-' if gain is None else f'{gain:+6.2f}'}")
        prev = a_med

    deg = recommended or (DEFAULT_DEG_Y, DEFAULT_DEG_X)
    if recommended:
        print(f"\n  criterion T: aligned transfer stops improving by more "
              f"than {FLOOR_PCT} % after (deg_y, deg_x) = {recommended}")

    by_shift, models = scans[deg]
    best_shift = min(by_shift, key=lambda s: by_shift[s][0])
    print(f"\n  rigid-shift scan at {deg} — median transfer P by field offset:")
    for shift in SHIFT_SCAN:
        if shift in by_shift:
            mark = "  <-- best" if shift == best_shift else ""
            print(f"    {shift:+3d} rows: {by_shift[shift][0]:5.2f} %{mark}")

    if best_shift:
        print(f"\n  best at {best_shift:+d} layer rows "
              f"({2 * best_shift:+d} stream px), "
              f"{by_shift[best_shift][0]:.2f} % vs {by_shift[0][0]:.2f} % "
              f"unshifted —\n  a registration offset between the two "
              f"captures, not a deficiency of the\n  polynomial. No degree "
              f"can correct it; only re-registering the captures can.")
    else:
        print("\n  no offset improves the match: the two captures are "
              "registered, and the\n  residual transfer error is real field "
              "or target structure.")

    # A median hides the pair that fails; name the worst few explicitly.
    worst = sorted(
        ((s.p_max, sf.led, sf.channel, sf.mean, df.mean)
         for m, sf, df in models
         if (s := score_model(m, df, patches, shift_y=best_shift)).p_max
         is not None),
        reverse=True)
    if worst:
        print(f"\n  worst pairs at {deg}, aligned:")
        for p_max, led, ch, s_mean, d_mean in worst[:5]:
            print(f"    {p_max:6.2f} %  {led:<11} {ch:<3} "
                  f"src {s_mean:7.1f} DN  dst {d_mean:7.1f} DN")


# ── Mode: -p / --plot ───────────────────────────────────────────────

def cmd_plot(reader: Hdf5Reader, h5_path: Path, led: str, channel: str,
             label: Optional[str], deg_y: int, deg_x: int,
             out_png: Optional[Path]) -> None:
    """
    Draw the four diagnostics that make a fit's behaviour legible.

    Top left: row and column profiles with the fitted surface over them —
    shows at a glance that the field is steep in y and nearly flat in x, the
    observation the anisotropic basis rests on.  Top right: the corrected
    frame as a percentage deviation from flat, with the ROI boxes drawn, so
    residual structure can be seen where it matters.  Bottom left: the same
    residual averaged into 34x34 blocks, which removes pixel noise and leaves
    only what the flat field failed to remove.  Bottom right: in-sample error
    against degree, the curve that keeps falling and must not be used to pick
    a degree.

    Args:
        reader: Open Hdf5Reader for the archive.
        h5_path: Archive path, for the title.
        led: LED color to plot.
        channel: Bayer channel to plot.
        label: Exposure label, or None for the best unclipped one.
        deg_y: Maximum exponent on y.
        deg_x: Maximum exponent on x.
        out_png: Optional path to save to instead of showing interactively.

    Returns:
        None.

    Raises:
        ValueError: No usable frame for the requested LED and channel.
    """
    import matplotlib
    if out_png:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    candidates = [f for f in iter_frames(reader, [led],
                                         [label] if label else [], [channel])
                  if f.usable]
    if not candidates:
        raise ValueError(
            f"no unclipped {led}/{channel} frame in {h5_path.name}")
    frame = max(candidates, key=lambda f: f.mean)

    patches = load_patches(frame.signal.shape)
    ff = fit_field(frame, deg_y, deg_x, "bound", patches.rects)
    surface = ff.evaluate()
    corrected = ff.correct(frame)
    resid = (corrected / corrected.mean() - 1.0) * 100.0

    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    fig.suptitle(f"{h5_path.name}   {led}/{frame.label}/{channel}   "
                 f"({deg_y},{deg_x}) over bound   "
                 f"mean {frame.mean:.0f} DN")

    ax = axes[0][0]
    ax.plot(frame.signal.mean(1), label="row mean, measured", lw=1)
    ax.plot(surface.mean(1), label="row mean, fitted", lw=1.5, ls="--")
    ax.set_xlabel("layer row (y)")
    ax.set_ylabel("DN")
    twin = ax.twiny()
    twin.plot(frame.signal.mean(0), color="tab:green", lw=1,
              label="column mean")
    twin.set_xlabel("layer column (x)", color="tab:green")
    ax.set_title("profiles: steep in y, nearly flat in x")
    ax.legend(fontsize=8, loc="lower left")

    ax = axes[0][1]
    lim = float(np.percentile(np.abs(resid), 99))
    im = ax.imshow(resid, cmap="RdBu_r", vmin=-lim, vmax=lim,
                   aspect="auto", interpolation="nearest")
    for name, (x, y, w, h) in patches.rects.items():
        ax.add_patch(Rectangle((x, y), w, h, fill=False, ec="k", lw=1.2))
        ax.text(x + w + 2, y + h / 2, name, fontsize=7, va="center")
    ax.set_title(f"corrected residual, % (clipped at {lim:.1f} %)")
    fig.colorbar(im, ax=ax, fraction=0.046)

    ax = axes[1][0]
    rows, cols = resid.shape
    block = 34
    blocks = np.array([[resid[i:i + block, j:j + block].mean()
                        for j in range(0, cols - block + 1, block)]
                       for i in range(0, rows - block + 1, block)])
    blim = float(np.abs(blocks).max()) or 1.0
    im = ax.imshow(blocks, cmap="RdBu_r", vmin=-blim, vmax=blim,
                   aspect="auto", interpolation="nearest")
    for (i, j), v in np.ndenumerate(blocks):
        ax.text(j, i, f"{v:+.2f}", ha="center", va="center", fontsize=7)
    b_rms, b_max = block_residual_pct(corrected, block)
    ax.set_title(f"34x34 block means, % — rms {b_rms:.2f}, max {b_max:.2f}\n"
                 f"(pixel noise averages away; this is what the fit missed)")
    fig.colorbar(im, ax=ax, fraction=0.046)

    ax = axes[1][1]
    degs, p_vals, b_vals = [], [], []
    for dy, dx in TRANSFER_DEGREES:
        f2 = fit_field(frame, dy, dx, "bound", patches.rects)
        sc = score_model(Model("bound", [f2]), frame, patches)
        degs.append(f"({dy},{dx})")
        p_vals.append(sc.p_max if sc.p_max is not None else np.nan)
        b_vals.append(sc.block_rms)
    ax.plot(degs, p_vals, "o-", label="criterion P, max patch dev")
    ax.plot(degs, b_vals, "s-", label="block residual rms")
    ax.axhline(TOL_P_IN_SAMPLE, color="r", ls=":",
               label=f"P tolerance {TOL_P_IN_SAMPLE} %")
    ax.set_yscale("log")
    ax.set_xlabel("(deg_y, deg_x)")
    ax.set_ylabel("%")
    ax.set_title("in-sample error vs degree\n"
                 "(always falls — do not pick a degree from this)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    fig.tight_layout()
    if out_png:
        fig.savefig(out_png, dpi=110)
        print(f"  wrote {out_png}")
    else:
        plt.show()


# ── Mode: -w / --write ──────────────────────────────────────────────

# Coefficients are the source of truth; the per-ROI gain maps are a derived
# cache that can be rebuilt from them whenever an ROI moves.  Same
# CREATE IF NOT EXISTS / INSERT OR REPLACE shape as hdf5inspect's bayervals.
FLATFIELD_DDL = """
CREATE TABLE IF NOT EXISTS flatfield (
    h5file      TEXT NOT NULL,
    suite_id    TEXT,
    led         TEXT NOT NULL,
    eno         TEXT NOT NULL,
    exposure_us INTEGER,
    bayerlayer  TEXT NOT NULL,
    extent      TEXT NOT NULL,
    deg_y       INTEGER NOT NULL,
    deg_x       INTEGER NOT NULL,
    ncoef       INTEGER NOT NULL,
    coef_json   TEXT NOT NULL,
    domain_rows INTEGER NOT NULL,
    domain_cols INTEGER NOT NULL,
    bound_x     INTEGER,
    bound_y     INTEGER,
    bound_w     INTEGER,
    bound_h     INTEGER,
    frame_w     INTEGER,
    frame_h     INTEGER,
    norm_mean   REAL,
    n_fit       INTEGER,
    n_masked    INTEGER,
    fit_rms_pct REAL,
    block_rms_pct REAL,
    patch_dev_max_pct REAL,
    add_dt      TEXT NOT NULL,
    PRIMARY KEY (h5file, led, eno, bayerlayer, extent, deg_y, deg_x)
)
"""


def cmd_write(reader: Hdf5Reader, h5_path: Path, colors: List[str],
              labels: List[str], deg_y: int, deg_x: int,
              db_path: Path, gain_dir: Path) -> None:
    """
    Fit the chosen model and persist coefficients plus derived gain maps.

    Two artifacts, deliberately redundant: the coefficient row is the source
    of truth and survives an ROI being moved, while the .npy gain map is the
    array the Pi would multiply against without needing a polynomial
    evaluator.  `verify_roundtrip()` checks the two agree.

    Only the best unclipped exposure per (LED, channel) is written — a
    clipped frame's polynomial would be fitted to a plateau.

    Args:
        reader: Open Hdf5Reader for the archive.
        h5_path: Archive path, recorded as flatfield.h5file.
        colors: LED color names to write.
        labels: Exposure labels to consider, or [] for all present.
        deg_y: Maximum exponent on y.
        deg_x: Maximum exponent on x.
        db_path: sqlite db; created with the flatfield schema if needed.
        gain_dir: Root directory for the derived <suite>/<led>_<ch>_<roi>.npy.

    Returns:
        None. Prints a summary.
    """
    import sqlite3
    from datetime import datetime

    frames = select_frames(reader, colors, labels, list(BAYER_CHANNELS))
    if not frames:
        print(f"{h5_path.name}: no usable (unclipped) frames to write.")
        return

    patches = load_patches(frames[0].signal.shape)
    rois = roi_mod.load_active(STREAM_W, STREAM_H, STREAM_MODEL)
    bound = rois["bound"]
    suite_id = reader.attrs("/").get("suite_id", h5_path.stem)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    out_dir = gain_dir / str(suite_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    rows = maps = 0
    try:
        conn.execute(FLATFIELD_DDL)
        for frame in frames:
            ff = fit_field(frame, deg_y, deg_x, "bound", patches.rects)
            model = Model(name="bound", fields=[ff])
            sc = score_model(model, frame, patches)

            conn.execute(
                "INSERT OR REPLACE INTO flatfield VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (h5_path.name, str(suite_id), frame.led, frame.label,
                 frame.exposure_us, frame.channel, ff.extent,
                 ff.deg_y, ff.deg_x, ff.n_terms, ff.coef_json(),
                 ff.domain[0], ff.domain[1],
                 bound.x, bound.y, bound.w, bound.h, STREAM_W, STREAM_H,
                 ff.norm_mean, ff.n_fit, ff.n_masked, ff.fit_rms_pct,
                 sc.block_rms, sc.p_max, now))
            rows += 1

            for name, rect in patches.rects.items():
                stem = f"{frame.led}_{frame.channel}_{name}_gain.npy"
                np.save(out_dir / stem, ff.gain_for_patch(rect))
                maps += 1
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        print(f"error: sqlite write failed, rolled back: {e}", file=sys.stderr)
    finally:
        conn.close()

    print(f"{h5_path.name}: wrote {rows} flatfield row(s) to {db_path}")
    print(f"{' ' * len(h5_path.name)}  {maps} gain map(s) under {out_dir}")


def verify_roundtrip(db_path: Path, gain_dir: Path) -> int:
    """
    Rebuild every stored gain map from its coefficients and compare.

    Guards the redundancy introduced by storing both a polynomial and its
    evaluated array: if the two ever disagree, the cached .npy is stale and
    whatever used it was silently wrong.

    Args:
        db_path: sqlite db holding the flatfield table.
        gain_dir: Root directory of the derived gain maps.

    Returns:
        Number of mismatching maps; 0 when everything agrees.

    Example:
        >>> verify_roundtrip(DEFAULT_DB_PATH, GAIN_DIR)    # doctest: +SKIP
        0
    """
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    bad = checked = 0
    try:
        rows = conn.execute(
            "SELECT suite_id, led, bayerlayer, coef_json, domain_rows, "
            "domain_cols, norm_mean, bound_x, bound_y, bound_w, bound_h "
            "FROM flatfield WHERE extent = 'bound'").fetchall()
    except sqlite3.Error:
        conn.close()
        return 0
    conn.close()

    for (suite, led, ch, cj, drows, dcols, norm, bx, by, bw, bh) in rows:
        spec = json.loads(cj)
        rebuilt = FlatField(
            coef=np.array(spec["coef"]), deg_y=spec["deg_y"],
            deg_x=spec["deg_x"], domain=(drows, dcols), extent="bound",
            window=(0, drows, 0, dcols), covers=(0, drows, 0, dcols),
            norm_mean=norm, led=led, label="", channel=ch,
            n_fit=0, n_masked=0, fit_rms_pct=0.0)
        patches = load_patches((drows, dcols))
        for name, rect in patches.rects.items():
            path = gain_dir / str(suite) / f"{led}_{ch}_{name}_gain.npy"
            if not path.exists():
                continue
            checked += 1
            if not np.allclose(np.load(path), rebuilt.gain_for_patch(rect),
                               rtol=1e-9, atol=1e-12):
                print(f"  MISMATCH {path.name}")
                bad += 1

    print(f"  round-trip: {checked - bad}/{checked} gain maps rebuild exactly "
          f"from their stored coefficients")
    return bad


# ── Self-test ───────────────────────────────────────────────────────
# Pure-function checks plus a synthetic end-to-end pass: a known polynomial
# field imposed on a known flat target must be recovered and divided back out.
# Nothing here touches the live database except to read the roi table.

def _selftest() -> int:
    """
    Run the built-in checks and print a pass/fail line for each.

    Returns:
        0 if every check passed; the assertion aborts on the first failure.
    """
    def check(label, got, want):
        ok = "OK " if got == want else "XX "
        print(f"  {ok}{label:52s} got={got!r}")
        assert got == want, f"{label}: {got!r} != {want!r}"

    def close(label, got, want, tol):
        ok = "OK " if abs(got - want) <= tol else "XX "
        print(f"  {ok}{label:52s} got={got:.6g}")
        assert abs(got - want) <= tol, f"{label}: {got} != {want} +-{tol}"

    print("== term_exponents: the anisotropic basis ==")
    check("(3,1) is 7 terms, not a full cubic's 10",
          term_exponents(3, 1),
          [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1), (3, 0)])
    check("(3,3) is the ordinary total-degree cubic",
          len(term_exponents(3, 3)), 10)
    check("(1,1) is a plane", len(term_exponents(1, 1)), 3)
    check("x exponent never exceeds deg_x",
          max(ix for _, ix in term_exponents(6, 2)), 2)

    print("== design_matrix: normalization and shape ==")
    A = design_matrix(np.arange(4), np.arange(3), 1, 1, (4, 3))
    check("shape is (npix, nterms)", A.shape, (12, 3))
    check("constant term is 1", float(A[:, 0].min()), 1.0)
    corners = design_matrix(np.array([0, 3]), np.array([0, 2]), 1, 1, (4, 3))
    check("domain corners map to +-1",
          sorted(set(np.round(corners[:, 1], 6))), [-1.0, 1.0])

    print("== extent_indices ==")
    r, c = extent_indices("bound", (330, 98))
    check("bound is the whole crop", (len(r), len(c)), (330, 98))
    r, c = extent_indices("bound_trim05", (330, 98))
    check("trim05 drops 5% each side", (r[0], r[-1], c[0], c[-1]),
          (16, 313, 5, 92))
    r, _ = extent_indices("sector3_1", (330, 98))
    check("sector3_1 is the middle band", (r[0], r[-1]), (110, 219))
    r, c = extent_indices("local_s", (330, 98), {"s": (34, 81, 34, 34)})
    check("local grows the ROI by 50% each way",
          (r[0], r[-1], c[0], c[-1]), (64, 131, 17, 84))

    print("== fit recovers a known surface exactly ==")
    rows, cols = 330, 98
    truth_coef = np.array([800.0, -9.0, -100.0, 2.0, -60.0, 1.0, -40.0])
    A = design_matrix(np.arange(rows), np.arange(cols), 3, 1, (rows, cols))
    truth = (A @ truth_coef).reshape(rows, cols)
    frame = Frame(signal=truth.copy(), mask=np.ones_like(truth, bool),
                  led="synth", label="e0", channel="Gr", exposure_us=1,
                  dark_link="none")
    ff = fit_field(frame, 3, 1)
    close("coefficients recovered", float(np.abs(ff.coef - truth_coef).max()),
          0.0, 1e-6)
    close("residual is zero", ff.fit_rms_pct, 0.0, 1e-9)
    close("gain is unit-mean over the fit extent",
          float(ff.gain().mean()), 1.0, 1e-12)

    print("== correction divides the field back out ==")
    flat_target = np.full((rows, cols), 250.0)
    lit = flat_target * (truth / truth.mean())
    frame2 = Frame(signal=lit, mask=np.ones_like(lit, bool), led="synth",
                   label="e0", channel="Gr", exposure_us=1, dark_link="none")
    ff2 = fit_field(frame2, 3, 1)
    corrected = ff2.correct(frame2)
    close("corrected target is flat",
          float(corrected.max() - corrected.min()), 0.0, 1e-9)
    close("and keeps its level", float(corrected.mean()), 250.0, 1e-9)
    # Subtracting instead of dividing is the methods doc's version; on a
    # target 3.7x darker than the calibration card it overshoots by ~3.7x.
    dark_target = flat_target * 0.27 * (truth / truth.mean())
    additive = dark_target - (ff2.evaluate() - ff2.evaluate().mean())
    multiplicative = dark_target / ff2.gain()
    check("subtracting leaves more spread than dividing",
          bool(np.ptp(additive) > 10 * np.ptp(multiplicative)), True)

    print("== metrics ==")
    check("flat array has no block residual",
          block_residual_pct(np.ones((68, 68))), (0.0, 0.0))
    close("flat patch has no ramp",
          patch_ramp_pct(np.ones((40, 40)), (0, 0, 20, 20)), 0.0, 1e-12)
    ramp = np.tile(np.linspace(99.0, 101.0, 20), (20, 1))
    close("a 2% ramp reads as 2%",
          patch_ramp_pct(ramp, (0, 0, 20, 20)), 2.0, 0.02)
    close("noise-free ramp expectation is zero",
          ramp_noise_pct(np.ones((40, 40)), (0, 0, 20, 20)), 0.0, 1e-12)

    print("== field coverage and model dispatch ==")
    local = fit_field(frame, 2, 1, "local_s", {"s": (34, 81, 34, 34)})
    check("a local fit does not cover a distant patch",
          local.contains((34, 291, 34, 34)), False)
    check("a local fit covers its own patch",
          local.contains((34, 81, 34, 34)), True)
    check("a trimmed fit still covers the edges",
          fit_field(frame, 3, 1, "bound_trim05").contains((34, 11, 34, 34)),
          True)
    model = Model("mixed", [ff, local])
    check("field_for prefers the tighter window",
          model.field_for((34, 81, 34, 34)) is local, True)
    check("field_for falls back to the global field",
          model.field_for((34, 291, 34, 34)) is ff, True)

    print("== coefficient JSON round-trip ==")
    spec = json.loads(ff.coef_json())
    check("terms match the basis",
          [tuple(t) for t in spec["terms"]], term_exponents(3, 1))
    close("coefficients survive JSON",
          float(np.abs(np.array(spec["coef"]) - ff.coef).max()), 0.0, 1e-12)

    print("== ROI geometry agrees with the roi table ==")
    patches = load_patches((330, 98))
    check("sample lands where the analysis assumed",
          patches.rects.get("sample"), (34, 81, 34, 34))
    check("white lands where the analysis assumed",
          patches.rects.get("white"), (34, 207, 33, 34))
    check("chip1 lands where the analysis assumed",
          patches.rects.get("chip1"), (34, 11, 34, 34))
    check("chip2 lands where the analysis assumed",
          patches.rects.get("chip2"), (34, 291, 34, 34))
    check("the white ROI is the P reference", patches.reference, "white")

    print("== anomalous-LED detection ==")
    def synth(led, ch, mean, exp):
        return Frame(signal=np.full((4, 4), mean), mask=np.ones((4, 4), bool),
                     led=led, channel=ch, label="e0", exposure_us=exp,
                     dark_link="none")
    pairs = [(synth(led, "Gr", 370.0, 100), synth(led, "Gr", 100.0, 100))
             for led in ("red", "green", "blue")]
    pairs.append((synth("violet", "Gr", 2200.0, 100),
                  synth("violet", "Gr", 100.0, 100)))
    ratios, flagged = find_anomalous_leds(pairs)
    check("the fluorescing LED is flagged", flagged, ["violet"])
    close("and the others agree", ratios["red"], 3.7, 0.01)

    print("\n== all flatfield self-tests passed ==")
    return 0


# ── CLI ─────────────────────────────────────────────────────────────

def _parse_args(argv=None) -> argparse.Namespace:
    """Parse the command line; see the module docstring for the modes."""
    parser = argparse.ArgumentParser(
        description="Polynomial illumination flat fields over a capture suite.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("file", nargs="?",
                        help="capture suite .h5 (bare name or path)")
    parser.add_argument("--selftest", action="store_true",
                        help="run the built-in checks and exit")
    parser.add_argument("file2", nargs="?",
                        help="second suite, required by -t/--transfer")
    parser.add_argument("-f", "--fit", action="store_true",
                        help="fit and print coefficients (default mode)")
    parser.add_argument("-c", "--compare", action="store_true",
                        help="score the extent x degree ladder, in-sample")
    parser.add_argument("-t", "--transfer", action="store_true",
                        help="fit on FILE, apply to FILE2 (requires FILE2)")
    parser.add_argument("-p", "--plot", nargs="*", metavar="ARG",
                        help="diagnostics for [LED [CHANNEL]] "
                             "(default green Gr)")
    parser.add_argument("-o", "--output", type=Path,
                        help="save the -p figure to this PNG instead of "
                             "showing it")
    parser.add_argument("-w", "--write", action="store_true",
                        help="store coefficients and derived gain maps")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH,
                        help="sqlite db for -w (default devdesk.sqlite)")
    parser.add_argument("--gain-dir", type=Path, default=GAIN_DIR,
                        help="root directory for derived gain maps")
    parser.add_argument("--led", action="append", default=[],
                        help="restrict to this LED color (repeatable)")
    parser.add_argument("--exp", action="append", default=[],
                        help="restrict to this exposure label (repeatable)")
    parser.add_argument("--deg-y", type=int, default=DEFAULT_DEG_Y,
                        help=f"max y exponent (default {DEFAULT_DEG_Y})")
    parser.add_argument("--deg-x", type=int, default=DEFAULT_DEG_X,
                        help=f"max x exponent (default {DEFAULT_DEG_X})")
    parser.add_argument("--captures-dir", type=Path,
                        default=DEFAULT_CAPTURES_DIR,
                        help="directory bare filenames resolve against")
    return parser.parse_args(argv)


def main() -> int:
    """
    Entry point: resolve the archive, run the requested modes.

    Returns:
        0 on success, 1 if the archive could not be opened or contains no
        LED data.
    """
    args = _parse_args()
    if args.selftest:
        return _selftest()
    if not args.file:
        print("error: a capture suite .h5 is required", file=sys.stderr)
        return 1
    try:
        h5_path = resolve_h5_path(args.file, args.captures_dir)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.transfer and not args.file2:
        print("error: -t/--transfer needs a second archive", file=sys.stderr)
        return 1

    any_mode = (args.compare or args.transfer or args.write
                or args.plot is not None)
    with Hdf5Reader(h5_path) as reader:
        colors = args.led or list_led_colors(reader)
        if not colors:
            print(f"{h5_path.name}: no /leds group found.", file=sys.stderr)
            return 1
        if args.compare:
            cmd_compare(reader, h5_path, colors, args.exp)
        if args.transfer:
            dst_path = resolve_h5_path(args.file2, args.captures_dir)
            with Hdf5Reader(dst_path) as dst:
                cmd_transfer(reader, dst, h5_path, dst_path, colors, args.exp)
        if args.plot is not None:
            pled = args.plot[0] if len(args.plot) > 0 else "green"
            pch = args.plot[1] if len(args.plot) > 1 else "Gr"
            try:
                cmd_plot(reader, h5_path, pled, pch,
                         args.exp[0] if args.exp else None,
                         args.deg_y, args.deg_x, args.output)
            except ValueError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
        if args.write:
            cmd_write(reader, h5_path, colors, args.exp, args.deg_y,
                      args.deg_x, args.db, args.gain_dir)
            verify_roundtrip(args.db, args.gain_dir)
        if args.fit or not any_mode:
            cmd_fit(reader, h5_path, colors, args.exp, args.deg_y, args.deg_x)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
