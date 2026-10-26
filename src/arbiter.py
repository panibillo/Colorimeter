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
from pathlib import Path

_SIMULATION_MODE = os.environ.get("COLORIMETER_SIMULATION", "0") == "1"

# Hardware drivers are imported lazily in _init_hardware() so this module (and
# its logic self-test) imports cleanly on a machine without the display/GPIO
# stacks.  Only the pure model + widget layer is imported at module load.
import sqlite3

import config_repo as config
import logging_manager
import widgets
import shutdown_screen
import splash_screen
import startup_checks
import version
import sample_repo
import exposure_repo
import layout
from widgets import Button, TextBox
from pool import Pool
from slot import Slot
from phases import (build_phases, EntryPhase, Entry2Phase, ReagentWeights,
                    _mmss, _to_float)

log = logging.getLogger(__name__)

# Phases during which the shared camera is unconditionally kept open: once a
# run reaches read (CAPTURE imminent) through analysis. reagent1 is
# deliberately NOT here — the camera should stay off as much as possible
# (it is believed to heat up and drift), on only long enough beforehand to be
# thermally stable. reagent2 is also absent from this tuple: whether the
# camera is wanted during reagent2 is a countdown threshold, not a blanket
# phase membership — see _reagent2_within()/_camera_wanted()/
# _maybe_open_camera(). Outside all of this (entry/entry2, and after results)
# no capture is imminent, so the camera is released.
_CAMERA_PHASES = ("read", "analysis")

# Reagent weight grid.  One entry box, one record key — the map is flat so a
# keystroke's destination is decided by which box was typed in and by nothing
# else.  The old single-field-per-reagent design picked the key from
# slot.running, which meant the same pixel wrote to a different column of the
# database depending on whether a timer happened to be counting.
WT_FIELD_KEYS = {
    "wt1_init_fld":  "reagent1_initial_wt",
    "wt1_final_fld": "reagent1_final_wt",
    "wt2_init_fld":  "reagent2_initial_wt",
    "wt2_final_fld": "reagent2_final_wt",
}


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

# Bounds for a reagent countdown entered on the Menu → Phases screen.  Zero is
# excluded (a wait that expires the instant it starts is never what was meant)
# and the ceiling is one hour, well past the longest protocol step.
# The range a reagent countdown may hold, shared with the Q10 formula's clamp
# so a hand-typed duration and a computed one are held to one answer about
# what is runnable.
_CFG_MIN_SECS, _CFG_MAX_SECS = config.MIN_REAGENT_SECS, config.MAX_REAGENT_SECS
# The step buttons on each phases row, in the order they are laid out.
_CFG_STEPS = (-60, -10, 10, 60)

# Config editor: body rows per page for the role/browse views (layout.py draws
# four), the roles view's grid size (4 rows x 2 columns, all on one page —
# layout.py draws eight), and the label of the entry that sits at the end of
# the role list rather than naming a role.
_CFGE_ROWS       = 4
_CFGE_ROLE_SLOTS = 8
_CFGE_RESTORE    = "Restore Factory Defaults"

# LED Color picker (Menu → LED Colors): 'dark' and 'jpg' are drawn fixed at
# the end of the active row for operator awareness only (a dark frame and a
# colour-corrected white-light photo are taken as part of every capture,
# automatically — see capture_runner.py). They are never offered in the
# inactive row, never selectable, and never written into led_color_order.
_LEDPICK_SPECIALS = ("dark", "jpg")

# Capture page: the largest signal (DN, black removed) a window setting may name.
_CAP_MAX_DN = 960

# Approximate (r, g, b) swatch colour per LED name — close enough to its
# wavelength to read at a glance — and the text colour that reads on it.
_LEDPICK_SWATCH = {
    "violet":     ((138,  43, 226), "white"),
    "royal_blue": (( 65, 105, 225), "white"),
    "blue":       ((  0,   0, 255), "white"),
    "cyan":       ((  0, 255, 255), "black"),
    "green":      ((  0, 170,   0), "white"),
    "amber":      ((255, 191,   0), "black"),
    "red_orange": ((255,  69,   0), "white"),
    "red":        ((213,   0,   0), "white"),
    "photo_red":  ((139,   0,   0), "white"),
    "white":      ((255, 255, 255), "black"),
    "dark":       ((  0,   0,   0), "white"),
    "jpg":        ((255, 255, 255), "black"),
}


def _led_initials(name: str) -> str:
    """'blue' -> 'B'; 'royal_blue' -> 'R B' — initials of each '_'-separated word."""
    return " ".join(w[0].upper() for w in name.split("_") if w)


def _wrap_csv(text: str, max_len: int) -> "tuple[str, str]":
    """
    Split a comma-separated string onto two lines, breaking only at a comma so
    no color name is cut mid-word. Whatever does not fit on line 1 goes onto
    line 2 whole, even past max_len — ten LED names fit two lines at the
    picker's font size with room to spare, and a line that ran long once would
    still rather show the whole value than silently truncate it.
    """
    parts = text.split(",") if text else []
    line1 = ""
    i = 0
    while i < len(parts):
        candidate = parts[i] if not line1 else f"{line1},{parts[i]}"
        if len(candidate) > max_len and line1:
            break
        line1 = candidate
        i += 1
    line2 = ",".join(parts[i:])
    return line1, line2


# Tunables (LED range, loop cadence, timeouts, reagent durations, rowid, the
# backlight pin) are rows in the database's `config` table and are read through
# config_repo — see the config.* calls throughout this module.


def _clamp_secs(secs: int) -> int:
    """Hold an entered reagent duration inside the allowed range."""
    return max(_CFG_MIN_SECS, min(_CFG_MAX_SECS, int(secs)))


def _clamp_us(us: float) -> int:
    """Hold a preview exposure inside the range the viewer will ask the sensor for."""
    return max(_PREVIEW_MIN_US, min(_PREVIEW_MAX_US, int(us)))


