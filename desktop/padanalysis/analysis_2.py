"""analysis_2.py -- hand-driven look at the dark frames.

Companion to `analysis_1.py`, same style: documented methods here, the
procedure hard-coded in the `__main__` block at the bottom and edited for
each run.  Read-only against `captures/*.h5`; the only files produced are
PNGs in `fig/`.

Why a separate module for the darks.  `probe.Capture.layer` returns
`lit - dark_level` already (one number per Bayer layer per sweep since
2026-09-27; see `capture_h5.dark_level`), so the dark never appears on its own in the
`analysis_1` chain.  Its spatial structure and its spread set the floor a
`Delta(K/S)` can be trusted above (see
`docs/reference_framework_preimage_and_chips.md`, "Dark-Frame Spatial
Structure"), and that floor is easier to judge by looking at the frame than
by reading a single std number out of `dark_groups()`.

Coordinates: bound-crop Bayer-layer, 130 x 183 for bound roi 39 -- the same
grid `analysis_1` works in.  One dark frame is shared by every LED at the
same exposure (`probe.Capture._dark_link` keys on microseconds), so a dark
is identified here by its exposure, not by an LED.
"""
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.special import erf
#from layermodel_repo import dark

SP = Path(__file__).resolve().parent
sys.path.insert(0, str(SP))

from probe import BAYER, Capture, sections            # noqa: E402

FIG = SP / "fig"


# -- helpers ---------------------------------------------------------------
def moving_average(x, window):
    """ 
    moving window average, with endpoint detail
    
    As the end points are neared, the window shrinks to size 1
    """
    c = np.concatenate(([0], np.cumsum(x)))
    n = len(x)

    result = np.empty(n)

    half = window // 2

    for i in range(n):
        w = max(0, min(i, half, n-i-1))
        lo = max(0, i - w)
        hi = min(n, i + w + 1)
        result[i] = (c[hi] - c[lo]) / (hi - lo)

    return result

def _finish(fig, name, what, figs=True, savefigs=False):
    """Save and/or show one figure.  Returns the path written, or None."""
    import matplotlib.pyplot as plt
    out = None
    if savefigs:
        FIG.mkdir(parents=True, exist_ok=True)
        out = FIG / f"analysis2_{name}_{what}.png"
        fig.savefig(out, dpi=120)
        out = FIG / f"analysis2_{name}_{what}.pdf"
        fig.savefig(out, dpi=120)
        print(f"  wrote {out}")
    if figs:
        plt.show()
    else:
        plt.close(fig)
    return out


def _dark_sources(c: Capture, exposure_us: int) -> list:
    """
    Every `(led, label)` in this sweep captured at one exposure.

    A dark frame is shared by all of them, so any one is a route to it
    through `Capture.darklayer`.  More than one is returned because a given
    `(led, label)` may lack a dark link for this layer; `darklayer` reads
    the linked dark directly and returns None when it is not stored.
    """
    out = []
    for led in c.leds:
        for label in c.labels(led):
            if c.exposure_us(led, label) == exposure_us:
                out.append((led, label))
    return out


def _dark_stack(samps, ch, sec=""):
    """
    Every stored dark frame for one channel, across a list of captures, in
    true acquisition order.

    This is the building block for anything that asks a *temporal*
    question -- drift, correlation, stability -- because it keeps each
    frame's real capture timestamp instead of collapsing straight to a mean.
    (`dark_analysis2`'s own stack does not preserve order: it sorts each
    pixel's values along the stack axis, which leaves the per-pixel mean and
    std untouched but destroys the sequence a timing question needs.)

    Args:
        samps: Capture names without the .h5.
        ch: Bayer layer -- 'R', 'Gr', 'Gb' or 'B'.
        sec: '' for the measurement sweep, 'pre' for the pre-image.

    Returns:
        `(stack, meta)`.  `stack` is (rows, cols, n) float64, axis 2 in
        increasing `captured_dt` order.  `meta` is a same-length list of
        dicts, one per `stack[..., k]`: `samp_no`, `exposure_us`, `dt` (a
        `datetime`), and `t_sec` (seconds since the earliest frame in the
        whole list -- one common time axis across every file given).

    Example:
        >>> stack, meta = _dark_stack(["C1017", "C1018"], "B")
        ...                                              # doctest: +SKIP
        >>> stack.shape[2] == len(meta)                  # doctest: +SKIP
        True
    """
    frames, meta = [], []
    for samp_no in samps:
        c = Capture(samp_no, sec)
        try:
            for name, info in c.dark_groups().items():
                got = info["layers"].get(ch)
                if got is None or got[1] is None:
                    continue          # dropped by the keep screen: no array
                us = info["exposure_us"]
                dark = None
                for lsrc, lab in _dark_sources(c, us):
                    dark = c.darklayer(lsrc, lab, ch)
                    if dark is not None:
                        break
                if dark is None:
                    continue
                dt = datetime.strptime(info["captured_dt"],
                                       "%Y-%m-%d %H:%M:%S.%f")
                frames.append(np.asarray(dark, dtype=np.float64))
                meta.append(dict(samp_no=samp_no, exposure_us=us, dt=dt))
        finally:
            c.close()
    order = sorted(range(len(meta)), key=lambda k: meta[k]["dt"])
    stack = np.stack([frames[k] for k in order], axis=2)
    meta = [meta[k] for k in order]
    t0 = meta[0]["dt"]
    for m in meta:
        m["t_sec"] = (m["dt"] - t0).total_seconds()
    return stack, meta


# -- the two methods -----------------------------------------------------

