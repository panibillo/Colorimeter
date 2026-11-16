# Work plan: proof of concept of the analysis chain (2026-11-09)

The chain is described in `tex/chain_unified.tex`. "Step N" below means that
document's step N. Its open items are numbered 1–8.

## Ground rules

- **Proof of concept.** The final apparatus doesn't exist yet. Each study
  answers "does the method work, and roughly how well?" Use a few pads per
  point, report each spread as a plain number, and run no formal tests.
- **Data:** the newer captures from cartridge holders **F, G and H only**.
  Older captures (dot-2mm, whole pad) are not used except as a cross-check
  where one helps.  A good brown sample does not yet exist in this set.
- **Deferred:** the gray polynomial (open item 1). Use Φ = the mean of the two
  chips' pre-to-post ratios. Also deferred: the mirror-bounce check (open
  item 2) and the binding-site chemistry (open item 6).
- **Each study** starts with three lines: the question, the step it could
  change, and what result ends it. It ends with a figure for Bill to approve,
  the numbers, and a short note. Production code waits until Bill approves.
- **The analysis covers the whole pad, out to its fitted edge.** Edge
  darkening is handled by the edge bands from band 0, the outermost pixel
  row (`tex/chain_unified.tex` step 5), and the arsenic is summed over every
  pad pixel (step 9). The old chain's `analysis_1.ERODE` = 6 px (still listed
  in `chain_samples.py`) does not apply to this chain. (Recorded 2026-11-14;
  the decision came with the edge bands, 2026-11-09.)
- **For scale:** camera noise is about 0.002 A, texture 0.007 A, and
  replicate strips disagree by an unknown factor (It was 1.9 earlier before 
  the procedure was tightned). An effect smaller than what is left over is 
  polish; say so and move on.

## 0. Inventory (do first)

1. Copy the F/G/H captures and their `samples` rows from the Pi. This machine
   only has up to C1038, which is in holder F.
2. **Correct C1045's `cap_style` to G** in the Pi database, and in the copy.
3. Make a table of the samples, one row each: sample, holder, arsenic
   (`manual_read`), whether a pre-image is present, its channels, its
   exposure_us rungs, and its repeats (e.g. `C1038rep2`–`rep5`).
4. Beginning with `cap_style` F, pads have dye reaching the edge, and the
   dyed-edge question (open item 3) is likely central here.  These samples 
   also have amorphos central brown spots - the pixel by pixel color analysis
   should not care, except that there may be some unanticipated effect on
   the edges.

**Output:** the table, plus a contact sheet of the pre-image and post-image
for each sample.

## 1. Alignment (step 4)

- **Question:** does `analysis_1.pre_to_post` put the same paper on the same
  pixel well enough in the cartridge holders? It could change whether lining
  up needs refining (`padregistration.py`, shelved).
- **Measure:**
  - (a) The shift found per sample, by holder.
  - (b) The texture left after lining up: the pixel-to-pixel spread of
    post-image ÷ pre-image on undyed pad. Reuse `shading_review.lineup_noise`.
  - (c) The false change at the edge: the post/pre band means on an undyed
    edge, in A.
- **Ends when** the false change is under 0.002 A beyond the outermost band.
  If it isn't, try the empirical refinement once, then stop.

## 2. The white (step 5)

- **Question:** can the edge bands plus the blend replace the plain pre-image
  divisor, and does it matter? It could change how Ŵ is built.
- **Measure, from the pre-images:**
  - The band profiles ℓ_k for each edge and channel. Add bands until a band
    is within 0.002 A of the interior. Check corners against the product rule.
  - The camera noise σ_δ′, from the repeat photos.
  - The texture left after the bands, σ_η. From these two, the best λ.
- **Measure, from the post-images:**
  - The texture carry-over slope on undyed pad and inside the dye
    (open item 4).
  - The band profiles with dye at the edge, against without (open item 3).
- **Ends with** one number: how much the blend and bands change each pad's
  mean A, compared with the replicate spread.
- **Status (Bill, 2026-11-13): closed.** The blend changes a pad's mean A by
  at most 0.00004 A; the four 100 µg/L strips differ by 0.011 A (photo_red.R)
  and 0.026 A (green.B) standard deviation. The white is polish. 2d-ii (dyed
  edges) is not needed for the white. Blur, exposures and stain drift are
  deferred. See `tex/chain_unified.tex`, step 5.

