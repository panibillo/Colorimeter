"""
flatfield_pool.py — pooled flat-field analysis over an interior bound.
--------------------------------------------------------------------------
Desktop-only developer tool (matplotlib for the figures).  Not synced to the
Pi.  Builds on `devdesk/flatfield.py`, which it imports rather than repeats.

Why this exists
---------------
`flatfield.py` answered "what polynomial, over what extent" for `bound`
roi.rowid 26 — the 732x728 calibration crop the gray-card suites were captured
against.  That bound is much larger than anything the instrument measures
through: its illumination falls 25 % in y and 19 % in x, which is what forced
the (4,4) basis.

The instrument's real geometry is a small interior bound (roi.rowid 39,
366x260 in stream space) holding three 68x68 ROIs in a nearly horizontal row:

    chip1 (rowid 36)      sample (rowid 32)      chip2 (rowid 35)
    layer x 10..44        layer x 72..106        layer x 144..178

At measurement time the two chips are imaged alongside every sample, so the
polynomial's job is not to flatten a picture.  It is to be **anchored on the
two chips and asked what the illumination is at `sample`** — the divisor that
normalizes the corrected sample reading, per LED, per Bayer layer, per
exposure.

The simplifying consequence
---------------------------
The three ROIs are collinear in x and the sample lies *between* the chips
(fractional position t = 0.4627), so the whole deliverable collapses to two
scalars per (LED, layer, exposure):

    divisor = w1 * mean(chip1) + w2 * mean(chip2)

The curvature correction says what that is worth:

    rho = g_sample / ((1 - t) * g_chip1 + t * g_chip2)

It is 1.0 exactly when the illumination is linear in x across the chips —
the case where a polynomial buys nothing, because interpolating the two chip
readings would already give the sample's illumination.  Measured here it is
about 1.02, because the sample sits on a ridge *between* the chips and
interpolating them underestimates it by 2 %.  The polynomial exists to
produce w1 and w2; everything else this module prints is evidence that they
are right.

`rho` is a property of the fitted field, so both anchor forms report the same
value.  `w1 + w2` is *not* the same number: it agrees to first order but
differs at second order in the chip-to-chip gain spread (0.13 % here),
because the weights combine the chips reciprocally where rho interpolates
them linearly.  Reports carry both, under `wsum` and `rho`.

What the five archives measure
------------------------------
ZP1001..ZP1005 image one gray card five times: an untouched repeat pair, one
re-placement, and a rotated pair.  Measured inside bound 39:

    saturated pixels, all 600 cells                          0.00 %
    black level (dark median)                                64 DN
    exposure linearity across e0/e1/e2                      < 1.7 %
    chip1/chip2 at 0 deg vs 180 deg              1.0169 vs 1.0192
    -> chip reflectance difference                     0.02 - 0.11 %
    -> illumination difference                           1.3 - 1.8 %
    in-sample residual, (1,1) -> (4,4)             2.96 % -> 2.50 %
    anchor error,      (1,1) -> (4,4)             -1.70 % -> 0.30 %

Three things follow, and they shape this module:

  1. **The rotation is a control, not just another capture.**  chip1/chip2 does
     not flip between orientations, which proves the ~1.8 % chip-to-chip
     difference is illumination and not the card.  Pooling all five captures
     into one baseline is therefore legitimate, and the symmetry decomposition
     is reported as a standing check that it stays legitimate.
  2. **In-sample residual cannot pick the degree here.**  It moves 0.46 % over
     the whole ladder while the anchor error moves 2 %.  Degree is chosen by
     leave-one-placement-out on the anchor — criterion T from
     `tex/colorimeter_flatfield.tex`, applied to the quantity that matters.
  3. **deg_x >= 2 is what matters; deg_y barely does.**  The chips and the
     sample lie along x, so predicting one from the other two is a question
     about curvature in x.  An (n,1) basis has none and misses by 1.7 - 4.6 %.

Where the answers go
--------------------
Three destinations, and only the last one leaves the desktop:

    devdesk/flatfield/<ref>/     five reports and the figures
    devdesk/flatfield.sqlite     `flatfield` (coefficients) and `anchor`
                                 (every rung's weights) — development history
    src/colorimeter.sqlite       `layermodels` — one row per LED x layer, the
                                 reduced result the instrument reads

The last is opt-in: pass `layermodels_db=` to analyze(), which
`run_bound39a.py` does.  It puts new weights **in force**, superseding the
previous rows rather than deleting them.  See build_layermodels() for which
cell becomes which row, and src/layermodel_repo.py for what reads them back.

The `anchor` table keeps a row per (LED, layer, exposure); `layermodels` keeps
one per (LED, layer).  That is not a loss: across the ladder the weights move
under 0.2 %, against a 0.4-0.8 % placement floor, so the exposure axis carries
no signal.  Only p10 and p90 depend on exposure, and those are published at a
reference exposure for the capture path to scale.

Coordinate spaces
-----------------
As in `flatfield.py`: everything works in **bound-crop Bayer-layer**
coordinates, and polynomial coordinates are normalized to [-1, 1] over the
whole crop.  The extra step here is that the archives were cropped to a
*larger* bound than the one being analysed, so each stored layer is subcropped
to the target bound before anything else happens.  See `subcrop_slices()`.

Usage:
    .venv/bin/python3 devdesk/run_bound39a.py
    .venv/bin/python3 devdesk/flatfield_pool.py --selftest
"""

import json
import sys
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "src"))

import flatfield as ff_mod  # noqa: E402
from flatfield import (BAYER_CHANNELS, FLOOR_PCT, MAX_MASKED_FRACTION,  # noqa: E402
                       RAW_MAX, SATURATED_DN, STREAM_MODEL, STREAM_H, STREAM_W,
                       Frame, Patches)
import db_manager  # noqa: E402
import layermodel_repo  # noqa: E402
from layermodel_repo import LayerModel  # noqa: E402
from hdf5read import Hdf5Reader  # noqa: E402
from rect import Rect  # noqa: E402
import roi as roi_mod  # noqa: E402

REPO_ROOT = _HERE.parent
DEFAULT_CAPTURES_DIR = REPO_ROOT / "captures"
DEFAULT_FF_DB = _HERE / "flatfield.sqlite"

# The degree ladder.  Ordered by cost, and deliberately carrying the (n,1)
# rungs that the commissioning bound chose: over this bound they fail on the
# anchor by more than a factor of three, and the table should show that rather
# than leave the old default looking untested.
DEGREES: Tuple[Tuple[int, int], ...] = (
    (1, 1), (2, 1), (1, 2), (2, 2), (3, 2), (3, 3), (4, 4),
)

# Exposure adequacy, as fractions of the usable signal range (RAW_MAX minus the
# black level).  A cell is under-exposed when its dimmest pixels carry too few
# electrons to measure, and over-exposed when its brightest are near the knee.
UNDER_FRAC, UNDER_PCTL = 0.10, 1.0
OVER_FRAC, OVER_PCTL = 0.90, 99.0

# Percentiles published to the instrument as `layermodels.p10` and `p90`, taken
# over the three patches together rather than the whole crop.  Wider than the
# 1/99 pair above on purpose: those are extreme-value statistics for spotting
# clipping and dead pixels in this analysis, whereas these describe the bulk of
# the pixels a measurement will average, and the capture path screens layers
# against them.  Two questions, two statistics — see Pool.patch_percentiles().
KEEP_LO_PCTL, KEEP_HI_PCTL = 10.0, 90.0

# Longest exposure the instrument will actually run, for the reachability flag
# on the minimum-exposure advice.  Nothing enforces this; it is the number the
# report measures "unreachable" against.
MAX_PRACTICAL_US = 200_000

# Block side for block_residual_pct().  The default 34 tiles a 130x183 crop as
# only 3x5; 26 gives 5x7 and still averages 676 pixels, so shot noise stays
# well under the structure being looked for.
BLOCK_PX = 26


# ── ROI geometry, addressed by rowid ────────────────────────────────

def load_rois_by_rowid(rowids: Sequence[int]) -> Dict[int, Rect]:
    """
    Return the named `roi` rows as stream-space Rects, keyed by rowid.

    Addressed by **rowid**, not by `active` or by name.  `roi.load_calibration()`
    returns every active=2 row, which here would add `white` (rowid 38) to a
    study that is deliberately about the two chips and the sample; and names are
    operator-editable, so 'chip1' has already meant two different rectangles.
    A rowid names one row of the table for as long as the table exists, which is
    what an archived analysis needs.

    Args:
        rowids: The `roi.rowid` values to load.

    Returns:
        {rowid: Rect}, each Rect carrying the row's name and its `role` as a
        `.role` attribute, matching what roi._to_rect() attaches.

    Raises:
        ValueError: A rowid is missing, was commissioned against a different
            stream or sensor, or is not Bayer-safe.

    Example:
        >>> load_rois_by_rowid((36,))[36].xywh()       # doctest: +SKIP
        (1012, 640, 68, 68)
    """
    marks = ",".join("?" * len(rowids))
    rows = db_manager.read_all(
        f"SELECT rowid, name, role, x, y, w, h, frame_w, frame_h, model "
        f"FROM roi WHERE rowid IN ({marks})", tuple(rowids))
    found = {r[0]: r for r in rows}

    missing = [r for r in rowids if r not in found]
    if missing:
        raise ValueError(
            f"roi table has no row(s) {missing}. The analysis names rois by "
            f"rowid, so a\nmissing row is a different study, not a fallback: "
            f"fix the rowid list or copy the\ninstrument's colorimeter.sqlite "
            f"over this desktop's.")

    out: Dict[int, Rect] = {}
    for rowid in rowids:
        _, name, role, x, y, w, h, fw, fh, model = found[rowid]
        if (fw, fh, model) != (STREAM_W, STREAM_H, STREAM_MODEL):
            raise ValueError(
                f"roi rowid {rowid} ('{name}') was commissioned against "
                f"{fw}x{fh} {model}, not {STREAM_W}x{STREAM_H} "
                f"{STREAM_MODEL}. Rescaling it would be a guess; recommission "
                f"the row instead.")
        rect = Rect(x, y, w, h, name=name)
        if not roi_mod.is_bayer_safe(rect):
            raise ValueError(
                f"roi rowid {rowid} ('{name}') is {rect.xywh()}, which is not "
                f"Bayer-safe.\nAn odd origin shifts the mosaic phase and "
                f"relabels every layer; an odd size makes\n//2 drop a row or "
                f"column. All four values must be even.")
        rect.role = role or ""
        out[rowid] = rect
    return out


@dataclass
class Geometry:
    """
    The target bound and the patches inside it, in layer coordinates.

    Attributes:
        bound: The target `bound` rectangle in stream space.
        bound_rowid: Its `roi.rowid`.
        patches: A flatfield.Patches over the crop, so every metric in
            flatfield.py can be reused unchanged.
        rowid_of: {patch name: roi.rowid}, for the database rows.
        chip1, chip2, sample: Patch names filled from the roles gray1, gray2
            and sample.  None when the set does not carry that role.
        t: Fractional x-position of the sample centre between the two chip
            centres, 0 at chip1 and 1 at chip2.  None without both chips.
    """
    bound: Rect
    bound_rowid: int
    patches: Patches
    rowid_of: Dict[str, int]
    chip1: Optional[str] = None
    chip2: Optional[str] = None
    sample: Optional[str] = None
    t: Optional[float] = None


def build_geometry(rowids: Sequence[int], bound_rowid: int) -> Geometry:
    """
    Resolve the requested rois into a target bound plus patches inside it.

    The rowid equal to `bound_rowid` becomes the crop and is dropped from the
    patch set — it is the region the analysis happens *in*, not a rectangle
    measured within it.  This mirrors flatfield.load_patches(), which drops the
    bound role for the same reason.

    Args:
        rowids: Every `roi.rowid` in the study, including the bound's.
        bound_rowid: Which of them is the bound.

    Returns:
        A Geometry.

    Raises:
        ValueError: The bound rowid is not in `rowids`, a patch falls outside
            the bound, or the roles needed for anchoring are absent.

    Example:
        >>> g = build_geometry((32, 35, 36, 39), 39)   # doctest: +SKIP
        >>> g.patches.rects['chip1'], round(g.t, 4)    # doctest: +SKIP
        ((10, 26, 34, 34), 0.4627)
    """
    if bound_rowid not in rowids:
        raise ValueError(
            f"bound rowid {bound_rowid} is not in roi_rowid={tuple(rowids)}; "
            f"add it so the crop\nand the patches come from one list.")

    rois = load_rois_by_rowid(rowids)
    bound = rois[bound_rowid]

    rects, roles, rowid_of, dropped = {}, {}, {}, []
    for rowid, r in rois.items():
        if rowid == bound_rowid:
            continue
        if not roi_mod.contains_rect(bound, r):
            dropped.append(f"{r.name}(rowid {rowid})")
            continue
        rects[r.name] = roi_mod.to_layer_rect_within(r, bound).xywh()
        roles[r.name] = getattr(r, "role", "")
        rowid_of[r.name] = rowid
    if dropped:
        raise ValueError(
            f"roi(s) {', '.join(dropped)} fall outside bound "
            f"{bound.xywh()}, so no archive holds\npixels for them. Drop them "
            f"from roi_rowid, or analyse a bound that contains them.")

    # The sample is the reference: the measurement the instrument makes is
    # sample-over-chips, so criterion P reads as "how far does each chip sit
    # from the sample after correction" — the error the flat field injects.
    by_role = {role: name for name, role in roles.items()}
    sample = by_role.get("sample")
    chip1, chip2 = by_role.get("gray1"), by_role.get("gray2")
    if not (sample and chip1 and chip2):
        raise ValueError(
            f"anchoring needs one roi of each role sample, gray1 and gray2; "
            f"got {roles}.\nRoles, not names, decide this — 'chip1' has been "
            f"two different rectangles already.")

    def centre_x(name: str) -> float:
        x, _, w, _ = rects[name]
        return x + (w - 1) / 2.0

    x1, x2 = centre_x(chip1), centre_x(chip2)
    if x1 == x2:
        raise ValueError(
            f"'{chip1}' and '{chip2}' share a column centre, so they cannot "
            f"anchor a field in x.")
    t = (centre_x(sample) - x1) / (x2 - x1)

    patches = Patches(rects=rects, roles=roles, reference=sample, dropped=[])
    return Geometry(bound=bound, bound_rowid=bound_rowid, patches=patches,
                    rowid_of=rowid_of, chip1=chip1, chip2=chip2,
                    sample=sample, t=t)


