# Design updates — prompt session 1

Dark-frame sampling and subtraction changes prompted by the dark-frame
analysis in `desktop/padanalysis/analysis_2.py` (round-dot series
C1017–C1025, channel B) and the chat working through it. Three separable
proposals so far; more may be appended here as the same session continues.

---

## Update 1: Reinstate before/after dark sampling

**Date:** 2026-09-11
**Status:** proposal — not yet implemented
**Prompted by:** dark-frame analysis in `desktop/padanalysis/analysis_2.py`, on
the round-dot series C1017–C1025 (channel B)

### Scope

This update is exactly one change: **capture a "before" dark batch again,
symmetric to the existing "after" batch, for both the pre-image lit sequence
and the measurement lit sequence.** Every archive gains a second, bracketing
dark sample per sweep instead of one.

It does **not** decide whether to shrink the exposure ladder used for darks,
how to combine before+after once both exist, or anything about per-pixel
fixed-pattern correction. Those are related but separable questions, listed
under "Explicitly out of scope" below so they don't get bundled in by
accident. (Update 2, below, is one of them, and now has its own decision
record rather than living as a bullet here.)

### Why

`analysis_2.py` characterized the dark frames on the current instrument and
protocol (channel B, 9 archives, 100 frames) and found:

| timescale | what it needs | can it be measured today? |
|---|---|---|
| 1 — seconds, within one file's dark batch | elapsed time vs. exposure order, which are collinear within a batch | only ambiguously (see below) |
| 2 — minutes, dark to lit, within one sweep | a dark taken *before* the sweep and one *after*, bracketing it | **no** — current archives hold only "after" |
| 3 — 10s of minutes to days, between files | many files' dark levels over real time | yes, and shows no linear drift, but a ~0.01–0.015 DN step at the first file of each session (suggestive of brief warm-up, not confirmed on 3 sessions) |

