# How to extract images from the .h5 files.

Run `devdesk/hdf5inspect.py` to extract Bayer layers (`-r`), or jpeg images (`-j`)

Examples

```bash
	$ source .venv/bin/activate
(.venv)$ python devdesk/hdf5inspect.py C1014.h5 -r amber 0 --layers R+Gr &

usage: hdf5inspect.py [-h] [--captures-dir DIR] [--pre] [-s] [-j]
                      [-r [COLOR [EXP ...]]] [--gray] [--layers [CH ...]]
                      [-o PNG] [-w] [--db PATH]
                      filename
```


# How to run the pad analysis yourself

Everything below is read-only.  Nothing writes to `captures/` or to
`src/colorimeter.sqlite`.  The only files produced are `records.json`,
`records_blob.json` and the images in `fig/`.

Use the project virtual environment.  The system Python does not have `h5py`,
`cv2`, `scipy` or `matplotlib`.

    cd ~/Projects/Colorimeter
    .venv/bin/python3 desktop/padanalysis/<module>.py

---

## 1. The short version

Three commands reproduce the whole analysis from the archives.

    .venv/bin/python3 desktop/padanalysis/measure.py 6 rect
    for n in 9 10 11 12 13; do
        .venv/bin/python3 desktop/padanalysis/report$n.py
    done

The two-dye analysis reads the archives directly and needs no `records.json`:

    timeout -k 10 900 .venv/bin/python3 -u desktop/padanalysis/look3.py
    timeout -k 10 1800 .venv/bin/python3 -u desktop/padanalysis/report14.py

`report8.py` compares the two pad finders, so it needs the other record file
as well — `measure.py 6 blob` — and both must be rebuilt together or it is
comparing a fresh reduction against a stale one.

The first takes a couple of minutes and writes `records.json`.  Each report
prints its findings and writes a figure into `fig/`.  Reports may be run in
any order and any number of times; they only read `records.json` and, for
report 12, the archives.

---

## 2. Adding a strip

`samples.py` is the single place the inventory lives.  To add a capture, put
the archive in `captures/` as `<name>.h5` and add one row to `_ROWS`:

```python
#   name        ppb  splashed role   pair       stage    leds     rungs   remark
    ("C1014",   40.0,  False, "post", None,     "black", "no_wv", "e0e1e2", ""),
```

| field | meaning |
|---|---|
| `name` | the archive's basename, without `.h5` |
| `ppb` | nominal concentration of the prepared standard; `None` for a strip imaged before exposure |
| `splashed` | `True` if the strip was **not** shielded from stray droplets, `False` if it was, `None` if the question does not apply |
| `role` | `"post"` for a strip imaged after the reaction, `"pre"` for one imaged before |
| `pair` | for a `"pre"` row, the name of the post-exposure capture of the same strip; otherwise `None` |

**You do not write pre-image rows.** From software 0.7.0 a strip's pre-image
lives inside its own archive, under the `/pre` section, and `samples.py` finds
it: every hand-written row is checked and a `"<capture>/pre"` row is inserted
after it if the archive carries one. Adding a strip stays one row, and the
inventory cannot drift from what the archives hold. The two hand-written pre
rows (`C1010-0`, `C1011-pre`) predate sections and are separate files; both
arrangements read the same way from here on.

A pre-image is a short sweep — royal blue and red, one exposure each, two
ladder rungs below e\* — so it measures two cells where a reading measures
fifteen. Anything comparing captures must therefore ask which cells a
particular capture holds rather than assume the full set; `r.common` is the
set every *exposed* strip holds, which is what makes it safe to loop over.
| `stage` | `"olive"` for the original channel, `"black"` for the repainted one |
| `leds` | `"full"` if all ten illuminants were stored, `"no_wv"` if white and violet were off |
| `rungs` | `"e0e1e2"` for the three-rung ladder, `"e0"` for a single rung |

Check it:

    .venv/bin/python3 desktop/padanalysis/samples.py

That prints the inventory, the replicate groups it will form, and the
before/after pairs it has found.  Then re-run `measure.py` and the reports.

`stage`, `leds` and `rungs` are descriptive — the code reads the archive to
find out what is actually there — but they are what the reports use to group
captures, so keep them right.

---

## 3. Checking the pad finder before trusting the numbers

