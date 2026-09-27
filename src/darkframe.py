"""
darkframe.py — find sensor hot pixels from dark frames, and dark-correct a raw
frame against them.
------------------------------------------------------------------------------
Why this exists
----------------
`tex/dark_signal_characterization.tex` worked through six statistical tests on
a large batch of pooled dark frames and landed on a model for one pixel's dark
reading:

    d_i(k) = mu(k) + p_i + eps_i(k)

— a frame-level spatial mean mu(k), a pixel's own time-invariant fixed offset
p_i, and fresh per-pixel-per-frame noise eps_i(k). Its §9 ("Combining it all:
the best per-pixel dark estimator") compared several ways to dark-correct a
new frame and found that for a typical pixel, subtracting the fresh dark
frame's own spatial mean is nearly as good as subtracting a fully corrected
per-pixel estimate (predictor A vs. predictor C in that section's table differ
by only about 7% in RMS error) — so a per-pixel correction buys almost
nothing for almost every pixel. It also found, via a *corrected* outlier test
(§9's "self-caught bug fix": thresholding on a pixel's own full predictive
spread `sqrt(sigma_p^2 + sigma_eps^2/n)` rather than on the population spread
`sigma_p` alone, which had over-flagged 1589 of 23790 pixels), that a small
number of pixels — roughly a dozen per Bayer layer in that data set — are
genuine exceptions: their fixed offset is large enough that the frame mean is
a poor estimate for them specifically.

This module is the operational form of both findings: `identify_hot_pixels()`
runs the corrected outlier test against a fresh batch of dark frames, and
`correct_dark()` does the two-part correction the document's Decision 1
describes — subtract the frame's own spatial mean everywhere, then repair
just the known exceptions.

Read `tex/dark_signal_characterization.tex` first if the statistics below are
unfamiliar; this file assumes it and cites it by section rather than
re-deriving it.

No scipy, no matplotlib
------------------------
This is an engine module: it must import cleanly and run on the Raspberry Pi
Zero as well as the desktop (project rule; see CLAUDE.md/CODING_GUIDELINES.md
on hardware-free business logic and Pi resource limits). It depends on numpy
only. `desktop/padanalysis/hotpixel_analysis.py` is the matplotlib-using
companion that drives this module against real archives and makes figures;
nothing here imports a plotting library, and nothing here touches the
database (that is `src/hotpixel_repo.py`'s job).

Coordinate space
-----------------
Every coordinate this module accepts or returns is a plain `(y, x)` pixel
index into *some* Bayer-layer array — whichever one the caller handed it.
This module does not know or care whether that array was extracted from the
whole sensor or from a `bound`-cropped region; that is the caller's concern.

`hotpixel_repo` stores coordinates in **whole-frame Bayer-layer space** — the
same space `roi.to_layer_rect()` produces (see that module's "trap C") —
because that is what survives a `bound` ROI edit. A caller reading a stored
`HotPixel` list and applying it to a `bound`-cropped array must first shift
each coordinate by the bound's own layer-space origin:

    crop_y = stored_y - bound_layer.y
    crop_x = stored_x - bound_layer.x

where `bound_layer = roi.to_layer_rect(bound_rect)`. This module is
deliberately kept ignorant of `roi.py` and the database so that it has no
import cost on the Pi beyond numpy; `hotpixel_analysis.py` and the capture
path are where that shift actually happens.

Each Bayer layer is a separate population
------------------------------------------
R, Gr, Gb and B sit behind different colour filters on different physical
photosites. A defect that makes one layer's pixel at (y, x) run hot says
nothing about the other three layers' pixels at that same (y, x) — they are
different silicon. `identify_hot_pixels()` therefore takes a single-layer
stack and must be called once per Bayer layer; nothing here pools layers
together, and `hotpixel_repo`'s `bayer_layer` column is what keeps their
records apart on the way back in.

Two choices made here, and the fair alternatives not taken
-------------------------------------------------------------
**Correction by neighbor-median replacement, not a stored per-pixel offset.**
§9 of the tex document derives a shrinkage estimator `p_hat_i = B * r_i`
(precision-weighted combination of the pixel's own history and the
population) that would, in principle, correct a hot pixel more precisely than
its neighbors' median — it is, after all, fitted to that exact pixel's own
track record. That was a real alternative and was considered; it was set
aside in favor of neighbor-median replacement because (a) it needs no stored
number to go stale between recalibrations — only *which* pixels are hot,
never *by how much* — and (b) for the ordinary, non-hot population the
document's own Table 1 already shows the per-pixel estimate is barely better
than the frame mean, so the accuracy being given up applies only to the
dozen-or-so flagged pixels where an approximate local estimate is still a
large improvement over doing nothing.

**A symmetric two-sided test, not one restricted to unusually bright pixels.**
`identify_hot_pixels()` flags `|z| > k` in both directions. A pixel with a
large negative score runs unusually *low* rather than high — colloquially a
"cold" pixel rather than a hot one, and outside this module's name in the
strict sense. It is flagged and corrected identically to a hot pixel because
the underlying test and the neighbor-median repair make no distinction
between the two directions; splitting them into separate functions would add
an API surface this project's code has no present use for.

**Method-of-moments variance decomposition, not a MAD-based robust estimate.**
`desktop/padanalysis/duotone.py` already has a median-absolute-deviation
outlier test (`_paper_noise()`) that would have been a reasonable, more
outlier-resistant alternative to the variance decomposition used here. It was
not used because the tex document's own cross-check — the split-half
correlation matching the shrinkage weight `B` predicted from this exact
decomposition (§9 and §6) — is a validated result specific to this
computation; swapping in a different variance estimator would need its own
validation before it could be trusted the same way.

**A 4-connected ("plus") neighborhood for replacement, not 8-connected.**
`replace_hot_pixels()` uses each pixel's four orthogonal neighbors. An
8-connected (3x3, corners included) neighborhood is a fair alternative that
would average over more pixels; it was not used because dark noise here is
spatially independent from pixel to pixel (established in the earlier work
this tex document continues), so a same-population diagonal neighbor is no
better evidence than an orthogonal one, and four neighbors are cheaper to
combine than eight.

Public surface
--------------
    dark_pixel_scores(dark_stack)          -> np.ndarray, one score per pixel
    identify_hot_pixels(dark_stack, k=8.0) -> list[(y, x, score)]
    hot_pixel_mask(shape, coords)          -> np.ndarray[bool]
    replace_hot_pixels(image, mask)        -> np.ndarray
    correct_dark(lit_layer, dark_frame, hot_coords=()) -> np.ndarray

Maintenance:
    python src/darkframe.py     run the self-test (synthetic data only)
"""

