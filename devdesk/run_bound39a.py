"""
run_bound39a.py — pooled flat-field run over bound roi.rowid 39.
--------------------------------------------------------------------------
Desktop-only.  One study, spelled out: which archives, which bound, which
ROIs, and where the answers go.  All the machinery is in flatfield_pool.py;
this file exists so the study is a thing that can be read, re-run and diffed
rather than a command line somebody remembers.

The five archives image one gray card five times, in three placements:

    ZP1001, ZP1002   'A1'    an exact repeat, the card not touched between
    ZP1003           'A2'    the card lifted and put back down
    ZP1004, ZP1005   'A180'  the card turned end for end, then repeated

Placements are what get equal weight, not files: pooling per file would give
the untouched pair two fifths of the vote when it carries one placement's
worth of information.  The rotated pair doubles as the control that separates
the instrument's illumination from the card's own chip-to-chip difference —
see flatfield_pool.rotation_control().

The archives were captured against bound roi.rowid 26 (806, 386, 732, 728);
the analysis is over roi.rowid 39 (992, 588, 366, 260), which is interior to
it, so every stored layer is subcropped before anything else happens.

Usage:
    .venv/bin/python3 devdesk/run_bound39a.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flatfield_pool import analyze  # noqa: E402

FILES = (
    ("ZP1001_grayA1a.h5", 26, "A1"),
    ("ZP1002_grayA1b.h5", 26, "A1"),
    ("ZP1003_grayA2a.h5", 26, "A2"),
    ("ZP1004_grayA180a.h5", 26, "A180"),
    ("ZP1005_grayA180b.h5", 26, "A180"),
)

if __name__ == "__main__":
    analyze(
        files=FILES,          # (archive, the roi.rowid it was cropped to,
                              #  placement); older archives record no bound,
                              #  so the rowid is carried here
        bound=39,             # the bound to analyse
        roi_rowid=(32, 35, 36, 39),   # sample, chip2, chip1, bound
        writedir="devdesk/flatfield/bound39a",
        rotated=("A180",),    # placements imaged end for end
        figures="--no-figures" not in sys.argv,
    )
