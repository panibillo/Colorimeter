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

Sections 1-7 are read-only.  Nothing in them writes to `captures/` or to
`src/colorimeter.sqlite`; the only files produced are `records.json`,
`records_blob.json` and the images in `fig/`.  Section 8 is the exception and
says so: `analysis_1.py` writes a `sample_roi` table into
`src/colorimeter.sqlite`, extracted images into `captures/<samp_no>/`, and
interim arrays into `analysis/samples/`.  The archives themselves are never
written by anything here.

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

---

## 8. Driving the analysis yourself — `analysis_1.py`

Everything above is a pipeline: `measure.py` reduces every capture to
`records.json` and the reports read that file and hand back conclusions.
`analysis_1.py` is the other way round.  It gives you documented methods and
leaves the procedure to you — a block of hard-coded steps at the bottom of the
module that you edit for each run.  Nothing is cached between calls except the
open archives, so a step can be commented out, changed and re-run.

It is written to be run from **Eclipse**, so every path resolves from
`Path(__file__).resolve().parent` and no argument parsing is involved.  Run the
file; the `if __name__ == "__main__":` block at the bottom is the script.  From
a shell it is the same file:

    timeout -k 10 600 .venv/bin/python3 -u desktop/padanalysis/analysis_1.py

Linux workstation only — it imports `scipy` and `cv2` and reads
`src/colorimeter.sqlite` directly.

Two modules:

| module | what it is |
|---|---|
| `padphot.py` | the photometry, as pure functions on numpy arrays.  No file or database I/O, so each one can be read and checked on its own.  `python3 padphot.py` runs its self-test |
| `analysis_1.py` | opening archives, finding the pad, applying the flat field, the figures, and the interim store |

### 8.1 The first five minutes on a new capture

The `__main__` block already holds this sequence; it is written out here
because the order matters.

```python
inventory("C1020")                 # 1. what the archive actually holds
locate_pad("C1020")                # 2. fit the pad, both sweeps, write sample_roi
roi_overlay("C1020")               # 3. LOOK at it
contact_sheet("C1020", quantity="R_inf")
cell_table("C1020", roi=ROISAMPLE) # 4. every cell, as a picture and as numbers
cell_table("C1020", roi=ROIWHITE)  # 5. the arithmetic check (see 8.4)
pre_post_overlay("C1020", "royal_blue", "B")   # 6. prove the warp
cell_PCA("C1020")                  # 7. yellow against brown
```

Step 3 is not optional.  If the fitted rectangle is not on the pad and the
paper region not on bare paper, every number after it is void — the same rule
`look3.py` states for the two-dye model.

Every figure function takes `figs=` (show it) and `savefigs=` (write
`fig/analysis1_<samp_no>_<what>.png`).  Under Eclipse, `figs=True` blocks on
`plt.show()`; set `figs=False, savefigs=True` for an unattended run.

`inventory` prints the sections, LEDs, rungs, exposure µs, chip levels and
saturated fractions.  It is the answer to "why is that cell missing", and
`rung_verdict(samp_no, cell)` gives the same answer for one cell as
`(rung, reason)` — e.g. `(None, 'clipped: 30% of the pad and chips at the
1023 ceiling')`.

### 8.2 What `get_cell_data` gives you

    a = get_cell_data(samp_no, LED, Bayerlayer, roi=ROISAMPLE, pre=False,
                      erode=ERODE, rung=None, register=True, crop=False)

A `numpy.ma.MaskedArray` on the **full layer grid** — 130 × 183 for bound
roi 39 — masked outside the ROI.  Full grid rather than a crop because the
pad, the bare paper and both gray chips have to be read from one frame that
lines up, and because `duotone.PadCube.paint` and the registration map are
defined there.  `crop=True` gives the ROI's bounding box instead, for looking
at rather than computing with.

| idiom | what it does |
|---|---|
| `a.mean()`, `a.std()`, `a.sum()` | ignore masked pixels — what you want |
| `a.count()` | number of ROI pixels |
| `a.compressed()` | 1-D vector of just the ROI pixels |
| `~a.mask` | the boolean region itself |
| `a.filled(np.nan)` | plain array, NaN outside, for plotting |
| `a.data` | **the whole frame, mask ignored.**  `np.mean(a.data)` silently averages the crop |

