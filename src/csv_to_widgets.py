"""
Convert csv_loader dataclasses into actual widget.Screen, Label, Button, etc. objects.

This bridges csv_loader (lightweight data models) and widgets.py (stateful UI classes).
"""

import logging
from geometry import Rect
from widgets import Screen, Label, Button, TextBox, RectWidget
import csv_loader

log = logging.getLogger(__name__)


# Color name → RGB565 lookup (populated from config_loader.palette)
# For now, hardcoded; should come from palette.yaml
_PALETTE = {
    "black": 0x0000,
    "white": 0xFFFF,
    "btn_text": 0x0000,
    "start_green": 0x07E0,
    "slot_a_text": 0xFFFF,
    "slot_a_bg": 0x001F,
    "slot_b_text": 0xFFFF,
    "slot_b_bg": 0xFE00,
    "label_coral": 0xFB74,
    "hdr_text": 0xFFFF,
    "hdr_blue": 0x001F,
    "body_text": 0xFFFF,
    "body_dark": 0x1084,
    "countdown": 0x07E0,
    "row_line": 0x8410,
    "field_blue": 0x041F,
    "btn_gray": 0x8410,
    "status_bg": 0x0000,
}


def resolve_color(color_name: str) -> int:
    """
    Resolve a color name (from palette) to RGB565 integer.
    Returns black (0x0000) if color not found.
    """
    if not color_name or color_name not in _PALETTE:
        log.warning("Unknown color name: %r (using black)", color_name)
        return 0x0000
    return _PALETTE[color_name]


def csv_widget_to_widget(csv_widget: csv_loader.Widget) -> (Label | Button | TextBox | RectWidget):
    """
    Convert a csv_loader.Widget dataclass into an actual widget object.
    Dispatches based on widget_type (Label, Button, TextBox, RectWidget).
    """
    rect = Rect(csv_widget.x, csv_widget.y, csv_widget.w, csv_widget.h)
    widget_id = int(csv_widget.widget_id) if csv_widget.widget_id.isdigit() else 0

    if csv_widget.widget_type == "Label":
        return Label(
            rect=rect,
            text=csv_widget.label,
            color=resolve_color(csv_widget.fgcolor),
            bg=resolve_color(csv_widget.bgcolor),
            size=csv_widget.size,
            align=csv_widget.align,
            valign=csv_widget.valign,
            name=csv_widget.widget_id,
            widget_id=widget_id,
        )

    elif csv_widget.widget_type == "Button":
        button = Button(
            rect=rect,
            label=csv_widget.label,
            color=resolve_color(csv_widget.bgcolor),  # button face
            text_color=resolve_color(csv_widget.fgcolor),  # text color
            border_color=resolve_color(csv_widget.border_color) if csv_widget.border_color else None,
            size=csv_widget.size,
            name=csv_widget.widget_id,
            widget_id=widget_id,
            nav_hint=csv_widget.default,
        )
        return button

    elif csv_widget.widget_type == "TextBox":
        textbox = TextBox(
            rect=rect,
            value=csv_widget.default or "",
            color=resolve_color(csv_widget.fgcolor),
            bg=resolve_color(csv_widget.bgcolor),
            size=csv_widget.size,
            align=csv_widget.align,
            valign=csv_widget.valign,
            border_color=resolve_color(csv_widget.border_color) if csv_widget.border_color else None,
            editable=csv_widget.editable,
            name=csv_widget.widget_id,
            widget_id=widget_id,
        )
        return textbox

    elif csv_widget.widget_type == "RectWidget":
        rect_widget = RectWidget(
            rect=rect,
            color=resolve_color(csv_widget.border_color or csv_widget.fgcolor),
            fill=bool(csv_widget.fill),
            name=csv_widget.widget_id,
            widget_id=widget_id,
        )
        return rect_widget

    else:
        raise ValueError(f"Unknown widget_type: {csv_widget.widget_type}")


def csv_screen_to_widgets_screen(
    csv_screen: csv_loader.Screen,
    bg_color: int = 0xFFFF,
) -> Screen:
    """
    Convert a csv_loader.Screen into a widgets.Screen, with all widgets.

    Args:
        csv_screen: from csv_loader.load_all()
        bg_color: RGB565 background color (default white)

    Returns:
        Screen object ready to use with ScreenManager
    """
    screen = Screen(
        bg_color=bg_color,
        name=csv_screen.screen_id,
        screen_id=csv_screen.screen_id,
        duration=csv_screen.duration,
        modal=csv_screen.modal,
        beginactions=csv_screen.beginactions,
        next_screen_id=csv_screen.next_screen,
    )

    # Process widgets in order: tabbed first, untabbed second
    # This preserves the draw order from csv_loader
    for csv_widget in csv_screen.widgets:
        widget = csv_widget_to_widget(csv_widget)
        if csv_widget.tab_order is not None:
            screen.add_tabbed(widget)
        else:
            screen.add_untabbed(widget)

    log.info(
        "Loaded screen %r: %d tabbed, %d untabbed, duration=%ds, modal=%s",
        csv_screen.screen_id,
        len(screen.tabbed_widgets),
        len(screen.untabbed_widgets),
        csv_screen.duration,
        csv_screen.modal,
    )

    return screen


def load_all_screens(
    widget_defs_path: str,
    screen_defs_path: str,
    screen_widgets_path: str,
    bg_color: int = 0xFFFF,
) -> dict[str, Screen]:
    """
    Load all CSV files and convert to a dict of Screen objects.

    Args:
        widget_defs_path: path to widget_defs.csv
        screen_defs_path: path to screen_defs.csv
        screen_widgets_path: path to screen_widgets.csv
        bg_color: RGB565 background for all screens

    Returns:
        {screen_id: Screen} ready for use with ScreenManager
    """
    # Load raw data from CSV
    csv_screens = csv_loader.load_all(widget_defs_path, screen_defs_path, screen_widgets_path)

    # Convert to actual Screen objects
    screens = {}
    for screen_id, csv_screen in csv_screens.items():
        screens[screen_id] = csv_screen_to_widgets_screen(csv_screen, bg_color=bg_color)

    log.info("Loaded %d screens from CSV", len(screens))
    return screens
