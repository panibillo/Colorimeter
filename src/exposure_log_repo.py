"""
exposure_log_repo.py  --  The `exposure_log` table: every frame the strip
rule measured, one row per Bayer layer.
-----------------------------------------------------------------------------
Detour stage 4b of docs/work_plan_chain_poc_2026-11-09.md (Bill, 2026-11-14).

Why a table when the archive already holds the same numbers: the archive is
one file per sample, so a question across samples ("how often does violet
re-aim?", "how dark does green.B get at Tus?", "which LEDs needed Bus last
month?") means opening every file.  Here it is one query, on the instrument
or on a desktop copy of its database:

    sqlite3 src/colorimeter.sqlite \\
      "SELECT LED, purpose, attempt, verdict, exposure_us, max_dn
         FROM exposure_log WHERE samp_no = 'C1048' AND bayer_layer = 'B'"

The archive stays the record of the sample; this table is an index of it,
and losing a write to it never costs a capture (capture_runner logs the
failure and carries on).

What gets a row
---------------
Strip-rule sweeps only (pre-image and measurement).  For each sweep:

  padfind  the pad-finding frame (its R layer only, the one converted), one
           row, measured over the test region built from that very fit.
  tus      every try at finding Tus, the rejected ones too (attempt 1, 2,
           ...), and then the extra frames kept at Tus (attempt NULL).
  bus      every frame kept at Bus (attempt NULL).

Each frame has one row per Bayer layer, kept or not; `stored` says whether
that layer of that frame is in the archive.

Columns (created in src/db_DDL.sql, not here)
---------------------------------------------
    rowid          INTEGER PRIMARY KEY
    add_dt         when the frame was captured (local time, ms), not when
                   the row was written
    samp_no        the sample, or NULL for a run that belongs to none (a
                   bench check)
    archive        the archive's file name, e.g. 'C1048.h5'
    section        'pre' (pre-image) or 'post' (measurement)
    purpose        'padfind', 'tus' or 'bus'
    attempt        the try number while Tus is searched for (1..
                   capture.max_exposure_tries); NULL for every other frame
    label          the archive label the frame is stored under ('e0', ...);
                   NULL for a rejected try and for the pad-finding frame
                   (stored in the sweep's `pad` group instead)
    verdict        the judgement of a try: 'accept', 'clipped' or 'dim';
                   NULL for frames that were not judged
    stored         1 if this layer of this frame is in the archive, else 0
    LED            LED color name
    bayer_layer    'R', 'Gr', 'Gb' or 'B'
    exposure_us    exposure_us asked for
    applied_us     exposure_us the camera reported applying
    dark_dn        the dark level the rule's arithmetic used (raw DN)
    min_dn, max_dn, mean_dn
                   over the test region (fitted pad + gray chips, hot pixels
                   left out), raw DN, dark level included
    max_where      'pad', 'gray1' or 'gray2': where the brightest pixel is
    floor_min_dn   the darkest pixel the noise-floor test looks at (the
                   region less the side-edge pixels it leaves out)
    n_pixels       test-region pixels measured
    n_clip_top     pixels at or above capture.clip_top_dn
    n_clip_bottom  pixels at or below capture.clip_bottom_dn
    n_below_floor  pixels below capture.noise_floor_dn (edge pixels left out)
    n_edge_left_out  side-edge pixels the floor test left out
    remark         free text, NULL unless something is worth saying

Public surface
--------------
    COLUMNS
    layer_rows(stats, **frame)        -> list of row dicts for one frame
    rows_for_led(color, record, frames, **sweep) -> row dicts for one LED
    write_rows(rows)                  -> rows written
    read_rows(samp_no=None, archive=None) -> list of row dicts
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Optional, Sequence

import db_manager as db

log = logging.getLogger(__name__)

#: Every column but rowid, in the order rows are written.
COLUMNS = ("add_dt", "samp_no", "archive", "section", "purpose", "attempt",
           "label", "verdict", "stored", "LED", "bayer_layer", "exposure_us",
           "applied_us", "dark_dn", "min_dn", "max_dn", "mean_dn",
           "max_where", "floor_min_dn", "n_pixels", "n_clip_top",
           "n_clip_bottom", "n_below_floor", "n_edge_left_out", "remark")

#: The LayerStats fields (exposure_rule.LayerStats.to_dict()) copied as is.
_STAT_FIELDS = ("min_dn", "max_dn", "mean_dn", "max_where", "floor_min_dn",
                "n_pixels", "n_clip_top", "n_clip_bottom", "n_below_floor",
                "n_edge_left_out")

#: Values of `purpose`.
PURPOSE_PADFIND = "padfind"
PURPOSE_TUS     = "tus"
PURPOSE_BUS     = "bus"


def _number(v):
    """A float for the table, or None for NaN (SQLite has no NaN)."""
    if v is None:
        return None
    f = float(v)
    return None if f != f else f


def layer_rows(stats: dict, *, stored_layers: Iterable[str] = (),
               **frame) -> List[dict]:
    """
    The rows for one frame: one per Bayer layer in `stats`.

    Args:
        stats:  {layer: LayerStats.to_dict()} for the frame.
        stored_layers: The layers of this frame that are in the archive.
        **frame: The columns every layer of the frame shares (add_dt,
                 samp_no, archive, section, purpose, attempt, label,
                 verdict, LED, exposure_us, applied_us, dark_dn, remark);
                 a column not given is NULL.

    Returns:
        A list of {column: value} dicts, in layer order.
    """
    unknown = set(frame) - set(COLUMNS)
    if unknown:
        raise ValueError(f"not exposure_log columns: {sorted(unknown)}")
    kept = set(stored_layers)
    rows = []
    for layer, st in stats.items():
        row = {c: frame.get(c) for c in COLUMNS}
        row["bayer_layer"] = layer
        row["stored"] = int(layer in kept)
        for k in _STAT_FIELDS:
            row[k] = st.get(k)
        for k in ("min_dn", "max_dn", "mean_dn", "floor_min_dn", "dark_dn"):
            row[k] = _number(row[k])
        rows.append(row)
    return rows


def rows_for_led(color: str, record: dict, frames: dict, *,
                 samp_no: Optional[str], archive: str, section: str,
                 dark_dn: float) -> List[dict]:
    """
    The rows for one LED of one strip-rule sweep.

    The kept try (the last one) is the frame stored as e0, so it gets one
    set of rows carrying both its attempt number and its label; the frames
    after it (e1, ...) carry their label only.

    Args:
        color:   LED color name.
        record:  The LED's strip-rule record, as stored in led_metadata
                 (capture_runner._store_strip_frames): its "attempts",
                 "roles" and "frame_stats" are read.
        frames:  {label: {"exposure_us", "applied_us", "captured_dt",
                 "stored": [layers in the archive]}} for the stored frames.
        samp_no: The sample, or None.
        archive: The archive's file name.
        section: 'pre' or 'post'.
        dark_dn: The dark level the rule used, raw DN.

    Returns:
        A list of row dicts (see layer_rows).
    """
    sweep = {"samp_no": samp_no or None, "archive": archive,
             "section": section, "LED": color, "dark_dn": dark_dn}
    rows: List[dict] = []
    attempts = record.get("attempts", [])
    labels = sorted(record.get("roles", {}), key=lambda lb: int(lb[1:]))
    for i, a in enumerate(attempts):
        label = labels[0] if (i == len(attempts) - 1 and labels) else None
        stored = frames.get(label, {}).get("stored", ()) if label else ()
        rows += layer_rows(
            a["stats"], stored_layers=stored,
            add_dt=a.get("captured_dt"), purpose=PURPOSE_TUS,
            attempt=a["attempt"], label=label, verdict=a["verdict"],
            exposure_us=a["exposure_us"], applied_us=a["applied_us"],
            **sweep)
    for label in labels[1:]:
        f = frames.get(label, {})
        rows += layer_rows(
            record["frame_stats"][label],
            stored_layers=f.get("stored", ()),
            add_dt=f.get("captured_dt"), purpose=record["roles"][label],
            attempt=None, label=label, verdict=None,
            exposure_us=f.get("exposure_us"),
            applied_us=f.get("applied_us"), **sweep)
    return rows


def write_rows(rows: Sequence[dict]) -> int:
    """
    Insert rows into exposure_log, in one transaction.

    Args:
        rows: Row dicts (layer_rows / rows_for_led).

    Returns:
        The number of rows written.

    Raises:
        sqlite3.Error: the write failed (logged by db_manager).  The capture
            catches it: a failed log write must never cost a capture.
    """
    if not rows:
        return 0
    db.write_many(
        f"INSERT INTO exposure_log ({', '.join(COLUMNS)}) "
        f"VALUES ({', '.join('?' * len(COLUMNS))})",
        [tuple(r.get(c) for c in COLUMNS) for r in rows])
    return len(rows)


def rename_archive(samp_no: str, old: str, new: str) -> int:
    """
    Point one sample's rows at an archive's new name.

    Used when a cancelled run's archive is moved aside (arbiter's end-of-run
    cleanup), so that the file name it leaves free, reused by the sample
    number's next run, does not also collect the abandoned run's rows.

    Args:
        samp_no: The sample whose rows move.  Rows of any other sample that
                 name the same file are left alone.
        old:     The archive column as written, e.g. 'C1054.h5'.
        new:     The new value, relative to the capture directory, e.g.
                 'abandoned/C1054_20261116_1225.h5'.

    Returns:
        The number of rows changed.

    Raises:
        sqlite3.Error: the write failed.
    """
    return db.write_one(
        "UPDATE exposure_log SET archive = ? WHERE samp_no = ? AND archive = ?",
        (new, samp_no, old))


def read_rows(samp_no: Optional[str] = None,
              archive: Optional[str] = None) -> List[dict]:
    """
    Read rows back, oldest first, for one sample and/or one archive.

    Args:
        samp_no: Only this sample's rows, if given.
        archive: Only this archive's rows, if given.

    Returns:
        A list of {column: value} dicts, each with its rowid.
    """
    where, vals = [], []
    if samp_no is not None:
        where.append("samp_no = ?")
        vals.append(samp_no)
    if archive is not None:
        where.append("archive = ?")
        vals.append(archive)
    sql = f"SELECT rowid, {', '.join(COLUMNS)} FROM exposure_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY rowid"
    return [dict(zip(("rowid",) + COLUMNS, r))
            for r in db.read_all(sql, tuple(vals))]
