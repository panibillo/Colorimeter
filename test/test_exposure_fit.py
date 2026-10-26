#!/usr/bin/env python3
"""
Unit tests for exposure_fit (src/exposure_fit.py).

A simulated sensor stands in for the camera: each patch has a reflectance, an
LED has a gain, and the signal is gain x reflectance x exposure, clipped at the
sensor's usable ceiling.  Low and high percentiles sit 10% either side of the
mean, as they would on a slightly uneven patch.

Run from the project root:
    python test/run_tests.py test_exposure_fit
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import config_repo as config
import db_bootstrap
import db_manager as db
import exposure_fit as ef

CEILING = 959.0

# Reflectance of each patch.  The pad's white-to-brown contrast is 3x.
PAD = {"pad_white": 0.90, "pad_yellow": 0.55, "pad_brown": 0.30, "chip": 0.30}
PAD_DARK_BROWN = dict(PAD, pad_brown=0.20)      # contrast 4.5x: a narrow fit
PAD_EVEN = {"pad_white": 0.90, "pad_yellow": 0.90, "pad_brown": 0.90,
            "chip": 0.90}                       # no contrast: a wide fit


class Sensor:
    """A linear sensor.  `gain` is signal per microsecond at reflectance 1."""

    def __init__(self, gain, pad=PAD):
        self.gain, self.pad, self.calls = gain, pad, []

    def __call__(self, exposure_us):
        self.calls.append(exposure_us)
        out = {}
        for name, refl in self.pad.items():
            mean = min(CEILING, self.gain * refl * exposure_us)
            out[name] = (mean * 0.9, min(CEILING, mean * 1.1))
        return out


P = ef.FitParams()


class TestHelpers(unittest.TestCase):

    def test_quantize_is_idempotent_and_on_a_shared_grid(self):
        for us in (1000, 1234, 4567, 88_000, 199_999):
            q = ef.quantize_us(us, P)
            self.assertEqual(ef.quantize_us(q, P), q)

    def test_quantize_snaps_near_values_together(self):
        self.assertEqual(ef.quantize_us(10_000, P), ef.quantize_us(10_150, P))

    def test_quantize_respects_the_limits(self):
        self.assertEqual(ef.quantize_us(10, P), P.min_us)
        top = ef.quantize_us(10_000_000, P)
        self.assertLessEqual(top, P.max_us)
        self.assertGreater(top, P.max_us / P.quantum_ratio)

    def test_check_levels(self):
        levels = {"a": (50, 400), "b": (200, 900), "c": (200, 400)}
        self.assertEqual(ef.check_levels(levels, P), (["a"], ["b"]))

    def test_usable_range_scales_linearly(self):
        # Brightest high reads 400 at 1000 us -> reaches 850 at 2125 us.
        # Darkest low reads 50 at 1000 us -> reaches 100 at 2000 us.
        r = ef.usable_range({"w": (300, 400), "b": (50, 80)}, 1000, P)
        self.assertAlmostEqual(r.e_max, 1000 * 850 / 400)
        self.assertAlmostEqual(r.e_min, 2000)
        self.assertEqual((r.dark_patch, r.bright_patch), ("b", "w"))
        self.assertTrue(r.feasible)

    def test_no_signal_leaves_the_range_unknown(self):
        r = ef.usable_range({"w": (300, 400), "b": (0, 0)}, 1000, P)
        self.assertFalse(r.known)
        self.assertFalse(r.feasible)


class TestFitLed(unittest.TestCase):

    def assert_every_bracket_passes(self, fit, gain, pad=PAD):
        sensor = Sensor(gain, pad)
        for group, exposures in fit.groups.items():
            self.assertEqual(len(exposures), int(group))
            self.assertEqual(exposures, sorted(exposures))
            for us in exposures:
                self.assertEqual(ef.check_levels(sensor(us), P), ([], []),
                                 f"group {group}, {us} us")

    def test_every_group_fits_and_every_bracket_passes(self):
        fit = ef.fit_led("red", Sensor(0.05), 10_000, P)
        self.assertTrue(fit.ok, fit.failed)
        self.assertEqual(sorted(fit.groups), ["1", "2", "3", "4", "5"])
        self.assert_every_bracket_passes(fit, 0.05)

    def test_does_not_depend_on_the_seed(self):
        a = ef.fit_led("red", Sensor(0.05), 3_000, P)
        b = ef.fit_led("red", Sensor(0.05), 90_000, P)
        self.assertEqual(a.groups, b.groups)

    def test_groups_are_nested_and_seven_exposures_serve_all_five(self):
        fit = ef.fit_led("red", Sensor(0.05, PAD_EVEN), 10_000, P)
        g = fit.groups
        distinct = {us for exposures in g.values() for us in exposures}
        self.assertEqual(len(distinct), 7)
        self.assertEqual(g["2"], [g["5"][0], g["5"][-1]])
        self.assertEqual(g["3"], [g["5"][0], g["5"][2], g["5"][-1]])
        self.assertEqual(g["1"], [g["3"][1]])
        self.assertEqual(g["4"][0], g["5"][0])
        self.assertEqual(g["4"][-1], g["5"][-1])

    def test_positions_are_log_spaced(self):
        fit = ef.fit_led("red", Sensor(0.05, PAD_EVEN), 10_000, P)
        g5 = fit.groups["5"]
        ratios = [b / a for a, b in zip(g5, g5[1:])]
        self.assertAlmostEqual(max(ratios) / min(ratios), 1.0, delta=0.25)

    def test_the_ends_say_which_limit_fixed_them(self):
        fit = ef.fit_led("red", Sensor(0.05, PAD_EVEN), 10_000, P)
        g5 = fit.groups["5"]
        low, high, mid = fit.notes[g5[0]], fit.notes[g5[-1]], fit.notes[g5[1]]
        self.assertTrue(low.remark.startswith("low DN"))
        self.assertIsNotNone(low.low_dn)
        self.assertIsNone(low.high_dn)
        self.assertTrue(high.remark.startswith("high DN"))
        self.assertIsNotNone(high.high_dn)
        self.assertIsNone(high.low_dn)
        self.assertEqual(mid.remark, "log rung 1/4")
        self.assertIsNone(mid.low_dn)
        self.assertEqual(fit.notes[g5[2]].remark, "log rung 1/2")

    def test_a_narrow_range_keeps_small_groups_and_drops_large_ones(self):
        fit = ef.fit_led("blue", Sensor(0.05, PAD_DARK_BROWN), 10_000, P)
        self.assertIn("1", fit.groups)
        self.assertIn("3", fit.groups)
        self.assertNotIn("5", fit.groups)
        self.assertIn("too narrow", fit.failed["5"])
        self.assert_every_bracket_passes(fit, 0.05, PAD_DARK_BROWN)

    def test_the_short_reason_names_the_two_patches_that_disagree(self):
        very_dark = dict(PAD, pad_brown=0.02)
        fit = ef.fit_led("violet", Sensor(0.05, very_dark), 10_000, P)
        text = fit.short_reason()
        self.assertTrue(text.startswith("violet: brown needs "), text)
        self.assertIn("white allows", text)
        self.assertEqual(ef.fit_led("red", Sensor(0.05), 10_000, P).short_reason(), "")

    def test_a_pad_the_window_cannot_hold_fails_every_group(self):
        very_dark = dict(PAD, pad_brown=0.02)
        fit = ef.fit_led("violet", Sensor(0.05, very_dark), 10_000, P)
        self.assertEqual(fit.groups, {})
        self.assertEqual(sorted(fit.failed), ["1", "2", "3", "4", "5"])
        self.assertIn("pad_brown", fit.failed["1"])
        self.assertFalse(fit.ok)

    def test_a_starting_exposure_with_no_signal_recovers(self):
        class Dead(Sensor):
            """Reads zero until the signal is a few DN, like a noisy floor."""
            def __call__(self, exposure_us):
                base = super().__call__(exposure_us)
                return {n: (lo, hi) if lo >= 2 else (0.0, 0.0)
                        for n, (lo, hi) in base.items()}

        sensor = Dead(0.006)
        fit = ef.fit_led("red", sensor, 1_000, P)
        self.assertTrue(fit.ok, fit.failed)
        self.assertGreater(len(sensor.calls), 2)

    def test_an_LED_too_dim_to_reach_the_window_fails_cleanly(self):
        fit = ef.fit_led("dim", Sensor(1e-6), 1_000, P)
        self.assertFalse(fit.ok)
        self.assertEqual(fit.groups, {})

    def test_each_distinct_exposure_is_captured_once(self):
        sensor = Sensor(0.05)
        fit = ef.fit_led("red", sensor, 10_000, P)
        self.assertEqual(len(sensor.calls), len(set(sensor.calls)))
        self.assertEqual(fit.captures, len(sensor.calls))

    def test_a_nonlinear_sensor_is_still_tested_into_the_window(self):
        class Compressing(Sensor):
            """Signal is flattened at the top, as a real sensor's knee does."""
            def __call__(self, exposure_us):
                base = super().__call__(exposure_us)
                return {n: (lo, hi * (1.0 if hi < 500 else 1.12))
                        for n, (lo, hi) in base.items()}

        sensor = Compressing(0.05)
        fit = ef.fit_led("red", sensor, 10_000, P)
        for exposures in fit.groups.values():
            for us in exposures:
                self.assertEqual(ef.check_levels(sensor(us), P), ([], []))


