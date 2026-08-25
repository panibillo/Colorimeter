#!/usr/bin/env python3
"""
fetch_samplerows_from_pi.py — copy rows of any table FROM the Pi's database
INTO the local one.
------------------------------------------------------------------------------
Usage:
    ./fetch_samplerows_from_pi.py C1015 C1016 ...      # samples, by samp_no
    ./fetch_samplerows_from_pi.py --table roi          # roi, whole table
    ./fetch_samplerows_from_pi.py --list-tables
    ./fetch_samplerows_from_pi.py --table roi --dry-run
    ./fetch_samplerows_from_pi.py --remote rpizero3 C1015

Pulls a throwaway copy of the Pi's colorimeter.sqlite (scp, the same remote
path pulldb.sh/pushdb.sh use) and copies rows out of it into
src/colorimeter.sqlite here.  Two modes, because the tables are two shapes:

  **upsert** — match rows by a natural key and leave every other local row
    alone.  `samples` is keyed by `samp_no` (UNIQUE COLLATE NOCASE, the rule
    sample_repo.py uses).  This is the mode for a table whose rows are
    independent records.

  **mirror** — replace the whole local table with the remote's, rowids and
    all, in one transaction.  This is the mode for the append-only,
    `active`-versioned tables: `roi`, `exposure`, `layermodels`,
    `colorcard`.  A row-by-row upsert on those leaves stale `active = 1`
    rows behind, so the local table would end up with two rows in force and
    `roi.load_active()` would raise on the duplicate.  Mirroring is also
    what keeps `layermodels.roisamp/roi1/roi2/roibound` pointing at the same
    `roi.rowid` values they meant on the instrument.

Each table has a default mode (see `TABLES`); `--mode` overrides it.

Only columns present in both databases' copy of the table are copied, so a
local db on an older schema doesn't break — it just doesn't receive the
newer columns, and says so.

**The local db is backed up first**, in place, as
`colorimeter.sqlite.bak-<date>` — the convention pushdb.sh already uses.  An
existing backup for today is kept rather than overwritten, so the first
state of the day survives however many times this is run.

Two cautions:

  * `--table config --mode mirror` would overwrite desktop-local settings
    with the instrument's.  It is allowed, and the backup is the safety net,
    but it is not something to do casually.
  * A table that exists only locally — `sample_roi`, written by
    desktop/padanalysis/analysis_1.py — has no remote counterpart and this
    tool will refuse it rather than empty it.

What this does NOT fix: a capture archive was cropped against whatever
`bound` was in force when it was taken, and that rect is recorded in the
archive's own `/meta`.  Syncing `roi` makes the *current* rects agree with
the instrument; it cannot tell you what a past capture's bound was.  Read
geometry for a past archive from the archive.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_DB = PROJECT_ROOT / "src" / "colorimeter.sqlite"
REMOTE = "rpizero3"
REMOTE_DB = "~/pi/Colorimeter/src/colorimeter.sqlite"
TEMPDIR = "/home/bill/R"
SCP_TIMEOUT = 60  # seconds — a bare db file, not the captures/ tree

#: {table: (default mode, natural key or None)}.  A table not listed here
#: still works; it defaults to mirroring, which is the safe assumption for
#: anything whose rows are versioned rather than independent.
TABLES = {
    "samples": ("upsert", "samp_no"),
    "roi": ("mirror", None),
    "exposure": ("mirror", None),
    "layermodels": ("mirror", None),
    "colorcard": ("mirror", None),
    "config": ("mirror", None),
    "data": ("mirror", None),
}
DEFAULT_TABLE = "samples"



def fetch_remote_copy(remote: str, remote_db: str, dest: Path) -> None:
    """
    scp the Pi's database into a local scratch file.

    Args:
        remote: ssh host alias.
        remote_db: Path to the database on the remote.
        dest: Where to put the copy.

    Raises:
        subprocess.CalledProcessError: scp failed.
        subprocess.TimeoutExpired: scp took longer than SCP_TIMEOUT.
    """
    print(f">>> Pulling {remote}:{remote_db} -> {dest} ...")
    subprocess.run(["scp", f"{remote}:{remote_db}", str(dest)],
                   check=True, timeout=SCP_TIMEOUT)


def table_columns(con: sqlite3.Connection, table: str) -> list[str]:
    """Column names of `table`, or [] if there is no such table."""
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})")]


def table_names(con: sqlite3.Connection) -> list[str]:
    """Every ordinary table in the database, alphabetically."""
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def backup_local(db: Path) -> Path | None:
    """
    Copy the local db aside before it is written to.

    Named `<db>.bak-<date>`, the convention pushdb.sh uses.  An existing
    backup for today is kept, so the first state of the day survives
    however many times this runs.

    Args:
        db: The database about to be modified.

    Returns:
        The backup path, or None if today's already existed.
    """
    dest = db.with_name(f"{db.name}.bak-{date.today().isoformat()}")
    if dest.exists():
        print(f"    backup for today already exists: {dest.name}")
        return None
    shutil.copy2(db, dest)
    print(f"    backed up local db as {dest.name}")
    return dest


def shared_columns(rcon, lcon, table) -> tuple:
    """
    The columns both databases have, and what each has that the other lacks.

    Args:
        rcon: Read-only connection to the remote copy.
        lcon: Connection to the local db.
        table: Table name.

    Returns:
        (shared, remote_only, local_only), all lists of column names, with
        `rowid` excluded from all three -- it is handled separately because
        mirroring has to preserve it and upserting must not.

    Raises:
        SystemExit: Either database has no such table.
    """
    rcols = table_columns(rcon, table)
    lcols = table_columns(lcon, table)
    if not rcols:
        raise SystemExit(f"Error: remote db has no `{table}` table")
    if not lcols:
        raise SystemExit(f"Error: local db has no `{table}` table")
    shared = [c for c in rcols if c in lcols and c != "rowid"]
    return (shared,
            [c for c in rcols if c not in lcols and c != "rowid"],
            [c for c in lcols if c not in rcols and c != "rowid"])


def do_mirror(rcon, lcon, table, cols, dry_run) -> None:
    """
    Replace the whole local table with the remote's, rowids preserved.

    Delete-then-insert inside one transaction rather than a row-by-row
    replace: `config` carries a partial unique index (`ux_config_one_active`,
    over `active = 1`) that a row-by-row pass can transiently violate, and
    an emptied table cannot violate anything.

    Args:
        rcon: Read-only connection to the remote copy.
        lcon: Connection to the local db.
        table: Table name.
        cols: Columns to copy.
        dry_run: Report and change nothing.

    Raises:
        sqlite3.Error: The write failed; the transaction is rolled back.
    """
    rows = rcon.execute(
        f"SELECT rowid, {', '.join(cols)} FROM {table}").fetchall()
    before = lcon.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"  {table}: local has {before} row(s), remote has {len(rows)}")
    if dry_run:
        print(f"  would replace all {before} local row(s) with the remote's "
              f"{len(rows)} (dry run)")
        return
    try:
        lcon.execute("BEGIN")
        lcon.execute(f"DELETE FROM {table}")
        lcon.executemany(
            f"INSERT INTO {table} (rowid, {', '.join(cols)}) VALUES "
            f"({', '.join('?' for _ in range(len(cols) + 1))})", rows)
        lcon.commit()
    except sqlite3.Error:
        lcon.rollback()
        raise
    print(f"  replaced {before} local row(s) with {len(rows)} from the Pi")


def do_upsert(rcon, lcon, table, cols, key, keys, dry_run) -> None:
    """
    Copy rows matched on a natural key, leaving other local rows alone.

    Args:
        rcon: Read-only connection to the remote copy.
        lcon: Connection to the local db.
        table: Table name.
        cols: Columns to copy.
        key: The natural-key column, matched COLLATE NOCASE.
        keys: Key values to copy, or empty for every remote row.
        dry_run: Report and change nothing.

    Raises:
        sqlite3.Error: The write failed; the transaction is rolled back.
    """
    if keys:
        wanted = list(keys)
    else:
        wanted = [r[0] for r in rcon.execute(
            f"SELECT {key} FROM {table} ORDER BY rowid")]
        print(f"  no keys given: copying all {len(wanted)} remote row(s)")

    inserted = updated = missing = 0
    try:
        lcon.execute("BEGIN")
        for k in wanted:
            row = rcon.execute(
                f"SELECT {', '.join(cols)} FROM {table} "
                f"WHERE {key} = ? COLLATE NOCASE", (k,)).fetchone()
            if row is None:
                print(f"  {k}: not found on the Pi")
                missing += 1
                continue
            values = dict(zip(cols, row))
            local = lcon.execute(
                f"SELECT rowid FROM {table} WHERE {key} = ? COLLATE NOCASE",
                (k,)).fetchone()
            action = "update" if local else "insert"
            print(f"  {values[key]}: {action}"
                  + (" (dry run)" if dry_run else ""))
            if dry_run:
                continue
            if local is None:
                lcon.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES "
                    f"({', '.join('?' for _ in cols)})",
                    tuple(values[c] for c in cols))
                inserted += 1
            else:
                upd = [c for c in cols if c != key]
                lcon.execute(
                    f"UPDATE {table} SET "
                    f"{', '.join(f'{c} = ?' for c in upd)} WHERE rowid = ?",
                    tuple(values[c] for c in upd) + (local[0],))
                updated += 1
        lcon.commit() if not dry_run else lcon.rollback()
    except sqlite3.Error:
        lcon.rollback()
        raise
    print(f"Done: {inserted} inserted, {updated} updated, "
          f"{missing} not found on the Pi.")

def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Copy rows of any table from the Pi's database into the "
                    "local one.")
    p.add_argument("keys", nargs="*",
                   help="natural keys to copy in upsert mode, e.g. C1015 "
                        "(case-insensitive).  Ignored in mirror mode; omit "
                        "in upsert mode to copy every remote row.")
    p.add_argument("--table", default=DEFAULT_TABLE,
                   help=f"table to copy (default: {DEFAULT_TABLE})")
    p.add_argument("--mode", choices=("upsert", "mirror"), default=None,
                   help="override the table's default mode; see TABLES")
    p.add_argument("--list-tables", action="store_true",
                   help="list both databases' tables and row counts, then "
                        "stop")
    p.add_argument("--remote", default=REMOTE,
                   help=f"ssh host alias (default: {REMOTE})")
    p.add_argument("--remote-db", default=REMOTE_DB,
                   help=f"remote db path (default: {REMOTE_DB})")
    p.add_argument("--local-db", default=str(LOCAL_DB), type=Path,
                   help=f"local db path (default: {LOCAL_DB})")
    p.add_argument("--no-backup", action="store_true",
                   help="skip the local backup (not recommended)")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would change without writing")
    return p.parse_args(argv)

def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        # ── IDE run guard ───────────────────────────────────────────────
        # No terminal?  Put the run you want here and hit Run/Debug.
        argv = ["--table", "roi", "--dry-run"]
        argv = ["--table", "samples", "--dry-run"]
        argv = "C1022 C1023 C1024 C1025 --table samples".split()
        print(f"(no arguments: using the IDE default {argv})")

    args = parse_args(argv)
    local_db = Path(args.local_db)
    if not local_db.exists():
        print(f"Error: local db not found: {local_db}", file=sys.stderr)
        return 1

    mode = args.mode or TABLES.get(args.table, ("mirror", None))[0]
    key = TABLES.get(args.table, ("mirror", None))[1]
    if mode == "upsert" and key is None:
        print(f"Error: no natural key known for `{args.table}`; add one to "
              f"TABLES or use --mode mirror", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(dir=TEMPDIR, prefix="fetch_rows_") as tmpdir:
        remote_copy = Path(tmpdir) / "pi_colorimeter.sqlite"
        try:
            fetch_remote_copy(args.remote, args.remote_db, remote_copy)
        except subprocess.CalledProcessError as e:
            print(f"Error: scp from {args.remote} failed: {e}",
                  file=sys.stderr)
            return 1
        except subprocess.TimeoutExpired:
            print(f"Error: scp from {args.remote} timed out after "
                  f"{SCP_TIMEOUT}s", file=sys.stderr)
            return 1

        rcon = sqlite3.connect(f"file:{remote_copy}?mode=ro", uri=True)
        lcon = sqlite3.connect(local_db)
        try:
            if args.list_tables:
                rt, lt = table_names(rcon), table_names(lcon)
                print(f"\n{'table':<16}{'Pi':>8}{'local':>8}")
                for t in sorted(set(rt) | set(lt)):
                    n_r = (rcon.execute(f"SELECT COUNT(*) FROM {t}"
                                        ).fetchone()[0] if t in rt else "-")
                    n_l = (lcon.execute(f"SELECT COUNT(*) FROM {t}"
                                        ).fetchone()[0] if t in lt else "-")
                    print(f"{t:<16}{n_r:>8}{n_l:>8}"
                          + ("   (local only)" if t not in rt else "")
                          + ("   (Pi only)" if t not in lt else ""))
                return 0

            cols, remote_only, local_only = shared_columns(
                rcon, lcon, args.table)
            if remote_only:
                print(f"Note: remote-only columns not copied: "
                      f"{', '.join(remote_only)}")
            if local_only:
                print(f"Note: local-only columns left untouched: "
                      f"{', '.join(local_only)}")

            print(f">>> {args.table}: {mode}"
                  + (f" on {key}" if mode == "upsert" else "")
                  + (" (dry run)" if args.dry_run else ""))
            if not args.dry_run and not args.no_backup:
                backup_local(local_db)

            if mode == "mirror":
                do_mirror(rcon, lcon, args.table, cols, args.dry_run)
            else:
                do_upsert(rcon, lcon, args.table, cols, key, args.keys,
                          args.dry_run)
        finally:
            rcon.close()
            lcon.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
