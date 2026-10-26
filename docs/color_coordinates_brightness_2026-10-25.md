# Yellow and brown coordinates, brightness, and summing the pad (2026-10-25)

A working note answering Bill's proposal: add up all the pixel values over the
dot, ignore surface roughness, compute yellow and brown coordinates that do not
depend on brightness, and use an edge factor ℓ(x) only where needed.  Companion
to `docs/pad_light_physics_2026-10-25.md` (tags **[D]** derived here, **[N]**
checked numerically in `color_alg.py` in the session scratchpad, **[?]**
unknown).  Nothing here is adopted into the chain.

Words before symbols.  A means absorbance, A = −log10(R); **bigger A is
darker**.  "Channel" means one LED color on one Bayer layer (six in the
analysis: violet.B, royal_blue.B, cyan.B, green.Gb, green.Gr, photo_red.R).
Because the project already uses A for absorbance, the absorbed fraction
below is written α (alpha).

## 1. The energy balance: the correct expression

Bill's sketch: "d = I / A / (E + B)".  Light is conserved, so the terms
**subtract**; they do not divide.  Let I be the light power falling on the
pad's top.

1. A small part, s·I, bounces off the top surface like a mirror.  Our lights are
   20°–28° from straight down and the camera is on the axis, so a mirror
   bounce goes to the other side and does not reach the camera (except from
   tilted facets, a small part).  Leave it out.
2. The rest, (1 − s)·I, enters.  It ends up as exactly one of: absorbed in the
   paper and dye (P_abs), lost out the cut edges (P_edge), lost out the back
   (P_back, net: what goes into the backing and is not returned), or
   re-emitted from the top (P_top):

       (1 − s)·I = P_abs + P_edge + P_back + P_top

3. The camera collects a fixed fraction κ of what leaves the top (set by the
   lens and by how diffuse paper emits).  So the pixel signal is

       d = κ · P_top = κ · (1 − s) · I · (1 − α − ε − τ)

   with α = P_abs / ((1−s)I), ε = P_edge / ((1−s)I) and τ = P_back / ((1−s)I)
   the fractions.  In the chain's words, `d = a·b·n` with
   `a = κ(1−s)(1 − α − ε − τ)`.

**Caution [D]:** this is an exact bookkeeping identity, but the three loss
fractions are **not independent knobs**.  More absorption (dye) shortens the
light's wandering, so it lowers ε and τ.  For an infinitely thick pad with no
edges only α is left and 1 − R_inf = α, with R_inf = a − b depending only on
K/S (physics note, 4.7).  For a finite pad over a backing of reflectance Rg
the same two-flow equations give

    R = [1 − Rg·(a − b·coth(bSX))] / [a − Rg + b·coth(bSX)]       (1 − R = α + τ)

and the edge multiplies this by a position factor, R(x) ≈ R·ℓ(x) (section 6).

## 2. What surface roughness does to the total [D]

Tilt a small facet by angle α toward azimuth ψ (angle from the pad's flat
surface; light zenith θ).  Results (physics note, 4.3):

- Light **intercepted** per unit flat area of pad: E_h·(1 + tan θ·tan α·cos ψ).
  Averaged over all tilt directions the cos ψ term is zero, so the intercepted
  light, and so the total absorbed, equals the flat pad's.  Roughness only
  redistributes it.
- Light **seen by a camera looking straight down**: E_h·(cos α + tan θ·sin α·cos ψ),
  which averages to cos α ≈ 1 − α²/2.  A tilted facet sends its diffuse
  light mostly into a tilted cone, away from the camera.

So Bill's statement is right for the total ("does not change the total
reflected, only clumps it"), with one correction: the camera sees a fraction
⟨cos α⟩ of it, about 1–2 % less at typical tilts.  That is a **constant
factor** if the roughness is the same in the pre-image and the post-image, so
it cancels in post/pre.  It does not cancel against the gray chips (different
material, different roughness), which matters only for absolute reflectance
to 1–2 %.  Shadows inside pores add a little extra absorption in dyed paper;
that is second order and is not estimated here.  **[?]** Real paper may not
emit exactly like an ideal matte surface relative to each facet.

