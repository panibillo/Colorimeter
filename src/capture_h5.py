"""
capture_h5.py — capture-suite HDF5 schema for colorimeter image suites.
------------------------------------------------------------------------
Owns the *layout* of a capture-suite archive: which groups exist, how they
are named, where attributes go, and how dark frames are shared between
LEDs. Built on the generic primitives in hdf5write.py, which know nothing
about capture suites.

This module is the single place that defines the schema. It is used by
both the live capture path on the Rpi and by the offline converter that
rebuilds an archive from a legacy camtest8 output directory, so the two
cannot drift apart.

Layout:
    /                                     attrs: suite_id, description,
                                                 schema_version
    /leds/<color>                         attrs: led_color, led_channel
    /leds/<color>/led_metadata            JSON text
    /leds/<color>/capture_info            JSON text  (optional)
    /leds/<color>/jpeg_e3                 raw JPEG bytes  (optional)
    /leds/<color>/lit/<label>/<ch>        attrs: exposure_us, exposure_ms
    /leds/<color>/dark/<when>_<label>     soft link → /dark/<when>_<us>us
    /dark/<when>_<us>us/<ch>              attrs: exposure_us, exposure_ms

Dark frames are captured with the LED off, so they depend only on the
exposure time — not on which label or LED asked for it — and are shared
across LEDs and labels rather than duplicated. They live under /dark keyed
by (when, exposure), and each LED gets a soft link, named for its own label,
to the shared group matching its exposure for that "when" (a normal-path
capture takes two batches per run — "before" and "after" — to let a caller
compare drift; the calibration path takes only "after").

Usage:
    from capture_h5 import CaptureSuiteWriter

    with CaptureSuiteWriter("suite.h5", suite_id="20260615_2120") as w:
        w.add_led("amber", led_metadata, led_channel=1)
        w.add_lit("amber", "e3", layers, exposure_us=45691)
        w.add_dark(layers_dark, exposure_us=45691, when="before")
        w.link_dark("amber", "e3", exposure_us=45691, when="before")
"""

from hdf5write import Hdf5Writer

SCHEMA_VERSION    = 1
SUITE_DESCRIPTION = "Colorimeter camtest8 capture suite"


