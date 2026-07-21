# Phase / Slot model — design

Supersedes the screen-centric model (`screen_manager.py`, `csv_loader.py`,
`csv_to_widgets.py`, `screen_defs.csv`, `screen_widgets.csv`, `widget_defs.csv`)
and the abandoned CSV-step exploration under `src/proposed/`. The new model now
drives the app — `main.py` and `run_simulation.py` launch `Arbiter().start()`.
The old modules still exist on this branch, unused, pending retirement.

## The problem with screens

A screen owned its widgets, so a widget belonged to exactly one screen. But
across a real test protocol the same rect is *the timer* in three phases, *the
action button* is START/RESET/CAPTURE/POST across five, and most phases change
only a few widgets. Copying widget definitions per screen (what
`csv_loader.wire_widgets_to_screens()` does) makes those separate objects with
no shared runtime state — a paused timer could not survive a phase change.

## The spine: one view, stateless phases, stateful slots

Three roles, each with one job. No `Screen`, no `ScreenManager`, no
`PhasedWidget`, no per-slot widgets, no CSV.

| role | count | holds | file |
|---|---|---|---|
| **Pool** — the view | 1 | one widget instance per rect, keyed by short name | `pool.py` + `layout.py` |
| **Phase** — behaviour | one per pattern | stateless: `enter(pool, slot, arb)`, `activate(arb, slot)`, `on_expire(arb, slot)` | `phases.py` |
| **Slot** — state | 2 (A, B) | record, viewed + live phase ids, `running`/`capturing` flags, `TimerState` | `slot.py` |
| **Arbiter** — coordination | 1 | both slots, focus, the single camera + buzzer, main loop | `arbiter.py` |

There is **one physical screen**, so there is **one widget pool** (the view).
A and B differ only in *data*, not in widgets — so the slot holds data and the
pool is shared. Rendering slot X = `pool.hide_all()` then
`phases[X.phase].enter(pool, X, arb)` then `pool.render(display)`.