## 3. Yellow and brown (step 8)

- **Question:** do two endpoint lists, **y** and **b**, describe the F/G/H
  stains? It could change whether a third amount is needed.
- **Method:**
  - Choose the yellow endpoint (lowest arsenic, palest) and the brown
    endpoint (highest arsenic, darkest). Average each one's A over its stain
    to get **y** and **b**, then build M = (SᵀS)⁻¹Sᵀ.
  - **Check with PCA**, through zero and with brightness removed: how many
    directions carry signal, whether the endpoints lie in the plane, and the
    residual left at mid-range concentrations.
- **Capture if needed:** one deliberately over-exposed pad, for a purer
  brown (open item 7).
- **Ends with** M saved, a map of yellow and brown amounts per pad, and the
  third component's size compared with its noise.
- **Status (2026-11-14): paused** after 3b, for the detour below. It resumes
  on a new set of strips Bill will capture once the exposure work is built
  and tested.

## 4. Calibration to known concentrations (step 9)

- **Question:** how well does the per-pixel sum predict the known arsenic,
  and in what range?
- **Method:**
  - Mass per pixel F(c_Y, c_B), summed over the pad. Start with F linear in
    c_Y and c_B; add a saturating form only if the residuals bend.
  - Compare the result with `manual_read` × sample volume. Show the
    replicates as separate points.
- **Side checks:**
  - The same fit with a third amount (open item 5).
  - Per-pixel against summing first; their difference shows how much the
    saturation matters.
- **Ends with** a calibration curve with its replicate spread, and the
  working range in µg/L.

## Detour (2026-11-13/14): pad finding on the Pi, and exposures set from the strip

Step 3 (yellow and brown) needs at least three channels with a usable
pre-image white. Since C1039 the dry pad is clipped (1023 DN raw) in
violet.B, royal_blue.B, cyan.B, green.Gb and green.Gr. Cause: the pre-image
takes each LED's group-1 exposure_us, and for every LED whose calibration was
"spread" that is the middle rung, which calibration never checks for clipping
(`exposure_fit._need` returns NEED_NONE). The post-images clip too (C1039:
about 11 % of the stored region at the middle rung, 1-3 % at the shortest).

Bill widened the fix into a detour (2026-11-14). Step 3 resumes after it, on a
new set of strips Bill will capture once the exposure work is built and
tested. The full plan is `~/.claude/plans/velvety-roaming-pascal.md`.

### Decisions (Bill, 2026-11-13/14)

- **Test region:** the fitted pad plus the gray1 and gray2 ROIs, non-hot
  pixels only ("unscreened" = not hot). gray1 and gray2 need no finding.
- **Limits, all in raw DN.** The dark level is 64.00-64.05 in every Bayer
  layer (12 sweeps, C1038-C1047, pre and post, checked 2026-11-14):

  | Config row | Raw DN | Meaning | Test |
  |---|---|---|---|
  | `capture.clip_top_dn` | 1023 | sensor ceiling | clipped if raw >= 1023 |
  | `capture.clip_bottom_dn` | 65 | dark level + 1 | clipped if raw <= 65 (counted in the log only) |
  | `capture.noise_floor_dn` | 164 | dark level + 100 | Bus needed if raw < 164 |

  Formulas that scale signals still subtract the dark level, e.g.
  Bus = Tus x (164 - 64) / (darkest raw - 64).
- **Tus** (the top is not clipped): accept a frame when no test-region pixel
  is at 1023 and the brightest is at least 80 % of 1023. Otherwise re-aim:
  after a clipped frame shorten by a fixed factor (0.7); after an unclipped
  one scale linearly so the brightest pixel lands at 85 % of 1023. At most 4
  frames. If the last one still clips, save it, mark the LED failed, and say
  so on screen.
- **Bus** (the bottom is above the noise floor): taken only when a test-region
  pixel of an analysis channel is below 164 at Tus. Check that Tus and Bus
  together leave every non-hot pixel unclipped at the top and above the floor
  in at least one of them.