def dark_inventory(samp_no, sec="") -> list:
    """
    The dark exposures one sweep holds, in milliseconds.

    Args:
        samp_no: Capture name without the .h5, e.g. 'C1017'.
        sec: '' for the measurement sweep, 'pre' for the pre-image.

    Returns:
        Sorted list of distinct dark exposure times in ms (float, 3 dp).
        Pass one of these straight to `dark_analysis1`.

    Example:
        >>> dark_inventory("C1017")                      # doctest: +SKIP
        [1.462, 2.193, 3.29, 5.283, 7.402, 7.915, ...]
    """
    c = Capture(samp_no, sec)
    try:
        us = {info["exposure_us"] for info in c.dark_groups().values()}
    finally:
        c.close()
    return sorted(round(u / 1000.0, 3) for u in us if u)


def dark_analysis1(samp_no, exposure_ms, ch="B", sec="", led=None,
                   figs=True, savefigs=True):
    """
    One dark frame, as an image and as its sorted value list.

    Reads the dark layer for the requested exposure through
    `probe.Capture.darklayer` (any LED at that exposure reaches the same
    shared frame), then draws two panels side by side:

      * left  -- the frame itself, `imshow`, robust colour limits, so any
        fixed-pattern structure is visible;
      * right -- `numpy.sort(frame.ravel())`, the same pixels laid out from
        darkest to brightest.  A flat middle with short ramps at each end is
        read noise on a uniform offset; a long slope, steps or spikes are
        structure -- hot pixels at the top, cold or dead pixels at the
        bottom.

    Args:
        samp_no: Capture name without the .h5.
        exposure_ms: A value from `dark_inventory(samp_no, sec)`.  The
            nearest stored dark exposure is used and a note is printed if it
            is not an exact match.
        ch: Bayer layer -- 'R', 'Gr', 'Gb' or 'B'.
        sec: '' for the measurement sweep, 'pre' for the pre-image.
        led: Force the LED whose group is read.  None tries each LED at that
            exposure until one yields the frame.
        figs: Show the figure.
        savefigs: Write `fig/analysis2_<samp_no>[_<sec>]_dark_<ms>ms_<ch>.png`.

    Returns:
        The dark frame as a float64 ndarray (rows, cols), or None if no LED
        at that exposure could be read.

    Example:
        >>> d = dark_analysis1("C1017", 11.888, ch="B")  # doctest: +SKIP
    """
    import matplotlib.pyplot as plt

    c = Capture(samp_no, sec)
    try:
        groups = c.dark_groups()
        if not groups:
            print(f"  {c.label()}: no shared dark groups in this archive")
            return None
        want = exposure_ms * 1000.0
        us = min((g["exposure_us"] for g in groups.values()),
                 key=lambda u: abs(u - want))
        if abs(us - want) > 1.0:
            print(f"  note: no dark at {exposure_ms} ms; using the nearest, "
                  f"{us / 1000:.3f} ms")

        sources = ([(led, lab) for (l, lab) in _dark_sources(c, us)
                    if l == led] if led else _dark_sources(c, us))
        dark = None
        for lsrc, lab in sources:
            dark = c.darklayer(lsrc, lab, ch)
            if dark is not None:
                led_used, label_used = lsrc, lab
                break
        if dark is None:
            print(f"  {c.label()}: could not read a {ch} dark at "
                  f"{us / 1000:.3f} ms from any LED ({len(sources)} tried)")
            return None

        dark = np.asarray(dark, dtype=np.float64)
        flat = np.sort(dark.ravel())
        lo, med, hi = flat[0], float(np.median(flat)), flat[-1]
        mean, std = float(dark.mean()), float(dark.std())
        print(f"  {c.label()}  dark {us / 1000:.3f} ms  {ch}  "
              f"(via {led_used}/{label_used})   n={dark.size}   "
              f"min/med/max = {lo:.0f}/{med:.1f}/{hi:.0f}   "
              f"mean {mean:.2f}   std {std:.2f}")

        p_lo, p_hi = np.percentile(flat, [0.5, 99.5])
        fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12.5, 5.2),
                                       layout="constrained")
        im = ax0.imshow(dark, cmap="magma", vmin=p_lo,
                        vmax=max(p_hi, p_lo + 1))
        ax0.set_title(f"dark frame   (colour limits {p_lo:.0f}-{p_hi:.0f} DN)",
                      fontsize=9)
        ax0.set_xlabel("layer col")
        ax0.set_ylabel("layer row")
        fig.colorbar(im, ax=ax0, shrink=0.85, label="DN")

        ax1.plot(flat, lw=1.0, color="tab:blue")
        ax1.axhline(med, color="0.5", lw=0.8, ls="--",
                    label=f"median {med:.1f} DN")
        ax1.set_title("pixels sorted, darkest to brightest", fontsize=9)
        ax1.set_xlabel("pixel rank")
        ax1.set_ylabel("DN")
        ax1.grid(alpha=0.3)
        ax1.legend(fontsize=8, loc="upper left")

        tag = f"/{sec}" if sec else ""
        fig.suptitle(f"{samp_no}{tag}   dark {us / 1000:.3f} ms   {ch}   "
                     f"mean {mean:.2f}  std {std:.2f}  "
                     f"range {lo:.0f}-{hi:.0f} DN", fontsize=10)

        what = (f"{'_' + sec if sec else ''}_dark_{us / 1000:.3f}ms_{ch}")
        _finish(fig, samp_no, what.lstrip("_"), figs, savefigs)
        return dark
    finally:
        c.close()

