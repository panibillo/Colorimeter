"""
roi.py — Region of interest detection and management.

Lets write roi.py. This file has a few jobs:
- Define a roi object
- Write roi defs to the database
- Read roi defs from the database into roi objects.

TODO:
- shrink rectangles to avoid edge noise
- remove matplotlib, which should be used only in test.
- cleanup unused code
- Create new format for color cards
    - encode card id on the margin: machine readable and human readable areas
    - ¿draw alignment ticks on the margins and redo pattern recognition?

"""

import numpy as np
import cv2
import db_manager as cdb
import logging_manager as log
import matplotlib.pyplot as plt

from bayer_cam import BayerCam

class Rect:
    """
    Recall for drawing that a rect x,y is upper left corner.
    """
    def __init__(self, x, y, w, h)
        self.x = int(x)
        self.y = int(y)
        self.w = int(w)
        self.h = int(h)

    def x2(self):
        return self.x + self.w
    def y2(self):
        return self.y + self.h
    def xywh(self):
        return self.x, self.y, self.w, self.h
    def xyslice(self):
        return slice(self.x, self.x2), slice(self.y, self.y2)

class Roi(Rect):
    def __init__(self, x, y, w, h, id, name, color=None):
        super().__init__(x, y, w, h)
        self.id = int(id)
        self.name = str(name)
        self.color = color

class Roi_group:
    def __init__(self, *roi):
        self.group = set{*roi}

def buffer(bayer_array, roi):
    



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
              f"top={margin_top}, bottom={margin_bottom}", 
             location="filter_margin_rectangles:L54")
    
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
        log.info(f"Filtered margin rectangles:", location="filter_margin_rectangles:L89")
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
            log.info(f"  {m['rectid']}: {edges_str}", location="filter_margin_rectangles:L101")
    
    log.info(f"Kept {len(interior_rects)} interior rectangles, removed {len(margin_rects)} margins", 
             location="filter_margin_rectangles:L103")
    
    return interior_rects




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

    Args:
        cardid: Unique identifier for this color card
        cardname: Human-readable name for this card (e.g., "cal_card_01")
        bayer_array: 2D numpy array (H x W) containing BGGR Bayer values (uint16)

    Returns:
        True if registration successful, False on error
    """
    log.info(f"Registering color card '{cardname}' (ID {cardid})", location="register_colorcard:L231")

    try:
        H, W = bayer_array.shape
        log.debug(f"Bayer array shape: {H} x {W}")

        # ==========================================
        # STEP 1: Extract best channel and normalize
        # ==========================================
        channels = BayerCam.extract_bayer_layers(bayer_array)
        log.debug(f"Extracted Bayer channels: {list(channels.keys())}")

        # Pick channel with highest contrast (std dev)
        channel_stdevs = {name: float(np.std(channel)) for name, channel in channels.items()}
        best_channel = max(channel_stdevs, key=channel_stdevs.get)
        log.info(f"Channel stdevs: B={channel_stdevs['B']:.1f}, Gb={channel_stdevs['Gb']:.1f}, "
                 f"Gr={channel_stdevs['Gr']:.1f}, R={channel_stdevs['R']:.1f}", 
                 location="register_colorcard:L246")
        log.info(f"Using '{best_channel}' channel for edge detection (stdev={channel_stdevs[best_channel]:.1f})",
                 location="register_colorcard:L247")

        # Normalize to 0-255 uint8
        channel_data = channels[best_channel].astype(np.float32)
        channel_normalized = ((channel_data - channel_data.min()) / (channel_data.max() - channel_data.min() + 1e-6) * 255).astype(np.uint8)
        log.debug(f"Normalized channel to 0-255 range")

        # ==========================================
        # STEP 2: Detect edges using Canny
        # ==========================================
        log.info("Detecting edges with Canny edge detector...", location="register_colorcard:L260")
        
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
        log.info("Detecting grid lines with Hough transform...", location="register_colorcard:L274")
        
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
        
        log.info(f"Classified lines: {len(horizontal_lines)} horizontal, {len(vertical_lines)} vertical",
                 location="register_colorcard:L310")
        
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
                            f"({num_missing} lines likely missed)", 
                            location="register_colorcard:L360")
                    
                    for j in range(num_missing):
                        interpolated = int(positions[i] + (j+1) * gap / (num_missing + 1))
                        new_positions.append(interpolated)
                        interpolated_count += 1
                        log.debug(f"  Interpolated {dimension_name} line at position {interpolated}")
                
                new_positions.append(positions[i+1])
            
            if split_gaps:
                log.info(f"Interpolation summary ({dimension_name}): "
                        f"added {interpolated_count} lines from {len(split_gaps)} gaps",
                        location="register_colorcard:L371")
            
            return sorted(new_positions)
        
        h_positions_before = len(h_positions)
        v_positions_before = len(v_positions)
        
        h_positions = interpolate_missing_lines(h_positions, dimension_name="horizontal")
        v_positions = interpolate_missing_lines(v_positions, dimension_name="vertical")
        
        if len(h_positions) > h_positions_before or len(v_positions) > v_positions_before:
            log.info(f"After interpolation: {len(h_positions)} horizontal, {len(v_positions)} vertical "
                    f"(added {len(h_positions) - h_positions_before}H, {len(v_positions) - v_positions_before}V)",
                    location="register_colorcard:L379")

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
        log.info(f"Writing {len(rectangles)} rectangles to database...",
                 location="register_colorcard:L444")
        
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
        
        log.info(f"✓ Successfully registered {len(rectangles)} rectangles for '{cardname}'",
                 location="register_colorcard:L468")
        return True

    except Exception as e:
        log.error(f"Error in register_colorcard(): {e}")
        import traceback
        log.debug(traceback.format_exc())
        return False


# ==========================================
# For testing, use: python test/test_roi.py
# ==========================================
