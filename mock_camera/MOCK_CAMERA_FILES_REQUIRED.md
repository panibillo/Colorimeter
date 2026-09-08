# Mock Camera Directory - Files Required

## Directory Structure
```
mock_camera/
├── cfg1_rgb888_00.jpg              # Config 1: 640×480 RGB
├── cfg1_rgb888_00.txt              # Config 1: metadata
├── cfg2_rgb888_00.jpg              # Config 2: 1640×1232 RGB
├── cfg2_rgb888_00.txt              # Config 2: metadata
├── cfg3_rgb888_00.jpg              # Config 3: 3280×2464 RGB
├── cfg3_rgb888_00.txt              # Config 3: metadata
├── cfg4_sbggr1_rgb_00.jpg          # Config 4: 640×480 RGB
├── cfg4_sbggr1_raw_00.npy          # Config 4: 640×480 Bayer (uint16)
├── cfg4_sbggr1_raw_00.txt          # Config 4: metadata
├── cfg5_sbggr1_rgb_00.jpg          # Config 5: 1640×1232 RGB
├── cfg5_sbggr1_raw_00.npy          # Config 5: 1640×1232 Bayer (uint16)
├── cfg5_sbggr1_raw_00.txt          # Config 5: metadata
├── cfg6_sbggr1_rgb_00.jpg          # Config 6: 3280×2464 RGB
├── cfg6_sbggr1_raw_00.npy          # Config 6: 3280×2464 Bayer (uint16)
└── cfg6_sbggr1_raw_00.txt          # Config 6: metadata
```

## Files by Configuration

### Single-Stream RGB Configs (1-3)

| Config | Size | File | Type | Purpose |
|--------|------|------|------|---------|
| 1 | 640×480 | `cfg1_rgb888_00.jpg` | JPG | Small RGB image |
| 1 | — | `cfg1_rgb888_00.txt` | TXT | Metadata |
| 2 | 1640×1232 | `cfg2_rgb888_00.jpg` | JPG | Medium RGB image |
| 2 | — | `cfg2_rgb888_00.txt` | TXT | Metadata |
| 3 | 3280×2464 | `cfg3_rgb888_00.jpg` | JPG | Full RGB image |
| 3 | — | `cfg3_rgb888_00.txt` | TXT | Metadata |

### Dual-Stream Raw Configs (4-6)

| Config | Size | File | Type | Purpose |
|--------|------|------|------|---------|
| 4 | 640×480 | `cfg4_sbggr1_rgb_00.jpg` | JPG | Small RGB main stream |
| 4 | 640×480 | `cfg4_sbggr1_raw_00.npy` | NPY | Small Bayer raw stream (uint16) |
| 4 | — | `cfg4_sbggr1_raw_00.txt` | TXT | Metadata |
| 5 | 1640×1232 | `cfg5_sbggr1_rgb_00.jpg` | JPG | Medium RGB main stream |
| 5 | 1640×1232 | `cfg5_sbggr1_raw_00.npy` | NPY | Medium Bayer raw stream (uint16) |
| 5 | — | `cfg5_sbggr1_raw_00.txt` | TXT | Metadata |
| 6 | 3280×2464 | `cfg6_sbggr1_rgb_00.jpg` | JPG | Full RGB main stream |
| 6 | 3280×2464 | `cfg6_sbggr1_raw_00.npy` | NPY | Full Bayer raw stream (uint16) |
| 6 | — | `cfg6_sbggr1_raw_00.txt` | TXT | Metadata |

## File Format Specifications

### JPG Files (RGB Images)
- **Format**: JPG image (OpenCV BGR color space)
- **Dtype**: uint8
- **Shape**: (height, width, 3) where height & width match config size
- **Note**: OpenCV loads as BGR, but human-readable for testing

**Creation example (from real capture):**
```python
import cv2
# After capturing with camtest2.py or camtest3.py
# Copy the generated JPG files to mock_camera/
```

### NPY Files (Bayer Raw Data)
- **Format**: NumPy binary array (.npy)
- **Dtype**: uint16 (10-bit Bayer values)
- **Shape**: (height, width) where height & width match config size
- **Bayer pattern**: BGGR (raw sensor output)
- **Note**: Values are 10-bit (0–1023), stored in uint16 (0–65535 range)

**Creation example:**
```python
import numpy as np
bayer_data = np.load("cfg4_sbggr1_raw_00.npy")
print(bayer_data.shape)  # (480, 640)
print(bayer_data.dtype)  # uint16
```

### TXT Files (Metadata)
- **Format**: Key-value pairs, one per line: `Key: Value`
- **Comments**: Lines starting with `#` are ignored
- **Number parsing**: Integers and floats automatically parsed
- **Example content**:

```
SensorTimestamp: 68013486777000
ExposureTime: 100000
AnalogueGain: 1.0
ColourGains: (1.0, 1.0)
ColourTemperature: 5500
Lux: 1.5
```

## How to Generate Mock Camera Files

### Option 1: Use Real Raspberry Pi Captures
1. **On RPi**: Run `camtest2.py` or `camtest3.py` to capture images
2. **Generate all configs**: Capture with configs 1-6
3. **Copy to desktop**: Use SCP or USB to transfer
4. **Organize**: Place files in `mock_camera/` directory with correct naming

```bash
# On RPi
python dev/camtest2.py
# Select configs 1-6 and capture images

# On desktop
scp pi@raspberrypi:/home/pi/R/savedir/cfg*.jpg mock_camera/
scp pi@raspberrypi:/home/pi/R/savedir/cfg*.npy mock_camera/
scp pi@raspberrypi:/home/pi/R/savedir/cfg*.txt mock_camera/
```

### Option 2: Generate Synthetic Test Data (for quick testing)
```python
import numpy as np
import cv2

# Create synthetic RGB image
rgb = np.random.randint(0, 256, (480, 640, 3), dtype=np.uint8)
cv2.imwrite("mock_camera/cfg1_rgb888_00.jpg", rgb)

# Create synthetic Bayer data
bayer = np.random.randint(0, 1024, (480, 640), dtype=np.uint16)
np.save("mock_camera/cfg4_sbggr1_raw_00.npy", bayer)

# Create synthetic metadata
with open("mock_camera/cfg1_rgb888_00.txt", "w") as f:
    f.write("ExposureTime: 100000\n")
    f.write("AnalogueGain: 1.0\n")
    f.write("ColourGains: (1.0, 1.0)\n")
```

## Total File Count
- **Single-stream configs**: 3 configs × 2 files = **6 files**
- **Dual-stream configs**: 3 configs × 3 files = **9 files**
- **Total**: **15 files**

## Testing the Mock Module

```python
from mock_Picamera2 import Picamera2, libcamera

# Single-stream capture
picam2 = Picamera2()
config = picam2.create_still_configuration(
    main={"size": (640, 480), "format": "RGB888"}
)
picam2.configure(config)
picam2.start()
rgb = picam2.capture_array()
print(rgb.shape)  # (480, 640, 3)
picam2.stop()

# Dual-stream capture
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
request.release()
picam2.stop()
```
