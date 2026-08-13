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
from widgets import Button, ImageWidget, Label, RectWidget, TextBox, Widget
import version
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
        button("remark_lbl",    L.BODY_LBL4, "white", "label_coral", 13, text="Remark")
        field("remark_fld",     L.BODY_FLD4, 18, "field_blue")

        # ── body: timer cluster + instructions ─────────────────────────────────
        #label("timer_lbl", L.TIMER_LBL, "white", "label_coral", 22)
        label("timer_lbl", L.TIMER_LBL, "black", "white", 22)
        # Shares TIMER_LBL's rect with the label above: the read phase shows
        # this button in its place, and swaps back to the "Elapsed" label for
        # the duration of a capture (Tool 2 — see ReadPhase.enter).
        button("view_sample_btn", L.TIMER_LBL, "btn_text", "btn_gray", 14,
               text="View\nSample")
        p._add("timer", TimerWidget(L.TIMER, text_color=color("timer_text"),
                                    bg=color("body_dark"), size=30))
        button("run_btn", L.RUN_BTN, "btn_text", "start_green", 24)
        label("instr1", L.INSTR1, "black", "white", 20, align="left")
        label("instr2", L.INSTR2, "black", "white", 20, align="left")
        label("instr3", L.INSTR3, "black", "white", 20, align="left")
        # label("instr1", L.INSTR1, "body_text", "body_dark", 20, align="left")
        # label("instr2", L.INSTR2, "body_text", "body_dark", 20, align="left")
        label("collect_line", L.COLLECT_LINE, "black", "white", 13, align="left")

        # ── body: step-status chips (reagent 1 / 2 / Capture) ──────────────────
        # Small pill/badge labels; shown only in the reagent/capture phases, bg
        # recoloured per step state by Phase._show_steps (built with the
        # "pending" tint as the resting look).
        label("chip_a", L.CHIP_A, "body_text", "step_pending", 13, text="1")
        label("chip_b", L.CHIP_B, "body_text", "step_pending", 13, text="2")
        label("chip_c", L.CHIP_C, "body_text", "step_pending", 13, text="Capture")

        # ── body: read-strip prompt (ReadPhase) ─────────────────────────────────
        # manual_lbl is a fixed prompt ("Manually read color:") beside the entry
        # field — red/larger to draw the eye there; color_fld's border matches
        # (field_red) for the same reason. color_lbl (row 2, full width) carries
        # the capture instruction only, black on white, larger. Text for both
        # color_lbl states is set per-render by ReadPhase; manual_lbl's text
        # never changes, so ReadPhase only show()s it.
        label("manual_lbl", L.MANUAL_LBL, "alert_text", "white", 16, align="left",
              text="Manually read color:")
        field("color_fld",  L.READ_FLD, 18, "field_red")
        label("color_lbl",  L.READ_LBL, "black", "white", 20, align="left")
        label("result_lbl", L.BODY_LBL3, "white", "label_coral", 13, text="Result")
        field("result_fld", L.BODY_FLD3, 18, "field_blue", editable=False)

        # ── header ─────────────────────────────────────────────────────────────
        label("title", L.TITLE, "black", "white", 22, align="left")
        button("main_btn", L.MAIN_BTN, "btn_text", "start_green", 14)
        button("slot_btn", L.SLOT_BTN, "slot_b_text", "slot_b_bg", 34)
        # Visible alarm (arbiter-painted): drawn after the title so it sits on top.
        button("alarm_btn", L.ALARM_BTN, "alarm_text", "alarm_bg", 18)

        # ── footer (static chrome) ─────────────────────────────────────────────
        button("prior_btn", L.PRIOR_BTN, "btn_text", "btn_gray", 13, text="PRIOR")
        label("clock", L.CLOCK, "hdr_text", "status_bg", 13)
        button("menu_btn", L.MENU_BTN, "btn_text", "btn_gray", 13, text="MENU")
        button("next_btn", L.NEXT_BTN, "btn_text", "btn_gray", 13, text="NEXT")

        # ── menu screen (its own screen, reached by the footer MENU button —
        # not part of the A/B toggle).  OK/CANCEL save or discard the choices
        # made while in the menu and return to the A/B slot that was showing.
        button("menu_cancel_btn", L.MENU_CANCEL_BTN, "hdr_text", "btn_gray",   18, text="CANCEL")
        label("menu_title",       L.MENU_VER_TITLE,   "black",   "white",     20, text=f"Menu\nV {version.__version__}")
        button("menu_ok_btn",     L.MENU_OK_BTN,       "hdr_text", "start_green", 18, text="OK")
        # The 3x2 option grid: left column Capture/Defaults/Phases, right column
        # View Image/Config/Power.  The two grid slots still named for the stubs
        # they started as are positional, like the body rows — the arbiter says
        # what each one opens.
        button("menu_capture",    L.MENU_CAPTURE,      "hdr_text", "start_green", 18, text="Capture")
        button("menu_stub1",      L.MENU_STUB1,        "hdr_text", "btn_gray",    18, text="View\nImage")
        button("menu_defaults",   L.MENU_DEFAULTS,     "hdr_text", "slot_a_bg",   18, text="Defaults")
        button("menu_stub2",      L.MENU_STUB2,        "hdr_text", "btn_gray",    18, text="Config")
        button("menu_config",     L.MENU_CONFIG,       "hdr_text", "slot_b_bg",   18, text="Phases")
        button("menu_power",      L.MENU_POWER,        "hdr_text", "label_coral", 18, text="Power")

        # ── config editor (Menu → Config): roles, one role, one parameter ──────
        # Four body rows, each built both ways — one full-width button for the
        # role and history lists, and a name/value/action triple for the role
        # view — because the three views never share the screen.  The arbiter
        # decides per render which set it shows.
        label("cfge_title", L.MENU_TITLE, "black", "white", 11)
        cfge_rows = ((L.CFGE_ROW1, L.CFGE_LBL1, L.CFGE_FLD1, L.CFGE_BTN1),
                     (L.CFGE_ROW2, L.CFGE_LBL2, L.CFGE_FLD2, L.CFGE_BTN2),
                     (L.CFGE_ROW3, L.CFGE_LBL3, L.CFGE_FLD3, L.CFGE_BTN3),
                     (L.CFGE_ROW4, L.CFGE_LBL4, L.CFGE_FLD4, L.CFGE_BTN4))
        for i, (row_r, lbl_r, fld_r, btn_r) in enumerate(cfge_rows, 1):
            button(f"cfge_row{i}", row_r, "hdr_text", "slot_b_bg", 16)
            label(f"cfge_lbl{i}", lbl_r, "black", "white", 11, align="left")
            field(f"cfge_fld{i}", fld_r, 13, "field_blue")
            button(f"cfge_btn{i}", btn_r, "btn_text", "btn_gray", 13)
        button("cfge_prior",    L.CFGE_PRIOR,    "btn_text", "btn_gray", 13, text="< Prior")
        button("cfge_rollback", L.CFGE_ROLLBACK, "btn_text", "btn_gray", 13, text="Rollbk")
        button("cfge_save",     L.CFGE_SAVE,     "btn_text", "start_green", 13, text="Save")
        button("cfge_default",  L.CFGE_DEFAULT,  "btn_text", "btn_gray", 13, text="Default")
        button("cfge_next",     L.CFGE_NEXT,     "btn_text", "btn_gray", 13, text="Next >")

        # ── phases screen (Menu → Phases): the two reagent countdowns ───────────
        # Each row is label / seconds field / four step buttons; the arbiter
        # wires the steps and writes the staged value into the field.
        label("cfg_title",   L.MENU_TITLE, "black", "white", 20, text="Phases")
        label("cfg_r1_lbl",  L.CFG_LBL1,   "black", "white", 13, align="left", text="Reagent 1")
        field("cfg_r1_fld",  L.CFG_FLD1,   18, "field_blue", align="center")
        button("cfg_r1_m60", L.CFG_R1_M60, "btn_text", "btn_gray", 13, text="-60")
        button("cfg_r1_m10", L.CFG_R1_M10, "btn_text", "btn_gray", 13, text="-10")
        button("cfg_r1_p10", L.CFG_R1_P10, "btn_text", "btn_gray", 13, text="+10")
        button("cfg_r1_p60", L.CFG_R1_P60, "btn_text", "btn_gray", 13, text="+60")
        label("cfg_r2_lbl",  L.CFG_LBL2,   "black", "white", 13, align="left", text="Reagent 2")
        field("cfg_r2_fld",  L.CFG_FLD2,   18, "field_blue", align="center")
        button("cfg_r2_m60", L.CFG_R2_M60, "btn_text", "btn_gray", 13, text="-60")
        button("cfg_r2_m10", L.CFG_R2_M10, "btn_text", "btn_gray", 13, text="-10")
        button("cfg_r2_p10", L.CFG_R2_P10, "btn_text", "btn_gray", 13, text="+10")
        button("cfg_r2_p60", L.CFG_R2_P60, "btn_text", "btn_gray", 13, text="+60")
        label("cfg_hint",    L.CFG_HINT,   "black", "white", 13, align="left")
        button("cfg_restore", L.CFG_RESTORE, "btn_text", "btn_gray", 16, text="Restore")

        # ── capture screen (Menu → Capture): choice form, progress, result ──────
        # Mode + N buttons act as radio groups — the arbiter recolours the
        # selected one green and the rest gray on each render.
        label("cap_title",      L.CAP_TITLE,     "black", "white",       22, text="Capture")
        label("cap_mode_lbl",   L.CAP_MODE_LBL,  "black", "white",       18, text="Path")
        button("cap_mode_cal",  L.CAP_MODE_CAL,  "btn_text", "btn_gray", 14, text="Calibrate")
        button("cap_mode_norm", L.CAP_MODE_NORM, "btn_text", "btn_gray", 14, text="Normal")
        label("cap_n_lbl",      L.CAP_N_LBL,     "black", "white",       18, text="Exposures")
        button("cap_n1", L.CAP_N1, "btn_text", "btn_gray", 18, text="1")
        button("cap_n2", L.CAP_N2, "btn_text", "btn_gray", 18, text="2")
        button("cap_n3", L.CAP_N3, "btn_text", "btn_gray", 18, text="3")
        button("cap_n4", L.CAP_N4, "btn_text", "btn_gray", 18, text="4")
        button("cap_n5", L.CAP_N5, "btn_text", "btn_gray", 18, text="5")
        label("cap_progress",   L.CAP_PROGRESS,  "black", "white", 16, align="left")
        button("cap_back",  L.CAP_BACK,  "btn_text", "btn_gray",    16, text="Back")
        button("cap_start", L.CAP_START, "btn_text", "start_green", 18, text="START")

        # ── image viewer (Menu → View Image, Read → View Sample) ───────────────
        # One screen serving both entry points; the Read entry differs only in
        # the view box it opens at. The canvas carries the ROI overlays itself,
        # so there are no rect widgets over it.
        p._add("img_canvas", ImageWidget(L.IMG_CANVAS, bg=color("black")))
        button("img_zoom_in",  L.IMG_ZOOM_IN,  "btn_text", "btn_gray", 20, text="+")
        button("img_up",       L.IMG_UP,       "btn_text", "btn_gray", 20, text="▲")
        button("img_zoom_out", L.IMG_ZOOM_OUT, "btn_text", "btn_gray", 20, text="−")
        button("img_left",     L.IMG_LEFT,     "btn_text", "btn_gray", 20, text="◀")
        button("img_fit",      L.IMG_FIT,      "btn_text", "btn_gray", 14, text="Home")
        button("img_right",    L.IMG_RIGHT,    "btn_text", "btn_gray", 20, text="▶")
        button("img_step",     L.IMG_STEP,     "btn_text", "btn_gray", 14, text="1px")
        button("img_down",     L.IMG_DOWN,     "btn_text", "btn_gray", 20, text="▼")
        button("img_reimage",  L.IMG_REIMAGE,  "btn_text", "btn_gray", 14, text="Img")
        label("img_name",      L.IMG_NAME,     "black", "white", 14)
        button("img_edit",     L.IMG_EDIT,     "btn_text", "btn_gray", 16, text="Edit")
        button("img_save",     L.IMG_SAVE,     "btn_text", "start_green", 16, text="Save")
        button("img_cancel",   L.IMG_CANCEL,   "btn_text", "btn_gray", 16, text="Cancel")
        button("img_exit",     L.IMG_EXIT,     "btn_text", "btn_gray", 16, text="Exit")

        # ── recapture options dialog (viewer's Img button) ──────────────────────
        # Cancel/Capture reuse menu_cancel_btn/menu_ok_btn (top-corner chrome).
        # The main row buttons are narrow (75px) so their labels wrap to two
        # lines; the save toggles are narrower still (42px) and use a smaller
        # size for the same reason.
        label("recap_focus_lbl",   L.RECAP_FOCUS_LBL,  "black", "white", 14,
              align="left", text="Focus")
        button("recap_focus_auto", L.RECAP_FOCUS_AUTO, "btn_text", "btn_gray", 14,
               text="Auto\nFocus")
        button("recap_focus_cfg",  L.RECAP_FOCUS_CFG,  "btn_text", "btn_gray", 14,
               text="Reread\nCfg")
        button("recap_save_focus", L.RECAP_SAVE_FOCUS, "btn_text", "btn_gray", 10,
               text="Save\nFocus")
        label("recap_exp_lbl",     L.RECAP_EXP_LBL,    "black", "white", 14,
              align="left", text="Exposure")
        button("recap_exp_sample", L.RECAP_EXP_SAMPLE, "btn_text", "btn_gray", 14,
               text="Sample\nRect")
        button("recap_exp_whole",  L.RECAP_EXP_WHOLE,  "btn_text", "btn_gray", 14,
               text="Whole\nImage")
        button("recap_save_exp",   L.RECAP_SAVE_EXP,   "btn_text", "btn_gray", 10,
               text="Save\nExp")
        label("recap_image_lbl",   L.RECAP_IMAGE_LBL,  "black", "white", 14,
              align="left", text="Save Image")
        button("recap_image_roi",   L.RECAP_IMAGE_ROI,   "btn_text", "btn_gray", 14,
               text="Bound\nROI")
        button("recap_image_whole", L.RECAP_IMAGE_WHOLE, "btn_text", "btn_gray", 14,
               text="Whole\nImage")
        button("recap_image_dark", L.RECAP_IMAGE_DARK, "btn_text", "btn_gray", 10,
               text="Dark")

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
        """
        Wire a widget's press handler.

        Any widget carrying an `on_press` attribute qualifies — Buttons, and
        the viewer's ImageWidget, whose handler receives the tap position.
        """
        w = self[name]
        if hasattr(w, "on_press"):
            w.on_press = callback
        else:
            log.warning("Pool.on: %r (%s) takes no press handler",
                        name, type(w).__name__)
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
        if on is not None and hasattr(w, "on_press"):
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
        def draw_image(self, image, x=0, y=0):
            print(f"    draw_image {image.size[0]}x{image.size[1]} at ({x:>3},{y:>3})")

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
    pool.set("instr1", "Add Reagent 1")
    pool.set("instr2", "and press START")
    pool.show("title", "main_btn", "slot_btn", "timer_lbl", "timer", "run_btn",
              "instr1", "instr2", "chip_a", "chip_b", "chip_c",
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

    # ── image viewer ─────────────────────────────────────────────────────────
    # Runs last: it shows its own widgets and leaves them shown, and the viewer
    # controls share screen space with the reagent-phase cluster above (IMG_FIT
    # overlaps RUN_BTN), which is fine — the two screens are never both up.
    from PIL import Image

    _VIEWER = ("img_canvas", "img_zoom_in", "img_up", "img_zoom_out",
               "img_left", "img_fit", "img_right",
               "img_step", "img_down", "img_reimage", "img_name",
               "img_edit", "img_save", "img_cancel", "img_exit")

    print("\n== emulate an image-viewer render (no image yet) ==")
    pool.hide_all()
    pool.set("img_name", "sample")
    pool.show(*_VIEWER)
    print(f"  visible: {len(pool.visible_names())} widgets")
    pool.render(disp)     # canvas paints as flat bg until an image arrives

    print("\n== the same render with a composed canvas ==")
    canvas = layout.IMG_CANVAS
    pool["img_canvas"].set_image(Image.new("RGB", (canvas.w, canvas.h), "navy"))
    pool.redraw(disp, "img_canvas")

    print("\n== viewer layout checks ==")
    # The 13 controls must tile the right column exactly, and none may overlap
    # the canvas — a control under the image would be unreachable.
    controls = [pool[n].rect for n in _VIEWER if n != "img_canvas"]
    covered = sum(r.w * r.h for r in controls)
    column = (layout.SCREEN_W - canvas.w) * layout.SCREEN_H
    print(f"  canvas {canvas.w}x{canvas.h}   controls {len(controls)}   "
          f"covered {covered} of {column} px in the right column")
    assert covered == column, f"right column not tiled: {covered} != {column}"
    for r in controls:
        assert r.x >= canvas.right, f"control at x={r.x} overlaps the canvas"
        assert r.right <= layout.SCREEN_W and r.bottom <= layout.SCREEN_H, \
            f"control {r} runs off screen"
    for i, a in enumerate(controls):
        for b in controls[i + 1:]:
            overlap = (a.x < b.right and b.x < a.right
                       and a.y < b.bottom and b.y < a.bottom)
            assert not overlap, f"controls overlap: {a} and {b}"
    # Every control must be big enough to hit with a finger.
    for name in _VIEWER:
        if name == "img_canvas" or name == "img_name":
            continue
        r = pool[name].rect
        assert r.w >= 40 and r.h >= 40, f"{name} is only {r.w}x{r.h}"
    print("  tiled exactly, no overlaps, every button >= 40x40")

    print("\n== touch routing: every viewer control is reachable ==")
    for name in _VIEWER:
        if name in ("img_canvas", "img_name"):
            continue
        hits = []
        pool.on(name, lambda n=name: hits.append(n))
        r = pool[name].rect
        assert pool.handle_touch(r.x + r.w // 2, r.y + r.h // 2, disp), name
        assert hits == [name], f"{name} press routed to {hits}"
    print(f"  all {len(_VIEWER) - 2} buttons route to themselves")

    print("\n== config editor: rows and footer are laid out clear of each other ==")
    # The role view draws a name over a value in the left column with an action
    # button beside them; the roles/history views draw one full-width button in
    # the same space.  The two never share the screen, so ROW is allowed to
    # cover LBL/FLD/BTN — but nothing may cross a row boundary or the footer.
    _footer = [pool[n].rect for n in ("cfge_prior", "cfge_rollback", "cfge_save",
                                      "cfge_default", "cfge_next")]
    _rows = []
    for i in (1, 2, 3, 4):
        row = pool[f"cfge_row{i}"].rect
        lbl, fld, btn = (pool[f"cfge_{p}{i}"].rect for p in ("lbl", "fld", "btn"))
        for part in (lbl, fld, btn):
            assert row.y <= part.y and part.bottom <= row.bottom, \
                f"cfge row {i}: {part} escapes {row}"
        assert lbl.bottom <= fld.y, f"cfge row {i}: name overlaps its value"
        assert fld.right <= btn.x, f"cfge row {i}: value overlaps its button"
        _rows.append(row)
    for a, b in zip(_rows, _rows[1:]):
        assert a.bottom <= b.y, f"cfge rows overlap: {a} and {b}"
    assert _rows[-1].bottom <= _footer[0].y, "cfge row 4 runs into the footer"
    for a, b in zip(_footer, _footer[1:]):
        assert a.right <= b.x, f"cfge footer buttons overlap: {a} and {b}"
    assert _footer[-1].right <= layout.SCREEN_W, "cfge footer runs off screen"
    assert _footer[-1].bottom <= layout.SCREEN_H, "cfge footer runs off screen"
    # Five across leaves each footer button under the 40 px the viewer's pad
    # gets; 62 x 33 is the floor a finger still lands on reliably.
    for r in _footer:
        assert r.w >= 60 and r.h >= 33, f"cfge footer button is only {r.w}x{r.h}"
    print(f"  4 rows y{_rows[0].y}..{_rows[-1].bottom}, "
          f"footer y{_footer[0].y}..{_footer[-1].bottom}, no overlaps")

    print("\n== palette: ROI overlay colours unpack to RGB ==")
    from palette import PALETTE, rgb888, unpack565
    assert unpack565(0xFFFF) == (255, 255, 255), unpack565(0xFFFF)
    assert unpack565(0x0000) == (0, 0, 0)
    roi_names = ("roi_idle", "roi_selected", "roi_edit", "roi_edge")
    seen = {}
    for n in roi_names:
        assert n in PALETTE, f"palette is missing {n}"
        seen[n] = rgb888(n)
        print(f"  {n:14s} #{PALETTE[n]:04x} -> {seen[n]}")
    assert len(set(seen.values())) == 4, "the four ROI colours must be distinct"

    print("\n== all pool self-tests passed ==")
