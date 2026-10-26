#!/usr/bin/env python3
"""
Unit tests for required_cells (src/required_cells.py) and its config row.

Run from the project root:
    python test/run_tests.py test_required_cells
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import config_repo as config
import db_bootstrap
import db_manager as db
import required_cells as rc

SIX = [("violet", "B"), ("royal_blue", "B"), ("cyan", "B"),
       ("green", "Gb"), ("green", "Gr"), ("photo_red", "R")]


class TestParse(unittest.TestCase):

    def test_parses_in_order(self):
        self.assertEqual(rc.parse_cells("violet.B,green.Gb,green.Gr"),
                         [("violet", "B"), ("green", "Gb"), ("green", "Gr")])

    def test_ignores_spaces_case_and_repeats(self):
        self.assertEqual(rc.parse_cells(" Violet.b , violet.B,, green.gb "),
                         [("violet", "B"), ("green", "Gb")])

    def test_empty_is_empty(self):
        self.assertEqual(rc.parse_cells(""), [])

    def test_rejects_a_bad_layer(self):
        with self.assertRaises(ValueError):
            rc.parse_cells("red.G")

    def test_rejects_an_entry_without_a_layer(self):
        with self.assertRaises(ValueError):
            rc.parse_cells("red")
        with self.assertRaises(ValueError):
            rc.parse_cells(".R")


class TestRequiredChannels(unittest.TestCase):

    def test_a_named_led_uses_exactly_its_named_channels(self):
        self.assertEqual(rc.required_channels("green", SIX), ("Gb", "Gr"))
        self.assertEqual(rc.required_channels("violet", SIX), ("B",))

    def test_an_unnamed_led_uses_its_nearest_channel(self):
        self.assertEqual(rc.required_channels("red", SIX), ("R",))
        self.assertEqual(rc.required_channels("blue", SIX), ("B",))
        self.assertEqual(rc.required_channels("amber", SIX), ("R",))
        self.assertEqual(rc.required_channels("white", SIX), ("Gr",))

    def test_naming_a_led_overrides_its_default(self):
        self.assertEqual(rc.required_channels("red", [("red", "Gr")]), ("Gr",))

    def test_an_empty_list_leaves_every_led_on_its_default(self):
        for led, channel in rc.DEFAULT_CHANNEL.items():
            self.assertEqual(rc.required_channels(led, []), (channel,))

    def test_an_unknown_led_is_refused(self):
        with self.assertRaises(ValueError):
            rc.required_channels("magenta", SIX)

    def test_every_default_is_a_real_layer(self):
        self.assertTrue(set(rc.DEFAULT_CHANNEL.values()) <= set(rc.LAYERS))


class TestFromConfig(unittest.TestCase):

    def setUp(self):
        self._saved_dbname = db.DBNAME
        self._tmpdir = tempfile.mkdtemp()
        db.DBNAME = str(Path(self._tmpdir) / "cells_test.sqlite")
        db_bootstrap.ensure(Path(db.DBNAME))
        config.load(reload=True)

    def tearDown(self):
        db.DBNAME = self._saved_dbname
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        config.load(reload=True)

    def test_seeded_list_is_the_six_shared_cells(self):
        self.assertEqual(config.calibration_required_cells(), SIX)

    def test_required_channels_reads_the_config_by_default(self):
        self.assertEqual(rc.required_channels("green"), ("Gb", "Gr"))
        self.assertEqual(rc.required_channels("red_orange"), ("R",))

    def test_no_led_window_means_the_global_one(self):
        w = config.calibration_window_for("green")
        self.assertEqual((w.low_dn, w.high_dn, w.low_pctl, w.high_pctl),
                         (100, 850, 10.0, 90.0))
        self.assertEqual(config.calibration_led_windows(), {})

    def test_a_led_window_overrides_only_that_led(self):
        config.save("calibration", "led_windows", "green=120:800:5:95")
        self.assertEqual(config.calibration_window_for("green"),
                         rc.LedWindow(120, 800, 5.0, 95.0))
        self.assertEqual(config.calibration_window_for("violet").low_dn, 100)

    def test_an_edited_list_takes_effect(self):
        config.save("calibration", "required_cells", "red.Gr")
        self.assertEqual(rc.required_channels("red"), ("Gr",))


class TestEditing(unittest.TestCase):

    def test_with_layers_replaces_one_leds_cells_in_place(self):
        cells = rc.parse_cells("violet.B,green.Gb,green.Gr,photo_red.R")
        self.assertEqual(
            rc.format_cells(rc.with_layers(cells, "green", ["Gr", "R"])),
            "violet.B,green.R,green.Gr,photo_red.R")

    def test_with_layers_adds_an_unnamed_led_at_the_end(self):
        cells = rc.parse_cells("violet.B")
        self.assertEqual(
            rc.format_cells(rc.with_layers(cells, "red", ["R", "Gb"])),
            "violet.B,red.R,red.Gb")

    def test_no_layers_removes_the_led(self):
        cells = rc.parse_cells("violet.B,green.Gb")
        self.assertEqual(
            rc.format_cells(rc.with_layers(cells, "green", [])), "violet.B")

    def test_windows_round_trip(self):
        text = "green=120:800:5:95;violet=90:700:10:90"
        self.assertEqual(rc.format_windows(rc.parse_windows(text)), text)
        self.assertEqual(rc.parse_windows(""), {})

    def test_a_malformed_window_is_refused(self):
        for bad in ("green", "green=1:2:3", "green=a:b:c:d"):
            with self.assertRaises(ValueError):
                rc.parse_windows(bad)


if __name__ == "__main__":
    unittest.main()
