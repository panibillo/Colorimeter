"""report13.py -- what else could explain the scatter between strips.

`report10` leaves strips prepared at one concentration disagreeing by a
factor of about 1.7 in the concentration they read back as.  `report12` shows
the disagreement is not localised on the pad, so it is not a droplet landing
on one.  This report asks what the disagreement *does* follow.

Three candidates are on the table, and the inventory records all three:

    shielding   whether the strip was protected from stray droplets;
    session     which day the strip was run on, which also carries the stage
                repaint and the change of illuminant set;
    timing      the intervals the archive stores for each strip -- how long
                each reagent acted, how long between them, and how long
                before the capture.

The first two are badly confounded with each other: every shielded strip was
run on one day and every unshielded strip on another.  Nothing in this set can
separate them, and this report says so rather than picking one.

The timing is not confounded, because it varies within each session.  Two of
the four intervals are fixed by the instrument -- both reagent periods are
held to a second.  The other two are whatever the operator did, and one of
them turns out to track the residual.

Method
------
Residuals are taken about the low-range power law of `report10`, so that
approaching the colour limit is not mistaken for scatter.  Each candidate is
tested by permutation: the labels are shuffled twenty thousand times and the
observed correlation is placed in that distribution.  With seven strips in the
low range and four intervals examined, a single p value near 0.05 is a
direction to test, not a result; the multiplicity is stated with it.

Figure: fig/report13_timing.png
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import Capture, open_capture  # noqa: E402
from chemistry import Reduction, exposed  # noqa: E402
from samples import SAMPLES, quantified  # noqa: E402
from report10 import fit_power, LINEAR_MAX_PPB, EXCLUDE, SESSION  # noqa: E402

FIG = Path(__file__).resolve().parent / "fig"
N_PERMUTATIONS = 20000

INTERVALS = {
    "reagent 1": ("reagent1_start", "reagent1_end"),
    "gap": ("reagent1_end", "reagent2_start"),
    "reagent 2": ("reagent2_start", "reagent2_end"),
    "wait": ("reagent2_end", "capture_start"),
    "total": ("reagent1_start", "capture_start"),
}


def timings(names) -> dict:
    """Protocol intervals in seconds, read from each archive's metadata."""
    out = {}
    for n in names:
        c = open_capture(n)
        try:
            s = c.meta["sample"]
            row = {}
            for label, (a, b) in INTERVALS.items():
                row[label] = (None if not (s.get(a) and s.get(b))
                              else int(s[b]) - int(s[a]))
            row["degC"] = s.get("samp_degC")
            out[n] = row
        finally:
            c.close()
    return out


def permutation_r(x, y, n=N_PERMUTATIONS, seed=0):
    """Correlation coefficient and its two-sided permutation p value."""
    rng = np.random.default_rng(seed)
    r = float(np.corrcoef(x, y)[0, 1])
    null = np.array([np.corrcoef(rng.permutation(x), y)[0, 1]
                     for _ in range(n)])
    return r, float((np.abs(null) >= abs(r)).mean())


