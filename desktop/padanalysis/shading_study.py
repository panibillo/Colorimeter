"""shading_study.py -- can the pre-image tell us where the pad is shaded?

A hand-driven study, run from an IDE, like `pca_study.py`.

The question: `duotone.contrast` removes shading by taking out the part of
every pixel that is the same in all channels.  That also takes out the part
of the dye that absorbs evenly, which is most of brown's darkness.  The
alternative studied here measures the shading directly, on the dry pad in
the pre-image, and removes it pixel by pixel from the measurement, leaving
all of the dye's darkness in.

Everything is in absorbance, where a shadow is an added constant:

    A      the pixel array values transformed to Absorbance
    a_R    the measurement's A against the dry-pad median (as now)
    a_U    the pre-image's A against its own median, carried onto the
           measurement's grid by one of the registrations below
    a^s    a smoothed with a Gaussian of width w px, over the pad only
    a*     a - a^s, the fine texture
    gamma  slope of a_R^s on a_U^s over the white zone: how much of the
           pre-image's smooth shading is still there in the measurement
    beta   slope of a_R* on a_U* over the white zone: the same for the
           fine texture
    a_flat = a_R - (gamma * a_U^s + beta * a_U*)

The white zone is the measurement's pad at least WHITE_GAP px outside the
dot, where there is (almost) no dye, so whatever a_R shows there is
shading, texture and noise.  Slopes, not correlations, because noise and
leftover dye in a_R do not bias a slope, and the slope is the weight that
leaves the least error (see docs/shading_study_2026-09-29.md).

Registrations compared (all start from 'arith'):
    'arith'    `analysis_1.pre_to_post`: the two fitted pad rectangles,
               no image matching (what `get_balanced_cell_data` uses);
    'ecc_rim'  OpenCV ECC, rigid (shift + rotation), on the white zone
               including the pad's rim -- lines up the rim shadow;
    'ecc_tex'  OpenCV ECC, shift only, on the fine texture of the inner
               white zone -- the "maximize R* . U*" of Bill's proposal;
    'peak'     the same aim by brute force: the shift, on a 0.25 px grid
               within +/-2 px of 'arith', where the fine textures correlate
               best (`shift_surface`).  A check on the ECC answers.
    An affine ECC was tried first and is not used: it failed to converge on
    some pads and ran off to scales of 1.5-5 on others, which the fixed
    camera cannot produce.

    Decision (Bill, 2026-09-29): keep 'arith'.  'peak' (`shift_surface`,
    `warp_of(..., "peak")`, `show_surface`) is kept as demonstration code:
    on the 18 pads it moves the pre-image a median 0.25 px (at most 0.56)
    and barely changes the correction.  The numbers, and when a better
    registration would pay, are in the doc's "Decisions" section.

Samples: C1017-C1025 and C1028-C1036, the six channels they all share.
Read-only.  Figures go to `fig/shading_study_<what>.png` when asked.
Findings: `docs/shading_study_2026-09-29.md`.
"""
import sys
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np
from scipy import ndimage

SP = Path(__file__).resolve().parent
sys.path.insert(0, str(SP))

import analysis_1 as a1                                     # noqa: E402
import duotone as dt                                        # noqa: E402
import pca_study as ps                                      # noqa: E402

FIG = SP / "fig"

POOL = ps.POOL
SET2 = ps.SET2

#: The six channels all 18 samples share, in wavelength order.
CELLS = [("violet", "B"), ("royal_blue", "B"), ("cyan", "B"),
         ("green", "Gb"), ("green", "Gr"), ("photo_red", "R")]

#: Smoothing widths tried, Gaussian sigma in layer px.
WIDTHS = (1.0, 2.0, 4.0)

#: The width used for the correction and the figures.
W = 2.0

#: The white zone starts this many layer px outside the dot's edge.
WHITE_GAP = 6.0

#: Pad pixels within this many px of the pad's edge are the "rim".
RIM_PX = 4.0

#: Registration methods, in the order they are tried.
METHODS = ("arith", "ecc_rim", "ecc_tex", "peak")

#: `shift_surface` searches this far either side of 'arith', px, ...
SEARCH_PX = 2.0

#: ... in steps of this size.
SEARCH_STEP = 0.25

#: ECC stopping rule: iterations, and the smallest change worth another.
ECC_CRITERIA = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6)

_planes = {}
_warps = {}


# ── data ───────────────────────────────────────────────────────────────

