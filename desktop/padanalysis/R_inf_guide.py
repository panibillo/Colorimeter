"""R_inf_guide.py -- pictures of how R_inf is built, and one side question.

A tangent, kept out of `analysis_1.py`.  Run it, look at the figures, read
the captions printed alongside them.  Nothing here writes to a capture or to
the database; it only reads, the same way `analysis_1.py` does.

    from R_inf_guide import illustrate, compare_cells
    illustrate("C1020", "royal_blue", "B", save=True)
    compare_cells("C1020")


What "against" means
-------------------
`analysis_1.py` and `tex/R_inf_guide.tex` say things like "R against the gray
chips" and "R_inf against the bare paper".  "Against X" is shorthand for
"divided by the reference value taken from X".  Both R and R_inf are ratios:

    R(y, x)      = sig(y, x) / div            div is one number from the chips
    R_inf(y, x)  = R(y, x)  / R_white         R_white is one number from paper

`sig` is the dark-subtracted signal in raw camera counts (what
`probe.Capture.layer` returns).  `R = 1` means the pad pixel is exactly as
bright as the gray chips, after the chips and the pad have been put on the
same footing (they sit in different parts of an uneven light field).
`R_inf = 1` means the pad pixel is exactly as bright as the strip's own bare
paper.  So "against the gray chips" = "as a fraction of the gray-chip
brightness", and "against the bare paper" = "as a fraction of the bare-paper
brightness".


The one number, and the side question
-------------------------------------
The reference `div` is a single number for the whole pad:

    div = w1 * mean(sig over chip1) + w2 * mean(sig over chip2)

The weights `w1, w2` come from `whitecorr.anchor_weights`.  They are built
from the cell's fitted illumination surface `g(y, x)` -- a smooth polynomial,
one per cell, scaled so its average over the whole bound crop is 1, that
records how bright the lit field is at each pixel relative to its own
average (the shape of the lighting, with the overall level divided out).
`w1, w2` use only three summary numbers off that surface: its mean over
chip1, over chip2, and over the pad region.

The side question, raised while reading the guide: the surface `g` is known
at every pixel, so why collapse it to one number?  Why not form a per-pixel
divisor

    div_poly(y, x) = K * g(y, x)         K chosen so the chips still match

and compute `sig / div_poly` pixel by pixel?

This module answers that by building both and drawing the difference.  Two
facts do most of the answering, and the figures show them on real captures:

1.  For R_inf the scalar `div` cancels exactly.  The same number divides the
    pad signal and the paper reference, so

        R_inf(y, x) = (sig_pad / div) / (median(sig_paper) / div)
                    =  sig_pad(y, x) / median(sig_paper)

    The value of `div` never reaches R_inf.  It reaches only R (the
    chip-referenced ratio) and anything built from R before the paper step,
    such as `duotone.PadCube`.

2.  A per-pixel `div_poly` does NOT cancel, because it takes a different
    value where the pad sits than where the paper sits.  Switching to it
    changes R_inf by three things added together:
      - the surface's gradient across the pad   (measured here: ~0.7-3.6 %),
      - the surface's gradient across the paper  (~1-3 %),
      - the surface's estimate of the lighting difference between the pad's
        place in the frame and the paper's place  (~1-5 %, and it can have
        either sign depending on the cell).

    The third term dominates, and it is a claim about the flat-field model,
    not a resolution improvement.  See `compare_cells` for the per-cell
    spread and `illustrate`'s closing note for the recommendation.

Reference: tex/R_inf_guide.tex; desktop/padanalysis/HOWTO.md section 8;
desktop/padanalysis/whitecorr.py (the surface and the weights);
desktop/padanalysis/padphot.py (divisor, reflectance, reflectance_infinity).
"""
import sys
from pathlib import Path

import numpy as np

SP = Path(__file__).resolve().parent
sys.path.insert(0, str(SP))

import analysis_1 as A                                      # noqa: E402
from whitecorr import anchor_weights                        # noqa: E402

FIG = SP / "fig"

#: Cells used by `compare_cells` when none are named -- one per LED that a
#: normal (non-pre) sweep lights, chosen to span the absorption edge.
SURVEY_CELLS = [
    ("violet", "B"), ("royal_blue", "B"), ("blue", "B"),
    ("green", "Gb"), ("amber", "R"), ("red", "R"), ("white", "B"),
]


