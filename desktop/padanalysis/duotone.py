"""duotone.py -- separating the two dyes on a pad, per pixel.

The Gutzeit product is not one colour.  Arsine adds to mercuric bromide in
steps, and the mono-substituted product is yellow where the tri-substituted
one is brown; `docs/gutzeit_protocol.md:63` asks for 415-430 nm because that
is where the yellow absorbs, and for 525-535 nm because that is where the
brown does.  A pad that is brown in the middle and yellow at its perimeter is
therefore not a pad with a bright middle -- it is two chemical states with
different spectra, and one number per pad cannot hold both.

The model this module fits, per pixel, in absorbance against the strip's own
bare paper:

    A[px, cell] = g[px] * 1 + y[px] * eps_y[cell] + b[px] * eps_b[cell]

`eps_y` and `eps_b` are the yellow and brown spectra, shared by every pixel
of every capture; `y` and `b` are per-pixel amounts, constrained non-negative
because a negative amount of a dye is not a thing; `g` is a per-pixel flat
term, free in sign and *deliberately discarded*.

Why the flat term is there and why it is discarded
--------------------------------------------------
A shadow multiplies the light reaching a pixel by the same factor in every
band, which in absorbance is the same *additive* offset in every cell.  The
pad rim is shadowed -- `report3.py` measured the strip's own unstained paper
falling off 11-24 % within 2-4 px of its edge -- so any pixel-level colour
statement that does not account for it is partly a statement about geometry.
Carrying `1` as a fitted component absorbs that offset before the dyes are
read, which is the same trick `report3.py` used when it divided one cell by
another, generalised to any number of cells.

The price is stated plainly: a spectrally flat absorber is indistinguishable
from shading in this instrument, so `y` and `b` measure *contrast*, not total
optical density.  For classifying a pixel as white, yellow or brown -- and
for counting how many pixels are which -- that is the right quantity anyway.

Coordinates and units
---------------------
Everything is in bound-crop Bayer-layer pixels (130 x 183 for bound 39), the
grid `probe.Capture.layer()` and `padrect.PadRect` already share.  Absorbance
is base-10, referred to the strip's own bare paper in the same frame under
the same illuminant, so bare paper sits at 0 by construction and an unstained
pad sits a little above it.

Cells are the 27 (LED, Bayer layer) pairs that carry a published flat-field
model (`whitecorr.load_models`), which are exactly the pairs where an LED
meaningfully excites a layer.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from padrect import PadRect  # noqa: E402
from probe import Capture, open_capture  # noqa: E402
from padphot import divisor_map  # noqa: E402
from whitecorr import load_models, rect_mask  # noqa: E402

#: Nominal LED peak wavelength in nm, from src/lighting_cal8.py.  White is
#: broadband and is parked past the red end so orderings put it last.
NM = {
    "violet": 410, "royal_blue": 455, "blue": 475, "cyan": 500, "green": 525,
    "amber": 590, "red_orange": 615, "red": 625, "photo_red": 660,
    "white": 999,
}

#: Screens lifted from measure.py/pca.py so every tool agrees on what a
#: usable exposure is.
SATURATED_DN = 1000
MIN_SIGNAL_DN = 25.0

#: Wavelength split used to tell the two fitted endmembers apart.  The yellow
#: product absorbs below the edge and the brown one carries on past it
#: (gutzeit_protocol.md:63-72), so the endmember with the larger long-side
#: share is the brown one.  White is excluded from the test -- it is a
#: broadband illuminant and belongs to neither side.
HUE_SPLIT_NM = 500


def cell_name(cell) -> str:
    """Return "led.layer", the spelling chemistry.cell_name already uses."""
    return f"{cell[0]}.{cell[1]}"


def cell_nm(cell) -> int:
    """Return a cell's LED wavelength, for ordering and for hue tests."""
    return NM[cell[0]]


