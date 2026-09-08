# Characterising the yellow and brown pixel clouds — full explanation

**Printable companion to `duotone_features_prompt.md`.** Same content, but every
quantity is defined, every abbreviation is written out, and nothing assumes the
reader has the code open. Written 2026-08-24 against branch `preimage-capture`.

---

## Part 1 — Vocabulary

Terms used throughout, in the order they become necessary.

**Arsenic concentration, parts per billion.** The nominal arsenic concentration
of the water sample that a test strip was exposed to. One part per billion is
one microgram of arsenic per litre of water, written elsewhere in the project as
one microgram per litre. The value is recorded by hand in the `manual_read`
column of the `samples` table of the database file
`src/colorimeter.sqlite`. The nine strips discussed here span 2 to 160 parts per
billion. Throughout this document "dose" and "concentration" mean this number.

**Strip, pad, and stain.** A test strip is a paper strip carrying a small
circular indicator pad impregnated with mercuric bromide. Arsine gas generated
from the water sample passes over the pad and reacts with the mercuric bromide,
producing a coloured stain. "Pad" means the whole indicator area; "stain" means
the coloured part of it, which is usually a round blot smaller than the pad.

**The two coloured products.** Arsine displaces bromide from mercuric bromide in
a stepwise sequence. Mercuric bromide itself is colourless. The
**mono-substituted** and **di-substituted** products are both **yellow**; the
**tri-substituted** product is **brownish-yellow to brown**. So "yellow" and
"brown" as used here are the two ends of that sequence, and yellow is an
intermediate: at a given spot on the pad it accumulates first and is then
consumed as brown forms. This is the chemistry set out in
`tex/gutzeit_analysis.tex`, section "Step 3: stain formation", and confirmed
from the image data in `docs/two_dye_pad_analysis_2026-08-19.md`, section 6.4.

**A blind spot to keep in mind.** The same source notes that at extreme loading
the pad can also form mercury arsenide and metallic deposits, which are
**spectrally neutral** — grey-black rather than coloured. Combined with the
definition of contrast absorbance below, this means the instrument **cannot see
them at all**: a spectrally flat absorber is indistinguishable from a shadow and
is removed along with the shading. If the 160 parts-per-billion strip is forming
any such deposit, every feature in this document under-reports it. This is a
reason to be cautious about the top of the range, and an argument for checking
the highest strips against a measurement that does not remove shading.

**Light-emitting diode, abbreviated LED.** The instrument illuminates the strip
with one narrow-band light-emitting diode at a time — violet, royal blue, blue,
cyan, green, amber, red-orange, red, photo-red — plus one broadband white one.
Each gives a different view of the stain, because the two coloured products
absorb differently at different wavelengths.

**Bayer layer.** The camera sensor has a colour filter mosaic in which each
group of four pixels carries one red filter, one blue filter, and two green
filters. The four are separated into four half-resolution images called Bayer
layers, named `R` (red), `Gr` (green on a red row), `Gb` (green on a blue row),
and `B` (blue). The two greens are kept separate rather than averaged, because
they measure measurably differently under red illumination.

**Cell.** One (light-emitting diode, Bayer layer) pair — for example
"royal blue, B". This is the project's unit of spectral measurement. Twenty-seven
cells have a published flat-field correction model; the nine strips discussed
here share **seventeen** of them.

**Exposure rung.** One step on the camera's exposure-time ladder. A capture may
store the same cell at several exposure times so that a later step can choose the
brightest one that does not clip at the sensor's ceiling.

**Region of interest, abbreviated ROI.** A named region of the image: the whole
crop, the left grey chip, the right grey chip, the fitted pad rectangle, or the
strip's own bare paper. Only the fitted pad rectangle matters below.

**Reflectance.** The measured signal divided by a reference brightness. Two
references are used in sequence: first the two grey chips that sit either side of
the strip, then the strip's own bare paper. Reflectance against bare paper is
1.0 on unstained paper by construction.

