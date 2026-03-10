#!/usr/bin/env python3
"""
camtest4.py - Comprehensive Mock Camera Test Suite Generator

Captures test images for all 6 configurations and saves all 3 file types:
  - RGB: .jpg (or .png for 16-bit)
  - Raw Bayer: .npy (uint16, BBBR 10-bit)
  - Metadata: .txt (key: value format)

Configuration coverage:
  Config 1-3: Single-stream RGB only
  Config 4-6: Dual-stream RGB + Bayer

Output directory structure:
  output_dir/
  ├── cfg1_rgb888_00.jpg
  ├── cfg1_rgb888_00.txt
  ├── cfg2_rgb888_00.jpg
  ├── cfg2_rgb888_00.txt
  ├── cfg3_rgb888_00.jpg
  ├── cfg3_rgb888_00.txt
  ├── cfg4_sbggr1_rgb_00.jpg
  ├── cfg4_sbggr1_raw_00.npy
  ├── cfg4_sbggr1_raw_00.txt
  ├── cfg5_sbggr1_rgb_00.jpg
  ├── cfg5_sbggr1_raw_00.npy
  ├── cfg5_sbggr1_raw_00.txt
  ├── cfg6_sbggr1_rgb_00.jpg
  ├── cfg6_sbggr1_raw_00.npy
  └── cfg6_sbggr1_raw_00.txt

Usage:
  python camtest4.py [output_dir]

  Press Enter when ready to start capturing
  Script will automatically loop through all 6 configurations
  (optional) Ctrl-C to abort
"""

import time
import signal
import sys
from pathlib import Path

try:
    from picamera2 import Picamera2
    import libcamera
    import cv2
except ImportError:
    print("Loading mock_Picamera2 (desktop testing)")
    from mock_Picamera2 import Picamera2, libcamera
    import cv2

import numpy as np


# ---------------------------------------------------------------------------
# Camera configurations (same as camtest2.py)
# ---------------------------------------------------------------------------
CONFIGS = {
    # '1': {
    #     "size":    (640, 480),
    #     "format":  "RGB888",
    #     "is_raw":  False,
    #     "comment": "Config 1: Small RGB (640×480)"
    # },
    # '2': {
    #     "size":    (1640, 1232),
    #     "format":  "RGB888",
    #     "is_raw":  False,
    #     "comment": "Config 2: Medium RGB (1640×1232)"
    # },
    # '3': {
    #     "size":    (3280, 2464),
    #     "format":  "RGB888",
    #     "is_raw":  False,
    #     "comment": "Config 3: Full RGB (3280×2464)"
    # },
    # '4': {
    #     "size":    (640, 480),
    #     "format":  "SBGGR10_CSI2P",
    #     "is_raw":  True,
    #     "comment": "Config 4: Small dual-stream RGB + Bayer (640×480)"
    # },
    '5': {
        "size":    (1640, 1232),
        "format":  "SBGGR10_CSI2P",
        "is_raw":  True,
        "comment": "Config 5: Medium dual-stream RGB + Bayer (1640×1232)"
    },
    # '6': {
    #     "size":    (3280, 2464),
    #     "format":  "SBGGR10_CSI2P",
    #     "is_raw":  True,
    #     "comment": "Config 6: Full dual-stream RGB + Bayer (3280×2464)"
    # },
}

# Camera control settings
EXPOSURE_TIME_US = 100_000
ANALOGUE_GAIN = 1.0
WB_GAINS_RB = (1.0, 1.0)


