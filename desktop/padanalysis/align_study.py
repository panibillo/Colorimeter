"""
align_study.py -- step 1 of the chain proof of concept: how well does lining
up put the same paper on the same pixel?

Plan: docs/work_plan_chain_poc_2026-11-09.md, section 1.
Chain: tex/chain_unified.tex, step 4 (line up).
Figure: tex/figs/step1_edgefit_error.png.

THE QUESTION
    The chain divides each post-image pixel by the pre-image pixel that saw
    the same piece of paper.  The strip is taken out and put back between
    the two photos, so the pad moves a little.  `analysis_1.pre_to_post`
    undoes that movement using only the pad's fitted edges (left side, its
    tilt, and the top).  Does that leave the two photos misaligned, and how
    much does a leftover misalignment matter near the pad's edge?

    It could change: whether lining up needs a texture-based refinement.
    It ends when: the false change (defined below) is under 0.002 A beyond
    the outermost edge band.  If not, try one refinement, then stop.

THE MEASURING STICK: THE PAPER'S TEXTURE
    The paper has its own fixed pixel-to-pixel pattern (texture).  It moves
    with the strip and is the same in every photo of that strip.  So if the
    texture of the post-image matches the pre-image's texture best after an
    extra shift (dy, dx), the edge fits were off by (dy, dx).  This measure
    does not depend on the edge fits at all.

    The texture is read in photo_red.R, because the yellow dye barely
    absorbs red light, so the texture is still visible after exposure to
    arsine.

WORDS AND SYMBOLS USED BELOW
    sample      a test, e.g. "C1041".
    repeat file one capture file (.h5) of a sample, e.g. "C1041rep3".  Each
                holds one pre-image and one post-image of a strip.  The
                strip is reseated between repeats (Bill, 2026-11-09), so a
                pre-image from one repeat file can be paired with the
                post-image of another.  n repeat files give n x n pairs.
    pre_file, post_file
                the repeat file the pre-image or post-image is taken from.
    sec         which photo of a file: '' for the post-image, a1.PRE
                ('pre') for the pre-image.
    cell        here, a channel: (LED, Bayer layer), e.g. ("photo_red", "R").
    layer px    one pixel of a Bayer layer's grid: every other sensor pixel
                in each direction.  All positions and shifts are in layer px.
    y, x        row (down the image) and column (across the image).
    u, v        position measured from the pad's own edges: u across the
                strip from its left side, v down the pad from its top.
    edge map    for each post-image pixel, the pre-image position the edge
                fits say holds the same paper (`edge_map`).
    residual    (dy, dx): the extra shift, beyond the edge map, that makes
                the two textures match best (`residual`).  This is what the
                edge fits got wrong.
    edge-fit error
                how far one photo's fitted pad sits from where its texture
                says it is (`split_errors`).  A residual is the post-image's
                edge-fit error minus the pre-image's.
    R           reflectance: the pixel divided by the gray-chip level there.
                Bigger means lighter.
    A           absorbance, -log10(R).  Bigger means darker (more dye).
    false change
                the change in A caused only by misalignment, not by dye
                (`false_change`).  Positive reads darker than the truth.
    edge band   band k of an edge is the pixels between k and k+1 layer px
                in from that edge: band 0 is the outermost row of pixels.

THE STEPS (each is a guarded block in `run()`)
    1a `all_pairs`:        residual for every pre-image x post-image pair.
    1b `split_errors`:     each photo's own edge-fit error, from 1a.
    1c `check_correction`: apply the 1a residual as a correction, then
                           measure what is left in another channel, and the
                           false change at the edge before and after.
    figure `show_split`:   the edge-fit errors from 1b.

RESULTS SO FAR (2026-11-09, before C1045rep3 and rep4 were added)
    - Pre-image edge fits agree with each other to about 0.1 px.
    - Post-image edge fits sit 0.1-0.4 px too high on the pad, and up to
      0.7 px too far left in C1041 and C1044, where dye reaches the edge.
    - The left and right halves of the pad shift equally: no stretch.
    - The interior false change is under 0.0001 A.  Band 1 changes about
      0.1 A per px of misalignment, so the stop rule needs about 0.02 px,
      finer than the texture can measure.  Over the whole pad the false
      change mostly cancels (0.0025 A or less, worst 0.009 A in C1044).

    CAUTION, 2026-11-10: with the 6-row limit in `padrect._find_sides`,
    the post-images of C1045rep3 and C1045rep4 are fitted as the "channel"
    layout on 7 rows (side-fit rms 2.4 px, width 38.9 px against 41.0),
    while every other C1045 photo is the "window" layout.  Their texture
    correlation `corr` is about 0.0-0.1, against 0.6 elsewhere: the
    texture match failed, so their residuals are not measurements.

Run with:  timeout -k 10 1800 ../../.venv/bin/python3 -u align_study.py
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi
from scipy import optimize

import analysis_1 as a1
import shading_review as sr

HERE = Path(__file__).resolve().parent
OUT = HERE / "align_out"                       # the .json tables of results
FIGS = HERE.parents[1] / "tex" / "figs"        # figures for the write-up

#: The repeat files used, by holder and sample.  Only files that hold both a
#: pre-image and a post-image are listed; files with only a pre-image
#: (runs that were restarted) are left out.  Holders: F (C1038-C1042),
#: H1 (C1044), G1 (C1045).  Older holders are not used.
SETS = {
    "F": {"C1038": ["C1038", "C1038rep2", "C1038rep3", "C1038rep4", "C1038rep5"],
          "C1039": ["C1039", "C1039rep2", "C1039rep3", "C1039rep4", "C1039rep5"],
          "C1040": ["C1040", "C1040rep2", "C1040rep3", "C1040rep4", "C1040rep5"],
          "C1041": ["C1041", "C1041rep2", "C1041rep3", "C1041rep4", "C1041rep5"],
          "C1042": ["C1042-b", "C1042rep2-a"]},
    "H": {"C1044": ["C1044-e", "C1044rep2-b"]},
    "G": {"C1045": ["C1045-a", "C1045rep2-a", "C1045rep3", "C1045rep4"]},
}
#: Channel the texture is matched in.  The yellow dye barely absorbs red
#: light, so the paper texture survives in the post-image.
CELL = ("photo_red", "R")
#: A second channel, used only to check the correction found in CELL (a
#: channel checked against itself would always look perfect).  green.B is
#: the only other pre-image channel not clipped from C1039 on.  C1038's
#: pre-image has no green.B layer, so C1038 drops out of the check.
CHECK_CELL = ("green", "B")
#: Width, in layer px, of the gaussian smoothing that is subtracted to leave
#: the texture.  Patterns finer than about this survive; shading and the
#: dye's broad changes are removed.
SMOOTH_PX = 2.0
#: Number of edge bands reported per edge (bands 0, 1, 2).
BANDS = 3
#: A band pixel closer than this (layer px) to one of the two neighboring
#: edges is left out, so that each band measures one edge, not a corner.
CORNER_PX = 3
#: The 'inner' region of `false_change`: at least this far (layer px) from
#: every edge.
INNER_EDGE_PX = 4

#: Results of `texture`, kept so a photo is read only once per run.
#: Key (file, cell, sec); value as `texture` returns.
_tex_cache = {}


# ── texture ────────────────────────────────────────────────────────────

def texture(samp, cell, sec):
    """
    The paper texture of one photo.

    Goal: isolate the paper's fixed pixel-to-pixel pattern, so that two
    photos of the same strip can be matched by it.  The pattern rides on
    top of slow changes (shading toward the rim, the dye's broad darkening).
    Subtracting a smoothed copy removes those slow changes and keeps the
    fine pattern.

    How:
        1. Read R (pixel / gray-chip level) for this photo and channel.
           Hot pixels become NaN.
        2. `pad`: pixels on the fitted pad with a usable R.
        3. `L` = log10(R) on the pad, 0 elsewhere.  (log10(R) = -A, so the
           texture is in absorbance units with the sign flipped.)  The log
           makes a 1% pattern the same size on a light and a dark pixel.
        4. `sm`, the smoothed copy: gaussian smoothing of L, divided by the
           same smoothing of the pad mask (`w`).  The division is a
           weighted average over pad pixels only, so pixels off the pad
           (set to 0) don't pull the smooth down near the edge.
        5. T = L - sm on the pad, NaN off it.

    Args:
        samp: scalar str, repeat file name without .h5, e.g. "C1041rep3".
        cell: (LED, Bayer layer), e.g. ("photo_red", "R").
        sec: '' for the post-image, `a1.PRE` for the pre-image.

    Returns:
        (T, inner, R), all arrays (rows, cols) on this photo's own layer
        grid:
            T      float, the texture, NaN off the pad.  A typical size is
                   about 0.007 (log10 units, the same as A).
            inner  bool, the pad shrunk by `shading_review.INNER_PX` (4 px)
                   from each edge, where T is finite.  The texture is
                   matched only here, away from the steep edge shading.
            R      float, the reflectance, hot pixels NaN.
    """
    key = (samp, cell, sec)
    if key in _tex_cache:
        return _tex_cache[key]
    f = sr.frame(samp, cell, sec)           # dict: 'R', 'pad', 'inner', ...
    R = np.array(f["R"], float)
    R[a1._hot(samp, sec)] = np.nan
    pad = f["pad"] & np.isfinite(R) & (R > 0)
    L = np.where(pad, np.log10(np.where(pad, R, 1.0)), 0.0)
    w = ndi.gaussian_filter(pad.astype(float), SMOOTH_PX)   # share of pad
    sm = ndi.gaussian_filter(L, SMOOTH_PX) / np.where(w > 0, w, 1.0)
    T = np.where(pad, L - sm, np.nan)
    out = (T, f["inner"] & np.isfinite(T), R)
    _tex_cache[key] = out
    return out


# ── lining up across files ─────────────────────────────────────────────

def edge_map(post_file, pre_file):
    """
    For every post-image pixel, the pre-image position that the edge fits
    say holds the same paper.

    Goal: reproduce `analysis_1.pre_to_post` exactly, but allow the
    pre-image to come from a different repeat file than the post-image.
    That is what lets one sample give n x n pairs instead of n.

    Each photo's fitted pad (`a1.pad_of`, a `padrect.PadRect`) supplies:
        aL   the column of the left side, on row yc;
        m    the tilt of the sides: columns moved per row down;
        yc   the row at which aL is given;
        top  the row of the pad's top edge.
    (The fit's height is fixed, so the top edge is set by the bottom edge.
    The width is not used here.)

    The map, for a post-image pixel at row y, column x:
        u     = x - (aL_post + m_post * (y - yc_post))
                  distance across the strip from the post pad's left side;
        y_pre = top_pre + (y - top_post)
                  the same depth below the top edge, in the pre-image;
        x_pre = aL_pre + u + m_pre * (y_pre - yc_pre)
                  the same distance from the left side, in the pre-image.

    Args:
        post_file: scalar str, repeat file giving the post-image.
        pre_file: scalar str, repeat file giving the pre-image (may equal
            post_file).

    Returns:
        (y_pre, x_pre): float arrays (rows, cols) shaped like the post-image
        layer grid.  Entry [y, x] is the pre-image row and column (with
        fractions) for post-image pixel (y, x).
    """
    post, pre = a1.pad_of(post_file, ""), a1.pad_of(pre_file, a1.PRE)
    rows, cols = a1.layer_shape(post_file, "")
    yy, xx = np.mgrid[0:rows, 0:cols].astype(np.float64)   # row, col of each px
    u = xx - (post.aL + post.m * (yy - post.yc))
    y_pre = pre.top + (yy - post.top)
    x_pre = pre.aL + u + pre.m * (y_pre - pre.yc)
    return y_pre, x_pre


def pad_coords(post_file):
    """
    Each post-image pixel's position measured from the post pad's edges.

    Goal: give every pixel its distance from each edge, so `false_change`
    can sort pixels into edge bands and `residual` can split the pad into
    left and right halves.

    Args:
        post_file: scalar str, repeat file giving the post-image.

    Returns:
        (u, v, width, height):
            u       array (rows, cols), layer px across the strip from the
                    left side (0 on the left side, width on the right);
            v       array (rows, cols), layer px down from the top edge
                    (0 on the top edge, height on the bottom);
            width   scalar, the fitted pad width, layer px;
            height  scalar, the fitted pad height, layer px.
    """
    p = a1.pad_of(post_file, "")
    rows, cols = a1.layer_shape(post_file, "")
    yy, xx = np.mgrid[0:rows, 0:cols].astype(np.float64)
    u = xx - (p.aL + p.m * (yy - p.yc))
    return u, yy - p.top, p.width, p.bot - p.top


def residual(post_file, pre_file, cell=CELL, extra=(0.0, 0.0), half=None):
    """
    The misalignment the edge map leaves, measured from the texture.

    Goal: find how far the edge map is off for this pair of photos.  Line
    up the pre-image texture with `edge_map`, then search for the extra
    shift (dy, dx) that makes it match the post-image texture best.  That
    shift is the residual.

    How:
        1. T_pre, T_post: the texture of each photo (`texture`).
        2. Pick the pixels to compare: the post-image's inner pad, or one
           half of it (`half`).
        3. For a trial shift q = (dy, dx), read the pre-image texture at
           the edge-map position + `extra` + q.  Positions fall between
           pixels, so the value is interpolated in a straight line from the
           four neighbors (`map_coordinates`, order=1).  Off the pad, the
           pre-image texture counts as 0.
        4. cost(q) = mean of (T_post - shifted T_pre)^2 over those pixels.
           Smaller is a better match.
        5. Try q on a coarse grid, -2 to +2 px in steps of 0.5 px, in both
           directions.  Start from the best grid point and refine with the
           Nelder-Mead search (it tries a small triangle of nearby points,
           moves toward the lowest cost, and shrinks; no derivatives needed)
           until q is known to 0.005 px.

    Args:
        post_file: scalar str, repeat file giving the post-image.
        pre_file: scalar str, repeat file giving the pre-image.
        cell: (LED, Bayer layer) whose texture is matched.
        extra: (dy, dx), layer px, added to the edge map before the search.
            Use it to test a correction: the result is then what is left
            over after that correction.
        half: None for the whole inner pad; 'L' or 'R' for its left or
            right half only (used to test for stretch: a pad that swelled
            would give its two halves different dx).

    Returns:
        dict:
            dy, dx  the residual, layer px.  Positive means the paper sits
                    that much further down (dy) or right (dx) in the
                    pre-image than the edge map says.
            corr0   correlation between the two textures with no extra
                    shift (1 = identical pattern, 0 = unrelated);
            corr    the same after the shift.  Read this first: if corr
                    is low (about 0.1 or less), the textures were never
                    matched and dy, dx mean nothing.
    """
    Tpre, _, _ = texture(pre_file, cell, a1.PRE)
    Tpost, inpost, _ = texture(post_file, cell, "")
    y_pre, x_pre = edge_map(post_file, pre_file)
    region = inpost.copy()                       # post pixels compared
    if half:
        u, _, W, _ = pad_coords(post_file)
        region &= (u < W / 2) if half == "L" else (u >= W / 2)
    yy, xx = np.nonzero(region)
    a = Tpost[yy, xx]                            # post texture, 1-D list
    P = np.nan_to_num(Tpre)                      # pre texture, 0 off the pad
    ys, xs = y_pre[yy, xx] + extra[0], x_pre[yy, xx] + extra[1]

    def lined(q):
        """Pre-image texture at the compared pixels, moved by q=(dy, dx)."""
        return ndi.map_coordinates(P, [ys + q[0], xs + q[1]], order=1,
                                   mode="nearest")

    def cost(q):
        """Mean squared texture difference for shift q.  Smaller = better."""
        return np.mean((a - lined(q)) ** 2)

    grid = [(dy, dx) for dy in np.arange(-2, 2.01, 0.5)
            for dx in np.arange(-2, 2.01, 0.5)]
    r = optimize.minimize(cost, min(grid, key=cost), method="Nelder-Mead",
                          options=dict(xatol=0.005, fatol=1e-12))
    return dict(dy=float(r.x[0]), dx=float(r.x[1]),
                corr0=float(np.corrcoef(a, lined((0, 0)))[0, 1]),
                corr=float(np.corrcoef(a, lined(r.x))[0, 1]))


def false_change(post_file, pre_file, shift, cell=CELL, base=(0.0, 0.0)):
    """
    The false change in absorbance that a misalignment causes, by edge band.

    Goal: turn a misalignment in pixels into what it does to the answer, in
    A.  The chain divides by the pre-image brightness W.  In the interior W
    is nearly flat, so a small shift barely changes it.  Near an edge W
    falls steeply (rim shading), so even a fraction of a pixel picks up a
    noticeably brighter or darker W.

    How:
        W_used  the pre-image R read at the edge-map position + `base`
                (what the chain would divide by);
        W_true  the pre-image R read `shift` further on (what it should
                divide by, if `shift` is the true leftover misalignment).
        A_used - A_true = -log10(post / W_used) + log10(post / W_true)
                        = -log10(W_true / W_used)  =: dA.
        The post-image cancels, so dA needs only the pre-image.  dA is then
        averaged over each edge band and over the interior.

    Args:
        post_file: scalar str, repeat file giving the post-image (its pad
            sets the bands).
        pre_file: scalar str, repeat file giving the pre-image.
        shift: (dy, dx), layer px, the misalignment to evaluate.
        cell: (LED, Bayer layer) whose pre-image R is used.
        base: (dy, dx), layer px, a correction already applied to the edge
            map, so W_used includes it.

    Returns:
        dict of band means of dA (A units; positive reads darker than the
        truth):
            'L0', 'L1', 'L2', 'R0', ..., 'B2'
                edge (Left, Right, Top, Bottom) and band number 0, 1, 2.
                Band k holds post pad pixels between k and k+1 layer px in
                from that edge, except those within CORNER_PX of the two
                neighboring edges.  NaN if a band is empty.
            'inner'
                pixels at least INNER_EDGE_PX from every edge.
    """
    _, _, R = texture(pre_file, cell, a1.PRE)
    y_pre, x_pre = edge_map(post_file, pre_file)
    y0, x0 = y_pre + base[0], x_pre + base[1]
    used = ndi.map_coordinates(R, [y0, x0], order=1, mode="nearest")
    true = ndi.map_coordinates(R, [y0 + shift[0], x0 + shift[1]],
                               order=1, mode="nearest")
    with np.errstate(invalid="ignore", divide="ignore"):
        dA = -np.log10(true / used)
    u, v, W, H = pad_coords(post_file)
    # dist[e]: each pixel's distance in from edge e, layer px.
    dist = {"L": u, "R": W - u, "T": v, "B": H - v}
    inside = (u >= 0) & (u < W) & (v >= 0) & (v < H) & np.isfinite(dA)
    res = {}
    for e, d in dist.items():
        # o1, o2: distances from the two edges that meet edge e at corners
        # (top and bottom for a side; left and right for top or bottom).
        o1, o2 = [dist[o] for o in ("TB" if e in "LR" else "LR")]
        away = (o1 >= CORNER_PX) & (o2 >= CORNER_PX)
        for k in range(BANDS):
            m = inside & away & (d >= k) & (d < k + 1)
            res[f"{e}{k}"] = float(np.mean(dA[m])) if m.any() else float("nan")
    m = inside & (u >= INNER_EDGE_PX) & (W - u >= INNER_EDGE_PX) \
        & (v >= INNER_EDGE_PX) & (H - v >= INNER_EDGE_PX)
    res["inner"] = float(np.mean(dA[m]))
    return res


# ── the study steps ────────────────────────────────────────────────────

def all_pairs(sets=SETS):
    """
    Step 1a: the residual for every pre-image x post-image pair.

    Goal: collect many independent looks at the edge fits.  A pair from the
    same repeat file is the chain's own case (`pre_to_post`).  Pairs across
    repeat files add more: because the strip was reseated, the edge fits
    must also follow a real move, and if they do, the residual still
    measures only the fits' errors.  `split_errors` then separates those
    errors photo by photo.

    For each pair it also records:
        dxL, dxR    the residual dx from the left and right halves of the
                    pad.  Equal means a plain shift.  Different means the
                    paper stretched or shrank across the strip.
        w_pre, w_post
                    the fitted pad widths, layer px.  Compare with dxR - dxL
                    to tell a real width change from a fit artifact.

    Args:
        sets: dict, holder -> sample -> list of repeat files (as SETS).

    Returns:
        list of dicts, one per (pre_file, post_file) pair:
            holder, sample, pre, post   names;
            same                        True when pre == post;
            dy, dx, corr0, corr         from `residual`, whole inner pad;
            dxL, dxR, w_pre, w_post     as above.
        A pair whose files fail (no pre-image, pad not found, ...) is
        printed and skipped.
    """
    rows = []
    for hold, samples in sets.items():
        for samp, files in samples.items():
            for post in files:
                for pre in files:
                    try:
                        r = residual(post, pre)
                        r["dxL"] = residual(post, pre, half="L")["dx"]
                        r["dxR"] = residual(post, pre, half="R")["dx"]
                        r["w_pre"] = float(a1.pad_of(pre, a1.PRE).width)
                        r["w_post"] = float(a1.pad_of(post, "").width)
                    except Exception as e:     # noqa: BLE001 -- report, go on
                        print(f"  skip {pre} -> {post}: {type(e).__name__}: {e}")
                        continue
                    r.update(holder=hold, sample=samp, pre=pre, post=post,
                             same=(pre == post))
                    rows.append(r)
                    print(f"  {hold} {pre:12s} -> {post:12s} dy {r['dy']:6.2f} "
                          f"dx {r['dx']:6.2f}  dxL {r['dxL']:6.2f} dxR {r['dxR']:6.2f}")
            a1.close_all()                     # free the sample's files
    return rows


def split_errors(rows):
    """
    Step 1b: each photo's own edge-fit error, from all of a sample's pairs.

    Goal: a residual mixes two photos' errors.  With every pre-image paired
    with every post-image, each photo appears in several pairs, which is
    enough to separate them.

    The model, for pre-image i and post-image j of one sample:
        r_ij = a_j - b_i + noise
            r_ij  the residual of the pair (dy or dx, done separately);
            a_j   post-image j's edge-fit error;
            b_i   pre-image i's edge-fit error;
            noise the texture measurement's own scatter.
        An edge-fit error is (fitted position - position the texture
        says), so a negative value means the fit put the pad too high (dy)
        or too far left (dx).

    Adding the same number to every a and every b leaves each difference
    unchanged, so the pairs can't tell where zero is.  We fix it by asking
    the b's to average 0: every error is then measured from the average
    pre-image.

    Solving by least squares: write the unknowns as one list
    x = (a_1 .. a_n, b_1 .. b_m), n post-images and m pre-images.  Each
    pair is one row of a matrix A with +1 in its post-image's column and
    -1 in its pre-image's column, so (A x) for that row is a_j - b_i.  One
    more row of m ones under the b columns, with target 0, holds the
    average of b at 0.  `lstsq` finds the x that makes the squared misfit
    over all pairs smallest.

    Small example, two repeat files (n = m = 2): four pairs,
        row for (pre 1, post 1):  [ 1  0 | -1  0 ]
        row for (pre 2, post 1):  [ 1  0 |  0 -1 ]
        row for (pre 1, post 2):  [ 0  1 | -1  0 ]
        row for (pre 2, post 2):  [ 0  1 |  0 -1 ]
        extra row, target 0:      [ 0  0 |  1  1 ]
    Four pairs, three free unknowns (4 minus the one fixed by the extra
    row), so one degree of freedom is left to measure the noise.

    Args:
        rows: list of dicts from `all_pairs`.

    Returns:
        dict, sample -> {
            'pre':  list of pre-image file names, in the order of b;
            'post': list of post-image file names, in the order of a;
            'dy', 'dx': each a dict with
                a    list, each post-image's edge-fit error, layer px;
                b    list, each pre-image's edge-fit error, layer px;
                rms  the scatter of one residual the model can't explain,
                     layer px: the texture measurement's noise.  It is
                     (sum of squared misfits / degrees of freedom) ** 0.5,
                     degrees of freedom = pairs - (n + m - 1).
        }
    """
    out = {}
    by = {}                                    # sample -> its pairs
    for r in rows:
        by.setdefault(r["sample"], []).append(r)
    for samp, rs in by.items():
        pres = sorted({r["pre"] for r in rs})
        posts = sorted({r["post"] for r in rs})
        res = {"pre": pres, "post": posts}
        for key in ("dy", "dx"):
            A, y = [], []                      # matrix rows, targets
            for r in rs:
                row = np.zeros(len(posts) + len(pres))
                row[posts.index(r["post"])] = 1.0
                row[len(posts) + pres.index(r["pre"])] = -1.0
                A.append(row)
                y.append(r[key])
            A.append(np.r_[np.zeros(len(posts)), np.ones(len(pres))])
            y.append(0.0)                      # average of b is 0
            A, y = np.array(A), np.array(y)
            x, *_ = np.linalg.lstsq(A, y, rcond=None)
            fit = A[:-1] @ x                   # model residual for each pair
            dof = max(len(rs) - (len(posts) + len(pres) - 1), 1)
            rms = float(np.sqrt(np.sum((y[:-1] - fit) ** 2) / dof))
            res[key] = dict(a=x[:len(posts)].tolist(), b=x[len(posts):].tolist(),
                            rms=rms)
        out[samp] = res
    return out


def check_correction(rows, check_cell=CHECK_CELL):
    """
    Step 1c: does correcting by the texture residual help, and how much?

    Goal: two questions.
        1. Is the residual found in photo_red.R a real shift of the paper?
           If so, after correcting by it, a different channel should find
           almost nothing left over.
        2. How much does the correction reduce the false change at the
           edges?

    How, for each pair from step 1a:
        1. Correct the edge map by the photo_red.R residual (dy, dx), then
           measure the residual left over in `check_cell` (ldy, ldx).
        2. Before: the false change if no correction is made.  The red
           residual stands in for the true misalignment:
           `false_change(shift = (dy, dx))`.
        3. After: the false change once the correction is made, with the
           leftover as the remaining misalignment:
           `false_change(shift = (ldy, ldx), base = (dy, dx))`.
        Both false changes use `check_cell`'s pre-image R.

    The leftover also includes `check_cell`'s own measurement noise, so it
    overstates what is truly left.

    Args:
        rows: list of dicts from `all_pairs`.
        check_cell: (LED, Bayer layer) used for the check.

    Returns:
        list of dicts, one per pair that could be checked:
            pre, post, holder, sample   names;
            dy, dx       the photo_red.R residual (the correction), layer px;
            ldy, ldx     the leftover in check_cell, layer px;
            before_<band>, after_<band>
                         `false_change` band means, A units, for every band
                         key ('L0' .. 'B2', 'inner').
        Pairs that fail (C1038, whose pre-image lacks green.B) are printed
        and skipped.
    """
    out = []
    for r in rows:
        try:
            left = residual(r["post"], r["pre"], cell=check_cell,
                            extra=(r["dy"], r["dx"]))
            before = false_change(r["post"], r["pre"], (r["dy"], r["dx"]),
                                  cell=check_cell)
            after = false_change(r["post"], r["pre"],
                                 (left["dy"], left["dx"]), cell=check_cell,
                                 base=(r["dy"], r["dx"]))
        except Exception as e:                 # noqa: BLE001 -- report, go on
            print(f"  skip {r['pre']} -> {r['post']}: {type(e).__name__}: {e}")
            continue
        d = dict(pre=r["pre"], post=r["post"], holder=r["holder"],
                 sample=r["sample"], dy=r["dy"], dx=r["dx"],
                 ldy=left["dy"], ldx=left["dx"])
        for k in before:
            d["before_" + k] = before[k]
            d["after_" + k] = after[k]
        out.append(d)
        print(f"  {r['pre']:12s} -> {r['post']:12s} red ({r['dy']:5.2f},{r['dx']:5.2f})"
              f"  leftover in {a1.cell_name(check_cell)} ({left['dy']:5.2f},{left['dx']:5.2f})")
    return out


#: Figure style per holder: (color, marker, legend label).
HOLDER_STYLE = {"F": ("#2a78d6", "o", "holder F (cassette 01)"),
                "G": ("#eb6834", "s", "holder G1"),
                "H": ("#1baf7a", "^", "holder H1")}


def show_split(split, rows, savefigs=True):
    """
    Figure for step 1b: each photo's own edge-fit error.

    Goal: show at a glance whether the pre-image and post-image edge fits
    are equally good, and whether any sample or holder stands out.

    Layout: two stacked panels sharing the sample axis.  Top panel: down
    the pad (dy).  Bottom panel: across the pad (dx).  For each sample, one
    column of open markers (its pre-images, the b's) and beside it one
    column of filled markers (its post-images, the a's).  Marker shape and
    color give the holder.  The gray band is +/-0.1 px.  Values are layer
    px, measured from the sample's average pre-image; negative means the
    edge fit put the pad too high (dy) or too far left (dx).

    Args:
        split: dict from `split_errors`.
        rows: list of dicts from `all_pairs` (used only to look up each
            sample's holder).
        savefigs: True to save tex/figs/step1_edgefit_error.png.

    Returns:
        The matplotlib figure.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    holder = {r["sample"]: r["holder"] for r in rows}
    samples = list(split)
    fig, axs = plt.subplots(2, 1, figsize=(7.4, 6.8), sharex=True)
    for ax, key, title in zip(axs, ("dy", "dx"),
                              ("Down the pad (dy)", "Across the pad (dx)")):
        ax.axhspan(-0.1, 0.1, color="#e8e7e2", zorder=0)
        ax.axhline(0, color="#a3a29b", lw=0.8)
        for i, s in enumerate(samples):        # i: the sample's x position
            c, m, _ = HOLDER_STYLE[holder[s]]
            b, a = split[s][key]["b"], split[s][key]["a"]
            ax.plot(np.full(len(b), i - 0.15), b, m, ms=8, mfc="white",
                    mec=c, mew=1.6, zorder=3)  # pre-images, open
            ax.plot(np.full(len(a), i + 0.15), a, m, ms=8, color=c,
                    mec="white", mew=1.2, zorder=3)  # post-images, filled
        ax.set_title(title, loc="left", fontsize=10)
        ax.set_ylabel("edge-fit error, layer px")
        ax.grid(axis="y", color="#ecebe6", lw=0.8)
        ax.set_axisbelow(True)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    axs[-1].set_xticks(range(len(samples)))
    axs[-1].set_xticklabels(samples)
    from matplotlib.lines import Line2D
    handles = [Line2D([], [], ls="", marker="o", mfc="white", mec="#55544f",
                      ms=8, label="pre-image (open)"),
               Line2D([], [], ls="", marker="o", color="#55544f", mec="white",
                      ms=8, label="post-image (filled)")]
    handles += [Line2D([], [], ls="", marker=m, color=c, ms=8, label=lab)
                for c, m, lab in HOLDER_STYLE.values()]
    fig.legend(handles=handles, fontsize=8, frameon=False, ncol=5,
               loc="upper left", bbox_to_anchor=(0.02, 0.925))
    fig.suptitle("Holders F, G, H, photo_red.R: each photo's edge-fit error, "
                 "measured against the paper texture\n(all pre x post pairs; "
                 "shaded band = +/-0.1 px)", fontsize=10, x=0.02, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.89))
    if savefigs:
        FIGS.mkdir(parents=True, exist_ok=True)
        fig.savefig(FIGS / "step1_edgefit_error.png", dpi=110)
        print(f"fig saved to {FIGS / 'step1_edgefit_error.png'}")
    return fig


def _save(name, obj):
    """Write `obj` to align_out/<name>.json."""
    OUT.mkdir(exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(obj, indent=1))


def _load(name):
    """Read align_out/<name>.json back."""
    return json.loads((OUT / f"{name}.json").read_text())


def run():
    """
    The study, step by step.  Turn each step on (1) or off (0).

    Steps 1b, 1c and the figure read align_out/pairs.json, so step 1a must
    have been run once, and again after any change to SETS or to the pad
    fits.  1a and 1c are the slow ones: they read the photos.

        1a  all_pairs         -> align_out/pairs.json
        1b  split_errors      -> align_out/split.json
        1c  check_correction  -> align_out/check.json
        fig show_split        -> tex/figs/step1_edgefit_error.png
    """
    if 0:   # 1a: residual for all n x n pre x post pairs
        rows = all_pairs()
        _save("pairs", rows)
    if 1:   # 1b: edge-fit error per photo, and the measurement noise
        _save("split", split_errors(_load("pairs")))
    if 1:   # 1c: correction checked in an independent channel
        _save("check", check_correction(_load("pairs")))
    if 1:   # figure for 1b
        show_split(_load("split"), _load("pairs"))


if __name__ == "__main__":
    sys.exit(run())
    print ("//////////////// DONE ////////////////")
