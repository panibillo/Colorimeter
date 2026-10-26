"""
exposure_fit.py  —  Find exposure brackets that are tested, not assumed.
-----------------------------------------------------------------------
Exposure calibration used to find one exposure per LED and multiply it by fixed
factors to get the brackets.  Nothing checked that the brackets were usable.
This module finds the brackets by testing them.

The idea, in plain terms.  The sensor is linear: twice the exposure gives twice
the signal.  So one measurement of the test pad (white, yellow and brown
patches) and the gray chips, at any exposure, tells us:

  * the longest exposure at which the brightest patch still stays under the
    upper limit, and
  * the shortest exposure at which the darkest patch still stays over the
    lower limit.

Every exposure between those two is usable on every patch.  That is the usable
range.  A group of N brackets is spread across it, as widely as the allowed
spacing permits, so the shortest bracket suits the white patch and the longest
suits the brown one.  Then each bracket is measured for real, and nudged until
it passes, because a real sensor is only roughly linear.

Nothing here touches the camera.  The caller supplies a `measure` function:

    measure(exposure_us) -> {patch_name: (low, high)}

where `low` and `high` are the patch's low and high percentile signal (DN,
black level removed) at that exposure, in the LED's brightest Bayer layer.

Calibrating one LED or all of them uses the same code:

    fit_led(led, measure, seed_us, params)          one LED
    fit_leds(measure_for, seeds, params, only=...)  all LEDs, or just `only`

Public surface
--------------
    FitParams(...)  / FitParams.from_config()
    quantize_us(us, params) -> int
    check_levels(levels, params) -> (too_dark, too_bright)
    usable_range(levels, exposure_us, params) -> UsableRange
    fit_led(led, measure, seed_us, params) -> LedFit
    fit_leds(measure_for, seeds, params, only=None, progress=None) -> dict
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# {patch name: (low percentile signal, high percentile signal)} in DN.
Levels = Dict[str, Tuple[float, float]]
Measure = Callable[[int], Levels]

MIN_EXPOSURE_US = 1000        # floor, matching exposure_repo and the runner
MAX_GROUP       = 5           # largest exposure group (images per LED)
QUANTUM_RATIO   = 2 ** (1 / 8)  # exposures snap to an eighth-of-a-stop grid
# Where each group's brackets sit along the usable range, as a fraction of its
# length on a log axis: 0 is the shortest usable exposure, 1 the longest.  The
# groups share positions, so seven exposures serve all five groups.
GROUP_POSITIONS = {
    1: (1 / 2,),
    2: (0.0, 1.0),
    3: (0.0, 1 / 2, 1.0),
    4: (0.0, 1 / 3, 2 / 3, 1.0),
    5: (0.0, 1 / 4, 1 / 2, 3 / 4, 1.0),
}
EDGE_MARGIN     = 0.03        # keep planned brackets this far inside the limits
NO_SIGNAL_STEP  = 4.0         # lengthen by this when a patch shows nothing
CONVERGED       = 0.05        # a re-aim this small counts as already centered


@dataclass(frozen=True)
class FitParams:
    """The window and search limits.  `from_config()` reads the config table."""
    low_dn: float = 100.0
    high_dn: float = 850.0
    max_iter: int = 8
    min_ratio: float = 1.2
    max_ratio: float = 2.0
    max_us: int = 200_000
    min_us: int = MIN_EXPOSURE_US
    max_group: int = MAX_GROUP
    quantum_ratio: float = QUANTUM_RATIO

    @classmethod
    def from_config(cls) -> "FitParams":
        """Build the parameters from the `calibration` config rows."""
        import config_repo as config
        return cls(
            low_dn=config.calibration_window_low_dn(),
            high_dn=config.calibration_window_high_dn(),
            max_iter=config.calibration_max_search_iter(),
            min_ratio=config.calibration_min_spacing_ratio(),
            max_ratio=config.calibration_max_spacing_ratio(),
            max_us=config.calibration_max_exposure_us(),
        )


@dataclass(frozen=True)
class UsableRange:
    """
    The exposures at which every patch is inside the window.

    `e_min` is set by the darkest patch (`dark_patch`), `e_max` by the
    brightest (`bright_patch`).  `e_min` is infinite when some patch shows no
    signal at all, because then no lower limit can be worked out.
    """
    e_min: float
    e_max: float
    dark_patch: Optional[str] = None
    bright_patch: Optional[str] = None

    @property
    def known(self) -> bool:
        """True when every patch showed some signal."""
        return math.isfinite(self.e_min)

    @property
    def feasible(self) -> bool:
        """True when at least one exposure suits every patch."""
        return self.known and self.e_min <= self.e_max

    @property
    def span(self) -> float:
        """Longest usable exposure over shortest (below 1 means infeasible)."""
        return self.e_max / self.e_min if self.known else 0.0


@dataclass(frozen=True)
class RungNote:
    """
    Why a tested exposure sits where it does.

    `remark` is 'low DN' or 'high DN' for the ends of the usable range (with the
    limiting patch), or 'log rung 1/4' for a position between them.  `low_dn`
    is set only on the low end and `high_dn` only on the high end: the
    measured signal of the patch that fixed the limit.
    """
    remark: str
    low_dn: Optional[float] = None
    high_dn: Optional[float] = None


@dataclass
class LedFit:
    """
    The outcome for one LED.

    `groups` maps '1'..'5' to the tested exposures (µs, ascending) and is what
    exposure_repo.write_all_groups() stores.  A group that could not be fitted
    is left out of `groups` and explained in `failed`.
    """
    led: str
    groups: Dict[str, List[int]] = field(default_factory=dict)
    failed: Dict[str, str] = field(default_factory=dict)
    usable: Optional[UsableRange] = None
    captures: int = 0
    # {exposure_us: RungNote} for the tested exposures that were kept.
    notes: Dict[int, "RungNote"] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when every group was fitted."""
        return not self.failed

    def short_reason(self) -> str:
        """
        One line saying why this LED did not fit, short enough for the screen.

        When no exposure suits every patch it names the two that disagree and
        what each asks for, e.g. 'blue: brown.B needs 12177us, white.B allows
        7673us'.  Empty when every group was fitted.
        """
        if self.ok:
            return ""
        u = self.usable
        if u is not None and u.known and not u.feasible:
            short = lambda name: (name or "?").replace("pad_", "")
            return (f"{self.led}: {short(u.dark_patch)} needs {u.e_min:.0f}us, "
                    f"{short(u.bright_patch)} allows {u.e_max:.0f}us")
        first = next(iter(self.failed.values()))
        return f"{self.led}: {first}"


