#!/usr/bin/env python3
"""
Tests for ExposureShowRunner (src/exposure_show.py) and the pad sampling it
uses, on the synthetic scene of test_exposure_fit_runner.

Run from the project root:
    python test/run_tests.py test_exposure_show
"""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import exposure_measure as em
import exposure_show as es
import roi
from capture_runner import CaptureConfig
from rect import Rect
from test_exposure_fit_runner import FitRunnerTestCase, GAINS


class TestPadSamples(unittest.TestCase):

    def test_pools_the_three_patches_sorted_without_the_black_level(self):
        layers = {"B": np.full((50, 50), em.BLACK_DN)}
        patches = {role: Rect(10 * i, 10, 5, 5, name=role)
                   for i, role in enumerate(roi.TEST_PAD_ROLES)}
        for i, role in enumerate(roi.TEST_PAD_ROLES):
            layers["B"][patches[role].xyslice()] = em.BLACK_DN + 100 * (3 - i)
        out = em.pad_samples(layers, patches, "B")
        self.assertEqual(out.size, 75)
        self.assertEqual(list(out[[0, -1]]), [100.0, 300.0])
        self.assertTrue(np.all(np.diff(out) >= 0))

    def test_a_missing_patch_is_named(self):
        with self.assertRaisesRegex(ValueError, "pad_brown"):
            em.pad_samples({"B": np.zeros((9, 9))}, {}, "B")


class TestShowRunner(FitRunnerTestCase):

    def make_show(self, **kw):
        cfg = CaptureConfig(out_dir=Path(self._tmpdir),
                            led_colors=["photo_red", "green", "violet"])
        kw.setdefault("exposures", [5_000])
        return es.ExposureShowRunner(self.cam, self.leds, kw.pop("led", "green"),
                                     config=cfg, **kw)

    def test_one_exposure_gives_a_curve_for_each_layer(self):
        runner = self.make_show(exposures=[5_000])
        list(runner.run())
        self.assertEqual([c.layer for c in runner.curves], list(es.LAYERS))
        self.assertEqual({c.exposure_us for c in runner.curves}, {5_000})
        self.assertEqual(self.cam.exposures_captured.count(5_000) >= 1, True)

    def test_curves_hold_the_pads_steps(self):
        runner = self.make_show(exposures=[10_000])
        list(runner.run())
        sig = runner.curves[0].signal
        gain = GAINS[2]
        self.assertAlmostEqual(sig[0], gain * 0.30 * 10_000, delta=1)
        self.assertAlmostEqual(sig[-1], gain * 0.90 * 10_000, delta=1)

    def test_one_layer_over_several_exposures(self):
        runner = self.make_show(exposures=[2_000, 4_000, 8_000], layers=("Gb",))
        list(runner.run())
        self.assertEqual([(c.exposure_us, c.layer) for c in runner.curves],
                         [(2_000, "Gb"), (4_000, "Gb"), (8_000, "Gb")])
        tops = [c.signal[-1] for c in runner.curves]
        self.assertAlmostEqual(tops[1] / tops[0], 2.0, places=2)

    def test_only_the_named_led_is_lit_and_it_ends_off(self):
        runner = self.make_show(led="violet")
        list(runner.run())
        self.assertEqual({ch for ch, _ in self.leds.history}, {3})
        self.assertEqual(self.leds.lit_channels(), set())

    def test_a_missing_pad_patch_is_refused_before_anything_is_lit(self):
        from test_exposure_fit_runner import db
        db.write_one("DELETE FROM roi WHERE active = 2 AND role = ?",
                     (roi.ROLE_PAD_WHITE,))
        runner = self.make_show()
        with self.assertRaisesRegex(ValueError, "pad_white"):
            list(runner.run())
        self.assertEqual(self.leds.history, [])


if __name__ == "__main__":
    unittest.main()
