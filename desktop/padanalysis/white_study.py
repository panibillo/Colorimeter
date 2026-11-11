"""
white_study.py -- step 2 of the chain proof of concept: the white each
post-image pixel is divided by.

Plan: docs/work_plan_chain_poc_2026-11-09.md, section 2.
Chain: tex/chain_unified.tex, step 5 (the white).

THE QUESTION
    Can the edge bands plus the blend replace the plain lined-up pre-image
    as the divisor, and does it matter?

    It could change: how the white W-hat is built (step 5).
    It ends with: one number per pad, how much the bands and the blend
    change its mean A, compared with the replicate spread.

THE PARTS (each is a guarded block in `run()`)
    2a  `band_profiles`: the edge factor of each band of each edge, from the
        pre-images.  How many bands are needed, and does the corner rule
        (product of the two edges' factors) hold?          (done 2026-11-11)
    2a-ii  `fit_all_models`, `test_models`: saved edge models (decay with
        distance from the edge, fitted over all F/G/H pre-images), tested
        on held-out samples against each photo's own band means; corner
        options.            (done 2026-11-11; models dropped for now)
    2b  `noise_still`, `noise_rungs`, `noise_reseated`: camera noise
        sigma_delta' from repeats taken without moving the cartridge, from
        two exposures of one photo, and from reseated pre-images.
                                                           (done 2026-11-11)
    2c  `texture_still`, `texture_pre`, `lambda_still`, `lambda_reseated`:
        texture the model white lacks, sigma_eta, and the best blend weight
        lambda, by the formula and by direct trial.       (done 2026-11-11)
    2d  post-images: edge bands with dye at the edge against without (open
        item 3), and how much texture carries into the dye (open item 4).
                                                             (not yet)

WORDS AND SYMBOLS
    W       the pre-image's reflectance R at a pixel: signal divided by the
            gray-chip level there.  Bigger means lighter.  Read in the
            pre-image's own pixels: no lining up is needed for 2a.
    S'      the smooth part: what W would be with no edge and average
            texture.  Here a plane, a + b*u + c*v, fitted to W over the
            pad's interior (INTERIOR_PX or more from every edge).  The
            document allows a constant or a plane; the plane's tilt is
            reported (`slope_pct`) so we can see whether it matters.
    u, v    position from the pad's fitted edges, layer px: u across from
            the left side, v down from the top edge.
    band k of an edge
            pad pixels whose center lies between k and k+1 layer px in
            from that edge.  Band 0 is the outermost.  Pixels closer than
            BAND_CLEAR_PX to either neighboring edge are left out, so each
            band sees one edge only.
    l_k     the band's edge factor: mean of W / S' over the band.  1 means
            no edge effect; below 1 means the band is darker.
    A_k     -log10(l_k): the same in absorbance.  Bigger means darker than
            the interior.  The stop rule for bands: a band is needed while
            |A_k| is 0.002 A (camera noise) or more.
    corner  a pixel in band k of one edge and band j of a neighboring edge.
            The document's rule predicts its factor as l_k * l_j, which in
            absorbance is A_k + A_j.

CHANNELS
    The pre-images of C1039 onward are clipped in every channel except
    photo_red.R and green.B (the exposure calibration of 2026-11-02 gives
    the pre-image too long an exposure).  C1038's pre-images are not
    clipped but hold no green.B.  So the channel comparison rests on C1038
    alone (5 repeat files, holder F), and the holder comparison on
    photo_red.R and green.B.  `a1.rung_verdict` decides what is usable.

RESULTS, 2026-11-11 (tables in white_out/; A = absorbance, bigger = darker)
    2a-ii, edges.  A saved model predicts a held-out photo's bands 0-3
        2 to 7 times worse than the photo's own band mean (photo_red.R,
        band 1: 0.029 A against 0.009 A; green.B: 0.048 against 0.009).
        The best weighted sum puts 90-100 % on the photo's own value there.
        In bands 4-7 a 50-80 % own / model mix cuts the error 10-20 %
        (about 0.0005 A).  Edge darkening varies too much from strip to
        strip to be stored once.  The model's shape hardly matters (all
        options fit to 0.010-0.013 A).  The top and bottom edges are not a
        simple decay: they are darkest about 1-2 px inside the fitted edge.
        DECISION (Bill, 2026-11-11): drop the saved edge models for now;
        each photo's own band means are used.
        Summaries: white_out/edge_test_summary.md, edge_models_summary.md
        (the full edge_test.json, 12 MB, is not committed; rebuild it with
        `test_models`).
    2a-ii, corners.  Every option (product, larger of two, interaction,
        average template) misses by 0.10-0.15 A in the outermost ring and
        0.05-0.06 A in the next.  DECISION (Bill, 2026-11-11): each corner
        pixel is divided by its own pre-image pixel, with no model; then
        measure how much stain falls in the two outer corner rings.
    2b, camera noise, one pixel, one photo.  Dot pad (saved, aged C1034,
        test_image/IMG0005-0007; mostly plain paper with a small stain
        dot near the middle, which violet.B shows at about +0.23 A): photo_red.R 0.0045 A, green.B
        0.006-0.008, violet.B 0.004.  Stained pad (saved C1044, IMG0008-0010):
        photo_red.R 0.005-0.006, green.B 0.009-0.012.  Exposure rungs and
        reseated pre-images agree (photo_red.R 0.004-0.005).  This is 2-4
        times the 0.002 A the chain assumed (NOISE_A, still used by
        `bands_needed`); DECISION (Bill): use the measured noise per channel
        and redo the stop rule and lambda.
    2b, drift.  The aged stained pad (saved C1044) read about 0.0035 A
        darker in photo_red.R on each repeat, about a minute apart, after a
        little over 2 s of each LED per capture.  The dot pad did not drift
        (0.001 A or less).  A stain-drift study is to come later (Bill).
    2c, the blend.  Model white = plane times each photo's own band
        means (`model_white`); corners use the pixel alone.  What the model
        lacks, per pixel, in the F/G/H pre-images: interior 0.006-0.007 A
        (photo_red.R), 0.007-0.008 (green.B), about the size of the noise;
        bands 0-1 about 0.04-0.06 A, because darkening changes steeply
        within one band.  Best lam, by the formula and by direct trial
        (they agree): interior 0.55-0.75, bands 4-7 0.55-0.85, bands 2-3
        0.75-0.95, bands 0-1 1.0.  The gain over the pixel alone is small:
        reseated pre-images, interior, photo_red.R 0.0066 -> 0.0064 A per
        pixel, green.B 0.0097 -> 0.0092; none at bands 0-1.  The blend moves
        a pad's mean A by under 0.0001 A (96 and 76 reseated pairs).
        Lining up adds spread where the white changes steeply: bands 0-1
        0.013-0.020 A against 0.009-0.013 unmoved, corners 0.014-0.024
        against 0.006-0.011.  With the measured noise the band stop rule
        needs 4-5 bands per edge (8 are modeled).
        DECISION (Bill, 2026-11-11): keep the blend for now, lam by zone.
        Bill is concerned about exposing pads to too much light, and may
        later use stored models to need fewer exposures and to check each
        capture's consistency as it is taken.
        Plain-language notes on the figure axes: tex/figs/
        step2c_lambda_<cell>.txt.

Run with:  timeout -k 10 1800 ../../.venv/bin/python3 -u white_study.py
"""
import json
import sys
from pathlib import Path

import numpy as np

import analysis_1 as a1
import shading_review as sr
from align_study import SETS, HOLDER_STYLE

HERE = Path(__file__).resolve().parent
OUT = HERE / "white_out"                       # the .json tables of results
FIGS = HERE.parents[1] / "tex" / "figs"        # figures for the write-up

#: Channels tried on every pre-image; the unusable ones are skipped.
CELLS = list(a1.PRECELLS) + [("green", "B")]
#: Bands measured per edge (0 .. NBANDS-1).  The document expects about 3;
#: more are measured to see where the edge effect ends.
NBANDS = 8
#: The interior, where S' is fitted: at least this far (layer px) from every
#: edge.  Equal to NBANDS, so the bands and the interior do not overlap.
INTERIOR_PX = 8
#: A band pixel closer than this (layer px) to a neighboring edge is left
#: out of that band.  Equal to INTERIOR_PX: past it, the neighboring edge
#: has no effect left.
BAND_CLEAR_PX = 8
#: Corner check: bands 0 .. CORNER_BANDS-1 of each of the two edges.
CORNER_BANDS = 3
#: Camera noise, in A: the stop rule for adding bands.
NOISE_A = 0.002
EDGES = ("L", "R", "T", "B")
#: Neighboring edges at each corner.
CORNERS = (("L", "T"), ("R", "T"), ("L", "B"), ("R", "B"))


# ── one pre-image ──────────────────────────────────────────────────────