def plane(samp_no, cell, sec="", rung=None) -> np.ndarray:
    """
    One channel's absorbance on its own sweep's grid, cached.

    Args:
        samp_no: Sample number, e.g. "C1032".
        cell: (LED, Bayer layer).
        sec: '' for the measurement, a1.PRE for the pre-image.
        rung: Exposure rung, or None for the one `analysis_1` picks.

    Returns:
        (rows, cols) float array over the whole bound crop, NaN where there
        is no value (hot pixels, outside the flat-field model), or None if
        the channel is not there or has no dry-pad reference.
    """
    key = (samp_no, cell, sec, rung)
    if key not in _planes:
        b = a1.get_balanced_cell_data(samp_no, *cell, roi=a1.ROIBOUND,
                                      sec=sec, rung=rung, register=False)
        _planes[key] = (None if b is None or b.A is None
                        else np.ma.filled(b.A.astype(float), np.nan))
    return _planes[key]


def mean_plane(samp_no, sec="") -> np.ndarray:
    """
    The average of the six channels' absorbance, for registration.

    Registration is geometry, the same for every channel, so one image per
    sweep does: averaging the channels lowers the noise.

    Args:
        samp_no: Sample number.
        sec: '' or a1.PRE.

    Returns:
        (rows, cols) float array, NaN where any channel is missing.
    """
    return np.mean([plane(samp_no, c, sec) for c in CELLS], axis=0)


def smooth(a, region, w) -> np.ndarray:
    """
    Gaussian smoothing that uses only pixels inside `region`.

    "Normalized" smoothing: smooth(a * m) / smooth(m), with m the usable
    pixels.  Plain smoothing next to the pad's edge would mix in the paper
    (or NaN); this averages only what is inside.

    Args:
        a: (rows, cols) array; NaN is treated as missing.
        region: (rows, cols) bool, the pixels allowed to contribute.
        w: Gaussian sigma in px.

    Returns:
        (rows, cols) array, NaN outside `region`.
    """
    m = (region & np.isfinite(a)).astype(float)
    num = ndimage.gaussian_filter(np.where(m > 0, a, 0.0), w, mode="constant")
    den = ndimage.gaussian_filter(m, w, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[~region] = np.nan
    return out


def zones(samp_no) -> dict:
    """
    The regions of the measurement's pad used throughout.

    Args:
        samp_no: Sample number.

    Returns:
        Dict of (rows, cols) bool arrays on the measurement grid:
            'pad'    the fitted pad, no erosion;
            'white'  pad pixels at least WHITE_GAP px outside the dot;
            'rim'    white pixels within RIM_PX of the pad's edge;
            'inner'  white pixels deeper than RIM_PX;
            'nodot'  pad pixels at least 3 px outside the dot, the region
                     a_R is smoothed over (so the dye does not leak out);
            'dist'   (float) signed distance from the dot's edge, px.
    """
    pad = a1.pad_of(samp_no)
    _, _, _, dist, _ = ps.dot_geometry(samp_no)
    m = pad.mask(0)
    white = m & (dist >= WHITE_GAP)
    return dict(pad=m, white=white, rim=white & (pad.depth <= RIM_PX),
                inner=white & (pad.depth > RIM_PX),
                nodot=m & (dist >= 3.0), dist=dist)


# ── registration ───────────────────────────────────────────────────────

def arith_warp(samp_no) -> np.ndarray:
    """
    `analysis_1.pre_to_post` written as a 2 x 3 affine matrix.

    pre_to_post is exactly affine in (x, y) (a shift, and a shear from the
    two tilts), so a least-squares fit to its coordinate grids reproduces it
    exactly.

    Args:
        samp_no: Sample number.

    Returns:
        W (2, 3) float32: [x_pre, y_pre] = W @ [x_post, y_post, 1].
    """
    y_pre, x_pre = a1.pre_to_post(samp_no)
    rows, cols = y_pre.shape
    yy, xx = np.mgrid[0:rows, 0:cols]
    G = np.column_stack([xx.ravel(), yy.ravel(), np.ones(xx.size)])
    wx, *_ = np.linalg.lstsq(G, x_pre.ravel(), rcond=None)
    wy, *_ = np.linalg.lstsq(G, y_pre.ravel(), rcond=None)
    return np.array([wx, wy], dtype=np.float32)


def resample(img, warp, shape) -> np.ndarray:
    """
    Carry a pre-image array onto the measurement grid.

    Bilinear, like `analysis_1._register`; a NaN input spreads to the up to
    four output pixels it touches, which are then NaN.

    Args:
        img: (rows, cols) array on the pre-image grid.
        warp: (2, 3) matrix from measurement (x, y) to pre-image (x, y).
        shape: The measurement grid's (rows, cols).

    Returns:
        (rows, cols) array on the measurement grid, NaN off the image.
    """
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]].astype(float)
    x_pre = warp[0, 0] * xx + warp[0, 1] * yy + warp[0, 2]
    y_pre = warp[1, 0] * xx + warp[1, 1] * yy + warp[1, 2]
    return ndimage.map_coordinates(np.asarray(img, float), [y_pre, x_pre],
                                   order=1, mode="constant", cval=np.nan)