def subcrop_slices(capture_bound: Rect,
                   target_bound: Rect) -> Tuple[slice, slice]:
    """
    Return the layer-space slices cutting `target_bound` out of a stored crop.

    The archives store layers already cropped to the bound in force when they
    were captured, so re-analysing over a smaller interior bound is a second
    crop — in layer coordinates, where both the origin offset and the size
    halve.  Both bounds must be Bayer-safe (checked by load_rois_by_rowid), so
    the halved offset lands on a whole layer pixel and the mosaic phase is
    preserved.

    Args:
        capture_bound: The bound the archive was cropped to, in stream space.
        target_bound: The bound to analyse, in stream space.

    Returns:
        (row_slice, col_slice) to index a stored layer array with.

    Raises:
        ValueError: The target is not wholly inside the capture bound.

    Example:
        >>> subcrop_slices(Rect(806, 386, 732, 728), Rect(992, 588, 366, 260))
        (slice(101, 231, None), slice(93, 276, None))
    """
    if not roi_mod.contains_rect(capture_bound, target_bound):
        raise ValueError(
            f"target bound {target_bound.xywh()} is not inside the capture "
            f"bound {capture_bound.xywh()};\nthe archive holds no pixels for "
            f"the difference.")
    r0 = (target_bound.y - capture_bound.y) // 2
    c0 = (target_bound.x - capture_bound.x) // 2
    return (slice(r0, r0 + target_bound.h // 2),
            slice(c0, c0 + target_bound.w // 2))


# ── Captures: the archives, opened once and subcropped ──────────────

@dataclass
class Capture:
    """
    One open archive, with the subcrop that maps it onto the target bound.

    Attributes:
        name: The archive's basename, as it appears in reports and db rows.
        path: Resolved path to the .h5 file.
        placement: Which card placement this capture belongs to.  Captures
            sharing a placement are averaged before pooling, so an untouched
            repeat pair does not outvote a single re-placement.
        reader: Open Hdf5Reader.
        slices: (row_slice, col_slice) into the stored layer arrays.
    """
    name: str
    path: Path
    placement: str
    reader: Hdf5Reader
    slices: Tuple[slice, slice]


def open_captures(files: Sequence[Sequence], target_bound: Rect,
                  captures_dir: Path = DEFAULT_CAPTURES_DIR) -> List[Capture]:
    """
    Open every archive and work out its subcrop onto the target bound.

    Each entry is (filename, capture_bound_rowid) or
    (filename, capture_bound_rowid, placement).  The bound rowid is carried per
    file rather than read from the archive because version-1 archives do not
    record their geometry; where an archive *does* stamp its bound, the two are
    compared and a disagreement is refused rather than guessed at.

    Args:
        files: The (filename, bound_rowid[, placement]) entries.
        target_bound: The bound being analysed, in stream space.
        captures_dir: Directory bare filenames resolve against.

    Returns:
        A list of open Captures, in the order given.  The caller must close
        them; `analyze()` does so in a finally block.

    Raises:
        ValueError: An archive's stamped bound disagrees with the rowid given
            for it, or the target does not fit inside it.
        FileNotFoundError: An archive is missing.
    """
    bound_rowids = {int(e[1]) for e in files}
    bounds = load_rois_by_rowid(sorted(bound_rowids))

    out: List[Capture] = []
    for entry in files:
        filename, bound_rowid = str(entry[0]), int(entry[1])
        placement = str(entry[2]) if len(entry) > 2 else filename
        path = ff_mod.resolve_h5_path(filename, captures_dir)
        reader = Hdf5Reader(path)
        reader.__enter__()
        try:
            cap_bound = bounds[bound_rowid]
            stamped = ff_mod.archive_bound(reader)
            if stamped is not None and stamped != cap_bound.xywh():
                raise ValueError(
                    f"{path.name} stamps bound {stamped} but was listed "
                    f"against roi rowid {bound_rowid} = {cap_bound.xywh()}.\n"
                    f"The archive knows its own geometry; correct the rowid "
                    f"in the file list.")
            out.append(Capture(name=path.name, path=path, placement=placement,
                               reader=reader,
                               slices=subcrop_slices(cap_bound, target_bound)))
        except Exception:
            reader.__exit__(None, None, None)
            for c in out:
                c.reader.__exit__(None, None, None)
            raise
    return out


# ── Pooling: many captures into one frame ───────────────────────────

@dataclass
class Pool:
    """
    One (LED, exposure, Bayer layer) cell, pooled across every capture.

    Attributes:
        frame: The pooled signal as a plain flatfield.Frame, so every metric
            and fitter in flatfield.py applies to it unchanged.  Its `signal`
            is the per-pixel median across placement groups and its `mask` is
            the AND of every capture's mask.
        by_group: {placement: signal}, each the pixel-wise mean of that
            placement's captures.  Kept for leave-one-out and for the
            group-to-group spread.
        by_file: {archive name: signal}, for per-capture diagnostics such as
            the rotation control.
        black: Median dark level in DN, measured from the subtracted darks.
        exposure_us: Exposure the captures agree on.
        dark_links: {archive name: dark group actually subtracted}.
        led, label, channel: What this cell is.
    """
    frame: Frame
    by_group: Dict[str, np.ndarray]
    by_file: Dict[str, np.ndarray]
    black: float
    exposure_us: int
    dark_links: Dict[str, str]
    led: str
    label: str
    channel: str

    @property
    def full_scale(self) -> float:
        """Usable signal range in DN: the ceiling less the black level."""
        return RAW_MAX - self.black

    @property
    def groups(self) -> List[str]:
        """Placement names, in a stable order."""
        return list(self.by_group)

    def patch_means(self, rects: Dict[str, Tuple[int, int, int, int]],
                    ) -> Dict[str, Dict[str, float]]:
        """
        Return {archive name: {patch name: mean signal}} over the raw captures.

        Args:
            rects: Patch rectangles in crop layer coordinates.

        Returns:
            Nested dict of patch means in DN, one inner dict per capture.
        """
        return {f: {n: ff_mod.patch_mean(sig, r) for n, r in rects.items()}
                for f, sig in self.by_file.items()}

    def patch_percentiles(self, rects: Dict[str, Tuple[int, int, int, int]],
                          lo: float, hi: float) -> Tuple[float, float]:
        """
        Return two percentiles of the pooled signal over the patches together.

        Deliberately over the union of the patch pixels rather than the whole
        crop: whether an exposure is worth capturing is a question about the
        three little squares that get measured, not about the surrounding
        field, which is a good deal darker at its corners and would drag the
        low percentile down for no reason.

        Masked pixels are excluded, so a dead pixel cannot masquerade as
        under-exposure.  Every patch contributes every one of its pixels, so a
        larger patch weighs proportionally more — which is what is wanted here,
        since the question is about the population of pixels that will be
        averaged.

        Args:
            rects: Patch rectangles in crop layer coordinates.
            lo: Lower percentile, 0-100.
            hi: Upper percentile, 0-100.

        Returns:
            (value at `lo`, value at `hi`) in DN.  (nan, nan) if the patches
            hold no unmasked pixels at all.

        Example:
            >>> pool.patch_percentiles(geom.patches.rects, 10.0, 90.0)
            ...                                             # doctest: +SKIP
            (402.1, 790.4)
        """
        signal, mask = self.frame.signal, self.frame.mask
        pixels: List[np.ndarray] = []
        for x, y, w, h in rects.values():
            sub = signal[y:y + h, x:x + w]
            sub_mask = mask[y:y + h, x:x + w]
            pixels.append(sub[sub_mask] if sub_mask.any() else sub.ravel())
        pooled = np.concatenate(pixels) if pixels else np.empty(0)
        if pooled.size == 0:
            return (float("nan"), float("nan"))
        return (float(np.percentile(pooled, lo)),
                float(np.percentile(pooled, hi)))


def load_pool(captures: Sequence[Capture], led: str, label: str,
              channel: str) -> Pool:
    """
    Load one cell from every capture and pool it into a single frame.

    Per capture: read the lit layer and the dark that `flatfield.find_dark_link`
    selects, subcrop both to the target bound, subtract the full dark *frame*
    pixel by pixel (a dark mean would throw away the fixed-pattern component,
    which is exactly the part that would masquerade as illumination structure),
    and mask with the same rule flatfield.load_frame() uses.

    Then, in two steps:
      1. average pixel-wise **within** each placement group, so an untouched
         repeat pair counts once and not twice;
      2. take the per-pixel **median across the group images**.

    The median is what removes dust, card blemishes and cosmic hits, with no
    threshold to tune.  It costs a little efficiency against the mean, which
    does not matter: pixel noise here is ~3 % but the quantities that get used
    are 34x34 patch means, where it averages down to ~0.1 %.

    Args:
        captures: Open captures, already subcropped.
        led: LED colour name.
        label: Exposure label, e.g. 'e1'.
        channel: One of BAYER_CHANNELS.

    Returns:
        A Pool.

    Raises:
        KeyError: The lit dataset or its dark counterpart is absent, or was
            measured and deliberately not stored — see flatfield.read_layer().
            analyze() catches this and moves on, which is what makes a pooled
            run tolerate archives that dropped their under-exposed layers.
        ValueError: The captures disagree on exposure time, which would make
            the pooled frame a blend of two different measurements.
    """
    by_file: Dict[str, np.ndarray] = {}
    dark_links: Dict[str, str] = {}
    masks: List[np.ndarray] = []
    blacks: List[float] = []
    exposures: Dict[str, int] = {}
    group_members: Dict[str, List[np.ndarray]] = {}

    for cap in captures:
        r = cap.reader
        dark_link = ff_mod.find_dark_link(r, led, label)
        if dark_link is None:
            raise KeyError(
                f"{cap.name}: /leds/{led}/dark has no group matching "
                f"'{label}'")
        lit = ff_mod.read_layer(
            r, f"/leds/{led}/lit/{label}/{channel}")[cap.slices]
        dark = ff_mod.read_layer(
            r, f"/leds/{led}/dark/{dark_link}/{channel}")[cap.slices]

        signal = lit - dark
        masks.append((lit < SATURATED_DN) & (signal > 0.0))
        blacks.append(float(np.median(dark)))
        by_file[cap.name] = signal
        dark_links[cap.name] = dark_link
        exposures[cap.name] = ff_mod.lit_exposure_us(r, led, label)
        group_members.setdefault(cap.placement, []).append(signal)

    distinct = set(exposures.values())
    if len(distinct) != 1:
        raise ValueError(
            f"{led}/{label}/{channel}: captures disagree on exposure "
            f"({exposures}).\nPooling them would average two different "
            f"measurements; split the file list instead.")

    by_group = {name: np.mean(members, axis=0)
                for name, members in group_members.items()}
    pooled = np.median(np.stack(list(by_group.values())), axis=0)
    mask = np.logical_and.reduce(masks)

    frame = Frame(signal=pooled, mask=mask, led=led, label=label,
                  channel=channel, exposure_us=distinct.pop(),
                  dark_link=", ".join(sorted(set(dark_links.values()))))
    return Pool(frame=frame, by_group=by_group, by_file=by_file,
                black=float(np.median(blacks)),
                exposure_us=frame.exposure_us, dark_links=dark_links,
                led=led, label=label, channel=channel)


# ── Exposure adequacy ───────────────────────────────────────────────

ACCEPTABLE, OVER, UNDER = "acceptable", "over", "under"


@dataclass
class Cell:
    """
    One pooled cell's exposure verdict and the numbers behind it.

    Attributes:
        pool: The cell itself.
        group: ACCEPTABLE, OVER or UNDER.
        p_lo: Signal at UNDER_PCTL, over unmasked pixels only — the dim end,
            which is where a dead pixel would sit and would otherwise be read
            as under-exposure.
        p_hi: Signal at OVER_PCTL, over *all* pixels — the bright end, where
            clipped pixels live; excluding them would hide the clipping the
            statistic exists to find.
        us_min: Exposure that would lift p_lo to UNDER_FRAC of full scale, in
            microseconds.  None unless the cell is under-exposed.
        us_max: Exposure that would hold p_hi at OVER_FRAC of full scale.
            None unless the cell is over-exposed.
        reachable: Whether the advised exposure is under MAX_PRACTICAL_US.
        noise_pct: The criterion-P noise floor this cell carries.
        spread_pct: Largest chip/sample patch-mean spread across the raw
            captures, relative to the pooled mean.
        patch_p10, patch_p90: KEEP_LO_PCTL and KEEP_HI_PCTL of the signal over
            the three patches together — the pair published to the instrument
            as `layermodels.p10` and `p90`.  Not the same statistic as p_lo and
            p_hi above, which are 1/99 over the whole crop; see
            Pool.patch_percentiles() for why the two differ.
    """
    pool: Pool
    group: str
    p_lo: float
    p_hi: float
    us_min: Optional[float]
    us_max: Optional[float]
    reachable: bool
    noise_pct: float
    spread_pct: float
    patch_p10: float
    patch_p90: float


def classify(pool: Pool, patches: Patches) -> Cell:
    """
    Decide whether a pooled cell is usable, over-exposed or under-exposed.

    The verdict belongs to the **cell**, not to individual captures.  Every
    capture in a suite shares one exposure ladder — verified identical across
    all five archives and all thirty LED/rung combinations — so they land in
    the same group together.  A disagreement would be a data fault, and
    load_pool() refuses it outright rather than quietly splitting the cell.

    Both exposure suggestions are a single linear scaling of the exposure
    actually used.  That is sound because the sensor measures linear to better
    than 1.7 % across the e0/e1/e2 rungs, so there is nothing to gain from
    borrowing the neighbouring rung's frame.

    Args:
        pool: The pooled cell.
        patches: The patch set, for the criterion-P noise floor.

    Returns:
        A Cell.

    Example:
        >>> classify(pool, patches).group           # doctest: +SKIP
        'acceptable'
    """
    signal, mask = pool.frame.signal, pool.frame.mask
    usable = signal[mask] if mask.any() else signal
    p_lo = float(np.percentile(usable, UNDER_PCTL))
    p_hi = float(np.percentile(signal, OVER_PCTL))
    full = pool.full_scale

    us_min = us_max = None
    if (pool.frame.masked_fraction > MAX_MASKED_FRACTION
            or p_hi > OVER_FRAC * full):
        group = OVER
        us_max = pool.exposure_us * (OVER_FRAC * full) / max(p_hi, 1e-9)
    elif p_lo < UNDER_FRAC * full:
        group = UNDER
        us_min = pool.exposure_us * (UNDER_FRAC * full) / max(p_lo, 1e-9)
    else:
        group = ACCEPTABLE

    advised = us_min if us_min is not None else us_max
    means = pool.patch_means(patches.rects)
    spread = 0.0
    for name in patches.rects:
        vals = [m[name] for m in means.values()]
        spread = max(spread, 100.0 * (max(vals) - min(vals))
                     / max(np.mean(vals), 1e-9))

    patch_p10, patch_p90 = pool.patch_percentiles(
        patches.rects, KEEP_LO_PCTL, KEEP_HI_PCTL)

    return Cell(pool=pool, group=group, p_lo=p_lo, p_hi=p_hi,
                us_min=us_min, us_max=us_max,
                reachable=advised is None or advised <= MAX_PRACTICAL_US,
                noise_pct=ff_mod.p_noise_pct(pool.frame, patches),
                spread_pct=spread,
                patch_p10=patch_p10, patch_p90=patch_p90)


# ── Anchoring: the deliverable ──────────────────────────────────────

@dataclass
class Anchor:
    """
    Two scalars that turn the two chip means into an illumination divisor.

    The runtime form of a flat field for this geometry.  Instead of evaluating
    a polynomial on the instrument, the measurement computes

        divisor = w1 * mean(chip1) + w2 * mean(chip2)

    and divides the corrected sample mean by it.  The polynomial's only job is
    to have produced w1 and w2.

    Attributes:
        form: '1anchor' or '2anchor'; see make_anchors().
        w1, w2: The weights on chip1 and chip2.
        g_chip1, g_sample, g_chip2: The fitted field's mean gain over each
            patch, unit-mean over the crop.
        t: The sample's fractional position between the chips.
    """
    form: str
    w1: float
    w2: float
    g_chip1: float
    g_sample: float
    g_chip2: float
    t: float

    @property
    def curvature(self) -> float:
        """
        How far the sample sits off the straight line joining the two chips.

        The number this whole analysis exists to produce:

            rho = g_sample / ((1 - t) * g_chip1 + t * g_chip2)

        Exactly 1.0 when the illumination is linear in x across the chips —
        which is when a polynomial would buy nothing, because interpolating
        the two chip readings would already give the sample's illumination.
        It is a property of the fitted field alone, so both anchor forms
        report the same value.  Measured here: about 1.02 for most LEDs, and
        0.967 for violet, whose field tilts the other way.

        Not to be confused with `w_sum`.  The two agree to first order and
        differ at second order in the chip-to-chip gain spread — 0.13 % on
        this geometry — because w_sum combines the chips reciprocally while
        this interpolates them linearly.

        The interpolation is in x alone, so rho also absorbs whatever the
        patches' small y offsets pick up from a y gradient: chip2 (roi 35,
        y = 638) sits one Bayer row above chip1 and sample (y = 640), which
        is worth about 0.02 % over this bound's 4.6 % y span.  That is a term,
        not a rounding error, but it is twenty times under the card-placement
        floor and pointing rho at the sample's actual position is what the
        measurement needs regardless of which axis the departure came from.
        """
        return self.g_sample / ((1.0 - self.t) * self.g_chip1
                                + self.t * self.g_chip2)

    @property
    def w_sum(self) -> float:
        """Total weight the divisor puts on the two chip means, w1 + w2."""
        return self.w1 + self.w2

    def predict(self, m_chip1: float, m_chip2: float) -> float:
        """
        Return the estimated illumination divisor at the sample.

        Args:
            m_chip1: Measured mean over chip1, in DN.
            m_chip2: Measured mean over chip2, in DN.

        Returns:
            The divisor, in DN.
        """
        return self.w1 * m_chip1 + self.w2 * m_chip2


def patch_gains(ff: ff_mod.FlatField,
                rects: Dict[str, Tuple[int, int, int, int]],
                ) -> Dict[str, float]:
    """
    Return each patch's mean gain under a fitted field.

    Args:
        ff: The fitted field.
        rects: Patch rectangles in crop layer coordinates.

    Returns:
        {patch name: mean gain}, unit-mean over the fit extent.
    """
    return {n: float(ff.gain_for_patch(r).mean()) for n, r in rects.items()}


def make_anchors(gains: Dict[str, float], geom: Geometry) -> Dict[str, Anchor]:
    """
    Build both anchoring forms from one field's patch gains.

    **1-anchor** treats the polynomial as fully trusted and fits a single
    brightness scale to both chips by least squares.  Any disagreement between
    the chips then shows up as a residual instead of being absorbed, which
    makes it a direct check on the polynomial's tilt.

    **2-anchor** re-fits offset *and* tilt from the chips every measurement,
    trusting the polynomial only for curvature.  The sample sits between the
    chips (t = 0.46), so interpolating rather than extrapolating: the noise
    amplification is sqrt((1-t)^2 + t^2) = 0.71, the same as averaging the two
    chips, so the extra freedom costs nothing in precision.

    Which is better is an empirical question, answered per cell by
    leave-one-placement-out — see loo_ladder().

    Args:
        gains: {patch name: mean gain}, from patch_gains().
        geom: Supplies the chip/sample names and the interpolation position t.

    Returns:
        {'1anchor': Anchor, '2anchor': Anchor}.
    """
    g1, gs, g2 = gains[geom.chip1], gains[geom.sample], gains[geom.chip2]
    t = geom.t
    denom = g1 * g1 + g2 * g2
    one = Anchor("1anchor", gs * g1 / denom, gs * g2 / denom, g1, gs, g2, t)
    two = Anchor("2anchor", (1.0 - t) * gs / g1, t * gs / g2, g1, gs, g2, t)
    return {one.form: one, two.form: two}


ANCHOR_FORMS = ("1anchor", "2anchor")


# ── Degree selection: leave one placement out ───────────────────────

@dataclass
class Rung:
    """
    One degree's scores for one cell, in sample and out.

    Attributes:
        deg_y, deg_x, n_terms: The basis.
        field: The field fitted over every placement group.
        gains: Its mean gain per patch.
        anchors: {form: Anchor} derived from `gains` — the deliverable.
        fit_rms_pct: In-sample per-pixel residual.  Recorded, but it must not
            pick the degree: over this bound it moves 0.46 % across the whole
            ladder while the anchor error moves 2 %.
        block_rms_pct, block_max_pct: Block-mean residual over the crop.
        p_max_pct, s_max_pct: Criteria P and S, in sample.
        s_snr: The worst patch's ramp divided by the ramp pixel noise alone
            would have produced.  Near 1 the ramp is a fitting artifact; well
            above 1 the field really does leave a tilt inside the patch.  A
            bare S of 1.7 % is unreadable without it.
        loo_err: {form: [signed % error per held-out placement]}.
        loo_med, loo_max: {form: median and max |error| over the folds}.
    """
    deg_y: int
    deg_x: int
    n_terms: int
    field: ff_mod.FlatField
    gains: Dict[str, float]
    anchors: Dict[str, Anchor]
    fit_rms_pct: float
    block_rms_pct: float
    block_max_pct: float
    p_max_pct: Optional[float]
    s_max_pct: float
    s_snr: float
    loo_err: Dict[str, List[float]] = dc_field(default_factory=dict)
    loo_med: Dict[str, float] = dc_field(default_factory=dict)
    loo_max: Dict[str, float] = dc_field(default_factory=dict)

    @property
    def n_folds(self) -> int:
        """Number of held-out placements the leave-one-out used."""
        return len(next(iter(self.loo_err.values()), ()))


def _frame_like(pool: Pool, signal: np.ndarray) -> Frame:
    """Return a Frame carrying `signal` with the pool's mask and identity."""
    return Frame(signal=signal, mask=pool.frame.mask, led=pool.led,
                 label=pool.label, channel=pool.channel,
                 exposure_us=pool.exposure_us,
                 dark_link=pool.frame.dark_link)


def _anchor_error_pct(anchor: Anchor, signal: np.ndarray,
                      geom: Geometry) -> float:
    """
    Return the signed % error of an anchored prediction on one image.

    Args:
        anchor: The weights to test.
        signal: The image to measure the three patches on.
        geom: Supplies the patch rectangles and names.

    Returns:
        100 * (predicted / measured - 1) at the sample patch.
    """
    rects = geom.patches.rects
    m1 = ff_mod.patch_mean(signal, rects[geom.chip1])
    m2 = ff_mod.patch_mean(signal, rects[geom.chip2])
    ms = ff_mod.patch_mean(signal, rects[geom.sample])
    return 100.0 * (anchor.predict(m1, m2) / ms - 1.0)


def loo_ladder(pool: Pool, geom: Geometry,
               degrees: Sequence[Tuple[int, int]] = DEGREES) -> List[Rung]:
    """
    Score every degree on this cell, in sample and by leave-one-placement-out.

    Each fold fits the remaining placements' median and then predicts the
    held-out placement's *sample* mean from its own two chip means — exactly
    the operation the instrument will perform, on a card it did not fit.  That
    is criterion T from `tex/colorimeter_flatfield.tex`, applied to the
    quantity that actually gets used rather than to a pixel residual.

    With one placement there is nothing to hold out; the rungs come back with
    in-sample scores only and `n_folds` 0.

    Args:
        pool: The pooled cell.
        geom: The target geometry.
        degrees: The (deg_y, deg_x) ladder to walk.

    Returns:
        One Rung per degree, in ladder order.

    Example:
        >>> [r.loo_max['2anchor'] for r in loo_ladder(pool, geom)]  # doctest: +SKIP
        [1.71, 1.69, 0.58, 0.57, 0.52, 0.41, 0.31]
    """
    groups = pool.groups
    rungs: List[Rung] = []

    for deg_y, deg_x in degrees:
        ff = ff_mod.fit_field(pool.frame, deg_y, deg_x, "bound")
        gains = patch_gains(ff, geom.patches.rects)
        anchors = make_anchors(gains, geom)

        corrected = ff.correct(pool.frame)
        block_rms, block_max = ff_mod.block_residual_pct(corrected, BLOCK_PX)
        score = ff_mod.score_model(ff_mod.Model("bound", [ff]), pool.frame,
                                   geom.patches)

        rung = Rung(deg_y=deg_y, deg_x=deg_x, n_terms=ff.n_terms, field=ff,
                    gains=gains, anchors=anchors,
                    fit_rms_pct=ff.fit_rms_pct, block_rms_pct=block_rms,
                    block_max_pct=block_max, p_max_pct=score.p_max,
                    s_max_pct=score.s_max, s_snr=score.s_snr)

        rung.loo_err = {form: [] for form in ANCHOR_FORMS}
        if len(groups) > 1:
            for held in groups:
                train = np.median(
                    np.stack([pool.by_group[g] for g in groups if g != held]),
                    axis=0)
                t_ff = ff_mod.fit_field(_frame_like(pool, train), deg_y, deg_x,
                                        "bound")
                t_anchors = make_anchors(patch_gains(t_ff, geom.patches.rects),
                                         geom)
                for form in ANCHOR_FORMS:
                    rung.loo_err[form].append(
                        _anchor_error_pct(t_anchors[form],
                                          pool.by_group[held], geom))
        for form in ANCHOR_FORMS:
            errs = [abs(e) for e in rung.loo_err[form]]
            rung.loo_med[form] = float(np.median(errs)) if errs else float("nan")
            rung.loo_max[form] = float(max(errs)) if errs else float("nan")
        rungs.append(rung)

    return rungs


def choose_rung(rungs: Sequence[Rung], form: str) -> Rung:
    """
    Return the cheapest rung that no later rung beats by more than the floor.

    Criterion T: accept the smallest basis whose out-of-sample error stops
    improving by more than FLOOR_PCT (0.1 %, the measured repeatability floor).
    Spending free parameters below that buys noise, not accuracy.

    Falls back to the in-sample block residual when there were no folds to
    hold out, which happens only on a single-placement run.

    Args:
        rungs: The ladder, in cost order.
        form: Which anchor form to judge by.

    Returns:
        The chosen Rung.
    """
    scores = [r.loo_max[form] for r in rungs]
    if not np.isfinite(scores).all():
        return min(rungs, key=lambda r: r.block_rms_pct)
    for i, score in enumerate(scores):
        if score - min(scores[i:]) <= FLOOR_PCT:
            return rungs[i]
    return rungs[-1]


def choose_form(rung: Rung) -> str:
    """
    Return the anchor form with the lower out-of-sample error on this rung.

    Ties and NaNs go to '2anchor', which re-fits tilt from the chips and so
    degrades more gracefully when the card moves.

    Args:
        rung: The chosen rung.

    Returns:
        '1anchor' or '2anchor'.
    """
    a, b = rung.loo_max["1anchor"], rung.loo_max["2anchor"]
    return "1anchor" if np.isfinite(a) and np.isfinite(b) and a < b else "2anchor"


# ── Results: one lightweight record per cell ────────────────────────

@dataclass
class Result:
    """
    Everything worth keeping about one (LED, exposure, layer) cell.

    Deliberately holds no images.  A run visits 120 cells and each pool carries
    eight 130x183 float64 arrays, so keeping them all would cost ~180 MB for
    no purpose: the figures re-load the forty cells they actually draw.

    Attributes:
        led, label, channel, exposure_us: What the cell is.
        group: ACCEPTABLE, OVER or UNDER.
        mean, p_lo, p_hi, masked_pct, black: Exposure statistics in DN.
        patch_p10, patch_p90: The pair published as `layermodels.p10`/`p90`,
            taken over the three patches together — see
            Pool.patch_percentiles().
        noise_pct, spread_pct: The criterion-P noise floor and the largest
            patch-mean spread across raw captures.
        us_min, us_max, reachable: The exposure advice, if any.
        rungs: The degree ladder; empty unless the cell is acceptable.
        chosen: The selected rung, or None.
        form: The selected anchor form, or None.
        per_file_means: {archive: {patch: mean DN}}, for the rotation control.
        placement_of: {archive: placement}.
    """
    led: str
    label: str
    channel: str
    exposure_us: int
    group: str
    mean: float
    p_lo: float
    p_hi: float
    patch_p10: float
    patch_p90: float
    masked_pct: float
    black: float
    noise_pct: float
    spread_pct: float
    us_min: Optional[float]
    us_max: Optional[float]
    reachable: bool
    rungs: List[Rung] = dc_field(default_factory=list)
    chosen: Optional[Rung] = None
    form: Optional[str] = None
    per_file_means: Dict[str, Dict[str, float]] = dc_field(default_factory=dict)
    placement_of: Dict[str, str] = dc_field(default_factory=dict)

    @property
    def anchor(self) -> Optional[Anchor]:
        """The selected rung's anchor in the selected form, if any."""
        if self.chosen is None or self.form is None:
            return None
        return self.chosen.anchors[self.form]

    @property
    def advice(self) -> str:
        """One-line exposure advice for the reports."""
        if self.group == UNDER:
            reach = "" if self.reachable else "  UNREACHABLE"
            return (f"raise to >= {self.us_min:,.0f} us "
                    f"(x{self.us_min / self.exposure_us:.1f}){reach}")
        if self.group == OVER:
            return (f"lower to <= {self.us_max:,.0f} us "
                    f"(x{self.us_max / self.exposure_us:.2f})")
        return ""


def analyse_cell(pool: Pool, geom: Geometry,
                 degrees: Sequence[Tuple[int, int]],
                 placement_of: Dict[str, str]) -> Result:
    """
    Classify one pooled cell and, if it is usable, fit and score its ladder.

    Args:
        pool: The pooled cell.
        geom: The target geometry.
        degrees: The degree ladder to walk.
        placement_of: {archive name: placement}.

    Returns:
        A Result carrying no image arrays.
    """
    cell = classify(pool, geom.patches)
    result = Result(
        led=pool.led, label=pool.label, channel=pool.channel,
        exposure_us=pool.exposure_us, group=cell.group,
        mean=pool.frame.mean, p_lo=cell.p_lo, p_hi=cell.p_hi,
        patch_p10=cell.patch_p10, patch_p90=cell.patch_p90,
        masked_pct=100.0 * pool.frame.masked_fraction, black=pool.black,
        noise_pct=cell.noise_pct, spread_pct=cell.spread_pct,
        us_min=cell.us_min, us_max=cell.us_max, reachable=cell.reachable,
        per_file_means=pool.patch_means(geom.patches.rects),
        placement_of=dict(placement_of))

    if cell.group == ACCEPTABLE:
        result.rungs = loo_ladder(pool, geom, degrees)
        result.chosen = choose_rung(result.rungs, "2anchor")
        result.form = choose_form(result.chosen)
    return result


# ── Reports ─────────────────────────────────────────────────────────

def _pct(value: Optional[float], width: int = 6, dp: int = 2) -> str:
    """Format an optional percentage right-aligned, '-' when absent."""
    if value is None or not np.isfinite(value):
        return f"{'-':>{width}}"
    return f"{value:>{width}.{dp}f}"


def _preamble(captures: Sequence[Capture], geom: Geometry, title: str) -> str:
    """
    Return the common heading every report carries.

    Args:
        captures: The open captures.
        geom: The target geometry.
        title: The report's own title line.

    Returns:
        The heading text, ending in a blank line.
    """
    files = ", ".join(c.name for c in captures)
    places = {}
    for c in captures:
        places.setdefault(c.placement, []).append(c.name)
    rects = ", ".join(
        f"{n}(rowid {geom.rowid_of[n]})={r}"
        for n, r in sorted(geom.patches.rects.items()))
    return (
        f"{title}\n"
        f"{'=' * len(title)}\n"
        f"file       : {files}\n"
        f"placements : "
        + "; ".join(f"{p} [{', '.join(f)}]" for p, f in places.items()) + "\n"
        + f"bound      : rowid {geom.bound_rowid} = {geom.bound.xywh()} "
        f"-> layers {geom.bound.h // 2}x{geom.bound.w // 2}\n"
        f"patches    : {rects}\n"
        f"anchor     : sample sits at t = {geom.t:.4f} between "
        f"{geom.chip1} and {geom.chip2}\n\n")


def write_cells_report(path: Path, results: Sequence[Result],
                       captures: Sequence[Capture], geom: Geometry) -> None:
    """
    Write the per-cell exposure table: what each cell reads and what to do.

    Args:
        path: Output file.
        results: Every cell, in visit order.
        captures: The open captures, for the heading.
        geom: The target geometry.

    Returns:
        None.
    """
    lines = [_preamble(captures, geom, "Exposure adequacy, cell by cell")]
    lines.append(
        f"Groups: '{ACCEPTABLE}' fits; '{UNDER}' has p{UNDER_PCTL:g} below "
        f"{UNDER_FRAC:.0%} of full scale;\n'{OVER}' has p{OVER_PCTL:g} above "
        f"{OVER_FRAC:.0%} of full scale or over "
        f"{100 * MAX_MASKED_FRACTION:.0f}% of pixels masked.\n"
        f"Advice is one linear scaling of the exposure used; the sensor is "
        f"linear to under 1.7 %\nacross the ladder, so the neighbouring rung "
        f"adds nothing.\n\n")
    lines.append(
        f"p01/p99 are over the whole crop, and decide the group. p10/p90 are "
        f"over the three\npatches together, and are what reaches the "
        f"instrument as layermodels.p10/p90.\n\n")
    lines.append(f"  {'led':<11} {'e#':<3} {'ch':<3} {'exp_us':>7} "
                 f"{'group':<10} {'mean':>7} {'p01':>7} {'p99':>7} "
                 f"{'p10':>7} {'p90':>7} "
                 f"{'mask%':>6} {'sprd%':>6} {'noise%':>7}  advice\n")
    for r in results:
        lines.append(
            f"  {r.led:<11} {r.label:<3} {r.channel:<3} {r.exposure_us:>7} "
            f"{r.group:<10} {r.mean:>7.1f} {r.p_lo:>7.1f} {r.p_hi:>7.1f} "
            f"{r.patch_p10:>7.1f} {r.patch_p90:>7.1f} "
            f"{r.masked_pct:>6.2f} {_pct(r.spread_pct)} "
            f"{_pct(r.noise_pct, 7, 3)}  {r.advice}\n")

    counts = {g: sum(1 for r in results if r.group == g)
              for g in (ACCEPTABLE, OVER, UNDER)}
    lines.append(f"\n{len(results)} cells: "
                 + ", ".join(f"{n} {g}" for g, n in counts.items()) + "\n")
    if not counts[OVER]:
        lines.append(
            "\nNo cell is over-exposed: nothing in this bound reaches the "
            "sensor's knee at any\nrung of the ladder. Said explicitly "
            "because an empty section and an unrun test look\nthe same in a "
            "report.\n")
    path.write_text("".join(lines))


def write_fits_report(path: Path, results: Sequence[Result],
                      captures: Sequence[Capture], geom: Geometry) -> None:
    """
    Write the chosen fit and the two anchor weights for every usable cell.

    Args:
        path: Output file.
        results: Every cell; only the acceptable ones appear.
        captures: The open captures, for the heading.
        geom: The target geometry.

    Returns:
        None.
    """
    lines = [_preamble(captures, geom,
                       "Chosen fits and anchor weights")]
    lines.append(
        "divisor = w1 * mean(chip1) + w2 * mean(chip2).  'rho' is the "
        "curvature correction,\ng_sample / ((1-t)*g_chip1 + t*g_chip2): 1.0 "
        "exactly when the illumination is linear\nin x across the chips, so "
        "the departure from 1 is what the polynomial buys. It is a\nproperty "
        "of the field, so both forms share it; 'wsum' = w1 + w2 does not, and "
        "differs\nfrom rho at second order in the chip-to-chip gain spread.\n"
        "'form' is whichever anchoring won on leave-one-placement-out; both "
        "are listed.\n"
        "'S/nz' is criterion S over the ramp pixel noise alone would have "
        "produced: near 1\nmeans the ramp is a fitting artifact, not a "
        "residual tilt.  'loo_med' is the more\nrobust of the two "
        "leave-one-out figures; 'loo_max' is one fold's worst case.\n\n")
    lines.append(f"  {'led':<11} {'e#':<3} {'ch':<3} {'deg':<6} {'nt':>3} "
                 f"{'resid%':>7} {'blk%':>6} {'P%':>6} {'S%':>6} "
                 f"{'S/nz':>5} "
                 f"{'g_chip1':>8} {'g_smp':>8} {'g_chip2':>8} "
                 f"{'form':<8} {'w1':>7} {'w2':>7} {'wsum':>7} {'rho':>7} "
                 f"{'loo_med':>8} {'loo_max':>8}\n")
    for r in results:
        if r.chosen is None:
            continue
        c, a = r.chosen, r.anchor
        lines.append(
            f"  {r.led:<11} {r.label:<3} {r.channel:<3} "
            f"({c.deg_y},{c.deg_x}){'':<2} {c.n_terms:>3} "
            f"{c.fit_rms_pct:>7.2f} {c.block_rms_pct:>6.2f} "
            f"{_pct(c.p_max_pct)} {_pct(c.s_max_pct)} "
            f"{_pct(c.s_snr, 5, 1)} "
            f"{c.gains[geom.chip1]:>8.4f} {c.gains[geom.sample]:>8.4f} "
            f"{c.gains[geom.chip2]:>8.4f} {r.form:<8} "
            f"{a.w1:>7.4f} {a.w2:>7.4f} {a.w_sum:>7.4f} "
            f"{a.curvature:>7.4f} "
            f"{_pct(c.loo_med[r.form], 8, 3)} "
            f"{_pct(c.loo_max[r.form], 8, 3)}\n")

    lines.append("\nBoth anchor forms at the chosen degree:\n\n")
    lines.append(f"  {'led':<11} {'e#':<3} {'ch':<3} "
                 f"{'1a w1':>7} {'1a w2':>7} {'1a sum':>7} {'1a loo':>7}  "
                 f"{'2a w1':>7} {'2a w2':>7} {'2a sum':>7} {'2a loo':>7}\n")
    for r in results:
        if r.chosen is None:
            continue
        one, two = r.chosen.anchors["1anchor"], r.chosen.anchors["2anchor"]
        lines.append(
            f"  {r.led:<11} {r.label:<3} {r.channel:<3} "
            f"{one.w1:>7.4f} {one.w2:>7.4f} {one.w_sum:>7.4f} "
            f"{_pct(r.chosen.loo_max['1anchor'], 7, 3)}  "
            f"{two.w1:>7.4f} {two.w2:>7.4f} {two.w_sum:>7.4f} "
            f"{_pct(r.chosen.loo_max['2anchor'], 7, 3)}\n")
    path.write_text("".join(lines))


def write_degrees_report(path: Path, results: Sequence[Result],
                         captures: Sequence[Capture], geom: Geometry) -> None:
    """
    Write the full leave-one-placement-out ladder for every usable cell.

    Args:
        path: Output file.
        results: Every cell; only the acceptable ones appear.
        captures: The open captures, for the heading.
        geom: The target geometry.

    Returns:
        None.
    """
    lines = [_preamble(captures, geom,
                       "Degree ladder, leave one placement out")]
    lines.append(
        f"Each fold fits the remaining placements and predicts the held-out "
        f"placement's sample\nmean from its own two chip means — the "
        f"operation the instrument performs, on a card\nit did not fit. "
        f"Criterion T: take the cheapest basis that no richer one beats by "
        f"more\nthan {FLOOR_PCT} %.\n\n"
        f"In-sample residual is printed but must not choose: over this bound "
        f"it moves under\nhalf a percent across the whole ladder while the "
        f"anchor error moves two.\n\n")
    for r in results:
        if not r.rungs:
            continue
        lines.append(f"{r.led}/{r.label}/{r.channel}  "
                     f"{r.exposure_us} us  mean {r.mean:.1f} DN  "
                     f"{r.rungs[0].n_folds} folds\n")
        lines.append(f"    {'deg':<6} {'nt':>3} {'resid%':>7} {'blk%':>6} "
                     f"{'P%':>6} {'1a med':>7} {'1a max':>7} "
                     f"{'2a med':>7} {'2a max':>7} {'rho':>7}\n")
        for c in r.rungs:
            mark = "  <-- chosen" if c is r.chosen else ""
            lines.append(
                f"    ({c.deg_y},{c.deg_x}){'':<2} {c.n_terms:>3} "
                f"{c.fit_rms_pct:>7.2f} {c.block_rms_pct:>6.2f} "
                f"{_pct(c.p_max_pct)} "
                f"{_pct(c.loo_med['1anchor'], 7, 3)} "
                f"{_pct(c.loo_max['1anchor'], 7, 3)} "
                f"{_pct(c.loo_med['2anchor'], 7, 3)} "
                f"{_pct(c.loo_max['2anchor'], 7, 3)} "
                f"{c.anchors['2anchor'].curvature:>7.4f}{mark}\n")
        lines.append("\n")
    path.write_text("".join(lines))


def rotation_control(results: Sequence[Result], geom: Geometry,
                     rotated: Sequence[str]) -> List[Tuple[str, float, float,
                                                           float, float]]:
    """
    Separate illumination from chip reflectance using the 180-degree captures.

    Turning the card end for end swaps which physical chip sits under which
    ROI but leaves the instrument's illumination where it was.  Writing r for
    the measured chip1/chip2 ratio, A for the chips' own reflectances and L
    for the illumination at the two ROI positions:

        r(0 deg)   = (A_a / A_b) * (L1 / L2)
        r(180 deg) = (A_b / A_a) * (L1 / L2)

    so the product isolates the illumination and the quotient isolates the
    card.  This is what licenses pooling every capture into one baseline: if
    the chips differed materially, r would visibly flip between orientations.

    Args:
        results: Every cell; only acceptable ones are used.
        geom: Supplies the chip names.
        rotated: Placement names imaged at 180 degrees.

    Returns:
        [(cell label, r0, r180, L1/L2, A_a/A_b)], one per acceptable cell that
        has captures in both orientations.
    """
    out = []
    rotset = set(rotated)
    for r in results:
        if r.group != ACCEPTABLE or not r.per_file_means:
            continue
        norm, rot = [], []
        for name, means in r.per_file_means.items():
            ratio = means[geom.chip1] / means[geom.chip2]
            (rot if r.placement_of.get(name) in rotset else norm).append(ratio)
        if not norm or not rot:
            continue
        r0, r180 = float(np.mean(norm)), float(np.mean(rot))
        out.append((f"{r.led}/{r.label}/{r.channel}", r0, r180,
                    float(np.sqrt(r0 * r180)), float(np.sqrt(r0 / r180))))
    return out


def write_summary_report(path: Path, results: Sequence[Result],
                         captures: Sequence[Capture], geom: Geometry,
                         rotated: Sequence[str]) -> None:
    """
    Write the headline report: adequacy, chosen degrees, curvature, control.

    Args:
        path: Output file.
        results: Every cell.
        captures: The open captures, for the heading.
        geom: The target geometry.
        rotated: Placement names imaged at 180 degrees.

    Returns:
        None.
    """
    lines = [_preamble(captures, geom, "Pooled flat-field analysis — summary")]
    leds = sorted({r.led for r in results})
    labels = sorted({r.label for r in results})

    lines.append("Exposure adequacy, LED x layer x rung\n")
    lines.append("-" * 60 + "\n")
    lines.append(f"  {'led':<11} {'ch':<3} "
                 + " ".join(f"{lab:<11}" for lab in labels) + "\n")
    for led in leds:
        for ch in BAYER_CHANNELS:
            row = []
            for lab in labels:
                hit = next((r for r in results if r.led == led
                            and r.label == lab and r.channel == ch), None)
                row.append("-" if hit is None
                           else f"{hit.group[:4]} {hit.mean:6.1f}")
            lines.append(f"  {led:<11} {ch:<3} "
                         + " ".join(f"{c:<11}" for c in row) + "\n")

    lines.append("\n\nChosen basis and curvature correction, per LED x layer\n")
    lines.append("-" * 60 + "\n")
    lines.append(f"  {'led':<11} {'ch':<3} {'e#':<3} {'deg':<6} {'form':<8} "
                 f"{'w1':>7} {'w2':>7} {'wsum':>7} {'rho':>7} "
                 f"{'loo_max%':>9}\n")
    for led in leds:
        for ch in BAYER_CHANNELS:
            best = _best_result(results, led, ch)
            if best is None:
                continue
            a = best.anchor
            lines.append(
                f"  {led:<11} {ch:<3} {best.label:<3} "
                f"({best.chosen.deg_y},{best.chosen.deg_x}){'':<2} "
                f"{best.form:<8} {a.w1:>7.4f} {a.w2:>7.4f} "
                f"{a.w_sum:>7.4f} {a.curvature:>7.4f} "
                f"{_pct(best.chosen.loo_max[best.form], 9, 3)}\n")

    curv = [_best_result(results, led, ch).anchor.curvature
            for led in leds for ch in BAYER_CHANNELS
            if _best_result(results, led, ch) is not None]
    if curv:
        lines.append(
            f"\nCurvature correction spans {min(curv):.4f} to {max(curv):.4f} "
            f"(median {np.median(curv):.4f}).\nA value of 1.0 would mean "
            f"linear interpolation between the chips already gives the\n"
            f"illumination at the sample. It does not: the sample sits on a "
            f"ridge between them.\n")

    control = rotation_control(results, geom, rotated)
    lines.append("\n\nRotation control: illumination vs card reflectance\n")
    lines.append("-" * 60 + "\n")
    if not control:
        lines.append("  No placement was flagged as rotated; control skipped.\n")
    else:
        lines.append(f"  {'cell':<24} {'r(0deg)':>8} {'r(180)':>8} "
                     f"{'L1/L2':>8} {'A_a/A_b':>8} {'chip diff%':>11}\n")
        for name, r0, r180, l, a in control:
            lines.append(f"  {name:<24} {r0:>8.4f} {r180:>8.4f} {l:>8.4f} "
                         f"{a:>8.4f} {100 * abs(a - 1):>11.3f}\n")
        diffs = [100 * abs(a - 1) for *_, a in control]
        lines.append(
            f"\n  Chip reflectance differs by {min(diffs):.3f} - "
            f"{max(diffs):.3f} % (median {np.median(diffs):.3f} %),\n"
            f"  while the illumination at the two chip positions differs by "
            f"far more. The\n  chip1/chip2 ratio does not flip between "
            f"orientations, so the asymmetry is the\n  instrument's "
            f"illumination and not the card. Pooling all placements into one\n"
            f"  baseline is sound.\n")

    under = [r for r in results if r.group == UNDER]
    lines.append(f"\n\nUnder-exposed cells: {len(under)} of {len(results)}\n")
    lines.append("-" * 60 + "\n")
    unreach = [r for r in under if not r.reachable]
    if unreach:
        lines.append(
            f"  {len(unreach)} cannot be fixed by exposure at all "
            f"(> {MAX_PRACTICAL_US:,} us):\n")
        for r in sorted(unreach, key=lambda r: -r.us_min)[:20]:
            lines.append(f"    {r.led}/{r.label}/{r.channel:<3} "
                         f"{r.mean:6.1f} DN -> needs {r.us_min:>10,.0f} us "
                         f"(x{r.us_min / r.exposure_us:.0f})\n")
        if len(unreach) > 20:
            lines.append(f"    ... and {len(unreach) - 20} more; see "
                         f"report_cells.txt\n")
        lines.append(
            "\n  These are LED x Bayer-layer combinations the emitter barely "
            "excites — a red LED\n  in B, a blue LED in R. No exposure makes "
            "them measurable; they are not a defect.\n")
    path.write_text("".join(lines))


def write_layermodels_report(path: Path, models: Sequence["LayerModel"],
                             captures: Sequence[Capture], geom: Geometry,
                             db_path: Optional[Path] = None) -> None:
    """
    Write the rows published to the instrument, one line per LED x layer.

    The last report in the run, and the only one describing something outside
    devdesk/: everything else documents the analysis, this documents what was
    handed over.  Read it to answer "what will the instrument actually do"
    without opening colorimeter.sqlite.

    Args:
        path: Output file.
        models: The rows, from build_layermodels().
        captures: The open captures, for the heading.
        geom: The target geometry.
        db_path: The database written to, named in the heading, or None if the
            rows were built but not published.

    Returns:
        None.
    """
    lines = [_preamble(captures, geom, "Published to the instrument "
                                       "(layermodels)")]
    lines.append(
        f"target     : {db_path if db_path else '(not written)'}\n\n"
        f"One row per LED colour x Bayer layer. w1 and w2 are what a "
        f"measurement multiplies\nthe two chip means by:  divisor = w1 * "
        f"mean(chip1) + w2 * mean(chip2).\n\n"
        f"There is no exposure key. w1 and w2 move less than 0.2 % across the "
        f"ladder, well\nunder the 0.4-0.8 % placement floor, so exposure_us is "
        f"only the reference p10 and\np90 were measured at; the capture path "
        f"scales them to whatever it is about to\nshoot.\n\n")

    lines.append(f"  {'led':<11} {'ch':<3} {'use':>3} {'ref_us':>7} "
                 f"{'w1':>8} {'w2':>8} {'wsum':>8} {'rho':>7} "
                 f"{'p10':>7} {'p90':>7} {'deg':>6} {'form':<8} "
                 f"{'loo%':>6}  remark\n")
    for m in models:
        w1 = f"{m.w1:>8.4f}" if m.w1 is not None else f"{'-':>8}"
        w2 = f"{m.w2:>8.4f}" if m.w2 is not None else f"{'-':>8}"
        wsum = (f"{m.w1 + m.w2:>8.4f}" if m.w1 is not None and m.w2 is not None
                else f"{'-':>8}")
        rho = f"{m.rho:>7.4f}" if m.rho is not None else f"{'-':>7}"
        deg = (f"{f'({m.deg_y},{m.deg_x})':>6}" if m.deg_y is not None
               else f"{'-':>6}")
        loo = f"{m.loo_pct:>6.3f}" if m.loo_pct is not None else f"{'-':>6}"
        lines.append(
            f"  {m.led_color:<11} {m.bayer_layer:<3} "
            f"{'yes' if m.usable else 'no':>3} {m.exposure_us:>7} "
            f"{w1} {w2} {wsum} {rho} "
            f"{m.p10:>7.1f} {m.p90:>7.1f} {deg} {(m.anchor_form or '-'):<8} "
            f"{loo}  {m.remark or ''}\n")

    usable = [m for m in models if m.usable]
    lines.append(f"\n{len(models)} rows: {len(usable)} usable, "
                 f"{len(models) - len(usable)} not.\n")
    if usable:
        sums = [m.w1 + m.w2 for m in usable]
        lines.append(
            f"w1 + w2 spans {min(sums):.4f} - {max(sums):.4f} "
            f"(median {np.median(sums):.4f}).\nA sum of 1.0 would mean the "
            f"sample's illumination is the plain interpolation of\nthe two "
            f"chips; the departure is what the polynomial was fitted to find.\n")

    # The verdict and the published window are two different statistics — the
    # group came from p01/p99 over the whole crop, p10/p90 are over the patches
    # — so they could in principle disagree, and a disagreement would have the
    # instrument discard a layer this run fitted a model for.  Check, and say
    # so either way rather than leaving it to be discovered later.
    conflicts = []
    for m in usable:
        full = RAW_MAX - (m.black_dn if m.black_dn is not None else 0.0)
        if m.p10 < UNDER_FRAC * full or m.p90 > OVER_FRAC * full:
            conflicts.append(m)
    if conflicts:
        lines.append(
            f"\nWARNING: {len(conflicts)} usable row(s) fall outside the "
            f"{UNDER_FRAC:.0%}-{OVER_FRAC:.0%} window their own\np10/p90 will "
            f"be screened against, so the instrument would drop a layer this "
            f"run\nfitted a model for:\n")
        for m in conflicts:
            lines.append(f"    {m.led_color + '/' + m.bayer_layer:<15} "
                         f"p10 {m.p10:.1f}, p90 {m.p90:.1f}\n")
    else:
        lines.append(
            f"\nEvery usable row's p10/p90 sits inside the "
            f"{UNDER_FRAC:.0%}-{OVER_FRAC:.0%} window it will be screened\n"
            f"against, so the fit verdict and the capture-time gate agree on "
            f"all {len(models)} rows.\n")

    unusable = [m for m in models if not m.usable]
    if unusable:
        lines.append(
            f"\nThe {len(unusable)} unusable rows are published, not omitted. "
            f"An absent row means\n\"never analysed\" and the capture path "
            f"keeps the layer; a usable = 0 row means\n\"measured, and "
            f"hopeless\" and it stops storing it. The two must not look "
            f"alike.\n")
        for m in unusable:
            lines.append(f"    {m.led_color + '/' + m.bayer_layer:<15} "
                         f"{m.remark}\n")
    path.write_text("".join(lines))


def _best_result(results: Sequence[Result], led: str,
                 channel: str) -> Optional[Result]:
    """
    Return the brightest acceptable cell for one LED and Bayer layer.

    The most signal a real calibration could use without leaving the usable
    band, chosen per layer rather than per LED because one LED lights the four
    layers to wildly different levels.

    Args:
        results: Every cell.
        led: LED colour name.
        channel: Bayer channel name.

    Returns:
        The Result, or None if no rung of this LED x layer is usable.
    """
    hits = [r for r in results if r.led == led and r.channel == channel
            and r.chosen is not None]
    return max(hits, key=lambda r: r.mean) if hits else None


# ── Database ────────────────────────────────────────────────────────

# The runtime deliverable.  Coefficients live in `flatfield` and stay the
# source of truth; this table is what the instrument would actually read, and
# every column in it is derivable from that one — kept separate because two
# scalars per cell is the whole point and burying them in a coefficient blob
# would hide it.
ANCHOR_DDL = """
CREATE TABLE IF NOT EXISTS anchor (
    h5file       TEXT NOT NULL,
    led          TEXT NOT NULL,
    eno          TEXT NOT NULL,
    exposure_us  INTEGER,
    bayerlayer   TEXT NOT NULL,
    deg_y        INTEGER NOT NULL,
    deg_x        INTEGER NOT NULL,
    form         TEXT NOT NULL,
    bound_rowid  INTEGER NOT NULL,
    sample_rowid INTEGER NOT NULL,
    chip1_rowid  INTEGER NOT NULL,
    chip2_rowid  INTEGER NOT NULL,
    t_sample     REAL,
    g_chip1      REAL NOT NULL,
    g_sample     REAL NOT NULL,
    g_chip2      REAL NOT NULL,
    w1_1anchor   REAL NOT NULL,
    w2_1anchor   REAL NOT NULL,
    w1_2anchor   REAL NOT NULL,
    w2_2anchor   REAL NOT NULL,
    rho          REAL NOT NULL,
    loo_max_pct  REAL,
    loo_med_pct  REAL,
    n_captures   INTEGER,
    n_groups     INTEGER,
    add_dt       TEXT NOT NULL,
    PRIMARY KEY (h5file, led, eno, bayerlayer, deg_y, deg_x)
)
"""


def write_db(db_path: Path, results: Sequence[Result],
             captures: Sequence[Capture], geom: Geometry) -> Tuple[int, int]:
    """
    Persist the chosen fits and their anchor weights.

    Two tables, deliberately redundant in the same way flatfield.cmd_write()
    is: `flatfield` keeps the coefficients, which survive an ROI moving, and
    `anchor` keeps the two scalars a measurement actually multiplies by.  The
    `flatfield` schema is reused verbatim so the existing tooling — including
    verify_roundtrip() — still applies to these rows.

    `h5file` holds every archive's basename joined by ', ', because a pooled
    row is not the property of any one of them.

    Args:
        db_path: sqlite database; created with both schemas if absent.
        results: Every cell; only the ones with a chosen rung are written.
        captures: The open captures, for the file and suite identifiers.
        geom: The target geometry.

    Returns:
        (flatfield rows written, anchor rows written).
    """
    import sqlite3
    from datetime import datetime

    h5file = ", ".join(c.name for c in captures)
    suites = ", ".join(sorted({
        str(c.reader.attrs("/").get("suite_id", c.path.stem))
        for c in captures}))
    patches_json = json.dumps({n: list(r) for n, r
                               in geom.patches.rects.items()}, sort_keys=True)
    n_caps = len(captures)
    n_groups = len({c.placement for c in captures})
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    b = geom.bound

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    ff_rows = an_rows = 0
    try:
        conn.execute(ff_mod.FLATFIELD_DDL)
        ff_mod._ensure_patches_column(conn)
        conn.execute(ANCHOR_DDL)
        for r in results:
            if r.chosen is None:
                continue
            c, one, two = (r.chosen, r.chosen.anchors["1anchor"],
                           r.chosen.anchors["2anchor"])
            f = c.field
            conn.execute(
                "INSERT OR REPLACE INTO flatfield VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (h5file, suites, r.led, r.label, r.exposure_us, r.channel,
                 f.extent, f.deg_y, f.deg_x, f.n_terms, f.coef_json(),
                 f.domain[0], f.domain[1], b.x, b.y, b.w, b.h,
                 STREAM_W, STREAM_H, f.norm_mean, f.n_fit, f.n_masked,
                 f.fit_rms_pct, c.block_rms_pct, c.p_max_pct, now,
                 patches_json))
            ff_rows += 1

            conn.execute(
                "INSERT OR REPLACE INTO anchor VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (h5file, r.led, r.label, r.exposure_us, r.channel,
                 c.deg_y, c.deg_x, r.form, geom.bound_rowid,
                 geom.rowid_of[geom.sample], geom.rowid_of[geom.chip1],
                 geom.rowid_of[geom.chip2], geom.t,
                 c.gains[geom.chip1], c.gains[geom.sample],
                 c.gains[geom.chip2],
                 one.w1, one.w2, two.w1, two.w2, two.curvature,
                 c.loo_max[r.form], c.loo_med[r.form], n_caps, n_groups, now))
            an_rows += 1
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        print(f"error: sqlite write failed, rolled back: {e}", file=sys.stderr)
        return (0, 0)
    finally:
        conn.close()
    return (ff_rows, an_rows)


# ── Publishing to the instrument ────────────────────────────────────
# Everything above writes to devdesk/flatfield.sqlite, which is a development
# artifact the instrument never sees.  This is the crossing: the same result,
# reduced to what a measurement actually needs, in the instrument's own
# `layermodels` table.  src/layermodel_repo.py owns that table's schema and SQL;
# this only decides which cell becomes each row.

def build_layermodels(results: Sequence[Result], geom: Geometry,
                      analysisref: str) -> List["LayerModel"]:
    """
    Reduce every cell to one row per (LED colour, Bayer layer).

    The analysis visits each LED x layer at every rung of the exposure ladder;
    the instrument wants one answer per LED x layer.  Two cases:

      * **Some rung was usable** — take the brightest of them (_best_result(),
        the same choice the figures draw) and publish its weights.  Its own
        exposure becomes the row's reference exposure, the one p10 and p90 are
        quoted at.  Publishing the brightest rather than averaging the rungs is
        deliberate: they agree on the weights to better than 0.2 % anyway, and
        the brightest carries the least shot noise in the percentiles, which
        are the part that actually varies.
      * **No rung was usable** — publish `usable = 0` with no weights, using
        the brightest cell there was for the percentiles.  The row is not
        filler: it is what tells the capture path to stop storing that layer,
        and its p10/p90 are the evidence for the verdict.  Dropping these rows
        instead would leave "measured, and hopeless" indistinguishable from
        "never measured", which keep_channels() has to treat oppositely.

    Args:
        results: Every cell from analyze()'s first pass.
        geom: The target geometry, for the roi rowids stamped on each row.
        analysisref: Name of the report set, stamped on every row.

    Returns:
        LayerModels in (LED, layer) order — one per pair seen in `results`.

    Example:
        >>> len(build_layermodels(results, geom, "bound39a"))  # doctest: +SKIP
        40
    """
    pairs = sorted({(r.led, r.channel) for r in results})
    models: List[LayerModel] = []

    for led, channel in pairs:
        best = _best_result(results, led, channel)
        if best is not None:
            anchor, rung = best.anchor, best.chosen
            models.append(LayerModel(
                usable=True, led_color=led, bayer_layer=channel,
                exposure_us=best.exposure_us,
                roisamp=geom.rowid_of[geom.sample],
                roi1=geom.rowid_of[geom.chip1],
                roi2=geom.rowid_of[geom.chip2],
                w1=anchor.w1, w2=anchor.w2,
                p10=best.patch_p10, p90=best.patch_p90, black_dn=best.black,
                roibound=geom.bound_rowid,
                deg_y=rung.deg_y, deg_x=rung.deg_x,
                coef_json=rung.field.coef_json(),
                norm_mean=rung.field.norm_mean,
                anchor_form=best.form, rho=anchor.curvature,
                loo_pct=rung.loo_max[best.form],
                analysisref=analysisref,
                remark=f"{best.label} of {_rung_count(results, led, channel)} "
                       f"rung(s), {rung.n_folds} placement(s)"))
            continue

        # Nothing usable.  Quote the brightest cell there was, and say why it
        # failed in the operator's terms rather than leaving a bare zero.
        pool_of_pair = [r for r in results
                        if r.led == led and r.channel == channel]
        fallback = max(pool_of_pair, key=lambda r: r.mean)
        why = "under-exposed" if fallback.group == UNDER else fallback.group
        reach = "" if fallback.reachable else ", unreachable at any exposure"
        models.append(LayerModel(
            usable=False, led_color=led, bayer_layer=channel,
            exposure_us=fallback.exposure_us,
            roisamp=geom.rowid_of[geom.sample],
            roi1=geom.rowid_of[geom.chip1],
            roi2=geom.rowid_of[geom.chip2],
            p10=fallback.patch_p10, p90=fallback.patch_p90,
            black_dn=fallback.black, roibound=geom.bound_rowid,
            analysisref=analysisref,
            remark=f"{why} at every rung{reach}; brightest was "
                   f"{fallback.label} at {fallback.mean:.1f} DN"))

    return models


def _rung_count(results: Sequence[Result], led: str, channel: str) -> int:
    """Return how many rungs of one LED x layer were usable."""
    return sum(1 for r in results if r.led == led and r.channel == channel
               and r.chosen is not None)


def write_layermodels(db_path: Path, models: Sequence["LayerModel"],
                      analysisref: str) -> int:
    """
    Put the models in force in an instrument database.

    Writing goes through src/layermodel_repo.py rather than raw SQL here, so
    the column list and the supersede-don't-delete rule have exactly one home —
    the same reason the calibration capture path writes exposures through
    exposure_repo instead of its own INSERT.

    `db_manager` settles the database location process-wide, and reassigning
    `DBNAME` to a scratch copy is its documented escape hatch (exposure_repo's
    own self-test does it).  This borrows the module for the length of the call
    and puts it back, so a caller that also reads the live database afterwards
    is not left pointing somewhere else.

    Args:
        db_path: The instrument database — normally src/colorimeter.sqlite.
            The `layermodels` table must already exist; db_bootstrap.ensure()
            creates it at every launch, and running src/db_DDL.sql by hand does
            the same.
        models: Rows to put in force, from build_layermodels().
        analysisref: Stamped on any row that does not carry its own.

    Returns:
        The number of rows inserted, or 0 if the write failed (which is
        reported on stderr rather than raised — one bad database should not
        cost a run its reports and figures).
    """
    import sqlite3

    was = db_manager.DBNAME
    db_manager.DBNAME = str(db_path)
    try:
        return layermodel_repo.write_models(models, analysisref=analysisref)
    except sqlite3.Error as e:
        print(f"error: layermodels write to {db_path} failed: {e}\n"
              f"  is the table there? run: sqlite3 {db_path} < src/db_DDL.sql",
              file=sys.stderr)
        return 0
    finally:
        db_manager.DBNAME = was


# ── Figures ─────────────────────────────────────────────────────────

def plot_cell(pool: Pool, geom: Geometry, result: Result,
              out_png: Path) -> None:
    """
    Draw the six diagnostics that make one cell's anchor legible.

    Top row: the row and column profiles on a shared DN scale, so "steeper in
    x than in y" is something the reader sees rather than something the axes
    assert; and the leave-one-out error against degree, which is the curve the
    degree was actually chosen from.

    Bottom row: the block-mean residual map with the ROI boxes on it; the
    anchor panel, which is the point of the whole analysis — the illumination
    across the chip/sample band, with the straight line between the chips drawn
    against the fitted curve so the gap between them *is* the curvature
    correction; and the per-placement patch means, which show the card-handling
    scatter that sets the floor on how well any of this can do.

    Args:
        pool: The pooled cell.
        geom: The target geometry.
        result: Its scored result; `result.chosen` must not be None.
        out_png: Path to write; parent directories are created.

    Returns:
        None.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    rung, anchor = result.chosen, result.anchor
    ff, rects = rung.field, geom.patches.rects
    surface = ff.evaluate()
    corrected = ff.correct(pool.frame)
    resid = (corrected / corrected.mean() - 1.0) * 100.0

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 3, figsize=(17, 9))
    fig.suptitle(
        f"{result.led}/{result.label}/{result.channel}   "
        f"({rung.deg_y},{rung.deg_x}) over bound rowid {geom.bound_rowid}   "
        f"{result.exposure_us:,} us   mean {result.mean:.0f} DN   "
        f"{result.form}: w1 {anchor.w1:.4f}  w2 {anchor.w2:.4f}  "
        f"rho {anchor.curvature:.4f}")

    prof = [(pool.frame.signal.mean(1), surface.mean(1), "row (y)"),
            (pool.frame.signal.mean(0), surface.mean(0), "column (x)")]
    lo = min(float(m.min()) for m, _, _ in prof)
    hi = max(float(m.max()) for m, _, _ in prof)
    pad = 0.05 * (hi - lo) or 1.0
    for ax, (measured, fitted, axis_name) in zip(axes[0], prof):
        span = (measured.max() - measured.min()) / measured.mean() * 100.0
        ax.plot(measured, lw=1, label="pooled median")
        ax.plot(fitted, lw=1.5, ls="--", label="fitted")
        ax.set_xlabel(f"layer {axis_name}")
        ax.set_ylabel("DN")
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_title(f"{axis_name} profile — spans {span:.1f} %")
        ax.legend(fontsize=8, loc="lower center")
        ax.grid(alpha=0.3)

    ax = axes[0][2]
    labels = [f"({c.deg_y},{c.deg_x})" for c in result.rungs]
    for form, style in (("1anchor", "o-"), ("2anchor", "s-")):
        ax.plot(labels, [c.loo_max[form] for c in result.rungs], style,
                label=f"{form} LOO max")
    ax.plot(labels, [c.fit_rms_pct for c in result.rungs], "^:", color="grey",
            label="in-sample resid (do not use)")
    ax.axhline(FLOOR_PCT, color="r", ls=":", label=f"floor {FLOOR_PCT} %")
    ax.axvline(labels[result.rungs.index(rung)], color="g", alpha=0.3, lw=6)
    ax.set_yscale("log")
    ax.set_xlabel("(deg_y, deg_x)")
    ax.set_ylabel("%")
    ax.set_title("anchor error, leave one placement out\n"
                 "(green band = chosen)")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)

    ax = axes[1][0]
    rows, cols = resid.shape
    blocks = np.array([[resid[i:i + BLOCK_PX, j:j + BLOCK_PX].mean()
                        for j in range(0, cols - BLOCK_PX + 1, BLOCK_PX)]
                       for i in range(0, rows - BLOCK_PX + 1, BLOCK_PX)])
    blim = float(np.abs(blocks).max()) or 1.0
    im = ax.imshow(blocks, cmap="RdBu_r", vmin=-blim, vmax=blim,
                   extent=(0.0, float(cols), float(rows), 0.0),
                   interpolation="nearest")
    for name, (x, y, w, h) in rects.items():
        ax.add_patch(Rectangle((x, y), w, h, fill=False, ec="k", lw=1.2))
        ax.text(x + w / 2, y - 4, name, fontsize=7, ha="center")
    ax.set_title(f"{BLOCK_PX}x{BLOCK_PX} block means, % — "
                 f"rms {rung.block_rms_pct:.2f}, max {rung.block_max_pct:.2f}")
    ax.set_xlabel("layer column (x)")
    ax.set_ylabel("layer row (y)")
    fig.colorbar(im, ax=ax, fraction=0.046)

    _plot_anchor(axes[1][1], pool, geom, rung, anchor)
    _plot_placements(axes[1][2], pool, geom, result)

    fig.tight_layout()
    fig.savefig(out_png, dpi=110)
    plt.close(fig)


def _plot_anchor(ax, pool: Pool, geom: Geometry, rung: Rung,
                 anchor: Anchor) -> None:
    """
    Draw the illumination across the chip/sample band and the anchor on it.

    The gap between the straight line joining the two chips and the fitted
    curve at the sample is the curvature correction, drawn to scale.

    Args:
        ax: Axes to draw on.
        pool: The pooled cell.
        geom: The target geometry.
        rung: The chosen rung.
        anchor: Its anchor in the chosen form.

    Returns:
        None.
    """
    rects = geom.patches.rects
    tops = [rects[n][1] for n in (geom.chip1, geom.sample, geom.chip2)]
    bots = [rects[n][1] + rects[n][3] for n in
            (geom.chip1, geom.sample, geom.chip2)]
    band = slice(min(tops), max(bots))

    gain = rung.field.gain()
    measured = pool.frame.signal[band].mean(0) / rung.field.norm_mean
    fitted = gain[band].mean(0)
    ax.plot(measured, lw=1, color="0.6", label="pooled median / norm")
    ax.plot(fitted, lw=1.8, color="C0", label="fitted gain")

    xs, gs = [], []
    for name, colour in ((geom.chip1, "C1"), (geom.sample, "C2"),
                         (geom.chip2, "C1")):
        x, _, w, _ = rects[name]
        centre = x + (w - 1) / 2.0
        xs.append(centre)
        gs.append(rung.gains[name])
        ax.axvspan(x, x + w, color=colour, alpha=0.15)
        ax.plot([centre], [rung.gains[name]], "o", color=colour, ms=7)
        ax.annotate(f"{name}\n{rung.gains[name]:.4f}", (centre,
                    rung.gains[name]), textcoords="offset points",
                    xytext=(0, 10), ha="center", fontsize=7)

    ax.plot([xs[0], xs[2]], [gs[0], gs[2]], ls="--", color="C3",
            label="linear between chips")
    lin = gs[0] + (gs[2] - gs[0]) * geom.t
    ax.annotate("", xy=(xs[1], gs[1]), xytext=(xs[1], lin),
                arrowprops=dict(arrowstyle="<->", color="C3", lw=1.5))
    ax.text(xs[1] + 4, (gs[1] + lin) / 2,
            f"curvature\n{100 * (anchor.curvature - 1):+.2f} %",
            fontsize=8, color="C3", va="center")
    ax.set_xlabel("layer column (x)")
    ax.set_ylabel("gain (unit mean over crop)")
    ax.set_title("the anchor: illumination across the chip/sample band")
    ax.legend(fontsize=7, loc="lower center")
    ax.grid(alpha=0.3)


def _plot_placements(ax, pool: Pool, geom: Geometry, result: Result) -> None:
    """
    Draw each capture's patch means, grouped by placement.

    The scatter between placements is card handling, and it is what sets the
    floor on the leave-one-out error — no polynomial can do better than the
    card can be put down.

    Args:
        ax: Axes to draw on.
        pool: The pooled cell.
        geom: The target geometry.
        result: The scored result, for the placement labels.

    Returns:
        None.
    """
    names = [geom.chip1, geom.sample, geom.chip2]
    placements = sorted({result.placement_of.get(f, f)
                         for f in result.per_file_means})
    width = 0.8 / max(len(placements), 1)
    base = np.arange(len(names))
    everything = []
    for i, place in enumerate(placements):
        vals = []
        for name in names:
            hits = [m[name] for f, m in result.per_file_means.items()
                    if result.placement_of.get(f, f) == place]
            vals.append(100.0 * np.mean(hits) / pool.frame.mean)
        everything.extend(vals)
        ax.bar(base + i * width, vals, width, label=place)
    ax.set_xticks(base + width * (len(placements) - 1) / 2)
    ax.set_xticklabels(names)
    ax.set_ylabel("% of pooled crop mean")
    # Zoomed hard, because the differences being shown are tenths of a percent
    # against a hundred: a bar chart from zero would draw three identical bars.
    lo, hi = min(everything), max(everything)
    pad = max(0.25, 0.35 * (hi - lo))
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_title(f"patch means by placement — spread {result.spread_pct:.2f} %\n"
                 "(card handling; the floor on leave-one-out)")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3, axis="y")


def plot_adequacy(results: Sequence[Result], out_png: Path) -> None:
    """
    Draw the LED x layer x rung exposure map for the whole run.

    Args:
        results: Every cell.
        out_png: Path to write.

    Returns:
        None.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch

    leds = sorted({r.led for r in results})
    labels = sorted({r.label for r in results})
    cols = [(ch, lab) for ch in BAYER_CHANNELS for lab in labels]
    codes = {UNDER: 0, ACCEPTABLE: 1, OVER: 2}
    grid = np.full((len(leds), len(cols)), np.nan)
    text = {}
    for i, led in enumerate(leds):
        for j, (ch, lab) in enumerate(cols):
            hit = next((r for r in results if r.led == led
                        and r.channel == ch and r.label == lab), None)
            if hit is None:
                continue
            grid[i, j] = codes[hit.group]
            text[(i, j)] = f"{hit.mean:.0f}"

    cmap = ListedColormap(["#e8b0b0", "#b6d7a8", "#f5d9a0"])
    fig, ax = plt.subplots(figsize=(1.0 + 0.62 * len(cols), 1.5 + 0.42 * len(leds)))
    ax.imshow(grid, cmap=cmap, vmin=-0.5, vmax=2.5, aspect="auto")
    for (i, j), s in text.items():
        ax.text(j, i, s, ha="center", va="center", fontsize=6.5)
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels([f"{ch}\n{lab}" for ch, lab in cols], fontsize=7)
    ax.set_yticks(range(len(leds)))
    ax.set_yticklabels(leds, fontsize=8)
    ax.set_title("Exposure adequacy by LED x Bayer layer x rung\n"
                 "(cell values are pooled mean signal, DN)")
    ax.legend(handles=[Patch(facecolor=c, label=g) for g, c in
                       zip((UNDER, ACCEPTABLE, OVER),
                           ("#e8b0b0", "#b6d7a8", "#f5d9a0"))],
              fontsize=7, ncol=3, loc="upper center",
              bbox_to_anchor=(0.5, -0.08))
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=120, bbox_inches="tight")
    plt.close(fig)


