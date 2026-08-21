"""pca.py -- how many dimensions are there, between samples and within a pad?

Two PCAs on the same quantity, log10 of the white-corrected reflectance:

  A. BETWEEN SAMPLES.  One 27-vector per capture (the pad mean at erode 10).
     Five points, so rank <= 4; the question it can still answer is how
     redundant the 27 cells are and which of them carry the concentration.

  B. WITHIN AND BETWEEN PADS, PER PIXEL.  Every pad pixel is its own 27-vector.
     Thousands of points, and the interesting structure is that the rim
     artifact and the dye load push along *different* directions: a shadow
     scales every channel together, a dye does not.  If PC1 is the shadow then
     projecting it out gives a dye estimate that does not care where the pad
     edge is -- which is the thing the erosion depth was buying, bought a
     better way.
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import Capture, FILES, open_capture  # noqa: E402
from padrect import PadRect  # noqa: E402
from whitecorr import load_models, rect_mask, anchor_weights  # noqa: E402

SP = Path(__file__).resolve().parent
ORDER = ["X15", "C1002", "C1003", "C1004", "C1005"]
ERODE = 10.0
MIN_SIGNAL_DN = 25.0
SATURATED_DN = 1000


def build(models):
    """Return (cells, per-capture pixel cubes, per-capture depth vectors)."""
    cells, cubes, depths, means = None, {}, {}, {}
    for name in ORDER:
        c = open_capture(name)
        pad = PadRect(c)
        shape = pad.tot.shape
        chip1 = rect_mask(shape, c.rects["gray1"])
        chip2 = rect_mask(shape, c.rects["gray2"])
        region = pad.mask(0)                     # every pad pixel, rim included
        core = pad.mask(ERODE)

        planes, names, mvals = [], [], []
        for (led, layer), m in sorted(models.items()):
            if not m.usable:
                continue
            w1, w2, *_ = anchor_weights(m, region, chip1, chip2)
            best = None
            for rung in ("e0", "e1", "e2"):
                got = c.layer(led, rung, layer)
                if got is None:
                    continue
                sig, raw = got
                if (raw[region | chip1 | chip2] >= SATURATED_DN).any():
                    continue
                if sig[core].mean() < MIN_SIGNAL_DN:
                    continue
                if best is None or sig[core].mean() > best[0][core].mean():
                    best = (sig, rung)
            if best is None:
                continue
            sig = best[0]
            div = w1 * float(sig[chip1].mean()) + w2 * float(sig[chip2].mean())
            planes.append(np.log10(np.maximum(sig, 1.0) / div))
            names.append(f"{led}.{layer}")
            w1c, w2c, *_ = anchor_weights(m, core, chip1, chip2)
            divc = w1c * float(sig[chip1].mean()) + w2c * float(sig[chip2].mean())
            mvals.append(np.log10(float(sig[core].mean()) / divc))
        if cells is None:
            cells = names
        keep = [names.index(n) for n in cells]
        cube = np.stack([planes[i] for i in keep], axis=-1)
        cubes[name] = cube[region]                       # (n_px, n_cells)
        depths[name] = pad.depth[region]
        means[name] = np.array([mvals[i] for i in keep])
        print(f"  {name}: {cubes[name].shape[0]} pad px x "
              f"{cubes[name].shape[1]} cells")
        c.close()
    return cells, cubes, depths, means


print("building pixel cubes ...")
models = load_models()
CELLS, CUBES, DEPTHS, MEANS = build(models)
NC = len(CELLS)

# ── A. between-sample PCA ────────────────────────────────────────────
X = np.stack([MEANS[n] for n in ORDER])            # (5, NC)
Xc = X - X.mean(0)
U, S, Vt = np.linalg.svd(Xc, full_matrices=False)
var = S ** 2 / (S ** 2).sum()
print()
print("=" * 84)
print("A.  BETWEEN-SAMPLE PCA on log10 reflectance, 5 captures x "
      f"{NC} cells (erode {ERODE:g})")
print("=" * 84)
print("  explained variance: " +
      "  ".join(f"PC{i + 1} {v * 100:.2f}%" for i, v in enumerate(var)))
print()
print(f"  {'capture':<8}{'ppb':>6}" +
      "".join(f"{f'PC{i + 1}':>10}" for i in range(4)))
scores = U * S
for i, n in enumerate(ORDER):
    print(f"  {n:<8}{FILES[n]:>6.0f}" +
          "".join(f"{scores[i, j]:>10.4f}" for j in range(4)))
print()
print("  PC1 loading by cell (all one sign = a single dye axis):")
o = np.argsort(-np.abs(Vt[0]))
for i in o:
    bar = "#" * int(abs(Vt[0, i]) * 60)
    print(f"    {CELLS[i]:<16}{Vt[0, i]:>8.3f}  {bar}")

# ── correlation of each cell with log(ppb) ───────────────────────────
lp = np.log10([FILES[n] for n in ORDER])
print()
print("  correlation of each cell with log10(ppb), and with X15 dropped:")
print(f"    {'cell':<16}{'r (all 5)':>11}{'r (no X15)':>12}"
      f"{'slope/decade':>14}")
keep = [i for i, n in enumerate(ORDER) if n != "X15"]
for i in np.argsort(-np.abs([np.corrcoef(X[keep, k], lp[keep])[0, 1]
                             for k in range(NC)])):
    r_all = np.corrcoef(X[:, i], lp)[0, 1]
    r_no = np.corrcoef(X[keep, i], lp[keep])[0, 1]
    sl = np.polyfit(lp[keep], X[keep, i], 1)[0]
    print(f"    {CELLS[i]:<16}{r_all:>11.3f}{r_no:>12.3f}{sl:>14.4f}")

# ── B. pixel-level PCA ───────────────────────────────────────────────
P = np.vstack([CUBES[n] for n in ORDER])
tag = np.concatenate([[i] * CUBES[n].shape[0] for i, n in enumerate(ORDER)])
dep = np.concatenate([DEPTHS[n] for n in ORDER])
Pc = P - P.mean(0)
U2, S2, Vt2 = np.linalg.svd(Pc, full_matrices=False)
var2 = S2 ** 2 / (S2 ** 2).sum()
print()
print("=" * 84)
print(f"B.  PIXEL-LEVEL PCA, {P.shape[0]} pad pixels x {NC} cells, all "
      "five captures pooled")
print("=" * 84)
print("  explained variance: " +
      "  ".join(f"PC{i + 1} {v * 100:.2f}%" for i, v in enumerate(var2[:5])))
sc = Pc @ Vt2[:3].T
print()
print("  PC1 vs PC2 loadings:")
print(f"    {'cell':<16}{'PC1':>9}{'PC2':>9}{'PC3':>9}")
for i in range(NC):
    print(f"    {CELLS[i]:<16}{Vt2[0, i]:>9.3f}{Vt2[1, i]:>9.3f}"
          f"{Vt2[2, i]:>9.3f}")
print()
print("  correlation of each PC score with depth-from-edge:")
for j in range(3):
    print(f"    PC{j + 1}: r = {np.corrcoef(sc[:, j], dep)[0, 1]:+.3f}")

print()
print("  per-capture PC scores, pixels with depth > 10 (rim excluded):")
print(f"    {'capture':<8}{'ppb':>6}{'PC1':>10}{'PC2':>10}{'PC3':>10}")
for i, n in enumerate(ORDER):
    m = (tag == i) & (dep > ERODE)
    print(f"    {n:<8}{FILES[n]:>6.0f}" +
          "".join(f"{sc[m, j].mean():>10.4f}" for j in range(3)))
print()
print("  per-capture PC scores, ALL pad pixels (rim included):")
print(f"    {'capture':<8}{'ppb':>6}{'PC1':>10}{'PC2':>10}{'PC3':>10}")
for i, n in enumerate(ORDER):
    m = tag == i
    print(f"    {n:<8}{FILES[n]:>6.0f}" +
          "".join(f"{sc[m, j].mean():>10.4f}" for j in range(3)))

# ── figure ───────────────────────────────────────────────────────────
fig, axes = plt.subplots(2, 3, figsize=(17, 9))
ax = axes[0, 0]
ax.bar(range(1, 6), var[:5] * 100)
ax.set_title("A. between-sample scree")
ax.set_ylabel("% variance")
ax.set_xlabel("PC")

ax = axes[0, 1]
for i, n in enumerate(ORDER):
    ax.scatter(scores[i, 0], scores[i, 1], s=80)
    ax.annotate(f"{n}\n{FILES[n]:g}ppb", (scores[i, 0], scores[i, 1]),
                fontsize=8, xytext=(5, 5), textcoords="offset points")
ax.set_xlabel("PC1")
ax.set_ylabel("PC2")
ax.set_title("A. sample scores")
ax.grid(alpha=.3)

ax = axes[0, 2]
for i, n in enumerate(ORDER):
    ax.scatter(FILES[n], X[i, CELLS.index("blue.Gb")], s=70, label=n)
    ax.scatter(FILES[n], X[i, CELLS.index("green.Gb")], s=70, marker="s")
ax.set_xscale("log")
ax.set_xlabel("ppb")
ax.set_ylabel("log10 reflectance")
ax.set_title("A. blue.Gb (o) and green.Gb (s) vs ppb")
ax.grid(alpha=.3)

ax = axes[1, 0]
ax.bar(range(1, 6), var2[:5] * 100)
ax.set_title("B. pixel-level scree")
ax.set_ylabel("% variance")
ax.set_xlabel("PC")

ax = axes[1, 1]
for i, n in enumerate(ORDER):
    m = tag == i
    ax.scatter(sc[m, 0][::7], sc[m, 1][::7], s=2, alpha=.35,
               label=f"{n} {FILES[n]:g}ppb")
ax.set_xlabel("PC1 (pixel)")
ax.set_ylabel("PC2 (pixel)")
ax.set_title("B. every pad pixel")
ax.legend(fontsize=7, markerscale=4)
ax.grid(alpha=.3)

ax = axes[1, 2]
sub = slice(None, None, 7)
s2 = ax.scatter(sc[sub, 0], sc[sub, 1], c=np.minimum(dep[sub], 20), s=2,
                cmap="viridis")
plt.colorbar(s2, ax=ax, label="depth from pad edge")
ax.set_xlabel("PC1 (pixel)")
ax.set_ylabel("PC2 (pixel)")
ax.set_title("B. same points, coloured by depth")
ax.grid(alpha=.3)
plt.tight_layout()
plt.savefig(SP / "fig/pca.png", dpi=95)
print("\nwrote fig/pca.png")
