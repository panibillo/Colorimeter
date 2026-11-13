# Desktop tools: the analysis chain, step by step

Last reviewed: 2026-11-11 (archive move the same day).

This document lists the desktop code for each step of the analysis chain,
from raw counts to an arsenic estimate. The steps and their numbers follow
`tex/chain_unified.tex` (2026-11-09), which replaces the earlier chain
drafts. The current proof-of-concept plan is
`docs/work_plan_chain_poc_2026-11-09.md`.

Companion documents:

- [inspection_tools.md](inspection_tools.md): looking inside the files, contact sheets, pad finding.
- [pi_transfer_tools.md](pi_transfer_tools.md): moving files and data between the desktop and the Pi.

Each status below comes from reading the code, its own notes, `HOWTO.md` and
the git history on the date above. Nothing was run to confirm it.

---

## Status key

| Status | Meaning |
|---|---|
| **Current** | In use now. Use it. |
| **Engine** | Library code that current scripts import. You rarely run it on its own (some have a self-test). |
| **Open study** | A study that is still in progress. |
| **Record** | A finished study or report. Kept as the record of how a decision was made. Its results are not current; don't build on it. |
| **Superseded** | Replaced by a newer method, named in the entry. Candidate to move to an archive folder. |
| **Parked** | Working code kept for later, but not used by the current chain. |
| **Broken** | No longer runs as written. Kept only as a record. |
| **Deprecated** | Don't use. Candidate to delete. |

---

## Where the chain lives

**Purpose.** One place to run the chain on a capture by hand and look at
every in-between result.

**Method.** `desktop/padanalysis/analysis_1.py` holds the methods; the
procedure is the `run_sampno()` block at the bottom, with each part behind
an `if 1:` / `if 0:` guard that is edited for each run. The photometry
formulas are in `padphot.py` as plain functions on numpy arrays, with no
file or database access, so each can be checked on its own.
`get_balanced_cell_data()` walks one channel through the whole chain and
returns a `BalancedCell` that keeps every in-between array.

| File / function | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `analysis_1.py` (`run_sampno()`) | **Current** | The hand-driven chain. Its version tag is `analysis_1.CHAIN` = `"dark_level+divisor_map+hot_mask+pre_white+ff_union"`. | Figures in `desktop/padanalysis/fig/analysis1_<samp_no>_<what>.png`; `sample_roi` rows in `src/colorimeter.sqlite`; the interim store (below). |
| `analysis_1.get_balanced_cell_data()` / `BalancedCell` | **Current** | One channel, every step kept: `sig`, `div`, `R`, `R_white`, `R_inf`, `A`, `KS`, `KS_signed`. | Returns a `BalancedCell`. |
| `analysis_1.get_cell_data()` | **Current** | The dark-corrected signal of one channel over one region (pad, paper, gray chip, whole crop), as a masked array on the full layer grid. `pre=True` returns the pre-image lined up on the post-image. | Returns a masked array. |
| `padphot.py` | **Engine, current** | The formulas, one per function. `python3 padphot.py` runs its self-test. | — |
| `analysis_1.writeh5_sampcell()`, `readh5_sampcell()`, `sampcell_attrs()`, `clear_sampcells()`, `read_duotone()` | **Current** | The interim store: saves arrays per sample so later steps don't recompute them. Each file is stamped with the source archive and with `CHAIN`. | `desktop/padanalysis/analysis/samples/<samp_no>.h5` (not committed). |

Before any step: run `inventory`, `locate_pad` and `roi_overlay`, and look
at the overlay. See [inspection_tools.md](inspection_tools.md) §1 and §3.

---

## Step 0. Dark level and hot pixels (settled)

**Purpose.** Remove what the sensor reads with no light, and leave out the
few pixels whose dark reading is reliably high.

**Method.** One number per Bayer layer per sweep is subtracted: the average
of the sweep's shortest-exposure dark frame (about 64 counts). Each pixel's
own dark value isn't used, because a pixel's dark offset barely repeats from
month to month, so subtracting it adds noise rather than removing it (decided
2026-09-12). Hot pixels were found once by a pooled outlier test (k = 6) and
stored in the `hotpixels` table. They are masked out, not repaired, using the
union over all four Bayer layers, so every channel uses the same pixel
positions.

