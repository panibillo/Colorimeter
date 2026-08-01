"""
roi.py — Regions of interest: storage, and the coordinate conversions.

Everything ROI-related lives here.  Two jobs:

  1. Data access over the `roi` table — read the ROIs in force, save an edited
     one, restore a commissioned default.  Modelled on exposure_repo.py: an
     `active` flag, an `add_dt` stamp, and supersede-don't-delete, so an edit
     keeps the previous geometry as history and the operator can flip rows by
     hand from outside the program.
  2. The coordinate conversions, each with a name — because the arithmetic is
     one character long and getting it wrong is silent.

Coordinate spaces
-----------------
A stored ROI is `(x, y, w, h)` in **stream space**: the pixel grid of the
configured camera stream, origin top-left, x → column, y → row.  Which stream
that is depends on the sensor and the picamera2 config, so every row records
`frame_w`, `frame_h` and `model` alongside the geometry (see `load_active`).

    imx219 (Module 2)   CONFIG_5 1640 x 1232 (4:3)    CONFIG_6 3280 x 2464
    imx708 (Module 3)   CONFIG_5 2304 x 1296 (16:9)   CONFIG_6 4608 x 2592

Between models there is no scale factor — 4:3 and 16:9 are different fields of
view, not different magnifications.

`capture_frame()` returns two arrays that share the stream's H and W:

    rgb    (H, W, 3) uint8, **BGR** channel order — index rgb[y, x, c]
    bayer  (H, W)    uint16, 0..1023 mosaic       — index bayer[y, x]

so a ROI slices either one directly:  `to_slices(r)`.

`BayerCam.extract_bayer_layers()` is where it gets tricky.  It returns four
arrays of shape (H//2, W//2) by strided slicing, and layer pixel (ly, lx) is
mosaic pixel (2*ly + py, 2*lx + px) for that layer's phase offset (py, px):

    BGGR   B [0::2, 0::2]   Gb[0::2, 1::2]   Gr[1::2, 0::2]   R [1::2, 1::2]
    RGGB   R [0::2, 0::2]   Gr[0::2, 1::2]   Gb[1::2, 0::2]   B [1::2, 1::2]

Three traps follow, and this module exists partly to close them:

  A. **Parity decides colour identity.**  Extract layers from a crop that
     starts on an odd row or column and every layer is relabelled — the dict
     still says 'R' but holds Gr.  So a ROI used as a crop origin must be even.
  B. **`//2` truncates.**  An odd x floors to a window starting one mosaic
     column early; an odd w silently drops the last column of every layer.
     Hence: x, y, w and h must all be even.  `snap_even()` enforces it on save,
     `is_bayer_safe()` checks it.
  C. **Two different layer origins.**  Layers extracted from the *whole* frame
     take an absolute ROI (`to_layer_rect`); layers extracted from a crop taken
     at some bounding ROI take a ROI relative to that crop
     (`to_layer_rect_within`).  Mixing them reads the wrong pixels, or an empty
     array.  Both conversions are `//2` arithmetic and look identical at a call
     site, which is exactly why each gets a name here.

Statistics footnote: a ROI of w x h stream pixels yields (w//2) x (h//2)
samples *per layer*.  The 68 x 68 sample ROI is 34 x 34 = 1156 pixels per
layer — and the two green layers are two separate 1156-pixel populations, not
one of 2312.

Public surface
--------------
    load_active(frame_w, frame_h, model) -> dict[str, Rect]   name -> rect.Rect
    load_defaults(...)                   -> dict[str, Rect]
    save(name, rect, remark=None)        -> int   new rowid; supersedes the old
    restore(name)                        -> int   new rowid from the default row
    seed_defaults()                      -> int   rows written (first-run only)

    to_slices(r)                   -> (yslice, xslice)  for rgb[] and bayer[]
    to_layer_rect(r)               -> Rect   against whole-frame layers
    to_layer_rect_within(r, bound) -> Rect   against layers of a `bound` crop
    snap_even(r)                   -> Rect   force the Bayer phase
    is_bayer_safe(r)               -> bool
    contains_rect(outer, inner)    -> bool

    register_colorcard(cardid, cardname, bayer_array, pattern) -> bool

TODO:
- shrink rectangles to avoid edge noise
- Create new format for color cards
    - encode card id on the margin: machine readable and human readable areas
    - ¿draw alignment ticks on the margins and redo pattern recognition?
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional, Tuple

import numpy as np

import db_manager as db
from rect import Rect, DEFAULT_COLOR

log = logging.getLogger(__name__)

# The stream the seed geometry was commissioned against.  Must match the seed
# block in db_DDL.sql — the self-test checks that they agree.
SEED_FRAME = (2304, 1296)
SEED_MODEL = "imx708"

# name, role, x, y, w, h — all even, so no snapping is needed on load.
#
# `bound` is the crop the h5 archive stores, so every other ROI must fit inside
# it or that part cannot be re-measured from the archive.  It was commissioned
# at w=160, ending at x=1250, which clipped 36 px off the white patch
# (x 1150..1286) — 26% of its width.  Widened to w=196 on 2026-07-31.
SEED_ROIS: Tuple[Tuple[str, str, int, int, int, int], ...] = (
    ("sample", "sample", 1150, 790,  68,  68),
    ("white",  "white",  1150, 496, 136,  68),
    ("black1", "black",  1150, 380,  68,  68),
    ("black2", "black",  1150, 900,  68,  68),
    ("bound",  "bound",  1090, 360, 196, 630),
)

# Smallest ROI that still yields a Bayer layer sample.
MIN_SIDE_PX = 2

_SELECT = ("SELECT rowid, name, role, x, y, w, h, frame_w, frame_h, model, color "
           "FROM roi ")


# ── Coordinate conversions ──────────────────────────────────────────────────
# Each conversion gets a name so that no bare `//2` survives at a call site.

def is_bayer_safe(r: Rect) -> bool:
    """
    Return True if `r` can be converted to Bayer-layer coordinates losslessly.

    Requires x, y, w and h all even (so the crop keeps the mosaic phase and
    `//2` truncates nothing) and both sides at least MIN_SIDE_PX.

    Args:
        r: Rectangle in stream space.

    Returns:
        True if the rectangle is even on all four values and not degenerate.

    Example:
        >>> is_bayer_safe(Rect(1150, 790, 68, 68))
        True
        >>> is_bayer_safe(Rect(1151, 790, 68, 68))
        False
    """
    return (r.x % 2 == 0 and r.y % 2 == 0
            and r.w % 2 == 0 and r.h % 2 == 0
            and r.w >= MIN_SIDE_PX and r.h >= MIN_SIDE_PX)


def snap_even(r: Rect) -> Rect:
    """
    Return a copy of `r` with x, y, w and h forced even.

    Each value rounds *down* to the next even number, so the snapped rectangle
    never extends past the original's right or bottom edge and can never push
    outside the frame on those sides.  Width and height are floored at
    MIN_SIDE_PX.

    This is what keeps the Bayer phase intact — see trap A and trap B in the
    module docstring.  Applied by `save()` to every edit.

    Args:
        r: Rectangle in stream space.

    Returns:
        A new Rect, even on all four values, carrying `r`'s name and colour.

    Example:
        >>> snap_even(Rect(1151, 791, 69, 69)).xywh()
        (1150, 790, 68, 68)
    """
    return Rect(r.x & ~1, r.y & ~1,
                max(MIN_SIDE_PX, r.w & ~1),
                max(MIN_SIDE_PX, r.h & ~1),
                name=r.name, color=r.color)


def contains_rect(outer: Rect, inner: Rect) -> bool:
    """
    Return True if `inner` lies wholly within `outer`.

    Half-open on the right and bottom, matching slice semantics: touching
    edges count as inside.

    Args:
        outer: The containing rectangle.
        inner: The rectangle being tested.

    Returns:
        True if inner's four edges are all within outer's.

    Example:
        >>> contains_rect(Rect(0, 0, 100, 100), Rect(10, 10, 20, 20))
        True
    """
    return (inner.x >= outer.x and inner.y >= outer.y
            and inner.x2() <= outer.x2() and inner.y2() <= outer.y2())


def to_slices(r: Rect) -> Tuple[slice, slice]:
    """
    Return (row_slice, col_slice) to cut `r` out of a full-resolution array.

    Applies to both arrays `capture_frame()` returns — the BGR `rgb` image and
    the `bayer` mosaic — because both are (H, W, ...) at the stream size, so a
    stream-space ROI indexes them 1:1 with no conversion.

    Parity does not matter here: this is not a layer extraction, so an odd
    origin costs nothing.

    Args:
        r: Rectangle in stream space.

    Returns:
        Tuple (slice(y, y2), slice(x, x2)), numpy axis order [row, col].

    Example:
        >>> patch = rgb[to_slices(rois['sample'])]     # doctest: +SKIP
    """
    return r.xyslice()


def to_layer_rect(r: Rect) -> Rect:
    """
    Convert a stream-space ROI to coordinates in **whole-frame** Bayer layers.

    Use with layers extracted from the full mosaic — each (H//2, W//2), sharing
    the frame's origin — so the conversion is a plain absolute halving.  This
    is trap C's first case; for layers of a cropped region use
    `to_layer_rect_within()` instead.

    Logs an error if `r` is not Bayer-safe: the result is then off by up to one
    mosaic pixel, which silently changes which colour each layer holds.

    Args:
        r: Rectangle in stream space.

    Returns:
        A new Rect in layer coordinates, carrying `r`'s name and colour.

    Example:
        >>> to_layer_rect(Rect(1150, 790, 68, 68)).xywh()
        (575, 395, 34, 34)
    """
    _warn_if_unsafe(r, "to_layer_rect")
    return Rect(r.x // 2, r.y // 2, r.w // 2, r.h // 2,
                name=r.name, color=r.color)


def to_layer_rect_within(r: Rect, bound: Rect) -> Rect:
    """
    Convert a stream-space ROI to coordinates in the Bayer layers of a crop.

    Use when the mosaic was cropped to `bound` *before* the layers were
    extracted — the layers are then (bound.h//2, bound.w//2) and their origin
    is `bound`'s corner, not the frame's.  This is trap C's second case.

    `bound` must itself be even: cropping at an odd origin shifts the mosaic
    phase and relabels every layer, so 'R' would no longer hold red.  Both that
    and containment are checked and logged.

    Args:
        r:     Rectangle in stream space, expected to lie within `bound`.
        bound: The rectangle the mosaic was cropped to, in stream space.

    Returns:
        A new Rect in the crop's layer coordinates, carrying `r`'s name and
        colour.

    Example:
        >>> to_layer_rect_within(Rect(1150, 790, 68, 68),
        ...                      Rect(1090, 360, 196, 630)).xywh()
        (30, 215, 34, 34)
    """
    _warn_if_unsafe(r, "to_layer_rect_within")
    _warn_if_unsafe(bound, "to_layer_rect_within(bound)")
    if not contains_rect(bound, r):
        log.error("roi.to_layer_rect_within: %s is not inside bound %s — "
                  "the result will index outside the cropped layers",
                  r, bound)
    return Rect((r.x - bound.x) // 2, (r.y - bound.y) // 2,
                r.w // 2, r.h // 2,
                name=r.name, color=r.color)


def _warn_if_unsafe(r: Rect, where: str) -> None:
    """Log an error if `r` would lose or shift pixels on conversion to layers."""
    if not is_bayer_safe(r):
        log.error("roi.%s: %s is not Bayer-safe (x, y, w, h must be even and "
                  "sides >= %d); layer coordinates will be off by up to one "
                  "mosaic pixel", where, r, MIN_SIDE_PX)


# ── Table access ────────────────────────────────────────────────────────────

def _now_local() -> str:
    """Local wall-clock timestamp for add_dt, matching exposure_repo's format."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_rect(row: tuple, frame_w: Optional[int], frame_h: Optional[int],
             model: Optional[str]) -> Rect:
    """
    Build a Rect from one `roi` row, rescaling it if the caller's stream
    differs from the one the row was stored against.

    Three cases, per the design:
      1. Sizes match — use the row as stored.
      2. Same model and both axes scale by the same factor (CONFIG_5 <-> 6 is
         x2) — scale, then snap_even, and log at INFO.
      3. Otherwise — log an ERROR and use the row unscaled.  Across models the
         aspect ratio differs, so guessing would be worse than being loudly
         wrong.

    The returned Rect carries a `.role` attribute alongside name and colour.
    """
    _, name, role, x, y, w, h, src_w, src_h, src_model, color = row
    r = Rect(x, y, w, h, name=name, color=color or DEFAULT_COLOR)

    if frame_w and frame_h and (src_w, src_h) != (frame_w, frame_h):
        sx, sy = frame_w / src_w, frame_h / src_h
        same_model = (not model or not src_model or model == src_model)
        if same_model and abs(sx - sy) < 1e-6:
            r = snap_even(Rect(round(x * sx), round(y * sy),
                               round(w * sx), round(h * sy),
                               name=name, color=r.color))
            log.info("roi %r: scaled %dx%d -> %dx%d (x%.3g) = %s",
                     name, src_w, src_h, frame_w, frame_h, sx, r)
        else:
            log.error("roi %r: stored for %s %dx%d but the live stream is "
                      "%s %dx%d — no valid scaling between them; using the "
                      "stored geometry unchanged",
                      name, src_model, src_w, src_h, model, frame_w, frame_h)

    r.role = role
    return r


def _load(where: str, frame_w: Optional[int], frame_h: Optional[int],
          model: Optional[str]) -> Dict[str, Rect]:
    """Run `_SELECT + where` and return {name: Rect}, newest row winning."""
    rows = db.read_all(_SELECT + where + " ORDER BY rowid")
    return {row[1]: _to_rect(row, frame_w, frame_h, model) for row in rows}


def load_active(frame_w: Optional[int] = None, frame_h: Optional[int] = None,
                model: Optional[str] = None) -> Dict[str, Rect]:
    """
    Return the ROIs currently in force, keyed by name.

    Exactly one row per name has active=1, so this is the set the capture path
    measures and the viewer draws.  Each Rect carries `.name`, `.color` (the
    palette override, or rect.DEFAULT_COLOR when the column is NULL) and
    `.role` ('sample', 'white', 'black', 'bound').

    Pass the live stream size and model to have rows stored against a different
    stream rescaled where that is meaningful — see `_to_rect` for the rules.
    Omit them to get the stored geometry verbatim.

    Args:
        frame_w: Live stream width in pixels, or None to skip the check.
        frame_h: Live stream height in pixels, or None to skip the check.
        model:   Live sensor model, e.g. 'imx708', or None to skip the check.

    Returns:
        {name: Rect}.  Empty dict if the table has no active rows.

    Example:
        >>> rois = load_active(*cam.config["size"], cam.model)   # doctest: +SKIP
        >>> rgb[to_slices(rois['sample'])].mean()                # doctest: +SKIP
    """
    return _load("WHERE active = 1", frame_w, frame_h, model)


def load_defaults(frame_w: Optional[int] = None, frame_h: Optional[int] = None,
                  model: Optional[str] = None) -> Dict[str, Rect]:
    """
    Return the commissioned fallback ROIs (isdefault=1), keyed by name.

    These rows are never edited.  The viewer shows them so an edit can be
    compared against the original, and `restore()` copies one back into force.

    Args:
        frame_w: Live stream width in pixels, or None to skip the check.
        frame_h: Live stream height in pixels, or None to skip the check.
        model:   Live sensor model, or None to skip the check.

    Returns:
        {name: Rect}, same shape as load_active().
    """
    return _load("WHERE isdefault = 1", frame_w, frame_h, model)


def _insert(name: str, role: Optional[str], r: Rect,
            frame_w: int, frame_h: int, model: Optional[str],
            color: Optional[str], remark: Optional[str],
            isdefault: int = 0, active: int = 1) -> int:
    """Insert one roi row and return its rowid (0 if the write failed)."""
    db.write_one(
        "INSERT INTO roi (active, isdefault, add_dt, name, role, "
        "x, y, w, h, frame_w, frame_h, model, color, remark) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (active, isdefault, _now_local(), name, role,
         r.x, r.y, r.w, r.h, frame_w, frame_h, model, color, remark))
    return db.read_val("SELECT MAX(rowid) FROM roi") or 0


