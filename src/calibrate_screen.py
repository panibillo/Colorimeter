#!/usr/bin/env python3
"""
calibrate_screen.py  —  Touch-screen calibration runner.

Runs the TSC2007 corner-tap calibration, validates the BR residual, and
saves the result through touch_cal_store: the existing file is copied to
touch_cal.bakN.json first, and the new one replaces it in one step, so the
old values are never lost.

The app has the same calibration, with a trial before saving, at
Menu → Advanced → Calibrate Touchpad.

Exit codes
----------
0  — calibration written successfully
1  — calibration failed (collinear / coincident touch points)
2  — calibration rejected (BR residual exceeded --max-error threshold)
3  — hardware or file error

Usage
-----
    python3 src/calibrate_screen.py
    python3 src/calibrate_screen.py --info
    python3 src/calibrate_screen.py --max-error 12
    python3 src/calibrate_screen.py --cal-path /tmp/test_cal.json
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import touch_cal_store
from tsc2007 import CAL_MAX_ERR_PX, TSC2007, _DEFAULT_CAL_PATH

_LOG_FORMAT = "%(levelname)-8s %(name)s:%(lineno)d  %(message)s"

# Default maximum acceptable BR residual in display pixels.
# Taps that land further than this from the true corner indicate a poor fit.
_DEFAULT_MAX_ERROR = float(CAL_MAX_ERR_PX)

log = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run TSC2007 touch-screen calibration and save the result."
    )
    p.add_argument(
        "--max-error", type=float, default=_DEFAULT_MAX_ERROR, metavar="PX",
        help=f"maximum BR residual in pixels before rejecting (default {_DEFAULT_MAX_ERROR})",
    )
    p.add_argument(
        "--cal-path", default=_DEFAULT_CAL_PATH, metavar="PATH",
        help="calibration JSON output path (default: %(default)s)",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--debug",   action="store_true", help="set log level to DEBUG")
    g.add_argument("--info",    action="store_true", help="set log level to INFO")
    g.add_argument("--warning", action="store_true", help="set log level to WARNING (default)")
    return p.parse_args()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    if args.warning:
        level = logging.WARNING
    elif args.info:
        level = logging.INFO
    else:
        level = logging.DEBUG
    logging.basicConfig(level=level, format=_LOG_FORMAT)

    # Import display driver — it requires SPI hardware; fail early if missing.
    try:
        from ili9341 import ILI9341
    except ImportError as exc:
        print(f"ERROR: cannot import ILI9341 driver: {exc}", file=sys.stderr)
        sys.exit(3)

    # ── Run calibration (save=False so we validate before writing) ────────────
    log.info("opening hardware")
    try:
        with ILI9341() as display, TSC2007(cal_path=args.cal_path) as touch:
            result = touch.calibrate(display, save=False)
    except Exception as exc:
        print(f"ERROR: hardware failure during calibration: {exc}", file=sys.stderr)
        log.debug("exception detail", exc_info=True)
        sys.exit(3)

    # ── Validate ──────────────────────────────────────────────────────────────
    if result is None:
        print(
            "\nCalibration aborted — a corner had no steady reading, or the\n"
            "touch points were collinear or coincident.\n"
            "Please retry, tapping each crosshair as precisely as possible.",
            file=sys.stderr,
        )
        sys.exit(1)

    cal, err_x, err_y = result
    err_mag = (err_x ** 2 + err_y ** 2) ** 0.5
    print(f"\nBR residual:  ({err_x:+.1f}, {err_y:+.1f}) px   magnitude = {err_mag:.1f} px")

    if abs(err_x) > args.max_error or abs(err_y) > args.max_error:
        print(
            f"REJECTED — residual exceeds the {args.max_error:.0f} px limit.\n"
            "The calibration file was NOT written.\n"
            "Please retry, tapping each crosshair as precisely as possible.",
            file=sys.stderr,
        )
        sys.exit(2)

    # ── Back up the existing file, then replace it ────────────────────────────
    try:
        bak = touch_cal_store.save(cal, args.cal_path)
    except touch_cal_store.CalWriteError as exc:
        print(f"ERROR: {exc}",
              file=sys.stderr)
        sys.exit(3)

    if bak:
        print(f"Previous calibration backed up to: {bak}")
    print(f"Calibration saved to: {args.cal_path}")
    log.info("calibration complete")


if __name__ == "__main__":
    main()
