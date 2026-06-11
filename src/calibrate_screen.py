#!/usr/bin/env python3
"""
calibrate_screen.py  —  Touch-screen calibration runner.

Runs the two-phase TSC2007 calibration routine (pressure threshold + affine
corner taps), validates the BR residual, backs up any existing calibration
file, and writes the new one.

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
import json
import logging
import os
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from tsc2007 import TSC2007, _DEFAULT_CAL_PATH

_LOG_FORMAT = "%(levelname)-8s %(name)s:%(lineno)d  %(message)s"

# Default maximum acceptable BR residual in display pixels.
# Taps that land further than this from the true corner indicate a poor fit.
_DEFAULT_MAX_ERROR = 10.0

log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _backup_existing(cal_path: str) -> str | None:
    """
    Rename the existing calibration file to an unused backup name.
    Pattern: touch_cal.bak1.json, touch_cal.bak2.json, …

    Returns the backup path on success, or None if no file existed.
    Raises OSError if the rename fails.
    """
    p = pathlib.Path(cal_path)
    if not p.exists():
        return None

    parent, stem, suffix = p.parent, p.stem, p.suffix
    n = 1
    while True:
        bak = parent / f"{stem}.bak{n}{suffix}"
        if not bak.exists():
            break
        n += 1

    shutil.move(str(p), str(bak))
    log.info("backed up %s → %s", p.name, bak.name)
    return str(bak)


def _write_cal(cal: dict, cal_path: str) -> None:
    """Write calibration dict to JSON, creating parent directories as needed."""
    os.makedirs(os.path.dirname(os.path.abspath(cal_path)), exist_ok=True)
    with open(cal_path, "w") as f:
        json.dump(cal, f, indent=2)


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
            "\nCalibration aborted — touch points were collinear or coincident.\n"
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

    # ── Back up existing file, then write new one ─────────────────────────────
    try:
        bak = _backup_existing(args.cal_path)
    except OSError as exc:
        print(f"ERROR: could not back up existing calibration: {exc}", file=sys.stderr)
        sys.exit(3)

    if bak:
        print(f"Previous calibration backed up to: {bak}")

    try:
        _write_cal(cal, args.cal_path)
    except OSError as exc:
        print(f"ERROR: could not write calibration file: {exc}", file=sys.stderr)
        # If the backup succeeded, the original is now gone — warn the user.
        if bak:
            print(f"WARNING: original was already moved to {bak}", file=sys.stderr)
        sys.exit(3)

    print(f"Calibration saved to: {args.cal_path}")
    log.info("calibration complete")


if __name__ == "__main__":
    main()
