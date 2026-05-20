"""
Bayer image noise analysis and smoothing utilities.

Design notes
------------
- Even-snapping the rect: Bayer patterns require pixel coordinates to be even
  to preserve which color sits at each position.
- Float32 for smoothing: avoids rounding in the residual calculation.
- Returning smoothed channels as float32: feed directly to contour plotting;
  cast with .astype(np.uint16) at the call site if uint16 is needed.
- Sigma reported as "cv2_default": when sigma=None, OpenCV derives it from
  kernel_size using: sigma = 0.3 * ((kernel_size - 1) * 0.5 - 1) + 0.8
- Poly fit on downsampled grid: for channels > 500 px on either side, fit is
  done on a 200×200 grid (INTER_AREA downsampled) then evaluated at full res.
"""

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _resolve_rect(bayer_shape, rect):
    """
    Snap rect to even coordinates and return (left, top, width, height).
    If rect is None, returns the full image bounds.
    """
    H, W = bayer_shape
    if rect is None:
        return 0, 0, W, H
    left, top, width, height = rect
    left   -= left   % 2
    top    -= top    % 2
    width  -= width  % 2
    height -= height % 2
    if width <= 0 or height <= 0:
        raise ValueError("rect has non-positive width or height after even-snap")
    if left + width > W or top + height > H:
        raise ValueError("rect extends beyond image bounds")
    return left, top, width, height


def _split_bggr(cropped):
    """
    Split a BGGR-pattern crop into four half-resolution channels.

    Layout:
        row 0:  B   Gb  B   Gb ...
        row 1:  Gr  R   Gr  R  ...

    Returns dict with keys 'B', 'Gb', 'Gr', 'R'.
    """
    return {
        "B":  cropped[0::2, 0::2],
        "Gb": cropped[0::2, 1::2],
        "Gr": cropped[1::2, 0::2],
        "R":  cropped[1::2, 1::2],
    }


