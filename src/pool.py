"""
pool.py  —  The widget pool: the single view for the physical screen.
----------------------------------------------------------------------
There is one physical 320x240 screen, so there is one Pool: one widget
instance per rect in layout.py, keyed by its short name.  Slots A and B differ
only in data; they share this one pool.  Rendering a slot means a phase
configures these widgets (text, enabled, which are shown, button wiring) and
the pool draws them.

Draw model
----------
- **Phase change → full clear.**  `render()` fills the screen with the layout
  background and draws every *visible* widget.  Hiding a widget is therefore
  just "don't show it" — there is no per-widget erase.  Phases are infrequent
  and user-driven, so the ~100-200 ms full repaint is fine.
- **1 Hz updates → single redraw.**  `redraw(name)` repaints one widget over
  its own rect (same background), for the clock, the focused timer, and capture
  progress.

Widgets start hidden.  A phase opts a widget in with `show()`, so nothing draws
unless a phase asked for it.  The usual flow, owned by the arbiter, is:

    pool.hide_all()
    phase.enter(pool, slot)      # sets content + show()s its widgets
    pool.render(display)

Phases speak only in names and text here — never pixels (those live in
layout.py) and never raw colours (those are palette names resolved at build).
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

import layout
from palette import color
from widgets import Button, Label, RectWidget, TextBox, Widget
from timer import TimerWidget

log = logging.getLogger(__name__)


class Pool:
    """
    An ordered collection of named widgets plus a visible set.

    Build with `Pool.build()`.  Access widgets with `pool[name]` (raises on a
    typo) or `pool.get(name)` (returns None).  The convenience methods `set`,
    `enable`, `show`, `hide` cover what phases do most; reach for `pool[name]`
    for type-specific calls (`pool['timer'].bind(state)`).
    """

    def __init__(self) -> None:
        # Insertion order is draw order (later = on top): body, header, footer,
        # then the modal overlay last so it lands above everything.
        self._widgets: Dict[str, Widget] = {}

    # ── Construction ──────────────────────────────────────────────────────────

    def _add(self, name: str, widget: Widget) -> Widget:
        widget.name = name
        widget.visible = False        # nothing draws until a phase shows it
        self._widgets[name] = widget
        return widget

    @classmethod
    def build(cls) -> "Pool":
        """Construct every widget from layout.py + palette.py."""
        p = cls()
        L = layout

        def label(name, rect, fg, bg, size, align="center", valign="middle", text=""):
            return p._add(name, Label(rect, text=text, color=color(fg), bg=color(bg),
                                      size=size, align=align, valign=valign))

        def field(name, rect, size, border, editable=True, align="left"):
            return p._add(name, TextBox(rect, value="", color=color("black"),
                                        bg=color("white"), size=size, align=align,
                                        valign="middle", border_color=color(border),
                                        editable=editable))

        def button(name, rect, fg, bg, size, text=""):
            return p._add(name, Button(rect, label=text, color=color(bg),
                                       text_color=color(fg), size=size))

        def rect(name, r, fill_color):
            return p._add(name, RectWidget(r, color=color(fill_color), fill=True))

        # ── body: entry screen 1 — id/user/test/test-lot ───────────────────────
        # The row labels are Buttons: pressing one loads its default value into
        # the field beside it (wired in EntryPhase / Entry2Phase).
        rect("div1", L.DIV1, "row_line")
        rect("div2", L.DIV2, "row_line")
        rect("div3", L.DIV3, "row_line")
        button("id_lbl",       L.BODY_LBL1, "white", "label_coral", 13, text="ID")
        field("id_fld",        L.BODY_FLD1, 22, "field_blue")
        button("user_lbl",     L.BODY_LBL2, "white", "label_coral", 13, text="User")
        field("user_fld",      L.BODY_FLD2, 18, "field_blue")
        button("test_lbl",     L.BODY_LBL3, "white", "label_coral", 13, text="Test")
        field("test_fld",      L.BODY_FLD3, 18, "field_blue")
        button("test_lot_lbl", L.BODY_LBL4, "white", "label_coral", 13, text="Test lot")
        field("test_lot_fld",  L.BODY_FLD4, 18, "field_blue")

        # ── body: entry screen 2 — temperature/weight/collect (reuses rows) ─────
        button("temp_lbl",      L.BODY_LBL1, "white", "label_coral", 13, text="Temp °C")
        field("temp_fld",       L.BODY_FLD1, 18, "field_blue")
        button("weight_lbl",    L.BODY_LBL2, "white", "label_coral", 13, text="Weight g")
        field("weight_fld",     L.BODY_FLD2, 18, "field_blue")
        button("collectdt_lbl", L.BODY_LBL3, "white", "label_coral", 13, text="Collect")
        field("collectdt_fld",  L.BODY_FLD3, 18, "field_blue")

        # ── body: timer cluster + instructions ─────────────────────────────────
        label("timer_lbl", L.TIMER_LBL, "white", "label_coral", 30)
        p._add("timer", TimerWidget(L.TIMER, text_color=color("timer_text"),
                                    bg=color("body_dark"), size=30))
        button("run_btn", L.RUN_BTN, "btn_text", "start_green", 30)
        label("instr3", L.INSTR3, "body_text", "body_dark", 20, align="left")
        label("instr4", L.INSTR4, "body_text", "body_dark", 20, align="left")
        label("collect_line", L.COLLECT_LINE, "black", "white", 13, align="left")

        # ── body: step-status chips (reagent 1 / 2 / Capture) ──────────────────
        # Small pill/badge labels; shown only in the reagent/capture phases, bg
        # recoloured per step state by Phase._show_steps (built with the
        # "pending" tint as the resting look).
        label("chip_a", L.CHIP_A, "body_text", "step_pending", 13, text="1")
        label("chip_b", L.CHIP_B, "body_text", "step_pending", 13, text="2")
        label("chip_c", L.CHIP_C, "body_text", "step_pending", 13, text="Capture")

        # ── body: wide label/field pairs ───────────────────────────────────────
        label("color_lbl",  L.BODY_LBL3, "white", "label_coral", 13, text="Read color")
        field("color_fld",  L.BODY_FLD3, 18, "field_yellow")
        label("result_lbl", L.BODY_LBL4, "white", "label_coral", 13, text="Result")
        field("result_fld", L.BODY_FLD4, 18, "field_blue", editable=False)

        # ── header ─────────────────────────────────────────────────────────────
        label("title", L.TITLE, "black", "white", 22, align="left")
        button("main_btn", L.MAIN_BTN, "btn_text", "start_green", 22)
        button("slot_btn", L.SLOT_BTN, "slot_b_text", "slot_b_bg", 34)

        # ── footer (static chrome) ─────────────────────────────────────────────
        button("prior_btn", L.PRIOR_BTN, "btn_text", "btn_gray", 13, text="PRIOR")
        label("clock", L.CLOCK, "hdr_text", "status_bg", 13)
        button("menu_btn", L.MENU_BTN, "btn_text", "btn_gray", 13, text="MENU")
        button("next_btn", L.NEXT_BTN, "btn_text", "btn_gray", 13, text="NEXT")

        # ── menu screen (its own screen, reached by the footer MENU button —
        # not part of the A/B toggle).  OK/CANCEL save or discard the choices
        # made while in the menu and return to the A/B slot that was showing.
        button("menu_cancel_btn", L.MENU_CANCEL_BTN, "hdr_text", "btn_gray",   18, text="CANCEL")
        label("menu_title",       L.MENU_TITLE,       "black",   "white",     20, text="Menu")
        button("menu_ok_btn",     L.MENU_OK_BTN,       "hdr_text", "start_green", 18, text="OK")
        button("menu_power",      L.MENU_POWER,        "hdr_text", "label_coral", 20, text="Power")
        button("menu_defaults",   L.MENU_DEFAULTS,     "hdr_text", "slot_a_bg",   20, text="Defaults")

        # ── modal overlay (drawn last = on top) ────────────────────────────────
        rect("modal_panel", L.MODAL_PANEL, "body_dark")
        label("modal_msg", L.MODAL_MSG, "body_text", "body_dark", 16)
        button("modal_yes", L.MODAL_YES, "btn_text", "start_green", 18)
        button("modal_no",  L.MODAL_NO,  "btn_text", "btn_gray", 18)
        button("modal_third", L.MODAL_3C, "btn_text", "btn_gray", 14)   # 3-choice modals

        log.info("Pool.build: %d widgets", len(p._widgets))
        return p

    # ── Access ────────────────────────────────────────────────────────────────

    def __getitem__(self, name: str) -> Widget:
        try:
            return self._widgets[name]
        except KeyError:
            raise KeyError(f"Pool has no widget {name!r}. "
                           f"Known: {sorted(self._widgets)}") from None

    def get(self, name: str) -> Optional[Widget]:
        return self._widgets.get(name)

    @property
    def names(self) -> List[str]:
        return list(self._widgets)

    # ── Content / state (what phases call most) ────────────────────────────────

    def set(self, name: str, text: str) -> Widget:
        """
        Set a widget's primary display string, whatever its type.
        Labels take text, Buttons take a label, TextBoxes take a value.
        """
        w = self[name]
        if isinstance(w, Label):
            w.text = text
        elif isinstance(w, Button):
            w.label = text
        elif isinstance(w, TextBox):
            w.set_value(text)
        else:
            log.warning("Pool.set: %r (%s) has no text content", name, type(w).__name__)
        return w

    def enable(self, name: str, on: bool = True) -> Widget:
        w = self[name]
        w.enabled = on
        return w

    def recolor(self, name: str, bg: Optional[str] = None,
                fg: Optional[str] = None) -> Widget:
        """
        Recolour a widget by *palette name* (phases still never touch raw
        values — the pool resolves the name).  bg sets the fill, fg the text.
        Used for status coding, e.g. the reagent/capture step chips.
        """
        w = self[name]
        if bg is not None:
            w.bg = color(bg)
        if fg is not None:
            w.color = color(fg)
        return w

    def on(self, name: str, callback: Optional[Callable]) -> Widget:
        """Wire a Button's press handler."""
        w = self[name]
        if isinstance(w, Button):
            w.on_press = callback
        else:
            log.warning("Pool.on: %r (%s) is not a Button", name, type(w).__name__)
        return w

    def put(self, name: str, text: Optional[str] = None,
            on: Optional[Callable] = None, enabled: bool = True) -> Widget:
        """
        Configure a widget and show it in one call: set its text (if given),
        wire its press handler (if a Button), set enabled, make it visible.

        The set-with-show discipline: because full-clear rendering keeps a
        shown widget's last content until something sets it, a phase should
        `put()` every widget it shows so stale text can never leak between
        phases.
        """
        w = self[name]
        if text is not None:
            self.set(name, text)
        if on is not None and isinstance(w, Button):
            w.on_press = on
        w.enabled = enabled
        w.visible = True
        return w

    # ── Visibility ─────────────────────────────────────────────────────────────

    def show(self, *names: str) -> None:
        for n in names:
            self[n].visible = True

    def hide(self, *names: str) -> None:
        for n in names:
            self[n].visible = False

    def hide_all(self) -> None:
        for w in self._widgets.values():
            w.visible = False

    def visible_names(self) -> List[str]:
        return [n for n, w in self._widgets.items() if w.visible]

    # ── Drawing ────────────────────────────────────────────────────────────────

    def render(self, display) -> None:
        """Full clear to the layout background, then draw every visible widget."""
        display.fill_screen(color(layout.SCREEN_BG))
        for w in self._widgets.values():
            if w.visible:
                w.invalidate()
                w.draw(display)

    def redraw(self, display, name: str) -> None:
        """Repaint one widget over its own rect (for 1 Hz updates). No-op if hidden."""
        w = self[name]
        if w.visible:
            w.draw(display)

    # ── Input routing ──────────────────────────────────────────────────────────

    def handle_touch(self, px: int, py: int, display=None) -> bool:
        """Dispatch a touch to the topmost visible, enabled widget under it."""
        for w in reversed(list(self._widgets.values())):
            if w.visible and w.enabled and w.hit_test(px, py):
                if w.handle_touch(px, py, display):
                    return True
        return False

    def focusable_widgets(self) -> List[Widget]:
        """Visible, enabled, focusable widgets in draw order (for Tab)."""
        return [w for w in self._widgets.values()
                if w.visible and w.enabled and w.focusable]

    def layout_modal_buttons(self, count: int) -> List[str]:
        """
        Position the modal action buttons for a 1/2/3-choice dialog and return
        their widget names in order.  One shared set of button widgets is
        repositioned per dialog (centred / two-across / three-across), so
        ModalPhase never mentions pixels.
        """
        if count <= 1:
            names, rects = ["modal_yes"], [layout.MODAL_ONE]
        elif count == 2:
            names, rects = ["modal_yes", "modal_no"], [layout.MODAL_YES, layout.MODAL_NO]
        else:
            names = ["modal_yes", "modal_no", "modal_third"]
            rects = [layout.MODAL_3A, layout.MODAL_3B, layout.MODAL_3C]
        for name, r in zip(names, rects):
            self[name].rect = r
        return names


