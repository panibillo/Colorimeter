"""
db_manager.py — Lightweight SQLite wrapper.

Provides simple functions to execute SQL queries. Each transaction opens
and closes its own connection. Calling code composes raw SQL and parses
returned values.

Usage:
    results = read_all("SELECT colorname, x, y, w, h FROM colorcard WHERE cardid = ?", (1,))
    row = read_one("SELECT value FROM meterconfig WHERE param=? and = ? and rectid = 2", (1,))
    val = read_val("SELECT COUNT(*) FROM colorcard")
    writevals("INSERT INTO colorcard (cardid, cardname) VALUES (?, ?)",
              (1, "card1"))
"""

import sqlite3
from pathlib import Path
import logging_manager as log

DBNAME = "src/colorimeter.sqlite"

TABLE_DEFINITIONS = """
----------------------------------
-- Color calibration card table
---------------------------------
CREATE TABLE colorcard (
    cardid     INTEGER,         -- Each color card has a unique ID
    cardname   TEXT,            -- Each color card has a unique NAME
    rectid     INTEGER,         -- Identifier of an individual rect patch
    colorname  TEXT,            -- Name of an individual rect patch
    x          INTEGER,
    y          INTEGER,
    w          INTEGER,
    h          INTEGER,
    CONSTRAINT UNIQUE (cardid, rectid),
    CONSTRAINT UNIQUE (cardid, x, y)
);
-----------------------------------
-- Main configuration table
-----------------------------------
CREATE TABLE meterconfig (     -- hardware and software params & constants
    configid   INTEGER,
    param      TEXT,
    value      TEXT,
    CONSTRAINT UNIQUE (configid, param)
);
-----------------------------------
-- Data configuration table
-----------------------------------
CREATE TABLE data (
    timestamp  INTEGER,  -- seconds since epoch
    label      TEXT,     -- user known GUID for the msmt
    param      TEXT,     -- each msmt may have many params
    numval     REAL,
    txtval     TEXT      -- in case there are any text vals
);
"""


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
        log.error(f"read_all() failed: {e}\n  SQL: {sql}\n  vals: {vals}")
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
        log.error(f"write_one() failed: {e}\n  SQL: {sql}\n  vals: {vals}")
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
        log.error(f"write_many() failed: {e}\n  SQL: {sql}\n"
                  f"  vals_list length: {len(vals_list)}")
        raise
    finally:
        if con:
            con.close()