def _ecc(template, moving, mask, warp, motion):
    """
    One OpenCV ECC run, NaN-safe.

    ECC ("enhanced correlation coefficient") adjusts `warp` until the
    moving image, carried through it, has the highest normalized
    correlation with the template over `mask`.

    Args:
        template: Measurement-grid image.
        moving: Pre-image-grid image.
        mask: Measurement-grid bool, the pixels that count.
        warp: Starting (2, 3) matrix, template (x, y) -> moving (x, y).
        motion: cv2.MOTION_AFFINE or cv2.MOTION_TRANSLATION.

    Returns:
        (warp, cc): the refined matrix and the correlation reached, or
        (the starting warp, nan) if ECC did not converge.
    """
    t = np.where(np.isfinite(template), template, 0).astype(np.float32)
    mv = np.where(np.isfinite(moving), moving, 0).astype(np.float32)
    m = (mask & np.isfinite(template)).astype(np.uint8)
    w = warp.astype(np.float32).copy()
    try:
        cc, w = cv2.findTransformECC(t, mv, w, motion, ECC_CRITERIA, m, 1)
    except cv2.error:
        return warp.astype(np.float32), float("nan")
    return w, float(cc)


def _shifted(warp, d) -> np.ndarray:
    """
    A warp moved by an extra shift `d` in measurement coordinates.

    Args:
        warp: (2, 3) matrix, measurement (x, y) -> pre-image (x, y).
        d: (dx, dy): the pre-image is read at warp(p + d) instead of warp(p).

    Returns:
        The new (2, 3) float32 matrix.
    """
    w = np.array(warp, np.float32).copy()
    w[:, 2] = warp[:, :2] @ np.asarray(d, float) + warp[:, 2]
    return w


def fine_pair(samp_no, w=W) -> tuple:
    """
    The fine texture of both sweeps, from the six-channel average.

    The pre-image's texture is taken on its own grid (smoothed over its own
    pad), so moving it by a shift needs only a resampling.

    Args:
        samp_no: Sample number.
        w: Smoothing width, px.

    Returns:
        (post_fine, pre_fine): measurement grid, and pre-image grid.
    """
    z = zones(samp_no)
    post = mean_plane(samp_no)
    pre = mean_plane(samp_no, a1.PRE)
    pre_pad = a1.pad_of(samp_no, a1.PRE).mask(0)
    return (post - smooth(post, z["nodot"], w),
            pre - smooth(pre, pre_pad, w))


def _fine_r(post_f, pre_f, warp, region) -> float:
    """Correlation of the two fine textures over `region`, given a warp."""
    u = resample(pre_f, warp, post_f.shape)[region]
    r = post_f[region]
    ok = np.isfinite(u) & np.isfinite(r)
    return float(np.corrcoef(u[ok], r[ok])[0, 1])


def shift_surface(samp_no) -> tuple:
    """
    How well the fine textures line up, for shifts around 'arith'.

    Args:
        samp_no: Sample number.

    Returns:
        (steps, surf): `steps` the 1-D list of shifts tried (px, the same
        for x and y); `surf[i, j]` the correlation over the inner white zone
        with the pre-image moved by (steps[j], steps[i]).
    """
    z = zones(samp_no)
    post_f, pre_f = fine_pair(samp_no)
    w0 = arith_warp(samp_no)
    steps = np.arange(-SEARCH_PX, SEARCH_PX + 1e-9, SEARCH_STEP)
    surf = np.array([[_fine_r(post_f, pre_f, _shifted(w0, (dx, dy)),
                              z["inner"]) for dx in steps] for dy in steps])
    return steps, surf


