"""
config_repo.py  —  Every tunable instrument parameter, over the `config` table.
------------------------------------------------------------------------------
This is the one place to look when asking "where is that configured?".  It
replaced config/instrument.yaml and config_manager.py in 2026-08: a parameter
is now a row, editable from `sqlite3` on the instrument exactly like `exposure`
and `roi`, with the same supersede-don't-delete history.

    sqlite3 src/colorimeter.sqlite \\
      "SELECT role, name, vtype, value FROM config WHERE active = 1"

Data access is modelled on exposure_repo.py and roi.py: an `active` flag marks
the row in force, an `isdefault` row is the commissioned fallback that Restore
copies back, and every write inserts rather than overwrites.

Values are read once and cached; call load(reload=True) after editing the table
in a long-running process (save() and restore() do it for you).

Three tiers resolve to the value in force, each winning over the one before it —
the same order the YAML layers had:

    profile = ''            what the instrument ships with
    profile = 'simulation'  only while COLORIMETER_SIMULATION=1
    source  = 'operator'    what was set on Menu -> Config

The operator's tier comes last, so a duration entered on the instrument holds
whichever mode it is running in.

If the table is missing or a parameter has no row, SEED_PARAMS below answers
instead and a warning is logged — a capture never fails merely because the
config table was not seeded.  seed_defaults() repairs such a database in place.

The one setting that is *not* here is the database path itself: config_repo
reads its parameters out of the database, so it cannot say where the database
is.  db_manager.py owns that (DBNAME / db_path, $COLORIMETER_DB).

Usage:
    import config_repo as config

    suite_id = config.capture_suite_id()             # "20260719_1343"
    path     = config.capture_dir() / config.capture_archive_name(suite_id)

Public surface
--------------
    load(reload=False) -> dict            {role: {name: coerced value}}
    get(role, name)                       the value in force
    default(role, name)                   the commissioned value — what Restore offers
    save(role, name, value)  -> int       supersede; new rowid
    restore(role, name)      -> int       put the commissioned value back; new rowid
    overrides()              -> list      (role, name) the operator has changed
    seed_defaults()          -> int       rows written for missing parameters
    is_simulation()          -> bool

    capture_dir() / capture_suite_id() / capture_archive_name()
    capture_led_start() / capture_led_end() / fallback_exposure_group()
    reagent1_secs() / reagent2_secs() / analysis_secs()
    main_loop_poll_s() / shutdown_timeout_s()
    default_rowid() / backlight_pin()

Maintenance:
    python src/config_repo.py              run the self-test
    python src/config_repo.py --emit-sql   regenerate the seed SQL
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import db_manager as db

log = logging.getLogger(__name__)

# The project root is the directory holding src/ and config/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Overrides capture.directory for one process, for tests and ad-hoc runs.
CAPTURE_DIR_ENV = "COLORIMETER_CAPTURE_DIR"
# "1" → run against mock hardware and select the simulation profile.  Set by
# run_simulation.py; it is the single switch the hardware selection and this
# module both key off, so the two never disagree.
SIMULATION_ENV = "COLORIMETER_SIMULATION"

# Row vocabulary.
SIM_PROFILE     = "simulation"   # config.profile of a simulation-only value
SOURCE_SEED     = "seed"         # written by the commissioned seed
SOURCE_OPERATOR = "operator"     # written on the Menu -> Config screen

# The date stamped on generated seed rows.  Fixed, not now(), so re-emitting
# the SQL produces a byte-identical block.
SEED_DT = "2026-08-01 00:00:00"

# ── The commissioned parameters ─────────────────────────────────────────────
# role, name, vtype, value, profile, remark — the single source of the default
# values.  db_DDL.sql's seed block is generated from this tuple by --emit-sql,
# and the self-test checks the two still agree.  Edit here, not there.
#
# `value` is written as it is stored: TEXT.  `vtype` says how to read it back.
SEED_PARAMS: Tuple[Tuple[str, str, str, str, str, str], ...] = (
    # Where capture archives are written and what they are called.
    ("capture", "directory", "path", "captures", "",
     "capture archives; relative paths resolve against the project root"),
    ("capture", "timestamp_format", "str", "%Y%m%d_%H%M", "",
     "strftime format naming each archive, and stored as its suite_id"),
    ("capture", "extension", "str", ".h5", "",
     "archive filename extension"),
    # Which LEDs a run sweeps, and the group used when none is saved.
    ("capture", "led_start", "int", "1", "",
     "first LED channel a capture run sweeps (inclusive)"),
    ("capture", "led_end", "int", "10", "",
     "last LED channel a capture run sweeps (inclusive)"),
    ("capture", "fallback_exposure_group", "str", "3", "",
     "exposure group preselected when the saved one is missing or inactive"),

    # Timed protocol steps, in seconds.  The simulation rows shorten the
    # reagent waits so a run is quick to test, but only in simulation; a
    # duration set on Menu -> Config wins over both.
    ("protocol", "reagent1_secs", "int", "120", "",
     "countdown for reagent step 1"),
    ("protocol", "reagent1_secs", "int", "10", SIM_PROFILE,
     "short wait so a simulated run is quick to test"),
    ("protocol", "reagent2_secs", "int", "420", "",
     "countdown for reagent step 2"),
    ("protocol", "reagent2_secs", "int", "10", SIM_PROFILE,
     "short wait so a simulated run is quick to test"),
    ("protocol", "analysis_secs", "float", "1.5", "",
     "placeholder analysis duration until the analysis engine lands"),

    # Event-loop and screen timing.
    ("runtime", "main_loop_poll_s", "float", "0.02", "",
     "main event-loop cadence"),
    ("runtime", "shutdown_timeout_s", "float", "15.0", "",
     "shutdown screen auto-picks Exit after this"),

    # Which default_gui_vals row seeds the entry screens.
    ("defaults", "rowid", "int", "1", "",
     "the default_gui_vals row that seeds the entry screens"),

    ("hardware", "backlight_pin", "int", "13", "",
     "BCM GPIO driving the display backlight"),
)

_SELECT = ("SELECT role, name, vtype, value, profile, source FROM config ")

# Returned by _coerce for a value that does not read back as its vtype — a
# hand-edited typo.  Distinct from None, which is a value a row may hold.
_INVALID = object()

_cache:         "Dict[str, Dict[str, Any]] | None" = None
# The commissioned values — what `default()` reports and Restore offers.
_default_cache: "Dict[str, Dict[str, Any]] | None" = None


# ── Simulation ──────────────────────────────────────────────────────────────

def is_simulation() -> bool:
    """
    True when running with mock hardware / the simulation profile.

    The COLORIMETER_SIMULATION env var is the only switch — it is what also
    selects mock hardware (arbiter.py, lighting_cal8.py read it directly), so
    the two never disagree.
    """
    return os.environ.get(SIMULATION_ENV, "0") == "1"


# ── Loading ─────────────────────────────────────────────────────────────────

def load(reload: bool = False) -> dict:
    """
    Return the parameters in force, reading the config table on first use.

    Args:
        reload: Re-read the table instead of returning the cached values.

    Returns:
        {role: {name: value}}, values coerced to the type `vtype` names.
        Empty if the table is missing — get() then falls back to SEED_PARAMS.
    """
    global _cache, _default_cache
    if _cache is not None and not reload:
        return _cache

    sim = is_simulation()
    _cache         = _resolve(_read_rows("active = 1"), sim)
    _default_cache = _resolve(_read_rows("isdefault = 1"), sim)
    if not _cache:
        log.warning("config: no active rows in the config table — using the "
                    "built-in seed values; run config_repo.seed_defaults()")
    return _cache


def _read_rows(where: str) -> List[tuple]:
    """Read one layer of the table, returning [] if it cannot be read."""
    try:
        return db.read_all(_SELECT + "WHERE " + where)
    except sqlite3.Error:
        log.exception("config: cannot read the config table — using the "
                      "built-in seed values")
        return []


def _resolve(rows: List[tuple], sim: bool) -> Dict[str, Dict[str, Any]]:
    """
    Merge one layer's rows into {role: {name: value}}.

    Three tiers, each winning over the one before it:

        profile = ''            what the instrument ships with
        profile = 'simulation'  only while simulation is active
        source  = 'operator'    what was set on Menu -> Config

    The operator's tier comes last so a value entered on the instrument holds
    in simulation too, exactly as user_settings.json used to.  A row carrying
    some other profile name belongs to nothing here and is skipped.
    """
    shipped: Dict[str, Dict[str, Any]] = {}
    profile: Dict[str, Dict[str, Any]] = {}
    operator: Dict[str, Dict[str, Any]] = {}

    for role, name, vtype, value, row_profile, source in rows:
        if source == SOURCE_OPERATOR:
            tier = operator
        elif row_profile == SIM_PROFILE:
            if not sim:
                continue
            tier = profile
        elif row_profile:
            continue
        else:
            tier = shipped
        coerced = _coerce(value, vtype, role, name)
        if coerced is _INVALID:
            continue                     # a typo'd row answers for nothing
        tier.setdefault(role, {})[name] = coerced

    merged: Dict[str, Dict[str, Any]] = {}
    for tier in (shipped, profile, operator):
        for role, items in tier.items():
            merged.setdefault(role, {}).update(items)
    return merged


def _coerce(value: Optional[str], vtype: str, role: str, name: str) -> Any:
    """
    Read a stored TEXT value back as the type `vtype` names.

    An unparseable value is logged and answered with _INVALID rather than
    raised — a hand-edited row with a typo in it must not stop a capture.  The
    caller drops such a row, so the tier below it (ultimately SEED_PARAMS)
    answers instead.  A stored NULL is a value, and comes back as None.

    'path' and 'str' are returned as text; capture_dir() resolves a path.
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


