"""pca_study.py -- hand-driven study of how the yellow and brown colors are
defined, run from an IDE.

The question: `analysis_1.cell_PCA` splits every pad pixel into a yellow
amount and a brown amount, using two reference colors ("endmembers") that
`duotone.endmembers` picks as the two extreme hues found on that one pad.
Should those two reference colors instead be fixed once, from a chosen set
of strongly colored pixels pooled over many pads, and then used unchanged on
every pad?  This module holds the looking-at steps toward that decision.
Each step makes a figure to be judged by eye; nothing here decides anything.

Samples: C1017-C1025 (set 1: larger, softer-edged dots) and C1028-C1036
(set 2: smaller, sharper dots).  `samples.manual_read` holds the solution
concentration.

The data: `duotone.PadCube`, absorbance against the strip's own dry pad in
the pre-image (the new white reference; no bare paper anywhere), in the six
channels every one of the 18 samples has.

Coordinates are bound-crop Bayer-layer coordinates, as in `analysis_1`.
Read-only.  Figures go to `fig/pca_study_<what>.png` when asked.

Findings and how to read each figure: `docs/pca_study_2026-09-27.md`.
"""
import sys
from pathlib import Path

import numpy as np

SP = Path(__file__).resolve().parent
sys.path.insert(0, str(SP))

import analysis_1 as a1                                     # noqa: E402
import dot_study as ds                                      # noqa: E402
import duotone as dt                                        # noqa: E402

FIG = SP / "fig"

#: Both sample sets, in capture order.
SET1 = [f"C{n}" for n in range(1017, 1026)]
SET2 = [f"C{n}" for n in range(1028, 1037)]
POOL = SET1 + SET2

#: Pads chosen to span the concentrations, pale to dark, from both sets.
SPAN = ["C1034", "C1032", "C1021", "C1035", "C1024", "C1025"]

#: Reference directions drawn on every map, degrees.
SPOKES = (-60, -30, 0, 30, 60)

#: The cell the dot's edge is found on (as `dot_study.ring_ratios`): every
#: capture has it, and both yellow and brown take out a lot of it.
EDGE_CELL = ("royal_blue", "B")

#: Layer px backed off the pad edge for the rings, so the pad's own rim
#: shadow is not read as color.  `dot_study.ERODE_WHITE`.
ERODE_RING = ds.ERODE_WHITE

#: Ring width in layer px.  `dot_study.RING`.
RING = ds.RING

#: A ring with fewer pixels than this is not drawn (the dot's very center).
MIN_RING_PX = 15

#: Half-height of a map panel, as a fraction of its width to the right of
#: the origin.
MAP_TALL = 0.5

#: Below this strength (distance from the origin on the map) a ring's hue
#: angle is not plotted: the ring is nearly white and its direction is
#: whatever small offset is left.
MIN_STRENGTH = 0.02


# ── data ───────────────────────────────────────────────────────────────

_cubes = {}
_maps = {}
_edges = {}


def cubes(samps, erode=0.0) -> dict:
    """
    The pad data for several samples, all cut to the channels they share.

    Each sample's data is a `duotone.PadCube`: absorbance against the
    strip's own dry pad in the pre-image, one row per pad pixel, one column
    per channel.  Only channels with that reference can be in it, and only
    the channels every sample in `samps` has are kept -- 6 for SET1 + SET2.

    Built once per (sample, erode) and cached.  The cut to shared channels
    is made on copies, so asking for a different sample list never shrinks
    what an earlier call already holds.

    Args:
        samps: List of sample numbers, e.g. ["C1032", "C1025"].
        erode: Layer px backed off the pad's edges before its pixels are
            taken.  0 keeps the whole pad, rim included.

    Returns:
        Dict {sample number: PadCube}.  The attributes used here:
            .A      (n_px, n_channels) absorbance of each pad pixel;
            .cells  the channel list, as (LED, Bayer layer) pairs, the same
                    order for every sample;
            .mask   (rows, cols) True at the pad pixels .A's rows are;
            .sigma  (n_channels,) per-pixel noise, from the dry pad.
        See `duotone.PadCube` for the rest.
    """
    import copy
    for s in samps:
        if (s, erode) not in _cubes:
            _cubes[(s, erode)] = dt.PadCube(s, erode=erode)
    shared = set.intersection(*[set(_cubes[(s, erode)].cells) for s in samps])
    shared = sorted(shared, key=lambda c: (dt.cell_nm(c), c[1]))
    out = {}
    for s in samps:
        c = copy.copy(_cubes[(s, erode)])
        keep = [c.cells.index(x) for x in shared]
        c.A = c.A[:, keep]
        c.dnoise = c.dnoise[:, keep]
        c.sigma = c.sigma[keep]
        c.white = c.white[keep]
        c.cells = list(shared)
        out[s] = c
    return out