def warp_of(samp_no, method) -> tuple:
    """
    The pre-to-measurement registration by one method, cached.

    Args:
        samp_no: Sample number.
        method: One of METHODS (see the module docstring).

    Returns:
        (warp, score): the (2, 3) matrix, measurement (x, y) -> pre-image
        (x, y), and the correlation the method reached (ECC's own number
        for the ECC methods, the peak correlation for 'peak', nan for
        'arith').  If ECC fails, the 'arith' matrix and nan.
    """
    key = (samp_no, method)
    if key in _warps:
        return _warps[key]
    w0 = arith_warp(samp_no)
    z = zones(samp_no)
    if method == "arith":
        out = (w0, float("nan"))
    elif method == "ecc_rim":
        out = _ecc(mean_plane(samp_no), mean_plane(samp_no, a1.PRE),
                   z["white"], w0, cv2.MOTION_EUCLIDEAN)
    elif method == "ecc_tex":
        # Shift-only ECC between the two fine textures, the pre-image's
        # already carried through 'arith'; the shift found is then folded
        # back into the warp.
        post_f, pre_f = fine_pair(samp_no)
        moved = resample(pre_f, w0, post_f.shape)
        eye = np.array([[1, 0, 0], [0, 1, 0]], np.float32)
        dw, cc = _ecc(post_f, moved, z["inner"], eye,
                      cv2.MOTION_TRANSLATION)
        out = (_shifted(w0, dw[:, 2]), cc)
    elif method == "peak":
        steps, surf = shift_surface(samp_no)
        i, j = np.unravel_index(np.nanargmax(surf), surf.shape)
        out = (_shifted(w0, (steps[j], steps[i])), float(surf[i, j]))
    else:
        raise ValueError(f"unknown method {method!r}")
    _warps[key] = out
    return out


def extra_shift(samp_no, method) -> np.ndarray:
    """
    How far a method moved the pre-image from 'arith', in measurement px.

    Args:
        samp_no: Sample number.
        method: One of METHODS.

    Returns:
        (dx, dy) at the pad's center.
    """
    ys, xs = np.nonzero(a1.pad_of(samp_no).mask(0))
    p = np.array([xs.mean(), ys.mean(), 1.0])
    w0 = arith_warp(samp_no)
    d = warp_of(samp_no, method)[0] @ p - w0 @ p
    # d is in pre-image px; the pre-image and measurement scales are equal.
    return d


def sensor_fixed(samp_no, w=W) -> float:
    """
    Fine pattern fixed to the camera rather than to the pad.

    Compares the two sweeps' fine textures at the *same camera pixel* (no
    registration), over pixels deep inside both pads.  If the strip moved
    by several px between the sweeps, the pad's own texture no longer lines
    up there, so any correlation left belongs to the camera or the lights.

    Args:
        samp_no: Sample number.
        w: Smoothing width, px.

    Returns:
        The correlation, or nan if too few pixels overlap.
    """
    z = zones(samp_no)
    post_f, pre_f = fine_pair(samp_no, w)
    region = z["inner"] & a1.pad_of(samp_no, a1.PRE).mask(3)
    x, y = pre_f[region], post_f[region]
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 50:
        return float("nan")
    return float(np.corrcoef(x[ok], y[ok])[0, 1])


# ── the correction ─────────────────────────────────────────────────────

def _slope(y, x) -> float:
    """Least-squares slope of y on x (with an intercept); nan if flat."""
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok] - x[ok].mean(), y[ok] - y[ok].mean()
    sxx = float(x @ x)
    return float(x @ y) / sxx if sxx > 0 else float("nan")


def _spread(v) -> float:
    """Robust sigma: 1.4826 * median absolute deviation, NaN ignored."""
    v = v[np.isfinite(v)]
    if v.size == 0:
        return float("nan")
    return float(1.4826 * np.median(np.abs(v - np.median(v))))


def correct(samp_no, cell, method="arith", w=W) -> dict:
    """
    Remove the pre-image's shading from one channel of the measurement.

    Args:
        samp_no: Sample number.
        cell: (LED, Bayer layer).
        method: Registration, see `warp_of`.
        w: Smoothing width, px.

    Returns:
        Dict of (rows, cols) arrays on the measurement grid -- 'aR', 'aU'
        (registered), 'aUs', 'aUf' (smooth and fine parts of aU), 'aRs',
        'aRf', 'flat' (the corrected measurement) -- and the scalars
        'gamma', 'beta', and 'zones' (from `zones`).
    """
    z = zones(samp_no)
    aR = plane(samp_no, cell)
    aU = resample(plane(samp_no, cell, a1.PRE), warp_of(samp_no, method)[0],
                  aR.shape)
    aUs = smooth(aU, z["pad"], w)
    aUf = aU - aUs
    aRs = smooth(aR, z["nodot"], w)
    aRf = aR - aRs
    wz = z["white"]
    gamma = _slope(aRs[wz], aUs[wz])
    beta = _slope(aRf[wz], aUf[wz])
    flat = aR - (gamma * aUs + beta * aUf)
    return dict(aR=aR, aU=aU, aUs=aUs, aUf=aUf, aRs=aRs, aRf=aRf, flat=flat,
                gamma=gamma, beta=beta, zones=z)


