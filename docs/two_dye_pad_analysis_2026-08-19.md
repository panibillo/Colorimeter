# Two-dye separation on Gutzeit pads

**Analysis date:** 2026-08-19
**Archives analysed:** C1002, C1003, C1004, C1005, C1008, C1009, C1014
**Code:** `desktop/padanalysis/duotone.py`, `look3.py`, `report14.py`
**Figures:** `desktop/padanalysis/fig/look3.png`, `fig/report14_ranking.png`

---

## 1. Purpose and scope

### 1.1 The question addressed

The reacted pad is not one colour at two strengths. The Gutzeit reaction adds
arsine to mercuric bromide in steps, and the mono-substituted product is
yellow while the tri-substituted product is brown. A pad photographed under a
single illuminant and read in a single Bayer layer returns one number per
pixel, and one number cannot distinguish a large quantity of the yellow
product from a small quantity of the brown product.

This report asks: **which combinations of illuminant and Bayer layer permit
the yellow product and the brown product to be measured separately, per
pixel, and how much does each additional combination contribute?**

### 1.2 What this report does not attempt

This report does **not** derive a dose-response relationship between
concentration and colour. The seven archives available were captured under
two different pad-exposure geometries, one of them (C1014) from an
interrupted run whose reagent timing is not trustworthy, and the concentration
range is sampled at seven points with two replicate pairs. Concentration
appears in this report only as an ordering variable, used to test whether the
two extracted colour components behave as the chemistry predicts they should.

The seven archives are treated here as **a library of colour samples**: a set
of pads exhibiting a range of yellow-to-brown mixtures, from which the two
component spectra can be estimated and the illuminant choice can be decided
before new data are collected.

### 1.3 The decision this report supports

Two capture decisions follow from the analysis, and Section 11 states both
explicitly:

1. Which illuminants the **pre-image capture** (the unreacted strip,
   photographed before reagent contact, stored under `/pre` in the archive)
   must include.
2. Which illuminant-and-layer combinations the **post-image analysis** (the
   measurement of the reacted pad) will use.

---

## 2. Definitions

Terms are used in this report with the following meanings, and with no other
meanings.

| Term | Definition |
|---|---|
| **Archive** | One HDF5 file, holding one strip photographed under every illuminant. Named by its sample number, for example `C1014`. |
| **Illuminant** | One of the ten LED channels: violet 410 nm, royal_blue 455 nm, blue 475 nm, cyan 500 nm, green 525 nm, amber 590 nm, red_orange 615 nm, red 625 nm, photo_red 660 nm, and a broadband white. Wavelengths are the nominal peaks recorded in each archive's LED metadata. |
| **Bayer layer** | One of the four colour planes the sensor's colour filter array produces: `R`, `Gr`, `Gb`, `B`. `Gr` and `Gb` are the two green-filtered positions of the Bayer quad, and are kept separate throughout because they have been measured to differ (see `docs/flatfield_calibration.md`). |
| **Cell** | **One (illuminant, Bayer layer) pair** — for example `violet.B`, meaning the strip lit by the 410 nm LED and read in the blue-filtered Bayer layer. A cell is the elementary photometric measurement of this instrument. Ten illuminants times four layers gives forty conceivable cells; twenty-seven of those forty carry a published flat-field model and are the ones used here (Section 3.3). |
| **Exposure rung** | One of the exposures in the geometric ladder captured for each illuminant, labelled `e0`, `e1`, `e2`, with each rung 1.5 times the previous exposure time. |
| **Layer pixel** | One pixel of a Bayer layer. A Bayer layer has half the linear resolution of the sensor, so one layer pixel spans approximately 0.15 to 0.19 mm at the pad. |
| **Bound crop** | The rectangular region of the sensor stored in the archive, 130 by 183 layer pixels for the region setting used by all seven archives. |
| **Pad rectangle** | The reacted pad's outline, fitted geometrically by `padrect.PadRect` from the strip's edges and the pad's own shadow line, nominally 41.35 by 53.34 layer pixels. |
| **Bare paper** | The unreacted backing strip below the pad, in the same frame under the same illuminant. Used as the photometric reference. |
| **Reflectance** | Signal in a cell divided by the level of the two grey reference chips in the same frame, each chip weighted through that cell's published illumination-field model. Written *r*. |
| **Absorbance** | *A* = −log₁₀(*r*<sub>pixel</sub> / *r*<sub>bare paper</sub>), base-10, per pixel per cell. Bare paper sits at *A* = 0 by construction. Absorbance is used because a dye's contribution is additive in absorbance and multiplicative in reflectance. |
| **Shading term** | A quantity added equally to every cell of one pixel, written *g*. Shading is multiplicative in reflectance and therefore a constant offset in absorbance. |
| **Contrast space** | Absorbance with the shading direction removed: the component of a pixel's 26-cell absorbance vector orthogonal to the all-ones vector. This is the part of a pixel's spectrum that no change of illumination can explain. |
| **Endmember** | The absorbance spectrum of one pure product, expressed as a vector with one component per cell, normalised to unit length. Written **ε**<sub>Y</sub> for yellow and **ε**<sub>B</sub> for brown. |
| **Amount** | The quantity of one product at one pixel, written *y* for yellow and *b* for brown. Amounts are constrained to be non-negative. Their unit is defined in Section 5.6. |
| **Total amount** | *t* = *y* + *b*, the quantity of coloured product of either kind at a pixel. |
| **Brown fraction** | *h* = *b* / (*y* + *b*), the share of the coloured product at a pixel that is the brown product. Defined only where *t* exceeds the detection floor. |
| **Noise sigma** | The standard deviation of absorbance at one pixel in one cell, written σ, measured on bare paper (Section 5.5). Its unit is absorbance. |

Measures defined for the ranking — *J*<sub>hue</sub>, *J*<sub>Y</sub>,
*J*<sub>B</sub>, efficiency, agreement, brown-area error and hue-axis
rotation — are defined where they are first used, in Sections 6.5 and 7.

---

## 3. Input data

### 3.1 The seven archives

| Archive | Nominal concentration (µg/L) | Stage | Pad-exposure geometry | Description recorded before analysis |
|---|---|---|---|---|
| C1002 | 256 | olive | full-face | brown overall, some yellow in the middle |
| C1003 | 128 | olive | full-face | brown at the edges, bleeding into yellow in the middle |
| C1004 | 64 | olive | full-face | brown at the edges, yellow in the centre |
| C1005 | 64 | olive | full-face | brown at the edges, yellow in the centre |
| C1008 | 16 | black | full-face | mostly yellow |
| C1009 | 16 | black | full-face | mostly yellow |
| C1014 | 10 (run interrupted) | black | hole | brown in the middle, yellow annulus, white elsewhere |

The concentration column is the nominal concentration of the prepared
standard, as recorded in `desktop/padanalysis/samples.py`. C1014's reagent-1
timing is not true because the run was interrupted, so C1014's concentration
is not comparable with the other six and is used in this report only for
ordering illustration.

The "description recorded before analysis" column is the operator's written
description of each pad, supplied before the analysis was run. That column is
used in Section 6.3 as an independent check and was not available to any
fitting procedure.

### 3.2 Contents of each archive

All seven archives contain all ten illuminants at all three exposure rungs.
Each (illuminant, rung) group stores up to four Bayer layers as 130 by 183
arrays of 10-bit values, together with a matching dark frame. A layer that
the capture software judged to hold too little signal is stored as an empty
placeholder and is skipped by every tool.

C1014 additionally holds a pre-image under `/pre`: the same strip photographed
before reagent contact, with two illuminants (royal_blue and red) at one
exposure rung. C1014 is the only archive of the seven with a pre-image inside
the same file.

### 3.3 The cell universe

Of the forty conceivable (illuminant, Bayer layer) combinations, **twenty-seven
carry a published flat-field model** in the `layermodels` table of
`src/colorimeter.sqlite`. The twenty-seven are exactly the combinations in
which an illuminant meaningfully excites a layer:

| Illuminant | Wavelength (nm) | Layers with a published model |
|---|---|---|
| violet | 410 | B |
| royal_blue | 455 | B |
| blue | 475 | B, Gb, Gr |
| cyan | 500 | B, Gb, Gr |
| green | 525 | B, Gb, Gr |
| amber | 590 | Gb, Gr, R |
| red_orange | 615 | Gb, Gr, R |
| red | 625 | Gb, Gr, R |
| photo_red | 660 | Gb, Gr, R |
| white | broadband | B, Gb, Gr, R |

One of those twenty-seven, `white.Gr`, is unusable on C1009 because it reaches
the sensor's saturation ceiling at every exposure rung within the measured
region. The analysis therefore proceeds on the **twenty-six cells common to
all seven archives**. Pooled over the seven pads, those twenty-six cells are
measured at **15 371 pad pixels**.

### 3.4 Geometry change beginning at C1014

Beginning with C1014 the pad sits farther from the end of the backing strip
than in the earlier archives, and the stored region of the sensor was moved
upward. Both changes were reported by the operator and both are confirmed by
the fitted geometry: the distance from the strip's end to the top of the pad
rectangle is 34.7 layer pixels on C1014 against 0.6 to 8.6 layer pixels on the
fifteen archives that precede C1014.

This change broke the pad finder, and the failure and its correction are
described in Section 6.7.

---

## 4. Tools

| Tool | Role in this analysis | Status |
|---|---|---|
| `desktop/padanalysis/probe.py` | Opens an archive; returns dark-subtracted Bayer layers and the region geometry in bound-crop coordinates. | pre-existing |
| `desktop/padanalysis/padrect.py` | Fits the pad rectangle from the strip's edges and the pad's shadow line; supplies the per-pixel distance inside the pad boundary and the bare-paper region. | pre-existing, corrected here (Section 6.7) |
| `desktop/padanalysis/whitecorr.py` | Rebuilds each cell's published illumination-field model and re-derives the two grey-chip weights for the region actually measured. | pre-existing |
| `desktop/padanalysis/measure.py`, `chemistry.py` | Per-pad scalar reduction used by the earlier reports 9 to 13. Not used by this analysis, which works per pixel, but its reflectance definition is followed exactly. | pre-existing |
| `desktop/padanalysis/duotone.py` | **New.** Builds the per-pixel absorbance matrix, measures the noise, fits the two endmember spectra, unmixes each pixel into shading, yellow and brown, classifies pixels, and computes the ranking measures. | written for this analysis |
| `desktop/padanalysis/look3.py` | **New.** Draws each pad in true colour beside the model's classification, for visual checking. | written for this analysis |
| `desktop/padanalysis/report14.py` | **New.** Prints the ranking and its supporting checks; draws `fig/report14_ranking.png`. | written for this analysis |

All three new tools are read-only with respect to the archives and the
database. The only files produced are the two figures.

---

## 5. Method

### 5.1 Selection of pixels

For each archive, `padrect.PadRect` fits the pad rectangle. Every pixel inside
that rectangle is used, with no erosion of the pad's edge. Erosion is not
applied because `report4.py` established that the dye-sensitive cells become
*less* reproducible as the pad edge is eroded away, which indicates that the
edge carries genuine chemical information rather than only an artefact.

The consequence of including the edge is that a photometric shadow at the pad
boundary enters the data. That shadow is handled by the model rather than by
erosion, as Section 5.6 describes. Pixel counts at three erosion depths are
reported in Section 10.3 so that the effect of the choice is visible.

### 5.2 Selection of exposure rung

For each archive and each cell independently, the exposure rung used is the
**longest exposure that does not saturate and contains no dead pixels** within
the union of the pad rectangle, the bare-paper region and the two grey chips.
Saturation is defined as a stored value of 1000 or more out of the 10-bit
ceiling of 1023, a margin of 23 counts adopted from the existing tools. The
dead-pixel test is new and its reason is given in Section 6.7.

The rung chosen turns out to be **the same for all seven archives in every
cell examined**, which means the rung selection is a property of the
instrument and the illuminant, not of the pad's colour. The rungs chosen are
listed in Section 11.3, where the exposure recommendation is made.

### 5.3 Reflectance

Reflectance at a pixel in a cell is

```
r = signal / ( w1 · mean(chip1) + w2 · mean(chip2) )
```

where `signal` is the dark-subtracted value at that pixel, `chip1` and `chip2`
are the two grey reference chips in the same frame, and `w1` and `w2` are that
cell's published anchor weights re-derived for the region being measured by
`whitecorr.anchor_weights`. This is the definition `measure.py` already uses
for pad averages, applied here to each pixel individually rather than to a
region mean.

### 5.4 Absorbance

Absorbance at a pixel in a cell is

```
A = −log10( r_pixel / r_bare_paper )
```

where `r_bare_paper` is the median reflectance of the strip's own bare paper,
in the same frame, under the same illuminant, in the same cell. Referring each
pad to the paper of its own strip removes session-to-session changes in the
lamp and the camera, and it places bare paper at *A* = 0 by construction.

Note carefully what this zero is and is not: it is **bare backing paper**, not
an unstained pad. The pad's own material sits above that zero. Section 11.1
quantifies the difference, which is central to the pre-image recommendation.

### 5.5 Noise

Per-pixel noise is measured, not assumed. For each archive and each cell, the
differences between horizontally adjacent bare-paper pixels are formed, and
the noise sigma is taken as

```
σ = 1.4826 · median_absolute_deviation( differences ) / √2
```

Differencing adjacent pixels is used rather than the plain standard deviation
of the paper region because the paper carries a real illumination gradient,
and a plain standard deviation would report that gradient as noise. The
division by √2 converts the spread of a difference of two pixels back to the
spread of one pixel. The median absolute deviation is used rather than the
standard deviation so that a speck of dust on the paper does not inflate the
estimate.

The measured noise is **0.005 to 0.015 absorbance per pixel per cell**, with a
median across the twenty-six cells of about 0.006 absorbance. The noisiest
cells are the green-filtered layers of the long-wavelength illuminants
(`photo_red.Gb` at 0.0147, `red.Gb` at 0.0128), which are cells where the
illuminant barely excites the layer.

A full 26-by-26 noise **covariance** is also formed, from the same adjacent-pixel
differences, with the robust sigmas above on its diagonal and the measured
correlations off the diagonal. The correlations matter and are not a
refinement: most pixel-to-pixel variation on paper is achromatic, because a
paper fibre standing proud dims every band together, so most of the noise lies
along the same direction as shading and is removed along with shading. A
ranking computed against a diagonal covariance would not see that
cancellation.

### 5.6 The model

At each pad pixel, the 26-cell absorbance vector **A** is modelled as

```
A = g · 1  +  y · ε_Y  +  b · ε_B  +  residual
```

with

- **1** the all-ones vector over the twenty-six cells,
- *g* the shading term, unconstrained in sign,
- *y* ≥ 0 the yellow amount and *b* ≥ 0 the brown amount,
- **ε**<sub>Y</sub> and **ε**<sub>B</sub> the two endmember spectra, each a
  unit-length vector over the twenty-six cells, shared by every pixel of every
  archive.

**Why the shading term is present.** A shadow multiplies the light reaching a
pixel by the same factor in every band, which in absorbance is the same
additive offset in every cell. The pad's rim is shadowed: `report3.py`
measured the strip's own unstained paper falling off by 11 % to 24 % of its
own level within 2 to 4 layer pixels of the strip's edge. Fitting **1** as a
component absorbs that offset before the dye amounts are read. This is the
same cancellation that dividing one cell by another achieves, generalised from
two cells to twenty-six.

**What the shading term costs.** A spectrally flat absorber is
indistinguishable from a change of illumination in this instrument. The
amounts *y* and *b* therefore measure **contrast**, not total optical density.
For deciding whether a pixel is white, yellow or brown, and for counting how
many pixels are which, contrast is the appropriate quantity. For an absolute
optical density, it is not.

