"""
color_study.py -- chain step 3 of the proof-of-concept work plan (the
document's step 8): yellow and brown.

THE QUESTION
    Do two reference color lists, one for the yellow and one for the
    brown, describe the F/G/H stains?  It could change whether the chain
    needs a third amount, and it gives the matrix that turns each pixel's
    channels into an amount of yellow and an amount of brown.  It ends
    with that matrix saved, a map of yellow and brown per pad, and the
    size of whatever the two colors leave unexplained compared with the
    noise (work plan, step 3).  Bill also wants the change in yellow and
    in brown from C1047 (400 ug/L) to C1048 (the same strip re-exposed,
    800 ug/L in all) measured.

THE PARTS (each is a guarded block in `run()`)
    3a  the samples: chain_samples.py, the one sample list (done
        2026-11-13).
    3b  `pairing_check`, `show_pairing`: C1048's post-image divided by
        C1047's pre-image (Bill: "Use C1047 pre-image with C1048
        post-image").  C1048's pad is located on photo_red
        (chain_samples.PAD_FIT_CELLS), C1047's on every channel; the check
        is whether lining up the two leaves a false change at the edges.
    3b-ii `fit_basis_offsets`: Bill's question -- can a pad too dark for
        the usual all-channel fit be located on photo_red alone, plus an
        offset measured in its pre-image?
    3c  `yellow_brown`, `show_yellow_brown`: the reference lists, the
        matrix M, each pad's amounts of yellow and brown, and the
        leftover.  First pass 2026-11-13 (yellow C1042, brown C1048, with
        C1047 -> C1048).  Second pass 2026-11-15 on the new strips
        C1049-C1051 (yellow C1051, brown C1050, 4 frames averaged).
    3c-2 `show_scatter`: every pixel on two-channel graphs with the yellow
        and brown lines: is C1049's dot a mix of the two? (2026-11-15)
    3d  `color_count`: the check with principal components (2026-11-15).
    3e  `three_colors`: three reference colors, C1049's dot as the third.
    3f  `color_count_3f`: principal components again on C1052-C1055
        (2026-11-16), every LED and layer with a pre-image white: how many
        colors plus brightness.

WORDS AND SYMBOLS
    R           a photo's reflectance at a pixel: the signal divided by
                the gray-chip level at that pixel (`shading_review.frame`).
                Bigger means lighter.
    A           absorbance, here A = -log10(R_post / R_pre), the pre-image
                lined up onto the post-image's pixels.  Bigger means
                darker (more dye).  0.01 A is about 2.3 % less light.
    channel     LED name . camera layer, e.g. photo_red.R.
    pairing     a post-image and the pre-image it is divided by.
    interior    pad pixels 8 or more layer pixels from every edge.
    bands 0-1   pad pixels within 2 layer pixels of their nearest edge
                (corners left out); white_study.zones_of.

Run with:  timeout -k 10 600 ../../.venv/bin/python3 -u color_study.py
"""
import json
import sys
from pathlib import Path

import numpy as np

import analysis_1 as a1
import chain_samples as cs
import shading_review as sr
import white_study as ws

HERE = Path(__file__).resolve().parent
OUT = HERE / "color_out"
FIG = HERE / "fig"

#: The pairings compared in 3b: (label, post-image file, pre-image file).
PAIRINGS = [("C1047 own pre-image", "C1047", "C1047"),
            ("C1048 with C1047's pre-image", "C1048", cs.pre_source("C1048"))]
#: Channels checked in 3b: one the dye barely absorbs, one it absorbs
#: strongly.
CHECK_CELLS = (("photo_red", "R"), ("green", "B"))
#: Edges, in white_study's order: left, right, top, bottom.
EDGE_NAMES = {"L": "left", "R": "right", "T": "top", "B": "bottom"}


def pair_A(post, pre, cell):
    """
    A for one pairing and channel, on the post-image's pixels.

    Args:
        post: file whose post-image is used.
        pre: file whose pre-image is used (lined up onto the post-image by
            the two pad fits, `white_study._carry`).
        cell: (led, layer).

    Returns:
        dict: R_pre (pre-image, its own pixels), R_post, R_pre_on_post,
        A (all arrays), zone (white_study.zones_of groups on the
        post-image), nearest (index of each pixel's nearest edge).
    """
    Rpost = np.array(sr.frame(post, cell, "")["R"], float)
    Rpre = np.array(sr.frame(pre, cell, a1.PRE)["R"], float)
    on = ws._carry(pre, a1.PRE, post, "", Rpre, Rpost.shape)
    with np.errstate(invalid="ignore", divide="ignore"):
        A = -np.log10(Rpost / on)
    zone, nearest, _ = ws.zones_of(post, "")
    return dict(R_pre=Rpre, R_post=Rpost, R_pre_on_post=on, A=A,
                zone=zone, nearest=nearest)


def pairing_check(pairings=PAIRINGS, cells=CHECK_CELLS):
    """
    Step 3b: does lining up C1047's pre-image onto C1048's post-image
    leave a false change at the edges?  Compared with C1047's own pairing.

    How, for each pairing and channel:
        1. A on the post-image's pixels (`pair_A`).
        2. The interior median of A.
        3. For each edge: the median of A over bands 0-1 of that edge,
           minus the interior median.  This strip has a lighter rim, about
           2-5 px wide, all round (less dye near the edges), so the edges
           read lighter in both pairings; what points to a misfit in
           lining up is a difference between the two pairings at one edge
           and not at the opposite one.
        4. The pad fits' widths (layer px) and which channels located them.

    Returns:
        list of dicts: pairing, post, pre, cell, A_interior, edge_minus_
        interior (dict edge -> A), width_post, width_pre, fit_cells_post,
        fit_cells_pre.
    """
    out = []
    for label, post, pre in pairings:
        for cell in cells:
            d = pair_A(post, pre, cell)
            A, zone, near = d["A"], d["zone"], d["nearest"]
            ok = np.isfinite(A)
            a_in = float(np.median(A[zone["interior"] & ok]))
            edges = {}
            for i, e in enumerate(ws.EDGES):
                m = zone["bands 0-1"] & (near == i) & ok
                edges[e] = float(np.median(A[m]) - a_in) if m.any() else None
            pp, pr = a1.pad_of(post, ""), a1.pad_of(pre, a1.PRE)
            out.append(dict(pairing=label, post=post, pre=pre,
                            cell=a1.cell_name(cell), A_interior=a_in,
                            edge_minus_interior=edges,
                            width_post=pp.width, width_pre=pr.width,
                            fit_cells_post=len(pp.cells),
                            fit_cells_pre=len(pr.cells)))
    a1.close_all()
    return out


def summarize_pairing(rows):
    """Print the 3b table."""
    print("Step 3b: lining up, C1047 own pairing against C1048 with "
          "C1047's pre-image")
    print("  A = -log10(R_post / R_pre lined up), darker +.  Edge columns: "
          "median A in bands 0-1 of that edge minus the interior median.")
    print(f"  {'pairing':32s}{'channel':>13s}{'interior':>10s}"
          + "".join(f"{EDGE_NAMES[e]:>9s}" for e in ws.EDGES)
          + f"{'width post/pre, px':>22s}")
    for r in rows:
        e = r["edge_minus_interior"]
        print(f"  {r['pairing']:32s}{r['cell']:>13s}{r['A_interior']:>10.4f}"
              + "".join(f"{e[k]:>+9.4f}" if e[k] is not None else
                        f"{'-':>9s}" for k in ws.EDGES)
              + f"{r['width_post']:>12.2f} / {r['width_pre']:.2f}")


def show_pairing(cell=("photo_red", "R"), pairings=PAIRINGS, savefig=True):
    """
    Check figure for 3b, one row per pairing: the pre-image as taken, the
    post-image, the pre-image lined up onto the post-image, and A.  The
    fitted pad of each photo is drawn in red.  R panels share one gray
    scale; the A panels share one color scale.

    Saved as fig/color_3b_pairing_<channel>.png, with a plain-language
    .txt beside it.
    """
    import matplotlib.pyplot as plt
    rows = [(label, post, pre, pair_A(post, pre, cell))
            for label, post, pre in pairings]
    rmax = max(np.nanpercentile(d["R_post"], 99.5) for *_, d in rows)
    amed = [np.nanmedian(d["A"][d["zone"]["interior"]]) for *_, d in rows]
    lo, hi = min(amed) - 0.08, max(amed) + 0.08
    name = a1.cell_name(cell)
    fig, axs = plt.subplots(len(rows), 4, figsize=(13, 3.6 * len(rows)))
    for (label, post, pre, d), ax in zip(rows, axs):
        panels = [(d["R_pre"], f"{pre} pre {name}, R", pre, a1.PRE),
                  (d["R_post"], f"{post} post {name}, R", post, ""),
                  (d["R_pre_on_post"], f"{pre} pre lined up onto "
                   f"{post} post", post, ""),
                  (d["A"], f"A = -log10(post / pre lined up)", post, "")]
        for k, (img, title, f, sec) in enumerate(panels):
            a = ax[k]
            if k < 3:
                a.imshow(img, cmap="gray", vmin=0, vmax=rmax)
            else:
                im = a.imshow(img, cmap="viridis", vmin=lo, vmax=hi)
                fig.colorbar(im, ax=a, fraction=0.046,
                             label="A (bigger = darker)")
            a.contour(a1.pad_of(f, sec).mask(0), levels=[0.5], colors="r",
                      linewidths=0.7)
            p = a1.pad_of(f, sec)
            a.set_xlim(p.aL - 12, p.aR + 12)
            a.set_ylim(p.bot + 10, p.top - 10)
            a.set_title(title, fontsize=8)
        ax[0].set_ylabel(label, fontsize=9)
    fig.suptitle(f"Step 3b: C1047 pre-image with C1047 and with C1048 "
                 f"post-images, {name}; red = fitted pad", fontsize=10)
    fig.tight_layout()
    if savefig:
        FIG.mkdir(exist_ok=True)
        out = FIG / f"color_3b_pairing_{name}.png"
        fig.savefig(out, dpi=100)
        out.with_suffix(".txt").write_text(PAIRING_NOTE.format(name=name))
        print(f"fig saved to {out}")
    a1.close_all()
    return fig


PAIRING_NOTE = """\
color_3b_pairing_{name}.png -- what the panels show
(made by color_study.py, show_pairing, 2026-11-13)

Two rows.  Top: strip C1047 (400 ug/L), its post-image divided by its own
pre-image, as the chain normally does.  Bottom: C1048, the same strip after
a second exposure to arsine (800 ug/L in all), divided by C1047's
pre-image (Bill: the C1048 file's own pre-image shows the already-stained
strip, so it is not a clean reference).

Columns, left to right:
  1. the pre-image, R: the photo divided by the gray chips at each pixel.
     Lighter = more light back.  Gray scale shared by the first three
     columns.
  2. the post-image, R, same scale.
  3. the pre-image moved onto the post-image's pixels ("lined up"), using
     the two fitted pads (white_study._carry).  If lining up is right, its
     pad sits exactly on the post-image's pad.
  4. A = -log10(post / lined-up pre), absorbance: bigger means darker,
     more dye.  Color scale shared by both rows.

Red outline: the fitted pad of the photo in that panel.  C1048's pad is
located on photo_red only (chain_samples.PAD_FIT_CELLS), because the dye
is so dark that the usual average of all channels loses its edges; C1047's
on all channels.  Axes: layer pixels (one layer pixel = 2 sensor pixels).

What to look for: in column 4, a thin light or dark line along one side
and not the opposite one means the two pads were not lined up exactly.
The strip has a lighter rim, about 2-5 px wide, all round (less dye near
the edges, most at the top); it shows in both rows and is real.
"""


# ── 3b-ii: photo_red-only fits against all-channel fits (Bill) ─────────

#: The channel a dark pad is located on (chain_samples.PAD_FIT_CELLS).
RED = (("photo_red", "R"),)
#: Fitted sides compared, in this order.
SIDE_NAMES = ("left", "right", "top", "bottom")


