# Phase Scheduling and Overdue Task Design

*Resolve before starting Step 1 (YAML/config) because the YAML phase and
period schema must reflect these decisions.*

See [TERMINOLOGY.md](TERMINOLOGY.md) for definitions of Test, Phase, Screen,
Period, Modal Period, and Focus Window.

---

## 1. The Core Problem

A test proceeds through a sequence of phases. Each phase (= screen) consists
of one or more **periods**. Some periods are **modal**: the user must perform
a specific action within a deadline, and the UI locks navigation away until
that action is complete (like a modal dialog). Other periods are non-modal
waiting times during which the user can freely switch between test slots.

If a modal period's deadline passes without completion, the task becomes
**overdue**: the modal lock is released, the task is pushed onto the overdue
stack, and an alert fires. Multiple simultaneous tests can generate multiple
overdue tasks.

The conflict scheduler ensures that no two simultaneously running tests have
overlapping modal periods — so in normal operation, the user never has two
modal demands at once.

---

## 2. DB Flags vs. Overdue Deque — Resolved

**DB is ground truth; the overdue deque is a derived in-memory view.**

The DB records what happened and when, survives power loss, and is the source
of truth for recovery on reboot. The deque is a live sorted list rebuilt from
DB state at boot (if incomplete tests exist) and maintained in real time.
They serve different roles and are not duplicative.

### Status enum (one column, not three booleans)

Three boolean flags produce 8 combinations, most invalid. Use a single
`status` column on the period row:

```
pending  → period not yet started
active   → period has started; user must act before scheduled_end (modal)
           or system is in waiting state (non-modal)
overdue  → modal period whose deadline passed without completion
complete → user completed the required action
skipped  → user or system explicitly skipped (result flagged degraded)
```

Transitions:
```
pending  ──[scheduled_start arrives]───► active
active   ──[user completes action]─────► complete      (modal or non-modal)
active   ──[deadline passes]───────────► overdue       (modal only)
overdue  ──[user completes action]─────► complete
overdue  ──[user skips]────────────────► skipped
active   ──[user skips]────────────────► skipped
```

Non-modal periods go `pending → active → complete` and never become overdue.

### DB schema

Tracking is at the **period** level, because modal periods within a phase
each have their own deadline and can each independently become overdue.

```sql
CREATE TABLE test_phases (
    phase_id     INTEGER PRIMARY KEY,
    test_id      TEXT    NOT NULL,        -- FK to tests table
    phase_index  INTEGER NOT NULL,        -- order within test
    phase_name   TEXT    NOT NULL,
    actual_start TEXT,                    -- ISO8601; set when phase begins
    actual_end   TEXT,                    -- ISO8601; set when all periods done
    status       TEXT    NOT NULL DEFAULT 'pending'  -- phase-level summary
);

CREATE TABLE test_periods (
    period_id        INTEGER PRIMARY KEY,
    phase_id         INTEGER NOT NULL,    -- FK to test_phases
    period_index     INTEGER NOT NULL,    -- order within phase
    period_name      TEXT    NOT NULL,
    is_modal         INTEGER NOT NULL DEFAULT 0,  -- 1 = modal period
    scheduled_start  TEXT,               -- ISO8601; computed at test start
    scheduled_end    TEXT,               -- ISO8601; the deadline (modal only)
    actual_start     TEXT,
    actual_end       TEXT,
    status           TEXT    NOT NULL DEFAULT 'pending',
    late_flag        INTEGER NOT NULL DEFAULT 0  -- 1 = completed after deadline
);
```

`late_flag` records that the task was eventually completed but after the
deadline — used to flag result quality without changing the `complete` status.

When a test starts, rows are written for every phase and period with
computed `scheduled_start` and `scheduled_end` timestamps. The overdue
system then works entirely by monitoring these rows.

---

## 3. Overdue Deque Design

### Contents

Only **modal periods with `status = 'overdue'`** are in the deque.

Each deque entry contains:
- `test_slot` — A / B / C / D (for slot color coding in the footer widget)
- `phase_name`, `period_name` — for the footer label
- `period_id` — DB key for status updates
- `scheduled_end` — the missed deadline; used to compute how long overdue
- `target_screen_id` — which screen to navigate to for this task

### Priority ordering

