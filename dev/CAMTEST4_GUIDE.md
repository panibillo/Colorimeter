# camtest4.py - Comprehensive Test Suite Generator

## Purpose

Automatically captures test images for all 6 camera configurations and saves all required file types for the mock camera test suite.

## Features

✓ **All 6 configurations**: Automatically loops through configs 1-6  
✓ **All file types**: Saves RGB (.jpg), Bayer (.npy), and metadata (.txt)  
✓ **Dual-stream**: Properly handles both single-stream (1-3) and dual-stream (4-6) configs  
✓ **File naming**: Matches exactly the mock_camera file naming convention  
✓ **Metadata capture**: Saves all camera parameters to .txt files  
✓ **Error handling**: Graceful cleanup on Ctrl-C  
✓ **Desktop compatible**: Works with mock_Picamera2 for testing without RPi  

## Usage

### Basic (uses default output directory "mock_camera")
```bash
python camtest4.py
```

### Custom output directory
```bash
python camtest4.py /path/to/output
```

### Example
```bash
cd /home/bill/eclipse-workspace1/Colorimeter/dev

# Capture to mock_camera directory
python camtest4.py

# Or specify custom directory
python camtest4.py ../test_images
```

## What It Does

1. **Displays configuration list** — Shows all 6 configs to be captured
2. **Waits for input** — Press Enter to start, Ctrl-C to abort
3. **Captures sequentially** — Loops through configs 1-6
4. **For each config**:
   - Initializes camera with proper configuration
   - Captures RGB main stream
   - Captures Bayer raw stream (configs 4-6 only)
   - Extracts metadata
   - Saves all 3 file types
   - Cleans up camera resources
5. **Reports results** — Summary of files created

## Output Files

### Single-Stream Configs (1-3)
Each config creates 2 files:
```
cfg1_rgb888_00.jpg      RGB image (640×480)
cfg1_rgb888_00.txt      Camera metadata

cfg2_rgb888_00.jpg      RGB image (1640×1232)
cfg2_rgb888_00.txt      Camera metadata

cfg3_rgb888_00.jpg      RGB image (3280×2464)
cfg3_rgb888_00.txt      Camera metadata
```

### Dual-Stream Configs (4-6)
Each config creates 3 files:
```
cfg4_sbggr1_rgb_00.jpg   RGB image (640×480)
cfg4_sbggr1_raw_00.npy   Bayer data (640×480, uint16)
cfg4_sbggr1_raw_00.txt   Camera metadata

cfg5_sbggr1_rgb_00.jpg   RGB image (1640×1232)
cfg5_sbggr1_raw_00.npy   Bayer data (1640×1232, uint16)
cfg5_sbggr1_raw_00.txt   Camera metadata

cfg6_sbggr1_rgb_00.jpg   RGB image (3280×2464)
cfg6_sbggr1_raw_00.npy   Bayer data (3280×2464, uint16)
cfg6_sbggr1_raw_00.txt   Camera metadata
```
**Total: 15 files**

Note that the Bayer data is in *unpacked* unint16 format camera  __format:'SBGGR10'__, which makes it easier to read in. 
This format is slightly larger than the alternative format obtained using camera __format:'SBGGR10_CSI2P'__,
 but we generally don't need to save these except to generate files for testing.

## Image Output Example

```
Comprehensive Camera Test Suite
==================================================
Output directory: mock_camera

Configurations to capture:
  1. Config 1: Small RGB (640×480)
  2. Config 2: Medium RGB (1640×1232)
  3. Config 3: Full RGB (3280×2464)
  4. Config 4: Small dual-stream RGB + Bayer (640×480)
  5. Config 5: Medium dual-stream RGB + Bayer (1640×1232)
  6. Config 6: Full dual-stream RGB + Bayer (3280×2464)

This will create 15 total files...

Press ENTER to start capturing (Ctrl-C to abort): 

==================================================
Starting capture sequence...
==================================================

──────────────────────────────────────────────────
Config 1: Config 1: Small RGB (640×480)
──────────────────────────────────────────────────
  Configuring camera: Config 1: Small RGB (640×480)
  Capturing config 1...
    ✓ RGB:      cfg1_rgb888_00.jpg (480, 640, 3)
    ✓ Metadata: cfg1_rgb888_00.txt

[... repeats for configs 2-6 ...]

==================================================
Capture sequence complete!
==================================================
Total files created: 15
Output directory: mock_camera

Files created:
  cfg1_rgb888_00.jpg               307.2 KB
  cfg1_rgb888_00.txt                0.8 KB
  cfg2_rgb888_00.jpg              1.2 MB
  cfg2_rgb888_00.txt                0.8 KB
  cfg3_rgb888_00.jpg              4.9 MB
  cfg3_rgb888_00.txt                0.8 KB
  cfg4_sbggr1_raw_00.npy          0.8 MB
  cfg4_sbggr1_rgb_00.jpg          307.2 KB
  cfg4_sbggr1_raw_00.txt            0.8 KB
  cfg5_sbggr1_raw_00.npy          3.2 MB
  cfg5_sbggr1_rgb_00.jpg          1.2 MB
  cfg5_sbggr1_raw_00.txt            0.8 KB
  cfg6_sbggr1_raw_00.npy         12.8 MB
  cfg6_sbggr1_rgb_00.jpg          4.9 MB
  cfg6_sbggr1_raw_00.txt            0.8 KB
```

## Desktop Testing (with mock_Picamera2)

The script automatically falls back to `mock_Picamera2` if the real library isn't available:

```bash
# On desktop (mock mode)
python camtest4.py

# It will:
# 1. Import mock_Picamera2 automatically
# 2. Load test images from mock_camera/
# 3. Save them to output directory
# (Allows testing without RPi)
```

## Integration with Test Suite

After running `camtest4.py` with output to `mock_camera/`:

```bash
# Verify images with test_bayer_display.py
python ../test/test_bayer_display.py

# Test mock_Picamera2 can load files
python -c "
from src.mock_Picamera2 import Picamera2
picam2 = Picamera2()
# ... test all configs
"
```

## Configuration Details

| Config | Size | Format | Stream Type | Purpose |
|--------|------|--------|-------------|---------|
| 1 | 640×480 | RGB888 | Single | Quick preview |
| 2 | 1640×1232 | RGB888 | Single | Balanced |
| 3 | 3280×2464 | RGB888 | Single | Full detail |
| 4 | 640×480 | RGB+Bayer | Dual | Quick raw |
| 5 | 1640×1232 | RGB+Bayer | Dual | Balanced raw |
| 6 | 3280×2464 | RGB+Bayer | Dual | Full raw |

## Camera Settings

All captures use these fixed settings:
- Exposure: Adaptive for each format: target_mean = 0.7 * 255 
- Analogue Gain: 1.0 (unity)
- White Balance: Disabled (gains 1.0, 1.0)
- Noise Reduction: Off
- Sharpness: 0.0
- Contrast: 1.0
- Saturation: 1.0

## Errors & Solutions

| Error | Cause | Fix |
|-------|-------|-----|
| Module not found (picamera2) | Running on desktop | Uses mock_Picamera2 automatically |
| Permission denied | Output directory restricted | Run with proper permissions |
| Camera timeout | Camera still initializing | Increase time.sleep() value |
| File already exists | Running script twice | Delete output files or use different directory |

## Notes

- ✓ Works on RPi with real picamera2
- ✓ Works on desktop with mock_Picamera2
- ✓ Proper resource cleanup between configs
- ✓ Metadata automatically extracted from camera
- ✓ Safe file naming matches mock test requirements
