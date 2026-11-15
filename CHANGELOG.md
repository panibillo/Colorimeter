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

## [Unreleased]

### Added

- Two more pressure-logger signals (`baro_signal.py`, development only):
  - `CALIBRATE`, with no arguments, right after `NEWRUN`. This is well over
    10 seconds before reagent 2's `START`.
  - `STOP <samp_no> Canceled` when a run is canceled after reagent 2's
    `START` and before the measurement capture's `STOP`. Until now a cancel
    left the laptop recording.
- The test-variant screen has a "C µg/L" box beside the image-reps value,
  for a standard's arsenic concentration. It is saved in a new
  `treatments.standard_conc` column, which is added to an existing database
  at start-up.
  - Leave it empty for a run that isn't a standard; it is stored as NULL.
  - 0 is a valid entry, for a blank standard. A negative number or text is
    marked invalid and holds Continue back.
  - Unlike the other choices, it isn't carried over from the last run. A
    series of standards changes it every run, and a carried value would label
    the sample with the wrong concentration.
- A short beep when an image set is stored and another set is still to
  come, on both the pre-image and the measurement capture, so the operator
  knows to reposition the cassette. The last set doesn't beep; the run moves
  on by itself.
- **Pad finding on the instrument** (`src/padfind.py`, new). Before each
  pre-image and measurement sweep, the pad is found from one 10 ms frame lit
  by photo_red, with no warm-up. The LED is on for about 0.06–0.09 s, and
  pad finding takes 0.5–1.0 s in all (rpizero3, 2026-11-14).
  - It uses the same method as the desktop's `padrect.py` (cassette window
    layout only) and gives the same fit on all 60 pre- and post-images of
    the F/G/H archives. A photo_red offset moves the fit to where the
    desktop's all-channel fit lands, to within 0.01 px on average.
  - A post-image is guided by its pre-image's fit, read back from the same
    archive, with the sides within ±2 px.
  - A pad that cannot be found, or fails the quality checks (width ±2 px,
    tilt 2°, edge scatter 0.5 px), is logged and stored, and the sweep goes
    on.
  - `BayerCam.capture_held()` / `bayer_of()` / `release_held()` let a frame be
    captured with the LED on and converted after it is off.
  - The pad finding is timed in the capture profile, section `padfind`.
- **Archive schema 6.** Each sweep's section can hold a `pad` group: the
  pad-finding frame's R layer (`R`, bound crop, raw DN), the fit before and
  after the offset (`before_offset`, `after_offset`), and attributes for the
  LED, exposure, frames, timings and quality checks.
  `capture_h5.CaptureSuiteWriter.add_pad()` writes it and `read_pad()` reads it.
  The group is optional, so schema-5 readers' other paths are unchanged. This
  moves the next release to a MINOR bump.
