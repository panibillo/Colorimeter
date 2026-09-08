#!/usr/bin/env python3
"""
Analyze noise patterns and edge detection issues in specific image regions.
"""
import sys
from pathlib import Path
import numpy as np
import cv2

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bayer_cam import BayerCam
import logging_manager as log

# Load test image
bayer = np.load("mock_camera/cfg5_sbggr1_raw_00checkerB.npy")
H, W = bayer.shape
print(f"Bayer array shape: {H} x {W}")

# Extract channels
channels = BayerCam.extract_bayer_layers(bayer)
channel_stdevs = {name: float(np.std(channel)) for name, channel in channels.items()}
best_channel = max(channel_stdevs, key=channel_stdevs.get)
print(f"Channel stdevs: {channel_stdevs}")
print(f"Using '{best_channel}' with stdev={channel_stdevs[best_channel]:.1f}")

# Normalize to 0-255
channel_data = channels[best_channel].astype(np.float32)
channel_normalized = ((channel_data - channel_data.min()) / 
                      (channel_data.max() - channel_data.min() + 1e-6) * 255).astype(np.uint8)

layer_h, layer_w = channel_normalized.shape
print(f"Layer (normalized) shape: {layer_h} x {layer_w}")

# Apply Gaussian blur
blurred = cv2.GaussianBlur(channel_normalized, (5, 5), 1.0)

# Apply Canny edge detection with current parameters
edges_current = cv2.Canny(blurred, threshold1=30, threshold2=80)

# Try alternative parameters
edges_high = cv2.Canny(blurred, threshold1=50, threshold2=120)   # Stricter
edges_low = cv2.Canny(blurred, threshold1=20, threshold2=60)     # More sensitive
edges_bilateral = cv2.Canny(
    cv2.bilateralFilter(channel_normalized, 5, 75, 75),
    threshold1=30, threshold2=80
)

print(f"\nEdge detection results (on layer {layer_h}x{layer_w}):")
print(f"  Current  (30, 80):     {np.count_nonzero(edges_current)} edge pixels")
print(f"  Stricter (50, 120):    {np.count_nonzero(edges_high)} edge pixels")
print(f"  Sensitive(20, 60):     {np.count_nonzero(edges_low)} edge pixels")
print(f"  Bilateral+Canny:       {np.count_nonzero(edges_bilateral)} edge pixels")

# Analyze the noisy region (user reported: [1100:1400, 225:375] in full Bayer)
# In layer space: [550:700, 112:187]
print(f"\n=== Analyzing noisy region ===")
print(f"User region (full Bayer): [1100:1400, 225:375]")
print(f"Equivalent in layer: [{1100//2}:{1400//2}, {225//2}:{375//2}] = [550:700, 112:187]")

y_start, y_end = 550, 700
x_start, x_end = 112, 187

region_current = edges_current[y_start:y_end, x_start:x_end]
region_high = edges_high[y_start:y_end, x_start:x_end]
region_low = edges_low[y_start:y_end, x_start:x_end]

print(f"\nEdges in region [{y_start}:{y_end}, {x_start}:{x_end}]:")
print(f"  Current (30, 80):   {np.count_nonzero(region_current)} edge pixels")
print(f"  Stricter (50, 120):  {np.count_nonzero(region_high)} edge pixels")
print(f"  Sensitive (20, 60):  {np.count_nonzero(region_low)} edge pixels")

# Calculate local statistics
channel_region = channel_normalized[y_start:y_end, x_start:x_end]
print(f"\nFull channel statistics (entire layer):")
print(f"  Mean: {np.mean(channel_normalized):.1f}, Std: {np.std(channel_normalized):.1f}")
print(f"  Min: {np.min(channel_normalized)}, Max: {np.max(channel_normalized)}")

print(f"\nNoisy region statistics [{y_start}:{y_end}, {x_start}:{x_end}]:")
print(f"  Mean: {np.mean(channel_region):.1f}, Std: {np.std(channel_region):.1f}")
print(f"  Min: {np.min(channel_region)}, Max: {np.max(channel_region)}")
print(f"  Contrast ratio: {np.std(channel_region) / np.std(channel_normalized):.2f}x")

# Detect lines with Hough on each edge map
print(f"\n=== Hough line detection (threshold=50, minLen=50, maxGap=10) ===")

for name, edges in [("current", edges_current), ("high", edges_high), 
                     ("low", edges_low), ("bilateral", edges_bilateral)]:
    lines = cv2.HoughLinesP(edges, rho=1, theta=np.pi/180, threshold=50,
                            minLineLength=50, maxLineGap=10)
    if lines is None:
        num_lines = 0
    else:
        num_lines = len(lines)
    print(f"  {name:12}: {num_lines} line segments")

# Detect objects of interest
print(f"\n=== Morphological analysis ===")
kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))

# Close small gaps in edges
edges_closed = cv2.morphologyEx(edges_current, cv2.MORPH_CLOSE, kernel, iterations=2)
print(f"After morphology close: {np.count_nonzero(edges_closed)} edge pixels")

# Find contours in the noisy region
contours, hierarchy = cv2.findContours(region_current, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
print(f"Contours in noisy region: {len(contours)}")
for i, cnt in enumerate(contours[:5]):
    print(f"  Contour {i}: area={cv2.contourArea(cnt):.1f}, perimeter={cv2.arcLength(cnt, True):.1f}")

print("\n" + "="*60)
print("RECOMMENDATIONS for handling noise:")
print("="*60)
print("""
1. BILATERAL FILTERING (preserve edges, reduce noise)
   - Apply cv2.bilateralFilter() before Canny
   - Effective for smooth regions with sharp edges
   - Reduces false edges in noisy areas

2. ADAPTIVE THRESHOLDING by region
   - Calculate local contrast statistics
   - Adjust Canny thresholds based on local std dev
   - High noise → stricter thresholds (higher threshold values)

3. MORPHOLOGICAL POST-PROCESSING
   - Use cv2.morphologyEx() to close small gaps
   - Use cv2.morphologyEx() to remove small isolated pixels
   - Bridge split lines in noisy regions

4. HOUGH LINE PARAMETER TUNING
   - Increase threshold (more evidence needed for line)
   - But may miss legitimate faint lines
   - Increase minLineLength to reject short noise fragments

5. EDGE QUALITY DETECTION
   - Estimate edge confidence from gradient magnitude
   - Reject edges in high-noise regions
   - Use Sobel gradients to detect noise patterns

6. CLAHE (Contrast Limited Adaptive Histogram Equalization)
   - cv2.createCLAHE() normalizes local contrast
   - Reduces impact of varying lighting
   - Often improves edge consistency
""")
