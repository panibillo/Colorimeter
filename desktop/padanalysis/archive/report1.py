"""report1.py -- exposure consistency, then reflectance vs ppb per cell."""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

SP = Path(__file__).resolve().parent
D = json.loads((SP / "records.json").read_text())
R = D["records"]
ORDER = ["X15", "C1002", "C1003", "C1004", "C1005"]
PPB = {"X15": 10, "C1002": 256, "C1003": 128, "C1004": 64, "C1005": 64}

# ── 1. does the ratio depend on the exposure rung? ───────────────────
print("=" * 78)
print("1.  EXPOSURE CONSISTENCY -- ratio must not depend on the rung")
print("=" * 78)
by_cell = defaultdict(list)
for r in R:
    by_cell[(r["capture"], r["led"], r["layer"])].append(r)

spreads = []
for k, recs in by_cell.items():
    if len(recs) < 2:
        continue
    v = np.array([r["ratio"] for r in recs])
    spreads.append(((v.max() - v.min()) / v.mean() * 100, k, len(recs),
                    [r["rung"] for r in recs]))
spreads.sort(reverse=True)
arr = np.array([s[0] for s in spreads])
print(f"  {len(spreads)} cells with >=2 usable rungs")
print(f"  spread across rungs: median {np.median(arr):.2f}%  "
      f"p90 {np.percentile(arr, 90):.2f}%  max {arr.max():.2f}%")
print("  worst 8:")
for s, k, n, rungs in spreads[:8]:
    print(f"    {k[0]:<6} {k[1]:<11}{k[2]:<3} {n} rungs {rungs}  {s:5.2f}%")

# ── 2. one ratio per (capture, cell): weight the rungs by signal ─────
best = {}
for k, recs in by_cell.items():
    r = max(recs, key=lambda z: z["mean"])       # brightest surviving rung
    best[k] = r

cells = sorted({(k[1], k[2]) for k in best})
print()
print("=" * 78)
print("2.  REFLECTANCE (pad / gray-chip divisor), brightest usable rung")
print("=" * 78)
hdr = f"{'LED':<11}{'lyr':<4}" + "".join(f"{c:>9}" for c in ORDER)
print(hdr + "    64ppb rep")
print("-" * len(hdr) + "-" * 14)
rep_errs = []
for led, layer in cells:
    vals = [best.get((c, led, layer), {}).get("ratio") for c in ORDER]
    if any(v is None for v in vals):
        line = f"{led:<11}{layer:<4}" + "".join(
            f"{'-':>9}" if v is None else f"{v:>9.4f}" for v in vals)
        print(line)
        continue
    rep = abs(vals[3] - vals[4]) / np.mean(vals[3:5]) * 100
    rep_errs.append(rep)
    print(f"{led:<11}{layer:<4}" + "".join(f"{v:>9.4f}" for v in vals)
          + f"   {rep:8.2f}%")
print(f"\n  C1004 vs C1005 (both 64 ppb) replicate disagreement: "
      f"median {np.median(rep_errs):.2f}%  max {max(rep_errs):.2f}%")

# ── 3. how much did moving off the seeded box change the answer? ─────
print()
print("=" * 78)
print("3.  SEGMENTED PAD vs SEEDED 34x34 BOX")
print("=" * 78)
print(f"  {'capture':<8}{'pad area':>9}{'px used':>9}{'centroid':>18}"
      f"{'median |dR|/R':>16}")
for cname in ORDER:
    d = D["pads"][cname]
    diffs = [abs(r["ratio"] - r["seed_ratio"]) / r["ratio"] * 100
             for r in R if r["capture"] == cname]
    print(f"  {cname:<8}{d['area']:>9}{d['n_used']:>9}"
          f"{'(%.1f, %.1f)' % tuple(d['centroid']):>18}"
          f"{np.median(diffs):>15.2f}%")