Summing pixels is therefore safe against roughness, **provided the quantity
being summed is linear in light power** (a raw pixel value after dark
subtraction, or reflectance R, not a log).

## 3. Yellow and brown coordinates: the algebra

### 3.1 Model

Let the pixel's reflectance in channel i, relative to the same pixel's
reference (the dry pad in the pre-image, or the dye-free white zone of the
same image), be R_i.  Two assumptions, both stated so they can be tested:

- **Achromatic brightness:** everything that changes brightness without
  changing color (lighting, texture, tilt, edge, back leak) multiplies
  every channel by the same factor g.  (Section 4 says how well this holds.)
- **Dye adds absorbance:** yellow dye amount c_y and brown dye amount c_u add
  absorbance spectra y_i and u_i (per unit amount, both **non-negative**:
  dyes only absorb).  This holds for small amounts; it is approximate for
  strong color and is calibrated against arsenic anyway.

Then, with γ = −log10 g,

    A_i = γ + c_y·y_i + c_u·u_i          (i = 1 … k;  k = 6 channels)

In vector form, with 𝟙 the vector of k ones (the "brightness direction"),
y and u the spectra as vectors:

    A = γ·𝟙 + c_y·y + c_u·u  =  B·(γ, c_y, c_u)ᵀ,     B = [𝟙  y  u]  (k × 3)

Brightness is exactly the direction 𝟙 in absorbance (log) space.  In the
linear reflectance space it is a stretch of all channels together; the log
turns that into a shift along 𝟙.

### 3.2 The coordinates and why they are free of brightness

If 𝟙, y and u are linearly independent, least squares gives the unique
coordinates

    (γ, c_y, c_u)ᵀ = M·A,     M = (BᵀB)⁻¹·Bᵀ        (3 × k)

and by construction **M·B = I** (the 3 × 3 identity).  Write m_y and m_u for
rows 2 and 3 of M.  Reading M·B = I column by column:

    m_y·𝟙 = 0,   m_y·u = 0,   m_y·y = 1
    m_u·𝟙 = 0,   m_u·y = 0,   m_u·u = 1

Consequences **[D, N]** (`color_alg.py` printed: row_y·𝟙 = −1e-14, row_y·u =
8e-16, row_y·y = 1.0000):

- Each coordinate row is **exactly orthogonal to the brightness direction**,
  and the yellow row is orthogonal to the brown *spectrum* (and the reverse).
  So c_y and c_u do not change when γ changes: they are independent of
  brightness, per pixel, without knowing g.
- Brightness drops out by **subtraction in log space**, which is the same as
  division in linear space: it is the "normalize by brightness in the same
  image" Bill asks for, done by the choice of rows.

### 3.3 Why the yellow and brown *spectra* cannot be orthogonal to brightness

Bill suspected "not precisely".  Proof: y_i ≥ 0 for every channel and y is not
all zero, so y·𝟙 = Σ y_i > 0.  The same for u.  Two vectors are orthogonal
only if their dot product is zero, so y is **never** orthogonal to 𝟙 (nor,
generally, to u).  Dyes darken the pad: yellow and brown have a gray
component.  What is orthogonal are the **measuring rows** m_y and m_u (they
are the "reciprocal basis" of 𝟙, y, u).  In the numeric example y·𝟙 = 2.45,
yet row_y·𝟙 = 0.

### 3.4 What it costs: noise gain

Say every channel has independent noise of size σ in A.  Then

    noise in c_y = σ·‖m_y‖,     noise in c_u = σ·‖m_u‖     (‖·‖ is vector length)

