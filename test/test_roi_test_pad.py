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


if __name__ == "__main__":
    unittest.main()
