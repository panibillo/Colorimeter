"""
exposure_rule.py  --  Set each LED's exposure_us from the strip itself.
-----------------------------------------------------------------------
Detour stage 3 of docs/work_plan_chain_poc_2026-11-09.md (rules decided by
Bill, 2026-11-13/14).  This module does the arithmetic; capture_runner takes
the frames and calls it.  It needs only numpy, so it runs on the Pi.

Why: until now the pre-image and the measurement took each LED's exposure_us
from the `exposure` table (an exposure group).  Since C1039 those exposures
clip the pad (1023 DN raw) in violet.B, royal_blue.B, cyan.B, green.Gb and
green.Gr, and nothing looked at the saved frame to notice.  Here every frame
is checked on the very pixels the analysis uses, and the exposure_us is
corrected until it fits.

Words used below
----------------
  raw DN         What the sensor reports for a pixel, 0-1023, dark level
                 included.  All three limits are in raw DN.
  dark level     What the sensor reads with no light: 64.00-64.05 DN in every
                 Bayer layer (C1038-C1047, checked 2026-11-14).  "Signal" is
                 raw DN minus the dark level.
  test region    The pixels a frame must be good on: the fitted pad plus the
                 gray1 and gray2 chips, leaving out hot pixels.  The chips
                 are what the pad is divided by, so they matter as much.
  decision layers  The Bayer layers whose pixels decide the exposure_us of
                 one LED: its analysis channels (ANALYSIS_CHANNELS) when it
                 has any, otherwise all its layers.
  Tus            "Top unclipped" exposure_us: the longest that leaves no
                 test-region pixel of a decision layer at the top limit,
                 aimed so the brightest one sits at 85 % of 1023.
  Bus            "Bottom unclipped" exposure_us: a longer one, taken only
                 when some test-region pixel of an analysis channel is below
                 the noise floor at Tus.  It lifts the darkest such pixel to
                 the floor, with a margin.

How one LED's exposure_us is found (capture_runner does the frames)
-------------------------------------------------------------------
  1. Start from a seed: the strip's own pre-image Tus for a measurement
     (dye only darkens the pad, so it cannot clip there), else the LED's
     seed row in the `exposure` table, else its group-1 exposure.
  2. Take a frame and measure every layer over the test region (measure()).
  3. Judge it on the decision layers (judge()):
       - a pixel at 1023: "clipped" -> next exposure_us x CLIP_STEP_FACTOR;
       - the brightest pixel below 80 % of 1023: "dim" -> scale the signal
         linearly so the brightest pixel lands at 85 % of 1023;
       - otherwise: "accept".
     At most MAX_EXPOSURE_TRIES frames.  The last one is kept whatever it
     shows; if it still clips, the LED is marked failed.
  4. Take multiple_exposure_count frames at Tus in all (the accepted frame
     is the first).  The Pi stores them; it never combines them.
  5. If an analysis channel has a test-region pixel below the noise floor at
     Tus, work out Bus (bus_exposure()) and take bus_exposure_count frames
     at it.  The floor test leaves out side-edge pixels that are mostly
     outside the pad (edge_left_out(), below); the clipping test does not.
  6. Save every analysis channel of every frame, and any other layer only
     when it is unclipped and above the noise floor (keep_layers()); with
     capture.save_all_layers on, save every layer (save_layers()).
  7. Check, pixel by pixel (coverage()), that every test-region pixel of
     every decision layer is below the top limit at Tus and above the floor
     at Tus or at Bus.  The counts that fail are recorded, not acted on.

Settings (detour stage 4a, 2026-11-14)
--------------------------------------
Every number the rule can be tuned by is a row in the `config` table, read
once per sweep into a RuleSettings (RuleSettings.from_config()) and passed
to every function below.  The factory values of those rows
(provisioning/config_defaults.sql) are the module constants of the same
names; RuleSettings() with no arguments holds them, for tests and desktop
studies that have no database at hand.  A test checks the two agree.

Public surface
--------------
    RuleSettings, RuleSettings.from_config(), DEFAULTS, CONFIG_ROWS
    check_edits(role, edits) -> problems an editor save would cause
    LayerStats, TestRegion
    measure(layers, region, s) -> {layer: LayerStats}
    analysis_layers(color, layers, s) / decision_layers(color, layers, s)
    judge(stats, decision, exposure_us, s) -> (verdict, next_us, layer)
    bus_exposure(stats, color, tus_us, s) -> (bus_us or None, layer, min_dn)
    keep_layers(color, stats, s) -> (kept layers, {layer: why})
    save_layers(color, stats, s) -> (saved layers, {layer: why})
    edge_left_out(layer, region, s) -> mask of edge pixels the floor test skips
    coverage(tus_layers, bus_layers, region, decision, s) -> {layer: counts}
  `s` is a RuleSettings; it defaults to DEFAULTS (the factory values).  The
  capture always passes the one it read from the config table.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

# ── Limits, in raw DN (Bill, 2026-11-14) ────────────────────────────────
# The constants named after a config row are that row's factory value (see
# "Settings" above); the capture uses the row.  DARK_DN is not a setting.
#
#   CLIP_TOP_DN     capture.clip_top_dn.  The sensor's ceiling: a pixel at
#                   1023 may have been brighter, so its value is unknown.
#                   Clipped at the top means raw >= CLIP_TOP_DN.
#   CLIP_BOTTOM_DN  capture.clip_bottom_dn.  Dark level + 1: a pixel this
#                   low carries no signal.  Clipped at the bottom means
#                   raw <= CLIP_BOTTOM_DN.  Counted for the record only.
#   NOISE_FLOOR_DN  capture.noise_floor_dn.  Dark level + 100.  Below it the
#                   camera's noise is a large share of the signal; a pixel
#                   of an analysis channel below it (raw < NOISE_FLOOR_DN)
#                   at Tus is what calls for a Bus frame.  Bill may raise it
#                   to dark + 500 (564) later.
#   DARK_DN         The dark level used in this module's arithmetic.  A
#                   constant is enough: the measured level is 64.00-64.05 in
#                   every layer at every exposure_us, and a pre-image's dark
#                   frames are taken before its sweep but a measurement's
#                   only after it.
CLIP_TOP_DN    = 1023
CLIP_BOTTOM_DN = 65
NOISE_FLOOR_DN = 164
DARK_DN        = 64.0

# ── Re-aiming Tus (Bill, 2026-11-13/14) ─────────────────────────────────
#   TOP_TARGET_FRACTION  capture.top_target_fraction.  A re-aimed frame puts
#                        its brightest test-region pixel at this fraction of
#                        CLIP_TOP_DN (0.85 x 1023 = 870 raw DN).  The 15 %
#                        left over covers the frame-to-frame change in LED
#                        brightness (gray chips 0.3-2.6 % between frames).
#   TOP_FLOOR_FRACTION   capture.top_floor_fraction.  A frame whose brightest
#                        pixel is at least this fraction of CLIP_TOP_DN
#                        (0.80 x 1023 = 818 raw DN), and none clipped, is
#                        accepted without re-aiming.
#   CLIP_STEP_FACTOR     capture.clip_step_factor.  After a clipped frame the
#                        exposure_us is multiplied by this.  A clipped frame
#                        doesn't say how far over it is, so a fixed step.
#   MAX_EXPOSURE_TRIES   capture.max_exposure_tries.  Frames taken to find
#                        Tus, counting the first.  Each one keeps the LED on
#                        about 0.6 s longer.
#   DIM_STEP_LIMIT       Largest factor one "dim" re-aim may lengthen the
#                        exposure_us by.  Guards against a frame with almost
#                        no signal (an LED that didn't light) sending the next
#                        one to the cap.  A strip needing more gets it over
#                        two tries.
#   EXPOSURE_MIN_US,     The range any exposure_us is kept within, in µs;
#   EXPOSURE_CAP_US      the cap is capture.exposure_cap_us.  The longest
#                        stored exposure so far is 47 ms (amber, group 5);
#                        200 ms is well clear of it and bounds the LED
#                        on-time if a frame is unexpectedly dark.  C1048's
#                        longest Bus was 69 ms (green.B, 2026-11-14).
TOP_TARGET_FRACTION = 0.85
TOP_FLOOR_FRACTION  = 0.80
CLIP_STEP_FACTOR    = 0.7
MAX_EXPOSURE_TRIES  = 4
DIM_STEP_LIMIT      = 4.0
EXPOSURE_MIN_US     = 100
EXPOSURE_CAP_US     = 200_000

# ── Bus ─────────────────────────────────────────────────────────────────
#   BUS_MARGIN  capture.bus_margin.  Bus is lengthened by this factor
#               beyond what would put the darkest pixel exactly on the
#               floor, so that frame-to-frame noise doesn't leave it just
#               under.
BUS_MARGIN = 1.1

# ── How many frames are kept (Bill, 2026-11-14) ─────────────────────────
#   MULTIPLE_EXPOSURE_COUNT  protocol.multiple_exposure_count.  Frames kept
#                    at Tus, all at the same exposure_us.  3 for the trial
#                    strips (an operator setting on the instrument), so their
#                    archives hold the data for the deferred multiple-
#                    exposure study (work plan, stage 2); the factory value
#                    is 1, for routine use (Bill, 2026-11-14).
#   BUS_EXPOSURE_COUNT  protocol.bus_exposure_count.  Frames kept at
#                    Bus when one is needed.  1 while the exposure rule itself
#                    is being studied ("only the needed Bus frames"); set to
#                    MULTIPLE_EXPOSURE_COUNT for the yellow/brown trials.
MULTIPLE_EXPOSURE_COUNT = 1
BUS_EXPOSURE_COUNT      = 1

# ── What is saved (Bill, 2026-11-16) ────────────────────────────────────
#   SAVE_ALL_LAYERS  capture.save_all_layers.  A development override: when
#                    on, every Bayer layer of every frame the sweep keeps
#                    (Tus and Bus) is saved, dark or clipped, and the
#                    desktop judges it.  Saving is then separate from the
#                    exposure decisions: Tus, Bus and noise_floor_dn work
#                    exactly as with it off.  The frames tried while finding
#                    Tus are still not saved.  Off for routine use, when
#                    keep_layers() decides (save_layers()).
SAVE_ALL_LAYERS = False

# ── Which channels matter ───────────────────────────────────────────────
#   ANALYSIS_CHANNELS  protocol.analysis_channels.  The LED.layer pairs the
#                      yellow/brown analysis reads.  Tus is decided on these
#                      for an LED that has any, and only these can call for
#                      Bus.  green.B is dim (about 160 raw DN at green's Tus),
#                      which is why it is listed: it is the channel most
#                      likely to need Bus.
ANALYSIS_CHANNELS = ("violet.B", "royal_blue.B", "cyan.B", "green.Gb",
                     "green.Gr", "green.B", "photo_red.R")

# ── Seeds for a pre-image ───────────────────────────────────────────────
# Each LED's first exposure_us in a pre-image (its seed) is its seed row in
# the `exposure` table (role 'tus'; exposure_repo.read_seed), read by
# capture_runner._seed_exposure.  The factory rows and where their values
# came from are in provisioning/config_defaults.sql (stage 4c, 2026-11-14).

# ── Edge pixels in the noise-floor test (Bill, 2026-11-14) ───────────────
# The fitted pad's outermost pixel row straddles the cut edge: a pixel there
# can be mostly outside the pad, and then reads far darker than the paper
# it is meant to measure.  Such pixels set off Bus for nothing (on the old
# 1 ug/L pad, every below-floor pixel was one of them).  They stay in the
# clipping test and in the analysis; only the floor test leaves them out.
#
# "Darkening" d is A relative to the pad's interior: d = -log10(signal /
# interior mean signal), bigger = darker.  For one side of the pad:
#   d_1      the darkening of band 1 (the row next in from the edge), from
#            the band's mean signal, as the edge study computes it;
#   rule     a band-0 (outermost row) pixel of that side is left out of the
#            floor test when its own d > EDGE_DARKENING_FACTOR x d_1, and its
#            signal is also below EDGE_SIGNAL_FRACTION of the interior's.
#
#   EDGE_DARKENING_FACTOR  capture.edge_darkening_factor, 5.0.  From the
#            edge study
#            (desktop/padanalysis/white_study.py step 2a,
#            white_out/bands.json: 76 pre-image profiles, F/G/H holders, 7
#            channels): on the left and right sides band 0 is 3.6-4.0 times
#            as dark as band 1 (median), 90 % of profiles below 5.0, highest
#            6.7.  Measured on undyed pads; a dyed edge may be darker.
#   EDGE_RULE_SIDES        Only the left and right sides.  At the top band 0
#            is not darker than band 1 (median d_0 = -0.014: the white paper
#            above lights it), and at the bottom the ratio is about 0.8, so
#            a ratio says nothing there; top and bottom pixels always stay.
#   EDGE_INTERIOR_PX       The interior mean is over pad pixels at least
#            this far (layer px) from every edge (white_study INTERIOR_PX).
#   EDGE_BAND_CLEAR_PX     Band 1's mean leaves out pixels nearer than this
#            to the top or bottom, so the corners don't count
#            (white_study BAND_CLEAR_PX).
#   EDGE_SIGNAL_FRACTION   capture.edge_signal_fraction, 0.5 (Bill,
#            2026-11-14).  The second condition: a pixel at least half
#            outside the pad reads at most about half the interior's signal
#            (d >= 0.30).  Without it the rule was unstable on dim layers:
#            when band 1 is darker than the interior only by noise, d_1 is
#            near 0, the factor limit comes out near the interior mean, and
#            every band-0 pixel darker than average was left out (green.B on
#            C1048, rpizero3 2026-11-14: 42, 0, 0, 0, 39, 36 pixels in frames
#            of one exposure; photo_red.B up to all 104).
EDGE_DARKENING_FACTOR = 5.0
EDGE_SIGNAL_FRACTION  = 0.5
EDGE_RULE_SIDES       = ("L", "R")
EDGE_INTERIOR_PX      = 8
EDGE_BAND_CLEAR_PX    = 8

# Verdicts judge() returns.
ACCEPT  = "accept"
CLIPPED = "clipped"
DIM     = "dim"

# The Bayer layer names an analysis channel may end in.
LAYER_NAMES = ("R", "Gr", "Gb", "B")


def _channel_tuple(channels) -> tuple:
    """
    analysis_channels as a tuple of stripped names.

    Args:
        channels: comma text ('violet.B,cyan.B') or a list or tuple.
    """
    if isinstance(channels, str):
        channels = channels.split(",")
    return tuple(c.strip() for c in channels if c.strip())


@dataclass(frozen=True)
class RuleSettings:
    """
    The tunable numbers of the strip rule, as one sweep uses them.

    Each field is one `config` row (role.name below); the default is that
    row's factory value, the module constant of the same name, where the
    reason for the value is written down.

    Attributes:
        clip_top_dn:     capture.clip_top_dn, raw DN.  Clipped at the top
                         means raw >= this (CLIP_TOP_DN).
        clip_bottom_dn:  capture.clip_bottom_dn, raw DN.  Clipped at the
                         bottom means raw <= this; counted only
                         (CLIP_BOTTOM_DN).
        noise_floor_dn:  capture.noise_floor_dn, raw DN.  An analysis-channel
                         pixel below it at Tus calls for Bus (NOISE_FLOOR_DN).
        top_target_fraction: capture.top_target_fraction.  A re-aim puts the
                         brightest pixel at this fraction of clip_top_dn
                         (TOP_TARGET_FRACTION).
        top_floor_fraction:  capture.top_floor_fraction.  Accepted when the
                         brightest pixel is at least this fraction of
                         clip_top_dn and none clips (TOP_FLOOR_FRACTION).
        clip_step_factor: capture.clip_step_factor.  exposure_us is
                         multiplied by this after a clipped frame
                         (CLIP_STEP_FACTOR).
        max_exposure_tries: capture.max_exposure_tries.  Frames taken to find
                         Tus, the first included (MAX_EXPOSURE_TRIES).
        exposure_cap_us: capture.exposure_cap_us, µs.  Longest exposure_us
                         the rule asks for (EXPOSURE_CAP_US).
        bus_margin:      capture.bus_margin.  Bus is lengthened by this
                         factor (BUS_MARGIN).
        multiple_exposure_count: protocol.multiple_exposure_count.  Frames
                         kept at Tus (MULTIPLE_EXPOSURE_COUNT).
        bus_exposure_count: protocol.bus_exposure_count.  Frames kept at Bus
                         when one is needed (BUS_EXPOSURE_COUNT).
        analysis_channels: protocol.analysis_channels.  'LED.layer' names
                         (ANALYSIS_CHANNELS).
        edge_darkening_factor: capture.edge_darkening_factor.  The edge rule
                         of the floor test (EDGE_DARKENING_FACTOR).
        edge_signal_fraction: capture.edge_signal_fraction.  Its second
                         condition (EDGE_SIGNAL_FRACTION).
        save_all_layers: capture.save_all_layers.  Save every layer of
                         every kept frame (SAVE_ALL_LAYERS; save_layers()).

    Raises:
        ValueError: on construction, when a value makes no sense (see
        _check), naming the config row, so a bad edit is caught before the
        sweep starts rather than halfway through it.
    """
    clip_top_dn: int = CLIP_TOP_DN
    clip_bottom_dn: int = CLIP_BOTTOM_DN
    noise_floor_dn: int = NOISE_FLOOR_DN
    top_target_fraction: float = TOP_TARGET_FRACTION
    top_floor_fraction: float = TOP_FLOOR_FRACTION
    clip_step_factor: float = CLIP_STEP_FACTOR
    max_exposure_tries: int = MAX_EXPOSURE_TRIES
    exposure_cap_us: int = EXPOSURE_CAP_US
    bus_margin: float = BUS_MARGIN
    multiple_exposure_count: int = MULTIPLE_EXPOSURE_COUNT
    bus_exposure_count: int = BUS_EXPOSURE_COUNT
    analysis_channels: tuple = ANALYSIS_CHANNELS
    edge_darkening_factor: float = EDGE_DARKENING_FACTOR
    edge_signal_fraction: float = EDGE_SIGNAL_FRACTION
    save_all_layers: bool = SAVE_ALL_LAYERS

    def __post_init__(self):
        # Frozen: set through object.__setattr__.  A list or comma text is
        # accepted for analysis_channels and kept as a tuple.
        object.__setattr__(self, "analysis_channels",
                           _channel_tuple(self.analysis_channels))
        problems = self._check()
        if problems:
            raise ValueError("exposure rule settings: " + "; ".join(problems))

    def _check(self) -> "list[str]":
        """The values that make no sense, each naming its config row."""
        p = []
        if not (DARK_DN < self.clip_bottom_dn < self.noise_floor_dn
                < self.clip_top_dn):
            p.append("need dark level < capture.clip_bottom_dn < "
                     "capture.noise_floor_dn < capture.clip_top_dn, got "
                     f"{DARK_DN:g} < {self.clip_bottom_dn} < "
                     f"{self.noise_floor_dn} < {self.clip_top_dn}")
        if not 0 < self.top_floor_fraction <= self.top_target_fraction < 1:
            p.append("need 0 < capture.top_floor_fraction <= "
                     "capture.top_target_fraction < 1, got "
                     f"{self.top_floor_fraction} and "
                     f"{self.top_target_fraction}")
        if not 0 < self.clip_step_factor < 1:
            p.append("capture.clip_step_factor must be between 0 and 1, got "
                     f"{self.clip_step_factor}")
        if self.max_exposure_tries < 1:
            p.append("capture.max_exposure_tries must be at least 1")
        if self.exposure_cap_us <= EXPOSURE_MIN_US:
            p.append(f"capture.exposure_cap_us must exceed {EXPOSURE_MIN_US}")
        if self.bus_margin < 1:
            p.append("capture.bus_margin must be at least 1")
        if self.multiple_exposure_count < 1:
            p.append("protocol.multiple_exposure_count must be at least 1")
        if self.bus_exposure_count < 1:
            p.append("protocol.bus_exposure_count must be at least 1")
        if self.edge_darkening_factor <= 0:
            p.append("capture.edge_darkening_factor must be positive")
        if not 0 < self.edge_signal_fraction < 1:
            p.append("capture.edge_signal_fraction must be between 0 and 1, "
                     f"got {self.edge_signal_fraction}")
        bad = [c for c in self.analysis_channels
               if c.count(".") != 1 or c.split(".")[1] not in LAYER_NAMES]
        if bad:
            p.append("protocol.analysis_channels: not LED.layer (layer one "
                     f"of {', '.join(LAYER_NAMES)}): {', '.join(bad)}")
        return p

    @classmethod
    def from_config(cls) -> "RuleSettings":
        """
        The settings in force, from the `config` table (config_repo).

        Each field is read from its row in CONFIG_ROWS, as the row's
        declared type.

        Raises:
            config_repo.ConfigMissing: a row is missing (db_bootstrap seeds
                them all, so only a damaged database).
            ValueError: a value makes no sense (see the class docstring).
        """
        import config_repo as config          # only here: keeps tests and
        #                                       desktop studies DB-free
        return cls(**{f: config.get(role, name)
                      for f, (role, name) in CONFIG_ROWS.items()})

    def to_dict(self) -> dict:
        """Plain values, for the archive's JSON record."""
        d = asdict(self)
        d["analysis_channels"] = list(self.analysis_channels)
        return d


