# padanalysis — reagent pad segmentation and reflectance, desktop only

Exploratory pipeline for the C10xx strip captures.  Findings:

- 2026-08-10, five strips — [`docs/pad_analysis_2026-08-10.html`](../../docs/pad_analysis_2026-08-10.html)
- 2026-08-13, fifteen captures — [`docs/pad_analysis_2026-08-13.html`](../../docs/pad_analysis_2026-08-13.html)

**To run the analysis yourself, read [HOWTO.md](HOWTO.md).**  This file is the
module reference.

Everything here is **read-only** against `captures/*.h5` and
`src/colorimeter.sqlite`.  Nothing writes to the instrument database, and the
`layermodels` rows are read but never superseded.  Desktop only — matplotlib
and scipy dependencies, not synced to the Pi.

Run with the project venv, from anywhere:

    .venv/bin/python3 desktop/padanalysis/report10.py

## The modules

| file | what it does |
|---|---|
| `samples.py` | **the inventory.**  One row per capture: nominal concentration, whether the strip was shielded from splashes, whether it was imaged before or after exposure, which stage and which illuminants |
| `probe.py` | opens an archive, resolves ROIs into bound-crop layer coordinates, hands back dark-subtracted Bayer layers |
| `fields.py` | the composite reflectance — signal over gray-chip level, averaged across every stored illuminant.  Lets the pad be found without the white LED |
| `padrect.py` | **the pad finder.**  Fits a rotated rectangle to the strip's edges and the pad's own shadow line |
| `segment.py` | the earlier free-form finder, kept as the comparison arm for `report7` and `report8`.  Needs the white illuminant |
| `whitecorr.py` | rebuilds each `layermodels` cell's fitted field from `coef_json` and re-anchors the chip weights on the region actually measured |
| `measure.py` | pad reflectance per LED × Bayer layer × exposure rung, screened for saturation and low signal; writes `records.json` |
| `chemistry.py` | reduces those records to per-strip dye quantities: reflectance against the strip's own paper, absorbance, and absorbance less the blank |
| `sweep.py` | re-runs the measurement at several erosion depths and shows the conclusions do not depend on it |
| `duotone.py` | **the two dyes.**  Per-pixel absorbance across all cells, the yellow and brown spectra fitted from the pixels themselves, the unmixing that reports how much of each is at a pixel, and the figures of merit that rank cell subsets |

`samples.py`, `padrect.py`, `fields.py`, `segment.py`, `whitecorr.py`,
`chemistry.py` and `duotone.py` all self-test when run directly.  `whitecorr.py`'s test is the
useful one: it must reproduce the published w1/w2 to 0.0000 % on all 27 usable
cells.

`measure.py` takes an erosion depth and a finder:

    .venv/bin/python3 desktop/padanalysis/measure.py 6 rect   # -> records.json
    .venv/bin/python3 desktop/padanalysis/measure.py 6 blob   # -> records_blob.json

## The reports

| file | question |
|---|---|
| `look1.py`, `look2.py` | first look at the layer data; the free-form segmentation, drawn |
| `report1.py` | exposure consistency, reflectance table, segmented pad vs seeded box |
| `report2.py` | the edge effect as a depth profile |
| `report3.py` | **is the edge effect photometric or chemical?** — the unstained-paper control |
| `report4.py` | erosion-depth sweep and replicate agreement |
| `report5.py` | channel ranking by sensitivity/noise; predicted ppb; within-pad heterogeneity |
| `report6.py` | the rank-1 dye model, and whether X15 is spectrally distinguishable |
| `report7.py` | **the rectangle against the blob** — masks, differences, edge fits |
| `report8.py` | what the geometry constraint changes in the reflectances and the conclusions |
| `report9.py` | **what the instrument contributes** — exposure consistency, the bare paper across fifteen strips, the two unexposed pads |
| `report10.py` | **the dose-response and replicate agreement** — the shape of the curve, and how far apart strips of one concentration land |
| `report11.py` | the two strips imaged before and after exposure: placement repeatability, the pad's own blank, the resolution floor |
| `report12.py` | **structure inside the pad** — trend, granularity, and whether any pad carries a droplet mark |
| `report13.py` | what else explains the scatter — shielding, session and protocol timing, with the confounding stated |
| `look3.py` | **the classification, for eye checking** — each pad in true colour beside the white/yellow/brown call and the brown fraction behind it |
| `report14.py` | **ranking the colour sets that separate brown from yellow** — the two fitted spectra, every 2-, 3- and 4-cell subset scored, and what each extra cell buys |

