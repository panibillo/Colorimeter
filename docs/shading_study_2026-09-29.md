# Shading study: can the pre-image remove the pad's shadows? (2026-09-29)

> **Reassessment started 2026-10-01.**  The sections below are the
> original study.  Each is marked: it may be deprecated, or kept.
> The new work is in "Reassessment" at the end.

Code: `desktop/padanalysis/shading_study.py` (read-only; `run()` has the
steps behind `if 1:` / `if 0:` guards).  Figures: `desktop/padanalysis/fig/shading_study_*.png`.

## Purpose

> *2026-10-01: under review; see "Reassessment" at the end.*

`duotone.contrast()` removes shading by taking out whatever is the same in
all channels.  That also removes the dye's even darkness, which is most of
brown, and is why the dot's strength does not follow concentration
(`tex/duotone_guide.tex`, section 12).  Bill proposed measuring the shading
directly on the dry pad in the pre-image and removing it pixel by pixel,
leaving all of the dye in.  Step 1 asks: how big is each kind of shadow,
and how much of it can the pre-image remove?

## The method, in plain terms

> *2026-10-01: under review; see "Reassessment" at the end.*

Everything is in absorbance A, where a shadow is a number *added* to a
pixel (in reflectance it would multiply).  Per channel:

| symbol | meaning |
|---|---|
| a_R | the measurement's A against the dry-pad median (what `analysis_1` gives now) |
| a_U | the pre-image's A against its own median: the dry pad's own shadows. Carried onto the measurement's grid by a registration. |
| smooth(a) | a averaged with a Gaussian 2 px wide, using pad pixels only (so the paper outside the pad does not leak in) |
| fine(a) | a − smooth(a): the pixel-scale texture |
| γ (gamma) | how much of the pre-image's *smooth* shading is still there in the measurement (slope of smooth(a_R) against smooth(a_U)) |
| β (beta) | the same for the *fine* texture |
| a_flat | a_R − γ·smooth(a_U) − β·fine(a_U): the measurement with the shading removed |

γ and β are measured on the **white zone**: the measurement's pad at least
6 px outside the dot, where there is (almost) no dye.  Its **rim** is the
part within 4 px of the pad's edge; its **inner** part is the rest.

γ and β are slopes rather than correlations, because noise and faint dye in
the measurement do not bias a slope, and the slope is the weight that leaves
the least error.  If the pre-image's shading carried over perfectly, both
would be 1; if it were useless, 0.

Four registrations (how the pre-image is laid onto the measurement):
- **arith** — `analysis_1.pre_to_post`, from the two fitted pad rectangles
  (in use now).
- **ecc_rim** — OpenCV ECC (shift + rotation) on the white zone, rim included.
- **ecc_tex** — OpenCV ECC (shift only) on the fine texture of the inner
  white zone: Bill's "maximize R*·U*".
- **peak** — the same aim by brute force.  Every shift within ±2 px of arith,
  in 0.25 px steps, is tried, and the best one kept.  This checks the ECC
  answers.

An affine ECC (which also allows stretch and shear) was tried first and
dropped: it failed to converge on some pads and ran off to scale factors of
1.5–5 on others, which a fixed camera cannot produce.

## Step 1a — is the pixel-to-pixel scatter noise or texture? (`show_split`)

> *2026-10-01: likely kept: the texture finding is a measurement, not a method choice; see "Reassessment" at the end.*

Figure: `fig/shading_study_split.png`.

- **Left:** in the pre-image each LED is a separate photograph, so the LEDs'
  camera noise is independent.  Whatever fine variation two LEDs share
  belongs to the pad.  Box = the correlation of fine texture between two
  different LEDs, over all pads and LED pairs.
- **Right:** set 2 (C1028–C1036) has three exposures per LED.  Two
  exposures share the pad but not the camera noise.  Line = the fraction of
  the fine variation that repeats between exposures, in the white zone.

| width w | LEDs agree (median correlation) | repeats between exposures |
|---|---|---|
| 1 px | 0.50 | 0.90–0.95 |
| 2 px | 0.70 | 0.96–0.98 |
| 4 px | 0.80 | 0.98–0.99 |

**What it shows:** 90–99 % of the pixel-to-pixel variation repeats from one
exposure to the next.  **The scatter on the pad is the pad's own texture,
not camera noise.**  Camera noise is only about 5–10 % of the fine variance
(σ ≈ 0.002 A, against 0.006–0.008 A for the total).  Different LEDs see
the texture only partly alike (0.5 at 1 px), so part of it is shading that
depends on the direction of the light: fibers lit from one side.

## Step 1b — the registrations, and what the correction does (`show_registration`)

> *2026-10-01: under review; see "Reassessment" at the end.*

Figure: `fig/shading_study_registration.png`.  One dot per pad per method,
each the median over the 6 channels.  Left γ, middle β, right the rim's
spread after correction divided by before (lower = better; 1 = no gain).

**Camera or pad?**  At the *same camera pixel*, with no registration, the
fine textures of the two sweeps correlate 0.58–0.88 on pads that moved
less than 0.7 px between the sweeps.  They correlate **−0.01 and −0.08** on
C1017 and C1028, which moved 7.3 and 8.5 px.  So the texture moves with the
strip; it belongs to the pad, not to the camera.