# ── assembling every quantity for one cell ─────────────────────────────

class Bench:
    """
    Every array and number in the R_inf chain for one cell, both ways.

    Built by `assemble`.  Attributes, all on the bound-crop layer grid
    (130 x 183 for bound roi 39) unless noted:

        samp_no, led, layer, sec, rung : what was read.
        sig        : dark-subtracted signal, raw counts (probe.Capture.layer).
        g          : the cell's fitted illumination surface, unit mean over
                     the whole crop.
        chip1, chip2, pad, paper : boolean region masks.  `pad` and `paper`
                     are eroded by `analysis_1.ERODE`.
        S1, S2     : mean of `sig` over chip1, chip2 (counts).
        w1, w2, t  : the published-form anchor weights and the pad's
                     fractional x-position between the chips.
        g1, g2, gs, g_paper : mean of `g` over chip1, chip2, pad, paper.
        div_scalar : the one-number divisor, w1*S1 + w2*S2 (counts).
        K          : div_scalar / gs -- counts per unit of surface.
        div_poly   : K * g -- the per-pixel divisor (counts), an array.
        R_scalar, R_poly       : sig / div, the two ways (arrays).
        Rw_scalar, Rw_poly     : median of R over the paper region (numbers).
        Rinf_scalar, Rinf_poly : R / R_white, the two ways (arrays).
    """

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    @property
    def cell(self):
        return A.cell_name((self.led, self.layer))

    @property
    def title(self):
        tag = f"/{self.sec}" if self.sec else ""
        return f"{self.samp_no}{tag}  {self.cell}  (rung {self.rung})"


def assemble(samp_no, led, layer, sec="", erode=A.ERODE) -> Bench:
    """
    Read one cell and compute the whole chain, scalar-divisor and
    polynomial-divisor side by side.

    Args:
        samp_no: Capture name without the .h5, e.g. 'C1020'.
        led: LED colour name, e.g. 'royal_blue'.
        layer: 'R', 'Gr', 'Gb' or 'B'.
        sec: '' for the measurement sweep, 'pre' for the pre-image.
        erode: Layer px backed off the pad and paper edges.

    Returns:
        A `Bench`.

    Raises:
        RuntimeError: the cell has no flat-field model, no usable exposure
            rung, or its bare paper clipped (no R_white).
    """
    model = A.models().get((led, layer))
    if model is None or not model.usable:
        raise RuntimeError(f"{samp_no} {led}.{layer}: no usable flat-field model")

    c = A.open_sample(samp_no, sec)
    rung = A._pick_rung(samp_no, (led, layer), sec, erode)
    if rung is None:
        raise RuntimeError(f"{samp_no} {led}.{layer}: no usable exposure rung")
    got = c.layer(led, rung, layer)
    if got is None:
        raise RuntimeError(f"{samp_no} {led}.{layer}: not stored in this sweep")
    sig = got[0].astype(np.float64)

    chip1 = A._region(samp_no, A.ROIGRAY1, sec)
    chip2 = A._region(samp_no, A.ROIGRAY2, sec)
    pad = A._region(samp_no, A.ROISAMPLE, sec, erode)
    paper = A._region(samp_no, A.ROIWHITE, sec, erode)

    g = model.gain()
    if g.shape != sig.shape:
        raise RuntimeError(f"surface {g.shape} does not match signal {sig.shape}")

    w1, w2, t, gs, g1, g2 = anchor_weights(model, pad, chip1, chip2)
    g_paper = float(g[paper].mean())
    S1 = float(sig[chip1].mean())
    S2 = float(sig[chip2].mean())

    div_scalar = w1 * S1 + w2 * S2
    K = div_scalar / gs
    div_poly = K * g

    R_scalar = sig / div_scalar
    R_poly = sig / div_poly
    Rw_scalar = float(np.median(R_scalar[paper]))
    Rw_poly = float(np.median(R_poly[paper]))
    Rinf_scalar = R_scalar / Rw_scalar
    Rinf_poly = R_poly / Rw_poly

    return Bench(
        samp_no=samp_no, led=led, layer=layer, sec=sec, rung=rung,
        sig=sig, g=g, chip1=chip1, chip2=chip2, pad=pad, paper=paper,
        S1=S1, S2=S2, w1=w1, w2=w2, t=t, g1=g1, g2=g2, gs=gs, g_paper=g_paper,
        div_scalar=div_scalar, K=K, div_poly=div_poly,
        R_scalar=R_scalar, R_poly=R_poly, Rw_scalar=Rw_scalar, Rw_poly=Rw_poly,
        Rinf_scalar=Rinf_scalar, Rinf_poly=Rinf_poly,
    )