# ── Reads ───────────────────────────────────────────────────────────────────

def get(role: str, name: str) -> Any:
    """
    Return one parameter's value, falling back to the built-in seed value.

    Args:
        role: The section the parameter sits under, e.g. 'protocol'.
        name: The parameter, e.g. 'reagent1_secs'.

    Returns:
        The value in force, coerced by its vtype; the SEED_PARAMS value when
        the table has no row for it; None when nothing knows the parameter.
    """
    return _lookup(load(), role, name)


def default(role: str, name: str) -> Any:
    """
    Return one parameter as the instrument was commissioned with it — what
    `get` would return had the operator never changed it on the Config screen.
    The Config screen's Restore offers these values.
    """
    load()
    return _lookup(_default_cache or {}, role, name)


def _lookup(settings: dict, role: str, name: str) -> Any:
    if role in settings and name in settings[role]:
        return settings[role][name]
    return _seed_value(role, name)


def _seed_value(role: str, name: str) -> Any:
    """
    The built-in value for a parameter the table does not carry.

    Simulation rows are honoured here too, so a database missing the config
    table still behaves like a seeded one.
    """
    found = None
    for seed_role, seed_name, vtype, value, profile, _ in SEED_PARAMS:
        if (seed_role, seed_name) != (role, name):
            continue
        if profile == SIM_PROFILE and not is_simulation():
            continue
        found = _coerce(value, vtype, role, name)
        if profile == SIM_PROFILE:
            break               # the simulation value wins when it applies
    if found is None:
        log.error("config: no parameter named %s.%s", role, name)
    return None if found is _INVALID else found


