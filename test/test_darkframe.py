#!/usr/bin/env python3
"""
Unit tests for darkframe (src/darkframe.py).

Pure numpy, synthetic data only -- no database, no camera, no matplotlib.
The statistics under test are derived in
tex/dark_signal_characterization.tex §9; see src/darkframe.py's module
docstring for the citations and the fair alternatives considered.

Run from the project root:
    python test/run_tests.py test_darkframe
or directly:
    python test/test_darkframe.py
"""

import sys
import unittest
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import darkframe as df


class TestDarkPixelScoresAndIdentify(unittest.TestCase):
    """The corrected outlier test."""

    def setUp(self):
        self.rng = np.random.default_rng(1)
        self.n_frames, self.h, self.w = 100, 40, 60
        self.clean = 64.0 + self.rng.normal(0, 0.73,
                                            size=(self.n_frames, self.h, self.w))

    def test_rejects_too_few_frames(self):
        with self.assertRaises(ValueError):
            df.dark_pixel_scores(self.clean[:1])
        with self.assertRaises(ValueError):
            df.identify_hot_pixels(self.clean[:1])

    def test_score_shape_matches_one_frame(self):
        z = df.dark_pixel_scores(self.clean)
        self.assertEqual(z.shape, (self.h, self.w))

    def test_clean_population_flags_nothing(self):
        self.assertEqual(df.identify_hot_pixels(self.clean, k=8.0), [])

    def test_injected_hot_pixel_is_found_and_scores_positive(self):
        stack = self.clean.copy()
        stack[:, 10, 20] += 6.0
        found = df.identify_hot_pixels(stack, k=8.0)
        coords = {(y, x): z for y, x, z in found}
        self.assertIn((10, 20), coords)
        self.assertGreater(coords[(10, 20)], 0)

    def test_injected_cold_pixel_is_found_and_scores_negative(self):
        stack = self.clean.copy()
        stack[:, 30, 5] -= 5.5
        found = df.identify_hot_pixels(stack, k=8.0)
        coords = {(y, x): z for y, x, z in found}
        self.assertIn((30, 5), coords)
        self.assertLess(coords[(30, 5)], 0)

    def test_higher_k_flags_fewer_or_equal_pixels(self):
        stack = self.clean.copy()
        stack[:, 10, 20] += 6.0
        loose = df.identify_hot_pixels(stack, k=4.0)
        strict = df.identify_hot_pixels(stack, k=12.0)
        self.assertGreaterEqual(len(loose), len(strict))

    def test_degenerate_zero_spread_population_flags_nothing(self):
        flat = np.full((5, 4, 4), 64.0)
        self.assertTrue(np.array_equal(df.dark_pixel_scores(flat),
                                       np.zeros((4, 4))))
        self.assertEqual(df.identify_hot_pixels(flat, k=0.001), [])


class TestHotPixelMask(unittest.TestCase):

    def test_marks_in_bounds_coordinates(self):
        mask = df.hot_pixel_mask((5, 5), [(1, 1), (4, 4)])
        self.assertTrue(mask[1, 1])
        self.assertTrue(mask[4, 4])
        self.assertEqual(mask.sum(), 2)

    def test_ignores_out_of_bounds_coordinates(self):
        mask = df.hot_pixel_mask((5, 5), [(-1, 0), (100, 100), (2, 2)])
        self.assertEqual(mask.sum(), 1)
        self.assertTrue(mask[2, 2])

    def test_empty_coords_gives_empty_mask(self):
        mask = df.hot_pixel_mask((3, 3), [])
        self.assertFalse(mask.any())