def build_and_configure_camera(config_key):
    """
    Create and configure camera for specified config.
    
    Args:
        config_key: "1" through "6"
    
    Returns:
        Picamera2 instance (started and ready)
    """
    cfg = CONFIGS[config_key]
    picam2 = Picamera2()
    
    print(f"  Configuring camera: {cfg['comment']}")
    
    if cfg['is_raw']:
        # Dual-stream: RGB main + Bayer raw
        config = picam2.create_still_configuration(
            main={
                "size":   cfg["size"],
                "format": "RGB888",
            },
            raw={
                "size":   cfg["size"],
                "format": cfg["format"],
            },
            buffer_count=1,
        )
    else:
        # Single-stream: RGB only
        config = picam2.create_still_configuration(
            main={
                "size":   cfg["size"],
                "format": cfg["format"],
            },
            buffer_count=1,
        )
    
    picam2.configure(config)
    
    # Set controls (apply to all configs)
    picam2.set_controls({
        "AeEnable":       False,
        "ExposureTime":   EXPOSURE_TIME_US,
        "AnalogueGain":   ANALOGUE_GAIN,
        "AwbEnable":      False,
        "ColourGains":    WB_GAINS_RB,
        "NoiseReductionMode": libcamera.controls.draft.NoiseReductionModeEnum.Off,
        "Sharpness":      0.0,
        "Contrast":       1.0,
        "Saturation":     1.0,
    })
    
    picam2.start()
    time.sleep(1)  # Allow stabilization
    
    return picam2

def capture_config(picam2, config_key, output_dir):
    """
    Capture and save all file types for a single configuration.
    
    Args:
        picam2: Picamera2 instance (started)
        config_key: "1" through "6"
        output_dir: Path to save files
    
    Returns:
        dict with file paths saved
    """
    cfg = CONFIGS[config_key]
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"  Capturing config {config_key}...")
    
    # Determine file basename
    if cfg['is_raw']:
        basename = f"cfg{config_key}_sbggr1"
    else:
        basename = f"cfg{config_key}_rgb888"
    
    files = {}
    
    if cfg['is_raw']:
        print("Dual-stream capture")
        request = picam2.capture_request()
        rgb8 = request.make_array("main")      # uint8 H×W×3 BGR
        bayer = request.make_array("raw")      # uint8 H×800 packed 10-bit Bayer
        meta = request.get_metadata()
        request.release()
        
        print(f"\n  RGB Stream Analysis:")
        print(f"    Shape: {rgb8.shape} (H×W×3)")
        print(f"    B mean: {rgb8[:,:,0].mean():.1f}, G mean: {rgb8[:,:,1].mean():.1f}, R mean: {rgb8[:,:,2].mean():.1f}")
        
        print(f"\n  Raw Bayer Stream (before unpacking):")
        print(f"    Shape: {bayer.shape} (H×packed_W)")
        print(f"    Dtype: {bayer.dtype}")
        
        print("\nSkipping unpacking on RPi (too slow).")
        print(f"    Packed data will be saved as-is: {bayer.shape} uint8")
        print(f"    (Unpacking will happen on desktop for analysis)")
        
        print(f"\n  Raw Bayer Stream (after unpacking):")
        print(f"    Shape: {bayer.shape}")
        print(f"    Dtype: {bayer.dtype}")
        
        print("Save RGB as JPG")
        rgb_file = out_dir / f"{basename}_rgb_00.jpg"
        cv2.imwrite(str(rgb_file), rgb8)
        files['rgb'] = rgb_file
        print(f"    ✓ RGB:      {rgb_file.name} ({rgb8.shape})")
        
        print("Save Bayer as NPY")
        raw_file = out_dir / f"{basename}_raw_00.npy"
        np.save(str(raw_file), bayer)
        files['raw'] = raw_file
        print(f"    ✓ Bayer:    {raw_file.name} ({bayer.shape}, {bayer.dtype})")
        
        # Save metadata
        meta_file = out_dir / f"{basename}_raw_00.txt"
        _save_metadata(meta_file, meta)
        files['meta'] = meta_file
        print(f"    ✓ Metadata: {meta_file.name}")
    
    else:
        # Single-stream capture (RGB only)
        rgb8 = picam2.capture_array()  # uint8 H×W×3 BGR
        meta = picam2.capture_metadata()
        
        # Save RGB as JPG
        rgb_file = out_dir / f"{basename}_00.jpg"
        cv2.imwrite(str(rgb_file), rgb8)
        files['rgb'] = rgb_file
        print(f"    ✓ RGB:      {rgb_file.name} ({rgb8.shape})")
        
        # Save metadata
        meta_file = out_dir / f"{basename}_00.txt"
        _save_metadata(meta_file, meta)
        files['meta'] = meta_file
        print(f"    ✓ Metadata: {meta_file.name}")
    
    return files

