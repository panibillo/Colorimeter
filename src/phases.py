"""
phases.py  —  The protocol as stateless phase strategies.
---------------------------------------------------------
Each phase is a single object (not one per slot) that knows how to render
itself for whatever slot it is handed, and how its buttons behave.  Slots hold
the state; phases hold the behaviour.  Phases differing only by data are one
class instantiated twice (the two ReagentPhases).

A phase implements:

    enter(pool, slot, arb)   configure the pool to display this phase for this
                             slot: set text, enable, show, wire buttons to this
                             phase's own methods.  Pure view + wiring; called on
                             every render; idempotent.  Speaks only in widget
                             names, text, and palette — never pixels.

    activate(arb, slot)      run once when a slot arrives at this phase; may
                             mutate slot state and touch hardware via the
                             arbiter (start a timer, kick off analysis).
                             Default: no-op.

    on_expire(arb, slot)     the slot's countdown reached zero (called by the
                             arbiter's poll after it alarms).  Default: no-op.

    prior / next             adjacent phase ids for the footer, or None.

Buttons are wired in enter() to closures capturing (arb, slot, pool), so a
phase's button behaviour lives with the rest of that phase.

Arbiter contract (what phases call; arbiter.py implements it)
-------------------------------------------------------------
    arb.now() -> float              monotonic time (for creating timers)
    arb.timestamp() -> str          wall-clock string for the collect stamp
    arb.read_temperature()->float   sample the DS18B20 (°C), or None on failure
    arb.temperature_attached()->bool  is a DS18B20 on the bus at all
    arb.reagent2_plan(slot)         (secs, temp_c, measured) for this slot's
                                    reagent-2 countdown — see Reagent2Phase
    arb.reagent_weigh_plan(slot, n) ReagentWeights(initial, final, net, target,
                                    overage, tol) for the weight grid's row n
    arb.reagent_finals_complete(slot) -> bool  both final weighings entered
    arb.image_reps(slot)            image sets taken at each imaging step (1..5)
    arb.reagent_time_factor(slot, n) multiplier on reagent `n`'s countdown
                                    (1.0 unless a test variant says otherwise)
    arb.reagent1_stir_seconds()     fixed stir duration for reagent 1
    arb.reagent2_stir_plan(slot)    temperature-compensated stir duration
                                    for reagent 2
    arb.goto(slot, phase_id)        advance slot to a phase — the live phase
                                    (runs activate, renders)
    arb.view(slot, phase_id)        preview a phase without advancing (no activate)
    arb.resume(slot)                return the view to the slot's live phase
    arb.render(slot)                re-render slot's current phase in place
    arb.request_capture(slot)->bool start a capture if the camera is free
    arb.capture_available()->bool   is the (single) camera free right now
    arb.cap_mode -> str              "calibration" | "normal" (Menu -> Advanced
                                     -> Capture selection)
    arb.open_modal(slot, modal_id)  push a modal, remembering the return phase
    arb.close_modal(slot)           pop back to the remembered phase
    arb.cancel_test(slot)           abandon this run
    arb.equipment_list()->list      the test-variant Equip choices (labels)
    arb.latest_treatment()          the newest treatments row as a dict, or None
    arb.save_treatment(slot)        write the slot's treatments row
    arb.reset_timer(slot)           clear the countdown, back to armed
    arb.retry_capture(slot)         re-attempt a capture after 'no sample'
    arb.start_analysis(slot)        begin analysis of the captured images
    arb.post_results(slot)          upload the result
    arb.next_sample(slot)           advance the id and start a fresh run
"""

from __future__ import annotations

import collections
import logging
from typing import Optional

from timer import TimerState

log = logging.getLogger(__name__)


# Ordered protocol steps, used to place the reagent/capture status chips
# relative to where the run actually is (slot.active_phase).  "preimage" is in
# the sequence whether or not the chain includes it: it only has to sort in the
# right place for the chips, and an id that never comes up costs nothing.
_STEP_SEQUENCE = ("entry", "entry2", "variant", "vent", "preimage",
                  "reagent1", "reagent2", "read", "analysis")
_STEP_POS = {pid: i for i, pid in enumerate(_STEP_SEQUENCE)}

# Font size of the timer label ("Remaining"/"Elapsed"/"Final"), set per phase
# because the one widget (timer_lbl) is shared. Base size is 22 (pool.build).
_TIMER_LBL_SIZE_REAGENT  = 13   # reagents "Remaining" — ~60% of base
_TIMER_LBL_SIZE_READ     = 18   # read "Elapsed"       — ~80% of base
_TIMER_LBL_SIZE_ANALYSIS = 22   # analysis "Final"     — base

# RUN button font size.  The button is shared, and the phases that need a
# two-line label shrink it, so every phase must state which it wants or it
# inherits whatever the last screen chose.
_RUN_BTN_SIZE       = 24   # as built in pool.build — one short word
_RUN_BTN_SIZE_2LINE = 18   # "Pre-image\nA", "Capture\nA", "ANALYZE"

# Instruction-line font size (instr1 as built in pool.build).  Named so
# _line() can restore it: the widgets are shared between phases, so a size set
# for one state would otherwise persist into every later one.
_INSTR_SIZE = 16

# The reagent weight grid, one tuple per row: (row label, initial box, final
# box, over cell, target cell).  Indexed by reagent number - 1.
_WT_ROWS = (
    ("wt1_row_lbl", "wt1_init_fld", "wt1_final_fld", "wt1_over_lbl", "wt1_targ_lbl"),
    ("wt2_row_lbl", "wt2_init_fld", "wt2_final_fld", "wt2_over_lbl", "wt2_targ_lbl"),
)
_WT_HEADINGS = ("wt_hdr_init", "wt_hdr_final", "wt_hdr_over", "wt_hdr_targ")

# What the grid needs to know about one reagent's weights, as returned by
# arb.reagent_weigh_plan().  Defined here rather than in the arbiter because
# the dependency runs one way — arbiter.py imports phases.py — so this is the
# only place both the real arbiter and the self-test's mock can share it.
# A named tuple rather than four positional values because `net` deliberately
# means two things (packet contents before the pour, amount poured after it),
# and a caller reading `w.net` cannot mistake it for a field it is not.
ReagentWeights = collections.namedtuple(
    "ReagentWeights", "initial final net target overage tol")

# The three progress chips shown in the reagent/capture phases:
# (widget name, the step's phase id, chip text).
_STEP_INDICATORS = (
    ("chip_a", "reagent1", "1"),
    ("chip_b", "reagent2", "2"),
    ("chip_c", "read",     "Capture"),
)


def _mmss(secs: int) -> str:
    """
    Seconds as m:ss, so a duration reads as the wait it is.

    Lives here rather than in the arbiter because the reagent phases are what
    put a duration in front of an operator; the arbiter's phases screen imports
    it so both spell a countdown the same way.

    Example:
        >>> _mmss(720)
        '12:00'
    """
    return f"{secs // 60}:{secs % 60:02d}"


def _line(pool, name: str, text: str, yellow: bool = False,
          size: Optional[int] = None) -> None:
    """
    Set an instruction line (instr1/2/3) with explicit colour and optional size.

    Enforces colour consistency: every render must explicitly set yellow or white,
    or a yellow line can leak into a later state that should be plain.  Size
    behaves the same way: omitting it restores the default rather than leaving
    whatever the last state chose.
    """
    pool.put(name, text)
    pool.recolor(name, bg=("field_yellow" if yellow else "white"))
    pool[name].size = _INSTR_SIZE if size is None else size


# ─────────────────────────────────────────────────────────────────────────────
# Base
# ─────────────────────────────────────────────────────────────────────────────

class Phase:
    """Base phase.  Subclasses set phase_id/prior/next and implement enter()."""

    phase_id: str = ""
    prior:    Optional[str] = None
    next:     Optional[str] = None
    title:    str = ""          # header title; "" -> use the slot's id

    def activate(self, arb, slot) -> None:
        """Run once on arrival at this phase.  Default: nothing."""

    def enter(self, pool, slot, arb) -> None:
        raise NotImplementedError(f"{type(self).__name__}.enter() not implemented")

    def on_expire(self, arb, slot) -> None:
        """The slot's countdown expired.  Default: nothing."""

    def _show_collect(self, pool, slot) -> None:
        """Keep the collect stamp in view once a run is under way (post-entry)."""
        ts = slot.record.get("collect_dt", "")
        if ts:
            pool.put("collect_line", f"Collected  {ts}")

    def _show_steps(self, pool, slot) -> None:
        """
        Show the reagent/capture progress chips (1 / 2 / Capture), colour-coded
        by the run's live progress (slot.active_phase, not whatever is previewed):

            before the step  → step_pending  (dim RUN green — not yet available)
            at the step, idle → start_green   (the RUN colour — ready to run)
            at the step, busy → step_running  (amber — counting down / capturing)
            past the step     → step_done     (dim amber — complete)
        """
        active = _STEP_POS.get(slot.active_phase, -1)
        for name, step_id, text in _STEP_INDICATORS:
            step = _STEP_POS[step_id]
            if active < step:
                tint = "step_pending"
            elif active > step:
                tint = "step_done"
            else:
                busy = slot.capturing if step_id == "read" else slot.running
                tint = "step_running" if busy else "start_green"
            pool.recolor(name, bg=tint)
            pool.put(name, text)

    def _show_weights(self, pool, slot, arb) -> None:
        """
        The reagent weight grid: two fixed rows, four cells each.

        Position never carries meaning here — row 1 is reagent 1 and column 2
        is the final weighing on every screen and in every run state.  Only the
        values and the colours change, which is what the grid replaced: the old
        single column relabelled itself Initial/Final as timers started, so the
        same pixel meant four things over a run.

        Both entry boxes stay editable throughout, so a packet can be re-weighed
        or a typo corrected at any point.

        Colour, per cell:

            Initial   empty                    -> yellow  (a weighing is owed)
                      short of target past tol -> amber   (packet holds too little)
            Final     owed and empty           -> yellow
            Over      short past tol           -> black on red
                      under target within tol  -> red on white
                      over  target within tol  -> blue on white
                      over  target past tol    -> blue on amber

        Every cell's colour is set on every call.  A full render leaves the last
        render's colour in place, so a rule that only paints its own case would
        strand an old tint on a cell that has since gone back to normal — the
        same trap _line()'s docstring describes for the instruction lines.
        """
        # Both final weighings come due when reagent 2 starts: that is the last
        # moment the operator is not yet busy with the strip, and it leaves the
        # whole reagent-2 countdown to get them entered.
        finals_due = bool(slot.record.get("reagent2_start"))
        pool.show(*_WT_HEADINGS)

        for n in (1, 2):
            row_lbl, init_fld, final_fld, over_lbl, targ_lbl = _WT_ROWS[n - 1]
            w = arb.reagent_weigh_plan(slot, n)
            short = w.overage is not None and w.overage < -w.tol

            pool.put(row_lbl, f"R{n}")

            pool.put(init_fld, slot.record.get(f"reagent{n}_initial_wt", ""))
            pool.recolor(init_fld, fg="black",
                         bg=("wt_missing" if w.initial is None else
                             "wt_warn" if short else "white"))

            pool.put(final_fld, slot.record.get(f"reagent{n}_final_wt", ""))
            pool.recolor(final_fld, fg="black",
                         bg=("wt_missing" if finals_due and w.final is None
                             else "white"))

            if w.overage is None:
                over_fg, over_bg = "black", "white"
            elif short:
                over_fg, over_bg = "black", "wt_alarm"
            elif w.overage < 0:
                over_fg, over_bg = "wt_under", "white"
            elif w.overage <= w.tol:
                over_fg, over_bg = "wt_over", "white"
            else:
                over_fg, over_bg = "wt_over", "wt_warn"
            pool.put(over_lbl,
                     "" if w.overage is None else f"{w.overage:+.3f}")
            pool.recolor(over_lbl, fg=over_fg, bg=over_bg)

            pool.put(targ_lbl, "" if w.target is None else f"{w.target:.3f}")
            pool.recolor(targ_lbl, fg="black", bg="white")

    # ── shared entry-row + header/footer chrome ────────────────────────────────

    def _row(self, pool, lbl_name: str, lbl_text: str, fld_name: str, value,
             on=None, enabled: bool = True) -> None:
        """
        One label/field row of an entry screen.

        `on` is the label button's action — pressing a row label loads that
        field's default value.  It is *cleared* rather than merely left unset
        when the row is disabled, because Pool.put ignores on=None and a stale
        handler from the last phase to draw this widget would otherwise survive.
        """
        pool.put(lbl_name, lbl_text, enabled=enabled)
        pool.on(lbl_name, on if enabled else None)
        pool.put(fld_name, value, enabled=enabled)

    def _chrome(self, pool, slot, arb, main_label: str, main_on,
                main_enabled: bool = True, lock_nav: bool = False,
                title: str = None) -> None:
        """
        Title, the header main button, the slot toggle, and the footer.

        PRIOR/NEXT *preview* the neighbouring steps (arb.view) without advancing
        the test.  While previewing (viewed phase != live phase) the header main
        button becomes Resume — a jump back to the live phase — and this phase's
        own action (run_btn) is disabled so a preview can't start or cancel
        anything.

        lock_nav can still hard-disable PRIOR/NEXT, but nothing uses it now that
        running countdowns and in-flight captures are safe to leave (the arbiter
        keeps them alive in the background and Resume returns to them); it stays
        for any future phase that genuinely must trap the user.  The slot toggle's
        label and colour are the arbiter's concern (they depend on focus), so it
        is only shown here.

        `title` overrides the phase's own: the entry screen names itself until a
        sample number is claimed, and shows that number afterwards.
        """
        if slot.phase != slot.active_phase:                 # previewing another step
            main_label, main_enabled = "Resume", True
            main_on  = lambda: arb.resume(slot)
            lock_nav = False                                # previewing is safe
            pool.enable("run_btn", False)

        if title is None:
            title = self.title or slot.record.get("samp_no", "")
        pool.put("title", title)
        pool.put("main_btn", main_label, on=main_on, enabled=main_enabled)
        pool.show("slot_btn")

        can_prior = bool(self.prior) and not lock_nav
        can_next  = bool(self.next)  and not lock_nav
        pool.put("prior_btn", "PRIOR",
                 on=(lambda: arb.view(slot, self.prior)) if can_prior else None,
                 enabled=can_prior)
        pool.put("next_btn", "NEXT",
                 on=(lambda: arb.view(slot, self.next)) if can_next else None,
                 enabled=can_next)
        pool.show("clock")
        pool.put("menu_btn", "MENU", on=lambda: arb.open_menu(slot))


