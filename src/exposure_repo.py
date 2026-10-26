"""
exposure_repo.py  —  Domain layer for the exposure ladder + `exposure` table.
-----------------------------------------------------------------------------
Two responsibilities, kept in one place because they are two faces of the same
idea — "an exposure group of size N is a geometric ladder of N exposures
centered on the ideal exposure e*":

  1. The ladder math (pure, no DB): given e* and N, produce the N exposure
     times.  Used by the Calibration capture path to derive what to store.
  2. Data access over the `exposure` table (thin, over db_manager, mirroring
     sample_repo): read the active exposures for a (LED, group) in the Normal
     capture path, and write the groups a calibration discovered.

The `exposure` table (created in the DB, not here):

    CREATE TABLE exposure (
        rowid          INTEGER PRIMARY KEY NOT NULL,  -- auto-assigned alias
        active         INTEGER,   -- use only rows where active = 1
        add_dt         TEXT,      -- calibration timestamp (local time)
        exposuregroup  TEXT,      -- '1'..'5' = images per LED
        LED            TEXT NOT NULL,   -- matches LEDBank color names
        microseconds   INTEGER,   -- one exposure time
        remark         TEXT);

The `active` flag is the current-vs-history switch.  Only active rows are read.
A new calibration does not delete or overwrite the previous one: it flips the
superseded rows to active=0 and inserts fresh rows with active=1 and add_dt set
to the run's local timestamp.  Prior calibrations are therefore preserved (a
help during development), and the operator can hand-edit the table from outside
the program — flip active flags, tweak microseconds — to control what a Normal
run uses without touching code.

Deactivation is per-LED: writing a LED's exposures supersedes only that LED's
active rows, leaving other LEDs' calibrations alone.  So a run that recalibrates
a subset of LEDs updates just those, and the common case (recalibrating the same
LEDs each time) behaves exactly like a full replace.

The ladder scheme (locked in 2026-07):

    exposures = e* · ratio^k,  k symmetric about 0 with unit spacing:
        N=1: k = 0
        N=2: k = -0.5, +0.5
        N=3: k = -1, 0, +1
        N=4: k = -1.5, -0.5, +0.5, +1.5
        N=5: k = -2, -1, 0, +1, +2

Geometric (log-uniform) spacing centered on e* gives uniform SNR across the
tonal range, keeps the highest-SNR frame central, and makes N a clean dial —
exactly the exposure groups '1'..'5'.  `ratio` (default 1.5 ≈ ⅔ stop) is the
one knob trading precision-overlap against total dynamic range.

Public surface
--------------
    exposure_ladder(e_star_us, n, ratio=DEFAULT_RATIO) -> list[int]
    groups_from_estar(e_star_us, ratio=DEFAULT_RATIO, max_n=MAX_GROUP) -> dict
    shift_exposures(exposures_us, rungs, ratio=DEFAULT_RATIO) -> list[int]
    read_exposures(led, group) -> list[int]        active exposures, one LED+group
    middle_exposure(led, group) -> int | None      middle stored rung, one LED+group
    read_group(group) -> dict[str, list[int]]      {led: exposures} for a group
    available_groups() -> list[str]                distinct active groups present
    write_led_exposures(led, group, microseconds, remark=None, add_dt=None) -> int
    write_all_groups(led, groups, remark=None, add_dt=None) -> int
    write_estar(led, e_star_us, ratio, max_n, remark, add_dt) -> int
    delete_led(led) -> int                         hard delete (history and all)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

import db_manager as db

log = logging.getLogger(__name__)

# Ladder parameters.  Kept here (not in config) for now; promote to config/DB
# if the operator ever needs to tune them without a code change.
DEFAULT_RATIO   = 1.5      # exposure step between adjacent rungs (≈ ⅔ stop)
MIN_EXPOSURE_US = 1000     # floor, matching capture_runner's exposure floor
MAX_GROUP       = 5        # largest exposure group (images per LED)


# ── Ladder math (pure — no DB) ──────────────────────────────────────────────

def exposure_ladder(e_star_us: float, n: int,
                    ratio: float = DEFAULT_RATIO) -> List[int]:
    """
    Return the `n` exposure times (µs) of a geometric ladder centered on e*.

    Exposures are e* · ratio^k for k = i − (n−1)/2, i in 0..n−1: symmetric
    about 0 in log space, so the ladder is centered on e* and spans
    ±(n−1)/2 rungs.  Values are rounded to whole microseconds, floored at
    MIN_EXPOSURE_US, and returned ascending (shortest first).

    Args:
        e_star_us: The ideal (center) exposure in microseconds.
        n:         Number of exposures (the group size, 1..MAX_GROUP).
        ratio:     Multiplicative step between adjacent rungs (> 1).

    Returns:
        A list of `n` integer exposure times, ascending.

    Raises:
        ValueError: if n < 1 or ratio <= 0.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    if ratio <= 0:
        raise ValueError(f"ratio must be > 0, got {ratio}")
    center = (n - 1) / 2.0
    ladder = []
    for i in range(n):
        k = i - center
        us = max(MIN_EXPOSURE_US, round(e_star_us * (ratio ** k)))
        ladder.append(int(us))
    return sorted(ladder)