Scale 2 is the gap this update closes. It is also the timescale most directly
relevant to what a dark frame is *for*: subtracting the sensor's own signal
from the lit frame it is paired with. Right now that pairing is never
checked — the "after" dark is taken up to about a minute later than some of
the lit frames it is subtracted from (`docs/HOWTO.md`'s and
`capture_runner.py`'s own description of the sweep), and nothing in any
archive can say whether that matters.

Separately, `dark_exposure_check` found that dark level depends on exposure
duration only very weakly — a real but tiny effect (0.0225 DN across a 15×
exposure range, ANOVA permutation p=0.026) that is about 30× smaller than the
single-frame per-pixel noise (σ_ε ≈ 0.73 DN) it would be competing with, and
no detectable effect on the noise level itself (p=0.79). That finding is not
part of this update's scope, but it matters to the cost side of the
trade-off below, and it still rests on one Bayer layer only (see "Open
questions").

### History: this was the design once

`capture_h5.py` and `capture_runner.py` both still carry the reason it
changed, verbatim:

> Both capture paths now take a single batch, "after", at the end of the
> run. A normal-path run used to take two — "before" as well, so a reader
> could difference them and measure the sensor's drift across the run (that
> reader is `devdesk/roi_darkdiff.py`). It answered its question; paying for
> it every run cost 30 settled captures and 5.3 MB per archive. The `when`
> parameter and the "before_&lt;us&gt;us" naming stay, because archives
> already written use them and must keep reading correctly.

That measurement is exactly what `docs/reference_framework_preimage_and_chips.md`
reports under "Dark-Frame Spatial Structure": before/after differences of
±3–4 DN max, ~1 DN std, "negligible" — and it explicitly says so **only for
pre-2026-08 archives, which have matched before/after dark batches**. None of
the round-dot archives (C1016 onward) do. The conclusion that justified
dropping "before" was correct for the instrument and protocol it was measured
on; it is not evidence about the current one, because the current one no
longer produces the data that conclusion needs to be re-checked.

### What already supports this

Nothing in the schema needs to change:

- `CaptureSuiteWriter.add_dark()` / `link_dark()` already take a `when`
  parameter (`"before"` / `"after"`), and `_dark_key()` already keys the
  shared dark group on `(when, exposure_us)`. This has never been removed —
  only the caller that used `when="before"` was.
- `probe.Capture._dark_link()` already searches both `before_` and `after_`
  links and prefers `after` when both exist, so a reader written against
  today's after-only archives keeps working unchanged against an archive
  that also has a `before` batch.

The change is entirely in `capture_runner.py`: `run_prefetch()` (stage 1,
before the lit sweep) needs to take the dark batch it used to take there,
and `run_capture()` keeps the one it already takes at the end. Because
`run_prefetch()`/`run_capture()` are the shared machinery for both the
normal-mode and pre-image sweeps (`self._is_pre()` only changes which LEDs
and exposures are used, not the stage structure), reinstating the "before"
call in that shared path covers both sequences at once — it is not two
separate changes.

### What this buys

1. **Scale 2 becomes measurable, on this instrument, for the first time
   since C1016.** Once an archive has both a before and an after dark for
   the same exposure, the pattern in `dark_temporal_check` can finally be
   pointed at the thing it was meant to check: does the dark drift *during*
   a sweep, not just between sessions.
2. **A free noise reduction.** Averaging two independent dark samples cuts
   σ_ε by 1/√2 — 0.73 → ~0.52 DN on channel B — regardless of what scale-2
   turns out to show, simply because it's two samples instead of one.

### Cost, and what offsets it

The number this project already paid to find out once — 30 settled captures
and 5.3 MB per archive — was for a **full exposure ladder** taken twice. If
the exposure-independence finding holds up across all four Bayer layers (see
below), a "before" batch does not need to repeat every exposure the sweep
uses; a small, fixed set would do, at a fraction of that cost. That is the
natural way to afford this change, but it is a second decision, not assumed
here — see "Open questions." This also needs to be weighed against
`capture-speedup` branch goals (overlapping capture work with reagent
timers): reinstating any second dark batch adds time to the sweep unless the
exposure-count reduction more than pays for it.

### Explicitly out of scope for this update

- **Reducing the dark exposure ladder.** Supported by `dark_exposure_check`,
  but decided separately, and only after the channel check below.
- **How to combine before + after** once both exist — plain average is the
  obvious default, but whether `probe.Capture.layer()` should average them,
  and whether `_dark_link`'s "prefer after" default should become "prefer
  the average," is worth deciding against real before/after data rather than
  in the abstract.
- **Per-pixel fixed-pattern correction** — see Update 2. A different axis
  (space, not time), decided on its own terms below.

### Open questions / before implementing

- [ ] `dark_exposure_check` has been run on Bayer layer **B only**. Confirm
      the same (small or negligible) exposure dependence on R, Gr, and Gb
      before it's used to justify shrinking any exposure list.
- [ ] Decide the exposure set the reinstated "before" batch takes: the full
      per-sweep ladder (simplest, same cost as the historical "before"), or
      a reduced set (cheaper, pending the channel check above).
- [ ] Implement the `run_prefetch()` change in `capture_runner.py`; confirm
      it fires for both the pre-image and measurement sweeps without a
      special case.
- [ ] Decide the before/after combination rule for subtraction and for
      analysis, once real paired data exists.
- [ ] Re-run a `dark_temporal_check`-style analysis on the first archives
      that carry both batches, to actually measure scale 2 on the current
      instrument — the whole point of this update.
- [ ] Weigh the added capture time/storage against `capture-speedup` branch
      goals before merging.

### References

- `desktop/padanalysis/analysis_2.py` — `dark_temporal_check`,
  `dark_exposure_check`, `dark_stability_check`, `estimate_dark_offset`.
- `docs/reference_framework_preimage_and_chips.md` — "Dark-Frame Spatial
  Structure" (the pre-2026-08 before/after measurement this update revisits
  on the current instrument).
- `src/capture_h5.py` (module docstring) and `src/capture_runner.py`
  (`run_prefetch`/`run_capture` comments) — where "before" was removed and
  why; both hold the code path this update reinstates.
