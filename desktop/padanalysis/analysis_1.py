"""analysis_1.py -- hand-driven pad analysis, run from an IDE.

Not a report.  This module holds the methods; the procedural steps are
hard-coded in the `__main__` block at the bottom and are meant to be edited
for each run.  Nothing here decides anything -- it puts the pixels, the
geometry and the photometric quantities where they can be looked at.

Read-only against `captures/*.h5`.  It writes three things:

  * extracted images into `captures/<samp_no>/` (the convention
    `hdf5inspect.section_out_dir` already defines);
  * per-sample interim arrays into `analysis/samples/<samp_no>.h5`;
  * one row per sweep into the `sample_roi` table of
    `src/colorimeter.sqlite`.

Coordinates
-----------
Everything is in **bound-crop Bayer-layer coordinates** -- 130 rows x 183
cols for bound roi 39.  A capture stores each Bayer layer already cropped to
`bound` and at half resolution, so a stream rect (x, y, w, h) becomes
((x - bound_x)//2, (y - bound_y)//2, w//2, h//2).  `probe.Capture.rects` has
done that conversion; use it rather than the `roi` table, because a past
capture was cropped against whatever `bound` was in force *then*, which its
own `/meta` records and the table no longer does.

Sections
--------
From schema 4 an archive holds two sweeps of one strip: the measurement at
the root and the **pre-image** -- the same strip before it met any reagent --
under `/pre`.  Every method takes `sec=''|'pre'` to say which.  The strip
is taken out and put back between the two, so the pad moves; the gray chips
do not, being stage furniture.  That asymmetry is the whole of the
registration rule in `get_cell_data`.

Reference: tex/gutzeit_analysis.tex, section "Reflectance photometry", for
the quantities; desktop/padanalysis/HOWTO.md for the existing pipeline this
one is checked against.

quantities
----------
sig     (signal)   
    - sig comes from c.layer(LED, rung, Bayerlayer): probe.Capture.layer
      returns lit - dark_level, where dark_level is ONE number per Bayer
      layer per sweep (the mean of the sweep's shortest-exposure dark
      frame), not a dark frame subtracted pixel by pixel.  So sig is
      dark-corrected, not raw counts.
    - sig is a numpy.ma.MaskedArray on the full frame grid, masked to 
      whatever ROI was requested (get_cell_data).
    - sig is the input to gray-chip balancing: R = sig / div, where div
      is a per-pixel map -- the cell's flat-field polynomial pegged to the
      two gray chips of the same frame (padphot.divisor_map).  So sig sits
      one step before reflectance R in the chain: sig → R → R_inf → A/KS.
    - In BalancedCell (analysis_1.py:818), sig is stored as the 
      dark-subtracted counts for that cell, alongside raw counts used
      separately just for the saturation/clipping checks 
      (raw[region] >= SATURATED_DN).
    - Whenever you see sig in this file, read it as:
         "dark-subtracted DN counts for this LED/Bayer-layer/ROI, 
          still in raw camera units,
          before any gray-card or paper normalization."

"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy import ndimage

SP = Path(__file__).resolve().parent
sys.path.insert(0, str(SP))

import padphot                                              # noqa: E402
from probe import BAYER, PRE, RAW_MAX, Capture, sections    # noqa: E402
from padrect import PadRect                                 # noqa: E402
from whitecorr import load_models, rect_mask                # noqa: E402
from fields import rung_for                                 # noqa: E402
from chemistry import cell_name                             # noqa: E402

# probe.py puts REPO/src and REPO/devdesk on the path.
import capture_h5                                           # noqa: E402
import version                                              # noqa: E402
from hdf5read import Hdf5Reader                             # noqa: E402
from hdf5write import Hdf5Writer                            # noqa: E402
import hdf5inspect                                          # noqa: E402

REPO = SP.parent.parent
CAPTURES = REPO / "captures"
DB = REPO / "src" / "colorimeter.sqlite"
ANALYSIS = SP / "analysis" / "samples"
FIG = SP / "fig"

# ── the cell sets ──────────────────────────────────────────────────────
# A "cell" is an (LED, Bayer layer) pair -- the codebase's own unit, spelled
# "led.layer" by chemistry.cell_name.  Exposure is not part of it; that
# travels alongside as the rung.
#
# The lists below are the intersection of three things: the LEDs a sweep
# lights, the layers the capture-time keep screen did not drop, and the 27
# cells that have a published flat-field model in `layermodels`.  They were
# read off C1016 and C1020 (software 0.9.0) and are hard-coded so a run is
# reproducible; `inventory()` prints what a given archive actually holds, and
# `cells_present()` regenerates these lists when the capture recipe changes.

#: The cells the analysis is about: the 27 with a published flat-field
#: model.  A given archive may hold fewer, and which ones depends on the
#: capture recipe rather than on anything chemical:
#:
#:   ladder captures (C1002, C1014, C1016) ......... all 27
#:   single-rung, exposure group 1 (C1019-C1021) ... 20
#:
#: At one exposure the capture-time keep screen drops the layers that carry
#: too little signal -- the greens under a red LED, the blue under green --
#: where a three-rung ladder catches them on one of its other rungs.
#: `cells_present(samp_no)` reports what an archive really holds and
#: `rung_verdict(samp_no, cell)` says why a cell is missing.
GRAYCELLS = [
    ("violet", "B"),
    ("royal_blue", "B"),
    ("blue", "B"), ("blue", "Gb"), ("blue", "Gr"),
    ("cyan", "B"), ("cyan", "Gb"), ("cyan", "Gr"),
    ("green", "B"), ("green", "Gb"), ("green", "Gr"),
    ("amber", "R"), ("amber", "Gr"), ("amber", "Gb"),
    ("red_orange", "R"), ("red_orange", "Gr"), ("red_orange", "Gb"),
    ("red", "R"), ("red", "Gr"), ("red", "Gb"),
    ("photo_red", "R"), ("photo_red", "Gr"), ("photo_red", "Gb"),
    ("white", "R"), ("white", "Gr"), ("white", "Gb"), ("white", "B"),
]

#: Cells the pre-image sweep contributes on the current recipe -- five LEDs,
#: one rung, two ladder rungs below e*.  These are exactly the cells for
#: which a strip can be its own blank, and therefore the only ones where
#: `padphot.delta_ks` is a per-strip quantity rather than a pooled one.
#: C1014 and earlier used a two-LED recipe and hold only royal_blue.B and
#: red.R, so anything comparing captures must ask rather than assume.
PRECELLS = [
    ("violet", "B"),
    ("royal_blue", "B"),
    ("cyan", "B"), ("cyan", "Gb"), ("cyan", "Gr"),
    ("green", "Gb"), ("green", "Gr"),
    ("photo_red", "R"),
]

#: Cells whose dark frame is worth characterising for the noise floor.
#: The dark level is already subtracted by `probe.Capture.layer` (one number
#: per Bayer layer, not a frame), so this list is not used to correct
#: anything -- it says where to *measure* read noise and
#: fixed-pattern offset, which sets the floor a Delta(K/S) can be trusted
#: above.  One dark frame is shared by every LED at the same exposure
#: (`capture_h5._dark_key` keys on microseconds, not on the LED), so cells
#: at equal exposure_us report the same frame and are not independent.
DARKCELLS = list(GRAYCELLS)

#: Cells read on the bare paper to build R_inf.  Every balanced cell needs a
#: white reference, so this tracks GRAYCELLS -- but the bare paper is about
#: twice as bright as the gray chips, so on the short wavelengths it reaches
#: the ceiling before the pad does and the reference is simply unavailable.
#: `BalancedCell.paper_clipped` says when.  On C1020 that costs nothing
#: extra, because the same three cells were already lost on the pad; on a
#: capture exposed a rung lower it would cost R_inf alone.
WHITECELLS = list(GRAYCELLS)

# ── the ROI names ──────────────────────────────────────────────────────
#: The whole bound crop: every pixel, no mask.
ROIBOUND = "bound"
#: The strip's own bare paper below the pad -- geometric, not a rect.  There
#: is no `white` rect in force in the `roi` table; the two `white_balance`
#: rects are the gray chips.  See `get_cell_data` for why this is a mask.
ROIWHITE = "white"
#: The left gray chip (stream rect `gray1`).  Stage furniture: does not move
#: with the strip.
ROIGRAY1 = "gray1"
#: The right gray chip (stream rect `gray2`).
ROIGRAY2 = "gray2"
#: The pad itself: the fitted rotated rectangle, not the seeded box.
ROISAMPLE = "sample"

#: Layer px backed off from every pad edge before the pad is measured.  The
#: pad is darker at its rim than in its middle, so a measured absorbance
#: falls as this deepens; 6 is what HOWTO.md documents and what
#: `measure.py 6 rect` used for records.json.
ERODE = 6.0

#: Exposure rung the pad finder builds its composite at.  `fields.rung_for`
#: falls back to whichever rung a capture actually holds, so this only
#: chooses among the three of a ladder capture.  `padrect.PadRect`'s own
#: default, kept here so `locate_pad` can record which rung was used.
PAD_RUNG = "e1"

#: Counts at or above this are treated as clipped.  The sensor ceiling is
#: 1023; the 23 DN margin is the one the flat-field work adopted.
SATURATED_DN = 1000
#: Below this mean the cell is read noise, not a measurement.
MIN_SIGNAL_DN = 25.0
#: A region with more than this fraction clipped is not measurable.
MAX_SAT_FRACTION = 0.001

#: Names the photometric chain an interim-store array was computed with.
#: Stamped on every array `writeh5_sampcell` writes; a cached result whose
#: stamp differs (or is missing -- written before 2026-09-27, with the
#: per-pixel dark frame and one scalar gray divisor) is recomputed, not
#: reused.  Change this whenever a step of the chain changes.
#: History: "dark_level+divisor_map" (2026-09-27), then "+hot_mask" the same
#: day when hot pixels began to be left out.
CHAIN = "dark_level+divisor_map+hot_mask"

_STRIP_ROIS = (ROISAMPLE, ROIWHITE)     # move with the strip; see registration
_ROI_NAMES = (ROIBOUND, ROIWHITE, ROIGRAY1, ROIGRAY2, ROISAMPLE)

_captures = {}      # (samp_no, section) -> Capture
_pads = {}          # (samp_no, section) -> PadRect
_models = None      # {(led, layer): whitecorr.LayerModel}
_manual_reads = {}  # samp_no -> str or None


# ── opening, caching, closing ──────────────────────────────────────────

def open_sample(samp_no, sec="") -> Capture:
    """
    Return the open `probe.Capture` for one sweep, cached.

    The archive's format versions are checked once, the way every devdesk
    tool checks them, because `probe.Capture` does not.

    Args:
        samp_no: Capture name without the .h5, e.g. 'C1020'.
        sec: '' for the measurement sweep, 'pre' for the pre-image.

    Returns:
        The cached `Capture`.  Do not close it; call `close_all()`.

    Raises:
        RuntimeError: The archive's schema or meta version is out of range.
        KeyError: `sec='pre'` on an archive with no pre-image.

    Example:
        >>> c = open_sample("C1020")                     # doctest: +SKIP
        >>> sorted(c.rects)                              # doctest: +SKIP
        ['bound', 'gray1', 'gray2', 'sample']
    """
    key = (samp_no, sec)
    if key not in _captures:
        path = CAPTURES / f"{samp_no}.h5"
        with Hdf5Reader(str(path)) as r:
            problem = version.check_archive(r.attrs("/"))
        if problem:
            raise RuntimeError(f"{samp_no}.h5: {problem}")
        _captures[key] = Capture(samp_no, sec)
    return _captures[key]


def models() -> dict:
    """
    Return `{(led, layer): LayerModel}` for the published flat fields.

    Loaded once from `src/colorimeter.sqlite` (read-only) and cached.  27
    cells are published and all are marked usable; a cell not in this dict
    has no white correction and cannot be balanced at all.

    Returns:
        The model dict from `whitecorr.load_models()`.
    """
    global _models
    if _models is None:
        _models = load_models()
    return _models


def close_all() -> None:
    """Close every cached capture and drop the pad and model caches."""
    for c in _captures.values():
        c.close()
    _captures.clear()
    _pads.clear()


def layer_shape(samp_no, sec="") -> tuple:
    """
    The (rows, cols) of this sweep's Bayer-layer grid.

    Half the bound rect, because a Bayer layer samples every other pixel in
    each direction.  130 x 183 for bound roi 39.

    Args:
        samp_no: Capture name.
        sec: Which sweep -- '' measurement, 'pre' pre-image.

    Returns:
        (rows, cols).
    """
    c = open_sample(samp_no, sec)
    return (c.bound[3] // 2, c.bound[2] // 2)


# ── what is actually in the archive ────────────────────────────────────

def cells_present(samp_no, sec="", modelled=True) -> list:
    """
    The cells this sweep really holds, in `(led, layer)` form.

    Use this to regenerate GRAYCELLS / PRECELLS after a change to the LED
    order or the capture-time keep screen.  A layer dropped by the keep
    screen is stored as a (0, 0) placeholder carrying its own statistics,
    not as a missing dataset, so it has to be tested for rather than caught.

    Args:
        samp_no: Capture name.
        sec: Which sweep -- '' measurement, 'pre' pre-image.
        modelled: Keep only cells that also have a published flat-field
            model.  False lists everything stored, including cells that
            cannot be white-corrected.

    Returns:
        Sorted list of `(led, layer)`.

    Example:
        >>> cells_present("C1020", sec="pre")            # doctest: +SKIP
        [('cyan', 'B'), ('cyan', 'Gb'), ..., ('violet', 'B')]
    """
    c = open_sample(samp_no, sec)
    keep = set(models()) if modelled else None
    out = []
    for led in c.leds:
        for lay in BAYER:
            cell = (led, lay)
            if keep is not None and cell not in keep:
                continue
            for rung in c.labels(led):
                if c.layer(led, rung, lay) is not None:
                    out.append(cell)
                    break
    return sorted(out)


def inventory(samp_no) -> None:
    """
    Print what an archive holds, sweep by sweep.  Run this first.

    One block per sweep: the LEDs, their exposure rungs and microseconds,
    which Bayer layers survived the capture-time keep screen and which were
    dropped, the gray-chip levels, and the clipped fraction over each ROI.
    Everything downstream assumes some of this; here it is stated.

    Args:
        samp_no: Capture name without the .h5.

    Returns:
        None.  Prints.

    Example:
        >>> inventory("C1020")                           # doctest: +SKIP
    """
    have = sections(samp_no)
    print(f"=== {samp_no}.h5   sections: "
          f"{', '.join(s or '(measurement)' for s in have)}")
    known = set(models())
    for sec in have:
        c = open_sample(samp_no, sec)
        a = c.meta
        run = a.get("run", {})
        print(f"\n-- section {sec or '(measurement)'}   mode={run.get('mode')}"
              f"   bound={c.bound}   grid={layer_shape(samp_no, sec)}")
        print(f"   {'LED':<11}{'rung':>5}{'us':>8}   layers"
              f"{'':<8}{'gray1':>9}{'gray2':>9}")
        for led in c.leds:
            for rung in c.labels(led):
                kept, dropped = [], []
                for lay in BAYER:
                    got = c.layer(led, rung, lay)
                    tag = lay if (led, lay) in known else lay.lower()
                    (kept if got is not None else dropped).append(tag)
                g1 = g2 = float("nan")
                for lay in BAYER:
                    got = c.layer(led, rung, lay)
                    if got is not None:
                        x, y, w, h = c.rects[ROIGRAY1]
                        g1 = float(got[0][y:y + h, x:x + w].mean())
                        x, y, w, h = c.rects[ROIGRAY2]
                        g2 = float(got[0][y:y + h, x:x + w].mean())
                        break
                drop = f"  (dropped {'+'.join(dropped)})" if dropped else ""
                print(f"   {led:<11}{rung:>5}{c.exposure_us(led, rung):>8}"
                      f"   {'+'.join(kept):<14}{g1:>9.1f}{g2:>9.1f}{drop}")

        darks = c.dark_groups()
        if not darks:
            print("   dark frames: none recorded in this archive")
        else:
            print(f"\n   dark frames (taken after the sweep, one per exposure,"
                  f" shared by every LED at that exposure):")
            print(f"   {'us':>8}{'when':>7}{'time':>14}"
                  f"{'R':>13}{'Gr':>13}{'Gb':>13}{'B':>13}"
                  f"   mean/std raw DN  ([x] = layer dropped, -- = absent)")
            for d in sorted(darks.values(), key=lambda d: d["exposure_us"]):
                cells = []
                for lay in BAYER:
                    v = d["layers"].get(lay)
                    if v is None:
                        cells.append("--")
                    elif v[1] is None:
                        cells.append(f"[{v[0]:.1f}]")
                    else:
                        cells.append(f"{v[0]:.1f}/{v[1]:.2f}")
                t = d["captured_dt"].split(" ")[-1] or "?"
                print(f"   {d['exposure_us']:>8}{d['when']:>7}{t:>14}"
                      + "".join(f"{x:>13}" for x in cells))

        print("   lower-case layer = stored but no flat-field model")
        print(f"   cells usable here: {len(cells_present(samp_no, sec))}"
              f" of {len(known)} modelled")


def saturation(samp_no, cell, roi=ROISAMPLE, sec="", rung=None) -> float:
    """
    Fraction of a region's raw counts at or above the clipping ceiling.

    Measured on the *raw* lit frame, before the dark is subtracted, because
    clipping happens in the sensor.

    Args:
        samp_no: Capture name.
        cell: `(led, layer)`.
        roi: One of the ROI names.
        sec: Which sweep -- '' measurement, 'pre' pre-image.
        rung: Exposure rung, or None for the one this method would pick.

    Returns:
        The clipped fraction, 0.0 to 1.0.  1.0 is returned when the cell is
        not stored at all, so a screen written as `sat > MAX_SAT_FRACTION`
        rejects a missing cell too.
    """
    c = open_sample(samp_no, sec)
    led, layer = cell
    rung = rung or _pick_rung(samp_no, cell, sec)
    if rung is None:
        return 1.0
    got = c.layer(led, rung, layer)
    if got is None:
        return 1.0
    region = _region(samp_no, roi, sec)
    return float((got[1][region] >= SATURATED_DN).mean())


# ── the pad, and where it is ───────────────────────────────────────────

def pad_of(samp_no, sec="") -> PadRect:
    """
    Return the fitted `padrect.PadRect` for one sweep, cached.

    The fit is geometric, not chemical: it works on `fields.composite`, the
    mean over every stored cell of signal divided by gray-chip level, which
    is nearly dye-blind.  So it places the pad the same way on a blank strip
    as on a heavily stained one -- which is what makes the pre-image and the
    measurement comparable.

    Args:
        samp_no: Capture name.
        sec: Which sweep -- '' measurement, 'pre' pre-image.

    Returns:
        The cached `PadRect`.  `.describe()` is its one-line QC summary;
        read it before believing any number that depends on the geometry.

    Example:
        >>> print(pad_of("C1020").describe())            # doctest: +SKIP
    """
    key = (samp_no, sec)
    if key not in _pads:
        _pads[key] = PadRect(open_sample(samp_no, sec), rung=PAD_RUNG)
    return _pads[key]


def _region(samp_no, roi, sec="", erode=ERODE) -> np.ndarray:
    """Boolean mask of one ROI on the layer grid.  See `get_cell_data`."""
    if roi not in _ROI_NAMES:
        raise ValueError(f"roi must be one of {_ROI_NAMES}, got {roi!r}")
    shape = layer_shape(samp_no, sec)
    if roi == ROIBOUND:
        return np.ones(shape, dtype=bool)
    if roi in (ROIGRAY1, ROIGRAY2):
        return rect_mask(shape, open_sample(samp_no, sec).rects[roi])
    pad = pad_of(samp_no, sec)
    if roi == ROISAMPLE:
        return pad.mask(erode)
    paper, depth = pad.paper_mask()
    return paper & (depth > erode)


# ── sample_roi: the located geometry ───────────────────────────────────

_SAMPLE_ROI_DDL = """
CREATE TABLE IF NOT EXISTS sample_roi (
  samp_no   TEXT NOT NULL,           -- capture name, no .h5
  section   TEXT NOT NULL DEFAULT '',-- '' measurement, 'pre' pre-image
  add_dt    TEXT,                    -- local timestamp of this fit
  rung      TEXT,                    -- rung the composite was fitted on
  aL        REAL, aR       REAL,     -- strip edges at v = yc, layer px
  width     REAL, height   REAL,
  m         REAL, yc       REAL,     -- tilt: x = aL + u + m * (v - yc)
  angle_deg REAL,
  top       REAL, bot      REAL,     -- pad's upper and lower edge, in v
  strip_top REAL,                    -- first row of strip in view
  edge_rms  REAL, n_rows   INTEGER, n_rejected INTEGER,
  top_residual REAL, top_clearance REAL, bot_source TEXT,
  bound_x   INTEGER, bound_y INTEGER,-- the crop these coords are inside
  bound_w   INTEGER, bound_h INTEGER,
  PRIMARY KEY (samp_no, section)
)
"""

_SAMPLE_ROI_COLS = (
    "samp_no", "section", "add_dt", "rung", "aL", "aR", "width", "height",
    "m", "yc", "angle_deg", "top", "bot", "strip_top", "edge_rms",
    "n_rows", "n_rejected", "top_residual", "top_clearance", "bot_source",
    "bound_x", "bound_y", "bound_w", "bound_h",
)


def locate_pad(samp_no, write=True) -> dict:
    """
    Fit the pad in every sweep of an archive and record where it is.

    One row per (samp_no, section) in `sample_roi`, replacing any earlier
    fit of the same sweep.  `bound_*` travels with the row and is not
    redundant: the crop a capture was made against is recorded in its own
    `/meta`, and the `roi` table -- which holds only the rect in force
    *now* -- cannot answer for a past capture.  A row read back against the
    wrong bound is off by the difference, silently.

    The pre-to-post transform is not stored, because it is fully determined
    by the two rows.  See `pre_to_post()`.

    Args:
        samp_no: Capture name without the .h5.
        write: False to fit and return without touching the database.

    Returns:
        `{section: {column: value}}`, the rows as written.

    Raises:
        sqlite3.Error: The write failed; the transaction is rolled back.

    Example:
        >>> rows = locate_pad("C1020")                   # doctest: +SKIP
        >>> sorted(rows)                                 # doctest: +SKIP
        ['', 'pre']
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = {}
    for sec in sections(samp_no):
        _pads.pop((samp_no, sec), None)          # always a fresh fit
        p = pad_of(samp_no, sec)
        bx, by, bw, bh = open_sample(samp_no, sec).bound
        tr = None if np.isnan(p.top_residual) else float(p.top_residual)
        rows[sec] = dict(
            samp_no=samp_no, section=sec, add_dt=now,
            rung=",".join(sorted(
                {rung_for(open_sample(samp_no, sec), led, PAD_RUNG)
                 for led in open_sample(samp_no, sec).leds})),
            aL=float(p.aL), aR=float(p.aR), width=float(p.width),
            height=float(p.height), m=float(p.m), yc=float(p.yc),
            angle_deg=float(p.angle_deg), top=float(p.top),
            bot=float(p.bot), strip_top=float(p.strip_top),
            edge_rms=float(p.edge_rms), n_rows=len(p.rows_used),
            n_rejected=int(p.n_rejected), top_residual=tr,
            top_clearance=float(p.top_clearance), bot_source=p.bot_source,
            bound_x=bx, bound_y=by, bound_w=bw, bound_h=bh,
        )
        print(f"  {samp_no}/{sec or 'post':<4} {p.describe().strip()}")
    if not write:
        return rows

    conn = sqlite3.connect(DB)
    try:
        conn.execute(_SAMPLE_ROI_DDL)
        for row in rows.values():
            conn.execute(
                f"INSERT OR REPLACE INTO sample_roi "
                f"({', '.join(_SAMPLE_ROI_COLS)}) VALUES "
                f"({', '.join('?' for _ in _SAMPLE_ROI_COLS)})",
                tuple(row[c] for c in _SAMPLE_ROI_COLS))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"  wrote {len(rows)} sample_roi row(s) to {DB.name}")
    return rows


