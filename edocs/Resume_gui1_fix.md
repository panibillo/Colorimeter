# Resume: GUI screen-definition consolidation

## Where this stands

`src/widget_defs.csv` was found deleted in the working tree (accidentally, outside
this thread) and has been **restored from HEAD** (`git checkout -- src/widget_defs.csv`).
It is clean now. `src/widget_defs2.csv` is untracked and was **not** touched — it
looks like your working draft for expanded screens (see below) and is still there.

No code changes have been made yet. Everything below is discussion + an agreed
plan that was interrupted before implementation started.

## The original problem

Screen/widget definitions are maintained in two places that must stay in sync:

- `src/widget_defs.csv`, `src/screen_defs.csv`, `src/screen_widgets.csv` — the
  intended single source of truth, loaded via `csv_loader.py` → `csv_to_widgets.py`.
- `src/app_controller.py` (lines ~71–92) — a block of Python constants
  (`_SE_FIELD_WIDGETS`, `_CLOCK_WIDGET`, `_COUNTDOWN_WIDGET`, `_CA_START_BTN`,
  `_CA_STATUS_1/2`) that re-declares which numeric widget_id plays which role,
  duplicating information already present (but unparsed) in the CSV `remarks`
  column.

Widgets are named by numeric id (`Widget.name = widget_id`) because a prior
attempt to look widgets up by semantic name (`find("clock")`, `find("id_field")`)
silently failed — `Screen.find()` returns `None` on a miss rather than raising,
so the clock just stopped ticking with no error. `test/test_app_wiring.py`'s
docstring documents this history. Any fix must not reintroduce that failure mode.

## Agreed direction: Option A

Promote the CSV `remarks` column to a parsed, identifier-valued `role` column,
and add `Screen.find_role(role)` plus a boot-time "required roles present" check
in `AppController`, so a missing/renamed role fails loudly at startup instead of
silently at runtime. Considered and set aside: a `role: str = "..."`+`action`
column pointing at controller methods (adds a second namespace only a Python
reader can validate — not justified yet), and CSV→constants codegen (adds a
build step for no real gain).

### Step sequence agreed (none started)

0. Baseline: `MPLBACKEND=Agg .venv/bin/python -m unittest test.test_app_wiring`
   → 7 pass. (`python` is not on PATH; use `.venv/bin/python`.)
1. Rename `remarks` → `role` in `widget_defs.csv`; give every looked-up widget
   an identifier-style value (table of proposed names was drafted — see chat
   history or redo). Inert — nothing parses this column yet. Leave
   `screen_widgets.csv`'s `remark` column alone; it's a different, per-line
   thing.
2. Companion cleanup first: collapse the two hand-copied 16-field `Widget`
   blocks in `csv_loader.wire_widgets_to_screens` (~lines 318–368) to
   `dataclasses.replace(widget, tab_order=...)`. Makes step 3 a one-line change
   instead of four sites.
3. Add `role: str = ""` to `csv_loader.Widget`; parse it in `widget_factory`.
4. Validate role uniqueness **per screen** (not global) in
   `wire_widgets_to_screens` — 902/904 legitimately repeat across screens.
5. Add `role` to `widgets.Widget` and thread it through Label/Button/TextBox/
   RectWidget construction in `csv_to_widgets.csv_widget_to_widget`. Leave
   `name=widget_id` as-is — the mock display's click mapping keys off it.
6. Add `Screen.find_role()`. Add a `_REQUIRED_ROLES` contract dict in
   `AppController`, validated once at boot (raise if missing) — deliberately
   *not* raising from the per-tick clock lookup, since a runtime crash mid-
   measurement is worse than the old silent stall. Delete the constant block
   in `app_controller.py`; rewrite the ~6 call sites to `find_role(...)`.
   `_FIELD_KEYS` stays (it's the data model, not a widget lookup).
7. Rewrite `test/test_app_wiring.py` against roles (same commit as step 6 —
   it asserts on constants step 6 deletes). Update its docstring: semantic
   names are now the mechanism, not the bug.
8. Verify in the simulator (`COLORIMETER_SIMULATION=1 .venv/bin/python
   src/main.py`): clock ticks, SE fields populate on entry, capture status
   lines paint. Tests alone won't catch the old symptom.

## Why we're pausing here

`src/widget_defs2.csv` (untracked, still on disk) shows screens beyond SE/CA
already being drafted: CB (311–314), CC (411–414), and CD-ish sub-states
(513/514, 523, 533/534) for a multi-reagent capture/analyze/done flow. Each
new screen repeats the same block shape as CA (countdown + start/capture
button + two instruction labels) with new numeric ids.

That's a **second, larger** duplication pattern than the one Option A fixes:
Option A removes app_controller's copy of the id→role mapping, but doesn't
address near-identical widget blocks being copy-pasted across screens in the
CSV itself (211–214 → 311–314 → 411–414, same shape, new numbers each time).
Roles like `countdown`/`status_1`/`status_2` would need to work the same way
on CB/CC/CD as they do on CA — which the per-screen uniqueness rule (step 4)
already accommodates — but it's worth deciding, before writing more screens
by hand, whether there should be some kind of block/template mechanism for
"a reagent step screen" rather than continuing to hand-copy id ranges (100s,
200s, 300s, 400s, 500s) per screen.

**Next session: reconsider the interface/screen-definition organization in
light of the multi-reagent screen expansion before implementing Option A**,
since the answer may change what "role" should mean (e.g. a role scoped to a
repeated block, not just a global-per-screen identifier) or introduce a
templating layer between "reagent screen shape" and the flat CSV rows.
