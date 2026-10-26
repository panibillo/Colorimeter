"""
required_cells.py  —  Which (LED, Bayer channel) pairs must pass calibration.
-----------------------------------------------------------------------------
The final analysis uses only a few LED and Bayer-channel pairs ("cells").  Only
those cells need their exposure brackets inside the calibration window; every
other channel may be too dark or too bright without harm.

The list of cells lives in the config table (`calibration.required_cells`, a
comma-separated list such as `violet.B,green.Gb,green.Gr`).  Rules:

  * A LED named in the list is held to exactly the channels named for it.
  * A LED not named falls back to the one Bayer channel closest to its color,
    from DEFAULT_CHANNEL below (red -> R, violet -> B, and so on).

This module is pure: it parses and resolves, and never reads the camera.

Each LED may also carry its own window (`calibration.led_windows`), which
overrides the global `calibration.window_*` rows for that LED.

Public surface
--------------
    parse_cells(text) -> list[(led, layer)]
    format_cells(cells) -> str
    with_layers(cells, led, layers) -> list[(led, layer)]
    required_channels(led, cells=None) -> tuple[str, ...]
    LedWindow, parse_windows(text), format_windows(windows)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

LAYERS = ("R", "Gr", "Gb", "B")

# The channel to hold an LED to when the final analysis does not name it: the
# Bayer channel closest to the LED's color.  Green sits between the two green
# channels; Gb is taken.  White is broadband; Gr is taken.
DEFAULT_CHANNEL = {
    "violet":     "B",
    "royal_blue": "B",
    "blue":       "B",
    "cyan":       "B",
    "green":      "Gb",
    "amber":      "R",
    "red_orange": "R",
    "red":        "R",
    "photo_red":  "R",
    "white":      "Gr",
}

Cell = Tuple[str, str]


def parse_cells(text: str) -> List[Cell]:
    """
    Parse 'led.layer,led.layer' into [(led, layer), ...], in order.

    Whitespace and letter case are ignored (LED names are lower case, layers
    are R, Gr, Gb and B).  An empty string gives an empty list.

    Raises:
        ValueError: if an entry is not 'led.layer', or the layer is not one of
                    R, Gr, Gb or B.

    Example:
        >>> parse_cells("violet.B, green.Gb ,green.Gr")
        [('violet', 'B'), ('green', 'Gb'), ('green', 'Gr')]
    """
    cells: List[Cell] = []
    canonical = {layer.lower(): layer for layer in LAYERS}
    for raw in text.split(","):
        entry = raw.strip()
        if not entry:
            continue
        led, dot, layer = entry.rpartition(".")
        led = led.strip().lower()
        if not dot or not led:
            raise ValueError(f"required cell {entry!r} is not 'led.layer'")
        layer = canonical.get(layer.strip().lower())
        if layer is None:
            raise ValueError(
                f"required cell {entry!r}: layer must be one of "
                f"{', '.join(LAYERS)}")
        if (led, layer) not in cells:
            cells.append((led, layer))
    return cells


def required_channels(led: str,
                      cells: Optional[Iterable[Cell]] = None) -> Tuple[str, ...]:
    """
    Return the Bayer channels of `led` that must pass the window.

    Args:
        led:   The LED's color name, e.g. 'green'.
        cells: The final analysis's (led, layer) pairs.  None reads the config
               row `calibration.required_cells`.

    Returns:
        The channels in the order they were named, or the single default
        channel for a LED the list does not name.

    Raises:
        ValueError: if the LED is not named and has no default.
    """
    if cells is None:
        import config_repo as config
        cells = config.calibration_required_cells()
    named = tuple(layer for name, layer in cells if name == led)
    if named:
        return named
    if led not in DEFAULT_CHANNEL:
        raise ValueError(f"no required channel known for LED {led!r}")
    return (DEFAULT_CHANNEL[led],)


def format_cells(cells: Iterable[Cell]) -> str:
    """The inverse of parse_cells: [('green', 'Gb')] -> 'green.Gb'."""
    return ",".join(f"{led}.{layer}" for led, layer in cells)


def with_layers(cells: Iterable[Cell], led: str,
                layers: Iterable[str]) -> List[Cell]:
    """
    Return `cells` with `led` held to exactly `layers` (in R, Gr, Gb, B order).

    Other LEDs' cells are untouched, and keep their place.  An empty `layers`
    removes the LED from the list, so it falls back to its default channel.
    """
    wanted = [layer for layer in LAYERS if layer in set(layers)]
    out: List[Cell] = []
    placed = False
    for name, layer in cells:
        if name != led:
            out.append((name, layer))
        elif not placed:
            out.extend((led, w) for w in wanted)
            placed = True
    if not placed:
        out.extend((led, w) for w in wanted)
    return out


@dataclass(frozen=True)
class LedWindow:
    """One LED's window: the signal limits (DN) and the percentiles they apply to."""
    low_dn: int
    high_dn: int
    low_pctl: float
    high_pctl: float


def parse_windows(text: str) -> Dict[str, LedWindow]:
    """
    Parse 'led=low:high:lowpct:highpct;led=...' into {led: LedWindow}.

    Raises:
        ValueError: on an entry that does not have that shape.
    """
    out: Dict[str, LedWindow] = {}
    for raw in text.split(";"):
        entry = raw.strip()
        if not entry:
            continue
        led, eq, rest = entry.partition("=")
        parts = rest.split(":")
        if not eq or not led.strip() or len(parts) != 4:
            raise ValueError(f"led window {entry!r} is not "
                             "'led=low_dn:high_dn:low_pctl:high_pctl'")
        try:
            out[led.strip().lower()] = LedWindow(
                int(float(parts[0])), int(float(parts[1])),
                float(parts[2]), float(parts[3]))
        except ValueError:
            raise ValueError(f"led window {entry!r} has a non-number") from None
    return out


def format_windows(windows: Dict[str, LedWindow]) -> str:
    """The inverse of parse_windows."""
    return ";".join(
        f"{led}={w.low_dn}:{w.high_dn}:{w.low_pctl:g}:{w.high_pctl:g}"
        for led, w in windows.items())
