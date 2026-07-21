# Resume prompt — phase/slot UI redesign

Paste this to resume work on the branch `phase-slot-model`.

---

We are rebuilding the Colorimeter UI from a screen-centric, CSV-driven model to
a **phase/slot** model, on branch `phase-slot-model`. The new model now drives
the app. Full design and rationale: `dev/phase_model_design.md` (read it first).

**Architecture (one view, stateless phases, stateful slots, one arbiter):**
- **Pool** (`pool.py` + `layout.py` + `palette.py`) — one widget instance per
  named rect; the single physical 320×240 screen. Full-clear render on phase
  change; single-widget redraw for 1 Hz updates.
- **Phase** (`phases.py`) — stateless strategy: `enter(pool, slot, arb)` builds
  the view and wires buttons, `activate(arb, slot)` runs once on a real arrival,
  `on_expire(arb, slot)`. Phases differing only by data are one class twice (the
  two `ReagentPhase`s). The arbiter contract is documented atop `phases.py`.
- **Slot** (`slot.py`) — pure data: record, `phase` (viewed) vs `active_phase`
  (live), `running`, `capturing`, `timer`, `progress`, `result`. Two of them
  (A, B); both stay live in the background.
- **Timer** (`timer.py`) — `TimerState` stores an absolute monotonic deadline
  (correct with zero background ticking; `now` always passed in) + `TimerWidget`
  (‖ / MM:SS / › thirds, `interactive=False` for armed/final previews).
- **Arbiter** (`arbiter.py`) — the one object above the slots: focus + slot
  toggle, `render()` draws only the focused slot (shared-pool invariant), the
  single-camera guard, buzzer, timer-expiry poll (alarm-only, follows
  `active_phase`), entry field write-through, keyboard/focus manager, main loop.
  Hardware imports are lazy (`_init_hardware`), so the module imports without
  matplotlib/GPIO/camera.

**Navigation:** PRIOR/NEXT *preview* neighbours (`arb.view` — no advance, no
activate); `arb.goto` is the only real advance (sets viewed + live, runs
activate); `arb.resume` returns the view to `active_phase`. While previewing, the
header shows **Resume** and the previewed action is disabled. Nothing locks nav —
running countdowns and in-flight captures stay alive in the background (same as
an A/B toggle) and Resume returns.

**Run it live** (use the venv — it has matplotlib):
```
~/Projects/Colorimeter/.venv/bin/python3 src/run_simulation.py
```
**Self-tests** (run before and after any change):
```
PYTHONPATH=src ~/Projects/Colorimeter/.venv/bin/python3 src/timer.py
PYTHONPATH=src ~/Projects/Colorimeter/.venv/bin/python3 src/pool.py
PYTHONPATH=src ~/Projects/Colorimeter/.venv/bin/python3 src/phases.py
COLORIMETER_SIMULATION=1 PYTHONPATH=src ~/Projects/Colorimeter/.venv/bin/python3 src/arbiter.py --selftest
```

**Done:** all modules + self-tests; cutover (`main.py` / `run_simulation.py`
launch `Arbiter().start()`); runs live in simulation; sim-rendering fixes (label
backgrounds, matplotlib keymaps, mock z-order); preview/Resume navigation.

**Next tasks:**
1. Retire `screen_manager.py`, `csv_loader.py`, `csv_to_widgets.py`,
   `app_controller.py`, and the CSVs. Move `src/proposed/` out.
2. Screen geometry needs work (`layout.py`).
3. Test on the Raspberry Pi — simulation only so far; the GUI is incomplete and
   untested on hardware. The loop is single-threaded cooperative; a Zero 2 W
   (quad A53 + NEON) is the target for concurrent capture + analysis + nav, a
   Zero W (single ARMv6, no NEON) cannot overlap heavy work.
4. Arbiter stubs: analysis engine (1.5 s placeholder), `post_results` (log only),
   `nosample` modal detection, `next_sample` id auto-increment.

**Working preferences (important):**
- Small, incremental steps; run the relevant self-test after each.
- Use `~/Projects/Colorimeter/.venv/bin/python3` for all simulations.
- No CSV, no YAML — geometry and colours are Python.
- Vocabulary is **phase** (Test → Phase → Period).
- Do **not** use the pop-up multiple-choice question tool — ask inline as text.
- Do not commit unless asked. Do not `git add -A` — the repo has many unrelated
  untracked files; stage specific paths only.
