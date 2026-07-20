"""
app_controller.py  —  Colorimeter application controller.
----------------------------------------------------------
Single place that owns:
  - Hardware lifetime  (display, touch, keyboard, beeper, backlight)
  - Screen construction and wiring
  - All button callbacks and navigation logic
  - The data record that the entry form edits
  - The active_slot guard (only one test runs at a time)
  - The main event loop (calls ScreenManager.tick() each iteration)

Screen modules (proto_screen, proto_screen1, …) only build layout.
All behaviour — what a button press *does* — is wired here.

Usage
-----
    from app_controller import AppController
    AppController().start()
"""

import datetime
import logging
import os
import signal
import sys
import time
import pathlib

_SRC = pathlib.Path(__file__).parent
_DEV = _SRC.parent / "dev"
sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(_DEV))

# Use mock hardware if COLORIMETER_SIMULATION=1
_SIMULATION_MODE = os.environ.get('COLORIMETER_SIMULATION', '0') == '1'

if _SIMULATION_MODE:
    from mock.gpiozero_mock import DigitalOutputDevice
    from mock.ili9341_mock import ILI9341
    from mock.tsc2007_mock import TSC2007
    from mock import beeper_mock as beeper
    from mock.kb_reader_mock import KbReader
    log_prefix = "[SIMULATION] "
else:
    from gpiozero import DigitalOutputDevice
    from ili9341 import ILI9341
    from tsc2007 import TSC2007
    import beeper
    from kb_reader import KbReader
    log_prefix = ""

import logging_manager
import widgets
from widgets import Button, Label, Screen, TextBox
from screen_manager import ScreenManager
import csv_to_widgets
from capture_runner import CaptureConfig
from capture_session import CaptureSession

# Legacy imports (can be removed after all screens converted to CSV)
# from proto_screen  import build_screen as _build_measure_screen
# from proto_screen1 import build_screen as _build_entry_screen

log = logging.getLogger(__name__)

# ── Application constants ─────────────────────────────────────────────────────

_BL_PIN    = 13
_POLL_S    = 0.02      # main-loop sleep / touch-poll cadence (seconds)

# Form field keys — defines the data model for the entry screen.
# Order matches the visual row order.
_FIELD_KEYS = ["id", "user", "test", "collect"]

# Widgets are named by their numeric id from widget_defs.csv, so that is what
# Screen.find() matches on. The `remarks` column reads like a name ("id_field")
# but csv_loader does not parse it, and it is not unique — 903 and 904 share
# "next_btn button" — so ids remain the identity here.
_SE_FIELD_WIDGETS = {
    "id":      "112",
    "user":    "114",
    "test":    "116",
    "collect": "118",
}
_CLOCK_WIDGET     = "902"   # shared status-bar clock, present on every screen
_COUNTDOWN_WIDGET = "211"   # TextBox: MM:SS left on a timed screen, if present

# Widget ids on the Capture screen. Widgets are named by their numeric id in
# widget_defs.csv, so these are what Screen.find() matches on.
_CA_START_BTN = "212"   # begins a capture run
_CA_STATUS_1  = "213"   # Label: current step
_CA_STATUS_2  = "214"   # Label: detail / result

# LED channels a capture sweeps. Full sweep is 1–10; see lighting_cal8.
_CAPTURE_LED_START = 1
_CAPTURE_LED_END   = 10


# ── AppController ─────────────────────────────────────────────────────────────

