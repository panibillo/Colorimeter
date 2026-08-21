"""chemistry.py -- reduce the measurement records to per-strip dye quantities.

`measure.py` writes one record per capture x LED x Bayer layer x exposure
rung.  Everything about the chemistry is asked of three derived quantities,
built here so that every report asks them the same way.

    rho   = R_pad / R_paper

          The pad's reflectance divided by that of the bare paper below it on
          the same strip.  Both are already referred to the gray chips through
          the fitted illumination surface, so this is a ratio of ratios: what
          survives is the pad against its own substrate, a few millimetres
          away, imaged in the same frame under the same illuminant.  It is
          free of the gray card, of the lamp, and of anything that changed
          between one session's stage and the next's.

    A     = -log10(rho)                       absorbance against bare paper
    dA    = A - A_blank                       absorbance attributable to dye

`A_blank` is measured, not assumed: a strip is photographed before it meets
the sample, and an unexposed pad is not the same white as the paper it sits
on.  From software 0.7.0 that pre-image lives inside the strip's own archive
(probe.sections()), so a reading can be corrected by *its own* pad rather than
by an average pad -- which is what `blank="own"`, the default, does.  Strips
with no pre-image fall back to the pooled blank; `blank="pooled"` puts every
strip on it, which is what the 2026-08 write-ups did.  Neither mode reproduces
those write-ups number for number, because the pool itself has since gained a
blank -- see blank_source().

The exposure rungs of one cell are averaged, having first been checked against
each other -- a reflectance that depends on exposure is not a reflectance.

Where a capture's own bare paper reached the sensor ceiling, the pooled paper
reflectance of that cell over the whole inventory stands in.  That pooling is
justified by measurement, not convenience: over fifteen captures, fifteen
different strips, two stages and four days, each cell's bare-paper reflectance
holds to about one percent (`report9.py`).
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from samples import NAMES, SAMPLES, PAIRS  # noqa: E402

SP = Path(__file__).resolve().parent


class Reduction:
    """Per (capture, cell) dye quantities, reduced from a record file."""

    def __init__(self, path="records.json", blank="own"):
        """
        Args:
            path: Record file written by `measure.py`, relative to this
                directory or absolute.
            blank: "own" uses each strip's own pre-image as its blank where
                it has one, falling back to the pooled blank where it does
                not.  "pooled" always uses the pooled blank, which is the
                method the 2026-08 write-ups used -- though not their exact
                numbers, since the pool is every pre-image in the inventory
                and that set grows as strips are pre-imaged.
        """
        if blank not in ("own", "pooled"):
            raise ValueError(f"blank must be 'own' or 'pooled', got {blank!r}")
        self.blank_mode = blank
        p = Path(path)
        self.path = p if p.is_absolute() else SP / p
        d = json.loads(self.path.read_text())
        self.erode = d["erode"]
        self.finder = d["finder"]
        self.pads = d["pads"]
        self.records = d["records"]

        ratio, paper, rungs = defaultdict(list), defaultdict(list), defaultdict(list)
        for r in self.records:
            k = (r["capture"], r["led"], r["layer"])
            ratio[k].append(r["ratio"])
            rungs[k].append(r["rung"])
            if r["paper_ratio"] is not None:
                paper[k].append(r["paper_ratio"])

        #: {(capture, led, layer): mean reflectance against the gray card}
        self.R_card = {k: float(np.mean(v)) for k, v in ratio.items()}
        #: Relative spread of that reflectance over the exposure rungs.
        self.rung_spread = {k: (float(np.ptp(v) / np.mean(v)) if len(v) > 1
                                else 0.0) for k, v in ratio.items()}
        self.n_rungs = {k: len(v) for k, v in ratio.items()}

        own = {k: float(np.mean(v)) for k, v in paper.items()}
        pooled = defaultdict(list)
        for (cap, led, layer), v in own.items():
            pooled[(led, layer)].append(v)
        #: {(led, layer): bare-paper reflectance pooled over the inventory}
        self.paper_pooled = {k: float(np.mean(v)) for k, v in pooled.items()}
        self.paper_pooled_cv = {k: float(np.std(v, ddof=1) / np.mean(v))
                                for k, v in pooled.items() if len(v) > 1}
        #: Cells whose own bare paper clipped and so borrow the pooled value.
        self.borrowed = sorted(k for k in self.R_card if k not in own)

        self.R_paper = {k: own.get(k, self.paper_pooled[(k[1], k[2])])
                        for k in self.R_card}
        #: {(capture, led, layer): pad reflectance against its own paper}
        self.rho = {k: self.R_card[k] / self.R_paper[k] for k in self.R_card}

        self.cells = sorted({k[1:] for k in self.R_card})
        #: Cells measured on every exposed capture -- the set safe to compare
        #: across the inventory.  Intersected over the exposed strips only,
        #: NOT over every capture: a pre-image is a two-cell sweep, and
        #: including one here would shrink the comparable set to those two
        #: cells and take every report down with it.
        self.common = [c for c in self.cells
                       if all((n,) + c in self.rho for n in exposed())]

        blanks = [n for n, _ in PAIRS]
        #: {cell: reflectance of an unexposed pad against its own paper},
        #: averaged over every blank that holds the cell.  What a strip with
        #: no pre-image of its own has to fall back on.
        self.rho_blank = {}
        for c in self.cells:
            v = [self.rho[(n,) + c] for n in blanks if (n,) + c in self.rho]
            if v:
                self.rho_blank[c] = float(np.mean(v))

        #: {capture: its own pre-image}, for the strips that were photographed
        #: before they met the sample.  PAIRS points pre -> post; this is the
        #: way a reduction actually asks the question.
        self.blank_of = {post: pre for pre, post in PAIRS}
        self.blank_names = blanks

    # -- the blank ---------------------------------------------------------
    def blank_source(self, capture, cell) -> str:
        """
        Which blank `dA` will use for a capture: "own" or "pooled".

        "own" means the strip was photographed before it met the sample and
        that image holds this cell -- the only blank that is certainly right,
        since a pad's colour before the reaction is a property of that pad and
        not of pads in general.  "pooled" is the average of every blank in the
        inventory, which is what a strip with no pre-image has to make do
        with.
        """
        if self.blank_mode == "own":
            pre = self.blank_of.get(capture)
            if pre is not None and (pre,) + tuple(cell) in self.rho:
                return "own"
        return "pooled"

    def blank_rho(self, capture, cell) -> float:
        """The blank reflectance `dA` removes for this capture and cell."""
        cell = tuple(cell)
        if self.blank_source(capture, cell) == "own":
            return self.rho[(self.blank_of[capture],) + cell]
        return self.rho_blank[cell]

    # -- the quantities reports ask for ------------------------------------
    def A(self, capture, cell) -> float:
        """Absorbance of the pad against the strip's own bare paper."""
        return -np.log10(self.rho[(capture,) + tuple(cell)])

    def dA(self, capture, cell) -> float:
        """
        Absorbance attributable to dye: the blank pad's own value removed.

        Which blank, and why it may not be the one an older write-up used, is
        `blank_source()`.
        """
        return self.A(capture, cell) + np.log10(self.blank_rho(capture, cell))

    def table(self, cells=None, names=None, quantity="rho") -> np.ndarray:
        """
        A capture-by-cell matrix of one quantity.

        Args:
            cells: Cells to include.  Defaults to the common set.
            names: Captures to include.  Defaults to the whole inventory.
            quantity: "rho", "A" or "dA".

        Returns:
            An array of shape (len(names), len(cells)), NaN where a capture
            does not hold that cell.
        """
        cells = cells or self.common
        names = names or NAMES
        f = {"rho": lambda n, c: self.rho[(n,) + c],
             "A": self.A, "dA": self.dA}[quantity]
        out = np.full((len(names), len(cells)), np.nan)
        for i, n in enumerate(names):
            for j, c in enumerate(cells):
                if (n,) + c in self.rho:
                    out[i, j] = f(n, c)
        return out