# ─────────────────────────────────────────────────────────────────────────────
# Entry
# ─────────────────────────────────────────────────────────────────────────────

def _text(v) -> str:
    """Render a stored (possibly numeric/None) field as display text."""
    return "" if v is None else str(v)


def _to_float(s):
    """Parse a numeric entry field to float, or None if blank/invalid."""
    try:
        s = str(s).strip()
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


class EntryPhase(Phase):
    """Sample entry (screen 1): id / user / test / test-lot, then Begin."""

    phase_id = "entry"
    prior    = None
    next     = "entry2"
    title    = "New Test"

    # entry field widget -> record key; the arbiter writes edits straight through
    # to the record on each keystroke, so the record is always the source of truth.
    FIELDS = {"id_fld": "samp_no", "user_fld": "user",
              "test_fld": "test_kit", "test_lot_fld": "test_lot"}

    def enter(self, pool, slot, arb) -> None:
        r = slot.record
        if not r.get("samp_no"):
            self.autofill(arb, slot)      # first view of a fresh run
        claimed = r.get("db_rowid") is not None

        # Exactly one group of rows is editable, and Begin is what flips it.
        # While the number is unclaimed it is the only thing here, because the
        # other slot's ID button can renumber this slot at any moment — so there
        # must be nothing in it worth losing.  Claiming the number locks it and
        # opens the identity fields, which from then on write through to the row.
        self._row(pool, "id_lbl", "ID", "id_fld", r["samp_no"],
                  on=lambda: self.fill_id(arb, slot), enabled=not claimed)
        self._row(pool, "user_lbl", "User", "user_fld", r["user"],
                  on=lambda: self.fill_user(arb, slot), enabled=claimed)
        self._row(pool, "test_lbl", "Test", "test_fld", r["test_kit"],
                  on=lambda: self.fill_test(arb, slot), enabled=claimed)
        self._row(pool, "test_lot_lbl", "Test lot", "test_lot_fld", r["test_lot"],
                  on=lambda: self.fill_test_lot(arb, slot), enabled=claimed)
        pool.show("div1", "div2", "div3")
        if claimed:
            self._chrome(pool, slot, arb, "Continue",
                         main_on=lambda: self.proceed(arb, slot),
                         title=r["samp_no"])
        else:
            self._chrome(pool, slot, arb, "Begin",
                         main_on=lambda: self.begin(arb, slot),
                         main_enabled=slot.ready_to_begin())

    # ── defaults + label-button fills ───────────────────────────────────────────
    # Each row label is a Button: pressing it loads that field's default value.

    def autofill(self, arb, slot) -> None:
        """
        Give a fresh run the sample number it will offer to claim.

        Called on the first view of a run.  The identity fields are deliberately
        left empty until Begin (see claim_defaults): a sample then records the
        defaults as they read at the moment it was claimed, and an unclaimed
        slot shows nothing but the number it is holding.
        """
        slot.record["samp_no"] = arb.next_sample_no(slot)

    def claim_defaults(self, arb, slot) -> None:
        """Fill the identity fields from the defaults, as Begin claims the row."""
        r = slot.record
        d = arb.defaults()
        if d is not None:
            r["user"]          = d.user
            r["test_kit"]      = d.testkit
            r["test_lot"]      = d.testlot
            r["instrument_sn"] = d.hardware_sn

    def clear_identity(self, slot) -> None:
        """Empty the identity fields: an unclaimed slot shows only its number."""
        r = slot.record
        r["user"] = r["test_kit"] = r["test_lot"] = r["instrument_sn"] = ""

    def fill_id(self, arb, slot) -> None:
        if slot.record.get("db_rowid") is not None:
            return                    # claimed — the button is inert anyway
        arb.claim_next_no(slot)
        arb.render(slot)

    def fill_user(self, arb, slot) -> None:
        d = arb.defaults()
        if d is not None:
            slot.record["user"] = d.user
            arb.render(slot)

    def fill_test(self, arb, slot) -> None:
        d = arb.defaults()
        if d is not None:
            slot.record["test_kit"] = d.testkit
            arb.render(slot)

    def fill_test_lot(self, arb, slot) -> None:
        d = arb.defaults()
        if d is not None:
            slot.record["test_lot"] = d.testlot
            arb.render(slot)

    def begin(self, arb, slot) -> None:
        """
        Claim samp_no in the database; on a duplicate, warn instead.

        The view stays on this screen, which now shows the claimed number in the
        header and offers Continue — so the operator sees what was claimed before
        entering anything against it.  The defaults have to reach the record
        first, because begin_sample writes them as the row's identity.
        """
        self.claim_defaults(arb, slot)
        if arb.begin_sample(slot):
            arb.render(slot)              # same screen, now in its claimed state
        else:
            self.clear_identity(slot)     # still unclaimed: show only the number
            arb.open_modal(slot, "dup")

    def proceed(self, arb, slot) -> None:
        """Leave for screen 2; goto() flushes any identity edits on the way out."""
        arb.goto(slot, self.next)


class Entry2Phase(Phase):
    """Sample entry (screen 2): temperature / weight / collect datetime. / remark"""

    phase_id = "entry2"
    prior    = "entry"
    # Whichever step follows entry is decided per build, because the pre-image
    # may or may not be in the chain — build_phases() sets this on the
    # instance.  The class value is the no-pre-image protocol.
    next     = "reagent1"
    title    = ""                 # header shows the sample id
    # Continue goes through the vent reminder, which NEXT does not preview:
    # it is a prompt to act on once, not a step anyone needs to look ahead at.
    proceed_to = "vent"

    FIELDS = {"temp_fld": "samp_degC", "weight_fld": "samp_grams",
              "collectdt_fld": "collect_dt", "remark_fld": "remark"}

    def enter(self, pool, slot, arb) -> None:
        r = slot.record
        # Unclaimed, this screen is only reachable as a NEXT preview from screen
        # 1 — visible, so the operator can see what is coming, but with nothing
        # to type into until the slot owns a row to put it in.
        claimed = r.get("db_rowid") is not None
        # Temp label reads the DS18B20; Weight label fills in the configured
        # nominal sample weight; Collect label stamps the current time.
        self._row(pool, "temp_lbl", self._temp_label(arb),
                  "temp_fld", _text(r.get("samp_degC")),
                  on=lambda: self.fill_temp(arb, slot), enabled=claimed)
        self._row(pool, "weight_lbl", "Weight g",
                  "weight_fld", _text(r.get("samp_grams")),
                  on=lambda: self.fill_weight(arb, slot), enabled=claimed)
        self._row(pool, "collectdt_lbl", "Collect",
                  "collectdt_fld", r.get("collect_dt") or "",
                  on=lambda: self.fill_collect(arb, slot), enabled=claimed)
        self._row(pool, "remark_lbl", "Remark",
                  "remark_fld", r.get("remark") or "",
                  on=lambda: self.fill_remark(arb, slot), enabled=claimed)
        pool.show("div1", "div2", "div3")
        self._chrome(pool, slot, arb, "Continue",
                     main_on=lambda: self.proceed(arb, slot))

    def autofill(self, arb, slot) -> None:
        """
        Seed selected fields of a fresh run from the loaded defaults.

        Called on the first view of a run, and again by the arbiter whenever a
        config edit moves the defaults out from under a slot that has not begun.
        """
        r = slot.record
        d = arb.defaults()
        if d is not None:
            r["remark"]          = d.remark

    def _default_temp(self, arb) -> Optional[float]:
        """The configured stand-in temperature (defaults.default_temperature)."""
        d = arb.defaults()
        return None if d is None else d.default_temperature

    def _temp_label(self, arb) -> str:
        """
        Text for the Temp button: the plain label when a probe is on the bus,
        and the stand-in value in brackets when there is none, so the operator
        can see what one press is about to write before pressing it.
        """
        if arb.temperature_attached():
            return "Temp °C"
        t = self._default_temp(arb)
        return "Temp °C" if t is None else f"[{t:.1f} °C]"

    def fill_temp(self, arb, slot) -> None:
        """
        Fill the Temp field in one press: the sensor's reading, or — on a unit
        with no probe attached — the configured default temperature.

        The read is attempted first either way, because the driver re-probes
        the bus and so picks up a sensor plugged in after start.
        """
        c = arb.read_temperature()
        if c is None and not arb.temperature_attached():
            c = self._default_temp(arb)
        # samp_degC is a string here, exactly as a typed entry would leave it
        # (proceed() converts it with _to_float); "err" marks a failed read of
        # a probe that IS attached, so the operator sees the press registered
        # but the sensor did not answer.
        slot.record["samp_degC"] = "err" if c is None else f"{c:.1f}"
        arb.render(slot)

    def fill_weight(self, arb, slot) -> None:
        """
        Fill the Weight field with the configured nominal sample weight.

        Almost every run measures out the same nominal volume of sample, so
        one press saves retyping the same number; the reagent weigh targets
        scale from whatever ends up here (see Arbiter.reagent_weigh_plan).
        Stored as text, exactly as a typed entry would leave it — proceed()
        converts it with _to_float.
        """
        d = arb.defaults()
        if d is not None:
            slot.record["samp_grams"] = d.default_sample_wt
            arb.render(slot)

    def fill_collect(self, arb, slot) -> None:
        slot.record["collect_dt"] = arb.timestamp()
        arb.render(slot)

    def fill_remark(self, arb, slot) -> None:
        d = arb.defaults()
        if d is not None:
            slot.record["remark"] = d.remark
            arb.render(slot)


    def proceed(self, arb, slot) -> None:
        r = slot.record
        if not r.get("collect_dt"):
            r["collect_dt"] = arb.timestamp()
        arb.persist(slot, collect_dt=r["collect_dt"],
                    samp_degC=_to_float(r.get("samp_degC")),
                    samp_grams=_to_float(r.get("samp_grams")),
                    remark=r.get("remark"))
        arb.goto(slot, self.proceed_to)


# ─────────────────────────────────────────────────────────────────────────────
# Test variant  (development runs only: protocol.test_variant = 1)
# ─────────────────────────────────────────────────────────────────────────────

_MAX_IMAGE_REPS = 5

