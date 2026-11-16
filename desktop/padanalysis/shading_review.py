"""shading_review.py -- the shading study reassessed, one step at a time.

A hand-driven study, run from an IDE, like `shading_study.py`.  Started
2026-10-01 to rebuild the shading question from the beginning
(`docs/shading_study_2026-09-29.md`, section "Reassessment").

Step R1 (this file so far): how even is the light across the pad, before
and after the gray-chip polynomial?  Looked at on the pre-image, where
there is no dye, so any unevenness is light, shadow or paper texture.

Every quantity is in the bound-crop Bayer-layer grid (130 rows x 183
columns for bound 39).  "Array" below means one value per pixel on that
grid; "scalar" means one number.

    sig     array, counts.  The dark-corrected signal: raw counts minus
            the sweep's dark level (one number per Bayer layer).
    div     array, counts.  The gray-chip level at every pixel: the
            channel's flat-field polynomial, scaled so that it matches the
            two gray chips in this same photo (`padphot.divisor_map`).
    R       array, no units.  sig / div: brightness against the gray
            chips.  1.0 means as bright as the chips would be there.
    m_sig   scalar, counts.  Median of sig over the pad.
    m_R     scalar.  Median of R over the pad.
    b_sig   array.  log10(sig / m_sig): the pixel's brightness compared
            with the pad's median, on a log scale.  0 is the median;
            +0.01 is about 2.3 % brighter, -0.01 about 2.3 % darker.
    b_R     array.  log10(R / m_R), the same for R.

Read-only.  Figures go to `fig/<label>_<what>.png` when asked, where
<label> is the step (R1a, R1b, ...); the plot window is named the same.
"""
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

SP = Path(__file__).resolve().parent
sys.path.insert(0, str(SP))

import analysis_1 as a1                                     # noqa: E402
import padphot                                              # noqa: E402

FIG = SP / "fig"

#: When set to a folder (for example SP.parent.parent / "tex" / "figs"),
#: `_finish` also writes each figure there as shading-<label>.pdf, for
#: the report tex/shading_removal.tex.  None: no PDF.  See `tex_figs`.
TEX_FIGS = None

#: The sample and channel looked at first: one Bill knows well.
SAMP = "C1032"
CELL = ("royal_blue", "B")

#: The 18 samples with a pre-image and a dot, and the six channels they
#: all share (the same lists as shading_study.py).
POOL = [f"C{n}" for n in (*range(1017, 1026), *range(1028, 1037))]
CELLS = [("violet", "B"), ("royal_blue", "B"), ("cyan", "B"),
         ("green", "Gb"), ("green", "Gr"), ("photo_red", "R")]

#: Gaussian width, layer px, of the smoothing used to draw contour lines
#: and to measure the light's trend apart from the paper's texture.  The
#: texture is 1-2 px across (shading_study step 1c), so 4 px smooths it out.
TREND_PX = 4.0

#: Pad erosion, layer px, for the "inner pad": the same 4 px the dot-ring
#: analysis leaves out (`dot_study.ERODE_WHITE`), which drops the rim.
INNER_PX = 4.0


# ── data ───────────────────────────────────────────────────────────────

def frame(samp_no, cell, sec=a1.PRE) -> dict:
    """
    One channel of one photo, before and after the gray-chip polynomial.

    Args:
        samp_no: scalar str, sample number, e.g. "C1032".
        cell: (LED, Bayer layer), e.g. ("royal_blue", "B").
        sec: scalar str, a1.PRE for the pre-image, "" for the measurement.

    Returns:
        Dict, every array (rows, cols) on this photo's own grid, NaN at
        hot pixels and where the polynomial was not fitted:
            'sig'   array, counts, dark-corrected signal;
            'div'   array, counts, gray-chip level (`divisor_map`);
            'R'     array, sig / div;
            'pad'   array bool, the fitted pad, no erosion;
            'inner' array bool, the pad eroded INNER_PX;
            'chip1', 'chip2'  array bool, the gray chips;
            'rung'  scalar str, the exposure read;
            'frames'  list of str, the frames averaged into 'sig': rung
                    and its repeats at the same exposure_us
                    (`a1.repeat_labels`; one frame before C1049);
            'exposure_us'  scalar float, its exposure time.

    Repeat frames (C1049 on) are averaged on the dark-corrected signal,
    before the ratio, so camera noise falls by about sqrt(frames)
    (Bill, 2026-11-15).
    """
    led, layer = cell
    rung, why = a1.rung_verdict(samp_no, cell, sec)
    if rung is None:
        raise RuntimeError(f"{samp_no} {sec or 'post'} {a1.cell_name(cell)}: "
                           f"{why}")
    c = a1.open_sample(samp_no, sec)
    frames = a1.repeat_labels(samp_no, cell, rung, sec)
    sig = np.mean([c.layer(led, lab, layer)[0] for lab in frames],
                  axis=0).astype(float)   # (rows, cols): mean over frames
    hot = a1._hot(samp_no, sec)
    chip1 = a1._region(samp_no, a1.ROIGRAY1, sec) & ~hot
    chip2 = a1._region(samp_no, a1.ROIGRAY2, sec) & ~hot
    div = padphot.divisor_map(a1.models()[cell], c.bound, chip1, chip2, sig)
    sig[hot] = np.nan
    pad = a1.pad_of(samp_no, sec)
    return dict(sig=sig, div=div, R=sig / div,
                pad=pad.mask(0) & ~hot, inner=pad.mask(INNER_PX) & ~hot,
                chip1=chip1, chip2=chip2, rung=rung, frames=frames,
                exposure_us=float(c.exposure_us(led, rung)))


def rel_log(a, region) -> np.ndarray:
    """
    log10 of an array divided by its median over `region`.

    Args:
        a: array (rows, cols).
        region: array bool (rows, cols).

    Returns:
        array (rows, cols): 0 at the region's median, +0.01 about 2.3 %
        brighter.  NaN where `a` is NaN or not positive.
    """
    m = float(np.nanmedian(a[region]))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.log10(np.where(a > 0, a, np.nan) / m)


