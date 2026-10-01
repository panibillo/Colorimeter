# Resume Prompt: White-Reference Corrections

**Date Created:** 2026-09-27
**Branch:** `preimage-capture`
**Scope:** Finish moving every *white reference* in `desktop/padanalysis/`
off the bare paper below the pad and onto the strip's own dry pad in the
pre-image.  The core change is done and committed (`eb4d8d2`); what remains
is the other code that still divides by, or measures noise on, the bare
paper.
**Related:** `docs/resume_prompt_find_dot.md` (the dot study, paused; it
depends on `get_balanced_cell_data`, which is already corrected).

---

## How to work with Bill on this

- Plain language, no jargon.  Everything clear, complete and double checked.
- Small steps.  Explain, propose, then **wait** for Bill before each file.
- Show figures when judging a result by eye.  Raw next to interpreted.
- The open questions below are Bill's to decide.  Offer a recommendation,
  do not settle them yourself.
- Only one session should edit `analysis_1.py` at a time.  On 2026-09-27
  two concurrent sessions touched it and it caused confusion.  Before
  editing, check `git status` and that nobody else is mid-edit.

---

## The rule, as Bill has set it

1. **The white reference is the dry pad in the pre-image.**  R_inf is a
   pixel's reflectance divided by the median reflectance of the same
   strip's pad in the pre-image (the photo taken before any reagent),
   read inside the pre-image's own fitted pad rectangle, backed off
   `erode` pixels from its edges.
2. **The bare paper below the pad is never a white reference.**  Not for
   R_inf, not for absorbance, not for a noise estimate, not as a
   pooled substitute.
