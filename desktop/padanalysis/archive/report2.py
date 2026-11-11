"""report2.py -- the edge effect: reflectance as a function of depth into pad."""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SP = Path(__file__).resolve().parent
D = json.loads((SP / "records.json").read_text())
R = D["records"]
BINS = D["depth_bins"]
CENTRES = [0.5 * (a + b) for a, b in zip(BINS[:-1], BINS[1:])]
ORDER = ["X15", "C1002", "C1003", "C1004", "C1005"]
PPB = {"X15": 10, "C1002": 256, "C1003": 128, "C1004": 64, "C1005": 64}

# brightest rung per (capture, led, layer)
best = {}
for r in R:
    k = (r["capture"], r["led"], r["layer"])
    if k not in best or r["mean"] > best[k]["mean"]:
        best[k] = r

SHOW = [("blue", "Gb"), ("green", "Gb"), ("white", "R"), ("amber", "R"),
        ("royal_blue", "B"), ("violet", "B")]

print("=" * 92)
print("EDGE EFFECT -- reflectance in each depth band, as % of the deepest band")
print("=" * 92)
for led, layer in SHOW:
    print(f"\n{led}/{layer}     depth band (layer px from pad edge)")
    print(f"  {'capture':<8}" + "".join(f"{f'{a}-{b}':>8}"
                                        for a, b in zip(BINS[:-1], BINS[1:])))
    for cname in ORDER:
        r = best.get((cname, led, layer))
        if r is None or "depth_profile" not in r:
            continue
        p = np.array(r["depth_profile"], dtype=float)
        ref = np.nanmean(p[-3:])
        print(f"  {cname:<8}" + "".join(
            "     nan" if not np.isfinite(v) else f"{(v / ref - 1) * 100:>8.1f}"
            for v in p))

# ── how much does the answer move with the erosion depth? ────────────
print()
print("=" * 92)
print("SENSITIVITY OF THE ANSWER TO THE EROSION DEPTH")
print("(ratio at each erosion, as % of the ratio at the deepest erosion)")
print("=" * 92)
for led, layer in SHOW:
    print(f"\n{led}/{layer}")
    print(f"  {'capture':<8}" + "".join(f"{f'>{b}':>8}" for b in BINS[:-1]))
    for cname in ORDER:
        r = best.get((cname, led, layer))
        if r is None:
            continue
        p = np.array(r["depth_profile"], dtype=float)
        # cumulative mean over bands deeper than each edge, area-weighted is
        # unavailable here, so weight by band area proxy: equal weights are
        # close enough for a shape read.  Use suffix means.
        suffix = [np.nanmean(p[i:]) for i in range(len(p))]
        ref = suffix[-1]
        print(f"  {cname:<8}" + "".join(
            f"{(s / ref - 1) * 100:>8.2f}" for s in suffix))

# ── figure ───────────────────────────────────────────────────────────
fig, axes = plt.subplots(2, 3, figsize=(16, 8), sharex=True)
for ax, (led, layer) in zip(axes.ravel(), SHOW):
    for cname in ORDER:
        r = best.get((cname, led, layer))
        if r is None:
            continue
        p = np.array(r["depth_profile"], dtype=float)
        ref = np.nanmean(p[-3:])
        ax.plot(CENTRES, (p / ref - 1) * 100, "o-",
                label=f"{cname} {PPB[cname]}ppb")
    ax.axhline(0, color="k", lw=.5)
    ax.axvline(4, color="r", lw=.5, ls=":")
    ax.set_title(f"{led}/{layer}")
    ax.set_xlabel("depth from pad edge (layer px)")
    ax.set_ylabel("% of deep-interior reflectance")
    ax.grid(alpha=.3)
    ax.legend(fontsize=7)
plt.tight_layout()
plt.savefig(SP / "fig/edge_effect.png", dpi=95)
print("\nwrote fig/edge_effect.png")
