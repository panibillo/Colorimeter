# Test Suite for Colorimeter

This directory contains unit tests and verification scripts for the colorimeter project.

## run_tests.py — the way to run the suite

```bash
python test/run_tests.py                     # every test/test_*.py
python test/run_tests.py test_config_repo    # one module
python test/run_tests.py test_roi test_rect  # several
```

Prefer this over `python -m unittest`: it puts a wall-clock cap on every test
and on the run as a whole, so a wedged test cannot pin a core unattended. The
cap is enforced by `faulthandler.dump_traceback_later(..., exit=True)`, whose
timer thread does not hold the GIL, so it still fires when a test is stuck
inside a C extension (numpy, matplotlib, a camera driver) — where
`signal.alarm` would never get a chance to run. On timeout it prints every
thread's stack beneath the name of the offending test and exits non-zero.

| Environment variable | Meaning | Default |
| --- | --- | --- |
| `COLORIMETER_TEST_TIMEOUT` | seconds allowed per test | 60 |
| `COLORIMETER_SUITE_TIMEOUT` | seconds allowed per run | 900 |

The runner also forces `MPLBACKEND=Agg` (unless overridden), so no test opens a
real window.

## test_bayer_display.py

**Purpose**: Visually verify Bayer raw image data from mock_Picamera2

**What it does**:
1. Loads `cfg5_sbggr1_raw_00.npy` from `mock_camera/` directory
2. Extracts the four Bayer color layers:
   - **B** (Blue): even rows, even cols
   - **Gb** (Green-blue): even rows, odd cols
   - **Gr** (Green-red): odd rows, even cols
   - **R** (Red): odd rows, odd cols
3. Displays layers in a 2×2 subplot grid with color-matched colormaps:
   - Top-left (B): Blues colormap
   - Top-right (Gb): Greens colormap
   - Bottom-left (Gr): Greens colormap
   - Bottom-right (R): Reds colormap

**Usage**:
```bash
cd /home/bill/eclipse-workspace1/Colorimeter
python test/test_bayer_display.py
```

**Output**:
- Console output with file validation and statistics
- Interactive matplotlib window showing the 4 color layers
- PNG file saved to `test/bayer_display.png`

**Verification Checklist**:
- ✓ File loads without error
- ✓ Shape is (1232, 1640) for config 5
- ✓ Dtype is uint16
- ✓ Values are in 10-bit range (0–1023)
- ✓ Visual inspection of Bayer pattern
- ✓ Each layer displays correct color

**Requirements**:
- numpy
- matplotlib
- `mock_camera/cfg5_sbggr1_raw_00.npy` file must exist

## Future Tests

Planned test modules:

- `test_mock_picamera2.py` — Unit tests for mock_Picamera2 API
- `test_color_analysis.py` — Color analysis algorithm verification
- `test_image_io.py` — Image file loading/saving
- `test_calibration.py` — Calibration workflow