3. **The bare paper stays for geometry.**  `padrect.py` uses the paper's
   brightness to find the strip's left and right edges and to place the
   pad.  That is correct and stays.  (Bill, 2026-09-27: "I was wrong
   that the white pad can go away entirely.")  So `PadRect.paper_level`,
   `EDGE_FRACTION` and the edge finders are **not** on the list below.
   `PadRect.paper_mask()` stays too, because `padrect` needs it; what goes
   is its use as a reference by other files.

---

## What is already done (do not redo)

Commit `eb4d8d2`, "Take R_inf's white reference from the pre-image's own
dry pad":

- `analysis_1.pre_white(samp_no, cell, erode)` returns
  `(R_white, rung, why)`.  It corrects the pre-image for darkness and
  against its own gray chips, exactly as the measurement is, and takes the
  median over the eroded pre-image pad.
- `get_balanced_cell_data` divides by it.  The same `erode` shrinks both
  the measured region and the pre-image pad used as white.  (Bill has not
  yet said whether the white should always use a fixed erode of 6; see
  open question 4.)
- Channels the pre-image does not hold keep R (against the gray chips) but
  get no R_inf, A or K/S.  `BalancedCell.white_why` says why: "pre-image:
  LED not lit in this sweep", "archive has no pre-image", and so on.
  `paper_sat` / `paper_clipped` are gone.
- `WHITECELLS = PRECELLS`.  On current captures that is 8 channels:
  violet.B, royal_blue.B, cyan.B/Gb/Gr, green.Gb/Gr, photo_red.R.  C1014
  has only royal_blue.B and red.R; C1002 and older have none.
- `CHAIN` ends in `+pre_white`, so stored results made before the change
  are recomputed, not reused.
- `padphot.reflectance_infinity` docs list the dry pad as the reference
  in use.

Checks that passed: R unchanged; R_white equals an independent median
of the pre-image pad; dry-pad R_inf median = 1.0000 on every pre-image
channel; `padphot` selftest and 11 docstring examples pass.

Effect (royal_blue.B, paper → dry pad): white reference about 4 % lower,
so pads read a little lighter.  C1032 pad R_inf 0.790 → 0.819.

---

## What remains: bare paper still used as a reference

In order of importance.  Each is a separate step for Bill to approve.

### 1. `duotone.PadCube`, behind `analysis_1.cell_PCA` (yellow/brown split)

**Done 2026-09-27.**  Bill chose (a) with (c): `PadCube(samp_no, erode)`
now takes every value from `analysis_1.get_balanced_cell_data` (`A` against
the pre-image's dry pad; matches it exactly, pixel for pixel), keeps only
cells with a dry-pad reference (6 shared by C1017-C1036, 8 on some), and
measures noise by neighbor differences on the pre-image's dry pad
(`dnoise`, `_pair_noise`).  No bare paper is read.  `look3.py` and
`report14.py` kept as records; they no longer run (C1002-C1014 lack the
pre-image).  The text below is what it was before.

**Checked 2026-10-01** (read-only script, not kept): `PadCube` builds on
all 18 strips C1017-C1025 and C1028-C1036.  8 channels on C1017, C1018,
C1028, C1029 and C1031-C1034; 6 on the rest, because blue.B and
cyan.Gb/Gr clip on the measurement, or (C1030) cyan.Gb/Gr on the
pre-image.  The 6 shared by all: violet.B, royal_blue.B, cyan.B,
green.Gb, green.Gr, photo_red.R.  About 2,200 pad pixels each; dry-pad
scatter `sigma` 0.004-0.009 A.  The endmember fit was not run (it has
known faults; Bill's duotone review comes after shading removal).
Wording oddity: C1030's skip reason reads "clipped: 0% of the pad and
chips", presumably a few pixels rounded down to 0 %.

The most important.  `PadCube` (duotone.py ~lines 120–170):
- divides every pad pixel by the median of the bare paper
  (`paper_mask = pad.paper_mask()`, `ref = nanmedian(r[paper_mask])`);
- estimates per-pixel noise on the paper (`_paper_noise`, `dpaper`),
  which feeds the noise covariance (~line 489–510);
- includes the paper region in its over-exposure check (`_reflectance`,
  `region = self.mask | paper_mask`).

**The difficulty:** `PadCube` uses every channel with a usable flat-field
model -- up to 27, the GRAYCELLS list; the pre-image holds only 8.
Switching to the dry pad would drop about 19 channels from the fit.  Options to put to Bill:
- (a) use only the 8 pre-image channels in the PCA;
- (b) change the pre-image recipe to light more LEDs (touches the
  instrument's capture settings, and the pre-image recipe is a settled
  decision, see memory `project_preimage_protocol`);
- (c) for the noise estimate only, use the dry pad's pixel-to-pixel
  differences instead of the paper's (the same method on a different
  region).

### 2. `measure.py` → `records.json` → `chemistry.Reduction`

`measure.py` writes `paper_ratio` (pad-area reflectance of the bare paper)
into each record.  `chemistry.Reduction` computes
`rho = R_pad / R_paper` and `A = -log10(rho)`, and borrows a pooled paper
value when a capture's paper clipped.  Readers: `report1`, `report2`,
`report8`–`report13`, `sweep.py`.

Most of those captures (C1002 and older) have **no pre-image**, so they
cannot be moved to the dry pad at all.  To put to Bill: leave this
pipeline as a record of the old studies (and say so in its docstrings), or
retire it, or convert it for pre-image captures only.

### 3. `R_guide.py` and `R_inf_guide.py` (explanation figures)

Each works out its own R_white from the paper (`A._region(..., ROIWHITE,
...)`, `R_white = median(R[paper])`).  Their figures, and the text in
`tex/R_inf_guide.*`, no longer match `analysis_1`.  Probably change them
to call `analysis_1.pre_white`, then regenerate the figures and the tex.

### 4. `analysis_1` leftovers

- `ROIWHITE` can still be asked for by name through `_region` /
  `get_cell_data`, and `roi_overlay` draws it in green as a check that
  the region sits on bare paper.  Nothing in the calculation reads it.
  To put to Bill: keep it as a drawn check (it shows the paper that
  `padrect` uses for the edges), or remove it.
- `_region` / `_paper` (~line 496) wrap `PadRect.paper_mask()`.

### 5. Studies of the paper itself: `report3.py`, `report9.py`, `report11.py`

These measure the paper on purpose (paper variation between strips,
same strip in two placements, and so on).  They are history, not
calibration.  Likely leave them, with a one-line note in each docstring
that the paper is no longer the white reference.

### Not on the list (stay as they are)

- `padrect.py`: `paper_level`, `EDGE_FRACTION`, the side and bottom edge
  finders, and `paper_mask()` itself.  Geometry, not reference.
- `fields.py` prints a paper value as a diagnostic column; not a
  reference.
- `duotone.NM["white"]` and `report14` `nm < NM["white"]`: this "white" is
  the white LED, not the paper.
- `look1.py` / `segment.py` `c.layer("white", ...)`: again the white LED.

---

## Open questions for Bill

1. ~~**`cell_PCA`:** options (a), (b) or (c) above, or a mix.~~  Settled:
   (a) with (c).  But `run_sampno()` step 7 (`cell_PCA`) still carries
   "DO NOT RUN THIS UNTIL white reference is corrected"; Bill does not yet
   know whether that note is out of date (2026-10-01).
2. **`measure.py` / `chemistry.py`:** keep as a historical record, retire,
   or convert for pre-image captures only?
3. **`ROIWHITE` in `analysis_1`:** keep the green outline in
   `roi_overlay` as a geometry check, or remove the name?
4. **Erode for the white:** keep "same `erode` as the measured region"
   (current), or always use 6 for the white whatever else is asked?
   One-line change in `get_balanced_cell_data`.
   **Accepted for now (Bill, 2026-10-01), still open.**  Note that
   `duotone.PadCube` calls `get_balanced_cell_data` without `erode`, so
   its white always uses `analysis_1.ERODE`, whatever `erode` the cube is
   given; the cube's `erode` only chooses which pad pixels become rows.

---

## Suggested first step (waits for Bill)

Show Bill, for one capture (C1032), the yellow and brown layers from
`cell_PCA` as they are now (paper reference) next to the same layers
built from the 8 pre-image channels against the dry pad.  Let him judge
by eye whether losing the other 19 channels costs anything visible, before
deciding question 1.

---

## Useful commands

```bash
cd desktop/padanalysis
../../.venv/bin/python3 -u padphot.py             # selftest + docstring examples
timeout -k 10 300 ../../.venv/bin/python3 -u analysis_1.py   # run block; step 5 = dry-pad check
grep -nE "paper_mask|ROIWHITE|paper_ratio|R_paper" *.py      # remaining users
```

Always use the `.venv` interpreter; the system python lacks h5py and cv2.
Always cap a run with `timeout` (see `CLAUDE.md`).