def shift_exposures(exposures_us: List[int], rungs: int,
                    ratio: float = DEFAULT_RATIO) -> List[int]:
    """
    Move a set of exposures up or down the ladder by whole rungs.

    The ladder is calibrated against the gray card: e* is the exposure that
    puts the chips near half scale.  A subject that is not the card wants a
    different exposure, and the honest way to say so is in ladder rungs rather
    than a made-up microsecond count — one rung is one ⅔-stop step of the same
    geometric scheme the groups are built from, so a shifted exposure is still
    a member of the family and still comparable with the rest.

    The pre-image is what needs this.  An unexposed reagent pad reflects about
    2.2x what the gray chips do in royal blue, so at e* it would read past the
    sensor's ceiling and clip; two rungs down is 1/2.25, which lands it near
    mid-scale.  See capture_runner's MODE_PRE.

    Args:
        exposures_us: Exposure times in microseconds.
        rungs:        Whole rungs to move by — negative shortens, positive
                      lengthens, 0 returns the input unchanged.
        ratio:        The ladder's step, the same one the groups use.

    Returns:
        A new list, ascending, floored at MIN_EXPOSURE_US.

    Example:
        >>> shift_exposures([3290], -2)
        [1462]
        >>> shift_exposures([2193, 3290, 4935], 0)
        [2193, 3290, 4935]
    """
    if not rungs:
        return list(exposures_us)
    factor = ratio ** rungs
    return sorted(max(MIN_EXPOSURE_US, int(round(us * factor)))
                  for us in exposures_us)


def groups_from_estar(e_star_us: float, ratio: float = DEFAULT_RATIO,
                      max_n: int = MAX_GROUP) -> Dict[str, List[int]]:
    """
    Build every exposure group 1..max_n from a single e*.

    A calibration finds e* per LED once (the expensive part); the whole family
    of groups is then free arithmetic, so one calibration populates every N.

    Returns:
        {'1': [...], '2': [...], ..., str(max_n): [...]}, each the ladder for
        that group size.
    """
    return {str(n): exposure_ladder(e_star_us, n, ratio)
            for n in range(1, max_n + 1)}


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

    `groups` is {group: [exposures]} as produced by groups_from_estar().  The
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