def edge_distances(samp, sec=a1.PRE):
    """
    Each pixel's distance in from each edge of its fitted pad.

    Goal: the coordinates that sort pixels into bands.

    Args:
        samp: scalar str, repeat file name.
        sec: a1.PRE (default) for the pre-image, '' for the measurement.

    Returns:
        (dist, u, v): dist, dict edge -> array (rows, cols) of layer px in
        from that edge (negative outside the pad); u and v, arrays of
        position across from the left side and down from the top edge.
    """
    p = a1.pad_of(samp, sec)
    u = p.u                                    # across, from the left side
    v = p.v - p.top                            # down, from the top edge
    H = p.bot - p.top
    dist = {"L": u, "R": p.width - u, "T": v, "B": H - v}
    return dist, u, v


def band_profile(samp, cell):
    """
    Step 2a for one pre-image and one channel: the edge factor of every band.

    Goal: measure how much darker each band near an edge is than the smooth
    part, so we know how many bands the model white needs and whether the
    corner rule holds.

    How:
        1. W: the pre-image R for this channel (`shading_review.frame`),
           hot pixels NaN.
        2. S': a plane a + b*u + c*v fitted by least squares to W over the
           interior (INTERIOR_PX or more from every edge).
        3. For each edge and band k: l_k = mean of W / S' over the band
           (pixels near the neighboring edges left out), A_k = -log10 l_k.
        4. Corners: for each corner and each pair of bands (k, j) below
           CORNER_BANDS, the measured A of the pixels in both bands, less
           the rule's prediction A_k + A_j.

    Args:
        samp: scalar str, repeat file name.
        cell: (LED, Bayer layer).

    Returns:
        dict:
            samp, cell        names;
            A                 dict edge -> list of A_k, k = 0 .. NBANDS-1
                              (A units; bigger = darker than the interior);
            n                 dict edge -> list of pixel counts per band;
            slope_pct         how far the plane S' changes across the pad
                              width and down the pad height, in % of its
                              mean: (across, down);
            interior_sd_A     the spread of A = -log10(W / S') over the
                              interior: the texture plus noise per pixel;
            corner            dict "LT", "RT", ... -> CORNER_BANDS x
                              CORNER_BANDS list: measured A minus A_k + A_j.
    """
    f = sr.frame(samp, cell, a1.PRE)
    W = np.array(f["R"], float)
    dist, u, v = edge_distances(samp)
    near = np.minimum.reduce([dist[e] for e in EDGES])   # to the nearest edge
    inner = (near >= INTERIOR_PX) & np.isfinite(W)
    # Plane fit: one row per interior pixel, columns 1, u, v.
    X = np.c_[np.ones(inner.sum()), u[inner], v[inner]]
    coef, *_ = np.linalg.lstsq(X, W[inner], rcond=None)
    S = coef[0] + coef[1] * u + coef[2] * v
    ratio = W / S                               # l at each pixel
    p = a1.pad_of(samp, a1.PRE)
    mean_S = float(np.mean(S[inner]))
    slope = (100 * coef[1] * p.width / mean_S,
             100 * coef[2] * (p.bot - p.top) / mean_S)
    ok = np.isfinite(ratio) & (near >= 0)

    def band(e, k):
        return ok & (dist[e] >= k) & (dist[e] < k + 1)

    A, n = {}, {}
    for e in EDGES:
        others = [o for o in EDGES if o != e and o != _opposite(e)]
        clear = (dist[others[0]] >= BAND_CLEAR_PX) & \
                (dist[others[1]] >= BAND_CLEAR_PX)
        A[e], n[e] = [], []
        for k in range(NBANDS):
            m = band(e, k) & clear
            A[e].append(float(-np.log10(np.mean(ratio[m]))) if m.any()
                        else float("nan"))
            n[e].append(int(m.sum()))
    corner = {}
    for e1, e2 in CORNERS:
        tab = []
        for k in range(CORNER_BANDS):
            row = []
            for j in range(CORNER_BANDS):
                m = band(e1, k) & band(e2, j)
                meas = -np.log10(np.mean(ratio[m])) if m.any() else np.nan
                row.append(float(meas - (A[e1][k] + A[e2][j])))
            tab.append(row)
        corner[e1 + e2] = tab
    with np.errstate(invalid="ignore"):
        sd = float(np.nanstd(-np.log10(ratio[inner])))
    return dict(samp=samp, cell=a1.cell_name(cell), A=A, n=n,
                slope_pct=slope, interior_sd_A=sd, corner=corner)


def _opposite(e):
    return {"L": "R", "R": "L", "T": "B", "B": "T"}[e]


# ── all pre-images ─────────────────────────────────────────────────────

def band_profiles(sets=SETS, cells=CELLS):
    """
    Step 2a: `band_profile` for every repeat file and every usable channel.

    Args:
        sets: dict, holder -> sample -> list of repeat files.
        cells: channels to try.

    Returns:
        list of `band_profile` dicts, each with holder and sample added.
        Unusable channels (clipped, or not stored) are skipped silently;
        other failures are printed and skipped.
    """
    rows = []
    for hold, samples in sets.items():
        for smp, files in samples.items():
            for f in files:
                for cell in cells:
                    rung, _ = a1.rung_verdict(f, cell, a1.PRE)
                    if rung is None:
                        continue
                    try:
                        r = band_profile(f, cell)
                    except Exception as e:             # noqa: BLE001
                        print(f"  skip {f} {a1.cell_name(cell)}: "
                              f"{type(e).__name__}: {e}")
                        continue
                    r.update(holder=hold, sample=smp)
                    rows.append(r)
                    print(f"  {f:12s} {r['cell']:13s} A_0 "
                          + " ".join(f"{e}{r['A'][e][0]:6.3f}" for e in EDGES)
                          + f"  slope {r['slope_pct'][0]:+.2f}%,"
                            f"{r['slope_pct'][1]:+.2f}%")
            a1.close_all()
    return rows


def bands_needed(rows, noise=NOISE_A):
    """
    How many bands each edge needs: one more than the last band whose
    median |A_k| (over the given rows) is noise or more.

    Args:
        rows: list of `band_profile` dicts (one channel, or a group).
        noise: scalar, A.

    Returns:
        dict edge -> (bands needed, list of median A_k).
    """
    out = {}
    for e in EDGES:
        med = np.nanmedian(np.array([r["A"][e] for r in rows]), axis=0)
        big = np.flatnonzero(np.abs(med) >= noise)
        out[e] = (int(big[-1]) + 1 if len(big) else 0, med.tolist())
    return out


def summary(rows):
    """
    Print the step 2a numbers: per channel (C1038) and per holder
    (photo_red.R, green.B): median A_k by edge, bands needed, the plane's
    slope, the interior spread, and the corner rule's worst miss.
    """
    def show(label, rs):
        if not rs:
            return
        bn = bands_needed(rs)
        sl = np.median(np.abs([r["slope_pct"] for r in rs]), axis=0)
        sd = np.median([r["interior_sd_A"] for r in rs])
        cor = np.array([r["corner"][c] for r in rs for c in r["corner"]])
        cmed = np.nanmedian(cor, axis=0)            # (k, j) median over all
        print(f"{label:28s} n={len(rs):2d}  bands needed "
              + " ".join(f"{e}{bn[e][0]}" for e in EDGES)
              + f"  |slope| {sl[0]:.2f}%,{sl[1]:.2f}%  interior sd "
                f"{sd:.4f} A  corner miss (0,0) {cmed[0, 0]:+.4f}, "
                f"worst {np.nanmax(np.abs(cmed)):.4f} A")
        for e in EDGES:
            print(f"      {e}: " + " ".join(f"{a:+.4f}" for a in bn[e][1]))

    print("-- C1038, by channel (holder F) --")
    c38 = [r for r in rows if r["sample"] == "C1038"]
    for cell in sorted({r["cell"] for r in c38}):
        show(f"C1038 {cell}", [r for r in c38 if r["cell"] == cell])
    print("-- photo_red.R and green.B, by holder --")
    for cell in ("photo_red.R", "green.B"):
        for hold in SETS:
            show(f"{hold} {cell}", [r for r in rows if r["cell"] == cell
                                    and r["holder"] == hold])


# ── 2a, part 2: a saved exponential model of the edge darkening ────────
#
# Bill, 2026-11-11: fit an exponential decay of the edge darkening against
# distance from the edge, over all F, G, H pre-images; leave the corners
# out; leave out, iteratively, edge pixels the model does not fit.  Save
# the models (one per edge, or fewer), then test using a saved model alone,
# the per-image measurement alone, or a weighted sum of the two.  Explore
# corner models the same way.

#: A pixel within this many layer px of two edges is a corner pixel: left
#: out of the edge-model fit, and used to test the corner models.
CORNER_ZONE_PX = 8
#: Pixels whose misfit exceeds this many robust standard deviations are
#: left out, and the model refitted, until none change (at most MAX_PASSES).
REJECT_SIGMA = 4.0
MAX_PASSES = 8
#: Two-exponential models: the tail's decay length is at least this many
#: times the sharp part's.
TAIL_RATIO_MIN = 1.5
#: Which edges share a model, for each option compared.  amp: edges with the
#: same key share an amplitude; tau: edges with the same key share a decay
#: length.
SPECS = {
    "E4":      dict(amp=dict(L="L", R="R", T="T", B="B"),
                    tau=dict(L="L", R="R", T="T", B="B")),
    "E4_tau1": dict(amp=dict(L="L", R="R", T="T", B="B"),
                    tau=dict(L="x", R="x", T="x", B="x")),
    "E2":      dict(amp=dict(L="s", R="s", T="e", B="e"),
                    tau=dict(L="s", R="s", T="e", B="e")),
    "E1":      dict(amp=dict(L="x", R="x", T="x", B="x"),
                    tau=dict(L="x", R="x", T="x", B="x")),
}

