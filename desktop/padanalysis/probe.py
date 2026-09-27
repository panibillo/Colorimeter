"""probe.py -- exploratory analysis of the C10xx / X15 strip captures.

Everything is in bound-crop Bayer-layer coordinates (130 rows x 183 cols
for bound roi 39).  Stream (x, y) -> layer ((x - bound_x)/2, (y - bound_y)/2).

Sections
--------
From schema 4 (software 0.7.0) an archive can hold two sweeps of one strip:
the measurement at the root, and the *pre-image* -- the same strip
photographed before it met any reagent -- under "/pre".  `Capture(name)`
opens the measurement, `Capture(name, "pre")` opens the pre-image of the same
file, and `sections(name)` says which are there.

The two are not interchangeable and nothing here pretends they are.  A
pre-image is a short sweep: two illuminants (royal blue and red), one
exposure each, taken two ladder rungs below e* because an undyed pad would
clip at e*.  Code that walks `c.leds` and `c.labels(led)` copes with that on
its own; code that assumes ten LEDs and three rungs does not, which is why
the section is asked for explicitly rather than merged in.

Archives written before schema 4 kept a pre-image in a separate file
(C1010-0, C1011-pre).  Those still read as ordinary captures -- `sections()`
reports no "pre" for them -- so both arrangements work side by side.
"""
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "devdesk"))

import capture_h5  # noqa: E402
from hdf5read import Hdf5Reader  # noqa: E402

CAPTURES = REPO / "captures"
BAYER = ("R", "Gr", "Gb", "B")
RAW_MAX = 1023
SATURATED_DN = RAW_MAX - 23

FILES = {
    "X15":   10.0,
    "C1002": 256.0,
    "C1003": 128.0,
    "C1004": 64.0,
    "C1005": 64.0,
}


#: The section holding a pre-image, as `Capture`'s second argument names it.
PRE = "pre"


def archive_path(name):
    """Return the archive path for a capture name."""
    return CAPTURES / f"{name}.h5"


def sections(name) -> list:
    """
    Return the sweeps an archive holds, measurement first.

    Args:
        name: Capture name, without the .h5.

    Returns:
        [""] for an archive with only a measurement sweep, ["", PRE] for one
        that also carries a pre-image.  The empty string is the measurement,
        matching `Capture`'s default, so a caller can loop over this and open
        each in turn without special-casing.
    """
    with Hdf5Reader(str(archive_path(name))) as r:
        return ["", PRE] if r.exists(f"/{PRE}/leds") else [""]


def split_label(label) -> tuple:
    """
    Split a sweep label back into (name, section).

    The inverse of `Capture.label()`.  A label is what the record files and
    the inventory use as a capture's identity, and for a pre-image that is
    "C1014/pre" -- one string, so that everything downstream can keep treating
    a capture as a name and does not have to carry a section alongside it.

    Example:
        >>> split_label("C1014/pre")
        ('C1014', 'pre')
        >>> split_label("C1014")
        ('C1014', '')
    """
    name, _, section = str(label).partition("/")
    return name, section


def open_capture(label) -> "Capture":
    """Open the sweep a label names, whichever section that is."""
    return Capture(*split_label(label))