**Registration.**  The brute-force peak lies within 0.5 px of arith on
every pad.  ECC on the texture agrees with the peak to about 0.3 px, except
C1025 (0.8 px).  ECC on the rim failed to converge on 6 of 18 pads, and on
C1025 it ran 3 px away, which made the correction worse than none there
(rim ratio 1.09).  The better registrations gain little over arith:

| method | γ | β | inner spread before → after | rim before → after | inner fine texture before → after |
|---|---|---|---|---|---|
| arith | 0.92 | 0.87 | 0.0076 → 0.0072 | 0.0415 → 0.0133 | 0.0072 → 0.0065 |
| ecc_rim | 0.92 | 0.88 | 0.0076 → 0.0073 | 0.0415 → 0.0142 | 0.0072 → 0.0066 |
| ecc_tex | 0.93 | 0.88 | 0.0076 → 0.0070 | 0.0415 → 0.0139 | 0.0072 → 0.0065 |
| peak | 0.94 | 0.88 | 0.0076 → 0.0070 | 0.0415 → 0.0138 | 0.0072 → 0.0063 |

(Medians over 18 pads × 6 channels; spreads are robust σ in A.)

**On the six-channel average** — the "flat" part that `contrast()` now
discards, and where shading lives — the correction does better:

| | before | after (arith) |
|---|---|---|
| inner spread | 0.0055 | 0.0038 (−30 %) |
| rim spread | 0.0423 | 0.0094 (4.5× smaller) |
| white zone median A | 0.012 | 0.004 |

Pad by pad, the rim improves 2.9–6.3× and the inner part 10–52 %.

**γ and β above 1.**  γ ranges 0.63–1.13 and β 0.62–1.09; 19 and 16 of the
108 pad-channels are above 1.  Where that happens, the measurement's
shading is a little *stronger* than the pre-image's.  C1028, the pad that
moved most (8.5 px), is the clearest case.

## Step 1c — how sharply the textures line up (`show_surface`)

> *2026-10-01: under review; see "Reassessment" at the end.*

Figure: `fig/shading_study_surface.png`.  For six pads, the correlation of
the fine textures (six-channel average, inner white zone) for every extra
shift of the pre-image around arith.  Markers: + arith, □ ecc_rim,
○ ecc_tex, × peak.

**What it shows:** one sharp peak on every pad, reaching 0.74–0.89.  The
correlation halves about 1–1.5 px from the peak, so the texture is 1–2 px in
size and a registration must be good to a fraction of a pixel to use it.
Arith sits within 0.5 px of the peak.

## Step 1d — the correction, pictured (`show_maps`, approve by eye)

> *2026-10-01: under review; see "Reassessment" at the end.*

Figure: `fig/shading_study_maps.png`.  royal_blue.B, arith, four pads (2.2,
5, 20 and 160 ppb).  Columns: the pre-image a_U; its smooth part × γ; its
fine part × β; the measurement a_R; the corrected a_flat.  One color scale
per row, centered on 0 and set by the white zone, so the dot saturates and
the shadows show.  Black line: the dot's edge; gray line: start of the
white zone.

**What to look for:** the dark (red) rim in a_R is nearly gone in a_flat.
The pre-image's rim shadow matches the measurement's closely.

**Where it falls short:** the pad's outermost column or row, where the two
fitted pad edges do not coincide exactly.  C1029's right edge is
over-corrected (blue); C1034's left edge keeps some shadow.

## Findings

> *2026-10-01: under review; see "Reassessment" at the end.*

1. **The pixel-to-pixel scatter is the pad's texture, not noise.**  It
   repeats between exposures (90–99 %) and moves with the strip.  So:
   - averaging exposures would not reduce it;
   - separating noise from shading needs no extra exposures, because the
     noise is small;
   - `duotone`'s per-pixel "noise" σ (0.006–0.008 A, from neighbor
     differences) is mostly this texture.
2. **The rim is where the shadow is.**  Rim spread 0.04 A, against 0.006
   inside.  The pre-image removes about two thirds of the rim shadow per
   channel, and four fifths on the six-channel average.
3. **Inside the pad the gain is real but modest:** 10 % per channel and
   30 % on the channel average.  The fine texture changes between the
   sweeps (the strip is taken out and put back, and the light meets the
   fibers at a slightly different angle).
4. **Registration: arith is good enough.**  It is within 0.5 px of the best
   alignment.  ECC on the texture finds that best alignment too, for a small
   gain.  ECC on the rim is unreliable, and affine ECC unusable.
5. **The remaining white-zone level (+0.004 A) is left in by design.**  It
   is either faint dye over the whole pad or a real change of the paper;
   neither is shading.

## For the proposal

> *2026-10-01: under review; see "Reassessment" at the end.*

Bill's approach works, mostly at the smooth scale (the rim).  The
regression weights γ and β do what the proposal's ν was meant to: they come
out near 0.9 where the pre-image's shading carries over.  The proposal's
separate treatment of noise (averaging exposures) and the "unshaded"
percentile û turn out not to be needed.

## Decisions (Bill, 2026-09-29)

> *2026-10-01: under review; see "Reassessment" at the end.*

### 1. Registration: keep the arithmetic one

The pre-image stays registered by `analysis_1.pre_to_post` (arith), as
now.

**The texture-peak search is kept as demonstration code, not used.**  It
lives in `shading_study.py`:
- `shift_surface(samp_no)` tries every extra shift of the pre-image within
  ±2 px of arith, in 0.25 px steps.  For each shift it computes the
  correlation of the two fine textures over the inner white zone.
