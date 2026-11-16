"""graychip_study.py -- is the light on the gray chips one fixed shape that
only gets brighter or dimmer?

A hand-driven study, run from an IDE, like `shading_study.py`.

The question
------------
The chain divides every pixel by the gray-chip level at that pixel
(`padphot.divisor_map`).  One published polynomial per channel gives the
*shape* of the light over the stage, and the two gray chips in the same
frame give its *level*.  That is only right if the shape stays the same:
if a different exposure_us, or a different day, only multiplies the whole
picture by one number.  This module tests that in two ways:

  1. **The scalar test, pixel by pixel.**  Two images of the same gray
     paper, with the paper not moved in between, should obey
     b(p) = s * a(p) at every pixel p, with one number s for the whole
     region.  We measure how far reality is from that, and compare it with
     the camera noise.
  2. **The polynomial test.**  A polynomial fitted to one image of the gray
     card (ZP1001-ZP1005) is used on another image: at a different
     exposure_us, or after the card was moved.  We measure how much worse
     it fits than the image's own polynomial, and how much the two shapes
     differ between the chips, where the pad is.

What decision this could change: whether one polynomial per channel,
fitted at one exposure_us (the e2 rung of ZP1001-ZP1005, 2026-08-07), is
good enough at every exposure_us and on every day.  What ends it: pairs of
images of unmoved paper sit at about the noise level, and polynomial
shapes agree between the chips to well under the 0.4-0.8 % card-placement
floor.  Then the published polynomials stand.

The photon picture
------------------
One pixel p of one image, in counts after the dark level is subtracted:

    d(p) = L * t * I(p) * rho(p) * k

    L       the LED's brightness (it drifts a little, with temperature)
    t       exposure_us
    I(p)    the shape of the light over the stage -- what the polynomial
            models
    rho(p)  the gray paper's own reflectance at p.  The paper is not even:
            this is its texture.  It belongs to the paper, so it moves
            when the paper moves.
    k       counts per photon, the same for every pixel

For two images a and b of the same channel, with the paper not moved:

    b(p) / a(p) = (L_b * t_b) / (L_a * t_a) * I_b(p) / I_a(p)

The texture rho(p) cancels exactly, pixel by pixel.  If the shape I(p) did
not change, the ratio is one number, s, at every pixel.  If the paper
moved, rho no longer cancels, and what is left over jumps up to the size
of the texture.  That jump is how the paper placements are found in the
C10nn series.

The noise
---------
Even with nothing changed, each pixel's reading jumps a little from one
image to the next: photon-counting noise, plus the sensor's read noise.
`noise_model()` measures it on pairs of exposures taken seconds apart in
one gray-card file:

    noise variance of one pixel (counts^2) = READ_VAR + gain * d(p)

READ_VAR is the dark frames' own pixel variance; gain is fitted.  For a
pair, the residual r(p) = b(p) - s * a(p) should then have variance
var_b(p) + s^2 * var_a(p).  The study's main number is

    excess = RMS of r(p) / RMS expected from noise alone

An excess of 1.0 means the scalar model is as good as the camera allows.
2.0 means the residual is twice the noise -- something besides noise
differs between the two images.

Finding the paper placements
----------------------------
The gray paper's texture is only about as large as the noise (2-3 % per
pixel each), so when the paper moves the excess rises only to about 2.
That is too weak to find the moves reliably.  The texture's *pattern* is
a much sharper fingerprint: `texture_match()` correlates the pattern of
one image with another's at every shift up to REACH px.  The same paper
correlates at 0.6-0.8 (noise keeps it below 1); different paper, about 0.

The search also shows that, between sweeps, the image of the chips often
moves by a fraction of a pixel to a few pixels -- usually both chips
together, even between the pre-image and the measurement of one archive.
So `scalar_fit(..., align=True)` moves the paper back by the whole-pixel
part of that shift before comparing.

The two sets of images
----------------------
  * ZP1001-ZP1005: a whole gray card, five times, in three placements
    (A1 untouched pair, A2 re-placed, A180 turned end for end).  Each holds
    three exposure_us steps (e0, e1, e2) of every LED.  The card covers the
    whole analysis area, so a polynomial can be fitted to it.
  * C1002-C1036: strip captures.  Only the two gray chips (low-quality gray
    paper) are gray, so only those two 34 x 34 px squares are used.  The
    paper was moved now and then, which `placements()` finds.  The newer
    archives also hold a pre-image sweep with the same chips.  A polynomial
    cannot be fitted to two small squares, so here the polynomial test is
    `chip_balance()`: does the published polynomial predict the brightness
    of chip 2 relative to chip 1?

Coordinates
-----------
Everything is on one grid: roi 39 (stream rect 992, 588, 366, 260) in
Bayer-layer pixels, 130 rows x 183 columns -- the grid the published
polynomials were fitted on.  `window()` places each capture on it by its
own bound, so the same grid pixel is the same sensor pixel in every image.
C1014 onward were cropped 20 layer rows higher, so their bottom 20 rows of
this grid are empty (NaN); the chips are well clear of that.

Channels: the six in use -- violet.B, royal_blue.B, blue.B, green.Gr,
green.Gb, photo_red.R.

Read-only.  Figures go to `fig/graychip_study_<what>.png` when asked.
"""
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SP = Path(__file__).resolve().parent
sys.path.insert(0, str(SP))

from probe import BAYER, CAPTURES, Capture, sections        # noqa: E402
from whitecorr import load_models, rect_mask                # noqa: E402

# probe.py puts REPO/devdesk on the path.
from flatfield import design_matrix                         # noqa: E402

FIG = SP / "fig"

#: The channels in use, in wavelength order.
CELLS = [("violet", "B"), ("royal_blue", "B"), ("blue", "B"),
         ("green", "Gr"), ("green", "Gb"), ("photo_red", "R")]

#: The common grid: roi 39 in stream pixels (x, y, w, h).
WINDOW = (992, 588, 366, 260)

