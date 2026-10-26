#!/usr/bin/env python3
"""
Unit tests for exposure_repo (src/exposure_repo.py).

Two surfaces are covered:
  * the pure exposure-ladder math (no DB), and
  * the `exposure` table round-trip, run against a throwaway copy of the real
    colorimeter.sqlite so the live DB is never touched.

Run from the project root:
    python test/test_exposure_repo.py
or with unittest discovery:
    python -m unittest test.test_exposure_repo
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import db_bootstrap
import db_manager as db
import exposure_repo as er


def ladder(e_star, ratio=1.5):
    """Groups 1..5 as a geometric ladder around e_star: just test data."""
    return {str(n): sorted(round(e_star * ratio ** (i - (n - 1) / 2))
                           for i in range(n)) for n in range(1, 6)}


class TestExposureTable(unittest.TestCase):
    """The `exposure` table round-trip, on a throwaway copy of the real DB."""

    def setUp(self):
        live = _ROOT / "src" / "colorimeter.sqlite"
        self._tmpdir = Path(tempfile.mkdtemp())
        tmp = self._tmpdir / "test.sqlite"
        shutil.copy(live, tmp)
        self._saved_dbname = db.DBNAME
        db.DBNAME = str(tmp)
        db_bootstrap.ensure(tmp)        # a live copy may predate new columns
        db.write_one("DELETE FROM exposure")

    def tearDown(self):
        db.DBNAME = self._saved_dbname
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_write_all_groups_round_trip(self):
        n = er.write_all_groups("white", ladder(40_000))
        self.assertEqual(n, 1 + 2 + 3 + 4 + 5)
        self.assertEqual(er.read_exposures("white", "1"), [40_000])
        self.assertEqual(er.read_exposures("white", "3"),
                         [26_667, 40_000, 60_000])

    def test_notes_layers_and_preimage_flag_round_trip(self):
        er.write_all_groups(
            "green", {"1": [20_000], "2": [10_000, 40_000]}, remark="r",
            notes={10_000: ("low DN (pad_brown.Gb)", 101.5, None),
                   40_000: ("high DN (pad_white.Gb)", None, 840.0)},
            layers="Gb Gr", preimage_group="1")
        rows = db.read_all(
            "SELECT exposuregroup, microseconds, remark, low_dn, high_dn, "
            "layers, preimage FROM exposure WHERE LED = 'green' "
            "ORDER BY exposuregroup, microseconds")
        self.assertEqual(rows, [
            ("1", 20_000, "r", None, None, "Gb Gr", 1),
            ("2", 10_000, "low DN (pad_brown.Gb)", 101.5, None, "Gb Gr", 0),
            ("2", 40_000, "high DN (pad_white.Gb)", None, 840.0, "Gb Gr", 0)])
        self.assertEqual(er.read_preimage_exposures("green"), [20_000])

    def test_read_led_exposures_is_distinct_active_and_ascending(self):
        er.write_all_groups("green", {"1": [20_000], "2": [10_000, 40_000],
                                      "3": [10_000, 20_000, 40_000]})
        self.assertEqual(er.read_led_exposures("green"), [10_000, 20_000, 40_000])
        er.write_all_groups("green", {"1": [5_000]})
        self.assertEqual(er.read_led_exposures("green"), [5_000])
        self.assertEqual(er.read_led_exposures("violet"), [])

    def test_no_preimage_flag_means_none_for_the_preimage(self):
        er.write_all_groups("green", {"1": [20_000]})
        self.assertEqual(er.read_preimage_exposures("green"), [])

    def test_read_missing_returns_empty(self):
        self.assertEqual(er.read_exposures("white", "1"), [])
        self.assertEqual(er.read_group("1"), {})
        self.assertEqual(er.available_groups(), [])

    def test_write_all_groups_stores_explicit_lists(self):
        # Tested brackets need not follow any ratio; they are stored as given.
        er.write_all_groups("red", {"1": [4_100], "3": [2_500, 4_100, 7_300]})
        self.assertEqual(er.read_exposures("red", "3"), [2_500, 4_100, 7_300])

    def test_middle_exposure(self):
        er.write_all_groups("red", {"1": [4_100], "3": [2_500, 4_100, 7_300],
                                    "4": [2_000, 3_000, 5_000, 8_000]})
        self.assertEqual(er.middle_exposure("red", "1"), 4_100)
        self.assertEqual(er.middle_exposure("red", "3"), 4_100)
        # Even count: the lower middle rung, always one that was tested.
        self.assertEqual(er.middle_exposure("red", "4"), 3_000)

    def test_middle_exposure_missing_is_none(self):
        self.assertIsNone(er.middle_exposure("red", "3"))

    def test_read_group_maps_leds(self):
        er.write_all_groups("white", ladder(40_000))
        er.write_all_groups("red", ladder(20_000))
        group2 = er.read_group("2")
        self.assertEqual(sorted(group2), ["red", "white"])
        self.assertEqual(len(group2["white"]), 2)

    def test_available_groups_sorted_numerically(self):
        er.write_all_groups("white", ladder(40_000))
        self.assertEqual(er.available_groups(), ["1", "2", "3", "4", "5"])

    def test_write_led_exposures_replaces_one_group_only(self):
        er.write_all_groups("white", ladder(40_000))
        er.write_led_exposures("white", "1", [12_345])
        self.assertEqual(er.read_exposures("white", "1"), [12_345])
        # Other groups are untouched by a single-group write.
        self.assertEqual(er.read_exposures("white", "3"),
                         [26_667, 40_000, 60_000])

    def test_rowids_stay_unique_across_writes(self):
        er.write_all_groups("white", ladder(40_000))
        er.write_all_groups("red", ladder(20_000))
        er.write_led_exposures("white", "1", [1_111])
        ids = [r[0] for r in db.read_all("SELECT rowid FROM exposure")]
        self.assertEqual(len(ids), len(set(ids)))

    def test_write_all_groups_supersedes_previous(self):
        er.write_all_groups("white", ladder(40_000))
        er.write_all_groups("white", ladder(10_000))
        # Reads see only the new (active) calibration.
        self.assertEqual(er.read_exposures("white", "1"), [10_000])
        active = db.read_val(
            "SELECT COUNT(*) FROM exposure WHERE LED='white' AND active=1")
        self.assertEqual(active, 1 + 2 + 3 + 4 + 5)

    def test_recalibration_keeps_history(self):
        er.write_all_groups("white", ladder(40_000))
        er.write_all_groups("white", ladder(10_000))
        # The prior calibration is deactivated, not deleted.
        total = db.read_val("SELECT COUNT(*) FROM exposure WHERE LED='white'")
        self.assertEqual(total, 2 * (1 + 2 + 3 + 4 + 5))
        inactive = db.read_val(
            "SELECT COUNT(*) FROM exposure WHERE LED='white' AND active=0")
        self.assertEqual(inactive, 1 + 2 + 3 + 4 + 5)

    def test_inactive_rows_excluded_from_reads(self):
        er.write_all_groups("white", ladder(40_000))
        db.write_one("UPDATE exposure SET active=0 WHERE LED='white'")
        self.assertEqual(er.read_exposures("white", "1"), [])
        self.assertEqual(er.read_group("1"), {})
        self.assertEqual(er.available_groups(), [])

    def test_add_dt_stamped_on_active_rows(self):
        er.write_all_groups("white", ladder(40_000),
                            add_dt="2026-07-26 09:30:00")
        stamps = db.read_all(
            "SELECT DISTINCT add_dt FROM exposure WHERE LED='white' AND active=1")
        self.assertEqual(stamps, [("2026-07-26 09:30:00",)])

    def test_write_supersedes_only_named_led(self):
        er.write_all_groups("white", ladder(40_000))
        er.write_all_groups("red", ladder(20_000))
        # Recalibrating white must not disturb red's active rows.
        er.write_all_groups("white", ladder(10_000))
        self.assertEqual(er.read_exposures("red", "1"), [20_000])

    def test_delete_led(self):
        er.write_all_groups("white", ladder(40_000))
        er.delete_led("white")
        self.assertEqual(er.read_exposures("white", "1"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
