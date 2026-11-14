#!/usr/bin/env python3
"""
Unit tests for padfind (src/padfind.py), on synthetic cassette images.

Each test draws a cassette window with a pad of known position, blurs it a
little as the lens does, and checks the fit against the truth.  The real
archives are checked against the desktop finder by
desktop/padanalysis/padfind_parity.py, not here.

Run from the project root:
    python test/run_tests.py test_padfind
"""

import math
import sys
import unittest
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import padfind                                   # noqa: E402
from rect import Rect                            # noqa: E402

# The synthetic scene, in layer px of a 100-column x 90-row crop.
#   frame  (field 0.1): everything outside the window
#   window (field 1.6, white paper): rows WIN_TOP..WIN_BOT, cols 20..80
#   bands  (field 0.2): beside the pad, inside the window
#   pad    (field 1.2): PAD_LEFT..PAD_LEFT+WIDTH, PAD_TOP..PAD_TOP+HEIGHT
#   gray chips (field 1.0): two 6 x 8 patches in the frame
ROWS, COLS = 90, 100
WIN_TOP, WIN_BOT = 8, 76
PAD_LEFT, PAD_TOP = 29.3, 12.4
DARK_DN = 64.0        # dark level added to the raw layer
CHIP_DN = 300.0       # gray-chip signal: field 1.0 -> 300 DN above dark
GRAY1 = Rect(2, 30, 6, 8, name="gray1")
GRAY2 = Rect(92, 30, 6, 8, name="gray2")
SAMPLE = Rect(30, 15, 40, 50, name="sample")      # middle column 50


def _box(rows, cols, x0, x1, y0, y1, sub=10):
    """
    Fraction of each pixel inside the box x0 <= x < x1, y0 <= y < y1.

    Pixel (r, c) is centered on (r, c): it covers columns c-0.5..c+0.5 and
    rows r-0.5..r+0.5.  That is padfind's (and the desktop's) convention,
    where a pixel's index is its center.  Drawn on a grid `sub` times finer
    and averaged, so edges at fractions of a pixel come out as partly
    covered pixels.
    """
    ys = (np.arange(rows * sub) + 0.5) / sub - 0.5
    xs = (np.arange(cols * sub) + 0.5) / sub - 0.5
    inside = (((ys >= y0) & (ys < y1))[:, None]
              & ((xs >= x0) & (xs < x1))[None, :])
    return inside.reshape(rows, sub, cols, sub).mean(axis=(1, 3))


def _blur(img, sigma=0.8):
    """Gaussian blur along both axes, numpy only (the lens's softness)."""
    radius = 3
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    k /= k.sum()
    pad = np.pad(img, radius, mode="edge")
    rows = np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 1, pad)
    return np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 0, rows)


def scene(dx=0.0, dy=0.0, open_channel=False):
    """
    The raw R layer (DN) of a synthetic cassette frame.

    Args:
        dx, dy: Move the pad right / down by this many layer px.
        open_channel: Make the bottom rows bright paper, as in the old
            open-channel holder, which padfind must refuse.

    Returns:
        (raw layer, true (left, right, top, bottom) of the pad).
    """
    left, top = PAD_LEFT + dx, PAD_TOP + dy
    right, bot = left + padfind.WIDTH_FIXED, top + padfind.HEIGHT_FIXED
    win_bot = ROWS if open_channel else WIN_BOT
    window = _box(ROWS, COLS, 20, 80, WIN_TOP, win_bot)
    pad_rows = _box(ROWS, COLS, 0, COLS, top, bot)      # the pad's rows
    pad = _box(ROWS, COLS, left, right, top, bot)
    f = 0.1 + 1.5 * window                              # frame or paper
    f = f - 1.4 * window * pad_rows                     # bands: 0.2
    f = f + 1.0 * pad                                   # pad: 1.2
    # No bottom shadow is drawn.  padfind places the bottom at the steepest
    # rise from pad to paper; a drawn shadow would move that point by an
    # amount set by how the shadow is drawn, which tests the drawing, not
    # the finder.  Real frames are checked by padfind_parity.py.
    f = _blur(f)
    for chip in (GRAY1, GRAY2):
        f[chip.y:chip.y + chip.h, chip.x:chip.x + chip.w] = 1.0
    return DARK_DN + CHIP_DN * f, np.array([left, right, top, bot])


