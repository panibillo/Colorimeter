#!/usr/bin/env python3
"""
Every widget's rectangle must be a geometry.Rect, the screen type.

The mock display never touches `.right` or `.bottom`, but the real ILI9341
driver does.  A widget built with rect.Rect (the image-space type) therefore
passes on the desktop and crashes on the device the moment it is drawn.

Run from the project root:
    python test/run_tests.py test_pool_rects
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import geometry
import pool


class TestPoolRects(unittest.TestCase):

    def test_every_widget_uses_the_screen_rect_type(self):
        p = pool.Pool.build()
        wrong = [name for name in p.names
                 if not isinstance(p[name].rect, geometry.Rect)]
        self.assertEqual(wrong, [])

    def test_every_widget_fits_on_the_320_by_240_screen(self):
        p = pool.Pool.build()
        off = [name for name in p.names
               if p[name].rect.right > 320 or p[name].rect.bottom > 240]
        self.assertEqual(off, [])


if __name__ == "__main__":
    unittest.main()