def dark_analysis2(samps, ch="B", sec="",
                   figs=True, savefigs=True):
    """
    One dark frame, as an image and as its sorted value list.

    Reads all dark layers for the requested channel through
    `probe.Capture.darklayer` (any LED at that exposure reaches the same
    shared frame), then gets the average and stats per pixel.

      * left  -- the average frame, `imshow`, robust colour limits, so any
        fixed-pattern structure is visible;
      * right -- mean and stdev per pixel, sorted by mean pixel value
        To test whether there is trend per pixel. 

    Args:
        samps: list of Capture names without the .h5.
        ch: Bayer layer -- 'R', 'Gr', 'Gb' or 'B'.
        sec: '' for the measurement sweep, 'pre' for the pre-image.
        led: Force the LED whose group is read.  None tries each LED at that
            exposure until one yields the frame.
        figs: Show the figure.
        savefigs: Write `fig/analysis2_<samp_no>[_<sec>]_dark_<ms>ms_<ch>.png`.

    Returns:
        The dark frame as a float64 ndarray (rows, cols), or None if no LED
        at that exposure could be read.

    Example:
        >>> d = dark_analysis1("C1017", 11.888, ch="B")  # doctest: +SKIP
    """
    import matplotlib.pyplot as plt

    darklist = []
    for samp_no in samps:
        
        try:
            c = Capture(samp_no, sec)
            groups = c.dark_groups()
            if not groups:
                # print(f"  {c.label()}: no shared dark groups in this archive")
                return None
            uss = sorted([g["exposure_us"] for g in groups.values()])
            for us in uss:
                #print(us)
                
                # want = exposure_ms * 1000.0
                # us = min((g["exposure_us"] for g in groups.values()),
                #          key=lambda u: abs(u - want))
                # if abs(us - want) > 1.0:
                #     print(f"  note: no dark at {exposure_ms} ms; using the nearest, "
                #           f"{us / 1000:.3f} ms")
            #
                # Obtain an instance of lsrc, lab for this us, e.g. ('royal_blue', 'e0')
                # used for addressing c.darklayer
                try:
                    lsrc, lab = _dark_sources(c, us)[0] 
                    dark = c.darklayer(lsrc, lab, ch)
                    if dark is not None:
                        darklist.append(np.asarray(dark, dtype=np.float64))
                except Exception as e:
                    print(f"  {c.label()}: could not read a {ch} dark at "
                          f"{_dark_sources(c, us)}")
                    continue
        finally:
            c.close()
        
    darkstack = np.stack(darklist, axis=2)
    n = darkstack.shape[-1]
    # Sorting here (unlike `_dark_stack`) is along the stack axis, i.e. within
    # each pixel across frames -- it reorders each pixel's own n values but
    # leaves the set of values, and hence the per-pixel mean and std below,
    # unchanged. It also destroys acquisition order, which is why anything
    # asking a *temporal* question uses `_dark_stack` instead (see its
    # docstring). Kept here only because nothing below needs the order.
    darkstack.sort(axis=2)
    darkmean = np.mean(darkstack, axis=2)
    darkstd = np.std(darkstack, axis=2)

    fig, ((ax0, ax1), (ax2, ax3)) = plt.subplots(2, 2, figsize=(12.5, 11.0),
                                   layout="constrained")

    Mflat = darkmean.ravel()
    Sflat = darkstd.ravel()
    order = np.argsort(Mflat)
    Mflat_hi = (Mflat + Sflat)[order]
    Mflat_lo = (Mflat - Sflat)[order]

    print(f"  {ch}: {n} frames, {Mflat.size} pixels   "
          f"mean(mean)={Mflat.mean():.3f} DN   median(std)={np.median(Sflat):.3f} DN")

    # -- ax0: the stacked mean frame itself, robust colour limits (from the
    # mean image, not the raw stack -- the mean's own range is much narrower
    # than any single frame's, so stack-wide limits would wash it flat).
    p_lo, p_hi = np.percentile(darkmean, [0.5, 99.5])
    im = ax0.imshow(darkmean, cmap="magma", vmin=p_lo, vmax=max(p_hi, p_lo + 1))
    ax0.set_title(f"mean dark frame, N={n}   "
                  f"(colour limits {p_lo:.2f}-{p_hi:.2f} DN)", fontsize=9)
    ax0.set_xlabel("layer col")
    ax0.set_ylabel("layer row")
    fig.colorbar(im, ax=ax0, shrink=0.85, label="DN")

    # -- ax1: every pixel's mean +/- 1 stdev, sorted by mean -- the
    # figure-1 view of dark_analysis1 but per-pixel-across-frames instead of
    # per-pixel-within-one-frame: a flat band says uniform noise on a
    # uniform offset, a rising tail at the right says hot pixels.
    ax1.plot(Mflat[order], color="k", lw=1.4, label="pixel mean")
    ax1.plot(Mflat_lo, color="tab:red", lw=0.7, label="mean $\\pm$ 1 stdev")
    ax1.plot(Mflat_hi, color="tab:red", lw=0.7)
    ax1.set_title("pixel means, sorted, with own stdev band", fontsize=9)
    ax1.set_xlabel("pixel rank (sorted by mean)")
    ax1.set_ylabel("DN")
    ax1.legend(fontsize=8, loc="upper left")
    ax1.grid(alpha=0.3)

    # -- ax2: zoom on the hot-pixel tail (the top 20 by mean) -- does their
    # stdev also stand out, or just their level?
    N2 = 15
    M = 40
    Ssmoothed = moving_average(Sflat[order], window=N2)
    ax2.plot(Sflat[order][-M:], ".", color="tab:blue", ms=6, label="stdev")
    ax2.plot(Ssmoothed[-M:], "-", color="tab:red", lw=1.2,
             label=f"smoothed (window {N2})")
    ax2.set_title(f"pixel stdev, top {M} by mean (hot-pixel tail)", fontsize=9)
    ax2.set_xlabel(f"pixel rank, last {M} (sorted by mean)")
    ax2.set_ylabel("stdev (DN)")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)

    # -- ax3: the same stdev-vs-mean-rank trend over every pixel, coarsely
    # smoothed -- is the noise level itself higher for the higher-mean
    # pixels, or is it flat across the whole population?
    N3 = max(len(Sflat) // 21, 1)
    Ssmoothed = moving_average(Sflat[order], window=N3)
    ax3.plot(Sflat[order], ".", color="tab:blue", ms=2, alpha=0.4, label="stdev")
    ax3.plot(Ssmoothed, "-", color="tab:red", lw=1.4,
             label=f"smoothed (window {N3})")
    ax3.set_title("pixel stdev vs mean-rank, all pixels", fontsize=9)
    ax3.set_xlabel("pixel rank (sorted by mean)")
    ax3.set_ylabel("stdev (DN)")
    ax3.legend(fontsize=8)
    ax3.grid(alpha=0.3)

    fig.suptitle(f"{ch}  per-pixel dark statistics across {n} frames "
                 f"({darklist[0].shape[0]}x{darklist[0].shape[1]} px)",
                 fontsize=10)
    _finish(fig, "series", f"pixelstats_{ch}", figs, savefigs)
        



def dark_cdf_check(samps, ch="B", sec="", quantity="mean",
                   figs=True, savefigs=True):
    """
    Test, rather than eyeball, whether a per-pixel dark statistic is normal.

    `dark_analysis2` plots the sorted per-pixel means and they look
    Gaussian; "look Gaussian" is not a test.  This builds the same sorted
    array and compares it against a normal, term by term, by its own
    definition rather than a library call:

        empirical CDF   F_emp(x_(i)) = (i + 0.5) / N        i-th smallest of N
        normal CDF      F_thy(x)     = 1/2 [ 1 + erf( (x - mu) / (sigma*sqrt(2)) ) ]

    with `mu`, `sigma` the plain sample mean and standard deviation of the
    same data.  Plotting both against the same x-axis is a direct visual
    test; `F_emp - F_thy` is the same test made quantitative -- its largest
    magnitude is the Kolmogorov-Smirnov distance, and *where* it occurs
    (which value of x) says what kind of deviation is present: a bump in
    one tail means an outlier population (hot pixels), not a wrong sigma.

    One caution stated rather than skipped: `mu` and `sigma` are fit from
    this same sample (a Lilliefors test would be exact about it), so the
    classical Kolmogorov 5% critical value of `1.36/sqrt(N)` is optimistic
    here -- the true critical value is smaller. Treat the number below as a
    descriptive distance, not a calibrated p-value.

    Args:
        samps: Capture names without the .h5, any order.
        ch: Bayer layer.
        sec: '' measurement sweep, 'pre' pre-image.
        quantity: 'mean' -- each pixel's average dark level across the
            stack (the fixed-pattern question).  'std' -- each pixel's own
            temporal spread (whether the *noise*, not just the level, is
            uniform pixel to pixel).
        figs, savefigs: as elsewhere.

    Returns:
        dict with `mu`, `sigma`, `ks` (the max |F_emp - F_thy|), `ks_x`
        (where it occurs), and the sorted values with both CDFs, for reuse.

    Example:
        >>> out = dark_cdf_check(SAMPS, "B")             # doctest: +SKIP
        >>> out["ks"] < 0.02                             # doctest: +SKIP
        True
    """
    import matplotlib.pyplot as plt

    stack, meta = _dark_stack(samps, ch, sec)
    n = stack.shape[2]
    stat = stack.mean(axis=2) if quantity == "mean" else stack.std(axis=2, ddof=1)
    x = np.sort(stat.ravel())
    N = x.size

    mu, sigma = float(x.mean()), float(x.std(ddof=1))
    p_emp = (np.arange(N) + 0.5) / N
    z = (x - mu) / (sigma * np.sqrt(2.0))
    p_thy = 0.5 * (1.0 + erf(z))
    dev = p_emp - p_thy
    k = int(np.argmax(np.abs(dev)))
    ks, ks_x = float(abs(dev[k])), float(x[k])

    print(f"  {ch} {quantity}: N={N} pixels, {n} frames   "
          f"mu={mu:.4f}  sigma={sigma:.4f}   "
          f"max|F_emp-F_normal|={ks:.4f} at x={ks_x:.3f} DN")

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12, 5), layout="constrained")
    ax0.plot(x, p_emp, "-", color="tab:blue", lw=1.4, label="empirical CDF")
    ax0.plot(x, p_thy, "--", color="tab:red", lw=1.4,
             label=f"normal($\\mu$={mu:.3f}, $\\sigma$={sigma:.3f})")
    ax0.set_xlabel(f"per-pixel {quantity} dark level (DN)")
    ax0.set_ylabel("cumulative fraction of pixels")
    ax0.set_title("data vs fitted normal", fontsize=9)
    ax0.legend(fontsize=8)
    ax0.grid(alpha=0.3)

    ax1.plot(x, dev, color="k", lw=1.0)
    ax1.axhline(0, color="0.6", lw=0.8)
    ax1.axvline(ks_x, color="tab:red", lw=0.8, ls=":",
                label=f"max |dev| {ks:.4f} @ {ks_x:.2f} DN")
    ax1.set_xlabel(f"per-pixel {quantity} dark level (DN)")
    ax1.set_ylabel("F_emp - F_normal")
    ax1.set_title("where it departs from normal", fontsize=9)
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)

    fig.suptitle(f"{ch}  N={N} px x {n} frames   "
                 f"normal-CDF check on per-pixel {quantity}", fontsize=10)
    _finish(fig, "series", f"cdfcheck_{quantity}_{ch}", figs, savefigs)
    return dict(mu=mu, sigma=sigma, ks=ks, ks_x=ks_x, x=x,
               p_emp=p_emp, p_thy=p_thy)