- `warp_of(samp_no, "peak")` keeps the best shift.
- `show_surface()` draws the search (step 1c).
- `white_stats(samp_no, "peak")` measures the correction with it.

**Tested on** all 18 pads, C1017–C1025 and C1028–C1036, in the six shared
channels.  The surface figure shows six of them: C1034, C1032, C1017,
C1029, C1035 and C1025.

**How much it improves registration** (per pad; the correction's spreads
are medians over the six channels):

| | median over the 18 pads | range |
|---|---|---|
| shift away from arith | 0.25 px | 0 to 0.56 px |
| fine-texture correlation | 0.782 → 0.789 | gain 0 to +0.06 (C1018, C1021) |
| inner fine spread after correction | −0.3 % | −15 % (C1033) to +5 % (C1020, C1036) |
| rim spread after correction | 0 % | −24 % (C1017) to +41 % (C1033) |

So arith is already within about a quarter pixel of the best texture
alignment.  The search helps a few pads a little inside the pad.  At the
rim it helps some pads and hurts others, because it lines up the texture,
not the rim.  It is not worth adopting as it stands.

**When a better registration would pay:**
- if the pixel-scale texture correction becomes important — for example,
  when judging single pixels near the detection limit;
- or if a new strip holder lets the strip move more between the two photos.

It would then need to line up the rim and the texture together, rather than
the texture alone.  The rim-based ECC (`ecc_rim`) failed on 6 of 18 pads,
so it is not the starting point.

### 2. The pad's outermost pixel: kept

