"""hotpixel_analysis.py -- desktop companion to src/darkframe.py.

Same relationship `analysis_2.py` has to the dark-signal investigation it
grew out of: `src/darkframe.py` is the engine (numpy only, no plotting, runs
on the Pi), this module drives it against real `.h5` archives through
`probe.py`, makes the figures that let a threshold or a correction be
approved by eye, and writes the result to the database through
`src/hotpixel_repo.py`.

The statistics this module visualizes -- the corrected outlier test, the
frame-mean-subtraction-plus-exceptions correction, and why a stored
per-pixel offset and a one-sided ("hot only") test were both considered and
set aside -- are derived and justified in
`tex/dark_signal_characterization.tex`, principally its §9. That document is
the reference for *why*; the docstrings in `src/darkframe.py` cite it section
by section, and this module's own docstrings point back to those rather than
repeating the derivation a third time.

Reused rather than reimplemented, on purpose (one implementation, not two):
  * `analysis_2._dark_stack()` -- every stored dark frame for one channel
    across a list of archives, already in bound-crop Bayer-layer coordinates.
    A fair alternative would have been a fresh loader written against
    `probe.Capture.darklayer()` directly; that was rejected because
    `_dark_stack()` already handles per-file dark-batch selection
    ("after_" preferred over "before_") and would only be duplicated, with
    the two copies free to drift apart.
  * `analysis_2._finish()` -- the one shared save/show helper every analysis
    module in this package already uses.

Coordinates: `_dark_stack()`'s arrays are bound-crop Bayer-layer coordinates
(130 x 183 for bound roi 39, matching `analysis_1`/`analysis_2`), but
`hotpixel_repo` stores **whole-frame** Bayer-layer coordinates -- see
`src/darkframe.py`'s module docstring for exactly why. `save_calibration()`
is where that shift happens: it reads one archive's stored `bound` (stream
space) and adds `bound.x // 2, bound.y // 2` to every flagged coordinate
before writing. This assumes every archive in `samps` shares one `bound`,
which holds for a single measurement campaign (the same assumption
`dark_analysis2` already makes); a campaign spanning a `bound` edit is
outside this module's scope and would need each archive's contribution
converted separately.
"""
import sys
from pathlib import Path

import numpy as np

SP = Path(__file__).resolve().parent
REPO = SP.parent.parent
sys.path.insert(0, str(SP))
sys.path.insert(0, str(REPO / "src"))

from probe import Capture                              # noqa: E402
from analysis_2 import _dark_stack, _finish, FIG        # noqa: E402

import darkframe                                        # noqa: E402
import hotpixel_repo                                    # noqa: E402


def _to_frame_first(stack: np.ndarray) -> np.ndarray:
    """(rows, cols, n) from `_dark_stack` -> (n, rows, cols) darkframe.py wants."""
    return np.moveaxis(stack, 2, 0)