A phase's buttons are wired inside `enter()` to closures capturing
`(arb, slot, pool)`, so each phase's button behaviour is co-located with it
(this is what the rejected `PhasedWidget` was meant to give). `enter()` is a
pure, idempotent view build — anything with a side effect (starting a timer,
kicking off analysis) goes in `activate()`, which runs once on arrival, so
re-rendering a phase (e.g. on focus toggle) never restarts its work. Entry-form
edits write through to `slot.record` per keystroke (the arbiter's job), so the
record is always the source of truth and `enter()` can reload fields freely.
Modals are ordinary phases that fill the screen for v1 (dimmed-overlay is a
later refinement).

`Phase` objects are **stateless strategies**: they read/write the slot passed
in, never hold per-slot state themselves, so one instance serves both slots.
Phases that differ only by data are one class instantiated twice
(`ReagentPhase(120, …)`, `ReagentPhase(420, …)`), not two subclasses.

### Why not `PhasedWidget` (widget composed with a list of phases)

That stores the widgets×phases matrix column-wise: every widget needs an entry
(or a default) for every phase, adding a phase touches many widgets, and "what
does phase 2 look like?" becomes a scatter-gather. The only thing it buys —
co-locating the action button's four behaviours — wiring buttons inside each
phase's `enter()` already gives, co-located with the rest of that phase. So the
wrapper earns nothing.

## Slots run concurrently; the Arbiter mediates the shared resources

Only one slot is *displayed*; the other stays live in the background. What must
survive in the background is **model state** (record, phase, timer), never
pixels.

- **Timers store an absolute monotonic deadline**, not a decrementing counter.
  `remaining = deadline - now`, computed when drawn or polled. A backgrounded
  countdown stays correct with **zero** background work — time advances on its
  own. Pause = freeze remaining; resume = `deadline = now + remaining`.
  `TimerState` methods take `now` as an argument (testable; never read the clock
  themselves).
- **Polling is arithmetic.** Each main-loop pass asks each slot `timer.expired(now)`
  — two subtractions. No threads.

The **Arbiter** is the single object above both slots. Slots and phases never
touch shared hardware directly — they ask the arbiter, which grants / defers /
denies:

- `request_capture(slot)` — one capture at a time across BOTH slots. A capture
  begins only from a focused CAPTURE press, then runs capture→analysis to
  completion while the user may toggle slots or preview other phases. Every
  CAPTURE button is disabled while any capture is live. (Predictive "act within a
  feasible window" conflict avoidance lands here later — one object to change.)
- `alarm(slot)` — sound the buzzer. **Alarm only; never auto-switch focus.**
- `focus(slot)` — user-initiated toggle only.
- `poll_timers()` / `pump_capture()` / `pump_analysis()` — advance the in-flight
  capture one step, complete analysis, and poll both slots for expiry. Driven by
  the main loop.

It owns the single in-flight `CaptureSession` (+ which slot owns it), both
slots, the focus pointer, the pool, and the buzzer.

## Navigation: preview vs the live phase

A slot separates the phase it is **on** from the phase it is **showing**:
`active_phase` is where the test actually is; `phase` is the screen drawn now.
They match except while browsing.

- **PRIOR / NEXT preview** the neighbouring steps (`arb.view`) — they change only
  the viewed phase, never the live one, and never run `activate()`, so a preview
  starts and resets nothing.
- **`arb.goto` is the only real advance** (Begin, a countdown expiring, a capture
  completing, next-sample). It sets both `phase` and `active_phase` and runs
  `activate()`.
- **While previewing** (`phase != active_phase`) the header main button becomes
  **Resume** (`arb.resume` → back to the live phase) and the previewed phase's
  action button is disabled — a preview can look but not act.
- **Timer expiry follows `active_phase`**, not the viewed phase, so a countdown
  you have navigated away from still advances and alarms correctly.
- **Nothing locks navigation.** A running countdown or an in-flight capture is
  arbiter-owned and stays alive in the background — leaving its screen is the
  same safe case as an A/B toggle, and Resume returns. (`lock_nav` survives in
  `_chrome` but is unused, kept for a future phase that must truly trap the user.)

## Decisions locked

1. **No CSV / no YAML.** Geometry and colours are Python. Deletes ~600 lines of
   loader/parser/validation and three data files.
2. **Vocabulary: phase** (Test → Phase → Period, per the existing docs).
3. **Armed vs running is a boolean on the phase, not a separate node.** It is
   transient interaction state, not part of the durable record; `slot.running`
   guards NEXT and picks the button label.
4. **Full-clear on phase change**, so hiding a widget is just "don't draw it" —
   no per-widget erase logic. Per-widget redraw is only for 1 Hz updates.
5. **Pixels live only in `layout.py`, never in a phase.** Phases speak in widget
   names, text, enabled, and palette names. That single rule makes a second
   frontend (Flask) cheap: swap `layout.py` + the pool builder, keep phases and
   slots. Geometry is per-frontend and deliberately not shared; the phase model
   is the shared asset.
6. **Preview navigation, not step-through.** PRIOR/NEXT preview neighbouring
   phases without advancing the test; the header offers Resume back to the live
   phase. `active_phase` (live) is distinct from `phase` (viewed).
7. **Background work never locks the UI.** Running countdowns and in-flight
   captures keep running while you navigate away — the arbiter polls and alarms,
   and Resume returns. Same handling as an A/B slot toggle.

## Files (this branch)

- `palette.py` — colour names → RGB565 (the hardware frontend's reading of the
  portable palette names). **Done.**
- `layout.py` — named `Rect`s for the 320×240 display; reusable as widget args.
  **Done.**
- `timer.py` — `TimerState` (model) + `TimerWidget` (view, pause/‖·MM:SS·›/resume
  thirds). **Done.**
- `pool.py` — build `{name: Widget}` from the layout; `put`/`show`/`hide_all`,
  render (full clear) / redraw (one widget) / touch routing. **Done, self-test.**
- `phases.py` — `Phase` base + `EntryPhase`, `ReagentPhase` (×2 by data),
  `ReadPhase`, `AnalysisPhase`, `ModalPhase`; `build_phases()` registry. The
  self-test's `MockArbiter` drives a slot end-to-end and is the executable spec
  for `arbiter.py`. **Done, self-test.**
- `slot.py` — the `Slot` dataclass (record, phase, running, capturing, timer,
  progress, result). **Done.**
- `arbiter.py` — the contract from `phases.py` made real: focus + slot toggle,
  render-only-the-focused-slot, the single-camera guard, the buzzer, the
  timer-expiry poll (alarm-only), entry field write-through, the keyboard/focus
  manager (ported from `ScreenManager`), and the main loop. Hardware drivers are
  imported lazily so the module + its `--selftest` import cleanly without the
  display/GPIO/camera stacks. **Done, headless self-test.**

**Stubbed / deferred in the arbiter** (each is a marked TODO):
- Analysis engine — placeholder completes after 1.5 s with a "not yet
  implemented" result so the flow reaches POST/NEXT.
- `post_results` — logs only; no upload.
- `nosample` modal — capture does not yet raise a distinguishable "no strip",
  so `retry_capture` just returns to read.
- `next_sample` — clears the run but does not auto-increment the id.

## Rendering notes

- **Labels paint their own background.** `Label`/`TextBox` fill their rect before
  drawing text — `draw_text` renders glyphs only. Without this, white-on-white
  labels are invisible and per-widget redraws (the clock) stack text. A real-
  hardware fix, not just a mock one.
- **The mock display** (`mock/ili9341_mock.py`) clears matplotlib's default
  keymaps (else typing `l`/`k`/`L` toggles a log axis into a math-domain crash,
  `f` fullscreens, `q` quits) and stamps every artist with an increasing z-order
  so call order is paint order — otherwise matplotlib draws patches under text
  and a per-widget redraw cannot erase.
- **Simulation shortcuts:** reagent countdowns are 20 s each under
  `COLORIMETER_SIMULATION`. Run live with
  `~/Projects/Colorimeter/.venv/bin/python3 src/run_simulation.py`.

## Remaining

1. **Retire** `screen_manager.py`, `csv_loader.py`, `csv_to_widgets.py`,
   `app_controller.py`, and the CSV files. Move `src/proposed/` out.
2. **Screen geometry** in `layout.py` still needs work (spacing, the collect
   line, the label chips, the footer clock chip).
3. **Test on the Raspberry Pi.** Verified in simulation only; the GUI is
   incomplete and untested on hardware. The loop is single-threaded cooperative,
   so a Pi Zero 2 W (quad A53 + NEON) is the target for concurrent capture +
   analysis + responsive nav; a Zero W (single ARMv6, no NEON) cannot overlap
   heavy work.
4. The arbiter stubs above (analysis engine, `post_results`, `nosample`,
   `next_sample` id increment).

**Cutover: done** — `main.py` / `run_simulation.py` launch `Arbiter().start()`
and the app runs live in simulation.
