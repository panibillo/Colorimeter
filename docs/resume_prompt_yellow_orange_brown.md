# Resume prompt: yellow, orange and brown (chain step 3, 2026-11-16)

For Claude, at the start of any session on the pad's colors: how many
there are, their reference lists, the unmixing matrix, and the amounts per
pixel.  **Read `How_best_to_help_Bill.md` in full first.**  Then read the
memory note `project_yellow_brown_3c.md` and step 3 of
`docs/work_plan_chain_poc_2026-11-09.md`.  Facts marked *Bill* came from
Bill; *measured* means computed from our images in the session named.
Regenerate any number before relying on it; don't recall it.

## What this work is for

- **Question:** how many colors does the stain hold, and what is each
  one's list of absorbances over the channels?  Bill's hypothesis
  (2026-11-15/16): an arsenic atom bound to one mercury bromide group is
  yellow, to two is a darker orange-like color, to three is brown.
- **Decision it could change:** whether the unmixing matrix M has two or
  three color columns, and which LEDs and camera layers the instrument has
  to capture.  Later, whether arsenic can be counted from the amounts
  (chain step 9, `tex/chain_unified.tex`, hypotheses H1/H2).
- **For now Bill's aim is telling colors apart, not calibrating to
  arsenic** (Bill, 2026-11-16).

## Words used here

| Word | Meaning | Direction |
|---|---|---|
| channel | one LED and one camera layer, e.g. `cyan.Gb` | |
| A | absorbance, A = −log10(R_post / R_pre lined up onto the post-image); R is the pixel divided by the gray-chip level at that pixel | bigger = darker; 0 = no change |
| K/S | Kubelka–Munk, (1 − R)² / (2R), with R = 10^−A | bigger = darker |
| interior | pad pixels 8 or more layer pixels from every edge (`white_study.zones_of`) | |
| noise unit | a channel's camera noise for one pixel's A, from the spread between the 4 repeat photos (post and pre-image combined), 0.003–0.007 A | |
| direction | a list of numbers, one per channel; principal components find the directions the pixels spread along most | |
| spread | the average squared distance of the pixels along a direction, noise units squared | 1 = camera noise only |
| even darkening (gamma) | the same added A in every channel; the list of all ones | |
| reference list (y, o, b) | the average A over one strip's interior: the yellow, orange and brown "colors" | |
| amount (c_Y, c_O, c_B) | how much of a reference color a pixel holds; 1 = as much as the reference strip's average | below 0 = outside what positive amounts can make |
| leftover | what the fit cannot describe: root mean square over the channels, A (3g); or the length left after k directions, noise units, scaled so noise alone gives about 1 (3f) | |

## The strips

All holder H (`cap_style = H1-cassette`), 4 repeat photos per LED, averaged
on the signal before any ratio (`shading_review.frame`, Bill 2026-11-15).

| Strip | As, µg/L | Pre-image | Notes |
|---|---|---|---|
| C1049 | 20 | 5 LEDs | dark dot in center; file renamed (inside it still says C1048) |
| C1050 | 600 | 5 LEDs | brown |
| C1051 | 12 | 5 LEDs | yellow |
| C1052 | 12.8 | 9 LEDs, all layers | yellow reference since 3g |
| C1053 | 20 | 9 LEDs, all layers | the one strip not used as a reference |
| C1054 | 40 | 9 LEDs, all layers | **file `captures/C1054-a.h5`**; `C1054.h5` is an abandoned first try (pre-image photo_red and red only, no post-image); orange reference |
| C1055 | 600 | 9 LEDs, all layers | brown reference |

- C1052–C1055 were captured 2026-11-16 with `capture.save_all_layers` = 1
  and N = 4 photos at Tus and at Bus.  Their sample rows were copied into
  the local `src/colorimeter.sqlite` from the Pi copy
  `src/colorimeter_2026-11-16.sqlite`, with `cap_style = H1-cassette`
  (pad position and width match C1050/C1051, *measured*) and
  `manual_read` = the amounts above (*Bill*).  Backup:
  `src/colorimeter.sqlite.bak-2026-11-16` (taken 08:51, before those rows).
- Bill sees "large dot patterns" on C1054 and C1055 (*Bill*), and much
  orange on C1054.

## Method in force (C1052–C1055)