**Absorbance.** The negative base-ten logarithm of reflectance. Zero on bare
paper, rising as the stain darkens. Its unit is written "A" below. Absorbance is
used rather than the Kubelka–Munk remission function everywhere in the two-dye
work; the Kubelka–Munk quantities exist in the codebase but are not part of this
analysis.

**Shading.** Uneven illumination and shadow. A shadow darkens every wavelength
by the same additive amount in absorbance, so it appears as a component that is
identical in all seventeen cells. It is removed before anything else by
projecting out the direction in which all cells are equal.

**Contrast absorbance.** Absorbance after the shading component has been removed.
This is the unit that the yellow and brown amounts are expressed in. Important
consequence: a **spectrally flat** absorber — something that darkens all
wavelengths equally — is indistinguishable from a shadow in this instrument, so
these amounts measure *colour contrast*, not total optical density.

**Principal component analysis, abbreviated PCA.** A method that finds the
directions along which a cloud of data points varies most. Applied here to the
seventeen-dimensional contrast absorbance of every pad pixel, it answers "how
many independent colours are actually present". The first principal component,
written PC1, is the direction of largest variation; PC2 the second largest;
PC3 the third. Each is reported as the fraction of the total variation it
accounts for, so the three numbers are between 0 and 1.

**Endmember.** The spectrum of one pure dye — one of the two corner colours that
every observed pixel is a mixture of. The two endmembers here are called
`eps_y` (the yellow spectrum) and `eps_b` (the brown spectrum). Each is a list of
seventeen numbers, one per cell, scaled to unit length.

**Wedge angle.** The angle between the two endmember spectra, in degrees. If the
two dyes are genuinely different colours this is a moderate angle. An angle near
zero means the two "different dyes" are nearly the same spectrum; an angle above
about 90 degrees means they point in nearly opposite directions, which no pair of
real absorbing dyes does. **This number is the health check on the whole
separation** and is the pivot of Finding 1 below.

**Unmixing.** Splitting each pixel into a shading amount plus a non-negative
amount of yellow and a non-negative amount of brown, by least squares. The
non-negativity is enforced by clamping: a pixel that would fit best with a
negative amount of one dye is refitted with that dye set to exactly zero. This
produces a visible spike of pixels at exactly zero, which is an artifact of the
clamp and not a measurement.

**Yellow amount and brown amount.** The two numbers unmixing produces per pixel,
written `y` and `b`, in contrast absorbance. These are what `read_duotone`
returns, as two masked images of 130 rows by 183 columns, valid only inside the
fitted pad rectangle — about 2200 valid pixels per strip.

**Total dye and brown fraction.** Two derived per-pixel quantities that the whole
recommendation rests on:

> **Total dye,** written `T`, is `y + b` — how much dye of either kind is at
> this pixel. Unit: contrast absorbance.
>
> **Brown fraction,** written `f`, is `b ÷ (y + b)` — what share of that pixel's
> dye is the brown, tri-substituted product. A pure number between 0 and 1.
> Because brown forms *from* yellow, `f` is a measure of **how far the reaction
> has progressed at that pixel**.

**Dyed pixel.** A pixel whose total dye exceeds a cut-off. The cut-off used is
`T > 0.25` contrast absorbance. Section "Why 0.25" explains why the obvious
noise-based cut-off is wrong.

**Quantile and percentile.** The value below which a stated share of the data
falls. The 50th percentile is the median. `f50` means the median brown fraction
over dyed pixels; `f90` means the value that 90 percent of dyed pixels fall
below.

**Correlation coefficient.** The Pearson correlation coefficient, a number
between −1 and +1 measuring how well two quantities move together in a straight
line. Reported below against the *natural logarithm* of concentration, not
concentration itself, because the underlying physics predicts a logarithmic
response (see Finding 3).

**Coefficient of determination, written R².** The share of the variation in the
target that a fitted model accounts for. 1.0 is a perfect fit, 0.0 is no better
than the average.