# ── Small pure helpers ──────────────────────────────────────────────────────

def _clamp(us: float, params: FitParams) -> float:
    return min(float(params.max_us), max(float(params.min_us), us))


def quantize_us(us: float, params: FitParams) -> int:
    """
    Snap an exposure to the grid, so near-identical values become identical.

    The grid is geometric, anchored at the minimum exposure, so it is the same
    for every LED.  Identical exposures mean the dark frames taken at them are
    identical too, and need to be stored only once.  Snapping is idempotent.
    """
    us = _clamp(us, params)
    step = round(math.log(us / params.min_us) / math.log(params.quantum_ratio))
    return int(_clamp(round(params.min_us * params.quantum_ratio ** step), params))


def check_levels(levels: Levels, params: FitParams) -> Tuple[List[str], List[str]]:
    """Return (patches too dark, patches too bright) at the measured exposure."""
    too_dark = sorted(n for n, (lo, _) in levels.items() if lo < params.low_dn)
    too_bright = sorted(n for n, (_, hi) in levels.items() if hi > params.high_dn)
    return too_dark, too_bright


def usable_range(levels: Levels, exposure_us: float,
                 params: FitParams) -> UsableRange:
    """
    Work out the usable exposure range from one measurement.

    Because the sensor is linear, a patch whose high percentile read `hi` at
    exposure e would reach the upper limit at e · limit / hi.  The smallest of
    those over all patches is `e_max`.  Likewise the largest of e · floor / lo
    is `e_min`.  Where a patch read nothing, `e_min` is reported as infinite.
    """
    e_max, bright = math.inf, None
    e_min, dark = 0.0, None
    for name, (lo, hi) in levels.items():
        if hi > 0 and exposure_us * params.high_dn / hi < e_max:
            e_max, bright = exposure_us * params.high_dn / hi, name
        if lo <= 0:
            e_min, dark = math.inf, name
        elif e_min != math.inf and exposure_us * params.low_dn / lo > e_min:
            e_min, dark = exposure_us * params.low_dn / lo, name
    return UsableRange(e_min, e_max, dark, bright)


def _effective(rng: UsableRange, params: FitParams) -> Tuple[float, float]:
    """The range limited by the shortest and longest exposures allowed."""
    return max(rng.e_min, params.min_us), min(rng.e_max, params.max_us)


# ── The measurement cache ───────────────────────────────────────────────────

