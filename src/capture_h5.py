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
                                                 schema_version, plus the
                                                 flattened essentials from
                                                 capture_meta.root_attrs()
                                                 (bound_x/y/w/h, frame_w/h,
                                                 sensor_model, hardware_sn, …)
    /meta                                 JSON text: the full metadata record
    /leds/<color>                         attrs: led_color, led_channel
    /leds/<color>/led_metadata            JSON text
    /leds/<color>/capture_info            JSON text  (optional)
    /leds/<color>/jpeg_e3                 raw JPEG bytes  (optional)
    /leds/<color>/lit/<label>/<ch>        attrs: exposure_us, captured_dt
    /leds/<color>/dark/<when>_<label>     soft link → /dark/<when>_<us>us
    /dark/<when>_<us>us/<ch>              attrs: exposure_us, captured_dt

`/meta` carries the ROI geometry, camera configuration, instrument identity
and sample record, so an archive can be interpreted without consulting any
database. That matters because the analysis machine's colorimeter.sqlite is a
*copy*: `sync_captures_from_pi.sh` brings `captures/` across but not the
database, so an ROI edited on the instrument never reaches it. `bound`'s width
and height can be recovered from the stored layer shape, but its x and y
cannot — they are only in `/meta` (and the root attrs). See capture_meta.py,
which decides what goes in; this module only writes what it is handed.

`/meta` is written twice, on purpose, and a maintainer needs to know both
times or the second one looks like a mistake:

  1. Once at the very start of a run, by CaptureRunner._write_metadata(),
     while the archive is still open. Everything known before the first
     photograph goes in here — above all the ROI geometry, without which the
     stored arrays cannot be located. Writing it first means a run that dies
     partway still leaves a readable file.
  2. Again after the run has finished and the archive has been closed, by
     capture_meta.update_archive_sample(), which reopens the file and swaps
     in a fresh copy of the sample record. This is needed because several
     columns of that record do not exist yet at step 1 — the operator has
     not finished the reading, so capture_end, txt_result and num_result are
     still empty. See the comments on those two functions for the full list.

Only the JSON text at `/meta` and the root attributes are rewritten in step
2. No image data is ever touched after it is written.

Dark frames are captured with the LED off, so they depend only on the
exposure time — not on which label or LED asked for it — and are shared
across LEDs and labels rather than duplicated. They live under /dark keyed
by (when, exposure), and each LED gets a soft link, named for its own label,
to the shared group matching its exposure for that "when".

Both capture paths now take a single batch, "after", at the end of the run.
A normal-path run used to take two — "before" as well, so a reader could
difference them and measure the sensor's drift across the run (that reader is
devdesk/roi_darkdiff.py). It answered its question; paying for it every run
cost 30 settled captures and 5.3 MB per archive. The `when` parameter and the
"before_<us>us" naming stay, because archives already written use them and
must keep reading correctly.

Usage:
    from capture_h5 import CaptureSuiteWriter

    with CaptureSuiteWriter("suite.h5", suite_id="20260615_2120") as w:
        meta = capture_meta.collect(cam, cfg, sample_rowid=7)
        w.add_metadata(meta, capture_meta.root_attrs(meta))
        w.add_led("amber", led_metadata, led_channel=1)
        w.add_lit("amber", "e3", layers, exposure_us=45691)
        w.add_dark(layers_dark, exposure_us=45691, when="before")
        w.link_dark("amber", "e3", exposure_us=45691, when="before")