Arithmetic between two masked arrays takes the union of their masks, so a post
frame divided by a pre frame is masked wherever either was.

Two of the five ROIs are not rectangles, which is why this returns a mask:

| ROI | what it is |
|---|---|
| `ROIBOUND` | the whole bound crop.  A rect |
| `ROIGRAY1`, `ROIGRAY2` | the two gray chips.  Rects, from the `roi` table.  Stage furniture — they do not move with the strip |
| `ROISAMPLE` | the fitted pad: a *rotated* rectangle (tilt ≈ −0.7°), eroded by `erode` px.  Its bounding-box corners are paper |
| `ROIWHITE` | the strip's own bare paper below the pad, `PadRect.paper_mask()`, with blemishes removed.  Irregular by construction |

**There is no `white` rect in force.**  The `roi` table's `white` rows sit at
`active=2` and `active=9`, invisible to `roi.load_active()`; the only
`white_balance` rects are the two gray chips.  So the white reference in this
pipeline is two different things — the **gray chips**, weighted per cell
through the flat field, and the **strip's own paper**, geometric.  `ROIWHITE`
means the second, and that is what `WHITECELLS` is read on.

### 8.3 The chain, one function per step

`get_balanced_cell_data` walks it and hands back a `BalancedCell` carrying
every intermediate, so you do not have to recompute anything to change your
mind about which quantity to look at.  Each step is a `padphot` function you
can also call on its own.

| step | function | against what |
|---|---|---|
| dark-corrected counts | `probe.Capture.layer` | `lit − dark_level`: **one number** per Bayer layer per sweep, the mean of the sweep's shortest-exposure dark frame (`capture_h5.dark_level`) — not a frame, see §8.11 |
| `div` | `padphot.divisor_map` | a per-pixel map: this cell's fitted illumination polynomial, placed by sensor position and pegged to the two gray chips of the same frame |
| `R` | `padphot.reflectance` | the gray chips.  `R = 1` means as bright as the chips would be at that pixel |
| `R_inf` | `padphot.reflectance_infinity` | the strip's own bare paper.  `R_inf = 1` on the paper |
| `A` | `padphot.absorbance` | `-log10(R_inf)`.  0 on the paper |
| `KS` | `padphot.km_remission` | `(1 − R_inf)² / 2R_inf` = K/S.  0 on the paper |
| `KS_signed` | `padphot.km_remission_signed` | the same, carrying the sign of (1 − R_inf) |
| `Δ(K/S)` | `padphot.delta_ks` | exposed pad less blank pad.  Only this is approximately proportional to loading |
| `Δ(K/S)_corr` | `padphot.ks_wetting_corrected` | with the neutral darkening of a wet pad removed via a red reference band |

Four things worth knowing before the numbers mean anything, since they are
where the confusion lives:

- **K and S do not come apart.**  Kubelka–Munk gives only the ratio.
  Separating them needs the pad measured over two different backings, which is
  also the test of optical thickness.  So there is `km_remission()` and
  deliberately no `absorption_K()`.
- **`f(R)` is scale-sensitive.**  It is non-linear, so the choice of white
  reference does *not* divide out: referring to the gray chips and referring to
  the strip's paper give different K/S and the difference does not cancel.
  `reflectance_infinity` therefore demands the reference explicitly.
- **`f` is not monotone in `R`.**  It has a minimum of 0 at `R = 1` and rises
  on *both* sides, so a pad reading slightly brighter than its white reference
  gets a positive K/S indistinguishable from one reading slightly darker.  Use
  `KS_signed` wherever K/S is averaged over pixels that straddle `R_inf = 1` —
  which is every blank pad.
- `A = -log10(R)` and `f(R) = K/S` are **different linearising transforms** of
  the same measurement.  `chemistry.Reduction.A/dA` uses the first;
  `tex/gutzeit_analysis.tex` argues for the second.  Both are computed so they
  can be compared on real data.

A cell whose *paper* clipped keeps its `R` and loses only `R_inf`, `A` and
`KS`; `BalancedCell.paper_clipped` says so, and `cell_table` reports it rather
than dropping the row.


