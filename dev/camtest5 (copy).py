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


MAIN_FORMT = "RGB888"
RAW_FORMAT = "SBGGR10"

# ---------------------------------------------------------------------------
# Camera configurations (same as camtest2.py)
# ---------------------------------------------------------------------------
CONFIGS = {
    '1': {
        "size":    (640, 480),
        "format":  MAIN_FORMT,
        "is_raw":  False,
        "comment": "Config 1: Small RGB (640×480)"
    },
    '2': {
        "size":    (1640, 1232),
        "format":  MAIN_FORMT,
        "is_raw":  False,
        "comment": "Config 2: Medium RGB (1640×1232)"
    },
    '3': {
        "size":    (3280, 2464),
        "format":  MAIN_FORMT,
        "is_raw":  False,
        "comment": "Config 3: Full RGB (3280×2464)"
    },
    '4': {
        "size":    (640, 480),
        "format":  RAW_FORMAT,
        "is_raw":  True,
        "comment": "Config 4: Small dual-stream RGB + Bayer (640×480)"
    },
    '5': {
        "size":    (1640, 1232),
        "format":  RAW_FORMAT,
        "is_raw":  True,
        "comment": "Config 5: Medium dual-stream RGB + Bayer (1640×1232)"
    },
    '6': {
        "size":    (3280, 2464),
        "format":  RAW_FORMAT,
        "is_raw":  True,
        "comment": "Config 6: Full dual-stream RGB + Bayer (3280×2464)"
    },
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
        bayer = request.make_array("raw")      # uint16 H×W BGGR Bayer
        meta = request.get_metadata()
        request.release()
        
        print(f"\n  RGB Stream Analysis:")
        print(f"    Shape: {rgb8.shape} (H×W×3)")
        print(f"    B mean: {rgb8[:,:,0].mean():.1f}, G mean: {rgb8[:,:,1].mean():.1f}, R mean: {rgb8[:,:,2].mean():.1f}")
        
        print(f"\n  Raw Bayer Stream (SBGGR10):") 
        print(f"    Shape: {bayer.shape} (H×W)")
        print(f"    Dtype: {bayer.dtype}")
        print(f"    Value range: {bayer.min()}-{bayer.max()} (10-bit: 0-1023)")
        
        print("Save RGB as JPG")
        rgb_file = out_dir / f"{basename}_rgb_00.jpg"
        cv2.imwrite(str(rgb_file), rgb8)
        files['rgb'] = rgb_file
        print(f"    ✓ RGB:      {rgb_file.name} ({rgb8.shape})")
        
        print("Save Bayer as NPY (SBGGR10 10-bit uint16)")
        raw_file = out_dir / f"{basename}_raw_00.npy"
        np.save(str(raw_file), bayer)
        files['raw'] = raw_file
        print(f"    ✓ Bayer:    {raw_file.name} (H={bayer.shape[0]}, W={bayer.shape[1]}, dtype={bayer.dtype})")
        
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
        recommended_us = max_exposure_us
    if recommended_us <= min_exposure_us:
        print(f"      ⚠  Exposure clamped to {min_exposure_us:,} µs — illumination may too bright")
        recommended_us = min_exposure_us
    #recommended_us = max( min(recommended_us, max_exposure_us), min_exposure_us)

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
                                               max_exposure_us=900_000,
                                               min_exposure_us=  1_000)
                exposure_results[config_key] = exp_result
                
                # Apply the recommended exposure
                recommended_us = exp_result["recommended_exposure"]
                print(f"  Applying recommended exposure: {recommended_us:,} µs")
                picam2.set_controls({"ExposureTime": recommended_us})
                time.sleep(1.0)  # Allow camera to stabilize with new exposure
                
                # Capture a throwaway frame to ensure exposure takes effect
                _ = picam2.capture_array()
                time.sleep(0.5)
                
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
        for config_key in sorted(CONFIGS.keys()):
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