---

## Part 2 — What was asked, and what was found

### The question

The routine `cell_PCA` in `desktop/padanalysis/analysis_1.py` produces, for each
strip, a six-panel figure. Its lower-left panel plots every pad pixel as a point
with brown amount horizontally and yellow amount vertically, shaded by how many
pixels fall in each bin. Its lower-middle and lower-right panels are histograms
of yellow amount and of brown amount separately.

The observed behaviour, described from those figures:

- In the two-dimensional panel the points lie along a characteristically curved
  ridge. At 2 and 5 parts per billion the ridge is a straight line from the
  origin slanting upward to the right. From 10 to 80 parts per billion it is
  arched, curving downward. At 160 parts per billion it runs vertically up the
  left edge, then arches over to the right. Pixel counts are highest at the two
  ends of the ridge.
- In the histograms the distributions are either single-peaked or double-peaked.
  At 2 and 5 parts per billion both yellow and brown are double-peaked. At higher
  concentrations yellow shows only its lower peak while brown keeps both.

The request was for ideas to describe these shapes with few parameters, with the
eventual aim — **not part of this work** — of predicting arsenic concentration
from them.

### Finding 1 — most of the shape variation is an artifact of how the fit is run

The routine `run_sampno` calls `cell_PCA` without supplying endmembers, so
**each pad's yellow and brown spectra are fitted using that pad alone.**

That fitting is only well-posed if the pad really does contain two distinguishable
colours. The second principal component holds only 2 to 4 percent of the
variation on every one of these strips, so on a pad that is nearly all one colour
the second endmember is being estimated from noise. Measured, per pad:

| Strip | Concentration, parts per billion | PC1 | PC2 | PC3 | Wedge angle, degrees |
|---|---|---|---|---|---|
| C1022 | 2 | 0.963 | 0.018 | 0.007 | **141.0** |
| C1020 | 5 | 0.949 | 0.032 | 0.008 | **146.3** |
| C1017 | 10 | 0.962 | 0.024 | 0.006 | 37.0 |
| C1018 | 10 | 0.960 | 0.027 | 0.006 | 37.4 |
| C1019 | 10 | 0.956 | 0.031 | 0.006 | 38.3 |
| C1021 | 20 | 0.952 | 0.039 | 0.004 | 44.8 |
| C1023 | 40 | 0.942 | 0.041 | 0.006 | 46.1 |
| C1024 | 80 | 0.939 | 0.045 | 0.005 | 47.7 |
| C1025 | 160 | 0.967 | 0.022 | 0.005 | **22.4** |

A wedge angle of 141 degrees means the two supposed dye spectra point nearly
opposite directions. No pair of real dyes does that. At the other extreme, 22.4
degrees at 160 parts per billion means the two spectra are nearly the same, which
makes the split between them numerically unstable in the opposite way.

**Both of the unusual shapes in the original description are pictures of this
wedge, not of the chemistry:** the straight slanting ray at 2 and 5 parts per
billion, and the vertical wall at brown amount zero at 160 parts per billion.

The consequences are measurable. Using the stored maps as they are:

- The plateau total dye — the 90th percentile of `y + b` — runs **backwards**
  with concentration: 1.56, 1.53, 1.03, 1.03, 0.97 as concentration rises from
  2 to 160 parts per billion.
- Integrated total dye over the pad is not monotone in concentration at all.

The operating manual `desktop/padanalysis/HOWTO.md` already records the
underlying fact, in its list of traps: "One strip alone is rank-1." What was not
previously quantified is how badly the resulting figures mislead.

Fitting one shared pair of endmembers across all nine strips at once gives a
healthy result: seventeen shared cells, PC1 = 0.933, PC2 = 0.050, PC3 = 0.007,
**wedge angle 51.3 degrees**, and a propagated per-pixel noise on total dye of
0.0064 absorbance units.

### Finding 2 — with a shared basis, all nine pads lie on a single curve

