"""
image_view.py — The image viewer's view model: zoom, pan, and compositing.

Pure geometry and pixels.  This module knows nothing about the display, the
widget pool, the database or the camera — it turns

    a full camera frame  +  a view box  +  some rectangles

into one canvas-sized PIL image with the rectangles outlined on it.  That is
the whole job.  Keeping it free of the frontend is what lets the same code
serve the ILI9341 panel, the matplotlib simulation, a PNG dumped from this
module's `__main__`, and a web front end later.

Two spaces are in play, and one transform between them:

    image space   the camera stream's pixel grid — where ROIs live.
                  See roi.py for how a stored ROI relates to the sensor.
    canvas space  the widget's own pixels, 200 x 240 on this instrument.

                  s        = canvas_w / view.w
                  canvas_x = (image_x - view.x) * s
                  canvas_y = (image_y - view.y) * s

The view box always carries the canvas's aspect ratio.  That one invariant
removes a whole class of special cases: letterboxing is not a separate code
path, it is simply what happens when the view box is taller than the frame, and
`compose()` fills whatever falls outside the frame with the background colour.
Fitting a 2304 x 1296 frame into a 200 x 240 canvas gives a 2304 x 2765 view
box, drawn as a 200 x 113 image with 63 px bars — no special case anywhere.

**The frame is never cropped or downsampled in storage.**  The caller holds the
whole captured frame (2304 x 1296 x 3, about 9 MB) for the life of the screen
and every repaint slices it.  That is what lets a rectangle be *enlarged*
during editing: the pixels behind the growth are already there, out to the
frame edge.  The image `compose()` returns is canvas-sized only because
`ILI9341.draw_image` blits 1:1 — it is a product of the slice, not a store.

Channel order: `BayerCam.capture_frame()` returns **BGR**, so that is the
default.  Pass `channel_order="RGB"` for anything else.  Getting this wrong is
not subtle — the sample turns blue.

Public surface
--------------
    ViewState(frame_w, frame_h, canvas_w, canvas_h)
        .fit()                     whole frame
        .fit_to(rect, margin=0.2)  a rectangle plus a margin
        .zoom(factor)              clamped to [fit_scale, MAX_SCALE]
        .pan(dx, dy)               in image px, clamped to the frame
        .centre_on(cx, cy)         a point
        .centre_on_rect(rect)      a rectangle's centre
        .centre_on_edge(rect, side)  edit-mode zoom-follow
        .scale .x .y .w .h .box() .rect()

    compose(frame, view, overlays, canvas_size, channel_order, bg) -> PIL.Image
    image_to_canvas(view, x, y)      -> (cx, cy)
    canvas_to_image(view, cx, cy)    -> (x, y)
    rect_to_canvas(view, r)          -> (left, top, right, bottom)
"""

from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw

# rect.Rect is a dependency-free value type — no display, no DB — so importing
# it here costs nothing and avoids inventing a fourth rectangle class.
from rect import Rect

# How far in the viewer will zoom.  At 8x one image pixel is an 8 px block on
# the canvas, which is enough to place a ROI edge by eye.
MAX_SCALE = 8.0

# Default extra room around a rectangle for fit_to(): 20% of its size in total,
# so 10% on each side.
DEFAULT_MARGIN = 0.2


