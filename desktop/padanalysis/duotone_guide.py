"""duotone_guide.py -- the figures for `tex/duotone_guide.tex`.

A companion to the LaTeX report that explains `duotone.py`.  Each function
draws one figure on real pads (C1017-C1025 and C1028-C1036, the six
channels they all share), so the report's pictures are the module's own
arithmetic, not sketches.  Read-only: nothing is written to a capture or to
the database.

Figures go to `tex/figs/duotone-<what>.pdf`.
"""
import sys
from pathlib import Path

import numpy as np

SP = Path(__file__).resolve().parent
sys.path.insert(0, str(SP))

import analysis_1 as a1                                     # noqa: E402
import duotone as dt                                        # noqa: E402
import pca_study as ps                                      # noqa: E402

TEXFIG = SP.parent.parent / "tex" / "figs"

#: The yellow example: a 2.2 ppb pad whose dot center sits at -29 degrees on
#: the shared map (docs/pca_study_2026-09-27.md, step 2b).
YELLOW_SAMP = "C1034"

#: The brown example: an 80 ppb pad whose dot center sits at +25 degrees.
BROWN_SAMP = "C1024"

#: Pixels at least this far inside the dot's edge (layer px) make up the
#: dot's example; step 2b found the hue steady from about 3 px in.
INSIDE_PX = 3.0

#: Pixels at least this far outside the dot's edge make up the white example.
OUTSIDE_PX = 6.0

#: Map angles (degrees) where the pooled yellow and brown pixels lie, from
#: `docs/pca_study_2026-09-27.md`, step 2b.  Used only by the schematic.
YELLOW_DEG, BROWN_DEG = -30.0, 20.0

#: Which pad each example comes from, for titles.
SAMP_OF = {"white": YELLOW_SAMP, "yellow": YELLOW_SAMP, "brown": BROWN_SAMP}


def _labels(cells):
    """
    Short channel names for bar-chart ticks.

    Args:
        cells: (LED, Bayer layer) pairs.

    Returns:
        List of strings such as "violet B".
    """
    return [f"{c[0].replace('_', ' ')} {c[1]}" for c in cells]


def example_pixels():
    """
    Three example spectra: white, yellow and brown.

    Each is the median, channel by channel, of a group of real pad pixels,
    so no single speck decides the picture.  The groups come from the dot's
    edge as `pca_study.dot_geometry` finds it.

    Returns:
        (cells, examples, counts): the six cells; a dict name -> (6,)
        absorbance; and a dict name -> number of pixels in the group.
            "white":  YELLOW_SAMP's pad at least OUTSIDE_PX outside the dot,
                      within the pad backed off `pca_study.ERODE_RING` px;
            "yellow": YELLOW_SAMP's pixels at least INSIDE_PX inside the dot;
            "brown":  BROWN_SAMP's pixels at least INSIDE_PX inside the dot.
    """
    cs = ps.cubes(ps.POOL)
    cells = cs[ps.POOL[0]].cells
    out, counts = {}, {}
    for name, samp, want in (("white", YELLOW_SAMP, "out"),
                             ("yellow", YELLOW_SAMP, "in"),
                             ("brown", BROWN_SAMP, "in")):
        cube = cs[samp]
        _, inner, _, dist, _ = ps.dot_geometry(samp)
        d = dist[cube.mask]
        ok = inner[cube.mask] & np.isfinite(d)
        sel = ok & ((d <= -INSIDE_PX) if want == "in" else (d >= OUTSIDE_PX))
        out[name] = np.median(cube.A[sel], axis=0)
        counts[name] = int(sel.sum())
    return cells, out, counts


def fig_split(save=True):
    """
    Figure: a pixel's six absorbances, split into its average (the flat
    part `contrast` removes) and what is left (its contrast).

    Args:
        save: Write tex/figs/duotone-split.pdf.

    Returns:
        The matplotlib figure.
    """
    import matplotlib.pyplot as plt
    cells, ex, n = example_pixels()
    x = np.arange(len(cells))
    fig, ax = plt.subplots(2, 3, figsize=(7.2, 4.6), sharey="row",
                           layout="constrained")
    for j, name in enumerate(("white", "yellow", "brown")):
        a = ex[name]
        m = a.mean()
        ax[0, j].bar(x, a, color="0.55", width=0.7)
        ax[0, j].axhline(m, color="tab:red", lw=1.2, ls="--")
        ax[0, j].text(len(cells) - 0.5, m, f"average {m:.3f}", color="tab:red",
                      fontsize=7, ha="right", va="bottom")
        ax[0, j].set_title(f"{name} ({SAMP_OF[name]}): A", fontsize=9)
        ax[1, j].bar(x, a - m, color="0.3", width=0.7)
        ax[1, j].axhline(0, color="k", lw=0.6)
        ax[1, j].set_title("A minus its average (contrast)", fontsize=9)
        for r in (0, 1):
            ax[r, j].set_xticks(x)
            ax[r, j].set_xticklabels(_labels(cells) if r else [], fontsize=7,
                                     rotation=45, ha="right")
            ax[r, j].grid(axis="y", alpha=0.3)
        print(f"{name:>7}: {n[name]} px  A = {np.round(a, 3)}  "
              f"average {m:.3f}")
    ax[0, 0].set_ylabel("A (base 10)")
    ax[1, 0].set_ylabel("contrast A")
    if save:
        fig.savefig(TEXFIG / "duotone-split.pdf")
    return fig