Recompute the yellow and brown amounts for all nine strips using the shared
endmembers, then for each strip take its dyed pixels, sort them into ten bins by
brown fraction, and report the median total dye in each bin.

**Median total dye, contrast absorbance, by brown-fraction bin:**

| Brown fraction | 2 | 5 | 10 | 10 | 10 | 20 | 40 | 80 | 160 |
|---|---|---|---|---|---|---|---|---|---|
| 0.1 – 0.2 | 0.42 | 0.51 | 0.37 | 0.40 | 0.40 | 0.34 | 0.34 | 0.38 | 0.32 |
| 0.2 – 0.3 | 0.73 | 0.72 | 0.55 | 0.64 | 0.63 | 0.50 | 0.52 | 0.57 | 0.45 |
| 0.3 – 0.4 | 0.86 | — | 0.77 | 0.84 | 0.85 | 0.72 | 0.72 | 0.70 | 0.57 |
| 0.4 – 0.5 | — | — | 0.93 | 0.97 | 0.98 | 0.91 | 0.90 | 0.88 | 0.57 |
| 0.5 – 0.6 | — | — | 1.03 | 1.06 | 1.05 | 1.03 | 1.03 | 0.94 | 0.83 |
| 0.6 – 0.7 | — | — | 1.11 | 1.12 | 1.07 | 1.08 | 1.05 | 1.02 | 0.96 |
| 0.7 – 0.8 | — | — | 1.12 | — | — | 1.11 | 1.08 | 1.07 | 1.04 |
| 0.8 – 0.9 | — | — | — | — | — | 1.04 | 0.98 | 1.05 | 1.01 |
| 0.9 – 1.0 | — | — | — | — | — | 0.97 | 0.83 | 0.95 | 0.88 |

*(Column headings are concentrations in parts per billion. A dash means fewer
than 25 pixels fell in that bin on that strip.)*

**Read across each row: the nine strips agree to about ±0.05 absorbance units.**
Total dye is a single-valued function of brown fraction, and it is the *same*
function on every strip regardless of concentration. It rises from about 0.3 at
low brown fraction to a crest of about 1.11 near brown fraction 0.75, then eases
off slightly.

That crest is the arch. The "straight ray" seen at 2 and 5 parts per billion is
the low-brown-fraction end of this same curve; the "anti-diagonal" at 160 parts
per billion is its high-brown-fraction end. **The ridge shape is a fixed property
of the chemistry, not information about the sample.**

What *does* vary with concentration is where the pixels sit along the curve:

**Share of dyed pixels in each brown-fraction bin:**

| Brown fraction | 2 | 5 | 10 | 10 | 10 | 20 | 40 | 80 | 160 |
|---|---|---|---|---|---|---|---|---|---|
| 0.0 – 0.2 | 0.32 | 0.61 | 0.18 | 0.26 | 0.25 | 0.10 | 0.09 | 0.11 | 0.02 |
| 0.6 – 0.7 | 0.01 | 0.00 | 0.36 | 0.27 | 0.31 | 0.09 | 0.08 | 0.09 | 0.07 |
| 0.9 – 1.0 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.18 | 0.23 | 0.20 | 0.48 |

At 2 and 5 parts per billion the pixels crowd at the low end. At 10 they pile up
around brown fraction 0.65. Above 20 a growing share reaches full conversion, and
at 160 nearly half of all dyed pixels are past brown fraction 0.9.

**This is why the problem simplifies.** Describing a two-dimensional curved shape
per sample is replaced by describing a one-dimensional distribution: where along
a known, fixed curve this strip's pixels sit, and how many of them there are.

Why pixels differ *within* one strip: arsine arrives at the pad unevenly, so each
point on the pad receives its own local dose and therefore sits at its own point
along the same sequential reaction. The centre of the stain is furthest along;
the diffuse rim is barely started. The two-dimensional cloud is that spatial
gradient plotted in colour space.

### Finding 3 — both findings were predicted by notes already in the project

