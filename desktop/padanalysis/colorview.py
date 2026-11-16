"""colorview.py -- a color picture of one LED's photo, roughly as an eye sees it.

A helper for judging other figures, not a measurement.  It takes one LED
photo (one LED, one exposure) and stacks its Bayer layers into a red /
green / blue picture:

    red   = the R layer
    green = the average of the Gr and Gb layers
    blue  = the B layer

Each layer is the dark-corrected signal, `sig` (an array, counts).  All
three are divided by the **same** scalar, so the LED's own color is kept: a
royal-blue photo looks blue, as the strip does to the eye under that light.
Then a display gamma is applied (value ** (1 / 2.2)), because a screen
expects gamma-encoded values and raw camera counts are linear in light.

What this picture is not:
- Not color-exact.  The camera's red, green and blue filters are not the
  eye's, and no color-correction matrix is applied.
- A layer the capture did not keep (for example B under the green LED, or
  Gr/Gb under the red LEDs) is shown as zero, and the figure title says so.
  Those layers were dropped because they see almost nothing under that LED.

Every image is on the bound-crop Bayer-layer grid (130 x 183 for bound 39),
the same grid as `analysis_1` and `shading_review`, so it lines up pixel for
pixel with their maps.
"""
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

SP = Path(__file__).resolve().parent
sys.path.insert(0, str(SP))

import analysis_1 as a1                                     # noqa: E402

FIG = SP / "fig"

#: Display gamma: the picture shows value ** (1 / GAMMA).
GAMMA = 2.2

#: Percentile of the brightest layer, over the whole crop, that is shown
#: as full brightness.  Not 100, so a few bright specks do not darken the
#: rest of the picture.
WHITE_PERCENTILE = 99.5

#: Raw counts at or above this are clipped (the same ceiling analysis_1
#: uses).
SATURATED_DN = a1.SATURATED_DN


def rung_for(cap, led, exposure_us=None, rung=None) -> str:
    """
    The exposure rung to use: named directly, or nearest to an exposure time.

    Args:
        cap: The open `probe.Capture`.
        led: scalar str, LED name, e.g. "royal_blue".
        exposure_us: scalar, exposure time in µs; the rung nearest to it is
            used.  Ignored if `rung` is given.
        rung: scalar str, e.g. "e0".  If both are None, the shortest rung.

    Returns:
        scalar str, the rung label.

    Raises:
        KeyError: The photo has no such LED or rung.
    """
    labels = cap.labels(led)
    if not labels:
        raise KeyError(f"{cap.label()}: LED {led!r} not in this photo")
    if rung is not None:
        if rung not in labels:
            raise KeyError(f"{cap.label()} {led}: no rung {rung!r} "
                           f"(has {labels})")
        return rung
    if exposure_us is None:
        return labels[0]
    return min(labels, key=lambda r: abs(cap.exposure_us(led, r)
                                         - exposure_us))