#: The factory values, used when a caller passes no settings.
DEFAULTS = RuleSettings()


#: The `config` row behind each RuleSettings field, as (role, name).  The
#: field and the row share a name; the role is where the row is listed in
#: the config editor.
CONFIG_ROWS = {
    "clip_top_dn":             ("capture",  "clip_top_dn"),
    "clip_bottom_dn":          ("capture",  "clip_bottom_dn"),
    "noise_floor_dn":          ("capture",  "noise_floor_dn"),
    "top_target_fraction":     ("capture",  "top_target_fraction"),
    "top_floor_fraction":      ("capture",  "top_floor_fraction"),
    "clip_step_factor":        ("capture",  "clip_step_factor"),
    "max_exposure_tries":      ("capture",  "max_exposure_tries"),
    "exposure_cap_us":         ("capture",  "exposure_cap_us"),
    "bus_margin":              ("capture",  "bus_margin"),
    "multiple_exposure_count": ("protocol", "multiple_exposure_count"),
    "bus_exposure_count":      ("protocol", "bus_exposure_count"),
    "analysis_channels":       ("protocol", "analysis_channels"),
    "edge_darkening_factor":   ("capture",  "edge_darkening_factor"),
    "edge_signal_fraction":    ("capture",  "edge_signal_fraction"),
    "save_all_layers":         ("capture",  "save_all_layers"),
}


