"""
test_image_repo.py  —  Data access for the `test_image` table.
--------------------------------------------------------------
One row per test image taken from Menu → Advanced → Capture Image.  The row
records the file's name, so a name is never used twice; the files themselves
are in config.capture_test_image_dir().  Nothing here deletes a row: to reuse a
name, delete its row (and its file) from outside the program.

Names are a prefix and a number, like sample numbers: 'IMG0001', 'IMG0002'.

Public surface
--------------
    file_name(name) -> str                     'IMG0001' -> 'IMG0001.h5'
    name_problem(name, directory) -> str|None  why a name cannot be used
    next_name(directory, template) -> str      the next free name
    record(name, led, rungs, exposures_us, image_types, description) -> int
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import db_manager as db

TEMPLATE = "IMG0001"
EXTENSION = ".h5"
_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_PREFIX_NUMBER = re.compile(r"^(.*?)(\d+)$")


def file_name(name: str) -> str:
    """The test image's file name: 'IMG0001' -> 'IMG0001.h5'."""
    return f"{name.strip()}{EXTENSION}"


def _in_table(h5name: str) -> bool:
    return db.read_val("SELECT COUNT(*) FROM test_image WHERE h5name = ?",
                       (h5name,)) > 0


def name_problem(name: str, directory: Path) -> Optional[str]:
    """
    Say why `name` cannot be used, or return None if it can.

    A name must be letters, digits, '-' or '_', and must not be in the table
    or already on disk (case is ignored, as the table ignores it).
    """
    name = name.strip()
    if not name:
        return "Type a file name"
    if not _NAME.match(name):
        return "Name: letters, digits, - and _ only"
    h5name = file_name(name)
    if _in_table(h5name):
        return f"{name} is already used"
    if directory.is_dir() and any(p.name.lower() == h5name.lower()
                                  for p in directory.iterdir()):
        return f"{h5name} is already on disk"
    return None


def next_name(directory: Path, template: str = TEMPLATE) -> str:
    """
    The name after the highest one used with the template's prefix, counting
    both the table and the files on disk; the template itself if none is.

    'IMG0001' with IMG0001 and IMG0007 used gives 'IMG0008'.  The number keeps
    the template's width.
    """
    m = _PREFIX_NUMBER.match(template)
    prefix, width = (m.group(1), len(m.group(2))) if m else (template, 4)
    used: Iterable[str] = [h for (h,) in db.read_all("SELECT h5name FROM test_image")]
    if directory.is_dir():
        used = list(used) + [p.name for p in directory.iterdir()]
    highest = None
    for h5name in used:
        stem = h5name[:-len(EXTENSION)] if h5name.lower().endswith(EXTENSION) else None
        if stem is None or not stem.upper().startswith(prefix.upper()):
            continue
        rest = stem[len(prefix):]
        if rest.isdigit():
            highest = int(rest) if highest is None else max(highest, int(rest))
    if highest is None:
        return template
    return f"{prefix}{highest + 1:0{width}d}"


def record(name: str, led: str, rungs: Iterable[str],
           exposures_us: Iterable[int], image_types: Iterable[str],
           description: str = "") -> int:
    """
    Add the row for a saved test image and return its rowid.

    Raises:
        sqlite3.IntegrityError: the name is already in the table.
    """
    db.write_one(
        "INSERT INTO test_image (add_dt, h5name, led, rungs, exposures_us, "
        "image_types, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), file_name(name), led,
         " ".join(rungs), " ".join(str(int(us)) for us in exposures_us),
         " ".join(image_types), description.strip()))
    return db.read_val("SELECT MAX(rowid) FROM test_image") or 0
