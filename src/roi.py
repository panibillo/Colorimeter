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

    to_slices(r)                   -> (yslice, xslice)  for rgb[] and bayer[]
    to_layer_rect(r)               -> Rect   against whole-frame layers
    to_layer_rect_within(r, bound) -> Rect   against layers of a `bound` crop
    snap_even(r)                   -> Rect   force the Bayer phase
    is_bayer_safe(r)               -> bool
    contains_rect(outer, inner)    -> bool

    by_role(rois, role)            -> Rect | None   the first ROI for a role
    all_by_role(rois, role)        -> list[Rect]    every ROI for a role
    missing_roles(rois, roles)     -> list[str]     which of `roles` are absent
    required_roles()               -> tuple[str]    from config roi.required_roles
    check_requirements(rois)       -> list[(sev, msg)]  the boot-time rules
    duplicate_active_names()       -> list[(name, n)]   hand-edit damage
    restore_role(role)             -> list[(name, rowid)]  reseed a whole role
    ROLE_SAMPLE / ROLE_WHITE_BALANCE / ROLE_BLACK / ROLE_BOUND

Select ROIs by **role**, never by name.  A name is an operator-editable label
— 'black1'/'black2' have already become 'chip1'/'chip2' once — so a literal
name in application code silently stops matching.  devdesk/ and dev/ scripts
are exempt: they are throwaway analyses pinned to a single archive.

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


class RoiMissing(RuntimeError):
    """
    A required ROI role has no active row.

    Always a bug, never a condition to work around: db_bootstrap runs
    provisioning/config_defaults.sql at every launch and fails loudly if it
    cannot seed the `roi` table, so by the time anything here reads a ROI the
    row is guaranteed to exist.  This means either the role was renamed, or
    the row was removed by hand.
    """


# ── Roles ───────────────────────────────────────────────────────────────────
# A ROI's *role* is what it is for; its *name* is a label the operator can
# change, and can add to or delete from the table at will.  Application code
# must therefore select by role and never by a hardcoded name: the
# commissioned 'black1'/'black2' rows have already been superseded by
# 'chip1'/'chip2' once, and anything that had spelled those names out would
# have gone quietly wrong rather than loudly missing.
#
# Roles are not unique — a card may carry several patches of the same role —
# so `by_role()` returns the first and `all_by_role()` returns every one.
# Only devdesk/ and dev/ scripts may hardcode names; they are throwaway
# analyses pinned to one archive, not the instrument.

ROLE_SAMPLE        = "sample"
ROLE_WHITE_BALANCE = "white_balance"
ROLE_BLACK         = "black"
ROLE_BOUND         = "bound"

# How many ROIs each role may have in force: (fewest, most before it is worth
# mentioning).  None as the second means any number is fine.
#
#   bound          exactly one — it is *the* crop the archive stores, so a
#                  second one makes which crop was written ambiguous
#   sample          one is the design; a second is more likely a leftover
#                  edit than a deliberate two-cuvette card, so it is said out
#                  loud without being refused
#   white_balance   as many as the card carries.  This card has two gray
#                  chips, and pooling them is the point
#
# A role in roi.required_roles with no entry here takes _ANY_NUMBER: present
# at least once, no upper limit.  The *list* of required roles is commissioned
# data and lives in the config table; these multiplicity rules are what the
# capture and analysis code is written against, so they live with the code.
_ANY_NUMBER = (1, None)
MULTIPLICITY = {
    ROLE_BOUND:         (1, 1),
    ROLE_SAMPLE:        (1, 1),
    ROLE_WHITE_BALANCE: _ANY_NUMBER,
}


def required_roles() -> Tuple[str, ...]:
    """
    Return the roles a run cannot proceed without.

    Read from the config table (roi.required_roles, editable=0) rather than
    written down here, so a unit commissioned for a different card can be
    given a different requirement without a code change, and so the boot check
    and the capture path read one list instead of two.

    Roles, not names — a name is an operator-editable label ('black1'/'black2'
    are already 'chip1'/'chip2'), so matching on one would stop working
    silently.

    Returns:
        Tuple of role strings.  Falls back to the built-in three if the
        parameter is unreadable, because a boot check that cannot state its
        own requirement is worse than one stating the usual one.

    Example:
        >>> required_roles()                            # doctest: +SKIP
        ('bound', 'sample', 'white_balance')
    """
    try:
        import config_repo as config
        roles = config.required_roi_roles()
    except Exception:
        log.exception("roi.required_roles: falling back to the built-in set")
        roles = ()
    return roles or (ROLE_BOUND, ROLE_SAMPLE, ROLE_WHITE_BALANCE)