# The four factor boxes: (row label button, wt box, time box, reagent number).
_VAR_FACTOR_ROWS = (
    ("var_r1_lbl", "var_r1_wt_fld", "var_r1_time_fld", 1),
    ("var_r2_lbl", "var_r2_wt_fld", "var_r2_time_fld", 2),
)
_VAR_CAPTIONS = ("var_r1_wt_cap", "var_r1_time_cap",
                 "var_r2_wt_cap", "var_r2_time_cap")


def _image_reps(v) -> int:
    """A stored image_reps as a usable count, 1.._MAX_IMAGE_REPS (1 if unset)."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 1
    return min(max(n, 1), _MAX_IMAGE_REPS)


def _factor_text(v) -> str:
    """A stored factor as box text: 1.5 -> '1.5', missing -> '1.0'."""
    return "1.0" if v is None or v == "" else str(v)


def _factor_ok(s) -> bool:
    """A factor box holds a usable multiplier: a number greater than zero."""
    v = _to_float(s)
    return v is not None and v > 0


def _factor_value(s) -> float:
    """
    A stored factor as the multiplier to apply: 1.0 unless it is usable.

    The variant screen will not let a run past it with a bad factor, so the
    1.0 fallback only answers for a production run (defaults) or a record that
    never went through the screen.
    """
    return _to_float(s) if _factor_ok(s) else 1.0


class VariantPhase(Phase):
    """
    The deliberate variations a development run is made with: which test
    equipment, how many image sets to take, and multipliers on each reagent's
    target weight and countdown.

    In the chain only when protocol.test_variant is on (see build_phases());
    a production run never sees it and keeps every default.  Continue writes
    the choices to the treatments table and goes on to the vent reminder.

    A new run starts from the previous sample's treatment, because development
    runs come in series that mostly repeat the last settings.  The values are
    editable only while the run is actually here: once the reagents have used
    the factors, a look back from a later step must not change them.
    """

    phase_id   = "variant"
    prior      = "entry2"
    next       = "reagent1"         # set per build — the step after vent
    title      = ""                 # header shows the sample id
    proceed_to = "vent"

    FIELDS = {"var_r1_wt_fld": "R1_wt_factor", "var_r1_time_fld": "R1_time_factor",
              "var_r2_wt_fld": "R2_wt_factor", "var_r2_time_fld": "R2_time_factor"}

    def enter(self, pool, slot, arb) -> None:
        r = slot.record
        if not r.get("test_equip"):
            self.carry_over(arb, slot)      # first view of this run
        live = slot.active_phase == self.phase_id

        self._row(pool, "var_equip_lbl", "Equip", "var_equip_btn",
                  r.get("test_equip") or "",
                  on=lambda: self.reset_equip(arb, slot), enabled=live)
        pool.on("var_equip_btn",
                (lambda: self.step_equip(arb, slot)) if live else None)
        self._row(pool, "var_reps_lbl", "Image\nreps", "var_reps_btn",
                  str(r.get("image_reps") or 1),
                  on=lambda: self.reset_reps(arb, slot), enabled=live)
        pool.on("var_reps_btn",
                (lambda: self.step_reps(arb, slot)) if live else None)

        for lbl, wt_fld, time_fld, n in _VAR_FACTOR_ROWS:
            pool.put(lbl, f"Reagent {n}", enabled=live)
            pool.on(lbl, (lambda n=n: self.reset_factors(arb, slot, n))
                    if live else None)
            for fld in (wt_fld, time_fld):
                value = r.get(self.FIELDS[fld], "")
                pool.put(fld, value, enabled=live)
                # Yellow marks a box that is holding Continue back.
                pool.recolor(fld, bg="white" if _factor_ok(value)
                             else "wt_missing")
        pool.show(*_VAR_CAPTIONS)
        pool.show("div1", "div2", "div3")
        self._chrome(pool, slot, arb, "Continue",
                     main_on=lambda: self.proceed(arb, slot),
                     main_enabled=self.ready(slot))

    def ready(self, slot) -> bool:
        """Every factor box holds a number greater than zero."""
        return all(_factor_ok(slot.record.get(k)) for k in self.FIELDS.values())

    # ── filling and stepping ────────────────────────────────────────────────────

    def carry_over(self, arb, slot) -> None:
        """
        Start this run's choices from the previous treatment, or from the
        defaults when there is none: the first equipment, one image set, every
        factor 1.0.
        """
        r = slot.record
        equip = arb.equipment_list()
        t = arb.latest_treatment() or {}
        r["test_equip"] = t.get("test_equip") or (equip[0] if equip else "")
        try:
            reps = int(t.get("image_reps") or 1)
        except (TypeError, ValueError):
            reps = 1
        r["image_reps"] = min(max(reps, 1), _MAX_IMAGE_REPS)
        for key in self.FIELDS.values():
            r[key] = _factor_text(t.get(key))

    def step_equip(self, arb, slot) -> None:
        """A tap on the value: the next equipment on the list, wrapping."""
        equip = arb.equipment_list()
        if not equip:
            return
        cur = slot.record.get("test_equip")
        i = equip.index(cur) if cur in equip else -1
        slot.record["test_equip"] = equip[(i + 1) % len(equip)]
        arb.render(slot)

    def reset_equip(self, arb, slot) -> None:
        equip = arb.equipment_list()
        if equip:
            slot.record["test_equip"] = equip[0]
            arb.render(slot)

    def step_reps(self, arb, slot) -> None:
        """A tap on the value: 1 -> 2 -> ... -> 5 -> 1."""
        reps = _image_reps(slot.record.get("image_reps"))
        slot.record["image_reps"] = reps % _MAX_IMAGE_REPS + 1
        arb.render(slot)

    def reset_reps(self, arb, slot) -> None:
        slot.record["image_reps"] = 1
        arb.render(slot)

    def reset_factors(self, arb, slot, n: int) -> None:
        """A reagent's row label: both its factors back to 1.0."""
        slot.record[f"R{n}_wt_factor"] = "1.0"
        slot.record[f"R{n}_time_factor"] = "1.0"
        arb.render(slot)

    def proceed(self, arb, slot) -> None:
        if not self.ready(slot):
            return
        arb.save_treatment(slot)
        arb.goto(slot, self.proceed_to)


# ─────────────────────────────────────────────────────────────────────────────
# Vent reminder  (a plain red screen before any reagent is handled)
# ─────────────────────────────────────────────────────────────────────────────

class VentPhase(Phase):
    """
    "Start Vent Hood", full screen in red, with one OK button.

    Reagent 2 evolves arsine, so the hood has to be running before the first
    packet is opened.  The screen carries nothing else — no header, no footer,
    no PRIOR/NEXT — so the only way on is to acknowledge it.  It sits in the
    chain whether or not the pre-image is, because the hood is about the
    reagents, not the camera; build_phases() sets where OK leads.
    """

    phase_id   = "vent"
    prior      = None
    next       = None
    next_phase = "reagent1"         # set per build — see build_phases()

    def enter(self, pool, slot, arb) -> None:
        pool.show("vent_bg_rect", "vent_text")
        pool.put("vent_ok", "OK", on=lambda: arb.goto(slot, self.next_phase))


# ─────────────────────────────────────────────────────────────────────────────
# Pre-image  (the strip photographed before it meets any reagent)
# ─────────────────────────────────────────────────────────────────────────────

class PreImagePhase(Phase):
    """
    Photograph the dry, unreacted strip, so the reading can be divided by this
    pad rather than by an average pad.

    A reagent pad carries a colour of its own before anything is done to it,
    and that colour varies from strip to strip by about as much as 5 ppb of
    arsenic develops.  At the bottom of the range the blank is therefore not a
    constant to assume but a thing to measure, and the only strip whose blank
    is certainly right is this one.

    Shaped like a reagent step rather than like Read: one button that runs it,
    and the run itself advances the slot.  There is no skip, because a run
    either follows the protocol or does not — an instrument that should not
    pre-image is one with capture.preimage_enabled off, which drops this phase
    out of the chain entirely (see build_phases()).

    The images go into the sample's archive under /pre; the measurement sweep
    later adds to the same file.  This step is what creates it, so h5file is
    stamped here.

    A test-variant run may ask for more than one image set (image_reps).  Each
    set after the first goes in its own "{samp_no}rep{k}.h5", and between sets
    the run stays here and asks for the cassette to be removed and replaced;
    only the last set moves the run on to reagent 1.
    """

    phase_id = "preimage"
    prior    = "entry2"
    next     = "reagent1"          # reached on completion, not by a button

    def enter(self, pool, slot, arb) -> None:
        is_live = slot.active_phase == self.phase_id
        # Same reasoning as ReadPhase: a look at the sample ROI is most useful
        # immediately before imaging, to confirm the strip is seated where the
        # rectangle expects it — and the imaging tools share the camera with
        # the capture path, so the button must be gone while one is running.
        if arb.capture_available() and not slot.capturing:
            import roi as _roi
            pool.put("view_sample_btn", "View\nSample",
                     on=lambda: arb.open_image_view(
                         slot=slot, focus_role=_roi.ROLE_SAMPLE))
        if is_live and slot.capturing:
            pool.put("run_btn", "RUNNING", enabled=False)
            pool["run_btn"].size = _RUN_BTN_SIZE_2LINE
            _line(pool, "instr1", "Imaging - DO NOT TOUCH")
        else:
            reps = arb.image_reps(slot)
            between = is_live and 0 < slot.pre_rep < reps
            label = (f"Pre-image\n{min(slot.pre_rep + 1, reps)} of {reps}"
                     if reps > 1 else f"Pre-image\n{slot.name}")
            pool.put("run_btn", label,
                     on=lambda: self.capture(arb, slot),
                     enabled=is_live and arb.capture_available())
            pool["run_btn"].size = _RUN_BTN_SIZE_2LINE
            if between:
                _line(pool, "instr1", "Remove & replace cassette", yellow=True)
            else:
                _line(pool, "instr1", "Insert new strip, weigh reagent 1")

        # The grid is on screen from here to the end of the run: reagent 1 is
        # weighed during this step, and reagent 2's row shows what is still owed.
        self._show_weights(pool, slot, arb)
        self._show_steps(pool, slot)
        self._chrome(pool, slot, arb, "CANCEL",
                     main_on=lambda: arb.open_modal(slot, "cancel"))

    def capture(self, arb, slot) -> None:
        if arb.request_preimage(slot):
            slot.capturing = True
            arb.render(slot)
        # Denied means the camera is busy; the arbiter leaves the button as it
        # was and the operator can press again.


# ─────────────────────────────────────────────────────────────────────────────
# Reagent  (armed <-> running, two instances)
# ─────────────────────────────────────────────────────────────────────────────

