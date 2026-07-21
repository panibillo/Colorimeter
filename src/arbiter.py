"""
arbiter.py  —  The one object above the two slots.
---------------------------------------------------
The Arbiter owns everything shared and single-instance: the display (one
screen, one focus), the camera + LED bank (one capture at a time), the buzzer,
the keyboard, and the main event loop.  Slots and phases never touch these —
they call the arbiter (the contract documented at the top of phases.py) and it
grants, defers, or denies.

Because the widget Pool is the single physical screen, `enter()` may run only
for the *focused* slot; a backgrounded slot advances its model (activate() and
timers mutate slot state only) but is never drawn.  This is why render() is a
no-op for an unfocused slot.

What the arbiter arbitrates
---------------------------
- **Focus.** One slot is displayed; the other stays live. The slot toggle
  switches which is drawn. Nothing auto-switches focus.
- **The camera.** At most one capture across both slots. A capture starts only
  from a focused CAPTURE press, then runs capture→analysis to completion while
  the user may toggle away; every CAPTURE button is disabled while a capture is
  live (ReadPhase asks `capture_available()`).
- **The buzzer.** A countdown reaching zero alarms — alarm only, never a focus
  switch — and the phase advances its own model via `on_expire`.

Run it:
    COLORIMETER_SIMULATION=1 python3 src/arbiter.py        # mock hardware
    python3 src/arbiter.py                                 # real Pi hardware

Headless logic self-test (no display, fake camera):
    PYTHONPATH=src python3 src/arbiter.py --selftest
"""

from __future__ import annotations

import datetime
import logging
import os
import signal
import sys
import time

_SIMULATION_MODE = os.environ.get("COLORIMETER_SIMULATION", "0") == "1"

# Hardware drivers are imported lazily in _init_hardware() so this module (and
# its logic self-test) imports cleanly on a machine without the display/GPIO
# stacks.  Only the pure model + widget layer is imported at module load.
import logging_manager
import widgets
from widgets import Button, TextBox
from pool import Pool
from slot import Slot
from phases import build_phases, EntryPhase

log = logging.getLogger(__name__)

_BL_PIN            = 13
_POLL_S            = 0.02        # main-loop cadence
_CAPTURE_LED_START = 1
_CAPTURE_LED_END   = 10
_ANALYSIS_S        = 1.5         # placeholder analysis duration (engine TODO)