def dark_stability_check(samps, ch="B", sec="", figs=True, savefigs=True):
    """
    Split-half reliability of the per-pixel fixed-pattern offset.

    Building a per-pixel dark estimator rests on one working assumption:
    that the "handful of pixels [that] have consistently higher levels"
    seen in `dark_analysis2` are a *stable* property of those pixels, not
    just this particular stack's own noise.  That is testable directly:
    split the acquisition history in half by time, compute each pixel's
    residual from the frame mean in each half separately, and correlate the
    two halves.  A real, time-invariant offset agrees between two
    independent halves; a noise artefact does not.

    Method
    ------
        r_i(k) = dark_i(k) - mean_pixels(dark(k))     frame level removed
        a_i    = mean over k in the first half of r_i(k)
        b_i    = mean over k in the second half

    `a` and `b` are two independent estimates of the same pixel property (no
    frame contributes to both).  Their Pearson correlation is the fraction
    of each half's variance that is the shared, stable part -- which makes
    it the same quantity as `estimate_dark_offset`'s shrinkage weight `B`,
    arrived at a different way (there with n = the full history, here with
    n = half of it); the two should roughly agree, correcting for that
    factor of two in n.

    Args:
        samps: Capture names, any order.
        ch: Bayer layer.
        sec: '' measurement sweep, 'pre' pre-image.
        figs, savefigs: as elsewhere.

    Returns:
        `(r, a, b)` -- the Pearson correlation and the two half-history
        offset maps (rows, cols).

    Example:
        >>> r, a, b = dark_stability_check(SAMPS, "B")   # doctest: +SKIP
    """
    import matplotlib.pyplot as plt

    stack, meta = _dark_stack(samps, ch, sec)
    n = stack.shape[2]
    frame_mean = stack.mean(axis=(0, 1))
    resid = stack - frame_mean[None, None, :]

    half = n // 2
    a = resid[:, :, :half].mean(axis=2)
    b = resid[:, :, half:].mean(axis=2)
    af, bf = a.ravel(), b.ravel()
    r = float(np.corrcoef(af, bf)[0, 1])

    print(f"  {ch}: {n} frames ({half}/{n - half} split)   "
          f"split-half correlation of the per-pixel offset: r={r:.3f}   "
          f"({meta[0]['samp_no']} .. {meta[-1]['samp_no']})")

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(11.5, 5.2),
                                   layout="constrained")
    lim = float(np.percentile(np.abs(np.concatenate([af, bf])), 99.5))
    ax0.scatter(af, bf, s=2, alpha=0.15, color="tab:blue")
    ax0.plot([-lim, lim], [-lim, lim], "--", color="0.5", lw=1)
    ax0.set_xlim(-lim, lim)
    ax0.set_ylim(-lim, lim)
    ax0.set_xlabel("offset, first half of history (DN)")
    ax0.set_ylabel("offset, second half of history (DN)")
    ax0.set_title(f"split-half agreement   r={r:.3f}", fontsize=9)
    ax0.set_aspect("equal")
    ax0.grid(alpha=0.3)

    full = resid.mean(axis=2).ravel()
    order = np.argsort(full)
    ax1.plot(full[order], color="k", lw=1.2, label="full-history offset")
    ax1.plot(af[order], ".", color="tab:blue", ms=1.5, alpha=0.4,
             label="first half")
    ax1.plot(bf[order], ".", color="tab:orange", ms=1.5, alpha=0.4,
             label="second half")
    ax1.set_xlabel("pixel rank (by full-history offset)")
    ax1.set_ylabel("offset (DN)")
    ax1.set_title("both halves against the full-history estimate", fontsize=9)
    ax1.legend(fontsize=7, markerscale=4)
    ax1.grid(alpha=0.3)

    fig.suptitle(f"{ch}  per-pixel offset stability, {n} frames", fontsize=10)
    _finish(fig, "series", f"stability_{ch}", figs, savefigs)
    return r, a, b