def _sides(p, y):
    """A pad fit's left and right columns at row y, and its top and bottom
    rows, layer px."""
    return np.array([p.aL + p.m * (y - p.yc), p.aR + p.m * (y - p.yc),
                     p.top, p.bot])


def fit_basis_offsets(sets=None):
    """
    Bill (2026-11-13): can the offset of a photo_red-only pad fit from the
    usual all-channel fit be measured in the pre-image, so that a
    post-image could be located on photo_red alone (a pad too dark for the
    all-channel fit) and still sit where the all-channel fit would put it?

    How, for every file holding both images (chain_samples, own
    pre-image):
        1. Pre-image: the usual fit (`analysis_1.pad_of`, all channels) and
           a photo_red-only fit (same layout, guided by itself).
        2. Post-image: the usual fit (guided by the usual pre-image fit)
           and a photo_red-only fit (guided by the photo_red pre-image fit).
        3. offset = all-channel minus photo_red-only, for the left and
           right sides (at the pad's middle row), the top and the bottom.
        4. The test: the post-image offset minus the same file's
           pre-image offset.  Near 0 means the pre-image offset carries
           over, and photo_red-only plus that offset finds the all-channel
           position.

    Returns:
        list of dicts: holder, file, offset_pre, offset_post, post_minus_pre
        (each [left, right, top, bottom], layer px), and edge_rms of the
        four fits (all_pre, red_pre, all_post, red_post).
    """
    from padrect import PadRect
    sets = cs.sets_with_own_pre() if sets is None else sets
    out = []
    for hold, samples in sets.items():
        for smp, files in samples.items():
            for f in files:
                lay = a1.CAP_LAYOUT.get(a1._cap_style(f))
                try:
                    ap, aq = a1.pad_of(f, a1.PRE), a1.pad_of(f, "")
                    rp = PadRect(a1.open_sample(f, a1.PRE), rung=a1.PAD_RUNG,
                                 layout=lay, cells=RED)
                    rp.guide_sides()
                    rq = PadRect(a1.open_sample(f, ""), rung=a1.PAD_RUNG,
                                 layout=lay, prior=rp, cells=RED)
                except Exception as e:
                    print(f"  {f}: skipped ({e})")
                    continue
                op = _sides(ap, (ap.top + ap.bot) / 2) - _sides(
                    rp, (ap.top + ap.bot) / 2)
                oq = _sides(aq, (aq.top + aq.bot) / 2) - _sides(
                    rq, (aq.top + aq.bot) / 2)
                out.append(dict(holder=hold, file=f, offset_pre=op.tolist(),
                                offset_post=oq.tolist(),
                                post_minus_pre=(oq - op).tolist(),
                                edge_rms=[ap.edge_rms, rp.edge_rms,
                                          aq.edge_rms, rq.edge_rms]))
            a1.close_all()
    return out


def summarize_offsets(rows):
    """Print the 3b-ii summary: the pre-image offsets and how well they
    carry over to the post-image."""
    A = np.array([r["offset_pre"] for r in rows])
    D = np.array([r["post_minus_pre"] for r in rows])
    E = np.array([r["edge_rms"] for r in rows])
    print("Step 3b-ii: pad fit on all channels minus fit on photo_red only, "
          "layer px (+ = all-channel fit further right or down)")
    print(f"  {len(rows)} files.  {'':28s}" + "".join(f"{n:>9s}"
                                                     for n in SIDE_NAMES))
    for lab, v in (("pre-image offset, mean", A.mean(0)),
                   ("pre-image offset, sd", A.std(0, ddof=1)),
                   ("post minus pre offset, mean", D.mean(0)),
                   ("post minus pre offset, sd", D.std(0, ddof=1)),
                   ("post minus pre, largest |.|", np.abs(D).max(0))):
        print(f"  {lab:38s}" + "".join(f"{x:>+9.2f}" for x in v))
    for h in sorted({r["holder"] for r in rows}):
        Ah = np.array([r["offset_pre"] for r in rows if r["holder"] == h])
        print(f"  holder {h} ({len(Ah)} files), pre-image offset mean"
              + "".join(f"{x:>+9.2f}" for x in Ah.mean(0)))
    print("  edge rms, median px: pre all %.2f, red %.2f; post all %.2f, "
          "red %.2f" % tuple(np.median(E, 0)))


ABOUT_OFFSETS = {
    "what": "Step 3b-ii (Bill's question, 2026-11-13): offset of a "
            "photo_red-only pad fit from the usual all-channel fit, in the "
            "pre-image and the post-image of each file.  "
            "color_study.fit_basis_offsets().",
    "offset_pre, offset_post": "[left, right, top, bottom]: all-channel "
                               "fit minus photo_red-only fit, layer pixels "
                               "(one layer pixel = 2 sensor pixels); left "
                               "and right at the pad's middle row; + = "
                               "all-channel fit further right or down",
    "post_minus_pre": "offset_post - offset_pre, layer pixels: how well "
                      "the pre-image offset predicts the post-image one",
    "edge_rms": "rms of each fit's edge points about its straight sides, "
                "layer pixels: [all pre, red pre, all post, red post]",
}


# ── 3c: the yellow and brown reference colors, and the amounts ─────────
#
# Chain step 8 (tex/chain_unified.tex): each pixel's list of absorbances,
# one per channel, is written as  A = gamma*1 + c_Y*y + c_B*b + leftover,
# where 1 is the list of all ones (an even darkening in every channel:
# brightness), y the yellow reference list and b the brown one.  With
# S = [1 y b] (one column each), the matrix M = (S^T S)^-1 S^T turns a
# pixel's list into (gamma, c_Y, c_B) in one multiplication.

#: The channels used: each pre-image LED's main layer, and all three green
#: layers (the two greens are never averaged).  Only channels present in
#: the pre-images can be used.
CHANNELS = (("violet", "B"), ("royal_blue", "B"), ("cyan", "B"),
            ("green", "Gb"), ("green", "Gr"), ("green", "B"),
            ("photo_red", "R"))
#: The reference pads.  2026-11-13 first pass: yellow C1042, brown C1048
#: (older strips; their pre-images clip on the pad in five channels).
#: 2026-11-15 (Bill): yellow from C1051 (12 ug/L, all yellow, perhaps not
#: the deepest yellow possible), brown from C1050 (600 ug/L, all brown),
#: both with 4 averaged frames per channel and no clipping.
YELLOW_REF, BROWN_REF = "C1051", "C1050"
#: The pads used with these references: the three strips of 2026-11-15
#: (holder H, 4 repeat frames).  C1049 (20 ug/L) has a dark dot in the
#: center that Bill expects to be a mix of yellow and brown, with the
#: deepest yellow nearest the dot.
NEW_FILES = {"C1051": "C1051", "C1049": "C1049", "C1050": "C1050"}


def _usable(f, cell, sec):
    try:
        return a1.rung_verdict(f, cell, sec)[0] is not None
    except Exception:
        return False


def first_files(sets=None):
    """Sample -> its first file (its first post-image), from chain_samples."""
    sets = cs.SETS if sets is None else sets
    return {smp: files[0] for v in sets.values() for smp, files in v.items()}


def usable_channels(files, channels=CHANNELS):
    """
    The channels usable (a readable exposure, `analysis_1.rung_verdict`)
    in every post-image of `files` and in the pre-image each is paired
    with.  Prints the ones dropped and why.
    """
    keep = []
    for cell in channels:
        bad = [f for f in files if not (_usable(f, cell, "") and _usable(
            cs.pre_source(f), cell, a1.PRE))]
        if bad:
            print(f"  {a1.cell_name(cell)} dropped: not usable in {bad}")
        else:
            keep.append(cell)
    return keep


def pad_lists(f, channels):
    """
    Every pad pixel's list of absorbances for one post-image.

    Returns:
        (A, zone, pad): A array (channels, rows, cols), A = -log10(R_post /
        R_pre lined up), with the pre-image from chain_samples.pre_source;
        zone, the white_study.zones_of groups; pad, bool array of the pad
        pixels (interior, bands and corners) where every channel is finite.
    """
    pre = cs.pre_source(f)
    A = np.stack([pair_A(f, pre, cell)["A"] for cell in channels])
    zone, _, _ = ws.zones_of(f, "")
    pad = np.zeros(A.shape[1:], bool)
    for zn in ws.ZONES + ("corner",):
        pad |= zone[zn]
    pad &= np.isfinite(A).all(0)
    return A, zone, pad


def unmix_matrix(*refs):
    """
    S = [1 refs...] (one column each) and M = (S^T S)^-1 S^T; M S is the
    identity.  unmix_matrix(y, b) for two colors, (y, b, d) for three.
    """
    S = np.column_stack([np.ones(len(refs[0]))] + list(refs))
    return S, np.linalg.inv(S.T @ S) @ S.T


def yellow_brown(files=None, channels=None):
    """
    Step 3c: the reference lists y and b, the matrix M, and each pad's
    amounts of yellow and brown.

    How:
        1. Channels: `usable_channels` over every first post-image.
        2. y: the mean list over YELLOW_REF's interior; b: the same over
           BROWN_REF's.  (Means over about 900 pixels, so their noise is
           small.)
        3. S = [1 y b], M = (S^T S)^-1 S^T (`unmix_matrix`).
        4. For every pad pixel: (gamma, c_Y, c_B) = M A.  c_Y = 1 means as
           much yellow as YELLOW_REF's interior on average; c_B = 1 as much
           brown as BROWN_REF's.  gamma is an even darkening of every
           channel, in A.
        5. leftover = A - S (M A): what the two colors and brightness
           cannot describe; its size per pixel is the root mean square
           over the channels, in A.

    Returns:
        dict: channels, y, b, M (rows gamma, c_Y, c_B), pads (list of
        dicts: sample, file, pre, conc, n, interior medians of gamma, c_Y,
        c_B, pad means of c_Y and c_B, leftover rms median over the
        interior, A).
    """
    files = files or NEW_FILES
    chans = channels or usable_channels(list(files.values()))
    if len(chans) < 3:
        raise ValueError(
            f"step 3c needs at least 3 usable channels (brightness, yellow, "
            f"brown), found {len(chans)}: {[a1.cell_name(c) for c in chans]}."
            f"  Since C1039 the pre-images clip on the pad in violet.B, "
            f"royal_blue.B, cyan.B, green.Gb and green.Gr (2026-11-13).")
    ref = {}
    for smp in (YELLOW_REF, BROWN_REF):
        A, zone, pad = pad_lists(files[smp], chans)
        z = zone["interior"] & pad
        ref[smp] = A[:, z].mean(1)
    y, b = ref[YELLOW_REF], ref[BROWN_REF]
    S, M = unmix_matrix(y, b)
    rows = []
    for smp, f in files.items():
        A, zone, pad = pad_lists(f, chans)
        X = A[:, pad]                                 # channels x pixels
        g, cy, cb = M @ X
        left = X - S @ np.vstack([g, cy, cb])
        rms = np.sqrt((left ** 2).mean(0))
        inner = zone["interior"][pad]
        rows.append(dict(sample=smp, file=f, pre=cs.pre_source(f),
                         conc=cs.concentration(smp), n=int(pad.sum()),
                         gamma=float(np.median(g[inner])),
                         c_Y=float(np.median(cy[inner])),
                         c_B=float(np.median(cb[inner])),
                         c_Y_pad_mean=float(cy.mean()),
                         c_B_pad_mean=float(cb.mean()),
                         leftover_rms=float(np.median(rms[inner]))))
        a1.close_all()
    return dict(channels=[a1.cell_name(c) for c in chans], y=y.tolist(),
                b=b.tolist(), M=M.tolist(), pads=rows)


