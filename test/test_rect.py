#!/usr/bin/env python3
"""
test_rect.py — Unit tests and visual check for src/rect.py.

Runs unit tests against the Rect class. With --plot, also displays a
matplotlib figure showing several Rect instances inside a 1640×1232
bounding rectangle, saved to test/rect_plot.png.

Usage:
    python test/test_rect.py            # unit tests only
    python test/test_rect.py --plot     # unit tests + plot
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rect import Rect, DEFAULT_COLOR


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestRectInit(unittest.TestCase):
    """Construction and attribute assignment."""

    def test_float_truncated_to_int(self):
        r = Rect(1.9, 2.7, 10.5, 8.9)
        self.assertEqual(r.x, 1)
        self.assertEqual(r.y, 2)
        self.assertEqual(r.w, 10)
        self.assertEqual(r.h, 8)

    def test_defaults(self):
        r = Rect(0, 0, 100, 50)
        self.assertEqual(r.name,  '')
        self.assertEqual(r.color, DEFAULT_COLOR)

    def test_explicit_name_color(self):
        r = Rect(10, 20, 100, 80, name='patch', color='#FF0000')
        self.assertEqual(r.name,  'patch')
        self.assertEqual(r.color, '#FF0000')


class TestRectDerivedGeometry(unittest.TestCase):
    """x2, y2, xywh, xyslice, area."""

    def setUp(self):
        self.r = Rect(10, 20, 100, 80, name='test', color='#00FF00')

    def test_x2(self):
        self.assertEqual(self.r.x2(), 110)

    def test_y2(self):
        self.assertEqual(self.r.y2(), 100)

    def test_xywh(self):
        self.assertEqual(self.r.xywh(), (10, 20, 100, 80))

    def test_xyslice_row_axis_is_y(self):
        """First slice (row axis) must span y to y2."""
        row_sl, col_sl = self.r.xyslice()
        self.assertEqual(row_sl.start, 20)    # y
        self.assertEqual(row_sl.stop,  100)   # y2

    def test_xyslice_col_axis_is_x(self):
        """Second slice (col axis) must span x to x2."""
        row_sl, col_sl = self.r.xyslice()
        self.assertEqual(col_sl.start, 10)    # x
        self.assertEqual(col_sl.stop,  110)   # x2

    def test_area(self):
        self.assertEqual(self.r.area(), 8000)


class TestRectContains(unittest.TestCase):
    """Point containment, half-open boundary convention."""

    def setUp(self):
        self.r = Rect(10, 20, 100, 80)

    def test_interior_point(self):
        self.assertTrue(self.r.contains(50, 60))

    def test_top_left_corner_included(self):
        self.assertTrue(self.r.contains(10, 20))

    def test_right_edge_excluded(self):
        self.assertFalse(self.r.contains(110, 50))

    def test_bottom_edge_excluded(self):
        self.assertFalse(self.r.contains(50, 100))

    def test_point_outside(self):
        self.assertFalse(self.r.contains(0, 0))


class TestRectStrRepr(unittest.TestCase):
    """__str__ and __repr__ output."""

    def setUp(self):
        self.r = Rect(10, 20, 100, 80, name='label', color='#AABBCC')

    def test_str_contains_coords(self):
        s = str(self.r)
        for token in ('name=label', 'x=10', 'y=20', 'w=100', 'h=80'):
            self.assertIn(token, s, msg=f"'{token}' not found in str()")

    def test_str_contains_color(self):
        self.assertIn('#AABBCC', str(self.r))

    def test_repr_is_eval_able(self):
        r2 = eval(repr(self.r))   # Rect is in scope
        self.assertEqual(r2.xywh(),  self.r.xywh())
        self.assertEqual(r2.name,    self.r.name)
        self.assertEqual(r2.color,   self.r.color)


# ---------------------------------------------------------------------------
# Visual plot
# ---------------------------------------------------------------------------

def plot_rects() -> None:
    """
    Display several Rect instances inside a 1640×1232 bounding rectangle.

    Draws:
        - The full camera frame as the outer boundary
        - A 4×3 grid of simulated calibration-card colour patches, each
          labelled with its (row, col) index
        - A centre aperture representing the indicator-strip window
    Saves the figure to test/rect_plot.png.
    """
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    IMG_W, IMG_H = 1640, 1232

    fig, ax = plt.subplots(figsize=(10, 7.5))
    ax.set_xlim(0, IMG_W)
    ax.set_ylim(IMG_H, 0)       # y increases downward (image convention)
    ax.set_aspect('equal')
    ax.set_title("Rect visual check — 1640×1232 image frame")
    ax.set_xlabel("x (pixels)")
    ax.set_ylabel("y (pixels)")

    def draw(rect: Rect, label: str | None = None) -> None:
        patch = mpatches.Rectangle(
            (rect.x, rect.y), rect.w, rect.h,
            linewidth=1.5,
            edgecolor=rect.color,
            facecolor='none',
        )
        ax.add_patch(patch)
        if label:
            ax.text(
                rect.x + rect.w / 2,
                rect.y + rect.h / 2,
                label,
                ha='center', va='center',
                fontsize=7, color=rect.color,
            )

    # Outer image boundary
    draw(Rect(0, 0, IMG_W, IMG_H, color='#666666'), "1640×1232 frame")

    # 4 columns × 3 rows of calibration-card reference patches
    PATCH_W, PATCH_H = 190, 170
    MARGIN_X, MARGIN_Y = 110, 110
    GAP = 20
    patch_colors = [
        '#FF4444', '#FF8800', '#FFFF44', '#88FF00',
        '#44FF88', '#44FFFF', '#4488FF', '#8844FF',
        '#FF44FF', '#FFFFFF', '#888888', '#FF4488',
    ]
    idx = 0
    for row in range(1, 4):
        for col in range(1, 5):
            px = MARGIN_X + (col - 1) * (PATCH_W + GAP)
            py = MARGIN_Y + (row - 1) * (PATCH_H + GAP)
            r = Rect(px, py, PATCH_W, PATCH_H,
                     name=f"r{row}c{col}", color=patch_colors[idx])
            draw(r, f"({row},{col})\n{r.area()//1000}k px")
            idx += 1

    # Centre aperture (indicator-strip window)
    AP_W, AP_H = 260, 260
    aperture = Rect(
        (IMG_W - AP_W) // 2,
        (IMG_H - AP_H) // 2,
        AP_W, AP_H,
        color='#FFFFFF',
    )
    draw(aperture, "aperture")

    # Annotate aperture xyslice
    row_sl, col_sl = aperture.xyslice()
    ax.annotate(
        f"xyslice: [{row_sl.start}:{row_sl.stop}, {col_sl.start}:{col_sl.stop}]",
        xy=(aperture.x + AP_W / 2, aperture.y2() + 12),
        ha='center', fontsize=7, color='#AAAAAA',
    )

    # Dark background to make coloured outlines visible
    fig.patch.set_facecolor('#1a1a1a')
    ax.set_facecolor('#1a1a1a')
    ax.tick_params(colors='#aaaaaa')
    ax.title.set_color('#cccccc')
    ax.xaxis.label.set_color('#aaaaaa')
    ax.yaxis.label.set_color('#aaaaaa')
    for spine in ax.spines.values():
        spine.set_edgecolor('#444444')
    ax.grid(linestyle=':', alpha=0.25, color='#555555')

    plt.tight_layout()
    out = Path(__file__).parent / "rect_plot.png"
    plt.savefig(str(out), dpi=100, bbox_inches='tight',
                facecolor=fig.get_facecolor())
    print(f"Plot saved to {out}")
    plt.show()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plot", action="store_true",
                        help="Display and save a visual plot of Rect instances")
    args, remaining = parser.parse_known_args()

    if args.plot:
        plot_rects()

    # Pass any remaining args (e.g. -v) to unittest
    sys.argv = [sys.argv[0]] + remaining
    unittest.main()
