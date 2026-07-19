#!/usr/bin/env python3
"""
Integration tests for CaptureRunner (src/capture_runner.py).

Drives a full capture suite against a synthetic camera and LED bank, with no
hardware and no picamera2. The fake camera returns frames whose brightness
scales with the requested exposure, so the exposure-bracketing and e3
refinement logic is genuinely exercised rather than stubbed out.

The final test closes the loop with step 1: the directory the runner produces
is fed to dev/build_h5_from_camtest8.py, proving the filenames the runner
writes are the filenames the converter expects.

Run from the project root:
    python test/test_capture_runner.py
or with unittest discovery:
    python -m unittest test.test_capture_runner
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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
        return rgb, bayer, {"ExposureTime": self._exposure_us}

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

    def set_channel(self, channel: int, on: bool) -> None:
        self.state[channel] = on
        self.history.append((channel, on))

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

    def test_writes_lit_and_dark_npy_for_every_label_and_channel(self):
        out = self.runner.out_dir
        for label in EXP_LABELS:
            for ch in ("B", "Gb", "Gr", "R"):
                lit = list(out.glob(f"camtest8_BOUND_amber_{label}_*ms_{ch}.npy"))
                dark = list(out.glob(f"camtest8_BOUND_black_{label}_*ms_{ch}.npy"))
                self.assertEqual(len(lit), 1, f"missing lit {label} {ch}")
                self.assertEqual(len(dark), 1, f"missing dark {label} {ch}")

    def test_roi_bound_crop_shape(self):
        arr = np.load(next(self.runner.out_dir.glob(
            "camtest8_BOUND_amber_e3_*ms_B.npy")))
        _, _, w, h = capture_runner.ROI_BOUND
        self.assertEqual(arr.shape, (h // 2, w // 2))

    def test_writes_e3_jpeg_only_once(self):
        jpegs = list(self.runner.out_dir.glob("*.jpg"))
        self.assertEqual([p.name for p in jpegs], ["camtest8_amber_e3.jpg"])

    def test_metadata_json_covers_all_labels(self):
        meta = json.loads(
            (self.runner.out_dir / "camtest8_amber_metadata.json").read_text())
        self.assertEqual(sorted(meta["computed_exposures"]), sorted(EXP_LABELS))
        self.assertEqual(sorted(meta["applied_exposures_us"]), sorted(EXP_LABELS))
        self.assertEqual(meta["led_color"], "amber")
        self.assertEqual(meta["focus_mode"], "autofocus")

    def test_result_recorded(self):
        self.assertEqual(len(self.runner.results), 1)
        result = self.runner.results[0]
        self.assertEqual(result.color, "amber")
        self.assertEqual(len(result.exposures_us), len(EXP_LABELS))

    def test_e3_converges_near_half_saturation(self):
        """The refinement loop must actually drive p50 toward HALF_SAT."""
        meta = json.loads(
            (self.runner.out_dir / "camtest8_amber_metadata.json").read_text())
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
        meta = json.loads(
            (runner.out_dir / "camtest8_amber_metadata.json").read_text())
        self.assertEqual(meta["focus_mode"], "manual")
        self.assertEqual(meta["focus_diopters"], 5.0)
        self.assertEqual(self.cam.lens_position, 5.0)

    def test_autofocus_beyond_cap_is_capped(self):
        self.cam = FakeCam(af_result=9.0)      # above MAX_AF_DIOPTER (7.5)
        runner = self.make_runner(led_start=1, led_end=1)
        self.run_to_completion(runner)
        meta = json.loads(
            (runner.out_dir / "camtest8_amber_metadata.json").read_text())
        self.assertEqual(meta["focus_mode"], "autofocus_capped")
        self.assertEqual(meta["focus_diopters"], capture_runner.MAX_AF_DIOPTER)
        self.assertEqual(meta["autofocus_raw_diopters"], 9.0)

    def test_manual_focus_without_libcamera_raises(self):
        """Guard the desktop failure mode explicitly rather than by accident."""
        capture_runner._libcamera = None
        runner = self.make_runner(led_start=1, led_end=1, focus_diopter=5.0)
        with self.assertRaises(RuntimeError):
            self.run_to_completion(runner)
        # The failure must still leave no LED burning.
        self.assertEqual(self.leds.lit_channels(), set())


# ---------------------------------------------------------------------------
# Multi-LED run and the handoff to the step-1 converter
# ---------------------------------------------------------------------------

class TestMultiLedAndConversion(RunnerTestCase):

    def test_two_leds_each_produce_a_result_and_metadata(self):
        runner = self.make_runner(led_start=1, led_end=2)
        self.run_to_completion(runner)
        self.assertEqual([r.color for r in runner.results], ["amber", "blue"])
        for color in ("amber", "blue"):
            self.assertTrue(
                (runner.out_dir / f"camtest8_{color}_metadata.json").exists())

    def test_output_directory_converts_to_hdf5(self):
        """
        End-to-end: what the runner writes, the step-1 converter must read.

        This is the check that catches a filename convention drifting apart
        from the converter's regexes.
        """
        import h5py
        from build_h5_from_camtest8 import build

        runner = self.make_runner(led_start=1, led_end=2)
        self.run_to_completion(runner)

        output_h5 = self.out_root / "suite.h5"
        build(runner.out_dir, output_h5)

        with h5py.File(output_h5, "r") as f:
            for color in ("amber", "blue"):
                self.assertIn(f"/leds/{color}", f)
                self.assertIn(f"/leds/{color}/jpeg_e3", f)
                for label in EXP_LABELS:
                    for ch in ("B", "Gb", "Gr", "R"):
                        self.assertIn(f"/leds/{color}/lit/{label}/{ch}", f)
            self.assertIn("/dark", f)


if __name__ == "__main__":
    unittest.main(verbosity=2)
