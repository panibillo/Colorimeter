"""dot_study.py -- hand-driven study toward `find_dot`, run from an IDE.

Not `find_dot` itself.  This module holds the looking-at steps that come
before it, one per section of `docs/resume_prompt_find_dot.md`
"Suggested first steps".  Each step makes a figure to be judged by eye;
nothing here decides anything.  The procedural steps are hard-coded in the
`__main__` block at the bottom and are meant to be edited for each run.

Samples: C1028-C1036, a circular exposed dot on the rectangular pad, not
reaching the pad edge.

Coordinates are bound-crop Bayer-layer coordinates, as in `analysis_1`:
130 rows x 183 cols.  x is the column, y the row, origin top-left.

Read-only.  Figures go to `fig/dot_study_<samp_no>_<what>.png` when asked.

Findings and how to read each figure: `docs/dot_study_2026-09-27.md`.
"""
import sys
from pathlib import Path

import numpy as np

SP = Path(__file__).resolve().parent
sys.path.insert(0, str(SP))

import analysis_1 as a1                                     # noqa: E402
from chemistry import cell_name                             # noqa: E402

FIG = SP / "fig"

#: Layer px backed off the pad edge for the white reference and the dot
#: search.  Safe while the dot stays clear of the pad edge (C1028-C1036).
#: Dots that reach the edge will need something else here.
ERODE_WHITE = 4.0

#: Layer px of margin kept around the pad when a figure zooms to it.
ZOOM_MARGIN = 6


# ── data ───────────────────────────────────────────────────────────────

def cell_image(samp_no, LED, Bayerlayer, quantity="R_inf"):
    """
    One cell on the whole bound crop, as a plain float array.

    The data prep for `find_dot`: `analysis_1.get_balanced_cell_data` with
    roi='bound', so the chain is the settled one -- dark level, per-pixel
    gray-chip divisor, R_inf against the strip's own dry pad in the
    pre-image -- and nothing is cut to the pad yet.  Hot pixels and pixels outside the flat-field
    domain come back as NaN.

    Args:
        samp_no: Capture name, e.g. 'C1032'.
        LED, Bayerlayer: The cell, e.g. 'royal_blue', 'B'.
        quantity: 'sig', 'R', 'R_inf', 'A', 'KS' or 'KS_signed'.

    Returns:
        (image, b): the (rows, cols) float array with NaN where masked, and
        the `BalancedCell` it came from (for rung, exposure, R_white).

    Raises:
        RuntimeError: the cell is not stored, has no model, or has no white
            reference (not in the pre-image) so R_inf and after do not
            exist.
    """
    b = a1.get_balanced_cell_data(samp_no, LED, Bayerlayer, roi=a1.ROIBOUND)
    if b is None or getattr(b, quantity) is None:
        raise RuntimeError(f"{samp_no} {cell_name((LED, Bayerlayer))}: "
                           f"no {quantity}")
    return np.ma.filled(getattr(b, quantity).astype(float), np.nan), b


def _zoom(ax, pad):
    """Limit an image axis to the pad's bounding box plus a margin."""
    x, y, w, h = pad.bbox
    ax.set_xlim(x - ZOOM_MARGIN - .5, x + w + ZOOM_MARGIN - .5)
    ax.set_ylim(y + h + ZOOM_MARGIN - .5, y - ZOOM_MARGIN - .5)


def _finish(fig, samp_no, what, figs, savefigs):
    """Save and/or show one figure.  Returns the path written, or None."""
    import matplotlib.pyplot as plt
    out = None
    if savefigs:
        FIG.mkdir(parents=True, exist_ok=True)
        out = FIG / f"dot_study_{samp_no}_{what}.png"
        fig.savefig(out, dpi=120)
        print(f"  wrote {out}")
    if figs:
        plt.show()
    else:
        plt.close(fig)
    return out


# ── step 1: the cell, with the pad drawn on it ─────────────────────────

def show_cell(samp_no, LED="royal_blue", Bayerlayer="B", quantity="R_inf",
              erode=ERODE_WHITE, figs=True, savefigs=False):
    """
    Step 1.  One cell, raw beside the same image with the pad drawn on.

    Left: the cell as `quantity`, zoomed to the pad, nothing drawn.
    Right: the same, with the fitted pad rectangle (solid), the pad eroded
    by `erode` px (dotted) -- the region the white reference and the dot
    search will work inside -- and the hot pixels on the pad (x).

    Judge: is the dot wholly inside the dotted outline, with white pad
    left over around it, top and bottom especially?

    Returns:
        The image array, for poking at in the IDE.
    """
    import matplotlib.pyplot as plt
    img, b = cell_image(samp_no, LED, Bayerlayer, quantity)
    pad = a1.pad_of(samp_no)
    inner = pad.mask(erode)
    hot = a1._hot(samp_no) & pad.mask(0)

    white = np.nanmedian(img[inner])
    print(f"  {samp_no} {cell_name(b.cell)}  rung={b.rung} "
          f"{b.exposure_us}us  R_white={b.R_white:.4f}")
    print(f"  pad {pad.area} px, eroded {erode:g} px -> {int(inner.sum())} px,"
          f" {int(hot.sum())} hot pixel(s) on the pad")
    print(f"  median {quantity} inside eroded pad = {white:.4f}")

    span = {"R": (0, 2.5), "R_inf": (0, 1.4), "A": (-0.2, 0.6),
            "KS": (-0.05, 0.4), "KS_signed": (-0.05, 0.4)}.get(quantity)
    vmin, vmax = span if span else np.nanpercentile(img, [0.5, 99.5])

    fig, axes = plt.subplots(1, 2, figsize=(10, 6), layout="constrained")
    for ax in axes:
        im = ax.imshow(img, cmap="magma", vmin=vmin, vmax=vmax,
                       interpolation="nearest")
        _zoom(ax, pad)
    axes[0].set_title("as measured")

    ax = axes[1]
    xy = pad.corners() + pad.corners()[:1]
    ax.plot([p[0] for p in xy], [p[1] for p in xy], "-", color="cyan",
            lw=1.4, label="fitted pad")
    ax.contour(inner.astype(float), levels=[0.5], colors="cyan",
               linewidths=1.2, linestyles=":")
    ax.plot([], [], ":", color="cyan", label=f"pad eroded {erode:g} px")
    ys, xs = np.nonzero(hot)
    ax.plot(xs, ys, "x", color="lime", ms=6, mew=1.5,
            label=f"hot pixel ({len(xs)})")
    ax.legend(fontsize=8, loc="lower right")
    ax.set_title(f"pad outline, eroded {erode:g} px")

    fig.colorbar(im, ax=axes, shrink=0.8, label=quantity)
    fig.suptitle(f"{samp_no}  {cell_name(b.cell)}  {quantity}   "
                 f"{b.rung} {b.exposure_us}us", fontsize=11)
    _finish(fig, samp_no, f"step1_{cell_name(b.cell)}_{quantity}",
            figs, savefigs)
    return img


# ── step 2: profiles across the pad, and their derivatives ─────────────

#: Rows (or columns) averaged into one profile.  Odd, so the bar has a
#: middle row.
BAR = 5

