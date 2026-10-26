# How light behaves in the pad: processes under consideration (2026-10-25)

Written for Bill, and for any Claude session working on the pad's texture,
edge darkening and "shadow" question.  Companion file with a short pad and
exposure summary to resume from: `docs/resume_prompt_pad_light_physics.md`.

**Status: a working note, not a result.**  Nothing here is adopted into the
analysis chain.  The question that started it: is "shadow" (the `b` in
`d = a·b·n`, `tex/analysis_chain.tex`) the right explanation for the pad's
pixel-scale brightness variation and the darkening at its edge, or is
photon travel inside the paper (how far light wanders sideways before it
leaves, and how much of the paper's depth it uses) a bigger cause?

## 0. How to read the tags

Every claim carries one tag, so you can tell what is established.

| Tag | Meaning |
|---|---|
| **[S]** | Taken from a source whose text I downloaded and read (the part quoted). |
| **[A]** | From an abstract, search listing or citation in another paper only; the source's text was not read. |
| **[D]** | Derived on this page (the algebra is shown). |
| **[N]** | A number computed in this work; the script is named. |
| **[B]** | Given by Bill (the pad, the geometry, the samples). |
| **[?]** | Unknown or untested. |

Scripts for **[N]** items are in the session scratchpad
`/tmp/claude-1000/-home-bill-Projects-Colorimeter/443cfeb5-2fe9-4ce5-aa38-2cf1db8e4880/scratchpad/`
(not in the repository): `mc_toy.py` (random walk), `derive_check.py` (spread
curve, Kubelka-Munk, cut edge), `derive_check2.py` (tilt by angle and
pressure only; its LED geometry is not used), `derive_check3.py` and
`derive_check3b.py` (geometry from the LED coordinates below), `testB.py`
and `testB2.py` (texture test B).

## 1. Plain meanings of the words used

- **Layer pixel**: one pixel of one Bayer layer (the grid every analysis
  figure uses).  About 0.12 mm on the pad **[N]**, section 2.
- **Absorbance A**: A = −log10(R_inf).  Bigger means darker.  0.01 A is
  about 2.3 % of the brightness.
- **Scattering**: light bounces off a fiber surface and changes direction.
  It is not lost.
- **Scattering distance** (mean free path): the average distance light goes
  between two bounces.
- **Sideways spread curve** (point spread function): light that enters the
  paper at one spot comes back out at a spread of places around it.  The
  curve says what fraction comes out at each distance.
- **Line spread**: the same curve added up along one direction, so it
  depends on one distance only.  Used for straight edges.
- **Contrast transfer** (modulation transfer function, MTF): if the light
  falling on the paper varies in a regular wave pattern, the picture of it
  coming back is a weaker wave.  The MTF is the ratio, for each wave
  spacing.  1 means no weakening; 0 means the wave is wiped out.
- **Optical thickness**: pad thickness divided by the scattering distance.
  Large means light rarely reaches the bottom.
- **Zenith angle** θ of a light: its angle from straight down, as seen from
  the pad.  **Azimuth** φ: its direction around the pad, measured from the
  +x axis.

## 2. The setup

### 2.1 From Bill [B]

- Pad: 5 mm wide (x), 6.5 mm long (y), 0.24 mm thick, white paper.  The pad
  is thought to contain no whiteners or brighteners and does not fluoresce
  under the violet LED.  The length 6.5 mm fits the image (54 layer pixels).
- Plastic backing 0.26–0.30 mm thick, white, slightly translucent (writing on
  the back is detectable but not readable).  Its reflectance is unknown.
- Pad edges are not shaded.  Each edge is lit obliquely by 2 LEDs of each
  color.
- 40 LEDs: 10 colors, 4 of each, each in a different place.  Each set of 4
  is arranged symmetrically about the pad.  Center locations and
  orientations in x, y, z Cartesian space (mm):

  | Item | x (mm) | y (mm) | z (mm) | orientation |
  | --- | --- | --- | --- | --- |
  | pad | 0 | 0 | 0 | faces up, size 5 by 6.5 mm (x by y) |
  | camera | 0 | 0 | 105 | faces down |
  | violet LED | ±44 | ±33 | 105 | faces down |
  | royal-blue LED | ±44 | ±29 | 105 | faces down |
  | blue LED | ±44 | ±25 | 105 | faces down |
  | cyan LED | ±44 | ±22 | 105 | faces down |
  | green LED | ±44 | ±18 | 105 | faces down |
  | amber LED | ±34 | ±33 | 105 | faces down |
  | red-orange LED | ±34 | ±29 | 105 | faces down |
  | red LED | ±34 | ±25 | 105 | faces down |
  | photo-red LED | ±34 | ±22 | 105 | faces down |
  | white LED | ±34 | ±18 | 105 | faces down |

  The four LEDs of a color are lit together in one exposure.

- Exposure setup.  The database is `src/colorimeter.sqlite`, table `samples`:
  `cap_style` holds the exposure method and `manual_read` holds the arsenic
  concentration in µg/L (`remark` sometimes repeats it).

  | Samples | `cap_style` | Description | Arsenic (`manual_read`, µg/L) |
  | --- | --- | --- | --- |
  | C1002–C1009 | whole pad | whole pad in the gas chamber, exposed to gas and splash droplets | 256, 128, 64, 64, 64, 32, 16, 16 |
  | C1010–C1013 | whole pad | whole pad in the gas chamber, gas only (splash protection from C1010) | 16, 5, 5, 25 |
  | C1014 | heart | other geometry; not used here | blank (remark: 10) |
  | C1015 | blank | not used here | blank |
  | C1016 | dot-2mm-1g | blank `manual_read`; remark says 10 | blank (remark: 10) |
  | C1017–C1025 | dot-2mm-1g | 2 mm hole, 1 g hold-down force | 10, 10, 10, 5, 20.02, 2.23, 39.99, 80, 160 |
  | C1028–C1036 | dot-2mm-20g | 2 mm hole, 20 g hold-down force | 20, 20, 20, 5, 5, 4.211, 2.239, 40, 40 |

  C1026 and C1027 are not in the database.  The **dot** samples (C1016,
  C1017–C1025, C1028–C1036) have the pad pressed onto a steel plate with a
  2 mm diameter hole for the gas, with splash protection.  The **whole pad**
  samples have no pre-image and are not used here.  The gas is mostly
  hydrogen with some arsine.  The pre-image is of a dry pad that has not
  been touched; pads are stored and shipped in bulk containers, touching
  each other.

### 2.2 Derived from the coordinates [N] (`derive_check3.py`)

Each LED's horizontal offset from the pad's center is √(x² + y²); its zenith
angle is θ = arctan(offset / 105 mm); its azimuth is φ = arctan(y / x).  The
shadow column is the horizontal length of the shadow of a wall as tall as
the whole pad, 0.24 mm × tan θ.  The edge-face columns are the light on a
pad edge face that faces two of the four LEDs, as a fraction of the light on
the top (section 4.2).

| Color | Offset (mm) | θ | φ | Shadow of 0.24 mm wall: mm (px) | Light on x-edge face / top | Light on y-edge face / top |
|---|---|---|---|---|---|---|
| violet | 55.0 | 27.7° | 36.9° | 0.126 (1.05) | 0.21 | 0.157 |
| royal-blue | 52.7 | 26.7° | 33.4° | 0.120 (1.00) | 0.21 | 0.138 |
| blue | 50.6 | 25.7° | 29.6° | 0.116 (0.96) | 0.21 | 0.119 |
| cyan | 49.2 | 25.1° | 26.6° | 0.112 (0.94) | 0.21 | 0.105 |
| green | 47.5 | 24.4° | 22.2° | 0.109 (0.91) | 0.21 | 0.086 |
| amber | 47.4 | 24.3° | 44.1° | 0.108 (0.90) | 0.16 | 0.157 |
| red-orange | 44.7 | 23.1° | 40.5° | 0.102 (0.85) | 0.16 | 0.138 |
| red | 42.2 | 21.9° | 36.3° | 0.096 (0.80) | 0.16 | 0.119 |
| photo-red | 40.5 | 21.1° | 32.9° | 0.093 (0.77) | 0.16 | 0.105 |
| white | 38.5 | 20.1° | 27.9° | 0.088 (0.73) | 0.16 | 0.086 |

Other derived numbers:

| Quantity | Value | How |
|---|---|---|
| Layer pixel on the pad | 0.119–0.120 mm | 5 mm / 42 px and 6.5 mm / 54 px (the fitted pad size in layer pixels) |
| Angle between a light and the camera, seen from the pad | 20.1° (white) to 27.7° (violet) | the camera is on the axis, so it equals the light's zenith angle |
| Camera's view of the pad corner | 2.2° off straight down | arctan(4.1 mm / 105 mm) |
| Width of a pad edge face the camera sees | 0 mm | The camera cannot see the pad edges |
| Fiber width | 0.020–0.035 mm, about 0.2–0.3 px | Linder 2014, Paper C **[S]** |

So a layer pixel spans four to six fibers.  The pixel-scale texture
(1–2 px, 0.12–0.24 mm) is made of clumps of fibers, not single fibers.  The
camera sees the pad's top face only.  Lights of different colors differ in
zenith angle by up to 7.5° and in azimuth by up to 22°, so their lighting
patterns are similar but not the same.  The three colors that share the B
layer (violet, royal-blue, cyan) are the closest: zenith 25.1°–27.7°,
azimuth 26.6°–36.9°.

## 3. The model the chain uses now

`tex/analysis_chain.tex` writes the photons reaching one pixel as

    d = a · b · n

`n` is the photons the LEDs send (set by exposure time and lamp brightness),
`b` is the fraction of that light which reaches the spot, and `a` is the
fraction the spot sends back to the camera (the paper and its dye).  The
ratio of post to pre cancels `b`, `n` cancels through the gray chips, and
what is left is the change in `a`.

This is exact **only if light leaves the paper from the same spot where it
entered.**  Section 4.5 shows why paper does not do that.

## 4. The processes

### 4.1 Light on the surface from a point LED [D, N]

An LED of intensity I (power per unit solid angle) sits at height h above a
flat surface and a horizontal distance s from the spot.  Define the
distance r = √(h² + s²) and the angle θ from straight down, so cos θ = h/r.
The irradiance (power per area) on a flat, horizontal spot is

    E = I · cos θ / r²  =  I · h / r³

Light adds, so with four LEDs of one color the total is the sum of four such
terms.  This is the smooth lighting pattern, and it is what the gray-chip
polynomial removes.  Over the 5 × 6.5 mm pad **[N]** (`derive_check3.py`,
`derive_check3b.py`) one LED's irradiance changes by 7 % (white) to 10 %
(violet) from the dim end to the bright end, but the four LEDs of a color
sit symmetrically, so their sum changes by only 0.09–0.15 %.  The smooth
lighting is nearly even, and any gradient left is tiny next to the 1.4–1.8 %
texture.

### 4.2 Cast shadow and edge-face light [D, N]

An object of height t lit from the zenith angle θ throws a shadow of
horizontal length

    length = t · tan θ

For the whole pad thickness, t = 0.24 mm, the longest possible shadow is
0.088 mm (white, 0.73 px) to 0.126 mm (violet, 1.05 px) (table, 2.2).  That
is for a wall as tall as the entire pad, and it falls on the plastic beside
the pad, not on the pad.  The pad's own top surface is not shadowed by its
own edge.

For a fiber-sized step, t = 0.03 mm, the shadow is 0.011–0.016 mm (0.1 px).
To cast a shadow one pixel long (0.12 mm) a bump must stand
0.12 / tan θ = 0.23 mm (violet) to 0.33 mm (white) above its surroundings,
as tall as the pad or taller.  Conclusions:

- Shadows from fibers and clumps are real but are at most a fraction of a
  pixel long unless the paper's relief is far larger than a fiber.  A
  one-pixel shadow would need relief of 0.23 mm or more.  **[?]** I do not
  know the relief of this paper.
- Shadows at the scale of the paper's own roughness (pores a few hundredths
  of a millimeter across) do darken the surface, in proportion to tan θ, so
  they differ between colors.  Averaged over a pixel they act as a property
  of the paper (how rough the pixel is), not as a pattern cast across
  pixels.  They are not separated from the paper's other properties here.