def check_edits(role: str, edits: dict) -> "list[str]":
    """
    What the next capture would refuse if these edits were saved.

    For the config editor (Bill, 2026-11-16): a typo in
    protocol.analysis_channels ('red_R' for 'red.R') was saved without
    complaint and then stopped every pre-image before its first LED.  The
    editor calls this before writing, so the same check that a capture runs
    (RuleSettings._check) refuses the edit on the spot.

    The edited values are put in place of the ones in force and the whole
    set is checked, so rules that join two rows (clip_bottom_dn below
    noise_floor_dn) are checked too.  Only problems naming an edited row
    are returned: a bad value already in force elsewhere does not block an
    unrelated edit, and fixing it is itself an edit to its own row.

    Args:
        role:  The section being edited, e.g. 'protocol'.
        edits: {name: text} as typed, each already a valid value of its
               declared type (the editor checks that first).

    Returns:
        One line per problem, each naming its config row as role.name;
        empty when none, when no edited row belongs to the strip rule, or
        when the settings in force cannot be read (logged; the capture
        will report it).
    """
    import config_repo as config              # only here, as in from_config
    touched = {f for f, (r, n) in CONFIG_ROWS.items()
               if r == role and n in edits}
    if not touched:
        return []
    try:
        values = {f: config.get(r, n) for f, (r, n) in CONFIG_ROWS.items()}
        for f in touched:
            values[f] = config.parse(role, CONFIG_ROWS[f][1],
                                     edits[CONFIG_ROWS[f][1]])
    except (KeyError, ValueError):
        import logging
        logging.getLogger(__name__).exception(
            "check_edits: could not read the strip rule's settings")
        return []
    s = RuleSettings.__new__(RuleSettings)    # unchecked: _check reports
    for f, v in values.items():
        object.__setattr__(s, f, v)
    object.__setattr__(s, "analysis_channels",
                       _channel_tuple(s.analysis_channels))
    named = [f"{r}.{n}" for r, n in (CONFIG_ROWS[f] for f in touched)]
    return [p for p in s._check() if any(n in p for n in named)]


