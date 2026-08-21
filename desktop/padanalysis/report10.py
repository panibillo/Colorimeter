"""report10.py -- the dose-response, and how well replicates agree.

The question this set was collected to answer: given a strip prepared at a
known concentration, how repeatable is the colour it develops?

Three levels carry replicates -- 5, 16 and 64 ppb -- and the inventory spans
5 to 256 ppb, so the shape of the response and the scatter about it can be
read off together.

Method
------
Each strip is reduced to `dA`, the absorbance of its pad against its own bare
paper, less the absorbance an unexposed pad shows for the same reason
(`chemistry.py`).  The response is fitted as a power law in concentration,

    dA = k * ppb ** b

over the part of the range where the strips are not close to their colour
limit.  Two things come out of the fit: the exponent `b`, which says how the
colour is built up, and the residual scatter, which is the reproducibility
being asked about.  Scatter in absorbance is then converted to scatter in
*concentration* through the local slope, since a factor f in dA is a factor
f**(1/b) in the concentration that would be read back.

Figures: fig/report10_dose.png, fig/report10_cells.png
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chemistry import Reduction, cell_name, exposed  # noqa: E402
from samples import SAMPLES, quantified, replicates  # noqa: E402

FIG = Path(__file__).resolve().parent / "fig"

#: Above this nominal concentration the pads approach their colour limit and
#: the power law no longer describes them.  See the figure.
LINEAR_MAX_PPB = 32.0

#: The expired strip is excluded from every fit; it is discussed separately.
EXCLUDE = ("X15",)

SESSION = {"X15": "08-10", "C1002": "08-10", "C1003": "08-10",
           "C1004": "08-10", "C1005": "08-10", "C1006": "08-11",
           "C1007": "08-11", "C1008": "08-12", "C1009": "08-12",
           "C1010": "08-13", "C1011": "08-13", "C1012": "08-13",
           "C1013": "08-13"}


def fit_power(r, cell, names):
    """
    Fit dA = k * ppb ** b by least squares in the logarithms.

    Args:
        r: The reduction.
        cell: (led, layer).
        names: Captures to fit.

    Returns:
        (b, log10k, residuals, used) -- `used` drops any capture whose dye
        absorbance came out at or below zero, which cannot be put in a log.
    """
    used, x, y = [], [], []
    for n in names:
        d = r.dA(n, cell)
        if d > 0:
            used.append(n)
            x.append(np.log10(SAMPLES[n].ppb))
            y.append(np.log10(d))
    b, a = np.polyfit(x, y, 1)
    return b, a, np.asarray(y) - (a + b * np.asarray(x)), used


def main(path="records.json"):
    r = Reduction(path)
    FIG.mkdir(exist_ok=True)
    cell = ("royal_blue", "B")

    # quantified(), not exposed(): a strip whose run was interrupted has no
    # concentration to regress against, however good its images are.
    post = [n for n in quantified(exposed()) if n not in EXCLUDE]
    low = [n for n in post if SAMPLES[n].ppb <= LINEAR_MAX_PPB]

    print(f"records: {r.path.name}   erode {r.erode:g} layer px\n")
    print("1. THE MEASUREMENTS, most sensitive cell "
          f"({cell_name(cell)})\n")
    print(f"   {'capture':<9}{'ppb':>5}{'prep':>11}{'session':>9}"
          f"{'rho':>8}{'A':>8}{'dA':>8}")
    for n in sorted(quantified(exposed()), key=lambda n: -SAMPLES[n].ppb):
        s = SAMPLES[n]
        prep = {True: "unshielded", False: "shielded"}[s.splashed]
        print(f"   {n:<9}{s.ppb:5.0f}{prep:>11}{SESSION[n]:>9}"
              f"{r.rho[(n,) + cell]:8.4f}{r.A(n, cell):8.4f}"
              f"{r.dA(n, cell):8.4f}")

    # -- the power law -----------------------------------------------------
    b, a, res, used = fit_power(r, cell, low)
    print(f"\n2. SHAPE OF THE RESPONSE")
    print(f"   Fitted over the {len(used)} strips at {LINEAR_MAX_PPB:g} ppb "
          f"and below:")
    print(f"     dA = {10 ** a:.3g} * ppb ** {b:.2f}")
    print(f"   Residual scatter {10 ** res.std(ddof=1):.2f}x in absorbance, "
          f"which is\n     {10 ** (res.std(ddof=1) / b):.2f}x in the "
          f"concentration that would be read back.")
    ball, aall, resall, usedall = fit_power(r, cell, post)
    print(f"\n   Fitted over the whole range instead, the exponent falls to "
          f"{ball:.2f}\n   and the scatter rises to "
          f"{10 ** resall.std(ddof=1):.2f}x: the strips above about "
          f"{LINEAR_MAX_PPB:g} ppb are\n   approaching their colour limit and "
          f"no longer resolve concentration.")
    hi = [n for n in post if SAMPLES[n].ppb > LINEAR_MAX_PPB]
    pred = 10 ** a * np.array([SAMPLES[n].ppb for n in hi]) ** b
    obs = np.array([r.dA(n, cell) for n in hi])
    print(f"\n   {'capture':<9}{'ppb':>5}{'dA seen':>10}{'dA if the':>11}"
          f"{'   ratio':>9}")
    print(f"   {'':<9}{'':>5}{'':>10}{'law held':>11}")
    for n, o, p in zip(hi, obs, pred):
        print(f"   {n:<9}{SAMPLES[n].ppb:5.0f}{o:10.3f}{p:11.2f}"
              f"{o / p:9.2f}")

    # -- replicates --------------------------------------------------------
    print(f"\n3. REPLICATE AGREEMENT")
    print(f"   Strips prepared at the same nominal concentration, and what "
          f"they\n   would each be read back as, on the fitted low-range "
          f"curve.")
    for ppb, group in replicates().items():
        print(f"\n   {ppb:g} ppb  ({', '.join(group)})")
        back = []
        for n in group:
            d = r.dA(n, cell)
            est = (d / 10 ** a) ** (1 / b) if d > 0 else float("nan")
            back.append(est)
            print(f"     {n:<9}{SESSION[n]:>7}  "
                  f"{'shielded' if SAMPLES[n].splashed is False else 'unshielded':<11}"
                  f"dA {d:7.4f}   reads back as {est:6.1f} ppb")
        back = np.asarray(back)
        print(f"     spread {np.nanmin(back):.1f} to {np.nanmax(back):.1f} ppb "
              f"= {np.nanmax(back) / np.nanmin(back):.1f}x")

    # -- the expired strip -------------------------------------------------
    d = r.dA("X15", cell)
    est = (d / 10 ** a) ** (1 / b)
    print(f"\n4. THE EXPIRED STRIP")
    print(f"   X15 was prepared at {SAMPLES['X15'].ppb:g} ppb and develops "
          f"dA {d:.4f},")
    print(f"   which on this curve reads back as {est:.0f} ppb -- "
          f"{est / SAMPLES['X15'].ppb:.0f} times its preparation, and darker")
    print(f"   than every strip below 128 ppb in the set.  It is excluded "
          f"from the fits.")

    # -- which cells are worth reading ------------------------------------
    print(f"\n5. CELLS RANKED BY WHAT THEY RESOLVE")
    print(f"   'blank noise' is the disagreement of the two unexposed pads; "
          f"'span' is\n   the absorbance between the 5 ppb and 64 ppb strips.")
    print(f"   {'cell':<16}{'slope b':>9}{'span':>8}{'blank':>9}{'span/blank':>12}")
    rank = []
    for c in r.common:
        try:
            bb, aa, rr, uu = fit_power(r, c, low)
        except (ValueError, TypeError):
            continue
        blank = abs(np.log10(r.rho[("C1010-0",) + c] / r.rho[("C1011-pre",) + c]))
        lo5 = np.mean([r.dA(n, c) for n in ("C1011", "C1012")])
        hi64 = np.mean([r.dA(n, c) for n in ("C1004", "C1005", "C1006")])
        span = hi64 - lo5
        rank.append((span / max(blank, 1e-6), c, bb, span, blank))
    for q, c, bb, span, blank in sorted(rank, reverse=True):
        print(f"   {cell_name(c):<16}{bb:9.2f}{span:8.3f}{blank:9.4f}{q:12.0f}")

    _figures(r, cell, post, low, a, b)


def _figures(r, cell, post, low, a, b):
    mark = {"08-10": ("o", "tab:red"), "08-11": ("^", "tab:orange"),
            "08-12": ("s", "tab:green"), "08-13": ("D", "tab:blue")}
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    for ax, logscale in zip(axes, (True, False)):
        for n in post + ["X15"]:
            s = SAMPLES[n]
            m, col = mark[SESSION[n]]
            filled = s.splashed is False
            ax.plot(s.ppb, max(r.dA(n, cell), 1e-3), m, color=col, ms=9,
                    mfc=col if filled else "none",
                    label=None)
            ax.annotate(n, (s.ppb, max(r.dA(n, cell), 1e-3)), fontsize=7,
                        xytext=(5, 3), textcoords="offset points")
        xx = np.logspace(np.log10(4), np.log10(300), 60)
        ax.plot(xx, 10 ** a * xx ** b, "k--", lw=1,
                label=f"dA = {10 ** a:.3g} ppb^{b:.2f}  (fit to <= 32 ppb)")
        ax.axvspan(32, 300, color="k", alpha=0.05)
        ax.set_xscale("log")
        if logscale:
            ax.set_yscale("log")
        ax.set_xlabel("nominal concentration, ppb")
        ax.set_ylabel("dye absorbance dA")
        ax.grid(alpha=0.3, which="both")
        ax.legend(fontsize=8, loc="upper left")
    axes[0].set_title(f"{cell_name(cell)}, logarithmic")
    axes[1].set_title("the same, linear in absorbance")
    fig.suptitle("Filled = shielded from splashes; open = not.  "
                 "Colour = session.  Shaded = above the fitted range.")
    fig.tight_layout()
    fig.savefig(FIG / "report10_dose.png", dpi=120)

    fig, ax = plt.subplots(figsize=(11, 6))
    for c in r.common:
        x = [SAMPLES[n].ppb for n in post]
        y = [max(r.dA(n, c), 1e-4) for n in post]
        o = np.argsort(x)
        ax.plot(np.asarray(x)[o], np.asarray(y)[o], "o-", ms=4, lw=0.9,
                label=cell_name(c))
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("nominal concentration, ppb")
    ax.set_ylabel("dye absorbance dA")
    ax.set_title("Every cell measured on every strip")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(FIG / "report10_cells.png", dpi=120)
    print(f"\nwrote {FIG / 'report10_dose.png'} and {FIG / 'report10_cells.png'}")


if __name__ == "__main__":
    main(*sys.argv[1:])