# ── small drawing helpers ──────────────────────────────────────────────

def _bbox(*masks):
    """The slice pair that just contains every True pixel in the masks."""
    m = np.zeros_like(masks[0])
    for mm in masks:
        m |= mm
    ys, xs = np.nonzero(m)
    return (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))


def _outline(ax, mask, box, colour, label=None):
    """Draw a mask's boundary on an axis whose image was cropped to `box`."""
    y0, x0 = box[0].start, box[1].start
    sub = mask[box].astype(float)
    ax.contour(sub, levels=[0.5], colors=colour, linewidths=1.1)
    if label:
        ys, xs = np.nonzero(mask[box])
        ax.text(xs.mean(), ys.min() - 1.5, label, color=colour, fontsize=7,
                ha="center", va="bottom")


def _finish(fig, name, save, show, outdir):
    """Save and/or show one figure.  Returns the path written, or None."""
    import matplotlib.pyplot as plt
    out = None
    if save:
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        out = outdir / f"R_inf_guide_{name}.png"
        fig.savefig(out, dpi=120, bbox_inches="tight")
        out = outdir / f"R_inf_guide_{name}.pdf"
        fig.savefig(out, dpi=120, bbox_inches="tight")
        print(f"  wrote {out}")
    if show:
        plt.show()
    else:
        plt.close(fig)
    return out


# ── the figures ────────────────────────────────────────────────────────

def fig_scene(B: Bench):
    """The dark-subtracted signal and the four regions it is read over."""
    import matplotlib.pyplot as plt
    box = _bbox(B.chip1, B.chip2, B.pad, B.paper)
    fig, ax = plt.subplots(figsize=(9, 5.2), layout="constrained")
    hi = np.percentile(B.sig[B.chip1 | B.chip2 | B.pad | B.paper], 99)
    im = ax.imshow(B.sig[box], cmap="gray", vmin=0, vmax=hi)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for m, colour, lab in ((B.chip1, "tab:cyan", "gray chip 1"),
                           (B.chip2, "tab:cyan", "gray chip 2"),
                           (B.pad, "tab:orange", "pad (eroded)"),
                           (B.paper, "tab:green", "bare paper (eroded)")):
        _outline(ax, m, box, colour, lab)
    fig.colorbar(im, ax=ax, shrink=0.8, label="sig  (dark-subtracted counts)")
    ax.set_title(f"{B.title}\nthe scene: sig, and the regions it is read over")
    cap = (
        f"Figure 1.  The dark-subtracted signal `sig` for {B.cell} on "
        f"{B.samp_no}, in raw camera counts, cropped to the working area.  "
        f"Four regions are marked: the two gray chips (stage furniture, the "
        f"reference for `R`), the pad (the fitted rectangle, backed off "
        f"{A.ERODE:g} px from its edge), and the strip's own bare paper below "
        f"the pad (the reference for `R_inf`).  Chip means here are "
        f"S1 = {B.S1:.1f} and S2 = {B.S2:.1f} counts; the pad mean is "
        f"{B.sig[B.pad].mean():.1f}.  \"R against the gray chips\" means each "
        f"pad pixel's `sig` will be divided by a reference number built from "
        f"S1 and S2; \"R_inf against the bare paper\" means that result is "
        f"then divided by the same quantity measured on the paper."
    )
    return fig, cap