Reports 1–8 were written for the 2026-08-10 set.  Reports 7 and 8 still run,
restricted to the captures that stored the white illuminant.  Figures land in
`fig/`.

## Why the pad finder is geometric

The strip does not sit against a uniform dark background in every capture, and
the pad is not always coloured.  Neither its sides nor its ends can be found
by thresholding its own colour.

**Sides.**  On the original stage the strip lay in a channel whose floor is a
dull olive material with **B/G ≈ 0.33** — less blue than the white paper
(0.47) and only a little bluer than the pad (0.25).  Any threshold placed
between paper and pad therefore swallowed however much channel floor happened
to be showing beside the strip.  `padrect.py` instead takes the strip's own
edges from the bare paper below the pad, on the composite reflectance, where
the strip stands 56 to 72 times above its surround in every capture in the
inventory.

**Ends.**  The pad's lower edge is a narrow dark line — the shadow where the
laminated pad stands proud of the paper — and it is there whatever colour the
pad is.  The upper edge shows the same feature but weakly, and on a strip
whose dye is graded down the pad it can be absent.  So the bottom is measured
and the top is placed one pad-height above it.  Where the top *can* be
measured independently, on thirteen of the fifteen captures, it agrees with
the placed one to **0.39 px** (r.m.s.).

Three details matter:

**One shared slope for both edges.**  Fitting the two edges independently over
a 30-row band leaves ±2° of tilt uncertainty — enough to swamp the real tilt.
Fitting them as parallel lines is the rigid-strip constraint and gets the
residual to 0.01–0.22 px.

**The fit rejects outlying rows.**  Some strips carry a hand-written mark on
the paper below the pad.  On C1008 it displaced one edge by several pixels
until the fit was made to reject on residual (`REFIT_PASSES`).

**Sharp edges only, for the pooled width.**  A strip against a channel wall,
or in its shadow, has a soft edge whose half-level crossing is displaced
inward.  `edge_softness()` measures the 20 %-to-80 % transition, and only
captures sharp on both sides contribute to `WIDTH_FIXED`.

## Three things worth remembering

**The bare paper reaches the sensor ceiling before the pad does.**  It is
about twice as bright as the gray chips, so on the blue and royal-blue cells
it clips a rung or two before the pad and chips do.  `measure.py` screens the
paper separately and nulls `paper_ratio` on the rungs where it clipped.
Overlooking this produced an apparent 9.5 % session-to-session drift in
`royal_blue.B` that does not exist.

**The pad opening in `segment.py` must be a plain disk opening, not an opening
by reconstruction.**  What it removes is the strip's own dark edge line, which
is as un-blue as the pad and touches it, so reconstruction grows straight back
down it.  (Moot for `padrect.py`, which has no region grow.)

**Two of the 27 `layermodels` cells were published as `1anchor`** —
`photo_red.Gr` and `violet.B`.  Anything recomputing chip weights has to read
`anchor_form` rather than assume the `2anchor` majority form.

## Known limits

`WIDTH_FIXED` and `HEIGHT_FIXED` are pooled from one strip lot.  Re-derive
with `padrect.pooled_size()` for a different lot.

The stage mask can hide the pad's upper end when a strip is pushed too far up
the channel.  `PadRect.top_clearance` is negative when it does, and
`PadRect.mask()` drops the hidden rows; on this inventory an erosion of 4
layer px or more already clears it.
