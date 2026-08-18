"""
config_repo.py  —  How the instrument's settings are reached, over `config`.
------------------------------------------------------------------------------
Every setting is a row in the `config` table: capture parameters, protocol
durations, runtime timing, the instrument's identity, and what a new test
pre-fills with.  This module is how the application reads and writes them.

    sqlite3 src/colorimeter.sqlite \\
      "SELECT role, name, vtype, value FROM config WHERE active = 1"

What this module is *not* is a place where values live.  Until 2026-08 it
carried a SEED_PARAMS table of default values that answered whenever the
database could not, which meant an instrument could run for weeks on values
that were not in its database and that nobody could see or edit.  Those are
gone.  The database is the only source of a setting, db_bootstrap guarantees
the rows are there before anything reads them, and a parameter that is missing
now raises ConfigMissing instead of quietly working.

Versioned like `exposure` and `roi`: `active` marks the row in force, an edit
deactivates it and activates a new one, so every value a parameter has ever
held stays in the table as history.

Four columns carry meaning, and none is inferred from another:

    active     use this row.  Exactly one per parameter.
    isdefault  the value the editor's Default button offers.  Movable, so an
               operator can commission a new value for this site.
    source     who created the row — 'seed' (config_defaults.sql, i.e. the
               factory), 'operator', or 'history'.  A creation fact, never
               rewritten, so it is an add-user and not an edit-user.  It is
               also how "Restore Factory Defaults" finds what shipped, which
               `isdefault` cannot answer once an operator has moved it.
    editable   0 for rows the config editor shows read-only.

One parameter is not a setting and is not seeded: device.software_ver.  It is a
fact about the running code, so src/version.py owns its value and
stamp_version() writes it in at every launch, editable=0.  Its superseded rows
are the instrument's version history.

So there are two levels of "default", which do different things:

    Default button           -> the isdefault row, this site's commissioned value
    Restore Factory Defaults -> the source='seed' row, what the unit shipped with

Reading a value is a lookup, not a merge: the table's partial unique indexes
guarantee one active row per parameter.  This module previously merged three
tiers to cope with a 'simulation' profile that shortened the reagent waits;
that profile is gone, and with it the only case where two rows competed.

Values are read once and cached; call load(reload=True) after editing the table
in a long-running process (the write functions here do it for you).

Two settings are deliberately *not* rows here:

    the database path      config_repo reads settings out of the database, so
                           it cannot say where the database is — db_manager
                           owns that ($COLORIMETER_DB)
    COLORIMETER_SIMULATION read at import time by the driver modules to choose
                           mock hardware, before any database read is possible.
                           It selects a launch mode, not a value, so it is not
                           a setting at all.  arbiter.py and lighting_cal8.py
                           read it directly; this module has no opinion on it.

Usage:
    import config_repo as config

    suite_id = config.capture_suite_id()             # "20260719_1343"
    path     = config.capture_dir() / config.capture_archive_name(suite_id)

Public surface
--------------
    load(reload=False) -> dict            {role: {name: coerced value}}
    get(role, name)                       the value in force
    default(role, name)                   what Default offers, or None
    snapshot()               -> dict      every active value, for the archive

    save(role, name, value)  -> int       supersede; new rowid
    set_default(role, name, value) -> int save, and commission the value
    stamp_version()          -> int       record the running software version
    restore(role, name)      -> int       put the Default value back
    restore_all()            -> int       every parameter back to the factory
    overrides()              -> list      (role, name) not on the Default value

    roles()                  -> list      for the config editor's role screen
    items(role)              -> list      (name, vtype, value, editable, isdefault)
    history(role, name)      -> list      every value ever used, for Browse

    capture_dir() / capture_suite_id() / capture_archive_name()
    capture_led_color_order() / capture_camera_warmup_s()
    capture_prefetch_budget_s()
    capture_layer_keep_min_dn() / capture_layer_keep_max_dn()
    inspection_exposure_us() / default_focus_diopter()
    reagent1_secs() / reagent2_secs() / analysis_secs()
    main_loop_poll_s() / shutdown_timeout_s()
    backlight_pin()

Maintenance:
    python src/config_repo.py              run the self-test
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import db_manager as db
import version

log = logging.getLogger(__name__)

# The project root is the directory holding src/ and config/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Row vocabulary.
# `source` and `add_dt` record who created a row and when.  They are creation
# facts: nothing rewrites them afterwards, so `source` is an add-user and never
# doubles as an edit-user.  Which value is in force is carried by `active`
# alone, and which one Default offers by `isdefault` alone.
SOURCE_SEED     = "seed"         # created by config_defaults.sql — the factory
SOURCE_OPERATOR = "operator"     # created on the instrument
SOURCE_HISTORY  = "history"      # a prior value, seeded for Browse to offer
SOURCE_CODE     = "code"         # stamped from a constant in the source tree

# The one parameter the code owns rather than the seed file: it is a fact about
# the running software, not a setting, so config_defaults.sql does not carry it
# and stamp_version() below is what puts it in the table.
VERSION_ROLE = "device"
VERSION_NAME = "software_ver"

_SELECT = "SELECT role, name, vtype, value FROM config "

# Returned by _coerce for a value that does not read back as its vtype — a
# hand-edited typo.  Distinct from None, which is a value a row may hold.
_INVALID = object()

_cache:         "Dict[str, Dict[str, Any]] | None" = None
# The commissioned values — what `default()` reports and Restore offers.
_default_cache: "Dict[str, Dict[str, Any]] | None" = None


class ConfigMissing(KeyError):
    """
    No active row for a parameter the code asked for.

    Always a bug, never a condition to work around: db_bootstrap runs the seed
    file at every launch and fails loudly if a parameter has no active row, so
    by the time anything reads a setting the row is guaranteed to exist.  This
    means either the name is wrong, or the row was removed by hand.
    """


# ── Loading ─────────────────────────────────────────────────────────────────

def load(reload: bool = False) -> dict:
    """
    Return the parameters in force, reading the config table on first use.

    Args:
        reload: Re-read the table instead of returning the cached values.

    Returns:
        {role: {name: value}}, values coerced to the type `vtype` names.

    Raises:
        sqlite3.Error: The config table cannot be read.  Not caught here —
                       db_bootstrap has already created and verified it, so a
                       failure now is real and must not be papered over.
    """
    global _cache, _default_cache
    if _cache is not None and not reload:
        return _cache

    _cache         = _index(db.read_all(_SELECT + "WHERE active = 1"))
    _default_cache = _index(db.read_all(_SELECT + "WHERE isdefault = 1"))
    return _cache


def _index(rows: List[tuple]) -> Dict[str, Dict[str, Any]]:
    """
    Turn one layer's rows into {role: {name: coerced value}}.

    There is nothing to resolve.  Exactly one row per parameter carries each
    flag — the table's partial unique indexes guarantee it — so reading a
    setting is a lookup, not a merge.

    Until 2026-08 this merged three tiers, because a 'simulation' row could
    shadow the shipped one and an operator's edit had to shadow that in turn.
    Dropping the simulation profile removed the only case where two rows
    competed, and with it the only reason reading a value had to know *why* a
    row was in force.

    Args:
        rows: (role, name, vtype, value) tuples from one layer.

    Returns:
        {role: {name: value}}, skipping any row whose stored text does not
        read back as its declared type.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for role, name, vtype, value in rows:
        coerced = _coerce(value, vtype, role, name)
        if coerced is _INVALID:
            continue                     # a typo'd row answers for nothing
        out.setdefault(role, {})[name] = coerced
    return out