**Light on the pad's edge faces.**  A vertical face with outward normal along
+x receives, per unit face area, tan θ·cos φ times the irradiance a
horizontal surface gets from the same LED.  Two of the four LEDs of a color
face it.  Compared with the top, which gets all four, the face receives
0.5·tan θ·cos φ **[D, N]**: 0.21 (violet through green) or 0.16 (amber
through white) on the x faces, and 0.5·tan θ·sin φ, 0.157 (violet, amber) down to
0.086 (green, white), on the y faces (table, 2.2).  The edge faces are
therefore lit.  Light that enters a cut face spreads inward and some leaves
by the top, near the edge, so it offsets part of the sideways loss of 4.8.
Order of size: the face is 0.24 mm tall, so the light entering per unit edge
length is 0.24 mm × 0.21 = 0.05 mm times the overhead irradiance (x face,
violet), or 0.02 mm (y face, green).  The loss at the top edge in 4.8 is
L/2 = 0.05 mm for L = 0.1 mm.  The two are the same order, but how much of
the face light comes back out the top, instead of going down or being
absorbed, is not known.  **[?]**  Consequence: the edge depth should depend
on edge and on color (edge faces get different light), and the cut-edge fit
in 4.8, which treats the edge as unlit, is not a clean measure of L.

### 4.3 Tilt: a facet turned toward or away from an LED [D, N]