"""

import numpy as np

from hdf5write import Hdf5Writer

# 1 -> 2: added /meta and the flattened root attrs. A version-1 archive has
# no ROI geometry of its own; a reader must fall back to its `roi` table and
# cannot verify the result. Readers should treat /meta as optional and check
# schema_version before relying on it.
#
# 2 -> 3: a layer dataset may be present but empty. See OMITTED_ATTR: a Bayer
# layer with no usable signal is stored as a zero-size array carrying its
# statistics, instead of 45 kB of noise. A reader that indexes a layer without
# checking will get an empty array rather than the KeyError it would get from
# an absent one, so every reader of image data needs is_omitted().
SCHEMA_VERSION    = 3
SUITE_DESCRIPTION = "Colorimeter camtest8 capture suite"
METADATA_NAME     = "meta"

# Marks a layer dataset that was measured and deliberately not stored.
#
# The alternatives were to leave the dataset out entirely, or to record the
# omission somewhere central. A zero-size dataset in the layer's own place is
# better than either: it costs nothing (HDF5 stores no bytes for it), it keeps
# the archive's shape uniform so a reader walking /leds/<c>/lit/<label> still
# sees four layers, and above all it puts the explanation exactly where someone
# looking for the missing data will look. An absent dataset says nothing about
# why it is absent.
OMITTED_ATTR = "omitted"

# Statistics kept for an omitted layer, so the archive still records what was
# seen. Raw DN, not dark-subtracted: at the moment a lit frame is written no
# dark frame exists yet — they are all taken at the end of the run — so there
# is nothing to subtract. Named raw_* for that reason, and to keep them
# distinct from the dark-subtracted signal DN that layermodels.p10/p90 hold.
_OMITTED_PCTLS = (10, 90)


def _frame_attrs(exposure_us: int, captured_dt: str) -> dict:
    """
    Build the attributes stamped on every stored image dataset.

    One helper for both lit and dark frames, so the two cannot drift apart.

    Exposure is recorded in microseconds only. There used to be an
    `exposure_ms` alongside it, computed as `exposure_us // 1000`; it was
    dropped because integer division threw away the remainder, so an 11,888 µs
    exposure was advertised as a flat 11 ms. Anything wanting milliseconds
    should divide `exposure_us` itself and keep the fraction.

    Args:
        exposure_us: Exposure time in microseconds.
        captured_dt: When the photograph was taken, as text with milliseconds,
                     or "" if unknown.

    Returns:
        The attribute dict. `captured_dt` is left out entirely when it is "",
        because an HDF5 attribute holding an empty string is harder for a
        reader to recognise as "no value" than an absent one.
    """
    attrs = {"exposure_us": exposure_us}
    if captured_dt:
        attrs["captured_dt"] = captured_dt
    return attrs


def _omitted_attrs(array) -> dict:
    """
    Build the statistics stored in place of a layer that is not being kept.

    Five numbers standing in for 45 kB. They are what lets a later reader
    answer "how dark was it, actually?" without the array — enough to confirm
    the decision was right, or to notice that it stopped being right.

    Args:
        array: The layer that would have been stored, as captured.

    Returns:
        The attribute dict, including OMITTED_ATTR.

    Example:
        >>> _omitted_attrs(np.full((4, 4), 70, dtype=np.uint16))
        ...                                            # doctest: +ELLIPSIS
        {'omitted': True, 'raw_mean': 70.0, ...}
    """
    lo, hi = np.percentile(array, _OMITTED_PCTLS)
    return {
        OMITTED_ATTR: True,
        "raw_mean": round(float(array.mean()), 3),
        f"raw_p{_OMITTED_PCTLS[0]}": round(float(lo), 3),
        f"raw_p{_OMITTED_PCTLS[1]}": round(float(hi), 3),
        "raw_min": int(array.min()),
        "raw_max": int(array.max()),
    }


def is_omitted(attrs: dict) -> bool:
    """
    Return whether a layer dataset's attributes mark it as not stored.

    The read-side companion to add_lit()/add_dark()'s `omit`. Kept here, in the
    module that owns the schema, so the two sides cannot drift — every reader
    of archive image data should call this before using an array.

    Treat an omitted layer exactly as an absent one. Both mean "there is no
    image here"; the difference is only that this one says why.

    Args:
        attrs: The dataset's attributes, e.g. from Hdf5Reader.attrs(path).

    Returns:
        True if the dataset is an empty placeholder.

    Example:
        >>> is_omitted({"exposure_us": 26187})
        False
        >>> is_omitted({"exposure_us": 26187, "omitted": True})
        True
    """
    return bool(attrs.get(OMITTED_ATTR, False))


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

    # ── Archive-wide metadata ────────────────────────────────────────

    def add_metadata(self, meta: dict, root_attrs: dict = None) -> None:
        """
        Store the archive-wide metadata record, and its flattened essentials.

        Write this once, before the LED content, so a reader that stops early
        or a run that aborts partway still yields an interpretable file — the
        ROI geometry is what makes the arrays mean anything.

        This module does not decide what metadata is; capture_meta.collect()
        does. Keeping the two apart means a new field is a one-line edit there
        and no change here.

        Args:
            meta: JSON-serializable metadata dict, from capture_meta.collect().
            root_attrs: Flat scalars to set on "/" for readers that should not
                have to parse JSON, from capture_meta.root_attrs(). HDF5
                attributes cannot hold None, so that helper omits absent
                values rather than writing placeholders.

        Returns:
            None

        Example:
            >>> attrs = capture_meta.root_attrs(meta)   # doctest: +SKIP
            >>> w.add_metadata(meta, attrs)            # doctest: +SKIP
        """
        self._w.write_metadata("/", METADATA_NAME, meta)
        if root_attrs:
            self._w.set_attrs("/", root_attrs)

    def add_photo(self, jpeg_bytes: bytes, filename: str, crop,
                  attrs: dict = None) -> None:
        """
        Store the run's colour-balanced photograph of the sample.

        Suite-level, not per-LED: there is one of these per run, taken under
        the white LED after every LED's frames are in, so it hangs off "/"
        rather than off any /leds group.  Contrast add_jpeg(), which stores
        one neutral preview per LED.

        The image is a crop, so the crop rectangle is stored beside it.
        Without it the photograph cannot be placed in the sensor frame, and a
        reader comparing it against the Bayer layers has no way to know the
        two cover the same ground — which they do: `crop` is the ROLE_BOUND
        rectangle the layers are cropped to as well.

        Args:
            jpeg_bytes: Raw contents of the JPEG.
            filename:   Original filename, kept as a provenance attribute.
            crop:       The Rect the photo was cropped to, in stream space.
            attrs:      Extra attributes to record alongside (exposure, LED,
                        colour gains) — whatever the caller knows that makes
                        the picture interpretable later.
        """
        meta = {
            "filename": filename,
            "format":   "jpeg",
            "crop_x":   int(crop.x),
            "crop_y":   int(crop.y),
            "crop_w":   int(crop.w),
            "crop_h":   int(crop.h),
        }
        meta.update(attrs or {})
        self._w.write_bytes("/", "photo", jpeg_bytes, attrs=meta)

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

    def _write_layers(self, group: str, layers: dict, exposure_us: int,
                      captured_dt: str, omit) -> None:
        """
        Write one group's four layers, replacing the omitted ones with markers.

        Shared by add_lit() and add_dark() so a lit layer and the dark frame
        that would be subtracted from it are stored — or not stored — by
        exactly the same rule.

        Args:
            group:       HDF5 group path to write under.
            layers:      {channel_name: ndarray}.
            exposure_us: Exposure time in microseconds.
            captured_dt: Capture timestamp, or "".
            omit:        Channel names to store as empty placeholders.
        """
        omit = set(omit or ())
        for ch_name, array in layers.items():
            attrs = _frame_attrs(exposure_us, captured_dt)
            if ch_name in omit:
                attrs.update(_omitted_attrs(array))
                # Shape (0, 0) rather than (0,): a reader that unpacks
                # array.shape into rows and columns still works. HDF5 accepts
                # the compression setting on an empty dataset and stores no
                # bytes for it.
                array = np.empty((0, 0), dtype=array.dtype)
            self._w.write_array(group, ch_name, array, attrs=attrs)

    def add_lit(
        self, color: str, label: str, layers: dict, exposure_us: int,
        captured_dt: str = "", omit=(),
    ) -> None:
        """
        Store the lit ROI crop for one LED at one exposure, all channels.

        Args:
            color:       LED color name.
            label:       Exposure label ("e0" … "e6").
            layers:      {channel_name: ndarray}, as returned by
                         BayerCam.extract_bayer_layers(). Pass all four
                         whatever is being kept — `omit` decides what reaches
                         the file, and the statistics of an omitted layer are
                         computed from the array passed here.
            exposure_us: Exposure time in microseconds.
            captured_dt: When this photograph was taken, as text with
                         milliseconds — "2026-08-06 13:56:48.123". The runner
                         reads the clock immediately after the frame arrives
                         from the sensor, so the exposure itself began about
                         `exposure_us` earlier than this says. Pass "" (the
                         default) when the time is not known, and the
                         attribute is left off the datasets rather than
                         written as an empty string; the offline converter in
                         dev/build_h5_from_camtest8.py does that, because it
                         rebuilds an archive from files photographed long ago.
            omit:        Channel names to store as empty placeholders instead
                         of arrays — the layers that carry no usable signal for
                         this LED at this exposure. The caller decides which:
                         see layermodel_repo.keep_channels(). Default keeps
                         everything, so a caller that knows nothing about the
                         flat-field analysis behaves as it always did.
        """
        self._write_layers(self._lit_group(color, label), layers,
                           exposure_us, captured_dt, omit)

    # ── Shared dark frames ───────────────────────────────────────────

    def add_dark(self, layers: dict, exposure_us: int, when: str = "after",
                 captured_dt: str = "", omit=()) -> bool:
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
            captured_dt: When this photograph was taken, as text with
                         milliseconds. Because a repeated (when, exposure) is
                         skipped rather than stored twice, the time kept is
                         the one from the photograph that was actually
                         stored — the first to reach this exposure. Any LED
                         that later links to it was photographed at some
                         other moment, so do not read this as that LED's own
                         capture time.
            omit:        Channel names to store as empty placeholders. A dark
                         layer exists to be subtracted from a lit one, so a
                         channel no lit frame at this exposure kept has nothing
                         to subtract from and is not worth storing. The caller
                         must therefore collect the lit sweep's decisions
                         first and pass the complement here — which is why the
                         dark batch runs after the sweep, not before it.

        Returns:
            True if the frames were written, False if an identical
            (when, exposure) was already present and this call was a no-op.
        """
        key = self._dark_key(exposure_us, when)
        if key in self._dark_written:
            return False

        self._write_layers(self._dark_group(exposure_us, when), layers,
                           exposure_us, captured_dt, omit)
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


