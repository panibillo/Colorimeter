#!/usr/bin/env python3
"""
test_widgets.py  —  Widget library test suite
----------------------------------------------
Exercises Screen, Label, Button, TextBox, and RectWidget from widgets.py.

Usage:
  python3 test_widgets.py screen    # basic screen render and toggle
  python3 test_widgets.py button    # buttons with touch callbacks
  python3 test_widgets.py textbox   # live-updating TextBox values
  python3 test_widgets.py overlay   # RectWidget overlaid on an image
  python3 test_widgets.py all       # run all tests in sequence
"""

import sys
import time
import math
from PIL import Image, ImageDraw as PIDraw

from ili9341 import ILI9341
from tsc2007 import TSC2007
from widgets  import Screen, Label, Button, TextBox, RectWidget
from geometry import Rect


def test_screen(d: ILI9341, t: TSC2007):
    """Build two screens and toggle between them on each tap."""
    print("\n── screen (3 taps or 30 s) ──────────────")

    # Screen A — instrument view with a dark blue background
    screen_a = Screen(bg_color=d.rgb(0, 0, 80), name="ScreenA")
    screen_a.add(Label(
        Rect(0, 5, d.WIDTH, 40), "Screen A — Instrument View",
        color=d.CYAN, size=20, align="center"))
    screen_a.add(Label(
        Rect(0, 60, d.WIDTH, 30), "Tap anywhere to switch",
        color=d.LTGRAY, size=14, align="center"))

    # Screen B — results view with a dark green background
    screen_b = Screen(bg_color=d.rgb(0, 60, 0), name="ScreenB")
    screen_b.add(Label(
        Rect(0, 5, d.WIDTH, 40), "Screen B — Results View",
        color=d.YELLOW, size=20, align="center"))
    screen_b.add(Label(
        Rect(0, 60, d.WIDTH, 30), "Tap anywhere to switch",
        color=d.LTGRAY, size=14, align="center"))

    screens = [screen_a, screen_b]
    current = 0
    screens[current].render(d)

    deadline = time.monotonic() + 30
    taps     = 0

    while time.monotonic() < deadline and taps < 6:
        if t.is_touched:
            taps   += 1
            current = 1 - current
            print(f"  tap {taps} → {screens[current].name}")
            screens[current].render(d)
            while t.is_touched:
                time.sleep(0.05)
            time.sleep(0.1)
        else:
            time.sleep(0.05)

    print("  screen complete.")


def test_button(d: ILI9341, t: TSC2007):
    """Three buttons; each updates a counter label when tapped."""
    print("\n── button (30 s) ────────────────────────")

    counts = [0, 0, 0]

    screen = Screen(bg_color=d.BLACK)
    screen.add(Label(Rect(0, 5, d.WIDTH, 35), "Button test",
                     color=d.WHITE, size=22, align="center"))

    counter_labels = []
    for i, (label, color, x) in enumerate([
        ("Red",   d.RED,   10),
        ("Green", d.GREEN, 115),
        ("Blue",  d.BLUE,  220),
    ]):
        btn = Button(
            Rect(x, 60, 90, 45), label,
            color=color, text_color=d.WHITE,
            border_color=d.WHITE, size=16,
            on_press=lambda i=i: _increment(i, counts, counter_labels, d))
        screen.add(btn)

        lbl = TextBox(Rect(x, 120, 90, 35), "0",
                      color=d.WHITE, bg=d.BLACK,
                      size=24, align="center")
        counter_labels.append(lbl)
        screen.add(lbl)

    screen.add(Label(Rect(0, 170, d.WIDTH, 25), "Tap a button to count",
                     color=d.GRAY, size=13, align="center"))
    screen.render(d)
    time.sleep(0.2)

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        pos = t.wait_for_touch(timeout=0.1)
        if pos:
            screen.handle_touch(*pos)

    print(f"  counts: Red={counts[0]} Green={counts[1]} Blue={counts[2]}")
    print("  button complete.")


def _increment(i, counts, labels, display):
    counts[i] += 1
    labels[i].set_value(str(counts[i]))
    labels[i].draw(display)


