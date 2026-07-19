# test_bayer_display.py - Quick Start Guide

## Overview

The `test_bayer_display.py` module verifies that the Bayer raw image data from `mock_Picamera2` is correctly formatted and accessible. It visually displays the four color layers of the BGGR Bayer pattern in an interactive matplotlib window.

## File Structure

```
test/
├── __init__.py                    (Python package marker)
├── README.md                      (Test suite overview)
├── test_bayer_display.py          (This script)
├── TEST_BAYER_DISPLAY_GUIDE.md    (This guide)
└── bayer_display.png             (Generated output)
```

## Prerequisites

### Required Files
```
mock_camera/cfg5_sbggr1_raw_00.npy
```
- Should be in `../mock_camera/` (relative to test/ directory)
- Must be uint16 dtype
- Must be shape (1232, 1640) for config 5
- Contains 10-bit Bayer BGGR sensor data

### Required Python Packages
```bash
pip install numpy matplotlib
```

## Running the Test

### Method 1: Direct Execution
```bash
cd /home/bill/eclipse-workspace1/Colorimeter
python test/test_bayer_display.py
```

### Method 2: From Python
```python
import sys
from pathlib import Path

# Add test directory to path
test_dir = Path("test")
sys.path.insert(0, str(test_dir))

from test_bayer_display import main
main()
```

### Method 3: Import Functions
```python
import numpy as np
from test.test_bayer_display import extract_bayer_layers, plot_bayer_layers

# Load your own Bayer data
bayer = np.load("path/to/your_bayer.npy")
layers = extract_bayer_layers(bayer)

# Display
plot_bayer_layers(bayer, output_file="my_bayer_display.png")
```

## Output Explanation

### Console Output
```
Test: Bayer Raw Image Display
================================
Mock directory: /home/bill/eclipse-workspace1/Colorimeter/mock_camera
Target file:   /home/bill/eclipse-workspace1/Colorimeter/mock_camera/cfg5_sbggr1_raw_00.npy

Loading: cfg5_sbggr1_raw_00.npy
✓ Loaded successfully

File Format Verification:
  Shape:  (1232, 1640)
  Dtype:  uint16
  Min:    0
  Max:    1023
  Mean:   512.5
  Median: 512.0
  ✓ Shape matches config 5 (1640×1232)
  ✓ Dtype is uint16 (10-bit Bayer in 16-bit container)

Extracting Bayer layers...
  B  (BGGR['B']): (616, 820), min=    0, max= 1023, mean= 512.5
  Gb (BGGR['Gb']): (616, 820), min=    0, max= 1023, mean= 512.5
  Gr (BGGR['Gr']): (616, 820), min=    0, max= 1023, mean= 512.5
  R  (BGGR['R']): (616, 820), min=    0, max= 1023, mean= 512.5

Displaying 2×2 subplot grid...
✓ Saved to: /home/bill/eclipse-workspace1/Colorimeter/test/bayer_display.png

✓ Test completed successfully!
```

### Visual Output: 2×2 Subplot Grid

```
┌─────────────────────────────────┬─────────────────────────────────┐
│  Blue (B) Layer                 │  Green-Blue (Gb) Layer          │
│  Blues colormap                 │  Greens colormap                │
│  (616, 820) uint16              │  (616, 820) uint16              │
│  [colorbar]                     │  [colorbar]                     │
├─────────────────────────────────┼─────────────────────────────────┤
│  Green-Red (Gr) Layer           │  Red (R) Layer                  │
│  Greens colormap                │  Reds colormap                  │
│  (616, 820) uint16              │  (616, 820) uint16              │
│  [colorbar]                     │  [colorbar]                     │
└─────────────────────────────────┴─────────────────────────────────┘
```

Each subplot shows:
- **Title**: Color name, shape, and value range
- **Image**: Monochrome representation with color-matched colormap
- **Colorbar**: Intensity scale (0–1023 for 10-bit)

## What to Look For

### ✓ Valid Output Indicators

1. **File loads successfully**
   - No "File not found" errors
   - Shape is (1232, 1640)
   - Dtype is uint16

2. **Data range is correct**
   - Values between 0–1023 (10-bit)
   - Integer range suitable for Bayer sensor

3. **Layers are extracted correctly**
   - Each layer is (616, 820) — exactly half the original dimensions
   - Each layer has similar value distributions
   - No negative or > 1023 values

