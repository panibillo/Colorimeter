"""
splash_screen.py  —  The boot splash: what the operator watches while we load.
-------------------------------------------------------------------------------
Shown by Arbiter.start() from the moment the display driver answers until the
operator presses MENU or START.  It narrates the rest of the boot one step at a
time, reports anything that had to be loaded from a default, and ends on
"Ready" — or on the first failure, with START greyed out.

Deliberately independent of pool.py / layout.py, for the same reason
shutdown_screen.py is: those describe the phase/slot screen, and during most of
the boot it does not exist yet (Pool.build() is one of the steps this screen is
narrating).  All this needs is a working display driver.

Two things live here:

    BootLog — records steps that ran *before* the display could show anything
              (db_bootstrap.ensure(), the driver imports, disp.init() itself).
              The launchers fill one in and hand it to Arbiter.start(), which
              replays it onto the splash's first paint.

    Splash  — the narrator.  Its step list is a model, not "whatever was last
              drawn", so it can be repainted after a notice dialog covers it or
              after an aborted shutdown screen wipes the panel.

              The steps hold the screen only while the boot is running.  Once
              the completeness checks are done, problems() replaces them with
              what went wrong — a list of things that happened is worth
              nothing next to the list of things that should not have.

Usage (see Arbiter.start):
    splash = Splash(disp, simulation=_SIMULATION_MODE, version=__version__)
    splash.open()
    splash.replay(boot)                      # steps that already happened
    with splash.running("Touch"):            # narrate a step, time it
        touch.init()
    splash.attach_input(touch=touch, kb=kb)  # input exists from here on
    splash.notice("Loading protocol.reagent1_secs = 120 (rowid 27)")
    splash.problems(report.problems())       # replaces the step list
    choice = splash.finish("Ready", ok=True) # "menu" | "start"
"""

from __future__ import annotations

import contextlib
import functools
import logging
import textwrap
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from geometry import Rect
from palette import color
from widgets import Button

log = logging.getLogger(__name__)

_POLL_S = 0.02
_NOTICE_TIMEOUT_S = 60.0     # a notice nobody answers must not strand the boot
_DEAF_PAUSE_S     = 2.0      # how long a notice shows when there is no input yet

# ── Step states ─────────────────────────────────────────────────────────────
# The text in a step's right-hand cell, and the palette name it is drawn in.
RUNNING = "..."
OK      = "ok"
WARN    = "warn"
FAIL    = "FAIL"

_TONE = {
    RUNNING: "hdr_blue",
    OK:      "body_dark",
    WARN:    "step_running",   # amber
    FAIL:    "edited_text",    # red
}

# A step that finished faster than this reads "ok" rather than a duration —
# three digits of nothing useful is worse than a word.
_TIMED_S = 0.3

# ── Layout (independent of layout.py — this screen has its own geometry) ─────
_BG = "white"                            # matches layout.SCREEN_BG

TITLE     = Rect(  0,   4, 320, 44)      # "Colorimeter"
SUBTITLE  = Rect(  0,  48, 320, 20)      # "MGWWG v0.4.0"
RULE      = Rect( 16,  70, 288,  1)
STEPS     = Rect( 12,  76, 296, 98)      # the step list
STATUS    = Rect(  0, 178, 320, 18)      # "Ready" / the first failure
MENU_BTN  = Rect( 12, 198,  96, 36)
START_BTN = Rect(212, 198,  96, 36)

TITLE_SIZE    = 34
SUBTITLE_SIZE = 15
STEP_SIZE     = 12
STATUS_SIZE   = 14
BTN_SIZE      = 16

STEP_LH   = 14                           # line height at STEP_SIZE
MAX_LINES = STEPS.h // STEP_LH           # 7

_NAME_X,  _NAME_W  =  12, 222            # left cell:  the step's label
_STATE_X, _STATE_W = 242,  66            # right cell: its state, right-aligned

# The notice dialog covers the step list only, so the title never flickers.
# The problem list replaces the step list once the checks are done, in the
# same region.  Smaller and tighter than a step row, because each line carries
# a sentence rather than a word: STEPS.h // PROBLEM_LH lines fit, and anything
# past that is counted rather than shown.
PROBLEM_SIZE  = 14
PROBLEM_LH    = PROBLEM_SIZE + 1
PROBLEM_LINES = STEPS.h // PROBLEM_LH     # 8
_PROBLEM_CHARS = 44                       # STEPS.w at PROBLEM_SIZE, with margin

