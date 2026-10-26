"""
exposure_repo.py  —  Data access for the `exposure` table.
---------------------------------------------------------
The table holds the exposure brackets calibration tested: for each LED, each
group size N ('1'..'5' = images per LED) lists N exposure times in microseconds
that were each checked to keep the test pad and gray chips inside the
calibration window.  See exposure_fit.py for how they are found.  Nothing here
computes exposures; this module stores and reads them (thin, over db_manager,
mirroring sample_repo).

The `exposure` table (db_DDL.sql is the authority):

    rowid          INTEGER PRIMARY KEY NOT NULL
    active         INTEGER   -- use only rows where active = 1
    add_dt         TEXT      -- calibration timestamp (local time)
    exposuregroup  TEXT      -- '1'..'5' = images per LED
    LED            TEXT NOT NULL   -- matches LEDBank color names
    microseconds   INTEGER   -- one exposure time
    remark         TEXT      -- 'low DN (patch)', 'high DN (patch)', 'log rung 1/4'
    low_dn, high_dn REAL     -- signal measured on the rung that fixed a limit
    layers         TEXT      -- Bayer layers that had to pass, e.g. 'Gb Gr'
    preimage       INTEGER   -- 1 = the pre-image takes this exposure

The `active` flag is the current-vs-history switch.  Only active rows are read.
A new calibration does not delete or overwrite the previous one: it flips the
superseded rows to active=0 and inserts fresh rows with active=1 and add_dt set
to the run's local timestamp.  Prior calibrations are therefore preserved (a
help during development), and the operator can hand-edit the table from outside
the program — flip active flags, set `preimage` — to control what a run uses
without touching code.

Deactivation is per-LED: writing a LED's exposures supersedes only that LED's
active rows, leaving other LEDs' calibrations alone.

Public surface
--------------
    read_exposures(led, group) -> list[int]        active exposures, one LED+group
    middle_exposure(led, group) -> int | None      middle stored rung, one LED+group
    rung_exposures(led) -> dict[str, int]          {'0': us, '1/4': us, ...}
    read_group(group) -> dict[str, list[int]]      {led: exposures} for a group
    available_groups() -> list[str]                distinct active groups present
    write_led_exposures(led, group, microseconds, remark=None, add_dt=None) -> int
    write_all_groups(led, groups, remark, add_dt, notes, layers, preimage_group) -> int
    read_led_exposures(led) -> list[int]           every distinct active exposure
    read_preimage_exposures(led) -> list[int]      those flagged for the pre-image
    delete_led(led) -> int                         hard delete (history and all)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

import db_manager as db

log = logging.getLogger(__name__)

# ── Reads (active rows only) ────────────────────────────────────────────────

def read_exposures(led: str, group: str) -> List[int]:
    """
    Return the active exposure times (µs) for one LED and group, ascending.

    Only rows with active=1 are considered.  Empty list if the (LED, group)
    pair has no active rows — the Normal capture path treats that as "nothing
    calibrated for this LED" and falls back.
    """
    rows = db.read_all(
        "SELECT microseconds FROM exposure "
        "WHERE LED = ? AND exposuregroup = ? AND active = 1 "
        "AND microseconds IS NOT NULL "
        "ORDER BY microseconds",
        (led, str(group)),
    )
    return [int(us) for (us,) in rows]


def middle_exposure(led: str, group: str) -> Optional[int]:
    """
    Return the middle stored exposure (µs) for one LED and group, or None.

    The preview and the pre-image each want one exposure, not a whole ladder.
    The middle rung of the tested ladder is the stand-in for both.  For an even
    count the lower of the two middle rungs is used, so the answer is always an
    exposure that was actually tested.
    """
    exposures = read_exposures(led, group)
    if not exposures:
        return None
    return exposures[(len(exposures) - 1) // 2]


# The seven rungs, shortest exposure first.  Each group's exposures sit at
# its own subset of them (exposure_fit.GROUP_POSITIONS), so the rungs are
# read back by position within the groups.
RUNGS = ("0", "1/4", "1/3", "1/2", "2/3", "3/4", "1")
_GROUP_RUNGS = {
    "5": ("0", "1/4", "1/2", "3/4", "1"),
    "4": ("0", "1/3", "2/3", "1"),
    "3": ("0", "1/2", "1"),
    "2": ("0", "1"),
    "1": ("1/2",),
}


def rung_exposures(led: str) -> Dict[str, int]:
    """
    Return {rung: exposure_us} for one LED, in RUNGS order, from its stored
    groups.

    A rung is read from the largest group that has it, so a calibration with
    every group gives all seven.  A group whose row count does not match its
    rungs (a hand-edited table) is skipped rather than misread.  A rung no
    stored group covers is left out.
    """
    found: Dict[str, int] = {}
    for group, rungs in _GROUP_RUNGS.items():
        exposures = read_exposures(led, group)
        if len(exposures) != len(rungs):
            continue
        for rung, us in zip(rungs, exposures):
            found.setdefault(rung, us)
    return {r: found[r] for r in RUNGS if r in found}


def read_group(group: str) -> Dict[str, List[int]]:
    """
    Return {LED: [exposures ascending]} for every LED with active rows in
    `group`.

    Used by the Normal capture path to know which LEDs are calibrated for the
    chosen group and at what exposures.
    """
    rows = db.read_all(
        "SELECT LED, microseconds FROM exposure "
        "WHERE exposuregroup = ? AND active = 1 AND microseconds IS NOT NULL "
        "ORDER BY LED, microseconds",
        (str(group),),
    )
    out: Dict[str, List[int]] = {}
    for led, us in rows:
        out.setdefault(led, []).append(int(us))
    return out


def available_groups() -> List[str]:
    """Return the distinct active exposuregroup values, sorted numerically."""
    rows = db.read_all(
        "SELECT DISTINCT exposuregroup FROM exposure "
        "WHERE active = 1 AND exposuregroup IS NOT NULL")
    groups = [g for (g,) in rows]
    # Sort numerically where possible so '10' would follow '9', not '1'.
    return sorted(groups, key=lambda g: (not g.isdigit(), int(g) if g.isdigit() else 0, g))


# ── Writes (deactivate old, insert new — never delete) ──────────────────────

def _now_local() -> str:
    """Local wall-clock timestamp for add_dt, matching the arbiter's format."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_led_exposures(led: str, group: str, microseconds: List[int],
                        remark: Optional[str] = None,
                        add_dt: Optional[str] = None) -> int:
    """
    Supersede the active exposures for one (LED, group): flip the existing
    active rows for that pair to active=0, then insert one active row per
    exposure time.  Prior rows are kept (active=0), not deleted.

    Returns the number of rows inserted.
    """
    group = str(group)
    db.write_one(
        "UPDATE exposure SET active = 0 "
        "WHERE LED = ? AND exposuregroup = ? AND active = 1",
        (led, group))
    return _insert_rows(led, {group: list(microseconds)}, remark,
                        add_dt or _now_local())