A small patch of the surface (a facet) whose normal is tilted by angle α
from straight up.  Take a Lambertian (ideally matte) facet: the radiance it
sends the camera does not depend on the viewing direction, so the pixel
signal is proportional to the irradiance per unit facet area,
E_f = I · cos(angle between the facet's normal and the light) / r².

Put the LED in the x–z plane, direction from the facet to the LED
l = (sin θ, 0, cos θ).  The facet's normal, tilted toward azimuth ψ
(measured from the LED's direction), is
n = (sin α cos ψ, sin α sin ψ, cos α).  Then

    n · l = cos α cos θ + sin α cos ψ sin θ

Relative to the flat facet (cos θ):

    signal ratio = cos α + tan θ · sin α · cos ψ  ≈  1 + α·tan θ·cos ψ − α²/2

(α in radians, small).  One LED, tan θ = 0.37 (white) to 0.52 (violet) **[N]**:
for α = 10° (0.175 rad) the first-order term is up to ±6 % to ±9 %, and for
α = 20° up to ±13 % to ±18 %.  That is large for one LED.

Four LEDs add, and the first-order term of each points along that LED's own
direction on the pad.  Their horizontal directions sum to zero at the pad's
center (mirror symmetry), so there the first-order terms cancel exactly.
Away from the center the cancellation is partial.  Computing the intensity-
weighted sum over the pad **[N]** (`derive_check3.py`), the leftover is at
most 0.028 (violet) to 0.033 (white) times α at the corner, that is 0.5–0.6 %
at 10° and about 1.0–1.2 % at 20°.  It is small, but not negligible next to
the 1.4–1.8 % texture.

What remains in the sum is the second-order term, −α²/2, and it is **always
a darkening**.  Exact values: cos α − 1 = −0.38 % (5°), −1.52 % (10°),
−6.0 % (20°) **[N]**.  Two consequences **[D]**:

- Tilt of 10° or more at the scale of a pixel is large enough to give a
  texture of the observed size (0.007 A is 1.6 %).  So tilt cannot be ruled
  out by size alone.
- Tilt can only darken in the four-LED sum (apart from the small leftover
  above), so it would skew the texture toward dark.  The inner pad's
  histogram leans slightly dark (skew −0.12,
  `docs/shading_study_2026-09-29.md`, R1d).  That is only a hint; other
  mechanisms can skew too.

Section 4.5 shows that a lighting pattern at the pixel's scale is weakened
by the sideways spread of light inside the paper.  **[?]** The facet tilts
of this paper are unknown.

### 4.3b Shadow hiding [A, D]

Looking along the direction of a light, you cannot see the shadows it casts,
because each lies behind the object that casts it.  This is the "shadow
hiding" part of the opposition effect studied for rough, grainy surfaces:
shadows cast on other grains become invisible as the angle between light and
viewer goes to zero.  One lunar study found the shadow-hiding part broad,
over angles up to about 20°, with a narrower coherent-backscatter spike under
about 2° (sources in section 8, **[A]**, search listings only).  Two cautions
from the same listings: shadow hiding alone does not explain the effect seen
in very reflective powders (magnesium oxide, barium sulfate), because
multiple scattering fills the shadows anyway; and the shares of the two
effects are disputed.

Here the angle between light and camera is 20°–28°, at or beyond the edge of
that range, so shadow hiding is at most a weak reduction of the shadows
and is **not relied on** in any conclusion.  The Oren–Nayar model of rough
matte surfaces (1995 **[A]**) is the closest ready-made description of
roughness shading.  **[?]** Not modeled and not measured.

### 4.4 Light inside the paper: a random walk [D, N]

A photon travels a distance s between scatterings, with probability density
μs · e^(−μs·s), where μs is the scattering coefficient.  Its average step is
1/μs.  After each scattering it is absorbed with probability 1 − ω (ω is
the fraction that survives each bounce; 1 means no absorption).  It leaves
when it returns to the surface.

**Sizes from the literature.**  The seven lowest-scattering sheets in
Linder's table (samples 1–4, 9, 10, 19; the table does not say whether they
contain filler) have μs of 19–66 mm⁻¹ **[S]**, so the average step is about
0.015–0.05 mm, about one fiber width.  Saying that the paper's scattering
distance is about one fiber width is my conversion **[D]**.

**Simulation [N] (`mc_toy.py`).**  The simplest case: isotropic
scattering (every bounce goes in a random direction), a flat surface,
no refraction at the surface, 400,000 photons, each entering straight down
at one point.  Distances are in scattering distances (about one fiber
width):

| ω | Fraction that comes back | Leave within 1 of the entry point | Within 2 | Within 8 | Leave after at most 3 bounces |
|---|---|---|---|---|---|
| 0.999 | 0.91 | 22 % | 43 % | 86 % | 35 % |
| 0.99 | 0.75 | 25 % | 50 % | 93 % | 42 % |
| 0.95 | 0.54 | 32 % | 60 % | 98 % | 55 % |

About a quarter to a third of the light comes out within one scattering
distance, and the rest wanders farther.  This is a toy, and the real paper
spreads light **more** than a model like this: Linder finds that his better
fiber-based Monte Carlo model still predicts less sideways spread than
measured (Paper C, discussion) **[S]**, and suggests light piping along
the hollow fiber centers or fibers interacting.  So the local fractions
above are probably upper bounds.  No source found reports the fraction of
photons that leave from the *same fiber* they entered.

### 4.5 Why `d = a·b·n` fails: the convolution model [D]

Let E(x′) be the irradiance entering the paper at position x′.  Let
h(x − x′) be the fraction of light entering at x′ (per area) that leaves
at x.  Its total, ∫h, is the paper's reflectance R_paper.  If the paper is
the same everywhere, the signal at x is the sum over all entry points:

    d(x) = n · ∫ E(x′) · h(x − x′) dx′          (a convolution, E ⊛ h)

So the camera sees the lighting **averaged** over the neighborhood the
light travels through, not the lighting at x.  `d = a·b·n` is the special
case where h is a single spike.

**Effect on a wave.**  Let E = E₀ (1 + m cos 2πux), a wave of spacing
w = 1/u.  Because h is symmetric, ∫h(x − x′) cos(2πux′) dx′ = H(u) cos(2πux),
where H(u) = ∫h(x) cos(2πux) dx.  So

    d(x) = n·E₀ · [R_paper + m·H(u)·cos(2πux)]

and the wave's contrast is multiplied by MTF(u) = H(u)/H(0).

**Exponential spread.**  Inoue, Tsumura and Miyake found the spread curve of
paper can be written as an exponential **[A, abstract only]**.  Take the
line spread to be l(x) = (1/2L)·e^(−|x|/L), L a length.  With k = 2πu:

    MTF(u) = (1/L) ∫₀^∞ e^(−x/L) cos(kx) dx
           = (1/L) · (1/L) / (1/L² + k²)
           = 1 / (1 + (2πuL)²)

(Checked numerically **[N]** at three spacings.)  The papers quote kp, the
wave spacing at which the MTF is one half.  Setting MTF = 0.5 gives
2πuL = 1, so

    kp = 2π·L        L = kp / 2π

**Sizes.**  Linder's simulated kp for an opaque pad of those same seven
lowest-scattering sheets is 0.69–0.88 mm (Paper C, Table 1) **[S]**, so
L = 0.11–0.14 mm.  Across all 22 papers kp is 0.15–0.88 mm, L = 0.024–0.14
mm.  Measured kp are larger than his simulations **[S]**, so these L are
floors.  I could not read the measured table in Arney, Arney, Katsube and
Engeldrum (1996) **[A]**.  Engeldrum's appendix gives the line-spread and
MTF formulas for Kubelka-Munk paper and says S for coated paper runs
5–150 mm⁻¹ **[S]**.

**How much a wave is weakened [D, N].**  MTF = 1/(1 + (2πL/w)²):

| Wave spacing w | L = 0.025 mm | L = 0.10 mm |
|---|---|---|
| 0.12 mm (1 px) | 0.37 | 0.035 |
| 0.24 mm (2 px) | 0.70 | 0.127 |
| 0.48 mm (4 px) | 0.90 | 0.37 |
| 1.0 mm | — | 0.72 |

(Computed by hand from the formula; the L = 0.10 column agrees with the
fitted edge in 4.8.)  So lighting patterns on the scale of a pixel (shadows,
tilt) reach the camera at a fraction of their strength, if L is about
0.1 mm.

### 4.6 Local factors at entry and at exit [D]

Suppose the paper (or a dye) changes the light by a factor T(x), once as the
light goes in at x′ and once as it comes out at x.  Then (Arney 1999,
eq. 6, for halftone dots on paper **[S]**):

    d(x) = n · T(x) · ∫ E(x′) · T(x′) · h(x − x′) dx′

The factor at the exit point acts at the spot itself and is **not**
blurred.  The factor at the entry point is blurred along with the lighting.
For a uniform factor, d = T²·(E ⊛ h): the absorbance of T is counted twice.

Consequences:

- A **lighting** pattern (shadow, tilt) enters only through E, so it is
  weakened as in 4.5.
- A pattern in the **paper's own properties** (local density, fiber
  alignment, how much light it returns) can act at exit unblurred.  The
  pixel-scale texture that survives the blur, and repeats 90–99 % from one
  exposure to the next (`docs/shading_study_2026-09-29.md`, step 1a), fits
  this better than a cast pattern.  **[?]** This is a consistency argument,
  not proof.