**Unit of the amounts.** Because **ε**<sub>Y</sub> and **ε**<sub>B</sub> are
unit-length vectors over the twenty-six cells, an amount of 1.0 means a pixel
whose contrast-space absorbance vector has length 1.0 in the 26-cell space.
Converting to a single familiar quantity: a yellow amount of *y* contributes
0.560 × *y* absorbance in `violet.B` and 0.116 × *y* in `cyan.B`; a brown
amount of *b* contributes 0.242 × *b* in `violet.B` and 0.217 × *b* in
`cyan.B`. Observed total amounts across the seven pads run from about 0.03 at
the detection floor to about 1.5 on the most heavily loaded pad.

### 5.7 Extraction of the two endmember spectra

The endmember spectra are **not** taken from the literature and are **not**
supplied by the operator. Both are estimated from the pixels themselves, as
follows.

1. The shading direction is projected out of every pixel's absorbance vector,
   leaving the contrast-space vectors.
2. All 15 371 pad pixels of all seven archives are pooled and decomposed by
   singular value decomposition. The two leading directions define a plane.
3. Within that plane, each pixel is described by an **angle**. If exactly two
   products are present in non-negative amounts, every pixel's contrast
   spectrum is a non-negative combination of the two product spectra, so the
   pixel cloud occupies a wedge and the two products sit at the wedge's two
   edges. The endmembers are therefore read off as the extreme *angles*, taken
   at the 2nd and 98th percentiles of the angle distribution among the
   dye-bearing half of the pixels, rather than at the exact extremes, so that
   one anomalous pixel cannot define a product spectrum.
4. Which of the two extracted spectra is yellow and which is brown is decided
   by wavelength, not by appearance: the spectrum with the greater share of
   its absorbance above 500 nm is labelled brown, because the tri-substituted
   product is the one that continues absorbing past the yellow product's
   absorption edge.

### 5.8 Unmixing and classification

Every pixel is unmixed by solving, in closed form,

```
minimise ‖ A − g·1 − y·ε_Y − b·ε_B ‖²   subject to y ≥ 0 and b ≥ 0
```

with *g* free. Projecting the shading direction out of both the data and the
endmembers reduces the problem to a two-variable non-negative least squares,
which for two variables has three candidate solutions and is solved for all
pixels simultaneously without iteration.

Pixels are then labelled:

- **white** where the total amount *t* = *y* + *b* is below a detection floor,
- **yellow** where *t* is above the floor and the brown fraction *h* < 0.5,
- **brown** where *t* is above the floor and *h* ≥ 0.5.

The detection floor is **not chosen by eye**. It is three times the standard
deviation of *t* obtained by propagating the measured noise covariance through
the same fit, which for the 26-cell fit is 0.0076 to 0.0086 absorbance
depending on the archive, giving a floor of 0.023 to 0.026 absorbance.

The cut at *h* = 0.5 **is** a convention, and it is the only arbitrary number
in the classification. Yellow and brown form a continuum, so the boundary
between the two labels is a choice of where to cut, not a discovery. The
continuous brown fraction is reported alongside the labels for that reason,
and the pixel counts are reported at several cut values in Section 10.3.

---

## 6. Verification that the two-component description is not an artefact

Any decomposition returns *some* pair of components. Six independent checks
were applied to establish that the two components extracted here correspond to
two chemical products.

### 6.1 Dimensionality of the contrast space

After the shading direction is removed, the variance of the pooled pixel cloud
distributes across its principal directions as follows. Each figure is that
direction's share of the total variance of the pooled contrast-space pixel
cloud; the divisor is the sum over all twenty-five available directions.

| Direction | Share of total contrast variance |
|---|---|
| 1st | 0.9503 |
| 2nd | 0.0380 |
| 3rd | 0.0032 |
| 4th | 0.0029 |

Two directions account for **98.83 %** of the contrast variance and the third
accounts for **0.32 %**. A two-component description is therefore not an
imposition on the data; the data are two-dimensional once shading is removed.

### 6.2 Residual against measured noise

The root-mean-square residual left by the two-component fit, compared with the
median per-pixel noise of the same archive:

| Archive | Residual (absorbance) | Median noise (absorbance) | Ratio of residual to noise |
|---|---|---|---|
| C1002 | 0.0239 | 0.0062 | 3.9 |
| C1003 | 0.0197 | 0.0065 | 3.1 |
| C1004 | 0.0199 | 0.0060 | 3.3 |
| C1005 | 0.0189 | 0.0061 | 3.1 |
| C1008 | 0.0226 | 0.0068 | 3.3 |
| C1009 | 0.0349 | 0.0066 | 5.3 |
| C1014 | 0.0222 | 0.0064 | 3.5 |

The residual exceeds the photometric noise by a factor of 3 to 5. Two
components therefore do **not** describe the pads to the limit of the
instrument's precision. What the excess represents is not established by these
data; the candidates are the pad's own texture (which `report12.py` measured
as a granularity proportional to the dye loading), a third minor absorbing
species, and departure from additivity at high loading. The excess is
nevertheless small in absolute terms — 0.02 to 0.035 absorbance against dye
signals of 0.3 to 1.5 absorbance — so the two-component description accounts
for the great majority of the colour variation while not exhausting it.

### 6.3 Agreement with the operator's written descriptions

The classification was compared with the seven descriptions recorded before
the analysis (Section 3.1). Pixel counts, at zero erosion of the pad edge:

| Archive | White pixels | Yellow pixels | Brown pixels | Description recorded beforehand |
|---|---|---|---|---|
| C1002 | 0 | 7 | 2037 | brown overall |
| C1003 | 0 | 953 | 1265 | brown edges, yellow middle |
| C1004 | 0 | 1985 | 241 | brown edges, yellow centre |
| C1005 | 1 | 1774 | 507 | brown edges, yellow centre |
| C1008 | 0 | 2147 | 0 | mostly yellow |
| C1009 | 0 | 2214 | 0 | mostly yellow |
| C1014 | 72 | 1766 | 402 | brown middle, yellow annulus, white elsewhere |

Every description is reproduced, including the two archives with zero brown
pixels out of 2147 and 2214 respectively, and including C1014 as the only
archive of the seven containing white pixels. The spatial arrangement is also
reproduced: `fig/look3.png` shows the brown at the rim on C1003, C1004 and
C1005 and in the centre on C1014.

### 6.4 Ordering against concentration

If the two components are the mono-substituted and tri-substituted products,
the brown share must increase with the quantity of arsine delivered. The brown
fraction of the whole pad, defined as the sum of *b* over all pad pixels
divided by the sum of (*y* + *b*) over the same pixels:

| Archive | Nominal concentration (µg/L) | Mean yellow amount | Mean brown amount | Pad brown fraction |
|---|---|---|---|---|
| C1008 | 16 | 0.2719 | 0.0010 | 0.004 |
| C1009 | 16 | 0.4933 | 0.0015 | 0.003 |
| C1004 | 64 | 0.8194 | 0.1877 | 0.186 |
| C1005 | 64 | 0.7594 | 0.3542 | 0.318 |
| C1003 | 128 | 0.5464 | 0.6864 | 0.557 |
| C1002 | 256 | 0.2169 | 1.1315 | 0.839 |

The brown fraction increases monotonically with concentration across the six
archives whose runs were not interrupted. The yellow amount **rises and then
falls**, peaking between 64 and 128 µg/L and dropping to 0.2169 at 256 µg/L,
which is the behaviour of an intermediate species in a sequential reaction:
yellow accumulates, then is consumed as it converts to brown.

This ordering is independent evidence, because concentration was not used
anywhere in the extraction of the endmembers.

### 6.5 Stability when a pad is withheld

The endmember spectra and the noise covariance were refitted seven times, each
time omitting one archive, and the recommended cell set was re-scored in each
fold.

Two quantities are reported. **Efficiency** is defined in Section 7.4.
**Hue-axis rotation** is the angle between the difference vector
(**ε**<sub>B</sub> − **ε**<sub>Y</sub>) obtained from the six retained archives
and the same difference vector obtained from all seven. Both vectors live in
the 26-dimensional cell space. An angle of 0 degrees means the direction that
separates brown from yellow is identical; 90 degrees would mean the two
estimates are unrelated. The angle is therefore a direct measure of how much
the *meaning* of "brown rather than yellow" changes when one pad is removed
from the estimation.