def save(name: str, rect: Rect, remark: Optional[str] = None,
         frame: Optional[Tuple[int, int]] = None,
         model: Optional[str] = None) -> int:
    """
    Supersede the active ROI named `name` with an edited rectangle.

    The geometry is passed through `snap_even()` first — an odd value would
    shift the Bayer phase, so the stored rectangle is always even.  The current
    active row is flipped to active=0 (kept as history, exactly as a
    recalibration keeps the previous exposures) and a new active row inserted.
    The isdefault row is untouched, so `restore()` can still undo this.

    `role`, the frame size and the model carry over from the row being
    superseded unless overridden, so an edit stays in the space it was made in.

    Args:
        name:   ROI identifier, e.g. 'sample'.  Must already exist.
        rect:   The edited rectangle in stream space.
        remark: Optional note stored on the new row.
        frame:  Optional (w, h) to record instead of the superseded row's.
        model:  Optional sensor model to record instead of the superseded row's.

    Returns:
        The new row's rowid, or 0 if `name` is not in the table.

    Example:
        >>> save('sample', Rect(1150, 792, 68, 68), remark='nudged down')  # doctest: +SKIP
    """
    row = db.read_one(_SELECT + "WHERE name = ? AND active = 1", (name,))
    if row is None:
        log.error("roi.save: no active ROI named %r — nothing to supersede", name)
        return 0

    _, _, role, _, _, _, _, src_w, src_h, src_model, color = row
    snapped = snap_even(rect)
    if (snapped.x, snapped.y, snapped.w, snapped.h) != rect.xywh():
        log.info("roi.save: %r snapped to even %s (was %s)",
                 name, snapped.xywh(), rect.xywh())

    fw, fh = frame if frame else (src_w, src_h)
    db.write_one("UPDATE roi SET active = 0 WHERE name = ? AND active = 1",
                 (name,))
    rowid = _insert(name, role, snapped, fw, fh, model or src_model,
                    color, remark)
    log.info("roi.save: %r -> %s (rowid %s)", name, snapped.xywh(), rowid)
    return rowid


