"""
db_bootstrap.py — Bring the database up to a runnable state before anything
reads it.

Called once at launch, from main.py and run_simulation.py, before the Arbiter
is constructed.  It executes the two files that define the database:

    src/db_DDL.sql                   structure, all CREATE ... IF NOT EXISTS
    provisioning/config_defaults.sql every seeded value, all INSERT OR IGNORE

Both are idempotent, so this is safe to run at every launch and repairs a
database that has lost a table or a parameter.  It never overwrites a value
that is already there, and never clears an `active` or `isdefault` flag —
whatever the operator has set stays set.

Why this exists: before it, nothing in the application ever created a table.
A missing table degraded silently to hardcoded fallback values in Python, so
an instrument could run for weeks on values that were not in its database and
that nobody could see or edit.  Those fallbacks are gone; the database is the
only source of a setting, which only works if the database is guaranteed to
have one.

Why it is not part of config_repo: config_repo reads the settings *out of* the
database, so it cannot be the thing that decides whether the database exists.
It sits alongside db_manager, which owns the path for the same reason.  It does
call back into config_repo for one write — the software version stamp — once
the tables are known to be there; see `ensure`.

Schema versions:
    0   pre-2026-08: separate device/users/testkit/testlot/defaults tables
    2   those folded into `config`, which gained `editable` and the unique
        constraints enforcing one active and one default row per parameter

A version 0 database is refused rather than upgraded in place — the migration
rewrites two tables and drops five, which is not something to do silently at
launch.  Run provisioning/migrate_v2.sql once, by hand, with a backup taken.

Usage:
    import db_bootstrap
    db_bootstrap.ensure()          # the live database
    db_bootstrap.ensure(path)      # a specific file, for tests
"""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config_repo as config
import db_manager as db
import logging_manager as log

PROJECT_ROOT  = Path(__file__).resolve().parent.parent
DDL_PATH      = PROJECT_ROOT / "src" / "db_DDL.sql"
DEFAULTS_PATH = PROJECT_ROOT / "provisioning" / "config_defaults.sql"

# Bumped when a change needs a migration.  Stamped into PRAGMA user_version so
# external tools (provisioning/deploy/commissioning_status.sh) can report it,
# but `ensure` does not trust it to decide what to do — a brand new database
# and a version 0 database both read as 0.  The column check below is what
# actually tells them apart.
SCHEMA_VERSION = 2

MIGRATION_HINT = (
    "run:  cp <db> <db>.v1.bak"
    "  &&  sqlite3 -bail <db> < provisioning/migrate_v2.sql"
)


class DatabaseError(RuntimeError):
    """The database cannot be brought to a state the application can run on."""


@dataclass(frozen=True)
class Restored:
    """
    One parameter that had to be brought back from its commissioned default.

    Two ways a parameter can arrive here, both healed by the seed file before
    anything reads the table:

        reactivated  its rows were all inactive, and the closing UPDATE in
                     config_defaults.sql put the isdefault row back in force.
                     Completely invisible until now.
        reseeded     it had no rows at all, and the seed INSERTed it.  This is
                     what Result.added counts, as a number with no names.

    Attributes:
        rowid: The config row now in force — what to look at in the editor.
        role:  The section the parameter sits under, e.g. 'protocol'.
        name:  The parameter, e.g. 'reagent1_secs'.
        value: Its value, as stored text.
        how:   'reactivated' or 'reseeded'.
    """
    rowid: int
    role:  str
    name:  str
    value: str
    how:   str

    def message(self) -> str:
        """The one line the splash screen shows, with an OK button."""
        return (f"Loading {self.role}.{self.name} = {self.value} "
                f"(rowid {self.rowid})")


REACTIVATED = "reactivated"
RESEEDED    = "reseeded"


