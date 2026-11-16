"""
chain_samples.py -- the one place that says which captures the chain
studies use, and how.

WHY THIS FILE
    Every chain study (align_study, white_study, drift_study, and step 3
    on) is to be re-run on new image sets: captures made for the analysis,
    and later the final device, whose geometry may change a little (Bill,
    2026-11-13).  So the sample list, and any per-sample exception, lives
    here and nowhere else.  A new image set means editing this file and
    re-running each study's `run()`.

WORDS
    sample      a strip and its test, named by its samples.samp_no
                ("C1046").
    file        one capture archive of that strip, captures/<file>.h5.
                Repeats and restarts have their own files ("C1041rep2",
                "C1042-b").  A file's sample is its leading letters and
                digits (`base_sample`).
    pre-image   the strip photographed before the test, the "pre"
                section of a file.
    post-image  the strip photographed after the test, the file's root
                section.
    holder      the cassette holder (samples.cap_style): F, G (G1), H (H1).

WHAT IS HERE
    SETS            holder -> sample -> its files, in capture order.  Only
                    files holding both a pre-image and a post-image.
    PRE_FROM        sample -> the sample whose pre-image its post-images
                    are to be divided by, when not its own.
    PAD_FIT_CELLS   sample -> the channels its pad is located on, when not
                    the usual average of every stored channel; the fit is
                    then moved by POST_OFFSET_STEADY and the offset
                    measured on its paired pre-image (analysis_1.pad_of).
    `concentration` arsenic of a sample, ug/L, from the database.
    `check_sets`    lists, for the samples in SETS, files on disk that hold
                    both images but are not listed, and listed files that
                    lack one.  Run it after adding captures.

GEOMETRY CONSTANTS TO RE-MEASURE ON THE FINAL DEVICE
    They stay in the modules that use them; this table says where.  Each
    was measured on holders F, G and H (2026-11) and may change with the
    final device.  Units: layer pixels (one layer pixel = 2 sensor pixels).

    constant                     value  where           what it is
    WIDTH_FIXED                  41.35  padrect.py      pad width, px
    HEIGHT_FIXED                 53.34  padrect.py      pad height, px
    SIDE_SEARCH_PX               2.0    padrect.py      guided side search
                                                        half-width, px
    WINDOW_END_CLEARANCE         3      padrect.py      rows kept clear of
                                                        the pad's ends
    CAP_LAYOUT                   -      analysis_1.py   holder -> layout
    PAD_RUNG                     e1     analysis_1.py   exposure rung the
                                                        pad is located on
    ERODE                        6.0    analysis_1.py   px trimmed from the
                                                        pad for "sample"
    NBANDS                       8      white_study.py  edge bands modeled
    INTERIOR_PX                  8      white_study.py  interior starts this
                                                        far from every edge
    BAND_CLEAR_PX                8      white_study.py  corner size
    BANDS, CORNER_PX,            3,3,4  align_study.py  edge bands and
    INNER_EDGE_PX                                       corners in step 1

Run directly to print the sets and the check:
    timeout -k 10 120 ../../.venv/bin/python3 -u chain_samples.py
"""
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CAPTURES = REPO / "captures"
DB = REPO / "src" / "colorimeter.sqlite"

