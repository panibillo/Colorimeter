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
import functools
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

import config_manager as config
import logging_manager
import widgets
import shutdown_screen
import sample_repo
import exposure_repo
import layout
from widgets import Button, TextBox
from pool import Pool
from slot import Slot
from phases import build_phases, EntryPhase, Entry2Phase

log = logging.getLogger(__name__)

# Phases during which the shared camera is kept open: a run from its first
# reagent wait through capture and analysis.  Outside this set (entry/entry2,
# and after results) no capture is imminent, so the camera is released.
_CAMERA_PHASES = ("reagent1", "reagent2", "read", "analysis")

# Tunables (LED range, loop cadence, timeouts, reagent durations, rowid, the
# backlight pin) live in config/instrument.yaml and are read through
# config_manager — see the config.* calls throughout this module.


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

        # Slots whose timer expired while unfocused, oldest first — each shows a
        # visible "Resume <slot>" alarm button until that slot regains focus.
        self._alarms: list[Slot] = []

        # Focus within the current screen
        self._focusables: list = []
        self._focus_idx:  int  = -1

        # The single capture in flight, plus which slot owns it.
        self._capture_factory = capture_factory
        self._capture: CaptureSession | None = None
        self._capture_slot: Slot | None = None

        # Set once the capture subsystem's heavy first-imports (picamera2,
        # libcamera, numpy, cv2) have been warmed — see prewarm_capture().
        self._capture_prewarmed = False

        # The persistent camera + LED bank, owned by the Arbiter and shared by
        # every capture.  Opened once a run reaches its reagent wait (so the
        # ~1s sensor-start settle overlaps the countdown) and reused across
        # captures; released when no slot needs them and at cleanup.  Each
        # CaptureSession *borrows* these rather than opening its own.  None
        # until opened — see _ensure_camera()/_maybe_open_camera().
        self._camera = None
        self._leds   = None

        # Placeholder analysis timer: (slot, started_at) or None.
        self._analysis: tuple | None = None

        # Shutdown state: guards against a second signal re-entering shutdown()
        # or cleanup() running its hardware-release chain twice.
        self._shutting_down = False
        self._cleaned_up    = False

        # Reference data: which default_gui_vals row seeds the entry screens, and
        # the resolved Defaults loaded from it (the rowid comes from config).
        self._default_rowid = config.default_rowid()
        self._defaults: sample_repo.Defaults | None = None

        # Menu screen state: the slot to return to, and a staged (not yet
        # committed) default_rowid choice — see open_menu()/_menu_ok().
        self._pre_menu_slot: Slot | None = None
        self._menu_staged_rowid: int | None = None

        # Capture screen state (Menu → Capture): the staged path + images
        # choice, which view is showing, and the run's progress/result text.
        # A menu-launched capture uses self._capture with _capture_slot = None
        # (the None marks it standalone, so pump_capture routes it here).
        self._cap_mode:  str = "normal"       # "calibration" | "normal"
        self._cap_group: str = "1"            # images per LED, '1'..'5'
        self._cap_view:  str | None = None    # None | "choice" | "progress" | "done"
        self._cap_progress: str = ""
        self._cap_result:   str = ""
        # The group saved before the choice screen opened, so CANCEL can restore it.
        self._cap_saved_on_open: str | None = None

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
        self._backlight = DigitalOutputDevice(config.backlight_pin(),
                                              initial_value=True)
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
        # Reagent countdowns come from config; the simulation profile shortens
        # them so runs are quick to test.
        self._phases = build_phases(reagent1_secs=config.reagent1_secs(),
                                    reagent2_secs=config.reagent2_secs())
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

        # Sessions borrow the shared camera without stopping it, so the Arbiter
        # (its owner) releases it here.
        self._release_camera()

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
                                             error=error, simulation=_SIMULATION_MODE,
                                             timeout_s=config.shutdown_timeout_s())
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
        self._paint_alarm(slot)
        self._pool.render(self._disp)
        self._rebuild_focus()

    def focus(self, slot: Slot) -> None:
        """Make a slot the displayed one and render it (user-initiated only)."""
        self._focused = slot
        self._disarm_alarm(slot)     # regaining focus clears its visible alarm
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

    # def _paint_slot_toggle(self) -> None:
    #     """Label the slot button with the slot you would switch TO."""
    #     target = "B" if self._focused.name == "A" else "A"
    #     from palette import color
    #     btn = self._pool["slot_btn"]
    #     btn.label = target
    #     btn.color = color("slot_b_bg" if target == "B" else "slot_a_bg")
    #     btn.on_press = self.toggle_slot

    def _paint_slot_toggle(self) -> None:
        """Show the CURRENT slot (id + colour); pressing it toggles to the other."""
        current = self._focused.name
        from palette import color
        btn = self._pool["slot_btn"]
        btn.label = current
        btn.color = color("slot_a_bg" if current == "A" else "slot_b_bg")
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
        self._pool.put("menu_capture",    "Capture",  on=self._menu_open_capture)
        self._pool.put("menu_defaults",   "Defaults", on=self._menu_open_defaults)
        self._pool.put("menu_power",      "Power",    on=self._menu_power)
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
        self._menu_staged_rowid = config.default_rowid()   # the only row today
        self._render_menu()

    # ── Capture screen (Menu → Capture) ────────────────────────────────────────
    # A standalone capture launched from the Menu: pick a path (Calibration or
    # Normal) and a number of images per LED (1..5 = the exposure group), run it,
    # and see a placeholder analysis result. It uses the single camera via
    # self._capture with _capture_slot = None (the None marks it as menu-launched,
    # so pump_capture routes it to _pump_capture_screen). This is independent of
    # the A/B slots and the per-sample ReadPhase capture.

    def _menu_open_capture(self) -> None:
        """Open the capture choice screen, initialising from the saved default."""
        self._cap_mode  = "normal"
        try:
            self._cap_saved_on_open = sample_repo.load_default_exposuregroup(
                self._default_rowid)
        except sqlite3.Error:
            self._cap_saved_on_open = None
        self._cap_group = self._initial_exposure_group()
        self._cap_progress = ""
        self._cap_result   = ""
        self._render_capture_choice()

    def _initial_exposure_group(self) -> str:
        """
        The exposure group to preselect when the capture screen opens: the value
        last saved on the defaults row, or the configured fallback when nothing
        has been saved yet.

        The operator's saved choice is honoured whether or not the exposure
        table currently has rows for that group — preselecting anything else
        would silently override what they picked (the previous behaviour, which
        made the screen always reopen on the fallback group after selecting an
        uncalibrated one). A Normal capture on an uncalibrated group simply
        finds nothing to capture; that is handled at capture time, not by
        second-guessing the preselection here.
        """
        try:
            saved = sample_repo.load_default_exposuregroup(self._default_rowid)
        except sqlite3.Error:
            log.exception("Arbiter: could not read saved exposure group")
            saved = None
        if saved is not None:
            return saved
        # Nothing saved yet — the configured default (config/instrument.yaml,
        # capture.fallback_exposure_group).
        return config.fallback_exposure_group()

    def _render_capture_choice(self) -> None:
        if self._pool is None:
            return
        from palette import color
        # A Button's fill is its .color (Labels use .bg), so a radio group is
        # highlighted by setting the selected button's .color green.
        sel, unsel = color("start_green"), color("btn_gray")
        self._cap_view = "choice"
        p = self._pool
        p.hide_all()
        # Narrow the title so it sits between the CANCEL/OK corners (like the
        # Menu screen) instead of painting over them.
        p["cap_title"].rect = layout.MENU_TITLE
        p.put("cap_title", "Capture")
        p.put("cap_mode_lbl", "Path")
        cal = self._cap_mode == "calibration"
        p.put("cap_mode_cal",  "Calibrate", on=lambda: self._cap_set_mode("calibration"))
        p["cap_mode_cal"].color  = sel if cal else unsel
        p.put("cap_mode_norm", "Normal",    on=lambda: self._cap_set_mode("normal"))
        p["cap_mode_norm"].color = sel if not cal else unsel
        p.put("cap_n_lbl", "Images")
        for n in range(1, 6):
            name = f"cap_n{n}"
            p.put(name, str(n), on=(lambda nn=n: self._cap_set_group(nn)))
            p[name].color = sel if self._cap_group == str(n) else unsel
        # Top-corner chrome like the Menu screen: reuse its CANCEL/OK widgets.
        p.put("menu_cancel_btn", "CANCEL", on=self._cap_choice_cancel)
        p.put("menu_ok_btn",     "OK",     on=self._cap_choice_ok)
        p.put("cap_start", "Calibrate\nExposures", on=self._cap_start)
        p.render(self._disp)
        self._rebuild_focus()

    def _cap_choice_ok(self) -> None:
        """Keep the current selection (already saved on tap) and leave to Menu."""
        self._cap_view = None
        self._render_menu()

    def _cap_choice_cancel(self) -> None:
        """Discard this visit's group change (restore the pre-open value), to Menu."""
        try:
            sample_repo.save_default_exposuregroup(
                self._default_rowid, self._cap_saved_on_open)
        except sqlite3.Error:
            log.exception("Arbiter: could not restore exposure group on cancel")
        self._cap_view = None
        self._render_menu()

    def _cap_set_mode(self, mode: str) -> None:
        self._cap_mode = mode
        self._render_capture_choice()

    def _cap_set_group(self, n) -> None:
        self._cap_group = str(n)
        # Persist the choice onto the defaults row so it is preselected next time.
        try:
            sample_repo.save_default_exposuregroup(self._default_rowid, self._cap_group)
        except sqlite3.Error:
            log.exception("Arbiter: could not save exposure group to defaults")
        self._render_capture_choice()

    def _cap_start(self) -> None:
        """Launch the capture with the staged path + images choice."""
        if not self.capture_available():
            self._cap_result = "Camera busy — try again later"
            self._render_capture_done()
            return
        from capture_runner import CaptureConfig
        cfg = CaptureConfig(mode=self._cap_mode, exposure_group=self._cap_group,
                            led_start=config.capture_led_start(),
                            led_end=config.capture_led_end())
        session = self._new_session(cfg)
        if not session.start():
            log.error("Arbiter: capture failed to start: %s", session.error)
            self._cap_result = f"Camera failed: {session.error}"
            self._render_capture_done()
            return
        self._capture = session
        self._capture_slot = None          # standalone (menu-launched) capture
        self._cap_progress = "Starting camera..."
        log.info("Arbiter: menu capture started (%s, group %s)",
                 self._cap_mode, self._cap_group)
        self._render_capture_progress()

    def _render_capture_progress(self) -> None:
        if self._pool is None:
            return
        self._cap_view = "progress"
        p = self._pool
        p.hide_all()
        p["cap_title"].rect = layout.CAP_TITLE       # full width (no corner buttons here)
        p.put("cap_title", "Calibrating" if self._cap_mode == "calibration"
              else "Capturing")
        p.put("cap_progress", self._cap_progress or "Working...")
        p.put("cap_back", "Cancel", on=self._cap_cancel)
        p.render(self._disp)
        self._rebuild_focus()

    def _render_capture_done(self) -> None:
        if self._pool is None:
            return
        self._cap_view = "done"
        p = self._pool
        p.hide_all()
        p["cap_title"].rect = layout.CAP_TITLE       # full width (no corner buttons here)
        failed = "fail" in self._cap_result.lower()
        p.put("cap_title", "Failed" if failed else "Done")
        p.put("cap_progress", self._cap_result or "")
        p.put("cap_start", "OK", on=self._cap_done_ok)
        p.render(self._disp)
        self._rebuild_focus()

    def _cap_done_ok(self) -> None:
        self._cap_view = None
        self._render_menu()

    def _cap_cancel(self) -> None:
        """Abandon a running menu capture and return to the Menu."""
        if self._capture is not None:
            try:
                self._capture.cancel()
            except Exception:
                log.exception("Arbiter: menu capture cancel failed")
        self._capture = None
        self._capture_slot = None
        self._cap_view = None
        self._render_menu()

    def _pump_capture_screen(self, session) -> None:
        """Drive a menu-launched capture and mirror it onto the capture screen."""
        progress = session.step()
        if progress is not None:
            self._cap_progress = (getattr(progress, "message", "")
                                  or getattr(progress, "color", ""))
            if self._cap_view == "progress" and self._pool is not None:
                self._pool.set("cap_progress", self._cap_progress)
                self._pool.redraw(self._disp, "cap_progress")
            return

        # step() returned None: the run ended.
        self._capture = None
        self._capture_slot = None
        if session.error is not None:
            self._cap_result = f"Capture failed: {str(session.error)[:40]}"
            self._render_capture_done()
            return
        if self._cap_mode == "calibration":
            self._persist_calibration(session)
            self._cap_result = self._analyze_calibration(session)
        else:
            self._cap_result = self._analyze_normal(session)
        self._render_capture_done()

    def _persist_calibration(self, session) -> None:
        """
        Write each LED's discovered ideal exposure (e*) to the exposure table as
        its family of groups. All LEDs in one run share a single add_dt so the
        run's rows group together; prior calibrations are kept (active=0).
        """
        runner = getattr(session, "runner", None)
        results = getattr(runner, "results", None) or []
        add_dt = self.timestamp()
        suite = getattr(runner, "suite_id", "")
        written = 0
        for r in results:
            estar = (getattr(r, "metadata", {}) or {}).get("e3_final_us")
            if estar is None:
                continue
            try:
                exposure_repo.write_estar(r.color, estar, add_dt=add_dt,
                                          remark=f"calib {suite}")
                written += 1
            except Exception:
                log.exception("Arbiter: failed to persist exposures for %s", r.color)
        log.info("Arbiter: persisted exposure groups for %d LED(s)", written)

    def _analyze_calibration(self, session) -> str:
        """Placeholder calibration-analysis path (engine TODO)."""
        runner = getattr(session, "runner", None)
        n = len(getattr(runner, "results", None) or [])
        log.info("Arbiter: calibration analysis (placeholder) for %d LED(s)", n)
        return f"Calibrated {n} LED(s); exposures saved."

    def _analyze_normal(self, session) -> str:
        """Placeholder normal-analysis path (engine TODO)."""
        log.info("Arbiter: normal analysis (placeholder)")
        name = session.out_path.name if session.out_path else "archive"
        return f"Capture complete: {name}"

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

    @property
    def cap_mode(self) -> str:
        """Current Menu -> Capture path selection: 'calibration' | 'normal'."""
        return self._cap_mode

    def request_capture(self, slot: Slot) -> bool:
        """Start a capture for slot if the camera is free.  Returns granted."""
        if self._capture is not None:
            log.info("Arbiter: capture denied — camera busy (slot %s)",
                     self._capture_slot.name if self._capture_slot else "?")
            return False
        # Use the Menu -> Capture screen's staged path + images choice, same
        # as the standalone menu capture — a sample's Read-phase capture must
        # not silently fall back to a full calibration sweep.
        from capture_runner import CaptureConfig
        cfg = CaptureConfig(mode=self._cap_mode, exposure_group=self._cap_group,
                            led_start=config.capture_led_start(),
                            led_end=config.capture_led_end())
        session = self._new_session(cfg)
        if not session.start():
            log.error("Arbiter: capture failed to start: %s", session.error)
            slot.progress = "Camera failed"
            return False
        self._capture = session
        self._capture_slot = slot
        slot.progress = "Starting camera..."
        self.persist(slot, capture_start=self.epoch(),
                     manual_read=slot.record.get("manual_read", ""))
        log.info("Arbiter: capture started for slot %s (mode=%s, group=%s)",
                 slot.name, self._cap_mode, self._cap_group)
        return True

    def _new_session(self, cfg=None):
        """
        Build a capture session (test factory if given, else the real one).

        Both callers (the Menu capture screen and the per-sample ReadPhase
        capture) now pass their own CaptureConfig, built from the current
        self._cap_mode / self._cap_group selection. cfg=None is a defensive
        fallback only — no caller currently relies on it.
        """
        if self._capture_factory is not None:
            return self._capture_factory()
        from capture_runner import CaptureConfig
        from capture_session import CaptureSession
        if cfg is None:
            cfg = CaptureConfig(led_start=config.capture_led_start(),
                                led_end=config.capture_led_end())
        # Borrow the Arbiter's persistent camera when available, so this run
        # reuses the already-open sensor instead of paying the open cost again.
        # If it could not be opened, the session builds and owns its own.
        hw = self._ensure_camera()
        if hw is not None:
            cam, leds = hw
            return CaptureSession(cfg, cam=cam, leds=leds)
        return CaptureSession(cfg)

    def start_analysis(self, slot: Slot) -> None:
        # Analysis engine is not implemented yet; a placeholder completes after
        # config.analysis_secs() so the whole flow is exercisable end to end.
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

    # ── Visible alarm (unfocused slot's timer expired) ──────────────────────────

    def _arm_alarm(self, slot: Slot) -> None:
        """
        Record a pending visible alarm for a backgrounded slot, then repaint the
        focused slot so its "Resume <slot>" button appears.  Oldest stays first.
        """
        if slot not in self._alarms:
            self._alarms.append(slot)
            log.info("Arbiter: visible alarm armed for slot %s", slot.name)
        if self._focused is not None:
            self.render(self._focused)

    def _disarm_alarm(self, slot: Slot) -> None:
        """Clear a slot's pending visible alarm (it regained focus)."""
        if slot in self._alarms:
            self._alarms.remove(slot)
            log.info("Arbiter: visible alarm disarmed for slot %s", slot.name)

    def _paint_alarm(self, focused: Slot) -> None:
        """
        Show the visible alarm button for the oldest OTHER slot with a pending
        alarm, or leave it hidden (by hide_all) when none remain.  Painted in
        render() after the phase, like the slot toggle.
        """
        pending = [s for s in self._alarms if s is not focused]
        if not pending:
            return
        target = pending[0]          # oldest OTHER slot still alarming

        # Tapping the alarm returns focus to the slot that expired. partial binds
        # `target` by value now — clearer than a lambda closing over a variable,
        # and it makes the side effect (focus THIS slot) explicit.
        on_resume = functools.partial(self.focus, target)
        self._pool.put("alarm_btn", f"Resume\n{target.name}", on=on_resume)

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
                # If the slot that expired is not the one on screen, the operator
                # would only have the beep to go on — raise a visible alarm too.
                if slot is not self._focused:
                    self._arm_alarm(slot)

    def pump_capture(self) -> None:
        """
        Drive the in-flight capture forward and mirror its progress onto the
        owner slot.

        The capture is a generator (CaptureRunner.run) with no thread or callback
        of its own, so it advances only when pumped — hence the call on every
        main-loop pass.  Each call resumes the generator and blocks until its next
        yield: not a fixed unit of work but wherever the runner placed its next
        checkpoint, from a mere wait tick to several back-to-back camera frames.
        Every yield carries a Progress, whose text is stored on owner.progress
        and, when that slot is on screen, repainted live in the read phase.

        A step that yields None means the run has ended: the camera is freed, the
        elapsed timer frozen, capture_end persisted, and the slot advanced to the
        analysis phase — or, on failure, left showing the capture error.
        """
        session = self._capture
        if session is None or not session.active:
            return
        # A menu-launched (standalone) capture has no owner slot; route it to
        # the capture screen instead of the per-slot read/analysis flow.
        if self._capture_slot is None:
            self._pump_capture_screen(session)
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
        self.persist(owner, capture_end=self.epoch(),
                     h5file=str(session.out_path) if session.out_path else "")
        self.goto(owner, "analysis")
        # A capture just freed the camera; re-enable a waiting CAPTURE button
        # on the other slot if it is the one being viewed (not, say, the Menu).
        if (self._focused is not None and self._focused is not owner
                and self._focused.phase == "read"):
            self.render(self._focused)

    def pump_analysis(self) -> None:
        """
        Advance the pending analysis and, when it finishes, record the result on
        the slot.  Called on every main-loop pass, like pump_capture — a no-op
        until start_analysis arms it (the (slot, started_at) pair in _analysis).

        Placeholder: no analysis engine exists yet, so there is no work to pump.
        This simply waits out config.analysis_secs() of wall-clock time, then stamps a stub
        result, persists it, and repaints the slot.  When the real engine lands
        it will be driven from here the way pump_capture drives a capture.
        """
        if self._analysis is None:
            return
        slot, started = self._analysis
        if self.now() - started < config.analysis_secs():
            return
        self._analysis = None
        slot.result = "Analysis not yet implemented"
        self.persist(slot, txt_result=slot.result)
        log.info("Arbiter: analysis done for slot %s", slot.name)
        self.render(slot)

    # ── Capture subsystem prewarm ───────────────────────────────────────────────

    def _maybe_prewarm(self) -> None:
        """
        Warm the capture imports once a run reaches its (minutes-long) reagent
        wait, so the picamera2/libcamera/numpy/cv2 first-import cost is paid
        during the countdown instead of the instant CAPTURE is pressed.

        Checked from the main loop rather than on phase entry so the reagent
        screen has already rendered — only a background loop pass absorbs the
        one-time import stall, and the monotonic reagent timer is unaffected by
        it (poll_timers is deadline arithmetic, so a stall just detects expiry a
        few seconds late over a 2–7 minute wait).
        """
        if self._capture_prewarmed:
            return
        if any(s.active_phase in ("reagent1", "reagent2")
               for s in self._slots.values()):
            self.prewarm_capture()

    def prewarm_capture(self) -> None:
        """
        Import the capture subsystem ahead of the first CAPTURE press.

        picamera2, libcamera, numpy and cv2 carry several seconds of first-import
        cost that would otherwise land when CAPTURE is pressed.  Importing them
        here populates sys.modules for the rest of the process, so the later
        import inside CaptureSession is a cache hit.  Attempted at most once per
        process (the flag is set before the work, so broken imports can't spam
        the loop with repeated stalls); camera hardware is NOT opened here — that
        has a thermal cost and is opened closer to the read phase.  A failure is
        logged and swallowed: capture will still import (and surface any real
        error) at CAPTURE time exactly as it did before.
        """
        if self._capture_prewarmed:
            return
        self._capture_prewarmed = True
        t0 = time.monotonic()
        try:
            import numpy            # noqa: F401 — also pulled in by capture_runner
            import capture_runner   # noqa: F401 — → bayer_cam(picamera2), libcamera
            import capture_session  # noqa: F401
            import lighting_cal8    # noqa: F401 — LEDBank module (SPI opened later)
            try:
                import cv2          # noqa: F401 — JPEG encode; import lazily if absent
            except ImportError:
                log.info("Arbiter: prewarm — cv2 unavailable, will import lazily")
        except Exception:
            log.exception("Arbiter: capture prewarm failed")
            return
        log.info("Arbiter: capture subsystem prewarmed in %.2fs",
                 time.monotonic() - t0)

    # ── Shared camera (persistent, borrowed by every capture) ───────────────────

    def _camera_wanted(self) -> bool:
        """True while any slot is in a phase that will soon use the camera."""
        return any(s.active_phase in _CAMERA_PHASES
                   for s in self._slots.values())

    def _maybe_open_camera(self) -> None:
        """
        Open the shared camera once a run reaches its reagent wait, so the
        ~1s sensor-start settle is paid during the (minutes-long) countdown
        rather than at CAPTURE.  Gated on prewarm so the picamera2 import cost
        is already absorbed; a failure is logged and left for capture time.

        Like _maybe_prewarm, this is polled from the main loop (not phase
        entry) so the reagent screen has rendered first and only a background
        pass wears the open cost.
        """
        if self._camera is not None or not self._capture_prewarmed:
            return
        if self._camera_wanted():
            self._ensure_camera()

    def _ensure_camera(self):
        """
        Return the open shared (camera, leds), opening them on demand.

        Used both by _maybe_open_camera (during reagent) and by _new_session
        (so a capture launched before the camera was opened — e.g. a Menu
        capture with no running slot — still gets one).  Returns None on
        failure; the caller then falls back to per-session hardware so capture
        still works (and surfaces any real error) exactly as before.
        """
        if self._camera is not None and self._leds is not None:
            return self._camera, self._leds
        try:
            from bayer_cam import BayerCam
            from lighting_cal8 import LEDBank
            cam = self._camera or BayerCam()
            cam.setup_camera()          # idempotent; opens + settles the sensor
            leds = self._leds or LEDBank()
            self._camera, self._leds = cam, leds
            log.info("Arbiter: shared camera opened")
            return self._camera, self._leds
        except Exception:
            log.exception("Arbiter: failed to open shared camera")
            self._release_camera()
            return None

    def _maybe_release_camera(self) -> None:
        """Close the shared camera once no slot needs it and no capture is live."""
        if self._camera is None and self._leds is None:
            return
        if self._capture is not None or self._camera_wanted():
            return
        log.info("Arbiter: no slot needs the camera — releasing it")
        self._release_camera()

    def _release_camera(self) -> None:
        """Stop + close the shared camera and LED bank. Idempotent; never raises."""
        if self._camera is not None:
            try:
                self._camera.stop()
            except Exception:
                log.exception("Arbiter: error stopping shared camera")
            self._camera = None
        if self._leds is not None:
            try:
                self._leds.close()
            except Exception:
                log.exception("Arbiter: error closing shared LED bank")
            self._leds = None

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
        poll_s = config.main_loop_poll_s()
        last_clock = 0.0
        while True:
            now = time.monotonic()

            if _SIMULATION_MODE and hasattr(self._disp, "process_events"):
                self._disp.process_events()   # type: ignore

            self.poll_timers()
            self._maybe_prewarm()
            self._maybe_open_camera()
            self.pump_capture()
            self.pump_analysis()
            self._maybe_release_camera()

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
                    time.sleep(poll_s)
                if time.monotonic() >= timeout:
                    self._touch._is_touched = False  # type: ignore

            time.sleep(poll_s)

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

    from pathlib import Path

    class _FakeRunner:
        """Stands in for CaptureRunner: what the arbiter reads after a run."""
        def __init__(self):
            self.suite_id = "TESTSUITE"; self.results = []

    class _FakeCapture:
        """Completes after `steps` step() calls."""
        def __init__(self, config=None, steps=3):
            self.active = False; self.error = None
            self.out_path = Path("fake.h5")
            self.runner = _FakeRunner()
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

    print("== capture prewarm: off-reagent no-op, fires on reagent, idempotent ==")
    check("not prewarmed initially",  arb._capture_prewarmed, False)
    arb._maybe_prewarm()                          # no slot in a reagent yet
    check("no prewarm off-reagent",   arb._capture_prewarmed, False)
    A.active_phase = "reagent1"
    arb._maybe_prewarm()                          # a slot is now in a reagent
    check("prewarmed on reagent",     arb._capture_prewarmed, True)
    check("capture_runner imported",  "capture_runner" in sys.modules, True)
    check("capture_session imported", "capture_session" in sys.modules, True)
    arb.prewarm_capture()                         # idempotent — must not raise
    A.active_phase = "entry"                       # restore for the checks below

    print("== shared camera: opens on reagent, released when idle ==")

    class _FakeCam:
        def __init__(self):  self.stopped = False
        def setup_camera(self): pass
        def stop(self):      self.stopped = True

    class _FakeLeds:
        def __init__(self):  self.closed = False
        def close(self):     self.closed = True

    # Stand in for the real hardware open so the self-test needs no camera.
    def _fake_ensure():
        if arb._camera is None:
            arb._camera, arb._leds = _FakeCam(), _FakeLeds()
        return arb._camera, arb._leds
    arb._ensure_camera = _fake_ensure

    check("no camera while idle",   arb._camera_wanted(), False)
    arb._maybe_open_camera()
    check("idle → camera stays shut", arb._camera is None, True)
    A.active_phase = "reagent2"
    arb._maybe_open_camera()
    check("reagent → camera opened", arb._camera is not None, True)
    fake_cam, fake_leds = arb._camera, arb._leds
    arb._maybe_release_camera()                    # still wanted (A in reagent2)
    check("kept open while wanted",  arb._camera is fake_cam, True)
    A.active_phase = "entry"                        # both slots idle now
    arb._maybe_release_camera()
    check("idle → camera released",  arb._camera is None, True)
    check("camera stopped",          fake_cam.stopped, True)
    check("leds closed",             fake_leds.closed, True)

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

    print("== unfocused expiry raises a visible alarm on the focused slot ==")
    check("B alarm armed",       B in arb._alarms, True)
    check("alarm button shown",  arb._pool["alarm_btn"].visible, True)
    check("alarm button label",  arb._pool["alarm_btn"].label, "Resume\nB")
    # Resuming B (via the button's action = focus B) disarms it.
    arb._pool["alarm_btn"].on_press()
    check("now focused on B",    arb._focused is B, True)
    check("B alarm disarmed",    B in arb._alarms, False)
    arb.focus(A)                                     # no alarms left
    check("alarm button hidden", arb._pool["alarm_btn"].visible, False)

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
    arb._analysis = (A, arb.now() - config.analysis_secs() - 1)   # force it done
    arb.pump_analysis()
    check("result set",           bool(A.result), True)
    check("POST enabled",         arb._pool["run_btn"].enabled, True)

    # ── Capture screen (Menu → Capture) ──────────────────────────────────────
    # Runs against a throwaway copy of the DB so the exposure table and the
    # saved default group are exercised for real without touching live data.
    import shutil
    import tempfile
    import db_manager as _db
    _tmpdir = tempfile.mkdtemp()
    _tmpdb = Path(_tmpdir) / "arbiter_selftest.sqlite"
    shutil.copy(Path(_db.DBNAME), _tmpdb)     # _db.DBNAME is the live DB (config)
    _db.DBNAME = str(_tmpdb)
    _db.write_one("DELETE FROM exposure")
    sample_repo.save_default_exposuregroup(1, None)     # start with nothing saved

    class _R:                       # a stand-in LedResult carrying e*
        def __init__(self, color, estar):
            self.color = color; self.metadata = {"e3_final_us": estar}

    print("== capture screen: unsaved group falls back to the configured default ==")
    arb.open_menu(A)
    check("menu open (no slot focus)", arb._focused is None, True)
    arb._menu_open_capture()
    check("choice view",          arb._cap_view, "choice")
    check("default mode normal",  arb._cap_mode, "normal")
    check("fallback group 3",     arb._cap_group, config.fallback_exposure_group())

    print("== capture screen: selecting a group persists it to defaults ==")
    arb._cap_set_group(4)
    check("group set to 4",        arb._cap_group, "4")
    check("persisted to defaults", sample_repo.load_default_exposuregroup(1), "4")

    print("== capture screen: reopen restores the saved group ==")
    exposure_repo.write_estar("amber", 30000)           # some exposures exist
    arb._menu_open_capture()
    check("restored saved group 4", arb._cap_group, "4")

    print("== capture screen: CANCEL restores pre-open group; OK keeps ==")
    arb._menu_open_capture()                            # opens with saved '4'
    arb._cap_set_group(2)                               # saves '2' on tap
    arb._cap_choice_cancel()
    check("CANCEL reverted to 4", sample_repo.load_default_exposuregroup(1), "4")
    check("CANCEL leaves choice", arb._cap_view, None)
    arb._menu_open_capture()
    arb._cap_set_group(2)
    arb._cap_choice_ok()
    check("OK kept 2",            sample_repo.load_default_exposuregroup(1), "2")
    check("OK leaves choice",     arb._cap_view, None)

    print("== capture screen: saved group is honoured even without exposures ==")
    exposure_repo.delete_led("amber")                   # no active groups remain
    arb._menu_open_capture()
    check("saved group 2 kept",   arb._cap_group, "2")  # was '2' from OK above
    sample_repo.save_default_exposuregroup(1, None)     # clear the saved choice
    arb._menu_open_capture()
    check("fallback when unset",  arb._cap_group, config.fallback_exposure_group())

    print("== capture screen: calibration run persists e* per LED ==")
    _db.write_one("DELETE FROM exposure")
    cap_cal = _FakeCapture(steps=2)
    cap_cal.runner.results = [_R("amber", 30000), _R("blue", 20000)]
    arb._capture_factory = lambda: cap_cal
    arb._menu_open_capture()
    arb._cap_set_mode("calibration")
    arb._cap_set_group(3)
    check("mode -> calibration",  arb._cap_mode, "calibration")
    arb._cap_start()
    check("progress view",        arb._cap_view, "progress")
    check("camera busy in run",   arb.capture_available(), False)
    for _ in range(5):
        arb.pump_capture()
    check("done view",            arb._cap_view, "done")
    check("camera freed",         arb.capture_available(), True)
    check("amber e* persisted",   exposure_repo.read_exposures("amber", "1"), [30000])
    check("blue e* persisted",    exposure_repo.read_exposures("blue", "1"), [20000])
    check("result names LEDs",    "2 LED" in arb._cap_result, True)
    arb._cap_done_ok()
    check("OK returns to menu",   arb._cap_view, None)

    print("== capture screen: normal run does not persist ==")
    rows_before = _db.read_val("SELECT COUNT(*) FROM exposure")
    cap_norm = _FakeCapture(steps=2)
    arb._capture_factory = lambda: cap_norm
    arb._menu_open_capture()
    check("reopen resets to normal", arb._cap_mode, "normal")
    arb._cap_start()
    for _ in range(5):
        arb.pump_capture()
    check("normal done",          arb._cap_view, "done")
    check("normal added no rows",  _db.read_val("SELECT COUNT(*) FROM exposure"), rows_before)
    check("normal result text",   "complete" in arb._cap_result.lower(), True)

    print("== capture screen: cancel aborts and frees the camera ==")
    cap_cancel = _FakeCapture(steps=99)      # long-running
    arb._capture_factory = lambda: cap_cancel
    arb._menu_open_capture()
    arb._cap_start()
    arb.pump_capture()                       # one step, still running
    check("still running",        arb.capture_available(), False)
    arb._cap_cancel()
    check("camera freed on cancel", arb.capture_available(), True)
    check("cancel leaves capture view", arb._cap_view, None)

    shutil.rmtree(_tmpdir, ignore_errors=True)

    print("\n== all arbiter self-tests passed ==")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        Arbiter().start()