def summarize_yellow_brown(res):
    """Print the 3c reference lists, the matrix and the table of pads."""
    ch = res["channels"]
    print("Step 3c: reference lists, A (bigger = darker)")
    print(f"  {'':10s}" + "".join(f"{c:>13s}" for c in ch))
    for name, v in (("yellow y", res["y"]), ("brown b", res["b"])):
        print(f"  {name:10s}" + "".join(f"{x:>13.4f}" for x in v))
    y, b = np.array(res["y"]), np.array(res["b"])
    ang = np.degrees(np.arccos(y @ b / np.linalg.norm(y) / np.linalg.norm(b)))
    print(f"  angle between y and b: {ang:.0f} deg (0 = same color)")
    print("  M, rows gamma, c_Y, c_B (each a weighted sum of the channels):")
    for name, r in zip(("gamma", "c_Y", "c_B"), res["M"]):
        print(f"  {name:10s}" + "".join(f"{x:>+13.3f}" for x in r))
    print(f"\n  {'sample':8s}{'ug/L':>7s}{'gamma A':>9s}{'c_Y':>8s}{'c_B':>8s}"
          f"{'c_Y pad':>9s}{'c_B pad':>9s}{'leftover A':>12s}{'pixels':>8s}"
          "   (c_ = interior medians; pad = mean over the whole pad)")
    for r in res["pads"]:
        c = "-" if r["conc"] is None else f"{r['conc']:g}"
        print(f"  {r['sample']:8s}{c:>7s}{r['gamma']:>+9.4f}{r['c_Y']:>8.3f}"
              f"{r['c_B']:>8.3f}{r['c_Y_pad_mean']:>9.3f}"
              f"{r['c_B_pad_mean']:>9.3f}{r['leftover_rms']:>12.4f}"
              f"{r['n']:>8d}")
    by = {r["sample"]: r for r in res["pads"]}
    if "C1047" in by and "C1048" in by:
        a, b2 = by["C1047"], by["C1048"]
        print(f"\n  C1047 -> C1048 (pad means): yellow {a['c_Y_pad_mean']:.3f}"
              f" -> {b2['c_Y_pad_mean']:.3f}, brown {a['c_B_pad_mean']:.3f}"
              f" -> {b2['c_B_pad_mean']:.3f}")


def true_color(f):
    """
    A plain color picture of a post-image, for looking at only: the white
    LED's R, Gr and B layers, each divided by the mean of its two gray
    chips, so all pictures share one scale and a gray chip shows as mid
    gray.  Not used in any number.  The white frame is read directly (its
    first frame), because in Gr it clips on the pad (26 % on C1051), which
    the screens in `sr.frame` reject; a clipped pixel shows as white here.

    Returns:
        array (rows, cols, 3), values 0-1 (red, green, blue).
    """
    c = a1.open_sample(f, "")
    lab = c.labels("white")[0]
    chips = a1._region(f, a1.ROIGRAY1, "") | a1._region(f, a1.ROIGRAY2, "")
    out = []
    for lay in ("R", "Gr", "B"):
        sig = c.layer("white", lab, lay)[0]
        out.append(sig / np.mean(sig[chips]) * GRAY_SHOWN)
    return np.clip(np.nan_to_num(np.dstack(out)), 0, 1)


#: Display level of a gray chip in `true_color` (0-1).
GRAY_SHOWN = 0.3


def _amount_maps(f, chans, S, M):
    """c_Y, c_B and leftover maps (rows, cols), NaN off the pad."""
    A, zone, pad = pad_lists(f, chans)
    maps = np.full((3,) + A.shape[1:], np.nan)
    X = A[:, pad]                                # channels x pixels
    g, cy, cb = M @ X                            # each: one value per pixel
    left = X - S @ np.vstack([g, cy, cb])        # channels x pixels
    maps[0][pad], maps[1][pad] = cy, cb
    maps[2][pad] = np.sqrt((left ** 2).mean(0))  # rms over the channels
    return maps


def show_yellow_brown(res, savefig=True):
    """
    Step 3c approval figure: for every pad (columns), a plain color
    picture (`true_color`), then the yellow amount c_Y, the brown amount
    c_B and the leftover, on shared scales.  Saved as
    fig/color_3c_yellow_brown.png, with a plain-language .txt beside it.
    """
    import matplotlib.pyplot as plt
    chans = [tuple(c.split(".")) for c in res["channels"]]
    S, M = unmix_matrix(np.array(res["y"]), np.array(res["b"]))
    pads = sorted(res["pads"], key=lambda r: (r["conc"] is None,
                                              r["conc"] or 0))
    fig, axs = plt.subplots(4, len(pads), figsize=(2.3 * len(pads) + 1.6, 10),
                            squeeze=False)
    rows = (("yellow amount c_Y", -0.5, 2.5, "YlOrBr"),
            ("brown amount c_B", -0.5, 2.5, "copper_r"),
            ("leftover, A (rms over channels)", 0, 0.05, "Greys"))
    for j, r in enumerate(pads):
        maps = _amount_maps(r["file"], chans, S, M)
        p = a1.pad_of(r["file"], "")
        c = "?" if r["conc"] is None else f"{r['conc']:g}"
        panels = [(true_color(r["file"]), None)] + [
            (m, spec) for m, spec in zip(maps, rows)]
        for i, (m, spec) in enumerate(panels):
            ax = axs[i, j]
            if spec is None:
                ax.imshow(m)
            else:
                im = ax.imshow(m, cmap=spec[3], vmin=spec[1], vmax=spec[2])
                if j == len(pads) - 1:
                    fig.colorbar(im, ax=axs[i, :], fraction=0.03, pad=0.02,
                                 label=spec[0])
            ax.set_xlim(p.aL - 3, p.aR + 3)
            ax.set_ylim(p.bot + 3, p.top - 3)
            ax.set_xticks([]); ax.set_yticks([])
            if i == 0:
                ax.set_title(f"{r['sample']}, {c} ug/L, post", fontsize=9)
        a1.close_all()
    for i, lab in enumerate(("white LED\nphoto", "yellow", "brown",
                             "leftover")):
        axs[i, 0].set_ylabel(lab, fontsize=9)
    fig.suptitle(f"Step 3c: amounts of yellow (1 = {YELLOW_REF} interior "
                 f"average) and brown (1 = {BROWN_REF} interior average), "
                 "and what they leave over", fontsize=10)
    if savefig:
        FIG.mkdir(exist_ok=True)
        out = FIG / "color_3c_yellow_brown.png"
        fig.savefig(out, dpi=100)
        out.with_suffix(".txt").write_text(YB_NOTE.format(
            y=YELLOW_REF, b=BROWN_REF, ch=", ".join(res["channels"])))
        print(f"fig saved to {out}")
    return fig


YB_NOTE = """\
color_3c_yellow_brown.png -- what the panels show
(made by color_study.py, show_yellow_brown; second pass 2026-11-15)

One column per strip, its post-image, ordered by arsenic (ug/L in the
column title).  Each channel is the average of 4 repeat photos.

Top row: a plain color picture, for the eye only: the white LED photo,
each camera layer divided by its gray chips.

The other rows come from each pixel's absorbances in these channels:
{ch}.
A = -log10(post-image / pre-image lined up), bigger = darker.  One matrix
multiplication (chain step 8) splits each pixel's list into an even
darkening of every channel (not shown), an amount of yellow and an amount
of brown.

Second row, yellow amount c_Y: 1 = as much yellow as the average of
{y}'s interior (the yellow reference).  0 = none.  Darker = more.
Below 0 (palest) means the pixel cannot be a positive mix of the two
references.

Third row, brown amount c_B: 1 = as much brown as the average of {b}'s
interior (the brown reference).  Darker = more.

Bottom row, leftover: what the even darkening, the yellow and the brown
together cannot describe, per pixel, as the root mean square over the
channels, in A.  Camera noise alone, after averaging 4 photos, gives
about 0.003-0.005 A per channel here (post and pre-image together).  More
than that means a third color, or a pixel at the edge where the channels
see slightly different spots.

Axes: the pad, layer pixels (one layer pixel = 2 sensor pixels).
"""

ABOUT_YB = {
    "what": "Step 3c: yellow and brown reference lists, the unmixing "
            "matrix, and each pad's amounts.  color_study.yellow_brown().",
    "A": "absorbance, -log10(R_post / R_pre lined up), bigger = darker; "
         "each R from the average of a channel's repeat frames",
    "channels": "LED.layer of each entry in y, b and the columns of M",
    "y, b": f"yellow and brown reference lists: mean A over the interior "
            f"of {YELLOW_REF} (yellow) and of {BROWN_REF} (brown)",
    "M": "rows gamma, c_Y, c_B: M = (S^T S)^-1 S^T with S = [1 y b]; "
         "(gamma, c_Y, c_B) = M A for a pixel's list A",
    "gamma": "even darkening of every channel, A (interior median)",
    "c_Y, c_B": "amounts of yellow and brown, no units: 1 = the reference "
                "pad's interior average (interior medians)",
    "c_Y_pad_mean, c_B_pad_mean": "the same, mean over every pad pixel",
    "leftover_rms": "median over the interior of the per-pixel root mean "
                    "square of A - S (M A), A",
    "conc": "arsenic, ug/L (chain_samples.concentration)",
    "n": "pad pixels used",
}


# ── 3c-2: every pixel on two-channel graphs, with the yellow and brown
#    lines ──────────────────────────────────────────────────────────────
#
# On a graph of one channel's A against another's, a pure color is a
# straight line from (0, 0): twice the dye, twice the A in both channels.
# A pixel that is a mix of yellow and brown (positive amounts of each)
# lies in the wedge between the two lines.  An even darkening moves a
# pixel along the 45-degree diagonal.  A pixel outside the wedge, by more
# than its noise, is not a mix of these two references.

#: The x axis of every panel: the channel the stains absorb most strongly
#: of the ones with little noise.
SCATTER_X = ("royal_blue", "B")
#: The y axes, one panel each.
SCATTER_Y = (("violet", "B"), ("cyan", "B"), ("green", "Gb"),
             ("photo_red", "R"))


def show_scatter(res, zone_name="interior", savefig=True):
    """
    Step 3c-2: every pixel of every pad on two-channel graphs, x =
    SCATTER_X, y = each of SCATTER_Y, with the yellow and brown reference
    lines drawn from (0, 0).  C1049's pixels are colored by their distance
    from the dot's center: a pixel's A in SCATTER_X (darker = nearer).

    Args:
        res: yellow_brown() result (channels, y, b, pads).
        zone_name: white_study zone whose pixels are drawn ('interior' =
            8 or more layer px from every edge).
    Saved as fig/color_3c2_scatter.png with a .txt beside it.
    """
    import matplotlib.pyplot as plt
    chans = [tuple(c.split(".")) for c in res["channels"]]
    ix = chans.index(SCATTER_X)
    y, b = np.array(res["y"]), np.array(res["b"])
    fig, axs = plt.subplots(2, 2, figsize=(11, 9.5), sharex=True)
    xmax = 0
    marks = {YELLOW_REF: ("tab:olive", "o"), BROWN_REF: ("saddlebrown", "s")}
    for r in sorted(res["pads"], key=lambda r: r["conc"] or 0):
        A, zone, pad = pad_lists(r["file"], chans)
        z = zone[zone_name] & pad
        X = A[:, z]
        xmax = max(xmax, np.percentile(X[ix], 99.9))
        for k, cell in enumerate(SCATTER_Y):
            ax = axs.flat[k]
            iy = chans.index(cell)
            lab = f"{r['sample']} {r['conc']:g} ug/L ({z.sum()} px)"
            if r["sample"] in marks:
                col_, m = marks[r["sample"]]
                ax.scatter(X[ix], X[iy], s=3, c=col_, marker=m, alpha=0.35,
                           lw=0, label=lab)
            else:
                sc = ax.scatter(X[ix], X[iy], s=4, c=X[ix], cmap="viridis",
                                vmin=0.25, vmax=0.8, alpha=0.7, lw=0,
                                label=lab)
        a1.close_all()
    t = np.linspace(0, 3, 50)
    for k, cell in enumerate(SCATTER_Y):
        ax = axs.flat[k]
        iy = chans.index(cell)
        ax.plot(t * y[ix], t * y[iy], color="goldenrod", lw=1.5,
                label=f"yellow line ({YELLOW_REF} mean x amount)")
        ax.plot(t * b[ix], t * b[iy], color="saddlebrown", lw=1.5,
                label=f"brown line ({BROWN_REF} mean x amount)")
        ax.plot([0, 1], [0, 1], color="gray", lw=0.8, ls=":",
                label="even darkening (45 deg)")
        ax.set_ylabel(f"{a1.cell_name(cell)} post, A  (bigger = darker)")
        ax.set_xlim(0, xmax * 1.05)
        ax.set_ylim(min(-0.02, ax.get_ylim()[0]), None)
        ax.grid(alpha=0.3)
    for ax in axs[1]:
        ax.set_xlabel(f"{a1.cell_name(SCATTER_X)} post, A  "
                      "(bigger = darker)")
    axs.flat[0].legend(fontsize=7, loc="upper left", markerscale=3)
    fig.colorbar(sc, ax=axs, fraction=0.02, pad=0.02,
                 label=f"C1049 pixels: {a1.cell_name(SCATTER_X)} A "
                       "(darker = nearer the dot's center)")
    fig.suptitle(f"Step 3c-2: every {zone_name} pad pixel, post-image A "
                 "in two channels; a mix of yellow and brown lies between "
                 "the two lines", fontsize=10)
    if savefig:
        FIG.mkdir(exist_ok=True)
        out = FIG / "color_3c2_scatter.png"
        fig.savefig(out, dpi=100)
        out.with_suffix(".txt").write_text(SCATTER_NOTE.format(
            x=a1.cell_name(SCATTER_X), y=YELLOW_REF, b=BROWN_REF,
            zone=zone_name))
        print(f"fig saved to {out}")
    return fig