def smooth(a, region, w=TREND_PX) -> np.ndarray:
    """
    Gaussian smoothing that uses only pixels inside `region`.

    smooth(a * m) / smooth(m), with m the usable pixels, so pixels outside
    the region (or NaN) do not leak in.  The same method as
    `shading_study.smooth`.

    Args:
        a: array (rows, cols); NaN is treated as missing.
        region: array bool (rows, cols).
        w: scalar, Gaussian sigma in layer px.

    Returns:
        array (rows, cols), NaN outside `region`.
    """
    m = (region & np.isfinite(a)).astype(float)
    num = ndimage.gaussian_filter(np.where(m > 0, a, 0.0), w, mode="constant")
    den = ndimage.gaussian_filter(m, w, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[~region] = np.nan
    return out


def evenness(b, region) -> dict:
    """
    How even one log-brightness array is over one region.

    Two numbers, because two things make a pad uneven: the light, which
    changes slowly across it, and the paper's texture, which changes from
    pixel to pixel.

    Args:
        b: array (rows, cols), log10 brightness against the median
            (`rel_log`).
        region: array bool (rows, cols).

    Returns:
        Dict of scalars, all in log10 units (0.01 is about 2.3 %):
            'spread'  robust spread of every pixel (1.4826 x the median
                      absolute deviation): light and texture together;
            'trend'   range of the smoothed brightness, 95th minus 5th
                      percentile: the light's unevenness, with the
                      texture smoothed out (`TREND_PX`);
            'n'       scalar int, pixels used.
    """
    v = b[region & np.isfinite(b)]
    s = smooth(b, region)[region]
    s = s[np.isfinite(s)]
    p5, p95 = np.percentile(s, [5, 95])
    return dict(spread=float(1.4826 * np.median(np.abs(v - np.median(v)))),
                trend=float(p95 - p5), n=int(v.size))


# ── figures ────────────────────────────────────────────────────────────

def _finish(fig, label, what, figs, savefigs):
    """
    Save and/or show one finished figure, then release it.

    The step label leads the file name and names the plot window
    ("Figure R1a" instead of "Figure 1"), so figures are easy to tell apart.

    Args:
        fig: The matplotlib figure.
        label: scalar str, the step label, e.g. "R1a".
        what: scalar str, short name: fig/<label>_<what>.png.
        figs: True to show it on screen.
        savefigs: True to write it to the file.

    Returns:
        The Path written, or None.
    """
    import matplotlib.pyplot as plt
    manager = getattr(fig.canvas, "manager", None)
    if manager is not None:
        manager.set_window_title(f"Figure {label}")
    out = None
    if savefigs:
        FIG.mkdir(parents=True, exist_ok=True)
        out = FIG / f"{label}_{what}.png"
        fig.savefig(out, dpi=110)
        print(f"  wrote {out}")
    if TEX_FIGS is not None:
        Path(TEX_FIGS).mkdir(parents=True, exist_ok=True)
        pdf = Path(TEX_FIGS) / f"shading-{label}.pdf"
        fig.savefig(pdf)
        print(f"  wrote {pdf}")
    if figs:
        plt.show()
    else:
        plt.close(fig)
    return out


def _box(region, margin):
    """Row and column slices around a bool region, `margin` px wider."""
    ys, xs = np.nonzero(region)
    return (slice(max(ys.min() - margin, 0), ys.max() + margin + 1),
            slice(max(xs.min() - margin, 0), xs.max() + margin + 1))


def show_gray_maps(samp_no=SAMP, cell=CELL, sec=a1.PRE, figs=True,
                   savefigs=False) -> dict:
    """
    Step R1a: brightness maps before and after the gray-chip polynomial.

    Two rows of three panels, all as log10(value / its pad median):
    sig (before), div (the polynomial pegged to the chips, i.e. the light
    the correction assumes), and R = sig / div (after).  Top row: the
    whole bound crop, so the chips show.  Bottom row: the pad alone, on a
    tighter scale.  Color = each pixel; black lines = contours of the
    smoothed brightness (TREND_PX), the light's trend without the texture.

    Args:
        samp_no: scalar str, sample number.
        cell: (LED, Bayer layer).
        sec: scalar str, a1.PRE or "".
        figs, savefigs: show / save.

    Returns:
        The `frame` dict.
    """
    import matplotlib.pyplot as plt
    f = frame(samp_no, cell, sec)
    pad = f["pad"]
    maps = [("sig: before (counts)", rel_log(f["sig"], pad)),
            ("div: gray-chip level (counts)", rel_log(f["div"], pad)),
            ("R = sig / div: after", rel_log(f["R"], pad))]
    # The outline is the fitted pad itself; hot pixels are not cut out of
    # it, or each would draw as a small diamond.
    outline = a1.pad_of(samp_no, sec).mask(0)
    whole = np.ones_like(pad)
    rs, cs = _box(pad, 3)
    rows = [("whole crop", whole, (slice(None), slice(None)), 0.25, 0.05),
            ("pad", pad, (rs, cs), 0.06, 0.01)]

    fig, axes = plt.subplots(2, 3, figsize=(15, 9.5))
    for r, (label, region, (ry, cx), lim, step) in enumerate(rows):
        for k, (name, b) in enumerate(maps):
            ax = axes[r, k]
            show = np.where(region, b, np.nan)[ry, cx]
            im = ax.imshow(show, cmap="RdBu_r", vmin=-lim, vmax=lim,
                           interpolation="nearest")
            trend = smooth(b, region & np.isfinite(b))[ry, cx]
            levels = np.arange(-lim, lim + step / 2, step)
            cs_ = ax.contour(trend, levels=levels, colors="k",
                             linewidths=0.6)
            ax.clabel(cs_, fmt="%+.2f", fontsize=7)
            ax.contour(outline[ry, cx].astype(float), levels=[0.5],
                       colors="lime", linewidths=1.0)
            if r == 0:
                for chip in (a1._region(samp_no, a1.ROIGRAY1, sec),
                             a1._region(samp_no, a1.ROIGRAY2, sec)):
                    ax.contour(chip.astype(float), levels=[0.5],
                               colors="orange", linewidths=1.0)
            ax.set_title(f"{name}\n{label}", fontsize=10)
            ax.set_xlabel("column, layer px")
            if k == 0:
                ax.set_ylabel("row, layer px")
        fig.colorbar(im, ax=axes[r, :], shrink=0.85,
                     label="log10(value / pad median)\n"
                           "+ = brighter;  0.01 ≈ 2.3 %")
    fig.suptitle(f"{samp_no} {a1.cell_name(cell)} "
                 f"{'pre' if sec else 'post'}-image, rung {f['rung']} "
                 f"({f['exposure_us']:.0f} µs): brightness before and "
                 f"after the gray-chip polynomial.  Green = pad, "
                 f"orange = gray chips, black = smoothed contours",
                 fontsize=11)
    _finish(fig, "R1a", f"gray_maps_{samp_no}_{a1.cell_name(cell)}", figs,
            savefigs)
    return f


def show_gray_hist(samp_no=SAMP, cell=CELL, sec=a1.PRE, figs=True,
                   savefigs=False) -> dict:
    """
    Step R1b: histogram and profiles of pad brightness, before and after.

    Left: histograms of b_sig and b_R over the pad, same bins.  Right, two
    stacked panels sharing the x axis: the pad's brightness averaged down
    each column (top) and the smoothed-trend version (bottom), before and
    after, so a tilt in the light shows as a slope.

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).
        sec: scalar str, a1.PRE or "".
        figs, savefigs: show / save.

    Returns:
        Dict: 'before', 'after' -- each the `evenness` dict on the whole
        pad -- and 'before_inner', 'after_inner' on the inner pad.
    """
    import matplotlib.pyplot as plt
    f = frame(samp_no, cell, sec)
    pad, inner = f["pad"], f["inner"]
    b_sig, b_R = rel_log(f["sig"], pad), rel_log(f["R"], pad)

    fig = plt.figure(figsize=(14, 6))
    gs = fig.add_gridspec(2, 2, width_ratios=[1, 1.4])
    # ax = fig.add_subplot(gs[:, 0])
    # bins = np.linspace(-0.08, 0.04, 121)
    # bins = np.linspace(-0.08, 0.16, 121)
    # for b, name, color, histtype in ((b_R, "after (R = sig / div)", "C0", "stepfilled"),
    #                                  (b_sig, "before (sig)", "C1", "stepfilled"),
    #                                  (b_R, "after (R = sig / div)", "C0", "step")):
    #     v = b[pad & np.isfinite(b)]
    #     # ax.hist(v, bins=bins, histtype="stepfilled", color=color, alpha=0.5, lw=1.4,
    #     #         label=name)
    #     ax.hist(v, bins=bins, histtype=histtype, color=color, lw=1.4,
    #             label=name)
    # ax.set_yscale("log")
    # ax.set_xlabel("log10(value / pad median)   (+ = brighter; "
    #               "0.01 ≈ 2.3 %)")
    # ax.set_ylabel("pixels per bin")
    # ax.set_title("every pad pixel", fontsize=10)
    # ax.legend(fontsize=9)
    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[1, 0], sharex=ax1)
    bins = np.linspace(-0.08, 0.12, 121)
    for b, name, color, histtype in ((b_R, "after (R = sig / div)", "C0", "stepfilled"),
                                     (b_sig, "before (sig)", "C1", "stepfilled"),
                                     (b_R, "after (R = sig / div)", "C0", "step")):
        v = b[pad & np.isfinite(b)]
        ax1.hist(v, bins=bins, histtype=histtype, color=color, lw=1.4,
                label=name)
        v_t = b[inner & np.isfinite(b)] 
        ax2.hist(v_t, bins=bins, histtype=histtype, color=color, lw=1.4,
                label=name)
    ax1.set_yscale("log")
    ax2.set_yscale("log")
    ax2.set_xlabel("log10(value / pad median)   (+ = brighter; "
                  "0.01 ≈ 2.3 %)")
    ax1.set_ylabel("pixels per bin")
    ax2.set_ylabel("pixels per bin\ninner pad")
    ax1.set_title("every pad pixel", fontsize=10)
    ax1.legend(fontsize=9)

    ax1 = fig.add_subplot(gs[0, 1])
    ax2 = fig.add_subplot(gs[1, 1], sharex=ax1)
    cols = np.nonzero(pad.any(axis=0))[0]
    for b, name, color in ((b_sig, "before", "C1"), 
                           (b_R, "after", "C0")):
        per_col = [np.nanmean(b[:, c][pad[:, c]]) for c in cols]
        t = smooth(b, pad & np.isfinite(b))
        per_col_t = [np.nanmean(t[:, c][inner[:, c]])
                     if inner[:, c].any() else np.nan for c in cols]
        ax1.plot(cols, per_col, color=color, label=name)
        ax2.plot(cols, per_col_t, color=color, label=name)
    ax1.set_ylabel("column mean,\nwhole pad")
    ax2.set_ylabel(f"column mean of\nsmoothed ({TREND_PX:.0f} px),"
                   f"\ninner pad")
    ax2.set_xlabel("column, layer px  (across the pad, left to right)")
    for a in (ax1, ax2):
        a.axhline(0, color="0.6", lw=0.6)
        a.legend(fontsize=8)
    plt.setp(ax1.get_xticklabels(), visible=False)

    out = dict(before=evenness(b_sig, pad), after=evenness(b_R, pad),
               before_inner=evenness(b_sig, inner),
               after_inner=evenness(b_R, inner))
    fig.suptitle(f"{samp_no} {a1.cell_name(cell)} "
                 f"{'pre' if sec else 'post'}-image: pad brightness before "
                 f"and after the gray-chip polynomial.  Trend range "
                 f"(inner pad): {out['before_inner']['trend']:.4f} → "
                 f"{out['after_inner']['trend']:.4f} log10",
                 fontsize=11)
    fig.tight_layout()
    _finish(fig, "R1b", f"gray_hist_{samp_no}_{a1.cell_name(cell)}", figs,
            savefigs)
    return out


def normal_fit(v) -> dict:
    """
    Fit a normal (bell) curve to one set of values, and say how well it fits.

    The fit: the normal curve with the same mean and standard deviation as
    the values.  The measures of fit, each a scalar:

        mean, sd      the fitted curve's center and width.
        median, rsd   the same two from the middle of the data only:
                      median, and 1.4826 x the median absolute deviation.
                      For normal data rsd = sd.  rsd well below sd means
                      long tails are widening sd.
        skew          lopsidedness: mean of ((v - mean) / sd) ** 3.
                      0 for a normal curve; negative = a longer dark tail
                      (on this scale, darker is to the left).
        kurt          tail weight: mean of ((v - mean) / sd) ** 4, minus 3.
                      0 for a normal curve; positive = more values far out
                      than a normal curve has.
        out3          fraction of values more than 3 sd from the mean.
                      A normal curve puts 0.27 % there.
        ks_D          the largest gap between the data's running fraction
                      (fraction of values at or below x) and the normal
                      curve's, over all x.  0 = perfect; 0.05 means at
                      worst 5 % of the pixels sit on the wrong side of
                      some value.  No p-value is given: neighboring pixels
                      are not independent (the texture is 1-2 px across),
                      so the usual p-value would be wrong.

    Args:
        v: array (n,), the values, NaN already removed.

    Returns:
        Dict of the scalars above, plus 'n' (scalar int).
    """
    from scipy import stats
    v = np.asarray(v, float)
    mean, sd = float(v.mean()), float(v.std(ddof=1))
    med = float(np.median(v))
    z = (v - mean) / sd
    return dict(n=int(v.size), mean=mean, sd=sd, median=med,
                rsd=float(1.4826 * np.median(np.abs(v - med))),
                skew=float(np.mean(z ** 3)), kurt=float(np.mean(z ** 4) - 3),
                out3=float(np.mean(np.abs(z) > 3)),
                ks_D=float(stats.kstest(v, "norm", args=(mean, sd)).statistic))


