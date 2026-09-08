# mock_Picamera2 Architecture & Data Flow

## Class Hierarchy

```
mock_Picamera2 Module
├── libcamera (SimpleNamespace)
│   └── controls.draft.NoiseReductionModeEnum.Off = 0
│
├── Picamera2 (main camera class)
│   ├── __init__()
│   ├── create_still_configuration(main, raw, **kwargs) → config dict
│   ├── configure(config)
│   ├── set_controls(controls)
│   ├── start()
│   ├── stop()
│   ├── close()
│   ├── capture_request() → MockRequest
│   ├── capture_array(stream="main") → numpy array
│   ├── capture_metadata() → dict
│   └── [helper methods]
│       ├── _infer_config_key(size, fmt) → "1"-"6"
│       ├── _load_images_and_metadata()
│       ├── _load_jpg(fpath) → uint8 array
│       └── _load_metadata(fpath) → dict
│
└── MockRequest (request object)
    ├── main_array (uint8, H×W×3)
    ├── raw_array (uint16, H×W)
    ├── metadata (dict)
    ├── make_array(stream_name) → numpy array
    ├── get_metadata() → dict
    └── release()
```

## Configuration Mapping (Internal)

```
CONFIG_MAP = {
    "1": ("cfg1_rgb888", False),      # Config 1: single-stream RGB
    "2": ("cfg2_rgb888", False),      # Config 2: single-stream RGB
    "3": ("cfg3_rgb888", False),      # Config 3: single-stream RGB
    "4": ("cfg4_sbggr1", True),       # Config 4: dual-stream RGB+Bayer
    "5": ("cfg5_sbggr1", True),       # Config 5: dual-stream RGB+Bayer
    "6": ("cfg6_sbggr1", True),       # Config 6: dual-stream RGB+Bayer
}
```

## File Loading Strategy

### Single-Stream Config (RGB888)
```
create_still_configuration(main={"size": (640, 480), "format": "RGB888"})
            ↓
configure(config)
    ├─ Infer config key: (640, 480) + "RGB888" → "1"
    ├─ Set is_raw_config = False
    └─ Set current_basename = "cfg1_rgb888"

capture_request() or capture_array()
    ↓
_load_images_and_metadata()
    ├─ Load: mock_camera/cfg1_rgb888_00.jpg → main_array
    └─ Load: mock_camera/cfg1_rgb888_00.txt → metadata

MockRequest(main_array=..., raw_array=None, metadata=...)
```

### Dual-Stream Config (RGB888 + SBGGR10_CSI2P)
```
create_still_configuration(
    main={"size": (640, 480), "format": "RGB888"},
    raw={"size": (640, 480), "format": "SBGGR10_CSI2P"}
)
            ↓
configure(config)
    ├─ Infer config key: (640, 480) + "RGB888" → "4"
    ├─ Set is_raw_config = True
    └─ Set current_basename = "cfg4_sbggr1"

capture_request()
    ↓
_load_images_and_metadata()
    ├─ Load: mock_camera/cfg4_sbggr1_rgb_00.jpg → main_array
    ├─ Load: mock_camera/cfg4_sbggr1_raw_00.npy → raw_array
    └─ Load: mock_camera/cfg4_sbggr1_raw_00.txt → metadata

MockRequest(main_array=..., raw_array=..., metadata=...)
```

## Data Types & Shapes

```
RGB Main Stream:
    ├─ Type: uint8
    ├─ Shape: (height, width, 3)
    ├─ Order: BGR (OpenCV convention)
    │
    ├─ Config 1: (480, 640, 3)
    ├─ Config 2: (1232, 1640, 3)
    ├─ Config 3: (2464, 3280, 3)
    ├─ Config 4: (480, 640, 3)
    ├─ Config 5: (1232, 1640, 3)
    └─ Config 6: (2464, 3280, 3)

Raw Bayer Stream (Configs 4-6 only):
    ├─ Type: uint16 (10-bit Bayer in 16-bit container)
    ├─ Shape: (height, width)
    ├─ Pattern: BGGR
    │
    ├─ Config 4: (480, 640)
    ├─ Config 5: (1232, 1640)
    └─ Config 6: (2464, 3280)

Metadata Dict:
    ├─ Key: str (e.g., "ExposureTime")
    ├─ Value: int | float | str
    ├─ Example keys:
    │   ├─ "ExposureTime": int (microseconds)
    │   ├─ "AnalogueGain": float
    │   ├─ "ColourGains": str or tuple
    │   ├─ "ColourTemperature": int
    │   └─ "Lux": float
```