- Whether a dye acts like T (on the surface) or inside the paper's volume
  (4.7) matters for the dot.

### 4.7 Dye inside the paper, and a pad that is not infinitely thick [D, N]

**Kubelka-Munk, two flows.**  Let i be the light flowing down and j the
light flowing up at depth x.  S is the scattering coefficient and K the
absorption coefficient, per length.  Then

    di/dx = −(K+S)·i + S·j
    dj/dx = −S·i + (K+S)·j

*Thick paper.*  The ratio r = j/i is the same at every depth, so
dj/dx = r·di/dx.  Substituting:
(K+S)r − S = r[−(K+S) + S r], which gives S r² − 2(K+S) r + S = 0, i.e.
r² − 2a r + 1 = 0 with a = 1 + K/S.  The root below 1 is

    R_inf = a − b,    b = √(a² − 1)

For an infinitely thick pad the reflectance depends **only on K/S**.
If a patch of paper is denser, S and K both rise in proportion and R_inf
does not change.  A thick pad hides density variation.

*Thickness X over a backing of reflectance Rg* (solving the same equations
with j = Rg·i at depth X):

    R = [1 − Rg (a − b·coth(bSX))] / [a − Rg + b·coth(bSX)]

I checked it **[N]**: R → R_inf as X → large, and R → Rg as X → 0.