def ppb(samp_no) -> float:
    """
    The solution concentration of one sample.

    Args:
        samp_no: Sample number, e.g. "C1032".

    Returns:
        `samples.manual_read` from the database, in ppb, as a float.
    """
    return float(a1._manual_read(samp_no))


def shared_map(pool=POOL):
    """
    The color map every figure here is drawn on.

    Each pad pixel is a list of six absorbances, one per channel.  Its
    overall darkness -- the part that is the same in all six, which is what
    a shadow does -- is taken out first (`duotone.contrast`), leaving only
    how the six differ from one another: its hue.  The two directions along
    which all pixels of all `pool` pads spread the most
    (`duotone.hue_plane`) become the map's two axes.

    On the map a white pixel sits at the origin; a pixel of one dye at
    growing strength moves out along a straight line from the origin, so the
    *direction* (the angle) is the hue and the *distance* is the strength.

    The sign of each axis is arbitrary in the arithmetic, so it is fixed
    here: axis 1 points the way the colored pixels mostly go, and axis 2
    points toward the pad with the highest concentration.

    Built once per pool and cached.

    Args:
        pool: List of sample numbers whose pixels define the map.  Every
            figure here uses POOL, all 18 samples.

    Returns:
        (basis, explained, cells):
            basis      (2, n_channels) array.  Row 0 is axis 1, row 1 is
                       axis 2; each row gives how much each channel counts
                       toward that axis.
            explained  1-D array, the fraction of all the color variation
                       lying along each direction found, largest first
                       (explained[0] is axis 1, explained[1] axis 2).
            cells      the channel list, as (LED, Bayer layer) pairs, in
                       the order of basis's columns.
    """
    key = tuple(pool)
    if key not in _maps:
        cs = cubes(pool)
        X = np.vstack([dt.contrast(cs[s].A) for s in pool])
        basis, explained = dt.hue_plane(X)
        if (X @ basis[0]).sum() < 0:
            basis[0] = -basis[0]
        dark = max(pool, key=ppb)
        if (dt.contrast(cs[dark].A) @ basis[1]).sum() < 0:
            basis[1] = -basis[1]
        _maps[key] = (basis, explained, cs[pool[0]].cells)
    return _maps[key]


def map_points(samp_no, pool=POOL) -> np.ndarray:
    """
    Where every pad pixel of one sample lies on the shared map.

    Args:
        samp_no: Sample number.  Must be one of `pool`.
        pool: The samples the map is made from (see `shared_map`).

    Returns:
        (n_px, 2) array: column 0 the position along axis 1, column 1 along
        axis 2, one row per pad pixel in the PadCube's order.

    Raises:
        ValueError: `samp_no` is not in `pool`.
    """
    if samp_no not in pool:
        raise ValueError(f"{samp_no} is not in the pool the map was made from")
    basis, _, _ = shared_map(pool)
    return dt.contrast(cubes(pool)[samp_no].A) @ basis.T


def angle_of(xy) -> np.ndarray:
    """
    The hue angle of one or more points on the map.

    Args:
        xy: A point (x, y), or an array of points whose last dimension is 2.

    Returns:
        Angle in degrees, from -180 to +180: 0 points straight along
        axis 1, positive angles point upward (toward brown), negative
        downward (toward yellow).  One value per point.
    """
    xy = np.asarray(xy, dtype=float)
    return np.degrees(np.arctan2(xy[..., 1], xy[..., 0]))