class PadCube:
    """
    One capture's pad, as absorbance per pixel per cell.

    Attributes:
        capture: The open `probe.Capture`.
        pad: The fitted `padrect.PadRect`.
        cells: List of (led, layer) pairs, ordered by wavelength then layer.
        A: (n_px, n_cells) absorbance of the pad pixels against bare paper.
        depth: (n_px,) distance inside the pad edge, layer px.
        yx: (n_px, 2) row/column of each pad pixel, for painting maps back.
        shape: (rows, cols) of the layer grid.
        mask: (rows, cols) bool, the pad pixels A's rows correspond to.
        sigma: (n_cells,) per-pixel absorbance noise, measured on bare paper.
        rungs: {cell: rung} actually used.
        paper: (n_cells,) the bare-paper reflectance each cell was referred to.
    """

    def __init__(self, capture: Capture, models: dict, erode: float = 0.0):
        """
        Args:
            capture: Open capture, already positioned on a section.
            models: `whitecorr.load_models()` result, shared across captures.
            erode: Pad erosion in layer px.  The default keeps the rim:
                `report4.py` found the dye-sensitive cells get *worse* with
                deeper erosion, so the rim holds chemistry, and the flat term
                is here to deal with the shadow that comes with it.
        """
        self.capture = capture
        self.pad = PadRect(capture)
        self.shape = self.pad.tot.shape
        # Known hot pixels -- hot in any Bayer layer, from the `hotpixels`
        # table -- are left out of the pad, the paper and the chips alike,
        # so every cell keeps the same pixel positions (the cube's rows).
        # Left out, not repaired.  See analysis_1._hot.
        hot = capture.hot_mask()
        self.mask = self.pad.mask(erode) & ~hot
        paper_mask, paper_depth = self.pad.paper_mask()
        # The paper reference has to be bare paper, not the pad's own skirt:
        # trim it the same way measure.py does before it averages.
        paper_mask = paper_mask & (paper_depth > 2.0) & ~hot

        chip1 = rect_mask(self.shape, capture.rects["gray1"]) & ~hot
        chip2 = rect_mask(self.shape, capture.rects["gray2"]) & ~hot

        self.paper_mask = paper_mask
        cells, planes, rungs, paper, sigma = [], [], {}, [], []
        for cell, model in sorted(models.items(), key=lambda kv: (
                cell_nm(kv[0]), kv[0][1])):
            if not model.usable:
                continue
            plane = self._reflectance(cell, model, chip1, chip2, paper_mask)
            if plane is None:
                continue
            r, rung = plane
            ref = float(np.nanmedian(r[paper_mask]))
            if ref <= 0:
                continue
            a = -np.log10(np.maximum(r, 1e-6) / ref)
            cells.append(cell)
            planes.append(a)
            rungs[cell] = rung
            paper.append(ref)
            sigma.append(_paper_noise(a, paper_mask))

        self.cells = cells
        self.rungs = rungs
        self.paper = np.array(paper)
        self.sigma = np.array(sigma)
        cube = np.stack(planes, axis=-1)          # (rows, cols, n_cells)
        self.A = cube[self.mask]                  # (n_px, n_cells)
        # Adjacent-pixel differences on bare paper, kept as the raw material
        # for the noise covariance: same construction as _paper_noise, but
        # across all cells at once so their correlations survive.
        pair = paper_mask[:, :-1] & paper_mask[:, 1:]
        self.dpaper = ((cube[:, 1:, :] - cube[:, :-1, :])[pair]
                       / np.sqrt(2.0))            # (n_pairs, n_cells)
        self.depth = self.pad.depth[self.mask]
        ys, xs = np.nonzero(self.mask)
        self.yx = np.column_stack([ys, xs])

    def _reflectance(self, cell, model, chip1, chip2, paper_mask):
        """
        Return (reflectance plane, rung) for one cell, or None.

        Reflectance is signal over `padphot.divisor_map` -- the cell's
        published flat-field polynomial pegged to the two gray chips of this
        frame -- per pixel, so the light is corrected at every pad pixel and
        at every paper pixel separately.  (Until 2026-09-27 this was one
        number for the pad and paper together.)  The rung is the brightest
        one that clips nowhere that matters, so each cell is read where its
        signal-to-noise is best.
        """
        led, layer = cell
        if led not in self.capture.leds:
            return None          # a short sweep -- a pre-image holds two LEDs
        region = self.mask | paper_mask
        best = None
        for rung in self.capture.labels(led):
            got = self.capture.layer(led, rung, layer)
            if got is None:
                continue
            sig, raw = got
            if (raw[region | chip1 | chip2] >= SATURATED_DN).any():
                continue
            # A dead pixel reads 0 DN, which no illuminated sensor does, and
            # it survives every other screen: subtract the dark frame and it
            # is merely dim, take -log10 of it and it is an enormous
            # absorbance.  C1002's white/e2 R and Gr carry 38 such pixels.
            if (raw[region] <= 0).any():
                continue
            level = float(sig[self.mask].mean())
            if level < MIN_SIGNAL_DN:
                continue
            if best is None or level > best[0]:
                best = (level, sig, rung)
        if best is None:
            return None
        _, sig, rung = best
        try:
            div = divisor_map(model, self.capture.bound, chip1, chip2, sig)
        except ValueError:
            return None
        return np.maximum(sig, 1.0) / div, rung

    def paint(self, values, fill=np.nan) -> np.ndarray:
        """
        Return a (rows, cols) image with `values` written at the pad pixels.

        Args:
            values: (n_px,) or (n_px, k) array in this cube's pixel order.
            fill: What everything outside the pad gets.

        Returns:
            Image shaped (rows, cols) or (rows, cols, k).
        """
        values = np.asarray(values)
        shape = self.shape if values.ndim == 1 else self.shape + values.shape[1:]
        out = np.full(shape, fill, dtype=float)
        out[self.mask] = values
        return out