def fig_surface(B: Bench):
    """The fitted illumination surface g, and how little it varies."""
    import matplotlib.pyplot as plt
    box = _bbox(B.chip1, B.chip2, B.pad, B.paper)
    fig, ax = plt.subplots(1, 2, figsize=(12, 5), layout="constrained")

    sub = B.g[box]
    im = ax[0].imshow(sub, cmap="viridis")
    ax[0].set_aspect("equal")
    ax[0].set_xticks([])
    ax[0].set_yticks([])
    for m, colour, lab in ((B.chip1, "w", f"g1={B.g1:.4f}"),
                           (B.chip2, "w", f"g2={B.g2:.4f}"),
                           (B.pad, "tab:orange", f"gs={B.gs:.4f}"),
                           (B.paper, "tab:red", f"g_paper={B.g_paper:.4f}")):
        _outline(ax[0], m, box, colour, lab)
    fig.colorbar(im, ax=ax[0], shrink=0.8, label="g  (unit mean over whole crop)")
    ax[0].set_title("fitted illumination surface  g(y, x)")

    # a vertical cut through the pad centre, carried down through the paper
    xc = int(round(np.nonzero(B.pad)[1].mean()))
    ys = np.nonzero(B.pad | B.paper)[0]
    rows = np.arange(max(ys.min() - 4, 0), min(ys.max() + 5, B.g.shape[0]))
    ax[1].plot(rows, B.g[rows, xc], color="tab:blue")
    pad_rows = np.nonzero(B.pad[:, xc])[0]
    pap_rows = np.nonzero(B.paper[:, xc])[0]
    if pad_rows.size:
        ax[1].axvspan(pad_rows.min(), pad_rows.max(), color="tab:orange",
                      alpha=0.15, label="pad")
    if pap_rows.size:
        ax[1].axvspan(pap_rows.min(), pap_rows.max(), color="tab:green",
                      alpha=0.15, label="paper")
    ax[1].set_xlabel("row (y), down the crop")
    ax[1].set_ylabel("g along the pad's centre column")
    ax[1].legend(fontsize=8)
    ax[1].grid(alpha=0.3)
    ax[1].set_title(f"column x = {xc}")

    pad_grad = (B.g[B.pad].max() - B.g[B.pad].min()) / B.g[B.pad].mean() * 100
    fig.suptitle(f"{B.title}   surface spread across the pad: {pad_grad:.2f} %")
    cap = (
        f"Figure 2.  The fitted illumination surface `g(y, x)` for {B.cell}: a "
        f"smooth polynomial (degree 3 down, 1 across), one per cell, scaled so "
        f"its mean over the whole crop is 1.  It is the shape of the lighting "
        f"with the overall level removed.  The anchor weights use only its "
        f"means over the marked regions: g1 = {B.g1:.4f}, g2 = {B.g2:.4f} on "
        f"the chips, gs = {B.gs:.4f} on the pad, g_paper = {B.g_paper:.4f} on "
        f"the paper.  Right: `g` along the pad's centre column.  Across the "
        f"pad it varies by only {pad_grad:.2f} %; the pad-to-paper step is "
        f"{(B.gs / B.g_paper - 1) * 100:+.2f} % (gs / g_paper = "
        f"{B.gs / B.g_paper:.4f})."
    )
    return fig, cap