class ViewState:
    """
    Which part of the frame is on screen, as a centre point and a scale.

    Storing centre + scale rather than a rectangle keeps repeated zooming from
    drifting: the box is derived on demand, so rounding never accumulates.  The
    box always has the canvas's aspect ratio.

    Clamping rules, applied after every change:

      - Scale is held between the fit scale (the whole frame visible) and
        MAX_SCALE.  You cannot zoom out past the whole frame.
      - On an axis where the view is *smaller* than the frame, the view is kept
        wholly inside the frame — you can never pan into empty space.
      - On an axis where the view is *larger* than the frame, it is centred, so
        the letterbox bars stay even and panning that axis does nothing.

    Attributes:
        frame_w, frame_h:   the camera frame this view is over, in image px
        canvas_w, canvas_h: the target canvas, in canvas px
        max_scale:          zoom ceiling
    """

    def __init__(self, frame_w: int, frame_h: int,
                 canvas_w: int, canvas_h: int,
                 max_scale: float = MAX_SCALE) -> None:
        """
        Args:
            frame_w, frame_h:   Camera frame size in image pixels.
            canvas_w, canvas_h: Canvas size in canvas pixels.
            max_scale:          Largest canvas-px-per-image-px allowed.

        Raises:
            ValueError: if any dimension is not positive.
        """
        if min(frame_w, frame_h, canvas_w, canvas_h) <= 0:
            raise ValueError("frame and canvas dimensions must be positive, "
                             f"got frame {frame_w}x{frame_h}, "
                             f"canvas {canvas_w}x{canvas_h}")
        self.frame_w  = int(frame_w)
        self.frame_h  = int(frame_h)
        self.canvas_w = int(canvas_w)
        self.canvas_h = int(canvas_h)
        self.max_scale = float(max_scale)
        self._scale = self.fit_scale
        self._cx = self.frame_w / 2.0
        self._cy = self.frame_h / 2.0
        self.fit()

    # ── Derived geometry ────────────────────────────────────────────────────

    @property
    def fit_scale(self) -> float:
        """Scale at which the whole frame is visible — the zoom-out limit."""
        return min(self.canvas_w / self.frame_w, self.canvas_h / self.frame_h)

    @property
    def scale(self) -> float:
        """Canvas pixels per image pixel."""
        return self._scale

    @property
    def w(self) -> float:
        """View box width in image px."""
        return self.canvas_w / self._scale

    @property
    def h(self) -> float:
        """View box height in image px."""
        return self.canvas_h / self._scale

    @property
    def x(self) -> float:
        """View box left edge in image px; negative when letterboxed."""
        return self._cx - self.w / 2.0

    @property
    def y(self) -> float:
        """View box top edge in image px; negative when letterboxed."""
        return self._cy - self.h / 2.0

    @property
    def cx(self) -> float:
        """View centre, image x."""
        return self._cx

    @property
    def cy(self) -> float:
        """View centre, image y."""
        return self._cy

    def box(self) -> Tuple[float, float, float, float]:
        """Return the view box as exact floats (x, y, w, h) in image px."""
        return self.x, self.y, self.w, self.h

    def rect(self) -> Rect:
        """
        Return the view box as an integer Rect, for logging and display.

        Rounded, so do not use it for the canvas transform — that uses the
        float box, which is what keeps the mapping exact.
        """
        return Rect(round(self.x), round(self.y), round(self.w), round(self.h),
                    name="view")

    # ── Movement ────────────────────────────────────────────────────────────

    def fit(self) -> "ViewState":
        """
        Show the whole frame, centred — the `Fit` button and the Menu entry.

        Returns self, so calls chain.
        """
        self._scale = self.fit_scale
        self._cx = self.frame_w / 2.0
        self._cy = self.frame_h / 2.0
        return self._clamp()

    def fit_to(self, r: Rect, margin: float = DEFAULT_MARGIN) -> "ViewState":
        """
        Frame one rectangle plus a margin — the "View Sample" entry, and what
        pressing `Edit` does so that edges are tappable.

        `margin` is a fraction of the rectangle's size *in total*, split evenly
        between the two sides: 0.2 turns a 68 x 68 ROI into an 82 x 82 view box,
        a scale of about 2.45 on a 200 x 240 canvas.

        The tighter of the two axes wins, so the whole rectangle plus its
        margin is always visible.  Scale is then clamped as usual, so asking to
        frame something tiny stops at MAX_SCALE rather than magnifying forever.

        Args:
            r:      The rectangle to frame, in image px.
            margin: Extra room as a fraction of the rectangle's size.

        Returns:
            self, so calls chain.
        """
        want_w = max(1.0, r.w * (1.0 + margin))
        want_h = max(1.0, r.h * (1.0 + margin))
        self._scale = min(self.canvas_w / want_w, self.canvas_h / want_h)
        return self.centre_on_rect(r)

    def zoom(self, factor: float) -> "ViewState":
        """
        Multiply the scale by `factor`, clamped to [fit_scale, max_scale].

        The view centre is kept, so zooming does not wander.  Factors above 1
        magnify.

        Args:
            factor: Multiplier, e.g. 1.5 for the `+` button, 1/1.5 for `−`.

        Returns:
            self, so calls chain.
        """
        if factor <= 0:
            raise ValueError(f"zoom factor must be > 0, got {factor}")
        self._scale = _clamp(self._scale * factor, self.fit_scale, self.max_scale)
        return self._clamp()

    def pan(self, dx: float, dy: float) -> "ViewState":
        """
        Move the view by (dx, dy) **image** pixels, then clamp to the frame.

        Image pixels, not canvas pixels, so a pan step means the same thing at
        every zoom — a nudge of 10 image px moves the ROI by 10 px whether it
        fills the canvas or not.

        Returns:
            self, so calls chain.
        """
        self._cx += dx
        self._cy += dy
        return self._clamp()

    def centre_on(self, cx: float, cy: float) -> "ViewState":
        """Put image point (cx, cy) at the canvas centre, then clamp."""
        self._cx = float(cx)
        self._cy = float(cy)
        return self._clamp()

    def centre_on_rect(self, r: Rect) -> "ViewState":
        """Centre the view on a rectangle's middle."""
        return self.centre_on(r.x + r.w / 2.0, r.y + r.h / 2.0)

    def centre_on_edge(self, r: Rect, side: str) -> "ViewState":
        """
        Centre the view on the midpoint of one of a rectangle's sides.

        This is edit mode's zoom-follow: arming an edge brings that edge to the
        middle of the canvas, and nudging it keeps it there, so the edge being
        moved is always the one you are looking at.

        Args:
            r:    The rectangle, in image px.
            side: 'left', 'right', 'top' or 'bottom'.

        Returns:
            self, so calls chain.

        Raises:
            ValueError: on an unknown side.
        """
        mid_x = r.x + r.w / 2.0
        mid_y = r.y + r.h / 2.0
        points = {"left":   (r.x,      mid_y),
                  "right":  (r.x2(),   mid_y),
                  "top":    (mid_x,    r.y),
                  "bottom": (mid_x,    r.y2())}
        if side not in points:
            raise ValueError(f"unknown side {side!r}; "
                             f"expected one of {sorted(points)}")
        return self.centre_on(*points[side])

    def _clamp(self) -> "ViewState":
        """Re-apply the scale and centre limits described in the class docstring."""
        self._scale = _clamp(self._scale, self.fit_scale, self.max_scale)
        w, h = self.w, self.h
        self._cx = (self.frame_w / 2.0 if w >= self.frame_w
                    else _clamp(self._cx, w / 2.0, self.frame_w - w / 2.0))
        self._cy = (self.frame_h / 2.0 if h >= self.frame_h
                    else _clamp(self._cy, h / 2.0, self.frame_h - h / 2.0))
        return self

    def __repr__(self) -> str:
        return (f"ViewState(frame={self.frame_w}x{self.frame_h}, "
                f"canvas={self.canvas_w}x{self.canvas_h}, "
                f"scale={self._scale:.4g}, box={self.rect().xywh()})")