| File / function | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `probe.Capture.layer()` (calls `src/capture_h5.dark_level`) | **Engine, current** | Lit layer minus the sweep's dark level. | Returns an array. |
| `probe.Capture.hot_mask()` | **Engine, current** | Reads `hotpixels` for this camera and moves the positions onto the crop. | Returns a mask. |
| `analysis_1._hot()` | **Current** | Union of hot pixels over the four layers, for masking. | Returns a mask. |
| `hotpixel_analysis.find_and_report()` | **Record** | Runs the hot-pixel test on one channel's pooled darks and shows it. | Figure in `fig/`; terminal report. |
| `hotpixel_analysis.save_calibration()` | **Current** (run once; re-run only for a new camera) | Writes the hot-pixel list through `src/hotpixel_repo.py`. | Database: `hotpixels` table in `src/colorimeter.sqlite`, `analysisref = 'dark2026-09-27'`. |
| `hotpixel_analysis.before_after()` | **Superseded** | Shows hot-pixel *repair*; the decision was to mask instead. | Figure in `fig/`. |
| `hotpixel_analysis.persistence_over_time()` | **Record** | Showed the same pixels are hot in August and September. | Figure in `fig/`. |
| `analysis_2.py` (dark study: `dark_inventory`, `dark_analysis1/2`, `dark_cdf_check`, `dark_stability_check`, `dark_temporal_check`, `dark_exposure_check`) | **Record** | The dark-signal study behind the decision. | Figures: `fig/analysis2_<name>_<what>.png` / `.pdf`. Write-up: `tex/dark_signal_characterization.tex`. |
| `analysis_2.estimate_dark_offset()`, `predict_dark()` | **Superseded** | A per-pixel dark offset estimate, rejected in favor of the frame average. | Returned arrays. |
| `devdesk/roi_darkdiff.py` | **Record** | Dark frames before vs after a sweep; old archives only. | Terminal report. |

---

## Steps 1 and 2. The pre-image and the post-image

**Purpose.** Read each channel of the pre-image (the strip before arsine)
and the post-image (after) at an exposure that is neither clipped nor too
dim.

**Method.** For each channel the code picks the exposure rung where the pad
and both gray chips are below the clipping level (1000 counts) and above a
minimum signal. If no rung passes, it says why rather than dropping the
channel silently. The pre-image is a short sweep: royal blue and red only,
one exposure, two rungs below the measurement's.

| File / function | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `analysis_1.rung_verdict()`, `_pick_rung()`, `_measured_region()` | **Current** | The rung choice and its reason. | Returns `(rung, reason)`. |
| `analysis_1.cells_present()`, constants `GRAYCELLS`, `PRECELLS` | **Current** | Which channels a section holds; which have a flat-field model; which a pre-image holds. | — |
| `probe.Capture`, `probe.sections()` | **Engine** | Opens the measurement or the `/pre` section. | — |

---

## Step 3. The gray chips and the gray polynomial

**Purpose.** Correct for the light being brighter or dimmer in one photo
than another, and for the light being uneven across the stage.

**Method in the code now.** Each channel has a published polynomial surface
(the *shape* of the light across the stage), fitted on gray-card captures
and stored in `layermodels`. `padphot.divisor_map` places that shape by
sensor position and pegs its *level* to the two gray chips in the same
photo, giving a gray-chip level at every pixel. `R = sig / div`.

**Method in `chain_unified.tex`.** The unified chain needs only Φ, the
average of the two chips' pre-to-post ratios. The gray polynomial is
deferred (open item 1, 2026-11-09). No production function computes Φ yet;
`chain_example.gray_ratio_check()` looks at it.