From C1014 the pad sits farther down the backing strip and the bound ROI
moved up, which slid `padrect`'s bottom-edge search window clean off the
pad and left it fitting the stain instead.  `PadRect` now re-seats itself on
the pad's *height* when the observed top edge disagrees with the fitted one
by more than a few px (`bot_source` says which rule placed the rectangle:
`window` for everything before C1014, `height` for C1014 and its pre-image).
The 15 earlier captures fit identically to before, to 0.01 px.

    .venv/bin/python3 desktop/padanalysis/padrect.py

One line per capture:

```
     C1012  L= 71.67 R=113.45 W=41.78 | top=24.71 bot=78.05 | tilt=-0.72 deg
            rms=0.032 px  n=39-0  top_resid= +0.22  clr=+2.71  soft=(1.46,1.29)
```

| field | what to want |
|---|---|
| `W` | the strip's width, near 41.3 px.  A value 1–3 px low means an edge was soft or clipped |
| `tilt` | within about ±1°.  One strip in the set sits at −2°; the fit is not clamped, so that is measured, not assumed |
| `rms` | residual of the two-parallel-lines edge fit.  Under about 0.3 px |
| `n=39-0` | 39 rows used, 0 rejected.  Rejections mean a mark on the paper below the pad; a few are fine |
| `top_resid` | the independently measured pad top less the placed one.  Under about 0.7 px, or `-` if the upper edge was too faint to measure |
| `clr` | clearance between the placed pad top and the stage mask.  **Negative means the mask hides part of the pad** — use an erosion depth larger than the magnitude |
| `soft` | the 20 %-to-80 % transition width of each strip edge.  Around 1.5 px is a sharp edge; 3 px or more means the strip was against a wall or in its shadow |

The last two lines pool the width and the height over the captures that
qualify.  If you have changed strip lot, put the new values into
`WIDTH_FIXED` and `HEIGHT_FIXED` at the top of `padrect.py`.

To see the rectangles drawn on the images, run `report7.py` (white-illuminant
captures only).

---

## 4. Measuring

    .venv/bin/python3 desktop/padanalysis/measure.py <erode> <finder>

`<erode>` is how many layer pixels to back off from every pad edge before
averaging; `6` is the documented default.  `<finder>` is `rect` (the geometric
finder, all captures) or `blob` (the earlier free-form finder, white-illuminant
captures only, written to `records_blob.json`).

One layer pixel is between 0.15 and 0.19 mm on the pad: the pad measures
41.4 × 53.3 layer px, and `PROJECT_OVERVIEW.md` gives the indicator as 8 mm
without saying which dimension. (The measured pad is not square, so that
description is worth checking against the strips.)

The pad is darker at its rim than in its middle, so the measured absorbance
falls as the erosion deepens.  To confirm that nothing you care about depends
on the choice:

    .venv/bin/python3 desktop/padanalysis/sweep.py

It measures at 4, 6, 8, 10 and 12 px in a temporary directory, leaves
`records.json` alone, and tabulates the dose-response exponent, the scatter
and the group means at each depth.

---

## 5. What the reports answer

Run them in this order the first time; each builds on the one before.

### `report9.py` — what the instrument contributes

Three checks that carry no chemistry: does a reflectance depend on the
exposure rung, does the strip's bare paper read the same on every strip, and
how far apart are the two unexposed pads.  **Read this first.**  If the bare
paper does not hold to about 1 % across the inventory, something in the
photometry has moved and nothing downstream can be believed.

### `report10.py` — the dose-response and replicate agreement

The main report.  Prints every strip's reflectance, absorbance and dye
absorbance; fits a power law over the low end of the range; converts the
scatter about it into the concentration each strip would be read back as; and
ranks the illuminant × Bayer-layer cells by what they resolve.

### `report11.py` — the two strips imaged twice

Placement repeatability, what a shared blank costs against each strip's own,
and the smallest concentration the photometry alone could resolve.

### `report12.py` — structure inside the pad

Separates each pad into a smooth trend, a pixel-scale granularity, and the
largest localised feature.  This is the report that tests whether a droplet
landed on a pad: a splash is *local*, and this is where a local mark would
show.  Reads the archives directly, so it takes a minute.

### `report13.py` — what else explains the scatter

Tests the residual scatter against shielding, session and every protocol
interval the archive records, with permutation p values and an explicit
statement of what is confounded with what.

### `report14.py` — ranking the colour sets that separate brown from yellow