def _residual_stats(channels, smoothed, black_level):
    """
    Compute per-channel SNR statistics from raw channels and their smooth fits.

    Parameters
    ----------
    channels : dict of str → 2D uint16/float array (raw)
    smoothed : dict of str → 2D float32 array (smooth surface)
    black_level : int

    Returns
    -------
    dict of str → {mean, signal, noise_stdev, relative_noise, snr}
    """
    measurements = {}
    for name, chan in channels.items():
        chan_f    = chan.astype(np.float32)
        residual  = chan_f - smoothed[name]
        mean_raw  = float(chan_f.mean())
        signal    = mean_raw - black_level
        noise_std = float(residual.std())
        measurements[name] = {
            "mean":           mean_raw,
            "signal":         signal,
            "noise_stdev":    noise_std,
            "relative_noise": noise_std / signal if signal > 0 else float("nan"),
            "snr":            signal / noise_std if noise_std > 0 else float("nan"),
        }
    return measurements


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def gaussian_smooth_bayer(
    bayer,
    rect=None,
    kernel_size=None,
    sigma=None,
    black_level=64,
):
    """
    Smooth each BGGR channel with a Gaussian blur and measure noise SNR.

    Parameters
    ----------
    bayer : np.ndarray
        uint16 array, shape (H, W), BGGR pattern.
    rect : tuple (left, top, width, height), optional
        ROI in full-image coordinates; snapped to even values.
    kernel_size : int, optional
        Odd integer >= 3. Defaults to channel_height // 10, rounded to odd.
    sigma : float, optional
        Gaussian sigma. If None, OpenCV derives it from kernel_size.
    black_level : int
        Pedestal subtracted from channel means for SNR. Default 64.

    Returns
    -------
    smoothed : dict  {'B', 'Gb', 'Gr', 'R'} → float32 (H//2, W//2)
    info : dict
        rect, image_size, method, kernel_size, sigma, black_level, channels
    """
    H, W = bayer.shape
    left, top, width, height = _resolve_rect(bayer.shape, rect)
    cropped  = bayer[top:top + height, left:left + width]
    channels = _split_bggr(cropped)
    ch_h     = height // 2

    if kernel_size is None:
        kernel_size = max(3, ch_h // 10)
        if kernel_size % 2 == 0:
            kernel_size += 1
    elif kernel_size < 3 or kernel_size % 2 == 0:
        raise ValueError("kernel_size must be an odd integer >= 3")

    gb_sigma = 0 if sigma is None else float(sigma)

    smoothed = {
        name: cv2.GaussianBlur(chan.astype(np.float32),
                               (kernel_size, kernel_size), gb_sigma)
        for name, chan in channels.items()
    }

    info = {
        "method":      "gaussian",
        "rect":        (left, top, width, height),
        "image_size":  (W, H),
        "kernel_size": kernel_size,
        "sigma":       gb_sigma if sigma is not None else "cv2_default",
        "black_level": black_level,
        "channels":    _residual_stats(channels, smoothed, black_level),
    }
    return smoothed, info


def poly_smooth_bayer(
    bayer,
    rect=None,
    poly_degree=4,
    black_level=64,
):
    """
    Fit a 2D polynomial surface to each BGGR channel and measure noise SNR.

    Parameters
    ----------
    bayer : np.ndarray
        uint16 array, shape (H, W), BGGR pattern.
    rect : tuple (left, top, width, height), optional
        ROI in full-image coordinates; snapped to even values.
    poly_degree : int
        Degree of the 2D polynomial. Number of terms = (d+1)(d+2)/2. Default 4.
    black_level : int
        Pedestal subtracted from channel means for SNR. Default 64.

    Returns
    -------
    smoothed : dict  {'B', 'Gb', 'Gr', 'R'} → float32 (H//2, W//2)
    info : dict
        rect, image_size, method, poly_degree, poly_fit_downsampled,
        poly_fit_grid (if downsampled), black_level, channels,
        poly_coefficients
    """
    H, W = bayer.shape
    left, top, width, height = _resolve_rect(bayer.shape, rect)
    cropped  = bayer[top:top + height, left:left + width]
    channels = _split_bggr(cropped)
    ch_h, ch_w = height // 2, width // 2

    # Build full-resolution design matrix (normalized coords for stability).
    ys = np.linspace(-1.0, 1.0, ch_h, dtype=np.float64)
    xs = np.linspace(-1.0, 1.0, ch_w, dtype=np.float64)
    xg, yg = np.meshgrid(xs, ys)
    xf, yf = xg.ravel(), yg.ravel()

    term_labels, columns = [], []
    for deg in range(poly_degree + 1):
        for iy in range(deg + 1):
            ix = deg - iy
            columns.append((xf ** ix) * (yf ** iy))
            term_labels.append(f"x^{ix}*y^{iy}")
    A = np.column_stack(columns)

    # For large channels fit on a downsampled grid, evaluate at full res.
    fit_downsampled = ch_h > 500 or ch_w > 500
    if fit_downsampled:
        ds_h = min(ch_h, 200)
        ds_w = min(ch_w, 200)
        ds_ys = np.linspace(-1.0, 1.0, ds_h, dtype=np.float64)
        ds_xs = np.linspace(-1.0, 1.0, ds_w, dtype=np.float64)
        ds_xg, ds_yg = np.meshgrid(ds_xs, ds_ys)
        ds_xf, ds_yf = ds_xg.ravel(), ds_yg.ravel()
        ds_cols = []
        for deg in range(poly_degree + 1):
            for iy in range(deg + 1):
                ix = deg - iy
                ds_cols.append((ds_xf ** ix) * (ds_yf ** iy))
        A_ds = np.column_stack(ds_cols)

    smoothed         = {}
    poly_coefficients = {}

    for name, chan in channels.items():
        chan_f = chan.astype(np.float32)
        if fit_downsampled:
            chan_ds = cv2.resize(chan_f, (ds_w, ds_h), interpolation=cv2.INTER_AREA)
            z       = chan_ds.ravel().astype(np.float64)
            coeffs, _, _, _ = np.linalg.lstsq(A_ds, z, rcond=None)
        else:
            z = chan_f.ravel().astype(np.float64)
            coeffs, _, _, _ = np.linalg.lstsq(A, z, rcond=None)

        surface = A.dot(coeffs).reshape(ch_h, ch_w).astype(np.float32)
        smoothed[name] = surface
        poly_coefficients[name] = {"terms": term_labels, "coeffs": coeffs.tolist()}

    info = {
        "method":               "poly",
        "rect":                 (left, top, width, height),
        "image_size":           (W, H),
        "poly_degree":          poly_degree,
        "poly_fit_downsampled": fit_downsampled,
        "black_level":          black_level,
        "channels":             _residual_stats(channels, smoothed, black_level),
        "poly_coefficients":    poly_coefficients,
    }
    if fit_downsampled:
        info["poly_fit_grid"] = (ds_w, ds_h)

    return smoothed, info