| File / function | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `padphot.divisor_map()` | **Current** | Gray-chip level at every pixel. | Returns an array. |
| `padphot.reflectance()` | **Current** | `R = sig / div`. 1 means as bright as the gray chips would be there. | Returns an array. |
| `padphot.divisor()` | **Superseded** by `divisor_map` | One number for the whole region. | Returns a number. |
| `whitecorr.LayerModel` (`gain_at`), `load_models()`, `anchor_weights()` | **Engine, current** | Rebuilds each published surface from its stored coefficients; places it by sensor position. | — |
| `devdesk/flatfield_pool.py` | **Current** | Fits the polynomials over a pool of gray-card captures, scores each degree by leaving one placement out, and sets the chip weights. | Reports and figures: `devdesk/flatfield/<ref>/`. Database: `flatfield` and `anchor` tables in `devdesk/flatfield.sqlite`; optionally `layermodels` in `src/colorimeter.sqlite` (puts new models in force). |
| `devdesk/run_bound_union_a.py` | **Current** | The run whose fit is in force (bound roi 61, published 2026-11-02). | As above, with `layermodels` written. |
| `devdesk/archive/run_bound39a.py` | **Superseded** by `run_bound_union_a.py` | The earlier run over bound roi 39. | As above. |
| `devdesk/flatfield.py` | **Engine** (its command line is a **Record**) | The polynomial fitting and scoring that `flatfield_pool` and `graychip_study` import. Its own modes chose the polynomial form. | Database: `flatfield` in `devdesk/devdesk.sqlite`; gain maps in `devdesk/flatfield/<suite_id>/*.npy`. Write-up: `tex/colorimeter_flatfield.tex`, `docs/flatfield_calibration.md`. |
| `graychip_study.py` | **Open study, paused** (open item 1 deferred) | Is the light on the gray chips one fixed shape that only gets brighter or dimmer? | Figures: `fig/graychip_study_<what>.png`. |
| `chain_example.gray_ratio_check()` | **Record** | What the gray-chip levels add to the stain. | Terminal report. |

---

## Step 4. Line up the pre-image on the post-image

**Purpose.** The strip is taken out and put back between the two photos,
so the pad moves a little. Each post-image pixel has to be divided by the
pre-image pixel that saw the same piece of paper.

**Method.** Each photo has its own fitted pad rectangle, so the lining up is
arithmetic: a post pixel's position measured from the pad's left edge and
top is carried to the same position in the pre-image pad, then the
pre-image is resampled once (bilinear) onto the post grid. No image
matching is used. Lining up applies only to regions that move with the
strip (pad, paper), not to the gray chips.

