"""
touch_cal_store.py  —  Read and safely replace the touch calibration file.
--------------------------------------------------------------------------
The touch calibration (config/touch_cal.json on the Pi) is six numbers that
turn the touch controller's raw readings into screen pixels:

    px = a*rx + b*ry + c
    py = d*rx + e*ry + f

It belongs to one panel as assembled and cannot be rebuilt without tapping the
screen again, so replacing it is careful:

  1. The present file is COPIED to touch_cal.bakN.json (the first unused N)
     before anything is written.  Copied, not moved: the present file stays
     in place until the new one has replaced it whole.  Backups are never
     deleted.
  2. The new values go to a temporary file in the same folder, which is
     flushed to disk and then swapped in with os.replace().  The file is
     therefore always either the complete old one or the complete new one,
     even if the power fails partway.
  3. The file is read back and compared with what was meant to be written.

Public surface
--------------
    AFFINE_KEYS
    load(path) -> dict | None       the six numbers, or None if unusable
    backup(path) -> Path | None     copy the present file aside
    save(cal, path) -> Path | None  backup, then replace; returns the backup
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

AFFINE_KEYS = ("a", "b", "c", "d", "e", "f")


class CalWriteError(RuntimeError):
    """The new calibration could not be written safely; the message says where the old values are."""


def _clean(cal: dict) -> dict:
    """The six numbers as floats.  Raises ValueError if any is missing or not finite."""
    out = {k: float(cal[k]) for k in AFFINE_KEYS}
    bad = [k for k, v in out.items() if not math.isfinite(v)]
    if bad:
        raise ValueError(f"calibration value(s) not finite: {', '.join(bad)}")
    return out


def load(path) -> Optional[dict]:
    """
    The six numbers from `path`, or None if the file is missing, unreadable or
    incomplete.  Any other keys in the file (an old 'threshold') are ignored.
    """
    try:
        with open(path) as f:
            return _clean(json.load(f))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def backup(path) -> Optional[Path]:
    """
    Copy the present file to <stem>.bakN<suffix>, the first N not in use, and
    return the copy's path; None if there is no file to back up.

    Raises:
        OSError: the copy failed.  Nothing has been changed.
    """
    p = Path(path)
    if not p.exists():
        return None
    n = 1
    while (bak := p.with_name(f"{p.stem}.bak{n}{p.suffix}")).exists():
        n += 1
    shutil.copy2(p, bak)
    log.info("touch calibration backed up: %s -> %s", p.name, bak.name)
    return bak


def save(cal: dict, path) -> Optional[Path]:
    """
    Replace the calibration at `path` with `cal`, keeping a backup.

    Returns:
        The backup's path, or None if there was no file before.

    Raises:
        ValueError:    `cal` lacks a value or holds a non-finite one.
        CalWriteError: the backup or the write failed, and the file at
                       `path` is exactly as it was; or the new file did not
                       read back as written, and the old values are in the
                       backup the message names.
    """
    new = _clean(cal)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        bak = backup(p)
    except OSError as e:
        raise CalWriteError(f"could not back up {p.name}: {e}") from e

    tmp = p.with_name(p.name + ".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(new, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        dir_fd = os.open(p.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)                  # the rename itself reaches the disk
        finally:
            os.close(dir_fd)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise CalWriteError(f"could not write {p.name}: {e}") from e

    if load(p) != new:
        raise CalWriteError(f"{p.name} did not read back as written; "
                            f"the old values are in {bak.name if bak else '(no old file)'}")
    log.info("touch calibration saved to %s", p)
    return bak