#: Points in the moving average applied to a profile before it is
#: differentiated.
SMOOTH = 3


def _smooth(p, n):
    """
    n-point moving average, NaN where the window runs off either end.

    Kept aligned with the input: out[i] is the mean of p[i - n//2 .. i + n//2].
    """
    if n <= 1:
        return p.copy()
    out = np.convolve(p, np.ones(n) / n, mode="same")
    h = n // 2
    out[:h] = np.nan
    out[len(out) - h:] = np.nan
    return out


def _peak(d, i):
    """
    Sub-pixel position of the extremum at index i: a parabola through
    d[i-1], d[i], d[i+1].  Falls back to i at an end or on a flat top.
    """
    if i <= 0 or i >= len(d) - 1 or not np.all(np.isfinite(d[i - 1:i + 2])):
        return float(i)
    a, b, c = d[i - 1], d[i], d[i + 1]
    den = a - 2 * b + c
    return float(i) if den == 0 else i + 0.5 * (a - c) / den


def profile(img, inner, axis, centre, bar=BAR, smooth=SMOOTH):
    """
    One profile across the pad, smoothed, and its two derivatives.

    A bar `bar` px wide is laid through `centre`, across the pad: along x
    for axis='x' (a horizontal bar, rows averaged), along y for axis='y'
    (a vertical bar, columns averaged).  Only pixels inside `inner` are
    used, and hot pixels (NaN) are skipped in the average, so a hot pixel
    in the bar leaves its position covered by the other rows.

    The edges proposed here are the extremes of the first derivative: going
    along the bar, the steepest fall and the steepest rise.  For R_inf the
    dot is darker, so the fall is the near edge and the rise the far edge;
    for A or the PCA layers it is the other way round.  Both are simply
    taken and sorted, so the sign of the quantity does not matter.  The
    second derivative crosses zero at those same points, and its own
    extremes on either side of each edge mark where the fade into the dot
    starts and ends.

    Args:
        img: The cell image (NaN where masked).
        inner: Boolean mask of the pad region to work inside.
        axis: 'x' or 'y'.
        centre: (x, y) the bar passes through.
        bar: Bar width, px.
        smooth: Moving-average length, points.

    Returns:
        dict with
          pos    -- positions along the bar (x or y), px
          raw    -- the averaged profile
          sm     -- the smoothed profile
          d1, d2 -- first and second derivatives of `sm`, per px
          edges  -- (e1, e2), the two first-derivative extremes, sub-pixel,
                    in increasing order
          fade   -- per edge, (start, end) of the second derivative's two
                    extremes within 4 px of it -- the width of the fade
          band   -- (lo, hi) the rows (axis 'x') or columns (axis 'y') of
                    the bar
    """
    import warnings
    cx, cy = centre
    h = bar // 2
    work = np.where(inner, img, np.nan)
    if axis == "x":
        c = int(round(cy))
        band = (c - h, c + h)
        strip = work[band[0]:band[1] + 1, :]
    else:
        c = int(round(cx))
        band = (c - h, c + h)
        strip = work[:, band[0]:band[1] + 1].T
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # all-NaN columns
        raw = np.nanmean(strip, axis=0)
    ok = np.nonzero(np.isfinite(raw))[0]
    lo, hi = ok.min(), ok.max() + 1
    pos = np.arange(lo, hi)
    raw = raw[lo:hi]
    sm = _smooth(raw, smooth)
    d1 = np.gradient(sm)
    d2 = np.gradient(d1)

    i_min, i_max = int(np.nanargmin(d1)), int(np.nanargmax(d1))
    edges = sorted((_peak(d1, i_min) + lo, _peak(d1, i_max) + lo))

    fade = []
    for e in edges:
        j = int(round(e)) - lo
        a, b = max(j - 4, 0), min(j + 5, len(d2))
        seg = d2[a:b]
        if not np.any(np.isfinite(seg)):
            fade.append((np.nan, np.nan))
            continue
        k1, k2 = int(np.nanargmin(seg)) + a, int(np.nanargmax(seg)) + a
        fade.append(tuple(sorted((_peak(d2, k1) + lo, _peak(d2, k2) + lo))))

    return dict(pos=pos, raw=raw, sm=sm, d1=d1, d2=d2, edges=tuple(edges),
                fade=fade, band=band)


