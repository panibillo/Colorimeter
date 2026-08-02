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

import config_repo as config
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

# Image viewer.  The preview is for looking at, not measuring, so the LED
# settle is a fraction of a capture run's LED_ON_WARMUP_S (2.0 s) — colour
# accuracy is not required and the operator's wait is what matters.
_PREVIEW_LED_CH      = 10       # LEDBank channel for the white LED
_PREVIEW_LED_NAME    = "white"  # its name in the `exposure` table
_PREVIEW_SETTLE_S    = 0.25     # after switching the LED on, before capturing
_PREVIEW_FALLBACK_US = 10_000   # first guess when nothing better is known

# Preview auto-exposure.  The preview is metered on the **sample** ROI alone:
# it is the only rectangle whose contents have to be readable, and the white
# patch beside it is far brighter, so metering over the frame (or over `white`,
# which is what the stored calibration exposures are for) leaves the sample a
# dark smudge — or, when the stored exposure was calibrated against a dimmer
# scene, blows it out.  Target and tolerance follow the capture path's e3
# convention: the median of the brightest Bayer channel at half of the
# sensor's 10-bit full scale.  See _meter_sample_level().
_PREVIEW_TARGET        = 512    # half of 1023 — capture_runner.HALF_SAT
_PREVIEW_AE_TOLERANCE  = 0.20   # accept a median within ±20 % of the target
_PREVIEW_AE_MAX_FRAMES = 3      # metered frames per preview, at most
# Frames the preview will spend waiting for the sensor to adopt a requested
# exposure, well under the capture path's MAX_SETTLE_FRAMES: a frame taken
# slightly off the request is fine here (the metering scales from the exposure
# the metadata reports, not from the request), whereas a screen that sits
# frozen for a dozen full-res captures is not.
_PREVIEW_SETTLE_FRAMES = 4
_PREVIEW_MIN_US        = 1_000  # exposure floor, matching the capture path
_PREVIEW_MAX_US        = 200_000  # ceiling: past this the preview is too slow

_VIEW_ZOOM_STEP      = 1.5      # one press of + or −
# One press of an arrow in view mode, as a fraction of the view box.  The
# 1px/10px button picks between them: it is a fine/coarse switch for panning
# and a literal pixel step for the edit nudge.  A pan of one image pixel would
# be invisible at every zoom the viewer can reach, which is why view mode
# scales with the view instead.
_VIEW_PAN_FINE       = 0.05     # with the step on 1px
_VIEW_PAN_COARSE     = 0.25     # with the step on 10px
_EDIT_MARGIN         = 0.5      # Edit zooms to the rectangle plus half its size
_EDIT_EDGE_TOL_PX    = 12       # canvas px within which a tap arms an edge
_EDIT_MIN_SIDE       = 16       # smallest ROI a Save will accept, image px

# Bounds for a reagent countdown entered on the Menu → Config screen.  Zero is
# excluded (a wait that expires the instant it starts is never what was meant)
# and the ceiling is one hour, well past the longest protocol step.
_CFG_MIN_SECS, _CFG_MAX_SECS = 1, 3600
# The step buttons on each config row, in the order they are laid out.
_CFG_STEPS = (-60, -10, 10, 60)

# Tunables (LED range, loop cadence, timeouts, reagent durations, rowid, the
# backlight pin) are rows in the database's `config` table and are read through
# config_repo — see the config.* calls throughout this module.


def _clamp_secs(secs: int) -> int:
    """Hold an entered reagent duration inside the allowed range."""
    return max(_CFG_MIN_SECS, min(_CFG_MAX_SECS, int(secs)))


def _clamp_us(us: float) -> int:
    """Hold a preview exposure inside the range the viewer will ask the sensor for."""
    return max(_PREVIEW_MIN_US, min(_PREVIEW_MAX_US, int(us)))


