#!/usr/bin/env python3
"""
test_touch.py  —  TSC2007 touch driver test suite
--------------------------------------------------
Exercises the TSC2007 class from tsc2007.py.
Requires the ILI9341 display to show feedback on screen.

Usage:
  python3 test_touch.py calibrate   # run interactive calibration, save to JSON
  python3 test_touch.py poll        # poll touch position for 30 s
  python3 test_touch.py irq         # wait for TSIRQ interrupt, show result
  python3 test_touch.py drag        # track continuous drag positions for 30 s
  python3 test_touch.py all         # calibrate then run poll and irq
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import time

from ili9341 import ILI9341
from tsc2007 import TSC2007
from geometry import Rect
from utils import install_exit_signals


def test_calibrate(d: ILI9341, t: TSC2007):
    print("\n── calibrate ────────────────────────────")
    print("  Follow on-screen instructions.")
    cal = t.calibrate(d)
    print(f"  Saved: {cal}")
    print("  calibrate complete.")


def test_poll(d: ILI9341, t: TSC2007):
    print("\n── poll (30 s) ──────────────────────────")
    print("  Touch the screen. Dots appear at touch points.")
    d.fill_screen(d.BLACK)
    d.draw_text("Touch the screen",
                Rect(0, 95, d.WIDTH, 50),
                color=d.CYAN, size=20, align="center")
    time.sleep(0.2)

    deadline = time.monotonic() + 30
    last     = (None, None)

    while time.monotonic() < deadline:
        pos = t.position()
        if pos:
            px, py = pos
            print(f"  pixel=({px:3d},{py:3d})")
            if (px, py) != last:
                d.fill_rect(Rect(max(0, px-3), max(0, py-3), 7, 7), d.YELLOW)
                last = (px, py)
        time.sleep(0.02)

    print("  poll complete.")


def test_irq(d: ILI9341, t: TSC2007):
    print("\n── irq (30 s) ───────────────────────────")
    print("  Waiting for TSIRQ on GPIO4. Tap the screen.")
    d.fill_screen(d.BLACK)
    d.draw_text("Waiting for tap...",
                Rect(0, 85, d.WIDTH, 40),
                color=d.MAGENTA, size=18, align="center")
    time.sleep(0.2)

    deadline = time.monotonic() + 30
    hit      = 0

    while time.monotonic() < deadline:
        if t.is_touched:
            pos = t.position()
            if pos:
                hit += 1
                px, py = pos
                print(f"  tap #{hit:3d}  pixel=({px:3d},{py:3d})")
                d.fill_screen(d.BLACK)
                d.draw_text(f"Tap #{hit}",
                            Rect(0, 70, d.WIDTH, 50),
                            color=d.GREEN, size=28, align="center")
                d.draw_text(f"x={px}  y={py}",
                            Rect(0, 125, d.WIDTH, 35),
                            color=d.WHITE, size=20, align="center")
            # Wait for release
            while t.is_touched:
                time.sleep(0.02)
            time.sleep(0.05)
        else:
            time.sleep(0.02)

    if hit == 0:
        print("  No taps detected.")
        print("  Check: TSIRQ/#2 jumper on 1651? GPIO4 → J1-Pin10?")
    else:
        print(f"  {hit} tap(s) detected.")
    print("  irq complete.")


def test_drag(d: ILI9341, t: TSC2007):
    print("\n── drag (30 s) ──────────────────────────")
    print("  Drag your finger across the screen.")
    d.fill_screen(d.BLACK)
    d.draw_text("Drag to draw",
                Rect(0, 95, d.WIDTH, 50),
                color=d.CYAN, size=20, align="center")
    time.sleep(0.2)

    deadline = time.monotonic() + 30
    last_pos = None

    while time.monotonic() < deadline:
        if t.is_touched:
            for px, py in t.track():
                d.fill_rect(Rect(max(0, px-1), max(0, py-1), 3, 3), d.WHITE)
                if last_pos:
                    d.draw_line(last_pos[0], last_pos[1], px, py, d.WHITE)
                last_pos = (px, py)
                if time.monotonic() >= deadline:
                    break
            last_pos = None
        else:
            time.sleep(0.02)

    print("  drag complete.")


TESTS = {
    "calibrate": test_calibrate,
    "poll":      test_poll,
    "irq":       test_irq,
    "drag":      test_drag,
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
        print(f"Unknown test: '{cmd}'");  usage()

    with ILI9341() as d:
        t = TSC2007()
        t.init()
        print(f"Model : {d.model}")
        try:
            if cmd == "all":
                test_calibrate(d, t)
                time.sleep(0.5)
                test_poll(d, t)
                time.sleep(0.5)
                test_irq(d, t)
            else:
                TESTS[cmd](d, t)
        except KeyboardInterrupt:
            print("\nInterrupted.")
        finally:
            t.cleanup()
