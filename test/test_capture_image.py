#!/usr/bin/env python3
"""
Tests for Capture Image: the rung lookup (exposure_repo.rung_exposures), the
test_image table (test_image_repo) and the runner (capture_image.py), on the
synthetic scene of test_exposure_fit_runner.

Run from the project root:
    python test/run_tests.py test_capture_image
"""

import sys
import unittest
from pathlib import Path

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import capture_image as ci
import exposure_repo
import roi
import test_image_repo
from test_exposure_fit_runner import (FRAME_H, FRAME_W, FitRunnerTestCase,
                                      SceneCam, db)

# Seven exposures, one per rung, as a full calibration stores them.
RUNG_US = {"0": 2000, "1/4": 2500, "1/3": 2800, "1/2": 3200,
           "2/3": 3600, "3/4": 4000, "1": 5000}


def store_ladder(led, groups=("5", "4", "3", "2", "1")):
    for group in groups:
        rungs = exposure_repo._GROUP_RUNGS[group]
        exposure_repo.write_led_exposures(led, group, [RUNG_US[r] for r in rungs])


class RgbSceneCam(SceneCam):
    """The scene camera, with an RGB frame beside the Bayer one, as BayerCam has."""

    def capture_frame(self):
        _, bayer, meta = super().capture_frame()
        return np.full((FRAME_H, FRAME_W, 3), 128, np.uint8), bayer, meta


class TestRungExposures(FitRunnerTestCase):

    def test_every_group_gives_all_seven_rungs_in_order(self):
        store_ladder("green")
        self.assertEqual(exposure_repo.rung_exposures("green"), RUNG_US)

    def test_only_the_rungs_a_stored_group_covers(self):
        store_ladder("green", groups=("3",))
        self.assertEqual(exposure_repo.rung_exposures("green"),
                         {"0": 2000, "1/2": 3200, "1": 5000})

    def test_a_group_with_the_wrong_count_is_skipped(self):
        exposure_repo.write_led_exposures("green", "4", [1000, 2000])
        self.assertEqual(exposure_repo.rung_exposures("green"), {})


class TestNames(FitRunnerTestCase):

    def setUp(self):
        super().setUp()
        self.dir = Path(self._tmpdir) / "test_image"

    def test_first_name_is_the_template(self):
        self.assertEqual(test_image_repo.next_name(self.dir), "IMG0001")

    def test_next_name_counts_the_table_and_the_disk(self):
        test_image_repo.record("IMG0001", "green", ["0"], [2000], ["jpg_whole"])
        self.assertEqual(test_image_repo.next_name(self.dir), "IMG0002")
        self.dir.mkdir()
        (self.dir / "IMG0007.h5").touch()
        self.assertEqual(test_image_repo.next_name(self.dir), "IMG0008")

    def test_problems(self):
        test_image_repo.record("IMG0001", "green", ["0"], [2000], ["jpg_whole"])
        self.dir.mkdir()
        (self.dir / "IMG0002.h5").touch()
        problem = test_image_repo.name_problem
        self.assertIn("already used", problem("img0001", self.dir))
        self.assertIn("on disk", problem("IMG0002", self.dir))
        self.assertIn("only", problem("IMG 3", self.dir))
        self.assertEqual(problem("", self.dir), "Type a file name")
        self.assertIsNone(problem("IMG0003", self.dir))


class TestChoiceProblem(unittest.TestCase):

    def check(self, rungs, types, exposures=RUNG_US):
        return ci.choice_problem(ci.ImageChoice("green", rungs, types), exposures)

    def test_problems(self):
        self.assertIn("rung", self.check([], ["jpg_whole"]))
        self.assertIn("no stored rung 1/3", self.check(["1/3"], ["jpg_whole"],
                                                       {"0": 1000}))
        self.assertIn("image type", self.check(["0"], ["darkframes"]))
        self.assertIn("Bayers", self.check(["0"], ["jpg_whole", "darkframes"]))
        self.assertIsNone(self.check(["0", "1"], ["bayer_bound", "darkframes"]))


class TestCaptureImageRunner(FitRunnerTestCase):

    def setUp(self):
        super().setUp()
        self.cam = RgbSceneCam(self.leds, roi.all_by_role(
            roi.load_active(FRAME_W, FRAME_H, None), roi.ROLE_WHITE_BALANCE))
        self.dir = Path(self._tmpdir) / "test_image"
        store_ladder("green")

    def run_choice(self, rungs=("0", "1/2"), types=ci.IMAGE_TYPES, name="IMG0001"):
        choice = ci.ImageChoice("green", list(rungs), list(types), name,
                                "a test", self.dir)
        runner = ci.CaptureImageRunner(self.cam, self.leds, choice)
        list(runner.run())
        return runner

    def test_saves_every_image_type_and_records_the_row(self):
        runner = self.run_choice()
        self.assertEqual(runner.out_path, self.dir / "IMG0001.h5")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["IMG0001.h5"])
        with h5py.File(runner.out_path, "r") as f:
            led = f["leds/green"]
            self.assertEqual(sorted(led["lit"]), ["r0", "r1_2"])
            self.assertEqual(led["lit_whole/r0/R"].shape, (FRAME_H // 2, FRAME_W // 2))
            self.assertLess(led["lit/r0/R"].shape[0], FRAME_H // 2)
            self.assertEqual(sorted(led["jpeg"]),
                             ["r0_bound", "r0_whole", "r1_2_bound", "r1_2_whole"])
            self.assertEqual(led["jpeg/r1_2_whole"].attrs["exposure_us"], 3200)
            self.assertEqual(led["lit/r1_2/R"].attrs["exposure_us"], 3200)
            self.assertIn("after_r0", led["dark"])
            self.assertIn("after_r1_2", led["dark_whole"])
            self.assertEqual(sorted(f["dark"]), ["after_2000us", "after_3200us"])
        row = db.read_one("SELECT h5name, led, rungs, exposures_us, image_types, "
                          "description FROM test_image")
        self.assertEqual(row, ("IMG0001.h5", "green", "0 1/2", "2000 3200",
                               " ".join(ci.IMAGE_TYPES), "a test"))
        self.assertEqual(self.leds.lit_channels(), set())

    def test_only_the_ticked_types_are_stored(self):
        runner = self.run_choice(rungs=["1"], types=["jpg_bound"])
        with h5py.File(runner.out_path, "r") as f:
            self.assertEqual(sorted(f["leds/green"]), ["jpeg", "led_metadata"])
            self.assertNotIn("dark", f)

    def test_a_used_name_is_refused_before_anything_is_lit(self):
        self.run_choice()
        self.leds.history.clear()
        with self.assertRaisesRegex(ValueError, "already used"):
            self.run_choice()
        self.assertEqual(self.leds.history, [])

    def test_a_failed_capture_leaves_no_file_and_no_row(self):
        self.cam.fail_after = 1
        with self.assertRaisesRegex(RuntimeError, "fell over"):
            self.run_choice()
        self.assertEqual(list(self.dir.iterdir()), [])
        self.assertEqual(db.read_val("SELECT COUNT(*) FROM test_image"), 0)
        self.assertEqual(self.leds.lit_channels(), set())


if __name__ == "__main__":
    unittest.main()
