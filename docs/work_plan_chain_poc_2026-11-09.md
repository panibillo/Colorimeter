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

## Order and hand-off

Do 0 → 1 → 2 → 3 → 4. Steps 3 and 4 can start on the plain pre-image
divisor if step 2 shows that the white is polish. After each step:
- update the step's section and open items in `tex/chain_unified.tex`;
- save the figures under `tex/figs/`;
- write a memory entry for the decision.

Nothing is committed without Bill's go-ahead.