@dataclass
class Result:
    """
    What `ensure` did.

    Attributes:
        state:    'created' if the database had no config table before this
                  call, 'current' if it was already at SCHEMA_VERSION.
        added:    config rows the seed file inserted this run.  0 on every run
                  after the first — that is the idempotency check.
        params:   distinct parameters now in force (one active row each).
        restored: a Restored record per parameter that came back from its
                  default this run, for the splash screen to report.  Always
                  empty on a freshly created database, where every parameter
                  is new and naming them all would say nothing.
    """
    state:    str
    added:    int
    params:   int
    restored: List[Restored] = field(default_factory=list)


def _schema_state(con: sqlite3.Connection) -> str:
    """
    Classify the database this connection is open on.

    Decided by looking for the `editable` column rather than by reading
    PRAGMA user_version, because an empty file and a version 0 database both
    report version 0, and because a hand-run of db_DDL.sql could stamp a
    version onto a database whose tables it left untouched (CREATE TABLE IF
    NOT EXISTS is a no-op on the old shape).

    Args:
        con: An open connection.

    Returns:
        'created' if there is no config table yet, 'current' if it has the
        v2 shape, 'legacy' if it has the pre-migration shape.
    """
    tables = con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'config'"
    ).fetchall()
    if not tables:
        return "created"

    columns = {row[1] for row in con.execute("PRAGMA table_info(config)")}
    return "current" if "editable" in columns else "legacy"


def _run_script(con: sqlite3.Connection, path: Path) -> None:
    """
    Execute one of the two schema files.

    Args:
        con:  An open connection.
        path: The .sql file to run.

    Raises:
        DatabaseError: The file is missing, or a statement in it failed.
    """
    try:
        script = path.read_text()
    except OSError as e:
        raise DatabaseError(f"cannot read {path}: {e}") from e

    try:
        con.executescript(script)
        con.commit()
    except sqlite3.Error as e:
        raise DatabaseError(f"{path.name} failed: {e}") from e


def _params_having(clause: str,
                   con: Optional[sqlite3.Connection] = None
                   ) -> List[Tuple[str, str]]:
    """
    Every parameter whose rows satisfy an aggregate condition.

    Args:
        clause: The HAVING expression, e.g. 'SUM(active) = 0'.
        con:    An open connection, or None to go through db_manager.  Callers
                outside a bootstrap transaction pass None.

    Returns:
        (role, name) pairs, ordered.
    """
    sql = ("SELECT role, name FROM config "
           " GROUP BY role, name "
           f"HAVING {clause} "
           " ORDER BY role, name")
    if con is not None:
        return con.execute(sql).fetchall()
    return [tuple(row) for row in db.read_all(sql)]


def dead_params(con: Optional[sqlite3.Connection] = None
                ) -> List[Tuple[str, str]]:
    """
    Parameters with no active row — every read of one raises ConfigMissing.

    Only reachable by hand-editing the table: the seed file's closing UPDATE
    reactivates any parameter that still has its commissioned default, so
    anything left here has no default to fall back on either.

    Example:
        >>> dead_params()                              # doctest: +SKIP
        []
    """
    return _params_having("SUM(active) = 0", con)


def orphan_params(con: Optional[sqlite3.Connection] = None
                  ) -> List[Tuple[str, str]]:
    """
    Parameters with no isdefault row — the value works, but Restore cannot
    reach them.

    Example:
        >>> orphan_params()                            # doctest: +SKIP
        []
    """
    return _params_having("SUM(isdefault) = 0", con)


def _add_samples_columns(con: sqlite3.Connection) -> None:
    """
    Add any missing columns to the samples table (idempotent migration for
    existing databases). SQLite 3.45.1 does not support ALTER TABLE ... ADD
    COLUMN IF NOT EXISTS, so check which columns exist first.
    """
    existing = {row[1] for row in con.execute("PRAGMA table_info(samples)")}
    columns_to_add = [
        ("reagent1_g", "REAL"),
        ("reagent2_g", "REAL"),
        ("reagent1_stir_end", "INTEGER"),
        ("reagent2_stir_end", "INTEGER"),
    ]
    for name, type_ in columns_to_add:
        if name not in existing:
            con.execute(f"ALTER TABLE samples ADD COLUMN {name} {type_}")
    con.commit()