def color_image(samp_no, led, exposure_us=None, rung=None, sec="",
                balance=None, hide_hot=True) -> dict:
    """
    Build the color picture of one LED photo.

    Args:
        samp_no: scalar str, sample number, e.g. "C1032".
        led: scalar str, LED name.
        exposure_us: scalar, exposure time in µs (nearest rung is used).
        rung: scalar str, rung label; overrides `exposure_us`.
        sec: scalar str, "" for the measurement, a1.PRE for the pre-image.
        balance: None for the LED's own color (default for colored LEDs);
            "gray" to divide each layer by its gray-chip average first, so
            the chips come out neutral gray.  Default: "gray" for the white
            LED, None otherwise.
        hide_hot: True to paint each known hot pixel with the median of
            its 3 x 3 neighbors, so it does not show as a bright dot.  For
            looking only.

    Returns:
        Dict:
            'rgb'     array (rows, cols, 3), 0..1, gamma-encoded, for
                      display;
            'linear'  array (rows, cols, 3), the same before gamma, still
                      divided by 'scale';
            'clipped' array bool (rows, cols): any layer at the raw ceiling;
            'scale'   scalar, the value shown as full brightness, in the
                      same units as the layers (counts, or chip units if
                      balanced);
            'rung'    scalar str;  'exposure_us'  scalar int;
            'missing' list of the layers not kept ('R', 'G' or 'B');
            'balance' what was used.
    """
    c = a1.open_sample(samp_no, sec)
    rung = rung_for(c, led, exposure_us, rung)
    if balance is None and led == "white":
        balance = "gray"
    hot = a1._hot(samp_no, sec)
    chip = a1._region(samp_no, a1.ROIGRAY1, sec) & ~hot

    planes, clipped, shape = {}, None, None
    for layer in ("R", "Gr", "Gb", "B"):
        got = c.layer(led, rung, layer)
        if got is None:
            continue
        sig, raw = got
        sig = sig.copy()
        shape = sig.shape
        if hide_hot and hot.any():
            sig[hot] = ndimage.median_filter(sig, size=3)[hot]
        if balance == "gray":
            sig = sig / max(float(np.mean(sig[chip])), 1e-6)
        planes[layer] = sig
        over = raw >= SATURATED_DN
        clipped = over if clipped is None else (clipped | over)
    if not planes:
        raise KeyError(f"{samp_no} {sec or 'post'} {led} {rung}: no layer "
                       f"kept")

    zero = np.zeros(shape)
    greens = [planes[g] for g in ("Gr", "Gb") if g in planes]
    stack = {"R": planes.get("R", zero),
             "G": np.mean(greens, axis=0) if greens else zero,
             "B": planes.get("B", zero)}
    missing = [k for k, have in (("R", "R" in planes), ("G", bool(greens)),
                                 ("B", "B" in planes)) if not have]
    lin = np.stack([stack["R"], stack["G"], stack["B"]], axis=-1)
    scale = float(np.percentile(lin.max(axis=-1), WHITE_PERCENTILE))
    lin = np.clip(lin / max(scale, 1e-9), 0.0, None)
    rgb = np.clip(lin, 0.0, 1.0) ** (1.0 / GAMMA)
    return dict(rgb=rgb, linear=lin, clipped=clipped, scale=scale,
                rung=rung, exposure_us=c.exposure_us(led, rung),
                missing=missing, balance=balance or "none")


def stretch(rgb_linear, region, low=1.0, high=99.0) -> np.ndarray:
    """
    The picture with its contrast stretched over one region.

    Each pixel's three values are scaled by one common factor, so its
    color stays the same; only brightness is stretched.  The region's
    `low` percentile of brightness becomes black and its `high` percentile
    becomes full brightness.  This makes a 2 % change in light visible,
    which the eye would barely notice in the plain picture.

    Args:
        rgb_linear: array (rows, cols, 3), 'linear' from `color_image`.
        region: array bool (rows, cols).
        low, high: scalar percentiles of brightness over the region.

    Returns:
        array (rows, cols, 3), 0..1, gamma-encoded.
    """
    lum = rgb_linear.max(axis=-1)                    # array (rows, cols)
    lo, hi = np.percentile(lum[region], [low, high])
    with np.errstate(invalid="ignore", divide="ignore"):
        k = np.clip((lum - lo) / max(hi - lo, 1e-9), 0, 1) / lum
    out = np.clip(rgb_linear * np.nan_to_num(k)[..., None], 0, 1)
    return out ** (1.0 / GAMMA)


def _box(region, margin):
    """Row and column slices around a bool region, `margin` px wider."""
    ys, xs = np.nonzero(region)
    return (slice(max(ys.min() - margin, 0), ys.max() + margin + 1),
            slice(max(xs.min() - margin, 0), xs.max() + margin + 1))


