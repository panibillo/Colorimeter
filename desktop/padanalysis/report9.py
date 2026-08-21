"""report9.py -- what the instrument contributes, before any chemistry.

Every statement about the strips rests on the claim that the photometry is
reproducible.  This report tests that claim three ways, on quantities that
carry no chemistry:

  1. **Exposure consistency.**  A reflectance must not depend on how long the
     sensor was integrating.  Each cell measured on more than one rung is a
     repeat measurement of the same quantity.

  2. **The bare paper below the pad.**  Fifteen captures, fifteen different
     strips, two stages, four days.  The paper is the same material every
     time and carries no reagent, so the spread of its reflectance is the
     spread of everything upstream of the chemistry: illumination, flat
     field, gray card, dark subtraction, placement.

  3. **The two unexposed pads.**  Two strips were photographed before use.
     Their pads differ from each other only by manufacture and placement, so
     their disagreement is the floor under any blank correction.

Figure: fig/report9_paper.png
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chemistry import Reduction, cell_name  # noqa: E402
from samples import NAMES, SAMPLES, PAIRS  # noqa: E402

FIG = Path(__file__).resolve().parent / "fig"


def main(path="records.json"):
    r = Reduction(path)
    FIG.mkdir(exist_ok=True)

    print(f"records: {r.path.name}   erode {r.erode:g} layer px   "
          f"finder {r.finder}\n")

    # -- 1. exposure consistency ------------------------------------------
    sp = np.array([v for k, v in r.rung_spread.items() if r.n_rungs[k] > 1])
    print(f"1. EXPOSURE CONSISTENCY  ({len(sp)} cells measured on more than "
          f"one rung)")
    print(f"   full range of the reflectance across rungs, as a fraction of "
          f"its mean:")
    print(f"     median {np.median(sp) * 100:.3f} %   "
          f"90th percentile {np.percentile(sp, 90) * 100:.3f} %   "
          f"largest {sp.max() * 100:.3f} %")
    worst = max((v, k) for k, v in r.rung_spread.items() if r.n_rungs[k] > 1)
    print(f"     largest is {worst[1][0]} {cell_name(worst[1][1:])}\n")

    # -- 2. the bare paper -------------------------------------------------
    print("2. BARE PAPER BELOW THE PAD, referred to the gray chips")
    print(f"   {'cell':<16}{'mean':>8}{'sd':>8}{'cv':>8}{'n':>4}   per capture")
    rows = []
    for cell in r.common:
        vals, caps = [], []
        for n in NAMES:
            k = (n,) + cell
            if k in r.R_card and k not in r.borrowed:
                v = r.R_paper[k]
                vals.append(v)
                caps.append(n)
        vals = np.asarray(vals)
        cv = vals.std(ddof=1) / vals.mean()
        rows.append((cell, vals, caps, cv))
        print(f"   {cell_name(cell):<16}{vals.mean():8.4f}"
              f"{vals.std(ddof=1):8.4f}{cv * 100:7.2f}%{len(vals):4d}")
    allcv = np.array([x[3] for x in rows])
    print(f"\n   Every cell holds to {allcv.max() * 100:.2f} % or better across "
          f"the whole inventory;")
    print(f"   the median cell holds to {np.median(allcv) * 100:.2f} %.  That "
          f"is the instrument's\n   contribution to any strip-to-strip "
          f"comparison below.")
    if r.borrowed:
        print(f"\n   {len(r.borrowed)} (capture, cell) combinations are excluded: the bare")
        print( "   paper is about twice as bright as the gray chips and reached the")
        print( "   sensor ceiling.  They borrow the pooled value instead:")
        for k in r.borrowed:
            print(f"     {k[0]:<10} {cell_name(k[1:])}")

    # -- 3. the unexposed pads ---------------------------------------------
    #
    # Blanks are neither two in number nor alike in coverage any more.  A
    # pre-image taken inside a sample's own archive is a deliberately short
    # sweep -- royal blue and red only -- so most cells are measured on some
    # blanks and not others, and the spread has to be taken over whatever
    # holds each cell rather than by differencing a fixed pair.
    print("\n3. THE UNEXPOSED PAD")
    blanks = [n for n, _ in PAIRS]
    print(f"   {'cell':<16}" + "".join(f"{n:>12}" for n in blanks)
          + f"{'spread':>10}{'in A':>9}")
    diffs = []
    for cell in r.common:
        v = {n: r.rho[(n,) + cell] for n in blanks if (n,) + cell in r.rho}
        if len(v) < 2:
            got = "".join(f"{v.get(n, float('nan')):12.4f}" for n in blanks)
            print(f"   {cell_name(cell):<16}{got}"
                  f"{'--':>10}{'--':>9}   (one blank only)")
            continue
        lo, hi = min(v.values()), max(v.values())
        dA = abs(np.log10(hi / lo))
        diffs.append(dA)
        got = "".join(f"{v[n]:12.4f}" if n in v else f"{'--':>12}"
                      for n in blanks)
        print(f"   {cell_name(cell):<16}{got}{hi - lo:10.4f}{dA:9.4f}")
    print(f"\n   An unexposed pad is not the same white as the paper it sits on:")
    print(f"   it is a little brighter in the red and darker in the blue, so a")
    print(f"   blank correction is a spectrum, not a number.")
    print(f"   Across the {len(blanks)} blanks the pads disagree by "
          f"{np.median(diffs):.4f} absorbance\n   (median over the "
          f"{len(diffs)} cells more than one of them measured), which is the "
          f"floor\n   under any measurement corrected by a blank that is not "
          f"its own strip's.")

    # -- figure ------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(11, 6))
    for cell, vals, caps, cv in rows:
        x = [NAMES.index(c) for c in caps]
        ax.plot(x, vals / vals.mean(), "o-", ms=3, lw=0.8,
                label=f"{cell_name(cell)}  {cv * 100:.2f}%")
    ax.set_xticks(range(len(NAMES)))
    ax.set_xticklabels(NAMES, rotation=45, ha="right", fontsize=8)
    ax.axhline(1.0, color="k", lw=0.6)
    ax.set_ylabel("bare-paper reflectance / its own mean")
    ax.set_title("The strip's bare paper, measured on fifteen different "
                 "strips over four days")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, ncol=2, loc="lower right")
    fig.tight_layout()
    fig.savefig(FIG / "report9_paper.png", dpi=120)
    print(f"\nwrote {FIG / 'report9_paper.png'}")


if __name__ == "__main__":
    main(*sys.argv[1:])