def dark_temporal_check(samps, ch="B", sec="", n_perm=2000, seed=0,
                        figs=True, savefigs=True):
    """
    Whether the frame-level dark offset drifts with time -- two of your
    three timescales.

    * **Scale 1, seconds, within one file** -- the frame mean against
      elapsed time since that file's first dark.
    * **Scale 3, minutes to days, between files** -- each file's own mean
      dark level against real elapsed time since the first file given,
      **restricted to the exposures every file has in common** (a ladder
      file stores more distinct exposures than a single-rung one; comparing
      full-file averages would confound a real drift with a change in which
      exposures went into the average).

    **Scale 2 (dark to lit, minutes, within one file) is not directly
    testable on this series.**  It needs two darks bracketing the same lit
    frame; these archives keep only the "after" batch -- the before/after
    pairing `docs/reference_framework_preimage_and_chips.md` used belongs to
    pre-2026-08 archives.  Scale 1's measured rate, extrapolated to the ~1
    minute a sweep spans, is the best stand-in available and is reported as
    an extrapolation, not a measurement.

    Method (both scales): fit the frame mean against elapsed time by
    ordinary least squares, from the formula rather than a library call --

        slope = cov(t, y) / var(t)

    -- then judge it against the null of no trend by a permutation test:
    shuffle the frame means against the fixed time values `n_perm` times,
    refit the same slope each time, and see where the observed slope falls
    in that shuffled distribution.  This avoids assuming the frame-mean
    residuals are themselves normal, which is a separate question from the
    one `dark_cdf_check` answers (that one is about the per-pixel offset,
    not the frame-to-frame level).

    Args:
        samps: Capture names, any order.
        ch: Bayer layer.
        sec: '' measurement sweep, 'pre' pre-image.
        n_perm: Permutations for the significance check.
        seed: RNG seed, for a reproducible p-value.
        figs, savefigs: as elsewhere.

    Returns:
        `{"within": (slope_DN_per_s, p), "between": (slope_DN_per_hr, p)}`.

    Example:
        >>> dark_temporal_check(SAMPS, "B")              # doctest: +SKIP
    """
    import matplotlib.pyplot as plt

    stack, meta = _dark_stack(samps, ch, sec)
    fmean = stack.mean(axis=(0, 1))
    t = np.array([m["t_sec"] for m in meta])
    files = [m["samp_no"] for m in meta]

    def slope_of(tt, yy):
        tt = tt - tt.mean()
        return float(np.sum(tt * (yy - yy.mean())) / np.sum(tt * tt))

    def perm_p(tt, yy, rng):
        obs = slope_of(tt, yy)
        null = np.array([slope_of(tt, rng.permutation(yy))
                         for _ in range(n_perm)])
        return obs, float((np.sum(np.abs(null) >= abs(obs)) + 1) / (n_perm + 1))

    rng = np.random.default_rng(seed)

    first_file = files[0]
    idx = [k for k, f in enumerate(files) if f == first_file]
    t1, y1 = t[idx] - t[idx[0]], fmean[idx]
    slope1, p1 = perm_p(t1, y1, rng)

    exp = np.array([m["exposure_us"] for m in meta])
    uniq = list(dict.fromkeys(files))
    by_file_exp = {u: set(exp[[k for k in range(len(files))
                              if files[k] == u]]) for u in uniq}
    common = set.intersection(*by_file_exp.values())
    if common:
        keep = np.array([e in common for e in exp])
        note = f"{len(common)} exposures common to all {len(uniq)} files"
    else:
        keep = np.ones(len(exp), dtype=bool)
        note = ("no exposure is common to every file -- using each file's "
                "full average; treat this scale with more caution")
    groups = {u: [k for k in range(len(files))
                 if files[k] == u and keep[k]] for u in uniq}
    t3 = np.array([t[groups[u]].mean() for u in uniq])
    y3 = np.array([fmean[groups[u]].mean() for u in uniq])
    slope3, p3 = perm_p(t3, y3, rng)

    print(f"  scale 1 (within {first_file}, {t1[-1]:.1f} s span): "
          f"slope {slope1 * 1000:+.4f} DN/1000s   permutation p={p1:.3f}")
    print(f"  scale 3 (between {len(uniq)} files, "
          f"{t3[-1] / 3600:.1f} h span, {note}): "
          f"slope {slope3 * 3600:+.4f} DN/hour   permutation p={p3:.3f}")

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12, 5), layout="constrained")
    ax0.plot(t1, y1, "o-", ms=4, color="tab:blue")
    ax0.set_xlabel("seconds since this file's first dark")
    ax0.set_ylabel("frame mean (DN)")
    ax0.set_title(f"scale 1: within {first_file}   "
                 f"slope {slope1 * 1000:+.3f} DN/1000s  p={p1:.3f}",
                 fontsize=9)
    ax0.grid(alpha=0.3)

    ax1.plot(t3 / 3600, y3, "o-", ms=5, color="tab:orange")
    for xi, yi, lab in zip(t3 / 3600, y3, uniq):
        ax1.annotate(lab, (xi, yi), fontsize=6, xytext=(2, 2),
                     textcoords="offset points")
    ax1.set_xlabel("hours since the first file")
    ax1.set_ylabel("file mean dark level (DN)")
    ax1.set_title(f"scale 3: between files   "
                 f"slope {slope3 * 3600:+.4f} DN/hour  p={p3:.3f}",
                 fontsize=9)
    ax1.grid(alpha=0.3)

    fig.suptitle(f"{ch}  frame-level dark drift, two timescales  "
                 f"(scale 2, dark-to-lit, is not testable on this series)",
                 fontsize=10)
    _finish(fig, "series", f"temporal_{ch}", figs, savefigs)
    return {"within": (slope1, p1), "between": (slope3, p3)}


