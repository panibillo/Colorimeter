#!/usr/bin/env python3
"""
Unit tests for exposure_rule (src/exposure_rule.py): the arithmetic of the
strip exposure rule, on small hand-made layers.

The frames themselves (re-aiming, N frames at Tus, Bus) are tested through
the capture runner in test_capture_runner.TestStripRule.

Run from the project root:
    python test/run_tests.py test_exposure_rule
"""

import sys
import unittest
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import exposure_rule as R                        # noqa: E402
from rect import Rect                            # noqa: E402

SHAPE = (10, 12)


def region(hot=None):
    """Pad = rows 2-7, cols 2-7; gray1 = cols 9-10 rows 0-1; gray2 rows 8-9."""
    pad = np.zeros(SHAPE, dtype=bool)
    pad[2:8, 2:8] = True
    return R.TestRegion.from_rects(SHAPE, pad, Rect(9, 0, 2, 2),
                                   Rect(9, 8, 2, 2), hot)


def layer(value, **spots):
    """A layer at `value` raw DN everywhere, with {(row, col): DN} spots."""
    a = np.full(SHAPE, float(value))
    for (r, c), v in spots.get("at", {}).items():
        a[r, c] = v
    return a


class TestMeasure(unittest.TestCase):

    def test_counts_and_where_the_maximum_is(self):
        a = layer(500, at={(3, 3): 1023, (4, 4): 100, (0, 9): 1100 - 77})
        st = R.measure({"R": a}, region())["R"]
        self.assertEqual(st.n_pixels, 36 + 4 + 4)
        self.assertEqual(st.max_dn, 1023)
        self.assertEqual(st.n_clip_top, 2)
        self.assertEqual(st.n_below_floor, 1)
        self.assertEqual(st.min_dn, 100)

    def test_hot_pixels_are_left_out(self):
        hot = np.zeros(SHAPE, dtype=bool)
        hot[3, 3] = True
        a = layer(500, at={(3, 3): 1023})
        st = R.measure({"R": a}, region(hot))["R"]
        self.assertEqual(st.n_clip_top, 0)
        self.assertEqual(st.n_pixels, 43)
        self.assertEqual(region(hot).n_hot_inside, 1)

    def test_pixels_outside_the_region_do_not_count(self):
        a = layer(500, at={(0, 0): 1023})
        self.assertEqual(R.measure({"R": a}, region())["R"].n_clip_top, 0)

    def test_maximum_on_a_chip_is_named(self):
        a = layer(500, at={(9, 10): 900})
        self.assertEqual(R.measure({"R": a}, region())["R"].max_where, "gray2")


class TestJudge(unittest.TestCase):

    def stats(self, top, clipped=0):
        a = layer(300, at={(3, 3): top})
        if clipped:
            a[4, 4] = 1023
        return R.measure({"R": a}, region())

    def test_clipped_steps_down(self):
        verdict, nxt, why = R.judge(self.stats(900, clipped=1), ["R"], 10_000)
        self.assertEqual((verdict, nxt, why), (R.CLIPPED, 7000, "R"))

    def test_dim_is_scaled_to_the_target(self):
        """Docstring example: 500 raw at 10000 us -> 18476 us."""
        verdict, nxt, _ = R.judge(self.stats(500), ["R"], 10_000)
        self.assertEqual(verdict, R.DIM)
        self.assertEqual(nxt, 18476)

    def test_accepts_at_80_percent(self):
        verdict, nxt, _ = R.judge(self.stats(819), ["R"], 10_000)
        self.assertEqual((verdict, nxt), (R.ACCEPT, 10_000))

    def test_a_dim_step_is_limited(self):
        """Brightest pixel 70 raw (6 DN of signal): at most x DIM_STEP_LIMIT."""
        st = R.measure({"R": layer(70)}, region())
        _, nxt, _ = R.judge(st, ["R"], 10_000)
        self.assertEqual(nxt, int(10_000 * R.DIM_STEP_LIMIT))

    def test_the_cap_holds(self):
        _, nxt, _ = R.judge(self.stats(500), ["R"], R.EXPOSURE_CAP_US)
        self.assertEqual(nxt, R.EXPOSURE_CAP_US)

    def test_only_decision_layers_count(self):
        st = R.measure({"R": layer(850), "B": layer(1023)}, region())
        verdict, _, _ = R.judge(st, ["R"], 10_000)
        self.assertEqual(verdict, R.ACCEPT)


