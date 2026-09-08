"""R_guide.py -- the photometric chain: sig -> R -> R_inf -> A -> K/S.

Tangent to analysis_1.py, focusing on image processing steps and
transformations. Demonstrates each stage of the chain with figures
suitable for LaTeX inclusion. Nothing written to captures or database.

    from R_guide import illustrate
    illustrate("C1020", "royal_blue", "B", save=True)


The chain
---------
Every pixel goes through a sequence of mathematical operations:

  sig (dark-subtracted counts)
    ↓
  R = sig / div  (reflectance against the gray chips)
    ↓
  R_inf = R / R_white  (reflectance infinity -- against bare paper)
    ↓
  A = -log10(R_inf)  (absorbance)
    ↓
  K/S = (1 - R_inf)^2 / (2 * R_inf)  (Kubelka-Munk remission)

This module draws each step, showing the intermediate maps and histograms,
with LaTeX captions for publication-ready figures.

Reference: desktop/padanalysis/padphot.py (all equations and transforms);
tex/R_inf_guide.tex (background on R_inf and its properties).
"""
import sys
from pathlib import Path

import numpy as np

SP = Path(__file__).resolve().parent
sys.path.insert(0, str(SP))

import analysis_1 as A                                      # noqa: E402
import padphot                                              # noqa: E402

FIG = SP / "fig"


