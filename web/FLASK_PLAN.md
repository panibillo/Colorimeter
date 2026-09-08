# Flask web interface for the Colorimeter — implementation plan

> **Status:** planned, not started. No code from this plan has been written.
> Drafted 2026-08-12. Supersedes nothing; `WEB_INTERFACE.md` still describes the
> `dev/webtest.py` hardware prototype and is rewritten by step 13 below.

## Context

The instrument's only interface today is a 320×240 ILI9341 touch panel driven by a
single-threaded loop in `src/arbiter.py`. Three existing specs call for a web
interface and agree on its shape:

- **`WEB_INTERFACE.md`** (status note, 2026-07) — the production web UI is to be a
  Flask *frontend of the phase/slot model*: "swap `layout.py` + the pool builder,
  keep phases/slots", **not** an extension of the `dev/webtest.py` hardware prototype.
- **`devdocs/lab_instrument_spec_v0.2.md`** §5.1–5.4, §6.4 — one process with a Flask
  thread; durable state in SQLite; **last write wins** between interfaces; the web is
  the primary path for sample metadata entry, config editing, and past-results review.
- **`src/layout.py:8-10`** and **`src/palette.py:1-8`** — the invariant that makes this
  cheap: pixels live only in `layout.py`, colours only in `palette.py`, and phases speak
  only *widget names*, text, `enabled`, and *palette names*.

One part of the old spec is stale: §5.4 has both frontends generated from YAML screen
definitions. That model was retired with the CSV/screen UI. The live portable contract
is the `Pool` API, so the web frontend is a **second Pool**, not a template generator.

Three things drive this plan beyond simple parity:

1. A future hardware model **omits the panel entirely** — the web becomes the only
   interface. Nothing may assume a screen exists.
2. There is **no physical restart button**. Stopping the app without a clean shutdown
   currently means pulling power or SSH-ing in.
3. The **analysis phase does not exist yet** (`arbiter.pump_analysis` is a 1.5 s
   placeholder writing `"Analysis not yet implemented"`). Its pages must be built to a
   contract that the engine can later fill without reworking the UI.

## Decisions taken

| Question | Decision |
|---|---|
| v1 scope | Read-only for the **run**; writes allowed where they touch no hardware (sample metadata, remarks, config). Blocked in v1: Begin, START, CAPTURE, Cancel. |
| Process model | Flask thread **in-process**; handlers never touch hardware — they enqueue commands the main loop drains. |
| Headless | Build it now, and make it the **desktop dev default**. |
| Recovery | Tiers 1–2 built (self-restart, wedged-loop force restart); tier 3 (recovery service) specified, not built. |
| Charts | **Client-side SVG from JSON.** No matplotlib in the app process. |

---

## Concepts

### The central mechanism: a second Pool

`Pool` (`src/pool.py`) is a small, closed API — `put / set / show / hide / hide_all /
enable / recolor / on / render / redraw / visible_names / focusable_widgets /
layout_modal_buttons`, plus `pool[name]` for a handful of type-specific calls. Grepping
`src/phases.py` shows phases use exactly four of those type-specific calls:
`pool["timer"].bind(state, interactive=)`, `.size = N` on two labels/buttons, and
`pool["color_fld"].value`.

So `WebPool` is a class satisfying that API whose widget stand-ins hold
`text / label / value / visible / enabled / size / on_press` and accumulate a JSON
view-model instead of drawing pixels. **`src/phases.py`, `src/slot.py`, and `src/timer.py`
are then reused verbatim** — the browser and the panel render the same phase objects.

The payoff is that the command vocabulary collapses to two verbs:

```
("press", slot_name, widget_name)      # invokes the closure phase.enter() wired
("set",   slot_name, widget_name, text) # writes a field, as a keystroke would
```

No new arbiter API, no parallel control path, and no way for the web to reach a
behaviour the panel doesn't already have. The main loop calls
`webpool[name].on_press()` during its next pass, so the single-camera guard and the
run state stay under the same single thread they are under today.

### Layering

```
   browser  ──HTTP──▶  Flask thread          ┐
                        │  reads snapshot    │  no hardware,
                        │  enqueues commands │  no arbiter calls
                        ▼                    ┘
                   command queue  /  state snapshot
                        │                ▲
   ┌────────────────────┼────────────────┼──────────────────┐
   │  Arbiter._run()    ▼                │   (single thread)│
   │   drain queue → webpool[n].on_press()                  │
   │   poll_timers / pump_capture / pump_analysis           │
   │   render(slot) → Pool → panel   +   WebPool → snapshot │
   └────────────────────────────────────────────────────────┘
```

### Screen sizes

The web frontend receives a *flat map of named regions with text and state* — it is not
handed a layout. CSS decides the arrangement, so one stylesheet covers phone through
desktop with no per-device code:

- Mobile-first single column; ≥720 px two columns; ≥1100 px slots A and B **side by
  side** instead of the panel's A/B toggle.
- 44 px minimum touch targets, `<meta viewport>`, no hover-only affordances.
- `palette.PALETTE` is served as CSS custom properties from a generated
  `/static/palette.css` (rendered via `palette.rgb888()`), so the two frontends cannot
  drift in colour.
- **No CSS framework** (resolves spec OQ-4). The palette is already the semantic colour
  source of truth; Pico or Bootstrap would be a second one. ~250 lines of hand-written
  CSS Grid, vendored, offline. Tradeoff: fewer free defaults, but no build step and
  nothing to fight.

Timers are **not** polled at 1 Hz. The snapshot carries the absolute deadline and the
browser counts down locally, resyncing on each poll (2 s idle, 1 s during capture,
`304 Not Modified` on an unchanged revision). This matters on a Zero 2 W.

### Opportunities the web unlocks

Things a 320×240 panel is bad at and a browser is good at: the config editor as one
table instead of a pager; ROI editing by dragging on a full-resolution image; past
results review; QC/error pages; downloading `.h5` archives; reading a boot log that
failed before the splash could draw. The reagent-timer alarm becomes a browser
notification, so a run can be watched from a phone in another room.

### Challenges, named

| Challenge | Handling |
|---|---|
| Flask thread + main loop both hit SQLite; `db_manager` opens a connection per call with no WAL and no busy timeout | Enable WAL + `busy_timeout` in `db_manager`'s connect path (see risk below) |
| Two interfaces editing one field | Spec §5.3 last-write-wins; the queue serialises, the poll refreshes |
| Headless dev default + read-only v1 means *nothing* can start a run in dev | Control level becomes a **config setting**, not a hardcode (below) |
| Web must not silently drive an instrument someone is standing at | Panel shows a web-active indicator; restart requires confirm and refuses mid-capture |
| Unauthenticated LAN page with a restart button | POST-only for side effects, CSRF token, admin token from config |

**WAL risk worth flagging now:** WAL adds `-wal`/`-shm` sidecar files. `pushdb.sh` /
`pulldb.sh` copy the `.sqlite` file alone, which under WAL can drop recent commits.
Those two scripts need a `VACUUM INTO` or a checkpoint before copying — a small change,
but a silent data-loss path if missed.

---

## Headless mode

Two orthogonal axes, replacing the single `_SIMULATION_MODE` boolean
(`src/arbiter.py:45`):

- `COLORIMETER_SIMULATION` — driver source (real / mock). Unchanged.
- `COLORIMETER_DISPLAY` ∈ `auto | panel | mock | none` — display backend. New.

`none` selects a null display, null touch, and null keyboard. The display API surface is
tiny and already written down twice — `pool.py`'s self-test stub (`fill_screen`,
`fill_rect`, `draw_rect`, `draw_text`, `draw_image`) is the whole contract.

Two behaviours must change when there is no panel, because both currently block on a
touch that can never arrive:

- `splash_screen.Splash` waits for OK on config-restore dialogs and for START/MENU at
  `finish()`. Headless **auto-acknowledges**, recording each dialog in `BootLog`; the
  web boot page shows them.
- `shutdown_screen.run()` waits 15 s for a choice. Headless skips the screen and acts on
  the already-chosen action.

The Flask thread starts **early** — right after `_init_logging()`, before drivers — so
the boot page is reachable during a slow or failing boot. That is precisely the case
headless mode has to survive.

---

## Restart and recovery

Tiers, by what is still listening:

| Tier | Failure | Listening | Recovery | Privilege |
|---|---|---|---|---|
| 1 | none | Flask thread | queue restart → clean shutdown → `os.execv` | none |
| 2 | main loop wedged | Flask thread (separate thread, still answers) | stale-heartbeat check → `os._exit(70)` | none |
| 3 | app exited | **nothing** | recovery service | scoped |
| 4 | crash-loop at boot | nothing, systemd gave up | recovery service, or `StartLimitIntervalSec=0` |
| 5–6 | network / OS down | nothing | physical |

**Tiers 1–2 need no new process and no privilege**, and reuse code that exists —
`shutdown_screen.py` already relaunches via `os.execv`. Add a `_loop_beat` monotonic
stamp to `Arbiter._run()` so the Flask thread can tell a wedged loop from a busy one.

The unit's `Restart=on-failure` (`install_colorimeter_service.sh:57`) is **already
correct and must not become `Restart=always`** — the panel's Menu → Power → Exit is a
legitimate clean exit that should stay exited. Non-zero exit codes restart; `exit(0)`
does not; `os.execv` bypasses systemd entirely. The only unit change needed is
`StartLimitIntervalSec=0`, so a crash-loop is retried forever instead of parking the
unit in `failed` after 5 attempts in 10 s.