- **Exposures set from the strip itself** (`src/exposure_rule.py`, new;
  detour stage 3). A pre-image and a measurement no longer take each LED's
  exposure_us from the `exposure` table. Each LED starts from a seed (the
  strip's own pre-image Tus for a measurement), and every frame is checked on
  the fitted pad and the two gray chips, hot pixels left out:
  - a pixel at 1023: the next try is 0.7 times as long;
  - the brightest pixel under 80 % of 1023: the next try is scaled so it
    lands at 85 %;
  - otherwise the frame is accepted (at most 4 tries; still clipped after 4
    marks the LED failed in its metadata and on screen).

  Three frames are then kept at that exposure (Tus), and a longer Bus frame
  is added when a pixel of an analysis channel falls below the noise floor
  (164 raw DN). Every analysis channel is stored; any other layer only when
  it is unclipped and has signal above the floor. The archive is written
  after the LED is off. On rpizero3 (2026-11-14) every LED was accepted on
  its first try, and LED on-time for three frames fell from 3.79 s to about
  2.95 s.
  - The noise-floor test (what calls for Bus) leaves out a left- or
    right-edge pixel whose darkening is more than 5 times that of the row
    next in: such a pixel is mostly outside the pad. The factor is from the
    edge-band study (76 pre-images: band 0 is 3.6-4.0 times as dark as band
    1, 90 % below 5). The clipping test keeps every pixel. On rpizero3 it
    left out 1-10 pixels per layer, photo_red no longer took a Bus frame,
    and green's Bus fell from about 42 to 32 ms.
  - `CaptureConfig.exposure_rule`: "strip" (default) or "group", the old
    rule, kept for comparison. Menu → Advanced → Capture Suite keeps
    "group", since it shoots an exposure group's spread.
  - In the archive (still schema 6): each LED's `led_metadata` has a
    `strip_rule` record (every try, every stored frame's statistics, why
    each layer was kept, Bus, coverage, the test region); its group has
    `tus_us`, `tus_status` and `bus_us`; each frame's group has `role`
    ('tus' or 'bus') and `applied_us`. Labels still run e0, e1, ....
- **The strip rule's settings are config rows** (detour stage 4a), read once
  per run, before the camera starts (`exposure_rule.RuleSettings`):
  - `protocol.multiple_exposure_count` (factory value 1; 3 for the trial
    strips), `protocol.bus_exposure_count` (1) and
    `protocol.analysis_channels`;
  - `capture.exposure_rule` ("strip" or "group"),
    `clip_top_dn` / `clip_bottom_dn` / `noise_floor_dn` (1023 / 65 / 164),
    `top_target_fraction` / `top_floor_fraction` (0.85 / 0.80),
    `clip_step_factor` (0.7), `max_exposure_tries` (4), `exposure_cap_us`
    (200000), `bus_margin` (1.1) and `edge_darkening_factor` (5.0);
  - for pad finding, `capture.padfind_led` (photo_red),
    `padfind_exposure_us` (10000), and `padfind_offset_pre` / `_post`.

  The factory values are the constants in `src/exposure_rule.py` and
  `src/padfind.py`, and a test keeps them equal. They are added to an
  existing database at start-up. A value that doesn't fit with the others
  stops a run before the camera starts, naming the row, and a new start-up
  check, "Exposure rule", warns about it. Each LED's `strip_rule` record
  holds the settings it was taken with (`settings`).
- **The `exposure_log` table** (`src/exposure_log_repo.py`, new; detour
  stage 4b). Every frame a strip-rule sweep measured gets one row per Bayer
  layer: the pad-finding frame, every try at Tus (rejected ones too, with
  the verdict), and every frame kept at Tus and Bus. A row has the sample,
  archive, section, purpose, try number, label, whether that layer is
  stored, the exposure_us asked for and applied, and the test-region
  statistics. The rows are written once per sweep, after the last coloured
  LED is off; on rpizero3 a 5-LED sweep's 77 rows took 0.04–0.1 s. A failed
  write is logged and never costs a capture. The table is created at
  start-up. Each LED's `strip_rule` record now also has `captured_dt`, the
  time of each stored frame.
- **Seed rows in the `exposure` table** (detour stage 4c). Each LED's first
  exposure_us in a pre-image now comes from its seed row (new columns
  `role` = 'tus' and `samp_no`; calibration rows get `role` = 'ladder' at
  start-up). Factory seeds are added at start-up for any LED without one:
  the D1 values for royal_blue, violet, cyan, green and photo_red, and the
  C1048 Tus for red, red_orange, amber and blue. A sample's pre-image
  replaces a seed when it had to re-aim and then found an accepted Tus,
  keeping the old row as history; a measurement or a bench run never does.
  A recalibration leaves seed rows alone, and the ladder reads ignore them.
  `exposure_repo.read_seed()` / `read_seeds()` / `write_seed()`. The
  start-up "Exposures" line counts the seeds. `exposure_rule.SEED_US` is
  gone.

### Removed

- The RESET button on the reagent screens, and its "Reset the timer?"
  dialog. No use for it had come up. A START pressed by mistake is now
  undone only by CANCEL.

### Changed

- Menu → View Image: the centre button of the navigation pad is now
  **Find/Pad** (it was Fit, which showed the whole frame). It flashes the
  pad-finding LED (`capture.padfind_led`), finds the pad the way a
  pre-image does (`src/padfind.py`), draws a 1 px outline just outside the
  pad's pixels, and zooms to the pad. The name strip shows the pad's size,
  or the first quality check it failed. The − button still zooms out to the
  whole frame.
- Pad finding measures the pad's top instead of placing it one fixed height
  above the bottom (`src/padfind.py` step 5, and the desktop's
  `padrect.py` for the cassette holders). The old height, 53.34 layer px,
  came from the open-channel holder; in the cassettes the pad measures 52.3
  (68 sweeps, sd 0.13), so the top sat about one row too high, on white
  backing, on every sweep. The top now moves down 0.8–1.5 rows; the bottom
  and sides do not move (sides by 0.09 px at most). The "reseat" fallback,
  which a dark mark on the pad could set off, is gone from the cassette
  layout. Fits record `top_source` ('measured' or 'height'); archives
  written before it read back as 'height'. Older open-channel captures fit
  exactly as before.
- The pad-finding offsets were re-measured with the new top:
  `capture.padfind_offset_pre` 0.10,0.44,0.43,0.33 (was 0.33 for the top)
  and `_post` 0.13,0.35,0.33,0.11 (was 0.11). An instrument still on the old
  factory values takes the new ones at its next launch; an operator's own
  value is kept. Desktop `chain_samples` offsets changed to match.
- View Image → Find/Pad now shows the photo_red frame it fitted (in gray)
  under the outline, instead of the white-LED picture, so the outline is
  judged against the pixels it was found on.
- The strip rule now keeps one frame per LED at Tus unless
  `protocol.multiple_exposure_count` says otherwise. It kept three before
  stage 4a.
- The edge rule of the noise-floor test has a second condition, a new
  config row `capture.edge_signal_fraction` (0.5): a left or right edge
  pixel is left out only if its signal is also below half the pad
  interior's. On dim layers the rule was unstable: when the row next in was
  darker than the interior only by noise, nearly every edge pixel darker
  than average was left out (green.B on C1048: 42, 0, 0, 0, 39, 36 pixels
  in frames of one exposure; photo_red.B up to all 104). Re-measured on the
  last two rpizero3 archives, no Bus decision changes.
- The strip rule's coverage check (and its "below the floor at Tus and Bus"
  warning) counts only the layers stored at Tus. Layers that are always
  dark and never stored, such as red.B under a red LED, made the warning
  appear for every red-side LED.
