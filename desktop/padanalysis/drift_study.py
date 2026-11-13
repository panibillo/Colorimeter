"""
drift_study.py -- does the stain change from one capture to the next?

THE QUESTION
    The repeat post-images of one strip are taken one after another, and
    each capture shines every LED on the pad for a little over 2 s.  Does
    the stain get darker (or lighter) from one capture to the next, and in
    which LED colors?

    It could change: which repeat counts as "the" post-image, how much
    light a capture may give the pad, and the capture order of the LEDs.
    It ends with: a table, per sample and per LED channel, of the change
    in A per capture.  This is a first look with the data on hand; the
    stain-drift study proper waits for Bill's new trials (memory
    project_new_trials_plan).

WORDS AND SYMBOLS
    A           absorbance, -log10 of a reflectance ratio.  Bigger means
                darker.  0.001 A is about 0.23 % of the light.
    R           the post-image's reflectance at a pixel: signal divided by
                the gray-chip level there (`shading_review.frame`).  The
                gray chips take out the exposure time and the lamp level,
                so R can be compared between captures.
    repeat n    the n-th post-image of the strip (n = 0 for the first).
                ASSUMED to be in capture order: C1041, C1041rep2, ...
                rep5.  The files carry no capture time; the samples table
                has only the start and end of the whole series
                (capture_start, capture_end, Unix seconds), so the time per
                capture is their difference over the number of repeats.
    delay       seconds from the end of reagent 2 (samples.reagent2_end)
                to the start of the captures.
    dA_n        how much darker repeat n reads than repeat 0, pixel by
                pixel:  dA_n = -log10(R_n / R_0), with repeat 0 lined up
                onto repeat n's pixels by the two pad fits (bilinear).
                Reported as the median over the pad's interior (8 or more
                layer px from every edge).
    per capture the slope of dA_n against n, by least squares: the change
                in A from one capture to the next.
    dye level   how dark the dye is in that channel, roughly: A of the
                first repeat's interior median R against the interior
                median R of the post-image of C1039 (holder F, 1 ug/L),
                which shows no dye (C1038, manual read 5.02, does show a
                little).  Two different strips, so this is good to about
                0.01 A; it only shows how strong the dye is in each color.

THE PHOTO_RED LIGHT TEST (C1046, 2026-11-11)
    C1046 (100 ug/L, holder H): its standard capture, then 10, 20, 60 and
    180 s of extra photo_red light, each followed by a test-image suite
    (IMG0011-0014, every LED, no pre-image).  `light_test` measures each
    suite against C1046's first post-image, timed by the stamps in the
    files.  First result: photo_red.R darkened +0.017, +0.017, +0.013,
    +0.013 A per step, about the same per suite whether the extra light
    was 10 s or 180 s; violet.B moved under 0.001 A.  So the extra
    photo_red light is not the main cause; the suite itself (some LED in
    it) or time since reagent 2 is.  One strip; a dark-wait control would
    separate the two.  The real photo times also confirm the repeat order
    taken from the file names (C1039, C1041, C1044, C1045).

PARKED (Bill, 2026-11-13)
    The dye-drift study (change with elapsed time or LED light) is
    deferred.  Done so far: the repeat table (drift_repeats) and the C1046
    light test (light_test).  Reopen with a dark-wait control strip
    (capture, about 5 min in the dark, then captures back to back) and,
    if possible, single-LED doses; together they separate time from
    light and find which LED does it.

LIMITS
    Every capture fires every LED, so this cannot tell which LED's light
    causes a change.  It shows which colors the change is seen in.  Nor
    can it tell a stain still developing after exposure to arsine from one
    darkened by the LEDs: both grow with time and with captures.

Run with:  timeout -k 10 600 ../../.venv/bin/python3 -u drift_study.py
"""
import json
import sys
from pathlib import Path

import numpy as np

import analysis_1 as a1
import shading_review as sr
import white_study as ws
from align_study import SETS

HERE = Path(__file__).resolve().parent
OUT = HERE / "drift_out"