**Tier 3 is specified, not built** — to be documented in `WEB_INTERFACE.md` as
`colorimeter-recovery.service`: a separate always-on stdlib-only (`http.server`) unit on
port 5001, no camera and no DB writes, serving unit status, the last journal lines, and
start/stop/restart/poweroff. Its privilege is one sudoers entry enumerating **exact
command lines with no wildcards**, password-protected and POST-only. The hardening
variant (flag file + root-owned systemd `.path` unit, granting a single bit rather than
a command line) is recorded as the alternative. On a screenless unit this stops being
optional, since the panel's Power menu is gone too.

---

## Room for the analysis phase

`analysis_view.py` is the seam: the pages are built against it, and when the engine
lands only this module changes.

```python
result_summary(sample_rowid) -> dict   # samp_no, value, units, ci, ci_level,
                                       # collect_dt, remark, status
result_metrics(sample_rowid) -> list   # name, label, value, units,
                                       # status ok|warn|fail, low, high, note
result_series(sample_rowid, name) -> dict   # chart JSON: {x[], y[], labels}
```

**No DDL change in v1.** `samples.txt_result` / `num_result` already exist; a
`result_metrics` table and headline columns (`num_result_ci`, `result_units`,
`result_status`) are *described* here as the engine's target, but adding schema before
the engine exists is premature. Note for whoever does add a `samples` column: it takes
three edits — `src/db_DDL.sql`, `sample_repo._UPDATABLE`, and
`capture_meta.SAMPLE_COLUMNS`.

**Three of the error factors named are computable today**, from existing timestamp
columns — so the QC page ships with real data on day one rather than an empty shell:

- reagent 1 and 2 actual duration vs `protocol.reagent{1,2}_secs`
- delay between reaction 2 and measurement — `capture_start - reagent2_end`
- sample temperature `samp_degC` vs range

Imaging-derived factors render as "pending". Limits come from new `config` rows under a
`qc` role, consistent with the config-in-DB direction.

The one **v1 chart with real data** is the sample timeline (reagent 1 / reagent 2 /
delay / capture, actual vs target), which exercises the whole JSON→SVG path end to end.

---

## Control-level policy

Making the read-only boundary a setting rather than a hardcode is what "build in-process
so control can be added later without re-architecting" means in practice — and it is
forced anyway by headless-as-dev-default, since with no panel nothing else can start a
run. New `config` role `web`:

| name | default | meaning |
|---|---|---|
| `control_level` | `metadata` | `readonly` \| `metadata` \| `full` |
| `enabled` | `1` | start the Flask thread |
| `port` | `5000` | |
| `bind` | `0.0.0.0` | narrow per site |
| `poll_interval_s` | `2.0` | idle poll |
| `admin_token` | *(generated)* | required by `/admin/*` |

`metadata` allows `set` on any field plus presses of navigation and entry fill-buttons;
it rejects `run_btn` in reagent/read phases and `main_btn` where it means Begin, POST or
CANCEL. Seed in `provisioning/config_defaults.sql`.

---

## Implementation steps

Small and incremental, per `CLAUDE.md`. Each step ends runnable.

1. **`WebPool`** — `src/web/webpool.py`. Widget stand-ins + the `Pool` API + `snapshot()`.
   No Flask yet.
2. **Conformance test** — `test/test_webpool.py`. `src/phases.py`'s own `__main__`
   self-test drives everything through `pool[name]` attributes and `w.on_press()`; point
   that harness at a `WebPool` and it must pass **unmodified**. This is the proof that
   the two frontends stay in step.
3. **Null backends + display axis** — `src/mock/display_null.py` (display, touch,
   keyboard); `COLORIMETER_DISPLAY` in `arbiter._init_drivers/_init_display/_init_peripherals`;
   headless branches in `splash_screen.py` and `shutdown_screen.py`. Add `run_headless.py`.
4. **DB concurrency** — WAL + `busy_timeout` in `db_manager`; checkpoint in `pushdb.sh` /
   `pulldb.sh`.
5. **Queue + snapshot + heartbeat** — `src/web/commands.py`, `src/web/state.py`;
   `_loop_beat` and a queue drain in `Arbiter._run()` (`arbiter.py:3530`); a second
   `WebPool` render alongside `Arbiter.render()` (`arbiter.py:812`).
6. **Flask server** — `src/web/server.py`. Thread start after `_init_logging()`;
   Waitress if importable, Flask dev server otherwise. `threaded=True` — a deliberate
   divergence from `WEB_INTERFACE.md`'s `threaded=False`, which was correct only because
   the prototype's handlers touched hardware directly; these do not.