def _cfge_fit(name: str, limit: int = 13) -> str:
    """
    Break a parameter name at its underscores so it fits the config editor's
    title, which has only the 120 px between the two corner buttons.

    Example:
        >>> _cfge_fit("prefetch_budget_s")
        'prefetch\\nbudget_s'
        >>> _cfge_fit("layer_keep_min_dn")
        'layer_keep\\nmin_dn'
    """
    if len(name) <= limit:
        return name
    lines, line = [], ""
    for part in name.split("_"):
        candidate = f"{line}_{part}" if line else part
        if line and len(candidate) > limit:
            lines.append(line)
            line = part
        else:
            line = candidate
    lines.append(line)
    return "\n".join(lines)


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
        self._kb_ok     = None      # what KbReader.open() said, for the checks

        # The boot splash, live only between _init_display() and the operator
        # pressing MENU or START.  _resume_display consults it, because an
        # aborted shutdown screen during the boot has nothing else to go back
        # to — there is no focused slot and no pool yet.
        self._splash: "splash_screen.Splash | None" = None
        self._boot:   "splash_screen.BootLog | None" = None

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
        # Live "Capturing <color>, N exposures" / "Capturing dark images, N
        # exposures" counter mirrored onto the read phase's collect_line while
        # a capture is in flight — see pump_capture(). Reset at both
        # request_capture() success paths below.
        self._cap_progress_kind: str | None = None   # "lit" | "dark" | None
        self._cap_progress_n:    int = 0

        # The normal path's capture prefetch (CaptureRunner.run_prefetch()),
        # started automatically near the end of a slot's reagent2 countdown —
        # see _maybe_start_prefetch(). Parked (not pumped further) once done,
        # holding the camera/archive open, until request_capture() promotes it
        # into self._capture or it is cancelled. At most one at a time, mirroring
        # self._capture — a second slot nearing its own reagent2 threshold
        # while this is in flight/parked simply defers (see _maybe_start_prefetch()).
        self._prefetch: CaptureSession | None = None
        self._prefetch_slot: Slot | None = None
        # self._camera_epoch as it stood when the parked prefetch's own session
        # was created — see _new_session(). request_capture() only promotes a
        # parked prefetch whose stamp still matches the CURRENT _camera_epoch;
        # a mismatch means some other session (the other slot's run, an image
        # viewer preview, a forced refocus) touched the shared camera's focus
        # or exposure since this one parked, so its "before" darks and
        # whatever run_capture() would do next could silently disagree about
        # camera state — safer to discard it and fall back to a fresh run.
        self._prefetch_epoch: int | None = None

        # Set once the capture subsystem's heavy first-imports (picamera2,
        # libcamera, numpy, cv2) have been warmed — see prewarm_capture().
        self._capture_prewarmed = False

        # The persistent camera + LED bank, owned by the Arbiter and shared by
        # every capture.  Opened within a lead window before a slot's reagent2
        # countdown expires (so the sensor-start settle AND the dark-frame
        # prefetch batch both complete before CAPTURE is reachable) and reused
        # across captures; released when no slot needs them and at cleanup.
        # Each CaptureSession *borrows* these rather than opening its own.
        # None until opened — see _ensure_camera()/_maybe_open_camera().
        self._camera = None
        self._leds   = None
        # Bumped by _new_session() every time it hands a brand-new session the
        # shared camera — the counter a parked prefetch's _prefetch_epoch is
        # checked against before being promoted (see above).
        self._camera_epoch: int = 0

        # Placeholder analysis timer: (slot, started_at) or None.
        self._analysis: tuple | None = None

        # Shutdown state: guards against a second signal re-entering shutdown()
        # or cleanup() running its hardware-release chain twice.
        self._shutting_down = False
        self._cleaned_up    = False

        # Reference data: the entry-screen pre-fills, read from the config
        # table's `defaults` and `device` roles.
        self._defaults: sample_repo.Defaults | None = None

        # Menu screen state: the slot to return to when the menu closes.
        self._pre_menu_slot: Slot | None = None

        # Capture screen state (Menu → Capture): which view is showing and what
        # kind of run is in flight.  A menu-launched run uses self._capture with
        # _capture_slot = None (the None marks it standalone, so pump_capture
        # routes it to _pump_capture_screen).  _cap_mode is the capture path a
        # *test sequence* takes (ReadPhase → request_capture); the Capture page
        # no longer sets it.
        self._cap_mode:     str = "normal"    # "calibration" | "normal"
        self._cap_run_mode: str = "fit"       # the in-flight standalone run: "fit" | "show"
        self._cap_group: str = "1"            # images per LED, '1'..'5'
        self._cap_view:  str | None = None    # None | "choice" | "show"
        # Choice form: the LED chosen on the bar, the settings fields' text, and
        # the line of help or status under them.
        self._cap_led: str | None = None
        self._cap_values: dict[str, str] = {}    # the boxes' text, for the chosen LED
        self._cap_global: dict[str, str] = {}    # the global window's text
        self._cap_over: dict[str, dict] = {}     # LEDs with a window of their own
        self._cap_layers: dict[str, list] = {}   # required Bayer layers per LED
        self._cap_layers0: dict[str, list] = {}  # ... as stored, to spot changes
        self._cap_hint_msg: str = ""
        self._cap_running: str | None = None   # LED being calibrated now
        self._cap_fit = None                   # finished fit, awaiting SAVE
        # Show screen: the LED, the exposures on offer, what is chosen, the plot.
        self._show_led: str = ""
        self._show_exps: list = []
        self._show_i: int = 0
        self._show_mode: str = "exposure"
        self._show_layer: str = "B"
        self._show_hint_msg: str = ""
        self._show_image = None

        # Phases screen state (Menu → Phases): the reagent countdowns in
        # seconds, staged while the screen is open and written only by its OK.
        self._cfg_secs: dict[str, int] = {"r1": 0, "r2": 0}

        # Config editor state (Menu → Config): which of the three views is up,
        # what it is showing, and where in the list it is.  _cfge_start holds
        # the value each field on the current page was loaded with, which is
        # what "edited" is measured against and what Rollback puts back.
        self._cfge_view:  str | None = None   # None | "roles" | "role" | "browse"
        self._cfge_role:  str = ""            # the role the role/browse views show
        self._cfge_name:  str = ""            # the parameter the browse view shows
        self._cfge_page:  int = 0
        self._cfge_list:  list = []           # the whole list being paged through
        self._cfge_start: dict[str, str] = {}
        # The page each list was showing when the operator dropped a level.
        # Stepping back comes up on those same four rows: a role picked off
        # page 2 has to hand the screen back on page 2, not at the top of a
        # list the operator would then have to page through again.
        self._cfge_roles_page: int = 0        # roles view, on entering a role
        self._cfge_names_page: int = 0        # role view, on entering Browse
        # An edit lives only in its field, and both the confirm modals and the
        # Browse view take the fields off the screen — so anything typed is
        # held here for the render that brings the role view back.
        self._cfge_hold:  dict[str, str] = {}
        # Whether the footer is currently drawn in its edit-pending state, so a
        # keystroke repaints those five buttons only when it flips.
        self._cfge_footer_pending: bool = False

        # Image viewer state (Menu → View Image, and later Read → View Sample).
        # _view_open is read by _camera_wanted(), so the shared camera is held
        # for as long as the screen is up; without it _maybe_release_camera()
        # would close the sensor on the very next main-loop pass.
        self._view_open      = False
        self._view_frame     = None    # the whole captured frame, numpy BGR
        self._view_bayer     = None    # that frame's raw mosaic, for recap saves
        self._view_state     = None    # image_view.ViewState over that frame
        self._view_pad:   bool = False   # editing the test pad, not the measurement ROIs
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
        # Non-exclusive: either, neither, or both may be armed at once.
        self._recap_save_roi   = False   # save the bound ROI as jpeg+bayer h5
        self._recap_save_whole = False   # save the whole frame as jpeg+bayer h5
        # When True, Capture takes a white-LED-off frame instead of the usual
        # briefly-lit one — for a dark reference at the same exposure.
        self._recap_dark       = False

        # LED Color picker state (Menu → LED Colors): the staged active-LED
        # sequence (LED names only — 'dark'/'jpg' never appear in it), which
        # LED is selected (present in exactly one of the two rows at a time,
        # so its name alone identifies it), and the history Prior/Next pages
        # through.
        self._ledpick_colors:   list = []            # staged order, LED names only
        self._ledpick_selected: "str | None" = None
        self._ledpick_history:  list = []             # [(rowid, value)] loaded on open
        self._ledpick_idx:      int = 0                # position in _ledpick_history

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self, boot=None) -> None:
        """
        Bring the instrument up behind the splash screen, then run.

        The boot is in three acts, because the splash cannot narrate its own
        preconditions: the database and the display come up silently (the
        launcher having timed them into `boot`), the splash then replays those
        and narrates everything after them, and finally the operator chooses
        where to start.

        Args:
            boot: A splash_screen.BootLog the launcher filled in around
                  db_bootstrap.ensure().  Omitted, the splash simply has one
                  fewer line to show.
        """
        self._init_logging()
        if _SIMULATION_MODE:
            log.info("SIMULATION MODE: mock hardware")

        self._boot = boot or splash_screen.BootLog()
        with self._boot.step("Display"):
            self._init_drivers()
            self._init_display()

        splash = self._splash = splash_screen.Splash(
            self._disp, simulation=_SIMULATION_MODE,
            version=version.__version__)
        splash.open()
        splash.replay(self._boot)

        self._init_peripherals(splash)
        # Only now can anything press OK, so notices wait until here.
        splash.attach_input(touch=self._touch, kb=self._kb)

        with splash.running("Screens"):
            self._build_model(splash)

        report = self._run_startup_checks(splash)
        choice = splash.finish(report.summary(), ok=report.ok)
        self._splash = None

        if choice == "menu":
            # open_menu rather than focus: its OK/CANCEL returns the view to
            # the slot passed here, so MENU still lands on the Begin screen,
            # and the Begin screen is not painted twice to get there.
            self.open_menu(self._slots["A"])
        else:
            self.focus(self._slots["A"])
        self._run()

    def _run_startup_checks(self, splash):
        """
        Run the completeness checks as the splash's last narrated step.

        Anything that had to be loaded from a default gets its own message and
        its own OK press — the boot pauses on each one rather than letting a
        recovered value scroll past unread.  Then the step list is replaced by
        the warnings and failures, which is the only part of the boot the
        operator has anything to do about.
        """
        i = splash.step("Configuration")
        report = startup_checks.run_all(
            restored=self._boot.restored if self._boot else None,
            touch=self._touch, temp=self._temp, keyboard=self._kb_ok,
            simulation=_SIMULATION_MODE,
            progress=lambda label: splash.status(f"Checking {label}..."))
        for message in (self._boot.notices if self._boot else []):
            splash.notice(message)
        for message in report.notices():
            splash.notice(message)
        if not report.ok:
            splash.fail(i)
        elif report.warnings():
            splash.warn(i)
        else:
            splash.done(i)

        # The steps have finished saying anything useful; hand the screen over
        # to what the checks found.  Nothing is drawn when nothing went wrong.
        splash.problems(report.problems())
        return report

    @staticmethod
    def _init_logging() -> None:
        name = (os.environ.get("COLORIMETER_LOGLEVEL") or "INFO").upper()
        level = getattr(logging, name, logging.INFO)
        logging.basicConfig(level=level,
                            format="%(levelname)-8s [%(name)s] %(message)s")
        logging_manager.set_level(name if isinstance(level, int) else "INFO")

    def _init_hardware(self) -> None:
        """
        Bring up every device, unnarrated.

        Kept for callers that have no splash to report to (the self-test at
        the foot of this module); start() uses the three phases below so the
        splash can time and show them one at a time.
        """
        self._init_drivers()
        self._init_display()
        self._init_peripherals(None)

    def _init_drivers(self) -> None:
        """
        Import the drivers for this launch mode and construct them.

        No device is talked to here beyond switching the backlight on, so this
        is fast and cannot fail on absent hardware — the probing is in
        _init_display and _init_peripherals.
        """
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

        # Installed here rather than after the peripherals, so the splash's
        # own buttons click like every other button.  Safe this early: beeper
        # builds its singleton at import.
        widgets.set_button_sound(beeper.click)

    def _init_display(self) -> None:
        """
        Bring up the display — the one step the splash cannot narrate, being
        the step that gives it somewhere to draw.
        """
        self._disp.init()

        # Registered here, immediately after the display exists, rather than
        # after the peripherals: the mock display is where simulated key and
        # mouse events come from, so a splash pumping input during the steps
        # below would otherwise be deaf for all of them.  Safe this early —
        # the mock touch driver's handlers work before its init().
        if _SIMULATION_MODE and hasattr(self._disp, "register_key_handler"):
            self._disp.register_key_handler(self._kb._on_key_event)          # type: ignore
            self._disp.register_mouse_handler(self._touch._on_touch)         # type: ignore
            self._disp.register_touch_release_handler(self._touch._on_release)  # type: ignore

    def _init_peripherals(self, splash) -> None:
        """
        Probe touch, keyboard and the temperature bus, narrating each.

        A step that runs without raising is not the same as a device that
        answered: KbReader.open() returns False when no keyboard is on the
        bus, and DS18B20.init() finds no probe just as quietly.  Both used to
        report 'ok' — the step had, after all, completed — which said the
        opposite of what the operator needed to know.  So the steps that can
        come up empty are marked from what they found, not from whether they
        returned, and startup_checks says what to do about it.

        Args:
            splash: The Splash to report to, or None to run silently.
        """
        def run(label, fn, found=None):
            """Narrate `fn`; mark the step amber when `found` says it is empty."""
            if splash is None:
                return fn()
            i = splash.step(label)
            try:
                out = fn()
            except BaseException:
                splash.fail(i)
                raise
            if found is not None and not found(out):
                splash.warn(i)
            else:
                splash.done(i)
            return out

        run("Touch", self._touch.init)
        self._kb_ok = run("Keyboard", self._kb.open, found=bool)
        run("Temperature", self._temp.init,
            found=lambda _: getattr(self._temp, "connected", False))

    def _build_model(self, splash=None) -> None:
        """
        Build the widget pool, the phase registry and the two slots.

        Args:
            splash: Unused beyond keeping the call sites symmetrical — the
                    step is narrated by the caller, which times it.
        """
        self._pool = Pool.build()
        # Step 1's countdown is a configured constant, so it is baked into the
        # phase here.  Step 2's is not passed at all: it depends on the sample,
        # and Reagent2Phase asks reagent2_plan() for it when it needs one.
        self._phases = build_phases(
            reagent1_secs=config.reagent1_secs(),
            preimage=config.capture_preimage_enabled())
        self._slots = {"A": Slot("A"), "B": Slot("B")}
        self._load_defaults()

    def _load_defaults(self) -> None:
        """Load the GUI default values (entry pre-fills) from the config table."""
        try:
            self._defaults = sample_repo.load_defaults()
        except sqlite3.Error:
            log.exception("Arbiter: could not load defaults")
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

        if self._prefetch is not None:
            try:
                self._prefetch.cancel()
            except Exception:
                log.exception("Arbiter: cleanup: prefetch.cancel failed")
            self._prefetch = None
            self._prefetch_slot = None
            self._prefetch_epoch = None

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

    def temperature_attached(self) -> bool:
        """
        Whether a DS18B20 was found on the 1-Wire bus.

        Separate from read_temperature() because the two answer different
        questions: a probe that is present but whose reading failed is an
        error worth showing the operator, while no probe at all is the
        ordinary case on a unit run without one — entry2 offers the
        configured default temperature instead of an error there.
        """
        return bool(getattr(self._temp, "connected", False))

    # ── Phase contract: reagent 2's temperature-compensated countdown ──────────

    def t2ref25_secs(self) -> int:
        """protocol.t2ref25_secs — reagent 2's wait at the 25 °C reference."""
        return config.t2ref25_secs()

    def reagent2_plan(self, slot: Slot) -> tuple:
        """
        How long reagent step 2 should run for this slot, and on what grounds.

        The duration is config_repo.reagent2_secs(theta); this method's job is
        only to decide which theta answers.  The sample's own temperature is
        preferred — entered or probe-read on Entry screen 2 and persisted as a
        float — and defaults.default_temperature stands in when the field was
        left empty, which is the same stand-in Entry 2's Temp button offers on a
        unit with no probe.

        The stand-in is reported rather than hidden: a wait computed from an
        assumed temperature is a weaker claim than one computed from a measured
        one, so Reagent2Phase brackets it on screen and the operator can see
        which they are looking at before pressing START.

        Args:
            slot: The slot whose sample temperature to read.

        Returns:
            (secs, temp_c, measured) — the countdown in seconds, the
            temperature it came from in °C, and whether that temperature came
            from the sample rather than the configured stand-in.
        """
        temp_c = _to_float(slot.record.get("samp_degC"))
        measured = temp_c is not None
        if not measured:
            temp_c = getattr(self._defaults, "default_temperature", None)
            if temp_c is None:
                # No sample temperature and no stand-in: the only honest answer
                # left is the reference itself, which is the uncompensated wait.
                temp_c = config.REF_TEMP_C
                log.warning("Arbiter: slot %s has no sample temperature and no "
                            "configured default — reagent 2 will run "
                            "uncompensated", slot.name)
        return config.reagent2_secs(temp_c), float(temp_c), measured

    def reagent1_stir_seconds(self) -> int:
        """protocol.reagent1_stir_seconds — stir duration for reagent 1."""
        return config.reagent1_stir_seconds()

    def reagent_weigh_plan(self, slot: Slot, n: int) -> ReagentWeights:
        """
        Everything the weight grid needs about reagent `n`, in grams.

        `net` deliberately answers two different questions, because the same
        cell answers them in turn:

        - initial entered, final blank -> what the packet holds
          (initial - the empty packet's own weight), which is what the operator
          needs before pouring;
        - both entered -> what was actually poured in (initial - final), which
          is the number the analysis wants.

        `overage` is net - target either way, so a single reading of the Over
        cell means "this much more than the protocol asks for" both before and
        after the pour.

        `target` comes from the sample volume alone and no longer waits for a
        valid initial weight: it fills the Target cell, and it is what makes an
        overage computable at all, so gating it on the initial weight would
        leave the grid blank exactly when the operator is deciding what to do.

        Any of `initial`, `final`, `net`, `target` and `overage` may be None —
        an unentered or unparseable weight is absent, not zero.

        Args:
            slot: The slot whose record to read weights from.
            n: 1 for reagent 1, 2 for reagent 2.

        Returns:
            A ReagentWeights(initial, final, net, target, overage, tol).
        """
        prefix = f"reagent{n}_"
        empty_wt = (config.reagent1_empty_packet_wt() if n == 1
                    else config.reagent2_empty_packet_wt())
        ratio = (config.reagent1_100ml_wt() if n == 1
                 else config.reagent2_100ml_wt())
        tol = config.reagent_wt_tolerance()

        initial = _to_float(slot.record.get(f"{prefix}initial_wt", ""))
        final = _to_float(slot.record.get(f"{prefix}final_wt", ""))
        sample_wt = _to_float(slot.record.get("samp_grams", ""))

        if initial is None:
            net = None
        elif final is None:
            net = initial - empty_wt
        else:
            net = initial - final

        target = None if sample_wt is None else sample_wt / 100.0 * ratio
        overage = None if (net is None or target is None) else net - target

        return ReagentWeights(initial=initial, final=final, net=net,
                              target=target, overage=overage, tol=tol)

    def reagent_finals_complete(self, slot: Slot) -> bool:
        """
        Both reagents' final packet weights have been entered.

        The gate between a finished capture and analysis: reagentN_g is the
        difference of the two weighings, so a run missing a final weighing has
        no record of how much reagent it actually received.
        """
        return all(str(slot.record.get(f"reagent{n}_final_wt", "")).strip()
                   for n in (1, 2))

    def reagent2_stir_plan(self, slot: Slot) -> int:
        """
        Stir duration in seconds for reagent 2, temperature-compensated.
        Returns 0 if no stir sub-alarm is configured.
        """
        return config.reagent2_stir_secs(
            float(slot.record.get("samp_degC", "21.0") or "21.0")
        )

    def _stir_alarm(self, slot: Slot) -> None:
        """Sound a 2-second beep for the stir sub-alarm."""
        log.info("Arbiter: STIR ALARM for slot %s", slot.name)
        if self._beeper is None:
            return
        fn = getattr(self._beeper, "beep", None)
        if callable(fn):
            fn(duration=2.0)

    # ── Phase contract: database / sample lifecycle ────────────────────────────

    def defaults(self):
        """The loaded GUI defaults (entry pre-fills), or None if unavailable."""
        return self._defaults

    def _samp_no_template(self) -> str:
        return self._defaults.samp_no_template if self._defaults else ""

    def next_sample_no(self, slot: Slot | None = None) -> str:
        """
        The next sample number for a slot opening fresh: one past the database
        and past whatever the *other* slots are displaying, so two slots never
        show the same number.  The asking slot is left out of that comparison —
        it is asking what to hold, so its own value cannot stand in its way.
        """
        held = [s.record.get("samp_no", "")
                for s in self._slots.values() if s is not slot]
        try:
            return sample_repo.next_sample_no(self._samp_no_template(), held)
        except sqlite3.Error:
            log.exception("Arbiter: next_sample_no failed")
            return ""

    def claim_next_no(self, slot: Slot) -> None:
        """
        Put the next unclaimed number in `slot`, moving any slot showing it aside.

        Unclaimed means "no row in samples" — Begin inserts synchronously, so the
        table is the whole claim registry and what another slot merely displays
        is not a claim.  This therefore takes the true next number and bumps that
        slot, rather than skipping past it.  (Were the insert ever deferred, this
        would have to consult the begun slots as well.)

        Returning early when the slot already holds that number is what makes
        repeated ID presses idempotent.
        """
        template = self._samp_no_template()
        try:
            want = sample_repo.next_sample_no(template)      # the table alone
            if sample_repo.same_samp_no(slot.record.get("samp_no", ""), want):
                return                                       # already ours
            slot.record["samp_no"] = want
            for other in self._slots.values():
                # A claimed number is in the table, so next_sample_no could not
                # have offered it; skipping those slots is belt and braces.
                if other is slot or other.record.get("db_rowid") is not None:
                    continue
                if sample_repo.same_samp_no(other.record.get("samp_no", ""), want):
                    other.record["samp_no"] = self.next_sample_no(other)
        except sqlite3.Error:
            log.exception("Arbiter: claim_next_no failed for slot %s", slot.name)

    def begin_sample(self, slot: Slot) -> bool:
        """
        Reserve the sample row at Begin (INSERT), claiming its samp_no in the DB.
        Returns False on a duplicate/invalid samp_no (UNIQUE violation) so the
        caller can warn the operator instead of advancing.
        """
        r = slot.record
        try:
            rowid = sample_repo.insert_sample(
                r["samp_no"], r["user"], r["test_kit"], r["test_lot"],
                r["instrument_sn"])
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

    def _flush_entry(self, slot: Slot) -> None:
        """
        Write screen-1 identity edits to the claimed row before the view leaves it.

        Those three fields are editable only once the row exists, and keystrokes
        land in the record rather than the database (write_one opens its own
        connection, far too costly per character).  So the record is flushed at
        the moments the operator navigates away, which is every way out of the
        screen bar losing power.  samp_no is deliberately absent: it is claimed,
        and the screen locks it.

        Gated on the *viewed* phase, so edits made while previewing screen 1 from
        entry2 are flushed too.
        """
        if slot.phase != "entry" or slot.record.get("db_rowid") is None:
            return
        r = slot.record
        self.persist(slot, user=r["user"], test_kit=r["test_kit"],
                     test_lot=r["test_lot"])

    # ── Phase contract: navigation / render ────────────────────────────────────

    def goto(self, slot: Slot, phase_id: str) -> None:
        """
        Advance a slot to a phase (the real protocol flow): this becomes the
        slot's live phase.  Runs its once-only activate, then draws if focused.
        """
        self._flush_entry(slot)
        slot.phase = slot.active_phase = phase_id
        self._phases[phase_id].activate(self, slot)
        self.render(slot)

    def view(self, slot: Slot, phase_id: str) -> None:
        """
        Preview a phase without advancing the test (PRIOR/NEXT browsing).  Only
        the viewed phase changes — the live phase, its timer, and any capture
        keep running untouched.  No activate(), so nothing is started or reset.
        """
        self._flush_entry(slot)
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
        if self._focused is not None and self._focused is not slot:
            self._flush_entry(self._focused)   # the slot being left behind
        self._focused = slot
        self._disarm_alarm(slot)     # regaining focus clears its visible alarm
        self.render(slot)

    def _resume_display(self) -> None:
        """Repaint whatever was on screen before a full-screen interruption
        (e.g. an aborted shutdown screen) — the splash, a slot, or the Menu."""
        # During the boot there is no focused slot and often no pool, so
        # without this an aborted shutdown screen would leave a blank panel
        # with the splash still pumping input onto it.
        if self._splash is not None:
            self._splash.repaint()
            return
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
        self._flush_entry(slot)
        self._pre_menu_slot = slot
        self._focused = None
        self._render_menu()

    def _render_menu(self) -> None:
        if self._pool is None:
            return
        self._pool.hide_all()
        self._pool.put("menu_cancel_btn", "CANCEL", on=self._menu_cancel)
        self._pool.put("menu_title",      "Menu")
        self._pool.put("menu_ok_btn",     "OK",     on=self._menu_ok)
        # The 3x2 option grid, laid out in layout.py.  "View Image" is disabled
        # while a capture is in flight — the imaging tools share the camera and
        # LED bank with the capture path.  "Reagent Phases" edits the two
        # reagent countdowns; "Config" is the general editor over every
        # setting, reagent countdowns included.
        self._pool.put("menu_capture",    "Capture",  on=self._menu_open_capture)
        self._pool.put("menu_stub1",      "View\nImage",
                       on=self._menu_open_image_view,
                       enabled=self.capture_available())
        self._pool.put("menu_defaults",   "LED\nColors", on=self._menu_open_ledpick)
        self._pool.put("menu_stub2",      "Config",   on=self._cfge_open)
        self._pool.put("menu_config",     "Reagent\nPhases", on=self._menu_open_config)
        self._pool.put("menu_power",      "Power",    on=self._menu_power)
        self._pool.render(self._disp)
        self._rebuild_focus()

    # Nothing is staged at the menu level any more — the screens it opens each
    # commit their own changes — so OK and CANCEL are two ways out of the same
    # screen.  Both are kept because the header chrome is shared with the
    # screens below, where the distinction still means something.

    def _menu_ok(self) -> None:
        """Leave the menu."""
        self._close_menu()

    def _menu_cancel(self) -> None:
        """Leave the menu."""
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

    # ── Phases screen (Menu → Phases) ──────────────────────────────────────────
    # Custom reagent countdowns, entered on the instrument itself.  The two
    # durations are staged in self._cfg_secs while the screen is open and are
    # written (as new rows in the `config` table) only by its OK, so a mis-tap
    # costs nothing — CANCEL leaves the saved values alone.
    #
    # They are ordinary `protocol` parameters, so the config editor below can
    # reach them too; this screen exists because stepping a countdown by ±10 s
    # against a mm:ss readout is the way an operator actually wants to set one.
    # Restore supersedes a custom value with the commissioned row again; the
    # edit stays in the table as history either way.
    #
    # The R2 row edits protocol.t2ref25_secs — the step 2 wait at 25 °C, not
    # the wait any particular sample will get, which is computed from its own
    # temperature (see reagent2_plan()).  protocol.q10 is deliberately NOT here:
    # a stepper invites nudging, and Q10 is a calibration constant for a test
    # kit rather than something to adjust between runs.  It is edited in
    # Menu -> Config, where changing it looks like the commissioning act it is.

    def _menu_open_config(self) -> None:
        """Open the phases screen, staging the durations now in force."""
        self._cfg_secs = {"r1": config.reagent1_secs(),
                          "r2": config.t2ref25_secs()}
        self._render_config()

    def _render_config(self) -> None:
        if self._pool is None:
            return
        p = self._pool
        # A slot mid-protocol already read these durations; editing them now
        # would change a running countdown out from under it. Viewing stays
        # available — only the editable controls are locked.
        editable = not self._any_slot_active()
        p.hide_all()
        # Top-corner chrome like the Menu and Capture screens.
        p.put("menu_cancel_btn", "CANCEL", on=self._cfg_cancel)
        p.put("cfg_title",       "Phases")
        p.put("menu_ok_btn",     "OK",     on=self._cfg_ok)
        # R2's label carries the reference temperature, because the number in
        # that field is not the wait a sample gets — it is the wait at 25 °C
        # that every sample's wait is scaled from.
        for row, lbl in (("r1", "Reagent 1"), ("r2", "Reagent 2\n@25°C")):
            p.put(f"cfg_{row}_lbl", lbl)
            for step in _CFG_STEPS:
                p.put(self._cfg_step_widget(row, step), f"{step:+d}",
                      on=(lambda r=row, d=step: self._cfg_step(r, d)),
                      enabled=editable)
        p.put("cfg_r1_fld", enabled=editable)
        p.put("cfg_r2_fld", enabled=editable)
        p.put("cfg_hint")
        p.put("cfg_restore", "Restore", on=self._cfg_restore, enabled=editable)
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
                          f"     R2 {_mmss(self._cfg_secs['r2'])} @25°C")
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
                          "r2": int(config.default("protocol", "t2ref25_secs"))}
        self._cfg_paint_values()

    def _cfg_ok(self) -> None:
        """Save the staged durations, apply them, and return to the Menu."""
        for row, key in (("r1", "reagent1_secs"), ("r2", "t2ref25_secs")):
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

        Only step 1 needs the rebuild.  Step 2's phase holds no duration to
        replace — it reads t2ref25_secs and q10 through reagent2_plan() every
        time it arms — so an edit to either is already in force by the time
        this is called.  It is rebuilt along with step 1 regardless, because
        build_phases() makes the whole registry or none of it.
        """
        r1 = config.reagent1_secs()
        self._phases = build_phases(reagent1_secs=r1,
                                    preimage=config.capture_preimage_enabled())
        log.info("Arbiter: reagent 1 countdown set to %ds; reagent 2 reference "
                 "%ds @%.0f °C, Q10 %.2f",
                 r1, config.t2ref25_secs(), config.REF_TEMP_C, config.q10())

    # ── Config editor (Menu → Config) ──────────────────────────────────────────
    # Every setting the instrument has, in one screen with three views: the list
    # of roles, one role's parameters, and one parameter's history.  Built as
    # _cfge_* methods rather than Phase objects for the reason set out for the
    # image viewer below — a Phase is bound to a Slot, and this screen has none.
    #
    # A value is typed into its field from the USB keyboard, or picked from the
    # Browse view, which is the whole editor for a touch-only instrument (there
    # is no on-screen keyboard).  Typing marks the row edited — red on yellow —
    # and from then until Rollback or Save the paging buttons are dead, so an
    # unsaved edit cannot be scrolled off the screen and lost.
    #
    # Nothing here writes SQL: config_repo owns the four transactions (save,
    # set_default, restore, restore_all) and this screen only decides which one
    # a button calls and with what.

    def _cfge_open(self) -> None:
        """Menu → Config: open the editor on the first page of roles."""
        self._cfge_role = ""
        self._cfge_name = ""
        self._cfge_roles_page = 0
        self._cfge_names_page = 0
        self._cfge_open_roles()

    def _cfge_editable(self) -> bool:
        """
        False while a slot is past entry: a live sample has already read these
        values and none of them may move under it.  The screen stays viewable —
        only the fields and the write buttons go dead.
        """
        return not self._any_slot_active()

    # ── the three views ────────────────────────────────────────────────────────

    def _cfge_open_roles(self, page: int = 0) -> None:
        """Show the roles, with Restore Factory Defaults after the last one."""
        self._cfge_view  = "roles"
        self._cfge_start = {}
        # Restore rides at the end of the list, so it lands on the last page
        # rather than under a finger aiming for the first role.
        self._cfge_list  = config.roles() + [_CFGE_RESTORE]
        self._cfge_goto(page)
        self._cfge_render()

    def _cfge_enter_role(self, role: str) -> None:
        """A role tapped on the roles view: remember this page, then open it."""
        self._cfge_roles_page = self._cfge_page
        self._cfge_open_role(role)

    def _cfge_open_role(self, role: str, page: int = 0) -> None:
        """Show one role's parameters, four to a page, alphabetically."""
        self._cfge_view = "role"
        self._cfge_role = role
        self._cfge_list = config.items(role)
        self._cfge_goto(page)
        self._cfge_render()

    def _cfge_open_browse(self, name: str) -> None:
        """
        Show every value one parameter has held: in force, default, history.

        Only ever reached from the role view, so the page it leaves is the one
        it has to come back to.

        Looking a value up is the obvious thing to do halfway through typing
        one, so an edit in progress is held rather than blocked or discarded —
        CANCEL comes back to the role view with it still in its field.
        """
        self._cfge_hold = dict(self._cfge_pending())
        self._cfge_names_page = self._cfge_page
        self._cfge_view = "browse"
        self._cfge_name = name
        self._cfge_list = config.history(self._cfge_role, name)
        self._cfge_goto(0)
        self._cfge_render()

    # ── paging ─────────────────────────────────────────────────────────────────

    def _cfge_page_size(self) -> int:
        """Roles view shows all 8 at once (4x2 grid); role/browse page by 4."""
        return _CFGE_ROLE_SLOTS if self._cfge_view == "roles" else _CFGE_ROWS

    def _cfge_pages(self) -> int:
        """How many pages the current list fills (at least one, even if empty)."""
        return max(1, -(-len(self._cfge_list) // self._cfge_page_size()))

    def _cfge_page_rows(self) -> list:
        """The slice of the list the current view's body slots are showing."""
        size  = self._cfge_page_size()
        first = self._cfge_page * size
        return self._cfge_list[first:first + size]

    def _cfge_goto(self, page: int) -> None:
        """
        Show `page` of the list just loaded, clamped to what the list has.

        A remembered page can outlive the list it was counted against — a role
        loses a parameter, a value drops out of a history — so it is clamped
        rather than trusted, and the operator lands on the last page instead of
        on four blank rows.
        """
        self._cfge_page = max(0, min(self._cfge_pages() - 1, page))

    def _cfge_page_step(self, delta: int) -> None:
        self._cfge_goto(self._cfge_page + delta)
        self._cfge_render()

    # ── rendering ──────────────────────────────────────────────────────────────

    def _cfge_render(self) -> None:
        """Draw the view that is up, header and footer included."""
        if self._pool is None:
            return
        p = self._pool
        p.hide_all()
        p.put("menu_cancel_btn", "CANCEL", on=self._cfge_cancel)
        p.put("cfge_title", self._cfge_title())
        p.put("menu_ok_btn", "OK", on=self._cfge_ok)
        if self._cfge_view == "roles":
            self._cfge_put_roles()
        elif self._cfge_view == "role":
            self._cfge_put_role()
        else:
            self._cfge_put_browse()
        self._cfge_put_footer()
        p.render(self._disp)
        self._rebuild_focus()

    def _cfge_title(self) -> str:
        """
        The header line, at most three lines in the 120 px between the corner
        buttons: what screen this is, then what it is showing.
        """
        if self._cfge_view == "roles":
            return "Config\nRoles"
        if self._cfge_view == "role":
            return f"Config\n{self._cfge_role}"
        return f"{self._cfge_role}\n{_cfge_fit(self._cfge_name)}"

    def _cfge_put_roles(self) -> None:
        from palette import color
        p = self._pool
        for i, role in enumerate(self._cfge_page_rows(), 1):
            row = f"cfge_row{i}"
            if role == _CFGE_RESTORE:
                p.put(row, "Restore Factory\nDefaults",
                      on=self._cfge_confirm_restore_all,
                      enabled=self._cfge_editable())
                p[row].color = color("label_coral")
            else:
                p.put(row, role, on=lambda r=role: self._cfge_enter_role(r))
                p[row].color = color("slot_b_bg")

    def _cfge_put_role(self) -> None:
        """
        Four rows of `name / value / Browse`.

        A parameter the table marks editable=0 — the serial numbers, the
        backlight pin — shows greyed and takes neither keystrokes nor a Browse:
        it is a record of how the instrument was built, not a setting.
        """
        p = self._pool
        editable = self._cfge_editable()
        self._cfge_start = {}
        for i, (name, _vtype, value, row_editable, _isdefault) in enumerate(
                self._cfge_page_rows(), 1):
            self._cfge_start[name] = value
            can_edit = editable and bool(row_editable)
            p.put(f"cfge_lbl{i}", name)
            # The field shows what was typed if an edit is being held over from
            # a modal or the Browse view, and the stored value otherwise.
            fld = p.put(f"cfge_fld{i}", self._cfge_hold.pop(name, value),
                        enabled=can_edit)
            # `editable` is what makes a TextBox take characters at all, and it
            # sets focusability; both have to be re-stated every render because
            # the four field widgets are shared with every other page and view.
            fld.editable  = can_edit
            fld.focusable = can_edit
            p.put(f"cfge_btn{i}", "Browse",
                  on=lambda n=name: self._cfge_open_browse(n),
                  enabled=bool(row_editable))
            self._cfge_paint_row(i, redraw=False)
        # Anything still held names a parameter on another page, which cannot
        # happen while paging is locked out — drop it rather than let it
        # reappear over an unrelated row later.
        self._cfge_hold = {}

    def _cfge_put_browse(self) -> None:
        """
        Four rows of one parameter's history: value, when and by whom it was
        first stored, and whether it is the row in force or the commissioned
        default.  Use puts it back in force — no typing involved, which is what
        makes this the editor a touch-only instrument gets.
        """
        from palette import color
        p = self._pool
        editable = self._cfge_editable()
        for i, (value, add_dt, source, active, isdefault) in enumerate(
                self._cfge_page_rows(), 1):
            marks = "  ".join(m for m, on in (("in force", active),
                                              ("default", isdefault)) if on)
            p.put(f"cfge_lbl{i}", f"{add_dt or '?'}  {source or '?'}  {marks}")
            fld = p.put(f"cfge_fld{i}", value, enabled=False)
            fld.editable  = False
            fld.focusable = False
            fld.color, fld.bg = color("black"), color("white")
            p.put(f"cfge_btn{i}", "Use",
                  on=lambda v=value: self._cfge_activate(v),
                  enabled=editable and not active)

    def _cfge_put_footer(self) -> None:
        """
        The five action buttons.  Paging is dead while an edit is pending —
        the field is the only place that edit exists, so paging away would
        discard it without saying so.
        """
        p = self._pool
        pending = bool(self._cfge_pending())
        self._cfge_footer_pending = pending
        p.put("cfge_prior", "< Prior", on=lambda: self._cfge_page_step(-1),
              enabled=self._cfge_page > 0 and not pending)
        p.put("cfge_rollback", "Rollbk", on=self._cfge_rollback, enabled=pending)
        p.put("cfge_save", "Save", on=self._cfge_save, enabled=pending)
        p.put("cfge_default", "Default", on=self._cfge_confirm_default,
              enabled=pending)
        p.put("cfge_next", "Next >", on=lambda: self._cfge_page_step(1),
              enabled=self._cfge_page < self._cfge_pages() - 1 and not pending)

    def _cfge_repaint_footer(self) -> None:
        """
        Repaint the five footer buttons after the pending-edit state flipped.

        Keyboard focus is preserved rather than rebuilt: the operator is typing
        in a field, and _rebuild_focus() would drop them back to the first
        widget mid-word.
        """
        keep = self._focused_widget()
        self._cfge_put_footer()
        for name in ("cfge_prior", "cfge_rollback", "cfge_save",
                     "cfge_default", "cfge_next"):
            self._pool[name].invalidate()
            self._pool.redraw(self._disp, name)
        self._focusables = self._pool.focusable_widgets()
        self._focus_idx  = (self._focusables.index(keep)
                            if keep in self._focusables else -1)

    def _cfge_paint_row(self, i: int, redraw: bool = True) -> None:
        """
        Colour one role-view field by its state: edited (red on yellow), fixed
        by the table (grey), or the value in force (black on white).  A no-op
        when the colour is already right, so most keystrokes cost no redraw.
        """
        from palette import color
        p    = self._pool
        fld  = p[f"cfge_fld{i}"]
        name = self._cfge_row_name(i)
        if name is None:
            return
        if not fld.editable:
            want = ("btn_gray", "white")
        elif str(fld.value) != self._cfge_start.get(name, ""):
            want = ("edited_text", "field_yellow")
        else:
            want = ("black", "white")
        fg, bg = color(want[0]), color(want[1])
        if (fld.color, fld.bg) == (fg, bg):
            return
        fld.color, fld.bg = fg, bg
        if redraw:
            fld.invalidate()
            p.redraw(self._disp, f"cfge_fld{i}")

    # ── edit state ─────────────────────────────────────────────────────────────

    def _cfge_row_name(self, i: int) -> "str | None":
        """The parameter name on body row i (1..4), or None if the row is empty."""
        rows = self._cfge_page_rows()
        return rows[i - 1][0] if 1 <= i <= len(rows) else None

    def _cfge_pending(self) -> "list[tuple[str, str]]":
        """
        (name, text) for every field on this page no longer showing the value
        it was loaded with.  Empty in the roles and browse views, which have
        nothing to edit.
        """
        if self._cfge_view != "role" or self._pool is None:
            return []
        out = []
        for i, row in enumerate(self._cfge_page_rows(), 1):
            fld  = self._pool[f"cfge_fld{i}"]
            text = str(fld.value)
            if fld.editable and text != self._cfge_start.get(row[0], text):
                out.append((row[0], text))
        return out

    def _cfge_writethrough(self, textbox) -> None:
        """
        React to a keystroke in one of the editor's fields.

        There is nothing to copy anywhere — the field itself is where the edit
        lives until Save.  What changes is the row's colour and whether the
        footer's write buttons are live.
        """
        if self._pool is None or not str(textbox.name).startswith("cfge_fld"):
            return
        self._cfge_paint_row(int(textbox.name[-1]))
        if bool(self._cfge_pending()) != self._cfge_footer_pending:
            self._cfge_repaint_footer()

    # ── writes ─────────────────────────────────────────────────────────────────

    def _cfge_rollback(self) -> None:
        """Put the fields back to the values they were loaded with."""
        self._cfge_render()

    def _cfge_save(self) -> None:
        """Put the edited values in force, keeping the previous ones as history."""
        self._cfge_write(config.save)

    def _cfge_set_default(self) -> None:
        """Put the edited values in force *and* commission them as the defaults."""
        self._cfge_write(config.set_default)

    def _cfge_write(self, writer) -> bool:
        """
        Validate every pending edit, then write them all with `writer`.

        Nothing is written unless all of them read back as their declared type,
        so a page of edits either lands together or not at all.

        Args:
            writer: config.save or config.set_default.

        Returns:
            True if the values were written; False if one was rejected, in
            which case a modal is up saying which.
        """
        pending = self._cfge_pending()
        vtypes  = {row[0]: row[1] for row in self._cfge_page_rows()}
        for name, text in pending:
            if not config.valid(text, vtypes[name]):
                log.error("config editor: %s.%s = %r is not a valid %s",
                          self._cfge_role, name, text, vtypes[name])
                # Hold the rejected text: the operator has to see it again to
                # correct it, and Rollback is right there if they would rather
                # start over.
                self._cfge_hold = dict(pending)
                self._cfge_warn(f"{name}\nis not a valid {vtypes[name]}")
                return False
        for name, text in pending:
            writer(self._cfge_role, name, text)
        self._cfge_hold = {}
        self._cfge_applied()
        self._cfge_list = config.items(self._cfge_role)
        self._cfge_render()
        return True

    def _cfge_activate(self, value: str) -> None:
        """Put a value from the Browse list back in force."""
        config.save(self._cfge_role, self._cfge_name, value)
        # Picking a value here settles this parameter — a half-typed one held
        # from the role view would otherwise reappear over what was just chosen.
        self._cfge_hold.pop(self._cfge_name, None)
        self._cfge_applied()
        self._cfge_list = config.history(self._cfge_role, self._cfge_name)
        self._cfge_render()

    def _cfge_applied(self) -> None:
        """
        Re-read everything the running program holds a copy of.

        Called after any write rather than only for the roles that matter, so
        no list of "settings with a consumer" has to be kept in step here —
        both calls are cheap and idempotent.

        Nothing has to be pushed back into the slots: a slot takes its identity
        fields from the defaults at Begin, not when its entry screen is first
        drawn, so an unclaimed slot is holding none of them and a claimed one
        must keep the values it was claimed with.
        """
        self._load_defaults()
        self._apply_reagent_secs()

    # ── confirms ───────────────────────────────────────────────────────────────

    def _cfge_confirm_default(self) -> None:
        names = ", ".join(name for name, _ in self._cfge_pending())
        self._cfge_modal(f"Commission as the\ndefault value?\n{names}",
                         self._cfge_set_default)

    def _cfge_confirm_restore_all(self) -> None:
        self._cfge_modal("Put every setting back\nto its factory value?",
                         self._cfge_restore_all)

    def _cfge_restore_all(self) -> None:
        count = config.restore_all()
        self._cfge_applied()
        log.info("config editor: %d parameter(s) put back to factory", count)
        # Restore sits at the end of the list, so this comes back to the last
        # page — the one the operator tapped it from.
        self._cfge_open_roles(self._cfge_page)

    def _cfge_modal(self, msg: str, on_ok) -> None:
        """A yes/no confirm over the editor; either answer returns to the view."""
        p = self._pool
        if p is None:
            return
        # Hold whatever is typed: the modal takes the fields off the screen,
        # and Cancel has to come back to the screen the operator left.
        self._cfge_hold = dict(self._cfge_pending())
        p.hide_all()
        p.put("modal_panel")
        p.put("modal_msg", msg)
        names = p.layout_modal_buttons(2)
        p.put(names[0], "OK",     on=on_ok)
        p.put(names[1], "Cancel", on=self._cfge_render)
        p.render(self._disp)
        self._rebuild_focus()

    def _cfge_warn(self, msg: str) -> None:
        """A one-button modal explaining why a write was refused."""
        p = self._pool
        if p is None:
            return
        p.hide_all()
        p.put("modal_panel")
        p.put("modal_msg", msg)
        names = p.layout_modal_buttons(1)
        p.put(names[0], "OK", on=self._cfge_render)
        p.render(self._disp)
        self._rebuild_focus()

    # ── leaving ────────────────────────────────────────────────────────────────

    def _cfge_ok(self) -> None:
        """Save anything typed, then step back one level."""
        if self._cfge_view == "role" and self._cfge_pending():
            if not self._cfge_write(config.save):
                return                     # rejected — the warning modal is up
        self._cfge_back()

    def _cfge_cancel(self) -> None:
        """Discard anything typed and step back one level."""
        self._cfge_back()

    def _cfge_back(self) -> None:
        """Browse → role → roles → the Menu, each onto the page it was left on."""
        if self._cfge_view == "browse":
            self._cfge_open_role(self._cfge_role, self._cfge_names_page)
        elif self._cfge_view == "role":
            self._cfge_open_roles(self._cfge_roles_page)
        else:
            self._cfge_view = None
            self._render_menu()

    # ── LED Color picker (Menu → LED Colors) ────────────────────────────────────
    # Edits capture.led_color_order without free-text typing: an active row
    # (the sequence, in capture order, plus the fixed 'dark'/'jpg' awareness
    # markers) and an inactive row (every LED not currently in the sequence,
    # each in its own fixed wavelength-order slot). A tap selects a swatch;
    # the four arrows between the rows move it. Nothing reaches the database
    # until Save — the footer's Prior/Rollbk/Save/Default/Next follow the
    # config editor's pending-edit rules exactly (_cfge_put_footer above),
    # reusing its five widgets since the two screens never share the display.

    def _menu_open_ledpick(self) -> None:
        """Menu → LED Colors: open the picker on the order now in force."""
        self._ledpick_history = config.capture_led_color_order_history()
        self._ledpick_colors  = list(config.capture_led_color_order())
        self._ledpick_idx     = self._ledpick_history_index(
            ",".join(self._ledpick_colors))
        self._ledpick_selected = None
        self._ledpick_render()

    def _ledpick_history_index(self, value: str) -> int:
        """Position of `value` in _ledpick_history, or 0 if it is not there."""
        for i, (_rowid, v) in enumerate(self._ledpick_history):
            if v == value:
                return i
        return 0

    def _ledpick_pending(self) -> bool:
        """True if the staged sequence differs from the value in force."""
        return ",".join(self._ledpick_colors) != config.get("capture", "led_color_order")

    def _ledpick_editable(self) -> bool:
        """False while a slot is mid-protocol — a live sample already read this."""
        return not self._any_slot_active()

    # ── selection + edits (nothing here touches the database) ──────────────────

    def _ledpick_select(self, led: str) -> None:
        """A tap on a swatch: select it, or deselect if it was already selected."""
        self._ledpick_selected = None if self._ledpick_selected == led else led
        self._ledpick_render()

    def _ledpick_add(self) -> None:
        """▲ : append the selected inactive color to the end of the sequence."""
        sel = self._ledpick_selected
        if sel is None or sel in self._ledpick_colors:
            return
        self._ledpick_colors.append(sel)
        self._ledpick_render()

    def _ledpick_remove(self) -> None:
        """▼ : drop the selected active color out of the sequence."""
        sel = self._ledpick_selected
        if sel is None or sel not in self._ledpick_colors:
            return
        self._ledpick_colors.remove(sel)
        self._ledpick_selected = None
        self._ledpick_render()

    def _ledpick_move(self, delta: int) -> None:
        """◀ / ▶ : swap the selected active color with its neighbour."""
        sel = self._ledpick_selected
        if sel is None or sel not in self._ledpick_colors:
            return
        i = self._ledpick_colors.index(sel)
        j = i + delta
        if not 0 <= j < len(self._ledpick_colors):
            return
        self._ledpick_colors[i], self._ledpick_colors[j] = \
            self._ledpick_colors[j], self._ledpick_colors[i]
        self._ledpick_render()

    # ── paging + writes ──────────────────────────────────────────────────────

    def _ledpick_goto(self, idx: int) -> None:
        """Load history[idx] as the staged sequence (a clean baseline)."""
        self._ledpick_idx = idx
        value = self._ledpick_history[idx][1] if self._ledpick_history else ""
        self._ledpick_colors = [c.strip() for c in value.split(",") if c.strip()]
        self._ledpick_selected = None
        self._ledpick_render()

    def _ledpick_page_step(self, delta: int) -> None:
        if self._ledpick_pending():
            return
        idx = max(0, min(len(self._ledpick_history) - 1, self._ledpick_idx + delta))
        self._ledpick_goto(idx)

    def _ledpick_default(self) -> None:
        """Jump to the commissioned order, like Prior/Next a browse — no write."""
        if self._ledpick_pending():
            return
        default_value = config.default("capture", "led_color_order")
        if default_value is None:
            return
        self._ledpick_goto(self._ledpick_history_index(str(default_value)))

    def _ledpick_rollback(self) -> None:
        """Put the sequence back to the value it was loaded/paged to."""
        self._ledpick_goto(self._ledpick_idx)

    def _ledpick_save(self) -> None:
        """Put the staged sequence in force, reusing a matching history row."""
        config.save("capture", "led_color_order", ",".join(self._ledpick_colors))
        self._ledpick_history = config.capture_led_color_order_history()
        self._ledpick_idx = self._ledpick_history_index(",".join(self._ledpick_colors))
        self._ledpick_selected = None
        self._ledpick_render()

    def _ledpick_ok(self) -> None:
        """Save any pending edit, then return to the Menu."""
        if self._ledpick_pending():
            self._ledpick_save()
        self._render_menu()

    def _ledpick_cancel(self) -> None:
        """Discard any pending edit and return to the Menu."""
        self._render_menu()

    # ── rendering ──────────────────────────────────────────────────────────────

    def _ledpick_render(self) -> None:
        if self._pool is None:
            return
        p = self._pool
        p.hide_all()
        p.put("menu_cancel_btn", "CANCEL", on=self._ledpick_cancel)
        p.put("ledpick_title", "LED\nColors")
        p.put("menu_ok_btn", "OK", on=self._ledpick_ok)
        self._ledpick_put_rows()
        self._ledpick_put_arrows()
        self._ledpick_put_footer()
        self._ledpick_put_value()
        p.render(self._disp)
        self._rebuild_focus()

    def _ledpick_put_rows(self) -> None:
        """
        Active row: the staged sequence, then the fixed 'dark'/'jpg' markers,
        one widget slot each, trailing slots hidden. Inactive row: the 10 LEDs
        in fixed wavelength-order slots, a slot hidden when that LED is
        currently active — each LED shows in exactly one row at a time.
        """
        from palette import color, rgb
        from lighting_cal8 import LEDBank
        p = self._pool
        editable = self._ledpick_editable()
        sequence = list(self._ledpick_colors) + list(_LEDPICK_SPECIALS)
        for i in range(1, 13):
            name = f"ledpick_act{i}"
            if i - 1 >= len(sequence):
                p.hide(name)
                continue
            led = sequence[i - 1]
            special = led in _LEDPICK_SPECIALS
            fill_rgb, text_name = _LEDPICK_SWATCH.get(led, ((128, 128, 128), "white"))
            p.put(name, led if special else _led_initials(led),
                  on=(lambda: None) if special
                     else (lambda l=led: self._ledpick_select(l)),
                  enabled=editable and not special)
            w = p[name]
            w.color = w.disabled_color = rgb(*fill_rgb)
            w.text_color = color(text_name)
            w.border_color = color("roi_selected") if led == self._ledpick_selected else None
        for i, led in enumerate(LEDBank.color_names(), 1):
            name = f"ledpick_ina{i}"
            if led in self._ledpick_colors:
                p.hide(name)
                continue
            fill_rgb, text_name = _LEDPICK_SWATCH[led]
            p.put(name, _led_initials(led),
                  on=lambda l=led: self._ledpick_select(l),
                  enabled=editable)
            w = p[name]
            w.color = w.disabled_color = rgb(*fill_rgb)
            w.text_color = color(text_name)
            w.border_color = color("roi_selected") if led == self._ledpick_selected else None

    def _ledpick_put_arrows(self) -> None:
        p = self._pool
        editable = self._ledpick_editable()
        sel = self._ledpick_selected
        is_active   = sel is not None and sel in self._ledpick_colors
        is_inactive = sel is not None and not is_active
        p.put("ledpick_arr_left",  "◀", on=lambda: self._ledpick_move(-1),
              enabled=editable and is_active)
        p.put("ledpick_arr_up",    "▲", on=self._ledpick_add,
              enabled=editable and is_inactive)
        p.put("ledpick_arr_down",  "▼", on=self._ledpick_remove,
              enabled=editable and is_active)
        p.put("ledpick_arr_right", "▶", on=lambda: self._ledpick_move(1),
              enabled=editable and is_active)

    def _ledpick_put_footer(self) -> None:
        """The config editor's five footer widgets, restyled for this screen."""
        p = self._pool
        editable = self._ledpick_editable()
        pending = self._ledpick_pending()
        p.put("cfge_prior", "< Prior", on=lambda: self._ledpick_page_step(-1),
              enabled=editable and not pending and self._ledpick_idx > 0)
        p.put("cfge_rollback", "Rollbk", on=self._ledpick_rollback,
              enabled=editable and pending)
        p.put("cfge_save", "Save", on=self._ledpick_save,
              enabled=editable and pending)
        p.put("cfge_default", "Default", on=self._ledpick_default,
              enabled=editable and not pending)
        p.put("cfge_next", "Next >", on=lambda: self._ledpick_page_step(1),
              enabled=editable and not pending
                      and self._ledpick_idx < len(self._ledpick_history) - 1)

    def _ledpick_put_value(self) -> None:
        """The rowid and text of the sequence on screen, yellow while unsaved."""
        from palette import color
        p = self._pool
        value = ",".join(self._ledpick_colors)
        rowid = next((rid for rid, v in self._ledpick_history if v == value), None)
        p.put("ledpick_rowid", f"rowid {rowid}" if rowid else "rowid (unsaved)")
        line1, line2 = _wrap_csv(value, 46)
        p.put("ledpick_value1", line1)
        p.put("ledpick_value2", line2)
        bg = color("field_yellow") if self._ledpick_pending() else color("white")
        for name in ("ledpick_rowid", "ledpick_value1", "ledpick_value2"):
            p[name].bg = bg

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
        self._cap_group = self._initial_exposure_group()
        self._cap_led = None
        self._cap_hint_msg = ""
        self._cap_fit = None
        self._cap_running = None
        self._cap_stage_from_config()
        self._render_capture_choice()

    def _initial_exposure_group(self) -> str:
        """
        The exposure group to preselect when the capture screen opens: the value
        in force for `defaults.exposuregroup`.

        The operator's saved choice is honoured whether or not the exposure
        table currently has rows for that group — preselecting anything else
        would silently override what they picked (the previous behaviour, which
        made the screen always reopen on the fallback group after selecting an
        uncalibrated one). A Normal capture on an uncalibrated group simply
        finds nothing to capture; that is handled at capture time, not by
        second-guessing the preselection here.

        A fresh instrument has never chosen one, and gets the seeded value; the
        Default button on the config editor puts that back.
        """
        try:
            return config.get("defaults", "exposuregroup")
        except config.ConfigMissing:
            log.exception("Arbiter: no saved exposure group")
            return "1"

    # The four window settings on the choice form: field key -> (config row,
    # kind, help).  Staged in the fields; written to the database by SAVE.
    _CAP_SETTINGS = (
        ("low_dn",  "window_low_dn",   int,
         "Darkest signal a bracket may give [0 to 960]"),
        ("high_dn", "window_high_dn",  int,
         "Lightest signal a bracket may give [0 to 960]"),
        ("low_pc",  "window_low_pctl", float,
         "Percentile of a patch's pixels held to Low DN"),
        ("high_pc", "window_high_pctl", float,
         "Percentile of a patch's pixels held to High DN"),
    )

    # The Bayer layer swatches' colors, and the text color that reads on each.
    _CAP_LAYER_SWATCH = {"R": ((213, 0, 0), "white"), "Gr": ((0, 170, 0), "white"),
                         "Gb": ((0, 110, 90), "white"), "B": ((0, 0, 255), "white")}

    def _cap_stage_from_config(self) -> None:
        """
        Stage everything the form edits from the database: the global window,
        each LED's own window (if it has one), and each LED's required layers.
        Nothing reaches the database until SAVE.
        """
        import required_cells
        from lighting_cal8 import LEDBank
        self._cap_global = {
            key: str(config.get("calibration", row))
            for key, row, *_ in self._CAP_SETTINGS}
        self._cap_over = {
            led: self._cap_values_of(w)
            for led, w in config.calibration_led_windows().items()}
        cells = config.calibration_required_cells()
        self._cap_layers = {
            led: list(required_cells.required_channels(led, cells))
            for led in LEDBank.color_names()}
        self._cap_layers0 = {led: list(v) for led, v in self._cap_layers.items()}
        self._cap_values = dict(self._cap_global)

    @staticmethod
    def _cap_values_of(w) -> "dict[str, str]":
        """A LedWindow as the form's field texts."""
        return {"low_dn": str(w.low_dn), "high_dn": str(w.high_dn),
                "low_pc": f"{w.low_pctl:g}", "high_pc": f"{w.high_pctl:g}"}

    def _render_capture_choice(self) -> None:
        if self._pool is None:
            return
        from palette import color, rgb
        from lighting_cal8 import LEDBank
        if self._cap_view == "choice":
            self._cap_read_fields()          # keep text typed since the last draw
        self._cap_view = "choice"
        p = self._pool
        p.hide_all()
        # Top row: CANCEL / title / SAVE, in the Menu screen's corners.
        p["cap_title"].rect = layout.MENU_TITLE
        p.put("menu_cancel_btn", "CANCEL", on=self._cap_choice_cancel)
        p.put("cap_title", "Capture")
        p.put("menu_ok_btn",     "SAVE",   on=self._cap_choice_save)
        # The LED bar: tap a swatch to choose the color for Calibrate color
        # and Show.  The chosen one carries a border.
        for i, led in enumerate(LEDBank.color_names(), 1):
            name = f"cap_led{i}"
            fill_rgb, text_name = _LEDPICK_SWATCH[led]
            p.put(name, _led_initials(led), on=lambda l=led: self._cap_pick_led(l))
            w = p[name]
            w.color = w.disabled_color = rgb(*fill_rgb)
            w.text_color = color(text_name)
            # Black: the chosen LED, or the one being calibrated now.
            w.border_color = (color("black")
                              if led in (self._cap_led, self._cap_running)
                              else None)
        # The Bayer layers the chosen LED must pass: lit = required.
        shown = self._cap_led or self._cap_running
        for i, layer in enumerate(("R", "Gr", "Gb", "B"), 1):
            name = f"cap_layer{i}"
            on = shown is not None and layer in self._cap_layers[shown]
            p.put(name, layer, on=lambda l=layer: self._cap_toggle_layer(l),
                  enabled=self._cap_led is not None)
            w = p[name]
            fill_rgb, text_name = self._CAP_LAYER_SWATCH[layer]
            w.color = rgb(*fill_rgb) if on else color("btn_gray")
            w.disabled_color = w.color
            w.text_color = color(text_name) if on else color("black")
            w.border_color = color("black") if on else None
        for key, _row, _kind, help_text in self._CAP_SETTINGS:
            p.put(f"cap_lbl_{key}", p[f"cap_lbl_{key}"].label,
                  on=lambda t=help_text: self._cap_hint(t))
            p.put(f"cap_fld_{key}", self._cap_values[key])
        problem = self._cap_problem()
        settings_ok = problem is None
        if not settings_ok:
            self._cap_hint_msg = problem
        has_layers = (self._cap_led is not None
                      and bool(self._cap_layers[self._cap_led]))
        p.put("cap_suite", "Calibrate\nsuite", on=self._cap_calibrate_suite,
              enabled=settings_ok)
        p.put("cap_color", "Calibrate\ncolor", on=self._cap_calibrate_color,
              enabled=settings_ok and has_layers)
        p.put("cap_show",  "Show", on=self._cap_show,
              enabled=settings_ok and has_layers)
        p.put("cap_pad", "Pad\nareas", on=self._cap_pad_areas,
              enabled=not self._any_slot_active() and self._capture is None)
        p.put("cap_hint", self._cap_hint_text())
        p.render(self._disp)
        self._rebuild_focus()

    def _cap_hint_text(self) -> str:
        if self._cap_hint_msg:
            return self._cap_hint_msg
        if self._cap_led is None:
            return "Tap an LED color"
        return (f"{self._cap_led}: " + (" ".join(self._cap_layers[self._cap_led])
                                        or "tap a Bayer layer"))

    def _cap_hint(self, text: str) -> None:
        """Show a line of help (or status) under the settings."""
        self._cap_hint_msg = text
        if self._pool is not None and self._cap_view == "choice":
            self._pool.set("cap_hint", text)
            self._pool.redraw(self._disp, "cap_hint")

    def _cap_write_fields(self) -> None:
        """Show the staged values in the boxes."""
        if self._pool is None:
            return
        for key, *_ in self._CAP_SETTINGS:
            field = self._pool.get(f"cap_fld_{key}")
            if field is not None:
                field.value = self._cap_values[key]

    def _cap_read_fields(self) -> None:
        """Keep what the fields now show: they are editable by keyboard."""
        if self._pool is None:
            return
        for key, *_ in self._CAP_SETTINGS:
            field = self._pool.get(f"cap_fld_{key}")
            if field is not None:
                self._cap_values[key] = str(field.value).strip()

    def _cap_parse(self, values: "dict[str, str]") -> "dict[str, float] | str":
        """Field texts as {config row: number}, or a message saying what is wrong."""
        out = {}
        labels = {"low_dn": "Low DN", "high_dn": "High DN",
                  "low_pc": "Low %", "high_pc": "High %"}
        for key, row, kind, _help in self._CAP_SETTINGS:
            try:
                out[row] = kind(float(values[key]))
            except ValueError:
                return f"{labels[key]}: not a number"
        for row in ("window_low_dn", "window_high_dn"):
            if not 0 <= out[row] <= _CAP_MAX_DN:
                return f"DN must be 0 to {_CAP_MAX_DN}"
        if not out["window_low_dn"] < out["window_high_dn"]:
            return "Low DN must be below High DN"
        if not 0 <= out["window_low_pctl"] < out["window_high_pctl"] <= 100:
            return "Percentiles: 0 <= low < high <= 100"
        return out

    def _cap_problem(self) -> "str | None":
        """What is wrong with the settings now in the fields, or None."""
        self._cap_read_fields()
        parsed = self._cap_parse(self._cap_values)
        return parsed if isinstance(parsed, str) else None

    def _cap_commit(self) -> None:
        """Store the fields into the global window, or the chosen LED's own."""
        self._cap_read_fields()
        if self._cap_led is None:
            self._cap_global = dict(self._cap_values)
        elif self._cap_values == self._cap_global:
            self._cap_over.pop(self._cap_led, None)
        else:
            self._cap_over[self._cap_led] = dict(self._cap_values)

    def _cap_pick_led(self, led: str) -> None:
        """Choose an LED (or clear it): the boxes then show that LED's window."""
        problem = self._cap_problem()
        if problem:                          # do not leave a bad window behind
            self._cap_hint(problem)
            return
        self._cap_commit()
        self._cap_led = None if self._cap_led == led else led
        self._cap_hint_msg = ""
        self._cap_values = dict(self._cap_over.get(self._cap_led, self._cap_global))
        self._cap_write_fields()
        self._render_capture_choice()

    def _cap_toggle_layer(self, layer: str) -> None:
        """Add or drop a Bayer layer from the chosen LED's required layers."""
        led = self._cap_led
        if led is None:
            return
        have = set(self._cap_layers[led]) ^ {layer}
        self._cap_layers[led] = [l for l in ("R", "Gr", "Gb", "B") if l in have]
        self._cap_hint_msg = ""
        self._cap_read_fields()
        self._render_capture_choice()

    def _cap_run_settings(self) -> dict:
        """Every LED's staged layers and window, for the fit runner."""
        import exposure_fit_runner as efr
        import required_cells
        out = {}
        for led, layers in self._cap_layers.items():
            parsed = self._cap_parse(self._cap_over.get(led, self._cap_global))
            if not layers:                    # nothing chosen: its default channel
                layers = list(required_cells.required_channels(led, []))
            out[led] = efr.LedSettings(tuple(layers), required_cells.LedWindow(
                parsed["window_low_dn"], parsed["window_high_dn"],
                parsed["window_low_pctl"], parsed["window_high_pctl"]))
        return out

    def _cap_choice_save(self) -> None:
        """Write the staged settings and any finished fit, then leave to the Menu."""
        import required_cells
        problem = self._cap_problem()
        if problem:
            self._cap_hint(problem)
            return
        self._cap_commit()
        global_rows = self._cap_parse(self._cap_global)
        for row, value in global_rows.items():
            if value != config.get("calibration", row):
                config.save("calibration", row, value)
        windows = {}
        for led, values in self._cap_over.items():
            w = self._cap_parse(values)
            windows[led] = required_cells.LedWindow(
                w["window_low_dn"], w["window_high_dn"],
                w["window_low_pctl"], w["window_high_pctl"])
        text = required_cells.format_windows(windows)
        if text != str(config.get("calibration", "led_windows")):
            config.save("calibration", "led_windows", text)
        cells = config.calibration_required_cells()
        for led, layers in self._cap_layers.items():
            if layers != self._cap_layers0[led]:
                cells = required_cells.with_layers(cells, led, layers)
        text = required_cells.format_cells(cells)
        if text != str(config.get("calibration", "required_cells")):
            config.save("calibration", "required_cells", text)
        if self._cap_fit is not None:
            rows = self._cap_fit.persist(add_dt=self.timestamp())
            log.info("Arbiter: stored %d calibrated exposure(s)", rows)
            self._cap_fit = None
        self._cap_view = None
        self._render_menu()

    def _cap_choice_cancel(self) -> None:
        """Leave to the Menu; nothing staged on this screen is written."""
        if self._cap_run_mode == "fit" and self._capture is not None:
            self._capture.cancel()
            self._capture = None
            self._capture_slot = None
        self._cap_fit = None
        self._cap_running = None
        self._cap_view = None
        self._render_menu()

    def _cap_calibrate_suite(self) -> None:
        self._cap_start_fit(None)

    def _cap_calibrate_color(self) -> None:
        self._cap_start_fit(self._cap_led)

    def _cap_start_fit(self, only: "str | None") -> None:
        """
        Run the exposure fit on the camera: every LED when `only` is None.

        The fit uses the layers and windows now staged, saved or not.  Nothing
        is stored until SAVE.  The run happens in the background of this
        screen: the fit repaints the LED bar and the hint line itself.
        """
        problem = self._cap_problem()
        if problem:
            self._cap_hint(problem)
            return
        if not self.capture_available():
            self._cap_hint("Camera busy - try again later")
            return
        self._cap_commit()
        if only is None:                    # a suite run is not about one LED
            self._cap_led = None
            self._cap_values = dict(self._cap_global)
            self._cap_write_fields()
        settings = self._cap_run_settings()
        import exposure_fit_runner
        from capture_runner import CaptureConfig

        def make_runner(cam, leds, cfg):
            return exposure_fit_runner.ExposureFitRunner(
                cam, leds, cfg, only=only, settings=settings,
                on_status=self._cap_fit_status)

        session = self._new_session(CaptureConfig(), runner_factory=make_runner)
        if not session.start():
            log.error("Arbiter: calibration failed to start: %s", session.error)
            self._cap_hint(f"Camera failed: {session.error}"[:48])
            return
        self._capture = session
        self._capture_slot = None
        self._cap_run_mode = "fit"
        self._cap_fit = None
        self._cap_running = None
        self._cap_hint("Starting camera ...")
        log.info("Arbiter: calibration started (%s)", only or "suite")

    def _cap_fit_status(self, led: str, text: str) -> None:
        """A fit hook: highlight `led` in the bar and show `text` above the buttons."""
        changed = led != self._cap_running
        self._cap_running = led
        if changed and self._pool is not None and self._cap_view == "choice":
            self._render_capture_choice()
        self._cap_hint(text)

    def _pump_fit(self, session) -> None:
        """Drive a running fit; the hooks do the repainting."""
        if session.step() is not None:
            return
        self._capture = None
        self._capture_slot = None
        self._cap_running = None
        runner = session.runner
        if session.error is not None:
            self._cap_hint(f"Calibration failed: {session.error}"[:48])
        else:
            self._cap_fit = runner
            good = sum(1 for f in runner.fits.values() if f.ok)
            bad = next((f for f in runner.fits.values() if not f.ok), None)
            if bad is None:
                spread = sum(1 for f in runner.fits.values() if f.spread)
                note = f" {spread} spread." if spread else ""
                self._cap_hint(f"{good} of {len(runner.fits)} fitted.{note} SAVE to keep.")
            else:
                self._cap_hint(bad.short_reason()[:60])
        if self._cap_view == "choice":
            self._render_capture_choice()

    def _cap_pad_areas(self) -> None:
        """
        Open the image viewer on the test pad's three patches.

        Any patch not yet placed is first created from the sample region: the
        top third is yellow, the middle brown, the bottom white.  Exit returns
        here, with the settings as they were.
        """
        import roi
        if not self.capture_available():
            self._cap_hint("Camera busy - try again later")
            return
        self._cap_read_fields()
        created = roi.init_test_pad_from_sample()
        if len(roi.load_test_pad()) < len(roi.TEST_PAD_ROLES):
            self._cap_hint("No sample region to start the pad from")
            return
        if created:
            log.info("Arbiter: test pad patches created: %s", created)
        self._cap_view = None            # the boxes were read; the viewer owns the screen
        self.open_image_view(slot=None, pad=True)

    def _view_fit_pad(self) -> None:
        """Zoom the viewer to the three patches together."""
        from rect import Rect
        rs = list(self._view_rois.values())
        x0, y0 = min(r.x for r in rs), min(r.y for r in rs)
        x1, y1 = max(r.x2() for r in rs), max(r.y2() for r in rs)
        self._view_state.fit_to(Rect(x0, y0, x1 - x0, y1 - y0, name="pad"))

    # ── Show screen (Capture page → Show) ──────────────────────────────────────
    # Photographs the whole pad (the sample region) with the chosen LED and
    # plots the pad pixels' sorted signal against the LED's window.  "One
    # exposure" takes one frame and plots all four Bayer layers; "One layer" takes a frame at each of the
    # LED's stored exposures and plots that layer at each.  Nothing is stored.

    def _cap_show(self) -> None:
        """Open the Show screen for the chosen LED."""
        led = self._cap_led
        if led is None:
            return
        from capture_runner import CaptureConfig
        self._cap_commit()
        self._show_led = led
        self._show_exps = (exposure_repo.read_led_exposures(led)
                           or [CaptureConfig().initial_us])
        self._show_i = len(self._show_exps) // 2
        self._show_mode = "exposure"
        self._show_layer = self._cap_layers[led][0] if self._cap_layers[led] else "B"
        self._show_hint_msg = "Tap Capture"
        self._show_image = None
        self._render_show()

    def _show_values(self) -> "tuple[list[int], list[str]]":
        """(exposures, layers) the next Capture will photograph and plot."""
        if self._show_mode == "exposure":
            return [self._show_exps[self._show_i]], ["R", "Gr", "Gb", "B"]
        return list(self._show_exps), [self._show_layer]

    def _render_show(self) -> None:
        if self._pool is None:
            return
        from palette import color
        sel, unsel = color("start_green"), color("btn_gray")
        self._cap_view = "show"
        p = self._pool
        p.hide_all()
        busy = self._capture is not None
        p["cap_title"].rect = layout.MENU_TITLE
        p.put("menu_cancel_btn", "BACK", on=self._show_back)
        p.put("cap_title", f"Show\n{self._show_led}")
        p.put("menu_ok_btn", "Capture", on=self._show_capture, enabled=not busy)
        exp_mode = self._show_mode == "exposure"
        p.put("show_mode_exp", "One exposure", on=lambda: self._show_set_mode("exposure"))
        p["show_mode_exp"].color = sel if exp_mode else unsel
        p.put("show_mode_layer", "One layer", on=lambda: self._show_set_mode("layer"))
        p["show_mode_layer"].color = unsel if exp_mode else sel
        if exp_mode:
            n = len(self._show_exps)
            p.put("show_prev", "◀", on=lambda: self._show_step(-1),
                  enabled=not busy and self._show_i > 0)
            p.put("show_value",
                  f"{self._show_exps[self._show_i]} us  ({self._show_i + 1} of {n})")
            p.put("show_next", "▶", on=lambda: self._show_step(1),
                  enabled=not busy and self._show_i < n - 1)
        else:
            for i, layer in enumerate(("R", "Gr", "Gb", "B"), 1):
                p.put(f"show_layer{i}", layer,
                      on=lambda l=layer: self._show_pick_layer(l), enabled=not busy)
                p[f"show_layer{i}"].color = sel if layer == self._show_layer else unsel
        p.put("show_hint", self._show_hint_msg)
        p.put("show_canvas")
        p["show_canvas"].set_image(self._show_image)
        p.render(self._disp)
        self._rebuild_focus()

    def _show_hint(self, text: str) -> None:
        self._show_hint_msg = text
        if self._pool is not None and self._cap_view == "show":
            self._pool.set("show_hint", text)
            self._pool.redraw(self._disp, "show_hint")

    def _show_set_mode(self, mode: str) -> None:
        self._show_mode = mode
        self._show_image = None
        self._show_hint_msg = "Tap Capture"
        self._render_show()

    def _show_step(self, delta: int) -> None:
        self._show_i = max(0, min(len(self._show_exps) - 1, self._show_i + delta))
        self._show_image = None
        self._render_show()

    def _show_pick_layer(self, layer: str) -> None:
        self._show_layer = layer
        self._show_image = None
        self._render_show()

    def _show_back(self) -> None:
        """Back to the Capture page, abandoning any capture under way."""
        if self._cap_run_mode == "show" and self._capture is not None:
            self._capture.cancel()
            self._capture = None
            self._capture_slot = None
        self._render_capture_choice()

    def _show_capture(self) -> None:
        """Photograph the pad at the chosen exposure(s) and plot the result."""
        if not self.capture_available():
            self._show_hint("Camera busy - try again later")
            return
        import exposure_show
        from capture_runner import CaptureConfig
        exposures, layers = self._show_values()
        led = self._show_led

        def make_runner(cam, leds, cfg):
            return exposure_show.ExposureShowRunner(
                cam, leds, led, exposures, layers, cfg,
                on_status=lambda _led, text: self._show_hint(text))

        session = self._new_session(CaptureConfig(), runner_factory=make_runner)
        if not session.start():
            log.error("Arbiter: show failed to start: %s", session.error)
            self._show_hint(f"Camera failed: {session.error}"[:48])
            return
        self._capture = session
        self._capture_slot = None
        self._cap_run_mode = "show"
        self._show_image = None
        self._show_hint("Starting camera ...")
        self._render_show()

    def _pump_show(self, session) -> None:
        """Drive a running Show capture; plot it when it ends."""
        if session.step() is not None:
            return
        self._capture = None
        self._capture_slot = None
        if session.error is not None:
            self._show_hint(f"Capture failed: {session.error}"[:48])
            self._show_image = None
        else:
            self._show_image = self._show_plot(session.runner.curves)
            self._show_hint("Pad pixels, dark to light; window in magenta")
        if self._cap_view == "show":
            self._render_show()

    def _show_plot(self, curves):
        """The curves as an image the size of the canvas."""
        import colorsys
        import show_plot
        window = self._cap_run_settings()[self._show_led].window
        if self._show_mode == "exposure":
            rgbs = {l: self._CAP_LAYER_SWATCH[l][0] for l in ("R", "Gr", "Gb", "B")}
            drawn = [(rgbs[c.layer], c.signal) for c in curves]
        else:
            n = max(len(curves) - 1, 1)
            drawn = []
            for i, c in enumerate(curves):      # blue (shortest) to red (longest)
                r, g, b = colorsys.hsv_to_rgb(0.66 * (1 - i / n), 1.0, 0.85)
                drawn.append(((int(r * 255), int(g * 255), int(b * 255)), c.signal))
        r = layout.SHOW_CANVAS
        return show_plot.render(drawn, (r.w, r.h), window.low_dn, window.high_dn)

    def _pump_capture_screen(self, session) -> None:
        """Drive a menu-launched fit or Show capture."""
        if self._cap_run_mode == "show":
            self._pump_show(session)
        else:
            self._pump_fit(session)

    # ── Phase contract: modals ─────────────────────────────────────────────────

    def open_modal(self, slot: Slot, modal_id: str) -> None:
        self._flush_entry(slot)
        slot.modal_return = slot.phase
        slot.phase = modal_id
        self.render(slot)

    def close_modal(self, slot: Slot) -> None:
        slot.phase, slot.modal_return = (slot.modal_return or "entry"), None
        self.render(slot)

    # ── Phase contract: capture / analysis ─────────────────────────────────────

    def capture_available(self) -> bool:
        return self._capture is None

    def _any_slot_active(self) -> bool:
        """True while slot A or B is past entry — a sample is live in it.

        Editable configuration (reagent seconds, ROI geometry, defaults)
        must not change out from under a slot that has already read it, so
        every editor screen gates its editable state on this.
        """
        return any(s.active_phase not in ("entry", "entry2")
                   for s in self._slots.values())

    @property
    def cap_mode(self) -> str:
        """Current Menu -> Capture path selection: 'calibration' | 'normal'."""
        return self._cap_mode

    def _measurement_config(self, slot: Slot):
        """
        Build the CaptureConfig for `slot`'s measurement sweep.

        One builder for both the prefetched and the fully-synchronous route,
        so a run cannot get a different archive or a different path depending
        on whether its reagent2 countdown left time to prefetch.

        Uses the Menu -> Capture screen's staged path + images choice, same as
        the standalone menu capture — a sample's Read-phase capture must not
        silently fall back to a full calibration sweep.

        When the slot has already been pre-imaged its archive exists, and this
        run adds to it rather than opening a second file: one strip, one .h5.
        The file is checked for rather than trusted, because the record can
        outlive it — an archive deleted or moved between the two sweeps must
        cost the pre-image, never the reading.
        """
        from capture_runner import CaptureConfig
        pre = str(slot.record.get("h5file") or "")
        archive = Path(pre) if pre and Path(pre).exists() else None
        if pre and archive is None:
            log.warning("Arbiter: slot %s pre-image archive %s is gone — "
                        "the measurement opens a new one", slot.name, pre)
        return CaptureConfig(mode=self._cap_mode, exposure_group=self._cap_group,
                             sample_rowid=slot.record.get("db_rowid"),
                             archive_path=archive)

    def _preimage_config(self, slot: Slot):
        """
        Build the CaptureConfig for `slot`'s pre-image.

        Everything that makes this run different from the measurement is
        configuration, not code: which LEDs, how far down the exposure ladder,
        and the mode that turns off the closing white-LED photograph.  See
        capture_runner's MODE_PRE.

        Group '1' — one exposure per LED.  Extra rungs would buy nothing on an
        undyed pad (the longer ones clip) and cost light on a strip that has
        not reacted yet.
        """
        from capture_runner import CaptureConfig, MODE_PRE
        return CaptureConfig(mode=MODE_PRE, exposure_group="1",
                             led_colors=config.capture_preimage_led_order(),
                             sample_rowid=slot.record.get("db_rowid"))

    def request_preimage(self, slot: Slot) -> bool:
        """
        Photograph the unreacted strip for `slot`.  Returns granted.

        The pre-image opens the sample's archive; the measurement sweep later
        adds to the same file.  Unlike the measurement it is never prefetched
        — the operator has just seated the strip and is standing there, and
        there is no countdown to hide the setup behind.

        Returns:
            True if the camera was free and the run started, False if it was
            busy — in which case the phase leaves its button as it was and the
            operator can press again.
        """
        if self._capture is not None:
            log.info("Arbiter: pre-image denied — camera busy (slot %s)",
                     self._capture_slot.name if self._capture_slot else "?")
            return False
        # A parked prefetch belongs to some other slot's reading; this run is
        # about to drive the shared camera, so it cannot be left to resume.
        if self._prefetch is not None:
            self._prefetch.cancel()
            self._prefetch = None
            self._prefetch_slot = None
            self._prefetch_epoch = None

        session = self._new_session(self._preimage_config(slot))
        if not session.start():
            log.error("Arbiter: pre-image failed to start: %s", session.error)
            slot.progress = "Camera failed"
            return False
        self._capture = session
        self._capture_slot = slot
        slot.progress = "Starting camera..."
        self._cap_progress_kind = None
        self._cap_progress_n = 0
        log.info("Arbiter: pre-image started for slot %s (LEDs %s)",
                 slot.name, ", ".join(session.config.led_colors))
        return True

    def request_capture(self, slot: Slot) -> bool:
        """Start a capture for slot if the camera is free.  Returns granted."""
        if self._capture is not None:
            log.info("Arbiter: capture denied — camera busy (slot %s)",
                     self._capture_slot.name if self._capture_slot else "?")
            return False

        if self._prefetch is not None and self._prefetch_slot is slot:
            # A prefetch already exists for exactly this slot — resume it if
            # it finished and nothing else has touched the shared camera
            # since; otherwise (still running, or stale) abandon it and fall
            # through to a fresh, fully-synchronous run either way. No fancy
            # recovery beyond that — this is still an experimental workflow.
            session = self._prefetch
            ready = (session.prefetch_done
                    and self._prefetch_epoch == self._camera_epoch)
            self._prefetch = None
            self._prefetch_slot = None
            self._prefetch_epoch = None
            if ready:
                if session.start_capture():
                    self._capture = session
                    self._capture_slot = slot
                    slot.progress = "Starting camera..."
                    self._cap_progress_kind = None
                    self._cap_progress_n = 0
                    self.persist(slot, capture_start=self.epoch(),
                                manual_read=slot.record.get("manual_read", ""))
                    log.info("Arbiter: capture resumed from prefetch for slot "
                            "%s (mode=%s, group=%s)",
                            slot.name, self._cap_mode, self._cap_group)
                    return True
                log.error("Arbiter: failed to resume prefetch into capture "
                         "for slot %s: %s", slot.name, session.error)
                slot.progress = "Camera failed"
                return False
            session.cancel()

        session = self._new_session(self._measurement_config(slot))
        if not session.start():
            log.error("Arbiter: capture failed to start: %s", session.error)
            slot.progress = "Camera failed"
            return False
        self._capture = session
        self._capture_slot = slot
        slot.progress = "Starting camera..."
        self._cap_progress_kind = None
        self._cap_progress_n = 0
        self.persist(slot, capture_start=self.epoch(),
                     manual_read=slot.record.get("manual_read", ""))
        log.info("Arbiter: capture started for slot %s (mode=%s, group=%s)",
                 slot.name, self._cap_mode, self._cap_group)
        return True

    def _new_session(self, cfg=None, runner_factory=None):
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
            cfg = CaptureConfig()
        # Borrow the Arbiter's persistent camera when available, so this run
        # reuses the already-open sensor instead of paying the open cost again.
        # If it could not be opened, the session builds and owns its own.
        hw = self._ensure_camera()
        if hw is not None:
            cam, leds = hw
            # A brand-new session is about to drive the shared camera's focus
            # and exposure — invalidate any prefetch parked from an earlier
            # session (see _prefetch_epoch and request_capture()'s promotion).
            self._camera_epoch += 1
            return CaptureSession(cfg, cam=cam, leds=leds,
                                  runner_factory=runner_factory)
        return CaptureSession(cfg, runner_factory=runner_factory)

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
        if self._prefetch_slot is slot and self._prefetch is not None:
            self._prefetch.cancel()
            self._prefetch = None
            self._prefetch_slot = None
            self._prefetch_epoch = None
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
        slot.stir_deadline = None
        slot.stir_role = None
        slot.stir_fired = False
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

            # Check for stir sub-alarm (mid-countdown beep during reagent phase)
            if (slot.stir_deadline is not None and now >= slot.stir_deadline
                    and not slot.stir_fired):
                slot.stir_fired = True
                self._stir_alarm(slot)
                self.persist(slot, **{f"{slot.stir_role}_stir_end": self.epoch()})
                if slot is self._focused:
                    self.render(slot)

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
            # Only exposure-store ticks are countable: non-transient, with a
            # non-empty step (excludes section headers like "Lit captures:" /
            # "--- Red ---", which yield step="", and _wait()'s warmup/settle
            # ticks, which yield step="wait" but transient=True). Classify
            # lit vs dark from the step name and keep a running count that
            # resets on each lit<->dark transition. Covers both the
            # calibration path (lit 1..7, dark 1..7, repeated per LED) and
            # the normal path (lit 1..len(group) per LED, one combined dark
            # batch — step="dark" literally — at the end).
            if progress.step and not progress.transient:
                kind = "dark" if progress.step.startswith("dark") else "lit"
                if kind != self._cap_progress_kind:
                    self._cap_progress_kind = kind
                    self._cap_progress_n = 0
                self._cap_progress_n += 1
                if kind == "dark":
                    owner.progress = (
                        f"Capturing dark images, {self._cap_progress_n} exposures")
                else:
                    owner.progress = (
                        f"Capturing {progress.color}, {self._cap_progress_n} exposures")
                if owner is self._focused and owner.phase == "read":
                    self._pool.set("collect_line", owner.progress)
                    self._pool.redraw(self._disp, "collect_line")
            return

        # step() returned None: the run ended.
        self._capture = None
        self._capture_slot = None
        from capture_runner import MODE_PRE   # lazy, like the other capture
                                             # imports; loaded already by now
        is_pre = str(session.config.mode).lower() == MODE_PRE
        if session.error is not None:
            owner.progress = (f"Pre-image failed: {str(session.error)[:28]}"
                              if is_pre else
                              f"Capture failed: {str(session.error)[:30]}")
            owner.capturing = False
            self.render(owner)
            return
        if is_pre:
            # A pre-image ends by handing the reading its archive and getting
            # out of the way. It stamps h5file — that column names the file
            # holding this sample's images, and this run is what created it —
            # but not capture_end, which belongs to the measurement. Nothing
            # is analysed yet, so the run simply moves on to reagent 1.
            owner.capturing = False
            self.persist(owner, h5file=str(session.out_path)
                                       if session.out_path else "")
            owner.progress = "Pre-image stored"
            self.goto(owner, "reagent1")
            return
        if owner.timer is not None:
            owner.timer.stop(self.now())     # freeze the final elapsed time
        owner.capturing = False
        self.persist(owner, capture_end=self.epoch(),
                     h5file=str(session.out_path) if session.out_path else "")
        # The archive was written with a copy of the sample record taken
        # before the first photograph, so it is missing everything decided
        # since — including the capture_end and h5file persisted on the line
        # above. Now that the file is closed, reopen it and put the current
        # record in. Does nothing for a menu-launched capture, which has no
        # sample. See capture_meta.update_archive_sample().
        import capture_meta          # imported here, like the other capture
                                     # modules, to keep it off the startup path
        capture_meta.update_archive_sample(session.out_path,
                                           owner.record.get("db_rowid"))
        # Analysis divides by how much reagent the sample actually received, so
        # a run whose packets were never re-weighed has nothing to divide by.
        # Rather than analyse a sample we cannot describe, hold on the read
        # phase, which brings the weight grid back and offers ANALYZE once both
        # final weighings are in.
        if self.reagent_finals_complete(owner):
            self.goto(owner, "analysis")
        else:
            owner.progress = "Enter final reagent weights"
            self.render(owner)
        # A capture just freed the camera; re-enable a waiting CAPTURE button
        # on the other slot if it is the one being viewed (not, say, the Menu).
        if (self._focused is not None and self._focused is not owner
                and self._focused.phase == "read"):
            self.render(self._focused)

    # ── Dark-frame prefetch (normal path only) ──────────────────────────────────

    def _reagent2_within(self, slot: Slot, threshold_s: float) -> bool:
        """True if `slot` is in reagent2 with `threshold_s` seconds or less
        left on its countdown. False if it is not in reagent2, or has no
        timer (armed-but-not-started, or just reset) to measure against."""
        if slot.active_phase != "reagent2" or slot.timer is None:
            return False
        return slot.timer.value(self.now()) <= threshold_s

    def _maybe_start_prefetch(self) -> None:
        """
        Automatically begin the normal path's capture prefetch once a
        slot's reagent2 countdown is within its budget of expiring — no user
        action; the operator is still watching the countdown, not yet at
        CAPTURE. Only applies to the normal capture path (self._cap_mode) —
        the calibration path has no prefetch stage to run.

        At most one prefetch in flight/parked at a time: if the camera is
        already busy (a capture in progress, or another slot's prefetch),
        this is a no-op — that slot's own eventual CAPTURE press falls back
        to a fresh, fully-synchronous run instead (see request_capture()).
        """
        if self._capture is not None or self._prefetch is not None:
            return
        if self._cap_mode != "normal":
            return
        budget = config.capture_prefetch_budget_s()
        for slot in self._slots.values():
            if self._reagent2_within(slot, budget):
                self._start_prefetch(slot)
                return

    def _start_prefetch(self, slot: Slot) -> None:
        """Build a session and begin run_prefetch() for `slot`'s upcoming capture."""
        session = self._new_session(self._measurement_config(slot))
        if not session.start_prefetch():
            log.error("Arbiter: capture prefetch failed to start for slot "
                     "%s: %s", slot.name, session.error)
            return
        self._prefetch = session
        self._prefetch_slot = slot
        log.info("Arbiter: capture prefetch started for slot %s", slot.name)

    def pump_prefetch(self) -> None:
        """
        Drive the in-flight prefetch forward, like pump_capture() does for a
        full capture — called every main-loop pass, a no-op unless
        _maybe_start_prefetch() has armed one.

        A step that yields None means run_prefetch() finished: the session
        parks itself (camera/archive left open — see CaptureSession.step()),
        and this stamps _prefetch_epoch so request_capture() can later tell
        whether anything else touched the shared camera before promoting it.
        A failure clears the slot the same way pump_capture() does, so the
        eventual CAPTURE press falls back to a fresh run.
        """
        session = self._prefetch
        if session is None or not session.active:
            return
        session.step()
        if session.active:
            return
        if session.prefetch_done:
            self._prefetch_epoch = self._camera_epoch
            log.info("Arbiter: capture prefetch parked for slot %s",
                     self._prefetch_slot.name if self._prefetch_slot else "?")
            return
        log.error("Arbiter: capture prefetch failed for slot %s: %s",
                 self._prefetch_slot.name if self._prefetch_slot else "?",
                 session.error)
        self._prefetch = None
        self._prefetch_slot = None
        self._prefetch_epoch = None

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
        # The result is the last thing to reach the sample record, so refresh
        # the archive's copy of it again here — the refresh done when the
        # capture ended happened before this existed. Same call, second time;
        # see capture_meta.update_archive_sample().
        import capture_meta          # as in pump_capture — kept off startup
        capture_meta.update_archive_sample(slot.record.get("h5file"),
                                           slot.record.get("db_rowid"))
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

    def _camera_open_lead_s(self) -> float:
        """
        Seconds before a slot's reagent2 countdown expires within which the
        shared camera should be open: camera_warmup_s (thermal-stability
        margin, on but idle) plus prefetch_budget_s (the prefetch stage
        itself — camera setup and focus — scheduled to start
        prefetch_budget_s before expiry, see _maybe_start_prefetch()). Shared by
        _camera_wanted() and _maybe_open_camera() so the two agree on when
        the camera should be open: were _camera_wanted()'s threshold any
        narrower than the one that opened it, the camera would open, then
        immediately read as "not wanted" and get released again on the very
        same main-loop pass, thrashing for the camera_warmup_s gap between
        the two thresholds.
        """
        return config.capture_camera_warmup_s() + config.capture_prefetch_budget_s()

    def _camera_wanted(self) -> bool:
        """
        True while anything on screen needs the shared camera to STAY open.

        Three claims: a slot already at read/analysis (_CAMERA_PHASES), a
        slot in reagent2 within its camera-open lead window (see
        _camera_open_lead_s()), and the image viewer, which reimages on
        demand. The viewer *must* be counted here — it is not a phase, so
        without it _maybe_release_camera() would close the sensor on the
        very next main-loop pass and every reimage would pay the full open
        cost again.
        """
        if self._view_open:
            return True
        lead = self._camera_open_lead_s()
        return any(s.active_phase in _CAMERA_PHASES or self._reagent2_within(s, lead)
                   for s in self._slots.values())

    def _maybe_open_camera(self) -> None:
        """
        Open the shared camera within its lead window (_camera_open_lead_s())
        before a slot's reagent2 countdown expires — long enough for the
        sensor to settle AND the capture prefetch stage to run to
        completion before CAPTURE becomes reachable. Gated on prewarm so the
        picamera2 import cost is already absorbed; a failure is logged and
        left for capture time.

        Deliberately NOT opened as soon as reagent1 (or even reagent2) starts:
        the user believes the camera heats up over time and that affects
        image stability, so it should stay off as much as possible while
        still on long enough beforehand to be thermally stable — see
        _CAMERA_PHASES' comment.

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
            if cam.lens_position is None:
                # Freshly opened: the sensor has no meaningful lens position
                # yet (setup_camera() locks AfMode Manual without one), so
                # restore the saved focus_cal.json calibration now — the same
                # thing CaptureRunner._set_focus() does before every real
                # capture — instead of leaving previews unfocused until an
                # explicit Auto/Reread action.
                cam.focus(force=False, leds=leds)
            self._camera, self._leds = cam, leds
            log.info("Arbiter: shared camera opened")
            return self._camera, self._leds
        except Exception:
            log.exception("Arbiter: failed to open shared camera")
            self._release_camera()
            return None

    def _maybe_release_camera(self) -> None:
        """
        Close the shared camera once no slot needs it and no capture or
        prefetch is live.

        The prefetch check matters even though a parked prefetch's slot is
        (by definition) within _camera_wanted()'s own reagent2 threshold: an
        operator can RESET the reagent2 timer while a prefetch is in flight
        or parked, which clears slot.timer without changing active_phase —
        _reagent2_within() then reads False for that slot, so this guard is
        what actually keeps the camera from being pulled out from under it.
        """
        if self._camera is None and self._leds is None:
            return
        if (self._capture is not None or self._prefetch is not None
                or self._camera_wanted()):
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

    def preview_frame(self, region: str = "sample", lit: bool = True):
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
                    (meters the entire frame) — see `_auto_expose`. Unused
                    when `lit` is False, since there is nothing to meter.
            lit: True (default) briefly lights the white LED and auto-exposes
                 against `region`, as above. False leaves the LED off and
                 takes a single frame at the last known exposure instead —
                 the recapture dialog's "Dark" option, a reference frame at
                 the same exposure a lit one would use, with no signal to
                 converge metering against.

        Returns:
            (frame, error).  `frame` is a numpy BGR array of the full stream on
            success and None on failure, in which case `error` is a short
            message for the screen.
        """
        hw = self._ensure_camera()
        if hw is None:
            return None, "Camera unavailable"
        cam, leds = hw
        # Metering here (and _recap_capture()'s forced-refocus just before
        # calling this) mutates the shared camera's exposure/focus — a parked
        # prefetch's assumption that nothing else touched the camera since it
        # parked no longer holds, so invalidate it (see _prefetch_epoch).
        self._camera_epoch += 1

        if not lit:
            import capture_runner
            us = self._view_exposure_us or self._start_exposure_us()
            try:
                rgb, bayer, meta = capture_runner._set_and_settle(
                    cam, us, max_frames=_PREVIEW_SETTLE_FRAMES)
            except Exception:
                log.exception("Arbiter: dark preview capture failed")
                return None, "Preview failed"
            if rgb is None or getattr(rgb, "ndim", 0) != 3:
                return None, "No image"
            self._view_exposure_us = int(meta.get("ExposureTime") or us)
            self._view_bayer = bayer
            log.info("Arbiter: dark preview frame %dx%d at %d us",
                     rgb.shape[1], rgb.shape[0], self._view_exposure_us)
            return rgb, ""

        led_on = False
        try:
            if leds is not None:
                leds.set_channel(_PREVIEW_LED_CH, True)
                led_on = True
                time.sleep(_PREVIEW_SETTLE_S)
            rgb, exposure_us = self._auto_expose(cam, region=region)
        except Exception:
            log.exception("Arbiter: preview capture failed")
            return None, "Preview failed"
        finally:
            if led_on:
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
        Likewise stashes the raw mosaic behind the returned frame in
        `_view_bayer`, for the recapture dialog's save-to-h5 options.

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
                self._view_bayer = bayer
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
                self._view_bayer = bayer
                return rgb, applied
            us = wanted

        log.warning("Arbiter: preview exposure did not converge in %d frames — "
                    "showing the frame taken at %d us; next preview starts at "
                    "%d us", _PREVIEW_AE_MAX_FRAMES, applied, us)
        self._view_exposure_us = us
        self._view_bayer = bayer
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
                us = exposure_repo.middle_exposure(_PREVIEW_LED_NAME, group)
                if us:
                    return _clamp_us(us)
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
        import roi as _roi
        r = _roi.by_role(self._view_rois, _roi.ROLE_SAMPLE)
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

    def open_image_view(self, slot: Slot | None = None,
                        focus_role: str | None = None,
                        pad: bool = False):
        """
        Open the image viewer, taking one preview frame first.

        Refused while a capture is in flight: the imaging tools share the
        camera and the LED bank with the capture path, so they must be
        unavailable from the moment CAPTURE is pressed until the last frame is
        stored.  `capture_available()` is the single guard covering both the
        per-sample Read capture and a standalone Menu capture.

        Args:
            slot:       Where Exit returns to; None returns to the Menu.
            focus_role: A ROI *role* to open zoomed to (Tool 2's entry point),
                        e.g. roi.ROLE_SAMPLE; None opens on the whole frame.
                        A role, not a name, because the caller means "the
                        sample patch", not whatever the operator last called
                        it — see the note in roi.py.
            pad:        Show and edit the exposure-calibration test pad's three
                        patches (the active=2 set) instead of the measurement
                        ROIs.  Exit then returns to the Capture page.
        """
        if not self.capture_available():
            log.info("Arbiter: image view refused — a capture is in flight")
            self._toast("Capture running")
            return
        import roi as _roi
        self._view_return_slot = slot
        self._view_open = True
        self._view_status = ""
        self._view_mode = "view"
        self._view_editing = None
        self._view_armed = None
        self._focused = None
        self._view_pad = pad
        self._view_rois = self._load_view_rois()
        # Resolve the requested role to the name the rest of the viewer keys
        # on — tap selection and the edit path both work in names. An
        # unfilled role simply opens on the whole frame.
        target = (_roi.by_role(self._view_rois, focus_role)
                  if focus_role else None)
        if focus_role and target is None:
            log.warning("Arbiter: image view asked to focus role %r, which no "
                        "active ROI fills; opening on the whole frame",
                        focus_role)
        self._view_selected = target.name if target is not None else None
        self._view_reimage(focus_roi=self._view_selected)
        if pad and self._view_state is not None and self._view_rois:
            self._view_fit_pad()
            self._render_image_view()

    def _load_view_rois(self) -> dict:
        """
        Read the ROIs in force, for drawing over the preview.

        Failure is survivable: an empty dict just means no rectangles are
        drawn, and looking at the raw image is still useful.
        """
        try:
            import roi
            size = getattr(self._camera, "config_size", None) or (None, None)
            model = getattr(self._camera, "model", None)
            if self._view_pad:
                return {n: r for n, r in
                        roi.load_calibration(size[0], size[1], model).items()
                        if r.role in roi.TEST_PAD_ROLES}
            return roi.load_active(size[0], size[1], model)
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
            self._view_bayer = None
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
                               and self._view_frame is not None
                               and not self._any_slot_active())
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
        p.put("recap_focus_auto", "Auto\nFocus",
              on=lambda: self._recap_set_focus("auto"))
        p["recap_focus_auto"].color = sel if self._recap_focus == "auto" else unsel
        p.put("recap_focus_cfg", "Reread\nCfg",
              on=lambda: self._recap_set_focus("reread"))
        p["recap_focus_cfg"].color = sel if self._recap_focus == "reread" else unsel
        p.put("recap_save_focus", "Save\nFocus", on=self._recap_toggle_save_focus)
        p["recap_save_focus"].color = sel if self._recap_save_focus else unsel
        p.put("recap_exp_lbl", "Exposure")
        p.put("recap_exp_sample", "Sample\nRect",
              on=lambda: self._recap_set_exp("sample"))
        p["recap_exp_sample"].color = sel if self._recap_exp == "sample" else unsel
        p.put("recap_exp_whole", "Whole\nImage",
              on=lambda: self._recap_set_exp("whole"))
        p["recap_exp_whole"].color = sel if self._recap_exp == "whole" else unsel
        p.put("recap_save_exp", "Save\nExp", on=self._recap_toggle_save_exp)
        p["recap_save_exp"].color = sel if self._recap_save_exp else unsel
        p.put("recap_image_lbl", "Save Image")
        p.put("recap_image_roi", "Bound\nROI", on=self._recap_toggle_save_roi)
        p["recap_image_roi"].color = sel if self._recap_save_roi else unsel
        p.put("recap_image_whole", "Whole\nImage", on=self._recap_toggle_save_whole)
        p["recap_image_whole"].color = sel if self._recap_save_whole else unsel
        p.put("recap_image_dark", "Dark", on=self._recap_toggle_dark)
        p["recap_image_dark"].color = sel if self._recap_dark else unsel
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

    def _recap_toggle_save_roi(self) -> None:
        # Non-exclusive: independent of _recap_toggle_save_whole.
        self._recap_save_roi = not self._recap_save_roi
        self._recap_render()

    def _recap_toggle_save_whole(self) -> None:
        # Non-exclusive: independent of _recap_toggle_save_roi.
        self._recap_save_whole = not self._recap_save_whole
        self._recap_render()

    def _recap_toggle_dark(self) -> None:
        self._recap_dark = not self._recap_dark
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

        frame, err = self.preview_frame(region=self._recap_exp,
                                        lit=not self._recap_dark)
        self._view_status = err

        diopter = cam.lens_position
        log.info("Arbiter: recapture — focus=%s diopter=%s exposure=%s us "
                 "region=%s lit=%s", self._recap_focus or "unchanged",
                 f"{diopter:.4f}" if diopter is not None else "n/a",
                 self._view_exposure_us, self._recap_exp, not self._recap_dark)

        if self._recap_save_focus and diopter is not None:
            cam.save_focus_cal()
            config.save("capture", "default_focus_diopter", diopter)
        if self._recap_save_exp and self._view_exposure_us is not None:
            config.save("capture", "inspection_exposure_us",
                        self._view_exposure_us)

        if frame is None:
            self._view_frame = None
            self._view_bayer = None
            self._view_state = None
        else:
            if self._recap_save_whole:
                self._recap_save_snapshot(frame, self._view_bayer, cam, "img")
            if self._recap_save_roi:
                import roi as _roi
                bound = _roi.by_role(self._view_rois, _roi.ROLE_BOUND)
                if bound is None:
                    log.warning("Arbiter: recapture — no bound ROI to save")
                else:
                    self._recap_save_snapshot(frame, self._view_bayer, cam,
                                              "bound", crop_rect=bound)
            self._view_install_frame(frame)
        self._render_image_view()

    def _recap_save_snapshot(self, rgb, bayer, cam, suffix: str,
                              crop_rect=None) -> None:
        """
        Write one recapture snapshot — a JPEG plus the four Bayer layers
        behind it — into the captures directory as "<timestamp>_<suffix>.h5".

        `suffix` is "img" for the whole frame or "bound" for the bound-ROI
        crop (`crop_rect`). Reuses capture_runner's private jpeg/layer
        helpers so this ad hoc snapshot encodes the same inputs the same way
        a normal suite archive would.
        """
        if bayer is None or getattr(bayer, "ndim", 0) != 2:
            log.warning("Arbiter: recapture — no raw frame to save (%s)", suffix)
            return
        import capture_runner
        from bayer_cam import BayerCam
        from hdf5write import Hdf5Writer

        pattern = getattr(cam, "bayer_pattern", "BGGR")
        if crop_rect is not None:
            rgb_out = BayerCam.crop_rgb(rgb, crop_rect)
            layers = capture_runner._roi_bound_layers(bayer, pattern, crop_rect)
        else:
            rgb_out = rgb
            layers = BayerCam.extract_bayer_layers(bayer, pattern)

        out_dir = config.capture_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = out_dir / f"{stamp}_{suffix}.h5"
        try:
            with Hdf5Writer(path) as w:
                w.set_attrs("/", {"suite_id": stamp})
                w.write_bytes("/", "jpeg", capture_runner._encode_jpeg(rgb_out),
                              attrs={"format": "jpeg"})
                for ch, arr in layers.items():
                    w.write_array("/bayer", ch, arr)
            log.info("Arbiter: recapture — saved %s", path)
        except Exception:
            log.exception("Arbiter: recapture — could not save %s", path)

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
                and self._view_frame is not None
                and not self._any_slot_active())
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
        bound  = roi.by_role(proposed, roi.ROLE_BOUND)
        sample = roi.by_role(proposed, roi.ROLE_SAMPLE)
        if bound is not None and sample is not None \
                and not roi.contains_rect(bound, sample):
            return self._view_reject(f"{sample.name} outside {bound.name}")

        rowid = roi.save(snapped.name, snapped, remark="edited on the instrument",
                         active=2 if self._view_pad else 1)
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
        self._view_bayer = None          # ~6 MB raw mosaic behind that frame
        self._view_state = None
        self._view_rois = {}
        self._view_selected = None
        self._view_editing = None
        self._view_armed = None
        self._view_mode = "view"
        slot = self._view_return_slot
        self._view_return_slot = None
        if self._view_pad:
            self._view_pad = False
            self._render_capture_choice()
            return
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
            # Commit weight field if tab away while editing
            if was_editing and str(cw.name) in WT_FIELD_KEYS:
                self._weight_field_commit(cw)
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
                was_editing = w.editing
                w.toggle_editing(self._disp)
                # Commit weight field if editing just turned off
                if was_editing and str(w.name) in WT_FIELD_KEYS:
                    self._weight_field_commit(w)
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
        # The config editor has no slot behind it — its fields stage their own
        # value until Save, so it only needs to recolour and re-enable.  Keyed
        # on the widget, not on the screen state, so a stale view field can
        # never reach a slot record.
        if str(textbox.name).startswith("cfge_"):
            self._cfge_writethrough(textbox)
            return

        slot = self._focused
        if slot is None:
            return

        # Weight grid: one box, one key, always.  Nothing about the run state
        # enters into it, so a correction typed late in a run lands in the same
        # column the original reading did.
        wt_key = WT_FIELD_KEYS.get(str(textbox.name))
        if wt_key is not None:
            slot.record[wt_key] = textbox.value
            return

        fields = {"entry":  EntryPhase.FIELDS,
                  "entry2": Entry2Phase.FIELDS}.get(slot.phase)
        if not fields:
            return
        key = fields.get(textbox.name)
        if key is None:
            return
        slot.record[key] = textbox.value
        # Only while the number is unclaimed does typing decide whether Begin is
        # available.  Once it is claimed the button reads Continue and stays lit;
        # what is being typed then is identity, which cannot un-claim a row.
        if slot.phase == "entry" and slot.record.get("db_rowid") is None:
            begin = self._pool["main_btn"]
            want = slot.ready_to_begin()
            if begin.enabled != want:
                begin.enabled = want
                begin.draw(self._disp)

    def _weight_field_commit(self, textbox) -> None:
        """
        A weight box was committed: repaint the grid, and store the amount
        added once both of that reagent's weighings exist.

        reagentN_g is the poured amount and nothing else, so it is written only
        in the both-entered case; while a final weight is still missing, `net`
        is the packet's contents and does not belong in that column.
        """
        slot = self._focused
        if slot is None:
            return
        key = WT_FIELD_KEYS.get(str(textbox.name))
        if key is None:
            return

        n = 1 if key.startswith("reagent1") else 2
        w = self.reagent_weigh_plan(slot, n)
        if w.initial is not None and w.final is not None:
            self.persist(slot, **{f"reagent{n}_g": w.net})
        # A weight moves the START gate, the Over/Target cells and their
        # colours all at once, so repaint the phase rather than one widget.
        self.render(slot)

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
            self._maybe_start_prefetch()
            self.pump_prefetch()
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

    # The same guarantee main.py gives the real Arbiter: every parameter this
    # test reads has an active row before anything reads it.  Without it the
    # test runs against whatever the developer's database happens to hold, so
    # adding a parameter breaks it with ConfigMissing rather than seeding it.
    import db_bootstrap
    db_bootstrap.ensure()

    class _MockDisplay:
        def fill_screen(self, c): pass
        def fill_rect(self, *a, **k): pass
        def draw_rect(self, *a, **k): pass
        def draw_text(self, *a, **k): pass
        def __init__(self): self.images = []
        def draw_image(self, image, x=0, y=0):
            self.images.append((image.size, x, y))

    class _FakeProgress:
        """
        Stands in for capture_runner.Progress — every field pump_capture reads.

        `step` matters as much as the counts: pump_capture classifies a tick
        as lit or dark from that label, and ignores ticks with an empty step
        (section headers) or transient=True (wait ticks), so a fake without
        one cannot exercise the live progress text at all.
        """
        def __init__(self, i, n, step="e1"):
            self.color = "blue"; self.fraction = i / n
            self.led_index = i; self.led_total = n; self.transient = False
            self.step = step; self.message = f"{step} ({i}/{n})"
            self.phase = "capture"

    from types import SimpleNamespace

    class _FakeRunner:
        """Stands in for CaptureRunner: what the arbiter reads after a run."""
        def __init__(self):
            self.suite_id = "TESTSUITE"; self.results = []

    class _FakeCapture:
        """
        Completes after `steps` step() calls.  Also stands in for the
        prefetch/capture split (start_prefetch()/prefetch_done/
        start_capture()), mirroring CaptureSession's external contract:
        run_prefetch() takes one step, then parks without releasing anything;
        start_capture() resumes the same fake into its ordinary step() count.
        """
        def __init__(self, config=None, steps=3, mode="normal"):
            self.active = False; self.error = None
            self.out_path = Path("fake.h5")
            self.runner = _FakeRunner()
            self._i = 0; self._n = steps
            self.prefetch_done = False
            self._prefetching = False
            # CaptureSession carries the config it was built from, and
            # pump_capture() reads .mode off it to tell a pre-image's ending
            # from a measurement's. The stand-in has to carry one too.
            self.config = config or SimpleNamespace(
                mode=mode, led_colors=["royal_blue", "red"])
        def start(self): self.active = True; return True
        def start_prefetch(self):
            self.active = True; self._prefetching = True
            return True
        def start_capture(self):
            if not self.prefetch_done:
                return False
            self.prefetch_done = False
            self.active = True; self._prefetching = False
            self._i = 0
            return True
        def step(self):
            if self._prefetching:
                self.active = False
                self.prefetch_done = True
                return None
            if self._i >= self._n:
                self.active = False; return None
            self._i += 1
            # Shaped like the normal path: the lit exposures first, then one
            # combined dark batch to close the run, so the lit -> dark
            # transition (and the counter reset it causes) is covered.
            step = "dark" if self._i == self._n else f"e{self._i}"
            return _FakeProgress(self._i, self._n, step)
        def cancel(self): self.active = False; self.prefetch_done = False

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

    print("== shared camera: opens near reagent2 expiry (not on reagent1), "
          "released when idle ==")

    from timer import TimerState

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

    open_lead = (config.capture_camera_warmup_s()
                + config.capture_prefetch_budget_s())

    check("no camera while idle",   arb._camera_wanted(), False)
    arb._maybe_open_camera()
    check("idle → camera stays shut", arb._camera is None, True)

    A.active_phase = "reagent1"     # believed to heat the camera — stays off
    arb._maybe_open_camera()
    check("reagent1 → camera stays shut", arb._camera is None, True)

    A.active_phase = "reagent2"
    A.timer = TimerState.down(open_lead * 10, arb.now())   # far from expiry
    arb._maybe_open_camera()
    check("reagent2 far from expiry → camera stays shut", arb._camera is None, True)

    A.timer = TimerState.down(open_lead - 1, arb.now())    # within the lead window
    arb._maybe_open_camera()
    check("reagent2 near expiry → camera opened", arb._camera is not None, True)
    fake_cam, fake_leds = arb._camera, arb._leds
    arb._maybe_release_camera()                    # still wanted (A within budget)
    check("kept open while wanted",  arb._camera is fake_cam, True)
    A.active_phase = "entry"; A.timer = None        # both slots idle now
    arb._maybe_release_camera()
    check("idle → camera released",  arb._camera is None, True)
    check("camera stopped",          fake_cam.stopped, True)
    check("leds closed",             fake_leds.closed, True)

    print("== capture prefetch: scheduled near reagent2 expiry, "
          "promoted at CAPTURE ==")
    arb._cap_mode = "normal"
    budget = config.capture_prefetch_budget_s()

    A.active_phase = "reagent2"
    A.timer = TimerState.down(budget * 10, arb.now())      # far from budget
    arb._maybe_start_prefetch()
    check("far from expiry → no prefetch yet", arb._prefetch is None, True)

    A.timer = TimerState.down(budget - 1, arb.now())       # within budget
    arb._maybe_start_prefetch()
    check("within budget → prefetch started", arb._prefetch is not None, True)
    check("prefetch owner is A",              arb._prefetch_slot is A, True)

    B.active_phase = "reagent2"
    B.timer = TimerState.down(budget - 1, arb.now())
    arb._maybe_start_prefetch()
    check("second slot's prefetch deferred (camera busy)",
         arb._prefetch_slot is A, True)
    B.active_phase = "entry"; B.timer = None       # tidy up

    for _ in range(5):
        arb.pump_prefetch()
    check("prefetch parked (not active)", arb._prefetch.active, False)
    check("prefetch_done",                arb._prefetch.prefetch_done, True)
    check("camera stays wanted while parked", arb._camera_wanted(), True)

    print("== stale prefetch (camera touched since parking) falls back ==")
    arb._camera_epoch += 1     # simulate another session using the camera meanwhile
    ok = arb.request_capture(A)
    check("stale prefetch still grants a fresh run", ok, True)
    check("stale prefetch was discarded", arb._prefetch is None, True)
    for _ in range(10):
        arb.pump_capture()
    check("fresh run completes", arb.capture_available(), True)

    print("== fresh prefetch: promoted (not restarted) at CAPTURE ==")
    A.active_phase = "reagent2"
    A.timer = TimerState.down(budget - 1, arb.now())
    arb._maybe_start_prefetch()
    for _ in range(5):
        arb.pump_prefetch()
    check("second prefetch parked", arb._prefetch.prefetch_done, True)
    ok = arb.request_capture(A)
    check("fresh prefetch promoted",          ok, True)
    check("prefetch cleared after promotion", arb._prefetch is None, True)
    check("capture now in flight (promoted)", arb._capture is not None, True)
    for _ in range(5):
        arb.pump_capture()
    check("promoted capture completes", arb.capture_available(), True)
    A.active_phase = "entry"; A.timer = None

    print("== render draws only the focused slot ==")
    arb.goto(A, "entry")
    check("A is focused",        arb._focused is A, True)
    # Advance B in the background into a running reagent; the shared pool must
    # keep showing A's entry phase (its title), not B's.
    arb.focus(A)
    B.record["samp_no"] = "BX9"; B.phase = B.active_phase = "reagent1"
    B.running = True
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
    # A carries a finished capture out of the prefetch section above, and the
    # read screen turns into the weights gate once capture_end is stamped.
    # Clear it so this section starts from a run with the camera still to use.
    A.record["capture_end"] = None
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

    print("== capture completes: analysis waits on the final weighings ==")
    arb.focus(A)
    A.capturing = True
    for _ in range(5):
        arb.pump_capture()
    # Nothing weighed the packets a second time, so there is no record of how
    # much reagent the sample got and the run stops short of analysis.
    check("A held on read",       A.phase, "read")
    check("camera freed anyway",  arb.capture_available(), True)
    check("run_btn is ANALYZE",   arb._pool["run_btn"].label, "ANALYZE")
    check("ANALYZE disabled",     arb._pool["run_btn"].enabled, False)

    print("== the final weighings release it -> analysis -> result ==")
    A.record["reagent1_final_wt"] = "1.200"
    A.record["reagent2_final_wt"] = "1.100"
    arb.render(A)
    check("ANALYZE enabled",      arb._pool["run_btn"].enabled, True)
    arb._pool["run_btn"].on_press()
    check("A moved to analysis",  A.phase, "analysis")
    arb._analysis = (A, arb.now() - config.analysis_secs() - 1)   # force it done
    arb.pump_analysis()
    check("result set",           bool(A.result), True)
    check("POST enabled",         arb._pool["run_btn"].enabled, True)

    print("== pre-image completes -> reagent1, stamping h5file and not capture_end ==")
    arb.goto(A, "entry")
    A.record["db_rowid"] = 7
    A.record["h5file"] = ""
    A.record["capture_end"] = None
    A.active_phase = A.phase = "preimage"
    cap_pre = _FakeCapture(mode="pre")
    arb._capture_factory = lambda: cap_pre
    check("pre-image granted",   arb.request_preimage(A), True)
    A.capturing = True
    for _ in range(5):
        arb.pump_capture()
    check("A moved to reagent1", A.phase, "reagent1")
    check("archive recorded",    A.record["h5file"], "fake.h5")
    check("capture_end untouched", A.record["capture_end"], None)
    check("camera freed",        arb.capture_available(), True)
    check("not capturing",       A.capturing, False)

    print("== the measurement adds to the pre-image's archive ==")
    cfg = arb._measurement_config(A)
    check("no archive to join (fake.h5 is not on disk)", cfg.archive_path, None)
    import tempfile as _tf
    with _tf.NamedTemporaryFile(suffix=".h5") as fh:
        A.record["h5file"] = fh.name
        cfg = arb._measurement_config(A)
        check("joins the file that is there", str(cfg.archive_path), fh.name)
    A.record["h5file"] = ""
    arb._capture_factory = _FakeCapture

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
    _db.write_one("DELETE FROM exposure")

    def saved_group():
        """The exposure group persisted in the config table."""
        return config.get("defaults", "exposuregroup")

    class _R:                       # a stand-in LedResult carrying e*
        def __init__(self, color, estar):
            self.color = color; self.metadata = {"e3_final_us": estar}

    print("== capture screen: opens on the group in force ==")
    config.save("defaults", "exposuregroup", "3")
    arb.open_menu(A)
    check("menu open (no slot focus)", arb._focused is None, True)
    arb._menu_open_capture()
    check("choice view",          arb._cap_view, "choice")
    check("default mode normal",  arb._cap_mode, "normal")
    check("group in force is 3",  arb._cap_group, "3")
    check("no LED chosen",        arb._cap_led, None)
    check("color/show need an LED", arb._pool["cap_color"].enabled, False)

    print("== capture screen: choosing an LED ==")
    arb._cap_pick_led("green")
    check("LED chosen",           arb._cap_led, "green")
    check("color/show enabled",   arb._pool["cap_show"].enabled, True)
    arb._cap_pick_led("green")
    check("second tap clears",    arb._cap_led, None)

    print("== capture screen: SAVE writes the window settings ==")
    check("fields show config",   arb._pool["cap_fld_low_dn"].value,
          str(config.get("calibration", "window_low_dn")))
    arb._pool["cap_fld_low_dn"].value = "120"
    arb._pool["cap_fld_high_pc"].value = "95"
    arb._cap_choice_save()
    check("SAVE leaves choice",   arb._cap_view, None)
    check("low DN saved",         config.get("calibration", "window_low_dn"), 120)
    check("high pctl saved",      config.get("calibration", "window_high_pctl"), 95.0)

    print("== capture screen: layers and a window of their own per LED ==")
    arb._menu_open_capture()
    arb._cap_pick_led("green")
    check("green defaults to Gb Gr", arb._cap_layers["green"], ["Gr", "Gb"][::-1])
    check("layers lit",  arb._pool["cap_layer3"].border_color is not None, True)
    check("R not lit",   arb._pool["cap_layer1"].border_color, None)
    arb._cap_toggle_layer("Gb")
    arb._cap_toggle_layer("R")
    check("toggled to R Gr", arb._cap_layers["green"], ["R", "Gr"])
    arb._pool["cap_fld_low_dn"].value = "130"
    arb._cap_pick_led("violet")                          # moves on, keeps green's
    check("violet shows the global window",
          arb._pool["cap_fld_low_dn"].value, arb._cap_global["low_dn"])
    check("violet keeps its default layer", arb._cap_layers["violet"], ["B"])
    arb._cap_toggle_layer("Gr")
    arb._cap_pick_led("violet")                          # clear the choice
    arb._cap_pick_led("green")
    check("green's own window came back", arb._pool["cap_fld_low_dn"].value, "130")
    arb._cap_pick_led("green")                           # clear
    settings = arb._cap_run_settings()
    check("run settings: green layers", settings["green"].layers, ("R", "Gr"))
    check("run settings: green window", settings["green"].window.low_dn, 130)
    check("run settings: violet layers", settings["violet"].layers, ("Gr", "B"))
    check("run settings: violet window is global",
          settings["violet"].window.low_dn, int(arb._cap_global["low_dn"]))
    arb._cap_choice_save()
    check("led window saved",
          config.get("calibration", "led_windows").startswith("green=130:"), True)
    check("layers saved",
          ("green", "R") in config.calibration_required_cells(), True)
    check("violet layers saved",
          ("violet", "Gr") in config.calibration_required_cells(), True)
    arb._menu_open_capture()
    arb._cap_pick_led("green")
    check("reopened: green layers", arb._cap_layers["green"], ["R", "Gr"])
    check("reopened: green window", arb._pool["cap_fld_low_dn"].value, "130")
    arb._cap_choice_cancel()
    config.save("calibration", "led_windows", "")
    config.save("calibration", "required_cells",
                "violet.B,royal_blue.B,cyan.B,green.Gb,green.Gr,photo_red.R")

    print("== capture screen: bad settings stay on the form ==")
    arb._menu_open_capture()
    arb._pool["cap_fld_low_dn"].value = "900"           # above High DN
    arb._cap_choice_save()
    check("stays on choice",      arb._cap_view, "choice")
    check("nothing written",      config.get("calibration", "window_low_dn"), 120)
    arb._pool["cap_fld_low_dn"].value = "abc"
    arb._cap_choice_save()
    check("non-number refused",   arb._cap_view, "choice")
    arb._pool["cap_fld_low_dn"].value = "100"
    arb._pool["cap_fld_high_dn"].value = "1000"          # past the 960 ceiling
    arb._render_capture_choice()                         # re-reads the boxes
    check("out of range: suite off", arb._pool["cap_suite"].enabled, False)
    check("out of range: color off", arb._pool["cap_color"].enabled, False)
    check("out of range: show off",  arb._pool["cap_show"].enabled, False)
    arb._cap_choice_save()
    check("out of range refused", arb._cap_view, "choice")
    arb._cap_pick_led("green")
    check("a bad window blocks choosing an LED", arb._cap_led, None)
    arb._pool["cap_fld_high_dn"].value = "850"
    arb._render_capture_choice()
    check("fixed: suite back on",  arb._pool["cap_suite"].enabled, True)

    print("== capture screen: CANCEL writes nothing ==")
    arb._pool["cap_fld_low_dn"].value = "130"
    arb._cap_choice_cancel()
    check("CANCEL leaves choice", arb._cap_view, None)
    check("low DN unchanged",     config.get("calibration", "window_low_dn"), 120)

    print("== capture screen: Calibrate suite shows progress, SAVE stores ==")
    from palette import color
    _stored = []
    cap_fit = _FakeCapture(steps=2)
    cap_fit.runner = SimpleNamespace(
        fits={"green": SimpleNamespace(ok=True),
              "violet": SimpleNamespace(ok=False, short_reason=lambda: "violet: brown needs 9us")},
        persist=lambda add_dt=None: _stored.append(add_dt) or 7)
    arb._capture_factory = lambda: cap_fit
    arb._menu_open_capture()
    arb._cap_pick_led("violet")
    arb._pool["cap_suite"].on_press()
    check("suite unselects the LED", arb._cap_led, None)
    check("run is a fit",          arb._cap_run_mode, "fit")
    check("still on the form",     arb._cap_view, "choice")
    arb._cap_fit_status("green", "green warming up ...")
    check("LED highlighted",       arb._cap_running, "green")
    check("running LED has a border",
          arb._pool["cap_led5"].border_color is not None, True)
    check("status in the hint",    arb._pool["cap_hint"].text, "green warming up ...")
    arb._cap_pick_led("violet")
    arb._cap_fit_status("green", "green calibrating rung 2 of 15 ...")
    check("chosen LED border black",
          arb._pool["cap_led1"].border_color, color("black"))
    check("running LED border black too",
          arb._pool["cap_led5"].border_color, color("black"))
    for _ in range(5):
        arb.pump_capture()
    check("camera freed",          arb.capture_available(), True)
    check("highlight cleared",     arb._cap_running, None)
    check("a failed LED says why", arb._pool["cap_hint"].text,
          "violet: brown needs 9us")
    arb._cap_choice_save()
    check("SAVE persisted the fit", len(_stored), 1)
    check("fit released",          arb._cap_fit, None)
    arb._menu_open_capture()
    arb._cap_fit = cap_fit.runner
    arb._cap_choice_cancel()
    check("CANCEL stored nothing", len(_stored), 1)

    print("== show screen: pick, capture, plot ==")
    import numpy as _np
    from exposure_show import Curve as _Curve
    exposure_repo.write_all_groups("green", {"1": [20000], "2": [10000, 40000]})
    cap_show = _FakeCapture(steps=2)
    cap_show.runner = SimpleNamespace(curves=[
        _Curve(20000, l, _np.linspace(100, 800, 60)) for l in ("R", "Gr", "Gb", "B")])
    arb._capture_factory = lambda: cap_show
    arb._menu_open_capture()
    arb._cap_pick_led("green")
    arb._pool["cap_show"].on_press()
    check("show view",            arb._cap_view, "show")
    check("exposures on offer",   arb._show_exps, [10000, 20000, 40000])
    check("starts on the middle", arb._show_i, 1)
    check("value shown",          arb._pool["show_value"].text, "20000 us  (2 of 3)")
    arb._pool["show_next"].on_press()
    check("stepped",              arb._show_values()[0], [40000])
    check("exposure mode: all layers", arb._show_values()[1], ["R", "Gr", "Gb", "B"])
    arb._pool["show_mode_layer"].on_press()
    check("layer mode: all exposures", arb._show_values()[0], [10000, 20000, 40000])
    arb._pool["show_layer3"].on_press()
    check("layer mode: one layer", arb._show_values()[1], ["Gb"])
    arb._pool["show_mode_exp"].on_press()
    arb._pool["menu_ok_btn"].on_press()                 # Capture
    check("camera busy",          arb.capture_available(), False)
    check("capture disabled while busy", arb._pool["menu_ok_btn"].enabled, False)
    for _ in range(5):
        arb.pump_capture()
    check("camera freed",         arb.capture_available(), True)
    check("plot drawn",           arb._show_image is not None, True)
    check("plot is canvas sized", arb._show_image.size,
          (layout.SHOW_CANVAS.w, layout.SHOW_CANVAS.h))
    arb._pool["menu_cancel_btn"].on_press()             # BACK
    check("back on the capture page", arb._cap_view, "choice")
    check("LED still chosen",     arb._cap_led, "green")
    arb._cap_choice_cancel()

    # ── Menu grid + Phases screen ────────────────────────────────────────────
    # The Phases screen writes to the `config` table, which is already the
    # throwaway copy set up above, so the live settings are untouched.

    print("== menu: six options in a 3x2 grid ==")
    arb.open_menu(A)
    for _name in ("menu_capture", "menu_stub1", "menu_defaults",
                  "menu_stub2", "menu_config", "menu_power"):
        check(f"{_name} shown", arb._pool[_name].visible, True)
    # Every grid slot opens a screen now: the two that were stubs became the
    # image viewer and the config editor, and menu_config narrowed to
    # "Reagent Phases" (the reagent countdowns) since the editor covers every
    # setting.
    check("stub1 is View Image", arb._pool["menu_stub1"].label, "View\nImage")
    check("stub2 is Config",     arb._pool["menu_stub2"].label, "Config")
    check("stub2 enabled",       arb._pool["menu_stub2"].enabled, True)
    check("config slot is Reagent Phases",
          arb._pool["menu_config"].label, "Reagent\nPhases")

    # Two different baselines, and the difference matters on a development
    # instrument.  _file_* is what config_defaults.sql commissioned — what
    # Restore goes back to, further down.  _live_* is what is in force right
    # now, which is what the phases screen stages and shows; the copied
    # database usually carries an operator's much shorter waits, so assuming
    # the two are equal fails on any instrument that has ever been retimed.
    _file_r1 = int(config.default("protocol", "reagent1_secs"))
    _file_r2 = int(config.default("protocol", "t2ref25_secs"))
    _live_r1 = config.reagent1_secs()
    _live_r2 = config.t2ref25_secs()

    print("== phases screen: steps stage a duration; CANCEL discards it ==")
    arb._menu_open_config()
    check("stages the live durations", arb._cfg_secs["r1"], _live_r1)
    check("field shows the seconds",   arb._pool["cfg_r1_fld"].value, str(_live_r1))
    arb._pool["cfg_r1_p60"].on_press()
    check("+60 stepped",       arb._cfg_secs["r1"], _live_r1 + 60)
    arb._cfg_cancel()
    check("CANCEL saved nothing", config.reagent1_secs(), _live_r1)

    print("== phases screen: OK saves the custom wait and applies it ==")
    arb._menu_open_config()
    arb._pool["cfg_r1_m10"].on_press()
    arb._cfg_ok()
    # Clamped, not _live_r1 - 10: the configured wait may itself be under 10 s
    # (it is, on a development instrument), and -10 then floors at the minimum.
    _stepped_r1 = _clamp_secs(_live_r1 - 10)
    check("saved",             config.reagent1_secs(), _stepped_r1)
    check("phases rebuilt",    arb._phases["reagent1"].secs, _stepped_r1)
    check("reagent2 reference untouched", config.t2ref25_secs(), _live_r2)

    print("== phases screen: a typed duration is honoured and clamped ==")
    arb._menu_open_config()
    arb._pool.set("cfg_r2_fld", "45")          # as a keyboard would leave it
    arb._cfg_ok()
    check("typed value saved", config.t2ref25_secs(), 45)
    arb._menu_open_config()
    arb._pool.set("cfg_r1_fld", "99999")
    check("clamped to the max", arb._cfg_staged("r1"), _CFG_MAX_SECS)
    arb._pool.set("cfg_r1_fld", "0")
    check("clamped to the min", arb._cfg_staged("r1"), _CFG_MIN_SECS)
    arb._pool.set("cfg_r1_fld", "abc")
    check("unparseable keeps the staged value",
          arb._cfg_staged("r1"), arb._cfg_secs["r1"])
    arb._cfg_cancel()

    print("== phases screen: Restore returns to the commissioned durations ==")
    arb._menu_open_config()
    arb._cfg_restore()
    check("staged from the commissioned row", arb._cfg_secs["r2"], _file_r2)
    arb._cfg_ok()
    check("reagent1 back to commissioned", config.reagent1_secs(), _file_r1)
    check("reagent2 reference back to commissioned",
          config.t2ref25_secs(), _file_r2)
    # Only the two durations this screen touches: the copied database carries
    # whatever else the instrument has been set to, which is not this test's
    # business.
    check("no reagent overrides left behind",
          [p for p in config.overrides() if p[0] == "protocol"], [])

    # ── Config editor (Menu → Config) ────────────────────────────────────────
    # Also against the throwaway copy.  It ends with Restore Factory Defaults,
    # which stands every parameter back on its seed row — nothing below reads
    # a value this screen has moved.

    import palette as _palette

    # Both slots are still parked on the result screen from the run above, and
    # the editor locks its fields while any slot is past entry.  Stand them
    # down the way Done does — a finished run cleared back to a fresh one; the
    # lockout gets a test of its own further down.
    for _s in arb._slots.values():
        _s.clear_run()

    def cfge_row_of(name):
        """Body row showing `name` on the page that is up, or None."""
        for i, row in enumerate(arb._cfge_page_rows(), 1):
            if row[0] == name:
                return i
        return None

    def cfge_open_at(role, name):
        """
        Open `role` on whichever page carries `name`, and return its row.

        A role's parameters are paged four at a time in alphabetical order, so
        which page a given one lands on moves whenever a parameter is added to
        that role.  Finding the page rather than assuming the first one keeps
        this test about the config editor instead of about the seed list.
        """
        for page in range(len(arb._cfge_list) + 1):
            arb._cfge_open_role(role, page=page)
            if arb._cfge_page != page:      # clamped: past the last page
                break
            row = cfge_row_of(name)
            if row is not None:
                return row
        raise AssertionError(f"{name!r} is not among role {role!r}")

    print("== config editor: opens on the roles, Restore last, one page of eight ==")
    arb.open_menu(A)
    arb._pool["menu_stub2"].on_press()
    check("roles view",        arb._cfge_view, "roles")
    check("lists every role",  arb._cfge_list[:-1], config.roles())
    check("Restore rides last", arb._cfge_list[-1], _CFGE_RESTORE)
    # Nine roles and Restore are ten entries: a full first page, then a short
    # second page that ends with Restore.
    check("first page is full", len(arb._cfge_page_rows()), _CFGE_ROLE_SLOTS)
    check("Prior dead on page 1", arb._pool["cfge_prior"].enabled, False)
    check("Next live on page 1",  arb._pool["cfge_next"].enabled, True)
    check("nothing to save",   arb._pool["cfge_save"].enabled, False)
    check("row 1 opens a role", arb._pool["cfge_row1"].label, config.roles()[0])
    arb._pool["cfge_next"].on_press()
    check("page 2 ends with Restore",
          arb._cfge_page_rows()[-1], _CFGE_RESTORE)
    arb._pool["cfge_prior"].on_press()

    print("== config editor: stepping back returns to the roles grid ==")

    def cfge_shown(kind):
        """The text of the visible body widgets of one kind, top to bottom."""
        attr = {"cfge_row": "label", "cfge_lbl": "text"}[kind]
        n = _CFGE_ROLE_SLOTS if kind == "cfge_row" else _CFGE_ROWS
        return [getattr(arb._pool[f"{kind}{i}"], attr) for i in range(1, n + 1)
                if arb._pool[f"{kind}{i}"].visible]

    _roles_shown = cfge_shown("cfge_row")
    arb._pool["cfge_row1"].on_press()          # a role picked off the grid
    check("opened a role", arb._cfge_view, "role")
    check("the role starts at its own top", arb._cfge_page, 0)
    arb._pool["menu_cancel_btn"].on_press()
    check("back on the roles",  arb._cfge_view, "roles")
    check("showing the same roles", cfge_shown("cfge_row"), _roles_shown)
    # One level further down: Browse leaves a page of names and CANCEL has
    # to hand back that page, not the first one.
    _long = next(r for r in config.roles() if len(config.items(r)) > _CFGE_ROWS)
    arb._cfge_open_role(_long)
    arb._pool["cfge_next"].on_press()
    _names_p2 = cfge_shown("cfge_lbl")
    _b = next(i for i in (1, 2, 3, 4) if arb._pool[f"cfge_btn{i}"].enabled)
    arb._pool[f"cfge_btn{_b}"].on_press()
    check("browsing a name off page 2", arb._cfge_view, "browse")
    check("the history starts at its own top", arb._cfge_page, 0)
    arb._pool["menu_cancel_btn"].on_press()
    check("back on the role",   arb._cfge_view, "role")
    check("on the page it left", arb._cfge_page, 1)
    check("showing the same names", cfge_shown("cfge_lbl"), _names_p2)
    arb._cfge_open()                           # a fresh entry starts at the top
    check("Menu → Config opens at the top", arb._cfge_page, 0)

    print("== config editor: a role lists its parameters; device_sn is fixed ==")
    arb._cfge_open_role("device")
    check("role view",         arb._cfge_view, "role")
    check("titled for the role", arb._pool["cfge_title"].text, "Config\ndevice")
    check("three device rows", len(arb._cfge_list), 3)
    _sn = cfge_row_of("hardware_sn")
    check("hardware_sn is listed", _sn is not None, True)
    check("hardware_sn read-only", arb._pool[f"cfge_fld{_sn}"].editable, False)
    check("its Browse is greyed",  arb._pool[f"cfge_btn{_sn}"].enabled, False)
    _desc = cfge_row_of("description")
    check("description editable",  arb._pool[f"cfge_fld{_desc}"].editable, True)
    check("shows the value in force",
          arb._pool[f"cfge_fld{_desc}"].value, config.get("device", "description"))

    print("== config editor: typing marks the row and wakes the write buttons ==")
    _fld = arb._pool[f"cfge_fld{_desc}"]
    _fld.set_value("bench rig")
    arb._writethrough(_fld)
    check("edit is pending",   arb._cfge_pending(), [("description", "bench rig")])
    check("field turned yellow", _fld.bg, _palette.color("field_yellow"))
    check("text turned red",     _fld.color, _palette.color("edited_text"))
    check("Save live",         arb._pool["cfge_save"].enabled, True)
    check("Rollback live",     arb._pool["cfge_rollback"].enabled, True)
    check("Default live",      arb._pool["cfge_default"].enabled, True)
    check("paging locked out", arb._pool["cfge_next"].enabled, False)
    check("nothing written yet", config.get("device", "description") != "bench rig", True)

    print("== config editor: Rollback puts the field back ==")
    arb._pool["cfge_rollback"].on_press()
    check("no edit pending",   arb._cfge_pending(), [])
    check("field back to white",
          arb._pool[f"cfge_fld{cfge_row_of('description')}"].bg,
          _palette.color("white"))
    check("Save dead again",   arb._pool["cfge_save"].enabled, False)

    print("== config editor: Save writes it and reloads the row ==")
    _was_desc = config.get("device", "description")
    _fld = arb._pool[f"cfge_fld{cfge_row_of('description')}"]
    _fld.set_value("bench rig")
    arb._writethrough(_fld)
    arb._pool["cfge_save"].on_press()
    check("in force",          config.get("device", "description"), "bench rig")
    check("edit cleared",      arb._cfge_pending(), [])
    check("still on the role", arb._cfge_view, "role")

    print("== config editor: a value its vtype cannot read is refused ==")
    _r1row = cfge_open_at("protocol", "reagent1_secs")
    _fld = arb._pool[f"cfge_fld{_r1row}"]
    _fld.set_value("two minutes")
    arb._writethrough(_fld)
    check("refused",           arb._cfge_write(config.save), False)
    check("unchanged",         config.reagent1_secs(), _file_r1)
    check("a modal says why",  arb._pool["modal_msg"].visible, True)
    check("names the type",    "int" in arb._pool["modal_msg"].text, True)
    arb._pool["modal_yes"].on_press()          # its OK returns to the view
    check("back on the role",  arb._cfge_view, "role")
    # The rejected text comes back with the screen — it is what has to be
    # corrected — and Rollback is the way to abandon it.
    check("rejected text is still there",
          arb._cfge_pending(), [("reagent1_secs", "two minutes")])
    arb._pool["cfge_rollback"].on_press()
    check("Rollback abandons it", arb._cfge_pending(), [])

    print("== config editor: a saved edit reaches the running program ==")
    _fld = arb._pool[f"cfge_fld{cfge_row_of('reagent1_secs')}"]
    _fld.set_value("77")
    arb._writethrough(_fld)
    arb._pool["cfge_save"].on_press()
    check("saved",             config.reagent1_secs(), 77)
    check("phases rebuilt",    arb._phases["reagent1"].secs, 77)

    print("== config editor: Browse lists the history, Use reactivates ==")
    arb._cfge_open_browse("reagent1_secs")
    check("browse view",       arb._cfge_view, "browse")
    check("in force is first", arb._cfge_list[0][0], "77")
    check("row 1 is not offered", arb._pool["cfge_btn1"].enabled, False)
    _old = [i for i, row in enumerate(arb._cfge_page_rows(), 1)
            if row[0] == str(_file_r1)]
    check("the commissioned value is listed", len(_old), 1)
    arb._pool[f"cfge_btn{_old[0]}"].on_press()
    check("reactivated",       config.reagent1_secs(), _file_r1)
    check("no duplicate row",
          _db.read_val("SELECT COUNT(*) FROM config WHERE role='protocol' "
                       "AND name='reagent1_secs' AND value=?", (str(_file_r1),)), 1)

    print("== config editor: Default commissions the edited value ==")
    arb._cfge_open_role("runtime")
    _fld = arb._pool[f"cfge_fld{cfge_row_of('shutdown_timeout_s')}"]
    _fld.set_value("12.5")
    arb._writethrough(_fld)
    arb._pool["cfge_default"].on_press()       # opens the confirm
    check("confirm is up",     arb._pool["modal_msg"].visible, True)
    arb._pool["modal_no"].on_press()           # Cancel writes nothing
    check("cancelled",         config.default("runtime", "shutdown_timeout_s") != 12.5, True)
    # …and comes back to the edit rather than dropping it on the floor.
    check("the edit survived the confirm",
          arb._cfge_pending(), [("shutdown_timeout_s", "12.5")])
    arb._pool["cfge_default"].on_press()
    arb._pool["modal_yes"].on_press()
    check("in force",          config.shutdown_timeout_s(), 12.5)
    check("and commissioned",  config.default("runtime", "shutdown_timeout_s"), 12.5)
    check("edit cleared after Default", arb._cfge_pending(), [])

    print("== config editor: an edit survives a look at the history ==")
    _fld = arb._pool[f"cfge_fld{cfge_row_of('shutdown_timeout_s')}"]
    _fld.set_value("9.0")
    arb._writethrough(_fld)
    arb._cfge_open_browse("shutdown_timeout_s")
    check("browsing",          arb._cfge_view, "browse")
    arb._cfge_cancel()
    check("back on the role",  arb._cfge_view, "role")
    check("still pending",     arb._cfge_pending(), [("shutdown_timeout_s", "9.0")])
    arb._pool["cfge_rollback"].on_press()
    check("rolled back",       arb._cfge_pending(), [])

    print("== config editor: locked out while a slot is live ==")
    A.active_phase = "reagent1"
    arb._cfge_open_role("runtime")
    check("fields disabled",   arb._pool["cfge_fld1"].editable, False)
    check("still viewable",    arb._pool["cfge_fld1"].visible, True)
    arb._cfge_open_browse("shutdown_timeout_s")
    check("Use disabled too",  arb._pool["cfge_btn2"].enabled, False)
    A.active_phase = "entry"

    print("== config editor: CANCEL walks back out to the Menu ==")
    arb._cfge_open_role("runtime")
    arb._cfge_open_browse("shutdown_timeout_s")
    arb._cfge_cancel()
    check("browse → role",     arb._cfge_view, "role")
    arb._cfge_cancel()
    check("role → roles",      arb._cfge_view, "roles")
    arb._cfge_cancel()
    check("roles → menu",      arb._cfge_view, None)
    check("the Menu is up",    arb._pool["menu_capture"].visible, True)

    print("== config editor: a saved default reaches the next sample claimed ==")
    # Nothing is pushed back into the slots any more.  An unclaimed slot holds
    # no identity at all, so an edit has nothing to land on — and, the reason
    # this used to matter, nothing to clobber: a hand-typed sample number is no
    # longer overwritten by a config save.  The defaults are read at Begin, so
    # it is the next claim that picks them up.
    A.clear_run(); B.clear_run()
    arb.focus(A)
    check("an unclaimed slot holds no identity", A.record["user"], "")
    A.record["samp_no"] = "ZZ8888"             # hand-typed: must survive the edit
    arb._cfge_open_role("defaults")
    while cfge_row_of("user") is None:          # 'user' sits on a later page
        arb._pool["cfge_next"].on_press()
    _fld = arb._pool[f"cfge_fld{cfge_row_of('user')}"]
    _fld.set_value("R. Feynman")
    arb._writethrough(_fld)
    arb._pool["cfge_save"].on_press()
    check("saved", config.get("defaults", "user"), "R. Feynman")
    check("a hand-typed number survives the save", A.record["samp_no"], "ZZ8888")
    check("still nothing to clobber",            A.record["user"], "")
    arb._cfge_cancel()
    arb._cfge_cancel()

    arb.focus(A)
    arb._phases["entry"].begin(arb, A)          # Begin is what reads the defaults
    check("the claim picked up the new default", A.record["user"], "R. Feynman")
    check("and it reached the row",
          _db.read_val("SELECT user FROM samples WHERE rowid = ?",
                       (A.record["db_rowid"],)), "R. Feynman")
    A.clear_run(); B.clear_run()

    print("== config editor: Restore Factory Defaults undoes all of it ==")
    arb._cfge_open()
    arb._cfge_confirm_restore_all()
    arb._pool["modal_yes"].on_press()
    check("back on the roles",  arb._cfge_view, "roles")
    check("description restored", config.get("device", "description"), _was_desc)
    # Restoring reaches the slots the same way a save does: not at all until
    # one of them claims a sample.  Both were cleared just above.
    check("the slots are left alone",
          [A.record["user"], B.record["user"]], ["", ""])
    check("shutdown_timeout_s restored",
          config.shutdown_timeout_s() != 12.5, True)
    # device.software_ver excepted: it has no factory row by design — the
    # source tree owns it — so restore_all() re-stamps it with source='code'
    # rather than leaving it with nothing active.  See config_repo.restore_all.
    check("every parameter on its seed row",
          _db.read_val("SELECT COUNT(*) FROM config WHERE active=1 "
                       "AND source <> 'seed' "
                       "AND NOT (role='device' AND name='software_ver')"), 0)
    arb._cfge_cancel()

    # ── LED Color picker (Menu → LED Colors) ─────────────────────────────────
    # Also against the throwaway copy — Restore Factory Defaults just put
    # led_color_order back on its seed row, a known starting sequence.
    _seed_leds = list(config.capture_led_color_order())

    print("== LED picker: opens on the order in force ==")
    arb.open_menu(A)
    arb._pool["menu_defaults"].on_press()
    check("colors staged from config", arb._ledpick_colors, _seed_leds)
    check("nothing selected",  arb._ledpick_selected, None)
    check("nothing pending",   arb._ledpick_pending(), False)
    check("Save dead, clean",  arb._pool["cfge_save"].enabled, False)
    check("Rollbk dead, clean", arb._pool["cfge_rollback"].enabled, False)
    check("Default live, clean", arb._pool["cfge_default"].enabled, True)
    # The active row sorts first in history (ORDER BY active DESC), so the
    # freshly-opened screen sits at index 0 — Prior has nothing before it.
    check("opens at history index 0", arb._ledpick_idx, 0)
    check("Prior dead at index 0", arb._pool["cfge_prior"].enabled, False)
    check("Next enabled iff more history follows",
          arb._pool["cfge_next"].enabled, len(arb._ledpick_history) > 1)
    # dark/jpg trail every active color, fixed, and are never selectable. The
    # seed order fills all 10 LED slots, so dark/jpg land in the last two of
    # the 12 widget slots with none spare — the "hidden when unused" case is
    # exercised below once a color is dropped.
    _n = len(_seed_leds)
    check("dark trails the sequence",
          arb._pool[f"ledpick_act{_n + 1}"].label, "dark")
    check("jpg follows dark",
          arb._pool[f"ledpick_act{_n + 2}"].label, "jpg")
    check("dark is not selectable",
          arb._pool[f"ledpick_act{_n + 1}"].enabled, False)
    # A LED shows in exactly one row: active ones are hidden from "inactive".
    from lighting_cal8 import LEDBank as _LB
    _all_leds = _LB.color_names()
    for _i, _led in enumerate(_all_leds, 1):
        _shown = arb._pool[f"ledpick_ina{_i}"].visible
        check(f"inactive slot for {_led} hidden iff active",
              _shown, _led not in arb._ledpick_colors)

    print("== LED picker: select inactive + up arrow appends it ==")
    # The seed order carries every LED, so drop one first to have somewhere
    # to add it back to.
    _dropped = _seed_leds[-1]
    _drop_i = next(i for i, led in enumerate(_seed_leds, 1)
                   if led == _dropped)
    arb._pool[f"ledpick_act{_drop_i}"].on_press()
    check("selected the dropped-to-be LED", arb._ledpick_selected, _dropped)
    arb._pool["ledpick_arr_down"].on_press()
    check("removed from the sequence", _dropped in arb._ledpick_colors, False)
    check("selection cleared by remove", arb._ledpick_selected, None)
    check("now pending", arb._ledpick_pending(), True)
    # 9 LEDs + dark + jpg = 11 of the 12 widget slots; the 12th sits unused.
    check("the spare trailing slot is hidden",
          arb._pool["ledpick_act12"].visible, False)
    check("Save live, dirty", arb._pool["cfge_save"].enabled, True)
    check("Prior dead while dirty", arb._pool["cfge_prior"].enabled, False)
    _ina_i = _all_leds.index(_dropped) + 1
    arb._pool[f"ledpick_ina{_ina_i}"].on_press()
    check("selected it in the inactive row", arb._ledpick_selected, _dropped)
    check("up arrow live, inactive selected",
          arb._pool["ledpick_arr_up"].enabled, True)
    check("down arrow dead, inactive selected",
          arb._pool["ledpick_arr_down"].enabled, False)
    arb._pool["ledpick_arr_up"].on_press()
    check("appended to the end", arb._ledpick_colors[-1], _dropped)
    check("still selected after adding", arb._ledpick_selected, _dropped)

    print("== LED picker: left/right reorder the selection ==")
    _last_two = arb._ledpick_colors[-2:]
    arb._pool["ledpick_arr_left"].on_press()
    check("swapped with its left neighbour",
          arb._ledpick_colors[-2:], list(reversed(_last_two)))
    arb._pool["ledpick_arr_right"].on_press()
    check("swapped back", arb._ledpick_colors[-2:], _last_two)

    print("== LED picker: Rollbk discards, Save writes and re-bases pending ==")
    arb._pool["cfge_rollback"].on_press()
    check("back to the value in force", arb._ledpick_colors, _seed_leds)
    check("selection cleared",  arb._ledpick_selected, None)
    check("clean again",        arb._ledpick_pending(), False)
    arb._ledpick_colors.remove(_dropped)
    _staged = list(arb._ledpick_colors)
    arb._pool["cfge_save"].on_press()
    check("config now holds the staged order",
          config.capture_led_color_order(), _staged)
    check("clean after save",   arb._ledpick_pending(), False)
    check("rowid shown, not '(unsaved)'",
          arb._pool["ledpick_rowid"].text.startswith("rowid "), True)

    print("== LED picker: Default jumps to the seed order without saving ==")
    arb._pool["cfge_default"].on_press()
    check("staged back to the seed order", arb._ledpick_colors, _seed_leds)
    check("Default is a jump, not a write — config unchanged",
          config.capture_led_color_order(), _staged)
    check("now pending (differs from what's in force)",
          arb._ledpick_pending(), True)
    check("Default itself goes dead while pending",
          arb._pool["cfge_default"].enabled, False)

    print("== LED picker: locked while a slot is mid-protocol ==")
    arb._pool["cfge_rollback"].on_press()      # back to the clean, in-force value
    A.active_phase = "reagent1"                 # marks A as past entry
    arb._menu_open_ledpick()
    check("swatches disabled while a slot is active",
          arb._pool["ledpick_act1"].enabled, False)
    check("arrows disabled too",
          arb._pool["ledpick_arr_up"].enabled, False)
    A.active_phase = "entry"
    config.save("capture", "led_color_order", ",".join(_seed_leds))
    arb._ledpick_cancel()

    # ── Image viewer (Tool 1) ─────────────────────────────────────────────────
    print("== image viewer: camera guard ==")
    import numpy as _np
    import layout as _L
    import roi as _roi

    class _PreviewCam:
        """
        A camera that returns a recognisable synthetic frame.

        Its raw level rises with the exposure and clips at 10-bit full scale,
        so the viewer's sample metering has something real to converge on: at
        the 40 ms fallback the sample reads clipped, exactly the overexposed
        preview the metering exists to fix.

        The bright patch is painted at whatever rectangle the sample ROI
        actually holds, read the same way _meter_sample_level() reads it,
        rather than at the commissioned coordinates.  These tests run against
        a copy of the live database, and an instrument whose sample ROI has
        been nudged would otherwise meter a patch of zeros and drive the
        exposure to the ceiling — a failure about the test's own geometry, not
        about the metering.
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
        def sample_rect(self):
            """Where to paint the sample — the ROI in force, else as commissioned."""
            r = _roi.by_role(arb._view_rois, _roi.ROLE_SAMPLE)
            return ((r.y, r.y2(), r.x, r.x2()) if r is not None
                    else (790, 858, 1150, 1218))
        def capture_frame(self):
            self.frames += 1
            y0, y1, x0, x1 = self.sample_rect()
            f = _np.zeros((1296, 2304, 3), dtype=_np.uint8)
            f[y0:y1, x0:x1] = (0, 200, 250)           # BGR: the sample, orange
            bayer = _np.zeros((1296, 2304), _np.uint16)
            bayer[y0:y1, x0:x1] = self.sample_level()
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
    # Bound from roles, never spelled out: the operator renames ROIs from the
    # instrument (black1/black2 are already chip1/chip2), and a literal list
    # here would fail on their machine while passing on a freshly seeded one.
    check("ROIs loaded",            len(arb._view_rois) > 0, True)
    check("the roles the viewer needs are filled",
          _roi.missing_roles(arb._view_rois,
                             (_roi.ROLE_SAMPLE, _roi.ROLE_BOUND)), [])
    _SAMPLE = _roi.by_role(arb._view_rois, _roi.ROLE_SAMPLE).name
    _BOUND  = _roi.by_role(arb._view_rois, _roi.ROLE_BOUND).name
    def _sample_label():
        """What the name strip should read for the sample, as it stands now.

        Computed on demand rather than captured once: the edit tests resize
        the ROI, so a constant would go stale halfway through.
        """
        r = arb._view_rois[_SAMPLE]
        return f"{_SAMPLE} {r.w}x{r.h}"
    print(f"     (sample role -> {_SAMPLE!r}, bound role -> {_BOUND!r})")
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

    arb._view_state.fit_to(arb._view_rois[_SAMPLE])
    check("nothing selected initially", arb._view_selected, None)
    _tap_on(_SAMPLE)
    check("tap inside sample selects the smallest", arb._view_selected, _SAMPLE)
    check("name strip shows it",  arb._pool["img_name"].text, _sample_label())
    _tap_on(_SAMPLE)            # sample and bound both contain this point
    check("second tap moves to the other candidate", arb._view_selected, _BOUND)
    _tap_on(_SAMPLE)
    check("third tap returns — a two-way toggle", arb._view_selected, _SAMPLE)
    # A tap on empty image space clears the selection.
    arb._view_state.fit()
    arb.on_touch(_L.IMG_CANVAS.x + 3, _L.IMG_CANVAS.y + 3)
    check("tap on empty space deselects", arb._view_selected, None)

    print("== image viewer: the preview is metered on the sample ROI ==")
    # The sample's median must land within tolerance of the target, and the
    # exposure that achieved it is remembered for the next preview.
    _lvl = pcam.sample_level()
    # Printed before the checks, so a convergence failure says what it reached.
    print(f"     converged to {pcam.exposure_us} us in {pcam.frames} frame(s), "
          f"sample median {_lvl} of {_PREVIEW_TARGET}")
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
    _saved_sample = arb._view_rois[_SAMPLE]
    from rect import Rect as _Rect
    arb._view_rois[_SAMPLE] = _Rect(9000, 9000, 68, 68, name=_SAMPLE)
    check("an off-frame sample is not metered",
          arb._meter_sample_level(_np.zeros((1296, 2304), _np.uint16), pcam), None)
    arb._view_rois.pop(_SAMPLE)
    check("no sample ROI at all is not metered",
          arb._meter_sample_level(_np.zeros((1296, 2304), _np.uint16), pcam), None)
    arb._view_rois[_SAMPLE] = _saved_sample

    print("== image viewer: 1px/10px toggle drives both modes ==")
    check("starts at 1px",   arb._pool["img_step"].label, "1px")
    arb._view_state.fit_to(arb._view_rois[_SAMPLE])   # zoomed in, so pan bites
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
    _tap_on(_SAMPLE)
    check("tap selects",       arb._view_selected, _SAMPLE)
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

    print("== test pad areas: Capture page → Pad areas → viewer ==")
    _db.write_one("DELETE FROM roi WHERE active = 2")
    arb._menu_open_capture()
    arb._cap_pick_led("green")
    arb._pool["cap_pad"].on_press()
    check("viewer open on the pad",   (arb._view_open, arb._view_pad), (True, True))
    check("three patches made from the sample", sorted(arb._view_rois),
          sorted(_roi.TEST_PAD_ROLES))
    _s = _roi.by_role(_roi.load_active(), _roi.ROLE_SAMPLE)
    _ys = {n: r.y for n, r in arb._view_rois.items()}
    check("yellow above brown above white",
          _ys["pad_yellow"] < _ys["pad_brown"] < _ys["pad_white"], True)
    check("pad lies inside the sample",
          all(r.y >= _s.y and r.y2() <= _s.y2() for r in arb._view_rois.values()),
          True)
    arb._view_selected = "pad_brown"
    arb._view_edit()
    arb._view_editing.y = arb._view_editing.y + 2
    _new = arb._view_editing.xywh()
    arb._view_save()
    check("brown patch saved in the pad set",
          _roi.load_test_pad()[_roi.ROLE_PAD_BROWN].xywh(), _new)
    check("measurement ROIs untouched",
          _roi.by_role(_roi.load_active(), _roi.ROLE_SAMPLE).xywh(), _s.xywh())
    arb._view_exit()
    check("Exit returns to the Capture page", arb._cap_view, "choice")
    check("viewer closed",            arb._view_open, False)
    check("pad mode cleared",         arb._view_pad, False)
    check("LED choice kept",          arb._cap_led, "green")
    arb._cap_choice_cancel()
    arb.open_image_view(slot=None)
    check("a normal open shows the measurement ROIs",
          _roi.ROLE_PAD_BROWN in [r.role for r in arb._view_rois.values()], False)
    arb._view_exit()

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
    check("sample preselected", arb._view_selected, _SAMPLE)
    _sample = arb._view_rois[_SAMPLE]
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
    check("name strip names it", arb._pool["img_name"].text, _sample_label())

    print("== image viewer: Tool 2's Exit returns to the read screen ==")
    arb._view_exit()
    check("focus is back on the slot", arb._focused is A, True)
    check("still the read phase",      A.phase, "read")
    check("View Sample offered again", arb._pool["view_sample_btn"].visible, True)

    # ── Edit mode (step 8) ────────────────────────────────────────────────────
    # Runs against the scratch DB copy, so real roi.save()/restore() are
    # exercised without touching the live colorimeter.sqlite.
    print("== edit mode: Edit is refused while a slot is past entry ==")
    # ROI geometry must not move under a run that has already read it, so
    # _any_slot_active() gates Edit.  The Tool 2 block above left A on the
    # read phase, which is exactly that case.
    arb._view_exit()
    arb.open_image_view(slot=None, focus_role=_roi.ROLE_SAMPLE)
    check("a live slot locks Edit", arb._pool["img_edit"].enabled, False)
    arb._view_exit()

    print("== edit mode: Edit takes a working copy and zooms to it ==")
    A.active_phase = "entry"          # park the run; the geometry is free again
    arb.open_image_view(slot=None, focus_role=_roi.ROLE_SAMPLE)
    check("Edit enabled with a selection", arb._pool["img_edit"].enabled, True)
    check("Save/Cancel wait",
          [arb._pool[n].enabled for n in ("img_save", "img_cancel")], [False, False])
    _orig = arb._view_rois[_SAMPLE].xywh()
    arb._view_edit()
    check("in edit mode",     arb._view_mode, "edit")
    check("working copy matches the stored ROI",
          arb._view_editing.xywh(), _orig)
    check("stored ROI untouched", arb._view_rois[_SAMPLE].xywh(), _orig)
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
    arb._view_editing = _roi.Rect(*_orig, name=_SAMPLE)
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
    arb._view_editing = _roi.Rect(*_orig, name=_SAMPLE)
    arb._view_arrow(0, 1)
    check("1px step", arb._view_editing.h, 69)
    arb._view_armed = "right"
    for _ in range(200):                            # try to collapse it
        arb._view_step = 10
        arb._view_arrow(-1, 0)
    check("cannot shrink past the minimum",
          arb._view_editing.w, _EDIT_MIN_SIDE)
    arb._view_armed = None
    arb._view_editing = _roi.Rect(*_orig, name=_SAMPLE)
    for _ in range(500):
        arb._view_arrow(-1, -1)
    check("cannot be pushed off the frame",
          (arb._view_editing.x, arb._view_editing.y), (0, 0))

    print("== edit mode: Cancel discards ==")
    arb._view_editing = _roi.Rect(9, 9, 30, 30, name=_SAMPLE)
    arb._view_cancel()
    check("back to view mode",  arb._view_mode, "view")
    check("working copy gone",  arb._view_editing, None)
    check("stored ROI unchanged", _roi.load_active()[_SAMPLE].xywh(), _orig)
    check("says cancelled",     arb._pool["img_name"].text, "cancelled")
    check("Exit offered again", arb._pool["img_exit"].enabled, True)

    print("== edit mode: Save validates before writing ==")
    def _try_save(rect):
        # A working copy carries its role — _view_edit() copies it out of
        # _view_rois — and Save's sample-inside-bound rule looks the edited
        # rectangle up by role, so a hand-built rect has to carry one too.
        rect.role = _roi.ROLE_SAMPLE
        arb._view_edit()
        arb._view_editing = rect
        arb._view_save()
        return arb._view_mode, arb._pool["img_name"].text

    check("too small is refused",
          _try_save(_roi.Rect(1150, 790, 8, 8, name=_SAMPLE)),
          ("edit", f"min {_EDIT_MIN_SIDE}px"))
    arb._view_cancel()
    check("outside the frame is refused",
          _try_save(_roi.Rect(2290, 790, 40, 40, name=_SAMPLE)),
          ("edit", "outside frame"))
    arb._view_cancel()
    check("a sample outside bound is refused",
          _try_save(_roi.Rect(100, 100, 40, 40, name=_SAMPLE)),
          ("edit", "sample outside bound"))
    arb._view_cancel()
    check("nothing was written by any of them",
          _roi.load_active()[_SAMPLE].xywh(), _orig)

    print("== edit mode: Save writes, snapping to even ==")
    # Inside whatever the bound is now: it moves when the pad is re-measured.
    _bx, _by = _roi.load_active()["bound"].xywh()[:2]
    _bx, _by = _bx + 50 - _bx % 2, _by + 50 - _by % 2
    arb._view_edit()
    arb._view_editing = _roi.Rect(_bx + 1, _by + 1, 41, 41, name=_SAMPLE)
    arb._view_editing.role = _roi.ROLE_SAMPLE
    arb._view_save()
    check("back to view mode",   arb._view_mode, "view")
    check("snapped even and stored",
          _roi.load_active()[_SAMPLE].xywh(), (_bx, _by, 40, 40))
    check("Bayer-safe",   _roi.is_bayer_safe(_roi.load_active()[_SAMPLE]), True)
    check("the screen reloaded it",
          arb._view_rois[_SAMPLE].xywh(), (_bx, _by, 40, 40))
    check("says what it saved", arb._pool["img_name"].text, "saved 40x40")
    check("history kept, not overwritten", _db.read_val(
        "SELECT COUNT(*) FROM roi WHERE name='sample' AND active=0") >= 1, True)

    print("== edit mode: restore puts the commissioned default back ==")
    # The commissioned rectangle, not _orig: _orig is what was in force when
    # this block started, which on an instrument whose ROI has been nudged is
    # an operator row, and restore() deliberately goes back past it.
    _default_sample = _roi.load_defaults()[_SAMPLE].xywh()
    _roi.restore(_SAMPLE)
    check("default is back", _roi.load_active()[_SAMPLE].xywh(), _default_sample)

    print("== image viewer: reimaging keeps where you were looking ==")
    arb._view_state.fit_to(arb._view_rois[_SAMPLE])
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
