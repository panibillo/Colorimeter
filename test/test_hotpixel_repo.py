#!/usr/bin/env python3
"""
Unit tests for hotpixel_repo (src/hotpixel_repo.py).

The `hotpixels` table round-trip, run against a throwaway copy of the real
colorimeter.sqlite so the live DB is never touched -- same technique as
test_exposure_repo.py and test_layermodel_repo.py.

Run from the project root:
    python test/run_tests.py test_hotpixel_repo
or with unittest discovery:
    python -m unittest test.test_hotpixel_repo
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import db_manager as db
import hotpixel_repo as hr


class TestHotpixelTable(unittest.TestCase):
    """The `hotpixels` table round-trip, on a throwaway copy of the real DB."""

    def setUp(self):
        live = _ROOT / "src" / "colorimeter.sqlite"
        self._tmpdir = Path(tempfile.mkdtemp())
        tmp = self._tmpdir / "test.sqlite"
        shutil.copy(live, tmp)
        self._saved_dbname = db.DBNAME
        db.DBNAME = str(tmp)
        db.write_one("DELETE FROM hotpixels")

    def tearDown(self):
        db.DBNAME = self._saved_dbname
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_empty_context_returns_empty_list(self):
        self.assertEqual(
            hr.read_active("imx708", 2304, 1296, hardware_sn="XCN-001"), [])

    def test_write_then_read_round_trips_every_field(self):
        a = hr.HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                        bayer_layer="B", x=112, y=340, hardware_sn="XCN-001",
                        bayer_pattern="RGGB", score=9.4, exposure_us=50973,
                        n_frames=100, remark="note")
        self.assertEqual(hr.write_hotpixels([a], analysisref="selftest"), 1)
        got = hr.read_active("imx708", 2304, 1296, hardware_sn="XCN-001")
        self.assertEqual(len(got), 1)
        row = got[0]
        self.assertEqual(row.x, 112)
        self.assertEqual(row.y, 340)
        self.assertEqual(row.bayer_layer, "B")
        self.assertEqual(row.bayer_pattern, "RGGB")
        self.assertAlmostEqual(row.score, 9.4)
        self.assertEqual(row.exposure_us, 50973)
        self.assertEqual(row.n_frames, 100)
        self.assertEqual(row.analysisref, "selftest")
        self.assertEqual(row.remark, "note")

    def test_analysisref_on_row_wins_over_argument(self):
        a = hr.HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                        bayer_layer="B", x=1, y=1, analysisref="own-tag")
        hr.write_hotpixels([a], analysisref="batch-tag")
        got = hr.read_active("imx708", 2304, 1296)[0]
        self.assertEqual(got.analysisref, "own-tag")

    def test_bayer_layer_filter(self):
        rows = [
            hr.HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                       bayer_layer="B", x=1, y=1, hardware_sn="XCN-001"),
            hr.HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                       bayer_layer="R", x=5, y=5, hardware_sn="XCN-001"),
        ]
        hr.write_hotpixels(rows)
        self.assertEqual(
            len(hr.read_active("imx708", 2304, 1296, hardware_sn="XCN-001")), 2)
        b_only = hr.read_active("imx708", 2304, 1296, hardware_sn="XCN-001",
                                bayer_layer="B")
        self.assertEqual([(r.bayer_layer, r.x, r.y) for r in b_only],
                         [("B", 1, 1)])

    def test_distinct_hardware_sn_is_a_distinct_context(self):
        a = hr.HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                        bayer_layer="B", x=1, y=1, hardware_sn="UNIT-A")
        hr.write_hotpixels([a])
        self.assertEqual(
            hr.read_active("imx708", 2304, 1296, hardware_sn="UNIT-B"), [])
        self.assertEqual(
            len(hr.read_active("imx708", 2304, 1296, hardware_sn="UNIT-A")), 1)

    def test_distinct_resolution_is_a_distinct_context(self):
        a = hr.HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                        bayer_layer="B", x=1, y=1, hardware_sn="XCN-001")
        hr.write_hotpixels([a])
        self.assertEqual(
            hr.read_active("imx708", 4608, 2592, hardware_sn="XCN-001"), [])

    def test_no_serial_context_is_distinct_from_a_named_serial(self):
        a = hr.HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                        bayer_layer="B", x=1, y=1, hardware_sn="XCN-001")
        hr.write_hotpixels([a])
        self.assertEqual(hr.read_active("imx708", 2304, 1296), [])

    def test_rewrite_supersedes_only_the_matching_layer(self):
        b1 = hr.HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                         bayer_layer="B", x=1, y=1, hardware_sn="XCN-001")
        r1 = hr.HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                         bayer_layer="R", x=5, y=5, hardware_sn="XCN-001")
        hr.write_hotpixels([b1, r1])

        b2 = hr.HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                         bayer_layer="B", x=9, y=9, hardware_sn="XCN-001")
        hr.write_hotpixels([b2], analysisref="rerun")

        active = hr.read_active("imx708", 2304, 1296, hardware_sn="XCN-001")
        self.assertEqual(
            sorted((row.bayer_layer, row.x, row.y) for row in active),
            [("B", 9, 9), ("R", 5, 5)])

    def test_history_kept_not_deleted_on_rewrite(self):
        a = hr.HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                        bayer_layer="B", x=1, y=1, hardware_sn="XCN-001")
        hr.write_hotpixels([a])
        b = hr.HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                        bayer_layer="B", x=2, y=2, hardware_sn="XCN-001")
        hr.write_hotpixels([b])
        total = db.read_val(
            "SELECT COUNT(*) FROM hotpixels WHERE hardware_sn = 'XCN-001'")
        self.assertEqual(total, 2)
        active = db.read_val(
            "SELECT COUNT(*) FROM hotpixels WHERE hardware_sn = 'XCN-001' "
            "AND active = 1")
        self.assertEqual(active, 1)

    def test_write_empty_list_is_a_no_op(self):
        self.assertEqual(hr.write_hotpixels([]), 0)

    def test_delete_context_hard_deletes(self):
        a = hr.HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                        bayer_layer="B", x=1, y=1, hardware_sn="XCN-001")
        hr.write_hotpixels([a])
        removed = hr.delete_context("imx708", 2304, 1296,
                                    hardware_sn="XCN-001")
        self.assertEqual(removed, 1)
        self.assertEqual(
            hr.read_active("imx708", 2304, 1296, hardware_sn="XCN-001"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