@dataclass
class LayerStats:
    """
    One Bayer layer of one frame, measured over the test region.

    All values are raw DN (dark level included) except the counts.

    Attributes:
        layer:         'R', 'Gr', 'Gb' or 'B'.
        n_pixels:      Test-region pixels measured (hot pixels left out).
        min_dn:        Darkest of them.
        max_dn:        Brightest of them.
        mean_dn:       Their mean.
        max_where:     Which part of the test region the brightest pixel is
                       in: 'pad', 'gray1' or 'gray2'.
        n_clip_top:    Pixels at or above clip_top_dn.
        n_clip_bottom: Pixels at or below clip_bottom_dn.
        n_below_floor: Pixels below noise_floor_dn, not counting the edge
                       pixels the floor test leaves out (edge_left_out).
        floor_min_dn:  Darkest pixel the floor test looks at (the same
                       pixels as n_below_floor).  Bus is worked out from it.
        n_edge_left_out: Side-edge pixels the floor test left out.
    """
    layer: str
    n_pixels: int
    min_dn: float
    max_dn: float
    mean_dn: float
    max_where: str
    n_clip_top: int
    n_clip_bottom: int
    n_below_floor: int
    floor_min_dn: float = float("nan")
    n_edge_left_out: int = 0

    def to_dict(self) -> dict:
        """Plain values, for the archive's JSON record."""
        d = asdict(self)
        for k in ("min_dn", "max_dn", "mean_dn", "floor_min_dn"):
            d[k] = round(float(d[k]), 2)
        return d