#: The strip with no dye seen, used for the rough dye level.
UNDYED = "C1039"
#: Each LED's main color layer(s): where that LED reads brightest.  The two
#: green layers are kept apart (they differ; never average them).
MAIN = [("violet", "B"), ("royal_blue", "B"), ("blue", "B"), ("cyan", "B"),
        ("green", "Gb"), ("green", "Gr"), ("green", "B"), ("amber", "R"),
        ("red_orange", "R"), ("red", "R"), ("photo_red", "R"),
        ("white", "B"), ("white", "Gb"), ("white", "Gr"), ("white", "R")]


def timing(smp, n_files):
    """
    (seconds per capture, delay after reagent 2 in seconds) for a sample,
    from the samples table; NaN where a time is missing.
    """
    import sqlite3
    conn = sqlite3.connect(f"file:{a1.DB}?mode=ro", uri=True)
    row = conn.execute("select reagent2_end, capture_start, capture_end "
                       "from samples where samp_no = ?", (smp,)).fetchone()
    conn.close()
    if row is None or None in row:
        return float("nan"), float("nan")
    r2, c0, c1 = row
    return (c1 - c0) / n_files, float(c0 - r2)


def _usable(f, cell):
    """True if the post-image of `f` has a usable exposure for `cell`."""
    try:
        return a1.rung_verdict(f, cell, "")[0] is not None
    except Exception:
        return False


def _interior_R(f, cell):
    """Median R over the pad's interior in the post-image, or NaN."""
    if not _usable(f, cell):
        return float("nan")
    try:
        fr = sr.frame(f, cell, "")
    except Exception:
        return float("nan")
    zone, _, _ = ws.zones_of(f, "")
    z = zone["interior"] & np.isfinite(fr["R"])
    return float(np.median(fr["R"][z]))


def drift_repeats(sets=SETS):
    """
    The change in A from one capture to the next, per sample and channel.

    How, for each sample with two or more post-images and each channel
    usable in all of them:
        1. R_0 .. R_n: the post-images' reflectance.
        2. For each later repeat n: line R_0 up onto repeat n's pixels and
           take dA_n = median over the interior of -log10(R_n / R_0).
        3. Per capture: least-squares slope of dA_n against n (dA_0 = 0).
        4. Dye level: -log10(interior median R of repeat 0 / that of the
           undyed strip UNDYED).

    Returns:
        list of dicts: holder, sample, files, cell, dA (list, one per
        repeat, the first 0), per_capture_A, dye_A.
    """
    undyed = {}
    out = []
    for hold, samples in sets.items():
        for smp, files in samples.items():
            if len(files) < 2:
                continue
            cells = a1.cells_present(files[0], "")
            for cell in cells:
                if not all(_usable(f, cell) for f in files):
                    continue
                try:
                    R = [np.array(sr.frame(f, cell, "")["R"], float)
                         for f in files]
                except Exception as e:      # e.g. an older gray-chip model
                    print(f"  {smp} {a1.cell_name(cell)}: skipped "
                          f"({type(e).__name__}: {e})")
                    continue
                dA = [0.0]
                for n in range(1, len(files)):
                    R0 = ws._onto(files[0], files[n], R[0], R[n].shape, "")
                    zone, _, _ = ws.zones_of(files[n], "")
                    with np.errstate(invalid="ignore", divide="ignore"):
                        d = -np.log10(R[n] / R0)
                    z = zone["interior"] & np.isfinite(d)
                    dA.append(float(np.median(d[z])))
                n = np.arange(len(files))
                slope = float(np.polyfit(n, dA, 1)[0])
                if cell not in undyed:
                    undyed[cell] = _interior_R(UNDYED, cell)
                zone, _, _ = ws.zones_of(files[0], "")
                z = zone["interior"] & np.isfinite(R[0])
                dye = float(-np.log10(np.median(R[0][z]) / undyed[cell]))
                per, delay = timing(smp, len(files))
                out.append(dict(holder=hold, sample=smp, files=files,
                                cell=a1.cell_name(cell), dA=dA,
                                per_capture_A=slope, dye_A=dye,
                                s_per_capture=per, delay_s=delay))
            a1.close_all()
            print(f"  {smp}: done")
    return out