def fig_plane(save=True):
    """
    Figure: every pad pixel of all 18 pads on the plane `hue_plane` finds,
    with the two directions `duotone.fit` picks as yellow and brown.

    Args:
        save: Write tex/figs/duotone-plane.pdf.

    Returns:
        The matplotlib figure.
    """
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm
    basis, explained, cells = ps.shared_map()
    xy = np.vstack([ps.map_points(s) for s in ps.POOL])
    r = np.hypot(xy[:, 0], xy[:, 1])
    eps_y, eps_b, _ = dt.fit(ps.cubes(ps.POOL))
    _, ex, _ = example_pixels()

    lim = float(np.percentile(r, 99.9)) * 1.05
    fig, ax = plt.subplots(figsize=(6.4, 4.4), layout="constrained")
    h = ax.hist2d(xy[:, 0], xy[:, 1], bins=180,
                  range=[[-0.35 * lim, lim], [-0.6 * lim, 0.6 * lim]],
                  norm=LogNorm(), cmap="Greys")
    fig.colorbar(h[3], ax=ax, label="pixels per bin (log)", shrink=0.8)
    cut = np.median(r)
    # Inset: the crowd of near-white pixels at the origin, where the half
    # that endmembers() keeps (outside the dotted circle) begins.
    ins = ax.inset_axes([0.03, 0.09, 0.26, 0.30])
    z = 3 * cut
    ins.hist2d(xy[:, 0], xy[:, 1], bins=60, range=[[-z, z], [-z, z]],
               norm=LogNorm(), cmap="Greys")
    t = np.linspace(0, 2 * np.pi, 200)
    ins.plot(cut * np.cos(t), cut * np.sin(t), ":", color="tab:blue", lw=1.2)
    ins.set_aspect("equal")
    ins.set_xticks([])
    ins.set_yticks([])
    ins.set_xlabel(f"origin zoomed; dotted:\nmedian strength {cut:.3f}",
                   fontsize=6, color="tab:blue", labelpad=1)
    for e, col, name in ((eps_y, "tab:orange", "fit()'s \"yellow\""),
                         (eps_b, "tab:brown", "fit()'s \"brown\"")):
        p = e @ basis.T
        p = p / np.hypot(*p) * 0.3 * lim
        ax.annotate("", p, (0, 0), arrowprops=dict(arrowstyle="->", color=col,
                                                   lw=1.8))
        ax.text(*(p * 1.08), name, color=col, fontsize=8,
                ha="center", va="bottom" if p[1] > 0 else "top")
        print(f"{name}: angle {ps.angle_of(p):+.0f} deg")
    for name, mk in (("yellow", "o"), ("brown", "s")):
        c = ex[name] - ex[name].mean()
        p = c @ basis.T
        ax.plot(*p, mk, mfc="none", mec="tab:red", ms=8, mew=1.4)
        ax.annotate(f"{name} example", p, xytext=(6, -2),
                    textcoords="offset points", fontsize=8, color="tab:red")
        print(f"{name} example: angle {ps.angle_of(p):+.0f} deg, "
              f"strength {np.hypot(*p):.3f}")
    ax.plot(0, 0, "o", mfc="white", mec="k", ms=6)
    ax.axhline(0, color="0.6", lw=0.6)
    ax.axvline(0, color="0.6", lw=0.6)
    ax.set_aspect("equal")
    ax.set_xlabel(f"axis 1  ({100 * explained[0]:.1f} % of the contrast)")
    ax.set_ylabel(f"axis 2  ({100 * explained[1]:.1f} %)")
    print(f"median strength {cut:.4f}; explained {np.round(explained, 4)}")
    if save:
        fig.savefig(TEXFIG / "duotone-plane.pdf")
    return fig


def fig_spectra(save=True):
    """
    Figure: the two unit spectra `duotone.fit` returns, next to the unit
    contrast spectra of the yellow and brown example pixels.

    Args:
        save: Write tex/figs/duotone-spectra.pdf.

    Returns:
        The matplotlib figure.
    """
    import matplotlib.pyplot as plt
    cells, ex, n = example_pixels()
    eps_y, eps_b, _ = dt.fit(ps.cubes(ps.POOL))
    x = np.arange(len(cells))
    fig, ax = plt.subplots(1, 2, figsize=(7.2, 2.9), sharey=True,
                           layout="constrained")
    for j, (name, e) in enumerate((("yellow", eps_y), ("brown", eps_b))):
        c = ex[name] - ex[name].mean()
        c = c / np.linalg.norm(c)
        ax[j].bar(x - 0.18, c, width=0.36, color="tab:red",
                  label=f"{name} example pixels")
        ax[j].bar(x + 0.18, e, width=0.36, color="0.55",
                  label=f"fit()'s \"{name}\"")
        ax[j].axhline(0, color="k", lw=0.6)
        ax[j].set_xticks(x)
        ax[j].set_xticklabels(_labels(cells), fontsize=7, rotation=45,
                              ha="right")
        ax[j].legend(fontsize=7, loc="upper center")
        ax[j].grid(axis="y", alpha=0.3)
        print(f"{name}: example {np.round(c, 3)}  fit {np.round(e, 3)}  "
              f"cos {c @ e:+.3f}")
    ax[0].set_ylabel("unit contrast spectrum")
    if save:
        fig.savefig(TEXFIG / "duotone-spectra.pdf")
    return fig