def show_profiles(samp_no, LED="royal_blue", Bayerlayer="B",
                  quantity="R_inf", erode=ERODE_WHITE, centre=None,
                  bar=BAR, smooth=SMOOTH, figs=True, savefigs=False):
    """
    Step 2.  Horizontal and vertical profiles, both derivatives, the edges.

    Left: the cell, zoomed to the pad, with the two bars shaded, the four
    proposed edges (x1, x2 on the horizontal bar; y1, y2 on the vertical),
    and the circle they imply -- centre at the midpoints, radius the mean
    of the two half-widths.  Middle column: the horizontal profile (raw and
    smoothed), its first derivative, its second.  Right column: the same
    for the vertical profile.  The proposed edges are the solid lines; the
    second derivative's extremes either side of each edge, the dotted.

    Judge: do the solid lines sit on the dot's edge in the image?  Is the
    first derivative's peak clean and single?  How wide is the fade?

    Note: a bar through the pad centre crosses the dot on a chord, not its
    diameter, if the dot is off centre.  That shortens that chord and so
    the radius.  Pass `centre` to move the bars.

    Args:
        centre: (x, y) the bars pass through; default the centroid of the
            eroded pad.
        bar, smooth: see `profile`.

    Returns:
        (hp, vp, (cx, cy, r)): the two `profile` dicts and the circle.
    """
    import matplotlib.pyplot as plt
    img, b = cell_image(samp_no, LED, Bayerlayer, quantity)
    pad = a1.pad_of(samp_no)
    inner = pad.mask(erode)
    if centre is None:
        ys, xs = np.nonzero(inner)
        centre = (float(xs.mean()), float(ys.mean()))

    hp = profile(img, inner, "x", centre, bar, smooth)
    vp = profile(img, inner, "y", centre, bar, smooth)
    (x1, x2), (y1, y2) = hp["edges"], vp["edges"]
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    r = ((x2 - x1) + (y2 - y1)) / 4

    print(f"  bars through ({centre[0]:.1f}, {centre[1]:.1f}), "
          f"{bar} px wide, {smooth}-point smoothing")
    for name, p, (e1, e2) in (("x", hp, hp["edges"]), ("y", vp, vp["edges"])):
        f1, f2 = p["fade"]
        print(f"  {name}1 = {e1:6.2f}  fade {f1[0]:6.2f} .. {f1[1]:6.2f}   "
              f"{name}2 = {e2:6.2f}  fade {f2[0]:6.2f} .. {f2[1]:6.2f}   "
              f"width {e2 - e1:5.2f}")
    print(f"  circle: centre ({cx:.2f}, {cy:.2f})  radius {r:.2f}  "
          f"(half-widths {(x2 - x1) / 2:.2f} x, {(y2 - y1) / 2:.2f} y)")

    span = {"R": (0, 2.5), "R_inf": (0, 1.4), "A": (-0.2, 0.6),
            "KS": (-0.05, 0.4), "KS_signed": (-0.05, 0.4)}.get(quantity)
    vmin, vmax = span if span else np.nanpercentile(img, [0.5, 99.5])

    fig, ax = plt.subplot_mosaic(
        [["img", "hp", "vp"], ["img", "h1", "v1"], ["img", "h2", "v2"]],
        figsize=(15, 8), layout="constrained",
        gridspec_kw={"width_ratios": [1.1, 1, 1]})

    # The image, bars, edges, circle.
    a = ax["img"]
    a.imshow(img, cmap="magma", vmin=vmin, vmax=vmax, interpolation="nearest")
    _zoom(a, pad)
    a.contour(inner.astype(float), levels=[0.5], colors="cyan",
              linewidths=1.0, linestyles=":")
    lo, hi = hp["band"]
    a.axhspan(lo - .5, hi + .5, color="cyan", alpha=0.15)
    lo, hi = vp["band"]
    a.axvspan(lo - .5, hi + .5, color="lime", alpha=0.15)
    a.plot([x1, x2], [centre[1]] * 2, "|", color="cyan", ms=14, mew=2)
    a.plot([centre[0]] * 2, [y1, y2], "_", color="lime", ms=14, mew=2)
    t = np.linspace(0, 2 * np.pi, 200)
    a.plot(cx + r * np.cos(t), cy + r * np.sin(t), "--", color="white",
           lw=1.0, label=f"from 4 edges: r={r:.2f}")
    a.plot(cx, cy, "+", color="white", ms=10)
    a.legend(fontsize=8, loc="lower right")
    a.set_title(f"bars {bar} px, pad eroded {erode:g} px")

    # The profiles.
    for key, p, colour, what in (("h", hp, "tab:cyan", "x (column)"),
                                 ("v", vp, "tab:green", "y (row)")):
        top, d1ax, d2ax = ax[key + "p"], ax[key + "1"], ax[key + "2"]
        top.plot(p["pos"], p["raw"], ".", color="0.6", ms=4, label="raw")
        top.plot(p["pos"], p["sm"], "-", color=colour, lw=1.5,
                 label=f"{smooth}-pt mean")
        top.set_ylabel(quantity)
        top.legend(fontsize=7)
        top.set_title(f"{'horizontal' if key == 'h' else 'vertical'} "
                      f"profile, bar {p['band'][0]}-{p['band'][1]}")
        d1ax.plot(p["pos"], p["d1"], "-", color=colour)
        d1ax.set_ylabel("1st derivative")
        d2ax.plot(p["pos"], p["d2"], "-", color=colour)
        d2ax.set_ylabel("2nd derivative")
        d2ax.set_xlabel(what)
        for axx in (top, d1ax, d2ax):
            axx.grid(alpha=0.3)
            for e in p["edges"]:
                axx.axvline(e, color="k", lw=1.0)
            for f in p["fade"]:
                for e in f:
                    axx.axvline(e, color="k", lw=0.8, ls=":")
        for axx in (d1ax, d2ax):
            axx.axhline(0, color="0.5", lw=0.6)

    fig.suptitle(f"{samp_no}  {cell_name(b.cell)}  {quantity}   "
                 f"{b.rung} {b.exposure_us}us   edges = 1st-derivative "
                 f"extremes (solid), fade = 2nd-derivative extremes (dotted)",
                 fontsize=10)
    _finish(fig, samp_no, f"step2_{cell_name(b.cell)}_{quantity}",
            figs, savefigs)
    return hp, vp, (cx, cy, r)


# ── graphic 1: the faint edge against the noise floor ──────────────────
#
# Why the faint edge matters (Bill, 2026-09-27): the gas enters the paper
# over the circle and may spread sideways through the paper's thickness, so
# much of the reaction can lie below the surface and show only as a faint
# tint around the edge.  And of the three binding sites, two give yellow and
# one brown, so the faint yellows carry the information and brown may mean
# no more than "saturated".  The centre may carry less than the rim.
#
# OPEN, not settled -- the white level and the noise (graphic 1, 2026-09-27):
#   * Which white?  The measurement's pad far from the dot reads below the
#     dry pad (R_inf 1) by 0.3 % (C1032 violet.B), 0.8 % (cyan.B,
#     royal_blue.B), 1.7 % (C1014 royal_blue.B).  Either the whole pad took
#     a faint tint, or pre and post differ in calibration (different
#     exposures; the dark level does not cancel).  Against far white the
#     fade meets 2x the noise floor ~7.5 px outside the edge; against the
#     dry pad, 10-13 px.  Check proposed, not done: post/pre R on the bare
#     paper below the pad, which the gas never reached.  Darker there too
#     -> calibration, use far white; unchanged -> real tint, use the dry pad.
#     Evidence from graphic 2b.  C1032 alone: photo_red.R, which the yellow
#     dot barely absorbs, has far pad / pre pad = 0.9986 while the blue and
#     green cells sit 0.3-0.8 % low -- that looked like a real tint.  But
#     across C1028-C1036 photo_red.R's far pad is 0.14-1.31 % low too
#     (C1028 1.31 %, C1034 0.94 %), the same size as the other cells'
#     0.04-2.16 %.  So most of the offset is common to every LED, red
#     included: calibration, or a change of the whole pad that is not
#     yellow.  royal_blue.B and cyan.B usually sit ~0.3-0.5 % above red,
#     which may be a real tint on top.  Not settled.
#   * Which noise?  sd of far white / sd of the pre-image pad = 0.83-1.0, so
#     no sign that the exposure adds pixel noise.  But the pre-image is shot
#     shorter (royal_blue.B 1000 us vs 2193 us), so its sd may overstate the
#     measurement's.  No zero-concentration exposure yet to settle it.
#   * The far-white level still rises as D_FAR goes 6 -> 12 px in cyan.B
#     and royal_blue.B: the tint fades on beyond 12 px there.  violet.B
#     levels off by 10 px.  C1014 has little far white (198 px beyond 8).

#: Width of one distance band, layer px.
BAND = 0.5

#: Layer px outside the dot's edge beyond which the post-image pad is taken
#: as far white: its median is the white level, its spread one noise
#: estimate.
D_FAR = 8.0

#: Quantities in which the dot reads lower than white.  In the others (A,
#: KS, the PCA layers) it reads higher.
_DARKER = ("sig", "R", "R_inf")


def _nan_smooth(img, size=3):
    """size x size moving mean that skips NaN; NaN where the window is empty."""
    from scipy import ndimage
    ok = np.isfinite(img)
    num = ndimage.uniform_filter(np.where(ok, img, 0.0), size, mode="nearest")
    den = ndimage.uniform_filter(ok.astype(float), size, mode="nearest")
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan)


