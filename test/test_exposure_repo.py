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

import db_manager as db
import exposure_repo as er


class TestExposureLadder(unittest.TestCase):
    """The pure ladder math — no database involved."""

    def test_n1_is_just_estar(self):
        self.assertEqual(er.exposure_ladder(40_000, 1), [40_000])

    def test_odd_n_is_centered_on_estar(self):
        self.assertEqual(er.exposure_ladder(40_000, 3)[1], 40_000)
        self.assertEqual(er.exposure_ladder(40_000, 5)[2], 40_000)

    def test_length_equals_n(self):
        for n in range(1, 6):
            self.assertEqual(len(er.exposure_ladder(40_000, n)), n)

    def test_geometric_spacing(self):
        self.assertEqual(er.exposure_ladder(30_000, 3, 1.5),
                         [20_000, 30_000, 45_000])

    def test_returned_ascending(self):
        ladder = er.exposure_ladder(40_000, 5)
        self.assertEqual(ladder, sorted(ladder))

    def test_even_n_straddles_estar(self):
        # N=2 with r=1.5: e*·1.5^∓0.5 = e*/1.2247, e*·1.2247
        low, high = er.exposure_ladder(40_000, 2, 1.5)
        self.assertLess(low, 40_000)
        self.assertGreater(high, 40_000)

    def test_floor_at_min_exposure(self):
        self.assertEqual(er.exposure_ladder(1_000, 3, 2.0)[0], er.MIN_EXPOSURE_US)

    def test_rejects_bad_args(self):
        with self.assertRaises(ValueError):
            er.exposure_ladder(40_000, 0)
        with self.assertRaises(ValueError):
            er.exposure_ladder(40_000, 3, ratio=0)

    def test_groups_from_estar_keys_and_lengths(self):
        g = er.groups_from_estar(40_000)
        self.assertEqual(sorted(g), ["1", "2", "3", "4", "5"])
        self.assertEqual([len(g[str(n)]) for n in range(1, 6)], [1, 2, 3, 4, 5])


class TestExposureTable(unittest.TestCase):
    """The `exposure` table round-trip, on a throwaway copy of the real DB."""

    def setUp(self):
        live = _ROOT / "src" / "colorimeter.sqlite"
        self._tmpdir = Path(tempfile.mkdtemp())
        tmp = self._tmpdir / "test.sqlite"
        shutil.copy(live, tmp)
        self._saved_dbname = db.DBNAME
        db.DBNAME = str(tmp)
        db.write_one("DELETE FROM exposure")

    def tearDown(self):
        db.DBNAME = self._saved_dbname
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_write_all_groups_round_trip(self):
        n = er.write_all_groups("white", er.groups_from_estar(40_000))
        self.assertEqual(n, 1 + 2 + 3 + 4 + 5)
        self.assertEqual(er.read_exposures("white", "1"), [40_000])
        self.assertEqual(er.read_exposures("white", "3"),
                         [26_667, 40_000, 60_000])

    def test_read_missing_returns_empty(self):
        self.assertEqual(er.read_exposures("white", "1"), [])
        self.assertEqual(er.read_group("1"), {})
        self.assertEqual(er.available_groups(), [])

    def test_read_group_maps_leds(self):
        er.write_all_groups("white", er.groups_from_estar(40_000))
        er.write_all_groups("red", er.groups_from_estar(20_000))
        group2 = er.read_group("2")
        self.assertEqual(sorted(group2), ["red", "white"])
        self.assertEqual(len(group2["white"]), 2)

    def test_available_groups_sorted_numerically(self):
        er.write_all_groups("white", er.groups_from_estar(40_000))
        self.assertEqual(er.available_groups(), ["1", "2", "3", "4", "5"])

    def test_write_led_exposures_replaces_one_group_only(self):
        er.write_all_groups("white", er.groups_from_estar(40_000))
        er.write_led_exposures("white", "1", [12_345])
        self.assertEqual(er.read_exposures("white", "1"), [12_345])
        # Other groups are untouched by a single-group write.
        self.assertEqual(er.read_exposures("white", "3"),
                         [26_667, 40_000, 60_000])

    def test_rowids_stay_unique_across_writes(self):
        er.write_all_groups("white", er.groups_from_estar(40_000))
        er.write_all_groups("red", er.groups_from_estar(20_000))
        er.write_led_exposures("white", "1", [1_111])
        ids = [r[0] for r in db.read_all("SELECT rowid FROM exposure")]
        self.assertEqual(len(ids), len(set(ids)))

    def test_write_all_groups_supersedes_previous(self):
        er.write_all_groups("white", er.groups_from_estar(40_000))
        er.write_all_groups("white", er.groups_from_estar(10_000))
        # Reads see only the new (active) calibration.
        self.assertEqual(er.read_exposures("white", "1"), [10_000])
        active = db.read_val(
            "SELECT COUNT(*) FROM exposure WHERE LED='white' AND active=1")
        self.assertEqual(active, 1 + 2 + 3 + 4 + 5)

    def test_recalibration_keeps_history(self):
        er.write_all_groups("white", er.groups_from_estar(40_000))
        er.write_all_groups("white", er.groups_from_estar(10_000))
        # The prior calibration is deactivated, not deleted.
        total = db.read_val("SELECT COUNT(*) FROM exposure WHERE LED='white'")
        self.assertEqual(total, 2 * (1 + 2 + 3 + 4 + 5))
        inactive = db.read_val(
            "SELECT COUNT(*) FROM exposure WHERE LED='white' AND active=0")
        self.assertEqual(inactive, 1 + 2 + 3 + 4 + 5)

    def test_inactive_rows_excluded_from_reads(self):
        er.write_all_groups("white", er.groups_from_estar(40_000))
        db.write_one("UPDATE exposure SET active=0 WHERE LED='white'")
        self.assertEqual(er.read_exposures("white", "1"), [])
        self.assertEqual(er.read_group("1"), {})
        self.assertEqual(er.available_groups(), [])

    def test_add_dt_stamped_on_active_rows(self):
        er.write_all_groups("white", er.groups_from_estar(40_000),
                            add_dt="2026-07-26 09:30:00")
        stamps = db.read_all(
            "SELECT DISTINCT add_dt FROM exposure WHERE LED='white' AND active=1")
        self.assertEqual(stamps, [("2026-07-26 09:30:00",)])

    def test_write_supersedes_only_named_led(self):
        er.write_all_groups("white", er.groups_from_estar(40_000))
        er.write_all_groups("red", er.groups_from_estar(20_000))
        # Recalibrating white must not disturb red's active rows.
        er.write_all_groups("white", er.groups_from_estar(10_000))
        self.assertEqual(er.read_exposures("red", "1"), [20_000])

    def test_write_estar_persists_all_groups(self):
        n = er.write_estar("amber", 30_000)
        self.assertEqual(n, 1 + 2 + 3 + 4 + 5)
        self.assertEqual(er.read_exposures("amber", "1"), [30_000])
        self.assertEqual(er.read_exposures("amber", "3"),
                         [20_000, 30_000, 45_000])

    def test_delete_led(self):
        er.write_all_groups("white", er.groups_from_estar(40_000))
        er.delete_led("white")
        self.assertEqual(er.read_exposures("white", "1"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
