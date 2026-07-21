"""
shutdown_screen.py  —  The interactive shutdown / relaunch / power-off screen.
-------------------------------------------------------------------------------
Shown by Arbiter.shutdown() while the display, touch, and keyboard are still
live — before cleanup() tears any of that down.  Offers three choices,
Re-launch / Exit / Power off, and defaults to Exit if the operator doesn't
pick one within `timeout_s`.

Deliberately independent of pool.py / layout.py: those describe the phase/
slot screen, which may not even exist yet (a crash during Arbiter._build_model
never built one).  This is a one-off overlay with its own tiny widget set, so
it can render on nothing more than a working display driver.

Usage (see Arbiter.shutdown):
    choice = shutdown_screen.run(disp, touch, kb, error=exc,
                                 simulation=_SIMULATION_MODE)
    # choice is one of "relaunch", "exit", "poweroff"
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from geometry import Rect
from palette import color
from widgets import Button, Label

log = logging.getLogger(__name__)

_POLL_S = 0.02
_DEFAULT_TIMEOUT_S = 15.0

# ── Layout (independent of layout.py — this screen has its own geometry) ──────
TITLE     = Rect( 10,  18, 300, 40)
DETAIL    = Rect( 10,  62, 300, 40)
COUNTDOWN = Rect( 10, 110, 300, 28)

ABORT_BTN    = Rect( 12, 180, 70, 44)
RELAUNCH_BTN = Rect( 90, 180, 70, 44)
EXIT_BTN     = Rect(168, 180, 70, 44)
POWEROFF_BTN = Rect(246, 180, 70, 44)

_BG = "body_dark"


def run(disp, touch, kb, *, error: Optional[BaseException] = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S, simulation: bool = False) -> str:
    """
    Draw the shutdown screen and block, pumping input, until the operator
    picks a choice or timeout_s elapses.  Returns "abort" | "relaunch" |
    "exit" | "poweroff" — "exit" is the default on timeout.

    "abort" means return to the running program without releasing hardware;
    all other choices proceed through cleanup() and exit.

    Never raises: a shutdown screen must not be the thing that prevents
    shutdown, so any failure here just falls back to "exit".
    """
    try:
        return _run(disp, touch, kb, error=error, timeout_s=timeout_s,
                    simulation=simulation)
    except Exception:
        log.exception("shutdown_screen: failed, defaulting to exit")
        return "exit"


def _run(disp, touch, kb, *, error, timeout_s, simulation) -> str:
    title  = "Error" if error else "Shutting down"
    detail = _truncate(str(error)) if error else ""

    disp.fill_screen(color(_BG))

    Label(TITLE, text=title, color=color("label_coral" if error else "body_text"),
         bg=color(_BG), size=26, align="center", valign="middle").draw(disp)

    if detail:
        Label(DETAIL, text=detail, color=color("body_text"), bg=color(_BG),
             size=13, align="center", valign="middle").draw(disp)

    countdown_lbl = Label(COUNTDOWN, color=color("body_text"), bg=color(_BG),
                          size=15, align="center", valign="middle")

    picked: list = []

    def _choose(name: str) -> None:
        picked.append(name)

    buttons = [
        Button(ABORT_BTN, "Abort", color=color("btn_gray"),
              text_color=color("btn_text"), size=12, name="abort",
              on_press=lambda: _choose("abort")),
        Button(RELAUNCH_BTN, "Re-launch", color=color("start_green"),
              text_color=color("btn_text"), size=12, name="relaunch",
              on_press=lambda: _choose("relaunch")),
        Button(EXIT_BTN, "Exit", color=color("btn_gray"),
              text_color=color("btn_text"), size=12, name="exit",
              on_press=lambda: _choose("exit")),
        Button(POWEROFF_BTN, "Power off", color=color("label_coral"),
              text_color=color("btn_text"), size=12, name="poweroff",
              on_press=lambda: _choose("poweroff")),
    ]
    for b in buttons:
        b.draw(disp)

    focus_idx = 2                       # default focus: Exit — matches the timeout outcome
    buttons[focus_idx].set_focused(True, disp)

    deadline   = time.monotonic() + timeout_s
    last_shown = None
    while not picked:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.info("shutdown_screen: timed out, defaulting to exit")
            return "exit"

        if simulation and hasattr(disp, "process_events"):
            disp.process_events()

        shown = int(remaining) + 1
        if shown != last_shown:
            last_shown = shown
            countdown_lbl.text = f"Exiting in {shown}s..."
            countdown_lbl.draw(disp)

        key = kb.read_one() if kb else None
        if key in ("tab", "shift-tab"):
            buttons[focus_idx].set_focused(False, disp)
            focus_idx = (focus_idx + (1 if key == "tab" else -1)) % len(buttons)
            buttons[focus_idx].set_focused(True, disp)
        elif key == "enter":
            buttons[focus_idx].on_press()

        if touch and touch.is_touched:
            pos = touch.position()
            if pos:
                px, py = pos
                for b in buttons:
                    if b.handle_touch(px, py, disp):
                        break

        time.sleep(_POLL_S)

    log.info("shutdown_screen: chose %r", picked[0])
    return picked[0]


def _truncate(text: str, limit: int = 70) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ─────────────────────────────────────────────────────────────────────────────
# Self-test — no hardware required.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)-8s [%(name)s] %(message)s")

    class _MockDisplay:
        def fill_screen(self, c): pass
        def fill_rect(self, *a, **k): pass
        def draw_rect(self, *a, **k): pass
        def draw_text(self, *a, **k): pass

    class _MockKb:
        def __init__(self, keys): self._keys = list(keys)
        def read_one(self): return self._keys.pop(0) if self._keys else None

    class _MockTouch:
        is_touched = False
        def position(self): return None

    def check(label, got, want):
        ok = "OK " if got == want else "XX "
        print(f"  {ok}{label:40s} got={got!r}")
        assert got == want, f"{label}: {got!r} != {want!r}"

    print("== Enter immediately picks the default-focused button (Exit) ==")
    choice = run(_MockDisplay(), _MockTouch(), _MockKb(["enter"]), timeout_s=5)
    check("choice", choice, "exit")

    print("== Shift-Tab moves focus to Abort, Enter picks it ==")
    choice = run(_MockDisplay(), _MockTouch(), _MockKb(["shift-tab", "shift-tab", "enter"]), timeout_s=5)
    check("choice", choice, "abort")

    print("== Tab from Exit goes to Power off ==")
    choice = run(_MockDisplay(), _MockTouch(), _MockKb(["tab", "enter"]), timeout_s=5)
    check("choice", choice, "poweroff")

    print("== Tab wraps from Power off back to Abort ==")
    choice = run(_MockDisplay(), _MockTouch(), _MockKb(["tab", "tab", "enter"]), timeout_s=5)
    check("choice", choice, "abort")

    print("== No input -> timeout defaults to exit ==")
    choice = run(_MockDisplay(), _MockTouch(), _MockKb([]), timeout_s=0.1)
    check("choice", choice, "exit")

    print("== A drawing failure still returns a choice, never raises ==")
    class _BrokenDisplay:
        def fill_screen(self, c): raise RuntimeError("SPI is on fire")
    choice = run(_BrokenDisplay(), _MockTouch(), _MockKb([]), timeout_s=5)
    check("choice", choice, "exit")

    print("\n== all shutdown_screen self-tests passed ==")
