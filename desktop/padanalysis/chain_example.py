"""chain_example.py -- the worked numbers for tex/analysis_chain.tex.

Follows two pixels of one channel through every step of the chain, for the
post-image and for the pre-image lined up on it, using the same functions
the chain uses today.  Prints each intermediate value under the symbol the
report gives it.  Also two checks the report quotes: what the gray-chip
level G contributes to the stain C (`gray_ratio_check`), and whether lining
up should average reflectances or absorbances (`lineup_mean_check`).
Read-only.

Symbols (see tex/analysis_chain.tex, "Notation"):

    E      image, DN   the lit layer as read from the .h5 file
    e      scalar, DN  the dark level (one per Bayer layer per photo)
    F      image, DN   E - e
    P      image       the flat-field polynomial shape, mean 1
    gam1, gam2  scalars, DN   light level at each gray chip: mean F / mean P
    gam_j  1-D, DN     the level blended between the chips, one per column
    G      image, DN   P_ij * gam_j, the gray-chip level
    R      image       F / G, post-image
    S      image       the same for the pre-image, lined up on R's grid
    A, B   images      -log10 R, -log10 S
    B~     image       B smoothed (here: Gaussian, 1 px, pad pixels only)
    B^     image       lam * B + (1 - lam) * B~
    C      image       A - B^, the stain
    S^     image       10 ** -B^, the per-pixel white
    T      image       R / S^ = 10 ** -C

Regions: pad, g1 and g2 (the two gray chips).
"""
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

SP = Path(__file__).resolve().parent
sys.path.insert(0, str(SP))

import analysis_1 as a1                                     # noqa: E402
import padphot                                              # noqa: E402
import shading_review as sr                                 # noqa: E402

SAMP = "C1032"
CELL = ("royal_blue", "B")

#: The weight on the unsmoothed pre-image used for the example: the value
#: fitted on C1032 royal_blue.B in shading_review step R3.  Provisional.
LAM = 0.81

#: Smoothing width, layer px, for the example (shading_review.R3_D_PX).
SIGMA = 1.0


def photo(samp_no, cell, sec):
    """
    One photo of one channel up to R, keeping every intermediate.

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).
        sec: "" for the post-image, a1.PRE for the pre-image.

    Returns:
        Dict: 'rung', 'exposure_us', 'e' (scalars); 'E', 'F', 'P', 'G',
        'R' (images on this photo's own grid, NaN at hot pixels); 'Fg1',
        'Fg2', 'Pg1', 'Pg2' (scalars, the means of F and P over the
        chips); 'gam1', 'gam2' (scalars, Fg/Pg); 'j1', 'j2' (scalars, the
        chips' center columns).
    """
    led, layer = cell
    c = a1.open_sample(samp_no, sec)
    rung = a1.rung_verdict(samp_no, cell, sec)[0]
    F, E = c.layer(led, rung, layer)
    hot = a1._hot(samp_no, sec)
    g1 = a1._region(samp_no, a1.ROIGRAY1, sec) & ~hot
    g2 = a1._region(samp_no, a1.ROIGRAY2, sec) & ~hot
    P = a1.models()[cell].gain_at(c.bound, F.shape)
    G = padphot.divisor_map(a1.models()[cell], c.bound, g1, g2, F)
    out = dict(rung=rung, exposure_us=c.exposure_us(led, rung),
               e=c.dark_level(layer, led, rung),
               Fg1=float(F[g1].mean()), Fg2=float(F[g2].mean()),
               Pg1=float(P[g1].mean()), Pg2=float(P[g2].mean()),
               j1=float(np.nonzero(g1)[1].mean()),
               j2=float(np.nonzero(g2)[1].mean()))
    out["gam1"] = out["Fg1"] / out["Pg1"]
    out["gam2"] = out["Fg2"] / out["Pg2"]
    for k, v in (("E", E), ("F", F), ("P", P), ("G", G), ("R", F / G)):
        v = np.array(v, dtype=float)
        v[hot] = np.nan
        out[k] = v
    return out


def _at(img, y, x) -> float:
    """Bilinear value of an own-grid image at a fractional (row, col)."""
    return float(ndimage.map_coordinates(img, [[y], [x]], order=1,
                                         mode="nearest")[0])