def white_stats(samp_no, method="arith", w=W) -> list:
    """
    The correction's numbers for every channel of one pad.

    Args:
        samp_no: Sample number.
        method: Registration, see `warp_of`.
        w: Smoothing width, px.

    Returns:
        List of dicts, one per channel: 'cell', 'gamma', 'beta', 'r_fine'
        (correlation of the fine textures in the white zone), and the
        robust spread of a_R and of the corrected a_flat in the white
        zone's inner part and rim ('sR_in', 'sF_in', 'sR_rim', 'sF_rim'),
        and of the fine part alone in the inner part, before and after
        its share of the correction ('sRf_in', 'sFf_in').
    """
    rows = []
    for cell in CELLS:
        c = correct(samp_no, cell, method, w)
        z = c["zones"]
        wz = z["white"]
        x, y = c["aUf"][wz], c["aRf"][wz]
        ok = np.isfinite(x) & np.isfinite(y)
        rows.append(dict(
            cell=cell, gamma=c["gamma"], beta=c["beta"],
            r_fine=float(np.corrcoef(x[ok], y[ok])[0, 1]),
            sR_in=_spread(c["aR"][z["inner"]]),
            sF_in=_spread(c["flat"][z["inner"]]),
            sR_rim=_spread(c["aR"][z["rim"]]),
            sF_rim=_spread(c["flat"][z["rim"]]),
            sRf_in=_spread(c["aRf"][z["inner"]]),
            sFf_in=_spread((c["aRf"] - c["beta"] * c["aUf"])[z["inner"]]),
            n_in=int(z["inner"].sum()), n_rim=int(z["rim"].sum())))
    return rows


# ── how much of the fine variation is repeatable? ─────────────────────

def pre_shared(samp_no, w) -> dict:
    """
    Fine texture shared between different LEDs in the pre-image.

    Each LED is a separate photograph, so their noises are independent;
    whatever fine variation two LEDs share is the pad itself (its texture
    and small-scale shading).  The pre-image's own grid is used -- nothing
    resampled -- over its pad eroded 1 px.

    Args:
        samp_no: Sample number.
        w: Smoothing width, px.

    Returns:
        {(cell_j, cell_k): correlation} for every pair of channels from
        different LEDs.
    """
    pad = a1.pad_of(samp_no, a1.PRE).mask(1)
    fine = {}
    for cell in CELLS:
        a = plane(samp_no, cell, a1.PRE)
        fine[cell] = a - smooth(a, pad, w)
    out = {}
    for cj, ck in combinations(CELLS, 2):
        if cj[0] == ck[0]:
            continue                      # same photograph: skip
        x, y = fine[cj][pad], fine[ck][pad]
        ok = np.isfinite(x) & np.isfinite(y)
        out[(cj, ck)] = float(np.corrcoef(x[ok], y[ok])[0, 1])
    return out


def post_repeat(samp_no, cell, w) -> dict:
    """
    Fine variation that repeats between exposures of the measurement.

    Set 2 has three exposures per LED.  Two exposures share the pad's
    texture but not their noise, so in the white zone
        repeatable variance = mean(f_i * f_j)
        noise variance      = mean((f_i - f_j)**2) / 2
    where f is the fine part.  Exposures with any clipped pad pixel are
    skipped.

    Args:
        samp_no: Sample number (set 2).
        cell: (LED, Bayer layer).
        w: Smoothing width, px.

    Returns:
        Dict 'rep' (repeatable fraction of the fine variance, averaged over
        exposure pairs), 'n_rungs', or {} if fewer than two exposures pass.
    """
    z = zones(samp_no)
    fine = []
    for rung in a1.open_sample(samp_no).labels(cell[0]):
        if a1.saturation(samp_no, cell, rung=rung) > 0:
            continue
        a = plane(samp_no, cell, "", rung)
        if a is None:
            continue
        fine.append((a - smooth(a, z["nodot"], w))[z["white"]])
    if len(fine) < 2:
        return {}
    reps = []
    for fi, fj in combinations(fine, 2):
        ok = np.isfinite(fi) & np.isfinite(fj)
        cov = float(np.mean(fi[ok] * fj[ok]))
        noise = float(np.mean((fi[ok] - fj[ok]) ** 2) / 2)
        reps.append(cov / (cov + noise))
    return dict(rep=float(np.mean(reps)), n_rungs=len(fine))


