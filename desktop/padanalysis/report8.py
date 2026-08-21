"""report8.py -- what the geometry constraint changes.

Compares the blob segmentation against the rectangle on the three things the
analysis actually rests on: the per-cell reflectances, the agreement of the
two 64 ppb replicates, and the rank-1 dye model.

Run measure.py under both finders first:

    .venv/bin/python3 desktop/padanalysis/measure.py 4 rect
    .venv/bin/python3 desktop/padanalysis/measure.py 4 blob
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from samples import WHITE_LED_SET, replicates  # noqa: E402

SP = Path(__file__).resolve().parent

#: The captures both finders can run on -- see `measure.py`.
ORDER = list(WHITE_LED_SET)


def cells(path):
    """
    {(led, layer): {capture: ratio}} averaged over surviving rungs.

    Restricted to ORDER -- the captures the free-form finder can run on -- and
    then to the cells every one of them holds, so the two finders are compared
    on identical ground.  Both steps are needed: the rect finder's record file
    also covers captures the blob finder cannot see (and, since 0.7.0, the
    pre-image sweeps inside them), and a cell counted across those would never
    match ORDER's length.
    """
    recs = json.loads((SP / path).read_text())["records"]
    acc = defaultdict(list)
    for r in recs:
        if r["capture"] not in ORDER:
            continue
        acc[(r["led"], r["layer"], r["capture"])].append(r["ratio"])
    out = defaultdict(dict)
    for (led, layer, cap), v in acc.items():
        out[(led, layer)][cap] = float(np.mean(v))
    return {k: v for k, v in out.items() if len(v) == len(ORDER)}


def rank1(tab):
    """Fit log10 ratio = mu_cell + s_capture * v_cell; return variance share."""
    keys = sorted(tab)
    M = np.array([[np.log10(tab[k][c]) for c in ORDER] for k in keys])
    mu = M.mean(axis=1, keepdims=True)
    D = M - mu
    U, S, Vt = np.linalg.svd(D, full_matrices=False)
    return S[0] ** 2 / (S ** 2).sum(), U[:, 0] * S[0], Vt[0], keys


rect, blob = cells("records.json"), cells("records_blob.json")
keys = sorted(set(rect) & set(blob))

print(f"{'cell':<18}" + "".join(f"{c:>18}" for c in ORDER))
print(f"{'':<18}" + "".join(f"{'rect   Δ%':>18}" for _ in ORDER))
big = []
for k in keys:
    line = f"{k[0] + '/' + k[1]:<18}"
    for c in ORDER:
        d = 100 * (rect[k][c] - blob[k][c]) / blob[k][c]
        line += f"{rect[k][c]:11.4f}{d:+7.2f}"
        big.append(abs(d))
    print(line)
print(f"\nchange in reflectance: median {np.median(big):.2f}%  "
      f"90th pct {np.percentile(big, 90):.2f}%  max {max(big):.2f}%")

print("\n--- the 64 ppb replicates, C1004 vs C1005 ---")
print(f"{'cell':<18}{'blob %':>9}{'rect %':>9}")
db, dr = [], []
for k in keys:
    b = 100 * (blob[k]["C1005"] / blob[k]["C1004"] - 1)
    r = 100 * (rect[k]["C1005"] / rect[k]["C1004"] - 1)
    db.append(b); dr.append(r)
    print(f"{k[0] + '/' + k[1]:<18}{b:9.2f}{r:9.2f}")
print(f"{'mean |disagreement|':<18}{np.abs(db).mean():9.2f}{np.abs(dr).mean():9.2f}")

print("\n--- rank-1 dye model ---")
for label, tab in (("blob", blob), ("rect", rect)):
    share, load, score, ks = rank1({k: tab[k] for k in keys})
    order = np.argsort(score)
    print(f"{label}: rank-1 captures {100 * share:.2f}%   "
          f"scores " + "  ".join(f"{ORDER[i]}={score[i]:+.3f}" for i in order))

print("\n--- X15 / C1005, cell by cell (flat => same colour) ---")
rb = np.array([blob[k]["X15"] / blob[k]["C1005"] for k in keys])
rr = np.array([rect[k]["X15"] / rect[k]["C1005"] for k in keys])
print(f"blob: mean {rb.mean():.4f}  CV {100 * rb.std() / rb.mean():.2f}%")
print(f"rect: mean {rr.mean():.4f}  CV {100 * rr.std() / rr.mean():.2f}%")
