#!/usr/bin/env python3
"""
Integration tests for CaptureRunner (src/capture_runner.py).

Drives a full capture suite against a synthetic camera and LED bank, with no
hardware and no picamera2. The fake camera returns frames whose brightness
scales with the requested exposure, so the exposure-bracketing and e3
refinement logic is genuinely exercised rather than stubbed out.

The runner writes a single HDF5 archive with nothing staged on disk, so the
assertions here read the archive back rather than globbing for files.

Run from the project root:
    python test/test_capture_runner.py
or with unittest discovery:
    python -m unittest test.test_capture_runner
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "dev"))

import capture_runner
import config_repo
from capture_runner import CaptureConfig, CaptureRunner, EXP_LABELS

# Full-sensor frame size the ROI constants are defined against.
FRAME_W, FRAME_H = 2304, 1296

# Per-pixel gain scale chosen so the median pixel reaches HALF_SAT (512)
# at roughly 45 ms, matching the real captures this workflow was tuned on.
_MEDIAN_GAIN = 0.65
_TARGET_US   = 45_000
_SCALE       = capture_runner.HALF_SAT / (_MEDIAN_GAIN * _TARGET_US)


class FakeCam:
    """
    Synthetic BayerCam stand-in.

    Produces a fixed random per-pixel gain field, scaled by the requested
    exposure and clipped at 10-bit saturation, so brighter exposures really
    do yield brighter frames and percentile-driven logic converges.
    """

    MAX_EXPOSURE_US = 10_000_000

    class Device:
        """Stands in for the libcamera device handle set_controls() targets."""

        def __init__(self):
            self.controls: list = []

        def set_controls(self, controls: dict) -> None:
            self.controls.append(controls)

    def __init__(self, pattern: str = "RGGB", af_result: float = 3.0):
        self.model_description = "FakeCam (synthetic)"
        self.has_autofocus     = True
        self.bayer_pattern     = pattern
        self.config_size       = None
        self.lens_position     = None
        self.camera            = None
        self._af_result        = af_result

        rng = np.random.default_rng(0)
        self._gain = rng.uniform(0.3, 1.0, size=(FRAME_H, FRAME_W))
        self._exposure_us = 0
        self.exposures_captured: list = []
        self.stopped = False

    def set_exposure(self, exposure_us: int) -> None:
        self._exposure_us = int(exposure_us)

    def setup_camera(self) -> None:
        self.config_size = (FRAME_W, FRAME_H)
        self.camera = self.Device()

    def capture_frame(self):
        self.exposures_captured.append(self._exposure_us)
        bayer = np.clip(self._gain * self._exposure_us * _SCALE,
                        0, 1023).astype(np.uint16)
        rgb = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)
        # Real picamera2 returns native tuples for multi-valued controls;
        # the desktop mock returns their string reprs instead. Model the
        # hardware, since that is what the archive has to represent.
        meta = {
            "ExposureTime": self._exposure_us,
            "ColourGains":  (1.0, 1.0),
            "SensorBlackLevels": (4096, 4096, 4096, 4096),
        }
        return rgb, bayer, meta

    def focus(self, force: bool = False, leds=None) -> float:
        self.lens_position = self._af_result
        return self._af_result

    def stop(self) -> None:
        self.stopped = True


class FakeLeds:
    """Synthetic LEDBank stand-in that records every switching operation."""

    _COLORS = {1: "amber", 2: "blue", 10: "white"}

    def __init__(self):
        self.state: dict = {}
        self.history: list = []
        self.closed = False

    def set_channel(self, channel: int, on: bool) -> None:
        self.state[channel] = on
        self.history.append((channel, on))

    def close(self) -> None:
        """Mirrors LEDBank.close(): everything off, resources released."""
        for channel in self.state:
            self.state[channel] = False
        self.closed = True

    def color_name(self, channel: int) -> str:
        return self._COLORS.get(channel, f"led{channel}")

    def channel_of(self, name: str) -> int:
        for ch, color in self._COLORS.items():
            if color == name:
                return ch
        raise ValueError(f"Unknown LED name {name!r}")

    def label(self, channel: int) -> str:
        return f"LED {channel} ({self.color_name(channel)})"

    def lit_channels(self) -> set:
        return {ch for ch, on in self.state.items() if on}


class RunnerTestCase(unittest.TestCase):
    """Base class: temp output dir and warmups shortened to keep tests fast."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out_root = Path(self._tmp.name)
        self.cam  = FakeCam()
        self.leds = FakeLeds()

        # The real 2 s LED waits would dominate the test runtime.
        self._real_on_warmup  = capture_runner.LED_ON_WARMUP_S
        self._real_off_settle = capture_runner.LED_OFF_SETTLE_S
        capture_runner.LED_ON_WARMUP_S  = 0.0
        capture_runner.LED_OFF_SETTLE_S = 0.0

    def tearDown(self):
        capture_runner.LED_ON_WARMUP_S  = self._real_on_warmup
        capture_runner.LED_OFF_SETTLE_S = self._real_off_settle
        self._tmp.cleanup()

    def make_runner(self, **kwargs) -> CaptureRunner:
        """Build a runner. Defaults to autofocus, the only path that works
        without libcamera; manual focus is covered separately in TestFocus."""
        config = CaptureConfig(out_dir=self.out_root, **kwargs)
        return CaptureRunner(self.cam, self.leds, config)

    def run_to_completion(self, runner: CaptureRunner) -> list:
        return list(runner.run())

    def archive(self, runner: CaptureRunner):
        """Open the archive a completed run produced."""
        return h5py.File(runner.out_path, "r")

    def led_metadata(self, runner: CaptureRunner, color: str) -> dict:
        """Read one LED's metadata dict back out of the archive."""
        with self.archive(runner) as f:
            return json.loads(f[f"/leds/{color}/led_metadata"][()])


