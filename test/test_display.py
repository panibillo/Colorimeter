#!/usr/bin/env python3
"""
test_display.py  —  ILI9341 driver test suite
----------------------------------------------
Exercises the ILI9341 class from ili9341.py.

Usage:
  python3 test_display.py fill       # solid colour fills
  python3 test_display.py rects      # fill_rect and draw_rect
  python3 test_display.py lines      # draw_line
  python3 test_display.py text       # draw_text at various sizes and alignments
  python3 test_display.py image      # draw_image with a synthetic Pillow image
  python3 test_display.py backlight  # backlight ramp
  python3 test_display.py all        # run all tests in sequence
"""

import sys
import time
from PIL import Image, ImageDraw as PIDraw

from ili9341 import ILI9341
from geometry import Rect


def test_fill(d: ILI9341):
    print("\n── fill ─────────────────────────────────")
    for color, name in [
        (d.RED,     "Red"),
        (d.GREEN,   "Green"),
        (d.BLUE,    "Blue"),
        (d.CYAN,    "Cyan"),
        (d.MAGENTA, "Magenta"),
        (d.YELLOW,  "Yellow"),
        (d.WHITE,   "White"),
        (d.BLACK,   "Black"),
    ]:
        print(f"  {name}")
        d.fill_screen(color)
        time.sleep(0.8)
    print("  fill complete.")


def test_rects(d: ILI9341):
    print("\n── rects ────────────────────────────────")
    d.fill_screen(d.BLACK)
    # Filled bars across the top half
    bw = d.WIDTH // 8
    colours = [d.RED, d.ORANGE, d.YELLOW, d.GREEN,
               d.CYAN, d.BLUE, d.MAGENTA, d.WHITE]
    for i, c in enumerate(colours):
        d.fill_rect(Rect(i * bw, 0, bw, d.HEIGHT // 2), c)
    # Outlined rectangles in the lower half
    d.draw_rect(Rect(10,  d.HEIGHT//2 + 10, 80, 50), d.WHITE)
    d.draw_rect(Rect(110, d.HEIGHT//2 + 10, 80, 50), d.CYAN,   thickness=3)
    d.draw_rect(Rect(210, d.HEIGHT//2 + 10, 80, 50), d.YELLOW, fill=True)
    print("  Colour bars (top half), outlined + filled rects (lower half).")
    time.sleep(3)
    print("  rects complete.")


def test_lines(d: ILI9341):
    print("\n── lines ────────────────────────────────")
    d.fill_screen(d.BLACK)
    # Diagonal cross
    d.draw_line(0, 0, d.WIDTH - 1, d.HEIGHT - 1, d.CYAN)
    d.draw_line(d.WIDTH - 1, 0, 0, d.HEIGHT - 1, d.MAGENTA)
    # Border
    d.draw_line(0, 0, d.WIDTH - 1, 0, d.WHITE)
    d.draw_line(0, d.HEIGHT - 1, d.WIDTH - 1, d.HEIGHT - 1, d.WHITE)
    d.draw_line(0, 0, 0, d.HEIGHT - 1, d.WHITE)
    d.draw_line(d.WIDTH - 1, 0, d.WIDTH - 1, d.HEIGHT - 1, d.WHITE)
    print("  Diagonal cross + border.")
    time.sleep(3)
    print("  lines complete.")


def test_text(d: ILI9341):
    print("\n── text ─────────────────────────────────")
    d.fill_screen(d.BLACK)

    lines = [
        # (text,                        x,   y,   w,   h,  color,    bg,     sz, align,    valign)
        ("Colorimeter",                  0,   5, 320,  40, d.CYAN,   d.BLACK, 28, "center", "top"),
        ("ILI9341  320x240",            10,  50, 300,  25, d.WHITE,  d.BLACK, 16, "left",   "top"),
        (f"SPI {d.spi_hz//1_000_000} MHz  {d.model[:20]}",
                                        10,  78, 300,  22, d.YELLOW, d.BLACK, 14, "left",   "top"),
        ("Left aligned text",           10, 108, 200,  22, d.GREEN,  d.BLACK, 14, "left",   "top"),
        ("Right aligned",              110, 108, 200,  22, d.ORANGE, d.BLACK, 14, "right",  "top"),
        ("Centred",                      0, 135, 320,  22, d.WHITE,  d.BLACK, 14, "center", "top"),
        ("Big centred text",             0, 162, 320,  50, d.RED,    d.BLACK, 32, "center", "middle"),
    ]

    for text, x, y, w, h, color, bg, size, align, valign in lines:
        r = Rect(x, y, w, h)
        d.draw_text(text, r, color=color, bg=bg,
                    size=size, align=align, valign=valign)
        print(f"  '{text}'")

    time.sleep(4)
    print("  text complete.")


def test_image(d: ILI9341):
    print("\n── image ────────────────────────────────")
    d.fill_screen(d.BLACK)

    # Create a synthetic 160x120 test image
    img = Image.new("RGB", (160, 120), (20, 20, 60))
    draw = PIDraw.Draw(img)
    # Gradient-ish bands
    for i in range(8):
        x0 = i * 20
        colours = [(255,0,0),(255,128,0),(255,255,0),(0,255,0),
                   (0,255,255),(0,0,255),(255,0,255),(255,255,255)]
        draw.rectangle([x0, 0, x0+19, 119], fill=colours[i])
    # White border
    draw.rectangle([0, 0, 159, 119], outline=(255,255,255))
    # Label
    draw.text((4, 50), "Test image", fill=(0,0,0))

    # Display at top-left
    d.draw_image(img, 10, 10)
    print("  160x120 test image at (10,10)")

    # Display a copy at right side
    d.draw_image(img, 155, 60)
    print("  Second copy at (155,60) — partially off-screen clip test")

    time.sleep(3)
    print("  image complete.")


def test_backlight(d: ILI9341):
    print("\n── backlight ────────────────────────────")
    d.fill_screen(d.WHITE)
    d.draw_text("Backlight ramp",
                Rect(0, 95, d.WIDTH, 50),
                color=d.BLACK, bg=d.WHITE, size=20, align="center")
    print("  0 → 100 %")
    for pct in range(0, 101, 5):
        d.set_backlight(pct)
        time.sleep(0.06)
    print("  100 → 0 %")
    for pct in range(100, -1, -5):
        d.set_backlight(pct)
        time.sleep(0.06)
    d.set_backlight(80)
    print("  backlight complete.")


TESTS = {
    "fill":      test_fill,
    "rects":     test_rects,
    "lines":     test_lines,
    "text":      test_text,
    "image":     test_image,
    "backlight": test_backlight,
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
        print(f"Model : {d.model}")
        print(f"SPI   : {d.spi_hz // 1_000_000} MHz")
        try:
            if cmd == "all":
                for name, fn in TESTS.items():
                    print(f"\nRunning: {name}")
                    fn(d)
                    time.sleep(0.5)
            else:
                TESTS[cmd](d)
        except KeyboardInterrupt:
            print("\nInterrupted.")