- The run screens take their colors from one table, `STYLE_TABLE` in
  `src/palette.py`. A look can be changed there without touching any screen's
  code. `src/pool.py` explains how a widget gets its colors.
  - Amber with black text, in a red frame, means "do this next". Red means
    something is wrong. Pink is an entry that isn't valid.
  - Disabled buttons and boxes are light gray with dark-gray text, and that
    look wins over every other state.
  - A box being typed into is yellow. Its frame is red if the box is also
    due (pending), and medium green otherwise.
  - Row-label buttons (User, Temp, ...) are light blue. Action buttons stay
    green.
- Things marked as the next to do:
  - Continue, once its screen is filled in (Entry 1: user, test and test lot;
    Entry 2: a temperature and a sample weight that read as numbers; test
    variant: every factor valid).
  - Resume, whenever it shows.
  - The pre-image and capture buttons, while a press would start one.
  - A reagent weight that is owed:
    - reagent 1's initial, until reagent 1 starts, and again from reagent 2's
      start if it is missing;
    - reagent 2's initial, once reagent 1's initial is in and good, or once
      reagent 1 has started;
    - the finals, from the start of reagent 2, as before.

    While reagent 1 counts down, reagent 2's initial is the only weight that
    can be marked.
  - Instructions that wait on the operator: load or reposition the cassette,
    weigh or add a reagent, weigh both packets. Stirring lines and the
    imaging line stay plain.
- The pre-image screen's instruction line is split in two.
  - The left box is for the reagent packets: "Weigh R1" until reagent 1's
    initial weight is in and good, then "Weigh R2", then blank.
  - The right box is for the imaging: "Load cassette in meter", "Imaging set
    n of N" over "DO NOT TOUCH" while a set is taken, and "Reposition cassette" between sets.
    Seen from a later step, it reads "Pre-image done".
- The weight grid has a new Packet column, left of Initial. It shows what a
  correctly filled packet should weigh on the balance (target plus the empty
  packet), to two decimals. The other columns are a little narrower to make
  room.
- The entry screens and the test-variant screen show one thing to do at a
  time, in screen order. A row's label button and its box are marked
  together, since pressing the label loads the default.
  - New Test: the ID while it is blank, then Begin. After Begin: the first
    empty identity row, then Continue. Continue on this screen is never held
    back.
  - Entry 2: the temperature, then the sample weight, then Continue.
  - Test variant: the first bad factor box (red), then Continue. Other bad
    boxes are pink until their turn.
  - While another step is being previewed, only Resume is marked.

  The pre-image screen and the steps after it may still mark several things
  at once.