def restore(name: str) -> int:
    """
    Put the commissioned default for `name` back in force.

    Copies the isdefault row into a new active row and deactivates the current
    one.  Nothing is deleted, so the edit being undone stays in the table as
    history and the default row itself is never consumed.

    Args:
        name: ROI identifier, e.g. 'sample'.

    Returns:
        The new row's rowid, or 0 if `name` has no isdefault row.

    Example:
        >>> restore('sample')      # doctest: +SKIP
    """
    row = db.read_one(_SELECT + "WHERE name = ? AND isdefault = 1", (name,))
    if row is None:
        log.error("roi.restore: no default row for %r", name)
        return 0

    _, _, role, x, y, w, h, fw, fh, model, color = row
    db.write_one("UPDATE roi SET active = 0 WHERE name = ? AND active = 1",
                 (name,))
    rowid = _insert(name, role, Rect(x, y, w, h), fw, fh, model, color,
                    "restored from default")
    log.info("roi.restore: %r -> %s (rowid %s)", name, (x, y, w, h), rowid)
    return rowid


def seed_defaults() -> int:
    """
    Write the commissioned ROI rows for any name missing from the table.

    db_DDL.sql seeds these already, so this is for a database that predates the
    `roi` table or has had rows removed.  Names that already have rows are left
    alone — this never overwrites an edit.  Each new name gets two rows: the
    isdefault fallback (active=0) and the row in force (active=1).

    Returns:
        Number of rows inserted (2 per missing name; 0 when nothing is missing).

    Example:
        >>> seed_defaults()
        0
    """
    frame_w, frame_h = SEED_FRAME
    written = 0
    for name, role, x, y, w, h in SEED_ROIS:
        if db.read_val("SELECT COUNT(*) FROM roi WHERE name = ?", (name,)):
            continue
        r = Rect(x, y, w, h, name=name)
        _insert(name, role, r, frame_w, frame_h, SEED_MODEL, None,
                "commissioned default", isdefault=1, active=0)
        _insert(name, role, r, frame_w, frame_h, SEED_MODEL, None,
                "seed", isdefault=0, active=1)
        written += 2
        log.info("roi.seed_defaults: seeded %r = %s", name, (x, y, w, h))
    return written