def dot_edge(img, inner, darker=True, far=D_FAR):
    """
    The dot's edge as the half-way line between white and the dot, and
    every pixel's signed distance from it.

    The edge follows the dot's own shape, so a band "d px outside the
    edge" is a band parallel to it -- round or not (C1014).  On a round dot
    d is simply r - r_edge.

    How:
      1. Smooth 3 x 3 (NaN skipped) so noise does not fray the line.
      2. First guess: the level half-way between the pad's median (mostly
         white -- the dot is under a fifth of the eroded pad) and its 1st
         percentile (deep in the dot).  Pixels past it, the largest
         connected piece, holes filled, form the dot.
      3. Refine once: white = median of the eroded pad more than `far` px
         outside that dot; dot level = median of the dot more than 2 px
         inside its edge; the half-way level between those two makes the
         final dot.  Holes are filled, so a pale centre (a ring-shaped
         dot) still counts as inside.
      4. Signed distance to the edge by `scipy.ndimage.distance_transform_edt`,
         positive outside, negative inside, 0 on the boundary between the
         last inside pixel and the first outside one.

    Returns:
        (core, dist, lev): `core` the boolean dot, `dist` the signed
        distance in layer px (same shape as img), and `lev` a dict with
        'white', 'dot', 'half', 'r_eq' (the radius of a circle of the same
        area) and 'centre' (the dot's centroid, x, y).
    """
    from scipy import ndimage
    sm = np.where(inner, _nan_smooth(img), np.nan)
    s = 1.0 if darker else -1.0          # work as "higher = more dot"
    v = s * -sm

    def _dot(level):
        with np.errstate(invalid="ignore"):
            m = np.nan_to_num(v, nan=-np.inf) > level
        lab, n = ndimage.label(m)
        if n == 0:
            raise RuntimeError("no pixel past the half-way level")
        big = 1 + int(np.argmax(ndimage.sum(m, lab, range(1, n + 1))))
        return ndimage.binary_fill_holes(lab == big)

    def _dist(core):
        return (np.where(core, 0.0, ndimage.distance_transform_edt(~core) - .5)
                - np.where(core, ndimage.distance_transform_edt(core) - .5,
                           0.0))

    vals = v[np.isfinite(v)]
    core = _dot(0.5 * (np.median(vals) + np.percentile(vals, 99)))
    dist = _dist(core)
    w = img[inner & (dist > far) & np.isfinite(img)]
    d = img[core & (dist < -2) & np.isfinite(img)]
    white, dotlev = float(np.median(w)), float(np.median(d))
    half = 0.5 * (white + dotlev)
    core = _dot(s * -half)
    dist = _dist(core)
    ys, xs = np.nonzero(core)
    return core, dist, dict(white=white, dot=dotlev, half=half,
                            r_eq=float(np.sqrt(core.sum() / np.pi)),
                            centre=(float(xs.mean()), float(ys.mean())))


def _robust_sd(v):
    """Standard deviation from the median absolute deviation (1.4826 MAD)."""
    v = v[np.isfinite(v)]
    return float(1.4826 * np.median(np.abs(v - np.median(v))))


def fade_profile(samp_no, LED, Bayerlayer, quantity="R_inf",
                 erode=ERODE_WHITE, band=BAND, far=D_FAR):
    """
    The numbers behind graphic 1: band means by distance from the dot's
    edge, in the measurement and in the pre-image, and two noise levels.

    **Measurement.**  `cell_image` on the bound crop; worked inside the pad
    eroded by `erode`.

    **Pre-image, band means.**  The pre-image R, shifted onto the
    measurement's grid (`get_balanced_cell_data(sec='pre', roi='sample',
    register=True)`), divided by the *measurement's* R_white so both are on
    one scale.  Its band means say whether the dry paper already had any
    structure where the fade lies.  The shift resamples, which smooths
    pixel noise; a band mean is not affected by that.

    **Two noise levels, pixel-to-pixel, robust (1.4826 MAD):**
      * `sd_pre` -- the pre-image pad, eroded by `erode`, on its **own**
        grid, not shifted (the shift would smooth it and understate it).
        No dot there: the paper before any reagent.
      * `sd_far` -- the measurement's pad more than `far` px outside the
        dot's edge.  Unexposed paper that has been through the exposure;
        higher than `sd_pre` if the exposure adds noise, or if the faint
        edge still reaches that far.
    A band of n pixels has a mean uncertain by about sd / sqrt(n), taking
    pixels as independent.

    Only quantity 'R_inf' is wired for the pre-image so far.

    Returns:
        dict with
          d          -- band centres, px from the edge (negative inside)
          n          -- pixels per band (measurement)
          mean       -- measurement band means
          pre        -- pre-image band means (NaN where it has none)
          white      -- far-white level of the measurement
          sd_pre, n_pre, sd_far, n_far
          signal     -- mean read as "amount of dot" against far white:
                        white - mean for R_inf (dot is darker)
          signal_dry -- the same against the dry pad, 1 - mean
          pre_signal -- 1 - pre, the dry paper's own structure
          core, dist, lev, img, inner, b -- from `dot_edge`, `cell_image`
    """
    if quantity != "R_inf":
        raise NotImplementedError("fade_profile: only R_inf so far")
    img, b = cell_image(samp_no, LED, Bayerlayer, quantity)
    inner = a1.pad_of(samp_no).mask(erode)
    core, dist, lev = dot_edge(img, inner, quantity in _DARKER, far)

    pre = a1.get_balanced_cell_data(samp_no, LED, Bayerlayer,
                                    roi=a1.ROISAMPLE, sec="pre", erode=0.0,
                                    register=True)
    pre_img = (np.ma.filled(pre.R.astype(float), np.nan) / b.R_white
               if pre is not None else np.full(img.shape, np.nan))
    own = a1.get_balanced_cell_data(samp_no, LED, Bayerlayer,
                                    roi=a1.ROISAMPLE, sec="pre", erode=erode,
                                    register=False)
    own_v = (np.ma.filled(own.R.astype(float), np.nan) / b.R_white
             if own is not None else np.array([np.nan]))
    far_v = img[inner & (dist > far)]

    use = inner & np.isfinite(img)
    lo = np.floor(dist[use].min() / band) * band
    hi = dist[use].max()
    edges = np.arange(lo, hi + band, band)
    k = np.digitize(dist, edges) - 1
    nb = len(edges) - 1
    n = np.zeros(nb, int)
    mean = np.full(nb, np.nan)
    pmean = np.full(nb, np.nan)
    for i in range(nb):
        sel = use & (k == i)
        n[i] = sel.sum()
        if n[i]:
            mean[i] = img[sel].mean()
            pv = pre_img[sel]
            pv = pv[np.isfinite(pv)]
            if pv.size:
                pmean[i] = pv.mean()
    keep = n > 0
    white = lev["white"]
    return dict(d=(edges[:-1] + band / 2)[keep], n=n[keep], mean=mean[keep],
                pre=pmean[keep], white=white,
                sd_pre=_robust_sd(own_v), n_pre=int(np.isfinite(own_v).sum()),
                sd_far=_robust_sd(far_v), n_far=int(np.isfinite(far_v).sum()),
                signal=white - mean[keep], signal_dry=1.0 - mean[keep],
                pre_signal=1.0 - pmean[keep],
                core=core, dist=dist, lev=lev, img=img, inner=inner, b=b)


def _first_below(d, sig, floor):
    """First band outside the edge (d > 0) where sig drops below floor."""
    out = (d > 0) & (sig < floor)
    return float(d[out][0]) if out.any() else np.nan


