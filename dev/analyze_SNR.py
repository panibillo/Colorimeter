"""

A few notes on choices made
- Even-snapping the rect. Bayer patterns require pixel coordinates to be even 
  to preserve which color sits at each position. If the user passes an odd left 
  or top, snapping down preserves the BGGR assumption without silently giving 
  wrong channel assignments.
- Kernel size based on channel height, not image height. Since each channel is 
  half-resolution after de-Bayering, I based the H/20 default on the channel's 
  own height. If you'd prefer it based on the original image height, just 
  remove the // 2.
- Float32 for the blur. cv2.GaussianBlur on uint16 works but float32 avoids 
  any rounding in the residual calculation and is what you want for stdev 
  measurement.
- Returning smoothed channels as float32. They're already in float form from 
  the blur, and you'll likely want to feed them to your contouring. If you  
  need   them back as uint16, cast with .astype(np.uint16) at the call site.
- Sigma reported as "cv2_default". When the user passes None, the actual sigma 
  used is derived internally by OpenCV from the kernel size. If you want 
  thenumerical value for logging, you can compute it as 
      sigma = 0.3 * ((kernel_size - 1) * 0.5 - 1) + 0.8
  which is the formula OpenCV uses.
"""

import cv2
import numpy as np


def measure_bayer_noise(
    bayer,
    rect=None,
    kernel_size=None,
    sigma=None,
    black_level=64,
):
    """
    Measure per-channel noise in a raw Bayer image of a uniform target.

    Splits a BGGR Bayer image into its four channels, applies Gaussian
    smoothing, and computes SNR from the high-frequency residual.

    Parameters
    ----------
    bayer : np.ndarray
        uint16 array of shape (H, W), BGGR Bayer pattern.
    rect : tuple of (left, top, width, height), optional
        Region of interest. If None, the whole image is used.
        Coordinates are in the full Bayer image; will be snapped to even
        values so the Bayer pattern is preserved.
    kernel_size : int, optional
        Odd integer kernel size for GaussianBlur, applied to each
        channel (which is half-resolution). If None, defaults to
        roughly H/20, rounded to the nearest odd integer >= 3.
    sigma : float, optional
        Sigma for GaussianBlur. If None, OpenCV derives it from
        kernel_size.
    black_level : int, optional
        Black level pedestal subtracted from channel means when
        computing SNR. Default 64 (typical for IMX219 10-bit raw).

    Returns
    -------
    smoothed : dict
        Dict with keys 'B', 'Gb', 'Gr', 'R', each a 2D float32 array of
        the smoothed channel within the rect (half-resolution).
    info : dict
        Dict with rect, image_size, kernel_size, sigma, black_level,
        and per-channel measurements (mean, signal, noise_stdev,
        relative_noise, snr).
    """
    H, W = bayer.shape

    # Resolve rect — snap to even coordinates to preserve Bayer alignment.
    if rect is None:
        left, top, width, height = 0, 0, W, H
    else:
        left, top, width, height = rect
        left -= left % 2
        top -= top % 2
        width -= width % 2
        height -= height % 2
        if width <= 0 or height <= 0:
            raise ValueError("rect has non-positive width or height after even-snap")
        if left + width > W or top + height > H:
            raise ValueError("rect extends beyond image bounds")

    # Crop to rect.
    cropped = bayer[top:top + height, left:left + width]

    # Split BGGR into four half-resolution channels.
    # BGGR layout:
    #   row 0: B G B G ...
    #   row 1: G R G R ...
    channels = {
        "B":  cropped[0::2, 0::2],
        "Gb": cropped[0::2, 1::2],
        "Gr": cropped[1::2, 0::2],
        "R":  cropped[1::2, 1::2],
    }

    # Resolve kernel_size. The channels are half-resolution, so base the
    # default on the channel height (height // 2), scaled by ~1/20.
    if kernel_size is None:
        kernel_size = max(3, (height // 2) // 10)
        if kernel_size % 2 == 0:
            kernel_size += 1
    else:
        if kernel_size < 3 or kernel_size % 2 == 0:
            raise ValueError("kernel_size must be an odd integer >= 3")

    # Resolve sigma — 0 tells OpenCV to derive it from kernel_size.
    gb_sigma = 0 if sigma is None else float(sigma)

    smoothed = {}
    measurements = {}

    for name, chan in channels.items():
        # GaussianBlur needs float32 (or similar) for accurate arithmetic
        # on uint16 data.
        chan_f = chan.astype(np.float32)
        blurred = cv2.GaussianBlur(
            chan_f, (kernel_size, kernel_size), gb_sigma
        )
        residual = chan_f - blurred

        mean_raw = float(chan_f.mean())
        signal = mean_raw - black_level
        noise_stdev = float(residual.std())
        relative_noise = noise_stdev / signal if signal > 0 else float("nan")
        snr = signal / noise_stdev if noise_stdev > 0 else float("nan")

        smoothed[name] = blurred
        measurements[name] = {
            "mean": mean_raw,
            "signal": signal,
            "noise_stdev": noise_stdev,
            "relative_noise": relative_noise,
            "snr": snr,
        }

    info = {
        "rect": (left, top, width, height),
        "image_size": (W, H),
        "kernel_size": kernel_size,
        "sigma": gb_sigma if sigma is not None else "cv2_default",
        "black_level": black_level,
        "channels": measurements,
    }

    return smoothed, info