| File / function | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `analysis_1.pre_to_post()` | **Current** | The coordinate map from post to pre. | Returns the map. |
| `analysis_1._register()` | **Current** | Resamples a pre-image frame onto the post grid. | Returns an array. |
| `analysis_1.pre_post_overlay()` | **Current** | Check figure: lined-up pre, post, and their ratio. | Figure: `fig/analysis1_<samp_no>_prepost.png`. |
| `align_study.py` | **Open study** (proof-of-concept step 1) | How well does lining up put the same paper on the same pixel? Measures the leftover misalignment from the paper texture, each photo's own edge-fit error, and the false change in A it causes at the edge. `show_poor()` draws a check figure for each pair whose post-image pad fit is poor (texture match below 0.2). Step 1d (`GuidedPad`, `compare_guided()`, `show_guided()`) tests a side finder that searches only within 2 px of the pre-image's sides. | JSON: `desktop/padanalysis/align_out/pairs.json`, `split.json`, `check.json`, `guided.json`. Figures: `tex/figs/step1_edgefit_error.png`; check figures `desktop/padanalysis/fig/align_poor_<pre>_<post>.png`, `align_guided_<file>.png`. |
| `white_study.py` | **Open study** (proof-of-concept step 2) | The white each post-image pixel is divided by. 2a (done 2026-11-11): edge-band darkening A_k per edge and channel from the pre-images, bands needed, the corner rule. 2a-ii (2026-11-11): saved edge-darkening models (exponential decay with distance from the edge, fitted over all F/G/H pre-images with poorly fitting edge pixels dropped), held-out-sample tests, corner options; the models lost to each photo's own band means and are dropped for now (Bill, 2026-11-11). 2b (2026-11-11): camera noise from unmoved repeats (`test_image/IMG0005`–`0010`), exposure rungs, reseated pre-images. 2c (2026-11-11): texture the model white lacks and the best blend weight λ, by formula and by direct trial on unmoved and reseated repeats; the blend gains 3–5 % per pixel in the interior and moves pad means by under 0.0001 A. 2d-i (first pass, 2026-11-11): share of the paper's grain showing in each post-image (`carryover`) and photo sharpness by repeat (`sharpness`); later repeats are blurrier and show less grain, with or without dye. 2d-ii (dyed edges) waits for Bill's new trials (C1047, stained evenly to every edge, now makes it possible). 2e (2026-11-13, `closing`, `lam_by_zone`, `summarize_closing`): the step's closing number. The blend against the plain pre-image divisor changes a pad's mean A by at most 0.00004 A, while replicate strips at 100 µg/L differ by 0.011 A (photo_red.R) and 0.026 A (green.B) standard deviation, so the white is polish. | JSON: `desktop/padanalysis/white_out/bands.json`, `edge_models.json`, `edge_models_summary.md`, `edge_test_summary.md` (the full `edge_test.json`, 12 MB, stays uncommitted), `noise_still.json`, `noise_rungs.json`, `noise_reseated.json`, `texture_still.json`, `texture_pre.json`, `lambda_still.json`, `lambda_reseated.json`. `carryover.json`, `sharpness.json`, `closing.json` (each with an "about" block of definitions and units). Figures: `tex/figs/step2c_lambda_photo_red.R.png`, `step2c_lambda_green.B.png`, `step2d_grain_carryover.png`; each has a plain-language `.txt` beside it. Figures: `tex/figs/step2a_bands_channels.png`, `step2a_bands_holders_photo_red.R.png`, `step2a_bands_holders_green.B.png`. |
| `drift_study.py` | **Parked** (first look 2026-11-11; deferred by Bill 2026-11-13) | Does the stain change from one capture to the next? Per sample and LED channel, the change in A per capture across the repeat post-images (each repeat lined up on the first), the rough dye level, seconds per capture and delay after reagent 2. Red channels darken 0.002–0.017 A per capture on dyed pads; violet/blue barely change; strips with little or no dye (C1038–C1040) stay within ±0.0004 A. Also the C1046 photo_red light test (`light_test`, `show_light_test`): C1046's first post-image against IMG0011–0014, taken after 10, 20, 60 and 180 s of extra photo_red light, with real photo times read from the files; photo_red.R darkened about 0.013–0.017 A per suite whatever the extra light, violet.B under 0.001. Reopen with a dark-wait control strip and, if possible, single-LED doses (see the docstring). | JSON: `desktop/padanalysis/drift_out/repeats.json`, `drift_out/light_test.json`; figure `desktop/padanalysis/fig/drift_C1046_photo_red_light.png` (+ `.txt`); tables to the terminal. |
| `shading_review.lineup_noise()`, `lineup_noise_table()`, `show_lineup_noise()`, `show_lineup_ratio()` (R4) | **Record**, reused by `align_study` | Texture left after lining up. | Figures: `fig/<label>_<what>.png`. |
| `padregistration.py` | **Parked** (shelved 2026-09-08; the work plan may try it once in step 1) | Lining up refined by correlating the pad edges. About 10% less noise in R_inf, about 100 ms. Reopen only if `align_study` shows the arithmetic map isn't good enough. | Returned arrays. Write-up: `docs/empirical_registration_investigation.md`. |
| `shading_study.warp_of()`, `arith_warp()`, `_ecc()`, `shift_surface()` | **Record** | Compared the arithmetic map with image-matching methods (OpenCV ECC). | Figures: `fig/shading_study_registration.png`, `_surface.png`. |

---

## Step 5. The white, one value per pixel

**Purpose.** Find what each pad pixel would read with no dye, so that
dividing by it leaves only the change the arsine made. The white is the pad
itself in the pre-image, never the bare paper below it.

**Method in the code now.** `analysis_1.pre_white()` reads the pre-image pad
(eroded 6 px), balanced against its own gray chips, and returns **one
number per channel**: the median R over the pad.

**Method in `chain_unified.tex`.** A white for every pixel: the lined-up
pre-image blended with a smooth model white that includes bands along the
pad edges, Ŵ = λ·W + (1 − λ)·S′·ℓ. **Not built.** It is
proof-of-concept step 2.

| File / function | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `analysis_1.pre_white()` | **Current** | One white per channel from the pre-image pad. | Returns `(R_white, rung, why)`. |
| `shading_review.py` R1–R3 (`show_gray_maps`, `show_gray_hist`, `show_split_hist`, `gray_table`, `post_pre`, `r2_*`, `r3_methods`, `r3_numbers`, `show_r3_*`, `show_white`, `r3_example`, `tex_figs`) | **Record** (rim over-correction and edge-aware smoothing deferred) | How even the light is over the pad; the post-image divided by the pre-image four ways. | Figures: `fig/<label>_<what>.png`; `tex/figs/shading-*.pdf` for `tex/shading_removal.tex`. Notes: `docs/shading_study_2026-09-29.md`. |
| `shading_study.py` (`correct`, `white_stats`, `pre_shared`, `post_repeat`, `show_*`) | **Record** | Can the pre-image say where the pad is shaded? First version of the study. | Figures: `fig/shading_study_<what>.png`. |
| `chain_example.lineup_mean_check()`, `smooth_mean_check()` | **Record** | Should lining up and smoothing average reflectances or absorbances? | Terminal report. |