7. **Run view** — templates + CSS + palette route + polling. Read-only, both slots.
8. **Control policy** — `control_level` enforcement on the drain path.
9. **Review screens** — `list_samples(limit, offset, filter)` and `get_sample(rowid)`
   added to `sample_repo.py`; paginated list + detail view.
10. **Analysis pages** — `src/analysis_view.py` with the timing/temperature metrics;
    summary page, QC page, `chart.js` (vanilla, ~120 lines, SVG); timeline chart.
11. **Config editor (web)** — native HTML over `config_repo`, honouring the same
    `_any_slot_active()` gate as the panel (`arbiter.py:1800`).
12. **Restart tiers 1–2** — `/admin/restart`, force path, confirm + mid-capture refusal,
    `StartLimitIntervalSec=0`.
13. **Docs + deploy** — rewrite `WEB_INTERFACE.md` around this design (keeping the
    prototype section clearly marked), add the tier-3 recovery spec, and **add every new
    file to `sync_pi_files.txt`** — it is an explicit non-wildcard list, so anything
    missing silently never reaches the Pi.

Not in this plan, and stated so it is not mistaken for an omission: the image viewer /
ROI editor and the capture-launch screens stay panel-only for now — they are the two
most fused into `arbiter.py` (~1100 lines) and need extraction first. `_cap_mode` should
move to `config` alongside the already-persisted `defaults.exposuregroup` so both
frontends read one source.

## Files

**New:** `src/web/{__init__,server,webpool,weblayout,commands,state}.py`,
`src/web/views_{run,review,analysis,config,admin}.py`, `src/web/templates/`,
`src/web/static/{style.css,chart.js}`, `src/analysis_view.py`,
`src/mock/display_null.py`, `run_headless.py`, `test/test_webpool.py`

**Modified:** `src/arbiter.py` (display axis, queue drain, `_loop_beat`, web render),
`src/db_manager.py` (WAL), `src/sample_repo.py` (list/get), `src/splash_screen.py` +
`src/shutdown_screen.py` (headless), `provisioning/config_defaults.sql`,
`provisioning/deploy/install_colorimeter_service.sh`, `pushdb.sh`, `pulldb.sh`,
`sync_pi_files.txt`, `WEB_INTERFACE.md`, `ARCHITECTURE.md`

**Reused unchanged:** `phases.py`, `slot.py`, `timer.py`, `config_repo.py`, `roi.py`,
`exposure_repo.py`, `capture_*`, `palette.py`

> **Open:** this plan places the package at `src/web/`, matching the project convention
> that all application modules live in `src/` (and `CODING_GUIDELINES.md:26`, which names
> `web_interface.py`). The repo now also has a top-level `web/` holding this document.
> Decide before step 1 whether the code goes to `src/web/` or `web/`; `sync_pi_files.txt`
> and the systemd `WorkingDirectory` both depend on the answer.

## Verification

- **Step 2 is the keystone**: `test/test_webpool.py` runs `phases.py`'s existing
  self-test against `WebPool` unmodified. If it passes, the web frontend renders every
  phase the panel does, with the same labels, enabled states and button wiring.
- `.venv/bin/python3 run_headless.py` on the desktop → browser at `localhost:5000` →
  drive a full mock run (entry → reagents → capture → analysis) with `control_level=full`,
  with **no panel window open at all**. This is the dev default and the screenless-model
  rehearsal in one.
- `.venv/bin/python3 src/run_simulation.py` unchanged → panel mock and browser open at
  once; edit a field in each and confirm last-write-wins in both directions.
- Existing `test/` suite green; `python3 src/phases.py` and `python3 src/arbiter.py`
  self-tests still pass.
- On a Pi: reagent timers counting in the browser while the panel runs; confirm the
  browser countdown stays within a second of the panel across a full 7-minute reagent
  phase (proves local countdown + resync).
- Restart: `/admin/restart` from the browser mid-idle → app returns within ~10 s, DB
  intact. Force path: `kill -STOP` the process to fake a wedged loop, confirm the
  heartbeat goes stale and the force restart is offered.
- Review at three widths (phone 390 px, tablet 820 px, desktop 1440 px) with devtools;
  no horizontal scroll, targets ≥44 px, A/B side-by-side above 1100 px.

## Open items

- **Package location** — `src/web/` vs `web/` (see the Files note above).
- **Auth (spec OQ-1)** — v1 leaves the UI unauthenticated on the LAN but gates `/admin/*`
  behind `web.admin_token`. Full HTTP Basic is deferred.
- **WCAG 2.1 AA (§6.4)** — hand-written CSS makes contrast and focus-visible our own
  job. v1 targets keyboard navigability, labelled controls, and no colour-only meaning;
  a full audit is not in scope.
- The `data` table (`timestamp, label, param, numval, txtval`) is empty and referenced by
  no repo. It is close to the `result_metrics` shape — worth deciding whether the
  analysis engine adopts it or it gets dropped.
