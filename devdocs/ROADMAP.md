# Colorimeter — Development Roadmap

Big-picture ordered plan. Steps are intentionally coarse — each will be
broken into smaller tasks when it becomes current. New decisions will shift
priorities; decisions that must be made before a step can proceed are
called out explicitly.

Effort key: **S** = hours · **M** = 1–2 days · **L** = 3–5 days · **XL** = 1–2 weeks

---

## Pre-Step: Interface and program flow redesign  ✅ Done

The interface simplification and scheduling model are documented in
[PHASE_SCHEDULING_DESIGN.md](PHASE_SCHEDULING_DESIGN.md) and
[TERMINOLOGY.md](TERMINOLOGY.md).

Key decisions resolved:
- **One test at a time**: a second test cannot start until the active test completes;
  no concurrent scheduling needed
- **Period layer eliminated**: each Screen is a Phase — one duration, one modal flag;
  no sub-period structure
- **`is_alive` model**: screens with non-zero duration are monitored; alarm fires on
  timeout; modal screens lock navigation until user acts
- **`active_slot` flag** (not a scheduler): single check; Start disabled while
  `active_slot` is set
- **Widget singletons** with hardcoded 3-digit ids; layout from CSV, wiring in Python
- DB status enum: `pending/active/complete/skipped` + `late_flag` (no `overdue` state)
- Skip allowed; flags result `degraded`
- Screen and widget format: CSV files (see [YAML_FORMAT_PROPOSAL.md](YAML_FORMAT_PROPOSAL.md))

---

## Step 1 — Config & CSV foundation  `M`

Format is decided: CSV for screens and widgets, YAML for hardware config and palette.
See [YAML_FORMAT_PROPOSAL.md](YAML_FORMAT_PROPOSAL.md) for full schemas.

- Write `config_loader.py`: load `instrument.yaml`, `palette.yaml`; expose display
  dimensions and hardware config; add `simulation_mode` flag
- Write `csv_loader.py`: parse `widget_defs.csv`, `screen_defs.csv`,
  `screen_widgets.csv`; construct Widget and Screen objects
- Migrate `proto_screen1.py` and `proto_screen.py` to CSV-driven construction as validation
- Update `AppController._wire()`: assign callbacks and data bindings by widget_id

**Why first:** Every subsequent screen is defined in CSV. Doing this now avoids
rewriting a dozen hand-coded screens later.

---

## Step 2 — Complete the widget library  `M`

Remaining widgets needed for the production screens:

- **Timer widget** — countdown / countup; configurable warning color thresholds; audible alert callback
- **Scrolling selector** — 8–16 entries from config; background changes with selection
- **Numeric keypad overlay** — modal 12-key overlay (0–9, `.`, backspace); feeds a target TextBox
- **Image viewer** — renders a pre-downsampled pixel array into a Rect
- **Progress bar** — animated fill variant of RectWidget (for phase progress)

**Decision required:** Numpad overlay confirmed modal (spec OQ-5 says yes). Define the overlay push/pop API in `ScreenManager` (stub exists: `push_modal` / `pop_modal`).

---

## Step 3 — Slot model and navigation  `M`

Two slots (A and B). Only one test runs at a time — the `active_slot` flag
prevents a second Start until the first test completes.

- Define `TestSlot` object: slot letter, color, data record
- `AppController.active_slot`: `None` | `'A'` | `'B'`; Start button disabled when set
- Header widget shows active slot letter/color; slot-toggle button switches
  view between A and B (viewing a slot ≠ running a slot)
- "New Test" button on opening menu navigates to sample entry for selected slot
- Slot color coding: A=Blue, B=Amber

---

## Step 4 — All touchscreen screens (skeleton)  `XL`

Build YAML definitions and connect navigation for all ~15 screens.
No hardware behavior yet — just layout and navigation flow.

