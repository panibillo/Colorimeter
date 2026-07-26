"""
exposure_repo.py  —  Domain layer for the exposure ladder + `exposure` table.
-----------------------------------------------------------------------------
Two responsibilities, kept in one place because they are two faces of the same
idea — "an exposure group of size N is a geometric ladder of N exposures
centered on the ideal exposure e*":

  1. The ladder math (pure, no DB): given e* and N, produce the N exposure
     times.  Used by the Calibration capture path to derive what to store.
  2. Data access over the `exposure` table (thin, over db_manager, mirroring
     sample_repo): read the exposures for a (LED, group) in the Normal capture
     path, and write the groups a calibration discovered.

The `exposure` table (created in the DB, not here):

    CREATE TABLE exposure (
        rowid          INTEGER NOT NULL UNIQUE,  -- explicit, NOT the hidden alias
        LED            TEXT NOT NULL,            -- matches LEDBank color names
        microseconds   INTEGER,                  -- one exposure time
        exposuregroup  TEXT,                     -- '1'..'5' = images per LED
        remark         TEXT);

Note the `rowid` column is a real declared column (NOT `INTEGER PRIMARY KEY`),
so SQLite does not auto-assign it and its NOT NULL constraint bites on a bare
INSERT.  Writes here assign it as MAX(rowid)+1; safe because the app is a
single-threaded cooperative loop with request-scoped connections.

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
    read_exposures(led, group) -> list[int]        exposures for one LED+group
    read_group(group) -> dict[str, list[int]]      {led: exposures} for a group
    available_groups() -> list[str]                distinct groups present
    write_led_exposures(led, group, microseconds, remark=None) -> int
    write_all_groups(led, groups, remark=None) -> int
    delete_led(led) -> int
"""

from __future__ import annotations

import logging
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


# ── Reads ───────────────────────────────────────────────────────────────────

def read_exposures(led: str, group: str) -> List[int]:
    """
    Return the exposure times (µs) stored for one LED and group, ascending.

    Empty list if the (LED, group) pair has no rows — the Normal capture path
    treats that as "nothing calibrated for this LED" and falls back.
    """
    rows = db.read_all(
        "SELECT microseconds FROM exposure "
        "WHERE LED = ? AND exposuregroup = ? AND microseconds IS NOT NULL "
        "ORDER BY microseconds",
        (led, str(group)),
    )
    return [int(us) for (us,) in rows]


def read_group(group: str) -> Dict[str, List[int]]:
    """
    Return {LED: [exposures ascending]} for every LED present in `group`.

    Used by the Normal capture path to know which LEDs are calibrated for the
    chosen group and at what exposures.
    """
    rows = db.read_all(
        "SELECT LED, microseconds FROM exposure "
        "WHERE exposuregroup = ? AND microseconds IS NOT NULL "
        "ORDER BY LED, microseconds",
        (str(group),),
    )
    out: Dict[str, List[int]] = {}
    for led, us in rows:
        out.setdefault(led, []).append(int(us))
    return out


def available_groups() -> List[str]:
    """Return the distinct exposuregroup values present, sorted numerically."""
    rows = db.read_all(
        "SELECT DISTINCT exposuregroup FROM exposure "
        "WHERE exposuregroup IS NOT NULL")
    groups = [g for (g,) in rows]
    # Sort numerically where possible so '10' would follow '9', not '1'.
    return sorted(groups, key=lambda g: (not g.isdigit(), int(g) if g.isdigit() else 0, g))


# ── Writes ──────────────────────────────────────────────────────────────────

def _next_rowid() -> int:
    """The next explicit rowid to assign (this table's rowid is not auto)."""
    return int(db.read_val("SELECT COALESCE(MAX(rowid), 0) FROM exposure") or 0) + 1


def write_led_exposures(led: str, group: str, microseconds: List[int],
                        remark: Optional[str] = None) -> int:
    """
    Replace the exposures stored for one (LED, group): delete the existing
    rows for that pair, then insert one row per exposure time.

    Returns the number of rows inserted.
    """
    group = str(group)
    db.write_one("DELETE FROM exposure WHERE LED = ? AND exposuregroup = ?",
                 (led, group))
    return _insert_rows(led, {group: list(microseconds)}, remark)


def write_all_groups(led: str, groups: Dict[str, List[int]],
                     remark: Optional[str] = None) -> int:
    """
    Replace every exposure row for one LED with a fresh set of groups.

    `groups` is {group: [exposures]} as produced by groups_from_estar().  All
    existing rows for the LED are deleted first, so a re-calibration cleanly
    supersedes the previous one.  Returns the number of rows inserted.
    """
    db.write_one("DELETE FROM exposure WHERE LED = ?", (led,))
    return _insert_rows(led, groups, remark)


def write_estar(led: str, e_star_us: float, ratio: float = DEFAULT_RATIO,
                max_n: int = MAX_GROUP, remark: Optional[str] = None) -> int:
    """
    Persist a calibrated e* for one LED as its whole family of exposure groups.

    The Calibration capture path discovers e* (the ideal exposure) per LED;
    this turns it into the stored groups 1..max_n that the Normal path reads.
    Convenience over write_all_groups(led, groups_from_estar(...)).

    Returns the number of rows written.
    """
    return write_all_groups(led, groups_from_estar(e_star_us, ratio, max_n),
                            remark)


def _insert_rows(led: str, groups: Dict[str, List[int]],
                 remark: Optional[str]) -> int:
    """Insert exposure rows for a LED, assigning consecutive explicit rowids."""
    rows = []
    rowid = _next_rowid()
    # Deterministic order: groups numerically, exposures ascending.
    for group in sorted(groups, key=lambda g: (int(g) if g.isdigit() else 1 << 30, g)):
        for us in sorted(int(u) for u in groups[group]):
            rows.append((rowid, led, us, str(group), remark))
            rowid += 1
    if not rows:
        return 0
    db.write_many(
        "INSERT INTO exposure (rowid, LED, microseconds, exposuregroup, remark) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def delete_led(led: str) -> int:
    """Delete every exposure row for one LED.  Returns rows removed."""
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
    live = Path(__file__).resolve().parent / "colorimeter.sqlite"
    tmp = Path(tempfile.mkdtemp()) / "test.sqlite"
    shutil.copy(live, tmp)
    db.DBNAME = str(tmp)
    db.write_one("DELETE FROM exposure")

    print("== write_all_groups + read back ==")
    n = write_all_groups("white", groups_from_estar(40_000), remark="selftest")
    check("rows written = 1+2+3+4+5", n, 15)
    check("group 1 read",   read_exposures("white", "1"), [40_000])
    check("group 3 read",   read_exposures("white", "3"), [26_667, 40_000, 60_000])
    check("group present",  "white" in read_group("3"), True)
    check("available groups", available_groups(), ["1", "2", "3", "4", "5"])

    print("== write_led_exposures replaces one group only ==")
    write_led_exposures("white", "1", [12345], remark="override")
    check("group 1 replaced", read_exposures("white", "1"), [12345])
    check("group 3 untouched", read_exposures("white", "3"), [26_667, 40_000, 60_000])

    print("== rowids stay unique after replace ==")
    ids = [r[0] for r in db.read_all("SELECT rowid FROM exposure")]
    check("rowids unique", len(ids) == len(set(ids)), True)

    print("== delete_led ==")
    delete_led("white")
    check("led gone", read_exposures("white", "1"), [])

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("\n== all exposure_repo self-tests passed ==")
