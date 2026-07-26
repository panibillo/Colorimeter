"""
palette.py  —  Named colours for the ILI9341 hardware frontend.
----------------------------------------------------------------
Phases and widgets refer to colours by *name* (e.g. "start_green"), never by
raw value. This module is the hardware frontend's interpretation of those
names into RGB565 integers. A future Flask frontend would interpret the same
names as CSS classes — which is why the names, not the numbers, are the
portable thing.

RGB565 packing:  RRRRRGGG GGGBBBBB  (5-6-5 bits)
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def rgb(r: int, g: int, b: int) -> int:
    """Pack 8-bit r,g,b into an RGB565 integer."""
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


# Name → RGB565.  Values match the legacy csv_to_widgets palette so mock
# renders look identical, with two additions (timer_text, field_yellow).
# RGB565 color picker:  https://barth-dev.de/online/rgb565-color-picker/
PALETTE: dict[str, int] = {
    "black":       0x0000,
    "white":       0xFFFF,
    "btn_text":    0x0000,
    "btn_gray":    0x8410,
    "start_green": 0x07E0,
    "slot_a_text": 0xFFFF,
    "slot_a_bg":   0x001F,   # blue
    "slot_b_text": 0xFFFF,
    "slot_b_bg":   0xFE00,   # orange
    "label_coral": 0xFB74,
    "hdr_text":    0xFFFF,
    "hdr_blue":    0x001F,
    "body_text":   0xFFFF,
    "body_dark":   0x1084,
    "timer_text":  0x07E0,   # bright green
    "row_line":    0x8410,
    "field_blue":  0x041F,
    "field_yellow":0xFFE0,
    "status_bg":   0x0000,
    # Step-status chips (reagent 1 / 2 / Capture).  "ready" reuses start_green
    # (the RUN colour); the rest are its dim form and a running amber + dim.
    #   "step_pending":0x03E0,   # dim green  — step not yet reached / unavailable
    "step_pending":0xC5F7,   # light gray — step not yet reached / unavailable
    "step_running":0xFD20,   # amber      — step in progress
    "step_done":   0x7A80,   # dim amber  — step complete
    # Visible alarm button: a backgrounded slot's timer expired unattended.
    "alarm_bg":    0xF800,   # red
    "alarm_text":  0xFFE0,   # yellow
}


def color(name: str) -> int:
    """
    Resolve a palette name to RGB565.  Unknown or empty names log a warning
    and fall back to black, so a typo shows up as a black rect rather than a
    crash mid-render.
    """
    if not name:
        return 0x0000
    value = PALETTE.get(name)
    if value is None:
        log.warning("palette: unknown colour %r (using black)", name)
        return 0x0000
    return value