from __future__ import annotations

import warnings
from typing import List, Sequence, Tuple

import numpy as np

# The four Bayer layers this module (and hotpixel_repo, and layermodel_repo)
# treat as independent populations. Matches BayerCam.extract_bayer_layers()'s
# keys.
BAYER_LAYERS = ("R", "Gr", "Gb", "B")

# A starting point, not a derived constant. tex/dark_signal_characterization
# .tex §9 reports roughly a dozen flagged pixels out of 23790 in its own data
# set using this same corrected test; the exact k that reproduces that count
# depends on the data pooled, so tune this against a real archive with
# desktop/padanalysis/hotpixel_analysis.py's find_and_report() (which plots
# the score distribution for exactly that purpose) rather than trusting this
# default blindly.
DEFAULT_K = 8.0


def dark_pixel_scores(dark_stack: np.ndarray) -> np.ndarray:
    """
    Return every pixel's departure from the population, in the units
    `identify_hot_pixels()` thresholds on.

    Implements the corrected outlier test from
    tex/dark_signal_characterization.tex §9:

        r_i        = mean over frames of pixel i's dark reading
        sigma_eps^2 = mean over pixels of each pixel's own frame-to-frame
                     variance                     (the fresh-noise floor)
        sigma_p^2  = max(Var(r_i) - sigma_eps^2 / n, 0)
                     (method-of-moments correction: the spread of r_i across
                     pixels overstates the true fixed-pattern spread by the
                     averaging noise still left in each r_i at n frames)
        z_i        = (r_i - mean(r)) / sqrt(sigma_p^2 + sigma_eps^2 / n)

    The denominator is r_i's own predictive standard deviation — how far a
    single pixel's n-frame average is expected to wander even if it carries
    no fixed offset at all — not sigma_p alone. Thresholding on sigma_p alone
    was tried first in the investigation this module implements and
    over-flagged the large majority of the population (1589 of 23790
    pixels); see that document's §9 for the worked comparison.

    Exposed separately from `identify_hot_pixels()` because choosing a
    threshold `k` against a real archive means looking at the whole
    distribution first, not only whatever survives a threshold already
    picked — see `desktop/padanalysis/hotpixel_analysis.py`'s
    `find_and_report()`, which plots exactly this array as a histogram
    against the chosen `k`.

    Args:
        dark_stack: Array of shape (n_frames, H, W), one Bayer layer's raw
            dark reading from each of n_frames independent dark captures, all
            at the same exposure and in the same coordinate space (whole-
            frame or a bound crop — this function does not care which, only
            that every frame shares it). n_frames must be at least 2.

    Returns:
        (H, W) array of z_i. All zero in the degenerate case where the
        population has no measurable spread at all (predictive variance is
        exactly 0) — an unrealistic input in practice, since real read noise
        alone gives sigma_eps^2 > 0.

    Raises:
        ValueError: `dark_stack` has fewer than 2 frames — sigma_eps^2 has
            no meaning from a single sample.

    Example:
        >>> rng = np.random.default_rng(0)
        >>> stack = 64.0 + rng.normal(0, 0.7, size=(50, 10, 10))
        >>> z = dark_pixel_scores(stack)
        >>> z.shape
        (10, 10)
    """
    stack = np.asarray(dark_stack, dtype=np.float64)
    if stack.ndim != 3 or stack.shape[0] < 2:
        raise ValueError(
            "dark_pixel_scores needs a (n_frames, H, W) stack with at "
            f"least 2 frames to estimate frame-to-frame noise; got shape "
            f"{stack.shape}")

    n = stack.shape[0]
    r = stack.mean(axis=0)                     # per-pixel mean over frames
    within_var = stack.var(axis=0, ddof=1)      # per-pixel frame-to-frame var
    sigma_eps2 = float(within_var.mean())       # pooled fresh-noise variance
    spatial_var = float(r.var(ddof=1))          # spread of the per-pixel means
    sigma_p2 = max(spatial_var - sigma_eps2 / n, 0.0)
    predictive_var = sigma_p2 + sigma_eps2 / n

    if predictive_var <= 0.0:
        return np.zeros_like(r)

    center = float(r.mean())
    return (r - center) / np.sqrt(predictive_var)