def _coerce(value: Optional[str], vtype: str, role: str = "", name: str = ""
            ) -> Any:
    """
    Read a stored TEXT value back as the type `vtype` names.

    An unparseable value is logged and answered with _INVALID rather than
    raised — a hand-edited row with a typo in it must not stop a capture; the
    tier below it answers instead, and if nothing does, get() raises with a
    name attached.  Also used by the config editor to validate what an operator
    has typed before it is saved.

    Args:
        value: The stored text, or None.
        vtype: One of 'int', 'float', 'bool', 'str', 'path'.
        role:  For the log message only.
        name:  For the log message only.

    Returns:
        The value as its declared type, None for a stored NULL, or the
        _INVALID sentinel if it does not parse.

    Example:
        >>> _coerce("42", "int")
        42
        >>> _coerce("later", "int") is _INVALID
        True
    """
    if value is None:
        return None
    try:
        if vtype == "int":
            return int(value)
        if vtype == "float":
            return float(value)
        if vtype == "bool":
            return str(value).strip().lower() in ("1", "true", "yes", "on")
    except (TypeError, ValueError):
        log.error("config: %s.%s = %r is not a valid %s — ignoring it",
                  role, name, value, vtype)
        return _INVALID
    return str(value)


def valid(value: str, vtype: str) -> bool:
    """
    True if `value` reads back as `vtype`.  For the config editor's field check.

    Args:
        value: The text an operator typed.
        vtype: The parameter's declared type.

    Example:
        >>> valid("1.5", "float"), valid("1.5", "int")
        (True, False)
    """
    return _coerce(value, vtype) is not _INVALID


# ── Reads ───────────────────────────────────────────────────────────────────

