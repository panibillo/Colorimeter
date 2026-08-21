"""look2.py -- validate the pad segmentation visually."""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import Capture, FILES, open_capture  # noqa: E402
from segment import Pad  # noqa: E402

SP = Path(__file__).resolve().parent

fig, axes = plt.subplots(3, 5, figsize=(19, 12))
for j, name in enumerate(FILES):
    c = open_capture(name)
    p = Pad(c)
    sx, sy, sw, sh = c.rects["sample"]

    ax = axes[0, j]
    ax.imshow(p.tot, cmap="gray")
    ax.contour(p.strip, levels=[0.5], colors="c", linewidths=1)
    ax.contour(p.pad, levels=[0.5], colors="r", linewidths=1)
    ax.add_patch(plt.Rectangle((sx, sy), sw, sh, ec="y", fc="none", lw=1))
    ax.set_title(f"{name} {FILES[name]:g}ppb  strip(c) pad(r) seed(y)", fontsize=9)

    ax = axes[1, j]
    im = ax.imshow(np.where(p.pad, p.blue, np.nan), cmap="magma")
    plt.colorbar(im, ax=ax, fraction=0.03)
    ax.set_title("B/G inside pad", fontsize=9)

    ax = axes[2, j]
    im = ax.imshow(np.where(p.pad, p.depth, np.nan), cmap="viridis")
    for k in (3, 6, 9):
        ax.contour(p.mask(k), levels=[0.5], colors="w", linewidths=.7)
    plt.colorbar(im, ax=ax, fraction=0.03)
    ax.set_title("depth, erode 3/6/9", fontsize=9)
    c.close()

plt.tight_layout()
plt.savefig(SP / "fig/look2.png", dpi=90)
print("wrote fig/look2.png")