def _finish(fig, what, figs, savefigs):
    """
    Save and/or show one finished figure, then release it.

    Args:
        fig: The matplotlib figure.
        what: Short name for the file: fig/shading_study_<what>.png.
        figs: True to show the figure on screen.
        savefigs: True to write it to the file.

    Returns:
        The Path written, or None if `savefigs` is False.
    """
    import matplotlib.pyplot as plt
    out = None
    if savefigs:
        FIG.mkdir(parents=True, exist_ok=True)
        out = FIG / f"shading_study_{what}.png"
        fig.savefig(out, dpi=110)
        print(f"  wrote {out}")
    if figs:
        plt.show()
    else:
        plt.close(fig)
    return out


# ── steps ──────────────────────────────────────────────────────────────

def show_split(samps=POOL, figs=True, savefigs=False) -> dict:
    """
    Step 1a: how much of the fine variation is the pad, how much noise?

    Prints, for each width, the median correlation of fine texture
    between different LEDs in the pre-image (all pads), and the
    repeatable fraction between exposures of the measurement (set 2).

    Args:
        samps: Sample numbers.
        figs: Show the figure.
        savefigs: Write fig/shading_study_split.png.

    Returns:
        {'pre': {w: [correlations]}, 'post': {w: {cell: [fractions]}}}.
        
    Uses:
        WIDTHS  gaussian sigma (smoothing width) in pixels (1.0,2.0,4.0)
    """
    import matplotlib.pyplot as plt
    res = {"pre": {}, "post": {}}
    for w in WIDTHS:
        res["pre"][w] = [r for s in samps for r in pre_shared(s, w).values()]
        res["post"][w] = {c: [] for c in CELLS}
        for s in samps:
            if s not in SET2:  # SET2: 2mm dots w/20g pressure.  2.3 to 40 ug/l, 
                continue
            for c in CELLS:
                got = post_repeat(s, c, w)
                if got:
                    res["post"][w][c].append(got["rep"])
    print(f"{'w px':>5}  {'pre: LED-to-LED corr (median, IQR)':>36}  "
          f"{'post: repeatable fraction by channel (median over set 2)'}")
    for w in WIDTHS:
        p = np.array(res["pre"][w])
        q = [np.median(v) if v else np.nan for v in res["post"][w].values()]
        print(f"{w:5.0f}  {np.median(p):+14.3f} "
              f"({np.percentile(p, 25):+.3f} .. {np.percentile(p, 75):+.3f})   "
              + "  ".join(f"{dt.cell_name(c)} {v:.2f}"
                          for c, v in zip(CELLS, q)))
    fig, ax = plt.subplots(1, 2, figsize=(10, 3.8), layout="constrained")
    ax[0].boxplot([res["pre"][w] for w in WIDTHS],
                  tick_labels=[f"{w:g}" for w in WIDTHS])
    ax[0].axhline(0, color="0.5", lw=0.7)
    ax[0].set_xlabel("smoothing width w (px)")
    ax[0].set_ylabel("correlation of fine texture")
    ax[0].set_title("pre-image: between different LEDs\n(all pads, all LED pairs)",
                    fontsize=9)
    for c in CELLS:
        ax[1].plot(WIDTHS, [np.median(res["post"][w][c]) for w in WIDTHS],
                   "o-", label=dt.cell_name(c))
    ax[1].set_ylim(0, 1)
    ax[1].set_xlabel("smoothing width w (px)")
    ax[1].set_ylabel("repeatable fraction")
    ax[1].set_title("measurement: between exposures of one LED\n"
                    "(set 2 white zone, median over pads)", fontsize=9)
    ax[1].legend(fontsize=7)
    for a in ax:
        a.grid(alpha=0.3)
    _finish(fig, "split", figs, savefigs)
    return res