This is the part worth checking most carefully, because it means the recommended
features are not a curve-fitting accident.

**From `docs/two_dye_pad_analysis_2026-08-19.md`, section 6.4** (an earlier study
of a different set of strips, C1002 to C1014, at 16 to 256 micrograms per litre):
the brown fraction of the whole pad rises monotonically with concentration, while
the mean yellow amount **rises and then falls**, peaking between 64 and 128
micrograms per litre and dropping again at 256. That document names the cause:
"the behaviour of an intermediate species in a sequential reaction: yellow
accumulates, then is consumed as it converts to brown."

That sequential reaction is exactly the curve found in Finding 2. Rise-then-fall
of yellow is the crest at brown fraction 0.75.

**From `tex/gutzeit_analysis.tex`, section "Deposition profile and local
saturation"** (a physical model of how arsine deposits on the pad, worked out
independently of any image data): local saturation begins at about **14 parts per
billion**, and above that — quoting — "the stain does not darken further at its
centre; it widens", with the saturated area growing only **logarithmically** with
the amount of arsenic captured.

Both predictions are confirmed by measurement here:

| Prediction from the physical model | Measurement on these nine strips |
|---|---|
| Above ~14 ppb the centre stops darkening | Plateau total dye is flat from 10 ppb upward: 1.07 → 1.01 across a 16-fold concentration increase |
| Above ~14 ppb the stain widens instead | Dyed area rises steadily: 658 pixels at 10 ppb, 1438 at 160 ppb |
| The widening is logarithmic in dose | Dyed area correlates +0.944 with the *logarithm* of concentration |

The area-based feature is therefore not an empirical accident; it is the response
the physical model says must dominate above roughly 14 parts per billion.

---

## Part 3 — The recommended description: six numbers

All six are computed with the **shared** endmembers, over **dyed** pixels
(total dye above 0.25 contrast absorbance).

| Name | Full definition | Correlation with log concentration |
|---|---|---|
| `f50` | Median brown fraction across dyed pixels. How far the reaction has gone at the typical dyed pixel. | +0.919 |
| `f90` | 90th percentile of brown fraction. How far the reaction has gone in the most-converted part of the stain. | +0.884 |
| `P(f>0.9)` | Share of dyed pixels whose brown fraction exceeds 0.9 — the fully converted core. | +0.892 |
| `n_dyed` | Count of dyed pixels. The stain's area. | **+0.944** |
| `Sum_f` | Sum of brown fraction over dyed pixels. An area-weighted measure of total conversion. | +0.931 |
| `Sum_T` | Sum of total dye over dyed pixels. Integrated dye on the pad. | +0.940 |

The first three are **intensive** — they describe colour and do not grow with the
size of the stain. The last three are **extensive** — they grow with area. That
distinction is what makes the pair necessary.

**Measured values:**

| Strip | ppb | `f50` | `f90` | `P(f>0.9)` | `n_dyed` | `Sum_f` | `Sum_T` |
|---|---|---|---|---|---|---|---|
| C1022 | 2 | 0.247 | 0.309 | 0.000 | 398 | 90.1 | 244.3 |
| C1020 | 5 | 0.171 | 0.251 | 0.000 | 465 | 73.9 | 247.0 |
| C1017 | 10 | 0.502 | 0.685 | 0.000 | 658 | 296.0 | 548.3 |
| C1018 | 10 | 0.411 | 0.665 | 0.000 | 795 | 322.4 | 643.0 |
| C1019 | 10 | 0.412 | 0.677 | 0.000 | 697 | 288.9 | 555.9 |
| C1021 | 20 | 0.631 | 0.920 | 0.177 | 964 | 576.9 | 820.9 |
| C1023 | 40 | 0.773 | 1.000 | 0.327 | 1204 | 812.8 | 990.2 |
| C1024 | 80 | 0.641 | 0.966 | 0.235 | 1000 | 603.7 | 835.1 |
| C1025 | 160 | 0.904 | 0.973 | 0.515 | 1438 | 1127.8 | 1213.7 |