def _paper_noise(plane: np.ndarray, paper_mask: np.ndarray) -> float:
    """
    Per-pixel absorbance noise of one cell, measured on bare paper.

    Estimated from differences between horizontally adjacent paper pixels
    rather than from their spread: the paper carries a real illumination
    gradient, and a plain standard deviation would report that gradient as
    noise.  Differencing removes anything smooth and leaves what varies pixel
    to pixel, which is what a per-pixel decision is up against.  The 1/sqrt(2)
    converts the spread of a difference back to the spread of one pixel.

    Args:
        plane: (rows, cols) absorbance.
        paper_mask: (rows, cols) bool, bare paper.

    Returns:
        Robust sigma in absorbance units.
    """
    pair = paper_mask[:, :-1] & paper_mask[:, 1:]
    if not pair.any():
        return float("nan")
    d = (plane[:, 1:] - plane[:, :-1])[pair]
    # MAD rather than std: a speck of dust on the paper is not noise.
    mad = np.median(np.abs(d - np.median(d)))
    return float(1.4826 * mad / np.sqrt(2.0))


def flat_direction(n: int) -> np.ndarray:
    """Return the unit vector every cell shares -- the shading direction."""
    return np.ones(n) / np.sqrt(n)


def contrast(A: np.ndarray) -> np.ndarray:
    """
    Return `A` with the flat (shading) component projected out.

    A shadow is the same additive offset in every cell, so what survives this
    is the part of a pixel's spectrum that no shading can explain.
    """
    u = flat_direction(A.shape[1])
    return A - np.outer(A @ u, u)


def hue_plane(X: np.ndarray, n_components: int = 2):
    """
    Return the leading directions of the flat-removed pixel cloud.

    Args:
        X: (n_px, n_cells) contrast-space absorbance, pooled over captures.
        n_components: How many directions to keep.

    Returns:
        (basis, explained): `basis` is (n_components, n_cells) orthonormal
        rows; `explained` is the variance fraction of every component, so the
        caller can say how much a two-dye model leaves behind.
    """
    _, s, vt = np.linalg.svd(X, full_matrices=False)
    explained = s ** 2 / np.sum(s ** 2)
    return vt[:n_components], explained


