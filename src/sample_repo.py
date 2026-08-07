"""
sample_repo.py  —  Domain data-access layer for the sample workflow.
--------------------------------------------------------------------
A thin domain layer over db_manager: the GUI (arbiter / phases) speaks in
these typed operations and never composes SQL itself.  Everything here is
about the `samples` table, the entry-screen pre-fills, and the sequential
sample-number rule.

Public surface
--------------
    load_defaults()         -> Defaults | None    the entry-screen pre-fills
    next_sample_no(template) -> str               the next sequential samp_no
    insert_sample(...)      -> int                reserve a row (raises on dup)
    update_sample(rowid, **cols) -> int           write columns as the run proceeds
    delete_sample(rowid)    -> int                discard a reserved row

Sample-number rule (from defaults.samp_no_template, e.g. 'ZZ1000')
------------------------------------------------------------------
- prefix  = the template up to and including its last alphabetic character.
- integer = the remainder of the template (its digits).
- Scan `samples` for the largest integer that follows the prefix; the next
  number is that max + 1, or — if none exist — the template's integer + 1.
- Zero-pad to at least the template's digit width.
- Matching is case-insensitive and ignores spaces.
  'ZZ1000' with an empty table -> 'ZZ1001'.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import config_repo as config
import db_manager as db

log = logging.getLogger(__name__)


# ── Defaults ──────────────────────────────────────────────────────────────────
# The entry screens' pre-fill values.  These used to be a `default_gui_vals`
# view joining `defaults` to `users`/`testkit`/`testlot`/`device`; they are now
# ordinary config parameters, so a Defaults is just a read of two config roles.
# The dataclass survives because it is the Phase contract (arb.defaults()) and
# it names, in one place, which parameters the entry screens consume.

@dataclass
class Defaults:
    """The entry-screen pre-fill values, resolved from the config table."""
    samp_no_template:  str
    user:              str
    user_email:        str
    user_org:          str
    testkit:           str
    testkit_vendor:    str
    testlot:           str
    testlot_expire_dt: str
    hardware_sn:       str      # role 'device' — stored on the sample as
    software_ver:      str      # instrument_sn / provenance


# field name -> (config role, config name).  Every field comes from config;
# listing the roles here keeps the mapping visible instead of implied.
_DEFAULT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("samp_no_template",  "defaults", "samp_no_template"),
    ("user",              "defaults", "user"),
    ("user_email",        "defaults", "user_email"),
    ("user_org",          "defaults", "user_org"),
    ("testkit",           "defaults", "testkit"),
    ("testkit_vendor",    "defaults", "testkit_vendor"),
    ("testlot",           "defaults", "testlot"),
    ("testlot_expire_dt", "defaults", "testlot_expire_dt"),
    ("hardware_sn",       "device",   "hardware_sn"),
    ("software_ver",      "device",   "software_ver"),
)


def load_defaults() -> Optional[Defaults]:
    """
    Return the entry-screen pre-fill values, or None if config is unreadable.

    db_bootstrap guarantees every parameter has an active row before the app
    starts, so a miss here means the database was changed underneath us.  That
    is logged and answered with None; the entry screens then start blank rather
    than the app failing on a screen the operator can still type into.
    """
    try:
        vals = {field: config.get(role, name)
                for field, role, name in _DEFAULT_FIELDS}
    except config.ConfigMissing as e:
        log.error("sample_repo: defaults unavailable — %s", e)
        return None
    return Defaults(**vals)


# ── Sample numbering ──────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Normalise for matching: drop spaces, upper-case."""
    return (s or "").replace(" ", "").upper()


def _parse_template(template: str) -> tuple[str, int, int]:
    """
    Split a samp_no template into (prefix, start_int, width).

    prefix   = up to and including the last alphabetic character
    start_int= integer value of the trailing digits (0 if none)
    width    = number of trailing digits (min 1)
    """
    t = (template or "").strip()
    last_alpha = -1
    for i, c in enumerate(t):
        if c.isalpha():
            last_alpha = i
    prefix = t[: last_alpha + 1]
    suffix = t[last_alpha + 1:]
    if suffix.isdigit():
        return prefix, int(suffix), max(len(suffix), 1)
    return prefix, 0, 1