def dark_exposure_check(samps, ch="B", sec="", quantity="mean", n_perm=2000,
                        seed=0, figs=True, savefigs=True):
    """
    Whether the dark level (or its spread) depends on exposure duration.

    This is the question behind "do I need a whole exposure ladder of
    darks, or would one -- or one low and one high -- do?", and it is a
    different question from `dark_temporal_check`'s scale 1, even though
    both look at "does the dark change across one file's dark sequence."
    Checked directly: within every archive, `captured_dt` is monotone in
    `exposure_us` -- the darks are taken in strictly increasing exposure
    order -- so *inside one file*, elapsed time and exposure duration are
    the same variable relabelled, and no test run on one file's sequence
    alone can tell you which of the two it is responding to (scale 1's
    "no significant trend" was silent on this, not an answer to it).

    They can only be separated where the *pairing* between `exposure_us`
    and elapsed/sequence position differs across files -- and it does: a
    ladder file visits 22 exposures over ~11.5 s; a single-rung file visits
    8 of the same exposure values over a much shorter span, at different
    relative positions. Comparing many files at the *same* exposure_us, in
    those different time-contexts, is the natural experiment this dataset
    actually offers.

    Method: treat `exposure_us` as a factor and compare its between-group
    spread to its within-group spread -- one-way ANOVA, but judged by a
    permutation null (shuffle the exposure labels across frames, same group
    sizes, recompute F, `n_perm` times) rather than the textbook F-tail,
    because the within-group counts here are small (9 files or fewer per
    exposure):

        F = ( SS_between / df_between ) / ( SS_within / df_within )

    Two independent questions, one call each:

      quantity="mean"  the per-frame spatial mean -- tests for dark
                       current (charge accumulating with integration time).
      quantity="std"   the per-frame spatial std -- tests for exposure-
                       dependent *noise* (e.g. shot noise riding on a real
                       dark current), separate from the level question.

    The **effect size** printed alongside F and p is what actually answers
    "can I neglect this": the largest exposure-group-mean difference,
    compared with the within-group (file-to-file) scatter already measured
    by `dark_temporal_check`'s scale 3.  A significant F on a difference far
    below that scatter is a real but operationally negligible effect; report
    both, not just the p-value.

    Args:
        samps: Capture names, any order.
        ch: Bayer layer.
        sec: '' measurement sweep, 'pre' pre-image.
        quantity: 'mean' or 'std', see above.
        n_perm: Permutations for the significance check.
        seed: RNG seed, for a reproducible p-value.
        figs, savefigs: as elsewhere.

    Returns:
        dict: `F`, `p`, `exposures_us` (the group values used -- only those
        common to every file, for the same reason `dark_temporal_check`
        restricts scale 3), `group_mean`, `group_sd`, `group_n`, and
        `effect_range` (max - min of `group_mean`, DN).

    Example:
        >>> out = dark_exposure_check(SAMPS, "B", quantity="mean")
        ...                                              # doctest: +SKIP
    """
    import matplotlib.pyplot as plt

    stack, meta = _dark_stack(samps, ch, sec)
    y_all = stack.mean(axis=(0, 1)) if quantity == "mean" \
        else stack.std(axis=(0, 1))
    exp_all = np.array([m["exposure_us"] for m in meta])
    files_all = [m["samp_no"] for m in meta]

    # restrict to exposures every file holds, so no file's absence from a
    # group is mistaken for that group being systematically different
    by_file = {f: set(exp_all[[k for k in range(len(files_all))
                              if files_all[k] == f]]) for f in set(files_all)}
    common = sorted(set.intersection(*by_file.values()))
    keep = np.isin(exp_all, common)
    y, exp = y_all[keep], exp_all[keep]

    def F_stat(labels, yy):
        grand = yy.mean()
        ss_b = ss_w = 0.0
        for g in common:
            yg = yy[labels == g]
            ss_b += yg.size * (yg.mean() - grand) ** 2
            ss_w += ((yg - yg.mean()) ** 2).sum()
        df_b, df_w = len(common) - 1, yy.size - len(common)
        return (ss_b / df_b) / (ss_w / df_w)

    F_obs = F_stat(exp, y)
    rng = np.random.default_rng(seed)
    F_null = np.array([F_stat(rng.permutation(exp), y) for _ in range(n_perm)])
    p = float((np.sum(F_null >= F_obs) + 1) / (n_perm + 1))

    group_mean = np.array([y[exp == g].mean() for g in common])
    group_sd = np.array([y[exp == g].std(ddof=1) for g in common])
    group_n = np.array([int((exp == g).sum()) for g in common])
    effect_range = float(group_mean.max() - group_mean.min())

    print(f"  {ch} {quantity}: {len(common)} common exposures, "
          f"{y.size} frames   F={F_obs:.3f}  permutation p={p:.3f}")
    print(f"  group-mean range {effect_range:.4f} DN   "
          f"pooled within-group sd {np.sqrt(np.mean(group_sd**2)):.4f} DN   "
          f"(n per group: {group_n.tolist()})")

    fig, ax = plt.subplots(figsize=(7.5, 5), layout="constrained")
    ax.errorbar(common, group_mean, yerr=group_sd, fmt="o-", ms=5,
               capsize=3, color="tab:blue")
    ax.set_xscale("log")
    ax.set_xlabel("exposure (us, log scale)")
    ax.set_ylabel(f"per-frame {quantity} (DN)")
    ax.set_title(f"{ch}  {quantity} vs exposure   F={F_obs:.2f}  p={p:.3f}   "
                f"range={effect_range:.4f} DN", fontsize=9)
    ax.grid(alpha=0.3)
    fig.suptitle(f"error bars: within-exposure-group sd across "
                f"{len(set(files_all))} files", fontsize=8)
    _finish(fig, "series", f"exposure_{quantity}_{ch}", figs, savefigs)
    return dict(F=F_obs, p=p, exposures_us=common, group_mean=group_mean,
               group_sd=group_sd, group_n=group_n, effect_range=effect_range)