def fitted_edges(fit):
    """(left, right, top, bottom), the sides at the pad's middle row."""
    y = 0.5 * (fit.top + fit.bot)
    return np.array([fit.aL + fit.m * (y - fit.yc),
                     fit.aR + fit.m * (y - fit.yc), fit.top, fit.bot])


RECTS = {"sample": SAMPLE, "gray1": GRAY1, "gray2": GRAY2}


class TestPadFind(unittest.TestCase):

    def test_pre_image_edges(self):
        """A pre-image fit finds every edge within 0.15 px of the truth."""
        raw, truth = scene()
        before, after = padfind.find_pad(raw, DARK_DN, RECTS, "pre")
        np.testing.assert_allclose(fitted_edges(before), truth, atol=0.15)
        self.assertEqual(before.side_rule, "guided")
        self.assertEqual(before.qc(), "")
        self.assertLess(abs(before.angle_deg), 0.1)

    def test_offset_added(self):
        """The fit after the offset is the fit before it, moved by it."""
        raw, _ = scene()
        before, after = padfind.find_pad(raw, DARK_DN, RECTS, "pre")
        moved = fitted_edges(after) - fitted_edges(before)
        np.testing.assert_allclose(moved, padfind.OFFSET_PRE, atol=1e-9)
        self.assertEqual(after.offset, padfind.OFFSET_PRE)
        _, post = padfind.find_pad(raw, DARK_DN, RECTS, "post",
                                   prior=before)
        self.assertEqual(post.offset, padfind.OFFSET_POST)

    def test_post_image_follows_moved_pad(self):
        """A post-image guided by the pre-image finds a pad moved 1 px."""
        raw_pre, _ = scene()
        pre_before, _ = padfind.find_pad(raw_pre, DARK_DN, RECTS, "pre")
        raw_post, truth = scene(dx=1.0, dy=0.6)
        post_before, _ = padfind.find_pad(raw_post, DARK_DN, RECTS, "post",
                                          prior=pre_before)
        np.testing.assert_allclose(fitted_edges(post_before), truth,
                                   atol=0.15)

    def test_mask(self):
        """The mask covers the pad's area and shrinks when eroded."""
        raw, _ = scene()
        before, _ = padfind.find_pad(raw, DARK_DN, RECTS, "pre")
        area = before.mask().sum()
        expected = padfind.WIDTH_FIXED * padfind.HEIGHT_FIXED
        self.assertLess(abs(area - expected) / expected, 0.05)
        self.assertLess(before.mask(erode=3).sum(), area)

    def test_open_channel_refused(self):
        """A crop whose bottom rows are bright is not a cassette: refused."""
        raw, _ = scene(open_channel=True)
        with self.assertRaises(padfind.PadFindError):
            padfind.find_pad(raw, DARK_DN, RECTS, "pre")

    def test_dark_chips_refused(self):
        """Gray chips too dark to divide by raise PadFindError."""
        raw = np.full((ROWS, COLS), DARK_DN + 10.0)
        with self.assertRaises(padfind.PadFindError):
            padfind.find_pad(raw, DARK_DN, RECTS, "pre")

    def test_bad_section(self):
        raw, _ = scene()
        with self.assertRaises(ValueError):
            padfind.find_pad(raw, DARK_DN, RECTS, "middle")

    def test_attrs_round_trip(self):
        """to_attrs / from_attrs rebuild an identical fit."""
        raw, _ = scene()
        _, after = padfind.find_pad(raw, DARK_DN, RECTS, "pre")
        again = padfind.PadFit.from_attrs(after.to_attrs())
        self.assertEqual(again, after)
        self.assertTrue(math.isfinite(again.width))


if __name__ == "__main__":
    unittest.main()