# ─────────────────────────────────────────────────────────────────────────────
# Self-test  (no hardware required)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)-8s [%(name)s] %(message)s")

    from timer import TimerState

    class _MockDisplay:
        def fill_screen(self, color):
            print(f"  fill_screen #{color:04x}")
        def fill_rect(self, rect, color, **kw):
            print(f"    fill_rect ({rect.x:>3},{rect.y:>3} {rect.w:>3}x{rect.h:>2}) #{color:04x}")
        def draw_rect(self, rect, color, **kw):
            print(f"    draw_rect ({rect.x:>3},{rect.y:>3} {rect.w:>3}x{rect.h:>2}) #{color:04x}")
        def draw_text(self, text, rect, **kw):
            print(f"    draw_text {text!r:22s} ({rect.x:>3},{rect.y:>3})")

    disp = _MockDisplay()
    pool = Pool.build()

    print("\n== emulate an entry-phase render ==")
    pool.hide_all()
    pool.set("title", "New Test")
    pool.set("id_fld", "AB0124")
    pool.set("user_fld", "William Olsen")
    pool.set("main_btn", "Begin")
    pool.enable("main_btn", False)          # disabled until id + user filled
    pool.show("title", "main_btn", "slot_btn",
              "id_lbl", "id_fld", "user_lbl", "user_fld",
              "test_lbl", "test_fld", "test_lot_lbl", "test_lot_fld",
              "div1", "div2", "div3",
              "prior_btn", "clock", "next_btn")
    print(f"  visible: {len(pool.visible_names())} widgets")
    pool.render(disp)

    print("\n== emulate a reagent-phase render (timer bound) ==")
    pool.hide_all()
    st = TimerState.down(120, now=1000.0)
    pool.set("title", "AB0124")
    pool.set("timer_lbl", "Remaining")
    pool["timer"].bind(st)
    pool.set("run_btn", "START")
    pool.set("instr3", "Add Reagent 1")
    pool.set("instr4", "and press START")
    pool.show("title", "main_btn", "slot_btn", "timer_lbl", "timer", "run_btn",
              "instr3", "instr4", "chip_a", "chip_b", "chip_c",
              "prior_btn", "clock", "next_btn")
    pool.render(disp)

    print("\n== 1 Hz update: clock only ==")
    pool.set("clock", "14:32:07")
    pool.redraw(disp, "clock")

    print("\n== touch routing: press run_btn ==")
    pressed = []
    pool.on("run_btn", lambda: pressed.append(True))
    cx = layout.RUN_BTN.x + 5
    cy = layout.RUN_BTN.y + 5
    consumed = pool.handle_touch(cx, cy, disp)
    print(f"  consumed={consumed}, on_press fired={bool(pressed)}")
    assert consumed and pressed, "run_btn touch should consume and fire"

    print("\n== hidden widget ignores touch ==")
    pool.hide("run_btn")
    pressed.clear()
    consumed = pool.handle_touch(cx, cy, disp)
    print(f"  consumed={consumed}, on_press fired={bool(pressed)}")
    assert not consumed and not pressed, "hidden run_btn must not react"

    print("\n== all pool self-tests passed ==")
