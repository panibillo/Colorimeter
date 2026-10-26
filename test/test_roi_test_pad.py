#!/usr/bin/env python3
"""
Unit tests for the exposure-calibration test pad ROIs (src/roi.py).

The pad's white, yellow and brown patches are stored with active=2, picked out
by role.  Runs against a throwaway copy of the real colorimeter.sqlite so the
live DB is never touched.

Run from the project root:
    python test/run_tests.py test_roi_test_pad
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import db_manager as db
import roi
from rect import Rect


class TestTestPad(unittest.TestCase):

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        tmp = self._tmpdir / "test.sqlite"
        shutil.copy(_ROOT / "src" / "colorimeter.sqlite", tmp)
        self._saved_dbname = db.DBNAME
        db.DBNAME = str(tmp)
        db.write_one("DELETE FROM roi WHERE active = 2")

    def tearDown(self):
        db.DBNAME = self._saved_dbname
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _add(self, name, role, active=2):
        roi._insert(name, role, Rect(100, 100, 40, 40), 2304, 1296, "imx708",
                    None, "test", active=active)

    def test_no_pad_means_all_roles_missing(self):
        pad = roi.load_test_pad()
        self.assertEqual(pad, {})
        self.assertEqual(roi.missing_test_pad_roles(pad), roi.TEST_PAD_ROLES)

    def test_loads_the_three_patches_by_role(self):
        self._add("padw", roi.ROLE_PAD_WHITE)
        self._add("pady", roi.ROLE_PAD_YELLOW)
        self._add("padb", roi.ROLE_PAD_BROWN)
        pad = roi.load_test_pad()
        self.assertEqual(sorted(pad), sorted(roi.TEST_PAD_ROLES))
        self.assertEqual(roi.missing_test_pad_roles(pad), ())

    def test_reports_which_patch_is_missing(self):
        self._add("padw", roi.ROLE_PAD_WHITE)
        self._add("padb", roi.ROLE_PAD_BROWN)
        pad = roi.load_test_pad()
        self.assertEqual(roi.missing_test_pad_roles(pad),
                         (roi.ROLE_PAD_YELLOW,))

    def test_other_calibration_patches_are_ignored(self):
        self._add("corner1", "white")
        self._add("padw", roi.ROLE_PAD_WHITE)
        self.assertEqual(list(roi.load_test_pad()), [roi.ROLE_PAD_WHITE])

    def test_pad_patches_are_not_in_force_during_a_run(self):
        self._add("padw", roi.ROLE_PAD_WHITE)
        roles = {r.role for r in roi.load_active().values()}
        self.assertNotIn(roi.ROLE_PAD_WHITE, roles)


class TestInitFromSample(TestTestPad):

    def sample(self):
        return roi.by_role(roi.load_active(), roi.ROLE_SAMPLE)

    def test_splits_the_sample_into_yellow_brown_white_top_to_bottom(self):
        created = roi.init_test_pad_from_sample()
        self.assertEqual(created, [roi.ROLE_PAD_YELLOW, roi.ROLE_PAD_BROWN,
                                   roi.ROLE_PAD_WHITE])
        pad = roi.load_test_pad()
        s = self.sample()
        y, b, w = (pad[r] for r in (roi.ROLE_PAD_YELLOW, roi.ROLE_PAD_BROWN,
                                    roi.ROLE_PAD_WHITE))
        self.assertLess(y.y, b.y)
        self.assertLess(b.y, w.y)
        for patch in (y, b, w):
            self.assertEqual((patch.x, patch.w), (s.x, s.w))
            self.assertTrue(roi.is_bayer_safe(patch))
            self.assertGreaterEqual(patch.y, s.y)
            self.assertLessEqual(patch.y2(), s.y2())
        self.assertEqual(y.h, b.h)
        self.assertEqual(b.h, w.h)
        self.assertEqual(b.y - y.y, y.h)               # no gaps, no overlap

    def test_a_second_call_creates_nothing(self):
        roi.init_test_pad_from_sample()
        self.assertEqual(roi.init_test_pad_from_sample(), [])

    def test_only_missing_patches_are_created_and_edits_survive(self):
        self._add("pad_brown", roi.ROLE_PAD_BROWN)
        created = roi.init_test_pad_from_sample()
        self.assertEqual(created, [roi.ROLE_PAD_YELLOW, roi.ROLE_PAD_WHITE])
        self.assertEqual(roi.load_test_pad()[roi.ROLE_PAD_BROWN].xywh(),
                         (100, 100, 40, 40))

    def test_the_sample_and_the_active_set_are_untouched(self):
        before = roi.load_active()
        roi.init_test_pad_from_sample()
        self.assertEqual({n: r.xywh() for n, r in roi.load_active().items()},
                         {n: r.xywh() for n, r in before.items()})

    def test_saving_a_pad_patch_supersedes_it_in_the_pad_set(self):
        roi.init_test_pad_from_sample()
        rowid = roi.save(roi.ROLE_PAD_BROWN, Rect(1000, 700, 40, 40),
                         active=2)
        self.assertGreater(rowid, 0)
        self.assertEqual(roi.load_test_pad()[roi.ROLE_PAD_BROWN].xywh(),
                         (1000, 700, 40, 40))
        self.assertEqual(db.read_val(
            "SELECT COUNT(*) FROM roi WHERE name = 'pad_brown'"), 2)
        self.assertEqual(db.read_val(
            "SELECT COUNT(*) FROM roi WHERE name = 'pad_brown' AND active = 2"), 1)


if __name__ == "__main__":
    unittest.main()
