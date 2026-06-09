#!/usr/bin/env python3
"""
proto_screen.py  —  Measurement-phase screen built with the widget system.
--------------------------------------------------------------------------
Draws header / body / status-bar using Area, Button, Label, and TextBox
widgets.  Touch events print to the log; no real measurement logic.

Hardware: ILI9341 320x240 landscape on RPi Zero W / Zero 2 W.

    python3 dev/proto_screen.py

Press Ctrl-C to exit.
"""

import logging
import signal
import sys
import time
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from gpiozero import DigitalOutputDevice
from ili9341 import ILI9341
from geometry import Rect
from widgets import Area, Button, Label, Screen, TextBox
from screen_manager import ScreenManager

log = logging.getLogger(__name__)

# ── Hardware ──────────────────────────────────────────────────────────────────
BL_PIN = 13

# ── Display geometry ──────────────────────────────────────────────────────────
W, H     = 320, 240
HDR_H    = 50
STATUS_H = 25
BODY_Y   = HDR_H
BODY_H   = H - HDR_H - STATUS_H   # 165 px
STATUS_Y = H - STATUS_H            # 215

# ── Colour palette ────────────────────────────────────────────────────────────
_c = ILI9341.rgb

TRUE_BLUE = _c(  0,   0, 255)   # slot-A square fill
HDR_BG    = _c(100, 155, 225)   # header strip background
HDR_A_CLR = _c(173, 216, 250)   # "A" glyph
DARK_BLUE = _c(  0,   0, 110)   # header text
BODY_BG   = _c( 18,  28,  48)   # dark body background
STATUS_BG = _c( 28,  38,  58)   # status bar background
BTN_GRAY  = _c( 65,  85, 115)   # MENU / NEXT buttons
BTN_TEXT  = _c(210, 225, 240)   # status bar text
START_BG  = _c( 25, 150,  25)   # Start button
COUNTDOWN = _c(255, 220,  40)   # countdown digits
BODY_TEXT = _c(195, 210, 235)   # instruction text
BLACK     = ILI9341.BLACK


def _make_status_area() -> Area:
    status = Area(Rect(0, STATUS_Y, W, STATUS_H), bg_color=STATUS_BG, name="status")
    status.add(Button(Rect(2, STATUS_Y + 2, 58, STATUS_H - 4), "MENU",
                      color=BTN_GRAY, text_color=BTN_TEXT, size=13,
                      name="menu",
                      on_press=lambda: log.info("MENU pressed")))
    status.add(TextBox(Rect(110, STATUS_Y + 2, 100, STATUS_H - 4), "12:34:56",
                       color=BTN_TEXT, bg=STATUS_BG, size=13,
                       align="center", name="clock"))
    status.add(Button(Rect(W - 60, STATUS_Y + 2, 58, STATUS_H - 4), "NEXT",
                      color=BTN_GRAY, text_color=BTN_TEXT, size=13,
                      name="next_btn",
                      on_press=lambda: log.info("NEXT pressed")))
    return status


def build_screen() -> Screen:
    screen = Screen(bg_color=BLACK, name="measure_screen")

    # Header: light-blue strip + slot-A square
    strip_w = W - HDR_H   # 270
    header  = Area(Rect(0, 0, W, HDR_H), bg_color=HDR_BG, name="header")
    header.add(Label(Rect(8, 5, strip_w - 12, 24), "AB0123",
                     color=DARK_BLUE, bg=HDR_BG, size=22, name="sample_id"))
    header.add(Label(Rect(8, 31, strip_w - 12, 17), "yyyy-mm-dd  hh:mm:ss",
                     color=DARK_BLUE, bg=HDR_BG, size=14, name="hdr_datetime"))
    header.add(Button(Rect(W - HDR_H, 0, HDR_H, HDR_H), "A",
                      color=TRUE_BLUE, text_color=HDR_A_CLR, size=34,
                      name="slot_a",
                      on_press=lambda: log.info("slot A tapped")))
    screen.add(header)

    # Body: countdown timer + Start button + instruction text
    body = Area(Rect(0, BODY_Y, W, BODY_H), bg_color=BODY_BG, name="body")
    body.add(TextBox(Rect(90, BODY_Y + 10, 140, 42), "02:00",
                     color=COUNTDOWN, bg=BODY_BG, size=30,
                     align="center", name="countdown"))
    body.add(Button(Rect(240, BODY_Y + 14, 72, 36), "Start",
                    color=START_BG, text_color=BTN_TEXT, size=18,
                    name="start",
                    on_press=lambda: log.info("Start pressed")))
    body.add(Label(Rect(14, BODY_Y + 75, 280, 28), "Add Reagent 1",
                   color=BODY_TEXT, bg=BODY_BG, size=20, name="instr1"))
    body.add(Label(Rect(14, BODY_Y + 107, 280, 28), "and press GO",
                   color=BODY_TEXT, bg=BODY_BG, size=20, name="instr2"))
    screen.add(body)

    screen.add(_make_status_area())
    return screen


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)-8s [%(name)s] %(message)s")
    backlight = DigitalOutputDevice(BL_PIN, initial_value=True)
    disp = ILI9341()

    def _shutdown(signum=None, frame=None):
        print("\nShutting down — blanking screen...")
        try:
            disp.fill_screen(ILI9341.BLACK)
        except Exception:
            pass
        disp.cleanup()
        backlight.close()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    disp.init()
    mgr = ScreenManager(disp)
    mgr.switch_to(build_screen())
    log.info("Measure screen active — Ctrl-C to exit.")

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
