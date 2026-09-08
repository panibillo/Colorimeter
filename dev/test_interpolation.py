#!/usr/bin/env python3
"""
Test the improved gap interpolation logic with baseline edge detection.
"""
import sys
from pathlib import Path
import numpy as np
import cv2

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bayer_cam import BayerCam
import logging_manager as log

log.set_level("DEBUG")

# Load test image
bayer = np.load("mock_camera/cfg5_sbggr1_raw_00checkerB.npy")
channels = BayerCam.extract_bayer_layers(bayer)
channel_stdevs = {name: float(np.std(channel)) for name, channel in channels.items()}
best_channel = max(channel_stdevs, key=channel_stdevs.get)
channel_data = channels[best_channel].astype(np.float32)
channel_normalized = ((channel_data - channel_data.min()) / 
                      (channel_data.max() - channel_data.min() + 1e-6) * 255).astype(np.uint8)

# Baseline edge detection
blurred = cv2.GaussianBlur(channel_normalized, (5, 5), 1.0)
edges = cv2.Canny(blurred, 30, 80)
lines = cv2.HoughLinesP(edges, 1, np.pi/180, 50, 50, 10)

def cluster_h_lines(lines):
    if lines is None or len(lines) == 0:
        return []
    positions = [(line[0][1] + line[0][3]) / 2 for line in lines]
    positions = sorted(positions)
    clusters = []
    current_cluster = [positions[0]]
    
    for pos in positions[1:]:
        if pos - current_cluster[-1] < 15:
            current_cluster.append(pos)
        else:
            clusters.append(int(np.mean(current_cluster)))
            current_cluster = [pos]
    if current_cluster:
        clusters.append(int(np.mean(current_cluster)))
    
    return clusters

h_positions = cluster_h_lines(lines)

print("="*70)
print("BASELINE DETECTION (Gaussian + Canny 30,80)")
print("="*70)
print(f"Clustered horizontal lines: {len(h_positions)}")
print(f"Positions: {h_positions}")

# Analyze gaps
gaps = [h_positions[i+1] - h_positions[i] for i in range(len(h_positions)-1)]
print(f"\nGap analysis:")
print(f"  Gaps: {[int(g) for g in gaps]}")
print(f"  Median: {np.median(gaps):.1f}")
print(f"  Mean: {np.mean(gaps):.1f}")
print(f"  Min: {min(gaps):.1f}, Max: {max(gaps):.1f}")

# Apply improved gap interpolation (max_gap_multiplier = 1.3)
print("\n" + "="*70)
print("AFTER GAP INTERPOLATION (multiplier=1.3)")
print("="*70)

def interpolate_missing_lines(positions, max_gap_multiplier=1.3):
    """
    Detect and interpolate missing lines in a grid.
    """
    if len(positions) < 2:
        return positions
    
    gaps = [positions[i+1] - positions[i] for i in range(len(positions)-1)]
    median_gap = np.median(gaps)
    
    print(f"Gap statistics: median={median_gap:.1f}")
    print(f"Threshold for interpolation: {median_gap * max_gap_multiplier:.1f}")
    
    new_positions = [positions[0]]
    interpolated_count = 0
    split_gaps = []
    
    for i in range(len(positions) - 1):
        gap = positions[i+1] - positions[i]
        
        # If gap is much larger than median, interpolate missing line(s)
        if gap > median_gap * max_gap_multiplier:
            # Estimate number of missing lines based on gap size
            num_missing = max(1, int(round(gap / median_gap - 1)))
            
            split_gaps.append((i, gap, num_missing))
            print(f"  Gap {positions[i]:3} → {positions[i+1]:3} ({gap:.1f}): "
                  f"interpolating {num_missing} line(s)")
            
            for j in range(num_missing):
                interpolated = int(positions[i] + (j+1) * gap / (num_missing + 1))
                new_positions.append(interpolated)
                interpolated_count += 1
                print(f"    → Position {interpolated}")
        
        new_positions.append(positions[i+1])
    
    print(f"\nInterpolated {interpolated_count} lines from {len(split_gaps)} gaps")
    return sorted(new_positions)

h_positions_new = interpolate_missing_lines(h_positions)
print(f"\nNew horizontal positions: {len(h_positions_new)}")
print(f"Positions: {h_positions_new}")

print("\n" + "="*70)
print("SUMMARY")
print("="*70)
print(f"Before: {len(h_positions):2} horizontal grid lines")
print(f"After:  {len(h_positions_new):2} horizontal grid lines (+{len(h_positions_new) - len(h_positions):1})")
print(f"\nExpected checkerboard layout (after margin filtering):")
print(f"  - Internal grid: ~6-7 rows × ~5-6 columns")
print(f"  - Current detection: {len(h_positions)} lines → {len(h_positions)-1} rows")
print(f"  - After interp.: {len(h_positions_new)} lines → {len(h_positions_new)-1} rows")
