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
    # Capture screen (ReadPhase): flags the manual colour-read prompt/field as
    # something the operator must act on. Same red hex as edited_text/alarm_bg
    # above, but a distinct name since the semantic role differs ("please
    # read/enter this" vs "unsaved edit" / "timer expired unattended").
    "alert_text":  0xF800,   # red
    "field_red":   0xF800,   # red
    "status_bg":   0x0000,
    # Step-status chips (reagent 1 / 2 / Capture).  "ready" reuses start_green
    # (the RUN colour).  Amber is kept off the chips: on the run screens amber
    # means "do this next" (see STYLE_TABLE below), and a chip is a status,
    # not an action.  Each tint has its own text colour, chosen to be readable
    # on it — see Phase._show_steps.
    "step_pending":0xC638,   # light gray — step not yet reached / unavailable
    "step_running":0x07FF,   # cyan       — step in progress
    "step_done":   0x8410,   # gray       — step complete
    # Run-screen style colours, used by STYLE_TABLE below.
    "amber":       0xFD20,   # (255, 165, 0)  — do this next
    "red":         0xF800,   # (255,   0, 0)  — something is wrong
    "pink":        0xFDB8,   # (255, 182, 193) — an entry that is not valid
    "yellow":      0xFFE0,   # (255, 255,   0) — a box being typed in
    "light_blue":  0xA65F,   # (160, 200, 255) — row-label buttons
    "light_gray":  0xC638,   # (198, 198, 198) — disabled face
    "dark_gray":   0x4208,   # ( 66,  66,  66) — disabled frame and text
    "medium_green":0x0540,   # (  0, 170,   0) — the frame of a box being typed in
    "pending_frame":0xF800,  # red — the frame round anything that is pending
    # Visible alarm button: a backgrounded slot's timer expired unattended.
    "alarm_bg":    0xF800,   # red
    "alarm_text":  0xFFE0,   # yellow
    # Vent reminder screen (VentPhase): the whole screen, so it cannot be
    # mistaken for an ordinary step.
    "vent_bg":     0xF800,   # red
    # Reagent weight grid, Over cell: how the weighed amount sits against
    # target.  Named for the state, not the hue, so the same rules can be read
    # off any frontend: "alarm" is short of target beyond the tolerance, "warn"
    # is outside it the recoverable way (too much), and under/over tint the
    # text of a reading still inside the tolerance.  The entry boxes beside it
    # (Initial, Final) take their look from STYLE_TABLE instead.
    "wt_alarm":    0xF800,   # red    — short of target by more than the tolerance
    "wt_warn":     0xFDB8,   # pink   — outside the tolerance the recoverable way
                             #          (pink, not amber: amber means "do this next")
    "wt_under":    0xF800,   # red    — under target, still within tolerance (text)
    "wt_over":     0x001F,   # blue   — over target (text)
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


# ── Run-screen styles ─────────────────────────────────────────────────────────
#
# How a run-screen widget looks, by its role and its state.  The whole route,
# from phase to pixel, is described in pool.py under "Where a widget's colours
# come from".  Edit the table to change a look; nothing else needs to change.
# Columns are separated by spaces; "-" in the frame column means no frame.
#
#   role      action   — a button that does the step's work (Continue, START,
#                        Capture, Resume, ...)
#             rowlabel — a row's label button on an entry screen (User, Temp,
#                        ...): pressing it loads that row's default
#             textbox  — a box the operator types into
#             msgbox   — an instruction line
#
#   state     Worked out by the widget when it draws (widgets.style_state),
#             from four separate facts — the first that applies wins:
#               disabled         the widget is not enabled
#               editing_pending  a text box being typed into that is pending
#               editing          a text box being typed into
#               invalid_pending  holds a bad entry, and fixing it is next
#               invalid          holds a bad entry
#               pending          this is what the operator should do next
#               normal           none of the above
#
#   width     The frame's thickness in pixels, drawn inside the widget's rect,
#             so a thicker frame never moves anything.
#
# A role with no row for a state uses its "normal" row (for example a message
# box is never disabled).  Widgets without a role — the menus, the footer,
# the slot button, the vent screen — keep the colours they were built with.
#
# Meanings:  amber  = do this next;  red = something is wrong;
#            pink   = an entry that is not valid;
#            yellow = the box being typed in (framed red if it is also pending).
STYLE_TABLE = """
role      state            background   frame          width  text
action    disabled         light_gray   dark_gray      1      dark_gray
action    normal           start_green  -              0      black
action    pending          amber        pending_frame  2      black
rowlabel  disabled         light_gray   dark_gray      1      dark_gray
rowlabel  normal           light_blue   -              0      black
rowlabel  pending          amber        pending_frame  2      black
textbox   disabled         light_gray   dark_gray      1      dark_gray
textbox   normal           white        field_blue     1      black
textbox   pending          amber        pending_frame  2      black
textbox   invalid          pink         -              0      black
textbox   invalid_pending  red          -              0      black
textbox   editing_pending  yellow       pending_frame  2      black
textbox   editing          yellow       medium_green   2      black
msgbox    normal           white        -              0      black
msgbox    pending          amber        pending_frame  2      black
"""


class Style:
    """
    One row of STYLE_TABLE, resolved to RGB565.

    Attributes:
        bg:     Background fill.
        frame:  Frame colour, or None for no frame.
        width:  Frame thickness in pixels (0 when there is no frame).
        text:   Text colour.
    """

    __slots__ = ("bg", "frame", "width", "text")

    def __init__(self, bg: int, frame, width: int, text: int):
        self.bg, self.frame, self.width, self.text = bg, frame, width, text

    def __repr__(self) -> str:
        frame = "none" if self.frame is None else f"0x{self.frame:04X}/{self.width}px"
        return f"Style(bg=0x{self.bg:04X}, frame={frame}, text=0x{self.text:04X})"


def _parse_styles(table: str) -> dict:
    """
    Read STYLE_TABLE into {(role, state): Style}.

    Raises:
        ValueError: a row has the wrong number of columns, names a colour
            that is not in PALETTE, or gives a width that is not a number —
            so a typo stops the program at start rather than painting black.
    """
    rows = [ln.split() for ln in table.strip().splitlines()]
    styles = {}
    for cols in rows[1:]:                       # rows[0] is the heading
        if len(cols) != 6:
            raise ValueError(f"STYLE_TABLE: expected 6 columns, got {cols}")
        role, state, bg, frame, width, text = cols
        for name in (bg, text) + (() if frame == "-" else (frame,)):
            if name not in PALETTE:
                raise ValueError(f"STYLE_TABLE: unknown colour {name!r} in {cols}")
        styles[(role, state)] = Style(PALETTE[bg],
                                      None if frame == "-" else PALETTE[frame],
                                      0 if frame == "-" else int(width),
                                      PALETTE[text])
    return styles


STYLES: dict = _parse_styles(STYLE_TABLE)


def style(role: str, state: str) -> Style:
    """
    The look for `role` in `state`, falling back to the role's normal row.

    Args:
        role:  "action", "rowlabel", "textbox" or "msgbox".
        state: One of the states listed above STYLE_TABLE.

    Returns:
        The Style to draw with.

    Raises:
        KeyError: the role has no "normal" row (a role name typed wrong).
    """
    return STYLES.get((role, state)) or STYLES[(role, "normal")]


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
