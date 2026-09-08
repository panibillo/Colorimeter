"""
ecalc_rects.py — Rectangle sizing and symmetric slice generation for Bayer layer analysis.

Produces two dicts of (row_slice, col_slice) pairs keyed by quadrant label:

  Key  Group 0 — rect 0 (same pixels, four image orientations)
  ---- ----------------------------------------------------------
  'lr' original    — apply slice to layer as-is
  'ul' rot180      — apply slice to layer[::-1, ::-1]
  'ur' vflip       — apply slice to layer[:, ::-1]   (flip on vert axis)
  'll' hflip       — apply slice to layer[::-1, :]   (flip on horiz axis)

  Key  Group 1 — rects 1–4 (in-place orientation variants, common size)
  ---- ----------------------------------------------------------
  'lr' rect1       — normal row/col order
  'ul' rect2       — step -1 in both axes  → 180° rotation
  'ur' rect3       — step -1 in rows only  → up/down flip
  'll' rect4       — step -1 in cols only  → left/right flip

  An optional buffer (pixels) expands (buffer > 0) or shrinks (buffer < 0)
  every crop symmetrically.  The value is clamped so no slice exceeds the
  image boundary or collapses to zero size.
"""

from __future__ import annotations

# Type aliases
Rect = tuple[int, int, int, int]       # (x1, y1, x2, y2) in layer pixel coords
SlicePair = tuple[slice, slice]        # (row_slice, col_slice)
SliceDict = dict[str, SlicePair]

# BGGR layer dimensions for this camera configuration
LAYER_W = 820
LAYER_H = 616

# Human-readable descriptions for print output
_G0_DESC = {'lr': 'original', 'ul': 'rot180', 'ur': 'vflip', 'll': 'hflip'}
_G1_DESC = {'lr': 'rect1',    'ul': 'rect2',  'ur': 'rect3', 'll': 'rect4'}


# ---------------------------------------------------------------------------
# Slice primitives
# ---------------------------------------------------------------------------

def _fwd(start: int, count: int) -> slice:
    """Forward slice: indices start, start+1, …, start+count-1."""
    return slice(start, start + count)


def _rev(start: int, count: int) -> slice:
    """Reversed slice: indices start+count-1, …, start  (step -1).

    Uses None as stop when start == 0 to avoid the ambiguity of stop=-1
    being re-interpreted as the last index by Python's slice machinery.
    """
    stop = start - 1
    return slice(start + count - 1, stop if stop >= 0 else None, -1)


def _fmt(s: slice) -> str:
    """Human-readable slice in [start:stop:step] notation."""
    step = f":{s.step}" if s.step is not None else ""
    return f"[{s.start}:{s.stop}{step}]"


# ---------------------------------------------------------------------------
# Buffer clamping
# ---------------------------------------------------------------------------

