"""
widgets.py  —  UI widget library for Colorimeter display
---------------------------------------------------------
Provides Screen, Area, and widget classes that sit above the ILI9341 and
TSC2007 drivers.  Widgets own a Rect (position and size), can draw themselves,
and can answer hit_test(x, y) for touch interaction.

New in this revision
--------------------
Every Widget now has:
    name      : str  — identifier for logging and future YAML/web binding
    enabled   : bool — False = greyed out, touch ignored
    focusable : bool — True = can receive keyboard focus (set by subclasses)
    focused   : bool — True = currently has keyboard focus

Area is a new Widget subclass that owns child widgets, draws its own
background, and routes touch events to its children.  Use it to create
reusable screen zones (header, body, status bar).

Button.focusable is always True.
TextBox.focusable is True when editable=True.

Widget hierarchy
----------------
  Widget (base)
    Area           — background fill + ordered children + touch routing
    Label          — static text
    TextBox        — updatable text; optional editable mode
    Button         — coloured rect + text; enabled/focused states; on_press
    RectWidget     — rect outline or fill
      DraggableRect — corner/body drag  (STUB)
    PickList       — scrollable selector  (STUB)
    Keyboard       — on-screen text entry (STUB)

Usage
-----
    from ili9341 import ILI9341
    from tsc2007 import TSC2007
    from widgets import Area, Button, Label, TextBox
    from geometry import Rect

    W, H = 320, 240
    with ILI9341() as display, TSC2007() as touch:
        status = Area(Rect(0, 215, W, 25), bg_color=display.DKGRAY, name="status")
        status.add(Button(Rect(2, 217, 58, 21), "MENU", name="menu",
                          on_press=lambda: go_menu()))
        clock = status.add(TextBox(Rect(110, 217, 100, 21), "00:00:00",
                                   name="clock", align="center"))
        status.draw(display)

        while True:
            clock.set_value(current_time())
            clock.draw(display)
            pos = touch.position()
            if pos:
                status.handle_touch(*pos)

Run this file directly to execute the self-test (no hardware required):
    python3 src/widgets.py
"""

from __future__ import annotations

import logging
from typing import Callable, List, Optional

from PIL import Image

from geometry import Rect

log = logging.getLogger(__name__)

_click_sound: Optional[Callable] = None


def set_button_sound(fn: Optional[Callable]) -> None:
    """Register a callable invoked on every Button press.  Pass None to silence."""
    global _click_sound
    _click_sound = fn


# ── Colour helpers ────────────────────────────────────────────────────────────

def _darken(color565: int, factor: float = 0.5) -> int:
    """Return an RGB565 colour dimmed by factor (0.0 = black, 1.0 = unchanged)."""
    r = (color565 >> 11) & 0x1F
    g = (color565 >>  5) & 0x3F
    b =  color565        & 0x1F
    return (int(r * factor) << 11) | (int(g * factor) << 5) | int(b * factor)


# ─────────────────────────────────────────────────────────────────────────────
# Base widget
# ─────────────────────────────────────────────────────────────────────────────

class Widget:
    """
    Abstract base class for all display widgets.

    Parameters
    ----------
    rect      : Rect   position and size on screen
    name      : str    optional identifier used in log messages and CSV binding
    widget_id : int    permanent 3-digit CSV id (100–999); 0 = no id assigned

    Attributes set by subclasses
    ----------------------------
    focusable : True for interactive widgets (Button, editable TextBox)
    """

    def __init__(self, rect: Rect, name: str = "", widget_id: int = 0):
        self.rect             = rect
        self.name             = name
        self.widget_id        = widget_id
        self.visible          = True
        self.enabled          = True
        self.focusable        = False
        self.focused          = False
        self.on_focus_change: Optional[Callable] = None
        log.debug("created %s %r", type(self).__name__, name or "(unnamed)")

    def draw(self, display) -> None:
        raise NotImplementedError(f"{type(self).__name__}.draw() not implemented")

    def invalidate(self) -> None:
        """Clear any cached display state so the next draw() unconditionally redraws."""

    def hit_test(self, px: int, py: int) -> bool:
        """True if (px, py) is within this widget's rect and the widget is visible."""
        return self.visible and self.rect.contains(px, py)

    def handle_touch(self, px: int, py: int, display=None) -> bool:
        """
        Called by Area/Screen when a touch lands on this widget.
        display is passed so widgets can redraw themselves (e.g. press flash).
        Returns True if the widget consumed the event.
        """
        return False

    def set_focused(self, focused: bool, display=None) -> None:
        """
        Set keyboard focus state.  Pass display to redraw the focus indicator
        immediately; otherwise the change takes effect on the next draw() call.
        """
        if self.focused == focused:
            return
        self.focused = focused
        action = "focused" if focused else "unfocused"
        log.info("%s %r: %s", type(self).__name__, self.name or "?", action)
        if callable(self.on_focus_change):
            self.on_focus_change(focused)
        if display is not None:
            self.draw(display)

    def __repr__(self) -> str:
        return (f"{type(self).__name__}(name={self.name!r}, "
                f"rect={self.rect!r}, visible={self.visible}, enabled={self.enabled})")