Remove brightness first by subtracting the mean over channels: ỹ = y − ȳ𝟙,
ũ = u − ū𝟙 (these *are* orthogonal to 𝟙).  Let φ be the angle between ỹ
and ũ.  Inside the plane spanned by ỹ and ũ, the dual vector has length

    ‖m_y‖ = 1 / (‖ỹ‖·sin φ),     ‖m_u‖ = 1 / (‖ũ‖·sin φ)

(checked numerically **[N]**: 2.7966 both ways).  Reading it:

- A dye whose spectrum is nearly flat across the channels (small ‖ỹ‖) is
  nearly invisible once brightness is removed, and its coordinate is noisy.
  Brown dyes absorb broadly, so **brown is expected to be the noisier
  coordinate**.
- If yellow and brown spectra point nearly the same way (small sin φ), the two
  coordinates cannot be told apart and both are noisy.
- In the toy example (invented spectra, not data): ‖m_y‖ = 2.8, ‖m_u‖ = 9.0.
  Only the formulas carry over.

**These spectra must be measured, not assumed.**  Bill's plan, pick the most
yellow and most brown images and get the transformation from them, works only
if each is **pure**.  A brown pad probably also has yellow in it.  Safer:
take every dot pixel's channel vector minus the white zone's, remove the mean
over channels, and find the plane they occupy (principal components).  The
plane is set by the data.  Which two directions inside it are called yellow
and brown is a **choice** (a rotation); any two independent directions in the
plane carry the same information about arsenic, so the labels matter for
interpretation, not for calibration.  With six channels the data have 5
color dimensions after removing brightness; two are signal, the other three
should be noise (a built-in check on the model).

## 4. How well "achromatic brightness" holds, and what breaks it

The rows m_y, m_u remove only a **common** factor.  Any channel-dependent
factor leaks in: if channel i has an extra error η_i in A, then

    error in c_y = m_y·η,     error in c_u = m_u·η

with size σ_η·‖m‖ for independent η.  Candidates:

| Source | Same in all channels? | Comment |
|---|---|---|
| Lighting level, exposure, lamp output | **yes** after the gray chips / pre-image | n cancels |
| Surface roughness (tilt, pores) | nearly (section 2) | constant factor, cancels in post/pre |
| Pixel-scale paper texture | partly | color-to-color agreement of the texture was 0.50 / 0.70 / 0.80 at 1 / 2 / 4 px (shading study).  The unshared part is chromatic noise. Test A measures it |
| Edge loss ℓ(x) | **no** | L depends on color and dye; section 6 |
| Back leak, backing show-through | **no** | translucency and the backing's reflectance depend on color; cancels in post/pre only if the dye does not change how much light reaches the backing, which it does; calibrated, not removed |
| Camera noise | no (independent) | about 0.002 A per pixel, averages down |

Numerical check **[N]** (`color_alg.py`): chromatic noise of 0.003 A per
channel gave a per-pixel spread of 0.0085 in c_y and 0.027 in c_u for the toy
spectra, equal to 0.003·‖m‖.  Averaging N independent pixels divides by √N.
A dot of 9.4 px radius has about 280 layer pixels, so about 17×.  The texture
noise is **not fully independent** pixel to pixel (it clumps over 1–2 px), so
the true gain is somewhat less.

## 5. Sum first, or normalize each pixel?

**Theorem [D, N]:** if, in a pixel set, every pixel has the same color and
different achromatic brightness (R_i = g_p·r, pixel p), then

- the per-pixel coordinates, averaged, and
- the coordinates of the summed channel vector, −log10(Σ_p R_p),

are **identical**, and both equal m·(−log10 r), because −log10(Σ g_p·r) =
−log10 r − log10(Σ g_p) and m·𝟙 = 0.  The numerical run gave 0.05, 0.20 for
both with 5 % brightness texture.  So if brightness is achromatic, **you can
average first, compute, and read the result; you do not need per-pixel
normalization.**  This also needs no pixel-by-pixel pre-image pairing, which
test B showed carries little texture into the dot (slope 0.3, red).

