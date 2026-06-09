"""
app_controller.py  —  Colorimeter application controller.
----------------------------------------------------------
Single place that owns:
  - Hardware lifetime  (display, touch, keyboard, beeper, backlight)
  - Screen construction
  - All button callbacks and navigation logic
  - The data record that the entry form edits
  - The main event loop

Screen modules (proto_screen, proto_screen1, …) only build layout.
All behaviour — what a button press *does* — is wired here.

Usage
-----
    from app_controller import AppController
    AppController().start()
"""

import datetime
import logging
import signal
import sys
import time
import pathlib

_SRC = pathlib.Path(__file__).parent
_DEV = _SRC.parent / "dev"
sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(_DEV))

from gpiozero import DigitalOutputDevice
from ili9341 import ILI9341
from tsc2007 import TSC2007
import beeper
import widgets
from widgets import Button, Screen, TextBox
from screen_manager import ScreenManager
from kb_reader import KbReader

from proto_screen  import build_screen as _build_measure_screen
from proto_screen1 import build_screen as _build_entry_screen

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
        self._entry_screen:   Screen | None = None
        self._measure_screen: Screen | None = None
        self._mgr:  ScreenManager | None    = None

        # Data model: the values the entry form displays and edits.
        self.record: dict[str, str] = {
            "id":      "AB0124",
            "user":    "William Olsen",
            "test":    "Ascel  Lot 1H37822",
            "collect": "",
        }

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Initialise hardware, build screens, then enter the event loop."""
        logging.basicConfig(level=logging.INFO,
                            format="%(levelname)-8s [%(name)s] %(message)s")
        self._init_hardware()
        self._build_and_wire_screens()
        self._navigate_to_entry()
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

        # Tell the widget layer which sound to play on button press.
        widgets.set_button_sound(beeper.click)

    # ── Screen construction & wiring ──────────────────────────────────────────

    def _build_and_wire_screens(self) -> None:
        """Build both screens once, then wire all static callbacks."""
        self._entry_screen   = _build_entry_screen()
        self._measure_screen = _build_measure_screen()
        self._mgr = ScreenManager(self._disp)
        self._wire_navigation()

    def _wire_navigation(self) -> None:
        """Wire the NEXT buttons to switch between screens."""
        next_e = self._entry_screen.find("next_btn")
        if isinstance(next_e, Button):
            next_e.on_press = self._navigate_to_measure

        next_m = self._measure_screen.find("next_btn")
        if isinstance(next_m, Button):
            next_m.on_press = self._navigate_to_entry

    # ── Navigation ────────────────────────────────────────────────────────────

    def _navigate_to_entry(self) -> None:
        """Switch to the entry screen and populate fields from the record."""
        self._mgr.switch_to(self._entry_screen)
        for key in _FIELD_KEYS:
            field = self._entry_screen.find(f"{key}_field")
            if isinstance(field, TextBox):
                value = self.record.get(key, "")
                if value:
                    field.set_value(value)
                    field.draw(self._disp)

    def _navigate_to_measure(self) -> None:
        """Save edited field values to the record, then switch to measure screen."""
        for key in _FIELD_KEYS:
            field = self._entry_screen.find(f"{key}_field")
            if isinstance(field, TextBox):
                self.record[key] = field.value
        log.info("AppController: record = %s", self.record)
        self._mgr.switch_to(self._measure_screen)

    # ── Event loop ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        log.info("AppController: running — Ctrl-C to exit")
        last_tick = 0.0

        while True:
            now = time.monotonic()

            # Clock: update the active screen's clock widget once per second.
            if now - last_tick >= 1.0:
                last_tick = now
                clock = self._mgr.active_screen.find("clock")
                if isinstance(clock, TextBox):
                    clock.set_value(datetime.datetime.now().strftime("%H:%M:%S"))
                    clock.draw(self._disp)

            # Keyboard input.
            key = self._kb.read_one()
            if key:
                self._mgr.on_key(key)

            # Touch input.
            if self._touch.is_touched:
                pos = self._touch.position()
                if pos:
                    self._mgr.on_touch(*pos)
                while self._touch.is_touched:
                    time.sleep(_POLL_S)

            time.sleep(_POLL_S)