def _safe_buffer(
    buffer: int,
    x1: int, y1: int, dx0: int, dy0: int,
    r0s: list[int], c0s: list[int],
    min_dx: int, min_dy: int,
    H: int, W: int,
) -> int:
    """Clamp buffer so all crops stay within the image and have size >= 1.

    Upper bound: no buffered edge may cross the image boundary.
    Lower bound: no buffered dimension may drop below 1 pixel.
    Prints a warning if clamping is applied.
    """
    x2, y2 = x1 + dx0, y1 + dy0

    # Maximum expansion before any edge hits the image boundary
    hi = min(x1, W - x2, y1, H - y2)                              # group 0
    for r0, c0 in zip(r0s, c0s):
        hi = min(hi, r0, c0, H - r0 - min_dy, W - c0 - min_dx)   # group 1

    # Maximum shrink before any crop reaches zero pixels in either dimension
    min_dim = min(dx0, dy0, min_dx, min_dy)
    lo = -(min_dim - 1) // 2

    effective = max(lo, min(buffer, hi))
    if effective != buffer:
        print(f"  Warning: buffer={buffer} clamped to {effective} "
              f"(valid range [{lo}, {hi}])")
    return effective


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def size_and_group_rects(
    rects_layer: list[Rect],
    buffer: int = 0,
) -> tuple[SliceDict, SliceDict]:
    """
    Compute symmetric slice groups from a list of layer-coordinate rects.

    rects_layer[0]   → Group 0 (4 image-orientation variants, same pixel set)
    rects_layer[1:5] → Group 1 (4 in-place orientation variants, common size)

    buffer: pixels to add (>0) or remove (<0) from each side of every crop.
    Clamped automatically to keep all slices within image bounds and >= 1 px.

    Prints both groups before returning.
    Returns (group0, group1) as SliceDicts keyed by 'lr', 'ul', 'ur', 'll'.
    """
    H, W = LAYER_H, LAYER_W

    # -------------------------------------------------------------------------
    # Group 0 base geometry: rect 0
    # -------------------------------------------------------------------------
    x1, y1, x2, y2 = rects_layer[0]
    dx0, dy0 = x2 - x1, y2 - y1

    # -------------------------------------------------------------------------
    # Group 1 base geometry: rects 1–4, minimum common size, centered origins
    # -------------------------------------------------------------------------
    sub = rects_layer[1:5]
    min_dx = min(r[2] - r[0] for r in sub)
    min_dy = min(r[3] - r[1] for r in sub)

    origins: list[tuple[int, int]] = []
    for rx1, ry1, rx2, ry2 in sub:
        c0 = rx1 + (rx2 - rx1 - min_dx) // 2
        r0 = ry1 + (ry2 - ry1 - min_dy) // 2
        origins.append((c0, r0))

    c0s = [o[0] for o in origins]
    r0s = [o[1] for o in origins]

    # -------------------------------------------------------------------------
    # Apply buffer (with boundary clamping)
    # -------------------------------------------------------------------------
    buf = _safe_buffer(buffer, x1, y1, dx0, dy0, r0s, c0s, min_dx, min_dy, H, W)

    # Buffered group 0 corners
    by1, by2 = y1 - buf, y2 + buf
    bx1, bx2 = x1 - buf, x2 + buf
    bdy,  bdx = by2 - by1, bx2 - bx1

    # Buffered group 1 sizes and origins
    b_min_dy = min_dy + 2 * buf
    b_min_dx = min_dx + 2 * buf
    br0s = [r0 - buf for r0 in r0s]
    bc0s = [c0 - buf for c0 in c0s]

    # -------------------------------------------------------------------------
    # Group 0: same pixels, four image orientations
    #
    # For buffered rect (bx1,by1)–(bx2,by2) on H×W image:
    #   rot180 from arr[::-1,::-1]  → row [H-by2 : H-by1], col [W-bx2 : W-bx1]
    #   vflip  from arr[:, ::-1]    → row [by1   : by2  ], col [W-bx2 : W-bx1]
    #   hflip  from arr[::-1, :]    → row [H-by2 : H-by1], col [bx1   : bx2  ]
    # -------------------------------------------------------------------------
    group0: SliceDict = {
        'lr': (_fwd(by1,     bdy), _fwd(bx1,     bdx)),   # original
        'ul': (_fwd(H - by2, bdy), _fwd(W - bx2, bdx)),   # rot180
        'ur': (_fwd(by1,     bdy), _fwd(W - bx2, bdx)),   # vflip
        'll': (_fwd(H - by2, bdy), _fwd(bx1,     bdx)),   # hflip
    }

    # -------------------------------------------------------------------------
    # Group 1: in-place orientation variants, common buffered size
    # -------------------------------------------------------------------------
    group1: SliceDict = {
        'lr': (_fwd(br0s[0], b_min_dy), _fwd(bc0s[0], b_min_dx)),  # rect1 normal
        'ul': (_rev(br0s[1], b_min_dy), _rev(bc0s[1], b_min_dx)),  # rect2 rot180
        'ur': (_rev(br0s[2], b_min_dy), _fwd(bc0s[2], b_min_dx)),  # rect3 hflip
        'll': (_fwd(br0s[3], b_min_dy), _rev(bc0s[3], b_min_dx)),  # rect4 vflip
    }

    _print_slices(group0, group1, bdx, bdy, b_min_dx, b_min_dy, bc0s, br0s, buf)
    return group0, group1


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _print_slices(
    group0: SliceDict,
    group1: SliceDict,
    bdx: int, bdy: int,
    b_min_dx: int, b_min_dy: int,
    bc0s: list[int], br0s: list[int],
    buf: int,
) -> None:
    sep = "=" * 68
    buf_tag = f"  (buffer={buf})" if buf != 0 else ""
    print(f"\n{sep}")

    print(f"GROUP 0 — rect 0 symmetry slices  ({bdx} × {bdy} px each){buf_tag}")
    print(f"  Image size: {LAYER_W} × {LAYER_H}  |  all four slices capture identical pixels")
    for key, (rs, cs) in group0.items():
        desc = _G0_DESC[key]
        print(f"  {key} ({desc:8s})  row={_fmt(rs)}  col={_fmt(cs)}")

    print()
    print(f"GROUP 1 — rects 1–4 in-place transforms  ({b_min_dx} × {b_min_dy} px each){buf_tag}")
    print(f"  All slices applied to original layer  |  orientation via step direction")
    for i, (key, (rs, cs)) in enumerate(group1.items()):
        desc = _G1_DESC[key]
        print(f"  {key} ({desc:5s})  r0={br0s[i]:4d} c0={bc0s[i]:4d}  row={_fmt(rs)}  col={_fmt(cs)}")

    print(sep + "\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    rects_layer = [
        (405, 265, 491, 355),  # rect 0  (86 × 90 px)
        (622, 376, 711, 465),  # rect 1  (89 × 89 px)
        (176, 144, 265, 223),  # rect 2  (89 × 79 px)
        (627, 147, 716, 230),  # rect 3  (89 × 83 px)
        (174, 380, 266, 461),  # rect 4  (92 × 81 px)
    ]

    group0, group1 = size_and_group_rects(rects_layer, buffer=0)