- Once the analysis result is in, PRIOR on the analysis screen opens, so
  every earlier screen can be looked over until NEXT is pressed. Resume is
  then plain rather than marked, since nothing is waiting.
- POST and NEXT first check the run. If anything is left undone, a dialog
  lists it, with Continue (go ahead anyway) and Return. Return, the safe
  choice, is the green button. It counts:
  - empty or bad entry and test-variant boxes;
  - an empty manual color reading;
  - a missing final weight;
  - image sets not all taken.
- The read screen says "Ready to analyze" (it was spelled "analyse").
- Entry 2's Continue now waits for a temperature and a sample weight that
  read as numbers. Without a weight there is no reagent target, so reagent 1's
  START never appeared. A failed probe reading ("err") is marked invalid. On a
  unit with no probe, the Temp button still fills in the configured default.
- A weight that doesn't read as a number, or a packet that is short of
  target, is marked invalid (red while it is still owed). The amber this used
  to be is now kept for "do this next". A bad test-variant factor is red too.
- The weight grid's Over cell is pink instead of amber when the packet holds
  more than the tolerance allows.
- The step chips: running is cyan instead of amber, done is gray, and each
  chip's text color is chosen to be readable on its tint. The startup
  screen's warnings keep their amber.
- The reagent screens' instruction line follows the reaction, and every
  line is marked as the thing to do while it shows:
  - reagent 1 ready: "Start stir - Add R1 - Press Start";
  - reagent 1 running: "Stop stir at beep. Ready R2 and Baro", then after
    the stir beep "Ready cassette in holder, R2, and Baro" (with no stir
    time configured: "Keep stirring. Ready R2 and Baro");
  - reagent 2 ready: "Add R2, Seal cassette, Start";
  - reagent 2 running: "VENTILATE · verify Baro, enter finals".

  The lines are 14 pt so the longest fits. The wording that names Baro is
  marked `# BARO-UDP`, like the other development barometer hooks.
- The reagent button reads "R1 Start" / "R2 Start" (marked as the thing to
  do), and "R1 Running" / "R2 Running", grayed out, while the countdown runs.
- The read screen's instruction is on the same line as the other run
  screens (y 125), and "Manually read color:" with its box moves down a row.
  The prompt reads "Load cassette in meter" ("Load Cassette Holder"
  before), "Reposition cassette" between sets, and "Imaging set n of N" over
  "DO NOT TOUCH" while a set is taken. The manual-reading box is marked
  while it is empty.
- The instruction line's text is centered, so it stays clear of the frame.
- When it counts sets, the pre-image button uses three lines
  ("Pre / image / 1 of 2") in a smaller font, so the count fits.

### Fixed

- A box being typed into keeps the keyboard when the screen redraws on its
  own, as it does when a capture finishes. The redraw used to move keyboard
  focus to the first button while the box still looked as if it were being
  edited. On the pre-image screen that button is View Sample, so the Enter
  meant to finish a reagent weight opened the image viewer (rpizero3,
  2026-11-03). A box that is no longer on screen now has its edit ended.

- The desktop readers handle the schema-5 dark frames. They go by what the
  archive holds rather than by its schema number, so archives in the new
  layout that still say schema 4 read correctly too.
  - `desktop/padanalysis/probe.py`: `Capture.layer()` returned None for every
    layer of a new-layout archive, because finding the dark failed on the
    missing per-LED links. It now takes the dark level from the statistics.
    `darklayer()` returns None there, because the arrays are not kept, and
    `dark_groups()` lists the statistics.
  - `devdesk/flatfield.py` and `devdesk/flatfield_pool.py`: `load_frame()` and
    the pooled loader no longer require a per-LED dark link. The new
    `flatfield.dark_used()` returns the level and where it came from: the link
    name in older archives, as before, or "dark 1000us" for shared statistics.
  - `devdesk/roi_darkdiff.py`: says the darks are statistics only and stops,
    instead of printing blank rows.

  Outputs on C1032 and C1038 are unchanged.

## [0.10.0] — 2026-11-02

Writes: archive schema 5 · meta 2 · database 2
Reads:  archive schema 1–5 · meta 1–2 · database 2

Covers everything since 0.9.0 (2026-08-21 to 2026-11-02): exposure
calibration rebuilt around a test pad, a new Advanced menu of tools, test-variant
runs, a smaller dark-frame protocol, and the desktop analysis chain.

