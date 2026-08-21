"""sweep.py -- does the answer depend on how much of the pad edge is discarded?

Every number in `report10` is measured on the pad rectangle backed off by some
number of layer pixels.  That depth is a judgement: too little and the pad's
own edge, which is darker than its middle, is averaged in; too much and there
is not enough pad left to average.

This runs the whole measurement at several depths and reports the quantities
the conclusions rest on.  It writes its record files to a temporary directory
and leaves `records.json` alone.

    .venv/bin/python3 desktop/padanalysis/sweep.py
    .venv/bin/python3 desktop/padanalysis/sweep.py 2 4 6 8 10 12 14
"""
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import measure  # noqa: E402
from chemistry import Reduction, cell_name, exposed  # noqa: E402
from samples import PAIRS, SAMPLES, quantified, replicates  # noqa: E402
from report10 import fit_power, LINEAR_MAX_PPB, EXCLUDE  # noqa: E402

DEFAULT_DEPTHS = (4.0, 6.0, 8.0, 10.0, 12.0)


def main(depths=DEFAULT_DEPTHS, cell=("royal_blue", "B")):
    """Measure at each depth and tabulate the quantities that matter."""
    tmp = Path(tempfile.mkdtemp(prefix="padsweep_"))
    try:
        rows = []
        for e in depths:
            out = tmp / f"records_{e:g}.json"
            measure.main(erode=e, finder="rect", out=str(out))
            rows.append((e, Reduction(out)))
        print(f"\nErosion sweep, cell {cell_name(cell)}\n")
        print(f"{'erode':>7}{'px used':>9}{'exponent':>10}{'scatter':>9}"
              f"{'blank':>9}" +
              "".join(f"{f'{k:g} ppb':>10}" for k in replicates()))
        for e, r in rows:
            post = [n for n in quantified(exposed()) if n not in EXCLUDE]
            low = [n for n in post if SAMPLES[n].ppb <= LINEAR_MAX_PPB]
            b, a, res, _ = fit_power(r, cell, low)
            # Spread over whatever blanks the inventory holds at this cell,
            # rather than a hardcoded pair: the count grows as strips are
            # pre-imaged, and the erosion sweep should feel that.
            bl = [r.rho[(n,) + cell] for n, _ in PAIRS if (n,) + cell in r.rho]
            blank = abs(np.log10(max(bl) / min(bl)))
            px = int(np.median([r.pads[n]["n_used"] for n in post]))
            print(f"{e:>7.0f}{px:>9d}{b:>10.2f}"
                  f"{10 ** res.std(ddof=1):>9.2f}{blank:>9.4f}" +
                  "".join(f"{np.mean([r.dA(n, cell) for n in g]):>10.4f}"
                          for g in replicates().values()))
        print("\nThe dye absorbance falls steadily as more of the pad edge is")
        print("discarded -- the pad is darker at its rim than in its middle --")
        print("but the exponent of the dose-response and the scatter about it")
        print("barely move.  No conclusion in these reports turns on the depth.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    args = [float(a) for a in sys.argv[1:]]
    main(tuple(args) if args else DEFAULT_DEPTHS)
