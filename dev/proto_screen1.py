#!/usr/bin/env python3
"""
proto_screen1.py  —  "New Test" data-entry screen built with the widget system.
--------------------------------------------------------------------------------
Header: white strip ("New Test" + datetime) | green Start button | coral B square
Body:   four form rows; label is a Button, value is a TextBox.
        All rows are editable.  Pressing a label button highlights it (LABEL_ACT)
        and moves keyboard focus to that row's TextBox.
Status: MENU | clock | NEXT  (identical to proto_screen.py)

    python3 dev/proto_screen1.py

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
from widgets import Area, Button, Label, RectWidget, Screen, TextBox

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

CORAL      = _c(240,  90,  70)   # slot-B square fill
PALE_CORAL = _c(255, 200, 185)   # "B" glyph
WHITE      = _c(255, 255, 255)
BLACK      = ILI9341.BLACK
BODY_BG    = _c(255, 238, 232)   # very light coral body background
ROW_LINE   = _c(220, 180, 168)   # row divider lines
LABEL_BTN  = _c(180,  55,  35)   # dark coral — inactive label buttons
LABEL_ACT  = _c( 30, 130, 210)   # bright blue — Collect (always-active)
FIELD_BDR  = _c( 60, 130, 200)   # blue border on Collect value field
START_BG   = _c( 25, 150,  25)   # Start button
STATUS_BG  = _c( 28,  38,  58)   # status bar
BTN_GRAY   = _c( 65,  85, 115)   # MENU / NEXT buttons
BTN_TEXT   = _c(210, 225, 240)   # status bar text

# ── Form row data ─────────────────────────────────────────────────────────────
# (label, value, value_font_size, name_key)  — all rows are editable
ROWS = [
    ("ID",      "AB0124",              22, "id"),
    ("User",    "William Olsen",       18, "user"),
    ("Test",    "Ascel  Lot 1H37822",  18, "test"),
    ("Collect", "2026-06-06 14:23:22", 18, "collect"),
]

# ── Body row geometry ─────────────────────────────────────────────────────────
ROW_H      = 39
ROW_GAP    = 1
ROW_STRIDE = ROW_H + ROW_GAP   # 40
ROW_START  = BODY_Y + 3        # 53  (3 px top padding in body)
LABEL_X    = 8
LABEL_W    = 68
FIELD_X    = 82
FIELD_W    = W - FIELD_X - 8   # 230


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
    screen = Screen(bg_color=BLACK, name="new_test_screen")

    # Header: white strip | Start button | coral B square
    STRIP_W = 190
    START_X = STRIP_W + 3              # 193
    START_W = (W - HDR_H) - START_X - 3   # 74
    SQ_X    = W - HDR_H               # 270

    header = Area(Rect(0, 0, W, HDR_H), bg_color=WHITE, name="header")
    header.add(Label(Rect(8, 5, STRIP_W - 12, 24), "New Test",
                     color=BLACK, bg=WHITE, size=22, name="title"))
    header.add(Label(Rect(8, 31, STRIP_W - 12, 17), "2026-06-06  14:23:22",
                     color=BLACK, bg=WHITE, size=14, name="hdr_datetime"))
    header.add(Button(Rect(START_X, 0, START_W, HDR_H), "Start",
                      color=START_BG, text_color=BTN_TEXT, size=22,
                      name="start",
                      on_press=lambda: log.info("Start pressed")))
    header.add(Button(Rect(SQ_X, 0, HDR_H, HDR_H), "B",
                      color=CORAL, text_color=PALE_CORAL, size=34,
                      name="slot_b",
                      on_press=lambda: log.info("slot B tapped")))
    screen.add(header)

    # Body: four form rows
    body = Area(Rect(0, BODY_Y, W, BODY_H), bg_color=BODY_BG, name="body")

    for i, (label, value, vsize, key) in enumerate(ROWS):
        row_y = ROW_START + i * ROW_STRIDE

        body.add(Label(Rect(LABEL_X, row_y + 2, LABEL_W, ROW_H - 4), label,
                       color=WHITE, bg=LABEL_BTN, size=13,
                       name=f"{key}_lbl"))

        body.add(TextBox(Rect(FIELD_X, row_y, FIELD_W, ROW_H), value,
                         color=BLACK, bg=WHITE, size=vsize,
                         align="left", valign="middle",
                         border_color=FIELD_BDR,
                         editable=True,
                         name=f"{key}_field"))

        if i < len(ROWS) - 1:
            body.add(RectWidget(Rect(0, row_y + ROW_H, W, ROW_GAP),
                                color=ROW_LINE, fill=True))

    screen.add(body)
    screen.add(_make_status_area())
    return screen


def main() -> None:
    """Render the screen for visual layout inspection.  No interaction."""
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)-8s [%(name)s] %(message)s")
    backlight = DigitalOutputDevice(BL_PIN, initial_value=True)
    disp = ILI9341()

    def _shutdown(signum=None, frame=None)Some things not working now with data entry.  Pressing a :
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
    from screen_manager import ScreenManager
    mgr = ScreenManager(disp)
    mgr.switch_to(build_screen())
    log.info("Entry screen displayed — Ctrl-C to exit.  Use app_controller for interaction.")

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