---

## Step 6. The ratio, R_inf

**Purpose.** R_inf is the pad's reflectance divided by its own white. 1.0
is unreacted; smaller is darker.

**Method.** `padphot.reflectance_infinity` divides R (step 3) by the white
from step 5. Because the white is the same pre-image pad, the arithmetic
check is that R_inf on the pre-image pad has a median of 1.0000
(`cell_table(..., sec=PRE)`).

| File / function | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `padphot.reflectance_infinity(R, R_white)` | **Current** | `R_inf = R / R_white`. | Returns an array. |
| `analysis_1.cell_table()`, `contact_sheet(quantity="R_inf")` | **Current** | The arithmetic check and the picture. | Terminal report; `fig/analysis1_<samp_no>_cells_R_inf.png`. |

---

## Step 7. Absorbance, once, after all averaging

**Purpose.** A = −log10(R_inf). Bigger means darker (more dye). The unified
chain takes the log only once, after any averaging has been done on R.

**Method.** `padphot.absorbance`. The Kubelka–Munk functions are a second
way to rescale the same measurement. They are still computed but the
unified chain doesn't use them.

| File / function | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `padphot.absorbance()` | **Current** | A = −log10(R_inf). | Returns an array. |
| `padphot.km_remission()`, `km_remission_signed()`, `km_reflectance()` | **Parked** (not in the unified chain) | K/S = (1 − R_inf)² / (2·R_inf), and its signed form and inverse. | Returns arrays. Argued for in `tex/gutzeit_analysis.tex`. |
| `padphot.delta_ks()`, `ks_wetting_corrected()`, `sigma_low_range()`, `area_saturated()` | **Parked**, never called | Blank-corrected K/S and two readouts built on it. | — |

---

## Step 8. Yellow and brown

**Purpose.** The pad holds two colored products: yellow at light arsine
loading, brown at heavy. Split each pixel's absorbances into an amount of
yellow and an amount of brown.

**Method in the code now.** `duotone.PadCube` holds one pad as A per pixel
per channel, white = pre-image pad. `duotone.contrast` removes the part every
channel shares (meant as shading), and `duotone.endmembers` takes the two
extreme colors of the remaining cloud as yellow and brown. **Both have known
faults** (found 2026-09-28): `contrast` also removes the dye's even
darkness, which is most of brown, and `endmembers` returned nearly the
mirror images of yellow and brown. `run_sampno()` carries a note not to run
`cell_PCA` until this is fixed.

**Method in `chain_unified.tex`.** Two fixed reference colors, **y** (palest,
lowest arsenic) and **b** (darkest, highest), chosen once and averaged over
their stains. Then (γ, c_Y, c_B) = M·A per pixel, M = (SᵀS)⁻¹Sᵀ. PCA is only a
check. **Not built.** It is proof-of-concept step 3.

| File / function | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `duotone.PadCube`, `build()`, `cell_name()`, `cell_nm()` | **Engine, current** | One pad as A per pixel per channel, on a channel set shared by all pads. | — |
| `duotone.contrast()`, `endmembers()`, `fit()`, `unmix()`, `classify()` | **Engine, known faulty** | The current yellow/brown split. | Returned arrays. |
| `duotone.hue_plane()`, `residual_rms()`, `amount_sigma()`, `noise_cov()`, `j_hue()`, `j_amounts()`, `subset_sigma()`, `subset_unmix()` | Engine | PCA plane, model misfit, noise on the amounts, and scores for small channel sets. | — |
| `analysis_1.cell_PCA()` | **Current, on hold** | The split on one pad, with figure; `run_sampno` also stores the result. | Figure: `fig/analysis1_<samp_no>_pca.png`. Interim store: `duotone/yellow`, `duotone/brown`, `<cell>/KS` in `analysis/samples/<samp_no>.h5`. |
| `pca_study.py` (`show_own_endmembers`, `show_dot_edges`, `show_ring_paths`, `shared_map`, `ring_colors`) | **Record** | Should yellow and brown be fixed once from many pads instead of fitted per pad? | Figures: `fig/pca_study_<what>.png`. Notes: `docs/pca_study_2026-09-27.md`. |
| `duotone_guide.py` | **Record** | Figures for the `duotone` write-up, on C1017–C1025 and C1028–C1036. | Figures: `tex/figs/duotone-split.pdf`, `-plane.pdf`, `-spectra.pdf`, `-unmix.pdf` for `tex/duotone_guide.tex`. |
| `archive/pca.py` | **Deprecated** | Runs a multi-minute analysis when imported; hard-wired to five captures. Don't import it. | Figure: `archive/fig/pca.png`. |
| `archive/report6.py` | **Broken** (record) | The single-dye model, and whether X15 differs spectrally (August set). | Figure (old): `archive/fig/spectra.png`. |
| `archive/report14.py` | **Broken** (record) | Ranked small channel sets for separating yellow from brown. Stopped running with the 2026-09-27 white change. | Figure (old): `archive/fig/report14_ranking.png`. |
| `archive/look3.py` | **Broken** (record) | Pads beside their white/yellow/brown call. | Figure (old): `archive/fig/look3.png`. |