#: The files used, by holder and sample, in capture order (confirmed from
#: the photo time stamps, drift_study.photo_time, 2026-11-13).  Only files
#: that hold both a pre-image and a post-image are listed; files with only
#: a pre-image (runs that were restarted) are left out.
#:   F  (cassette 01): C1038-C1042
#:   H  (H1):          C1044, C1046 (100 ug/L), C1047 (400 ug/L)
#:   G  (G1):          C1045
#: C1043 has only pre-images.  C1046's later photos, after long photo_red
#: light, are test_image/IMG0011-0014 (no pre-image), so they are not
#: here.  C1048 is strip C1047 re-exposed to a second 400 ug/L sample (800
#: in all): its own pre-image is the already-stained C1047 strip, so it
#: uses C1047's (PRE_FROM), and its pad is located on photo_red
#: (PAD_FIT_CELLS).
#: C1049 (20 ug/L, a dark dot in the center), C1050 (600 ug/L, brown) and
#: C1051 (12 ug/L, yellow), holder H, 2026-11-15: the first strips with
#: 4 repeat frames per LED at Tus (and 4 at Bus for green), which
#: shading_review.frame averages.  C1049.h5 was renamed after an
#: identifier mix-up: inside, its root samp_no attribute and its Pi
#: samples.h5file still say C1048.  It is not the C1048 strip.
SETS = {
    "F": {"C1038": ["C1038", "C1038rep2", "C1038rep3", "C1038rep4", "C1038rep5"],
          "C1039": ["C1039", "C1039rep2", "C1039rep3", "C1039rep4", "C1039rep5"],
          "C1040": ["C1040", "C1040rep2", "C1040rep3", "C1040rep4", "C1040rep5"],
          "C1041": ["C1041", "C1041rep2", "C1041rep3", "C1041rep4", "C1041rep5"],
          "C1042": ["C1042-b", "C1042rep2-a"]},
    "H": {"C1044": ["C1044-e", "C1044rep2-b"],
          "C1046": ["C1046"],
          "C1047": ["C1047"],
          "C1048": ["C1048"],
          "C1049": ["C1049"],
          "C1050": ["C1050"],
          "C1051": ["C1051"]},
    "G": {"C1045": ["C1045-a", "C1045rep2-a", "C1045rep3", "C1045rep4"]},
}

#: Sample -> the file whose pre-image its post-images are divided by,
#: when not their own (Bill, 2026-11-13: "Use C1047 pre-image with C1048
#: post-image").
PRE_FROM = {"C1048": "C1047"}

#: Sample -> the (LED, Bayer layer) channels its pad is located on, in
#: both sections, when not the usual average of every stored channel.
#: C1048 is stained so dark that the average loses the pad's edges (fits
#: tilted 5-8 deg, edge error 1.2-1.7 px); on photo_red, which the dye
#: barely absorbs, the fits are normal (tilt -0.2/-0.3 deg, 0.28/0.35 px).
#: photo_red alone gives pads 0.3-0.5 px narrower than the average on the
#: pads where both work (C1046, C1047), so it is not used everywhere.
PAD_FIT_CELLS = {"C1048": (("photo_red", "R"),)}

#: For a pad located on PAD_FIT_CELLS: the fit is moved by the offset of
#: the usual all-channel fit from a photo_red-only fit, measured on the
#: pre-image it is paired with (pre_source; for C1048, C1047's pre-image),
#: so it lands where the all-channel fit would (Bill, 2026-11-13).  A
#: post-image gets this steady extra [left, right, top, bottom], layer px:
#: the mean of (post-image offset - pre-image offset) over the 30 F/G/H
#: files is +0.03, -0.09, -0.10, -0.22 (sd 0.09, 0.05, 0.18, 0.07); only
#: the top and bottom term is used (color_study.fit_basis_offsets).
#: Re-measured 2026-11-15, when the window layout's top began to be
#: measured (padrect `_measure_top`); the top term was -0.22 before.
POST_OFFSET_STEADY = (0.0, 0.0, -0.10, -0.22)
#: Switch for the offset rule (Bill approved it 2026-11-13).  First
#: result, C1048 (color_study 3b): the shift widens its pad to 42.24 px
#: against 41.94 for the same strip in C1047, and its right edge band
#: then reads lighter than C1047's own pairing (photo_red.R -0.121 A
#: against -0.074; without the shift -0.070).  Set False to locate C1048
#: on photo_red without the shift; awaiting Bill's choice.
APPLY_FIT_OFFSET = True
#: The pre-image offset used when the paired pre-image cannot be fitted on
#: all channels either: the mean over the 30 files, [left, right, top,
#: bottom], layer px (sd 0.14, 0.07, 0.14, 0.08).  Re-measured 2026-11-15
#: (top measured, not placed): the top term was 0.33 before.
DEFAULT_PRE_OFFSET = (0.10, 0.44, 0.43, 0.33)