- `devdesk/roi_darkdiff.py` — the earlier before/after reader; may be
  reusable once new before/after archives exist.

---

## Update 2: subtract the dark frame's spatial mean, not its per-pixel array

**Date:** 2026-09-11
**Status:** proposal — not yet implemented
**Prompted by:** working through `dark_stability_check` and
`estimate_dark_offset` (`desktop/padanalysis/analysis_2.py`) in chat, asking
what each actually predicts and why.

### Scope

Change what a dark frame contributes to `lit - dark`: instead of the matched
dark frame's own per-pixel raw values, subtract that same frame's **spatial
mean**, uniformly, from every pixel — except at a small, separately
maintained set of pixels with a large, historically-confirmed fixed offset,
which keep their own correction.

This is independent of Update 1: whichever dark frame(s) Update 1 ends up
producing (one "after", or an average of "before" and "after"), this update
changes what is done with the result — a scalar per frame, not a per-pixel
array, subtracted from the lit frame.

### Why

Two variances describe the correction one pixel can be given, both measured
on channel B, C1017–C1025:

- **σ_p = 0.028 DN** — the true pixel-to-pixel spread of this sensor's own
  fixed bias pattern (`estimate_dark_offset`, cross-checked by
  `dark_stability_check`'s split-half reliability).
- **σ_ε = 0.731 DN** — the noise a single fresh dark sample carries at one
  pixel, independent of the lit frame it will be subtracted from.

Two candidate corrections for an ordinary pixel, compared against the true
(but unobservable) dark level at that pixel:

| correction | what it captures | RMS error |
|---|---|---|
| **A** — the frame's own spatial mean, `mu(now)`, applied to every pixel | the frame-level common-mode term, essentially exactly (`mu(now)`'s own sampling error, averaged over ~23,790 pixels, is ≈0.005 DN) | σ_p = **0.028 DN** |
| **D** — that same frame's own per-pixel value, `dark_i(now)` (today's practice) | the frame-level term **and** the pixel's true offset `p_i`, plus one fresh, uncancelled noise draw on top | σ_ε = **0.731 DN** |

MSE(A) = σ_p² ≈ 0.0008; MSE(D) = σ_ε² ≈ 0.534 — **A beats D by roughly 26× in
RMS.** This sensor's real per-pixel structure is small enough that a single
noisy sample of it (D, today's practice) does far more harm than not
correcting for it at all (A). This is the same relationship
`dark_exposure_check` found for exposure duration, now applied to the
subtraction step itself: the correction that looks more "complete" (per-pixel)
is worse in practice because it is built from a single noisy sample.

**Exception: the hot-pixel set.** `estimate_dark_offset` found ~12 pixels (of
23,790, channel B) whose own offset is large (0.4–1.4 DN) and reproducible
between independent halves of history — that reproducibility is what made
them outliers there. For those specific pixels, A's error (the full,
uncorrected `p_i`, systematic and repeated every frame) is worse than D's
(0.73 DN, random, unbiased). A is the right *default*; it is the wrong choice
at exactly the pixels a per-pixel correction would matter most for. Those
pixels need their own, separately maintained correction — built from pooled
history the way `estimate_dark_offset` already does, not from a single fresh
sample either.

**Why `mu(now)` and not a pooled historic frame-mean.** `mu(now)` is already
close to exact (~0.005 DN sampling error, from a single frame). Pooling it
with other frames' means cannot improve that precision further — there is
almost no sampling noise left to average away. Substituting a historic
average, `mu_bar`, for `mu(now)` would only be justified if the frame-level
term is truly constant over time, and `dark_temporal_check` (Update 1)
could not confirm that either way: scale 1 found no trend distinguishable
from noise (p=0.165, but from one 22-point sequence — weak evidence, not
proof of stability); scale 3 found no clean linear drift but an unconfirmed
session-start step. Using `mu(now)` costs nothing (it is already precise)
and needs no assumption about constancy; using `mu_bar` risks being
measurably wrong for the very session it is applied to, for no offsetting
gain. `mu(now)` dominates whether or not the drift turns out to be real.

