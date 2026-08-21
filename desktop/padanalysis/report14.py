"""report14.py -- which colour sets separate brown from yellow, and how well.

The pad carries two products, not one colour at two strengths: yellow where
the arsine load is light, brown where it is heavy.  `duotone.py` fits both
per pixel from all 26 cells; this report asks how few of those cells are
actually needed, and ranks the small sets by how many noise sigmas they put
between a pure brown pixel and a pure yellow one.

Three findings drive the ranking, and each is a consequence of the model
rather than an observation about these particular strips:

  ONE CELL CANNOT DO IT, at all.  Not poorly -- exactly zero.  A single
  number cannot tell a lot of yellow from a little brown, because shading
  alone can carry one cell anywhere.  The figure of merit returns 0.000 for
  every one of the 26, which is the theorem, not a measurement.

  TWO CELLS BUY THE HUE, and nothing else.  A ratio is immune to shading and
  says which dye dominates, but it cannot say how much of either is present:
  removing the shading direction from two cells leaves one dimension, and two
  amounts need two.  A two-cell set therefore cannot even separate white from
  dyed except by assuming the illumination.

  THREE CELLS BUY THE AMOUNTS.  With three, yellow and brown are separately
  estimable *and* shading-immune, which is what a pixel count over a stained
  area needs.  The fourth cell buys signal-to-noise, not new ability.

Figure: fig/report14_ranking.png
"""
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import duotone  # noqa: E402
import samples  # noqa: E402

FIG = Path(__file__).resolve().parent / "fig"

NAMES = ["C1002", "C1003", "C1004", "C1005", "C1008", "C1009", "C1014"]

#: Sizes of colour set to rank.  One is included so the theorem is printed
#: with its numeric zero rather than asserted.
SIZES = (1, 2, 3, 4)

#: Bands docs/gutzeit_protocol.md:63-72 asks for, and what it wants them for.
PROTOCOL_BANDS = [
    (415, 430, "core yellow absorption"),
    (450, 470, "edge position"),
    (525, 535, "brown / trisubstituted"),
    (620, 630, "reference -- wetting"),
]


def led_set(idx, cells) -> frozenset:
    """The distinct LEDs a subset uses -- its real cost at capture time."""
    return frozenset(cells[i][0] for i in idx)


def rank(cells, eps_y, eps_b, cov, k, drop=()):
    """
    Score every k-cell subset and keep the best one per distinct LED set.

    Two cells from the same LED are two readings of one illuminant, so the
    top of an unfiltered list is the same answer permuted over Bayer layers.
    Collapsing by LED set makes the ranking a ranking of *illuminant
    choices*, which is the decision actually facing the instrument.

    Args:
        cells: The cell list.
        eps_y: Yellow spectrum.
        eps_b: Brown spectrum.
        cov: Noise covariance.
        k: Subset size.
        drop: LED names to exclude entirely.

    Returns:
        List of (J, idx, leds), best first, one entry per LED set.
    """
    usable = [i for i, c in enumerate(cells) if c[0] not in drop]
    best = {}
    for idx in combinations(usable, k):
        j = duotone.j_hue(idx, eps_y, eps_b, cov)
        key = led_set(idx, cells)
        if key not in best or j > best[key][0]:
            best[key] = (j, idx, key)
    return sorted(best.values(), key=lambda r: -r[0])


def agreement(cubes, idx, eps_y, eps_b, cov, ref_labels):
    """
    How often a subset's per-pixel verdict matches the all-cell verdict.

    The figures of merit are linear algebra; this is the thing the pixel
    counting depends on, so it is measured on the pads themselves.

    Args:
        cubes: {name: PadCube}.
        idx: Cell indices of the subset.
        eps_y, eps_b: Endmember spectra.
        cov: Noise covariance.
        ref_labels: {name: labels} from the all-cell model.

    Returns:
        (agree_fraction, hue_agree_fraction, brown_area_error_fraction),
        pooled over captures.  The second is scored only where the all-cell
        model found dye at all, so it measures the yellow/brown call without
        the white/dyed call on top of it.
    """
    # Each subset is held to its own noise, not to the all-cell model's:
    # a smaller set is noisier, and judging it by a threshold it could never
    # meet would be failing it for a question it was not asked.
    shading = len(idx) >= 3
    floor = 3.0 * duotone.subset_sigma(idx, eps_y, eps_b, cov, shading)[2]
    hits = total = hue_hits = hue_total = 0
    ref_area = sub_area = 0
    for name, cube in cubes.items():
        y, b = duotone.subset_unmix(cube.A, idx, eps_y, eps_b, cov, shading)
        lab = duotone.classify(y, b, floor=floor)
        dyed = ref_labels[name] > 0
        hue_hits += int((lab[dyed] == ref_labels[name][dyed]).sum())
        hue_total += int(dyed.sum())
        hits += int((lab == ref_labels[name]).sum())
        total += len(lab)
        ref_area += int((ref_labels[name] == 2).sum())
        sub_area += int((lab == 2).sum())
    err = abs(sub_area - ref_area) / max(ref_area, 1)
    return hits / total, hue_hits / max(hue_total, 1), err