#: Its size in Bayer-layer pixels, (rows, cols).
SHAPE = (WINDOW[3] // 2, WINDOW[2] // 2)

#: The left and right gray chips on that grid, (x, y, w, h) layer px.
#: roi 36 and roi 35; every C10nn archive's own /meta agrees.
CHIP1 = (10, 26, 34, 34)
CHIP2 = (144, 25, 34, 34)

#: The band between the chips, at the chips' rows: where the pad is, and
#: so where the polynomial's shape matters to a measurement.
BETWEEN = (44, 25, 100, 35)

#: The five gray-card archives, with the card placement each belongs to.
ZP_FILES = (("ZP1001_grayA1a", "A1"), ("ZP1002_grayA1b", "A1"),
            ("ZP1003_grayA2a", "A2"), ("ZP1004_grayA180a", "A180"),
            ("ZP1005_grayA180b", "A180"))

#: Raw counts at or above this are clipped (as `analysis_1`).
SATURATED_DN = 1000

#: A chip whose mean is below this many counts is not a gray chip.
MIN_CHIP_DN = 50.0

#: A chip whose pixel spread (standard deviation / mean) is above this is
#: not an even gray surface: something is on it or it is missing.
MAX_CHIP_SPREAD = 0.10

#: Texture matching searches shifts up to this many layer px each way.
REACH = 6

#: Two images show the same paper placement when the paper's texture
#: correlates at least this well on both chips, at the best shift.  Same
#: paper scores 0.6-0.8 (noise keeps it below 1); moved paper, about 0.
MATCH_MIN = 0.3

_images = {}        # (cell, which) -> [GrayImage]
_textures = {}      # (id(img), rect) -> texture array
_matches = {}       # (id(a), id(b)) -> match dict
_noise = None       # (READ_VAR, gain)
_models = None      # whitecorr models


# ── the common grid ────────────────────────────────────────────────────

def window(c, arr) -> np.ndarray:
    """
    Place one capture's layer array on the common grid.

    Args:
        c: The open `probe.Capture` the array came from, for its bound.
        arr: (rows, cols) array on that capture's own layer grid.

    Returns:
        (130, 183) float array on the roi-39 grid, NaN where the capture
        did not cover it.

    Example:
        >>> sig = window(c, c.layer("violet", "e0", "B")[0])  # doctest: +SKIP
    """
    oy = (c.bound[1] - WINDOW[1]) // 2
    ox = (c.bound[0] - WINDOW[0]) // 2
    out = np.full(SHAPE, np.nan)
    rr = np.arange(SHAPE[0]) - oy
    cc = np.arange(SHAPE[1]) - ox
    okr = (rr >= 0) & (rr < arr.shape[0])
    okc = (cc >= 0) & (cc < arr.shape[1])
    out[np.ix_(okr, okc)] = arr[np.ix_(rr[okr], cc[okc])]
    return out


def chips() -> np.ndarray:
    """Boolean mask of both gray chips on the common grid."""
    return rect_mask(SHAPE, CHIP1) | rect_mask(SHAPE, CHIP2)


# ── the images ─────────────────────────────────────────────────────────

@dataclass
class GrayImage:
    """
    One Bayer layer of one exposure, placed on the common grid.

    Attributes:
        label: Short name for tables, e.g. 'ZP1003 e1' or 'C1032/pre e0'.
        samp: Archive name without .h5.
        sec: '' for the measurement sweep, 'pre' for the pre-image.
        rung: Exposure label, e.g. 'e1'.
        exposure_us: Camera exposure time, microseconds.
        when: The sample's collect_dt, 'YYYY-MM-DD HH:MM'.
        placement: Card placement for ZP images ('A1', 'A2', 'A180'),
            None for C10nn (found by `placements()`).
        sig: (130, 183) counts after the dark level, NaN where not covered.
        ok: (130, 183) True where the pixel can be used: covered, not
            hot, not clipped, above zero.
    """
    label: str
    samp: str
    sec: str
    rung: str
    exposure_us: int
    when: str
    placement: object
    sig: np.ndarray
    ok: np.ndarray


def _load(name, sec, cell, placement=None) -> list:
    """
    Every exposure of one channel in one sweep, as GrayImages.

    Args:
        name: Archive name without .h5.
        sec: '' or 'pre'.
        cell: (LED, Bayer layer).
        placement: Stamped on each image (ZP files only).

    Returns:
        List of GrayImage, in exposure order.  Empty if the LED was not
        lit in this sweep or the layer was not stored.
    """
    led, layer = cell
    c = Capture(name, sec)
    try:
        if led not in c.leds:
            return []
        hot = window(c, c.hot_mask().astype(float)) > 0
        when = str(c.meta.get("sample", {}).get("collect_dt", ""))[:16]
        short = name.split("_")[0]
        out = []
        for rung in c.labels(led):
            got = c.layer(led, rung, layer)
            if got is None:
                continue
            sig, raw = window(c, got[0]), window(c, got[1])
            with np.errstate(invalid="ignore"):
                ok = (np.isfinite(sig) & ~hot & (raw < SATURATED_DN)
                      & (sig > 0))
            tag = f"{short}/{sec}" if sec else short
            out.append(GrayImage(f"{tag} {rung}", name, sec, rung,
                                 c.exposure_us(led, rung), when, placement,
                                 sig, ok))
        return out
    finally:
        c.close()


def c_names() -> list:
    """Every C10nn archive in captures/, by name, without .h5."""
    return sorted(p.stem for p in CAPTURES.glob("C10*.h5"))


def has_chips(img) -> tuple:
    """
    Whether an image shows two usable gray chips, and if not, why.

    A chip must be bright enough to be lit gray paper, even enough not to
    have something lying on it, and unclipped.

    Args:
        img: A GrayImage.

    Returns:
        (True, 'ok') or (False, reason).
    """
    for name, rect in (("chip 1", CHIP1), ("chip 2", CHIP2)):
        m = rect_mask(SHAPE, rect)
        if not img.ok[m].all():
            bad = 1.0 - img.ok[m].mean()
            if bad > 0.02:
                return False, f"{name}: {bad:.0%} of pixels unusable"
        v = img.sig[m & img.ok]
        if v.mean() < MIN_CHIP_DN:
            return False, f"{name}: only {v.mean():.0f} counts"
        if v.std() / v.mean() > MAX_CHIP_SPREAD:
            return False, (f"{name}: uneven, spread "
                           f"{v.std() / v.mean():.1%}")
    return True, "ok"


def images(cell, which) -> list:
    """
    All images of one channel from one set, in capture order, cached.

    Args:
        cell: (LED, Bayer layer).
        which: 'zp' for the gray-card archives, 'c' for C1002-C1036.

    Returns:
        List of GrayImage.  For 'c', only images that pass `has_chips`;
        the ones dropped are printed with the reason.
    """
    key = (cell, which)
    if key in _images:
        return _images[key]
    out = []
    if which == "zp":
        for name, placement in ZP_FILES:
            out += _load(name, "", cell, placement)
    else:
        for name in c_names():
            # The pre-image sweep was taken before the measurement sweep.
            for sec in sorted(sections(name), key=lambda s: s != "pre"):
                for img in _load(name, sec, cell):
                    good, why = has_chips(img)
                    if good:
                        out.append(img)
                    else:
                        print(f"  dropped {img.label} {_cn(cell)}: {why}")
        out.sort(key=_order)
    _images[key] = out
    return out


def _cn(cell) -> str:
    """'violet.B' from ('violet', 'B')."""
    return f"{cell[0]}.{cell[1]}"


# ── step 1: the noise ──────────────────────────────────────────────────

def noise_model(verbose=True) -> tuple:
    """
    Step 1: how much one pixel jumps between two images, from the counts.

    Calibrated on the cleanest repeats there are: two exposure_us steps of
    the same LED in the same gray-card file, taken seconds apart with
    nothing touched.  Every ZP file, LED, Bayer layer and pair of rungs,
    over the whole grid.  For one such pair a (shorter) and b (longer):

        s      = mean(b) / mean(a)
        r(p)   = b(p) - s * a(p)          what is left, pixel by pixel
        var(r) = READ_VAR * (1 + s^2)  +  gain * (d_b + s^2 * d_a)

    with d_a, d_b the two images' mean counts.  The first term is read
    noise, the second photon-counting noise, which grows with the light.
    READ_VAR is the dark frames' own pixel variance (no light at all), so
    only `gain` is fitted, as the straight line through the read-noise
    term that best fits all the pairs:

        x = d_b + s^2 * d_a,   y = var(r) - READ_VAR * (1 + s^2)
        gain = sum(x * y) / sum(x * x)

    For pure photon-counting noise, gain is counts per photo-electron.

    The untouched pairs of files (ZP1001/ZP1002, ZP1004/ZP1005) are then a
    check rather than the calibration: minutes apart instead of seconds,
    they may carry a little more than noise.  `zp_scalar` reports them as
    'same placement, same exposure_us'.

    Args:
        verbose: Print a sample of the pairs and the fit.

    Returns:
        (READ_VAR, gain), cached.  Noise variance of a pixel reading d
        counts is READ_VAR + gain * d.
    """
    global _noise
    if _noise is not None:
        return _noise
    rows, read_vars = [], []
    for name, _ in ZP_FILES:
        c = Capture(name)
        try:
            darks = c.dark_groups()
            shortest = min(darks.values(), key=lambda d: d["exposure_us"])
            read_vars += [sd * sd for _, sd in shortest["layers"].values()]
            hot = window(c, c.hot_mask().astype(float)) > 0
            for led in c.leds:
                for lay in BAYER:
                    got = {r: c.layer(led, r, lay) for r in c.labels(led)}
                    got = {r: (window(c, g[0]), window(c, g[1]))
                           for r, g in got.items() if g is not None}
                    rungs = sorted(got, key=lambda r: int(r[1:]))
                    for i, ra in enumerate(rungs):
                        for rb in rungs[i + 1:]:
                            (sa, wa), (sb, wb) = got[ra], got[rb]
                            ok = (~hot & (wa < SATURATED_DN)
                                  & (wb < SATURATED_DN))
                            da = float(sa[ok].mean())
                            if da < MIN_CHIP_DN:
                                continue
                            db = float(sb[ok].mean())
                            s = db / da
                            v = float(np.var(sb[ok] - s * sa[ok]))
                            rows.append((f"{name[:6]} {led}.{lay} "
                                         f"{ra}/{rb}", s, da, db, v))
        finally:
            c.close()
    read_var = float(np.mean(read_vars))
    s = np.array([r[1] for r in rows])
    x = np.array([r[3] + r[1] ** 2 * r[2] for r in rows])
    y = np.array([r[4] for r in rows]) - read_var * (1 + s * s)
    gain = float(np.sum(x * y) / np.sum(x * x))
    _noise = (read_var, gain)
    if verbose:
        model = read_var * (1 + s * s) + gain * x
        meas = y + read_var * (1 + s * s)
        print(f"\nnoise: {len(rows)} pairs of exposure_us steps within one "
              f"file, ZP1001-ZP1005, every LED and layer reading at least "
              f"{MIN_CHIP_DN:g} counts")
        print(f"  READ_VAR = {read_var:.3f} counts^2 (dark frames, so read "
              f"noise {np.sqrt(read_var):.2f} counts);  gain = {gain:.4f} "
              f"counts per electron")
        print(f"  {'pair':<34}{'d_a':>7}{'d_b':>7}{'var(r)':>9}{'model':>8}"
              f"{'one image, % of d_b':>21}")
        for i in np.argsort(x)[::max(1, len(x) // 12)]:
            one = 100 * np.sqrt(read_var + gain * rows[i][3]) / rows[i][3]
            print(f"  {rows[i][0]:<34}{rows[i][2]:>7.1f}{rows[i][3]:>7.1f}"
                  f"{meas[i]:>9.2f}{model[i]:>8.2f}{one:>21.2f}")
        rel = np.sqrt(meas / model)
        print(f"  measured / model, as a standard deviation: median "
              f"{np.median(rel):.3f}, 5-95 % range "
              f"{np.percentile(rel, 5):.3f}-{np.percentile(rel, 95):.3f}")
    return _noise


def noise_var(d) -> np.ndarray:
    """Noise variance, counts^2, of a pixel reading d counts."""
    read_var, gain = noise_model(verbose=False)
    return read_var + gain * np.clip(d, 0.0, None)


# ── step 2: the paper's texture, and where the paper moved ─────────────

def texture(img, rect) -> np.ndarray:
    """
    The paper's own pattern over one chip, as a fraction of its level.

    A tilted plane is fitted to the chip's counts (the light's gentle slope
    across 34 px) and divided out:

        texture(p) = d(p) / plane(p) - 1

    What is left is the paper's texture plus noise.  Cached.

    Args:
        img: A GrayImage.
        rect: CHIP1 or CHIP2.

    Returns:
        (h, w) array over the chip rect, NaN at unusable pixels.
    """
    key = (id(img), rect)
    if key not in _textures:
        x, y, w, h = rect
        v = img.sig[y:y + h, x:x + w]
        ok = img.ok[y:y + h, x:x + w].ravel()
        yy, xx = np.mgrid[:h, :w]
        A = np.column_stack([np.ones(h * w), xx.ravel(), yy.ravel()])
        coef, *_ = np.linalg.lstsq(A[ok], v.ravel()[ok], rcond=None)
        t = v / (A @ coef).reshape(h, w) - 1.0
        t[~ok.reshape(h, w)] = np.nan
        _textures[key] = t
    return _textures[key]


def _corr(ta, tb, dy, dx) -> float:
    """
    Correlation of a's texture with b's, b looked at (dy, dx) px away.

    a is read over the chip shrunk by REACH on every side; b over the same
    square moved by (dy, dx), which stays inside the chip.
    """
    h, w = ta.shape
    a = ta[REACH:h - REACH, REACH:w - REACH]
    b = tb[REACH + dy:h - REACH + dy, REACH + dx:w - REACH + dx]
    m = np.isfinite(a) & np.isfinite(b)
    return float(np.corrcoef(a[m], b[m])[0, 1])


def _peak(c, i) -> float:
    """
    Sub-pixel position of a peak from three neighbors c[i-1], c[i], c[i+1]:
    the top of the parabola through them, as an offset from i.
    """
    lo, mid, hi = c[i - 1], c[i], c[i + 1]
    den = lo - 2 * mid + hi
    return 0.0 if den >= 0 else 0.5 * (lo - hi) / den


def texture_match(a, b, rect) -> dict:
    """
    Does b show the same paper as a on one chip, and how far has it moved?

    The texture of a is compared with the texture of b at every whole-pixel
    shift up to REACH each way.  Same paper gives a clear peak of about
    0.6-0.8 (noise keeps it below 1); different paper, or paper moved
    more than REACH, gives about 0 everywhere.

    Shift convention: the paper pattern at pixel p in a is at p + shift
    in b.

    Args:
        a, b: GrayImages of the same channel.
        rect: CHIP1 or CHIP2.

    Returns:
        {'r0': correlation with no shift,
         'r': correlation at the best whole-pixel shift,
         'shift': (dy, dx) best whole-pixel shift,
         'sub': (dy, dx) refined to a fraction of a pixel}.
    """
    ta, tb = texture(a, rect), texture(b, rect)
    n = 2 * REACH + 1
    grid = np.full((n, n), np.nan)
    for i, dy in enumerate(range(-REACH, REACH + 1)):
        for j, dx in enumerate(range(-REACH, REACH + 1)):
            grid[i, j] = _corr(ta, tb, dy, dx)
    i, j = np.unravel_index(np.nanargmax(grid), grid.shape)
    fy = _peak(grid[:, j], i) if 0 < i < n - 1 else 0.0
    fx = _peak(grid[i, :], j) if 0 < j < n - 1 else 0.0
    return {"r0": float(grid[REACH, REACH]), "r": float(grid[i, j]),
            "shift": (int(i) - REACH, int(j) - REACH),
            "sub": (i - REACH + fy, j - REACH + fx)}


def match(a, b) -> dict:
    """
    `texture_match` on both chips, and the verdict.  Cached.

    Returns:
        {'chip1': ..., 'chip2': ..., 'same': True when both chips match
        at least MATCH_MIN}.
    """
    key = (id(a), id(b))
    if key not in _matches:
        m1, m2 = texture_match(a, b, CHIP1), texture_match(a, b, CHIP2)
        _matches[key] = {"chip1": m1, "chip2": m2,
                         "same": min(m1["r"], m2["r"]) >= MATCH_MIN}
    return _matches[key]


# ── step 3: the scalar test ────────────────────────────────────────────

def _core(rect) -> tuple:
    """A chip rect shrunk by REACH on every side, as (x, y, w, h)."""
    x, y, w, h = rect
    return (x + REACH, y + REACH, w - 2 * REACH, h - 2 * REACH)


def _pixels(img, rect, shift=(0, 0)) -> tuple:
    """The (values, usable) of img over rect moved by shift = (dy, dx)."""
    x, y, w, h = rect
    dy, dx = shift
    sl = (slice(y + dy, y + dy + h), slice(x + dx, x + dx + w))
    return img.sig[sl], img.ok[sl]


def scalar_fit(a, b, region=None, align=False) -> dict:
    """
    How well b is one number times a, pixel by pixel.

        s       = mean(b) / mean(a), over the pixels both can use
        r(p)    = b(p) - s * a(p)
        expect  = noise_var(b) + s^2 * noise_var(a), at every pixel
        excess  = sqrt( mean(r^2) / mean(expect) )

    With `align`, each chip of b is first moved back by the whole-pixel
    shift `match()` found for it, so the paper's texture lines up again,
    and both chips are read over their core (shrunk by REACH) so the
    moved square stays on the chip.  The fraction of a pixel left over is
    not undone: interpolating would smooth the noise and flatter the test.

    Args:
        a, b: GrayImages of the same channel.
        region: Boolean mask on the common grid; None for both chips.
            Must be None when `align` is set.
        align: Undo the shift of the paper between a and b first.

    Returns:
        Dict with
          s          the scale factor;
          drift_pct  how far s is from the exposure_us ratio, %:
                     100 * (s / (t_b / t_a) - 1).  LED drift plus any
                     departure of the sensor from straight-line response;
          rms_pct    RMS of r as a % of mean(b);
          noise_pct  the RMS noise alone would give, % of mean(b);
          excess     rms_pct / noise_pct;
          chip_pct   100 * (s2 / s1 - 1), where s1 and s2 are the scale
                     factors of chip 1 and chip 2 alone: how much the light
                     changed shape between the chips;
          n          pixels used.

    Raises:
        ValueError: `align` with a `region`.
    """
    if align and region is not None:
        raise ValueError("align works on the two chips only")
    if align:
        m = match(a, b)
        parts = []
        for rect, key in ((CHIP1, "chip1"), (CHIP2, "chip2")):
            va, oa = _pixels(a, _core(rect))
            vb, ob = _pixels(b, _core(rect), m[key]["shift"])
            use = oa & ob
            parts.append((va[use], vb[use]))
    else:
        region = chips() if region is None else region
        parts = []
        for rect in (CHIP1, CHIP2):
            use = region & rect_mask(SHAPE, rect) & a.ok & b.ok
            parts.append((a.sig[use], b.sig[use]))
        rest = region & ~chips() & a.ok & b.ok
        if rest.any():
            parts.append((a.sig[rest], b.sig[rest]))
    va = np.concatenate([p[0] for p in parts])
    vb = np.concatenate([p[1] for p in parts])
    s = float(vb.mean() / va.mean())
    r = vb - s * va
    expect = noise_var(vb) + s * s * noise_var(va)
    mb = float(vb.mean())
    rms = float(np.sqrt(np.mean(r * r)))
    noise = float(np.sqrt(np.mean(expect)))
    s1, s2 = (float(p[1].mean() / p[0].mean()) for p in parts[:2])
    return {"s": s,
            "drift_pct": 100 * (s / (b.exposure_us / a.exposure_us) - 1),
            "rms_pct": 100 * rms / mb, "noise_pct": 100 * noise / mb,
            "excess": rms / noise, "chip_pct": 100 * (s2 / s1 - 1),
            "n": int(va.size)}


def scalar_matrix(imgs, region=None) -> np.ndarray:
    """
    The excess for every pair of images, as a square matrix.

    Args:
        imgs: List of GrayImages of one channel.
        region: As `scalar_fit`.

    Returns:
        (n, n) array; [i, j] is scalar_fit(imgs[i], imgs[j])['excess'],
        and the diagonal is NaN.
    """
    n = len(imgs)
    out = np.full((n, n), np.nan)
    for i in range(n):
        for j in range(i + 1, n):
            out[i, j] = out[j, i] = scalar_fit(imgs[i], imgs[j],
                                               region)["excess"]
    return out


def match_matrix(imgs) -> np.ndarray:
    """
    The texture match for every pair of images, as a square matrix.

    Args:
        imgs: List of GrayImages of one channel.

    Returns:
        (n, n) array; [i, j] is the smaller of the two chips' best
        correlations, `match()`; the diagonal is NaN.
    """
    n = len(imgs)
    out = np.full((n, n), np.nan)
    for i in range(n):
        for j in range(i + 1, n):
            m = match(imgs[i], imgs[j])
            out[i, j] = out[j, i] = min(m["chip1"]["r"], m["chip2"]["r"])
    return out


def _pair_kind(a, b) -> str:
    """How two ZP images are related: same file, same placement, or not."""
    if a.samp == b.samp:
        return "same file, other exposure_us"
    if a.placement == b.placement:
        return ("same placement, same exposure_us" if a.rung == b.rung
                else "same placement, other exposure_us")
    return "card moved"


def zp_scalar(cells=CELLS, region="chips") -> dict:
    """
    Step 3a: the scalar test on the gray card, grouped by how the two
    images are related.

    Args:
        cells: Channels to test.
        region: 'chips' for the two chip squares only (the same pixels as
            the C10nn test), 'grid' for the whole grid.

    Returns:
        {cell: {kind: [scalar_fit dicts]}}.  Prints one table per cell:
        the median and largest excess, the median excess after aligning
        the texture (chips only), and the median residual and noise in %.
    """
    mask = None if region == "chips" else np.ones(SHAPE, bool)
    out = {}
    print(f"\nscalar test, gray card ZP1001-ZP1005, region '{region}'")
    for cell in cells:
        imgs = images(cell, "zp")
        kinds = {}
        for i in range(len(imgs)):
            for j in range(i + 1, len(imgs)):
                f = scalar_fit(imgs[i], imgs[j], mask)
                if region == "chips":
                    f["aligned"] = scalar_fit(imgs[i], imgs[j],
                                              align=True)["excess"]
                kinds.setdefault(_pair_kind(imgs[i], imgs[j]), []).append(f)
        out[cell] = kinds
        print(f"\n  {_cn(cell)}   ({len(imgs)} images)")
        print(f"    {'pair kind':<36}{'pairs':>6}{'excess':>8}{'max':>7}"
              f"{'aligned':>9}{'resid %':>9}{'noise %':>9}{'chip %':>8}")
        for kind in ("same file, other exposure_us",
                     "same placement, same exposure_us",
                     "same placement, other exposure_us", "card moved"):
            fs = kinds.get(kind, [])
            if not fs:
                continue
            ex = [f["excess"] for f in fs]
            al = (f"{np.median([f['aligned'] for f in fs]):>9.2f}"
                  if region == "chips" else f"{'':>9}")
            print(f"    {kind:<36}{len(fs):>6}{np.median(ex):>8.2f}"
                  f"{max(ex):>7.2f}{al}"
                  f"{np.median([f['rms_pct'] for f in fs]):>9.2f}"
                  f"{np.median([f['noise_pct'] for f in fs]):>9.2f}"
                  f"{np.median([abs(f['chip_pct']) for f in fs]):>8.2f}")
    return out


def placements(cell) -> list:
    """
    Split the C10nn images of one channel into paper placements.

    Walks the images in capture order and starts a new placement when an
    image's paper texture does not match the image just before it
    (`match()`, MATCH_MIN on both chips).

    Args:
        cell: (LED, Bayer layer).

    Returns:
        List of placements, each a list of GrayImages.
    """
    imgs = images(cell, "c")
    groups = [[imgs[0]]]
    for prev, img in zip(imgs, imgs[1:]):
        if not match(prev, img)["same"]:
            groups.append([])
        groups[-1].append(img)
    return groups


def c_sequence(cell=("green", "Gr")) -> None:
    """
    Step 3b: every image against the one before it, for one channel.

    The raw material behind `placements()` and `c_scalar()`: the texture
    correlation on each chip with no shift (r0) and at the best shift (r),
    the shift found (layer px, to a fraction), and the scalar-test excess
    as captured and after aligning.  '|' marks a new placement.

    Args:
        cell: (LED, Bayer layer).

    Returns:
        None.  Prints.
    """
    imgs = images(cell, "c")
    print(f"\n{_cn(cell)}: each image against the one before it")
    print(f"    {'image':<15}{'when':<17}{'r0 1':>6}{'r 1':>6}{'r0 2':>6}"
          f"{'r 2':>6}{'shift 1 (dy,dx)':>17}{'shift 2':>13}"
          f"{'excess':>8}{'aligned':>8}")
    for prev, img in zip(imgs, imgs[1:]):
        m = match(prev, img)
        c1, c2 = m["chip1"], m["chip2"]
        f = scalar_fit(prev, img)
        al = scalar_fit(prev, img, align=True)["excess"] if m["same"] \
            else float("nan")
        print(f"  {' ' if m['same'] else '|'} {img.label:<15}{img.when:<17}"
              f"{c1['r0']:>6.2f}{c1['r']:>6.2f}{c2['r0']:>6.2f}{c2['r']:>6.2f}"
              f"{c1['sub'][0]:>+8.2f},{c1['sub'][1]:+.2f}"
              f"{c2['sub'][0]:>+7.2f},{c2['sub'][1]:+.2f}"
              f"{f['excess']:>8.2f}{al:>8.2f}")


def c_scalar(cells=CELLS) -> dict:
    """
    Step 3c: the scalar test on the C10nn chips, placement by placement.

    Within each placement, every pair of images, sorted into
      * same sweep: the exposure_us steps of one sweep, seconds apart;
      * other sweep: different sweeps (the pre-image and measurement of
        one archive, or another archive), minutes to days apart --
        reported as captured and after aligning the texture.

    Args:
        cells: Channels to test.

    Returns:
        {cell: [row dict per placement]}.  Prints a table: the first and
        last image, the count, the median excess of each kind of pair, the
        median and largest shift of the paper (layer px, the larger chip's),
        the median LED drift, and the median and largest change in chip
        balance (chip % from `scalar_fit`, aligned).
    """
    out = {}
    print(f"\nscalar test, C10nn gray chips, by paper placement "
          f"(texture match >= {MATCH_MIN:g} on both chips)")
    for cell in cells:
        groups = placements(cell)
        rows = []
        print(f"\n  {_cn(cell)}   {sum(map(len, groups))} images, "
              f"{len(groups)} placement(s)")
        print(f"    {'#':>2} {'first':<14}{'last':<14}{'imgs':>5}"
              f"{'same sweep':>11}{'other':>7}{'aligned':>8}"
              f"{'shift px':>9}{'max':>6}{'drift %':>8}{'chip %':>7}"
              f"{'max':>6}")
        for k, g in enumerate(groups):
            same, other, aligned, shift, drift, chip = [], [], [], [], [], []
            for i in range(len(g)):
                for j in range(i + 1, len(g)):
                    a, b = g[i], g[j]
                    f = scalar_fit(a, b)
                    if (a.samp, a.sec) == (b.samp, b.sec):
                        same.append(f["excess"])
                        continue
                    m = match(a, b)
                    other.append(f["excess"])
                    fa = scalar_fit(a, b, align=True)
                    aligned.append(fa["excess"])
                    shift.append(max(np.hypot(*m["chip1"]["sub"]),
                                     np.hypot(*m["chip2"]["sub"])))
                    drift.append(abs(fa["drift_pct"]))
                    chip.append(abs(fa["chip_pct"]))
            med = lambda v: float(np.median(v)) if v else float("nan")  # noqa
            row = {"first": g[0].label, "last": g[-1].label, "n": len(g),
                   "same_sweep": med(same), "other_sweep": med(other),
                   "aligned": med(aligned), "shift_med": med(shift),
                   "shift_max": max(shift) if shift else float("nan"),
                   "drift_med": med(drift), "chip_med": med(chip),
                   "chip_max": max(chip) if chip else float("nan")}
            rows.append(row)
            print(f"    {k + 1:>2} {row['first']:<14}{row['last']:<14}"
                  f"{row['n']:>5}{row['same_sweep']:>11.2f}"
                  f"{row['other_sweep']:>7.2f}{row['aligned']:>8.2f}"
                  f"{row['shift_med']:>9.2f}{row['shift_max']:>6.2f}"
                  f"{row['drift_med']:>8.2f}{row['chip_med']:>7.2f}"
                  f"{row['chip_max']:>6.2f}")
        out[cell] = rows
    return out


# ── step 4: the polynomial test, on the gray card ──────────────────────

def models() -> dict:
    """The published flat-field models, `{(led, layer): LayerModel}`."""
    global _models
    if _models is None:
        _models = load_models()
    return _models


def fit_poly(img, deg) -> np.ndarray:
    """
    Fit a polynomial surface to one image over the whole grid.

    The same basis and coordinates as the published fits
    (`flatfield.design_matrix`, x and y scaled to -1..1 over the grid), by
    ordinary least squares over the image's usable pixels.

    Args:
        img: A GrayImage covering the whole grid (a ZP image).
        deg: (deg_y, deg_x).

    Returns:
        (130, 183) fitted surface in counts.
    """
    A = design_matrix(np.arange(SHAPE[0]), np.arange(SHAPE[1]), deg[0],
                      deg[1], SHAPE)
    keep = img.ok.ravel()
    coef, *_ = np.linalg.lstsq(A[keep], img.sig.ravel()[keep], rcond=None)
    return (A @ coef).reshape(SHAPE)


def pegged(g, s1, s2) -> np.ndarray:
    """
    A shape pegged to two chip readings, as `padphot.divisor_map` does
    ('2anchor', 26 of the 27 published cells).

        g1, g2   mean of g over chip 1, chip 2
        t(x)     = (x - x1) / (x2 - x1), x1, x2 the chips' center columns
        div      = g * [ (1 - t) * s1 / g1  +  t * s2 / g2 ]

    Args:
        g: (130, 183) shape (any scale).
        s1, s2: The chips' mean counts.

    Returns:
        (130, 183) gray-chip level at every pixel, counts.
    """
    m1, m2 = rect_mask(SHAPE, CHIP1), rect_mask(SHAPE, CHIP2)
    g1, g2 = float(g[m1].mean()), float(g[m2].mean())
    x1 = CHIP1[0] + (CHIP1[2] - 1) / 2
    x2 = CHIP2[0] + (CHIP2[2] - 1) / 2
    t = (np.arange(SHAPE[1], dtype=np.float64) - x1) / (x2 - x1)
    return g * ((1.0 - t) * s1 / g1 + t * s2 / g2)[None, :]


def shape_change(ga, gb) -> dict:
    """
    How differently two shapes divide a pad, once both are pegged to the
    same two chip readings.

    Pegging removes everything the chips measure for themselves, so what
    is left is only the difference in shape *between and around* the
    chips -- the error a measurement would carry if it used shape `ga`
    when the light really had shape `gb`.

    Args:
        ga, gb: (130, 183) shapes.

    Returns:
        {'between_max', 'between_rms', 'grid_max'}: |pegged(ga) /
        pegged(gb) - 1| in %, the largest and the RMS over BETWEEN, and the
        largest anywhere on the grid.
    """
    d = 100 * (pegged(ga, 1.0, 1.0) / pegged(gb, 1.0, 1.0) - 1)
    band = rect_mask(SHAPE, BETWEEN)
    return {"between_max": float(np.abs(d[band]).max()),
            "between_rms": float(np.sqrt(np.mean(d[band] ** 2))),
            "grid_max": float(np.abs(d).max())}


def transfer(img, g) -> float:
    """
    RMS misfit of one image to a given shape, the best scale allowed, % of
    the image's mean.

    Args:
        img: A GrayImage.
        g: (130, 183) shape.

    Returns:
        RMS of img - k * g over the usable pixels, k fitted by least
        squares, as a % of the image's mean.
    """
    v, gv = img.sig[img.ok], g[img.ok]
    k = float(np.dot(v, gv) / np.dot(gv, gv))
    return float(100 * np.sqrt(np.mean((v - k * gv) ** 2)) / v.mean())


def zp_poly(cells=CELLS) -> dict:
    """
    Step 4: does a polynomial fitted to one gray-card image fit the
    others?

    For each channel, at its published degree, a polynomial is fitted to
    each of the 15 images (5 files x 3 exposure_us).  Then:

      * **across exposure_us** -- in each file, e0 and e1 against e2 (the
        rung that was published);
      * **across placements** -- each file's e2 against ZP1001's e2;
      * **against the published polynomial** -- each image's own fit.

    Two numbers per comparison:
      * shape change between the chips (`shape_change`), in %: the error
        a pad measurement would carry;
      * transfer penalty: how much worse the image is fitted by the other
        polynomial than by its own, as sqrt(T^2 - O^2) in %, with T and O
        the two RMS misfits from `transfer`.  O itself is mostly the
        card's texture plus noise.

    Args:
        cells: Channels to test.

    Returns:
        {cell: list of row dicts}.  Prints a table per channel.
    """
    out = {}
    print("\npolynomial test, gray card ZP1001-ZP1005, at each channel's "
          "published degree")
    for cell in cells:
        deg = _published_degree(cell)
        imgs = images(cell, "zp")
        fits = {i.label: fit_poly(i, deg) for i in imgs}
        own = {i.label: transfer(i, fits[i.label]) for i in imgs}
        by = {i.label: i for i in imgs}
        pairs = []
        for name, _ in ZP_FILES:
            short = name.split("_")[0]
            ref = f"{short} e2"
            for r in ("e0", "e1"):
                if f"{short} {r}" in fits and ref in fits:
                    pairs.append(("exposure_us", f"{short} {r}", ref))
            if short != "ZP1001" and ref in fits:
                pairs.append(("placement", ref, "ZP1001 e2"))
        rows = []
        for kind, lb, la in pairs:
            sc = shape_change(fits[la], fits[lb])
            t = transfer(by[lb], fits[la])
            pen = float(np.sqrt(max(t * t - own[lb] ** 2, 0.0)))
            rows.append({"kind": kind, "image": lb, "poly_from": la,
                         **sc, "own_pct": own[lb], "penalty_pct": pen})
        pub = models()[cell].gain()
        for i in imgs:
            sc = shape_change(pub, fits[i.label])
            t = transfer(i, pub)
            pen = float(np.sqrt(max(t * t - own[i.label] ** 2, 0.0)))
            rows.append({"kind": "published", "image": i.label,
                         "poly_from": "published", **sc,
                         "own_pct": own[i.label], "penalty_pct": pen})
        out[cell] = rows
        noise = np.median([100 * np.sqrt(np.mean(noise_var(i.sig[i.ok])))
                           / i.sig[i.ok].mean() for i in imgs])
        print(f"\n  {_cn(cell)}   degree (y, x) = {deg};  noise alone "
              f"~{noise:.2f} % per pixel")
        print(f"    {'kind':<12}{'image':<11}{'poly from':<11}"
              f"{'between max %':>14}{'rms %':>7}{'grid max %':>11}"
              f"{'own fit %':>10}{'penalty %':>10}")
        for r in rows:
            print(f"    {r['kind']:<12}{r['image']:<11}{r['poly_from']:<11}"
                  f"{r['between_max']:>14.3f}{r['between_rms']:>7.3f}"
                  f"{r['grid_max']:>11.3f}{r['own_pct']:>10.2f}"
                  f"{r['penalty_pct']:>10.3f}")
    return out


def _published_degree(cell) -> tuple:
    """(deg_y, deg_x) of a published cell, read from its coef_json."""
    import json
    import sqlite3
    from whitecorr import DB
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        j = conn.execute(
            "SELECT coef_json FROM layermodels WHERE active = 1 AND "
            "led_color = ? AND bayer_layer = ?", cell).fetchone()[0]
    finally:
        conn.close()
    j = json.loads(j)
    return (int(j["deg_y"]), int(j["deg_x"]))


# ── step 5: the published polynomial on the C10nn chips ────────────────

def chip_balance(cell) -> list:
    """
    Step 5: does the published polynomial predict chip 2 relative to
    chip 1, image by image?

        balance = 100 * ( (m2 / m1) / (g2 / g1) - 1 )   %

    m1, m2 are the chips' measured means and g1, g2 the published shape's
    means over the same squares.  If the two papers reflected equally and
    the shape were right, balance would be 0.  The papers need not match,
    so the level of balance is not the point: within one placement it
    should stay constant (the shape is not changing), and it may step at a
    placement change (the papers moved).

    Args:
        cell: (LED, Bayer layer).

    Returns:
        List of (GrayImage, balance %), in capture order.
    """
    g = models()[cell].gain()
    m1, m2 = rect_mask(SHAPE, CHIP1), rect_mask(SHAPE, CHIP2)
    pred = float(g[m2].mean() / g[m1].mean())
    out = []
    for img in images(cell, "c"):
        meas = (float(img.sig[m2 & img.ok].mean())
                / float(img.sig[m1 & img.ok].mean()))
        out.append((img, 100 * (meas / pred - 1)))
    return out


def c_balance(cells=CELLS) -> dict:
    """
    Step 5, as a table: chip balance per placement.

    Args:
        cells: Channels to show.

    Returns:
        {cell: [(first label, n, mean %, spread %)] per placement}, where
        spread is the standard deviation within the placement.  Prints it.
    """
    out = {}
    print("\nchip balance against the published polynomial, C10nn, by "
          "placement (spread = standard deviation within the placement)")
    for cell in cells:
        bal = {id(i): b for i, b in chip_balance(cell)}
        rows = []
        for g in placements(cell):
            v = np.array([bal[id(i)] for i in g])
            rows.append((g[0].label, len(g), float(v.mean()),
                         float(v.std())))
        out[cell] = rows
        print(f"  {_cn(cell):<14}" + "   ".join(
            f"[{f} x{n}] {m:+.2f}+-{s:.2f}" for f, n, m, s in rows))
    return out


# ── figures ────────────────────────────────────────────────────────────

def _finish(fig, what, figs, savefigs):
    """
    Save and/or show one finished figure, then release it.

    Args:
        fig: The matplotlib figure.
        what: Short name for the file: fig/graychip_study_<what>.png.
        figs: True to show the figure on screen.
        savefigs: True to write it to the file.

    Returns:
        The Path written, or None if `savefigs` is False.
    """
    import matplotlib.pyplot as plt
    out = None
    if savefigs:
        FIG.mkdir(parents=True, exist_ok=True)
        out = FIG / f"graychip_study_{what}.png"
        fig.savefig(out, dpi=110)
        print(f"  wrote {out}")
    if figs:
        plt.show()
    else:
        plt.close(fig)
    return out


def _order(img) -> tuple:
    """Sort key for capture order: day and time, pre-image first, rung."""
    return (img.when, img.sec != "pre", int(img.rung[1:]), img.samp)


def show_matrix(which="c", quantity="match", cells=CELLS, figs=True,
                savefigs=False):
    """
    Every pair of images compared, one panel per channel.

    Images in capture order along both axes; the lines mark the paper
    placements `placements()` found (C10nn) or the card placements (ZP).

    Two quantities:
      * 'match' -- the paper's texture correlation at the best shift, the
        smaller of the two chips (`match_matrix`).  Bright blocks along the
        diagonal are placements: 0.6-0.8 is the same paper, about 0 is not.
      * 'excess' -- the scalar-test excess as captured, no alignment
        (`scalar_matrix`), on a log scale: 1 is the noise level.

    'match' over 100 images costs about a minute per channel.

    Args:
        which: 'c' for the C10nn chips, 'zp' for the gray card (chip
            squares only, the same pixels).
        quantity: 'match' or 'excess'.
        cells: Channels, one panel each.
        figs: Show the figure.
        savefigs: Write fig/graychip_study_<quantity>_<which>.png.

    Returns:
        None.
    """
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm
    n = len(cells)
    ncol = 3
    nrow = (n + ncol - 1) // ncol
    fig, ax = plt.subplots(nrow, ncol, figsize=(5.2 * ncol, 5.0 * nrow),
                           layout="constrained", squeeze=False)
    for k, cell in enumerate(cells):
        a = ax[k // ncol, k % ncol]
        imgs = images(cell, which)
        if quantity == "match":
            im = a.imshow(match_matrix(imgs), cmap="viridis", vmin=0,
                          vmax=0.85, interpolation="nearest")
        else:
            im = a.imshow(scalar_matrix(imgs), cmap="magma",
                          norm=LogNorm(vmin=0.8, vmax=4),
                          interpolation="nearest")
        if which == "c":
            edges = np.cumsum([len(g) for g in placements(cell)])[:-1]
        else:
            pl = [i.placement for i in imgs]
            edges = [i for i in range(1, len(pl)) if pl[i] != pl[i - 1]]
        for e in edges:
            a.axhline(e - 0.5, color="r", lw=0.7)
            a.axvline(e - 0.5, color="r", lw=0.7)
        step = max(1, len(imgs) // 25)
        ticks = list(range(0, len(imgs), step))
        a.set_xticks(ticks, [imgs[i].label for i in ticks], rotation=90,
                     fontsize=6)
        a.set_yticks(ticks, [imgs[i].label for i in ticks], fontsize=6)
        a.set_title(f"{_cn(cell)}, {len(imgs)} images", fontsize=9)
    for k in range(n, nrow * ncol):
        ax[k // ncol, k % ncol].axis("off")
    label = ("texture correlation at best shift (smaller chip)"
             if quantity == "match"
             else "excess = residual RMS / noise RMS  (1 = noise)")
    fig.colorbar(im, ax=ax, shrink=0.6, label=label)
    title = ("C10nn gray chips" if which == "c"
             else "ZP1001-ZP1005 gray card, chip squares")
    what = ("same paper?  texture match" if quantity == "match"
            else "scalar test b = s * a, excess over noise")
    fig.suptitle(f"{title}: {what}, every pair of images", fontsize=11)
    _finish(fig, f"{quantity}_{which}", figs, savefigs)


def show_series(cells=CELLS, figs=True, savefigs=False):
    """
    The C10nn chips over time: brightness drift and chip balance.

    Two panels, stacked, sharing the x axis, which lists every image of
    any channel in capture order (a channel missing from an image leaves
    a gap):
      * drift %: each image against the first image of the series, the
        exposure_us ratio taken out -- the LED's brightness (plus any
        bend in the sensor's response);
      * chip balance % (`chip_balance`): chip 2 / chip 1 against what the
        published polynomial predicts.  Flat within a placement means the
        shape of the light between the chips is steady.
    Gray lines: the paper placements found on green.Gr.

    Args:
        cells: Channels.
        figs: Show the figure.
        savefigs: Write fig/graychip_study_series.png.

    Returns:
        None.
    """
    import matplotlib.pyplot as plt
    allimg = {}
    for cell in cells:
        for i in images(cell, "c"):
            allimg.setdefault(i.label, i)
    order = sorted(allimg.values(), key=_order)
    xof = {i.label: k for k, i in enumerate(order)}
    fig, ax = plt.subplots(2, 1, figsize=(14, 7.5), sharex=True,
                           layout="constrained")
    for cell in cells:
        imgs = images(cell, "c")
        x = [xof[i.label] for i in imgs]
        drift = [scalar_fit(imgs[0], i)["drift_pct"] for i in imgs]
        bal = [b for _, b in chip_balance(cell)]
        ax[0].plot(x, drift, ".-", lw=0.7, ms=3, label=_cn(cell))
        ax[1].plot(x, bal, ".-", lw=0.7, ms=3, label=_cn(cell))
    for g in placements(("green", "Gr"))[1:]:
        for a in ax:
            a.axvline(xof[g[0].label] - 0.5, color="0.5", lw=0.8)
    ax[0].set_ylabel("drift against the first image, %\n"
                     "(brighter is up)")
    ax[1].set_ylabel("chip balance, %\n(chip 2 / chip 1, over the "
                     "published\npolynomial's prediction)")
    ax[0].legend(fontsize=7, ncol=3)
    step = max(1, len(order) // 50)
    ax[1].set_xticks(range(0, len(order), step),
                     [i.label for i in order[::step]], rotation=90,
                     fontsize=6)
    for a in ax:
        a.grid(alpha=0.3)
    fig.suptitle("C10nn gray chips in capture order; gray lines = new "
                 "paper placement", fontsize=10)
    _finish(fig, "series", figs, savefigs)


def show_shapes(cell=("violet", "B"), figs=True, savefigs=False):
    """
    Approval figure: the gray card, its polynomial, and how the polynomial
    changes with exposure_us and placement.

    Top row: ZP1001 e2 as captured (divided by its mean), its own
    polynomial at the published degree, and the published polynomial, on
    one shared color scale.
    Lower rows: pegged shape ratio pegged(other) / pegged(ZP1001 e2) - 1,
    in %, on one shared diverging scale, for e0 and e1 of ZP1001, and e2
    of ZP1003 and ZP1004.  Chip squares in black, the BETWEEN band dashed.

    Args:
        cell: The channel.
        figs: Show the figure.
        savefigs: Write fig/graychip_study_shapes_<led>.<layer>.png.

    Returns:
        None.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    deg = _published_degree(cell)
    by = {i.label: i for i in images(cell, "zp")}
    ref = by["ZP1001 e2"]
    fref = fit_poly(ref, deg)
    pub = models()[cell].gain()
    raw = np.where(ref.ok, ref.sig, np.nan) / np.nanmean(ref.sig[ref.ok])
    top = (("ZP1001 e2, as captured / mean", raw),
           (f"its own polynomial {deg}", fref / fref.mean()),
           ("published polynomial", pub / pub.mean()))
    others = ("ZP1001 e0", "ZP1001 e1", "ZP1003 e2", "ZP1004 e2")
    fig, ax = plt.subplots(3, 3, figsize=(13, 9), layout="constrained")
    lo, hi = np.nanpercentile(raw, [1, 99])

    def outline(a):
        for r in (CHIP1, CHIP2):
            a.add_patch(Rectangle((r[0] - .5, r[1] - .5), r[2], r[3],
                                  fill=False, ec="k", lw=0.8))
        b = BETWEEN
        a.add_patch(Rectangle((b[0] - .5, b[1] - .5), b[2], b[3],
                              fill=False, ec="k", lw=0.6, ls="--"))

    for j, (t, img) in enumerate(top):
        im = ax[0, j].imshow(img, cmap="gray", vmin=lo, vmax=hi)
        ax[0, j].set_title(t, fontsize=9)
        outline(ax[0, j])
    fig.colorbar(im, ax=ax[0, :], shrink=0.8, label="relative brightness")
    maps = []
    for lb in others:
        if lb in by:
            maps.append((f"{lb} vs ZP1001 e2",
                         100 * (pegged(fit_poly(by[lb], deg), 1, 1)
                                / pegged(fref, 1, 1) - 1)))
    maps.append(("published vs ZP1001 e2",
                 100 * (pegged(pub, 1, 1) / pegged(fref, 1, 1) - 1)))
    lim = max(float(np.abs(m).max()) for _, m in maps)
    lim = min(lim, 3.0)
    for k, (t, m) in enumerate(maps):
        a = ax[1 + k // 3, k % 3]
        im2 = a.imshow(m, cmap="RdBu_r", vmin=-lim, vmax=lim)
        band = rect_mask(SHAPE, BETWEEN)
        a.set_title(f"{t}\nbetween chips: max {np.abs(m[band]).max():.2f} %",
                    fontsize=9)
        outline(a)
    for k in range(len(maps), 6):
        ax[1 + k // 3, k % 3].axis("off")
    fig.colorbar(im2, ax=ax[1:, :], shrink=0.8,
                 label="pegged shape difference, %")
    fig.suptitle(f"{_cn(cell)}, gray card: polynomial shape across "
                 f"exposure_us and placement (both pegged to the same chips)",
                 fontsize=11)
    _finish(fig, f"shapes_{_cn(cell)}", figs, savefigs)


# ── the steps ──────────────────────────────────────────────────────────

def run():
    """
    The procedural steps, each behind an `if 1:` / `if 0:` guard.

    Returns:
        None.  Tables are printed and figures go to fig/.
    """
    # 1. How big is the camera noise?
    if 1:
        print("\n=== step 1: noise_model")
        noise_model()

    # 2 and 3a. Gray card: is each image one number times another?
    if 1:
        print("\n=== step 3a: zp_scalar")
        zp_scalar(region="chips")
        zp_scalar(region="grid")

    # 2 and 3b. C10nn chips: image by image, for one channel.
    if 1:
        print("\n=== step 3b: c_sequence")
        c_sequence(("green", "Gr"))

    # 3c. C10nn chips: the scalar test within each paper placement.
    if 1:
        print("\n=== step 3c: c_scalar")
        c_scalar()

    # 4. Gray card: does one image's polynomial fit the others?
    if 1:
        print("\n=== step 4: zp_poly")
        zp_poly()

    # 5. C10nn chips: is the chip-to-chip balance steady?
    if 1:
        print("\n=== step 5: c_balance")
        c_balance()

    # Figures.
    if 1:
        print("\n=== figures")
        show_matrix("zp", "excess", figs=False, savefigs=True)
        show_matrix("c", "excess", figs=False, savefigs=True)
        show_matrix("c", "match", figs=False, savefigs=True)
        show_series(figs=False, savefigs=True)
        for cell in CELLS:
            show_shapes(cell, figs=False, savefigs=True)


if __name__ == "__main__":
    run()