class AppController:
    """
    Central controller.  One instance per application run.

    Call start() to initialise hardware, build screens, and enter the
    blocking event loop.  cleanup() is called automatically on SIGINT/SIGTERM.
    """

    def __init__(self) -> None:
        # Hardware handles
        self._backlight = None
        self._disp      = None
        self._touch     = None
        self._kb        = KbReader()

        # Screens and manager
        self._screens: dict[str, Screen]  = {}  # {screen_id: Screen}
        self._mgr: ScreenManager | None   = None

        # Active slot guard: 'A', 'B', or None.
        # Only one test runs at a time; Start is blocked while this is set.
        self.active_slot: str | None = None

        # The capture in flight, pumped once per event-loop pass. None when
        # no capture is running.
        self._capture: CaptureSession | None = None

        # Data model: the values the entry form displays and edits.
        self.record: dict[str, str] = {
            "id":      "AB0124",
            "user":    "William Olsen",
            "test":    "Ascel  Lot 1H37822",
            "collect": "",
        }

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Initialise hardware, load screens from CSV, then enter the event loop."""
        self._init_logging()
        if _SIMULATION_MODE:
            log.info("=" * 60)
            log.info("SIMULATION MODE: using mock hardware drivers")
            log.info("Set COLORIMETER_SIMULATION=0 to use real hardware")
            log.info("=" * 60)
        self._init_hardware()
        self._build_and_wire_screens()
        self._navigate_to("SE")  # Start at Sample Entry screen
        self._run()

    @staticmethod
    def _init_logging() -> None:
        """
        Route both logging systems to the terminal at one chosen level.

        The level comes from $COLORIMETER_LOGLEVEL (DEBUG, INFO, WARNING,
        ERROR), defaulting to INFO, so a level can be raised from the launch
        command over ssh without editing the source:

            COLORIMETER_LOGLEVEL=DEBUG python3 src/main.py

        Modules are split across stdlib logging (app_controller, widgets,
        screen_manager, capture_runner) and the logging_manager module
        (bayer_cam, roi, db_manager, error_handler); one variable sets both.
        """
        name = (os.environ.get("COLORIMETER_LOGLEVEL") or "INFO").upper()
        level = getattr(logging, name, None)
        if not isinstance(level, int):
            print(f"Unknown COLORIMETER_LOGLEVEL {name!r} — using INFO")
            name, level = "INFO", logging.INFO

        logging.basicConfig(level=level,
                            format="%(levelname)-8s [%(name)s] %(message)s")
        logging_manager.set_level(name)
        log.info("Logging at %s (set COLORIMETER_LOGLEVEL to change)", name)

    def cleanup(self) -> None:
        """Silence hardware and release resources.  Safe to call multiple times."""
        log.info("AppController: cleanup")
        # Cancel first: this turns off any lit LED and closes the archive.
        if self._capture is not None:
            self._capture.cancel()
            self._capture = None
        try:
            if self._disp:
                self._disp.fill_screen(ILI9341.BLACK)
        except Exception:
            pass
        self._kb.cleanup()
        beeper.cleanup()
        if self._touch:
            self._touch.cleanup()
        if self._disp:
            self._disp.cleanup()
        if self._backlight:
            self._backlight.close()

    # ── Hardware ──────────────────────────────────────────────────────────────

    def _init_hardware(self) -> None:
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

        # In simulation mode, wire display events to touch and keyboard
        if _SIMULATION_MODE and hasattr(self._disp, 'register_key_handler'):
            self._disp.register_key_handler(self._kb._on_key_event)  # type: ignore
            self._disp.register_mouse_handler(self._touch._on_touch)  # type: ignore
            self._disp.register_touch_release_handler(self._touch._on_release)  # type: ignore
            log.info("AppController: wired display events to touch and keyboard")

        # Tell the widget layer which sound to play on button press.
        widgets.set_button_sound(beeper.click)

    # ── Screen construction & wiring ──────────────────────────────────────────

    def _build_and_wire_screens(self) -> None:
        """Load screens from CSV, then wire all static callbacks."""
        config_dir = _SRC.parent / "config"

        try:
            self._screens = csv_to_widgets.load_all_screens(
                widget_defs_path=str(_SRC / "widget_defs.csv"),
                screen_defs_path=str(_SRC / "screen_defs.csv"),
                screen_widgets_path=str(_SRC / "screen_widgets.csv"),
                bg_color=0xFFFF,  # white background
            )
        except (IOError, ValueError) as e:
            log.error("Failed to load screens from CSV: %s", e)
            raise

        if not self._screens:
            raise RuntimeError("No screens loaded from CSV")

        self._mgr = ScreenManager(self._disp)
        self._wire_callbacks()

    def _wire_callbacks(self) -> None:
        """Wire button callbacks for all screens."""
        for screen_id, screen in self._screens.items():
            # Timed screens ring and release the slot guard when they expire.
            if screen.duration > 0:
                screen.on_timeout = self._on_phase_timeout
                log.info("Wired timeout on screen %r (%ds)", screen_id, screen.duration)

            # Wire Start button (if present) to start a test
            start_btn = screen.find("103")  # widget_id for start button
            if isinstance(start_btn, Button):
                start_btn.on_press = lambda slot='A': self._start_test(slot)
                log.info("Wired Start button on screen %r", screen_id)

            # Wire the Capture screen's Start button to begin a capture run
            if screen_id == "CA":
                capture_btn = screen.find(_CA_START_BTN)
                if isinstance(capture_btn, Button):
                    capture_btn.on_press = self._toggle_capture
                    log.info("Wired capture button on screen %r", screen_id)

            # Wire any button with nav_hint as a NEXT button
            for widget in screen.tabbed_widgets + screen.untabbed_widgets:
                if isinstance(widget, Button) and widget.nav_hint in ('NO_CLEAR', 'CLEAR_ALL'):
                    if screen.next_screen_id:
                        target_screen_id = screen.next_screen_id
                        # nav_hint='NO_CLEAR' → clear_first=False; otherwise clear_first=True
                        should_clear = (widget.nav_hint != 'NO_CLEAR')
                        widget.on_press = lambda target=target_screen_id, c=should_clear: self._navigate_to(target, clear_first=c)
                        log.info("Wired NEXT button on screen %r (nav_hint=%r, clear_first=%s) → %r",
                                 screen_id, widget.nav_hint, should_clear, target_screen_id)

    # ── Slot management ───────────────────────────────────────────────────────

    def _start_test(self, slot: str = 'A') -> None:
        """
        Start a test run for slot.  No-op if a test is already running.

        This is the single guard point for the one-test-at-a-time constraint.
        Wire the Start button to this method.
        """
        if self.active_slot is not None:
            log.warning("AppController: Start blocked — slot %s already running",
                        self.active_slot)
            return
        self.active_slot = slot
        log.info("AppController: test started in slot %s", slot)
        self._navigate_to("CA")

    def _complete_test(self) -> None:
        """Mark the active test complete and clear the slot guard."""
        log.info("AppController: test complete (slot %s)", self.active_slot)
        self.active_slot = None
        self._navigate_to("SE")

    # ── Capture ───────────────────────────────────────────────────────────────

    def _toggle_capture(self) -> None:
        """Start a capture, or cancel the one in flight. Wired to CA Start."""
        if self._capture is not None and self._capture.active:
            self._capture.cancel()
            self._set_capture_status("Capture cancelled", "")
            self._capture = None
            return

        config = CaptureConfig(led_start=_CAPTURE_LED_START,
                               led_end=_CAPTURE_LED_END)
        session = CaptureSession(config)
        self._set_capture_status("Starting camera...", "")

        if not session.start():
            self._set_capture_status("Camera failed", str(session.error)[:40])
            return

        self._capture = session
        log.info("AppController: capture started → %s", config.out_dir)

    def _pump_capture(self) -> None:
        """
        Advance the running capture by one step and repaint its progress.

        Called once per event-loop pass. One step is one camera operation, so
        the loop keeps servicing touch and the clock between frames instead of
        blocking for the minutes a full sweep takes.
        """
        session = self._capture
        if session is None or not session.active:
            return

        progress = session.step()

        if progress is not None:
            # Transient updates are warmup countdowns; they carry no new step.
            if not progress.transient:
                log.debug("capture: %s", progress.message)
            self._show_progress(progress)
            return

        # step() returned None: the run just ended, one way or another.
        if session.error is not None:
            self._set_capture_status("Capture failed", str(session.error)[:40])
        elif session.out_path is not None:
            self._set_capture_status("Capture complete", session.out_path.name)
        self._capture = None

    def _show_progress(self, progress) -> None:
        """
        Repaint the Capture screen from one Progress value.

        LED position rides on the detail line rather than in widget 211:
        that widget is the phase countdown, which runs concurrently with a
        capture and would otherwise be overwritten every step.
        """
        detail = progress.color or f"{progress.fraction * 100:.0f}%"
        if progress.led_total:
            detail = f"{progress.led_index}/{progress.led_total}  {detail}"
        self._set_capture_status(progress.message[:34], detail)

    def _set_capture_status(self, line1: str, line2: str) -> None:
        """
        Write the two capture status lines.

        Only touches the Capture screen, and only when it is the active one —
        drawing to a screen the user has navigated away from would corrupt
        whatever is on the display.

        Unchanged text is not redrawn. Progress arrives every event-loop pass
        during a warmup countdown, and each redraw is an SPI write to the
        display, so repainting identical text would cost far more than it
        shows.
        """
        screen = self._screens.get("CA")
        if screen is None or self._mgr is None or self._mgr.active_screen is not screen:
            return

        for widget_id, text in ((_CA_STATUS_1, line1), (_CA_STATUS_2, line2)):
            widget = screen.find(widget_id)
            if isinstance(widget, Label) and widget.text != text:
                widget.text = text
                widget.draw(self._disp)

    # ── Timed phases ──────────────────────────────────────────────────────────

    def _run_beginactions(self, screen: Screen) -> None:
        """
        Run the actions a screen declares in screen_defs.csv `beginactions`,
        on entry to that screen.

        Recognised actions:
            start_timer         — start the phase countdown and register the
                                  screen for timeout monitoring
            alarm_on/alarm_off  — sound or silence the alarm

        The alarm actions are resolved on the beeper module rather than
        called outright: the simulation beeper defines them, the Pi beeper
        does not, and a missing one must not take the screen down.
        """
        for action in screen.beginactions:
            if action == "start_timer":
                if screen.duration <= 0:
                    log.warning("start_timer on screen %r with duration 0 — ignored",
                                screen.screen_id)
                    continue
                screen.start_timer()
                self._mgr.add_alive(screen)
                self._update_countdown()
            elif action in ("alarm_on", "alarm_off"):
                fn = getattr(beeper, action, None)
                if callable(fn):
                    fn()
                else:
                    log.debug("beeper has no %s() — action skipped", action)
            else:
                log.warning("Unknown beginaction %r on screen %r",
                            action, screen.screen_id)

    def _update_countdown(self) -> None:
        """
        Repaint the active screen's countdown widget, if it has one running.

        A no-op on screens without a timer or without widget 211, so the
        event loop can call it unconditionally.
        """
        screen = self._mgr.active_screen if self._mgr else None
        if screen is None or not screen.is_alive or screen.duration <= 0:
            return
        remaining = max(0, int(round(screen.duration - screen.elapsed())))
        text = f"{remaining // 60:02d}:{remaining % 60:02d}"
        box = screen.find(_COUNTDOWN_WIDGET)
        if isinstance(box, TextBox) and box.value != text:
            box.set_value(text)
            box.draw(self._disp)

    def _on_phase_timeout(self, screen: Screen) -> None:
        """
        A timed phase ran out: sound the alarm and release the slot guard.

        The screen is deliberately left showing. A capture sweep outlasts the
        two-minute phase, and navigating away mid-run would stop its progress
        from being drawn; the user moves on with NEXT when ready.
        """
        log.info("AppController: phase %r timed out",
                 screen.screen_id or screen.name)
        alarm = getattr(beeper, "alarm_on", None) or getattr(beeper, "beep", None)
        if callable(alarm):
            alarm()
        if self.active_slot is not None:
            log.info("AppController: test complete (slot %s)", self.active_slot)
            self.active_slot = None

    # ── Navigation ────────────────────────────────────────────────────────────

    def _navigate_to(self, screen_id: str, clear_first: bool = True) -> None:
        """
        Switch to the named screen.
        Specializes for certain screens (e.g., SE = Sample Entry).
        clear_first: if False, skip full screen clear (for partial updates).
        """
        if screen_id not in self._screens:
            log.warning("AppController: unknown screen_id %r", screen_id)
            return

        screen = self._screens[screen_id]

        # Sample Entry (SE): populate fields from record on entry
        if screen_id == "SE":
            for key in _FIELD_KEYS:
                field = screen.find(_SE_FIELD_WIDGETS[key])
                if isinstance(field, TextBox):
                    value = self.record.get(key, "")
                    if value:
                        field.set_value(value)
                        field.draw(self._disp)

        # Capture (CA): save edited fields to record on entry
        elif screen_id == "CA":
            se_screen = self._screens.get("SE")
            if se_screen:
                for key in _FIELD_KEYS:
                    field = se_screen.find(_SE_FIELD_WIDGETS[key])
                    if isinstance(field, TextBox):
                        self.record[key] = field.value
            log.info("AppController: record = %s", self.record)

        self._mgr.switch_to(screen, clear_first=clear_first)
        log.info("AppController: navigated to screen %r (clear_first=%s)", screen_id, clear_first)
        self._run_beginactions(screen)

    # ── Event loop ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        log.info("AppController: running — Ctrl-C to exit")
        last_clock = 0.0

        while True:
            now = time.monotonic()

            # Process matplotlib events (simulation mode only)
            if _SIMULATION_MODE and hasattr(self._disp, 'process_events'):
                self._disp.process_events()  # type: ignore

            # Alive-screen timeout monitor.
            self._mgr.tick()

            # Advance any capture in flight by one step.
            self._pump_capture()

            # Clock and phase countdown: once per second on the active screen.
            if now - last_clock >= 1.0:
                last_clock = now
                clock = self._mgr.active_screen.find(_CLOCK_WIDGET)
                if isinstance(clock, TextBox):
                    clock.set_value(datetime.datetime.now().strftime("%H:%M:%S"))
                    clock.draw(self._disp)
                self._update_countdown()

            # Keyboard input.
            key = self._kb.read_one()
            if key:
                self._mgr.on_key(key)

            # Touch input.
            if self._touch.is_touched:  # type: ignore
                pos = self._touch.position()  # type: ignore
                log.info(f"AppController: touch detected at {pos}, is_touched={self._touch.is_touched}")  # type: ignore
                if pos:
                    self._mgr.on_touch(*pos)
                # Wait for touch release with timeout. In simulation the
                # release event only arrives through process_events(), so it
                # must be pumped here or every click waits out the timeout.
                timeout = time.monotonic() + 1.0  # 1 second timeout
                while self._touch.is_touched and time.monotonic() < timeout:  # type: ignore
                    if _SIMULATION_MODE and hasattr(self._disp, 'process_events'):
                        self._disp.process_events()  # type: ignore
                    time.sleep(_POLL_S)
                if time.monotonic() >= timeout:
                    log.warning("AppController: touch timeout — forcing release")
                    self._touch._is_touched = False  # type: ignore

            time.sleep(_POLL_S)
