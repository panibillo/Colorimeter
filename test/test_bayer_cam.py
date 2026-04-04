#!/usr/bin/env python3
"""
Live camera test for BayerCam.unpack_raw10() and extract_bayer_layers().

Captures one frame at 1640×1232 using natural lighting (no LEDs), unpacks
the raw Bayer data, and reports the value range and statistics for each of
the four BGGR sub-arrays.

Run from the project root:
    python test/test_bayer_cam.py
or from the test/ directory:
    python test_bayer_cam.py
"""

import sys
import time
from pathlib import Path

import numpy as np

# Allow import from src/ regardless of working directory
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bayer_cam import BayerCam

try:
    from picamera2 import Picamera2
    import libcamera
except ImportError:
    from mock_Picamera2 import Picamera2, libcamera


RAWFORMAT    = "SBGGR10"
CAPTURE_SIZE = (1640, 1232)
EXPOSURE_US  = 50_000        # 50 ms; adjust for ambient lighting
ANALOGUE_GAIN = 1.0
SETTLE_SECS  = 3.0


def main():
    print("=" * 56)
    print(f'Running {__file__}')
    print("=" * 56)
    print("BayerCam.unpack_raw10 / extract_bayer_layers test")
    print("=" * 56)
    print(f"  Size      : {CAPTURE_SIZE[0]} x {CAPTURE_SIZE[1]}")
    print(f"  Exposure  : {EXPOSURE_US:,} µs  ({EXPOSURE_US/1000:.0f} ms)")
    print(f"  Lighting  : natural (no LEDs)")
    print()

    time.sleep(2)
    try:
        picam2 = Picamera2()
    except Exception as exc:
        sys.exit(f"Cannot open camera: {exc}")
    time.sleep(2)

    config = picam2.create_still_configuration(
        main={"size": CAPTURE_SIZE, "format": "RGB888"},
        raw={"size": CAPTURE_SIZE, "format": RAWFORMAT},
        buffer_count=1,
    )
    picam2.configure(config)
    picam2.set_controls({
        "AeEnable":           False,
        "ExposureTime":       EXPOSURE_US,
        "AnalogueGain":       ANALOGUE_GAIN,
        "AwbEnable":          False,
        "ColourGains":        (1.0, 1.0),
        "NoiseReductionMode": libcamera.controls.draft.NoiseReductionModeEnum.Off,
        "Sharpness":          0.0,
        "Contrast":           1.0,
        "Saturation":         1.0,
    })

    print(f"Starting camera, settling {SETTLE_SECS:.0f} s ...")
    picam2.start()
    time.sleep(SETTLE_SECS)

    request   = picam2.capture_request()
    raw_bytes = request.make_array("raw")
    meta      = request.get_metadata()
    request.release()
    picam2.stop()
    picam2.close()

    print()
    print(f"--- raw_bytes (before unpack) format={RAWFORMAT} ---")
    print(f"  dtype : {raw_bytes.dtype}")
    print(f"  shape : {raw_bytes.shape}")
    print(f"  min   : {int(raw_bytes.min())}")
    print(f"  max   : {int(raw_bytes.max())}")

    bayer = BayerCam.unpack_raw10(raw_bytes, CAPTURE_SIZE[0])

    print()
    print(f"--- bayer (after unpack_raw10)  format={RAWFORMAT} ---")
    print(f"  dtype : {bayer.dtype}")
    print(f"  shape : {bayer.shape}")
    print(f"  min   : {int(bayer.min())}")
    print(f"  max   : {int(bayer.max())}")
    print(f"  mean  : {bayer.mean():.1f}")
    if bayer.max() > 1023:
        print("  WARNING: values exceed 1023 — unpack may be incorrect")
    else:
        print("  OK: all values in 0–1023 range")

    layers = BayerCam.extract_bayer_layers(bayer)

    print()
    print("--- Bayer layers ---")
    print(f"  {'Layer':<5}  {'shape':<14}  {'min':>5}  {'#min':>6}  "
          f"{'max':>5}  {'#max':>6}  {'mean':>7}")
    print(f"  {'-'*5}  {'-'*14}  {'-'*5}  {'-'*6}  {'-'*5}  {'-'*6}  {'-'*7}")
    for name, arr in layers.items():
        mn, mx = int(arr.min()), int(arr.max())
        print(f"  {name:<5}  {str(arr.shape):<14}  "
              f"{mn:>5}  {int(np.count_nonzero(arr == mn)):>6}  "
              f"{mx:>5}  {int(np.count_nonzero(arr == mx)):>6}  "
              f"{arr.mean():>7.1f}")

    print()
    print("--- frame metadata ---")
    for k in ("ExposureTime", "AnalogueGain", "FrameDuration",
              "ColourGains", "SensorBlackLevels"):
        if k in meta:
            print(f"  {k}: {meta[k]}")


if __name__ == "__main__":
    main()