class _Counted:
    """Wrap `measure` so repeated exposures are measured once, and counted."""

    def __init__(self, measure: Measure):
        self._measure = measure
        self._seen: Dict[int, Levels] = {}

    def __call__(self, exposure_us: int) -> Levels:
        if exposure_us not in self._seen:
            self._seen[exposure_us] = self._measure(exposure_us)
        return self._seen[exposure_us]

    @property
    def captures(self) -> int:
        return len(self._seen)


# ── The search ──────────────────────────────────────────────────────────────

def _find_range(measure: _Counted, seed_us: float,
                params: FitParams) -> Optional[UsableRange]:
    """
    Measure at the seed, then re-aim at the middle of the usable range.

    The sensor is only roughly linear, so the range is re-measured at its own
    middle until the middle stops moving.  A patch with no signal makes the
    exposure longer first.  Returns None if no measurement could be made.
    """
    exposure = int(_clamp(seed_us, params))
    rng = None
    for _ in range(params.max_iter):
        rng = usable_range(measure(exposure), exposure, params)
        if not rng.known:
            if exposure >= params.max_us:
                return rng
            exposure = int(_clamp(exposure * NO_SIGNAL_STEP, params))
            continue
        if not rng.feasible:
            return rng
        lo, hi = _effective(rng, params)
        if lo > hi:
            return rng
        target = int(_clamp(math.sqrt(lo * hi), params))
        if abs(target / exposure - 1) < CONVERGED:
            return rng
        exposure = target
    return rng


def _fit_rung(measure: _Counted, exposure: int, params: FitParams
              ) -> Tuple[Optional[int], str, Levels]:
    """
    Measure one bracket and nudge it until every patch passes.

    Returns (exposure, "", levels) on success, or (None, reason, {}).
    """
    exposure = quantize_us(exposure, params)
    for _ in range(params.max_iter):
        levels = measure(exposure)
        too_dark, too_bright = check_levels(levels, params)
        if not too_dark and not too_bright:
            return exposure, "", levels
        if too_dark and too_bright:
            return None, (f"{exposure} us: {', '.join(too_dark)} too dark "
                          f"while {', '.join(too_bright)} too bright"), {}
        if too_bright:
            factor = min(params.high_dn / levels[n][1] for n in too_bright)
            factor *= 1 - EDGE_MARGIN
        else:
            factor = max(params.low_dn / levels[n][0] if levels[n][0] > 0
                         else NO_SIGNAL_STEP for n in too_dark)
            factor *= 1 + EDGE_MARGIN
        nxt = quantize_us(exposure * factor, params)
        if nxt == exposure:                       # stuck on the grid, or a limit
            at_limit = exposure in (params.min_us, params.max_us)
            if at_limit:
                return None, f"{exposure} us: at the exposure limit and still failing", {}
            nxt = quantize_us(exposure * (params.quantum_ratio ** (
                -1 if too_bright else 1)) * 1.001, params)
        exposure = nxt
    return None, (f"{exposure} us: still outside the window after "
                  f"{params.max_iter} tries"), {}


def _position_label(pos: float) -> str:
    """0.25 -> '1/4', 1/3 -> '1/3'."""
    from fractions import Fraction
    f = Fraction(pos).limit_denominator(12)
    return f"{f.numerator}/{f.denominator}"


def _note(pos: float, levels: Levels) -> RungNote:
    """Describe the rung at `pos` from its measurement."""
    if pos <= 0.0:
        name, (lo, _) = min(levels.items(), key=lambda kv: kv[1][0])
        return RungNote(f"low DN ({name})", low_dn=lo)
    if pos >= 1.0:
        name, (_, hi) = max(levels.items(), key=lambda kv: kv[1][1])
        return RungNote(f"high DN ({name})", high_dn=hi)
    return RungNote(f"log rung {_position_label(pos)}")


def _positions_us(rng: UsableRange, params: FitParams
                  ) -> Optional[Tuple[float, float]]:
    """The usable range pulled in by the edge margin, or None if nothing is left."""
    lo, hi = _effective(rng, params)
    lo, hi = lo * (1 + EDGE_MARGIN), hi * (1 - EDGE_MARGIN)
    return (lo, hi) if lo <= hi else None


def _group_feasible(n: int, span: float, params: FitParams) -> bool:
    """Do n nested positions keep at least min_ratio between neighbors?"""
    if n == 1:
        return True
    return span ** (1 / (n - 1)) >= params.min_ratio