def get(role: str, name: str) -> Any:
    """
    Return one parameter's value.

    Args:
        role: The section the parameter sits under, e.g. 'protocol'.
        name: The parameter, e.g. 'reagent1_secs'.

    Returns:
        The value in force, coerced by its vtype.

    Raises:
        ConfigMissing: No active row for this parameter.

    Example:
        >>> get('protocol', 'reagent1_secs')            # doctest: +SKIP
        120
    """
    settings = load()
    if role in settings and name in settings[role]:
        return settings[role][name]
    raise ConfigMissing(f"{role}.{name}")


def default(role: str, name: str) -> Any:
    """
    Return the value the editor's Default button offers.

    This site's commissioned value, which an operator may have set with
    Default; not necessarily what the unit shipped with.

    Returns:
        The Default value, or None if the parameter has no isdefault row
        (Restore is then unavailable for it, which db_bootstrap warns about).
    """
    load()
    settings = _default_cache or {}
    if role in settings and name in settings[role]:
        return settings[role][name]
    return None


def snapshot() -> Dict[str, Dict[str, str]]:
    """
    Return every active value as stored text, grouped by role.

    Written into each capture archive so an .h5 records the full state of the
    instrument that produced it, and can be read back without this database.
    Deliberately the raw text rather than coerced values, so the archive
    carries what the table actually held.

    Returns:
        {role: {name: value}} for every active row.
    """
    out: Dict[str, Dict[str, str]] = {}
    for role, name, value in db.read_all(
            "SELECT role, name, value FROM config "
            "WHERE active = 1 ORDER BY role, name"):
        out.setdefault(role, {})[name] = value
    return out


def overrides() -> List[Tuple[str, str]]:
    """
    Return (role, name) for every parameter not on its Default value.

    These are the parameters restore() would change — an empty list means
    every Restore button is a no-op.  Note this is not what restore_all()
    undoes: that goes back to the factory value, which an operator may have
    moved away from with Default.

    Keyed on `isdefault`, because that is what restore() puts back.
    """
    rows = db.read_all("SELECT role, name FROM config WHERE active = 1 "
                       "AND isdefault = 0 ORDER BY role, name")
    return [(role, name) for role, name in rows]


# ── The config editor's reads ───────────────────────────────────────────────

def roles() -> List[str]:
    """
    Return the roles that have editable parameters, for the role screen.

    Simulation rows are excluded: they are development scaffolding, not
    something to offer an operator.
    """
    rows = db.read_all("SELECT DISTINCT role FROM config "
                       "WHERE active = 1 ORDER BY role")
    return [role for (role,) in rows]


def items(role: str) -> List[tuple]:
    """
    Return the parameters in force for one role, for the role screen.

    Args:
        role: The role to list.

    Returns:
        [(name, vtype, value, editable, isdefault)] ordered by name, with
        `value` the stored text.  `editable` is 0 for rows the editor must
        show read-only; `isdefault` is 1 when the value in force is also the
        commissioned one, so the editor can show there is nothing to restore.
    """
    return db.read_all(
        "SELECT name, vtype, value, editable, isdefault FROM config "
        "WHERE role = ? AND active = 1 ORDER BY name",
        (role,))


def history(role: str, name: str) -> List[tuple]:
    """
    Return every value this parameter has ever held, for the Browse screen.

    Args:
        role: The section.
        name: The parameter.

    Returns:
        [(value, add_dt, source, active, isdefault)] with the row in force
        first, the commissioned row second, and the rest newest first.
    """
    return db.read_all(
        "SELECT value, add_dt, source, active, isdefault FROM config "
        "WHERE role = ? AND name = ? "
        "ORDER BY active DESC, isdefault DESC, add_dt DESC, rowid DESC",
        (role, name))


# ── Writes ──────────────────────────────────────────────────────────────────