**Not used yet: the pre-image.**  No step above subtracts or divides the
pre-image out of the measurement.  `cell_PCA` reads the measurement only;
`pre_post_overlay` shows `post / pre` purely as a registration check; and
`padphot.delta_ks` is defined but nothing calls it.  Using the pre-image is
the next thing to explore.  Two facts to carry into that: it covers only the
eight `PRECELLS`, at a different exposure from the measurement (1000 µs
against ~2193 µs); and the dark level does **not** cancel between pre and
post, because every step divides rather than subtracting raw counts.

### 8.4 The arithmetic check

On the strip's own bare paper the chain is over-determined, and that is the
proof it is wired right:

    cell_table("C1020", roi=ROIWHITE)

`R_inf` must be 1.0000 (it is, exactly, by construction), `A` ≈ 0 and `KS` ≈ 0,
for every cell.  On the gray chips `R` must be 1.0000.  If any of those four
drifts, stop.

The pad spectrum is the other sanity check, and it is chemistry rather than
arithmetic: `R_inf` on the pad should climb monotonically with wavelength
across the absorption edge — on C1020, violet.B 0.66 → royal_blue.B 0.77 →
blue 0.88 → cyan 0.93 → green 0.99 → amber/red slightly above 1.0.

### 8.5 Constants at the top of the module

| constant | what it holds |
|---|---|
| `GRAYCELLS` | the 27 `(led, layer)` cells that have a flat-field model in `layermodels`.  A ladder capture holds all 27; a single-rung group-1 capture holds 20 |
| `PRECELLS` | the 8 cells a `/pre` sweep contributes (royal blue and red only) |
| `DARKCELLS`, `WHITECELLS` | the same 27, read on the dark frames and on the bare paper respectively |
| `ERODE = 6.0` | layer px backed off every pad edge, matching `measure.py 6 rect` |
| `PAD_RUNG = "e1"` | the rung `padrect` builds its composite at; `fields.rung_for` falls back to whatever the capture holds |
| `SATURATED_DN = 1000` | counts at or above this are clipped (ceiling 1023, 23 DN margin) |
| `MIN_SIGNAL_DN`, `MAX_SAT_FRACTION` | the floor and ceiling a rung has to pass to be usable |

Anything outside `GRAYCELLS` has **no white correction at all**.  Two cells are
`anchor_form='1anchor'` — `photo_red.Gr` and `violet.B` — and
`whitecorr.anchor_weights` already branches on it.

### 8.6 `sample_roi`, and where geometry comes from

`locate_pad` fits `PadRect` on each sweep and writes one row per
`(samp_no, section)` into a `sample_roi` table in `src/colorimeter.sqlite`,
created with `CREATE TABLE IF NOT EXISTS`.  It is deliberately **not** in
`src/db_DDL.sql`: that is instrument schema, and adding to it would drag in a
`SCHEMA_VERSION` bump and a release.  This is a desktop analysis artefact.

    sqlite3 src/colorimeter.sqlite "SELECT * FROM sample_roi WHERE samp_no='C1020'"

The row carries the `bound_*` rect the fit is relative to, read from the
archive's own `/meta`, **not** from the `roi` table.  That is not redundancy: a
past capture was cropped against whatever bound was in force when it was taken,
and no amount of syncing the `roi` table can recover that.  `probe.Capture`
already reads `/meta`; so does everything here.

`pushdb.sh` copies the whole local file to the Pi, so `sample_roi` will travel
there.  Harmless — the instrument never reads it — but not a surprise.

### 8.7 Pre against post

Both sweeps get their own `PadRect`, which gives an exact strip frame, so
registration is arithmetic and needs no image matching.  For a post pixel at
strip coordinate `(u, v)`:

```
v_pre = top_pre + (v - top_post)
x_pre = aL_pre + u + m_pre * (v_pre - yc_pre)
```

`get_cell_data(pre=True)` applies one bilinear resample of the pre cell onto
the post grid, so `post / pre` divides pixel by pixel.  `register=False`
returns the unregistered frame.

Registration applies **only** to the strip-attached ROIs (`sample`, `white`).
The gray chips are stage furniture and do not move.

Two flags:

- Interpolation correlates neighbouring pixels, so a per-pixel noise estimate
  taken *after* a resample understates the noise.  Take σ from the
  unregistered frame.
- `pre_post_overlay` prints the corner disagreement after the map.  It should
  be a small fraction of a pixel — 0.27 layer px on C1020 — and a large value
  means one of the two *fits* is wrong, not that the strip moved.  Structure in
  the ratio panel that follows the pad outline is a registration error;
  structure inside the pad is chemistry.