class TestLayersAndBus(unittest.TestCase):

    def test_decision_layers(self):
        names = ["R", "Gr", "Gb", "B"]
        self.assertEqual(R.decision_layers("photo_red", names), ["R"])
        self.assertEqual(R.decision_layers("green", names), ["Gr", "Gb", "B"])
        self.assertEqual(R.decision_layers("amber", names), names)

    def test_bus_docstring_example(self):
        """Darkest green.B 120 raw at Tus 8760 us -> 17207 us."""
        st = R.measure({"B": layer(400, at={(5, 5): 120}),
                        "Gb": layer(800)}, region())
        bus, lay, low = R.bus_exposure(st, "green", 8760)
        self.assertEqual((bus, lay, low), (17207, "B", 120.0))

    def test_no_bus_when_nothing_is_below_the_floor(self):
        st = R.measure({"B": layer(400)}, region())
        self.assertIsNone(R.bus_exposure(st, "green", 8760)[0])

    def test_no_bus_for_an_led_without_analysis_channels(self):
        st = R.measure({"R": layer(400, at={(5, 5): 100})}, region())
        self.assertIsNone(R.bus_exposure(st, "amber", 8760)[0])

    def test_keep_layers(self):
        st = R.measure({"B": layer(1023), "Gb": layer(900, at={(3, 3): 1023}),
                        "Gr": layer(150), "R": layer(500)}, region())
        kept, why = R.keep_layers("green", st)
        self.assertEqual(sorted(kept), ["B", "Gb", "Gr", "R"])  # R: unclipped
        self.assertEqual(why["Gr"], "analysis channel")
        kept, why = R.keep_layers("amber", st)
        self.assertEqual(kept, ["R"])
        self.assertEqual((why["B"], why["Gr"]), ("clipped", "below floor"))

    def test_coverage(self):
        tus = {"B": layer(400, at={(5, 5): 120, (6, 6): 130})}
        bus = {"B": layer(1023, at={(5, 5): 230, (6, 6): 150})}
        c = R.coverage(tus, bus, region(), ["B"])["B"]
        self.assertEqual(c["n_below_floor_both"], 1)       # (6, 6) only
        none = R.coverage(tus, None, region(), ["B"])["B"]
        self.assertEqual(none["n_below_floor_both"], 2)



class TestEdgeRule(unittest.TestCase):
    """
    A 40 x 40 crop with a pad on columns 5-34 and rows 4-35.  Column 5 and
    column 34 are band 0 of the left and right sides (0.5 px in), columns 6
    and 33 band 1.  Interior signal 700 DN, band 1 560 DN (d_1 = 0.097, so
    the limit for band 0 is d = 0.485, signal 229).
    """

    SH = (40, 40)

    def setUp(self):
        y, x = np.mgrid[0:self.SH[0], 0:self.SH[1]].astype(float)
        self.dist = {"L": x - 4.5, "R": 34.5 - x, "T": y - 3.5, "B": 35.5 - y}
        near = np.minimum.reduce(list(self.dist.values()))
        self.pad = near > 0
        self.raw = np.full(self.SH, 64.0 + 700)
        for c in (6, 33):
            self.raw[:, c] = 64 + 560
        for c in (5, 34):
            self.raw[:, c] = 64 + 400            # an ordinary edge pixel
        self.raw[20, 5] = 144                    # mostly off the pad
        self.raw[3 + 1, 20] = 144                # top edge: always kept

    def region(self, dist="yes"):
        return R.TestRegion.from_rects(
            self.SH, self.pad, Rect(0, 0, 2, 2), Rect(38, 0, 2, 2),
            pad_source="fit", dist=self.dist if dist == "yes" else None)

    def test_only_the_off_pad_side_pixel_is_left_out(self):
        out = R.edge_left_out(self.raw, self.region())
        self.assertEqual(list(zip(*np.nonzero(out))), [(20, 5)])

    def test_the_floor_counts_skip_it_and_the_clip_count_does_not(self):
        st = R.measure({"R": self.raw}, self.region())["R"]
        self.assertEqual(st.n_edge_left_out, 1)
        self.assertEqual(st.n_below_floor, 1)        # the top-edge pixel
        self.assertEqual(st.min_dn, 144)
        self.assertEqual(st.floor_min_dn, 144)       # the top one stays
        self.raw[4, 20] = 600
        st = R.measure({"R": self.raw}, self.region())["R"]
        self.assertEqual(st.n_below_floor, 0)
        self.assertEqual(st.min_dn, 144)
        self.assertEqual(st.floor_min_dn, 464)
        self.assertIsNone(R.bus_exposure({"R": st}, "photo_red", 30_000)[0])

    def test_no_band_1_darkening_leaves_nothing_out(self):
        self.raw[:, 6] = self.raw[:, 33] = 64 + 700
        self.assertFalse(R.edge_left_out(self.raw, self.region()).any())

    def test_no_fitted_pad_leaves_nothing_out(self):
        self.assertFalse(
            R.edge_left_out(self.raw, self.region(dist=None)).any())

    def test_coverage_skips_it(self):
        self.raw[4, 20] = 600
        c = R.coverage({"R": self.raw}, None, self.region(), ["R"])["R"]
        self.assertEqual(c["n_below_floor_both"], 0)

