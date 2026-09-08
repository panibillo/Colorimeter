#!/usr/bin/env python3
"""
Test if CLAHE + bilateral filtering fixes the missing row issue.
"""
import sys
from pathlib import Path
import numpy as np
import cv2

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bayer_cam import BayerCam
import logging_manager as log

log.set_level("INFO")

# Load test image
bayer = np.load("mock_camera/cfg5_sbggr1_raw_00checkerB.npy")
print(f"Loaded Bayer array: {bayer.shape}")

# Extract best channel
channels = BayerCam.extract_bayer_layers(bayer)
channel_stdevs = {name: float(np.std(channel)) for name, channel in channels.items()}
best_channel = max(channel_stdevs, key=channel_stdevs.get)
channel_data = channels[best_channel].astype(np.float32)
channel_normalized = ((channel_data - channel_data.min()) / 
                      (channel_data.max() - channel_data.min() + 1e-6) * 255).astype(np.uint8)

print(f"\nChannel: {best_channel}, Normalized shape: {channel_normalized.shape}")

# Compare old and new preprocessing
print("\n" + "="*60)
print("OLD METHOD (Gaussian blur only)")
print("="*60)

blurred_old = cv2.GaussianBlur(channel_normalized, (5, 5), 1.0)
edges_old = cv2.Canny(blurred_old, threshold1=30, threshold2=80)
lines_old = cv2.HoughLinesP(edges_old, rho=1, theta=np.pi/180, threshold=50,
                            minLineLength=50, maxLineGap=10)
print(f"Edge pixels: {np.count_nonzero(edges_old)}")
print(f"Line segments detected: {len(lines_old) if lines_old is not None else 0}")

# Cluster horizontal lines
def cluster_positions(lines, is_horizontal=True):
    if lines is None or len(lines) == 0:
        return []
    positions = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if is_horizontal:
            positions.append((y1 + y2) / 2)
        else:
            positions.append((x1 + x2) / 2)
    
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

h_old = cluster_positions(lines_old, is_horizontal=True)
print(f"Horizontal grid lines (old): {len(h_old)}")
print(f"  Positions: {h_old}")

print("\n" + "="*60)
print("NEW METHOD (CLAHE + bilateral filter)")
print("="*60)

# Apply CLAHE
clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
enhanced = clahe.apply(channel_normalized)

# Apply bilateral filtering
blurred_new = cv2.bilateralFilter(enhanced, d=7, sigmaColor=75, sigmaSpace=75)
edges_new = cv2.Canny(blurred_new, threshold1=30, threshold2=80)
lines_new = cv2.HoughLinesP(edges_new, rho=1, theta=np.pi/180, threshold=50,
                            minLineLength=50, maxLineGap=10)
print(f"Edge pixels: {np.count_nonzero(edges_new)}")
print(f"Line segments detected: {len(lines_new) if lines_new is not None else 0}")

h_new = cluster_positions(lines_new, is_horizontal=True)
print(f"Horizontal grid lines (new): {len(h_new)}")
print(f"  Positions: {h_new}")

print("\n" + "="*60)
print("COMPARISON")
print("="*60)
print(f"Edge pixels:    {np.count_nonzero(edges_old):4} → {np.count_nonzero(edges_new):4} "
      f"({100*(np.count_nonzero(edges_new) / np.count_nonzero(edges_old) - 1):+.1f}%)")
print(f"Line segments:  {len(lines_old) if lines_old is not None else 0:4} → "
      f"{len(lines_new) if lines_new is not None else 0:4}")
print(f"Grid lines H:   {len(h_old):4} → {len(h_new):4}")

if len(h_new) > len(h_old):
    print(f"\n✓ SUCCESS: Added {len(h_new) - len(h_old)} horizontal grid lines!")
elif len(h_new) == len(h_old):
    print(f"\n~ No change: Same number of lines detected")
else:
    print(f"\n✗ REGRESSION: Lost {len(h_old) - len(h_new)} grid lines!")
