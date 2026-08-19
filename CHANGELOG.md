# Changelog

Notable changes to the Colorimeter, in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
form. The project follows [Semantic Versioning](https://semver.org/); being on
0.x is the alpha statement — anything may change. See
[CODING_GUIDELINES.md](CODING_GUIDELINES.md#versioning-and-releases) for when
each number moves.

## Reading this file

Every release opens with two lines, because the version number alone does not
tell you whether a desktop tool can open an instrument's captures:

- **Writes** — the format versions an instrument at that release stamps into
  what it produces.
- **Reads** — the format versions a checkout at that release will open.

So a desktop checkout can read an instrument's archives when the instrument's
**Writes** numbers fall inside the desktop's **Reads** ranges. Nothing branches
on the release number itself; `software_ver` records which build wrote a file,
never whether it can be read.

The version an instrument is running is stamped into its own database at every
launch, and superseded rows are kept, so the instrument carries its own history:

```sh
sqlite3 src/colorimeter.sqlite \
  "SELECT value, add_dt, active FROM config
    WHERE role='device' AND name='software_ver' ORDER BY add_dt"
```

---

## [0.7.0] — 2026-08-18

Writes: archive schema 4 · meta 2 · database 2
Reads:  archive schema 1–4 · meta 1–2 · database 2

Two protocol changes in one release: the strip is now photographed before it
meets any reagent, and reagent step 2 is timed against the sample's
temperature instead of by a fixed countdown.

MINOR, on four counts from the table in CODING_GUIDELINES: config parameters
were added (`capture.preimage_enabled`, `capture.preimage_led_order`,
`capture.preimage_rung_shift`, `protocol.t2ref25_secs`, `protocol.q10`), one
was retired (`protocol.reagent2_secs`), the protocol gained a step, and the
archive schema moved to 4. The schema change is additive — everything a schema-3
reader knows about is exactly where it was, and the new content hangs off a
`/pre` group it will simply not see — but it is a layout change and so the
version moves. Archives written here are readable by a 0.4.x–0.6.x checkout,
which will find the measurement sweep and miss the pre-image. Reading the
pre-image needs this release or later.

### Added

- **The pre-image.** A run now photographs the dry, unreacted strip before
  reagent 1, so the reading can be divided by *this* pad rather than by an
  average pad. A reagent pad carries a colour of its own that varies from
  strip to strip by about as much as 5 ppb of arsenic develops, so near the
  bottom of the range the blank is a thing to measure, not a constant to
  assume. Both sweeps of one strip land in one archive: the pre-image creates
  it and writes under `/pre`; the measurement reopens the same file and writes
  at the root as before.
- A new protocol step, `PreImagePhase`, between entry screen 2 and reagent 1.
  One button, shaped like a reagent step — the run itself advances the slot.
  It stamps `samples.h5file`, being the run that creates the archive.
- Config `capture.preimage_enabled` (bool, seeded on) puts the step in or
  leaves it out of the chain entirely; `capture.preimage_led_order` (str,
  seeded `royal_blue,red`) is the sweep; `capture.preimage_rung_shift` (int,
  seeded −2) is how far below e\* it shoots. They are deliberately separate
  from the measurement's own `led_color_order` and exposure group, because the
  two sweeps are tuned against opposite constraints.
- `exposure_repo.shift_exposures()` — move a calibrated ladder by whole rungs.
  The ladder puts the *gray card* near half scale, and an undyed pad reflects
  about 2.2x the card in royal blue, so it would clip at e\*; two rungs down
  is 1/2.25, which lands it near mid-scale.
- `CaptureSuiteWriter` takes a `section` (the group prefix a run writes under)
  and a `mode` (`"w"` to create, `"a"` to add to an archive another run
  opened). Each section carries its own `<section>/meta`, because the ROI can
  be edited between the two sweeps and each one's layers are a crop of
  whatever `bound` was in force when it ran.

- **Temperature-compensated reagent 2.** The Gutzeit colour development runs
  faster warm and slower cold, so a fixed wait does not develop every sample
  to the same extent. Step 2's countdown is now
  `t2ref25_secs · q10 ** ((25 − θ) / 10)`, computed per sample from the
  temperature entered on entry screen 2. `protocol.reagent2_secs` (420 s) is
  replaced by `protocol.t2ref25_secs` (720 s — the wait at the 25 °C
  reference) and `protocol.q10` (2.0); `q10 = 1` removes the compensation
  entirely. The reagent 2 screen shows where its duration came from.

### Removed

- `protocol.reagent2_secs`, deleted rather than deactivated or renamed. Nothing
  reads it, a parameter with no active row fails `db_bootstrap._verify()`, and
  renaming would carry 420 s across as if it had been measured at 25 °C, which
  it never was. Its history goes with it — the values only ever answered a
  question that is no longer asked.

### Why these LEDs, and this exposure

Measured on the fifteen-strip archive (`desktop/padanalysis`), not chosen by
eye:

- At 5 ppb the dye moves `royal_blue/B` by 0.0126 absorbance, `blue/B` by
  0.0051, `cyan/B` by 0.0030, and every red, amber and photo_red cell not at
  all below 64 ppb. Only royal blue needs a blank; red is carried as the
  dye-blind control, and it is long-wavelength light, which is the cheap kind.
- Strip-to-strip blank variation is 0.002–0.007 A at `royal_blue/B` — 16–56 %
  of the 5 ppb signal, 2–6 % at 16 ppb, 1–3 % at 25. The pre-image is worth
  having below about 10 ppb and does nothing above it.
- One exposure loses nothing: on an undyed pad the ladder's upper rungs clip
  and are discarded anyway, and where three rungs did survive ρ agreed to
  0.1–0.45 %.
- Dose is set by the LED count, not the rung count — each LED is lit 3.7–4.2 s
  whatever the ladder, of which 2.0 s is `LED_ON_WARMUP_S`. Two LEDs at one
  rung is ~5.3 s of LED-on against a measurement sweep's 31 s.

## [0.6.0] — 2026-08-12

Writes: archive schema 3 · meta 2 · database 2
Reads:  archive schema 1–3 · meta 1–2 · database 2

MINOR, on two counts from the table in CODING_GUIDELINES: config parameters
were added (`capture.photo_enabled`, `capture.photo_wb_gains`), and both
capture paths gained a step. No format version moved: the archive gains a
`/photo` dataset, but it is additive and optional — absent when the photo is
disabled or fails — so a reader must probe for it rather than infer it from
`schema_version`, and one that ignores it is unaffected. Archives written here
remain readable by any 0.4.x or 0.5.x checkout.

### Added

- A run now ends by photographing the sample under the white LED, rendered in
  colour, and stores it as `/photo` in the archive. Every other frame the
  instrument takes is deliberately colour-neutral because the measurement is
  the Bayer data; this one exists for a person to look at. It is taken after
  the dark frames, when all measurement is complete.
- The photograph is cropped to the `bound` ROI — the same region the archived
  Bayer layers cover — so it shows exactly what was measured and costs a
  fraction of a frame to encode and store. The crop rectangle is stored beside
  it, since a crop cannot otherwise be located in the sensor frame.
- Config `capture.photo_enabled` (bool, seeded on) turns the step off, and
  `capture.photo_wb_gains` (str, seeded `1.0,1.0`) holds the white LED's
  colour gains. **The seeded gains are uncorrected**, so until they are
  commissioned the photo looks like the neutral RGB stream; measure them by
  photographing a neutral card and dividing its green mean by red and blue.
- `BayerCam.set_color_rendering()` / `set_objective_rendering()` switch how the
  main RGB stream is rendered. The raw Bayer stream is tapped off upstream of
  every control involved, so this cannot change the objective data — which is
  what makes the photograph free of consequence for the measurement.

### Changed

- Normal-path runs now light the white LED once, at the very end, for the
  photograph. They still run no white calibration phase: white never comes on
  before or during the LED sweep, which is what that path exists to avoid.

## [0.5.0] — 2026-08-11

Writes: archive schema 3 · meta 2 · database 2
Reads:  archive schema 1–3 · meta 1–2 · database 2

MINOR, on two counts from the table in CODING_GUIDELINES: a config parameter
was added (`defaults.default_temperature`), and the entry workflow changed —
Begin now claims the sample number in place instead of advancing a screen. No
format version moved, so the pair above is unchanged from 0.4.1 and archives
written here are readable by any 0.4.x checkout.

### Added

- Config `defaults.default_temperature` (float, seeded 21.0 °C) — the sample
  temperature to record on an instrument run without a thermometer. Editable
  from Menu → Config → defaults like any other parameter. The Entry2 screen's
  Temp button reads it: with no probe on the 1-Wire bus the button is labelled
  `[21.0 °C]` and one press writes that value into the field, instead of the
  `err` it used to write. A probe that is attached but does not answer still
  reports `err` — the two cases are told apart by the new
  `arb.temperature_attached()` on the phase contract.

### Changed

- Sample numbers are now coordinated between slots A and B, which previously
  offered the same number and left the operator to keep them apart by hand.
  A number is *claimed* only by a row in `samples`, which Begin still writes;
  from that, two rules: no two slots ever display the same number, and the
  `ID` button means "give me the next unclaimed number", taking it from the
  table alone and moving any unclaimed slot showing it out of the way.
  Pressing `ID` when you already hold that number does nothing.
- Begin no longer jumps to entry screen 2. It claims the number, moves it into
  the header, fills User / Test / Test lot from the defaults, and relabels
  itself `Continue`; a second press advances. The operator therefore sees what
  was claimed before entering anything against it.
- The entry screens now lock in two states, flipped by Begin. Before it, the
  sample number is the only editable thing in the slot and the identity fields
  are empty — an unclaimed slot can be renumbered by the other slot's `ID`
  button, so it holds nothing worth losing. Screen 2, reachable unclaimed only
  as a `NEXT` preview, is visible but not typable. After Begin, the number
  locks and the identity fields open, writing through to the claimed row.
- Identity fields are filled from the defaults at Begin rather than when the
  entry screen is first drawn, so a sample records the defaults as they read
  at the moment it was claimed. `Arbiter._cfge_refill_slots()` existed to push
  edited defaults back into open slots and is gone: there is nothing left in an
  unclaimed slot for a config edit to land on.
- `slot.ready_to_begin()` requires only a sample number. User is no longer
  typable before Begin, so requiring it would have left the button unpressable
  on an instrument whose defaults could not be read.
- The "Sample number already in use" dialog offers `New number` and `Edit`
  instead of a lone `OK`, which returned to the entry screen with the rejected
  number still in the field and no way forward but retyping it.

### Fixed

- Pressing `ID` on an entry screen previewed with `PRIOR` after Begin rewrote
  the slot's sample number even though its row was already claimed, leaving the
  screen permanently disagreeing with the database. The same hole let User /
  Test / Test lot be retyped after Begin, where the edits reached no database
  at all; those three now persist to the claimed row when the view leaves the
  screen (`Arbiter._flush_entry`).
- A config edit overwrote a hand-typed sample number in any slot that had not
  begun, because the refill path reseeded the number along with the defaults.

- `roi.snap_even()` dropped the `role` attribute when it rebuilt a rectangle.
  The image viewer's Save looks the edited rectangle up *by role* to check
  that `sample` still lies inside `bound`, so the role-less copy made that
  rule skip the one edit it exists to police: the sample ROI could be dragged
  outside the bound crop and saved, leaving a sample the archive cannot
  re-measure. The stored `role` column was never affected — `roi.save()`
  carries it over from the row being superseded.

## [0.4.1] — 2026-08-09

Writes: archive schema 3 · meta 2 · database 2
Reads:  archive schema 1–3 · meta 1–2 · database 2

### Added

- Boot splash screen (`src/splash_screen.py`) and its completeness aggregator
  `src/startup_checks.py`: narrates each boot step, replays what ran before
  the display existed, and reports anything a default had to be loaded for
  with its own message and OK press. Absorbs two checks from
  `provisioning/deploy/commissioning_status.sh` that used to fail silently
  (bus device nodes, hardware group membership).
- Config `roi.required_roles` (non-editable): the ROI roles a run cannot
  proceed without — exactly one `bound`, one or more `sample`, one or more
  `white_balance` — moved out of code so a differently-commissioned unit can
  be given a different requirement. The boot check now reports over-fill and
  out-of-bound ROI problems as warnings instead of tolerating or failing on
  them silently.
- Config `defaults.remark` and a `remark` field on the Entry2 screen — a
  free-text note carried onto the sample record. (Noted in its own commit as
  an imperfect first pass: the default-autofill path is written but not yet
  wired to run automatically.)
- `pulldb.sh` / `pushdb.sh` — pull the instrument's sqlite database from the
  Pi, or push a desktop copy to it; both back up the destination file before
  overwriting.
- Live capture progress on the per-slot Capture screen: while a capture is in
  flight, the "Collected" timestamp is temporarily replaced with "Capturing
  {color}, {n} exposures" / "Capturing dark images, {n} exposures", updating
  per exposure and reverting to the timestamp once the run finishes.

### Changed

- ROI role `white` renamed to `white_balance`, to name what the region is for.
- Capture screen: the manual colour-read prompt is now its own label
  ("Manually read color:", red, above the instruction line) instead of being
  appended to the capture instruction text, and its entry field's border is
  now red. Capture instructions read "Insert strip & press Capture" /
  "Imaging - DO NOT TOUCH".

### Fixed

- `bayer_cam.DEFAULT_FOCUS_CAL_PATH` was hardcoded to
  `~/pi/Colorimeter/config/focus_cal.json`; it is now derived from the
  module's own location, so startup finds `focus_cal.json` regardless of
  checkout path. `startup_checks.check_focus_cal()` reads the same constant
  instead of a second hardcoded copy that could disagree with it.
- Saving a focus calibration from the recapture dialog now actually calls
  `cam.save_focus_cal()` — previously only the diopter value was written to
  config, not the calibration file itself.

## [0.4.0] — 2026-08-08

Writes: archive schema 3 · meta 2 · database 2
Reads:  archive schema 1–3 · meta 1–2 · database 2

### Added

- `layermodels` table in `colorimeter.sqlite`: the flat-field result the
  instrument reads, one row per LED colour × Bayer layer. Carries the two chip
  weights a measurement multiplies by (`divisor = w1·mean(chip1) +
  w2·mean(chip2)`), the percentiles the capture path screens layers against,
  and the polynomial that produced them as provenance. Read through the new
  `src/layermodel_repo.py`; written by `devdesk/flatfield_pool.py`.
- Config `capture.layer_keep_min_dn` / `capture.layer_keep_max_dn` — the signal
  window a Bayer layer must fall in to be worth storing.

### Changed

- **Archive schema 2 → 3.** A layer dataset may now be present but empty: one
  with no usable signal is stored as a zero-size array carrying its statistics
  (`omitted=True`, `raw_mean`, `raw_p10`, `raw_p90`, `raw_min`, `raw_max`)
  instead of 45 kB of noise. Readers of image data must call
  `capture_h5.is_omitted()` and treat an omitted layer as an absent one.
- Normal-path captures take **one** dark batch, after the LED sweep, instead of
  two. The "before" batch existed so a reader could difference the two and
  measure sensor drift across a run; it cost 30 settled captures and 5.3 MB per
  archive. `devdesk/roi_darkdiff.py`, which asked that question, now applies
  only to archives captured before this change. A dark layer is stored only
  where some lit frame at that exposure kept it.
- Together those take about **40 %** off an archive — 28.8 MB → 17.3 MB on the
  ZP1001 gray-card suite.
- Config `capture.dark_prefetch_budget_s` → `capture.prefetch_budget_s`. The
  stage it budgets no longer takes dark frames; what is left is camera setup
  and focus. `provisioning/config_defaults.sql` renames it in place, so value,
  history and commissioned default all come across.

## [0.3.0] — 2026-08-06

Writes: archive schema 2 · meta 2 · database 2
Reads:  archive schema 1–2 · meta 1–2 · database 2

First release with a version number. Everything before this reported `0.2`,
which was a hand-written row in the seed file that nothing ever changed.

### Added

- `src/version.py` — the release number and the archive/database formats this
  checkout can read, in one file. Nothing else in the project holds a version
  string.
- `config_repo.stamp_version()`, called by `db_bootstrap.ensure()` at every
  launch. An instrument now records what it is running, and — because the
  config table keeps superseded rows — every version it has ever run.
- A compatibility gate in the `devdesk/` tools: an archive outside this
  checkout's supported range is refused at the door, naming both versions and
  which way to sync, rather than failing somewhere deep in an analysis.
- `LICENSE` (MIT) and this file.

### Changed

- The whole settings surface folded into one `config` table, absorbing the
  `device`, `users`, `testkit`, `testlot` and `defaults` tables. `config`
  gained an `editable` column and unique indexes enforcing one active and one
  default row per parameter. **Database schema 1 → 2**; a pre-migration
  database is refused at launch with a hint to run
  `provisioning/migrate_v2.sql` once, by hand.
- `config_defaults.sql` is now the only place a default value is written down.
  The Python `SEED_PARAMS` fallback table is gone: a missing parameter raises
  `ConfigMissing` instead of the instrument quietly running on values that were
  not in its database.
- `device.software_ver` is no longer seeded from SQL — `src/version.py` owns it.
  It is the one exception to the rule above, because it is a fact about the
  running code rather than a setting.
- The config editor edits every setting on screen, not just the two reagent
  timers, and returns to the page it was left on.
- ROI geometry is seeded from `config_defaults.sql` rather than from Python; a
  missing ROI role is now a hard failure.
- Both slots refill from the defaults a config edit has just changed.
- `db_bootstrap.ensure()` requires `db_manager.DBNAME` to name the same file it
  is given, since the version stamp writes through `config_repo`.

### Fixed

- The LED sweep reads one list of LEDs rather than a list *and* a channel
  range, which could disagree.

## [0.2] — before 2026-08-06

Writes: archive schema 1–2 · meta 1–2 · database 1
Reads:  n/a — no compatibility check existed

Not a release: the value `0.2` sat in the seed file from 2026-07-21 and was
never updated, so every archive written between March and August 2026 claims to
come from it. Archives from this period are readable — schema 1 archives carry
no ROI geometry of their own and fall back to the `roi` table, which cannot be
verified against them.

[Unreleased]: https://github.com/panibillo/Colorimeter/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/panibillo/Colorimeter/releases/tag/v0.6.0
[0.5.0]: https://github.com/panibillo/Colorimeter/releases/tag/v0.5.0
[0.3.0]: https://github.com/panibillo/Colorimeter/releases/tag/v0.3.0