@dataclass
class TestRegion:
    """
    The pixels every frame is judged on, in layer px of the bound crop.

    Attributes:
        parts: {'pad': mask, 'gray1': mask, 'gray2': mask}, boolean arrays
               of the crop's layer shape.  'pad' is the fitted pad (not
               eroded), or the sample ROI when no fit passed its checks.
        hot:   Boolean array, True at hot pixels (any Bayer layer), which
               are left out of every part.
        pad_source: 'fit' or 'sample ROI', for the record.
        dist:  {'L', 'R', 'T', 'B': array}, each pixel's distance in from
               each edge of the fitted pad (padfind.PadFit.edge_distances),
               layer px; None when the pad was not fitted, and then the
               edge rule (edge_left_out) is not applied.
    """
    __test__ = False                     # not a unittest class
    parts: dict
    hot: np.ndarray
    pad_source: str = "fit"
    dist: "dict | None" = None
    # The edge rule's pixel groups, built once per region (edge_masks()):
    # they don't change between the frames of a sweep.
    _edge: "dict | None" = field(default=None, repr=False, compare=False)

    def edge_masks(self) -> "dict | None":
        """
        The pixel groups edge_left_out() needs, built on first use.

        Returns:
            None when there is no fitted pad (dist is None); otherwise
            {"interior": mask, "sides": {side: (band0, band1)}}, where band0
            is the pad pixels 0-1 px in from that side and band1 those 1-2
            px in and at least EDGE_BAND_CLEAR_PX from the top and bottom.
        """
        if self.dist is None:
            return None
        if self._edge is None:
            d = self.dist
            pad = self.parts["pad"] & ~self.hot
            near = np.minimum.reduce([d[e] for e in ("L", "R", "T", "B")])
            clear = ((d["T"] >= EDGE_BAND_CLEAR_PX)
                     & (d["B"] >= EDGE_BAND_CLEAR_PX))
            self._edge = {
                "interior": pad & (near >= EDGE_INTERIOR_PX),
                "sides": {side: (pad & (d[side] >= 0) & (d[side] < 1),
                                 pad & clear & (d[side] >= 1) & (d[side] < 2))
                          for side in EDGE_RULE_SIDES}}
        return self._edge

    @property
    def mask(self) -> np.ndarray:
        """All parts together, hot pixels left out."""
        m = np.zeros_like(self.hot)
        for part in self.parts.values():
            m |= part
        return m & ~self.hot

    @property
    def n_hot_inside(self) -> int:
        """Hot pixels that fall inside the parts and so were left out."""
        m = np.zeros_like(self.hot)
        for part in self.parts.values():
            m |= part
        return int((m & self.hot).sum())

    @classmethod
    def from_rects(cls, shape, pad_mask, gray1, gray2, hot=None,
                   pad_source="fit", dist=None) -> "TestRegion":
        """
        Build the region from the pad mask and the two chip rectangles.

        Args:
            shape:    (rows, cols) of the crop's Bayer layers.
            pad_mask: Boolean array of `shape`, True on the pad.
            gray1, gray2: Rect (x, y, w, h) of each chip, layer px.
            hot:      Boolean array of `shape`, True at hot pixels, or None.
            pad_source: Recorded in the archive (see `pad_source`).
            dist:     Edge distances of the fitted pad, or None (see `dist`).
        """
        parts = {"pad": np.asarray(pad_mask, dtype=bool)}
        for name, r in (("gray1", gray1), ("gray2", gray2)):
            m = np.zeros(shape, dtype=bool)
            m[max(r.y, 0):r.y + r.h, max(r.x, 0):r.x + r.w] = True
            parts[name] = m
        if hot is None:
            hot = np.zeros(shape, dtype=bool)
        return cls(parts=parts, hot=np.asarray(hot, dtype=bool),
                   pad_source=pad_source, dist=dist)


