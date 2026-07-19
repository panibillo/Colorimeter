#!/usr/bin/env python3
"""
camtest7.py — Single-exposure capture test using BayerCam + cal8 LED board.

Captures one image under the white LED.  Auto-detects IMX219 (Module 2)
or IMX708 (Module 3) and configures the correct raw format and size.
Saves JPG + Bayer NPY + metadata TXT to dev/camtest7/.

Usage:
    python dev/camtest7.py [exposure_ms]
    python dev/camtest7.py          # default 500 ms
    python dev/camtest7.py 1000     # 1 second
    python dev/camtest7.py 50       # 50 ms (dim scenes)
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
from bayer_cam import BayerCam
from lighting_cal8 import LEDBank
from utils import install_exit_signals

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_EXPOSURE_MS = 10       # ms — sensible default for Module 3
LED_WARMUP_S        = 0.3       # seconds after LED on before capture
FLUSH_FRAMES        = 2         # dummy frames to discard before measuring

OUT_DIR  = Path(__file__).parent / "camtest7"
OUT_STEM = "camtest7"

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    install_exit_signals()

    exposure_ms = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXPOSURE_MS
    exposure_us = int(exposure_ms * 1000)
    led_start = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    led_end = int(sys.argv[3])  if len(sys.argv) > 3 else led_start

    print("=" * 60)
    print("camtest7 — BayerCam + cal8 LED capture test")
    print("=" * 60)
    print(f"  Exposure : {exposure_ms:.0f} ms  ({exposure_us:,} µs)")
    print(f"  Output   : {OUT_DIR.resolve()}")
    print(f"  LEDS     : [{led_start} to {led_end}]")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with LEDBank() as leds:
        cam = BayerCam()
        try:
            # ── Camera init ───────────────────────────────────────────────
            cam.set_exposure(exposure_us)
            cam.setup_camera()
            size = cam.config_size
            print(f"  Camera  : {cam.model_description}")
            print(f"  Pattern : {cam.bayer_pattern}")
            print(f"  Size    : {size[0]}×{size[1]}")
            print(f"  Max exp : {cam.MAX_EXPOSURE_US/1e6:.1f} s")
            print()

            # ── Focus ─────────────────────────────────────────────────────
            focus_pos = cam.focus(force=True, leds=leds)
            if cam.has_autofocus:
                print(f"  Focus   : {focus_pos:.4f} diopters")
            print()

            # ── Flush dummy frames ────────────────────────────────────────
            print(f"Flushing {FLUSH_FRAMES} frames ...")
            for n in range(FLUSH_FRAMES):
                t0 = time.monotonic()
                cam.capture_frame()
                print(f"  flush {n+1}: {(time.monotonic()-t0)*1000:.0f} ms")
            print()

            for led in range(led_start, led_end + 1):
                
                # ── LED on ────────────────────────────────────────────────────
                leds.set_channel(led, True)
                print(f"{leds.label(led)} (ch {led}) ON  —  warming up {LED_WARMUP_S:.1f} s ...")
                time.sleep(LED_WARMUP_S)

                # ── Capture ───────────────────────────────────────────────────
                print(f"Capturing ({exposure_ms:.0f} ms) ...")
                t0 = time.monotonic()
                rgb, bayer, meta = cam.capture_frame()
                cap_ms = (time.monotonic() - t0) * 1000
                print(f"  Done in {cap_ms:.0f} ms")

                # ── LED off ───────────────────────────────────────────────────
                leds.set_channel(led, False)
                print(f"{leds.label(led)} (ch {led}) OFF")
                print("processing image ...")

                # ── Metadata ──────────────────────────────────────────────────
                applied_us = int(meta.get("ExposureTime", 0))
                delta_pct  = (applied_us - exposure_us) / max(exposure_us, 1) * 100
                print("--- metadata ---")
                print(f"  Requested : {exposure_us:,} µs  ({exposure_ms:.0f} ms)")
                print(f"  Applied   : {applied_us:,} µs  ({delta_pct:+.1f}%  "
                    f"{'OK' if abs(delta_pct) <= 5.0 else 'HIGH'})")
                print(f"  Gain      : {meta.get('AnalogueGain', '?')}")
                print(f"  Lux       : {meta.get('Lux', '?')}")
                if cam.lens_position is not None:
                    print(f"  Lens pos  : {cam.lens_position:.3f} diopters")
                print()

                # ── Bayer channel means (pattern-aware) ───────────────────────
                layers = BayerCam.extract_bayer_layers(bayer, cam.bayer_pattern)
                b_mean = float(layers['B'].mean())
                g_mean = float(np.concatenate(
                    [layers['Gb'].ravel(), layers['Gr'].ravel()]).mean())
                r_mean = float(layers['R'].mean())
                bgr_m  = rgb.astype(float).mean(axis=(0, 1))
                print("--- channel means ---")
                print(f"  Bayer  B={b_mean:.1f}  G={g_mean:.1f}  R={r_mean:.1f}"
                      f"  max={bayer.max()}  dtype={bayer.dtype}")
                print(f"  RGB888 B={bgr_m[0]:.1f}  G={bgr_m[1]:.1f}  R={bgr_m[2]:.1f}")
                print()

                # ── Save ──────────────────────────────────────────────────────
                stem = f"{OUT_STEM}-{led}-{exposure_us}"
                saved = BayerCam.save_frame(
                    OUT_DIR, stem, rgb, bayer, meta,
                    lens_position=cam.lens_position)
                print("--- saved ---")
                for kind, path in saved.items():
                    print(f"  [{kind}] {path}")

        finally:
            cam.stop()
            print("\nCamera stopped.")

    print("Done.")


if __name__ == "__main__":
    main()
