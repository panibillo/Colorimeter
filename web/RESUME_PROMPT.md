# Resume prompt — Flask web interface

Paste the block below into a fresh Claude Code session in `~/Projects/Colorimeter`
to pick this work up. Everything it needs is either in the prompt or in the repo;
nothing depends on the conversation that produced it.

---

```
We planned a Flask web interface for the Colorimeter. The full plan is in
web/FLASK_PLAN.md — read that first, along with WEB_INTERFACE.md (its 2026-07
status note), ARCHITECTURE.md, and the docstrings at the top of src/layout.py,
src/palette.py, src/pool.py and src/phases.py.

Nothing has been implemented yet. Planning only.

The core idea, so you can sanity-check the plan rather than take it on faith:
src/pool.py is a small closed API, and src/phases.py speaks only widget names,
text, enabled, and palette names — never pixels. So the web frontend is a second
Pool (a WebPool that accumulates a JSON view-model instead of drawing), which
lets phases.py / slot.py / timer.py be reused verbatim. Control collapses to two
verbs the main loop drains from a queue: ("press", slot, widget) and
("set", slot, widget, text). Flask handlers never touch hardware or call the
arbiter directly.

Decisions already made — do not re-litigate these without asking me:
- v1 is read-only for the RUN. Writes that touch no hardware are allowed
  (sample metadata, remarks, config). Blocked in v1: Begin, START, CAPTURE, Cancel.
  The boundary is a config setting (web.control_level), not a hardcode.
- Flask runs in-process as a thread inside the arbiter process, with a command
  queue drained by the main loop. Not a separate process.
- Headless mode gets built now and becomes the desktop dev default: a new
  COLORIMETER_DISPLAY axis (auto|panel|mock|none) orthogonal to
  COLORIMETER_SIMULATION. A future hardware model has no panel at all, so nothing
  may assume a screen exists.
- Restart: build tiers 1-2 (in-process graceful restart via the existing os.execv
  path, plus a wedged-loop force restart using a main-loop heartbeat). Neither
  needs privilege. The separate recovery service (tier 3) is specified in the plan
  but NOT built. Keep Restart=on-failure in the systemd unit — do not change it to
  always; only add StartLimitIntervalSec=0.
- Charts are client-side SVG rendered from JSON. No matplotlib in the app process.
- No CSS framework. Palette names become CSS custom properties generated from
  palette.PALETTE so the two frontends can't drift.

Before writing code, settle one open question with me: the plan puts the package
at src/web/ (matching the convention that all app modules live in src/), but the
repo now has a top-level web/ holding the planning docs. Which location do you
want for the code? sync_pi_files.txt and the systemd WorkingDirectory both depend
on the answer.

Then start at step 1 of the plan (WebPool) and step 2 (the conformance test) —
step 2 is the keystone: src/phases.py's existing __main__ self-test drives
everything through pool[name] attributes and w.on_press(), so pointing that
harness at a WebPool must pass UNMODIFIED. That is the proof the two frontends
stay in step. Get those two steps working and stop for review before going on.

Project constraints to respect: work in small incremental steps (CLAUDE.md); run
Python with .venv/bin/python3, not system python3; sync_pi_files.txt is an
explicit non-wildcard list, so any new file must be added there or it never
reaches the Pi.
```

---

## State of play (for your own reference, not part of the prompt)

**Written so far:** this file and `web/FLASK_PLAN.md`. No code.

**The three specs the plan reconciles**

| Doc | What it contributes | Caveat |
|---|---|---|
| `WEB_INTERFACE.md` | Flask/LAN framework choice, signal-handling table, matplotlib rules; the 2026-07 note directing a phase/slot frontend | Describes the `dev/webtest.py` prototype, not the app. Its `threaded=False` and matplotlib guidance are both deliberately diverged from. |
| `devdocs/lab_instrument_spec_v0.2.md` | §5.1 process model, §5.3 last-write-wins, §6.4 web responsibilities | §5.4 (YAML-generated templates) is **stale** — that model retired with the CSV/screen UI |
| `devdocs/ROADMAP.md` Step 12 | Waitress, auth, WCAG targets | Pico CSS leaning is declined in the plan, with reasons |

**Findings from the survey that the plan depends on**

- `src/phases.py` reaches past the `Pool` API only four times — `timer.bind()`, two
  `.size` assignments, and `pool["color_fld"].value`. That is what makes `WebPool` small.
- `install_colorimeter_service.sh:57` has `Restart=on-failure` with no `StartLimit*`
  overrides, so systemd defaults apply: 5 starts in 10 s, then the unit parks in `failed`.
- `arbiter._SIMULATION_MODE` (`arbiter.py:45`) is a single boolean branching at
  `_init_drivers` / `_init_display` / `_init_peripherals`.
- `db_manager.py` opens a connection per call with no WAL and no busy timeout — fine for
  one thread, not for two.
- `samples` already has `txt_result` / `num_result` but nothing computes them; every
  populated row reads `"Analysis not yet implemented"`.
- Three of the QC factors wanted for the analysis page are computable from existing
  columns today: reagent 1/2 actual vs target, `capture_start - reagent2_end`, and
  `samp_degC`.

**Deliberately out of scope**, so it isn't mistaken for an oversight: the image
viewer / ROI editor and the capture-launch screens stay panel-only — they are the two
most fused into `arbiter.py` (~1100 lines) and need extraction first.