| # | Screen |
|---|---|
| 1 | Splash / boot |
| 2 | Opening menu (new test, resume, diagnostics) |
| 3 | New test — sample entry *(already exists as proto_screen1)* |
| 4 | Temperature / sensor check |
| 5 | Phase N — reagent timer |
| 6 | Phase N — capture in progress |
| 7 | Phase N — capture review |
| 8 | Results summary |
| 9 | Results detail |
| 10 | Results image viewer |
| 11 | Past results browser |
| 12 | System status / diagnostics |
| 13 | Error / warning overlay |

**Decision required:** Define the phase sequence and how many reagent phases exist
per test type. This determines how many "Phase N" screen instances are needed and
whether they are templated from a single YAML definition.

---

## Step 5 — SQLite schema and test record  `M`

- Design the full test record schema (see spec Section 4.1)
- Implement incremental writes: each phase completion commits immediately
- Operational log table (separate from test records)
- `db_manager.py` extended with typed write helpers for test records
- Boot-time check for incomplete tests → "Resume Tests" menu option

**Decision required:** Confirm SQLite WAL mode is appropriate (spec Section 4.3 flags this
as needing study). Worth a short spike on the Pi Zero W under concurrent read/write load.

---

## Step 6 — DS18B20 temperature sensors  `S`

- `temp_sensor.py`: enumerate sensors on 1-wire bus, read by GUID
- Mock implementation returning synthetic values
- Wire into AppController: read at test start and at each phase transition
- Record sensor GUIDs and readings in test record

Low effort, low risk — depends on Step 5 (record storage).

---

## Step 7 — LED sequencing and camera capture pipeline  `M`

