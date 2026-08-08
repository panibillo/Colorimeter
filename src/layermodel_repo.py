"""
layermodel_repo.py  —  Domain layer for the `layermodels` table.
-----------------------------------------------------------------------------
The instrument's side of the flat-field analysis.  `devdesk/flatfield_pool.py`
fits illumination polynomials offline and reduces each one to two numbers; this
module is where the capture and analysis paths pick those numbers up.

The geometry is what makes the reduction possible.  chip1, the sample and
chip2 sit in a row across the field of view, all three imaged together in every
capture, with the sample *between* the chips.  The illumination at the sample
is therefore a fixed linear combination of the two chip readings:

    divisor = w1 * mean(chip1) + w2 * mean(chip2)

and a measurement divides its dark-subtracted, corrected sample mean by that.
No polynomial is evaluated on the instrument.  The coefficients are carried in
the table as provenance (see `coef_json`) so a moved ROI can be re-anchored
offline, but nothing here reads them.

Two responsibilities:

  1. The **capture** path asks which Bayer layers are worth storing at a given
     exposure — keep_channels().  A layer whose signal never leaves the noise
     floor costs about 45 kB per frame and tells no one anything.
  2. The **analysis** path asks for the divisor — divisor().

The `layermodels` table (created in db_DDL.sql, not here):

    active       1 = in force, 0 = superseded history
    usable       1 = this LED/layer carries usable signal, 0 = it never does
    led_color    matches LEDBank colour names
    bayer_layer  'R' | 'Gr' | 'Gb' | 'B'
    exposure_us  the reference exposure p10/p90 were measured at
    roisamp / roi1 / roi2   roi.rowid of the sample and the two chips
    w1, w2       the weights, NULL when usable = 0
    p10, p90     dark-subtracted signal DN over the three patches pooled
    ...          see db_DDL.sql for the rest, which is provenance

`active` and `usable` are independent and neither may be inferred from the
other.  `active` is the versioning switch every table here uses: a fresh
analysis flips the old rows to 0 and inserts new ones, so nothing is lost.
`usable` is a verdict about the optics.  Re-running the analysis must never
look like a layer got worse.

**There is deliberately no exposure key.**  w1 and w2 are properties of the
illumination geometry, not of the exposure: measured across an e0/e1/e2 ladder
they move less than 0.2 %, against a card-placement repeatability floor of
0.4-0.8 %.  Only p10 and p90 depend on exposure, and the sensor is linear to
better than 1.7 % across a ladder, so keep_channels() scales them rather than
demanding a row per rung.  Keying the table on microseconds would have looked
tidier and would have broken the first time the `exposure` table was
recalibrated — which has already happened once, between the archives this
analysis ran on and the ladder now in force.

Public surface
--------------
    read_active()                     -> dict[(led, layer), LayerModel]
    read_model(led, layer)            -> LayerModel | None
    keep_channels(led, exposure_us)   -> tuple[str, ...]
    divisor(led, layer, m1, m2)       -> float
    write_models(models, analysisref) -> int

Maintenance:
    python src/layermodel_repo.py     run the self-test
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

import config_repo as config
import db_manager as db

log = logging.getLogger(__name__)

# The four Bayer layers, in the order the reports and the archive use them.
# These are the keys BayerCam.extract_bayer_layers() returns, so a name here
# indexes an archive dataset directly.
BAYER_LAYERS = ("R", "Gr", "Gb", "B")

# Column order shared by the SELECT in _read() and the INSERT in write_models(),
# so the two cannot drift apart.  `rowid` is left out: SQLite assigns it.
_COLUMNS = (
    "active", "usable", "add_dt", "led_color", "bayer_layer", "exposure_us",
    "roisamp", "roi1", "w1", "roi2", "w2", "p10", "p90", "black_dn",
    "roibound", "deg_y", "deg_x", "coef_json", "norm_mean", "anchor_form",
    "rho", "loo_pct", "analysisref", "remark",
)


@dataclass
class LayerModel:
    """
    One row of `layermodels` — the correction for one LED colour and layer.

    Attributes:
        usable: Whether this LED/layer carries enough signal to be worth
            capturing at all.  When False, w1 and w2 are None and only the
            percentiles are meaningful (they are the evidence for the verdict).
        led_color: LEDBank colour name, e.g. 'amber'.
        bayer_layer: One of BAYER_LAYERS.
        exposure_us: The reference exposure p10 and p90 were measured at.
        roisamp, roi1, roi2: roi.rowid of the sample and the two chips, so a
            reader can confirm the geometry the weights assume still stands.
        w1, w2: The weights on mean(roi1) and mean(roi2).
        p10, p90: Dark-subtracted signal DN over the three patches pooled, at
            exposure_us.
        black_dn: The dark level those percentiles were measured from.
        roibound: roi.rowid of the bound the polynomial was fitted over.
        deg_y, deg_x, coef_json, norm_mean: The polynomial, kept as provenance.
            Nothing on the instrument evaluates it.
        anchor_form: '1anchor' (one brightness scale, least squares over both
            chips) or '2anchor' (offset and tilt re-fitted from the chips).
        rho: The curvature correction the fit found — 1.0 would mean the
            illumination is linear across the chips and the polynomial bought
            nothing.  Evidence, not an input.
        loo_pct: Leave-one-placement-out error, in percent.
        analysisref: The report set that produced the row, e.g. 'bound39a'.
    """
    usable: bool
    led_color: str
    bayer_layer: str
    exposure_us: int
    roisamp: Optional[int] = None
    roi1: Optional[int] = None
    roi2: Optional[int] = None
    w1: Optional[float] = None
    w2: Optional[float] = None
    p10: Optional[float] = None
    p90: Optional[float] = None
    black_dn: Optional[float] = None
    roibound: Optional[int] = None
    deg_y: Optional[int] = None
    deg_x: Optional[int] = None
    coef_json: Optional[str] = None
    norm_mean: Optional[float] = None
    anchor_form: Optional[str] = None
    rho: Optional[float] = None
    loo_pct: Optional[float] = None
    analysisref: Optional[str] = None
    remark: Optional[str] = None

    @property
    def key(self) -> Tuple[str, str]:
        """The (led_color, bayer_layer) pair this model is stored under."""
        return (self.led_color, self.bayer_layer)

    def scaled_p10(self, exposure_us: float) -> Optional[float]:
        """
        p10 rescaled from the reference exposure to `exposure_us`.

        Linear in exposure, which the sensor supports to better than 1.7 %
        across a ladder — comfortably inside a decision as coarse as "is this
        layer worth storing".

        Args:
            exposure_us: The exposure being judged, in microseconds.

        Returns:
            The scaled percentile in signal DN, or None if this model has no
            p10 or no usable reference exposure.
        """
        return self._scale(self.p10, exposure_us)

    def scaled_p90(self, exposure_us: float) -> Optional[float]:
        """p90 rescaled to `exposure_us`; see scaled_p10()."""
        return self._scale(self.p90, exposure_us)

    def _scale(self, value: Optional[float],
               exposure_us: float) -> Optional[float]:
        """Rescale one percentile, or None when there is nothing to scale."""
        if value is None or not self.exposure_us:
            return None
        return value * float(exposure_us) / float(self.exposure_us)

    def predict(self, m_chip1: float, m_chip2: float) -> float:
        """
        Return the illumination divisor at the sample, in DN.

        Args:
            m_chip1: Measured dark-subtracted mean over roi1, in DN.
            m_chip2: Measured dark-subtracted mean over roi2, in DN.

        Returns:
            w1 * m_chip1 + w2 * m_chip2.

        Raises:
            ValueError: This model has no weights (usable is False).
        """
        if self.w1 is None or self.w2 is None:
            raise ValueError(
                f"{self.led_color}/{self.bayer_layer} has no weights "
                f"(usable={self.usable}); there is no divisor to compute")
        return self.w1 * float(m_chip1) + self.w2 * float(m_chip2)


# ── Reads ───────────────────────────────────────────────────────────────────

def _row_to_model(row: tuple) -> LayerModel:
    """Build a LayerModel from a row selected in _COLUMNS order."""
    (active, usable, _add_dt, led_color, bayer_layer, exposure_us,
     roisamp, roi1, w1, roi2, w2, p10, p90, black_dn,
     roibound, deg_y, deg_x, coef_json, norm_mean, anchor_form,
     rho, loo_pct, analysisref, remark) = row
    del active      # the caller filtered on it; it is not part of the model
    return LayerModel(
        usable=bool(usable), led_color=led_color, bayer_layer=bayer_layer,
        exposure_us=int(exposure_us), roisamp=roisamp, roi1=roi1, roi2=roi2,
        w1=w1, w2=w2, p10=p10, p90=p90, black_dn=black_dn, roibound=roibound,
        deg_y=deg_y, deg_x=deg_x, coef_json=coef_json, norm_mean=norm_mean,
        anchor_form=anchor_form, rho=rho, loo_pct=loo_pct,
        analysisref=analysisref, remark=remark)


def read_active() -> Dict[Tuple[str, str], LayerModel]:
    """
    Return every model in force, keyed by (led_color, bayer_layer).

    Includes the usable = 0 rows: "this layer is never worth capturing" is a
    result, and a caller that dropped them could not tell it apart from "no
    analysis has been run yet", which has to be treated very differently — see
    keep_channels().

    Returns:
        {(led_color, bayer_layer): LayerModel}.  Empty when the table has no
        active rows, which is the state of a freshly commissioned instrument.

    Example:
        >>> read_active()[("amber", "R")].w1        # doctest: +SKIP
        0.5477
    """
    rows = db.read_all(
        f"SELECT {', '.join(_COLUMNS)} FROM layermodels WHERE active = 1 "
        "ORDER BY led_color, bayer_layer")
    return {m.key: m for m in (_row_to_model(r) for r in rows)}


def read_model(led_color: str, bayer_layer: str) -> Optional[LayerModel]:
    """
    Return the model in force for one LED colour and layer, or None.

    Args:
        led_color: LEDBank colour name.
        bayer_layer: One of BAYER_LAYERS.

    Returns:
        The LayerModel, or None if no active row exists for that pair.  A
        returned model may still have usable = False — check it, rather than
        reading None as "unusable".
    """
    rows = db.read_all(
        f"SELECT {', '.join(_COLUMNS)} FROM layermodels "
        "WHERE active = 1 AND led_color = ? AND bayer_layer = ?",
        (led_color, bayer_layer))
    return _row_to_model(rows[0]) if rows else None


def keep_channels(led_color: str, exposure_us: int,
                  channels: Sequence[str] = BAYER_LAYERS,
                  ) -> Tuple[str, ...]:
    """
    Return the Bayer layers worth storing for one LED at one exposure.

    Three ways a layer is kept, and one way it is dropped:

      * **No active rows at all** — every layer is kept.  A freshly
        commissioned instrument has not been analysed yet, and discarding
        image data because a table has not been filled in would be the worst
        possible failure mode: it destroys the very captures the analysis
        needs.
      * **No row for this LED and layer** — kept, for the same reason.  A newly
        fitted LED should not go dark because the analysis predates it.
      * **A row with usable = 1** whose reference percentiles, scaled to this
        exposure, sit inside the configured window — kept.
      * **A row with usable = 0**, or one whose scaled p10 is below
        capture.layer_keep_min_dn, or whose scaled p90 is above
        capture.layer_keep_max_dn — dropped.

    Reads the table and both thresholds on every call.  A ten-LED sweep makes
    thirty of these against a local SQLite file while the sensor is busy with
    exposures of tens of milliseconds each, so caching would buy nothing and
    would risk serving a stale answer after an operator edit.

    Args:
        led_color: LEDBank colour name.
        exposure_us: The exposure about to be captured, in microseconds.
        channels: The layers to consider, defaulting to all four.

    Returns:
        The subset of `channels` to store, in the order given.

    Example:
        >>> keep_channels("amber", 58920)           # doctest: +SKIP
        ('R', 'Gr', 'Gb')
    """
    models = read_active()
    if not models:
        return tuple(channels)

    min_dn = config.capture_layer_keep_min_dn()
    max_dn = config.capture_layer_keep_max_dn()

    keep: List[str] = []
    for ch in channels:
        model = models.get((led_color, ch))
        if model is None:
            keep.append(ch)
            continue
        if not model.usable:
            continue
        p10 = model.scaled_p10(exposure_us)
        p90 = model.scaled_p90(exposure_us)
        if p10 is not None and p10 < min_dn:
            continue
        if p90 is not None and p90 > max_dn:
            continue
        keep.append(ch)
    return tuple(keep)


def divisor(led_color: str, bayer_layer: str,
            m_chip1: float, m_chip2: float) -> float:
    """
    Return the illumination divisor at the sample, in DN.

    The runtime form of the whole flat-field analysis.  Divide a measurement's
    dark-subtracted sample mean by this to normalise out the illumination.

    Args:
        led_color: LEDBank colour name.
        bayer_layer: One of BAYER_LAYERS.
        m_chip1: Measured dark-subtracted mean over roi1, in DN.
        m_chip2: Measured dark-subtracted mean over roi2, in DN.

    Returns:
        w1 * m_chip1 + w2 * m_chip2.

    Raises:
        LookupError: No model is in force for that LED and layer.  Asking for
            one that was never fitted is a fault in the caller, not a value to
            be guessed at — use read_model() to test first.
        ValueError: The model exists but has no weights (usable is False).

    Example:
        >>> divisor("amber", "R", 610.0, 598.0)     # doctest: +SKIP
        620.1...
    """
    model = read_model(led_color, bayer_layer)
    if model is None:
        raise LookupError(
            f"no active layermodels row for {led_color}/{bayer_layer}; "
            f"run the flat-field analysis, or check `active` in the table")
    return model.predict(m_chip1, m_chip2)


# ── Writes (deactivate old, insert new — never delete) ──────────────────────

def _now_local() -> str:
    """Local wall-clock timestamp for add_dt, matching exposure_repo's format."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_models(models: Sequence[LayerModel],
                 analysisref: Optional[str] = None,
                 add_dt: Optional[str] = None) -> int:
    """
    Supersede the active rows for each model's (LED, layer) and insert it.

    Deactivation is per (led_color, bayer_layer), the same granularity
    exposure_repo.write_led_exposures() uses, so re-analysing one LED replaces
    only that LED's rows and leaves the rest of the calibration alone.  Prior
    rows are kept with active = 0, never deleted.

    Args:
        models: The rows to put in force.
        analysisref: Stamped on every row that does not carry its own, naming
            the report set that produced it (e.g. 'bound39a').
        add_dt: Shared timestamp for the batch; defaults to now.

    Returns:
        The number of rows inserted.

    Example:
        >>> write_models(models, analysisref="bound39a")    # doctest: +SKIP
        40
    """
    if not models:
        return 0
    stamp = add_dt or _now_local()

    for m in models:
        db.write_one(
            "UPDATE layermodels SET active = 0 "
            "WHERE led_color = ? AND bayer_layer = ? AND active = 1",
            (m.led_color, m.bayer_layer))

    rows = [
        (1, int(bool(m.usable)), stamp, m.led_color, m.bayer_layer,
         int(m.exposure_us), m.roisamp, m.roi1, m.w1, m.roi2, m.w2,
         m.p10, m.p90, m.black_dn, m.roibound, m.deg_y, m.deg_x,
         m.coef_json, m.norm_mean, m.anchor_form, m.rho, m.loo_pct,
         m.analysisref or analysisref, m.remark)
        for m in models
    ]
    db.write_many(
        f"INSERT INTO layermodels ({', '.join(_COLUMNS)}) "
        f"VALUES ({', '.join('?' * len(_COLUMNS))})",
        rows,
    )
    return len(rows)