def main(path="records.json"):
    red = Reduction(path)
    FIG.mkdir(exist_ok=True)
    cell = ("royal_blue", "B")

    # quantified(), not exposed() -- see report10.
    post = [n for n in quantified(exposed()) if n not in EXCLUDE]
    low = [n for n in post if SAMPLES[n].ppb <= LINEAR_MAX_PPB]
    b, a, _, _ = fit_power(red, cell, low)
    resid = {n: np.log10(red.dA(n, cell)) - (a + b * np.log10(SAMPLES[n].ppb))
             for n in post}
    t = timings(post)

    print(f"records: {red.path.name}   erode {red.erode:g} layer px\n")

    # -- the confound, stated plainly -------------------------------------
    print("1. SHIELDING AND SESSION CANNOT BE SEPARATED")
    print(f"   {'session':<10}{'shielded':>10}{'unshielded':>12}")
    sess = sorted({SESSION[n] for n in post})
    for s in sess:
        grp = [n for n in post if SESSION[n] == s]
        sh = [n for n in grp if SAMPLES[n].splashed is False]
        un = [n for n in grp if SAMPLES[n].splashed is True]
        print(f"   {s:<10}{len(sh):>10}{len(un):>12}   "
              f"{', '.join(sh + un)}")
    print( "\n   Every shielded strip was run on one day and every unshielded "
           "strip on\n   another.  The stage was repainted and two illuminants "
           "were switched off\n   between those days as well.  Any difference "
           "between the groups carries\n   all of that, and this set cannot "
           "attribute it.")

    # -- the timing -------------------------------------------------------
    print(f"\n2. WHAT THE PROTOCOL ACTUALLY DID")
    print(f"   {'capture':<9}{'ppb':>5}" +
          "".join(f"{k:>11}" for k in INTERVALS) + f"{'degC':>7}{'residual':>10}")
    for n in post:
        print(f"   {n:<9}{SAMPLES[n].ppb:5.0f}" +
              "".join(f"{t[n][k] if t[n][k] is not None else 0:11d}"
                      for k in INTERVALS) +
              f"{t[n]['degC'] or 0:7.1f}{10 ** resid[n]:10.2f}x")
    for k in ("reagent 1", "reagent 2"):
        v = np.array([t[n][k] for n in post])
        print(f"\n   {k} is held to {v.max() - v.min()} s over the whole set "
              f"({v.min()}-{v.max()} s).")
    for k in ("gap", "wait"):
        v = np.array([t[n][k] for n in post])
        print(f"   {k} is not controlled at all: {v.min()} to {v.max()} s.")

    # -- the tests --------------------------------------------------------
    print(f"\n3. WHICH OF THEM TRACKS THE RESIDUAL")
    print(f"   Over the {len(low)} strips at {LINEAR_MAX_PPB:g} ppb and below, "
          f"where the power law holds.")
    print(f"   {'quantity':<12}{'r':>8}{'p (permutation)':>18}")
    y = np.array([resid[n] for n in low])
    results = []
    for k in list(INTERVALS) + ["degC"]:
        x = np.array([float(t[n][k]) for n in low])
        if x.std() == 0:
            print(f"   {k:<12}{'-':>8}{'fixed by the instrument':>26}")
            continue
        r, p = permutation_r(x, y)
        results.append((k, r, p))
        print(f"   {k:<12}{r:+8.2f}{p:18.3f}")

    best = min(results, key=lambda z: z[2])
    n_tested = len(results)
    print(f"\n   The strongest is '{best[0]}': r = {best[1]:+.2f}, "
          f"p = {best[2]:.3f} on its own.")
    print(f"   {n_tested} quantities were examined, so the p value to judge it "
          f"by is nearer\n   {min(1.0, best[2] * n_tested):.2f}.  It explains "
          f"{best[1] ** 2 * 100:.0f} % of the residual variance, and its sign "
          f"says a\n   longer interval gives a weaker colour.")
    print( "\n   This is a direction to test, not a result: seven strips, four "
           "free\n   intervals, and no strip run deliberately at a different "
           "interval.  The\n   way to settle it is a set in which the interval "
           "is varied on purpose at\n   one concentration, everything else held.")

    _figure(low, t, resid, results)


def _figure(low, t, resid, results):
    keys = [k for k, _, _ in results]
    fig, axes = plt.subplots(1, len(keys), figsize=(3.6 * len(keys), 4.2),
                             sharey=True)
    for ax, (k, r, p) in zip(np.atleast_1d(axes), results):
        for n in low:
            ax.plot(t[n][k], 10 ** resid[n], "o", ms=9,
                    mfc="tab:blue" if SAMPLES[n].splashed is False else "none",
                    color="tab:blue")
            ax.annotate(f"{n} ({SAMPLES[n].ppb:g})", (t[n][k], 10 ** resid[n]),
                        fontsize=6, xytext=(5, 3), textcoords="offset points")
        ax.axhline(1.0, color="k", lw=0.6)
        ax.set_yscale("log")
        ax.set_xlabel(f"{k}, seconds" if k != "degC" else "sample, degC")
        ax.set_title(f"r = {r:+.2f}   p = {p:.3f}", fontsize=9)
        ax.grid(alpha=0.3)
    np.atleast_1d(axes)[0].set_ylabel("observed dA / fitted dA")
    fig.suptitle("Residual about the low-range dose-response, against every "
                 "interval the archive records")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(FIG / "report13_timing.png", dpi=120)
    print(f"\nwrote {FIG / 'report13_timing.png'}")


if __name__ == "__main__":
    main(*sys.argv[1:])
