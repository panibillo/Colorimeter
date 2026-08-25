"""padphot.py -- the photometric quantities, one function each.

Every function here is a pure transform of numpy arrays.  Nothing opens a
file, a database or an archive, so each one can be read, checked and
re-derived on its own, and each carries the formula it implements and the
reference that formula is measured against.

The chain, from sensor counts to the quantity a dose is fitted against:

    sig   = lit - dark                    counts, done by probe.Capture.layer
    div   = divisor(...)                  the gray-chip level at this region
    R     = reflectance(sig, div)         referred to the gray chips
    R_inf = reflectance_infinity(R, Rw)   referred to a diffuse white
    A     = absorbance(R_inf)             -log10(R)
    f     = km_remission(R_inf)           (1-R)^2 / 2R  =  K/S
    dKS   = delta_ks(f, f_blank)          the dose-proportional quantity

Three things about this chain are easy to get wrong, and are the reason the
steps are separate functions rather than one:

**K and S do not come apart.**  Kubelka-Munk gives only their *ratio*.  One
reflectance measurement cannot yield K and S individually; that needs a
second measurement of the same layer over a second backing -- which is also
the test of assumption (i), optical thickness.  So there is a
`km_remission()` returning K/S and there is deliberately no `absorption_K()`
or `scattering_S()`.

**f(R) is scale-sensitive.**  It is non-linear, so the choice of white
reference does *not* divide out.  Referring R to the gray chips and
referring it to the strip's own bare paper give different K/S, and the
difference is not a constant factor.  `reflectance_infinity()` therefore
demands the reference explicitly instead of defaulting to one.

**A and f(R) are different linearising transforms** of the same measurement,
not two names for one thing.  `chemistry.Reduction.A/dA` uses A;
`tex/gutzeit_analysis.tex` sec. "Reflectance photometry" argues for f(R).
Both are here so they can be compared on real pads.

Every function accepts a `numpy.ma.MaskedArray` and returns one, so a region
mask carries through the whole chain untouched.

Reference: tex/gutzeit_analysis.tex, section "Reflectance photometry",
equations (eq:km) and (eq:dks).
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from whitecorr import anchor_weights  # noqa: E402

#: Reflectances at or below this are treated as this, so -log10 and the
#: Kubelka-Munk denominator stay finite.  A dead pixel reads 0 counts, which
#: no illuminated sensor does; see duotone.PadCube._reflectance.
MIN_REFLECTANCE = 1e-6


# -- step 1: the gray-card level --------------------------------------------

def divisor(model, region, chip1, chip2, sig) -> float:
    """
    The gray-chip signal level as seen at one region, in counts.

    The two gray chips sit either side of the strip and the illumination is
    not flat between them, so neither chip alone is the reference for a
    region lying between them.  `whitecorr.anchor_weights` reads the cell's
    fitted illumination surface and returns the pair of weights that
    interpolate the chips to the region actually being measured; this is
    their weighted sum.

        divisor = w1 * mean(sig[chip1]) + w2 * mean(sig[chip2])

    Args:
        model: The published cell, a `whitecorr.LayerModel`, carrying the
            flat-field surface and the anchor form ('1anchor'/'2anchor').
        region: Boolean mask, shape (rows, cols), of the pixels being
            measured.  The weights depend on *where* the region is, so pass
            the region you will divide, not a nominal box.
        chip1: Boolean mask of the left gray chip (roi 'gray1').
        chip2: Boolean mask of the right gray chip (roi 'gray2').
        sig: Dark-subtracted signal over the whole bound crop, in counts.

    Returns:
        The divisor in counts.  Positive on any usable cell; a value at or
        below zero means the chips carry no signal and the cell must be
        dropped rather than divided by.

    Example:
        >>> div = divisor(model, pad.mask(6), chip1, chip2, sig)
        ...                                          # doctest: +SKIP
        >>> round(div)                               # doctest: +SKIP
        438
    """
    w1, w2, *_ = anchor_weights(model, region, chip1, chip2)
    return w1 * float(np.mean(sig[chip1])) + w2 * float(np.mean(sig[chip2]))


# -- step 2: reflectance -----------------------------------------------------

def reflectance(sig, div):
    """
    Reflectance referred to the gray chips.

        R = sig / div

    A surface reading R = 1.0 is exactly as bright as the gray chips at that
    position, under that illuminant, in that Bayer layer.  Bare strip paper
    runs near 2.0 (it is roughly twice as bright as the chips); a heavily
    stained pad in the blue runs well below 1.0.

    This is the quantity `measure.py` reports as `ratio` and the one
    `duotone.PadCube` builds its cube from.  It is *not* an absolute
    reflectance: the gray chips are a working reference, not a calibrated
    diffuse white standard.

    Args:
        sig: Dark-subtracted signal in counts.  ndarray or MaskedArray.
        div: The gray-chip level from `divisor()`, in counts.

    Returns:
        Reflectance, same shape and masking as `sig`.

    Raises:
        ValueError: `div` is not positive.

    Example:
        >>> reflectance(np.array([100.0, 400.0]), 400.0)
        array([0.25, 1.  ])
    """
    if not div > 0:
        raise ValueError(f"divisor must be positive, got {div!r}")
    return sig / div


def reflectance_infinity(R, R_white):
    """
    Reflectance re-referred to a diffuse white standard: R_inf.

        R_inf = R / R_white

    Kubelka-Munk assumption (iv) is that R is measured against a stable
    diffuse white.  `reflectance()` gives R against the gray chips, which
    are neither white nor a standard, so it has to be rescaled before
    `km_remission()` means anything.

    Two references are available on this instrument and they are not
    interchangeable:

      * **the strip's own bare paper** -- `PadRect.paper_mask()`.  Same
        paper, same strip, same illumination, no chemistry.  Cancels
        anything that changed between one session's gray card and the
        next's, which is why `chemistry.Reduction` uses it (as `rho`).  It
        is not a perfect diffuse reflector either, but it is the closest
        thing in the frame and it is attached to the sample.
      * **a measured white standard** -- a captured reference of known
        diffuse reflectance, if one is ever put in the frame.

    Because f(R) is non-linear, the choice matters and does not divide out
    downstream.  Pass it explicitly; there is no default.

    Args:
        R: Reflectance from `reflectance()`.
        R_white: The white reference, in the same units as R.  A scalar (the
            median of R over the reference region) or an array of R's shape
            if the reference has been fitted per pixel.

    Returns:
        R_inf, same shape and masking as `R`.  Approximately 1.0 wherever
        the reference region itself is measured -- that is the arithmetic
        check that the reference was applied correctly.

    Raises:
        ValueError: `R_white` is not positive.

    Example:
        >>> reflectance_infinity(np.array([0.5, 1.0]), 2.0)
        array([0.25, 0.5 ])
    """
    if np.any(np.asarray(R_white) <= 0):
        raise ValueError("R_white must be positive")
    return R / R_white


# -- step 3a: the absorbance transform --------------------------------------

def absorbance(R):
    """
    Decadic absorbance of a reflectance.

        A = -log10(R)

    A is linear in absorber concentration for *transmission* through a
    non-scattering medium (Beer-Lambert).  A stain on a scattering paper pad
    is neither, so on this instrument A is an empirical linearising
    transform, useful and conventional but not a physical absorbance.  Where
    a dose response is wanted from first principles, `km_remission()` is the
    transform with a theory behind it.

    A is 0 wherever R is 1, negative where the surface is brighter than its
    reference, and grows without bound as R approaches 0.  Reflectances at
    or below `MIN_REFLECTANCE` are clamped to it, so a dead pixel gives a
    large finite number rather than infinity.

    Args:
        R: Reflectance, against whichever reference you chose.

    Returns:
        Absorbance, same shape and masking as `R`.

    Example:
        >>> absorbance(np.array([0.1, 0.01, 0.001]))
        array([1., 2., 3.])
    """
    return -np.log10(np.maximum(R, MIN_REFLECTANCE))


# -- step 3b: the Kubelka-Munk transform ------------------------------------

def km_remission(R_inf):
    """
    The Kubelka-Munk remission function: f(R_inf) = K/S.

        f(R_inf) = (1 - R_inf)^2 / (2 R_inf)  =  K / S

    Equation (eq:km) of tex/gutzeit_analysis.tex.  For an optically thick,
    diffusely scattering layer this is the transform that is linear in
    absorber, where -log10(R) is not.  K is the absorption coefficient and S
    the scattering coefficient; only their **ratio** is observable from one
    reflectance measurement, which is why this function returns K/S and
    there is no `absorption_K()` beside it.  Separating them needs the same
    pad measured over two different backings.

    Assumptions, from the tex, in the order they are likely to fail here:

      (ii) isotropic diffuse illumination and collection.  A directional LED
           and a camera approximate this only roughly, so treat f(R) as an
           empirical linearising transform, not an absolute K/S.
      (iii) S independent of wavelength and of the absorber.  Violated by
           wetting -- see `ks_wetting_corrected()`.
      (iv) R_inf measured against a stable diffuse white.  See
           `reflectance_infinity()`.
      (i)  the layer is optically thick.  Test by measuring a pad over a
           white and over a black backing and confirming they agree.

    **Trap.**  f is *not* monotone in R.  It has a minimum of 0 at R = 1 and
    rises on both sides, so a pad reading a little brighter than its white
    reference gets a positive K/S indistinguishable from one reading a
    little darker.  On a blank pad, where noise straddles R = 1, this folds
    negative excursions into positive ones and biases the mean K/S upward.
    Use `km_remission_signed()` where that matters.

    Args:
        R_inf: Reflectance against a diffuse white, from
            `reflectance_infinity()`.

    Returns:
        K/S, same shape and masking as `R_inf`.  Zero where R_inf is 1,
        growing without bound as R_inf approaches 0.

    Example:
        >>> np.round(km_remission(np.array([1.0, 0.5, 0.25])), 4)
        array([0.   , 0.25 , 1.125])
    """
    r = np.maximum(R_inf, MIN_REFLECTANCE)
    return (1.0 - r) ** 2 / (2.0 * r)


def km_remission_signed(R_inf):
    """
    The remission function carrying the sign of (1 - R_inf).

        f_signed(R) = sign(1 - R) * (1 - R)^2 / (2 R)

    Identical to `km_remission()` for R_inf <= 1, and the negative of it
    above.  That makes it monotone decreasing in R over the whole range, so
    photometric noise around a blank pad averages toward zero instead of
    accumulating a positive bias.  Use it whenever K/S is averaged over a
    region that includes pixels brighter than the white reference -- which
    means, in practice, every blank and every lightly loaded pad.

    It is not the textbook quantity.  Anything reported as "K/S" should come
    from `km_remission()`; this one is for the intermediate arithmetic.

    Args:
        R_inf: Reflectance against a diffuse white.

    Returns:
        Signed K/S, same shape and masking as `R_inf`.

    Example:
        >>> np.round(km_remission_signed(np.array([0.5, 1.0, 2.0])), 4)
        array([ 0.25,  0.  , -0.25])
    """
    r = np.maximum(R_inf, MIN_REFLECTANCE)
    return np.sign(1.0 - r) * (1.0 - r) ** 2 / (2.0 * r)


def km_reflectance(f):
    """
    Invert the remission function: the reflectance that would give f = K/S.

        R = (1 + f) - sqrt((1 + f)^2 - 1)

    The root at or below 1, which is the physical branch.  Present so the
    forward transform can be round-tripped -- it is the self-test at the
    bottom of this module -- and so a modelled K/S can be turned back into a
    predicted reflectance for comparison with a measurement.

    Args:
        f: K/S, non-negative.

    Returns:
        Reflectance in (0, 1], same shape and masking as `f`.

    Example:
        >>> np.round(km_reflectance(np.array([0.0, 0.25, 1.125])), 4)
        array([1.  , 0.5 , 0.25])
    """
    a = 1.0 + np.asarray(f, dtype=np.float64)
    return a - np.sqrt(a * a - 1.0)


# -- step 4: the dose-proportional quantity ---------------------------------

def delta_ks(f_exposed, f_blank):
    """
    The blank-corrected remission: Delta(K/S).

        Delta(K/S) = f(R)_exposed - f(R)_blank

    Equation (eq:dks) of tex/gutzeit_analysis.tex.  K is proportional to
    chromophore concentration while S is a property of the paper, so
    subtracting an unexposed pad measured in the same band removes the
    paper's own contribution and leaves a quantity approximately
    proportional to arsenic loading.  **Only Delta(K/S) is that**; neither
    f(R) nor R nor A is.

    Which blank matters, and the two are separable effects: a strip's own
    pre-image (`/pre` in its own archive) against a pooled average over
    every pre-image in the inventory.  `chemistry.Reduction(blank=...)`
    makes the same distinction for absorbance and measured 0.003-0.009 A
    between them on the three strips that had both.

    Args:
        f_exposed: K/S of the exposed pad, from `km_remission()`.
        f_blank: K/S of the unexposed pad in the same band, either a scalar
            region mean or an array of the same shape for a per-pixel blank.

    Returns:
        Delta(K/S), same shape and masking as `f_exposed`.  Exactly zero
        where the two agree.

    Example:
        >>> delta_ks(np.array([1.125, 0.25]), 0.25)
        array([0.875, 0.   ])
    """
    return f_exposed - f_blank


def ks_wetting_corrected(dks, dks_ref, beta):
    """
    Remove the neutral darkening of a wet pad from Delta(K/S).

        Delta(K/S)_corr = Delta(K/S)_lambda - beta * Delta(K/S)_ref

    Water darkens paper *neutrally*, by changing S across all wavelengths.
    Arsenic darkens *selectively* in the blue, by changing K.  A reference
    band at 620-670 nm (red, photo_red) therefore measures wetting almost
    independently of arsenic loading, and subtracting a calibrated multiple
    of it removes the wetting from the analytical bands.  The tex calls this
    mandatory given the condensation risk in a near-saturated gas stream; a
    single-band instrument reads a damp blank as arsenic and cannot tell.

    Beware the other reading of the same arithmetic: Hg3As2 and metallic
    deposits are also spectrally flat, so this subtraction removes genuine
    flat absorbers along with the water.  The flat-versus-edge distinction
    is what the red band resolves; it does not distinguish two flat causes.

    Args:
        dks: Delta(K/S) in the analytical band.
        dks_ref: Delta(K/S) in the red reference band, same shape.
        beta: The calibration coefficient for this band, from deliberately
            wetted blank pads.  Not yet measured on this instrument -- until
            it is, this function is a hypothesis generator, not a
            correction.

    Returns:
        Corrected Delta(K/S), same shape and masking as `dks`.

    Example:
        >>> ks_wetting_corrected(np.array([0.9, 0.5]), np.array([0.1, 0.1]),
        ...                      beta=1.0)
        array([0.8, 0.4])
    """
    return dks - beta * dks_ref


# -- step 5: the two spatial readouts ---------------------------------------

def sigma_low_range(dks, region=None, pixel_area=1.0) -> float:
    """
    The low-range readout: Delta(K/S) integrated over the pad.

        Sigma = sum over pixels of Delta(K/S)_blue * dA

    An area-*averaged* spectrum cannot tell "uniformly half-substituted"
    from "core saturated, periphery untouched" -- the two integrate the
    same at very different loadings.  Since saturation is spatially
    localised, the spectrum has to be evaluated per pixel and integrated
    afterward, which is exactly what a camera can do and a single-aperture
    colorimeter cannot.  This is that integral, and it is linear in dose
    below the saturation onset n0.

    Args:
        dks: Delta(K/S) per pixel in a blue band, ndarray or MaskedArray.
        region: Optional boolean mask selecting the pad pixels.  If `dks` is
            already a MaskedArray over the pad, leave this None.
        pixel_area: Area of one layer pixel, in whatever unit the result
            should carry.  One layer pixel is 0.15-0.19 mm on the pad, so
            pass e.g. 0.17**2 for mm^2; the default leaves it in pixels.

    Returns:
        The integral, a float.

    Example:
        >>> sigma_low_range(np.array([[1.0, 2.0], [3.0, 4.0]]))
        10.0
    """
    v = dks if region is None else dks[region]
    return float(np.sum(v) * pixel_area)


def area_saturated(saturated, pixel_area=1.0) -> float:
    """
    The high-range readout: the area of pad classified as saturated.

    Above the onset n0 the stain stops responding linearly and the loading
    shows instead as the *extent* of the saturated core, which grows
    logarithmically with dose.  The two readouts are spliced at n0,
    determined empirically.

    Args:
        saturated: Boolean array, True where the pixel is classified
            saturated.  How that call is made -- a Delta(K/S) threshold, or
            `duotone.classify`'s brown verdict -- is the caller's choice and
            is the part that needs empirical support.
        pixel_area: Area of one layer pixel; see `sigma_low_range`.

    Returns:
        The area, a float, in units of `pixel_area`.

    Example:
        >>> area_saturated(np.array([[True, False], [True, True]]))
        3.0
    """
    return float(np.count_nonzero(saturated) * pixel_area)


# -- self-test ---------------------------------------------------------------

def _selftest() -> int:
    """Check the identities the rest of the analysis will lean on."""
    import doctest
    fails = []

    def check(name, ok, detail=""):
        print(f"  {'PASS' if ok else 'FAIL'}  {name}"
              + (f"   {detail}" if detail else ""))
        if not ok:
            fails.append(name)

    R = np.linspace(0.01, 1.0, 400)
    f = km_remission(R)

    check("f(1) == 0", km_remission(np.array([1.0]))[0] == 0.0)
    check("A(1) == 0", absorbance(np.array([1.0]))[0] == 0.0)
    check("f decreasing on (0, 1]", np.all(np.diff(f) < 0),
          f"max diff {np.max(np.diff(f)):+.3e}")

    back = km_reflectance(f)
    err = float(np.max(np.abs(back - R)))
    check("f round-trips through km_reflectance", err < 1e-9,
          f"max |R - f^-1(f(R))| = {err:.2e}")

    check("delta_ks of a pad against itself is exactly zero",
          np.all(delta_ks(f, f) == 0.0))

    # The documented trap: f folds R > 1 back onto positive K/S, and the
    # signed form is the one that does not.
    hi = np.array([0.9, 1.1])
    check("km_remission folds R > 1 to positive (documented trap)",
          km_remission(hi)[1] > 0,
          f"f(0.9)={km_remission(hi)[0]:.5f}  "
          f"f(1.1)={km_remission(hi)[1]:.5f}")
    check("km_remission_signed is monotone across R = 1",
          km_remission_signed(hi)[1] < km_remission_signed(hi)[0],
          f"{km_remission_signed(hi)[0]:+.5f} -> "
          f"{km_remission_signed(hi)[1]:+.5f}")

    # A mask must survive the whole chain untouched.
    m = np.ma.masked_array([[100.0, 400.0], [200.0, 800.0]],
                           mask=[[False, True], [False, False]])
    chain = km_remission(reflectance_infinity(reflectance(m, 400.0), 0.5))
    check("MaskedArray carries its mask through the chain",
          np.array_equal(chain.mask, m.mask) and chain.count() == 3)

    res = doctest.testmod(sys.modules[__name__], verbose=False)
    check(f"docstring examples ({res.attempted} run)", res.failed == 0,
          f"{res.failed} failed")

    print(f"\n{len(fails)} failure(s)" if fails else "\nall checks passed")
    return 1 if fails else 0


if __name__ == "__main__":
    print("padphot self-test")
    raise SystemExit(_selftest())