def identify_hot_pixels(dark_stack: np.ndarray, k: float = DEFAULT_K
                        ) -> List[Tuple[int, int, float]]:
    """
    Flag pixels whose dark level departs from the population, given a batch
    of dark frames for one Bayer layer.

    Thresholds `dark_pixel_scores()` at `|z| > k`; see that function for the
    statistics and tex/dark_signal_characterization.tex §9 for the
    derivation and the worked comparison against a naive (over-flagging)
    threshold.

    Args:
        dark_stack: Same shape and meaning as `dark_pixel_scores()`'s
            argument.
        k: Flag threshold, in units of the predictive standard deviation.
            See DEFAULT_K's docstring note on tuning this.

    Returns:
        (y, x, z) for each flagged pixel, y and x in `dark_stack`'s own
        coordinate space and z the signed score (positive: unusually high —
        a hot pixel in the ordinary sense; negative: unusually low — a
        "cold" pixel, flagged and later corrected the same way; see the
        module docstring's note on why this test is two-sided). Empty when
        nothing departs from the population.

    Raises:
        ValueError: `dark_stack` has fewer than 2 frames.

    Example:
        >>> rng = np.random.default_rng(0)
        >>> stack = 64.0 + rng.normal(0, 0.7, size=(50, 10, 10))
        >>> stack[:, 3, 3] += 5.0   # inject one hot pixel
        >>> found = identify_hot_pixels(stack, k=8.0)
        >>> (3, 3) in [(y, x) for y, x, _ in found]
        True
    """
    z = dark_pixel_scores(dark_stack)
    ys, xs = np.nonzero(np.abs(z) > k)
    return [(int(y), int(x), float(z[y, x])) for y, x in zip(ys, xs)]


def hot_pixel_mask(shape: Tuple[int, int],
                   coords: Sequence[Tuple[int, int]]) -> np.ndarray:
    """
    Build a boolean mask from a list of (y, x) coordinates.

    Args:
        shape: (H, W) of the array the mask will be applied to.
        coords: (y, x) pairs, already in that array's own coordinate space —
            see the module docstring's note on whole-frame vs. crop-relative
            coordinates. A coordinate outside `shape` is silently ignored,
            since a stored calibration should not fail an entire correction
            just because one point falls outside the current crop.

    Returns:
        Boolean array of shape `shape`, True at each in-bounds coordinate.

    Example:
        >>> hot_pixel_mask((3, 3), [(1, 1), (5, 5)])
        array([[False, False, False],
               [False,  True, False],
               [False, False, False]])
    """
    mask = np.zeros(shape, dtype=bool)
    h, w = shape
    for y, x in coords:
        if 0 <= y < h and 0 <= x < w:
            mask[y, x] = True
    return mask


