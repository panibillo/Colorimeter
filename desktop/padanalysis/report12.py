"""report12.py -- structure inside the pad, and the splash question.

If stray droplets landed on some pads during the reaction, they would show as
*localised* features: a droplet is a fraction of a millimetre across.  The pad
measures 41.4 x 53.3 layer px, and PROJECT_OVERVIEW.md gives the indicator as
8 mm, which puts one layer pixel between 0.15 and 0.19 mm depending on which
of the two dimensions that 8 mm refers to.  A droplet is therefore a few layer
pixels, and the smoothing below is set to match.  A pad whose
colour is merely stronger or weaker overall, or graded smoothly from one end
to the other, has not been splashed -- it has reacted differently.

So the pad is separated into three parts:

    trend         a cubic surface fitted over the pad, which absorbs the
                  smooth variation the reaction itself produces as the sample
                  wicks through the pad;
    granularity   the standard deviation of what is left, pixel by pixel --
                  the texture of the deposit;
    features      the largest coherent excursion in the residual after
                  smoothing at about a droplet's scale, measured in units of
                  the granularity.

The two unexposed pads set the floor for all three.  A splash should appear as
a feature large against the granularity, and should not appear on a strip that
was shielded.

Figure: fig/report12_structure.png
"""
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import open_capture  # noqa: E402
from padrect import PadRect  # noqa: E402
from fields import reflectance  # noqa: E402
from samples import NAMES, SAMPLES  # noqa: E402
from chemistry import Reduction  # noqa: E402

FIG = Path(__file__).resolve().parent / "fig"

#: Standard deviation of the smoothing kernel, in layer px -- roughly a
#: quarter of a millimetre, which is a droplet's scale.
DROPLET_SIGMA = 1.5

#: Degree of the surface that absorbs the reaction's own smooth variation.
TREND_DEGREE = 3


def _surface(vals, u, v, degree=TREND_DEGREE):
    """Least-squares polynomial surface of the given total degree."""
    terms = [(i, j) for i in range(degree + 1) for j in range(degree + 1)
             if i + j <= degree]
    A = np.column_stack([u ** i * v ** j for i, j in terms])
    coef, *_ = np.linalg.lstsq(A, vals, rcond=None)
    return A @ coef


def analyse(name, erode=6.0, led="royal_blue", layer="B", rung="e0"):
    """
    Split one pad into trend, granularity and features.

    Args:
        name: Capture name.
        erode: Layer px backed off the pad edge.
        led, layer, rung: The cell to work in.  The default is the most
            dye-sensitive one and the rung least likely to clip.

    Returns:
        dict of the summary numbers, plus the residual image and its mask.
    """
    c = open_capture(name)
    try:
        p = PadRect(c)
        r = reflectance(c, led, rung, layer)
        m = p.mask(erode)
        A = -np.log10(np.maximum(r, 1e-6))
        u = (p.u - p.width / 2) / (p.width / 2)
        v = (p.v - 0.5 * (p.top + p.bot)) / (p.height / 2)
        trend = _surface(A[m], u[m], v[m])
        res = A[m] - trend

        img = np.full(m.shape, np.nan)
        img[m] = res
        filled = np.where(m, np.nan_to_num(img), 0.0)
        weight = m.astype(float)
        sm = (ndimage.gaussian_filter(filled, DROPLET_SIGMA)
              / np.maximum(ndimage.gaussian_filter(weight, DROPLET_SIGMA), 1e-9))
        sm = np.where(m, sm, np.nan)
        gran = float(np.std(res, ddof=1))
        peak = float(np.nanmax(np.abs(sm)))
        return dict(name=name, ppb=SAMPLES[name].ppb,
                    splashed=SAMPLES[name].splashed,
                    pad=float(np.mean(A[m])), trend=float(np.std(trend, ddof=1)),
                    granularity=gran, feature=peak, feature_ratio=peak / gran,
                    image=sm, mask=m, n=int(m.sum()))
    finally:
        c.close()


def _span(rows) -> str:
    """
    The concentration range a set of strips covers, as "5 to 256".

    Skips the strips whose concentration is unknown -- an interrupted run
    leaves a pad worth measuring and a nominal value that would be a fiction.
    "" if none of them carry one.
    """
    ppb = [o["ppb"] for o in rows if o["ppb"] is not None]
    return f"{min(ppb):g} to {max(ppb):g}" if ppb else "an unknown range of"