class Capture:
    """One sweep of one archive, loaded lazily into dark-subtracted layers."""

    def __init__(self, name, section: str = ""):
        """
        Args:
            name:    Capture name, without the .h5.
            section: "" for the measurement sweep at the root, PRE for the
                     pre-image.  See the module docstring.

        Raises:
            KeyError: The archive has no such section.
        """
        self.name = name
        self.section = section.strip("/")
        self.path = archive_path(name)
        self.reader = Hdf5Reader(str(self.path))
        # Every path this class builds hangs off here, so a section moves the
        # whole sweep or none of it -- the same rule the writer follows.
        self._at = f"/{self.section}" if self.section else ""
        if self.section and not self.reader.exists(f"{self._at}/leds"):
            self.reader.close()
            raise KeyError(f"{name}.h5 has no {self.section!r} section")
        a = self.reader.attrs("/")
        self.bound = (int(a["bound_x"]), int(a["bound_y"]),
                      int(a["bound_w"]), int(a["bound_h"]))
        # Each section carries its own metadata record, and it is the section's
        # own that must be read: the ROI can be edited between the pre-image
        # and the measurement, and each sweep's layers are a crop of whatever
        # `bound` was in force when that sweep ran.  Archives written before
        # sections existed have only the root record.
        meta_at = self._at if self.reader.exists(f"{self._at}/meta") else ""
        raw = self.reader._file[f"{meta_at}/meta"][()]
        meta = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        self.meta = meta
        self.rects = {k: self.to_layer(v) for k, v in
                      meta["roi"]["rects"].items()}
        self.leds = sorted(self.reader.list_children(f"{self._at}/leds"))
        self._cache = {}

    @property
    def is_pre(self) -> bool:
        """True when this is the pre-image rather than the measurement."""
        return self.section == PRE

    def label(self) -> str:
        """A name for this sweep that is unique across an inventory."""
        return f"{self.name}/{self.section}" if self.section else self.name

    def to_layer(self, r):
        bx, by = self.bound[0], self.bound[1]
        return ((r["x"] - bx) // 2, (r["y"] - by) // 2, r["w"] // 2, r["h"] // 2)

    def labels(self, led):
        return sorted(self.reader.list_children(f"{self._at}/leds/{led}/lit"),
                      key=lambda s: int(s[1:]))

    def exposure_us(self, led, label):
        return int(self.reader.attrs(
            f"{self._at}/leds/{led}/lit/{label}/R")["exposure_us"])

    def led_metadata(self, led) -> dict:
        """The per-LED record: exposures, LED on/off times, layers kept."""
        return self.reader.read_metadata(
            f"{self._at}/leds/{led}", "led_metadata")

    def layer(self, led, label, ch):
        """
        Dark-corrected layer, as `(lit - dark_level, lit)`, or None.

        The dark correction is a single number per Bayer layer per sweep --
        the mean of the sweep's shortest-exposure dark frame, from
        `capture_h5.dark_level` -- not the matching dark frame subtracted
        pixel by pixel.  A pixel's own dark value carries 0.73 DN of fresh
        noise and almost no lasting pattern, so the frame mean is the better
        estimate for every pixel but about a dozen hot ones per layer (up to
        ~1.4 DN, see `hotpixel_analysis.persistence_over_time`), and the
        dark level does not depend on exposure (64.02-64.04 DN from 1 to
        51 ms).  See the comment above `capture_h5.dark_level`.

        Returns None if the lit layer was not stored.
        """
        key = (led, label, ch)
        if key in self._cache:
            return self._cache[key]
        lp = f"{self._at}/leds/{led}/lit/{label}/{ch}"
        out = None
        try:
            if not capture_h5.is_omitted(self.reader.attrs(lp)):
                lit = self.reader.read_array(lp).astype(np.float64)
                out = (lit - self.dark_level(ch, led, label), lit)
        except KeyError:
            out = None
        self._cache[key] = out
        return out

    def dark_level(self, ch, led=None, label=None) -> float:
        """
        The dark level subtracted from every layer `ch` of this sweep, DN.

        One number per Bayer layer per sweep; see `layer`.  `led` and
        `label` are only used to find a fallback dark in archives that keep
        no shared `/dark` groups.

        Raises:
            KeyError: The sweep has no dark frame for this layer.
        """
        key = ("dark_level", ch)
        if key not in self._cache:
            fallback = None
            if led is not None:
                dl = self._dark_link(led, label)
                if dl is not None:
                    fallback = f"{self._at}/leds/{led}/dark/{dl}/{ch}"
            self._cache[key] = capture_h5.dark_level(
                self.reader, ch, self.section, fallback)[0]
        return self._cache[key]

    def hot_mask(self, ch=None) -> np.ndarray:
        """
        The known hot pixels on this sweep's layer grid, True where hot.

        Read from the `hotpixels` table (`src/hotpixel_repo.py`) for this
        archive's own camera -- `sensor_model`, `frame_w`, `frame_h` and
        `hardware_sn` from its root attributes -- and moved from whole-sensor
        layer coordinates onto this crop by subtracting `bound // 2`.

        The analysis leaves these pixels OUT rather than repairing them.

        Args:
            ch: One Bayer layer -- 'R', 'Gr', 'Gb' or 'B' -- or None (the
                default) for the union of all four: a pixel position that is
                hot in any layer.  The analysis uses the union, so every
                cell keeps the same set of pixel positions and the cells'
                pixel lists line up one to one.

        Returns:
            (rows, cols) bool array.  All False when the archive does not
            name its camera or the table holds nothing for it.
        """
        key = ("hot_mask", ch)
        if key in self._cache:
            return self._cache[key]
        import darkframe
        import hotpixel_repo
        a = self.reader.attrs("/")
        shape = (self.bound[3] // 2, self.bound[2] // 2)
        mask = np.zeros(shape, dtype=bool)
        model = a.get("sensor_model")
        if model and "frame_w" in a and "frame_h" in a:
            layers = BAYER if ch is None else (ch,)
            oy, ox = self.bound[1] // 2, self.bound[0] // 2
            for layer in layers:
                rows = hotpixel_repo.read_active(
                    str(model), int(a["frame_w"]), int(a["frame_h"]),
                    hardware_sn=a.get("hardware_sn"), bayer_layer=layer)
                mask |= darkframe.hot_pixel_mask(
                    shape, [(r.y - oy, r.x - ox) for r in rows])
        self._cache[key] = mask
        return mask

    def darklayer(self, led, label, ch):
        """
        The dark frame matching one lit exposure, as stored, or None.

        For inspecting the darks (analysis_2, hotpixel_analysis).  It is
        not what `layer` subtracts -- that is the scalar `dark_level`.
        """
        dl = self._dark_link(led, label)
        if dl is None:
            return None
        dp = f"{self._at}/leds/{led}/dark/{dl}/{ch}"
        try:
            if capture_h5.is_omitted(self.reader.attrs(dp)):
                return None
            return self.reader.read_array(dp).astype(np.float64)
        except KeyError:
            return None

    def _dark_link(self, led, label):
        """
        The dark group matching one LED's exposure label.

        A run's dark frames are shared by exposure time and linked into each
        LED's own group under a name carrying the batch ("after_e0").  Older
        archives took two batches and named them "before_"/"after_"; "after"
        is preferred where both exist because it is the one every current
        run writes.
        """
        kids = self.reader.list_children(f"{self._at}/leds/{led}/dark")
        m = [c for c in kids if c == label or c.endswith("_" + label)]
        if not m:
            return None
        m.sort(key=lambda n: 0 if n.startswith("after_") else
               (1 if n.startswith("before_") else 2))
        return m[0]

    def dark_groups(self) -> dict:
        """
        The shared dark frames this sweep holds, one entry per exposure batch.

        Dark frames are photographed once per exposure time, after the sweep
        (hence the "after_" prefix), and stored under
        `/<section>/dark/<when>_<us>us`; every `/leds/<led>/dark/<when>_<label>`
        is a soft link to one of them.  Ten LEDs at the same exposure share
        one dark frame, so the truthful listing is per exposure, not per LED.

        `Capture.layer` already subtracts these, so this is for inspection
        only -- reading the noise floor and the fixed-pattern offset a
        Delta(K/S) can be trusted above.

        Returns:
            `{name: info}` in name order.  `info` carries `exposure_us`,
            `when` ('after' or 'before'), `captured_dt`, and `layers` --
            `{ch: (mean, std)}` of the raw DN for a stored layer, or
            `{ch: (raw_mean, None)}` for one the keep screen dropped (its
            array is empty but its statistics were kept).  A channel never
            written at all is absent from `layers`.  Empty dict when the
            archive keeps no shared dark groups.
        """
        base = f"{self._at}/dark"
        if not self.reader.exists(base):
            return {}
        out = {}
        for name in sorted(self.reader.list_children(base)):
            gp = f"{base}/{name}"
            us, cdt, layers = 0, "", {}
            for ch in BAYER:
                cp = f"{gp}/{ch}"
                if not self.reader.exists(cp):
                    continue
                a = self.reader.attrs(cp)
                us = us or int(a.get("exposure_us", 0))
                cdt = cdt or a.get("captured_dt", "")
                if capture_h5.is_omitted(a):
                    layers[ch] = (float(a.get("raw_mean", float("nan"))), None)
                else:
                    v = self.reader.read_array(cp).astype(np.float64)
                    layers[ch] = (float(v.mean()), float(v.std()))
            out[name] = {"exposure_us": us, "when": name.split("_", 1)[0],
                         "captured_dt": cdt, "layers": layers}
        return out

    def close(self):
        self.reader.close()


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "C1002"
    for section in sections(name):
        c = Capture(name, section)
        print(f"== {c.label()}")
        print("  bound", c.bound)
        print("  rects (layer)", c.rects)
        for led in c.leds:
            print(f"  {led:<11}", c.labels(led),
                  [c.exposure_us(led, l) for l in c.labels(led)])
        c.close()
