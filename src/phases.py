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
    arb.reagent_weigh_plan(slot, n) (initial_valid, packet_net, target_wt,
                                    added_wt) for reagent n's weight validation
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
# relative to where the run actually is (slot.active_phase).  "preimage" is in
# the sequence whether or not the chain includes it: it only has to sort in the
# right place for the chips, and an id that never comes up costs nothing.
_STEP_SEQUENCE = ("entry", "entry2", "preimage",
                  "reagent1", "reagent2", "read", "analysis")
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
    or a yellow line can leak into a later state that should be plain.
    """
    pool.put(name, text)
    pool.recolor(name, bg=("field_yellow" if yellow else "white"))
    if size is not None:
        pool[name].size = size


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

    FIELDS = {"temp_fld": "samp_degC", "weight_fld": "samp_grams",
              "collectdt_fld": "collect_dt", "remark_fld": "remark"}

    def enter(self, pool, slot, arb) -> None:
        r = slot.record
        # Unclaimed, this screen is only reachable as a NEXT preview from screen
        # 1 — visible, so the operator can see what is coming, but with nothing
        # to type into until the slot owns a row to put it in.
        claimed = r.get("db_rowid") is not None
        # Temp label reads the DS18B20; Collect label stamps the current time.
        # Weight has no source yet — its label is an inert placeholder.
        self._row(pool, "temp_lbl", self._temp_label(arb),
                  "temp_fld", _text(r.get("samp_degC")),
                  on=lambda: self.fill_temp(arb, slot), enabled=claimed)
        self._row(pool, "weight_lbl", "Weight g",
                  "weight_fld", _text(r.get("samp_grams")), enabled=claimed)
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
        arb.goto(slot, self.next)


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
            pool["run_btn"].size = 18
            pool.put("instr1", "Imaging - DO NOT TOUCH")
            # Keep weight instruction visible even while imaging
            _line(pool, "instr2", "Weigh reagent 1 packet:")
        else:
            pool.put("run_btn", f"Pre-image\n{slot.name}",
                     on=lambda: self.capture(arb, slot),
                     enabled=is_live and arb.capture_available())
            pool["run_btn"].size = 18
            pool.put("instr1", "Insert new strip in meter")
            _line(pool, "instr2", "Weigh reagent 1 packet:")

            # instr3: "Do not add reagent 1 yet" while invalid; otherwise show net/target
            initial_valid, packet_net, target_wt, _ = arb.reagent_weigh_plan(slot, 1)
            if initial_valid:
                pool.put("instr3",
                        f"R1: Net {packet_net:4.3f}g. Add {target_wt:4.3f} g")
            else:
                pool.put("instr3", "Do not add reagent 1 yet")

        # Show reagent 1 weight row; hide reagent 2 row
        # In PreImagePhase, always show initial weight field (never flip to final)
        pool.put("reagent1_wt_lbl", "Initial", enabled=is_live)
        pool.put("reagent1_wt_fld", slot.record.get("reagent1_initial_wt", ""), enabled=is_live)
        pool.show("reagent1_wt_lbl", "reagent1_wt_fld")
        pool.hide("reagent2_wt_lbl", "reagent2_wt_fld")

        self._show_steps(pool, slot)
        self._chrome(pool, slot, arb, "CANCEL",
                     main_on=lambda: arb.open_modal(slot, "cancel"))

    def _leds_line(self) -> str:
        """
        The instr3 line naming what light this step will put on the strip.

        Worth the line: the whole design of this step is about how little of
        it there is, and an operator changing capture.preimage_led_order in
        Menu should be able to see the change land here.  A configuration
        that cannot be read is not gone unread by chance, so a failure to
        reach the database leaves the line blank rather than the run stopped.
        """
        try:
            import config_repo as config
            return ", ".join(config.capture_preimage_led_order())
        except Exception:                                  # noqa: BLE001
            log.exception("preimage: cannot read the LED list")
            return ""

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

    Four states per the weight-entry workflow:
    1. Idle, initial_valid false: empty weight row, no START button
    2. Idle, initial_valid true: flip to final weight row, START button shown
    3. Running, before stir: show next reagent's initial-weight row
    4. Running, after stir fired: show stop-stirring message

    Full-duration expiry still advances to `next` regardless of weight state.
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

    def duration(self, slot, arb) -> int:
        """
        Seconds this step counts down for `slot`.

        Fixed here, and read fresh on every render and every START rather than
        cached, so a duration edited in Menu takes effect on the next run
        without a rebuild.  Overridden by Reagent2Phase, whose answer depends on
        the sample.
        """
        return self.secs

    def stir_secs(self, slot, arb) -> int:
        """
        Stir duration in seconds for this step.
        Reagent1 returns reagent1_stir_seconds(); Reagent2Phase overrides.
        """
        return arb.reagent1_stir_seconds()

    def _basis(self, slot, arb) -> str:
        """
        The instr3 line explaining where this step's duration came from, or ""
        to leave that line blank.  Only reagent 2 has anything to explain.
        """
        return ""

    def enter(self, pool, slot, arb) -> None:
        is_live = slot.active_phase == self.phase_id
        n = 1 if "reagent1" in self.phase_id else 2

        # Weight entry state
        initial_valid, packet_net, target_wt, added_wt = arb.reagent_weigh_plan(slot, n)

        if is_live and slot.running:
            # Running: countdown is active
            pool["timer_lbl"].size = _TIMER_LBL_SIZE_REAGENT
            pool["timer"].bind(slot.timer, interactive=True)
            pool.put("timer")
            pool.put("run_btn", "RESET", on=lambda: arb.open_modal(slot, "reset"))

            # Stir sub-alarm text change
            if slot.stir_fired:
                _line(pool, "instr1", "Stop stirring, VENTILATE")
            else:
                _line(pool, "instr1", self.running[0])
            _line(pool, "instr2", self.running[1], yellow=(n == 2))
            pool.put("instr3", self.running[2] if len(self.running) > 2 else "")

            # Show appropriate weight row (next reagent's initial while running)
            if n == 1:
                # Reagent 1 running: show reagent 2's initial row
                pool.put("reagent1_wt_lbl", "Final", enabled=is_live)
                pool.put("reagent1_wt_fld", slot.record.get("reagent1_final_wt", ""), enabled=is_live)
                pool.show("reagent1_wt_lbl", "reagent1_wt_fld")
                pool.put("reagent2_wt_lbl", "Initial", enabled=is_live)
                pool.put("reagent2_wt_fld", slot.record.get("reagent2_initial_wt", ""), enabled=is_live)
                pool.show("reagent2_wt_lbl", "reagent2_wt_fld")
            else:
                # Reagent 2 running: show reagent 2's final row
                pool.put("reagent1_wt_lbl", "Final", enabled=is_live)
                pool.put("reagent1_wt_fld", slot.record.get("reagent1_final_wt", ""), enabled=is_live)
                pool.show("reagent1_wt_lbl", "reagent1_wt_fld")
                pool.put("reagent2_wt_lbl", "Final", enabled=is_live)
                pool.put("reagent2_wt_fld", slot.record.get("reagent2_final_wt", ""), enabled=is_live)
                pool.show("reagent2_wt_lbl", "reagent2_wt_fld")

        else:
            # Armed (idle): always show current reagent's initial weight (for potential re-weighing)
            pool.put("timer_lbl", "Remaining")
            pool["timer_lbl"].size = _TIMER_LBL_SIZE_REAGENT

            # Idle state text and button depend on initial_valid
            if initial_valid:
                # State 2: initial valid, enable START
                preview = TimerState.down(self.duration(slot, arb), arb.now())
                preview.pause(arb.now())
                pool["timer"].bind(preview, interactive=False)
                pool.put("timer")
                pool.put("run_btn", self.start_label,
                        on=lambda: self.start(arb, slot), enabled=is_live)
                pool["run_btn"].visible = is_live

                # Instr text for state 2
                _line(pool, "instr1",
                     f"Add {target_wt:4.3f}g of Reagent {n}", yellow=True)
                _line(pool, "instr2", "Weigh again after adding")
                # Show net weight from packet and target amount to add
                pool.put("instr3",
                        f"R{n}: Net {packet_net:4.3f}g. Add {target_wt:4.3f}g")
            else:
                # State 1: initial invalid, hide START button
                pool.put("timer")  # show but don't bind (empty timer)
                pool.put("run_btn", "")  # empty label
                pool["run_btn"].visible = False

                # Instr text for state 1
                pool.put("instr1", "")
                _line(pool, "instr2", "Weigh reagent 1 packet:")
                pool.put("instr3", "Before adding!")

            # Idle weight fields: always show current reagent's initial (never flip to final)
            # This allows re-weighing the packet at any time before Start is pressed
            if n == 1:
                pool.put("reagent1_wt_lbl", "Initial", enabled=is_live)
                pool.put("reagent1_wt_fld", slot.record.get("reagent1_initial_wt", ""), enabled=is_live)
                pool.show("reagent1_wt_lbl", "reagent1_wt_fld")
                pool.hide("reagent2_wt_lbl", "reagent2_wt_fld")
            else:
                pool.put("reagent1_wt_lbl", "Final", enabled=is_live)
                pool.put("reagent1_wt_fld", slot.record.get("reagent1_final_wt", ""), enabled=is_live)
                pool.show("reagent1_wt_lbl", "reagent1_wt_fld")
                pool.put("reagent2_wt_lbl", "Initial", enabled=is_live)
                pool.put("reagent2_wt_fld", slot.record.get("reagent2_initial_wt", ""), enabled=is_live)
                pool.show("reagent2_wt_lbl", "reagent2_wt_fld")

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
        return arb.reagent2_plan(slot)[0]

    def stir_secs(self, slot, arb) -> int:
        """Stir sub-alarm duration for reagent 2 (temperature-compensated)."""
        return arb.reagent2_stir_plan(slot)

    def _basis(self, slot, arb) -> str:
        """
        Say which temperature the countdown was computed from, because the
        operator is otherwise looking at a wait that changes between samples
        with nothing on screen to explain it.  A stand-in temperature is shown
        in brackets, the same convention Entry 2's Temp button uses for a value
        that was not measured.
        """
        _, temp_c, measured = arb.reagent2_plan(slot)
        shown = f"{temp_c:.1f} °C" if measured else f"[{temp_c:.1f} °C]"
        return f"{shown}  ref {_mmss(arb.t2ref25_secs())} @25 °C"

    def enter(self, pool, slot, arb) -> None:
        is_live = slot.active_phase == self.phase_id

        # Weight entry state
        initial_valid2, packet_net2, target_wt2, added_wt2 = arb.reagent_weigh_plan(slot, 2)
        initial_valid1, packet_net1, target_wt1, added_wt1 = arb.reagent_weigh_plan(slot, 1)

        if is_live and slot.running:
            # Running: countdown is active
            pool["timer_lbl"].size = _TIMER_LBL_SIZE_REAGENT
            pool["timer"].bind(slot.timer, interactive=True)
            pool.put("timer")
            pool.put("run_btn", "RESET", on=lambda: arb.open_modal(slot, "reset"))

            # Stir sub-alarm text change
            stir = self.stir_secs(slot, arb)
            if slot.stir_fired:
                _line(pool, "instr1", "Stop stirring, VENTILATE")
            else:
                if stir > 0:
                    _line(pool, "instr1", f"Stir {_mmss(stir)} and VENTILATE!")
                else:
                    _line(pool, "instr1", "Stir continuously and VENTILATE")

            # Line 2 override: show reagent 1's target/net if valid
            if added_wt1 is not None:
                _line(pool, "instr2",
                     f"R1 target {target_wt1:4.3f} Net {added_wt1:4.3f}")
            else:
                _line(pool, "instr2", "Weigh again after adding")

            # Line 3: "VENTILATE!" in larger font, with line 4 override
            if added_wt2 is not None:
                _line(pool, "instr3",
                     f"R2 target {target_wt2:4.3f} Net {added_wt2:4.3f}", size=16)
            else:
                pool["instr3"].size = 22
                _line(pool, "instr3", "VENTILATE!")

            # Show both weight rows (reagent 1 final and reagent 2 final)
            pool.put("reagent1_wt_lbl", "Final", enabled=is_live)
            pool.put("reagent1_wt_fld", slot.record.get("reagent1_final_wt", ""), enabled=is_live)
            pool.show("reagent1_wt_lbl", "reagent1_wt_fld")
            pool.put("reagent2_wt_lbl", "Final", enabled=is_live)
            pool.put("reagent2_wt_fld", slot.record.get("reagent2_final_wt", ""), enabled=is_live)
            pool.show("reagent2_wt_lbl", "reagent2_wt_fld")

        else:
            # Armed (idle)
            pool.put("timer_lbl", "Remaining")
            pool["timer_lbl"].size = _TIMER_LBL_SIZE_REAGENT

            if initial_valid2:
                # State 2: initial valid, show final row, enable START
                preview = TimerState.down(self.duration(slot, arb), arb.now())
                preview.pause(arb.now())
                pool["timer"].bind(preview, interactive=False)
                pool.put("timer")
                pool.put("run_btn", self.start_label,
                        on=lambda: self.start(arb, slot), enabled=is_live)
                pool["run_btn"].visible = is_live

                # Instr text for state 2
                _line(pool, "instr1",
                     f"Add {target_wt2:4.3f}g of Reagent 2", yellow=True)
                _line(pool, "instr2", "Weigh again after adding")
                _line(pool, "instr3",
                     f"R2: Net {packet_net2:4.3f}g. Add {target_wt2:4.3f} g")
            else:
                # State 1: initial invalid, hide START button
                pool.put("timer")  # show but don't bind (empty timer)
                pool.put("run_btn", "")  # empty label
                pool["run_btn"].visible = False

                # Instr text for state 1
                _line(pool, "instr1", "Weigh before adding")
                _line(pool, "instr2", "Weigh reagent 2 packet:")
                pool.put("instr3", "")

            # Idle weight fields: always show reagent 2's initial (never flip to final)
            # This allows re-weighing the packet at any time before Start is pressed
            pool.put("reagent1_wt_lbl", "Final", enabled=is_live)
            pool.put("reagent1_wt_fld", slot.record.get("reagent1_final_wt", ""), enabled=is_live)
            pool.show("reagent1_wt_lbl", "reagent1_wt_fld")
            pool.put("reagent2_wt_lbl", "Initial", enabled=is_live)
            pool.put("reagent2_wt_fld", slot.record.get("reagent2_initial_wt", ""), enabled=is_live)
            pool.show("reagent2_wt_lbl", "reagent2_wt_fld")

        self._show_steps(pool, slot)
        self._chrome(pool, slot, arb, "CANCEL",
                     main_on=lambda: arb.open_modal(slot, "cancel"))


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
        if arb.capture_available() and not slot.capturing:
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
        # manual_lbl ("Manually read color:") is a fixed prompt beside the
        # entry field — its text never changes, so a bare show() suffices.
        pool.show("manual_lbl")
        pool.put("color_fld", slot.record.get("manual_read", ""))
        if is_live and slot.capturing:
            pool.put("run_btn", "RUNNING", enabled=False)
            pool["run_btn"].size = 18
            pool.put("color_lbl", "Imaging - DO NOT TOUCH")
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
            pool.put("color_lbl", "Insert strip & press Capture")
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

def build_phases(reagent1_secs: int = 120, preimage: bool = True) -> dict:
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

    Args:
        reagent1_secs: Countdown for reagent step 1, in seconds.
        preimage:      Whether the protocol includes the pre-image step.
    """
    entry2 = Entry2Phase()
    entry2.next = "preimage" if preimage else "reagent1"
    phases = [
        EntryPhase(),
        entry2,
        ReagentPhase("reagent1", reagent1_secs, "reagent2",
                     prior="preimage" if preimage else "entry2",
                     armed=("Add Reagent 1", "and press START"),
                     running=("Stir 20 sec, then rest", "Prepare reagent 2 and strip"),
                     start_label="START\n1"),
        # secs=0 is never read: Reagent2Phase.duration() overrides it outright.
        Reagent2Phase("reagent2", 0, "read", prior="reagent1",
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
        # The rejected number is by definition in the table, so New number
        # always moves; Edit returns to the field to be corrected by hand.
        ModalPhase("dup", "Sample number already in use",
                   [("New number", _renumber), ("Edit", None)]),
    ]
    if preimage:
        phases.append(PreImagePhase())
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
        remark="", default_temperature=21.0,
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

        # weight entry and stir sub-alarms.  Mirrors the real arbiter.
        def reagent_weigh_plan(self, slot, n):
            prefix = f"reagent{n}_"
            empty_wt = 0.530 if n == 1 else 0.540
            ratio = 2.900 if n == 1 else 1.300

            initial_str = slot.record.get(f"{prefix}initial_wt", "")
            final_str = slot.record.get(f"{prefix}final_wt", "")

            try:
                s = str(initial_str).strip() if initial_str else ""
                initial = float(s) if s else None
            except (ValueError, TypeError):
                initial = None

            initial_valid = initial is not None and initial > empty_wt
            packet_net = (initial - empty_wt) if initial_valid else None

            sample_wt_raw = slot.record.get("samp_grams", "")
            try:
                s = str(sample_wt_raw).strip() if sample_wt_raw else ""
                sample_wt = float(s) if s else None
            except (ValueError, TypeError):
                sample_wt = None

            target_wt = None
            if initial_valid and sample_wt is not None:
                target_wt = sample_wt / 100.0 * ratio

            try:
                s = str(final_str).strip() if final_str else ""
                final = float(s) if s else None
            except (ValueError, TypeError):
                final = None

            added_wt = None
            if initial is not None and final is not None:
                added_wt = final - initial

            return initial_valid, packet_net, target_wt, added_wt

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
            self.persist(slot, h5file="fake.h5")
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

    print("== entry2: an attached probe that fails still says err ==")
    arb.temp_probe = True; arb.temp_c = None
    press(pool, "temp_lbl")
    check("failed read marked",    A.record["samp_degC"], "err")
    arb.temp_c = 21.7

    print("== entry2: collect stamp + Continue persists -> preimage ==")
    A.record["samp_degC"] = "21.5"; A.record["samp_grams"] = "3.2"   # writethrough sim
    A.record["remark"] = "cloudy sample"                             # writethrough sim
    press(pool, "collectdt_lbl")                   # Collect label stamps the datetime
    check("collect stamped",  bool(A.record["collect_dt"]), True)
    press(pool, "main_btn")                        # Continue
    check("phase preimage",   A.phase, "preimage")
    check("degC persisted (float)",  A.record["samp_degC"], 21.5)
    check("grams persisted (float)", A.record["samp_grams"], 3.2)
    check("remark persisted",        A.record["remark"], "cloudy sample")
    check("run_btn label",    pool["run_btn"].label, "Pre-image\nA")
    check("instr1",           pool["instr1"].text, "Insert new strip in meter")
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

    # Weight entry state machine: initial not set, so run_btn is hidden
    check("run_btn not shown", pool["run_btn"].visible, False)
    check("instr2 for weighing", pool["instr2"].text, "Weigh reagent 1 packet:")

    # Simulate entering initial weight to reach state 2
    A.record["reagent1_initial_wt"] = "1.850"
    arb.render(A)
    check("run_btn shown after weight", pool["run_btn"].visible, True)
    check("run_btn label",    pool["run_btn"].label, "START\n1")
    check("instr1",           pool["instr1"].text.startswith("Add"), True)

    print("== a protocol without the pre-image links entry2 straight to reagent1 ==")
    plain = build_phases(preimage=False)
    check("no preimage phase",  "preimage" in plain, False)
    check("entry2 -> reagent1", plain["entry2"].next, "reagent1")
    check("reagent1 <- entry2", plain["reagent1"].prior, "entry2")
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
    # In state 1 (no initial weight entered), instr3 is blank per the weight
    # entry workflow; the basis line appears in state 2 once weights are entered.
    check("instr3 blank in state 1",
          pool["instr3"].text, "")

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
    # In state 1 (no weight entered), instr3 is blank; enter weight to reach state 2
    A.record["reagent2_initial_wt"] = "1.650"
    arb.render(A)
    check("instr3 shows net/target in state 2", pool["instr3"].text.startswith("R2: Net"), True)

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
    arb.goto(B, "entry"); press(pool, "main_btn")  # Begin -> claims, stays on entry
    press(pool, "main_btn")                         # Continue -> entry2
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

    print("\n== all phase self-tests passed ==")