# ---------------------------------------------------------------------------
# LED timing
# ---------------------------------------------------------------------------

class TestLedTiming(unittest.TestCase):
    """Pins the shipped wait durations, independent of the tests that zero
    them out for speed."""

    def test_on_warmup_is_two_seconds(self):
        self.assertEqual(capture_runner.LED_ON_WARMUP_S, 2.0)

    def test_off_settle_is_two_seconds(self):
        self.assertEqual(capture_runner.LED_OFF_SETTLE_S, 2.0)


# ---------------------------------------------------------------------------
# Output location
# ---------------------------------------------------------------------------
# Where archives are written and what they are called is config_repo's
# job now; see test_config_repo.py. What belongs here is that a config
# built for a run honours an explicit directory.

class TestOutputLocation(unittest.TestCase):

    def setUp(self):
        self._saved = os.environ.pop(config_repo.CAPTURE_DIR_ENV, None)

    def tearDown(self):
        os.environ.pop(config_repo.CAPTURE_DIR_ENV, None)
        if self._saved is not None:
            os.environ[config_repo.CAPTURE_DIR_ENV] = self._saved

    def test_config_resolves_location_when_built_not_when_imported(self):
        """
        Regression guard: out_dir was once a plain dataclass default, baked in
        at import, so setting the variable afterwards silently had no effect.
        """
        os.environ[config_repo.CAPTURE_DIR_ENV] = "/tmp/set/after/import"
        self.assertEqual(CaptureConfig().out_dir, Path("/tmp/set/after/import"))

    def test_explicit_out_dir_wins_over_environment(self):
        os.environ[config_repo.CAPTURE_DIR_ENV] = "/tmp/ignored"
        config = CaptureConfig(out_dir=Path("/tmp/explicit"))
        self.assertEqual(config.out_dir, Path("/tmp/explicit"))


# ---------------------------------------------------------------------------
# A complete single-LED run
# ---------------------------------------------------------------------------