| Archive withheld | Efficiency of the recommended set in that fold | Hue-axis rotation (degrees) |
|---|---|---|
| C1002 | 0.818 | 9.86 |
| C1003 | 0.811 | 2.59 |
| C1004 | 0.804 | 0.85 |
| C1005 | 0.823 | 2.72 |
| C1008 | 0.769 | 2.43 |
| C1009 | 0.759 | 4.79 |
| C1014 | 0.802 | 1.12 |
| *all seven retained* | *0.802* | *0 by definition* |

The largest rotation, 9.86 degrees, occurs when C1002 is withheld, which is
expected: C1002 at 256 µg/L is the only archive whose pad is nearly pure brown
and therefore the only one that constrains the brown end of the wedge. Even
so, 9.86 degrees out of the 90 degrees that would indicate an unrelated
direction is a modest movement, and the recommended cell set retains an
efficiency between 0.759 and 0.823 in every fold against 0.802 in-sample.

### 6.6 Whether the violet band is measuring absorption

The 410 nm cell `violet.B` proves to be the single most informative cell
(Section 8). That makes one known instrument property worth testing: white
card stock in this instrument fluoresces under 410 nm excitation, so a violet
measurement could in principle be recording emitted light rather than
absorption.

Emission and absorption are distinguishable by their behaviour with dye
loading. Emitted light is added by the substrate and cannot be removed by the
dye, so contamination by emission would hold the apparent absorbance **down**
on exactly the darkest pixels, where absorbance is largest. The test is
therefore to follow the ratio of `violet.B` absorbance to `royal_blue.B`
absorbance as the pad darkens. `royal_blue.B` at 455 nm is the nearest cell
that does not excite the substrate's brighteners.

Pixels from all seven archives were pooled and split into ten equal groups by
`royal_blue.B` absorbance:

| Group (by increasing dye loading) | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Mean `royal_blue.B` absorbance | 0.05 | 0.11 | 0.20 | 0.33 | 0.48 | 0.56 | 0.60 | 0.64 | 0.70 | 0.82 |
| Ratio `violet.B` / `royal_blue.B` | 1.94 | 1.92 | 1.74 | 1.46 | 1.30 | 1.18 | 1.11 | 1.06 | 1.04 | 1.06 |

The two-component model predicts this ratio should run from
ε<sub>Y</sub>(violet.B) / ε<sub>Y</sub>(royal_blue.B) = 0.560 / 0.398 = **1.41**
for a purely yellow pixel down to
ε<sub>B</sub>(violet.B) / ε<sub>B</sub>(royal_blue.B) = 0.242 / 0.279 = **0.87**
for a purely brown one. The observed ratio does fall in that direction and by
approximately that amount as the pads darken, which is the opposite of the
signature emission would produce.

The observed ratio at the lightest group, 1.94, exceeds the pure-yellow
prediction of 1.41. That excess is attributed to the pad's own violet
absorbance, because the zero of this analysis is bare paper rather than an
unstained pad (Section 5.4). The excess biases the absolute yellow amount; it
does not bias the yellow-versus-brown decision, which depends on the
difference between the two endmembers rather than on their common offset.

**Conclusion of the test:** `violet.B` is behaving as an absorption
measurement in these archives. This conclusion rests on the shape of the
loading dependence, not on a direct measurement of pad fluorescence, and a
direct measurement would be stronger.

### 6.7 Defects found and corrected during the analysis

Two defects were found. Both affected results and both are now corrected.

**Defect 1 — the pad finder mis-placed the pad rectangle on C1014.**
`padrect.py` located the pad's lower edge by searching a window 40 to 78 rows
below the end of the backing strip. That window encodes an assumption that the
pad sits a fixed distance from the strip's end. The geometry change described
in Section 3.4 violated that assumption: on C1014 the pad's true lower edge
lies 87 rows below the strip's end, outside the window, so the search returned
the strongest gradient it could find inside the window, which was the flank of
the stain itself. The resulting rectangle contained roughly 16 rows of bare
backing strip and omitted the lower part of the pad.

The correction uses the fact that the pad's **height** did not change even
though the pad's position did. When the independently observed top edge
disagrees with the fitted top edge by more than 3 layer pixels, the rectangle
is re-seated on the gradient one pad-height below the observed top edge. The
threshold of 3 layer pixels is an order of magnitude above the largest
disagreement seen on the fifteen archives that predate C1014, whose
disagreements range from −0.64 to +0.61 layer pixels, so the correction cannot
be triggered by them. All fifteen earlier archives fit identically to within
0.01 layer pixels after the correction; C1014 and its pre-image are the only
archives whose geometry changes. Each fitted rectangle now records which rule
placed it, in an attribute named `bot_source`.

**Defect 2 — 38 dead pixels in one stored frame.** In C1002, the `white`
illuminant at rung `e2` stores 38 pixels with a value of exactly zero in the
`R` layer and 38 in the `Gr` layer. An illuminated sensor does not read zero,
and a zero survives every screen the existing tools apply: after dark
subtraction such a pixel is merely dim, and after the logarithm it becomes an
enormous absorbance. A scan of all seventeen archives, all illuminants, all
rungs and all layers found no other occurrence.

Those 38 pixels were inflating C1002's two-component residual from 0.0239 to
0.0639 absorbance, which is to say they were making the most heavily loaded
pad appear to be the worst-described pad. The exposure-rung screen now rejects
any frame containing a zero-valued pixel within the measured region. Whether
the capture path can drop pixels is a question this report cannot answer and
is worth investigating separately.

---

## 7. Measures and criteria used for the ranking

### 7.1 What is being ranked

A **cell set** is a set of *k* cells, for example {`violet.B`, `cyan.B`,
`photo_red.R`}. Every set of size 2, 3 and 4 drawn from the twenty-six shared
cells was scored exhaustively: 325 sets of size 2, 2600 of size 3 and 14 950 of
size 4. No search heuristic was used and no set was excluded in advance.

Sets are reported after collapsing by **illuminant set**: two cells taken from
the same illuminant are two readings of one illumination, and without
collapsing, the top of the list would be one answer repeated across Bayer
layers. The number of distinct illuminants a set requires is reported, because
that number is what the set costs at capture time.

### 7.2 *J*<sub>hue</sub> — the measure of yellow-versus-brown discrimination

For a cell set *S*, consider all linear combinations **w** of the cells in *S*
that are immune to shading, meaning **w**·**1** = 0. Among those, take the
combination that best separates the two products. The value achieved is

```
J_hue(S)² = d' C⁻¹ d − (1' C⁻¹ d)² / (1' C⁻¹ 1)
```

where **d** = (**ε**<sub>B</sub> − **ε**<sub>Y</sub>) restricted to *S*, **C**
is the measured noise covariance restricted to *S*, and **1** is the all-ones
vector over *S*.

**Interpretation:** *J*<sub>hue</sub> is the number of noise standard
deviations separating a pure-brown pixel from a pure-yellow pixel **of the
same total amount 1.0**, using the best shading-immune combination of the cells
in the set. Because observed total amounts run from about 0.03 to about 1.5, a
set with *J*<sub>hue</sub> = 100 separates the two products by 100 × 0.03 = 3
standard deviations at the faintest detectable stain and by 100 × 1.5 = 150
standard deviations at the darkest.

The subtracted second term is the price of shading immunity. At *k* = 1 the two
terms are identical and *J*<sub>hue</sub> is exactly zero.

### 7.3 *J*<sub>Y</sub> and *J*<sub>B</sub> — the measures of separate quantification

*J*<sub>hue</sub> asks which product dominates. A different question is how
precisely the quantity of each product is determined. Form the information
matrix for (*y*, *b*) with the shading term profiled out,

```
M(S) = E' C⁻¹ E − (E' C⁻¹ 1)(1' C⁻¹ E) / (1' C⁻¹ 1),    E = [ε_Y , ε_B] on S
```

