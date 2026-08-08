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
  2. **The basis is anisotropic, but the degrees belong to the bound.**
     Over the commissioning bound — a 196x630 strip — row means fell 25 %
     top-to-bottom while column means varied ~2 %, so (3,1) scored within
     0.05 % of (3,3) on criterion P for three fewer free parameters, and a
     near-1-D basis was the right economy.
     That reasoning does not survive a change of bound.  Over the 732x728
     calibration bound the field falls 25-31 % along y *and* 16-21 % along x,
     and (3,1) scores 4.5-6.4 % on criterion P against (4,4)'s 0.2-0.5 % —
     a factor of twenty, not a rounding difference.  `deg_x` is therefore a
     free parameter to be chosen per geometry by `-c` and `-t`, never
     inherited.  The defaults below are set for the calibration bound.
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
    .venv/bin/python3 devdesk/flatfield.py white_20260803_1742.h5 -f --led green
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

import capture_h5  # noqa: E402
import version  # noqa: E402
from hdf5read import Hdf5Reader  # noqa: E402
from rect import Rect  # noqa: E402
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

DEFAULT_DEG_Y, DEFAULT_DEG_X = 4, 4

# Refuse a fit whose usable pixels have been eaten by saturation or dead
# pixels; a fit over the survivors would be quietly biased toward the dim
# corners.
MAX_MASKED_FRACTION = 0.01

# Criteria thresholds, in percent.  See the module docstring.
TOL_P_IN_SAMPLE = 0.5
TOL_P_TRANSFER = 1.0
TOL_S_RAMP = 1.0
FLOOR_PCT = 0.1         # measured repeatability floor; degree-selection step

# Largest criterion-P noise floor a frame may carry and still be scored, in
# percent — half the in-sample tolerance.  Above this the flat field is not
# what is being measured.  See p_noise_pct().
MAX_P_NOISE_PCT = 0.25
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

    @property
    def pixel_noise_pct(self) -> float:
        """
        Per-pixel relative noise, in percent of the frame mean.

        Estimated from the spread of horizontally adjacent differences rather
        than from a fit residual, so it carries no assumption about the degree
        the illumination needs: over one pixel step the illumination is flat to
        far better than the noise, and the difference of two neighbours has
        sqrt(2) times a single pixel's variance.

        This is what decides whether an LED x Bayer-layer combination can be
        measured at all.  An LED barely excites some layers — royal_blue reads
        14 DN in R where it reads 632 DN in B — and at 14 DN the shot noise
        alone is 10 % per pixel.
        """
        diff = np.diff(self.signal, axis=1)
        return float(diff.std() / np.sqrt(2.0) / self.mean * 100.0)


def read_layer(reader: Hdf5Reader, path: str) -> np.ndarray:
    """
    Read one Bayer-layer dataset, refusing one the archive did not store.

    From capture_h5 schema 3 onward a layer with no usable signal is written as
    an empty placeholder carrying its statistics instead of 45 kB of noise (see
    capture_h5.OMITTED_ATTR).  An omitted layer is raised as KeyError, the same
    thing an absent one raises, because every caller here already treats a
    missing combination as one to skip — so this needs no new handling
    anywhere, only this one place to translate.

    Without it the failure would be silent and strange: a (0, 0) array
    subtracted from another (0, 0) array is a perfectly well-formed frame with
    no pixels in it, and the first thing to go wrong would be a fit reporting
    nan several steps later.

    Args:
        reader: Open Hdf5Reader for the archive.
        path: Dataset path, e.g. '/leds/green/lit/e1/Gr'.

    Returns:
        The layer as float64.

    Raises:
        KeyError: The dataset is absent, or is present but omitted.

    Example:
        >>> read_layer(reader, "/leds/green/lit/e1/Gr").shape  # doctest: +SKIP
        (364, 366)
    """
    if capture_h5.is_omitted(reader.attrs(path)):
        raise KeyError(f"{path} was measured and not stored (too little "
                       f"signal); the archive keeps only its statistics")
    return reader.read_array(path).astype(np.float64)


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
        KeyError: The lit dataset or its dark counterpart is absent, or was
            measured and deliberately not stored — see read_layer().

    Example:
        >>> f = load_frame(reader, 'green', 'e1', 'Gr')    # doctest: +SKIP
        >>> f.mean, f.masked_fraction                      # doctest: +SKIP
        (770.9, 0.0)
    """
    dark_link = find_dark_link(reader, led, label)
    if dark_link is None:
        raise KeyError(f"/leds/{led}/dark has no group matching '{label}'")

    lit = read_layer(reader, f"/leds/{led}/lit/{label}/{channel}")
    dark = read_layer(reader, f"/leds/{led}/dark/{dark_link}/{channel}")
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
        roles: {name: role}, e.g. 'sample', 'white'.
        reference: Name of the ROI used as the denominator in criterion P —
            the sample-role patch when there is one, else a white-role patch.
            None if the set has neither inside `bound`.
        dropped: Names of ROIs that fall outside `bound`, so the archive holds
            no pixels for them.  Reported rather than silently skipped: a patch
            missing from the scoring is otherwise indistinguishable from one
            that passed.
    """
    rects: Dict[str, Tuple[int, int, int, int]]
    roles: Dict[str, str]
    reference: Optional[str]
    dropped: List[str] = dc_field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rects)


def archive_bound(reader: Hdf5Reader) -> Optional[Tuple[int, int, int, int]]:
    """
    Return the `bound` an archive records for itself, or None if it has none.

    Archives written at capture_h5 schema_version 2 and later stamp their ROI
    geometry into the root attributes (and in full under `/meta`), so they can
    be interpreted without consulting any database — which matters because the
    analysis machine's colorimeter.sqlite is a copy that an instrument-side ROI
    edit never reaches.  Version-1 archives predate that and return None; the
    caller then falls back to the `roi` table with no way to verify it.

    Args:
        reader: Open Hdf5Reader for the archive.

    Returns:
        (x, y, w, h) in stream space, or None when the archive does not say.

    Example:
        >>> archive_bound(reader)                      # doctest: +SKIP
        (1082, 478, 196, 660)
    """
    try:
        attrs = reader.attrs("/")
    except (KeyError, OSError):
        return None
    keys = ("bound_x", "bound_y", "bound_w", "bound_h")
    if all(k in attrs for k in keys):
        x, y, w, h = (int(attrs[k]) for k in keys)
        return (x, y, w, h)

    # Fall back to the full record: root attrs are a convenience, /meta is the
    # source of truth, and an archive could carry one without the other.
    try:
        bound = reader.read_metadata("/", "meta").get("roi", {}).get("bound")
    except (KeyError, OSError, ValueError):
        return None
    if not bound:
        return None
    return (int(bound["x"]), int(bound["y"]), int(bound["w"]), int(bound["h"]))