### What would need to change

- `probe.Capture.layer()` — currently returns `lit - dark` as a full
  per-pixel array minus a full per-pixel array. Would need to return
  `lit - mu(dark)` for the bulk of pixels, with a lookup against a
  maintained hot-pixel table overriding the small number that need it.
- ~~A place to store and refresh the hot-pixel table...~~ **Done, offline.**
  `src/darkframe.py` (a numpy-only, no-scipy, Pi-safe engine —
  `identify_hot_pixels()`, `correct_dark()`, ...) and `src/hotpixel_repo.py`
  (the `hotpixels` table: one row per flagged pixel, keyed on camera
  model/serial/resolution/Bayer layer, versioned like every other table
  here) now exist, built and unit-tested against
  `tex/dark_signal_characterization.tex` §9's math.
  `desktop/padanalysis/hotpixel_analysis.py` is the offline harness that
  pools archives, finds the hot pixels, and writes `hotpixel_repo`. What
  does **not** exist yet is either half of putting this to work on the
  instrument: nothing in `capture_runner.py` calls `darkframe.correct_dark()`
  during a real capture, and there is no periodic/production job that
  re-runs the desktop harness's pooling step and refreshes the table as more
  calibration data accumulates. See Update 3, below, for both.
- Everything downstream of `layer()` (`padphot`, `get_cell_data`,
  `analysis_1`) is unaffected in interface — it still receives a
  dark-subtracted array of the same shape — so this change is contained to
  the subtraction step itself.

### Explicitly out of scope for this update

- Which dark frame(s) `mu(now)` is computed from — that is Update 1, and its
  open question of how before+after get combined.
- Confirming the session-start warm-up hint, or otherwise resolving the
  scale-1/scale-3 drift question — already flagged as open under Update 1.