The measured pad keeps its full area, out to its edge.  If the work goes
back from the dot geometry to the whole pad (for example, adding up the
whole pad, or filling in a dot cut off by the pad's edge), the whole pad
edge is needed.

**The cost of this choice:** in the outermost row or column the two photos'
fitted pad edges do not quite coincide, so the shading correction there is
sometimes too strong (C1029's right edge) and sometimes too weak (C1034's
left edge); see step 1d.

**When a different choice would be better:**
- when only the inside of the pad is used (the dot and the rings around it,
  as in `pca_study`), leaving out the outermost 1 px costs nothing and
  removes those errors;
- or once the pad edges are fitted more closely in both photos.

### 3. Step 2 is not started

Before going on, Bill asked for a summary of what this study changes in the
existing processing chain.  It is the next section.

## What step 2 would change in the chain (draft, 2026-10-01)

> *2026-10-01: superseded in part: points a–d are being redone in the Reassessment; see "Reassessment" at the end.*

A reminder of the quantity: **A (absorbance)** = −log10( R_inf ).  Bigger
means darker.  On the dry pad in the pre-image it is 0 by construction.

### Where shading is removed now

In one place only: `duotone.contrast()`.  For every pixel it takes the
average of that pixel's A over the channels and subtracts it from each
channel.  Call that average the **flat part**.  A shadow darkens every
channel by the same amount, so the shadow goes into the flat part and is
removed.  But any dye darkness that is the same in every channel goes too,
and that is most of brown (`tex/duotone_guide.tex`, "Limits of the
model").

`contrast()` is used by `duotone.unmix`, `duotone.residual_rms` and
`duotone.fit`.  `duotone.amount_sigma` removes the same flat part from its
noise numbers.  Outside `duotone`, it is used by `analysis_1.cell_PCA` and by
`pca_study.py` (the shared color map, the map positions of each pad's own
reference colors, the ring colors).

Nothing earlier in the chain removes shading.  R, R_inf, A and K/S from
`analysis_1.get_balanced_cell_data` all still include it.

### What step 2 would do

1. **Remove the shading measured on the pre-image**, per channel, as
   `shading_study.correct()` does now:
   a_flat = a_R − γ·smooth(a_U) − β·fine(a_U).  The symbols are defined
   in the table near the top of this document.  The inputs are both
   photos' A from `get_balanced_cell_data`, the arithmetic registration,
   and the dot's distance map, which marks the dye-free white zone where
   γ and β are measured.
2. **Stop subtracting the flat part.**  The yellow/brown split would then
   work on a_flat, which keeps all of the dye's darkness, including the
   part that is the same in every channel.
3. **Do it in the study scripts first.** Add a step that redoes pca_study's
   step 2b (ring colors around the dot) on a_flat.  `duotone.py` and
   `analysis_1.py` stay as they are until Bill has reviewed duotone.

### What does not change

- The dark level, the hot-pixel mask, the per-pixel gray divisor, the
  white reference (dry pad in the pre-image), and the arithmetic
  registration.
- R, R_inf, A and K/S as `get_balanced_cell_data` returns them, and the
  `analysis_1.CHAIN` version tag.
- Finding the dot's edge (`dot_study.dot_edge`, on uncorrected R_inf).
- This study's figures and numbers.

### What step 2 would make out of date, if adopted

- Everything the yellow/brown split has produced through `contrast()`:
  - `fig/pca_study_own_endmembers.png` and `fig/pca_study_ring_paths.png`;
  - `fig/analysis1_*_pca.png`;
  - the printout of `duotone.py` run on its own;
  - the steps in `tex/duotone_guide.tex` built on removing the flat part
    (step 1, and the noise section).
- The noise numbers that go with the split.
  - `PadCube.sigma` and `noise_cov` measure the scatter of A *before*
    correction.
  - `amount_sigma` assumes the flat part has been removed.
  - After correction the scatter is smaller, per channel: about 3× at the
    rim (0.042 → 0.013 A) and 5–10% inside the pad (all variation
    0.0076 → 0.0072 A; fine texture alone 0.0072 → 0.0065 A).  Subtracting the pre-image also adds
    the pre-image's own camera noise, about 0.002 A per pixel.
  - The scatter would need measuring again on the corrected white zone.

### Points that need a decision

a. **Where γ and β are measured.**  On the white zone: pad pixels at least
   6 px outside the dot.  This needs a dye-free part of the pad, so it
   depends on the present exposure geometry, which is not final.  If a
   future geometry darkens the whole pad, there is nowhere to measure them.
   The fallback would be fixed values: 1 (plain subtraction) or the
   study's medians (γ ≈ 0.92, β ≈ 0.87).
b. **Whether to keep the fine part (β).**  Nearly all of the gain is in
   the smooth part, at the rim.  The fine part cuts the fine texture inside
   the pad by about 10%, needs registration good to a fraction of a pixel, and adds the
   pre-image's camera noise.  A smooth-only correction (β = 0) is simpler.
c. **Six channels or eight.**  The study used the six that all 18 strips
   share.  Ten of them have only those six.
d. **What happens to shading the correction misses.**  Once the flat part
   is no longer removed, leftover shading counts as dye.  The leftover is
   about 0.013 A spread at the rim per channel, and +0.004 A median on the
   white zone.  This is the price of keeping all of the dye's darkness, and
   step 2 should report it.
e. **Pi code.**  The correction uses scipy (Gaussian smoothing,
   resampling).  That is fine on the desktop, but the Pi code must be numpy
   only.  It doesn't matter until the method moves to `src/`.

### The question step 2 answers, and when to stop

- **Question:** once all of the dye's darkness is kept, does the dot's
  strength follow concentration across the 18 strips?
- **Decision it could change:** whether `duotone` replaces `contrast()`
  with the pre-image correction.
- **Stop when:** one figure shows dot strength against concentration
  (ppb), uncorrected next to corrected, on the same scale.  If the
  corrected version doesn't order the strips better, removing the flat
  part was not the main reason they were out of order.  Replicate strips
  disagree by about 1.9×, so expect scatter either way.

## Possible next step (not started)

> *2026-10-01: under review; see "Reassessment" at the end.*

**Step 2:** apply the correction to all six channels, **stop removing the
flat part** (`contrast()`), and redo the dot measurements of pca_study
step 2b in full absorbance.
- Does the dot's strength now follow concentration?
- Do the yellow and brown directions stay clean?

---

## Reassessment (started 2026-10-01)

Code: `desktop/padanalysis/shading_review.py` (read-only; `run()` has the
steps behind `if 1:` / `if 0:` guards).  Figures:
`desktop/padanalysis/fig/R*_*.png` (each file and plot window starts with its step label).

### Why start again

The original study answered "can the pre-image remove the pad's
shadows?" well, but it did not start from what the shadows *are*, and it
grew sideways (four registrations).  Bill was no longer sure where it stood,
or whether dye darkness and shading are now fully kept apart.  This
reassessment rebuilds the question in small steps, each with figures.  It
follows `How_best_to_help_Bill.md`: every variable is named as an **array**
(one value per pixel) or a **scalar** (one number).

### Kinds of unevenness on the pad (Bill, 2026-10-01)

| kind | what it is | expected size | removed by |
|---|---|---|---|
| A. Lighting | the light is not the same everywhere on the stage | smooth, across the frame | the gray-chip polynomial (`div`) |
| B. Fiber shadows | fibers in the paper cast tiny shadows, perhaps a little stronger at the cut edge | 0.5 to 2–3 px | not removed now |
| C. Pad-edge shadow | the pad's edge shades the paper next to it | under 1–2 px wide (Bill's estimate) | not removed now |
| D. Dye | the signal | — | must **not** be removed |

Each LED color is really four LEDs, one beyond each corner of the pad.  So
most shadows are lit by one, two or three of them, and a shaded pixel
averages shaded and unshaded paper.  Bill's aim for shading removal: a
smoothly varying surface whose average over any small area equals the same
color unshaded.

A wide dark rim, several pixels across, was seen on the earliest samples
(before about C1012, before the dot geometry).  It was probably chemical,
which is kind D.  Those samples have no pre-image.

### What the chain does now, step by step

All on the bound-crop Bayer-layer grid (130 × 183 for bound 39).

| name | array or scalar | what it is |
|---|---|---|
| `sig` | array, counts | raw counts minus the dark level (the dark level is one scalar per Bayer layer per photo) |
| `div` | array, counts | the gray-chip level at every pixel: the channel's flat-field polynomial, scaled to match the two gray chips in the same photo |
| `R` | array | `sig / div`, brightness against the gray chips |
| `R_white` | **scalar** | the median of the pre-image's `R` over its pad (eroded 6 px): one number per channel |
| `R_inf` | array | `R / R_white` |
| `A` | array | −log10( `R_inf` ).  Bigger means darker. |

**The pre-image enters the chain as one number per channel, `R_white`.**
The post-image is *not* divided by the pre-image pixel by pixel.  Only
the original study's correction (`shading_study.correct`) used the
pre-image pixel by pixel, and that correction is not in the chain.

### Bill's questions, and the step that answers each

**Q1. How even is the pad before and after the gray-chip polynomial?**
Step R1, below.

**Q2. If the pre-image is as unevenly lit as the post-image, wouldn't
dividing post by pre give the same result with or without the gray-chip
polynomial?**  Reasoning first; step R2 will show it with figures.

Write each photo's signal at one camera pixel as three factors:
- the light reaching that camera pixel (an array, fixed to the stage);
- the paper's reflectance at the spot of paper that sits there (an array
  that moves with the strip);
