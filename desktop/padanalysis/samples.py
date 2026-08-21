"""samples.py -- the strip inventory this analysis runs over.

One row per capture archive in `captures/`.  Everything downstream reads the
nominal concentration, the sample-preparation flags and the stage era from
here, so that adding a strip means editing one table.

Fields
------
ppb
    Nominal arsenic concentration of the prepared standard, in parts per
    billion.  `None` for a strip photographed before it was exposed to the
    sample, which carries no concentration by construction.
splashed
    Whether the strip was shielded from stray droplets during the reaction.
    True  -- unshielded, the preparation under suspicion.
    False -- shielded.
    None  -- not applicable (pre-exposure image), or unknown.
role
    "post" for a strip photographed after the reaction, "pre" for the same
    strip photographed before it.
pair
    For a pre-exposure image, the name of the post-exposure capture of the
    same physical strip.  This is what makes a self-referenced measurement
    possible: the strip is its own blank.

    Pre-image rows are not written by hand.  From software 0.7.0 a strip's
    pre-image lives inside its own archive, under the "/pre" section, so the
    inventory finds them: every hand-written row is checked for a pre-image
    and one row per section found is inserted after it, named "<capture>/pre".
    Adding a strip therefore stays what the HOWTO says it is -- one row -- and
    the inventory cannot drift from what the archives actually hold.  The two
    hand-written pre rows below (C1010-0, C1011-pre) predate sections and are
    separate files; both arrangements read the same way downstream.
stage
    Which stage the capture was made on.  "olive" is the original moulded
    channel, whose floor is a dull olive material; "black" is the same
    channel after the surround was painted flat black on 2026-08-12.
leds
    "full"  -- all ten illuminants stored.
    "no_wv" -- white and violet were switched off.
rungs
    "e0e1e2" -- the three-rung exposure ladder;  "e0" -- a single rung.
"""
from collections import namedtuple

Sample = namedtuple(
    "Sample", "name ppb splashed role pair stage leds rungs remark")

_ROWS = [
    # name        ppb  splashed role   pair       stage    leds     rungs
    ("X15",      10.0,  True,  "post", None,     "olive", "full",  "e0e1e2",
     "expired lot; retained from the 2026-08-10 set"),
    ("C1002",   256.0,  True,  "post", None,     "olive", "full",  "e0e1e2", ""),
    ("C1003",   128.0,  True,  "post", None,     "olive", "full",  "e0e1e2", ""),
    ("C1004",    64.0,  True,  "post", None,     "olive", "full",  "e0e1e2", ""),
    ("C1005",    64.0,  True,  "post", None,     "olive", "full",  "e0e1e2", ""),
    ("C1006",    64.0,  True,  "post", None,     "olive", "full",  "e0",     ""),
    ("C1007",    32.0,  True,  "post", None,     "olive", "full",  "e0",     ""),
    ("C1008",    16.0,  True,  "post", None,     "black", "full",  "e0e1e2", ""),
    ("C1009",    16.0,  True,  "post", None,     "black", "full",  "e0e1e2", ""),
    ("C1010-0",  None,  None,  "pre",  "C1010",  "black", "no_wv", "e0e1e2", ""),
    ("C1010",    16.0,  False, "post", None,     "black", "no_wv", "e0e1e2", ""),
    ("C1011-pre", None, None,  "pre",  "C1011",  "black", "no_wv", "e0e1e2", ""),
    ("C1011",     5.0,  False, "post", None,     "black", "no_wv", "e0e1e2", ""),
    ("C1012",     5.0,  False, "post", None,     "black", "no_wv", "e0e1e2", ""),
    ("C1013",    25.0,  False, "post", None,     "black", "no_wv", "e0e1e2", ""),
    # First strip captured with a pre-image (software 0.7.0).  Its reagent 1
    # timing is not true -- the run was interrupted -- so it carries no
    # concentration that the dose-response may be fitted against.
    ("C1014",    10.0,  False, "post", None,     "black", "full",  "e0e1e2",
     "interrupted run: reagent 1 timing untrue; kept for the pre-image"),
]