def endmembers(X: np.ndarray, basis: np.ndarray, cells, weight=None,
               percentile: float = 2.0):
    """
    Find the yellow and brown spectra at the two edges of the pixel cloud.

    With two dyes and a non-negative amount of each, every pixel's contrast
    spectrum lies in the wedge between the two dyes' own spectra.  Projected
    onto the two leading directions the wedge is an arc, and the dyes sit at
    its ends -- so the endmembers are read off as extreme *angles*, not
    extreme lengths, which is what keeps a merely dark pixel from being
    mistaken for a pure one.

    Args:
        X: (n_px, n_cells) contrast-space absorbance.
        basis: (2, n_cells) from `hue_plane`.
        cells: The cell list, used to tell yellow from brown by wavelength.
        weight: Optional (n_px,) weights; pass the contrast magnitude so that
            pixels holding no dye do not vote on what the dyes look like.
        percentile: How far in from the extreme angle to sit, in percent.
            A hard min/max would be one hot pixel's opinion.

    Returns:
        (eps_y, eps_b, angles): unit spectra in cell space, and the (n_px,)
        angle of every pixel in the plane, for diagnostics.
    """
    s = X @ basis.T                                    # (n_px, 2)
    angles = np.arctan2(s[:, 1], s[:, 0])
    if weight is None:
        weight = np.linalg.norm(s, axis=1)

    # The arc must not straddle the +/-pi cut, or percentiles are nonsense.
    # Rotate to the weighted mean direction first, then rotate back.
    mean_ang = np.arctan2(np.sum(weight * np.sin(angles)),
                          np.sum(weight * np.cos(angles)))
    rel = np.angle(np.exp(1j * (angles - mean_ang)))
    keep = weight > np.percentile(weight, 50)          # dye-bearing half
    lo = np.percentile(rel[keep], percentile)
    hi = np.percentile(rel[keep], 100 - percentile)

    ends = []
    for ang in (lo, hi):
        d = np.array([np.cos(mean_ang + ang), np.sin(mean_ang + ang)])
        e = d @ basis                                  # back to cell space
        ends.append(e / np.linalg.norm(e))

    long_share = [_long_wave_share(e, cells) for e in ends]
    order = np.argsort(long_share)                     # yellow first
    return ends[order[0]], ends[order[1]], angles


def _long_wave_share(eps: np.ndarray, cells) -> float:
    """
    How much of a spectrum sits on the long side of the yellow/brown edge.

    The sign of a fitted direction is arbitrary, so compare the two ends the
    way the chemistry does: the brown product keeps absorbing past 500 nm
    where the yellow one has stopped.  Cells are averaged per side after the
    spectrum is put on a common footing (its own minimum removed), and white
    is left out because a broadband illuminant is not a wavelength.
    """
    e = eps - eps.min()
    short = [e[i] for i, c in enumerate(cells)
             if cell_nm(c) < HUE_SPLIT_NM]
    long = [e[i] for i, c in enumerate(cells)
            if HUE_SPLIT_NM <= cell_nm(c) < NM["white"]]
    if not short or not long:
        return float("nan")
    return float(np.mean(long) / max(np.mean(short), 1e-9))


def unmix(A: np.ndarray, eps_y: np.ndarray, eps_b: np.ndarray):
    """
    Split every pixel into shading, yellow and brown.

    Solves, per pixel and in closed form,

        min || A - g*1 - y*eps_y - b*eps_b ||^2   subject to y >= 0, b >= 0

    with `g` unconstrained.  Projecting the flat direction out of both the
    data and the endmembers turns it into a two-variable non-negative least
    squares, which for two variables is just three candidate solutions --
    interior, y clamped, b clamped -- so it vectorises over every pixel at
    once instead of iterating.

    Args:
        A: (n_px, n_cells) absorbance.
        eps_y: (n_cells,) yellow spectrum.
        eps_b: (n_cells,) brown spectrum.

    Returns:
        (g, y, b): three (n_px,) arrays.  `y` and `b` are in contrast
        absorbance units; `g` is the shading offset that was taken out.
    """
    u = flat_direction(A.shape[1])
    Xc = contrast(A)
    ey = eps_y - (eps_y @ u) * u
    eb = eps_b - (eps_b @ u) * u

    M = np.array([[ey @ ey, ey @ eb], [ey @ eb, eb @ eb]])
    rhs = np.column_stack([Xc @ ey, Xc @ eb])          # (n_px, 2)
    sol = rhs @ np.linalg.inv(M).T                     # unconstrained

    y, b = sol[:, 0].copy(), sol[:, 1].copy()
    # y < 0 means "no yellow": refit brown alone.  Same the other way.
    only_b = y < 0
    y[only_b] = 0.0
    b[only_b] = np.maximum(rhs[only_b, 1] / (eb @ eb), 0.0)
    only_y = b < 0
    b[only_y] = 0.0
    y[only_y] = np.maximum(rhs[only_y, 0] / (ey @ ey), 0.0)

    fit = np.outer(y, ey) + np.outer(b, eb)
    g = (A - fit) @ u
    return g, y, b


