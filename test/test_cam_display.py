#!/usr/bin/env python3
"""
test_cam_display.py  —  Camera-to-ILI9341 display test
-------------------------------------------------------
Captures a frame with BayerCam and renders the result on the ILI9341
touchscreen using the cal8 LED board for illumination.

Tests:
  preview   — full frame downsampled to 320×240 and shown on display
  layers    — each Bayer channel (B, Gb, Gr, R) shown in sequence
  roi       — a fixed crop rect extracted and shown on display

Usage:
  python3 test/test_cam_display.py preview           # white LED, default exposure
  python3 test/test_cam_display.py preview green 200 # green LED, 200 ms
  python3 test/test_cam_display.py layers white 100  # white LED, 100 ms
  python3 test/test_cam_display.py roi white 500
  python3 test/test_cam_display.py all white 200
"""

import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ili9341 import ILI9341
from bayer_cam import BayerCam
from lighting_cal8 import LEDBank
from geometry import Rect
from utils import install_exit_signals


DEFAULT_EXPOSURE_MS = 20
DEFAULT_CHANNEL     = 'white'
IMAGE_DISPLAY_DURATION = 4 # seconds to show the image on the display

# ROI in camera-pixel coordinates (adjust after first preview)
_ROI = Rect(x=600, y=400, w=400, h=300)


def _capture(cam: BayerCam, leds: LEDBank, channel, exposure_ms: float):
    """Light one LED channel, capture, return (rgb, bayer, meta)."""
    leds.set_channel(channel, True)
    time.sleep(0.2)
    rgb, bayer, meta = cam.capture_frame()
    leds.set_channel(channel, False)
    applied = int(meta.get("ExposureTime", 0))
    lbl = leds.label(channel)
    print(f"  {lbl}  {exposure_ms:.0f} ms  applied={applied/1000:.0f} ms")
    return rgb, bayer, meta


def test_preview(d: ILI9341, cam: BayerCam, leds: LEDBank,
                 channel, exposure_ms: float) -> None:
    """Downsample full RGB frame to 320×240 and show on display."""
    print(f"\n── preview ──────────────────────────────────────────────")
    rgb, bayer, meta = _capture(cam, leds, channel, exposure_ms)

    img = BayerCam.to_display_image(rgb, d.WIDTH, d.HEIGHT)
    d.draw_image(img, 0, 0)
    d.draw_text(leds.label(channel),
                Rect(0, 0, d.WIDTH, 20),
                color=d.YELLOW, size=12, align="left")
    time.sleep(IMAGE_DISPLAY_DURATION)
    print("  preview complete.")


def test_layers(d: ILI9341, cam: BayerCam, leds: LEDBank,
                channel, exposure_ms: float) -> None:
    """Show each Bayer channel in sequence (2 s each)."""
    print(f"\n── layers ───────────────────────────────────────────────")
    rgb, bayer, meta = _capture(cam, leds, channel, exposure_ms)

    layers = BayerCam.extract_bayer_layers(bayer, cam.bayer_pattern)
    for name, arr in layers.items():
        img = BayerCam.to_display_layer(arr, d.WIDTH, d.HEIGHT)
        d.draw_image(img, 0, 0)
        time.sleep(IMAGE_DISPLAY_DURATION)
        d.draw_text(f"Layer {name}  max={arr.max()}",
                    Rect(0, 0, d.WIDTH, 20),
                    color=d.YELLOW, size=12, align="left")
        print(f"  {name}: shape={arr.shape}  min={arr.min()}  max={arr.max()}"
              f"  mean={arr.mean():.1f}")
        time.sleep(2.0)
    print("  layers complete.")


def test_roi(d: ILI9341, cam: BayerCam, leds: LEDBank,
             channel, exposure_ms: float) -> None:
    """Crop _ROI from the frame, scale to display, and show."""
    print(f"\n── roi ({_ROI.x},{_ROI.y}  {_ROI.w}×{_ROI.h}) ────────────────────")
    rgb, bayer, meta = _capture(cam, leds, channel, exposure_ms)

    crop = BayerCam.crop_rgb(rgb, _ROI)
    if crop.size == 0:
        print("  ERROR: ROI is empty — adjust _ROI coordinates.")
        return

    img = BayerCam.to_display_image(crop, d.WIDTH, d.HEIGHT)
    d.draw_image(img, 0, 0)
    time.sleep(IMAGE_DISPLAY_DURATION)
    d.draw_text(f"ROI {_ROI.w}×{_ROI.h}",
                Rect(0, 0, d.WIDTH, 20),
                color=d.CYAN, size=12, align="left")
    print(f"  crop shape: {crop.shape}")
    print("  roi complete.")



TESTS = {
    "preview": test_preview,
    "layers":  test_layers,
    "roi":     test_roi,
}


def usage():
    print(__doc__)
    sys.exit(1)


if __name__ == "__main__":
    install_exit_signals()

    if len(sys.argv) < 2:
        usage()

    cmd = sys.argv[1].lower()
    if cmd not in TESTS and cmd != "all":
        print(f"Unknown test: '{cmd}'")
        usage()

    # optional: channel (name or int), exposure_ms
    raw_ch  = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_CHANNEL
    exp_ms  = float(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_EXPOSURE_MS
    exp_us  = int(exp_ms * 1000)

    try:
        channel = int(raw_ch)
    except ValueError:
        channel = raw_ch

    cam = BayerCam()
    cam.set_exposure(exp_us)
    cam.setup_camera()

    print(f"  Camera  : {cam.model_description}")
    print(f"  Pattern : {cam.bayer_pattern}")
    print(f"  Size    : {cam.config_size[0]}×{cam.config_size[1]}")

    with ILI9341() as d:
        with LEDBank() as leds:
            leds.all_off()

            focus_pos = cam.focus(leds=leds)
            if cam.has_autofocus:
                print(f"  Focus   : {focus_pos:.4f} diopters")

            try:
                if cmd == "all":
                    for name, fn in TESTS.items():
                        fn(d, cam, leds, channel, exp_ms)
                        time.sleep(0.5)
                else:
                    TESTS[cmd](d, cam, leds, channel, exp_ms)
            except KeyboardInterrupt:
                print("\nInterrupted.")
            finally:
                leds.all_off()
                cam.stop()
