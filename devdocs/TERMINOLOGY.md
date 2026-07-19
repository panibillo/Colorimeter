# Colorimeter — Terminology Reference

Canonical definitions for the concepts used throughout design documents,
config files, and code. Update this document when a term is added or
its definition changes.

---

## Test

One complete protocol run for one sample. Occupies one slot (A or B).
Only one test may run at a time; a second test cannot begin until the
active test completes.

---

## Protocol

The ordered sequence of screens (phases) that defines a test type.
Specifies screen ids, durations, modal flags, and begin-actions.
The protocol is fixed per test type; individual test records store
the timestamps and outcomes of each screen.

---

## Phase

One named, sequential step in a protocol (e.g. "Incubation",
"Measurement", "Sample Entry"). Phases are ordered; each completes
before the next begins.

**One phase = one screen.** Phase is the protocol concept; screen is the
UI concept. The words are interchangeable when the distinction does not matter.

---

## Screen

The UI view displayed while a phase is active. Switching phases and
switching screens are the same event.

Each screen has:
- **`id`** — 2-character hardcoded identifier (e.g. `MM` = main menu)
- **`phase`** — phase_id if this screen is part of a timed sequence; `0` if not
- **`seq`** — ordering within the phase sequence (1, 2, 3 …); `0` if not phased
- **`tabbed_widgets`** — ordered list of widgets in their tab/focus order
- **`untabbed_widgets`** — non-focusable display widgets (labels, decorations)
- **`duration`** — seconds the screen is timed for; `0` = no time limit
- **`modal`** — `1` if navigation is locked while this screen is active; `0` if free
- **`beginactions`** — ordered list of actions taken when the screen's timer starts
  (e.g. `alarm_on`, `alarm_off`, `start_timer`)
- **`next`** — screen `id` to navigate to when the NEXT button is pressed
- **`is_visible`** — True when this screen has focus (is the displayed screen)
- **`is_alive`** — True when the screen is being monitored for timeout; a screen
  with a non-zero duration stays alive from when its timer starts until the test
  ends, and automatically takes focus when its duration expires

---

## Modal Screen

A screen with `modal = 1`. The user **cannot navigate away** while it is active.
Exits from a modal screen:

1. **Completion** — user performs the required action and presses NEXT. Normal flow.
2. **Timeout** — the duration expires. The system sounds an alarm and may auto-advance.
3. **Skip** — user explicitly skips (marks result degraded).

---

## Non-Modal Screen

A screen with `modal = 0`. The user is free to navigate to other screens.
The screen's timer continues running in the background (`is_alive = True`).
When the duration expires the screen takes focus — it does not interrupt
the user mid-action, it waits for the next navigation event or fires an alarm.

---

## Slot

The label (A or B) identifying which of the two test positions a test occupies.
Each slot has a fixed color used consistently across all interfaces:

| Slot | Color |
|------|-------|
| A | Blue |
| B | Gold / Amber |

---

## Widget

A reusable UI element. Each widget is a **singleton**: widget `TX` (for example)
is one Python object that may appear on multiple screens and always holds the
same value. This eliminates explicit data-sync on screen navigation.

Widget types include: Button, TextBox, Timer, Label, RectWidget.

Each widget is identified by a hardcoded 3-digit integer `widget_id` (100–999).
Visual properties (window rect, label, fg/bg colors, default value) are loaded
from a CSV file at startup. Behavioral wiring (callbacks, data bindings) is
assigned in Python after loading.

---

## Summary table

| Term | Scope | Protocol or UI |
|------|-------|----------------|
| Test | Full run, one sample | Both |
| Protocol | Ordered screen definitions | Protocol |
| Phase | One named step | Protocol |
| Screen | UI view for one phase; owns duration + modal flag | Both |
| Modal screen | Screen that locks navigation while active | Both |
| Non-modal screen | Screen that allows free navigation while its timer runs | Both |
| Slot | A / B test position | Both |
| Widget | Singleton UI element with hardcoded id | UI |
| is_alive | Screen is being monitored for timeout | Runtime |
| is_visible | Screen currently has focus | Runtime |
