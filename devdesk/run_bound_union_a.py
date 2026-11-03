"""
run_bound_union_a.py — pooled flat-field run over the union bound, roi.rowid 61.
--------------------------------------------------------------------------
Desktop-only.  The same study as run_bound39a.py, over a bigger area and
with the gray-chip and sample positions moved on 2026-10-28.

Why a new fit.  On 2026-10-28 the chips were moved outward (roi rowids 50 and
51) and the bound became roi rowid 52 (974, 608, 398, 230).  Each chip then
hung 6 layer px (12 stream px) past the left or right edge of the bound-39 fit
(992, 588, 366, 260), so `whitecorr.LayerModel.gain_at` returned NaN there and
`padphot.divisor_map` refused the capture (first seen on C1038_IMG003).

Why the union bound.  Fitting over bound 52 alone would leave the top rows of
older captures outside the fit (C1014 onward were cropped at y = 548, C1002 to
C1013 at y = 588).  Roi rowid 61, 'ffbound' (974, 548, 398, 300), is the
smallest rectangle containing bounds 39, 41 and 52, so every capture so far
lies inside it.  The row is inactive (active = 0), so the instrument never
loads it as a crop; it exists only so this fit can name its domain by rowid.

The archives are the same five gray-card images as bound39a.  They were cropped
to bound 26 (806, 386, 732, 728), which contains rowid 61 with room to spare,
so no new captures are needed.

Roles.  Since 2026-08-09 both chips have the role 'white_balance' in the roi
table, so the left/right assignment is given here explicitly.

Like run_bound39a.py, this run **publishes** to src/colorimeter.sqlite's
`layermodels` table unless --no-publish is given.  Prior rows are superseded
(active = 0), never deleted.

Usage:
    timeout -k 10 900 .venv/bin/python3 -u devdesk/run_bound_union_a.py --no-publish
    timeout -k 10 900 .venv/bin/python3 -u devdesk/run_bound_union_a.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flatfield_pool import analyze  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

FILES = (
    ("ZP1001_grayA1a.h5", 26, "A1"),
    ("ZP1002_grayA1b.h5", 26, "A1"),
    ("ZP1003_grayA2a.h5", 26, "A2"),
    ("ZP1004_grayA180a.h5", 26, "A180"),
    ("ZP1005_grayA180b.h5", 26, "A180"),
)

if __name__ == "__main__":
    publish = "--no-publish" not in sys.argv
    analyze(
        files=FILES,          # (archive, the roi.rowid it was cropped to,
                              #  placement)
        bound=61,             # 'ffbound', the union of bounds 39, 41 and 52
        roi_rowid=(53, 50, 51, 61),   # sample, chip2, chip1, bound
        roles={53: "sample", 51: "gray1", 50: "gray2"},
        writedir="devdesk/flatfield/bound_union_a",
        rotated=("A180",),    # placements imaged end for end
        figures="--no-figures" not in sys.argv,
        layermodels_db=(REPO_ROOT / "src" / "colorimeter.sqlite"
                        if publish else None),
        analysisref="bound_union_a",
    )