class ReagentPhase(Phase):
    """
    A timed reagent step with weight entry and stir sub-alarm.

    Two states, not four: armed (waiting for START) and running (counting
    down).  The weight grid below is the same in both — it is what used to
    supply the extra states, by relabelling one column Initial or Final
    depending on the timers.  All that is left to vary here is the instruction
    line, the timer binding, and whether START is offered.

    START appears only once this reagent's packet holds enough to reach target:
    `overage >= -tol`.  A packet short of that cannot make the sample right, and
    starting the countdown is the irreversible step.  Note that `overage` is
    None until the sample volume is entered on Entry 2, so START also waits for
    that — without a volume there is no target to weigh against.

    Full-duration expiry still advances to `next` regardless of weight state.
    """

    def __init__(self, phase_id: str, secs: int, next_phase: str, prior: str,
                 armed: str, start_label: str = "START"):
        self.phase_id    = phase_id
        self.secs        = secs
        self.next        = next_phase
        self.prior       = prior
        self.armed       = armed        # instr1 while waiting for START
        self.start_label = start_label  # armed START button text (may be multi-line)

    def duration(self, slot, arb) -> int:
        """
        Seconds this step counts down for `slot`.

        Fixed here, and read fresh on every render and every START rather than
        cached, so a duration edited in Menu takes effect on the next run
        without a rebuild.  Scaled by the run's time factor (1.0 outside a
        test-variant run).  Overridden by Reagent2Phase, whose base answer
        depends on the sample.
        """
        return max(1, round(self.secs * arb.reagent_time_factor(slot, 1)))

    def stir_secs(self, slot, arb) -> int:
        """
        Stir duration in seconds for this step.
        Reagent1 returns reagent1_stir_seconds(); Reagent2Phase overrides.
        """
        return arb.reagent1_stir_seconds()

    def _running_line(self, slot, arb) -> str:
        """
        The instruction shown while the countdown runs.

        Built from the configured stir duration rather than stated as a
        constant, so an operator who edits the stir seconds in Menu is told the
        duration actually being timed.  Overridden by Reagent2Phase, whose step
        also has to be ventilated.
        """
        if slot.stir_fired:
            return "Stop stirring, VENTILATE"
        stir = self.stir_secs(slot, arb)
        return f"Stir {_mmss(stir)}, then rest" if stir > 0 else "Stir continuously"

    def enter(self, pool, slot, arb) -> None:
        is_live = slot.active_phase == self.phase_id
        n = 1 if "reagent1" in self.phase_id else 2
        w = arb.reagent_weigh_plan(slot, n)

        pool["timer_lbl"].size = _TIMER_LBL_SIZE_REAGENT

        if is_live and slot.running:
            pool["timer"].bind(slot.timer, interactive=True)
            pool.put("timer")
            pool.put("run_btn", "RESET", on=lambda: arb.open_modal(slot, "reset"))
            pool["run_btn"].size = _RUN_BTN_SIZE
            _line(pool, "instr1", self._running_line(slot, arb))
        else:
            pool.put("timer_lbl", "Remaining")
            # START is gated on the packet holding enough to reach target, so
            # an under-filled packet is caught before the countdown rather than
            # after it, when nothing can be done about it.
            ready = w.overage is not None and w.overage >= -w.tol
            if ready:
                preview = TimerState.down(self.duration(slot, arb), arb.now())
                preview.pause(arb.now())
                pool["timer"].bind(preview, interactive=False)
                pool.put("timer")
                pool.put("run_btn", self.start_label,
                         on=lambda: self.start(arb, slot), enabled=is_live)
                pool["run_btn"].size = _RUN_BTN_SIZE
                pool["run_btn"].visible = is_live
                _line(pool, "instr1", f"Add {w.target:.3f} g of Reagent {n}",
                      yellow=True)
            else:
                pool.put("timer")           # shown but unbound — an empty clock
                pool.put("run_btn", "")
                pool["run_btn"].visible = False
                _line(pool, "instr1", self.armed)

        self._show_weights(pool, slot, arb)
        self._show_steps(pool, slot)
        self._chrome(pool, slot, arb, "CANCEL",
                     main_on=lambda: arb.open_modal(slot, "cancel"))

    def start(self, arb, slot) -> None:
        slot.timer   = TimerState.down(self.duration(slot, arb), arb.now())
        slot.running = True

        # Arm stir sub-alarm if configured
        stir = self.stir_secs(slot, arb)
        if stir > 0:
            slot.stir_deadline = arb.now() + stir
            slot.stir_role = self.phase_id
            slot.stir_fired = False

        arb.persist(slot, **{f"{self.phase_id}_start": arb.epoch()})
        arb.render(slot)

    def on_expire(self, arb, slot) -> None:
        slot.running = False
        slot.timer = None
        slot.stir_deadline = None
        slot.stir_role = None
        slot.stir_fired = False
        arb.persist(slot, **{f"{self.phase_id}_end": arb.epoch()})
        arb.goto(slot, self.next)


class Reagent2Phase(ReagentPhase):
    """
    Reagent step 2, whose countdown is temperature-compensated with stir sub-alarm.

    The Gutzeit reaction runs faster warm and slower cold, so a fixed wait does
    not develop every sample to the same extent.  The duration is therefore
    computed per sample from the temperature entered on Entry screen 2:

        t2(theta) = protocol.t2ref25_secs * protocol.q10 ** ((25 - theta) / 10)

    Both constants live in the config table and are editable in Menu -> Config;
    the reference duration is also on the Menu -> Reagent Phases stepper.
    config_repo.reagent2_secs() owns the arithmetic and the clamp, and the
    arbiter owns deciding which temperature answers — see arb.reagent2_plan().

    The stir sub-alarm duration is also temperature-compensated using the same
    temperature decision, via arb.reagent2_stir_plan().

    Nothing is cached: the duration is recomputed on every render and again at
    START, so correcting the temperature on Entry 2 and stepping back here
    shows the corrected wait rather than a stale one.
    """

    def duration(self, slot, arb) -> int:
        return max(1, round(arb.reagent2_plan(slot)[0]
                            * arb.reagent_time_factor(slot, 2)))

    def stir_secs(self, slot, arb) -> int:
        """Stir sub-alarm duration for reagent 2 (temperature-compensated)."""
        return arb.reagent2_stir_plan(slot)

    def _running_line(self, slot, arb) -> str:
        """
        Reagent 2 evolves arsine, so every line of this step carries the
        ventilation warning as well as the stir instruction.
        """
        if slot.stir_fired:
            return "Stop stirring, VENTILATE"
        stir = self.stir_secs(slot, arb)
        return (f"Stir {_mmss(stir)} and VENTILATE!" if stir > 0
                else "Stir continuously and VENTILATE")


# ─────────────────────────────────────────────────────────────────────────────
# Read strip  (count-up; CAPTURE)
# ─────────────────────────────────────────────────────────────────────────────

class ReadPhase(Phase):
    """
    Read the strip: an elapsed timer runs from entry, the operator may type a
    manual colour reading, and CAPTURE starts the camera sweep.  While a capture
    is in flight the button reads RUNNING and navigation locks.

    One further state sits between the capture and analysis.  Analysis divides
    by how much reagent the sample actually received, which is the difference of
    two weighings, so a run whose packets were never re-weighed cannot be
    analysed.  If the capture finishes with either final weight missing, the run
    stops here instead of advancing: the weight grid comes back in place of the
    colour-reading row, and ANALYZE lights only once both finals are entered.

    A test-variant run may take several image sets (image_reps).  Between sets
    the run stays here, the cassette is removed and replaced, and CAPTURE
    reads "Capture k of n".  capture_end is stamped only after the last set,
    so the weights gate above waits for it without a flag of its own.
    """

    phase_id = "read"
    prior    = "reagent2"
    next     = None            # advance happens on capture completion

    # The manual reading writes through to the record on each keystroke, like
    # the entry screens, so a redraw (A/B switch, Menu, arriving here after
    # typing it during a NEXT preview) shows it again rather than erasing it.
    # For a lab standard this is the known amount, entered well ahead of time.
    FIELDS = {"color_fld": "manual_read"}

    def activate(self, arb, slot) -> None:
        slot.timer   = TimerState.up(arb.now())
        slot.running = True

    def enter(self, pool, slot, arb) -> None:
        # When merely previewing this step (not the live phase) the slot's timer
        # belongs to another phase, so show a neutral 00:00 elapsed instead.
        is_live = slot.active_phase == self.phase_id
        # Tool 2.  The "Elapsed" label's rect carries a "View Sample" button
        # instead while no capture is running — a look at the sample ROI is
        # most useful here, right before pressing CAPTURE, to confirm the strip
        # is seated where the rectangle expects it.
        #
        # From the moment CAPTURE is pressed until the last frame is stored,
        # the button is *replaced by the label*, not merely disabled: the
        # imaging tools share the camera and LED bank with the capture path, so
        # there must be nothing to press.  capture_available() covers a capture
        # started by either slot, and slot.capturing covers this one's.
        # Once the measurement capture has finished, this screen stops being
        # about the camera and becomes the run's last gate: collect the final
        # packet weights, then hand over to analysis.  Keyed on capture_end
        # rather than on the missing weights themselves, or the screen would
        # flip back to offering another CAPTURE the moment the last weight was
        # typed.  Checked before anything is drawn: the grid stands where the
        # colour-reading row goes.
        awaiting = bool(slot.record.get("capture_end")) and not slot.capturing

        if arb.capture_available() and not slot.capturing and not awaiting:
            import roi as _roi
            pool.put("view_sample_btn", "View\nSample",
                     on=lambda: arb.open_image_view(
                         slot=slot, focus_role=_roi.ROLE_SAMPLE))
        else:
            pool.put("timer_lbl", "Elapsed")
            pool["timer_lbl"].size = _TIMER_LBL_SIZE_READ
        if is_live and slot.timer is not None:
            pool["timer"].bind(slot.timer, interactive=False)
        else:
            preview = TimerState.up(arb.now())
            preview.pause(arb.now())
            pool["timer"].bind(preview, interactive=False)
        pool.put("timer")
        if awaiting:
            # The colour-reading row (manual_lbl / color_fld / color_lbl) stays
            # hidden: its rects lie under the grid, and the reading was taken
            # when CAPTURE was pressed.
            ready = arb.reagent_finals_complete(slot)
            pool.put("run_btn", "ANALYZE", enabled=is_live and ready)
            # Cleared rather than left unset: Pool.put ignores on=None, so the
            # CAPTURE handler from before the capture would otherwise survive
            # under the ANALYZE label.
            pool.on("run_btn", (lambda: arb.goto(slot, "analysis")) if ready
                    else None)
            pool["run_btn"].size = _RUN_BTN_SIZE_2LINE
            _line(pool, "instr1",
                  "Weigh both packets to finish" if not ready
                  else "Ready to analyse", yellow=not ready)
            self._show_weights(pool, slot, arb)
            self._show_steps(pool, slot)
            self._chrome(pool, slot, arb, "CANCEL",
                         main_on=lambda: arb.open_modal(slot, "cancel"))
            return
        # manual_lbl ("Manually read color:") is a fixed prompt beside the
        # entry field — its text never changes, so a bare show() suffices.
        pool.show("manual_lbl")
        pool.put("color_fld", slot.record.get("manual_read", ""))
        pool.recolor("color_lbl", bg="white")     # yellow only between sets
        if is_live and slot.capturing:
            pool.put("run_btn", "RUNNING", enabled=False)
            pool["run_btn"].size = _RUN_BTN_SIZE_2LINE
            pool.put("color_lbl", "Imaging - DO NOT TOUCH")
        else:
            # Reflect what a press will actually do — the Menu -> Advanced ->
            # Capture screen's Path selection governs this capture too, so a
            # stale "CAPTURE" label can no longer hide a full calibration sweep.
            reps = arb.image_reps(slot)
            between = is_live and 0 < slot.cap_rep < reps
            if arb.cap_mode == "calibration":
                label = "Calibrate\nExposures"
            elif reps > 1:
                label = f"Capture\n{min(slot.cap_rep + 1, reps)} of {reps}"
            else:
                label = f"Capture\n{slot.name}"
            pool.put("run_btn", label,
                     on=lambda: self.capture(arb, slot, pool),
                     enabled=is_live and arb.capture_available())
            pool["run_btn"].size = _RUN_BTN_SIZE_2LINE
            pool.put("color_lbl", "Remove & replace cassette" if between
                     else "Insert strip & press Capture")
            if between:
                pool.recolor("color_lbl", bg="field_yellow")
        self._show_steps(pool, slot)
        self._show_collect(pool, slot)
        # A capture in flight does NOT lock nav either: it is arbiter-owned and
        # runs to completion regardless of what's on screen (same as an A/B
        # toggle), advancing to analysis when done; Resume returns to it.
        self._chrome(pool, slot, arb, "CANCEL",
                     main_on=lambda: arb.open_modal(slot, "cancel"))

    def capture(self, arb, slot, pool) -> None:
        slot.record["manual_read"] = pool["color_fld"].value
        if arb.request_capture(slot):
            slot.capturing = True
            arb.render(slot)
        # If denied (camera busy), the arbiter leaves the button as-is; the
        # 'no sample' case is raised by the capture itself and handled there.


# ─────────────────────────────────────────────────────────────────────────────
# Analysis  (running -> done, branch on slot.result)
# ─────────────────────────────────────────────────────────────────────────────

class AnalysisPhase(Phase):
    """
    Capture is done; analysis runs.  Until it lands, the result field shows
    progress and POST is disabled.  When the result arrives, POST enables and
    the header offers NEXT to start the next sample.
    """

    phase_id = "analysis"
    prior    = None
    next     = None

    def activate(self, arb, slot) -> None:
        slot.result   = ""
        slot.progress = "Analyzing ..."
        arb.start_analysis(slot)

    def enter(self, pool, slot, arb) -> None:
        pool.put("timer_lbl", "Final")
        pool["timer_lbl"].size = _TIMER_LBL_SIZE_ANALYSIS
        pool["timer"].bind(slot.timer, interactive=False)
        pool.put("timer")
        pool.put("result_lbl", "Result")
        done = bool(slot.result)
        pool.put("result_fld", slot.result if done else slot.progress)
        pool.put("run_btn", "POST",
                 on=(lambda: arb.post_results(slot)) if done else None,
                 enabled=done)
        self._show_collect(pool, slot)
        if done:
            self._chrome(pool, slot, arb, "NEXT",
                         main_on=lambda: arb.next_sample(slot))
        else:
            self._chrome(pool, slot, arb, "CANCEL",
                         main_on=lambda: arb.open_modal(slot, "cancel"),
                         lock_nav=True)