SCATTER_NOTE = """\
color_3c2_scatter.png -- what the graphs show
(made by color_study.py, show_scatter, 2026-11-15)

Each dot is one pad pixel ({zone} pixels: 8 or more layer pixels from
every edge) of a post-image.  Every channel is the average of 4 repeat
photos.  Both axes are absorbance, A = -log10(post-image / pre-image lined
up): 0 = unchanged paper, bigger = darker.  0.1 A is about 21 % less
light.

x axis, every panel: {x}, the channel the stains darken most of those
with little noise.  y axis: one other channel per panel.

Lines, all from (0, 0):
  yellow line: the average color of {y}'s interior, scaled up and down.
      A pixel with only that yellow, in any amount, lies on it.
  brown line: the same for {b}'s interior.
  dotted 45-degree line: the direction of an even darkening (the same
      added A in both channels).
A pixel that is a mix of the yellow and the brown (positive amounts of
each) lies in the wedge between the yellow and brown lines, shifted at
most a little along the dotted direction.  A cloud that leaves the wedge,
by more than its own width, is not a mix of these two references.

Colors: {y} olive circles, {b} brown squares.  C1049's pixels are colored
by their own x value, so the dot's center (darkest) is yellow-green and
the rest of the pad is purple.
"""


# ── 3d: how many colors?  Principal components in noise units ──────────
#
# The question: how many separate color directions carry signal in the
# pixels of C1049-C1051?  Two (yellow and brown) means the two-color model
# is enough; three means a third color (e.g. C1049's dot).
#
# Method, in words:
#   1. Each pixel has a list of values, one per channel.
#   2. Each channel is divided by its own camera noise (`frame_noise`), so
#      every value is in "noise units".  Pure noise then has a spread of 1
#      in every direction.
#   3. The even darkening (the same A added to every channel) is removed
#      from every pixel's list, because chain step 8 measures it
#      separately (gamma).
#   4. The principal components are the directions, at right angles to
#      each other, that hold the most spread, then the most of what is
#      left, and so on.  Each one's spread is the mean squared length of
#      the pixels along it, in noise units squared.  About 1 = noise only.
#
# Two versions:
#   A  "through zero": the channels with their own pre-image white, A =
#      -log10(post / pre-image lined up).  Spread is measured from A = 0
#      (no dye), as tex/chain_unified.tex step 8 asks.
#   B  "from the average": every usable channel, from the post-image
#      alone, -log10(R_post).  A channel without a pre-image white differs
#      from its true A by a constant (its unknown white).  Measuring from
#      the average over all pixels removes any constant, so the unknown
#      white drops out -- if the dry pad reads the same on every strip
#      (`white_agreement` checks that on the channels that have a white).

#: Every LED in the post-images, in order of wavelength.
ALL_LEDS = ("violet", "royal_blue", "blue", "cyan", "green", "amber",
            "red_orange", "red", "photo_red", "white")
#: The camera layers tried for every LED; a layer the Pi did not store, or
#: that clips, on any of the pads is left out (`usable_post_channels`).
LAYERS = ("B", "Gb", "Gr", "R")
#: Channels with a pre-image white on every new strip (version A).
WHITE_CHANNELS = (("violet", "B"), ("royal_blue", "B"), ("cyan", "B"),
                  ("green", "B"), ("green", "Gb"), ("green", "Gr"),
                  ("photo_red", "Gb"), ("photo_red", "Gr"),
                  ("photo_red", "R"))
#: C1049's dot center: interior pixels darker than this in royal_blue.B,
#: A.  (74 pixels; the rest of the pad is 0.25-0.40 A.)
DOT_SAMPLE, DOT_MIN_A = "C1049", 0.65


def frame_noise(f, cell, sec, fr=None):
    """
    Camera noise of one channel's frame average, per pixel, in A.

    From the repeat frames (`shading_review.frame`'s 'frames'): at each
    inner-pad pixel, the spread (standard deviation) of log10(frame /
    the pixel's mean over the frames); squared, averaged over the pixels,
    square-rooted, then divided by sqrt(number of frames).  NaN if the
    channel has only one frame.  `fr`: that channel's `sr.frame` result,
    if already read (saves reading it again).
    """
    fr = sr.frame(f, cell, sec) if fr is None else fr
    c = a1.open_sample(f, sec)
    if len(fr["frames"]) < 2:
        return float("nan")
    st = np.array([c.layer(cell[0], lab, cell[1])[0][fr["inner"]]
                   for lab in fr["frames"]])        # (frames, pixels)
    L = np.log10(st / st.mean(0))
    return float(np.sqrt(np.nanmean(L.var(0, ddof=1)) / len(st)))


def usable_post_channels(files, leds=ALL_LEDS, layers=LAYERS):
    """
    Channels `rung_verdict` accepts in the post-image of every file, and
    that have repeat frames there (so their camera noise can be measured).
    On C1049-C1051 the white LED has one frame, so it is left out; its Gb
    and Gr layers also clip on C1051.
    """
    out = []
    for led in leds:
        for lay in layers:
            cell = (led, lay)
            if all(_usable(f, cell, "") and len(sr.frame(f, cell, "")["frames"])
                   > 1 for f in files):
                out.append(cell)
    return out


def pca_data(files, chans, version):
    """
    Every interior pad pixel's list, in A, and the camera noise per
    channel.

    Args:
        files: sample -> file.
        chans: list of (led, layer).
        version: 'A' (A = -log10(post / pre-image lined up)) or 'B'
            (-log10(R_post), no white).

    Returns:
        dict: X array (pixels, channels), A; sigma array (channels,), the
        camera noise of one pixel's value, A (version A: post and
        pre-image noise combined, sqrt(post^2 + pre^2), the median over
        the files); sample array (pixels,) of str; dot array (pixels,)
        bool, C1049's dot center; where list of (sample, rows, cols) for
        drawing maps.
    """
    X, smp, dot, where, sig = [], [], [], [], []
    for s, f in files.items():
        Aw, zone, pad = pad_lists(f, WHITE_CHANNELS)     # for the dot mask
        z = zone["interior"] & pad
        if version == "A":
            A = np.stack([Aw[WHITE_CHANNELS.index(c)] for c in chans])
        else:
            A = np.stack([-np.log10(np.array(sr.frame(f, c, "")["R"], float))
                          for c in chans])
        z &= np.isfinite(A).all(0)
        X.append(A[:, z].T)
        smp += [s] * int(z.sum())
        rb = Aw[WHITE_CHANNELS.index(("royal_blue", "B"))][z]
        dot.append((rb > DOT_MIN_A) if s == DOT_SAMPLE else np.zeros(z.sum(), bool))
        rr, cc = np.nonzero(z)
        where.append((s, rr, cc))
        n = [frame_noise(f, c, "") ** 2 + (frame_noise(f, c, a1.PRE) ** 2
             if version == "A" else 0.0) for c in chans]
        sig.append(np.sqrt(n))
        a1.close_all()
    return dict(X=np.vstack(X), sigma=np.nanmedian(sig, 0),
                sample=np.array(smp), dot=np.concatenate(dot), where=where)


def noise_pca(X, sigma, through_zero, remove_even=True):
    """
    Principal components in noise units, with the even darkening removed
    (or kept, for step 3f's count of colors plus brightness).

    Args:
        X: array (pixels, channels), A.
        sigma: array (channels,), camera noise of one pixel's value, A.
        through_zero: True to measure spread from A = 0 (version A), False
            from the average over all pixels (version B).
        remove_even: True (3d) to take out the even darkening first; False
            to keep it, so it shows up as one of the directions.

    Returns:
        dict:
          spread  array (channels - 1,), or (channels,) when the darkening
                  is kept: mean squared length of the pixels
                  along each direction, noise units squared (1 = noise);
          dirs    array (directions, channels): each direction, in noise
                  units (unit length);
          dirs_A  the same directions turned back into A per channel
                  (dirs * sigma), each scaled to largest entry 1, for
                  reading which channels a direction uses;
          Z       array (pixels, channels): the pixels in noise units,
                  darkening removed (and the average, if not through zero);
          u       array (channels,): the even-darkening direction in noise
                  units (1/sigma, unit length).
    """
    Z = X / sigma                                   # (pixels, channels)
    u = (1 / sigma) / np.linalg.norm(1 / sigma)     # darkening direction
    if remove_even:
        Z = Z - np.outer(Z @ u, u)                  # remove darkening
    if not through_zero:
        Z = Z - Z.mean(0)                           # remove the average
    # SVD: Z = U s Vt; rows of Vt are the directions, s^2/pixels the spread
    _, s, Vt = np.linalg.svd(Z, full_matrices=False)
    k = len(sigma) - 1 if remove_even else len(sigma)
    dA = Vt[:k] * sigma
    dA = dA / np.abs(dA).max(1, keepdims=True)
    return dict(spread=s[:k] ** 2 / len(Z), dirs=Vt[:k], dirs_A=dA, Z=Z,
                u=u)


def leftover_after(Z, dirs, m, removed=1):
    """Per pixel, the length left after the first m directions, noise
    units, divided by sqrt(channels left) so noise alone gives about 1.
    `removed`: directions taken out before the analysis (1 = the even
    darkening, as in 3d; 0 when it was kept)."""
    P = Z - (Z @ dirs[:m].T) @ dirs[:m]
    return np.sqrt((P ** 2).sum(1) / (Z.shape[1] - removed - m))


def white_agreement(files, chans=WHITE_CHANNELS):
    """
    Version B's assumption: does the dry pad read the same on every strip?
    Per channel, the pre-image interior mean of R (gray-chip ratio) on
    each strip, and the largest difference between strips in A.
    """
    rows = []
    for c in chans:
        m = {}
        for s, f in files.items():
            fr = sr.frame(f, c, a1.PRE)
            m[s] = float(np.nanmean(fr["R"][fr["inner"]]))
        v = np.array(list(m.values()))
        rows.append(dict(channel=a1.cell_name(c), R=m,
                         range_A=float(np.log10(v.max() / v.min()))))
    a1.close_all()
    return rows