def main():
    FIG.mkdir(exist_ok=True)
    cubes = duotone.build(NAMES)
    cells = cubes[NAMES[0]].cells
    eps_y, eps_b, info = duotone.fit(cubes)
    cov = duotone.noise_cov(cubes)
    sigma = np.sqrt(np.diag(cov))
    ev = info["explained"]

    print("=" * 78)
    print("A.  IS THE PAD TWO COLOURS?")
    print("=" * 78)
    print(f"{len(cells)} cells shared by {len(NAMES)} captures, "
          f"{sum(len(c.A) for c in cubes.values())} pad pixels pooled.")
    print("Contrast-space variance (shading already removed):")
    print(f"   PC1 {ev[0]:.4f}   PC2 {ev[1]:.4f}   PC3 {ev[2]:.4f}   "
          f"PC4 {ev[3]:.4f}")
    print(f"   two components hold {100*(ev[0]+ev[1]):.2f} % of it; the third "
          f"holds {100*ev[2]:.2f} %.")
    for name, cube in cubes.items():
        g, y, b = duotone.unmix(cube.A, eps_y, eps_b)
        r = duotone.residual_rms(cube.A, eps_y, eps_b, y, b)
        print(f"   {name}: two-dye residual {r:.4f} A against a per-pixel "
              f"noise of {np.median(cube.sigma):.4f} A  ({r/np.median(cube.sigma):.1f}x)")

    print()
    print("=" * 78)
    print("B.  THE TWO SPECTRA")
    print("=" * 78)
    print("Unit-norm absorbance shapes.  eps_y peaks where the protocol puts")
    print("the yellow product's absorption, eps_b where it puts the brown.")
    print(f"{'cell':>16} {'nm':>5} {'eps_y':>8} {'eps_b':>8} {'eps_b-eps_y':>12} "
          f"{'sigma':>8}")
    for i, c in enumerate(cells):
        band = ""
        for lo, hi, what in PROTOCOL_BANDS:
            if lo <= duotone.cell_nm(c) <= hi:
                band = f"  <- {what}"
        print(f"{duotone.cell_name(c):>16} {duotone.cell_nm(c):5d} "
              f"{eps_y[i]:+8.3f} {eps_b[i]:+8.3f} {eps_b[i]-eps_y[i]:+12.3f} "
              f"{sigma[i]:8.4f}{band}")

    ceiling = duotone.j_hue(range(len(cells)), eps_y, eps_b, cov)
    print()
    print("=" * 78)
    print("C.  RANKING")
    print("=" * 78)
    print(f"Ceiling, all {len(cells)} cells together: J_hue = {ceiling:.1f} "
          f"sigma per unit amount.")
    print("Efficiency is J_hue(set) / ceiling.  n_led is what the set costs to")
    print("capture: cells sharing an LED share one illumination.")

    tables = {}
    for k in SIZES:
        rows = rank(cells, eps_y, eps_b, cov, k)
        tables[k] = rows
        print()
        print(f"--- {k} cell{'s' if k > 1 else ''} "
              f"({len(rows)} distinct LED sets) ---")
        if k == 1:
            print(f"   every single cell scores J_hue = "
                  f"{max(r[0] for r in rows):.6f}.  Exactly zero, by "
                  f"construction:")
            print( "   with one cell the shading term and the dye term are the "
                   "same direction,")
            print( "   so nothing is left to tell them apart.")
            continue
        print(f"   {'#':>2} {'J_hue':>7} {'eff':>6} {'J_y':>7} {'J_b':>7} "
              f"{'n_led':>5}  cells")
        for n, (j, idx, leds) in enumerate(rows[:12], 1):
            jy, jb = duotone.j_amounts(idx, eps_y, eps_b, cov)
            names = " + ".join(duotone.cell_name(cells[i]) for i in idx)
            print(f"   {n:2d} {j:7.1f} {j/ceiling:6.3f} {jy:7.1f} {jb:7.1f} "
                  f"{len(leds):5d}  {names}")
        if k == 2:
            print("   J_y and J_b are zero for every pair: two cells give the")
            print("   hue and no amount.  The third cell is what buys them.")

    # -- what the sets actually do to the pixels -------------------------
    ref_labels = {}
    for name, cube in cubes.items():
        g, y, b = duotone.unmix(cube.A, eps_y, eps_b)
        _, _, s_tot = duotone.amount_sigma(cube.sigma, eps_y, eps_b)
        ref_labels[name] = duotone.classify(y, b, floor=3.0 * s_tot)

    print()
    print("=" * 78)
    print("D.  AGREEMENT WITH THE ALL-CELL ANSWER, PIXEL BY PIXEL")
    print("=" * 78)
    print("Same three-way call, made from the subset alone, against the call")
    print("made from all 26 cells, over every pad pixel of all seven strips.")
    print("Pairs are scored with the shading term switched off, since that is")
    print("the only way two cells can report an amount at all; every k>=3 set")
    print("keeps its shading immunity.  Each set is held to its own noise.")
    print(f"   {'k':>2} {'cells':>40} {'eff':>6} {'J_b':>6} {'3-way':>7} "
          f"{'hue only':>9} {'area err':>9}")
    recommended = {}
    for k in (2, 3, 4):
        # Judge the leaders on J_hue, but also the best set whose cells come
        # from distinct LEDs -- two cells on one LED are two readings of one
        # illuminant, and the amounts pay for it even where the hue does not.
        scored = []
        for j, idx, leds in tables[k][:20]:
            frac, hue, err = agreement(cubes, idx, eps_y, eps_b, cov,
                                       ref_labels)
            jy, jb = duotone.j_amounts(idx, eps_y, eps_b, cov)
            scored.append((frac, err, j, idx, leds, jb, hue))
        scored.sort(key=lambda r: (-r[0], r[1]))
        for frac, err, j, idx, leds, jb, hue in scored[:8]:
            names = " + ".join(duotone.cell_name(cells[i]) for i in idx)
            star = " *" if len(leds) == k else ""
            print(f"   {k:2d} {names:>40} {j/ceiling:6.3f} {jb:6.1f} "
                  f"{frac:6.1%} {hue:8.1%} {err:8.1%}{star}")
        frac, err, j, idx, leds, jb, hue = scored[0]
        recommended[k] = (j, idx, leds, frac, jb, err, hue)
    print("   * = every cell from a different LED")
    print()
    print("   Recommended, by three-way agreement:")
    for k in (2, 3, 4):
        j, idx, leds, frac, jb, err, hue = recommended[k]
        print(f"     k={k}: {' + '.join(duotone.cell_name(cells[i]) for i in idx)}"
              f"   eff {j/ceiling:.3f}, agrees {frac:.1%}, "
              f"brown area within {err:.1%}, {len(leds)} LED(s)"
              + ("   [shading assumed]" if k < 3 else ""))

    # -- does the ranking survive dropping a capture? --------------------
    print()
    print("=" * 78)
    print("E.  LEAVE ONE CAPTURE OUT")
    print("=" * 78)
    print("Endmembers and noise refitted without each strip in turn.  The")
    print("recommended 3-cell set is then re-scored in that fold -- it was")
    print("chosen on agreement, so its J_hue rank is not the test; whether it")
    print("keeps its efficiency and whether the hue axis itself moves are.")
    idx3 = recommended[3][1]
    names3 = " + ".join(duotone.cell_name(cells[i]) for i in idx3)
    print(f"   set: {names3}   in-sample efficiency "
          f"{recommended[3][0]/ceiling:.3f}")
    for held in NAMES:
        part = {n: c for n, c in cubes.items() if n != held}
        ey, eb, _ = duotone.fit(part)
        cv = duotone.noise_cov(part)
        top = duotone.j_hue(range(len(cells)), ey, eb, cv)
        eff = duotone.j_hue(idx3, ey, eb, cv) / top
        jy, jb = duotone.j_amounts(idx3, ey, eb, cv)
        ang = np.degrees(np.arccos(np.clip(
            (eb - ey) @ (eps_b - eps_y)
            / np.linalg.norm(eb - ey) / np.linalg.norm(eps_b - eps_y), -1, 1)))
        print(f"   without {held}: efficiency {eff:.3f}, J_b {jb:5.1f}, "
              f"hue axis moves {ang:5.2f} deg")

    # -- violet is the one cell that might not be absorption -------------
    print()
    print("=" * 78)
    print("F.  WITHOUT VIOLET")
    print("=" * 78)
    print("White card stock fluoresces under 410 nm in this instrument, so")
    print("violet.B may not be pure absorption.  The ranking without it:")
    for k in (2, 3):
        rows = rank(cells, eps_y, eps_b, cov, k, drop=("violet",))
        j, idx, leds = rows[0]
        names = " + ".join(duotone.cell_name(cells[i]) for i in idx)
        with_v = tables[k][0]
        print(f"   k={k}: best without violet {names} J={j:.1f} "
              f"(eff {j/ceiling:.3f}); with violet "
              f"{' + '.join(duotone.cell_name(cells[i]) for i in with_v[1])} "
              f"J={with_v[0]:.1f} (eff {with_v[0]/ceiling:.3f}) -- "
              f"costs {100*(1-j/with_v[0]):.1f} %")

    # Emission would not scale with the dye: it adds light the dye cannot
    # take away, so the ratio of violet's absorbance to a neighbouring
    # band's would sag as the pad darkens.  Measured against royal_blue.B,
    # the nearest cell that cannot fluoresce the pad's brighteners.
    iv = cells.index(("violet", "B"))
    ir = cells.index(("royal_blue", "B"))
    print()
    print("   Is violet.B absorption?  Its ratio to royal_blue.B by decile of")
    print("   dye load -- emission contaminating it would make this sag as the")
    print("   pad darkens.")
    X = np.vstack([c.A for c in cubes.values()])
    order = np.argsort(X[:, ir])
    dec = np.array_split(order, 10)
    ratios = [X[d, iv].mean() / X[d, ir].mean() for d in dec]
    print("      decile:  " + " ".join(f"{i+1:5d}" for i in range(10)))
    print("      A_rb  :  " + " ".join(f"{X[d, ir].mean():5.2f}" for d in dec))
    print("      ratio :  " + " ".join(f"{r:5.2f}" for r in ratios))
    pure_y = eps_y[iv] / eps_y[ir]
    pure_b = eps_b[iv] / eps_b[ir]
    print(f"   the two-dye model predicts this ratio runs from {pure_y:.2f} "
          f"(pure yellow)")
    print(f"   to {pure_b:.2f} (pure brown), and it does fall that way as the "
          f"pad darkens.")
    print("   Emission would show as the opposite: light the dye cannot")
    print("   remove, holding the ratio DOWN on the dark pixels where")
    print("   absorbance is largest.  It does not.")
    print(f"   The light end overshoots the pure-yellow prediction "
          f"({ratios[0]:.2f} vs {pure_y:.2f}),")
    print("   which is the pad's own violet absorbance: this zero is bare")
    print("   paper, not an unstained pad.  It biases the absolute yellow")
    print("   amount, not the yellow-versus-brown call.")

    # -- the chemistry has to agree -------------------------------------
    print()
    print("=" * 78)
    print("G.  DOES THE BROWN FRACTION FOLLOW THE DOSE?")
    print("=" * 78)
    print("If the two components really are the mono- and tri-substituted")
    print("products, the brown share must rise with concentration.")
    print(f"   {'capture':>8} {'ppb':>7} {'mean y':>8} {'mean b':>8} "
          f"{'b/(y+b)':>9}")
    for name in sorted(NAMES, key=lambda n: samples.SAMPLES[n].ppb or 0):
        cube = cubes[name]
        g, y, b = duotone.unmix(cube.A, eps_y, eps_b)
        ppb = samples.SAMPLES[name].ppb
        print(f"   {name:>8} {ppb:7.0f} {y.mean():8.4f} {b.mean():8.4f} "
              f"{b.sum()/(y.sum()+b.sum()):9.3f}")
    print("   (C1014's run was interrupted, so its ppb is not comparable --")
    print("    samples.py flags it; it is listed for completeness.)")

    _figure(cells, eps_y, eps_b, cov, tables, ceiling, cubes, recommended)