def fig_divisor(B: Bench):
    """The one-number divisor, and the per-pixel alternative."""
    import matplotlib.pyplot as plt
    box = _bbox(B.pad)
    fig, ax = plt.subplots(1, 3, figsize=(13.5, 4.6), layout="constrained")

    vmin = min(B.div_scalar, B.div_poly[B.pad].min())
    vmax = max(B.div_scalar, B.div_poly[B.pad].max())
    flat = np.where(B.pad, B.div_scalar, np.nan)[box]
    im0 = ax[0].imshow(flat, cmap="magma", vmin=vmin, vmax=vmax)
    ax[0].set_title(f"scalar divisor\ndiv = w1*S1 + w2*S2 = {B.div_scalar:.1f}")
    fig.colorbar(im0, ax=ax[0], shrink=0.8)

    poly = np.where(B.pad, B.div_poly, np.nan)[box]
    im1 = ax[1].imshow(poly, cmap="magma", vmin=vmin, vmax=vmax)
    ax[1].set_title(f"per-pixel divisor\ndiv_poly = K * g,  K = {B.K:.1f}")
    fig.colorbar(im1, ax=ax[1], shrink=0.8)

    ratio = np.where(B.pad, B.div_poly / B.div_scalar, np.nan)[box]
    lim = np.nanmax(np.abs(ratio - 1.0))
    im2 = ax[2].imshow(ratio, cmap="RdBu_r", vmin=1 - lim, vmax=1 + lim)
    ax[2].set_title("div_poly / div_scalar   ( = g / gs )")
    fig.colorbar(im2, ax=ax[2], shrink=0.8)
    for a in ax:
        a.set_aspect("equal")
        a.set_xticks([])
        a.set_yticks([])

    spread = (np.nanmax(ratio) - np.nanmin(ratio)) * 100
    fig.suptitle(f"{B.title}   the two divisors differ by {spread:.2f} % "
                 f"across the pad")
    cap = (
        f"Figure 3.  Left: the divisor as `analysis_1.py` uses it -- one "
        f"number for the whole pad, {B.div_scalar:.1f} counts, formed from the "
        f"chip means S1, S2 and the weights w1 = {B.w1:.4f}, w2 = {B.w2:.4f} "
        f"(the pad sits a fraction t = {B.t:.3f} of the way from chip 1 to "
        f"chip 2).  Middle: the per-pixel alternative, K * g(y, x), with "
        f"K = div_scalar / gs so the chips still match on average.  Right: "
        f"their ratio, which is exactly g(y, x) / gs -- the surface's "
        f"variation across the pad, {spread:.2f} % peak to peak here.  This "
        f"is the only thing a per-pixel divisor changes about `R` on the pad."
    )
    return fig, cap


def fig_reflectance(B: Bench):
    """R computed both ways, and the difference."""
    import matplotlib.pyplot as plt
    box = _bbox(B.pad)
    fig, ax = plt.subplots(1, 3, figsize=(13.5, 4.6), layout="constrained")

    rs = np.where(B.pad, B.R_scalar, np.nan)[box]
    rp = np.where(B.pad, B.R_poly, np.nan)[box]
    vmin, vmax = np.nanpercentile(np.stack([rs, rp]), [1, 99])
    for a, dat, ttl, mean in ((ax[0], rs, "R, scalar divisor", B.R_scalar[B.pad].mean()),
                              (ax[1], rp, "R, per-pixel divisor", B.R_poly[B.pad].mean())):
        im = a.imshow(dat, cmap="magma", vmin=vmin, vmax=vmax)
        a.set_title(f"{ttl}\npad mean = {mean:.4f}")
        fig.colorbar(im, ax=a, shrink=0.8)

    diff = np.where(B.pad, B.R_poly - B.R_scalar, np.nan)[box]
    lim = np.nanmax(np.abs(diff))
    im = ax[2].imshow(diff, cmap="RdBu_r", vmin=-lim, vmax=lim)
    ax[2].set_title("R_poly - R_scalar")
    fig.colorbar(im, ax=ax[2], shrink=0.8)
    for a in ax:
        a.set_aspect("equal")
        a.set_xticks([])
        a.set_yticks([])

    fig.suptitle(f"{B.title}   R = sig / divisor")
    cap = (
        f"Figure 4.  `R` for {B.cell}, the pad signal divided by the divisor, "
        f"both ways.  The pad-mean values are equal to four decimals "
        f"({B.R_scalar[B.pad].mean():.4f} vs {B.R_poly[B.pad].mean():.4f}) -- "
        f"they must be, because gs is by definition the mean of `g` over the "
        f"pad, so the two divisors agree on average there.  The per-pixel "
        f"version only removes the gentle across-pad gradient (right panel), "
        f"max {np.nanmax(np.abs(diff)):.4f} in `R` units.  `R = 1` would mean "
        f"\"as bright as the interpolated chips\"; bare strip paper runs near "
        f"2, a stained pad in a band it absorbs runs below 1."
    )
    return fig, cap


