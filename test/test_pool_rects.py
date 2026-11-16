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



class TestWrapTall(unittest.TestCase):
    """
    pool.wrap_tall: a failure's reason in the tall modal (2026-11-16), so
    the whole reason is on the screen rather than its first 28 characters.
    """

    REASON = ("exposure rule settings: protocol.analysis_channels: not "
              "LED.layer (layer one of R, Gr, Gb, B): red_R")

    def test_the_whole_reason_is_kept(self):
        lines = pool.wrap_tall("Pre-image failed:", self.REASON).splitlines()
        self.assertEqual(lines[0], "Pre-image failed:")
        self.assertEqual(" ".join(lines[1:]), self.REASON)

    def test_a_long_reason_is_cut_to_the_panel(self):
        lines = pool.wrap_tall("Capture failed:", self.REASON * 5).splitlines()
        self.assertEqual(len(lines), pool.TALL_MSG_LINES)
        self.assertTrue(lines[-1].endswith("..."))
        self.assertTrue(all(len(l) <= pool.TALL_MSG_CHARS for l in lines))

    def test_lines_fit_the_message_width_in_the_pi_font(self):
        from PIL import ImageFont
        import layout
        from ili9341 import ILI9341
        path = ILI9341._default_font_path()
        if not path:
            self.skipTest("no TrueType font on this machine")
        font = ImageFont.truetype(path, pool.TALL_MSG_SIZE)
        # Wide letters and the real reason, wrapped as the dialog does.
        text = self.REASON + " WWWW MMMM capture.edge_signal_fraction"
        for line in pool.wrap_tall("Not saved:", text).splitlines():
            self.assertLessEqual(font.getlength(line), layout.MODAL_TALL_MSG.w,
                                 line)

    def test_one_button_tall_dialog_is_centred(self):
        import layout
        p = pool.Pool.build()
        self.assertEqual(p.layout_modal_buttons(1, tall=True), ["modal_yes"])
        self.assertEqual(p["modal_yes"].rect, layout.MODAL_TALL_ONE)


if __name__ == "__main__":
    unittest.main()