def _now_local() -> str:
    """Local wall-clock timestamp for add_dt, matching roi/exposure_repo."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _vtype_of(role: str, name: str) -> Optional[str]:
    """The vtype the table declares for a parameter, or None if unknown."""
    return db.read_val("SELECT vtype FROM config WHERE role = ? AND name = ? "
                       "ORDER BY active DESC LIMIT 1",
                       (role, name))


def save(role: str, name: str, value: Any,
         remark: Optional[str] = None) -> int:
    """
    Put a value in force, keeping the previous one as history.

    Three statements in one transaction, in an order the `config` table's
    partial unique indexes require: deactivate the row in force, insert the new
    value inactive, then activate it.  Inserting inactive means the table never
    momentarily holds two active rows for the parameter.

    A value the parameter has held before is *reused* rather than duplicated —
    un_config_value makes the insert a no-op and the activation finds the
    existing row.  So repeatedly toggling between two values leaves two rows,
    not a growing pile, and the Browse screen shows each distinct value once.

    The vtype carries over from the parameter's existing rows, so the value is
    stored as text and read back as the type it has always had.

    Args:
        role:   The section, e.g. 'protocol'.
        name:   The parameter, e.g. 'reagent1_secs'.
        value:  The new value; str() is what gets stored.
        remark: Optional note stored on the new row.

    Returns:
        The rowid now in force, or 0 if the parameter is unknown.

    Example:
        >>> save('protocol', 'reagent1_secs', 45)      # doctest: +SKIP
    """
    vtype = _vtype_of(role, name)
    if vtype is None:
        log.error("config.save: no parameter named %s.%s — nothing saved",
                  role, name)
        return 0

    text = str(value)
    now  = _now_local()
    db.write_tx([
        ("UPDATE config SET active = 0 "
         "WHERE role = ? AND name = ? AND active = 1",
         (role, name)),
        ("INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, "
         "role, name, vtype, value, source, remark) "
         "VALUES (0, 0, 1, ?, ?, ?, ?, ?, ?, ?)",
         (now, role, name, vtype, text, SOURCE_OPERATOR, remark)),
        # No source/add_dt on the UPDATE: the insert is a no-op when this value
        # is already in the history, and those two columns describe when and by
        # whom the row was *created*, which re-selecting it does not change.
        ("UPDATE config SET active = 1 "
         "WHERE role = ? AND name = ? AND value = ?",
         (role, name, text)),
    ])
    load(reload=True)
    rowid = db.read_val("SELECT rowid FROM config WHERE role = ? AND name = ? "
                        "AND active = 1", (role, name)) or 0
    log.info("config.save: %s.%s = %r (rowid %s)", role, name, value, rowid)
    return rowid


def set_default(role: str, name: str, value: Any,
                remark: Optional[str] = None) -> int:
    """
    Put a value in force *and* commission it as the parameter's default.

    What the config editor's Default button does.  The previously commissioned
    row keeps its place in the history but stops being what Restore offers.

    Args:
        role:   The section.
        name:   The parameter.
        value:  The value to commission.
        remark: Optional note stored on the row.

    Returns:
        The rowid now in force, or 0 if the parameter is unknown.
    """
    rowid = save(role, name, value, remark)
    if not rowid:
        return 0
    # Only the flag moves.  `source` still says who created the row, and the
    # factory row keeps its source='seed', so Restore Factory Defaults can
    # still find what shipped after this.
    db.write_tx([
        ("UPDATE config SET isdefault = 0 "
         "WHERE role = ? AND name = ? AND isdefault = 1",
         (role, name)),
        ("UPDATE config SET isdefault = 1 WHERE rowid = ?", (rowid,)),
    ])
    load(reload=True)
    log.info("config.set_default: %s.%s = %r", role, name, value)
    return rowid


def stamp_version() -> int:
    """
    Record the version of the software now running on this instrument.

    Called once per launch, by db_bootstrap.ensure(), before anything reads a
    setting.  Writes version.__version__ to device.software_ver, which from
    there reaches every capture archive (capture_meta.py).  Because superseded
    rows are kept, the parameter's history becomes a dated log of every version
    this instrument has ever run — the version stamp the Pi has never had.

    Not save(): that writes editable=1 and source='operator', and refuses a
    parameter it cannot already find a vtype for, which on a fresh database
    this is.  The three statements are otherwise save()'s, in the same order
    for the same reason — the partial unique indexes forbid a moment with two
    active rows, and un_config_value makes a repeat of an earlier version reuse
    its row instead of piling up duplicates.

    isdefault is claimed only on the first stamp, when the parameter has no
    default row at all.  That row is then the version the instrument was
    commissioned at, and later stamps leave it alone.  Nothing offers it — the
    row is editable=0, so the config editor's Default button is disabled — but
    db_bootstrap._verify() warns about a parameter with no default, and this is
    a real answer rather than a way to quiet the warning.

    Returns:
        The rowid now in force, or 0 if the running version was already the
        value in force, which is every launch but the first after a bump.
    """
    current = db.read_val(
        "SELECT value FROM config WHERE role = ? AND name = ? AND active = 1",
        (VERSION_ROLE, VERSION_NAME))
    if current == version.__version__:
        return 0

    has_default = db.read_val(
        "SELECT COUNT(*) FROM config WHERE role = ? AND name = ? "
        "AND isdefault = 1", (VERSION_ROLE, VERSION_NAME)) or 0

    now = _now_local()
    db.write_tx([
        ("UPDATE config SET active = 0 "
         "WHERE role = ? AND name = ? AND active = 1",
         (VERSION_ROLE, VERSION_NAME)),
        ("INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, "
         "role, name, vtype, value, source, remark) "
         "VALUES (0, ?, 0, ?, ?, ?, 'str', ?, ?, ?)",
         (0 if has_default else 1, now, VERSION_ROLE, VERSION_NAME,
          version.__version__, SOURCE_CODE,
          "version this instrument was commissioned at" if not has_default
          else "version this instrument ran")),
        ("UPDATE config SET active = 1 "
         "WHERE role = ? AND name = ? AND value = ?",
         (VERSION_ROLE, VERSION_NAME, version.__version__)),
    ])
    load(reload=True)
    rowid = db.read_val("SELECT rowid FROM config WHERE role = ? AND name = ? "
                        "AND active = 1",
                        (VERSION_ROLE, VERSION_NAME)) or 0
    log.info("config.stamp_version: %s.%s = %r (was %r)",
             VERSION_ROLE, VERSION_NAME, version.__version__, current)
    return rowid


def restore(role: str, name: str) -> int:
    """
    Put the commissioned value for a parameter back in force.

    Puts back the isdefault row — this site's commissioned value, which an
    operator may have moved with Default.  For the value the unit shipped
    with, see restore_all().

    Nothing is deleted, so the value being undone stays in the table as
    history.  Restoring a parameter already on its Default value writes
    nothing.

    Args:
        role: The section, e.g. 'protocol'.
        name: The parameter, e.g. 'reagent1_secs'.

    Returns:
        The rowid now in force; 0 if there was nothing to restore, or if the
        parameter has no commissioned row.
    """
    row = db.read_one("SELECT rowid, active FROM config WHERE role = ? "
                      "AND name = ? AND isdefault = 1",
                      (role, name))
    if row is None:
        log.error("config.restore: no commissioned row for %s.%s", role, name)
        return 0

    rowid, already_active = row
    if already_active:
        return 0                         # already commissioned — nothing to do

    db.write_tx([
        ("UPDATE config SET active = 0 "
         "WHERE role = ? AND name = ? AND active = 1",
         (role, name)),
        ("UPDATE config SET active = 1 WHERE rowid = ?", (rowid,)),
    ])
    load(reload=True)
    log.info("config.restore: %s.%s (rowid %s)", role, name, rowid)
    return rowid


def restore_all() -> int:
    """
    Put every parameter back to the value the instrument shipped with.

    What the config editor's "Restore Factory Defaults" does, and deliberately
    a different thing from Restore: it goes back to the factory value, not to
    whatever the operator has since commissioned with Default.

    Keyed on source='seed', because that is what config_defaults.sql writes and
    nothing rewrites afterwards.  `isdefault` cannot answer this — an operator
    who presses Default moves that flag onto their own value, which is exactly
    what this has to undo.  It leaves `isdefault` alone as well, so a
    commissioned site value stays on the Default button.

    Two statements, in this order because the partial unique index forbids a
    moment with two active rows: stand everything down, then raise the factory
    rows.  A parameter with no factory row is left inactive, which
    db_bootstrap's verify reports on the next launch.

    device.software_ver is the one parameter that has no factory row by design
    — the source tree owns it, not config_defaults.sql — so it is stamped again
    here.  Without that it would be left with nothing active and the next read
    of it would raise ConfigMissing.  It is also not a setting, so putting it
    "back" to an older version would be a lie about what is running.

    Returns:
        Number of parameters that were not already on their factory value.
        software_ver is not counted: it is restamped, not restored.
    """
    changed = db.read_val(
        "SELECT COUNT(*) FROM config c WHERE c.active = 1 AND c.source <> ? "
        "AND NOT (c.role = ? AND c.name = ?)",
        (SOURCE_SEED, VERSION_ROLE, VERSION_NAME)) or 0
    db.write_tx([
        ("UPDATE config SET active = 0 WHERE active = 1", None),
        ("UPDATE config SET active = 1 WHERE source = ?", (SOURCE_SEED,)),
    ])
    stamp_version()
    load(reload=True)
    log.info("config.restore_all: %d parameter(s) put back", changed)
    return changed


# ── Capture output ──────────────────────────────────────────────────────────
# Everything about where an archive is written and what it is called.

def _resolve_path(value: str) -> Path:
    """Expand ~ and resolve a relative path against the project root."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def capture_dir() -> Path:
    """
    Return the directory capture archives are written to.

    capture.directory, resolved against the project root when relative.
    """
    return _resolve_path(str(get("capture", "directory")))


