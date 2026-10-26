#!/usr/bin/env python3
"""
Tests for ExposureFitRunner (src/exposure_fit_runner.py).

A synthetic camera looks at a scene with a test pad and the seeded gray chips.
Each LED has its own gain, and the signal is linear in exposure on top of the
64 DN black level, clipped at the sensor ceiling.  No hardware needed.

Run from the project root:
    python test/run_tests.py test_exposure_fit_runner
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import capture_runner
import config_repo as config
import db_bootstrap
import db_manager as db
import exposure_fit as ef
import exposure_fit_runner as efr
import exposure_measure as em
import exposure_repo
import roi
from capture_runner import CaptureConfig
from rect import Rect

FRAME_W, FRAME_H = 2304, 1296
CEILING = 1023.0

# Test pad patches, in stream space, clear of the seeded regions.
PAD_RECTS = {
    roi.ROLE_PAD_WHITE:  (100, 1100, 40, 40),
    roi.ROLE_PAD_YELLOW: (200, 1100, 40, 40),
    roi.ROLE_PAD_BROWN:  (300, 1100, 40, 40),
}
PAD_REFLECTANCE = {roi.ROLE_PAD_WHITE: 0.90, roi.ROLE_PAD_YELLOW: 0.55,
                   roi.ROLE_PAD_BROWN: 0.30}
CHIP_REFLECTANCE = 0.30

# Signal per microsecond at reflectance 1, for each LED (channel number).
GAINS = {1: 0.05, 2: 0.02, 3: 0.08}


class FakeLeds:
    """Three LEDs.  Records switching, and knows which one is lit."""

    _COLORS = {1: "photo_red", 2: "green", 3: "violet"}

    def __init__(self):
        self.state, self.history = {}, []

    def set_channel(self, channel, on):
        self.state[channel] = on
        self.history.append((channel, on))

    def color_name(self, channel):
        return self._COLORS[channel]

    def channel_of(self, name):
        for ch, color in self._COLORS.items():
            if color == name:
                return ch
        raise ValueError(f"Unknown LED name {name!r}")

    def label(self, channel):
        return f"LED {channel} ({self.color_name(channel)})"

    def lit_channels(self):
        return {ch for ch, on in self.state.items() if on}


class SceneCam:
    """A camera over the pad scene.  RGGB, 10-bit, black level 64."""

    has_autofocus = True
    bayer_pattern = "RGGB"
    model_description = "SceneCam (synthetic)"

    def __init__(self, leds, chips, gains=GAINS):
        self.leds, self.gains = leds, dict(gains)
        self.config_size = None
        self.exposures_captured = []
        self._exposure_us = 0
        self.fail_after = None
        self._regions = [(rect, PAD_REFLECTANCE[role])
                         for role, rect in PAD_RECTS.items()]
        self._regions += [((c.x, c.y, c.w, c.h), CHIP_REFLECTANCE)
                          for c in chips]

    def set_exposure(self, us):
        self._exposure_us = int(us)

    def setup_camera(self):
        self.config_size = (FRAME_W, FRAME_H)

    def focus(self, force=False, leds=None):
        return 3.0

    def signal_at(self, reflectance, exposure_us):
        lit = self.leds.lit_channels()
        gain = self.gains[next(iter(lit))] if lit else 0.0
        return min(CEILING - em.BLACK_DN, gain * reflectance * exposure_us)

    def capture_frame(self):
        self.exposures_captured.append(self._exposure_us)
        if (self.fail_after is not None
                and len(self.exposures_captured) > self.fail_after):
            raise RuntimeError("camera fell over")
        bayer = np.full((FRAME_H, FRAME_W), em.BLACK_DN)
        for (x, y, w, h), refl in self._regions:
            bayer[y:y + h, x:x + w] = (
                em.BLACK_DN + self.signal_at(refl, self._exposure_us))
        meta = {"ExposureTime": self._exposure_us}
        return None, bayer.astype(np.uint16), meta


class FitRunnerTestCase(unittest.TestCase):

    def setUp(self):
        self._saved_dbname = db.DBNAME
        self._tmpdir = tempfile.mkdtemp()
        db.DBNAME = str(Path(self._tmpdir) / "fit_runner_test.sqlite")
        db_bootstrap.ensure(Path(db.DBNAME))
        config.load(reload=True)
        self._saved_warmup = capture_runner.LED_ON_WARMUP_S
        capture_runner.LED_ON_WARMUP_S = 0.0

        self.leds = FakeLeds()
        chips = roi.all_by_role(roi.load_active(FRAME_W, FRAME_H, None),
                                roi.ROLE_WHITE_BALANCE)
        self.cam = SceneCam(self.leds, chips)
        self.add_pad()

    def tearDown(self):
        capture_runner.LED_ON_WARMUP_S = self._saved_warmup
        db.DBNAME = self._saved_dbname
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        config.load(reload=True)

    def add_pad(self, skip=()):
        for role, (x, y, w, h) in PAD_RECTS.items():
            if role not in skip:
                roi._insert(role, role, Rect(x, y, w, h), FRAME_W, FRAME_H,
                            None, None, "test pad", active=2)

    def make_runner(self, **kwargs):
        cfg = CaptureConfig(out_dir=Path(self._tmpdir),
                            led_colors=["photo_red", "green", "violet"])
        return efr.ExposureFitRunner(self.cam, self.leds, cfg, **kwargs)

    def passes(self, color, exposure_us):
        """Would every required channel of `color` pass the window here?"""
        gain = self.cam.gains[self.leds.channel_of(color)]
        low = config.calibration_window_low_dn()
        high = config.calibration_window_high_dn()
        for refl in list(PAD_REFLECTANCE.values()) + [CHIP_REFLECTANCE]:
            level = min(CEILING - em.BLACK_DN, gain * refl * exposure_us)
            if not low <= level <= high:
                return False
        return True


class TestFitAllLeds(FitRunnerTestCase):

    def test_every_led_gets_every_group_and_every_bracket_passes(self):
        runner = self.make_runner()
        list(runner.run())
        self.assertEqual(list(runner.fits), ["photo_red", "green", "violet"])
        self.assertTrue(runner.ok, {c: f.failed for c, f in runner.fits.items()})
        for color, fit in runner.fits.items():
            self.assertEqual(sorted(fit.groups), ["1", "2", "3", "4", "5"])
            for exposures in fit.groups.values():
                for us in exposures:
                    self.assertTrue(self.passes(color, us), (color, us))

    def test_leds_are_off_afterward(self):
        runner = self.make_runner()
        list(runner.run())
        self.assertEqual(self.leds.lit_channels(), set())

    def test_progress_ends_done_with_a_full_fraction(self):
        steps = list(self.make_runner().run())
        self.assertEqual(steps[-1].phase, "done")
        self.assertEqual(steps[-1].fraction, 1.0)

    def test_the_capture_hook_sees_every_frame(self):
        seen = []
        runner = self.make_runner(on_capture=lambda *a: seen.append(a))
        list(runner.run())
        self.assertEqual(len(seen), len(self.cam.exposures_captured))
        self.assertEqual([n for _, _, n in seen], list(range(1, len(seen) + 1)))


class TestStatusHook(FitRunnerTestCase):

    def test_reports_warm_up_then_every_rung_in_order(self):
        seen = []
        runner = self.make_runner(only="green",
                                  on_status=lambda c, t: seen.append((c, t)))
        list(runner.run())
        self.assertEqual(seen[0], ("green", "green warming up ..."))
        self.assertEqual(seen[1], ("green", "green finding range ..."))
        rungs = [t for _, t in seen[2:]]
        self.assertEqual(rungs[0], "green calibrating rung 1 of 7 ...")
        self.assertEqual(rungs[-1], "green calibrating rung 7 of 7 ...")
        self.assertEqual(len(rungs), 7)

    def test_a_given_setting_replaces_the_configs_for_that_led(self):
        mine = efr.LedSettings(("Gr",), efr.required_cells.LedWindow(120, 850, 1.0, 99.0))
        runner = self.make_runner(only="green", settings={"green": mine})
        list(runner.run())
        self.assertTrue(runner.ok)
        self.assertEqual(runner.layers["green"], "Gr")
        for us in runner.fits["green"].groups["5"]:
            gain = self.cam.gains[2]
            self.assertGreaterEqual(gain * 0.30 * us, 120 * 0.97)
            self.assertLessEqual(gain * 0.90 * us, 850 * 1.03)

    def test_a_led_window_in_config_applies_to_that_led_only(self):
        config.save("calibration", "led_windows", "green=120:850:10:90")
        runner = self.make_runner()
        list(runner.run())
        self.assertGreaterEqual(
            GAINS[2] * 0.30 * runner.fits["green"].groups["5"][0], 120 * 0.97)
        self.assertEqual(runner.settings_for("violet").window.low_dn, 100)


class TestFitOneLed(FitRunnerTestCase):

    def test_only_the_named_led_is_lit_and_fitted(self):
        runner = self.make_runner(only="green")
        list(runner.run())
        self.assertEqual(list(runner.fits), ["green"])
        self.assertEqual({ch for ch, _ in self.leds.history}, {2})

    def test_the_result_matches_the_same_led_in_a_full_run(self):
        full = self.make_runner()
        list(full.run())
        alone = self.make_runner(only="green")
        list(alone.run())
        self.assertEqual(alone.fits["green"].groups, full.fits["green"].groups)

    def test_an_unknown_led_is_refused_before_anything_is_lit(self):
        runner = self.make_runner(only="magenta")
        with self.assertRaises(ValueError):
            list(runner.run())
        self.assertEqual(self.leds.history, [])


class TestSetupProblems(FitRunnerTestCase):

    def test_a_missing_pad_patch_is_refused_before_anything_is_lit(self):
        db.write_one("DELETE FROM roi WHERE active = 2 AND role = ?",
                     (roi.ROLE_PAD_BROWN,))
        runner = self.make_runner()
        with self.assertRaisesRegex(ValueError, "pad_brown"):
            list(runner.run())
        self.assertEqual(self.leds.history, [])

    def test_a_camera_failure_still_turns_the_led_off(self):
        self.cam.fail_after = 5
        runner = self.make_runner(only="green")
        with self.assertRaises(RuntimeError):
            list(runner.run())
        self.assertEqual(self.leds.lit_channels(), set())


class TestSeed(FitRunnerTestCase):

    def test_starts_from_the_stored_middle_bracket(self):
        exposure_repo.write_all_groups("green", {"3": [8_000, 12_000, 18_000]})
        runner = self.make_runner(only="green")
        list(runner.run())
        self.assertEqual(self.cam.exposures_captured[0], 12_000)

    def test_without_a_stored_calibration_starts_from_the_default(self):
        runner = self.make_runner(only="green")
        list(runner.run())
        self.assertEqual(self.cam.exposures_captured[0],
                         CaptureConfig().initial_us)


class TestPersist(FitRunnerTestCase):

    def test_stores_the_tested_lists_and_replaces_the_old(self):
        exposure_repo.write_all_groups("green", {"1": [1_234]})
        runner = self.make_runner(only="green")
        list(runner.run())
        rows = runner.persist(add_dt="2026-10-26 12:00:00")
        fit = runner.fits["green"]
        self.assertEqual(rows, sum(len(v) for v in fit.groups.values()))
        for group, exposures in fit.groups.items():
            self.assertEqual(exposure_repo.read_exposures("green", group),
                             exposures)
        self.assertEqual(exposure_repo.middle_exposure("green", "3"),
                         fit.groups["3"][1])

    def test_stores_the_limits_the_layers_and_the_preimage_flag(self):
        runner = self.make_runner(only="green")
        list(runner.run())
        runner.persist(add_dt="2026-10-26 12:00:00")
        rows = db.read_all(
            "SELECT exposuregroup, microseconds, remark, low_dn, high_dn, "
            "layers, preimage FROM exposure WHERE LED = 'green' AND active = 1")
        self.assertTrue(rows)
        self.assertEqual({r[5] for r in rows}, {"Gb Gr"})
        g5 = runner.fits["green"].groups["5"]
        low = [r for r in rows if r[0] == "5" and r[1] == g5[0]][0]
        high = [r for r in rows if r[0] == "5" and r[1] == g5[-1]][0]
        mid = [r for r in rows if r[0] == "5" and r[1] == g5[2]][0]
        self.assertTrue(low[2].startswith("low DN"))
        self.assertIsNotNone(low[3]); self.assertIsNone(low[4])
        self.assertTrue(high[2].startswith("high DN"))
        self.assertIsNone(high[3]); self.assertIsNotNone(high[4])
        self.assertEqual(mid[2], "log rung 1/2")
        self.assertEqual({r[6] for r in rows if r[0] == "1"}, {1})
        self.assertEqual({r[6] for r in rows if r[0] != "1"}, {0})
        self.assertEqual(exposure_repo.read_preimage_exposures("green"),
                         runner.fits["green"].groups["1"])

    def test_a_led_that_fitted_nothing_keeps_its_old_calibration(self):
        self.cam.gains[2] = 1e-7                       # green is far too dim
        exposure_repo.write_all_groups("green", {"1": [7_777]})
        runner = self.make_runner(only="green")
        list(runner.run())
        self.assertEqual(runner.fits["green"].groups, {})
        self.assertFalse(runner.ok)
        self.assertEqual(runner.persist(), 0)
        self.assertEqual(exposure_repo.read_exposures("green", "1"), [7_777])

    def test_other_leds_are_untouched_by_a_single_led_run(self):
        exposure_repo.write_all_groups("violet", {"1": [4_321]})
        runner = self.make_runner(only="green")
        list(runner.run())
        runner.persist()
        self.assertEqual(exposure_repo.read_exposures("violet", "1"), [4_321])


if __name__ == "__main__":
    unittest.main()