def find_and_report(samps, ch="B", sec="", k=darkframe.DEFAULT_K,
                    figs=True, savefigs=True):
    """
    Run the hot-pixel test on one channel's pooled dark frames and show it.

    Prints every flagged pixel (layer coordinates and score, most extreme
    first), then draws two panels:

      * left  -- the mean dark frame, flagged pixels circled, so the
        physical pattern (clustered vs. scattered, edge vs. interior) can be
        seen -- the same "approve the picture before trusting the number"
        practice this project already uses for other image models.
      * right -- a histogram of `darkframe.dark_pixel_scores()` for every
        pixel, with `k` marked, so the threshold can be judged against the
        whole population rather than guessed at -- exactly the question
        tex/dark_signal_characterization.tex §9 raised when a naive
        threshold over-flagged 1589 of 23790 pixels.

    Args:
        samps: Capture names without the .h5.
        ch: Bayer layer -- 'R', 'Gr', 'Gb' or 'B'. Call once per layer; see
            src/darkframe.py's module docstring on why layers are never
            pooled together.
        sec: '' for the measurement sweep, 'pre' for the pre-image.
        k: Flag threshold passed to `darkframe.identify_hot_pixels()`.
        figs: Show the figure interactively.
        savefigs: Save the figure to fig/.

    Returns:
        The list of (y, x, z) darkframe.identify_hot_pixels() found, in
        `_dark_stack()`'s bound-crop layer coordinates (not yet the
        whole-frame coordinates `hotpixel_repo` stores -- see
        `save_calibration()`).

    Example:
        >>> find_and_report(["C1017", "C1018"], ch="B")   # doctest: +SKIP
    """
    import matplotlib.pyplot as plt

    stack, meta = _dark_stack(samps, ch, sec)
    dark_stack = _to_frame_first(stack)
    n, rows, cols = dark_stack.shape

    z = darkframe.dark_pixel_scores(dark_stack)
    found = darkframe.identify_hot_pixels(dark_stack, k=k)

    print(f"{ch}: {n} frames, {rows * cols} pixels, "
         f"{len(found)} flagged at k={k:g}")
    for y, x, score in sorted(found, key=lambda t: -abs(t[2])):
        print(f"  (y={y:3d}, x={x:3d})  z={score:+7.2f}")

    darkmean = dark_stack.mean(axis=0)
    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(11, 4.6),
                                   layout="constrained")

    lo, hi = np.percentile(darkmean, [0.5, 99.5])
    im = ax0.imshow(darkmean, cmap="viridis", vmin=lo, vmax=hi)
    if found:
        ys = [y for y, x, _ in found]
        xs = [x for y, x, _ in found]
        ax0.plot(xs, ys, "o", mfc="none", mec="red", mew=1.3, ms=9,
                 label=f"{len(found)} flagged")
        ax0.legend(loc="upper right", fontsize=8)
    fig.colorbar(im, ax=ax0, label="mean dark DN")
    ax0.set_title(f"{ch}: mean dark frame, n={n}")
    ax0.set_xlabel("column (bound-crop layer px)")
    ax0.set_ylabel("row (bound-crop layer px)")

    ax1.hist(z.ravel(), bins=80, color="steelblue", edgecolor="none")
    ax1.axvline(k, color="red", ls="--", lw=1, label=f"k = {k:g}")
    ax1.axvline(-k, color="red", ls="--", lw=1)
    ax1.set_yscale("log")
    ax1.set_title(f"{ch}: per-pixel score distribution")
    ax1.set_xlabel("z  (units of predictive sigma)")
    ax1.set_ylabel("pixel count")
    ax1.legend(loc="upper right", fontsize=8)
    ax1.grid(alpha=0.3)

    fig.suptitle(f"hotpixel_analysis.find_and_report  {','.join(samps)}  {ch}")
    _finish(fig, "_".join(samps), f"hotpixel_{ch}", figs, savefigs)
    return found


def save_calibration(samps, ch, model, frame_w, frame_h, sec="",
                     k=darkframe.DEFAULT_K, hardware_sn=None,
                     analysisref=None, bayer_pattern=None):
    """
    Find hot pixels for one channel and write them to `hotpixels`.

    Converts `_dark_stack()`'s bound-crop layer coordinates to the
    whole-frame layer coordinates `hotpixel_repo` stores by reading the
    first archive's own stored `bound` (stream space) and adding
    `bound.x // 2, bound.y // 2` -- see the module docstring's coordinate
    note for why this, and its one-bound-per-campaign assumption.

    Args:
        samps: Capture names without the .h5.
        ch: Bayer layer -- 'R', 'Gr', 'Gb' or 'B'.
        model: Camera type to stamp on every row, e.g. 'imx708'.
        frame_w: Stream width in pixels the whole-frame coordinates assume.
        frame_h: Stream height in pixels.
        sec: '' for the measurement sweep, 'pre' for the pre-image.
        k: Flag threshold passed to `darkframe.identify_hot_pixels()`.
        hardware_sn: Camera identifier to stamp on every row, or None.
        analysisref: Tag naming this run, e.g. 'dark2026-09'. Defaults to
            the joined sample list if not given.
        bayer_pattern: 'BGGR' or 'RGGB', stamped as a sanity check.

    Every row's `remark` records the threshold, the archive span and the
    exposure range pooled.  `exposure_us` is stamped only when every frame
    shared one exposure; a pool across exposures leaves it NULL rather
    than name one exposure out of many.

    Returns:
        The number of rows written (== the number of pixels flagged).

    Example:
        >>> save_calibration(["C1017", "C1018", "C1019"], "B",
        ...                  model="imx708", frame_w=2304, frame_h=1296,
        ...                  analysisref="dark2026-09")  # doctest: +SKIP
    """
    stack, meta = _dark_stack(samps, ch, sec)
    dark_stack = _to_frame_first(stack)
    n = dark_stack.shape[0]
    found = darkframe.identify_hot_pixels(dark_stack, k=k)

    c = Capture(samps[0], sec)
    bound_x, bound_y, _bw, _bh = c.bound
    bound_ly, bound_lx = bound_y // 2, bound_x // 2
    c.close()

    exposures = sorted({m["exposure_us"] for m in meta})
    exposure_us = exposures[0] if len(exposures) == 1 else None
    remark = (f"k={k:g}; {samps[0]}..{samps[-1]} ({len(samps)} archives"
              f"{', ' + sec if sec else ''}); dark exposures "
              f"{exposures[0]}-{exposures[-1]} us pooled")
    rows = [
        hotpixel_repo.HotPixel(
            model=model, frame_w=frame_w, frame_h=frame_h, bayer_layer=ch,
            x=x + bound_lx, y=y + bound_ly, hardware_sn=hardware_sn,
            bayer_pattern=bayer_pattern, score=score,
            exposure_us=exposure_us, n_frames=n, remark=remark)
        for y, x, score in found
    ]
    ref = analysisref or "_".join(samps)
    written = hotpixel_repo.write_hotpixels(rows, analysisref=ref)
    print(f"{ch}: wrote {written} hot pixel(s) for {model} "
         f"{frame_w}x{frame_h} (analysisref={ref!r})")
    return written