def capture_suite_id(when: "datetime | None" = None) -> str:
    """
    Return the identifier naming one capture set, e.g. "20260719_1343".

    Formatted with capture.timestamp_format.  Also stored as the archive's
    suite_id attribute.

    Args:
        when: Timestamp to format; defaults to now.
    """
    when = when or datetime.now()
    return when.strftime(str(get("capture", "timestamp_format")))


def capture_archive_name(suite_id: str) -> str:
    """Return the archive filename for a suite id, e.g. "20260719_1343.h5"."""
    return f"{suite_id}{get('capture', 'extension')}"


def capture_archive_path(out_dir: Path, stem: str) -> Path:
    """
    Return a path in out_dir named for `stem` that no file is using yet.

    Archives are named after the sample they hold — "ZP1007.h5" — because
    that is how an operator looks for one.  Sample numbers are unique in the
    `samples` table, so ordinarily the plain name is free and is what you get.

    A clash is still possible: a database restored from a backup can reissue
    a sample number whose archive is already sitting in the directory, and
    archives synced from an instrument are not covered by that table's
    uniqueness at all.  Rather than overwrite a capture that cannot be taken
    again, a letter is appended — ZP1007.h5, then ZP1007-a.h5, ZP1007-b.h5,
    and so on.  Past the alphabet it falls back to the capture timestamp, so
    this always returns something rather than failing a run.

    Args:
        out_dir: Directory the archive will be written to.
        stem:    Filename without extension — a sample number, or the capture
                 timestamp for a menu-launched capture with no sample.

    Returns:
        A path that did not exist at the moment it was checked.

    Example:
        >>> capture_archive_path(Path("/captures"), "ZP1007")  # doctest: +SKIP
        PosixPath('/captures/ZP1007.h5')
    """
    ext = str(get("capture", "extension"))
    out_dir = Path(out_dir)

    candidate = out_dir / f"{stem}{ext}"
    if not candidate.exists():
        return candidate

    for i in range(26):                      # 'a' through 'z'
        candidate = out_dir / f"{stem}-{chr(ord('a') + i)}{ext}"
        if not candidate.exists():
            return candidate

    return out_dir / f"{stem}-{capture_suite_id()}{ext}"


