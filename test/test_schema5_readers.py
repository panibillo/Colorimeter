"""
Desktop readers on schema-4 and schema-5 dark frames.

Schema 5 changed where a sweep's dark frames are and what they hold: two
frames (1000 us and the longest lit exposure), statistics only, and no
per-LED links under /leds/<color>/dark/.  Three desktop tools used to need
those links or the arrays:

    desktop/padanalysis/probe.py   Capture.layer() returned None for every layer
    devdesk/flatfield.py           load_frame() raised KeyError on every frame
    devdesk/roi_darkdiff.py        printed blank rows without saying why

Each test builds a small archive with the real writer (capture_h5), once in
each layout, so the readers are checked against what the instrument writes
rather than against a hand-made imitation.

Run:
    python test/run_tests.py test_schema5_readers
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
for sub in ("src", "devdesk", "desktop/padanalysis"):
    sys.path.insert(0, str(REPO / sub))

import capture_h5                       # noqa: E402
import flatfield                        # noqa: E402
import probe                            # noqa: E402
import roi_darkdiff                     # noqa: E402
from hdf5read import Hdf5Reader         # noqa: E402

CHANNELS = ("R", "Gr", "Gb", "B")
LIT_DN = 500.0
DARK_DN = 64.0
LIT_US = 5000
BOUND = {"x": 100, "y": 200, "w": 40, "h": 30}     # layers are 15 x 20


def _layers(value: float) -> dict:
    """Four Bayer layers of the bound crop, every pixel `value`."""
    shape = (BOUND["h"] // 2, BOUND["w"] // 2)
    return {ch: np.full(shape, value, dtype=np.uint16) for ch in CHANNELS}


def write_archive(path: Path, schema: int) -> Path:
    """
    One green LED at one exposure, with its dark frames.

    schema 4: one dark frame per exposure, stored as arrays and linked into
              the LED, as 0.9.0 wrote them.
    schema 5: two dark frames (1000 us and the lit exposure), statistics
              only, no per-LED link, as 0.10.0 writes them.
    """
    w = capture_h5.CaptureSuiteWriter(path, suite_id="TEST",
                                      schema_version=schema)
    meta = {"roi": {"rects": {"bound": dict(BOUND),
                              "sample": {"x": 110, "y": 210, "w": 8, "h": 8}}}}
    w.add_metadata(meta, {f"bound_{k}": v for k, v in BOUND.items()})
    w.add_led("green", {"led_color": "green"}, led_channel=4)
    w.add_lit("green", "e0", _layers(LIT_DN), exposure_us=LIT_US)
    if schema <= 4:
        w.add_dark(_layers(DARK_DN), exposure_us=LIT_US)
        w.link_dark("green", "e0", exposure_us=LIT_US)
    else:
        for us in (1000, LIT_US):
            w.add_dark(_layers(DARK_DN), exposure_us=us, omit=list(CHANNELS))
    w.close()
    return path


class SchemaCase(unittest.TestCase):
    """A temporary captures folder holding one archive in each layout."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.paths = {s: write_archive(self.dir / f"S{s}.h5", s) for s in (4, 5)}
        self._captures = probe.CAPTURES
        probe.CAPTURES = self.dir

    def tearDown(self):
        probe.CAPTURES = self._captures
        shutil.rmtree(self.dir, ignore_errors=True)


class TestTheArchivesAreWhatTheyClaim(SchemaCase):
    """Guards the fixture: a schema-5 test is worthless on a schema-4 file."""

    def test_schema_5_has_statistics_and_no_links(self):
        with Hdf5Reader(str(self.paths[5])) as r:
            self.assertEqual(r.attrs("/")["schema_version"], 5)
            self.assertFalse(r.exists("/leds/green/dark"))
            self.assertEqual(sorted(r.list_children("/dark")),
                             ["after_1000us", f"after_{LIT_US}us"])
            self.assertTrue(capture_h5.is_omitted(r.attrs("/dark/after_1000us/R")))

    def test_schema_4_has_arrays_and_links(self):
        with Hdf5Reader(str(self.paths[4])) as r:
            self.assertTrue(r.exists("/leds/green/dark"))
            self.assertFalse(capture_h5.is_omitted(
                r.attrs(f"/dark/after_{LIT_US}us/R")))


class TestProbe(SchemaCase):

    def test_both_layouts_give_the_same_corrected_layer(self):
        for schema in (4, 5):
            with self.subTest(schema=schema):
                c = probe.Capture(f"S{schema}")
                try:
                    out = c.layer("green", "e0", "Gr")
                    self.assertIsNotNone(out, "lit layer lost")
                    signal, lit = out
                    self.assertAlmostEqual(float(signal.mean()), LIT_DN - DARK_DN)
                    self.assertAlmostEqual(c.dark_level("Gr"), DARK_DN)
                finally:
                    c.close()

    def test_schema_5_has_no_dark_array_to_inspect(self):
        c = probe.Capture("S5")
        try:
            self.assertIsNone(c.darklayer("green", "e0", "Gr"))
            groups = c.dark_groups()
            self.assertEqual(sorted(groups), ["after_1000us", f"after_{LIT_US}us"])
            self.assertEqual(groups["after_1000us"]["layers"]["Gr"], (DARK_DN, None))
        finally:
            c.close()

    def test_schema_4_still_inspects_the_dark_array(self):
        c = probe.Capture("S4")
        try:
            self.assertEqual(float(c.darklayer("green", "e0", "Gr").mean()), DARK_DN)
        finally:
            c.close()


class TestFlatfield(SchemaCase):

    def test_both_layouts_load_a_frame(self):
        for schema, source in ((4, "after_e0"), (5, "dark 1000us")):
            with self.subTest(schema=schema):
                with Hdf5Reader(str(self.paths[schema])) as r:
                    f = flatfield.load_frame(r, "green", "e0", "Gr")
                    self.assertAlmostEqual(float(f.signal.mean()), LIT_DN - DARK_DN)
                    self.assertEqual(f.dark_link, source)
                    self.assertTrue(f.mask.all())

    def test_dark_used_names_the_shared_frame_in_schema_5(self):
        with Hdf5Reader(str(self.paths[5])) as r:
            self.assertEqual(flatfield.dark_used(r, "green", "e0", "B"),
                             (DARK_DN, "dark 1000us"))


class TestRoiDarkdiff(SchemaCase):

    def test_statistics_only_darks_have_no_pixels(self):
        with Hdf5Reader(str(self.paths[5])) as r:
            self.assertFalse(roi_darkdiff.darks_have_pixels(r))
        with Hdf5Reader(str(self.paths[4])) as r:
            self.assertTrue(roi_darkdiff.darks_have_pixels(r))

    def test_main_says_why_and_stops(self):
        argv = sys.argv
        sys.argv = ["roi_darkdiff.py", str(self.paths[5]), "-d"]
        try:
            from io import StringIO
            from contextlib import redirect_stdout
            out = StringIO()
            with redirect_stdout(out):
                code = roi_darkdiff.main()
        finally:
            sys.argv = argv
        self.assertEqual(code, 0)
        self.assertIn("statistics only", out.getvalue())


if __name__ == "__main__":
    unittest.main()
