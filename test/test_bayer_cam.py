#!/usr/bin/env python3
"""
test_bayer_cam.py  —  BayerCam live capture test
-------------------------------------------------
Captures one frame using BayerCam (auto-detects IMX219 or IMX708),
unpacks the Bayer raw data, and reports statistics for each of the four
color sub-arrays.  Illumination is controlled via the cal8 LED board.

Usage:
  python3 test/test_bayer_cam.py <channel> [half|full] [exposure_ms]

  channel     : natural | white | walk | all | <color-name> | <ch-number>
  half | full : sensor resolution (default: half)
  exposure_ms : exposure in milliseconds (default: 50)

Examples:
  python3 test/test_bayer_cam.py natural            # ambient, half-res
  python3 test/test_bayer_cam.py white              # white LED, half-res
  python3 test/test_bayer_cam.py white full         # white LED, full-res
  python3 test/test_bayer_cam.py green 200          # green LED, half-res, 200 ms
  python3 test/test_bayer_cam.py green full 200     # green LED, full-res, 200 ms
  python3 test/test_bayer_cam.py 8 100              # ch 8, half-res, 100 ms
  python3 test/test_bayer_cam.py walk full          # walk all channels, full-res
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import time
import numpy as np

from bayer_cam import BayerCam
from lighting_cal8 import LEDBank
from utils import install_exit_signals


DEFAULT_EXPOSURE_MS = 50     # ms — enough for a lit scene with the cal8 board


def _report(cam: BayerCam, rgb: np.ndarray, bayer: np.ndarray,
            meta: dict, label: str) -> None:
    """Print bayer unpack verification and per-layer statistics."""
    print()
    print(f"--- raw bayer array ({label}) ---")
    print(f"  dtype : {bayer.dtype}")
    print(f"  shape : {bayer.shape}")
    print(f"  min   : {int(bayer.min())}")
    print(f"  max   : {int(bayer.max())}")
    print(f"  mean  : {bayer.mean():.1f}")
    if bayer.max() > 1023:
        print("  WARNING: values exceed 1023 — unpack may be incorrect")
    else:
        print("  OK: all values in 0–1023 range")

    layers = BayerCam.extract_bayer_layers(bayer, cam.bayer_pattern)

    print()
    print(f"--- Bayer layers (pattern={cam.bayer_pattern}) ---")
    print(f"  {'Layer':<5}  {'shape':<14}  {'min':>5}  {'#min':>6}  "
          f"{'max':>5}  {'#max':>6}  {'mean':>7}")
    print(f"  {'-'*5}  {'-'*14}  {'-'*5}  {'-'*6}  {'-'*5}  {'-'*6}  {'-'*7}")
    for name, arr in layers.items():
        mn, mx = int(arr.min()), int(arr.max())
        print(f"  {name:<5}  {str(arr.shape):<14}  "
              f"{mn:>5}  {int(np.count_nonzero(arr == mn)):>6}  "
              f"{mx:>5}  {int(np.count_nonzero(arr == mx)):>6}  "
              f"{arr.mean():>7.1f}")

    bgr_m = rgb.astype(float).mean(axis=(0, 1))
    print()
    print(f"--- RGB888 means ---")
    print(f"  B={bgr_m[0]:.1f}  G={bgr_m[1]:.1f}  R={bgr_m[2]:.1f}")

    print()
    print("--- frame metadata ---")
    for k in ("ExposureTime", "AnalogueGain", "FrameDuration",
              "ColourGains", "SensorBlackLevels"):
        if k in meta:
            print(f"  {k}: {meta[k]}")


def test_natural(cam: BayerCam, exposure_ms: float) -> None:
    """Capture under ambient light (no LEDs)."""
    print(f"\n── natural light  ({exposure_ms:.0f} ms) ──────────────────────────")
    rgb, bayer, meta = cam.capture_frame()
    _report(cam, rgb, bayer, meta, "natural")
    print("  natural complete.")


def test_channel(cam: BayerCam, leds: LEDBank,
                 channel, exposure_ms: float) -> None:
    """Capture under one LED channel."""
    label = leds.label(channel)
    print(f"\n── {label}  ({exposure_ms:.0f} ms) ──────────────────────────")
    leds.set_channel(channel, True)
    time.sleep(0.2)
    rgb, bayer, meta = cam.capture_frame()
    leds.set_channel(channel, False)
    _report(cam, rgb, bayer, meta, label)
    print(f"  {label} complete.")


def test_walk(cam: BayerCam, leds: LEDBank, exposure_ms: float) -> None:
    """Capture one frame per LED channel (channels 1–10)."""
    print(f"\n── walk all channels  ({exposure_ms:.0f} ms each) ──────────────")
    for ch in range(1, 11):
        test_channel(cam, leds, ch, exposure_ms)
        time.sleep(0.1)
    print("  walk complete.")


TESTS = {
    "natural":  test_natural,
    "walk":     test_walk,
}

def usage():
    print(__doc__)
    sys.exit(1)


if __name__ == "__main__":
    install_exit_signals()

    if len(sys.argv) < 2:
        usage()

    cmd = sys.argv[1].lower()

    # Optional size flag ('half' or 'full') followed by optional exposure_ms.
    remaining = sys.argv[2:]
    size_str = "half"
    if remaining and remaining[0] in ("half", "full"):
        size_str = remaining[0]
        remaining = remaining[1:]
    exposure_ms = float(remaining[0]) if remaining else DEFAULT_EXPOSURE_MS
    exposure_us = int(exposure_ms * 1000)

    print("=" * 56)
    print(f"test_bayer_cam  —  {cmd}  [{size_str}]")
    print("=" * 56)

    cam = BayerCam()
    cam.config = cam.CONFIG_6 if size_str == "full" else cam.CONFIG_5
    cam.set_exposure(exposure_us)
    cam.setup_camera()

    size = cam.config_size
    print(f"  Camera  : {cam.model_description}")
    print(f"  Pattern : {cam.bayer_pattern}")
    print(f"  Size    : {size[0]}×{size[1]}")
    print(f"  Exposure: {exposure_ms:.0f} ms")

    try:
        with LEDBank() as leds:
            leds.all_off()

            focus_pos = cam.focus(leds=leds)
            if cam.has_autofocus:
                print(f"  Focus   : {focus_pos:.4f} diopters")

            try:
                if cmd == "natural":
                    test_natural(cam, exposure_ms)
                elif cmd == "walk":
                    test_walk(cam, leds, exposure_ms)
                elif cmd == "all":
                    test_natural(cam, exposure_ms)
                    time.sleep(0.3)
                    test_walk(cam, leds, exposure_ms)
                else:
                    # treat as a channel name or number
                    try:
                        ch = int(cmd)
                    except ValueError:
                        ch = cmd
                    test_channel(cam, leds, ch, exposure_ms)
            except KeyboardInterrupt:
                print("\nInterrupted.")
            finally:
                leds.all_off()
    finally:
        cam.stop()
