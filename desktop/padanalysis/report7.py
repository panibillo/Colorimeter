"""report7.py -- the geometry-constrained rectangle against the old blob.

Row 1  white-LED colour, the old `segment.Pad` outline (red) and the new
       `padrect.PadRect` rectangle (cyan), titled with how sharp each strip
       edge is (`padrect.edge_softness`).
Row 2  what the old mask claimed that the rectangle rejects, and vice versa.
Row 3  the per-row edge crossings the rectangle was fitted to.
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import Capture, open_capture  # noqa: E402
from samples import WHITE_LED_SET as FILES  # noqa: E402
from segment import Pad, white_fields  # noqa: E402
from padrect import PadRect, edge_softness  # noqa: E402

SP = Path(__file__).resolve().parent
X0, X1, Y0, Y1 = 60, 130, 14, 116

fig, axes = plt.subplots(3, len(FILES), figsize=(4.2 * len(FILES), 13))
print("Only the captures that stored the white LED can be compared: the "
      "free-form\nfinder reads B/G under white illumination and the "
      "2026-08-13 set has none.\n")
print(f"{'cap':>6} {'old area':>9} {'new area':>9} {'old only':>9} "
      f"{'new only':>9} {'old outside rect':>17}")
for j, name in enumerate(FILES):
    c = open_capture(name)
    old = Pad(c)
    new = PadRect(c)
    tot, blue, lay = white_fields(c, "e1")
    rgb = np.dstack([lay["R"], .5 * (lay["Gr"] + lay["Gb"]), lay["B"]])
    rgb = np.clip(rgb / np.percentile(rgb[Y0:Y1, X0:X1], 99), 0, 1) ** (1 / 2.0)
    ext = [X0 - .5, X1 - .5, Y1 - .5, Y0 - .5]
    gx, gy = np.arange(X0, X1), np.arange(Y0, Y1)
    sub = (slice(Y0, Y1), slice(X0, X1))

    ax = axes[0, j]
    ax.imshow(rgb[sub], extent=ext, interpolation="nearest")
    ax.contour(gx, gy, old.pad[sub], levels=[.5], colors="r", linewidths=1.2)
    ax.add_patch(Polygon(new.corners(), closed=True, ec="c", fc="none", lw=1.2))
    sl, sr = edge_softness(new)
    ax.set_title(f"{name} {FILES[name]:g} ppb   old(r)  rect(c)\n"
                 f"edge transition {sl:.1f} / {sr:.1f} px", fontsize=9)

    m_old, m_new = old.pad, new.mask(0)
    diff = np.zeros(m_old.shape)
    diff[m_old & ~m_new] = 1.0
    diff[~m_old & m_new] = -1.0
    ax = axes[1, j]
    ax.imshow(diff[sub], extent=ext, cmap="bwr", vmin=-1, vmax=1,
              interpolation="nearest")
    ax.add_patch(Polygon(new.corners(), closed=True, ec="k", fc="none", lw=.8))
    only_old = int((m_old & ~m_new).sum())
    only_new = int((~m_old & m_new).sum())
    ax.set_title(f"old only {only_old} (red)   rect only {only_new} (blue)",
                 fontsize=9)

    ax = axes[2, j]
    ax.plot(new.rows_used, new.L_obs, ".", ms=4, color="#2b6cb0", label="left")
    ax.plot(new.rows_used, new.R_obs, ".", ms=4, color="#b7791f", label="right")
    yy = new.rows_used - new.yc
    ax.plot(new.rows_used, new.aL + new.m * yy, "-", lw=1, color="#2b6cb0")
    ax.plot(new.rows_used, new.aR + new.m * yy, "-", lw=1, color="#b7791f")
    ax.set_title(f"W={new.width:.2f}  H={new.height:.2f}  "
                 f"tilt={new.angle_deg:+.2f}deg  rms={new.edge_rms:.2f}px",
                 fontsize=9)
    ax.set_xlabel("row"); ax.grid(alpha=.3); ax.legend(fontsize=8)

    print(f"{name:>6} {int(m_old.sum()):9d} {int(m_new.sum()):9d} "
          f"{only_old:9d} {only_new:9d} "
          f"{100 * only_old / m_old.sum():16.1f}%")
    c.close()

plt.tight_layout()
plt.savefig(SP / "fig/padrect.png", dpi=85)
print("\nwrote fig/padrect.png")