def estimate_dark_offset(samps, ch="B", sec=""):
    """
    The empirical-Bayes per-pixel dark offset, and the weight it deserves.

    **The model**, one term per timescale already examined:

        dark_i(k) = mu(k)  +  p_i  +  eps_i(k)

        mu(k)      frame-level common-mode term.  Scales 1 and 3 are here:
                   it is what `dark_temporal_check` measures drifting with
                   time.  Treated as a nuisance parameter -- estimated per
                   frame as that frame's own spatial mean and removed before
                   anything else is fit.
        p_i        the pixel's own fixed offset from mu -- time-invariant,
                   *if* the working hypothesis `dark_stability_check` tests
                   holds.
        eps_i(k)   fresh, independent noise, one draw per pixel per frame:
                   spatially independent (established earlier, so no
                   borrowing strength across neighbours is needed) and
                   independent of both p_i and mu by construction.

    **The estimator.**  p_i is never observed alone, only p_i + eps_i(k),
    once per frame.  Averaging the `n` available frames for one pixel gives

        r_i = mean_k( dark_i(k) - mu(k) ) = p_i + mean_k(eps_i)

    an unbiased but noisy estimate of p_i, Var(r_i) = sigma_eps^2 / n. If the
    *population* spread of p_i over pixels, sigma_p^2, is also known, the
    minimum-mean-square-error linear estimate of one pixel's p_i given its
    own noisy r_i is the standard Gaussian-conjugate (empirical-Bayes /
    James-Stein) shrinkage toward the population mean of p (zero, since mu
    already absorbed the overall level):

        B      = sigma_p^2 / (sigma_p^2 + sigma_eps^2 / n)     0 <= B <= 1
        p_hat_i = B * r_i

    `B` is the same quantity `dark_stability_check`'s split-half correlation
    estimates directly, by a different route, and the two are a check on
    each other.  `B` near 1 says the pixel's own history is trustworthy and
    a fresh single dark sample adds little beyond it; `B` near 0 says the
    apparent per-pixel spread is mostly noise and the population mean (i.e.
    just `mu(k)`, no per-pixel term) is the better bet.

    **The two variances, both estimated from this stack, not assumed:**

        sigma_eps^2   the leftover spread of eps_i(k) after both mu(k) and
                      p_i are removed -- should sit close to the
                      single-frame std `dark_analysis1` already reports.
                      Estimated as mean over pixels of Var_k(dark_i(k) -
                      mu(k) - r_i).
        sigma_p^2     the *true* between-pixel spread of p_i.  Var(r_i)
                      overstates it, because r_i itself carries sampling
                      noise sigma_eps^2 / n; the method-of-moments
                      correction removes that:

                          sigma_p^2 = Var(r_i) - sigma_eps^2 / n

                      clipped at 0 -- a negative raw result means the
                      apparent pixel-to-pixel spread is no more than
                      sampling noise, i.e. no fixed pattern to estimate.

    **Precondition.**  This shrinkage is the *Gaussian*-conjugate answer,
    optimal if p_i is normally distributed across pixels -- what
    `dark_cdf_check(quantity="mean")` tests.  A distinct hot-pixel tail
    there means a single global `(sigma_p, B)` over-shrinks exactly the
    pixels it matters most to get right; `outliers` below flags them rather
    than silently mistreating them.

    Args:
        samps: Capture names, any order -- more history shrinks
            sigma_eps^2/n and pushes `B` toward 1.
        ch: Bayer layer.
        sec: '' measurement sweep, 'pre' pre-image.

    Returns:
        dict: `p_hat` (rows, cols, DN), `B` (scalar -- every pixel shares
        the same `n` here, since every frame has every pixel), `sigma_eps`,
        `sigma_p` (DN), `n` (frames used), `mu_frame` (per-frame mean used),
        `meta`, and `outliers` (bool, rows, cols): `|r_i|` beyond 5 times
        r_i's own predictive spread, `sqrt(sigma_p^2 + sigma_eps^2/n)` --
        the full uncertainty an ordinary pixel's r_i can carry, sampling
        noise included, not `sigma_p` alone.  That distinction matters here:
        with `sigma_eps^2/n` comparable to or larger than `sigma_p^2` (this
        instrument, see the printed values), thresholding on `sigma_p` alone
        flags a large fraction of pixels on sampling noise, not on anything
        peculiar to them.  A high count after the correct threshold means
        `dark_cdf_check` is being asked to judge a mixture, not one
        population.

    Example:
        >>> est = estimate_dark_offset(SAMPS, "B")       # doctest: +SKIP
        >>> pred = predict_dark(est, mu_new=64.1)        # doctest: +SKIP
    """
    stack, meta = _dark_stack(samps, ch, sec)
    n = stack.shape[2]
    mu = stack.mean(axis=(0, 1))
    resid = stack - mu[None, None, :]
    r = resid.mean(axis=2)

    sigma_eps2 = float(resid.var(axis=2, ddof=1).mean())
    var_r = float(r.var(ddof=1))
    sigma_p2 = max(var_r - sigma_eps2 / n, 0.0)
    B = sigma_p2 / (sigma_p2 + sigma_eps2 / n) if sigma_p2 > 0 else 0.0

    p_hat = B * r
    r_sd = np.sqrt(max(var_r, 1e-12))          # r_i's own predictive spread
    outliers = np.abs(r) > 5.0 * r_sd

    print(f"  {ch}: n={n} frames   sigma_eps={np.sqrt(sigma_eps2):.4f} DN   "
          f"sigma_p={np.sqrt(sigma_p2):.4f} DN   B={B:.4f}   "
          f"{int(outliers.sum())} pixel(s) beyond 5x r_i's own spread "
          f"({5 * r_sd:.3f} DN)")

    return dict(p_hat=p_hat, B=B, sigma_eps=float(np.sqrt(sigma_eps2)),
               sigma_p=float(np.sqrt(sigma_p2)), n=n, outliers=outliers,
               mu_frame=mu, meta=meta)