### Three things to read off this table

**1. Repeatability is good.** C1017, C1018 and C1019 are three separate strips
all prepared at 10 parts per billion. Their dyed areas are 658, 795 and 697
pixels; their median brown fractions are 0.502, 0.411 and 0.412. The extensive
features agree to about 6 percent. This is the only estimate of strip-to-strip
reproducibility available in this set, and it is encouraging.

**2. C1024 at 80 parts per billion is an outlier.** It reads low on *every one*
of the six features, sitting between the 20 and 40 parts-per-billion strips
rather than between 40 and 160. Because it is low on all six independent
measures, this is a property of that strip or its preparation — not something a
different choice of feature can repair. It should be investigated or set aside,
not fitted around.

**3. No single number covers the range 2 to 160 parts per billion.** The
intensive features carry the information at the low end and flatten above about
40; the extensive features are weak at the low end and keep climbing to 160. A
usable description needs at least one of each. Taking `f90` together with
`P(f>0.9)` accounts for R² = 0.901 of the variation in the logarithm of
concentration.

> **Caution about that R² figure.** It comes from fitting three parameters to
> nine data points, three of which are replicates of the same concentration, and
> it was evaluated on the same nine points used to fit it. It should be read as
> "these features carry real signal", not as a validated calibration. A genuine
> calibration needs more strips and an honest held-out test.

### Why 0.25 and not the noise floor

The obvious cut-off between "dyed" and "undyed" pixels is a multiple of the
measurement noise. The propagated noise on total dye is 0.0064 absorbance units,
so three times that is 0.019. **This cut-off does not work:** it labels 96 to 98
percent of every pad "dyed", on every strip, making the feature useless.

The reason is that unstained pad is not blank. It sits at a total dye of about
0.09 absorbance units — roughly fourteen times the noise — because the pad
material itself differs slightly from the bare paper used as the white reference.
That is the lower peak in the histograms of the original description.

A cut-off of 0.25 sits above the unstained-pad peak and below the stain, and
separates the two cleanly. The number is empirical and should be re-checked if
the pad stock or the white reference changes.

### Why the histograms are double-peaked

Worth stating plainly, since it drove several of the rejected ideas below. The
fitted pad rectangle is a **rectangle**, and the stain inside it is a **round
blot** that usually does not fill it. So the region measured contains two
populations:

- the **unstained surround** — pad inside the rectangle that the stain never
  reached, at total dye near 0.09. This is the lower peak.
- the **stain plateau** — the saturated core of the blot. This is the upper peak.

Between them is the stain's **diffuse rim**, where the dye fades out gradually.
The rim is a continuous bridge between the two peaks, not an empty valley.

At low concentration the plateau is yellow, so both histograms show two peaks. At
high concentration the plateau has converted to brown, so the yellow histogram's
upper peak disappears while the brown histogram keeps both — exactly as
described in the original observation.

---

## Part 4 — Approaches considered and rejected

Recorded so the choice is not revisited by accident.

**Fitting the ridge curve separately for each sample.** This is the most literal
reading of "characterise the curve": fit a quadratic of yellow amount against
brown amount, or extract the density ridge as radius against angle, or take the
second and third moments of the point cloud. Rejected because Finding 2 shows the
curve is shared across all nine strips. A per-sample curve fit spends its
parameters re-estimating a constant, and whatever variation it appears to show is
mostly just how far along the arc that strip's pixels happen to reach — which the
recommended features measure directly and more robustly.

**Fitting a two-component mixture model to the double-peaked histograms.**
Appealing, because the histograms genuinely are double-peaked. Rejected because
the valley between the peaks is the stain's diffuse rim — a populated bridge, not
a gap — and mixture models fit bridges badly, splitting the rim arbitrarily
between the two components. The useful half of the idea survives: the mixing
weight of the upper component is the stained-area fraction, which is `n_dyed`
measured more simply and more stably.

