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
        # The stream rect the surface was fitted in (the model's `roibound`).
        # Needed to place the surface on a capture cropped differently: see
        # gain_at().
        keys = row.keys()
        self.bound = (tuple(int(row[k]) for k in
                            ("bound_x", "bound_y", "bound_w", "bound_h"))
                      if "bound_x" in keys and row["bound_x"] is not None
                      else None)
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

    def gain_at(self, bound, shape) -> np.ndarray:
        """
        The unit-mean gain field on a capture's own crop, NaN outside the fit.

        The surface describes the light on the *stage*, and the stage does
        not move on the sensor when the crop is edited: C1002-C1013 were
        cropped at bound y = 588 and C1014 onward at y = 548, and the gray
        chips sit at the same sensor rows in both (layer row 320).  So the
        surface is placed by sensor position, not by crop position -- a crop
        20 layer rows higher sees the surface 20 rows lower.  `gain()` alone
        assumes the capture was cropped exactly where the model was fitted.

        Pixels outside the fitted domain are NaN rather than extrapolated:
        a cubic in y is not to be trusted past the rows it was fitted on.

        Args:
            bound: The capture's own stream rect (x, y, w, h), e.g.
                `probe.Capture.bound`.
            shape: (rows, cols) of the capture's layer grid.

        Returns:
            (rows, cols) float array, unit mean over the fitted domain.

        Raises:
            ValueError: The model does not record the bound it was fitted in
                and the capture's grid is not the model's own.
        """
        rows_m, cols_m = self.domain
        if self.bound is None:
            if tuple(shape) != (rows_m, cols_m):
                raise ValueError(f"model fitted on {self.domain}, capture "
                                 f"grid {shape}, and no fit bound recorded")
            return self.gain()
        off_y = (int(bound[1]) - self.bound[1]) // 2
        off_x = (int(bound[0]) - self.bound[0]) // 2
        r = np.arange(shape[0], dtype=np.float64) + off_y
        c = np.arange(shape[1], dtype=np.float64) + off_x
        y = (r / (rows_m - 1)) * 2.0 - 1.0
        x = (c / (cols_m - 1)) * 2.0 - 1.0
        xg, yg = np.meshgrid(x, y)
        out = np.zeros(tuple(shape))
        for (iy, ix), co in zip(self.terms, self.coef):
            out += co * (xg ** ix) * (yg ** iy)
        out /= self.norm_mean
        outside = ((r < 0) | (r > rows_m - 1))[:, None] | \
                  ((c < 0) | (c > cols_m - 1))[None, :]
        out[outside] = np.nan
        return out


def load_models(db: Path = DB) -> dict:
    """Return {(led, layer): LayerModel} for every active row."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT l.*, r.x AS bound_x, r.y AS bound_y, r.w AS bound_w, "
            "r.h AS bound_h FROM layermodels l "
            "LEFT JOIN roi r ON r.rowid = l.roibound "
            "WHERE l.active = 1").fetchall()
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
