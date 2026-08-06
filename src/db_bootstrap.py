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
It sits alongside db_manager, which owns the path for the same reason.

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
from dataclasses import dataclass
from pathlib import Path

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


@dataclass
class Result:
    """
    What `ensure` did.

    Attributes:
        state:  'created' if the database had no config table before this call,
                'current' if it was already at SCHEMA_VERSION.
        added:  config rows the seed file inserted this run.  0 on every run
                after the first — that is the idempotency check.
        params: distinct parameters now in force (one active row each).
    """
    state:  str
    added:  int
    params: int


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
    dead = con.execute(
        "SELECT role, name FROM config "
        " GROUP BY role, name "
        "HAVING SUM(active) = 0 "
        " ORDER BY role, name"
    ).fetchall()
    if dead:
        named = ", ".join(f"{r}.{n}" for r, n in dead)
        raise DatabaseError(
            f"{len(dead)} config parameter(s) have no active row: {named}. "
            "Set one row per parameter to active = 1, or run "
            "provisioning/config_defaults.sql after clearing them."
        )

    orphans = con.execute(
        "SELECT role, name FROM config "
        " GROUP BY role, name "
        "HAVING SUM(isdefault) = 0 "
        " ORDER BY role, name"
    ).fetchall()
    if orphans:
        named = ", ".join(f"{r}.{n}" for r, n in orphans)
        log.warning(f"db_bootstrap: {len(orphans)} config parameter(s) have no "
                    f"commissioned default, so Restore cannot reach them: "
                    f"{named}")

    return con.execute(
        "SELECT COUNT(*) FROM config WHERE active = 1"
    ).fetchone()[0]


def ensure(path=None) -> Result:
    """
    Create, repair and verify the database, then stamp its schema version.

    Idempotent: the first call on a new file builds and seeds it, and every
    call after that inserts nothing.  Existing values, and the active and
    isdefault flags, are never touched.

    Args:
        path: The database file.  Defaults to db_manager.DBNAME, which is
              where the application keeps it.

    Returns:
        A Result describing what changed.

    Raises:
        DatabaseError: The database predates the 2026-08 migration, a schema
                       file is missing or failed, or a parameter is left with
                       no active row.

    Example:
        >>> import db_bootstrap
        >>> r = db_bootstrap.ensure()
        >>> r.added
        0
    """
    path = Path(path) if path else Path(db.DBNAME)

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

        _run_script(con, DDL_PATH)
        _run_script(con, DEFAULTS_PATH)

        after  = con.execute("SELECT COUNT(*) FROM config").fetchone()[0]
        params = _verify(con)

        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        con.commit()

    except sqlite3.Error as e:
        raise DatabaseError(f"{path}: {e}") from e
    finally:
        if con:
            con.close()

    result = Result(state=state, added=after - before, params=params)
    if result.state == "created":
        log.info(f"db_bootstrap: created {path} with {result.params} "
                 f"parameters")
    elif result.added:
        log.info(f"db_bootstrap: restored {result.added} missing config "
                 f"row(s) in {path}")
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

    _tmpdir = Path(tempfile.mkdtemp())

    print("== a new file is built and seeded ==")
    fresh = _tmpdir / "sub" / "fresh.sqlite"   # parent does not exist yet
    r1 = ensure(fresh)
    check("state", r1.state, "created")
    check("parent dir made", fresh.parent.is_dir(), True)
    check("params seeded", r1.params > 20, True)
    _con = sqlite3.connect(fresh)
    check("version stamped",
          _con.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
    check("one active row each",
          _con.execute("SELECT COUNT(*) FROM config WHERE active = 1"
                       ).fetchone()[0], r1.params)
    check("roi seeded too",
          _con.execute("SELECT COUNT(*) FROM roi").fetchone()[0], 10)
    _con.close()

    print("== running again changes nothing ==")
    r2 = ensure(fresh)
    check("state", r2.state, "current")
    check("rows added", r2.added, 0)
    check("params unchanged", r2.params, r1.params)

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
    _con.close()

    print("== a deactivated parameter is healed, not reported ==")
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
    _con.close()

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
    legacy = _tmpdir / "legacy.sqlite"
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

    shutil.rmtree(_tmpdir, ignore_errors=True)

    print(f"\n{'PASSED' if not _failures else f'FAILURES: {_failures}'}")
    raise SystemExit(1 if _failures else 0)