# ─────────────────────────────────────────────────────────────────────────────
# Screen
# ─────────────────────────────────────────────────────────────────────────────

class Screen:
    """
    A complete display view: background + ordered list of widgets (or Areas).

    The application creates one Screen per logical view and calls render() to
    switch to it.  Widgets draw in the order they were added; later widgets
    appear on top.

    CSV-loaded screens use tabbed_widgets / untabbed_widgets instead of _widgets.
    Area-based (legacy) construction still works via add().

    Parameters
    ----------
    bg_color     : RGB565 background (used when no bg_image is given)
    bg_image     : optional Pillow Image drawn as background before widgets
    name         : optional identifier for logging
    screen_id    : 2-char hardcoded identifier (e.g. 'SE', 'MM')
    phase        : phase group id; 0 if this screen has no timed phase
    seq          : ordering within phase (1,2,3…); 0 if not phased
    duration     : timer duration in seconds; 0 = no time limit
    modal        : if True navigation is locked while this screen is active
    beginactions : list of action strings executed when timer starts
                   ('alarm_on', 'alarm_off', 'start_timer')
    next_screen_id : screen_id to navigate to on NEXT; '' = no NEXT
    """

    def __init__(
        self,
        bg_color:       int            = 0,
        bg_image:       Image.Image    = None,
        name:           str            = "",
        screen_id:      str            = "",
        phase:          int            = 0,
        seq:            int            = 0,
        duration:       int            = 0,
        modal:          bool           = False,
        beginactions:   List[str]      = None,
        next_screen_id: str            = "",
    ):
        self.bg_color       = bg_color
        self.bg_image       = bg_image
        self.name           = name
        self.screen_id      = screen_id
        self.phase          = phase
        self.seq            = seq
        self.duration       = duration
        self.modal          = modal
        self.beginactions   = beginactions if beginactions is not None else []
        self.next_screen_id = next_screen_id

        # Runtime state
        self.is_visible:    bool              = False
        self.is_alive:      bool              = False
        self._timer_start:  Optional[float]   = None
        self.on_timeout:    Optional[Callable] = None   # called by ScreenManager.tick() on expiry

        # Widget lists
        # _widgets      : Area-based legacy construction (add())
        # tabbed_widgets   : focusable widgets in CSV tab order
        # untabbed_widgets : non-focusable display widgets from CSV
        self._widgets:        List[Widget] = []
        self.tabbed_widgets:  List[Widget] = []
        self.untabbed_widgets: List[Widget] = []

    def add(self, widget: Widget) -> Widget:
        """Add a widget or Area (legacy Area-based construction). Returns widget."""
        self._widgets.append(widget)
        log.debug("Screen %r: added %s %r",
                  self.name or "?", type(widget).__name__, widget.name or "?")
        return widget

    def add_tabbed(self, widget: Widget) -> Widget:
        """Add a focusable widget to the tab order (CSV-loaded screens)."""
        self.tabbed_widgets.append(widget)
        return widget

    def add_untabbed(self, widget: Widget) -> Widget:
        """Add a non-focusable display widget (CSV-loaded screens)."""
        self.untabbed_widgets.append(widget)
        return widget

    def remove(self, widget: Widget) -> None:
        for lst in (self._widgets, self.tabbed_widgets, self.untabbed_widgets):
            if widget in lst:
                lst.remove(widget)
                return

    def _all_widgets(self) -> List[Widget]:
        """All widgets in draw order: legacy _widgets, then untabbed, then tabbed."""
        return self._widgets + self.untabbed_widgets + self.tabbed_widgets

    def start_timer(self) -> None:
        """Mark this screen's timer as started.  Sets is_alive."""
        import time
        self._timer_start = time.monotonic()
        self.is_alive = True
        log.info("Screen %r: timer started (duration=%ds)", self.screen_id or self.name, self.duration)

    def elapsed(self) -> float:
        """Seconds since start_timer() was called; 0 if not started."""
        if self._timer_start is None:
            return 0.0
        import time
        return time.monotonic() - self._timer_start

    def render(self, display, clear_first: bool = True) -> None:
        """Full redraw: background (if clear_first), then all visible widgets in order."""
        log.info("Screen %r: render (clear_first=%s)", self.screen_id or self.name or "?", clear_first)
        for w in self._all_widgets():
            w.invalidate()
        if clear_first:
            if self.bg_image is not None:
                display.draw_image(self.bg_image, 0, 0)
            else:
                display.fill_screen(self.bg_color)
        for w in self._all_widgets():
            if w.visible:
                w.draw(display)

    def handle_touch(self, px: int, py: int, display=None) -> bool:
        """
        Dispatch a touch event to the topmost widget that contains (px, py).
        Returns True if any widget consumed the event.
        """
        for w in reversed(self._all_widgets()):
            if w.visible and w.enabled and w.hit_test(px, py):
                if w.handle_touch(px, py, display):
                    return True
        return False

    def focusable_widgets(self) -> List[Widget]:
        """
        Focusable, visible, enabled widgets in tab order.

        If tabbed_widgets is populated (CSV-loaded screen), returns those.
        Otherwise recurses into Areas (legacy Area-based construction).
        """
        if self.tabbed_widgets:
            return [w for w in self.tabbed_widgets
                    if w.visible and w.enabled and w.focusable]

        def _collect(widgets):
            out = []
            for w in widgets:
                if not w.visible or not w.enabled:
                    continue
                if isinstance(w, Area):
                    out.extend(_collect(w.children))
                elif w.focusable:
                    out.append(w)
            return out
        return _collect(self._widgets)

    def find(self, name: str) -> Optional[Widget]:
        """
        Return the first widget with the given name, searching all widget lists.
        Recurses into Areas for legacy construction.
        """
        def _search(widgets):
            for w in widgets:
                if w.name == name:
                    return w
                if isinstance(w, Area):
                    found = _search(w.children)
                    if found is not None:
                        return found
            return None
        return _search(self._all_widgets())

    def find_by_id(self, widget_id: int) -> Optional[Widget]:
        """Return the widget with the given widget_id, or None."""
        for w in self._all_widgets():
            if w.widget_id == widget_id:
                return w
        return None

    def __repr__(self) -> str:
        return (f"Screen(id={self.screen_id!r}, name={self.name!r}, "
                f"widgets={len(self._all_widgets())})")