def plot_anchor_overview(results: Sequence[Result], out_png: Path) -> None:
    """
    Draw the curvature correction per LED x layer, with its spread over rungs.

    The correction is a property of the illumination, so it should not depend
    on exposure.  The bar spans the values found at every usable rung of that
    LED x layer; a long bar means something other than illumination is moving.

    Args:
        results: Every cell.
        out_png: Path to write.

    Returns:
        None.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    leds = sorted({r.led for r in results if r.chosen is not None})
    fig, ax = plt.subplots(figsize=(2.0 + 0.85 * len(leds), 6))
    width = 0.8 / len(BAYER_CHANNELS)
    base = np.arange(len(leds))
    for k, ch in enumerate(BAYER_CHANNELS):
        centres, lo, hi, xs = [], [], [], []
        for i, led in enumerate(leds):
            vals = [r.anchor.curvature for r in results
                    if r.led == led and r.channel == ch and r.chosen]
            if not vals:
                continue
            best = _best_result(results, led, ch)
            xs.append(i + k * width)
            centres.append(best.anchor.curvature)
            lo.append(best.anchor.curvature - min(vals))
            hi.append(max(vals) - best.anchor.curvature)
        ax.bar(xs, np.array(centres) - 1.0, width, bottom=1.0,
               yerr=[lo, hi], capsize=2, label=ch)
    ax.axhline(1.0, color="k", lw=1)
    ax.set_xticks(base + width * (len(BAYER_CHANNELS) - 1) / 2)
    ax.set_xticklabels(leds, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("rho")
    ax.set_title("Curvature correction per LED x Bayer layer\n"
                 "(1.0 = linear interpolation between the chips would do; "
                 "bars span the usable rungs)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=120)
    plt.close(fig)


def plot_degrees_overview(results: Sequence[Result], out_png: Path) -> None:
    """
    Draw every usable cell's leave-one-out error against degree.

    Args:
        results: Every cell.
        out_png: Path to write.

    Returns:
        None.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    scored = [r for r in results if r.rungs]
    if not scored:
        return
    labels = [f"({c.deg_y},{c.deg_x})" for c in scored[0].rungs]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), sharey=True)
    for ax, form in zip(axes, ANCHOR_FORMS):
        curves = np.array([[c.loo_max[form] for c in r.rungs] for r in scored])
        for row in curves:
            ax.plot(labels, row, color="C0", alpha=0.12, lw=1)
        ax.plot(labels, np.median(curves, axis=0), "o-", color="C3", lw=2,
                label="median over cells")
        ax.axhline(FLOOR_PCT, color="k", ls=":",
                   label=f"repeatability floor {FLOOR_PCT} %")
        ax.set_yscale("log")
        ax.set_xlabel("(deg_y, deg_x)")
        ax.set_title(f"{form} — {len(scored)} usable cells")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    axes[0].set_ylabel("leave-one-placement-out anchor error, max %")
    fig.suptitle("Anchor error against degree — deg_x >= 2 is what matters")
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=120)
    plt.close(fig)