def summary(rows, cells=MAIN):
    """
    Print the table: for each sample, each LED's main channel(s), the dye
    level and the change per capture (A), and the change per capture as a
    percent of the dye level.
    """
    names = [a1.cell_name(c) for c in cells]
    samples = []
    for r in rows:
        if r["sample"] not in samples:
            samples.append(r["sample"])
    print("Change in A per capture (darker +), and the rough dye level, "
          "interior medians")
    print(f"  {'channel':14s}" + "".join(f"{s:>22s}" for s in samples))
    first = {s: next(x for x in rows if x["sample"] == s) for s in samples}
    print(f"  {'repeats':14s}" + "".join(
        f"{len(first[s]['files']):>22d}" for s in samples))
    print(f"  {'s per capture':14s}" + "".join(
        f"{first[s]['s_per_capture']:>22.0f}" for s in samples))
    print(f"  {'delay, s':14s}" + "".join(
        f"{first[s]['delay_s']:>22.0f}" for s in samples))
    print(f"  {'':14s}" + "".join(f"{'dye A / per capture':>22s}"
                                  for _ in samples))
    for name in names:
        line = f"  {name:14s}"
        for s in samples:
            r = [x for x in rows if x["sample"] == s and x["cell"] == name]
            if r:
                line += f"{r[0]['dye_A']:>11.3f} {r[0]['per_capture_A']:>+9.4f} "
            else:
                line += f"{'-':>22s}"
        print(line)


# ── The photo_red light test, C1046 (Bill, 2026-11-11) ─────────────────
#
# C1046 (100 ug/L, holder H) had its standard capture, then extra photo_red
# light before each of four test-image suites (all LEDs, no pre-image).

#: (file, seconds of extra photo_red light given just before it).
LIGHT_TEST = [("C1046", 0), ("IMG0011", 10), ("IMG0012", 20),
              ("IMG0013", 60), ("IMG0014", 180)]
#: Repeat series shown beside C1046 for comparison (photo_red.R only):
#: about 2 s of each LED per capture and no extra light.
COMPARE = ("C1039", "C1041", "C1044", "C1045")
#: The panels of the light-test figure: LED channels grouped by color.
GROUPS = {"red LEDs (and white.R)": [("amber", "R"), ("red_orange", "R"),
                                      ("red", "R"), ("photo_red", "R"),
                                      ("white", "R")],
          "green LED (and white greens)": [("green", "Gb"), ("green", "Gr"),
                                           ("green", "B"), ("white", "Gb"),
                                           ("white", "Gr")],
          "violet to cyan LEDs (and white.B)": [("violet", "B"),
                                                ("royal_blue", "B"),
                                                ("blue", "B"), ("cyan", "B"),
                                                ("white", "B")]}


def photo_time(f, cell, sec=""):
    """
    When a channel's photo was taken: the middle of the captured_dt stamps
    of that LED's lit rungs, as Unix seconds (local clock, like the samples
    table).

    Args:
        f: file name (captures/ or test_image/).
        cell: (led, layer).
        sec: '' post-image, a1.PRE pre-image.

    Returns:
        float seconds, or NaN if no stamp.
    """
    import datetime as dt
    import h5py
    import probe
    led, layer = cell
    base = f"{sec}/leds/{led}/lit" if sec else f"leds/{led}/lit"
    with h5py.File(probe.archive_path(f), "r") as h:
        if base not in h:
            return float("nan")
        ts = [dt.datetime.strptime(h[f"{base}/{r}/{layer}"].attrs[
                  "captured_dt"].split(".")[0], "%Y-%m-%d %H:%M:%S")
              .timestamp() for r in h[base]]
    return float(np.median(ts))


def reagent2_end(smp):
    """samples.reagent2_end for a sample, Unix seconds."""
    import sqlite3
    conn = sqlite3.connect(f"file:{a1.DB}?mode=ro", uri=True)
    t = conn.execute("select reagent2_end from samples where samp_no = ?",
                     (smp,)).fetchone()[0]
    conn.close()
    return float(t)


