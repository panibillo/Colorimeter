"""report4.py -- how the answer depends on the erosion depth, and what the
reflectances look like once the photometric rim is excluded."""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import Capture, FILES, open_capture  # noqa: E402
from measure import measure_capture  # noqa: E402
from whitecorr import load_models  # noqa: E402

SP = Path(__file__).resolve().parent
ORDER = ["X15", "C1002", "C1003", "C1004", "C1005"]
ERODES = [0, 2, 4, 6, 8, 10, 12, 14]

models = load_models()
table = {}          # (erode, capture, led, layer) -> ratio
npx = {}
for e in ERODES:
    for name in ORDER:
        c = open_capture(name)
        recs, pad = measure_capture(c, models, erode=float(e))
        npx[(e, name)] = int(pad.mask(e).sum())
        for r in recs:
            k = (e, name, r["led"], r["layer"])
            if k not in table or r["mean"] > table[k][1]:
                table[k] = (r["ratio"], r["mean"])
        c.close()
    print(f"erode {e:2d}: px used " +
          " ".join(f"{n}={npx[(e, n)]}" for n in ORDER))

cells = sorted({(k[2], k[3]) for k in table})

# ── replicate agreement, C1004 vs C1005, both 64 ppb ─────────────────
print()
print("=" * 88)
print("REPLICATE AGREEMENT  |C1004 - C1005| / mean,  both 64 ppb")
print("=" * 88)
print(f"{'LED':<11}{'lyr':<4}" + "".join(f"{f'e{e}':>9}" for e in ERODES))
print("-" * (15 + 9 * len(ERODES)))
allrep = {e: [] for e in ERODES}
for led, layer in cells:
    row = []
    for e in ERODES:
        a = table.get((e, "C1004", led, layer))
        b = table.get((e, "C1005", led, layer))
        if a is None or b is None:
            row.append(None)
            continue
        v = abs(a[0] - b[0]) / (0.5 * (a[0] + b[0])) * 100
        row.append(v)
        allrep[e].append(v)
    print(f"{led:<11}{layer:<4}" + "".join(
        f"{'-':>9}" if v is None else f"{v:>8.1f}%" for v in row))
print("-" * (15 + 9 * len(ERODES)))
print(f"{'MEDIAN':<15}" + "".join(f"{np.median(allrep[e]):>8.1f}%"
                                  for e in ERODES))

# ── monotonicity with ppb ────────────────────────────────────────────
print()
print("=" * 88)
print("REFLECTANCE vs ppb at erode=10 (rim excluded)")
print("=" * 88)
seq = ["C1002", "C1003", "C1004", "C1005", "X15"]
print(f"{'LED':<11}{'lyr':<4}" + "".join(
    f"{n + ' ' + str(int(FILES[n])):>13}" for n in seq))
print("-" * (15 + 13 * len(seq)))
for led, layer in cells:
    vals = [table.get((10, n, led, layer)) for n in seq]
    print(f"{led:<11}{layer:<4}" + "".join(
        f"{'-':>13}" if v is None else f"{v[0]:>13.4f}" for v in vals))

json.dump({f"{e}|{n}|{led}|{lay}": v[0]
           for (e, n, led, lay), v in table.items()},
          open(SP / "ratios_by_erode.json", "w"), indent=0)
print("\nwrote ratios_by_erode.json")