---

## Step 9. Arsenic mass per pixel, then summed

**Purpose.** Turn each pixel's yellow and brown amounts into arsenic, add
over the pad, and calibrate against known concentrations.

**Method in `chain_unified.tex`.** m = Σ F(c_Y, c_B) over pad pixels,
starting with F linear, compared with `manual_read` × sample volume.
**Not started.** It is proof-of-concept step 4.

The only dose-response code is the August pipeline below
(`archive/report10.py`), which works on pad averages against the bare paper,
is superseded, and no longer runs.

---

## Side study: finding the dot

**Purpose.** On captures C1028–C1036 the arsine reached a round dot on the
pad, not the whole pad. A `find_dot` function would locate the dot's edge so
that pixels can be grouped by distance from it.

**Method.** Profiles across the pad and their derivatives; the dot edge as
the halfway line between white and dot; rings by distance from that edge.
The dot geometry isn't final, so this is parked.

| File / function | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `dot_study.py` (`show_cell`, `show_profiles`, `dot_edge`, `fade_profile`, `show_fade`, `cumulative`, `show_cutoff`, `ring_ratios`, `show_rings`) | **Record** | Looking-at steps toward `find_dot`. | Figures: `fig/dot_study_<samp_no>_<what>.png`. Notes: `docs/dot_study_2026-09-27.md`, `docs/resume_prompt_find_dot.md`. |
| `find_dot` | **Not built** | — | — |

---

## Write-up figure scripts for earlier chain drafts

| File | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `chain_example.py` | **Record** | Follows two pixels through every step, printing each value under its symbol. Written for `tex/analysis_chain.tex`, which `chain_unified.tex` replaces. | Terminal report. |
| `R_inf_guide.py` | **Record, out of date** | Figures for `tex/R_inf_guide.tex`: scalar against per-pixel divisor, bare paper as white. Both choices have since changed. | Figures: `fig/R_inf_guide_<name>.png` / `.pdf`. |
| `archive/R_guide.py` | **Deprecated** | Started ahead of need; Bill asked to set it aside (2026-09). | Figures: `archive/fig/R_guide_<name>.png`. |

---

## The August 2026 pipeline (superseded as a whole, archived)

**Purpose.** The first analysis of the C1002–C1014 strips: one reflectance
per pad per channel, then dose-response, replicate agreement and the
effect of the edge.

**Method.** `measure.py` averages each pad rectangle (eroded 6 px), divides
by the gray chips, and writes one record per capture × LED × layer × rung to
`records.json`. `chemistry.Reduction` divides the pad by the **bare paper**
below it and takes −log10. The reports read `records.json`.

**Why superseded.** The white is now the pre-image pad, not the bare paper
(decided 2026-09-08). The chain now works pixel by pixel rather than on pad
averages. And `records.json` is dated 2026-08-18, so it predates the
dark-level and per-pixel gray-chip changes of 2026-09-27. These files are
the record behind `docs/pad_analysis_2026-08-10.html` and
`docs/pad_analysis_2026-08-13.html`.

**Where they are.** On 2026-11-11 the scripts, the three record files and
their figures moved to `desktop/padanalysis/archive/` (figures in
`archive/fig/`). Each archived script now also looks in `padanalysis/` for
the modules still in use (`probe`, `padrect`, `whitecorr`, `samples`,
`fields`, `chemistry`), and reports 9–13 read `archive/records.json`. Run
them as `.venv/bin/python3 desktop/padanalysis/archive/<name>.py`. A run
overwrites the old figure in `archive/fig/`; the originals are in git.