def residual_rms(A, eps_y, eps_b, y, b) -> float:
    """RMS of what the two-dye model could not explain, in absorbance."""
    u = flat_direction(A.shape[1])
    Xc = contrast(A)
    ey = eps_y - (eps_y @ u) * u
    eb = eps_b - (eps_b @ u) * u
    r = Xc - np.outer(y, ey) - np.outer(b, eb)
    return float(np.sqrt(np.mean(r ** 2)))


#: Pixel classes, in the order they are painted and counted.
CLASSES = ("white", "yellow", "brown")


def classify(y: np.ndarray, b: np.ndarray, floor: float,
             brown_at: float = 0.5) -> np.ndarray:
    """
    Label every pixel white, yellow or brown.

    Yellow and brown are the ends of a continuum, not two clusters, so a
    label is a choice of where to cut, not a discovery.  Two cuts are made
    and both are reported by whoever calls this: `floor` decides whether a
    pixel holds any dye at all, and `brown_at` decides which dye dominates.

    Args:
        y: (n_px,) yellow amount from `unmix`.
        b: (n_px,) brown amount.
        floor: Total amount below which a pixel counts as white.  Pass a
            multiple of the propagated noise, not a round number.
        brown_at: Brown fraction b/(y+b) at or above which a dyed pixel is
            called brown.

    Returns:
        (n_px,) int array indexing CLASSES.
    """
    total = y + b
    out = np.zeros(len(total), dtype=int)
    dyed = total >= floor
    frac = np.divide(b, np.maximum(total, 1e-12))
    out[dyed & (frac < brown_at)] = 1
    out[dyed & (frac >= brown_at)] = 2
    return out


def amount_sigma(sigma: np.ndarray, eps_y, eps_b) -> tuple:
    """
    Propagate per-cell noise into the fitted amounts.

    The fit is linear once the clamps are inactive, so the noise on `y` and
    `b` is the noise on the cells pushed through the same pseudo-inverse.
    This is what sets an honest "is there any dye here" threshold.

    Args:
        sigma: (n_cells,) per-pixel absorbance noise per cell.
        eps_y: (n_cells,) yellow spectrum.
        eps_b: (n_cells,) brown spectrum.

    Returns:
        (sigma_y, sigma_b, sigma_total) in the same units as y and b.
    """
    u = flat_direction(len(sigma))
    E = np.column_stack([eps_y - (eps_y @ u) * u, eps_b - (eps_b @ u) * u])
    # Cell noise is independent pixel to pixel; the flat projection mixes it,
    # so carry the full covariance rather than the diagonal.
    P = np.eye(len(sigma)) - np.outer(u, u)
    C = P @ np.diag(sigma ** 2) @ P.T
    pinv = np.linalg.inv(E.T @ E) @ E.T
    cov = pinv @ C @ pinv.T
    s_y, s_b = np.sqrt(np.diag(cov))
    s_tot = float(np.sqrt(cov[0, 0] + cov[1, 1] + 2 * cov[0, 1]))
    return float(s_y), float(s_b), s_tot


def noise_cov(cubes) -> np.ndarray:
    """
    Per-pixel noise covariance across cells, in absorbance squared.

    The correlations are the point, not a refinement.  Most of the pixel-to-
    pixel variation on paper is achromatic -- the surface is rough, and a
    fibre standing proud dims every band together -- so it lies along the
    same flat direction that shading does, and a projector that removes
    shading removes most of the noise with it.  Score subsets against a
    diagonal covariance and that cancellation is invisible, which flatters
    every subset equally and ranks them wrongly.

    Variances come from the robust per-cell sigmas and correlations from the
    sample covariance, so one speck of dust cannot inflate a variance while
    the correlation structure is still measured over everything.

    Args:
        cubes: One PadCube, or a {name: PadCube} mapping to pool over.

    Returns:
        (n_cells, n_cells) covariance.
    """
    if isinstance(cubes, dict):
        d = np.vstack([c.dpaper for c in cubes.values()])
        sigma = np.mean([c.sigma for c in cubes.values()], axis=0)
    else:
        d, sigma = cubes.dpaper, cubes.sigma
    corr = np.corrcoef(d.T)
    return np.outer(sigma, sigma) * corr