and define *J*<sub>Y</sub> = 1 / √(M⁻¹)₁₁ and *J*<sub>B</sub> = 1 / √(M⁻¹)₂₂.

**Interpretation:** *J*<sub>Y</sub> is the reciprocal of the standard error on
the yellow amount, after the brown amount **and** the shading have both been
allowed to take any value they like. *J*<sub>B</sub> is the same for the brown
amount. A large value means the amount is pinned down; a small value means the
set can see that a product is present but cannot say how much.

Removing the shading direction from *k* cells leaves *k* − 1 dimensions, and
two amounts require two dimensions. Consequently **M**(*S*) is singular for
*k* = 2, and both *J*<sub>Y</sub> and *J*<sub>B</sub> are exactly zero there.

### 7.4 Efficiency

**Efficiency** = *J*<sub>hue</sub>(*S*) / *J*<sub>hue</sub>(all 26 cells). The
divisor is the value obtained using every cell together, which is 128.3.
Efficiency is therefore the fraction of the discrimination available from the
complete instrument that a particular small set retains. An efficiency of
0.802 means the set retains 80.2 % of the full instrument's discriminating
power, the divisor being the full instrument's *J*<sub>hue</sub>.

### 7.5 Agreement

*J*<sub>hue</sub>, *J*<sub>Y</sub> and *J*<sub>B</sub> are properties of the
linear algebra. **Agreement** is a measurement made on the pads themselves.

Each cell set is used, on its own, to unmix and classify every pad pixel of all
seven archives into white, yellow or brown. Those labels are compared with the
labels produced by the full 26-cell fit. **Agreement** is the number of pixels
receiving the same label from the set as from the full fit, divided by the
total number of pad pixels, which is 15 371. An agreement of 94.5 % therefore
means 14 526 of 15 371 pad pixels received the same label from three cells as
from all twenty-six.

Two details make the comparison fair. First, each set is judged against **its
own** detection floor, computed by propagating the noise through that set's own
fit, rather than against the floor of the 26-cell fit: a smaller set is
inherently noisier, and holding it to a threshold it cannot meet would penalise
it for a question it was not asked. Second, sets of two cells are scored with
the shading term switched off, because that is the only circumstance in which
two cells can report an amount at all; every set of three or more retains full
shading immunity.

A second agreement figure, **hue-only agreement**, restricts the comparison to
pixels the full fit found to contain dye, and therefore measures the
yellow-versus-brown decision without the white-versus-dyed decision on top of
it. The divisor is the number of pixels the full fit labelled yellow or brown.

### 7.6 Brown-area error

**Brown-area error** = | *N*<sub>set</sub> − *N*<sub>full</sub> | /
*N*<sub>full</sub>, where *N*<sub>set</sub> is the number of pixels the cell set
labels brown, summed over all seven archives, and *N*<sub>full</sub> is the
number the full 26-cell fit labels brown, summed over the same pixels. The
divisor is *N*<sub>full</sub>.

This measure is included because counting pixels to obtain a stained area is
the intended downstream use, and a set can label most pixels correctly while
still misestimating an area if its errors are one-sided.

---

## 8. Results — the ranking

### 8.1 Three structural results

Three results follow from the model and the geometry of the problem rather
than from these particular pads.

**One cell separates nothing at all.** Every one of the twenty-six cells scores
*J*<sub>hue</sub> = 0.000000 exactly. This is not a statement that one cell
performs poorly. With a single cell, the shading direction and the dye
direction are the same direction, so no combination can distinguish a large
quantity of yellow from a small quantity of brown. No improvement in
signal-to-noise, exposure or averaging changes this.

**Two cells determine the hue and nothing else.** A pair yields a
shading-immune index and therefore reports which product dominates, with
efficiencies up to 0.799. But *J*<sub>Y</sub> = *J*<sub>B</sub> = 0 for every
pair, so a pair cannot state how much of either product is present. A
consequence that matters practically: **a pair cannot distinguish an unstained
pixel from a stained one**, because that decision requires an amount. Any
two-cell scheme must obtain the white-versus-stained decision from elsewhere,
or assume that the illumination is known and flat.

**Three cells determine both amounts while remaining immune to shading.** The
third cell is what converts a hue indicator into a measurement. In the results
below, adding a third cell drawn from the long-wavelength illuminants reduces
the brown-area error from 26.0 % to 6.6 % of the full-fit brown area while
costing 10 % of the available *J*<sub>hue</sub>.

### 8.2 The two extracted spectra

Both endmember spectra, as unit-length vectors over the twenty-six cells,
ordered by illuminant wavelength. The right-hand column is the difference,
which is the direction *J*<sub>hue</sub> measures along.

| Cell | Wavelength (nm) | ε_Y (yellow) | ε_B (brown) | ε_B − ε_Y | Noise σ |
|---|---|---|---|---|---|
| violet.B | 410 | +0.560 | +0.242 | −0.318 | 0.0049 |
| royal_blue.B | 455 | +0.398 | +0.279 | −0.119 | 0.0050 |
| blue.B | 475 | +0.273 | +0.270 | −0.003 | 0.0053 |
| blue.Gb | 475 | +0.197 | +0.249 | +0.053 | 0.0064 |
| blue.Gr | 475 | +0.197 | +0.253 | +0.056 | 0.0062 |
| cyan.B | 500 | +0.116 | +0.217 | +0.100 | 0.0051 |
| cyan.Gb | 500 | +0.073 | +0.187 | +0.114 | 0.0055 |
| cyan.Gr | 500 | +0.073 | +0.190 | +0.117 | 0.0053 |
| green.B | 525 | +0.005 | +0.139 | +0.134 | 0.0078 |
| green.Gb | 525 | −0.027 | +0.103 | +0.130 | 0.0054 |
| green.Gr | 525 | −0.026 | +0.105 | +0.131 | 0.0057 |
| amber.Gb | 590 | −0.150 | −0.131 | +0.018 | 0.0068 |
| amber.Gr | 590 | −0.150 | −0.130 | +0.020 | 0.0065 |
| amber.R | 590 | −0.155 | −0.144 | +0.011 | 0.0070 |
| red_orange.Gb | 615 | −0.156 | −0.195 | −0.039 | 0.0121 |
| red_orange.Gr | 615 | −0.159 | −0.194 | −0.035 | 0.0116 |
| red_orange.R | 615 | −0.156 | −0.200 | −0.044 | 0.0067 |
| red.Gb | 625 | −0.155 | −0.200 | −0.045 | 0.0128 |
| red.Gr | 625 | −0.158 | −0.201 | −0.043 | 0.0119 |
| red.R | 625 | −0.153 | −0.204 | −0.051 | 0.0067 |
| photo_red.Gb | 660 | −0.151 | −0.221 | −0.070 | 0.0147 |
| photo_red.Gr | 660 | −0.155 | −0.222 | −0.067 | 0.0136 |
| photo_red.R | 660 | −0.152 | −0.223 | −0.071 | 0.0067 |
| white.B | broadband | +0.206 | +0.194 | −0.012 | 0.0055 |
| white.Gb | broadband | −0.052 | +0.004 | +0.056 | 0.0060 |
| white.R | broadband | −0.143 | −0.168 | −0.025 | 0.0052 |

Negative values are a consequence of the vectors being unit-length after the
shading direction has been removed: each spectrum is defined only up to a
constant added to every cell, so only the *shape* of each column carries
meaning, not its sign at any one cell.

Reading the shape: the yellow spectrum falls steeply from 410 nm and crosses
the brown spectrum near 475 nm, then continues below it. The brown spectrum is
flatter, retaining absorbance through 500 to 525 nm where the yellow spectrum
has already fallen to approximately zero. This is the behaviour
`docs/gutzeit_protocol.md` predicts when it asks for 415 to 430 nm as "core
yellow absorption" and 525 to 535 nm as "brown / trisubstituted". The
prediction was made independently of these images and is reproduced by them.

The two spectra are nearly parallel from 590 nm upward, which is why the
long-wavelength illuminants contribute almost nothing to hue discrimination
and a great deal to amount determination: they measure the total, largely
without regard to which product supplies it.