# ── The run ─────────────────────────────────────────────────────────

def analyze(files: Sequence[Sequence], bound: int,
            roi_rowid: Sequence[int], writedir,
            db_path=DEFAULT_FF_DB,
            degrees: Sequence[Tuple[int, int]] = DEGREES,
            rotated: Sequence[str] = (),
            leds: Optional[Sequence[str]] = None,
            labels: Optional[Sequence[str]] = None,
            captures_dir: Path = DEFAULT_CAPTURES_DIR,
            figures: bool = True,
            layermodels_db=None,
            analysisref: Optional[str] = None) -> List[Result]:
    """
    Pool a set of captures over one bound and settle its anchor weights.

    Two passes.  The first walks every (LED, exposure, layer) cell, pools it,
    classifies its exposure and — when it is usable — fits the degree ladder
    and scores it out of sample.  Nothing but scalars is kept: 120 cells of
    130x183 images would cost ~180 MB for no purpose.  The second pass re-loads
    only the forty cells that get a figure.

    Args:
        files: (filename, capture_bound_rowid[, placement]) per archive.
            Captures sharing a placement are averaged before pooling, so an
            untouched repeat pair does not outvote a single re-placement.
        bound: `roi.rowid` of the bound to analyse.
        roi_rowid: Every `roi.rowid` in the study, including `bound`.
        writedir: Directory for the reports and `figs/`.
        db_path: sqlite database for the `flatfield` and `anchor` rows.
        degrees: The (deg_y, deg_x) ladder.
        rotated: Placement names imaged at 180 degrees, for the control.
        leds: LED colours to visit, or None for every one present.
        labels: Exposure labels to visit, or None for every one present.
        captures_dir: Directory bare filenames resolve against.
        figures: Draw the PNGs.  False makes a run about ten times faster,
            for when only the tables are wanted.
        layermodels_db: Instrument database to publish the reduced result to —
            normally src/colorimeter.sqlite.  None analyses without publishing,
            which is what an exploratory run wants: a `layermodels` write puts
            new weights in force, and no run should do that by accident.
        analysisref: Name stamped on the published rows, e.g. 'bound39a'.
            Defaults to the writedir's own name, which is where the reports
            backing those rows sit.

    Returns:
        Every Result, in visit order.

    Example:
        >>> analyze((("ZP1001_grayA1a.h5", 26),), 39, (32, 35, 36, 39),
        ...         "devdesk/flatfield/bound39a")      # doctest: +SKIP
    """
    geom = build_geometry(roi_rowid, bound)
    writedir = Path(writedir)
    writedir.mkdir(parents=True, exist_ok=True)
    db_path = Path(db_path)

    captures = open_captures(files, geom.bound, captures_dir)
    placement_of = {c.name: c.placement for c in captures}
    print(f"bound rowid {bound} = {geom.bound.xywh()} -> layers "
          f"{geom.bound.h // 2}x{geom.bound.w // 2}; "
          f"{len(captures)} capture(s) in "
          f"{len({c.placement for c in captures})} placement(s)")

    results: List[Result] = []
    try:
        first = captures[0].reader
        for led in (leds or ff_mod.list_led_colors(first)):
            present = ff_mod.list_lit_labels(first, led)
            for label in (labels or present):
                if label not in present:
                    continue
                for channel in BAYER_CHANNELS:
                    try:
                        pool = load_pool(captures, led, label, channel)
                    except KeyError:
                        continue
                    results.append(analyse_cell(pool, geom, degrees,
                                                placement_of))
            done = sum(1 for r in results if r.led == led)
            usable = sum(1 for r in results
                         if r.led == led and r.chosen is not None)
            print(f"  {led:<11} {done:>2} cells, {usable:>2} usable")

        write_cells_report(writedir / "report_cells.txt", results,
                           captures, geom)
        write_fits_report(writedir / "report_fits.txt", results,
                          captures, geom)
        write_degrees_report(writedir / "report_degrees.txt", results,
                             captures, geom)
        write_summary_report(writedir / "report_summary.txt", results,
                             captures, geom, rotated)

        ref = analysisref or writedir.name
        models = build_layermodels(results, geom, ref)
        write_layermodels_report(writedir / "report_layermodels.txt", models,
                                 captures, geom, layermodels_db)
        print(f"  wrote 5 report(s) to {writedir}")

        ff_rows, an_rows = write_db(db_path, results, captures, geom)
        print(f"  wrote {ff_rows} flatfield and {an_rows} anchor row(s) "
              f"to {db_path}")

        if layermodels_db is None:
            print(f"  built {len(models)} layermodels row(s), not published "
                  f"(pass layermodels_db= to put them in force)")
        else:
            n = write_layermodels(Path(layermodels_db), models, ref)
            usable = sum(1 for m in models if m.usable)
            print(f"  put {n} layermodels row(s) in force in "
                  f"{layermodels_db} ({usable} usable) as '{ref}'")

        if figures:
            figdir = writedir / "figs"
            n = 0
            for led in sorted({r.led for r in results}):
                for channel in BAYER_CHANNELS:
                    best = _best_result(results, led, channel)
                    if best is None:
                        continue
                    pool = load_pool(captures, led, best.label, channel)
                    plot_cell(pool, geom, best,
                              figdir / f"fit_{led}_{channel}.png")
                    n += 1
            plot_adequacy(results, figdir / "overview_adequacy.png")
            plot_anchor_overview(results, figdir / "overview_anchor.png")
            plot_degrees_overview(results, figdir / "overview_degrees.png")
            print(f"  wrote {n + 3} figure(s) to {figdir}")
    finally:
        for c in captures:
            c.reader.__exit__(None, None, None)

    return results