class TestRuleSettings(unittest.TestCase):
    """The settings object every function takes (stage 4a)."""

    def test_defaults_are_the_module_constants(self):
        d = R.RuleSettings()
        self.assertEqual(d, R.DEFAULTS)
        self.assertEqual((d.clip_top_dn, d.clip_bottom_dn, d.noise_floor_dn),
                         (R.CLIP_TOP_DN, R.CLIP_BOTTOM_DN, R.NOISE_FLOOR_DN))
        self.assertEqual(d.multiple_exposure_count, 1)
        self.assertEqual(d.analysis_channels, R.ANALYSIS_CHANNELS)

    def test_channels_may_be_given_as_text(self):
        s = R.RuleSettings(analysis_channels=" violet.B, photo_red.R ,")
        self.assertEqual(s.analysis_channels, ("violet.B", "photo_red.R"))

    def test_nonsense_is_refused_naming_the_row(self):
        for kwargs, row in (
                ({"noise_floor_dn": 1100}, "capture.noise_floor_dn"),
                ({"top_floor_fraction": 0.9}, "capture.top_floor_fraction"),
                ({"clip_step_factor": 1.2}, "capture.clip_step_factor"),
                ({"multiple_exposure_count": 0},
                 "protocol.multiple_exposure_count"),
                ({"bus_margin": 0.5}, "capture.bus_margin"),
                ({"analysis_channels": "violet.Q"},
                 "protocol.analysis_channels")):
            with self.subTest(**kwargs), self.assertRaises(ValueError) as cm:
                R.RuleSettings(**kwargs)
            self.assertIn(row, str(cm.exception))

    def test_to_dict_is_plain(self):
        d = R.DEFAULTS.to_dict()
        self.assertIsInstance(d["analysis_channels"], list)
        self.assertEqual(d["noise_floor_dn"], 164)

    def test_a_raised_floor_counts_more_pixels_below_it(self):
        a = layer(500, at={(3, 3): 300})
        st = R.measure({"R": a}, region())["R"]
        self.assertEqual(st.n_below_floor, 0)
        high = R.RuleSettings(noise_floor_dn=564)
        st = R.measure({"R": a}, region(), high)["R"]
        self.assertEqual(st.n_below_floor, 44)            # every pixel

    def test_judge_follows_its_settings(self):
        st = R.measure({"R": layer(300, at={(3, 3): 600})}, region())
        self.assertEqual(R.judge(st, ["R"], 10_000)[0], R.DIM)
        easy = R.RuleSettings(top_floor_fraction=0.5, top_target_fraction=0.6)
        self.assertEqual(R.judge(st, ["R"], 10_000, easy)[0], R.ACCEPT)
        capped = R.RuleSettings(exposure_cap_us=12_000)
        self.assertEqual(R.judge(st, ["R"], 10_000, capped)[1], 12_000)

    def test_the_analysis_channels_setting_decides_the_layers(self):
        names = ["R", "Gr", "Gb", "B"]
        s = R.RuleSettings(analysis_channels=("amber.R",))
        self.assertEqual(R.decision_layers("amber", names, s), ["R"])
        self.assertEqual(R.decision_layers("photo_red", names, s), names)
        st = R.measure({"R": layer(400, at={(5, 5): 100})}, region())
        self.assertIsNotNone(R.bus_exposure(st, "amber", 8760, s)[0])


if __name__ == "__main__":
    unittest.main()