def test_textbox(d: ILI9341, t: TSC2007):
    """TextBox showing simulated sensor readings updating in real time."""
    print("\n── textbox (15 s) ───────────────────────")

    screen = Screen(bg_color=d.BLACK)
    screen.add(Label(Rect(0, 5, d.WIDTH, 35), "Live sensor readout",
                     color=d.WHITE, size=18, align="center"))
    screen.add(Label(Rect(5, 52, 140, 22), "Temperature:",
                     color=d.LTGRAY, size=13))
    screen.add(Label(Rect(5, 102, 140, 22), "Pressure:",
                     color=d.LTGRAY, size=13))
    screen.add(Label(Rect(5, 152, 140, 22), "Elapsed:",
                     color=d.LTGRAY, size=13))

    tb_temp = TextBox(Rect(5, 72, 230, 28), "---",
                      color=d.CYAN,   size=22, border_color=d.DKGRAY)
    tb_pres = TextBox(Rect(5, 122, 230, 28), "---",
                      color=d.YELLOW, size=22, border_color=d.DKGRAY)
    tb_time = TextBox(Rect(5, 172, 230, 28), "00:00",
                      color=d.WHITE,  size=22, border_color=d.DKGRAY)

    screen.add(tb_temp)
    screen.add(tb_pres)
    screen.add(tb_time)
    screen.render(d)
    time.sleep(0.2)

    start    = time.monotonic()
    deadline = start + 15

    while time.monotonic() < deadline:
        elapsed = time.monotonic() - start
        temp    = 22.5 + 1.5 * math.sin(elapsed * 0.3)
        pres    = 1013.2 + 0.8 * math.cos(elapsed * 0.2)
        mins    = int(elapsed) // 60
        secs    = int(elapsed) % 60

        tb_temp.set_value(f"{temp:+.2f} °C")
        tb_pres.set_value(f"{pres:.1f} hPa")
        tb_time.set_value(f"{mins:02d}:{secs:02d}")

        tb_temp.draw(d)
        tb_pres.draw(d)
        tb_time.draw(d)

        time.sleep(0.25)

    print("  textbox complete.")


def test_overlay(d: ILI9341, t: TSC2007):
    """
    RectWidget drawn over a synthetic image.
    Tap the screen to move the overlay rectangle to the tap position.
    """
    print("\n── overlay (30 s) ───────────────────────")
    print("  Tap screen to move the overlay rectangle.")

    # Synthetic 200x160 image
    img = Image.new("RGB", (200, 160), (20, 20, 80))
    draw = PIDraw.Draw(img)
    for row in range(8):
        for col in range(10):
            c = (col * 25, row * 20, 128)
            draw.rectangle([col*20, row*20, col*20+19, row*20+19], fill=c)
    draw.rectangle([0, 0, 199, 159], outline=(200, 200, 200))

    screen = Screen(bg_color=d.BLACK)
    screen.add(Label(Rect(0, 5, d.WIDTH, 30), "Tap to move overlay",
                     color=d.WHITE, size=14, align="center"))

    img_x, img_y = 10, 35
    overlay = RectWidget(Rect(img_x + 30, img_y + 30, 60, 40),
                         color=d.RED, thickness=2)
    screen.add(overlay)

    d.draw_image(img, img_x, img_y)
    screen.render(d)
    # Redraw image (screen.render draws bg_color=BLACK over it)
    d.draw_image(img, img_x, img_y)
    overlay.draw(d)
    time.sleep(0.2)

    deadline = time.monotonic() + 30

    while time.monotonic() < deadline:
        pos = t.wait_for_touch(timeout=0.1)
        if pos:
            px, py = pos
            # Centre the overlay rect on the tap point
            new_x = max(img_x, min(px - 30, img_x + 200 - 60))
            new_y = max(img_y, min(py - 20, img_y + 160 - 40))
            print(f"  tap ({px},{py}) → overlay at ({new_x},{new_y})")
            # Erase old overlay by redrawing image region
            d.draw_image(img, img_x, img_y)
            overlay.move_to(new_x, new_y)
            overlay.draw(d)

    print("  overlay complete.")


TESTS = {
    "screen":  test_screen,
    "button":  test_button,
    "textbox": test_textbox,
    "overlay": test_overlay,
}

def usage():
    print(__doc__)
    sys.exit(1)

if __name__ == "__main__":
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
                for name, fn in TESTS.items():
                    print(f"\nRunning: {name}")
                    fn(d, t)
                    time.sleep(0.5)
            else:
                TESTS[cmd](d, t)
        except KeyboardInterrupt:
            print("\nInterrupted.")
        finally:
            t.cleanup()