def show_registration(samps=POOL, figs=True, savefigs=False) -> dict:
    """
    Step 1b: the registrations compared, and what the correction does.

    Prints, per pad: how far each method moved the pre-image from 'arith',
    the correlation each reached, and the camera-fixed pattern
    (`sensor_fixed`).  Then, per method, medians over pads and channels of
    gamma, beta, the fine-texture correlation, and the white zone's spread
    before and after correction.  The figure shows gamma, beta and the
    rim's improvement pad by pad.

    Args:
        samps: Sample numbers.
        figs: Show the figure.
        savefigs: Write fig/shading_study_registration.png.

    Returns:
        {method: {samp: `white_stats` rows}}.
    """
    import matplotlib.pyplot as plt
    res = {m: {} for m in METHODS}
    print(f"{'pad':>6} {'ppb':>5} {'moved px':>9} | extra shift from arith "
          f"(dx, dy) and score: " + "   ".join(METHODS[1:])
          + " | camera-fixed r")
    for s in samps:
        for m in METHODS:
            res[m][s] = white_stats(s, m)
        moved = np.hypot(*arith_warp(s)[:, 2])
        parts = []
        for m in METHODS[1:]:
            d = extra_shift(s, m)
            parts.append(f"({d[0]:+.2f},{d[1]:+.2f}) {warp_of(s, m)[1]:.3f}")
        print(f"{s:>6} {ps.ppb(s):5.1f} {moved:9.2f} | " + "  ".join(parts)
              + f" | {sensor_fixed(s):+.3f}")

    keys = ("gamma", "beta", "r_fine", "sR_in", "sF_in", "sR_rim", "sF_rim",
            "sRf_in", "sFf_in")
    print(f"\nmedians over pads and channels, w = {W:g} px "
          f"(spreads in A, robust sigma)")
    print(f"{'method':>8} {'gamma':>6} {'beta':>6} {'r_fine':>6} | "
          f"{'inner':>6} {'->':>6} | {'rim':>6} {'->':>6} | "
          f"{'inner fine':>10} {'->':>6}")
    for m in METHODS:
        rows = [r for s in samps for r in res[m][s]]
        v = {k: np.median([r[k] for r in rows]) for k in keys}
        print(f"{m:>8} {v['gamma']:6.2f} {v['beta']:6.2f} {v['r_fine']:6.2f} | "
              f"{v['sR_in']:6.4f} {v['sF_in']:6.4f} | {v['sR_rim']:6.4f} "
              f"{v['sF_rim']:6.4f} | {v['sRf_in']:10.4f} {v['sFf_in']:6.4f}")

    fig, ax = plt.subplots(1, 3, figsize=(13, 3.9), layout="constrained")
    x = np.arange(len(samps))
    for k, m in enumerate(METHODS):
        off = (k - 1.5) * 0.18
        for j, key in enumerate(("gamma", "beta")):
            v = [np.median([r[key] for r in res[m][s]]) for s in samps]
            ax[j].plot(x + off, v, "o", ms=4, label=m)
        gain = [np.median([r["sF_rim"] / r["sR_rim"] for r in res[m][s]])
                for s in samps]
        ax[2].plot(x + off, gain, "o", ms=4, label=m)
    for a, t in zip(ax, ("gamma: smooth shading carried over",
                         "beta: fine texture carried over",
                         "rim spread, after / before")):
        a.set_title(t + "\n(median over the 6 channels)", fontsize=9)
        a.set_xticks(x)
        a.set_xticklabels([f"{s} ({ps.ppb(s):g})" for s in samps],
                          rotation=90, fontsize=7)
        a.grid(alpha=0.3)
        a.axhline(1, color="0.5", lw=0.7)
        a.axhline(0, color="0.5", lw=0.7)
        a.legend(fontsize=7)
    _finish(fig, "registration", figs, savefigs)
    return res


def show_surface(samps=("C1034", "C1032", "C1017", "C1029", "C1035",
                        "C1025"), figs=True, savefigs=False):
    """
    Step 1c: how sharply the fine textures line up, around 'arith'.

    One panel per pad: the correlation of the two fine textures over the
    inner white zone, for every extra shift of the pre-image within
    +/-SEARCH_PX.  Markers: 'arith' (the center, +), 'ecc_rim' (square),
    'ecc_tex' (circle), 'peak' (x).  A sharp, single peak means the
    texture is the pad's own and pins the registration; its width says how
    quickly a misregistration spoils it.

    Args:
        samps: Sample numbers, one panel each.
        figs: Show the figure.
        savefigs: Write fig/shading_study_surface.png.

    Returns:
        None.
    """
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, len(samps), figsize=(2.6 * len(samps), 3.0),
                           layout="constrained", squeeze=False)
    for a, s in zip(ax[0], samps):
        steps, surf = shift_surface(s)
        h = SEARCH_STEP / 2
        im = a.imshow(surf, origin="lower", cmap="viridis",
                      extent=(steps[0] - h, steps[-1] + h,
                              steps[0] - h, steps[-1] + h))
        a.plot(0, 0, "+", color="w", ms=10, mew=1.5)
        for m, mk in (("ecc_rim", "s"), ("ecc_tex", "o"), ("peak", "x")):
            d = extra_shift(s, m)
            a.plot(d[0], d[1], mk, mfc="none", mec="r", ms=7, mew=1.3)
        a.set_title(f"{s} ({ps.ppb(s):g} ppb)\npeak r {np.nanmax(surf):.2f}",
                    fontsize=8)
        a.set_xlabel("extra dx (px)", fontsize=8)
        fig.colorbar(im, ax=a, shrink=0.7)
    ax[0, 0].set_ylabel("extra dy (px)", fontsize=8)
    _finish(fig, "surface", figs, savefigs)


