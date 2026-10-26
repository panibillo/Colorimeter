#!/usr/bin/env python3
"""
Unit tests for exposure_measure (src/exposure_measure.py).

A synthetic scene stands in for the camera: four Bayer layers whose patches
have known reflectances, scaled by exposure and offset by the black level.
The last test runs the whole chain, measure function into the fit.

Run from the project root:
    python test/run_tests.py test_exposure_measure
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import config_repo as config
import db_bootstrap
import db_manager as db
import exposure_fit as ef
import exposure_measure as em
import roi
from rect import Rect

# Stream-space regions (even, so Bayer-safe); layer space is half of these.
PAD = {
    roi.ROLE_PAD_WHITE:  Rect(100, 100, 40, 40, name="pw"),
    roi.ROLE_PAD_YELLOW: Rect(200, 100, 40, 40, name="py"),
    roi.ROLE_PAD_BROWN:  Rect(300, 100, 40, 40, name="pb"),
}
for _role, _r in PAD.items():
    _r.role = _role
CHIP = Rect(400, 100, 40, 40, name="chip1")
CHIP.role = roi.ROLE_WHITE_BALANCE
ROIS = {"chip1": CHIP}

# Reflectance of each patch, by layer-space name.
REFLECTANCE = {"pw": 0.90, "py": 0.55, "pb": 0.30, "chip1": 0.30}
FRAME = (200, 300)                      # layer rows, cols


def layers_at(exposure_us, gain, bright_layer=None, boost=50.0):
    """Four layers of a scene: patches scaled by reflectance, plus black."""
    layers = {ch: np.full(FRAME, em.BLACK_DN, dtype=float)
              for ch in ("R", "Gr", "Gb", "B")}
    rects = {"pw": PAD[roi.ROLE_PAD_WHITE], "py": PAD[roi.ROLE_PAD_YELLOW],
             "pb": PAD[roi.ROLE_PAD_BROWN], "chip1": CHIP}
    for ch, layer in layers.items():
        # A channel that is far too bright, to prove it is never consulted.
        factor = boost if ch == bright_layer else 1.0
        for name, r in rects.items():
            lr = roi.to_layer_rect(r)
            signal = min(959.0, gain * factor * REFLECTANCE[name] * exposure_us)
            layer[lr.xyslice()] = em.BLACK_DN + signal
    return layers


class TestCalibrationPatches(unittest.TestCase):

    def test_gathers_pad_and_chips_in_layer_coordinates(self):
        patches = em.calibration_patches(ROIS, PAD)
        self.assertEqual(sorted(patches),
                         sorted(list(roi.TEST_PAD_ROLES) + ["chip1"]))
        self.assertEqual(patches[roi.ROLE_PAD_WHITE].xywh(), (50, 50, 20, 20))

    def test_a_missing_pad_patch_is_named(self):
        pad = {k: v for k, v in PAD.items() if k != roi.ROLE_PAD_BROWN}
        with self.assertRaisesRegex(ValueError, "pad_brown"):
            em.calibration_patches(ROIS, pad)

    def test_no_gray_chip_is_refused(self):
        with self.assertRaisesRegex(ValueError, "gray chip"):
            em.calibration_patches({}, PAD)


class TestPatchLevels(unittest.TestCase):

    def setUp(self):
        self.patches = em.calibration_patches(ROIS, PAD)

    def test_removes_the_black_level(self):
        layers = layers_at(1000, 0.1)               # white: 0.1 * 0.9 * 1000 = 90
        out = em.patch_levels(layers, self.patches, ("B",), 10, 90)
        low, high = out["pad_white.B"]
        self.assertAlmostEqual(low, 90.0)
        self.assertAlmostEqual(high, 90.0)

    def test_keys_name_the_patch_and_the_channel(self):
        out = em.patch_levels(layers_at(1000, 0.1), self.patches,
                              ("Gb", "Gr"), 10, 90)
        self.assertIn("pad_brown.Gb", out)
        self.assertIn("chip1.Gr", out)
        self.assertEqual(len(out), 4 * 2)

    def test_percentiles_follow_the_spread(self):
        layers = layers_at(1000, 0.1)
        lr = self.patches[roi.ROLE_PAD_WHITE]
        patch = layers["B"][lr.xyslice()]
        patch[: patch.shape[0] // 2] += 100         # upper half brighter
        out = em.patch_levels(layers, self.patches, ("B",), 10, 90)
        low, high = out["pad_white.B"]
        self.assertAlmostEqual(high - low, 100.0)

    def test_a_patch_outside_the_frame_is_refused(self):
        patches = {"far": Rect(5000, 5000, 20, 20, name="far")}
        with self.assertRaises(ValueError):
            em.patch_levels(layers_at(1000, 0.1), patches, ("B",), 10, 90)


class TestMakeMeasure(unittest.TestCase):

    def test_grabs_a_frame_at_the_requested_exposure(self):
        asked = []

        def grab(us):
            asked.append(us)
            return layers_at(us, 0.1)

        measure = em.make_measure(grab, em.calibration_patches(ROIS, PAD),
                                  ("B",), 10, 90)
        short, long_ = measure(1000), measure(2000)
        self.assertEqual(asked, [1000, 2000])
        self.assertAlmostEqual(long_["pad_white.B"][1],
                               2 * short["pad_white.B"][1])

    def test_unrequired_channels_are_never_measured(self):
        grabbed = layers_at(5000, 0.1, bright_layer="R")
        measure = em.make_measure(lambda us: grabbed,
                                  em.calibration_patches(ROIS, PAD),
                                  ("B",), 10, 90)
        self.assertTrue(all(key.endswith(".B") for key in measure(5000)))


class TestEndToEnd(unittest.TestCase):

    def setUp(self):
        self._saved_dbname = db.DBNAME
        self._tmpdir = tempfile.mkdtemp()
        db.DBNAME = str(Path(self._tmpdir) / "measure_test.sqlite")
        db_bootstrap.ensure(Path(db.DBNAME))
        config.load(reload=True)

    def tearDown(self):
        db.DBNAME = self._saved_dbname
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        config.load(reload=True)

    def test_a_too_bright_unrequired_channel_does_not_spoil_the_fit(self):
        """Red is a required channel for photo_red only; B here is not."""
        gain = 0.05
        grab = lambda us: layers_at(us, gain, bright_layer="B")   # B is 50x
        measure = em.measure_for_led("photo_red", grab, ROIS, PAD)  # needs R
        fit = ef.fit_led("photo_red", measure, 10_000, ef.FitParams.from_config())
        self.assertTrue(fit.ok, fit.failed)
        self.assertEqual(sorted(fit.groups), ["1", "2", "3", "4", "5"])

    def test_a_too_bright_required_channel_does_shape_the_fit(self):
        gain = 0.05
        bright = lambda us: layers_at(us, gain, bright_layer="R", boost=3.0)
        plain = lambda us: layers_at(us, gain)
        params = ef.FitParams.from_config()
        fit_bright = ef.fit_led(
            "photo_red", em.measure_for_led("photo_red", bright, ROIS, PAD),
            10_000, params)
        fit_plain = ef.fit_led(
            "photo_red", em.measure_for_led("photo_red", plain, ROIS, PAD),
            10_000, params)
        self.assertLess(fit_bright.groups["1"][0], fit_plain.groups["1"][0])

    def test_green_is_measured_in_both_green_channels(self):
        measure = em.measure_for_led("green", lambda us: layers_at(us, 0.05),
                                     ROIS, PAD)
        keys = measure(5000)
        self.assertIn("pad_white.Gb", keys)
        self.assertIn("pad_white.Gr", keys)
        self.assertNotIn("pad_white.R", keys)


if __name__ == "__main__":
    unittest.main()
