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
    arb.goto(slot, phase_id)        advance slot to a phase — the live phase
                                    (runs activate, renders)
    arb.view(slot, phase_id)        preview a phase without advancing (no activate)
    arb.resume(slot)                return the view to the slot's live phase
    arb.render(slot)                re-render slot's current phase in place
    arb.request_capture(slot)->bool start a capture if the camera is free
    arb.capture_available()->bool   is the (single) camera free right now
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
        ts = slot.record.get("collect", "")
        if ts:
            pool.put("collect_line", f"Collected  {ts}")

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

        pool.put("title", self.title or slot.record.get("id", ""))
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


# ─────────────────────────────────────────────────────────────────────────────
# Entry
# ─────────────────────────────────────────────────────────────────────────────

class EntryPhase(Phase):
    """Sample entry: fill the form, press Begin."""

    phase_id = "entry"
    prior    = None
    next     = "reagent1"
    title    = "New Test"

    # field widget -> record key; the arbiter writes edits straight through to
    # the record on each keystroke, so the record is always the source of truth.
    FIELDS = {"id_fld": "id", "user_fld": "user",
              "test_fld": "test", "collect_fld": "collect"}

    def enter(self, pool, slot, arb) -> None:
        r = slot.record
        pool.put("id_lbl", "ID");        pool.put("id_fld", r["id"])
        pool.put("user_lbl", "User");    pool.put("user_fld", r["user"])
        pool.put("test_lbl", "Test");    pool.put("test_fld", r["test"])
        pool.put("collect_lbl", "Collect"); pool.put("collect_fld", r["collect"])
        pool.show("div1", "div2", "div3")
        self._chrome(pool, slot, arb, "Begin",
                     main_on=lambda: self.begin(arb, slot),
                     main_enabled=slot.ready_to_begin())

    def begin(self, arb, slot) -> None:
        if not slot.record.get("collect"):
            slot.record["collect"] = arb.timestamp()
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
                 armed: Tuple[str, str], running: Tuple[str, str]):
        self.phase_id = phase_id
        self.secs     = secs
        self.next     = next_phase
        self.prior    = prior
        self.armed    = armed        # (instr1, instr2) while waiting for START
        self.running  = running      # (instr1, instr2) while counting down

    def enter(self, pool, slot, arb) -> None:
        # Show the live countdown only when this reagent is the slot's live phase;
        # while it is merely being previewed, show the armed duration instead.
        is_live = slot.active_phase == self.phase_id
        pool.put("caption", "Remaining")
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
            pool.put("run_btn", "START", on=lambda: self.start(arb, slot))
            i1, i2 = self.armed
        pool.put("instr1", i1)
        pool.put("instr2", i2)
        self._show_collect(pool, slot)
        # A running countdown does NOT lock nav: the timer keeps running in the
        # background (arbiter polls it and alarms on expiry, same as an A/B
        # toggle), and Resume brings the view back.
        self._chrome(pool, slot, arb, "CANCEL",
                     main_on=lambda: arb.open_modal(slot, "cancel"))

    def start(self, arb, slot) -> None:
        slot.timer   = TimerState.down(self.secs, arb.now())
        slot.running = True
        arb.render(slot)

    def on_expire(self, arb, slot) -> None:
        slot.running = False
        slot.timer   = None
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
        pool.put("caption", "Elapsed")
        if is_live and slot.timer is not None:
            pool["timer"].bind(slot.timer, interactive=False)
        else:
            preview = TimerState.up(arb.now())
            preview.pause(arb.now())
            pool["timer"].bind(preview, interactive=False)
        pool.put("timer")
        pool.put("color_lbl", "Read color")
        pool.put("color_fld", slot.record.get("manual_color", ""))
        if is_live and slot.capturing:
            pool.put("instr1", slot.progress or "Capturing...")
            pool.put("run_btn", "RUNNING", enabled=False)
            pool.put("instr2", "Do not touch strip")
        else:
            pool.put("run_btn", "CAPTURE",
                     on=lambda: self.capture(arb, slot, pool),
                     enabled=is_live and arb.capture_available())
            pool.put("instr2", "Insert strip & press CAPTURE")
        self._show_collect(pool, slot)
        # A capture in flight does NOT lock nav either: it is arbiter-owned and
        # runs to completion regardless of what's on screen (same as an A/B
        # toggle), advancing to analysis when done; Resume returns to it.
        self._chrome(pool, slot, arb, "CANCEL",
                     main_on=lambda: arb.open_modal(slot, "cancel"))

    def capture(self, arb, slot, pool) -> None:
        slot.record["manual_color"] = pool["color_fld"].value
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
        pool.put("caption", "Final")
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
    A confirm dialog.  `message` may reference record fields (e.g. "{id}").
    `yes_action` is a callable(arb, slot); the No button always closes back to
    the phase that opened the modal.
    """

    def __init__(self, phase_id: str, message: str,
                 yes_label: str, yes_action, no_label: str = "Resume"):
        self.phase_id   = phase_id
        self.message    = message
        self.yes_label  = yes_label
        self.yes_action = yes_action
        self.no_label   = no_label

    def enter(self, pool, slot, arb) -> None:
        try:
            msg = self.message.format(**slot.record)
        except (KeyError, IndexError):
            msg = self.message
        pool.put("modal_panel")
        pool.put("modal_msg", msg)
        pool.put("modal_yes", self.yes_label, on=lambda: self.yes_action(arb, slot))
        pool.put("modal_no",  self.no_label,  on=lambda: arb.close_modal(slot))


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

def build_phases(reagent1_secs: int = 120, reagent2_secs: int = 420) -> dict:
    """
    Return {phase_id: Phase}, the whole protocol.

    reagent1_secs / reagent2_secs let the caller shorten the reagent countdowns
    (the arbiter passes 20 s each in simulation so runs are quick to test).
    """
    phases = [
        EntryPhase(),
        ReagentPhase("reagent1", reagent1_secs, "reagent2", prior="entry",
                     armed=("Add Reagent 1", "and press START"),
                     running=("Stir 20 sec, then rest", "Prepare reagent 2 and strip")),
        ReagentPhase("reagent2", reagent2_secs, "read", prior="reagent1",
                     armed=("Add reagent 2 and cap/strip", "Ventilate and press START"),
                     running=("Stir 20 sec, then rest", "Ventilate")),
        ReadPhase(),
        AnalysisPhase(),
        ModalPhase("cancel", "Cancel test {id}?", "Cancel test",
                   yes_action=lambda arb, slot: arb.cancel_test(slot), no_label="Resume"),
        ModalPhase("reset", "Reset the timer?", "Reset",
                   yes_action=lambda arb, slot: arb.reset_timer(slot), no_label="No"),
        ModalPhase("nosample", "INSERT SAMPLE AND CONTINUE", "CAPTURE",
                   yes_action=lambda arb, slot: arb.retry_capture(slot), no_label="RETURN"),
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

    class MockArbiter:
        """Minimal stand-in exercising the whole phase contract."""
        def __init__(self, pool, display):
            self.pool, self.display = pool, display
            self.phases = build_phases()
            self.clock = 1000.0
            self.capturing_slot = None
            self.calls = []

        # clock / stamp
        def now(self):        return self.clock
        def timestamp(self):  return "2026-07-20 14:00:00"

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
            slot.phase, slot.modal_return = slot.modal_return, None
            self.render(slot)

        # capture / analysis
        def capture_available(self):    return self.capturing_slot is None
        def request_capture(self, slot):
            if self.capturing_slot is None:
                self.capturing_slot = slot
                return True
            return False
        def complete_capture(self, slot):        # driven by the loop in real life
            slot.timer.stop(self.now())
            slot.capturing = False
            self.capturing_slot = None
            self.goto(slot, "analysis")
        def start_analysis(self, slot):  self.calls.append("start_analysis")
        def analysis_done(self, slot, text):
            slot.result = text
            self.render(slot)

        # poll for expiry (arithmetic each loop pass)
        def poll(self):
            now = self.now()
            for slot in self.slots:
                t = slot.timer
                if t is not None and t.expired(now):
                    slot.timer = None
                    self.alarm(slot)
                    self.phases[slot.phase].on_expire(self, slot)

        # actions invoked by buttons / modals
        def alarm(self, slot):        self.calls.append(("alarm", slot.name))
        def cancel_test(self, slot):  self.calls.append("cancel"); self.goto(slot, "entry")
        def reset_timer(self, slot):
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

    print("== entry: Begin disabled until id+user ==")
    arb.goto(A, "entry")
    check("main_btn label",    pool["main_btn"].label, "Begin")
    check("Begin disabled",    pool["main_btn"].enabled, False)
    A.record["id"] = "AB0124"; A.record["user"] = "Bill"   # arbiter write-through
    arb.render(A)
    check("Begin enabled",     pool["main_btn"].enabled, True)

    print("== begin -> reagent1 armed ==")
    press(pool, "main_btn")
    check("phase",             A.phase, "reagent1")
    check("collect stamped",   bool(A.record["collect"]), True)
    check("run_btn label",     pool["run_btn"].label, "START")
    check("timer not interactive (armed)", pool["timer"].interactive, False)
    check("instr1",            pool["instr1"].text, "Add Reagent 1")
    check("main is CANCEL",    pool["main_btn"].label, "CANCEL")
    check("NEXT enabled (armed)", pool["next_btn"].enabled, True)

    print("== START -> running; nav stays open (preview + Resume) ==")
    press(pool, "run_btn")
    check("running",           A.running, True)
    check("run_btn is RESET",  pool["run_btn"].label, "RESET")
    check("timer interactive", pool["timer"].interactive, True)
    check("NEXT open while running", pool["next_btn"].enabled, True)

    print("== reset modal round-trip ==")
    press(pool, "run_btn")                         # RESET -> reset modal
    check("phase is modal",    A.phase, "reset")
    check("modal msg",         pool["modal_msg"].text, "Reset the timer?")
    press(pool, "modal_no")                        # No -> back to running reagent1
    check("back to reagent1",  A.phase, "reagent1")
    check("still running",     A.running, True)
    press(pool, "run_btn"); press(pool, "modal_yes")   # RESET -> Reset confirmed
    check("timer cleared",     A.timer, None)
    check("armed again",       A.running, False)

    print("== run to expiry -> reagent2 ==")
    press(pool, "run_btn")                         # START reagent1
    arb.clock += 121                               # past the 120 s deadline
    arb.poll()
    check("advanced to reagent2", A.phase, "reagent2")
    check("alarm sounded",     ("alarm", "A") in arb.calls, True)
    check("reagent2 armed START", pool["run_btn"].label, "START")

    print("== reagent2 -> read (capture) ==")
    press(pool, "run_btn")                         # START reagent2
    arb.clock += 421
    arb.poll()
    check("advanced to read",  A.phase, "read")
    check("count-up running",  A.running, True)
    check("CAPTURE enabled",   pool["run_btn"].enabled, True)
    pool.set("color_fld", "pale blue")             # operator types a reading
    press(pool, "run_btn")                         # CAPTURE
    check("manual colour saved", A.record["manual_color"], "pale blue")
    check("capturing",         A.capturing, True)
    check("run_btn RUNNING disabled", pool["run_btn"].enabled, False)
    check("nav open while capturing", pool["prior_btn"].enabled, True)

    print("== capture completes -> analysis -> done ==")
    arb.clock += 30
    arb.complete_capture(A)
    check("phase analysis",    A.phase, "analysis")
    check("start_analysis ran","start_analysis" in arb.calls, True)
    check("POST disabled",     pool["run_btn"].enabled, False)
    check("result shows progress", pool["result_fld"].value, "Analyzing ...")
    arb.analysis_done(A, "Nitrate 12 ppm")
    check("result shown",      pool["result_fld"].value, "Nitrate 12 ppm")
    check("POST enabled",      pool["run_btn"].enabled, True)
    check("main is NEXT",      pool["main_btn"].label, "NEXT")

    print("== NEXT -> fresh entry ==")
    press(pool, "main_btn")
    check("back to entry",     A.phase, "entry")
    check("record cleared",    A.record["id"], "")

    print("\n== all phase self-tests passed ==")
