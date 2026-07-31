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
    arb.goto(slot, phase_id)        advance slot to a phase — the live phase
                                    (runs activate, renders)
    arb.view(slot, phase_id)        preview a phase without advancing (no activate)
    arb.resume(slot)                return the view to the slot's live phase
    arb.render(slot)                re-render slot's current phase in place
    arb.request_capture(slot)->bool start a capture if the camera is free
    arb.capture_available()->bool   is the (single) camera free right now
    arb.cap_mode -> str              "calibration" | "normal" (Menu -> Capture selection)
    arb.open_modal(slot, modal_id)  push a modal, remembering the return phase
    arb.close_modal(slot)           pop back to the remembered phase
    arb.cancel_test(slot)           abandon this run
    arb.reset_timer(slot)           clear the countdown, back to armed
    arb.retry_capture(slot)         re-attempt a capture after 'no sample'
    arb.start_analysis(slot)        begin analysis of the captured images
    arb.post_results(slot)          upload the result
    arb.next_sample(slot)           advance the id and start a fresh run
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from timer import TimerState

log = logging.getLogger(__name__)


# Ordered protocol steps, used to place the reagent/capture status chips
# relative to where the run actually is (slot.active_phase).
_STEP_SEQUENCE = ("entry", "entry2", "reagent1", "reagent2", "read", "analysis")
_STEP_POS = {pid: i for i, pid in enumerate(_STEP_SEQUENCE)}

# Font size of the timer label ("Remaining"/"Elapsed"/"Final"), set per phase
# because the one widget (timer_lbl) is shared. Base size is 22 (pool.build).
_TIMER_LBL_SIZE_REAGENT  = 13   # reagents "Remaining" — ~60% of base
_TIMER_LBL_SIZE_READ     = 18   # read "Elapsed"       — ~80% of base
_TIMER_LBL_SIZE_ANALYSIS = 22   # analysis "Final"     — base