def show_fade(samp_no, LED="royal_blue", Bayerlayer="B", quantity="R_inf",
              erode=ERODE_WHITE, band=BAND, far=D_FAR, figs=True,
              savefigs=False):
    """
    Graphic 1.  How far out the dot's faint edge can be told from noise.

    Left: the cell, zoomed to the pad, with the dot's edge (solid, the
    half-way line), bands at 2, 4, 6 px outside it (dotted) and the start of
    the far-white region (dashed).

    Top right, linear: band means against distance from the edge, the
    measurement (colour) and the pre-image (gray), with the far-white level
    and the dry pad (1.0) drawn across.  The whole dot, centre (left end)
    to far pad (right end).

    Bottom right, log: the amount of dot in each band --
      * filled: far white - band mean (the dot against the pad around it)
      * light:  1 - band mean (the dot against the dry pad before exposure)
      * gray:   1 - pre-image band mean (the dry paper's own structure)
    Open markers are negative values, plotted by size.  Against those, the
    noise floor of a band mean, sd / sqrt(n), for the two noise levels, at
    1x (solid) and 2x (dashed).  Where the dot's curve meets the floor is
    where its information runs out; the first band below 2x is marked.

    Returns:
        The `fade_profile` dict.
    """
    import matplotlib.pyplot as plt
    f = fade_profile(samp_no, LED, Bayerlayer, quantity, erode, band, far)
    d, n, lev, b = f["d"], f["n"], f["lev"], f["b"]
    fl_pre = f["sd_pre"] / np.sqrt(n)
    fl_far = f["sd_far"] / np.sqrt(n)
    cut = {"pre1": _first_below(d, f["signal"], fl_pre),
           "pre2": _first_below(d, f["signal"], 2 * fl_pre),
           "far1": _first_below(d, f["signal"], fl_far),
           "far2": _first_below(d, f["signal"], 2 * fl_far)}

    print(f"  {samp_no} {cell_name(b.cell)}  {b.rung} {b.exposure_us}us  "
          f"R_white={b.R_white:.4f} (pre rung {b.white_rung})")
    print(f"  edge: half-way level {lev['half']:.4f} between white "
          f"{lev['white']:.4f} and dot {lev['dot']:.4f};  "
          f"equal-area radius {lev['r_eq']:.2f}, centre "
          f"({lev['centre'][0]:.2f}, {lev['centre'][1]:.2f})")
    print(f"  noise, pixel to pixel:  pre-image pad {f['sd_pre']:.4f} "
          f"(n={f['n_pre']})   measurement far white {f['sd_far']:.4f} "
          f"(n={f['n_far']}, > {far:g} px out)   ratio "
          f"{f['sd_far'] / f['sd_pre']:.2f}")
    print(f"  far white {f['white']:.4f}  ->  1 - far white = "
          f"{1 - f['white']:+.4f} against the dry pad")
    print(f"  signal first below the floor, px outside the edge:  "
          f"pre 1x {cut['pre1']:.2f}  2x {cut['pre2']:.2f}   "
          f"far 1x {cut['far1']:.2f}  2x {cut['far2']:.2f}")

    img, pad = f["img"], a1.pad_of(samp_no)
    fig, ax = plt.subplot_mosaic([["img", "lin"], ["img", "log"]],
                                 figsize=(15, 9), layout="constrained",
                                 gridspec_kw={"width_ratios": [1, 1.5]})
    a = ax["img"]
    a.imshow(img, cmap="magma", vmin=0, vmax=1.4, interpolation="nearest")
    _zoom(a, pad)
    a.contour(f["inner"].astype(float), levels=[0.5], colors="cyan",
              linewidths=1.0, linestyles=":")
    a.contour(f["dist"], levels=[0], colors="white", linewidths=1.4)
    a.contour(f["dist"], levels=[2, 4, 6], colors="white", linewidths=0.8,
              linestyles=":")
    a.contour(f["dist"], levels=[far], colors="lime", linewidths=1.0,
              linestyles="--")
    a.plot([], [], "-", color="white", label="edge (half-way level)")
    a.plot([], [], ":", color="white", label="2, 4, 6 px outside")
    a.plot([], [], "--", color="lime", label=f"far white beyond {far:g} px")
    a.legend(fontsize=8, loc="lower right", facecolor="0.3",
             labelcolor="white")
    a.set_title(f"pad eroded {erode:g} px")

    a = ax["lin"]
    a.plot(d, f["mean"], ".-", color="tab:blue", ms=4, label="measurement")
    a.plot(d, f["pre"], ".-", color="0.6", ms=3, label="pre-image")
    a.axhline(f["white"], color="tab:blue", lw=0.8, ls="--",
              label=f"far white {f['white']:.4f}")
    a.axhline(1.0, color="0.4", lw=0.8, ls=":", label="dry pad 1.0")
    a.axvline(0, color="k", lw=0.8)
    a.axvline(far, color="tab:green", lw=0.8, ls="--")
    a.set_ylabel(quantity + " (band mean)")
    a.legend(fontsize=8)
    a.grid(alpha=0.3)
    a.set_title(f"band means, {band:g} px bands, by distance from the edge")

    a = ax["log"]

    def _pts(y, colour, label, ms=4, alpha=1.0):
        pos = y > 0
        a.plot(d[pos], y[pos], "o", color=colour, ms=ms, alpha=alpha,
               label=label)
        a.plot(d[~pos], -y[~pos], "o", mfc="none", color=colour, ms=ms,
               alpha=alpha)

    _pts(f["signal"], "tab:blue", "far white - mean")
    _pts(f["signal_dry"], "tab:blue", "1 - mean (vs dry pad)", ms=3,
         alpha=0.35)
    _pts(f["pre_signal"], "0.5", "1 - pre-image mean", ms=3)
    for fl, colour, what in ((fl_pre, "tab:orange", "pre-image"),
                             (fl_far, "tab:red", "far white")):
        sd = f["sd_pre"] if what == "pre-image" else f["sd_far"]
        a.plot(d, fl, "-", color=colour, lw=1.2,
               label=f"floor, {what} sd {sd:.4f} / sqrt(n)")
        a.plot(d, 2 * fl, "--", color=colour, lw=1.0, label="  x 2")
    if np.isfinite(cut["pre2"]):
        a.axvline(cut["pre2"], color="tab:orange", lw=0.8, ls="--")
    a.axvline(0, color="k", lw=0.8)
    a.axvline(far, color="tab:green", lw=0.8, ls="--")
    a.set_yscale("log")
    a.set_ylim(1e-4, 2)
    a.set_xlabel("distance from the dot's edge, layer px "
                 "(negative inside, 0 = half-way line)")
    a.set_ylabel("amount of dot, " + quantity)
    a.legend(fontsize=7, ncol=2, loc="lower left")
    a.grid(alpha=0.3, which="both")
    a.set_title("log scale; open markers are negative")

    fig.suptitle(f"{samp_no}  {cell_name(b.cell)}  {quantity}   "
                 f"{b.rung} {b.exposure_us}us   the faint edge against "
                 f"the noise floor", fontsize=11)
    _finish(fig, samp_no, f"fade_{cell_name(b.cell)}_{quantity}",
            figs, savefigs)
    return f


# ── graphic 2: what a cutoff gains and costs ───────────────────────────

