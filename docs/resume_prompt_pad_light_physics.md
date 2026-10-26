# Resume prompt: the pad, the exposure geometry, and the light (2026-10-25)

For Claude, at the start of any session on the pad's texture, its edge
darkening, or the "shadow" question.  **Read `How_best_to_help_Bill.md` in
full first.**  Then read `docs/pad_light_physics_2026-10-25.md` (the
processes, derivations, the LED coordinates, the sample table and the
references).  Facts below marked *Bill* were told to us by Bill; *measured*
means taken from our images; *derived* means computed by us.  Where
something is unknown it says so.

## What this work is for

Decide whether "shadow" (the `b` in `d = a·b·n`, `tex/analysis_chain.tex`)
is the right explanation for the pad's pixel-scale brightness variation
and its edge darkening, or whether photon travel inside the paper is a
bigger cause.  It could change how the chain removes shading (the
pre-image division methods A, B1, B2, C, D in
`docs/shading_study_2026-09-29.md`, R3).  Stop when the mechanism is clear
enough to choose among those methods, or when two more tests do not narrow it.

## The pad

- Paper pad, white, 5 mm wide (x) × 6.5 mm long (y) × 0.24 mm thick (*Bill*).
  Thought to contain no whiteners or brighteners; does not fluoresce under
  the violet LED, as writing paper does (*Bill*).  Reagent: mercuric bromide
  (project memory).  The pad's fiber type, density, filler and surface
  height variation are **unknown**.
- Stuck to a white plastic backing, 0.26–0.30 mm thick, slightly
  translucent: writing on the back is detectable but not readable (*Bill*).
  The backing's reflectance is **unknown**.
- All four pad edges are fully exposed to the lighting (*Bill*).
- Storage and shipping: pads in bulk containers, touching each other (*Bill*).
  The pre-image is of a dry pad that has not been touched (*Bill*).

## The exposure to gas

- Gas is mostly hydrogen with some arsine (*Bill*).  Whether it carries
  moisture is **unknown**.
- **Dot samples:** the pad is pressed against a stainless-steel plate with a
  hole **2 mm in diameter**, with splash protection (*Bill*).  The hold-down
  force over the whole face is 1 g for C1016–C1025 and 20 g for
  C1028–C1036 (`cap_style` dot-2mm-1g and dot-2mm-20g in
  `src/colorimeter.sqlite`, table `samples`).  Derived pressure if spread
  evenly over the 32.5 mm² face: 0.30 kPa (1 g), 6.0 kPa (20 g); if the steel
  carries all the force on the ring around the hole, 0.33 and 6.7 kPa.
- **Whole-pad samples** (C1002–C1013, whole pad in the gas chamber; splash
  protection from C1010) have no pre-image and are not used here.
- Arsenic (`manual_read`, µg/L): C1017–C1025 are 10, 10, 10, 5, 20.02, 2.23,
  39.99, 80, 160; C1028–C1036 are 20, 20, 20, 5, 5, 4.211, 2.239, 40, 40.
  C1016's `manual_read` is blank (remark: 10).  C1026 and C1027 are not in
  the database.  Most brown: C1025 (160) and C1024 (80).
- Dot size on our samples (measured, radius of a circle of the same area,
  layer pixels × 0.12 mm): C1032 (20 g) 9.4 px = 1.13 mm; C1024 (1 g) 15.9 px
  = 1.9 mm; C1025 (1 g) 19.2 px = 2.3 mm.  A 2 mm hole has radius 1.0 mm =
  8.3 px.  C1032 is 1.27 times the hole's area.  C1024 and C1025 are 3.6
  and 5.3 times, so the 1 g dots are much larger than the hole.  Why is
  **unknown**; a pad held by 1 g may not seal against the plate and let gas
  creep sideways (a guess).

## Lighting and camera (*Bill*, coordinates in mm; pad center at the origin)