# The three progress chips shown in the reagent/capture phases:
# (widget name, the step's phase id, chip text).
_STEP_INDICATORS = (
    ("chip_a", "reagent1", "1"),
    ("chip_b", "reagent2", "2"),
    ("chip_c", "read",     "Capture"),
)


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

    # ── shared header/footer chrome ────────────────────────────────────────────

    def _chrome(self, pool, slot, arb, main_label: str, main_on,
                main_enabled: bool = True, lock_nav: bool = False) -> None:
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
        """
        if slot.phase != slot.active_phase:                 # previewing another step
            main_label, main_enabled = "Resume", True
            main_on  = lambda: arb.resume(slot)
            lock_nav = False                                # previewing is safe
            pool.enable("run_btn", False)

        pool.put("title", self.title or slot.record.get("samp_no", ""))
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
              "test_fld": "test", "test_lot_fld": "test_lot"}

    def enter(self, pool, slot, arb) -> None:
        r = slot.record
        if not r.get("samp_no"):
            self._autofill(arb, slot)     # first view of a fresh run
        pool.put("id_lbl",       "ID",       on=lambda: self.fill_id(arb, slot))
        pool.put("id_fld",       r["samp_no"])
        pool.put("user_lbl",     "User",     on=lambda: self.fill_user(arb, slot))
        pool.put("user_fld",     r["user"])
        pool.put("test_lbl",     "Test",     on=lambda: self.fill_test(arb, slot))
        pool.put("test_fld",     r["test"])
        pool.put("test_lot_lbl", "Test lot", on=lambda: self.fill_test_lot(arb, slot))
        pool.put("test_lot_fld", r["test_lot"])
        pool.show("div1", "div2", "div3")
        self._chrome(pool, slot, arb, "Begin",
                     main_on=lambda: self.begin(arb, slot),
                     main_enabled=slot.ready_to_begin())

    # ── defaults + label-button fills ───────────────────────────────────────────
    # Each row label is a Button: pressing it loads that field's default value.

    def _autofill(self, arb, slot) -> None:
        """Seed every field of a fresh run from the loaded defaults."""
        r = slot.record
        r["samp_no"] = arb.next_sample_no()
        d = arb.defaults()
        if d is not None:
            r["user"],     r["userid"]    = d.fullname, d.userid
            r["test"],     r["testlotid"] = d.testkit,  d.testlotid
            r["test_lot"]                 = d.lotno
            r["colorimeterid"]            = d.deviceid

    def fill_id(self, arb, slot) -> None:
        slot.record["samp_no"] = arb.next_sample_no()
        arb.render(slot)

    def fill_user(self, arb, slot) -> None:
        d = arb.defaults()
        if d is not None:
            slot.record["user"], slot.record["userid"] = d.fullname, d.userid
            arb.render(slot)

    def fill_test(self, arb, slot) -> None:
        d = arb.defaults()
        if d is not None:
            slot.record["test"], slot.record["testlotid"] = d.testkit, d.testlotid
            arb.render(slot)

    def fill_test_lot(self, arb, slot) -> None:
        d = arb.defaults()
        if d is not None:
            slot.record["test_lot"], slot.record["testlotid"] = d.lotno, d.testlotid
            arb.render(slot)

    def begin(self, arb, slot) -> None:
        """Reserve the sample row (claims samp_no); on a duplicate, warn instead."""
        if arb.begin_sample(slot):
            arb.goto(slot, self.next)
        else:
            arb.open_modal(slot, "dup")


class Entry2Phase(Phase):
    """Sample entry (screen 2): temperature / weight / collect datetime."""

    phase_id = "entry2"
    prior    = "entry"
    next     = "reagent1"
    title    = ""                 # header shows the sample id

    FIELDS = {"temp_fld": "samp_degC", "weight_fld": "samp_grams",
              "collectdt_fld": "collect_dt"}

    def enter(self, pool, slot, arb) -> None:
        r = slot.record
        # Temp label reads the DS18B20; Collect label stamps the current time.
        # Weight has no source yet — its label is an inert placeholder.
        pool.put("temp_lbl",      "Temp °C", on=lambda: self.fill_temp(arb, slot))
        pool.put("temp_fld",      _text(r.get("samp_degC")))
        pool.put("weight_lbl",    "Weight g")
        pool.put("weight_fld",    _text(r.get("samp_grams")))
        pool.put("collectdt_lbl", "Collect", on=lambda: self.fill_collect(arb, slot))
        pool.put("collectdt_fld", r.get("collect_dt") or "")
        pool.show("div1", "div2")
        self._chrome(pool, slot, arb, "Continue",
                     main_on=lambda: self.proceed(arb, slot))

    def fill_temp(self, arb, slot) -> None:
        """Sample the temperature sensor into the Temp field (one-press read)."""
        c = arb.read_temperature()
        # samp_degC is a string here, exactly as a typed entry would leave it
        # (proceed() converts it with _to_float); "err" marks a failed read so
        # the operator sees the press registered but no sensor responded.
        slot.record["samp_degC"] = "err" if c is None else f"{c:.1f}"
        arb.render(slot)

    def fill_collect(self, arb, slot) -> None:
        slot.record["collect_dt"] = arb.timestamp()
        arb.render(slot)

    def proceed(self, arb, slot) -> None:
        r = slot.record
        if not r.get("collect_dt"):
            r["collect_dt"] = arb.timestamp()
        arb.persist(slot, collect_dt=r["collect_dt"],
                    samp_degC=_to_float(r.get("samp_degC")),
                    samp_grams=_to_float(r.get("samp_grams")))
        arb.goto(slot, self.next)


# ─────────────────────────────────────────────────────────────────────────────
# Reagent  (armed <-> running, two instances)
# ─────────────────────────────────────────────────────────────────────────────

class ReagentPhase(Phase):
    """
    A timed reagent step.  Armed shows the duration and a START button; running
    counts down, swaps START for RESET, and locks navigation.  Reaching zero
    alarms and advances to `next`.
    """

    def __init__(self, phase_id: str, secs: int, next_phase: str, prior: str,
                 armed: Tuple[str, str], running: Tuple[str, str],
                 start_label: str = "START"):
        self.phase_id    = phase_id
        self.secs        = secs
        self.next        = next_phase
        self.prior       = prior
        self.armed       = armed        # (instr1, instr2) while waiting for START
        self.running     = running      # (instr1, instr2) while counting down
        self.start_label = start_label  # armed START button text (may be multi-line)

    def enter(self, pool, slot, arb) -> None:
        # Show the live countdown only when this reagent is the slot's live phase;
        # while it is merely being previewed, show the armed duration instead.
        is_live = slot.active_phase == self.phase_id
        pool.put("timer_lbl", "Remaining")
        pool["timer_lbl"].size = _TIMER_LBL_SIZE_REAGENT
        if is_live and slot.running:
            pool["timer"].bind(slot.timer, interactive=True)
            pool.put("timer")
            pool.put("run_btn", "RESET", on=lambda: arb.open_modal(slot, "reset"))
            i1, i2 = self.running
        else:
            # Armed: a paused, display-only preview at the full duration.
            preview = TimerState.down(self.secs, arb.now())
            preview.pause(arb.now())
            pool["timer"].bind(preview, interactive=False)
            pool.put("timer")
            pool.put("run_btn", self.start_label, on=lambda: self.start(arb, slot))
            i1, i2 = self.armed
        pool.put("instr1", i1)
        pool.put("instr2", i2)
        self._show_steps(pool, slot)
        self._show_collect(pool, slot)
        # A running countdown does NOT lock nav: the timer keeps running in the
        # background (arbiter polls it and alarms on expiry, same as an A/B
        # toggle), and Resume brings the view back.
        self._chrome(pool, slot, arb, "CANCEL",
                     main_on=lambda: arb.open_modal(slot, "cancel"))

    def start(self, arb, slot) -> None:
        slot.timer   = TimerState.down(self.secs, arb.now())
        slot.running = True
        arb.persist(slot, **{f"{self.phase_id}_start": arb.epoch()})
        arb.render(slot)

    def on_expire(self, arb, slot) -> None:
        slot.running = False
        slot.timer   = None
        arb.persist(slot, **{f"{self.phase_id}_end": arb.epoch()})
        arb.goto(slot, self.next)


# ─────────────────────────────────────────────────────────────────────────────
# Read strip  (count-up; CAPTURE)
# ─────────────────────────────────────────────────────────────────────────────

class ReadPhase(Phase):
    """
    Read the strip: an elapsed timer runs from entry, the operator may type a
    manual colour reading, and CAPTURE starts the camera sweep.  While a capture
    is in flight the button reads RUNNING and navigation locks.
    """

    phase_id = "read"
    prior    = "reagent2"
    next     = None            # advance happens on capture completion

    def activate(self, arb, slot) -> None:
        slot.timer   = TimerState.up(arb.now())
        slot.running = True

    def enter(self, pool, slot, arb) -> None:
        # When merely previewing this step (not the live phase) the slot's timer
        # belongs to another phase, so show a neutral 00:00 elapsed instead.
        is_live = slot.active_phase == self.phase_id
        pool.put("timer_lbl", "Elapsed")
        pool["timer_lbl"].size = _TIMER_LBL_SIZE_READ
        if is_live and slot.timer is not None:
            pool["timer"].bind(slot.timer, interactive=False)
        else:
            preview = TimerState.up(arb.now())
            preview.pause(arb.now())
            pool["timer"].bind(preview, interactive=False)
        pool.put("timer")
        # The wide Read-color label carries the instruction too (multi-line), so
        # there is no separate instr line to overlap it. The entry sits to its right.
        pool.put("color_fld", slot.record.get("manual_read", ""))
        if is_live and slot.capturing:
            pool.put("run_btn", "RUNNING", enabled=False)
            pool["run_btn"].size = 18
            pool.put("color_lbl", "Capturing...\nDon't touch strip.\nRead color:")
        else:
            # Reflect what a press will actually do — the Menu -> Capture
            # screen's Path selection governs this capture too, so a stale
            # "CAPTURE" label can no longer hide a full calibration sweep.
            label = ("Calibrate\nExposures" if arb.cap_mode == "calibration"
                     else f"Capture\n{slot.name}")
            pool.put("run_btn", label,
                     on=lambda: self.capture(arb, slot, pool),
                     enabled=is_live and arb.capture_available())
            pool["run_btn"].size = 18
            pool.put("color_lbl", "Insert strip &\npress CAPTURE.\nRead color:")
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


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

def build_phases(reagent1_secs: int = 120, reagent2_secs: int = 420) -> dict:
    """
    Return {phase_id: Phase}, the whole protocol.

    reagent1_secs / reagent2_secs let the caller set the reagent countdowns; the
    arbiter passes the configured durations (config_manager.reagent{1,2}_secs),
    which the simulation profile shortens so runs are quick to test and the
    Menu -> Config screen overrides on the instrument itself.  That screen
    rebuilds the phases through this function, so a new duration takes effect
    without a restart.
    """
    phases = [
        EntryPhase(),
        Entry2Phase(),
        ReagentPhase("reagent1", reagent1_secs, "reagent2", prior="entry2",
                     armed=("Add Reagent 1", "and press START"),
                     running=("Stir 20 sec, then rest", "Prepare reagent 2 and strip"),
                     start_label="START\n1"),
        ReagentPhase("reagent2", reagent2_secs, "read", prior="reagent1",
                     armed=("Add reagent 2 and cap/strip", "Ventilate and press START"),
                     running=("Stir 20 sec, then rest", "Ventilate"),
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
        ModalPhase("dup", "Sample number already in use",
                   [("OK", None)]),
    ]
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

    import sample_repo

    DEFAULTS = sample_repo.load_defaults(1)
    if DEFAULTS is None:
        raise SystemExit("self-test: no default_gui_vals row with rowid=1 "
                          "in the database — populate the `defaults` table first.")

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
            self.fail_begin = False       # flip on to simulate a duplicate samp_no
            self.temp_c = 21.7            # canned DS18B20 reading

        # clock / stamps
        def now(self):        return self.clock
        def timestamp(self):  return "2026-07-20 14:00:00"
        def epoch(self):      return self.epoch_s
        def read_temperature(self):  return self.temp_c

        # database / sample lifecycle
        def defaults(self):        return DEFAULTS
        def next_sample_no(self):  return "ZZ1001"
        def begin_sample(self, slot):
            if self.fail_begin:
                return False
            slot.record["db_rowid"] = 42
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

        # capture / analysis
        def capture_available(self):    return self.capturing_slot is None
        def request_capture(self, slot):
            if self.capturing_slot is None:
                self.capturing_slot = slot
                self.persist(slot, capture_start=self.epoch(),
                             manual_read=slot.record.get("manual_read", ""))
                return True
            return False
        def complete_capture(self, slot):        # driven by the loop in real life
            slot.timer.stop(self.now())
            slot.capturing = False
            self.capturing_slot = None
            self.persist(slot, capture_end=self.epoch(), h5file="fake.h5")
            self.goto(slot, "analysis")
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

        # actions invoked by buttons / modals
        def alarm(self, slot):        self.calls.append(("alarm", slot.name))
        def cancel_test(self, slot, delete=False):
            self.calls.append(("cancel", delete)); slot.clear_run(); self.goto(slot, "entry")
        def reset_timer(self, slot):
            ap = slot.active_phase
            if ap in ("reagent1", "reagent2"):
                self.persist(slot, **{f"{ap}_start": None})
            slot.timer = None; slot.running = False
            self.close_modal(slot)
        def retry_capture(self, slot):
            self.close_modal(slot)
        def post_results(self, slot): self.calls.append("post")
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

    print("== entry: autofills from defaults, Begin enabled ==")
    arb.goto(A, "entry")
    check("main_btn label",       pool["main_btn"].label, "Begin")
    check("id autofilled",        pool["id_fld"].value, "ZZ1001")
    check("user autofilled",      pool["user_fld"].value, DEFAULTS.fullname)
    check("test autofilled",      pool["test_fld"].value, DEFAULTS.testkit)
    check("test-lot autofilled",  pool["test_lot_fld"].value, DEFAULTS.lotno)
    check("Begin enabled",        pool["main_btn"].enabled, True)
    check("userid carried",       A.record["userid"], DEFAULTS.userid)
    check("testlotid carried",    A.record["testlotid"], DEFAULTS.testlotid)
    check("colorimeterid carried", A.record["colorimeterid"], DEFAULTS.deviceid)

    print("== label-button refills a cleared field ==")
    A.record["user"] = ""; arb.render(A)
    check("user cleared",   pool["user_fld"].value, "")
    press(pool, "user_lbl")                        # User label = load-default button
    check("user refilled",  pool["user_fld"].value, DEFAULTS.fullname)

    print("== Begin reserves the row -> entry2 ==")
    press(pool, "main_btn")
    check("phase entry2",      A.phase, "entry2")
    check("row reserved",      A.record["db_rowid"], 42)
    check("main is Continue",  pool["main_btn"].label, "Continue")
    check("temp label shown",  pool["temp_lbl"].visible, True)

    print("== entry2: Temp label reads the sensor into temp_fld ==")
    press(pool, "temp_lbl")                        # Temp label = sensor-read button
    check("temp read into field",  pool["temp_fld"].value, "21.7")
    check("temp stored in record", A.record["samp_degC"], "21.7")

    print("== entry2: collect stamp + Continue persists -> reagent1 ==")
    A.record["samp_degC"] = "21.5"; A.record["samp_grams"] = "3.2"   # writethrough sim
    press(pool, "collectdt_lbl")                   # Collect label stamps the datetime
    check("collect stamped",  bool(A.record["collect_dt"]), True)
    press(pool, "main_btn")                        # Continue
    check("phase reagent1",   A.phase, "reagent1")
    check("degC persisted (float)",  A.record["samp_degC"], 21.5)
    check("grams persisted (float)", A.record["samp_grams"], 3.2)
    check("run_btn label",    pool["run_btn"].label, "START\n1")
    check("instr1",           pool["instr1"].text, "Add Reagent 1")
    check("main is CANCEL",   pool["main_btn"].label, "CANCEL")

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

    print("== reagent2 -> read (capture) ==")
    press(pool, "run_btn")                         # START reagent2
    arb.clock += 421
    arb.poll()
    check("advanced to read",  A.phase, "read")
    check("CAPTURE enabled",   pool["run_btn"].enabled, True)
    pool.set("color_fld", "pale blue")             # operator types a reading
    press(pool, "run_btn")                         # CAPTURE
    check("manual read saved",     A.record["manual_read"], "pale blue")
    check("capture_start stamped", A.record["capture_start"], arb.epoch_s)
    check("capturing",             A.capturing, True)

    print("== capture completes -> analysis -> done ==")
    arb.clock += 30
    arb.complete_capture(A)
    check("phase analysis",     A.phase, "analysis")
    check("capture_end stamped", A.record["capture_end"], arb.epoch_s)
    check("h5file stamped",     A.record["h5file"], "fake.h5")
    arb.analysis_done(A, "Nitrate 12 ppm")
    check("result persisted",   A.record["txt_result"], "Nitrate 12 ppm")
    check("main is NEXT",       pool["main_btn"].label, "NEXT")

    print("== cancel offers 3 choices; Delete row deletes ==")
    B = Slot("B"); arb.slots = [B]
    arb.goto(B, "entry"); press(pool, "main_btn")  # Begin -> entry2 (reserved)
    press(pool, "main_btn")                         # Continue -> reagent1
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
    press(pool, "main_btn")                         # Begin -> begin_sample fails
    check("dup modal shown",   C.phase, "dup")
    check("dup message",       pool["modal_msg"].text, "Sample number already in use")
    check("single button",     pool["modal_no"].visible, False)
    press(pool, "modal_yes")                        # OK -> close back to entry
    check("back to entry",     C.phase, "entry")

    print("\n== all phase self-tests passed ==")