def color_count(files=None):
    """
    Step 3d: both versions of the principal components.

    Returns:
        dict with, for 'A' and 'B': channels, sigma (A), spread, dirs_A,
        the leftover per pixel after 2 and after 3 directions (median over
        each sample, and over C1049's dot center), and whether each
        reference list (C1051 mean, C1050 mean, C1049 dot mean) lies in
        the plane of the first two directions; plus white_agreement.
    """
    files = files or NEW_FILES
    out = {"white_agreement": white_agreement(files)}
    for version, chans in (("A", list(WHITE_CHANNELS)),
                           ("B", usable_post_channels(list(files.values())))):
        d = pca_data(files, chans, version)
        p = noise_pca(d["X"], d["sigma"], through_zero=(version == "A"))
        left = {m: leftover_after(p["Z"], p["dirs"], m) for m in (1, 2, 3, 4)}
        groups = {s: d["sample"] == s for s in files}
        groups["C1049 dot center"] = d["dot"]
        groups["C1049 outside the dot"] = (d["sample"] == "C1049") & ~d["dot"]
        # reference lists: mean of each group in noise units; how far off
        # the plane of the first two directions, against the mean's noise
        refs = {}
        for g, sel in groups.items():
            zm = p["Z"][sel].mean(0)
            off = zm - (zm @ p["dirs"][:2].T) @ p["dirs"][:2]
            refs[g] = dict(n=int(sel.sum()),
                           off_plane=float(np.linalg.norm(off)),
                           off_plane_in_mean_noise=float(
                               np.linalg.norm(off) * np.sqrt(sel.sum())))
        out[version] = dict(
            channels=[a1.cell_name(c) for c in chans],
            sigma=d["sigma"].tolist(), spread=p["spread"].tolist(),
            dirs_A=p["dirs_A"].tolist(),
            leftover={g: {m: float(np.median(left[m][sel]))
                          for m in left} for g, sel in groups.items()},
            refs=refs)
        out[version]["_maps"] = (d["where"], d["sample"], left)
    return out


def summarize_color_count(res):
    """Print the 3d tables."""
    print("Step 3d. Dry pad on each strip (pre-image interior mean R):")
    for r in res["white_agreement"]:
        print(f"  {r['channel']:14s}" + "".join(
            f"{s} {v:.4f}  " for s, v in r["R"].items())
            + f"  largest difference {r['range_A']:.4f} A")
    for v in ("A", "B"):
        r = res[v]
        print(f"\nVersion {v}: {len(r['channels'])} channels "
              f"({'through zero' if v == 'A' else 'from the average'}).")
        print("  camera noise per pixel, A: " + ", ".join(
            f"{c} {s:.4f}" for c, s in zip(r["channels"], r["sigma"])))
        print("  spread along each direction, noise units squared "
              "(about 1 = noise only):")
        print("   " + "  ".join(f"{i+1}: {s:.1f}" for i, s in
                                enumerate(r["spread"])))
        print("  leftover per pixel after keeping m directions, noise units "
              "(about 1 = noise only), medians:")
        print(f"   {'group':24s}{'px':>6s}" + "".join(
            f"{'m=' + str(m):>8s}" for m in (1, 2, 3, 4)))
        for g, lm in r["leftover"].items():
            print(f"   {g:24s}{r['refs'][g]['n']:6d}" + "".join(
                f"{lm[m]:8.2f}" for m in (1, 2, 3, 4)))
        print("  group means off the plane of directions 1-2: noise units "
              "of one pixel / of the group's mean:")
        for g, x in r["refs"].items():
            print(f"   {g:24s}{x['off_plane']:8.2f} / "
                  f"{x['off_plane_in_mean_noise']:8.1f}")


def show_color_count(res, savefig=True):
    """
    Step 3d figures.
      fig/color_3d_spread.png: spread along each direction, both versions,
          log scale, with the noise level.
      fig/color_3d_directions_B.png: the first four directions of version
          B as bars over the channels (A per channel, largest = 1).
      fig/color_3d_leftover.png: leftover maps after 2 and after 3
          directions, both versions, per pad.
    Each with a .txt beside it.
    """
    import matplotlib.pyplot as plt
    FIG.mkdir(exist_ok=True)
    saved = []
    # 1. spread
    fig, axs = plt.subplots(2, 1, figsize=(8, 7), sharex=True)
    for ax, v in zip(axs, ("A", "B")):
        sp = res[v]["spread"]
        ax.semilogy(np.arange(1, len(sp) + 1), sp, "o-")
        ax.axhline(1, color="gray", ls=":", label="noise only (1)")
        ax.set_ylabel("spread, noise units squared")
        ax.set_title(f"Version {v}: {len(res[v]['channels'])} channels, "
                     + ("through zero, own pre-image white" if v == "A" else
                        "from the average, post-image only"), fontsize=9)
        ax.grid(alpha=0.3, which="both")
        ax.legend(fontsize=8)
    axs[1].set_xlabel("direction number (largest spread first)")
    fig.suptitle("Step 3d: C1049-C1051 interior pixels, spread along each "
                 "color direction (even darkening removed)", fontsize=10)
    saved.append(("color_3d_spread", fig))
    # 2. directions of version B
    r = res["B"]
    fig, axs = plt.subplots(4, 1, figsize=(11, 9), sharex=True)
    x = np.arange(len(r["channels"]))
    for i, ax in enumerate(axs):
        ax.bar(x, r["dirs_A"][i], color="tab:blue")
        ax.axhline(0, color="k", lw=0.6)
        ax.set_ylabel(f"direction {i+1}\n(spread "
                      f"{r['spread'][i]:.0f})", fontsize=9)
        ax.grid(alpha=0.3, axis="y")
    axs[-1].set_xticks(x)
    axs[-1].set_xticklabels(r["channels"], rotation=70, fontsize=8)
    fig.suptitle("Step 3d version B: the first four color directions, as A "
                 "per channel (largest entry = 1); the sign of a direction "
                 "is arbitrary", fontsize=10)
    fig.tight_layout()
    saved.append(("color_3d_directions_B", fig))
    # 3. leftover maps
    files = list(NEW_FILES)
    fig, axs = plt.subplots(4, len(files), figsize=(2.4 * len(files) + 1.5,
                                                     10), squeeze=False)
    for i, (v, m) in enumerate((("A", 2), ("A", 3), ("B", 2), ("B", 3))):
        where, smp, left = res[v]["_maps"]
        for j, (s, rr, cc) in enumerate(where):
            img = np.full(a1.pad_of(NEW_FILES[s], "").mask(0).shape, np.nan)
            img[rr, cc] = left[m][smp == s]
            ax = axs[i, j]
            im = ax.imshow(img, cmap="magma", vmin=0, vmax=4)
            p = a1.pad_of(NEW_FILES[s], "")
            ax.set_xlim(p.aL - 2, p.aR + 2); ax.set_ylim(p.bot + 2, p.top - 2)
            ax.set_xticks([]); ax.set_yticks([])
            if i == 0:
                ax.set_title(f"{s} post", fontsize=9)
            if j == 0:
                ax.set_ylabel(f"version {v}\nafter {m} directions",
                              fontsize=9)
    fig.colorbar(im, ax=axs, fraction=0.03,
                 label="leftover per pixel, noise units (1 = noise only)")
    fig.suptitle("Step 3d: what is left after keeping 2 or 3 color "
                 "directions", fontsize=10)
    saved.append(("color_3d_leftover", fig))
    a1.close_all()
    if savefig:
        for name, f in saved:
            out = FIG / f"{name}.png"
            f.savefig(out, dpi=100)
            out.with_suffix(".txt").write_text(PCA_NOTE)
            print(f"fig saved to {out}")


PCA_NOTE = """\
color_3d_*.png -- step 3d, how many color directions carry signal
(made by color_study.py, show_color_count, 2026-11-15)

Pixels: every interior pad pixel (8 or more layer pixels from every edge)
of the post-images of C1049 (20 ug/L, dark dot), C1050 (600 ug/L) and
C1051 (12 ug/L).  Each channel is the average of 4 repeat photos.

Noise units: each channel's value is divided by its own camera noise per
pixel (from the spread between the 4 repeat photos).  In these units,
pure camera noise has a spread of about 1 in every direction.

Even darkening removed: the part of each pixel's list that is the same
added A in every channel is taken out first (chain step 8 measures it
separately).

Version A (9 channels with a pre-image white): A = -log10(post /
pre-image lined up), 0 = no dye; spread is measured from 0.
Version B (every channel the Pi stored, unclipped, with repeat photos,
21; the white LED had one photo): the
post-image alone, -log10(post / gray chips); spread is measured from the
average over all pixels, which removes each channel's unknown white.

color_3d_spread.png: y axis, the spread along each direction (mean
squared distance of the pixels along it), noise units squared, log scale.
x axis, the directions, largest first.  A direction near the dotted line
at 1 holds only noise; well above it, real color differences.

color_3d_directions_B.png: each panel one direction, as the change in A
per channel that moving along it makes (largest = 1).  The sign is
arbitrary: a direction and its mirror image are the same direction.

color_3d_leftover.png: per pixel, what is left after keeping the first 2
(or 3) directions, noise units, scaled so that noise alone gives about 1.
Bright = the kept directions do not describe that pixel.
"""

ABOUT_3D = {
    "what": "Step 3d: principal components of C1049-C1051 interior "
            "pixels in noise units, even darkening removed. "
            "color_study.color_count().",
    "A / B": "A: 9 channels with a pre-image white, A = -log10(post/pre "
             "lined up), through zero.  B: every usable post-image "
             "channel, -log10(R_post), from the average.",
    "sigma": "camera noise of one pixel's value, A (from 4 repeat frames)",
    "spread": "mean squared length of the pixels along each direction, "
              "noise units squared; about 1 = noise only",
    "dirs_A": "each direction as A per channel, largest entry 1; sign "
              "arbitrary",
    "leftover": "median per group of the per-pixel leftover after m "
                "directions, noise units, about 1 = noise only",
    "refs": "group means' distance off the plane of directions 1-2: per "
            "pixel noise units, and in units of the mean's own noise",
    "white_agreement": "pre-image interior mean R per strip and the "
                       "largest difference between strips, A",
}


def _json_ready(res):
    """color_count result without the map arrays."""
    return {k: ({kk: vv for kk, vv in v.items() if kk != "_maps"}
                if isinstance(v, dict) else v) for k, v in res.items()}


# ── 3e: three reference colors: yellow, brown and the dot's dark yellow ─
#
# Step 3d found a third color direction, at C1049's dot.  Bill's idea
# (2026-11-15): an arsenic atom holding two mercury bromide groups may give
# its own color, a darker yellow (tex/chain_unified.tex step 9, H2).  If
# each molecule absorbs its own form's color, the absorbances of the many
# molecules at a pixel still add, so the model becomes
#     A = gamma*1 + c_Y*y + c_B*b + c_D*d + leftover,
# with d the dot center's average list, S = [1 y b d], M = (S^T S)^-1 S^T.

def three_colors(files=None, channels=WHITE_CHANNELS):
    """
    Step 3e: amounts of yellow, brown and the dot's color, and the
    leftover with two and with three reference colors.

    References (means over interior pixels, A): y = C1051, b = C1050,
    d = C1049's dot center (royal_blue.B A > DOT_MIN_A).  c_D = 1 means as
    much of the dot's color as its center's average.

    Returns:
        dict: channels, y, b, d, M3 (rows gamma, c_Y, c_B, c_D), row
        lengths of M2 and M3 (noise multipliers), pads (per sample:
        interior medians of every amount and of the leftover, A, with two
        and three references; for C1049 also the dot center and outside).
    """
    files = files or NEW_FILES
    chans = list(channels)
    data = {s: pad_lists(f, chans) for s, f in files.items()}
    a1.close_all()

    def mean_over(s, extra=None):
        A, zone, pad = data[s]
        z = zone["interior"] & pad
        if extra is not None:
            z &= extra(A)
        return A[:, z].mean(1)
    ib = chans.index(("royal_blue", "B"))
    is_dot = lambda A: A[ib] > DOT_MIN_A
    y, b = mean_over(YELLOW_REF), mean_over(BROWN_REF)
    d = mean_over(DOT_SAMPLE, is_dot)
    S2, M2 = unmix_matrix(y, b)
    S3, M3 = unmix_matrix(y, b, d)
    pads = []
    for s, (A, zone, pad) in data.items():
        z = zone["interior"] & pad
        groups = {s: z}
        if s == DOT_SAMPLE:
            groups[f"{s} dot center"] = z & is_dot(A)
            groups[f"{s} outside the dot"] = z & ~is_dot(A)
        for g, sel in groups.items():
            X = A[:, sel]
            row = dict(group=g, n=int(sel.sum()), conc=cs.concentration(s))
            for tag, S_, M_ in (("2", S2, M2), ("3", S3, M3)):
                c = M_ @ X
                left = X - S_ @ c
                row[f"leftover_{tag}"] = float(np.median(
                    np.sqrt((left ** 2).mean(0))))
                for name, v in zip(("gamma", "c_Y", "c_B", "c_D"), c):
                    row[f"{name}_{tag}"] = float(np.median(v))
            pads.append(row)
    return dict(channels=[a1.cell_name(c) for c in chans], y=y.tolist(),
                b=b.tolist(), d=d.tolist(), M3=M3.tolist(),
                row_len_2=np.linalg.norm(M2, axis=1).tolist(),
                row_len_3=np.linalg.norm(M3, axis=1).tolist(), pads=pads)