def show_split_hist(samp_no=SAMP, cell=CELL, sec=a1.PRE, figs=True,
                    savefigs=False) -> dict:
    """
    Step R1d: the pad's histogram split into inner pad and rim, with
    normal curves fitted to the "after" values.

    Three panels stacked, sharing the x axis, same bins:
        top     every pad pixel (as in R1b);
        middle  the inner pad: the pad eroded INNER_PX;
        bottom  the rim: pad minus inner pad, the outer INNER_PX band.
                Its counts are exactly top minus middle.
    Each panel shows b_R ("after", filled blue), b_sig ("before", filled
    orange) and b_R again as an outline.  A normal curve is fitted to the
    "after" values of the inner pad and of the rim (`normal_fit`) and drawn
    on its own panel; the top panel shows both curves and their sum.  A
    curve is drawn as count per bin = n x bin width x the normal density.

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).
        sec: scalar str, a1.PRE or "".
        figs, savefigs: show / save.

    Returns:
        Dict: 'inner', 'rim' -- each the `normal_fit` dict of the "after"
        values -- and 'outside', scalar int, pad pixels beyond the bins.
    """
    import matplotlib.pyplot as plt
    f = frame(samp_no, cell, sec)
    pad, inner = f["pad"], f["inner"]
    rim = pad & ~inner
    b_sig, b_R = rel_log(f["sig"], pad), rel_log(f["R"], pad)
    width = 0.002                                     # scalar, bin width
    bins = np.arange(-0.20, 0.12 + width / 2, width)

    regions = [("every pad pixel", pad), (f"inner pad (eroded "
               f"{INNER_PX:.0f} px)", inner), (f"rim = pad minus inner "
               f"pad (outer {INNER_PX:.0f} px)", rim)]
    fits = {"inner": normal_fit(b_R[inner & np.isfinite(b_R)]),
            "rim": normal_fit(b_R[rim & np.isfinite(b_R)])}
    x = np.linspace(bins[0], bins[-1], 801)

    def curve(fit):
        """Fitted normal curve as count per bin, array over x."""
        return (fit["n"] * width / (fit["sd"] * np.sqrt(2 * np.pi))
                * np.exp(-0.5 * ((x - fit["mean"]) / fit["sd"]) ** 2))

    fig, axes = plt.subplots(3, 1, figsize=(11, 11), sharex=True)
    for ax, (title, region) in zip(axes, regions):
        for b, name, color, histtype in (
                (b_R, "after (R = sig / div)", "C0", "stepfilled"),
                (b_sig, "before (sig)", "C1", "stepfilled"),
                (b_R, "after (outline)", "C0", "step")):
            v = b[region & np.isfinite(b)]
            ax.hist(v, bins=bins, histtype=histtype, color=color, lw=1.2,
                    alpha=0.45 if histtype == "stepfilled" else 1.0,
                    label=name)
        ax.set_yscale("log")
        ax.set_ylim(0.7, None)
        ax.set_ylabel("pixels per bin")
        ax.set_title(title, fontsize=10)

    ci, cr = curve(fits["inner"]), curve(fits["rim"])
    axes[0].plot(x, ci, "k--", lw=1, label="normal fit, inner")
    axes[0].plot(x, cr, "k:", lw=1.2, label="normal fit, rim")
    axes[0].plot(x, ci + cr, "k-", lw=1, label="sum of the two fits")
    axes[1].plot(x, ci, "k--", lw=1.2, label="normal fit (after)")
    axes[2].plot(x, cr, "k:", lw=1.4, label="normal fit (after)")
    for ax, key in ((axes[1], "inner"), (axes[2], "rim")):
        fz = fits[key]
        ax.text(0.99, 0.95,
                f"after, n = {fz['n']}\n"
                f"mean {fz['mean']:+.4f}   sd {fz['sd']:.4f}\n"
                f"median {fz['median']:+.4f}   robust sd {fz['rsd']:.4f}\n"
                f"skew {fz['skew']:+.2f}   excess kurtosis {fz['kurt']:+.2f}\n"
                f"beyond 3 sd {100 * fz['out3']:.2f} %  (normal: 0.27 %)\n"
                f"largest gap in running fraction {fz['ks_D']:.3f}",
                transform=ax.transAxes, ha="right", va="top", fontsize=8.5,
                family="monospace",
                bbox=dict(boxstyle="round", fc="white", ec="0.7"))
    for ax in axes:
        ax.legend(fontsize=8, loc="upper left")
    axes[2].set_xlabel("log10(value / pad median)   (+ = brighter; "
                       "0.01 ≈ 2.3 %)")
    v_all = b_R[pad & np.isfinite(b_R)]
    outside = int(np.sum((v_all < bins[0]) | (v_all > bins[-1])))
    fig.suptitle(f"{samp_no} {a1.cell_name(cell)} "
                 f"{'pre' if sec else 'post'}-image: pad brightness, inner "
                 f"pad and rim, with normal curves fitted to 'after'"
                 + (f"  ({outside} pixels beyond the axis)" if outside
                    else ""), fontsize=11)
    fig.tight_layout()
    _finish(fig, "R1d", f"split_hist_{samp_no}_{a1.cell_name(cell)}", figs,
            savefigs)
    return dict(inner=fits["inner"], rim=fits["rim"], outside=outside)


def gray_table(samps=POOL, cells=CELLS, sec=a1.PRE) -> list:
    """
    Step R1c: the evenness numbers for every sample and channel.

    Args:
        samps: list of scalar str, sample numbers.
        cells: list of (LED, Bayer layer).
        sec: scalar str, a1.PRE or "".

    Returns:
        List of dicts, one per sample and channel: 'samp', 'cell', and the
        `evenness` numbers before and after, whole pad and inner pad.
        Printed as a table, with the medians over all rows at the end.
    """
    rows = []
    for s in samps:
        for cell in cells:
            try:
                f = frame(s, cell, sec)
            except RuntimeError as e:
                print(f"  skip: {e}")
                continue
            b_sig, b_R = rel_log(f["sig"], f["pad"]), rel_log(f["R"], f["pad"])
            rows.append(dict(samp=s, cell=a1.cell_name(cell),
                             sp_b=evenness(b_sig, f["pad"])["spread"],
                             sp_a=evenness(b_R, f["pad"])["spread"],
                             tr_b=evenness(b_sig, f["inner"])["trend"],
                             tr_a=evenness(b_R, f["inner"])["trend"]))
    print(f"\n{'sample':7} {'channel':14} {'spread before':>14} "
          f"{'after':>7} {'trend before':>13} {'after':>7}")
    print(f"{'':22} {'(whole pad, log10)':>22} {'(inner pad, log10)':>22}")
    for r in rows:
        print(f"{r['samp']:7} {r['cell']:14} {r['sp_b']:14.4f} "
              f"{r['sp_a']:7.4f} {r['tr_b']:13.4f} {r['tr_a']:7.4f}")
    med = {k: float(np.median([r[k] for r in rows]))
           for k in ("sp_b", "sp_a", "tr_b", "tr_a")}
    print(f"{'median':22} {med['sp_b']:14.4f} {med['sp_a']:7.4f} "
          f"{med['tr_b']:13.4f} {med['tr_a']:7.4f}   ({len(rows)} rows)")
    return rows


# ── step R2: divide the post-image by the pre-image, pixel by pixel ───

#: The dye-free zone starts this many layer px outside the dot's edge
#: (the same 6 px as shading_study.WHITE_GAP).
WHITE_GAP = 6.0


