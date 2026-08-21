"""report5.py -- which channels are worth using, and what would a calibration
built on them actually do?

Ranks cells by the only figure of merit that matters: sensitivity to
concentration divided by the scatter between strips that are supposed to be
the same.  A cell can correlate beautifully with ppb and still be useless if
its slope is smaller than the strip-to-strip spread -- photo_red does exactly
that.

Then fits log10(reflectance) = a + b*log10(ppb) on the strips other than X15,
and asks what the fit says about every strip including X15.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import Capture, FILES, open_capture  # noqa: E402
from padrect import PadRect  # noqa: E402
from measure import measure_capture  # noqa: E402
from whitecorr import load_models, rect_mask, anchor_weights  # noqa: E402

ORDER = ["X15", "C1002", "C1003", "C1004", "C1005"]
ERODE = 10.0

models = load_models()
val = {}
for name in ORDER:
    c = open_capture(name)
    recs, _ = measure_capture(c, models, erode=ERODE)
    for r in recs:
        k = (name, r["led"], r["layer"])
        if k not in val or r["mean"] > val[k][1]:
            val[k] = (np.log10(r["ratio"]), r["mean"])
    c.close()
cells = sorted({(k[1], k[2]) for k in val})

# ── figure of merit ──────────────────────────────────────────────────
lp = np.log10([FILES[n] for n in ORDER])
fit_on = [ORDER.index(n) for n in ("C1002", "C1003", "C1004", "C1005")]
rows = []
for led, layer in cells:
    y = np.array([val[(n, led, layer)][0] for n in ORDER])
    b, a = np.polyfit(lp[fit_on], y[fit_on], 1)
    resid = y[fit_on] - (a + b * lp[fit_on])
    rms = float(np.sqrt((resid ** 2).mean()))
    rep = abs(val[("C1004", led, layer)][0] - val[("C1005", led, layer)][0])
    # ppb error a 1-sigma strip-to-strip wobble turns into, in decades
    dec = rms / abs(b) if b else np.inf
    rows.append((abs(b) / max(rms, 1e-6), led, layer, b, rms, rep, dec,
                 a, y))
rows.sort(reverse=True)

print("=" * 100)
print(f"CHANNEL RANKING at erode {ERODE:g} -- fit log10(R) = a + b*log10(ppb) "
      "on C1002/3/4/5")
print("=" * 100)
print(f"{'LED':<11}{'lyr':<4}{'slope/dec':>11}{'fit rms':>10}{'S/N':>8}"
      f"{'|64ppb rep|':>13}{'-> ppb err':>13}")
print("-" * 70)
for sn, led, layer, b, rms, rep, dec, a, y in rows:
    print(f"{led:<11}{layer:<4}{b:>11.4f}{rms:>10.4f}{sn:>8.2f}"
          f"{rep:>13.4f}{f'x/÷{10 ** dec:.2f}':>13}")

# ── what the best cells predict ──────────────────────────────────────
print()
print("=" * 100)
print("PREDICTED ppb from the six best cells (fit excludes X15)")
print("=" * 100)
top = rows[:6]
print(f"{'capture':<9}{'true':>7}" + "".join(
    f"{led + '.' + lay:>14}" for _, led, lay, *_ in top) + f"{'geo mean':>11}")
for i, n in enumerate(ORDER):
    preds = []
    for sn, led, layer, b, rms, rep, dec, a, y in top:
        preds.append(10 ** ((y[i] - a) / b))
    print(f"{n:<9}{FILES[n]:>7.0f}" + "".join(f"{p:>14.1f}" for p in preds)
          + f"{10 ** np.mean(np.log10(preds)):>11.1f}")

# ── within-pad heterogeneity as a QC flag ────────────────────────────
print()
print("=" * 100)
print("WITHIN-PAD HETEROGENEITY (pixels deeper than 10, so the rim is out)")
print("A strip that developed unevenly is not a strip whose mean means much.")
print("=" * 100)
print(f"{'capture':<9}{'ppb':>6}{'cyan.Gb CV%':>13}{'colour idx CV%':>16}"
      f"{'idx p90-p10':>13}{'idx skew':>10}")
for name in ORDER:
    c = open_capture(name)
    pad = PadRect(c)
    shape = pad.tot.shape
    core = pad.mask(ERODE)
    ch1 = rect_mask(shape, c.rects["gray1"])
    ch2 = rect_mask(shape, c.rects["gray2"])

    def plane(led, layer):
        m = models[(led, layer)]
        best = None
        for rung in ("e0", "e1", "e2"):
            got = c.layer(led, rung, layer)
            if got is None:
                continue
            if best is None or got[0][core].mean() > best[core].mean():
                best = got[0]
        w1, w2, *_ = anchor_weights(m, core, ch1, ch2)
        return best / (w1 * best[ch1].mean() + w2 * best[ch2].mean())

    cy = plane("cyan", "Gb")[core]
    idx = (plane("cyan", "Gb") / plane("amber", "R"))[core]
    print(f"{name:<9}{FILES[name]:>6.0f}{cy.std() / cy.mean() * 100:>13.2f}"
          f"{idx.std() / idx.mean() * 100:>16.2f}"
          f"{np.percentile(idx, 90) - np.percentile(idx, 10):>13.4f}"
          f"{float(((idx - idx.mean()) ** 3).mean() / idx.std() ** 3):>10.2f}")
    c.close()