def _verify(con: sqlite3.Connection) -> int:
    """
    Check that every parameter in the table is usable, and count them.

    Two things can be wrong even after the seed has run, both only reachable
    by hand-editing the table:

      * a parameter whose rows are all inactive — every read of it would fail,
        so this is fatal;
      * a parameter with no isdefault row — the value works, but Restore has
        nothing to go back to, so this is a warning.

    There is deliberately no list of required parameter names here.  The seed
    file inserts whatever it names, so a name it knows about cannot be
    missing; a list in this module would be a second copy of the values the
    database is supposed to be the only home for.

    Args:
        con: An open connection.

    Returns:
        The number of parameters with exactly one active row.

    Raises:
        DatabaseError: One or more parameters have no active row.
    """
    dead = dead_params(con)
    if dead:
        named = ", ".join(f"{r}.{n}" for r, n in dead)
        raise DatabaseError(
            f"{len(dead)} config parameter(s) have no active row: {named}. "
            "Set one row per parameter to active = 1, or run "
            "provisioning/config_defaults.sql after clearing them."
        )

    orphans = orphan_params(con)
    if orphans:
        named = ", ".join(f"{r}.{n}" for r, n in orphans)
        log.warning(f"db_bootstrap: {len(orphans)} config parameter(s) have no "
                    f"commissioned default, so Restore cannot reach them: "
                    f"{named}")

    return con.execute(
        "SELECT COUNT(*) FROM config WHERE active = 1"
    ).fetchone()[0]


def _active_counts(con: sqlite3.Connection) -> Dict[Tuple[str, str], int]:
    """How many rows are in force per parameter, keyed by (role, name)."""
    return {(role, name): n for role, name, n in con.execute(
        "SELECT role, name, SUM(active) FROM config GROUP BY role, name")}


def _restored(con: sqlite3.Connection,
              was_active: Dict[Tuple[str, str], int]) -> List[Restored]:
    """
    Name the parameters the seed file just brought back.

    Compares what is in force now against what was in force before the seed
    ran.  A parameter absent from `was_active` had no rows at all and was
    reseeded; one that was there with a count of zero had rows but none
    active, and the seed file's closing UPDATE reactivated its default.

    Args:
        con:        An open connection, after the seed file has run.
        was_active: The _active_counts() taken before it ran.

    Returns:
        A Restored per healed parameter, ordered by role and name.
    """
    out: List[Restored] = []
    for rowid, role, name, value in con.execute(
            "SELECT rowid, role, name, value FROM config "
            " WHERE active = 1 ORDER BY role, name"):
        seen = was_active.get((role, name))
        if seen is None:
            out.append(Restored(rowid, role, name, value, RESEEDED))
        elif not seen:
            out.append(Restored(rowid, role, name, value, REACTIVATED))
    return out


