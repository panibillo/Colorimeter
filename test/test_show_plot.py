#!/usr/bin/env python3
"""
Tests for show_plot (src/show_plot.py).

Run from the project root:
    python test/run_tests.py test_show_plot
"""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import show_plot

SIZE = (300, 100)
RED = (255, 0, 0)


def colors(img):
    return {c for _, c in img.getcolors(maxcolors=1 << 20)}


class TestRender(unittest.TestCase):

    def test_is_exactly_the_requested_size(self):
        img = show_plot.render([(RED, np.linspace(0, 900, 50))], SIZE, 100, 850)
        self.assertEqual(img.size, SIZE)

    def test_draws_the_curve_and_the_window_limits(self):
        img = show_plot.render([(RED, np.linspace(0, 900, 50))], SIZE, 100, 850)
        self.assertIn(RED, colors(img))
        self.assertIn((200, 0, 200), colors(img))

    def test_no_curves_still_draws_the_frame(self):
        img = show_plot.render([], SIZE, 100, 850)
        self.assertNotIn(RED, colors(img))
        self.assertGreater(len(colors(img)), 1)

    def test_a_brighter_curve_is_drawn_higher(self):
        dim = show_plot.render([(RED, np.full(40, 200.0))], SIZE, 100, 850)
        bright = show_plot.render([(RED, np.full(40, 800.0))], SIZE, 100, 850)

        def row(img):
            px = img.load()
            return next(y for y in range(SIZE[1]) if px[150, y] == RED)

        self.assertLess(row(bright), row(dim))

    def test_values_off_the_scale_are_clipped_not_fatal(self):
        img = show_plot.render([(RED, np.array([-50.0, 2000.0]))], SIZE, 100, 850)
        self.assertEqual(img.size, SIZE)

    def test_an_empty_curve_is_skipped(self):
        show_plot.render([(RED, np.array([]))], SIZE, 100, 850)


if __name__ == "__main__":
    unittest.main()