- the photo's overall brightness, from the exposure time and how bright
  the LEDs were that day (a scalar).

Divide post by pre pixel by pixel, after lining up the spots of paper:
- **The light's shape cancels**, as long as the strip moved little between
  the photos.  The two photos then use nearly the same camera pixels for
  the same spot.  So yes: the polynomial's *shape* would hardly matter.
- **The overall brightness does not cancel.**  The ratio still carries the
  scalar (post brightness) / (pre brightness).  The gray chips supply
  that, or a known exposure ratio and perfectly steady LEDs would.  So the
  polynomial's *level*, pegged to the chips, is still needed.
- But the chain divides by the scalar `R_white`, not pixel by pixel.  So
  in the chain as it is, the polynomial is what flattens the light.

**Q3. Does dividing by the pre-image add the pre-image's noise?**
Reasoning first; step R3 will show it with figures.
- **In the chain now, hardly at all.**  `R_white` is a median of about
  1,500 pad pixels.  Its own error is roughly 1.25 × (pixel spread) / √n:
  1.25 × 0.0077 / √1500 ≈ 0.0003 log10, under 0.1 %.  And it is the same
  for every pixel, so it shifts the whole pad slightly but adds no
  pixel-to-pixel scatter.
- **Pixel by pixel, yes.**  Each post pixel would take on its pre pixel's
  scatter.  The paper's texture would partly *cancel*, because it repeats
  between the photos (fine-texture correlation about 0.78, step 1b).  The
  camera noise would *add*: about 0.002 A from each photo, so about 0.003 A
  combined.  That is the trade the original study's β weighed.

### Step R1 — evenness before and after the gray-chip polynomial (pre-image)

Functions: `show_gray_maps` (R1a), `show_gray_hist` (R1b), `gray_table`
(R1c).  Brightness is shown as **log10(value / pad median)**: an array, 0
at the median, + is brighter, and 0.01 is about 2.3 %.  On this scale
−0.01 is the same as +0.01 in A.

**R1a, maps** (`fig/R1a_gray_maps_C1032_royal_blue.B.png`).
C1032 royal_blue.B pre-image, e0 (1000 µs).  Columns: `sig` (before),
`div`, `R` (after).  Top row: whole crop.  Bottom row: pad only.  Color =
each pixel; black lines = contours of a 4 px smoothing.
- `div` is nearly flat over the pad: it varies by only 0.003 log10
  (0.7 %).  It sits 0.003–0.012 log10 lower at the gray chips, and spans
  0.021 log10 (5 %) over the whole fitted frame.  Its rows 0–20 are blank
  because the polynomial was not fitted there.
- So, at the pad, the polynomial mostly carries the chips' level across
  to the pad (a correction of 1–3 %).  It changes the pad's shape very
  little, and before and after look nearly the same.
- What does stand out is the **pad's edge**: darker all the way round, in
  both before and after.  The polynomial does not touch it.
- **Caution:** the black contour lines make the edge look wider than it
  is, because smoothing spreads the dark edge inward.  Read the edge's
  width from R1b's top profile, not from the contours.

**R1b, histogram and profiles**
(`fig/R1b_gray_hist_C1032_royal_blue.B.png`).
- Histogram: one main peak, about 0.01 log10 wide, plus a long dark tail
  down to −0.08.  The tail is the edge pixels.  Before and after are
  nearly the same.
- Column means across the pad, not smoothed (top right): left edge
  −0.15, −0.04, −0.01, then flat; right edge −0.007, −0.011, −0.027,
  −0.075.  **The edge shadow is about 2–3 px wide on this pad and
  channel**, in line with Bill's estimate for kind C.  The outermost
  column is darkened by about 30 %.
- Smoothed inner-pad trend (bottom right): within about 0.01 log10;
  before and after differ by about 0.001.

**R1c, all 18 pre-images × six channels.**  "Spread" = robust spread of
every pixel on the whole pad (an array reduced to a scalar).  "Trend" =
the 95th minus 5th percentile of the 4 px smoothed brightness on the inner
pad (eroded 4 px): the lighting's unevenness, with texture and edge left
out.  Both in log10.

| channel | trend before | trend after | after is better on |
|---|---|---|---|
| violet.B | 0.0109 | 0.0058 | 18 of 18 |
| royal_blue.B | 0.0057 | 0.0056 | 7 of 18 |
| cyan.B | 0.0064 | 0.0059 | 11 of 18 |
| green.Gb | 0.0069 | 0.0062 | 16 of 18 |
| green.Gr | 0.0065 | 0.0058 | 15 of 18 |
| photo_red.R | 0.0074 | 0.0093 | **2 of 18** |
| all 108 | 0.0071 | 0.0063 | — |

Whole-pad spread: 0.0125 before, 0.0124 after (medians).  The polynomial
leaves the pixel scatter alone, as it should.

