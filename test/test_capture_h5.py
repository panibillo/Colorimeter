#!/usr/bin/env python3
"""
Unit tests for CaptureSuiteWriter (src/capture_h5.py).

All tests run without hardware, writing small synthetic arrays to
temporary .h5 files and reading them back with h5py.

Focus is the behaviour the offline converter cannot exercise: dark-frame
deduplication and linking. The converter's directory scan already collapses
duplicate dark filenames before they reach the writer, so the live capture
path — which offers the same exposure once per LED — is the only caller
that depends on these guards.

Run from the project root:
    python test/test_capture_h5.py
or with unittest discovery:
    python -m unittest test.test_capture_h5
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import capture_h5
from capture_h5 import SCHEMA_VERSION, CaptureSuiteWriter


def _layers(fill: int) -> dict:
    """Return a synthetic 4-channel layers dict with distinguishable content."""
    return {ch: np.full((4, 3), fill + i, dtype=np.uint16)
            for i, ch in enumerate(("B", "Gb", "Gr", "R"))}


class CaptureSuiteWriterTestCase(unittest.TestCase):
    """Base class providing a temp .h5 path and a reopen helper."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "suite.h5"

    def tearDown(self):
        self._tmp.cleanup()

    def writer(self, **kwargs) -> CaptureSuiteWriter:
        return CaptureSuiteWriter(self.path, suite_id="20260615_2120", **kwargs)


# ---------------------------------------------------------------------------
# Root attributes and basic layout
# ---------------------------------------------------------------------------

class TestRootAndLayout(CaptureSuiteWriterTestCase):

    def test_root_attrs_stamped(self):
        with self.writer():
            pass
        with h5py.File(self.path, "r") as f:
            self.assertEqual(f.attrs["suite_id"], "20260615_2120")
            self.assertEqual(f.attrs["schema_version"], SCHEMA_VERSION)
            self.assertIn("description", f.attrs)

    def test_metadata_round_trips(self):
        """/meta carries the record; root attrs carry the essentials."""
        meta = {"roi": {"bound": {"x": 1082, "y": 478, "w": 196, "h": 660}},
                "camera": {"model": "imx708"},
                "sample": {"samp_no": "ZZ1001"}}
        with self.writer() as w:
            w.add_metadata(meta, {"bound_x": 1082, "bound_y": 478,
                                  "bound_w": 196, "bound_h": 660})
        with h5py.File(self.path, "r") as f:
            stored = json.loads(f["meta"][()])
            self.assertEqual(stored["roi"]["bound"]["w"], 196)
            self.assertEqual(stored["sample"]["samp_no"], "ZZ1001")
            # The whole point: bound's x and y are unrecoverable from the
            # layer shape, so they have to survive as attributes.
            self.assertEqual(f.attrs["bound_x"], 1082)
            self.assertEqual(f.attrs["bound_y"], 478)

    def test_metadata_root_attrs_optional(self):
        """add_metadata works with no attrs, leaving the stamped ones alone."""
        with self.writer() as w:
            w.add_metadata({"meta_version": 1})
        with h5py.File(self.path, "r") as f:
            self.assertEqual(json.loads(f["meta"][()])["meta_version"], 1)
            self.assertEqual(f.attrs["suite_id"], "20260615_2120")
            self.assertNotIn("bound_x", f.attrs)

    def test_add_led_writes_metadata_and_attrs(self):
        meta = {"led_color": "amber", "led_channel": 1, "nested": {"a": [1, 2]}}
        with self.writer() as w:
            w.add_led("amber", meta, led_channel=1)
        with h5py.File(self.path, "r") as f:
            grp = f["/leds/amber"]
            self.assertEqual(grp.attrs["led_color"], "amber")
            self.assertEqual(grp.attrs["led_channel"], 1)
            self.assertIn("led_metadata", grp)

    def test_add_lit_writes_all_channels_with_exposure_attrs(self):
        with self.writer() as w:
            w.add_lit("amber", "e3", _layers(100), exposure_us=45691)
        with h5py.File(self.path, "r") as f:
            grp = f["/leds/amber/lit/e3"]
            self.assertEqual(sorted(grp.keys()), ["B", "Gb", "Gr", "R"])
            self.assertEqual(grp["B"].attrs["exposure_us"], 45691)
            # Microseconds only. There was an exposure_ms alongside it, which
            # reported this same 45691 as a flat 45; it was dropped rather
            # than let a reader take the truncated value for the real one.
            self.assertNotIn("exposure_ms", grp["B"].attrs)
            np.testing.assert_array_equal(grp["B"][()], _layers(100)["B"])

    def test_capture_time_is_stored_when_given_and_absent_when_not(self):
        """The attribute is left off rather than written as an empty string,
        so a reader can tell "not recorded" from a real value."""
        with self.writer() as w:
            w.add_lit("amber", "e3", _layers(100), exposure_us=45691,
                      captured_dt="2026-08-06 13:56:48.123")
            w.add_lit("blue", "e3", _layers(100), exposure_us=45691)
        with h5py.File(self.path, "r") as f:
            self.assertEqual(f["/leds/amber/lit/e3/B"].attrs["captured_dt"],
                             "2026-08-06 13:56:48.123")
            self.assertNotIn("captured_dt", f["/leds/blue/lit/e3/B"].attrs)

    def test_add_jpeg_roundtrips_bytes(self):
        blob = b"\xff\xd8\xff\xe0 not really a jpeg \x00\x01"
        with self.writer() as w:
            w.add_jpeg("amber", blob, filename="camtest8_amber_e3.jpg")
        with h5py.File(self.path, "r") as f:
            ds = f["/leds/amber/jpeg_e3"]
            self.assertEqual(ds[()].tobytes(), blob)
            self.assertEqual(ds.attrs["format"], "jpeg")
            self.assertEqual(ds.attrs["filename"], "camtest8_amber_e3.jpg")