# ── Self-test ───────────────────────────────────────────────────────

def _synth_surface(shape: Tuple[int, int], coefs: Dict[Tuple[int, int], float],
                   scale: float = 600.0) -> np.ndarray:
    """
    Build a known illumination surface in the module's own coordinates.

    Args:
        shape: (rows, cols) of the crop.
        coefs: {(iy, ix): coefficient} in coordinates normalized to [-1, 1]
            over the crop, added to a constant 1.0.
        scale: Overall brightness in DN.

    Returns:
        float64 (rows, cols) of surface values in DN.
    """
    rows, cols = shape
    y = (np.arange(rows) / (rows - 1)) * 2.0 - 1.0
    x = (np.arange(cols) / (cols - 1)) * 2.0 - 1.0
    xg, yg = np.meshgrid(x, y)
    out = np.ones(shape)
    for (iy, ix), c in coefs.items():
        out += c * (xg ** ix) * (yg ** iy)
    return out * scale


def _synth_pool(geom: Geometry, groups: Dict[str, np.ndarray]) -> Pool:
    """
    Wrap synthetic group images in a Pool without touching an archive.

    Args:
        geom: The target geometry, for the crop shape.
        groups: {placement: image}.

    Returns:
        A Pool whose frame is the per-pixel median of the group images.
    """
    pooled = np.median(np.stack(list(groups.values())), axis=0)
    mask = np.ones(pooled.shape, dtype=bool)
    frame = Frame(signal=pooled, mask=mask, led="synth", label="e0",
                  channel="Gr", exposure_us=1000, dark_link="none")
    return Pool(frame=frame, by_group=dict(groups),
                by_file={k: v for k, v in groups.items()}, black=64.0,
                exposure_us=1000, dark_links={k: "none" for k in groups},
                led="synth", label="e0", channel="Gr")