**What R1 shows**
1. Over the pad itself the light is already fairly even: about 1.5 %
   from one side to the other.  The polynomial halves that in violet,
   helps a little in green, and does nothing in royal blue.
2. **In photo_red.R the polynomial makes the pad slightly *less* even**
   on 16 of 18 strips (0.0074 → 0.0093 log10, about 1.7 % → 2.2 %).  Its
   polynomial may put a small tilt across the pad.  That is small next to
   the edge shadow, but about the size of the paper's texture.  Noted;
   not pursued yet.
3. The largest unevenness on the dry pad is the **edge shadow** (kind C):
   2–3 px wide and up to 30 % in the outermost column.  The polynomial
   cannot remove it, because it belongs to the pad, not the light.

**R1d, the pad's histogram split into inner pad and rim**
(`show_split_hist`, `fig/R1d_split_hist_C1032_royal_blue.B.png`).
Bill saw two humps in the whole-pad histogram (R1b): the inner pad, and
the pad minus the inner pad (the outer 4 px, called the **rim** here).
Three panels share the x axis: whole pad, inner pad, rim.  The rim's
counts are exactly whole minus inner.  A normal curve is fitted to the
"after" values (b_R, an array) of each part, with the same mean and
standard deviation.  The measures of fit, each a scalar, are defined in
`normal_fit`.

| measure | normal curve would give | inner pad (n = 1522) | rim (n = 689) |
|---|---|---|---|
| mean, sd (log10) | — | +0.0025, 0.0073 | −0.0341, 0.0443 |
| median, robust sd | median = mean, robust sd = sd | +0.0026, 0.0073 | −0.0186, 0.0287 |
| skew (lopsidedness) | 0 | −0.12 | −1.85 |
| excess kurtosis (tail weight) | 0 | +0.06 | +3.70 |
| beyond 3 sd | 0.27 % | 0.20 % | 2.76 % |
| largest gap in running fraction | 0 | 0.027 | 0.144 |