def before_after(samp, led, label, ch, hot_coords=(), sec="",
                 figs=True, savefigs=True):
    """
    One lit frame, dark-corrected plainly and then with hot-pixel repair,
    side by side.

    The approval-figures practice this project already uses for other image
    models: the visible effect of `darkframe.correct_dark()` should be judged
    before its output is trusted, not assumed correct because the numbers
    changed in the expected direction.

    Args:
        samp: Capture name without the .h5.
        led: LED colour name, e.g. 'royal_blue'.
        label: Exposure label within that LED, e.g. 'e0'.
        ch: Bayer layer -- 'R', 'Gr', 'Gb' or 'B'.
        hot_coords: (y, x) pairs already in this frame's own (bound-crop)
            coordinate space -- e.g. the output of `find_and_report()` for
            this same sample set and channel. Empty shows plain mean
            subtraction on both panels, which is a legitimate before/after
            of "no calibration yet".
        sec: '' for the measurement sweep, 'pre' for the pre-image.
        figs: Show the figure interactively.
        savefigs: Save the figure to fig/.

    Returns:
        None.

    Example:
        >>> before_after("C1017", "royal_blue", "e0", "B",
        ...              hot_coords=[(45, 12)])   # doctest: +SKIP
    """
    import matplotlib.pyplot as plt

    c = Capture(samp, sec)
    lit_minus_dark, lit = c.layer(led, label, ch)
    dark = c.darklayer(led, label, ch)
    c.close()

    plain = darkframe.correct_dark(lit, dark, hot_coords=())
    repaired = darkframe.correct_dark(lit, dark, hot_coords=hot_coords)

    lo, hi = np.percentile(plain, [0.5, 99.5])
    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(10, 4.6),
                                   layout="constrained", sharex=True,
                                   sharey=True)
    ax0.imshow(plain, cmap="viridis", vmin=lo, vmax=hi)
    ax0.set_title("plain mean subtraction")
    ax1.imshow(repaired, cmap="viridis", vmin=lo, vmax=hi)
    ax1.set_title(f"+ {len(hot_coords)} hot pixel(s) repaired")
    for ax in (ax0, ax1):
        ax.set_xlabel("column (layer px)")
        ax.set_ylabel("row (layer px)")

    fig.suptitle(f"before_after  {samp}/{led}/{label}  {ch}")
    _finish(fig, samp, f"hotpixel_before_after_{led}_{label}_{ch}",
           figs, savefigs)