def check_Bayer_packed(packed_data, width):
    """
    Diagnostic: examine packed SBGGR10_CSI2P data before unpacking.
    """
    print(f"\n  ╔═══ PACKED DATA DIAGNOSTIC ═══╗")
    print(f"    dtype: {packed_data.dtype}")
    print(f"    shape: {packed_data.shape}  (H={packed_data.shape[0]}, packed_W={packed_data.shape[1]})")
    packed_bytes_expected = (width * 10 + 7) // 8
    print(f"    Expected: ~{packed_bytes_expected} bytes per row ({width} pixels × 10 bits / 8)")
    print(f"    Actual:   {packed_data.shape[1]} bytes per row")
    
    # Sample bytes from middle region where blue pixel should be bright
    row, col = min(300, packed_data.shape[0]//2), 10   
    print(f"\n    Sample region (row {row}, packed cols {col}-{col+4}):")
    sample_bytes = packed_data[row, col:col+5]
    for i, b in enumerate(sample_bytes):
        print(f"      byte[{col+i}]: {b:3d} = {b:08b}")
    
    print(f"\n    Data range: min={packed_data.min()}, max={packed_data.max()}")
    print(f"  ╚════════════════════════════╝")


def check_Bayer_unpacked(unpacked_data):
    """
    Diagnostic: examine unpacked 10-bit Bayer data after unpacking.
    """
    print(f"\n  ╔═══ UNPACKED DATA DIAGNOSTIC ═══╗")
    print(f"    dtype: {unpacked_data.dtype}")
    print(f"    shape: {unpacked_data.shape}  (H={unpacked_data.shape[0]}, W={unpacked_data.shape[1]})")
    print(f"    Expected: 640 pixels per row, uint16, values 0-1023")
    
    # Sample bytes from middle region where blue pixel should be bright
    row, col = 300, 10
    print(f"\n    Sample region (row {row}, unpacked cols {col}-{col+9}):")
    sample_pixels = unpacked_data[row, col:col+5]
    for i, p in enumerate(sample_pixels):
        print(f"      pixel[{col+i}]: {p:4d} = {p:010b}")
    
    # Extract BGGR layers to check Blue channel
    b_layer = unpacked_data[0::2, 0::2]     # Blue pixels
    gb_layer = unpacked_data[0::2, 1::2]    # Gb pixels
    gr_layer = unpacked_data[1::2, 0::2]    # Gr pixels
    r_layer = unpacked_data[1::2, 1::2]     # Red pixels
    
    print(f"\n    BGGR Layer Analysis:")
    print(f"      B  layer:  mean={b_layer.mean():7.1f}, min={b_layer.min():4d}, max={b_layer.max():4d}")
    print(f"      Gb layer:  mean={gb_layer.mean():7.1f}, min={gb_layer.min():4d}, max={gb_layer.max():4d}")
    print(f"      Gr layer:  mean={gr_layer.mean():7.1f}, min={gr_layer.min():4d}, max={gr_layer.max():4d}")
    print(f"      R  layer:  mean={r_layer.mean():7.1f}, min={r_layer.min():4d}, max={r_layer.max():4d}")
    
    print(f"\n    Data range: min={unpacked_data.min()}, max={unpacked_data.max()}")
    print(f"    ✓ Valid 10-bit range [0-1023] expected; actual {unpacked_data.min()}-{unpacked_data.max()}")
    print(f"  ╚═════════════════════════════════╝")

def _unpack_sbggr10_csi2p(packed_data, width):
    """
    Unpack SBGGR10_CSI2P packed 10-bit Bayer data to uint16.
    
    Picamera2 raw stream returns SBGGR10_CSI2P as packed bytes:
    - width pixels × 10 bits = width*10 bits = (width*10+7)//8 bytes per row
    - Saved as (H, packed_W) uint8
    
    This function unpacks to proper (H, width) uint16 with values 0-1023.
    
    CSI2P packing (5 bytes → 4 pixels):
        Pixel 0: bits [9:2] in byte 0, bits [1:0] in byte 1 [7:6]
        Pixel 1: bits [5:0] in byte 1, bits [9:8] in byte 2 [7:6]
        Pixel 2: bits [3:0] in byte 2, bits [9:6] in byte 3 [7:4]
        Pixel 3: bits [1:0] in byte 3, bits [9:0] in byte 4
    
    Args:
        packed_data: numpy array (H, packed_W) uint8 - packed 10-bit Bayer data
        width: Expected number of pixels per row (e.g., 640, 1640, 3280)
    
    Returns:
        numpy array (H, width) uint16 - unpacked 10-bit values
    """
    height = packed_data.shape[0]
    unpacked = np.zeros((height, width), dtype=np.uint16)

    print("\n  [DIAGNOSTICS] BEFORE unpacking:")
    check_Bayer_packed(packed_data, width) 

    for row in range(height):
        packed_row = packed_data[row]
        
        # Unpack 5 bytes → 4 pixels (40 bits = 4×10 bits)
        pixel_idx = 0
        for byte_idx in range(0, len(packed_row), 5):
            if byte_idx + 4 < len(packed_row):
                b0, b1, b2, b3, b4 = packed_row[byte_idx:byte_idx+5].astype(np.uint16)
            else:
                # Handle edge case if data is not byte-aligned
                b0 = packed_row[byte_idx] if byte_idx < len(packed_row) else 0
                b1 = packed_row[byte_idx+1] if byte_idx+1 < len(packed_row) else 0
                b2 = packed_row[byte_idx+2] if byte_idx+2 < len(packed_row) else 0
                b3 = packed_row[byte_idx+3] if byte_idx+3 < len(packed_row) else 0
                b4 = packed_row[byte_idx+4] if byte_idx+4 < len(packed_row) else 0
            
            # Extract 4 10-bit values from 5 bytes (CSI2P packing)
            p0 = ((b0 << 2) | (b1 >> 6)) & 0x3FF
            p1 = ((b1 << 4) | (b2 >> 4)) & 0x3FF
            p2 = ((b2 << 6) | (b3 >> 2)) & 0x3FF
            p3 = ((b3 << 8) | b4) & 0x3FF
            
            # Store unpacked pixels (but don't exceed width)
            if pixel_idx < width:
                unpacked[row, pixel_idx] = p0
            if pixel_idx + 1 < width:
                unpacked[row, pixel_idx + 1] = p1
            if pixel_idx + 2 < width:
                unpacked[row, pixel_idx + 2] = p2
            if pixel_idx + 3 < width:
                unpacked[row, pixel_idx + 3] = p3
            
            pixel_idx += 4
    
    print("\n  [DIAGNOSTICS] AFTER unpacking:")
    check_Bayer_unpacked(unpacked)
    return unpacked


def _save_metadata(fpath, meta):
    """
    Save metadata dict to text file (key: value format).
    
    Args:
        fpath: Path object or string
        meta: dict of metadata
    """
    with open(fpath, 'w') as f:
        for key, val in meta.items():
            f.write(f"{key}: {val}\n")


def estimate_exposure(picam2, target_fraction=0.70, reference_channel="B",
                      min_exposure_us=1_000, max_exposure_us=900_000):
    """
    Fire a test capture at the current ExposureTime and calculate the
    exposure needed to bring the reference channel to target_fraction
    of full scale.

    Args:
        picam2: Picamera2 instance (started and ready)
        target_fraction: Target fill fraction (0.0-1.0), default 0.70
        reference_channel: "B", "G", or "R", default "B" (blue)
        max_exposure_us: Maximum allowed exposure in microseconds, default 900_000

    Returns:
        dict with exposure estimation results
    """
    # Read what the camera is currently set to
    current_meta = picam2.capture_metadata()
    current_exposure = current_meta["ExposureTime"]

    # Capture a test frame
    bgr = picam2.capture_array()

    ch_idx = {"B": 0, "G": 1, "R": 2}[reference_channel]
    current_mean = float(bgr[:, :, ch_idx].mean())
    target_mean = target_fraction * 255.0

    if current_mean < 1.0:
        print("    WARNING: channel mean < 1.0 — image is essentially black. "
              "Check illumination and lens cap.")
        return {
            "config": None,
            "current_exposure": current_exposure,
            "recommended_exposure": current_exposure,
            "reference_channel": reference_channel,
            "current_mean": current_mean,
            "target_mean": target_mean,
            "scale_factor": 1.0,
        }

    scale_factor = target_mean / current_mean
    recommended_us = int(current_exposure * scale_factor)
    print(    f"         Exp recommended_us  {recommended_us:,}")
    if recommended_us >= max_exposure_us:
        print(f"      ⚠  Exposure clamped to {max_exposure_us:,} µs — illumination may be insufficient")
    if recommended_us <= min_exposure_us:
        print(f"      ⚠  Exposure clamped to {min_exposure_us:,} µs — illumination may too bright")

    recommended_us = max( min(recommended_us, max_exposure_us), min_exposure_us)

    # Report
    bgr_means = {
        "B": float(bgr[:, :, 0].mean()),
        "G": float(bgr[:, :, 1].mean()),
        "R": float(bgr[:, :, 2].mean()),
    }

    print(f"    Exposure estimation:")
    print(f"      Blue  mean:   {bgr_means['B']:5.1f} / 255")
    print(f"      Green mean:   {bgr_means['G']:5.1f} / 255")
    print(f"      Red   mean:   {bgr_means['R']:5.1f} / 255")
    print(f"      Current exposure:     {current_exposure:,} µs")
    print(f"      {reference_channel}-channel target: {target_mean:.1f} / 255 ({target_fraction*100:.0f}%)")
    print(f"      Scale factor:         {scale_factor:.3f}×")
    print(f"      Recommended exposure: {recommended_us:,} µs ({recommended_us/1e6:.3f} s)")


    return {
        "current_exposure": current_exposure,
        "recommended_exposure": recommended_us,
        "reference_channel": reference_channel,
        "channel_means": bgr_means,
        "current_mean": current_mean,
        "target_mean": target_mean,
        "scale_factor": scale_factor,
        "clamped": recommended_us >= max_exposure_us,
    }


def main():
    """
    Main capture loop.
    
    Flow:
      1. Display configuration list
      2. Wait for user input
      3. Initialize camera
      4. Loop through all 6 configurations
      5. For each config: create/configure camera, capture, save files
      6. Cleanup
    """
    
    # Parse command-line arguments
    if len(sys.argv) > 1:
        output_dir = sys.argv[1]
    else:
        output_dir = "mock_camera"
    
    print(f"\nComprehensive Camera Test Suite")
    print(f"{'='*50}")
    print(f"Output directory: {output_dir}")
    print()
    
    # Display configuration list
    print(f"Configurations to capture:")
    print(f"{'-'*50}")
    for key in sorted(CONFIGS.keys()):
        cfg = CONFIGS[key]
        print(f"  {key}. {cfg['comment']}")
    print()
    
    # Wait for user input
    print("This will capture all configurations and save:")
    print("  - Single-stream configs (1-3): RGB + metadata")
    print("  - Dual-stream configs (4-6): RGB + Bayer + metadata")
    print()
    print("Total files to create: 15")
    print(f"Output directory: {output_dir}")
    print()
    
    # user_input = input("Press ENTER to start capturing (Ctrl-C to abort): ").strip()
    
    print("\n" + "="*50)
    print("Starting capture sequence...")
    print("="*50 + "\n")
    
    # Initialize camera once
    print(f"Initializing camera...")
    try:
        # We'll reconfigure for each config to support both single and dual stream
        picam2_instances = {}
        
        total_files = 0
        exposure_results = {}
        
        # Loop through all configurations
        for config_key in sorted(CONFIGS.keys()):
            print(f"\n{'─'*50}")
            print(f"Config {config_key}: {CONFIGS[config_key]['comment']}")
            print(f"{'─'*50}")
            
            # Create and configure camera for this config
            picam2 = build_and_configure_camera(config_key)
            
            try:
                # Estimate exposure for this config
                print(f"  Estimating exposure...")
                exp_result = estimate_exposure(picam2, target_fraction=0.70,
                                               reference_channel="B",
                                               max_exposure_us=330_000,
                                               min_exposure_us=330_000)
                exposure_results[config_key] = exp_result
                
                # Apply the recommended exposure
                recommended_us = exp_result["recommended_exposure"]
                print(f"  Applying recommended exposure: {recommended_us:,} µs")
                picam2.set_controls({"ExposureTime": recommended_us})
                time.sleep(0.5)  # Allow camera to stabilize with new exposure
                
                # Capture and save
                files = capture_config(picam2, config_key, output_dir)
                total_files += len(files)
            
            finally:
                # Always stop and close camera after each config
                picam2.stop()
                picam2.close()
                time.sleep(0.5)
        
        # Summary
        print(f"\n{'='*50}")
        print(f"Capture sequence complete!")
        print(f"{'='*50}")
        print(f"Total files created: {total_files}")
        print(f"Output directory: {output_dir}")
        
        # Exposure analysis summary
        print(f"\n{'='*50}")
        print(f"Exposure Analysis Summary")
        print(f"{'='*50}")
        print(f"{'Config':<10} {'Current (µs)':<15} {'Recommended (µs)':<18} {'Scale':<8} {'B-mean':<8}")
        print(f"{'-'*60}")
        
        recommended_exposures = set()
        for config_key in ['5']: #sorted(CONFIGS.keys()):
            if config_key in exposure_results:
                result = exposure_results[config_key]
                current = result["current_exposure"]
                recommended = result["recommended_exposure"]
                scale = result["scale_factor"]
                b_mean = result["channel_means"]["B"]
                
                recommended_exposures.add(recommended)
                clamped_marker = " (clamped)" if result["clamped"] else ""
                print(f"{config_key:<10} {current:<15,} {recommended:<17,}{clamped_marker:<1} {scale:<8.3f} {b_mean:<8.1f}")
        
        print(f"{'-'*60}")
        
        # Check if exposure is constant
        if len(recommended_exposures) == 1:
            exp_value = list(recommended_exposures)[0]
            print(f"\n✓ Exposure is CONSTANT across all configs: {exp_value:,} µs ({exp_value/1e6:.3f} s)")
        else:
            print(f"\n⚠ Exposure varies across configs:")
            for exp in sorted(recommended_exposures):
                configs = [k for k, r in exposure_results.items() if r["recommended_exposure"] == exp]
                print(f"    {exp:,} µs: configs {', '.join(sorted(configs))}")
        print()
        
        # List created files
        out_path = Path(output_dir)
        if out_path.exists():
            files = sorted(out_path.glob('cfg*'))
            print(f"Files created:")
            for f in files:
                size = f.stat().st_size
                if size > 1_000_000:
                    size_str = f"{size/1_000_000:.1f} MB"
                elif size > 1_000:
                    size_str = f"{size/1_000:.1f} KB"
                else:
                    size_str = f"{size} B"
                print(f"  {f.name:<35} {size_str:>10}")
        
        return True
    
    except KeyboardInterrupt:
        print("\n\n⚠  Interrupted by user (Ctrl-C)")
        return False
    
    except Exception as e:
        print(f"\n⚠  Error during capture: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