class CaptureSuiteWriter:
    """
    Writes a capture-suite HDF5 archive. Use as a context manager.

    All group/dataset naming is encapsulated here; callers pass semantic
    values (color, exposure label, exposure time) and never build paths.
    """

    def __init__(
        self, path, suite_id: str,
        description: str = SUITE_DESCRIPTION,
        schema_version: int = SCHEMA_VERSION,
    ):
        """
        Open a new archive and stamp the root attributes.

        Args:
            path:           Path of the .h5 file to create (overwritten).
            suite_id:       Identifier for this capture suite, conventionally
                            the capture timestamp (e.g. "20260615_2120").
            description:    Human-readable description stored at the root.
            schema_version: Layout version stored at the root.
        """
        self._w = Hdf5Writer(path, mode="w")
        self._w.set_attrs("/", {
            "suite_id":       suite_id,
            "description":    description,
            "schema_version": schema_version,
        })
        # Exposure keys of the dark groups already written, so a dark frame
        # shared by several LEDs is stored once.
        self._dark_written: set = set()

    def close(self) -> None:
        """Close the underlying archive. Safe to call more than once."""
        self._w.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ── Path construction (the schema lives here) ────────────────────

    @staticmethod
    def _led_group(color: str) -> str:
        """Return the group path holding everything for one LED color."""
        return f"/leds/{color}"

    @staticmethod
    def _lit_group(color: str, label: str) -> str:
        """Return the group path for one LED's lit frames at one exposure."""
        return f"/leds/{color}/lit/{label}"

    @staticmethod
    def _dark_key(exposure_us: int, when: str = "after") -> str:
        """
        Return the name identifying a shared dark group.

        Keyed on exposure time and `when` alone — deliberately NOT on label:
        a dark frame's noise floor depends only on how long the sensor was
        exposed, not on which capture label asked for that duration, so two
        different labels (or LEDs) sharing an exposure time correctly dedup
        to the same stored frame. `when` ("before"/"after") keeps a
        normal-path run's two prefetch batches from colliding when they
        happen to share an exposure time.

        Exposure is rendered in whole microseconds. This is the one place
        that formatting lives — changing it changes the whole schema
        consistently.
        """
        return f"{when}_{exposure_us}us"

    @classmethod
    def _dark_group(cls, exposure_us: int, when: str = "after") -> str:
        """Return the group path of the shared dark frames for an exposure."""
        return f"/dark/{cls._dark_key(exposure_us, when)}"

    # ── Per-LED content ──────────────────────────────────────────────

    def add_led(self, color: str, led_metadata: dict, led_channel: int) -> None:
        """
        Create the group for one LED color and store its metadata.

        Args:
            color:        LED color name (e.g. "amber").
            led_metadata: Full per-LED metadata dict, stored as JSON text.
            led_channel:  LEDBank channel number driving this LED.
        """
        group = self._led_group(color)
        self._w.write_metadata(group, "led_metadata", led_metadata)
        self._w.set_attrs(group, {
            "led_color":   color,
            "led_channel": led_channel,
        })

    def add_capture_info(self, color: str, capture_info: dict) -> None:
        """Store the image-stats / picamera2-metadata dict for one LED."""
        self._w.write_metadata(self._led_group(color), "capture_info", capture_info)

    def add_jpeg(self, color: str, jpeg_bytes: bytes, filename: str) -> None:
        """
        Store the focused full-image JPEG captured at e3 for one LED.

        Args:
            color:      LED color name.
            jpeg_bytes: Raw contents of the JPEG.
            filename:   Original filename, kept as a provenance attribute.
        """
        self._w.write_bytes(
            self._led_group(color), "jpeg_e3", jpeg_bytes,
            attrs={"filename": filename, "format": "jpeg"},
        )

    def add_lit(
        self, color: str, label: str, layers: dict, exposure_us: int,
    ) -> None:
        """
        Store the lit ROI crop for one LED at one exposure, all channels.

        Args:
            color:       LED color name.
            label:       Exposure label ("e0" … "e6").
            layers:      {channel_name: ndarray}, as returned by
                         BayerCam.extract_bayer_layers().
            exposure_us: Exposure time in microseconds.
        """
        group = self._lit_group(color, label)
        attrs = {"exposure_us": exposure_us, "exposure_ms": exposure_us // 1000}
        for ch_name, array in layers.items():
            self._w.write_array(group, ch_name, array, attrs=attrs)

    # ── Shared dark frames ───────────────────────────────────────────

    def add_dark(self, layers: dict, exposure_us: int, when: str = "after") -> bool:
        """
        Store a dark ROI crop, unless one for this (when, exposure) already
        exists.

        Dark frames depend only on exposure time, so the first caller to
        reach a given exposure (within one "when" batch) writes them and
        later callers reuse them via link_dark().

        Args:
            layers:      {channel_name: ndarray}.
            exposure_us: Exposure time in microseconds.
            when:        Which batch this frame belongs to — "before" or
                         "after" (default) — see the module docstring.

        Returns:
            True if the frames were written, False if an identical
            (when, exposure) was already present and this call was a no-op.
        """
        key = self._dark_key(exposure_us, when)
        if key in self._dark_written:
            return False

        group = self._dark_group(exposure_us, when)
        attrs = {"exposure_us": exposure_us, "exposure_ms": exposure_us // 1000}
        for ch_name, array in layers.items():
            self._w.write_array(group, ch_name, array, attrs=attrs)
        self._dark_written.add(key)
        return True

    def link_dark(self, color: str, label: str, exposure_us: int,
                  when: str = "after") -> bool:
        """
        Link one LED's dark slot to the shared group for its exposure.

        Args:
            color:       LED color name.
            label:       Exposure label ("e0" … "e6"), used only to name this
                         LED's own link — the shared group itself is not
                         keyed by label (see _dark_key()).
            exposure_us: The LED's own exposure time for this label.
            when:        Which batch to link against — "before" or "after"
                         (default) — see the module docstring.

        Returns:
            True if the link was created, False if no dark group exists for
            that (when, exposure) (nothing to link to).
        """
        if self._dark_key(exposure_us, when) not in self._dark_written:
            return False
        self._w.link(
            self._dark_group(exposure_us, when),
            f"/leds/{color}/dark/{when}_{label}",
        )
        return True