def cumulative(f, step=BAND):
    """
    The numbers behind graphic 2: everything inside a cutoff, summed, for
    each cutoff c from the dot's centre outward.

    A pixel is inside cutoff c when its distance from the dot's edge
    (`dot_edge`) is at most c, so c = 0 is the half-way line and c = 4 the
    line 4 px outside it.  For each c:

      * S      -- the dot summed: sum of (far white - R_inf) over the
                  pixels inside.  Read it as "fully dark pixels' worth": a
                  pixel at R_inf 0 on white 1 adds 1.
      * S_dry  -- the same against the dry pad: sum of (1 - R_inf).
      * noise  -- the random error of S, sd * sqrt(N), N the pixels inside,
                  taking pixels as independent; for both noise levels.
      * white_err -- the error S carries from the far-white level itself
                  being uncertain: N * sd_far / sqrt(n_far).  It grows with
                  N, not sqrt(N), because one white is taken from every
                  pixel.
      * tail   -- the dot left outside c: S at `far` minus S at c.  Only
                  out to `far`: beyond it the far-white reference reads the
                  dot as zero by construction, so any tail there is missed
                  by this estimate too.

    Pixels masked (hot, NaN) are left out of the sums and of N.

    Returns:
        dict with c, N, S, S_dry, noise_pre, noise_far, white_err, tail,
        and c_full -- the largest c whose whole band lies inside the eroded
        pad; beyond it the pad's edge clips the band and the sums stop
        meaning "everything within c".
    """
    img, dist, inner = f["img"], f["dist"], f["inner"]
    use = inner & np.isfinite(img)
    order = np.argsort(dist[use])
    dv, iv = dist[use][order], img[use][order]
    c = np.arange(np.ceil(dv.min() / step) * step, dv.max() + step, step)
    N = np.searchsorted(dv, c, side="right")
    S = np.concatenate([[0.0], np.cumsum(f["white"] - iv)])[N]
    S_dry = np.concatenate([[0.0], np.cumsum(1.0 - iv)])[N]
    far = f["far"]
    S_at_far = S[np.searchsorted(c, far)]
    return dict(c=c, N=N, S=S, S_dry=S_dry,
                noise_pre=f["sd_pre"] * np.sqrt(N),
                noise_far=f["sd_far"] * np.sqrt(N),
                white_err=N * f["sd_far"] / np.sqrt(f["n_far"]),
                tail=np.where(c < far, S_at_far - S, np.nan),
                c_full=float(dist[~inner].min()))


def show_cutoff(samp_no, LED="royal_blue", Bayerlayer="B", quantity="R_inf",
                erode=ERODE_WHITE, band=BAND, far=D_FAR, figs=True,
                savefigs=False):
    """
    Graphic 2.  What moving the cutoff outward gains, and what it costs.

    Left: the cell, zoomed to the pad, with the dot's edge (solid) and
    where the band first reaches the eroded pad's edge (cyan dashed).

    Right, all against the cutoff c, px outside the edge:
      * top: the dot summed inside c, against far white and against the dry
        pad, with the number of pixels inside on the right-hand axis.  How
        much the faint edge adds beyond the half-way line.
      * middle: signal-to-noise, S / (sd sqrt N), for the two noise levels.
        It peaks where the pixels being added bring less dot than their
        noise -- the best cutoff for *seeing* the dot, which is not the
        same as the best for *measuring* how much there is.
      * bottom, log: the error budget for measuring how much dot there is.
        Random noise and the white-level error grow as c moves out; the dot
        left outside c shrinks; the dotted line is how much the total moves
        between the two white references.  No best cutoff is marked: the
        tail is measured against far white, so it reaches zero at `far` by
        construction, and any minimum of a combined error would sit just
        inside `far` whatever the data.

    Returns:
        (f, cu): the `fade_profile` and `cumulative` dicts.
    """
    import matplotlib.pyplot as plt
    f = fade_profile(samp_no, LED, Bayerlayer, quantity, erode, band, far)
    f["far"] = far
    cu = cumulative(f, band)
    c, N, b = cu["c"], cu["N"], f["b"]
    snr_pre = np.where(N > 0, cu["S"] / np.maximum(cu["noise_pre"], 1e-12),
                       np.nan)
    snr_far = np.where(N > 0, cu["S"] / np.maximum(cu["noise_far"], 1e-12),
                       np.nan)
    i_snr = int(np.nanargmax(snr_pre))
    i0 = int(np.searchsorted(c, 0.0))

    print(f"  {samp_no} {cell_name(b.cell)}  {b.rung} {b.exposure_us}us  "
          f"far white {f['white']:.4f}  sd pre {f['sd_pre']:.4f}  "
          f"sd far {f['sd_far']:.4f} (n={f['n_far']})")
    print(f"  band reaches the eroded pad edge at c = {cu['c_full']:.2f} px")
    print(f"  signal-to-noise peaks at c = {c[i_snr]:.2f} px")
    print("       c      N       S   S/S(0)    S_dry  S_dry/S(0)   noise"
          "  white_err    tail")
    for cc in (0, 2, 4, 6, 7.5, 10, 12):
        i = int(np.searchsorted(c, cc))
        if i >= len(c):
            continue
        print(f"  {c[i]:6.2f} {N[i]:6d} {cu['S'][i]:7.1f} "
              f"{cu['S'][i] / cu['S'][i0]:7.3f}  {cu['S_dry'][i]:7.1f} "
              f"{cu['S_dry'][i] / cu['S_dry'][i0]:9.3f}  "
              f"{cu['noise_pre'][i]:7.2f} {cu['white_err'][i]:9.2f} "
              f"{cu['tail'][i]:7.2f}")

    img, pad = f["img"], a1.pad_of(samp_no)
    fig, ax = plt.subplot_mosaic([["img", "cum"], ["img", "snr"],
                                  ["img", "err"]],
                                 figsize=(15, 10), layout="constrained",
                                 gridspec_kw={"width_ratios": [1, 1.5]})
    a = ax["img"]
    a.imshow(img, cmap="magma", vmin=0, vmax=1.4, interpolation="nearest")
    _zoom(a, pad)
    a.contour(f["inner"].astype(float), levels=[0.5], colors="cyan",
              linewidths=1.0, linestyles=":")
    a.contour(f["dist"], levels=[0], colors="white", linewidths=1.4)
    a.contour(f["dist"], levels=[cu["c_full"]], colors="cyan",
              linewidths=1.0, linestyles="--")
    a.plot([], [], "-", color="white", label="edge (half-way level)")
    a.plot([], [], "--", color="cyan",
           label=f"band reaches pad edge, c = {cu['c_full']:.2f}")
    a.legend(fontsize=8, loc="lower right", facecolor="0.3",
             labelcolor="white")
    a.set_title(f"pad eroded {erode:g} px")

    def _marks(axx):
        axx.axvline(0, color="k", lw=0.8)
        axx.axvline(far, color="tab:green", lw=0.8, ls="--")
        axx.axvspan(cu["c_full"], c[-1], color="0.85", zorder=0)
        axx.grid(alpha=0.3, which="both")

    a = ax["cum"]
    a.plot(c, cu["S"], "-", color="tab:blue", lw=1.5, label="vs far white")
    a.plot(c, cu["S_dry"], "-", color="tab:blue", lw=1.0, alpha=0.4,
           label="vs dry pad")
    a.set_ylabel("dot summed inside c\n(fully dark pixels' worth)")
    t = a.twinx()
    t.plot(c, N, ":", color="0.4", lw=1.0)
    t.set_ylabel("pixels inside c (dotted)")
    _marks(a)
    a.legend(fontsize=8, loc="upper left")
    a.set_title("the dot summed inside the cutoff "
                "(gray: band clipped by the pad edge)")

    a = ax["snr"]
    a.plot(c, snr_pre, "-", color="tab:orange", label="noise from pre-image")
    a.plot(c, snr_far, "-", color="tab:red", label="noise from far white")
    a.plot(c[i_snr], snr_pre[i_snr], "v", color="tab:orange", ms=8)
    a.set_ylabel("signal / noise")
    _marks(a)
    a.legend(fontsize=8)
    a.set_title("signal-to-noise of the sum (peak = best for seeing the dot)")

    a = ax["err"]
    a.plot(c, cu["noise_pre"], "-", color="tab:orange",
           label="random noise, sd_pre sqrt(N)")
    a.plot(c, cu["white_err"], "-", color="tab:purple",
           label="far-white level error, N sd_far / sqrt(n_far)")
    a.plot(c, cu["tail"], "-", color="tab:blue",
           label=f"dot left outside c (out to {far:g} px)")
    a.plot(c, np.abs(cu["S_dry"] - cu["S"]), ":", color="0.4",
           label="dry pad minus far white (reference choice)")
    _marks(a)
    a.set_yscale("log")
    a.set_ylim(1e-2, None)
    a.set_xlabel("cutoff c, layer px outside the dot's edge "
                 "(0 = half-way line)")
    a.set_ylabel("fully dark pixels' worth")
    a.legend(fontsize=7, loc="lower left")
    a.set_title("error budget for the dot's total (tail reaches 0 at "
                f"{far:g} px by construction)")

    fig.suptitle(f"{samp_no}  {cell_name(b.cell)}  {quantity}   "
                 f"{b.rung} {b.exposure_us}us   what the cutoff gains "
                 f"and costs", fontsize=11)
    _finish(fig, samp_no, f"cutoff_{cell_name(b.cell)}_{quantity}",
            figs, savefigs)
    return f, cu