def predict_dark(offset, mu_new):
    """
    The estimator's output: the predicted dark level for a new frame.

        dark_hat_i = mu_new + p_hat_i

    Args:
        offset: The dict `estimate_dark_offset` returned.
        mu_new: The new dark frame's own spatial mean, DN -- read off
            whichever fresh dark is captured nearest the lit frame in time;
            see `dark_temporal_check` for where `mu` actually moves.

    Returns:
        (rows, cols) predicted dark frame, DN.

    Example:
        >>> est = estimate_dark_offset(SAMPS, "B")       # doctest: +SKIP
        >>> pred = predict_dark(est, mu_new=64.1)        # doctest: +SKIP
    """
    return mu_new + offset["p_hat"]


# -- procedural steps: edit for each run --------------------------------

def run():
    SAMP = "C1017"
    SEC = ""
    
    if 0: 
        np.random.seed(1)
        A = np.random.rand(6)
        B = moving_average(A, 5)
        print ('A', A[0:2], A[-2:])
        print ('B', B[0:2], B[-2:])

    if 0:
        msall = dark_inventory(SAMP, SEC)
        print(f"{SAMP}{('/' + SEC) if SEC else ''} dark exposures (ms): {msall}")

    if 0:
        for ch in ["B"]:
            for ms in msall:
                dark_analysis1(SAMP, ms, ch=ch, sec=SEC, figs=True, savefigs=False)
                # dark_analysis1(SAMP, dark_inventory(SAMP, SEC)[0], ch=ch, sec=SEC,
                #            figs=True, savefigs=False)
    if 1: 
        samps = ["C1017", "C1018", "C1019", "C1020", "C1021", "C1022", "C1023", "C1024", "C1025"]
        for ch in ["Gr", "Gb", "B", "R"]:
            dark_analysis2(samps, ch=ch, sec=SEC, figs=True, savefigs=True)

    if 0:
        samps = ["C1017", "C1018", "C1019", "C1020", "C1021", "C1022", "C1023", "C1024", "C1025"]
        dark_cdf_check(samps, ch="B", sec="", quantity="mean",
                   figs=True, savefigs=False)

    if 0:
        samps = ["C1017", "C1018", "C1019", "C1020", "C1021", "C1022", "C1023", "C1024", "C1025"]
        dark_stability_check(samps, ch="B", sec="", 
                   figs=True, savefigs=False)
    
    if 0:
        samps = ["C1017", "C1018", "C1019", "C1020", "C1021", "C1022", "C1023", "C1024", "C1025"]
        estimate_dark_offset(samps, ch="B", sec="",)

if __name__ == "__main__":
    run()