# ---------------------------------------------------------------------------
# Omitted layers — a layer measured and deliberately not stored
# ---------------------------------------------------------------------------

class TestOmittedLayers(CaptureSuiteWriterTestCase):
    """
    A Bayer layer with no usable signal is written as an empty placeholder
    carrying its statistics, rather than 45 kB of noise or nothing at all.

    Nothing at all was the tempting option and is the wrong one: an absent
    dataset cannot say why it is absent, and a reader hunting for missing data
    looks at the layer's own path first.
    """

    def test_kept_layers_are_unaffected(self):
        with self.writer() as w:
            w.add_lit("amber", "e3", _layers(100), exposure_us=45691,
                      omit=("B",))
        with h5py.File(self.path, "r") as f:
            for ch in ("Gb", "Gr", "R"):
                ds = f[f"/leds/amber/lit/e3/{ch}"]
                self.assertEqual(ds.shape, (4, 3))
                self.assertFalse(capture_h5.is_omitted(ds.attrs))

    def test_omitted_layer_is_empty_but_present(self):
        with self.writer() as w:
            w.add_lit("amber", "e3", _layers(100), exposure_us=45691,
                      omit=("B",))
        with h5py.File(self.path, "r") as f:
            ds = f["/leds/amber/lit/e3/B"]
            self.assertEqual(ds.shape, (0, 0))    # ndim 2, like a real layer
            self.assertEqual(ds.size, 0)
            self.assertEqual(ds.id.get_storage_size(), 0)
            self.assertTrue(capture_h5.is_omitted(ds.attrs))

    def test_omitted_layer_keeps_the_ordinary_attrs(self):
        """
        exposure_us and captured_dt survive, so anything reading metadata
        rather than pixels goes on working — hdf5inspect's exposure column,
        for one, indexes R whether or not R holds an array.
        """
        with self.writer() as w:
            w.add_lit("amber", "e3", _layers(100), exposure_us=45691,
                      captured_dt="2026-08-06 13:56:48.123", omit=("B",))
        with h5py.File(self.path, "r") as f:
            attrs = f["/leds/amber/lit/e3/B"].attrs
            self.assertEqual(attrs["exposure_us"], 45691)
            self.assertEqual(attrs["captured_dt"], "2026-08-06 13:56:48.123")

    def test_omitted_layer_records_what_was_seen(self):
        """The five numbers standing in for the array. Raw DN, not signal."""
        with self.writer() as w:
            w.add_lit("amber", "e3", _layers(100), exposure_us=45691,
                      omit=("B",))
        with h5py.File(self.path, "r") as f:
            attrs = f["/leds/amber/lit/e3/B"].attrs
            # _layers fills B with 100 exactly.
            self.assertEqual(attrs["raw_mean"], 100.0)
            self.assertEqual(attrs["raw_min"], 100)
            self.assertEqual(attrs["raw_max"], 100)
            self.assertEqual(attrs["raw_p10"], 100.0)
            self.assertEqual(attrs["raw_p90"], 100.0)

    def test_dark_layers_can_be_omitted_too(self):
        """
        A dark layer exists to be subtracted from a lit one, so a channel no
        lit frame kept has nothing to subtract from.
        """
        with self.writer() as w:
            w.add_dark(_layers(10), exposure_us=45691, omit=("B", "Gb"))
        with h5py.File(self.path, "r") as f:
            for ch in ("B", "Gb"):
                self.assertTrue(
                    capture_h5.is_omitted(f[f"/dark/after_45691us/{ch}"].attrs))
            for ch in ("Gr", "R"):
                self.assertEqual(f[f"/dark/after_45691us/{ch}"].shape, (4, 3))

    def test_default_keeps_everything(self):
        """
        A caller that knows nothing about layer screening behaves as before —
        which is what keeps the calibration path and the offline converter
        writing complete archives without being changed.
        """
        with self.writer() as w:
            w.add_lit("amber", "e3", _layers(100), exposure_us=45691)
            w.add_dark(_layers(10), exposure_us=45691)
        with h5py.File(self.path, "r") as f:
            for ch in ("B", "Gb", "Gr", "R"):
                self.assertFalse(
                    capture_h5.is_omitted(f[f"/leds/amber/lit/e3/{ch}"].attrs))
                self.assertFalse(
                    capture_h5.is_omitted(f[f"/dark/after_45691us/{ch}"].attrs))

    def test_is_omitted_reads_a_plain_dict(self):
        self.assertFalse(capture_h5.is_omitted({}))
        self.assertFalse(capture_h5.is_omitted({"exposure_us": 1}))
        self.assertTrue(capture_h5.is_omitted({"omitted": True}))