**Which still run** (tested 2026-11-11 with a 4-minute cap): `report1`,
`report2`, `report7`, `report9`, `report11`, `report12`, `look1`, `look2`.
`report3`, `report5`, `report6`, `report8`, `report10` and `report13` stop
with errors, and they stop the same way when run from their original
folder, so the move didn't cause it. `report10` and `report13` stop on a
missing session entry for C1014; `report3`, `report5` and `report6` on an
array-size mismatch (150 rows against a smaller mask); `report8` on an empty
array. `measure.py`, `report4.py` and `sweep.py` were not run, because the
first two overwrite the record files.

| File | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `archive/measure.py` | **Superseded**, not tested | Pad averages per channel and rung, screened for clipping. Running it overwrites the record file. | JSON: `archive/records.json` (`rect` finder) or `archive/records_blob.json` (`blob`). |
| `chemistry.py` (stays in `padanalysis/`) | **Engine** for `cell_name()` only; `Reduction` **Superseded** | ρ = R_pad / R_paper, A, and A less the blank. `analysis_1` and `dot_study` import its `cell_name`. Its own self-test looks for `records.json` beside it and will no longer find it. | — |
| `archive/sweep.py` | **Superseded**, not tested | Re-runs `measure` at erosions 4–12 px in a temporary folder. | Terminal table. |
| `samples.py` (stays) | **Engine, frozen** | The hand-written inventory, ending at C1014. | Terminal report. |
| `archive/segment.py` | **Superseded** by `padrect` | Free-form pad finder. | — |
| `archive/look1.py`, `archive/look2.py` | **Superseded**, runs | First looks at the five-strip set. | `archive/fig/look1.png`, `look2.png`. |
| `archive/report1.py` | **Record**, runs | Exposure consistency; reflectance against ppb. Hard-wired to X15, C1002–C1005. | Terminal report. |
| `archive/report2.py` | **Record**, runs | The edge effect by depth. | `archive/fig/edge_effect.png`. |
| `archive/report3.py` | **Broken** | Is the edge effect light or chemistry? | `archive/fig/edge_origin.png` (old). |
| `archive/report4.py` | **Record**, not tested | Erosion sweep and replicate agreement. Running it overwrites `ratios_by_erode.json`. | `archive/ratios_by_erode.json`; terminal report. |
| `archive/report5.py` | **Broken** | Channel ranking; predicted ppb; spread inside the pad. | Terminal report. |
| `archive/report6.py` | **Broken** | Single-dye model (see step 8). | `archive/fig/spectra.png` (old). |
| `archive/report7.py` | **Record**, runs | Rectangle against free-form finder. | `archive/fig/padrect.png`. |
| `archive/report8.py` | **Broken** | What the finder choice changes. Needs both record files. | Terminal report. |
| `archive/report9.py` | **Record**, runs | What the instrument contributes (exposure consistency, bare paper across strips). | `archive/fig/report9_paper.png`. |
| `archive/report10.py` | **Broken** | Dose-response and replicate agreement. | `archive/fig/report10_dose.png`, `report10_cells.png` (old). |
| `archive/report11.py` | **Record**, runs | The two strips imaged before and after. | `archive/fig/report11_pairs.png`. |
| `archive/report12.py` | **Record**, runs | Structure inside the pad; splash test. | `archive/fig/report12_structure.png`. |
| `archive/report13.py` | **Broken** | Shielding, session and timing against the scatter. | `archive/fig/report13_timing.png` (old). |
| `archive/report14.py`, `archive/look3.py` | **Broken** | See step 8. | — |
| `archive/pca.py`, `archive/R_guide.py` | **Deprecated** | See step 8 and the write-up section. | — |
| `archive/REGISTRATION.md` | **Deprecated** | Summary written ahead of need. | — |
| `archive/records.json`, `records_blob.json`, `ratios_by_erode.json` | **Record** (data) | The August reductions. | — |

---

## How to keep this document current

When a step changes:

1. Update that step's "method in the code now" paragraph and its table.
2. When a proof-of-concept step moves from study to production code, list
   the new function as **Current** and move the study script to **Record**.
3. If `analysis_1.CHAIN` changes, update it in "Where the chain lives".
4. Change the "Last reviewed" date at the top.
5. Keep the step numbers matched to `tex/chain_unified.tex`.