# ── Colour-card registration ────────────────────────────────────────────────
# cv2 and BayerCam are imported inside register_colorcard() so that importing
# roi.py from the UI or the capture path costs nothing.

def _filter_margin_rectangles(rectangles: list, layer_height: int, layer_width: int, 
                              margin_tolerance_fraction: float = 0.05) -> list:
    """
    Remove rectangles that represent image margins (border regions).
    
    Margins are rectangles whose boundaries are physically close to the image edges.
    This is determined by physical distance, not grid position, so it correctly
    identifies all four margins (top, bottom, left, right) regardless of grid order.
    
    Algorithm:
    1. Calculate expected interior region based on margin tolerance
    2. For each rectangle, check if it extends beyond the interior region
    3. Keep only rectangles completely within the interior region
    
    Args:
        rectangles: List of rectangle dicts with keys {'row', 'col', 'x', 'y', 'w', 'h'}
        layer_height: Height of Bayer layer image in pixels (bayer_height // 2)
        layer_width: Width of Bayer layer image in pixels (bayer_width // 2)
        margin_tolerance_fraction: Fraction of image dimension to consider as margin (default 0.15 = 15%)
    
    Returns:
        Filtered list containing only interior (non-margin) rectangles
    
    Example:
        >>> rects = [{'x': 10, 'y': 5, 'w': 100, 'h': 100, ...}]
        >>> interior = _filter_margin_rectangles(rects, 616, 820)  # half-resolution layer space
    """
    if not rectangles:
        return rectangles
    
    # Calculate margin boundary thresholds based on layer dimensions
    margin_left = int(layer_width * margin_tolerance_fraction)
    margin_right = int(layer_width * (1.0 - margin_tolerance_fraction))
    margin_top = int(layer_height * margin_tolerance_fraction)
    margin_bottom = int(layer_height * (1.0 - margin_tolerance_fraction))
    
    log.info(f"Margin boundaries: layer_height={layer_height}, layer_width={layer_width}, "
              f"left={margin_left}, right={margin_right}, "
              f"top={margin_top}, bottom={margin_bottom}")
    
    # Filter rectangles: keep only those completely within interior region
    interior_rects = []
    margin_rects = []
    
    for rect in rectangles:
        x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
        x_end = x + w
        y_end = y + h
        
        # Check if rectangle is within interior region
        # Rectangle is interior if:
        # - Left edge is past left margin
        # - Right edge is before right margin
        # - Top edge is past top margin
        # - Bottom edge is before bottom margin
        is_interior = (x >= margin_left and 
                      x_end <= margin_right and 
                      y >= margin_top and 
                      y_end <= margin_bottom)
        
        if is_interior:
            interior_rects.append(rect)
        else:
            margin_rects.append({
                'rectid': f"[{rect['row']},{rect['col']}]",
                'x': x, 'y': y, 'w': w, 'h': h,
                'x_end': x_end, 'y_end': y_end
            })
    
    # Log margin rectangles for debugging
    if margin_rects:
        log.info(f"Filtered margin rectangles:")
        for m in margin_rects:
            edges = []
            if m['x'] < margin_left:
                edges.append(f"left ({m['x']} < {margin_left})")
            if m['x_end'] > margin_right:
                edges.append(f"right ({m['x_end']} > {margin_right})")
            if m['y'] < margin_top:
                edges.append(f"top ({m['y']} < {margin_top})")
            if m['y_end'] > margin_bottom:
                edges.append(f"bottom ({m['y_end']} > {margin_bottom})")
            edges_str = ", ".join(edges)
            log.info(f"  {m['rectid']}: {edges_str}")
    
    log.info(f"Kept {len(interior_rects)} interior rectangles, removed {len(margin_rects)} margins")
    
    return interior_rects


def _classify_color(rgb_mean: tuple) -> str:
    """
    Classify RGB values to a color name.

    Args:
        rgb_mean: Tuple of (R, G, B) average values (0-255)

    Returns:
        Color name string
    """
    r, g, b = rgb_mean

    # Simple classification based on which channel dominates
    # More sophisticated methods (HSV, Euclidean distance to reference) could be used
    channels = {"R": r, "G": g, "B": b}
    max_channel = max(channels, key=channels.get)
    intensity = (r + g + b) / 3

    if intensity < 50:
        return "black"
    elif intensity > 200:
        if abs(r - g) < 20 and abs(g - b) < 20:
            return "white"
        else:
            return "light_gray"
    elif max_channel == "R" and r > g + 30 and r > b + 30:
        return "red" if intensity < 150 else "pink"
    elif max_channel == "G" and g > r + 30 and g > b + 30:
        return "green"
    elif max_channel == "B" and b > r + 30 and b > g + 30:
        return "blue"
    elif r > 150 and g > 100 and b < 100:
        return "yellow" if g > b + 20 else "orange"
    elif r > 100 and g < 100 and b < 100:
        return "brown" if g > 30 else "dark_red"
    elif r > 100 and b > 100 and abs(r - b) < 30:
        return "purple"
    elif r > 100 and g > 100 and abs(r - g) < 30:
        return "cyan" if b > 100 else "yellow"
    else:
        return "gray"