# ── graphic 2b: exposed against unexposed, ring by ring, many cells ─────

#: Band width for graphic 2b, layer px.  Wider than BAND: eight curves on
#: one chart need less scatter each.
RING = 1.0

#: Colour and line per cell in graphic 2b.  Gb solid, Gr dashed: the two
#: greens are never averaged (they differ ~1.5 % under red LEDs).
_CELL_STYLE = {
    ("violet", "B"): ("darkviolet", "-", "o"),
    ("royal_blue", "B"): ("royalblue", "-", "o"),
    ("cyan", "B"): ("teal", "-", "o"),
    ("cyan", "Gb"): ("darkturquoise", "-", "o"),
    ("cyan", "Gr"): ("darkturquoise", "--", "s"),
    ("green", "Gb"): ("green", "-", "o"),
    ("green", "Gr"): ("limegreen", "--", "s"),
    ("photo_red", "R"): ("red", "-", "o"),
}


def ring_ratios(samp_no, cells, ref=("royal_blue", "B"), erode=ERODE_WHITE,
                ring=RING, far=D_FAR):
    """
    The numbers behind graphic 2b: for each cell, the band mean of R in the
    exposed image against R unexposed, in rings by distance from the dot's
    edge.

    **One geometry for every cell.**  The rings are laid out once, from the
    edge of the `ref` cell (`dot_edge` on its R_inf), and used for all
    cells, so a given ring is the same pixels in every cell and the curves
    can be compared ring by ring.  royal_blue.B by default: every capture
    has it, C1014 included.  The Bayer layers sit on the sensor half a
    layer pixel apart, so across layers a ring is shifted by up to that
    much -- small against 1 px rings.

    **R unexposed, two ways** (R is `sig / divisor`, which is exposure-free,
    so pre and post compare directly):
      * `pix` -- the pre-image, pixel for pixel: its R shifted onto the
        measurement's grid (`register=True`) and averaged over the *same*
        pixels as the exposed band.  The ring mean does the smoothing; no
        extra smoothing is needed.  Takes out the paper's own pattern.
        Carries any pre/post calibration difference.
      * `far` -- the median R of the exposed image's own pad more than `far`
        px outside the ref edge.  Same image, same exposure, so any
        calibration difference cancels; but it reads whatever tint lies
        beyond `far` as white.
    A third, the pre-image's dry-pad median (`R_white`, today's R_inf), is
    returned as `prepad` for the printout: it differs from `pix` only by the
    pre-image's own ring-to-ring pattern.

    The comparison is 1 - (band mean R exposed) / (band mean R unexposed):
    the fraction of the light the dot takes out in that ring.

    Returns:
        dict with d (ring centres, px from the ref edge), lev (the ref
        `dot_edge` levels), c_full (see `cumulative`), and per cell under
        rows[cell]: n, pix, far, prepad (the three fractions), floor_pix,
        floor_far (the noise of a ring mean, sd / sqrt(n), sd robust over
        the far pixels of the per-pixel ratio), white_far, white_pre.
        Cells without a pre-image are skipped, with a message.
    """
    img_ref, _ = cell_image(samp_no, *ref)
    inner = a1.pad_of(samp_no).mask(erode)
    core, dist, lev = dot_edge(img_ref, inner, True, far)
    use = inner & np.isfinite(dist)
    lo = np.floor(dist[use].min() / ring) * ring
    edges = np.arange(lo, dist[use].max() + ring, ring)
    k = np.digitize(dist, edges) - 1
    d = edges[:-1] + ring / 2

    rows = {}
    for cell in cells:
        b = a1.get_balanced_cell_data(samp_no, *cell, roi=a1.ROIBOUND)
        pre = a1.get_balanced_cell_data(samp_no, *cell, roi=a1.ROISAMPLE,
                                        sec="pre", erode=0.0, register=True)
        if b is None or b.R_white is None or pre is None:
            print(f"  {samp_no} {cell_name(cell)}: no pre-image, skipped")
            continue
        post = np.ma.filled(b.R.astype(float), np.nan)
        prer = np.ma.filled(pre.R.astype(float), np.nan)
        ok = inner & np.isfinite(post) & np.isfinite(prer)
        farm = ok & (dist > far)
        white_far = float(np.median(post[farm]))
        n = np.zeros(len(d), int)
        mp, mq = np.full(len(d), np.nan), np.full(len(d), np.nan)
        for i in range(len(d)):
            sel = ok & (k == i)
            n[i] = sel.sum()
            if n[i]:
                mp[i], mq[i] = post[sel].mean(), prer[sel].mean()
        root_n = np.sqrt(np.maximum(n, 1))
        rows[cell] = dict(
            n=n, pix=1 - mp / mq, far=1 - mp / white_far,
            prepad=1 - mp / b.R_white,
            floor_pix=_robust_sd(post[farm] / prer[farm]) / root_n,
            floor_far=_robust_sd(post[farm] / white_far) / root_n,
            white_far=white_far, white_pre=float(b.R_white),
            rung=b.rung)
    return dict(d=d, lev=lev, c_full=float(dist[~inner].min()), rows=rows,
                ref=ref, far=far)