NOTE_PANEL = Rect( 14,  74, 292, 100)
NOTE_TEXT  = Rect( 22,  82, 276,  56)
NOTE_BTN   = Rect(126, 142,  68,  28)
NOTE_SIZE  = 12
_NOTE_CHARS = 38                         # NOTE_TEXT.w at NOTE_SIZE, with margin


def _name_rect(row: int) -> Rect:
    return Rect(_NAME_X, STEPS.y + row * STEP_LH, _NAME_W, STEP_LH)


def _state_rect(row: int) -> Rect:
    return Rect(_STATE_X, STEPS.y + row * STEP_LH, _STATE_W, STEP_LH)


def _problem_rect(row: int) -> Rect:
    return Rect(STEPS.x, STEPS.y + row * PROBLEM_LH, STEPS.w, PROBLEM_LH)


def _problem_lines(found) -> list:
    """
    Wrap (status, text) pairs into rows that fit, keeping the worst.

    Failures come first from CheckReport.problems(), so truncating from the
    end drops warnings before it drops anything that stopped the boot.  The
    last row says how many were dropped rather than letting them vanish.

    Args:
        found: (status, text) pairs.

    Returns:
        (status, line) pairs, at most PROBLEM_LINES of them.

    Example:
        >>> _problem_lines([("warn", "a")])
        [('warn', 'a')]
    """
    rows = []
    for i, (status, text) in enumerate(found):
        wrapped = textwrap.wrap('> ' + text, _PROBLEM_CHARS) or [" "]
        remaining = PROBLEM_LINES - len(rows)
        if len(wrapped) > remaining or (remaining <= 1 and i < len(found) - 1):
            left = len(found) - i
            rows = rows[:PROBLEM_LINES - 1]
            rows.append((status, f"...and {left} more (see the log)"))
            return rows
        rows.extend((status, line) for line in wrapped)
    return rows


def _state_text(seconds: float) -> str:
    """The right-hand cell for a step that finished cleanly."""
    return f"{seconds:.1f}s" if seconds >= _TIMED_S else OK


