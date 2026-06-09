"""
screen_manager.py  —  Active screen and keyboard-focus tracking.
----------------------------------------------------------------
ScreenManager owns exactly one active Screen at a time.  It tracks which
widget holds keyboard focus, routes touch events to the active screen, and
handles Tab/Enter navigation.

Typical use
-----------
    mgr = ScreenManager(display)
    mgr.switch_to(main_screen)

    while True:
        if key := read_key():
            mgr.on_key(key)
        if pos := touch.position():
            mgr.on_touch(*pos)

Focus rules
-----------
- All focusable, visible, enabled widgets in the active Screen are collected in
  draw order (Screen.focusable_widgets() recurses into Areas automatically).
- Tab moves forward; Shift-Tab moves backward; both wrap.
- Enter triggers the focused widget: Button.on_press or TextBox.on_edit_start.
- switch_to() renders the new screen and focuses the first focusable widget.
- If widgets change visible/enabled state, call switch_to() again to rebuild
  the focus list (reliable over clever).

Run this file directly to execute the self-test (no hardware required):
    python3 src/screen_manager.py
"""

from __future__ import annotations

import logging
from typing import List, Optional

from geometry import Rect
from widgets import Area, Button, Label, Screen, TextBox, Widget

log = logging.getLogger(__name__)


class ScreenManager:
    """
    Manages the active Screen and keyboard focus within it.

    Parameters
    ----------
    display : ILI9341 driver instance (or any object satisfying the display
              protocol: fill_screen, fill_rect, draw_rect, draw_text).
    """

    def __init__(self, display) -> None:
        self.display     = display
        self._screen:    Optional[Screen] = None
        self._focus_idx: int              = -1
        self._focusables: List[Widget]    = []

    # ── Screen switching ──────────────────────────────────────────────────────

    def switch_to(self, screen: Screen) -> None:
        """Full render of screen; rebuild focus list; focus first widget."""
        log.info("ScreenManager: switch to Screen %r", screen.name or "?")
        self._screen     = screen
        self._focus_idx  = -1
        screen.render(self.display)
        self._focusables = screen.focusable_widgets()
        log.info("ScreenManager: %d focusable widget(s): %s",
                 len(self._focusables),
                 [w.name or type(w).__name__ for w in self._focusables])
        if self._focusables:
            self._move_focus(0)

    @property
    def active_screen(self) -> Optional[Screen]:
        return self._screen

    # ── Focus management ──────────────────────────────────────────────────────

    def focused_widget(self) -> Optional[Widget]:
        """Return the currently focused widget, or None."""
        if 0 <= self._focus_idx < len(self._focusables):
            return self._focusables[self._focus_idx]
        return None

    def _move_focus(self, new_idx: int) -> None:
        """Unfocus previous widget, move focus to new_idx (wrapping), redraw both."""
        if not self._focusables:
            return
        prev = self.focused_widget()
        if prev is not None:
            prev.set_focused(False, self.display)
        self._focus_idx = new_idx % len(self._focusables)
        self._focusables[self._focus_idx].set_focused(True, self.display)

    def focus_widget(self, widget: Widget) -> None:
        """Move keyboard focus directly to widget (must be in the current focusable list)."""
        try:
            idx = self._focusables.index(widget)
        except ValueError:
            log.warning("focus_widget: %r not in focusable list", getattr(widget, 'name', widget))
            return
        self._move_focus(idx)

    # ── Event routing ─────────────────────────────────────────────────────────

    def on_touch(self, px: int, py: int) -> bool:
        """Dispatch a touch event to the active screen. Returns True if consumed."""
        if self._screen is None:
            return False
        consumed = self._screen.handle_touch(px, py, self.display)
        log.debug("ScreenManager: touch (%d,%d) consumed=%s", px, py, consumed)
        # Sync keyboard focus: if a TextBox started editing from this touch,
        # move focus to it so key events and Tab state stay consistent.
        if consumed:
            for i, w in enumerate(self._focusables):
                if isinstance(w, TextBox) and w.editing and i != self._focus_idx:
                    self._move_focus(i)
                    break
        return consumed

    def on_key(self, key: str) -> bool:
        """
        Handle a keyboard / navigation event.

        Recognised keys
        ---------------
        "tab"        — move focus to next widget (wrapping)
        "shift-tab"  — move focus to previous widget (wrapping)
        "enter"      — activate focused widget: Button.on_press or
                        TextBox.on_edit_start

        Returns True if the event was consumed.
        """
        log.info(f'on_key {key}')
        if not self._focusables:
            return False

        if key in ("tab", "shift-tab"):
            cw = self.focused_widget()
            was_editing = isinstance(cw, TextBox) and cw.editing
            delta = 1 if key == "tab" else -1
            self._move_focus(self._focus_idx + delta)
            if was_editing:
                nw = self.focused_widget()
                if isinstance(nw, TextBox) and nw.editable:
                    nw.editing = True
                    nw.invalidate()
                    nw.draw(self.display, force=True)
            return True

        if key == "enter":
            w = self.focused_widget()
            if w is None or not w.enabled:
                return False
            if isinstance(w, TextBox) and w.editable:
                log.info("ScreenManager: Enter → TextBox %r toggle editing", w.name or "?")
                w.toggle_editing(self.display)
                return True
            if isinstance(w, Button) and callable(w.on_press):
                log.info("ScreenManager: Enter → Button %r", w.name or w.label or "?")
                w.on_press()
                return True

        # Physical keyboard character input — route to focused editing TextBox.
        w = self.focused_widget()
        if isinstance(w, TextBox) and w.editing and w.enabled:
            if key in ('backspace', 'delete') or (len(key) == 1 and key.isprintable()):
                log.debug("ScreenManager: char %r → TextBox %r", key, w.name or "?")
                w.handle_char(key, self.display)
                return True

        return False

    # ── Modal overlay stubs ───────────────────────────────────────────────────

    def push_modal(self, area: Area) -> None:
        """Push a modal overlay Area (not yet implemented)."""
        raise NotImplementedError("push_modal not yet implemented")

    def pop_modal(self) -> None:
        """Pop the top modal overlay (not yet implemented)."""
        raise NotImplementedError("pop_modal not yet implemented")

    def __repr__(self) -> str:
        name = self._screen.name if self._screen else "none"
        return f"ScreenManager(screen={name!r}, focus_idx={self._focus_idx})"