def read_sample_roi(samp_no, sec="") -> dict:
    """
    Read one sweep's stored geometry back out of `sample_roi`.

    Args:
        samp_no: Capture name.
        sec: Which sweep -- '' measurement, 'pre' pre-image.

    Returns:
        The row as a dict of column: value.

    Raises:
        KeyError: No row for that sweep -- run `locate_pad()` first.
    """
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM sample_roi WHERE samp_no = ? AND section = ?",
            (samp_no, sec)).fetchone()
    except sqlite3.OperationalError as e:
        raise KeyError(f"sample_roi unavailable: {e}") from e
    finally:
        conn.close()
    if row is None:
        raise KeyError(f"no sample_roi row for {samp_no}/{sec}"
                       f" -- run locate_pad({samp_no!r})")
    return dict(row)


def _manual_read(samp_no):
    """The nominal concentration from `samples.manual_read`, or None."""
    if samp_no not in _manual_reads:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT manual_read FROM samples WHERE samp_no = ?",
                (samp_no,)).fetchone()
        finally:
            conn.close()
        _manual_reads[samp_no] = row[0] if row and row[0] else None
    return _manual_reads[samp_no]


def _samp_title(samp_no):
    """`samp_no`, with its nominal concentration appended when on record."""
    mr = _manual_read(samp_no)
    return f"{samp_no} {mr}ppb" if mr else samp_no