def _mmss(secs: int) -> str:
    """Seconds as m:ss, so the config screen shows what a duration means."""
    return f"{secs // 60}:{secs % 60:02d}"


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
        # _cap_mode is the Path radio: it selects the mode a Capture takes
        # during a *test sequence* (ReadPhase → request_capture).  The Menu →
        # Capture "Calibrate Exposures" button is calibration-only and does NOT
        # read it — the standalone run's mode is _cap_run_mode, fixed at start.
        self._cap_mode:     str = "normal"    # Path radio: "calibration" | "normal"
        self._cap_run_mode: str = "calibration"  # the in-flight standalone run's mode
        self._cap_group: str = "1"            # images per LED, '1'..'5'
        self._cap_view:  str | None = None    # None | "choice" | "progress" | "done"
        self._cap_progress: str = ""
        self._cap_result:   str = ""
        # The group saved before the choice screen opened, so CANCEL can restore it.
        self._cap_saved_on_open: str | None = None

        # Config screen state (Menu → Config): the reagent countdowns in
        # seconds, staged while the screen is open and written only by its OK.
        self._cfg_secs: dict[str, int] = {"r1": 0, "r2": 0}

        # Image viewer state (Menu → View Image, and later Read → View Sample).
        # _view_open is read by _camera_wanted(), so the shared camera is held
        # for as long as the screen is up; without it _maybe_release_camera()
        # would close the sensor on the very next main-loop pass.
        self._view_open      = False
        self._view_frame     = None    # the whole captured frame, numpy BGR
        self._view_state     = None    # image_view.ViewState over that frame
        self._view_rois:  dict = {}    # name -> rect.Rect, in image space
        self._view_selected: str | None = None   # one name, or None
        self._view_return_slot: Slot | None = None
        self._view_step      = 1       # edit nudge, image px: 1 or 10; also
                                       # picks the view-mode pan fraction
        self._view_status    = ""      # transient message for the name strip
        # The exposure the last preview settled on, in µs — the starting point
        # for the next one, so re-imaging normally converges on its first frame
        # instead of walking back from the stored calibration every time.
        self._view_exposure_us: int | None = None
        # Edit mode: a working copy of the selected rectangle, and which of its
        # sides (if any) the arrows move.  The working copy is what the screen
        # draws and what Save validates; the stored ROI is untouched until then,
        # so Cancel is free.
        self._view_mode      = "view"  # "view" | "edit"
        self._view_editing   = None    # rect.Rect working copy, or None
        self._view_armed: str | None = None   # left|right|top|bottom, or None

        # Recapture options dialog (opened by the viewer's Img button).
        self._recap_focus: str | None = None   # None | "auto" | "reread"
        self._recap_exp        = "sample"      # "sample" | "whole"
        self._recap_save_focus = False
        self._recap_save_exp   = False

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
        # The 3x2 option grid, laid out in layout.py: the remaining stub is
        # shown disabled (no handler wired) so the grid reads as a whole while
        # the screen behind it is still to come.  "View Image" is disabled
        # while a capture is in flight — the imaging tools share the camera and
        # LED bank with the capture path.
        self._pool.put("menu_capture",    "Capture",  on=self._menu_open_capture)
        self._pool.put("menu_stub1",      "View\nImage",
                       on=self._menu_open_image_view,
                       enabled=self.capture_available())
        self._pool.put("menu_defaults",   "Defaults", on=self._menu_open_defaults)
        self._pool.put("menu_stub2",      "stub2",    enabled=False)
        self._pool.put("menu_config",     "Config",   on=self._menu_open_config)
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

    def _menu_open_image_view(self) -> None:
        """
        Menu → View Image (Tool 1): the whole frame with every ROI drawn on.

        Exit returns to the Menu, not to the slot, so the menu's OK/CANCEL
        still get their say over anything staged before the viewer was opened.
        """
        self.open_image_view(slot=None)

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

    # ── Config screen (Menu → Config) ──────────────────────────────────────────
    # Custom reagent countdowns, entered on the instrument itself.  The two
    # durations are staged in self._cfg_secs while the screen is open and are
    # written (as new rows in the `config` table) only by its OK, so a mis-tap
    # costs nothing — CANCEL leaves the saved values alone.
    #
    # A saved duration wins over both the commissioned value and the simulation
    # profile, so it applies on real hardware too — which the shortened
    # simulation durations do not.  Restore supersedes it with the commissioned
    # row again; the edit stays in the table as history either way.

    def _menu_open_config(self) -> None:
        """Open the config screen, staging the durations now in force."""
        self._cfg_secs = {"r1": config.reagent1_secs(),
                          "r2": config.reagent2_secs()}
        self._render_config()

    def _render_config(self) -> None:
        if self._pool is None:
            return
        p = self._pool
        p.hide_all()
        # Top-corner chrome like the Menu and Capture screens.
        p.put("menu_cancel_btn", "CANCEL", on=self._cfg_cancel)
        p.put("cfg_title",       "Config")
        p.put("menu_ok_btn",     "OK",     on=self._cfg_ok)
        for row, n in (("r1", 1), ("r2", 2)):
            p.put(f"cfg_{row}_lbl", f"Reagent {n}")
            for step in _CFG_STEPS:
                p.put(self._cfg_step_widget(row, step), f"{step:+d}",
                      on=(lambda r=row, d=step: self._cfg_step(r, d)))
        p.put("cfg_r1_fld")
        p.put("cfg_r2_fld")
        p.put("cfg_hint")
        p.put("cfg_restore", "Restore", on=self._cfg_restore)
        self._cfg_paint_values(redraw=False)
        p.render(self._disp)
        self._rebuild_focus()

    @staticmethod
    def _cfg_step_widget(row: str, step: int) -> str:
        """Widget name of one step button, e.g. ('r1', -60) -> 'cfg_r1_m60'."""
        return f"cfg_{row}_{'m' if step < 0 else 'p'}{abs(step)}"

    def _cfg_paint_values(self, redraw: bool = True) -> None:
        """
        Write the staged durations into their fields and the hint line.

        `redraw` repaints just those three widgets — a step button changes
        nothing else on the screen, so it costs one small update instead of a
        full-screen render on every tap.
        """
        p = self._pool
        if p is None:
            return
        for row in ("r1", "r2"):
            p.set(f"cfg_{row}_fld", str(self._cfg_secs[row]))
        p.set("cfg_hint", f"seconds     R1 {_mmss(self._cfg_secs['r1'])}"
                          f"     R2 {_mmss(self._cfg_secs['r2'])}")
        if redraw:
            for name in ("cfg_r1_fld", "cfg_r2_fld", "cfg_hint"):
                p.redraw(self._disp, name)

    def _cfg_staged(self, row: str) -> int:
        """
        The row's duration as it now reads on screen, clamped to the allowed
        range.  The field is editable (a keyboard can type a duration straight
        in), so what it shows — not the last stepped value — is the answer;
        anything unparseable falls back to the staged value.
        """
        field = self._pool.get(f"cfg_{row}_fld") if self._pool else None
        try:
            return _clamp_secs(int(str(field.value).strip()))
        except (AttributeError, TypeError, ValueError):
            return self._cfg_secs[row]

    def _cfg_step(self, row: str, delta: int) -> None:
        self._cfg_secs[row] = _clamp_secs(self._cfg_staged(row) + delta)
        self._cfg_paint_values()

    def _cfg_restore(self) -> None:
        """Stage the commissioned durations (saved only if OK follows)."""
        self._cfg_secs = {"r1": int(config.default("protocol", "reagent1_secs")),
                          "r2": int(config.default("protocol", "reagent2_secs"))}
        self._cfg_paint_values()

    def _cfg_ok(self) -> None:
        """Save the staged durations, apply them, and return to the Menu."""
        for row, key in (("r1", "reagent1_secs"), ("r2", "reagent2_secs")):
            secs = self._cfg_staged(row)
            self._cfg_secs[row] = secs
            # Matching the commissioned value again means there is nothing
            # custom left to remember: restore rather than freeze today's
            # commissioned value into an operator row.
            if secs == config.default("protocol", key):
                config.restore("protocol", key)
            else:
                config.save("protocol", key, secs)
        self._apply_reagent_secs()
        self._render_menu()

    def _cfg_cancel(self) -> None:
        """Discard the staged durations and return to the Menu."""
        self._render_menu()

    def _apply_reagent_secs(self) -> None:
        """
        Rebuild the phase strategies so the new durations take effect at once.

        Phases hold no run state (slots do), so this is safe mid-run: a
        countdown already running lives on its slot and is untouched — only
        what the next START arms changes.
        """
        r1, r2 = config.reagent1_secs(), config.reagent2_secs()
        self._phases = build_phases(reagent1_secs=r1, reagent2_secs=r2)
        log.info("Arbiter: reagent countdowns set to %ds / %ds", r1, r2)

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
        # Nothing saved yet — the configured default (the config table,
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
        # This button does exactly one thing — calibrate exposures — regardless
        # of the Path radio, so its label is fixed.  (The Path radio selects the
        # mode for a Capture run during a test sequence, handled elsewhere.)
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
        """
        Launch the standalone "Calibrate Exposures" run.

        This always calibrates, independent of the Path radio (_cap_mode): the
        run's mode is pinned to calibration in _cap_run_mode so the progress
        title and the completion path (persist e*) can't be diverted by whatever
        Path was selected for test-sequence captures.
        """
        if not self.capture_available():
            self._cap_result = "Camera busy — try again later"
            self._render_capture_done()
            return
        self._cap_run_mode = "calibration"
        from capture_runner import CaptureConfig
        cfg = CaptureConfig(mode=self._cap_run_mode, exposure_group=self._cap_group,
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
                 self._cap_run_mode, self._cap_group)
        self._render_capture_progress()

    def _render_capture_progress(self) -> None:
        if self._pool is None:
            return
        self._cap_view = "progress"
        p = self._pool
        p.hide_all()
        p["cap_title"].rect = layout.CAP_TITLE       # full width (no corner buttons here)
        p.put("cap_title", "Calibrating" if self._cap_run_mode == "calibration"
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
        if self._cap_run_mode == "calibration":
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
        """
        True while anything on screen needs the shared camera.

        Two claims: a slot in a phase that will soon capture, and the image
        viewer, which reimages on demand.  The viewer *must* be counted here —
        it is not a phase, so without it _maybe_release_camera() would close
        the sensor on the very next main-loop pass and every reimage would pay
        the full open cost again.
        """
        return self._view_open or any(s.active_phase in _CAMERA_PHASES
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

    def preview_frame(self, region: str = "sample"):
        """
        Take a briefly-lit, metered frame for the image viewer.

        The white LED is switched on, given a short settle, the exposure is
        metered (see `_auto_expose`), and the LED goes off again.  The LED
        settle is deliberately far shorter than a capture run's
        LED_ON_WARMUP_S: this frame is for looking at, and for judging where
        the ROI rectangles fall, so colour accuracy is not required and the
        operator's wait matters more.

        Blocks the main loop for one to three frame times plus the settle.
        That is acceptable because it only ever runs from an explicit button
        press, and every alternative (threads, a state machine over the settle)
        buys nothing here.

        Args:
            region: "sample" (default, meters the sample ROI) or "whole"
                    (meters the entire frame) — see `_auto_expose`.

        Returns:
            (frame, error).  `frame` is a numpy BGR array of the full stream on
            success and None on failure, in which case `error` is a short
            message for the screen.
        """
        hw = self._ensure_camera()
        if hw is None:
            return None, "Camera unavailable"
        cam, leds = hw

        lit = False
        try:
            if leds is not None:
                leds.set_channel(_PREVIEW_LED_CH, True)
                lit = True
                time.sleep(_PREVIEW_SETTLE_S)
            rgb, exposure_us = self._auto_expose(cam, region=region)
        except Exception:
            log.exception("Arbiter: preview capture failed")
            return None, "Preview failed"
        finally:
            if lit:
                try:
                    leds.set_channel(_PREVIEW_LED_CH, False)
                except Exception:
                    log.exception("Arbiter: could not switch the preview LED off")

        if rgb is None or getattr(rgb, "ndim", 0) != 3:
            return None, "No image"
        log.info("Arbiter: preview frame %dx%d at %d us",
                 rgb.shape[1], rgb.shape[0], exposure_us)
        return rgb, ""

    def _auto_expose(self, cam, region: str = "sample"):
        """
        Capture the preview, metering the exposure on the sample ROI or the
        whole frame.

        Each pass sets an exposure, waits for the pipeline to actually adopt it
        (`capture_runner._set_and_settle` — without that wait the frame read
        back still carries the *previous* exposure, which is the older bug this
        replaces), then measures the chosen region. The measurement is the
        median of the brightest raw Bayer channel, and the next exposure is
        the linear scaling that would put that median on `_PREVIEW_TARGET`.
        Raw, not the ISP's 8-bit output, because only the raw values are
        linear in exposure — scaling against gamma-encoded pixels
        under-corrects and would need many more frames to converge.

        At most `_PREVIEW_AE_MAX_FRAMES` frames are taken, so a scene that
        cannot be brought into range (the LED failed, the cuvette is opaque)
        costs a bounded wait and returns the best frame it managed.

        Every pass is logged at INFO with the exposure in µs and what the
        region measured, so an over- or under-exposed preview can be
        diagnosed from the log alone.

        Also updates `_view_exposure_us`, the starting point for the next
        preview: the exposure that worked when the metering converged, and the
        correction it was about to try when it ran out of frames.  Either way
        the next preview starts from the best estimate this one reached.

        Args:
            cam:    The shared camera, already started.
            region: "sample" (default, meters the sample ROI alone) or "whole"
                    (meters the entire frame).

        Returns:
            (rgb, exposure_us) — the BGR frame and the exposure it was actually
            taken at, in microseconds.  `exposure_us` is what the frame used,
            which is not always what was asked for.
        """
        import capture_runner

        us = self._start_exposure_us()
        rgb, applied = None, us
        for i in range(_PREVIEW_AE_MAX_FRAMES):
            rgb, bayer, meta = capture_runner._set_and_settle(
                cam, us, max_frames=_PREVIEW_SETTLE_FRAMES)
            applied = int(meta.get("ExposureTime") or us)
            level = (self._meter_sample_level(bayer, cam) if region == "sample"
                     else self._meter_whole_frame_level(bayer, cam))
            if level is None:
                log.info("Arbiter: preview exposure %d us (sample not metered)",
                         applied)
                self._view_exposure_us = applied
                return rgb, applied

            error = abs(level - _PREVIEW_TARGET) / _PREVIEW_TARGET
            wanted = _clamp_us(applied * _PREVIEW_TARGET / max(level, 1.0))
            log.info("Arbiter: preview AE [%d/%d] %d us -> sample median %.0f "
                     "of %d (%+.0f%%)%s", i + 1, _PREVIEW_AE_MAX_FRAMES,
                     applied, level, _PREVIEW_TARGET,
                     100.0 * (level - _PREVIEW_TARGET) / _PREVIEW_TARGET,
                     "" if error <= _PREVIEW_AE_TOLERANCE
                     else f" -> retry at {wanted} us")
            if error <= _PREVIEW_AE_TOLERANCE or wanted == us:
                # `wanted == us` means the correction is pinned at a limit;
                # another frame would only ask for the same exposure again.
                self._view_exposure_us = applied
                return rgb, applied
            us = wanted

        log.warning("Arbiter: preview exposure did not converge in %d frames — "
                    "showing the frame taken at %d us; next preview starts at "
                    "%d us", _PREVIEW_AE_MAX_FRAMES, applied, us)
        self._view_exposure_us = us
        return rgb, applied

    def _start_exposure_us(self) -> int:
        """
        Where the preview's metering starts: the last settled preview exposure,
        else the calibrated white exposure, else a fixed fallback.

        The calibrated value is the middle rung of whatever active group the
        white LED has — a sensible opening guess because it was measured under
        this instrument's own LED, even though it was metered on `white` rather
        than on the sample.  Any failure falls back rather than propagating: a
        preview is never worth failing a screen over.
        """
        if self._view_exposure_us:
            return _clamp_us(self._view_exposure_us)
        try:
            for group in ("1", "3", "5"):
                us = exposure_repo.read_exposures(_PREVIEW_LED_NAME, group)
                if us:
                    return _clamp_us(us[len(us) // 2])
        except Exception:
            log.exception("Arbiter: could not read a calibrated preview exposure")
        return _PREVIEW_FALLBACK_US

    def _meter_sample_level(self, bayer, cam) -> "float | None":
        """
        Measure the sample ROI: the median of its brightest raw Bayer channel.

        The brightest channel rather than the mean of all four, for the same
        reason the capture path picks a target channel — a coloured sample
        leaves one channel near the floor, and metering on the average would
        drive the bright channel into clipping.

        Args:
            bayer: The raw (H, W) uint16 mosaic from the same capture.
            cam:   The camera, for its Bayer pattern.

        Returns:
            The median in 10-bit counts, or None when the sample cannot be
            measured (no sample ROI loaded, no raw stream, a rectangle that
            does not lie inside the mosaic) — the caller then keeps the
            exposure it has rather than guessing.
        """
        r = self._view_rois.get("sample")
        if r is None or bayer is None or getattr(bayer, "ndim", 0) != 2:
            return None
        try:
            import numpy as np
            import roi as roi_mod
            from bayer_cam import BayerCam

            lr = roi_mod.to_layer_rect(r)
            layers = BayerCam.extract_bayer_layers(
                bayer, getattr(cam, "bayer_pattern", "BGGR"))
            lh, lw = next(iter(layers.values())).shape
            if lr.w < 1 or lr.h < 1 or lr.x < 0 or lr.y < 0 \
                    or lr.x2() > lw or lr.y2() > lh:
                log.warning("Arbiter: sample ROI %s falls outside the %dx%d "
                            "Bayer layers — preview not metered",
                            r.xywh(), lw, lh)
                return None
            return max(float(np.median(layer[lr.xyslice()]))
                       for layer in layers.values())
        except Exception:
            log.exception("Arbiter: could not meter the sample ROI")
            return None

    def _meter_whole_frame_level(self, bayer, cam) -> "float | None":
        """
        Measure the whole frame: the median of its brightest raw Bayer channel.

        Same brightest-channel rationale as `_meter_sample_level`, just
        without cropping to any ROI — used when the operator asks the
        recapture dialog to meter on the whole image instead of the sample.

        Args:
            bayer: The raw (H, W) uint16 mosaic from the same capture.
            cam:   The camera, for its Bayer pattern.

        Returns:
            The median in 10-bit counts, or None when there is no raw stream
            to measure.
        """
        if bayer is None or getattr(bayer, "ndim", 0) != 2:
            return None
        try:
            import numpy as np
            from bayer_cam import BayerCam

            layers = BayerCam.extract_bayer_layers(
                bayer, getattr(cam, "bayer_pattern", "BGGR"))
            return max(float(np.median(layer)) for layer in layers.values())
        except Exception:
            log.exception("Arbiter: could not meter the whole frame")
            return None

    # ── Image viewer screen (Menu → View Image) ────────────────────────────────
    # A screen of its own, like Menu and Config: _focused is None while it is
    # up, it draws no header or footer, and the whole 320x240 belongs to it.
    #
    # The full frame is held as a numpy array for the life of the screen and
    # re-sliced on every repaint (see image_view.compose), which is what lets a
    # ROI be *enlarged* during editing — the pixels behind the growth are
    # already there, out to the frame edge.

    def open_image_view(self, slot: Slot | None = None, focus_roi: str | None = None):
        """
        Open the image viewer, taking one preview frame first.

        Refused while a capture is in flight: the imaging tools share the
        camera and the LED bank with the capture path, so they must be
        unavailable from the moment CAPTURE is pressed until the last frame is
        stored.  `capture_available()` is the single guard covering both the
        per-sample Read capture and a standalone Menu capture.

        Args:
            slot:      Where Exit returns to; None returns to the Menu.
            focus_roi: A ROI name to open zoomed to (Tool 2's entry point);
                       None opens on the whole frame.
        """
        if not self.capture_available():
            log.info("Arbiter: image view refused — a capture is in flight")
            self._toast("Capture running")
            return
        self._view_return_slot = slot
        self._view_open = True
        self._view_selected = focus_roi
        self._view_status = ""
        self._view_mode = "view"
        self._view_editing = None
        self._view_armed = None
        self._focused = None
        self._view_rois = self._load_view_rois()
        self._view_reimage(focus_roi=focus_roi)

    def _load_view_rois(self) -> dict:
        """
        Read the ROIs in force, for drawing over the preview.

        Failure is survivable: an empty dict just means no rectangles are
        drawn, and looking at the raw image is still useful.
        """
        try:
            import roi
            size = getattr(self._camera, "config_size", None) or (None, None)
            return roi.load_active(size[0], size[1],
                                   getattr(self._camera, "model", None))
        except Exception:
            log.exception("Arbiter: could not load ROIs for the image view")
            return {}

    def _view_reimage(self, focus_roi: str | None = None) -> None:
        """Take a fresh preview frame and rebuild the view over it."""
        if self._pool is not None:
            self._pool.put("img_name", "Imaging...")
            self._pool.redraw(self._disp, "img_name")
        frame, err = self.preview_frame()
        self._view_status = err
        if frame is None:
            self._view_frame = None
            self._view_state = None
        else:
            self._view_install_frame(frame, focus_roi=focus_roi)
        self._render_image_view()

    def _view_install_frame(self, frame, focus_roi: str | None = None) -> None:
        """
        Adopt a newly captured frame as the viewer's current image.

        Shared by `_view_reimage` and the recapture dialog's Capture button.
        The view is rebuilt only when there is none yet, a ROI to focus was
        asked for, or the stream size changed under us — reimaging must not
        throw away where you were looking, since the whole point of Img
        mid-edit is to see the effect of a moved edge under a fresh exposure.
        """
        import image_view
        prev = self._view_state
        self._view_frame = frame
        fh, fw = frame.shape[:2]
        if (prev is None or focus_roi is not None
                or (prev.frame_w, prev.frame_h) != (fw, fh)):
            canvas = layout.IMG_CANVAS
            self._view_state = image_view.ViewState(fw, fh, canvas.w, canvas.h)
            target = focus_roi or self._view_selected
            if target and target in self._view_rois:
                self._view_state.fit_to(self._view_rois[target])

    def _render_image_view(self) -> None:
        """Build (or rebuild) the whole viewer screen."""
        if self._pool is None:
            return
        self._pool.hide_all()
        self._pool.put("img_canvas", on=self._view_canvas_press)
        self._pool.put("img_zoom_in",  "+",    on=self._view_zoom_in)
        self._pool.put("img_up",       "▲",    on=lambda: self._view_arrow(0, -1))
        self._pool.put("img_zoom_out", "−",    on=self._view_zoom_out)
        self._pool.put("img_left",     "◀",    on=lambda: self._view_arrow(-1, 0))
        self._pool.put("img_fit",      "Fit",  on=self._view_fit)
        self._pool.put("img_right",    "▶",    on=lambda: self._view_arrow(1, 0))
        self._pool.put("img_step",     f"{self._view_step}px",
                       on=self._view_toggle_step)
        self._pool.put("img_down",     "▼",    on=lambda: self._view_arrow(0, 1))
        self._pool.put("img_reimage",  "Img",  on=self._recap_open)
        self._pool.put("img_name",     self._view_name_text())
        # Edit / Save / Cancel are a mode pair with Exit: editing offers Save
        # and Cancel and withholds both Edit and Exit, so there is no way to
        # leave the screen with an unsaved working copy and therefore no
        # lost-work modal to write.
        editing = self._view_mode == "edit"
        self._pool.put("img_edit",   "Edit",   on=self._view_edit,
                       enabled=not editing and self._view_selected is not None
                               and self._view_frame is not None)
        self._pool.put("img_save",   "Save",   on=self._view_save,
                       enabled=editing)
        self._pool.put("img_cancel", "Cancel", on=self._view_cancel,
                       enabled=editing)
        self._pool.put("img_exit",   "Exit",   on=self._view_exit,
                       enabled=not editing)
        self._paint_canvas()
        self._pool.render(self._disp)
        self._rebuild_focus()

    # ── Recapture options dialog (viewer's Img button) ─────────────────────────
    # A second screen swapped in over the viewer, following the same ad hoc
    # "hide_all + put() everything + render" pattern as _render_capture_choice
    # and friends — not a ModalPhase, which is bound to a Slot the viewer
    # doesn't have. Cancel returns to the viewer untouched (no camera calls);
    # Capture applies the chosen focus/exposure actions, takes one frame, and
    # installs it into the still-open viewer.

    def _recap_open(self) -> None:
        if not self.capture_available():
            self._toast("Capture running")
            return
        self._recap_render()

    def _recap_render(self) -> None:
        if self._pool is None:
            return
        from palette import color
        sel, unsel = color("start_green"), color("btn_gray")
        p = self._pool
        p.hide_all()
        p.put("menu_cancel_btn", "Cancel", on=self._recap_cancel)
        p.put("menu_title",      "Recapture")
        p.put("menu_ok_btn",     "Capture", on=self._recap_capture)
        p.put("recap_focus_lbl", "Focus")
        p.put("recap_focus_auto", "Auto Focus",
              on=lambda: self._recap_set_focus("auto"))
        p["recap_focus_auto"].color = sel if self._recap_focus == "auto" else unsel
        p.put("recap_focus_cfg", "Reread Cfg",
              on=lambda: self._recap_set_focus("reread"))
        p["recap_focus_cfg"].color = sel if self._recap_focus == "reread" else unsel
        p.put("recap_exp_lbl", "Exposure")
        p.put("recap_exp_sample", "Sample Rect",
              on=lambda: self._recap_set_exp("sample"))
        p["recap_exp_sample"].color = sel if self._recap_exp == "sample" else unsel
        p.put("recap_exp_whole", "Whole Image",
              on=lambda: self._recap_set_exp("whole"))
        p["recap_exp_whole"].color = sel if self._recap_exp == "whole" else unsel
        p.put("recap_save_focus", "Save Focus", on=self._recap_toggle_save_focus)
        p["recap_save_focus"].color = sel if self._recap_save_focus else unsel
        p.put("recap_save_exp", "Save Exposure", on=self._recap_toggle_save_exp)
        p["recap_save_exp"].color = sel if self._recap_save_exp else unsel
        p.render(self._disp)
        self._rebuild_focus()

    def _recap_set_focus(self, mode: str) -> None:
        # Tri-state: tapping the already-selected option clears it to None
        # (neither checked = leave focus untouched), unlike an ordinary radio.
        self._recap_focus = None if self._recap_focus == mode else mode
        self._recap_render()

    def _recap_set_exp(self, region: str) -> None:
        self._recap_exp = region
        self._recap_render()

    def _recap_toggle_save_focus(self) -> None:
        self._recap_save_focus = not self._recap_save_focus
        self._recap_render()

    def _recap_toggle_save_exp(self) -> None:
        self._recap_save_exp = not self._recap_save_exp
        self._recap_render()

    def _recap_cancel(self) -> None:
        """Discard the pending choices and return to the viewer untouched."""
        self._render_image_view()

    def _recap_capture(self) -> None:
        """Apply the chosen focus/exposure actions and install a fresh frame."""
        hw = self._ensure_camera()
        if hw is None:
            log.error("Arbiter: recapture — camera unavailable")
            self._render_image_view()
            return
        cam, leds = hw

        if self._recap_focus == "auto":
            cam.focus(force=True, leds=leds)
        elif self._recap_focus == "reread":
            cam.reread_focus_cal()
        # else: neither checked — leave focus exactly as it is.

        frame, err = self.preview_frame(region=self._recap_exp)
        self._view_status = err

        diopter = cam.lens_position
        log.info("Arbiter: recapture — focus=%s diopter=%s exposure=%s us "
                 "region=%s", self._recap_focus or "unchanged",
                 f"{diopter:.4f}" if diopter is not None else "n/a",
                 self._view_exposure_us, self._recap_exp)

        if self._recap_save_focus and diopter is not None:
            config.save("capture", "default_focus_diopter", diopter)
        if self._recap_save_exp and self._view_exposure_us is not None:
            config.save("capture", "inspection_exposure_us",
                        self._view_exposure_us)

        if frame is None:
            self._view_frame = None
            self._view_state = None
        else:
            self._view_install_frame(frame)
        self._render_image_view()

    def _paint_canvas(self) -> None:
        """
        Compose the canvas from the frame, the view box and the ROIs.

        Four colours, as specified: `roi_idle` for a rectangle that is merely
        present, `roi_selected` for the selected one, `roi_edit` for the
        working copy under edit, and `roi_edge` for its armed side.

        Overlays are drawn in order, so the interesting one goes last and wins
        wherever two rectangles overlap.  The armed side is passed as a
        *degenerate* rectangle — zero width for a vertical edge, zero height
        for a horizontal one — which `compose` strokes as a line without
        needing to know anything about edit mode.
        """
        if self._view_frame is None or self._view_state is None:
            self._pool["img_canvas"].set_image(None)
            return
        import image_view
        from palette import rgb888

        editing = self._view_editing
        overlays = []
        for name, r in sorted(self._view_rois.items()):
            if editing is not None and name == editing.name:
                continue                      # the working copy is drawn instead
            colour = ("roi_selected"
                      if editing is None and name == self._view_selected
                      else "roi_idle")
            overlays.append((r, rgb888(colour)))
        # Selected last in view mode, the working copy last in edit mode.
        overlays.sort(key=lambda pair: pair[0].name == self._view_selected)
        if editing is not None:
            overlays.append((editing, rgb888("roi_edit")))
            edge = self._armed_edge_rect()
            if edge is not None:
                overlays.append((edge, rgb888("roi_edge")))

        self._pool["img_canvas"].set_image(
            image_view.compose(self._view_frame, self._view_state, overlays))

    def _armed_edge_rect(self):
        """
        The armed side as a zero-thickness Rect, or None when nothing is armed.

        `compose` draws a rectangle's outline just outside its bounds, so a
        zero-width rect comes out as a two-pixel vertical line on the edge —
        which is exactly what marking one side wants, with no special case in
        the compositor.
        """
        r, side = self._view_editing, self._view_armed
        if r is None or side is None:
            return None
        from rect import Rect
        return {"left":   Rect(r.x,    r.y,    0,   r.h, name=r.name),
                "right":  Rect(r.x2(), r.y,    0,   r.h, name=r.name),
                "top":    Rect(r.x,    r.y,    r.w, 0,   name=r.name),
                "bottom": Rect(r.x,    r.y2(), r.w, 0,   name=r.name)}[side]

    def _repaint_canvas(self) -> None:
        """Recompose and blit just the canvas — the common case for pan/zoom."""
        if self._pool is None:
            return
        self._paint_canvas()
        self._pool.redraw(self._disp, "img_canvas")
        self._pool.put("img_name", self._view_name_text())
        self._pool.redraw(self._disp, "img_name")
        self._sync_edit_button()

    def _sync_edit_button(self) -> None:
        """
        Keep Edit's enabled state in step with the selection.

        Selecting a rectangle from the canvas repaints only the canvas and the
        name strip, so without this the button keeps whatever state the last
        *full* render left it in.  Opening from the Menu selects nothing, which
        disabled Edit, and `Pool.handle_touch` skips disabled widgets — so
        pressing Edit after tapping a rectangle did nothing at all.

        Only touched when it changes: repainting a button that already looks
        right costs a needless SPI blit on every pan.
        """
        if self._pool is None:
            return
        w = self._pool.get("img_edit")
        if w is None or not w.visible:
            return
        want = (self._view_mode != "edit"
                and self._view_selected is not None
                and self._view_frame is not None)
        if w.enabled == want:
            return
        self._pool.put("img_edit", "Edit", on=self._view_edit, enabled=want)
        self._pool.redraw(self._disp, "img_edit")
        self._refresh_focusables()

    def _refresh_focusables(self) -> None:
        """
        Rebuild the tab list after a widget's enabled state changed, keeping
        whatever is focused focused.

        `_rebuild_focus()` would do the rebuild but also drags the focus ring
        back to the first control, which is wrong for a change the user did not
        make with the keyboard.
        """
        current = self._focused_widget()
        self._focusables = self._pool.focusable_widgets()
        self._focus_idx = (self._focusables.index(current)
                           if current in self._focusables else -1)

    def _view_name_text(self) -> str:
        """
        What the strip between the two button grids shows.

        In edit mode it names the armed side rather than the ROI — while an
        edge is armed that is the thing the arrows act on, and the ROI's name
        is already known from having selected it.
        """
        if self._view_status:
            return self._view_status
        if self._view_editing is not None:
            r = self._view_editing
            head = self._view_armed or r.name
            return f"{head} {r.w}x{r.h}"
        if self._view_selected:
            r = self._view_rois.get(self._view_selected)
            if r is not None:
                return f"{self._view_selected} {r.w}x{r.h}"
            return self._view_selected
        return f"x{self._view_state.scale:.2f}" if self._view_state else ""

    # ── Viewer controls ────────────────────────────────────────────────────────

    def _view_zoom_in(self) -> None:
        self._view_zoom(_VIEW_ZOOM_STEP)

    def _view_zoom_out(self) -> None:
        self._view_zoom(1.0 / _VIEW_ZOOM_STEP)

    def _view_zoom(self, factor: float) -> None:
        if self._view_state is None:
            return
        self._view_status = ""
        self._view_state.zoom(factor)
        self._repaint_canvas()

    def _view_arrow(self, sx: int, sy: int) -> None:
        """
        The arrows do double duty: pan in view mode, nudge in edit mode.

        In edit mode they move the armed side if one is armed, and the whole
        rectangle if none is — the rule as specified, and it means the same
        four buttons cover both kinds of adjustment without a mode button.

        In view mode they pan by a fraction of what is on screen, so a press
        moves the same *visible* distance at every zoom.  The 1px/10px button
        picks the fraction (fine or coarse); it does not mean image pixels
        here, because one image pixel is a fraction of a canvas pixel at fit
        zoom and would look like a dead button.
        """
        if self._view_state is None:
            return
        self._view_status = ""
        if self._view_mode == "edit":
            self._nudge(sx, sy)
        else:
            v = self._view_state
            frac = _VIEW_PAN_FINE if self._view_step == 1 else _VIEW_PAN_COARSE
            v.pan(sx * v.w * frac, sy * v.h * frac)
        self._repaint_canvas()

    def _nudge(self, sx: int, sy: int) -> None:
        """
        Move the armed side, or the whole rectangle when nothing is armed.

        Steps by `_view_step` image pixels (the 1px/10px toggle).  An arrow
        perpendicular to the armed side does nothing: with the left edge armed,
        up and down have no meaning, and moving the whole rectangle instead
        would be a surprise.

        The result is clamped to the frame and to a minimum side, so a rectangle
        can neither be pushed off the image nor collapsed to nothing.  The view
        then follows the armed edge, keeping the thing being moved under the
        eye.
        """
        from rect import Rect
        r, side, d = self._view_editing, self._view_armed, self._view_step
        if r is None:
            return
        dx, dy = sx * d, sy * d
        x, y, w, h = r.xywh()

        if side is None:
            x, y = x + dx, y + dy
        elif side == "left":
            x, w = x + dx, w - dx
        elif side == "right":
            w = w + dx
        elif side == "top":
            y, h = y + dy, h - dy
        elif side == "bottom":
            h = h + dy

        # Minimum size first, so shrinking an edge past the far one pins it
        # rather than inverting the rectangle.
        if w < _EDIT_MIN_SIDE:
            if side == "left":
                x -= _EDIT_MIN_SIDE - w
            w = _EDIT_MIN_SIDE
        if h < _EDIT_MIN_SIDE:
            if side == "top":
                y -= _EDIT_MIN_SIDE - h
            h = _EDIT_MIN_SIDE
        fh, fw = self._view_frame.shape[:2]
        w, h = min(w, fw), min(h, fh)
        x = max(0, min(x, fw - w))
        y = max(0, min(y, fh - h))

        self._view_editing = Rect(x, y, w, h, name=r.name, color=r.color)
        self._view_editing.role = getattr(r, "role", None)
        if side is not None:
            self._view_state.centre_on_edge(self._view_editing, side)
        else:
            self._view_state.centre_on_rect(self._view_editing)

    def _view_fit(self) -> None:
        if self._view_state is None:
            return
        self._view_status = ""
        self._view_state.fit()
        self._repaint_canvas()

    def _view_toggle_step(self) -> None:
        """
        Toggle the arrow step: fine or coarse.

        It means two things, one per mode — the edit nudge in image pixels
        (1 or 10, which is what the label states), and the view-mode pan
        fraction (`_VIEW_PAN_FINE` or `_VIEW_PAN_COARSE`).
        """
        self._view_step = 10 if self._view_step == 1 else 1
        log.debug("Arbiter: viewer step -> %dpx (pan %.0f%% of the view)",
                  self._view_step,
                  100 * (_VIEW_PAN_FINE if self._view_step == 1
                         else _VIEW_PAN_COARSE))
        if self._pool is not None:
            self._pool.put("img_step", f"{self._view_step}px",
                           on=self._view_toggle_step)
            self._pool.redraw(self._disp, "img_step")

    def _view_canvas_press(self, cx: int, cy: int) -> None:
        """
        Select a ROI from a tap on the canvas.

        The tap is unprojected into image space and every rectangle containing
        that point becomes a candidate.  When more than one does — `bound`
        contains everything, and zoomed out the rectangles are only a few
        screen pixels apart — the tap is genuinely ambiguous, so rather than
        guessing it simply picks one, preferring one that is *not* currently
        selected.  Tapping the same spot again therefore moves on to the next.

        The rule, given the candidates:
          1. none              -> deselect
          2. one, already selected -> deselect it (so a lone rectangle can be
             cleared by touch, which was the original toggle behaviour)
          3. otherwise         -> order by area, smallest first (ties by name),
             start scanning just *after* the current selection, wrap, and take
             the first.  That is the first candidate not currently selected,
             and it advances by one on each repeat tap.

        Scanning from after the selection rather than from the start is what
        makes three or more overlapping rectangles cycle through all of them
        instead of bouncing between the first two.  With two it reduces exactly
        to a toggle: A -> B -> A -> B.
        """
        if self._view_state is None:
            return
        import image_view
        ix, iy = image_view.canvas_to_image(self._view_state, cx, cy)

        if self._view_mode == "edit":
            self._arm_side_at(ix, iy)
            return

        candidates = sorted((r for r in self._view_rois.values()
                             if r.contains(ix, iy)),
                            key=lambda r: (r.area(), r.name))
        if not candidates:
            self._view_selected = None
        elif len(candidates) == 1 and candidates[0].name == self._view_selected:
            self._view_selected = None
        else:
            names = [r.name for r in candidates]
            start = names.index(self._view_selected) + 1 if self._view_selected in names else 0
            self._view_selected = names[start % len(names)]

        self._view_status = ""
        log.debug("Arbiter: canvas tap (%d,%d) -> image (%.0f,%.0f) -> %r "
                  "of candidates %s", cx, cy, ix, iy, self._view_selected,
                  [r.name for r in candidates])
        self._repaint_canvas()

    def _arm_side_at(self, ix: float, iy: float) -> None:
        """
        Arm (or disarm) a side of the rectangle being edited, from a tap.

        A tap within `_EDIT_EDGE_TOL_PX` *canvas* pixels of a side arms it;
        tapping the armed side again, or anywhere else, disarms.  The tolerance
        is a screen band converted to image pixels, so it stays a comfortable
        finger's width at every zoom instead of shrinking with the rectangle.

        Pressing Edit auto-zooms to the rectangle, which is what makes this
        workable: at fit zoom the whole sample ROI is about six canvas pixels
        wide and every tap would be within tolerance of every edge.
        """
        r = self._view_editing
        if r is None or self._view_state is None:
            return
        tol = _EDIT_EDGE_TOL_PX / self._view_state.scale
        side = None
        if (r.x - tol <= ix <= r.x2() + tol and r.y - tol <= iy <= r.y2() + tol):
            dist = {"left":   abs(ix - r.x),   "right":  abs(ix - r.x2()),
                    "top":    abs(iy - r.y),   "bottom": abs(iy - r.y2())}
            nearest = min(dist, key=dist.get)
            if dist[nearest] <= tol:
                side = nearest
        self._view_armed = None if side == self._view_armed else side
        self._view_status = ""
        # Follow whatever is now in play: the armed edge, or the whole
        # rectangle when nothing is.  Without the second half, disarming would
        # leave the view parked on the old edge with the opposite one off
        # canvas, and the only way back would be to zoom out.
        if self._view_armed is not None:
            self._view_state.centre_on_edge(r, self._view_armed)
        else:
            self._view_state.centre_on_rect(r)
        log.debug("Arbiter: edit tap (%.0f,%.0f) tol=%.1f -> armed %r",
                  ix, iy, tol, self._view_armed)
        self._repaint_canvas()

    # ── Edit mode ──────────────────────────────────────────────────────────────

    def _view_edit(self) -> None:
        """
        Start editing the selected rectangle.

        Takes a working copy — the stored ROI is not touched until Save — and
        zooms to it plus half its size again, which is what makes the edges
        tappable.  Exit is withheld until Save or Cancel, so there is no way to
        walk away from an unsaved edit.
        """
        name = self._view_selected
        r = self._view_rois.get(name) if name else None
        if r is None or self._view_state is None:
            return
        from rect import Rect
        self._view_editing = Rect(r.x, r.y, r.w, r.h, name=name, color=r.color)
        self._view_editing.role = getattr(r, "role", None)
        self._view_armed = None
        self._view_mode = "edit"
        self._view_status = ""
        self._view_state.fit_to(self._view_editing, _EDIT_MARGIN)
        log.info("Arbiter: editing ROI %r at %s", name, r.xywh())
        self._render_image_view()

    def _view_save(self) -> None:
        """
        Validate the working copy and write it, superseding the stored ROI.

        Validation, in order:
          1. `snap_even` — the one that fails silently if skipped.  An odd
             coordinate shifts the Bayer phase, so which of B/Gb/Gr/R is being
             sampled changes without anything looking wrong.
          2. Inside the frame.
          3. At least `_EDIT_MIN_SIDE` on each side.
          4. `sample` still inside `bound`, whichever of the two was edited —
             the archive stores only the bound crop, so a sample outside it
             could not be re-measured.

        A rejected save leaves edit mode running with the reason in the name
        strip, so the edit can be corrected rather than lost.
        """
        r = self._view_editing
        if r is None:
            return
        import roi
        snapped = roi.snap_even(r)
        fh, fw = self._view_frame.shape[:2]

        if snapped.w < _EDIT_MIN_SIDE or snapped.h < _EDIT_MIN_SIDE:
            return self._view_reject(f"min {_EDIT_MIN_SIDE}px")
        if snapped.x < 0 or snapped.y < 0 or snapped.x2() > fw or snapped.y2() > fh:
            return self._view_reject("outside frame")

        proposed = dict(self._view_rois)
        proposed[snapped.name] = snapped
        bound, sample = proposed.get("bound"), proposed.get("sample")
        if bound is not None and sample is not None \
                and not roi.contains_rect(bound, sample):
            return self._view_reject("sample outside bound")

        rowid = roi.save(snapped.name, snapped, remark="edited on the instrument")
        if not rowid:
            return self._view_reject("save failed")
        log.info("Arbiter: saved ROI %r as %s (rowid %s)",
                 snapped.name, snapped.xywh(), rowid)
        self._view_rois = self._load_view_rois()
        self._view_end_edit(f"saved {snapped.w}x{snapped.h}")

    def _view_reject(self, why: str) -> None:
        """Refuse a Save, keeping edit mode running so the edit can be fixed."""
        log.info("Arbiter: save refused — %s (%s)", why,
                 self._view_editing.xywh() if self._view_editing else "?")
        self._view_status = why
        self._repaint_canvas()

    def _view_cancel(self) -> None:
        """Discard the working copy and return to view mode."""
        log.info("Arbiter: edit cancelled")
        self._view_end_edit("cancelled")

    def _view_end_edit(self, status: str) -> None:
        """Leave edit mode, keeping the ROI selected so Edit can be pressed again."""
        self._view_editing = None
        self._view_armed = None
        self._view_mode = "view"
        self._view_status = status
        self._render_image_view()

    def _view_exit(self) -> None:
        """Close the viewer, release the frame, and return where we came from."""
        if self._view_mode == "edit":
            return                    # Exit is disabled while editing
        self._view_open = False
        self._view_frame = None          # ~9 MB; do not hold it past the screen
        self._view_state = None
        self._view_rois = {}
        self._view_selected = None
        self._view_editing = None
        self._view_armed = None
        self._view_mode = "view"
        slot = self._view_return_slot
        self._view_return_slot = None
        if slot is not None:
            self.focus(slot)
        else:
            self._render_menu()

    def _toast(self, message: str) -> None:
        """Show a one-line message where the current screen has room for it."""
        if self._pool is None:
            return
        for name in ("img_name", "menu_title", "cap_progress"):
            w = self._pool.get(name)
            if w is not None and w.visible:
                self._pool.put(name, message)
                self._pool.redraw(self._disp, name)
                return
        log.info("Arbiter: %s", message)

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
        def __init__(self): self.images = []
        def draw_image(self, image, x=0, y=0):
            self.images.append((image.size, x, y))

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
    shutil.copy(Path(_db.DBNAME), _tmpdb)     # _db.DBNAME is the live DB
    _db.DBNAME = str(_tmpdb)
    # Settings are rows in that database now, so the copy also isolates the
    # Config screen's writes below — but the cache was filled from the live DB
    # during construction, so it has to be refilled from the copy.
    config.load(reload=True)
    config.seed_defaults()                    # in case the copy predates `config`
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
    check("button always Calibrate", arb._pool["cap_start"].label, "Calibrate\nExposures")
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

    print("== capture screen: Calibrate button persists e*, even with Path=Normal ==")
    _db.write_one("DELETE FROM exposure")
    cap_cal = _FakeCapture(steps=2)
    cap_cal.runner.results = [_R("amber", 30000), _R("blue", 20000)]
    arb._capture_factory = lambda: cap_cal
    arb._menu_open_capture()
    arb._cap_set_mode("normal")             # Path = Normal — the button must ignore it
    arb._cap_set_group(3)
    check("Path radio is normal", arb._cap_mode, "normal")
    check("button says Calibrate", arb._pool["cap_start"].label, "Calibrate\nExposures")
    arb._cap_start()
    check("run pinned to calibration", arb._cap_run_mode, "calibration")
    check("progress title Calibrating", arb._pool["cap_title"].text, "Calibrating")
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

    print("== capture screen: Path radio survives a Calibrate run (test-seq mode) ==")
    arb._menu_open_capture()
    check("reopen resets Path to normal", arb._cap_mode, "normal")
    arb._cap_set_mode("calibration")        # operator sets the test-sequence Path
    check("Path now calibration", arb._cap_mode, "calibration")
    cap_x = _FakeCapture(steps=1)
    arb._capture_factory = lambda: cap_x
    arb._cap_start()
    for _ in range(3):
        arb.pump_capture()
    check("Path unchanged by the run", arb._cap_mode, "calibration")

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

    # ── Menu grid + Config screen ────────────────────────────────────────────
    # The Config screen writes to the `config` table, which is already the
    # throwaway copy set up above, so the live settings are untouched.

    print("== menu: six options in a 3x2 grid ==")
    arb.open_menu(A)
    for _name in ("menu_capture", "menu_stub1", "menu_defaults",
                  "menu_stub2", "menu_config", "menu_power"):
        check(f"{_name} shown", arb._pool[_name].visible, True)
    # stub1 became "View Image" in step 6; stub2 is the last inert one.
    check("stub1 is View Image", arb._pool["menu_stub1"].label, "View\nImage")
    check("stub2 inert",   arb._pool["menu_stub2"].enabled, False)

    _file_r1 = int(config.default("protocol", "reagent1_secs"))
    _file_r2 = int(config.default("protocol", "reagent2_secs"))

    print("== config screen: steps stage a duration; CANCEL discards it ==")
    arb._menu_open_config()
    check("stages the live durations", arb._cfg_secs["r1"], config.reagent1_secs())
    check("field shows the seconds",   arb._pool["cfg_r1_fld"].value, str(_file_r1))
    arb._pool["cfg_r1_p60"].on_press()
    check("+60 stepped",       arb._cfg_secs["r1"], _file_r1 + 60)
    arb._cfg_cancel()
    check("CANCEL saved nothing", config.reagent1_secs(), _file_r1)

    print("== config screen: OK saves the custom wait and applies it ==")
    arb._menu_open_config()
    arb._pool["cfg_r1_m10"].on_press()
    arb._cfg_ok()
    # Clamped, not _file_r1 - 10: the configured wait may itself be under 10 s
    # (it is, on a development instrument), and -10 then floors at the minimum.
    _stepped_r1 = _clamp_secs(_file_r1 - 10)
    check("saved",             config.reagent1_secs(), _stepped_r1)
    check("phases rebuilt",    arb._phases["reagent1"].secs, _stepped_r1)
    check("reagent2 untouched", config.reagent2_secs(), _file_r2)

    print("== config screen: a typed duration is honoured and clamped ==")
    arb._menu_open_config()
    arb._pool.set("cfg_r2_fld", "45")          # as a keyboard would leave it
    arb._cfg_ok()
    check("typed value saved", config.reagent2_secs(), 45)
    arb._menu_open_config()
    arb._pool.set("cfg_r1_fld", "99999")
    check("clamped to the max", arb._cfg_staged("r1"), _CFG_MAX_SECS)
    arb._pool.set("cfg_r1_fld", "0")
    check("clamped to the min", arb._cfg_staged("r1"), _CFG_MIN_SECS)
    arb._pool.set("cfg_r1_fld", "abc")
    check("unparseable keeps the staged value",
          arb._cfg_staged("r1"), arb._cfg_secs["r1"])
    arb._cfg_cancel()

    print("== config screen: Restore returns to the commissioned durations ==")
    arb._menu_open_config()
    arb._cfg_restore()
    check("staged from the commissioned row", arb._cfg_secs["r2"], _file_r2)
    arb._cfg_ok()
    check("reagent1 back to commissioned", config.reagent1_secs(), _file_r1)
    check("reagent2 back to commissioned", config.reagent2_secs(), _file_r2)
    check("no overrides left behind", config.overrides(), [])

    # ── Image viewer (Tool 1) ─────────────────────────────────────────────────
    print("== image viewer: camera guard ==")
    import numpy as _np
    import layout as _L

    class _PreviewCam:
        """
        A camera that returns a recognisable synthetic frame.

        Its raw level rises with the exposure and clips at 10-bit full scale,
        so the viewer's sample metering has something real to converge on: at
        the 40 ms fallback the sample reads clipped, exactly the overexposed
        preview the metering exists to fix.
        """
        config_size = (2304, 1296)
        model = "imx708"
        bayer_pattern = "RGGB"
        COUNTS_PER_US = 0.05                          # 10-bit counts per µs
        def __init__(self):
            self.stopped = False
            self.exposures = []
            self.frames = 0
            self.exposure_us = 0
        def setup_camera(self): pass
        def set_exposure(self, us):
            self.exposure_us = int(us); self.exposures.append(int(us))
        def sample_level(self):
            return min(1023, int(self.exposure_us * self.COUNTS_PER_US))
        def capture_frame(self):
            self.frames += 1
            f = _np.zeros((1296, 2304, 3), dtype=_np.uint8)
            f[790:858, 1150:1218] = (0, 200, 250)     # BGR: the sample, orange
            bayer = _np.zeros((1296, 2304), _np.uint16)
            bayer[790:858, 1150:1218] = self.sample_level()
            return f, bayer, {"ExposureTime": self.exposure_us}
        def stop(self): self.stopped = True

    class _PreviewLeds:
        def __init__(self): self.history = []; self.closed = False
        def set_channel(self, ch, on): self.history.append((ch, on))
        def close(self): self.closed = True

    arb._release_camera()
    pcam, pleds = _PreviewCam(), _PreviewLeds()
    arb._ensure_camera = lambda: (setattr(arb, "_camera", pcam),
                                  setattr(arb, "_leds", pleds),
                                  (pcam, pleds))[-1]
    A.active_phase = "entry"
    B.active_phase = "entry"
    check("no camera wanted while idle", arb._camera_wanted(), False)
    arb._view_open = True
    check("viewer alone keeps the camera", arb._camera_wanted(), True)
    arb._maybe_release_camera()
    check("and _maybe_release respects it", arb._camera_wanted(), True)
    arb._view_open = False

    print("== image viewer: Menu → View Image opens on the whole frame ==")
    arb.open_menu(A)
    check("menu stub1 is now View Image",
          arb._pool["menu_stub1"].label, "View\nImage")
    check("enabled while no capture runs", arb._pool["menu_stub1"].enabled, True)
    arb.on_touch(_L.MENU_STUB1.x + 5, _L.MENU_STUB1.y + 5)
    check("viewer open",            arb._view_open, True)
    check("no slot has focus",      arb._focused is None, True)
    check("frame captured",         arb._view_frame.shape, (1296, 2304, 3))
    check("metering is bounded",    pcam.frames <= _PREVIEW_AE_MAX_FRAMES, True)
    check("white LED on then off",  pleds.history[-2:],
          [(_PREVIEW_LED_CH, True), (_PREVIEW_LED_CH, False)])
    check("ROIs loaded",            sorted(arb._view_rois),
          ["black1", "black2", "bound", "sample", "white"])
    check("opens fitted to the frame",
          round(arb._view_state.scale, 4), round(_L.IMG_CANVAS.w / 2304, 4))
    check("canvas image is canvas-sized",
          arb._pool["img_canvas"].image.size, (_L.IMG_CANVAS.w, _L.IMG_CANVAS.h))
    check("all 15 viewer widgets shown",
          len([n for n in arb._pool.visible_names() if n.startswith("img_")]), 15)
    check("Edit/Save/Cancel wait for step 8",
          [arb._pool[n].enabled for n in ("img_edit", "img_save", "img_cancel")],
          [False, False, False])

    print("== image viewer: zoom, pan, fit ==")
    fit_scale = arb._view_state.scale
    arb._view_zoom_in()
    check("+ zooms in",  round(arb._view_state.scale, 4),
          round(fit_scale * _VIEW_ZOOM_STEP, 4))
    arb._view_zoom_out()
    check("− returns",   round(arb._view_state.scale, 4), round(fit_scale, 4))
    arb._view_zoom_out()
    check("cannot zoom out past fit", arb._view_state.scale, fit_scale)
    for _ in range(4):
        arb._view_zoom_in()
    cx_before = arb._view_state.cx
    arb._view_arrow(1, 0)
    check("▶ pans right", arb._view_state.cx > cx_before, True)
    arb._view_arrow(-1, 0)
    check("◀ pans back",  round(arb._view_state.cx), round(cx_before))
    arb._view_fit()
    check("Fit restores", arb._view_state.scale, fit_scale)

    print("== image viewer: canvas taps select, ambiguity cycles ==")
    import image_view as _iv
    def _tap_on(name):
        """Tap the centre of a ROI, in screen coordinates."""
        r = arb._view_rois[name]
        cx, cy = _iv.image_to_canvas(arb._view_state,
                                     r.x + r.w / 2, r.y + r.h / 2)
        arb.on_touch(int(_L.IMG_CANVAS.x + cx), int(_L.IMG_CANVAS.y + cy))

    arb._view_state.fit_to(arb._view_rois["sample"])
    check("nothing selected initially", arb._view_selected, None)
    _tap_on("sample")
    check("tap inside sample selects the smallest", arb._view_selected, "sample")
    check("name strip shows it",  arb._pool["img_name"].text, "sample 68x68")
    _tap_on("sample")            # sample and bound both contain this point
    check("second tap moves to the other candidate", arb._view_selected, "bound")
    _tap_on("sample")
    check("third tap returns — a two-way toggle", arb._view_selected, "sample")
    # A tap on empty image space clears the selection.
    arb._view_state.fit()
    arb.on_touch(_L.IMG_CANVAS.x + 3, _L.IMG_CANVAS.y + 3)
    check("tap on empty space deselects", arb._view_selected, None)

    print("== image viewer: the preview is metered on the sample ROI ==")
    # The sample's median must land within tolerance of the target, and the
    # exposure that achieved it is remembered for the next preview.
    _lvl = pcam.sample_level()
    check("sample median is on target",
          abs(_lvl - _PREVIEW_TARGET) <= _PREVIEW_AE_TOLERANCE * _PREVIEW_TARGET,
          True)
    check("exposure inside the allowed range",
          _PREVIEW_MIN_US <= pcam.exposure_us <= _PREVIEW_MAX_US, True)
    check("and it is remembered",   arb._view_exposure_us, pcam.exposure_us)
    print(f"     converged to {pcam.exposure_us} us in {pcam.frames} frame(s), "
          f"sample median {_lvl} of {_PREVIEW_TARGET}")
    # Metering reads only the sample: a ROI outside the mosaic is refused
    # rather than silently metered on something else.
    _saved_sample = arb._view_rois["sample"]
    from rect import Rect as _Rect
    arb._view_rois["sample"] = _Rect(9000, 9000, 68, 68, name="sample")
    check("an off-frame sample is not metered",
          arb._meter_sample_level(_np.zeros((1296, 2304), _np.uint16), pcam), None)
    arb._view_rois.pop("sample")
    check("no sample ROI at all is not metered",
          arb._meter_sample_level(_np.zeros((1296, 2304), _np.uint16), pcam), None)
    arb._view_rois["sample"] = _saved_sample

    print("== image viewer: 1px/10px toggle drives both modes ==")
    check("starts at 1px",   arb._pool["img_step"].label, "1px")
    arb._view_state.fit_to(arb._view_rois["sample"])   # zoomed in, so pan bites
    _w_before = arb._view_state.w
    _cx0 = arb._view_state.cx
    arb._view_arrow(1, 0)
    check("fine pan moves 5% of the view",
          round(arb._view_state.cx - _cx0, 3), round(_w_before * _VIEW_PAN_FINE, 3))
    arb._view_toggle_step()
    check("toggles to 10px", (arb._view_step, arb._pool["img_step"].label),
          (10, "10px"))
    _cx1 = arb._view_state.cx
    arb._view_arrow(1, 0)
    check("coarse pan moves 25% of the view",
          round(arb._view_state.cx - _cx1, 3),
          round(_w_before * _VIEW_PAN_COARSE, 3))
    arb._view_toggle_step()
    check("and back",        arb._view_step, 1)
    arb._view_fit()

    print("== image viewer: Edit follows the selection, not just the render ==")
    # The bug this covers: opening from the Menu selects nothing, so Edit was
    # rendered disabled; selecting a ROI by touch repaints only the canvas, so
    # Edit stayed disabled and its press was swallowed by the pool.
    arb._view_selected = None
    arb._repaint_canvas()
    check("nothing selected -> Edit disabled", arb._pool["img_edit"].enabled, False)
    _tap_on("sample")
    check("tap selects",       arb._view_selected, "sample")
    check("Edit is now live",  arb._pool["img_edit"].enabled, True)
    check("and the pool routes a press to it",
          arb._pool.handle_touch(_L.IMG_EDIT.x + 5, _L.IMG_EDIT.y + 5), True)
    check("which entered edit mode", arb._view_mode, "edit")
    arb._view_cancel()
    arb._view_selected = None
    arb._repaint_canvas()
    check("deselecting disables it again",
          arb._pool["img_edit"].enabled, False)

    print("== image viewer: reimage takes a fresh frame ==")
    n_before = pcam.frames
    arb._view_reimage()
    check("one more capture", pcam.frames, n_before + 1)
    check("already-converged exposure needs no second frame",
          pcam.frames - n_before, 1)
    check("LED cycled again", pleds.history[-2:],
          [(_PREVIEW_LED_CH, True), (_PREVIEW_LED_CH, False)])

    print("== image viewer: Exit returns and frees the frame ==")
    arb._view_exit()
    check("closed",            arb._view_open, False)
    check("frame released",    arb._view_frame, None)
    check("camera no longer wanted", arb._camera_wanted(), False)
    check("back on the Menu",  arb._pool["menu_title"].text, "Menu")

    print("== image viewer: locked out while a capture is in flight ==")
    arb._capture = _FakeCapture()
    check("capture_available False", arb.capture_available(), False)
    arb._render_menu()
    check("View Image disabled", arb._pool["menu_stub1"].enabled, False)
    frames_before = pcam.frames
    arb.open_image_view(slot=None)
    check("open refused",      arb._view_open, False)
    check("no frame taken",    pcam.frames, frames_before)
    arb._capture = None

    print("== image viewer: Tool 2 opens zoomed to the sample ==")
    arb._ensure_camera = lambda: (setattr(arb, "_camera", pcam),
                                  setattr(arb, "_leds", pleds),
                                  (pcam, pleds))[-1]
    A.capturing = False           # cleared after the capture tests above
    arb.focus(A)                  # the viewer's Exit left the Menu focused
    arb.goto(A, "read")
    check("read shows View Sample",
          arb._pool["view_sample_btn"].visible, True)
    check("Elapsed label hidden", arb._pool["timer_lbl"].visible, False)
    _r = _L.TIMER_LBL
    arb.on_touch(_r.x + _r.w // 2, _r.y + _r.h // 2)
    check("viewer open",       arb._view_open, True)
    check("sample preselected", arb._view_selected, "sample")
    _sample = arb._view_rois["sample"]
    # fit_to(sample, 0.2): the ROI plus a fifth of its size, so about x2.45 on
    # a 200-wide canvas, and centred.
    check("zoomed to the sample + 20%",
          round(arb._view_state.scale, 2),
          round(_L.IMG_CANVAS.w / (_sample.w * 1.2), 2))
    _c = _iv.image_to_canvas(arb._view_state,
                             _sample.x + _sample.w / 2, _sample.y + _sample.h / 2)
    check("and centred on it", (round(_c[0]), round(_c[1])),
          (_L.IMG_CANVAS.w // 2, _L.IMG_CANVAS.h // 2))
    check("whole ROI fits on the canvas",
          all(0 <= v <= m for v, m in zip(
              _iv.rect_to_canvas(arb._view_state, _sample),
              (_L.IMG_CANVAS.w, _L.IMG_CANVAS.h) * 2)), True)
    check("name strip names it", arb._pool["img_name"].text, "sample 68x68")

    print("== image viewer: Tool 2's Exit returns to the read screen ==")
    arb._view_exit()
    check("focus is back on the slot", arb._focused is A, True)
    check("still the read phase",      A.phase, "read")
    check("View Sample offered again", arb._pool["view_sample_btn"].visible, True)

    # ── Edit mode (step 8) ────────────────────────────────────────────────────
    # Runs against the scratch DB copy, so real roi.save()/restore() are
    # exercised without touching the live colorimeter.sqlite.
    print("== edit mode: Edit takes a working copy and zooms to it ==")
    import roi as _roi
    arb._view_exit()
    arb.open_image_view(slot=None, focus_roi="sample")
    check("Edit enabled with a selection", arb._pool["img_edit"].enabled, True)
    check("Save/Cancel wait",
          [arb._pool[n].enabled for n in ("img_save", "img_cancel")], [False, False])
    _orig = arb._view_rois["sample"].xywh()
    arb._view_edit()
    check("in edit mode",     arb._view_mode, "edit")
    check("working copy matches the stored ROI",
          arb._view_editing.xywh(), _orig)
    check("stored ROI untouched", arb._view_rois["sample"].xywh(), _orig)
    check("nothing armed yet", arb._view_armed, None)
    check("zoomed to rect + 50%", round(arb._view_state.scale, 2),
          round(_L.IMG_CANVAS.w / (68 * 1.5), 2))
    check("Edit now disabled",   arb._pool["img_edit"].enabled, False)
    check("Save/Cancel offered",
          [arb._pool[n].enabled for n in ("img_save", "img_cancel")], [True, True])
    check("Exit withheld while editing", arb._pool["img_exit"].enabled, False)
    arb._view_exit()
    check("and Exit really refuses", arb._view_open, True)

    print("== edit mode: arrows move the whole rect when nothing is armed ==")
    arb._view_step = 10
    arb._view_arrow(1, 0)
    check("moved right by the step",
          arb._view_editing.xywh(), (_orig[0] + 10, _orig[1], 68, 68))
    arb._view_arrow(0, 1)
    check("and down",
          arb._view_editing.xywh(), (_orig[0] + 10, _orig[1] + 10, 68, 68))
    check("size unchanged", (arb._view_editing.w, arb._view_editing.h), (68, 68))
    arb._view_arrow(-1, 0); arb._view_arrow(0, -1)
    check("back where it started", arb._view_editing.xywh(), _orig)

    print("== edit mode: a tap near a side arms it; the view follows ==")
    def _tap_image(ix, iy):
        cx, cy = _iv.image_to_canvas(arb._view_state, ix, iy)
        arb.on_touch(int(_L.IMG_CANVAS.x + cx), int(_L.IMG_CANVAS.y + cy))

    r = arb._view_editing
    _tap_image(r.x, r.y + r.h / 2)                 # on the left edge
    check("left armed", arb._view_armed, "left")
    check("name strip names the side", arb._pool["img_name"].text, "left 68x68")
    _c = _iv.image_to_canvas(arb._view_state, r.x, r.y + r.h / 2)
    check("view centred on the armed edge",
          (round(_c[0]), round(_c[1])),
          (_L.IMG_CANVAS.w // 2, _L.IMG_CANVAS.h // 2))
    _tap_image(r.x, r.y + r.h / 2)                 # same edge again
    check("tapping it again disarms", arb._view_armed, None)
    _tap_image(arb._view_editing.x2(), arb._view_editing.y + 34)
    check("right armed", arb._view_armed, "right")

    print("== edit mode: an armed side moves alone ==")
    arb._view_armed = "right"
    arb._view_editing = _roi.Rect(*_orig, name="sample")
    arb._view_arrow(1, 0)
    check("right edge out by the step",
          arb._view_editing.xywh(), (_orig[0], _orig[1], 78, 68))
    arb._view_arrow(0, 1)
    check("a perpendicular arrow does nothing",
          arb._view_editing.xywh(), (_orig[0], _orig[1], 78, 68))
    arb._view_armed = "left"
    arb._view_arrow(1, 0)
    check("left edge in shrinks from the left",
          arb._view_editing.xywh(), (_orig[0] + 10, _orig[1], 68, 68))
    arb._view_armed = "top"
    arb._view_arrow(0, -1)
    check("top edge up grows upward",
          arb._view_editing.xywh(), (_orig[0] + 10, _orig[1] - 10, 68, 78))

    print("== edit mode: 1px/10px, and the limits ==")
    arb._view_step = 1
    arb._view_armed = "bottom"
    arb._view_editing = _roi.Rect(*_orig, name="sample")
    arb._view_arrow(0, 1)
    check("1px step", arb._view_editing.h, 69)
    arb._view_armed = "right"
    for _ in range(200):                            # try to collapse it
        arb._view_step = 10
        arb._view_arrow(-1, 0)
    check("cannot shrink past the minimum",
          arb._view_editing.w, _EDIT_MIN_SIDE)
    arb._view_armed = None
    arb._view_editing = _roi.Rect(*_orig, name="sample")
    for _ in range(500):
        arb._view_arrow(-1, -1)
    check("cannot be pushed off the frame",
          (arb._view_editing.x, arb._view_editing.y), (0, 0))

    print("== edit mode: Cancel discards ==")
    arb._view_editing = _roi.Rect(9, 9, 30, 30, name="sample")
    arb._view_cancel()
    check("back to view mode",  arb._view_mode, "view")
    check("working copy gone",  arb._view_editing, None)
    check("stored ROI unchanged", _roi.load_active()["sample"].xywh(), _orig)
    check("says cancelled",     arb._pool["img_name"].text, "cancelled")
    check("Exit offered again", arb._pool["img_exit"].enabled, True)

    print("== edit mode: Save validates before writing ==")
    def _try_save(rect):
        arb._view_edit()
        arb._view_editing = rect
        arb._view_save()
        return arb._view_mode, arb._pool["img_name"].text

    check("too small is refused",
          _try_save(_roi.Rect(1150, 790, 8, 8, name="sample")),
          ("edit", f"min {_EDIT_MIN_SIDE}px"))
    arb._view_cancel()
    check("outside the frame is refused",
          _try_save(_roi.Rect(2290, 790, 40, 40, name="sample")),
          ("edit", "outside frame"))
    arb._view_cancel()
    check("a sample outside bound is refused",
          _try_save(_roi.Rect(100, 100, 40, 40, name="sample")),
          ("edit", "sample outside bound"))
    arb._view_cancel()
    check("nothing was written by any of them",
          _roi.load_active()["sample"].xywh(), _orig)

    print("== edit mode: Save writes, snapping to even ==")
    arb._view_edit()
    arb._view_editing = _roi.Rect(1151, 791, 41, 41, name="sample")
    arb._view_save()
    check("back to view mode",   arb._view_mode, "view")
    check("snapped even and stored",
          _roi.load_active()["sample"].xywh(), (1150, 790, 40, 40))
    check("Bayer-safe",   _roi.is_bayer_safe(_roi.load_active()["sample"]), True)
    check("the screen reloaded it", arb._view_rois["sample"].xywh(), (1150, 790, 40, 40))
    check("says what it saved", arb._pool["img_name"].text, "saved 40x40")
    check("history kept, not overwritten", _db.read_val(
        "SELECT COUNT(*) FROM roi WHERE name='sample' AND active=0") >= 1, True)

    print("== edit mode: restore puts the commissioned default back ==")
    _roi.restore("sample")
    check("default is back", _roi.load_active()["sample"].xywh(), _orig)

    print("== image viewer: reimaging keeps where you were looking ==")
    arb._view_state.fit_to(arb._view_rois["sample"])
    _scale_before, _cx_before = arb._view_state.scale, arb._view_state.cx
    arb._view_reimage()
    check("scale kept",  arb._view_state.scale, _scale_before)
    check("centre kept", arb._view_state.cx, _cx_before)
    arb._view_exit()

    print("== image viewer: a failed preview does not break the screen ==")
    arb._ensure_camera = lambda: None
    arb.open_image_view(slot=A)
    check("still opens",        arb._view_open, True)
    check("no frame",           arb._view_frame, None)
    check("canvas cleared",     arb._pool["img_canvas"].image, None)
    check("says why",           arb._pool["img_name"].text, "Camera unavailable")
    arb._view_exit()
    check("Exit returns to the slot", arb._focused is A, True)

    shutil.rmtree(_tmpdir, ignore_errors=True)

    print("\n== all arbiter self-tests passed ==")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        Arbiter().start()