#: Pixel tables already read, keyed (file, cell, sec).
_img_cache = {}


def load_image(samp, cell, sec=a1.PRE):
    """
    One photo's pad pixels, as the edge model needs them.

    Args:
        samp: scalar str, file name.
        cell: (LED, Bayer layer).
        sec: a1.PRE for a pre-image, '' for a measurement photo.

    Returns:
        dict of 1-D arrays, one entry per usable pad pixel (inside the
        fitted rectangle, finite, positive R):
            y        -log10 R: absorbance against the gray chips (bigger is
                     darker);
            u, v     position across and down the pad, layer px;
            d        dict edge -> distance in from that edge, layer px;
            corner   bool, within CORNER_ZONE_PX of two edges;
            corner_of  the corner's two edges ("LT", ...), '' if none;
        plus samp, cell, sec.
    """
    key = (samp, a1.cell_name(cell), sec)
    if key in _img_cache:
        return _img_cache[key]
    f = sr.frame(samp, cell, sec)
    W = np.array(f["R"], float)
    dist, u, v = edge_distances(samp, sec)
    near = np.minimum.reduce([dist[e] for e in EDGES])
    ok = (near >= 0) & np.isfinite(W) & (W > 0)
    d = {e: dist[e][ok] for e in EDGES}
    corner_of = np.full(ok.sum(), "", dtype=object)
    for e1, e2 in CORNERS:
        m = (d[e1] < CORNER_ZONE_PX) & (d[e2] < CORNER_ZONE_PX)
        corner_of[m] = e1 + e2
    out = dict(samp=samp, cell=a1.cell_name(cell), sec=sec,
               y=-np.log10(W[ok]), u=u[ok], v=v[ok], d=d,
               corner=corner_of != "", corner_of=corner_of)
    _img_cache[key] = out
    return out


def _edge_columns(d, spec, taus, n_exp):
    """
    The edge-model columns for a set of pixels.

    For each amplitude key k and exponential term m: the sum, over the
    edges e that share k, of exp(-d_e / tau), with tau the decay length of
    e's tau key and term m.  A model value is then sum of a_(k,m) times
    these columns.

    Returns:
        (cols, names): cols array (pixels, n_columns); names list of
        (amp key, m).
    """
    akeys = sorted(set(spec["amp"].values()))
    cols, names = [], []
    for k in akeys:
        for m in range(n_exp):
            c = np.zeros(len(d["L"]))
            for e in EDGES:
                if spec["amp"][e] == k:
                    c += np.exp(-d[e] / taus[(spec["tau"][e], m)])
            cols.append(c)
            names.append((k, m))
    return np.array(cols).T, names


def _design_planes(imgs, keep):
    """The plane columns alone: (1, u/20, v/20) per photo, zero elsewhere."""
    blocks = []
    n_img = len(imgs)
    for i, (im, kp) in enumerate(zip(imgs, keep)):
        P = np.zeros((kp.sum(), 3 * n_img))
        P[:, 3 * i] = 1.0
        P[:, 3 * i + 1] = im["u"][kp] / 20.0     # scaled to about 1
        P[:, 3 * i + 2] = im["v"][kp] / 20.0
        blocks.append(P)
    return np.vstack(blocks)


def _design(imgs, keep, spec, taus, n_exp):
    """Design matrix: a plane (1, u, v) per image, then the edge columns."""
    blocks, edge = [], []
    n_img = len(imgs)
    for i, (im, kp) in enumerate(zip(imgs, keep)):
        n = kp.sum()
        P = np.zeros((n, 3 * n_img))
        P[:, 3 * i] = 1.0
        P[:, 3 * i + 1] = im["u"][kp] / 20.0     # scaled to about 1
        P[:, 3 * i + 2] = im["v"][kp] / 20.0
        blocks.append(P)
        dk = {e: im["d"][e][kp] for e in EDGES}
        edge.append(_edge_columns(dk, spec, taus, n_exp)[0])
    return np.hstack([np.vstack(blocks), np.vstack(edge)])


def fit_edge_model(imgs, spec_name="E4", n_exp=1):
    """
    Fit one shared edge model to a set of photos (one channel).

    Goal: an exponential darkening per edge, a * exp(-d / tau), the same in
    every photo of the set, on top of each photo's own plane.

    How:
        1. Pixels used: every pad pixel except the corners, and except
           those rejected in step 4.
        2. For given decay lengths tau, the model is linear in everything
           else (each photo's plane, every amplitude), so those come from
           one least-squares solve.
        3. The decay lengths are found by a search around that solve
           (`scipy.optimize.least_squares` on log tau).
        4. Pixels missing the model by more than REJECT_SIGMA robust
           standard deviations (1.4826 x median absolute misfit) are left
           out and the fit repeated, until the rejected set stops changing.

    Args:
        imgs: list of `load_image` dicts, all one channel.
        spec_name: key of SPECS: which edges share amplitudes and decays.
        n_exp: 1 for one exponential per edge; 2 for a sharp one plus a
            slow tail.

    Returns:
        dict:
            spec, n_exp, cell;
            amp      {"key:m": amplitude, A};
            tau      {"key:m": decay length, layer px};
            planes   list of (a0, b_u, b_v) per photo, in the order given;
            rms      misfit of the accepted pixels, A;
            rejected list per photo of bool arrays (True = left out), as
                     plain lists of pixel indices;
            reject_by_band  fraction rejected in bands 0..NBANDS-1 (by the
                     nearest edge) and beyond.
    """
    from scipy import optimize
    spec = SPECS[spec_name]
    tkeys = sorted(set(spec["tau"].values()))
    keep = [~im["corner"] for im in imgs]
    y_all = None
    x0 = np.log(np.array([[1.0, 2.5][m] for k in tkeys
                          for m in range(n_exp)]))

    def taus_of(x):
        # The first decay length is exp(x); a second one (the slow tail) is
        # the first times (TAIL_RATIO_MIN + exp(x)), so it is always at least
        # TAIL_RATIO_MIN times longer.  Without this, the two terms on the
        # ends collapse into a near-equal pair with huge, opposite amplitudes
        # (an unstable way of drawing the peak 1-2 px in).
        t, i = {}, 0
        for k in tkeys:
            t[(k, 0)] = float(np.exp(x[i]))
            if n_exp == 2:
                t[(k, 1)] = t[(k, 0)] * (TAIL_RATIO_MIN + float(np.exp(x[i + 1])))
            i += n_exp
        return t

    cache = {}

    def solve(x, keep):
        # The plane columns P do not depend on tau, so their products are
        # computed once per set of kept pixels; only the edge columns E are
        # rebuilt for each trial tau.  Normal equations [P E]' [P E] b =
        # [P E]' y, solved directly (a 3 x photos + amplitudes square system).
        kid = id(keep)
        if cache.get("kid") != kid:
            P = _design_planes(imgs, keep)
            y = np.concatenate([im["y"][kp] for im, kp in zip(imgs, keep)])
            d = {e: np.concatenate([im["d"][e][kp] for im, kp in
                                    zip(imgs, keep)]) for e in EDGES}
            cache.update(kid=kid, P=P, y=y, d=d, PtP=P.T @ P, Pty=P.T @ y)
        P, y = cache["P"], cache["y"]
        E, _ = _edge_columns(cache["d"], spec, taus_of(x), n_exp)
        PtE = P.T @ E
        M = np.block([[cache["PtP"], PtE], [PtE.T, E.T @ E]])
        rhs = np.r_[cache["Pty"], E.T @ y]
        beta = np.linalg.solve(M, rhs)
        return beta, y - P @ beta[:P.shape[1]] - E @ beta[P.shape[1]:]

    x = x0
    for _ in range(MAX_PASSES):
        r = optimize.least_squares(lambda xx: solve(xx, keep)[1], x,
                                   bounds=(np.log(0.05), np.log(30.0)),
                                   x_scale=1.0, diff_step=1e-3)
        x = r.x
        beta, _ = solve(x, keep)
        # misfit of every non-corner pixel, rejected or not
        everyone = [~im["corner"] for im in imgs]
        X = _design(imgs, everyone, spec, taus_of(x), n_exp)
        y = np.concatenate([im["y"][kp] for im, kp in zip(imgs, everyone)])
        res = y - X @ beta
        kept = np.concatenate(keep)[np.concatenate(everyone)]
        scale = 1.4826 * np.median(np.abs(res[kept]))
        new_bad = np.abs(res) > REJECT_SIGMA * scale
        # back to per-photo keep masks
        new_keep, i0 = [], 0
        for im, ev in zip(imgs, everyone):
            n = ev.sum()
            kp = ev.copy()
            kp[np.flatnonzero(ev)] = ~new_bad[i0:i0 + n]
            new_keep.append(kp)
            i0 += n
        same = all(np.array_equal(a, b) for a, b in zip(keep, new_keep))
        keep = new_keep
        if same:
            break
    beta, res_kept = solve(x, keep)
    n_img = len(imgs)
    _, names = _edge_columns({e: np.zeros(1) for e in EDGES}, spec,
                             taus_of(x), n_exp)
    amps = {f"{k}:{m}": float(b) for (k, m), b in zip(names, beta[3 * n_img:])}
    # rejection by band of the nearest edge
    nearest = np.concatenate([np.minimum.reduce([im["d"][e] for e in EDGES])
                              [~im["corner"]] for im in imgs])
    rej = ~np.concatenate([kp[~im["corner"]] for im, kp in zip(imgs, keep)])
    by_band = [float(rej[(nearest >= k) & (nearest < k + 1)].mean())
               for k in range(NBANDS)] + [float(rej[nearest >= NBANDS].mean())]
    return dict(spec=spec_name, n_exp=n_exp, cell=imgs[0]["cell"],
                amp=amps, tau={f"{k}:{m}": v for (k, m), v in
                               taus_of(x).items()},
                planes=[beta[3 * i:3 * i + 3].tolist() for i in range(n_img)],
                rms=float(np.sqrt(np.mean(res_kept ** 2))),
                reject_by_band=by_band,
                files=[im["samp"] for im in imgs])