def ensure(path=None) -> Result:
    """
    Create, repair and verify the database, then stamp its schema version.

    Idempotent: the first call on a new file builds and seeds it, and every
    call after that inserts nothing.  Existing values, and the active and
    isdefault flags, are never touched.  The one write that is not from the
    seed file is the software version stamp, which only writes on the first
    launch after src/version.py changes.

    Args:
        path: The database file.  Defaults to db_manager.DBNAME, which is
              where the application keeps it.  A different path must be
              accompanied by db_manager.DBNAME being reassigned to it, because
              the version stamp goes through config_repo, which reads the path
              from db_manager; a mismatch is refused rather than allowed to
              write to the wrong database.

    Returns:
        A Result describing what changed.

    Raises:
        DatabaseError: The database predates the 2026-08 migration, a schema
                       file is missing or failed, a parameter is left with no
                       active row, or `path` disagrees with db_manager.DBNAME.

    Example:
        >>> import db_bootstrap
        >>> r = db_bootstrap.ensure()
        >>> r.added
        0
    """
    path = Path(path) if path else Path(db.DBNAME)

    # config_repo, which stamps the version below, reaches the database through
    # db_manager rather than through the connection opened here.  Catch the
    # disagreement now: silently stamping a version into a different file than
    # the one being built is the kind of bug that only shows up much later.
    if path.resolve() != Path(db.DBNAME).resolve():
        raise DatabaseError(
            f"ensure({path}) does not match db_manager.DBNAME "
            f"({db.DBNAME}). Set db_manager.DBNAME (or $COLORIMETER_DB) to "
            f"the same file first — config_repo writes through it.")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise DatabaseError(f"cannot create {path.parent}: {e}") from e

    con = None
    try:
        con = sqlite3.connect(path)

        state = _schema_state(con)
        if state == "legacy":
            raise DatabaseError(
                f"{path} predates the 2026-08 schema migration (its config "
                f"table has no `editable` column). It must be migrated once, "
                f"by hand, with a backup taken first — {MIGRATION_HINT}"
            )

        before = 0 if state == "created" else con.execute(
            "SELECT COUNT(*) FROM config").fetchone()[0]
        # What was in force before the seed file ran, so the rows it heals can
        # be named afterwards rather than only counted.  Skipped on a new
        # database, where nothing was in force and nothing was restored.
        was_active = ({} if state == "created"
                      else _active_counts(con))

        _run_script(con, DDL_PATH)
        _add_samples_columns(con)
        _run_script(con, DEFAULTS_PATH)

        after  = con.execute("SELECT COUNT(*) FROM config").fetchone()[0]
        restored = _restored(con, was_active) if state != "created" else []

        # Counted after `after`, so Result.added keeps meaning "seed rows the
        # file put back", and before _verify, so software_ver always has an
        # active row by the time the fatal check for one runs.  config_repo
        # opens its own connection; `con` holds no write transaction here, so
        # the two do not contend.
        config.stamp_version()

        params = _verify(con)

        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        con.commit()

    except sqlite3.Error as e:
        raise DatabaseError(f"{path}: {e}") from e
    finally:
        if con:
            con.close()

    result = Result(state=state, added=after - before, params=params,
                    restored=restored)
    if result.state == "created":
        log.info(f"db_bootstrap: created {path} with {result.params} "
                 f"parameters")
    elif result.added:
        log.info(f"db_bootstrap: restored {result.added} missing config "
                 f"row(s) in {path}")
    # Logged as well as returned: the splash screen shows these with an OK
    # button, but an unattended instrument nobody is watching still records
    # that it came up on a default rather than on a commissioned value.
    for r in result.restored:
        log.warning(f"db_bootstrap: {r.how} {r.role}.{r.name} = {r.value} "
                    f"(rowid {r.rowid})")
    return result