### 8.8 The interim store

    writeh5_sampcell(samp_no, cellname, array, **attrs)
    readh5_sampcell(samp_no, cellname)
    sampcell_attrs(samp_no, cellname="/")
    clear_sampcells(samp_no)

`desktop/padanalysis/analysis/samples/<samp_no>.h5`, dataset at
`/<cellname>/array`, `attrs` on the enclosing group.  A masked array is stored
as `filled(nan)` plus a sibling `/mask` and put back together on read.

The file root carries the source archive's identity — `samp_no`,
`software_ver`, `schema_version` and both sweeps' bound rects — so a sidecar
can never be silently paired with a re-cropped capture.

`clear_sampcells` before a re-run: HDF5 does not hand back the space a deleted
dataset occupied, so rewriting into the same file grows it every time.
`.gitignore` already excludes `**.h5`, so these stay out of the repo.

### 8.9 Syncing a table from the Pi

`desktop/fetch_samplerows_from_pi.py` now updates **any** table, not just
`samples`:

    .venv/bin/python3 desktop/fetch_samplerows_from_pi.py --list-tables
    .venv/bin/python3 desktop/fetch_samplerows_from_pi.py --table roi --dry-run
    .venv/bin/python3 desktop/fetch_samplerows_from_pi.py --table roi

| mode | when |
|---|---|
| `upsert` | match on a natural key (`samp_no COLLATE NOCASE` for `samples`); other local rows untouched.  The old behaviour, now explicit |
| `mirror` | replace the whole local table from the remote, preserving `rowid`, in one transaction.  What `roi`, `exposure`, `layermodels` and `colorcard` need, since they are append-only history keyed by `rowid` and a per-row upsert would leave stale `active=1` rows behind |

Each table has a default mode; `--mode` overrides it.  The local database is
backed up to `<db>.bak-<date>` before any write unless `--no-backup`.

Two cautions.  Mirroring `config` overwrites desktop-local settings, and
`config` carries a partial unique index (`ux_config_one_active`) that a
row-by-row replace can transiently violate — hence delete-then-insert inside
one transaction.  And `sample_roi` is local-only: it has no remote counterpart
and the tool refuses it.

### 8.10 Things that will bite

**Exposure group 1 over-exposes three cells.**  On C1019–C1021, `blue.B`,
`cyan.Gr` and `cyan.Gb` clip on 100 % of the bare paper and 58–89 % of the pad
— three of the 27 lost outright.  C1016–C1018, at exposure group 3, read all
27.  The group changed between C1018 and C1019 and raised every exposure 1.5×
(blue 5283 → 7915 µs, cyan 7402 → 11130 µs).  This is not a code fault, and
`rung_verdict` names it rather than dropping the cell silently.

**Never average Gr and Gb.**  They differ about 1.5 % under red LEDs, and they
sit on different Bayer sub-grids half a layer pixel apart, which matters for
any per-pixel cross-layer work and for the pre/post warp.

**Violet fluoresces the card.**  White card stock carries optical brighteners
(≈22× under violet).  `violet.B` on a white or paper region is not
reflectance.

**One strip alone is rank-1.**  `cell_PCA("C1020")` gives PC1 0.949, PC2 0.032
and a single straight ridge through the origin in the yellow-against-brown
density: on one pad the two products are *proportional*, so the two-dye model
separates nothing within a strip.  Per `tex/gutzeit_analysis.tex` that means
the species ratio is constant, not that there is one species.  Endmembers
fitted on one pad describe that pad's own cloud and are not comparable between
samples — `duotone.fit(cubes)` fits one shared basis over a set, and
`cell_PCA(..., endmembers=...)` takes it.

**`roi.active` is overloaded.**  It holds 0, 1, 2, 3 and 9 in
`src/colorimeter.sqlite` while only 0/1/2 are defined.  Rows at 3 and 9 are
invisible to every loader *and* immune to `roi.save()`, which only supersedes
`active=1`.  Worth cleaning up separately; it is the same "one column, one
meaning" problem noted elsewhere.

**Do not `import pca`.**  `pca.py` runs a multi-minute analysis at import time
and is hardwired to five captures.  Use `duotone`, which does its PCA with
`np.linalg.svd`.

