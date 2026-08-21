"""report6.py -- is X15 spectrally distinguishable, or just the same colour?

If every strip's 27-vector is one fixed spectral shape times a per-strip dye
loading, then a strip's colour tells you its loading and nothing else, and no
choice of LEDs, channels, PCA or ANN can separate two strips that sit at the
same loading.  Fit that rank-1 model and look at what is left over.
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import Capture, FILES, open_capture  # noqa: E402
from measure import measure_capture  # noqa: E402
from whitecorr import load_models  # noqa: E402

SP = Path(__file__).resolve().parent
ORDER = ["X15", "C1002", "C1003", "C1004", "C1005"]
ERODE = 10.0
# rough peak wavelengths, for plotting the reflectance spectrum in order
NM = dict(violet=405, royal_blue=455, blue=470, cyan=505, green=530,
          amber=590, red_orange=615, red=630, photo_red=660, white=None)

models = load_models()
val = {}
for name in ORDER:
    c = open_capture(name)
    recs, _ = measure_capture(c, models, erode=ERODE)
    for r in recs:
        k = (name, r["led"], r["layer"])
        if k not in val or r["mean"] > val[k][1]:
            val[k] = (r["ratio"], r["mean"])
    c.close()
cells = sorted({(k[1], k[2]) for k in val})
Y = np.array([[np.log10(val[(n, led, lay)][0]) for led, lay in cells]
              for n in ORDER])

# ── rank-1 model: Y = mu + s (x) v ───────────────────────────────────
mu = Y.mean(0)
Yc = Y - mu
U, S, Vt = np.linalg.svd(Yc, full_matrices=False)
s = U[:, 0] * S[0]
v = Vt[0]
resid = Yc - np.outer(s, v)

print("=" * 92)
print("RANK-1 DYE MODEL   log10 R[strip, cell] = mu[cell] + s[strip]*v[cell]")
print("=" * 92)
print(f"  rank-1 captures {S[0] ** 2 / (S ** 2).sum() * 100:.2f}% of the "
      "between-strip variance")
print(f"  residual RMS over all cells: {np.sqrt((resid ** 2).mean()):.4f} "
      f"in log10 ({(10 ** np.sqrt((resid ** 2).mean()) - 1) * 100:.2f}%)")
print()
print(f"  {'capture':<9}{'ppb':>6}{'dye loading s':>15}{'resid RMS':>12}"
      f"{'resid max':>12}   worst cell")
for i, n in enumerate(ORDER):
    r = resid[i]
    w = int(np.argmax(np.abs(r)))
    print(f"  {n:<9}{FILES[n]:>6.0f}{s[i]:>15.4f}"
          f"{np.sqrt((r ** 2).mean()):>12.4f}{np.abs(r).max():>12.4f}"
          f"   {cells[w][0]}.{cells[w][1]} {r[w]:+.4f}")

print()
print("  Per-cell reflectance, X15 against the two 64 ppb strips.")
print("  If X15 is only 'more developed' the last column is flat.")
print(f"  {'cell':<16}{'X15':>9}{'C1004':>9}{'C1005':>9}"
      f"{'X15/C1005':>11}{'X15/C1004':>11}")
rat5, rat4 = [], []
for j, (led, lay) in enumerate(cells):
    a = val[("X15", led, lay)][0]
    b = val[("C1004", led, lay)][0]
    d = val[("C1005", led, lay)][0]
    rat5.append(a / d)
    rat4.append(a / b)
    print(f"  {led + '.' + lay:<16}{a:>9.4f}{b:>9.4f}{d:>9.4f}"
          f"{a / d:>11.4f}{a / b:>11.4f}")
rat5 = np.array(rat5)
rat4 = np.array(rat4)
print(f"  {'':<16}{'':>9}{'':>9}{'mean':>9}{rat5.mean():>11.4f}"
      f"{rat4.mean():>11.4f}")
print(f"  {'':<16}{'':>9}{'':>9}{'CV %':>9}"
      f"{rat5.std() / rat5.mean() * 100:>11.2f}"
      f"{rat4.std() / rat4.mean() * 100:>11.2f}")

# ── figure: the reflectance spectra ──────────────────────────────────
fig, axes = plt.subplots(1, 3, figsize=(17, 5))
ax = axes[0]
leds = [l for l in NM if NM[l]]
for n in ORDER:
    xs, ys = [], []
    for led in sorted(leds, key=lambda l: NM[l]):
        picks = [val[(n, led, lay)][0] for (le, lay) in cells if le == led]
        if picks:
            xs.append(NM[led])
            ys.append(np.mean(picks))
    ax.plot(xs, ys, "o-", label=f"{n}  {FILES[n]:g} ppb")
ax.set_xlabel("LED peak wavelength (nm)")
ax.set_ylabel("reflectance vs gray chips")
ax.set_title(f"Pad reflectance spectrum (erode {ERODE:g})")
ax.grid(alpha=.3)
ax.legend(fontsize=8)

ax = axes[1]
for n in ORDER:
    xs, ys = [], []
    for led in sorted(leds, key=lambda l: NM[l]):
        picks = [val[(n, led, lay)][0] for (le, lay) in cells if le == led]
        if picks:
            xs.append(NM[led])
            ys.append(np.mean(picks))
    ys = np.array(ys) / np.array(
        [np.mean([val[("C1005", led, lay)][0]
                  for (le, lay) in cells if le == led])
         for led in sorted(leds, key=lambda l: NM[l])
         if any(le == led for le, _ in cells)])
    ax.plot(xs, ys, "o-", label=f"{n}  {FILES[n]:g} ppb")
ax.axhline(1, color="k", lw=.5)
ax.set_xlabel("LED peak wavelength (nm)")
ax.set_ylabel("reflectance / C1005's")
ax.set_title("Same, divided by one 64 ppb strip\n(flat = same hue, "
             "different amount)")
ax.grid(alpha=.3)
ax.legend(fontsize=8)

ax = axes[2]
lp = np.log10([FILES[n] for n in ORDER])
ax.scatter(lp, s, s=90, zorder=3)
for i, n in enumerate(ORDER):
    ax.annotate(f"{n}", (lp[i], s[i]), fontsize=8,
                xytext=(6, -3), textcoords="offset points")
keep = [i for i, n in enumerate(ORDER) if n != "X15"]
b, a = np.polyfit(lp[keep], s[keep], 1)
xx = np.linspace(0.9, 2.5, 20)
ax.plot(xx, a + b * xx, "r--", label="fit without X15")
ax.set_xlabel("log10 ppb")
ax.set_ylabel("dye loading s (rank-1 score)")
ax.set_title("Dye loading vs concentration")
ax.grid(alpha=.3)
ax.legend(fontsize=8)
plt.tight_layout()
plt.savefig(SP / "fig/spectra.png", dpi=95)
print("\nwrote fig/spectra.png")