def _dA_series(files, cell):
    """
    For a list of post-images of one strip: how much darker each reads
    than the first, as the interior median of -log10(R_n / R_0), with R_0
    lined up onto photo n's pixels by the two pad fits.

    Returns:
        (dA list, first one 0; dye level of the first photo against
        UNDYED, A), or None if a photo lacks a usable exposure.
    """
    if not all(_usable(f, cell) for f in files):
        return None
    R = [np.array(sr.frame(f, cell, "")["R"], float) for f in files]
    dA = [0.0]
    for f, Rn in zip(files[1:], R[1:]):
        R0 = ws._carry(files[0], "", f, "", R[0], Rn.shape)
        zone, _, _ = ws.zones_of(f, "")
        with np.errstate(invalid="ignore", divide="ignore"):
            d = -np.log10(Rn / R0)
        z = zone["interior"] & np.isfinite(d)
        dA.append(float(np.median(d[z])))
    zone, _, _ = ws.zones_of(files[0], "")
    z = zone["interior"] & np.isfinite(R[0])
    dye = float(-np.log10(np.median(R[0][z]) / _interior_R(UNDYED, cell)))
    return dA, dye


def light_test(test=LIGHT_TEST, groups=GROUPS, compare=COMPARE):
    """
    The C1046 photo_red light test, and the repeat series beside it.

    How:
        1. For each channel in `groups`: dA of each test photo against
           C1046's first post-image (`_dA_series`), and the time each was
           taken (`photo_time`), in minutes after reagent 2 ended.
        2. The extra light: each dose is drawn as ending when its photo's
           suite began (the earliest stamp in the file), so its start is
           that time minus the dose.  The exact switch-off time is not
           recorded; the gap between the light and the suite is not known.
        3. For each sample in `compare`: the same for photo_red.R over its
           repeat files (align_study.SETS), with the real photo times, which
           also checks the repeat order taken from the file names.

    Returns:
        dict: t0 (reagent2_end), light (list of [start_min, end_min, s]),
        channels (name -> dict t_min, dA, dye_A), compare (sample -> dict
        files, t_min, dA, dye_A).
    """
    files = [f for f, _ in test]
    smp = files[0]
    t0 = reagent2_end(smp)
    out = dict(sample=smp, t0=t0, files=files, light=[], channels={},
               compare={})
    for f, dose in test[1:]:
        start = min(photo_time(f, c) for c in MAIN)
        out["light"].append([(start - dose - t0) / 60, (start - t0) / 60,
                             dose])
    for cells in groups.values():
        for cell in cells:
            r = _dA_series(files, cell)
            if r is None:
                print(f"  {a1.cell_name(cell)}: not usable in every photo")
                continue
            out["channels"][a1.cell_name(cell)] = dict(
                t_min=[(photo_time(f, cell) - t0) / 60 for f in files],
                dA=r[0], dye_A=r[1])
        a1.close_all()
    cell = ("photo_red", "R")
    for hold, samples in SETS.items():
        for s, fl in samples.items():
            if s not in compare:
                continue
            r = _dA_series(fl, cell)
            if r is None:
                continue
            ts = reagent2_end(s)
            out["compare"][s] = dict(
                files=fl, t_min=[(photo_time(f, cell) - ts) / 60 for f in fl],
                dA=r[0], dye_A=r[1])
            a1.close_all()
    return out


def light_summary(lt):
    """Print the light test as a table: one row per channel."""
    print(f"{lt['sample']}: change in A since its first post-image "
          f"(darker +), interior median")
    print(f"  extra photo_red light before each photo, s: "
          + "  ".join(f"{d:.0f}" for _, _, d in lt["light"]))
    first = next(iter(lt["channels"].values()))
    print(f"  {'channel':14s}{'dye A':>7s}" + "".join(
        f"{t:>9.1f}" for t in first["t_min"]) + "   <- minutes after reagent 2")
    for name, c in lt["channels"].items():
        print(f"  {name:14s}{c['dye_A']:>7.3f}" + "".join(
            f"{a:>+9.4f}" for a in c["dA"]))
    print("\nStep by step (photo n-1 to photo n): change in A, minutes "
          "between, extra photo_red s")
    for name in ("photo_red.R", "red.R", "green.B", "violet.B"):
        c = lt["channels"].get(name)
        if c is None:
            continue
        d, t = np.diff(c["dA"]), np.diff(c["t_min"])
        print(f"  {name:12s}" + "  ".join(
            f"{a:+.4f} in {m:.1f} min, {l[2]:.0f} s"
            for a, m, l in zip(d, t, lt["light"])))
    print("\nphoto_red.R repeat series (no extra light):")
    for s, c in lt["compare"].items():
        order = "in order" if np.all(np.diff(c["t_min"]) > 0) else \
            "NOT in file-name order"
        print(f"  {s} dye {c['dye_A']:.3f}, times {order}: " + "  ".join(
            f"{t:.1f} min {a:+.4f}" for t, a in zip(c["t_min"], c["dA"])))