- `capture_pipeline.py`: for each LED color — turn on LED, wait settle time, capture frame(s), turn off LED
- Exposure and settle time from config
- Captured frames (rgb + bayer) passed to analysis or saved directly
- Wire into Phase capture screen (#6)

---

## Step 8 — Image analysis pipeline  `XL`

This is the core scientific work. Much prototype code already exists in `dev/`.

- Finalize ROI definition, storage, and retrieval (`roi.py`)
- Bayer layer extraction per LED color
- Black subtraction, white balance, areal luminance trend correction
- Per-channel statistical description (mean, SD, etc.)
- Apply calibration coefficients → result value + error measures
- Flag out-of-range results
- Write results to test record

**Decision required:** Calibration data format and storage (coefficients in YAML config?
SQLite? HDF5?). This determines how calibration is updated in the field.

**Decision required:** How much analysis runs on-device (Pi Zero W is memory-/CPU-constrained)
vs. deferred to desktop post-processing? Defines minimum viable result for on-device display.

---

## Step 9 — HDF5 archival storage  `M`

- One HDF5 file per test: images, Bayer arrays, metadata, results
- `hdf5_writer.py`: write/read HDF5 using h5py
- Config policy: embed images in HDF5 vs. reference by path
- Link HDF5 file path into SQLite test record

---

## Step 10 — Alive-screen monitor and alarm system  `S`

The concurrent scheduler is replaced by the simpler `is_alive` model.

- `ScreenManager.alive_screens`: list of Screen objects with running timers
- Each iteration of the main loop: check elapsed time against `screen.duration`
- On timeout: fire `screen.timeout()` — triggers alarm and takes focus
- `Beeper.alarm_on()` / `Beeper.alarm_off()` wired to screen begin-actions
- `late_flag` set in DB when a modal screen times out before user completes it

---

## Step 11 — Pub/sub shared state bus  `M`

Currently `AppController` owns all state directly. For Flask parity and future resilience:

- Integrate pypubsub (or a thin custom equivalent)
- Transient state (active screen, timer values) published as events
- Durable state always written to SQLite first, then published
- `TextBox.on_update` hook already wired for subscriber auto-updates

**Decision required:** pypubsub vs. a simpler custom event dict. pypubsub adds a
dependency; a custom solution may be sufficient given the scope.

---

## Step 12 — Flask web interface  `XL`

- Flask app in background thread; Waitress for stability
- Routes mirror screen inventory from Step 4
- Jinja2 templates generated from same YAML screen definitions (structural parity)
- CSS framework choice (spec OQ-4: Bootstrap vs. Pico CSS — prefer offline-capable)
- Shared state via pub/sub bus from Step 11
- Authentication (spec OQ-1: HTTP Basic Auth over LAN recommended as simplest)
- WCAG 2.1 AA accessibility targets

**Decision required:** CSS framework. Bootstrap is familiar but heavy; Pico CSS is ~10 kB
and requires no build step — good fit for a LAN-only instrument UI.

**Decision required:** Web interface is primary path for metadata entry, config editing,
and results review per spec. Define which features must be in v1 vs. deferred.

---

## Step 13 — Data upload and desktop receiver  `M`

- `uploader.py`: package HDF5 per session, POST to configured receiver address
- Retry with backoff; log E-NET-02 on failure; results remain local
- `desktop/colorimeter_receiver.py`: Flask server, port 51051, accepts HDF5
- Unpacks HDF5, upserts records into desktop SQLite, archives file
- Security: validate HDF5 structure before unpacking; reject unexpected keys

---

## Step 14 — Error handling and warning system  `M`

- `error_handler.py` (stub exists): publish errors on state bus
- Persistent warnings in status bar
- Critical error overlay (spec Section 9 error table)
- Escalating beeper cadence for timer overrun (E-TIMER-01)
- E-CAM, E-SENS, E-TEMP, E-NET codes all wired

---

## Step 15 — Boot sequence and power-loss recovery  `S`

- Splash screen during hardware self-check
- Enumerate sensors, ping camera, LED test
- Check SQLite for incomplete tests → "Resume Tests" option
- Flask starts in background thread after display is ready
- Graceful shutdown handler (already partial in AppController)

---

## Step 16 — Localization  `M`

**Decision required:** YAML resource files vs. GNU gettext `.po` (spec OQ-3).
gettext has better tooling and is the standard; YAML is simpler to edit manually.
Recommend gettext for long-term maintainability.

- Externalize all user-visible strings to locale files
- Locale loaded from config; date format and decimal separator configurable
- RTL support: design decision (spec OQ-8) — recommend deferring RTL rendering
  but keeping string externalization RTL-compatible from the start

---

## Step 17 — Hardening and deployment  `M`

- Simulation mode fully functional (all mocks substitutable via config flag)
- Version recorded in operational log at boot
- systemd service file for autostart
- `sync_to_pi.sh` / `sync_from_pi.sh` update scripts
- Performance profiling on Pi Zero W (memory budget, GIL hold times)
- Test suite covering widget library, screen manager, analysis pipeline

---

## Key decisions summary

| # | Decision | When needed | Complexity |
|---|---|---|---|
| D-1 | Screen/widget format (CSV) | ✅ Resolved | — |
| D-2 | One test at a time; two slots (A/B) | ✅ Resolved | — |
| D-3 | Phase sequence and count per test type | Before Step 4 | Medium — scientific/UX |
| D-4 | SQLite WAL mode suitability | Before Step 5 | Low — spike on hardware |
| D-5 | Calibration data format and update path | Before Step 8 | Medium — scientific |
| D-6 | How much analysis on-device vs. desktop | Before Step 8 | High — affects all of Step 8 |
| D-7 | Alive-screen timeout behavior (alarm only vs. auto-advance) | Before Step 10 | Low |
| D-8 | pypubsub vs. custom event bus | Before Step 11 | Low |
| D-9 | CSS framework for web UI | Before Step 12 | Low |
| D-10 | Flask auth method (OQ-1) | Before Step 12 | Low |
| D-11 | Web interface v1 scope | Before Step 12 | Medium |
| D-12 | Localization format — gettext vs. YAML (OQ-3) | Before Step 16 | Low |
| D-13 | RTL language support — now or defer (OQ-8) | Before Step 16 | Medium if now |
