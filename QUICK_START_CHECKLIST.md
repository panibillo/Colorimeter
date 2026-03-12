# Quick Start: mock_Picamera2 Setup

## ✓ Completed
- [x] Rewrote `mock_Picamera2.py` with full API support
- [x] Implemented dual-stream capture (RGB + Bayer)
- [x] State machine and error handling
- [x] File loading from `mock_camera/` directory
- [x] Metadata parsing
- [x] Documentation (3 detailed guides)

## ⚙ Setup Steps

### 1. Create mock_camera Directory
```bash
cd /home/bill/eclipse-workspace1/Colorimeter
mkdir -p mock_camera
```

### 2. Populate Mock Camera Files
You need to provide **15 files**. Choose one method:

#### Method A: Capture from Real Raspberry Pi ✓ RECOMMENDED
```bash
# On Raspberry Pi:
python dev/camtest2.py
# Go through each configuration (1-6) and capture at least one image

# Transfer to desktop:
scp pi@raspberrypi:/home/pi/R/savedir/cfg*.jpg ~/Colorimeter/mock_camera/
scp pi@raspberrypi:/home/pi/R/savedir/cfg*.npy ~/Colorimeter/mock_camera/
# Metadata files not needed if you use camtest2.py output directly
```

#### Method B: Generate Synthetic Test Data
```python
# Run on desktop to create dummy files
import numpy as np
import cv2
from pathlib import Path

mock_dir = Path(...) / "Colorimeter" / "mock_camera"
mock_dir.mkdir(exist_ok=True)

# Single-stream configs (1-3): RGB only
for cfg in [1, 2, 3]:
    sizes = {1: (480, 640), 2: (1232, 1640), 3: (2464, 3280)}
    h, w = sizes[cfg]
    rgb = np.random.randint(50, 200, (h, w, 3), dtype=np.uint8)
    cv2.imwrite(str(mock_dir / f"cfg{cfg}_rgb888_00.jpg"), rgb)
    
    # Create metadata
    with open(mock_dir / f"cfg{cfg}_rgb888_00.txt", "w") as f:
        f.write("ExposureTime: 100000\n")
        f.write("AnalogueGain: 1.0\n")
        f.write("ColourGains: (1.0, 1.0)\n")
        f.write("ColourTemperature: 5500\n")

# Dual-stream configs (4-6): RGB + Bayer
for cfg in [4, 5, 6]:
    sizes = {4: (480, 640), 5: (1232, 1640), 6: (2464, 3280)}
    h, w = sizes[cfg]
    
    # RGB main stream
    rgb = np.random.randint(50, 200, (h, w, 3), dtype=np.uint8)
    cv2.imwrite(str(mock_dir / f"cfg{cfg}_sbggr1_rgb_00.jpg"), rgb)
    
    # Bayer raw stream (uint16, values in 0-1023 range for 10-bit)
    bayer = np.random.randint(0, 1024, (h, w), dtype=np.uint16)
    np.save(str(mock_dir / f"cfg{cfg}_sbggr1_raw_00.npy"), bayer)
    
    # Metadata
    with open(mock_dir / f"cfg{cfg}_sbggr1_raw_00.txt", "w") as f:
        f.write("ExposureTime: 100000\n")
        f.write("AnalogueGain: 1.0\n")
        f.write("ColourGains: (1.0, 1.0)\n")
        f.write("ColourTemperature: 5500\n")

print(f"✓ Created {len(list(mock_dir.glob('*')))} mock files")
```

### 3. Verify File Structure
```bash
ls -la mock_camera/
# Should show 15 files:
# cfg1_rgb888_00.jpg       cfg1_rgb888_00.txt
# cfg2_rgb888_00.jpg       cfg2_rgb888_00.txt
# cfg3_rgb888_00.jpg       cfg3_rgb888_00.txt
# cfg4_sbggr1_rgb_00.jpg   cfg4_sbggr1_raw_00.npy   cfg4_sbggr1_raw_00.txt
# cfg5_sbggr1_rgb_00.jpg   cfg5_sbggr1_raw_00.npy   cfg5_sbggr1_raw_00.txt
# cfg6_sbggr1_rgb_00.jpg   cfg6_sbggr1_raw_00.npy   cfg6_sbggr1_raw_00.txt
```

### 4. Test the Mock Module
```python
import sys
sys.path.insert(0, '/home/bill/eclipse-workspace1/Colorimeter/src')

from mock_Picamera2 import Picamera2

# Test single-stream
picam2 = Picamera2()
config = picam2.create_still_configuration(
    main={"size": (640, 480), "format": "RGB888"}
)
picam2.configure(config)
picam2.start()
rgb = picam2.capture_array()
print(f"✓ Single-stream capture: {rgb.shape}, dtype={rgb.dtype}")
picam2.stop()

# Test dual-stream
picam2 = Picamera2()
config = picam2.create_still_configuration(
    main={"size": (640, 480), "format": "RGB888"},
    raw={"size": (640, 480), "format": "SBGGR10_CSI2P"}
)
picam2.configure(config)
picam2.start()
request = picam2.capture_request()
rgb = request.make_array("main")
bayer = request.make_array("raw")
meta = request.get_metadata()
print(f"✓ Dual-stream capture: RGB={rgb.shape}, Bayer={bayer.shape}")
print(f"✓ Metadata keys: {list(meta.keys())}")
request.release()
picam2.stop()

print("✓ All tests passed!")
```

### 5. Update camtest2.py & camtest3.py (Optional)
Add try/except to use mock on desktop:

```python
# At the top of camtest2.py or camtest3.py
try:
    from picamera2 import Picamera2
    import libcamera
    import cv2
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from mock_Picamera2 import Picamera2, libcamera
    import mock_Picamera2.cv2 as cv2
```

Then the rest of the code works unchanged on both RPi and desktop!

## Troubleshooting

### "Mock camera directory not found"
→ Ensure `mock_camera/` exists in `/home/bill/eclipse-workspace1/Colorimeter/`

### "cfg1_rgb888_00.jpg [Errno 2] No such file or directory"
→ You haven't populated the mock_camera directory yet. See Step 2 above.

### "No main stream data in this request"
→ Config not properly set. Call `configure()` before `capture_request()`

### "No raw stream data in this request"
→ Trying to get raw from single-stream config (1-3). Only configs 4-6 have raw streams.

### Files load but return wrong size
→ Check file naming matches config size exactly (e.g., cfg1 must be 640×480)

## Files Created/Modified

```
Modified:
  src/mock_Picamera2.py                ← Complete rewrite
  dev/camtest2.py                      ← Updated to dual-stream

New Documentation:
  MOCK_PICAMERA2_IMPROVEMENTS.md       ← Summary of changes
  MOCK_CAMERA_FILES_REQUIRED.md        ← File spec & creation guide
  MOCK_PICAMERA2_ARCHITECTURE.md       ← Technical architecture
  QUICK_START_CHECKLIST.md             ← This file
```

## Next Steps

1. **Create mock_camera directory and populate with test files**
2. **Test mock_Picamera2 with the verification script above**
3. **Update camtest2.py and camtest3.py with try/except imports**
4. **Run camtest2.py on desktop using mock images**
5. **Test color_analysis.py and other modules on desktop**

## Verification Checklist

```
□ mock_camera/ directory created
□ 15 test files populated
□ mock_Picamera2.py imports without error
□ Picamera2() constructor works
□ Single-stream capture returns RGB array
□ Dual-stream capture returns RGB + Bayer arrays
□ Metadata parsing works
□ Error handling produces clear messages
□ camtest2.py runs on desktop
□ All 6 configurations tested
```