def write_all_groups(led: str, groups: Dict[str, List[int]],
                     remark: Optional[str] = None,
                     add_dt: Optional[str] = None,
                     notes: Optional[Dict[int, tuple]] = None,
                     layers: Optional[str] = None,
                     preimage_group: Optional[str] = None) -> int:
    """
    Supersede every active exposure row for one LED with a fresh set of groups.

    `groups` is {group: [exposures]} as produced by exposure_fit.fit_led().  The
    LED's currently-active rows are flipped to active=0 (kept as history), and
    the new rows are inserted active=1 — so a re-calibration cleanly supersedes
    the previous one without losing it.  Other LEDs are untouched.

    Args:
        notes:   {exposure_us: (remark, low_dn, high_dn)} for the rungs that
                 have something to say; the remark replaces `remark` on that
                 exposure's rows, and low_dn / high_dn (either may be None)
                 are stored with it.
        layers:  The Bayer layers that had to pass, e.g. 'Gb Gr'.
        preimage_group: The group whose rows are flagged for the pre-image.

    Returns the number of rows inserted.
    """
    db.write_one("UPDATE exposure SET active = 0 WHERE LED = ? AND active = 1",
                 (led,))
    return _insert_rows(led, groups, remark, add_dt or _now_local(),
                        notes, layers, preimage_group)