def j_hue(idx, eps_y, eps_b, cov) -> float:
    """
    Contrast-to-noise between pure yellow and pure brown, from a cell subset.

    The best shading-immune linear index of the cells in `idx` is the one
    maximising |w.(eps_b - eps_y)| / sqrt(w.cov.w) subject to w.1 = 0, and
    this returns the value it reaches: how many noise sigmas separate a pure
    brown pixel from a pure yellow one of the same spectral magnitude.

    Closed form, with d the endmember difference and u the all-ones vector:

        J^2 = d' C^-1 d - (u' C^-1 d)^2 / (u' C^-1 u)

    The subtracted term is the price of shading immunity.  At k = 1 the two
    terms are identical and J is exactly zero -- one cell cannot tell a lot
    of yellow from a little brown, whatever its signal-to-noise.

    Args:
        idx: Sequence of cell indices.
        eps_y: (n_cells,) yellow spectrum.
        eps_b: (n_cells,) brown spectrum.
        cov: (n_cells, n_cells) noise covariance.

    Returns:
        J in sigmas per unit amount.
    """
    idx = np.asarray(idx)
    C = cov[np.ix_(idx, idx)]
    d = (eps_b - eps_y)[idx]
    u = np.ones(len(idx))
    Ci = np.linalg.inv(C)
    a = float(d @ Ci @ d)
    b = float(u @ Ci @ d)
    c = float(u @ Ci @ u)
    return float(np.sqrt(max(a - b * b / c, 0.0)))


def j_amounts(idx, eps_y, eps_b, cov) -> tuple:
    """
    Precision on the yellow and brown amounts separately, from a subset.

    Where `j_hue` asks which dye dominates, this asks how well each amount is
    pinned down once the other dye *and* the shading have been marginalised
    out -- the quantity a "how much brown is here" reading rests on.  It is
    the Fisher information of (y, b) after profiling out the flat term.

    Fewer than three cells cannot do it: removing the shading direction
    leaves k-1 dimensions, and two amounts need two.  At k = 2 the
    information matrix is singular and both precisions are returned as zero,
    which is the exact statement of what the third cell buys.

    Args:
        idx: Sequence of cell indices.
        eps_y: (n_cells,) yellow spectrum.
        eps_b: (n_cells,) brown spectrum.
        cov: (n_cells, n_cells) noise covariance.

    Returns:
        (J_y, J_b), reciprocal standard errors in units of 1/amount.
    """
    idx = np.asarray(idx)
    if len(idx) < 3:
        return 0.0, 0.0
    C = cov[np.ix_(idx, idx)]
    E = np.column_stack([eps_y[idx], eps_b[idx]])
    u = np.ones(len(idx))
    Ci = np.linalg.inv(C)
    # Information on (y, b) with g profiled out.
    M = E.T @ Ci @ E - np.outer(E.T @ Ci @ u, u @ Ci @ E) / float(u @ Ci @ u)
    if np.linalg.matrix_rank(M, tol=1e-12) < 2:
        return 0.0, 0.0
    var = np.linalg.inv(M)
    return float(1 / np.sqrt(var[0, 0])), float(1 / np.sqrt(var[1, 1]))