### 8.3 Ranking by *J*<sub>hue</sub>

Ceiling, all 26 cells: *J*<sub>hue</sub> = **128.3**.

Best sets at each size, one row per distinct illuminant set:

| Size | Cells | Illuminants | J_hue | Efficiency | J_Y | J_B |
|---|---|---|---|---|---|---|
| 1 | any single cell | 1 | 0.000 | 0.000 | 0 | 0 |
| 2 | violet.B + cyan.B | 2 | 102.5 | 0.799 | 0 | 0 |
| 2 | violet.B + green.Gr | 2 | 95.5 | 0.745 | 0 | 0 |
| 2 | violet.B + blue.B | 2 | 81.1 | 0.632 | 0 | 0 |
| 2 | royal_blue.B + cyan.B | 2 | 60.0 | 0.468 | 0 | 0 |
| 3 | violet.B + cyan.B + cyan.Gr | 2 | 116.0 | 0.904 | 58.3 | 5.4 |
| 3 | violet.B + cyan.B + green.Gr | 3 | 115.9 | 0.904 | 89.3 | 19.7 |
| 3 | violet.B + cyan.B + amber.Gr | 3 | 105.9 | 0.826 | 99.2 | 42.2 |
| 3 | violet.B + cyan.B + photo_red.R | 3 | 102.9 | 0.802 | 101.2 | 49.7 |
| 4 | violet.B + cyan.B + cyan.Gb + green.Gr | 3 | 119.1 | 0.928 | 91.5 | 19.8 |
| 4 | violet.B + cyan.B + green.Gr + white.Gb | 4 | 117.9 | 0.919 | 97.1 | 28.7 |

The set with the highest *J*<sub>hue</sub> at size 3 spends two of its three
cells on one illuminant, cyan, and pays for it: its *J*<sub>B</sub> is 5.4
against 49.7 for a set of the same size using three different illuminants.
Ranking on hue discrimination alone therefore selects a set that is poor at
quantification, which is why the operational measures of Section 7.5 and 7.6
are reported alongside.

### 8.4 Ranking by agreement with the full 26-cell fit

The eight best sets of each size, ranked by agreement. An asterisk marks sets
whose cells all come from different illuminants.

| Size | Cells | Efficiency | J_B | Agreement | Hue-only agreement | Brown-area error |
|---|---|---|---|---|---|---|
| 2 | violet.B + royal_blue.B * | 0.473 | 0 | 79.2 % | 79.5 % | 66.5 % |
| 2 | violet.B + blue.B * | 0.632 | 0 | 78.6 % | 79.0 % | 69.9 % |
| 2 | violet.B + cyan.B * | 0.799 | 0 | 75.7 % | 76.1 % | 80.3 % |
| 3 | **violet.B + cyan.B + photo_red.R** * | **0.802** | **49.7** | **94.5 %** | **94.8 %** | **6.6 %** |
| 3 | violet.B + green.Gr + red.Gr * | 0.749 | 25.2 | 94.1 % | 94.3 % | 5.2 % |
| 3 | violet.B + cyan.B + red.R * | 0.806 | 47.6 | 93.9 % | 94.2 % | 8.6 % |
| 3 | violet.B + cyan.B + red_orange.R * | 0.805 | 46.8 | 93.7 % | 94.0 % | 9.5 % |
| 3 | violet.B + cyan.B + amber.Gr * | 0.826 | 42.2 | 92.1 % | 92.3 % | 15.7 % |
| 4 | violet.B + cyan.B + cyan.Gr + photo_red.Gr | 0.905 | 30.8 | 94.2 % | 94.4 % | 6.8 % |
| 4 | violet.B + cyan.B + cyan.Gr + red.Gr | 0.906 | 33.8 | 94.1 % | 94.2 % | 7.7 % |
| 4 | violet.B + cyan.B + green.Gr + red.Gr * | 0.906 | 33.0 | 92.8 % | 92.9 % | 10.4 % |

Three observations follow.

**Every competitive set of size 3 or 4 has the same structure**: one cell at
410 nm, one cell at 500 to 525 nm, and one cell at 590 to 660 nm. That is, one
measurement where the yellow product absorbs strongly and the brown product
does not, one measurement near the crossing point of the two spectra, and one
measurement where neither product absorbs much, which serves as a reference.
This structure is the one `docs/gutzeit_protocol.md` prescribes on
spectroscopic grounds, recovered here from the images alone.

**The third cell buys quantification, not discrimination.** Comparing
`violet.B + cyan.B + green.Gr` with `violet.B + cyan.B + photo_red.R`: the
first has the higher efficiency, 0.904 against 0.802, and the second has the
far better brown-area error, 6.6 % against 26.0 %, and the higher
*J*<sub>B</sub>, 49.7 against 19.7. The long-wavelength cell contributes little
to telling the products apart and a great deal to determining how much of each
is present.

**The fourth cell adds very little.** The best set of four agrees with the full
fit on 94.2 % of 15 371 pad pixels; the best set of three agrees on 94.5 %. The
fourth cell raises efficiency from 0.802 to 0.905 but does not improve the
classification, because at these signal levels the classification is limited by
the pads themselves rather than by photometric noise.

### 8.5 Sets optimised for one purpose

Different purposes select different sets. All sets of size 3 were searched
separately against each of the three measures.

| Purpose | Best set of three | J_hue | J_Y | J_B |
|---|---|---|---|---|
| Distinguish the products | violet.B + cyan.B + green.Gr | 115.9 | 89.3 | 19.7 |
| Quantify yellow | violet.B + blue.B + cyan.B | 106.2 | 104.7 | 13.9 |
| Quantify brown | violet.B + cyan.B + white.R | 104.2 | 100.5 | 49.9 |
| Balanced | violet.B + cyan.B + photo_red.R | 102.9 | 101.2 | 49.7 |

`violet.B + cyan.B + photo_red.R` appears within the top five sets for both
yellow quantification and brown quantification while remaining above 0.80
efficiency, which is why it is the recommended balanced set. Note that
`white.R` and `photo_red.R` are interchangeable for the brown-quantification
role, differing by 0.2 in *J*<sub>B</sub>.

---

## 9. Whether different concentration ranges require different cell sets

The operator's expectation, that different concentration ranges may call for
different cell combinations or different weights, is supported by the data,
with the following specific structure.

### 9.1 The estimator itself does not change

Once the two endmember spectra are fixed, the unmixing is a linear operation
followed by a non-negativity clamp. The same three cells produce both amounts
at every loading. There is no need to switch estimators as a function of
concentration, and switching would introduce a discontinuity at whatever
threshold were chosen.

### 9.2 The *precision* of each amount changes greatly with loading

Using the recommended set `violet.B + cyan.B + photo_red.R`, the noise on the
amounts is fixed by the instrument at σ<sub>y</sub> = 0.0099 and
σ<sub>b</sub> = 0.0201 in amount units. The **relative** precision therefore
depends entirely on how much of each product is present:

| Archive | Concentration (µg/L) | Mean yellow | Mean brown | σ_y / mean yellow | σ_b / mean brown |
|---|---|---|---|---|---|
| C1008 | 16 | 0.487 | 0.0024 | 0.020 | 8.28 |
| C1009 | 16 | 0.791 | 0.0044 | 0.012 | 4.53 |
| C1004 | 64 | 0.859 | 0.198 | 0.012 | 0.102 |
| C1005 | 64 | 0.709 | 0.381 | 0.014 | 0.053 |
| C1003 | 128 | 0.495 | 0.713 | 0.020 | 0.028 |
| C1002 | 256 | 0.301 | 1.099 | 0.033 | 0.018 |
| C1014 | interrupted | 0.309 | 0.152 | 0.032 | 0.132 |

The yellow amount is determined to between 1.2 % and 3.3 % of its own value
across the entire range, the divisor in each case being that archive's own mean
yellow amount. The brown amount is determined to better than 11 % of its own
value at 64 µg/L and above, but at 16 µg/L the standard error exceeds the mean
brown amount by factors of 4.5 and 8.3 — which is the correct report, because
at 16 µg/L there is essentially no brown product present.