# The commissioned geometry lives only in provisioning/config_defaults.sql now
# — db_bootstrap seeds it into the `roi` table at every launch — so it is not
# duplicated here.  See that file's "roi" section for the numbers and the
# rationale (bound was widened from w=160 to w=196 on 2026-07-31, etc.).

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
        A new Rect, even on all four values, carrying `r`'s name, colour and
        role.

    Example:
        >>> snap_even(Rect(1151, 791, 69, 69)).xywh()
        (1150, 790, 68, 68)
    """
    out = Rect(r.x & ~1, r.y & ~1,
               max(MIN_SIDE_PX, r.w & ~1),
               max(MIN_SIDE_PX, r.h & ~1),
               name=r.name, color=r.color)
    # `role` is not a Rect constructor argument — _to_rect() attaches it after
    # building one — so it has to be carried across by hand.  Dropping it is
    # not cosmetic: the image viewer's Save looks the edited rectangle up *by
    # role* to check that `sample` still lies inside `bound`, so a role-less
    # copy made that rule skip the one edit it exists to police.
    role = getattr(r, "role", None)
    if role is not None:
        out.role = role
    return out


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


# ── Role lookup ─────────────────────────────────────────────────────────────
# The alternative to `rois["bound"]`.  See the note beside the role constants
# for why a literal name in application code is a bug waiting to happen.

def all_by_role(rois: Dict[str, Rect], role: str) -> list:
    """
    Return every ROI with the given role, ordered by name.

    Args:
        rois: The dict from load_active() / load_defaults().
        role: One of the ROLE_* constants, or any role string in the table.

    Returns:
        List of Rects, possibly empty.  Ordered by name so the result is
        stable across calls and across machines.

    Example:
        >>> all_by_role({'b1': Rect(0, 0, 2, 2)}, ROLE_BLACK)
        []
    """
    return [r for _, r in sorted(rois.items())
            if getattr(r, "role", None) == role]


def by_role(rois: Dict[str, Rect], role: str) -> Optional[Rect]:
    """
    Return the first ROI with the given role, or None if there is none.

    Use for the roles that are singular by construction — 'bound' is the only
    one the instrument insists on.  'sample' is singular by design but only
    warned about when it is not (see MULTIPLICITY), and 'white_balance' may
    legitimately repeat, so prefer `all_by_role()` for both.

    Args:
        rois: The dict from load_active() / load_defaults().
        role: One of the ROLE_* constants.

    Returns:
        The Rect, or None when no active ROI carries that role.

    Example:
        >>> by_role(load_active(), ROLE_BOUND)            # doctest: +SKIP
        Rect(1082, 478, 196, 660, name='bound')
    """
    found = all_by_role(rois, role)
    return found[0] if found else None


ERROR   = "error"
WARNING = "warning"


def _overhang(bound: Rect, r: Rect) -> str:
    """
    Describe how far `r` sticks out of `bound`, or '' when it does not.

    Args:
        bound: The ROLE_BOUND rectangle — the crop the archive stores.
        r:     Any other rectangle in the same space.

    Returns:
        Text like 'left by 12px  bottom by 4px', or '' when `r` is contained.

    Example:
        >>> _overhang(Rect(0, 0, 10, 10), Rect(2, 2, 4, 4))
        ''
        >>> _overhang(Rect(0, 0, 10, 10), Rect(-2, 0, 4, 4))
        'left by 2px'
    """
    if contains_rect(bound, r):
        return ""
    return "  ".join(
        f"{edge} by {px}px" for edge, px in (
            ("left",   bound.x - r.x),   ("right",  r.x2() - bound.x2()),
            ("top",    bound.y - r.y),   ("bottom", r.y2() - bound.y2()))
        if px > 0)


def validate(rois: Dict[str, Rect], roles: Optional[Tuple[str, ...]] = None
             ) -> list:
    """
    Check the assumptions the Bayer-layer conversions rest on.

    Two families of problem, both from roi.py's traps A and B:

      * a ROI that is not Bayer-safe, so layer extraction loses its mosaic
        phase and every layer coordinate is off by up to one pixel;
      * a ROI extending outside the ROLE_BOUND one, which is the only crop the
        archive stores.  Containment matters at two severities: the run itself
        measures against whole-frame layers, so an overhang costs it nothing.
        What it costs is the archive.  Losing part of `sample` makes the
        reading unrecoverable (error); losing part of a reference patch only
        degrades later re-analysis (warning).

    Reports rather than raises, so the two callers can each apply their own
    policy: the capture path logs at these severities and proceeds, while the
    boot check applies the softer rules in `check_requirements`.

    Args:
        rois:  The dict from load_active().
        roles: Restrict the check to ROIs in these roles; None checks all of
               them.  The boot check passes required_roles(), because a role
               nothing reads is the operator's business, not the instrument's.

    Returns:
        (severity, message) pairs, severity being ERROR or WARNING.  Empty
        when the geometry is sound.

    Example:
        >>> validate({})
        []
    """
    def wanted(r):
        return roles is None or getattr(r, "role", None) in roles

    out = []
    for name, r in sorted(rois.items()):
        if wanted(r) and not is_bayer_safe(r):
            out.append((ERROR,
                        f"ROI {name!r} is not Bayer-safe ({r.xywh()}) — layer "
                        f"coordinates will be off by up to one mosaic pixel"))

    bound = by_role(rois, ROLE_BOUND)
    if bound is None:
        return out

    for name, r in sorted(rois.items()):
        if r is bound or not wanted(r):
            continue
        lost = _overhang(bound, r)
        if not lost:
            continue
        severity = (ERROR if getattr(r, "role", None) == ROLE_SAMPLE
                    else WARNING)
        out.append((severity,
                    f"ROI {name!r} {r.xywh()} extends outside bound "
                    f"{bound.xywh()} ({lost}) — that part is not stored in "
                    f"the archive and cannot be re-measured from it"))
    return out


def check_requirements(rois: Dict[str, Rect],
                       roles: Optional[Tuple[str, ...]] = None) -> list:
    """
    Apply the boot-time rules on which ROIs must be in force, and how many.

    Three rules, in the order an operator would meet them:

      1. every required role is filled — an absent one is an ERROR, and the
         caller is expected to have tried `restore_role` first;
      2. no required role is over-filled — see MULTIPLICITY.  A WARNING: the
         instrument still runs, on `by_role`'s first match, and the operator
         is told which extra rows exist rather than having one silently
         ignored;
      3. every required ROI lies inside `bound` — a WARNING, because what is
         lost is the archive rather than the reading.  This is the softer of
         the two containment policies; `validate` holds the capture path's.

    ROIs in any other role are not looked at, by design: a card may carry as
    many reference patches as its operator finds useful, and none of them is
    the instrument's business.

    Args:
        rois:  The dict from load_active().
        roles: The required roles; None reads them from config.

    Messages are written for a 320x240 panel, where every one of them has to
    fit on a line or two — terse, and naming the region rather than explaining
    what it costs.  `validate` keeps the fuller wording, which goes to the log.

    Returns:
        (severity, message) pairs, severity being ERROR or WARNING.  Empty
        when every rule is satisfied.

    Example:
        >>> check_requirements({}, roles=(ROLE_BOUND,))[0][0]
        'error'
    """
    roles = required_roles() if roles is None else roles
    out = []

    for role in roles:
        found = all_by_role(rois, role)
        fewest, most = MULTIPLICITY.get(role, _ANY_NUMBER)
        if len(found) < fewest:
            out.append((ERROR, f"no region has the role {role!r}"))
        elif most is not None and len(found) > most:
            out.append((WARNING,
                        f"{len(found)} regions have the role {role!r}; "
                        f"using {found[0].name!r}"))

    bound = by_role(rois, ROLE_BOUND)
    if bound is not None:
        for name, r in sorted(rois.items()):
            if r is bound or getattr(r, "role", None) not in roles:
                continue
            lost = _overhang(bound, r)
            if lost:
                out.append((WARNING, f"{name!r} reaches outside bound, {lost}"))
    return out


def duplicate_active_names() -> list:
    """
    Return names carrying more than one active row.

    `load_active` keys on name, so a duplicate does not raise — it disappears,
    the later rowid winning and the earlier one being measured by nothing.
    Only a hand-edit can produce one: `save` deactivates the row it supersedes,
    so the application never writes a second.  Asked of the table rather than
    of load_active()'s dict, which by then has already collapsed them.

    Returns:
        List of (name, count) for each duplicated name, ordered by name.
        Empty on a healthy table.

    Example:
        >>> duplicate_active_names()                    # doctest: +SKIP
        [('white_balance', 2)]
    """
    return [tuple(row) for row in db.read_all(
        "SELECT name, COUNT(*) FROM roi WHERE active = 1 "
        "GROUP BY name HAVING COUNT(*) > 1 ORDER BY name")]


def missing_roles(rois: Dict[str, Rect], roles: Tuple[str, ...]) -> list:
    """
    Return the roles from `roles` that no ROI in `rois` fills.

    For callers that cannot proceed without particular roles and want to say
    exactly which are absent rather than raising a KeyError on a name.

    Args:
        rois: The dict from load_active().
        roles: Roles that must be present.

    Returns:
        List of missing role strings, in the order given.  Empty when all are
        present.

    Example:
        >>> missing_roles({}, (ROLE_SAMPLE, ROLE_BOUND))
        ['sample', 'bound']
    """
    return [role for role in roles if by_role(rois, role) is None]


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
    `.role` ('sample', 'white_balance', 'black', 'bound', ...).

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


def load_calibration(frame_w: Optional[int] = None,
                     frame_h: Optional[int] = None,
                     model: Optional[str] = None) -> Dict[str, Rect]:
    """
    Return the calibration patch set (active=2), keyed by name.

    A calibration capture images a uniform card through a `bound` chosen wider
    than the measurement one, so that different sub-bounds can be tried without
    re-imaging, and samples it at patches spread across that bound.  Those rows
    describe a card on the stage rather than the instrument's own targets, so
    they must not be in force during a run — hence active=2 rather than 1.
    Both sets live in the same table because they share the stream space and
    the same `bound` semantics.

    The set is expected to hold one `bound`, one `sample` role at the centre,
    and several `white` roles spread to the corners; nothing here enforces that,
    since which patches a card carries is the operator's choice.

    Args:
        frame_w: Live stream width in pixels, or None to skip the check.
        frame_h: Live stream height in pixels, or None to skip the check.
        model:   Live sensor model, or None to skip the check.

    Returns:
        {name: Rect}, same shape as load_active().  Empty dict when no
        calibration capture has been set up.
    """
    return _load("WHERE active = 2", frame_w, frame_h, model)


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


def restore_role(role: str) -> list:
    """
    Put every commissioned default filling `role` back in force.

    The boot check works in roles, not names, and a role may be filled by more
    than one region — this card's white balance is a pair of gray chips — so
    restoring "the default for a role" means restoring all of them, not the
    first one found.

    Args:
        role: One of the ROLE_* constants, or any role string in the table.

    Returns:
        List of (name, rowid) for each row restored, ordered by name.  Empty
        when no isdefault row offers this role, which is the case the caller
        has to report as unrecoverable.

    Example:
        >>> restore_role('bound')      # doctest: +SKIP
        [('bound', 41)]
    """
    names = sorted(n for n, r in load_defaults().items()
                   if getattr(r, "role", None) == role)
    if not names:
        log.error("roi.restore_role: no default row has the role %r", role)
        return []
    out = []
    for name in names:
        rowid = restore(name)
        if rowid:
            out.append((name, rowid))
    return out


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

    # Seed the copy the way a launch would.  The rules below are about the
    # table as the instrument actually meets it, and db_bootstrap is what puts
    # it in that shape — it applies the role renames and reactivates any
    # commissioned default left with nothing in force.
    import db_bootstrap
    db_bootstrap.ensure(tmp)

    print("== load_active: whatever is in force ==")
    # Asserted against the table as it stands, not against a fixed list of
    # names: the operator adds, renames and supersedes ROIs from the
    # instrument, and a literal list here would fail the moment they do (as
    # it did when 'black1'/'black2' became 'chip1'/'chip2').  What must hold
    # is structural — the roles the instrument needs are filled, every
    # rectangle is Bayer-safe, and the sample lies inside the crop.
    rois = load_active()
    active_names = sorted(rois)
    print(f"     (in force: {', '.join(active_names)})")
    check("every active row is loaded", len(rois) > 0, True)
    # One active row per name is the invariant `save` maintains, but only a
    # hand-edit can break it and the live table is hand-edited — so the count
    # asserted is the one that always holds, and a breach is reported instead.
    dupes = duplicate_active_names()
    if dupes:
        print(f"     (duplicate active names: {dupes} — load_active keeps one)")
    check("one Rect per distinct active name", len(rois), db.read_val(
        "SELECT COUNT(DISTINCT name) FROM roi WHERE active = 1"))
    check("names round-trip onto the Rects",
          all(rois[n].name == n for n in rois), True)
    check("every ROI carries a role",
          all(getattr(r, "role", None) for r in rois.values()), True)
    check("colour defaults when the column is NULL",
          all(r.color for r in rois.values()), True)
    check("all active ROIs Bayer-safe",
          all(is_bayer_safe(r) for r in rois.values()), True)

    print("== the roles the instrument cannot run without ==")
    # The list is commissioned data, not a constant here — required_roles()
    # reads config roi.required_roles.  What the self-test can assert is that
    # it is non-empty and that the table satisfies it.
    required = required_roles()
    print(f"     (required: {', '.join(required)})")
    check("the requirement is stated", len(required) >= 1, True)
    check("none missing", missing_roles(rois, required), [])
    sample_r = by_role(rois, ROLE_SAMPLE)
    bound_r  = by_role(rois, ROLE_BOUND)
    check("by_role finds the sample", sample_r is not None, True)
    check("sample inside bound", contains_rect(bound_r, sample_r), True)
    check("by_role agrees with all_by_role",
          all_by_role(rois, ROLE_SAMPLE)[0].xywh(), sample_r.xywh())
    check("an unfilled role gives None", by_role(rois, "no_such_role"), None)
    check("and lists as missing",
          missing_roles(rois, ("no_such_role",)), ["no_such_role"])
    check("all_by_role is empty for an unfilled role",
          all_by_role(rois, "no_such_role"), [])

    print("== check_requirements: the boot-time rules ==")
    # Reported, not asserted clean: a commissioned unit whose bound has been
    # re-drawn legitimately shows containment warnings here, and that is
    # exactly what the splash screen is for.  What must hold is that nothing
    # is at ERROR severity, since that is the level that stops a run.
    found = check_requirements(rois, required)
    for severity, message in found:
        print(f"     ({severity}: {message})")
    check("no required role unfilled",
          [m for sev, m in found if sev == ERROR], [])

    # Synthetic tables, so each rule can be shown failing on its own.
    one = {"b": Rect(0, 0, 100, 100), "s": Rect(10, 10, 20, 20),
           "w": Rect(40, 10, 20, 20)}
    one["b"].role, one["s"].role, one["w"].role = (
        ROLE_BOUND, ROLE_SAMPLE, ROLE_WHITE_BALANCE)
    for n, r in one.items():
        r.name = n
    roles3 = (ROLE_BOUND, ROLE_SAMPLE, ROLE_WHITE_BALANCE)
    check("a complete set is silent", check_requirements(one, roles3), [])

    two = dict(one)
    two["s2"] = Rect(10, 40, 20, 20)
    two["s2"].role, two["s2"].name = ROLE_SAMPLE, "s2"
    sev = [x[0] for x in check_requirements(two, roles3)]
    check("a second sample warns", sev, [WARNING])
    check("and names the one that wins",
          "'s'" in check_requirements(two, roles3)[0][1], True)

    many = dict(one)
    many["w2"] = Rect(70, 10, 20, 20)
    many["w2"].role, many["w2"].name = ROLE_WHITE_BALANCE, "w2"
    check("a second white_balance does not",
          check_requirements(many, roles3), [])

    outside = dict(one)
    outside["w"] = Rect(90, 10, 20, 20)
    outside["w"].role, outside["w"].name = ROLE_WHITE_BALANCE, "w"
    out_found = check_requirements(outside, roles3)
    check("a required ROI outside bound warns",
          [x[0] for x in out_found], [WARNING])
    check("and says by how much", "right by 10px" in out_found[0][1], True)

    ignored = dict(one)
    ignored["x"] = Rect(200, 200, 20, 20)
    ignored["x"].role, ignored["x"].name = "gray1", "x"
    check("an unrequired role outside bound is not checked",
          check_requirements(ignored, roles3), [])

    gone = {n: r for n, r in one.items() if n != "w"}
    check("an unfilled required role is an error",
          [x[0] for x in check_requirements(gone, roles3)], [ERROR])

    print("== validate scoped to roles ==")
    check("scoped past the offender", validate(ignored, roles=roles3), [])
    check("unscoped it is not silent", len(validate(outside)) >= 1, True)

    print("== load_defaults ==")
    # The defaults are the *commissioned* set, which the active set has since
    # diverged from — that is what superseding is for.  So the invariant is
    # not that the two agree, only that every default is well formed and that
    # the roles needed to fall back on are all present.
    defs = load_defaults()
    check("defaults exist",       len(defs) > 0, True)
    check("defaults are Bayer-safe",
          all(is_bayer_safe(r) for r in defs.values()), True)
    check("defaults fill the required roles",
          missing_roles(defs, required), [])
    check("and satisfy the rules once restored",
          [m for sev, m in check_requirements(defs, required)
           if sev == ERROR], [])
    check("one isdefault row per default name", db.read_val(
        "SELECT COUNT(*) FROM roi WHERE isdefault = 1"), len(defs))

    print("== frame-size handling ==")
    # Relative to whatever the sample is now, so an edit on the instrument
    # cannot invalidate the arithmetic being tested.
    sname = sample_r.name
    sxywh = sample_r.xywh()
    check("matching size is verbatim",
          load_active(2304, 1296, "imx708")[sname].xywh(), sxywh)
    check("same model, x2 scales",
          load_active(4608, 2592, "imx708")[sname].xywh(),
          tuple(v * 2 for v in sxywh))
    check("scaled result stays even",
          all(is_bayer_safe(r)
              for r in load_active(4608, 2592, "imx708").values()), True)
    check("cross-model is left unscaled",
          load_active(1640, 1232, "imx219")[sname].xywh(), sxywh)

    print("== save: supersedes, snaps, keeps history ==")
    history_before = db.read_val(
        "SELECT COUNT(*) FROM roi WHERE name = ?", (sname,))
    other = next((n for n in rois if n != sname), None)
    other_before = rois[other].xywh() if other else None
    rowid = save(sname, Rect(1151, 791, 69, 69), remark="selftest")
    check("returned a rowid",   rowid > 0, True)
    check("snapped on the way in",
          load_active()[sname].xywh(), (1150, 790, 68, 68))
    save(sname, Rect(1200, 800, 40, 40), remark="selftest 2")
    check("edit is in force",
          load_active()[sname].xywh(), (1200, 800, 40, 40))
    check("still one active",   db.read_val(
        "SELECT COUNT(*) FROM roi WHERE name = ? AND active=1", (sname,)), 1)
    check("history kept, nothing deleted", db.read_val(
        "SELECT COUNT(*) FROM roi WHERE name = ?", (sname,)),
        history_before + 2)
    check("default untouched",  load_defaults()[sname].xywh(),
          defs[sname].xywh())
    check("role carried over",  load_active()[sname].role, ROLE_SAMPLE)
    check("frame carried over", db.read_val(
        "SELECT frame_w FROM roi WHERE name = ? AND active=1", (sname,)), 2304)
    if other:
        check("other ROIs untouched", load_active()[other].xywh(), other_before)
    check("unknown name is refused", save("nosuch", Rect(0, 0, 8, 8)), 0)

    print("== restore ==")
    check("restore returns a rowid", restore(sname) > 0, True)
    check("back to the default",  load_active()[sname].xywh(),
          defs[sname].xywh())
    check("still one active",     db.read_val(
        "SELECT COUNT(*) FROM roi WHERE name = ? AND active=1", (sname,)), 1)
    check("default row not consumed", db.read_val(
        "SELECT COUNT(*) FROM roi WHERE name = ? AND isdefault=1", (sname,)), 1)
    check("unknown name is refused",  restore("nosuch"), 0)

    print("== restore_role: a whole role at once ==")
    # What the boot check calls, because a role may be filled by more than one
    # region and restoring only the first would leave the rest missing.
    db.write_one("UPDATE roi SET active = 0 WHERE role = ? AND active = 1",
                 (ROLE_WHITE_BALANCE,))
    check("the role is gone",
          missing_roles(load_active(), (ROLE_WHITE_BALANCE,)),
          [ROLE_WHITE_BALANCE])
    back = restore_role(ROLE_WHITE_BALANCE)
    check("names every row it restored", [n for n, _ in back],
          sorted(n for n, r in load_defaults().items()
                 if r.role == ROLE_WHITE_BALANCE))
    check("and the role is filled again",
          missing_roles(load_active(), (ROLE_WHITE_BALANCE,)), [])
    check("a role with no default is refused", restore_role("no_such_role"), [])

    print("== empty table degrades quietly ==")
    # Repairing a missing row is db_bootstrap's job now (it re-runs
    # provisioning/config_defaults.sql at every launch) — roi.py itself just
    # has to report the gap rather than paper over it.
    db.write_one("DELETE FROM roi")
    check("no active ROIs", load_active(), {})
    check("no roles filled", missing_roles(load_active(), required),
          list(required))
    check("save on empty",  save(sname, Rect(0, 0, 8, 8)), 0)

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("\n== all roi self-tests passed ==")
