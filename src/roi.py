"""
roi.py — Region of interest detection and management.

Lets write roi.py. This file has a few jobs:
- Define a roi object
- Write roi defs to the database
- Read roi defs from the database into roi objects.
"""

import numpy as np
import db_manager as cdb
import logging_manager as log
import matplotlib.pyplot as plt

from color_cam import ColorCam

def smooth_ema(signal: np.ndarray, alpha: float = 0.1) -> np.ndarray:
    """
    Exponential Moving Average smoother - memory efficient for Raspberry Pi.

    Args:
        signal: 1D numpy array
        alpha: Smoothing factor (0 < alpha <= 1).
               Lower = more smoothing, higher = more responsive
               Typical range: 0.2-0.4 for edge detection

    Returns:
        Smoothed 1D array

    Memory: O(1) - only previous value stored
    Time: O(n) - single pass
    """
    smoothed = np.zeros_like(signal, dtype=np.float64)
    smoothed[0] = signal[0]

    for i in range(1, len(signal)):
        smoothed[i] = alpha * signal[i] + (1 - alpha) * smoothed[i-1]

    return smoothed


def smooth_triangular(signal: np.ndarray, window_size: int = 10) -> np.ndarray:
    """
    Triangular (Bartlett) window smoother - linear weighting.

    Args:
        signal: 1D numpy array
        window_size: Kernel size (e.g., 5, 7, 9, 10)

    Returns:
        Smoothed 1D array (guaranteed same length as input)

    Memory: O(window_size) - minimal vs full convolution
    Time: O(n * window_size)
    """
    # Create triangular kernel (peaks at center)
    kernel = np.array([i + 1 if i < window_size // 2 + 1 else window_size - i
                       for i in range(window_size)], dtype=np.float64)
    print ('kernel = ', kernel)
    
    kernel = kernel / np.sum(kernel)

    result = np.convolve(signal, kernel, mode="same")
    
    # Ensure output is exactly same length as input
    if len(result) != len(signal):
        result = result[:len(signal)]
    
    return result


def rect_to_slices(X, Y, x, y, w, h, scale_x=1, scale_y=1):
    """
    Calculate yslice and xslice encompassing rectangle definition x,y,w,h.

    Scale the rect size from the upper left corner by x,y scaling factors.
    All dimensions in pixels, one pixel per array index.

    Args:
        X: Digit upper left corner row in full image
        Y: Digit upper left corner col in full image
        x: Segment upper left corner row relative to digit upper left
        y: Segment upper left corner col relative to digit upper left
        w: Segment width (columns)
        h: Segment height (rows)
        scale_x: Horizontal scaling factor (default 1)
        scale_y: Vertical scaling factor (default 1)

    Returns:
        Tuple of (yslice, xslice) for numpy array indexing

    Example:
        >>> A = np.zeros((1500, 900))
        >>> roi = rect_to_slices(0, 0, 100, 1505, 110, 110)
        >>> b = A[roi]
    """
    y0 = int(round((Y + y    ) * scale_y))
    y1 = int(round((Y + y + h) * scale_y))
    x0 = int(round((X + x    ) * scale_x))
    x1 = int(round((X + x + w) * scale_x))

    return (slice(y0,y1), slice(x0,x1))


def _find_edges_1d(signal: np.ndarray, threshold: float = 0.22) -> list:
    """
    Find peaks in second derivative to detect edges.

    Args:
        signal: 1D numpy array of values (e.g., average pixel intensity)
        threshold: Minimum peak height as fraction of max second derivative

    Returns:
        List of indices where edges occur
    """
    
    # Smooth signal using efficient EMA (exponential moving average) - memory efficient for Raspberry Pi
    # smoothed = smooth_ema(signal, alpha=0.1)
    signal = (signal[:-1] + signal[1:]) / 2
    smoothed = smooth_triangular(signal, window_size=9)

    # Compute first and second differences on smoothed signal
    # diff1 = smooth_triangular(np.diff(smoothed), window_size=10)
    diff1 = smooth_triangular(np.diff(smoothed), window_size=7)
    diff2 = np.diff(diff1)

    print (signal.shape, smoothed.shape, diff1.shape, diff2.shape)

    # Second derivative of signal: positive = rising edge, negative = falling
    # Find absolute value to detect both types of edges
    edges = np.abs(diff1)

    # Threshold to find significant edges (ignore noise)
    threshold_val = np.max(edges) * threshold if np.max(edges) > 0 else 0
    #threshold_val = np.median(edges)
    edge_indices = np.where(edges > threshold_val)[0] + 1  # +1 because diff reduces array
    print ('edges', len(edges), edge_indices[-4:])

    # DEBUG: Plot signal and edge detection
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 6), sharex=True)
    
    ax1.plot(signal, label="Raw Signal", linewidth=1, alpha=0.5)
    ax1.plot(smoothed, label="Smoothed (6-pt avg)", linewidth=1.5, color="blue")
    ax1.set_ylabel("Pixel Value")
    ax1.set_title("Signal (Luminance) - Raw vs Smoothed")
    ax1.grid(True, alpha=0.3)
    ax1.legend()
    
    ax2.plot(diff1, label="First Derivative", linewidth=1, color="orange")
    ax2.set_ylabel("d/dx")
    ax2.set_xlabel("Position")
    ax2.grid(True, alpha=0.3)
    ax2.legend()
    
    ax3.plot(edges, label="Abs(Second Derivative)", linewidth=1, color="orange")
    ax3.axhline(y=threshold_val, color="red", linestyle="--", linewidth=2, label=f"Threshold ({threshold_val:.1f})")
    # ax3.scatter(edge_indices, edges[edge_indices], color="green", s=40, zorder=5, label="Detected edges")
    ax3.set_ylabel("Abs(d²/dx²)")
    ax3.set_xlabel("Position")
    ax3.set_title(f"Edge Detection (threshold={threshold:.2f}, found {len(edge_indices)} edges)")
    ax3.grid(True, alpha=0.3)
    ax3.legend()
    
    plt.tight_layout()
    plt.show()

    return edge_indices.tolist()


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


