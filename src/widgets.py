"""
widgets.py  —  UI widget library for Colorimeter display
---------------------------------------------------------
Provides Screen and widget classes that sit above the ILI9341 and TSC2007
drivers.  Widgets own a Rect (position and size), can draw themselves, and
can answer hit_test(x, y) for touch interaction.

Screen manages a collection of widgets and a background colour or image.
Calling screen.render(display) does a full redraw of background + all widgets.
The application switches views by calling render() on a different Screen.

Widget hierarchy:
  Widget (base)
    Label          — static text
    Button         — coloured rectangle with text, fires a callback on tap
    TextBox        — displays a string value, updated by application code
    RectWidget     — draws a rectangle outline or fill over the display
    DraggableRect  — RectWidget with corner-drag and body-drag (STUB)
    PickList       — scrollable list of text options (STUB)
    Keyboard       — on-screen text entry (STUB)

Stubs have __init__ and draw() defined; interactive methods raise
NotImplementedError so that imports do not break when they are added later.

Usage:
    from ili9341 import ILI9341
    from tsc2007 import TSC2007
    from widgets import Screen, Button, Label, TextBox
    from geometry import Rect

    with ILI9341() as display, TSC2007() as touch:
        screen = Screen(bg_color=display.DKGRAY)
        screen.add(Label(Rect(10, 10, 200, 30), "Temperature",
                         color=display.CYAN, size=20))
        temp = TextBox(Rect(10, 50, 200, 40), "---",
                       color=display.WHITE, size=32)
        screen.add(temp)
        screen.render(display)

        while True:
            temp.set_value(f"{read_sensor():.2f} °C")
            temp.draw(display)

            pos = touch.position()
            if pos:
                screen.handle_touch(*pos)
"""

from __future__ import annotations
from typing import Callable, List, Optional
from PIL import Image

from geometry import Rect


# ─────────────────────────────────────────────────────────────────────────────
# Base widget
# ─────────────────────────────────────────────────────────────────────────────

class Widget:
    """
    Abstract base class for all display widgets.

    Every widget has a Rect defining its position and size on screen.
    Subclasses must implement draw(display).
    """

    def __init__(self, rect: Rect):
        self.rect    = rect
        self.visible = True

    def draw(self, display):
        raise NotImplementedError(f"{type(self).__name__}.draw() not implemented")

    def hit_test(self, px: int, py: int) -> bool:
        """Return True if (px, py) falls within this widget's rect."""
        return self.visible and self.rect.contains(px, py)

    def handle_touch(self, px: int, py: int) -> bool:
        """
        Called by Screen when a touch lands on this widget.
        Returns True if the widget consumed the event.
        Base implementation does nothing.
        """
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Screen
# ─────────────────────────────────────────────────────────────────────────────

class Screen:
    """
    A complete display view: background + ordered list of widgets.

    The application creates one Screen per view (e.g. one per active test)
    and calls render() to switch to it.  Widgets draw in the order they were
    added; later widgets appear on top.

    Parameters
    ----------
    bg_color    : RGB565 background colour (default ILI9341.BLACK = 0)
    bg_image    : optional Pillow Image drawn as background before widgets.
                  Must already be sized to the display.  If provided,
                  bg_color is ignored.
    name        : optional string identifier for debugging
    """

    def __init__(
        self,
        bg_color: int          = 0,
        bg_image: Image.Image  = None,
        name:     str          = "",
    ):
        self.bg_color  = bg_color
        self.bg_image  = bg_image
        self.name      = name
        self._widgets: List[Widget] = []

    def add(self, widget: Widget) -> Widget:
        """Add a widget to this screen. Returns the widget for chaining."""
        self._widgets.append(widget)
        return widget

    def remove(self, widget: Widget):
        """Remove a widget from this screen."""
        self._widgets.remove(widget)

    def render(self, display):
        """
        Full redraw: background first, then all visible widgets in order.
        Call this when switching to this screen.
        """
        if self.bg_image is not None:
            display.draw_image(self.bg_image, 0, 0)
        else:
            display.fill_screen(self.bg_color)
        for w in self._widgets:
            if w.visible:
                w.draw(display)

    def handle_touch(self, px: int, py: int) -> bool:
        """
        Dispatch a touch event to the topmost widget that contains (px, py).
        Returns True if any widget consumed the event.
        Widgets are tested in reverse order (topmost drawn = first tested).
        """
        for w in reversed(self._widgets):
            if w.hit_test(px, py):
                if w.handle_touch(px, py):
                    return True
        return False

    def __repr__(self):
        return f"Screen(name={self.name!r}, widgets={len(self._widgets)})"


# ─────────────────────────────────────────────────────────────────────────────
# Label
# ─────────────────────────────────────────────────────────────────────────────