If the **color itself varies** across the pixels (patchy dye), the two
differ by the second-order term (a standard expansion, R = 10^−A):

    −log10⟨R⟩ = ⟨A⟩ − (ln 10 / 2)·Var(A) + …        (per channel, ln 10/2 = 1.15)

So the summed value reads **less dark** than the average of per-pixel values
by 1.15·Var(A), per channel.  With dye patchiness of 0.05 A that is 0.003 A,
about 6 % of c_y in the toy run **[N]**, and it goes through the rows m.  The
summed value is what the photons actually give (photon counting); the
per-pixel mean also has trouble where noisy pixels get large −log values.  The
bias is not a flaw if it is calibrated: it grows with patchiness, which
differs by pad, so the pad's variance map gives a **correction** and the
difference between the two methods is itself a patchiness measure.

Recommendation **[D]**: sum (or average) raw reflectance over the same pixel
set in the post-image and pre-image (the dot mask, from `find_dot`, mapped
by `pre_to_post`), take the ratio per channel, convert to A, apply rows m.
Use the same pixel set for both.

## 6. The edge factor ℓ(x) and corners

For one straight edge with exponential line spread (physics note, 4.8):

    ℓ(x) = R(x)/R_bulk = 1 − ½·e^(−x/L)

with x the distance from the edge to the pixel centroid (pixel-averaged form
in 4.8).  The model leaves out light entering the lit edge faces, which is
the same order of size (4.2), so ℓ(x) should be **measured**, not taken from
the formula.

**Corner:** with two edges at distances x1 and x2, "the product ℓ(x1)·ℓ(x2)"
is the right form of guess.  It is exactly the retained fraction if the
sideways spread separates into an x part times a y part.  For a round
(isotropic) spread it is exact at the corner itself (a quarter-plane keeps
a quarter of the directions: 0.2476 vs 0.2476 **[N]**) and slightly low
elsewhere: 0.4263 exact against 0.4207 at x1 = x2 = 0.5·(spread length), 0.588
against 0.579 at 1.0, 0.532 against 0.529 at (0.3, 2) **[N]**, an error
of 0.5–1.5 % of the retained fraction.  This used a made-up round
exponential kernel, not paper; **[?]** for real paper.

**Where it matters for color [D]:** ℓ is channel-dependent (L depends on
color and shrinks with dye), so −log10 ℓ_i is not along 𝟙 and leaks into c
by m·(−log10 ℓ).  Three ways to handle it:

1. **Keep away from it.**  The dye dot of set 2 (C1028–C1036, radius about
   9–10 px on a pad 21 px from center to the left/right edge) sits well inside;
   there ℓ ≈ 1 for the dot itself.  Use a white zone that also avoids the edges
   (at least 3 px, about 3L).
2. **Divide it out with the pre-image.**  ℓ_pre and ℓ_post are the same
   wherever there is no dye, so the ratio cancels it; this fails only
   where dye reaches the edge (the 1 g dots of set 1 do).
3. **Measure ℓ_i(x1, x2) per channel and per edge** from the dry pre-images of
   all 18 pads (texture averages out) and apply it as a factor.  This is the
   plan in `docs/edge_profile_plan_2026-10-23.md`.  Where dye reaches the edge
   ℓ has to be remeasured for dyed paper.

## 7. Measuring the back leak: Bill's proposed strip test

Proposal: image only the plastic backing strip lying across a background of
painted white and black stripes.  What it can measure, using the standard
result for a translucent layer of reflectance R0 over black (no light coming
back from below), transmittance T (fraction of light that goes through), and
about the same reflectance R0 seen from below, laid on a background of
reflectance Rg:

    R(Rg) = R0 + T²·Rg / (1 − R0·Rg)