## State Machine

```
Initial state:
    started = False
    config = None
    current_config_key = None

create_still_configuration() → returns config dict (no state change)

configure(config)
    └─ current_config_key = "1"-"6"
    └─ current_basename set
    └─ is_raw_config = True/False

start()
    └─ started = True (must be called before capture)

capture_request() or capture_array()
    └─ Requires: started == True
    └─ Returns: MockRequest or numpy array
    └─ Loads files from mock_camera/

stop()
    └─ started = False

close()
    └─ started = False
    └─ Cleanup
```

## Error Handling

```
Scenario 1: capture() before start()
    → RuntimeError: "Camera not started; call start() first"

Scenario 2: File not found
    → FileNotFoundError: "cfg1_rgb888_00.jpg not found"
    → Prints warning to console

Scenario 3: Stream not available
    → ValueError: "No raw stream data in this request"
    (e.g., trying to get raw from single-stream config)

Scenario 4: mock_camera directory missing
    → FileNotFoundError: "Mock camera directory not found"
    → Prints path where it was expected
```

## Integration with camtest*.py

### camtest2.py (Updated)
```python
try:
    from picamera2 import Picamera2        # Real: RPi
    import libcamera
    import cv2
except ImportError:
    from mock_Picamera2 import Picamera2   # Mock: Desktop
    from mock_Picamera2 import libcamera
    import mock_Picamera2.cv2 as cv2       # Optional: use real cv2 if available

# Rest of code works unchanged!
picam2 = make_camera(config_key)
request = picam2.capture_request()
rgb = request.make_array("main")
bayer = request.make_array("raw")        # If dual-stream
```

### camtest3.py (Unchanged)
```python
try:
    from picamera2 import Picamera2
    import libcamera
    import cv2
except:
    from mock_Picamera2 import Picamera2
    from mock_Picamera2 import libcamera
    from mock_Picamera2 import cv2

# All methods work!
request = picam2.capture_request()
bgr8 = request.make_array("main")
bayer = request.make_array("raw")
meta = request.get_metadata()
```

## Mock File Validation

Before using mock_Picamera2, verify files exist:

```python
import os
from pathlib import Path

mock_dir = Path(__file__).parent.parent / "mock_camera"
required_files = [
    ("cfg1_rgb888_00.jpg", "Config 1 RGB"),
    ("cfg1_rgb888_00.txt", "Config 1 metadata"),
    ("cfg4_sbggr1_rgb_00.jpg", "Config 4 main"),
    ("cfg4_sbggr1_raw_00.npy", "Config 4 raw"),
    ("cfg4_sbggr1_raw_00.txt", "Config 4 metadata"),
    # ... etc
]

for fname, desc in required_files:
    fpath = mock_dir / fname
    if not fpath.exists():
        print(f"⚠  Missing: {desc} ({fpath})")
    else:
        print(f"✓  Found: {desc}")
```

## Summary of Key Capabilities

| Capability | Before | After | Status |
|-----------|--------|-------|--------|
| Drop-in replacement | ✗ | ✓ | Perfect drop-in |
| Single-stream RGB | ✗ | ✓ | Fully working |
| Dual-stream RGB+Bayer | ✗ | ✓ | Fully working |
| File loading | Stub | ✓ | Auto-loads from mock_camera/ |
| Metadata parsing | ✗ | ✓ | Parses TXT files |
| State tracking | ✗ | ✓ | Full state machine |
| Error handling | ✗ | ✓ | Clear error messages |
| Config auto-detection | ✗ | ✓ | Size+format → config 1-6 |