def _seed_vtype(role: str, name: str) -> Optional[str]:
    """The vtype SEED_PARAMS declares for a parameter, or None if unknown."""
    for seed_role, seed_name, vtype, _, _, _ in SEED_PARAMS:
        if (seed_role, seed_name) == (role, name):
            return vtype
    return None


def overrides() -> List[Tuple[str, str]]:
    """
    Return (role, name) for every parameter the operator has changed.

    The Config screen's Restore removes one; an empty list means nothing is
    overridden and the instrument is running on its commissioned values.
    """
    try:
        rows = db.read_all("SELECT role, name FROM config "
                           "WHERE active = 1 AND source = ? ORDER BY role, name",
                           (SOURCE_OPERATOR,))
    except sqlite3.Error:
        log.exception("config.overrides: cannot read the config table")
        return []
    return [(role, name) for role, name in rows]


# ── Writes ──────────────────────────────────────────────────────────────────

def _now_local() -> str:
    """Local wall-clock timestamp for add_dt, matching roi/exposure_repo."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _insert(role: str, name: str, vtype: str, value: Optional[str],
            profile: str, source: str, remark: Optional[str],
            isdefault: int = 0, active: int = 1) -> int:
    """Insert one config row and return its rowid (0 if the write failed)."""
    db.write_one(
        "INSERT INTO config (active, isdefault, add_dt, role, name, vtype, "
        "value, profile, source, remark) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (active, isdefault, _now_local(), role, name, vtype,
         value, profile, source, remark))
    return db.read_val("SELECT MAX(rowid) FROM config") or 0


def save(role: str, name: str, value: Any,
         remark: Optional[str] = None) -> int:
    """
    Supersede a parameter with a value set on the instrument.

    The current active row is flipped to active=0 (kept as history, exactly as
    a recalibration keeps the previous exposures) and a new active row with
    source='operator' is inserted.  The isdefault row is untouched, so
    restore() can still undo this, and the operator's tier wins over the
    simulation profile so the value holds in either mode.

    The vtype carries over from the row being superseded, so the value is
    stored as text and read back as the type the parameter has always had.

    Args:
        role:   The section, e.g. 'protocol'.
        name:   The parameter, e.g. 'reagent1_secs'.
        value:  The new value; str() is what gets stored.
        remark: Optional note stored on the new row.

    Returns:
        The new row's rowid, or 0 if the parameter is unknown.

    Example:
        >>> save('protocol', 'reagent1_secs', 45)      # doctest: +SKIP
    """
    row = db.read_one(_SELECT + "WHERE role = ? AND name = ? AND active = 1 "
                                "AND profile = ''", (role, name))
    vtype = row[2] if row else _seed_vtype(role, name)
    if vtype is None:
        log.error("config.save: no parameter named %s.%s — nothing saved",
                  role, name)
        return 0

    db.write_one("UPDATE config SET active = 0 "
                 "WHERE role = ? AND name = ? AND active = 1 AND profile = ''",
                 (role, name))
    rowid = _insert(role, name, vtype, str(value), "", SOURCE_OPERATOR, remark)
    load(reload=True)
    log.info("config.save: %s.%s = %r (rowid %s)", role, name, value, rowid)
    return rowid


def restore(role: str, name: str) -> int:
    """
    Put the commissioned value for a parameter back in force.

    Copies the isdefault row into a new active row and deactivates the current
    one, mirroring roi.restore().  Nothing is deleted, so the override being
    undone stays in the table as history and the isdefault row is never
    consumed.  With the operator's row gone, the simulation profile applies
    again when simulation is active.

    Restoring a parameter that is already on its commissioned value writes
    nothing — pressing Restore on an untouched instrument must not grow the
    table a pair of rows at a time.

    Args:
        role: The section, e.g. 'protocol'.
        name: The parameter, e.g. 'reagent1_secs'.

    Returns:
        The new row's rowid; 0 if there was nothing to restore, or if the
        parameter has no isdefault row.

    Example:
        >>> restore('protocol', 'reagent1_secs')       # doctest: +SKIP
    """
    row = db.read_one(_SELECT + "WHERE role = ? AND name = ? AND isdefault = 1 "
                                "AND profile = ''", (role, name))
    if row is None:
        log.error("config.restore: no commissioned row for %s.%s", role, name)
        return 0

    _, _, vtype, value, _, _ = row
    current = db.read_one("SELECT value, source FROM config WHERE role = ? "
                          "AND name = ? AND active = 1 AND profile = ''",
                          (role, name))
    if current is not None and current == (value, SOURCE_SEED):
        return 0                         # already commissioned — nothing to undo

    db.write_one("UPDATE config SET active = 0 "
                 "WHERE role = ? AND name = ? AND active = 1 AND profile = ''",
                 (role, name))
    rowid = _insert(role, name, vtype, value, "", SOURCE_SEED,
                    "restored from default")
    load(reload=True)
    log.info("config.restore: %s.%s = %r (rowid %s)", role, name, value, rowid)
    return rowid


def seed_defaults() -> int:
    """
    Write the commissioned rows for any parameter missing from the table.

    db_DDL.sql seeds these already, so this is for a database that predates the
    `config` table or has had rows removed.  Parameters that already have rows
    are left alone — this never overwrites an operator's setting.  Each missing
    one gets two rows: the isdefault fallback (active=0) and the row in force
    (active=1), exactly as roi.seed_defaults() does.

    Returns:
        Number of rows inserted (2 per missing parameter; 0 when the table is
        complete, or when it cannot be written).

    Example:
        >>> seed_defaults()
        0
    """
    written = 0
    try:
        for role, name, vtype, value, profile, remark in SEED_PARAMS:
            if db.read_val("SELECT COUNT(*) FROM config "
                           "WHERE role = ? AND name = ? AND profile = ?",
                           (role, name, profile)):
                continue
            _insert(role, name, vtype, value, profile, SOURCE_SEED,
                    remark, isdefault=1, active=0)
            _insert(role, name, vtype, value, profile, SOURCE_SEED,
                    remark, isdefault=0, active=1)
            written += 2
            log.info("config.seed_defaults: seeded %s.%s = %r", role, name, value)
    except sqlite3.Error:
        log.exception("config.seed_defaults: cannot write the config table")
        return written
    if written:
        load(reload=True)
    return written


# ── Capture output ──────────────────────────────────────────────────────────
# Everything about where an archive is written and what it is called.

def _resolve_path(value: str) -> Path:
    """Expand ~ and resolve a relative path against the project root."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def capture_dir() -> Path:
    """
    Return the directory capture archives are written to.

    $COLORIMETER_CAPTURE_DIR wins if set.  Otherwise capture.directory,
    resolved against the project root when relative.
    """
    from_env = os.environ.get(CAPTURE_DIR_ENV)
    if from_env:
        return Path(from_env).expanduser()
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