def fig_rinf(B: Bench):
    """R_inf both ways -- and the fact that the scalar divisor cancels."""
    import matplotlib.pyplot as plt
    box = _bbox(B.pad)
    fig, ax = plt.subplots(1, 3, figsize=(13.5, 4.8), layout="constrained")

    a_scalar = B.Rinf_scalar
    check = B.sig / np.median(B.sig[B.paper])          # div-free form
    cancel_err = np.abs(a_scalar - check)[B.pad].max()

    rs = np.where(B.pad, B.Rinf_scalar, np.nan)[box]
    rp = np.where(B.pad, B.Rinf_poly, np.nan)[box]
    vmin, vmax = np.nanpercentile(np.stack([rs, rp]), [1, 99])
    for a, dat, ttl, mean in (
            (ax[0], rs, "R_inf, scalar divisor", B.Rinf_scalar[B.pad].mean()),
            (ax[1], rp, "R_inf, per-pixel divisor", B.Rinf_poly[B.pad].mean())):
        im = a.imshow(dat, cmap="magma", vmin=vmin, vmax=vmax)
        a.set_title(f"{ttl}\npad mean = {mean:.4f}")
        fig.colorbar(im, ax=a, shrink=0.8)

    diff = np.where(B.pad, B.Rinf_poly - B.Rinf_scalar, np.nan)[box]
    lim = np.nanmax(np.abs(diff))
    im = ax[2].imshow(diff, cmap="RdBu_r", vmin=-lim, vmax=lim)
    ax[2].set_title("R_inf_poly - R_inf_scalar")
    fig.colorbar(im, ax=ax[2], shrink=0.8)
    for a in ax:
        a.set_aspect("equal")
        a.set_xticks([])
        a.set_yticks([])

    shift = (B.Rinf_poly[B.pad].mean() / B.Rinf_scalar[B.pad].mean() - 1) * 100
    fig.suptitle(f"{B.title}   per-pixel divisor shifts pad-mean R_inf by "
                 f"{shift:+.2f} %")
    cap = (
        f"Figure 5.  `R_inf` for {B.cell}, both ways.  With the scalar "
        f"divisor it cancels: dividing `sig / div` by `median(sig_paper) / "
        f"div` leaves `sig_pad / median(sig_paper)`, with no `div` in it "
        f"(checked here: the scalar map matches that div-free form to "
        f"{cancel_err:.2e}).  So the scalar divisor's value does not affect "
        f"R_inf, A or K/S at all.  The per-pixel divisor does not cancel -- it "
        f"divides the pad and the paper each by their own local surface value "
        f"-- and it moves the pad-mean R_inf by {shift:+.2f} % here.  Most of "
        f"that is the pad-to-paper step in `g` "
        f"({(B.gs / B.g_paper - 1) * 100:+.2f} %), i.e. a modelled estimate "
        f"of the lighting difference between the two regions, not a "
        f"sharpening of the map."
    )
    return fig, cap


def fig_profile(B: Bench):
    """One vertical cut through the whole chain, pad to paper."""
    import matplotlib.pyplot as plt
    xc = int(round(np.nonzero(B.pad)[1].mean()))
    ys = np.nonzero(B.pad | B.paper)[0]
    rows = np.arange(max(ys.min() - 5, 0), min(ys.max() + 5, B.sig.shape[0]))
    pad_rows = np.nonzero(B.pad[:, xc])[0]
    pap_rows = np.nonzero(B.paper[:, xc])[0]

    fig, ax = plt.subplots(4, 1, figsize=(9, 10), sharex=True,
                           layout="constrained")

    def bands(a):
        if pad_rows.size:
            a.axvspan(pad_rows.min(), pad_rows.max(), color="tab:orange",
                      alpha=0.13)
        if pap_rows.size:
            a.axvspan(pap_rows.min(), pap_rows.max(), color="tab:green",
                      alpha=0.13)
        a.grid(alpha=0.3)

    ax[0].plot(rows, B.sig[rows, xc], color="0.2")
    ax[0].set_ylabel("sig (counts)")
    bands(ax[0])
    ax[0].set_title(f"{B.title}   vertical cut at column x = {xc} "
                    f"(orange = pad, green = paper)")

    ax[1].plot(rows, B.div_poly[rows, xc], color="tab:purple",
               label="div_poly = K*g")
    ax[1].axhline(B.div_scalar, color="tab:gray", ls="--",
                  label=f"div_scalar = {B.div_scalar:.0f}")
    ax[1].set_ylabel("divisor (counts)")
    ax[1].legend(fontsize=8)
    bands(ax[1])

    ax[2].plot(rows, B.R_scalar[rows, xc], color="tab:gray", ls="--",
               label="R, scalar")
    ax[2].plot(rows, B.R_poly[rows, xc], color="tab:purple", label="R, per-pixel")
    ax[2].axhline(1.0, color="k", lw=0.6)
    ax[2].set_ylabel("R")
    ax[2].legend(fontsize=8)
    bands(ax[2])

    ax[3].plot(rows, B.Rinf_scalar[rows, xc], color="tab:gray", ls="--",
               label="R_inf, scalar")
    ax[3].plot(rows, B.Rinf_poly[rows, xc], color="tab:purple",
               label="R_inf, per-pixel")
    ax[3].axhline(1.0, color="k", lw=0.6)
    ax[3].set_ylabel("R_inf")
    ax[3].set_xlabel("row (y), down the crop")
    ax[3].legend(fontsize=8)
    bands(ax[3])

    cap = (
        f"Figure 6.  A single column of pixels through the pad centre "
        f"(x = {xc}) and on down into the paper region, {B.cell} on "
        f"{B.samp_no}.  Top to bottom: the raw signal; the two divisors (the "
        f"scalar is a flat line, the polynomial a gentle curve of the same "
        f"K * g); `R` each way; `R_inf` each way.  The pad band and the paper "
        f"band are shaded.  On `R_inf` the two curves are near-parallel "
        f"inside the pad -- the per-pixel divisor is not reshaping the pad "
        f"profile -- but they are offset, because the polynomial applied a "
        f"different correction over the pad than over the paper."
    )
    return fig, cap


