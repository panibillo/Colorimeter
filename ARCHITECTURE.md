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
once as `arbiter._SIMULATION_MODE`. `Arbiter._init_drivers()` imports drivers
**lazily**, so the modules import cleanly on any machine:

- Real: `ili9341`, `tsc2007`, `kb_reader`, `beeper`, `gpiozero`, `DS18B20`
- Mock (`src/mock/`): `ili9341_mock`, `tsc2007_mock`, `kb_reader_mock`,
  `beeper_mock`, `gpiozero_mock`, `picamera2_mock`, `spidev_mock`,
  `DS18B20_mock`

The mock display is a matplotlib window: mouse = touch, key presses = keyboard.

## Initialization sequence

The boot is in three acts, because the splash screen cannot narrate its own
preconditions — the database and the display have to be up before anything can
be shown at all.

1. The launcher installs signal handlers, times `db_bootstrap.ensure()` into a
   `splash_screen.BootLog`, and calls `Arbiter().start(boot=boot)`.
2. `start()` → `_init_logging()` → `_init_drivers()` (lazy driver import,
   backlight on, signal handlers) → `_init_display()`. Silent so far.
3. The `Splash` opens and `replay()`s the BootLog, so the two steps that
   happened before it existed appear as already done. From here every step is
   narrated: `_init_peripherals()` (touch, keyboard, temperature),
   `_build_model()` (the shared `Pool`, `build_phases()`, slots A and B), and
   finally `startup_checks.run_all()`.
4. Anything the seed file had to load from a default gets its own
   `Loading {role}.{name} = {value} (rowid {n})` dialog and its own OK press.
5. `splash.problems()` then **replaces** the step list with the warnings and
   failures the checks found, one message each. Every step that ran, ran; what
   the operator can act on is the exception, and only one of the two lists
   fits on a 320×240 panel. Nothing is drawn when nothing went wrong.
6. `splash.finish()` shows `Ready` (or the first failure, with START greyed
   out) and waits. MENU → `open_menu(slot A)`, START → `focus(slot A)`; either
   way the operator ends up on the Begin screen. Then `_run()`.

A step is marked from **what it found**, not from whether it returned:
`KbReader.open()` answers False when no keyboard is on the bus and
`DS18B20.init()` finds no probe just as quietly, so both would otherwise
report `ok` while saying the opposite of what the operator needs.

`_init_hardware()` remains as the unnarrated equivalent of steps 2-3, for
callers with no splash to report to (the self-test at the foot of `arbiter.py`).

Severity rule for the checks: **a check may only FAIL if the operator can fix
it from the Menu.** A missing config parameter or image region may stop a boot
(Menu → Config, Menu → View Image); a missing flat-field model, exposure ladder
or touch calibration may only warn, because each needs a desktop tool. So do
the bus device nodes and the hardware group memberships, which need
`/boot/firmware/config.txt` and `usermod`.

### Image regions

Which ROI roles a run cannot proceed without is **commissioned data, not a
constant**: `config` holds `roi.required_roles` (`editable=0`), and
`roi.required_roles()` reads it, so an instrument built around a different card
is given a different requirement without a code change. Both the boot check
and the capture path read that one list.

The rules applied to those roles live in `roi.check_requirements()`, with the
multiplicities in `roi.MULTIPLICITY`:

| Role | In force | Over-filled |
|---|---|---|
| `bound` | exactly one — it is *the* crop the archive stores | warning |
| `sample` | one is the design | warning, naming the one that wins |
| `white_balance` | one or more — this card balances on a pair of gray chips | fine |

A required role with nothing in force is restored from its commissioned
default — every row of it, since a role may be filled by more than one region
— and each restoration earns its own OK press. A role with no default to fall
back on is the only ROI failure. A required region hanging outside `bound` is a
warning: what is lost is the archive, not the reading. **Regions in any other
role are not checked at all**, so a card may carry as many reference patches as
its operator finds useful.

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

## Shutdown

`Arbiter.shutdown(error=None)` (`arbiter.py`) is the one shutdown entry point —
reached from the SIGINT/SIGTERM handler installed in `_init_drivers()`, and
from the launchers' top-level `except` clauses (a fallback for anything that
happens before that handler is installed, or an unhandled exception inside the
loop). Ctrl-Z (SIGTSTP) is unrelated — `main.py` lets it suspend the process
normally so `fg` resumes it.

1. Show `shutdown_screen.run()` — a small self-contained screen (independent
   of `pool.py`/`layout.py`, so it renders even if the phase/slot model never
   finished building) offering **Re-launch / Exit / Power off**. Touch, Tab,
   and Enter all work; Exit is focused by default and is also what a 15 s
   timeout falls back to if no one answers. On an error shutdown the screen
   shows the exception message instead of "Shutting down".
2. `Arbiter.cleanup()` — release every hardware resource (capture, kb, beeper,
   touch, display, backlight). Idempotent, and each release is wrapped
   independently so one device failing to close can't strand the rest.
3. Act on the choice: **Exit** → `sys.exit(0)`. **Re-launch** →
   `os.execv(sys.executable, sys.argv)`, replacing the process image (hardware
   must already be released at this point, or the new process would inherit
   half-open SPI/GPIO state). **Power off** → `sudo shutdown -h now`; a no-op
   log line in simulation, never touching the host machine.

A second SIGINT/SIGTERM while shutdown is already in progress skips straight
to `os._exit(1)` rather than re-entering `shutdown()` or `cleanup()` — a
safety valve so a stuck shutdown can't make the process unkillable.

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

- `config_repo.py` — every tunable instrument parameter, as rows in the
  database's `config` table (role/name/vtype/value, versioned like `exposure`
  and `roi`). Replaced `config_manager.py` + `config/instrument.yaml` in 2026-08.
- `logging_manager.py` — unified logging, used across modules.
- `error_handler.py` — exception handling and cleanup.
- `db_manager.py` — thin SQL wrapper (`read_all`, `read_one`, `read_val`,
  `write_one`, `write_many`); connections are request-scoped, not pooled. Also
  resolves the database path itself (`db_path()` / `$COLORIMETER_DB`), since
  `config_repo` reads its parameters out of the database and cannot be the one
  to say where it lives.
- `splash_screen.py` — the boot splash, and the `BootLog` the launchers use to
  record the steps that ran before there was a display. Deliberately
  pool-free/layout-free, like `shutdown_screen.py`: it has to draw while the
  `Pool` it is narrating the construction of does not exist yet.
- `startup_checks.py` — the boot-time "is this instrument commissioned enough
  to run?" aggregator, and the in-app twin of
  `provisioning/deploy/commissioning_status.sh`. Defines no threshold, path or
  required-name list of its own — the required ROI roles come from the config
  table, the schema's tables are read back out of `db_DDL.sql` — and imports
  each subsystem inside the check that needs it so `arbiter`'s header does not
  pull in numpy or picamera2. Its two exceptions, `BUS_NODES` and
  `HARDWARE_GROUPS`, are facts about the Pi that no module of ours owns; their
  other copy is in `commissioning_status.sh`, and the two are kept in step.
- `utils.py` — shared helpers (no hardware or DB access).
- Hardware drivers: `ili9341.py` (display, SPI), `tsc2007.py` (touch, I²C),
  `kb_reader.py` (keyboard), `beeper.py` (buzzer), `DS18B20.py` (temperature,
  1-Wire on GPIO16).

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