Two backgrounds give two equations for two unknowns: over black, Rg = Rb,
R(Rb) = R0 + T²Rb/(1 − R0Rb); over white, Rg = Rw, R(Rw) = R0 + T²Rw/(1 − R0Rw).
If the black is nearly 0 then R0 ≈ R(Rb) and T² ≈ (R(Rw) − R0)(1 − R0Rw)/Rw.
Needed:

- The painted stripes' own reflectance Rb and Rw, per channel, against the gray
  chips (the stripes are not ideal black and white).
- Strip thickness 0.26–0.30 mm, as used.
- The **same surface under the strip as in the instrument**, since in use
  something sits under the pad's backing; its reflectance is then the Rg
  that matters.  **[?]** Unknown: what is under the pad holder.
- A better version **[D]**: put the **whole pad with its backing** across the
  black/white boundary.  R(black) versus R(white) of the dry pad then shows the
  back leak of the actual stack in each channel, and the stripe edge also
  shows the lateral spread over the backing.

Does it matter for the coordinates?  For calibration against arsenic,
only if the under-surface or backing differ between runs: the same backing
and under-surface in pre and post give the same leak, and the arsenic
calibration absorbs the rest.  It matters when we want to **model** R from
K/S, or to know whether to expect the dye's effect to depend on how much
light reaches the backing.

## 8. Verdict on the simplification, and what is not established

- **Sum first, ignore roughness: supported [D, N]**, if the quantity summed is
  linear in power and the same pixel set is used before and after.  Roughness
  is a constant ≈ 1–2 % factor that cancels in post/pre.
- **Brightness-free yellow and brown coordinates: yes, by construction
  [D, N]**, but the spectra y and u are never orthogonal to brightness (3.3);
  what is orthogonal is the measuring rows m.  The price is the noise gain
  ‖m‖ (3.4), larger for dyes with flat spectra, so brown first.
- **Not established:** the actual spectra y and u (need the data's color
  plane); how achromatic the texture, edge and back leak really are (test
  A's color-to-color correlation measures the texture part, ℓ_i(x) and the
  strip test measure the others); real-paper corner behavior; whether the
  linear dye model is good enough at strong color.

## 9. The color plane fitted from the existing images [N]

Scripts (session scratchpad, not in the repo): `plane_collect.py`,
`plane_fit.py`, `plane_fit2.py`, `plane_fit3.py`.  All read-only.

**Method.**  For each of the 18 dot pads (C1017–C1025, C1028–C1036) and each
of the six channels: the dye signal is A_flat = A_post − A_pre (the chain's
post/pre in A; bigger is darker), averaged over the deep dot (at least 3 px
inside the dot's edge, off the pad's rim) minus the same average over the
dye-free white zone of the same pad (at least 6 px outside the dot).  That
gives one six-number vector per pad.  "Brightness" is the mean over the six
channels; the "centered" vector has it removed.  Region means have standard
errors of 0.001–0.003 A.

**Bill's guidance:** the lightest yellows have the least brown, and the
darkest browns have the least yellow.  So the endpoints are the lightest
yellow, C1034 (2.239 µg/L, smallest dye signal), and the darkest brown,
C1025 (160 µg/L).

**Dye vectors** (A, order violet, royal-blue, cyan, green Gb, green Gr,
photo-red):

| Pad | µg/L | violet | royal | cyan | Gb | Gr | photo-red |
|---|---|---|---|---|---|---|---|
| C1034 (yellow end) | 2.2 | 0.484 | 0.310 | 0.096 | 0.036 | 0.035 | 0.003 |
| C1032 | 5 | 0.604 | 0.551 | 0.331 | 0.176 | 0.173 | −0.002 |
| C1017 | 10 | 0.651 | 0.610 | 0.441 | 0.280 | 0.280 | 0.002 |
| C1036 | 40 | 0.670 | 0.679 | 0.601 | 0.508 | 0.504 | 0.027 |
| C1025 (brown end) | 160 | 0.743 | 0.755 | 0.691 | 0.605 | 0.604 | 0.161 |