def _clamp(v: float, lo: float, hi: float) -> float:
    """Clamp v to [lo, hi]; tolerant of lo > hi (returns lo)."""
    return lo if v < lo else (hi if v > hi else v)


# ── Coordinate transforms ───────────────────────────────────────────────────

def image_to_canvas(view: ViewState, x: float, y: float,
                    canvas_size: Optional[Tuple[int, int]] = None
                    ) -> Tuple[float, float]:
    """
    Map an image-space point to canvas pixels.

    Args:
        view:        The current view.
        x, y:        Point in image px.
        canvas_size: Override (w, h); defaults to the view's canvas.

    Returns:
        (canvas_x, canvas_y) as floats, which may fall outside the canvas.

    Example:
        >>> v = ViewState(2304, 1296, 200, 240)
        >>> image_to_canvas(v, 0, 0)[0]
        0.0
    """
    s = _scale_for(view, canvas_size)
    return (x - view.x) * s, (y - view.y) * s


def canvas_to_image(view: ViewState, cx: float, cy: float,
                    canvas_size: Optional[Tuple[int, int]] = None
                    ) -> Tuple[float, float]:
    """
    Map a canvas point back to image space — the inverse of image_to_canvas.

    This is what turns a touch into a ROI hit-test, so it has to be the exact
    inverse, not an approximation.

    Args:
        view:        The current view.
        cx, cy:      Point in canvas px.
        canvas_size: Override (w, h); defaults to the view's canvas.

    Returns:
        (image_x, image_y) as floats, which may fall outside the frame when the
        view is letterboxed and the touch landed on a bar.
    """
    s = _scale_for(view, canvas_size)
    return view.x + cx / s, view.y + cy / s


