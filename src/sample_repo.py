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
    next_sample_no(template, held) -> str         the next sequential samp_no
    same_samp_no(a, b)      -> bool               do two numbers name one sample?
    insert_sample(...)      -> int                reserve a row (raises on dup)
    update_sample(rowid, **cols) -> int           write columns as the run proceeds
    delete_sample(rowid)    -> int                discard a reserved row

    equipment_list()        -> list[str]          the test-variant Equip choices
    save_treatment(samp_no, **cols) -> int        write a test-variant row
    latest_treatment()      -> dict | None        the newest row, for carry-over
    delete_treatment(samp_no) -> int              discard a sample's row

Sample-number rule (from defaults.samp_no_template, e.g. 'ZZ1000')
------------------------------------------------------------------
- prefix  = the template up to and including its last alphabetic character.
- integer = the remainder of the template (its digits).
- Scan `samples`, plus any numbers the caller says are spoken for, for the
  largest integer that follows the prefix; the next number is that max + 1,
  or — if none exist — the template's integer + 1.
- Zero-pad to at least the template's digit width.
- Matching is case-insensitive and ignores spaces.
  'ZZ1000' with an empty table -> 'ZZ1001'.

A number is *claimed* only by a row in `samples`.  What a slot merely displays
is not a claim, which is why the numbers to consider beyond the table are the
caller's to supply rather than something this module can know.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import chain
from typing import Iterable, Optional

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
    remark:            str
    default_temperature: float  # entry2 fills this in when no probe answers
    default_sample_wt: str      # entry2's Weight button fills this in
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
    ("remark",            "defaults", "remark"),
    ("default_temperature", "defaults", "default_temperature"),
    ("default_sample_wt",  "defaults", "default_sample_wt"),
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


def same_samp_no(a: str, b: str) -> bool:
    """
    True when two sample numbers name the same sample.

    `samples.samp_no` is UNIQUE COLLATE NOCASE, so the database reads 'zz1001'
    and 'ZZ1001' as one number.  Anything comparing what a slot displays against
    what is stored has to judge them the same way, so the rule lives here rather
    than being spelled out again at each caller.
    """
    return _norm(a) == _norm(b)


def next_sample_no(template: str, held: Iterable[str] = ()) -> str:
    """
    Compute the next sequential sample number for `template`, considering every
    existing samp_no in the `samples` table plus every number in `held` (see the
    module docstring for the rule).  Reads the DB; does not reserve anything.

    `held` carries the numbers that are spoken for but not yet in the table —
    in practice the sample numbers the other slots are displaying.  Omitting it
    asks a different and equally useful question: the next number the *table*
    alone allows, which is what the entry screen's ID button wants.
    """
    prefix, start, width = _parse_template(template)
    nprefix = _norm(prefix)

    max_int: Optional[int] = None
    rows = (samp_no for (samp_no,) in db.read_all("SELECT samp_no FROM samples"))
    for samp_no in chain(rows, held):
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
    "reagent1_g", "reagent2_g", "reagent1_stir_end", "reagent2_stir_end",
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


# ── Test-variant treatments ───────────────────────────────────────────────────
# A treatments row exists only for a sample run with protocol.test_variant on,
# so its presence is the record that the sample was a test variant.  Joined to
# samples by samp_no rather than rowid, because samp_no is the name the
# operator and the archive files both use.

# The treatment columns, in table order.  Also the whitelist save_treatment
# writes through, which keeps caller-supplied names out of the SQL text.
TREATMENT_COLS = ("test_equip", "image_reps",
                  "R1_wt_factor", "R1_time_factor",
                  "R2_wt_factor", "R2_time_factor")


def equipment_list() -> list:
    """
    The test-variant screen's equipment choices, as display labels.

    Each label is the code and description together — 'B 250 Ehrl,
    unprotected' — which is also exactly what a treatments row stores, so the
    sample keeps the words the operator picked even if the list is edited.

    Returns:
        The active test_equip rows as labels, ordered by code.  Empty if the
        table is empty.
    """
    rows = db.read_all("SELECT code, description FROM test_equip "
                       "WHERE active = 1 ORDER BY code")
    return [f"{code} {desc}" for code, desc in rows]


def save_treatment(samp_no: str, **cols) -> int:
    """
    Write one sample's treatment, replacing any row it already has.

    Args:
        samp_no: The sample the treatment belongs to.
        **cols:  Any of TREATMENT_COLS.  Unknown names are ignored.

    Returns:
        The number of rows written (1), or 0 when samp_no is empty.
    """
    if not samp_no:
        return 0
    cols = {k: v for k, v in cols.items() if k in TREATMENT_COLS}
    keys = ["samp_no"] + list(cols)
    marks = ", ".join("?" for _ in keys)
    updates = ", ".join(f"{k} = excluded.{k}" for k in cols) or "samp_no = samp_no"
    return db.write_one(
        f"INSERT INTO treatments ({', '.join(keys)}) VALUES ({marks}) "
        f"ON CONFLICT(samp_no) DO UPDATE SET {updates}",
        (samp_no,) + tuple(cols.values()))