def edge_left_out(layer, region: TestRegion,
                  s: RuleSettings = DEFAULTS) -> np.ndarray:
    """
    The side-edge pixels of one layer that the noise-floor test leaves out.

    For each side in EDGE_RULE_SIDES:
      1. signal = raw DN - DARK_DN; S = its mean over the pad's interior
         (EDGE_INTERIOR_PX or more from every edge, hot pixels left out).
      2. d_1 = -log10(mean signal of band 1 / S), band 1 being the pad
         pixels 1 to 2 px in from that side, away from the corners.
      3. A band-0 pixel (0 to 1 px in) is left out when both
         -log10(its signal / S) > s.edge_darkening_factor x d_1, and
         its signal < s.edge_signal_fraction x S.
    A side whose d_1 is not positive (no darkening to compare with) leaves
    nothing out.

    Example: S = 700, band 1 mean 560 -> d_1 = 0.097; the limit is 0.485,
    i.e. signal 229 (the fraction's 350 is higher, so 229 holds).  An edge
    pixel at raw 144 (signal 80, d = 0.94) is left out; one at raw 400
    (signal 336, d = 0.32) stays.  With band 1 at 690 (d_1 = 0.006, noise)
    the factor alone would allow 653; the fraction holds it to 350.

    Args:
        layer:  One Bayer layer of the bound crop, raw DN.
        region: The test region; nothing is left out when region.dist is
                None (the pad was not fitted).
        s:      The settings (edge_darkening_factor, edge_signal_fraction).

    Returns:
        Boolean array of the layer's shape, True at the pixels left out.
    """
    a = np.asarray(layer)
    out = np.zeros(a.shape, dtype=bool)
    groups = region.edge_masks()
    if groups is None or not groups["interior"].any():
        return out
    S = float(a[groups["interior"]].mean()) - DARK_DN
    if S <= 0:
        return out
    for band0, band1 in groups["sides"].values():
        if not band1.any():
            continue
        mean1 = float(a[band1].mean()) - DARK_DN
        if mean1 <= 0:
            continue
        d1 = -np.log10(mean1 / S)
        if d1 <= 0:
            continue
        # d > factor x d1  <=>  signal < S x 10^(-factor x d1): the same
        # test without a logarithm at every pixel (and a signal at or below
        # zero is always left out).
        limit = min(S * 10.0 ** (-s.edge_darkening_factor * d1),
                    S * s.edge_signal_fraction)
        out |= band0 & ((a - DARK_DN) < limit)
    return out