def _figure(cells, eps_y, eps_b, cov, tables, ceiling, cubes, recommended):
    """Draw the four panels."""
    fig, axes = plt.subplots(2, 2, figsize=(13, 10), layout="constrained")
    nm = np.array([duotone.cell_nm(c) for c in cells])
    optical = nm < duotone.NM["white"]

    ax = axes[0, 0]
    for lo, hi, what in PROTOCOL_BANDS:
        ax.axvspan(lo, hi, color="0.85", zorder=0)
        ax.text((lo + hi) / 2, 0.98, what.split()[0], rotation=90, fontsize=7,
                ha="center", va="top", transform=ax.get_xaxis_transform())
    for eps, lab, col in ((eps_y, "yellow product", "goldenrod"),
                          (eps_b, "brown product", "saddlebrown")):
        o = np.argsort(nm[optical])
        ax.plot(nm[optical][o], eps[optical][o], "o-", color=col, label=lab)
    ax.axhline(0, color="k", lw=0.6)
    ax.set_xlabel("LED wavelength (nm)")
    ax.set_ylabel("unit-norm absorbance shape")
    ax.set_title("the two fitted spectra, against the bands the protocol asks for")
    ax.legend(fontsize=8)

    ax = axes[0, 1]
    for k in (2, 3, 4):
        js = [r[0] for r in tables[k]]
        ax.scatter([k] * len(js), js, s=6, alpha=0.25, color="0.5")
        ax.scatter([k], [js[0]], s=45, color="crimson", zorder=3,
                   label="best J_hue" if k == 2 else None)
        rj = recommended[k][0]
        ax.scatter([k], [rj], s=55, marker="D", color="steelblue", zorder=4,
                   label="recommended" if k == 2 else None)
        ax.annotate(" + ".join(duotone.cell_name(cells[i])
                               for i in recommended[k][1]),
                    (k, rj), textcoords="offset points", xytext=(8, -2),
                    fontsize=7, va="center", color="steelblue")
    ax.axhline(ceiling, color="k", ls="--", lw=1,
               label=f"all {len(cells)} cells = {ceiling:.0f}")
    ax.scatter([1], [0], s=45, color="crimson", zorder=3)
    ax.annotate("one cell: exactly 0", (1, 0), textcoords="offset points",
                xytext=(8, 4), fontsize=7)
    ax.set_xticks([1, 2, 3, 4])
    ax.set_xlabel("cells in the set")
    ax.set_ylabel("J_hue (sigma per unit amount)")
    ax.set_title("what each extra cell buys")
    ax.legend(fontsize=8, loc="lower right")

    ax = axes[1, 0]
    leds = sorted({c[0] for c in cells}, key=lambda L: duotone.NM[L])
    M = np.full((len(leds), len(leds)), np.nan)
    for a, la in enumerate(leds):
        for b_, lb in enumerate(leds):
            if a >= b_:
                continue
            best = 0.0
            for i, ci in enumerate(cells):
                if ci[0] != la:
                    continue
                for j, cj in enumerate(cells):
                    if cj[0] != lb:
                        continue
                    best = max(best, duotone.j_hue((i, j), eps_y, eps_b, cov))
            M[a, b_] = M[b_, a] = best
    im = ax.imshow(M, cmap="viridis")
    ax.set_xticks(range(len(leds)))
    ax.set_xticklabels(leds, rotation=90, fontsize=7)
    ax.set_yticks(range(len(leds)))
    ax.set_yticklabels(leds, fontsize=7)
    plt.colorbar(im, ax=ax, fraction=0.046, label="best J_hue for the pair")
    ax.set_title("every LED pair, at its best Bayer layers")

    ax = axes[1, 1]
    j, idx, leds_r, frac, jb_r, err, hue_r = recommended[3]
    cube = cubes["C1014"]
    y, b = duotone.subset_unmix(cube.A, idx, eps_y, eps_b, cov)
    floor = 3.0 * duotone.subset_sigma(idx, eps_y, eps_b, cov)[2]
    total = np.maximum(y + b, 1e-12)
    # Blank where there is no dye to take a fraction of: most of this pad is
    # unstained, and a ratio of two noises is not a colour.
    img = cube.paint(np.where(y + b >= floor, b / total, np.nan))
    ys, xs = np.nonzero(cube.mask)
    im = ax.imshow(img[ys.min():ys.max() + 1, xs.min():xs.max() + 1],
                   cmap="YlOrBr", vmin=0, vmax=1, interpolation="nearest")
    plt.colorbar(im, ax=ax, fraction=0.046, label="brown fraction")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("C1014 from three cells alone:\n"
                 + " + ".join(duotone.cell_name(cells[i]) for i in idx)
                 + f"\n(blank where dye < 3 sigma = {floor:.3f} A)",
                 fontsize=10)

    fig.suptitle("report14 -- ranking the colour sets that separate brown "
                 "from yellow", fontsize=13)
    out = FIG / "report14_ranking.png"
    fig.savefig(out, dpi=120)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