#: Line colors and markers within a panel, in fixed order (never cycled).
LINE_STYLE = [("#2a6fdb", "o"), ("#e07b00", "s"), ("#1a9e5c", "^"),
              ("#c2185b", "D"), ("#555555", "v")]


def show_light_test(lt, savefigs=True):
    """
    Stacked panels, shared time axis: change in A since C1046's first
    post-image, for each LED group, then photo_red.R beside the repeat
    series of other strips.  Gray bands: the extra photo_red light.

    Saved as fig/drift_C1046_photo_red_light.png, with a plain-language
    .txt beside it.
    """
    import matplotlib.pyplot as plt
    n = len(GROUPS) + 1
    fig, axs = plt.subplots(n, 1, figsize=(7.4, 2.6 * n), sharex=True)
    for ax, (title, cells) in zip(axs, GROUPS.items()):
        for (color, mk), cell in zip(LINE_STYLE, cells):
            name = a1.cell_name(cell)
            c = lt["channels"].get(name)
            if c is None:
                continue
            ax.plot(c["t_min"], c["dA"], color=color, marker=mk, lw=1.5,
                    ms=5, label=f"{name} (dye {_dye(c['dye_A'])})")
        ax.set_title(f"{lt['sample']} post, {title}", fontsize=9)
    ax = axs[-1]
    c = lt["channels"].get("photo_red.R")
    if c:
        ax.plot(c["t_min"], c["dA"], color="black", marker="o", lw=2.2,
                label=f"{lt['sample']}, extra light (dye "
                      f"{_dye(c['dye_A'])})")
    for (color, mk), (s, r) in zip(LINE_STYLE, lt["compare"].items()):
        ax.plot(r["t_min"], r["dA"], color=color, marker=mk, lw=1, ms=4,
                ls="--", label=f"{s} repeats, no extra light "
                               f"(dye {_dye(r['dye_A'])})")
    ax.set_title("photo_red.R post: this strip beside other strips' "
                 "repeat captures", fontsize=9)
    for ax in axs:
        for a, b, d in lt["light"]:
            ax.axvspan(a, b, color="0.85", lw=0)
        ax.axhline(0, color="0.6", lw=0.8)
        ax.set_ylabel("change in A\n(darker +)", fontsize=8)
        ax.legend(fontsize=7, loc="upper left" if ax is not axs[-1]
                  else "lower right")
        ax.grid(alpha=0.3)
    for a, b, d in lt["light"]:
        axs[0].text((a + b) / 2, 1.0, f"{d:.0f} s", ha="center",
                    va="bottom", fontsize=7,
                    transform=axs[0].get_xaxis_transform())
    axs[-1].set_xlabel("minutes after reagent 2 ended")
    fig.suptitle(f"{lt['sample']} (100 ug/L, holder H) post: change in A "
                 "since the first post-image; gray = extra photo_red light",
                 fontsize=10)
    fig.tight_layout()
    if savefigs:
        out = HERE / "fig" / "drift_C1046_photo_red_light.png"
        out.parent.mkdir(exist_ok=True)
        fig.savefig(out, dpi=110)
        out.with_suffix(".txt").write_text(LIGHT_FIG_NOTE)
        print(f"fig saved to {out}")
    return fig


def _dye(a):
    """Dye level for a legend: 'n/a' where the undyed strip had no usable
    exposure in that channel."""
    return "n/a" if not np.isfinite(a) else f"{abs(a) if abs(a) < 0.005 else a:.2f} A"


LIGHT_FIG_NOTE = """\
drift_C1046_photo_red_light.png -- what the axes mean
(made by drift_study.py, show_light_test)

Across (all panels): minutes after reagent 2 ended, from the samples
table (reagent2_end) and the time stamp of each channel's photo in its
file.

Up (all panels): how much darker the pad's interior reads than in the
strip's first post-image, in A (absorbance).  A = -log10(R), where R is
the pixel divided by the gray chips at that pixel.  Up means darker; 0.01
A is about 2.3 % less light.  Each point is the middle value over the
pad's interior (8 or more pixels from every edge), after lining the first
post-image up onto the later photo's pixels.

Gray bands: when the extra photo_red light was on, 10, 20, 60 and 180 s.
Each band is drawn ending when the next photo's suite began; the exact
switch-off time is not recorded.

Top three panels: C1046, one line per LED channel (LED name, then the
camera layer).  "dye" in the legend is how dark the dye is in that channel
in the first post-image, against the undyed strip C1039 (rough, good to
about 0.01 A).

Bottom panel: photo_red.R only.  C1046 (black, solid) beside the repeat captures
of other strips (dashed), which got about 2 s of every LED per capture and
no extra light.  Each strip starts at 0 at its own first post-image.
"""


