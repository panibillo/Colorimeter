#!/usr/bin/env python3
"""
find_rects.py — Discover a 5×7 grid of colored patches in a test image.

Locates grid lines via Sobel edge projection, refines each cell boundary
inward past edge-blending noise, applies an optional pixel buffer, and
reports each patch as a Rect named by its 1-based (row, col) position.

Usage:
    python dev/find_rects.py -i image_name.jpg
    python dev/find_rects.py -i image_name.jpg -b 5
"""

import sys
import argparse
from itertools import combinations
import numpy as np
import cv2
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from rect import Rect

NROWS = 5
NCOLS = 7
IMG_DIR = Path(__file__).parent.parent / "images"

# Pixels to expand each grid cell beyond its nominal boundary before running
# refine_patch.  Allows the refined boundary to move outward past the grid
# line (needed when the patch grid is slightly rotated relative to the image).
EXPAND = 20


# ---------------------------------------------------------------------------
# Grid detection
# ---------------------------------------------------------------------------

def _find_dividers(projection: np.ndarray, n_dividers: int) -> list[int]:
    """
    Find n_dividers peak positions in a 1D projection.

    Collects ~2× as many candidate peaks as needed using non-max suppression
    with a tighter window, then selects the n_dividers candidates whose
    spacings — together with the image edges (0 and len(projection)) as outer
    bounds — have the lowest standard deviation.

    A real colour-patch grid has approximately equal cell widths, so
    minimising spacing variance selects the correct dividers even when one
    boundary has low edge contrast and its peak is weaker than a noise peak
    in another region.

    Args:
        projection: 1D float array (e.g. mean edge magnitude along one axis).
        n_dividers: Number of dividers to return (= n_cells - 1).

    Returns:
        Sorted list of n_dividers pixel positions.
    """
    n = len(projection)
    n_candidates = max(n_dividers * 2, n_dividers + 4)
    # Tighter suppression window so weak-but-real peaks are not masked by
    # strong neighbours.
    min_gap = max(1, n // (n_candidates + 2))

    proj = projection.astype(float).copy()
    candidates: list[int] = []
    for _ in range(n_candidates):
        idx = int(np.argmax(proj))
        if proj[idx] == 0.0:
            break
        candidates.append(idx)
        lo = max(0, idx - min_gap // 2)
        hi = min(n, idx + min_gap // 2 + 1)
        proj[lo:hi] = 0.0

    candidates = sorted(candidates)

    if len(candidates) <= n_dividers:
        # Not enough candidates found; return what we have.
        return candidates

    # Select the n_dividers candidates whose spacings (with 0 and n as outer
    # bounds) are most equal, i.e. minimise std of gap lengths.
    best_combo: tuple[int, ...] = tuple(candidates[:n_dividers])
    best_score = float("inf")
    for combo in combinations(candidates, n_dividers):
        gaps = np.diff([0] + list(combo) + [n]).astype(float)
        score = float(gaps.std())
        if score < best_score:
            best_score = score
            best_combo = combo
    return sorted(best_combo)


def compute_grid(img: np.ndarray) -> tuple[list[int], list[int]]:
    """
    Find row and column boundary positions for the patch grid.

    Converts to grayscale, computes Sobel edges, projects onto each axis
    to find peak-energy positions (grid lines), and returns boundary lists
    that include the image edges (0 and H or W) as the outer bounds.

    The outer 20 pixels of the edge image are suppressed to avoid image-
    border artefacts being mistaken for grid lines.

    Args:
        img: BGR image as a numpy array, shape (H, W, 3).

    Returns:
        Tuple (row_bounds, col_bounds) where each is a sorted list of
        length NROWS+1 or NCOLS+1 respectively.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    sx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    sy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    edges = np.abs(sx) + np.abs(sy)

    # Suppress image border so outer edges don't count as grid lines.
    margin = 20
    edges[:margin, :]  = 0.0
    edges[-margin:, :] = 0.0
    edges[:, :margin]  = 0.0
    edges[:, -margin:] = 0.0

    # Project: mean along columns → sensitive to horizontal (row) boundaries;
    #          mean along rows    → sensitive to vertical (column) boundaries.
    row_proj = edges.mean(axis=1)
    col_proj = edges.mean(axis=0)

    h_divs = _find_dividers(row_proj, NROWS - 1)
    v_divs = _find_dividers(col_proj, NCOLS - 1)

    H, W = img.shape[:2]
    row_bounds = [0] + h_divs + [H]
    col_bounds  = [0] + v_divs + [W]
    return row_bounds, col_bounds


# ---------------------------------------------------------------------------
# Boundary refinement
# ---------------------------------------------------------------------------

def _trim_one_edge(
    patch: np.ndarray,
    axis: int,
    from_end: bool,
    interior: np.ndarray,
    color_thresh: float = 30.0,
    max_trim: int = 60,
) -> int:
    """
    Scan inward from one edge and return the number of noisy pixels to drop.

    Compares the mean BGR colour of each successive 1-pixel-wide strip to
    the interior median colour.  Returns the index of the first strip that
    is within color_thresh on every channel (i.e. the first "clean" strip).
    Everything before it is considered boundary noise.

    Args:
        patch:       HxWx3 BGR image of the rough cell region.
        axis:        0 to scan rows (top/bottom edge); 1 for cols (left/right).
        from_end:    True = scan inward from the far edge (bottom / right).
        interior:    3-element float array, median BGR of the clean interior.
        color_thresh: Euclidean distance threshold in BGR colour space.
                      A strip is accepted when its mean colour is within
                      this distance of the interior reference (0–441,
                      where 441 = √(255²×3) is the maximum possible).
        max_trim:    Maximum pixels to trim from this edge.

    Returns:
        Number of pixels to trim (≥ 0).  0 means no noise detected.
    """
    n = patch.shape[0] if axis == 0 else patch.shape[1]
    limit = min(max_trim, n // 4)
    for i in range(limit):
        idx = (n - 1 - i) if from_end else i
        strip = patch[idx, :] if axis == 0 else patch[:, idx]
        mean_color = strip.astype(float).mean(axis=0)
        if float(np.linalg.norm(mean_color - interior)) < color_thresh:
            return i
    return limit


def refine_patch(
    patch: np.ndarray,
    n_sigma: float = 3.0,
    min_thresh: float = 15.0,
    max_trim: int = 60,
) -> tuple[int, int, int, int]:
    """
    Compute per-edge trim amounts to remove boundary blending noise.

    Each edge gets its own interior reference colour and an adaptive
    acceptance threshold derived from that same reference strip.  The
    threshold is n_sigma × (L2 norm of per-channel std), floored at
    min_thresh.  This automatically scales to the colour variability of
    each individual patch: a noisy or gradient patch tolerates more
    deviation before declaring a strip "outside", while a flat uniform
    patch demands a much tighter match.

    Reference strip geometry (REF_THICK × (edge_length − 2×REF_LAT)):
        top    reference: rows  [H//4 : H//4+THICK],  cols [LAT : W-LAT]
        bottom reference: rows  [3H//4-THICK : 3H//4], cols [LAT : W-LAT]
        left   reference: cols  [W//4 : W//4+THICK],  rows [LAT : H-LAT]
        right  reference: cols  [3W//4-THICK : 3W//4], rows [LAT : H-LAT]

    Args:
        patch:       HxWx3 BGR image of the (possibly expanded) cell region.
        n_sigma:     Threshold multiplier applied to the reference std
                     (default 3.0).  Increase to accept more boundary
                     variation; decrease for tighter fits.
        min_thresh:  Floor on the adaptive threshold (default 15.0).
                     Prevents near-zero thresholds on extremely uniform
                     reference patches from making the scan over-sensitive.
        max_trim:    Maximum pixels trimmed per edge.

    Returns:
        Tuple (top, bottom, left, right) — pixels to drop from each edge.
    """
    H, W = patch.shape[:2]
    if H < 32 or W < 32:
        return 0, 0, 0, 0

    REF_THICK = 10   # reference strip thickness, perpendicular to the edge
    REF_LAT   = 5    # lateral trim at each end, parallel to the edge

    def _ref_h(row_start: int) -> tuple[np.ndarray, float]:
        """Median BGR and adaptive threshold for a horizontal reference strip."""
        r1 = max(0, row_start)
        r2 = min(H, row_start + REF_THICK)
        c1, c2 = REF_LAT, W - REF_LAT
        pixels = patch[r1:r2, c1:c2].reshape(-1, 3).astype(float)
        interior = np.median(pixels, axis=0)
        thresh = max(min_thresh,
                     n_sigma * float(np.linalg.norm(pixels.std(axis=0))))
        return interior, thresh

    def _ref_v(col_start: int) -> tuple[np.ndarray, float]:
        """Median BGR and adaptive threshold for a vertical reference strip."""
        c1 = max(0, col_start)
        c2 = min(W, col_start + REF_THICK)
        r1, r2 = REF_LAT, H - REF_LAT
        pixels = patch[r1:r2, c1:c2].reshape(-1, 3).astype(float)
        interior = np.median(pixels, axis=0)
        thresh = max(min_thresh,
                     n_sigma * float(np.linalg.norm(pixels.std(axis=0))))
        return interior, thresh

    inset_h = H // 4
    inset_w = W // 4

    int_top, thr_top = _ref_h(inset_h)
    int_bot, thr_bot = _ref_h(H - inset_h - REF_THICK)
    int_lft, thr_lft = _ref_v(inset_w)
    int_rgt, thr_rgt = _ref_v(W - inset_w - REF_THICK)

    top    = _trim_one_edge(patch, axis=0, from_end=False,
                            interior=int_top, color_thresh=thr_top,
                            max_trim=max_trim)
    bottom = _trim_one_edge(patch, axis=0, from_end=True,
                            interior=int_bot, color_thresh=thr_bot,
                            max_trim=max_trim)
    left   = _trim_one_edge(patch, axis=1, from_end=False,
                            interior=int_lft, color_thresh=thr_lft,
                            max_trim=max_trim)
    right  = _trim_one_edge(patch, axis=1, from_end=True,
                            interior=int_rgt, color_thresh=thr_rgt,
                            max_trim=max_trim)
    return top, bottom, left, right


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rects_overlap(a: "Rect", b: "Rect") -> bool:
    """Return True if two axis-aligned rects share any pixel area."""
    return (a.x < b.x2() and a.x2() > b.x and
            a.y < b.y2() and a.y2() > b.y)


def _check_overlaps(rects: list["Rect"]) -> int:
    """
    Check all rect pairs for overlap and print any that intersect.

    Args:
        rects: List of Rect instances to check.

    Returns:
        Number of overlapping pairs found.
    """
    count = 0
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            if _rects_overlap(rects[i], rects[j]):
                a, b = rects[i], rects[j]
                ox = min(a.x2(), b.x2()) - max(a.x, b.x)
                oy = min(a.y2(), b.y2()) - max(a.y, b.y)
                print(f"  OVERLAP: {a.name} ∩ {b.name}  "
                      f"({ox}×{oy} px)")
                count += 1
    return count


def _bgr_to_hex(bgr_pixels: np.ndarray) -> str:
    """
    Compute the mean BGR colour of a pixel array and return '#RRGGBB'.

    Args:
        bgr_pixels: Array of shape (..., 3) in BGR order.

    Returns:
        Hex colour string, e.g. '#FF8800'.
    """
    mean = bgr_pixels.reshape(-1, 3).mean(axis=0)
    b, g, r = (int(round(float(v))) for v in mean)
    return f'#{r:02X}{g:02X}{b:02X}'


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '-i', '--image_name', required=True,
        help='Image filename inside ../images/',
    )
    parser.add_argument(
        '-b', '--buffer_pixels', type=int, default=0,
        help='Extra inward buffer (pixels) applied after noise trimming',
    )
    parser.add_argument(
        '-s', '--sigma', type=float, default=3.0,
        help='Adaptive threshold multiplier: threshold = sigma × std of '
             'reference strip (default 3.0)',
    )
    args = parser.parse_args()

    img_path = IMG_DIR / args.image_name
    img = cv2.imread(str(img_path))
    if img is None:
        sys.exit(f"Error: cannot read {img_path}")

    buf     = args.buffer_pixels
    n_sigma = args.sigma
    row_bounds, col_bounds = compute_grid(img)

    H_img, W_img = img.shape[:2]
    rects: list[Rect] = []
    for ri in range(NROWS):
        for ci in range(NCOLS):
            y1 = row_bounds[ri];     y2 = row_bounds[ri + 1]
            x1 = col_bounds[ci];     x2 = col_bounds[ci + 1]

            # Expand the analysis window beyond the grid cell boundary so
            # that refine_patch can place the refined edge on either side of
            # the grid line (needed when the card is slightly rotated).
            ey1 = max(0,     y1 - EXPAND)
            ey2 = min(H_img, y2 + EXPAND)
            ex1 = max(0,     x1 - EXPAND)
            ex2 = min(W_img, x2 + EXPAND)

            patch = img[ey1:ey2, ex1:ex2]
            top, bot, left, right = refine_patch(patch, n_sigma=n_sigma)

            # Apply trim + buffer relative to the expanded window origin.
            rx = ex1 + left  + buf
            ry = ey1 + top   + buf
            rw = (ex2 - right - buf) - rx
            rh = (ey2 - bot   - buf) - ry

            if rw <= 0 or rh <= 0:
                continue

            color = _bgr_to_hex(img[ry:ry + rh, rx:rx + rw])
            # Name encodes 1-based row and col as zero-padded 2-digit pairs.
            name  = f"{ri + 1:d}{ci + 1:d}"
            rects.append(Rect(rx, ry, rw, rh, name=name, color=color))

    # Check for overlapping rects (expected to occur at shared boundaries
    # when adjacent patches are similar in colour and both expand outward).
    print(f"\nOverlap check ({len(rects)} rects):")
    n_overlaps = _check_overlaps(rects)
    if n_overlaps == 0:
        print("  No overlaps.")
    else:
        print(f"  {n_overlaps} overlapping pair(s) found.")

    # ------------------------------------------------------------------
    # Plot
    # ------------------------------------------------------------------
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    fig, ax = plt.subplots(figsize=(13, 10))
    ax.imshow(rgb)
    ax.set_title(
        f"find_rects — {args.image_name}  ({len(rects)} patches found)",
        fontsize=11,
    )

    for r in rects:
        border = mpatches.Rectangle(
            (r.x, r.y), r.w, r.h,
            linewidth=1.5, edgecolor='white', facecolor='none',
        )
        ax.add_patch(border)
        ax.text(
            r.x + r.w / 2, r.y + r.h / 2, r.name,
            ha='center', va='center', fontsize=8, color='white',
            bbox=dict(facecolor='black', alpha=0.5, pad=2, edgecolor='none'),
        )

    ax.axis('off')
    plt.tight_layout()

    stem     = Path(args.image_name).stem
    out_path = IMG_DIR / f"{stem}_found.png"
    plt.savefig(str(out_path), dpi=100, bbox_inches='tight')
    print(f"Plot saved to {out_path}")
    try:
        plt.show()
    except Exception:
        pass

    # ------------------------------------------------------------------
    # Print Rect strings
    # ------------------------------------------------------------------
    print()
    for r in rects:
        print(r)


if __name__ == '__main__':
    main()