MINOR, on several counts: the archive schema moved to 5; config parameters were
added (`capture.test_image_directory`, `capture.preview_target_90th_dn`,
`protocol.test_variant`, and the `calibration` section), one moved role
(`defaults.exposuregroup` → `capture.exposuregroup`) and one was retired
(`capture.preimage_rung_shift`); tables were added (`treatments`, `test_equip`,
`test_image`, `hotpixels`) and `exposure` gained columns; and the run gained a
screen.

**Archive schema 5.** A sample sweep (and its pre-image) now keeps two dark
frames, at 1000 µs and its longest lit exposure, stored as statistics only, and
no longer writes the per-LED links under `/leds/<color>/dark/`. The dark level
is the same at every exposure (the mean over 2564 dark frames is 64.02–64.04 DN
from 1 to 51 ms), which is why one number per layer is enough.
`capture_h5.dark_level()` reads the new layout. Anything that subtracts a dark
frame pixel by pixel, or follows a per-LED dark link, will not work on archives
in the new layout; see Known gaps. The layout started with commit 6c62fcb
(2026-11-02), so it applies to archives written by that code or later. Any made
before this release was tagged still say schema 4. C1038 (2026-11-01) and every
earlier capture are in the old layout.

### Added

- **Exposure calibration against a test pad** (Menu → Advanced → Calibrate
  Exposures). Each stored exposure is now tested against a calibration window
  on a test pad (white, yellow and brown patches) and the gray chips, instead
  of being a fixed multiple of one exposure. Seven nested rungs (log positions
  0, 1/4, 1/3, 1/2, 2/3, 3/4, 1) serve exposure spreads 1–5. The screen has the
  LED bar, the window settings (global or per LED), a column choosing which
  Bayer layers must pass, a Spacing box (`calibration.min_spacing_ratio`, seeded
  1.02), Pad areas (places the test pad's three patches in the image viewer),
  and Show (plots the pad's sorted signal against the window). When no exposure
  suits every patch, the rungs are spread from the white-limited exposure to the
  brown-limited one, and the remark ends in "spread". The screen says why an LED
  did not fit.
- White is calibrated for one exposure only, and that calibration also measures
  the photo's colour gains from the gray chips, saved as
  `capture.photo_wb_gains`. If the chips are out of range at that exposure, the
  gains are measured again at a rescaled exposure (up to three frames).
- **The Advanced menu** (the Menu's old Capture button), with:
  - **Test LED**: tap a color to light it.
  - **Capture Image**: one LED at chosen rungs, saving the ticked image types
    (JPEG and Bayer, whole frame or bound crop, dark frames) as a test image.
  - **Capture Suite**: a sample's whole measurement sweep on whatever is in the
    holder, saved as a test image. Only Name and Desc are typed.
  - **Wifi**: connection status, IP, MAC and hostname; connect to or forget a
    network, or join one from a USB keyboard.
  - **Calibrate Touchpad**: four corner taps, a 30-second trial, and SAVE.

  Test images go in `capture.test_image_directory` as `<Name>.h5`, written as
  `.h5.part` and renamed once complete, with a row in the new `test_image`
  table. A failed or abandoned capture leaves no file and no row.
- **Test-variant runs.** With `protocol.test_variant = 1`, a screen after Entry
  2 sets the equipment (from the `test_equip` list), the number of image sets
  (1–5), and weight and time factors for each reagent. They are recorded in the
  new `treatments` table and applied: reagent targets and countdowns are scaled,
  and with n image sets both the pre-image and the measurement run n times, set
  k ≥ 2 in its own `{samp_no}rep{k}.h5`. With `test_variant = 0` a run is
  unchanged.
- A red **Start / Vent / Hood** screen after Entry 2, before the pre-image or
  reagent 1. OK is the only way on.
- The image viewer's preview uses the white LED's colour gains, and meters its
  exposure on the 90th percentile of the brightest Bayer layer against
  `capture.preview_target_90th_dn` (seeded 400 DN), so a two-tone card is
  exposed for its lighter half.
- A narrow LED steps down to the next smaller exposure spread it has (3 → 2 →
  1) rather than falling back to a single default exposure. Each LED's metadata
  records the spread used and the spread asked for.
- The progress line reads "Capturing <color> n of N", counted within each LED.
- Pressure-logger signalling over UDP at run milestones (`baro_signal.py`). It
  is off unless `config/baro_udp.json` and `config/secrets_net.py` both exist.
- The `hotpixels` table, holding hot pixels found in the dark frames.
- `devdesk/hdf5inspect.py`: the summary is a full inventory, one row per LED
  and stored exposure.

### Changed

- The pre-image shoots the exposures flagged in the `exposure` table's new
  `preimage` column (the middle of the usable range), falling back to the
  middle of spread 1. The per-LED rung shift and its ladder arithmetic are gone.
- White is photographed once, last, at its spread-1 exposure, after the dark
  frames. The same frame is both the white measurement and the colour photo.
- Exposures are kept in whole microseconds. The shared ×1.09 grid that let LEDs
  share a dark frame is gone; it made close rungs collide, so a narrow pad lost
  spreads 4 and 5.
- `exposuregroup` (the exposure spread, 1–5) moved from role `defaults` to
  role `capture`, so the config editor lists it with the other capture
  settings. The seed file carries an existing value and its history across at
  the next launch.
- Entry 2's Continue saves the sample temperature as
  `defaults.default_temperature`, so the next run starts from it.
- Touchpad calibration files are saved safely: the old file is backed up,
  the new one written to a temporary file and swapped in, then read back.

### Removed

- `capture.preimage_rung_shift`. Its reader went with the pre-image change
  above, but the row stayed in databases seeded before then. The seed file now
  deletes it.

### Fixed

- Sample runs read the saved exposure spread every time. After a restart they
  used to take spread 1 whatever was saved (C1037 and C1038 hold one exposure
  per LED with 3 saved).
- A manual reading typed ahead on the Read screen survives redraws and reaches
  `samples.manual_read`.
- The Calibrate Exposures page crashed on the device: its swatches used the
  image-space Rect, which the real display driver cannot draw.
- The launch report no longer calls a moved parameter "reseeded", and its count
  of added rows no longer goes negative when a retired row is deleted.
- The arbiter self-test runs against a copy of the database from the start
  (it used to write to the live one), and runs on the mock drivers without
  setup.

### Desktop analysis (`desktop/padanalysis/`)

Not part of the instrument, but recorded here because it moved a lot:

- `analysis_1.py`, the hand-run pipeline for one strip: locate the pad in both
  sweeps, read a cell through the photometric chain, and separate the two dyes.
- Pre-processing: the dark correction is one level per Bayer layer per sweep,
  the gray-chip divisor is per pixel, and hot pixels are masked, not repaired.
- R_inf's white reference is the strip's own dry pad in the pre-image. Bare
  paper is no longer a reference anywhere; `duotone.PadCube` follows.
- Studies and write-ups: R_inf guide, dark-signal characterization, dot study,
  PCA study and shading study. Edge-correlation pad registration was tried and
  shelved; the arithmetic registration stays.

### Known gaps

- `desktop/padanalysis/probe.py`, `devdesk/flatfield.py` and
  `devdesk/roi_darkdiff.py` still look for per-LED dark links or full dark
  arrays, so they cannot read archives in the new layout. Fixed after this
  release; see Unreleased.

## [0.9.0] — 2026-08-21

Writes: archive schema 4 · meta 2 · database 2
Reads:  archive schema 1–4 · meta 1–2 · database 2

Reworks the reagent weight grid. Each packet now has its own initial/final
fields (`reagent1_initial_wt`/`reagent1_final_wt`, and the same for reagent 2)
instead of one field that retargeted itself between the two weighings, so
which box a keystroke lands in is fixed by which box was typed into and never
by run state. `Arbiter.reagent_weigh_plan()` returns a `ReagentWeights` record
in which the Net cell answers "what the packet holds" before the pour and
"what was actually poured" after, Target no longer waits on a valid initial
weight, and Over (`net - target`) is computable throughout instead of only
once both weighings are in. The reagent1_g/reagent2_g sign convention is now
`initial - final` (was `final - initial`) to match. `reagent_finals_complete()`
gates analysis on both final weighings actually being entered.

The entry2 screen's Weight button now fills in a configured nominal sample
weight in one press, the same way the Temp button already offers a stand-in
reading — the reagent targets that button feeds scale from whatever ends up
in that field.

MINOR: two config parameters were added — `protocol.reagent_wt_tolerance`
(the tolerance band was previously a hardcoded constant) and
`defaults.default_sample_wt`.

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