# ─────────────────────────────────────────────────────────────────────────────
# Self-test  (no hardware required)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-8s [%(name)s] %(message)s",
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
    log.info("=== screen_manager.py self-test ===")

    # ── Build a screen that mirrors the proto_screen1 layout ─────────────────
    print("\n── Build screen: header Area + body Area with buttons ──")
    screen = Screen(bg_color=0x001F, name="proto1")

    _c = lambda r, g, b: ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    CORAL     = _c(240,  90,  70)
    LABEL_BTN = _c(180,  55,  35)
    LABEL_ACT = _c( 30, 130, 210)
    START_BG  = _c( 25, 150,  25)
    WHITE     = 0xFFFF
    BLACK     = 0x0000

    header = Area(Rect(0, 0, 320, 50), bg_color=WHITE, name="header")
    header.add(Label(Rect(8, 5, 182, 24), "New Test",
                     color=BLACK, bg=WHITE, size=22, name="title"))
    start_btn = header.add(Button(Rect(207, 0, 63, 50), "Start",
                                  color=START_BG, name="start",
                                  on_press=lambda: print("    >>> Start pressed")))
    screen.add(header)

    body = Area(Rect(0, 50, 320, 165), bg_color=_c(255, 238, 232), name="body")
    id_btn   = body.add(Button(Rect(8, 55, 68, 35), "ID",
                               color=LABEL_BTN, name="id_btn",
                               on_press=lambda: print("    >>> ID edit")))
    user_btn = body.add(Button(Rect(8, 95, 68, 35), "User",
                               color=LABEL_BTN, name="user_btn",
                               on_press=lambda: print("    >>> User edit")))
    test_btn = body.add(Button(Rect(8, 135, 68, 35), "Test",
                               color=LABEL_BTN, name="test_btn",
                               on_press=lambda: print("    >>> Test edit")))
    col_btn  = body.add(Button(Rect(8, 175, 68, 35), "Collect",
                               color=LABEL_ACT, name="collect_btn",
                               on_press=lambda: print("    >>> Collect edit")))
    screen.add(body)

    # ── switch_to: render + focus first widget ────────────────────────────────
    print("\n── switch_to: render + initial focus ──")
    mgr = ScreenManager(disp)
    mgr.switch_to(screen)
    fw = mgr.focused_widget()
    print(f"  initially focused: {fw.name!r}")

    # ── Tab cycles through all focusable widgets ──────────────────────────────
    print("\n── Tab cycles all 5 focusable widgets (wraps) ──")
    for step in range(6):
        mgr.on_key("tab")
        fw = mgr.focused_widget()
        print(f"  tab #{step+1:02d}: focused = {fw.name!r}")

    # ── Shift-Tab goes backward ───────────────────────────────────────────────
    print("\n── Shift-Tab ──")
    mgr.on_key("shift-tab")
    print(f"  after shift-tab: focused = {mgr.focused_widget().name!r}")

    # ── Enter activates the focused button ───────────────────────────────────
    print("\n── Enter activates focused button ──")
    while mgr.focused_widget().name != "start":
        mgr.on_key("tab")
    print(f"  focused: {mgr.focused_widget().name!r}")
    print(f"  on_key('enter') consumed: {mgr.on_key('enter')}")

    # ── Touch dispatch ────────────────────────────────────────────────────────
    print("\n── on_touch dispatch ──")
    # id_btn is at Rect(8,55,68,35) → x 8–76, y 55–90 (body offset 50 → abs y 105–140)
    print(f"  id_btn touch  ( 40, 120): consumed={mgr.on_touch(40, 120)}")
    print(f"  body miss     (200, 120): consumed={mgr.on_touch(200, 120)}")
    print(f"  start touch   (230,  25): consumed={mgr.on_touch(230, 25)}")

    # ── Disabled widget excluded from tab order ───────────────────────────────
    print("\n── Disable id_btn; switch_to rebuilds focus list ──")
    id_btn.enabled = False
    mgr.switch_to(screen)
    names = [w.name for w in mgr._focusables]
    print(f"  focusables: {names}")
    print(f"  first focused: {mgr.focused_widget().name!r}")
    id_btn.enabled = True

    print("\n=== self-test complete ===")