def summarize_three_colors(res):
    """Print the 3e reference lists, noise multipliers and amounts."""
    print("Step 3e: reference lists, A (bigger = darker)")
    print(f"  {'':12s}" + "".join(f"{c:>13s}" for c in res["channels"]))
    for n in ("y", "b", "d"):
        lab = {"y": "yellow y", "b": "brown b", "d": "dot d"}[n]
        print(f"  {lab:12s}" + "".join(f"{x:>13.4f}" for x in res[n]))
    print("  row lengths of M (noise in each amount per unit of channel "
          "noise): two colors gamma/c_Y/c_B "
          + "/".join(f"{x:.1f}" for x in res["row_len_2"])
          + "; three colors gamma/c_Y/c_B/c_D "
          + "/".join(f"{x:.1f}" for x in res["row_len_3"]))
    print(f"\n  {'group':22s}{'px':>5s}   two colors: {'c_Y':>6s}{'c_B':>6s}"
          f"{'left A':>8s}   three colors: {'gamma':>7s}{'c_Y':>6s}{'c_B':>6s}"
          f"{'c_D':>6s}{'left A':>8s}   (interior medians)")
    for r in res["pads"]:
        print(f"  {r['group']:22s}{r['n']:5d}   {'':12s}{r['c_Y_2']:6.2f}"
              f"{r['c_B_2']:6.2f}{r['leftover_2']:8.4f}   {'':14s}"
              f"{r['gamma_3']:+7.3f}{r['c_Y_3']:6.2f}{r['c_B_3']:6.2f}"
              f"{r['c_D_3']:6.2f}{r['leftover_3']:8.4f}")


def show_three_colors(res, savefig=True):
    """
    Step 3e figures.
      fig/color_3e_maps.png: per pad (columns): color picture, c_Y, c_B,
          c_D, then the leftover with two references and with three, on
          one scale (before and after).
      fig/color_3e_dot_profile.png: C1049, every amount and the leftover
          against distance from the dot's center (ring averages), stacked
          panels sharing the x axis.
    Each with a .txt beside it.
    """
    import matplotlib.pyplot as plt
    chans = [tuple(c.split(".")) for c in res["channels"]]
    y, b, d = (np.array(res[k]) for k in ("y", "b", "d"))
    S2, M2 = unmix_matrix(y, b)
    S3, M3 = unmix_matrix(y, b, d)
    files = list(NEW_FILES)
    fig, axs = plt.subplots(6, len(files), figsize=(2.4 * len(files) + 2,
                                                     14), squeeze=False)
    specs = (("yellow c_Y", -0.25, 1.5, "YlOrBr"),
             ("brown c_B", -0.25, 1.5, "copper_r"),
             ("dot color c_D", -0.25, 1.5, "Oranges"),
             ("leftover, 2 refs, A", 0, 0.03, "Greys"),
             ("leftover, 3 refs, A", 0, 0.03, "Greys"))
    prof = None
    for j, s in enumerate(files):
        A, zone, pad = pad_lists(NEW_FILES[s], chans)
        maps = np.full((5,) + A.shape[1:], np.nan)
        X = A[:, pad]
        c3 = M3 @ X
        maps[0][pad], maps[1][pad], maps[2][pad] = c3[1], c3[2], c3[3]
        for k, (S_, M_) in ((3, (S2, M2)), (4, (S3, M3))):
            left = X - S_ @ (M_ @ X)
            maps[k][pad] = np.sqrt((left ** 2).mean(0))
        p = a1.pad_of(NEW_FILES[s], "")
        panels = [(true_color(NEW_FILES[s]), None)] + list(zip(maps, specs))
        for i, (m, sp) in enumerate(panels):
            ax = axs[i, j]
            if sp is None:
                ax.imshow(m)
            else:
                im = ax.imshow(m, cmap=sp[3], vmin=sp[1], vmax=sp[2])
                if j == len(files) - 1:
                    fig.colorbar(im, ax=axs[i, :], fraction=0.03, pad=0.02,
                                 label=sp[0])
            ax.set_xlim(p.aL - 3, p.aR + 3); ax.set_ylim(p.bot + 3, p.top - 3)
            ax.set_xticks([]); ax.set_yticks([])
            if i == 0:
                ax.set_title(f"{s}, {cs.concentration(s):g} ug/L, post",
                             fontsize=9)
        if s == DOT_SAMPLE:
            prof = (A, zone, pad, maps, c3)
        a1.close_all()
    for i, lab in enumerate(("white LED\nphoto", "yellow", "brown",
                             "dot color", "leftover\n2 references",
                             "leftover\n3 references")):
        axs[i, 0].set_ylabel(lab, fontsize=9)
    fig.suptitle("Step 3e: three reference colors: yellow (1 = C1051), "
                 "brown (1 = C1050), dot color (1 = C1049 dot center)",
                 fontsize=10)
    out = [("color_3e_maps", fig)]
    # profile from the dot's center
    A, zone, pad, maps, c3 = prof
    ib = chans.index(("royal_blue", "B"))
    z = zone["interior"] & pad
    core = z & (A[ib] > DOT_MIN_A)
    rr, cc = np.nonzero(core)
    r0, c0 = rr.mean(), cc.mean()
    R, C = np.indices(A.shape[1:])
    dist = np.hypot(R - r0, C - c0)
    sel = pad & ~zone["bands 0-1"] & ~zone["corner"]
    edges = np.arange(0, 26, 1.0)
    mid = 0.5 * (edges[1:] + edges[:-1])

    def ring(m):
        return [np.nanmean(m[sel & (dist >= a) & (dist < b2)])
                if (sel & (dist >= a) & (dist < b2)).sum() >= 5 else np.nan
                for a, b2 in zip(edges[:-1], edges[1:])]
    fig2, ax2 = plt.subplots(3, 1, figsize=(8, 9), sharex=True)
    for m, lab, colr in ((maps[0], "yellow c_Y", "goldenrod"),
                         (maps[1], "brown c_B", "saddlebrown"),
                         (maps[2], "dot color c_D", "darkorange")):
        ax2[0].plot(mid, ring(m), "o-", color=colr, label=lab, ms=3)
    ax2[0].set_ylabel("amount (1 = reference)")
    ax2[0].legend(fontsize=8)
    ax2[1].plot(mid, ring(A[ib]), "o-", color="tab:purple", ms=3,
                label="royal_blue.B")
    ax2[1].plot(mid, ring(A[chans.index(("green", "Gb"))]), "o-",
                color="tab:green", ms=3, label="green.Gb")
    ax2[1].plot(mid, ring(A[chans.index(("photo_red", "R"))]), "o-",
                color="tab:red", ms=3, label="photo_red.R")
    ax2[1].set_ylabel("A, post (bigger = darker)")
    ax2[1].legend(fontsize=8)
    ax2[2].plot(mid, ring(maps[3]), "o-", color="gray", ms=3,
                label="2 references")
    ax2[2].plot(mid, ring(maps[4]), "o-", color="k", ms=3,
                label="3 references")
    ax2[2].set_ylabel("leftover, A (rms over channels)")
    ax2[2].legend(fontsize=8)
    ax2[2].set_xlabel("distance from the dot's center, layer px "
                      "(ring averages, 1 px wide; edge bands 0-1 left out)")
    for a in ax2:
        a.grid(alpha=0.3)
    fig2.suptitle("Step 3e: C1049 post, amounts against distance from the "
                  "dot's center", fontsize=10)
    out.append(("color_3e_dot_profile", fig2))
    if savefig:
        FIG.mkdir(exist_ok=True)
        for name, f in out:
            pth = FIG / f"{name}.png"
            f.savefig(pth, dpi=100)
            pth.with_suffix(".txt").write_text(THREE_NOTE.format(
                ch=", ".join(res["channels"])))
            print(f"fig saved to {pth}")


THREE_NOTE = """\
color_3e_*.png -- step 3e, three reference colors
(made by color_study.py, show_three_colors, 2026-11-15)

Each pixel's absorbances in these channels: {ch}.
A = -log10(post-image / pre-image lined up), bigger = darker; every
channel is the average of 4 repeat photos.  One matrix multiplication
splits each pixel's list into an even darkening of every channel (not
shown), and amounts of three reference colors:
  yellow c_Y: 1 = the average of C1051's interior (12 ug/L);
  brown c_B: 1 = the average of C1050's interior (600 ug/L);
  dot color c_D: 1 = the average of C1049's dot center (20 ug/L; interior
      pixels darker than 0.65 A in royal_blue.B).
0 = none of that color.  Below 0 means the pixel's color lies outside
what positive amounts of the three can make.

color_3e_maps.png: columns = strips; rows = a plain color picture (white
LED, for the eye only), the three amounts, then the leftover (what the
darkening and the reference colors cannot describe, root mean square
over the channels, A) with two references (yellow, brown) and with three,
on one scale.  Camera noise alone gives about 0.004 A here.

color_3e_dot_profile.png: C1049 only.  x axis, distance from the dot's
center (the average position of its darkest pixels), in layer pixels
(one layer pixel = 2 sensor pixels).  Each point is the average over a
ring 1 px wide.  Top: the three amounts.  Middle: A in three channels.
Bottom: the leftover with two and with three references.
"""

ABOUT_3E = {
    "what": "Step 3e: three reference colors (yellow C1051, brown C1050, "
            "dot center C1049), color_study.three_colors().",
    "A": "absorbance, -log10(R_post / R_pre lined up), bigger = darker",
    "y, b, d": "reference lists: mean A over C1051's interior, C1050's "
               "interior, and C1049's interior pixels with royal_blue.B "
               "A > 0.65",
    "M3": "rows gamma, c_Y, c_B, c_D; M = (S^T S)^-1 S^T, S = [1 y b d]",
    "row_len_2, row_len_3": "length of each row of M with two / three "
                            "references: noise in that amount per unit "
                            "of channel noise",
    "pads": "per group, interior medians: gamma (A), amounts (1 = "
            "reference), leftover rms over channels (A); suffix _2 = two "
            "references, _3 = three",
}


# ── step 3f: how many colors on the strips of 2026-11-16 ───────────────
#
# THE QUESTION: how many independent directions of color change stand above
# camera noise on C1052-C1055?  Bill's hypothesis (2026-11-15/16): yellow
# (a bonding site holding 1 As atom), brown, and a third, darker,
# orange-like color (2 atoms), plus overall brightness -- so 4 directions
# with the even darkening kept, 3 with it removed.  It decides whether the
# unmixing matrix needs 2 or 3 reference colors, and which channels the
# instrument has to capture.  It ends when what is left after keeping k
# directions is about 1 noise unit everywhere, C1054's orange dots included.
#
# WHY IT WORKS: a pixel's A in one channel is about the sum over the dyes of
# (amount of that dye) x (that dye's A in that channel), plus an even
# darkening (the same A in every channel).  So every pixel's list of A
# values lies in the space spanned by a few fixed lists, one per dye plus
# the list of all ones.  The principal components find, one at a time, the
# direction the pixels spread along most.  Measured in noise units, pure
# camera noise spreads about 1 along every direction, so the number of
# directions well above 1 is the number of fixed lists the data needs.