- Camera at (0, 0, 105), facing down.  40 LEDs, all at z = 105, facing down,
  4 per color, in the mirror-symmetric positions (±x, ±y):

  | Color | x | y | | Color | x | y |
  |---|---|---|---|---|---|---|
  | violet | 44 | 33 | | amber | 34 | 33 |
  | royal-blue | 44 | 29 | | red-orange | 34 | 29 |
  | blue | 44 | 25 | | red | 34 | 25 |
  | cyan | 44 | 22 | | photo-red | 34 | 22 |
  | green | 44 | 18 | | white | 34 | 18 |

  Each pad edge is lit obliquely by 2 LEDs of each color.  The 4 LEDs of a
  color are lit together (*Bill*).
- Derived: the light's angle from straight down (zenith) is 20.1° (white) to
  27.7° (violet); the azimuth is 22°–44°.  The angle between any light and
  the camera, seen from the pad, is the same 20°–28°.  The camera sees the
  top face only (it sees an edge face 0.009 mm wide).
- Derived: a wall as tall as the pad (0.24 mm) casts a shadow 0.088–0.126 mm
  long (0.73–1.05 px); a fiber-sized step (0.03 mm) 0.011–0.016 mm (0.1 px).
  A one-pixel shadow needs relief of 0.23 mm or more.
- Derived: each pad edge face gets 0.5·tan θ·cos φ (x faces: 0.21 violet to
  green, 0.16 amber to white) or 0.5·tan θ·sin φ (y faces: 0.086 green and
  white up to 0.157 violet and amber) of the light on the top.  The edge
  faces are lit.
- Derived: one LED's irradiance varies 7–10 % across the pad; the four
  LEDs of a color together vary only 0.09–0.15 %.
- Pixel scale (derived): the pad is 42 × 54 layer pixels, so 0.12 mm per layer
  pixel.  A fiber (0.02–0.035 mm) is a quarter of a pixel; a pixel spans
  about 4–6 fibers.
- The channels used: violet.B, royal_blue.B, cyan.B, green.Gb, green.Gr,
  photo_red.R (the six all 18 strips share).  `.B` channels share the
  blue Bayer layer.

## What is established, with numbers

All A are absorbance: bigger means darker.

| Finding | Number | Where |
|---|---|---|
| Pixel-scale texture is the paper's, not camera noise | 90–99 % repeats between exposures | `shading_study` step 1a |
| Camera noise per pixel | about 0.002 A | `How_best_to_help_Bill.md` §6 |
| Paper texture per pixel | 0.006–0.008 A (1.4–1.8 %) | same |
| Texture size | 1–2 px (0.12–0.24 mm) | `shading_study` step 1c |
| Edge darkening | 2–3 px wide (0.24–0.36 mm), up to 30 % in the outermost column (C1032 royal_blue.B) | `shading_review` R1b |
| Color-to-color agreement of fine texture | 0.50 / 0.70 / 0.80 at 1 / 2 / 4 px | `shading_study` step 1a |
| Texture carries into the dot less than onto the white zone | slope drop 0.29 ± 0.03, red channel, 16 of 18 pads; also where red absorbance in the dot is ~0 | test B (below) |

## What we concluded so far, and how firmly

1. The chain's `d = a·b·n` assumes light leaves where it entered.  In paper
   it does not (physics note section 4.5).  Firm, from sources.
2. Cast shadow: a pixel-long shadow needs relief of 0.23 mm or more, which
   is as tall as the pad, and fiber-sized steps cast about 0.1 px.  The
   pad's own edge cannot shadow its own top.  So shadow is **unlikely** to
   make the 1–2 px texture or the 2–3 px edge darkening unless the paper's
   relief is much larger than a fiber; relief is **unknown**.  Roughness
   shadows at the pore scale do exist, depend on color through tan θ, and
   act as a paper property.
3. Tilt of the paper's tiny surfaces is not ruled out.  One LED's first-order
   effect is ±6–9 % at 10° tilt, but it mostly cancels in the four-LED sum
   (leftover at most 0.03·α, 0.5–0.6 % at 10°).  What remains is darkening
   only, −α²/2 (1.5 % at 10°, 6 % at 20°), the size of the texture.  It
   would skew the texture dark, and the inner pad leans slightly dark (skew
   −0.12).  The sideways blur of light in the paper weakens such a pattern
   at the pixel scale (4.5).  Open.
