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


if __name__ == "__main__":
    unittest.main(verbosity=2)