def model_edge(model, d):
    """
    A saved model's edge darkening at pixels with distances `d`.

    Args:
        model: dict from `fit_edge_model` (or read back from json).
        d: dict edge -> array of distances, layer px.

    Returns:
        dict edge -> array, each edge's own term (A); their sum is the
        model's darkening (the corner product rule, in absorbance).
    """
    spec = SPECS[model["spec"]]
    out = {}
    for e in EDGES:
        t = np.zeros_like(d[e], dtype=float)
        for m in range(model["n_exp"]):
            a = model["amp"][f"{spec['amp'][e]}:{m}"]
            tau = model["tau"][f"{spec['tau'][e]}:{m}"]
            t += a * np.exp(-d[e] / tau)
        out[e] = t
    return out


def _plane_given(model, im):
    """
    A photo's own plane, with a saved edge model held fixed.

    Fitted by least squares over the photo's non-corner pixels, after
    subtracting the model's edge darkening; the same rejection rule as the
    model fit, once.
    """
    edge = sum(model_edge(model, im["d"]).values())
    m = ~im["corner"]
    X = np.c_[np.ones(m.sum()), im["u"][m] / 20.0, im["v"][m] / 20.0]
    yy = im["y"][m] - edge[m]
    beta, *_ = np.linalg.lstsq(X, yy, rcond=None)
    r = yy - X @ beta
    good = np.abs(r) <= REJECT_SIGMA * 1.4826 * np.median(np.abs(r))
    beta, *_ = np.linalg.lstsq(X[good], yy[good], rcond=None)
    return beta


def _plane_at(beta, im):
    return beta[0] + beta[1] * im["u"] / 20.0 + beta[2] * im["v"] / 20.0


# ── testing the edge model ──────────────────────────────────────────────

#: Split of each band, for testing: alternate blocks of this many pixels
#: along the edge go to half 1 and half 2.  Blocks, not single pixels,
#: because neighboring pixels share texture.
SPLIT_BLOCK_PX = 4


def split_test(model, im):
    """
    For one held-out photo: per edge and band, how well do (a) the saved
    model, (b) the photo's own band mean, and (c) a weighted sum predict
    the band's mean A?

    How: the band's pixels (corners left out) are split into two halves,
    alternate blocks of SPLIT_BLOCK_PX along the edge.  Half 2 is the
    target.  The photo's own measurement is the mean of half 1; the model's
    prediction is its mean over half 2's pixels.  Everything is relative to
    the photo's plane, fitted with the model held fixed (`_plane_given`).

    Returns:
        list of dicts: edge, k, target, own, model (all A, relative to the
        plane), n1, n2 (pixels in each half).
    """
    beta = _plane_given(model, im)
    rel = im["y"] - _plane_at(beta, im)
    pred = sum(model_edge(model, im["d"]).values())
    along = {"L": im["v"], "R": im["v"], "T": im["u"], "B": im["u"]}
    out = []
    for e in EDGES:
        half = (np.floor(along[e] / SPLIT_BLOCK_PX) % 2).astype(int)
        for k in range(NBANDS):
            m = (~im["corner"]) & (im["d"][e] >= k) & (im["d"][e] < k + 1)
            h1, h2 = m & (half == 0), m & (half == 1)
            if h1.sum() < 4 or h2.sum() < 4:
                continue
            out.append(dict(edge=e, k=k, target=float(rel[h2].mean()),
                            own=float(rel[h1].mean()),
                            model=float(pred[h2].mean()),
                            n1=int(h1.sum()), n2=int(h2.sum())))
    return out


def corner_test(model, im, corner_models):
    """
    For one held-out photo: how well does each corner option predict the
    corner pixels?

    Options (`corner_models` holds what the training photos taught):
        product   the two edges' terms added (A), i.e. the edge factors
                  multiplied: the document's rule;
        max       the larger of the two edges' terms;
        interact  product plus b * A1 * A2, one b per corner (fitted on the
                  training photos);
        template  product plus the training photos' mean misfit in that
                  corner's cell (integer distances from the two edges).
    Each corner pixel's own value is the target, relative to the photo's
    plane, so even a perfect model misses by the pixel's texture.

    Returns:
        list of dicts: corner, k, j (integer distances), target, and one
        prediction per option.
    """
    beta = _plane_given(model, im)
    rel = im["y"] - _plane_at(beta, im)
    terms = model_edge(model, im["d"])
    out = []
    for c in sorted(set(im["corner_of"][im["corner"]])):
        e1, e2 = c[0], c[1]
        m = im["corner_of"] == c
        A1, A2 = terms[e1][m], terms[e2][m]
        k = np.floor(im["d"][e1][m]).astype(int)
        j = np.floor(im["d"][e2][m]).astype(int)
        b = corner_models["interact"].get(c, 0.0)
        tmpl = corner_models["template"].get(c, {})
        for i in range(m.sum()):
            out.append(dict(
                corner=c, k=int(k[i]), j=int(j[i]), target=float(rel[m][i]),
                product=float(A1[i] + A2[i]),
                max=float(max(A1[i], A2[i])),
                interact=float(A1[i] + A2[i] + b * A1[i] * A2[i]),
                template=float(A1[i] + A2[i]
                               + tmpl.get(f"{k[i]},{j[i]}", 0.0))))
    return out


def train_corners(model, imgs):
    """
    What the training photos say about the corners, given an edge model.

    Returns:
        dict: "interact": {corner: b}, the least-squares b of the misfit
        (target - product) on A1 * A2; "template": {corner: {"k,j": mean
        misfit}}.
    """
    acc = {}
    for im in imgs:
        beta = _plane_given(model, im)
        rel = im["y"] - _plane_at(beta, im)
        terms = model_edge(model, im["d"])
        for c in set(im["corner_of"][im["corner"]]):
            m = im["corner_of"] == c
            A1, A2 = terms[c[0]][m], terms[c[1]][m]
            mis = rel[m] - (A1 + A2)
            k = np.floor(im["d"][c[0]][m]).astype(int)
            j = np.floor(im["d"][c[1]][m]).astype(int)
            a = acc.setdefault(c, dict(x=[], y=[], cells={}))
            a["x"].append(A1 * A2)
            a["y"].append(mis)
            for kk, jj, mm in zip(k, j, mis):
                a["cells"].setdefault(f"{kk},{jj}", []).append(mm)
    inter, tmpl = {}, {}
    for c, a in acc.items():
        x, yv = np.concatenate(a["x"]), np.concatenate(a["y"])
        inter[c] = float(np.sum(x * yv) / np.sum(x * x)) if np.any(x) else 0.0
        tmpl[c] = {kj: float(np.median(v)) for kj, v in a["cells"].items()}
    return dict(interact=inter, template=tmpl)


#: Channels with photos from several samples: the only ones a held-out
#: sample can test.
TEST_CELLS = (("photo_red", "R"), ("green", "B"))


def _files_with(cell, sets=SETS):
    """(holder, sample, file) for every repeat file whose pre-image has a
    usable `cell`."""
    out = []
    for hold, samples in sets.items():
        for smp, files in samples.items():
            for f in files:
                if a1.rung_verdict(f, cell, a1.PRE)[0] is not None:
                    out.append((hold, smp, f))
    return out


def fit_all_models(cells=CELLS, specs=SPECS, n_exps=(1, 2)):
    """
    Step 2a-ii: every model option, fitted to all pre-images of each
    channel (holders pooled).  These are the models to save.

    Returns:
        list of `fit_edge_model` dicts, plus holder counts.
    """
    out = []
    for cell in cells:
        fl = _files_with(cell)
        if not fl:
            continue
        imgs = [load_image(f, cell) for _, _, f in fl]
        for sp in specs:
            for n in n_exps:
                m = fit_edge_model(imgs, sp, n)
                m["holders"] = sorted({h for h, _, _ in fl})
                out.append(m)
                print(f"  {m['cell']:13s} {sp:8s} n_exp {n}  rms "
                      f"{m['rms']:.4f} A  tau "
                      + " ".join(f"{k}={v:.2f}" for k, v in m["tau"].items())
                      + "  amp " + " ".join(f"{k}={v:.3f}" for k, v in
                                            m["amp"].items()))
        a1.close_all()
    return out