def _with_pre_sections(rows) -> list:
    """
    Return `rows` with a row inserted for every archive-held pre-image.

    A missing or unreadable archive yields no extra rows rather than an
    error: the inventory is also read by tools that only want the table (the
    HOWTO's `python3 samples.py`), and on a machine where `captures/` has not
    been synced yet that must still print.

    Args:
        rows: The hand-written `Sample` tuples.

    Returns:
        A new list, each post row followed by its pre-image row if it has one.
    """
    import probe
    out = []
    for row in rows:
        out.append(row)
        sample = Sample(*row)
        if sample.role != "post":
            continue
        try:
            found = [s for s in probe.sections(sample.name) if s]
        except OSError:
            continue
        for section in found:
            out.append((f"{sample.name}/{section}", None, None, section,
                        sample.name, sample.stage, "pre_rb_red", "e0",
                        f"pre-image inside {sample.name}.h5"))
    return out


_ROWS = _with_pre_sections(_ROWS)

SAMPLES = {r[0]: Sample(*r) for r in _ROWS}

#: Capture names in inventory order.
NAMES = [r[0] for r in _ROWS]

#: Strips photographed after the reaction.
EXPOSED = [n for n in NAMES if SAMPLES[n].role == "post"]


def quantified(names=None) -> list:
    """
    Exposed captures whose concentration is known.

    Not the same set as EXPOSED, and the difference matters: a strip can be
    worth keeping without carrying a number the dose-response may be fitted
    against.  C1014 is the case -- its run was interrupted, so its reagent 1
    timing is not true and its nominal concentration would be a fiction, but
    its pre-image is perfectly good.  Anything that regresses against ppb must
    ask for this set rather than EXPOSED.

    Args:
        names: Restrict to these captures.  Defaults to every exposed strip.

    Returns:
        Capture names, in inventory order.
    """
    return [n for n in (names or EXPOSED) if SAMPLES[n].ppb is not None]

#: {name: ppb} for the captures that stored the white illuminant, which the
#: free-form finder of `segment.py` needs.  Used by the comparison reports.
WHITE_LED_SET = {n: SAMPLES[n].ppb for n in NAMES
                 if SAMPLES[n].leds == "full" and SAMPLES[n].ppb is not None}

#: (pre, post) name pairs for the two strips imaged twice.
PAIRS = [(n, SAMPLES[n].pair) for n in NAMES if SAMPLES[n].role == "pre"]

#: Replicate groups: nominal ppb -> the exposed captures prepared at that level.
def replicates(names=None) -> dict:
    """Group exposed captures by nominal concentration.

    Args:
        names: Restrict to these captures.  Defaults to every exposed strip.

    Returns:
        {ppb: [name, ...]} for every level with at least two captures.
    """
    out = {}
    for n in (names or EXPOSED):
        s = SAMPLES[n]
        if s.ppb is not None:
            out.setdefault(s.ppb, []).append(n)
    return {k: v for k, v in sorted(out.items()) if len(v) > 1}


def ppb(name) -> float:
    """Nominal concentration of a capture, or None for a pre-exposure image."""
    return SAMPLES[name].ppb


if __name__ == "__main__":
    print(f"{'name':<11}{'ppb':>7}  {'prep':<9}{'role':<6}{'stage':<7}"
          f"{'leds':<11}{'rungs':<8}remark")
    for n in NAMES:
        s = SAMPLES[n]
        sp = {True: "exposed", False: "shielded", None: "-"}[s.splashed]
        conc = "      -" if s.ppb is None else f"{s.ppb:7.0f}"
        print(f"{n:<11}{conc}  {sp:<9}{s.role:<6}{s.stage:<7}"
              f"{s.leds:<11}{s.rungs:<8}{s.remark}")
    print("\nreplicate groups:")
    for k, v in replicates().items():
        print(f"  {k:6.0f} ppb  {', '.join(v)}")
    print("\npre/post pairs:", ", ".join(f"{a}->{b}" for a, b in PAIRS))