def measure(layers: dict, region: TestRegion,
            s: RuleSettings = DEFAULTS) -> "dict[str, LayerStats]":
    """
    Measure each Bayer layer of one frame over the test region.

    Args:
        layers: {layer: array} of the bound crop, raw DN.
        region: The test region (its masks have the layers' shape).
        s:      The settings (the three limits, the edge factor).

    Returns:
        {layer: LayerStats}.  A layer with no test-region pixel at all is
        left out (it can't happen with a valid ROI table).
    """
    out = {}
    keep = {name: part & ~region.hot for name, part in region.parts.items()}
    every = region.mask
    for name, arr in layers.items():
        vals = np.asarray(arr)[every]
        if vals.size == 0:
            continue
        brightest = float(vals.max())
        where = next((part for part, m in keep.items()
                      if m.any() and float(np.asarray(arr)[m].max())
                      == brightest), "pad")
        skipped = edge_left_out(arr, region, s) & every
        floor_vals = np.asarray(arr)[every & ~skipped]
        out[name] = LayerStats(
            layer=name, n_pixels=int(vals.size),
            min_dn=float(vals.min()), max_dn=brightest,
            mean_dn=float(vals.mean()), max_where=where,
            n_clip_top=int((vals >= s.clip_top_dn).sum()),
            n_clip_bottom=int((vals <= s.clip_bottom_dn).sum()),
            n_below_floor=int((floor_vals < s.noise_floor_dn).sum()),
            floor_min_dn=(float(floor_vals.min()) if floor_vals.size
                          else float("nan")),
            n_edge_left_out=int(skipped.sum()))
    return out


def analysis_layers(color: str, layers,
                    s: RuleSettings = DEFAULTS) -> "list[str]":
    """The layers of `color` named in s.analysis_channels, in layer order."""
    return [ch for ch in layers if f"{color}.{ch}" in s.analysis_channels]


def decision_layers(color: str, layers,
                    s: RuleSettings = DEFAULTS) -> "list[str]":
    """
    The layers whose test-region pixels decide this LED's Tus.

    Its analysis channels when it has any (photo_red: R only; green: Gb, Gr
    and B).  An LED with none (amber, red, ...) is judged on every layer, so
    nothing it stores is clipped.

    Args:
        color:  LED color name.
        layers: The layer names a frame has (e.g. dict keys).
        s:      The settings (analysis_channels).
    """
    named = analysis_layers(color, layers, s)
    return named if named else list(layers)


def _clamp_us(us: float, s: RuleSettings) -> int:
    """Round to whole µs and keep within EXPOSURE_MIN_US..s.exposure_cap_us."""
    return int(min(max(round(us), EXPOSURE_MIN_US), s.exposure_cap_us))


def judge(stats: dict, decision, exposure_us: int,
          s: RuleSettings = DEFAULTS) -> "tuple[str, int, str]":
    """
    Decide whether one frame's exposure_us is Tus, and if not, the next one.

    Args:
        stats:       measure()'s result for the frame.
        decision:    The decision layers (decision_layers()).
        exposure_us: The exposure_us the frame was taken at (as applied).
        s:           The settings.

    Returns:
        (verdict, next_us, layer):
          verdict  ACCEPT, CLIPPED or DIM (see the module docstring).
          next_us  The exposure_us to try next: `exposure_us` itself for
                   ACCEPT, and also when the cap or floor stops any change.
          layer    The layer that decided it: the first clipped one, or the
                   brightest.

    Example (dark level 64, target 870):
        brightest 500 raw at 10000 µs -> DIM, next 10000 x 805.6 / 436 = 18476.
    """
    present = [ch for ch in decision if ch in stats]
    clipped = [ch for ch in present if stats[ch].n_clip_top > 0]
    if clipped:
        return (CLIPPED, _clamp_us(exposure_us * s.clip_step_factor, s),
                clipped[0])
    top = max(present, key=lambda ch: stats[ch].max_dn)
    brightest = stats[top].max_dn
    if brightest >= s.top_floor_fraction * s.clip_top_dn:
        return ACCEPT, int(exposure_us), top
    signal = max(brightest - DARK_DN, 1.0)
    target = s.top_target_fraction * s.clip_top_dn - DARK_DN
    factor = min(target / signal, DIM_STEP_LIMIT)
    return DIM, _clamp_us(exposure_us * factor, s), top