# ─────────────────────────────────────────────────────────────────────────────
# Area
# ─────────────────────────────────────────────────────────────────────────────

class Area(Widget):
    """
    A rectangular region that owns and draws child widgets.

    Area draws its background colour first, then each visible child in the
    order they were added.  Touch events are routed to children in reverse
    order (topmost drawn = first hit-tested).

    Use Area to build reusable screen zones: header, body, status bar.
    Areas can be added to a Screen or nested inside other Areas.

    Parameters
    ----------
    rect     : position and size of the zone on screen
    bg_color : RGB565 fill colour for the zone's background
    name     : identifier for logging
    """

    def __init__(self, rect: Rect, bg_color: int = 0, name: str = "", widget_id: int = 0):
        super().__init__(rect, name=name, widget_id=widget_id)
        self.bg_color  = bg_color
        self._children: List[Widget] = []

    def add(self, widget: Widget) -> Widget:
        """Add a child widget. Returns the widget for one-line construction."""
        self._children.append(widget)
        log.debug("Area %r: added %s %r",
                  self.name or "?", type(widget).__name__, widget.name or "?")
        return widget

    def remove(self, widget: Widget) -> None:
        self._children.remove(widget)

    @property
    def children(self) -> List[Widget]:
        """Read-only view of the child list."""
        return list(self._children)

    def focusable_children(self) -> List[Widget]:
        """Focusable, visible, enabled children in draw order."""
        return [w for w in self._children
                if w.focusable and w.visible and w.enabled]

    def invalidate(self) -> None:
        for w in self._children:
            w.invalidate()

    def draw(self, display) -> None:
        display.fill_rect(self.rect, self.bg_color)
        for w in self._children:
            if w.visible:
                w.draw(display)

    def handle_touch(self, px: int, py: int, display=None) -> bool:
        """Route touch to children in reverse draw order (topmost first)."""
        for w in reversed(self._children):
            if w.visible and w.enabled and w.hit_test(px, py):
                if w.handle_touch(px, py, display):
                    return True
        return False


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
    color  : RGB565 foreground (default WHITE)
    bg     : RGB565 background (default BLACK)
    size   : font size in points
    align  : "left" | "center" | "right"
    valign : "top"  | "middle" | "bottom"
    name   : identifier for logging
    """

    def __init__(
        self,
        rect:      Rect,
        text:      str  = "",
        color:     int  = 0xFFFF,
        bg:        int  = 0x0000,
        size:      int  = 16,
        align:     str  = "left",
        valign:    str  = "top",
        name:      str  = "",
        widget_id: int  = 0,
    ):
        super().__init__(rect, name=name, widget_id=widget_id)
        self.text   = text
        self.color  = color
        self.bg     = bg
        self.size   = size
        self.align  = align
        self.valign = valign

    def draw(self, display) -> None:
        display.draw_text(self.text, self.rect,
                          color=self.color, bg=self.bg,
                          size=self.size,
                          align=self.align, valign=self.valign,
                          widget_id=str(self.widget_id) if self.widget_id else "")


# ─────────────────────────────────────────────────────────────────────────────
# TextBox
# ─────────────────────────────────────────────────────────────────────────────

class TextBox(Widget):
    """
    A text display box whose value can be updated at runtime.

    TextBox skips redraw when the value has not changed since the last draw,
    reducing SPI traffic.  Pass force=True to draw() to override this.

    When editable=True:
    - focusable is set to True so Tab can land here
    - touching the widget sets editing=True (draws EDIT_BORDER, accepts chars)
    - Enter key toggles editing on/off
    - focus loss always clears editing

    Visual states (priority order)
    --------------------------------
    editing  → EDIT_BORDER (bright green), 2 px
    focused  → FOCUS_BORDER (white), 2 px
    normal   → border_color (1 px) or nothing

    Parameters
    ----------
    rect         : position and size
    value        : initial string to display
    color        : RGB565 foreground
    bg           : RGB565 background
    size         : font size in points
    align        : "left" | "center" | "right"
    valign       : "top"  | "middle" | "bottom"
    border_color : RGB565 normal border (None = no border)
    editable     : if True, widget is focusable and accepts character input
    name         : identifier for logging
    """

    FOCUS_BORDER = 0xFFFF   # white focus ring
    EDIT_BORDER  = 0x07E0   # bright green — actively editing

    def __init__(
        self,
        rect:         Rect,
        value:        str           = "",
        color:        int           = 0xFFFF,
        bg:           int           = 0x0000,
        size:         int           = 16,
        align:        str           = "left",
        valign:       str           = "middle",
        border_color: Optional[int] = None,
        editable:     bool          = False,
        name:         str           = "",
        widget_id:    int           = 0,
    ):
        super().__init__(rect, name=name, widget_id=widget_id)
        self.value        = value
        self.color        = color
        self.bg           = bg
        self.size         = size
        self.align        = align
        self.valign       = valign
        self.border_color = border_color
        self.editable     = editable
        self.editing      = False   # True while user is typing into this field
        self.focusable    = editable
        self._last_drawn  = object()   # sentinel — forces first draw

    def invalidate(self) -> None:
        self._last_drawn = object()

    def set_value(self, value: str) -> None:
        """Update the displayed value. Call draw() to push to screen."""
        self.value = str(value)

    def on_update(self, value) -> None:
        """Pub/sub hook: subscribe this to a bus topic to auto-update the value."""
        self.set_value(value)

    def set_focused(self, focused: bool, display=None) -> None:
        """Clear editing when focus leaves; always force-redraw the border."""
        if not focused:
            self.editing = False
        self.invalidate()
        super().set_focused(focused, display)

    def toggle_editing(self, display=None) -> None:
        """Toggle editing mode. Called by Enter key."""
        if not self.editable:
            return
        self.editing = not self.editing
        log.info("TextBox %r: editing=%s", self.name or "?", self.editing)
        self.invalidate()
        if display is not None:
            self.draw(display, force=True)

    def draw(self, display, force: bool = False) -> None:
        state = (self.value, self.focused, self.editing)
        if not force and state == self._last_drawn:
            return
        self._last_drawn = state
        widget_id_str = str(self.widget_id) if self.widget_id else ""
        display.draw_text(self.value, self.rect,
                          color=self.color, bg=self.bg,
                          size=self.size,
                          align=self.align, valign=self.valign,
                          widget_id=widget_id_str)
        if self.editing:
            display.draw_rect(self.rect, self.EDIT_BORDER, fill=False, thickness=2)
        elif self.focused:
            display.draw_rect(self.rect, self.FOCUS_BORDER, fill=False, thickness=2)
        elif self.border_color is not None:
            display.draw_rect(self.rect, self.border_color, widget_id=widget_id_str)

    def handle_char(self, char: str, display=None) -> None:
        """Append or delete a character. Only active while editing."""
        if not self.editing:
            return
        if char in ('backspace', 'delete'):
            self.value = self.value[:-1]
        elif len(char) == 1 and char.isprintable():
            self.value += char
        else:
            return
        self.invalidate()
        if display is not None:
            self.draw(display, force=True)

    def handle_touch(self, px: int, py: int, display=None) -> bool:
        if not self.enabled or not self.editable:
            return False
        if self.hit_test(px, py):
            log.info("TextBox %r: editing started", self.name or "?")
            if _click_sound is not None:
                _click_sound()
            if not self.editing:
                self.editing = True
                self.invalidate()
                if display is not None:
                    self.draw(display, force=True)
            return True
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Button
# ─────────────────────────────────────────────────────────────────────────────

class Button(Widget):
    """
    A coloured rectangular button with a text label.

    focusable = True always — buttons can receive keyboard focus (Tab/Enter).
    When focused, a 2-pixel focus ring is drawn instead of the normal border.
    When disabled, face colour and text are darkened and touch is ignored.

    Parameters
    ----------
    rect           : position and size
    label          : button text
    color          : RGB565 button face colour
    text_color     : RGB565 text colour
    border_color   : RGB565 normal border (None = no border)
    size           : font size in points
    on_press       : callable invoked when button is tapped or Enter is pressed
    disabled_color : RGB565 face when disabled (None = auto-darken color)
    name           : identifier for logging
    """

    FOCUS_BORDER = 0xFFFF   # white; override per-instance if needed

    def __init__(
        self,
        rect:           Rect,
        label:          str                = "",
        color:          int                = 0x001F,
        text_color:     int                = 0xFFFF,
        border_color:   Optional[int]      = None,
        size:           int                = 16,
        on_press:       Optional[Callable] = None,
        disabled_color: Optional[int]      = None,
        name:           str                = "",
        widget_id:      int                = 0,
        nav_hint:       str                = "",
    ):
        super().__init__(rect, name=name, widget_id=widget_id)
        self.label          = label
        self.color          = color
        self.text_color     = text_color
        self.border_color   = border_color
        self.size           = size
        self.on_press       = on_press
        self.disabled_color = disabled_color
        self.focusable      = True
        self.nav_hint       = nav_hint
        self.pressed        = False

    def draw(self, display) -> None:
        if not self.enabled:
            face   = (self.disabled_color if self.disabled_color is not None
                      else _darken(self.color))
            tcolor = _darken(self.text_color)
        elif self.pressed:
            face   = _darken(self.color, 0.65)
            tcolor = self.text_color
        else:
            face   = self.color
            tcolor = self.text_color

        display.fill_rect(self.rect, face, widget_id=str(self.widget_id) if self.widget_id else "")

        if self.focused and self.enabled and not self.pressed:
            display.draw_rect(self.rect, self.FOCUS_BORDER, fill=False, thickness=2)
        elif self.border_color is not None and not self.pressed:
            display.draw_rect(self.rect, self.border_color)

        if self.label:
            display.draw_text(self.label, self.rect,
                              color=tcolor, bg=face,
                              size=self.size,
                              align="center", valign="middle",
                              widget_id=str(self.widget_id) if self.widget_id else "")

    def handle_touch(self, px: int, py: int, display=None) -> bool:
        if not self.enabled:
            log.debug("Button %r: touch ignored (disabled)", self.name or self.label)
            return False
        if self.hit_test(px, py):
            log.info("Button %r: pressed", self.name or self.label or "?")
            if display is not None:
                self.pressed = True
                self.draw(display)
            if _click_sound is not None:
                _click_sound()
            if callable(self.on_press):
                self.on_press()
            if display is not None:
                self.pressed = False
                self.draw(display)
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
    name      : identifier for logging
    """

    def __init__(
        self,
        rect:      Rect,
        color:     int  = 0xFFFF,
        fill:      bool = False,
        thickness: int  = 1,
        name:      str  = "",
        widget_id: int  = 0,
    ):
        super().__init__(rect, name=name, widget_id=widget_id)
        self.color     = color
        self.fill      = fill
        self.thickness = thickness

    def draw(self, display) -> None:
        display.draw_rect(self.rect, self.color,
                          fill=self.fill, thickness=self.thickness,
                          widget_id=str(self.widget_id) if self.widget_id else "")

    def move_to(self, x: int, y: int) -> None:
        self.rect = Rect(x, y, self.rect.w, self.rect.h)

    def resize(self, w: int, h: int) -> None:
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
    on_change    : callable(rect) invoked whenever the rect changes
    name         : identifier for logging
    """

    HANDLE = 10

    def __init__(
        self,
        rect:         Rect,
        color:        int      = 0xFFFF,
        handle_color: int      = 0x07E0,   # GREEN
        on_change:    Callable = None,
        name:         str      = "",
    ):
        super().__init__(rect, color=color, fill=False, name=name)
        self.handle_color = handle_color
        self.on_change    = on_change

    def draw(self, display) -> None:
        display.draw_rect(self.rect, self.color)
        h = self.HANDLE
        for (cx, cy) in self.rect.corners:
            display.fill_rect(
                Rect(cx - h // 2, cy - h // 2, h, h),
                self.handle_color)

    def update(self, touch) -> None:
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

    Parameters
    ----------
    rect      : position and size
    items     : list of strings
    selected  : index of initially selected item
    color     : RGB565 text colour
    bg        : RGB565 background
    sel_color : RGB565 highlight for selected row
    size      : font size
    on_select : callable(item, index) invoked on selection
    name      : identifier for logging
    """

    def __init__(
        self,
        rect:      Rect,
        items:     List[str]  = None,
        selected:  int        = 0,
        color:     int        = 0xFFFF,
        bg:        int        = 0x0000,
        sel_color: int        = 0x001F,
        size:      int        = 14,
        on_select: Callable   = None,
        name:      str        = "",
    ):
        super().__init__(rect, name=name)
        self.items     = items or []
        self.selected  = selected
        self.color     = color
        self.bg        = bg
        self.sel_color = sel_color
        self.size      = size
        self.on_select = on_select
        self.focusable = True

    def draw(self, display) -> None:
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

    Parameters
    ----------
    rect      : position and size of keyboard overlay area
    on_change : callable(buffer) on each keypress
    on_submit : callable(buffer) on Done/Enter
    initial   : pre-populated buffer string
    name      : identifier for logging
    """

    def __init__(
        self,
        rect:       Rect,
        on_change:  Callable = None,
        on_submit:  Callable = None,
        initial:    str      = "",
        name:       str      = "",
    ):
        super().__init__(rect, name=name)
        self.buffer    = initial
        self.on_change = on_change
        self.on_submit = on_submit
        self.focusable = True

    @property
    def value(self) -> str:
        return self.buffer

    def draw(self, display) -> None:
        raise NotImplementedError("Keyboard.draw() not yet implemented.")

    def handle_touch(self, px: int, py: int) -> bool:
        raise NotImplementedError("Keyboard.handle_touch() not yet implemented.")


# ─────────────────────────────────────────────────────────────────────────────
# Self-test  (no hardware required)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)-8s %(name)s:%(lineno)d  %(message)s",
    )

    class _MockDisplay:
        """Prints draw calls instead of driving hardware."""
        def fill_screen(self, color):
            print(f"    fill_screen #{color:04x}")
        def fill_rect(self, rect, color):
            print(f"    fill_rect   ({rect.x},{rect.y} {rect.w}x{rect.h}) #{color:04x}")
        def draw_rect(self, rect, color, fill=False, thickness=1):
            mode = "fill" if fill else f"border t={thickness}"
            print(f"    draw_rect   ({rect.x},{rect.y} {rect.w}x{rect.h}) #{color:04x} {mode}")
        def draw_text(self, text, rect, **kw):
            print(f"    draw_text   {text!r:25s} ({rect.x},{rect.y} {rect.w}x{rect.h})"
                  f"  sz={kw.get('size','?')}")

    disp = _MockDisplay()
    log.info("=== widgets.py self-test ===")

    # ── Area with Label and Button ────────────────────────────────────────────
    print("\n── Area.draw() ──")
    area = Area(Rect(0, 0, 320, 50), bg_color=0x1234, name="header")
    lbl  = area.add(Label(Rect(8, 5, 200, 24), "New Test",
                          color=0x0000, bg=0xFFFF, size=22, name="title"))
    btn  = area.add(Button(Rect(250, 10, 60, 30), "Go",
                           color=0x03E0, name="go_btn",
                           on_press=lambda: print("    >>> Go pressed!")))
    area.draw(disp)

    # ── Button states ─────────────────────────────────────────────────────────
    print("\n── Button: normal → focused → disabled ──")
    print("  normal:")
    btn.draw(disp)
    print("  focused:")
    btn.set_focused(True)
    btn.draw(disp)
    print("  disabled:")
    btn.set_focused(False)
    btn.enabled = False
    btn.draw(disp)
    btn.enabled = True

    # ── Touch dispatch ────────────────────────────────────────────────────────
    print("\n── Area.handle_touch() ──")
    print(f"  hit btn  (265, 25): consumed={area.handle_touch(265, 25)}")
    print(f"  hit lbl  ( 10, 10): consumed={area.handle_touch(10, 10)}")
    print(f"  miss     (400,400): consumed={area.handle_touch(400, 400)}")

    print("\n── Disabled button ignores touch ──")
    btn.enabled = False
    print(f"  hit disabled btn:   consumed={area.handle_touch(265, 25)}")
    btn.enabled = True

    # ── focusable_children ────────────────────────────────────────────────────
    print("\n── Area.focusable_children() ──")
    fc = area.focusable_children()
    print(f"  {[w.name for w in fc]}")   # only 'go_btn'; Label is not focusable

    # ── TextBox: static vs editable ───────────────────────────────────────────
    print("\n── TextBox: static ──")
    tb_ro = TextBox(Rect(10, 60, 180, 30), "AB0124",
                    color=0x0000, bg=0xFFFF, size=22, name="id_field")
    print(f"  focusable: {tb_ro.focusable}")
    tb_ro.draw(disp)
    print("  draw again (same value — should skip):")
    tb_ro.draw(disp)
    tb_ro.set_value("XY9999")
    print("  draw after set_value (changed — renders):")
    tb_ro.draw(disp)

    print("\n── TextBox: editable ──")
    tb_ed = TextBox(Rect(10, 100, 180, 30), "William Widgets",
                    color=0x0000, bg=0xFFFF, size=18, name="user_field",
                    editable=True)
    print(f"  focusable: {tb_ed.focusable}")
    tb_ed.set_focused(True, disp)
    print("  draw while focused (should show focus ring):")
    tb_ed.draw(disp, force=True)
    print("  handle_touch — should start editing:")
    tb_ed.handle_touch(15, 110, disp)
    print(f"  editing: {tb_ed.editing}")
    print("  toggle_editing — should stop editing:")
    tb_ed.toggle_editing(disp)
    print(f"  editing: {tb_ed.editing}")

    # ── Screen ────────────────────────────────────────────────────────────────
    print("\n── Screen.render() ──")
    screen = Screen(bg_color=0x001F, name="test_screen")
    screen.add(area)
    screen.add(tb_ro)
    screen.render(disp)
    fw = screen.focusable_widgets()
    print(f"  focusable top-level widgets: {[w.name for w in fw]}")

    print("\n=== self-test complete ===")