def fig_cell_survey(B_list, samp_no, sec=""):
    """Across a set of cells: how far the two divisors take R_inf apart."""
    import matplotlib.pyplot as plt
    names = [b.cell for b in B_list]
    pad_grad = [(b.g[b.pad].max() - b.g[b.pad].min()) / b.g[b.pad].mean() * 100
                for b in B_list]
    pad_paper = [(b.gs / b.g_paper - 1) * 100 for b in B_list]
    shift = [(b.Rinf_poly[b.pad].mean() / b.Rinf_scalar[b.pad].mean() - 1) * 100
             for b in B_list]

    x = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(10, 5), layout="constrained")
    ax.bar(x - 0.25, pad_grad, 0.25, label="surface spread across pad (%)",
           color="tab:blue")
    ax.bar(x, pad_paper, 0.25, label="pad-to-paper step in surface (%)",
           color="tab:orange")
    ax.bar(x + 0.25, shift, 0.25, label="resulting shift in pad-mean R_inf (%)",
           color="tab:red")
    ax.axhline(0, color="k", lw=0.6)
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=30, ha="right")
    ax.set_ylabel("percent")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    tag = f"/{sec}" if sec else ""
    ax.set_title(f"{samp_no}{tag}   scalar vs per-pixel divisor, per cell")
    cap = (
        f"Figure 7.  The same comparison across {len(names)} cells of "
        f"{samp_no}.  Blue: how much the fitted surface varies across the pad "
        f"(what a per-pixel divisor removes from the map).  Orange: the "
        f"surface's pad-to-paper step (the modelled lighting difference "
        f"between the two regions).  Red: the net shift a per-pixel divisor "
        f"puts on the pad-mean R_inf.  The shift is cell-dependent and "
        f"changes sign -- short-wavelength cells like violet.B move one way, "
        f"the rest the other -- so adopting it would change the shape of the "
        f"pad spectrum, not just its level.  violet.B is also the "
        f"lowest-signal, most-vignetted cell, where the low-order surface fit "
        f"is least certain."
    )
    return fig, cap


# ── the drivers ────────────────────────────────────────────────────────