class Label(Widget):
    """
    A static text label.  Does not respond to touch.

    Parameters
    ----------
    rect   : position and size on screen
    text   : string to display
    color  : RGB565 foreground (default WHITE = 0xFFFF)
    bg     : RGB565 background (default BLACK = 0)
    size   : font size in points
    align  : "left" | "center" | "right"
    valign : "top"  | "middle" | "bottom"
    """

    def __init__(
        self,
        rect:   Rect,
        text:   str  = "",
        color:  int  = 0xFFFF,
        bg:     int  = 0x0000,
        size:   int  = 16,
        align:  str  = "left",
        valign: str  = "top",
    ):
        super().__init__(rect)
        self.text   = text
        self.color  = color
        self.bg     = bg
        self.size   = size
        self.align  = align
        self.valign = valign

    def draw(self, display):
        display.draw_text(self.text, self.rect,
                          color=self.color, bg=self.bg,
                          size=self.size,
                          align=self.align, valign=self.valign)


# ─────────────────────────────────────────────────────────────────────────────
# TextBox
# ─────────────────────────────────────────────────────────────────────────────

class TextBox(Widget):
    """
    A text display box whose value can be updated at runtime.

    Unlike Label, TextBox is designed for values that change frequently
    (sensor readings, elapsed time, etc.).  Call set_value() to update;
    call draw() to push the new value to the display.

    Optionally, set a border_color to draw a rectangle around the box.
    """

    def __init__(
        self,
        rect:         Rect,
        value:        str  = "",
        color:        int  = 0xFFFF,
        bg:           int  = 0x0000,
        size:         int  = 16,
        align:        str  = "left",
        valign:       str  = "middle",
        border_color: int  = None,
    ):
        super().__init__(rect)
        self.value        = value
        self.color        = color
        self.bg           = bg
        self.size         = size
        self.align        = align
        self.valign       = valign
        self.border_color = border_color
        self._last_drawn  = object()    # sentinel — forces first draw

    def set_value(self, value: str):
        """Update the displayed value. Call draw() to push to screen."""
        self.value = str(value)

    def draw(self, display, force: bool = False):
        """
        Draw the text box.  Skips redraw if value has not changed
        unless force=True.
        """
        if not force and self.value == self._last_drawn:
            return
        self._last_drawn = self.value
        display.draw_text(self.value, self.rect,
                          color=self.color, bg=self.bg,
                          size=self.size,
                          align=self.align, valign=self.valign)
        if self.border_color is not None:
            display.draw_rect(self.rect, self.border_color)


# ─────────────────────────────────────────────────────────────────────────────
# Button
# ─────────────────────────────────────────────────────────────────────────────

class Button(Widget):
    """
    A coloured rectangular button with a text label.

    Parameters
    ----------
    rect         : position and size
    label        : button text
    color        : RGB565 button face colour
    text_color   : RGB565 text colour
    border_color : RGB565 border colour (None = no border)
    size         : font size
    on_press     : callable invoked when button is tapped
    """

    def __init__(
        self,
        rect:         Rect,
        label:        str      = "",
        color:        int      = 0x001F,    # BLUE
        text_color:   int      = 0xFFFF,    # WHITE
        border_color: int      = 0xFFFF,    # WHITE
        size:         int      = 16,
        on_press:     Callable = None,
    ):
        super().__init__(rect)
        self.label        = label
        self.color        = color
        self.text_color   = text_color
        self.border_color = border_color
        self.size         = size
        self.on_press     = on_press

    def draw(self, display):
        display.fill_rect(self.rect, self.color)
        if self.border_color is not None:
            display.draw_rect(self.rect, self.border_color)
        if self.label:
            display.draw_text(self.label, self.rect,
                              color=self.text_color, bg=self.color,
                              size=self.size,
                              align="center", valign="middle")

    def handle_touch(self, px: int, py: int) -> bool:
        if self.hit_test(px, py):
            if callable(self.on_press):
                self.on_press()
            return True
        return False


# ─────────────────────────────────────────────────────────────────────────────
# RectWidget
# ─────────────────────────────────────────────────────────────────────────────

class RectWidget(Widget):
    """
    Draws a rectangle outline or filled rectangle on screen.
    Useful for overlaying measurement regions on a camera image.

    Parameters
    ----------
    rect      : position and size of the rectangle
    color     : RGB565 colour
    fill      : True = filled, False = outline only
    thickness : outline thickness in pixels (fill=False only)
    """

    def __init__(
        self,
        rect:      Rect,
        color:     int  = 0xFFFF,
        fill:      bool = False,
        thickness: int  = 1,
    ):
        super().__init__(rect)
        self.color     = color
        self.fill      = fill
        self.thickness = thickness

    def draw(self, display):
        display.draw_rect(self.rect, self.color,
                          fill=self.fill, thickness=self.thickness)

    def move_to(self, x: int, y: int):
        """Move the rectangle to a new top-left position."""
        self.rect = Rect(x, y, self.rect.w, self.rect.h)

    def resize(self, w: int, h: int):
        """Resize the rectangle."""
        self.rect = Rect(self.rect.x, self.rect.y, w, h)