def show_rings(samp_no, cells=a1.PRECELLS, ref=("royal_blue", "B"),
               erode=ERODE_WHITE, ring=RING, far=D_FAR, figs=True,
               savefigs=False):
    """
    Graphic 2b.  Exposed against unexposed, ring by ring, many cells.

    Two charts stacked, x shared: distance of the ring from the `ref`
    cell's dot edge (bottom axis) and roughly the radius from the dot's
    centre (top axis, edge distance + the equal-area radius).  Up the side,
    log: 1 - R exposed / R unexposed, the fraction of the light the dot
    takes out, one curve per cell.  Open markers are negative, plotted by
    size.  Thin dotted lines in each cell's colour: twice that cell's noise
    floor for a ring mean.
      * top:    R unexposed = the pre-image, the same pixels.
      * bottom: R unexposed = the exposed pad's median beyond `far`.
    See `ring_ratios` for why these two.  photo_red.R is the control: a
    yellow dot takes out almost no red, so its curve shows what the method
    reads when there is (nearly) no dot.

    Returns:
        The `ring_ratios` dict.
    """
    import matplotlib.pyplot as plt
    rr = ring_ratios(samp_no, cells, ref, erode, ring, far)
    d, rows, lev = rr["d"], rr["rows"], rr["lev"]

    print(f"  {samp_no}  rings {ring:g} px from the {cell_name(ref)} edge "
          f"(equal-area radius {lev['r_eq']:.2f}); pad eroded {erode:g} px, "
          f"rings clipped by it beyond {rr['c_full']:.2f} px")
    print("  1 - R exposed / R unexposed, per ring (d = px outside the edge);"
          "  pix = pre-image same pixels, far = exposed pad beyond "
          f"{far:g} px, pad = pre-image dry-pad median")
    show_d = (-4.5, 0.5, 2.5, 4.5, 6.5, 9.5, 12.5)
    idx = [int(np.argmin(np.abs(d - x))) for x in show_d]
    print("  " + " " * 13 + "white far/pre  "
          + "".join(f"  d={d[i]:5.1f}      " for i in idx))
    print("  " + " " * 13 + "              "
          + "".join("   pix   far   pad" for _ in idx))
    for cell, r in rows.items():
        print(f"  {cell_name(cell):13s}{r['white_far'] / r['white_pre']:8.4f}"
              "      "
              + "".join(f"{r['pix'][i]:6.3f}{r['far'][i]:6.3f}"
                        f"{r['prepad'][i]:6.3f}" for i in idx))

    fig, axes = plt.subplots(2, 1, figsize=(12, 11), sharex=True,
                             layout="constrained")
    for a, key, what in (
            (axes[0], "pix", "R unexposed = pre-image, same pixels "
                             "(shifted onto this grid)"),
            (axes[1], "far", f"R unexposed = exposed pad's median beyond "
                             f"{far:g} px")):
        for cell, r in rows.items():
            colour, ls, mk = _CELL_STYLE.get(cell, ("k", "-", "o"))
            y = r[key]
            pos = y > 0
            a.plot(d, np.where(pos, y, np.nan), ls, color=colour, lw=1.2,
                   marker=mk, ms=4, label=cell_name(cell))
            a.plot(d[~pos], -y[~pos], mk, mfc="none", color=colour, ms=5)
            a.plot(d, 2 * r["floor_" + key], ":", color=colour, lw=0.9,
                   alpha=0.7)
        a.axvline(0, color="k", lw=0.8)
        a.axvline(far, color="0.4", lw=0.8, ls="--")
        a.axvspan(rr["c_full"], d[-1] + ring / 2, color="0.9", zorder=0)
        a.set_yscale("log")
        a.set_ylim(1e-4, 1.2)
        a.grid(alpha=0.3, which="both")
        a.set_ylabel("1 - R exposed / R unexposed\n"
                     "(fraction of light the dot takes out)")
        a.set_title(what, fontsize=10)
    axes[0].legend(fontsize=8, ncol=4, loc="lower left")
    axes[1].set_xlabel(f"ring centre, layer px outside the {cell_name(ref)} "
                       f"dot edge (0 = half-way line)")
    top = axes[0].secondary_xaxis(
        "top", functions=(lambda x: x + lev["r_eq"],
                          lambda x: x - lev["r_eq"]))
    top.set_xlabel("≈ radius from the dot's centre, layer px")
    fig.suptitle(f"{samp_no}   exposed vs unexposed R in {ring:g} px rings;"
                 f"  open = negative;  dotted = 2x noise floor;  "
                 f"gray = ring clipped by the pad edge;  dashed = {far:g} px",
                 fontsize=10)
    _finish(fig, samp_no, f"rings_{cell_name(ref)}", figs, savefigs)
    return rr


# ── the run ────────────────────────────────────────────────────────────

def run():

    # ── Edit this block for each run.  Guards choose the steps.
    SAMPS = ["C1032"]
    CELL = ("royal_blue", "B")

    # 1. One cell, as R_inf, with the eroded pad outline drawn on it.
    if 0:
        for SAMP in SAMPS:
            print(f"\n=== step 1: show_cell({SAMP!r}, {cell_name(CELL)})")
            show_cell(SAMP, *CELL, figs=True, savefigs=True)

    # 2. Horizontal and vertical profiles through the pad, both
    #    derivatives, the proposed edges marked on the profiles and image.
    if 0:
        for SAMP in SAMPS:
            print(f"\n=== step 2: show_profiles({SAMP!r}, {cell_name(CELL)})")
            show_profiles(SAMP, *CELL, figs=True, savefigs=True)

    # Graphic 1.  The faint edge against the noise floor, log scale.
    #    C1014 has R_inf only in royal_blue.B (its pre-image did not light
    #    violet or cyan); C1028-C1036 have violet.B and cyan.B as well.
    RUNS = [("C1032", ("violet", "B")), ("C1032", ("cyan", "B")),
            ("C1014", ("royal_blue", "B"))]
    if 0:
        for SAMP, cell in RUNS:
            print(f"\n=== graphic 1: show_fade({SAMP!r}, {cell_name(cell)})")
            show_fade(SAMP, *cell, figs=True, savefigs=True)

    # Graphic 2.  The dot summed inside a cutoff; signal-to-noise; the
    #    error budget for the dot's total, against the cutoff.
    if 0:
        for SAMP, cell in RUNS:
            print(f"\n=== graphic 2: show_cutoff({SAMP!r}, {cell_name(cell)})")
            show_cutoff(SAMP, *cell, figs=True, savefigs=True)

    # Graphic 2b.  Exposed against unexposed R, ring by ring, every cell
    #    that has a pre-image, overlaid; two choices of R unexposed stacked.
    if 1:
        SAMPS = [f"C{i}" for i in range(1028,1037)]
        for SAMP in SAMPS:
            print(f"\n=== graphic 2b: show_rings({SAMP!r})")
            show_rings(SAMP, figs=True, savefigs=True)

    a1.close_all()


if __name__ == "__main__":
    run()