class Arbiter:
    """One instance per run.  Call start() to init hardware and enter the loop."""

    def __init__(self, capture_factory=None) -> None:
        # capture_factory: a zero-arg callable returning a capture session (for
        # tests). None -> the real CaptureSession, imported lazily on first use.
        # Hardware (drivers loaded in _init_hardware; None until then)
        self._backlight = None
        self._disp      = None
        self._touch     = None
        self._kb        = None
        self._beeper    = None
        self._ILI9341   = None

        # Model
        self._pool:   Pool | None = None
        self._phases: dict        = {}
        self._slots:  dict[str, Slot] = {}
        self._focused: Slot | None = None

        # Focus within the current screen
        self._focusables: list = []
        self._focus_idx:  int  = -1

        # The single capture in flight, plus which slot owns it.
        self._capture_factory = capture_factory
        self._capture: CaptureSession | None = None
        self._capture_slot: Slot | None = None

        # Placeholder analysis timer: (slot, started_at) or None.
        self._analysis: tuple | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._init_logging()
        if _SIMULATION_MODE:
            log.info("SIMULATION MODE: mock hardware")
        self._init_hardware()
        self._build_model()
        self.focus(self._slots["A"])
        self._run()

    @staticmethod
    def _init_logging() -> None:
        name = (os.environ.get("COLORIMETER_LOGLEVEL") or "INFO").upper()
        level = getattr(logging, name, logging.INFO)
        logging.basicConfig(level=level,
                            format="%(levelname)-8s [%(name)s] %(message)s")
        logging_manager.set_level(name if isinstance(level, int) else "INFO")

    def _init_hardware(self) -> None:
        if _SIMULATION_MODE:
            from mock.gpiozero_mock import DigitalOutputDevice
            from mock.ili9341_mock import ILI9341
            from mock.tsc2007_mock import TSC2007
            from mock import beeper_mock as beeper
            from mock.kb_reader_mock import KbReader
        else:
            from gpiozero import DigitalOutputDevice
            from ili9341 import ILI9341
            from tsc2007 import TSC2007
            import beeper
            from kb_reader import KbReader

        self._beeper    = beeper
        self._ILI9341   = ILI9341
        self._kb        = KbReader()
        self._backlight = DigitalOutputDevice(_BL_PIN, initial_value=True)
        self._disp      = ILI9341()
        self._touch     = TSC2007()

        def _shutdown(signum=None, frame=None):
            self.cleanup()
            sys.exit(0)

        signal.signal(signal.SIGINT,  _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        self._disp.init()
        self._touch.init()
        self._kb.open()

        if _SIMULATION_MODE and hasattr(self._disp, "register_key_handler"):
            self._disp.register_key_handler(self._kb._on_key_event)          # type: ignore
            self._disp.register_mouse_handler(self._touch._on_touch)         # type: ignore
            self._disp.register_touch_release_handler(self._touch._on_release)  # type: ignore

        widgets.set_button_sound(beeper.click)

    def _build_model(self) -> None:
        self._pool = Pool.build()
        # Shorten the reagent countdowns in simulation so runs are quick to test.
        if _SIMULATION_MODE:
            self._phases = build_phases(reagent1_secs=20, reagent2_secs=20)
        else:
            self._phases = build_phases()
        self._slots = {"A": Slot("A"), "B": Slot("B")}

    def cleanup(self) -> None:
        log.info("Arbiter: cleanup")
        if self._capture is not None:
            self._capture.cancel()
            self._capture = None
        try:
            if self._disp and self._ILI9341:
                self._disp.fill_screen(self._ILI9341.BLACK)
        except Exception:
            pass
        if self._kb:
            self._kb.cleanup()
        if self._beeper:
            self._beeper.cleanup()
        if self._touch:
            self._touch.cleanup()
        if self._disp:
            self._disp.cleanup()
        if self._backlight:
            self._backlight.close()

    # ── Phase contract: clock / stamps ─────────────────────────────────────────

    def now(self) -> float:
        return time.monotonic()

    def timestamp(self) -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Phase contract: navigation / render ────────────────────────────────────

    def goto(self, slot: Slot, phase_id: str) -> None:
        """
        Advance a slot to a phase (the real protocol flow): this becomes the
        slot's live phase.  Runs its once-only activate, then draws if focused.
        """
        slot.phase = slot.active_phase = phase_id
        self._phases[phase_id].activate(self, slot)
        self.render(slot)

    def view(self, slot: Slot, phase_id: str) -> None:
        """
        Preview a phase without advancing the test (PRIOR/NEXT browsing).  Only
        the viewed phase changes — the live phase, its timer, and any capture
        keep running untouched.  No activate(), so nothing is started or reset.
        """
        slot.phase = phase_id
        self.render(slot)

    def resume(self, slot: Slot) -> None:
        """Return the view to the slot's live phase (the header Resume button)."""
        self.view(slot, slot.active_phase)

    def render(self, slot: Slot) -> None:
        """
        Draw the slot's current phase.  A no-op for an unfocused slot: enter()
        writes into the single shared pool, so only the focused slot may run it.
        """
        if slot is not self._focused or self._pool is None:
            return
        self._pool.hide_all()
        self._phases[slot.phase].enter(self._pool, slot, self)
        self._paint_slot_toggle()
        self._pool.render(self._disp)
        self._rebuild_focus()

    def focus(self, slot: Slot) -> None:
        """Make a slot the displayed one and render it (user-initiated only)."""
        self._focused = slot
        self.render(slot)

    def toggle_slot(self) -> None:
        """Switch the display to the other slot; both stay live regardless."""
        other = self._slots["B"] if self._focused is self._slots["A"] else self._slots["A"]
        log.info("Arbiter: focus %s -> %s", self._focused.name, other.name)
        self.focus(other)

    def _paint_slot_toggle(self) -> None:
        """Label the slot button with the slot you would switch TO."""
        target = "B" if self._focused.name == "A" else "A"
        from palette import color
        btn = self._pool["slot_btn"]
        btn.label = target
        btn.color = color("slot_b_bg" if target == "B" else "slot_a_bg")
        btn.on_press = self.toggle_slot

    # ── Phase contract: modals ─────────────────────────────────────────────────

    def open_modal(self, slot: Slot, modal_id: str) -> None:
        slot.modal_return = slot.phase
        slot.phase = modal_id
        self.render(slot)

    def close_modal(self, slot: Slot) -> None:
        slot.phase, slot.modal_return = (slot.modal_return or "entry"), None
        self.render(slot)

    # ── Phase contract: capture / analysis ─────────────────────────────────────

    def capture_available(self) -> bool:
        return self._capture is None

    def request_capture(self, slot: Slot) -> bool:
        """Start a capture for slot if the camera is free.  Returns granted."""
        if self._capture is not None:
            log.info("Arbiter: capture denied — camera busy (slot %s)",
                     self._capture_slot.name if self._capture_slot else "?")
            return False
        session = self._new_session()
        if not session.start():
            log.error("Arbiter: capture failed to start: %s", session.error)
            slot.progress = "Camera failed"
            return False
        self._capture = session
        self._capture_slot = slot
        slot.progress = "Starting camera..."
        log.info("Arbiter: capture started for slot %s", slot.name)
        return True

    def _new_session(self):
        """Build a capture session (test factory if given, else the real one)."""
        if self._capture_factory is not None:
            return self._capture_factory()
        from capture_runner import CaptureConfig
        from capture_session import CaptureSession
        return CaptureSession(CaptureConfig(led_start=_CAPTURE_LED_START,
                                            led_end=_CAPTURE_LED_END))

    def start_analysis(self, slot: Slot) -> None:
        # Analysis engine is not implemented yet; a placeholder completes after
        # _ANALYSIS_S so the whole flow is exercisable end to end.
        log.info("Arbiter: analysis started for slot %s (placeholder)", slot.name)
        self._analysis = (slot, self.now())

    def post_results(self, slot: Slot) -> None:
        log.info("Arbiter: POST results for slot %s (upload not implemented)", slot.name)
        slot.progress = "Posted (stub)"
        self.render(slot)

    def next_sample(self, slot: Slot) -> None:
        log.info("Arbiter: next sample in slot %s", slot.name)
        slot.clear_run()
        self.goto(slot, "entry")

    def cancel_test(self, slot: Slot) -> None:
        log.info("Arbiter: cancel test in slot %s", slot.name)
        if self._capture_slot is slot and self._capture is not None:
            self._capture.cancel()
            self._capture = None
            self._capture_slot = None
        slot.clear_run()
        self.goto(slot, "entry")

    def reset_timer(self, slot: Slot) -> None:
        slot.timer = None
        slot.running = False
        self.close_modal(slot)

    def retry_capture(self, slot: Slot) -> None:
        # Return to the read phase; the operator presses CAPTURE again.
        self.close_modal(slot)

    # ── Alarm ──────────────────────────────────────────────────────────────────

    def alarm(self, slot: Slot) -> None:
        log.info("Arbiter: ALARM for slot %s", slot.name)
        if self._beeper is None:
            return
        fn = getattr(self._beeper, "alarm_on", None) or getattr(self._beeper, "beep", None)
        if callable(fn):
            fn()

    # ── Poll / pump (driven each loop pass) ────────────────────────────────────

    def poll_timers(self) -> None:
        """Expire check for both slots — arithmetic only. Alarm; never switch focus."""
        now = self.now()
        for slot in self._slots.values():
            t = slot.timer
            if t is not None and t.expired(now):
                slot.timer = None
                self.alarm(slot)
                # The timer belongs to the live phase, not whatever is being
                # previewed, so expiry is always handled by active_phase.
                self._phases[slot.active_phase].on_expire(self, slot)

    def pump_capture(self) -> None:
        """Advance the in-flight capture one step; reflect progress on the owner slot."""
        session = self._capture
        if session is None or not session.active:
            return
        owner = self._capture_slot
        progress = session.step()

        if progress is not None:
            detail = progress.color or f"{progress.fraction * 100:.0f}%"
            if progress.led_total:
                detail = f"{progress.led_index}/{progress.led_total} {detail}"
            owner.progress = detail
            if owner is self._focused and owner.phase == "read":
                self._pool.set("instr1", owner.progress)
                self._pool.redraw(self._disp, "instr1")
            return

        # step() returned None: the run ended.
        self._capture = None
        self._capture_slot = None
        if session.error is not None:
            owner.progress = f"Capture failed: {str(session.error)[:30]}"
            self.render(owner)
            return
        if owner.timer is not None:
            owner.timer.stop(self.now())     # freeze the final elapsed time
        owner.capturing = False
        self.goto(owner, "analysis")
        # A capture just freed the camera; re-enable a waiting CAPTURE button
        # on the other slot if it is the one being viewed.
        if self._focused is not owner and self._focused.phase == "read":
            self.render(self._focused)

    def pump_analysis(self) -> None:
        """Placeholder: complete the analysis after a short delay."""
        if self._analysis is None:
            return
        slot, started = self._analysis
        if self.now() - started < _ANALYSIS_S:
            return
        self._analysis = None
        slot.result = "Analysis not yet implemented"
        log.info("Arbiter: analysis done for slot %s", slot.name)
        self.render(slot)

    # ── Focus / keyboard (over the pool) ───────────────────────────────────────

    def _rebuild_focus(self) -> None:
        self._focusables = self._pool.focusable_widgets()
        self._focus_idx  = -1
        if self._focusables:
            self._move_focus(0)

    def _focused_widget(self):
        if 0 <= self._focus_idx < len(self._focusables):
            return self._focusables[self._focus_idx]
        return None

    def _move_focus(self, new_idx: int) -> None:
        if not self._focusables:
            return
        prev = self._focused_widget()
        if prev is not None:
            prev.set_focused(False, self._disp)
        self._focus_idx = new_idx % len(self._focusables)
        self._focusables[self._focus_idx].set_focused(True, self._disp)

    def on_key(self, key: str) -> None:
        if not self._focusables:
            return
        if key in ("tab", "shift-tab"):
            cw = self._focused_widget()
            was_editing = isinstance(cw, TextBox) and cw.editing
            self._move_focus(self._focus_idx + (1 if key == "tab" else -1))
            nw = self._focused_widget()
            if was_editing and isinstance(nw, TextBox) and nw.editable:
                nw.editing = True
                nw.invalidate()
                nw.draw(self._disp, force=True)
            return

        w = self._focused_widget()
        if key == "enter":
            if w is None or not w.enabled:
                return
            if isinstance(w, TextBox) and w.editable:
                w.toggle_editing(self._disp)
            elif isinstance(w, Button) and callable(w.on_press):
                w.on_press()
            return

        if isinstance(w, TextBox) and w.editing and w.enabled:
            if key in ("backspace", "delete") or (len(key) == 1 and key.isprintable()):
                w.handle_char(key, self._disp)
                self._writethrough(w)

    def on_touch(self, px: int, py: int) -> None:
        if self._pool is None:
            return
        if self._pool.handle_touch(px, py, self._disp):
            # A touch may have started editing a TextBox — sync keyboard focus.
            for i, w in enumerate(self._focusables):
                if isinstance(w, TextBox) and w.editing and i != self._focus_idx:
                    self._move_focus(i)
                    break

    def _writethrough(self, textbox) -> None:
        """
        Entry-form edits are the source of truth: copy the edited field to the
        focused slot's record, and re-enable Begin the moment id+user are set.
        """
        slot = self._focused
        if slot is None or slot.phase != "entry":
            return
        key = EntryPhase.FIELDS.get(textbox.name)
        if key is None:
            return
        slot.record[key] = textbox.value
        begin = self._pool["main_btn"]
        want = slot.ready_to_begin()
        if begin.enabled != want:
            begin.enabled = want
            begin.draw(self._disp)

    # ── Main loop ──────────────────────────────────────────────────────────────

    def _run(self) -> None:
        log.info("Arbiter: running — Ctrl-C to exit")
        last_clock = 0.0
        while True:
            now = time.monotonic()

            if _SIMULATION_MODE and hasattr(self._disp, "process_events"):
                self._disp.process_events()   # type: ignore

            self.poll_timers()
            self.pump_capture()
            self.pump_analysis()

            if now - last_clock >= 1.0:
                last_clock = now
                self._tick_1hz()

            key = self._kb.read_one()
            if key:
                self.on_key(key)

            if self._touch.is_touched:            # type: ignore
                pos = self._touch.position()      # type: ignore
                if pos:
                    self.on_touch(*pos)
                timeout = time.monotonic() + 1.0
                while self._touch.is_touched and time.monotonic() < timeout:  # type: ignore
                    if _SIMULATION_MODE and hasattr(self._disp, "process_events"):
                        self._disp.process_events()  # type: ignore
                    time.sleep(_POLL_S)
                if time.monotonic() >= timeout:
                    self._touch._is_touched = False  # type: ignore

            time.sleep(_POLL_S)

    def _tick_1hz(self) -> None:
        """Once-a-second repaint of the clock and, if running, the focused timer."""
        if self._pool is None or self._focused is None:
            return
        self._pool.set("clock", datetime.datetime.now().strftime("%H:%M:%S"))
        self._pool.redraw(self._disp, "clock")
        tw = self._pool.get("timer")
        if tw is not None and tw.visible and self._focused.running:
            self._pool.redraw(self._disp, "timer")


# ─────────────────────────────────────────────────────────────────────────────
# Headless logic self-test — no display, fake camera.
# ─────────────────────────────────────────────────────────────────────────────

def _selftest() -> None:
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)-8s [%(name)s] %(message)s")

    class _MockDisplay:
        def fill_screen(self, c): pass
        def fill_rect(self, *a, **k): pass
        def draw_rect(self, *a, **k): pass
        def draw_text(self, *a, **k): pass

    class _FakeProgress:
        def __init__(self, i, n):
            self.color = "blue"; self.fraction = i / n
            self.led_index = i; self.led_total = n; self.transient = False

    class _FakeCapture:
        """Completes after `steps` step() calls."""
        def __init__(self, config=None, steps=3):
            self.active = False; self.error = None; self.out_path = "fake.h5"
            self._i = 0; self._n = steps
        def start(self): self.active = True; return True
        def step(self):
            if self._i >= self._n:
                self.active = False; return None
            self._i += 1
            return _FakeProgress(self._i, self._n)
        def cancel(self): self.active = False

    def check(label, got, want):
        ok = "OK " if got == want else "XX "
        print(f"  {ok}{label:44s} got={got!r}")
        assert got == want, f"{label}: {got!r} != {want!r}"

    arb = Arbiter(capture_factory=_FakeCapture)
    arb._disp = _MockDisplay()
    arb._build_model()
    A, B = arb._slots["A"], arb._slots["B"]
    arb.focus(A)

    print("== render draws only the focused slot ==")
    arb.goto(A, "entry")
    check("A is focused",        arb._focused is A, True)
    # Advance B in the background into a running reagent; the shared pool must
    # keep showing A's entry phase (its title), not B's.
    arb.focus(A)
    B.record["id"] = "BX9"; B.phase = B.active_phase = "reagent1"
    B.running = True
    from timer import TimerState
    B.timer = TimerState.down(120, arb.now())
    arb.render(B)                                   # background: must be a no-op
    check("pool still shows A entry title", arb._pool["title"].text, "New Test")

    print("== background countdown: alarm only, no focus switch ==")
    B.timer = TimerState.down(1, arb.now() - 5)     # already past its deadline
    focused_before = arb._focused
    arb.poll_timers()
    check("B advanced to reagent2", B.phase, "reagent2")
    check("focus unchanged",     arb._focused is focused_before, True)

    print("== single-camera guard across slots ==")
    A.record["id"] = "AX1"; A.record["user"] = "u"
    arb.goto(A, "read")
    check("A read: CAPTURE enabled", arb._pool["run_btn"].enabled, True)
    ok = arb.request_capture(A)
    check("A capture granted",   ok, True)
    check("capture_available now False", arb.capture_available(), False)
    # With A capturing, view B in read: its CAPTURE must be disabled.
    B.record["id"] = "BX9"; B.record["user"] = "u"
    arb.focus(B)
    arb.goto(B, "read")
    check("B read: CAPTURE disabled (camera busy)", arb._pool["run_btn"].enabled, False)
    denied = arb.request_capture(B)
    check("B capture denied",    denied, False)

    print("== capture completes -> analysis -> result ==")
    arb.focus(A)
    A.capturing = True
    for _ in range(5):
        arb.pump_capture()
    check("A moved to analysis",  A.phase, "analysis")
    check("camera freed",         arb.capture_available(), True)
    arb._analysis = (A, arb.now() - _ANALYSIS_S - 1)   # force it done
    arb.pump_analysis()
    check("result set",           bool(A.result), True)
    check("POST enabled",         arb._pool["run_btn"].enabled, True)

    print("\n== all arbiter self-tests passed ==")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        Arbiter().start()
