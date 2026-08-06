# Illumination flat-field calibration

## Purpose

Measure each LED's illumination pattern across the imaging area and fit a
polynomial to it, so a sample ROI and a reference ROI at different places in
the frame can be compared without the illumination gradient biasing the result.

This is a **per-instrument** procedure. The lightboard, lens and stage geometry
differ unit to unit, so nothing below transfers between units except the method.
Run the whole procedure on each colorimeter and record its own answer.

Everything runs on a **desktop**, from capture archives copied off the Pi. The
only Pi-side step is taking the captures.

---

## Prerequisites

- Five capture suites of a uniform card (below), synced into `captures/`.
- The `roi` table's `active=2` rows defining the calibration patch set.
- `.venv` on the desktop (`h5py`, `numpy`, `matplotlib`).

Verify the tooling before starting:

```bash
# desktop, from the repo root
.venv/bin/python3 devdesk/flatfield.py --selftest
```

---

## 1 — Define the calibration patch set (`roi` table, `active=2`)

The flat-field analysis reads its geometry from the `roi` rows with
`active = 2`, kept separate from the in-force `active = 1` rows because they
describe a card on the stage, not the instrument's own targets.

Required rows, all in **whole-image (stream) coordinates**, all even:

| role | count | placement |
|---|---|---|
| `bound` | 1 | the crop the archive stores — make it **larger than the measurement bound**, so different sub-bounds can be tried later without re-imaging |
| `sample` | 1 | at the centre, where the sample will sit |
| `white` | 4 | one per quadrant of the bound |

> ⚠ **The most likely mistake is a coordinate-space error.** The table is in
> whole-image space (2304 × 1296). The stored Bayer layers are *half* that in
> each axis, so a patch's origin **and** its width and height halve. The bound
> halves with them, so a patch keeps its relative place in the crop — halving
> does not push patches into a corner. If you compute a patch position in Bayer
> layer space, double both its offset from the bound origin and its size before
> writing it to the table.  
> - Option 1: 
>     - Define the 'bound' on the device, capture an image set, download the 
>image set `.h5` file, inspect it with 
>         `python src/hdf5read.py captures/S1000.h5`
>     - read the bound origin and size, eg: 
>         ` 'bound_h': np.int64(728), 'bound_w': np.int64(732),` 
>         ` 'bound_x': np.int64(806), 'bound_y': np.int64(386),`
>     - extract the `.png` image from the `.h5` file with
>        `python devdesk/hdf5inspect.py captures/S1000.h5 -o S1000bound.png`
>     - open `S1000bound.png` with inkscape, set the image object bounds using `bound_` values.
>     - draw your desired rectangles on the image and enter their bounds in the roi table. 
>
> - Option 2: Define the rectangles on the device and download the roi table
> to the desktop db. The Rect dimensions in the roi table are guaranteed correct.

Check the result before capturing anything — this catches the error above in
one look:

```bash
# desktop; prints each patch as a percentage across the bound
.venv/bin/python3 -c "
import sqlite3
c=sqlite3.connect('src/colorimeter.sqlite')
rows=list(c.execute('SELECT name,x,y,w,h FROM roi WHERE active=2'))
bx,by,bw,bh=[r[1:] for r in rows if r[0]=='bound'][0]
for n,x,y,w,h in rows:
    if n=='bound': continue
    print(f'{n:<8} {(x+w/2-bx)/bw*100:5.1f}%, {(y+h/2-by)/bh*100:5.1f}% across bound')
"
```

The four `white` patches must land near 25 % and 75 % on each axis, and
`sample` near 50 %. Anything clustered on one side is the halving error.

---

## 2 — Capture the suite (on the Pi)

Five suites of the **same uniform card**, using the normal capture path. The
card must be matte and uniform; a gray card is preferred over white stock,
which may have optical brighteners that fluoresce under violet.

| # | file | what to do |
|---|---|---|
| 1 | `*_A1a` | card placed, imaged |
| 2 | `*_A1b` | **do not touch the card.** Image again |
| 3 | `*_A2a` | lift the card and set it back down |
| 4 | `*_A180a` | rotate the card 180° in the plane |
| 5 | `*_A180b` | **do not touch it.** Image again |

Each suite must cover every LED and every exposure step.

**Then verify the five files are actually distinct** — a copy or a failed write
is easy to miss and silently invalidates the analysis:

```bash
# desktop, after syncing
md5sum captures/<prefix>*.h5
.venv/bin/python3 -c "
import h5py,glob
for p in sorted(glob.glob('captures/<prefix>*.h5')):
    with h5py.File(p) as f:
        print(p, f.attrs['samp_no'], f.attrs['suite_id'])
"
```

Five different checksums, five different `suite_id`s. If two match, re-capture.

---

## 3 — Choose the polynomial degree

### 3a. In-sample ladder

```bash
# desktop
.venv/bin/python3 devdesk/flatfield.py <A1a>.h5 -c
```

Read three things:

- **the noise exclusions.** Some LED × Bayer-layer combinations are barely
  excited — an LED that reads 630 DN in one layer may read 14 DN in another —
  and their patch means are too noisy to test a flat field. They are listed and
  dropped. Expect roughly 5–8 of 40 to go.
- **the patch line**, confirming the geometry from step 1.
- **the ladder**, which shows which degrees are *capable*. It is in-sample, so
  it is optimistic by construction; **do not choose a degree from it.**

### 3b. Transfer — this is what chooses the degree

Run all four pairings against the first suite:

```bash
# desktop
.venv/bin/python3 devdesk/flatfield.py <A1a>.h5 <A1b>.h5   -t
.venv/bin/python3 devdesk/flatfield.py <A1a>.h5 <A2a>.h5   -t
.venv/bin/python3 devdesk/flatfield.py <A1a>.h5 <A180a>.h5 -t --rot180
.venv/bin/python3 devdesk/flatfield.py <A1a>.h5 <A180b>.h5 -t --rot180
```

Each run registers the two captures from the card's own paper texture, then
scores every degree through that one registration. Registration is measured,
not assumed — no centre of rotation is presumed for the rotated pair, only the
translation that follows the 180° turn.

**Criterion T** picks the cheapest rung within the repeatability floor of the
ladder's best. Take the degree that all four pairings agree on. If they
disagree, prefer the rotated pairs: they move a card spot across the frame and
so actually exercise the field.

### 3c. Read the two columns apart

| column | what it contains |
|---|---|
| **P** | flatness of the corrected destination — flat-field error **plus** the card's own non-uniformity. Cannot separate them. |
| **R** | the destination's patch pattern divided by the source's. The same card spot is on both sides, so the card cancels. |

`R` means different things in the two cases, and this is the point of capturing
both:

- **rotated pair** — the card spot sits at two far-apart frame positions, so R
  is flat-field error with the card removed. This is the real test.
- **un-rotated repeat** — both positions coincide, so the field cancels out of
  the ratio along with the card. R then measures the bare capture-to-capture
  repeatability of a patch ratio: **the floor no degree can beat.** R staying
  constant down the degree column is the sign this is what you are seeing.

Sanity checks that the run is trustworthy:

- registration `peak/sigma` above 20 (a real texture match; a repeat scores >100)
- `quadrant spread` ≤ 2 layer px — one rigid offset aligns the whole card. A
  larger spread means the card was not turned through a clean 180°, and the
  patch-for-patch comparison degrades toward the corners. Re-seat and re-capture.

### 3d. Watch for an LED that is not measuring reflectance

Check the "worst pairs" list. An LED that is worst in **all four Bayer layers**
at every degree, while its noise floor is low, is not a noise problem. Two
causes to distinguish:

- **fluorescence** — the card stock re-emits at a different wavelength, so the
  "illumination field" is really the emission field. The `-t` mode flags this
  automatically when the two captures are of *different* cards.
- **a genuinely different field shape** — the LED sits elsewhere on the
  lightboard and its pattern needs more degrees than the rest.

Tell them apart with the rotated pair: rotate the card and see whether the
pattern follows it. A pattern fixed to the **frame** is illumination; one that
follows the **card** is the card.

---

## 4 — Record the result

```bash
# desktop; writes coefficients + gain maps, then verifies the round trip
.venv/bin/python3 devdesk/flatfield.py <A1a>.h5 -w --deg-y <N> --deg-x <N>
```

Writes to `devdesk/devdesk.sqlite` table `flatfield` — one row per
LED × exposure × Bayer layer, holding the coefficients, the bound, the patch
geometry the run used, and the fit quality — and one `.npy` gain map per
patch under `devdesk/flatfield/<suite_id>/`.

The round-trip check rebuilds every `.npy` from its stored coefficients. It
must report **all** maps rebuilding exactly. Rows written before the patch
geometry was stored cannot be verified and are reported separately; re-run `-w`
on those suites to make them self-contained.