# ─────────────────────────────────────────────────────────────────────────────
# DraggableRect  (STUB)
# ─────────────────────────────────────────────────────────────────────────────

class DraggableRect(RectWidget):
    """
    A RectWidget whose corners and body can be dragged via touch.

    Corner drag resizes the rectangle; body drag moves it.
    Corner handles are drawn as small filled squares at each corner.

    STUB: draw() and hit_test() are implemented. update() and the
    drag state machine are not yet implemented.

    When implementing:
    - use touch.track() generator to follow the finger while held
    - use rect.near_corner(px, py, radius=12) to detect corner hits
    - use rect.with_corner(name, px, py) to compute resized rect
    - redraw only the affected region between drag frames

    Parameters
    ----------
    rect         : initial rectangle
    color        : outline colour
    handle_color : corner handle colour
    handle_size  : corner handle square size in pixels
    on_change    : callable(rect) invoked whenever the rect changes
    """

    HANDLE = 10     # corner handle size in pixels

    def __init__(
        self,
        rect:         Rect,
        color:        int      = 0xFFFF,
        handle_color: int      = 0x07E0,    # GREEN
        on_change:    Callable = None,
    ):
        super().__init__(rect, color=color, fill=False)
        self.handle_color = handle_color
        self.on_change    = on_change

    def draw(self, display):
        """Draw rectangle outline and corner handles."""
        display.draw_rect(self.rect, self.color)
        h = self.HANDLE
        for (cx, cy) in self.rect.corners:
            display.fill_rect(
                Rect(cx - h // 2, cy - h // 2, h, h),
                self.handle_color)

    def update(self, touch):
        """
        Process a drag interaction using the touch driver.
        touch : TSC2007 instance

        NOT YET IMPLEMENTED.
        """
        raise NotImplementedError(
            "DraggableRect.update() not yet implemented. "
            "Use touch.track() and rect.with_corner() / rect.moved_by().")


# ─────────────────────────────────────────────────────────────────────────────
# PickList  (STUB)
# ─────────────────────────────────────────────────────────────────────────────

class PickList(Widget):
    """
    A scrollable list of text options. User taps to select.

    STUB: __init__ is defined. draw() and handle_touch() are not yet
    implemented.

    When implementing:
    - display a visible window of items within self.rect
    - draw up/down arrow buttons if items exceed visible rows
    - highlight the selected item
    - fire on_select(item, index) callback on tap
    """

    def __init__(
        self,
        rect:       Rect,
        items:      List[str]  = None,
        selected:   int        = 0,
        color:      int        = 0xFFFF,
        bg:         int        = 0x0000,
        sel_color:  int        = 0x001F,
        size:       int        = 14,
        on_select:  Callable   = None,
    ):
        super().__init__(rect)
        self.items      = items or []
        self.selected   = selected
        self.color      = color
        self.bg         = bg
        self.sel_color  = sel_color
        self.size       = size
        self.on_select  = on_select

    def draw(self, display):
        raise NotImplementedError("PickList.draw() not yet implemented.")

    def handle_touch(self, px: int, py: int) -> bool:
        raise NotImplementedError("PickList.handle_touch() not yet implemented.")


# ─────────────────────────────────────────────────────────────────────────────
# Keyboard  (STUB)
# ─────────────────────────────────────────────────────────────────────────────

class Keyboard(Widget):
    """
    On-screen keyboard for text entry.

    STUB: __init__ is defined. draw() and handle_touch() are not yet
    implemented.

    When implementing:
    - lay out QWERTY rows as Button widgets within self.rect
    - maintain an internal buffer string
    - fire on_change(buffer) on each keypress
    - fire on_submit(buffer) on Enter/Done
    - support Backspace
    - optional: shift key for upper/lower case
    """

    def __init__(
        self,
        rect:       Rect,
        on_change:  Callable = None,
        on_submit:  Callable = None,
        initial:    str      = "",
    ):
        super().__init__(rect)
        self.buffer     = initial
        self.on_change  = on_change
        self.on_submit  = on_submit

    @property
    def value(self) -> str:
        return self.buffer

    def draw(self, display):
        raise NotImplementedError("Keyboard.draw() not yet implemented.")

    def handle_touch(self, px: int, py: int) -> bool:
        raise NotImplementedError("Keyboard.handle_touch() not yet implemented.")
