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
    3c-  the reference colors, the matrix, the check with principal
        components, C1047 -> C1048.  Not started; proposed to Bill first.

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
    """
    if 0:
        _save("pairing", pairing_check(), ABOUT_PAIRING)
        summarize_pairing(_load("pairing"))
    if 0:
        show_pairing(("photo_red", "R"))
        show_pairing(("green", "B"))
    if 1:   # 3b-ii: photo_red-only fits against all-channel fits
        _save("fit_offsets", fit_basis_offsets(), ABOUT_OFFSETS)
        summarize_offsets(_load("fit_offsets"))


if __name__ == "__main__":
    sys.exit(run())