# ---------------------------------------------------------------------------
# Dark-frame deduplication — the guard the converter cannot reach
# ---------------------------------------------------------------------------

class TestDarkDedup(CaptureSuiteWriterTestCase):

    def test_first_dark_is_written(self):
        with self.writer() as w:
            self.assertTrue(w.add_dark(_layers(10), exposure_us=45691))
        with h5py.File(self.path, "r") as f:
            self.assertIn("/dark/after_45691us", f)
            self.assertEqual(f["/dark/after_45691us/B"].attrs["exposure_us"], 45691)
            self.assertNotIn("exposure_ms", f["/dark/after_45691us/B"].attrs)

    def test_repeat_dark_is_skipped_not_raised(self):
        """A second LED offering the same exposure must be a silent no-op.

        np.save would have overwritten; h5py.create_dataset raises on an
        existing name, so the writer must suppress the repeat itself.
        """
        with self.writer() as w:
            self.assertTrue(w.add_dark(_layers(10), exposure_us=45691))
            self.assertFalse(w.add_dark(_layers(99), exposure_us=45691))
        with h5py.File(self.path, "r") as f:
            # The first write wins; the repeat did not overwrite it.
            np.testing.assert_array_equal(
                f["/dark/after_45691us/B"][()], _layers(10)["B"])

    def test_microsecond_precision_no_longer_collapses(self):
        """Exposures differing by sub-millisecond no longer collide.

        Naming switched from truncated milliseconds to whole microseconds
        specifically to stop the precision loss the old ms-based naming had:
        45691 and 45999 us are now two distinct groups, not one.
        """
        with self.writer() as w:
            self.assertTrue(w.add_dark(_layers(10), exposure_us=45691))
            self.assertTrue(w.add_dark(_layers(99), exposure_us=45999))
        with h5py.File(self.path, "r") as f:
            self.assertEqual(sorted(f["/dark"]), ["after_45691us", "after_45999us"])

    def test_labels_no_longer_affect_dedup_only_exposure_does(self):
        """Two different labels sharing an exposure now correctly dedup.

        A dark frame's noise floor depends only on exposure time, not on
        which label asked for it, so label is deliberately not part of the
        shared group's key (unlike the legacy schema, which kept them
        separate purely because label happened to be in the name).
        """
        with self.writer() as w:
            self.assertTrue(w.add_dark(_layers(10), exposure_us=45691))
            self.assertFalse(w.add_dark(_layers(20), exposure_us=45691))
        with h5py.File(self.path, "r") as f:
            self.assertEqual([k for k in f["/dark"]], ["after_45691us"])

    def test_different_exposures_do_not_collide(self):
        with self.writer() as w:
            self.assertTrue(w.add_dark(_layers(10), exposure_us=45691))
            self.assertTrue(w.add_dark(_layers(20), exposure_us=30000))
        with h5py.File(self.path, "r") as f:
            self.assertEqual(sorted(f["/dark"]), ["after_30000us", "after_45691us"])

    def test_before_and_after_batches_do_not_collide(self):
        """The same exposure captured in both batches gets two groups."""
        with self.writer() as w:
            self.assertTrue(w.add_dark(_layers(10), exposure_us=45691, when="before"))
            self.assertTrue(w.add_dark(_layers(99), exposure_us=45691, when="after"))
        with h5py.File(self.path, "r") as f:
            self.assertEqual(
                sorted(f["/dark"]), ["after_45691us", "before_45691us"])
            np.testing.assert_array_equal(
                f["/dark/before_45691us/B"][()], _layers(10)["B"])
            np.testing.assert_array_equal(
                f["/dark/after_45691us/B"][()], _layers(99)["B"])


