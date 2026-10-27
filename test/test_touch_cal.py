#!/usr/bin/env python3
"""
Tests for touch calibration: the corner solve and tap averaging (tsc2007.py)
and the safe file replacement (touch_cal_store.py).

The store tests matter most: the calibration file belongs to one panel and
cannot be rebuilt without re-tapping, so no failure may lose the old values.

Run from the project root:
    python test/run_tests.py test_touch_cal
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import touch_cal_store as store
import tsc2007

OLD = {"a": 0.002, "b": -0.088, "c": 321.0, "d": 0.067, "e": 0.0015,
       "f": -18.4, "threshold": 3}
NEW = {"a": 0.0021, "b": -0.087, "c": 320.0, "d": 0.066, "e": 0.0014, "f": -18.0}


def raw_for(cal, px, py):
    """Invert the affine transform: the raw reading that maps to (px, py)."""
    a, b, c, d, e, f = (cal[k] for k in store.AFFINE_KEYS)
    det = a * e - b * d
    x, y = px - c, py - f
    return ((e * x - b * y) / det, (a * y - d * x) / det)


class TestSolve(unittest.TestCase):

    def taps(self, cal, nudge_br=(0, 0)):
        pts = {lbl: raw_for(cal, x, y)
               for lbl, x, y in tsc2007.corner_targets(320, 240)}
        bx, by = tsc2007.corner_targets(320, 240)[2][1:]
        pts["BR"] = raw_for(cal, bx + nudge_br[0], by + nudge_br[1])
        return pts

    def test_recovers_the_transform(self):
        cal, ex, ey = tsc2007.solve_affine(self.taps(NEW), 320, 240)
        for k in store.AFFINE_KEYS:
            self.assertAlmostEqual(cal[k], NEW[k], places=6)
        self.assertAlmostEqual(ex, 0, places=6)
        self.assertAlmostEqual(ey, 0, places=6)

    def test_br_error_is_the_miss(self):
        _, ex, ey = tsc2007.solve_affine(self.taps(NEW, (7, -4)), 320, 240)
        self.assertAlmostEqual(ex, 7, places=6)
        self.assertAlmostEqual(ey, -4, places=6)

    def test_collinear_taps_are_refused(self):
        pts = {"TL": (100, 100), "TR": (200, 200), "BL": (300, 300), "BR": (9, 9)}
        with self.assertRaises(ValueError):
            tsc2007.solve_affine(pts, 320, 240)

    def test_average_drops_empty_readings(self):
        self.assertEqual(tsc2007.average_raw([(0, 0, 0), (0, 0, 0)]), None)
        self.assertEqual(tsc2007.average_raw([(900, 2500, 40), (0, 0, 0)]),
                         (900, 2500))


class StoreTestCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "touch_cal.json"

    def tearDown(self):
        self._tmp.cleanup()

    def write_old(self):
        self.path.write_text(json.dumps(OLD))
        return self.path.read_bytes()

    def names(self):
        return sorted(p.name for p in self.dir.iterdir())


class TestLoad(StoreTestCase):

    def test_reads_the_six_and_ignores_threshold(self):
        self.write_old()
        got = store.load(self.path)
        self.assertEqual(set(got), set(store.AFFINE_KEYS))
        self.assertEqual(got["c"], 321.0)

    def test_unusable_files(self):
        self.assertIsNone(store.load(self.path))                 # missing
        self.path.write_text("{not json")
        self.assertIsNone(store.load(self.path))
        self.path.write_text(json.dumps({"a": 1}))
        self.assertIsNone(store.load(self.path))
        self.path.write_text(json.dumps(dict(NEW, a=float("nan"))))
        self.assertIsNone(store.load(self.path))


class TestSave(StoreTestCase):

    def test_copies_the_old_file_then_replaces(self):
        old_bytes = self.write_old()
        bak = store.save(NEW, self.path)
        self.assertEqual(bak, self.dir / "touch_cal.bak1.json")
        self.assertEqual(bak.read_bytes(), old_bytes)
        self.assertEqual(store.load(self.path), NEW)
        self.assertEqual(self.names(), ["touch_cal.bak1.json", "touch_cal.json"])

    def test_backups_are_never_overwritten(self):
        self.write_old()
        store.save(NEW, self.path)
        bak2 = store.save(dict(NEW, c=300.0), self.path)
        self.assertEqual(bak2.name, "touch_cal.bak2.json")
        self.assertEqual(store.load(self.dir / "touch_cal.bak1.json")["c"], 321.0)
        self.assertEqual(store.load(bak2)["c"], 320.0)

    def test_no_old_file_means_no_backup(self):
        self.assertIsNone(store.save(NEW, self.path))
        self.assertEqual(self.names(), ["touch_cal.json"])

    def test_a_failed_write_leaves_the_old_file(self):
        old_bytes = self.write_old()
        with mock.patch.object(store.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(store.CalWriteError, "disk full"):
                store.save(NEW, self.path)
        self.assertEqual(self.path.read_bytes(), old_bytes)
        self.assertNotIn("touch_cal.json.tmp", self.names())

    def test_a_failed_backup_writes_nothing(self):
        old_bytes = self.write_old()
        with mock.patch.object(store.shutil, "copy2", side_effect=OSError("read-only")):
            with self.assertRaises(store.CalWriteError):
                store.save(NEW, self.path)
        self.assertEqual(self.path.read_bytes(), old_bytes)
        self.assertEqual(self.names(), ["touch_cal.json"])

    def test_bad_values_are_refused_before_any_file_work(self):
        old_bytes = self.write_old()
        with self.assertRaises(ValueError):
            store.save(dict(NEW, e=float("inf")), self.path)
        with self.assertRaises(KeyError):
            store.save({"a": 1.0}, self.path)
        self.assertEqual(self.path.read_bytes(), old_bytes)
        self.assertEqual(self.names(), ["touch_cal.json"])


class TestDriver(StoreTestCase):
    """The driver's in-memory calibration, without hardware."""

    def test_apply_is_memory_only_and_save_backs_up(self):
        old_bytes = self.write_old()
        t = tsc2007.TSC2007(cal_path=str(self.path))
        t._load_cal()
        self.assertTrue(t.calibrated)
        before = t.cal
        t.apply_cal(NEW)
        self.assertEqual(t.cal, NEW)
        self.assertEqual(self.path.read_bytes(), old_bytes)      # file untouched
        t.apply_cal(before)                                      # revert
        self.assertEqual(t.cal["c"], 321.0)
        t.apply_cal(NEW)
        bak = t.save_cal()
        self.assertEqual(bak.read_bytes(), old_bytes)
        self.assertEqual(store.load(self.path), NEW)

    def test_cal_is_a_copy(self):
        t = tsc2007.TSC2007(cal_path=str(self.path))
        t.cal["a"] = 99.0
        self.assertNotEqual(t.cal["a"], 99.0)


class TestMockDriver(unittest.TestCase):

    def test_mock_never_points_at_the_project_file(self):
        from mock.tsc2007_mock import TSC2007
        path = TSC2007().cal_path
        self.assertTrue(path.startswith(tempfile.gettempdir()))
        self.assertNotIn(os.path.join("config", "touch_cal.json"),
                         path.replace("colorimeter_mock", ""))


if __name__ == "__main__":
    unittest.main()