def bus_exposure(stats: dict, color: str, tus_us: int,
                 s: RuleSettings = DEFAULTS
                 ) -> "tuple[int | None, str, float]":
    """
    The Bus exposure_us, when an analysis channel needs one.

    Bus = Tus x (noise_floor_dn - dark) / (darkest raw - dark) x bus_margin,
    the darkest raw being the lowest pixel the floor test looks at
    (LayerStats.floor_min_dn: the test region less the edge pixels it
    leaves out) among this LED's analysis channels that have any such pixel
    below the floor at Tus.  Both are signals, so the dark level comes off
    before dividing.

    Args:
        stats:  measure()'s result for the accepted Tus frame.
        color:  LED color name.
        tus_us: Tus, as applied.
        s:      The settings.

    Returns:
        (bus_us, layer, min_dn).  bus_us is None when no analysis channel of
        this LED has a pixel below the floor (no Bus needed); layer and
        min_dn are then '' and NaN.

    Example (dark 64): darkest green.B pixel 120 raw at Tus 8760 µs ->
        8760 x 100 / 56 x 1.1 = 17207 µs.
    """
    short = [ch for ch in analysis_layers(color, stats, s)
             if stats[ch].n_below_floor > 0]
    if not short:
        return None, "", float("nan")
    layer = min(short, key=lambda ch: stats[ch].floor_min_dn)
    darkest = stats[layer].floor_min_dn
    signal = max(darkest - DARK_DN, 1.0)
    bus = tus_us * (s.noise_floor_dn - DARK_DN) / signal * s.bus_margin
    return _clamp_us(max(bus, tus_us + 1), s), layer, darkest


def keep_layers(color: str, stats: dict,
                s: RuleSettings = DEFAULTS) -> "tuple[list[str], dict]":
    """
    Which Bayer layers of one frame are saved (replaces the layermodel
    screen, layermodel_repo.keep_channels, for the strip rule).

    Rule (Bill, 2026-11-14):
      - an analysis channel is always saved, even where it clips: in a Bus
        frame the bright pixels clip by design, and Tus covers them;
      - any other layer is saved only when no test-region pixel is at
        clip_top_dn and its brightest test-region pixel is at or above
        noise_floor_dn (so it has signal worth keeping).

    Args:
        color: LED color name.
        stats: measure()'s result for the frame.
        s:     The settings.

    Returns:
        (kept layer names, {layer: why}), why one of 'analysis channel',
        'unclipped', 'clipped', 'below floor'.
    """
    kept, why = [], {}
    for ch, st in stats.items():
        if f"{color}.{ch}" in s.analysis_channels:
            why[ch] = "analysis channel"
        elif st.n_clip_top > 0:
            why[ch] = "clipped"
        elif st.max_dn < s.noise_floor_dn:
            why[ch] = "below floor"
        else:
            why[ch] = "unclipped"
        if why[ch] in ("analysis channel", "unclipped"):
            kept.append(ch)
    return kept, why


def save_layers(color: str, stats: dict,
                s: RuleSettings = DEFAULTS) -> "tuple[list[str], dict]":
    """
    Which Bayer layers of one kept frame reach the archive.

    With s.save_all_layers off: exactly keep_layers().  With it on (a
    development override, Bill 2026-11-16): every layer, whatever
    keep_layers() says; a layer it would have left out is marked
    "<its reason>, saved (save_all_layers)" so the record still shows it is
    clipped or below the floor.  keep_layers() itself is unchanged, and the
    coverage check still uses it.

    Args:
        color: LED color name.
        stats: measure()'s result for the frame.
        s:     The settings.

    Returns:
        (saved layer names, {layer: why}).
    """
    kept, why = keep_layers(color, stats, s)
    if not s.save_all_layers:
        return kept, why
    for ch in stats:
        if ch not in kept:
            why[ch] = f"{why[ch]}, saved (save_all_layers)"
    return list(stats), why


def coverage(tus_layers: dict, bus_layers: "dict | None",
             region: TestRegion, decision,
             s: RuleSettings = DEFAULTS) -> "dict[str, dict]":
    """
    Check every test-region pixel is usable at Tus or at Bus.

    A pixel is usable at the top when it is below clip_top_dn at Tus; at the
    bottom when it is at or above noise_floor_dn at Tus, or at Bus and below
    clip_top_dn there.  The bottom check skips the edge pixels the floor
    test leaves out at Tus (edge_left_out).

    Args:
        tus_layers: {layer: array} of the accepted Tus frame, raw DN.
        bus_layers: The same for the Bus frame, or None if none was taken.
        region:     The test region.
        decision:   The layers to check.  The capture passes the decision
                    layers it stores at Tus: a layer that is not stored
                    (red.B under a red LED, always dark) is not checked.
        s:          The settings.

    Returns:
        {layer: {"n_pixels", "n_clip_top", "n_below_floor_both"}}: pixels
        clipped at Tus, and pixels below the floor at Tus that Bus did not
        rescue (all of them below the floor when there is no Bus frame).
    """
    every = region.mask
    out = {}
    for ch in decision:
        if ch not in tus_layers:
            continue
        t = np.asarray(tus_layers[ch])[every]
        skipped = edge_left_out(tus_layers[ch], region, s)[every]
        low = (t < s.noise_floor_dn) & ~skipped
        if bus_layers is not None and ch in bus_layers:
            b = np.asarray(bus_layers[ch])[every]
            low &= ~((b >= s.noise_floor_dn) & (b < s.clip_top_dn))
        out[ch] = {"n_pixels": int(t.size),
                   "n_clip_top": int((t >= s.clip_top_dn).sum()),
                   "n_below_floor_both": int(low.sum())}
    return out
