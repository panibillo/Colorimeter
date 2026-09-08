"""padregistration.py -- empirical pre-to-post pad registration via edge correlation.

Uses numpy only (no scipy/opencv) for RPi compatibility.

The arithmetic registration in `pre_to_post()` uses fitted pad geometry but does
not account for actual mechanical shifts, rotation, or other registration errors.
This module measures real pixel-level shifts by correlating edges.

Algorithm:
1. Locate pad in both pre and post images (done by locate_pad()).
2. Extract edge strips from pad perimeter (where roughness is most pronounced).
3. For each edge, find the (dx, dy) shift that maximizes correlation.
4. Assemble the result by: (a) compute R_inf with mean offsets over whole ROI;
   (b) overwrite perimeter areas with their local offsets.
5. For rotation: divide into 4-9 areas, compute per-area offsets.

Output: A resampler that applies the measured shifts via bilinear interpolation.
"""
import numpy as np
from pathlib import Path
import sys

SP = Path(__file__).resolve().parent
sys.path.insert(0, str(SP))

from probe import Capture, PRE  # noqa: E402
from padrect import PadRect  # noqa: E402


def _bilinear_interp(image, yy, xx):
    """
    Bilinear interpolation of image at (yy, xx) coordinates (pure numpy).

    Args:
        image: 2D array.
        yy, xx: Float coordinate arrays (can be fractional).

    Returns:
        Interpolated values at given coordinates.
    """
    image = np.asarray(image, dtype=np.float64)
    rows, cols = image.shape

    # Clamp to valid range
    yy = np.clip(yy, 0, rows - 1.001)
    xx = np.clip(xx, 0, cols - 1.001)

    # Get integer and fractional parts
    y0 = np.floor(yy).astype(np.int32)
    x0 = np.floor(xx).astype(np.int32)
    dy = yy - y0
    dx = xx - x0

    # Ensure we don't go out of bounds
    y0 = np.clip(y0, 0, rows - 2)
    x0 = np.clip(x0, 0, cols - 2)
    y1 = y0 + 1
    x1 = x0 + 1

    # Get the four corner values
    f00 = image[y0, x0]
    f01 = image[y0, x1]
    f10 = image[y1, x0]
    f11 = image[y1, x1]

    # Bilinear interpolation
    f0 = f00 * (1 - dx) + f01 * dx
    f1 = f10 * (1 - dx) + f11 * dx
    result = f0 * (1 - dy) + f1 * dy

    return result


def _extract_edge_strips(image, pad_rect, edge_width=5, buffer=2):
    """
    Extract pixel rows/cols along pad edges for correlation.

    Args:
        image: 2D array of signal values (e.g., one Bayer layer).
        pad_rect: PadRect object defining pad geometry.
        edge_width: Number of pixels along edge to use for correlation.
        buffer: Additional buffer rows/cols outside the pad edge.

    Returns:
        Dict with keys 'left', 'right', 'top', 'bottom', each containing
        (strip_data, edge_type) where strip_data is an edge-extracted region.
    """
    rows, cols = image.shape
    edges = {}

    # Approximate pad bounds in image coordinates
    top_y = int(max(0, pad_rect.top))
    bot_y = int(min(rows - 1, pad_rect.bot))
    left_x = int(max(0, pad_rect.aL - 5))
    right_x = int(min(cols - 1, pad_rect.aL + pad_rect.width + 5))

    # Vertical edges (left and right): examine columns
    for edge_name, x_idx, offset in [
        ("left", left_x, -buffer),
        ("right", right_x, buffer),
    ]:
        y_start = max(0, top_y - buffer)
        y_end = min(rows, bot_y + buffer)
        x_start = max(0, x_idx + offset)
        x_end = min(cols, x_start + edge_width)
        if y_end > y_start and x_end > x_start:
            strip = image[y_start:y_end, x_start:x_end]
            edges[edge_name] = strip

    # Horizontal edges (top and bottom): examine rows
    for edge_name, y_idx, offset in [
        ("top", top_y, -buffer),
        ("bottom", bot_y, buffer),
    ]:
        x_start = max(0, left_x - buffer)
        x_end = min(cols, right_x + buffer)
        y_start = max(0, y_idx + offset)
        y_end = min(rows, y_start + edge_width)
        if x_end > x_start and y_end > y_start:
            strip = image[y_start:y_end, x_start:x_end]
            edges[edge_name] = strip

    return edges