def test_models(cells=TEST_CELLS, specs=SPECS, n_exps=(1, 2)):
    """
    Step 2a-ii test: hold out one sample at a time.

    For each held-out sample: fit each model option on the other samples'
    pre-images, then on each held-out pre-image run `split_test` (edges)
    and, for the "E4" options, `corner_test` (corners, with corner options
    trained on the other samples).

    Returns:
        dict: "edges": list of split_test rows with cell, spec, n_exp,
        held-out sample and file; "corners": the same for corner_test.
    """
    edges, corners = [], []
    for cell in cells:
        fl = _files_with(cell)
        samples = sorted({s for _, s, _ in fl})
        for held in samples:
            train = [load_image(f, cell) for _, s, f in fl if s != held]
            test = [(h, f) for h, s, f in fl if s == held]
            for sp in specs:
                for n in n_exps:
                    m = fit_edge_model(train, sp, n)
                    cm = train_corners(m, train) if sp == "E4" else None
                    for h, f in test:
                        im = load_image(f, cell)
                        for row in split_test(m, im):
                            row.update(cell=m["cell"], spec=sp, n_exp=n,
                                       holder=h, sample=held, file=f)
                            edges.append(row)
                        if cm is not None:
                            for row in corner_test(m, im, cm):
                                row.update(cell=m["cell"], n_exp=n,
                                           holder=h, sample=held, file=f)
                                corners.append(row)
            print(f"  {a1.cell_name(cell)} held out {held}: done")
            a1.close_all()
    return dict(edges=edges, corners=corners)


def summarize_tests(t):
    """
    Print the test results.

    Edges, per option and band k: rms error (A) of the model alone, of the
    photo's own half-band mean, and of the best weighted sum
    w * own + (1 - w) * model, with that w.  The own measurement's error
    includes the noise of both halves, so it is a fair comparison with
    the model's error, which includes half 2's noise.

    Corners, per option and ring (the smaller of the two distances): rms
    error (A) against each corner pixel; the pixel's texture (about 0.008 A)
    is a floor no model can beat.
    """
    E = t["edges"]
    print("EDGES: rms error, A, by band k (model / own / best blend, w)")
    for cell in sorted({r["cell"] for r in E}):
        for sp in SPECS:
            for n in (1, 2):
                rs = [r for r in E if r["cell"] == cell and r["spec"] == sp
                      and r["n_exp"] == n]
                if not rs:
                    continue
                line = []
                for k in range(NBANDS):
                    b = [r for r in rs if r["k"] == k]
                    tg = np.array([r["target"] for r in b])
                    mo = np.array([r["model"] for r in b])
                    ow = np.array([r["own"] for r in b])
                    dm, do = mo - tg, ow - tg
                    den = np.sum((ow - mo) ** 2)
                    w = float(np.clip(np.sum((tg - mo) * (ow - mo)) / den,
                                      0, 1)) if den > 0 else 0.0
                    bl = w * ow + (1 - w) * mo - tg
                    rm = lambda z: float(np.sqrt(np.mean(z ** 2)))
                    line.append(f"k{k} {rm(dm):.4f}/{rm(do):.4f}/"
                                f"{rm(bl):.4f},w{w:.2f}")
                print(f"  {cell:12s} {sp:8s} n{n}  " + "  ".join(line))
    C = t["corners"]
    print("CORNERS: rms error, A, by ring min(k, j) (product / max / "
          "interact / template)")
    for cell in sorted({r["cell"] for r in C}):
        for n in (1, 2):
            rs = [r for r in C if r["cell"] == cell and r["n_exp"] == n]
            line = []
            for ring in range(4):
                b = [r for r in rs if min(r["k"], r["j"]) == ring]
                if not b:
                    continue
                tg = np.array([r["target"] for r in b])
                vals = [float(np.sqrt(np.mean((np.array([r[o] for r in b])
                                               - tg) ** 2)))
                        for o in ("product", "max", "interact", "template")]
                line.append(f"ring{ring} " + "/".join(f"{v:.4f}" for v in vals))
            print(f"  {cell:12s} n{n}  " + "  ".join(line))


# ── 2b: camera noise ───────────────────────────────────────────────────

#: Repeat sets taken without moving the cartridge (Bill, 2026-11-11):
#: IMG0005-0007 the saved, aged C1034 strip, plain paper with a small stain
#: dot near the middle (a dot-geometry capture); IMG0008-0010 the saved C1044
#: strip, stained all over.
#: Saved from the Test Image screen into test_image/; full LED suites with
#: three exposures (e0, e1, e2) each, and no pre-image section.
STILL_SETS = {"dot pad": ("IMG0005", "IMG0006", "IMG0007"),
              "stained pad": ("IMG0008", "IMG0009", "IMG0010")}
#: Channels looked at in 2b: the pre-image channels plus green.B.
NOISE_CELLS = CELLS


def frame_at(samp, cell, sec, rung):
    """
    R (signal / gray-chip level) for one channel at one named exposure.

    Like `shading_review.frame`, but at the exposure asked for rather than
    the one `a1.rung_verdict` picks, so that two exposures of one photo can
    be compared.  Returns None if any pad pixel is clipped
    (>= a1.SATURATED_DN) or the layer is missing.

    Returns:
        dict: R (array, hot pixels NaN), pad (bool array), exposure_us.
    """
    import padphot
    led, layer = cell
    c = a1.open_sample(samp, sec)
    if rung not in c.labels(led):
        return None
    got = c.layer(led, rung, layer)            # (dark-corrected, raw) or None
    if got is None or not got[1].any():        # not stored, or a placeholder
        return None
    sig, raw = got
    pad = a1.pad_of(samp, sec).mask(0)
    if (raw[pad] >= a1.SATURATED_DN).any():
        return None
    sig = sig.astype(float)
    hot = a1._hot(samp, sec)
    chip1 = a1._region(samp, a1.ROIGRAY1, sec) & ~hot
    chip2 = a1._region(samp, a1.ROIGRAY2, sec) & ~hot
    div = padphot.divisor_map(a1.models()[cell], c.bound, chip1, chip2, sig)
    R = sig / div
    R[hot] = np.nan
    return dict(R=R, pad=pad & ~hot, exposure_us=float(c.exposure_us(led, rung)))