# ── pre to post registration ───────────────────────────────────────────

def pre_to_post(samp_no):
    """
    The coordinate map that carries the pre-image pad onto the measurement.

    The strip is taken out of the channel between the pre-image and the
    measurement and put back, so the pad moves and tilts a little.  Both
    sweeps get their own `PadRect`, and each yields an exact strip frame --
    `u` across the strip from its left edge, `v` down the crop -- so the map
    between them is arithmetic, with no image matching involved:

        u     = x - (aL_post + m_post * (y - yc_post))     post pixel -> u
        v_pre = top_pre + (y - top_post)                   same depth in pad
        x_pre = aL_pre + u + m_pre * (v_pre - yc_pre)

    Args:
        samp_no: Capture name.  Both sweeps must have been fitted; this
            calls `pad_of()` for each.

    Returns:
        `(y_pre, x_pre)`, two float arrays shaped like the post layer grid,
        giving for every measurement pixel the pre-image coordinate it came
        from.  Feed straight to `scipy.ndimage.map_coordinates`.

    Raises:
        KeyError: The archive has no pre-image.
    """
    post, pre = pad_of(samp_no, ""), pad_of(samp_no, PRE)
    rows, cols = layer_shape(samp_no, "")
    yy, xx = np.mgrid[0:rows, 0:cols].astype(np.float64)
    u = xx - (post.aL + post.m * (yy - post.yc))
    y_pre = pre.top + (yy - post.top)
    x_pre = pre.aL + u + pre.m * (y_pre - pre.yc)
    return y_pre, x_pre


def _register(frame, samp_no):
    """
    Resample a pre-image frame onto the measurement's layer grid.

    The pre-image's own hot pixels are set to NaN first.  Bilinear
    resampling mixes each output pixel from four input pixels, so a hot
    pixel would otherwise leak into up to four measurement pixels; as NaN it
    marks those four as missing instead, and callers mask NaN.
    """
    y_pre, x_pre = pre_to_post(samp_no)
    data = frame.filled(np.nan) if np.ma.isMaskedArray(frame) else frame
    data = np.array(data, dtype=np.float64)
    data[_hot(samp_no, PRE)] = np.nan
    return ndimage.map_coordinates(data, [y_pre, x_pre], order=1,
                                   mode="nearest")


def _hot(samp_no, sec="") -> np.ndarray:
    """
    Pixel positions left out of every measurement: hot in any Bayer layer.

    From the `hotpixels` table via `probe.Capture.hot_mask`, on this
    sweep's own layer grid.  The union over the four layers is used, not
    each cell's own layer, so that every cell keeps the same pixel
    positions and their pixel lists line up one to one -- about 70 of the
    23790 positions, 0-10 of them on a pad.  They are left out, not
    repaired.  `_region` stays purely geometric; this is applied where
    pixel values are read.
    """
    return open_sample(samp_no, sec).hot_mask()


# ── reading a cell ─────────────────────────────────────────────────────

def _measured_region(samp_no, sec="", erode=ERODE) -> np.ndarray:
    """
    The pixels a rung has to be good over: the pad and the two gray chips.

    Deliberately *not* whatever ROI the caller asked for.  The screen has to
    mean the same thing whichever region is being displayed, or a cell would
    be read at one rung on the pad and another on the chips it is divided
    by.  And it must not be the whole crop: a specular glint on the stage,
    far outside anything measured, once vetoed every rung of C1009's green
    layers (see `duotone.render_rgb`).

    **The bare paper is deliberately left out.**  It is roughly twice as
    bright as the gray chips, so on the short-wavelength illuminants it
    reaches the sensor ceiling one or two rungs before the pad and the chips
    do.  A rung where only the paper clipped still measures the pad
    perfectly well, and screening on the paper would throw that away, so the
    paper carries its own screen in `get_balanced_cell_data` and a cell that
    fails it keeps its R and loses only R_inf.  `measure.py` makes the same
    split and for the same reason.

    C1020 is not an example of that: there `blue.B`, `cyan.Gr` and `cyan.Gb`
    clip on 100 % of the paper *and* on 58-89 % of the pad, so they are lost
    outright.  Exposure group 1 over-exposes them; group 3, which C1016-18
    used, does not.
    """
    return (_region(samp_no, ROISAMPLE, sec, erode)
            | _region(samp_no, ROIGRAY1, sec)
            | _region(samp_no, ROIGRAY2, sec))