# ── Self-test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import shutil
    import tempfile

    _failures = 0

    def check(label: str, got, want) -> None:
        global _failures
        ok = got == want
        if not ok:
            _failures += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label}"
              f"{'' if ok else f'   got {got!r}, want {want!r}'}")

    import version

    _tmpdir = Path(tempfile.mkdtemp())

    def use(p: Path) -> Path:
        """Point db_manager at a scratch file, as the unit tests do."""
        db.DBNAME = str(p)
        return p

    print("== a new file is built and seeded ==")
    fresh = use(_tmpdir / "sub" / "fresh.sqlite")  # parent does not exist yet
    r1 = ensure(fresh)
    check("state", r1.state, "created")
    check("parent dir made", fresh.parent.is_dir(), True)
    check("params seeded", r1.params > 20, True)
    _con = sqlite3.connect(fresh)
    check("schema version stamped",
          _con.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
    check("one active row each",
          _con.execute("SELECT COUNT(*) FROM config WHERE active = 1"
                       ).fetchone()[0], r1.params)
    check("roi seeded too",
          _con.execute("SELECT COUNT(*) FROM roi").fetchone()[0], 10)
    _con.close()
    # Every parameter is new here, but none of them was "loaded from a
    # default" in the sense worth telling an operator about — naming all 28
    # would be one OK press each on a first boot.
    check("a new database restores nothing", r1.restored, [])

    print("== the running software version is stamped in ==")
    _sw = ("SELECT value, editable, source, isdefault FROM config "
           "WHERE role='device' AND name='software_ver' AND active=1")
    _con = sqlite3.connect(fresh)
    check("stamped from the source tree",
          _con.execute(_sw).fetchone(),
          (version.__version__, 0, "code", 1))   # first stamp claims isdefault
    check("only one row for it",
          _con.execute("SELECT COUNT(*) FROM config WHERE role='device' "
                       "AND name='software_ver'").fetchone()[0], 1)
    _con.close()

    print("== running again changes nothing ==")
    r2 = ensure(fresh)
    check("state", r2.state, "current")
    check("rows added", r2.added, 0)
    check("params unchanged", r2.params, r1.params)

    print("== a version bump supersedes, and keeps the commissioned row ==")
    _was = version.__version__
    version.__version__ = "9.9.9"
    ensure(fresh)
    _con = sqlite3.connect(fresh)
    check("new version in force", _con.execute(_sw).fetchone(),
          ("9.9.9", 0, "code", 0))
    check("old version kept as history",
          _con.execute("SELECT value, active, isdefault FROM config "
                       "WHERE role='device' AND name='software_ver' "
                       "AND value = ?", (_was,)).fetchone(),
          (_was, 0, 1))          # commissioned-at row: superseded, still default
    _con.close()
    version.__version__ = _was
    ensure(fresh)                                  # and back, reusing the row
    _con = sqlite3.connect(fresh)
    check("returning to a known version reuses its row",
          _con.execute("SELECT COUNT(*) FROM config WHERE role='device' "
                       "AND name='software_ver'").fetchone()[0], 2)
    _con.close()

    print("== restore_all leaves the version stamped, not reverted ==")
    # software_ver is the one parameter with no source='seed' row to raise, so
    # without restore_all's restamp it would be left with nothing active.
    import config_repo as _cfg
    _cfg.load(reload=True)
    _cfg.restore_all()
    _con = sqlite3.connect(fresh)
    check("still exactly one active row",
          _con.execute("SELECT COUNT(*) FROM config WHERE role='device' "
                       "AND name='software_ver' AND active=1").fetchone()[0], 1)
    check("at the running version", _con.execute(_sw).fetchone()[0],
          version.__version__)
    _con.close()

    print("== an operator value survives, a deleted one comes back ==")
    _con = sqlite3.connect(fresh)
    _con.executescript("""
        UPDATE config SET active = 0
         WHERE role='protocol' AND name='reagent1_secs';
        INSERT INTO config (active, isdefault, editable, add_dt, role, name,
                            vtype, value, source)
             VALUES (1, 0, 1, '2026-08-05', 'protocol', 'reagent1_secs',
                     'int', '333', 'operator');
        DELETE FROM config WHERE role='runtime' AND name='shutdown_timeout_s';
    """)
    _con.commit()
    _con.close()
    r3 = ensure(fresh)
    _con = sqlite3.connect(fresh)
    check("operator value kept",
          _con.execute("SELECT value FROM config WHERE role='protocol' "
                       "AND name='reagent1_secs' AND active=1"
                       ).fetchone()[0], "333")
    check("deleted param restored",
          _con.execute("SELECT value FROM config WHERE role='runtime' "
                       "AND name='shutdown_timeout_s' AND active=1"
                       ).fetchone()[0], "15.0")
    check("only the deleted one was added", r3.added, 1)
    check("and it is named, not just counted",
          [(r.role, r.name, r.value, r.how) for r in r3.restored],
          [("runtime", "shutdown_timeout_s", "15.0", RESEEDED)])
    check("every insert is accounted for",
          len([r for r in r3.restored if r.how == RESEEDED]), r3.added)
    check("the message names the row",
          r3.restored[0].message().startswith(
              "Loading runtime.shutdown_timeout_s = 15.0 (rowid "), True)
    _con.close()

    print("== a deactivated parameter is healed, and named ==")
    # Its commissioned row is still flagged isdefault, so the seed file's
    # closing UPDATE puts it back in force without inserting anything.
    _con = sqlite3.connect(fresh)
    _con.execute("UPDATE config SET active = 0 WHERE role='capture' "
                 "AND name='extension'")
    _con.commit()
    _con.close()
    r4 = ensure(fresh)
    _con = sqlite3.connect(fresh)
    check("back in force",
          _con.execute("SELECT value FROM config WHERE role='capture' "
                       "AND name='extension' AND active=1").fetchone()[0],
          ".h5")
    check("nothing inserted to do it", r4.added, 0)
    check("reported as reactivated, not reseeded",
          [(r.role, r.name, r.how) for r in r4.restored],
          [("capture", "extension", REACTIVATED)])
    check("no insert, so it does not count as added", r4.added, 0)
    _con.close()

    print("== a healthy relaunch reports nothing ==")
    r5 = ensure(fresh)
    check("no rows added",    r5.added,    0)
    check("nothing restored", r5.restored, [])

    print("== a parameter with no active and no default row is fatal ==")
    # Clearing both flags is past what the seed file can repair: it cannot
    # re-insert the value (un_config_value already holds it) and it only
    # reactivates rows still flagged isdefault.
    _con = sqlite3.connect(fresh)
    _con.execute("UPDATE config SET active = 0, isdefault = 0 "
                 "WHERE role='capture' AND name='extension'")
    _con.commit()
    _con.close()
    try:
        ensure(fresh)
        check("raised", False, True)
    except DatabaseError as e:
        check("raised", "capture.extension" in str(e), True)

    print("== a pre-migration database is refused, not upgraded ==")
    legacy = use(_tmpdir / "legacy.sqlite")
    _con = sqlite3.connect(legacy)
    _con.executescript(
        "CREATE TABLE config (rowid INTEGER PRIMARY KEY NOT NULL, "
        "active INTEGER, isdefault INTEGER, add_dt TEXT, role TEXT, "
        "name TEXT, vtype TEXT, value TEXT, profile TEXT, source TEXT, "  # v1 shape
        "remark TEXT);"
        "INSERT INTO config (active, role, name, vtype, value) "
        "     VALUES (1, 'protocol', 'reagent1_secs', 'int', '120');")
    _con.commit()
    _con.close()
    try:
        ensure(legacy)
        check("raised", False, True)
    except DatabaseError as e:
        check("raised", "migrat" in str(e).lower(), True)
    _con = sqlite3.connect(legacy)
    check("left untouched",
          {r[1] for r in _con.execute("PRAGMA table_info(config)")}
          & {"editable"}, set())
    check("version not stamped",
          _con.execute("PRAGMA user_version").fetchone()[0], 0)
    _con.close()

    print("== a path db_manager does not know about is refused ==")
    # config_repo writes through db_manager, so ensure() must not build one
    # file while the version stamp goes to another.
    try:
        ensure(_tmpdir / "elsewhere.sqlite")
        check("raised", False, True)
    except DatabaseError as e:
        check("raised", "DBNAME" in str(e), True)
    check("not created", (_tmpdir / "elsewhere.sqlite").exists(), False)

    shutil.rmtree(_tmpdir, ignore_errors=True)

    print(f"\n{'PASSED' if not _failures else f'FAILURES: {_failures}'}")
    raise SystemExit(1 if _failures else 0)