**Using plateau depth — the maximum or high percentile of total dye — as the
primary feature.** Rejected because it saturates by 10 parts per billion, moving
only from 1.07 to 1.01 while concentration rises sixteen-fold. This is not a
defect of the measurement; `tex/gutzeit_analysis.tex` predicts it, and predicts
the saturation onset at about 14 parts per billion.

**Using three times the propagated noise as the dyed/undyed cut-off.** Rejected
for the reason given under "Why 0.25": unstained pad sits far above sensor noise,
so a noise-based cut-off calls essentially the whole pad dyed.

### One extension worth trying later

The class `PadCube` already carries, for every pixel, its distance inward from
the pad edge, and nothing currently uses it for a profile. Since the high-
concentration signal is the stain *spreading*, fitting brown fraction against
distance from the stain's centre — giving a **front radius**, a **front width**,
and a **plateau value**, three parameters — would model the spreading front
directly and would average over far more pixels than a percentile does. This is
worth trying only after the six simple features are on record, so there is
something to compare it against.

---

## Part 5 — What would be built

Five steps, none of which touch captured data.

1. **A shared-endmember helper** in `analysis_1.py`, cached, listing the nine
   dosed strips C1017 to C1025. It calls two functions that already exist and are
   already used this way in `report14.py` and `look3.py`. Note that the
   concentrations for these nine strips live only in the database — the
   hand-written table in `samples.py` covers C1002 to C1014 and does not know
   about them.

2. **A feature routine** returning the six numbers plus quality-control figures:
   pixel count, model residual, and wedge angle.

3. **Recording the endmembers alongside the stored maps.** At present the stored
   yellow and brown maps carry no record of which endmember pair produced them,
   so a map fitted on one basis cannot be distinguished from one fitted on
   another, nor converted between them. Writing the two spectra and a name for
   the pool into the stored file is the single change that prevents Finding 1
   from happening again.

4. **A step in the run script** that fits the shared basis once and regenerates
   all nine strips against it.
   **This overwrites existing files** — the stored analysis files for C1020 and
   C1022 to C1025, and the nine saved figures — replacing per-strip-basis results
   with shared-basis ones. That is the intent, but it is a destructive rewrite.

5. **One approval figure**: the shared curve with all nine strips' binned medians
   drawn on top of each other, beside the nine brown-fraction histograms on
   common axes. This is what should be looked at, and agreed to by eye, before
   any of the numbers above are trusted.

### How it would be checked

Run the analysis module under a wall-clock limit, then confirm:

1. The shared fit reports seventeen cells, first principal component about 0.933,
   second about 0.050, wedge angle about 51.3 degrees.
2. Each strip's two-dye residual stays close to what the per-strip fit achieved,
   0.009 to 0.020 absorbance units. A large increase would mean the nine strips
   should not share one basis after all.
3. The feature table reproduces the numbers in Part 3.
4. Reading the stored maps back still gives two images of 130 by 183 with 2173
   valid pixels for C1020, now carrying a basis name.
5. The approval figure shows the nine traces lying on top of one another, and the
   brown-fraction histograms marching steadily left to right with concentration.
6. No analysis process is left running afterwards.

---

## Source notes referenced

| Document | What to read it for |
|---|---|
| `desktop/padanalysis/HOWTO.md`, section 8 | How to drive `analysis_1.py`; the note that one strip alone is rank-one |
| `docs/two_dye_pad_analysis_2026-08-19.md` | Sections 5.6 to 5.8 for the two-dye model and its units; 6.1 for dimensionality; **6.4 for yellow rising then falling**; 6.5 for how stable the endmembers are when a pad is withheld |
| `tex/gutzeit_analysis.tex` | **"Deposition profile and local saturation"** for the saturation-then-widening prediction; "Step 3: stain formation" for the two products; "Reflectance photometry" for the photometric quantities |
| `docs/gutzeit_protocol.md` | The test protocol as actually performed |