def show_maps(samps=("C1034", "C1032", "C1029", "C1025"),
              cell=("royal_blue", "B"), method="arith", figs=True,
              savefigs=False):
    """
    Step 1d (approval figure): the correction, pictured, for a few pads.

    One row per pad: the registered pre-image a_U, its smooth part scaled
    by gamma, its fine part scaled by beta, the measurement a_R, and the
    corrected a_flat.  One color scale per row, centered on 0 and set from
    the white zone, so the dot saturates and the shading shows.  Black
    line: the dot's edge; gray: the white zone's inner boundary.

    Args:
        samps: Sample numbers, one row each.
        cell: The channel shown.
        method: Registration, see `warp_of`.
        figs: Show the figure.
        savefigs: Write fig/shading_study_maps.png.

    Returns:
        None.
    """
    import matplotlib.pyplot as plt
    cols = ("a_U (pre-image)", "gamma x smooth(a_U)", "beta x fine(a_U)",
            "a_R (measurement)", "a_flat = a_R - both")
    fig, ax = plt.subplots(len(samps), 5, figsize=(12, 2.9 * len(samps)),
                           layout="constrained", squeeze=False)
    for i, s in enumerate(samps):
        c = correct(s, cell, method)
        z = c["zones"]
        ys, xs = np.nonzero(z["pad"])
        box = (slice(ys.min() - 2, ys.max() + 3),
               slice(xs.min() - 2, xs.max() + 3))
        lim = 4 * _spread(c["aR"][z["white"]])
        imgs = (c["aU"], c["gamma"] * c["aUs"], c["beta"] * c["aUf"],
                c["aR"], c["flat"])
        for j, img in enumerate(imgs):
            v = np.where(z["pad"], img, np.nan)[box]
            im = ax[i, j].imshow(v, cmap="RdBu_r", vmin=-lim, vmax=lim)
            ax[i, j].contour(z["dist"][box], levels=[0], colors="k",
                             linewidths=0.6)
            ax[i, j].contour(z["dist"][box], levels=[WHITE_GAP],
                             colors="0.5", linewidths=0.6)
            ax[i, j].set_xticks([])
            ax[i, j].set_yticks([])
            if i == 0:
                ax[i, j].set_title(cols[j], fontsize=9)
        ax[i, 0].set_ylabel(f"{s}  {ps.ppb(s):g} ppb\ngamma {c['gamma']:.2f}"
                            f"  beta {c['beta']:.2f}", fontsize=8)
        fig.colorbar(im, ax=ax[i, :], shrink=0.8, label="A")
    fig.suptitle(f"{dt.cell_name(cell)}, registration '{method}', "
                 f"w = {W:g} px", fontsize=10)
    _finish(fig, "maps", figs, savefigs)


def run():
    """
    The procedural steps, each behind an `if 1:` / `if 0:` guard.

    Returns:
        None.  Figures go to fig/ and tables are printed.
    """
    # 1a. How much of the fine variation is the pad itself, how much noise?
    if 1:
        print("\n=== step 1a: show_split")
        show_split(figs=True, savefigs=True)

    # 1b. The three registrations, and what the correction does.
    if 1:
        print("\n=== step 1b: show_registration")
        show_registration(figs=True, savefigs=True)

    # 1c. How sharply the textures line up around 'arith'.
    if 1:
        print("\n=== step 1c: show_surface")
        show_surface(figs=True, savefigs=True)

    # 1d. The correction pictured on four pads (approve by eye).
    if 1:
        print("\n=== step 1d: show_maps")
        show_maps(figs=True, savefigs=True)

    a1.close_all()


if __name__ == "__main__":
    run()