# ---------------------------------------------------------------------------
# Dark linking
# ---------------------------------------------------------------------------

class TestDarkLinking(CaptureSuiteWriterTestCase):

    def test_link_resolves_to_shared_group(self):
        with self.writer() as w:
            w.add_dark(_layers(10), exposure_us=45691)
            self.assertTrue(w.link_dark("amber", "e3", exposure_us=45691))
        with h5py.File(self.path, "r") as f:
            self.assertIsNotNone(f["/leds/amber/dark/after_e3"])
            np.testing.assert_array_equal(
                f["/leds/amber/dark/after_e3/B"][()], f["/dark/after_45691us/B"][()])

    def test_two_leds_share_one_dark_group(self):
        with self.writer() as w:
            w.add_dark(_layers(10), exposure_us=45691)
            w.add_dark(_layers(99), exposure_us=45691)   # skipped
            self.assertTrue(w.link_dark("amber", "e3", exposure_us=45691))
            self.assertTrue(w.link_dark("blue", "e3", exposure_us=45691))
        with h5py.File(self.path, "r") as f:
            self.assertEqual([k for k in f["/dark"]], ["after_45691us"])
            np.testing.assert_array_equal(
                f["/leds/amber/dark/after_e3/B"][()],
                f["/leds/blue/dark/after_e3/B"][()])

    def test_link_without_matching_dark_is_refused(self):
        """An LED whose exposure has no dark group must not get a broken link."""
        with self.writer() as w:
            w.add_dark(_layers(10), exposure_us=45691)
            self.assertFalse(w.link_dark("amber", "e3", exposure_us=30000))
        with h5py.File(self.path, "r") as f:
            self.assertNotIn("/leds/amber/dark/after_e3", f)

    def test_link_when_selects_matching_batch(self):
        """A before-batch link must not resolve against the after batch."""
        with self.writer() as w:
            w.add_dark(_layers(10), exposure_us=45691, when="before")
            self.assertTrue(
                w.link_dark("amber", "e3", exposure_us=45691, when="before"))
            self.assertFalse(
                w.link_dark("amber", "e3", exposure_us=45691, when="after"))
        with h5py.File(self.path, "r") as f:
            self.assertIn("/leds/amber/dark/before_e3", f)
            self.assertNotIn("/leds/amber/dark/after_e3", f)


# ---------------------------------------------------------------------------
# Rewriting the metadata of an archive that has already been closed
# ---------------------------------------------------------------------------