def delete_led(led_color: str) -> int:
    """
    Hard-delete every layermodels row for one LED — history included.

    Unlike write_models(), which only deactivates, this really removes rows.
    For explicit purges and test cleanup, not the normal flow.

    Args:
        led_color: LEDBank colour name.

    Returns:
        The number of rows removed.
    """
    return db.write_one("DELETE FROM layermodels WHERE led_color = ?",
                        (led_color,))


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
    db.write_one("DELETE FROM layermodels")

    ref_us = 58920
    good = LayerModel(usable=True, led_color="amber", bayer_layer="R",
                      exposure_us=ref_us, roisamp=32, roi1=36, roi2=35,
                      w1=0.5477, w2=0.4783, p10=402.0, p90=790.0,
                      black_dn=64.0, roibound=39, deg_y=1, deg_x=2,
                      norm_mean=610.0, anchor_form="2anchor", rho=1.0260,
                      loo_pct=0.20)
    dark = LayerModel(usable=False, led_color="amber", bayer_layer="B",
                      exposure_us=ref_us, roisamp=32, roi1=36, roi2=35,
                      p10=18.0, p90=31.0, black_dn=64.0, roibound=39)

    print("== empty table keeps everything ==")
    check("no rows -> all four layers",
          keep_channels("amber", ref_us), BAYER_LAYERS)

    print("== write_models + read back ==")
    check("rows written", write_models([good, dark], analysisref="selftest"), 2)
    check("read_active size", len(read_active()), 2)
    check("analysisref stamped",
          read_model("amber", "R").analysisref, "selftest")
    check("usable round-trips true", read_model("amber", "R").usable, True)
    check("usable round-trips false", read_model("amber", "B").usable, False)
    check("weights round-trip", read_model("amber", "R").w1, 0.5477)
    check("unknown pair is None", read_model("amber", "Gr"), None)

    print("== keep_channels: verdict, window and the unknown-pair rule ==")
    check("usable + in window kept",
          keep_channels("amber", ref_us, ("R",)), ("R",))
    check("usable=0 dropped",
          keep_channels("amber", ref_us, ("B",)), ())
    check("unfitted layer kept",
          keep_channels("amber", ref_us, ("Gr", "Gb")), ("Gr", "Gb"))
    check("unfitted LED kept entirely",
          keep_channels("green", ref_us), BAYER_LAYERS)
    # 402 DN at the reference; a quarter of that exposure puts p10 at 100 DN,
    # still over the 96 DN floor, and a fifth puts it at 80 DN, under it.
    check("scaled p10 just over the floor",
          keep_channels("amber", ref_us // 4, ("R",)), ("R",))
    check("scaled p10 under the floor",
          keep_channels("amber", ref_us // 5, ("R",)), ())
    # 790 DN at the reference; 1.1x holds under 863, 1.2x does not.
    check("scaled p90 just under the ceiling",
          keep_channels("amber", int(ref_us * 1.09), ("R",)), ("R",))
    check("scaled p90 over the ceiling",
          keep_channels("amber", int(ref_us * 1.2), ("R",)), ())
    check("order follows the argument",
          keep_channels("amber", ref_us, ("Gb", "Gr", "R")), ("Gb", "Gr", "R"))

    print("== scaling arithmetic ==")
    check("p10 at the reference is p10",
          read_model("amber", "R").scaled_p10(ref_us), 402.0)
    check("p10 at half is half",
          read_model("amber", "R").scaled_p10(ref_us / 2), 201.0)

    print("== divisor ==")
    check("divisor is w1*m1 + w2*m2",
          round(divisor("amber", "R", 610.0, 598.0), 4),
          round(0.5477 * 610.0 + 0.4783 * 598.0, 4))
    try:
        divisor("amber", "B", 100.0, 100.0)
        raise AssertionError("expected ValueError for a model with no weights")
    except ValueError:
        print("  OK  unusable model raises ValueError")
    try:
        divisor("green", "R", 100.0, 100.0)
        raise AssertionError("expected LookupError for an absent model")
    except LookupError:
        print("  OK  absent model raises LookupError")

    print("== re-analysis supersedes, does not delete ==")
    revised = LayerModel(usable=True, led_color="amber", bayer_layer="R",
                         exposure_us=ref_us, w1=0.55, w2=0.47, p10=402.0,
                         p90=790.0)
    write_models([revised], analysisref="rerun")
    check("active row is the new one", read_model("amber", "R").w1, 0.55)
    check("one active row per pair", db.read_val(
        "SELECT COUNT(*) FROM layermodels "
        "WHERE led_color='amber' AND bayer_layer='R' AND active=1"), 1)
    check("history kept", db.read_val(
        "SELECT COUNT(*) FROM layermodels "
        "WHERE led_color='amber' AND bayer_layer='R'"), 2)
    check("other layer untouched", db.read_val(
        "SELECT COUNT(*) FROM layermodels "
        "WHERE led_color='amber' AND bayer_layer='B' AND active=1"), 1)

    print("== delete_led hard-deletes ==")
    check("rows removed", delete_led("amber"), 3)
    check("table empty", len(read_active()), 0)

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("\n== all layermodel_repo self-tests passed ==")