def subset_sigma(idx, eps_y, eps_b, cov, shading: bool = True) -> tuple:
    """
    Noise on the amounts a subset would report, in the same units as y and b.

    A subset with fewer cells is noisier, so judging its verdicts against a
    threshold derived from all 26 cells would fail it for being asked the
    wrong question.  Each subset gets its own white/dyed cut from this.

    Args:
        idx: Sequence of cell indices.
        eps_y: (n_cells,) yellow spectrum.
        eps_b: (n_cells,) brown spectrum.
        cov: (n_cells, n_cells) noise covariance.

    Args (continued):
        shading: Whether to leave the flat term free.  Set False to price a
            pair, which can identify the two amounts only by assuming the
            illumination is already known.

    Returns:
        (sigma_y, sigma_b, sigma_total); infinities where the amounts are
        unidentifiable, which is any subset of fewer than three cells that
        also insists on shading immunity.
    """
    idx = np.asarray(idx)
    if len(idx) < (3 if shading else 2):
        return float("inf"), float("inf"), float("inf")
    C = cov[np.ix_(idx, idx)]
    E = np.column_stack([eps_y[idx], eps_b[idx]])
    u = np.ones(len(idx))
    Ci = np.linalg.inv(C)
    M = E.T @ Ci @ E
    if shading:
        M = M - np.outer(E.T @ Ci @ u, u @ Ci @ E) / float(u @ Ci @ u)
    if np.linalg.matrix_rank(M, tol=1e-12) < 2:
        return float("inf"), float("inf"), float("inf")
    var = np.linalg.inv(M)
    return (float(np.sqrt(var[0, 0])), float(np.sqrt(var[1, 1])),
            float(np.sqrt(var[0, 0] + var[1, 1] + 2 * var[0, 1])))


def subset_unmix(A, idx, eps_y, eps_b, cov, shading: bool = True):
    """
    Unmix using only the cells in `idx`, weighting by the noise covariance.

    Args:
        A: (n_px, n_cells) absorbance.
        idx: Sequence of cell indices to use.
        eps_y: (n_cells,) yellow spectrum.
        eps_b: (n_cells,) brown spectrum.
        cov: (n_cells, n_cells) noise covariance.
        shading: Whether to fit the flat term.  False assumes the
            illumination is flat and known, which is the only way two cells
            can report an amount at all.

    Returns:
        (y, b) each (n_px,), non-negative.
    """
    idx = np.asarray(idx)
    C = cov[np.ix_(idx, idx)]
    Ci = np.linalg.inv(C)
    X = A[:, idx]
    cols = [eps_y[idx], eps_b[idx]]
    if shading:
        cols.append(np.ones(len(idx)))
    E = np.column_stack(cols)
    M = E.T @ Ci @ E
    sol = np.linalg.pinv(M) @ (E.T @ Ci @ X.T)     # (3, n_px)
    y, b = sol[0].copy(), sol[1].copy()
    only_b = y < 0
    only_y = b < 0
    y[only_b] = 0.0
    b[only_y] = 0.0
    return np.maximum(y, 0.0), np.maximum(b, 0.0)


def render_rgb(cube: PadCube, gamma: float = 2.2,
               clip_percentile: float = 99.5,
               region: np.ndarray = None) -> np.ndarray:
    """
    Build a true-colour picture of the crop from the white LED.

    Not part of the measurement -- this exists so a person can look at the
    pad and at what the model made of it side by side, on the same grid and
    the same pixels.  White balance comes from the gray chip, which is the
    one surface in frame that is neutral by construction.

    Args:
        cube: A built `PadCube` (for its capture and layer grid).
        gamma: Display gamma applied after normalisation.
        clip_percentile: Where full white is put, to keep one hot pixel from
            darkening the whole picture.
        region: Where the picture has to be right, as a bool image.  Only
            here is clipping disqualifying: a specular glint on the stage,
            far outside the crop, once vetoed every rung of C1009's green
            layers and left the panel black.  Defaults to the pad and the
            chip the balance is taken from.

    Returns:
        (rows, cols, 3) float array in 0..1, sRGB-ish.
    """
    c = cube.capture
    chip = rect_mask(cube.shape, c.rects["gray1"])
    if region is None:
        region = cube.mask | chip
    else:
        region = region | chip
    planes = {}
    for layer in ("R", "Gr", "Gb", "B"):
        best = None
        for rung in c.labels("white"):
            got = c.layer("white", rung, layer)
            if got is None:
                continue
            sig, raw = got
            if (raw[region] >= SATURATED_DN).any() or (raw[region] <= 0).any():
                continue
            level = float(sig[chip].mean())
            if best is None or level > best[0]:
                best = (level, sig)
        if best is None:
            return np.zeros(cube.shape + (3,))
        planes[layer] = best[1] / max(best[0], 1e-6)   # chip -> 1.0

    rgb = np.stack([planes["R"],
                    0.5 * (planes["Gr"] + planes["Gb"]),
                    planes["B"]], axis=-1)
    scale = np.percentile(rgb, clip_percentile)
    return np.clip(rgb / max(scale, 1e-6), 0, 1) ** (1.0 / gamma)