def _axis_words(v, cells) -> str:
    """
    Describe in words what moving along one map axis means.

    Names the two LEDs that count most in the positive direction and the
    two that count most in the negative direction.  The two green layers
    count as one LED.

    Args:
        v: (n_channels,) how much each channel counts toward the axis --
            one row of `shared_map`'s basis.
        cells: The channel list, in the order of `v`.

    Returns:
        A phrase such as "more violet & royal blue light taken out than
        photo red & green".
    """
    def top(order):
        """
        Args:
            order: Channel indexes, most important first.

        Returns:
            The first two distinct LED names in that order, joined by " & ".
        """
        leds = []
        for i in order:
            led = cells[i][0].replace("_", " ")
            if led not in leds:
                leds.append(led)
        return " & ".join(leds[:2])
    o = np.argsort(v)
    return f"more {top(o[::-1])} light taken out than {top(o)}"


def _print_map(pool=POOL):
    """
    Print how much of the color variation the map holds, how much each
    channel counts toward each axis, and what each axis means in words.

    Args:
        pool: The samples the map is made from (see `shared_map`).

    Returns:
        None.  Prints only.
    """
    basis, explained, cells = shared_map(pool)
    print(f"  shared map from {len(pool)} pads, {len(cells)} channels; "
          f"it holds {100 * explained[:2].sum():.1f} % of the color "
          f"variation (axis 1 {100 * explained[0]:.1f} %, axis 2 "
          f"{100 * explained[1]:.1f} %)")
    print(f"  {'channel':14s}{'axis 1':>8s}{'axis 2':>8s}")
    for c, u, v in zip(cells, basis[0], basis[1]):
        print(f"  {dt.cell_name(c):14s}{u:+8.3f}{v:+8.3f}")
    print(f"  axis 1 -> {_axis_words(basis[0], cells)}")
    print(f"  axis 2 -> {_axis_words(basis[1], cells)}")


def _guides(ax, lim, label):
    """
    Draw the map's fixed guides on one panel: lines through the origin,
    the dashed reference spokes (SPOKES, labelled in degrees just inside the
    panel's edge), and an open circle at the origin.

    Args:
        ax: The matplotlib axes to draw on.
        lim: The panel's reach to the right of the origin, in map units --
            the same number later passed to `_set_map_axes`.
        label: True to also write "origin = white (the dry pad)" beside the
            origin (done on the first panel only).

    Returns:
        None.
    """
    xmax, ymax = lim, MAP_TALL * lim
    ax.axhline(0, color="0.55", lw=0.7, zorder=1)
    ax.axvline(0, color="0.55", lw=0.7, zorder=1)
    for deg in SPOKES:
        if deg == 0:
            continue
        t = np.radians(deg)
        c, s = np.cos(t), abs(np.sin(t))
        r = min(xmax / c, ymax / s)
        ax.plot([0, r * c], [0, r * np.sin(t)], "--", color="0.6",
                lw=0.7, zorder=1)
        ax.text(0.86 * r * c, 0.86 * r * np.sin(t), f"{deg:+d}°",
                fontsize=7, color="0.35", ha="center", va="center",
                bbox=dict(fc="white", ec="none", pad=0.5), zorder=2)
    ax.plot(0, 0, "o", mfc="white", mec="k", ms=7, zorder=5)
    if label:
        ax.annotate("origin = white\n(the dry pad)", (0, 0),
                    xytext=(-4, 22), textcoords="offset points", fontsize=7,
                    ha="left", zorder=6)


def _set_map_axes(ax, lim, basis, cells, xlabel=True, ylabel=True):
    """
    Set a map panel's limits, equal scales and axis labels.

    Equal scales on both axes, so an angle drawn on the page is the angle
    the numbers give.  The panel runs from -0.2 * lim to lim across and
    from -MAP_TALL * lim to +MAP_TALL * lim up and down.

    Args:
        ax: The matplotlib axes to set up.
        lim: The panel's reach to the right of the origin, in map units.
        basis: `shared_map`'s basis, used to word the axis labels.
        cells: The channel list, in the order of basis's columns.
        xlabel: True to label axis 1 (bottom row of panels only).
        ylabel: True to label axis 2 (left column of panels only).

    Returns:
        None.
    """
    ax.set_xlim(-0.2 * lim, lim)
    ax.set_ylim(-MAP_TALL * lim, MAP_TALL * lim)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(alpha=0.25)
    if xlabel:
        ax.set_xlabel(f"axis 1 →\n{_axis_words(basis[0], cells)}",
                      fontsize=8)
    if ylabel:
        ax.set_ylabel(f"axis 2 ↑\n{_axis_words(basis[1], cells)}",
                      fontsize=8)