class TestSingleLedRun(RunnerTestCase):

    def setUp(self):
        super().setUp()
        self.runner   = self.make_runner(led_start=1, led_end=1)
        self.progress = self.run_to_completion(self.runner)

    def test_run_reaches_done(self):
        self.assertEqual(self.progress[-1].phase, "done")
        self.assertEqual(self.progress[-1].fraction, 1.0)

    def test_all_phases_observed(self):
        phases = {p.phase for p in self.progress}
        self.assertEqual(phases, {"setup", "calibrate", "focus", "capture", "done"})

    def test_fraction_is_monotonic(self):
        fractions = [p.fraction for p in self.progress]
        for earlier, later in zip(fractions, fractions[1:]):
            self.assertLessEqual(earlier, later)

    def test_writes_one_archive_and_nothing_else(self):
        """The whole point of the direct path: no staged image files."""
        written = list(self.out_root.iterdir())
        self.assertEqual(written, [self.runner.out_path])
        self.assertEqual(self.runner.out_path.suffix, ".h5")

    def test_archive_holds_lit_and_dark_for_every_label_and_channel(self):
        with self.archive(self.runner) as f:
            for label in EXP_LABELS:
                for ch in ("B", "Gb", "Gr", "R"):
                    self.assertIn(f"/leds/amber/lit/{label}/{ch}", f)
                    self.assertIn(f"/leds/amber/dark/after_{label}/{ch}", f)

    def test_roi_bound_crop_shape(self):
        with self.archive(self.runner) as f:
            arr = f["/leds/amber/lit/e3/B"][()]
        # The bound the run actually used, loaded from the `roi` table, not a
        # module constant — so an edited ROI is checked, not a stale copy.
        bound = self.runner.rois["bound"]
        self.assertEqual(arr.shape, (bound.h // 2, bound.w // 2))

    def test_rois_are_loaded_and_bayer_safe(self):
        """Every ROI the run used must be even, or layer extraction shifts phase."""
        import roi as roi_mod
        for name in capture_runner.REQUIRED_ROIS:
            with self.subTest(roi=name):
                r = self.runner.rois[name]
                self.assertTrue(roi_mod.is_bayer_safe(r), f"{name}={r.xywh()}")

    def test_every_roi_lies_inside_bound(self):
        """
        The archive stores only the bound crop, so anything reaching outside it
        cannot be re-measured from the archive.

        Regression guard: bound was commissioned at w=160, ending at x=1250,
        which clipped 36 px off the white patch (x 1150..1286) — 26% of its
        width. Widened to w=196 on 2026-07-31, changing the archived crop shape
        from 315x80 to 315x98.
        """
        import roi as roi_mod
        bound = self.runner.rois["bound"]
        for name, r in sorted(self.runner.rois.items()):
            if name == "bound":
                continue
            with self.subTest(roi=name):
                self.assertTrue(
                    roi_mod.contains_rect(bound, r),
                    f"{name}={r.xywh()} outside bound={bound.xywh()}")

    def test_lit_arrays_carry_exposure_attrs(self):
        meta = self.led_metadata(self.runner, "amber")
        with self.archive(self.runner) as f:
            ds = f["/leds/amber/lit/e3/B"]
            self.assertEqual(ds.attrs["exposure_us"],
                             meta["computed_exposures"]["e3"]["exposure_us"])

    def test_archive_holds_a_decodable_e3_jpeg(self):
        import cv2
        with self.archive(self.runner) as f:
            blob = f["/leds/amber/jpeg_e3"][()].tobytes()
        img = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(img, "stored e3 JPEG did not decode")
        self.assertEqual(img.shape, (FRAME_H, FRAME_W, 3))

    def test_capture_info_preserves_native_metadata_types(self):
        """Tuple-valued controls must survive as sequences, not stringified."""
        with self.archive(self.runner) as f:
            info = json.loads(f["/leds/amber/capture_info"][()])
        self.assertEqual(info["picamera2_metadata"]["ColourGains"], [1.0, 1.0])
        self.assertEqual(info["image_stats"]["image_width"], FRAME_W)
        self.assertEqual(info["image_stats"]["bayer_shape"],
                         f"{FRAME_H}x{FRAME_W}")

    def test_metadata_covers_all_labels(self):
        meta = self.led_metadata(self.runner, "amber")
        self.assertEqual(sorted(meta["computed_exposures"]), sorted(EXP_LABELS))
        self.assertEqual(sorted(meta["applied_exposures_us"]), sorted(EXP_LABELS))
        self.assertEqual(meta["led_color"], "amber")
        self.assertEqual(meta["focus_mode"], "autofocus")

    def test_suite_id_stamped_on_archive_root(self):
        with self.archive(self.runner) as f:
            self.assertEqual(f.attrs["suite_id"], self.runner.suite_id)

    def test_result_recorded(self):
        self.assertEqual(len(self.runner.results), 1)
        result = self.runner.results[0]
        self.assertEqual(result.color, "amber")
        self.assertEqual(len(result.exposures_us), len(EXP_LABELS))

    def test_e3_converges_near_half_saturation(self):
        """The refinement loop must actually drive p50 toward HALF_SAT."""
        meta = self.led_metadata(self.runner, "amber")
        self.assertTrue(meta["e3_refinement"][-1]["converged"],
                        f"e3 never converged: {meta['e3_refinement']}")

    def test_exposures_increase_across_the_bracket(self):
        """e1 → e5 are percentile-driven and must be monotonically longer."""
        exposures = dict(zip(EXP_LABELS, self.runner.results[0].exposures_us))
        ordered = [exposures[f"e{i}"] for i in range(1, 6)]
        self.assertEqual(ordered, sorted(ordered), f"not monotonic: {ordered}")

    def test_all_leds_off_at_end(self):
        self.assertEqual(self.leds.lit_channels(), set())


# ---------------------------------------------------------------------------
# LED lifetime and abandonment
# ---------------------------------------------------------------------------

class TestLedLifetime(RunnerTestCase):

    def test_white_led_used_for_calibration_then_turned_off(self):
        runner = self.make_runner(led_start=1, led_end=1)
        self.run_to_completion(runner)
        white = capture_runner.WHITE_LED_CH
        self.assertIn((white, True), self.leds.history)
        self.assertIn((white, False), self.leds.history)

    def test_abandoning_generator_turns_off_lit_led(self):
        """Closing the generator mid-run must not leave an LED burning."""
        runner = self.make_runner(led_start=1, led_end=1)
        gen = runner.run()
        for progress in gen:
            # Stop as soon as a capture-phase LED is actually lit.
            if progress.phase == "capture" and self.leds.lit_channels() == {1}:
                break
        self.assertEqual(self.leds.lit_channels(), {1})
        gen.close()
        self.assertEqual(self.leds.lit_channels(), set())

    def test_abandoning_generator_leaves_a_readable_archive(self):
        """A partial run must still close the file, not leave it locked."""
        runner = self.make_runner(led_start=1, led_end=1)
        gen = runner.run()
        for progress in gen:
            if progress.step == "e0":     # first lit frame is in the archive
                break
        gen.close()
        with h5py.File(runner.out_path, "r") as f:
            self.assertIn("/leds/amber/lit/e0/B", f)

    def test_runner_does_not_stop_the_camera(self):
        """Hardware lifetime belongs to the caller, not the runner."""
        runner = self.make_runner(led_start=1, led_end=1)
        self.run_to_completion(runner)
        self.assertFalse(self.cam.stopped)

    def test_exactly_one_on_off_toggle_per_capture_led(self):
        """
        Each color gets exactly two LED-state-change waits: on, then off.
        The LED must not be toggled again between individual exposures —
        all lit frames are captured in the one "on" state, all dark frames
        in the one "off" state.
        """
        runner = self.make_runner(led_start=1, led_end=2)
        self.run_to_completion(runner)
        for led in (1, 2):
            events = [(ch, on) for ch, on in self.leds.history if ch == led]
            self.assertEqual(events, [(led, True), (led, False)],
                             f"LED {led} was toggled more than once: {events}")


# ---------------------------------------------------------------------------
# Focus modes
# ---------------------------------------------------------------------------

class TestFocus(RunnerTestCase):
    """
    Covers the manual and capped-autofocus paths.

    Both call _set_manual_lens(), which needs libcamera — unavailable on the
    desktop, so a stub is installed for these tests only. The runner's own
    behaviour is unchanged: without libcamera, manual focus still raises.
    """

    def setUp(self):
        super().setUp()
        self._real_libcamera = capture_runner._libcamera
        capture_runner._libcamera = SimpleNamespace(
            controls=SimpleNamespace(AfModeEnum=SimpleNamespace(Manual="Manual")))

    def tearDown(self):
        capture_runner._libcamera = self._real_libcamera
        super().tearDown()

    def test_manual_focus_sets_requested_lens_position(self):
        runner = self.make_runner(led_start=1, led_end=1, focus_diopter=5.0)
        self.run_to_completion(runner)
        meta = self.led_metadata(runner, "amber")
        self.assertEqual(meta["focus_mode"], "manual")
        self.assertEqual(meta["focus_diopters"], 5.0)
        self.assertEqual(self.cam.lens_position, 5.0)

    def test_autofocus_beyond_cap_is_capped(self):
        raw = capture_runner.MAX_AF_DIOPTER + 3.0   # above the cap
        self.cam = FakeCam(af_result=raw)
        runner = self.make_runner(led_start=1, led_end=1)
        self.run_to_completion(runner)
        meta = self.led_metadata(runner, "amber")
        self.assertEqual(meta["focus_mode"], "autofocus_capped")
        self.assertEqual(meta["focus_diopters"], capture_runner.MAX_AF_DIOPTER)
        self.assertEqual(meta["autofocus_raw_diopters"], raw)

    def test_manual_focus_without_libcamera_raises(self):
        """Guard the desktop failure mode explicitly rather than by accident."""
        capture_runner._libcamera = None
        runner = self.make_runner(led_start=1, led_end=1, focus_diopter=5.0)
        with self.assertRaises(RuntimeError):
            self.run_to_completion(runner)
        # The failure must still leave no LED burning, and — because focus
        # precedes Phase 2 — no empty archive either.
        self.assertEqual(self.leds.lit_channels(), set())
        self.assertEqual(list(self.out_root.iterdir()), [])


# ---------------------------------------------------------------------------
# Multi-LED runs and dark-frame sharing
# ---------------------------------------------------------------------------

class TestMultiLed(RunnerTestCase):

    def setUp(self):
        super().setUp()
        self.runner = self.make_runner(led_start=1, led_end=2)
        self.run_to_completion(self.runner)

    def test_each_led_produces_a_result_and_metadata(self):
        self.assertEqual([r.color for r in self.runner.results],
                         ["amber", "blue"])
        with self.archive(self.runner) as f:
            for color in ("amber", "blue"):
                self.assertIn(f"/leds/{color}/led_metadata", f)
                self.assertIn(f"/leds/{color}/jpeg_e3", f)

    def test_identical_exposures_share_one_dark_group(self):
        """
        Both LEDs see the same synthetic scene, so they compute the same
        exposures and must share dark frames rather than storing them twice.
        """
        with self.archive(self.runner) as f:
            dark_groups = sorted(f["/dark"])
            self.assertEqual(len(dark_groups), len(EXP_LABELS), dark_groups)
            for label in EXP_LABELS:
                np.testing.assert_array_equal(
                    f[f"/leds/amber/dark/after_{label}/B"][()],
                    f[f"/leds/blue/dark/after_{label}/B"][()])

    def test_dark_links_resolve_for_every_led_and_label(self):
        with self.archive(self.runner) as f:
            for color in ("amber", "blue"):
                for label in EXP_LABELS:
                    link = f[f"/leds/{color}/dark"].get(f"after_{label}", getlink=True)
                    self.assertIsInstance(link, h5py.SoftLink)
                    self.assertTrue(link.path.startswith("/dark/"))

    def test_progress_reports_reuse_of_shared_darks(self):
        """The second LED should report reusing darks, not storing them."""
        progress = self.run_to_completion(self.make_runner(led_start=1, led_end=2))
        messages = [p.message for p in progress if p.step.startswith("dark")]
        self.assertTrue(any("stored dark" in m for m in messages), messages)
        self.assertTrue(any("reused dark" in m for m in messages), messages)


# ---------------------------------------------------------------------------
# Normal capture path — captures only the exposures stored in the DB
# ---------------------------------------------------------------------------

class TestNormalMode(RunnerTestCase):
    """
    The normal path reads each LED's exposures from the `exposure` table for a
    chosen group and captures exactly those — no white-LED calibration, no e*
    search. Tests run against a throwaway copy of the real DB so the live one
    is never touched.
    """

    def setUp(self):
        super().setUp()
        import db_manager as db
        import exposure_repo
        self._db = db
        self._er = exposure_repo

        self._saved_dbname = db.DBNAME
        self._dbdir = tempfile.TemporaryDirectory()
        tmpdb = Path(self._dbdir.name) / "test.sqlite"
        shutil.copy(_ROOT / "src" / "colorimeter.sqlite", tmpdb)
        db.DBNAME = str(tmpdb)
        db.write_one("DELETE FROM exposure")
        # FakeLeds colors: 1 -> amber, 2 -> blue, 10 -> white.
        exposure_repo.write_all_groups("amber", exposure_repo.groups_from_estar(30_000))
        exposure_repo.write_all_groups("blue",  exposure_repo.groups_from_estar(20_000))

        # The normal path now sweeps config.capture_led_color_order(), not
        # led_start/led_end, so tests control which LEDs run by patching it —
        # single-color tests get "amber"; multi-color tests override below.
        self._real_color_order = capture_runner.config.capture_led_color_order
        capture_runner.config.capture_led_color_order = lambda: ["amber"]

    def tearDown(self):
        capture_runner.config.capture_led_color_order = self._real_color_order
        self._db.DBNAME = self._saved_dbname
        self._dbdir.cleanup()
        super().tearDown()

    def test_captures_exactly_the_stored_exposures(self):
        runner = self.make_runner(mode="normal", exposure_group="3")
        self.run_to_completion(runner)
        want = self._er.read_exposures("amber", "3")
        self.assertEqual(len(want), 3)
        self.assertEqual(runner.results[0].exposures_us, want)

    def test_archive_holds_e0_to_eN_lit_and_dark(self):
        runner = self.make_runner(mode="normal", exposure_group="2")
        self.run_to_completion(runner)
        with self.archive(runner) as f:
            for label in ("e0", "e1"):
                for ch in ("B", "Gb", "Gr", "R"):
                    self.assertIn(f"/leds/amber/lit/{label}/{ch}", f)
                    self.assertIn(f"/leds/amber/dark/after_{label}/{ch}", f)
            # group "2" -> exactly two exposures, no e2.
            self.assertNotIn("/leds/amber/lit/e2", f)

    def test_reaches_done(self):
        capture_runner.config.capture_led_color_order = lambda: ["amber", "blue"]
        runner = self.make_runner(mode="normal", exposure_group="1")
        progress = self.run_to_completion(runner)
        self.assertEqual(progress[-1].phase, "done")
        self.assertEqual(progress[-1].fraction, 1.0)

    def test_no_white_calibration_phase(self):
        """Normal mode must not switch the white calibration LED on at all."""
        runner = self.make_runner(mode="normal", exposure_group="1")
        self.run_to_completion(runner)
        white = capture_runner.WHITE_LED_CH
        self.assertNotIn((white, True), self.leds.history)

    def test_metadata_records_group_and_source(self):
        runner = self.make_runner(mode="normal", exposure_group="4")
        self.run_to_completion(runner)
        meta = self.led_metadata(runner, "amber")
        self.assertEqual(meta["mode"], "normal")
        self.assertEqual(meta["exposure_group"], "4")
        self.assertEqual(meta["exposure_source"], "exposure_table")
        self.assertEqual(len(meta["exposures_us"]), 4)

    def test_fallback_when_led_has_no_stored_exposures(self):
        """A LED with no rows for the group still captures, at the fallback."""
        self._er.delete_led("amber")
        runner = self.make_runner(mode="normal", exposure_group="3")
        self.run_to_completion(runner)
        meta = self.led_metadata(runner, "amber")
        self.assertEqual(meta["exposure_source"], "fallback")
        self.assertEqual(len(meta["exposures_us"]), 1)
        with self.archive(runner) as f:
            self.assertIn("/leds/amber/lit/e0/B", f)
            self.assertNotIn("/leds/amber/lit/e1", f)

    def test_all_leds_off_at_end(self):
        capture_runner.config.capture_led_color_order = lambda: ["amber", "blue"]
        runner = self.make_runner(mode="normal", exposure_group="2")
        self.run_to_completion(runner)
        self.assertEqual(self.leds.lit_channels(), set())


# ---------------------------------------------------------------------------
# Normal path: prefetch/capture split (dark-frame prefetch during reagent2)
# ---------------------------------------------------------------------------

class TestNormalModePrefetchSplit(RunnerTestCase):
    """
    run_prefetch() and run_capture() are meant to be driven as two separate
    generators on the same CaptureRunner instance, with an arbitrary pause
    between them (the reagent2 countdown, then whenever CAPTURE is pressed).
    run() chains them back-to-back for a caller that never splits the run.
    """

    def setUp(self):
        super().setUp()
        import db_manager as db
        import exposure_repo
        self._db = db
        self._er = exposure_repo

        self._saved_dbname = db.DBNAME
        self._dbdir = tempfile.TemporaryDirectory()
        tmpdb = Path(self._dbdir.name) / "test.sqlite"
        shutil.copy(_ROOT / "src" / "colorimeter.sqlite", tmpdb)
        db.DBNAME = str(tmpdb)
        db.write_one("DELETE FROM exposure")
        exposure_repo.write_all_groups("amber", exposure_repo.groups_from_estar(30_000))
        exposure_repo.write_all_groups("blue",  exposure_repo.groups_from_estar(20_000))

        self._real_color_order = capture_runner.config.capture_led_color_order
        capture_runner.config.capture_led_color_order = lambda: ["amber", "blue"]

    def tearDown(self):
        capture_runner.config.capture_led_color_order = self._real_color_order
        self._db.DBNAME = self._saved_dbname
        self._dbdir.cleanup()
        super().tearDown()

    def test_prefetch_then_capture_produces_before_and_after_darks(self):
        runner = self.make_runner(mode="normal", exposure_group="1")
        list(runner.run_prefetch())
        # Prefetch must leave the archive open for run_capture(), not closed.
        self.assertIsNotNone(runner._writer)
        list(runner.run_capture())
        with self.archive(runner) as f:
            dark_groups = sorted(f["/dark"])
            self.assertTrue(any(k.startswith("before_") for k in dark_groups))
            self.assertTrue(any(k.startswith("after_") for k in dark_groups))
            for color in ("amber", "blue"):
                self.assertIn(f"/leds/{color}/dark/before_e0", f)
                self.assertIn(f"/leds/{color}/dark/after_e0", f)

    def test_run_chains_prefetch_and_capture_for_fallback_path(self):
        """run() (no split) must still produce both before/after batches."""
        runner = self.make_runner(mode="normal", exposure_group="1")
        self.run_to_completion(runner)
        with self.archive(runner) as f:
            dark_groups = sorted(f["/dark"])
            self.assertTrue(any(k.startswith("before_") for k in dark_groups))
            self.assertTrue(any(k.startswith("after_") for k in dark_groups))

    def test_abandoning_prefetch_closes_the_archive(self):
        """
        Closing the prefetch generator mid-flight must clean up (unlike a
        normal completion, which deliberately leaves the archive open for
        run_capture() to continue) — no dangling writer, no unreadable file.
        """
        runner = self.make_runner(mode="normal", exposure_group="1")
        gen = runner.run_prefetch()
        while runner._writer is None:
            next(gen)
        gen.close()
        self.assertIsNone(runner._writer)
        with h5py.File(runner.out_path, "r"):
            pass   # must open cleanly — .close() left a valid archive

    def test_color_order_drives_the_sweep_not_led_start_end(self):
        """CaptureConfig.led_start/led_end no longer govern the normal path —
        only config.capture_led_color_order() (patched here) does."""
        capture_runner.config.capture_led_color_order = lambda: ["blue", "amber"]
        runner = self.make_runner(mode="normal", exposure_group="1")
        self.run_to_completion(runner)
        self.assertEqual([r.color for r in runner.results], ["blue", "amber"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
