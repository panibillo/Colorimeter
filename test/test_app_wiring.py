#!/usr/bin/env python3
"""
Wiring tests for AppController's widget lookups (src/app_controller.py).

Screen.find() returns None for an unknown name rather than raising, so a
lookup that stops matching fails silently: the clock simply stops ticking and
the entry form silently stops populating. That is exactly how the previous
semantic-name lookups ("clock", "id_field") went unnoticed — widgets are named
by their numeric id from widget_defs.csv, so none of those names ever matched.

These tests bind the constants in app_controller to the CSV definitions, so a
renumbered widget or an edited screen_widgets.csv fails here instead of going
quiet at runtime. They also guard screen loading itself, which raises when a
screen references an undefined widget id.

Run from the project root:
    python test/test_app_wiring.py
or with unittest discovery:
    python -m unittest test.test_app_wiring
"""

import os
import sys
import unittest
from pathlib import Path

# Import the mock hardware stack, and keep matplotlib headless.
os.environ.setdefault("COLORIMETER_SIMULATION", "1")
os.environ.setdefault("MPLBACKEND", "Agg")

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import app_controller
from csv_to_widgets import load_all_screens
from widgets import Button, Label, TextBox


def _load():
    src = _ROOT / "src"
    return load_all_screens(
        widget_defs_path=str(src / "widget_defs.csv"),
        screen_defs_path=str(src / "screen_defs.csv"),
        screen_widgets_path=str(src / "screen_widgets.csv"),
    )


class TestScreensLoad(unittest.TestCase):

    def test_screens_load_from_csv(self):
        """Guards typos like a screen referencing an undefined widget id."""
        screens = _load()
        self.assertIn("SE", screens)
        self.assertIn("CA", screens)


class TestWidgetLookups(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.screens = _load()

    def test_entry_form_fields_resolve_to_textboxes(self):
        se = self.screens["SE"]
        for key in app_controller._FIELD_KEYS:
            widget_id = app_controller._SE_FIELD_WIDGETS[key]
            widget = se.find(widget_id)
            self.assertIsNotNone(widget, f"SE field {key!r} (id {widget_id}) not found")
            self.assertIsInstance(widget, TextBox, f"SE field {key!r} is not a TextBox")

    def test_every_field_key_has_a_widget(self):
        self.assertEqual(sorted(app_controller._SE_FIELD_WIDGETS),
                         sorted(app_controller._FIELD_KEYS))

    def test_clock_resolves_on_every_screen(self):
        """The clock lives in the shared status bar, so every screen has one."""
        for screen_id, screen in self.screens.items():
            widget = screen.find(app_controller._CLOCK_WIDGET)
            self.assertIsNotNone(widget, f"no clock on screen {screen_id!r}")
            self.assertIsInstance(widget, TextBox)

    def test_capture_widgets_resolve_with_expected_types(self):
        ca = self.screens["CA"]
        for widget_id, expected in (
            (app_controller._CA_START_BTN,     Button),
            (app_controller._COUNTDOWN_WIDGET, TextBox),
            (app_controller._CA_STATUS_1,      Label),
            (app_controller._CA_STATUS_2,      Label),
        ):
            widget = ca.find(widget_id)
            self.assertIsNotNone(widget, f"CA widget {widget_id} not found")
            self.assertIsInstance(widget, expected)

    def test_timed_screens_start_their_timer(self):
        """
        A screen with a duration must ask for its timer, or it renders a
        countdown that never moves.
        """
        for screen_id, screen in self.screens.items():
            if screen.duration > 0:
                self.assertIn("start_timer", screen.beginactions,
                              f"screen {screen_id!r} has duration "
                              f"{screen.duration}s but no start_timer beginaction")

    def test_start_button_resolves_on_entry_screen(self):
        """The id _wire_callbacks uses to find the Start button."""
        self.assertIsInstance(self.screens["SE"].find("103"), Button)


if __name__ == "__main__":
    unittest.main(verbosity=2)