def _finish(fig, what, figs, savefigs):
    """
    Save and/or show one finished figure, then release it.

    Args:
        fig: The matplotlib figure.
        what: Short name for the file: fig/pca_study_<what>.png.
        figs: True to show the figure on screen.
        savefigs: True to write it to the file.

    Returns:
        The Path written, or None if `savefigs` is False.
    """
    import matplotlib.pyplot as plt
    out = None
    if savefigs:
        FIG.mkdir(parents=True, exist_ok=True)
        out = FIG / f"pca_study_{what}.png"
        fig.savefig(out, dpi=110)
        print(f"  wrote {out}")
    if figs:
        plt.show()
    else:
        plt.close(fig)
    return out


# ── step 1: each pad's own yellow and brown, on one shared map ─────────

def _apart(a, b) -> float:
    """
    The angle between two directions, the short way round.

    Args:
        a, b: Two angles in degrees.

    Returns:
        The difference in degrees, from 0 to 180 (so +179 and -179 are 2
        apart, not 358).
    """
    return abs((b - a + 180.0) % 360.0 - 180.0)


def show_own_endmembers(samps=SPAN, pool=POOL, figs=True, savefigs=False):
    """
    Step 1.  What the per-pad fit calls "yellow" and "brown", pad by pad.

    One panel per pad in `samps`, all on the same map (`shared_map`):
      * light gray dots -- every pixel of every pad in `pool`;
      * black dots -- this pad's pixels;
      * orange line -- the "yellow" `duotone.endmembers` picks from this
        pad alone (the default in `cell_PCA`);
      * brown line -- the "brown" it picks;
      * dashed gray spokes -- reference directions, labelled in degrees;
      * open circle at the origin -- white, i.e. no change from the dry pad.
    Both axes are drawn to the same scale, so the angle of a line on the
    page is the angle in the table.

    Judge: do the orange and brown lines point the same way on every pad?
    Do they run along the pad's own black dots?

    Also prints the map's make-up (`_print_map`) and a table of each pad's
    own yellow and brown angles.

    Args:
        samps: The pads to draw, one panel each, two rows.  Each must be in
            `pool`.  Default SPAN, six pads from pale to dark.
        pool: The samples the map is made from, and whose pixels form the
            gray background.  Default POOL, all 18.
        figs: True to show the figure on screen.
        savefigs: True to write fig/pca_study_own_endmembers.png.

    Returns:
        Dict {sample number: (own yellow angle, own brown angle)}, degrees
        on the shared map.
    """
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    basis, _, cells = shared_map(pool)
    _print_map(pool)
    allpts = np.vstack([map_points(s, pool) for s in pool])
    lim = float(np.percentile(np.hypot(*allpts.T), 99.9)) * 1.1

    n = len(samps)
    ncol = (n + 1) // 2
    fig, axes = plt.subplots(2, ncol, figsize=(5.2 * ncol, 8.4),
                             layout="constrained", sharex=True, sharey=True)
    result = {}
    print(f"  {'pad':6s}{'ppb':>7s}{'own yellow':>12s}{'own brown':>11s}"
          f"{'apart':>8s}   (degrees on the shared map)")
    for j, (ax, s) in enumerate(zip(axes.flat, samps)):
        X = dt.contrast(cubes(pool)[s].A)
        pts = X @ basis.T
        eps_y, eps_b, _ = dt.endmembers(X, dt.hue_plane(X)[0], cells)
        ang = [float(angle_of(e @ basis.T)) for e in (eps_y, eps_b)]
        result[s] = tuple(ang)
        print(f"  {s:6s}{ppb(s):7.3g}{ang[0]:12.1f}{ang[1]:11.1f}"
              f"{_apart(*ang):8.1f}")
        ax.plot(allpts[::5, 0], allpts[::5, 1], ".", color="0.85", ms=1,
                zorder=0)
        ax.plot(pts[:, 0], pts[:, 1], ".", color="k", ms=2, alpha=0.5,
                zorder=3)
        _guides(ax, lim, label=(j == 0))
        for a, col in ((ang[0], "orange"), (ang[1], "saddlebrown")):
            t = np.radians(a)
            ax.plot([0, 2 * lim * np.cos(t)], [0, 2 * lim * np.sin(t)], "-",
                    color=col, lw=2.5, zorder=4)
        _set_map_axes(ax, lim, basis, cells,
                      xlabel=(ax in axes[-1]), ylabel=(ax in axes[:, 0]))
        ax.set_title(f"{s}   {ppb(s):g} ppb\nown yellow {ang[0]:+.0f}°,  "
                     f"own brown {ang[1]:+.0f}°,  {_apart(*ang):.0f}° apart",
                     fontsize=9)
    handles = [
        Line2D([], [], ls="", marker=".", color="0.7", ms=6,
               label=f"every pixel of all {len(pool)} pads"),
        Line2D([], [], ls="", marker=".", color="k", ms=6,
               label="this pad's pixels"),
        Line2D([], [], color="orange", lw=2.5,
               label="this pad's own 'yellow' (per-pad fit)"),
        Line2D([], [], color="saddlebrown", lw=2.5,
               label="this pad's own 'brown' (per-pad fit)"),
        Line2D([], [], color="0.6", ls="--", lw=0.8,
               label="reference directions, degrees"),
        Line2D([], [], ls="", marker="o", mfc="white", mec="k", ms=7,
               label="origin = white (no change from the dry pad)"),
    ]
    fig.legend(handles=handles, loc="outside lower center", ncol=3,
               fontsize=8, frameon=False)
    fig.suptitle("Step 1.  Each pad's own 'yellow' and 'brown' from the "
                 "per-pad fit, on one color map shared by all 18 pads.\n"
                 "Angle from the origin = hue;  distance from the origin = "
                 "color strength (overall darkness removed).", fontsize=10)
    _finish(fig, "own_endmembers", figs, savefigs)
    return result


