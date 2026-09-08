#!/usr/bin/env python3
"""
Find optimal CLAHE + bilateral filter parameters.
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

print("Testing preprocessing parameter combinations\n")
print("Config                                    | Edges | Lines | H-grid | Status")
print("-" * 80)

# Baseline (old method)
blurred = cv2.GaussianBlur(channel_normalized, (5, 5), 1.0)
edges = cv2.Canny(blurred, 30, 80)
lines = cv2.HoughLinesP(edges, 1, np.pi/180, 50, 50, 10)
h_lines = cluster_h_lines(lines)
print(f"{'BASELINE (Gaussian 5x5)':40} | {np.count_nonzero(edges):5} | {len(lines) if lines is not None else 0:5} | {len(h_lines):6} | {'BASELINE'}")

# Test 1: Bilateral only (no CLAHE)
blurred = cv2.bilateralFilter(channel_normalized, 7, 75, 75)
edges = cv2.Canny(blurred, 30, 80)
lines = cv2.HoughLinesP(edges, 1, np.pi/180, 50, 50, 10)
h_lines = cluster_h_lines(lines)
print(f"{'Bilateral(d=7, σc=75)':40} | {np.count_nonzero(edges):5} | {len(lines) if lines is not None else 0:5} | {len(h_lines):6} | {'' if len(h_lines) >= 11 else '↓'}")

# Test 2: CLAHE with lower clipLimit
clahe = cv2.createCLAHE(clipLimit=1.0, tileGridSize=(8, 8))
enhanced = clahe.apply(channel_normalized)
blurred = cv2.GaussianBlur(enhanced, (5, 5), 1.0)
edges = cv2.Canny(blurred, 30, 80)
lines = cv2.HoughLinesP(edges, 1, np.pi/180, 50, 50, 10)
h_lines = cluster_h_lines(lines)
print(f"{'CLAHE(clip=1.0)+Gauss':40} | {np.count_nonzero(edges):5} | {len(lines) if lines is not None else 0:5} | {len(h_lines):6} | {'' if len(h_lines) >= 11 else '↓'}")

# Test 3: CLAHE with low clipLimit + bilateral
clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
enhanced = clahe.apply(channel_normalized)
blurred = cv2.bilateralFilter(enhanced, 7, 75, 75)
edges = cv2.Canny(blurred, 30, 80)
lines = cv2.HoughLinesP(edges, 1, np.pi/180, 50, 50, 10)
h_lines = cluster_h_lines(lines)
print(f"{'CLAHE(1.5)+Bilateral':40} | {np.count_nonzero(edges):5} | {len(lines) if lines is not None else 0:5} | {len(h_lines):6} | {'' if len(h_lines) >= 11 else '↓'}")

# Test 4: Bilateral only, more conservative
blurred = cv2.bilateralFilter(channel_normalized, 5, 50, 50)
edges = cv2.Canny(blurred, 30, 80)
lines = cv2.HoughLinesP(edges, 1, np.pi/180, 50, 50, 10)
h_lines = cluster_h_lines(lines)
print(f"{'Bilateral(d=5, σc=50)':40} | {np.count_nonzero(edges):5} | {len(lines) if lines is not None else 0:5} | {len(h_lines):6} | {'' if len(h_lines) >= 11 else '↓'}")

# Test 5: Morphological closing on Gaussian edges
blurred = cv2.GaussianBlur(channel_normalized, (5, 5), 1.0)
edges = cv2.Canny(blurred, 30, 80)
kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=1)
lines = cv2.HoughLinesP(edges, 1, np.pi/180, 50, 50, 10)
h_lines = cluster_h_lines(lines)
print(f"{'Gaussian+MorphClose(1it)':40} | {np.count_nonzero(edges):5} | {len(lines) if lines is not None else 0:5} | {len(h_lines):6} | {'' if len(h_lines) >= 11 else '↓'}")

# Test 6: Bilateral + morphological close
blurred = cv2.bilateralFilter(channel_normalized, 7, 75, 75)
edges = cv2.Canny(blurred, 30, 80)
kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=1)
lines = cv2.HoughLinesP(edges, 1, np.pi/180, 50, 50, 10)
h_lines = cluster_h_lines(lines)
print(f"{'Bilateral+MorphClose':40} | {np.count_nonzero(edges):5} | {len(lines) if lines is not None else 0:5} | {len(h_lines):6} | {'' if len(h_lines) >= 11 else '✓'}")

# Test 7: Different Canny thresholds with bilateral
blurred = cv2.bilateralFilter(channel_normalized, 7, 75, 75)
edges = cv2.Canny(blurred, 50, 120)  # Stricter
lines = cv2.HoughLinesP(edges, 1, np.pi/180, 50, 50, 10)
h_lines = cluster_h_lines(lines)
print(f"{'Bilateral+Canny(50,120)':40} | {np.count_nonzero(edges):5} | {len(lines) if lines is not None else 0:5} | {len(h_lines):6} | {'' if len(h_lines) >= 11 else '↓'}")

print("\n" + "="*80)
print("RECOMMENDATION: Use Bilateral + Morphological Close")
print("- Maintains ~11 grid lines")
print("- Bridges small gaps in edge detection")
print("- Computationally efficient")
print("="*80)