The pad carries two products, not one colour at two strengths: yellow at
light arsine loading, brown at heavy.  `duotone.py` fits both per pixel from
all 26 cells; this report ranks the small subsets that could replace them.
Prints the two fitted spectra against the bands `docs/gutzeit_protocol.md`
asks for, the exhaustive ranking at 2, 3 and 4 cells, what each subset's
verdict does to the pixel counts, leave-one-capture-out stability, a violet
fluorescence check, and the brown share against dose.  Reads the archives, so
it takes a couple of minutes.

Three results are structural rather than empirical: one cell scores exactly
zero (shading and dye are the same direction, so nothing distinguishes
them); two cells give the hue but no amount, and so cannot even separate
white from dyed without assuming the illumination; three cells give both,
shading-immune.

### `look3.py` — the classification, for eye checking

Not a report.  Each pad in true colour beside the model's white/yellow/brown
call and the brown fraction it was cut from.  Look at this before believing
any number in `report14.py`: if the middle row disagrees with the top row,
the model is wrong.  Prints pixel counts at three pad erosions, because the
rim is where the two readings of the edge differ most.

---

## 6. Working with the numbers directly

`chemistry.Reduction` is the layer the reports are built on.

```python
import sys; sys.path.insert(0, "desktop/padanalysis")
from chemistry import Reduction, cell_name, exposed
from samples import SAMPLES

r = Reduction()                      # reads records.json
cell = ("royal_blue", "B")

for n in exposed():
    print(f"{n:8} {SAMPLES[n].ppb:5.0f} ppb   "
          f"rho {r.rho[(n,) + cell]:.4f}   "
          f"A {r.A(n, cell):.4f}   dA {r.dA(n, cell):.4f}")
```

| quantity | meaning |
|---|---|
| `r.R_card[(capture, led, layer)]` | pad reflectance against the gray chips, through the fitted illumination surface |
| `r.R_paper[...]` | the same for the bare paper below the pad |
| `r.rho[...]` | `R_card / R_paper` — the pad against its own substrate.  Free of the gray card and of anything that changed between sessions |
| `r.A(capture, cell)` | `-log10(rho)` |
| `r.dA(capture, cell)` | `A` less the absorbance an unexposed pad shows for the same reason |
| `r.common` | the cells present on every capture — the set that is safe to compare across the inventory |
| `r.rho_blank[cell]` | the unexposed pad, averaged over every pre-image that holds the cell |
| `r.blank_of[capture]` | that strip's own pre-image, if it has one |
| `r.blank_source(capture, cell)` | `"own"` or `"pooled"` — which blank `dA` just used |
| `r.table(quantity="dA")` | a capture × cell matrix |

`r.pads[name]` holds the fitted geometry for each capture, including
`top_clearance`, `n_rejected` and `edge_rms`.

**Which blank `dA` removes.** `Reduction(path)` defaults to `blank="own"`: a
strip photographed before it met the sample is corrected by *its own* pad, and
only a strip without a pre-image falls back to the pooled average. That is the
reduction the pre-image exists to make possible, and it is not the one the
2026-08 write-ups used — they predate per-strip blanks.
`Reduction(path, blank="pooled")` restores the *method* they used, one blank
for every strip, but not their exact numbers: the pool is every pre-image in
the inventory, and it has grown since. Two things therefore move a published
`dA`, and they are separable — switching to a strip's own blank (0.003–0.009 A
on the three strips that have one) and each new pre-image joining the pool
(0.004 A at `royal_blue.B` when C1014 joined). If a figure has to be
reproduced exactly, pin the inventory as well as the mode.

---

## 7. If something goes wrong

**`RuntimeError: only N usable strip rows below the pad`** — the finder could
not see enough bare paper.  The strip is too low in the channel, or the
capture is cropped differently.  Check the bound ROI.

**`KeyError: no white illuminant was stored`** — you asked for the `blob`
finder or a report that needs it on a capture that has no white LED.  Use the
`rect` finder.

**`top_resid` shows `-` on many captures** — the pads are too faint for their
upper edge to be seen.  That is expected on unexposed and very low
concentration strips, and the placed top is still right; but if it happens on
strongly coloured strips as well, `HEIGHT_FIXED` is probably wrong for the
lot.

**A capture reads far off the dose-response** — before treating it as
chemistry, check its line from `padrect.py`.  A soft edge, a negative
clearance or a large `rms` all put the region in the wrong place.

**Long runs** — nothing here should take more than a few minutes.  Cap it
anyway, as the project guidelines require:

    timeout -k 10 600 .venv/bin/python3 -u desktop/padanalysis/measure.py 6 rect