class TestFitLeds(unittest.TestCase):

    GAINS = {"red": 0.05, "green": 0.02, "blue": 0.08}
    SEEDS = {"red": 10_000, "green": 20_000, "blue": 5_000}

    def measure_for(self, led):
        return Sensor(self.GAINS[led])

    def test_all_leds_by_default_in_seed_order(self):
        fits = ef.fit_leds(self.measure_for, self.SEEDS, P)
        self.assertEqual(list(fits), ["red", "green", "blue"])
        self.assertTrue(all(f.ok for f in fits.values()))

    def test_only_one_led_when_asked(self):
        asked = []
        fits = ef.fit_leds(lambda led: asked.append(led) or self.measure_for(led),
                           self.SEEDS, P, only="green")
        self.assertEqual(list(fits), ["green"])
        self.assertEqual(asked, ["green"])

    def test_one_led_alone_matches_the_same_led_in_a_full_run(self):
        full = ef.fit_leds(self.measure_for, self.SEEDS, P)
        alone = ef.fit_leds(self.measure_for, self.SEEDS, P, only="blue")
        self.assertEqual(alone["blue"].groups, full["blue"].groups)

    def test_unknown_led_is_refused(self):
        with self.assertRaises(ValueError):
            ef.fit_leds(self.measure_for, self.SEEDS, P, only="magenta")

    def test_progress_names_each_led_before_it_is_fitted(self):
        seen = []
        ef.fit_leds(self.measure_for, self.SEEDS, P, progress=seen.append)
        self.assertEqual(seen, ["red", "green", "blue"])

    def test_leds_with_different_gains_get_different_exposures(self):
        fits = ef.fit_leds(self.measure_for, self.SEEDS, P)
        self.assertGreater(fits["green"].groups["1"][0],
                           fits["blue"].groups["1"][0])


class TestFromConfig(unittest.TestCase):

    def setUp(self):
        self._saved_dbname = db.DBNAME
        self._tmpdir = tempfile.mkdtemp()
        db.DBNAME = str(Path(self._tmpdir) / "fit_test.sqlite")
        db_bootstrap.ensure(Path(db.DBNAME))
        config.load(reload=True)

    def tearDown(self):
        db.DBNAME = self._saved_dbname
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        config.load(reload=True)

    def test_reads_the_seeded_rows(self):
        p = ef.FitParams.from_config()
        self.assertLess(p.low_dn, p.high_dn)
        self.assertGreater(p.max_ratio, p.min_ratio)


if __name__ == "__main__":
    unittest.main()