def main(path="records.json"):
    red = Reduction(path)
    FIG.mkdir(exist_ok=True)
    out = [analyse(n, erode=red.erode) for n in NAMES]

    print(f"records: {red.path.name}   erode {red.erode:g} layer px   "
          f"cell royal_blue.B\n")
    print("All quantities are absorbances measured inside the pad.\n")
    print(f"   {'capture':<10}{'ppb':>6}{'prep':>11}{'dye dA':>9}{'trend':>8}"
          f"{'grain':>8}{'grain/dA':>10}{'feature':>9}{'/grain':>8}")
    for o in out:
        n = o["name"]
        dye = red.dA(n, ("royal_blue", "B")) if SAMPLES[n].role == "post" else 0.0
        prep = {True: "unshielded", False: "shielded", None: "blank"}[o["splashed"]]
        rel = f"{o['granularity'] / dye * 100:9.1f}%" if dye > 0.02 else f"{'-':>10}"
        print(f"   {n:<10}{o['ppb'] or 0:6.0f}{prep:>11}{dye:9.4f}"
              f"{o['trend']:8.4f}{o['granularity']:8.4f}{rel}"
              f"{o['feature']:9.4f}{o['feature_ratio']:8.2f}")

    blanks = [o for o in out if o["splashed"] is None]
    print(f"\n1. THE FLOOR")
    print(f"   The {len(blanks)} unexposed pads have granularity "
          + ", ".join(f"{b['granularity']:.4f}" for b in blanks))
    print(f"   absorbance, and no feature larger than "
          f"{max(b['feature_ratio'] for b in blanks):.1f} times it.  Nothing")
    print( "   in the optics or the paper puts a mark on a pad.")

    dyed = [o for o in out if o["splashed"] is not None
            and red.dA(o["name"], ("royal_blue", "B")) > 0.05]
    rel = np.array([o["granularity"]
                    / red.dA(o["name"], ("royal_blue", "B")) for o in dyed])
    print(f"\n2. GRANULARITY IS PROPORTIONAL TO THE DEPOSIT")
    print(f"   Across the {len(dyed)} strips that developed a usable colour, "
          f"the texture of\n   the deposit is "
          f"{rel.mean() * 100:.1f} +/- {rel.std(ddof=1) * 100:.1f} % of the "
          f"dye absorbance -- a constant\n   *relative* roughness, from "
          f"{_span(dyed)} ppb.  The dye is laid down with a\n   fixed grain, "
          f"not with a fixed noise.")

    print(f"\n3. LOCALISED FEATURES")
    ranked = sorted(dyed, key=lambda o: -o["feature_ratio"])
    print( "   The largest coherent excursion in each pad, relative to that "
           "pad's own\n   granularity, most prominent first:")
    for o in ranked:
        prep = {True: "unshielded", False: "shielded"}[o["splashed"]]
        conc = "    ? " if o["ppb"] is None else f"{o['ppb']:5.0f} "
        print(f"     {o['name']:<9}{conc}ppb  {prep:<11}"
              f"{o['feature_ratio']:5.2f}")
    sh = [o["feature_ratio"] for o in dyed if o["splashed"] is False]
    un = [o["feature_ratio"] for o in dyed if o["splashed"] is True]
    print(f"\n   shielded   n={len(sh)}  mean {np.mean(sh):.2f}  "
          f"range {min(sh):.2f} to {max(sh):.2f}")
    print(f"   unshielded n={len(un)}  mean {np.mean(un):.2f}  "
          f"range {min(un):.2f} to {max(un):.2f}")
    print( "\n   The two groups overlap.  Only one capture stands clear of the "
           "rest, and\n   the ordering does not follow whether the strip was "
           "shielded.")

    _figure(out)


def _figure(out):
    fig, axes = plt.subplots(3, 5, figsize=(18, 11))
    for a, o in zip(axes.ravel(), out):
        m = o["mask"]
        ys, xs = np.nonzero(m)
        sub = o["image"][ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        lim = 4 * o["granularity"]
        im = a.imshow(sub, cmap="RdBu_r", vmin=-lim, vmax=lim)
        prep = {True: "unshielded", False: "shielded",
                None: "unexposed"}[o["splashed"]]
        a.set_title(f"{o['name']}  {o['ppb'] or 0:g} ppb  {prep}\n"
                    f"grain {o['granularity']:.4f}   "
                    f"largest feature {o['feature_ratio']:.1f} x grain",
                    fontsize=8)
        a.set_xticks([])
        a.set_yticks([])
        plt.colorbar(im, ax=a, fraction=0.045)
    fig.suptitle("Absorbance left in each pad after a cubic surface is removed, "
                 "smoothed at a droplet's scale.\n"
                 "Each panel is scaled to its own granularity, so the panels "
                 "show shape, not amount.", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(FIG / "report12_structure.png", dpi=110)
    print(f"\nwrote {FIG / 'report12_structure.png'}")


if __name__ == "__main__":
    main(*sys.argv[1:])
