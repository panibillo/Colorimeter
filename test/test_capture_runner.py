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

        # The real 2 s LED warmups would dominate the test runtime.
        self._real_warmup = capture_runner.LED_WARMUP_S
        capture_runner.LED_WARMUP_S = 0.0

    def tearDown(self):
        capture_runner.LED_WARMUP_S = self._real_warmup
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
# Output location
# ---------------------------------------------------------------------------

class TestOutputLocation(unittest.TestCase):

    def setUp(self):
        self._saved = os.environ.pop(capture_runner.CAPTURE_DIR_ENV, None)

    def tearDown(self):
        os.environ.pop(capture_runner.CAPTURE_DIR_ENV, None)
        if self._saved is not None:
            os.environ[capture_runner.CAPTURE_DIR_ENV] = self._saved

    def test_default_is_relative_to_the_project(self):
        """Never a hardcoded home path — the Pi and desktop differ."""
        out = capture_runner.default_out_dir()
        self.assertEqual(out.name, capture_runner.CAPTURE_SUBDIR)
        self.assertEqual(out.parent, Path(capture_runner.__file__).resolve().parent.parent)

    def test_environment_variable_overrides_default(self):
        os.environ[capture_runner.CAPTURE_DIR_ENV] = "/tmp/somewhere/else"
        self.assertEqual(capture_runner.default_out_dir(),
                         Path("/tmp/somewhere/else"))

    def test_environment_variable_expands_user(self):
        os.environ[capture_runner.CAPTURE_DIR_ENV] = "~/capture_archives"
        self.assertEqual(capture_runner.default_out_dir(),
                         Path.home() / "capture_archives")

    def test_config_resolves_location_when_built_not_when_imported(self):
        """
        Regression guard: out_dir was once a plain dataclass default, baked in
        at import, so setting the variable afterwards silently had no effect.
        """
        os.environ[capture_runner.CAPTURE_DIR_ENV] = "/tmp/set/after/import"
        self.assertEqual(CaptureConfig().out_dir, Path("/tmp/set/after/import"))

    def test_explicit_out_dir_wins_over_environment(self):
        os.environ[capture_runner.CAPTURE_DIR_ENV] = "/tmp/ignored"
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
                    self.assertIn(f"/leds/amber/dark/{label}/{ch}", f)

    def test_roi_bound_crop_shape(self):
        with self.archive(self.runner) as f:
            arr = f["/leds/amber/lit/e3/B"][()]
        _, _, w, h = capture_runner.ROI_BOUND
        self.assertEqual(arr.shape, (h // 2, w // 2))

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
        self.cam = FakeCam(af_result=9.0)      # above MAX_AF_DIOPTER (7.5)
        runner = self.make_runner(led_start=1, led_end=1)
        self.run_to_completion(runner)
        meta = self.led_metadata(runner, "amber")
        self.assertEqual(meta["focus_mode"], "autofocus_capped")
        self.assertEqual(meta["focus_diopters"], capture_runner.MAX_AF_DIOPTER)
        self.assertEqual(meta["autofocus_raw_diopters"], 9.0)

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
                    f[f"/leds/amber/dark/{label}/B"][()],
                    f[f"/leds/blue/dark/{label}/B"][()])

    def test_dark_links_resolve_for_every_led_and_label(self):
        with self.archive(self.runner) as f:
            for color in ("amber", "blue"):
                for label in EXP_LABELS:
                    link = f[f"/leds/{color}/dark"].get(label, getlink=True)
                    self.assertIsInstance(link, h5py.SoftLink)
                    self.assertTrue(link.path.startswith("/dark/"))

    def test_progress_reports_reuse_of_shared_darks(self):
        """The second LED should report reusing darks, not storing them."""
        progress = self.run_to_completion(self.make_runner(led_start=1, led_end=2))
        messages = [p.message for p in progress if p.step.startswith("dark")]
        self.assertTrue(any("stored dark" in m for m in messages), messages)
        self.assertTrue(any("reused dark" in m for m in messages), messages)


if __name__ == "__main__":
    unittest.main(verbosity=2)
