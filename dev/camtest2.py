"""
Capture 640 x 480 images & full frame images interactively without shutting down

CONFIGS 1,2,3 work: capture 640 x 480 images
CONFIGS 4,5,6 do not work. They generate the following camera error:  
-- make_camera() with config 4: 640x480 SBGGR10_CSI2P
[40:19:28.628980919] [3115]  INFO Camera camera_manager.cpp:326 libcamera v0.5.1+100-e53bdf1f
[40:19:28.897378346] [3118]  WARN RPiSdn sdn.cpp:40 Using legacy SDN tuning - please consider moving SDN inside rpi.denoise
[40:19:28.918495907] [3118]  INFO RPI vc4.cpp:440 Registered camera /base/soc/i2c0mux/i2c@1/imx219@10 to Unicam device /dev/media1 and ISP device /dev/media0
[40:19:28.919849879] [3118]  INFO RPI pipeline_base.cpp:1107 Using configuration file '/usr/share/libcamera/pipeline/rpi/vc4/rpi_apps.yaml'
-- Unexpected error: Bad format SBGGR10_CSI2P in stream main


CLAUDE: This file should retain debugging messages printed to std out
"""

import time
import signal
import cv2
import numpy as np
from picamera2 import Picamera2

# ---------------------------------------------------------------------------
# Camera configurations
# Key : (size,          format,           comment)
# ---------------------------------------------------------------------------
CONFIGS = {
    '1': {
        "size":    (640, 480),
        "format":  "RGB888",
        "comment": "Small RGB: ISP debayers + downscales from full raw frame. "
                   "Fast capture, low memory. Good for testing/preview."
    },
    '2': {
        "size":    (1640, 1232),
        "format":  "RGB888",
        "comment": "Medium RGB: ISP debayers from 2x2 binned raw frame. "
                   "Natural half-resolution full-frame. Balanced quality/speed."
    },
    '3': {
        "size":    (3280, 2464),
        "format":  "RGB888",
        "comment": "Full RGB: ISP debayers full-resolution raw frame. "
                   "Maximum detail, slow capture, high memory use on Pi Zero."
    },
    '4': {
        "size":    (640, 480),
        "format":  "SBGGR10_CSI2P",
        "is_raw":  True,
        "comment": "Small RAW10 + RGB: dual-stream capture. Main stream RGB888 "
                   "(ISP debayered), raw stream native Bayer 10-bit packed. "
                   "10-bit packed (2 pixels/3 bytes). Use for custom debayering."
    },
    '5': {
        "size":    (1640, 1232),
        "format":  "SBGGR10_CSI2P",
        "is_raw":  True,
        "comment": "Medium RAW10 + RGB: dual-stream. 2x2 binned Bayer data in "
                   "raw stream, ISP-debayered RGB888 in main stream. "
                   "Good balance of raw detail vs. file size."
    },
    '6': {
        "size":    (3280, 2464),
        "format":  "SBGGR10_CSI2P",
        "is_raw":  True,
        "comment": "Full RAW10 + RGB: dual-stream. Full-resolution Bayer in raw "
                   "stream, ISP-debayered RGB888 in main stream. "
                   "Maximum fidelity, slowest capture."
    },
}

def print_config_menu():
    print("\nAvailable camera configurations:")
    print(f"  {'Key':<4} {'Size':<14} {'Format':<18}")
    print(f"  {'-'*4} {'-'*14} {'-'*18}")
    for key, cfg in CONFIGS.items():
        size_str = f"{cfg['size'][0]}x{cfg['size'][1]}"
        print(f"  {key:<4} {size_str:<14} {cfg['format']:<18}")
    print()

def get_config_choice():
    while True:
        print_config_menu()
        choice = input("Select configuration (1-6): ").strip()
        if choice in CONFIGS:
            return choice
        print(f"  !! Invalid choice '{choice}'. Please enter a number from 1 to 6.")