# def _extract_bayer_channels(bayer_array: np.ndarray) -> dict:
#     """
#     Extract 4 Bayer color channels from BGGR pattern.

#     Args:
#         bayer_array: 2D numpy array (H x W) with BGGR Bayer values

#     Returns:
#         Dict with keys 'B', 'Gb', 'Gr', 'R' containing extracted channels
#     """
#     H, W = bayer_array.shape
#     channels = {
#         "B":  bayer_array[0:H:2, 0:W:2],      # Even rows, even cols
#         "Gb": bayer_array[0:H:2, 1:W:2],      # Even rows, odd cols
#         "Gr": bayer_array[1:H:2, 0:W:2],      # Odd rows, even cols
#         "R":  bayer_array[1:H:2, 1:W:2],      # Odd rows, odd cols
#     }
#     return channels


def register_colorcard(cardid: int, cardname: str, bayer_array: np.ndarray,
                       pattern: str = "BGGR") -> bool:
    """
    Analyze an image of a color card and register rectangle ROIs to database.

    Uses Canny edge detection and Hough line transform for robust grid detection:

    Algorithm:
    1. Extract best Bayer channel and normalize to 0-255
    2. Apply Gaussian blur to reduce noise
    3. Use Canny edge detection to find edges
    4. Use Hough line transform to detect grid lines (horizontal & vertical)
    5. Cluster nearby parallel lines (lines at same position = same grid line)
    6. Find line intersections to define rectangular patches
    7. Validate grid structure (expect ~5x7 internal grid)
    8. Extract RGB mean values for each patch and classify colors
    9. Write rectangles to database

    The rectangles it writes are in **Bayer layer** coordinates (half the
    stream size), not stream coordinates — the grid is detected on a layer.
    That differs from the `roi` table, whose geometry is stream space.

    cv2 and BayerCam are imported here rather than at module scope so that the
    UI and the capture path can import roi.py without paying for them.

    Args:
        cardid: Unique identifier for this color card
        cardname: Human-readable name for this card (e.g., "cal_card_01")
        bayer_array: 2D numpy array (H x W) of Bayer mosaic values (uint16)
        pattern: Bayer layout of `bayer_array`, "BGGR" or "RGGB".  Pass
            cam.bayer_pattern; the default matches both current profiles.

    Returns:
        True if registration successful, False on error
    """
    import cv2
    from bayer_cam import BayerCam

    log.info(f"Registering color card '{cardname}' (ID {cardid})")

    try:
        H, W = bayer_array.shape
        log.debug(f"Bayer array shape: {H} x {W}")

        # ==========================================
        # STEP 1: Extract best channel and normalize
        # ==========================================
        channels = BayerCam.extract_bayer_layers(bayer_array, pattern)
        log.debug(f"Extracted Bayer channels: {list(channels.keys())}")

        # Pick channel with highest contrast (std dev)
        channel_stdevs = {name: float(np.std(channel)) for name, channel in channels.items()}
        best_channel = max(channel_stdevs, key=channel_stdevs.get)
        log.info(f"Channel stdevs: B={channel_stdevs['B']:.1f}, Gb={channel_stdevs['Gb']:.1f}, "
                 f"Gr={channel_stdevs['Gr']:.1f}, R={channel_stdevs['R']:.1f}")
        log.info(f"Using '{best_channel}' channel for edge detection (stdev={channel_stdevs[best_channel]:.1f})")

        # Normalize to 0-255 uint8
        channel_data = channels[best_channel].astype(np.float32)
        channel_normalized = ((channel_data - channel_data.min()) / (channel_data.max() - channel_data.min() + 1e-6) * 255).astype(np.uint8)
        log.debug(f"Normalized channel to 0-255 range")

        # ==========================================
        # STEP 2: Detect edges using Canny
        # ==========================================
        log.info("Detecting edges with Canny edge detector...")
        
        # Apply Gaussian blur to reduce noise
        blurred = cv2.GaussianBlur(channel_normalized, (5, 5), 1.0)
        
        # Apply Canny edge detection
        # For checkerboard, we want to find the transitions between colors
        # Threshold values: lower = more edges, higher = fewer edges
        edges = cv2.Canny(blurred, threshold1=30, threshold2=80)
        log.debug(f"Edge map created: {np.count_nonzero(edges)} edge pixels")

        # ==========================================
        # STEP 3: Detect grid lines using Hough transform
        # ==========================================
        log.info("Detecting grid lines with Hough transform...")
        
        # Use HoughLinesP for line segment detection (more robust than HoughLines)
        # minLineLength: minimum line length to accept (helps filter noise)
        # maxLineGap: maximum gap between line segments to join them
        lines = cv2.HoughLinesP(edges, rho=1, theta=np.pi/180, threshold=50, 
                                 minLineLength=50, maxLineGap=10)
        
        if lines is None or len(lines) == 0:
            log.error("No lines detected by Hough transform!")
            return False
        
        log.debug(f"Detected {len(lines)} line segments")

        # ==========================================
        # STEP 4: Classify lines as horizontal or vertical
        # ==========================================
        horizontal_lines = []
        vertical_lines = []
        
        for line in lines:
            x1, y1, x2, y2 = line[0]
            angle = np.abs(np.arctan2(y2 - y1, x2 - x1) * 180 / np.pi)
            
            # Normalize angle to 0-90 degrees
            if angle > 90:
                angle = 180 - angle
            
            # Classify based on angle
            # Horizontal: angle close to 0 (tolerance ±15°)
            # Vertical: angle close to 90 (tolerance ±15°)
            if angle < 15:
                horizontal_lines.append(((y1 + y2) / 2, x1, x2))  # Store as (y_pos, x_start, x_end)
            elif angle > 75:
                vertical_lines.append(((x1 + x2) / 2, y1, y2))    # Store as (x_pos, y_start, y_end)
        
        log.info(f"Classified lines: {len(horizontal_lines)} horizontal, {len(vertical_lines)} vertical")
        
        if len(horizontal_lines) < 2 or len(vertical_lines) < 2:
            log.error(f"Not enough lines detected! H={len(horizontal_lines)}, V={len(vertical_lines)}")
            return False

        # ==========================================
        # STEP 5: Cluster parallel lines
        # ==========================================
        log.info("Clustering parallel lines...")
        
        def cluster_lines_1d(lines_1d, tolerance=10):
            """
            Cluster 1D positions of parallel lines.
            lines_1d: list of positions (y for horizontal, x for vertical)
            Returns: list of clustered positions (averaged)
            """
            if not lines_1d:
                return []
            
            positions = sorted([pos for pos, *_ in lines_1d])
            clusters = []
            current_cluster = [positions[0]]
            
            for pos in positions[1:]:
                if pos - current_cluster[-1] < tolerance:
                    current_cluster.append(pos)
                else:
                    clusters.append(int(np.mean(current_cluster)))
                    current_cluster = [pos]
            if current_cluster:
                clusters.append(int(np.mean(current_cluster)))
            
            return clusters
        
        h_positions = cluster_lines_1d(horizontal_lines, tolerance=15)
        v_positions = cluster_lines_1d(vertical_lines, tolerance=15)
        
        log.info(f"Clustered to {len(h_positions)} horizontal and {len(v_positions)} vertical grid lines")
        log.debug(f"  Horizontal positions: {h_positions}")
        log.debug(f"  Vertical positions: {v_positions}")
        
        # ==========================================
        # STEP 5b: Interpolate missing lines in large gaps
        # ==========================================
        # Detect anomalously large gaps that suggest missed edge detection
        # (e.g., low-contrast regions where lines split between adjacent rows)
        
        def interpolate_missing_lines(positions: list, dimension_name: str = "grid", 
                                      max_gap_multiplier: float = 1.8) -> list:
            """
            Detect and interpolate missing lines in a grid.
            
            If a gap is significantly larger than the median gap, interpolate
            a line at the midpoint. This recovers grid lines missed in 
            low-contrast regions.
            
            Args:
                positions: Sorted list of grid line positions
                dimension_name: "horizontal" or "vertical" for logging
                max_gap_multiplier: Threshold ratio (gap / median_gap) for interpolation
                                  Lower = more aggressive (default 1.3 = 30% larger)
            
            Returns:
                New position list with interpolated lines added
            """
            if len(positions) < 2:
                return positions
            
            gaps = [positions[i+1] - positions[i] for i in range(len(positions)-1)]
            median_gap = np.median(gaps)
            mean_gap = np.mean(gaps)
            
            log.debug(f"Gap statistics ({dimension_name}): median={median_gap:.1f}, "
                     f"mean={mean_gap:.1f}, min={min(gaps):.1f}, max={max(gaps):.1f}")
            
            new_positions = [positions[0]]
            interpolated_count = 0
            split_gaps = []  # Track which gaps were likely split rows
            
            for i in range(len(positions) - 1):
                gap = positions[i+1] - positions[i]
                
                # If gap is much larger than median, interpolate missing line(s)
                if gap > median_gap * max_gap_multiplier:
                    # Estimate number of missing lines based on gap size
                    # For a gap of 2.5x median, we're missing about 1 line
                    num_missing = max(1, int(round(gap / median_gap - 1)))
                    
                    split_gaps.append((i, gap, num_missing))
                    log.info(f"Large gap detected: {gap:.1f} vs {median_gap * max_gap_multiplier:.1f} "
                            f"({num_missing} lines likely missed)")
                    
                    for j in range(num_missing):
                        interpolated = int(positions[i] + (j+1) * gap / (num_missing + 1))
                        new_positions.append(interpolated)
                        interpolated_count += 1
                        log.debug(f"  Interpolated {dimension_name} line at position {interpolated}")
                
                new_positions.append(positions[i+1])
            
            if split_gaps:
                log.info(f"Interpolation summary ({dimension_name}): "
                        f"added {interpolated_count} lines from {len(split_gaps)} gaps")
            
            return sorted(new_positions)
        
        h_positions_before = len(h_positions)
        v_positions_before = len(v_positions)
        
        h_positions = interpolate_missing_lines(h_positions, dimension_name="horizontal")
        v_positions = interpolate_missing_lines(v_positions, dimension_name="vertical")
        
        if len(h_positions) > h_positions_before or len(v_positions) > v_positions_before:
            log.info(f"After interpolation: {len(h_positions)} horizontal, {len(v_positions)} vertical "
                    f"(added {len(h_positions) - h_positions_before}H, {len(v_positions) - v_positions_before}V)")

        # ==========================================
        # STEP 6: Build rectangles from grid
        # ==========================================
        log.info("Building rectangle grid from line intersections...")
        
        rectangles = []
        for row in range(len(h_positions) - 1):
            for col in range(len(v_positions) - 1):
                y1 = h_positions[row]
                y2 = h_positions[row + 1]
                x1 = v_positions[col]
                x2 = v_positions[col + 1]
                
                rect_h = max(1, y2 - y1)
                rect_w = max(1, x2 - x1)
                
                # Filter out very small rectangles (noise)
                if rect_h > 5 and rect_w > 5:
                    rectangles.append({
                        "row": row,
                        "col": col,
                        "x": x1,
                        "y": y1,
                        "w": rect_w,
                        "h": rect_h,
                    })
        
        grid_rows = len(h_positions) - 1
        grid_cols = len(v_positions) - 1
        log.info(f"Found {len(rectangles)} valid rectangles in {grid_rows}×{grid_cols} grid")
        
        # Validate expected grid size (5x7 checkerboard pattern)
        if grid_rows < 4 or grid_cols < 5:
            log.warning(f"Grid size {grid_rows}×{grid_cols} is smaller than expected 5×7")
        if grid_rows > 8 or grid_cols > 10:
            log.warning(f"Grid size {grid_rows}×{grid_cols} is larger than expected 5×7 (may include border)")

        # ==========================================
        # STEP 6b: Filter out margin rectangles
        # ==========================================
        log.info("Filtering out margin rectangles...")
        
        # Get layer dimensions (channels are half resolution of full Bayer array)
        layer_h, layer_w = channels['B'].shape
        rectangles = _filter_margin_rectangles(rectangles, layer_h, layer_w)
        
        if not rectangles:
            log.error("No interior rectangles remaining after filtering margins!")
            return False

        # ==========================================
        # STEP 7: Extract and classify colors
        # ==========================================
        log.info("Classifying rectangle colors...")
        
        # Prepare normalized Bayer channels for color extraction
        r_data = channels["R"].astype(np.float32)
        gbgr_data = (channels["Gb"].astype(np.float32) + channels["Gr"].astype(np.float32)) / 2
        b_data = channels["B"].astype(np.float32)
        
        # Normalize each channel independently to 0-255
        r_norm = ((r_data - r_data.min()) / (r_data.max() - r_data.min() + 1e-6) * 255).astype(np.uint8)
        g_norm = ((gbgr_data - gbgr_data.min()) / (gbgr_data.max() - gbgr_data.min() + 1e-6) * 255).astype(np.uint8)
        b_norm = ((b_data - b_data.min()) / (b_data.max() - b_data.min() + 1e-6) * 255).astype(np.uint8)

        for rect in rectangles:
            x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
            
            # Convert from full resolution to Bayer channel coordinates (Bayer is half resolution)
            bx, by = max(0, x // 2), max(0, y // 2)
            bw = min((x + w) // 2, r_norm.shape[1]) - bx
            bh = min((y + h) // 2, r_norm.shape[0]) - by
            
            # Ensure valid dimensions
            if bw <= 0 or bh <= 0:
                log.warning(f"Rect [{rect['row']},{rect['col']}] has invalid dimensions after conversion")
                rect["rgb"] = (0, 0, 0)
                rect["color"] = "unknown"
                continue
            
            # Extract mean RGB values
            r_val = int(np.mean(r_norm[by:by+bh, bx:bx+bw]))
            g_val = int(np.mean(g_norm[by:by+bh, bx:bx+bw]))
            b_val = int(np.mean(b_norm[by:by+bh, bx:bx+bw]))
            
            color_name = _classify_color((r_val, g_val, b_val))
            
            rect["color"] = color_name
            rect["rgb"] = (r_val, g_val, b_val)
            log.debug(f"  Rect [{rect['row']},{rect['col']}]: {color_name} RGB=({r_val},{g_val},{b_val})")

        # ==========================================
        # STEP 8: Write to database
        # ==========================================
        log.info(f"Writing {len(rectangles)} rectangles to database...")
        
        for rectid, rect in enumerate(rectangles):
            sql = """
                INSERT INTO colorcard 
                    (cardid, cardname, rectid, colorname, x, y, w, h)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
            vals = (
                cardid,
                cardname,
                rectid,
                rect["color"],
                rect["x"],
                rect["y"],
                rect["w"],
                rect["h"],
            )
            
            rows_affected = db.write_one(sql, vals)
            if rows_affected != 1:
                log.warning(f"Unexpected rows affected: {rows_affected}")
        
        log.info(f"✓ Successfully registered {len(rectangles)} rectangles for '{cardname}'")
        return True

    except Exception as e:
        log.error(f"Error in register_colorcard(): {e}")
        import traceback
        log.debug(traceback.format_exc())
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Self-test — the converters run pure; the table round-trip runs on a throwaway
# copy of the real DB so the live colorimeter.sqlite is never touched.
#
# Colour-card registration is not covered here; for that use test/test_roi.py.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import shutil
    import tempfile
    from pathlib import Path

    logging.basicConfig(level=logging.CRITICAL,
                        format="%(levelname)-8s [%(name)s] %(message)s")

    def check(label, got, want):
        ok = "OK " if got == want else "XX "
        print(f"  {ok}{label:46s} got={got!r}")
        assert got == want, f"{label}: {got!r} != {want!r}"

    print("== is_bayer_safe ==")
    check("even rect is safe",   is_bayer_safe(Rect(1150, 790, 68, 68)), True)
    check("odd x is not",        is_bayer_safe(Rect(1151, 790, 68, 68)), False)
    check("odd y is not",        is_bayer_safe(Rect(1150, 791, 68, 68)), False)
    check("odd w is not",        is_bayer_safe(Rect(1150, 790, 67, 68)), False)
    check("odd h is not",        is_bayer_safe(Rect(1150, 790, 68, 67)), False)
    check("degenerate is not",   is_bayer_safe(Rect(0, 0, 0, 0)), False)

    print("== snap_even ==")
    check("rounds all four down", snap_even(Rect(1151, 791, 69, 69)).xywh(),
          (1150, 790, 68, 68))
    check("even rect unchanged",  snap_even(Rect(1150, 790, 68, 68)).xywh(),
          (1150, 790, 68, 68))
    check("never exceeds x2",     snap_even(Rect(11, 11, 11, 11)).x2() <= 22, True)
    check("floors at MIN_SIDE_PX", snap_even(Rect(4, 4, 1, 1)).xywh(), (4, 4, 2, 2))
    check("snapped is safe",      is_bayer_safe(snap_even(Rect(7, 9, 13, 15))), True)
    check("keeps name and colour",
          (lambda s: (s.name, s.color))(snap_even(Rect(1, 1, 5, 5, 'a', '#F00'))),
          ('a', '#F00'))

    print("== to_slices: numpy row/col order ==")
    ys, xs = to_slices(Rect(1150, 790, 68, 68))
    check("row slice is y",  (ys.start, ys.stop), (790, 858))
    check("col slice is x",  (xs.start, xs.stop), (1150, 1218))
    frame = np.zeros((1296, 2304), dtype=np.uint16)
    frame[790:858, 1150:1218] = 7
    check("slices cut the right region",
          int(frame[to_slices(Rect(1150, 790, 68, 68))].sum()), 68 * 68 * 7)

    print("== to_layer_rect: absolute, whole-frame layers ==")
    check("sample halves",  to_layer_rect(Rect(1150, 790, 68, 68)).xywh(),
          (575, 395, 34, 34))
    check("bound halves",   to_layer_rect(Rect(1090, 360, 196, 630)).xywh(),
          (545, 180, 98, 315))
    # A layer of the whole frame really is indexable there.
    layer = frame[0::2, 0::2]                      # (648, 1152)
    lr = to_layer_rect(Rect(1150, 790, 68, 68))
    check("layer shape",    layer.shape, (648, 1152))
    check("in bounds",      (lr.x2() <= layer.shape[1] and lr.y2() <= layer.shape[0]), True)
    check("samples per layer", int(layer[lr.xyslice()].size), 34 * 34)

    print("== to_layer_rect_within: relative to a crop ==")
    bound  = Rect(1090, 360, 196, 630)
    sample = Rect(1150, 790, 68, 68)
    check("sample inside bound",  contains_rect(bound, sample), True)
    check("relative halves", to_layer_rect_within(sample, bound).xywh(),
          (30, 215, 34, 34))
    # Crop first, then extract — the archive path.  Both routes must read the
    # same pixels, which is the whole point of having two named converters.
    crop        = frame[bound.xyslice()]
    crop_layer  = crop[0::2, 0::2]                 # (315, 98)
    check("crop layer shape", crop_layer.shape, (315, 98))
    check("crop route sum",
          int(crop_layer[to_layer_rect_within(sample, bound).xyslice()].sum()),
          int(layer[to_layer_rect(sample).xyslice()].sum()))
    check("both routes non-empty",
          int(layer[to_layer_rect(sample).xyslice()].sum()) > 0, True)
    check("absolute rect would miss the crop",
          crop_layer[to_layer_rect(sample).xyslice()].size, 0)

    print("== to_layer_rect_within with an odd bound relabels layers ==")
    odd_bound = Rect(1091, 360, 160, 630)
    check("odd bound is unsafe", is_bayer_safe(odd_bound), False)

    # Point db_manager at a throwaway copy of the real DB for the table tests.
    live = Path(db.DBNAME)
    tmp = Path(tempfile.mkdtemp()) / "test.sqlite"
    shutil.copy(live, tmp)
    db.DBNAME = str(tmp)

    print("== load_active: the seeded table ==")
    rois = load_active()
    check("five names",     sorted(rois), ["black1", "black2", "bound", "sample", "white"])
    check("sample geometry", rois["sample"].xywh(), (1150, 790, 68, 68))
    check("bound geometry",  rois["bound"].xywh(),  (1090, 360, 196, 630))
    check("role attached",   rois["black1"].role,   "black")
    check("name attached",   rois["white"].name,    "white")
    check("colour defaults", rois["sample"].color,  DEFAULT_COLOR)
    check("all seeds Bayer-safe",
          all(is_bayer_safe(r) for r in rois.values()), True)
    check("sample inside bound",
          contains_rect(rois["bound"], rois["sample"]), True)
    check("one active row per name", db.read_val(
        "SELECT COUNT(*) FROM roi WHERE active = 1"), 5)

    print("== seed values agree with db_DDL.sql ==")
    for nm, role, x, y, w, h in SEED_ROIS:
        check(f"{nm} matches the DDL seed", rois[nm].xywh(), (x, y, w, h))
        check(f"{nm} role", rois[nm].role, role)

    print("== load_defaults ==")
    defs = load_defaults()
    check("same names",     sorted(defs), sorted(rois))
    check("same geometry",  defs["sample"].xywh(), rois["sample"].xywh())

    print("== frame-size handling ==")
    check("matching size is verbatim",
          load_active(2304, 1296, "imx708")["sample"].xywh(), (1150, 790, 68, 68))
    check("same model, x2 scales",
          load_active(4608, 2592, "imx708")["sample"].xywh(), (2300, 1580, 136, 136))
    check("scaled result stays even",
          all(is_bayer_safe(r) for r in load_active(4608, 2592, "imx708").values()), True)
    check("cross-model is left unscaled",
          load_active(1640, 1232, "imx219")["sample"].xywh(), (1150, 790, 68, 68))

    print("== save: supersedes, snaps, keeps history ==")
    rowid = save("sample", Rect(1151, 791, 69, 69), remark="selftest")
    check("returned a rowid",   rowid > 0, True)
    check("snapped on the way in", load_active()["sample"].xywh(), (1150, 790, 68, 68))
    save("sample", Rect(1200, 800, 40, 40), remark="selftest 2")
    check("edit is in force",   load_active()["sample"].xywh(), (1200, 800, 40, 40))
    check("still one active",   db.read_val(
        "SELECT COUNT(*) FROM roi WHERE name='sample' AND active=1"), 1)
    # 2 seeded (default + active) + one row per save, nothing deleted.
    check("history kept",       db.read_val(
        "SELECT COUNT(*) FROM roi WHERE name='sample'"), 4)
    check("default untouched",  load_defaults()["sample"].xywh(), (1150, 790, 68, 68))
    check("role carried over",  load_active()["sample"].role, "sample")
    check("frame carried over", db.read_val(
        "SELECT frame_w FROM roi WHERE name='sample' AND active=1"), 2304)
    check("other ROIs untouched", load_active()["white"].xywh(), (1150, 496, 136, 68))
    check("unknown name is refused", save("nosuch", Rect(0, 0, 8, 8)), 0)

    print("== restore ==")
    check("restore returns a rowid", restore("sample") > 0, True)
    check("back to the default",  load_active()["sample"].xywh(), (1150, 790, 68, 68))
    check("still one active",     db.read_val(
        "SELECT COUNT(*) FROM roi WHERE name='sample' AND active=1"), 1)
    check("default row not consumed", db.read_val(
        "SELECT COUNT(*) FROM roi WHERE name='sample' AND isdefault=1"), 1)
    check("unknown name is refused",  restore("nosuch"), 0)

    print("== seed_defaults is a no-op on a seeded table ==")
    check("nothing written",   seed_defaults(), 0)
    db.write_one("DELETE FROM roi WHERE name = 'white'")
    check("re-seeds one name", seed_defaults(), 2)
    check("re-seeded geometry", load_active()["white"].xywh(), (1150, 496, 136, 68))
    check("sample not re-seeded", db.read_val(     # 2 seeded + 2 saves + 1 restore
        "SELECT COUNT(*) FROM roi WHERE name='sample'"), 5)

    print("== empty table degrades quietly ==")
    db.write_one("DELETE FROM roi")
    check("no active ROIs", load_active(), {})
    check("save on empty",  save("sample", Rect(0, 0, 8, 8)), 0)
    check("seed refills",   seed_defaults(), 10)

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("\n== all roi self-tests passed ==")
