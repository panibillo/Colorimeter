#!/usr/bin/env python3
"""
test_roi.py — Test suite for ROI detection and visualization.

Tests the register_colorcard() function by:
1. Loading a test Bayer image (checkerboard pattern)
2. Registering ROI rectangles to database
3. Querying stored rectangles from database
4. Visualizing rectangles on the image with bounds and labels

Usage:
    python test/test_roi.py
"""

import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches

# Add src/ to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import logging_manager as log
import db_manager as cdb
from roi import register_colorcard
from bayer_cam import BayerCam


def load_test_image(image_path: str = "mock_camera/cfg5_sbggr1_raw_00checkerB.npy") -> np.ndarray:
    """
    Load test Bayer image from file.
    
    Args:
        image_path: Path to .npy file containing Bayer array
    
    Returns:
        numpy array of shape (H, W) dtype uint16
    
    Raises:
        FileNotFoundError: If file not found
    """
    try:
        bayer = np.load(image_path)
        log.info(f"✓ Loaded test image: {image_path}")
        log.debug(f"  Shape: {bayer.shape}, dtype: {bayer.dtype}")
        return bayer
    except FileNotFoundError as e:
        log.error(f"Cannot load test image: {e}")
        raise


def display_roi_visualization(cardid: int, bayer_array: np.ndarray):
    """
    Query database and display image with ROI rectangles and labels.
    
    Shows:
    - Normalized Bayer image (visualizing best channel for contrast)
    - Green rectangle borders for each ROI
    - Rectangle ID label centered in each rectangle
    - Color name and RGB values in legend
    
    Args:
        cardid: Card ID to query from database
        bayer_array: Original Bayer array (for normalization/display)
    """
    log.info(f"Querying database for card {cardid}...")
    
    # Query stored rectangles
    sql = """
        SELECT rectid, colorname, x, y, w, h, CAST(x AS REAL) AS rgb_r
        FROM colorcard 
        WHERE cardid = ? 
        ORDER BY rectid
    """
    rows = cdb.read_all(sql, (cardid,))
    
    if not rows:
        log.error(f"No rectangles found for card {cardid} in database!")
        return False
    
    log.info(f"✓ Retrieved {len(rows)} rectangles from database")
    
    # Extract best channel for display (same as registration used)
    channels = BayerCam.extract_bayer_layers(bayer_array)
    channel_stdevs = {name: float(np.std(channel)) for name, channel in channels.items()}
    best_channel = max(channel_stdevs, key=channel_stdevs.get)
    channel_data = channels[best_channel].astype(np.float32)
    
    # Normalize to 0-255 for display
    channel_normalized = ((channel_data - channel_data.min()) / 
                          (channel_data.max() - channel_data.min() + 1e-6) * 255).astype(np.uint8)
    
    # Create figure
    fig, ax = plt.subplots(figsize=(14, 10))
    
    # Display image
    ax.imshow(channel_normalized, cmap='gray', aspect='auto')
    ax.set_title(f"ROI Detection: Color Card {cardid}\n"
                 f"Using '{best_channel}' channel for visualization | "
                 f"Bayer array size: {bayer_array.shape[0]}×{bayer_array.shape[1]}",
                 fontsize=14, fontweight='bold')
    
    # Dictionary to track color combinations
    color_stats = {}
    
    # Draw rectangles and labels
    for idx, row in enumerate(rows):
        rectid, colorname, x, y, w, h = row[:6]
        
        # Draw rectangle border
        rect = patches.Rectangle((x, y), w, h, linewidth=2.5, 
                                 edgecolor='lime', facecolor='none', 
                                 linestyle='-', label=f'ROI {rectid}' if idx < 5 else '')
        ax.add_patch(rect)
        
        # Draw rectid label centered in rectangle
        text_x = x + w / 2
        text_y = y + h / 2
        ax.text(text_x, text_y, str(rectid), 
               color='yellow', fontsize=10, fontweight='bold',
               ha='center', va='center',
               bbox=dict(boxstyle='round,pad=0.3', facecolor='black', alpha=0.7))
        
        # Collect color statistics
        if colorname not in color_stats:
            color_stats[colorname] = []
        color_stats[colorname].append(rectid)
        
        log.debug(f"  Rect {rectid}: {colorname:12} at ({x:4}, {y:4}) size {w:4}×{h:4}")
    
    # Set axis labels and grid
    ax.set_xlabel("X (pixels)", fontsize=11, fontweight='bold')
    ax.set_ylabel("Y (pixels)", fontsize=11, fontweight='bold')
    ax.grid(True, alpha=0.2, linestyle='--', linewidth=0.5)
    
    # Create legend with color statistics
    legend_text = f"Color Distribution ({len(rows)} rectangles):\n"
    for color_name in sorted(color_stats.keys()):
        count = len(color_stats[color_name])
        rectids = color_stats[color_name][:5]  # Show first 5
        rectid_str = ", ".join(map(str, rectids))
        if len(color_stats[color_name]) > 5:
            rectid_str += ", ..."
        legend_text += f"  {color_name:12} ({count:2}) → [{rectid_str}]\n"
    
    ax.text(0.02, 0.98, legend_text, transform=ax.transAxes,
           fontsize=10, verticalalignment='top', family='monospace',
           bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))
    
    plt.tight_layout()
    
    # Save figure
    output_file = "test_roi_visualization.png"
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    log.info(f"✓ Saved visualization to: {output_file}")
    
    plt.show()
    
    return True


def main():
    """
    Main test flow:
    1. Setup logging
    2. Load test Bayer image
    3. Clear previous test data from database
    4. Call register_colorcard()
    5. Query and visualize results
    """
    log.set_level("INFO")
    log.info("=" * 60)
    log.info("ROI Detection Test Suite")
    log.info("=" * 60)
    
    try:
        # ==========================================
        # STEP 1: Load test image
        # ==========================================
        bayer_test = load_test_image()
        
        # ==========================================
        # STEP 2: Clear previous test data
        # ==========================================
        # Delete any previous test data for cardid=1
        log.info("Clearing previous test data...")
        sql = "DELETE FROM colorcard WHERE cardid = ?"
        cdb.write_one(sql, (1,))
        log.debug("Previous data cleared")
        
        # ==========================================
        # STEP 3: Register color card
        # ==========================================
        log.info("\nRegistering color card...")
        success = register_colorcard(1, "test_checkerboard", bayer_test)
        
        if not success:
            log.error("✗ Registration failed!")
            return False
        
        # ==========================================
        # STEP 4: Visualize results
        # ==========================================
        log.info("\nGenerating visualization...")
        success = display_roi_visualization(1, bayer_test)
        
        if not success:
            log.error("✗ Visualization failed!")
            return False
        
        log.info("\n" + "=" * 60)
        log.info("✓ Test completed successfully!")
        log.info("=" * 60)
        return True
    
    except Exception as e:
        log.error(f"✗ Test failed with exception: {e}")
        import traceback
        log.debug(traceback.format_exc())
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