class TestReplaceHotPixels(unittest.TestCase):

    def test_single_hot_pixel_replaced_by_neighbor_median(self):
        img = np.ones((4, 4))
        img[2, 2] = 500.0
        mask = np.zeros_like(img, dtype=bool)
        mask[2, 2] = True
        fixed = df.replace_hot_pixels(img, mask)
        self.assertEqual(fixed[2, 2], 1.0)

    def test_unmasked_pixels_unchanged(self):
        img = np.arange(16, dtype=float).reshape(4, 4)
        mask = np.zeros_like(img, dtype=bool)
        mask[1, 1] = True
        fixed = df.replace_hot_pixels(img, mask)
        self.assertTrue(np.array_equal(fixed[~mask], img[~mask]))

    def test_input_array_not_mutated(self):
        img = np.ones((3, 3))
        img[1, 1] = 500.0
        mask = np.zeros_like(img, dtype=bool)
        mask[1, 1] = True
        df.replace_hot_pixels(img, mask)
        self.assertEqual(img[1, 1], 500.0)

    def test_no_masked_pixels_is_a_copy(self):
        img = np.ones((3, 3)) * 7.0
        mask = np.zeros_like(img, dtype=bool)
        fixed = df.replace_hot_pixels(img, mask)
        self.assertTrue(np.array_equal(fixed, img))
        self.assertIsNot(fixed, img)

    def test_adjacent_hot_pixels_each_repair_from_unmasked_neighbors(self):
        img = np.ones((4, 4))
        img[1, 1] = 500.0
        img[1, 2] = 500.0
        mask = np.zeros_like(img, dtype=bool)
        mask[1, 1] = True
        mask[1, 2] = True
        fixed = df.replace_hot_pixels(img, mask)
        self.assertEqual(fixed[1, 1], 1.0)
        self.assertEqual(fixed[1, 2], 1.0)

    def test_all_neighbors_masked_falls_back_to_plain_median(self):
        # A plus-shape of five masked pixels: the center's four orthogonal
        # neighbors are themselves all masked, so its clean-neighbor median
        # is undefined and replace_hot_pixels must fall back to the plain
        # (masked values included) median rather than raise or return NaN --
        # the documented limitation for a hot-pixel cluster this dense,
        # which real defect maps essentially never produce.
        img = np.ones((5, 5))
        for y, x in [(2, 2), (1, 2), (3, 2), (2, 1), (2, 3)]:
            img[y, x] = 500.0
        mask = np.zeros_like(img, dtype=bool)
        for y, x in [(2, 2), (1, 2), (3, 2), (2, 1), (2, 3)]:
            mask[y, x] = True
        fixed = df.replace_hot_pixels(img, mask)
        self.assertFalse(np.isnan(fixed[2, 2]))
        self.assertEqual(fixed[2, 2], 500.0)  # fallback median of 4x 500.0

    def test_single_pixel_image_does_not_crash(self):
        img = np.array([[42.0]])
        mask = np.array([[True]])
        fixed = df.replace_hot_pixels(img, mask)
        self.assertEqual(fixed[0, 0], 42.0)


class TestCorrectDark(unittest.TestCase):

    def test_plain_mean_subtraction_with_no_hot_coords(self):
        lit = np.full((6, 6), 700.0)
        dark = np.full((6, 6), 64.0)
        out = df.correct_dark(lit, dark)
        self.assertTrue(np.allclose(out, 636.0))

    def test_hot_pixel_is_repaired_others_are_plain(self):
        lit = np.full((6, 6), 700.0)
        lit[3, 3] = 900.0
        dark = np.full((6, 6), 64.0)
        out = df.correct_dark(lit, dark, hot_coords=[(3, 3)])
        self.assertAlmostEqual(out[3, 3], 636.0)
        self.assertAlmostEqual(out[0, 0], 636.0)

    def test_dark_frames_own_per_pixel_pattern_is_ignored(self):
        # Decision 1: only dark.mean() matters, not dark's own per-pixel
        # array -- a non-uniform dark frame with the same mean must give the
        # same correction as a uniform one.
        lit = np.full((5, 5), 700.0)
        uniform_dark = np.full((5, 5), 64.0)
        lumpy_dark = uniform_dark.copy()
        lumpy_dark[0, 0] += 10.0
        lumpy_dark[4, 4] -= 10.0
        out_uniform = df.correct_dark(lit, uniform_dark)
        out_lumpy = df.correct_dark(lit, lumpy_dark)
        self.assertTrue(np.allclose(out_uniform, out_lumpy))


if __name__ == "__main__":
    unittest.main(verbosity=2)
