"""measure.py -- pad reflectance per LED x Bayer layer, white-corrected.

For every capture x LED x layer x exposure rung:

    signal   = lit - dark_level, DN (one number per Bayer layer; see probe)
    region   = the pad rectangle eroded by `erode` layer px (padrect.PadRect)
    divisor  = w1 * mean(chip1 signal) + w2 * mean(chip2 signal), with w1/w2
               recomputed for the region actually measured (whitecorr)
    ratio    = mean(region signal) / divisor        -- reflectance vs gray card
    A        = -log10(ratio)                        -- absorbance

Rungs are screened for saturation and for too little signal, and the surviving
rungs are a consistency check on each other: ratio must not depend on exposure.

Three reference quantities travel with every measurement:

    seed_ratio   what the un-moved seeded sample box would have said
    paper_ratio  the same reflectance computed on the bare strip below the
                 pad.  Same paper, same strip, same illumination, no
                 chemistry -- so `ratio / paper_ratio` is a reflectance
                 referred to the strip's own substrate rather than to the
                 gray card, and it cancels anything that changed between the
                 gray card of one session and the next.

The bare paper is roughly twice as bright as the gray chips, so on the short
wavelength illuminants it reaches the sensor ceiling one or two rungs before
the pad and the chips do.  It carries its own saturation screen for that
reason, and `paper_ratio` is null on the rungs where it clipped -- the pad
measurement on those rungs is still sound and is kept.

Writes nothing outside this directory.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import Capture, open_capture  # noqa: E402
from samples import NAMES, SAMPLES, WHITE_LED_SET  # noqa: E402
from segment import Pad  # noqa: E402
from padrect import PadRect  # noqa: E402
from whitecorr import load_models, rect_mask, anchor_weights  # noqa: E402

SP = Path(__file__).resolve().parent
SATURATED_DN = 1000        # 1023 ceiling less the 23 DN margin flatfield uses
MIN_SIGNAL_DN = 25.0       # below this the layer is noise, not a measurement
MAX_SAT_FRACTION = 0.001


def measure_capture(c: Capture, models: dict, erode: float = 4.0,
                    depth_bins=None, finder: str = "rect") -> tuple:
    """
    Return one record per (LED, layer, rung) that survives screening.

    Args:
        c: The open capture.
        models: {(led, layer): LayerModel} from whitecorr.load_models().
        erode: Layer px to back off from the pad edge before averaging.
        depth_bins: Optional edges for the signal-vs-depth-from-edge profile.
        finder: "rect" for the geometry-constrained padrect.PadRect,
            "blob" for the original free-form segment.Pad.  "blob" needs the
            white LED and so is available only on captures that stored it.

    Returns:
        (records, pad) -- the list of dicts and the fitted region object.
    """
    pad = PadRect(c) if finder == "rect" else Pad(c)
    shape = pad.comp.shape if finder == "rect" else pad.tot.shape
    chip1 = rect_mask(shape, c.rects["gray1"])
    chip2 = rect_mask(shape, c.rects["gray2"])
    region = pad.mask(erode)
    seed = rect_mask(shape, c.rects["sample"])
    if hasattr(pad, "paper_mask"):
        paper, paper_depth = pad.paper_mask()
        paper = paper & (paper_depth > erode)
    else:
        # The free-form finder has no strip frame to cut a paper region from,
        # so the comparison arm carries no paper reference.
        paper = np.zeros(shape, dtype=bool)

    samp = SAMPLES[c.label()]
    out = []
    for (led, layer), m in sorted(models.items()):
        if not m.usable or led not in c.leds:
            continue
        w1, w2, t, gs, g1, g2 = anchor_weights(m, region, chip1, chip2)
        w1s, w2s, *_ = anchor_weights(m, seed, chip1, chip2)
        w1p, w2p = (anchor_weights(m, paper, chip1, chip2)[:2]
                    if paper.any() else (0.0, 0.0))
        for rung in c.labels(led):
            got = c.layer(led, rung, layer)
            if got is None:
                continue
            sig, raw = got
            sat = float((raw[region | chip1 | chip2] >= SATURATED_DN).mean())
            sat_paper = (float((raw[paper] >= SATURATED_DN).mean())
                         if paper.any() else 1.0)
            s = sig[region]
            m1, m2 = float(sig[chip1].mean()), float(sig[chip2].mean())
            div = w1 * m1 + w2 * m2
            if (div <= 0 or s.mean() < MIN_SIGNAL_DN
                    or sat > MAX_SAT_FRACTION):
                continue
            rec = dict(
                capture=c.label(), ppb=samp.ppb, splashed=samp.splashed,
                role=samp.role, stage=samp.stage, pair=samp.pair,
                led=led, layer=layer, rung=rung,
                exposure_us=c.exposure_us(led, rung),
                n=int(region.sum()), sat=sat,
                mean=float(s.mean()), std=float(s.std(ddof=1)),
                p10=float(np.percentile(s, 10)),
                p50=float(np.percentile(s, 50)),
                p90=float(np.percentile(s, 90)),
                skew=float(((s - s.mean()) ** 3).mean() / s.std() ** 3),
                chip1=m1, chip2=m2, w1=w1, w2=w2, t=t,
                divisor=div, ratio=float(s.mean()) / div,
                seed_ratio=float(sig[seed].mean()) / (w1s * m1 + w2s * m2),
                paper_ratio=(None if sat_paper > MAX_SAT_FRACTION else
                             float(sig[paper].mean()) / (w1p * m1 + w2p * m2)),
                sat_paper=sat_paper, n_paper=int(paper.sum()),
            )
            if depth_bins is not None:
                prof = []
                for lo, hi in zip(depth_bins[:-1], depth_bins[1:]):
                    band = (pad.depth > lo) & (pad.depth <= hi)
                    prof.append(float(sig[band].mean()) / div
                                if band.sum() > 20 else np.nan)
                rec["depth_profile"] = prof
            out.append(rec)
    return out, pad


def main(erode: float = 4.0, finder: str = "rect", out: str = "records.json",
         names=None):
    """Measure every capture in the inventory and write the record file."""
    models = load_models()
    bins = [0, 1, 2, 3, 4, 6, 8, 10, 13, 16, 24]
    records, pads = [], {}
    if names is None:
        # The free-form finder reads B/G under white illumination, so it can
        # only run on the captures that stored a white illuminant.
        names = NAMES if finder == "rect" else list(WHITE_LED_SET)
    for name in names:
        # A name may be a plain capture or a sweep inside one ("C1014/pre").
        # Everything past this line treats the two alike; only opening them
        # differs.  See probe.sections().
        c = open_capture(name)
        recs, pad = measure_capture(c, models, erode=erode, depth_bins=bins,
                                    finder=finder)
        records.extend(recs)
        geo = dict(area=pad.area, bbox=pad.bbox, centroid=pad.centroid,
                   n_used=int(pad.mask(erode).sum()))
        if finder == "rect":
            geo.update(width=pad.width, height=pad.height, top=pad.top,
                       bot=pad.bot, angle_deg=pad.angle_deg,
                       edge_rms=pad.edge_rms, n_rows=len(pad.rows_used),
                       n_rejected=pad.n_rejected,
                       top_clearance=pad.top_clearance,
                       top_residual=(None if np.isnan(pad.top_residual)
                                     else pad.top_residual),
                       n_hidden=pad.n_hidden, n_cells=len(pad.cells))
        pads[name] = geo
        print(f"{name:>10}: {len(recs):3d} cell/rung records, "
              f"{geo['n_used']:4d} px measured")
        c.close()
    (SP / out).write_text(json.dumps(
        dict(erode=erode, finder=finder, depth_bins=bins, pads=pads,
             records=records), indent=1))
    print(f"\nwrote {out}  ({len(records)} records)")


if __name__ == "__main__":
    args = sys.argv[1:]
    e = float(args[0]) if args else 4.0
    f = args[1] if len(args) > 1 else "rect"
    main(erode=e, finder=f, out="records.json" if f == "rect"
         else "records_blob.json")
