"""look3.py -- the pads as they look, beside what the two-dye model made of them.

Nothing here is a measurement.  It exists to be approved or rejected by eye:
if the middle row does not agree with what the top row plainly shows, the
model in `duotone.py` is wrong and no ranking built on it is worth reading.

Row 1  True colour from the white LED, white-balanced on the gray chip.
Row 2  Every pad pixel painted white / yellow / brown, over a dimmed copy of
       row 1 so the pad's place on the strip stays visible.
Row 3  Brown fraction b/(y+b) inside the dyed pixels only, which is the
       continuous quantity the labels in row 2 were cut from.

Figure: fig/look3.png
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import duotone  # noqa: E402

FIG = Path(__file__).resolve().parent / "fig"

NAMES = ["C1002", "C1003", "C1004", "C1005", "C1008", "C1009", "C1014"]

#: What each class is painted.  Chosen to look like what it claims to be, so
#: the eye can compare row 2 against row 1 without a lookup.
PAINT = {
    "white": (0.93, 0.93, 0.90),
    "yellow": (0.93, 0.78, 0.18),
    "brown": (0.42, 0.26, 0.11),
}

#: Pixels this far outside the pad rectangle are shown for context.
MARGIN = 10

#: Layer px trimmed off the pad edge before painting.  Zero, deliberately:
#: this figure exists to be checked against the eye, and trimming would hide
#: exactly the pixels most worth arguing about -- `report4.py` found the rim
#: carries real chemistry, while a pixel straddling the pad's boundary is
#: part pad and part paper and no spectral model can unmix a spatial mixture.
#: The printed table gives the counts at 0, 2 and 4 px so the difference
#: between those two readings of the rim is visible rather than assumed.
DISPLAY_ERODE = 0.0
COUNT_ERODES = (0.0, 2.0, 4.0)


def crop_box(cube, margin: int = MARGIN):
    """Return the (rows, cols) slices framing one pad with a margin."""
    ys, xs = np.nonzero(cube.mask)
    r0 = max(int(ys.min()) - margin, 0)
    r1 = min(int(ys.max()) + margin + 1, cube.shape[0])
    c0 = max(int(xs.min()) - margin, 0)
    c1 = min(int(xs.max()) + margin + 1, cube.shape[1])
    return slice(r0, r1), slice(c0, c1)


def main(floor_sigma: float = 3.0, brown_at: float = 0.5):
    """
    Draw the three rows for every capture.

    Args:
        floor_sigma: How many propagated sigmas of dye a pixel needs before
            it stops being called white.
        brown_at: Brown fraction at which a dyed pixel is called brown.
    """
    FIG.mkdir(exist_ok=True)
    cubes = duotone.build(NAMES)
    eps_y, eps_b, info = duotone.fit(cubes)
    print(f"{len(info['cells'])} shared cells; contrast variance "
          f"PC1 {info['explained'][0]:.3f} PC2 {info['explained'][1]:.3f} "
          f"PC3 {info['explained'][2]:.3f}")

    cmap = ListedColormap([PAINT[c] for c in duotone.CLASSES])
    fig, axes = plt.subplots(3, len(NAMES), figsize=(3.0 * len(NAMES), 10.5),
                             layout="constrained")

    for j, name in enumerate(NAMES):
        cube = cubes[name]
        g, y, b = duotone.unmix(cube.A, eps_y, eps_b)
        s_y, s_b, s_tot = duotone.amount_sigma(cube.sigma, eps_y, eps_b)
        floor = floor_sigma * s_tot
        lab = duotone.classify(y, b, floor=floor, brown_at=brown_at)
        shown = cube.depth > DISPLAY_ERODE
        counts = {c: int(((lab == i) & shown).sum())
                  for i, c in enumerate(duotone.CLASSES)}

        rs, cs = crop_box(cube)
        seen = np.zeros(cube.shape, bool)
        seen[rs, cs] = True
        rgb = duotone.render_rgb(cube, region=seen)

        ax = axes[0, j]
        ax.imshow(rgb[rs, cs], interpolation="nearest")
        ax.set_title(f"{name}\n{len(y)} pad px", fontsize=9)

        # Row 2: labels over a dimmed grayscale of the same pixels, so the
        # pad's surroundings stay legible without competing for attention.
        gray = rgb[rs, cs].mean(axis=-1)
        under = np.repeat((0.25 + 0.35 * gray)[..., None], 3, axis=-1)
        painted = under.copy()
        # -1 for the trimmed edge pixels: they are in the fit but not in the
        # picture, and matching no class leaves them showing the backdrop.
        lab_img = cube.paint(np.where(shown, lab, -1).astype(float))[rs, cs]
        for i, cls in enumerate(duotone.CLASSES):
            painted[lab_img == i] = PAINT[cls]
        ax = axes[1, j]
        ax.imshow(painted, interpolation="nearest")
        ax.set_title(f"w {counts['white']}  y {counts['yellow']}  "
                     f"b {counts['brown']}", fontsize=9)

        # Row 3: the continuum the labels came from.  White pixels are left
        # blank -- a brown fraction of nothing is not a number.
        total = y + b
        frac = np.where((total >= floor) & shown,
                        b / np.maximum(total, 1e-12), np.nan)
        ax = axes[2, j]
        im = ax.imshow(cube.paint(frac)[rs, cs], cmap="YlOrBr",
                       vmin=0, vmax=1, interpolation="nearest")
        ax.set_title(f"brown fraction, cut at {brown_at:.2f}", fontsize=9)
        if j == len(NAMES) - 1:
            plt.colorbar(im, ax=axes[2, :].tolist(), fraction=0.02,
                         label="b / (y + b)")

        print(f"{name}: floor={floor:.4f} A ({floor_sigma:.1f} sigma of "
              f"{s_tot:.4f})  resid="
              f"{duotone.residual_rms(cube.A, eps_y, eps_b, y, b):.4f} A")
        for e in COUNT_ERODES:
            keep = cube.depth > e
            n = [int(((lab == i) & keep).sum())
                 for i in range(len(duotone.CLASSES))]
            mark = "  <- drawn" if e == DISPLAY_ERODE else ""
            print(f"    erode {e:4.1f} px: {keep.sum():5d} px   "
                  f"white {n[0]:5d}  yellow {n[1]:5d}  brown {n[2]:5d}{mark}")

    for ax in axes.ravel():
        ax.set_xticks([])
        ax.set_yticks([])

    axes[1, 0].legend(
        handles=[Patch(facecolor=PAINT[c], edgecolor="k", label=c)
                 for c in duotone.CLASSES],
        loc="lower left", fontsize=7, framealpha=0.85)
    fig.suptitle(
        "look3 -- true colour (top), the model's white/yellow/brown call "
        f"(middle), and the brown fraction it was cut from (bottom).  "
        f"Pad edge trimmed {DISPLAY_ERODE:.0f} px for the picture; the fit "
        "used every pad pixel.", fontsize=12)

    out = FIG / "look3.png"
    fig.savefig(out, dpi=120)
    print(f"wrote {out}")


if __name__ == "__main__":
    main(*[float(a) for a in sys.argv[1:]])
