"""
timer.py  —  TimerState (model) + TimerWidget (view).
-----------------------------------------------------
A countdown/count-up timer split along the same view/state seam as the rest of
the phase model:

    TimerState   pure data, lives in a Slot, survives while the slot is
                 backgrounded.  Stores an absolute monotonic deadline (down)
                 or start (up), so "how much is left/elapsed" is computed from
                 `now` on demand and stays correct with zero background work.
                 Every method takes `now` as an argument and never reads the
                 clock itself — trivially testable, and the caller (the main
                 loop) owns the single clock read per pass.

    TimerWidget  one instance in the pool, renders whichever slot's TimerState
                 it is bound to.  The rect is split into thirds:
                     left  ‖   pause      (shown while running)
                     centre    MM:SS
                     right ›   resume      (shown while paused)

Pause freezes the displayed value; resume re-anchors the deadline to `now` so
no time is lost to the pause.

Run directly for the self-test (no hardware required):
    PYTHONPATH=src python3 src/timer.py
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from geometry import Rect
from widgets import Widget

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# TimerState  (model)
# ─────────────────────────────────────────────────────────────────────────────

class TimerState:
    """
    A running clock, up or down, anchored to a monotonic time value.

    Do not construct directly in normal use — call TimerState.down(secs, now)
    or TimerState.up(now).

    Fields
    ------
    mode     : 'down' | 'up'
    running  : True while advancing; False while paused/stopped
    _anchor  : monotonic value.  down -> the deadline; up -> the start.
    _frozen  : displayed seconds while not running (None while running)
    total    : the down-timer's starting seconds (for progress bars); None for up
    """

    __slots__ = ("mode", "running", "_anchor", "_frozen", "total")

    def __init__(self, mode: str, anchor: float,
                 running: bool = True, frozen: Optional[float] = None,
                 total: Optional[float] = None):
        self.mode    = mode
        self.running = running
        self._anchor = anchor
        self._frozen = frozen
        self.total   = total

    @classmethod
    def down(cls, seconds: float, now: float) -> "TimerState":
        """A countdown of `seconds`, expiring at now + seconds."""
        return cls("down", now + seconds, total=seconds)

    @classmethod
    def up(cls, now: float) -> "TimerState":
        """A count-up from 0, started at `now`, with no expiry."""
        return cls("up", now)

    def value(self, now: float) -> float:
        """Seconds to display: remaining (down) or elapsed (up), never < 0."""
        if not self.running and self._frozen is not None:
            return self._frozen
        if self.mode == "down":
            return max(0.0, self._anchor - now)
        return max(0.0, now - self._anchor)

    def expired(self, now: float) -> bool:
        """True once a running countdown reaches zero. Up-timers never expire."""
        return self.mode == "down" and self.value(now) <= 0.0

    def pause(self, now: float) -> None:
        """Freeze the displayed value. No-op if already paused."""
        if not self.running:
            return
        self._frozen = self.value(now)
        self.running = False

    def resume(self, now: float) -> None:
        """Re-anchor to `now` and advance again. No-op if already running."""
        if self.running:
            return
        rem = self._frozen if self._frozen is not None else 0.0
        self._anchor = now + rem if self.mode == "down" else now - rem
        self._frozen = None
        self.running = True

    def stop(self, now: float) -> None:
        """Freeze permanently at the current value (e.g. 'Final' elapsed time)."""
        self._frozen = self.value(now)
        self.running = False

    def text(self, now: float) -> str:
        """The MM:SS string for the current value."""
        s = int(round(self.value(now)))
        return f"{s // 60:02d}:{s % 60:02d}"

    def __repr__(self) -> str:
        return (f"TimerState(mode={self.mode!r}, running={self.running}, "
                f"anchor={self._anchor:.3f}, frozen={self._frozen})")


# ─────────────────────────────────────────────────────────────────────────────
# TimerWidget  (view)
# ─────────────────────────────────────────────────────────────────────────────

class TimerWidget(Widget):
    """
    Renders a TimerState into a rect and turns its left/right thirds into
    pause/resume touch zones on the bound state.

    Parameters
    ----------
    rect         : position and size
    text_color   : RGB565 for the MM:SS value
    bg           : RGB565 background
    afford_color : RGB565 for the ‖ / › affordance glyphs (default text_color)
    size         : font size for the value
    on_pause     : optional callable invoked after a pause touch
    on_resume    : optional callable invoked after a resume touch
    name         : identifier for logging / pool key
    widget_id    : optional numeric id (0 = none)
    """

    def __init__(
        self,
        rect:         Rect,
        text_color:   int = 0x07E0,
        bg:           int = 0x0000,
        afford_color: Optional[int] = None,
        size:         int = 30,
        on_pause:     Optional[Callable] = None,
        on_resume:    Optional[Callable] = None,
        name:         str = "",
        widget_id:    int = 0,
    ):
        super().__init__(rect, name=name, widget_id=widget_id)
        self.text_color   = text_color
        self.bg           = bg
        self.afford_color = afford_color if afford_color is not None else text_color
        self.size         = size
        self.on_pause     = on_pause
        self.on_resume    = on_resume
        self.state: Optional[TimerState] = None
        self.interactive  = True    # False = display only (no ‖/› glyph, no touch)

        third = rect.w // 3
        self._left  = Rect(rect.x,              rect.y, third, rect.h)
        self._right = Rect(rect.x + 2 * third,  rect.y, rect.w - 2 * third, rect.h)

    def bind(self, state: Optional[TimerState], interactive: bool = True) -> None:
        """
        Point the widget at the TimerState it should draw (the focused slot's).

        interactive=False renders it as a plain readout — no pause/resume glyph
        and no touch response — for an armed preview (the full duration shown
        before START) or a stopped final time.
        """
        self.state = state
        self.interactive = interactive

    def draw(self, display, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        wid = str(self.widget_id) if self.widget_id else ""
        display.fill_rect(self.rect, self.bg, widget_id=wid)

        if self.state is not None and self.interactive:
            glyph, zone = ("||", self._left) if self.state.running else (">", self._right)
            display.draw_text(glyph, zone,
                              color=self.afford_color, bg=self.bg,
                              size=self.size, align="center", valign="middle")

        text = self.state.text(now) if self.state is not None else "--:--"
        display.draw_text(text, self.rect,
                          color=self.text_color, bg=self.bg,
                          size=self.size, align="center", valign="middle",
                          widget_id=wid)

    def handle_touch(self, px: int, py: int, display=None) -> bool:
        if self.state is None or not self.interactive or not self.hit_test(px, py):
            return False
        now = time.monotonic()
        frac = (px - self.rect.x) / max(1, self.rect.w)
        if frac < 1 / 3:
            self.state.pause(now)
            cb = self.on_pause
        elif frac > 2 / 3:
            self.state.resume(now)
            cb = self.on_resume
        else:
            return True   # centre: consume, no state change
        if display is not None:
            self.draw(display, now)
        if callable(cb):
            cb()
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Self-test  (no hardware required)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)-8s [%(name)s] %(message)s")

    def check(label, got, want):
        ok = "OK " if got == want else "XX "
        print(f"  {ok}{label:38s} got={got!r} want={want!r}")
        assert got == want, f"{label}: {got!r} != {want!r}"

    print("== TimerState: countdown ==")
    t = TimerState.down(120, now=1000.0)
    check("t=1000 text",        t.text(1000.0), "02:00")
    check("t=1005 text",        t.text(1005.0), "01:55")
    check("t=1005 not expired", t.expired(1005.0), False)
    check("t=1120 text",        t.text(1120.0), "00:00")
    check("t=1120 expired",     t.expired(1120.0), True)
    check("t=1130 clamps at 0", t.text(1130.0), "00:00")

    print("== TimerState: pause holds, resume loses no time ==")
    t = TimerState.down(120, now=1000.0)
    t.pause(now=1030.0)                      # 90s remain
    check("paused text",         t.text(1030.0), "01:30")
    check("paused holds later",  t.text(9999.0), "01:30")
    check("paused not expired",  t.expired(9999.0), False)
    t.resume(now=2000.0)                      # deadline -> 2090
    check("resumed text",        t.text(2000.0), "01:30")
    check("resumed +85",         t.text(2085.0), "00:05")
    check("resumed +90 expired", t.expired(2090.0), True)

    print("== TimerState: count-up ==")
    u = TimerState.up(now=0.0)
    check("up t=5",   u.text(5.0),  "00:05")
    check("up never expires", u.expired(9999.0), False)
    u.pause(now=10.0)
    check("up paused", u.text(50.0), "00:10")
    u.resume(now=100.0)
    check("up resumed +5", u.text(105.0), "00:15")
    u.stop(now=125.0)                          # elapsed since resume: 25 -> 10+...
    check("up stopped holds", u.text(999.0), "00:35")

    print("== TimerWidget: draw running vs paused ==")

    class _MockDisplay:
        def fill_rect(self, rect, color, **kw):
            print(f"    fill_rect ({rect.x},{rect.y} {rect.w}x{rect.h}) #{color:04x}")
        def draw_text(self, text, rect, **kw):
            print(f"    draw_text {text!r:8s} ({rect.x},{rect.y} {rect.w}x{rect.h})"
                  f" align={kw.get('align')}")

    disp = _MockDisplay()
    w = TimerWidget(Rect(90, 60, 140, 42), text_color=0x07E0, bg=0x1084, name="timer")
    st = TimerState.down(120, now=1000.0)
    w.bind(st)
    print("  running (expect '||' on the left third, then '02:00'):")
    w.draw(disp, now=1000.0)
    st.pause(now=1000.0)
    print("  paused (expect '>' on the right third, then '02:00'):")
    w.draw(disp, now=1000.0)

    print("\n== all timer self-tests passed ==")