Priority = time overdue, descending. The entry with the earliest missed
`scheduled_end` is highest priority (index 0). This requires no explicit
priority field — it is always implicit in the sort order.

The deque is maintained sorted by `scheduled_end` ascending. New overdue
entries are inserted in sorted position. Completed or skipped entries are
removed by `period_id`.

### Operations

```
push(entry)        — insert sorted by scheduled_end
remove(period_id)  — remove completed/skipped entry
peek()             — deque[0], the highest-priority entry
at(index)          — deque[index]; the footer widget maintains a cursor
is_empty()         — len(deque) == 0
```

### Boot recovery

Query `test_periods` for all rows with `status = 'overdue'` across all
incomplete tests, sorted by `scheduled_end`. Populate the deque. The
overdue footer widget activates automatically if the deque is non-empty.

---

## 4. Footer Overdue Widget

### Normal footer (overdue deque empty)

```
[ MENU/BACK ]    [ YY-MM-DD HH:MM:SS ]    [ NEXT ]
```

### Overdue footer (one or more overdue periods)

```
[ PRIORITY ↑ ]  [A  +MM:SS  Add Reagent ]  [ NEXT → ]
  (flashing)      (slot color, count-up)      (flashing)
```

The center panel shows:
- The slot letter in the slot's color (A=blue, B=amber, C=green, D=coral)
- A count-up timer: how long the **currently highlighted** task has been overdue
- A brief label: phase name + period name (e.g. "A  +01:23  Add Reagent")

### PRIORITY button (left)

- Navigates to the `target_screen_id` of `deque[0]`
- Resets the footer cursor to index 0
- **Flashes** continuously while deque is non-empty, except when the
  currently displayed screen is already for `deque[0]` (then steady)

### NEXT button (right)

- Advances the footer cursor by one position; wraps to 0 at the end
- Navigates to that entry's `target_screen_id`; updates timer and label
- **Flashes** when the deque has more than one entry (indicating more to see)
- Single direction only — no backward walk

### When a task is completed or skipped

Remove from deque. If the cursor pointed at the removed entry, reset cursor
to 0. If the deque empties, the footer reverts to normal immediately.

### Audio

When a period first becomes overdue: sound a distinct alert (different tone
from click or standard beep). Repeat at a cadence configured per period type
in YAML (e.g. every 30 s for reagent addition, every 120 s for capture).
Alert continues until the task is completed or skipped.

---

## 5. What Makes a Period Modal

Each period is defined in the YAML protocol config with a `modal: true/false`
attribute. Modal means: the user must perform a specific action by
`scheduled_end`; until they do (or the period goes overdue), the UI locks
navigation away from this screen.

The full list of phases, their periods, and which periods are modal will be
defined when the test protocol is fully specified. A preliminary sketch:

| Phase (= Screen) | Period | Modal? | Notes |
|---|---|---|---|
| Sample entry | Data entry | Yes | Required before test can start |
| Temperature check | Sensor read | No | Instrument reads automatically |
| Reagent addition + timer | Add reagent | Yes | User must act by deadline |
| Reagent addition + timer | Incubation wait | No | Timer runs; user is free |
| Measurement capture | Initiate capture | Yes | User must press Start |
| Measurement capture | Capture in progress | No | Automated |
| Capture review | Review results | No | Informational; user resolved earlier |
| Results summary | View results | No | Informational |

This table is illustrative. Actual phases, their period breakdown, and modal
designations are protocol-dependent and will be defined in YAML config.

---

## 6. Completion Detection

Each modal period has a `completion_action` defined in its YAML period entry.
`AppController` wires the corresponding screen action to the period completion
handler. When triggered:

1. Set `status = 'complete'`, `actual_end` in `test_periods`
2. Set `late_flag = 1` if `status` was `'overdue'`
3. Remove from overdue deque if present
4. Sound confirmation tone
5. Advance to the next period (or next phase if all periods complete)

Preliminary completion actions per period type:

| Period type | Completion trigger |
|---|---|
| Data entry | NEXT with all required fields non-empty |
| Reagent addition | "Confirm Added" button on reagent screen |
| Capture initiation | Capture successfully starts (camera acknowledgement) |

---

## 7. Scheduling Conflict Check

Before a new test may start, its projected modal period times are compared
against the modal periods of all active tests.

**Algorithm:**