def show_color(samp_no, led, exposure_us=None, rung=None, sec="",
               balance=None, outline=True, label="aux", figs=True,
               savefigs=False) -> dict:
    """
    The auxiliary figure: one LED photo as a color picture.

    Three panels on the same grid as `shading_review.show_gray_maps`:
    (1) the whole crop; (2) the pad, with the same zoom as that figure's
    bottom row; (3) the pad again, with its brightness stretched
    (`stretch`) so the small changes those maps show become visible.
    Clipped pixels are painted magenta.

    Args:
        samp_no: scalar str.
        led: scalar str.
        exposure_us: scalar µs, or None.
        rung: scalar str, or None.
        sec: scalar str, "" or a1.PRE.
        balance: see `color_image`.
        outline: True to draw the pad (green) and gray chips (orange).
        label: scalar str, leads the file name and names the plot window
            ("Figure aux" instead of "Figure 1").
        figs, savefigs: show / save (fig/<label>_colorview_<...>.png).

    Returns:
        The `color_image` dict.
    """
    import matplotlib.pyplot as plt
    im = color_image(samp_no, led, exposure_us, rung, sec, balance)
    # The outline is the fitted pad itself; hot pixels are not cut out of
    # it, or each would draw as a small diamond.
    pad = a1.pad_of(samp_no, sec).mask(0)
    chips = [a1._region(samp_no, r, sec) for r in (a1.ROIGRAY1, a1.ROIGRAY2)]
    ry, cx = _box(pad, 3)

    plain = im["rgb"].copy()
    plain[im["clipped"]] = (1, 0, 1)
    stretched = stretch(im["linear"], pad)
    stretched[im["clipped"]] = (1, 0, 1)

    fig, axes = plt.subplots(1, 3, figsize=(16, 6),
                             gridspec_kw=dict(width_ratios=[1.6, 1, 1]))
    panels = [(plain, (slice(None), slice(None)), "whole crop"),
              (plain, (ry, cx), "pad"),
              (stretched, (ry, cx), "pad, brightness stretched\n"
                                    "(1st-99th percentile of the pad)")]
    
    custom_dash = (0, (1, 8))
    for ax, (pic, (sy, sx), title) in zip(axes, panels):
        ax.imshow(pic[sy, sx], interpolation="nearest")
        if outline:
            ax.contour(pad[sy, sx].astype(float), levels=[0.5],
                       colors="lime", linewidths=0.8, linestyles=[custom_dash] )
            for ch in chips:
                if ch[sy, sx].any():
                    ax.contour(ch[sy, sx].astype(float), levels=[0.5],
                               colors="orange", linewidths=0.8, linestyles=[custom_dash] )
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("column, layer px")
    axes[0].set_ylabel("row, layer px")
    note = (f"; layers not kept, shown as 0: {', '.join(im['missing'])}"
            if im["missing"] else "")
    fig.suptitle(f"{samp_no} {'pre' if sec else 'post'}-image, {led} LED, "
                 f"rung {im['rung']} ({im['exposure_us']} µs): color "
                 f"picture, gamma {GAMMA}, balance {im['balance']}{note}.  "
                 f"Magenta = clipped.", fontsize=11)
    fig.tight_layout()
    manager = getattr(fig.canvas, "manager", None)
    if manager is not None:
        manager.set_window_title(f"Figure {label}")
    if savefigs:
        FIG.mkdir(parents=True, exist_ok=True)
        out = FIG / (f"{label}_colorview_{samp_no}_{'pre' if sec else 'post'}_"
                     f"{led}_{im['rung']}.png")
        fig.savefig(out, dpi=110)
        print(f"  wrote {out}")
    if figs:
        plt.show()
    else:
        plt.close(fig)
    return im


if __name__ == "__main__":
    # The reference for shading_review step R1: the same photo it maps.
    # show_color("C1032", "white", rung="e0", sec=a1.PRE, figs=False, savefigs=True)
    # show_color("C1032", "violet", rung="e0", sec=a1.PRE, figs=False, savefigs=True)
    # show_color("C1032", "photo_red", rung="e0", sec=a1.PRE, figs=False, savefigs=True)
    show_color("C1032", "cyan", rung="e0", sec=a1.PRE, figs=False, savefigs=True)
    # show_color("C1032", "royal_blue", rung="e0", sec=a1.PRE, figs=False, savefigs=True)
    a1.close_all()