# ── Rewriting the metadata of an archive that is already closed ──────────
# These two are the only way anything reaches an archive after its run has
# finished. They exist so the sample record can be brought up to date once
# the operator has finished the reading — see the module docstring, step 2,
# and capture_meta.update_archive_sample(), which is the one caller.
#
# They are deliberately kept apart from CaptureSuiteWriter: that class creates
# a file and owns it for the length of a run, whereas these open a finished
# file, change one small thing, and leave. Mixing the two would blur when the
# archive is safe to touch.

def read_metadata(path) -> dict:
    """
    Read back the metadata record of an existing archive.

    Args:
        path: Path of the .h5 file to read. Opened read-only.

    Returns:
        The dict that capture_meta.collect() produced when the archive was
        written.

    Raises:
        KeyError: The archive has no /meta — it was written by a version of
            this schema older than 2, before the record existed.
        OSError: The file is missing or is not readable as HDF5.
    """
    from hdf5read import Hdf5Reader     # imported here to keep the write path
                                        # free of the reader on the instrument
    with Hdf5Reader(path) as r:
        return r.read_metadata("/", METADATA_NAME)


def replace_metadata(path, meta: dict, root_attrs: dict = None) -> None:
    """
    Reopen a closed archive and rewrite its metadata record in place.

    The old /meta dataset is deleted and a new one written in its place.
    HDF5 does not return the deleted bytes to the file, so each rewrite
    leaves the previous copy's space behind; the record is a few kilobytes of
    text and is rewritten at most a couple of times per archive, so the waste
    is not worth reclaiming. No image data is touched.

    Args:
        path:       Path of the .h5 file, which must already exist. Opened in
                    append mode, so nothing else in it is disturbed.
        meta:       The complete replacement record. Pass the whole dict, not
                    just the part that changed — this overwrites, it does not
                    merge.
        root_attrs: Flat scalars to refresh on "/", from
                    capture_meta.root_attrs(). Attributes not named here keep
                    the values they already had.

    Returns:
        None
    """
    w = Hdf5Writer(path, mode="a")
    try:
        w.delete(f"/{METADATA_NAME}")
        w.write_metadata("/", METADATA_NAME, meta)
        if root_attrs:
            w.set_attrs("/", root_attrs)
    finally:
        w.close()