# ── Capture selection ───────────────────────────────────────────────────────
# Which LEDs a run sweeps — see capture_led_color_order() below, the one
# representation both capture paths read.

def inspection_exposure_us() -> int:
    """Operator-saved exposure from the viewer's Img recapture dialog."""
    return int(get("capture", "inspection_exposure_us"))


def default_focus_diopter() -> float:
    """Operator-saved focus diopter from the viewer's Img recapture dialog."""
    return float(get("capture", "default_focus_diopter"))


def capture_led_color_order() -> list:
    """LED color-name sweep order for normal captures, as a list."""
    raw = str(get("capture", "led_color_order"))
    return [c.strip() for c in raw.split(",") if c.strip()]


def capture_led_color_order_history() -> List[Tuple[int, str]]:
    """
    (rowid, value) for every led_color_order this instrument has ever held,
    for the LED Color picker screen's Prior/Next paging.

    Ordered like history(): the value in force first, then the commissioned
    default, then the rest newest first.
    """
    return db.read_all(
        "SELECT rowid, value FROM config WHERE role = 'capture' "
        "AND name = 'led_color_order' "
        "ORDER BY active DESC, isdefault DESC, add_dt DESC, rowid DESC")


def capture_camera_warmup_s() -> float:
    """Seconds to warm the camera before captures (thermal-stability margin)."""
    return float(get("capture", "camera_warmup_s"))


def capture_prefetch_budget_s() -> float:
    """
    Reserved time for the normal path's prefetch stage, in seconds.

    How long before a reagent2 countdown expires the arbiter starts
    CaptureRunner.run_prefetch(), so its work is finished by the time CAPTURE
    becomes reachable.  Also part of how early the shared camera is opened —
    see arbiter._camera_open_lead_s().

    Was `dark_prefetch_budget_s`, and was 20 s because the stage took a batch
    of thirty dark frames.  It no longer does: every dark frame is taken after
    the LED sweep now, and what is left here is camera setup and focus.  Worth
    re-measuring with src/capture_profile.py rather than assuming the old
    number still fits.
    """
    return float(get("capture", "prefetch_budget_s"))


def capture_layer_keep_min_dn() -> int:
    """
    Floor below which a Bayer layer is too dark to be worth storing, in DN.

    Compared against the layer's tenth-percentile dark-subtracted signal, taken
    from `layermodels` and scaled to the exposure actually being captured — see
    layermodel_repo.keep_channels().  The seeded 96 DN is a tenth of the
    sensor's 959 DN usable range (1023 minus a 64 DN black level), matching the
    threshold the offline flat-field analysis calls under-exposed.
    """
    return int(get("capture", "layer_keep_min_dn"))