def next_sample_no(template: str) -> str:
    """
    Compute the next sequential sample number for `template`, considering every
    existing samp_no in the `samples` table (see the module docstring for the
    rule).  Reads the DB; does not reserve anything.
    """
    prefix, start, width = _parse_template(template)
    nprefix = _norm(prefix)

    max_int: Optional[int] = None
    for (samp_no,) in db.read_all("SELECT samp_no FROM samples"):
        n = _norm(samp_no)
        if not n.startswith(nprefix):
            continue
        rest = n[len(nprefix):]
        if rest.isdigit():
            v = int(rest)
            max_int = v if max_int is None else max(max_int, v)

    nxt = (max_int + 1) if max_int is not None else (start + 1)
    return f"{prefix}{nxt:0{width}d}"


# ── Sample CRUD ───────────────────────────────────────────────────────────────

# Columns update_sample() is allowed to write.  The whitelist both documents the
# writable surface and keeps caller-supplied names out of the SQL text.
_UPDATABLE = frozenset({
    "samp_no", "user", "test_kit", "test_lot", "instrument_sn", "collect_dt",
    "samp_grams", "samp_degC",
    "reagent1_start", "reagent1_end", "reagent2_start", "reagent2_end",
    "capture_start", "capture_end",
    "manual_read", "txt_result", "num_result", "h5file", "remark",
})


def insert_sample(samp_no: str, user, test_kit, test_lot, instrument_sn) -> int:
    """
    Reserve a sample row with its identifying columns.  Returns the new rowid.

    Provenance is stored by value, not by foreign key: the operator's name, the
    kit, the reagent lot and the instrument serial are written as the text they
    were at the time, so a sample stays readable after those settings change.

    Raises sqlite3.IntegrityError if samp_no already exists (the UNIQUE, NOCASE
    constraint) — the caller uses this to catch a duplicate/manually-typed id.
    """
    db.write_one(
        "INSERT INTO samples (samp_no, user, test_kit, test_lot, instrument_sn) "
        "VALUES (?, ?, ?, ?, ?)",
        (samp_no, user, test_kit, test_lot, instrument_sn),
    )
    # samp_no is UNIQUE, so this reliably identifies the row just inserted.
    return db.read_val("SELECT rowid FROM samples WHERE samp_no = ?", (samp_no,))


def samp_no_for(rowid: Optional[int]) -> str:
    """
    Return the sample number of one row, or "" if there is not one.

    Used by the capture runner to name the archive file after the sample it
    belongs to.  Answers "" rather than raising for a capture launched from
    the menu, which belongs to no sample at all — the caller then falls back
    to naming the file after the capture time.
    """
    if rowid is None:
        return ""
    return db.read_val("SELECT samp_no FROM samples WHERE rowid = ?", (rowid,)) or ""


def update_sample(rowid: int, **cols) -> int:
    """UPDATE the given columns of one sample row.  Unknown columns are ignored."""
    if rowid is None:
        return 0
    cols = {k: v for k, v in cols.items() if k in _UPDATABLE}
    if not cols:
        return 0
    keys = list(cols)
    set_clause = ", ".join(f"{k} = ?" for k in keys)
    vals = tuple(cols[k] for k in keys) + (rowid,)
    return db.write_one(f"UPDATE samples SET {set_clause} WHERE rowid = ?", vals)


def delete_sample(rowid: int) -> int:
    """Delete a reserved sample row (used when a run is cancelled and discarded)."""
    if rowid is None:
        return 0
    return db.write_one("DELETE FROM samples WHERE rowid = ?", (rowid,))


