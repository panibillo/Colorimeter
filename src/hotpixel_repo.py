"""
hotpixel_repo.py  —  Domain layer for the `hotpixels` table.
-----------------------------------------------------------------------------
Where `src/darkframe.py` (the pure numpy engine) finds and corrects hot
pixels, this module is where those findings are made durable — written once
by a desktop calibration run, read every time the instrument dark-corrects a
frame, and carried between the desktop and the Pi the same way every other
table here is: by copying colorimeter.sqlite (see pushdb.sh / pulldb.sh).

Why a hot-pixel record needs a camera *context*
------------------------------------------------
A hot pixel is a defect in one physical sensor, not a property of the
software.  Four things about the camera can change and each one invalidates
an old record if not accounted for:

  * `model`        — a different Camera Module (imx219 vs imx708) is a
                     different silicon design with a different defect map.
  * `hardware_sn`  — a different physical unit of even the same model has its
                     own, unrelated defects.  This project's camera and
                     instrument are one physical assembly (see
                     capture_meta.py's DEVICE_NAMES), so there is no camera
                     serial distinct from the instrument serial; hardware_sn
                     is used as the practical camera identifier.  Fair
                     alternative not taken: omit hardware_sn and key on model
                     alone, which would be simpler but would silently share
                     one unit's defect map with every other unit of the same
                     model — wrong the moment two instruments exist.
  * `frame_w/h`    — the configured stream resolution (CONFIG_5 vs CONFIG_6,
                     see bayer_cam.py).  A hot pixel's coordinates are only
                     meaningful against the resolution they were found at.
  * `bayer_layer`  — see darkframe.py: each of R/Gr/Gb/B is an independent
                     population of photosites, so a coordinate hot in one
                     layer is unrelated to the same coordinate in another.

`(model, hardware_sn, frame_w, frame_h, bayer_layer)` is therefore the
versioning key — the same granularity layermodel_repo.write_models() uses for
(led_color, bayer_layer), and for the same reason: layers are commonly
recalibrated one at a time (as of this writing only channel B has been run at
all — see tex/dark_signal_characterization.tex's own "Provenance and open
items"), and superseding a coarser unit would silently discard another
layer's calibration history the moment one layer alone is re-run.

The underlying statistics — why the frame's own spatial mean is subtracted
rather than a stored per-pixel array, why a *corrected* outlier test is used
rather than a raw one, and why hot pixels are then repaired by neighbor
interpolation rather than by a remembered offset — are derived and justified
in tex/dark_signal_characterization.tex, principally §9. That document is the
citable reference for every non-obvious choice in this table and in
darkframe.py; this module and that document should be read together.

The `hotpixels` table (created in db_DDL.sql, not here):

    active        1 = in force, 0 = superseded history
    model         camera type, e.g. 'imx708'
    hardware_sn   camera identifier (the instrument serial; see above)
    frame_w/h     stream size the coordinates assume
    bayer_pattern 'BGGR' | 'RGGB', informational
    bayer_layer   'R' | 'Gr' | 'Gb' | 'B'
    x, y          whole-frame Bayer-layer coordinate (see darkframe.py)
    score         deviation at detection time; provenance, not read by the
                  corrector
    exposure_us   reference exposure identification ran at; provenance only,
                  not part of the lookup key — see db_DDL.sql's comment for
                  why, which mirrors layermodels' own reasoning about w1/w2
    n_frames      dark frames pooled for the calibration run
    analysisref   tag naming the analysis run
    remark

Public surface
--------------
    read_active(model, frame_w, frame_h, hardware_sn=None, bayer_layer=None)
        -> list[HotPixel]
    write_hotpixels(hotpixels, analysisref=None, add_dt=None) -> int
    delete_context(model, frame_w, frame_h, hardware_sn=None) -> int

Maintenance:
    python src/hotpixel_repo.py     run the self-test
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence

import db_manager as db

log = logging.getLogger(__name__)

# Column order shared by the SELECT in read_active() and the INSERT in
# write_hotpixels(), so the two cannot drift apart.  `rowid` is left out:
# SQLite assigns it.
_COLUMNS = (
    "active", "add_dt", "model", "hardware_sn", "frame_w", "frame_h",
    "bayer_pattern", "bayer_layer", "x", "y", "score", "exposure_us",
    "n_frames", "analysisref", "remark",
)


@dataclass
class HotPixel:
    """
    One row of `hotpixels` — one flagged photosite behind one Bayer layer.

    Attributes:
        model: Camera type, e.g. 'imx708'.  Matches BayerCam.model.
        frame_w, frame_h: Stream size the coordinates below assume, in
            pixels.  Matches BayerCam.config["size"].
        bayer_layer: One of darkframe.BAYER_LAYERS ('R', 'Gr', 'Gb', 'B').
        x, y: Whole-frame Bayer-layer coordinates — see darkframe.py's module
            docstring for exactly what space this is and why.
        hardware_sn: Camera identifier (the instrument serial); None matches
            any/no serial recorded, which is normal for desktop-only testing
            against a mock camera.
        bayer_pattern: 'BGGR' or 'RGGB', carried along as a sanity check;
            not part of the lookup key.
        score: How far this pixel's dark-frame mean departed from the
            population at detection time, in units of its own predictive
            standard deviation (see darkframe.identify_hot_pixels()).
            Provenance only — correct_dark() does not read it.
        exposure_us: The reference exposure identification ran at.
            Provenance only, not part of the lookup key — see db_DDL.sql.
        n_frames: How many dark frames were pooled for this calibration run.
        analysisref: Tag naming the analysis run that produced this row,
            e.g. 'dark2026-09'.
        remark: Free-text note.
    """
    model: str
    frame_w: int
    frame_h: int
    bayer_layer: str
    x: int
    y: int
    hardware_sn: Optional[str] = None
    bayer_pattern: Optional[str] = None
    score: Optional[float] = None
    exposure_us: Optional[int] = None
    n_frames: Optional[int] = None
    analysisref: Optional[str] = None
    remark: Optional[str] = None


# ── Reads ───────────────────────────────────────────────────────────────────

def _row_to_hotpixel(row: tuple) -> HotPixel:
    """Build a HotPixel from a row selected in _COLUMNS order."""
    (active, _add_dt, model, hardware_sn, frame_w, frame_h,
     bayer_pattern, bayer_layer, x, y, score, exposure_us,
     n_frames, analysisref, remark) = row
    del active      # the caller filtered on it; it is not part of the record
    return HotPixel(
        model=model, frame_w=frame_w, frame_h=frame_h,
        bayer_layer=bayer_layer, x=x, y=y, hardware_sn=hardware_sn,
        bayer_pattern=bayer_pattern, score=score, exposure_us=exposure_us,
        n_frames=n_frames, analysisref=analysisref, remark=remark)


def read_active(model: str, frame_w: int, frame_h: int,
                hardware_sn: Optional[str] = None,
                bayer_layer: Optional[str] = None) -> List[HotPixel]:
    """
    Return the hot pixels known for one camera context.

    An empty result is a normal state — a freshly commissioned instrument, or
    one whose calibration has not been re-run since a resolution change — and
    means "correct nothing but the frame mean", not an error.  See
    darkframe.correct_dark(), which is written to degrade gracefully when
    given no hot-pixel list.

    Args:
        model: Camera type, e.g. 'imx708'.
        frame_w: Stream width in pixels the stored coordinates assume.
        frame_h: Stream height in pixels the stored coordinates assume.
        hardware_sn: Camera identifier to match, or None to match rows with
            no serial recorded (the desktop/mock-camera case).  This does
            not do a wildcard match across serials — a specific serial and
            None are two distinct contexts, by design (see module
            docstring): sharing one unit's defect map with another would be
            silently wrong.
        bayer_layer: Restrict to one Bayer layer, or None for all four.

    Returns:
        List of HotPixel, one per flagged coordinate.  Empty when the
        context has never been calibrated.

    Example:
        >>> read_active("imx708", 2304, 1296, bayer_layer="B")  # doctest: +SKIP
        [HotPixel(model='imx708', frame_w=2304, frame_h=1296, bayer_layer='B',
                  x=112, y=340, ...), ...]
    """
    sql = (f"SELECT {', '.join(_COLUMNS)} FROM hotpixels "
           "WHERE active = 1 AND model = ? AND frame_w = ? AND frame_h = ? "
           "AND hardware_sn IS ?")
    vals: list = [model, frame_w, frame_h, hardware_sn]
    if bayer_layer is not None:
        sql += " AND bayer_layer = ?"
        vals.append(bayer_layer)
    sql += " ORDER BY bayer_layer, y, x"
    rows = db.read_all(sql, tuple(vals))
    return [_row_to_hotpixel(r) for r in rows]


# ── Writes (deactivate old context, insert new — never delete) ─────────────

def _now_local() -> str:
    """Local wall-clock timestamp for add_dt, matching exposure_repo's format."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_hotpixels(hotpixels: Sequence[HotPixel],
                    analysisref: Optional[str] = None,
                    add_dt: Optional[str] = None) -> int:
    """
    Supersede every active row sharing (context, bayer_layer), then insert.

    A context is (model, hardware_sn, frame_w, frame_h); rows are grouped by
    (context, bayer_layer) and each group's prior active rows are deactivated
    before that group's new rows are inserted — so recalibrating one Bayer
    layer replaces only that layer's rows and leaves the other three layers'
    history untouched, the same granularity layermodel_repo.write_models()
    uses for (led_color, bayer_layer). Prior rows are kept with active = 0,
    never deleted.

    Passing an empty list for a (context, layer) that has active rows is not
    how a "no hot pixels found on this layer" result is recorded implicitly —
    call this with that layer's rows explicitly omitted only if you mean to
    leave the prior calibration in force for it.  To retire a whole context
    outright, use `delete_context()`.

    Args:
        hotpixels: The rows to put in force. May span more than one camera
            context and more than one Bayer layer; each (context, layer)
            combination is superseded independently.
        analysisref: Stamped on every row that does not carry its own,
            naming the analysis run that produced it (e.g. 'dark2026-09').
        add_dt: Shared timestamp for the batch; defaults to now.

    Returns:
        The number of rows inserted.

    Example:
        >>> write_hotpixels(found, analysisref="dark2026-09")  # doctest: +SKIP
        12
    """
    if not hotpixels:
        return 0
    stamp = add_dt or _now_local()

    groups = {(h.model, h.hardware_sn, h.frame_w, h.frame_h, h.bayer_layer)
              for h in hotpixels}
    for model, hardware_sn, frame_w, frame_h, bayer_layer in groups:
        db.write_one(
            "UPDATE hotpixels SET active = 0 "
            "WHERE model = ? AND hardware_sn IS ? AND frame_w = ? "
            "AND frame_h = ? AND bayer_layer = ? AND active = 1",
            (model, hardware_sn, frame_w, frame_h, bayer_layer))

    rows = [
        (1, stamp, h.model, h.hardware_sn, h.frame_w, h.frame_h,
         h.bayer_pattern, h.bayer_layer, int(h.x), int(h.y), h.score,
         h.exposure_us, h.n_frames, h.analysisref or analysisref, h.remark)
        for h in hotpixels
    ]
    db.write_many(
        f"INSERT INTO hotpixels ({', '.join(_COLUMNS)}) "
        f"VALUES ({', '.join('?' * len(_COLUMNS))})",
        rows,
    )
    return len(rows)