#: Studies that need a pre-image and a post-image in one file skip these
#: samples until they handle PRE_FROM.
NEEDS_PRE_FROM = tuple(PRE_FROM)


def base_sample(name):
    """
    The sample a file belongs to: its leading letters and digits
    ("C1045rep3" -> "C1045", "C1042-b" -> "C1042").
    """
    m = re.match(r"[A-Za-z]+\d+", name)
    return m.group(0) if m else name


def pre_source(name):
    """
    The file whose pre-image a file's post-image is divided by: the file
    itself, or PRE_FROM's sample for its sample.
    """
    return PRE_FROM.get(base_sample(name), name)


def pad_fit_cells(name):
    """The channels a file's pad is located on, or None for the usual."""
    return PAD_FIT_CELLS.get(base_sample(name))


def sets_with_own_pre(sets=None):
    """SETS without the samples that use another sample's pre-image."""
    sets = SETS if sets is None else sets
    return {h: {s: f for s, f in v.items() if s not in NEEDS_PRE_FROM}
            for h, v in sets.items()}


def concentration(samp):
    """
    Arsenic of a sample, ug/L: treatments.standard_conc, else
    samples.manual_read (stored as text); None if neither is recorded.
    """
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        r = conn.execute(
            "select t.standard_conc, s.manual_read from samples s left join "
            "treatments t on t.samp_no = s.samp_no where s.samp_no = ?",
            (base_sample(samp),)).fetchone()
    finally:
        conn.close()
    v = None if r is None else (r[0] if r[0] is not None else r[1])
    try:
        return None if v in (None, "") else float(v)
    except ValueError:
        return None


def _sections(path):
    import h5py
    with h5py.File(path, "r") as h:
        return {"post": "leds" in h, "pre": "pre" in h and "leds" in h["pre"]}


def check_sets(sets=None):
    """
    For every sample in `sets`, compare the list with the files on disk.

    Returns:
        list of one-line findings (empty when all is in order): a listed
        file that is missing or lacks a pre-image or post-image (allowed
        for samples in PRE_FROM, which take another file's pre-image), and
        a file on disk holding both that is not listed.
    """
    sets = SETS if sets is None else sets
    out = []
    for hold, samples in sets.items():
        for smp, files in samples.items():
            for f in files:
                p = CAPTURES / f"{f}.h5"
                if not p.exists():
                    out.append(f"{hold} {smp}: {f}.h5 not found")
                    continue
                sec = _sections(p)
                if not sec["post"]:
                    out.append(f"{hold} {smp}: {f} has no post-image")
                if not sec["pre"] and smp not in PRE_FROM:
                    out.append(f"{hold} {smp}: {f} has no pre-image")
            for p in sorted(CAPTURES.glob(f"{smp}*.h5")):
                f = p.stem
                if base_sample(f) != smp or f in files:
                    continue
                sec = _sections(p)
                if sec["post"] and sec["pre"]:
                    out.append(f"{hold} {smp}: {f} holds both images but "
                               f"is not listed")
    return out


if __name__ == "__main__":
    for hold, samples in SETS.items():
        for smp, files in samples.items():
            c = concentration(smp)
            extra = (f", pre-image from {PRE_FROM[smp]}" if smp in PRE_FROM
                     else "")
            extra += (f", pad located on {PAD_FIT_CELLS[smp]}"
                      if smp in PAD_FIT_CELLS else "")
            print(f"{hold} {smp} {'-' if c is None else f'{c:g}':>5} ug/L "
                  f"{len(files)} file(s){extra}")
    found = check_sets()
    print("check_sets: " + ("all in order" if not found else ""))
    for line in found:
        print("  " + line)
    sys.exit(0)