- **The inner pad is very close to a normal curve.**  Its spread, 0.0073
  log10 (about 1.7 %), is the paper's texture plus camera noise.  The
  slight dark lean (skew −0.12) is a small hint that fiber shadows darken
  more than they brighten (Bill's kind B), but only a hint.
- **The rim is not one normal curve.**  It is itself two parts:
  - a hump near 0, like the inner pad: rim pixels with little shadow;
  - a long dark tail down to −0.20: the shaded edge pixels.
  
  One curve cannot fit that, hence the large skew, tail weight and gap.
  The rim mixes pixels at different distances from the edge, and their
  darkness depends on that distance.
- The two fitted curves summed (top panel) follow the whole-pad histogram
  in the middle, but not the rim's hump or its far tail.

Pictures for judging these figures, from `colorview.py` (not a
measurement):
- `fig/aux_colorview_C1032_pre_royal_blue_e0.png`: the same photo R1 maps;
- `fig/aux_colorview_C1032_post_white_e1.png`: the post-image under the white
  LED, gray-balanced, the nearest to what an eye sees.  The pad's top and
  bottom edges show as gray lines across the strip.

### Step R2 — the post-image divided by the pre-image, pixel by pixel

Functions: `post_pre` (the data), `show_r2_maps` (R2a), `show_r2_hist`
(R2b), `r2_numbers` (R2c).  C1032 royal_blue.B.  Both photos go to R
through the chain.  The pre-image's R is carried onto the post-image's
grid by the chain's registration (`pre_to_post`), and both are divided by
the same scalar `R_white`:

| name | array or scalar | what it is |
|---|---|---|
| `A_post` | array | −log10(`R_post` / `R_white`): the chain's A now ("before") |
| `A_pre` | array | −log10(`R_pre` / `R_white`): the dry pad, on the post-image's grid |
| `A_flat` | array | `A_post` − `A_pre` = −log10(`R_post` / `R_pre`): "after" |
| `r` | scalar, per region | the correlation of `A_post` and `A_pre`, pixel by pixel |

Regions: **dyed** = inside the dot's edge.  **Dye-free** = at least 6 px
outside it, split into **inner** (more than 4 px from the pad's edge) and
**rim**.  **Whole pad** also holds the 0–6 px band where the color fades.

Why `r` matters:
sd_after² = sd_post² + sd_pre² − 2·r·sd_post·sd_pre.
- If the two photos' patterns are unrelated (r = 0), dividing *adds* the
  pre-image's spread.
- If they are the same (r = 1, equal spreads), dividing cancels them.

**R2a, maps** (`fig/R2a_maps_C1032_royal_blue.B.png`).  The edge shadow
in `A_post` is nearly gone in `A_flat`.  The dot is unchanged.  The
outermost column is now over-corrected in places (blue, left edge), where
the two photos' fitted pad edges do not quite coincide (as in the
original study, step 1d).

**R2b, histograms** (`fig/R2b_hist_C1032_royal_blue.B.png`) and **R2c,
numbers** (in A; bigger means darker):

| region | n | mean before → after | sd before | sd pre | sd after | sd if unrelated | r |
|---|---|---|---|---|---|---|---|
| whole pad | 2215 | +0.0835 → +0.0724 | 0.1646 | 0.0295 | 0.1667 | 0.1673 | +0.02 |
| dyed | 280 | +0.4941 → +0.4935 | 0.1125 | 0.0061 | 0.1115 | 0.1127 | +0.20 |
| dye-free, inner | 760 | +0.0049 → +0.0050 | 0.0068 | 0.0066 | **0.0074** | 0.0095 | +0.39 |
| dye-free, rim | 686 | +0.0394 → +0.0040 | 0.0411 | 0.0432 | **0.0122** | 0.0596 | +0.96 |

**What R2 shows**
1. **The edge shadow (kind C) is removed.**  It is almost the same in both
   photos (r = 0.96).  The rim's spread falls 3.4× (0.041 → 0.012), and its
   mean falls from +0.039 to +0.004, the same as the inner pad's.
2. **The fiber texture (kind B) is not removed; the inner pad gets
   slightly worse** (0.0068 → 0.0074).  Pixel by pixel, the two photos'
   inner textures are only partly alike (r = 0.39).  So dividing cancels
   some texture but adds the pre-image's non-matching texture and camera
   noise.  The result is between "cancels" and "unrelated" (0.0095), on
   the wrong side of "no change".
3. **The dye is untouched.**  The dot's mean moves by 0.0006 A (0.1 %)
   and its spread by 1 %.  The dot itself is not one normal population:
   it holds pale and dark parts (A from about 0.2 to 0.6), so its normal
   fit is not meaningful and is shown only for completeness.
4. The whole pad's mean drops by 0.011 because the rim's shadow is gone.

### Step R3 — four ways to divide the post-image by the pre-image (2026-10-02)

**Question:** which way of bringing in the pre-image leaves the corrected
pad flattest without touching the dye?  Same sample and channel as R2
(C1032 royal_blue.B).  Code: `shading_review.r3_methods`, `r3_numbers`,
`show_r3_maps` (R3a), `show_r3_sorted` (R3b).

Both photos are already corrected for darkness and against their gray
chips (R, then A).  In A, dividing reflectances is subtracting A, so each
method is A_after = A_post − corr, where corr is what it takes out:

| method | corr (what is taken out) | array or scalar |
|---|---|---|
| A | nothing beyond the scalar `R_white` (median of the pre-image pad, eroded 6 px), already in A_post: the chain now | scalar |
| B1, B2 | smooth(A_pre): the pre-image blurred 1 px or 2 px, pad pixels only | array |
| C | A_pre: pixel by pixel (R2) | array |
| D | wt · A_pre + (1 − wt) · smooth(A_pre, 1 px): weights summing to 1 (Bill) | array; wt scalar |

D lies between B1 (wt = 0) and C (wt = 1).  wt is fitted (least squares)
on the dye-free zone: **wt = 0.81**.  For comparison only: fitting the
smooth and fine weights freely gives 0.94 and 0.87, close to the original
study's γ and β.

Mean or median for A: the choice moves every pixel by the same constant
and does not change any spread below, so only the median (the chain's) is
shown.

**Error, exactly as computed** (arrays unless marked SCALAR):

| name | what it is |
|---|---|
| corr | what the method takes out of A_post (row 1 of R3a); all zero for A |
| level | **SCALAR**, one per method: median of A_post − corr over the dye-free inner pad |
| err | A_post − corr − level, on the whole pad (row 2).  0 = "as the dye-free inner pad".  Inside the dot it is dye, not error |
| err_s | err blurred 2 px over the whole pad, pad pixels only (row 3): the smooth part, shadow left over (+) or overdone (−) |
| err_f | err − err_s (row 4): the fine part, everything that changes within about 2 px.  Not only camera noise: also fiber texture not cancelled, one-pixel mismatch at the pad's edge, and near the dot the dye's steep edge |

**Where a single number is applied across the pad:**
- **R_white** (one number, the median of the pre-image pad eroded 6 px).
  It cancels exactly in B, C and D, and `level` removes it in A.  So it
  has no effect on err, err_s or err_f.
- **level** (one per method), subtracted from every pad pixel, rim and
  dot included.  It only sets where 0 is.  But because it centers each
  method's dye-free inner pad on 0, a method that shifts the whole inner
  pad (B2: 0.0027 against 0.0045) doesn't show that shift in err.
- **wt** (D only, one number), fitted on the dye-free zone with inner and
  rim pooled, then applied to every pad pixel, the dot included.  **Fitted
  on each part alone it differs a lot: inner pad 0.24, rim 0.85, pooled
  0.81.**  The pooled fit is set by the rim, whose error is about 6 times
  larger.  So D's one wt suits the rim and is too high for the inner pad.
- The fit's constant (D only): fitted alongside wt so an overall offset
  doesn't bias wt, then discarded.
- The blur widths (1 px, 2 px, and 2 px for the split) are fixed
  settings, not measured.

Checked: blurring err over the whole pad mixes a little of the dye's fade
into err_s near the start of the dye-free zone: on average +0.0004 to
+0.001 A within 3 px of the gray line (at most +0.004), against
blurring over the dye-free zone alone.  Small, but not zero.

Results (sd in A; "rob sd" = 1.4826 × median absolute deviation):

| method | inner: sd | inner: rob sd | inner: smooth | inner: fine | rim: sd | rim: smooth | rim: fine | rim: 99th pct | dot mean |
|---|---|---|---|---|---|---|---|---|---|
| A | 0.0068 | 0.0066 | 0.0038 | 0.0065 | 0.0411 | 0.0157 | 0.0299 | +0.177 | 0.4941 |
| B1 | **0.0066** | 0.0061 | 0.0028 | **0.0058** | 0.0203 | 0.0042 | 0.0184 | +0.061 | 0.4934 |
| B2 | 0.0073 | 0.0071 | 0.0035 | 0.0062 | 0.0296 | 0.0064 | 0.0252 | +0.111 | 0.4933 |
| C | 0.0074 | 0.0064 | 0.0026 | 0.0067 | 0.0122 | 0.0043 | 0.0100 | +0.032 | 0.4935 |
| D | 0.0071 | **0.0060** | **0.0026** | 0.0063 | **0.0119** | 0.0042 | **0.0099** | +0.034 | 0.4935 |

(inner = dye-free inner pad, 760 px; rim = dye-free rim, 686 px; dot 280 px.)

**What R3 shows**

1. **No method wins everywhere.**  On the inner pad, B1 is best (sd
   0.0066, fine part 0.0058), and C is worst (0.0074).  On the rim, C
   and D are best (0.012) and B1 is about twice as bad (0.020).  The rim
   matters more: its error is several times larger.
2. **D is about as good as C on the rim and a little better inside**
   (inner sd 0.0071 against 0.0074; robust sd 0.0060, the lowest).  The
   gain over C is small.
3. **Blurring the pre-image spreads the edge shadow inward.**  The edge
   shadow is darkest in the outermost column, and a blur moves part of
   it one or two columns in.  So B under-corrects the outermost column
   and over-corrects the next ones in (blue just inside the pad's edge
   in R3a's "error" row).  With 2 px (B2) the leak reaches the inner pad:
   its level drops from 0.0045 to 0.0027.
4. **The dye is untouched by every method**: the dot's mean differs by
   at most 0.0008 A, and the sorted dot values lie on top of each other
   (R3b, bottom middle).
5. **Every method leaves some over-correction at the pad's left edge**
   (blue in R3a).  C and D show it in single pixels; B spreads it.  See
   the deferred item below.

Note on row 4 of R3a (fine part): inside and around the dot it shows
the dye's own edge (blue ring, red center), not noise, because the 2 px
blur cannot follow the dye's steep edge.  The rows were relabeled on
2026-10-02 (formulas and scalars on the figure; "pixel noise" renamed
"fine part of error").

**Report:** `tex/shading_removal.tex` (2026-10-02) writes R3 up with equations, a worked example, and two more figures: R3d (the pre-image pad against the single white value R_white, by depth) and R3e (every method along one row).  Made by `shading_review.tex_figs()`.

### Step R4 — lining up lowers the pre-image's pixel-to-pixel scatter (2026-10-03)

Bill's note on `tex/shading_removal.tex`: the 4-pixel (bilinear) average used to
line the pre-image up on the post-image lowers its pixel noise, so measure it.
`shading_review.lineup_noise`, `lineup_noise_table`, figures R4a and R4b.
Robust spread of neighbor differences, inner pad:

| | own grid | nearest pixel | bilinear (chain) | ratio bilinear/own | predicted (independent noise) |
|---|---|---|---|---|---|
| C1032 royal_blue.B, along rows | 0.0067 | 0.0067 | 0.0054 | 0.80 | 0.81 |
| C1032 royal_blue.B, along columns | 0.0068 | 0.0068 | 0.0049 | 0.72 | 0.74 |
| 18 pads × 6 channels, median | 0.0067 | 0.0067 | 0.0041 | 0.61 (range 0.43–0.96) | 0.58 |

- The ratio is set by where each pad lands between pre-image pixels (fractional
  shift); half-way loses about half the scatter (C1017, C1019, C1020, C1022,
  C1025, C1030, C1034, C1035).
- So method 𝒞 ("pixel by pixel") is really a smoothing whose strength varies
  from pad to pad, and the best λ in 𝒟 may too.  C1032 is a mild case.
- `duotone.PadCube` measures noise with `register=False`, so it is unaffected.
- `tex/shading_removal.tex` rewritten 2026-10-03 in the notation of
  `tex/analysis_chain.tex` (C = A − B̂ for every method).

### Next steps (not started)

- **Deferred to the next step (Bill, 2026-10-02): why the rim is
  over-corrected, and how to avoid it.**  Bill asks what mechanism makes
  the outer rim over-corrected (R2, R3), and whether there are smoothing
  methods that recognize an edge, so the correction is not overdone
  there.  Leads already seen: the two photos' fitted pad edges do not
  line up to the pixel (R2), and a blur carries the edge shadow inward
  (R3, finding 3).  Edge-aware smoothing to look at then: the
  normalized smoothing already used (pad pixels only), a smoothing done
  along the edge rather than across it, and a weight that changes with
  distance from the pad's edge (more of the pixel-by-pixel pre-image
  near the edge, more of the blur inside).
- **R1e (a note for later, maybe; Bill is not sure it would be useful):** the rim split into 1 px bands by distance from the
  pad's edge, one histogram per band.  This tests Bill's estimate that
  the edge shadow is 1–2 px wide better than one 4 px rim can.
- ~~R2~~ done (pixel by pixel; the no-polynomial comparison was dropped
  by Bill).  ~~R3~~ done (four methods, 2026-10-02).
- R3 on the other 17 samples and 6 channels, to see whether wt and the
  ranking of the methods hold.
- Later: the edge-shadow profile on all pads and channels (kind C), and
  the inner pad's texture histogram (kind B, whether shadows only darken).
