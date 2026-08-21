"""look1.py -- first look at the layer data: brightness and blueness fields."""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import Capture, FILES, open_capture  # noqa: E402

SP = Path(__file__).resolve().parent


def fields(c, label="e1"):
    """Return (total, blueness) from the white LED at one exposure rung."""
    lay = {}
    for ch in ("R", "Gr", "Gb", "B"):
        got = c.layer("white", label, ch)
        lay[ch] = got[0] if got else None
    tot = lay["R"] + lay["Gr"] + lay["Gb"] + lay["B"]
    g = 0.5 * (lay["Gr"] + lay["Gb"])
    with np.errstate(divide="ignore", invalid="ignore"):
        blue = np.where(tot > 30, lay["B"] / np.maximum(g, 1e-6), np.nan)
    return tot, blue, lay


fig, axes = plt.subplots(3, 5, figsize=(20, 11))
for j, name in enumerate(FILES):
    c = open_capture(name)
    tot, blue, lay = fields(c)
    sx, sy, sw, sh = c.rects["sample"]

    ax = axes[0, j]
    im = ax.imshow(tot, cmap="viridis")
    ax.add_patch(plt.Rectangle((sx, sy), sw, sh, ec="r", fc="none", lw=1))
    ax.set_title(f"{name}  {FILES[name]:g} ppb\nwhite e1 total")
    plt.colorbar(im, ax=ax, fraction=0.03)

    ax = axes[1, j]
    im = ax.imshow(blue, cmap="coolwarm", vmin=0.2, vmax=1.2)
    ax.add_patch(plt.Rectangle((sx, sy), sw, sh, ec="k", fc="none", lw=1))
    ax.set_title("B / G  (low = yellow pad)")
    plt.colorbar(im, ax=ax, fraction=0.03)

    ax = axes[2, j]
    # column profile across the strip, rows 20..60; row profile down the strip
    colp = np.nanmedian(tot[15:70, :], axis=0)
    ax.plot(colp / colp.max(), label="col prof tot (rows15-70)")
    rowp = np.nanmedian(blue[:, sx + 5:sx + sw - 5], axis=1)
    ax.plot(rowp, label="row prof B/G (sample cols)")
    rowt = np.nanmedian(tot[:, sx + 5:sx + sw - 5], axis=1)
    ax.plot(rowt / np.nanmax(rowt), label="row prof tot")
    ax.legend(fontsize=6)
    ax.grid(alpha=.3)
    ax.set_xlim(0, 183)
    c.close()

plt.tight_layout()
plt.savefig(SP / "fig/look1.png", dpi=90)
print("wrote fig/look1.png")