**`scipy` is installed but missing from `requirements-frozen.txt`**, even
though `padrect.py` and `segment.py` already import it.  A fresh environment
built from that file will not run this module.

### 8.11 Dark and gray-chip corrections (changed 2026-09-27)

**Dark: one number per Bayer layer per sweep.**  Measured over every dark
frame in C1002–C1036 (2564 frames, 1–51 ms, 10 Aug – 26 Sep 2026):

- The frame mean is 64.02–64.04 DN in every layer at every exposure.  Within
  one sweep, the shortest exposure's mean is within 0.07 DN of every longer
  one (typically 0.01 DN).  Exposure length does not matter.
- **Shortest reliable exposure: 1000 µs**, the shortest ever captured (the
  pre-image darks).  Its mean is as good as any.  Nothing shorter has been
  captured, so nothing shorter has been tested; the 64 DN level looks like
  the sensor's fixed black level, so a shorter dark would probably do, but
  that needs one capture to confirm.
- Inside one frame, pixels spread 0.73 DN around the mean.  That spread is
  almost all fresh noise: the same pixel's offset in August and in
  September correlates only r = 0.17–0.28.  Subtracting a pixel's own dark
  value therefore adds 0.73 DN of noise and removes almost nothing real,
  so `probe.Capture.layer` now subtracts the sweep's shortest-exposure dark
  mean (`capture_h5.dark_level`).  So do `devdesk/flatfield.py`,
  `flatfield_pool.py` and `hdf5inspect.py` mode 4, whose rows are now
  labelled `processing = 'dark_level'` instead of `'dark_sub'`.
- The exceptions are hot pixels: 8–21 per Bayer layer, sitting 0.5–1.4 DN
  above the frame mean, rising about 0.3 DN per 10 ms of exposure.  They are
  the same pixels in August and September
  (`hotpixel_analysis.persistence_over_time`).  At the signal levels here
  (100–800 DN) they are a 0.2–1 % error on a dozen pixels.
- **Stored and applied.**  The pooled test over C1014–C1036 at k = 6
  (`hotpixel_analysis.save_calibration`) put 19 R, 22 Gr, 16 Gb and 16 B
  pixels in the `hotpixels` table, in whole-sensor layer coordinates,
  `analysisref = 'dark2026-09-27'`.  k = 6 because the main score hump ends
  near z ≈ 5 with no clean gap after it, and a masked pixel costs almost
  nothing (0–4 of ~2300 pad pixels per layer).
- **How they are used: left out, not repaired.**  `probe.Capture.hot_mask()`
  reads the table for the archive's own camera and moves the positions onto
  its crop.  `analysis_1._hot` takes the union over all four Bayer layers —
  about 70 positions — and masks them in the pad, the paper and both gray
  chips; `duotone.PadCube` does the same.  The union is used, not each
  cell's own layer, so every cell keeps the same pixel positions and the
  cells' pixel lists line up one to one.  A registered pre-image also loses
  every pixel that resampling drew from one of *its* hot pixels.  On C1032
  that removes 2 of 1243 eroded pad pixels (6 more for the registered
  pre-image) and 8 of 2245 in the PCA.

**Gray chips: a per-pixel divisor.**  `padphot.divisor_map` takes the
cell's fitted polynomial as the *shape* of the light and pegs its *level*
to the two gray chips in the same frame.  The old `padphot.divisor` gave
one number for the whole region and divided the paper by the pad's number.
On C1032 the light varies 0.4–2 % across the pad and differs 1–4 % between
pad and paper, so R_inf moves by −0.5 % to +3.9 %.

**The fitted polynomial is placed by sensor position.**  The models were
fitted on bound 39 (y = 588); C1014 onward are cropped at y = 548, but the
gray chips sit on the same sensor row in both, so the stage did not move —
only the crop did.  `whitecorr.LayerModel.gain_at` shifts the surface by
the difference (20 layer rows) and leaves NaN outside the fitted rows
rather than extrapolate.  The old `gain()` indexed by crop position and
was 20 rows off for C1014 onward; on region averages that cost under 0.4 %.

**The interim store is stamped** with `analysis_1.CHAIN`.  Results written
before this change have no stamp and are recomputed rather than reused.