def replace_hot_pixels(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """
    Replace masked pixels with the median of their four orthogonal neighbors.

    No scipy.ndimage — see the module docstring's Pi-compatibility note.
    Neighbors are taken via shifted, edge-padded slices; a masked pixel's own
    other-masked neighbors are excluded from its median where at least one
    unmasked neighbor remains, falling back to the plain 4-neighbor median
    (masked neighbors included) only when all four are themselves masked —
    which real hot-pixel maps essentially never produce, since they are a
    small, scattered fraction of the population.

    Args:
        image: 2-D array to repair.
        mask: Boolean array, same shape as `image`, True where a pixel
            should be replaced.

    Returns:
        A new array, same shape and dtype-promoted-to-float as `image`, with
        every True-masked pixel replaced and every other pixel unchanged.
        `image` itself is not modified.

    Example:
        >>> img = np.array([[1., 1., 1.], [1., 99., 1.], [1., 1., 1.]])
        >>> mask = np.array([[False]*3, [False, True, False], [False]*3])
        >>> replace_hot_pixels(img, mask)[1, 1]
        1.0
    """
    image = np.asarray(image, dtype=np.float64)
    mask = np.asarray(mask, dtype=bool)
    if not mask.any():
        return image.copy()

    h, w = image.shape
    padded = np.pad(image, 1, mode="edge")
    pmask = np.pad(mask, 1, mode="constant", constant_values=False)

    shifts = ((-1, 0), (1, 0), (0, -1), (0, 1))     # up, down, left, right
    neighbor_vals = np.empty((4, h, w), dtype=np.float64)
    neighbor_bad = np.empty((4, h, w), dtype=bool)
    for i, (dy, dx) in enumerate(shifts):
        neighbor_vals[i] = padded[1 + dy: 1 + dy + h, 1 + dx: 1 + dx + w]
        neighbor_bad[i] = pmask[1 + dy: 1 + dy + h, 1 + dx: 1 + dx + w]

    excluding_bad = np.where(neighbor_bad, np.nan, neighbor_vals)
    with warnings.catch_warnings():
        # A pixel whose all four neighbors are masked yields an all-NaN
        # column here; the resulting NaN is replaced below via all_bad, so
        # numpy's "All-NaN slice" warning would only be noise.
        warnings.simplefilter("ignore", category=RuntimeWarning)
        clean_median = np.nanmedian(excluding_bad, axis=0)
    all_bad = neighbor_bad.all(axis=0)
    fallback_median = np.median(neighbor_vals, axis=0)
    replacement = np.where(all_bad, fallback_median, clean_median)

    out = image.copy()
    out[mask] = replacement[mask]
    return out


def correct_dark(lit_layer: np.ndarray, dark_frame: np.ndarray,
                 hot_coords: Sequence[Tuple[int, int]] = ()) -> np.ndarray:
    """
    Dark-correct one lit Bayer layer: subtract the dark frame's own spatial
    mean, then repair the known hot pixels by neighbor-median replacement.

    This is tex/dark_signal_characterization.tex's Decision 1 in code: a
    fresh dark frame's *scalar* spatial mean is subtracted everywhere — not
    its raw per-pixel array, which that document's §9 found buys almost
    nothing for a typical pixel — and only the pixels already known to be
    exceptions get a further, local correction.

    Args:
        lit_layer: The captured layer to correct, 2-D, any numeric dtype.
        dark_frame: One fresh dark-frame layer taken at the same exposure,
            same shape as `lit_layer`. Only its mean is used; see Decision 1
            above for why the rest of it is deliberately not read.
        hot_coords: (y, x) pairs already in `lit_layer`'s own coordinate
            space — see the module docstring's note on shifting stored
            whole-frame coordinates into a bound crop's own space first.
            Empty (the default) degrades to plain mean subtraction, which is
            the correct behavior for a camera context with no calibration
            yet, or none at all.

    Returns:
        A new float64 array, same shape as `lit_layer`, dark-corrected.

    Example:
        >>> lit = np.full((4, 4), 700.0)
        >>> dark = np.full((4, 4), 64.0)
        >>> correct_dark(lit, dark, hot_coords=[])[0, 0]
        636.0
    """
    lit_layer = np.asarray(lit_layer, dtype=np.float64)
    dark_frame = np.asarray(dark_frame, dtype=np.float64)
    signal = lit_layer - float(dark_frame.mean())
    if not hot_coords:
        return signal
    mask = hot_pixel_mask(signal.shape, hot_coords)
    return replace_hot_pixels(signal, mask)


# ─────────────────────────────────────────────────────────────────────────────
# Self-test — synthetic data only, no camera, no database, no matplotlib.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    def check(label, got, want):
        ok = "OK " if got == want else "XX "
        print(f"  {ok}{label:56s} got={got!r}")
        assert got == want, f"{label}: {got!r} != {want!r}"

    rng = np.random.default_rng(1)

    print("== identify_hot_pixels finds an injected hot pixel ==")
    n_frames, H, W = 100, 40, 60
    clean = 64.0 + rng.normal(0, 0.73, size=(n_frames, H, W))
    injected = clean.copy()
    injected[:, 10, 20] += 6.0    # far outside the noise floor
    injected[:, 30, 5]  -= 5.5    # a "cold" pixel, same test, other sign
    found = identify_hot_pixels(injected, k=8.0)
    coords = [(y, x) for y, x, _ in found]
    check("hot pixel found", (10, 20) in coords, True)
    check("cold pixel found", (30, 5) in coords, True)
    check("hot pixel scores positive",
          [z for y, x, z in found if (y, x) == (10, 20)][0] > 0, True)
    check("cold pixel scores negative",
          [z for y, x, z in found if (y, x) == (30, 5)][0] < 0, True)

    print("== a clean population flags nothing ==")
    check("no false positives", identify_hot_pixels(clean, k=8.0), [])

    print("== identify_hot_pixels rejects too few frames ==")
    try:
        identify_hot_pixels(clean[:1])
        raise AssertionError("expected ValueError for a single frame")
    except ValueError:
        print("  OK  single frame raises ValueError")

    print("== hot_pixel_mask ==")
    m = hot_pixel_mask((5, 5), [(1, 1), (-1, 0), (100, 100)])
    check("in-bounds coordinate set", bool(m[1, 1]), True)
    check("out-of-bounds coordinates ignored, not raised", m.sum(), 1)

    print("== replace_hot_pixels ==")
    img = np.array([[1., 1., 1., 1.],
                    [1., 1., 1., 1.],
                    [1., 1., 1., 1.],
                    [1., 1., 1., 1.]])
    img[2, 2] = 500.0
    mask = np.zeros_like(img, dtype=bool)
    mask[2, 2] = True
    fixed = replace_hot_pixels(img, mask)
    check("hot pixel replaced by its neighbors' median", fixed[2, 2], 1.0)
    check("everything else untouched",
          np.array_equal(fixed[mask == False], img[mask == False]), True)
    check("original array not mutated", img[2, 2], 500.0)

    print("== replace_hot_pixels: two adjacent hot pixels still repair ==")
    img2 = np.ones((4, 4))
    img2[1, 1] = 500.0
    img2[1, 2] = 500.0
    mask2 = np.zeros_like(img2, dtype=bool)
    mask2[1, 1] = True
    mask2[1, 2] = True
    fixed2 = replace_hot_pixels(img2, mask2)
    check("both repaired using their unmasked neighbors",
          (fixed2[1, 1], fixed2[1, 2]), (1.0, 1.0))

    print("== replace_hot_pixels: no masked pixels is a no-op copy ==")
    untouched = replace_hot_pixels(img, np.zeros_like(img, dtype=bool))
    check("values unchanged", np.array_equal(untouched, img), True)

    print("== correct_dark ==")
    lit = np.full((6, 6), 700.0)
    dark = np.full((6, 6), 64.0)
    lit_with_hot = lit.copy()
    lit_with_hot[3, 3] = 900.0
    corrected = correct_dark(lit_with_hot, dark, hot_coords=[(3, 3)])
    check("ordinary pixels get plain mean subtraction",
          float(corrected[0, 0]), 636.0)
    check("hot pixel repaired by its neighbors",
          float(corrected[3, 3]), 636.0)
    check("no hot_coords degrades to plain mean subtraction",
          float(correct_dark(lit, dark)[0, 0]), 636.0)

    print("\n== all hotpixel self-tests passed ==")