def _never_raises(fallback=None):
    """
    Swallow and log any exception out of a Splash method.

    A splash screen must not be the thing that prevents a boot, so a bug in
    here degrades to a blank area of screen rather than a stack trace on an
    instrument with no console.  Same rationale as shutdown_screen.run.
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            try:
                return fn(self, *args, **kwargs)
            except Exception:
                log.exception("splash_screen: %s failed", fn.__name__)
                return fallback
        return wrapper
    return deco


# ── BootLog ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BootStep:
    """One step that ran before the splash could draw it."""
    label:   str
    seconds: float
    state:   str = OK


class BootLog:
    """
    Steps and notices from before there was a screen to put them on.

    db_bootstrap.ensure() runs in the launcher, before any display exists, and
    the display's own bring-up obviously cannot narrate itself.  Recording them
    here lets Splash.replay() show them as already-done on its first paint,
    rather than pretending the boot started when the panel lit.

    Example:
        >>> boot = BootLog()
        >>> with boot.step("Database"):
        ...     pass
        >>> boot.steps[0].label
        'Database'
    """

    def __init__(self) -> None:
        self.steps:    List[BootStep] = []
        self.notices:  List[str]      = []
        self.restored: list           = []   # db_bootstrap.Restored records

    @contextlib.contextmanager
    def step(self, label: str):
        """
        Time a pre-display step and record how it ended.

        Re-raises whatever the body raised, after recording it as failed, so a
        crash during the database or display bring-up still reaches the
        launcher's handler.
        """
        t0 = time.monotonic()
        try:
            yield
        except BaseException:
            self.steps.append(BootStep(label, time.monotonic() - t0, FAIL))
            raise
        self.steps.append(BootStep(label, time.monotonic() - t0, OK))

    def note(self, message: str) -> None:
        """Queue a message for the splash to show once input exists."""
        self.notices.append(message)


# ── Splash ──────────────────────────────────────────────────────────────────

@dataclass
class _Step:
    label: str
    state: str = RUNNING
    t0:    float = field(default_factory=time.monotonic)


class Splash:
    """
    The boot splash: a title, a version line, a list of steps, and two buttons.

    Every public method is guarded — see _never_raises.  The buttons exist from
    open() but stay invisible (and so untouchable, Widget.hit_test requires
    visible) until finish() arms them, so a stray tap during the boot does
    nothing.

    Args:
        disp:       A display driver (ILI9341 or its mock).
        simulation: True when disp is the matplotlib mock, whose event queue
                    has to be pumped by hand.
        version:    The release string for the subtitle, from version.py.
    """

    ORG = "MGWWG"

    def __init__(self, disp, *, simulation: bool = False,
                 version: str = "") -> None:
        self._disp       = disp
        self._simulation = simulation
        self._version    = version

        self._steps:  List[_Step] = []
        self._scroll  = 0                    # index of the top visible step
        self._problems: List[tuple] = []     # (status, text); replaces the list
        self._status  = ""
        self._status_tone = "body_dark"

        self._touch = None
        self._kb    = None
        self._armed = False                  # True once finish() shows buttons

        self._menu_btn = Button(
            MENU_BTN, "MENU", color=color("btn_gray"),
            text_color=color("btn_text"), size=BTN_SIZE, name="menu")
        self._start_btn = Button(
            START_BTN, "START", color=color("start_green"),
            text_color=color("btn_text"), size=BTN_SIZE, name="start")
        for b in (self._menu_btn, self._start_btn):
            b.visible = False

    # ── Setup ───────────────────────────────────────────────────────────────

    @_never_raises()
    def attach_input(self, touch=None, kb=None) -> None:
        """
        Hand the splash the input devices, once they have been initialised.

        Before this, notice() cannot wait for an OK press and says so instead
        of hanging.
        """
        self._touch = touch
        self._kb    = kb

    @_never_raises()
    def open(self) -> None:
        """Paint the title, version line and an empty step list."""
        self.repaint()

    @_never_raises()
    def replay(self, boot: BootLog) -> None:
        """
        Show steps that finished before the splash existed, already done.

        Args:
            boot: The BootLog the launcher filled in.
        """
        for bs in boot.steps:
            self._steps.append(_Step(bs.label, state=(
                bs.state if bs.state != OK else _state_text(bs.seconds))))
        self._reflow()
        self._draw_steps()

    # ── Narration ───────────────────────────────────────────────────────────

    @_never_raises(fallback=-1)
    def step(self, text: str) -> int:
        """
        Append a step and mark it running.

        Returns:
            Its index, for a later done()/warn()/fail().  -1 if the paint
            failed, which the setters treat as "the last step".
        """
        self._steps.append(_Step(text))
        if self._reflow():
            self._draw_steps()
        else:
            self._draw_row(len(self._steps) - 1)
        return len(self._steps) - 1

    @_never_raises()
    def done(self, index: Optional[int] = None, *, state: str = "") -> None:
        """Mark a step finished: its duration, or 'ok' if it was quick."""
        s = self._at(index)
        if s is not None:
            s.state = state or _state_text(time.monotonic() - s.t0)
            self._draw_state(self._steps.index(s))

    @_never_raises()
    def warn(self, index: Optional[int] = None, *, state: str = WARN) -> None:
        """Mark a step finished with a warning (amber)."""
        self._set_state(index, state)

    @_never_raises()
    def fail(self, index: Optional[int] = None, *, state: str = FAIL) -> None:
        """Mark a step failed (red)."""
        self._set_state(index, state)

    @_never_raises()
    def status(self, message: str, *, tone: str = "body_dark") -> None:
        """
        Write the line above the buttons.

        Used while the completeness checks run, so a slow one says which it
        is; finish() overwrites it with the ending message.
        """
        self._status = message
        self._status_tone = tone
        self._draw_status()

    @contextlib.contextmanager
    def running(self, text: str):
        """
        Narrate a step for the duration of a block.

        Marks it failed and re-raises if the body raises, so a crash during
        init still propagates to the launcher (and on to shutdown_screen)
        while leaving the operator looking at which step it died on.

        Example:
            >>> with splash.running("Touch"):     # doctest: +SKIP
            ...     touch.init()
        """
        i = self.step(text)
        try:
            yield
        except BaseException:
            self.fail(i)
            raise
        self.done(i)

    # ── Notice dialog ───────────────────────────────────────────────────────

    @_never_raises()
    def notice(self, message: str, *,
               timeout_s: float = _NOTICE_TIMEOUT_S) -> None:
        """
        Show a message over the step list and wait for OK.

        Used for "Loading {role}.{name} = {value} (rowid {n})" — one dialog,
        one OK press, per parameter that had to come from a default.  Always
        logged as well, so an unattended instrument still records it.

        Args:
            message:   The text, wrapped to the panel.
            timeout_s: Dismiss itself after this long rather than strand a
                       boot nobody is watching.
        """
        log.warning("splash: %s", message)

        self._disp.fill_rect(NOTE_PANEL, color("body_dark"))
        self._disp.draw_text(
            "\n".join(textwrap.wrap(message, _NOTE_CHARS)) or " ",
            NOTE_TEXT, color=color("body_text"), bg=color("body_dark"),
            size=NOTE_SIZE, align="center", valign="middle")

        ok = Button(NOTE_BTN, "OK", color=color("start_green"),
                    text_color=color("btn_text"), size=14, name="ok")

        if self._kb is None and self._touch is None:
            # Nothing can press OK yet.  Show it, pause so it is readable, and
            # move on — a splash must never be able to hang the boot.
            log.warning("splash: no input attached, notice self-dismisses")
            ok.enabled = False
            ok.draw(self._disp)
            self._wait(_DEAF_PAUSE_S)
        else:
            ok.draw(self._disp)
            ok.set_focused(True, self._disp)
            self._pump([ok], 0, timeout_s)

        self._draw_steps()

    @_never_raises()
    def problems(self, found) -> None:
        """
        Replace the step list with what the checks found.

        The steps have said all they can by now — they are a list of things
        that happened, and every one of them happened.  What the operator
        needs off the screen is the exception: each failure and each warning,
        in its own words.  Nothing is drawn when nothing went wrong, and the
        ending message carries the whole story.

        Args:
            found: (status, text) pairs from CheckReport.problems(), failures
                   first.  `status` selects the colour; anything unrecognised
                   is drawn as a warning, since a check that reported at all
                   had something to say.

        Example:
            >>> splash.problems([("fail", "Database: ...")])   # doctest: +SKIP
        """
        self._problems = list(found)
        for status, text in self._problems:
            log.warning("splash: %s", text)
        self._draw_problems()

    # ── Ending ──────────────────────────────────────────────────────────────

    @_never_raises(fallback="start")
    def finish(self, message: str = "Ready", *, ok: bool = True,
               timeout_s: Optional[float] = None) -> str:
        """
        Show the ending message, reveal the buttons, and wait for a press.

        Args:
            message:   The status line — "Ready", or what failed.
            ok:        False greys out START, leaving MENU as the way to reach
                       the Config editor and fix whatever failed.
            timeout_s: None waits indefinitely, which is the normal case.

        Returns:
            "menu" or "start".  "start" on any internal failure, so a broken
            splash still lets the instrument boot.
        """
        self._status = message
        self._status_tone = "body_dark" if ok else "edited_text"
        self._draw_status()

        self._armed = True
        self._start_btn.enabled = ok
        for b in (self._menu_btn, self._start_btn):
            b.visible = True
            b.draw(self._disp)

        buttons = [self._menu_btn, self._start_btn]
        focus   = 1 if ok else 0
        buttons[focus].set_focused(True, self._disp)

        choice = self._pump(buttons, focus, timeout_s)
        log.info("splash: chose %r", choice)
        return choice or ("start" if ok else "menu")

    # ── Painting ────────────────────────────────────────────────────────────

    @_never_raises()
    def repaint(self) -> None:
        """
        Redraw the whole screen from the model.

        Called on open(), and again by Arbiter._resume_display when an aborted
        shutdown screen has painted over the splash.
        """
        self._disp.fill_screen(color(_BG))
        self._disp.draw_text("Colorimeter", TITLE, color=color("hdr_blue"),
                             bg=color(_BG), size=TITLE_SIZE,
                             align="center", valign="middle")
        self._disp.draw_text(f"{self.ORG} v{self._version}", SUBTITLE,
                             color=color("hdr_blue"), bg=color(_BG),
                             size=SUBTITLE_SIZE, align="center",
                             valign="middle")
        self._disp.fill_rect(RULE, color("row_line"))
        if self._problems:
            self._draw_problems()
        else:
            self._draw_steps()
        self._draw_status()
        if self._armed:
            for b in (self._menu_btn, self._start_btn):
                b.draw(self._disp)

    def _draw_steps(self) -> None:
        if self._problems:
            # The list has been handed over; a stray repaint must not undo it.
            self._draw_problems()
            return
        self._disp.fill_rect(STEPS, color(_BG))
        for i in range(self._scroll, len(self._steps)):
            self._draw_row(i)

    def _draw_problems(self) -> None:
        self._disp.fill_rect(STEPS, color(_BG))
        lines = _problem_lines(self._problems)
        for row, (status, text) in enumerate(lines):
            self._disp.draw_text(
                text, _problem_rect(row),
                color=color("fail_text" if status == "fail"
                            else "warning_text"),
                bg=color(_BG), size=PROBLEM_SIZE, align="left",
                valign="middle")

    def _draw_row(self, index: int) -> None:
        row = index - self._scroll
        if not 0 <= row < MAX_LINES:
            return
        s = self._steps[index]
        self._disp.fill_rect(_name_rect(row), color(_BG))
        self._disp.draw_text(s.label, _name_rect(row), color=color("black"),
                             bg=color(_BG), size=STEP_SIZE,
                             align="left", valign="middle")
        self._draw_state(index)

    def _draw_state(self, index: int) -> None:
        row = index - self._scroll
        if not 0 <= row < MAX_LINES:
            return
        s = self._steps[index]
        tone = _TONE.get(s.state, "body_dark")
        self._disp.fill_rect(_state_rect(row), color(_BG))
        self._disp.draw_text(s.state, _state_rect(row), color=color(tone),
                             bg=color(_BG), size=STEP_SIZE,
                             align="right", valign="middle")

    def _draw_status(self) -> None:
        self._disp.fill_rect(STATUS, color(_BG))
        if self._status:
            self._disp.draw_text(self._status, STATUS,
                                 color=color(self._status_tone),
                                 bg=color(_BG), size=STATUS_SIZE,
                                 align="center", valign="middle")

    # ── Model helpers ───────────────────────────────────────────────────────

    def _at(self, index: Optional[int]) -> Optional[_Step]:
        """The step an index refers to; the last one for None or a bad index."""
        if not self._steps:
            return None
        if index is None or not 0 <= index < len(self._steps):
            return self._steps[-1]
        return self._steps[index]

    def _set_state(self, index: Optional[int], state: str) -> None:
        s = self._at(index)
        if s is not None:
            s.state = state
            self._draw_state(self._steps.index(s))

    def _reflow(self) -> bool:
        """
        Keep the newest step visible.

        Returns:
            True if the window moved, meaning the caller owes a full repaint
            of the step region rather than a single row.
        """
        scroll = max(0, len(self._steps) - MAX_LINES)
        if scroll == self._scroll:
            return False
        self._scroll = scroll
        return True

    # ── Input ───────────────────────────────────────────────────────────────

    def _events(self) -> None:
        """Let the matplotlib mock repaint and deliver clicks."""
        if self._simulation and hasattr(self._disp, "process_events"):
            self._disp.process_events()

    def _wait(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._events()
            time.sleep(_POLL_S)

    def _pump(self, buttons: List[Button], focus: int,
              timeout_s: Optional[float]) -> str:
        """
        Block until one of `buttons` fires, or the timeout expires.

        Tab/shift-tab move focus over the enabled buttons only — a greyed
        START is skipped rather than being a dead stop in the cycle.

        Returns:
            The pressed button's name, or "" on timeout.
        """
        picked: List[str] = []
        for b in buttons:
            b.on_press = lambda name=b.name: picked.append(name)

        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        live = [i for i, b in enumerate(buttons) if b.enabled]
        if focus not in live and live:
            focus = live[0]

        while not picked:
            if deadline is not None and time.monotonic() >= deadline:
                log.info("splash: timed out waiting for a press")
                return ""

            self._events()

            key = self._kb.read_one() if self._kb else None
            if key in ("tab", "shift-tab") and len(live) > 1:
                buttons[focus].set_focused(False, self._disp)
                at = live.index(focus)
                at = (at + (1 if key == "tab" else -1)) % len(live)
                focus = live[at]
                buttons[focus].set_focused(True, self._disp)
            elif key == "enter" and buttons[focus].enabled:
                buttons[focus].on_press()

            if self._touch is not None and self._touch.is_touched:
                pos = self._touch.position()
                if pos:
                    px, py = pos
                    for b in buttons:
                        if b.handle_touch(px, py, self._disp):
                            break

            time.sleep(_POLL_S)

        return picked[0]


# ─────────────────────────────────────────────────────────────────────────────
# Self-test — no hardware required.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL,
                        format="%(levelname)-8s [%(name)s] %(message)s")

    class _MockDisplay:
        """Records every string drawn, so the tests can assert on the view."""
        def __init__(self):
            self.texts = []          # (text, rect, colour)
        def fill_screen(self, c): pass
        def fill_rect(self, *a, **k): pass
        def draw_rect(self, *a, **k): pass
        def draw_text(self, text, rect, **k):
            self.texts.append((text, rect, k.get("color")))
        def shown(self):
            return [t for t, _, _ in self.texts]
        def colors(self):
            return [c for _, _, c in self.texts]

    class _MockKb:
        def __init__(self, keys=()): self._keys = list(keys)
        def read_one(self): return self._keys.pop(0) if self._keys else None

    class _MockTouch:
        def __init__(self, points=()):
            self._points = list(points)
        @property
        def is_touched(self): return bool(self._points)
        def position(self): return self._points.pop(0) if self._points else None

    def check(label, got, want):
        ok = "OK " if got == want else "XX "
        print(f"  {ok}{label:46s} got={got!r}")
        assert got == want, f"{label}: {got!r} != {want!r}"

    def new(disp=None, keys=(), points=()):
        s = Splash(disp or _MockDisplay(), version="0.4.0")
        s.attach_input(touch=_MockTouch(points), kb=_MockKb(keys))
        s.open()
        return s

    print("== the chrome names the org and version ==")
    d = _MockDisplay()
    Splash(d, version="0.4.0").open()
    check("title",    d.shown()[0], "Colorimeter")
    check("subtitle", d.shown()[1], "MGWWG v0.4.0")

    print("== step() appends, returns its index, and starts running ==")
    s = new()
    check("first index",  s.step("Touch"),    0)
    check("second index", s.step("Keyboard"), 1)
    check("state",        s._steps[0].state,  RUNNING)
    check("label",        s._steps[1].label,  "Keyboard")

    print("== done/warn/fail set the state, and default to the last step ==")
    s = new()
    i = s.step("Touch")
    s.done(i)
    check("quick step reads ok", s._steps[0].state, OK)
    s.step("Keyboard"); s.warn()
    check("warn on last", s._steps[1].state, WARN)
    s.step("Temperature"); s.fail()
    check("fail on last", s._steps[2].state, FAIL)
    check("fail is red",  _TONE[FAIL],       "edited_text")

    print("== a slow step reads as its duration ==")
    s = new()
    i = s.step("Temperature")
    s._steps[i].t0 -= 1.4
    s.done(i)
    check("duration", s._steps[0].state, "1.4s")

    print("== the window follows the newest step ==")
    s = new()
    for n in range(MAX_LINES):
        s.step(f"step{n}")
    check("no scroll while it fits", s._scroll, 0)
    s.step("step7"); s.step("step8")
    check("scrolled by two",  s._scroll, 2)
    check("top visible step", s._steps[s._scroll].label, "step2")
    check("row of the last",  len(s._steps) - 1 - s._scroll, MAX_LINES - 1)

    print("== running() times the body and re-raises on failure ==")
    s = new()
    with s.running("Screens"):
        pass
    check("marked done", s._steps[0].state, OK)
    try:
        with s.running("Touch"):
            raise RuntimeError("no I2C")
    except RuntimeError as e:
        check("re-raised", str(e), "no I2C")
    check("marked failed", s._steps[1].state, FAIL)

    print("== BootLog records durations, and replay() shows them done ==")
    boot = BootLog()
    with boot.step("Database"):
        pass
    check("recorded", boot.steps[0].label, "Database")
    try:
        with boot.step("Display"):
            raise RuntimeError("no SPI")
    except RuntimeError:
        pass
    check("failure recorded", boot.steps[1].state, FAIL)
    s = new()
    s.replay(boot)
    check("replayed count",  len(s._steps),      2)
    check("replayed state",  s._steps[0].state,  OK)
    check("replayed keeps FAIL", s._steps[1].state, FAIL)

    print("== notice() wraps its message and waits for OK ==")
    d = _MockDisplay()
    s = new(d, keys=["enter"])
    s.step("Configuration")
    s.notice("Loading protocol.reagent1_secs = 120 (rowid 27)")
    wrapped = [t for t, _, _ in d.texts if t.startswith("Loading")][0]
    check("wrapped to the panel",
          max(len(line) for line in wrapped.split("\n")) <= _NOTE_CHARS, True)
    # The dialog covered the list; the last thing drawn must be the list back
    # again, not the dialog's own OK button.
    check("step list repainted after", d.shown()[-2:], ["Configuration", RUNNING])

    print("== notice() with no input self-dismisses instead of hanging ==")
    s = Splash(_MockDisplay(), version="0.4.0")
    s.open()
    t0 = time.monotonic()
    s.notice("Loading capture.directory = captures (rowid 3)")
    check("returned without input", time.monotonic() - t0 < _DEAF_PAUSE_S + 1, True)

    print("== finish() returns the focused choice on enter ==")
    check("start is the default", new(keys=["enter"]).finish("Ready"), "start")
    check("tab moves to MENU",
          new(keys=["tab", "enter"]).finish("Ready"), "menu")

    print("== finish(ok=False) greys START and focuses MENU ==")
    s = new(keys=["enter"])
    check("choice", s.finish("No active layer models", ok=False), "menu")
    check("START disabled", s._start_btn.enabled, False)
    check("status is red",  s._status_tone,       "edited_text")

    print("== a greyed START cannot be pressed, by touch or by tab ==")
    s = new(keys=["tab", "tab", "enter"],
            points=[(START_BTN.x + 40, START_BTN.y + 18)])
    check("still menu", s.finish("broken", ok=False), "menu")

    print("== START is touchable once armed ==")
    s = new(points=[(START_BTN.x + 40, START_BTN.y + 18)])
    check("touch picks start", s.finish("Ready"), "start")

    print("== buttons are inert before finish() ==")
    s = new(keys=["enter"], points=[(START_BTN.x + 40, START_BTN.y + 18)])
    s.notice("Loading capture.extension = .h5 (rowid 9)")
    check("notice not stolen by START", s._armed, False)

    print("== finish() honours a timeout ==")
    s = new()
    check("timed out to start", s.finish("Ready", timeout_s=0.05), "start")

    print("== problems() replaces the step list ==")
    d = _MockDisplay()
    s = new(d)
    s.step("Database"); s.done()
    s.step("Configuration"); s.warn()
    d.texts.clear()
    s.problems([("fail", "Database: missing table roi"),
                ("warn", "Temperature probe: no device detected")])
    shown = " ".join(d.shown())
    check("the failure is shown",   "missing table roi" in shown, True)
    check("the warning too",        "no device detected" in shown, True)
    check("the steps are gone",     "Configuration" in shown, False)
    check("failure drawn in red",   d.colors()[0], color("edited_text"))
    check("warning drawn in amber", d.colors()[-1], color("step_running"))

    print("== and survives a repaint ==")
    d.texts.clear()
    s.repaint()
    check("problems redrawn", "missing table roi" in " ".join(d.shown()), True)
    check("not the steps",    "Configuration" in " ".join(d.shown()), False)

    print("== nothing to say leaves the region blank ==")
    d = _MockDisplay()
    s = new(d)
    s.step("Database"); s.done()
    d.texts.clear()
    s.problems([])
    check("no lines drawn", d.shown(), [])

    print("== a long list is truncated, not overflowed ==")
    many = [("warn", f"Check {i}: something the operator should know about "
                     f"that runs past one line of a 320 pixel panel")
            for i in range(9)]
    lines = _problem_lines(many)
    check("fits the region", len(lines) <= PROBLEM_LINES, True)
    check("says how many were dropped", "more" in lines[-1][1], True)
    check("a failure is never the one dropped",
          _problem_lines([("fail", "first")] + many)[0][1], "first")

    print("== a broken display still yields a choice, never raises ==")
    class _BrokenDisplay:
        def fill_screen(self, c): raise RuntimeError("SPI is on fire")
        def fill_rect(self, *a, **k): raise RuntimeError("SPI is on fire")
        def draw_rect(self, *a, **k): raise RuntimeError("SPI is on fire")
        def draw_text(self, *a, **k): raise RuntimeError("SPI is on fire")
    s = Splash(_BrokenDisplay(), version="0.4.0")
    s.open()
    check("step survives",   s.step("Touch"),  -1)
    s.done(); s.notice("Loading x.y = 1 (rowid 2)")
    check("finish survives", s.finish("Ready"), "start")

    print("\n== all splash_screen self-tests passed ==")