# ── Capture selection ───────────────────────────────────────────────────────
# Which LEDs a run sweeps, and the exposure group used when none is saved.

def capture_led_start() -> int:
    """First (inclusive) LED channel a capture run sweeps."""
    return int(get("capture", "led_start"))


def capture_led_end() -> int:
    """Last (inclusive) LED channel a capture run sweeps."""
    return int(get("capture", "led_end"))


def fallback_exposure_group() -> str:
    """Exposure group to preselect when the saved one is missing or inactive."""
    return str(get("capture", "fallback_exposure_group"))


# ── Protocol timing ─────────────────────────────────────────────────────────
# Durations of the timed steps, in seconds.  The simulation profile shortens
# the reagent waits, and the Menu -> Config screen overrides both (saved
# through save(), so a custom wait holds on real hardware too).

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


# ── Reference data ──────────────────────────────────────────────────────────

def default_rowid() -> int:
    """The default_gui_vals rowid that seeds the entry screens."""
    return int(get("defaults", "rowid"))


# ── Hardware ────────────────────────────────────────────────────────────────

def backlight_pin() -> int:
    """GPIO pin driving the display backlight."""
    return int(get("hardware", "backlight_pin"))


# ── Seed SQL generation ─────────────────────────────────────────────────────
# SEED_PARAMS is the single source of the commissioned values.  The seed block
# in db_DDL.sql and provisioning/config_defaults.sql are both generated from
# it, and the self-test below checks db_DDL.sql has not drifted.