def _pair_noise(Ra, Rb, region):
    """
    Noise from two photos of the same paper on the same pixels.

    d = -log10(Ra / Rb) per pixel.  Texture and edges cancel; what is left
    is the two photos' noise together, so one photo's noise is
    sd(d) / sqrt(2).  The mean of d is any whole-photo change between the
    two (lamp or camera drift).

    Returns:
        (noise_A, mean_A, n): one photo's noise and the mean difference,
        both in A, and the pixel count.
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        d = -np.log10(Ra / Rb)
    d = d[region & np.isfinite(d)]
    return float(np.std(d) / np.sqrt(2)), float(np.mean(d)), int(d.size)


def noise_still(sets=STILL_SETS, cells=NOISE_CELLS):
    """
    Step 2b, way 1: camera noise from repeats taken without moving the
    cartridge.

    For each set, channel and exposure: every pair of the three photos,
    over the pad (all of it, and the interior alone).  The strip did not
    move (the pad fits agree within 0.05 px), so the same pixel sees the
    same paper.

    Returns:
        list of dicts: set, cell, rung, exposure_us, pair, noise_A (one
        photo), mean_A (drift between the two), noise_inner_A, n.
    """
    out = []
    for name, files in sets.items():
        for cell in cells:
            labels = a1.open_sample(files[0], "").labels(cell[0])
            for rung in labels:
                fr = [frame_at(f, cell, "", rung) for f in files]
                if any(x is None for x in fr):
                    continue
                inner = a1.pad_of(files[0], "").mask(INTERIOR_PX)
                for i in range(len(files)):
                    for j in range(i + 1, len(files)):
                        reg = fr[i]["pad"] & fr[j]["pad"]
                        nz, mu, n = _pair_noise(fr[i]["R"], fr[j]["R"], reg)
                        nzi, _, _ = _pair_noise(fr[i]["R"], fr[j]["R"],
                                                reg & inner)
                        out.append(dict(set=name, cell=a1.cell_name(cell),
                                        rung=rung,
                                        exposure_us=fr[i]["exposure_us"],
                                        pair=f"{files[i]}-{files[j]}",
                                        noise_A=nz, mean_A=mu,
                                        noise_inner_A=nzi, n=n))
        a1.close_all()
    return out


def noise_rungs(files=("IMG0005", "IMG0008", "C1041", "C1045-a"),
                cells=NOISE_CELLS):
    """
    Step 2b, way 2: two exposures of one photo (e0 against e1, e1 against
    e2), same paper, same pixels.  The gray-chip division takes out the
    exposure.  The two exposures have different noise, so sd(d)/sqrt(2) is
    their average noise.

    Returns:
        list of dicts: file, cell, rungs, noise_A, mean_A, n.
    """
    out = []
    for f in files:
        for cell in cells:
            labels = a1.open_sample(f, "").labels(cell[0])
            fr = {r: frame_at(f, cell, "", r) for r in labels}
            for r1, r2 in zip(labels, labels[1:]):
                if fr[r1] is None or fr[r2] is None:
                    continue
                reg = fr[r1]["pad"] & fr[r2]["pad"]
                nz, mu, n = _pair_noise(fr[r1]["R"], fr[r2]["R"], reg)
                out.append(dict(file=f, cell=a1.cell_name(cell),
                                rungs=f"{r1}-{r2}", noise_A=nz, mean_A=mu,
                                n=n))
        a1.close_all()
    return out


def noise_reseated(sample="C1040", files=None, cells=(("photo_red", "R"),
                                                       ("green", "B"))):
    """
    Step 2b, way 3: between reseated repeats (pre-images of one sample),
    lined up by the guided pad fits.  The strip moved between them, so this
    is noise plus the lining-up error acting on the texture: an upper
    bound on the camera noise, and a measure of what lining up leaves.

    Returns:
        list of dicts: cell, pair, noise_A (one photo, interior), mean_A, n.
    """
    files = files or SETS["F"][sample]
    out = []
    for cell in cells:
        for i in range(len(files)):
            for j in range(i + 1, len(files)):
                fa, fb = files[i], files[j]
                Ra = sr.frame(fa, cell, a1.PRE)["R"]
                Rb = sr.frame(fb, cell, a1.PRE)["R"]
                Ra_on_b = _onto(fa, fb, Ra, Rb.shape)
                pb = a1.pad_of(fb, a1.PRE)
                inner = pb.mask(INTERIOR_PX)
                nz, mu, n = _pair_noise(Ra_on_b, Rb, inner)
                out.append(dict(cell=a1.cell_name(cell), pair=f"{fa}-{fb}",
                                noise_A=nz, mean_A=mu, n=n))
        a1.close_all()
    return out


def _onto(fa, fb, arr, shape):
    """
    Carry an array in pre-image fa's pixels onto pre-image fb's pixels,
    lining up by the two guided pad fits.  Each fb pixel takes the
    weighted average of the four nearest fa pixels (bilinear, as
    `a1.pre_to_post` does).  A result that leans on a NaN pixel is NaN.

    Args:
        fa, fb: scalar str, repeat file names.
        arr: array in fa's pixels.
        shape: (rows, cols) of fb's arrays.

    Returns:
        array of `shape`.
    """
    from scipy import ndimage as ndi
    pa, pb = a1.pad_of(fa, a1.PRE), a1.pad_of(fb, a1.PRE)
    rows, cols = shape
    yy, xx = np.mgrid[0:rows, 0:cols].astype(float)
    u = xx - (pb.aL + pb.m * (yy - pb.yc))
    ya = pa.top + (yy - pb.top)
    xa = pa.aL + u + pa.m * (ya - pa.yc)
    bad = ~np.isfinite(arr)
    out = ndi.map_coordinates(np.where(bad, 0.0, arr), [ya, xa], order=1,
                              mode="nearest")
    if bad.any():
        hit = ndi.map_coordinates(bad.astype(float), [ya, xa], order=1,
                                  mode="nearest")
        out[hit > 0] = np.nan
    return out


# ── 2c: texture, and the best blend weight ─────────────────────────────
#
# The white used in the ratio is  W-hat = lam * W + (1 - lam) * W-tilde
# (chain step 5): W is the pixel itself, W-tilde the model white.  The
# model white here is a plane over the interior times each edge's own band
# means, measured on the same photo (the saved edge models were dropped,
# Bill 2026-11-11).  Corner pixels get no model: they are divided by their
# own pixel (lam = 1 there, Bill 2026-11-11).
#
# What the model lacks is the paper's texture.  What the pixel carries is
# camera noise.  With texture spread t and noise spread s (both in A), the
# document's best weight is  lam = t^2 / (t^2 + s^2).

#: Blend weights tried in the direct trials.
LAMBDAS = [round(x, 2) for x in np.linspace(0, 1, 21)]
#: Pixel groups reported in 2c, by distance to the nearest edge.
ZONES = ("interior", "bands 0-1", "bands 2-3", "bands 4-7")


def zones_of(samp, sec):
    """
    Sort a photo's pad pixels into the 2c groups.

    A pixel within BAND_CLEAR_PX of a side and of an end is a corner.  The
    other pixels are sorted by the distance to their nearest edge: under 2,
    2 to 4, 4 to NBANDS layer px, or the interior (NBANDS or more).

    Returns:
        (zone, nearest, dist): zone, dict name -> bool array, the ZONES and
        "corner"; nearest, array of the nearest edge's index in EDGES;
        dist, from `edge_distances`.
    """
    dist, _, _ = edge_distances(samp, sec)
    D = np.stack([dist[e] for e in EDGES])
    inside = (D >= 0).all(0)
    near = D.min(0)
    side = np.minimum(dist["L"], dist["R"])
    end = np.minimum(dist["T"], dist["B"])
    corner = inside & (side < BAND_CLEAR_PX) & (end < BAND_CLEAR_PX)
    edge = inside & ~corner & (near < NBANDS)
    zone = {"interior": inside & (near >= NBANDS),
            "bands 0-1": edge & (near < 2),
            "bands 2-3": edge & (near >= 2) & (near < 4),
            "bands 4-7": edge & (near >= 4),
            "corner": corner}
    return zone, D.argmin(0), dist


def model_white(R, samp, sec):
    """
    The model white W-tilde for one photo: the smooth part times each
    edge's own band means.

    How:
        1. S': a plane a + b*u + c*v fitted to R over the interior.
        2. For each edge and band k (pixels whose nearest edge it is, k to
           k+1 layer px in, corners left out): l_k = mean of R / S'.
        3. W-tilde = S' in the interior, S' * l_k in band k, NaN in the
           corners and off the pad.

    Args:
        R: array, the photo's reflectance (hot pixels NaN).
        samp, sec: the photo, for its pad fit.

    Returns:
        (Wt, zone): the model white and the `zones_of` groups.
    """
    zone, nearest, dist = zones_of(samp, sec)
    _, u, v = edge_distances(samp, sec)
    ok = np.isfinite(R)
    inner = zone["interior"] & ok
    X = np.c_[np.ones(inner.sum()), u[inner], v[inner]]
    coef, *_ = np.linalg.lstsq(X, R[inner], rcond=None)
    S = coef[0] + coef[1] * u + coef[2] * v
    Wt = np.full(R.shape, np.nan)
    Wt[zone["interior"]] = S[zone["interior"]]
    edge = zone["bands 0-1"] | zone["bands 2-3"] | zone["bands 4-7"]
    for i, e in enumerate(EDGES):
        for k in range(NBANDS):
            m = edge & (nearest == i) & (dist[e] >= k) & (dist[e] < k + 1)
            if (m & ok).any():
                Wt[m] = S[m] * np.mean(R[m & ok] / S[m & ok])
    return Wt, zone


def _best(t2, s2):
    """The document's best weight and the white's spread there, from
    texture t2 and noise s2 (both squared, A^2)."""
    lam = t2 / (t2 + s2) if t2 + s2 > 0 else float("nan")
    return lam, float(np.sqrt(t2 * s2 / (t2 + s2))) if t2 + s2 > 0 else 0.0


def texture_still(sets=STILL_SETS, cells=NOISE_CELLS):
    """
    Step 2c, way 1: the texture the model white lacks, from the repeats
    taken without moving the cartridge.

    How, for each set, channel and exposure:
        1. M: the mean of the three photos.  Its noise is the one-photo
           noise s over sqrt(3).
        2. A = -log10(M / W-tilde(M)): texture plus that noise.
        3. s from the pairs of photos on the same pixels (`_pair_noise`).
        4. texture t: t^2 = var(A) - s^2 / 3, per zone.
        5. The document's best lam and the white's spread there.

    Returns:
        list of dicts: set, cell, rung, zone, n, texture_A, noise_A,
        lam, white_A (spread the white adds at that lam).
    """
    out = []
    for name, files in sets.items():
        for cell in cells:
            labels = a1.open_sample(files[0], "").labels(cell[0])
            for rung in labels:
                fr = [frame_at(f, cell, "", rung) for f in files]
                if any(x is None for x in fr):
                    continue
                Rs = [x["R"] for x in fr]
                M = np.mean(Rs, axis=0)
                Wt, zone = model_white(M, files[0], "")
                with np.errstate(invalid="ignore", divide="ignore"):
                    A = -np.log10(M / Wt)
                for zn in ZONES:
                    z = zone[zn] & np.isfinite(A)
                    s2 = float(np.mean([_pair_noise(Rs[i], Rs[j], z)[0] ** 2
                                        for i in range(3)
                                        for j in range(i + 1, 3)]))
                    t2 = max(float(np.var(A[z])) - s2 / 3, 0.0)
                    lam, w = _best(t2, s2)
                    out.append(dict(set=name, cell=a1.cell_name(cell),
                                    rung=rung, zone=zn, n=int(z.sum()),
                                    texture_A=float(np.sqrt(t2)),
                                    noise_A=float(np.sqrt(s2)),
                                    lam=lam, white_A=w))
        a1.close_all()
    return out


def texture_pre(cells=(("photo_red", "R"), ("green", "B"))):
    """
    Step 2c, way 2: the texture in the F/G/H pre-images.

    Each pre-image is one photo, so its noise cannot be taken out from the
    photo itself.  The noise s is taken from `noise_still` (dot pad,
    interior, median for the channel): t^2 = var(A) - s^2.

    Returns:
        list of dicts: holder, sample, file, cell, zone, n, total_A,
        noise_A (assumed), texture_A, lam, white_A.
    """
    ns = _load("noise_still")
    out = []
    for cell in cells:
        name = a1.cell_name(cell)
        s = float(np.median([r["noise_inner_A"] for r in ns
                             if r["set"] == "dot pad" and r["cell"] == name]))
        for hold, smp, f in _files_with(cell):
            R = np.array(sr.frame(f, cell, a1.PRE)["R"], float)
            Wt, zone = model_white(R, f, a1.PRE)
            with np.errstate(invalid="ignore", divide="ignore"):
                A = -np.log10(R / Wt)
            for zn in ZONES:
                z = zone[zn] & np.isfinite(A)
                tot = float(np.var(A[z]))
                t2 = max(tot - s ** 2, 0.0)
                lam, w = _best(t2, s ** 2)
                out.append(dict(holder=hold, sample=smp, file=f, cell=name,
                                zone=zn, n=int(z.sum()),
                                total_A=float(np.sqrt(tot)), noise_A=s,
                                texture_A=float(np.sqrt(t2)), lam=lam,
                                white_A=w))
        a1.close_all()
    return out


def _trial(Rpost, Rpre, Wt, zone):
    """
    The spread of A = -log10(Rpost / W-hat) per zone, for every lam in
    LAMBDAS.  Corner pixels use the pixel itself (lam = 1).

    Returns:
        dict zone -> list of spreads (A), one per lam; and "n" per zone.
    """
    out = {}
    for zn in ZONES + ("corner",):
        z = zone[zn] & np.isfinite(Rpost) & np.isfinite(Rpre)
        if zn != "corner":
            z &= np.isfinite(Wt)
        sds = []
        for lam in LAMBDAS:
            W = Rpre[z] if zn == "corner" else lam * Rpre[z] + (1 - lam) * Wt[z]
            with np.errstate(invalid="ignore", divide="ignore"):
                sds.append(float(np.std(-np.log10(Rpost[z] / W))))
        out[zn] = sds
        out["n " + zn] = int(z.sum())
    return out


def lambda_still(sets=STILL_SETS, cells=NOISE_CELLS):
    """
    Step 2c, way 3: try every lam on the unmoved repeats.  One photo plays
    the post-image, another the pre-image (same pixels, no lining up, no
    dye).  The spread of the resulting A over each zone is what the white
    adds plus the post-image's own noise.

    Returns:
        list of dicts: set, cell, rung, post, pre, and `_trial`'s results.
    """
    out = []
    for name, files in sets.items():
        for cell in cells:
            labels = a1.open_sample(files[0], "").labels(cell[0])
            for rung in labels:
                fr = [frame_at(f, cell, "", rung) for f in files]
                if any(x is None for x in fr):
                    continue
                for j in range(3):                 # j: the "pre-image"
                    Wt, zone = model_white(fr[j]["R"], files[j], "")
                    for i in range(3):             # i: the "post-image"
                        if i == j:
                            continue
                        row = dict(set=name, cell=a1.cell_name(cell),
                                   rung=rung, post=files[i], pre=files[j])
                        row.update(_trial(fr[i]["R"], fr[j]["R"], Wt, zone))
                        out.append(row)
        a1.close_all()
    return out


def lambda_reseated(cells=(("photo_red", "R"), ("green", "B"))):
    """
    Step 2c, way 4: try every lam on reseated repeats of one sample.  One
    pre-image plays the post-image; another, lined up onto it by the
    guided pad fits (bilinear, as the chain does), plays the pre-image.
    This adds what lining up does: the bilinear average lowers the
    pre-image's noise, and any misfit moves the texture.

    Returns:
        list of dicts: holder, sample, cell, post, pre, and `_trial`'s
        results.
    """
    out = []
    for cell in cells:
        fl = _files_with(cell)
        for hold, smp in sorted({(h, s) for h, s, _ in fl}):
            files = [f for h, s, f in fl if s == smp]
            R = {f: np.array(sr.frame(f, cell, a1.PRE)["R"], float)
                 for f in files}
            Wt = {f: model_white(R[f], f, a1.PRE)[0] for f in files}
            for fb in files:                       # fb: the "post-image"
                zone, _, _ = zones_of(fb, a1.PRE)
                for fa in files:                   # fa: the "pre-image"
                    if fa == fb:
                        continue
                    Ra = _onto(fa, fb, R[fa], R[fb].shape)
                    Wa = _onto(fa, fb, Wt[fa], R[fb].shape)
                    row = dict(holder=hold, sample=smp, cell=a1.cell_name(cell),
                               post=fb, pre=fa)
                    row.update(_trial(R[fb], Ra, Wa, zone))
                    out.append(row)
            a1.close_all()
    return out


def summarize_2c(tex_still, tex_pre, lam_still, lam_res):
    """
    Print step 2c's numbers.

    Texture: median t, s and the document's best lam per zone.  Trials:
    the root-mean-square spread over pairs at lam = 0 (model only), 1
    (pixel only) and the best lam on the grid.
    """
    def med(rows, key):
        return float(np.median([r[key] for r in rows]))

    print("TEXTURE the model lacks, A (median): texture / noise -> best lam,"
          " white's spread")
    for name in ("dot pad", "stained pad"):
        for cell in sorted({r["cell"] for r in tex_still}):
            rs = [r for r in tex_still if r["set"] == name and r["cell"] == cell]
            if not rs:
                continue
            txt = "  ".join(
                f"{zn}: {med(z, 'texture_A'):.4f}/{med(z, 'noise_A'):.4f}"
                f" -> {med(z, 'lam'):.2f}, {med(z, 'white_A'):.4f}"
                for zn in ZONES if (z := [r for r in rs if r["zone"] == zn]))
            print(f"  still {name:11s} {cell:13s} {txt}")
    for cell in sorted({r["cell"] for r in tex_pre}):
        for hold in sorted({r["holder"] for r in tex_pre}):
            rs = [r for r in tex_pre if r["cell"] == cell and r["holder"] == hold]
            if not rs:
                continue
            txt = "  ".join(
                f"{zn}: {med(z, 'texture_A'):.4f}/{med(z, 'noise_A'):.4f}"
                f" -> {med(z, 'lam'):.2f}, {med(z, 'white_A'):.4f}"
                for zn in ZONES if (z := [r for r in rs if r["zone"] == zn]))
            print(f"  pre {hold:2s} {cell:13s} {txt}")

    def curves(rows, label):
        for cell in sorted({r["cell"] for r in rows}):
            rs = [r for r in rows if r["cell"] == cell]
            parts = []
            for zn in ZONES + ("corner",):
                c = np.sqrt(np.mean([np.square(r[zn]) for r in rs], axis=0))
                b = int(np.argmin(c))
                parts.append(f"{zn}: {c[0]:.4f}/{c[-1]:.4f}/{c[b]:.4f}"
                             f"@{LAMBDAS[b]:.2f}" if zn != "corner"
                             else f"corner: {c[-1]:.4f}")
            print(f"  {label:20s} {cell:13s} " + "  ".join(parts))

    print("TRIALS: spread of A, rms over pairs: model only / pixel only /"
          " best @ lam")
    for name in ("dot pad", "stained pad"):
        curves([r for r in lam_still if r["set"] == name], "still " + name)
    for hold in sorted({r["holder"] for r in lam_res}):
        curves([r for r in lam_res if r["holder"] == hold], "reseated " + hold)


# ── figures ────────────────────────────────────────────────────────────

def _band_axes(title):
    """Four stacked panels, one per edge, sharing the band axis."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axs = plt.subplots(4, 1, figsize=(7.4, 9.2), sharex=True)
    names = {"L": "Left edge", "R": "Right edge", "T": "Top edge",
             "B": "Bottom edge"}
    for ax, e in zip(axs, EDGES):
        ax.set_yscale("symlog", linthresh=0.002)
        ax.axhspan(-NOISE_A, NOISE_A, color="#e8e7e2", zorder=0)
        ax.axhline(0, color="#a3a29b", lw=0.8)
        ax.set_title(names[e], loc="left", fontsize=9)
        ax.set_ylabel("A_k (darker up)")
        ax.grid(axis="y", color="#ecebe6", lw=0.8)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    axs[-1].set_xlabel("band k (layer px in from the edge; 0 = outermost)")
    axs[-1].set_xticks(range(NBANDS))
    fig.suptitle(title, fontsize=10, x=0.02, ha="left")
    return fig, axs


