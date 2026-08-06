"""
db_manager.py — Lightweight SQLite wrapper.

Provides simple functions to execute SQL queries. Each transaction opens
and closes its own connection. Calling code composes raw SQL and parses
returned values.

The schema lives in src/db_DDL.sql — that file is authoritative and builds a
complete, seeded, runnable database on its own.  Do not restate it here.

This module also settles where the database *is* (DBNAME / db_path), because
config_repo reads the instrument's settings out of the database and so cannot
be the one to say where it lives.  Nothing here imports config_repo.

Usage:
    results = read_all("SELECT name, x, y, w, h FROM roi WHERE active = 1")
    row     = read_one("SELECT x, y, w, h FROM roi WHERE name = ? AND active = 1",
                       ("sample",))
    val     = read_val("SELECT COUNT(*) FROM samples")
    write_one("INSERT INTO colorcard (cardid, cardname) VALUES (?, ?)",
              (1, "card1"))
"""

import os
import sqlite3
from pathlib import Path

import logging_manager as log

# The database location is settled here rather than in config_repo, because
# config_repo reads its parameters *out of* this database — it cannot be asked
# where the database is.  This is the one setting that must live outside the
# config table.
#
# The path is resolved against the project root (the directory holding src/ and
# config/) so it is independent of the working directory — the app launches
# from src/, tools from the repo root.  $COLORIMETER_DB overrides it for one
# process; tests may also reassign DBNAME directly to a scratch copy.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH_ENV  = "COLORIMETER_DB"
DEFAULT_DB   = PROJECT_ROOT / "src" / "colorimeter.sqlite"


def db_path() -> Path:
    """
    Return the SQLite database path.

    $COLORIMETER_DB wins if set; otherwise DEFAULT_DB.  A relative override is
    resolved against the project root.

    Returns:
        The database path, with ~ expanded.
    """
    configured = Path(os.environ.get(DB_PATH_ENV) or DEFAULT_DB).expanduser()
    if configured.is_absolute():
        return configured
    return PROJECT_ROOT / configured


DBNAME = str(db_path())
print (f"Colorimeter db_manager.py using db: {DBNAME}")


def read_all(sql: str, vals: tuple = None) -> list:
    """
    Execute SELECT query and return all rows.

    Args:
        sql: SQL query string. Use ? for placeholders.
        vals: Tuple of parameter values for placeholders.

    Returns:
        list of tuples. Empty list if no rows match.

    Raises:
        sqlite3.Error: On database error (logged).
    """
    con = None
    try:
        con = sqlite3.connect(DBNAME)
        cursor = con.cursor()
        if vals:
            cursor.execute(sql, vals)
        else:
            cursor.execute(sql)
        return cursor.fetchall()
    except sqlite3.Error as e:
        log.error(f"read_all() failed: {e}\n  SQL: {sql}\n  vals: {vals}\n db: {DBNAME}")
        raise
    finally:
        if con:
            con.close()


def read_one(sql: str, vals: tuple = None) -> tuple:
    """
    Execute SELECT query and return first row only.

    Args:
        sql: SQL query string. Use ? for placeholders.
        vals: Tuple of parameter values for placeholders.

    Returns:
        Tuple of values for first row, or None if no rows match.

    Raises:
        sqlite3.Error: On database error (logged).
    """
    try:
        results = read_all(sql, vals)
        return results[0] if results else None
    except sqlite3.Error:
        raise


def read_val(sql: str, vals: tuple = None):
    """
    Execute SELECT query and return single scalar value.

    Args:
        sql: SQL query string. Use ? for placeholders.
        vals: Tuple of parameter values for placeholders.

    Returns:
        Single value (int, float, str, etc.), or None if no rows match.

    Raises:
        sqlite3.Error: On database error (logged).
    """
    try:
        row = read_one(sql, vals)
        return row[0] if row else None
    except sqlite3.Error:
        raise


def write_one(sql: str, vals: tuple = None) -> int:
    """
    Execute INSERT/UPDATE/DELETE query (single row).

    Args:
        sql: SQL query string. Use ? for placeholders.
        vals: Tuple of parameter values for placeholders.

    Returns:
        Number of rows affected. 0 if no rows matched (e.g., UPDATE with no
        matching rows).

    Raises:
        sqlite3.Error: On database error (logged and rolled back).
    """
    con = None
    try:
        con = sqlite3.connect(DBNAME)
        cursor = con.cursor()
        if vals:
            cursor.execute(sql, vals)
        else:
            cursor.execute(sql)
        con.commit()
        return cursor.rowcount
    except sqlite3.Error as e:
        if con:
            con.rollback()
        log.error(f"write_one() failed: {e}\n  SQL: {sql}\n  vals: {vals}\n db: {DBNAME}")
        raise
    finally:
        if con:
            con.close()


def write_many(sql: str, vals_list: list) -> int:
    """
    Execute INSERT/UPDATE/DELETE query with multiple parameter sets.

    Args:
        sql: SQL query string. Use ? for placeholders.
        vals_list: List of tuples, each tuple is parameter values for one
                   execution.

    Returns:
        Total number of rows affected across all executions.

    Raises:
        sqlite3.Error: On database error (logged and rolled back).
    """
    con = None
    try:
        con = sqlite3.connect(DBNAME)
        cursor = con.cursor()
        cursor.executemany(sql, vals_list)
        con.commit()
        return cursor.rowcount
    except sqlite3.Error as e:
        if con:
            con.rollback()
        log.error(f"write_many() failed: {e}\n  SQL: {sql}\n db: {DBNAME}\n"
                  f"  vals_list length: {len(vals_list)}")
        raise
    finally:
        if con:
            con.close()

def write_tx(statements: list) -> int:
    """
    Execute several statements in one transaction.

    For a write that is only correct as a whole.  config_repo's save is the
    reason this exists: it deactivates the row in force, inserts the new value
    and activates it, and the `config` table's partial unique indexes mean the
    intermediate states are only legal in that order.  Run as three separate
    calls, a crash in the middle would leave a parameter with no active row.

    Args:
        statements: List of (sql, vals) pairs, executed in order.  vals may be
                    None for a statement with no placeholders.

    Returns:
        Number of rows affected by the last statement.

    Raises:
        sqlite3.Error: On database error (logged, and the whole transaction is
                       rolled back).

    Example:
        >>> write_tx([("UPDATE config SET active = 0 WHERE rowid = ?", (7,)),
        ...           ("UPDATE config SET active = 1 WHERE rowid = ?", (9,))])
        1
    """
    con = None
    try:
        con = sqlite3.connect(DBNAME)
        cursor = con.cursor()
        for sql, vals in statements:
            if vals:
                cursor.execute(sql, vals)
            else:
                cursor.execute(sql)
        con.commit()
        return cursor.rowcount
    except sqlite3.Error as e:
        if con:
            con.rollback()
        log.error(f"write_tx() failed: {e}\n  SQL: {sql}\n  vals: {vals}\n"
                  f" db: {DBNAME}")
        raise
    finally:
        if con:
            con.close()


if __name__ == '__main__':
    print (DBNAME)