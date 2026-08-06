"""
version.py — What this checkout is, and what it can read.
------------------------------------------------------------------------------
One file answers both halves of "which version?", because in practice they are
asked together and confusing them is the expensive mistake:

    __version__     the release this checkout *is*.  A label on a build.
    READS_*         the archive and database formats this checkout can *open*.

The distinction matters because archives outlive the code that wrote them, and
the Pi and the desktop are synced independently:

    `software_ver` tells you which build wrote a file.  It never tells you
    whether you can read it.  Readers branch on the format versions below,
    never on `software_ver`.

Nothing here imports from the project, so devdesk/ and desktop/ tools can read
it without dragging in the database layer.

Version model
-------------
Plain semantic versioning, MAJOR.MINOR.PATCH.  Being on 0.x is itself the alpha
statement (SemVer clause 4: anything may change at any time), so there is no
`aN`/`devN` suffix to also keep moving.

    PATCH  0.3.0 -> 0.3.1   Fixes and internal changes.  Archives and the
                            database keep the same shape, so a desktop tool at
                            0.3.0 reads 0.3.1 archives unchanged.
    MINOR  0.3.0 -> 0.4.0   Anything a stored archive or database would notice:
                            a config parameter added, renamed or retyped; a
                            capture-path or workflow change; and *always*
                            whenever one of the format versions below moves.
    MAJOR  0.x   -> 1.0.0   Archive format frozen, instrument calibrated and
                            validated.  Not soon.

Where the version goes
----------------------
`config_repo.stamp_version()` writes __version__ into the instrument's own
config table at every launch (role 'device', name 'software_ver', editable=0).
The config table keeps superseded rows, so an instrument accumulates a dated log
of every version it has ever run:

    sqlite3 src/colorimeter.sqlite \\
      "SELECT value, add_dt, active FROM config
        WHERE role='device' AND name='software_ver' ORDER BY add_dt"

From there it reaches every capture archive, as the root attribute
`software_ver` and inside the /meta record (capture_meta.py).

The format versions
-------------------
Three integers are the actual compatibility contract.  Each lives beside the
code that writes it, keeps its own `1 -> 2: what changed` comment, and moves
independently of the others:

    capture_h5.SCHEMA_VERSION    .h5 group/dataset layout   attr schema_version
    capture_meta.META_VERSION    shape of the /meta record  attr meta_version
    db_bootstrap.SCHEMA_VERSION  the SQLite schema          PRAGMA user_version

The ranges below are the other side of that contract: what *this* checkout
accepts.  Widening one is how a desktop change restores compatibility with an
older instrument's archives; narrowing one is how support is dropped.  Either
way it is one visible edit here plus a CHANGELOG line.  test/test_version.py
fails if a writer is bumped without the matching range being widened.

Usage:
    import version

    print(version.__version__)                     # "0.3.0"

    with Hdf5Reader(path) as r:                    # devdesk/ tools
        problem = version.check_archive(r.attrs("/"))
        if problem:
            sys.exit(problem)
"""

from typing import Dict, Optional, Tuple

__version__ = "0.3.0"

# (oldest, newest) format version this checkout can read, inclusive.
READS_SCHEMA: Tuple[int, int] = (1, 2)   # .h5 layout,  capture_h5
READS_META:   Tuple[int, int] = (1, 2)   # /meta shape, capture_meta
READS_DB:     Tuple[int, int] = (2, 2)   # SQLite,      db_bootstrap

# attr name -> (supported range, what to call it in a message).  An archive
# missing an attr predates it entirely, which is version 1 by definition.
_ARCHIVE_CHECKS = (
    ("schema_version", READS_SCHEMA, "archive layout"),
    ("meta_version",   READS_META,   "archive metadata"),
)


def check_archive(attrs: Dict) -> Optional[str]:
    """
    Say whether this checkout can read an archive, and why not if it cannot.

    Returns a message rather than raising, because a caller may know how to
    cope: devdesk/flatfield.py, for one, has a documented fallback for
    schema-1 archives that carry no ROI geometry of their own.

    Args:
        attrs: The archive's root attributes, as Hdf5Reader.attrs("/") returns
               them.  A missing version attribute is read as version 1.

    Returns:
        None if every format version is in range, otherwise a one-line
        explanation naming the archive's version, the range this checkout
        supports, and which way to fix it.

    Example:
        >>> check_archive({"schema_version": 2, "meta_version": 2})
        >>> check_archive({"schema_version": 9})[:26]
        'archive layout version 9 '
    """
    wrote = attrs.get("software_ver")
    made_by = f" (written by software {wrote})" if wrote else ""

    for name, (oldest, newest), label in _ARCHIVE_CHECKS:
        got = int(attrs.get(name, 1))
        if got > newest:
            return (f"{label} version {got}{made_by} is newer than these tools "
                    f"(version {__version__} reads {oldest}-{newest}). Sync "
                    f"this checkout to the version the instrument is running.")
        if got < oldest:
            return (f"{label} version {got}{made_by} is older than these tools "
                    f"support (version {__version__} reads {oldest}-{newest}). "
                    f"Use a checkout from before support was dropped.")
    return None


def check_database(user_version: int) -> Optional[str]:
    """
    Say whether this checkout can run on a database, and why not if it cannot.

    Args:
        user_version: The database's PRAGMA user_version.

    Returns:
        None if the schema is in range, otherwise a one-line explanation.
        db_bootstrap does the real gating at launch, including the migration
        hint; this is for desktop tools that open a database directly.
    """
    oldest, newest = READS_DB
    if oldest <= user_version <= newest:
        return None
    direction = "newer than" if user_version > newest else "older than"
    return (f"database schema version {user_version} is {direction} these "
            f"tools (version {__version__} runs on {oldest}-{newest}).")