#: The strips of 2026-11-16 (holder H; every LED but white in the
#: pre-image; all four camera layers saved; 4 repeat photos per LED).
#: sample -> file.  C1054's capture is in C1054-a.h5: C1054.h5 is an
#: abandoned first try that holds only photo_red and red in its pre-image.
FILES_3F = {"C1052": "C1052", "C1053": "C1053", "C1054": "C1054-a",
            "C1055": "C1055"}
#: Arsenic in each sample, ug/L (Bill, 2026-11-16; also in the local
#: samples.manual_read).
AMOUNT_3F = {"C1052": 12.8, "C1053": 20, "C1054": 40, "C1055": 600}
#: LEDs with a pre-image (the white LED has none), shortest wavelength first.
PRE_LEDS = tuple(led for led in ALL_LEDS if led != "white")
#: Besides the median, the leftover per strip is also reported at this
#: percentile of its pixels, so a local dot pattern is not hidden.
LEFT_PCTL = 95
#: Numbers of kept directions for which the leftover is reported.
KEEP_M = (1, 2, 3, 4, 5, 6)
#: Numbers of directions drawn as bars and as maps.
SHOW_DIRS = 5
SHOW_SCORES = 4


def white_channels_3f(files, leds=PRE_LEDS, layers=LAYERS):
    """
    Channels with a usable flat-field model (`analysis_1.models`) and
    usable (`analysis_1.rung_verdict`) in both the pre-image and the
    post-image of every file, with repeat photos in both (so their camera
    noise can be measured).

    Args:
        files: list of file names.
        leds, layers: what to try.

    Returns:
        (keep, dropped): keep, list of (led, layer), LED order of `leds`
        then layer order; dropped, dict channel name -> list of str, the
        reasons it was left out.
    """
    keep, dropped = [], {}
    for led in leds:
        for lay in layers:
            cell, why = (led, lay), []
            m = a1.models().get(cell)
            if m is None or not m.usable:
                # no flat-field fit: the weak layers, where the LED barely
                # reaches the layer (calibration p90 15-86 DN); the
                # gray-chip divisor cannot be built without the fit
                dropped[a1.cell_name(cell)] = ["no usable flat-field model"]
                continue
            for f in files:
                for sec in ("", a1.PRE):
                    tag = f"{f} {'pre' if sec else 'post'}"
                    try:
                        rung, reason = a1.rung_verdict(f, cell, sec)
                    except Exception as e:          # e.g. layer not stored
                        rung, reason = None, f"{type(e).__name__}: {e}"
                    if rung is None:
                        why.append(f"{tag}: {reason}")
                    elif len(a1.repeat_labels(f, cell, rung, sec)) < 2:
                        why.append(f"{tag}: one photo")
            if why:
                dropped[a1.cell_name(cell)] = why
            else:
                keep.append(cell)
    a1.close_all()
    return keep, dropped


def pca_data_3f(files, chans):
    """
    Every interior pad pixel's list of A, and each channel's camera noise.

    For each strip and channel the post-image and its own pre-image are
    read once (`sr.frame`, the 4 repeat photos averaged on the signal);
    A = -log10(R_post / R_pre lined up onto the post-image), exactly as
    `pair_A`; and the noise of both averages is measured from the same
    reads (`frame_noise`).

    Args:
        files: dict sample -> file.
        chans: list of (led, layer).

    Returns:
        dict:
          X       array (pixels, channels), A, bigger = darker;
          sigma   array (channels,), camera noise of one pixel's A: post
                  and pre-image noise combined, sqrt(post^2 + pre^2), the
                  median over the strips, A;
          sigma_by_strip  array (strips, channels), the same per strip, A;
          sample  array (pixels,) of str;
          where   list of (sample, rows, cols), for drawing maps;
          photos  dict sample -> channel name -> [post rung, post photos,
                  pre rung, pre photos].
    """
    X, smp, where, sig, photos = [], [], [], [], {}
    for s, f in files.items():
        pre = cs.pre_source(f)
        A, noise, info = [], [], {}
        for cell in chans:
            fpost = sr.frame(f, cell, "")
            fpre = sr.frame(pre, cell, a1.PRE)
            Rpost = np.array(fpost["R"], float)
            Rpre = np.array(fpre["R"], float)
            on = ws._carry(pre, a1.PRE, f, "", Rpre, Rpost.shape)
            with np.errstate(invalid="ignore", divide="ignore"):
                A.append(-np.log10(Rpost / on))
            noise.append(np.hypot(frame_noise(f, cell, "", fpost),
                                  frame_noise(pre, cell, a1.PRE, fpre)))
            info[a1.cell_name(cell)] = [fpost["rung"], len(fpost["frames"]),
                                        fpre["rung"], len(fpre["frames"])]
        A = np.stack(A)                             # (channels, rows, cols)
        zone, _, _ = ws.zones_of(f, "")
        z = zone["interior"] & np.isfinite(A).all(0)
        X.append(A[:, z].T)                         # (pixels, channels)
        smp += [s] * int(z.sum())
        rr, cc = np.nonzero(z)
        where.append((s, rr, cc))
        sig.append(noise)
        photos[s] = info
        a1.close_all()
    sig = np.array(sig)
    return dict(X=np.vstack(X), sigma=np.median(sig, 0), sigma_by_strip=sig,
                sample=np.array(smp), where=where, photos=photos)


def one_layer_per_led(chans, sigma):
    """
    For each LED, the one usable layer with the smallest camera noise (A),
    LED order kept.  Used to check that the four layers of one LED are not
    just repeating each other.
    """
    best = {}
    for c, s in zip(chans, sigma):
        if c[0] not in best or s < best[c[0]][1]:
            best[c[0]] = (c, s)
    return [best[led][0] for led in PRE_LEDS if led in best]


def _pca_run(d, idx, remove_even):
    """
    One principal-component run on the channels `idx` of `pca_data_3f`'s
    result, through zero (A = 0 means no dye).  Each direction's sign is
    set so that its largest entry is positive (the sign is arbitrary).

    Returns:
        dict: channels, spread, dirs_A (as in `noise_pca`), leftover
        (strip -> m -> [median, LEFT_PCTL percentile], noise units, 1 =
        noise only), and '_maps' (where, sample, leftover per pixel per m,
        scores) for the figures; scores array (pixels, SHOW_SCORES), each
        pixel's position along directions 1.., noise units.
    """
    X, sigma = d["X"][:, idx], d["sigma"][idx]
    p = noise_pca(X, sigma, through_zero=True, remove_even=remove_even)
    dirs, dA = p["dirs"].copy(), p["dirs_A"].copy()
    for i in range(len(dA)):                    # largest entry positive
        if dA[i][np.argmax(np.abs(dA[i]))] < 0:
            dirs[i], dA[i] = -dirs[i], -dA[i]
    rem = 1 if remove_even else 0
    ms = [m for m in KEEP_M if m < len(sigma) - rem]
    left = {m: leftover_after(p["Z"], dirs, m, rem) for m in ms}
    strips = list(dict.fromkeys(d["sample"]))
    lev = {s: {m: [float(np.median(left[m][d["sample"] == s])),
                   float(np.percentile(left[m][d["sample"] == s],
                                       LEFT_PCTL))] for m in ms}
           for s in strips}
    scores = p["Z"] @ dirs[:SHOW_SCORES].T
    return dict(spread=p["spread"].tolist(), dirs_A=dA.tolist(),
                leftover=lev,
                _maps=(d["where"], d["sample"], left, scores))


def color_count_3f(files=None):
    """
    Step 3f: how many color directions C1052-C1055 need.

    Three runs, all through zero, in noise units:
      'all, darkening kept'     every usable channel; hypothesis: 4
                                directions (yellow, brown, orange,
                                brightness);
      'all, darkening removed'  the same with the even darkening taken out
                                first (as in 3d); hypothesis: 3;
      'one layer per LED, darkening kept'  the least noisy layer of each
                                LED; if it finds the same count, the
                                layers are not just repeating each other.

    Returns:
        dict: channels (names), dropped (name -> reasons), sigma (A per
        channel, median over the strips), sigma_by_strip, photos, n_pixels
        per strip, and runs: run name -> `_pca_run` result plus its
        channel names.
    """
    files = files or FILES_3F
    keep, dropped = white_channels_3f(list(files.values()))
    d = pca_data_3f(files, keep)
    names = [a1.cell_name(c) for c in keep]
    one = one_layer_per_led(keep, d["sigma"])
    plan = {"all, darkening kept": (list(range(len(keep))), False),
            "all, darkening removed": (list(range(len(keep))), True),
            "one layer per LED, darkening kept":
                ([keep.index(c) for c in one], False)}
    runs = {}
    for name, (idx, rem) in plan.items():
        runs[name] = _pca_run(d, idx, rem)
        runs[name]["channels"] = [names[i] for i in idx]
    return dict(channels=names, dropped=dropped, sigma=d["sigma"].tolist(),
                sigma_by_strip={s: v.tolist() for s, v in
                                zip(files, d["sigma_by_strip"])},
                photos=d["photos"],
                n_pixels={s: int((d["sample"] == s).sum()) for s in files},
                runs=runs)


def summarize_3f(res):
    """Print the 3f tables."""
    print(f"Step 3f: {', '.join(f'{s} ({AMOUNT_3F[s]} ug/L)' for s in FILES_3F)}")
    print(f"  interior pixels per strip: {res['n_pixels']}")
    print(f"  {len(res['channels'])} channels kept; "
          f"{len(res['dropped'])} dropped:")
    for c, why in res["dropped"].items():
        print(f"    {c:16s} {why[0]}" + (f" (+{len(why)-1} more)"
                                         if len(why) > 1 else ""))
    print("  camera noise of one pixel's A (post and pre combined, median "
          "over strips), A:")
    for c, s in zip(res["channels"], res["sigma"]):
        print(f"    {c:16s} {s:.4f}")
    for name, r in res["runs"].items():
        print(f"\n  Run '{name}', {len(r['channels'])} channels.")
        print("    spread along directions 1-10, noise units squared "
              "(1 = noise only): " + ", ".join(f"{v:.1f}" for v in
                                              r["spread"][:10]))
        ms = list(next(iter(r["leftover"].values())))
        print(f"    leftover per pixel after m directions, noise units "
              f"(median / {LEFT_PCTL}th percentile):")
        print("      " + " " * 8 + "".join(f"   m={m:<9d}" for m in ms))
        for s, lv in r["leftover"].items():
            print(f"      {s:8s}" + "".join(
                f"{lv[m][0]:5.2f}/{lv[m][1]:<6.2f} " for m in ms))


def _json_ready_3f(res):
    """color_count_3f result without the per-pixel arrays."""
    out = dict(res)
    out["runs"] = {k: {kk: vv for kk, vv in v.items() if kk != "_maps"}
                   for k, v in res["runs"].items()}
    return out


def _pad_axes(ax, f):
    """Zoom a map axis onto file f's fitted post-image pad."""
    p = a1.pad_of(f, "")
    ax.set_xlim(p.aL - 2, p.aR + 2)
    ax.set_ylim(p.bot + 2, p.top - 2)
    ax.set_xticks([])
    ax.set_yticks([])


def _strip_maps(res_run, files, values):
    """Per strip, a (rows, cols) image of `values` (one per pixel), NaN off
    the interior."""
    where, smp, _, _ = res_run["_maps"]
    out = {}
    for s, rr, cc in where:
        img = np.full(a1.pad_of(files[s], "").mask(0).shape, np.nan)
        img[rr, cc] = values[smp == s]
        out[s] = img
    return out