def capture_layer_keep_max_dn() -> int:
    """
    Ceiling above which a Bayer layer is too bright to be worth storing, in DN.

    The companion to capture_layer_keep_min_dn(), compared against the layer's
    ninetieth-percentile signal.  The seeded 863 DN is nine tenths of the same
    959 DN range, so a layer whose bright end is approaching the sensor's
    non-linear knee is rejected rather than stored and later distrusted.
    """
    return int(get("capture", "layer_keep_max_dn"))


def capture_photo_enabled() -> bool:
    """True when a run ends by photographing the sample under the white LED."""
    return bool(get("capture", "photo_enabled"))


def capture_photo_wb_gains() -> tuple:
    """
    Colour gains (red, blue) rendering the final photo, as a 2-tuple.

    Fixed gains rather than libcamera's AWB because the light is known: the
    photo is always taken under the white LED, so what a general-purpose AWB
    algorithm would spend several frames converging towards can simply be
    written down once per unit and applied instantly.  Commission them by
    photographing a neutral card and dividing its green mean by its red and
    blue means; the seeded 1.0,1.0 is the uncorrected sensor response, which
    looks the same as today's objective capture until somebody measures it.

    Returns:
        (red_gain, blue_gain).  Falls back to (1.0, 1.0) if the stored text
        is not two numbers, since an unusable photo is a far smaller loss
        than a run that stops.
    """
    raw = str(get("capture", "photo_wb_gains"))
    try:
        red, blue = (float(p) for p in raw.split(","))
        return (red, blue)
    except ValueError:
        log.error("config: capture.photo_wb_gains = %r is not 'red,blue' — "
                  "using 1.0,1.0", raw)
        return (1.0, 1.0)


# ── Image regions ───────────────────────────────────────────────────────────

def required_roi_roles() -> tuple:
    """
    Return the ROI roles the instrument refuses to run without.

    roi.required_roles, as a tuple of role names.  Stated in *roles* rather
    than names because a name is an operator-editable label — see src/roi.py's
    "Roles" section — and non-editable in the config table because it decides
    what the capture and analysis paths may assume exists.

    Returns:
        Tuple of role strings, in the order they are listed.

    Example:
        >>> required_roi_roles()                        # doctest: +SKIP
        ('bound', 'sample', 'white_balance')
    """
    raw = str(get("roi", "required_roles"))
    return tuple(r.strip() for r in raw.split(",") if r.strip())


# ── Protocol timing ─────────────────────────────────────────────────────────
# Durations of the timed steps, in seconds.  A shorter pair used to be seeded
# under a 'simulation' profile so desktop runs finished quickly; that profile
# is gone, so a developer wanting short waits sets them in their own database
# ($COLORIMETER_DB) or from the config editor.

def reagent1_secs() -> int:
    """Countdown for reagent step 1, in seconds."""
    return int(get("protocol", "reagent1_secs"))


def reagent2_secs() -> int:
    """Countdown for reagent step 2, in seconds."""
    return int(get("protocol", "reagent2_secs"))


def analysis_secs() -> float:
    """Placeholder analysis duration, in seconds (until the engine lands)."""
    return float(get("protocol", "analysis_secs"))


# ── Runtime timing ──────────────────────────────────────────────────────────

def main_loop_poll_s() -> float:
    """Main event-loop cadence, in seconds."""
    return float(get("runtime", "main_loop_poll_s"))


def shutdown_timeout_s() -> float:
    """Seconds before the shutdown screen auto-selects Exit."""
    return float(get("runtime", "shutdown_timeout_s"))


# ── Hardware ────────────────────────────────────────────────────────────────