# ─────────────────────────────────────────────────────────────────────────────
# Self-test — numbering rules run in-memory; CRUD runs on a throwaway copy of
# the real DB so the live colorimeter.sqlite is never touched.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import shutil
    import sqlite3
    import tempfile
    from pathlib import Path

    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)-8s [%(name)s] %(message)s")

    def check(label, got, want):
        ok = "OK " if got == want else "XX "
        print(f"  {ok}{label:44s} got={got!r}")
        assert got == want, f"{label}: {got!r} != {want!r}"

    print("== _parse_template ==")
    check("ZZ1000", _parse_template("ZZ1000"), ("ZZ", 1000, 4))
    check("Z000",   _parse_template("Z000"),   ("Z", 0, 3))
    check("ABC12",  _parse_template("ABC12"),  ("ABC", 12, 2))
    check("no digits 'ZZ'", _parse_template("ZZ"), ("ZZ", 0, 1))

    # Build a fresh database in a scratch directory: DDL + seed, exactly what a
    # new instrument gets.  The live colorimeter.sqlite is never opened.
    import db_bootstrap

    tmpdir = Path(tempfile.mkdtemp())
    db.DBNAME = str(tmpdir / "test.sqlite")
    db_bootstrap.ensure()
    config.load(reload=True)

    SN = config.get("device", "hardware_sn")

    print("== next_sample_no on empty table ==")
    check("ZZ1000 -> ZZ1001", next_sample_no("ZZ1000"), "ZZ1001")
    check("Z000   -> Z001",   next_sample_no("Z000"),   "Z001")

    print("== insert_sample reserves, then numbering advances ==")
    rid = insert_sample("ZZ1001", "Ann", "Ascel 458201", "0 12V601-HH", SN)
    check("insert returns a rowid", isinstance(rid, int) and rid > 0, True)
    check("next after ZZ1001", next_sample_no("ZZ1000"), "ZZ1002")

    print("== provenance is stored as text, not ids ==")
    check("identity columns persisted",
          db.read_one("SELECT user, test_kit, test_lot, instrument_sn "
                      "FROM samples WHERE rowid = ?", (rid,)),
          ("Ann", "Ascel 458201", "0 12V601-HH", SN))

    print("== duplicate samp_no raises IntegrityError (case-insensitive) ==")
    dup = False
    try:
        insert_sample("zz1001", "Ann", "", "", SN)   # different case, same value
    except sqlite3.IntegrityError:
        dup = True
    check("IntegrityError on duplicate", dup, True)

    print("== numbering rules: width + prefix matching ==")
    db.write_one("DELETE FROM samples")
    insert_sample("Z1234", "Ann", "", "", SN)   # matches prefix 'Z'
    insert_sample("ZZ0009", "Ann", "", "", SN)  # does NOT match 'Z' (rest 'Z0009')
    check("max match +1, widens", next_sample_no("Z000"), "Z1235")
    check("spaces ignored in match",
          next_sample_no("Z 000"), "Z1235")   # template prefix 'Z', space ignored

    print("== update_sample writes whitelisted columns only ==")
    db.write_one("DELETE FROM samples")
    rid = insert_sample("ZZ1001", "Ann", "", "", SN)
    update_sample(rid, samp_degC=21.5, samp_grams=3.2, collect_dt="2026-07-21 10:00:00",
                  bogus_col="ignored")
    row = db.read_one("SELECT samp_degC, samp_grams, collect_dt FROM samples WHERE rowid=?",
                      (rid,))
    check("degC/grams/collect persisted", row, (21.5, 3.2, "2026-07-21 10:00:00"))

    print("== update_sample refuses a dropped v1 column ==")
    update_sample(rid, userid=7)                  # the FK columns are gone
    check("unknown column ignored",
          db.read_val("SELECT COUNT(*) FROM samples WHERE rowid=?", (rid,)), 1)

    print("== delete_sample discards the row ==")
    delete_sample(rid)
    check("row gone", db.read_val("SELECT COUNT(*) FROM samples WHERE rowid=?", (rid,)), 0)

    print("== load_defaults reads the config table ==")
    d = load_defaults()
    check("defaults loaded",      d is not None, True)
    check("samp_no_template",     d.samp_no_template, "ZZ1000")
    check("user",                 d.user, "William Olsen")
    check("testkit present",      bool(d.testkit), True)
    check("testlot adjacent to its expiry",
          bool(d.testlot) and bool(d.testlot_expire_dt), True)
    check("device role folded in", d.hardware_sn, SN)

    print("== an edited value shows up on the next load ==")
    config.save("defaults", "user", "Barbara Olsen")
    check("edit visible", load_defaults().user, "Barbara Olsen")

    print("== load_defaults answers None when a parameter is missing ==")
    db.write_one("DELETE FROM config WHERE role='defaults' AND name='user'")
    config.load(reload=True)
    check("None, not a crash", load_defaults(), None)

    shutil.rmtree(tmpdir, ignore_errors=True)
    print("\n== all sample_repo self-tests passed ==")