def show_3f(res, files=None, savefig=True):
    """
    Step 3f figures, each with a .txt beside it (NOTE_3F):
      fig/color_3f_spread.png: spread along each direction, the three
          runs stacked, log scale, with the noise level 1.
      fig/color_3f_directions.png: the first SHOW_DIRS directions of the
          run 'all, darkening kept', as A per channel (largest = 1).
      fig/color_3f_scores.png: each strip's plain color picture, then
          each pixel's position along directions 1-SHOW_SCORES.
      fig/color_3f_leftover.png: the plain color picture, then the
          leftover after 2, 3, 4 and 5 directions (run 'all, darkening
          kept').
    """
    import matplotlib.pyplot as plt
    files = files or FILES_3F
    FIG.mkdir(exist_ok=True)
    strips = list(files)
    saved = []
    # 1. spread, three runs stacked with one x axis
    fig, axs = plt.subplots(len(res["runs"]), 1, figsize=(8, 8), sharex=True)
    for ax, (name, r) in zip(axs, res["runs"].items()):
        sp = r["spread"]
        ax.semilogy(np.arange(1, len(sp) + 1), sp, "o-", ms=4)
        ax.axhline(1, color="gray", ls=":", label="camera noise only (1)")
        ax.set_ylabel("spread,\nnoise units squared", fontsize=9)
        ax.set_title(f"{name}: {len(r['channels'])} channels", fontsize=9)
        ax.grid(alpha=0.3, which="both")
        ax.legend(fontsize=8)
    axs[-1].set_xlabel("direction number (largest spread first)")
    fig.suptitle("Step 3f: C1052-C1055 post vs own pre-image, A, interior "
                 "pixels: spread along each color direction", fontsize=10)
    fig.tight_layout()
    saved.append(("color_3f_spread", fig))
    # 2. directions of the main run
    r = res["runs"]["all, darkening kept"]
    lay_color = {"B": "tab:blue", "Gb": "tab:green", "Gr": "tab:olive",
                 "R": "tab:red"}
    ch = r["channels"]
    x = np.arange(len(ch))
    cols = [lay_color[c.split(".")[1]] for c in ch]
    fig, axs = plt.subplots(SHOW_DIRS, 1, figsize=(11, 10), sharex=True)
    for i, ax in enumerate(axs):
        ax.bar(x, r["dirs_A"][i], color=cols)
        ax.axhline(0, color="k", lw=0.6)
        for j in range(1, len(ch)):             # line between LEDs
            if ch[j].split(".")[0] != ch[j - 1].split(".")[0]:
                ax.axvline(j - 0.5, color="gray", lw=0.5)
        ax.set_ylabel(f"direction {i+1}\n(spread {r['spread'][i]:.0f})",
                      fontsize=9)
        ax.grid(alpha=0.3, axis="y")
    axs[-1].set_xticks(x)
    axs[-1].set_xticklabels(ch, rotation=70, fontsize=8)
    fig.suptitle("Step 3f, all channels, darkening kept: the first "
                 f"{SHOW_DIRS} color directions as A per channel (largest "
                 "entry = +1; bar color = camera layer: blue B, green Gb, "
                 "olive Gr, red R)", fontsize=9)
    fig.tight_layout()
    saved.append(("color_3f_directions", fig))
    # 3. scores and 4. leftover maps: plain picture on top, strips across
    pics = {s: true_color(files[s]) for s in strips}
    sc = r["_maps"][3]
    rows = [(f"direction {i+1}\nposition, noise units", sc[:, i])
            for i in range(SHOW_SCORES)]
    left = r["_maps"][2]
    lrows = [(f"leftover after {m}\ndirections, noise units", left[m])
             for m in (2, 3, 4, 5) if m in left]
    for name, rws, kind in (("color_3f_scores", rows, "score"),
                            ("color_3f_leftover", lrows, "left")):
        fig, axs = plt.subplots(len(rws) + 1, len(strips),
                                figsize=(2.6 * len(strips) + 1.5,
                                         2.2 * (len(rws) + 1)),
                                squeeze=False)
        for j, s in enumerate(strips):
            axs[0, j].imshow(pics[s])
            _pad_axes(axs[0, j], files[s])
            axs[0, j].set_title(f"{s} post, {AMOUNT_3F[s]:g} ug/L",
                                fontsize=9)
        axs[0, 0].set_ylabel("white LED\n(for the eye)", fontsize=9)
        for i, (lab, vals) in enumerate(rws, start=1):
            maps = _strip_maps(r, files, vals)
            if kind == "score":
                lim = float(np.nanpercentile(np.abs(vals), 99))
                lo, hi, cmap = (0, lim, "magma") if i == 1 else \
                    (-lim, lim, "RdBu_r")
            else:
                lo, hi, cmap = 0, 4, "magma"
            for j, s in enumerate(strips):
                im = axs[i, j].imshow(maps[s], cmap=cmap, vmin=lo, vmax=hi)
                _pad_axes(axs[i, j], files[s])
            axs[i, 0].set_ylabel(lab, fontsize=8)
            fig.colorbar(im, ax=list(axs[i]), fraction=0.03, pad=0.01)
        fig.suptitle("Step 3f, all channels, darkening kept: " +
                     ("each interior pixel's position along each direction"
                      if kind == "score" else "what is left after keeping "
                      "k directions (1 = camera noise only)"), fontsize=10)
        saved.append((name, fig))
    a1.close_all()
    if savefig:
        for name, f in saved:
            out = FIG / f"{name}.png"
            f.savefig(out, dpi=100)
            out.with_suffix(".txt").write_text(NOTE_3F)
            print(f"fig saved to {out}")


NOTE_3F = """\
color_3f_*.png -- step 3f, how many color directions the strips of
2026-11-16 need (made by color_study.py, color_count_3f and show_3f)

Strips: C1052 12.8 ug/L, C1053 20 ug/L, C1054 40 ug/L (file C1054-a.h5),
C1055 600 ug/L; holder H.  Pixels: every interior pad pixel (8 or more
layer pixels from every edge) of each post-image.

A (absorbance) = -log10(post-image / the strip's own pre-image lined up),
per channel (LED . camera layer).  Bigger A = darker, more dye; 0 = no
change.  Every channel is the average of 4 repeat photos in both images.
Channels: every LED but white (no pre-image) x every camera layer that is
usable in both images of all four strips.

Noise units: each channel's A is divided by its camera noise per pixel
(post and pre-image combined, from the spread between the 4 repeat
photos).  In these units pure camera noise spreads about 1 along every
direction.  Paper texture that does not cancel between pre and post
images, and small misalignment, add to that, so the last directions sit
somewhat above 1 (3d found 2.5-3.9).

Through zero: spread is measured from A = 0 (no dye), not from the average.
Darkening kept / removed: the even darkening is the same added A in every
channel (overall brightness).  'Kept' leaves it in, so it can show up as a
direction; 'removed' takes it out first, as step 3d did.

color_3f_spread.png: y axis, the spread along each direction (mean squared
distance of the pixels along it), noise units squared, log scale; x axis,
directions, largest first.  Top: all channels, darkening kept (Bill's
hypothesis predicts 4 directions well above the rest: yellow, brown,
orange and brightness).  Middle: darkening removed (predicts 3).  Bottom:
one layer per LED (the least noisy), darkening kept: the same count here
means the four layers of one LED are not just repeating each other.

color_3f_directions.png: each panel one direction, drawn as the change in
A per channel that moving along it makes, scaled so the largest entry is
+1.  The sign is arbitrary (a direction and its mirror are the same
direction).  Gray lines separate LEDs; LEDs run from violet (shortest
wavelength) to photo_red (longest).

color_3f_scores.png: top row, a plain picture from the white LED (post
only, for the eye, not used in any number).  Below, each pixel's position
along directions 1-4, noise units: where on the pad each direction is
found.  Direction 1 runs from 0 up (darker); the others are centered on 0,
red and blue being opposite signs.

color_3f_leftover.png: top row as above.  Below, per pixel, what is left
after keeping the first k directions: the length of the rest, noise
units, divided by sqrt(number of directions left) so that noise alone gives
about 1.  Bright = the kept directions do not describe that pixel.
"""

ABOUT_3F = {
    "what": "Step 3f: principal components of C1052-C1055 interior pixels, "
            "through zero, noise units; color_study.color_count_3f().",
    "A": "-log10(post / own pre-image lined up), bigger = darker",
    "channels": "LED.layer usable in pre and post of all four strips",
    "dropped": "channel -> reasons it was left out",
    "sigma": "camera noise of one pixel's A, post and pre combined, "
             "median over the strips, A",
    "photos": "strip -> channel -> [post rung, post photos averaged, pre "
              "rung, pre photos averaged]",
    "runs": "run name -> spread (noise units squared, 1 = noise only), "
            "dirs_A (A per channel, largest entry +1), leftover (strip -> "
            "m -> [median, 95th percentile], noise units, 1 = noise only)",
}


def _save(name, obj, about):
    """Write {"about": about, "rows": obj} to color_out/<name>.json."""
    OUT.mkdir(exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(
        {"about": about, "rows": obj}, indent=1))


def _load(name):
    """The rows of color_out/<name>.json."""
    return json.loads((OUT / f"{name}.json").read_text())["rows"]


ABOUT_PAIRING = {
    "what": "Step 3b: C1048's post-image divided by C1047's pre-image, "
            "against C1047's own pairing.  color_study.pairing_check().",
    "A": "absorbance, -log10(R_post / R_pre lined up onto the "
         "post-image); bigger means darker.",
    "A_interior": "median A over the interior (8 or more layer px from "
                  "every edge), A",
    "edge_minus_interior": "per edge (L left, R right, T top, B bottom): "
                           "median A over bands 0-1 of that edge (within 2 "
                           "layer px) minus A_interior, A",
    "width_post, width_pre": "fitted pad widths, layer pixels",
    "fit_cells_post, fit_cells_pre": "number of channels the pad was "
                                     "located on",
}


def run():
    """
    The study, step by step.  Turn each step on (1) or off (0).

        3b  pairing_check -> color_out/pairing.json, terminal
            show_pairing  -> fig/color_3b_pairing_<channel>.png (+ .txt)
        3b-ii fit_basis_offsets -> color_out/fit_offsets.json, terminal
        3c  yellow_brown  -> color_out/yellow_brown.json, terminal
            show_yellow_brown -> fig/color_3c_yellow_brown.png (+ .txt)
        3c-2 show_scatter -> fig/color_3c2_scatter.png (+ .txt)
        3d  color_count   -> color_out/color_count.json, terminal
            show_color_count -> fig/color_3d_spread.png,
                color_3d_directions_B.png, color_3d_leftover.png (+ .txt)
        3e  three_colors  -> color_out/three_colors.json, terminal
            show_three_colors -> fig/color_3e_maps.png,
                color_3e_dot_profile.png (+ .txt)
        3f  color_count_3f -> color_out/color_count_3f.json, terminal
            show_3f -> fig/color_3f_spread.png, color_3f_directions.png,
                color_3f_scores.png, color_3f_leftover.png (+ .txt)
    """
    if 0:
        _save("pairing", pairing_check(), ABOUT_PAIRING)
        summarize_pairing(_load("pairing"))
    if 0:
        show_pairing(("photo_red", "R"))
        show_pairing(("green", "B"))
    if 0:   # 3b-ii: photo_red-only fits against all-channel fits
        _save("fit_offsets", fit_basis_offsets(), ABOUT_OFFSETS)
    if 0:
        summarize_offsets(_load("fit_offsets"))
    if 0:   # 3c: reference lists, matrix, amounts per pad
        _save("yellow_brown", yellow_brown(), ABOUT_YB)
    if 0:
        res = _load("yellow_brown")
        summarize_yellow_brown(res)
        show_yellow_brown(res)
    if 0:   # 3c-2: every pixel on two-channel graphs
        show_scatter(_load("yellow_brown"))
    if 0:   # 3d: how many colors (principal components, noise units)
        res = color_count()
        _save("color_count", _json_ready(res), ABOUT_3D)
        summarize_color_count(res)
        show_color_count(res)
    if 0:   # 3e: three reference colors
        res = three_colors()
        _save("three_colors", res, ABOUT_3E)
        summarize_three_colors(res)
        show_three_colors(res)
    if 1:   # 3f: how many colors on C1052-C1055 (all LEDs and layers)
        res = color_count_3f()
        _save("color_count_3f", _json_ready_3f(res), ABOUT_3F)
        summarize_3f(res)
        show_3f(res)


if __name__ == "__main__":
    sys.exit(run())