- **Exposures per LED:** `protocol.multiple_exposure_count` (int, default 1)
  is the number of frames kept at Tus, all at the same exposure_us. Bus is
  taken only when needed, `protocol.bus_exposure_count` frames of it: 1 while
  the exposure rule itself is being studied, N for the yellow/brown trials.
  The Pi never combines frames; the desktop decides how (2026-11-14, Bill:
  option B).
- **Post-images** start at the strip's own pre-image Tus. Dye only darkens the
  pad, so that cannot clip.
- **Layer filter:** `capture.layer_keep_min_dn` / `max_dn` do not fit the new
  rule and are retired from the pre/post path. An analysis channel is always
  saved; any other layer only when it has no clipped pixel in the test region
  and its brightest pixel is above the noise floor.
- **Exposure table:** one seed row per LED (last accepted Tus, and Bus if
  used). Groups 1-5 stay as history; nothing reads them for pre/post.
- **Multiple exposures:** the test is whether combining frames makes the
  yellow and brown amounts more accurate, so the study waits until those
  amounts can be measured (Bill, 2026-11-14). Meanwhile the trial strips are
  captured with `multiple_exposure_count` = 3, so their archives hold the
  study's data, blue side included. The extra light (about 0.57 s of LED
  on-time per extra frame) is accepted for the trials: a little stain drift
  acts like ordinary noise, and this is not the final calibration.

### What the archives already say

- **D1, safe pre-image exposure_us** (`preexposure_study.py`, 2026-11-13):
  the exposure_us predicted to put the brightest non-hot pixel on the pad and
  gray chips at 0.85 x 1023 raw. The brightest pixel is always on the pad,
  never on a chip. Per LED, set by its brightest layer: royal_blue ~2260
  (B; now 4017), violet ~14850 (B; now 21431), cyan ~16070 (B; now 27412),
  green ~8760 (Gb/Gr; now 14582), photo_red ~37900 (R; now 34909). The
  blue-side values rest on C1038 alone, which reads about 5 % dimmer than the
  other strips in the layers that can be compared.
- **Delay between captures:** successive lit frames of one LED are 0.57 s
  apart (median of 586 gaps, C1030-C1048); the exposures themselves are
  3-44 ms. LED on to the first stored frame takes 2.53 s. LED on-time is
  2.60 s with 1 frame and 3.79 s with 3, so each extra frame adds about 23 %
  to the light on the pad. Nearly all of it is camera settling and file
  writing, not exposure.
- **Frame-to-frame brightness:** the gray chips' signal per microsecond
  differs between the rungs of one LED by 0.3-2.6 % (cyan worst). Frames
  can't be combined on raw counts alone; each must first be scaled by its own
  chip level.
- **Pi libraries:** numpy, OpenCV (`cv2`), picamera2, h5py. No scipy.

### Stages

- **Stage 1. Pad finding on the Pi** (`src/padfind.py`). Port the cassette
  ("window") part of `padrect.py`, numpy and cv2 only. Find the pad once
  before the pre-images and once before the post-images, on photo_red.R only
  (it barely changes the pad and still works on stained pads). Shift the fit
  by the photo_red.R offset so it matches the desktop's all-channel fit:
  pre-image (left, right, top, bottom) = (+0.10, +0.44, +0.33, +0.33) layer
  px; post-image (+0.13, +0.35, +0.11, +0.11). Light the LED only long
  enough for one frame, with no warm-up, and fit after it is off. Profile the
  whole step, the LED on-time and the fit, not counting library loading.
  Check against `analysis_1.pad_of` on the 30 F/G/H files.
  **Done 2026-11-14** (Bill approved). Parity: the port matches `padrect`
  exactly (0.000 px, 60 fits); after the offsets every edge's mean is within
  0.01 px of the all-channel fit, spread at most 0.13 px. On rpizero3: LED on
  0.06-0.09 s (frames are held and converted after the LED is off), pad
  finding 0.45-0.99 s in all, fit 0.02-0.09 s. Archive schema 6 stores the
  fit in each section's `pad` group. QC tilt limit stays at 2 degrees.
- **Stage 2. Multiple-exposure study. Deferred** (Bill, 2026-11-14) until
  the yellow and brown amounts can be measured; it will be judged on their
  accuracy, using the N = 3 trial archives. Original outline: does
  combining 2-3 exposures change a pad's mean A by more than 0.5 % of its dye
  signal, or cut per-pixel noise usefully? Compare one frame; Bill's average
  of (DN - dark) / exposure_us over unclipped frames; and total signal /
  total exposure_us over unclipped frames, each with and without scaling
  every frame by its own gray-chip level.