1. Load the protocol for the new test type from YAML config
2. From proposed start time T, compute projected `[scheduled_start, scheduled_end]`
   for every modal period
3. Query `test_periods` for all modal periods in active tests with
   `status IN ('pending', 'active', 'overdue')` — not yet done
4. Check each pair for time overlap
5. **Overlap found:** disable the Start button; show "Conflict — earliest
   available: HH:MM" (computed by finding the first gap large enough)
6. **No overlap:** Start is enabled

This check re-runs every 10–30 seconds. As active tests advance through
their modal periods, previously blocked start times may open up.

**Temperature-dependent durations:** Some period durations depend on sample
temperature. Use the config default for scheduling; flag the result if the
actual temperature-adjusted duration differs materially.

---

## 8. Skipping Overdue Tasks

A modal period that the user cannot complete (reagent unavailable, sample
contaminated) must have an escape. Without a skip mechanism the overdue
footer can never clear.

**Skip behavior:**
- Sets `status = 'skipped'`, `late_flag = 1`, records `actual_end`
- Flags the test result `degraded`
- Removes from overdue deque
- Sounds a distinct warning tone

**UI placement:** A "Skip" button appears on the task's target screen when
it is accessed from the overdue footer. It does not appear in the footer
itself, to prevent accidental skips during navigation.

---

## 9. Open Questions

| # | Question | Recommended answer |
|---|---|---|
| OQ-S1 | Protocol period durations: YAML config or DB? | YAML (fixed per protocol); DB stores computed timestamps |
| OQ-S2 | Is "capture review" ever modal? | No per current design; defer to protocol spec |
| OQ-S3 | Escalating audio cadence: fixed or per period type? | Configured per period in YAML |
| OQ-S4 | Slot color coding in overdue footer center panel? | Yes — immediate multi-test context |
| OQ-S5 | What does Skip do to downstream analysis? | Flags result `degraded`; specifics TBD |
| OQ-S6 | Maximum deque size in practice? | 2–4 (2 tests × 1–2 modal periods per test) |
| OQ-S7 | Abandon a test entirely from overdue widget? | No; system menu action only |
| OQ-S8 | User override before deadline (exit modal early)? | TBD; design supports adding it later |

---

## 10. YAML Schema Requirements (informs Step 1)

Phase and period definitions in the protocol YAML must include:

```yaml
# Protocol definition (per test type)
protocol:
  name: standard_colorimetric
  phases:
    - name: reagent_phase
      screen: reagent_screen
      periods:
        - name: reagent_addition
          modal: true
          duration_seconds: 30          # deadline window
          alert_repeat_seconds: 30      # overdue re-alert cadence
          completion_action: confirm_reagent_added
        - name: incubation_timer
          modal: false
          duration_seconds: 600         # fixed; or temperature_adjusted: true

    - name: measurement_phase
      screen: capture_screen
      periods:
        - name: initiate_capture
          modal: true
          duration_seconds: 60
          alert_repeat_seconds: 60
          completion_action: capture_started
        - name: capture_in_progress
          modal: false
          duration_seconds: 120         # estimated; actual determined by camera
```

The conflict scheduler computes `scheduled_start` and `scheduled_end` for
each modal period at test-start time and writes them to `test_periods`.

---

## 11. Decisions Resolved

| Decision | Resolution |
|---|---|
| DB flags vs. deque | DB (`test_periods` table) is ground truth; deque is derived in-memory view |
| Tracking granularity | Period level (not phase level) — periods have independent deadlines |
| Status field | Single `status` enum per period; not three booleans |
| Priority definition | Oldest missed deadline = highest priority; implicit from sort, no explicit field |
| Deque type | `collections.deque`; maintained sorted by `scheduled_end` |
| Modal definition | Modal-dialog style: navigation locked until complete, overdue, or override |
| Modal position constraint | **None** — modal periods may appear anywhere within a phase |
| Footer widget | PRIORITY (left) + slot-colored count-up timer + label (center) + NEXT (right) |
| Audio | Distinct overdue tone; repeat cadence configured per period in YAML |
| Completion trigger | Defined per period type in YAML `completion_action`; wired in AppController |
| Skip | Allowed; flags result `degraded`; button on target screen, not footer |
| Conflict check | Runs at test start; re-runs every 10–30 s; checks modal period overlap |
