# Resume prompt — phase/slot UI redesign

Paste this to resume work on the branch `phase-slot-model`.

---

We are rebuilding the Colorimeter UI from a screen-centric, CSV-driven model to
a **phase/slot** model, on branch `phase-slot-model`. The full design and
rationale are in `dev/phase_model_design.md` — read it first. Key context:

**Architecture (one view, stateless phases, stateful slots, one arbiter):**
- **Pool** (`pool.py` + `layout.py` + `palette.py`) — one widget instance per
  named rect; the single physical 320×240 screen. Full-clear render on phase
  change; single-widget redraw for 1 Hz updates.
- **Phase** (`phases.py`) — stateless strategy: `enter(pool, slot, arb)` builds
  the view and wires buttons, `activate(arb, slot)` runs once on arrival,
  `on_expire(arb, slot)`. Phases differing only by data are one class twice
  (the two `ReagentPhase`s). The arbiter contract phases call is documented at
  the top of `phases.py`.
- **Slot** (`slot.py`) — pure data (record, phase, running, capturing, timer,
  progress, result). Two of them (A, B); both stay live in the background.
- **Timer** (`timer.py`) — `TimerState` stores an absolute monotonic deadline
  (correct with zero background ticking; `now` always passed in) + `TimerWidget`
  (‖ / MM:SS / › thirds, `interactive=False` for armed/final previews).
- **Arbiter** (`arbiter.py`) — the one object above the slots: focus + slot
  toggle, `render()` draws only the focused slot (the shared-pool invariant),
  the single-camera guard, buzzer, timer-expiry poll (alarm-only, never
  auto-switch focus), entry field write-through, keyboard/focus manager, main
  loop. Hardware imports are lazy (`_init_hardware`), so the module imports
  without matplotlib/GPIO/camera.

**Everything has a passing self-test** — run before and after any change:
```
PYTHONPATH=src python3 src/timer.py
PYTHONPATH=src python3 src/pool.py
PYTHONPATH=src python3 src/phases.py
COLORIMETER_SIMULATION=1 PYTHONPATH=src python3 src/arbiter.py --selftest
```

**Immediate next task — cutover (not yet done):**
1. Point `main.py` and `run_simulation.py` at `Arbiter().start()` instead of
   `AppController().start()`. Leave the old modules in place at first so they
   can be A/B compared.
2. Verify in simulation. This needs `matplotlib` (the sim display,
   `mock/ili9341_mock.py`) — it was NOT installed in the last dev shell, so the
   app could not be run live there; verification so far is the four self-tests
   only. Install matplotlib in the dev shell, or run on the Pi.
3. Then retire `screen_manager.py`, `csv_loader.py`, `csv_to_widgets.py`,
   `app_controller.py`, and the CSVs (`screen_defs.csv`, `screen_widgets.csv`,
   `widget_defs*.csv`). Move `src/proposed/` (abandoned CSV exploration) out.

**Stubbed TODOs in `arbiter.py`** (search "not implemented"/"TODO"/"stub"):
analysis engine (1.5 s placeholder result), `post_results` (log only),
`nosample` modal detection (capture can't yet report "no strip"),
`next_sample` id auto-increment.

**Working preferences (important):**
- Small, incremental steps; run the relevant self-test after each.
- No CSV, no YAML — geometry and colours are Python.
- Vocabulary is **phase** (Test → Phase → Period).
- Do **not** use the pop-up multiple-choice question tool — it obscured the
  conversation for this user; ask any questions inline as plain text.
- Do not commit unless asked. Do not `git add -A` — the repo has many unrelated
  untracked files; stage specific paths only.