# ── step 2: ring-averaged colors, all pads, on the shared map ──────────

def dot_geometry(samp_no):
    """
    The dot's edge, and every pixel's distance from it, cached.

    `dot_study.dot_edge` on EDGE_CELL's R_inf, inside the pad eroded by
    ERODE_RING: the edge is the half-way line between the pad's white and
    the dot's inside, and the distance is signed -- negative inside the
    dot, positive outside, 0 on the edge.

    Args:
        samp_no: Sample number.

    Returns:
        (img, inner, core, dist, lev), all on the bound-crop layer grid:
            img    (rows, cols) EDGE_CELL's R_inf, NaN where not measured;
            inner  (rows, cols) True inside the pad eroded ERODE_RING px --
                   the region the edge was searched in and the rings use;
            core   (rows, cols) True inside the dot;
            dist   (rows, cols) signed distance from the dot's edge, layer
                   px, negative inside the dot;
            lev    dict: 'white' and 'dot' (the R_inf levels either side of
                   the edge), 'half' (the level the edge is drawn at),
                   'r_eq' (radius of a circle of the dot's area, px) and
                   'centre' (the dot's center, x, y).
    """
    if samp_no not in _edges:
        img, _ = ds.cell_image(samp_no, *EDGE_CELL)
        inner = a1.pad_of(samp_no).mask(ERODE_RING)
        core, dist, lev = ds.dot_edge(img, inner)
        _edges[samp_no] = (img, inner, core, dist, lev)
    return _edges[samp_no]