def backlight_pin() -> int:
    """GPIO pin driving the display backlight."""
    return int(get("hardware", "backlight_pin"))


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import shutil
    import tempfile

    import db_bootstrap

    logging.basicConfig(level=logging.CRITICAL)

    _failures = 0

    def check(label: str, got, want) -> None:
        global _failures
        ok = got == want
        if not ok:
            _failures += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label}"
              f"{'' if ok else f'   got {got!r}, want {want!r}'}")

    # Everything below runs against a throwaway database built the way the
    # application builds its own, so the live config table is never written.
    _tmpdir = Path(tempfile.mkdtemp())
    _tmpdb  = _tmpdir / "config_selftest.sqlite"
    db.DBNAME = str(_tmpdb)
    db_bootstrap.ensure(_tmpdb)
    load(reload=True)

    print("== the seeded values are what the table holds ==")
    check("reagent1", reagent1_secs(), 120)
    check("extension", capture_archive_name("X"), "X.h5")
    check("dir is absolute", capture_dir().is_absolute(), True)
    check("colour order", capture_led_color_order()[0], "photo_red")
    check("backlight", backlight_pin(), 13)

    print("== a missing parameter raises, it does not quietly work ==")
    try:
        get("protocol", "no_such_param")
        check("raised", False, True)
    except ConfigMissing as e:
        check("raised", "no_such_param" in str(e), True)

    print("== saving supersedes, keeps history, and can be restored ==")
    check("saved", save("protocol", "reagent1_secs", 45) > 0, True)
    check("in force", reagent1_secs(), 45)
    check("commissioned unchanged", default("protocol", "reagent1_secs"), 120)
    check("listed as an override",
          ("protocol", "reagent1_secs") in overrides(), True)
    check("history has both",
          [r[0] for r in history("protocol", "reagent1_secs")], ["45", "120"])
    check("restored", restore("protocol", "reagent1_secs") > 0, True)
    check("back in force", reagent1_secs(), 120)
    check("no overrides left", overrides(), [])
    check("restoring again is a no-op", restore("protocol", "reagent1_secs"), 0)

    print("== reusing a former value does not duplicate a row ==")
    save("protocol", "reagent1_secs", 45)
    save("protocol", "reagent1_secs", 120)
    save("protocol", "reagent1_secs", 45)
    check("still two rows", len(history("protocol", "reagent1_secs")), 2)
    check("in force", reagent1_secs(), 45)

    print("== the table's invariants survive all that ==")
    _con = sqlite3.connect(_tmpdb)
    check("one active row each",
          _con.execute("SELECT COUNT(*) FROM (SELECT 1 FROM config "
                       "GROUP BY role, name HAVING SUM(active) <> 1)"
                       ).fetchone()[0], 0)
    _con.close()

    print("== Default commissions a site value, distinct from the factory ==")
    check("set", set_default("protocol", "reagent1_secs", 45) > 0, True)
    check("Default now offers it", default("protocol", "reagent1_secs"), 45)
    check("so Restore has nothing to do", overrides(), [])
    check("but the factory row keeps its source",
          [r[2] for r in history("protocol", "reagent1_secs")
           if r[0] == "120"], ["seed"])

    print("== Restore Factory Defaults ignores the commissioned value ==")
    save("capture", "extension", ".hdf5")
    check("two off the factory value", restore_all(), 2)
    check("reagent1 back to the factory", reagent1_secs(), 120)
    check("extension back", capture_archive_name("X"), "X.h5")
    check("Default still offers the site value",
          default("protocol", "reagent1_secs"), 45)
    check("which Restore can put back",
          restore("protocol", "reagent1_secs") > 0, True)
    check("in force again", reagent1_secs(), 45)
    set_default("protocol", "reagent1_secs", 120)
    restore_all()

    print("== the editor's reads ==")
    check("roles", roles(), ["capture", "defaults", "device", "hardware",
                             "protocol", "roi", "runtime"])
    _items = dict((n, (v, t)) for n, t, v, _, _ in items("device"))
    check("device names", sorted(_items), ["description", "hardware_sn",
                                           "software_ver"])
    check("serial value", _items["hardware_sn"][0], "XCN-001")
    check("serial is locked",
          [e for n, _, _, e, _ in items("device") if n == "hardware_sn"], [0])
    check("version comes from the source tree",
          _items["software_ver"][0], version.__version__)
    check("version is locked",
          [e for n, _, _, e, _ in items("device") if n == "software_ver"], [0])
    check("restamping at the same version writes nothing", stamp_version(), 0)
    check("reagent1 is editable",
          [e for n, _, _, e, _ in items("protocol")
           if n == "reagent1_secs"], [1])
    check("kit history offers the other kit",
          sorted(r[0] for r in history("defaults", "testkit")),
          ["Ascel 458201", "Hach 1"])
    check("no simulation rows on the role screen",
          "simulation" in str(roles()), False)

    print("== validation, for the editor's fields ==")
    check("int ok",      valid("42", "int"), True)
    check("int rejects", valid("4.2", "int"), False)
    check("float ok",    valid("4.2", "float"), True)
    check("str takes anything", valid("anything", "str"), True)

    print("== the archive snapshot ==")
    _snap = snapshot()
    check("grouped by role", _snap["protocol"]["reagent1_secs"], "120")
    check("text, not coerced", isinstance(_snap["capture"]["led_color_order"], str),
          True)
    check("identity travels too", _snap["device"]["hardware_sn"], "XCN-001")

    print("== an unknown parameter cannot be written ==")
    check("save refused", save("nope", "nothing", 1), 0)
    check("restore refused", restore("nope", "nothing"), 0)

    shutil.rmtree(_tmpdir, ignore_errors=True)

    print(f"\n{'PASSED' if not _failures else f'FAILURES: {_failures}'}")
    raise SystemExit(1 if _failures else 0)