def rung_verdict(samp_no, cell, sec="", erode=ERODE) -> tuple:
    """
    Which rung this cell should be read at, and why -- especially why not.

    A cell that quietly disappears from a table is worse than one that says
    what happened to it, and on this instrument cells do disappear: at
    exposure group 1, `blue.B`, `cyan.Gr` and `cyan.Gb` clip over most of
    the pad, and at any group the greens under a red LED were never stored.

    The rung must clip nowhere in `_measured_region` -- the pad and the two
    gray chips -- and must carry more than read noise.  Among the rungs that
    pass, the brightest wins, because that is where the signal-to-noise is
    best.

    Args:
        samp_no: Capture name.
        cell: `(led, layer)`.
        sec: Which sweep -- '' measurement, 'pre' pre-image.
        erode: Layer px backed off the pad edge.

    Returns:
        `(rung, reason)`.  `rung` is the label to read, or None.  `reason`
        is 'ok' or a short phrase naming the screen that rejected it.

    Example:
        >>> rung_verdict("C1020", ("cyan", "Gr"))        # doctest: +SKIP
        (None, 'clipped: 89% of the pad at the 1023 ceiling')
    """
    c = open_sample(samp_no, sec)
    led, layer = cell
    if led not in c.leds:
        return None, "LED not lit in this sweep"
    region = _measured_region(samp_no, sec, erode)
    best, why = None, "layer dropped by the capture keep screen"
    for rung in c.labels(led):
        got = c.layer(led, rung, layer)
        if got is None:
            continue
        sig, raw = got
        clipped = float((raw[region] >= SATURATED_DN).mean())
        if clipped > 0:
            why = (f"clipped: {clipped:.0%} of the pad and chips at the "
                   f"{RAW_MAX} ceiling")
            continue
        if (raw[region] <= 0).any():
            why = "dead pixel (0 counts) inside the measured region"
            continue                      # see duotone.PadCube._reflectance
        level = float(sig[region].mean())
        if level < MIN_SIGNAL_DN:
            why = (f"only {level:.0f} DN, below MIN_SIGNAL_DN "
                   f"({MIN_SIGNAL_DN:g})")
            continue
        if best is None or level > best[1]:
            best = (rung, level)
    return (best[0], "ok") if best else (None, why)


def _pick_rung(samp_no, cell, sec="", erode=ERODE):
    """The rung from `rung_verdict`, or None.  See it for the screens."""
    return rung_verdict(samp_no, cell, sec, erode)[0]


