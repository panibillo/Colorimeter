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
# RGB565 color picker:  https://barth-dev.de/online/rgb565-color-picker/  0xFC20
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
    # Config editor: a field holding a typed value that has not been saved yet
    # reads red on field_yellow, so an unsaved edit is obvious at a glance.
    "edited_text": 0xF800,   # red
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
    # Splash screen
    "warning_text":0x581F,   # purple   0xFC20,   # dark amber
    "fail_text":   0xFC20,   # red
    # ROI overlay outlines in the image viewer, one per selection state.  These
    # are stroked into a PIL image over a photograph, not drawn as widgets, so
    # they are reached through rgb888() rather than color().  Chosen saturated
    # and far apart in hue so they stay legible over a lit sample.
    "roi_idle":     0x07FF,  # cyan     — present, not selected
    "roi_selected": 0xFFE0,  # yellow   — selected, not being edited
    "roi_edit":     0x07E0,  # green    — the rectangle under edit
    "roi_edge":     0xF81F,  # magenta  — its armed side
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


def unpack565(value: int) -> tuple[int, int, int]:
    """
    Expand an RGB565 integer to an 8-bit (r, g, b) triple.

    The low bits are replicated from the high bits rather than zero-filled, so
    full-scale stays full-scale: 0x1F -> 255, not 248.  Without that, "white"
    would render as (248, 252, 248) and pure colours would read slightly dark.

    Args:
        value: RGB565 integer, 0x0000..0xFFFF.

    Returns:
        Tuple (r, g, b), each 0..255.

    Example:
        >>> unpack565(0xFFFF)
        (255, 255, 255)
        >>> unpack565(0x0000)
        (0, 0, 0)
    """
    r5 = (value >> 11) & 0x1F
    g6 = (value >> 5)  & 0x3F
    b5 = value         & 0x1F
    return (r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4), (b5 << 3) | (b5 >> 2)


def rgb888(name: str) -> tuple[int, int, int]:
    """
    Resolve a palette name to an 8-bit (r, g, b) triple.

    The PIL-drawing path — the image viewer's ROI overlays — needs real RGB
    tuples, but the names must stay the single source of truth for colour, so
    this goes through the same PALETTE as color() rather than duplicating
    values.  Unknown names fall back to black exactly as color() does.

    Args:
        name: A key of PALETTE, e.g. "roi_selected".

    Returns:
        Tuple (r, g, b), each 0..255.

    Example:
        >>> rgb888("white")
        (255, 255, 255)
    """
    return unpack565(color(name))