def post_pre(samp_no=SAMP, cell=CELL) -> dict:
    """
    The post-image, the pre-image lined up on it, and their difference, in A.

    Both photos go through the chain to R (dark level, gray-chip
    polynomial) by `analysis_1.get_balanced_cell_data`.  The pre-image's R
    is carried onto the post-image's grid by the chain's own registration
    (`analysis_1.pre_to_post`, bilinear); that is done for the pad only,
    which is why everything here is on the pad.  Both are divided by the
    same scalar white, `R_white` (`analysis_1.pre_white`, the chain's
    default erosion), so:

        A_post  = -log10(R_post / R_white)      array, the chain's A now
        A_pre   = -log10(R_pre  / R_white)      array, the dry pad's A,
                                                on the post-image's grid
        A_flat  = A_post - A_pre                array
                = -log10(R_post / R_pre)        (R_white cancels)

    A_flat is the post-image divided by the pre-image pixel by pixel.  A:
    bigger means darker.

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).

    Returns:
        Dict on the post-image's grid (rows, cols):
            'A_post', 'A_pre', 'A_flat'  arrays, NaN off the pad and where
                                         either photo has no value;
            'R_white'  scalar;
            'pad'      array bool, the pad (no erosion), hot pixels out;
            'dot'      array bool, inside the dot's edge (dyed);
            'free'     array bool, pad at least WHITE_GAP px outside the
                       dot (dye-free);
            'inner'    array bool, pad deeper than INNER_PX from its edge;
            'rim'      array bool, pad within INNER_PX of its edge;
            'dist'     array, signed distance from the dot's edge, px
                       (negative inside the dot).
    """
    import pca_study as ps
    led, layer = cell
    post = a1.get_balanced_cell_data(samp_no, led, layer, roi=a1.ROIBOUND)
    pre = a1.get_balanced_cell_data(samp_no, led, layer, roi=a1.ROISAMPLE,
                                    sec=a1.PRE, erode=0, register=True)
    R_white = a1.pre_white(samp_no, cell)[0]
    pad_rect = a1.pad_of(samp_no)
    pad = pad_rect.mask(0) & ~a1._hot(samp_no)
    R_post = np.where(pad, np.ma.filled(post.R, np.nan), np.nan)
    R_pre = np.where(pad, np.ma.filled(pre.R, np.nan), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        A_post = -np.log10(R_post / R_white)
        A_pre = -np.log10(R_pre / R_white)
    ok = np.isfinite(A_post) & np.isfinite(A_pre)
    A_post[~ok] = np.nan
    A_pre[~ok] = np.nan
    pad = pad & ok
    dist = ps.dot_geometry(samp_no)[3]
    depth = pad_rect.depth
    return dict(A_post=A_post, A_pre=A_pre, A_flat=A_post - A_pre,
                R_white=R_white, pad=pad, dot=pad & (dist < 0),
                free=pad & (dist >= WHITE_GAP), inner=pad & (depth > INNER_PX),
                rim=pad & (depth <= INNER_PX), dist=dist)


def r2_numbers(d) -> dict:
    """
    Step R2c: what the division did, as numbers.

    For each region, the mean and the spread (standard deviation, sd) of
    A_post (before), A_pre and A_flat (after), all scalars in A.  Plus, for
    each region, r: the correlation between A_post and A_pre pixel by
    pixel (1 = the same pattern in both photos, 0 = unrelated).

    Why r matters: the spread after the division follows exactly from the
    three numbers before it,

        sd_after^2 = sd_post^2 + sd_pre^2 - 2 * r * sd_post * sd_pre

    So if the two photos' patterns were unrelated (r = 0), dividing would
    *add* the pre-image's spread: sd_after = sqrt(sd_post^2 + sd_pre^2).
    If they matched perfectly (r = 1, equal spreads), it would cancel.
    'if_unrelated' gives the first case for comparison.

    Args:
        d: dict from `post_pre`.

    Returns:
        {region name: dict of scalars 'n', 'mean_post', 'mean_pre',
        'mean_flat', 'sd_post', 'sd_pre', 'sd_flat', 'r', 'if_unrelated'}.
    """
    regions = [("whole pad", d["pad"]), ("dyed (inside the dot)", d["dot"]),
               ("dye-free, inner", d["free"] & d["inner"]),
               ("dye-free, rim", d["free"] & d["rim"])]
    out = {}
    for name, m in regions:
        p, q, f = d["A_post"][m], d["A_pre"][m], d["A_flat"][m]
        sp, sq = float(p.std(ddof=1)), float(q.std(ddof=1))
        out[name] = dict(n=int(m.sum()), mean_post=float(p.mean()),
                         mean_pre=float(q.mean()), mean_flat=float(f.mean()),
                         sd_post=sp, sd_pre=sq, sd_flat=float(f.std(ddof=1)),
                         r=float(np.corrcoef(p, q)[0, 1]),
                         if_unrelated=float(np.hypot(sp, sq)))
    return out


def show_r2_maps(samp_no=SAMP, cell=CELL, figs=True, savefigs=False) -> dict:
    """
    Step R2a: maps of A_post, A_pre and A_flat on the pad.

    Two rows of three panels, pad zoom.  Top row: a scale that shows the
    dot (0 to the dot's 99th percentile).  Bottom row: a tight scale
    centered on 0, set so the dye-free shading shows and the dot
    saturates.  Black line = the dot's edge; gray line = start of the
    dye-free zone (WHITE_GAP px out).  A: bigger means darker (red).

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).
        figs, savefigs: show / save.

    Returns:
        The `post_pre` dict.
    """
    import matplotlib.pyplot as plt
    d = post_pre(samp_no, cell)
    ry, cx = _box(a1.pad_of(samp_no).mask(0), 3)
    top = float(np.nanpercentile(d["A_post"][d["dot"]], 99))
    tight = 0.04
    # Labels in the notation of tex/analysis_chain.tex.  The arrays are
    # the code's: A_post = A - b_w, A_pre = B - b_w, A_flat = A - B, with
    # b_w = -log10(R_white).
    names = [(r"$\mathbf{C}$ by method $\mathcal{A}$: $\mathbf{A} - b_w$",
              d["A_post"]),
             (r"$\mathbf{B} - b_w$: pre-image, lined up", d["A_pre"]),
             (r"$\mathbf{C}$ by method $\mathcal{C}$: "
              r"$\mathbf{A} - \mathbf{B}$", d["A_flat"])]
    fig, axes = plt.subplots(2, 3, figsize=(14, 10))
    for r, (vmin, vmax, cmap, what) in enumerate(
            [(-0.02, top, "magma_r", "scale to show the dot"),
             (-tight, tight, "RdBu_r", "tight scale: shading")]):
        for k, (name, A) in enumerate(names):
            ax = axes[r, k]
            im = ax.imshow(A[ry, cx], cmap=cmap, vmin=vmin, vmax=vmax,
                           interpolation="nearest")
            ax.contour(d["dist"][ry, cx], levels=[0.0], colors="k",
                       linewidths=0.9)
            ax.contour(d["dist"][ry, cx], levels=[WHITE_GAP], colors="0.5",
                       linewidths=0.9)
            ax.set_title(f"{name}\n{what}", fontsize=9.5)
            ax.set_xlabel("column, layer px")
            if k == 0:
                ax.set_ylabel("row, layer px")
        fig.colorbar(im, ax=axes[r, :], shrink=0.85,
                     label="absorbance, bigger = darker")
    fig.suptitle(f"{samp_no} {a1.cell_name(cell)} post-image, divided by "
                 f"its pre-image pixel by pixel.  "
                 r"$b_w = -\log_{10} R_\mathrm{white}$.  "
                 f"Black = dot edge, gray = start of the dye-free zone "
                 f"({WHITE_GAP:.0f} px out)", fontsize=11)
    _finish(fig, "R2a", f"maps_{samp_no}_{a1.cell_name(cell)}", figs,
            savefigs)
    return d


def show_r2_hist(samp_no=SAMP, cell=CELL, figs=True, savefigs=False) -> dict:
    """
    Step R2b: histograms of A before and after the division, by region.

    Four panels stacked, sharing the x axis and the bins:
        whole pad; dyed (inside the dot's edge); dye-free inner pad;
        dye-free rim (within INNER_PX of the pad's edge).
    The band between the dot's edge and the dye-free zone (0 to WHITE_GAP
    px outside the dot, where the color fades) is in "whole pad" only.
    Each panel: A_post (before, filled orange), A_flat (after, filled
    blue, and as an outline).  A normal curve is fitted to "after" in the
    three parts (`normal_fit`) and its measures are printed in a box, with
    the "before" sd for comparison.

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).
        figs, savefigs: show / save.

    Returns:
        {region name: `normal_fit` dict of "after", plus 'sd_before'}.
    """
    import matplotlib.pyplot as plt
    d = post_pre(samp_no, cell)
    regions = [("whole pad", d["pad"]), ("dyed (inside the dot's edge)",
               d["dot"]), (f"dye-free, inner pad (≥ {WHITE_GAP:.0f} px "
               f"outside the dot, > {INNER_PX:.0f} px from the pad edge)",
               d["free"] & d["inner"]), (f"dye-free, rim (within "
               f"{INNER_PX:.0f} px of the pad edge)", d["free"] & d["rim"])]
    width = 0.002                                      # scalar, bin width
    hi = float(np.nanpercentile(d["A_post"][d["pad"]], 99.9)) + 0.01
    lo = min(-0.05, float(np.nanpercentile(d["A_flat"][d["pad"]], 0.1)))
    bins = np.arange(lo, hi + width / 2, width)
    x = np.linspace(lo, hi, 801)

    fig, axes = plt.subplots(4, 1, figsize=(11, 13), sharex=True)
    out = {}
    for i, (ax, (title, m)) in enumerate(zip(axes, regions)):
        for A, name, color, histtype in (
                (d["A_flat"], "after: A_flat = A_post − A_pre", "C0",
                 "stepfilled"),
                (d["A_post"], "before: A_post", "C1", "stepfilled"),
                (d["A_flat"], "after (outline)", "C0", "step")):
            ax.hist(A[m], bins=bins, histtype=histtype, color=color, lw=1.2,
                    alpha=0.45 if histtype == "stepfilled" else 1.0,
                    label=name)
        ax.set_yscale("log")
        ax.set_ylim(0.7, None)
        ax.set_ylabel("pixels per bin")
        ax.set_title(title, fontsize=10)
        if i == 0:
            ax.legend(fontsize=8, loc="upper right")
            continue
        fz = normal_fit(d["A_flat"][m])
        fz["sd_before"] = float(d["A_post"][m].std(ddof=1))
        out[("dyed", "dye-free, inner", "dye-free, rim")[i - 1]] = fz
        ax.plot(x, fz["n"] * width / (fz["sd"] * np.sqrt(2 * np.pi))
                * np.exp(-0.5 * ((x - fz["mean"]) / fz["sd"]) ** 2),
                "k--", lw=1.1, label="normal fit (after)")
        ax.legend(fontsize=8, loc="upper left")
        ax.text(0.99, 0.95,
                f"n = {fz['n']}\n"
                f"sd before {fz['sd_before']:.4f} → after {fz['sd']:.4f}\n"
                f"after: mean {fz['mean']:+.4f}  median {fz['median']:+.4f}\n"
                f"robust sd {fz['rsd']:.4f}\n"
                f"skew {fz['skew']:+.2f}   excess kurtosis {fz['kurt']:+.2f}\n"
                f"beyond 3 sd {100 * fz['out3']:.2f} %  (normal: 0.27 %)\n"
                f"largest gap in running fraction {fz['ks_D']:.3f}",
                transform=ax.transAxes, ha="right", va="top", fontsize=8.5,
                family="monospace",
                bbox=dict(boxstyle="round", fc="white", ec="0.7"))
    axes[-1].set_xlabel("A = −log10(R / R_white)   (bigger = darker; "
                        "0 = the dry pad's median)")
    fig.suptitle(f"{samp_no} {a1.cell_name(cell)}: A before and after "
                 f"dividing the post-image by the pre-image pixel by pixel",
                 fontsize=11)
    fig.tight_layout()
    _finish(fig, "R2b", f"hist_{samp_no}_{a1.cell_name(cell)}", figs,
            savefigs)
    return out


# ── step R3: four ways to divide the post-image by the pre-image ─────

#: Blur widths for method B, Gaussian sigma in layer px.  The edge shadow
#: is 2-3 px wide and the fiber texture 1-2 px (shading_study step 1c).
R3_BLUR = (1.0, 2.0)

#: Width, layer px, that splits the pre-image into a smooth part and a
#: fine part for method D.
R3_D_PX = 1.0

#: Width, layer px, that splits each result's error into a smooth part
#: (shadow left over or overdone) and a fine part (everything that
#: changes within about 2 px).  2 px: wider than the fiber texture,
#: narrower than the edge shadow's 2-3 px.
R3_SPLIT_PX = 2.0


def r3_methods(d) -> dict:
    """
    Step R3: the post-image divided by the pre-image four ways.

    Everything is in A, where dividing reflectances is subtracting A, so
    each method is A_after = A_post - corr, with corr what is taken out:

        A   corr = 0                        the chain now: the pre-image
                                            enters only as one scalar,
                                            R_white (the median of its
                                            pad, eroded 6 px), already in
                                            A_post.
        B   corr = smooth(A_pre, w)         array; the pre-image blurred,
                                            w in R3_BLUR (B1 = 1 px,
                                            B2 = 2 px).
        C   corr = A_pre                    array; pixel by pixel (R2).
        D   corr = wt * A_pre + (1 - wt) * s
                                            array; a weighted mix of the
                                            pre-image and its blur, the
                                            weights summing to 1.
                                            s = smooth(A_pre, R3_D_PX).

    D sits between B1 and C: with f = A_pre - s (the fine part),
    corr = s + wt * f, so wt = 0 is B1 and wt = 1 is C.  wt (scalar) is
    the least-squares weight that leaves A_after flattest on the dye-free
    zone (A_post - s fitted to 1 and f there; the constant is not used).

    For comparison only, 'gamma_free' and 'beta_free' are the weights on
    s and f when both are fitted freely (the original study's gamma and
    beta, fitted together); they are not used.

    smooth() averages pad pixels only (`smooth`), so the paper outside the
    pad does not leak in at the edge.

    The error of each result is measured against the dye-free inner pad,
    where the corrected pad should be flat:

        level   SCALAR, one per method: the median of A_after over the
                dye-free inner pad;
        err     array, A_after - level, on the whole pad: 0 means "as
                the dye-free inner pad".  Inside the dot it is mostly
                dye, not error;
        err_s   array, smooth(err, R3_SPLIT_PX) over the whole pad: the
                error's smooth part, shadow left over (positive, darker)
                or overdone (negative);
        err_f   array, err - err_s: the error's fine part, everything
                that changes within about 2 px.  It is not only camera
                noise: it also holds fiber texture that was not
                cancelled, any one-pixel mismatch at the pad's edge, and
                near the dot the dye's own steep edge.

    Every SCALAR that touches the arrays here:
        R_white  (in A_post and A_pre) one number for the whole pad.  It
                 cancels exactly in B, C and D (smoothing a constant gives
                 the same constant), and `level` removes it in A, so it
                 has no effect on err, err_s or err_f.
        level    one number per method, subtracted from every pad pixel,
                 rim and dot included.  It sets where 0 is, and only
                 that: it makes every method's dye-free inner pad center
                 on 0, so a method that shifts the whole inner pad (B2)
                 does not show that shift in err.
        wt       (D only) one number for the whole pad, fitted on the
                 dye-free zone (inner and rim pooled) and applied to
                 every pad pixel, the dot included.
        the fit's constant (D only) fitted with wt so that an overall
                 offset does not bias wt, then thrown away.
    The blur widths (R3_BLUR, R3_D_PX, R3_SPLIT_PX) are fixed settings,
    not measured from the data.

    Args:
        d: dict from `post_pre`.

    Returns:
        {method name: dict with 'corr', 'after', 'err', 'err_s', 'err_f'
        (arrays); 'level' and, as corr = gamma * s + beta * f, 'gamma'
        and 'beta' (scalars; for D, beta = wt); 'gamma_free', 'beta_free'
        (scalars, the same in every entry); 'what' (str)}.
    """
    pad, A_pre = d["pad"], d["A_pre"]
    s = smooth(A_pre, pad, R3_D_PX)
    f = A_pre - s
    fz = d["free"] & np.isfinite(s)
    X = np.column_stack([np.ones(fz.sum()), f[fz]])
    _, wt = np.linalg.lstsq(X, d["A_post"][fz] - s[fz], rcond=None)[0]
    X = np.column_stack([np.ones(fz.sum()), s[fz], f[fz]])
    _, gamma_free, beta_free = np.linalg.lstsq(X, d["A_post"][fz],
                                               rcond=None)[0]
    # 'what' labels use the notation of tex/analysis_chain.tex: each
    # method takes out an estimated shading B^ (here relative to b_w).
    corrs = [("A", np.zeros_like(A_pre), 0.0, 0.0,
              r"$\mathcal{A}$: one white, $\widehat{\mathbf{B}} = b_w$")]
    for k, w in enumerate(R3_BLUR, 1):
        corrs.append((f"B{k}", smooth(A_pre, pad, w), 1.0, 0.0,
                      rf"$\mathcal{{B}}{k}$: $\widehat{{\mathbf{{B}}}} = "
                      rf"\widetilde{{\mathbf{{B}}}}$, $\sigma$ = {w:g} px"))
    corrs.append(("C", A_pre.copy(), 1.0, 1.0,
                  r"$\mathcal{C}$: $\widehat{\mathbf{B}} = \mathbf{B}$"))
    corrs.append(("D", wt * A_pre + (1 - wt) * s, 1.0, float(wt),
                  rf"$\mathcal{{D}}$: $\widehat{{\mathbf{{B}}}} = "
                  rf"{wt:.2f}\,\mathbf{{B}} + {1 - wt:.2f}\,"
                  rf"\widetilde{{\mathbf{{B}}}}$"))
    inner_free = d["free"] & d["inner"]
    out = {}
    for name, corr, g, b, what in corrs:
        corr = np.where(pad, corr, np.nan)
        after = d["A_post"] - corr
        level = float(np.nanmedian(after[inner_free]))
        err = after - level
        err_s = smooth(err, pad, R3_SPLIT_PX)
        out[name] = dict(corr=corr, after=after, err=err, err_s=err_s,
                         err_f=err - err_s, level=level, gamma=g, beta=b,
                         what=what, gamma_free=float(gamma_free),
                         beta_free=float(beta_free))
    return out


def _r3_regions(d) -> list:
    """(short name, bool array) for the regions R3 reports on."""
    return [("dye-free inner", d["free"] & d["inner"]),
            ("dye-free rim", d["free"] & d["rim"]),
            ("dyed", d["dot"])]


def r3_numbers(d, m) -> dict:
    """
    Step R3c: each method's error and its two parts, by region, as
    numbers.

    Args:
        d: dict from `post_pre`.
        m: dict from `r3_methods`.

    Returns:
        {(method, region): dict of scalars, all in A:
            'n'      pixels;
            'mean'   mean of A_after (in the dot: how dark it reads);
            'sd'     sd of err (the whole error);
            'rsd'    robust sd of err (1.4826 x median absolute deviation);
            'sd_s'   sd of err_s (shadow left over or overdone);
            'sd_f'   sd of err_f (the fine part: camera noise, fiber
                     texture, one-pixel edge mismatch);
            'p1', 'p99'  1st and 99th percentiles of err}.
    """
    out = {}
    for name, v in m.items():
        for region, r in _r3_regions(d):
            r = r & np.isfinite(v["err_s"])
            e = v["err"][r]
            out[name, region] = dict(
                n=int(r.sum()), mean=float(v["after"][r].mean()),
                sd=float(e.std(ddof=1)),
                rsd=float(1.4826 * np.median(np.abs(e - np.median(e)))),
                sd_s=float(v["err_s"][r].std(ddof=1)),
                sd_f=float(v["err_f"][r].std(ddof=1)),
                p1=float(np.percentile(e, 1)),
                p99=float(np.percentile(e, 99)))
    return out


def show_r3_maps(samp_no=SAMP, cell=CELL, figs=True, savefigs=False) -> dict:
    """
    Step R3a: where each method leaves its error, as maps.

    One column per method (A, B1, B2, C, D), pad zoom.  Every panel is an
    array on the pad; the SCALARS each column uses are printed under its
    title.  Rows (see `r3_methods` for the full definitions):
        1. corr = what the method takes out of A_post.  All zero for A,
           whose only correction, the scalar R_white, is already inside
           A_post and so cannot be drawn.
        2. err = A_post - corr - level.  level is a SCALAR, one per
           method (median over the dye-free inner pad), subtracted from
           every pixel; it only sets where 0 is.  Inside the dot this is
           dye, not error.
        3. err_s = blur(err), R3_SPLIT_PX wide, over the whole pad: the
           error's smooth part.  Red = shadow left over, blue = overdone.
        4. err_f = err - err_s: the error's fine part, everything that
           changes within about 2 px (camera noise, fiber texture not
           cancelled, one-pixel edge mismatch; around the dot, the dye's
           steep edge).
    Rows 1-3 share one scale (± 0.04 A), row 4 a tighter one (± 0.02 A);
    the dot saturates (dark red) on purpose.  Black line = the dot's
    edge; gray line = start of the dye-free zone.

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).
        figs, savefigs: show / save.

    Returns:
        The `r3_methods` dict.
    """
    import matplotlib.pyplot as plt
    d = post_pre(samp_no, cell)
    m = r3_methods(d)
    ry, cx = _box(a1.pad_of(samp_no).mask(0), 3)
    rows = [("corr", "1. taken out, relative to $b_w$:\n"
                     r"$\widehat{\mathbf{B}} - b_w$", 0.04),
            ("err", "2. error:\n"
                    r"$\mathbf{D} = \mathbf{C} - \overline{C}$", 0.04),
            ("err_s", "3. smooth part of error:\n"
                      rf"$\widetilde{{\mathbf{{D}}}}$, $\sigma$ = "
                      rf"{R3_SPLIT_PX:g} px", 0.04),
            ("err_f", "4. fine part of error:\n"
                      r"$\mathbf{D} - \widetilde{\mathbf{D}}$", 0.02)]
    scalars = {"A": r"$R_\mathrm{white}$, $\overline{C}$",
               "B1": r"$\overline{C}$", "B2": r"$\overline{C}$",
               "C": r"$\overline{C}$",
               "D": rf"$\hat\lambda$ = {m['D']['beta']:.2f} (whole pad), "
                    r"$\overline{C}$"}
    fig, axes = plt.subplots(len(rows), len(m), figsize=(13, 14),
                             layout="constrained")
    for i, (key, what, lim) in enumerate(rows):
        for k, (name, v) in enumerate(m.items()):
            ax = axes[i, k]
            im = ax.imshow(v[key][ry, cx], cmap="RdBu_r", vmin=-lim,
                           vmax=lim, interpolation="nearest")
            ax.contour(d["dist"][ry, cx], levels=[0.0], colors="k",
                       linewidths=0.8)
            ax.contour(d["dist"][ry, cx], levels=[WHITE_GAP], colors="0.5",
                       linewidths=0.8)
            ax.set_xticks([])
            ax.set_yticks([])
            if i == 0:
                ax.set_title(f"{v['what']}\nSCALARS: {scalars[name]}\n"
                             rf"$\overline{{C}}$ = {v['level']:+.4f}",
                             fontsize=9.5)
            if k == 0:
                ax.set_ylabel(what, fontsize=10)
        fig.colorbar(im, ax=axes[i, :], shrink=0.9,
                     label="absorbance (bigger = darker)")
    fig.suptitle(f"{samp_no} {a1.cell_name(cell)}: the post-image divided "
                 f"by the pre-image four ways, "
                 r"$\mathbf{C} = \mathbf{A} - \widehat{\mathbf{B}}$.  "
                 f"Black = dot edge, gray = start of the dye-free zone",
                 fontsize=11)
    # supxlabel, not fig.text, so the constrained layout leaves room.
    fig.supxlabel(
        r"Images (one value per pixel): $\mathbf{A}$, $\mathbf{B}$, "
        r"$\widetilde{\mathbf{B}}$, $\widehat{\mathbf{B}}$, $\mathbf{C}$, "
        r"$\mathbf{D}$, $\widetilde{\mathbf{D}}$."
        "\nSCALARS (one number, applied to every pad pixel):"
        "\n" r"  $R_\mathrm{white}$ = median of the pre-image pad (more than 6 "
        r"px in);  $b_w = -\log_{10} R_\mathrm{white}$.  Method "
        r"$\mathcal{A}$ only."
        "\n" r"  $\overline{C}$ = median of $\mathbf{C}$ over the dye-free "
        "inner pad, one per method; sets 0 only."
        "\n" r"  $\hat\lambda$ (method $\mathcal{D}$ only) = least-squares "
        "weight fitted on the dye-free zone, inner and rim pooled."
        "\n  smoothing (tilde) = Gaussian over pad pixels only; "
        r"$\mathcal{B}$1 1 px, $\mathcal{B}$2 2 px, $\mathcal{D}$ 1 px, "
        f"rows 3-4 {R3_SPLIT_PX:g} px.", fontsize=9, x=0.02, ha="left")
    _finish(fig, "R3a", f"maps_{samp_no}_{a1.cell_name(cell)}", figs,
            savefigs)
    return m


def show_r3_sorted(samp_no=SAMP, cell=CELL, figs=True,
                   savefigs=False) -> dict:
    """
    Step R3b: sorted error and sorted pixel values, all methods overlaid.

    Each curve is one method's values in one region, sorted from lowest
    to highest and drawn against the percent of pixels below.  A flat
    curve near 0 is a good correction; a steep end shows a few pixels far
    off (for example the pad's outermost column).
        top row:    err on the dye-free inner pad; err on the dye-free
                    rim; err_f (the fine part) on the whole dye-free
                    zone;
        bottom row: A_after (the pixel values) on the whole pad, the dyed
                    part, and the dye-free zone.

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).
        figs, savefigs: show / save.

    Returns:
        The `r3_numbers` dict.
    """
    import matplotlib.pyplot as plt
    d = post_pre(samp_no, cell)
    m = r3_methods(d)
    nums = r3_numbers(d, m)
    inner_free, rim_free = d["free"] & d["inner"], d["free"] & d["rim"]
    panels = [("err", inner_free, r"error $\mathbf{D}$, dye-free inner pad"),
              ("err", rim_free, r"error $\mathbf{D}$, dye-free rim"),
              ("err_f", d["free"], r"fine part $\mathbf{D} - "
               r"\widetilde{\mathbf{D}}$, dye-free zone"),
              ("after", d["pad"], r"stain $\mathbf{C}$, whole pad"),
              ("after", d["dot"], r"stain $\mathbf{C}$, dyed"),
              ("after", d["free"], r"stain $\mathbf{C}$, dye-free zone")]
    styles = {"A": ("k", "-"), "B1": ("C0", "-"), "B2": ("C9", "--"),
              "C": ("C3", "-"), "D": ("C2", "-.")}
    fig, axes = plt.subplots(2, 3, figsize=(14, 9))
    for ax, (key, region, title) in zip(axes.flat, panels):
        for name, v in m.items():
            vals = np.sort(v[key][region & np.isfinite(v[key])])
            pct = 100 * (np.arange(vals.size) + 0.5) / vals.size
            color, ls = styles[name]
            ax.plot(pct, vals, color=color, ls=ls, lw=1.3, label=v["what"])
        ax.axhline(0, color="0.6", lw=0.6)
        ax.set_title(f"{title}  (n = {int(region.sum())})", fontsize=10)
        ax.set_xlabel("percent of pixels below")
        ax.grid(alpha=0.3)
    for ax in axes[0]:
        ax.set_ylabel(r"$\mathbf{D}$ (0 = the dye-free inner pad)")
    for ax in axes[1]:
        ax.set_ylabel(r"$\mathbf{C}$ (bigger = darker)")
    axes[0, 0].legend(fontsize=8.5, loc="upper left")
    fig.suptitle(f"{samp_no} {a1.cell_name(cell)}: sorted error and sorted "
                 f"pixel values for the four ways of dividing by the "
                 f"pre-image", fontsize=11)
    fig.tight_layout()
    _finish(fig, "R3b", f"sorted_{samp_no}_{a1.cell_name(cell)}", figs,
            savefigs)
    return nums


# ── how much lining up lowers the pre-image's pixel-to-pixel scatter ──

def _neighbor_diffs(X, region, axis) -> np.ndarray:
    """
    Differences between each pixel and its next neighbor, over a region.

    Args:
        X: array (rows, cols); NaN is treated as missing.
        region: array bool (rows, cols).
        axis: scalar int, 1 for the right-hand neighbor (along a row), 0
            for the one below (along a column).

    Returns:
        1-D array: (X[next] - X[this]) / sqrt(2), for every pair with both
        pixels in `region` and finite.  Divided by sqrt(2) so that, for
        independent pixels, its spread is the spread of one pixel.
    """
    ok = region & np.isfinite(X)
    if axis == 1:
        d, both = X[:, 1:] - X[:, :-1], ok[:, 1:] & ok[:, :-1]
    else:
        d, both = X[1:, :] - X[:-1, :], ok[1:, :] & ok[:-1, :]
    return d[both] / np.sqrt(2.0)


def lineup_noise(samp_no=SAMP, cell=CELL) -> dict:
    """
    Step R4a: the pixel-to-pixel scatter of the pre-image, before and after
    lining it up on the post-image's grid.

    Lining up (`analysis_1._register`) takes each value as a weighted
    average of the 4 nearest pre-image pixels (bilinear).  Averaging lowers
    the scatter, so the lined-up pre-image looks smoother than the photo
    really is.  Three versions of the pre-image's absorbance B are compared
    on the inner pad (more than INNER_PX px in, so the edge shadow does not
    count):

        own       B on the pre-image's own grid, before lining up;
        nearest   lined up by taking the single nearest pre-image pixel
                  (no averaging);
        bilinear  lined up as the chain does (4-pixel average).

    The scatter of each is measured from differences between neighbors
    (`_neighbor_diffs`), along rows and along columns, as a robust spread
    (`duotone._pair_noise`: 1.4826 x median absolute deviation) and as a
    plain standard deviation.  Neighbor differences cancel anything smooth,
    so what is left is what changes from one pixel to the next: camera
    noise *and* the paper's fiber texture (mostly texture, see
    shading_study step 1a).

    For comparison, 'predicted' is what bilinear averaging would do to
    camera noise alone (independent from pixel to pixel), from where each
    pixel falls between pre-image pixels.  With fy, fx the fractional row
    and column positions (scalars per pixel, 0 to 1), a difference along a
    row is scaled in spread by

        sqrt( [(1-fx)^2 + (1-2fx)^2 + fx^2] / 2  *  [(1-fy)^2 + fy^2] )

    and along a column the same with fx and fy swapped.  'predicted' is the
    root-mean-square of that over the inner pad.  Texture spans 1-2 px, so
    it is lowered less than independent noise would be.

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).

    Returns:
        {'row' or 'col': {'own', 'nearest', 'bilinear': dict of scalars
        'rsd' (robust spread), 'sd', 'n' (pairs)}, plus 'predicted'
        (scalar ratio bilinear/own for independent noise)}; and 'fy', 'fx'
        (scalars, the median fractional positions).
    """
    from scipy import ndimage as ndi
    import duotone
    f = frame(samp_no, cell, a1.PRE)
    with np.errstate(invalid="ignore", divide="ignore"):
        B_own = -np.log10(f["R"])
    R_src = np.array(f["R"], dtype=float)
    R_src[a1._hot(samp_no, a1.PRE)] = np.nan
    y_pre, x_pre = a1.pre_to_post(samp_no)
    lined = {}
    for name, order in (("nearest", 0), ("bilinear", 1)):
        R_on = ndi.map_coordinates(R_src, [y_pre, x_pre], order=order,
                                   mode="nearest")
        with np.errstate(invalid="ignore", divide="ignore"):
            lined[name] = -np.log10(R_on)
    post_rect = a1.pad_of(samp_no)
    inner_post = post_rect.mask(INNER_PX) & ~a1._hot(samp_no)
    fy, fx = y_pre % 1.0, x_pre % 1.0
    out = {"fy": float(np.median(fy[inner_post])),
           "fx": float(np.median(fx[inner_post]))}
    for axis, key in ((1, "row"), (0, "col")):
        res = {}
        for name, X, region in (("own", B_own, f["inner"]),
                                ("nearest", lined["nearest"], inner_post),
                                ("bilinear", lined["bilinear"], inner_post)):
            dd = _neighbor_diffs(X, region, axis)
            res[name] = dict(rsd=duotone._pair_noise(dd),
                             sd=float(dd.std(ddof=1)), n=int(dd.size))
        a, b = (fx, fy) if axis == 1 else (fy, fx)
        along = ((1 - a) ** 2 + (1 - 2 * a) ** 2 + a ** 2) / 2
        across = (1 - b) ** 2 + b ** 2
        res["predicted"] = float(np.sqrt(np.mean((along * across)
                                                 [inner_post])))
        out[key] = res
    return out


def lineup_noise_table(samps=POOL, cells=CELLS) -> list:
    """
    Step R4b: `lineup_noise` on every sample and channel, as a table.

    Prints, per sample and channel, the robust spread along rows on the
    pre-image's own grid, lined up by nearest pixel, and lined up as the
    chain does; the ratio bilinear / own along rows and along columns; and
    the ratio predicted for independent noise.  Then the medians.

    Args:
        samps: list of sample numbers.
        cells: list of (LED, Bayer layer).

    Returns:
        list of dicts, one per sample and channel, with the scalars printed.
    """
    rows = []
    print(f"\n{'sample':7} {'channel':14} {'own':>7} {'nearest':>8} "
          f"{'bilin':>7} | {'ratio row':>9} {'pred':>6} | {'ratio col':>9} "
          f"{'pred':>6} | {'fy':>5} {'fx':>5}")
    for s in samps:
        for cell in cells:
            try:
                v = lineup_noise(s, cell)
            except (RuntimeError, KeyError, ValueError) as e:
                print(f"{s:7} {a1.cell_name(cell):14} skipped: {e}")
                continue
            r, c = v["row"], v["col"]
            row = dict(samp=s, cell=a1.cell_name(cell),
                       own=r["own"]["rsd"], nearest=r["nearest"]["rsd"],
                       bilinear=r["bilinear"]["rsd"],
                       ratio_row=r["bilinear"]["rsd"] / r["own"]["rsd"],
                       pred_row=r["predicted"],
                       ratio_col=c["bilinear"]["rsd"] / c["own"]["rsd"],
                       pred_col=c["predicted"], fy=v["fy"], fx=v["fx"])
            rows.append(row)
            print(f"{s:7} {row['cell']:14} {row['own']:7.4f} "
                  f"{row['nearest']:8.4f} {row['bilinear']:7.4f} | "
                  f"{row['ratio_row']:9.3f} {row['pred_row']:6.3f} | "
                  f"{row['ratio_col']:9.3f} {row['pred_col']:6.3f} | "
                  f"{row['fy']:5.2f} {row['fx']:5.2f}")
    keys = ("own", "nearest", "bilinear", "ratio_row", "pred_row",
            "ratio_col", "pred_col")
    med = {k: float(np.median([r[k] for r in rows])) for k in keys}
    print(f"{'median':22} {med['own']:7.4f} {med['nearest']:8.4f} "
          f"{med['bilinear']:7.4f} | {med['ratio_row']:9.3f} "
          f"{med['pred_row']:6.3f} | {med['ratio_col']:9.3f} "
          f"{med['pred_col']:6.3f}   ({len(rows)} rows)")
    return rows


def show_lineup_noise(samps=("C1032", "C1020"), cell=CELL, figs=True,
                      savefigs=False) -> None:
    """
    Step R4a: the pre-image before and after lining up, as maps and as
    histograms of neighbor differences.

    One row per sample.  Panels: B on the pre-image's own grid; lined up
    by nearest pixel (no averaging); lined up as the chain does (4-pixel
    bilinear average) -- each minus its own median over the inner pad, on
    one tight color scale; then the histograms of the three's neighbor
    differences along rows (`_neighbor_diffs`), with their robust spreads.
    C1032 falls nearly on whole pixels (small averaging); C1020 falls
    about half-way between pixels (strong averaging).

    Args:
        samps: sample numbers, one row each.
        cell: (LED, Bayer layer).
        figs, savefigs: show / save.

    Returns:
        None.
    """
    import matplotlib.pyplot as plt
    from scipy import ndimage as ndi
    import duotone
    fig, axes = plt.subplots(len(samps), 4, figsize=(15, 4.6 * len(samps)),
                             gridspec_kw=dict(width_ratios=[1, 1, 1, 1.5]),
                             squeeze=False)
    for row, s in enumerate(samps):
        f = frame(s, cell, a1.PRE)
        R_src = np.array(f["R"], dtype=float)
        R_src[a1._hot(s, a1.PRE)] = np.nan
        y_pre, x_pre = a1.pre_to_post(s)
        inner_post = a1.pad_of(s).mask(INNER_PX) & ~a1._hot(s)
        with np.errstate(invalid="ignore", divide="ignore"):
            imgs = [("own grid", -np.log10(f["R"]), f["inner"])]
            for name, order in (("nearest pixel", 0), ("bilinear", 1)):
                imgs.append((name, -np.log10(ndi.map_coordinates(
                    R_src, [y_pre, x_pre], order=order, mode="nearest")),
                    inner_post))
        fy = float(np.median((y_pre % 1.0)[inner_post]))
        fx = float(np.median((x_pre % 1.0)[inner_post]))
        bins = np.linspace(-0.03, 0.03, 61)
        for k, (name, X, region) in enumerate(imgs):
            ax = axes[row, k]
            ry, cx = _box(region, 1)
            show = np.where(region, X - np.nanmedian(X[region]), np.nan)
            ax.imshow(show[ry, cx], cmap="RdBu_r", vmin=-0.02, vmax=0.02,
                      interpolation="nearest")
            ax.set_xticks([])
            ax.set_yticks([])
            dd = _neighbor_diffs(X, region, 1)
            rsd = duotone._pair_noise(dd)
            ax.set_title(f"{s}: B, {name}\nrobust spread {rsd:.4f}",
                         fontsize=9.5)
            axes[row, 3].hist(dd, bins=bins, histtype="step", lw=1.4,
                              label=f"{name}: {rsd:.4f}")
        axes[row, 3].set_title(f"{s}: neighbor differences along rows, /√2"
                               f"\n(fractional position: row {fy:.2f}, "
                               f"column {fx:.2f})", fontsize=9.5)
        axes[row, 3].set_xlabel("absorbance")
        axes[row, 3].set_ylabel("pairs")
        axes[row, 3].legend(fontsize=8.5)
    fig.suptitle(f"{a1.cell_name(cell)} pre-image, inner pad: lining up by "
                 f"a 4-pixel average lowers the pixel-to-pixel scatter.  "
                 f"Maps: each minus its median, ±0.02 (bigger = darker)",
                 fontsize=11)
    fig.tight_layout()
    _finish(fig, "R4a", f"lineup_{a1.cell_name(cell)}", figs, savefigs)


def show_lineup_ratio(rows, figs=True, savefigs=False) -> None:
    """
    Step R4b: measured against predicted lowering, every sample and channel.

    Args:
        rows: list from `lineup_noise_table`.
        figs, savefigs: show / save.

    Returns:
        None.
    """
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 6.5))
    ax.plot([0.3, 1.0], [0.3, 1.0], color="0.6", lw=1, label="measured = "
            "predicted")
    ax.scatter([r["pred_row"] for r in rows], [r["ratio_row"] for r in rows],
               s=18, label="differences along rows")
    ax.scatter([r["pred_col"] for r in rows], [r["ratio_col"] for r in rows],
               s=18, marker="^", label="differences along columns")
    ax.set_xlabel("predicted for independent pixel noise")
    ax.set_ylabel("measured: robust spread, bilinear / own grid")
    ax.set_title(f"Pre-image inner pad, {len(rows)} sample-channel pairs: "
                 f"how much lining up\nlowers the pixel-to-pixel scatter "
                 f"(1 = not at all)", fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    _finish(fig, "R4b", "lineup_ratio", figs, savefigs)


# ── for the report: the white reference, one row, a worked example ───

def show_white(samp_no=SAMP, cell=CELL, figs=True, savefigs=False) -> dict:
    """
    Step R3d: the scalar white reference R_white against the pre-image pad.

    R_white (scalar) is the median of the pre-image's R over its pad
    eroded `a1.ERODE` px (`analysis_1.pre_white`).  The chain divides
    every pixel by it.  This figure shows how far each pre-image pad
    pixel is from it, as

        A_pre_own = -log10(R_pre / R_white)    array, on the pre-image's
                                               own grid (not registered)

    0 means "exactly R_white"; +0.04 means about 9 % darker.
    Left: the map, pad zoom, with the eroded region (where the median is
    taken) outlined.  Right: four histograms stacked, sharing the x axis,
    by depth into the pad (distance from its edge): the eroded region
    (> ERODE px), 2-ERODE px, 1-2 px and the outermost pixel (< 1 px).

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).
        figs, savefigs: show / save.

    Returns:
        {band name: dict of scalars 'n', 'median', 'mean', 'sd', 'p95',
        'max' (A)}, plus 'R_white' (scalar) and 'mean_over_median', the
        mean of R over the eroded region divided by R_white (scalar).
    """
    import matplotlib.pyplot as plt
    f = frame(samp_no, cell, a1.PRE)
    R_white = a1.pre_white(samp_no, cell)[0]
    rect = a1.pad_of(samp_no, a1.PRE)
    with np.errstate(invalid="ignore", divide="ignore"):
        A = -np.log10(f["R"] / R_white)
    pad = f["pad"] & np.isfinite(A)
    depth = rect.depth
    used = pad & rect.mask(a1.ERODE)
    bands = [(f"more than {a1.ERODE} px in (where R_white is taken)", used),
             (f"2 to {a1.ERODE} px in", pad & ~used & (depth > 2)),
             ("1 to 2 px in", pad & (depth > 1) & (depth <= 2)),
             ("outermost pixel (less than 1 px in)", pad & (depth <= 1))]
    out = dict(R_white=R_white,
               mean_over_median=float(np.mean(f["R"][used]) / R_white))
    fig = plt.figure(figsize=(12.5, 8))
    gs = fig.add_gridspec(4, 2, width_ratios=[1, 1.3])
    ax = fig.add_subplot(gs[:, 0])
    ry, cx = _box(f["pad"], 3)
    im = ax.imshow(np.where(pad, A, np.nan)[ry, cx], cmap="RdBu_r",
                   vmin=-0.08, vmax=0.08, interpolation="nearest")
    # The outline is the eroded rectangle itself; hot pixels are not cut
    # out of it, or each would draw as a small diamond.
    ax.contour(rect.mask(a1.ERODE)[ry, cx].astype(float), levels=[0.5],
               colors="k", linewidths=0.9)
    fig.colorbar(im, ax=ax, shrink=0.8,
                 label=r"$\mathbf{B}_\mathrm{own} - b_w = "
                       r"-\log_{10}(\mathbf{S}_\mathrm{own} / "
                       r"R_\mathrm{white})$"
                       "\n0 = $R_\\mathrm{white}$; bigger = darker")
    ax.set_title("pre-image pad, own grid, against "
                 rf"$R_\mathrm{{white}}$ = {R_white:.4f}" "\n"
                 r"black line: the region $R_\mathrm{white}$ is the median of",
                 fontsize=10)
    ax.set_xlabel("column, layer px")
    ax.set_ylabel("row, layer px")
    bins = np.arange(-0.04, 0.25, 0.002)
    first = None
    for i, (name, m) in enumerate(bands):
        axh = fig.add_subplot(gs[i, 1], sharex=first)
        first = first or axh
        v = A[m]
        st = dict(n=int(v.size), median=float(np.median(v)),
                  mean=float(v.mean()), sd=float(v.std(ddof=1)),
                  p95=float(np.percentile(v, 95)), max=float(v.max()))
        out[name] = st
        axh.hist(v, bins=bins, color="C0", alpha=0.6)
        axh.axvline(0, color="k", lw=1, label=r"$R_\mathrm{white}$")
        axh.axvline(st["median"], color="C3", ls="--", lw=1,
                    label="this band's median")
        axh.text(0.99, 0.9, f"n = {st['n']}\nmedian {st['median']:+.4f}\n"
                 f"sd {st['sd']:.4f}\n95th pct {st['p95']:+.4f}",
                 transform=axh.transAxes, ha="right", va="top", fontsize=8.5,
                 family="monospace",
                 bbox=dict(boxstyle="round", fc="white", ec="0.7"))
        axh.set_title(name, fontsize=9.5)
        axh.set_ylabel("pixels")
        if i == 0:
            axh.legend(fontsize=8, loc="upper left")
        if i < 3:
            plt.setp(axh.get_xticklabels(), visible=False)
    axh.set_xlabel(r"$\mathbf{B}_\mathrm{own} - b_w$  (0 = "
                   r"$R_\mathrm{white}$; +0.04 is about 9 % darker)")
    fig.suptitle(f"{samp_no} {a1.cell_name(cell)} pre-image: how far each "
                 f"pad pixel is from the one white value R_white",
                 fontsize=11)
    fig.tight_layout()
    _finish(fig, "R3d", f"white_{samp_no}_{a1.cell_name(cell)}", figs,
            savefigs)
    return out


def _profile_row(d, dye_free=True) -> int:
    """
    A pad row for `show_r3_profile`.

    Args:
        d: dict from `post_pre`.
        dye_free: True for the row with the most pad pixels, all of them
            at least WHITE_GAP px from the dot; False for the row through
            the dot's middle.

    Returns:
        scalar int, the row index.
    """
    rows = np.nonzero(d["pad"].any(axis=1))[0]
    if not dye_free:
        ys = np.nonzero(d["dot"])[0]
        return int(round(ys.mean()))
    best, best_n = rows[0], -1
    for r in rows:
        on = d["pad"][r]
        if (d["dist"][r][on] >= WHITE_GAP).all() and on.sum() > best_n:
            best, best_n = r, int(on.sum())
    return int(best)


def show_r3_profile(samp_no=SAMP, cell=CELL, figs=True,
                    savefigs=False) -> dict:
    """
    Step R3e: every method along one row of the pad, pixel by pixel.

    Four panels stacked, sharing the x axis (column, layer px):
        1. a dye-free row: A_post (the post-image against R_white),
           A_pre (the pre-image lined up on it, against R_white), and
           smooth(A_pre, R3_D_PX).  The line A = 0 is R_white itself.
        2. the same row: corr, what each method takes out.
        3. the same row: A_after = A_post - corr, each method.
        4. the row through the dot's middle: A_after, each method.
    The rim (within INNER_PX of the pad's edge) is shaded gray.

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).
        figs, savefigs: show / save.

    Returns:
        Dict: 'row_free', 'row_dot' (scalar ints).
    """
    import matplotlib.pyplot as plt
    d = post_pre(samp_no, cell)
    m = r3_methods(d)
    s1 = smooth(d["A_pre"], d["pad"], R3_D_PX)
    r_free, r_dot = _profile_row(d, True), _profile_row(d, False)
    styles = {"A": ("k", "-"), "B1": ("C0", "-"), "B2": ("C9", "--"),
              "C": ("C3", "-"), "D": ("C2", "-.")}
    fig, axes = plt.subplots(4, 1, figsize=(12, 14), sharex=True)

    # Panels 1-2 show A, B, B~ and B^ themselves (against the gray chips,
    # no R_white): the code's arrays are relative to b_w, so b_w is added
    # back.  The gray line there is b_w, the single white of method A.
    b_w = -float(np.log10(d["R_white"]))

    def shade(ax, r, level=0.0, label=None):
        cols = np.nonzero(d["pad"][r])[0]
        for c in cols[d["rim"][r][cols]]:
            ax.axvspan(c - 0.5, c + 0.5, color="0.88", lw=0)
        ax.axhline(level, color="0.5", lw=0.7, label=label)
        ax.grid(alpha=0.3)
        return cols

    wline = r"$b_w = -\log_{10} R_\mathrm{white}$"
    cols = shade(axes[0], r_free, b_w, wline)
    for A, name, st in ((d["A_post"], r"$\mathbf{A}$ (post-image)",
                         ("C1", "-")),
                        (d["A_pre"], r"$\mathbf{B}$ (pre-image, lined up)",
                         ("C4", "-")),
                        (s1, rf"$\widetilde{{\mathbf{{B}}}}$ ($\sigma$ = "
                             rf"{R3_D_PX:g} px)", ("C4", ":"))):
        axes[0].plot(cols, A[r_free, cols] + b_w, color=st[0], ls=st[1],
                     marker=".", lw=1.2, label=name)
    axes[0].set_title(f"1. row {r_free} (dye-free): the two photos in "
                      f"absorbance, against the gray chips", fontsize=10)
    for i, (key, r, title, off, level, lab) in enumerate(
            (("corr", r_free, f"2. row {r_free}: "
              r"$\widehat{\mathbf{B}}$, what each method takes out",
              b_w, b_w, wline),
             ("after", r_free, f"3. row {r_free}: "
              r"$\mathbf{C} = \mathbf{A} - \widehat{\mathbf{B}}$",
              0.0, 0.0, None),
             ("after", r_dot, f"4. row {r_dot} (through the dot): "
              r"$\mathbf{C} = \mathbf{A} - \widehat{\mathbf{B}}$",
              0.0, 0.0, None)), 1):
        cols = shade(axes[i], r, level, lab)
        for name, v in m.items():
            color, ls = styles[name]
            axes[i].plot(cols, v[key][r, cols] + off, color=color, ls=ls,
                         marker=".", ms=3, lw=1.1, label=v["what"])
        axes[i].set_title(title, fontsize=10)
    for i, ax in enumerate(axes):
        ax.set_ylabel("absorbance (bigger = darker)")
        ax.legend(fontsize=8, loc="upper left" if i == 3 else "upper center",
                  ncol=1 if i == 3 else 3)
    axes[-1].set_xlabel("column, layer px   (gray band: the rim, within "
                        f"{INNER_PX:g} px of the pad's edge)")
    fig.suptitle(f"{samp_no} {a1.cell_name(cell)} post-image divided by "
                 f"the pre-image four ways, along one row", fontsize=11)
    fig.tight_layout()
    _finish(fig, "R3e", f"profile_{samp_no}_{a1.cell_name(cell)}", figs,
            savefigs)
    return dict(row_free=r_free, row_dot=r_dot)


def r3_example(samp_no=SAMP, cell=CELL) -> dict:
    """
    The worked example for the report: two dye-free pixels, every method.

    One pixel deep in the dye-free inner pad (the one at least 8 px from
    the pad's edge nearest the middle of that region's pixel list) and one
    in the outermost pixel of the dye-free rim (likewise).

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).

    Returns:
        {'inner' or 'rim': dict with 'pixel' (row, col), 'depth', and the
        scalars 'R_post', 'R_pre', 'R_white', 'A_post', 'A_pre',
        'smooth1' (smooth(A_pre, R3_D_PX)), and per method {'corr',
        'after', 'err'}}; plus 'level' {method: scalar}.
    """
    d = post_pre(samp_no, cell)
    m = r3_methods(d)
    s1 = smooth(d["A_pre"], d["pad"], R3_D_PX)
    depth = a1.pad_of(samp_no).depth
    out = {"level": {k: v["level"] for k, v in m.items()}}
    for name, region in (("inner", d["free"] & (depth >= 8)),
                         ("rim", d["free"] & (depth <= 1))):
        ys, xs = np.nonzero(region)
        p = (int(ys[len(ys) // 2]), int(xs[len(xs) // 2]))
        e = dict(pixel=p, depth=float(depth[p]), R_white=d["R_white"],
                 A_post=float(d["A_post"][p]), A_pre=float(d["A_pre"][p]),
                 smooth1=float(s1[p]))
        e["R_post"] = d["R_white"] * 10 ** -e["A_post"]
        e["R_pre"] = d["R_white"] * 10 ** -e["A_pre"]
        for k, v in m.items():
            e[k] = dict(corr=float(v["corr"][p]), after=float(v["after"][p]),
                        err=float(v["err"][p]))
        out[name] = e
    return out


def tex_figs(samp_no=SAMP, cell=CELL) -> None:
    """
    Write every figure that tex/shading_removal.tex uses, as PDF, to
    tex/figs/shading-<label>.pdf (R2a, R3a, R3b, R3d, R3e, R4a, R4b), and
    print the numbers the report quotes (`r3_numbers`, `show_white`,
    `r3_example`, `lineup_noise`, `lineup_noise_table`).

    Args:
        samp_no: scalar str.
        cell: (LED, Bayer layer).

    Returns:
        None.
    """
    global TEX_FIGS
    TEX_FIGS = SP.parent.parent / "tex" / "figs"
    try:
        show_r2_maps(samp_no, cell, figs=False)
        show_white_out = show_white(samp_no, cell, figs=False)
        show_r3_maps(samp_no, cell, figs=False)
        nums = show_r3_sorted(samp_no, cell, figs=False)
        rows = show_r3_profile(samp_no, cell, figs=False)
        show_lineup_noise(cell=cell, figs=False)
        print("\nR4a/R4b: lining up and pixel-to-pixel scatter")
        v = lineup_noise(samp_no, cell)
        for key in ("row", "col"):
            print(f"  {samp_no} along {key}s: " + ", ".join(
                f"{n} {v[key][n]['rsd']:.4f}" for n in
                ("own", "nearest", "bilinear"))
                + f"; predicted ratio {v[key]['predicted']:.3f}")
        show_lineup_ratio(lineup_noise_table(), figs=False)
    finally:
        TEX_FIGS = None
    print("\nR3d:", {k: v for k, v in show_white_out.items()})
    print("R3e rows:", rows)
    for (name, region), v in nums.items():
        print(f"  {name:3} {region:15} " + " ".join(
            f"{k} {v[k]:+.4f}" for k in ("mean", "sd", "rsd", "sd_s",
                                         "sd_f", "p1", "p99")))
    ex = r3_example(samp_no, cell)
    print("levels:", {k: round(v, 4) for k, v in ex["level"].items()})
    for name in ("inner", "rim"):
        e = ex[name]
        print(f"{name}: pixel {e['pixel']} depth {e['depth']:.2f} "
              f"R_post {e['R_post']:.4f} R_pre {e['R_pre']:.4f} R_white "
              f"{e['R_white']:.4f} A_post {e['A_post']:+.4f} A_pre "
              f"{e['A_pre']:+.4f} smooth1 {e['smooth1']:+.4f}")
        for k in ("A", "B1", "B2", "C", "D"):
            print(f"   {k:3} corr {e[k]['corr']:+.4f} after "
                  f"{e[k]['after']:+.4f} err {e[k]['err']:+.4f}")


# ── run ────────────────────────────────────────────────────────────────

def run():
    """
    The procedural steps, each behind an `if 1:` / `if 0:` guard.  Edit
    the guards (and any arguments) to choose what runs.

    Args:
        None.

    Returns:
        None.  Figures are written to fig/ and tables printed.
    """

    # R1a. Maps of the pre-image's brightness, before and after the
    #      gray-chip polynomial.
    if 0:
        print("\n=== step R1a: show_gray_maps")
        show_gray_maps(figs=True, savefigs=True)

    # R1b. Histogram and column profiles of the pad, before and after.
    if 0:
        print("\n=== step R1b: show_gray_hist")
        out = show_gray_hist(figs=True, savefigs=True)
        for k, v in out.items():
            print(f"  {k:13} spread {v['spread']:.4f}  trend "
                  f"{v['trend']:.4f}  (log10; n={v['n']})")

    # R1d. The pad's histogram split into inner pad and rim, with normal
    #      curves fitted to the "after" values.
    if 0:
        print("\n=== step R1d: show_split_hist")
        out = show_split_hist(figs=True, savefigs=True)
        for k in ("inner", "rim"):
            v = out[k]
            print(f"  {k:5} n={v['n']:5d} mean {v['mean']:+.4f} sd "
                  f"{v['sd']:.4f} median {v['median']:+.4f} rsd "
                  f"{v['rsd']:.4f} skew {v['skew']:+.2f} kurt "
                  f"{v['kurt']:+.2f} out3 {100 * v['out3']:.2f}% "
                  f"ks_D {v['ks_D']:.3f}")

    # R1c. The same numbers for all 18 pre-images, six channels.
    if 0:
        print("\n=== step R1c: gray_table")
        gray_table()

    # R2a. Maps: the post-image, the pre-image lined up on it, and the
    #      post-image divided by the pre-image pixel by pixel (in A).
    if 0:
        print("\n=== step R2a: show_r2_maps")
        show_r2_maps(figs=False, savefigs=True)

    # R2b. Histograms by region (whole pad, dyed, dye-free inner, rim).
    if 0:
        print("\n=== step R2b: show_r2_hist")
        show_r2_hist(figs=False, savefigs=True)

    # R2c. The numbers: mean and spread before, pre, after, and how alike
    #      the two photos' patterns are.
    if 0:
        print("\n=== step R2c: r2_numbers")
        for name, v in r2_numbers(post_pre()).items():
            print(f"  {name:22} n={v['n']:5d}  mean post {v['mean_post']:+.4f}"
                  f" pre {v['mean_pre']:+.4f} flat {v['mean_flat']:+.4f} | sd"
                  f" post {v['sd_post']:.4f} pre {v['sd_pre']:.4f} flat "
                  f"{v['sd_flat']:.4f} (if unrelated {v['if_unrelated']:.4f})"
                  f"  r {v['r']:+.3f}")

    # R3a. Maps of what each of the four methods takes out, its error,
    #      the error's smooth part and its fine part.
    if 1:
        print("\n=== step R3a: show_r3_maps")
        for name, v in show_r3_maps(figs=False, savefigs=True).items():
            print(f"  {name:3} weight on smooth {v['gamma']:.3f}, on fine "
                  f"{v['beta']:.3f}  level {v['level']:+.4f}")
        print(f"  (both weights fitted freely: smooth {v['gamma_free']:.3f}, "
              f"fine {v['beta_free']:.3f}; not used)")

    # R3b, R3c. Sorted error and pixel values; the numbers by region.
    if 1:
        print("\n=== step R3b/R3c: show_r3_sorted, r3_numbers")
        nums = show_r3_sorted(figs=False, savefigs=True)
        print(f"  {'method':6} {'region':15} {'n':>5} {'mean A':>8} "
              f"{'sd':>7} {'rob sd':>7} {'smooth':>7} {'fine':>7} "
              f"{'p1':>8} {'p99':>8}")
        for (name, region), v in nums.items():
            print(f"  {name:6} {region:15} {v['n']:5d} {v['mean']:+8.4f} "
                  f"{v['sd']:7.4f} {v['rsd']:7.4f} {v['sd_s']:7.4f} "
                  f"{v['sd_f']:7.4f} {v['p1']:+8.4f} {v['p99']:+8.4f}")

    a1.close_all()


if __name__ == "__main__":
    run()