def get_cell_data(samp_no, LED, Bayerlayer, roi=ROISAMPLE, sec="",
                  erode=ERODE, rung=None, register=True, crop=False):
    """
    The dark-subtracted signal of one cell over one ROI, in counts.

    **What you get back.**  A `numpy.ma.MaskedArray` on the full layer grid
    (130 x 183 for bound roi 39), with `mask=True` at every pixel *outside*
    the ROI.  Full grid rather than a crop, because everything downstream
    reads several regions from one frame -- the pad, the bare paper and both
    gray chips all have to line up -- and because `duotone.PadCube.paint`
    and the registration map are defined there.  Pass `crop=True` for the
    ROI's bounding box instead, for looking at rather than computing with.

    **Using the mask.**  Masked arrays do the right thing by default and the
    wrong thing if you reach past them:

        a.mean(), a.std(), a.sum()   ignore masked pixels -- what you want
        a.count()                    number of ROI pixels
        a.compressed()               1-D vector of just the ROI pixels
        ~a.mask                      the boolean region itself
        a.filled(np.nan)             plain array, NaN outside, for plotting
        a.data                       the WHOLE frame, mask ignored.
                                     np.mean(a.data) averages the crop.

    Arithmetic between two masked arrays takes the union of their masks, so
    a post frame divided by a pre frame is masked wherever either was.

    **Hot pixels are masked too.**  Every position in the `hotpixels` table
    (hot in any Bayer layer, see `_hot`) is masked in every ROI, including
    'bound'.  With registration, the pre-image's own hot pixels are masked
    as well, wherever resampling drew on them.

    **The ROIs.**  Two are rectangles and two are not, which is why this
    returns a mask rather than a rect:

        'bound'   the whole crop, nothing masked
        'gray1'   left gray chip, the rect from the archive's own /meta
        'gray2'   right gray chip, likewise
        'sample'  the *fitted* pad -- a rotated rectangle, tilted about a
                  degree, eroded by `erode` px, with any rows hidden behind
                  the stage mask dropped.  Not the seeded 'sample' box in
                  the roi table, which is only a starting guess.
        'white'   the strip's own bare paper below the pad, from
                  `PadRect.paper_mask()`: the region's own edge distance
                  must exceed `erode`, and pixels far below its median are
                  dropped as marks on the paper.  There is no `white` rect
                  in the roi table in force, and this is not a rectangle.

    **Registration.**  With `sec='pre'` and `register=True` the pre-image is
    resampled onto the measurement's grid so `post / pre` divides pixel by
    pixel.  This applies only to the strip-attached ROIs -- 'sample' and
    'white' -- because only the strip moves between the two sweeps; the gray
    chips are stage furniture and are read where they are.  One bilinear
    resample is used.  Note that interpolation correlates neighbouring
    pixels, so a per-pixel noise estimate taken after registration
    understates the noise: take sigma from `register=False`.

    Args:
        samp_no: Capture name without the .h5, e.g. 'C1020'.
        LED: LED colour name, e.g. 'royal_blue'.
        Bayerlayer: One of 'R', 'Gr', 'Gb', 'B'.
        roi: One of ROIBOUND, ROIWHITE, ROIGRAY1, ROIGRAY2, ROISAMPLE.
        sec: '' for the measurement, 'pre' to read the `/pre` sweep.
        erode: Layer px backed off from the pad edge, and from the paper
            region's edge.  Ignored by the rect ROIs.
        rung: Exposure rung, or None to take the brightest unclipped one.
        register: See above.  Ignored unless `sec='pre'` and a strip ROI.
        crop: True to return the ROI's bounding box instead of the full grid.

    Returns:
        MaskedArray of float64 counts, or None if the cell is not stored in
        this sweep -- an LED that was not lit, or a Bayer layer the
        capture-time keep screen dropped.

    Raises:
        ValueError: `roi` is not one of the five names.
        KeyError: `sec='pre'` on an archive with no pre-image.

    Example:
        >>> a = get_cell_data("C1020", "royal_blue", "B")   # doctest: +SKIP
        >>> a.shape, a.count()                              # doctest: +SKIP
        ((130, 183), 1690)
    """
    c = open_sample(samp_no, sec)
    rung = rung or _pick_rung(samp_no, (LED, Bayerlayer), sec, erode)
    if rung is None:
        return None
    got = c.layer(LED, rung, Bayerlayer)
    if got is None:
        return None
    sig = got[0]

    if sec and register and roi in _STRIP_ROIS:
        sig = _register(sig, samp_no)
        region = _region(samp_no, roi, "", erode=erode) & ~_hot(samp_no, "")
    else:
        region = _region(samp_no, roi, sec, erode) & ~_hot(samp_no, sec)

    # NaN marks pixels a registered pre-image drew from a hot pixel.
    out = np.ma.masked_invalid(np.ma.masked_array(sig, mask=~region))
    if not crop:
        return out
    ys, xs = np.nonzero(region)
    return out[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


class BalancedCell:
    """
    One cell, balanced, with every step of the chain kept.

    Attributes:
        samp_no, cell, roi, sec: what was asked for.
        rung, exposure_us: the exposure actually read.
        sig: dark-corrected counts (`lit - dark_level`), masked to the ROI.
        div: the gray-chip level at every pixel, counts, a (rows, cols)
            array on the sweep's own grid, not registered
            (`padphot.divisor_map`).  NaN outside the flat-field domain.
        R: reflectance against the gray chips, `sig / div` per pixel.  1.0
            means as bright as the chips would be at that spot.
        R_white: the bare-paper reflectance this cell was referred to, a
            scalar -- the median of R over the paper region.  **None** when
            the paper clipped at this rung; see `paper_clipped`.
        R_inf: `R / R_white`.  1.0 on the strip's own paper.  None when
            R_white is None, and so are A, KS and KS_signed.
        A: `-log10(R_inf)`.  0 on the paper.
        KS: `(1 - R_inf)^2 / 2 R_inf` = K/S.  0 on the paper.
        KS_signed: the same, carrying the sign of (1 - R_inf); use this one
            wherever K/S is averaged over pixels that straddle R_inf = 1.
        sat_frac: fraction of this ROI's raw counts at the ceiling.
        paper_sat: the same for the bare-paper region.
        paper_clipped: True when `paper_sat` exceeds MAX_SAT_FRACTION, so
            this cell has no per-strip white reference at this rung.  The
            pad measurement is still sound -- R is valid; only the step that
            refers it to the paper is missing.
    """

    __slots__ = ("samp_no", "cell", "roi", "sec", "rung", "exposure_us",
                 "sig", "div", "R", "R_white", "R_inf", "A", "KS",
                 "KS_signed", "sat_frac", "paper_sat", "paper_clipped")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def __repr__(self):
        head = (f"<BalancedCell {self.samp_no}"
                f"{('/' + self.sec) if self.sec else ''} {cell_name(self.cell)}"
                f" {self.roi} rung={self.rung} n={self.R.count()}"
                f" R={self.R.mean():.4f}")
        if self.paper_clipped:
            return head + f" paper clipped ({self.paper_sat:.3f})>"
        return (head + f" R_inf={self.R_inf.mean():.4f}"
                f" A={self.A.mean():+.4f} KS={self.KS_signed.mean():+.5f}>")


def get_balanced_cell_data(samp_no, LED, Bayerlayer, roi=ROISAMPLE,
                           sec="", erode=ERODE, rung=None, register=True,
                           quantity=None):
    """
    Read one cell and carry it through the whole photometric chain.

    **The algorithm**, one `padphot` function per step:

    1. *Dark.*  `probe.Capture.layer` returns `lit - dark_level` already.
       `dark_level` is ONE number per Bayer layer per sweep -- the mean of
       the sweep's shortest-exposure dark frame -- not a dark frame
       subtracted pixel by pixel.  See `probe.Capture.layer` and the comment
       above `capture_h5.dark_level` for the measurements behind that.
       Known hot pixels (the `hotpixels` table, hot in any Bayer layer --
       see `_hot`) are then left out: masked in the pad, the paper and both
       gray chips, not repaired.

    2. *Illumination and gray balance.*  The two gray chips sit either side
       of the strip and the illumination is not flat between them.  The
       cell's published flat-field polynomial (the `layermodels` table)
       gives the shape of the light at every pixel; the chips, read in this
       same frame, peg its level:

           div(y, x) = padphot.divisor_map(...)     counts, per pixel
           R(y, x)   = sig(y, x) / div(y, x)

       See `padphot.divisor_map` for the formula.  R is reflectance *against
       the gray chips*: R = 1 means as bright as the chips would be at that
       spot.  Until 2026-09-27 `div` was a single number, the map's average
       over the region asked for, so the pad was not flattened and the
       paper was divided by the pad's number rather than its own.

    3. *White balance.*  Kubelka-Munk wants reflectance against a diffuse
       white.  The gray chips are neither white nor a standard, so R is
       re-referred to the strip's own bare paper -- same paper, same strip,
       no chemistry, and now each pixel corrected for its own light:

           R_white = median(R over the bare-paper region)
           R_inf   = R / R_white

       Note what cancels: `div`'s level is set by the chips, and a common
       scale factor divides out of R_inf.  What does not cancel, and is the
       point of the map, is the polynomial's *shape* -- the difference in
       light between each pad pixel and the paper.

       This is `chemistry.Reduction`'s `rho`, and it cancels anything that
       changed between one session's gray card and the next's.  The paper is
       about twice as bright as the chips, so on the short wavelengths it
       clips before the pad does; when it has, R_white and everything past
       it are **None** and `paper_clipped` says so.  Step 2 is unaffected.

    4. *The transforms.*  `A = -log10(R_inf)` and `KS = f(R_inf) = K/S`.
       They are different linearisations of the same measurement, not two
       names for one; see `padphot` for which to trust when.

    **What the numbers mean.**  On the bare paper itself R_inf is 1, so A
    and KS are 0 there by construction -- that is the arithmetic check the
    chain was wired right.  On a stained pad R_inf < 1, A > 0, KS > 0, and
    KS grows faster than A as the stain deepens.  None of these is yet
    proportional to arsenic: that needs the blank subtracted,
    `padphot.delta_ks(KS_exposed, KS_blank)`, with the blank being this
    strip's own pre-image where it has one.

    **Which white reference.**  R_white is taken from the *same sweep* the
    cell came from, so a pre-image is referred to its own paper and a
    measurement to its own.  That is deliberate: it removes the session, and
    leaves the pad-versus-paper contrast, which is the chemistry.

    **The pre-image is not used by the analysis yet.**  Nothing downstream
    subtracts or divides the pre-image out of the measurement: `cell_PCA`
    reads the measurement only, `pre_post_overlay` shows `post/pre` as a
    registration check, and `padphot.delta_ks` is defined but never called.
    Using it is the next thing to explore.  When it is: the pre-image
    covers only the eight PRECELLS, at a different exposure (1000 us against
    ~2193 us), and the dark level does NOT cancel between the two, because
    every step here divides rather than subtracts raw counts.

    **Registration and the divisor.**  The light belongs to the stage and
    the strip moves, so a pre-image is divided by its own `div` in its own
    frame first, and only then is R resampled onto the measurement's grid.

    Args:
        samp_no: Capture name without the .h5.
        LED: LED colour name.
        Bayerlayer: 'R', 'Gr', 'Gb' or 'B'.
        roi: Which region to return.  The gray chips and the paper are read
            from the whole frame regardless, since they are the references.
        sec: '' for the measurement, 'pre' to read the `/pre` sweep.
        erode: Layer px backed off from the pad and paper edges.
        rung: Exposure rung, or None to pick the brightest unclipped one.
        register: Resample a pre-image onto the measurement grid; see
            `get_cell_data`.
        quantity: None for the whole `BalancedCell`; or one of 'sig', 'R',
            'R_inf', 'A', 'KS', 'KS_signed' to get just that array.

    Returns:
        A `BalancedCell`, or the single named array, or None if the cell is
        not stored in this sweep or has no published flat-field model.

    Raises:
        ValueError: `quantity` is not a known name.

    Example:
        >>> b = get_balanced_cell_data("C1020", "royal_blue", "B")
        ...                                              # doctest: +SKIP
        >>> round(float(b.R_inf.mean()), 3)              # doctest: +SKIP
        0.412
    """
    cell = (LED, Bayerlayer)
    model = models().get(cell)
    if model is None or not model.usable:
        return None
    c = open_sample(samp_no, sec)
    rung = rung or _pick_rung(samp_no, cell, sec, erode)
    if rung is None:
        return None
    got = c.layer(LED, rung, Bayerlayer)
    if got is None:
        return None
    raw_sig, raw_lit = got

    # References are read in the sweep the cell came from, on the whole
    # frame, before any registration: the chips do not move with the strip.
    # Hot pixels (`_hot`) are left out of every one of them.
    hot = _hot(samp_no, sec)
    chip1 = _region(samp_no, ROIGRAY1, sec) & ~hot
    chip2 = _region(samp_no, ROIGRAY2, sec) & ~hot
    paper = _region(samp_no, ROIWHITE, sec, erode) & ~hot
    measured = _region(samp_no, roi, sec, erode) & ~hot

    # Per-pixel divisor, in this sweep's own frame.  R is formed here, then
    # registered: the light is fixed to the stage, not to the strip.
    div = padphot.divisor_map(model, c.bound, chip1, chip2, raw_sig)
    R_frame = padphot.reflectance(raw_sig, div)
    R_frame[hot] = np.nan

    a = get_cell_data(samp_no, LED, Bayerlayer, roi=roi, sec=sec,
                      erode=erode, rung=rung, register=register)
    if a is None:
        return None
    if sec and register and roi in _STRIP_ROIS:
        R_grid = _register(R_frame, samp_no)
        region = _region(samp_no, roi, "", erode=erode) & ~_hot(samp_no, "")
    else:
        R_grid = R_frame
        region = measured
    # NaN -- where the flat-field polynomial was not fitted, or at a hot
    # pixel, or drawn from a pre-image hot pixel -- is masked too.
    R = np.ma.masked_invalid(np.ma.masked_array(R_grid, mask=~region))

    # The paper's own saturation screen.  It clips before the pad does on
    # the short wavelengths, and when it has, this cell simply has no
    # per-strip white reference at this rung -- R stays valid.
    paper_sat = float((raw_lit[paper] >= SATURATED_DN).mean())
    if paper_sat > MAX_SAT_FRACTION:
        R_white = R_inf = A = KS = KS_signed = None
    else:
        R_white = float(np.nanmedian(R_frame[paper]))
        R_inf = padphot.reflectance_infinity(R, R_white)
        A = padphot.absorbance(R_inf)
        KS = padphot.km_remission(R_inf)
        KS_signed = padphot.km_remission_signed(R_inf)

    out = BalancedCell(
        samp_no=samp_no, cell=cell, roi=roi, sec=sec, rung=rung,
        exposure_us=c.exposure_us(LED, rung), sig=a, div=div, R=R,
        R_white=R_white, R_inf=R_inf, A=A, KS=KS, KS_signed=KS_signed,
        sat_frac=float((raw_lit[measured] >= SATURATED_DN).mean()),
        paper_sat=paper_sat, paper_clipped=paper_sat > MAX_SAT_FRACTION,
    )
    if quantity is None:
        return out
    if quantity not in out.__slots__:
        raise ValueError(f"unknown quantity {quantity!r}")
    return getattr(out, quantity)


# ── the interim store ──────────────────────────────────────────────────

def _sampcell_path(samp_no) -> Path:
    return ANALYSIS / f"{samp_no}.h5"


def writeh5_sampcell(samp_no, cellname, array, **attrs) -> Path:
    """
    Append an interim array to `analysis/samples/<samp_no>.h5`.

    Written at `/<cellname>/array`, with `attrs` set on the enclosing group
    and the source archive's identity set on the file root, so a sidecar can
    never be silently paired with a re-cropped capture.

    A masked array is stored as `filled(nan)` plus a sibling `/mask` dataset,
    since HDF5 has no masked type; `readh5_sampcell` puts them back together.

    Args:
        samp_no: Capture name, which names the file.
        cellname: Group name.  `chemistry.cell_name((led, layer))` gives the
            'led.layer' spelling used elsewhere; anything HDF5 accepts as a
            name works, and '/' makes subgroups.
        array: The ndarray or MaskedArray to store.
        **attrs: Scalars recorded on the group.  Say what the array is --
            the quantity, the erosion, the rung.  `chain` is added
            automatically (see CHAIN).

    Returns:
        The path written.

    Raises:
        OSError: The file could not be written.

    Example:
        >>> writeh5_sampcell("C1020", "royal_blue.B/KS", ks,
        ...                  quantity="K/S", erode=6.0)   # doctest: +SKIP
    """
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    path = _sampcell_path(samp_no)
    root = {}
    for sec in ("", PRE):
        key = (samp_no, sec)
        if key in _captures:
            c = _captures[key]
            bx, by, bw, bh = c.bound
            tag = f"{sec}_" if sec else ""
            root.update({f"{tag}bound_x": bx, f"{tag}bound_y": by,
                         f"{tag}bound_w": bw, f"{tag}bound_h": bh})
    with Hdf5Reader(str(CAPTURES / f"{samp_no}.h5")) as r:
        a = r.attrs("/")
    root.update(samp_no=samp_no, software_ver=str(a.get("software_ver", "")),
                schema_version=int(a.get("schema_version", 1)),
                written_dt=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    masked = np.ma.isMaskedArray(array)
    data = np.ma.filled(array, np.nan) if masked else np.asarray(array)
    with Hdf5Writer(str(path), mode="a") as w:
        w.set_attrs("/", root)
        w.delete(f"/{cellname}/array")
        w.write_array(f"/{cellname}", "array", data)
        w.delete(f"/{cellname}/mask")
        if masked:
            w.write_array(f"/{cellname}", "mask",
                          np.ma.getmaskarray(array).astype(np.uint8))
        w.set_attrs(f"/{cellname}", {"chain": CHAIN, **attrs})
    return path


def readh5_sampcell(samp_no, cellname):
    """
    Read an interim array back out of `analysis/samples/<samp_no>.h5`.

    Args:
        samp_no: Capture name.
        cellname: The group name it was written under.

    Returns:
        The array.  A MaskedArray if a `/mask` sibling was stored, otherwise
        a plain ndarray.

    Raises:
        KeyError: No such file or no such group.

    Example:
        >>> ks = readh5_sampcell("C1020", "royal_blue.B/KS")
        ...                                              # doctest: +SKIP
    """
    path = _sampcell_path(samp_no)
    if not path.exists():
        raise KeyError(f"no interim store at {path}")
    with Hdf5Reader(str(path)) as r:
        if not r.exists(f"/{cellname}/array"):
            raise KeyError(f"{path.name} has no /{cellname}/array")
        a = r.read_array(f"/{cellname}/array")
        if r.exists(f"/{cellname}/mask"):
            return np.ma.masked_array(
                a, mask=r.read_array(f"/{cellname}/mask").astype(bool))
    return a


def sampcell_attrs(samp_no, cellname="/") -> dict:
    """
    The attributes stored beside an interim array, or on the file root.

    Args:
        samp_no: Capture name.
        cellname: Group name, or '/' for the file's provenance record --
            samp_no, software_ver, schema_version and the bound rect each
            sweep was cropped to.

    Returns:
        The attribute dict.

    Raises:
        KeyError: No such file.
    """
    path = _sampcell_path(samp_no)
    if not path.exists():
        raise KeyError(f"no interim store at {path}")
    with Hdf5Reader(str(path)) as r:
        return r.attrs("/" if cellname == "/" else f"/{cellname}")


def clear_sampcells(samp_no) -> bool:
    """
    Delete the interim store so the next run starts clean.

    HDF5 does not hand back the space a deleted dataset occupied, so
    rewriting arrays into the same file grows it every time.  Call this
    before a re-run rather than letting it bloat.

    Args:
        samp_no: Capture name.

    Returns:
        True if a file was removed.
    """
    path = _sampcell_path(samp_no)
    if path.exists():
        path.unlink()
        return True
    return False


def read_duotone(samp_no):
    """
    The yellow and brown amount maps `cell_PCA` wrote to the interim store.

    Args:
        samp_no: Capture name.

    Returns:
        `(yellow, brown)` -- MaskedArrays on the layer grid, in contrast
        absorbance.

    Raises:
        KeyError: No interim store, or `cell_PCA` has not been run and
            stored for this sample yet.

    Example:
        >>> yellow, brown = read_duotone("C1020")         # doctest: +SKIP
    """
    yellow = readh5_sampcell(samp_no, "duotone/yellow")
    brown = readh5_sampcell(samp_no, "duotone/brown")
    return yellow, brown


# ── extraction ─────────────────────────────────────────────────────────

def extract_images_from_hd5(samp_no, layers=True) -> None:
    """
    Write the archive's embedded images out as files, one dir per sweep.

    JPEGs go to `captures/<samp_no>/<led>.jpg` and, for the pre-image,
    `captures/<samp_no>/pre/<led>.jpg` -- the directory convention
    `hdf5inspect.section_out_dir` defines and `captures/C1002/` already
    follows.  With `layers`, each LED also gets a four-panel PNG of its
    Bayer layers on one shared 0-1023 grayscale, which is the only view that
    shows Gb at all and the one that makes a dropped layer obvious.

    Args:
        samp_no: Capture name without the .h5.
        layers: False to skip the per-layer PNGs, which are the slow part.

    Returns:
        None.  Prints what it wrote.

    Raises:
        RuntimeError: The archive's format version is out of range.
        FileNotFoundError: No such archive.

    Example:
        >>> extract_images_from_hd5("C1020")             # doctest: +SKIP
    """
    path = CAPTURES / f"{samp_no}.h5"
    if not path.exists():
        raise FileNotFoundError(path)
    with Hdf5Reader(str(path)) as r:
        problem = version.check_archive(r.attrs("/"))
        if problem:
            raise RuntimeError(f"{samp_no}.h5: {problem}")
        for sec in sections(samp_no):
            view = hdf5inspect.SectionView(r, sec) if sec else r
            hdf5inspect.cmd_jpeg_extract(view, path)
            if not layers:
                continue
            out_dir = hdf5inspect.section_out_dir(path, view)
            out_dir.mkdir(parents=True, exist_ok=True)
            for led in hdf5inspect.list_led_colors(view):
                for rung in hdf5inspect.list_lit_labels(view, led):
                    png = out_dir / f"{led}_{rung}_layers.png"
                    hdf5inspect.cmd_layer_plot(view, path, led, rung,
                                               list(BAYER), output=str(png))


# ── looking at it ──────────────────────────────────────────────────────

def _finish(fig, samp_no, what, figs=True, savefigs=False):
    """Save and/or show one figure.  Returns the path written, or None."""
    import matplotlib.pyplot as plt
    out = None
    if savefigs:
        FIG.mkdir(parents=True, exist_ok=True)
        out = FIG / f"analysis1_{samp_no}_{what}.png"
        fig.savefig(out, dpi=120)
        print(f"  wrote {out}")
    if figs:
        plt.show()
    else:
        plt.close(fig)
    return out


def roi_overlay(samp_no, sec=None, erode=ERODE, figs=True, savefigs=False):
    """
    The crop as measured, beside the same crop with every roi drawn on.

    Look at this before believing any number.  The left panel is the raw
    composite field the finder worked on; the right is that field with the
    gray chips, the seeded sample box, the fitted pad rectangle and the
    bare-paper region drawn over it.  If the rectangle is not on the pad, or
    the paper region is not on bare paper, everything downstream is void and
    no amount of arithmetic will say so.

    One row of panels per sweep, measurement above the pre-image, so the
    two `locate_pad` fits are checked together -- the pad moves between the
    sweeps and the pre fit is usually the looser of the two.

    Read `PadRect.describe()` alongside it -- printed here.  Want a width
    near 41.3 layer px, a tilt within about a degree, an edge rms under
    0.3 px, and a positive clearance (negative means the stage mask hides
    part of the pad).

    Args:
        samp_no: Capture name without the .h5.
        sec: None (default) draws every sweep the archive holds; pass '' or
            'pre' to restrict to one.
        erode: Layer px backed off the pad and paper edges, as drawn.
        figs: Show the figure.
        savefigs: Also write `fig/analysis1_<samp_no>_roi[_<sec>].png`.

    Returns:
        `{section: PadRect}`, one entry per sweep drawn.

    Example:
        >>> roi_overlay("C1020", savefigs=True)          # doctest: +SKIP
    """
    import matplotlib.pyplot as plt
    secs = sections(samp_no) if sec is None else [sec]

    fig, axes = plt.subplots(len(secs), 2, figsize=(13, 5 * len(secs)),
                             squeeze=False, layout="constrained")
    pads = {}
    for row, s in zip(axes, secs):
        prepost = s if s else 'post'
        c = open_sample(samp_no, s)
        pad = pad_of(samp_no, s)
        pads[s] = pad
        comp = pad.comp
        print(f"  {samp_no}/{prepost:<4} " + pad.describe().strip())

        for ax in row:
            ax.imshow(comp, cmap="gray", origin="upper",
                      vmin=0, vmax=np.percentile(comp, 99.5))
            ax.set_aspect("equal")
        row[0].set_title(f"{prepost}: {_samp_title(samp_no)}  "
                         f"composite reflectance (raw)")

        ax = row[1]
        for name, colour in ((ROIGRAY1, "tab:cyan"), (ROIGRAY2, "tab:cyan"),
                             (ROISAMPLE, "tab:orange")):
            x, y, w, h = c.rects[name]
            ax.add_patch(plt.Rectangle((x - .5, y - .5), w, h, fill=False,
                                       ec=colour, lw=1.2, ls="--"))
            ax.text(x, y - 2, name + (" (seed)" if name == ROISAMPLE else ""),
                    color=colour, fontsize=7)
        xy = pad.corners() + pad.corners()[:1]
        ax.plot([p[0] for p in xy], [p[1] for p in xy], "-", color="tab:red",
                lw=1.6, label="fitted pad")
        ax.contour(_region(samp_no, ROISAMPLE, s, erode).astype(float),
                   levels=[0.5], colors="tab:red", linewidths=0.9,
                   linestyles=":")
        ax.contour(_region(samp_no, ROIWHITE, s, erode).astype(float),
                   levels=[0.5], colors="tab:green", linewidths=1.2)
        ax.plot([], [], color="tab:green", label=f"{ROIWHITE} (bare paper)")
        ax.plot([], [], color="tab:red", ls=":",
                label=f"pad eroded {erode:g} px")
        ax.legend(fontsize=7, loc="lower right")
        ax.set_title(f"{prepost}: roi   "
                     f"tilt={pad.angle_deg:+.2f} deg  "
                     f"W={pad.width:.2f}  rms={pad.edge_rms:.3f} px")

    _finish(fig, samp_no, f"roi_{sec}" if sec else "roi", figs, savefigs)
    return pads


def contact_sheet(samp_no, sec="", quantity="R", figs=True,
                  savefigs=False):
    """
    Every modeled cell of one sweep as a small panel, on one shared scale.

    Reflectance rather than counts, so the panels are comparable: a cell
    whose chips read 7 DN and one whose chips read 390 DN both land near 1.0
    on the gray chips.  This is where a dead pixel, a dropped layer or a
    stray reflection shows up, and where the pad's colour is visible as the
    pad going dark in the blue cells and not in the red ones.

    Args:
        samp_no: Capture name.
        sec: Which sweep -- '' measurement, 'pre' pre-image.
        quantity: 'R' (against the gray chips), 'R_inf' (against the
            strip's own paper), 'A' or 'KS'.
        figs: Show the figure.
        savefigs: Also write `fig/analysis1_<samp_no>_cells[_pre].png`.

    Returns:
        The list of `(led, layer)` drawn, in wavelength order.

    Example:
        >>> contact_sheet("C1020", savefigs=True)        # doctest: +SKIP
    """
    import matplotlib.pyplot as plt
    from duotone import cell_nm
    cells = sorted(cells_present(samp_no, sec),
                   key=lambda c: (cell_nm(c), c[1]))
    if not cells:
        raise RuntimeError(f"{samp_no}: no modeled cell in this sweep")
    span = {"R": (0, 2.5), "R_inf": (0, 1.4), "A": (-0.2, 0.6),
            "KS": (-0.05, 0.4)}[quantity]

    ncol = min(5, len(cells))
    nrow = -(-len(cells) // ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(2.6 * ncol, 2.3 * nrow),
                             squeeze=False, layout="constrained")
    im = None
    for ax, cell in zip([a for row in axes for a in row], cells):
        b = get_balanced_cell_data(samp_no, cell[0], cell[1], roi=ROIBOUND,
                                   sec=sec)
        v = None if b is None else getattr(
            b, "KS_signed" if quantity == "KS" else quantity)
        if v is None:
            ax.axis("off")
            why = (rung_verdict(samp_no, cell, sec)[1] if b is None else
                   f"bare paper clipped ({b.paper_sat:.0%})")
            ax.set_title(f"{cell_name(cell)}\n{why}", fontsize=6.5,
                         color="tab:red")
            continue
        im = ax.imshow(np.ma.filled(v, np.nan), cmap="magma",
                       vmin=span[0], vmax=span[1])
        ax.set_aspect("equal")
        ax.set_title(f"{cell_name(cell)}  {cell_nm(cell)}nm\n"
                     f"{b.rung} {b.exposure_us}us  sat={b.sat_frac:.3f}",
                     fontsize=7)
        ax.set_xticks([])
        ax.set_yticks([])
    for ax in [a for row in axes for a in row][len(cells):]:
        ax.axis("off")
    fig.colorbar(im, ax=axes, shrink=0.6, label=quantity)
    fig.suptitle(f"{_samp_title(samp_no)}{('/' + sec) if sec else ''}  {quantity}  "
                 f"({len(cells)} modelled cells)", fontsize=10)
    _finish(fig, samp_no, f"cells_{quantity}" + (f"_{sec}" if sec else ""),
            figs, savefigs)
    return cells


def cell_table(samp_no, roi=ROISAMPLE, sec="", erode=ERODE,
               quantity="R_inf") -> dict:
    """
    Print one row per cell: how many pixels, and how they are distributed.

    The numeric companion to `contact_sheet`.  Percentiles rather than mean
    and standard deviation alone, because a splash or a mark on the paper
    shows in the tails long before it moves the mean.

    Args:
        samp_no: Capture name.
        roi: Which region to summarise.
        sec: Which sweep -- '' measurement, 'pre' pre-image.
        erode: Layer px backed off the pad and paper edges.
        quantity: 'sig', 'R', 'R_inf', 'A', 'KS' or 'KS_signed'.

    Returns:
        `{(led, layer): BalancedCell}` for every cell that had a usable
        exposure rung -- including cells whose bare paper clipped, which
        carry R but no R_inf.  A cell with no usable rung is absent.

    Example:
        >>> cell_table("C1020", roi=ROIWHITE)            # doctest: +SKIP
    """
    from duotone import cell_nm
    cells = sorted(cells_present(samp_no, sec),
                   key=lambda c: (cell_nm(c), c[1]))
    print(f"=== {samp_no}{('/' + sec) if sec else ''}   roi={roi}   "
          f"erode={erode:g}   {quantity}")
    print(f"   {'cell':<16}{'nm':>5}{'rung':>5}{'us':>7}{'n':>6}"
          f"{'mean':>10}{'std':>9}{'p10':>9}{'p50':>9}{'p90':>9}{'sat':>8}")
    out = {}
    for cell in cells:
        b = get_balanced_cell_data(samp_no, cell[0], cell[1], roi=roi,
                                   sec=sec, erode=erode)
        if b is None:
            print(f"   {cell_name(cell):<16}{cell_nm(cell):>5}"
                  f"      -- {rung_verdict(samp_no, cell, sec, erode)[1]}")
            continue
        out[cell] = b
        if getattr(b, quantity) is None:
            print(f"   {cell_name(cell):<16}{cell_nm(cell):>5}{b.rung:>5}"
                  f"{b.exposure_us:>7}      -- bare paper clipped "
                  f"({b.paper_sat:.3f}); no {quantity}, R is still good")
            continue
        v = getattr(b, quantity).compressed()
        print(f"   {cell_name(cell):<16}{cell_nm(cell):>5}{b.rung:>5}"
              f"{b.exposure_us:>7}{v.size:>6}"
              f"{v.mean():>10.4f}{v.std(ddof=1):>9.4f}"
              f"{np.percentile(v, 10):>9.4f}{np.percentile(v, 50):>9.4f}"
              f"{np.percentile(v, 90):>9.4f}{b.sat_frac:>8.4f}")
    return out


def pre_post_overlay(samp_no, LED="royal_blue", Bayerlayer="B",
                     erode=ERODE, figs=True, savefigs=False):
    """
    The registered pre-image beside the measurement, and their ratio.

    Proof that the warp is right, before anything is divided by it.  The
    corner disagreement printed here is how far the two fitted rectangles
    are from each other *after* the map is applied; it should be a small
    fraction of a pixel, since the map is derived from those very
    rectangles, and a large value means one of the two fits is wrong rather
    than that the strip moved.

    The third panel is `post / pre` per pixel -- the strip against its own
    dry self.  Structure there is chemistry; structure that follows the pad
    outline is a registration error.

    Args:
        samp_no: Capture name.  Needs an archive with a `/pre` section.
        LED: LED colour name.  Must be lit in both sweeps -- see PRECELLS.
        Bayerlayer: 'R', 'Gr', 'Gb' or 'B'.
        erode: Layer px backed off the pad edge.
        figs: Show the figure.
        savefigs: Also write `fig/analysis1_<samp_no>_prepost.png`.

    Returns:
        `(pre_registered, post, ratio)`, three MaskedArrays on the
        measurement grid.

    Raises:
        KeyError: The archive has no pre-image.
        RuntimeError: The cell is not stored in both sweeps.

    Example:
        >>> pre_post_overlay("C1020", "cyan", "B")       # doctest: +SKIP
    """
    import matplotlib.pyplot as plt
    post = get_balanced_cell_data(samp_no, LED, Bayerlayer, erode=erode)
    pre_cell = get_balanced_cell_data(samp_no, LED, Bayerlayer, sec=PRE,
                                      erode=erode, register=True)
    if post is None or pre_cell is None:
        raise RuntimeError(f"{cell_name((LED, Bayerlayer))} is not stored in "
                           f"both sweeps of {samp_no}; see PRECELLS")

    y_pre, x_pre = pre_to_post(samp_no)
    pad_post, pad_pre = pad_of(samp_no, ""), pad_of(samp_no, PRE)
    worst = 0.0
    for (xc, yc) in pad_post.corners():
        iy, ix = int(round(yc)), int(round(xc))
        iy = min(max(iy, 0), y_pre.shape[0] - 1)
        ix = min(max(ix, 0), y_pre.shape[1] - 1)
        # where that corner lands in the pre frame, vs the pre fit's corner
        du = (x_pre[iy, ix] - (pad_pre.aL + pad_pre.m
                               * (y_pre[iy, ix] - pad_pre.yc)))
        u = xc - (pad_post.aL + pad_post.m * (yc - pad_post.yc))
        worst = max(worst, abs(du - u))
    print(f"  pad shift pre->post: aL {pad_pre.aL:.2f} -> {pad_post.aL:.2f}"
          f"   top {pad_pre.top:.2f} -> {pad_post.top:.2f}"
          f"   tilt {pad_pre.angle_deg:+.2f} -> {pad_post.angle_deg:+.2f} deg")
    print(f"  corner disagreement after the map: {worst:.4f} layer px")

    ratio = post.R_inf / pre_cell.R_inf
    ys, xs = np.nonzero(~np.ma.getmaskarray(post.R_inf))
    box = (slice(max(ys.min() - 4, 0), ys.max() + 5),
           slice(max(xs.min() - 4, 0), xs.max() + 5))
    # One scale for the pre and the post, so the flat dry pad reads as flat
    # instead of being stretched into noise by its own narrow range.
    lo = float(min(pre_cell.R_inf.min(), post.R_inf.min()))
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.6), layout="constrained")
    for ax, v, title, kw in (
            (axes[0], pre_cell.R_inf, "pre-image, registered",
             dict(vmin=lo, vmax=1.05)),
            (axes[1], post.R_inf, "measurement",
             dict(vmin=lo, vmax=1.05)),
            (axes[2], ratio, "post / pre", dict(cmap="RdBu_r",
                                                vmin=0.4, vmax=1.6))):
        im = ax.imshow(np.ma.filled(v, np.nan)[box],
                       **({"cmap": "magma"} | kw))
        ax.set_aspect("equal")
        ax.set_title(f"{title}\n{cell_name((LED, Bayerlayer))}", fontsize=9)
        fig.colorbar(im, ax=ax, shrink=0.8)
    fig.suptitle(f"{_samp_title(samp_no)}  R_inf, pad eroded {erode:g} px   "
                 f"(a ring at the pad edge in post/pre means the "
                 f"registration is off, not chemistry)", fontsize=9)
    _finish(fig, samp_no, "prepost", figs, savefigs)
    return pre_cell.R_inf, post.R_inf, ratio