*How much a thickness change moves R.*  With R_inf = 0.85 (K/S = 0.013) and
S·X from 5 to 16 (my guess, using Linder's μs as if it were S, which is not
exact; take it as good to a factor of two), the change in R for a given
fractional change in thickness, dlnR/dlnX, is **[N]**:

| Backing Rg | S·X = 5 | 10 | 16 |
|---|---|---|---|
| 0.5 (darker than the paper) | +0.082 | +0.026 | +0.006 |
| 0.9 (brighter) | −0.020 | −0.009 | −0.002 |

A 20 % thickness (or mass) variation at S·X = 5 over a darker backing would
change R by 1.6 %, 0.007 A, the size of the texture.  If the backing is
about as bright as the paper, the effect is a few tenths of a percent.
**[?]** The backing's reflectance, S for this paper, and the pad's mass
variation are unknown.  Bill's note that writing on the back is detectable
shows light reaches the backing and returns, so the pad is not strictly
infinitely thick.

*Dye.*  Dye raises K.  Then K/S, a and b grow, coth(bSX) tends to 1, and
the table's sensitivity falls toward zero (computed rows K/S = 0.1 and 0.4
**[N]**): the dye hides the texture that comes from thickness and backing.
It also shortens the light's sideways reach.  In the diffusion picture the
fluence falls as e^(−r/δ) with δ = 1/√(3·μa·μtr′), μa the absorption and
μtr′ = μa + μs′ the transport coefficient (standard result, tissue optics
**[A]**: Farrell, Patterson and Wilson 1992; the formula is recalled, not
re-read).  More absorption means a smaller δ.