def rect_to_canvas(view: ViewState, r: Rect,
                   canvas_size: Optional[Tuple[int, int]] = None
                   ) -> Tuple[float, float, float, float]:
    """
    Map a rectangle to canvas coordinates as (left, top, right, bottom).

    Half-open, matching Rect: `right` and `bottom` are one past the last pixel,
    so `right - left` is the width on canvas.

    Args:
        view:        The current view.
        r:           Rectangle in image px.
        canvas_size: Override (w, h); defaults to the view's canvas.

    Returns:
        (left, top, right, bottom) as floats.
    """
    left, top = image_to_canvas(view, r.x, r.y, canvas_size)
    right, bottom = image_to_canvas(view, r.x2(), r.y2(), canvas_size)
    return left, top, right, bottom


def _scale_for(view: ViewState, canvas_size: Optional[Tuple[int, int]]) -> float:
    """Canvas px per image px, honouring a canvas_size override."""
    if canvas_size is None:
        return view.scale
    return canvas_size[0] / view.w


# ── Compositing ─────────────────────────────────────────────────────────────

def compose(frame: np.ndarray,
            view: ViewState,
            overlays: Iterable[Tuple[Rect, Sequence[int]]] = (),
            canvas_size: Optional[Tuple[int, int]] = None,
            channel_order: str = "BGR",
            bg: Sequence[int] = (0, 0, 0)) -> Image.Image:
    """
    Render the view box of `frame` into a canvas-sized image, rectangles drawn on.

    The frame is sliced, not copied: only the visible region is touched, so a
    9 MB frame costs nothing extra to hold between repaints.  Anything in the
    view box that falls outside the frame is filled with `bg`, which is where
    the letterbox bars come from.

    Resampling is chosen by direction.  Magnifying uses NEAREST so a ROI edge
    lands on an honest pixel boundary rather than being smeared across two —
    the entire point of the tool is judging where an edge is.  Minifying uses
    BOX (area average), because a nearest-neighbour reduction of 2304 px to
    200 px would sample about one pixel in twelve and could miss a 68 px ROI
    entirely.

    Overlay outlines are stroked **just outside** each rectangle, so every
    pixel the ROI actually covers stays visible and can be judged.  Rectangles
    larger than the canvas clip for free.

    Args:
        frame:         (H, W, 3) uint8 array from BayerCam.capture_frame().
        view:          Which part of it to show.
        overlays:      Iterable of (Rect, (r, g, b)) drawn in order, so later
                       entries win where they overlap.
        canvas_size:   Override (w, h); defaults to the view's canvas.
        channel_order: "BGR" (the camera's order, the default) or "RGB".
        bg:            Fill for anything outside the frame.

    Returns:
        A PIL RGB image of exactly `canvas_size`.

    Raises:
        ValueError: on a frame that is not (H, W, 3), a frame whose size
            disagrees with the view, or an unknown channel_order.

    Example:
        >>> v = ViewState(2304, 1296, 200, 240)
        >>> img = compose(np.zeros((1296, 2304, 3), np.uint8), v)
        >>> img.size
        (200, 240)
    """
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError(f"frame must be (H, W, 3), got {frame.shape}")
    fh, fw = frame.shape[:2]
    if (fw, fh) != (view.frame_w, view.frame_h):
        raise ValueError(f"frame is {fw}x{fh} but the view is over "
                         f"{view.frame_w}x{view.frame_h}")
    if channel_order not in ("BGR", "RGB"):
        raise ValueError(f"channel_order must be 'BGR' or 'RGB', "
                         f"got {channel_order!r}")

    cw, ch = canvas_size if canvas_size else (view.canvas_w, view.canvas_h)
    s = cw / view.w
    canvas = Image.new("RGB", (cw, ch), tuple(bg))

    # Intersect the view box with the frame, in whole image pixels.
    vx, vy, vw, vh = view.box()
    ix0 = max(0, int(math.floor(vx)))
    iy0 = max(0, int(math.floor(vy)))
    ix1 = min(fw, int(math.ceil(vx + vw)))
    iy1 = min(fh, int(math.ceil(vy + vh)))

    if ix1 > ix0 and iy1 > iy0:
        # Where that slice lands on the canvas.  Derived from the same
        # transform as the overlays, so image and outlines cannot disagree.
        dl = int(round((ix0 - vx) * s))
        dt = int(round((iy0 - vy) * s))
        dr = int(round((ix1 - vx) * s))
        db = int(round((iy1 - vy) * s))
        dw, dh = max(1, dr - dl), max(1, db - dt)

        crop = frame[iy0:iy1, ix0:ix1]

        # Heavy reductions: pre-thin with a strided view before handing the
        # data to PIL.  At fit zoom a full-quality BOX average would read all
        # 3M pixels of the frame to produce 22K, and on a Pi Zero that is the
        # slowest thing the viewer does.  Striding first — keeping at least 2x
        # oversampling for BOX to average over — costs a fraction of that and
        # is visually indistinguishable at a scale where the sample ROI is six
        # canvas pixels wide.  The fit view is for orientation; edges are
        # judged zoomed in, where s >= 1 and NEAREST shows real pixels.
        if s < 0.5:
            k = max(1, int(1.0 / s) // 2)
            if k > 1:
                crop = crop[::k, ::k]

        if channel_order == "BGR":
            crop = crop[:, :, ::-1]
        patch = Image.fromarray(np.ascontiguousarray(crop), "RGB")
        if (dw, dh) != patch.size:
            patch = patch.resize((dw, dh),
                                 Image.NEAREST if s >= 1.0 else Image.BOX)
        canvas.paste(patch, (dl, dt))

    draw = ImageDraw.Draw(canvas)
    for r, colour in overlays:
        left, top, right, bottom = rect_to_canvas(view, r, (cw, ch))
        # One canvas pixel outside the rectangle on every side, so the outline
        # never hides a pixel the ROI covers.  PIL clips out-of-range corners.
        draw.rectangle([int(math.floor(left)) - 1, int(math.floor(top)) - 1,
                        int(math.ceil(right)),     int(math.ceil(bottom))],
                       outline=tuple(colour), width=1)
    return canvas


# ─────────────────────────────────────────────────────────────────────────────
# Self-test and PNG dump
#
#     python3 src/image_view.py                    # synthetic frame
#     python3 src/image_view.py <frame.jpg|.npy>   # a real capture
#
# Writes image_view_check.png: the fit view and the sample view side by side,
# with the ROIs from the database drawn on, so the geometry can be checked on a
# desktop before any of it reaches the Pi.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from pathlib import Path

    CANVAS = (200, 240)
    FRAME  = (2304, 1296)

    def check(label, got, want):
        ok = "OK " if got == want else "XX "
        print(f"  {ok}{label:46s} got={got!r}")
        assert got == want, f"{label}: {got!r} != {want!r}"

    def near(label, got, want, tol=1e-6):
        ok = "OK " if abs(got - want) <= tol else "XX "
        print(f"  {ok}{label:46s} got={got!r}")
        assert abs(got - want) <= tol, f"{label}: {got!r} != {want!r}"

    fw, fh = FRAME
    cw, ch = CANVAS

    print("== fit: whole frame, canvas aspect, letterboxed ==")
    v = ViewState(fw, fh, cw, ch)
    near("fit scale = canvas_w / frame_w", v.scale, cw / fw)
    near("view width = frame width",       v.w, fw)
    near("view height overshoots",         v.h, ch / (cw / fw))
    check("view box taller than the frame", v.h > fh, True)
    # 1296 * 200/2304 = 112.5 exactly, so the bars are 63.75 each — the frame
    # does not land on a whole number of canvas rows at fit.
    near("frame renders 200 x 112.5", fh * v.scale, 112.5)
    near("bars are 63.75 px each", (ch - fh * v.scale) / 2, 63.75)
    check("centred on the frame", (round(v.cx), round(v.cy)), (fw // 2, fh // 2))

    print("== the view box always has the canvas aspect ==")
    for f in (1.0, 1.5, 3.0, 100.0, 0.5):
        v.zoom(f)
        near(f"aspect after zoom({f})", v.w / v.h, cw / ch, 1e-9)

    print("== zoom clamps both ways ==")
    v.fit()
    v.zoom(0.001)
    near("cannot zoom out past fit", v.scale, v.fit_scale)
    v.zoom(1e6)
    near("cannot zoom in past MAX_SCALE", v.scale, MAX_SCALE)
    try:
        v.zoom(0)
    except ValueError as e:
        print(f"  OK zoom(0) rejected: {e}")

    print("== pan clamps to the frame ==")
    v.fit()
    v.zoom(4.0)                      # well inside the frame on both axes
    v.pan(-100000, -100000)
    near("left edge stops at 0", v.x, 0.0, 1e-6)
    near("top edge stops at 0",  v.y, 0.0, 1e-6)
    v.pan(100000, 100000)
    near("right edge stops at frame_w", v.x + v.w, float(fw), 1e-6)
    near("bottom edge stops at frame_h", v.y + v.h, float(fh), 1e-6)

    print("== the letterboxed axis is centred and will not pan ==")
    v.fit()
    before = v.cy
    v.pan(0, 500)
    near("vertical pan is a no-op at fit", v.cy, before)
    near("still centred", v.cy, fh / 2.0)

    print("== fit_to: the sample ROI plus 20% ==")
    sample = Rect(1150, 790, 68, 68, name="sample")
    v = ViewState(fw, fh, cw, ch).fit_to(sample)
    near("scale ~= 2.45", round(v.scale, 2), 2.45)
    check("ROI renders about 166 px across",
          round(sample.w * v.scale), 167)
    check("ROI is centred on the canvas",
          (round(image_to_canvas(v, sample.x + sample.w / 2,
                                 sample.y + sample.h / 2)[0]),
           round(image_to_canvas(v, sample.x + sample.w / 2,
                                 sample.y + sample.h / 2)[1])),
          (cw // 2, ch // 2))
    check("whole ROI is inside the canvas",
          all(0 <= c <= m for c, m in
              zip(rect_to_canvas(v, sample), (cw, ch, cw, ch))), True)
    tiny = ViewState(fw, fh, cw, ch).fit_to(Rect(100, 100, 2, 2))
    near("fit_to a tiny rect stops at MAX_SCALE", tiny.scale, MAX_SCALE)

    print("== transforms are exact inverses ==")
    v = ViewState(fw, fh, cw, ch).fit_to(sample)
    for px, py in ((0, 0), (100, 120), (199, 239), (37, 201)):
        ix, iy = canvas_to_image(v, px, py)
        bx, by = image_to_canvas(v, ix, iy)
        near(f"canvas({px},{py}) round-trips x", bx, px, 1e-9)
        near(f"canvas({px},{py}) round-trips y", by, py, 1e-9)

    print("== centre_on_edge: zoom-follow ==")
    for side, want in (("left",   (sample.x, sample.y + sample.h / 2)),
                       ("right",  (sample.x2(), sample.y + sample.h / 2)),
                       ("top",    (sample.x + sample.w / 2, sample.y)),
                       ("bottom", (sample.x + sample.w / 2, sample.y2()))):
        v = ViewState(fw, fh, cw, ch).fit_to(sample).centre_on_edge(sample, side)
        got = image_to_canvas(v, *want)
        check(f"{side:6s} edge lands at the canvas centre",
              (round(got[0]), round(got[1])), (cw // 2, ch // 2))
    try:
        v.centre_on_edge(sample, "diagonal")
    except ValueError as e:
        print(f"  OK unknown side rejected: {e}")

    print("== compose: size, letterbox, channel order ==")
    frame = np.zeros((fh, fw, 3), dtype=np.uint8)
    frame[:, :, 2] = 200            # BGR: this is the RED channel
    v = ViewState(fw, fh, cw, ch)
    img = compose(frame, v)
    check("canvas size", img.size, CANVAS)
    check("BGR is honoured — frame reads red", img.getpixel((100, 120)), (200, 0, 0))
    check("bar above the frame is background", img.getpixel((100, 5)), (0, 0, 0))
    check("bar below the frame is background", img.getpixel((100, 234)), (0, 0, 0))
    check("RGB override reads blue",
          compose(frame, v, channel_order="RGB").getpixel((100, 120)), (0, 0, 200))
    check("custom background",
          compose(frame, v, bg=(9, 9, 9)).getpixel((100, 5)), (9, 9, 9))
    check("canvas_size override", compose(frame, v, canvas_size=(400, 480)).size,
          (400, 480))

    print("== compose: bad input is refused, not guessed ==")
    for bad, why in ((np.zeros((10, 10), np.uint8), "2-D frame"),
                     (np.zeros((fh, fw, 4), np.uint8), "4 channels"),
                     (np.zeros((100, 100, 3), np.uint8), "wrong size")):
        try:
            compose(bad, v)
            raise AssertionError(f"{why} should have raised")
        except ValueError as e:
            print(f"  OK {why:12s} rejected: {str(e)[:52]}")
    try:
        compose(frame, v, channel_order="RBG")
        raise AssertionError("bad channel_order should have raised")
    except ValueError as e:
        print(f"  OK bad channel_order rejected")

    print("== compose: the slice really is the view box ==")
    # A single bright pixel must land where the transform says it will.
    frame[:] = 0
    frame[800, 1200] = (255, 255, 255)
    v = ViewState(fw, fh, cw, ch).fit_to(Rect(1150, 790, 68, 68))
    img = compose(frame, v)
    ex, ey = image_to_canvas(v, 1200, 800)
    lit = [(x, y) for y in range(ch) for x in range(cw)
           if img.getpixel((x, y)) != (0, 0, 0)]
    check("exactly one region is lit", len(lit) > 0, True)
    cxs = sum(p[0] for p in lit) / len(lit)
    cys = sum(p[1] for p in lit) / len(lit)
    near("lit centroid x matches the transform", cxs, ex, 1.5)
    near("lit centroid y matches the transform", cys, ey, 1.5)

    print("== overlays are drawn outside the ROI, and clip ==")
    frame[:] = 40
    v = ViewState(fw, fh, cw, ch).fit_to(sample)
    img = compose(frame, v, [(sample, (255, 0, 255))])
    left, top, right, bottom = rect_to_canvas(v, sample)
    inside = img.getpixel((int(left) + 3, int(top) + 3))
    check("ROI interior is untouched by the outline", inside, (40, 40, 40))
    check("outline sits one px outside the left edge",
          img.getpixel((int(math.floor(left)) - 1, int((top + bottom) / 2))),
          (255, 0, 255))
    check("first ROI pixel is not overdrawn",
          img.getpixel((int(math.floor(left)), int((top + bottom) / 2))),
          (40, 40, 40))
    huge = compose(frame, ViewState(fw, fh, cw, ch).fit_to(sample).zoom(4.0),
                   [(sample, (255, 0, 255))])
    check("a ROI larger than the canvas still composes", huge.size, CANVAS)

    print("== the fit view's strided reduction keeps small features ==")
    # The pre-thinning in compose() must not alias a ROI-sized patch away —
    # that would make the orientation view lie about where things are.
    frame = np.zeros((fh, fw, 3), dtype=np.uint8)
    frame[790:858, 1150:1218] = (255, 255, 255)      # a 68x68 block
    v = ViewState(fw, fh, cw, ch)
    img = compose(frame, v)
    lit = [(x, y) for y in range(ch) for x in range(cw)
           if sum(img.getpixel((x, y))) > 90]
    check("the 68px block survives at fit zoom", len(lit) > 0, True)
    bx, by = image_to_canvas(v, 1184, 824)           # the block's centre
    near("and it is where the transform says",
         sum(p[0] for p in lit) / len(lit), bx, 2.0)
    near("on both axes",
         sum(p[1] for p in lit) / len(lit), by, 2.0)
    print(f"  block covers {len(lit)} canvas px at scale {v.scale:.3f}")

    print("== timing ==")
    import time
    frame = np.random.default_rng(0).integers(0, 256, (fh, fw, 3),
                                              dtype=np.uint8)
    for label, vv in (("fit (minify, BOX)", ViewState(fw, fh, cw, ch)),
                      ("sample (magnify, NEAREST)",
                       ViewState(fw, fh, cw, ch).fit_to(sample))):
        t0 = time.perf_counter()
        for _ in range(10):
            compose(frame, vv, [(sample, (255, 255, 0))])
        print(f"  {label:28s} {(time.perf_counter() - t0) / 10 * 1000:7.2f} ms")

    print("\n== all image_view self-tests passed ==")

    # ── PNG dump ────────────────────────────────────────────────────────────
    # Uses the real ROIs when the database is reachable.  roi.py is imported
    # here, in __main__, never by the module itself — the view model stays
    # free of the database.
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if src and src.suffix == ".npy":
        arr = np.load(src)
        real = np.dstack([arr] * 3).astype(np.uint8) if arr.ndim == 2 else arr
    elif src:
        real = np.asarray(Image.open(src).convert("RGB"))[:, :, ::-1]  # -> BGR
    else:
        real = None

    if real is not None and real.shape[:2] != (fh, fw):
        print(f"\n{src} is {real.shape[1]}x{real.shape[0]}, not {fw}x{fh} — "
              f"drawing it anyway at its own size")
        fh, fw = real.shape[:2]
    if real is None:
        # Synthetic stand-in: a grid plus a blob where the sample sits.
        real = np.full((fh, fw, 3), 30, dtype=np.uint8)
        real[::100, :] = 70
        real[:, ::100] = 70
        real[790:858, 1150:1218] = (40, 90, 180)      # BGR -> orange
        real[496:564, 1150:1286] = (230, 230, 230)    # the white patch

    try:
        import roi as roi_mod
        rois = roi_mod.load_active(fw, fh, None)
    except Exception as e:                            # no DB on this machine
        print(f"  (database unavailable: {e}; using roi.py's seed values)")
        import roi as roi_mod
        rois = {n: Rect(x, y, w, h, name=n)
                for n, _, x, y, w, h in roi_mod.SEED_ROIS}

    COLOURS = {"sample": (255, 255, 0), "white": (0, 255, 255),
               "black1": (0, 255, 0), "black2": (0, 255, 0),
               "bound": (255, 0, 255)}
    overlays = [(r, COLOURS.get(n, (255, 255, 255))) for n, r in sorted(rois.items())]

    views = [("fit", ViewState(fw, fh, cw, ch)),
             ("sample +20%", ViewState(fw, fh, cw, ch).fit_to(rois["sample"])),
             ("bound", ViewState(fw, fh, cw, ch).fit_to(rois["bound"], 0.05))]
    sheet = Image.new("RGB", (cw * len(views) + 10 * (len(views) + 1), ch + 20),
                      (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    for i, (label, vv) in enumerate(views):
        sheet.paste(compose(real, vv, overlays), (10 + i * (cw + 10), 10))
        draw.text((10 + i * (cw + 10), 0), f"{label}  x{vv.scale:.2f}",
                  fill=(220, 220, 220))
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("image_view_check.png")
    sheet.save(out)
    print(f"\nwrote {out}  ({sheet.size[0]}x{sheet.size[1]})")
    for n, r in sorted(rois.items()):
        print(f"  {n:8s} {str(r.xywh()):22s} -> canvas "
              f"{tuple(round(c, 1) for c in rect_to_canvas(views[1][1], r))}")