Figures, one per LED × layer of interest:

```bash
# desktop
mkdir -p devdesk/flatfield/figs
.venv/bin/python3 devdesk/flatfield.py <A1a>.h5 -p green Gr \
    -o devdesk/flatfield/figs/fit_green_Gr.png
```

---

## 5 — Results for XCN-001 (2026-08-04)

Captures `ZP1001`–`ZP1005`, gray card, bound `(806, 386, 732, 728)` →
364 × 366 layers.

### Chosen degree: `(deg_y, deg_x) = (4, 4)`

Criterion T selected (4,4) on **all four** transfer pairings independently.

### The field is two-dimensional — this was the main finding

The commissioning bound was a 196 × 630 strip where the field fell 25 % along y
and ~2 % along x, so an anisotropic `(3,1)` basis was correct there. Over this
wider bound the field falls **24.7 % in y and 16.1 % in x**, and the old default
fails badly:

| degree | terms | criterion P, in-sample median |
|---|---|---|
| (3,1) — old default | 7 | **4.5 – 6.4 %** |
| (2,2) | 6 | 1.16 % |
| (3,3) | 10 | 0.69 % |
| **(4,4)** | 15 | **0.45 %** |
| (5,5) | 21 | 0.47 % |
| (6,6) | 28 | 0.60 % |

`(2,2)`, with *fewer* terms than `(3,1)`, beats it by a factor of five. **The
degrees belong to the bound, not to the instrument** — re-derive them whenever
the bound changes.

### Transfer at (4,4)

| pair | registration | P med | P max (ex-violet) | R max (ex-violet) |
|---|---|---|---|---|
| repeat, untouched | (0, 0) | 0.38 % | 1.36 % | 1.16 % — *repeatability floor* |
| card lifted and replaced | (−5, −11) px | 0.27 % | 1.20 % | 0.67 % |
| rotated 180° | (−9, +8) px, spread 2 | 0.71 % | 0.89 % | 1.27 % |
| rotated 180°, repeat | (−10, +8) px, spread 2 | 0.69 % | 0.97 % | 1.22 % |

### Measurement floor

From the untouched repeat, where the flat field cancels out of R entirely:

- patch-ratio repeatability **median 0.13 %**, p90 0.28 %, max 1.16 %
- the 1.16 % max is **royal_blue**, worst in all three of its layers while its
  noise floor is only 0.19 % — so this is LED output drift between captures,
  not shot noise. Worth allowing royal_blue to settle longer before capture.

Criterion P max of ~1.2 % is therefore close to what the measurement itself can
resolve; the median, 0.27–0.71 %, is the meaningful number.

### Excluded combinations

7 of 40 LED × Bayer-layer combinations were dropped for noise (criterion-P
floor above 0.25 %): `amber/B`, `blue/R`, `cyan/R`, `photo_red/B`, `red/B`,
`red_orange/B`, `royal_blue/R`. All are cases of an LED barely exciting a layer
— 14–26 DN against 500–700 DN in the layer that LED does drive.

### Violet is an outlier, and it is illumination, not the card

Violet scores 1.9–2.2 % at (4,4) in **all four** Bayer layers, against ≤ 0.91 %
for every other LED, with a low noise floor. Two measurements identify it:

- its field shape differs from the median LED's by **6 % (Gr) and 9 % (B)**,
  where every other LED except photo_red is within 1.7 %;
- its patch pattern is **inverted** — the corner patches read *brighter* than
  the centre, where every other LED reads them darker;
- rotating the card **does not move the pattern**, so it is fixed to the frame.
  It is the illumination geometry, not card fluorescence.

Violet needs more degrees than the rest: its block residual falls from 0.68 %
at (4,4) to 0.39 % at (6,6). Options are to fit violet at a higher degree than
the other LEDs, or to exclude it. **Not yet decided — carry this forward.**

---

## Open items

- Decide how violet is handled: per-LED degree, or exclusion.
- The `flatfield` table lives in `devdesk/devdesk.sqlite`. Getting the chosen
  coefficients onto the instrument is not yet designed.
- `photo_red` is the second-worst shape outlier (5.2 % in Gr) and the second
  worst on criterion P. Its Gb/Gr crosstalk is already known; check whether the
  two interact.

---

## See also

- `devdesk/flatfield.py` — the harness; its module docstring carries the
  reasoning behind divide-not-subtract and degree selection.
- `docs/commissioning/06_calibration.md` — where this sits among the other
  per-unit calibrations.