def show_dot_edges(samps=POOL, figs=True, savefigs=False):
    """
    Step 2a.  The dot's edge on every pad, to be checked by eye before the
    rings in step 2b are believed.

    One panel per pad: EDGE_CELL's R_inf (dark = more light taken out),
    zoomed to the pad.  Red solid line: the dot's edge (half-way line).
    Cyan dotted line: the pad eroded ERODE_RING px, the outer limit of the
    rings.

    Judge: does the red line sit on the dot's edge on every pad?  Is there
    white pad left between the dot and the dotted line?

    Also prints, per pad: concentration, the white and dot R_inf levels,
    the half-way level, the dot's equal-area radius, and the pixel counts
    of the dot and of the eroded pad.

    Args:
        samps: The pads to draw, six to a row.  Default POOL, all 18.
        figs: True to show the figure on screen.
        savefigs: True to write fig/pca_study_dot_edges.png.

    Returns:
        None.
    """
    import matplotlib.pyplot as plt
    ncol = 6
    nrow = (len(samps) + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(2.6 * ncol, 3.3 * nrow),
                             layout="constrained")
    print(f"  {'pad':6s}{'ppb':>7s}{'white':>8s}{'dot':>8s}{'half':>8s}"
          f"{'r_eq px':>9s}{'dot px':>8s}{'pad px':>8s}")
    for ax, s in zip(axes.flat, samps):
        img, inner, core, dist, lev = dot_geometry(s)
        print(f"  {s:6s}{ppb(s):7.3g}{lev['white']:8.3f}{lev['dot']:8.3f}"
              f"{lev['half']:8.3f}{lev['r_eq']:9.2f}{int(core.sum()):8d}"
              f"{int(inner.sum()):8d}")
        ax.imshow(img, cmap="gray", vmin=0.2, vmax=1.1,
                  interpolation="nearest")
        ax.contour(core.astype(float), [0.5], colors="red", linewidths=1.2)
        ax.contour(inner.astype(float), [0.5], colors="cyan",
                   linewidths=1.0, linestyles=":")
        ds._zoom(ax, a1.pad_of(s))
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(f"{s}  {ppb(s):g} ppb", fontsize=9)
    for ax in list(axes.flat)[len(samps):]:
        ax.axis("off")
    fig.suptitle(f"Step 2a.  The dot's edge on every pad, found on "
                 f"{dt.cell_name(EDGE_CELL)} R_inf (dark = light taken out).  "
                 f"Red: dot edge (half-way line).  Cyan dotted: pad eroded "
                 f"{ERODE_RING:g} px, outer limit of the rings.", fontsize=10)
    _finish(fig, "dot_edges", figs, savefigs)


def ring_colors(samp_no, pool=POOL) -> dict:
    """
    The pad's color averaged in rings around the dot, placed on the map.

    Rings are RING px wide, laid out by distance from the dot's edge
    (`dot_geometry`), from the dot's center out to the pad eroded by
    ERODE_RING.  Each ring's pixels are averaged (after overall darkness is
    removed) and the average is placed on the shared map.  Averaging many
    pixels takes out most of the pixel noise.  A ring with fewer than
    MIN_RING_PX pixels (the dot's very center) is left out.

    Args:
        samp_no: Sample number.  Must be one of `pool`.
        pool: The samples the map is made from (see `shared_map`).

    Returns:
        Dict.  Every entry except 'lev' is a 1-D array with one value per
        ring kept, in order from the dot's center outward:
            'd'         ring center, layer px from the dot's edge
                        (negative = inside the dot);
            'n'         number of pixels in the ring;
            'x', 'y'    the ring's average position on the map (axis 1,
                        axis 2);
            'angle'     its hue angle, degrees (see `angle_of`);
            'strength'  its distance from the origin;
            'lev'       the `dot_geometry` levels dict for this pad.
    """
    basis, _, _ = shared_map(pool)
    cube = cubes(pool)[samp_no]
    _, inner, _, dist, lev = dot_geometry(samp_no)
    X = dt.contrast(cube.A)
    d_px = dist[cube.mask]
    use = inner[cube.mask] & np.isfinite(d_px)
    edges = np.arange(np.floor(d_px[use].min() / RING) * RING,
                      d_px[use].max() + RING, RING)
    k = np.digitize(d_px, edges) - 1
    rows = []
    for i in range(len(edges) - 1):
        sel = use & (k == i)
        if sel.sum() < MIN_RING_PX:
            continue
        x, y = X[sel].mean(axis=0) @ basis.T
        rows.append((edges[i] + RING / 2, int(sel.sum()), x, y))
    r = np.array(rows)
    xy = r[:, 2:4]
    return dict(d=r[:, 0], n=r[:, 1].astype(int), x=xy[:, 0], y=xy[:, 1],
                angle=angle_of(xy), strength=np.hypot(xy[:, 0], xy[:, 1]),
                lev=lev)