def register_colorcard(cardid: int, cardname: str, bayer_array: np.ndarray) -> bool:
    """
    Analyze an image of a color card and register rectangle ROIs to database.

    Algorithm:
    1. Extract Bayer color channels (B, Gb, Gr, R)
    2. Sample horizontal lines and find X boundaries (column edges) via second derivative
    3. Sample vertical lines and find Y boundaries (row edges) via second derivative
    4. Refine and validate the rectangular grid
    5. For each rectangle, extract RGB mean values and classify color
    6. Write all rectangles to database with metadata

    Args:
        cardid: Unique identifier for this color card
        cardname: Human-readable name for this card (e.g., "cal_card_01")
        bayer_array: 2D numpy array (H x W) containing BGGR Bayer values (uint16)

    Returns:
        True if registration successful, False on error
    """
    log.info(f"Registering color card '{cardname}' (ID {cardid})")

    try:
        H, W = bayer_array.shape
        log.debug(f"Bayer array shape: {H} x {W}")

        # Extract the four Bayer channels
        channels = ColorCam.extract_bayer_layers(bayer_array)
        log.debug(f"Extracted Bayer channels: {list(channels.keys())}")

        # Pick the channel with highest standard deviation (best contrast for edge detection)
        # Green is typically good, but test image may have better contrast in another channel
        channel_stdevs = {}
        for name, channel in channels.items():
            channel_stdevs[name] = float(np.std(channel))

        best_channel = max(channel_stdevs, key=channel_stdevs.get)
        log.info(f"Channel stdevs: B={channel_stdevs['B']:.1f}, Gb={channel_stdevs['Gb']:.1f}, "
                 f"Gr={channel_stdevs['Gr']:.1f}, R={channel_stdevs['R']:.1f}")
        log.info(f"Using '{best_channel}' channel (stdev={channel_stdevs[best_channel]:.1f})")

        # Use best channel for edge detection
        luminance = channels[best_channel].astype(np.float32)
        log.debug(f"Luminance channel shape: {luminance.shape}")

        # ==========================================
        # STEP 1: Find X boundaries (column edges)
        # ==========================================
        log.info("Finding X boundaries (vertical edges)...")

        # Sample horizontal lines at multiple Y positions to find consistent X boundaries
        # Use 5 samples evenly distributed across image height, omitting edges
        sample_count_y = 5
        sample_y_indices = np.linspace(20, luminance.shape[0] - 20, sample_count_y+2, dtype=int)[1:-1]

        all_x_boundaries = []
        for y_idx in sample_y_indices:
            # Extract horizontal line and find edges
            h_line = luminance[y_idx, :]
            edges = _find_edges_1d(h_line, threshold=0.4)
            all_x_boundaries.extend(edges)
            log.debug(f"  Y={y_idx}: found {len(edges)} edges at X positions: {edges[:5]}...")

        # Find consensus X boundaries (edges that appear in multiple samples)
        if all_x_boundaries:
            # Cluster nearby edges together
            all_x_boundaries = sorted(all_x_boundaries)
            x_boundaries = []
            current_cluster = [all_x_boundaries[0]]

            for x_val in all_x_boundaries[1:]:
                if x_val - current_cluster[-1] < 10:  # Within 10 pixels = same edge
                    current_cluster.append(x_val)
                else:
                    x_boundaries.append(int(np.mean(current_cluster)))
                    current_cluster = [x_val]
            if current_cluster:
                x_boundaries.append(int(np.mean(current_cluster)))

            log.info(f"Found {len(x_boundaries)} X boundaries: {x_boundaries}")
        else:
            log.error("No X boundaries found!")
            return False

        # ==========================================
        # STEP 2: Find Y boundaries (row edges)
        # ==========================================
        log.info("Finding Y boundaries (horizontal edges)...")

        # Between each pair of X boundaries, sample vertical lines
        y_boundaries_list = []  # Will be a list of boundary sets, one per X section

        for i in range(len(x_boundaries) - 1):
            x_start = x_boundaries[i]
            x_end = x_boundaries[i + 1]
            x_mid = (x_start + x_end) // 2

            # Sample vertical column at middle of this X section
            v_line = luminance[:, x_mid]
            edges = _find_edges_1d(v_line, threshold=0.4)

            if edges:
                # Cluster nearby edges
                edges = sorted(edges)
                y_boundaries = []
                current_cluster = [edges[0]]

                for y_val in edges[1:]:
                    if y_val - current_cluster[-1] < 10:
                        current_cluster.append(y_val)
                    else:
                        y_boundaries.append(int(np.mean(current_cluster)))
                        current_cluster = [y_val]
                if current_cluster:
                    y_boundaries.append(int(np.mean(current_cluster)))

                y_boundaries_list.append(y_boundaries)
                log.debug(f"  X section [{x_start}:{x_end}]: found {len(y_boundaries)} Y edges")

        if not y_boundaries_list:
            log.error("No Y boundaries found!")
            return False

        # Use the most common Y boundaries (assumed to be the main grid)
        # For now, use the first valid set
        y_boundaries = y_boundaries_list[0]
        log.info(f"Using Y boundaries: {y_boundaries}")

        # ==========================================
        # STEP 3: Build rectangle list from grid
        # ==========================================
        log.info("Building rectangle grid...")

        rectangles = []
        for row_idx in range(len(y_boundaries) - 1):
            for col_idx in range(len(x_boundaries) - 1):
                y = y_boundaries[row_idx]
                y_next = y_boundaries[row_idx + 1]
                x = x_boundaries[col_idx]
                x_next = x_boundaries[col_idx + 1]

                rect_h = y_next - y
                rect_w = x_next - x

                # Filter out very small rectangles (likely noise)
                if rect_h > 10 and rect_w > 10:
                    rectangles.append({
                        "row": row_idx,
                        "col": col_idx,
                        "x": x,
                        "y": y,
                        "w": rect_w,
                        "h": rect_h,
                    })

        log.info(f"Found {len(rectangles)} valid rectangles")
        log.debug(f"Rectangle grid: {len(y_boundaries)-1} rows x {len(x_boundaries)-1} cols")

        # ==========================================
        # STEP 4: Extract and classify colors
        # ==========================================
        log.info("Classifying rectangle colors...")

        # Convert Bayer channels to 0-255 range for color classification
        r_mean = channels["R"].astype(np.float32)
        g_mean = (channels["Gb"].astype(np.float32) + channels["Gr"].astype(np.float32)) / 2
        b_mean = channels["B"].astype(np.float32)

        # Normalize to 0-255
        r_norm = (r_mean / r_mean.max() * 255).astype(np.uint8) if r_mean.max() > 0 else r_mean.astype(np.uint8)
        g_norm = (g_mean / g_mean.max() * 255).astype(np.uint8) if g_mean.max() > 0 else g_mean.astype(np.uint8)
        b_norm = (b_mean / b_mean.max() * 255).astype(np.uint8) if b_mean.max() > 0 else b_mean.astype(np.uint8)

        for rect in rectangles:
            x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]

            # Extract rectangle region from Bayer channels
            # Bayer channels are downsampled by 2, so divide coordinates by 2
            bx, by = x // 2, y // 2
            bw, bh = (w + 1) // 2, (h + 1) // 2

            # Clip to channel bounds
            bx = min(bx, r_norm.shape[1] - 1)
            by = min(by, r_norm.shape[0] - 1)
            bw = min(bw, r_norm.shape[1] - bx)
            bh = min(bh, r_norm.shape[0] - by)

            # Compute mean RGB for this rectangle
            r_val = int(np.mean(r_norm[by:by+bh, bx:bx+bw]))
            g_val = int(np.mean(g_norm[by:by+bh, bx:bx+bw]))
            b_val = int(np.mean(b_norm[by:by+bh, bx:bx+bw]))

            color_name = _classify_color((r_val, g_val, b_val))

            rect["color"] = color_name
            rect["rgb"] = (r_val, g_val, b_val)
            log.debug(f"  Rect [{rect['row']},{rect['col']}]: {color_name} RGB=({r_val},{g_val},{b_val})")

        # ==========================================
        # STEP 5: Write to database
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

            rows_affected = cdb.write_one(sql, vals)
            if rows_affected != 1:
                log.warning(f"Unexpected rows affected: {rows_affected}")

        log.info(f"Successfully registered {len(rectangles)} rectangles for '{cardname}'")
        return True

    except Exception as e:
        log.error(f"Error in register_colorcard(): {e}")
        import traceback
        log.debug(traceback.format_exc())
        return False


# ==========================================
# TEST
# ==========================================
if __name__ == "__main__":
    log.set_level("DEBUG")
    log.info("Starting roi.py test...")

    # Load test Bayer array
    try:
        bayer_test = np.load("mock_camera/cfg5_sbggr1_raw_00checkerB.npy")
        log.info(f"Loaded test Bayer array: shape={bayer_test.shape}, dtype={bayer_test.dtype}")
    except FileNotFoundError as e:
        log.error(f"Cannot load test file: {e}")
        exit(1)

    # Run registration
    success = register_colorcard(1, "test_checkerboard", bayer_test)

    if success:
        log.info("✓ Test passed!")
    else:
        log.error("✗ Test failed")
        exit(1)