def make_camera(config_key):
    cfg = CONFIGS[config_key]
    is_raw = cfg.get("is_raw", False)
    
    if is_raw:
        print(f"-- make_camera() with config {config_key}: "
              f"{cfg['size'][0]}x{cfg['size'][1]} (dual-stream: RGB888 + {cfg['format']})")
        picam2 = Picamera2()
        config = picam2.create_still_configuration(
            main={
                "size":   cfg["size"],
                "format": "RGB888",              # ISP-debayered RGB for analysis
            },
            raw={
                "size":   cfg["size"],
                "format": cfg["format"],         # Raw Bayer for archival
            },
            buffer_count=1
        )
    else:
        print(f"-- make_camera() with config {config_key}: "
              f"{cfg['size'][0]}x{cfg['size'][1]} {cfg['format']}")
        picam2 = Picamera2()
        config = picam2.create_still_configuration(
            main={"size": cfg["size"], "format": cfg["format"]},
            buffer_count=1
        )
    
    picam2.configure(config)
    picam2.start()
    time.sleep(2)
    return picam2

def release_camera(picam2):
    """Fully release the camera hardware resource."""
    if picam2 is not None:
        try:
            picam2.stop()
        except Exception:
            pass
        try:
            picam2.close()
        except Exception:
            pass
        del picam2
        time.sleep(0.5)

def handle_sigtstp(signum, frame):
    raise KeyboardInterrupt("SIGTSTP received — exiting cleanly")

signal.signal(signal.SIGTSTP, handle_sigtstp)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
i = 0
picam2 = None
config_key = None

try:
    config_key = get_config_choice()

    while True:
        picam2 = make_camera(config_key)

        cfg = CONFIGS[config_key]
        is_raw = cfg.get("is_raw", False)
        fmt_tag = cfg["format"].replace("_", "")[:6].lower()  # e.g. rgb888, sbggr
        
        if is_raw:
            # Dual-stream capture: RGB + Bayer
            request = picam2.capture_request()
            rgb = request.make_array("main")        # Main stream: RGB888
            bayer = request.make_array("raw")       # Raw stream: BGGR10
            request.release()
            
            # Save RGB as JPG
            rgb_fname = f"/home/bill/R/savedir/cfg{config_key}_{fmt_tag}_rgb_{i:02}.jpg"
            print(f"-- capturing RGB to '{rgb_fname}'")
            cv2.imwrite(rgb_fname, rgb)
            
            # Save raw Bayer as NPY
            bayer_fname = f"/home/bill/R/savedir/cfg{config_key}_{fmt_tag}_raw_{i:02}.npy"
            print(f"-- capturing Bayer to '{bayer_fname}'")
            np.save(bayer_fname, bayer)
            
            print(f"  RGB shape: {rgb.shape}, dtype: {rgb.dtype}")
            print(f"  Bayer shape: {bayer.shape}, dtype: {bayer.dtype}")
        else:
            # Single-stream capture: RGB only
            fname = f"/home/bill/R/savedir/cfg{config_key}_{fmt_tag}_{i:02}.jpg"
            print(f"-- capturing to '{fname}'")
            rgb = picam2.capture_array()
            cv2.imwrite(fname, rgb)

        release_camera(picam2)
        picam2 = None

        print(f"\nLast capture: config {config_key} "
              f"({cfg['size'][0]}x{cfg['size'][1]} {cfg['format']})")
        print("Options:")
        print("  y  - capture again with same configuration")
        print("  c  - choose a new configuration")
        print("  n  - exit")
        choice = input("Choice: ").strip().lower()

        if choice == 'y':
            i += 1
        elif choice == 'c':
            config_key = get_config_choice()
            # i = 0          # reset counter for new config series
        else:
            break

except KeyboardInterrupt:
    print("\n-- Interrupted by user (Ctrl-C / Ctrl-Z)")

except Exception as e:
    print(f"\n-- Unexpected error: {e}")

finally:
    if picam2 is not None:
        print("-- Emergency camera shutdown...")
        release_camera(picam2)
    print("Camera has been correctly shut down")

print("END of script")