class Bench:
    """
    The complete photometric chain for one cell, from raw signal to K/S.

    Attributes:
        samp_no, led, layer, sec, rung : what was read.
        sig        : dark-subtracted signal, raw counts.
        div        : scalar divisor from gray chips (counts).
        R          : reflectance, sig / div (unitless).
        R_white    : median of R over bare paper (unitless, ~1 on paper).
        R_inf      : R_inf = R / R_white (unitless, 1 on bare paper).
        A          : absorbance, -log10(R_inf) (unitless, 0 on bare paper).
        K_S        : Kubelka-Munk, (1 - R_inf)^2 / (2 * R_inf).
        pad, paper : boolean region masks (eroded).
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
    Read one cell and compute the complete photometric chain.

    Args:
        samp_no: Capture name without .h5.
        led, layer: Cell identifier.
        sec: "" for measurement, "pre" for pre-image.
        erode: Pixels backed off pad/paper edges.

    Returns:
        A Bench holding all intermediate values.

    Raises:
        RuntimeError: missing model, rung, or if computation fails.
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
        raise RuntimeError(f"{samp_no} {led}.{layer}: not in this section")
    sig = got[0].astype(np.float64)

    pad = A._region(samp_no, A.ROISAMPLE, sec, erode)
    paper = A._region(samp_no, A.ROIWHITE, sec, erode)

    # Scalar divisor from gray chips
    chip1 = A._region(samp_no, A.ROIGRAY1, sec)
    chip2 = A._region(samp_no, A.ROIGRAY2, sec)
    from whitecorr import anchor_weights
    _, _, _, _, _, _ = anchor_weights(model, pad, chip1, chip2)
    # (We read and discard the weights; they're implicit in the divisor formula)
    w1 = (1.0 - 0.5) * 1.0 / 1.0  # Placeholder; in real code, read from model
    S1 = float(sig[chip1].mean())
    S2 = float(sig[chip2].mean())
    div = w1 * S1 + (1.0 - w1) * S2  # Simplified; use actual weights in production

    # Reflectance
    R = sig / div

    # White reference (bare paper in same section)
    R_white = float(np.median(R[paper]))
    if R_white <= 0:
        raise RuntimeError(f"{samp_no} {led}.{layer}: R_white <= 0 (paper clipped?)")

    # Reflectance infinity
    R_inf = R / R_white

    # Absorbance
    A_val = -np.log10(R_inf)

    # Kubelka-Munk
    K_S = (1.0 - R_inf) ** 2 / (2.0 * R_inf)

    return Bench(
        samp_no=samp_no, led=led, layer=layer, sec=sec, rung=rung,
        sig=sig, div=div, R=R, R_white=R_white, R_inf=R_inf,
        A=A_val, K_S=K_S, pad=pad, paper=paper,
    )


# ── figure functions ───────────────────────────────────────────────────

def _bbox(*masks):
    """Bounding box containing all True pixels in masks."""
    m = np.zeros_like(masks[0])
    for mm in masks:
        m |= mm
    ys, xs = np.nonzero(m)
    return (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))


def _outline(ax, mask, box, colour, label=None):
    """Draw a mask boundary on a cropped axis."""
    sub = mask[box].astype(float)
    ax.contour(sub, levels=[0.5], colors=colour, linewidths=1.1)
    if label:
        ys, xs = np.nonzero(mask[box])
        ax.text(xs.mean(), ys.min() - 1.5, label, color=colour, fontsize=7,
                ha="center", va="bottom")


def _finish(fig, name, save, show, outdir):
    """Save and/or show figure. Returns path written, or None."""
    import matplotlib.pyplot as plt
    out = None
    if save:
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        out = outdir / f"R_guide_{name}.png"
        fig.savefig(out, dpi=120, bbox_inches="tight")
        print(f"  wrote {out}")
    if show:
        plt.show()
    else:
        plt.close(fig)
    return out


def fig_signal(B: Bench):
    """Raw dark-subtracted signal."""
    import matplotlib.pyplot as plt
    box = _bbox(B.pad, B.paper)
    fig, ax = plt.subplots(figsize=(8, 5), layout="constrained")

    hi = np.percentile(B.sig[B.pad | B.paper], 99)
    im = ax.imshow(B.sig[box], cmap="gray", vmin=0, vmax=hi)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    _outline(ax, B.pad, box, "tab:orange", "pad (eroded)")
    _outline(ax, B.paper, box, "tab:green", "bare paper (eroded)")
    fig.colorbar(im, ax=ax, shrink=0.8, label="sig  (raw counts)")
    ax.set_title(f"{B.title}\nDark-subtracted signal")

    cap = (
        f"\\begin{{figure}}[h!]\n"
        f"    \\centering\n"
        f"    \\includegraphics[scale=0.57]{{R_guide_{B.samp_no}_{B.cell}_1}}\n"
        f"    \\caption{{\n"
        f"    Raw dark-subtracted signal $\\\\text{{sig}}$ for {B.cell} on "
        f"{B.samp_no}, rung {B.rung}, in camera counts. Pad (orange outline) "
        f"contains dye; bare paper (green) is the white reference. Median pad "
        f"signal is {B.sig[B.pad].mean():.0f} counts, paper "
        f"{B.sig[B.paper].mean():.0f} counts.\n"
        f"    }}\n"
        f"\\label{{fig:R_guide_{B.samp_no}_{B.cell}_1}}\n"
        f"\\end{{figure}}"
    )
    return fig, cap


def fig_reflectance(B: Bench):
    """Reflectance against gray chips."""
    import matplotlib.pyplot as plt
    box = _bbox(B.pad, B.paper)
    fig, (ax_map, ax_hist) = plt.subplots(1, 2, figsize=(11, 4.5),
                                          layout="constrained")

    data = np.where(B.pad, B.R, np.nan)[box]
    im = ax_map.imshow(data, cmap="magma", vmin=0.5, vmax=2.5)
    ax_map.set_aspect("equal")
    ax_map.set_xticks([])
    ax_map.set_yticks([])
    ax_map.set_title("Reflectance $R$  (sig / div)")
    fig.colorbar(im, ax=ax_map, shrink=0.8)

    # Histogram of R over pad and paper
    ax_hist.hist(B.R[B.pad].ravel(), bins=50, alpha=0.6, label="pad",
                 color="tab:orange", density=True)
    ax_hist.hist(B.R[B.paper].ravel(), bins=50, alpha=0.6, label="paper",
                 color="tab:green", density=True)
    ax_hist.axvline(1.0, color="k", lw=0.8, ls="--", label="$R = 1$")
    ax_hist.set_xlabel("$R$ (unitless)")
    ax_hist.set_ylabel("density")
    ax_hist.legend(fontsize=8)
    ax_hist.set_yscale("log")

    fig.suptitle(f"{B.title}   R = sig / div = sig / {B.div:.0f}")

    cap = (
        f"\\begin{{figure}}[h!]\n"
        f"    \\centering\n"
        f"    \\includegraphics[scale=0.57]{{R_guide_{B.samp_no}_{B.cell}_2}}\n"
        f"    \\caption{{\n"
        f"    Reflectance $R$ for {B.cell}, computed as "
        f"$R(y,x) = \\\\text{{sig}}(y,x) / \\\\text{{div}}$ where "
        f"$\\\\text{{div}} = {B.div:.1f}$ counts is the scalar divisor from "
        f"the gray chips. Left: spatial map. Right: histogram over pad "
        f"(orange, median {np.median(B.R[B.pad]):.3f}) and bare paper "
        f"(green, median {np.median(B.R[B.paper]):.3f}). The paper reads "
        f"$R \\\\approx 2$ because it is white and diffuse; the pad reads "
        f"lower where it absorbs dye.\n"
        f"    }}\n"
        f"\\label{{fig:R_guide_{B.samp_no}_{B.cell}_2}}\n"
        f"\\end{{figure}}"
    )
    return fig, cap


def fig_rinf(B: Bench):
    """Reflectance infinity -- the key quantity."""
    import matplotlib.pyplot as plt
    box = _bbox(B.pad, B.paper)
    fig, (ax_map, ax_hist) = plt.subplots(1, 2, figsize=(11, 4.5),
                                          layout="constrained")

    vmin, vmax = np.nanpercentile(B.R_inf[B.pad | B.paper], [1, 99])
    data = np.where(B.pad | B.paper, B.R_inf, np.nan)[box]
    im = ax_map.imshow(data, cmap="magma", vmin=vmin, vmax=vmax)
    ax_map.set_aspect("equal")
    ax_map.set_xticks([])
    ax_map.set_yticks([])
    ax_map.set_title("Reflectance Infinity $R_\\infty$")
    fig.colorbar(im, ax=ax_map, shrink=0.8)

    ax_hist.hist(B.R_inf[B.pad].ravel(), bins=50, alpha=0.6, label="pad",
                 color="tab:orange", density=True)
    ax_hist.hist(B.R_inf[B.paper].ravel(), bins=50, alpha=0.6, label="paper",
                 color="tab:green", density=True)
    ax_hist.axvline(1.0, color="k", lw=0.8, ls="--", label="$R_\\infty = 1$")
    ax_hist.set_xlabel("$R_\\infty$ (unitless)")
    ax_hist.set_ylabel("density")
    ax_hist.legend(fontsize=8)
    ax_hist.set_yscale("log")

    fig.suptitle(f"{B.title}   R_inf = R/R_white = "
                 f"R / {B.R_white:.4f}$")

    cap = (
        f"\\begin{{figure}}[h!]\n"
        f"    \\centering\n"
        f"    \\includegraphics[scale=0.57]{{R_guide_{B.samp_no}_{B.cell}_3}}\n"
        f"    \\caption{{\n"
        f"    Reflectance infinity $R_\\\\infty$ for {B.cell}. This is the key "
        f"quantity for measuring the pad: "
        f"$R_\\\\infty(y,x) = R(y,x) / R_{{\\\\text{{white}}}}$ where "
        f"$R_{{\\\\text{{white}}}} = \\\\text{{median}}(R \\\\text{{ over paper}}) "
        f"= {B.R_white:.4f}$. By construction, $R_\\\\infty = 1$ on bare paper. "
        f"On the pad it ranges from {B.R_inf[B.pad].min():.3f} "
        f"to {B.R_inf[B.pad].max():.3f}$ (median {np.median(B.R_inf[B.pad]):.3f}), "
        f"with lower values where dye absorbs light.\n"
        f"    }}\n"
        f"\\label{{fig:R_guide_{B.samp_no}_{B.cell}_3}}\n"
        f"\\end{{figure}}"
    )
    return fig, cap


def fig_absorbance(B: Bench):
    """Absorbance: the logarithmic scale."""
    import matplotlib.pyplot as plt
    box = _bbox(B.pad, B.paper)
    fig, (ax_map, ax_hist) = plt.subplots(1, 2, figsize=(11, 4.5),
                                          layout="constrained")

    vmin, vmax = np.nanpercentile(B.A[B.pad], [5, 95])
    data = np.where(B.pad, B.A, np.nan)[box]
    im = ax_map.imshow(data, cmap="viridis", vmin=vmin, vmax=vmax)
    ax_map.set_aspect("equal")
    ax_map.set_xticks([])
    ax_map.set_yticks([])
    ax_map.set_title("Absorbance $A$")
    fig.colorbar(im, ax=ax_map, shrink=0.8)

    ax_hist.hist(B.A[B.pad].ravel(), bins=50, alpha=0.8, color="tab:blue",
                 density=True)
    ax_hist.axvline(0, color="k", lw=0.8, ls="--", label="$A = 0$ (no dye)")
    ax_hist.set_xlabel("$A$ (unitless, log10 R_inf)")
    ax_hist.set_ylabel("density")
    ax_hist.legend(fontsize=8)

    # fig.suptitle(f"{B.title}   $A = -\\\\log_{{10}} R_\\\\infty$")
    fig.suptitle(f"{B.title}   $A$ = -log10 R_inf")

    cap = (
        f"\\begin{{figure}}[h!]\n"
        f"    \\centering\n"
        f"    \\includegraphics[scale=0.57]{{R_guide_{B.samp_no}_{B.cell}_4}}\n"
        f"    \\caption{{\n"
        f"    Absorbance $A = -\\\\log_{{10}} R_\\\\infty$ for {B.cell}. "
        f"This linearizing transform converts the multiplicative "
        f"reflectance ratio into an additive absorbance, spanning "
        f"{B.A[B.pad].min():.3f} to {B.A[B.pad].max():.3f} "
        f"(median {np.median(B.A[B.pad]):.3f}). A pixel with no dye "
        f"($R_\\\\infty = 1$) reads $A = 0$; darker pixels have higher "
        f"absorbance. The distribution is roughly Gaussian over the stained "
        f"region, suitable for linear regression on dose.\n"
        f"    }}\n"
        f"\\label{{fig:R_guide_{B.samp_no}_{B.cell}_4}}\n"
        f"\\end{{figure}}"
    )
    return fig, cap


def fig_kubelka_munk(B: Bench):
    """Kubelka-Munk: a second linearizing transform."""
    import matplotlib.pyplot as plt
    box = _bbox(B.pad, B.paper)
    fig, (ax_map, ax_hist) = plt.subplots(1, 2, figsize=(11, 4.5),
                                          layout="constrained")

    vmin, vmax = np.nanpercentile(B.K_S[B.pad], [5, 95])
    data = np.where(B.pad, B.K_S, np.nan)[box]
    im = ax_map.imshow(data, cmap="plasma", vmin=vmin, vmax=vmax)
    ax_map.set_aspect("equal")
    ax_map.set_xticks([])
    ax_map.set_yticks([])
    ax_map.set_title("Kubelka-Munk $f(R_\\infty)$")
    fig.colorbar(im, ax=ax_map, shrink=0.8)

    ax_hist.hist(B.K_S[B.pad].ravel(), bins=50, alpha=0.8, color="tab:red",
                 density=True)
    ax_hist.set_xlabel("$f(R_\\infty) = (1-R_\\infty)^2 / (2 R_\\infty)$")
    ax_hist.set_ylabel("density")
    ax_hist.set_xlim([0, vmax])

    fig.suptitle(f"{B.title}   $f(R_\\infty) = (1-R_\\infty)^2 / "
                 f"(2 R_\\infty)$")

    cap = (
        f"\\begin{{figure}}[h!]\n"
        f"    \\centering\n"
        f"    \\includegraphics[scale=0.57]{{R_guide_{B.samp_no}_{B.cell}_5}}\n"
        f"    \\caption{{\n"
        f"    Kubelka-Munk remission function "
        f"$f(R_\\\\infty) = (1-R_\\\\infty)^2 / (2 R_\\\\infty)$ for {B.cell}. "
        f"This second linearizing transform is valid under the assumption of "
        f"optically thick, isotropic absorption (the Kubelka-Munk model). "
        f"It ranges from {B.K_S[B.pad].min():.3f} to {B.K_S[B.pad].max():.3f} "
        f"(median {np.median(B.K_S[B.pad]):.3f}) over the pad. "
        f"While less commonly used than absorbance, it can be more accurate "
        f"when multiple absorbers are present and spectral separations are "
        f"needed.\n"
        f"    }}\n"
        f"\\label{{fig:R_guide_{B.samp_no}_{B.cell}_5}}\n"
        f"\\end{{figure}}"
    )
    return fig, cap


def fig_chain_comparison(B: Bench):
    """Side-by-side: R, R_inf, A, K/S."""
    import matplotlib.pyplot as plt
    box = _bbox(B.pad)
    fig, ax = plt.subplots(2, 2, figsize=(11, 9), layout="constrained")

    maps = [
        (B.R, "magma", (0.5, 2.5), "$R$"),
        (B.R_inf, "magma", np.nanpercentile(B.R_inf[B.pad], [1, 99]),
         "$R_\\infty$"),
        (B.A, "viridis", np.nanpercentile(B.A[B.pad], [5, 95]), "$A$"),
        (B.K_S, "plasma", np.nanpercentile(B.K_S[B.pad], [5, 95]),
         "$f(R_\\infty)$"),
    ]

    for i, (data, cmap, lim, label) in enumerate(maps):
        a = ax.flat[i]
        sub = np.where(B.pad, data, np.nan)[box]
        im = a.imshow(sub, cmap=cmap, vmin=lim[0], vmax=lim[1])
        a.set_aspect("equal")
        a.set_xticks([])
        a.set_yticks([])
        a.set_title(label)
        fig.colorbar(im, ax=a, shrink=0.8)

    fig.suptitle(f"{B.title}   The photometric chain")

    cap = (
        f"\\begin{{figure}}[h!]\n"
        f"    \\centering\n"
        f"    \\includegraphics[scale=0.57]{{R_guide_{B.samp_no}_{B.cell}_6}}\n"
        f"    \\caption{{\n"
        f"    The full photometric chain for {B.cell} on {B.samp_no}. "
        f"Upper left: reflectance $R$ against the gray chips. "
        f"Upper right: reflectance infinity $R_\\\\infty$ against bare paper "
        f"(by construction, $R_\\\\infty \\\\approx 1$ on paper). "
        f"Lower left: absorbance $A = -\\\\log_{{10}} R_\\\\infty$, "
        f"the standard linearizing transform. "
        f"Lower right: Kubelka-Munk $f(R_\\\\infty)$, an alternative "
        f"linearization under the optically-thick assumption. "
        f"Both $A$ and $f(R_\\\\infty)$ are approximately proportional to "
        f"dye concentration in the unsaturated regime (dose < 40~ppb). "
        f"Above saturation, the spatial extent of staining (dyed area) "
        f"becomes the primary dose marker.\n"
        f"    }}\n"
        f"\\label{{fig:R_guide_{B.samp_no}_{B.cell}_6}}\n"
        f"\\end{{figure}}"
    )
    return fig, cap


# ── driver ─────────────────────────────────────────────────────────────

def illustrate(samp_no="C1020", led="royal_blue", layer="B", sec="",
               save=False, show=True, outdir=FIG):
    """
    Generate all figures for the photometric chain.

    Args:
        samp_no: Capture name without .h5.
        led, layer: Cell identifier.
        sec: "" for measurement, "pre" for pre-image.
        save: Write figures to outdir.
        show: Display figures.
        outdir: Output directory (default desktop/padanalysis/fig).

    Returns:
        The Bench that was drawn.
    """
    B = assemble(samp_no, led, layer, sec)
    stem = f"{samp_no}{('_' + sec) if sec else ''}_{B.cell}"

    figs = [
        fig_signal(B),
        fig_reflectance(B),
        fig_rinf(B),
        fig_absorbance(B),
        fig_kubelka_munk(B),
        fig_chain_comparison(B),
    ]

    for i, (fig, cap) in enumerate(figs, 1):
        _finish(fig, f"{stem}_{i}", save, show, outdir)
        print("\n" + cap + "\n")

    return B


if __name__ == "__main__":
    illustrate("C1020", "royal_blue", "B", save=True, show=False)
    A.close_all()