def cell_PCA(samp_no, figs=True, savefigs=False, erode=0.0,
             endmembers=None):
    """
    Separate the pad into yellow and brown, per pixel, and look at it.

    The pad carries two products, not one colour at two strengths: yellow at
    light arsine loading, brown at heavy.  `duotone` models each pixel's
    absorbance as `g*1 + y*eps_y + b*eps_b` -- a shading term that is the
    same in every cell, plus non-negative amounts of two dye spectra -- and
    fits the two spectra as the extreme *angles* of the pixel cloud once the
    shading direction is projected out.  The PCA is the step that finds the
    plane that cloud lives in.

    **Which endmembers.**  Fitted on this pad alone by default, which is the
    honest first look at one strip but is not comparable between strips:
    "brown" would mean a different spectrum on each.  Pass `endmembers` from
    `duotone.fit(duotone.build(names))` once you want to compare pads.

    Args:
        samp_no: Capture name without the .h5.
        figs: Draw the 2x3 figure -- top row colour / yellow / brown, bottom
            row the yellow-against-brown pixel density, then each one's
            histogram.
        savefigs: Also write `fig/analysis1_<samp_no>_pca.png`.
        erode: Layer px backed off the pad edge.  The default keeps the rim,
            because report4 found the dye-sensitive cells get *worse* with
            deeper erosion -- the rim holds chemistry, and the shading term
            is what deals with the shadow that comes with it.
        endmembers: Optional `(eps_y, eps_b)` to use instead of fitting.

    Returns:
        `(y, n)` -- yellow and brown amounts as MaskedArrays on the layer
        grid, masked outside the pad, the same shape and type
        `get_cell_data` returns.  Units are contrast absorbance.

        When no figure is asked for, `endmembers` is left to fit, and the
        interim store already holds a result for this `erode`, that result
        is read back with `read_duotone` instead of being recomputed --
        `figs`/`savefigs` force a recompute, since drawing needs the fit
        details (`cube`, `explained`, `rms`) that the cache does not carry.

    Raises:
        RuntimeError: Too few cells to fit a two-dye model.

    Example:
        >>> y, n = cell_PCA("C1020", savefigs=True)      # doctest: +SKIP
    """
    if endmembers is None and not (figs or savefigs):
        try:
            cached_attrs = sampcell_attrs(samp_no, "duotone/yellow")
        except KeyError:
            cached_attrs = None
        if (cached_attrs is not None
                and cached_attrs.get("chain") == CHAIN
                and float(cached_attrs.get("erode", float("nan"))) == erode):
            print(f"  {samp_no}: two-dye separation cached, skipping recompute")
            return read_duotone(samp_no)

    from duotone import (PadCube, contrast, hue_plane, render_rgb,
                         residual_rms, unmix)
    from duotone import endmembers as fit_endmembers

    cube = PadCube(open_sample(samp_no, ""), models(), erode=erode)
    if len(cube.cells) < 3:
        raise RuntimeError(f"{samp_no}: {len(cube.cells)} cells is too few "
                           f"for a two-dye model")
    X = contrast(cube.A)
    basis, explained = hue_plane(X)
    if endmembers is None:
        eps_y, eps_b, _ = fit_endmembers(X, basis, cube.cells)
    else:
        eps_y, eps_b = endmembers
    g, y, b = unmix(cube.A, eps_y, eps_b)
    rms = residual_rms(cube.A, eps_y, eps_b, y, b)
    print(f"  {samp_no}: {len(y)} pad px x {len(cube.cells)} cells   "
          f"PC1 {explained[0]:.3f}  PC2 {explained[1]:.3f}  "
          f"PC3 {explained[2]:.3f}")
    print(f"  two-dye residual {rms:.4f} A   median per-pixel noise "
          f"{np.median(cube.sigma):.4f} A   brown share "
          f"{b.sum() / max(y.sum() + b.sum(), 1e-9):.3f}")

    region = cube.mask
    ymap = np.ma.masked_array(cube.paint(y), mask=~region)
    nmap = np.ma.masked_array(cube.paint(b), mask=~region)
    if not (figs or savefigs):
        return ymap, nmap

    import matplotlib.pyplot as plt
    ys, xs = np.nonzero(region)
    box = (slice(max(ys.min() - 6, 0), ys.max() + 7),
           slice(max(xs.min() - 6, 0), xs.max() + 7))
    fig, ax = plt.subplots(2, 3, figsize=(13.5, 8.4), layout="constrained")

    # Scale the picture on the pad, not on the whole crop: the strip's own
    # white paper is much the brightest thing in frame and washes the pad out.
    rgb = render_rgb(cube, region=region)
    ax[0, 0].imshow(rgb[box])
    ax[0, 0].set_title(f"{_samp_title(samp_no)}  true colour (white LED)")
    y0, x0 = box[0].start, box[1].start
    xy = cube.pad.corners()
    xy = xy + xy[:1]
    ax[0, 0].plot([p[0] - x0 for p in xy], [p[1] - y0 for p in xy], "-",
                 color="red", lw=0.7)

    vmin = min(float(ymap.min()), float(nmap.min()))
    vmax = max(float(ymap.max()), float(nmap.max()))
    for j, (v, name) in ((1, (ymap, "yellow")), (2, (nmap, "brown"))):
        im = ax[0, j].imshow(np.ma.filled(v, np.nan)[box], cmap="jet",
                             vmin=vmin, vmax=vmax)
        ax[0, j].set_title(f"{name} amount")
        fig.colorbar(im, ax=ax[0, j], shrink=0.8)
    for a in ax[0]:
        a.set_aspect("equal")
        a.set_xticks([])
        a.set_yticks([])

    # Log levels: on a pad whose pixels crowd near the origin, linear
    # contours put every level in one corner and hide the tail, which is
    # the part that says whether the two amounts vary independently.
    from matplotlib.colors import LogNorm
    h, xe, ye = np.histogram2d(b, y, bins=60)
    lev = np.unique(np.round(np.logspace(0, np.log10(max(h.max(), 10)), 12)))
    cs = ax[1, 0].contourf(0.5 * (xe[:-1] + xe[1:]),
                           0.5 * (ye[:-1] + ye[1:]),
                           np.maximum(h.T, 0.9), levels=lev,
                           norm=LogNorm(vmin=1, vmax=max(h.max(), 10)),
                           cmap="viridis", extend="max")
    fig.colorbar(cs, ax=ax[1, 0], label="pixel count (log)")
    lim = max(b.max(), y.max()) * 1.05
    ax[1, 0].plot([0, lim], [0, lim], "--", color="0.45", lw=0.9)
    ax[1, 0].set_xlabel("brown amount")
    ax[1, 0].set_ylabel("yellow amount")
    ax[1, 0].set_title("pixel density   (dashed: equal amounts)")
    ax[1, 0].set_aspect("equal")
    ax[1, 1].hist(y, bins=60, color="tab:orange")
    ax[1, 1].set_title("yellow")
    ax[1, 2].hist(b, bins=60, color="tab:brown")
    ax[1, 2].set_title("brown")
    xlim = (min(ax[1, 1].get_xlim()[0], ax[1, 2].get_xlim()[0]),
           max(ax[1, 1].get_xlim()[1], ax[1, 2].get_xlim()[1]))
    ylim = (min(ax[1, 1].get_ylim()[0], ax[1, 2].get_ylim()[0]),
           max(ax[1, 1].get_ylim()[1], ax[1, 2].get_ylim()[1]))
    for a in ax[1, 1:]:
        a.set_xlim(xlim)
        a.set_ylim(ylim)
        a.set_xlabel("contrast absorbance")
        a.set_ylabel("pixels")
    for a in ax[1]:
        a.grid(alpha=0.3)
    fig.suptitle(f"{_samp_title(samp_no)}  two-dye separation, erode {erode:g} px, "
                 f"{len(cube.cells)} cells, residual {rms:.4f} A",
                 fontsize=10)
    _finish(fig, samp_no, "pca", figs, savefigs)
    return ymap, nmap

