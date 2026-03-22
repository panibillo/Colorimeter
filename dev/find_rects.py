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
        color_thresh: Per-channel tolerance for "matches interior" (0–255).
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
        if np.abs(mean_color - interior).max() < color_thresh:
            return i
    return limit


def refine_patch(
    patch: np.ndarray,
    color_thresh: float = 30.0,
    max_trim: int = 60,
) -> tuple[int, int, int, int]:
    """
    Compute per-edge trim amounts to remove boundary blending noise.

    Derives a reference interior colour from the central 50% of the patch,
    then trims each edge until the outermost strip matches that interior
    within color_thresh on all BGR channels.

    Args:
        patch:       HxWx3 BGR image of the rough cell region.
        color_thresh: Per-channel tolerance (0–255) for "clean" detection.
        max_trim:    Maximum pixels trimmed per edge.

    Returns:
        Tuple (top, bottom, left, right) — pixels to drop from each edge.
    """
    H, W = patch.shape[:2]
    if H < 16 or W < 16:
        return 0, 0, 0, 0

    # Median of central quarter as a noise-robust interior reference.
    cy1, cy2 = H // 4, 3 * H // 4
    cx1, cx2 = W // 4, 3 * W // 4
    interior = np.median(
        patch[cy1:cy2, cx1:cx2].reshape(-1, 3).astype(float), axis=0
    )

    kw = dict(interior=interior, color_thresh=color_thresh, max_trim=max_trim)
    top    = _trim_one_edge(patch, axis=0, from_end=False, **kw)
    bottom = _trim_one_edge(patch, axis=0, from_end=True,  **kw)
    left   = _trim_one_edge(patch, axis=1, from_end=False, **kw)
    right  = _trim_one_edge(patch, axis=1, from_end=True,  **kw)
    return top, bottom, left, right


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    args = parser.parse_args()

    img_path = IMG_DIR / args.image_name
    img = cv2.imread(str(img_path))
    if img is None:
        sys.exit(f"Error: cannot read {img_path}")

    buf = args.buffer_pixels
    row_bounds, col_bounds = compute_grid(img)

    rects: list[Rect] = []
    for ri in range(NROWS):
        for ci in range(NCOLS):
            y1 = row_bounds[ri];     y2 = row_bounds[ri + 1]
            x1 = col_bounds[ci];     x2 = col_bounds[ci + 1]

            patch = img[y1:y2, x1:x2]
            top, bot, left, right = refine_patch(patch)

            # Apply trim + buffer to all four sides.
            rx = x1 + left  + buf
            ry = y1 + top   + buf
            rw = (x2 - right - buf) - rx
            rh = (y2 - bot   - buf) - ry

            if rw <= 0 or rh <= 0:
                continue

            color = _bgr_to_hex(img[ry:ry + rh, rx:rx + rw])
            # Name encodes 1-based row and col as zero-padded 2-digit pairs.
            name  = f"{ri + 1:02d}{ci + 1:02d}"
            rects.append(Rect(rx, ry, rw, rh, name=name, color=color))

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