def _selftest() -> int:
    """
    Check the geometry, the pooling and the anchor against known answers.

    Returns:
        0 if every check passed, 1 otherwise.  Prints what it checked.
    """
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"  {'PASS' if ok else 'FAIL'}  {name}"
              + (f"   {detail}" if detail else ""))
        failures += 0 if ok else 1

    sl = subcrop_slices(Rect(806, 386, 732, 728), Rect(992, 588, 366, 260))
    check("subcrop arithmetic",
          sl == (slice(101, 231), slice(93, 276)), f"{sl}")

    geom = build_geometry((32, 35, 36, 39), 39)
    shape = (geom.bound.h // 2, geom.bound.w // 2)
    check("geometry", shape == (130, 183) and abs(geom.t - 0.4627) < 5e-4,
          f"shape {shape}, t = {geom.t:.4f}")

    # A uniform card under a curved field: both anchor forms must predict the
    # sample exactly, because that is the identity they are built to satisfy.
    curved = _synth_surface(shape, {(0, 1): 0.05, (1, 0): 0.03, (0, 2): -0.08})
    pool = _synth_pool(geom, {"p1": curved, "p2": curved, "p3": curved})
    ff = ff_mod.fit_field(pool.frame, 2, 2, "bound")
    gains = patch_gains(ff, geom.patches.rects)
    rects = geom.patches.rects
    m1 = ff_mod.patch_mean(curved, rects[geom.chip1])
    m2 = ff_mod.patch_mean(curved, rects[geom.chip2])
    ms = ff_mod.patch_mean(curved, rects[geom.sample])
    for form, anchor in make_anchors(gains, geom).items():
        err = abs(anchor.predict(m1, m2) / ms - 1.0)
        check(f"{form} exact on a uniform card", err < 1e-9, f"err {err:.2e}")

    # A field linear in x has nothing for the polynomial to add, so rho must
    # be exactly 1.  w_sum must not be asserted to match: it agrees only to
    # first order, and confusing the two is the mistake this check exists to
    # keep caught.
    def rho_of(coefs, deg=(1, 1)):
        surf = _synth_surface(shape, coefs)
        fitted = ff_mod.fit_field(_synth_pool(geom, {"p1": surf}).frame,
                                  deg[0], deg[1], "bound")
        return make_anchors(patch_gains(fitted, rects), geom)

    lin_anchors = rho_of({(0, 1): 0.05})
    for form, anchor in lin_anchors.items():
        check(f"{form}: field linear in x has rho 1",
              abs(anchor.curvature - 1.0) < 1e-9,
              f"rho = {anchor.curvature:.12f}")
    check("rho is form-independent",
          abs(lin_anchors["1anchor"].curvature
              - lin_anchors["2anchor"].curvature) < 1e-12)
    check("w_sum is not rho",
          abs(lin_anchors["2anchor"].w_sum - 1.0) > 1e-4,
          f"wsum = {lin_anchors['2anchor'].w_sum:.9f} on a field with no "
          f"x-curvature")

    # The three patches are not exactly collinear: chip2 (roi 35, y = 638)
    # sits one Bayer row above chip1 and sample (y = 640).  A pure y gradient
    # therefore leaks into rho.  Quantified rather than assumed away — at the
    # 4.6 % y span this bound actually has, it is worth ~0.02 %, far under the
    # placement floor, but it is a term and not a rounding error.
    leak = abs(rho_of({(1, 0): 0.03})["2anchor"].curvature - 1.0)
    check("y-gradient leak through the chips' 1-row offset",
          1e-6 < leak < 1e-3, f"{100 * leak:.4f} % for a 3 % y gradient")

    curved_rho = rho_of({(0, 1): 0.05, (1, 0): 0.03,
                         (0, 2): -0.08}, (2, 2))["2anchor"].curvature
    check("x-curvature moves rho far more", abs(curved_rho - 1.0) > 1e-2,
          f"rho = {curved_rho:.4f}")

    # One capture ruined by a spike must not move the pooled median.
    spiked = curved.copy()
    spiked[40:60, 80:100] = RAW_MAX
    dirty = _synth_pool(geom, {"p1": curved, "p2": curved, "p3": spiked})
    check("median rejects a spiked capture",
          np.allclose(dirty.frame.signal, curved),
          f"max dev {np.abs(dirty.frame.signal - curved).max():.3e} DN")

    # Exposure advice is one linear scaling, so it must invert exactly.
    dim = _synth_surface(shape, {}, scale=30.0)
    cell = classify(_synth_pool(geom, {"p1": dim}), geom.patches)
    want = 1000.0 * (UNDER_FRAC * (RAW_MAX - 64.0)) / 30.0
    check("under-exposure advice", cell.group == UNDER
          and abs(cell.us_min - want) < 1.0,
          f"{cell.group}, us_min {cell.us_min:,.0f} (want {want:,.0f})")

    bright = _synth_surface(shape, {}, scale=0.95 * (RAW_MAX - 64.0))
    check("over-exposure advice",
          classify(_synth_pool(geom, {"p1": bright}), geom.patches).group
          == OVER)

    # Against a real archive, so the subcrop arithmetic cannot drift unnoticed:
    # the synthetic checks above would all pass on the wrong 130x183 window.
    # These are the numbers measured by hand when the study was set up.
    archive = DEFAULT_CAPTURES_DIR / "ZP1001_grayA1a.h5"
    if not archive.exists():
        print(f"  SKIP  archive regression ({archive.name} not present)")
    else:
        caps = open_captures((("ZP1001_grayA1a.h5", 26),), geom.bound)
        try:
            pool = load_pool(caps, "white", "e2", "Gr")
            ff = ff_mod.fit_field(pool.frame, 4, 4, "bound")
            g = patch_gains(ff, rects)
            surf = ff.evaluate()
            span = 100.0 * (surf.max() - surf.min()) / surf.mean()
            want = {"resid": 2.50, "span": 8.79, geom.chip1: 1.0108,
                    geom.sample: 1.0221, geom.chip2: 0.9912}
            got = {"resid": ff.fit_rms_pct, "span": span,
                   geom.chip1: g[geom.chip1], geom.sample: g[geom.sample],
                   geom.chip2: g[geom.chip2]}
            for key, target in want.items():
                tol = 0.005 if key in ("resid", "span") else 5e-5
                check(f"ZP1001 white/e2/Gr (4,4): {key}",
                      abs(got[key] - target) < tol,
                      f"{got[key]:.4f} (want {target})")
        finally:
            for c in caps:
                c.reader.__exit__(None, None, None)

    # The publishing path, end to end on a throwaway copy of the instrument
    # database.  What matters is not that a row can be inserted but that the
    # right cell becomes each row, and that the weights come back out the far
    # side of layermodel_repo unchanged — the analysis and the instrument have
    # to be reading the same two numbers.
    good = Result(led="amber", label="e2", channel="R", exposure_us=58920,
                  group=ACCEPTABLE, mean=610.0, p_lo=580.0, p_hi=640.0,
                  patch_p10=599.5, patch_p90=632.0, masked_pct=0.0,
                  black=64.0, noise_pct=0.05, spread_pct=0.3,
                  us_min=None, us_max=None, reachable=True)
    dimmer = Result(led="amber", label="e0", channel="R", exposure_us=26187,
                    group=ACCEPTABLE, mean=271.0, p_lo=250.0, p_hi=290.0,
                    patch_p10=266.0, patch_p90=281.0, masked_pct=0.0,
                    black=64.0, noise_pct=0.08, spread_pct=0.3,
                    us_min=None, us_max=None, reachable=True)
    hopeless = Result(led="amber", label="e2", channel="B", exposure_us=58920,
                      group=UNDER, mean=23.7, p_lo=18.0, p_hi=28.0,
                      patch_p10=22.5, patch_p90=25.0, masked_pct=0.0,
                      black=64.0, noise_pct=2.0, spread_pct=1.0,
                      us_min=2.5e6, us_max=None, reachable=False)
    for r in (good, dimmer):
        r.rungs = loo_ladder(_synth_pool(geom, {"p1": curved, "p2": curved}),
                             geom, ((1, 2),))
        r.chosen = r.rungs[0]
        r.form = "2anchor"

    models = build_layermodels([dimmer, good, hopeless], geom, "selftest")
    by_key = {(m.led_color, m.bayer_layer): m for m in models}
    check("one row per LED x layer", len(models) == 2, f"{len(models)} rows")
    check("brightest rung becomes the row",
          by_key[("amber", "R")].exposure_us == 58920,
          f"ref {by_key[('amber', 'R')].exposure_us} us")
    check("percentiles come from that rung",
          by_key[("amber", "R")].p10 == 599.5)
    check("hopeless layer published as usable = 0",
          by_key[("amber", "B")].usable is False
          and by_key[("amber", "B")].w1 is None
          and by_key[("amber", "B")].p10 == 22.5)
    check("roi rowids stamped",
          (by_key[("amber", "R")].roi1, by_key[("amber", "R")].roisamp,
           by_key[("amber", "R")].roi2, by_key[("amber", "R")].roibound)
          == (36, 32, 35, 39))

    import shutil
    import tempfile
    live = REPO_ROOT / "src" / "colorimeter.sqlite"
    if not live.exists():
        print(f"  SKIP  layermodels round-trip ({live.name} not present)")
    else:
        tmpdir = Path(tempfile.mkdtemp())
        scratch = tmpdir / "colorimeter.sqlite"
        shutil.copy(live, scratch)
        was = db_manager.DBNAME
        try:
            db_manager.DBNAME = str(scratch)
            db_manager.write_one("DELETE FROM layermodels")
            n = write_layermodels(scratch, models, "selftest")
            back = layermodel_repo.read_active()
            check("rows written", n == 2, f"{n}")
            check("weights survive the round trip",
                  back[("amber", "R")].w1 == by_key[("amber", "R")].w1
                  and back[("amber", "R")].w2 == by_key[("amber", "R")].w2)
            check("usable flag survives",
                  back[("amber", "R")].usable is True
                  and back[("amber", "B")].usable is False)
            kept = layermodel_repo.keep_channels("amber", 58920)
            check("capture gate drops the hopeless layer",
                  kept == ("R", "Gr", "Gb"), f"{kept}")
            # Gr and Gb are in that answer because they have no row at all —
            # "never analysed" must keep the layer, not discard it.
            check("re-publishing supersedes rather than duplicates",
                  write_layermodels(scratch, models, "again") == 2
                  and db_manager.read_val(
                      "SELECT COUNT(*) FROM layermodels WHERE active = 1") == 2
                  and db_manager.read_val(
                      "SELECT COUNT(*) FROM layermodels") == 4)
        finally:
            db_manager.DBNAME = was
            shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n{'selftest passed' if not failures else f'{failures} FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(__doc__)
    print("Run devdesk/run_bound39a.py, or import analyze() from this module.")