4. **Visual inspection**
   - All four subplots display without errors
   - Colormaps match expected colors (blue, green, green, red)
   - No artifacts or noise patterns
   - Consistent pixel values across layers

### ⚠ Warning Signs

| Issue | Cause | Solution |
|-------|-------|----------|
| "File not found" | cfg5_sbggr1_raw_00.npy missing | Generate or copy from RPi |
| Wrong shape | File is different config or size | Use correct test file |
| Wrong dtype | File corrupted or created wrong | Regenerate from RPi |
| All zeros | File not populated | Capture real image on RPi |
| All max values | Exposure issue | Check brightness settings |
| Empty plot | Matplotlib not installed | `pip install matplotlib` |

## Bayer Pattern Explanation

The BGGR pattern in a raw sensor is organized as:

```
          Columns
          0  1  2  3  4  5 ...
     0    B  Gb B  Gb B  Gb
     1    Gr R  Gr R  Gr R
     2    B  Gb B  Gb B  Gb
     3    Gr R  Gr R  Gr R
Rows
     ...
```

This test extracts each color separately:
- **B layer** = (0::2, 0::2) = even rows, even cols
- **Gb layer** = (0::2, 1::2) = even rows, odd cols  
- **Gr layer** = (1::2, 0::2) = odd rows, even cols
- **R layer** = (1::2, 1::2) = odd rows, odd cols

Each layer is therefore half the original resolution in both dimensions.

## Advanced Usage

### Extract Layers Only (No Display)
```python
from test.test_bayer_display import extract_bayer_layers
import numpy as np

bayer = np.load("cfg5_sbggr1_raw_00.npy")
layers = extract_bayer_layers(bayer)

b_layer = layers['B']
gb_layer = layers['Gb']
gr_layer = layers['Gr']
r_layer = layers['R']

# Use layers in your own analysis
print(f"Blue channel mean: {b_layer.mean()}")
```

### Save Plot Without Display
```python
from test.test_bayer_display import plot_bayer_layers
import numpy as np

bayer = np.load("cfg5_sbggr1_raw_00.npy")
fig, axes = plot_bayer_layers(bayer, output_file="my_plot.png")
# Figure displays, PNG saved, no interactive window
```

### Process All Configs
```python
from pathlib import Path
from test.test_bayer_display import extract_bayer_layers, plot_bayer_layers
import numpy as np

mock_dir = Path("mock_camera")

for cfg in [4, 5, 6]:  # Dual-stream configs only
    npy_file = mock_dir / f"cfg{cfg}_sbggr1_raw_00.npy"
    if npy_file.exists():
        bayer = np.load(npy_file)
        fig, axes = plot_bayer_layers(
            bayer, 
            output_file=f"test/bayer_display_cfg{cfg}.png"
        )
```

## Troubleshooting

### Error: "FileNotFoundError: cfg5_sbggr1_raw_00.npy"
**Problem**: Mock camera file doesn't exist
**Solution**: Create mock_camera directory and populate with test files (see QUICK_START_CHECKLIST.md)

### Error: "No module named matplotlib"
**Problem**: matplotlib not installed
**Solution**: 
```bash
pip install matplotlib
```

### Error: "ValueError: invalid literal for int()"
**Problem**: Metadata file corrupted
**Solution**: Ensure metadata .txt file has proper format (Key: Value)

### Plot shows all black/white
**Problem**: Data out of range
**Solution**: 
1. Verify file is uint16 with values in 0–1023
2. Check RGB file (cfg5_sbggr1_rgb_00.jpg) loads correctly
3. Regenerate test files from RPi

### Can't find mock_camera directory
**Problem**: Script run from wrong location
**Solution**:
```bash
# Make sure you're in Colorimeter root
cd /home/bill/eclipse-workspace1/Colorimeter
python test/test_bayer_display.py
```

## Integration with mock_Picamera2

This test verifies the file loading system used by `mock_Picamera2.py`. If this test passes:
- ✓ Files are accessible from the mock module
- ✓ Data format is correct
- ✓ Array shapes and dtypes are as expected
- ✓ mock_Picamera2 can successfully load and return this data

## Next Steps

After successful testing:
1. Run `test/test_bayer_display.py` for configs 4, 5, 6
2. Verify all three dual-stream configs load correctly
3. Test `mock_Picamera2.py` fully with all 6 configs
4. Run color analysis on the Bayer data
5. Proceed to full integration testing with colorimeter.py