def _insert_rows(led: str, groups: Dict[str, List[int]],
                 remark: Optional[str], add_dt: str,
                 notes: Optional[Dict[int, tuple]] = None,
                 layers: Optional[str] = None,
                 preimage_group: Optional[str] = None) -> int:
    """Insert active exposure rows for a LED (rowid auto-assigned by SQLite)."""
    notes = notes or {}
    rows = []
    # Deterministic order: groups numerically, exposures ascending.
    for group in sorted(groups, key=lambda g: (int(g) if g.isdigit() else 1 << 30, g)):
        flag = 1 if preimage_group is not None and str(group) == str(preimage_group) else 0
        for us in sorted(int(u) for u in groups[group]):
            note_remark, low, high = notes.get(us, (None, None, None))
            rows.append((1, add_dt, str(group), led, us,
                         note_remark if note_remark is not None else remark,
                         low, high, layers, flag))
    if not rows:
        return 0
    db.write_many(
        "INSERT INTO exposure "
        "(active, add_dt, exposuregroup, LED, microseconds, remark, "
        "low_dn, high_dn, layers, preimage) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def read_led_exposures(led: str) -> List[int]:
    """Every distinct active exposure (µs) stored for the LED, any group, ascending."""
    rows = db.read_all(
        "SELECT DISTINCT microseconds FROM exposure "
        "WHERE LED = ? AND active = 1 AND microseconds IS NOT NULL "
        "ORDER BY microseconds", (led,))
    return [int(us) for (us,) in rows]


def read_preimage_exposures(led: str) -> List[int]:
    """The LED's active exposures flagged for the pre-image, ascending."""
    rows = db.read_all(
        "SELECT microseconds FROM exposure "
        "WHERE LED = ? AND active = 1 AND preimage = 1 "
        "ORDER BY microseconds", (led,))
    return sorted({int(r[0]) for r in rows})


def delete_led(led: str) -> int:
    """
    Hard-delete every exposure row for one LED — history included.

    Unlike the calibration writes (which only deactivate), this really removes
    rows.  Used for explicit purges and test cleanup, not the normal flow.
    Returns rows removed.
    """
    return db.write_one("DELETE FROM exposure WHERE LED = ?", (led,))


# ─────────────────────────────────────────────────────────────────────────────
# Self-test — the table round-trip runs on a
# throwaway copy of the real DB so the live colorimeter.sqlite is never touched.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import shutil
    import tempfile
    from pathlib import Path

    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)-8s [%(name)s] %(message)s")

    def check(label, got, want):
        ok = "OK " if got == want else "XX "
        print(f"  {ok}{label:44s} got={got!r}")
        assert got == want, f"{label}: {got!r} != {want!r}"

    # Point db_manager at a throwaway copy of the real DB for the table tests.
    live = Path(db.DBNAME)     # the live DB location (from config)
    tmp = Path(tempfile.mkdtemp()) / "test.sqlite"
    shutil.copy(live, tmp)
    db.DBNAME = str(tmp)
    db.write_one("DELETE FROM exposure")

    print("== write_all_groups + read back (active) ==")
    LADDER = {"1": [40_000], "2": [32_660, 49_000],
              "3": [26_667, 40_000, 60_000], "4": [23_700, 35_500, 53_300, 80_000],
              "5": [17_800, 26_667, 40_000, 60_000, 90_000]}
    n = write_all_groups("white", LADDER, remark="selftest")
    check("rows written = 1+2+3+4+5", n, 15)
    check("group 1 read",   read_exposures("white", "1"), [40_000])
    check("group 3 read",   read_exposures("white", "3"), [26_667, 40_000, 60_000])
    check("group present",  "white" in read_group("3"), True)
    check("available groups", available_groups(), ["1", "2", "3", "4", "5"])
    check("add_dt stamped",  db.read_val(
        "SELECT add_dt FROM exposure WHERE LED='white' AND active=1 LIMIT 1") is not None, True)

    print("== re-calibration deactivates, does not delete ==")
    write_all_groups("white", {g: [u // 4 for u in v] for g, v in LADDER.items()})
    check("active group 1 is new",   read_exposures("white", "1"), [10_000])
    check("active rows = 15",  db.read_val(
        "SELECT COUNT(*) FROM exposure WHERE LED='white' AND active=1"), 15)
    check("history kept (30 rows)", db.read_val(
        "SELECT COUNT(*) FROM exposure WHERE LED='white'"), 30)

    print("== write_led_exposures supersedes one group only ==")
    write_led_exposures("white", "1", [12345], remark="override")
    check("group 1 replaced", read_exposures("white", "1"), [12345])
    check("group 3 untouched", read_exposures("white", "3"),
          [6_666, 10_000, 15_000])

    print("== rowids auto-assigned and unique ==")
    ids = [r[0] for r in db.read_all("SELECT rowid FROM exposure")]
    check("rowids unique", len(ids) == len(set(ids)), True)

    print("== delete_led hard-deletes ==")
    delete_led("white")
    check("led gone entirely", db.read_val(
        "SELECT COUNT(*) FROM exposure WHERE LED='white'"), 0)

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("\n== all exposure_repo self-tests passed ==")
