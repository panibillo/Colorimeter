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
from test_exposure_fit_runner import FRAME_H, FRAME_W, FitRunnerTestCase, GAINS


class TestPadSamples(unittest.TestCase):

    def test_pools_the_region_sorted_without_the_black_level(self):
        layers = {"B": np.full((50, 50), em.BLACK_DN)}
        layers["B"][10:15, 10:20] = em.BLACK_DN + 300
        layers["B"][15:20, 10:20] = em.BLACK_DN + 100
        out = em.pad_samples(layers, Rect(10, 10, 10, 10), "B")
        self.assertEqual(out.size, 100)
        self.assertEqual(list(out[[0, -1]]), [100.0, 300.0])
        self.assertTrue(np.all(np.diff(out) >= 0))

    def test_an_empty_region_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no pixels"):
            em.pad_samples({"B": np.zeros((9, 9))}, Rect(20, 20, 4, 4), "B")

    def test_a_missing_sample_region_is_named(self):
        with self.assertRaisesRegex(ValueError, "sample region"):
            em.sample_rect({})


# The whole pad, in stream space: a dark top half and a light bottom half.
SAMPLE_RECT = (500, 1100, 40, 40)
SAMPLE_HALVES = [((500, 1100, 40, 20), 0.30), ((500, 1120, 40, 20), 0.90)]


class TestShowRunner(FitRunnerTestCase):

    def setUp(self):
        super().setUp()
        roi.save("sample", Rect(*SAMPLE_RECT), frame=(FRAME_W, FRAME_H))
        self.cam._regions += SAMPLE_HALVES

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
        self.assertEqual(sig.size, (SAMPLE_RECT[2] // 2) * (SAMPLE_RECT[3] // 2))
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

    def test_the_test_pad_is_not_needed(self):
        from test_exposure_fit_runner import db
        db.write_one("DELETE FROM roi WHERE active = 2")
        runner = self.make_show()
        list(runner.run())
        self.assertEqual(len(runner.curves), len(es.LAYERS))

    def test_a_missing_sample_region_is_refused_before_anything_is_lit(self):
        from test_exposure_fit_runner import db
        db.write_one("UPDATE roi SET active = 0 WHERE active = 1 AND role = ?",
                     (roi.ROLE_SAMPLE,))
        runner = self.make_show()
        with self.assertRaisesRegex(roi.RoiMissing, "sample"):
            list(runner.run())
        self.assertEqual(self.leds.history, [])

if __name__ == "__main__":
    unittest.main()
