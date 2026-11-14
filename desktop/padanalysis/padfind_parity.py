"""
padfind_parity.py  --  Does the Pi pad finder (src/padfind.py) match the desktop?
---------------------------------------------------------------------------------
Detour stage 1 of docs/work_plan_chain_poc_2026-11-09.md.

Question:  does `src/padfind.py`, given only the photo_red R layer, put the pad
           where the desktop does?
Changes:   whether the Pi can find the pad itself before any saved photo.
Ends with: two tables over the F/G/H files that hold their own pre-image.

Two comparisons, for every pre-image and post-image:

  A. Port check.  padfind's fit *before* its offset against the desktop's
     photo_red-only fit (analysis_1._cells_fit with photo_red R).  The
     arithmetic is the same, so every difference should be rounding error
     (under 0.001 px).
  B. Offset check.  padfind's fit *after* its offset against the desktop's
     usual fit from every stored LED and layer (analysis_1.pad_of), the one
     the analysis chain uses.  Target (plan): mean difference within
     +/- 0.1 px and standard deviation at most 0.15 px, for each edge.

Edges are compared as the desktop's color_study.fit_basis_offsets does: the
left and right sides at the pad's middle row, and the top and bottom rows,
all in layer px of the bound crop (+ = right or down).

The photo used is the same one the desktop fits on: rung e1 of photo_red
where the sweep has it, otherwise its only rung (fields.rung_for).  On the
Pi, pad finding gets a frame of its own.

Run:
    .venv/bin/python3 desktop/padanalysis/padfind_parity.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

import analysis_1 as a1
import chain_samples
import fields

sys.path.insert(0, str(a1.REPO / "src"))
import padfind                                        # noqa: E402
from rect import Rect                                 # noqa: E402

OUT = a1.SP / "padfind_out"
CELL = (("photo_red", "R"),)
EDGES = ("left", "right", "top", "bottom")


def files():
    """Every file in chain_samples.SETS that holds its own pre-image."""
    names = [f for holder in chain_samples.sets_with_own_pre().values()
             for group in holder.values() for f in group]
    return [f for f in names if a1.PRE in a1.sections(f)]


def edges(p):
    """
    (left, right, top, bottom) of a pad fit, layer px; the sides at the
    pad's middle row.  Works for padfind.PadFit and desktop PadRect alike.
    """
    y = 0.5 * (p.top + p.bot)
    return np.array([p.aL + p.m * (y - p.yc), p.aR + p.m * (y - p.yc),
                     p.top, p.bot])


def pi_inputs(name, sec):
    """
    What src/padfind receives on the Pi, read from the archive instead.

    Returns:
        (layer, dark_dn, rects): the photo_red R layer (raw DN) of the rung
        the desktop fits on, its dark level, and the sample/gray1/gray2
        rectangles as src Rects.
    """
    c = a1.open_sample(name, sec)
    label = fields.rung_for(c, "photo_red", a1.PAD_RUNG)
    layer = c.layer("photo_red", label, "R")[1]
    dark = c.dark_level("R", "photo_red", label)
    rects = {k: Rect(*c.rects[k], name=k) for k in ("sample", "gray1",
                                                     "gray2")}
    return layer, dark, rects


def compare():
    """
    Run both comparisons on every file.

    Returns:
        A list of dicts: file, section, port (A, 4 differences), offset
        (B, 4 differences), qc (padfind's QC message), error (if padfind
        failed).
    """
    rows = []
    for name in files():
        pre_before = None
        for sec, pf_sec in (("pre", padfind.SECTION_PRE),
                            ("", padfind.SECTION_POST)):
            if sec == "" and "" not in a1.sections(name):
                continue
            row = {"file": name, "section": pf_sec}
            try:
                layer, dark, rects = pi_inputs(name, sec)
                before, after = padfind.find_pad(
                    layer, dark, rects, pf_sec,
                    prior=pre_before if pf_sec == "post" else None)
            except padfind.PadFindError as exc:
                row["error"] = str(exc)
                rows.append(row)
                continue
            if pf_sec == "pre":
                pre_before = before
            desk_red = a1._cells_fit(name, sec, CELL)
            desk_all = a1.pad_of(name, sec)
            row["port"] = (edges(before) - edges(desk_red)).tolist()
            row["offset"] = (edges(after) - edges(desk_all)).tolist()
            row["qc"] = after.qc()
            row["describe"] = after.describe()
            rows.append(row)
    return rows


def report(rows):
    """Print both tables and the summary lines."""
    bad = [r for r in rows if "error" in r]
    ok = [r for r in rows if "error" not in r]
    print(f"\n{len(ok)} fits, {len(bad)} failures")
    for r in bad:
        print(f"  FAILED {r['file']} {r['section']}: {r['error']}")

    port = np.array([r["port"] for r in ok])
    print("\nA. Port check: padfind before offset minus desktop photo_red fit")
    print("   largest |difference|, layer px:  " + "  ".join(
        f"{e} {v:.2e}" for e, v in zip(EDGES, np.abs(port).max(axis=0))))

    print("\nB. Offset check: padfind after offset minus desktop all-channel "
          "fit, layer px")
    print(f"   {'section':8s}" + "".join(f"{e:>20s}" for e in EDGES))
    for sec in ("pre", "post"):
        d = np.array([r["offset"] for r in ok if r["section"] == sec])
        if not len(d):
            continue
        cells = [f"{m:+.3f} sd {s:.3f}" for m, s in
                 zip(d.mean(axis=0), d.std(axis=0, ddof=1))]
        print(f"   {sec:8s}" + "".join(f"{c:>20s}" for c in cells)
              + f"   n={len(d)}")
        worst = np.abs(d).max(axis=0)
        print(f"   {'worst':8s}" + "".join(f"{v:>20.3f}" for v in worst))

    flagged = [r for r in ok if r["qc"]]
    print(f"\nQC flags: {len(flagged)}")
    for r in flagged:
        print(f"  {r['file']} {r['section']}: {r['qc']}")


def run():
    rows = compare()
    if 1:   # both tables
        report(rows)
    if 1:   # keep the numbers
        OUT.mkdir(exist_ok=True)
        about = {
            "about": "padfind_parity.py: src/padfind.py against the desktop "
                     "pad finder. port = padfind before offset minus "
                     "analysis_1._cells_fit(photo_red R); offset = padfind "
                     "after offset minus analysis_1.pad_of (all channels). "
                     "Each is [left, right, top, bottom] in layer px of the "
                     "bound crop, sides at the pad's middle row, + = right "
                     "or down.",
            "rows": rows}
        path = OUT / "parity.json"
        path.write_text(json.dumps(about, indent=1))
        print(f"\nwrote {path}")
    a1.close_all()
    return rows


if __name__ == "__main__":
    run()