# ── procedural steps: edit for each run ────────────────────────────────

def run_sampno():

    # ── Edit this block for each run.  
    # ── Use guards to choose choose which portions to run.
    # ── Nothing below depends on anything above it except that
    # ── locate_pad() must run before anything that measures the pad.
    SAMPS = [f"C{n}" for n in range(1034, 1037)] 
    # SAMPS = ['C1025']
    # SAMP = SAMPS[-1]
    # SAMPS = [SAMP]

    # 1. What is actually in the archive.  Run this first, every time.
    if 0: 
        for SAMP in SAMPS:
            print(f"\n=== inventory ({SAMP!r})")
            inventory(SAMP)
        return
    
    # 2. Find the pad, in both sweeps, and record where it is.
    if 0:
        for SAMP in SAMPS:
            print(f"\n=== locate_pad({SAMP!r})")
            locate_pad(SAMP, write=False)
        # return

    # 3. LOOK at it before believing anything.  The fitted rectangle has to
    #    be on the pad and the green region on bare paper.
    if 0:
        for SAMP in SAMPS:
            print(f"\n=== roi_overlay({SAMP!r})")
            roi_overlay(SAMP, figs=True, savefigs=True)
        return

    # 4. Every cell at once, then the same thing as numbers.
    #    quantity = one of 'sig', 'R', 'R_inf', 'A', 'KS', 'KS_signed' 
    if 0:
        for SAMP in SAMPS:
            for quantity in ["R_inf"]:
            # for quantity in [ 'R', 'R_inf', 'A', 'KS']:
                print(f"\n=== contact_sheet({SAMP!r}) {quantity}")
                contact_sheet(SAMP, quantity=quantity, figs=True, savefigs=True)
                print()
            cell_table(SAMP, roi=ROISAMPLE)
        return

    # 5. The arithmetic check: on the strip's own bare paper R_inf must be
    #    1, and A and K/S must be 0.  If they are not, the chain is wrong.
    if 0:
        for SAMP in SAMPS:
            print(f"\n=== arithmetic check on strip white ({SAMP!r})")
            cell_table(SAMP, roi=ROIWHITE)
        return

    # 6. One cell end to end, and the strip against its own dry self.
    #    quantity = one of 'sig', 'R', 'R_inf', 'A', 'KS', 'KS_signed' 
    if 0: 
        for quantity in [ None, 'sig', 'R', 'R_inf', 'A', 'KS', 'KS_signed']:
        # for quantity in [None]:
            print(f"\n=== a single cell, quantity = {quantity!r}")
            data = get_balanced_cell_data(SAMP, "royal_blue", "B", quantity=quantity)
            #print(" ", get_balanced_cell_data(SAMP, "royal_blue", "B", quantity=quantity))
            print (" ", data)
            print(f"\n=== pre_post_overlay({SAMP!r})")
            pre_post_overlay(SAMP, "royal_blue", "B", figs=True, savefigs=False)

    # 7. The two-dye separation, and its figure.
    if 1:
        for SAMP in SAMPS:
            print(f"\n=== cell_PCA({SAMP!r})")
            # yellow, brown = cell_PCA(SAMP, figs=True, savefigs=True)
            yellow, brown = cell_PCA(SAMP, figs=True, savefigs=True)
            
    
            # 8. Keep what is worth keeping.
            clear_sampcells(SAMP)
            writeh5_sampcell(SAMP, "duotone/yellow", yellow,
                             quantity="yellow amount, contrast absorbance", erode=0.0)
            writeh5_sampcell(SAMP, "duotone/brown", brown,
                             quantity="brown amount, contrast absorbance", erode=0.0)
            for cell in PRECELLS:
                b = get_balanced_cell_data(SAMP, cell[0], cell[1])
                if b is None or b.KS_signed is None:
                    continue
                writeh5_sampcell(SAMP, f"{cell_name(cell)}/KS", b.KS_signed,
                                 quantity="K/S, signed", erode=ERODE, rung=b.rung,
                                 exposure_us=b.exposure_us, R_white=b.R_white)
            print(f"\n  interim store: {_sampcell_path(SAMP)}")
            print()

    close_all()

if __name__ == "__main__":
    run_sampno()