def load_patches(shape: Tuple[int, int],
                 bound_override: Optional[Tuple[int, int, int, int]] = None,
                 ) -> Patches:
    """
    Return the calibration ROIs as (x, y, w, h) in bound-crop layer coordinates.

    Read from the live `roi` table through roi.load_calibration() — the
    active=2 rows, which describe the uniform card on the stage rather than the
    instrument's own targets — and converted with roi.to_layer_rect_within(),
    so a patch edited on the instrument is picked up here without touching this
    file.  `bound` itself is dropped: it is the crop, not a patch inside it.

    Note both the origin *and* the size halve, because the layers are half the
    stream in each axis.  The bound halves with them, so a patch keeps its
    relative place in the crop.

    The archive records no ROI geometry of its own (see the `--bound` note in
    the module docstring), so the `bound` in force has to be taken on trust
    and is checked against the stored layer shape.  When the check fails the
    usual cause is that this desktop's `src/colorimeter.sqlite` is an older
    copy than the instrument's: `sync_captures_from_pi.sh` copies `captures/`
    but not the database, so an ROI edited on the Pi does not arrive here.

    Args:
        shape: (rows, cols) of the stored layer arrays, used to verify that
            the archive really was cropped to the `bound` being used.
        bound_override: (x, y, w, h) in stream space to use instead of the
            table's `bound` row.  For archives captured against a `bound`
            this database has never seen.

    Returns:
        A Patches record; ROIs not wholly inside `bound` are dropped, since
        the archive holds no pixels for them.

    Raises:
        ValueError: The table has no calibration `bound` row, or the `bound`
            in force disagrees with the archive's array shape.

    Example:
        >>> load_patches((364, 366)).rects['sample']   # doctest: +SKIP
        (172, 134, 34, 34)
    """
    rois = roi_mod.load_calibration(STREAM_W, STREAM_H, STREAM_MODEL)
    if bound_override is not None:
        bound = Rect(*bound_override, name="bound")
    else:
        bound = rois.get("bound")
    if bound is None:
        raise ValueError(
            "the roi table has no calibration 'bound' row (active=2).\n"
            "A flat-field capture is sampled through the active=2 patch set: "
            "one 'bound',\none 'sample' and the 'white' patches spread across "
            "it. Add those rows, or pass\n--bound X,Y,W,H to supply the crop "
            "geometry alone.")

    rows, cols = shape
    if (bound.h // 2, bound.w // 2) != (rows, cols):
        source = ("--bound" if bound_override is not None
                  else "the active roi row")
        arch_w, arch_h = cols * 2, rows * 2
        raise ValueError(
            f"bound mismatch: {source} is {bound.xywh()}, implying layers of "
            f"{bound.h // 2}x{bound.w // 2}, but the archive stores "
            f"{rows}x{cols}.\n\n"
            f"The archive was cropped to a bound {arch_w} wide by {arch_h} "
            f"tall. Width and height read\nback from the layer shape like "
            f"this; x and y cannot — nothing in the file\nrecords them.\n\n"
            f"Most likely this desktop's src/colorimeter.sqlite is older "
            f"than the instrument's:\nsync_captures_from_pi.sh copies "
            f"captures/ but not the database, so an ROI edited\non the Pi "
            f"never arrives here. Copy the Pi's colorimeter.sqlite over, or "
            f"pass the\ngeometry explicitly:\n\n"
            f"    --bound X,Y,{arch_w},{arch_h}\n\n"
            f"The {STREAM_W}x{STREAM_H} stream bounds the unknowns: "
            f"0 <= x <= {STREAM_W - arch_w}, 0 <= y <= {STREAM_H - arch_h}, "
            f"both even."
        )

    rects, roles, dropped = {}, {}, []
    for name, r in rois.items():
        if name == "bound" or getattr(r, "role", "") == "bound":
            continue
        if not roi_mod.contains_rect(bound, r):
            dropped.append(name)
            continue
        rects[name] = roi_mod.to_layer_rect_within(r, bound).xywh()
        roles[name] = getattr(r, "role", "")

    # The sample patch is the reference when there is one: the measurement the
    # instrument actually makes is sample-over-reference, so criterion P reads
    # as "how far does each reference patch sit from the sample after
    # correction" — the error the flat field would inject into a result. Fall
    # back to a white patch for a card that carries no sample position.
    reference = (next((n for n, role in roles.items() if role == "sample"), None)
                 or next((n for n, role in roles.items() if role == "white"), None))
    return Patches(rects=rects, roles=roles, reference=reference,
                   dropped=dropped)


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

    Whether the economy is worth taking depends entirely on the bound.  Over
    the narrow commissioning strip the field varied ~2 % along x and (3,1),
    (3,2) and (3,3) agreed to 0.01 %, so 7 terms bought what 10 would.  Over
    the wide calibration bound the field varies 16-21 % along x and dropping
    those terms costs a factor of twenty on criterion P.  Measure before
    economizing.

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
        deg_x: Maximum exponent on x. Belongs to the bound — see
            term_exponents(); do not assume a low value transfers.
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


def p_noise_pct(frame: Frame, patches: Patches) -> float:
    """
    Return the criterion-P noise floor this frame carries, in percent.

    Criterion P is a ratio of two patch means, so both contribute: for a
    per-pixel relative noise s, a patch of n pixels has a mean good to
    s/sqrt(n), and the ratio of two independent patches to
    s * sqrt(1/n_a + 1/n_b).  The worst pair is the reference against the
    smallest patch, which on this card is the reference itself — the sample
    ROI is 34x34 where the white patches are 60x60.

    A frame whose floor approaches the tolerance cannot test the flat field:
    a passing score would mean the noise happened to land small.  Compare
    against MAX_P_NOISE_PCT and see partition_frames().

    Args:
        frame: A dark-subtracted Frame.
        patches: The calibration ROIs, for the patch sizes.

    Returns:
        Expected |P| from noise alone, as a percentage.  inf when the patch
        set has no reference.

    Example:
        >>> round(p_noise_pct(frame, patches), 2)      # doctest: +SKIP
        0.34
    """
    if patches.reference is None or patches.reference not in patches.rects:
        return float("inf")
    _, _, rw, rh = patches.rects[patches.reference]
    n_ref = rw * rh
    n_min = min(w * h for _, _, w, h in patches.rects.values())
    return frame.pixel_noise_pct * float(np.sqrt(1.0 / n_ref + 1.0 / n_min))


def partition_frames(frames: List[Frame], patches: Patches,
                     limit: float = MAX_P_NOISE_PCT,
                     ) -> Tuple[List[Frame], List[Tuple[Frame, float]]]:
    """
    Split frames into those that can test criterion P and those too noisy to.

    Args:
        frames: Candidate frames, normally from select_frames().
        patches: The calibration ROIs.
        limit: Largest criterion-P noise floor to accept, in percent.

    Returns:
        (kept, [(frame, floor) ...]) — the excluded list carries each frame's
        floor so the caller can say why it went.
    """
    kept, dropped = [], []
    for frame in frames:
        floor = p_noise_pct(frame, patches)
        if floor > limit:
            dropped.append((frame, floor))
        else:
            kept.append(frame)
    return kept, dropped


def report_exclusions(dropped: List[Tuple[Frame, float]],
                      limit: float = MAX_P_NOISE_PCT) -> None:
    """
    Print the LED x channel combinations excluded for noise, and why.

    Silence here would be misleading: an LED that excites a layer weakly still
    produces a frame, still fits, and still scores — it simply scores noise.
    Naming the combinations is also how the operator learns which LEDs to stop
    metering into which layers on the next card.

    Args:
        dropped: The excluded list from partition_frames().
        limit: The floor that was applied, for the heading.

    Returns:
        None. Prints to stdout.
    """
    if not dropped:
        print(f"  noise exclusions: none "
              f"(every LED x layer floors below {limit:.2f} % on criterion P)")
        return
    print(f"\n  excluded — criterion-P noise floor above {limit:.2f} %, so a "
          f"score would measure\n  the noise and not the flat field:")
    print(f"    {'led':<12} {'ch':<3} {'e#':<3} {'mean DN':>8} "
          f"{'pix noise%':>10} {'P floor%':>9}")
    for frame, floor in sorted(dropped, key=lambda t: -t[1]):
        print(f"    {frame.led:<12} {frame.channel:<3} {frame.label:<3} "
              f"{frame.mean:>8.1f} {frame.pixel_noise_pct:>10.2f} "
              f"{floor:>9.3f}")


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

# The ladder: extent x degree, cheapest first.  Degree pairs are
# (deg_y, deg_x).  The (n,1) rungs are kept not because they are candidates but
# because they are the ones the commissioning bound chose, and on a wide bound
# they fail by a factor of twenty — the table should show that rather than
# leave the old default looking untested.
LADDER: Tuple[Tuple[str, int, int], ...] = (
    ("bound", 1, 1),
    ("bound", 2, 1),
    ("bound", 3, 1),
    ("bound", 2, 2),
    ("bound", 3, 3),
    ("bound", 4, 4),
    ("bound", 5, 5),
    ("bound", 6, 6),
    ("bound_trim05", 3, 3),
    ("bound_trim05", 4, 4),
    ("bound_trim10", 4, 4),
    ("sector3", 2, 2),
    ("local", 1, 1),
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
                labels: List[str],
                bound: Optional[Tuple[int, int, int, int]] = None) -> None:
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

    patches = load_patches(frames[0].signal.shape, bound)
    frames, dropped = partition_frames(frames, patches)
    if not frames:
        print(f"{h5_path.name}: every frame is too noisy to score.")
        report_exclusions(dropped)
        return

    print(f"\n{h5_path.name}: in-sample ladder over {len(frames)} frames "
          f"(best unclipped exposure per LED x channel)")
    cmd_noise_floor(reader, colors)
    report_exclusions(dropped)
    listed = ', '.join(f'{n}{patches.rects[n][:2]}'
                       for n in sorted(patches.rects))
    print(f"\n  patches: {listed}   reference: {patches.reference}")
    if patches.dropped:
        print(f"  NOT sampled — outside bound: {', '.join(patches.dropped)}")
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

# Degrees scanned by the transfer ladder.  Isotropic, because on the wide bound
# x carries nearly as much structure as y; the (n,1) rungs are kept only as the
# reference point the commissioning bound settled on.  The question here is how
# much flexibility of either kind survives being applied to a separate capture.
TRANSFER_DEGREES: Tuple[Tuple[int, int], ...] = (
    (1, 1), (2, 1), (3, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6),
)

# Criterion T compares each rung against the one below it, which only means
# anything when the rungs nest — every term in one is present in the next.
# The (n,1) rungs above do not nest with the isotropic ones: (3,1) has y^3 that
# (2,2) lacks, and (2,2) has x^2 that (3,1) lacks, so their difference is not
# "one more degree" and a gain computed across them is noise. They are measured
# and printed for reference; only this ladder is eligible for the choice.
T_LADDER: Tuple[Tuple[int, int], ...] = (
    (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6),
)

# Rigid offsets, in layer pixels, tried when asking whether a card-registration
# shift rather than a bad model explains a transfer mismatch.  Both axes: a
# card set down by hand misses in x as readily as in y, and the rotated pair
# has no reason to land on either the sample's centre or the bound's.
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


# Degree used to divide the illumination out before registering.  It only has
# to be smooth enough to leave the card's texture behind, so its exact value
# does not matter; the default is reused for want of a reason to differ.
TEXTURE_DEG = DEFAULT_DEG_Y

# Largest card displacement the registration will consider, in layer pixels.
# A card dropped into the fixture by hand lands within a few millimetres.
MAX_REGISTER_SHIFT = 40

# Correlation peak, in units of the search region's own standard deviation,
# below which a registration is not believed.  A matched pair scores 50-130;
# an unmatched one scores under 10.
MIN_REGISTER_SHARPNESS = 20.0


def texture_map(frame: Frame, deg: int = TEXTURE_DEG) -> np.ndarray:
    """
    Return the card's own texture, with the illumination divided out.

    Registration has to key on something that moves with the card.  The raw
    frame is dominated by the illumination, which is fixed to the *stage* and
    would register two captures as aligned no matter where the card sat.
    Dividing by a smooth fit of the frame's own illumination leaves the paper's
    fibre texture, which does move with the card.

    Args:
        frame: A dark-subtracted Frame.
        deg: Degree of the smooth surface divided out, both axes.

    Returns:
        float64 (rows, cols), mean-subtracted, masked pixels set flat.
    """
    smooth = fit_field(frame, deg, deg).evaluate()
    t = np.where(frame.mask, frame.signal / smooth, 1.0)
    return t - t.mean()


# Largest quadrant-to-quadrant disagreement, in layer pixels, still consistent
# with the card having been turned through exactly 180 degrees.  Beyond this the
# rigid model is wrong and a single offset cannot align the whole card.
MAX_ROTATION_SPREAD = 2


@dataclass
class Registration:
    """
    How a destination capture's card sits relative to the source capture's.

    The transform is a 180 degree rotation (when `rot180`) followed by a
    translation.  Composed, that is a point reflection about some centre —
    but the centre is never assumed or needed.  Only the translation is
    measured, from the card's own texture, and the centre it implies falls out
    of it.  Nothing here supposes the card turned about the sample patch, the
    bound's centre, or any other landmark.

    Attributes:
        dy, dx: Layer-pixel offset applied *after* any rotation.
        rot180: True when the card was turned end for end between captures.
        sharpness: Correlation peak over the search region's standard
            deviation.  Above MIN_REGISTER_SHARPNESS the match is real.
        spread: Largest departure, in layer pixels, between the offset that
            suits one quadrant and the global one.  Tests the rigid model
            itself: a card set down a degree or two off 180 cannot be aligned
            by any single offset, and opposite quadrants then pull in opposite
            directions.  None when it was not measured.
        n: How many frames voted.
    """
    dy: int
    dx: int
    rot180: bool
    sharpness: float
    n: int
    spread: Optional[int] = None

    @property
    def trusted(self) -> bool:
        """True when the peak is sharp and one offset suits the whole card."""
        return (self.sharpness >= MIN_REGISTER_SHARPNESS
                and (self.spread is None or self.spread <= MAX_ROTATION_SPREAD))

    def describe(self) -> str:
        """One-line human summary, in both layer and stream pixels."""
        rot = "rotated 180 deg, " if self.rot180 else ""
        spread = ("" if self.spread is None
                  else f", quadrant spread {self.spread} px")
        return (f"{rot}offset ({self.dy:+d}, {self.dx:+d}) layer px "
                f"= ({2 * self.dy:+d}, {2 * self.dx:+d}) stream px, "
                f"peak/sigma {self.sharpness:.0f} over {self.n} frames"
                f"{spread}")


def _quadrant_spread(a: np.ndarray, b: np.ndarray, dy: int, dx: int,
                     search: int = 12) -> int:
    """
    Return how far the four quadrants disagree about the offset, in pixels.

    The global offset is applied first, then each quadrant is re-registered on
    its own.  For a genuinely rigid transform every quadrant should already be
    aligned and return zero.  A residual that grows toward the corners, and
    reverses between opposite ones, is the signature of a rotation that was not
    quite 180 degrees — which no single offset can absorb.

    Args:
        a: Reference texture map.
        b: Texture map already rotated, if the pair is a rotated one.
        dy, dx: The global offset found for the whole frame.
        search: Half-width of the per-quadrant search, in pixels.

    Returns:
        The largest per-quadrant departure from the global offset, in pixels.
    """
    shifted = np.roll(b, (dy, dx), axis=(0, 1))
    rows, cols = a.shape
    worst = 0
    for rs in (slice(0, rows // 2), slice(rows // 2, rows)):
        for cs in (slice(0, cols // 2), slice(cols // 2, cols)):
            qy, qx, _ = _peak_shift(a[rs, cs], shifted[rs, cs], search)
            worst = max(worst, abs(qy), abs(qx))
    return int(worst)


def _peak_shift(a: np.ndarray, b: np.ndarray,
                max_shift: int) -> Tuple[int, int, float]:
    """
    Return (dy, dx, sharpness) of the cross-correlation peak of `b` onto `a`.

    Circular correlation by FFT, then the peak is sought only within
    +/- max_shift of zero so a spurious far-field maximum cannot win.

    Args:
        a: Reference texture map.
        b: Texture map to locate, same shape.
        max_shift: Largest offset considered, in pixels, each axis.

    Returns:
        (dy, dx, peak / std of the correlation surface).
    """
    xc = np.fft.irfft2(np.fft.rfft2(a) * np.conj(np.fft.rfft2(b)), s=a.shape)
    span = np.arange(-max_shift, max_shift + 1)
    window = xc[np.ix_(span % a.shape[0], span % a.shape[1])]
    iy, ix = np.unravel_index(int(window.argmax()), window.shape)
    return int(span[iy]), int(span[ix]), float(window.max() / xc.std())


def register_frames(src: List[Frame], dst: List[Frame], rot180: bool,
                    max_shift: int = MAX_REGISTER_SHIFT) -> Registration:
    """
    Find the rigid offset between two captures of the same card.

    Registration is a property of the two captures, not of any polynomial, so
    it is measured once from the card's texture rather than by minimizing the
    fit error over a grid.  Measuring it from the fit instead lets a low degree
    win simply by being too smooth to notice a misalignment.

    Each (LED, channel) pair present in both captures votes, and the median
    offset is taken; the dimmest layers contribute noise, but they are outvoted
    rather than excluded, since a wrong vote costs nothing once the median is
    taken.

    Args:
        src: Frames from the source capture.
        dst: Frames from the destination capture.
        rot180: Rotate the destination's texture before correlating — for a
            card deliberately turned end for end between captures.
        max_shift: Largest offset considered, in layer pixels.

    Returns:
        A Registration.  Check `.trusted` before acting on it.

    Example:
        >>> register_frames(a, b, rot180=True).describe()   # doctest: +SKIP
        'rotated 180 deg, offset (-9, +9) layer px = ...'
    """
    dst_by_key = {(f.led, f.channel): f for f in dst}
    votes: List[Tuple[int, int, float]] = []
    brightest: Optional[Tuple[np.ndarray, np.ndarray]] = None
    best_mean = -1.0
    for sf in src:
        df = dst_by_key.get((sf.led, sf.channel))
        if df is None or sf.signal.shape != df.signal.shape:
            continue
        t_src = texture_map(sf)
        t_dst = texture_map(df)
        if rot180:
            t_dst = np.rot90(t_dst, 2)
        votes.append(_peak_shift(t_src, t_dst, max_shift))
        if sf.mean > best_mean:
            best_mean, brightest = sf.mean, (t_src, t_dst)
    if not votes:
        return Registration(0, 0, rot180, 0.0, 0)

    dys, dxs, sharps = zip(*votes)
    dy, dx = int(np.median(dys)), int(np.median(dxs))
    # The rigid model is checked on the brightest pair alone: the quadrant
    # search runs on a quarter of the pixels, so a dim layer would answer with
    # noise, and one clean answer is worth more than a median of guesses.
    spread = (None if brightest is None
              else _quadrant_spread(brightest[0], brightest[1], dy, dx))
    return Registration(dy=dy, dx=dx, rot180=rot180,
                        sharpness=float(np.median(sharps)), n=len(votes),
                        spread=spread)


def shift2d(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """
    Shift an array by whole pixels, filling the vacated edge with NaN.

    np.roll would wrap the opposite edge in, which for a field that runs 25 %
    corner to corner would splice the bright edge onto the dim one.  NaN
    instead, so anything that reads a vacated pixel produces NaN rather than a
    plausible wrong number.

    Args:
        a: Array to shift.
        dy: Rows to shift down (positive) or up (negative).
        dx: Columns to shift right (positive) or left (negative).

    Returns:
        A new array, same shape.

    Example:
        >>> shift2d(np.ones((3, 3)), 1, 0)[0].tolist()
        [nan, nan, nan]
    """
    out = np.full_like(a, np.nan, dtype=np.float64)
    ys = slice(max(dy, 0), a.shape[0] + min(dy, 0))
    xs = slice(max(dx, 0), a.shape[1] + min(dx, 0))
    yr = slice(max(-dy, 0), a.shape[0] + min(-dy, 0))
    xr = slice(max(-dx, 0), a.shape[1] + min(-dx, 0))
    out[ys, xs] = a[yr, xr]
    return out


def aligned_correction(field: FlatField, frame: Frame,
                       reg: Registration) -> np.ndarray:
    """
    Correct a destination frame and map it onto the source's card coordinates.

    Order matters.  The flat field belongs to the *stage*, so the frame is
    corrected where it was captured; the card's placement belongs to the
    *card*, so the corrected image is only then rotated and shifted onto the
    source's view of the card.  Correcting after the transform would apply the
    stage's illumination model at the wrong place.

    With the transform applied, a patch rectangle names the same physical spot
    on the card in both captures.  That alone does not cancel the card:
    criterion P compares *different* patches within one image, so the card's
    own patch-to-patch variation still enters it.  Cancelling the card takes
    the further step in patch_profile_ratio(), which divides the destination's
    patch pattern by the source's.

    Args:
        field: The flat field fitted on the source capture.
        frame: The destination frame, in its own coordinates.
        reg: The registration from register_frames().

    Returns:
        The corrected destination, resampled onto source card coordinates.
        Edges vacated by the shift are NaN.
    """
    corrected = field.correct(frame)
    if reg.rot180:
        corrected = np.rot90(corrected, 2)
    return shift2d(corrected, reg.dy, reg.dx)


def patch_ratios(corrected: np.ndarray, patches: Patches,
                 ) -> Tuple[Dict[str, float], Optional[float]]:
    """
    Return each patch's deviation from the reference, and the worst of them.

    Args:
        corrected: A corrected (and, for a transfer, aligned) array.
        patches: The calibration ROIs.

    Returns:
        ({name: % deviation from the reference}, max |deviation|).  A patch
        whose window contains NaN — shifted off the edge — is omitted, and the
        result is (—, None) if the reference itself is missing.
    """
    means: Dict[str, float] = {}
    for name, rect in patches.rects.items():
        value = patch_mean(corrected, rect)
        if np.isfinite(value):
            means[name] = value
    ref = patches.reference
    if ref not in means:
        return {}, None
    dev = {n: (m / means[ref] - 1.0) * 100.0
           for n, m in means.items() if n != ref}
    return dev, max((abs(v) for v in dev.values()), default=None)


def patch_profile(corrected: np.ndarray, patches: Patches,
                  ) -> Dict[str, float]:
    """
    Return each patch's corrected mean divided by the reference patch's.

    Args:
        corrected: A corrected array.
        patches: The calibration ROIs.

    Returns:
        {name: mean / reference mean}, omitting patches that read NaN and the
        reference itself.  Empty if the reference is unreadable.
    """
    means = {n: patch_mean(corrected, r) for n, r in patches.rects.items()}
    means = {n: v for n, v in means.items() if np.isfinite(v)}
    ref = patches.reference
    if ref not in means:
        return {}
    return {n: v / means[ref] for n, v in means.items() if n != ref}


def patch_profile_ratio(field: FlatField, src_frame: Frame, dst_frame: Frame,
                        patches: Patches, reg: Registration,
                        ) -> Tuple[Dict[str, float], Optional[float]]:
    """
    Return the flat-field error with the card's own non-uniformity divided out.

    Criterion P on a single capture cannot separate the two: a patch reading
    0.5 % high may be a card 0.5 % brighter there, or a flat field 0.5 % wrong
    there, and one uniform card cannot say which.

    Taking the same card spot at two *different* frame positions can.  Under
    the registration, patch `n` names one physical piece of card in both
    captures, so its card reflectance appears identically in both profiles and
    divides out:

        ratio(n) = [dst(n)/dst(ref)] / [src(n)/src(ref)]

    What survives is how much the flat field's error differs between the two
    frame positions that piece of card occupied.  For a rotated pair those
    positions are far apart and the ratio is a real test of the field.

    For an un-rotated repeat they are the *same* position, so the field's error
    cancels too — along with the field itself, which drops out of the ratio
    entirely and takes the degree with it.  What is left is neither the card
    nor the model but the bare capture-to-capture repeatability of a patch
    ratio: the floor no flat field of any degree can beat.  A repeat pair is
    therefore the right way to measure that floor, and a ratio that does not
    change with degree is the sign it is working.

    Args:
        field: The flat field fitted on the source capture.
        src_frame: The source frame.
        dst_frame: The destination frame.
        patches: The calibration ROIs.
        reg: The registration from register_frames().

    Returns:
        ({name: % departure from 1}, max |departure|), or ({}, None) when
        either profile could not be formed.
    """
    src_prof = patch_profile(field.correct(src_frame), patches)
    dst_prof = patch_profile(aligned_correction(field, dst_frame, reg),
                             patches)
    shared = set(src_prof) & set(dst_prof)
    if not shared:
        return {}, None
    dev = {n: (dst_prof[n] / src_prof[n] - 1.0) * 100.0 for n in shared}
    return dev, max(abs(v) for v in dev.values())


def cmd_transfer(src: Hdf5Reader, dst: Hdf5Reader, src_path: Path,
                 dst_path: Path, colors: List[str], labels: List[str],
                 bound: Optional[Tuple[int, int, int, int]] = None,
                 rot180: bool = False) -> None:
    """
    Fit fields on one suite, apply them to another, and report the error.

    This is the only honest way to choose a degree.  An in-sample residual
    always falls as degree rises; a transfer error stops falling once the
    extra terms are fitting that capture's own noise and texture.  Criterion T
    takes the smallest degree whose transfer error improves on the previous by
    less than the measured repeatability floor.

    The two captures are registered first, from the card's own texture, and
    every degree is then scored through that one registration.  Registration
    is a property of the captures, not of the polynomial, so measuring it from
    the fit error instead would let a low degree win by being too smooth to
    notice a misalignment.

    With `rot180`, the destination is a capture of the same card turned end for
    end.  Each corrected patch is then compared against *the same physical
    piece of card* rather than merely the same frame position, so the card's
    own non-uniformity cancels out of the ratio.  Running both ways separates
    the two error sources: what the un-rotated comparison shows and the
    rotated one does not is the card, not the flat field.

    Args:
        src: Reader for the archive the fields are fitted on.
        dst: Reader for the archive they are applied to.
        src_path, dst_path: Paths, for the heading.
        colors: LED color names to include.
        labels: Exposure labels to consider, or [] for all present.
        bound: Stream-space bound override, or None for the archive's.
        rot180: The destination imaged the card rotated 180 degrees.

    Returns:
        None. Prints to stdout.
    """
    pairs = _paired_frames(src, dst, colors, labels)
    if not pairs:
        print("no LED/channel pairs usable in both archives.")
        return

    patches = load_patches(pairs[0][0].signal.shape, bound)
    kept, dropped = partition_frames([sf for sf, _ in pairs], patches)
    keep_keys = {(f.led, f.channel) for f in kept}
    pairs = [p for p in pairs if (p[0].led, p[0].channel) in keep_keys]

    print(f"\ntransfer: fit on {src_path.name} -> apply to {dst_path.name}")
    print(f"  {len(pairs)} LED x channel pairs, both unclipped; "
          f"reference patch: {patches.reference}")
    report_exclusions(dropped)

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
    if not pairs:
        print("  nothing left to score.")
        return

    reg = register_frames([sf for sf, _ in pairs], [df for _, df in pairs],
                          rot180)
    print(f"\n  registration from card texture: {reg.describe()}")
    if reg.sharpness < MIN_REGISTER_SHARPNESS:
        print(f"    ! peak below {MIN_REGISTER_SHARPNESS:.0f} — the two "
              f"captures do not share a recognizable\n      card texture. "
              f"Treating them as unshifted; the transfer numbers below\n"
              f"      carry an unknown registration error.")
        reg = Registration(0, 0, rot180, reg.sharpness, reg.n, reg.spread)
    elif reg.spread is not None and reg.spread > MAX_ROTATION_SPREAD:
        print(f"    ! quadrants disagree by {reg.spread} layer px about the "
              f"offset, over a limit of\n      {MAX_ROTATION_SPREAD}. One "
              f"rigid offset does not align the whole card, so the card was\n"
              f"      not turned through a clean 180 degrees. Patch-for-patch "
              f"comparison below is\n      unreliable toward the corners; "
              f"re-seat the card and re-capture.")
    elif rot180:
        print(f"    the rotation was measured from the card's texture, not "
              f"assumed: no centre of\n    rotation is presumed, only the "
              f"translation that follows the 180 degree turn.\n    All four "
              f"quadrants agree on it to within {reg.spread} layer px, so one "
              f"rigid offset\n    does align the whole card, and a patch names "
              f"the same physical piece of card\n    in both captures — at two "
              f"frame positions far apart.")

    print(f"\n  criterion P <= {TOL_P_TRANSFER} % on transfer "
          f"(in-sample column shown for contrast).")
    print(f"  R divides the destination's patch pattern by the source's. The "
          f"same card spot\n  is on both sides, so the card's own "
          f"non-uniformity cancels and R is flat-field\n  error alone — for a "
          f"*rotated* pair, where that spot sits at two far-apart frame\n"
          f"  positions. For an un-rotated repeat both positions coincide, the "
          f"field cancels\n  out of the ratio along with the card, and R "
          f"instead measures the bare\n  capture-to-capture repeatability of a "
          f"patch ratio: the floor no degree can beat.\n  Constant R down the "
          f"column is the sign of that case.\n")
    print(f"  {'deg':<6} {'trms':>4} | {'in-sample':>13} | "
          f"{'aligned transfer':>13} | {'card-free':>7} | gain vs")
    print(f"  {'':<6} {'':>4} | {'P med':>6} {'blk':>6} | "
          f"{'P med':>6} {'P max':>6} | {'R max':>7} | prev rung")

    scored: Dict[Tuple[int, int], List] = {}
    medians: Dict[Tuple[int, int], Optional[float]] = {}
    for deg_y, deg_x in TRANSFER_DEGREES:
        ins, ins_b, rows, r_vals = [], [], [], []
        for sf, df in pairs:
            try:
                field = fit_field(sf, deg_y, deg_x)
            except ValueError:
                continue
            sc = score_model(Model("bound", [field]), sf, patches)
            if sc.p_max is not None:
                ins.append(sc.p_max)
            if sc.block_rms is not None:
                ins_b.append(sc.block_rms)
            _, p_max = patch_ratios(aligned_correction(field, df, reg),
                                    patches)
            if p_max is not None:
                rows.append((p_max, sf, df))
            _, r_max = patch_profile_ratio(field, sf, df, patches, reg)
            if r_max is not None:
                r_vals.append(r_max)
        scored[(deg_y, deg_x)] = rows

        i_med, _ = _agg(ins)
        ib, _ = _agg(ins_b)
        a_med, a_max = _agg([r[0] for r in rows])
        _, r_max_all = _agg(r_vals)
        medians[(deg_y, deg_x)] = a_med

        # Only ladder rungs get a gain: the reference rungs do not nest with
        # their neighbours, so the difference would not mean "one more degree".
        gain = None
        if (deg_y, deg_x) in T_LADDER:
            i = T_LADDER.index((deg_y, deg_x))
            if i > 0:
                below = medians.get(T_LADDER[i - 1])
                if below is not None and a_med is not None:
                    gain = below - a_med
        note = "" if (deg_y, deg_x) in T_LADDER else "  ref"
        n_terms = len(term_exponents(deg_y, deg_x))
        print(f"  ({deg_y},{deg_x}) {n_terms:>4} | "
              f"{_fmt(i_med)} {_fmt(ib, 6)} | {_fmt(a_med)} {_fmt(a_max)} | "
              f"{_fmt(r_max_all, 7)} | "
              f"{'-' if gain is None else f'{gain:+6.2f}'}{note}")

    # Criterion T: the cheapest rung that gets within the repeatability floor
    # of the best the ladder manages.  Stopping at the first rung that fails to
    # improve on its predecessor would be simpler, but the ladder is not
    # monotonic — a rung can sit a little worse than the one below it and the
    # next still be much better — and a single dip would then end the search
    # several degrees early.
    usable = [(d, medians[d]) for d in T_LADDER if medians.get(d) is not None]
    recommended = None
    if usable:
        best = min(v for _, v in usable)
        recommended = next(d for d, v in usable if v <= best + FLOOR_PCT)

    deg = recommended or (DEFAULT_DEG_Y, DEFAULT_DEG_X)
    if recommended:
        print(f"\n  criterion T: (deg_y, deg_x) = {recommended} at "
              f"{medians[recommended]:.2f} % is the cheapest rung within "
              f"{FLOOR_PCT} % of the\n  ladder's best ({best:.2f} %).")
    else:
        print(f"\n  criterion T: nothing on the ladder could be scored; "
              f"reporting at the default {deg}")

    # A median hides the pair that fails; name the worst few explicitly.
    worst = sorted(scored[deg], key=lambda r: -r[0])
    if worst:
        print(f"\n  worst pairs at {deg}, aligned:")
        for p_max, sf, df in worst[:5]:
            print(f"    {p_max:6.2f} %  {sf.led:<11} {sf.channel:<3} "
                  f"src {sf.mean:7.1f} DN  dst {df.mean:7.1f} DN")


# ── Mode: -p / --plot ───────────────────────────────────────────────

def cmd_plot(reader: Hdf5Reader, h5_path: Path, led: str, channel: str,
             label: Optional[str], deg_y: int, deg_x: int,
             out_png: Optional[Path],
             bound: Optional[Tuple[int, int, int, int]] = None) -> None:
    """
    Draw the four diagnostics that make a fit's behaviour legible.

    Top left and top right: the row and column profiles, measured against
    fitted, drawn as equal panels on a shared scale.  They were once one panel
    with the column trace squeezed onto a second x-axis, which suited a bound
    where x varied 2 % and y varied 25 %; on a near-square bound both axes
    carry real structure and hiding either one invites the same mistake that
    the anisotropic default made.  Bottom left: the corrected frame averaged
    into 34x34 blocks — pixel noise averages away, leaving what the fit
    missed — with the patch boxes overlaid.  Bottom right: in-sample error
    against degree, the curve that keeps falling and must not be used on its
    own to pick a degree.

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

    patches = load_patches(frame.signal.shape, bound)
    ff = fit_field(frame, deg_y, deg_x, "bound", patches.rects)
    surface = ff.evaluate()
    corrected = ff.correct(frame)
    resid = (corrected / corrected.mean() - 1.0) * 100.0

    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    fig.suptitle(f"{h5_path.name}   {led}/{frame.label}/{channel}   "
                 f"({deg_y},{deg_x}) over bound   "
                 f"mean {frame.mean:.0f} DN")

    # One scale for both profiles, so "steeper in y than in x" is something the
    # reader sees rather than something the axes assert.
    prof = [(frame.signal.mean(1), surface.mean(1), "row (y)"),
            (frame.signal.mean(0), surface.mean(0), "column (x)")]
    lo = min(float(m.min()) for m, _, _ in prof)
    hi = max(float(m.max()) for m, _, _ in prof)
    pad = 0.05 * (hi - lo)
    for ax, (measured, fitted, axis_name) in zip(axes[0], prof):
        span = (measured.max() - measured.min()) / measured.mean() * 100.0
        ax.plot(measured, lw=1, label="measured")
        ax.plot(fitted, lw=1.5, ls="--", label="fitted")
        ax.set_xlabel(f"layer {axis_name}")
        ax.set_ylabel("DN")
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_title(f"{axis_name} profile — spans {span:.1f} %")
        ax.legend(fontsize=8, loc="lower center")

    ax = axes[1][0]
    rows, cols = resid.shape
    block = 34
    blocks = np.array([[resid[i:i + block, j:j + block].mean()
                        for j in range(0, cols - block + 1, block)]
                       for i in range(0, rows - block + 1, block)])
    blim = float(np.abs(blocks).max()) or 1.0
    im = ax.imshow(blocks, cmap="RdBu_r", vmin=-blim, vmax=blim,
                   extent=(0.0, float(cols), float(rows), 0.0),
                   interpolation="nearest")
    for name, (x, y, w, h) in patches.rects.items():
        ax.add_patch(Rectangle((x, y), w, h, fill=False, ec="k", lw=1.2))
        ax.text(x + w / 2, y - 4, name, fontsize=7, ha="center")
    b_rms, b_max = block_residual_pct(corrected, block)
    ax.set_title(f"34x34 block means, % — rms {b_rms:.2f}, max {b_max:.2f}\n"
                 f"(pixel noise averages away; this is what the fit missed)")
    ax.set_xlabel("layer column (x)")
    ax.set_ylabel("layer row (y)")
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
    patches_json TEXT,
    PRIMARY KEY (h5file, led, eno, bayerlayer, extent, deg_y, deg_x)
)
"""


def _ensure_patches_column(conn) -> None:
    """
    Add `patches_json` to an existing flatfield table that predates it.

    CREATE TABLE IF NOT EXISTS leaves an older table alone, so a db written
    before the column existed would silently keep writing rows without it.

    Args:
        conn: Open sqlite3 connection.

    Returns:
        None.
    """
    have = {r[1] for r in conn.execute("PRAGMA table_info(flatfield)")}
    if have and "patches_json" not in have:
        conn.execute("ALTER TABLE flatfield ADD COLUMN patches_json TEXT")


def cmd_write(reader: Hdf5Reader, h5_path: Path, colors: List[str],
              labels: List[str], deg_y: int, deg_x: int,
              db_path: Path, gain_dir: Path,
              bound_override: Optional[Tuple[int, int, int, int]] = None,
              ) -> None:
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

    patches = load_patches(frames[0].signal.shape, bound_override)
    # The calibration set, not the in-force one: these rows must record the
    # bound the patches were taken from, and active=1 carries a different one.
    bound = (Rect(*bound_override, name="bound") if bound_override
             else roi_mod.load_calibration(STREAM_W, STREAM_H,
                                           STREAM_MODEL)["bound"])
    # Stored with every row so a map can be rebuilt from the database alone.
    # Without it the check has to ask the roi table where a patch was, and the
    # table answers for today, not for the run that wrote the map.
    patches_json = json.dumps({n: list(r) for n, r in patches.rects.items()},
                              sort_keys=True)
    suite_id = reader.attrs("/").get("suite_id", h5_path.stem)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    out_dir = gain_dir / str(suite_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    rows = maps = 0
    try:
        conn.execute(FLATFIELD_DDL)
        _ensure_patches_column(conn)
        for frame in frames:
            ff = fit_field(frame, deg_y, deg_x, "bound", patches.rects)
            model = Model(name="bound", fields=[ff])
            sc = score_model(model, frame, patches)

            conn.execute(
                "INSERT OR REPLACE INTO flatfield VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (h5_path.name, str(suite_id), frame.led, frame.label,
                 frame.exposure_us, frame.channel, ff.extent,
                 ff.deg_y, ff.deg_x, ff.n_terms, ff.coef_json(),
                 ff.domain[0], ff.domain[1],
                 bound.x, bound.y, bound.w, bound.h, STREAM_W, STREAM_H,
                 ff.norm_mean, ff.n_fit, ff.n_masked, ff.fit_rms_pct,
                 sc.block_rms, sc.p_max, now, patches_json))
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
    bad = checked = skipped = 0
    try:
        _ensure_patches_column(conn)
        rows = conn.execute(
            "SELECT suite_id, led, bayerlayer, coef_json, domain_rows, "
            "domain_cols, norm_mean, patches_json "
            "FROM flatfield WHERE extent = 'bound'").fetchall()
    except sqlite3.Error:
        conn.close()
        return 0
    conn.close()

    for (suite, led, ch, cj, drows, dcols, norm, pj) in rows:
        # A row that predates patches_json cannot be checked: the rects it was
        # written against are not recorded anywhere, and substituting today's
        # roi table would compare the map against a different patch and report
        # a mismatch that says nothing about the stored coefficients.
        if not pj:
            skipped += 1
            continue
        spec = json.loads(cj)
        rebuilt = FlatField(
            coef=np.array(spec["coef"]), deg_y=spec["deg_y"],
            deg_x=spec["deg_x"], domain=(drows, dcols), extent="bound",
            window=(0, drows, 0, dcols), covers=(0, drows, 0, dcols),
            norm_mean=norm, led=led, label="", channel=ch,
            n_fit=0, n_masked=0, fit_rms_pct=0.0)
        for name, rect in json.loads(pj).items():
            path = gain_dir / str(suite) / f"{led}_{ch}_{name}_gain.npy"
            if not path.exists():
                continue
            checked += 1
            if not np.allclose(np.load(path),
                               rebuilt.gain_for_patch(tuple(rect)),
                               rtol=1e-9, atol=1e-12):
                print(f"  MISMATCH {path.name}")
                bad += 1

    print(f"  round-trip: {checked - bad}/{checked} gain maps rebuild exactly "
          f"from their stored coefficients")
    if skipped:
        print(f"  {skipped} row(s) predate the stored patch geometry and "
              f"cannot be verified;\n  re-run -w on those suites to make them "
              f"self-contained.")
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

    print("== calibration ROI geometry (roi table, active=2) ==")
    # The layer crop is half the bound in each axis, so a patch's origin *and*
    # its size halve. The bound halves with them, which is why the patches keep
    # their relative places rather than collapsing into one corner.
    patches = load_patches((364, 366))
    check("sample is the P reference", patches.reference, "sample")
    check("four white patches, one per quadrant",
          sum(1 for r in patches.roles.values() if r == "white"), 4)
    check("no patch fell outside the bound", patches.dropped, [])
    for name, rect in patches.rects.items():
        x, y, w, h = rect
        check(f"{name} lies inside the crop",
              x >= 0 and y >= 0 and y + h <= 364 and x + w <= 366, True)
    centre_x = np.mean([x + w / 2 for x, _, w, _ in patches.rects.values()])
    centre_y = np.mean([y + h / 2 for _, y, _, h in patches.rects.values()])
    sx, sy, sw, sh = patches.rects["sample"]
    close("the sample sits at the centroid of the patch set",
          float(np.hypot(sx + sw / 2 - centre_x, sy + sh / 2 - centre_y)),
          0.0, 2.0)

    print("== registration: shift, rotation, and the rigid-model check ==")
    rng = np.random.default_rng(7)
    card = rng.normal(0.0, 1.0, (120, 130))
    # _peak_shift returns the offset that brings the second argument back onto
    # the first, so a card displaced by (+5, -7) is recovered as (-5, +7).
    moved = np.roll(card, (5, -7), axis=(0, 1))
    check("a pure translation is recovered",
          _peak_shift(card, moved, 20)[:2], (-5, 7))
    check("an unmoved card registers at zero",
          _peak_shift(card, card, 20)[:2], (0, 0))
    # Mirror the pipeline: the card is imaged turned end for end and displaced,
    # and register_frames un-rotates the destination before correlating.
    turned = np.roll(np.rot90(card, 2), (5, -7), axis=(0, 1))
    check("a 180 degree turn is recovered as a rotation plus an offset",
          _peak_shift(card, np.rot90(turned, 2), 20)[:2], (5, -7))
    check("the same pair without un-rotating does not register",
          _peak_shift(card, turned, 20)[2] < MIN_REGISTER_SHARPNESS, True)
    check("shift2d vacates the leading edge with NaN",
          bool(np.isnan(shift2d(np.ones((3, 3)), 1, 0)[0]).all()), True)
    check("shift2d does not wrap the far edge in",
          float(shift2d(np.arange(9.0).reshape(3, 3), 1, 0)[1, 0]), 0.0)
    check("a rigid pair shows no quadrant spread",
          _quadrant_spread(card, moved, -5, 7), 0)
    # A card set down a few degrees off 180 cannot be aligned by any single
    # offset; the quadrants then pull apart, which is what `spread` catches.
    # theta rad displaces a quadrant centre, ~44 px out, by ~44*theta px; 0.15
    # puts that well past the 2 px limit while staying a small-angle rotation.
    yy, xx = np.mgrid[0:120, 0:130]
    theta = 0.15
    warped = card[np.clip(np.rint(yy + theta * (xx - 65)).astype(int), 0, 119),
                  np.clip(np.rint(xx - theta * (yy - 60)).astype(int), 0, 129)]
    check("a card turned off 180 shows quadrant spread",
          _quadrant_spread(card, warped, *_peak_shift(card, warped, 20)[:2])
          > MAX_ROTATION_SPREAD, True)

    print("== noise floor and exclusion ==")
    quiet = Frame(signal=np.full((60, 60), 500.0),
                  mask=np.ones((60, 60), bool), led="green", channel="Gr",
                  label="e0", exposure_us=1, dark_link="none")
    close("a noiseless frame carries no noise floor",
          quiet.pixel_noise_pct, 0.0, 1e-9)
    loud = Frame(signal=rng.normal(100.0, 10.0, (60, 60)),
                 mask=np.ones((60, 60), bool), led="green", channel="Gr",
                 label="e0", exposure_us=1, dark_link="none")
    close("a 10 % frame reads about 10 %", loud.pixel_noise_pct, 10.0, 1.0)
    kept, dropped = partition_frames([quiet, loud], patches, limit=0.05)
    check("the quiet frame is kept", [f.mean for f in kept], [500.0])
    check("the loud frame is excluded", len(dropped), 1)

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
    parser.add_argument("--rot180", action="store_true",
                        help="FILE2 imaged the same card turned 180 deg; "
                             "compares each patch against the same physical "
                             "card spot, cancelling the card's own "
                             "non-uniformity")
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
    parser.add_argument("--bound", metavar="X,Y,W,H",
                        help="bound ROI in stream space, overriding the roi "
                             "table; for archives captured against a bound "
                             "this database has not got")
    args = parser.parse_args(argv)

    if args.bound:
        try:
            parts = tuple(int(v) for v in args.bound.split(","))
        except ValueError:
            parser.error("--bound wants four integers, X,Y,W,H")
        if len(parts) != 4:
            parser.error(f"--bound wants four integers, got {len(parts)}")
        args.bound = parts
    return args


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
    try:
        return _run_modes(args, h5_path, any_mode)
    except ValueError as e:
        # A bound/ROI mismatch is an operator problem with a stated remedy,
        # not a crash; a traceback would bury the remedy.
        print(f"\nerror: {e}", file=sys.stderr)
        return 1


def resolve_bound(reader: Hdf5Reader,
                  override: Optional[Tuple[int, int, int, int]],
                  ) -> Optional[Tuple[int, int, int, int]]:
    """
    Decide which `bound` to use, and say where it came from.

    Precedence: an explicit --bound wins, then the archive's own record, then
    None (meaning fall back to the `roi` table).  The archive is preferred over
    the table because it cannot be stale — it was written by the run that
    produced the pixels.

    Args:
        reader: Open Hdf5Reader for the archive.
        override: The --bound value, or None.

    Returns:
        (x, y, w, h) or None. Prints a one-line note about the source.
    """
    if override is not None:
        print(f"  bound {override} from --bound (overriding the archive)")
        return override
    stamped = archive_bound(reader)
    if stamped is not None:
        print(f"  bound {stamped} from the archive's own metadata")
        return stamped
    print("  bound from the roi table — this archive does not record its own "
          "geometry\n  (schema_version 1); the table cannot be verified "
          "against it.")
    return None


def _run_modes(args: argparse.Namespace, h5_path: Path,
               any_mode: bool) -> int:
    """Open the archive and run whichever modes were asked for."""
    with Hdf5Reader(h5_path) as reader:
        # Only formats outside src/version.py's READS_* ranges are refused
        # here. A schema-1 archive is still supported, and resolve_bound below
        # is what copes with its missing geometry.
        problem = version.check_archive(reader.attrs("/"))
        if problem:
            print(f"{h5_path.name}: {problem}", file=sys.stderr)
            return 1
        args.bound = resolve_bound(reader, args.bound)
        colors = args.led or list_led_colors(reader)
        if not colors:
            print(f"{h5_path.name}: no /leds group found.", file=sys.stderr)
            return 1
        if args.compare:
            cmd_compare(reader, h5_path, colors, args.exp, args.bound)
        if args.transfer:
            dst_path = resolve_h5_path(args.file2, args.captures_dir)
            with Hdf5Reader(dst_path) as dst:
                dst_problem = version.check_archive(dst.attrs("/"))
                if dst_problem:
                    print(f"{dst_path.name}: {dst_problem}", file=sys.stderr)
                    return 1
                # Transfer compares two archives pixel for pixel, so they must
                # be crops of the same rectangle. Two different bounds would
                # silently compare different parts of the stage.
                dst_bound = archive_bound(dst)
                if (dst_bound is not None and args.bound is not None
                        and dst_bound != tuple(args.bound)):
                    raise ValueError(
                        f"the two archives were cropped to different bounds:\n"
                        f"  {h5_path.name}: {tuple(args.bound)}\n"
                        f"  {dst_path.name}: {dst_bound}\n"
                        f"Transfer compares them pixel for pixel, so it would "
                        f"be comparing different\nparts of the stage. "
                        f"Re-capture both against one bound.")
                cmd_transfer(reader, dst, h5_path, dst_path, colors,
                             args.exp, args.bound, args.rot180)
        if args.plot is not None:
            pled = args.plot[0] if len(args.plot) > 0 else "green"
            pch = args.plot[1] if len(args.plot) > 1 else "Gr"
            try:
                cmd_plot(reader, h5_path, pled, pch,
                         args.exp[0] if args.exp else None,
                         args.deg_y, args.deg_x, args.output, args.bound)
            except ValueError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
        if args.write:
            cmd_write(reader, h5_path, colors, args.exp, args.deg_y,
                      args.deg_x, args.db, args.gain_dir, args.bound)
            verify_roundtrip(args.db, args.gain_dir)
        if args.fit or not any_mode:
            cmd_fit(reader, h5_path, colors, args.exp, args.deg_y, args.deg_x)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