Yellow dye absorbs mostly in violet and royal-blue and is nearly zero in
photo-red; brown adds absorbance through the green and, at the highest
amounts, the red.  Violet absorbance is already 0.48 at 2.2 µg/L and levels
off near 0.65–0.74.

**Findings**

1. **Two directions carry almost everything.**  After removing brightness
   the 18 vectors have singular values 2.13, 0.61, 0.098, 0.019, 0.007:
   92.3 %, 7.5 %, 0.2 %, 0.01 % of the variance.  The third is about 12 times
   the noise floor (about 0.008), so it is real but small.
2. **The color rotates smoothly from yellow to brown with concentration**
   (cosine of each pad's centered vector with C1025's): 0.62–0.69 at
   2.2 µg/L, 0.66–0.83 at 5, 0.90–0.91 at 10, 0.94–0.99 at 20, at least 0.99
   from 40 up.  This supports Bill's endpoint picture.
3. **Noise gain with these endpoints** (angle between the centered spectra
   52.1°; section 3.4): ‖m_y‖ = 2.93, ‖m_u‖ = 2.56 per unit of channel noise.
   Brown is **not** the noisier coordinate, contrary to my guess in 3.4: the
   photo-red channel sees brown and not yellow, which separates them.  The
   rows are orthogonal to brightness (1e-16).  Region-mean noise in the two
   coordinates is about 0.007 (yellow) and 0.004 (brown), in units where each
   endpoint pad is 1.
4. **Coordinates** (yellow a, brown b, endpoints = 1): at 2–5 µg/L a is 0.83
   to 1.07 and b is 0.07 to 0.44; at 10 µg/L a is 0.61–0.68 and b 0.62–0.67; at
   20 a is 0.19–0.54 and b 0.81–0.94; at 40 a is 0.03–0.13 and b 0.92–1.08.
   Yellow falls and brown rises as arsenic rises, as Bill said.
5. **The model is not exact.**  The two-endpoint fit leaves a residual up to
   12 % of a pad's vector length (6–18 times the noise) on the mid-range pads
   (4–10 µg/L); the best plane through all the data (not tied to endpoints)
   leaves 1–7 %.  The leftover direction is one number pattern (violet +0.39,
   royal −0.55, cyan −0.47, green +0.40, red −0.16) strongest at 5–10 µg/L: the
   path from yellow to brown is **curved** in A.  Converting to Kubelka–Munk
   K/S (assuming a white reflectance of 0.7–0.95) did not make it more linear.
   **[?]** Cause: the endpoints are not pure, the spectra change shape with
   amount, or a third chromophore.  A purer brown (overexposed pad), Bill's
   plan, would test the first.
6. **Same concentration, different pads.**  At 5 µg/L the 1 g pad C1020 has
   a = 1.00, b = 0.07, while the 20 g pads C1031 and C1032 have a = 0.83–0.88
   and b = 0.41–0.44.  At 2.2 µg/L the 1 g pad C1022 has b = 0.12 against
   C1034 (20 g), the yellow endpoint by definition.  The 1 g dots are 3.6–5.3
   times the hole's area, so the same gas may be spread thinner there.
   **[?]** Mechanism not tested.  The two sets should not be pooled on one
   concentration axis without a check.
7. **Pixel scatter inside the dot** (deviation from the pad's dot mean,
   share of the variance): along brightness 40–59 % (89 % on C1025), in the
   yellow–brown plane beyond that 7–31 %, in neither 4–28 %.  The last part is
   chromatic scatter (texture or noise), which the coordinate rows cannot
   remove and averaging over the dot reduces.

**Consequence for the plan [D].**  A two-coordinate (yellow, brown)
description with the brightness rows is workable and lowers noise, but
a straight two-endpoint linear model has a 6–18 % systematic at 4–10 µg/L.
Use the coordinates as **indices**, calibrated against arsenic with a curve
(and the curved path, finding 5), not as exact dye amounts.

