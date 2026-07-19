#!/usr/bin/env python3
"""
camtest8.py — command-line front end for the multi-exposure ROI capture workflow.

The workflow itself lives in src/capture_runner.py; this script only parses
arguments, creates the hardware, pumps the runner's generator, and prints
progress. Keeping it thin means the same capture can be driven from the GUI
without duplicating any of the logic.

Performs automatic exposure bracketing for each LED color and saves only
the ROI_BOUND crop (all 4 Bayer channels) for each of 7 exposures, plus
a focused full-image JPEG at the median exposure (e3).

Output filenames (saved to OUT_DIR/<timestamp>/):
  camtest8_BOUND_{color}_{e_label}_{ms}ms_{ch}.npy   — lit ROI_BOUND per channel
  camtest8_BOUND_black_{e_label}_{ms}ms_{ch}.npy     — dark ROI_BOUND per channel
  camtest8_{color}_e3.jpg                            — focused full-image JPEG at e3
  camtest8_{color}_metadata.json                     — focus, channel, exposure analysis

Usage:
    python dev/camtest8.py [exposure_ms [led_start [led_end [focus_diopter]]]]

    exposure_ms   : initial exposure in ms  (default 10)
    led_start     : first LED channel 1-10  (default 1)
    led_end       : last  LED channel 1-10  (default led_start)
    focus_diopter : manual lens position in diopters; omit for autofocus
                    Higher = closer focus. IMX708 range ~0.0 (inf) to ~10+ (macro).

Examples:
    python dev/camtest8.py 10 1 1          # LED 1, autofocus, 10 ms initial
    python dev/camtest8.py 10 1 10 8.0     # all 10 LEDs, 8 diopters manual focus
    python dev/camtest8.py 2  1  1 10.0    # violet, close focus (minimum distance)
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bayer_cam import BayerCam
from capture_runner import (
    DEFAULT_EXPOSURE_MS, EXP_LABELS, CaptureConfig, CaptureRunner,
)
from lighting_cal8 import LEDBank
from utils import install_exit_signals

# ---------------------------------------------------------------------------
# Logging setup  (DEBUG goes to console; reduce to INFO for quieter runs)
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)-7s  %(message)s",
    stream=sys.stdout,
)


def _print_header(config: CaptureConfig) -> None:
    """Print the run parameters before any hardware is touched."""
    print("=" * 60)
    print("camtest8 — ROI multi-exposure capture")
    print("=" * 60)
    print(f"  Initial exp   : {config.exposure_ms:.0f} ms  "
          f"({config.initial_us:,} µs)")
    print(f"  Output        : {config.out_dir}")
    print(f"  LEDs          : {config.led_start} to {config.led_end}")
    if config.focus_diopter is not None:
        print(f"  Focus         : {config.focus_diopter:.2f} diopters (manual)")
    else:
        print("  Focus         : autofocus")
    print()


def _print_summary(runner: CaptureRunner) -> None:
    """Print the per-LED exposure table produced by a completed run."""
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"{'LED':<12} {'Ch':<4}" + "".join(f"  {lbl:>8}" for lbl in EXP_LABELS))
    print("-" * 60)
    for result in runner.results:
        vals = "".join(f"  {e // 1000:>6}ms" for e in result.exposures_us)
        print(f"{result.color:<12} {result.target_channel:<4}{vals}")
    print(f"\nOutput: {runner.out_dir}")


def main() -> None:
    install_exit_signals()

    exposure_ms   = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXPOSURE_MS
    led_start     = int(sys.argv[2])   if len(sys.argv) > 2 else 1
    led_end       = int(sys.argv[3])   if len(sys.argv) > 3 else led_start
    focus_diopter = float(sys.argv[4]) if len(sys.argv) > 4 else None

    config = CaptureConfig(
        exposure_ms=exposure_ms,
        led_start=led_start,
        led_end=led_end,
        focus_diopter=focus_diopter,
    )
    _print_header(config)

    with LEDBank() as leds:
        cam = BayerCam()
        try:
            runner = CaptureRunner(cam, leds, config)
            for progress in runner.run():
                # Transient updates are countdown ticks during a warmup wait;
                # rewrite the same line rather than scrolling the log.
                if progress.transient:
                    print(f"  {progress.message}", end="\r", flush=True)
                else:
                    print(f"  {progress.message}")
            _print_summary(runner)
        finally:
            cam.stop()
            print("\nCamera stopped.")

    print("Done.")


if __name__ == "__main__":
    main()
