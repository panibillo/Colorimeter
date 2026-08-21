"""report11.py -- the two strips photographed before and after exposure.

C1010 and C1011 were each imaged twice: once before the strip met the sample,
once after.  The strip was taken out of the stage in between, so each pair is
two independent placements of one physical object.  That makes them the
tightest test available of everything except the chemistry, and it removes
the one term a shared blank cannot: the pad's own manufactured colour.

Three questions:

  1. **How well does a placement repeat?**  Width, tilt and position of the
     fitted rectangle, and the reflectance of the strip's own bare paper,
     compared between the two images of one strip.

  2. **What does a shared blank cost?**  The dye absorbance of C1010 and
     C1011 computed against their own pre-exposure image, against the other
     strip's, and against the mean of the two.

  3. **What is the smallest concentration this could resolve?**  From the
     disagreement of the two blanks and the slope of the dose-response.

Figure: fig/report11_pairs.png
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chemistry import Reduction, cell_name  # noqa: E402
from samples import PAIRS, SAMPLES, quantified  # noqa: E402
from report10 import fit_power, LINEAR_MAX_PPB, EXCLUDE  # noqa: E402
from chemistry import exposed  # noqa: E402

FIG = Path(__file__).resolve().parent / "fig"


def main(path="records.json"):
    r = Reduction(path)
    FIG.mkdir(exist_ok=True)
    cell = ("royal_blue", "B")

    print(f"records: {r.path.name}   erode {r.erode:g} layer px\n")

    # -- 1. placement repeatability ---------------------------------------
    print("1. TWO PLACEMENTS OF ONE STRIP")
    print(f"   {'':<22}{'width':>8}{'tilt':>8}{'pad top':>9}{'pad bot':>9}"
          f"{'edge rms':>10}")
    for pre, post in PAIRS:
        for n in (pre, post):
            g = r.pads[n]
            print(f"   {n:<22}{g['width']:8.2f}{g['angle_deg']:8.2f}"
                  f"{g['top']:9.2f}{g['bot']:9.2f}{g['edge_rms']:10.3f}")
        a, b = r.pads[pre], r.pads[post]
        print(f"   {'difference':<22}{b['width'] - a['width']:8.2f}"
              f"{b['angle_deg'] - a['angle_deg']:8.2f}"
              f"{b['top'] - a['top']:9.2f}{b['bot'] - a['bot']:9.2f}\n")

    # A pre-image taken inside the strip's own archive is a two-illuminant
    # sweep, so a pair is compared only on the cells that pair actually holds
    # -- not on r.common, which is the exposed strips' set and far wider.
    print("   The strip's own bare paper, same strip, two placements:")
    diffs = []
    for pre, post in PAIRS:
        shared = [c for c in r.common
                  if (pre,) + c in r.R_paper and (post,) + c in r.R_paper]
        print(f"   {pre} -> {post}   ({len(shared)} cells in common)")
        for c in shared:
            u, v = r.R_paper[(pre,) + c], r.R_paper[(post,) + c]
            print(f"     {cell_name(c):<16}{u:10.4f}{v:10.4f}"
                  f"{(v / u - 1) * 100:+7.1f}%")
            diffs.append(abs(v / u - 1))
    print(f"\n   Median disagreement {np.median(diffs) * 100:.2f} %, largest "
          f"{max(diffs) * 100:.2f} %.")
    print( "   That is the whole chain -- placement, illumination, flat field,")
    print( "   gray card, dark frame -- measured on one unchanging surface.")

    # -- 2. what a shared blank costs -------------------------------------
    print("\n2. THE PAD'S OWN BLANK versus a shared one")
    print(f"   dye absorbance of each pre-imaged strip, {cell_name(cell)}, "
          f"computed three ways")
    print(f"   {'strip':<9}{'own blank':>12}{'others'' mean':>14}"
          f"{'pooled':>10}{'own - pooled':>14}")
    blanks = [pre for pre, _ in PAIRS if (pre,) + cell in r.rho]
    spreads = []
    for pre, post in PAIRS:
        if (pre,) + cell not in r.rho:
            continue
        others = [b for b in blanks if b != pre]
        A = r.A(post, cell)
        own = A + np.log10(r.rho[(pre,) + cell])
        oth = A + np.log10(np.mean([r.rho[(b,) + cell] for b in others]))
        pooled = A + np.log10(r.rho_blank[cell])
        spreads.append(abs(own - pooled))
        print(f"   {post:<9}{own:12.4f}{oth:14.4f}{pooled:10.4f}"
              f"{own - pooled:14.4f}")
    print(f"\n   Using a strip's own blank rather than the pooled one moves its")
    print(f"   dye absorbance by {np.median(spreads):.4f} (median), "
          f"{max(spreads):.4f} at worst.")

    # -- 3. resolution floor ----------------------------------------------
    post_names = [n for n in quantified(exposed()) if n not in EXCLUDE]
    low = [n for n in post_names if SAMPLES[n].ppb <= LINEAR_MAX_PPB]
    b, a, res, used = fit_power(r, cell, low)
    # Over whatever blanks the inventory holds, not a hardcoded pair: the
    # count grows as strips are pre-imaged, and with it the confidence in
    # this number.
    blank_A = [-np.log10(r.rho[(b,) + cell]) for b in blanks]
    blank_sd = float(np.std(blank_A, ddof=1))
    print("\n3. WHAT THE BLANK ALONE WOULD ALLOW")
    print(f"   {len(blank_A)} blanks give a standard deviation of "
          f"{blank_sd:.4f} absorbance in {cell_name(cell)}.")
    for k in (2, 3):
        lim = (k * blank_sd / 10 ** a) ** (1 / b)
        print(f"   {k} standard deviations = {k * blank_sd:.4f} absorbance "
              f"= {lim:.1f} ppb on the fitted curve.")
    print( "\n   This is the floor the *photometry* imposes, from two blanks only,")
    print( "   and it is far below the strip-to-strip scatter of report10.  The")
    print( "   assay's own reproducibility, not the instrument's, sets the")
    print( "   smallest concentration that can be reported.")

    _figure(r, cell)


def _plotted(r, pre):
    """
    The cells a pair can be drawn on, and where they sit on the shared axis.

    A pre-image taken inside a sample's own archive holds two cells, not
    fifteen, so its curve is two points on the same axis rather than a short
    axis of its own -- which keeps every pair comparable at a glance and
    makes the sparseness visible instead of hiding it.
    """
    idx = [i for i, c in enumerate(r.common) if (pre,) + c in r.rho]
    return idx, [r.common[i] for i in idx]


def _ppb_label(post):
    """'16 ppb', or 'ppb unknown' for a strip whose run was interrupted."""
    ppb = SAMPLES[post].ppb
    return "ppb unknown" if ppb is None else f"{ppb:g} ppb"


def _figure(r, cell):
    fig, ax = plt.subplots(1, 2, figsize=(13, 5.5))
    x = np.arange(len(r.common))
    for pre, post in PAIRS:
        idx, cells = _plotted(r, pre)
        ax[0].plot(idx, [r.rho[(pre,) + c] for c in cells], "o--", ms=5,
                   label=f"{pre} (unexposed)")
        ax[0].plot(idx, [r.rho[(post,) + c] for c in cells], "s-", ms=5,
                   label=f"{post} ({_ppb_label(post)})")
    ax[0].axhline(1.0, color="k", lw=0.6)
    ax[0].set_xticks(x)
    ax[0].set_xticklabels([cell_name(c) for c in r.common], rotation=60,
                          ha="right", fontsize=7)
    ax[0].set_ylabel("pad reflectance / bare paper of the same strip")
    ax[0].set_title("Before and after, one strip at a time")
    ax[0].grid(alpha=0.3)
    ax[0].legend(fontsize=8)

    for pre, post in PAIRS:
        idx, cells = _plotted(r, pre)
        ax[1].plot(idx, [r.A(post, c) + np.log10(r.rho[(pre,) + c])
                         for c in cells], "s-", ms=5,
                   label=f"{post} against {pre}")
    ax[1].axhline(0.0, color="k", lw=0.6)
    ax[1].set_xticks(x)
    ax[1].set_xticklabels([cell_name(c) for c in r.common], rotation=60,
                          ha="right", fontsize=7)
    ax[1].set_ylabel("dye absorbance dA")
    ax[1].set_title("The dye alone, each strip against its own blank")
    ax[1].grid(alpha=0.3)
    ax[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG / "report11_pairs.png", dpi=120)
    print(f"\nwrote {FIG / 'report11_pairs.png'}")


if __name__ == "__main__":
    main(*sys.argv[1:])