- Whether R, Gr, Gb show the same σ_p ≪ σ_ε relationship as B — only B has
  been checked (the same caveat as Update 1's exposure-independence finding).

### Open questions / before implementing

- [ ] Confirm σ_p ≪ σ_ε on R, Gr, Gb, not just B, before generalizing
      mean-not-array subtraction to every cell.
- [x] Decide where the hot-pixel table lives — the `hotpixels` table via
      `src/hotpixel_repo.py`; see "What would need to change," above. Still
      open: the production refresh cadence — see Update 3.
- [ ] Decide the threshold that defines "hot enough to need its own
      correction" — `estimate_dark_offset` used 5× `r_i`'s own predictive
      spread as a first cut; `darkframe.identify_hot_pixels()`'s current
      default (`k=8`) is likewise a starting point, not a derived constant
      (see that function's own docstring) — worth a second look once R, Gr,
      Gb have been run, not a final answer.
- [ ] Re-derive σ_p, σ_ε (and the hot-pixel list) once Update 1's
      before/after data exists — averaging two dark samples changes σ_ε
      (and so the A-vs-D comparison), though not σ_p.
- [ ] Decide whether analysis code (`analysis_1.py`, `analysis_2.py`) should
      adopt this on the desktop side first, ahead of any change to
      `probe.Capture.layer()` or the Pi capture path, as a lower-risk way to
      see the effect on real R_inf/K-S numbers before touching the
      instrument.

### References

- `desktop/padanalysis/analysis_2.py` — `dark_stability_check`,
  `estimate_dark_offset`.
- Update 1, above, for the temporal-drift evidence this update's `mu(now)`
  argument depends on.
- This session's chat, working through the A/B/C/D predictor comparisons.

---

## Update 3: get `darkframe.py` onto the instrument, and an interim capture policy to feed it

**Date:** 2026-09-12
**Status:** proposal — not yet implemented
**Prompted by:** `src/darkframe.py` and `src/hotpixel_repo.py` (built and
unit-tested this session, implementing Update 2's correction rule and hot-pixel
table) existing only as a desktop-exercised engine and database table, with
nothing in the capture path or a production pipeline using either yet.

### Scope

Update 2 proposed subtracting `mu(dark)` plus a hot-pixel exception list;
that proposal is now backed by running code (`src/darkframe.py`'s
`correct_dark()`, `src/hotpixel_repo.py`'s `hotpixels` table), but two things
are still missing before it does anything for the instrument, and this
update covers both:

1. **Capture-sequence wiring** — nothing on the Pi calls
   `darkframe.correct_dark()`. It is exercised today only by
   `desktop/padanalysis/hotpixel_analysis.py` against archived `.h5` files.
2. **The analysis component that keeps the hot-pixel table current** — does
   not exist at all yet, on the Pi or the desktop. `hotpixel_analysis.py` is
   a hand-run script for one investigation, not a maintained job; nothing
   re-pools fresh archives and calls `hotpixel_repo.write_hotpixels()` as
   more data accumulates the way, say, a periodic exposure or layer-model
   recalibration would.

Bundled with both is a third, narrower question this update also settles:
**what the capture sequence should do in the meantime**, while items 1 and 2
above are still being built. That is not a detail to defer — the data those
two eventually need is dark frames from real production/calibration
captures, and if the capture sequence is trimmed down before they exist, the
project loses the very data both depend on.

### Why

Everything this document and `tex/dark_signal_characterization.tex` rest on
came from dark frames captured for an entirely different reason (measurement
sweeps), pooled after the fact. That worked once, retrospectively, on
C1017–C1025. It should not be treated as a one-time exercise: the instrument
is still being developed and calibrated, meaning its dark-frame population
(and hence its hot-pixel map) is exactly as unsettled as everything else
about it — a fixed pattern found today is evidence for today's sensor and
today's protocol, not a permanent fact. Continuing to capture dark frames
generously, on purpose, during this period is what keeps that kind of
analysis re-runnable rather than dependent on however many old archives
happen to survive.

### What this proposes

**1. Capture-sequence wiring (extends Updates 1 and 2).**

- Implement Update 1 in full: every sweep — pre-image and measurement —
  takes both a "before" and an "after" dark batch. `darkframe.correct_dark()`
  needs *a* fresh dark frame; which one (before, after, or their average) is
  still Update 1's own open question, not decided here.
- Wherever the dark subtraction step ends up living (`probe.Capture.layer()`
  today; Update 2's own open question on Pi vs. desktop first still stands),
  it reads `hotpixel_repo.read_active()` once per camera context — not once
  per frame, the way `layermodel_repo.keep_channels()` already reads its own
  table per LED rather than caching it, but at the coarser grain of "once per
  session," since the camera context (model, serial, resolution) cannot
  change mid-run — and passes the result to `darkframe.correct_dark()`.
- A context with no `hotpixels` rows (a freshly commissioned instrument, or
  one whose resolution just changed) needs no special case:
  `darkframe.correct_dark()` already degrades to plain mean subtraction when
  handed an empty hot-pixel list.

**2. The analysis component (net new).**

- A job — most plausibly a `desktop/padanalysis` or `devdesk` script run by
  hand at first, the same maturity level `hotpixel_analysis.py`,
  `flatfield_pool.py` and `run_bound39a.py` are already at, rather than
  something scheduled on the Pi from day one — that pools recent
  archives, re-runs `darkframe.identify_hot_pixels()` per Bayer layer, and
  calls `hotpixel_repo.write_hotpixels()`.
- Needs a decided cadence and trigger before it can be called "built" rather
  than "possible" — see Open questions.

**3. Interim capture policy, until 1 and 2 exist and are validated.**

- **Keep before *and* after, every sweep, starting now.** This is Update 1
  restated as a direction rather than a proposal: it is the prerequisite for
  everything else in this update, and there is no reason to wait for the
  analysis component to be built before starting to collect the data it will
  need.
- **Do not capture a dark batch at every `exposure_us` in the sweep.**
  Update 1's own "Cost, and what offsets it" already identified this as the
  natural way to afford reinstating "before"; `dark_exposure_check`'s finding
  that dark level barely depends on exposure duration (Update 2's "Why")
  supports it directly. A small, fixed set of exposures — not the full
  per-LED ladder, twice — is enough to keep `mu(dark)` estimates current.
- **But do not shrink the set, or the frame count, so far that dark-frame and
  hot-pixel analysis on production data stops being possible.** The exact
  floor is an open question (below), but the guiding constraint is direct:
  `darkframe.identify_hot_pixels()`'s outlier test needs enough pooled frames
  (`n`) that `sigma_eps^2/n` stays small relative to `sigma_p^2`, the same
  n-dependence `tex/dark_signal_characterization.tex` §9 derives for the
  shrinkage weight `B`. A capture-time reduction driven only by storage
  and time budget, without checking it against that requirement, could
  quietly make this document's own kind of analysis impossible to repeat.
- **New candidate captures for the warm-up question.** §7 of
  `tex/dark_signal_characterization.tex` found a temporal-drift pattern it
  could not confirm or explain: a small step at the first file of a session
  (~0.01–0.015 DN) and an unconfirmed sawtooth at roughly 17-hour and
  47-hour intervals, suggestive of the instrument having just been powered
  on rather than a true optical drift, but based on only 3 sessions. Neither
  the current capture sequence nor the schema can identify "how long since
  this session started" today, so no future analysis can test the idea no
  matter how many archives accumulate. Proposed, not yet decided:
    - Record elapsed time since the instrument's own boot/session start (or
      simply a session id) somewhere in `capture_meta.py`'s `run` block, so
      archives can be grouped by session and ordered within one.
    - Capture one extra dark frame as early as possible in a session, before
      any lit exposure, and consider a second one a short fixed interval
      later (minutes, not the full sweep) — bracketing a possible transient
      the way `roi_darkdiff.py`'s original before/after design bracketed a
      sweep.

### Cost

Added capture time and storage from generous before/after batches (Update
1's own cost, now endorsed rather than left open) plus the new candidate
warm-up frames, partially offset by trimming the exposure list per batch.
Still needs weighing against `capture-speedup` branch goals, the same
caveat Update 1 already carries — this update does not resolve that
tension, it just says which side of it should not be cut short first.

### Explicitly out of scope for this update

- The exact reduced exposure set for before/after batches — depends on the
  R/Gr/Gb channel check, already an open item under Update 1.
- Whether `correct_dark()` runs on the Pi at capture time or stays a
  desktop-side post-processing step for now — Update 2's own last open
  question, sharpened here by having concrete code to place, but not decided.
- The recalibration cadence and trigger for the analysis component — named
  as a requirement here, decided under Open questions below once there is
  more experience running it by hand.

### Open questions / before implementing

- [ ] Where does the analysis component run (Pi, desktop, or both) and on
      what trigger (manual command, a fixed schedule, a capture count)?
- [ ] What minimum `n` (pooled dark frames) and how many distinct exposures
      keep `darkframe.identify_hot_pixels()`'s test meaningful — a floor for
      "how much can capture-time trimming take away" that today's capture
      sequence should not fall below even before item 3's other pieces are
      decided.
- [ ] Where session/boot-elapsed metadata lives (`capture_meta.py`'s `run`
      block is the natural place) and how it is populated — nothing records
      this today.
- [ ] Whether the two proposed warm-up frames (session-start and
      short-interval-later) are the right bracket, or whether more points
      are needed to actually resolve a ~17–47 hour-scale pattern from
      within-session data alone.

### References

- `src/darkframe.py`, `src/hotpixel_repo.py`,
  `desktop/padanalysis/hotpixel_analysis.py` — the implementation this
  update proposes wiring into the capture path and the (not yet built)
  recalibration job.
- Update 1 and Update 2, above — the capture-sequence and subtraction-rule
  proposals this one depends on and extends.
- `tex/dark_signal_characterization.tex` §7 ("Does the dark level drift with
  time?") — the warm-up hint this update's new candidate captures are meant
  to let a future analysis confirm or refute.
- `capture_meta.py` — where session/run metadata already lives and where a
  boot-elapsed or session-id field would be added.
