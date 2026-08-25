"""whitecorr.py -- the gray-chip white correction, re-anchored on a moved pad.

Reads the published `layermodels` rows (read-only) and rebuilds each cell's
fitted illumination surface from its stored coef_json.  Nothing here writes.

The published w1/w2 answer "what is the illumination at roi 32", the seeded
34x34 sample box.  The pad found by segment.py is not that box -- it is about
twice the area and its centroid moves +/-3 layer px between placements -- so
the weights are recomputed for the region actually measured, by the same
formula flatfield_pool.make_anchors() uses:

    g1, gs, g2  = mean of the unit-mean gain field over chip1 / region / chip2
    t           = fractional x-position of the region's centroid between chips
    w1, w2      = (1 - t) * gs / g1 ,  t * gs / g2       ('2anchor')
    divisor     = w1 * mean(chip1 signal) + w2 * mean(chip2 signal)

With the region back at roi 32 this reproduces the published weights, which is
the self-test at the bottom of this file.
"""
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent.parent
DB = REPO / "src" / "colorimeter.sqlite"


class LayerModel:
    """One published (LED, Bayer layer) cell: its gain field and its anchors."""

    def __init__(self, row: sqlite3.Row):
        self.led = row["led_color"]
        self.layer = row["bayer_layer"]
        self.usable = bool(row["usable"])
        self.w1_pub = row["w1"]
        self.w2_pub = row["w2"]
        self.form = row["anchor_form"]
        self.p10 = row["p10"]
        self.p90 = row["p90"]
        self.exposure_us = row["exposure_us"]
        self.remark = row["remark"]
        self._gain = None
        if row["coef_json"]:
            j = json.loads(row["coef_json"])
            self.terms = [tuple(t) for t in j["terms"]]
            self.coef = np.asarray(j["coef"], dtype=np.float64)
            self.domain = (int(j["domain_rows"]), int(j["domain_cols"]))
            self.norm_mean = float(row["norm_mean"])

    def gain(self) -> np.ndarray:
        """The unit-mean gain field over the whole bound crop, (rows, cols)."""
        if self._gain is None:
            rows, cols = self.domain
            y = (np.arange(rows, dtype=np.float64) / (rows - 1)) * 2.0 - 1.0
            x = (np.arange(cols, dtype=np.float64) / (cols - 1)) * 2.0 - 1.0
            xg, yg = np.meshgrid(x, y)
            out = np.zeros((rows, cols))
            for (iy, ix), c in zip(self.terms, self.coef):
                out += c * (xg ** ix) * (yg ** iy)
            self._gain = out / self.norm_mean
        return self._gain


def load_models(db: Path = DB) -> dict:
    """Return {(led, layer): LayerModel} for every active row."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM layermodels WHERE active = 1").fetchall()
    finally:
        conn.close()
    return {(r["led_color"], r["bayer_layer"]): LayerModel(r) for r in rows}


def rect_mask(shape, rect) -> np.ndarray:
    """Boolean mask of an (x, y, w, h) layer rect."""
    x, y, w, h = rect
    m = np.zeros(shape, dtype=bool)
    m[y:y + h, x:x + w] = True
    return m


def anchor_weights(model: LayerModel, region: np.ndarray,
                   chip1: np.ndarray, chip2: np.ndarray) -> tuple:
    """
    Return (w1, w2, t, gs, g1, g2) for an arbitrary measured region.

    Args:
        model: The published cell, for its gain field.
        region: Boolean mask of the pixels being measured.
        chip1, chip2: Boolean masks of the two gray chips.

    Returns:
        The weights, in the form the cell was published under, and the
        quantities behind them.
    """
    g = model.gain()
    g1 = float(g[chip1].mean())
    g2 = float(g[chip2].mean())
    gs = float(g[region].mean())
    xc = lambda m: float(np.nonzero(m)[1].mean())   # noqa: E731
    x1, x2, xs = xc(chip1), xc(chip2), xc(region)
    t = (xs - x1) / (x2 - x1)
    if model.form == "1anchor":
        denom = g1 * g1 + g2 * g2
        return gs * g1 / denom, gs * g2 / denom, t, gs, g1, g2
    return (1.0 - t) * gs / g1, t * gs / g2, t, gs, g1, g2


if __name__ == "__main__":
    # Self-test: at the seeded sample rect the recomputed weights must
    # reproduce the published ones.
    models = load_models()
    shape = (130, 183)
    samp = rect_mask(shape, (72, 26, 34, 34))
    c1 = rect_mask(shape, (10, 26, 34, 34))
    c2 = rect_mask(shape, (144, 25, 34, 34))
    print(f"{'cell':<18}{'w1 pub':>9}{'w1 calc':>9}{'w2 pub':>9}"
          f"{'w2 calc':>9}{'t':>8}   max err")
    worst = 0.0
    for (led, layer), m in sorted(models.items()):
        if not m.usable or m.w1_pub is None:
            continue
        w1, w2, t, *_ = anchor_weights(m, samp, c1, c2)
        e = max(abs(w1 - m.w1_pub) / m.w1_pub, abs(w2 - m.w2_pub) / m.w2_pub)
        worst = max(worst, e)
        print(f"{led + '/' + layer:<18}{m.w1_pub:>9.4f}{w1:>9.4f}"
              f"{m.w2_pub:>9.4f}{w2:>9.4f}{t:>8.4f}   {e * 100:.4f}%")
    print(f"\nworst disagreement with the published weights: {worst * 100:.4f}%")