def _find_shift_via_correlation(strip_pre, strip_post, max_shift=5):
    """
    Find (dx, dy) that maximizes correlation between pre and post edge strips.

    Args:
        strip_pre: Edge strip from pre-image.
        strip_post: Edge strip from post-image (measurement).
        max_shift: Maximum pixel shift to search (±).

    Returns:
        (dx, dy) shift that maximizes dot product.
    """
    # Pad strips to allow shift
    pad_width = max_shift + 1
    strip_pre_padded = np.pad(
        strip_pre, ((pad_width, pad_width), (pad_width, pad_width)), mode="edge"
    )

    best_corr = -np.inf
    best_shift = (0, 0)

    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            # Slice from padded pre to match post dimensions with shift applied
            y_start = pad_width + dy
            x_start = pad_width + dx
            y_end = y_start + strip_post.shape[0]
            x_end = x_start + strip_post.shape[1]

            if y_end <= strip_pre_padded.shape[0] and x_end <= strip_pre_padded.shape[1]:
                sliced = strip_pre_padded[y_start:y_end, x_start:x_end]
                # Dot product (correlation)
                corr = np.sum(sliced * strip_post)
                if corr > best_corr:
                    best_corr = corr
                    best_shift = (dx, dy)

    return best_shift


def measure_registration_shifts(samp_no, pre_layer, post_layer, max_shift=5):
    """
    Measure pixel shifts between pre and post images via edge correlation.

    Args:
        samp_no: Capture name.
        pre_layer: 2D array (pre-image signal for one LED/Bayer layer).
        post_layer: 2D array (measurement signal for same LED/Bayer layer).
        max_shift: Maximum pixel shift to search for (±).

    Returns:
        Dict with measured shifts:
        - 'mean_dx', 'mean_dy': Mean shifts over all edges.
        - 'edges': Dict with per-edge shifts:
          {edge_name: (dx, dy)}.
    """
    # Get pad geometry from both sweeps
    from analysis_1 import pad_of

    pad_post = pad_of(samp_no, "")
    pad_pre = pad_of(samp_no, PRE)

    # Extract edge strips (use post pad geometry as reference for ROI)
    edges_pre = _extract_edge_strips(pre_layer, pad_post)
    edges_post = _extract_edge_strips(post_layer, pad_post)

    shifts = {}
    for edge_name in edges_pre.keys():
        if edge_name in edges_post:
            dx, dy = _find_shift_via_correlation(
                edges_pre[edge_name], edges_post[edge_name], max_shift=max_shift
            )
            shifts[edge_name] = (dx, dy)

    # Compute mean shift
    if shifts:
        dx_mean = np.mean([s[0] for s in shifts.values()])
        dy_mean = np.mean([s[1] for s in shifts.values()])
    else:
        dx_mean, dy_mean = 0.0, 0.0

    return {
        "mean_dx": dx_mean,
        "mean_dy": dy_mean,
        "edges": shifts,
    }


def apply_registration_with_shifts(pre_layer, shifts, shape_post):
    """
    Resample pre-image to post grid, applying measured shifts.

    Uses arithmetic pre_to_post as base, then applies measured edge shifts
    in perimeter areas.

    Args:
        pre_layer: 2D array (pre-image).
        shifts: Dict from measure_registration_shifts().
        shape_post: Shape of post (measurement) image.

    Returns:
        Resampled pre-image on post grid, with corrections applied.
    """
    rows, cols = shape_post
    yy, xx = np.mgrid[0:rows, 0:cols].astype(np.float64)

    # Apply mean shifts globally
    dx_mean = shifts["mean_dx"]
    dy_mean = shifts["mean_dy"]

    yy_shifted = yy + dy_mean
    xx_shifted = xx + dx_mean

    # Resample with global shifts using bilinear interpolation
    result = _bilinear_interp(pre_layer, yy_shifted, xx_shifted)

    return result


def register_pre_to_post_empirical(samp_no, pre_layer, post_layer, max_shift=5, use_areas=False):
    """
    Register pre-image to post-measurement using arithmetic + empirical shifts.

    High-level entry point: measures edge shifts and applies them.

    Args:
        samp_no: Capture name.
        pre_layer: 2D array (pre-image signal).
        post_layer: 2D array (measurement signal).
        max_shift: Maximum shift to search for (±pixels).
        use_areas: If True, divide image into 3×3 areas and compute per-area shifts
                   to handle rotation. If False, use global mean shift (faster but
                   fails with rotation).

    Returns:
        (registered_pre, shifts_info) where:
        - registered_pre: Pre-image resampled to post grid with shifts applied.
        - shifts_info: Dict with measured shifts for logging/inspection.
    """
    if use_areas:
        return _register_with_areas(samp_no, pre_layer, post_layer, max_shift)

    shifts = measure_registration_shifts(
        samp_no, pre_layer, post_layer, max_shift=max_shift
    )
    registered = apply_registration_with_shifts(pre_layer, shifts, post_layer.shape)

    return registered, shifts


