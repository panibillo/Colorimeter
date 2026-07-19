# Phase Scheduling Design

See [TERMINOLOGY.md](TERMINOLOGY.md) for definitions.

---

## 1. Core Model

A test proceeds through a sequence of screens. Each screen is the entire
phase — there is no sub-period layer. A screen has one duration and one
modal flag.

Only **one test runs at a time.** A second test cannot start until the
active test completes. This eliminates concurrent-test scheduling entirely.

---

## 2. Screen Lifecycle

A screen with a non-zero `duration` becomes **alive** when its timer starts.
It stays alive until the test ends. While alive it is monitored. When its
duration expires, the behavior depends on the `modal` flag:

```
modal = 1 (modal screen):
  Navigation is locked while this screen is visible.
  On timeout → alarm fires; system may auto-advance or wait for user action.

modal = 0 (non-modal screen):
  User may navigate freely while the timer runs.
  On timeout → alarm fires; screen takes focus at the next safe moment.
```

A screen with `duration = 0` has no timeout and is never monitored.

---

## 3. State Machine

One status column per screen row in `test_screens`:

```
pending  → screen not yet started
active   → screen is current (visible or alive in background)
complete → user or system completed the required action and advanced
skipped  → user explicitly skipped (result flagged degraded)
```

Transitions:

```
pending  ──[phase sequence reaches this screen]──► active
active   ──[completion action + NEXT]────────────► complete
active   ──[user skip]───────────────────────────► skipped
```

No `overdue` state — a modal screen that times out fires an alarm and
waits; the user must act (complete or skip) before advancing.

---

## 4. DB Schema

Tracking is at the **screen** level (one row per screen per test run).

```sql
CREATE TABLE test_screens (
    screen_run_id    INTEGER PRIMARY KEY,
    test_id          TEXT    NOT NULL,        -- FK to tests table
    screen_seq       INTEGER NOT NULL,        -- order within test
    screen_id        TEXT    NOT NULL,        -- 2-char screen identifier
    is_modal         INTEGER NOT NULL DEFAULT 0,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    scheduled_start  TEXT,                    -- ISO8601; set when phase begins
    scheduled_end    TEXT,                    -- ISO8601; non-null if duration > 0
    actual_start     TEXT,
    actual_end       TEXT,
    status           TEXT    NOT NULL DEFAULT 'pending',
    late_flag        INTEGER NOT NULL DEFAULT 0  -- 1 = completed after timeout
);
```

`late_flag` records that the screen was completed after its duration expired.
It does not change `status`; it qualifies the result for downstream analysis.

---

## 5. Timeout Monitor

`ScreenManager` holds a list of **alive screens**: screens in the active test
whose timer is running. The main loop checks this list each iteration:

```python
for screen in alive_screens:
    if screen.duration > 0 and elapsed(screen) >= screen.duration:
        screen.timeout()   # fires alarm, takes focus if non-modal
```

Since only one test runs at a time, the alive list contains at most one
timed screen at any moment in normal operation.

---

## 6. Begin Actions

When a screen's timer starts, its `beginactions` list is executed in order.
Defined actions:

| Action | Effect |
|--------|--------|
| `alarm_on` | Start the repeating alarm for this screen |
| `alarm_off` | Silence any active alarm |
| `start_timer` | Start the countdown display widget |

Example: a reagent screen might use `[alarm_off, start_timer]` at begin
(silence any prior alarm) and fire `alarm_on` on timeout.

---

## 7. Single-Slot Guard

`AppController` holds an `active_slot` variable (`None`, `'A'`, or `'B'`).
The Start button is enabled only when `active_slot is None`. When a test
completes (all screens reach `complete` or `skipped`), `active_slot` is
cleared.

No conflict scheduling is needed — the guard is a single flag check.

---

## 8. Screen Sequence for Standard Protocol (illustrative)

| seq | screen_id | screen name | duration | modal | notes |
|-----|-----------|-------------|----------|-------|-------|
| 1 | SE | Sample Entry | 0 | 1 | Required fields before start |
| 2 | TC | Temperature Check | 0 | 0 | Auto-read; user may leave |
| 3 | R1 | Reagent 1 | 30 | 1 | User must add by deadline |
| 4 | I1 | Incubation 1 | 600 | 0 | Timer runs; user is free |
| 5 | CA | Capture | 60 | 1 | User initiates camera |
| 6 | CR | Capture Review | 0 | 0 | Informational |
| 7 | RS | Results Summary | 0 | 0 | View and save |

Actual screen ids, durations, and modal flags are defined in the screen
definition CSV and protocol config.

---

## 9. Completion Detection

Each screen has a `completion_action` defined in its config. `AppController`
wires the corresponding screen widget to the completion handler. When fired:

1. Set `status = 'complete'`, `actual_end` in `test_screens`
2. Set `late_flag = 1` if timeout had already expired
3. Sound confirmation tone
4. Advance to the screen defined by `next`

---

## 10. Decisions

| Decision | Resolution |
|----------|------------|
| Concurrent tests | Not supported; one test at a time via `active_slot` flag |
| Scheduling unit | Screen (not phase + period); one duration + one modal flag per screen |
| Status field | 4-value enum per screen (`pending/active/complete/skipped`) + `late_flag` |
| Timeout behavior | Alarm fires; modal screens wait for user; non-modal screens take focus |
| Overdue deque | Removed; not needed with single-test constraint |
| Conflict scheduler | Removed; replaced by single `active_slot` flag check |
| Footer widget | Standard 3-zone footer (menu, clock, next); no overdue variant needed |
| Audio | Alarm fires on timeout; silenced by begin-action `alarm_off` on next screen |
| Skip | Allowed; flags result `degraded`; button on target screen |