def latest_treatment() -> Optional[dict]:
    """
    The most recently started sample's treatment, or None if there is none.

    The test-variant screen starts a new sample from this, because development
    runs come in series and most of a series repeats the last settings.
    """
    row = db.read_one(f"SELECT {', '.join(TREATMENT_COLS)} FROM treatments "
                      f"ORDER BY rowid DESC LIMIT 1")
    return dict(zip(TREATMENT_COLS, row)) if row else None


def delete_treatment(samp_no: str) -> int:
    """Delete a sample's treatment row (when its sample row is discarded)."""
    if not samp_no:
        return 0
    return db.write_one("DELETE FROM treatments WHERE samp_no = ?", (samp_no,))


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

    print("== same_samp_no judges as the NOCASE column does ==")
    check("case ignored",   same_samp_no("zz1001", "ZZ1001"), True)
    check("spaces ignored", same_samp_no("ZZ 1001", "ZZ1001"), True)
    check("different numbers", same_samp_no("ZZ1001", "ZZ1002"), False)

    print("== held numbers count alongside the table ==")
    # The table holds ZZ1001, so the table alone answers ZZ1002 throughout.
    check("held above the table pushes past it",
          next_sample_no("ZZ1000", held=["ZZ1002"]), "ZZ1003")
    check("held below the table max changes nothing",
          next_sample_no("ZZ1000", held=["ZZ1000"]), "ZZ1002")
    check("held nobody asked about is not consulted",
          next_sample_no("ZZ1000"), "ZZ1002")
    check("non-conforming held is ignored",
          next_sample_no("ZZ1000", held=["FIELD-7", ""]), "ZZ1002")

    print("== two slots opening in turn get consecutive numbers ==")
    a = next_sample_no("ZZ1000")             # slot A opens
    b = next_sample_no("ZZ1000", held=[a])   # slot B opens while A holds `a`
    check("A and B differ", (a, b), ("ZZ1002", "ZZ1003"))

    print("== the ID press asks the table alone — hence the bump ==")
    # Neither slot has begun, so neither number is claimed: the next unclaimed
    # number is still the one slot A is displaying.  That is why the arbiter has
    # to move A aside when B presses ID, rather than handing B a third number.
    check("table-only answers the number A holds", next_sample_no("ZZ1000"), a)

    print("== a number is claimed by a row, and released with it ==")
    rid_a = insert_sample(a, "Ann", "", "", SN)
    check("claimed, so the next ask advances", next_sample_no("ZZ1000"), b)
    delete_sample(rid_a)
    check("released, so it is offered again", next_sample_no("ZZ1000"), a)

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
    check("default temperature is a number", isinstance(d.default_temperature, float),
          True)
    check("default sample weight is text", d.default_sample_wt, "100.")

    print("== an edited value shows up on the next load ==")
    config.save("defaults", "user", "Barbara Olsen")
    check("edit visible", load_defaults().user, "Barbara Olsen")

    print("== load_defaults answers None when a parameter is missing ==")
    db.write_one("DELETE FROM config WHERE role='defaults' AND name='user'")
    config.load(reload=True)
    check("None, not a crash", load_defaults(), None)

    print("== test_equip is seeded A..F, labelled code + description ==")
    equip = equipment_list()
    check("six choices",         len(equip), 6)
    check("first label",         equip[0], "A Original bottle")
    check("last label",          equip[-1], "F 250 Ehrl, casset 01")

    print("== treatments: save, replace, latest, delete ==")
    db.write_one("DELETE FROM treatments")
    check("none yet",            latest_treatment(), None)
    save_treatment("ZZ2001", test_equip=equip[1], image_reps=2,
                   R1_wt_factor=1.0, R1_time_factor=1.5,
                   R2_wt_factor=0.5, R2_time_factor=1.0, bogus="ignored")
    check("row written", latest_treatment(),
          {"test_equip": "B 250 Ehrl, unprotected", "image_reps": 2,
           "R1_wt_factor": 1.0, "R1_time_factor": 1.5,
           "R2_wt_factor": 0.5, "R2_time_factor": 1.0})
    save_treatment("zz2001", image_reps=3)        # same sample, any case
    check("one row per sample",
          db.read_val("SELECT COUNT(*) FROM treatments"), 1)
    check("replaced in place",   latest_treatment()["image_reps"], 3)
    check("other columns kept",  latest_treatment()["R1_time_factor"], 1.5)
    save_treatment("ZZ2002", test_equip=equip[0], image_reps=1)
    check("latest is the newest sample", latest_treatment()["test_equip"],
          "A Original bottle")
    delete_treatment("ZZ2002")
    check("deleted; the one before is latest again",
          latest_treatment()["image_reps"], 3)
    check("empty samp_no writes nothing", save_treatment("", image_reps=1), 0)

    shutil.rmtree(tmpdir, ignore_errors=True)
    print("\n== all sample_repo self-tests passed ==")