def _whole_frame_offsets(samp, ch, sec=""):
    """
    One archive's per-pixel dark offset, placed in whole-frame layer space.

    The offset of a pixel is how far it sits from its own frame's mean,
    averaged over every dark frame the archive holds for this layer:

        offset_i = mean over frames k of ( d_i(k) - mean_j d_j(k) )

    Subtracting each frame's own mean first removes the frame-to-frame
    drift of the whole sensor, which is common to every pixel and is not
    what makes a pixel hot.

    Args:
        samp: Capture name without the .h5.
        ch: Bayer layer -- 'R', 'Gr', 'Gb' or 'B'.
        sec: '' for the measurement sweep, 'pre' for the pre-image.

    Returns:
        dict with `offset` (rows, cols) DN, `stack` (n, rows, cols) raw DN,
        `origin` (ly, lx) -- the crop's top-left corner in whole-frame layer
        px, i.e. (bound_y // 2, bound_x // 2) -- `exposure_us` (n,),
        and `when` (datetime of the first frame).
    """
    stack, meta = _dark_stack([samp], ch, sec)
    stack = _to_frame_first(stack)
    resid = stack - stack.mean(axis=(1, 2), keepdims=True)
    c = Capture(samp, sec)
    bound_x, bound_y, _bw, _bh = c.bound
    c.close()
    return dict(offset=resid.mean(axis=0), stack=stack,
                origin=(bound_y // 2, bound_x // 2),
                exposure_us=np.array([m["exposure_us"] for m in meta]),
                when=meta[0]["dt"])


def persistence_over_time(periods, ch, sec="", k=darkframe.DEFAULT_K,
                          figs=True, savefigs=True):
    """
    Do the hot pixels stay the same over weeks, or come and go?

    Method, one Bayer layer at a time (layers are separate silicon, see
    src/darkframe.py):

    1. For each period (a list of archives taken close together in time)
       pool every dark frame and run the same corrected outlier test
       `find_and_report()` uses, `darkframe.identify_hot_pixels()`.  Each
       period is judged on its own frames only.
    2. Move every flagged coordinate from bound-crop to whole-frame layer
       space (add bound // 2), because the crop moved between campaigns
       (C1014 raised bound.y from 588 to 548) and the same physical pixel
       has different crop coordinates before and after.
    3. Compare the sets, counting only pixels every period could see (the
       overlap of the crops), since a pixel outside one period's crop cannot
       have been flagged there.
    4. Follow each flagged pixel archive by archive: its offset from the
       frame mean, in DN, over the whole time span.  A persistent defect is
       a flat line well away from zero; a transient one is a spike.

    Also prints, for each flagged pixel, how its offset depends on
    exposure (DN per 10 ms), because true dark-current hot pixels grow with
    exposure while a fixed offset does not.

    Args:
        periods: {name: [samp, ...]} in time order.
        ch: Bayer layer -- 'R', 'Gr', 'Gb' or 'B'.
        sec: '' for the measurement sweep, 'pre' for the pre-image.
        k: Flag threshold passed to `darkframe.identify_hot_pixels()`.
        figs: Show the figure interactively.
        savefigs: Save the figure to fig/.

    Returns:
        {period name: set of (y, x)} flagged, in whole-frame layer px.

    Example:
        >>> persistence_over_time({"aug": ["C1017"], "sep": ["C1028"]},
        ...                       "B")                   # doctest: +SKIP
    """
    import matplotlib.pyplot as plt

    per_archive = {}                     # samp -> _whole_frame_offsets dict
    flagged, extent = {}, {}
    for name, samps in periods.items():
        arcs = [_whole_frame_offsets(s, ch, sec) for s in samps]
        origins = {a["origin"] for a in arcs}
        if len(origins) != 1:
            raise ValueError(f"period {name!r} mixes crops {origins}; "
                             f"split it so each period has one bound")
        (oy, ox), = origins
        pooled = np.concatenate([a["stack"] for a in arcs], axis=0)
        found = darkframe.identify_hot_pixels(pooled, k=k)
        flagged[name] = {(y + oy, x + ox): z for y, x, z in found}
        rows, cols = pooled.shape[1:]
        extent[name] = (oy, oy + rows, ox, ox + cols)
        per_archive.update(zip(samps, arcs))
        print(f"{ch} {name}: {len(samps)} archives, {pooled.shape[0]} "
              f"frames, {len(found)} flagged at k={k:g}")

    # The rectangle every period saw.
    y0 = max(e[0] for e in extent.values())
    y1 = min(e[1] for e in extent.values())
    x0 = max(e[2] for e in extent.values())
    x1 = min(e[3] for e in extent.values())
    seen_by_all = lambda p: y0 <= p[0] < y1 and x0 <= p[1] < x1  # noqa: E731
    names = list(periods)
    common = {n: {p for p in flagged[n] if seen_by_all(p)} for n in names}
    union = set().union(*common.values())
    everywhere = set.intersection(*common.values()) if common else set()
    print(f"{ch} overlap rows {y0}-{y1 - 1}, cols {x0}-{x1 - 1} "
          f"(whole-frame layer px): {len(union)} pixel(s) flagged in at "
          f"least one period, {len(everywhere)} in every period")

    # Offset of every union pixel in every archive that covers it.
    def offset_at(a, p):
        oy, ox = a["origin"]
        yy, xx = p[0] - oy, p[1] - ox
        h, w = a["offset"].shape
        return a["offset"][yy, xx] if 0 <= yy < h and 0 <= xx < w else np.nan

    order = sorted(per_archive, key=lambda s: per_archive[s]["when"])
    allflag = sorted(set().union(*flagged.values()))
    print(f"\n  {'pixel (y, x)':<14}" + "".join(f"{n:>12}" for n in names)
          + f"{'DN/10ms':>10}   (offset DN when flagged, '.' = seen but "
          f"not flagged, '-' = outside crop)")
    for p in allflag:
        cells = []
        for n in names:
            e = extent[n]
            if not (e[0] <= p[0] < e[1] and e[2] <= p[1] < e[3]):
                cells.append(f"{'-':>12}")
            elif p in flagged[n]:
                v = np.nanmean([offset_at(per_archive[s], p)
                                for s in periods[n]])
                cells.append(f"{v:>+12.2f}")
            else:
                cells.append(f"{'.':>12}")
        # exposure slope, all frames that cover this pixel
        xs, ys = [], []
        for s in order:
            a = per_archive[s]
            oy, ox = a["origin"]
            yy, xx = p[0] - oy, p[1] - ox
            h, w = a["offset"].shape
            if 0 <= yy < h and 0 <= xx < w:
                fr = a["stack"]
                ys.extend(fr[:, yy, xx] - fr.mean(axis=(1, 2)))
                xs.extend(a["exposure_us"] / 1e4)
        slope = np.polyfit(xs, ys, 1)[0] if len(set(xs)) > 1 else np.nan
        print(f"  {str(p):<14}" + "".join(cells) + f"{slope:>+10.3f}")

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(13, 4.8),
                                   layout="constrained")
    t = [per_archive[s]["when"] for s in order]
    for p in allflag:
        v = [offset_at(per_archive[s], p) for s in order]
        ax0.plot(t, v, ".-", lw=0.8, ms=4,
                 color="tab:red" if p in everywhere else "0.6")
    ax0.axhline(0, color="k", lw=0.6)
    ax0.set_ylabel("offset from frame mean (DN)")
    ax0.set_title(f"{ch}: every flagged pixel, archive by archive\n"
                  f"red = flagged in every period, gray = not")
    ax0.grid(alpha=0.3)
    fig.autofmt_xdate()

    # Early-vs-late offsets for every pixel in the overlap: a fixed pattern
    # lies on the diagonal, pure noise is a round blob.
    first = [per_archive[s] for s in periods[names[0]]]
    last = [per_archive[s] for s in periods[names[-1]]]

    def mean_offset(arcs):
        return np.mean([a["offset"][y0 - a["origin"][0]:y1 - a["origin"][0],
                                    x0 - a["origin"][1]:x1 - a["origin"][1]]
                        for a in arcs], axis=0)
    e_off, l_off = mean_offset(first).ravel(), mean_offset(last).ravel()
    r = np.corrcoef(e_off, l_off)[0, 1]
    ax1.plot(e_off, l_off, ",", color="steelblue")
    lim = max(np.abs(e_off).max(), np.abs(l_off).max()) * 1.05
    ax1.plot([-lim, lim], [-lim, lim], "--", color="0.5", lw=0.8)
    ax1.set_xlim(-lim, lim)
    ax1.set_ylim(-lim, lim)
    ax1.set_aspect("equal")
    ax1.set_xlabel(f"offset, {names[0]} (DN)")
    ax1.set_ylabel(f"offset, {names[-1]} (DN)")
    ax1.set_title(f"every overlap pixel, first vs last period   "
                  f"r = {r:.3f}")
    ax1.grid(alpha=0.3)
    print(f"{ch}: correlation of per-pixel offsets, {names[0]} vs "
          f"{names[-1]}, all {e_off.size} overlap pixels: r = {r:.3f}")

    fig.suptitle(f"hotpixel_analysis.persistence_over_time  {ch}  "
                 f"k={k:g}  {' / '.join(names)}")
    _finish(fig, f"persistence_{'_'.join(names)}", f"hotpixel_{ch}",
            figs, savefigs)
    return {n: set(flagged[n]) for n in names}


#: The three dark-frame campaigns in the archive, in time order.  The crop
#: moved between the first and the second (bound.y 588 -> 548 at C1014).
PERIODS = {
    "aug10-13": [f"C{n}" for n in range(1002, 1014)],
    "aug18-23": ["C1014"] + [f"C{n}" for n in range(1016, 1026)],
    "sep25-26": [f"C{n}" for n in range(1028, 1037)],
}


if __name__ == "__main__":
    # Hard-coded procedure, edited per run -- same convention analysis_2.py
    # uses. Bounded run: a handful of archives, one channel, no writes.
    if 0:
        SAMPS = ["C1017", "C1018", "C1019", "C1020", "C1021",
                 "C1022", "C1023", "C1024", "C1025"]
        find_and_report(SAMPS, ch="B", figs=False, savefigs=True)
    if 1:
        for CH in ("R", "Gr", "Gb", "B"):
            persistence_over_time(PERIODS, CH, figs=False, savefigs=True)
            print()