**The practical consequence:** below roughly 30 µg/L, the measurement is a
yellow measurement and the brown channel returns noise. Above roughly 100
µg/L, brown carries most of the signal and the yellow amount is falling. A
useful instrument reports both amounts and their standard errors at every
concentration, and allows the *interpretation* to change with range rather than
the estimator.

### 9.3 Where extra cells would help, by range

- **At low loading**, where only yellow is present, the quantity of interest is
  the yellow amount, and the yellow-optimal set `violet.B + blue.B + cyan.B`
  gives *J*<sub>Y</sub> = 104.7 against 101.2 for the balanced set — an
  improvement of 3.5 % of the balanced set's *J*<sub>Y</sub>. This is a small
  gain and does not justify a different capture.
- **At high loading**, where brown dominates, the constraint is different: the
  blue-side cells lose dynamic range as the pad darkens, while the reference
  cells retain it. This is a reason to keep a long-wavelength cell in the set
  regardless of range, which the balanced set already does.
- **Across the whole range**, the one genuinely range-dependent quantity is the
  exposure, not the cell set. See Section 11.3.

---

## 10. Colour-space coverage of the present image set

The stated purpose of these seven archives is to provide a set of colour
samples. This section reports what part of the yellow-to-brown colour space
they actually sample, and where the gaps are.

### 10.1 Range covered by each archive

Percentiles are over the pad pixels of that archive. The brown fraction is
reported only over pixels above the detection floor.

| Archive | Total amount, 5th pct | median | 95th pct | Brown fraction, 5th pct | median | 95th pct |
|---|---|---|---|---|---|---|
| C1002 | 1.020 | 1.373 | 1.527 | 0.656 | 0.843 | 1.000 |
| C1003 | 1.038 | 1.230 | 1.444 | 0.256 | 0.541 | 0.845 |
| C1004 | 0.755 | 0.974 | 1.375 | 0.000 | 0.026 | 0.593 |
| C1005 | 0.859 | 1.106 | 1.404 | 0.000 | 0.266 | 0.693 |
| C1008 | 0.173 | 0.238 | 0.476 | 0.000 | 0.000 | 0.000 |
| C1009 | 0.322 | 0.451 | 0.799 | 0.000 | 0.000 | 0.000 |
| C1014 | 0.034 | 0.149 | 1.317 | 0.000 | 0.000 | 0.825 |

### 10.2 Occupancy of the colour space

Pixel counts pooled over all seven archives, with rows the total amount and
columns the brown fraction. Only pixels above the detection floor are counted.

| Total amount | h 0.00–0.05 | 0.05–0.20 | 0.20–0.40 | 0.40–0.60 | 0.60–0.80 | 0.80–1.00 |
|---|---|---|---|---|---|---|
| 0.00–0.10 | 360 | 123 | 124 | 73 | 24 | 48 |
| 0.10–0.25 | 1667 | 25 | 11 | 5 | 5 | 2 |
| 0.25–0.50 | 2453 | 23 | 10 | 2 | 25 | 26 |
| 0.50–0.75 | 962 | 25 | 17 | 15 | 14 | 42 |
| 0.75–1.00 | 1463 | 125 | 36 | 50 | 38 | 77 |
| 1.00–1.50 | 256 | 832 | 1473 | 1529 | 1659 | 1382 |
| 1.50 and above | 0 | 0 | 1 | 22 | 153 | 121 |

**The sampling is strongly bimodal and the colour space is not covered.** Two
regions are densely populated: pure yellow at low to moderate total amount
(the left column, rows up to 1.00, roughly 6900 pixels), and mixtures of every
hue at high total amount (the bottom two rows, roughly 7400 pixels). The
intermediate region — moderate total amount with an appreciable brown
fraction, meaning total amount between 0.25 and 1.00 with brown fraction
between 0.2 and 0.8 — contains **207 pixels out of the 15 298 pixels above the
detection floor, which is 1.4 %**, the divisor being the number of pixels above
the floor.

That gap matters for the intended purpose. A colour-sample library used to
develop or verify an inversion should populate the region where the two
products coexist in comparable quantities at a moderate total loading. In the
present set, the brown product essentially only appears on pads that are also
heavily loaded overall, so brown fraction and total amount are confounded.

**Suggested additions to break that confounding:** pads reacted at high
concentration for a short time, or at moderate concentration with a reduced
reaction time, so that the brown product forms while the total loading stays
moderate. The new hole geometry may achieve this naturally at the periphery of
the stain, where the arsine dose falls off with distance from the hole: C1014
is the only archive in the present set with pixels spread continuously from
total amount 0.03 to 1.3 within a single pad.

### 10.3 Sensitivity of the pixel counts to the pad-edge treatment

Because pixel counting is the intended downstream use, the counts are reported
at three erosion depths of the pad rectangle. "Erosion 2" means pixels within 2
layer pixels of the pad's fitted boundary are excluded.

| Archive | Erosion 0 px: white / yellow / brown | Erosion 2 px | Erosion 4 px |
|---|---|---|---|
| C1002 | 0 / 7 / 2037 | 0 / 0 / 1694 | 0 / 0 / 1376 |
| C1003 | 0 / 953 / 1265 | 0 / 951 / 903 | 0 / 942 / 580 |
| C1004 | 0 / 1985 / 241 | 0 / 1842 / 20 | 0 / 1530 / 0 |
| C1005 | 1 / 1774 / 507 | 0 / 1654 / 260 | 0 / 1511 / 67 |
| C1008 | 0 / 2147 / 0 | 0 / 1787 / 0 | 0 / 1460 / 0 |
| C1009 | 0 / 2214 / 0 | 0 / 1850 / 0 | 0 / 1518 / 0 |
| C1014 | 72 / 1766 / 402 | 11 / 1528 / 337 | 0 / 1227 / 317 |

Two things are visible. On the full-face geometry, the brown product is
concentrated at the pad's boundary: C1004's brown count falls from 241 pixels
at erosion 0 to 20 at erosion 2 and 0 at erosion 4, a loss of 100 % of its
brown pixels, the divisor being the erosion-0 count. Whether that rim brown is
a genuine concentration of product at the pad's edge or an artefact of pixels
that straddle the pad boundary is **not resolved by this analysis**. Argument
in favour of it being genuine: `report3.py` found the colour index at the rim
changes sign between archives, which a geometric artefact would not do.

On the hole geometry, by contrast, C1014's brown count is stable — 402, 337
and 317 across the three erosions, a loss of 21 % of the erosion-0 brown count
while 31 % of the pad's pixels are being removed (2240 falling to 1544; both
divisors are the erosion-0 figures). The brown region of C1014 is in the middle of the
pad and is therefore unaffected by whatever the rim is doing. **The hole
geometry is materially better suited to this measurement for that reason.**

---

## 11. Recommendations for the next set of test images

### 11.1 What the pre-image capture is for, and what it must contain

**The purpose of the pre-image** is to measure the unstained pad's own
reflectance in each cell, so that the dye's contribution can be obtained as the
difference between the reacted and unreacted states rather than by assuming
the pad's starting colour.

**A measurement that bears directly on this.** The unstained pad's absorbance
against bare paper was measured on the 72 pixels of C1014 that the model
classifies as white. Averaged over the twenty-six cells, the unstained pad
reads **+0.1117 absorbance** relative to bare paper — that is, the pad is about
11 % darker in reflectance than the paper beside it. The deviations from that
flat average are small, running from −0.0217 absorbance at `green.B` to +0.0184
at `red.R`. Since a flat offset is absorbed by the shading term, **the
unstained pad is close to spectrally neutral against bare paper**, and the
blank correction is a small correction rather than a first-order one.

**A discrepancy that must be resolved before the pre-image is relied upon.**
The pre-image capture already present in C1014 measures two usable cells,
`royal_blue.B` and `red.R`. Comparing the pre-image of C1014 with the unstained
pixels of the same strip's post-image:

| Cell | Pre-image (dry, unreacted) | Post-image, unstained pixels (wetted, no arsine) |
|---|---|---|
| royal_blue.B | +0.0239 A | +0.1058 A |
| red.R | −0.0105 A | +0.1301 A |
| difference between the two cells | +0.0344 A | −0.0243 A |

The two states differ by about 0.11 absorbance in their common level and by
0.0587 absorbance in the one spectral contrast the pre-image is currently able
to measure. For comparison, the dye contrast between the same two cells is
0.551 absorbance per unit amount, so 0.0587 is between 7 % and 34 % of the dye
signal at the loadings observed here, the divisor being the dye contrast at
total amounts of 1.5 and 0.3 respectively.

The cause is not established by these data. The candidates are: the pad is wet
or dried-from-wet in the post-image and dry in the pre-image, which changes
scattering — this is precisely why `docs/gutzeit_protocol.md` designates 620 to
630 nm as the "reference — wetting correction" band; the strip was removed and
replaced between the two captures; and the two captures used different exposure
rungs. **This should be resolved deliberately, because it determines whether a
dry pre-image can serve as the blank for a wet post-image at all.**

**Recommended pre-image capture set.** Every cell used in the post-image
analysis needs a matching pre-image measurement, because the blank correction
is applied per cell. The recommendation is therefore:

| Illuminant | Layer used | Status | Reason |
|---|---|---|---|
| **violet 410 nm** | B | **essential** | The single most informative cell. Largest yellow-product absorbance of any cell (ε_Y = +0.560) and the largest yellow-to-brown difference (ε_B − ε_Y = −0.318). Also the cell where the pad's own absorbance most needs measuring. Currently **absent** from the pre-image. |
| **cyan 500 nm** | B | **essential** | The second cell of every competitive set. Sits at the crossing of the two product spectra, where the brown product absorbs and the yellow product has nearly stopped. Currently **absent** from the pre-image. |
| **photo_red 660 nm** or **red 625 nm** | R | **essential** (one of the two) | The reference that makes both amounts determinable. `photo_red.R` gives the best brown quantification of any third cell (J_B = 49.7); `red.R` is within 5 % of it on that measure (J_B = 47.6, divisor 49.7) and is **already in the pre-image**, so retaining red costs nothing. |
| green 525 nm | Gr or Gb | **recommended** | The brown-diagnostic band named by the protocol, and the best fourth cell for discrimination. Provides redundancy against cyan, and permits the discrimination-optimal set `violet.B + cyan.B + green.Gr` to be formed. |
| royal_blue 455 nm | B | **retain** | Already present in the pre-image. Second-best yellow band, and retaining it preserves comparability with every archive captured so far. |
| blue 475 nm | B | optional | Only of value if yellow quantification at low loading becomes the priority; the yellow-optimal set is `violet.B + blue.B + cyan.B`. |
| white | B, Gb, R | optional | `white.R` is interchangeable with `photo_red.R` for the brown-quantification role. Useful chiefly for producing true-colour renderings for visual checking. |
| amber 590, red_orange 615 | R | not needed | Interchangeable with red and photo_red as a reference, with no unique contribution. |

**The minimum pre-image capture is therefore four illuminants: violet,
cyan, red (or photo_red), and royal_blue** — the last retained for continuity.
Adding green makes five and is recommended. The present pre-image capture of
royal_blue and red alone measures neither of the two cells that carry most of
the colour information.

### 11.2 The post-image capture

**Recommendation: change nothing about the post-image capture for now.** Continue
capturing all ten illuminants at all three exposure rungs.

The reason is the stated purpose of the next image set. The analysis cells can
be selected after the fact from a complete capture, but a cell that was not
captured cannot be recovered; and the endmember spectra themselves are
estimated from the full cell set, with the twenty-six-cell fit serving as the
reference against which every reduced set is judged. Until the endmember
spectra are settled on the new geometry, and until the colour-space gaps of
Section 10.2 are filled, the complete capture is what makes the next set
useful as a colour-sample library.

The case for trimming the post-image capture arises later, when the analysis is
fixed and capture time matters. At that point the evidence in this report
supports reducing the measurement to three or four cells from three
illuminants.

### 11.3 Exposure

The rung selected by the analysis is the same on all seven archives for every
cell examined, which means the exposures need not vary with concentration over
the 16 to 256 µg/L range:

| Cell | Rung selected on all seven archives | Comment on the exposure ladder |
|---|---|---|
| violet.B | e0, the shortest | e1 saturates in the measured region; e2 is not stored at all. Only one usable rung exists. |
| royal_blue.B | e0, the shortest | e1 and e2 both saturate. Only one usable rung exists. |
| cyan.B | e2, the longest | Even at e2 the peak value in the measured region reaches 657 of the 1023 available counts, which is 64 % of full scale. Cyan is under-exposed at the top of its ladder. |
| green.Gr | e1 | e2 saturates. |
| red.R | e1 | e2 saturates. |
| photo_red.R | e1 | e2 saturates. |

**Two changes to the exposure ladders are indicated.** First, the violet and
royal_blue ladders are centred too long: two of the three rungs are unusable,
so two-thirds of the frames captured for those illuminants are wasted, and no
headroom exists if a future pad is lighter than these. Second, the cyan ladder
is centred too short, leaving 36 % of the sensor's range unused at the longest
rung, which costs signal-to-noise in the second most important cell of the
recommended set.

**For the pre-image specifically**, note that an unstained pad is *brighter*
than a stained one, so the existing convention of capturing the pre-image two
rungs below the measurement is correct in direction for violet and royal_blue.
Applied uniformly, however, that convention would place cyan two rungs below an
exposure that is already too short, reducing cyan's signal to approximately 195
counts of 1023 and degrading its precision by roughly a factor of 1.5 relative
to the measurement. **The pre-image exposure shift should be set per
illuminant rather than uniformly.**

### 11.4 The cells to use in the post-image analysis

| Purpose | Cells | Efficiency | Agreement with 26-cell fit |
|---|---|---|---|
| **Standing recommendation** | violet.B + cyan.B + photo_red.R | 0.802 | 94.5 % |
| If only two illuminants can be used | violet.B + cyan.B | 0.799 | hue only; cannot separate white from stained |
| If discrimination is paramount | violet.B + cyan.B + green.Gr | 0.904 | 81.7 %, brown-area error 24.1 % |
| If four cells are affordable | violet.B + cyan.B + cyan.Gr + photo_red.Gr | 0.905 | 94.2 % |
| Reference for all of the above | all 26 cells | 1.000 | by definition 100 % |

---

## 12. Limitations

1. **The residual exceeds the noise by a factor of 3 to 5** (Section 6.2). Two
   components describe most, but not all, of the colour variation. A third
   component has not been sought.
2. **The zero of the absorbance scale is bare paper, not an unstained pad**
   (Section 5.4). This biases the absolute yellow amount upward by the pad's own
   absorbance. It does not bias the yellow-versus-brown decision.
3. **A spectrally flat absorber cannot be distinguished from shading** by this
   instrument, by construction (Section 5.6). The amounts are contrast
   quantities.
4. **The rim question is unresolved on the full-face geometry** (Section 10.3).
   Whether brown at the pad's boundary is genuine chemistry or an artefact of
   mixed pixels is not settled by these data. The hole geometry avoids the
   question by placing the stain away from the boundary.
5. **The pre-image comparison rests on one strip and two cells** (Section 11.1).
   C1014 is the only archive with an internal pre-image, and that pre-image
   measures only `royal_blue.B` and `red.R` of the twenty-six cells.
6. **Stage colour is confounded with concentration** in this set: C1002 to
   C1005 were captured on the olive stage and C1008, C1009 and C1014 on the
   black stage, and the three lowest-loading pads are all on the black stage.
   No stage-dependent effect was detected, but this set cannot exclude one.
7. **The violet fluorescence test is indirect** (Section 6.6). The loading
   dependence of the violet-to-royal-blue ratio has the shape absorption
   predicts and not the shape emission predicts, but pad fluorescence was not
   measured directly.
8. **Concentrations are nominal**, taken from the preparation record, and C1014's
   concentration is not comparable with the rest because its run was
   interrupted.