def build(names, erode: float = 0.0, models: dict = None) -> dict:
    """
    Build one `PadCube` per capture, on a cell set they all share.

    Cells are intersected rather than assumed: an LED that clipped on one
    strip is simply absent there, and a matrix with a hole in it is worse
    than a narrower matrix.

    Args:
        names: Capture labels, as `probe.open_capture` takes them.
        erode: Pad erosion in layer px.
        models: `whitecorr.load_models()` result; loaded if not given.

    Returns:
        {name: PadCube}, every cube restricted to the shared cell list.
    """
    models = load_models() if models is None else models
    cubes = {}
    for name in names:
        cubes[name] = PadCube(open_capture(name), models, erode=erode)

    shared = set(cubes[names[0]].cells)
    for cube in cubes.values():
        shared &= set(cube.cells)
    shared = sorted(shared, key=lambda c: (cell_nm(c), c[1]))

    for cube in cubes.values():
        keep = [cube.cells.index(c) for c in shared]
        cube.A = cube.A[:, keep]
        cube.dpaper = cube.dpaper[:, keep]
        cube.sigma = cube.sigma[keep]
        cube.paper = cube.paper[keep]
        cube.cells = list(shared)
    return cubes


def fit(cubes: dict, percentile: float = 2.0):
    """
    Fit the shared yellow and brown spectra over every capture at once.

    One basis for all strips is the whole point: two pads can only be
    compared if "brown" means the same spectrum on both.

    Args:
        cubes: {name: PadCube} from `build`.
        percentile: Passed to `endmembers`.

    Returns:
        (eps_y, eps_b, info) where `info` carries the explained variance of
        the pooled cloud, the per-capture pixel counts and the fitted angles.
    """
    names = list(cubes)
    X = np.vstack([contrast(cubes[n].A) for n in names])
    basis, explained = hue_plane(X)
    cells = cubes[names[0]].cells
    eps_y, eps_b, angles = endmembers(X, basis, cells, percentile=percentile)
    info = {
        "explained": explained,
        "basis": basis,
        "angles": angles,
        "counts": {n: len(cubes[n].A) for n in names},
        "cells": cells,
    }
    return eps_y, eps_b, info


if __name__ == "__main__":
    names = sys.argv[1:] or ["C1002", "C1003", "C1004", "C1005",
                             "C1008", "C1009", "C1014"]
    cubes = build(names)
    cells = cubes[names[0]].cells
    print(f"{len(cells)} shared cells over {len(names)} captures")
    eps_y, eps_b, info = fit(cubes)
    ev = info["explained"]
    print(f"contrast-space variance: PC1 {ev[0]:.3f}  PC2 {ev[1]:.3f}  "
          f"PC3 {ev[2]:.3f}  PC4 {ev[3]:.3f}")
    print(f"{'cell':>16} {'nm':>5} {'eps_y':>8} {'eps_b':>8}")
    for i, c in enumerate(cells):
        print(f"{cell_name(c):>16} {cell_nm(c):5d} "
              f"{eps_y[i]:+8.3f} {eps_b[i]:+8.3f}")
    for n in names:
        cube = cubes[n]
        g, y, b = unmix(cube.A, eps_y, eps_b)
        s_y, s_b, s_tot = amount_sigma(cube.sigma, eps_y, eps_b)
        lab = classify(y, b, floor=3 * s_tot)
        counts = [int((lab == i).sum()) for i in range(len(CLASSES))]
        rms = residual_rms(cube.A, eps_y, eps_b, y, b)
        print(f"{n}: px={len(y):5d} white={counts[0]:5d} yellow={counts[1]:5d} "
              f"brown={counts[2]:5d} | resid={rms:.4f} A  "
              f"sigma_tot={s_tot:.4f} A  median sigma_cell="
              f"{np.median(cube.sigma):.4f} A")