def fit_led(led: str, measure: Measure, seed_us: float,
            params: Optional[FitParams] = None,
            on_rung: Optional[Callable[[int, int], None]] = None) -> LedFit:
    """
    Find tested brackets for every group 1..max_group of one LED.

    The groups are nested (GROUP_POSITIONS): each distinct position along the
    usable range is tested once, and every group takes the positions it needs.

    Args:
        led:     The LED's color name, used only to label the result.
        measure: measure(exposure_us) -> {patch: (low, high)}, see the module
                 docstring.  Called at most once per distinct exposure.
        seed_us: A starting exposure, such as the old e* or the white result.
        params:  Window and limits; defaults to FitParams.from_config().
        on_rung: Called as on_rung(i, total) before each position is tested,
                 i counting 1..total, and once as on_rung(0, total) before the
                 usable range is found.

    Returns:
        LedFit.  Check `.ok`; groups that could not be fitted are in `.failed`.
    """
    params = params or FitParams.from_config()
    counted = _Counted(measure)
    result = LedFit(led)
    sizes = range(1, params.max_group + 1)
    positions = sorted({p for n in sizes for p in GROUP_POSITIONS[n]})
    total = len(positions)
    if on_rung:
        on_rung(0, total)
    rng = _find_range(counted, seed_us, params)
    result.usable = rng
    edges = _positions_us(rng, params) if rng is not None and rng.feasible else None
    if edges is None:
        why = _infeasible_reason(rng)
        for n in sizes:
            result.failed[str(n)] = why
    else:
        lo, hi = edges
        span = hi / lo
        # Test only the positions some feasible group will use.
        feasible = [n for n in sizes if _group_feasible(n, span, params)]
        for n in sizes:
            if n not in feasible:
                result.failed[str(n)] = (
                    f"usable range too narrow for {n} brackets "
                    f"({rng.dark_patch} too dark below {rng.e_min:.0f} us, "
                    f"{rng.bright_patch} too bright above {rng.e_max:.0f} us)")
        needed = sorted({p for n in feasible for p in GROUP_POSITIONS[n]})
        fitted: Dict[float, int] = {}
        reasons: Dict[float, str] = {}
        for i, pos in enumerate(positions, 1):
            if pos not in needed:
                continue
            if on_rung:
                on_rung(i, total)
            got, reason, levels = _fit_rung(
                counted, int(round(lo * span ** pos)), params)
            if got is None:
                reasons[pos] = reason
            else:
                fitted[pos] = got
                result.notes[got] = _note(pos, levels)
        for n in feasible:
            lost = [p for p in GROUP_POSITIONS[n] if p not in fitted]
            if lost:
                result.failed[str(n)] = reasons[lost[0]]
                continue
            exposures = [fitted[p] for p in GROUP_POSITIONS[n]]
            if any(b <= a or b / a < params.min_ratio * 0.98
                   for a, b in zip(exposures, exposures[1:])):
                result.failed[str(n)] = f"brackets collided after testing: {exposures}"
                continue
            result.groups[str(n)] = exposures
    result.captures = counted.captures
    for group, reason in result.failed.items():
        log.warning("%s group %s not fitted: %s", led, group, reason)
    return result


def _infeasible_reason(rng: Optional[UsableRange]) -> str:
    if rng is None:
        return "no measurement"
    if not rng.known:
        return f"{rng.dark_patch} shows no signal even at the longest exposure"
    return (f"no exposure suits every patch: {rng.dark_patch} needs at least "
            f"{rng.e_min:.0f} us but {rng.bright_patch} allows at most "
            f"{rng.e_max:.0f} us")


def fit_leds(measure_for: Callable[[str], Measure], seeds: Dict[str, float],
             params: Optional[FitParams] = None, only: Optional[str] = None,
             progress: Optional[Callable[[str], None]] = None
             ) -> Dict[str, LedFit]:
    """
    Fit every LED in `seeds`, or just the one named by `only`.

    Args:
        measure_for: measure_for(led) -> that LED's measure function.  The
                     caller turns the LED on and aims the camera inside it.
        seeds:       {led: starting exposure µs}.  Its keys, in order, are the
                     LEDs to calibrate.
        params:      As for fit_led().
        only:        Calibrate just this LED (must be a key of `seeds`).
        progress:    Called with each LED's name just before it is fitted.

    Returns:
        {led: LedFit}, in the order of `seeds`.

    Raises:
        ValueError: if `only` is not one of the LEDs in `seeds`.
    """
    params = params or FitParams.from_config()
    if only is not None and only not in seeds:
        raise ValueError(f"unknown LED {only!r}; known: {', '.join(seeds)}")
    leds = [only] if only is not None else list(seeds)
    out: Dict[str, LedFit] = {}
    for led in leds:
        if progress:
            progress(led)
        out[led] = fit_led(led, measure_for(led), seeds[led], params)
    return out
