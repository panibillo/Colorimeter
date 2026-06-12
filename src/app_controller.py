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

import widgets
from widgets import Button, Screen, TextBox
from screen_manager import ScreenManager
import csv_to_widgets

# Legacy imports (can be removed after all screens converted to CSV)
# from proto_screen  import build_screen as _build_measure_screen
# from proto_screen1 import build_screen as _build_entry_screen

log = logging.getLogger(__name__)

# ── Application constants ─────────────────────────────────────────────────────

_BL_PIN    = 13
_POLL_S    = 0.02      # main-loop sleep / touch-poll cadence (seconds)

# Form field keys — defines the data model for the entry screen.
# Order matches the visual row order; names match widget names in proto_screen1.
_FIELD_KEYS = ["id", "user", "test", "collect"]


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
        logging.basicConfig(level=logging.INFO,
                            format="%(levelname)-8s [%(name)s] %(message)s")
        if _SIMULATION_MODE:
            log.info("=" * 60)
            log.info("SIMULATION MODE: using mock hardware drivers")
            log.info("Set COLORIMETER_SIMULATION=0 to use real hardware")
            log.info("=" * 60)
        self._init_hardware()
        self._build_and_wire_screens()
        self._navigate_to("SE")  # Start at Sample Entry screen
        self._run()

    def cleanup(self) -> None:
        """Silence hardware and release resources.  Safe to call multiple times."""
        log.info("AppController: cleanup")
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
            # Wire Start button (if present) to start a test
            start_btn = screen.find("103")  # widget_id for start button
            if isinstance(start_btn, Button):
                start_btn.on_press = lambda slot='A': self._start_test(slot)
                log.info("Wired Start button on screen %r", screen_id)

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
                field = screen.find(f"{key}_field")
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
                    field = se_screen.find(f"{key}_field")
                    if isinstance(field, TextBox):
                        self.record[key] = field.value
            log.info("AppController: record = %s", self.record)

        self._mgr.switch_to(screen, clear_first=clear_first)
        log.info("AppController: navigated to screen %r (clear_first=%s)", screen_id, clear_first)

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

            # Clock: update the active screen's clock widget once per second.
            if now - last_clock >= 1.0:
                last_clock = now
                clock = self._mgr.active_screen.find("clock")
                if isinstance(clock, TextBox):
                    clock.set_value(datetime.datetime.now().strftime("%H:%M:%S"))
                    clock.draw(self._disp)

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
                # Wait for touch release with timeout
                timeout = time.monotonic() + 1.0  # 1 second timeout
                while self._touch.is_touched and time.monotonic() < timeout:  # type: ignore
                    time.sleep(_POLL_S)
                if time.monotonic() >= timeout:
                    log.warning("AppController: touch timeout — forcing release")
                    self._touch._is_touched = False  # type: ignore

            time.sleep(_POLL_S)