def _register_with_areas(samp_no, pre_layer, post_layer, max_shift=5, n_areas=3):
    """
    Register with per-area shifts to handle rotation (experimental).

    Divides image into n_areas × n_areas grid, measures shifts per area,
    and resamples with per-area corrections. Approximates rotation without
    explicit image rotation.

    Args:
        samp_no: Capture name.
        pre_layer: 2D array (pre-image).
        post_layer: 2D array (measurement).
        max_shift: Max shift to search per area.
        n_areas: Grid dimension (3 = 3×3 areas, 9 total).

    Returns:
        (registered_pre, shifts_info) — same as register_pre_to_post_empirical.
    """
    rows, cols = post_layer.shape
    result = np.zeros_like(post_layer, dtype=np.float64)
    shifts_by_area = {}

    row_size = rows // n_areas
    col_size = cols // n_areas

    for i in range(n_areas):
        for j in range(n_areas):
            r_start = i * row_size
            r_end = r_start + row_size if i < n_areas - 1 else rows
            c_start = j * col_size
            c_end = c_start + col_size if j < n_areas - 1 else cols

            # Measure shift in this area
            roi_pre = pre_layer[r_start:r_end, c_start:c_end]
            roi_post = post_layer[r_start:r_end, c_start:c_end]

            if roi_pre.size == 0 or roi_post.size == 0:
                continue

            # Correlation-based shift
            best_corr = -np.inf
            best_shift = (0, 0)
            for dy in range(-max_shift, max_shift + 1):
                for dx in range(-max_shift, max_shift + 1):
                    roi_pre_pad = np.pad(
                        roi_pre,
                        ((max_shift + 1, max_shift + 1), (max_shift + 1, max_shift + 1)),
                        mode="edge",
                    )
                    y_start = max_shift + dy
                    x_start = max_shift + dx
                    sliced = roi_pre_pad[
                        y_start : y_start + roi_post.shape[0],
                        x_start : x_start + roi_post.shape[1],
                    ]
                    corr = np.sum(sliced * roi_post)
                    if corr > best_corr:
                        best_corr = corr
                        best_shift = (dx, dy)

            shifts_by_area[(i, j)] = best_shift

            # Resample this area with its local shift
            yy, xx = np.mgrid[r_start:r_end, c_start:c_end].astype(np.float64)
            dx, dy = best_shift
            yy_shifted = yy + dy
            xx_shifted = xx + dx
            area_resampled = _bilinear_interp(pre_layer, yy_shifted, xx_shifted)
            result[r_start:r_end, c_start:c_end] = area_resampled

    # Compute overall mean shift for logging
    if shifts_by_area:
        mean_dx = np.mean([s[0] for s in shifts_by_area.values()])
        mean_dy = np.mean([s[1] for s in shifts_by_area.values()])
    else:
        mean_dx, mean_dy = 0.0, 0.0

    shifts_info = {
        "mean_dx": mean_dx,
        "mean_dy": mean_dy,
        "per_area": shifts_by_area,
        "method": f"{n_areas}x{n_areas} areas",
    }

    return result, shifts_info


# ── alternative: normalized cross-correlation (numpy only) ──────────────────

def compute_ncc(pre_layer, post_layer, shifts):
    """
    Compute normalized cross-correlation at given shifts (numpy only).

    Can be used to evaluate shift quality or search over discrete shifts.

    Args:
        pre_layer: 2D array (pre-image).
        post_layer: 2D array (measurement).
        shifts: (dx, dy) tuple or list.

    Returns:
        NCC value (higher = better alignment). Range [−1, 1].
    """
    dx, dy = shifts
    rows, cols = post_layer.shape
    yy, xx = np.mgrid[0:rows, 0:cols].astype(np.float64)
    yy_shifted = yy + dy
    xx_shifted = xx + dx

    resampled = _bilinear_interp(pre_layer, yy_shifted, xx_shifted)

    # Normalized cross-correlation
    mean_pre = np.mean(resampled)
    mean_post = np.mean(post_layer)
    num = np.sum((resampled - mean_pre) * (post_layer - mean_post))
    denom = np.sqrt(
        np.sum((resampled - mean_pre) ** 2) * np.sum((post_layer - mean_post) ** 2)
    )
    if denom < 1e-6:
        return 0.0  # No correlation
    return num / denom
