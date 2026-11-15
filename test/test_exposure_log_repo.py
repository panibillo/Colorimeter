#!/usr/bin/env python3
"""
Unit tests for exposure_log_repo (src/exposure_log_repo.py): turning a
strip-rule record into rows, and the table round trip.

The rows a real run writes are tested through the capture runner, in
test_capture_runner.TestExposureLog.

Run from the project root:
    python test/run_tests.py test_exposure_log_repo
"""

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import config_repo as config                     # noqa: E402
import db_bootstrap                              # noqa: E402
import db_manager as db                          # noqa: E402
import exposure_log_repo as L                    # noqa: E402


def stats(max_dn, n_clip=0):
    """LayerStats.to_dict() for two layers, R at max_dn, B dim."""
    one = {"n_pixels": 40, "min_dn": 150.0, "mean_dn": 400.0,
           "max_where": "pad", "n_clip_bottom": 0, "n_below_floor": 0,
           "floor_min_dn": 150.0, "n_edge_left_out": 2}
    return {"R": dict(one, layer="R", max_dn=max_dn, n_clip_top=n_clip),
            "B": dict(one, layer="B", max_dn=120.0, n_clip_top=0,
                      n_below_floor=40, floor_min_dn=float("nan"))}


def record():
    """Two tries (clipped, accept), one more Tus frame, one Bus frame."""
    return {
        "attempts": [
            {"attempt": 1, "exposure_us": 10_000, "applied_us": 9990,
             "verdict": "clipped", "captured_dt": "2026-11-14 10:00:00.100",
             "stats": stats(1023, n_clip=5)},
            {"attempt": 2, "exposure_us": 7000, "applied_us": 6990,
             "verdict": "accept", "captured_dt": "2026-11-14 10:00:00.700",
             "stats": stats(900)}],
        "roles": {"e0": "tus", "e1": "tus", "e2": "bus"},
        "frame_stats": {"e0": stats(900), "e1": stats(905),
                        "e2": stats(1023, n_clip=30)}}


FRAMES = {"e0": {"exposure_us": 7000, "applied_us": 6990,
                 "captured_dt": "2026-11-14 10:00:00.700",
                 "stored": ["R", "B"]},
          "e1": {"exposure_us": 7000, "applied_us": 6990,
                 "captured_dt": "2026-11-14 10:00:00.930",
                 "stored": ["R", "B"]},
          "e2": {"exposure_us": 30_000, "applied_us": 29_990,
                 "captured_dt": "2026-11-14 10:00:01.400",
                 "stored": ["B"]}}


class TestRows(unittest.TestCase):

    def rows(self):
        return L.rows_for_led("photo_red", record(), FRAMES, samp_no="C9",
                              archive="C9.h5", section="pre", dark_dn=64.0)

    def test_one_row_per_layer_per_frame_without_repeating_e0(self):
        rows = self.rows()
        self.assertEqual(len(rows), 2 * 4)        # 2 tries + e1 + e2
        keyed = [(r["purpose"], r["attempt"], r["label"], r["verdict"])
                 for r in rows if r["bayer_layer"] == "R"]
        self.assertEqual(keyed, [("tus", 1, None, "clipped"),
                                 ("tus", 2, "e0", "accept"),
                                 ("tus", None, "e1", None),
                                 ("bus", None, "e2", None)])

    def test_stored_says_what_is_in_the_archive(self):
        rows = self.rows()
        got = {(r["attempt"], r["label"], r["bayer_layer"]): r["stored"]
               for r in rows}
        self.assertEqual(got[(1, None, "R")], 0)  # a rejected try
        self.assertEqual(got[(2, "e0", "R")], 1)
        self.assertEqual(got[(None, "e2", "R")], 0)
        self.assertEqual(got[(None, "e2", "B")], 1)

    def test_frame_values_and_the_sweep_are_on_every_row(self):
        r = [r for r in self.rows() if r["label"] == "e2"][0]
        self.assertEqual((r["exposure_us"], r["applied_us"], r["add_dt"]),
                         (30_000, 29_990, "2026-11-14 10:00:01.400"))
        self.assertEqual((r["samp_no"], r["archive"], r["section"],
                          r["LED"], r["dark_dn"]),
                         ("C9", "C9.h5", "pre", "photo_red", 64.0))

    def test_nan_becomes_null(self):
        b = [r for r in self.rows() if r["bayer_layer"] == "B"][0]
        self.assertIsNone(b["floor_min_dn"])

    def test_an_unknown_column_is_refused(self):
        with self.assertRaises(ValueError):
            L.layer_rows(stats(900), colour="red")

    def test_no_sample_is_null(self):
        rows = L.rows_for_led("photo_red", record(), FRAMES, samp_no="",
                              archive="x.h5", section="post", dark_dn=64.0)
        self.assertIsNone(rows[0]["samp_no"])


class TestTable(unittest.TestCase):
    """The table round trip, on a throwaway bootstrapped database."""

    def setUp(self):
        self._saved = db.DBNAME
        self._dir = tempfile.mkdtemp()
        db.DBNAME = str(Path(self._dir) / "log.sqlite")
        db_bootstrap.ensure(db.DBNAME)
        config.load(reload=True)

    def tearDown(self):
        db.DBNAME = self._saved
        shutil.rmtree(self._dir, ignore_errors=True)
        config.load(reload=True)

    def test_bootstrap_creates_the_table_with_every_column(self):
        con = sqlite3.connect(db.DBNAME)
        try:
            cols = [r[1] for r in con.execute(
                "PRAGMA table_info(exposure_log)")]
        finally:
            con.close()
        self.assertEqual(cols, ["rowid", *L.COLUMNS])

    def test_written_rows_read_back(self):
        rows = L.rows_for_led("photo_red", record(), FRAMES, samp_no="C9",
                              archive="C9.h5", section="pre", dark_dn=64.0)
        self.assertEqual(L.write_rows(rows), 8)
        back = L.read_rows(samp_no="C9")
        self.assertEqual(len(back), 8)
        e0 = [r for r in back if r["label"] == "e0"
              and r["bayer_layer"] == "R"][0]
        self.assertEqual((e0["attempt"], e0["verdict"], e0["max_dn"]),
                         (2, "accept", 900.0))
        self.assertEqual([r["rowid"] for r in back],
                         sorted(r["rowid"] for r in back))
        self.assertEqual(L.read_rows(archive="other.h5"), [])
        self.assertEqual(L.write_rows([]), 0)


if __name__ == "__main__":
    unittest.main()