class TestMetadataRewrite(CaptureSuiteWriterTestCase):
    """
    The second of the two /meta writes — see the capture_h5 module docstring.
    An archive is written with a sample record that is mostly still empty,
    and the record is filled in later, once the operator has finished.
    """

    def _write_archive(self) -> dict:
        """Write an archive the way a real run does, and return its metadata."""
        meta = {
            "roi":    {"bound": {"x": 1082, "y": 478, "w": 196, "h": 660}},
            "camera": {"model": "imx708"},
            "sample": {"samp_no": "ZP1007", "capture_end": None,
                       "txt_result": None},
        }
        with self.writer() as w:
            w.add_metadata(meta, {"samp_no": "ZP1007"})
            w.add_lit("amber", "e3", _layers(100), exposure_us=45691)
        return meta

    def test_read_metadata_returns_what_was_written(self):
        meta = self._write_archive()
        self.assertEqual(capture_h5.read_metadata(self.path), meta)

    def test_replace_metadata_updates_the_record_in_place(self):
        meta = self._write_archive()
        meta["sample"]["capture_end"] = 1786042699
        meta["sample"]["txt_result"] = "0.42 ppm"
        capture_h5.replace_metadata(self.path, meta)

        after = capture_h5.read_metadata(self.path)
        self.assertEqual(after["sample"]["capture_end"], 1786042699)
        self.assertEqual(after["sample"]["txt_result"], "0.42 ppm")

    def test_replace_metadata_leaves_the_images_alone(self):
        """The whole point: results arrive long after the images, and must
        not put them at risk."""
        self._write_archive()
        meta = capture_h5.read_metadata(self.path)
        meta["sample"]["txt_result"] = "0.42 ppm"
        capture_h5.replace_metadata(self.path, meta)

        with h5py.File(self.path, "r") as f:
            np.testing.assert_array_equal(f["/leds/amber/lit/e3/B"][()],
                                          _layers(100)["B"])
            self.assertEqual(f["/leds/amber/lit/e3/B"].attrs["exposure_us"],
                             45691)

    def test_replace_metadata_keeps_the_blocks_it_was_not_given(self):
        """The camera is closed by now and could not be asked again, so the
        blocks recorded during the run have to survive untouched."""
        self._write_archive()
        meta = capture_h5.read_metadata(self.path)
        meta["sample"]["txt_result"] = "0.42 ppm"
        capture_h5.replace_metadata(self.path, meta)

        after = capture_h5.read_metadata(self.path)
        self.assertEqual(after["roi"]["bound"]["x"], 1082)
        self.assertEqual(after["camera"]["model"], "imx708")

    def test_replace_metadata_refreshes_root_attrs(self):
        self._write_archive()
        meta = capture_h5.read_metadata(self.path)
        capture_h5.replace_metadata(self.path, meta, {"samp_no": "ZP1007-a"})
        with h5py.File(self.path, "r") as f:
            self.assertEqual(f.attrs["samp_no"], "ZP1007-a")
            # An attribute not named in the refresh keeps its old value.
            self.assertEqual(f.attrs["suite_id"], "20260615_2120")

    def test_rewriting_more_than_once_is_allowed(self):
        """Called once when the capture ends and again when analysis lands."""
        self._write_archive()
        for result in ("pending", "0.42 ppm"):
            meta = capture_h5.read_metadata(self.path)
            meta["sample"]["txt_result"] = result
            capture_h5.replace_metadata(self.path, meta)
        self.assertEqual(
            capture_h5.read_metadata(self.path)["sample"]["txt_result"],
            "0.42 ppm")


# ---------------------------------------------------------------------------
# Sections — two sweeps of one strip in one archive
# ---------------------------------------------------------------------------