#: Fixed color per channel (categorical, in a fixed order).
CELL_COLOR = {"violet.B": "#7b3fbf", "royal_blue.B": "#2a4fd6",
              "cyan.B": "#1a9fb5", "green.Gb": "#3f9e3a",
              "green.Gr": "#9ac24a", "photo_red.R": "#d6402a",
              "green.B": "#3b6b3a"}


def show_channels(rows, savefigs=True):
    """
    Figure for step 2a: band profiles by channel, C1038 pre-images.

    Goal: does the edge darkening depend on the LED color?  One line per
    channel: the median A_k over C1038's 5 repeat files; the thin band
    around it spans their smallest to largest value.  The gray band is the
    camera noise (+/-0.002 A): bands inside it are not needed.  The y axis
    is linear inside +/-0.002 A and logarithmic outside, so both the large
    outer bands and the faint inner ones show.
    """
    fig, axs = _band_axes("C1038 pre-images (holder F), by channel: edge "
                          "band darkening A_k = -log10(band mean / S')\n"
                          "median over 5 repeat files, shaded = their range;"
                          " gray = camera noise +/-0.002 A")
    c38 = [r for r in rows if r["sample"] == "C1038"]
    k = np.arange(NBANDS)
    for cell in CELL_COLOR:
        rs = [r for r in c38 if r["cell"] == cell]
        if not rs:
            continue
        for ax, e in zip(axs, EDGES):
            a = np.array([r["A"][e] for r in rs])
            ax.fill_between(k, a.min(0), a.max(0), color=CELL_COLOR[cell],
                            alpha=0.15, lw=0)
            ax.plot(k, np.median(a, 0), "-o", color=CELL_COLOR[cell], ms=3,
                    lw=1.4, label=cell)
    axs[0].legend(fontsize=7, ncol=3, frameon=False, loc="upper right")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    _savefig(fig, "step2a_bands_channels", savefigs)
    return fig


