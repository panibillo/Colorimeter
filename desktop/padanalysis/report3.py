"""report3.py -- is the edge effect photometric (shadow) or chemical (dye)?

Two tests.

CONTROL: the white paper below the pad is the same strip, the same height, the
same illumination, and no chemistry at all.  Profile its reflectance against
distance from the *paper's* own edge.  If the paper falls off at its edges the
way the pad does, the effect belongs to the strip's geometry -- it is standing
proud of the stage, so its rim is shadowed and partly turned away from the
camera -- and not to the reagent.

COLOR: a shadow is multiplicative and cancels in a ratio of two channels.
Profile blue/Gb divided by amber/R against depth.  Whatever survives is dye.
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
BINS = [0, 1, 2, 3, 4, 6, 8, 10, 13, 16, 24]
CENTRES = [0.5 * (a + b) for a, b in zip(BINS[:-1], BINS[1:])]
PAIRS = [("blue", "Gb"), ("green", "Gb"), ("amber", "R"), ("white", "R")]


def band_profile(sig, depth, div, bins):
    """Mean signal / divisor in each depth band."""
    out = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        b = (depth > lo) & (depth <= hi)
        out.append(float(sig[b].mean()) / div if b.sum() > 20 else np.nan)
    return np.array(out)


models = load_models()
fig, axes = plt.subplots(3, 5, figsize=(20, 11))
print("=" * 96)
print("CONTROL -- the unstained paper below the pad, profiled from ITS own edge")
print("(reflectance in each depth band as % of that region's deep interior)")
print("=" * 96)

colour_prof = {}
for j, name in enumerate(FILES):
    c = open_capture(name)
    pad = PadRect(c)
    shape = pad.tot.shape
    chip1 = rect_mask(shape, c.rects["gray1"])
    chip2 = rect_mask(shape, c.rects["gray2"])
    paper, paper_depth = pad.paper_mask()

    print(f"\n  {name} ({FILES[name]:g} ppb), paper area {int(paper.sum())} px")
    print(f"    {'cell':<14}" + "".join(f"{f'{a}-{b}':>7}"
                                        for a, b in zip(BINS[:-1], BINS[1:])))
    for led, layer in PAIRS:
        m = models.get((led, layer))
        got = c.layer(led, "e2", layer)
        if m is None or got is None:
            continue
        sig = got[0]
        w1, w2, *_ = anchor_weights(m, paper, chip1, chip2)
        div = w1 * float(sig[chip1].mean()) + w2 * float(sig[chip2].mean())
        p = band_profile(sig, paper_depth, div, BINS)
        ref = np.nanmean(p[-3:])
        print(f"    {led + '/' + layer:<14}" + "".join(
            "    nan" if not np.isfinite(v) else f"{(v / ref - 1) * 100:>7.1f}"
            for v in p))

    # ---- figure row 0: paper vs pad, one bright channel ----------------
    ax = axes[0, j]
    for region, depth, style, lbl in ((pad.mask(0), pad.depth, "o-", "pad"),
                                      (paper, paper_depth, "s--", "paper")):
        m = models[("amber", "R")]
        sig = c.layer("amber", "e2", "R")[0]
        w1, w2, *_ = anchor_weights(m, region, chip1, chip2)
        div = w1 * float(sig[chip1].mean()) + w2 * float(sig[chip2].mean())
        p = band_profile(sig, depth, div, BINS)
        ax.plot(CENTRES, (p / np.nanmean(p[-3:]) - 1) * 100, style, label=lbl)
    ax.axhline(0, color="k", lw=.5)
    ax.set_title(f"{name} {FILES[name]:g}ppb -- amber/R\npad vs unstained paper",
                 fontsize=9)
    ax.set_xlabel("depth from own edge (layer px)")
    ax.set_ylabel("% of deep interior")
    ax.grid(alpha=.3)
    ax.legend(fontsize=8)

    # ---- figure row 1: colour index vs depth ---------------------------
    ax = axes[1, j]
    prof = {}
    for led, layer in PAIRS:
        m = models[(led, layer)]
        sig = c.layer(led, "e2", layer)[0]
        w1, w2, *_ = anchor_weights(m, pad.mask(0), chip1, chip2)
        div = w1 * float(sig[chip1].mean()) + w2 * float(sig[chip2].mean())
        prof[(led, layer)] = band_profile(sig, pad.depth, div, BINS)
    idx = prof[("blue", "Gb")] / prof[("amber", "R")]
    idx2 = prof[("green", "Gb")] / prof[("amber", "R")]
    colour_prof[name] = idx
    ax.plot(CENTRES, (idx / np.nanmean(idx[-3:]) - 1) * 100, "o-",
            label="blue.Gb / amber.R")
    ax.plot(CENTRES, (idx2 / np.nanmean(idx2[-3:]) - 1) * 100, "s-",
            label="green.Gb / amber.R")
    ax.axhline(0, color="k", lw=.5)
    ax.axvline(4, color="r", lw=.5, ls=":")
    ax.set_title("colour index vs depth\n(shadow cancels; dye does not)",
                 fontsize=9)
    ax.set_xlabel("depth from pad edge")
    ax.set_ylabel("% of deep interior")
    ax.grid(alpha=.3)
    ax.legend(fontsize=7)

    # ---- figure row 2: the colour index as an image --------------------
    ax = axes[2, j]
    sb = c.layer("blue", "e2", "Gb")[0]
    sa = c.layer("amber", "e2", "R")[0]
    with np.errstate(invalid="ignore", divide="ignore"):
        img = np.where(pad.mask(0), sb / np.maximum(sa, 1.0), np.nan)
    v = img[np.isfinite(img)]
    im = ax.imshow(img, cmap="turbo",
                   vmin=np.percentile(v, 2), vmax=np.percentile(v, 98))
    plt.colorbar(im, ax=ax, fraction=0.03)
    ax.set_xlim(55, 135)
    ax.set_ylim(95, 10)
    ax.set_title("blue.Gb / amber.R over the pad", fontsize=9)
    c.close()

print()
print("=" * 96)
print("COLOUR INDEX blue.Gb / amber.R vs depth (% of deep interior)")
print("=" * 96)
print(f"  {'capture':<8}" + "".join(f"{f'{a}-{b}':>8}"
                                    for a, b in zip(BINS[:-1], BINS[1:])))
for name, idx in colour_prof.items():
    ref = np.nanmean(idx[-3:])
    print(f"  {name:<8}" + "".join(f"{(v / ref - 1) * 100:>8.1f}" for v in idx))

plt.tight_layout()
plt.savefig(SP / "fig/edge_origin.png", dpi=95)
print("\nwrote fig/edge_origin.png")