# ─────────────────────────────────────────────────────────────────────────────
# Modal overlay
# ─────────────────────────────────────────────────────────────────────────────

class ModalPhase(Phase):
    """
    A choice dialog.  `message` may reference record fields (e.g. "{samp_no}")
    and the slot id as "{slot}".  `buttons` is a list of 1..3 (label, action)
    pairs, laid out by count; action is a callable(arb, slot), or None to simply
    close back to the phase that opened the modal.
    """

    def __init__(self, phase_id: str, message: str, buttons: list):
        self.phase_id = phase_id
        self.message  = message
        self.buttons  = buttons

    def enter(self, pool, slot, arb) -> None:
        # slot.name is exposed as {slot}; it wins over any record field of the
        # same name so the slot id is always the A/B being cancelled.
        fields = {**slot.record, "slot": slot.name}
        try:
            msg = self.message.format(**fields)
        except (KeyError, IndexError):
            msg = self.message
        pool.put("modal_panel")
        pool.put("modal_msg", msg)
        names = pool.layout_modal_buttons(len(self.buttons))
        for name, (label, action) in zip(names, self.buttons):
            on = ((lambda a=action: a(arb, slot)) if action
                  else (lambda: arb.close_modal(slot)))
            pool.put(name, label, on=on)


def _renumber(arb, slot) -> None:
    """Dup modal: take the next unclaimed number and go back to the entry screen."""
    arb.claim_next_no(slot)
    arb.close_modal(slot)


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

def build_phases(reagent1_secs: int = 120, preimage: bool = True,
                 test_variant: bool = False) -> dict:
    """
    Return {phase_id: Phase}, the whole protocol.

    reagent1_secs lets the caller set step 1's countdown; the arbiter passes the
    configured duration (config_repo.reagent1_secs), which the Menu -> Reagent
    Phases screen overrides on the instrument itself.  That screen rebuilds the
    phases through this function, so a new duration takes effect without a
    restart.

    Step 2 takes no duration argument, because it does not have one duration:
    Reagent2Phase asks the arbiter for a countdown computed from the sample's
    temperature each time it needs one.  A change to protocol.t2ref25_secs or
    protocol.q10 therefore lands without a rebuild at all.

    `preimage` (config_repo.capture_preimage_enabled) inserts PreImagePhase
    between entry 2 and reagent 1.  It is left OUT of the chain rather than
    made skippable when off, so there is no half-state: a protocol either
    blanks every strip or none of them, and a run cannot end up with some
    strips carrying their own blank and some not.  The link is written in one
    place here — the phases either side take their neighbour from this
    function, not from their own class attributes.

    The vent reminder (VentPhase) always comes before the first reagent-side
    step, and its OK leads to the pre-image or to reagent 1.

    `test_variant` (config_repo.test_variant_enabled) inserts VariantPhase
    between entry 2 and the vent reminder, for development runs.  Off, the
    chain is exactly the production protocol.

    Args:
        reagent1_secs: Countdown for reagent step 1, in seconds.
        preimage:      Whether the protocol includes the pre-image step.
        test_variant:  Whether runs show the test-variant screen.
    """
    first = "preimage" if preimage else "reagent1"   # first reagent-side step
    before = "variant" if test_variant else "entry2"  # the data screen before it
    entry2 = Entry2Phase()
    entry2.next = "variant" if test_variant else first
    entry2.proceed_to = "variant" if test_variant else "vent"
    vent = VentPhase()
    vent.next_phase = first
    phases = [
        EntryPhase(),
        entry2,
        vent,
        ReagentPhase("reagent1", reagent1_secs, "reagent2",
                     prior="preimage" if preimage else before,
                     armed="Weigh the reagent 1 packet",
                     start_label="START\n1"),
        # secs=0 is never read: Reagent2Phase.duration() overrides it outright.
        Reagent2Phase("reagent2", 0, "read", prior="reagent1",
                      armed="Weigh the reagent 2 packet",
                      start_label="START\n2"),
        ReadPhase(),
        AnalysisPhase(),
        # Cancel offers three outcomes: stop the run but keep the reserved DB row
        # as-is, stop and delete the row (start over), or resume the run.
        ModalPhase("cancel", "Cancel test {slot}  {samp_no}?",
                   [("Keep row",   lambda arb, slot: arb.cancel_test(slot, delete=False)),
                    ("Delete row", lambda arb, slot: arb.cancel_test(slot, delete=True)),
                    ("Resume",     None)]),
        ModalPhase("reset", "Reset the timer?",
                   [("Reset", lambda arb, slot: arb.reset_timer(slot)),
                    ("No",    None)]),
        ModalPhase("nosample", "INSERT SAMPLE AND CONTINUE",
                   [("CAPTURE", lambda arb, slot: arb.retry_capture(slot)),
                    ("RETURN",  None)]),
        # The rejected number is by definition in the table, so New number
        # always moves; Edit returns to the field to be corrected by hand.
        ModalPhase("dup", "Sample number already in use",
                   [("New number", _renumber), ("Edit", None)]),
    ]
    if test_variant:
        variant = VariantPhase()
        variant.next = first
        phases.append(variant)
    if preimage:
        pre = PreImagePhase()
        pre.prior = before
        phases.append(pre)
    return {p.phase_id: p for p in phases}