def show_ring_paths(samps=POOL, pool=POOL, figs=True, savefigs=False):
    """
    Step 2b.  Every pad's ring averages on the shared map, as paths.

    Left, the map (same axes and scale as step 1): one path per pad, from
    the dot's center (filled marker) out to the pad's white.  The open black
    ring on each path is the ring at the dot's edge.  Light gray behind: all
    pixels of all pads.
    Right top: hue angle of each ring against its distance from the dot's
    edge, drawn only where the ring's strength is above MIN_STRENGTH.
    Right bottom: strength of each ring against the same distance.

    Color of a path = concentration (darker blue = higher, log scale).
    Marker = sample set: circle set 1 (C1017-C1025), square set 2
    (C1028-C1036).

    Judge: do all paths follow one common curve?  Do pads of the same
    concentration end at the same place?  Where does each path turn?

    Also prints the map's make-up (`_print_map`) and, per pad in order of
    concentration, the angle and strength of the center ring, the ring at
    the dot's edge, and the strongest ring (with its distance from the
    edge).

    Args:
        samps: The pads to draw, one path each.  Each must be in `pool`.
            Default POOL, all 18.
        pool: The samples the map is made from, and whose pixels form the
            gray background.  Default POOL.
        figs: True to show the figure on screen.
        savefigs: True to write fig/pca_study_ring_paths.png.

    Returns:
        Dict {sample number: the `ring_colors` dict for that pad}.
    """
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm
    from matplotlib.lines import Line2D
    basis, _, cells = shared_map(pool)
    _print_map(pool)
    allpts = np.vstack([map_points(s, pool) for s in pool])
    lim = float(np.percentile(np.hypot(*allpts.T), 99.9)) * 1.1

    rings = {s: ring_colors(s, pool) for s in samps}
    norm = LogNorm(vmin=2, vmax=160)
    cmap = plt.get_cmap("Blues")

    def colour(s):
        """
        Args:
            s: Sample number.

        Returns:
            The RGBA color of that pad's path: its concentration on the
            blue scale (log, 2-160 ppb), kept off the palest blues.
        """
        return cmap(0.35 + 0.65 * float(norm(ppb(s))))

    print(f"  {'pad':6s}{'ppb':>7s}{'rings':>6s}   center: {'angle':>6s}"
          f"{'strength':>9s}   at edge: {'angle':>6s}{'strength':>9s}"
          f"   strongest: {'angle':>6s}{'strength':>9s}{'d px':>6s}")
    for s in sorted(samps, key=ppb):
        r = rings[s]
        e = int(np.argmin(np.abs(r["d"])))
        m = int(np.argmax(r["strength"]))
        print(f"  {s:6s}{ppb(s):7.3g}{len(r['d']):6d}           "
              f"{r['angle'][0]:+6.1f}{r['strength'][0]:9.3f}             "
              f"{r['angle'][e]:+6.1f}{r['strength'][e]:9.3f}               "
              f"{r['angle'][m]:+6.1f}{r['strength'][m]:9.3f}"
              f"{r['d'][m]:6.1f}")

    fig = plt.figure(figsize=(17, 8.6), layout="constrained")
    gs = fig.add_gridspec(2, 2, width_ratios=[1.25, 1])
    ax = fig.add_subplot(gs[:, 0])
    ax_a = fig.add_subplot(gs[0, 1])
    ax_s = fig.add_subplot(gs[1, 1], sharex=ax_a)

    ax.plot(allpts[::5, 0], allpts[::5, 1], ".", color="0.88", ms=1,
            zorder=0)
    _guides(ax, lim, label=True)
    for s in samps:
        r = rings[s]
        mk = "o" if s in SET1 else "s"
        c = colour(s)
        e = int(np.argmin(np.abs(r["d"])))
        ax.plot(r["x"], r["y"], "-", color=c, lw=1.5, zorder=3)
        ax.plot(r["x"], r["y"], mk, color=c, ms=3, zorder=3)
        ax.plot(r["x"][0], r["y"][0], mk, color=c, mec="k", mew=0.6, ms=9,
                zorder=4)
        ax.plot(r["x"][e], r["y"][e], "o", mfc="none", mec="k", mew=1.0,
                ms=11, zorder=4)
        ax.annotate(s[1:], (r["x"][0], r["y"][0]), xytext=(6, 3),
                    textcoords="offset points", fontsize=7, color="0.2",
                    zorder=6)
        show = r["strength"] >= MIN_STRENGTH
        ax_a.plot(r["d"][show], r["angle"][show], "-" + mk, color=c, lw=1.2,
                  ms=3)
        ax_s.plot(r["d"], r["strength"], "-" + mk, color=c, lw=1.2, ms=3)
    _set_map_axes(ax, lim, basis, cells)
    ax.set_title("ring averages on the shared map;  path runs from the "
                 "dot's center (big filled marker, labelled) out to white",
                 fontsize=9)
    for a in (ax_a, ax_s):
        a.axvline(0, color="k", lw=0.8)
        a.grid(alpha=0.3)
    for deg in SPOKES:
        ax_a.axhline(deg, color="0.6", ls="--", lw=0.7)
    ax_a.set_ylabel("hue angle on the map, degrees")
    ax_a.set_title(f"hue of each ring (only rings with strength ≥ "
                   f"{MIN_STRENGTH:g})", fontsize=9)
    ax_s.set_ylabel("strength (distance from the origin)")
    ax_s.set_xlabel(f"ring center, px from the dot's edge "
                    f"(negative = inside the dot; {dt.cell_name(EDGE_CELL)} "
                    f"half-way line = 0)")
    ax_s.set_title("strength of each ring", fontsize=9)

    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    cb = fig.colorbar(sm, ax=[ax_a, ax_s], shrink=0.8, pad=0.01)
    cb.set_ticks([2, 5, 10, 20, 40, 80, 160])
    cb.set_ticklabels(["2", "5", "10", "20", "40", "80", "160"])
    cb.set_label("solution concentration, ppb (log scale; the blue "
                 "is not the dye color)")
    handles = [
        Line2D([], [], ls="", marker="o", color="0.4", ms=6,
               label="set 1: C1017-C1025 (larger, softer dots)"),
        Line2D([], [], ls="", marker="s", color="0.4", ms=6,
               label="set 2: C1028-C1036 (smaller, sharper dots)"),
        Line2D([], [], ls="", marker="o", mfc="0.6", mec="k", ms=9,
               label="the dot's center ring (labelled with the sample no.)"),
        Line2D([], [], ls="", marker="o", mfc="none", mec="k", ms=11,
               label="the ring at the dot's edge"),
        Line2D([], [], color="0.6", ls="--", lw=0.8,
               label="reference directions, degrees"),
    ]
    fig.legend(handles=handles, loc="outside lower center", ncol=5,
               fontsize=8, frameon=False)
    fig.suptitle(f"Step 2b.  The pad's color averaged in {RING:g} px rings "
                 f"around the dot, all 18 pads.  Angle = hue; distance "
                 f"from the origin = color strength (overall darkness "
                 f"removed).", fontsize=10)
    _finish(fig, "ring_paths", figs, savefigs)
    return rings


# ── the run ────────────────────────────────────────────────────────────

def run():
    """
    The procedural steps, each behind an `if 1:` / `if 0:` guard.  Edit
    the guards (and any arguments) to choose what runs.

    Args:
        None.

    Returns:
        None.  Figures are written to fig/ and tables printed.
    """

    # ── Edit this block for each run.  Guards choose the steps.

    # 1. Each pad's own yellow and brown, drawn on one shared color map.
    if 0:
        print("\n=== step 1: show_own_endmembers")
        show_own_endmembers(figs=False, savefigs=True)

    # 2a. The dot's edge on every pad -- check by eye before step 2b.
    if 1:
        print("\n=== step 2a: show_dot_edges")
        show_dot_edges(figs=False, savefigs=True)

    # 2b. Ring-averaged colors of every pad, as paths on the shared map.
    if 1:
        print("\n=== step 2b: show_ring_paths")
        show_ring_paths(figs=False, savefigs=True)

    a1.close_all()


if __name__ == "__main__":
    run()