### 4.8 The cut edge [D, N]

Take the pad edge at x = 0, paper for x > 0, a uniformly lit top surface,
and the exponential line spread of 4.5.  A photon entering at distance x
from the edge would, without the edge, leave anywhere around its entry
point.  The fraction that would have left beyond the edge (distance more
than x away in that direction) is lost out the side or onto the backing:

    loss(x) = ∫ₓ^∞ (1/2L) e^(−s/L) ds = ½ · e^(−x/L)

So the reflectance at distance x from the edge is

    R(x) / R_bulk = 1 − ½ · e^(−x/L)

At the edge itself half the light is lost.  By reciprocity, the same holds
for entering and leaving positions.  A layer pixel covers [kp, (k+1)p],
p = 0.12 mm, so its mean is
1 − (L/2p)·(e^(−kp/L) − e^(−(k+1)p/L)).

This leaves out the light that enters the cut face itself (4.2), which
works the other way.

**Check against Bill's edge numbers [N]** (`derive_check.py`), the column
means in log10 across the pad, C1032 royal_blue.B pre-image
(`docs/shading_study_2026-09-29.md`, R1b):

| Edge | Measured, outermost → inward | Model, best single L | Fitted L |
|---|---|---|---|
| left | −0.15, −0.04, −0.01 | −0.149, −0.040, −0.012 | 0.100 mm |
| right | −0.075, −0.027, −0.011 | −0.080, −0.005, 0.000 | 0.043 mm |