- **Stage 3. The exposure rule** in `capture_runner.py`: Tus, Bus and the
  saved-layer rule above, for pre-images and post-images, keeping
  `multiple_exposure_count` frames at Tus. Write the archive after the LED is
  off, so file writing is not LED on-time.
  **Done 2026-11-14** (Bill approved; with the edge rule of the floor test).
  **C1048 test on rpizero3** (2026-11-14, all 10 LEDs,
  `captures_exposure_test/20261114_1655.h5`): pad found pre and post (tilt
  0.01 degrees, same height as the desktop fit). The blue-side Tus did not
  rise on the brown pad. The top 2 rows and bottom row of the fitted pad
  read white (violet.B ~830 raw against 230-360 inside), and the desktop fit
  of C1047/C1048 shows the same rows: they are the backing strip (Bill).
  They set Tus on a stained pad. Left as is for now; the pad's end rows are
  to be dealt with in the pad-edge steps as planned (Bill, 2026-11-14).
  Bus ratios on C1048: 1.2-1.6 x Tus for violet, royal_blue and cyan; about
  7 x for green.B.
- **Stage 4. Database**, in three steps:
  - **4a. Config rows. Done 2026-11-14.** Every setting of the strip rule
    and of pad finding is a config row, read once per run before the camera
    starts (`exposure_rule.RuleSettings.from_config()`): the rows in
    `docs/operating_pad_finding.md` section 6, including
    `capture.exposure_rule`. Factory values = the module constants (a test
    keeps them equal); `multiple_exposure_count` is 1, and rpizero3 is set
    to 3 for the trials as an operator change. Bad values stop a run
    before the camera starts, and a start-up check warns. The coverage
    check counts only layers stored at Tus. `capture.layer_keep_min_dn` /
    `max_dn` stay active: the group rule, the white photo and a start-up
    check still read them.
  - **4b. `exposure_log` table. Done 2026-11-14.** As below, with
    `verdict` ('accept' | 'clipped' | 'dim') in place of a separate
    `accepted` flag, and strip-rule sweeps only. rpizero3: 154 rows for a
    5-LED pre/post pair, written in 0.10 s and 0.04 s.
    The log showed the floor test's edge rule unstable on dim layers; it
    now also requires an edge pixel's signal to be below
    `capture.edge_signal_fraction` (0.5) of the interior's (Bill,
    2026-11-14). No Bus decision changed on the last two Pi archives.
    (One row per frame per layer, retries and
    the pad-finding frame included: sample, archive, pre/post, purpose,
    attempt, label, accepted, stored, LED, Bayer layer, exposure_us
    requested and applied, dark, min, max and mean raw DN, where the max
    sits, the floor test's darkest pixel, pixel count, clipped at top,
    clipped at bottom, below the floor, edge pixels left out). Written after
    the LED is off; a failed write never costs a capture.
  - **4c. Seed rows in `exposure`. Done 2026-11-14.** Role values
    'ladder' and 'tus'; the factory seeds are rows in
    provisioning/config_defaults.sql; a bench run with no sample never
    updates a seed. (New columns `role` and `samp_no`.)
    "Seed" = the exposure_us of an LED's first try. A measurement already
    starts at its own pre-image Tus. Seed rows are read for every
    pre-image and updated only from pre-images, when the seed was re-aimed
    (Bill, 2026-11-14). Red, red_orange, amber and blue are not in the
    pre-image, so they get fixed seeds from the C1048 run (19301, 22999,
    44728 and 7351 µs) that never update (Bill, 2026-11-14). Whether to
    keep a Bus seed for stained pads is to be re-asked after more data.

Bill reviews after each stage. Nothing is committed without his go-ahead.

## Order and hand-off

Do 0 → 1 → 2 → 3 → 4. Steps 3 and 4 can start on the plain pre-image
divisor if step 2 shows that the white is polish. After each step:
- update the step's section and open items in `tex/chain_unified.tex`;
- save the figures under `tex/figs/`;
- write a memory entry for the decision.

Nothing is committed without Bill's go-ahead.