### 9b. Split by hold-down force, and the third direction [N]

Scripts: `plane_split.py`, `plane_curve.py`, `plane_curve2.py` (session
scratchpad).  Bill's explanation of the two sets: with 1 g, more gas leaks
across the pad surface, so the exposed area is larger and the exposure less
intense; with 20 g, gas goes deeper into the paper before it leaks sideways,
so the dot is smaller and the exposure inside it more intense.  Bill also
notes there are three arsine binding sites; the literature says the first is
yellow and the third is brown, and Bill has been assuming the second is yellow.

1. **The two sets have the same color structure.**  After removing brightness
   the shares of variance are 92.1 / 7.7 / 0.2 % (1 g, nine pads) and
   92.4 / 7.3 / 0.2 % (20 g, nine pads).  The fourth singular value is
   0.004–0.013, at the noise floor, in both.  So within the noise the colors
   occupy three directions plus brightness.
2. **The third direction is the same in both sets.**  Its pattern across the
   channels (violet, royal-blue, cyan, Gb, Gr, photo-red) is
   (+0.37, −0.52, −0.50, +0.40, +0.40, −0.16) for 1 g and
   (+0.40, −0.57, −0.45, +0.41, +0.37, −0.16) for 20 g; cosine 0.997.
   So it is a stable spectral feature, not an effect of the holder.
3. **The two sets follow the same path.**
   - The yellow and brown endpoints match across the sets: cosine 0.992 for
     C1022 (1 g) with C1034 (20 g), 1.000 for C1025 with C1036, 0.996 for
     C1020 with C1034.
   - Pads with the same total dye signal have the same color: C1021 (1 g)
     with C1029 (20 g) has cosine 1.000 and difference 0.011 against noise
     0.008; C1024 with C1029 gives 1.000 and 0.017 against 0.010.
   - The third-direction score, plotted against the brown coordinate, falls
     on one curve for both sets: 0.07–0.09 (in units of A) at brown
     coordinate 0.3–0.7, 0.02–0.03 near 0.93, zero at both endpoints (by
     construction).
   So 1 g against 20 g moves a pad **along** one color path, as Bill
   describes (less intense versus more intense exposure); it does not change
   the path.  The earlier observation that C1020 (1 g) and C1031/C1032
   (20 g) differ at the same 5 µg/L is therefore a difference in dose, not in
   chemistry.
4. **Dose axis versus independent amounts.**  Fitting the other two scores as
   smooth functions of the yellow-to-brown score (PC2), the third score falls
   within 1.4–2.4 times the noise of one common curve (rms 0.007–0.012 against
   0.005), but the first score does not (0.028 against 0.005).  Fitting one
   set's curve and predicting the other's is no worse than fitting a set
   with its own curve (0.033 against 0.028).  So there is a second
   independent quantity beyond a single dose axis (the overall strength of
   the color, the first score), and it is a pad-to-pad difference, not a
   1 g versus 20 g difference.  **[?]** What it is.
5. **On the three binding sites [D].**  The data are consistent with three
   independent color directions (finding 1), which is what three species with
   different colors would give.  Two cautions.  (a) If the second site's
   spectrum equals the first's (both yellow), it adds no new direction: a
   third direction needs site 2 to differ in color from both sites 1 and 3.
   (b) A straight-line (additive) dye model failing for **two** dyes, because
   the response is not linear in amount, also produces a third direction.
   The data so far, nine pads per set with a gap between 5 and 10 µg/L in the
   1 g set, cannot tell these apart.  What would: pads between the
   endpoints at many doses (a dose series on one pad geometry), a purer
   brown (Bill's overexposed pad), and the third direction's score being
   monotone or not in dose.  Whether the third direction is "the second
   site" stays **[?]**; the numbers make it a measurable, stable quantity.
   The pattern (+, −, −, +, +, −) is not a simple shift of a band; not tested.
