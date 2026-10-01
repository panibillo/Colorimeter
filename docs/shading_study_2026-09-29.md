# Shading study: can the pre-image remove the pad's shadows? (2026-09-29)

Code: `desktop/padanalysis/shading_study.py` (read-only; `run()` has the
steps behind `if 1:` / `if 0:` guards).  Figures: `desktop/padanalysis/fig/shading_study_*.png`.

## Purpose

`duotone.contrast()` removes shading by taking out whatever is the same in
all channels.  That also removes the dye's even darkness, which is most of
brown, and is why the dot's strength does not follow concentration
(`tex/duotone_guide.tex`, section 12).  Bill proposed measuring the shading
directly on the dry pad in the pre-image and removing it pixel by pixel,
leaving all of the dye in.  Step 1 asks: how big is each kind of shadow,
and how much of it can the pre-image remove?

## The method, in plain terms

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

Figure: `fig/shading_study_surface.png`.  For six pads, the correlation of
the fine textures (six-channel average, inner white zone) for every extra
shift of the pre-image around arith.  Markers: + arith, □ ecc_rim,
○ ecc_tex, × peak.

**What it shows:** one sharp peak on every pad, reaching 0.74–0.89.  The
correlation halves about 1–1.5 px from the peak, so the texture is 1–2 px in
size and a registration must be good to a fraction of a pixel to use it.
Arith sits within 0.5 px of the peak.

## Step 1d — the correction, pictured (`show_maps`, approve by eye)

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

Bill's approach works, mostly at the smooth scale (the rim).  The
regression weights γ and β do what the proposal's ν was meant to: they come
out near 0.9 where the pre-image's shading carries over.  The proposal's
separate treatment of noise (averaging exposures) and the "unshaded"
percentile û turn out not to be needed.

## Decisions (Bill, 2026-09-29)

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
existing processing chain.

## Possible next step (not started)

**Step 2:** apply the correction to all six channels, **stop removing the
flat part** (`contrast()`), and redo the dot measurements of pca_study
step 2b in full absorbance.
- Does the dot's strength now follow concentration?
- Do the yellow and brown directions stay clean?