def fig_unmix(save=True):
    """
    Figure (schematic): how `unmix` splits a point on the plane of the two
    reference colors, and the four regions its clamps create.

    Directions are the pooled yellow and brown angles (YELLOW_DEG,
    BROWN_DEG); lengths are arbitrary.

    Args:
        save: Write tex/figs/duotone-unmix.pdf.

    Returns:
        The matplotlib figure.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon
    ty, tb = np.radians(YELLOW_DEG), np.radians(BROWN_DEG)
    ey = np.array([np.cos(ty), np.sin(ty)])
    eb = np.array([np.cos(tb), np.sin(tb)])
    R = 8.0                        # far enough to fill the panel

    def ray(t, r=R):
        return r * np.array([np.cos(t), np.sin(t)])

    fig, ax = plt.subplots(figsize=(5.2, 4.4), layout="constrained")
    # Region boundaries: the two rays, and the lines at right angles to them.
    regions = [
        ("both amounts", [tb, ty], "#fde9b8", 2.75),
        ("brown only", [tb + np.pi / 2, tb], "#e8d3c0", 1.7),
        ("yellow only", [ty, ty - np.pi / 2], "#fff6c9", 1.7),
        ("neither:\nwhite", [ty - np.pi / 2, tb + np.pi / 2 - 2 * np.pi],
         "#e4e4e4", 1.5),
    ]
    for name, (t0, t1), col, rl in regions:
        ts = np.linspace(t0, t1, 60)
        pts = [(0, 0)] + [tuple(ray(t)) for t in ts]
        ax.add_patch(Polygon(pts, closed=True, fc=col, ec="none", zorder=0))
        ax.text(*ray(0.5 * (t0 + t1), rl), name, ha="center", va="center",
                fontsize=9)
    for e, col, name in ((ey, "goldenrod", "yellow\nreference"),
                         (eb, "saddlebrown", "brown\nreference")):
        ax.annotate("", 2.4 * e, (0, 0),
                    arrowprops=dict(arrowstyle="->", color=col, lw=2))
        ax.text(*(2.5 * e), name, color=col, fontsize=9, va="center")
    for t in (ty - np.pi / 2, tb + np.pi / 2):
        ax.plot(*zip((0, 0), ray(t)), "--", color="0.5", lw=0.7)

    # A pixel inside the wedge: parallelogram rule.
    y1, b1 = 0.9, 0.8
    p = y1 * ey + b1 * eb
    k = y1 * ey
    ax.plot([0, k[0], p[0]], [0, k[1], p[1]], "-", color="k", lw=0.9)
    ax.text(*(0.5 * k), "y × yellow", fontsize=7, rotation=YELLOW_DEG,
            ha="center", va="top")
    ax.text(*(0.5 * (k + p) + np.array([0.08, 0])), "b × brown", fontsize=7,
            rotation=BROWN_DEG, ha="left", va="center")
    ax.plot(*p, "ko", ms=5)
    ax.annotate("pixel P", p, xytext=(4, 5), textcoords="offset points",
                fontsize=8)
    # A pixel beyond the yellow edge: drop onto the yellow ray.
    q = 1.5 * ey + 0.7 * np.array([ey[1], -ey[0]])
    foot = (q @ ey) * ey
    ax.plot([q[0], foot[0]], [q[1], foot[1]], ":", color="k", lw=1)
    ax.plot(*q, "k^", ms=6)
    ax.annotate("pixel Q: b would be negative,\nso b = 0 and y is refit", q,
                xytext=(8, -4), textcoords="offset points", fontsize=8,
                va="top")
    ax.plot(0, 0, "o", mfc="white", mec="k", ms=6)
    ax.set_xlim(-2.2, 3.4)
    ax.set_ylim(-2.4, 2.2)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    if save:
        fig.savefig(TEXFIG / "duotone-unmix.pdf")
    return fig


def run():
    """
    Draw the report's figures.  Edit the guards to choose which.

    Returns:
        None.
    """
    TEXFIG.mkdir(parents=True, exist_ok=True)

    # 1. One pixel split into its average and its contrast.
    if 1:
        print("\n=== fig_split")
        fig_split()

    # 2. All pixels on the hue plane, with fit()'s two directions.
    if 1:
        print("\n=== fig_plane")
        fig_plane()

    # 3. fit()'s spectra against the example pixels'.
    if 1:
        print("\n=== fig_spectra")
        fig_spectra()

    # 4. Schematic of unmix's four regions.
    if 1:
        print("\n=== fig_unmix")
        fig_unmix()

    a1.close_all()


if __name__ == "__main__":
    run()
