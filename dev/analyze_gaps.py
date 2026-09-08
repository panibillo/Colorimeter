#!/usr/bin/env python3
"""
Test different gap interpolation thresholds to find the right one.
"""
import sys
from pathlib import Path
import numpy as np
import cv2

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bayer_cam import BayerCam

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

def interpolate_lines(positions, max_gap_multiplier):
    """Interpolate missing lines."""
    if len(positions) < 2:
        return positions
    
    gaps = [positions[i+1] - positions[i] for i in range(len(positions)-1)]
    median_gap = np.median(gaps)
    
    new_positions = [positions[0]]
    interpolated_count = 0
    
    for i in range(len(positions) - 1):
        gap = positions[i+1] - positions[i]
        
        if gap > median_gap * max_gap_multiplier:
            num_missing = max(1, int(round(gap / median_gap - 1)))
            
            for j in range(num_missing):
                interpolated = int(positions[i] + (j+1) * gap / (num_missing + 1))
                new_positions.append(interpolated)
                interpolated_count += 1
        
        new_positions.append(positions[i+1])
    
    return sorted(new_positions), interpolated_count

h_positions = cluster_h_lines(lines)
gaps = [h_positions[i+1] - h_positions[i] for i in range(len(h_positions)-1)]
median_gap = np.median(gaps)

print("="*70)
print("BASELINE DETECTION")
print("="*70)
print(f"Horizontal lines: {len(h_positions)}")
print(f"Positions: {h_positions}")
print(f"Gaps: {[int(g) for g in gaps]}")
print(f"Median gap: {median_gap:.1f}")
print()

# Find anomalous gaps
anomalies = []
for i, gap in enumerate(gaps):
    ratio = gap / median_gap
    if ratio > 1.5:
        anomalies.append((i, gap, ratio))
        print(f"Gap {i} ({h_positions[i]} → {h_positions[i+1]}): {gap:.1f} ({ratio:.2f}x median)")

print("\n" + "="*70)
print("INTERPOLATION WITH DIFFERENT THRESHOLDS")
print("="*70)

for multiplier in [1.3, 1.5, 1.8, 2.0, 2.5]:
    new_h, added = interpolate_lines(h_positions, multiplier)
    total_rows = len(new_h) - 1
    print(f"Multiplier {multiplier:.1f}: {len(h_positions):2} → {len(new_h):2} lines "
          f"(+{added} interpolated) → {total_rows:2} rows")

print("\n" + "="*70)
print("RECOMMENDATION")
print("="*70)
print(f"""
Based on the gap analysis:
- Expected row height (normal gap): {median_gap:.0f} pixels
- Anomalous gaps that suggest split rows: {[f"{g:.0f}px" for _, g, _ in anomalies]}

For a checkerboard with 6-7 interior rows (after margin filtering):
- We expect ~6-7 horizontal lines or ~7 detected grid lines if we include margins
- Current detection finds {len(h_positions)} lines → {len(h_positions)-1} rows

The split row in the noisy region [550:700, 112:187] likely manifests as
ONE anomalously large gap (2x-3x the median).

Recommendation: Use multiplier=1.8
- Catches 2x+ gaps (nearly certain split rows)
- Avoids over-interpolating normal variation
- For split row: 1.8 * {median_gap:.0f} = {1.8*median_gap:.0f} threshold
""")

# Show what 1.8 would do
new_h_18, added_18 = interpolate_lines(h_positions, 1.8)
print(f"With multiplier=1.8: Adds {added_18} lines, total={len(new_h_18)}")