def cell_name(cell) -> str:
    """'led.layer' for a (led, layer) pair."""
    return f"{cell[0]}.{cell[1]}"


def exposed(names=None) -> list:
    """Captures of strips that were exposed to a sample, in inventory order."""
    return [n for n in (names or NAMES) if SAMPLES[n].role == "post"]


if __name__ == "__main__":
    r = Reduction()
    print(f"{len(r.records)} records, erode {r.erode}, finder {r.finder}")
    print(f"{len(r.cells)} cells, {len(r.common)} common to every exposed strip")
    print(f"{len(r.borrowed)} (capture, cell) borrow the pooled bare-paper "
          f"reflectance because their own clipped")

    print(f"\npooled blank pad, from {', '.join(r.blank_names)}:")
    for c in r.common:
        if c in r.rho_blank:
            print(f"   {cell_name(c):<16} rho_blank = {r.rho_blank[c]:.4f}")

    # Which strips carry their own blank, and what using it costs them. The
    # difference is the whole argument for pre-imaging, so it is printed
    # rather than left to be worked out.
    cell = ("royal_blue", "B")
    print(f"\nstrips with a pre-image of their own, at {cell_name(cell)}:")
    print(f"   {'strip':<10}{'blank':<10}{'dA own':>9}{'dA pooled':>11}"
          f"{'shift':>9}")
    pooled = Reduction(r.path, blank="pooled")
    for post in exposed():
        if r.blank_source(post, cell) != "own":
            continue
        print(f"   {post:<10}{r.blank_of[post]:<10}{r.dA(post, cell):9.4f}"
              f"{pooled.dA(post, cell):11.4f}"
              f"{r.dA(post, cell) - pooled.dA(post, cell):9.4f}")