class TestSections(CaptureSuiteWriterTestCase):
    """
    A section moves the whole layout under a prefix, or none of it.

    The case it exists for: a pre-image and a measurement both sweep royal
    blue, so without a prefix the second would collide with the first. What is
    checked here is that the prefix is total — lit frames, dark frames, the
    per-LED links and the metadata all move together — because a layout that
    moved only partly would put a pre-image's frames where a reader looking
    for the measurement would find them.
    """

    def _sweep(self, w, color="amber", exposure_us=1462):
        """One LED's worth of content, the way a run writes it."""
        w.add_led(color, {"led_color": color}, led_channel=1)
        w.add_lit(color, "e0", _layers(10), exposure_us=exposure_us)
        w.add_dark(_layers(1), exposure_us=exposure_us, when="after")
        w.link_dark(color, "e0", exposure_us=exposure_us, when="after")

    def test_a_section_moves_the_whole_layout(self):
        with self.writer(section="/pre") as w:
            w.add_metadata({"meta_version": 2}, {"samp_no": "ZP1007"})
            self._sweep(w)

        with h5py.File(self.path, "r") as f:
            self.assertIn("/pre/leds/amber/lit/e0/B", f)
            self.assertIn("/pre/dark/after_1462us/B", f)
            self.assertIn("/pre/leds/amber/dark/after_e0/B", f)
            self.assertIn("/pre/meta", f)
            self.assertNotIn("/leds", f)
            self.assertNotIn("/dark", f)
            # The root record is written too: it describes the archive as a
            # whole, and until a measurement rewrites it the pre-image is all
            # the archive holds.
            self.assertIn("/meta", f)
            self.assertEqual(f.attrs["samp_no"], "ZP1007")

    def test_appending_adds_beside_what_is_there(self):
        with self.writer(section="/pre") as w:
            w.add_metadata({"meta_version": 2})
            self._sweep(w)
        with self.writer(mode="a") as w:
            w.add_metadata({"meta_version": 2})
            self._sweep(w, exposure_us=3290)

        with h5py.File(self.path, "r") as f:
            self.assertIn("/pre/leds/amber/lit/e0/B", f)
            self.assertIn("/leds/amber/lit/e0/B", f)
            self.assertIn("/pre/dark/after_1462us/B", f)
            self.assertIn("/dark/after_3290us/B", f)

    def test_appending_leaves_the_root_stamp_alone(self):
        """suite_id names the moment the archive was opened, not the last write."""
        with self.writer(section="/pre") as w:
            w.add_metadata({"meta_version": 2})
        with CaptureSuiteWriter(self.path, suite_id="20260615_2200", mode="a") as w:
            w.add_metadata({"meta_version": 2})

        with h5py.File(self.path, "r") as f:
            self.assertEqual(f.attrs["suite_id"], "20260615_2120")

    def test_the_second_run_rewrites_the_root_record(self):
        """
        Two records, saying different things on purpose: the root describes
        the last sweep written, the section preserves its own.
        """
        with self.writer(section="/pre") as w:
            w.add_metadata({"meta_version": 2, "who": "pre"})
        with self.writer(mode="a") as w:
            w.add_metadata({"meta_version": 2, "who": "measurement"})

        with h5py.File(self.path, "r") as f:
            self.assertEqual(json.loads(f["/meta"][()])["who"], "measurement")
            self.assertEqual(json.loads(f["/pre/meta"][()])["who"], "pre")

    def test_a_reader_can_ask_whether_a_section_is_there(self):
        """
        How a reader tells a pre-imaged archive from one without.

        Probing beats catching a KeyError from inside a read, because an
        archive with no pre-image is a normal archive -- a strip that was not
        pre-imaged -- and not an error to recover from.
        """
        from hdf5read import Hdf5Reader
        with self.writer(section="/pre") as w:
            w.add_metadata({"meta_version": 2})
            self._sweep(w)
        with Hdf5Reader(self.path) as r:
            self.assertTrue(r.exists("/pre/leds"))
            self.assertTrue(r.exists("/pre/meta"))
            self.assertFalse(r.exists("/leds"))
            self.assertFalse(r.exists("/nothing/here"))
            self.assertEqual(sorted(r.list_children("/pre/leds")), ["amber"])

    def test_no_section_is_the_layout_it_always_was(self):
        with self.writer() as w:
            w.add_metadata({"meta_version": 2})
            self._sweep(w)
        with h5py.File(self.path, "r") as f:
            self.assertIn("/leds/amber/lit/e0/B", f)
            self.assertIn("/dark/after_1462us/B", f)
            self.assertNotIn("/pre", f)


if __name__ == "__main__":
    unittest.main(verbosity=2)
