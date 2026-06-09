# Colorimeter — Development Roadmap

Big-picture ordered plan. Steps are intentionally coarse — each will be
broken into smaller tasks when it becomes current. New decisions will shift
priorities; decisions that must be made before a step can proceed are
called out explicitly.

Effort key: **S** = hours · **M** = 1–2 days · **L** = 3–5 days · **XL** = 1–2 weeks

---

## Pre-Step: Phase scheduling design  ✅ Done

The phase scheduling, overdue state machine, and overdue widget design are
documented in [PHASE_SCHEDULING_DESIGN.md](PHASE_SCHEDULING_DESIGN.md)
before any YAML or screen work begins — because the YAML phase schema must
encode `modal`, `duration_seconds`, `alert_repeat_seconds`, and
`target_screen` per phase, and screen YAML must carry a `focus_window`
attribute.

Key decisions resolved:
- DB `status` enum (`pending/active/overdue/complete/skipped`) is ground truth
- Overdue deque is a derived in-memory view, rebuilt from DB on boot
- Priority = oldest missed deadline (no explicit priority field needed)
- Footer widget: PRIORITY (left) + count-up timer + label (center) + NEXT (right)
- Skip allowed; flags result `degraded`; button lives on target screen not footer
- Open questions: OQ-S1 through OQ-S7 in PHASE_SCHEDULING_DESIGN.md

---

## Step 1 — Config & YAML foundation  `L`

**Decision required:** Choose the YAML screen/widget definition format before building any more screens. This decision shapes everything downstream (screen construction, Flask parity, localization).

- Define YAML schema for screens, widgets, navigation, **and phase definitions**
  (must include: `modal`, `duration_seconds`, `alert_repeat_seconds`, `target_screen`,
  `focus_window` — see PHASE_SCHEDULING_DESIGN.md §10)
- Write `config_loader.py`: load YAML configs; make display dimensions a config value (not hardcoded)
- Write `screen_builder.py`: instantiate `Screen`/`Area`/widget trees from YAML
- Migrate `proto_screen1.py` and `proto_screen.py` to YAML-driven construction as validation
- Add `simulation_mode` config flag that substitutes mock hardware modules at startup

**Why first:** Every subsequent screen is defined in YAML. Doing this now avoids
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

## Step 3 — Multiple test slots and navigation model  `L`

The slot system (A/B/C/D) is the spine of the entire UX.

- Define `TestSlot` object: slot letter, color, current screen state, data record
- Header widget shows active slot letter/color; slot-toggle button cycles active slots
- `AppController` holds up to 4 slots; each maintains independent screen state
- "New Test" button on opening menu creates a new slot and navigates to sample entry
- Slot color coding applied consistently (Blue/Amber/Green/Coral)

**Decision required:** Hard cap at 2 simultaneous tests for now (spec OQ-6 recommendation)?
This simplifies scheduling significantly and matches practical operator capacity.

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

## Step 10 — Phase scheduling and resource locking  `L`

Required before two simultaneous tests can run safely.

- Resource lock manager: camera, LED array, display body area
- Cooperative scheduler: check projected focus-window conflicts before activating Start
- Timer interleaving: ensure focus windows of concurrent tests don't overlap
- Focus windows defined in screen YAML (per spec Section 3)

**Decision required:** This is architecturally non-trivial. Cooperative scheduling is
sufficient (spec says timing precision is loose), but the locking mechanism needs a
clear design before Step 10 begins. Consider whether a simple mutex per shared resource
covers all cases.

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
| D-1 | YAML screen/widget schema design | Before Step 1 | High — shapes everything |
| D-2 | Hard cap at 2 simultaneous tests | Before Step 3 | Low — recommendation is yes |
| D-3 | Phase sequence and count per test type | Before Step 4 | Medium — scientific/UX |
| D-4 | SQLite WAL mode suitability | Before Step 5 | Low — spike on hardware |
| D-5 | Calibration data format and update path | Before Step 8 | Medium — scientific |
| D-6 | How much analysis on-device vs. desktop | Before Step 8 | High — affects all of Step 8 |
| D-7 | Resource lock / scheduler design | Before Step 10 | High — concurrent tests |
| D-8 | pypubsub vs. custom event bus | Before Step 11 | Low |
| D-9 | CSS framework for web UI | Before Step 12 | Low |
| D-10 | Flask auth method (OQ-1) | Before Step 12 | Low |
| D-11 | Web interface v1 scope | Before Step 12 | Medium |
| D-12 | Localization format — gettext vs. YAML (OQ-3) | Before Step 16 | Low |
| D-13 | RTL language support — now or defer (OQ-8) | Before Step 16 | Medium if now |
