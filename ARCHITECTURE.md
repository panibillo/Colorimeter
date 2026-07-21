# Architecture

> Scope: how the running system is structured **today**. The UI's phase/slot
> model has a deep-dive in [dev/phase_model_design.md](dev/phase_model_design.md).
> Anything not yet built is confined to
> **[Planned / not yet built](#planned--not-yet-built)** — treat everything
> outside that section as code that exists.

## Entry points and platform selection

Two launchers, each of which builds and runs one `Arbiter`:

| Launcher | Use | Hardware |
|---|---|---|
| `main.py` | On the Raspberry Pi | real drivers |
| `run_simulation.py` | Desktop dev | mock drivers (sets `COLORIMETER_SIMULATION=1`) |

Platform is chosen by the **`COLORIMETER_SIMULATION`** environment variable, read
once as `arbiter._SIMULATION_MODE`. `Arbiter._init_hardware()` imports drivers
**lazily**, so the modules import cleanly on any machine:

- Real: `ili9341`, `tsc2007`, `kb_reader`, `beeper`, `gpiozero`
- Mock (`src/mock/`): `ili9341_mock`, `tsc2007_mock`, `kb_reader_mock`,
  `beeper_mock`, `gpiozero_mock`, `picamera2_mock`, `spidev_mock`

The mock display is a matplotlib window: mouse = touch, key presses = keyboard.

## Initialization sequence

1. The launcher installs signal handlers and calls `Arbiter().start()`.
2. `start()` → `_init_logging()` → `_init_hardware()` (lazy driver import;
   display/touch/keyboard init) → `_build_model()` → `focus(slot A)` → `_run()`.
3. `_build_model()` builds the shared `Pool`, the phase registry
   (`build_phases()`), and slots A and B.

## Main event loop

`Arbiter._run()` is a **single-threaded cooperative loop** (no threads). Each
pass:

- pump the display's event queue (simulation only);
- `poll_timers()` — expire check for both slots (arithmetic on monotonic
  deadlines); on expiry, alarm and advance the slot's live phase;
- `pump_capture()` — advance the in-flight capture one `step()`;
- `pump_analysis()` — complete analysis when ready (placeholder today);
- read one keyboard event; dispatch a touch if the panel is pressed;
- once per second, repaint the clock and the running timer.

Everything is interleaved on one core, so responsiveness is bounded by the
longest single step — a real concern on a single-core Pi Zero W (no NEON) versus
a quad-core Zero 2 W. See WEB_INTERFACE.md and the capture review for detail.

## The UI: phase / slot model

The screen is driven by four roles — full rationale in
[dev/phase_model_design.md](dev/phase_model_design.md):

| role | file(s) | holds |
|---|---|---|
| **Pool** — the one view | `pool.py`, `layout.py`, `palette.py` | one widget per named rect for the 320×240 screen |
| **Phase** — behaviour | `phases.py` | stateless `enter`/`activate`/`on_expire`; entry, two reagents, read, analysis, modals |
| **Slot** — state | `slot.py` | record, viewed vs live phase, timer, run/capture flags (A and B) |
| **Arbiter** — coordination | `arbiter.py` | focus, single-camera guard, buzzer, timers, keyboard/focus, main loop |

Supporting: `timer.py` (`TimerState` + `TimerWidget`), `widgets.py` (Label,
Button, TextBox, RectWidget, …), `geometry.py` + `rect.py` (Rect math).
Navigation is preview/Resume — PRIOR/NEXT preview neighbouring phases without
advancing; `active_phase` (live) is distinct from `phase` (viewed).

**Legacy, being retired:** `screen_manager.py`, `csv_loader.py`,
`csv_to_widgets.py`, `app_controller.py`, and the `*.csv` screen definitions —
the old screen-centric UI, superseded by the phase/slot model.

## Capture pipeline

Raw-Bayer, multi-exposure, LED-sweep capture, streamed to one HDF5 archive:

- `bayer_cam.py` — `BayerCam`, the camera abstraction (IMX219 / IMX708). Loads
  `mock.picamera2_mock` on the desktop. Unpacks packed 10-bit raw to uint16 and
  crops ROIs.
- `lighting_cal8.py` — `LEDBank`, the shift-register LED control used by the
  capture pipeline. (A separate GPIO-pin `LedController` in
  `dev/lighting_lightboard1.py` serves the web prototype — see the doc note in
  WEB_INTERFACE.md.)
- `capture_runner.py` — the suite workflow as a **generator**: white-LED
  exposure calibration, focus, then per-LED exposure bracketing (7 exposures,
  lit + dark). Yields `Progress` between camera ops so the caller stays
  responsive; waits yield every 50 ms.
- `capture_session.py` — wraps the generator so a UI can `step()` it once per
  loop pass and inspect `active` / `error`.
- `capture_h5.py` + `hdf5write.py` / `hdf5read.py` — the HDF5 suite archive (one
  file per suite). `roi.py` / `rect.py` describe target regions.

The arbiter owns at most one `CaptureSession` at a time (single camera).

## Support modules

- `config_manager.py` — configuration load/access.
- `logging_manager.py` — unified logging, used across modules.
- `error_handler.py` — exception handling and cleanup.
- `db_manager.py` — thin SQL wrapper (`read_all`, `read_one`, `read_val`,
  `write_one`, `write_many`); connections are request-scoped, not pooled.
- `utils.py` — shared helpers (no hardware or DB access).
- Hardware drivers: `ili9341.py` (display, SPI), `tsc2007.py` (touch, I²C),
  `kb_reader.py` (keyboard), `beeper.py` (buzzer).

## Database lifecycle

Request-scoped; no persistent connection. On a task (save a reading, fetch a
calibration): open a connection, execute in a transaction, commit and close
(rollback on error). No pooling. `db_manager.py` provides the per-call read/write
wrappers.

## Planned / not yet built

The measurement-science and reporting layers are designed but not implemented;
the arbiter's analysis step is a ~1.5 s placeholder today. None of these modules
exist yet — they are the roadmap, not the current tree:

- **Analysis** — colour analysis in a target ROI; calibration-card patch reading;
  ROI auto-detection.
- **Colour → concentration map** — calibration curves per test-strip type.
- **Report** — format and persist results (timestamp, image refs, calibration
  used).
- **Calibration workflow** — orchestrate calibration captures + analysis.
- **Web interface** — a Flask frontend. The phase/slot model is designed to make
  this cheap: swap `layout.py` + the pool builder, keep phases and slots. A
  separate hardware-exercise prototype already exists at `dev/webtest.py` — see
  [WEB_INTERFACE.md](WEB_INTERFACE.md).