#: Definitions and units written into an output file as its "about" block,
#: so the file can be read on its own (How_best_to_help_Bill.md 3.2).
_COMMON = {
    "A": "absorbance, -log10 of a reflectance ratio; bigger means darker.  "
         "0.001 A is about 0.23 % of the light.",
    "R": "a photo's reflectance at a pixel: the signal divided by the "
         "gray-chip level at that pixel (shading_review.frame).",
    "cell": "channel: LED name . Bayer layer",
    "interior": "pad pixels 8 or more layer pixels from every edge",
    "dye_A": "rough dye level: -log10 of the first post-image's interior "
             "median R over that of the undyed strip C1039 (holder F).  "
             "Two strips, so good to about 0.01 A.  A.",
}
ABOUT = {
    "repeats": dict(_COMMON, **{
        "what": "Change in the stain from one capture to the next, by "
                "sample and channel.  drift_study.drift_repeats().",
        "files": "the repeat post-images, in capture order",
        "dA": "per repeat n: interior median of -log10(R_n / R_0), the "
              "first post-image lined up onto repeat n.  A, darker +.",
        "per_capture_A": "least-squares slope of dA against n: change per "
                         "capture, A.",
        "s_per_capture": "(samples.capture_end - capture_start) / number "
                         "of files, seconds",
        "delay_s": "samples.capture_start - reagent2_end, seconds",
    }),
    "light_test": dict(_COMMON, **{
        "what": "C1046 photo_red light test: change since C1046's first "
                "post-image in IMG0011-0014, taken after 10, 20, 60, 180 s "
                "of extra photo_red light.  drift_study.light_test().",
        "t0": "samples.reagent2_end of C1046, Unix seconds (local clock)",
        "light": "[start, end, seconds] of each extra photo_red dose, "
                 "minutes after reagent 2 ended.  Each is drawn ending "
                 "when the next suite began; the exact switch-off time is "
                 "not recorded.",
        "t_min": "when each channel's photo was taken (median of its "
                 "rungs' captured_dt), minutes after reagent 2 ended",
        "dA": "interior median of -log10(R_n / R_0), the first post-image "
              "lined up onto photo n.  A, darker +.",
        "compare": "other strips' photo_red.R repeat series, same "
                   "quantities, each from its own reagent 2 end",
    }),
}


def _save(name, obj):
    """
    Write `obj` to drift_out/<name>.json, as {"about": definitions and
    units, "rows": obj} when ABOUT has an entry for `name`.
    """
    OUT.mkdir(exist_ok=True)
    if name in ABOUT:
        obj = {"about": ABOUT[name], "rows": obj}
    (OUT / f"{name}.json").write_text(json.dumps(obj, indent=1))


def _load(name):
    """Read drift_out/<name>.json back (without the about block)."""
    obj = json.loads((OUT / f"{name}.json").read_text())
    if isinstance(obj, dict) and set(obj) == {"about", "rows"}:
        return obj["rows"]
    return obj


def run():
    """
    The study, step by step.  Turn each step on (1) or off (0).

        drift_repeats -> drift_out/repeats.json
        summary       -> terminal
        light_test    -> drift_out/light_test.json  (C1046, photo_red light)
        light_summary, show_light_test -> terminal, fig/
    """
    if 0:   # reads every repeat post-image
        _save("repeats", drift_repeats())
    if 0:
        summary(_load("repeats"))
    if 0:   # C1046 and IMG0011-0014, plus the photo_red.R repeat series
        _save("light_test", light_test())
    if 1:
        lt = _load("light_test")
        light_summary(lt)
        show_light_test(lt)


if __name__ == "__main__":
    sys.exit(run())