def delete_context(model: str, frame_w: int, frame_h: int,
                   hardware_sn: Optional[str] = None) -> int:
    """
    Hard-delete every hotpixels row for one camera context — history included.

    Unlike write_hotpixels(), which only deactivates, this really removes
    rows. For explicit purges and test cleanup, not the normal flow.

    Args:
        model: Camera type, e.g. 'imx708'.
        frame_w: Stream width in pixels.
        frame_h: Stream height in pixels.
        hardware_sn: Camera identifier, or None to match rows with no serial
            recorded.

    Returns:
        The number of rows removed.
    """
    return db.write_one(
        "DELETE FROM hotpixels WHERE model = ? AND hardware_sn IS ? "
        "AND frame_w = ? AND frame_h = ?",
        (model, hardware_sn, frame_w, frame_h))


# ─────────────────────────────────────────────────────────────────────────────
# Self-test — runs on a throwaway copy of the real database, so the live
# colorimeter.sqlite is never touched.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import shutil
    import tempfile
    from pathlib import Path

    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)-8s [%(name)s] %(message)s")

    def check(label, got, want):
        ok = "OK " if got == want else "XX "
        print(f"  {ok}{label:48s} got={got!r}")
        assert got == want, f"{label}: {got!r} != {want!r}"

    live = Path(db.DBNAME)
    tmp = Path(tempfile.mkdtemp()) / "test.sqlite"
    shutil.copy(live, tmp)
    db.DBNAME = str(tmp)
    db.write_one("DELETE FROM hotpixels")

    a = HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                bayer_layer="B", x=112, y=340, hardware_sn="XCN-001",
                bayer_pattern="RGGB", score=9.4, exposure_us=50973,
                n_frames=100)
    b = HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                bayer_layer="B", x=980, y=12, hardware_sn="XCN-001",
                score=11.2, exposure_us=50973, n_frames=100)
    other_layer = HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                           bayer_layer="R", x=5, y=5, hardware_sn="XCN-001",
                           score=8.1)

    print("== empty context returns nothing ==")
    check("no rows -> []",
          read_active("imx708", 2304, 1296, hardware_sn="XCN-001"), [])

    print("== write_hotpixels + read back ==")
    check("rows written",
          write_hotpixels([a, b, other_layer], analysisref="selftest"), 3)
    check("all layers by default",
          len(read_active("imx708", 2304, 1296, hardware_sn="XCN-001")), 3)
    check("filtered by layer",
          len(read_active("imx708", 2304, 1296, hardware_sn="XCN-001",
                          bayer_layer="B")), 2)
    got = read_active("imx708", 2304, 1296, hardware_sn="XCN-001",
                      bayer_layer="R")[0]
    check("layer-B coordinate absent from layer-R read", (got.x, got.y), (5, 5))
    check("analysisref stamped", got.analysisref, "selftest")

    print("== distinct contexts do not collide ==")
    check("different hardware_sn is a different context",
          read_active("imx708", 2304, 1296, hardware_sn="OTHER-UNIT"), [])
    check("different resolution is a different context",
          read_active("imx708", 4608, 2592, hardware_sn="XCN-001"), [])
    check("no-serial context is distinct too",
          read_active("imx708", 2304, 1296), [])

    print("== a second write supersedes only the matching context ==")
    c = HotPixel(model="imx708", frame_w=2304, frame_h=1296,
                bayer_layer="B", x=1, y=1, hardware_sn="XCN-001")
    write_hotpixels([c], analysisref="rerun")
    active_now = read_active("imx708", 2304, 1296, hardware_sn="XCN-001")
    check("only the new B-layer row and the untouched R-layer row remain",
          sorted((h.bayer_layer, h.x, h.y) for h in active_now),
          [("B", 1, 1), ("R", 5, 5)])
    check("history kept", db.read_val(
        "SELECT COUNT(*) FROM hotpixels WHERE hardware_sn = 'XCN-001'"), 4)
    check("one active B row", db.read_val(
        "SELECT COUNT(*) FROM hotpixels WHERE hardware_sn = 'XCN-001' "
        "AND bayer_layer = 'B' AND active = 1"), 1)

    print("== delete_context hard-deletes ==")
    check("rows removed",
          delete_context("imx708", 2304, 1296, hardware_sn="XCN-001"), 4)
    check("context now empty",
          read_active("imx708", 2304, 1296, hardware_sn="XCN-001"), [])

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("\n== all hotpixel_repo self-tests passed ==")