def show_holders(rows, cell="photo_red.R", savefigs=True):
    """
    Figure for step 2a: band profiles by holder, one channel, all samples.

    Goal: does the edge darkening depend on the holder?  One line per
    holder: the median A_k over its repeat files, shaded from smallest to
    largest.  Axes as `show_channels`.
    """
    fig, axs = _band_axes(f"Pre-images, {cell}, by holder: edge band "
                          f"darkening A_k = -log10(band mean / S')\n"
                          f"median over repeat files, shaded = their range;"
                          f" gray = camera noise +/-0.002 A")
    k = np.arange(NBANDS)
    for hold, (col, mk, lab) in HOLDER_STYLE.items():
        rs = [r for r in rows if r["cell"] == cell and r["holder"] == hold]
        if not rs:
            continue
        for ax, e in zip(axs, EDGES):
            a = np.array([r["A"][e] for r in rs])
            ax.fill_between(k, a.min(0), a.max(0), color=col, alpha=0.15,
                            lw=0)
            ax.plot(k, np.median(a, 0), "-", marker=mk, color=col, ms=4,
                    lw=1.4, label=f"{lab} ({len(rs)} files)")
    axs[0].legend(fontsize=7, frameon=False, loc="upper right")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    _savefig(fig, f"step2a_bands_holders_{cell}", savefigs)
    return fig


def show_lambda(lam_still, lam_res, tex_still, cell="photo_red.R",
                savefigs=True):
    """
    Figure for step 2c: the spread of A against the blend weight lam.

    Goal: see where the best lam lies in each zone, and whether the
    document's formula matches the direct trial.  One panel per zone, from
    the interior out to bands 0-1.  Lines: the trial on the unmoved dot-pad
    repeats (dark gray) and on reseated pre-images, lined up, per holder
    (holder colors); each is the root-mean-square over its pairs.  A filled
    marker sits at each line's lowest point.  Dotted: the formula for the
    dot pad, spread^2 = s^2 + lam^2 s^2 + (1 - lam)^2 t^2, with the noise s
    and texture t of `texture_still` (the first s^2 is the post-image's own
    noise, which no lam removes).  lam = 0 is the model white alone, lam = 1
    the pixel alone.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    lam = np.array(LAMBDAS)
    order = ("interior", "bands 4-7", "bands 2-3", "bands 0-1")
    fig, axs = plt.subplots(4, 1, figsize=(7.4, 9.2), sharex=True)

    def line(ax, rows, zn, col, lab, ls="-"):
        c = np.sqrt(np.mean([np.square(r[zn]) for r in rows], axis=0))
        ax.plot(lam, c, ls, color=col, lw=1.6, label=lab)
        b = int(np.argmin(c))
        ax.plot(lam[b], c[b], "o", color=col, ms=6, mec="white", mew=1.2,
                zorder=3)

    for ax, zn in zip(axs, order):
        rs = [r for r in lam_still if r["set"] == "dot pad" and r["cell"] == cell]
        if rs:
            line(ax, rs, zn, "#4a4944", f"unmoved repeats, dot pad "
                 f"({len(rs)} pairs)")
        for hold, (col, mk, lab) in HOLDER_STYLE.items():
            rs = [r for r in lam_res if r["holder"] == hold and r["cell"] == cell]
            if rs:
                line(ax, rs, zn, col, f"reseated pre-images, {lab} "
                     f"({len(rs)} pairs)")
        ts = [r for r in tex_still if r["set"] == "dot pad"
              and r["cell"] == cell and r["zone"] == zn]
        if ts:
            s = np.median([r["noise_A"] for r in ts])
            t = np.median([r["texture_A"] for r in ts])
            ax.plot(lam, np.sqrt(s**2 + lam**2 * s**2 + (1 - lam)**2 * t**2),
                    ":", color="#4a4944", lw=1.2,
                    label="formula, dot pad (s, t from texture_still)")
        ax.set_title(zn if zn != "interior" else
                     f"interior ({NBANDS} or more px from every edge)",
                     loc="left", fontsize=9)
        ax.set_ylabel("spread of A")
        ax.grid(axis="y", color="#ecebe6", lw=0.8)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    axs[0].legend(fontsize=7, frameon=False, loc="upper right")
    axs[-1].set_xlabel("blend weight lam on the pixel itself "
                       "(0 = model white alone, 1 = pixel alone)")
    fig.suptitle(f"Pre-images as stand-in post-images, {cell}: spread of "
                 f"A = -log10(R_post / W-hat) against lam\n"
                 f"W-hat = lam * pixel + (1 - lam) * model white; dots = "
                 f"lowest point; no dye anywhere", fontsize=10, x=0.02,
                 ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    _savefig(fig, f"step2c_lambda_{cell}", savefigs)
    return fig


def _savefig(fig, name, savefigs):
    if savefigs:
        FIGS.mkdir(parents=True, exist_ok=True)
        out = FIGS / f"{name}.png"
        fig.savefig(out, dpi=110)
        print(f"fig saved to {out}")


def _save(name, obj):
    """Write `obj` to white_out/<name>.json."""
    OUT.mkdir(exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(obj, indent=1))


def _load(name):
    """Read white_out/<name>.json back."""
    return json.loads((OUT / f"{name}.json").read_text())


def run():
    """
    The study, step by step.  Turn each step on (1) or off (0).

        2a     band_profiles   -> white_out/bands.json
               summary         -> terminal
               figures         -> tex/figs/step2a_bands_channels.png,
                                  step2a_bands_holders_<cell>.png
        2a-ii  fit_all_models  -> white_out/edge_models.json (about 3 min)
               test_models     -> white_out/edge_test.json (about 25 min)
        2b     noise_still, noise_rungs, noise_reseated
                               -> white_out/noise_*.json
        2c     texture_still, texture_pre, lambda_still, lambda_reseated
                               -> white_out/texture_*.json, lambda_*.json
               summarize_2c    -> terminal
               show_lambda     -> tex/figs/step2c_lambda_<cell>.png
    """
    if 0:   # 2a: band profiles from the pre-images (reads the photos)
        _save("bands", band_profiles())
    if 0:   # 2a: numbers
        summary(_load("bands"))
    if 0:   # 2a: figures
        rows = _load("bands")
        show_channels(rows)
        show_holders(rows, "photo_red.R")
        show_holders(rows, "green.B")
    if 0:   # 2a-ii: saved edge models and their held-out tests (slow)
        _save("edge_models", fit_all_models())
        t = test_models()
        _save("edge_test", t)
        summarize_tests(t)
    if 0:   # 2b: camera noise
        _save("noise_still", noise_still())
        _save("noise_rungs", noise_rungs())
        _save("noise_reseated", noise_reseated())
    if 1:   # 2c: texture and the blend weight (needs noise_still.json)
        _save("texture_still", texture_still())
        _save("texture_pre", texture_pre())
        _save("lambda_still", lambda_still())
        _save("lambda_reseated", lambda_reseated())
    if 1:   # 2c: numbers and figures
        ts, tp = _load("texture_still"), _load("texture_pre")
        ls, lr = _load("lambda_still"), _load("lambda_reseated")
        summarize_2c(ts, tp, ls, lr)
        show_lambda(ls, lr, ts, "photo_red.R")
        show_lambda(ls, lr, ts, "green.B")


if __name__ == "__main__":
    sys.exit(run())
