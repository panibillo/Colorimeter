# Phase / Slot model — design

Supersedes the screen-centric model (`screen_manager.py`, `csv_loader.py`,
`csv_to_widgets.py`, `screen_defs.csv`, `screen_widgets.csv`, `widget_defs.csv`)
and the abandoned CSV-step exploration under `src/proposed/`. Nothing is deleted
yet; the old and new systems coexist on this branch until the new one drives the
app.

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
| **Slot** — state | 2 (A, B) | record, current phase id, `running` flag, `TimerState` | `slot.py` |
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
  completion while the user may toggle away. Every CAPTURE button is disabled
  while any capture is live. (Predictive "act within a feasible window" conflict
  avoidance lands here later — one object to change.)
- `alarm(slot)` — sound the buzzer. **Alarm only; never auto-switch focus.**
- `focus(slot)` — user-initiated toggle only.
- `pump()` — advance the in-flight capture one step; poll both slots for expiry.
  Driven by the main loop.

It owns the single in-flight `CaptureSession` (+ which slot owns it), both
slots, the focus pointer, the pool, and the buzzer.

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

## Remaining

1. **Cutover.** Point `main.py` (and `run_simulation.py`) at `Arbiter().start()`
   instead of `AppController().start()`. Verify in simulation (needs
   `matplotlib`, which is not installed in the current dev shell).
2. **Retire** `screen_manager.py`, `csv_loader.py`, `csv_to_widgets.py`,
   `app_controller.py`, and the three CSV files. Move `src/proposed/` out.