def write_estar(led: str, e_star_us: float, ratio: float = DEFAULT_RATIO,
                max_n: int = MAX_GROUP, remark: Optional[str] = None,
                add_dt: Optional[str] = None) -> int:
    """
    Persist a calibrated e* for one LED as its whole family of exposure groups.

    The Calibration capture path discovers e* (the ideal exposure) per LED;
    this turns it into the stored groups 1..max_n that the Normal path reads.
    Pass a shared `add_dt` for every LED in one calibration run so the run's
    rows carry the same timestamp.  Convenience over
    write_all_groups(led, groups_from_estar(...)).

    Returns the number of rows written.
    """
    return write_all_groups(led, groups_from_estar(e_star_us, ratio, max_n),
                            remark, add_dt)


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
# Self-test — ladder math runs in-memory; the table round-trip runs on a
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

    print("== exposure_ladder: centering + length ==")
    check("N=1 is [e*]",        exposure_ladder(40_000, 1), [40_000])
    check("N=3 center is e*",   exposure_ladder(40_000, 3)[1], 40_000)
    check("N=5 length",         len(exposure_ladder(40_000, 5)), 5)
    check("N=3 with r=1.5",     exposure_ladder(30_000, 3, 1.5), [20_000, 30_000, 45_000])
    check("ascending",          exposure_ladder(40_000, 5) == sorted(exposure_ladder(40_000, 5)), True)
    check("floor at MIN",       exposure_ladder(1_000, 3, 2.0)[0], MIN_EXPOSURE_US)

    print("== groups_from_estar ==")
    g = groups_from_estar(40_000)
    check("keys 1..5",          sorted(g), ["1", "2", "3", "4", "5"])
    check("group N has N",      [len(g[str(n)]) for n in range(1, 6)], [1, 2, 3, 4, 5])

    # Point db_manager at a throwaway copy of the real DB for the table tests.
    live = Path(db.DBNAME)     # the live DB location (from config)
    tmp = Path(tempfile.mkdtemp()) / "test.sqlite"
    shutil.copy(live, tmp)
    db.DBNAME = str(tmp)
    db.write_one("DELETE FROM exposure")

    print("== write_all_groups + read back (active) ==")
    n = write_all_groups("white", groups_from_estar(40_000), remark="selftest")
    check("rows written = 1+2+3+4+5", n, 15)
    check("group 1 read",   read_exposures("white", "1"), [40_000])
    check("group 3 read",   read_exposures("white", "3"), [26_667, 40_000, 60_000])
    check("group present",  "white" in read_group("3"), True)
    check("available groups", available_groups(), ["1", "2", "3", "4", "5"])
    check("add_dt stamped",  db.read_val(
        "SELECT add_dt FROM exposure WHERE LED='white' AND active=1 LIMIT 1") is not None, True)

    print("== re-calibration deactivates, does not delete ==")
    write_all_groups("white", groups_from_estar(10_000))
    check("active group 1 is new",   read_exposures("white", "1"), [10_000])
    check("active rows = 15",  db.read_val(
        "SELECT COUNT(*) FROM exposure WHERE LED='white' AND active=1"), 15)
    check("history kept (30 rows)", db.read_val(
        "SELECT COUNT(*) FROM exposure WHERE LED='white'"), 30)

    print("== write_led_exposures supersedes one group only ==")
    write_led_exposures("white", "1", [12345], remark="override")
    check("group 1 replaced", read_exposures("white", "1"), [12345])
    check("group 3 untouched", read_exposures("white", "3"),
          [6_667, 10_000, 15_000])

    print("== rowids auto-assigned and unique ==")
    ids = [r[0] for r in db.read_all("SELECT rowid FROM exposure")]
    check("rowids unique", len(ids) == len(set(ids)), True)

    print("== write_estar convenience ==")
    write_estar("amber", 30_000)
    check("amber group 3", read_exposures("amber", "3"), [20_000, 30_000, 45_000])

    print("== delete_led hard-deletes ==")
    delete_led("white")
    check("led gone entirely", db.read_val(
        "SELECT COUNT(*) FROM exposure WHERE LED='white'"), 0)

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("\n== all exposure_repo self-tests passed ==")
