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
import subprocess
import sys
import time

_SIMULATION_MODE = os.environ.get("COLORIMETER_SIMULATION", "0") == "1"

# Hardware drivers are imported lazily in _init_hardware() so this module (and
# its logic self-test) imports cleanly on a machine without the display/GPIO
# stacks.  Only the pure model + widget layer is imported at module load.
import sqlite3

import logging_manager
import widgets
import shutdown_screen
import sample_repo
from widgets import Button, TextBox
from pool import Pool
from slot import Slot
from phases import build_phases, EntryPhase, Entry2Phase

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
        self._temp      = None
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

        # Shutdown state: guards against a second signal re-entering shutdown()
        # or cleanup() running its hardware-release chain twice.
        self._shutting_down = False
        self._cleaned_up    = False

        # Reference data: which default_gui_vals row seeds the entry screens, and
        # the resolved Defaults loaded from it (rowid 1 for now; later config).
        self._default_rowid = 1
        self._defaults: sample_repo.Defaults | None = None

        # Menu screen state: the slot to return to, and a staged (not yet
        # committed) default_rowid choice — see open_menu()/_menu_ok().
        self._pre_menu_slot: Slot | None = None
        self._menu_staged_rowid: int | None = None

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
            from mock.DS18B20_mock import DS18B20
        else:
            from gpiozero import DigitalOutputDevice
            from ili9341 import ILI9341
            from tsc2007 import TSC2007
            import beeper
            from kb_reader import KbReader
            from DS18B20 import DS18B20

        self._beeper    = beeper
        self._ILI9341   = ILI9341
        self._kb        = KbReader()
        self._backlight = DigitalOutputDevice(_BL_PIN, initial_value=True)
        self._disp      = ILI9341()
        self._touch     = TSC2007()
        self._temp      = DS18B20()

        def _shutdown(signum=None, frame=None):
            self.shutdown()

        signal.signal(signal.SIGINT,  _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        self._disp.init()
        self._touch.init()
        self._kb.open()
        self._temp.init()

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
        self._load_defaults()

    def _load_defaults(self) -> None:
        """Load the GUI default values (entry pre-fills) from the DB."""
        try:
            self._defaults = sample_repo.load_defaults(self._default_rowid)
        except sqlite3.Error:
            log.exception("Arbiter: could not load defaults (rowid=%s)",
                          self._default_rowid)
            self._defaults = None
        if self._defaults is None:
            log.warning("Arbiter: no defaults loaded — entry fields start blank")

    def cleanup(self) -> None:
        """
        Release every hardware resource.  Idempotent (safe to call more than
        once), and each release is wrapped independently so one device
        failing to close can't strand the rest still holding their pins/fds.
        """
        if self._cleaned_up:
            return
        self._cleaned_up = True
        log.info("Arbiter: cleanup")

        if self._capture is not None:
            try:
                self._capture.cancel()
            except Exception:
                log.exception("Arbiter: cleanup: capture.cancel failed")
            self._capture = None

        if self._disp and self._ILI9341:
            try:
                self._disp.fill_screen(self._ILI9341.BLACK)
            except Exception:
                log.exception("Arbiter: cleanup: fill_screen failed")

        if self._kb:
            try:
                self._kb.cleanup()
            except Exception:
                log.exception("Arbiter: cleanup: keyboard release failed")
        if self._beeper:
            try:
                self._beeper.cleanup()
            except Exception:
                log.exception("Arbiter: cleanup: beeper release failed")
        if self._touch:
            try:
                self._touch.cleanup()
            except Exception:
                log.exception("Arbiter: cleanup: touch release failed")
        if self._temp:
            try:
                self._temp.cleanup()
            except Exception:
                log.exception("Arbiter: cleanup: temperature sensor release failed")
        if self._disp:
            try:
                self._disp.cleanup()
            except Exception:
                log.exception("Arbiter: cleanup: display release failed")
        if self._backlight:
            try:
                self._backlight.close()
            except Exception:
                log.exception("Arbiter: cleanup: backlight release failed")

    def shutdown(self, error: BaseException | None = None) -> None:
        """
        The one true shutdown entry point — called by the SIGINT/SIGTERM
        handler and by the launchers' top-level except clauses.  Shows the
        interactive shutdown screen (Abort / Re-launch / Exit / Power off)
        while hardware is still live.  "Abort" returns to the running program
        without releasing hardware; all other choices release hardware, then
        act on the choice.  Never returns: it always ends in sys.exit() or
        os.execv(), except when choosing "Abort".

        A second call while shutdown is already in progress (e.g. a second
        Ctrl-C because the first seems to hang) skips straight to a hard
        process exit rather than re-entering this method or cleanup().
        """
        if self._shutting_down:
            log.warning("Arbiter: shutdown re-entered — forcing immediate exit")
            os._exit(1)
        self._shutting_down = True
        log.info("Arbiter: shutdown requested%s", " (error)" if error else "")

        choice = "exit"
        try:
            if self._disp is not None:
                choice = shutdown_screen.run(self._disp, self._touch, self._kb,
                                             error=error, simulation=_SIMULATION_MODE)
        except Exception:
            log.exception("Arbiter: shutdown screen failed")

        if choice == "abort":
            log.info("Arbiter: shutdown aborted, resuming")
            self._shutting_down = False
            self._resume_display()
            return

        try:
            self.cleanup()
        finally:
            self._finish_shutdown(choice)

    def _finish_shutdown(self, choice: str) -> None:
        """Act on the operator's shutdown choice.  Hardware is already released."""
        if choice == "relaunch":
            log.info("Arbiter: relaunching")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        elif choice == "poweroff":
            if _SIMULATION_MODE:
                log.info("Arbiter: SIMULATION — would power off now")
            else:
                log.info("Arbiter: powering off")
                try:
                    subprocess.run(["sudo", "shutdown", "-h", "now"], check=True)
                except Exception:
                    log.exception("Arbiter: poweroff command failed")
        sys.exit(0)

    # ── Phase contract: clock / stamps ─────────────────────────────────────────

    def now(self) -> float:
        return time.monotonic()

    def timestamp(self) -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def epoch(self) -> int:
        """Wall-clock seconds since the epoch — for the samples.* milestone stamps."""
        return int(time.time())

    # ── Phase contract: temperature sensor ─────────────────────────────────────

    def read_temperature(self):
        """
        Sample the DS18B20 and return the temperature in Celsius as a float, or
        None if no sensor is connected / the reading failed.  Called from the
        entry2 Temp label button so the operator can capture the sample's
        temperature with one press.
        """
        if self._temp is None:
            return None
        return self._temp.read_c()

    # ── Phase contract: database / sample lifecycle ────────────────────────────

    def defaults(self):
        """The loaded GUI defaults (entry pre-fills), or None if unavailable."""
        return self._defaults

    def next_sample_no(self) -> str:
        """The next sequential sample number for the current default template."""
        template = self._defaults.samp_no_template if self._defaults else ""
        try:
            return sample_repo.next_sample_no(template)
        except sqlite3.Error:
            log.exception("Arbiter: next_sample_no failed")
            return ""

    def begin_sample(self, slot: Slot) -> bool:
        """
        Reserve the sample row at Begin (INSERT), claiming its samp_no in the DB.
        Returns False on a duplicate/invalid samp_no (UNIQUE violation) so the
        caller can warn the operator instead of advancing.
        """
        r = slot.record
        try:
            rowid = sample_repo.insert_sample(
                r["samp_no"], r["userid"], r["testlotid"], r["colorimeterid"])
        except sqlite3.IntegrityError:
            log.warning("Arbiter: samp_no %r already in use", r["samp_no"])
            return False
        except sqlite3.Error:
            log.exception("Arbiter: begin_sample failed for slot %s", slot.name)
            return False
        r["db_rowid"] = rowid
        log.info("Arbiter: reserved sample %s (rowid=%s) for slot %s",
                 r["samp_no"], rowid, slot.name)
        return True

    def persist(self, slot: Slot, **cols) -> None:
        """
        Update the slot's record and write the given columns to its samples row.
        A no-op on the DB side until the row is reserved (db_rowid set); the
        record is always updated so the values survive in memory regardless.
        """
        slot.record.update(cols)
        rowid = slot.record.get("db_rowid")
        if rowid is None:
            return
        try:
            sample_repo.update_sample(rowid, **cols)
        except sqlite3.Error:
            log.exception("Arbiter: persist failed for slot %s (rowid=%s)",
                          slot.name, rowid)

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

    def _resume_display(self) -> None:
        """Repaint whatever was on screen before a full-screen interruption
        (e.g. an aborted shutdown screen) — a slot, or the Menu."""
        if self._focused is not None:
            self.render(self._focused)
        else:
            self._render_menu()

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

    # ── Menu screen ──────────────────────────────────────────────────────────────
    # A screen of its own, reached by the dedicated footer MENU button (not the
    # A/B toggle) and left via its own OK/CANCEL — never tied to slot A/B data.

    def open_menu(self, slot: Slot) -> None:
        """Open the Menu screen; `slot` is who OK/CANCEL will return the view to."""
        self._pre_menu_slot = slot
        self._menu_staged_rowid = self._default_rowid
        self._focused = None
        self._render_menu()

    def _render_menu(self) -> None:
        if self._pool is None:
            return
        self._pool.hide_all()
        self._pool.put("menu_cancel_btn", "CANCEL", on=self._menu_cancel)
        self._pool.put("menu_title",      "Menu")
        self._pool.put("menu_ok_btn",     "OK",     on=self._menu_ok)
        self._pool.put("menu_power",      "Power",    on=self._menu_power)
        self._pool.put("menu_defaults",   "Defaults", on=self._menu_open_defaults)
        self._pool.render(self._disp)
        self._rebuild_focus()

    def _menu_ok(self) -> None:
        """Save any choices staged while in the menu, then return."""
        if self._menu_staged_rowid != self._default_rowid:
            self._default_rowid = self._menu_staged_rowid
            self._load_defaults()
        self._close_menu()

    def _menu_cancel(self) -> None:
        """Discard any choices staged while in the menu, then return."""
        self._close_menu()

    def _close_menu(self) -> None:
        slot = self._pre_menu_slot or self._slots["A"]
        self._pre_menu_slot = None
        self.focus(slot)

    def _menu_power(self) -> None:
        self.shutdown()

    def _menu_open_defaults(self) -> None:
        """
        A minimal row-picker over default_gui_vals.  Only rowid=1 exists today,
        so this is a confirm-and-stage rather than a real list; Select stages
        the rowid (applied only if the menu's OK is then pressed) so the
        mechanism is exercised end to end and is ready to grow into a real
        picker once more default rows exist.
        """
        if self._pool is None:
            return
        d = self._defaults
        msg = (f"rowid {d.rowid}: {d.fullname} / {d.testkit}"
              if d else "No defaults loaded")
        self._pool.hide_all()
        self._pool.put("modal_panel")
        self._pool.put("modal_msg", msg)
        names = self._pool.layout_modal_buttons(2)
        self._pool.put(names[0], "Select", on=self._menu_select_defaults)
        self._pool.put(names[1], "Back",   on=self._render_menu)
        self._pool.render(self._disp)
        self._rebuild_focus()

    def _menu_select_defaults(self) -> None:
        self._menu_staged_rowid = 1     # the only default row today
        self._render_menu()

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
        self.persist(slot, capture_start=self.epoch(),
                     manual_read=slot.record.get("manual_read", ""))
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

    def cancel_test(self, slot: Slot, delete: bool = False) -> None:
        """
        Abandon the current run.  `delete` removes the reserved samples row (the
        operator chose 'Delete row' to start over); otherwise the row is kept
        as-is ('Keep row').  Either way the slot resets to a fresh entry.
        """
        log.info("Arbiter: cancel test in slot %s (delete=%s)", slot.name, delete)
        if self._capture_slot is slot and self._capture is not None:
            self._capture.cancel()
            self._capture = None
            self._capture_slot = None
        if delete:
            rowid = slot.record.get("db_rowid")
            if rowid is not None:
                try:
                    sample_repo.delete_sample(rowid)
                except sqlite3.Error:
                    log.exception("Arbiter: delete_sample failed (rowid=%s)", rowid)
        slot.clear_run()
        self.goto(slot, "entry")

    def reset_timer(self, slot: Slot) -> None:
        # Reset re-arms the reagent step, so its recorded start is no longer valid.
        ap = slot.active_phase
        if ap in ("reagent1", "reagent2"):
            self.persist(slot, **{f"{ap}_start": None})
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
                self._pool.set("instr3", owner.progress)
                self._pool.redraw(self._disp, "instr3")
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
        self.persist(owner, capture_end=self.epoch(),
                     h5file=str(session.out_path) if session.out_path else "")
        self.goto(owner, "analysis")
        # A capture just freed the camera; re-enable a waiting CAPTURE button
        # on the other slot if it is the one being viewed (not, say, the Menu).
        if (self._focused is not None and self._focused is not owner
                and self._focused.phase == "read"):
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
        self.persist(slot, txt_result=slot.result)
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
        focused slot's record.  On the first entry screen, also re-enable Begin
        the moment the sample number and user are both set.
        """
        slot = self._focused
        if slot is None:
            return
        fields = {"entry":  EntryPhase.FIELDS,
                  "entry2": Entry2Phase.FIELDS}.get(slot.phase)
        if not fields:
            return
        key = fields.get(textbox.name)
        if key is None:
            return
        slot.record[key] = textbox.value
        if slot.phase == "entry":
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
        """Once-a-second repaint of the footer clock and, if running, the
        focused slot's timer.  A no-op on the Menu screen (no clock there)."""
        if self._pool is None:
            return
        self._pool.set("clock", datetime.datetime.now().strftime("%b %d %H:%M:%S"))
        self._pool.redraw(self._disp, "clock")
        if self._focused is None:
            return
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
    B.record["samp_no"] = "BX9"; B.phase = B.active_phase = "reagent1"
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
    A.record["samp_no"] = "AX1"; A.record["user"] = "u"
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