def worked_example(samp_no=SAMP, cell=CELL, lam=LAM, sigma=SIGMA) -> dict:
    """
    Print the worked numbers: two pixels, both photos, every step.

    The pixels: one deep in the dye-free inner pad (left column of the
    report's tables) and one near the dot's center (right column).  The
    pre-image's values are taken where that post-image pixel came from
    (`analysis_1.pre_to_post`), by the same 4-pixel average the chain uses.

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).
        lam: scalar, the weight on the unsmoothed pre-image.
        sigma: scalar, smoothing width in layer px.

    Returns:
        {'post': photo dict, 'pre': photo dict, 'pixels': {name: (i, j)},
        and per pixel name a dict of the scalars printed}.
    """
    post = photo(samp_no, cell, "")
    pre = photo(samp_no, cell, a1.PRE)
    d = sr.post_pre(samp_no, cell)               # S, A, B on the post grid
    pad = d["pad"]
    # A and B against the gray chips only (no R_white): post_pre's arrays
    # are relative to R_white, so it is put back.
    lw = np.log10(d["R_white"])
    A = d["A_post"] - lw
    B = d["A_pre"] - lw
    Bt = sr.smooth(B, pad, sigma)
    Bh = lam * B + (1 - lam) * Bt
    C = A - Bh
    y_pre, x_pre = a1.pre_to_post(samp_no)
    depth = a1.pad_of(samp_no).depth
    ys, xs = np.nonzero(d["free"] & (depth >= 8))
    p_in = (int(ys[len(ys) // 2]), int(xs[len(xs) // 2]))
    yc, xc = np.nonzero(d["dot"])
    k = int(np.argmin((yc - yc.mean()) ** 2 + (xc - xc.mean()) ** 2))
    p_dot = (int(yc[k]), int(xc[k]))

    print(f"{samp_no} {a1.cell_name(cell)}")
    for name, ph in (("post", post), ("pre", pre)):
        print(f"  {name}: rung {ph['rung']}, exposure_us {ph['exposure_us']},"
              f" e = {ph['e']:.3f} DN; mean F over g1 {ph['Fg1']:.2f}, g2 "
              f"{ph['Fg2']:.2f} DN; mean P over g1 {ph['Pg1']:.4f}, g2 "
              f"{ph['Pg2']:.4f}; gam1 {ph['gam1']:.2f}, gam2 "
              f"{ph['gam2']:.2f} DN; j1 {ph['j1']:.1f}, j2 {ph['j2']:.1f}")
    print(f"  pad pixels {int(pad.sum())}, hot pixels on the pad "
          f"{int((a1.pad_of(samp_no).mask(0) & a1._hot(samp_no)).sum())}")
    out = dict(post=post, pre=pre, pixels=dict(inner=p_in, dot=p_dot))
    rows = []
    for name, p in (("inner", p_in), ("dot", p_dot)):
        i, j = p
        yp, xp = float(y_pre[p]), float(x_pre[p])
        v = dict(depth=float(depth[p]), dist=float(d["dist"][p]),
                 i_pre=yp, j_pre=xp)
        for tag, ph, at in (("post", post, lambda im: float(im[p])),
                            ("pre", pre, lambda im: _at(im, yp, xp))):
            jj = j if tag == "post" else xp
            t = (jj - ph["j1"]) / (ph["j2"] - ph["j1"])
            v[tag] = dict(E=at(ph["E"]), F=at(ph["F"]), P=at(ph["P"]), t=t,
                          gam=(1 - t) * ph["gam1"] + t * ph["gam2"],
                          G=at(ph["G"]), R=at(ph["R"]))
        v.update(S=float(10 ** -B[p]), A=float(A[p]), B=float(B[p]),
                 Bt=float(Bt[p]), Bh=float(Bh[p]), C=float(C[p]),
                 Sh=float(10 ** -Bh[p]), T=float(10 ** -C[p]))
        out[name] = v
        rows.append((name, p, v))

    print(f"\n  {'':22}{'dye-free':>12}{'dot':>12}")
    print(f"  {'pixel (i, j)':22}{str(rows[0][1]):>12}{str(rows[1][1]):>12}")
    print(f"  {'depth, px':22}{rows[0][2]['depth']:12.2f}"
          f"{rows[1][2]['depth']:12.2f}")
    print(f"  {'pre-image position':22}"
          f"{'(%.2f, %.2f)' % (rows[0][2]['i_pre'], rows[0][2]['j_pre']):>12}"
          f"{'(%.2f, %.2f)' % (rows[1][2]['i_pre'], rows[1][2]['j_pre']):>12}")
    for tag in ("post", "pre"):
        for key, fmt in (("E", "12.2f"), ("F", "12.2f"), ("P", "12.4f"),
                         ("t", "12.4f"), ("gam", "12.2f"), ("G", "12.2f"),
                         ("R", "12.4f")):
            a, b = rows[0][2][tag][key], rows[1][2][tag][key]
            print(f"  {tag + ' ' + key:22}{a:{fmt}}{b:{fmt}}")
    for key in ("S", "A", "B", "Bt", "Bh", "C", "Sh", "T"):
        a, b = rows[0][2][key], rows[1][2][key]
        print(f"  {key:22}{a:+12.4f}{b:+12.4f}")
    return out


def gray_ratio_check(samp_no=SAMP, cell=CELL) -> dict:
    """
    What the gray-chip levels contribute to the stain C = A - B.

    With S = F_pre / G_pre lined up, C = A - B splits into two parts:

        C = -log10(F_post / F_pre')  +  log10(G_post / G_pre')

    (' = lined up).  The second part is what G does.  It splits again into
    the polynomial's part, log10(P_post / P_pre'), which is near 0 because
    the strip moved only a fraction of a pixel, and the chips' part,
    log10(gam_post / gam_pre'), which carries the difference in exposure
    time and lamp brightness between the two photos.

    Prints that second part at the two worked pixels and its spread over
    the pad, and the size of the first part without G.

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).

    Returns:
        Dict of scalars: 'g_med', 'g_p1', 'g_p99' (log10 G ratio over the
        pad: median, 1st and 99th percentiles); 'p_med', 'p_range' (the
        polynomial's part); 'exposure_ratio' (post / pre exposure_us);
        'gam_ratio' (mean of the post chips' gam / the pre chips').
    """
    post = photo(samp_no, cell, "")
    pre = photo(samp_no, cell, a1.PRE)
    y_pre, x_pre = a1.pre_to_post(samp_no)
    pad = a1.pad_of(samp_no).mask(0) & ~a1._hot(samp_no)
    G_pre = ndimage.map_coordinates(pre["G"], [y_pre, x_pre], order=1,
                                    mode="nearest")
    P_pre = ndimage.map_coordinates(pre["P"], [y_pre, x_pre], order=1,
                                    mode="nearest")
    g = np.log10(post["G"] / G_pre)[pad]
    pp = np.log10(post["P"] / P_pre)[pad]
    g, pp = g[np.isfinite(g)], pp[np.isfinite(pp)]
    out = dict(g_med=float(np.median(g)), g_p1=float(np.percentile(g, 1)),
               g_p99=float(np.percentile(g, 99)),
               p_med=float(np.median(pp)),
               p_range=float(pp.max() - pp.min()),
               exposure_ratio=post["exposure_us"] / pre["exposure_us"],
               gam_ratio=((post["gam1"] + post["gam2"])
                          / (pre["gam1"] + pre["gam2"])))
    print(f"\n{samp_no} {a1.cell_name(cell)}: log10(G_post / G_pre') over "
          f"the pad: median {out['g_med']:+.4f}, 1st-99th percentile "
          f"{out['g_p1']:+.4f} to {out['g_p99']:+.4f}")
    print(f"  polynomial's part log10(P_post / P_pre'): median "
          f"{out['p_med']:+.5f}, range {out['p_range']:.5f}")
    print(f"  chips' level post/pre {out['gam_ratio']:.4f} "
          f"(log10 {np.log10(out['gam_ratio']):+.4f}); exposure_us ratio "
          f"{out['exposure_ratio']:.4f} (log10 "
          f"{np.log10(out['exposure_ratio']):+.4f})")
    return out


def lineup_mean_check(samp_no=SAMP, cell=CELL) -> dict:
    """
    Lining up: does it matter whether reflectances or absorbances are
    averaged?

    The chain lines up R (a 4-pixel weighted average of reflectances, i.e.
    of light) and then takes B = -log10.  The alternative averages
    absorbances (the same weights applied to -log10 R, which is a
    geometric mean of reflectances).  Prints the difference
    B(average of absorbances) - B(average of reflectances), which is never
    negative, by depth into the pad.

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).

    Returns:
        {band name: dict of scalars 'n', 'median', 'p99', 'max'}.
    """
    pre = photo(samp_no, cell, a1.PRE)
    y_pre, x_pre = a1.pre_to_post(samp_no)
    with np.errstate(invalid="ignore", divide="ignore"):
        B_lin = -np.log10(ndimage.map_coordinates(
            pre["R"], [y_pre, x_pre], order=1, mode="nearest"))
        B_log = ndimage.map_coordinates(-np.log10(pre["R"]), [y_pre, x_pre],
                                        order=1, mode="nearest")
    rect = a1.pad_of(samp_no)
    pad = rect.mask(0) & ~a1._hot(samp_no)
    diff = B_log - B_lin
    out = {}
    print(f"\n{samp_no} {a1.cell_name(cell)}: B(average of absorbances) - "
          f"B(average of reflectances), lined-up pre-image")
    for name, m in (("more than 4 px in", pad & (rect.depth > 4)),
                    ("1 to 4 px in", pad & (rect.depth > 1)
                     & (rect.depth <= 4)),
                    ("outermost pixel", pad & (rect.depth <= 1))):
        v = diff[m]
        v = v[np.isfinite(v)]
        out[name] = dict(n=int(v.size), median=float(np.median(v)),
                         p99=float(np.percentile(v, 99)),
                         max=float(v.max()))
        print(f"  {name:20} n {v.size:5d}  median {np.median(v):.6f}  "
              f"99th pct {np.percentile(v, 99):.6f}  max {v.max():.6f}")
    return out


def smooth_mean_check(samp_no=SAMP, cell=CELL, sigmas=(1.0, 2.0)) -> dict:
    """
    Smoothing the pre-image: does it matter whether reflectances or
    absorbances are averaged?

    Step 6 smooths B (absorbance), which is a weighted geometric mean of
    the reflectances S.  The alternative smooths S (a weighted arithmetic
    mean: light averaged over a larger area) and then takes -log10.  Both
    use the same weights (`shading_review.smooth`, pad pixels only).
    Prints the difference smooth(B) - (-log10 smooth(S)), which is never
    negative, by depth into the pad, for each width.

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).
        sigmas: smoothing widths, layer px.

    Returns:
        {(sigma, band name): dict of scalars 'n', 'median', 'p99', 'max'}.
    """
    d = sr.post_pre(samp_no, cell)
    pad = d["pad"]
    lw = np.log10(d["R_white"])
    B = d["A_pre"] - lw
    S = 10 ** -B
    rect = a1.pad_of(samp_no)
    out = {}
    print(f"\n{samp_no} {a1.cell_name(cell)}: smoothed absorbance minus "
          f"absorbance of smoothed reflectance, lined-up pre-image")
    for w in sigmas:
        diff = sr.smooth(B, pad, w) + np.log10(sr.smooth(S, pad, w))
        for name, m in (("more than 4 px in", pad & (rect.depth > 4)),
                        ("1 to 4 px in", pad & (rect.depth > 1)
                         & (rect.depth <= 4)),
                        ("outermost pixel", pad & (rect.depth <= 1))):
            v = diff[m]
            v = v[np.isfinite(v)]
            out[w, name] = dict(n=int(v.size), median=float(np.median(v)),
                                p99=float(np.percentile(v, 99)),
                                max=float(v.max()))
            print(f"  sigma {w:g} px  {name:20} median {np.median(v):.6f}  "
                  f"99th pct {np.percentile(v, 99):.6f}  max {v.max():.6f}")
    return out


if __name__ == "__main__":
    worked_example()
    gray_ratio_check()
    lineup_mean_check()
    smooth_mean_check()
    a1.close_all()