_SEED_MARK_BEGIN = "-- ── config seed ─── generated by config_repo.py --emit-sql ──"
_SEED_MARK_END   = "-- ── end config seed ──"

DDL_PATH      = PROJECT_ROOT / "src" / "db_DDL.sql"
DEFAULTS_PATH = PROJECT_ROOT / "provisioning" / "config_defaults.sql"


def _sql_quote(value: Optional[str]) -> str:
    """Render a Python str as a SQLite string literal (or NULL)."""
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def seed_sql() -> str:
    """
    Render the seed rows as an idempotent INSERT block.

    Explicit rowids so INSERT OR IGNORE makes a re-run a no-op, matching the
    roi seed: odd rowids are the isdefault fallbacks, even rowids the rows in
    force.  add_dt is the fixed SEED_DT, so re-emitting is byte-identical.
    """
    lines = [
        _SEED_MARK_BEGIN,
        "-- Two rows per parameter: the isdefault fallback (active=0) and the row",
        "-- in force (active=1).  Odd rowids are the fallbacks, even the active ones.",
        "-- Edit config_repo.SEED_PARAMS and re-emit; do not hand-edit this block.",
        "INSERT OR IGNORE INTO config (rowid, active, isdefault, add_dt, role, name,",
        "                              vtype, value, profile, source, remark)",
        "     VALUES",
    ]
    rows = []
    rowid = 0
    for role, name, vtype, value, profile, remark in SEED_PARAMS:
        for isdefault, active in ((1, 0), (0, 1)):
            rowid += 1
            rows.append(
                f"       ({rowid:2d}, {active}, {isdefault}, '{SEED_DT}', "
                f"{_sql_quote(role)}, {_sql_quote(name)}, {_sql_quote(vtype)}, "
                f"{_sql_quote(value)}, {_sql_quote(profile)}, "
                f"{_sql_quote(SOURCE_SEED)}, {_sql_quote(remark)})")
    lines.append(",\n".join(rows) + ";")
    lines.append(_SEED_MARK_END)
    return "\n".join(lines) + "\n"


