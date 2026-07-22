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
        "user":          "",   # user_fld      (fullname; display only)
        "test":          "",   # test_fld      (testkit;  display only)
        "test_lot":      "",   # test_lot_fld  (lotno;    display only)
        "collect_dt":    "",   # entry2        -> samples.collect_dt
        "samp_degC":     "",   # entry2 temp   -> samples.samp_degC
        "samp_grams":    "",   # entry2 weight -> samples.samp_grams
        "manual_read":   "",   # read screen   -> samples.manual_read
        # ── identity carried from the loaded defaults (the real FKs stored) ──
        "db_rowid":      None,  # samples.rowid, once reserved at Begin
        "userid":        None,  # samples.userid
        "testlotid":     None,  # samples.testlotid
        "colorimeterid": None,  # samples.colorimeterid (defaults.deviceid)
        # ── milestone timestamps (epoch seconds) -> samples.* ───────────────
        "reagent1_start": None, "reagent1_end": None,
        "reagent2_start": None, "reagent2_end": None,
        "capture_start":  None, "capture_end":  None,
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

    def ready_to_begin(self) -> bool:
        """Begin is allowed once the sample number and user are filled."""
        return bool(self.record.get("samp_no", "").strip()
                    and self.record.get("user", "").strip())

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