1. Channels: every LED but white (no pre-image) × every camera layer with
   a usable flat-field model (`analysis_1.models()`, `usable`), accepted by
   `analysis_1.rung_verdict` in the pre- and post-image of every strip,
   with 2 or more photos.  **22 channels.**  Dropped: 13 weak layers with no
   flat-field model (violet.Gb/Gr/R, royal_blue.Gb/Gr/R, blue.R, cyan.R,
   green.R, amber.B, red_orange.B, red.B, photo_red.B; calibration p90 only
   15–86 DN), and red.R (C1054-a's pre-image has one photo of it).
2. A per pixel and channel, from each strip's **own** pre-image
   (`color_study.pca_data_3f`, the same arithmetic as `pair_A`).
3. Interior pixels only, about 900 per strip.  **The pad rim was not
   studied.**
4. Each channel's Tus rung is used as `rung_verdict` picks it; Bus frames
   are not combined with Tus.

## Results

Earlier (C1049–C1051, 9 channels, 2026-11-15, commits 0100d48, cf578de):
C1049's dot center fit no positive mix of yellow (C1051) and brown (C1050);
a third reference from the dot center cut its leftover 0.024 → 0.011 A.
Only photo_red separated brown from the dot color among those 9 channels;
amber and red_orange were expected to help and needed pre-image whites.
That is why the 2026-11-16 strips have 9-LED pre-images.

**3f — how many colors** (*measured* 2026-11-16, commit 09a28b2;
`color_count_3f`; figures `fig/color_3f_*.png`).  Through zero, noise units:

| Run | Spread, directions 1–5 |
|---|---|
| 22 channels, darkening kept | 125 801, 6 266, 981, 35, 18 |
| 22 channels, darkening removed | 36 577, 1 881, 181, 35, 18 |
| one layer per LED (9), darkening kept | 70 760, 3 747, 555, 15, 7 |

- **Three color directions.  The even darkening is not a fourth** (Bill
  predicted 4 with it kept): leftover after 3 directions is the same with
  and without it (C1054: 2.83 vs 2.80).
- With the darkening removed, two directions leave C1054 at 5.1 and C1055
  at 5.0 noise units: yellow + brown + brightness can't describe them.
- Directions 4 and 5 are camera-layer differences (Gb against Gr, B against
  G within one LED): speckled maps, lines at the bottom edge of C1054/C1055.
  Not dye.
- After 3 directions the floor is about 2 noise units (about 0.009 A), the
  size of paper texture, as in 3d.

**3f-2 — leave one strip out** (commit da3033f; `leave_one_out_3f`):

| Left out | Others' spread, directions 1–4 | Left-out strip's leftover after 3 |
|---|---|---|
| C1052 | 159 076, 5 888, 479, 41 | 2.1 |
| C1053 | 158 449, 7 000, 1 256, 41 | 1.8 |
| C1054 | 124 986, 6 430, 107, 28 | 3.6 (2.8 included) |
| C1055 | 66 826, 2 108, 29, 25 | 45 |

Orange is not carried by C1054 alone (C1053 holds some); brown is found
only in C1055.

**3g — three reference colors** (commit da3033f; `three_refs_3g`,
`show_3g`; `fig/color_3g_refs.png`, `color_3g_maps.png`).  References:
yellow = C1052 interior mean, orange = C1054, brown = C1055 (Bill,
2026-11-16, first pass).  M = (SᵀS)⁻¹Sᵀ with S = [1 y b o].

- C1053 (the real test): two colors give yellow 0.60, brown 0.24, a false
  darkening −0.055 A, leftover 0.0104 A; three colors give **yellow 0.54,
  orange 0.23, brown 0.02**, darkening 0.00, leftover **0.0074 A**.
- C1054 with only yellow + brown: darkening −0.245, leftover 0.034 A.
- Shapes (A scaled to largest = 1): yellow falls fast from violet and is
  about 0 from amber on; orange is nearly flat from violet to green (green
  0.55 of violet) and drops sharply at amber (0.13); brown absorbs every
  channel, photo_red still 0.26 of violet.
- **Orange is not saturated yellow:** K/S of orange ÷ K/S of yellow runs
  1.5 (violet) → 29 (green); a denser yellow would give one constant ratio.
- Maps: C1054 orange over its disc, yellow only on its bottom rim (orange
  replaces yellow, not adds); C1052 yellow strongest in a disc; C1055
  brown everywhere, a little yellow lower left.
- Cost: row lengths of M (noise in each amount per unit of channel noise)
  gamma/c_Y/c_B/c_O = 1.81/3.37/7.05/6.94, against 0.71/2.73/1.88 with
  two colors.  Orange and brown look alike in blue–green.
- Concentration order: yellow (12.8) → yellow + orange (20) → orange (40)
  → brown (600).  Fits Bill's 1/2/3-group picture; **one strip per amount,
  so consistent, not proved.**

**3h — a generic pre-image** (Bill's question, 2026-11-16; commit da3033f;
`generic_white_3h`).  Each strip's post-image divided by the average of the
*other three* strips' pre-images, against its own:

- Whole-strip error ±0.002–0.004 A, nearly the same in every channel
  (C1052/C1053 −0.002, C1054/C1055 +0.002 to +0.003), so mostly absorbed
  by gamma.  Amounts move at most 0.018 (C1055: orange +0.018, brown
  −0.016).
- Per-pixel noise added: 0.004–0.006 A (violet to amber), 0.008–0.011 A
  (G layers of red_orange, red, photo_red); in amounts about 0.01 (yellow)
  and 0.03 (orange, brown) per pixel.
- **A lower bound:** one day, one paper lot, one holder.  Older archives
  differ in holder (F, G: build one generic white per holder), lot, flat-
  field calibration, clipped post-images (C1039–C1048), layers saved and
  photos per LED.  An old archive's usable channels are set by its
  post-image.

## Open items

Deferred by Bill on 2026-11-16 (don't start unasked):
1. Cross-day test of the generic pre-image: divide C1049–C1051 (own
   pre-images on 9 channels) by a generic white from C1052–C1055.
2. A list of older holder-H archives (C1046 on) a generic white could open:
   holder, unclipped post-image channels, layers saved, photos per LED.
3. Inside the strips (yellow → orange out from C1054's center) against
   more strips first.  Claude recommended more strips first.

Not yet raised with Bill:
- Is C1049's dot color (3e) the same as C1054's orange?  Testable on the
  9 channels the two sets share.
- `tex/chain_unified.tex` step 9 says groups 1 and 2 are both yellow (H1
  with H2 as the alternative); the new results favor H2 with 2 = orange.
  Open items `op:third`, `op:sites`, `op:purebrown` there need updating.
  Not edited.
- The 13 weak layers have no flat-field model, so they can't be used.
  They carry little light anyway.
- The pad rim and edge bands, the Bus frames, and the stain drift
  (memory `project_stain_drift_study`) are not part of these results.
- Replicates: one strip per amount.

## Where things live

- Code: `desktop/padanalysis/color_study.py`.  Steps 3f (`color_count_3f`,
  `show_3f`), 3f-2 (`leave_one_out_3f`), 3g (`three_refs_3g`, `show_3g`),
  3h (`generic_white_3h`).  Shared data: `data_3f` (reads once per run).
  `FILES_3F`, `AMOUNT_3F`, `REFS_3G` near the 3f block.  `noise_pca` and
  `leftover_after` take `remove_even` / `removed` (defaults = 3d).
- Run: `cd desktop/padanalysis && timeout -k 10 1200 ../../.venv/bin/python3
  -u color_study.py`; turn steps on and off with the `if 1:` / `if 0:`
  blocks in `run()` (3f-2, 3g, 3h on as of da3033f).  Run time not
  measured; each run finished well inside the 20-minute cap.
- Outputs: `desktop/padanalysis/color_out/color_count_3f.json`,
  `leave_one_out_3f.json`, `three_refs_3g.json`, `generic_white_3h.json`;
  figures `desktop/padanalysis/fig/color_3f_*.png`, `color_3g_*.png`, each
  with a plain-language `.txt`.
- Catalog: `docs/desktop_tools/analysis_chain_tools.md`, color_study row.
- Commits: 0100d48, cf578de (3c–3e), 09a28b2 (3f), da3033f (3f-2, 3g, 3h).

## Closing checklist (How_best_to_help_Bill.md §8)

- Resume prompt: this file.
- .tex report: not written (`tex/chain_unified.tex` step 8/9 is the home;
  see the open item above).
- Production code: not moved; the matrix is not saved for the chain.
  `analysis_1.CHAIN` not bumped.
- Memory: `project_yellow_brown_3c.md`, updated 2026-11-16.
