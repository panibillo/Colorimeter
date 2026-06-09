# Colorimeter — Terminology Reference

Canonical definitions for the concepts used throughout design documents,
config files, and code. Update this document when a term is added or
its definition changes.

---

## Test

One complete protocol run for one sample. Occupies one slot (A, B, C, or D).
A test proceeds through a fixed sequence of phases defined by its protocol.
Up to 4 tests may run concurrently; 2 is the expected practical maximum.

---

## Protocol

The ordered sequence of phases that defines a test type. Loaded from YAML
config. Specifies phase names, period structure, durations, and which
periods are modal. The protocol is fixed per test type; individual test
records store the timestamps and outcomes of each phase.

---

## Phase

One named, sequential step in a protocol (e.g. "Incubation", "Measurement",
"Sample Entry"). A test's phases are ordered; each phase completes before
the next begins.

**One phase = one screen.** Phase is the protocol concept; screen is the
UI concept for the same unit. The words are interchangeable when the
distinction does not matter.

A phase may be **repeatable** — some protocols have multiple incubation
rounds whose phase definitions are templated from a single config entry.

---

## Screen

The UI view displayed while a phase is active. Because one phase = one
screen, switching phases and switching screens are the same event.

---

## Period

A time window within a phase. Phases consist of one or more periods that
run sequentially. Each period is either **modal** or **non-modal**.

A phase may have any number of periods. There is no constraint on where
modal periods appear within a phase.

---

## Modal Period  (= Focus Window)

These two terms name the **same concept**.

A period during which the user must perform a specific action. The UI
behaves like a modal dialog: the user **cannot navigate away** from the
current screen while a modal period is active. The three exits from a
modal period are:

1. **Completion** — user performs the required action. Normal flow.
2. **Overdue** — the deadline passes without completion. The system
   automatically moves the task to the overdue stack and releases
   the modal lock, allowing navigation. An alert sounds.
3. **User override** — (TBD) user may have the option to manually
   release the modal lock before the deadline, with a warning.

**Conflict scheduling:** The scheduler ensures that no two simultaneously
running tests have overlapping modal periods. Before a new test may start,
its projected modal period times are compared against the modal periods of
all active tests. If any overlap is found, the Start button is disabled
until a conflict-free start time is available.

---

## Non-Modal Period

A period during which no user action is required. The user is free to
navigate between active test slots or attend to other tasks. Timers
and automated processes (e.g. camera capture) may run during non-modal
periods.

---

## Overdue

A modal period whose deadline has passed without completion. When a period
becomes overdue:

- The modal lock is released (user may now navigate freely)
- The task is pushed onto the overdue stack
- An audible alert fires and repeats at a configured cadence
- A visual indicator replaces the normal status bar footer

The task remains overdue until the user completes or skips it.

---

## Overdue Stack

An in-memory sorted collection (Python `deque`) of overdue tasks across
all active tests. Sorted by deadline (earliest deadline = highest priority
= position 0). Rebuilt from the database on boot if incomplete tests exist.

The overdue stack is the in-memory working view. The database `test_phases`
table is the ground truth (status column: `pending / active / overdue /
complete / skipped`).

See [PHASE_SCHEDULING_DESIGN.md](PHASE_SCHEDULING_DESIGN.md) for the full
overdue state machine and footer widget behavior.

---

## Slot

The label (A, B, C, D) identifying which of the four concurrent test
positions a test occupies. Each slot has a fixed color used consistently
across the touchscreen and web interfaces:

| Slot | Color |
|---|---|
| A | Blue |
| B | Gold / Amber |
| C | Green |
| D | Red / Coral |

---

## Summary table

| Term | Scope | Protocol or UI |
|---|---|---|
| Test | Full run, one sample | Both |
| Protocol | Ordered phase definitions | Protocol |
| Phase | One named step | Protocol |
| Screen | UI view for one phase | UI |
| Period | Time window within a phase | Both |
| Modal period / Focus window | Period requiring user action; locks navigation | Both |
| Non-modal period | Waiting/informational period; navigation free | Both |
| Overdue | Modal period past its deadline | Both |
| Overdue stack | Live sorted list of overdue tasks | Runtime |
| Slot | A / B / C / D test position | Both |
