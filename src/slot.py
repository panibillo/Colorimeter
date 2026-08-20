"""
slot.py  —  Slot: one live run of the test protocol.
-----------------------------------------------------
A Slot is pure data — the mutable state of one sample's run through the phases.
There are two (A and B); both stay live even while backgrounded.  The shared
Pool (the view) renders whichever slot is focused; the Slot never touches
widgets or hardware.

What survives in the background is here: the record being collected, the
current phase, and the TimerState (an absolute monotonic deadline, so a
backgrounded countdown stays correct with no ticking).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from timer import TimerState


def _new_record() -> dict:
    """
    A blank data record.  Keys are aligned to the `samples` table columns where
    they persist (see sample_repo), plus a few display-only fields.
    """
    return {
        # ── display fields shown on the entry screens ───────────────────────
        "samp_no":       "",   # id_fld        -> samples.samp_no
        "user":          "",   # user_fld      -> samples.user
        "test_kit":      "",   # test_fld      -> samples.test_kit
        "test_lot":      "",   # test_lot_fld  -> samples.test_lot
        "collect_dt":    "",   # entry2        -> samples.collect_dt
        "samp_degC":     "",   # entry2 temp   -> samples.samp_degC
        "samp_grams":    "",   # entry2 weight -> samples.samp_grams
        "manual_read":   "",   # read screen   -> samples.manual_read
        # ── reagent weight entry (display-only: initial/final in-memory only) ────
        "reagent1_initial_wt": "",  # reagent1_wt_fld while accumulating initial weight
        "reagent1_final_wt":   "",  # reagent1_wt_fld after initial is valid
        "reagent2_initial_wt": "",  # reagent2_wt_fld while accumulating initial weight
        "reagent2_final_wt":   "",  # reagent2_wt_fld after initial is valid
        # ── identity carried from the loaded defaults ───────────────────────
        # Provenance travels by value: the strings above and the serial below
        # are stored on the sample as they read at the time, not as FKs.
        "db_rowid":      None,  # samples.rowid, once reserved at Begin
        "instrument_sn": "",    # samples.instrument_sn (device.hardware_sn)
        # ── milestone timestamps (epoch seconds) -> samples.* ───────────────
        "reagent1_start": None, "reagent1_end": None,
        "reagent2_start": None, "reagent2_end": None,
        "capture_start":  None, "capture_end":  None,
        # ── reagent net weights (final - initial) -> samples.* ──────────────
        "reagent1_g":     None, # samples.reagent1_g (net grams added)
        "reagent2_g":     None, # samples.reagent2_g (net grams added)
        # ── stir sub-alarm epoch timestamps -> samples.* ──────────────────
        "reagent1_stir_end": None,  # samples.reagent1_stir_end (when sub-alarm fired)
        "reagent2_stir_end": None,  # samples.reagent2_stir_end (when sub-alarm fired)
        # ── results ─────────────────────────────────────────────────────────
        "txt_result":    "",   # samples.txt_result
        "num_result":    None, # samples.num_result
        "h5file":        "",   # samples.h5file (capture archive path)
    }


@dataclass
class Slot:
    """
    The state of one protocol run.

    Fields
    ------
    name         : 'A' or 'B'
    record       : the data being collected (see _new_record)
    phase        : viewed phase id — which screen is on display (key into phases)
    active_phase : live phase id — where the test actually is.  Equals `phase`
                   except while the user previews other steps with PRIOR/NEXT;
                   the header offers Resume to jump back to it.
    running      : a countdown/count-up timer is active (armed vs running)
    capturing    : a camera capture for this slot is in flight
    timer        : the active TimerState, or None
    progress     : latest capture/analysis status line
    result       : final analysis result ('' until analysis completes)
    modal_return : phase id to return to when a modal closes
    stir_deadline    : monotonic deadline for the next stir sub-alarm, or None
    stir_role        : "reagent1" | "reagent2" | None — which countdown armed the sub-alarm
    stir_fired       : True once the sub-alarm has fired, used to update instr text
    reagent1_wt_target : record key ("reagent1_initial_wt" or "reagent1_final_wt")
                        which the reagent1_wt_fld currently writes to
    reagent2_wt_target : record key ("reagent2_initial_wt" or "reagent2_final_wt")
                        which the reagent2_wt_fld currently writes to
    """

    name:         str
    record:       dict                 = field(default_factory=_new_record)
    phase:        str                  = "entry"
    active_phase: str                  = "entry"
    running:      bool                 = False
    capturing:    bool                 = False
    timer:        Optional[TimerState] = None
    progress:     str                  = ""
    result:       str                  = ""
    modal_return: Optional[str]        = None
    stir_deadline:      Optional[float] = None
    stir_role:          Optional[str]   = None
    stir_fired:         bool            = False
    reagent1_wt_target: str             = "reagent1_initial_wt"
    reagent2_wt_target: str             = "reagent2_initial_wt"

    def ready_to_begin(self) -> bool:
        """
        Begin is allowed once there is a sample number to claim.

        The number is the only thing the operator supplies before Begin — the
        identity fields are filled from the defaults at the moment of claiming,
        so requiring one of them here would leave the button unpressable on an
        instrument whose defaults could not be read.
        """
        return bool(self.record.get("samp_no", "").strip())

    def clear_run(self) -> None:
        """Reset the per-run state (keeps name); used when starting a new sample."""
        self.record       = _new_record()
        self.phase        = "entry"
        self.active_phase = "entry"
        self.running   = False
        self.capturing = False
        self.timer     = None
        self.progress  = ""
        self.result    = ""
        self.modal_return = None
        self.stir_deadline      = None
        self.stir_role          = None
        self.stir_fired         = False
        self.reagent1_wt_target = "reagent1_initial_wt"
        self.reagent2_wt_target = "reagent2_initial_wt"