The left edge is fitted well by L = 0.10 mm, which is inside the literature
range of 4.5.  The right edge's outermost value is fitted, but its tail is
heavier than one exponential gives.  This is one pad and one channel.  The
model leaves out lens blur (sharp strip edges measure 1.5 px or more, and
the pad's edge position is uncertain by a fraction of a pixel) and the
edge-face light.  It does not prove sideways loss.  It shows that sideways
loss alone gives about the observed size and width without any shadow.

### 4.9 Things that can change the paper between pre-image and post-image [?]

Bill describes: pads stored in bulk, touching each other; pre-image of a dry,
untouched pad; exposure to hydrogen with some arsine, protected from
liquid splash; the pad held against a stainless-steel plate with a 2 mm hole
by 1 g (C1016–C1025) or 20 g (C1028–C1036) spread over the whole face.

- **Pressure [D, N]:** 20 g over the 5 × 6.5 mm face (32.5 mm²) is
  0.196 N / 32.5 mm² = 6.0 kPa (1 g: 0.30 kPa).  This assumes the force
  spreads evenly.  A 2 mm hole removes 3.1 mm² of the 32.5 mm², so if the
  steel carries all of the force on the remaining ring, the pressure there
  is 6.7 kPa (1 g: 0.33 kPa), only 11 % more.  Whether that compacts
  fibers at the ring is **[?]**.  The two sets differ twenty-fold in
  force, which gives a test (section 7, item 3).
- **Dot size against the hole [N]:** a 2 mm hole has radius 1.0 mm = 8.3
  layer pixels.  The measured dot of C1032 (20 g) has an equal-area radius
  of 9.4 px = 1.13 mm, 1.27 times the hole's area: the dye reaches about
  0.13 mm past the hole's edge, which is a little more than one pixel.
  The 1 g dots are far larger: C1024 15.9 px = 1.9 mm (3.6 times the
  hole's area) and C1025 19.2 px = 2.3 mm (5.3 times, a diameter of 4.6 mm
  on a pad 5 mm wide).  A pad held by 1 g against the steel may not seal,
  so gas could creep sideways between pad and plate.  **[?]**  That is a
  guess, not a measurement.
- **Moisture:** the gas may carry some.  Whether the pad swells or dries
  unevenly **[?]**.
- **Reaction product:** the colored product sits in or on the fibers.
  Whether it sits at the surface (acting like T in 4.6) or in the volume
  (4.7) **[?]**.
- **Handling in bulk containers:** pads touch each other; scuffed or
  crushed edges are possible **[?]**.

## 5. What each idea predicts

"Shadow" is really three things plus the paper's own properties:

| Mechanism | Acts through | How it looks in the data |
|---|---|---|
| Cast shadow | lighting E, blocked | Length t·tan θ, 0.088–0.126 mm for a wall as tall as the pad, about 0.1 px for a fiber-sized step; one pixel needs relief of 0.23 mm or more; longer and in a different direction for violet than for white (θ 27.7° against 20.1°, φ 37° against 28°); weakened by the blur of 4.5 |
| Tilt | lighting E, angle | First order mostly cancels in the four-LED sum (leftover 0.03·α); second order only darkens, −α²/2, 1.5 % at 10°; weakened by the blur of 4.5 |
| Sideways loss at the cut edge | the paper's spread h | Width about L (about 0.1 mm); falls as e^(−x/L); L may change a little with color and with dye |
| Light entering the edge face | lighting of the face | Brightens the edge; depends on edge and color (x faces 0.21 or 0.16, y faces 0.086–0.157 of the top's light) |
| Local paper properties (density, alignment, roughness) | exit factor T | Not blurred; the same pattern in every color, up to a color-dependent strength |
| Thickness or backing show-through | finite-thickness term | Hidden by dye (the sensitivity falls); depends on the backing's reflectance |

Test B (section 6) found that the dye's presence *reduces* how much of the
pre-image's texture carries into the post-image.  A picture in which dye
shortens the light's paths and so raises the texture predicts the opposite;
the thickness mechanism predicts the observed direction.

## 6. What the data say so far

- **Texture repeats** 90–99 % between exposures and moves with the strip
  (shading study, step 1a).
- **Edge:** darkening 2–3 px wide (0.24–0.36 mm), up to 30 % in the outermost
  column on C1032 royal_blue.B (shading review R1b); consistent with 4.8
  (not corrected for edge-face light).
- **Color-to-color agreement of fine texture:** 0.50 at 1 px, 0.70 at 2 px,
  0.80 at 4 px (median over pads and LED pairs; shading study).  Lights of
  different colors differ by up to 7.5° in zenith and 22° in azimuth
  (section 2.2), so their shadow and tilt patterns differ somewhat.  The
  agreement could therefore reflect the lighting, the dependence of the
  paper's response on color, or the half-pixel offsets between Bayer
  layers.  Not yet separated.
- **Test B [N] (`testB.py`, `testB2.py`), red channel, all 18 pads:**
  the slope of the post-image's pixel-scale A on the pre-image's
  is 0.4–0.8 on the white zone and lower in the dot on 16 of 18 pads;
  weighted mean drop 0.29 ± 0.03.  Examples: C1025 0.82 ± 0.09 → 0.30 ± 0.08;
  C1024 0.56 ± 0.07 → 0.26 ± 0.09.  The drop also occurs where the dot is
  invisible in red (C1029: dot median A = 0.008, slope 0.73 → 0.04), so it
  is not caused by red absorption.  In blue and green the dye's own
  pixel-scale patchiness (0.02–0.05 A) hides the texture.  Result:
  inconclusive about mechanism; shows a simple factor model does not carry
  the texture into the dot.
- **R2 (earlier):** the correlation between post and pre was 0.20 in the dye
  and 0.39 in the inner dye-free pad on C1032 royal_blue.B; test B agrees.

## 7. Tests under consideration (nothing run beyond B)

1. **Control for test B.**  Bin the white zone's slope by distance from the
   dot's edge.  Does it recover with distance?  Separates "the dot area's
   paper changed" from "a different part of the pad".
2. **Edge profile by color and by edge.**  The four edges get different
   face light by color (section 2.2): x faces 0.21 or 0.16, y faces 0.086
   to 0.157 of the top's light.  Is the edge's depth and width the same in
   all six channels and on all four edges?  Sideways loss predicts one
   width per channel, narrower where the paper is darker; face light
   predicts shallower edges where the face is brighter.
   (`docs/edge_profile_plan_2026-10-23.md` is already working on this.)
3. **Test B split by hold-down force.**  Do the 1 g pads (C1017–C1025) and
   the 20 g pads (C1028–C1036) show the same drop in slope?  A pressure
   effect predicts a larger drop at 20 g.
4. **Test A: color-to-color texture.**  Correlate fine texture between
   violet.B, royal_blue.B and cyan.B on the white zone.  These share the B
   layer, so the half-pixel offsets between layers do not matter, and
   camera noise allows about 0.9.  The three are lit from nearly the same
   direction (zenith 25.1°–27.7°, azimuth 26.6°–36.9°), so a high
   agreement cannot separate lighting from paper; a clearly lower one
   shows the texture depends on color.
5. **Rotate the pad by 90° and re-image** (new captures).  The paper's
   texture turns with the pad.  A lighting pattern stays tied to the LEDs,
   and their offsets (44 mm by 33 mm) are not the same after a 90° turn, so
   it changes.  This separates paper from lighting more cleanly than any
   test on existing images.
6. **Fiber-scale height** **[?]** of the paper's surface, to put a number on
   the longest cast shadows.

## 8. References and what I actually read

| # | Reference | Status | Used for |
|---|---|---|---|
| 1 | J. S. Arney, "The Physics Behind the Yule-Nielsen Equation," IS&T PICS 1999, [PDF](https://www.imaging.org/common/uploaded%20files/pdfs/Papers/1999/PICS-0-42/1065.pdf) | **[S]** downloaded; read the parts on the PSF model and eq. (6) | 4.5, 4.6 |
| 2 | D. Modrić, S. Bolanča and R. Beuc, "Monte Carlo Modeling of Light Scattering in Paper," J. Imaging Sci. Technol. 53(2), 020201 (2009), [PDF](https://library.imaging.org/admin/apis/public/api/ist/website/downloadArticle/jist/53/2/art00002) | **[S]** abstract and introduction only | background on optical dot gain |
| 3 | T. Linder, "Light Scattering in Fiber-based Materials," doctoral thesis, Luleå University of Technology (2014), ISBN 978-91-7439-994-3, [PDF](https://www.diva-portal.org/smash/get/diva2:998871/FULLTEXT01.pdf); Paper C, "Lateral light scattering in fibrous media" | **[S]** Paper C's method, Table 1 and discussion; parts of the rest | 4.4, 4.5, fiber sizes |
| 4 | P. G. Engeldrum, "Paper Substrate Spread Function and the MTF of Photographic Paper," IS&T PICS 2003, [PDF](https://www.imaging.org/common/uploaded%20files/pdfs/Papers/2003/PICS-0-287/8514.pdf) | **[S]** appendix formulas and the S range | 4.5 |
| 5 | S. Inoue, N. Tsumura and Y. Miyake, "Measuring MTF of Paper by Sinusoidal Test Pattern Projection," J. Imaging Sci. Technol. 41(6), 657 (1997), [abstract](https://www.mi.tj.chiba-u.jp/~tsumura/Tsumura/papers/JIST97_inoue1.htm) | **[A]** abstract: PSF can be an exponential | 4.5 |
| 6 | J. S. Arney, C. D. Arney, M. Katsube and P. G. Engeldrum, "An MTF analysis of papers," J. Imaging Sci. Technol. 40, 19 (1996) | **[A]** cited in 3 and 4; not accessed | measured kp |
| 7 | L. G. Coppel, M. Neuman and P. Edström, "Lateral light scattering in paper: MTF simulation and measurement," Optics Express (2011), [PubMed 22273909](https://pubmed.ncbi.nlm.nih.gov/22273909) | **[A]** search listing and Linder's description | measured vs. simulated kp |
| 8 | M. Oren and S. K. Nayar, "Generalization of the Lambertian model and implications for machine vision," Int. J. Computer Vision 14 (1995); [Wikipedia summary](https://en.wikipedia.org/wiki/Oren%E2%80%93Nayar_reflectance_model) | **[A]** search listings and the Wikipedia article | 4.3, 4.3b |
| 8b | [Opposition surge (Wikipedia)](https://en.wikipedia.org/wiki/Opposition_surge); NASA technical report listings [19940016173](https://ntrs.nasa.gov/citations/19940016173) and [20040062367](https://ntrs.nasa.gov/citations/20040062367); a lunar opposition-effect study (listing only) | **[A]** search listings; none of the texts read; Hapke's own papers not found | 4.3b |
| 9 | T. J. Farrell, M. S. Patterson and B. Wilson, "A diffusion theory model of spatially resolved, steady-state diffuse reflectance...," Med. Phys. 19, 879 (1992) | **[A]** search listing; formula recalled | 4.7 |
| 10 | P. Kubelka and F. Munk, "Ein Beitrag zur Optik der Farbanstriche," Z. Tech. Phys. 12 (1931); P. Kubelka, J. Opt. Soc. Am. 38, 448 (1948) | not accessed; the two-flow algebra in 4.7 was derived here and checked numerically | 4.7 |
| 11 | J. A. C. Yule and W. J. Nielsen, "The penetration of light into paper and its effect on halftone reproduction," TAGA Proc. 3, 65 (1951) | **[A]** cited in 2 and 3 | background |
| 12 | ISO 9416, paper: determination of light scattering and absorption coefficients (Kubelka-Munk) | **[A]** listing: white and near-white uncoated paper, 457 nm | background |
| 13 | G. L. Rogers, "Effect of light scatter on halftone color," J. Opt. Soc. Am. A 17, 1813 (1998); and Appl. Opt. 37, 7235 (1998) | **[A]** cited in 4; not accessed | background |

Searches that found nothing usable: the fraction of photons that leave from
the same fiber they entered (no source reports it); measured paper spread
widths in micrometers (only kp values in papers I could not open); a
plain-paper S at 550 nm (the standard is at 457 nm).

## 9. Open items

1. The pixel scale (0.12 mm per layer pixel) is a fit to the pad's size in
   pixels; confirm it with the camera's calibration if one exists.
2. Why the 1 g dots (radius 1.9–2.3 mm) are larger than the 2 mm hole
   (4.9).
3. Backing reflectance, the paper's S, mass variation of the pad, relief
   of the paper's surface.  A test is proposed in
   `docs/color_coordinates_brightness_2026-10-25.md`, section 7.
4. Whether L is the same in dye-free and dyed paper (test 2 above).
5. Nothing here is committed.  Date and file name are the only record.