4. Sideways loss at a cut edge (`R/R_bulk = 1 − ½·e^(−x/L)`) reproduces
   the left-edge profile of C1032 royal_blue.B with L = 0.10 mm, inside
   the literature range (0.024–0.14 mm, and measured spread is larger than
   simulated).  The right edge fits only its outermost value.  One pad and
   one channel; **not proof** (section 4.8).  The model ignores light
   entering the lit edge faces, which is the same order of size as the
   loss and varies with color and edge (4.2), so the fitted L is not a
   clean number.
5. A toy random walk has a quarter to a third of photons leaving within one
   scattering distance of entry.  Real paper spreads more, so that is an
   upper bound.  No source gives the fraction leaving from the *same fiber*.
6. A picture in which dye shortens the light's paths and so raises the
   texture predicts the opposite of test B.  Do not use it.
7. **Not established:** which mechanism makes the pixel-scale texture, and
   why it carries less strongly into the dot.

## Test B, in one paragraph (so a new session need not redo it)

For each pad and channel: fine A = A minus its 2 px smooth (pad pixels
only).  Slope = least-squares slope of the post-image's fine A on the
pre-image's (lined up by `analysis_1.pre_to_post`), in the white zone
(at least 6 px outside the dot, off the 4 px rim) and in the deep dot
(at least 3 px inside the dot's edge, off the rim).  A pure factor
predicts equal slopes.  Blue and green are unusable (the dye's own
patchiness is 3–6× the texture).  Red: white slopes 0.43–0.82, dot
slopes 0.04–0.80, 16 of 18 pads lower.  C1025: 0.82 → 0.30; C1024: 0.56 →
0.26.  Code (not in the repo, under
`/tmp/claude-1000/-home-bill-Projects-Colorimeter/443cfeb5-2fe9-4ce5-aa38-2cf1db8e4880/scratchpad/`):
`testB.py` and `testB2.py`; they use `shading_review.post_pre`.

## Open items (ask Bill, one at a time)

1. Why the 1 g dots are larger than the 2 mm hole.
2. Backing reflectance and translucency (test proposed in
   `docs/color_coordinates_brightness_2026-10-25.md`, section 7).
3. Whether the gas stream is dry.
4. The color-coordinate simplification (sum first, yellow and brown, fitted plane in section 9
   directions, edge factor): see `docs/color_coordinates_brightness_2026-10-25.md`.
5. Tests proposed, not run (details in the physics note, section 7), in the
   recommended order: **control for B** (slope against distance from the
   dot's edge); **edge profile by color and edge** (face light differs by
   edge and color; `docs/edge_profile_plan_2026-10-23.md`); **test B split
   by 1 g and 20 g**; **test A** (texture correlation among violet.B,
   royal_blue.B, cyan.B, which are lit from nearly the same direction, so
   it cannot separate lighting from paper); **rotate the pad 90° and
   re-image** (new captures; the cleanest separation of paper from
   lighting).

## How to work with Bill on this (from `How_best_to_help_Bill.md`)

- Treat Bill's physical hunches as hypotheses to test with a figure.  This
  line of work began as one of them (photon travel, fiber exposure).  So far
  the evidence says cast shadow is unlikely to be the texture's main cause
  and has not settled the rest.
- Propose, then **wait**.  State the question, the decision it could change,
  and what result would end it, in two or three lines, before any test.
- Plain English, words before symbols, U.S. spelling, say which way is
  darker, and number every question with a recommendation at the end.
- Say what ran, what finished, and what did not.  Do not say "verified"
  unless it ran here and you looked at the output.
- Nothing from this session is committed; the two documents are untracked
  files.  Do not start processes without `timeout -k 10 <seconds>`, and
  check for survivors before finishing (`CLAUDE.md`).