# ─────────────────────────────────────────────────────────────────────────────
# Self-test — drive a slot end-to-end via a mock arbiter.
# The mock is the executable spec for arbiter.py.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)-8s [%(name)s] %(message)s")

    from pool import Pool
    from slot import Slot

    class _MockDisplay:
        def fill_screen(self, color): pass
        def fill_rect(self, rect, color, **kw): pass
        def draw_rect(self, rect, color, **kw): pass
        def draw_text(self, text, rect, **kw): pass

    import version
    from sample_repo import Defaults

    # Canned pre-fills.  These tests are about phase behaviour, so they state
    # the defaults outright rather than depending on what the database holds.
    # software_ver is the exception: it is never a stored default, so the only
    # honest value for it is the one the running code carries.
    DEFAULTS = Defaults(
        samp_no_template="ZZ1000",
        user="William Olsen", user_email="w@example.org", user_org="MGWWG",
        testkit="Ascel 458201", testkit_vendor="Industrial Test Systems, Inc.",
        testlot="0 12V601-HH", testlot_expire_dt="2027-07",
        remark="", default_temperature=21.0, default_sample_wt="100.",
        hardware_sn="XCN-001", software_ver=version.__version__,
    )

    class MockArbiter:
        """Minimal stand-in exercising the whole phase contract."""
        def __init__(self, pool, display):
            self.pool, self.display = pool, display
            self.phases = build_phases()
            self.clock = 1000.0
            self.epoch_s = 1_700_000_000
            self.capturing_slot = None
            self.cap_mode = "normal"      # "calibration" | "normal"
            self.calls = []
            self.claimed = []             # samp_nos with a row — the mock's table
            self.fail_begin = False       # flip on to simulate a duplicate samp_no
            self.temp_c = 21.7            # canned DS18B20 reading
            self.temp_probe = True        # flip off to simulate no probe
            # protocol.t2ref25_secs / protocol.q10, canned like the defaults.
            self.t2ref25 = 720
            self.q10 = 2.0

        # clock / stamps
        def now(self):        return self.clock
        def timestamp(self):  return "2026-07-20 14:00:00"
        def epoch(self):      return self.epoch_s
        def read_temperature(self):
            return self.temp_c if self.temp_probe else None
        def temperature_attached(self):  return self.temp_probe

        # reagent 2's temperature-compensated countdown.  Mirrors the real
        # arbiter: the sample's own temperature when it has one, otherwise the
        # configured stand-in, flagged so the screen can bracket it.
        def t2ref25_secs(self):  return self.t2ref25
        def reagent2_plan(self, slot):
            t = _to_float(slot.record.get("samp_degC"))
            measured = t is not None
            if not measured:
                t = DEFAULTS.default_temperature
            secs = round(self.t2ref25 * self.q10 ** ((25.0 - t) / 10.0))
            return max(1, min(3600, secs)), t, measured

        # weight entry and stir sub-alarms.  Mirrors the real arbiter — see
        # Arbiter.reagent_weigh_plan for what `net` means in each case.
        def reagent_weigh_plan(self, slot, n):
            empty_wt = 0.530 if n == 1 else 0.540
            ratio = 2.900 if n == 1 else 1.300
            tol = 0.050

            initial = _to_float(slot.record.get(f"reagent{n}_initial_wt", ""))
            final = _to_float(slot.record.get(f"reagent{n}_final_wt", ""))
            sample_wt = _to_float(slot.record.get("samp_grams", ""))

            if initial is None:
                net = None
            elif final is None:
                net = initial - empty_wt
            else:
                net = initial - final

            target = (None if sample_wt is None else sample_wt / 100.0 * ratio
                      * _factor_value(slot.record.get(f"R{n}_wt_factor")))
            overage = None if (net is None or target is None) else net - target

            return ReagentWeights(initial=initial, final=final, net=net,
                                   target=target, overage=overage, tol=tol)

        def reagent_time_factor(self, slot, n):
            return _factor_value(slot.record.get(f"R{n}_time_factor"))

        def reagent_finals_complete(self, slot):
            return all(str(slot.record.get(f"reagent{n}_final_wt", "")).strip()
                       for n in (1, 2))

        def reagent1_stir_seconds(self):
            return 25  # canned default for testing

        def reagent2_stir_plan(self, slot):
            # Temperature-compensated stir duration, 0 if disabled
            t = _to_float(slot.record.get("samp_degC"))
            if t is None:
                t = DEFAULTS.default_temperature
            base_stir = 0  # canned: reagent 2 has no stir by default
            if base_stir == 0:
                return 0
            secs = round(base_stir * self.q10 ** ((25.0 - t) / 10.0))
            return max(1, min(3600, secs))

        # database / sample lifecycle.  The numbering is modelled rather than
        # canned, because which number a slot ends up holding is exactly what
        # the entry screen's behaviour turns on.
        def defaults(self):        return DEFAULTS

        @staticmethod
        def _one_past(taken):
            return "ZZ%d" % (max((int(n[2:]) for n in taken
                                  if n[:2] == "ZZ" and n[2:].isdigit()),
                                 default=1000) + 1)

        def next_sample_no(self, slot=None):
            """A fresh slot's number: past the table and past the other slots."""
            held = [s.record.get("samp_no", "") for s in self.slots if s is not slot]
            return self._one_past(self.claimed + held)

        def claim_next_no(self, slot):
            """The ID press: the next unclaimed number, bumping whoever shows it."""
            want = self._one_past(self.claimed)
            if slot.record.get("samp_no") == want:
                return
            slot.record["samp_no"] = want
            for other in self.slots:
                if other is slot or other.record.get("db_rowid") is not None:
                    continue
                if other.record.get("samp_no") == want:
                    other.record["samp_no"] = self.next_sample_no(other)

        def begin_sample(self, slot):
            if self.fail_begin:
                return False
            slot.record["db_rowid"] = 42
            self.claimed.append(slot.record["samp_no"])
            return True
        def persist(self, slot, **cols):
            slot.record.update(cols)
            self.calls.append(("persist", tuple(sorted(cols))))

        # navigation / render
        def goto(self, slot, phase_id):
            slot.phase = slot.active_phase = phase_id
            self.phases[phase_id].activate(self, slot)
            self.render(slot)
        def view(self, slot, phase_id):        # preview: no advance, no activate
            slot.phase = phase_id
            self.render(slot)
        def resume(self, slot):
            self.view(slot, slot.active_phase)
        def render(self, slot):
            self.pool.hide_all()
            self.phases[slot.phase].enter(self.pool, slot, self)
            self.pool.set("slot_btn", "B" if slot.name == "A" else "A")
            self.pool.render(self.display)

        # modals
        def open_modal(self, slot, mid):
            slot.modal_return = slot.phase
            slot.phase = mid
            self.render(slot)
        def close_modal(self, slot):
            slot.phase, slot.modal_return = (slot.modal_return or "entry"), None
            self.render(slot)

        # image viewer (Tool 2) — records the call so the read phase's
        # "View Sample" button can be checked without a camera.
        def open_image_view(self, slot=None, focus_role=None):
            self.calls.append(("open_image_view",
                               slot.name if slot else None, focus_role))

        # capture / analysis
        def capture_available(self):    return self.capturing_slot is None
        def image_reps(self, slot):
            return _image_reps(slot.record.get("image_reps"))
        def request_capture(self, slot):
            if self.capturing_slot is None:
                self.capturing_slot = slot
                if slot.cap_rep == 0:             # the first set starts the clock
                    self.persist(slot, capture_start=self.epoch())
                self.persist(slot, manual_read=slot.record.get("manual_read", ""))
                return True
            return False
        def complete_capture(self, slot):        # driven by the loop in real life
            slot.capturing = False
            self.capturing_slot = None
            slot.cap_rep += 1
            k = slot.cap_rep
            slot.rep_files.setdefault(k, "fake.h5" if k == 1 else f"fakerep{k}.h5")
            if k < self.image_reps(slot):         # another set to come
                slot.progress = f"Set {k} of {self.image_reps(slot)} stored"
                self.render(slot)
                return
            slot.timer.stop(self.now())
            self.persist(slot, capture_end=self.epoch(), h5file=slot.rep_files[1])
            # Mirrors the real arbiter: analysis waits on both final weighings.
            if self.reagent_finals_complete(slot):
                self.goto(slot, "analysis")
            else:
                slot.progress = "Enter final reagent weights"
                self.render(slot)

        # pre-image.  Note what it does NOT do: no capture_start, no timer.
        # It stamps h5file because it is the run that creates the archive,
        # and it advances to reagent 1 rather than to analysis.
        def request_preimage(self, slot):
            if self.capturing_slot is not None:
                return False
            self.capturing_slot = slot
            return True
        def complete_preimage(self, slot):
            slot.capturing = False
            self.capturing_slot = None
            slot.pre_rep += 1
            k = slot.pre_rep
            slot.rep_files[k] = "fake.h5" if k == 1 else f"fakerep{k}.h5"
            if k == 1:
                self.persist(slot, h5file="fake.h5")
            if k < self.image_reps(slot):         # stay for the next set
                slot.progress = f"Pre-image {k} of {self.image_reps(slot)} stored"
                self.render(slot)
                return
            self.goto(slot, "reagent1")
        def start_analysis(self, slot):  self.calls.append("start_analysis")
        def analysis_done(self, slot, text):
            slot.result = text
            self.persist(slot, txt_result=text)
            self.render(slot)

        # poll for expiry (arithmetic each loop pass)
        def poll(self):
            now = self.now()
            for slot in self.slots:
                t = slot.timer
                if t is not None and t.expired(now):
                    slot.timer = None
                    self.alarm(slot)
                    self.phases[slot.active_phase].on_expire(self, slot)

                # Check for stir sub-alarm
                if (slot.stir_deadline is not None and now >= slot.stir_deadline
                        and not slot.stir_fired):
                    slot.stir_fired = True
                    self.calls.append(("stir_alarm", slot.name))
                    self.persist(slot, **{f"{slot.stir_role}_stir_end": self.epoch()})
                    if slot is self.slots.get(self.pool["slot_btn"].text):
                        self.render(slot)

        # actions invoked by buttons / modals
        def alarm(self, slot):        self.calls.append(("alarm", slot.name))
        def cancel_test(self, slot, delete=False):
            self.calls.append(("cancel", delete))
            if delete:                      # deleting the row frees its number
                self.claimed = [n for n in self.claimed
                                if n != slot.record.get("samp_no")]
            slot.clear_run(); self.goto(slot, "entry")
        def reset_timer(self, slot):
            ap = slot.active_phase
            if ap in ("reagent1", "reagent2"):
                self.persist(slot, **{f"{ap}_start": None})
            slot.timer = None
            slot.running = False
            slot.stir_deadline = None
            slot.stir_role = None
            slot.stir_fired = False
            self.close_modal(slot)
        def retry_capture(self, slot):
            self.close_modal(slot)
        def post_results(self, slot): self.calls.append("post")

        # test-variant treatments.  Modelled, like the numbering: the carry-
        # over a new run starts from is the last row saved here.
        EQUIP = ["A Original bottle", "B 250 Ehrl, unprotected",
                 "C 250 Ehrl, protected"]
        treatments = {}
        def equipment_list(self):  return list(self.EQUIP)
        def latest_treatment(self):
            return (list(self.treatments.values())[-1] if self.treatments
                    else None)
        def save_treatment(self, slot):
            r = slot.record
            self.treatments.pop(r["samp_no"], None)   # re-saving moves it last
            self.treatments[r["samp_no"]] = {
                "test_equip": r["test_equip"], "image_reps": r["image_reps"],
                **{k: _to_float(r[k]) for k in VariantPhase.FIELDS.values()}}

        def next_sample(self, slot):
            self.calls.append("next_sample"); slot.clear_run(); self.goto(slot, "entry")

    def press(pool, name):
        w = pool[name]
        assert w.visible and w.enabled, f"{name} not pressable (visible={w.visible}, enabled={w.enabled})"
        w.on_press()

    def check(label, got, want):
        ok = "OK " if got == want else "XX "
        print(f"  {ok}{label:44s} got={got!r}")
        assert got == want, f"{label}: {got!r} != {want!r}"

    pool = Pool.build()
    arb  = MockArbiter(pool, _MockDisplay())
    A    = Slot("A")
    arb.slots = [A]

    print("== entry: only the ID row is live until the number is claimed ==")
    arb.goto(A, "entry")
    check("main_btn label",        pool["main_btn"].label, "Begin")
    check("title names the screen", pool["title"].text, "New Test")
    check("id autofilled",         pool["id_fld"].value, "ZZ1001")
    check("id editable",           pool["id_fld"].enabled, True)
    check("id label live",         pool["id_lbl"].enabled, True)
    check("user empty",            pool["user_fld"].value, "")
    check("test empty",            pool["test_fld"].value, "")
    check("test-lot empty",        pool["test_lot_fld"].value, "")
    check("user locked",           pool["user_fld"].enabled, False)
    check("user label inert",      pool["user_lbl"].enabled, False)
    check("Begin enabled by the number alone", pool["main_btn"].enabled, True)

    print("== ID keeps its number, or takes one from an unclaimed slot ==")
    # Nothing is claimed, so the number A2 displays is still the next one going:
    # pressing ID on B2 takes it and moves A2 aside rather than skipping past it.
    A2, B2 = Slot("A"), Slot("B")
    arb.slots = [A2, B2]
    arb.goto(A2, "entry"); arb.goto(B2, "entry")
    check("two slots never agree",
          (A2.record["samp_no"], B2.record["samp_no"]), ("ZZ1001", "ZZ1002"))
    arb.render(A2); press(pool, "id_lbl"); press(pool, "id_lbl")
    check("ID is idempotent",   A2.record["samp_no"], "ZZ1001")
    check("the other is untouched", B2.record["samp_no"], "ZZ1002")
    arb.render(B2); press(pool, "id_lbl")
    check("B takes the next unclaimed", B2.record["samp_no"], "ZZ1001")
    check("A is bumped aside",          A2.record["samp_no"], "ZZ1002")
    arb.slots = [A]; arb.render(A)

    print("== entry2 previewed before Begin is look-but-don't-touch ==")
    arb.view(A, "entry2")
    check("fields are visible",   pool["temp_fld"].visible, True)
    check("temp locked",          pool["temp_fld"].enabled, False)
    check("remark locked",        pool["remark_fld"].enabled, False)
    check("temp label inert",     pool["temp_lbl"].enabled, False)
    check("header offers Resume", pool["main_btn"].label, "Resume")
    press(pool, "main_btn")                        # Resume -> back to entry
    check("back on entry",        A.phase, "entry")

    print("== Begin claims the number and stays on screen 1 ==")
    press(pool, "main_btn")
    check("still on entry",        A.phase, "entry")
    check("row claimed",           A.record["db_rowid"], 42)
    check("title is the number",   pool["title"].text, "ZZ1001")
    check("main is Continue",      pool["main_btn"].label, "Continue")
    check("id locked",             pool["id_fld"].enabled, False)
    check("id label inert",        pool["id_lbl"].enabled, False)
    check("user filled at claim",  pool["user_fld"].value, DEFAULTS.user)
    check("test filled at claim",  pool["test_fld"].value, DEFAULTS.testkit)
    check("test-lot filled",       pool["test_lot_fld"].value, DEFAULTS.testlot)
    check("identity now editable", pool["user_fld"].enabled, True)
    check("instrument_sn carried", A.record["instrument_sn"], DEFAULTS.hardware_sn)

    print("== label-button refills a cleared field (claimed slot) ==")
    A.record["user"] = ""; arb.render(A)
    check("user cleared",   pool["user_fld"].value, "")
    press(pool, "user_lbl")                        # User label = load-default button
    check("user refilled",  pool["user_fld"].value, DEFAULTS.user)

    print("== Continue -> entry2 ==")
    press(pool, "main_btn")
    check("phase entry2",      A.phase, "entry2")
    check("main is Continue",  pool["main_btn"].label, "Continue")
    check("temp label shown",  pool["temp_lbl"].visible, True)
    check("entry2 now editable", pool["temp_fld"].enabled, True)

    print("== entry2: Temp label reads the sensor into temp_fld ==")
    check("plain label with a probe", pool["temp_lbl"].label, "Temp °C")
    press(pool, "temp_lbl")                        # Temp label = sensor-read button
    check("temp read into field",  pool["temp_fld"].value, "21.7")
    check("temp stored in record", A.record["samp_degC"], "21.7")

    print("== entry2: with no probe, the Temp button offers the default ==")
    arb.temp_probe = False
    A.record["samp_degC"] = ""; arb.render(A)
    check("label shows the default", pool["temp_lbl"].label, "[21.0 °C]")
    press(pool, "temp_lbl")
    check("default filled in",     pool["temp_fld"].value, "21.0")
    check("default in record",     A.record["samp_degC"], "21.0")

    print("== entry2: the Weight label fills in the configured sample weight ==")
    A.record["samp_grams"] = ""; arb.render(A)
    press(pool, "weight_lbl")
    check("weight filled in",  pool["weight_fld"].value, DEFAULTS.default_sample_wt)
    check("weight in record",  A.record["samp_grams"], DEFAULTS.default_sample_wt)

    print("== entry2: an attached probe that fails still says err ==")
    arb.temp_probe = True; arb.temp_c = None
    press(pool, "temp_lbl")
    check("failed read marked",    A.record["samp_degC"], "err")
    arb.temp_c = 21.7

    print("== entry2: collect stamp + Continue persists -> vent reminder ==")
    A.record["samp_degC"] = "21.5"; A.record["samp_grams"] = "3.2"   # writethrough sim
    A.record["remark"] = "cloudy sample"                             # writethrough sim
    press(pool, "collectdt_lbl")                   # Collect label stamps the datetime
    check("collect stamped",  bool(A.record["collect_dt"]), True)
    press(pool, "main_btn")                        # Continue
    check("degC persisted (float)",  A.record["samp_degC"], 21.5)
    check("grams persisted (float)", A.record["samp_grams"], 3.2)
    check("remark persisted",        A.record["remark"], "cloudy sample")

    print("== vent reminder: a plain red screen; OK goes on to preimage ==")
    check("phase vent",       A.phase, "vent")
    check("live phase vent",  A.active_phase, "vent")
    check("red backdrop",     pool["vent_bg_rect"].visible, True)
    check("vent text",        pool["vent_text"].text, "Start\nVent\nHood")
    check("OK shown",         pool["vent_ok"].visible, True)
    check("no header button", pool["main_btn"].visible, False)
    check("no PRIOR",         pool["prior_btn"].visible, False)
    check("no NEXT",          pool["next_btn"].visible, False)
    check("no MENU",          pool["menu_btn"].visible, False)
    check("NEXT on entry2 skips it", arb.phases["entry2"].next, "preimage")
    press(pool, "vent_ok")
    check("phase preimage",   A.phase, "preimage")
    check("run_btn label",    pool["run_btn"].label, "Pre-image\nA")
    check("instr1",           pool["instr1"].text,
          "Insert new strip, weigh reagent 1")
    check("main is CANCEL",   pool["main_btn"].label, "CANCEL")

    print("== preimage runs, stamps h5file, and advances to reagent1 ==")
    press(pool, "run_btn")
    check("capturing",        A.capturing, True)
    check("run_btn is RUNNING", pool["run_btn"].label, "RUNNING")
    check("no View Sample while imaging",
          pool["view_sample_btn"].visible, False)
    check("no capture_start",  A.record["capture_start"], None)
    arb.complete_preimage(A)
    check("phase reagent1",   A.phase, "reagent1")
    check("h5file stamped",   A.record["h5file"], "fake.h5")
    check("capture_end still empty", A.record["capture_end"], None)

    # No initial weight yet, so there is no overage and START stays hidden.
    check("run_btn not shown", pool["run_btn"].visible, False)
    check("armed instruction", pool["instr1"].text, "Weigh the reagent 1 packet")

    # Simulate entering the initial weight; the packet holds far more than the
    # 3.2 mL sample's 0.093 g target, so START appears.
    A.record["reagent1_initial_wt"] = "1.850"
    arb.render(A)
    check("run_btn shown after weight", pool["run_btn"].visible, True)
    check("run_btn label",    pool["run_btn"].label, "START\n1")
    check("instr1",           pool["instr1"].text.startswith("Add"), True)

    print("== weight grid: the four Over bands and the two nagging colours ==")
    from palette import color as _color
    _W = Slot("W"); arb.slots = [_W]
    arb.goto(_W, "reagent1")
    # 100 mL of sample puts reagent 1's target at a realistic 2.900 g, so the
    # bands below are the ones an operator actually sees.
    _W.record["samp_grams"] = "100.0"

    def _grid(initial, final=""):
        _W.record["reagent1_initial_wt"] = initial
        _W.record["reagent1_final_wt"] = final
        arb.render(_W)
        return (pool["wt1_init_fld"], pool["wt1_over_lbl"], pool["wt1_targ_lbl"])

    _init, _over, _targ = _grid("")
    check("target shown without any weighing", _targ.text, "2.900")
    check("empty Initial nags",  _init.bg, _color("wt_missing"))
    check("Over blank with no weight", _over.text, "")
    check("no START",            pool["run_btn"].visible, False)

    # 3.000 g in the packet less the 0.530 g packet itself = 2.470 g, which is
    # 0.430 g short of target — far more than the 0.050 g tolerance.
    _init, _over, _targ = _grid("3.000")
    check("short packet: Initial amber", _init.bg, _color("wt_warn"))
    check("short packet: Over on red",   _over.bg, _color("wt_alarm"))
    check("short packet: Over text",     _over.text, "-0.430")
    check("short packet: no START",      pool["run_btn"].visible, False)

    # 3.400 - 0.530 = 2.870, 0.030 g under target and inside the tolerance.
    _init, _over, _targ = _grid("3.400")
    check("just under: Initial plain",  _init.bg, _color("white"))
    check("just under: Over red text",  _over.color, _color("wt_under"))
    check("just under: on white",       _over.bg, _color("white"))
    check("just under: START offered",  pool["run_btn"].visible, True)

    # 3.450 - 0.530 = 2.920, 0.020 g over target, inside the tolerance.
    _init, _over, _targ = _grid("3.450")
    check("just over: Over blue text",  _over.color, _color("wt_over"))
    check("just over: on white",        _over.bg, _color("white"))

    # 3.600 - 0.530 = 3.070, 0.170 g over target: past the tolerance.
    _init, _over, _targ = _grid("3.600")
    check("well over: Over blue text",  _over.color, _color("wt_over"))
    check("well over: on amber",        _over.bg, _color("wt_warn"))
    check("well over: START still offered", pool["run_btn"].visible, True)

    # With a final weighing, Over switches from what the packet holds to what
    # was actually poured in: 3.600 - 0.700 = 2.900, exactly on target.
    _init, _over, _targ = _grid("3.600", "0.700")
    check("after pouring: Over reads the amount added", _over.text, "+0.000")
    check("after pouring: blue on white", _over.bg, _color("white"))

    print("== weight grid: Final boxes nag only once reagent 2 has started ==")
    _W.record["reagent1_final_wt"] = ""
    _W.record["reagent2_start"] = None
    arb.render(_W)
    check("before reagent 2, Final is plain",
          pool["wt1_final_fld"].bg, _color("white"))
    _W.record["reagent2_start"] = arb.epoch()
    arb.render(_W)
    check("once reagent 2 starts, both Finals nag",
          (pool["wt1_final_fld"].bg, pool["wt2_final_fld"].bg),
          (_color("wt_missing"), _color("wt_missing")))
    _W.record["reagent1_final_wt"] = "0.700"
    arb.render(_W)
    check("a filled Final stops nagging",
          pool["wt1_final_fld"].bg, _color("white"))

    print("== weight grid: a keystroke's destination never depends on the run ==")
    from arbiter import WT_FIELD_KEYS
    check("one box, one record key",
          sorted(WT_FIELD_KEYS.values()),
          ["reagent1_final_wt", "reagent1_initial_wt",
           "reagent2_final_wt", "reagent2_initial_wt"])

    # Hand the screen back to A, whose run the sections below carry on with.
    arb.slots = [A]
    arb.render(A)

    print("== a protocol without the pre-image links entry2 straight to reagent1 ==")
    plain = build_phases(preimage=False)
    check("no preimage phase",  "preimage" in plain, False)
    check("entry2 -> reagent1", plain["entry2"].next, "reagent1")
    check("reagent1 <- entry2", plain["reagent1"].prior, "entry2")
    check("vent still in it",   plain["vent"].next_phase, "reagent1")
    check("with it, vent -> preimage", arb.phases["vent"].next_phase, "preimage")
    check("with it, entry2 -> preimage", arb.phases["entry2"].next, "preimage")
    check("with it, reagent1 <- preimage",
          arb.phases["reagent1"].prior, "preimage")

    print("== reagent1 START stamps reagent1_start ==")
    press(pool, "run_btn")
    check("running",              A.running, True)
    check("run_btn is RESET",     pool["run_btn"].label, "RESET")
    check("reagent1_start stamped", A.record["reagent1_start"], arb.epoch_s)

    print("== reset modal (2 buttons) nulls the start ==")
    press(pool, "run_btn")                         # RESET -> reset modal
    check("phase is modal",    A.phase, "reset")
    check("modal msg",         pool["modal_msg"].text, "Reset the timer?")
    check("no 3rd button",     pool["modal_third"].visible, False)
    press(pool, "modal_no")                        # No -> back to running reagent1
    check("back to reagent1",  A.phase, "reagent1")
    press(pool, "run_btn"); press(pool, "modal_yes")   # RESET -> confirmed
    check("timer cleared",     A.timer, None)
    check("reagent1_start nulled", A.record["reagent1_start"], None)

    print("== run to expiry -> reagent2 (stamps reagent1_end) ==")
    press(pool, "run_btn")                         # START reagent1
    arb.clock += 121                               # past the deadline
    arb.poll()
    check("reagent1_end stamped", A.record["reagent1_end"], arb.epoch_s)
    check("advanced to reagent2", A.phase, "reagent2")
    check("alarm sounded",     ("alarm", "A") in arb.calls, True)

    print("== reagent2's countdown is computed from the sample temperature ==")
    # 21.5 C was persisted on entry2, so the wait is longer than the 12:00
    # reference: 720 * 2 ** ((25 - 21.5) / 10) = 918 s.
    check("armed at the compensated wait",
          arb.reagent2_plan(A)[0], 918)
    # No reagent 2 weighing yet, so its Over cell is blank and START is hidden.
    check("R2 over cell blank", pool["wt2_over_lbl"].text, "")
    check("no START without a weight", pool["run_btn"].visible, False)

    print("== a colder sample waits longer, a warmer one less ==")
    A.record["samp_degC"] = 15.0; arb.render(A)
    check("10 C below the reference doubles it", arb.reagent2_plan(A)[0], 1440)
    A.record["samp_degC"] = 35.0; arb.render(A)
    check("10 C above halves it",                arb.reagent2_plan(A)[0], 360)

    print("== Q10 = 1 turns the compensation off ==")
    arb.q10 = 1.0
    check("constant, whatever the temperature",
          {arb.reagent2_plan(A)[0] for A.record["samp_degC"] in (5.0, 25.0, 40.0)},
          {720})
    arb.q10 = 2.0

    print("== with no temperature at all, the stand-in is used and bracketed ==")
    A.record["samp_degC"] = None; arb.render(A)
    _secs, _t, _measured = arb.reagent2_plan(A)
    check("falls back to the default", _t, DEFAULTS.default_temperature)
    check("flagged as not measured",   _measured, False)
    A.record["reagent2_initial_wt"] = "1.650"
    arb.render(A)
    check("R2 over cell filled once weighed",
          pool["wt2_over_lbl"].text.startswith("+"), True)
    check("START offered", pool["run_btn"].visible, True)

    print("== reagent2 -> read (capture) ==")
    A.record["samp_degC"] = 21.5; arb.render(A)
    press(pool, "run_btn")                         # START reagent2
    arb.clock += 919                               # past the computed deadline
    arb.poll()
    check("advanced to read",  A.phase, "read")
    check("CAPTURE enabled",   pool["run_btn"].enabled, True)

    print("== read: View Sample stands where Elapsed goes (Tool 2) ==")
    check("View Sample shown",  pool["view_sample_btn"].visible, True)
    check("its label",          pool["view_sample_btn"].label, "View\nSample")
    check("Elapsed label hidden", pool["timer_lbl"].visible, False)
    check("same rect as the label",
          pool["view_sample_btn"].rect is pool["timer_lbl"].rect, True)
    press(pool, "view_sample_btn")
    import roi as _roi
    check("opens the viewer on the sample role, returning to this slot",
          ("open_image_view", "A", _roi.ROLE_SAMPLE) in arb.calls, True)

    pool.set("color_fld", "pale blue")             # operator types a reading
    press(pool, "run_btn")                         # CAPTURE
    check("manual read saved",     A.record["manual_read"], "pale blue")
    check("capture_start stamped", A.record["capture_start"], arb.epoch_s)
    check("capturing",             A.capturing, True)

    print("== read: once CAPTURE is pressed, Elapsed replaces View Sample ==")
    check("View Sample gone",   pool["view_sample_btn"].visible, False)
    check("Elapsed label back", pool["timer_lbl"].visible, True)
    check("reads Elapsed",      pool["timer_lbl"].text, "Elapsed")
    # Not merely disabled — there must be nothing to press, because the imaging
    # tools share the camera and LED bank with the capture in flight.  Tapping
    # where the button was must reach nothing at all.
    n_before = len(arb.calls)
    _r = pool["view_sample_btn"].rect
    check("a tap there is not consumed",
          pool.handle_touch(_r.x + _r.w // 2, _r.y + _r.h // 2, arb.display), False)
    check("and opens nothing", len(arb.calls), n_before)

    print("== capture completes but the final weighings are missing: hold on read ==")
    arb.clock += 30
    arb.complete_capture(A)
    check("held on read",        A.phase, "read")
    check("capture_end stamped", A.record["capture_end"], arb.epoch_s)
    check("h5file stamped",      A.record["h5file"], "fake.h5")
    check("run_btn is ANALYZE",  pool["run_btn"].label, "ANALYZE")
    check("ANALYZE disabled",    pool["run_btn"].enabled, False)
    check("colour-reading row hidden", pool["manual_lbl"].visible, False)
    check("weight grid shown",   pool["wt1_final_fld"].visible, True)
    # Both finals came due when reagent 2 started, so both empty boxes nag.
    from palette import color as _color
    check("R1 final flagged empty", pool["wt1_final_fld"].bg, _color("wt_missing"))
    check("R2 final flagged empty", pool["wt2_final_fld"].bg, _color("wt_missing"))

    print("== entering both final weighings releases ANALYZE ==")
    A.record["reagent1_final_wt"] = "1.760"        # 1.850 - 1.760 = 0.090 g added
    arb.render(A)
    check("still held on one weighing", pool["run_btn"].enabled, False)
    check("R1 final no longer flagged", pool["wt1_final_fld"].bg, _color("white"))
    A.record["reagent2_final_wt"] = "1.610"        # 1.650 - 1.610 = 0.040 g added
    arb.render(A)
    check("ANALYZE released",    pool["run_btn"].enabled, True)
    check("still ANALYZE, not CAPTURE", pool["run_btn"].label, "ANALYZE")
    press(pool, "run_btn")
    check("phase analysis",      A.phase, "analysis")
    arb.analysis_done(A, "Nitrate 12 ppm")
    check("result persisted",   A.record["txt_result"], "Nitrate 12 ppm")
    check("main is NEXT",       pool["main_btn"].label, "NEXT")

    print("== cancel offers 3 choices; Delete row deletes ==")
    B = Slot("B"); arb.slots = [B]
    arb.goto(B, "entry"); press(pool, "main_btn")  # Begin -> claims, stays on entry
    press(pool, "main_btn")                         # Continue -> entry2
    press(pool, "main_btn")                         # Continue -> vent
    press(pool, "vent_ok")                          # OK -> preimage
    press(pool, "main_btn")                         # CANCEL -> cancel modal
    check("cancel modal",      B.phase, "cancel")
    check("cancel msg: slot + samp_no",
          pool["modal_msg"].text, f"Cancel test B  {B.record['samp_no']}?")
    check("3rd button shown",  pool["modal_third"].visible, True)
    check("keep label",        pool["modal_yes"].label, "Keep row")
    check("delete label",      pool["modal_no"].label, "Delete row")
    check("resume label",      pool["modal_third"].label, "Resume")
    press(pool, "modal_no")                         # Delete row
    check("cancel(delete=True)", ("cancel", True) in arb.calls, True)
    check("back to entry",     B.phase, "entry")

    print("== duplicate samp_no opens the dup modal ==")
    arb.fail_begin = True
    C = Slot("C"); arb.slots = [C]
    arb.goto(C, "entry")
    was = C.record["samp_no"]
    press(pool, "main_btn")                         # Begin -> begin_sample fails
    check("dup modal shown",     C.phase, "dup")
    check("dup message",         pool["modal_msg"].text, "Sample number already in use")
    check("two buttons",         pool["modal_no"].visible, True)
    check("first is New number", pool["modal_yes"].label, "New number")
    check("second is Edit",      pool["modal_no"].label, "Edit")
    check("identity left empty", C.record["user"], "")
    press(pool, "modal_no")                         # Edit -> correct it by hand
    check("back to entry",       C.phase, "entry")
    check("number kept to edit", C.record["samp_no"], was)

    print("== dup modal: New number takes the next unclaimed one ==")
    arb.claimed.append(was)          # the real dup case: someone else holds it
    press(pool, "main_btn")                         # Begin fails again
    check("dup modal shown",     C.phase, "dup")
    press(pool, "modal_yes")                        # New number
    check("back to entry",       C.phase, "entry")
    check("number moved on",     C.record["samp_no"] != was, True)
    check("still unclaimed",     C.record["db_rowid"], None)

    print("== test variant: the chain gains a screen between entry2 and vent ==")
    tv = build_phases(test_variant=True)
    check("entry2 Continue -> variant", tv["entry2"].proceed_to, "variant")
    check("entry2 NEXT previews it",    tv["entry2"].next, "variant")
    check("variant Continue -> vent",   tv["variant"].proceed_to, "vent")
    check("variant NEXT skips vent",    tv["variant"].next, "preimage")
    check("preimage <- variant",        tv["preimage"].prior, "variant")
    plain_tv = build_phases(preimage=False, test_variant=True)
    check("no pre-image: reagent1 <- variant", plain_tv["reagent1"].prior, "variant")
    check("off: no variant phase",      "variant" in build_phases(), False)
    check("off: preimage <- entry2",    build_phases()["preimage"].prior, "entry2")

    print("== test variant: a first run starts from the defaults ==")
    arb.phases = tv
    arb.fail_begin = False
    D = Slot("D"); arb.slots = [D]
    arb.goto(D, "entry"); press(pool, "main_btn")  # Begin
    press(pool, "main_btn")                         # Continue -> entry2
    D.record["samp_grams"] = "100"                  # writethrough sim
    press(pool, "main_btn")                         # Continue -> variant
    check("phase variant",       D.phase, "variant")
    check("first equipment",     pool["var_equip_btn"].label, "A Original bottle")
    check("one image set",       pool["var_reps_btn"].label, "1")
    check("factor boxes at 1.0", [pool[f].value for f in VariantPhase.FIELDS],
          ["1.0"] * 4)
    check("Continue live",       pool["main_btn"].enabled, True)
    check("footer like entry2",  (pool["prior_btn"].visible,
                                  pool["next_btn"].visible,
                                  pool["menu_btn"].visible), (True, True, True))

    print("== test variant: taps step the choices; labels reset them ==")
    press(pool, "var_equip_btn"); press(pool, "var_equip_btn")
    check("equipment stepped",   D.record["test_equip"], "C 250 Ehrl, protected")
    press(pool, "var_equip_btn")
    check("and wraps",           D.record["test_equip"], "A Original bottle")
    press(pool, "var_equip_btn")                    # leave it on B
    for _ in range(5):
        press(pool, "var_reps_btn")
    check("reps 1 -> 5 -> 1",    D.record["image_reps"], 1)
    press(pool, "var_reps_btn")
    check("reps stepped",        D.record["image_reps"], 2)
    press(pool, "var_reps_lbl")
    check("label resets reps",   D.record["image_reps"], 1)
    press(pool, "var_reps_btn")                     # leave it at 2

    print("== test variant: a bad factor holds Continue back ==")
    D.record["R1_time_factor"] = "abc"; arb.render(D)   # writethrough sim
    check("Continue held",       pool["main_btn"].enabled, False)
    from palette import color as _color
    check("bad box is yellow",   pool["var_r1_time_fld"].bg, _color("wt_missing"))
    check("good box is white",   pool["var_r1_wt_fld"].bg, _color("white"))
    D.record["R1_time_factor"] = "0"; arb.render(D)
    check("zero is refused too", pool["main_btn"].enabled, False)
    press(pool, "var_r1_lbl")
    check("label puts 1.0 back", (D.record["R1_wt_factor"], D.record["R1_time_factor"]),
          ("1.0", "1.0"))
    D.record["R1_time_factor"] = "1.5"; D.record["R2_wt_factor"] = "0.5"
    arb.render(D)
    check("Continue live again", pool["main_btn"].enabled, True)

    print("== test variant: Continue saves the treatment and goes to vent ==")
    press(pool, "main_btn")
    check("phase vent",          D.phase, "vent")
    check("treatment saved", arb.treatments[D.record["samp_no"]],
          {"test_equip": "B 250 Ehrl, unprotected", "image_reps": 2,
           "R1_wt_factor": 1.0, "R1_time_factor": 1.5,
           "R2_wt_factor": 0.5, "R2_time_factor": 1.0})
    press(pool, "vent_ok")
    check("then preimage",       D.phase, "preimage")

    print("== test variant: a look back from a later step cannot edit ==")
    press(pool, "prior_btn")                        # preview variant
    check("previewing variant",  D.phase, "variant")
    check("factors locked",      pool["var_r1_wt_fld"].enabled, False)
    check("equipment locked",    pool["var_equip_btn"].enabled, False)
    check("header offers Resume", pool["main_btn"].label, "Resume")
    press(pool, "main_btn")                         # Resume
    check("back on preimage",    D.phase, "preimage")

    print("== test variant: the next run carries the last treatment over ==")
    E = Slot("E"); arb.slots = [E]
    arb.goto(E, "entry"); press(pool, "main_btn"); press(pool, "main_btn")
    press(pool, "main_btn")                         # -> variant
    check("equipment carried",   E.record["test_equip"], "B 250 Ehrl, unprotected")
    check("reps carried",        E.record["image_reps"], 2)
    check("factors carried",     [E.record[k] for k in VariantPhase.FIELDS.values()],
          ["1.0", "1.5", "0.5", "1.0"])

    print("== test variant: the factors scale targets and countdowns ==")
    # D: 100 g sample, R1 time x1.5, R2 weight x0.5 (saved above).
    check("R1 target unscaled",  round(arb.reagent_weigh_plan(D, 1).target, 3), 2.9)
    check("R2 target halved",    round(arb.reagent_weigh_plan(D, 2).target, 3), 0.65)
    arb.goto(D, "reagent1")
    check("Target cell shows it", pool["wt2_targ_lbl"].text, "0.650")
    D.record["reagent2_initial_wt"] = "1.190"        # 1.190 - 0.540 = 0.650
    check("Over follows the scaled target",
          round(arb.reagent_weigh_plan(D, 2).overage, 3), 0.0)
    check("R1 countdown x1.5",   tv["reagent1"].duration(D, arb), 180)
    _r2 = arb.reagent2_plan(D)[0]
    check("R2 countdown x1.0",   tv["reagent2"].duration(D, arb), _r2)
    D.record["R2_time_factor"] = "2"
    check("R2 countdown x2",     tv["reagent2"].duration(D, arb), 2 * _r2)
    check("stir unchanged",      tv["reagent1"].stir_secs(D, arb), 25)
    D.record["R1_time_factor"] = "0.001"
    check("never below 1 s",     tv["reagent1"].duration(D, arb), 1)
    P = Slot("P"); P.record["samp_grams"] = "100"
    check("production: target as before",
          round(arb.reagent_weigh_plan(P, 2).target, 3), 1.3)
    check("production: countdown as before", tv["reagent1"].duration(P, arb), 120)
    arb.phases = build_phases()

    print("== image reps: each set is pre-imaged, with a cassette swap between ==")
    R = Slot("R"); arb.slots = [R]
    R.record.update(samp_no="ZP2000", image_reps=2)
    arb.goto(R, "preimage")
    check("button counts sets",  pool["run_btn"].label, "Pre-image\n1 of 2")
    check("first set: insert",   pool["instr1"].text, "Insert new strip, weigh reagent 1")
    press(pool, "run_btn")
    check("set 1 running",       R.capturing, True)
    arb.complete_preimage(R)
    check("stays on preimage",   R.phase, "preimage")
    check("set 1 is the h5file", R.record["h5file"], "fake.h5")
    check("button offers set 2", pool["run_btn"].label, "Pre-image\n2 of 2")
    check("asks for a swap",     pool["instr1"].text, "Remove & replace cassette")
    check("in yellow",           pool["instr1"].bg, _color("field_yellow"))
    press(pool, "run_btn"); arb.complete_preimage(R)
    check("last set moves on",   R.phase, "reagent1")
    check("each set its own file", R.rep_files, {1: "fake.h5", 2: "fakerep2.h5"})
    check("h5file still set 1",  R.record["h5file"], "fake.h5")

    print("== image reps: each set is measured, the reading ends after the last ==")
    arb.goto(R, "read")
    check("button counts sets",  pool["run_btn"].label, "Capture\n1 of 2")
    check("first set: insert",   pool["color_lbl"].text, "Insert strip & press Capture")
    press(pool, "run_btn")
    _start = R.record["capture_start"]
    check("set 1 starts the clock", _start, arb.epoch_s)
    arb.complete_capture(R)
    check("stays on read",       R.phase, "read")
    check("no capture_end yet",  R.record["capture_end"], None)
    check("button offers set 2", pool["run_btn"].label, "Capture\n2 of 2")
    check("asks for a swap",     pool["color_lbl"].text, "Remove & replace cassette")
    check("in yellow",           pool["color_lbl"].bg, _color("field_yellow"))
    check("manual read row kept", pool["color_fld"].visible, True)
    arb.epoch_s += 60
    press(pool, "run_btn")
    check("set 2 leaves capture_start", R.record["capture_start"], _start)
    arb.complete_capture(R)
    check("capture_end after the last", R.record["capture_end"], arb.epoch_s)
    check("h5file is set 1's",   R.record["h5file"], "fake.h5")
    check("on to the weights gate", pool["run_btn"].label, "ANALYZE")
    arb.epoch_s -= 60
    R.clear_run()
    check("a new run starts over", (R.pre_rep, R.cap_rep, R.rep_files), (0, 0, {}))

    print("== image reps: one set reads as before ==")
    S = Slot("S"); arb.slots = [S]
    arb.goto(S, "preimage")
    check("pre-image names the slot", pool["run_btn"].label, "Pre-image\nS")
    arb.goto(S, "read")
    check("capture names the slot",   pool["run_btn"].label, "Capture\nS")
    check("reading prompt in white",  pool["color_lbl"].bg, _color("white"))

    print("\n== all phase self-tests passed ==")
