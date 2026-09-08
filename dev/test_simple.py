#!/usr/bin/env python3
"""
Test the simplified conservative approach: Bilateral filter + stricter Canny.
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

print("="*70)
print("EVALUATING PREPROCESSING STRATEGY")
print("="*70)

configs = [
    ("BASELINE: Gaussian blur (30,80)", 
     lambda img: cv2.GaussianBlur(img, (5, 5), 1.0),
     (30, 80)),
    
    ("CANDIDATE: Bilateral(d=5, σ=50) + Canny(40,100)",
     lambda img: cv2.bilateralFilter(img, 5, 50, 50),
     (40, 100)),
    
    ("CANDIDATE: Bilateral(d=7, σ=75) + Canny(40,100)",
     lambda img: cv2.bilateralFilter(img, 7, 75, 75),
     (40, 100)),
    
    ("CANDIDATE: Bilateral(d=5, σ=50) + Canny(30,80)",
     lambda img: cv2.bilateralFilter(img, 5, 50, 50),
     (30, 80)),
]

results = []

for name, preprocess_fn, (t1, t2) in configs:
    blurred = preprocess_fn(channel_normalized)
    edges = cv2.Canny(blurred, t1, t2)
    lines = cv2.HoughLinesP(edges, 1, np.pi/180, 50, 50, 10)
    h_lines = cluster_h_lines(lines)
    num_lines = len(lines) if lines is not None else 0
    
    results.append({
        'name': name,
        'edges': np.count_nonzero(edges),
        'lines': num_lines,
        'h_grid': len(h_lines),
        'h_positions': h_lines
    })
    
    print(f"\n{name}")
    print(f"  Edge pixels:  {np.count_nonzero(edges):6}")
    print(f"  Line segments: {num_lines:6}")
    print(f"  H-grid lines: {len(h_lines):6}")
    print(f"  Positions:    {h_lines}")

# Analyze noisy region [550:700, 112:187]
print("\n" + "="*70)
print("NOISE REGION ANALYSIS")
print("="*70)

blurred_baseline = cv2.GaussianBlur(channel_normalized, (5, 5), 1.0)
edges_baseline = cv2.Canny(blurred_baseline, 30, 80)

blurred_candidate = cv2.bilateralFilter(channel_normalized, 5, 50, 50)
edges_candidate = cv2.Canny(blurred_candidate, 40, 100)

region_base = edges_baseline[550:700, 112:187]
region_cand = edges_candidate[550:700, 112:187]

print(f"Region [550:700, 112:187] edge detection:")
print(f"  Baseline:  {np.count_nonzero(region_base):4} pixels")
print(f"  Candidate: {np.count_nonzero(region_cand):4} pixels")
print(f"  Improvement: {100*(np.count_nonzero(region_cand) / max(1, np.count_nonzero(region_base)) - 1):+.1f}%")

print("\n" + "="*70)
print("RECOMMENDATION")
print("="*70)
print("""
The gap interpolation logic will help recover missing grid lines in low-contrast
regions. Combined with bilateral filtering (which improves edge preservation),
we should detect split rows and interpolate the missing horizontal line.

The strategy:
1. Use bilateral filter (d=5, σ=50) - conservative, minimal artifacts
2. Keep Canny thresholds at (40, 100) - reduces false positives
3. Rely on gap interpolation to recover split rows in high-noise regions

This approach balances noise reduction with grid fidelity.
""")