def _ddl_seed_block() -> Optional[str]:
    """The seed block as it currently stands in db_DDL.sql, or None."""
    try:
        text = DDL_PATH.read_text()
    except OSError:
        return None
    start = text.find(_SEED_MARK_BEGIN)
    end   = text.find(_SEED_MARK_END)
    if start < 0 or end < 0:
        return None
    return text[start:end + len(_SEED_MARK_END)] + "\n"


def emit_sql() -> None:
    """
    Write the generated seed block into db_DDL.sql and config_defaults.sql.

    db_DDL.sql's block is replaced in place between its markers; the block must
    already be present (add the two marker lines once by hand).
    config_defaults.sql is rewritten whole — it is the standalone re-seed
    provisioning applies to bring a unit's config back to commissioned values
    without touching any other table.
    """
    block = seed_sql()

    text  = DDL_PATH.read_text()
    start = text.find(_SEED_MARK_BEGIN)
    end   = text.find(_SEED_MARK_END)
    if start < 0 or end < 0:
        raise SystemExit(f"{DDL_PATH}: seed markers not found — add\n"
                         f"  {_SEED_MARK_BEGIN}\n  {_SEED_MARK_END}\n"
                         f"where the config seed belongs, then re-run.")
    DDL_PATH.write_text(text[:start] + block.rstrip("\n")
                        + text[end + len(_SEED_MARK_END):])
    print(f"updated {DDL_PATH}")

    DEFAULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULTS_PATH.write_text(
        "-- ==========================================================================\n"
        "-- config_defaults.sql — the commissioned values of every tunable parameter\n"
        "-- ==========================================================================\n"
        "--\n"
        "-- GENERATED by `python src/config_repo.py --emit-sql` from that module's\n"
        "-- SEED_PARAMS.  Do not hand-edit.\n"
        "--\n"
        "-- Applying it to an instrument seeds any parameter the config table is\n"
        "-- missing, and is a no-op for those it already has:\n"
        "--\n"
        "--     sqlite3 src/colorimeter.sqlite < provisioning/config_defaults.sql\n"
        "--\n"
        "-- It never touches another table, and never overwrites a value the\n"
        "-- operator set on Menu -> Config.  To drop those overrides as well, use\n"
        "-- the Config screen's Restore, or:\n"
        "--\n"
        "--     UPDATE config SET active = 0 WHERE active = 1 AND source = 'operator';\n"
        "--     UPDATE config SET active = 1 WHERE isdefault = 1;\n"
        "--\n"
        "-- The same block is embedded in src/db_DDL.sql, so a database built from\n"
        "-- that file alone is already seeded.\n"
        "-- ==========================================================================\n"
        "\n" + block)
    print(f"wrote   {DEFAULTS_PATH}")


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import shutil
    import sys
    import tempfile

    logging.basicConfig(level=logging.ERROR)

    if "--emit-sql" in sys.argv:
        emit_sql()
        raise SystemExit(0)

    _failures = 0

    def check(label: str, got, want) -> None:
        global _failures
        ok = got == want
        if not ok:
            _failures += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label}"
              f"{'' if ok else f'   got {got!r}, want {want!r}'}")

    # Everything below runs against a throwaway database built from db_DDL.sql,
    # so the live config table is never written and the checks say something
    # about the schema file rather than about whatever this machine's database
    # happens to hold.
    _tmpdir = tempfile.mkdtemp()
    _tmpdb  = Path(_tmpdir) / "config_selftest.sqlite"
    sqlite3.connect(_tmpdb).executescript(DDL_PATH.read_text())
    db.DBNAME = str(_tmpdb)
    os.environ.pop(SIMULATION_ENV, None)
    load(reload=True)

    print("== db_DDL.sql agrees with SEED_PARAMS ==")
    check("seed block matches", _ddl_seed_block(), seed_sql())

    print("== the shipped values ==")
    check("reagent1", reagent1_secs(), 120)
    check("reagent2", reagent2_secs(), 420)
    check("analysis", analysis_secs(), 1.5)
    check("poll",     main_loop_poll_s(), 0.02)
    check("shutdown", shutdown_timeout_s(), 15.0)
    check("led range", (capture_led_start(), capture_led_end()), (1, 10))
    check("group",    fallback_exposure_group(), "3")
    check("rowid",    default_rowid(), 1)
    check("backlight", backlight_pin(), 13)
    check("archive",  capture_archive_name("x"), "x.h5")
    check("suite id", capture_suite_id(datetime(2026, 7, 19, 13, 43)),
          "20260719_1343")
    check("capture dir", capture_dir(), PROJECT_ROOT / "captures")

    print("== $COLORIMETER_CAPTURE_DIR wins over the table ==")
    os.environ[CAPTURE_DIR_ENV] = "/tmp/from_env"
    check("env dir", capture_dir(), Path("/tmp/from_env"))
    del os.environ[CAPTURE_DIR_ENV]

    print("== save supersedes, keeping the old row as history ==")
    _rows_before = db.read_val("SELECT COUNT(*) FROM config "
                               "WHERE role = 'protocol' AND name = 'reagent1_secs'")
    check("saved", save("protocol", "reagent1_secs", 45) > 0, True)
    check("in force",     reagent1_secs(), 45)
    check("survives reload", (load(reload=True), reagent1_secs())[1], 45)
    check("commissioned unchanged", default("protocol", "reagent1_secs"), 120)
    # Two active rows survive — the operator's and the simulation profile's.
    check("one active base row", db.read_val(
        "SELECT COUNT(*) FROM config WHERE role = 'protocol' "
        "AND name = 'reagent1_secs' AND active = 1 AND profile = ''"), 1)
    check("history kept", db.read_val(
        "SELECT COUNT(*) FROM config WHERE role = 'protocol' "
        "AND name = 'reagent1_secs'"), _rows_before + 1)
    check("listed as an override", ("protocol", "reagent1_secs") in overrides(), True)
    check("neighbour untouched", reagent2_secs(), 420)

    print("== an operator value wins over the simulation profile ==")
    os.environ[SIMULATION_ENV] = "1"
    load(reload=True)
    check("simulation active",   is_simulation(), True)
    check("operator value holds", reagent1_secs(), 45)
    check("unset one uses the profile", reagent2_secs(), 10)
    check("commissioned reads the profile too",
          default("protocol", "reagent2_secs"), 10)

    print("== restore puts the commissioned value back ==")
    check("restored", restore("protocol", "reagent1_secs") > 0, True)
    check("profile applies again", reagent1_secs(), 10)
    check("no overrides left", overrides(), [])
    check("restoring again writes nothing",
          restore("protocol", "reagent1_secs"), 0)
    os.environ.pop(SIMULATION_ENV, None)
    load(reload=True)
    check("shipped value on hardware", reagent1_secs(), 120)

    print("== an unknown parameter is refused, not guessed ==")
    check("save refused",    save("protocol", "nonesuch", 1), 0)
    check("restore refused", restore("protocol", "nonesuch"), 0)
    check("get is None",     get("protocol", "nonesuch"), None)

    print("== a hand-edited typo is ignored, not raised ==")
    db.write_one("UPDATE config SET value = 'abc' WHERE role = 'runtime' "
                 "AND name = 'main_loop_poll_s' AND active = 1")
    load(reload=True)
    check("falls back to the seed value", main_loop_poll_s(), 0.02)
    restore("runtime", "main_loop_poll_s")

    print("== seed_defaults refills a gap and is otherwise a no-op ==")
    check("nothing to do", seed_defaults(), 0)
    db.write_one("DELETE FROM config WHERE role = 'hardware' "
                 "AND name = 'backlight_pin'")
    check("refilled", seed_defaults(), 2)
    check("value back", backlight_pin(), 13)

    print("== an empty table still runs on the built-in seed values ==")
    db.write_one("DELETE FROM config")
    load(reload=True)
    check("reagent1", reagent1_secs(), 120)
    check("group",    fallback_exposure_group(), "3")
    check("refilled wholesale", seed_defaults(), 2 * len(SEED_PARAMS))
    check("reagent1 from the table", reagent1_secs(), 120)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print(f"\n{'FAILURES: ' + str(_failures) if _failures else 'all checks passed'}")
    raise SystemExit(1 if _failures else 0)
