"""
preexposure_study.py  --  How long can each pre-image exposure_us be without clipping?
-------------------------------------------------------------------------------------
Diversion D1 of docs/work_plan_chain_poc_2026-11-09.md.

Question:  for each LED and Bayer layer, which exposure_us puts the brightest
           non-hot pixel on the pad and the two gray chips at 0.85 x 1023 DN
           (raw, before the dark level is removed)?
Changes:   the pre-image exposure_us stored for each LED (D3), replacing the
           group-1 exposure that clips since C1039.
Ends with: one table, LED by Bayer layer, and the spread between files.

The method, one frame at a time:

  1. Take the pre-image layer as stored (raw DN, 0-1023) and its exposure_us t.
  2. Keep only the pixels that count: the pad (analysis_1.pad_of, not eroded,
     so the rim is included) and the gray1 and gray2 chips, minus the hot
     pixels (analysis_1._hot, the union over the four layers).
  3. M = the largest of those pixels.  b = the sweep's dark level for the
     layer (about 64 DN).  The signal M - b grows in proportion to t (the
     sensor is linear), so the exposure_us that would put the largest pixel
     at the target T = 0.85 x 1023 = 869.6 DN is

         t85 = t x (T - b) / (M - b)

  4. A frame whose largest pixel is at 1023 is clipped: its true M is
     unknown, so it gives no t85.  It is counted, not used.

What the data cannot tell: every file is one strip in one holder; repeats of
the same strip (C1038rep2-5) show photo-to-photo spread, different samples
show strip-to-strip spread, and the two are mixed in the "range" column.

Run:
    .venv/bin/python3 desktop/padanalysis/preexposure_study.py
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

import numpy as np

import analysis_1 as a1
import chain_samples

CEILING_DN = 1023                    # the sensor's largest raw value
TARGET_FRACTION = 0.85               # aim the largest pixel at this x CEILING_DN
TARGET_DN = TARGET_FRACTION * CEILING_DN
LAYERS = ("R", "Gr", "Gb", "B")


def pre_files():
    """
    Every file that holds its own pre-image, from chain_samples.SETS.

    Returns:
        A list of file names, e.g. ['C1038', 'C1038rep2', ...].
    """
    files = [f for holder in chain_samples.sets_with_own_pre().values()
             for group in holder.values() for f in group]
    return [f for f in files if a1.PRE in a1.sections(f)]


def frame_rows(name):
    """
    Measure every stored pre-image layer of one file.

    Args:
        name: File name without .h5, e.g. 'C1039rep3'.

    Returns:
        A list of dicts, one per (LED, layer) stored:
          file, led, layer, t (exposure_us), black (DN), max (raw DN, largest
          non-hot pixel on pad + gray1 + gray2), where ('pad', 'gray1' or
          'gray2': where that pixel is), clipped (count of non-hot pixels at
          1023), t85 (exposure_us for max = TARGET_DN, None when clipped).
    """
    c = a1.open_sample(name, a1.PRE)
    hot = a1._hot(name, a1.PRE)
    regions = {"pad": a1._region(name, a1.ROISAMPLE, a1.PRE, erode=0.0),
               "gray1": a1._region(name, a1.ROIGRAY1, a1.PRE),
               "gray2": a1._region(name, a1.ROIGRAY2, a1.PRE)}
    rows = []
    for led in c.leds:
        for label in c.labels(led):
            for ch in LAYERS:
                got = c.layer(led, label, ch)
                if got is None:
                    continue
                raw = got[1]
                black = c.dark_level(ch, led, label)
                t = c.exposure_us(led, label)
                best, where, clipped = -1.0, None, 0
                for rname, mask in regions.items():
                    px = raw[mask & ~hot]
                    clipped += int((px >= CEILING_DN).sum())
                    if px.size and px.max() > best:
                        best, where = float(px.max()), rname
                t85 = (None if best >= CEILING_DN
                       else t * (TARGET_DN - black) / (best - black))
                rows.append(dict(file=name, led=led, layer=ch, t=t,
                                 black=black, max=best, where=where,
                                 clipped=clipped, t85=t85))
    return rows


def flagged_exposures():
    """{led: exposure_us} flagged for the pre-image in the live database."""
    conn = sqlite3.connect(f"file:{a1.DB}?mode=ro", uri=True)
    try:
        return dict(conn.execute(
            "SELECT LED, microseconds FROM exposure "
            "WHERE active = 1 AND preimage = 1"))
    finally:
        conn.close()


def summary(rows):
    """
    Print the LED x layer table of t85.

    Each cell: median t85 over the files where the layer was not clipped,
    then (lowest-highest, number of files).  'clip k' means k files had the
    layer clipped and gave no estimate.
    """
    by = defaultdict(list)
    clip = defaultdict(int)
    for r in rows:
        key = (r["led"], r["layer"])
        if r["t85"] is None:
            clip[key] += 1
        else:
            by[key].append(r["t85"])
    leds = sorted({r["led"] for r in rows})
    flagged = flagged_exposures()
    print(f"\nexposure_us predicted to put the largest non-hot pixel on the pad "
          f"and gray chips at {TARGET_DN:.1f} DN raw\n"
          "cell = median (lowest-highest, files); 'clip k' = k files clipped, "
          "no estimate; '-' = layer never stored\n")
    print(f"{'LED':11s} {'now flagged':>11s}  " + "  ".join(f"{ch:>26s}" for ch in LAYERS))
    for led in leds:
        cells = []
        for ch in LAYERS:
            v, k = by.get((led, ch), []), clip.get((led, ch), 0)
            if not v and not k:
                cells.append(f"{'-':>26s}")
                continue
            s = (f"{np.median(v):6.0f} ({min(v):.0f}-{max(v):.0f}, {len(v)})"
                 if v else "")
            if k:
                s += f" clip {k}"
            cells.append(f"{s:>26s}")
        print(f"{led:11s} {flagged.get(led, 0):11d}  " + "  ".join(cells))


def where_report(rows):
    """Print where the largest pixel sat, counted over every frame."""
    count = defaultdict(lambda: defaultdict(int))
    for r in rows:
        count[(r["led"], r["layer"])][r["where"]] += 1
    print("\nwhere the largest non-hot pixel sat (frames):")
    for key in sorted(count):
        print(f"  {key[0]}.{key[1]:2s}  " +
              ", ".join(f"{w} {n}" for w, n in sorted(count[key].items())))


def run():
    files = pre_files()
    print("files:", ", ".join(files))
    rows = [r for f in files for r in frame_rows(f)]
    if 1:   # D1: the LED x layer table
        summary(rows)
    if 1:   # where the largest pixel sits
        where_report(rows)
    if 0:   # every frame, for checking one cell by hand
        for r in rows:
            print(r)
    a1.close_all()
    return rows


if __name__ == "__main__":
    run()