def illustrate(samp_no="C1020", led="royal_blue", layer="B", sec="",
               save=False, show=True, outdir=FIG):
    """
    Draw the whole set for one cell and print each figure's caption.

    Args:
        samp_no: Capture name without the .h5.
        led: LED colour name.
        layer: 'R', 'Gr', 'Gb' or 'B'.
        sec: '' for the measurement sweep, 'pre' for the pre-image.
        save: Write each figure to `outdir/R_inf_guide_<samp>_<cell>_<n>.png`.
        show: Display the figures.
        outdir: Where saved figures go (default `desktop/padanalysis/fig`).

    Returns:
        The `Bench` that was drawn.
    """
    B = assemble(samp_no, led, layer, sec)
    stem = f"{samp_no}{('_' + sec) if sec else ''}_{B.cell}"

    figs = [fig_scene(B), fig_surface(B), fig_divisor(B), fig_reflectance(B),
            fig_rinf(B), fig_profile(B)]
    for i, (fig, cap) in enumerate(figs, 1):
        _finish(fig, f"{stem}_{i}", save, show, outdir)
        print("\n" + _wrap(cap) + "\n")

    print("=" * 72)
    print(_wrap(
        "Bottom line.  R_inf, A and K/S do not see the scalar divisor -- it "
        "cancels.  A per-pixel polynomial divisor would change R_inf, but "
        "mostly by importing the fitted surface's estimate of the lighting "
        "difference between the pad and the paper "
        f"({(B.gs / B.g_paper - 1) * 100:+.2f} % for this cell), which is a "
        "claim about the flat-field model rather than a finer measurement.  "
        "The across-pad gradient it also removes is "
        f"{(B.g[B.pad].max() - B.g[B.pad].min()) / B.g[B.pad].mean() * 100:.2f}"
        " %.  Keep the scalar divisor for R_inf.  If the pad-to-paper offset "
        "is worth correcting, test it on its own -- photograph one blank "
        "strip with the pad slid down to the paper's position and measure the "
        "ratio directly -- rather than trusting the low-order surface to "
        "supply it, especially for the short-wavelength cells where it and "
        "the survey disagree most."))
    print("=" * 72)
    return B

def compare_cells(samp_no="C1020", cells=None, sec="", save=False, show=True,
                  outdir=FIG):
    """
    Tabulate and plot the scalar-vs-per-pixel divergence across cells.

    Args:
        samp_no: Capture name without the .h5.
        cells: List of (led, layer) pairs, or None for `SURVEY_CELLS`.
        sec: '' measurement, 'pre' pre-image.
        save: Write the bar chart.
        show: Display it.
        outdir: Where a saved chart goes.

    Returns:
        The list of `Bench` objects that could be assembled.
    """
    cells = cells or SURVEY_CELLS
    out = []
    print(f"=== {samp_no}{('/' + sec) if sec else ''}   scalar vs per-pixel "
          f"divisor\n")
    print(f"   {'cell':<16}{'pad grad %':>11}{'pad/paper %':>12}"
          f"{'Rinf scalar':>13}{'Rinf poly':>11}{'shift %':>9}")
    for led, layer in cells:
        try:
            B = assemble(samp_no, led, layer, sec)
        except RuntimeError as e:
            print(f"   {A.cell_name((led, layer)):<16} -- {e}")
            continue
        out.append(B)
        pg = (B.g[B.pad].max() - B.g[B.pad].min()) / B.g[B.pad].mean() * 100
        pp = (B.gs / B.g_paper - 1) * 100
        a, b = B.Rinf_scalar[B.pad].mean(), B.Rinf_poly[B.pad].mean()
        print(f"   {B.cell:<16}{pg:>11.2f}{pp:>12.2f}{a:>13.4f}{b:>11.4f}"
              f"{(b / a - 1) * 100:>9.2f}")

    if out:
        fig, cap = fig_cell_survey(out, samp_no, sec)
        _finish(fig, f"{samp_no}{('_' + sec) if sec else ''}_survey",
                save, show, outdir)
        print("\n" + _wrap(cap))
    return out


def _wrap(text, width=78):
    import textwrap
    return "\n".join(textwrap.wrap(text, width=width,
            initial_indent="    ", subsequent_indent="    "))


# ── procedural: edit for each run ──────────────────────────────────────

if __name__ == "__main__":
    # Edit this block for each run.
    SAMP = "C1020"
    LED, LAYER = "royal_blue", "B"
    SEC = ""
    SAVE = True

    if 1:
        illustrate(SAMP, LED, LAYER, SEC, save=SAVE, show=False)

    if 1:
        compare_cells(SAMP, sec=SEC, save=SAVE, show=False)

    A.close_all()
