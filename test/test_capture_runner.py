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
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "dev"))

import capture_h5
import capture_runner
import config_repo
import db_bootstrap
import roi
from capture_runner import CaptureConfig, CaptureRunner, EXP_LABELS

# Full-sensor frame size the ROI constants are defined against.
FRAME_W, FRAME_H = 2304, 1296

# Per-pixel gain scale chosen so the median pixel reaches HALF_SAT (512)
# at roughly 45 ms, matching the real captures this workflow was tuned on.
_MEDIAN_GAIN = 0.65
_TARGET_US   = 45_000
_SCALE       = capture_runner.HALF_SAT / (_MEDIAN_GAIN * _TARGET_US)


AMBER_GROUPS = {"1": [30_000], "2": [20_000, 45_000],
                "3": [20_000, 30_000, 45_000],
                "4": [14_000, 20_000, 30_000, 45_000]}
BLUE_GROUPS = {"1": [20_000], "3": [14_000, 20_000, 30_000]}


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
        # Every rendering switch, in order, as ("color", gains) / ("objective",
        # None) — so a test can check the objective rendering the Bayer data
        # depends on is put back after the closing photograph.
        self.rendering: list = []
        # Colour gains reach a frame `gain_lag` frames after they are set, and
        # every frame reports the gains it was rendered with — as libcamera's
        # ColourGains metadata does.  A test sets gain_lag to model a slow (or,
        # with a huge value, a stuck) pipeline.
        self.gain_lag = 1
        self._gains = (1.0, 1.0)
        self._pending = None
        self._pending_in = 0
        # Optional FakeLeds: when set, every frame records which LEDs were lit,
        # as (exposure_us, lit channels, reported gains).
        self.leds = None
        self.frames: list = []

    def set_exposure(self, exposure_us: int) -> None:
        self._exposure_us = int(exposure_us)

    def _set_gains(self, gains) -> None:
        self._pending = tuple(float(g) for g in gains)
        self._pending_in = self.gain_lag

    def set_color_rendering(self, wb_gains) -> None:
        self.rendering.append(("color", tuple(float(g) for g in wb_gains)))
        self._set_gains(wb_gains)

    def set_objective_rendering(self) -> None:
        self.rendering.append(("objective", None))
        self._set_gains((1.0, 1.0))

    def setup_camera(self) -> None:
        self.config_size = (FRAME_W, FRAME_H)
        self.camera = self.Device()

    def capture_frame(self):
        self.exposures_captured.append(self._exposure_us)
        if self._pending is not None:
            if self._pending_in <= 0:
                self._gains, self._pending = self._pending, None
            else:
                self._pending_in -= 1
        if self.leds is not None:
            self.frames.append((self._exposure_us,
                                frozenset(self.leds.lit_channels()), self._gains))
        bayer = np.clip(self._gain * self._exposure_us * _SCALE,
                        0, 1023).astype(np.uint16)
        rgb = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)
        # Real picamera2 returns native tuples for multi-valued controls;
        # the desktop mock returns their string reprs instead. Model the
        # hardware, since that is what the archive has to represent.
        meta = {
            "ExposureTime": self._exposure_us,
            "ColourGains":  self._gains,
            "SensorBlackLevels": (4096, 4096, 4096, 4096),
        }
        return rgb, bayer, meta

    # Held frames (BayerCam.capture_held / bayer_of / release_held): the
    # frame is drawn when it is captured, so the LEDs lit at that moment
    # decide what it shows, as on the camera.  `held` counts frames captured
    # but not yet converted or released, so a test can check none leak.
    held = 0

    def capture_held(self):
        _, bayer, meta = self.capture_frame()
        self.held += 1
        return bayer, meta

    def bayer_of(self, request):
        self.held -= 1
        return request

    def release_held(self, request) -> None:
        self.held -= 1

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
        self.cam.leds = self.leds

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
    """
    These used to drive $COLORIMETER_CAPTURE_DIR. That override is gone —
    capture.directory is a config row and the table is the only source — so
    they now change the row instead. The regression they guard is unchanged.
    """

    def setUp(self):
        import db_bootstrap
        import db_manager as db
        self._db = db
        self._saved_dbname = db.DBNAME
        self._tmpdir = tempfile.mkdtemp()
        db.DBNAME = str(Path(self._tmpdir) / "capture_config.sqlite")
        db_bootstrap.ensure(db.DBNAME)
        config_repo.load(reload=True)

    def tearDown(self):
        self._db.DBNAME = self._saved_dbname
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        config_repo.load(reload=True)

    def test_config_resolves_location_when_built_not_when_imported(self):
        """
        Regression guard: out_dir was once a plain dataclass default, baked in
        at import, so changing the setting afterwards silently had no effect.
        """
        config_repo.save("capture", "directory", "/tmp/set/after/import")
        self.assertEqual(CaptureConfig().out_dir, Path("/tmp/set/after/import"))

    def test_explicit_out_dir_wins_over_the_configured_one(self):
        config_repo.save("capture", "directory", "/tmp/ignored")
        config = CaptureConfig(out_dir=Path("/tmp/explicit"))
        self.assertEqual(config.out_dir, Path("/tmp/explicit"))


# ---------------------------------------------------------------------------
# ROI loading has no Python fallback left
# ---------------------------------------------------------------------------
# _load_rois() used to self-heal an empty or short `roi` table from a copy of
# the seed geometry kept in roi.py. That copy is gone — db_bootstrap seeds the
# table from provisioning/config_defaults.sql at every launch instead — so a
# role still unfilled by the time a run starts must now fail loudly.

class TestLoadRoisHasNoFallback(unittest.TestCase):

    def setUp(self):
        import db_bootstrap
        import db_manager as db
        self._db = db
        self._saved_dbname = db.DBNAME
        self._tmpdir = tempfile.mkdtemp()
        db.DBNAME = str(Path(self._tmpdir) / "roi_load.sqlite")
        db_bootstrap.ensure(db.DBNAME)
        self.cam = FakeCam()

    def tearDown(self):
        self._db.DBNAME = self._saved_dbname
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_a_freshly_bootstrapped_table_loads_fine(self):
        import roi as roi_mod
        rois = capture_runner._load_rois(self.cam)
        self.assertEqual(
            roi_mod.missing_roles(rois, roi_mod.required_roles()), [])

    def test_the_requirement_comes_from_the_config_table(self):
        # Not a constant in either module: an instrument commissioned around a
        # different card is given a different list, without a code change.
        import roi as roi_mod
        self.assertEqual(roi_mod.required_roles(),
                         ("bound", "sample", "white_balance"))

    def test_a_role_left_unfilled_raises_rather_than_substitutes(self):
        import roi as roi_mod
        self._db.write_one("UPDATE roi SET active = 0 WHERE role = ?",
                           (roi_mod.ROLE_WHITE_BALANCE,))
        with self.assertRaises(roi_mod.RoiMissing) as ctx:
            capture_runner._load_rois(self.cam)
        self.assertIn(roi_mod.ROLE_WHITE_BALANCE, str(ctx.exception))

    def test_an_empty_table_raises_naming_every_required_role(self):
        import roi as roi_mod
        self._db.write_one("UPDATE roi SET active = 0")
        with self.assertRaises(roi_mod.RoiMissing) as ctx:
            capture_runner._load_rois(self.cam)
        for role in roi_mod.required_roles():
            self.assertIn(role, str(ctx.exception))


# ---------------------------------------------------------------------------
# A complete single-LED run
# ---------------------------------------------------------------------------

class TestSingleLedRun(RunnerTestCase):

    def setUp(self):
        super().setUp()
        self.runner   = self.make_runner(led_colors=["amber"])
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
        import roi as roi_mod
        bound = roi_mod.by_role(self.runner.rois, roi_mod.ROLE_BOUND)
        self.assertEqual(arr.shape, (bound.h // 2, bound.w // 2))

    def test_rois_are_loaded_and_bayer_safe(self):
        """Every ROI the run used must be even, or layer extraction shifts phase."""
        import roi as roi_mod
        self.assertEqual(
            roi_mod.missing_roles(self.runner.rois,
                                  roi_mod.required_roles()), [],
            "a role the capture path cannot run without is unfilled")
        for name, r in sorted(self.runner.rois.items()):
            with self.subTest(roi=name):
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
        bound = roi_mod.by_role(self.runner.rois, roi_mod.ROLE_BOUND)
        for name, r in sorted(self.runner.rois.items()):
            if r is bound:
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
        runner = self.make_runner(led_colors=["amber"])
        self.run_to_completion(runner)
        white = capture_runner.WHITE_LED_CH
        self.assertIn((white, True), self.leds.history)
        self.assertIn((white, False), self.leds.history)

    def test_abandoning_generator_turns_off_lit_led(self):
        """Closing the generator mid-run must not leave an LED burning."""
        runner = self.make_runner(led_colors=["amber"])
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
        runner = self.make_runner(led_colors=["amber"])
        gen = runner.run()
        for progress in gen:
            if progress.step == "e0":     # first lit frame is in the archive
                break
        gen.close()
        with h5py.File(runner.out_path, "r") as f:
            self.assertIn("/leds/amber/lit/e0/B", f)

    def test_runner_does_not_stop_the_camera(self):
        """Hardware lifetime belongs to the caller, not the runner."""
        runner = self.make_runner(led_colors=["amber"])
        self.run_to_completion(runner)
        self.assertFalse(self.cam.stopped)

    def test_exactly_one_on_off_toggle_per_capture_led(self):
        """
        Each color gets exactly two LED-state-change waits: on, then off.
        The LED must not be toggled again between individual exposures —
        all lit frames are captured in the one "on" state, all dark frames
        in the one "off" state.
        """
        runner = self.make_runner(led_colors=["amber", "blue"])
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
        runner = self.make_runner(led_colors=["amber"], focus_diopter=5.0)
        self.run_to_completion(runner)
        meta = self.led_metadata(runner, "amber")
        self.assertEqual(meta["focus_mode"], "manual")
        self.assertEqual(meta["focus_diopters"], 5.0)
        self.assertEqual(self.cam.lens_position, 5.0)

    def test_autofocus_beyond_cap_is_capped(self):
        raw = capture_runner.MAX_AF_DIOPTER + 3.0   # above the cap
        self.cam = FakeCam(af_result=raw)
        runner = self.make_runner(led_colors=["amber"])
        self.run_to_completion(runner)
        meta = self.led_metadata(runner, "amber")
        self.assertEqual(meta["focus_mode"], "autofocus_capped")
        self.assertEqual(meta["focus_diopters"], capture_runner.MAX_AF_DIOPTER)
        self.assertEqual(meta["autofocus_raw_diopters"], raw)

    def test_manual_focus_without_libcamera_raises(self):
        """Guard the desktop failure mode explicitly rather than by accident."""
        capture_runner._libcamera = None
        runner = self.make_runner(led_colors=["amber"], focus_diopter=5.0)
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
        self.runner = self.make_runner(led_colors=["amber", "blue"])
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
        progress = self.run_to_completion(self.make_runner(led_colors=["amber", "blue"]))
        messages = [p.message for p in progress if p.step.startswith("dark")]
        self.assertTrue(any("stored dark" in m for m in messages), messages)
        self.assertTrue(any("reused dark" in m for m in messages), messages)

    def test_led_colors_sets_the_sweep_order(self):
        """The calibration path follows led_colors too, order included."""
        runner = self.make_runner(led_colors=["blue", "amber"])
        self.run_to_completion(runner)
        self.assertEqual([r.color for r in runner.results], ["blue", "amber"])


# ---------------------------------------------------------------------------
# The configured sweep list is operator-editable, so it is validated
# ---------------------------------------------------------------------------

class TestColorOrderResolution(RunnerTestCase):

    def test_names_resolve_to_channels_and_canonical_spelling(self):
        self.assertEqual(
            capture_runner._resolve_color_order(self.leds, ["blue", "amber"]),
            [(2, "blue"), (1, "amber")])

    def test_led_count_follows_the_list(self):
        self.assertEqual(CaptureConfig(led_colors=["amber", "blue"]).led_count, 2)
        self.assertEqual(CaptureConfig(led_colors=[]).led_count, 0)

    def test_an_unknown_color_is_named_in_the_error(self):
        with self.assertRaises(ValueError) as ctx:
            capture_runner._resolve_color_order(self.leds, ["amber", "chartreuse"])
        self.assertIn("chartreuse", str(ctx.exception))

    def test_a_typo_fails_before_the_archive_is_opened(self):
        """A bad name costs an error, not a run that dies with a half-written
        archive — the check runs before the writer is created."""
        runner = self.make_runner(led_colors=["amber", "chartreuse"])
        with self.assertRaises(ValueError):
            self.run_to_completion(runner)
        self.assertIsNone(runner._writer)


# ---------------------------------------------------------------------------
# Normal capture path — captures only the exposures stored in the DB
# ---------------------------------------------------------------------------

class CalibratedExposures(RunnerTestCase):
    """
    A runner test with an `exposure` table to read from.

    Every path that is not the calibration search needs stored exposures, so
    the fixture is shared: a throwaway copy of the real database, emptied of
    exposures and refilled with two LEDs the FakeLeds bank actually has
    (1 -> amber, 2 -> blue, 10 -> white). The live database is never touched.
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
        db_bootstrap.ensure(tmpdb)       # a live copy may predate new columns
        config_repo.load(reload=True)    # settings from this copy, not the live
        exposure_repo.write_all_groups("amber", AMBER_GROUPS, preimage_group="1")
        exposure_repo.write_all_groups("blue",  BLUE_GROUPS,  preimage_group="1")

    def tearDown(self):
        self._db.DBNAME = self._saved_dbname
        self._dbdir.cleanup()
        config_repo.load(reload=True)
        super().tearDown()

    def make_runner(self, **kwargs):
        """
        These tests are about the stored exposure groups, so they run the
        group rule unless a test asks for the strip rule (TestStripRule).
        """
        kwargs.setdefault("exposure_rule", capture_runner.RULE_GROUP)
        return super().make_runner(**kwargs)


class TestNormalMode(CalibratedExposures):
    """
    The normal path reads each LED's exposures from the `exposure` table for a
    chosen group and captures exactly those — no white-LED calibration, no e*
    search.
    """

    def make_runner(self, **kwargs):
        """Both capture paths sweep CaptureConfig.led_colors, so that is where
        a test says which LEDs run. Defaults to the one calibrated color."""
        kwargs.setdefault("led_colors", ["amber"])
        return super().make_runner(**kwargs)

    def test_captures_exactly_the_stored_exposures(self):
        runner = self.make_runner(mode="normal", exposure_group="3")
        self.run_to_completion(runner)
        want = self._er.read_exposures("amber", "3")
        self.assertEqual(len(want), 3)
        self.assertEqual(runner.results[0].exposures_us, want)

    def test_archive_holds_e0_to_eN_lit(self):
        runner = self.make_runner(mode="normal", exposure_group="2")
        self.run_to_completion(runner)
        with self.archive(runner) as f:
            for label in ("e0", "e1"):
                for ch in ("B", "Gb", "Gr", "R"):
                    self.assertIn(f"/leds/amber/lit/{label}/{ch}", f)
            # group "2" -> exactly two exposures, no e2.
            self.assertNotIn("/leds/amber/lit/e2", f)
            # The dark frames are the run's, not an LED's: no per-LED links.
            self.assertNotIn("/leds/amber/dark", f)

    def test_reaches_done(self):
        runner = self.make_runner(mode="normal", exposure_group="1",
                                  led_colors=["amber", "blue"])
        progress = self.run_to_completion(runner)
        self.assertEqual(progress[-1].phase, "done")
        self.assertEqual(progress[-1].fraction, 1.0)

    def test_no_white_calibration_phase(self):
        """
        Normal mode must not run a white calibration phase.

        The white LED does light once, at the very end, for the closing colour
        photograph — that is after every measurement is taken and stored, so
        it cannot influence a reading.  What this guards is the other end of
        the run: white must not come on before or during the sweep, because
        that is the Phase 0 calibration the normal path exists to avoid.
        """
        runner = self.make_runner(mode="normal", exposure_group="1")
        self.run_to_completion(runner)
        white = capture_runner.WHITE_LED_CH

        lit = [i for i, (ch, on) in enumerate(self.leds.history) if ch == white and on]
        self.assertEqual(len(lit), 1, "white should be lit exactly once")
        sweep = [i for i, (ch, _) in enumerate(self.leds.history) if ch != white]
        self.assertGreater(lit[0], max(sweep),
                           "white must light only after the LED sweep has ended")

    def test_closing_photo_is_stored(self):
        """The normal path takes the photograph too — see TestClosingPhoto."""
        runner = self.make_runner(mode="normal", exposure_group="1")
        self.run_to_completion(runner)
        bound = roi.by_role(runner.rois, roi.ROLE_BOUND)
        with h5py.File(runner.out_path, "r") as f:
            self.assertIn("photo", f)
            self.assertEqual(f["photo"].attrs["crop_w"], bound.w)

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

    def _drop_group(self, led, group):
        self._db.write_one(
            "DELETE FROM exposure WHERE LED = ? AND exposuregroup = ?", (led, group))

    def test_missing_group_steps_down_to_the_next_smaller(self):
        """A group calibration could not fit for one LED gives way to the
        largest smaller group it did fit, not to the single default exposure."""
        self._drop_group("amber", "3")
        runner = self.make_runner(mode="normal", exposure_group="3")
        self.run_to_completion(runner)
        meta = self.led_metadata(runner, "amber")
        self.assertEqual(meta["exposure_source"], "exposure_table")
        self.assertEqual(meta["exposure_group"], "2")
        self.assertEqual(meta["exposure_group_requested"], "3")
        self.assertEqual(runner.results[0].exposures_us,
                         self._er.read_exposures("amber", "2"))

    def test_step_down_skips_every_missing_group(self):
        for g in ("3", "2"):
            self._drop_group("amber", g)
        runner = self.make_runner(mode="normal", exposure_group="3")
        self.run_to_completion(runner)
        meta = self.led_metadata(runner, "amber")
        self.assertEqual(meta["exposure_group"], "1")
        self.assertEqual(len(meta["exposures_us"]), 1)

    def test_step_down_is_per_led(self):
        """Only the LED missing the group steps down; the other keeps it."""
        self._drop_group("amber", "3")
        runner = self.make_runner(mode="normal", exposure_group="3",
                                  led_colors=["amber", "blue"])
        self.run_to_completion(runner)
        self.assertEqual(self.led_metadata(runner, "amber")["exposure_group"], "2")
        self.assertEqual(self.led_metadata(runner, "blue")["exposure_group"], "3")

    def test_group_in_force_is_recorded_when_present(self):
        runner = self.make_runner(mode="normal", exposure_group="3")
        self.run_to_completion(runner)
        meta = self.led_metadata(runner, "amber")
        self.assertEqual(meta["exposure_group"], "3")
        self.assertEqual(meta["exposure_group_requested"], "3")

    def test_all_leds_off_at_end(self):
        runner = self.make_runner(mode="normal", exposure_group="2",
                                  led_colors=["amber", "blue"])
        self.run_to_completion(runner)
        self.assertEqual(self.leds.lit_channels(), set())


# ---------------------------------------------------------------------------
# Pre-image path — the unreacted strip, photographed into the same archive
# ---------------------------------------------------------------------------

class TestPreImage(CalibratedExposures):
    """
    MODE_PRE is the normal path with three deliberate differences, and each is
    about not putting light on a pad that has not reacted yet: fewer LEDs, one
    exposure shifted down the ladder, and no closing white-LED photograph.

    The fourth thing that matters is not about light at all — where the frames
    land. Both sweeps of one strip belong in one archive, so the pre-image
    creates the file and writes under /pre, and the measurement reopens it and
    writes at the root beside it.
    """

    def make_runner(self, **kwargs):
        kwargs.setdefault("led_colors", ["amber"])
        return super().make_runner(**kwargs)

    def pre_runner(self, **kwargs):
        """A pre-image run: the exposures flagged for it."""
        kwargs.setdefault("mode", capture_runner.MODE_PRE)
        kwargs.setdefault("exposure_group", "1")
        return self.make_runner(**kwargs)

    # ── where the frames land ───────────────────────────────────────────

    def test_frames_land_under_the_pre_section(self):
        runner = self.pre_runner()
        self.run_to_completion(runner)
        with self.archive(runner) as f:
            for ch in ("B", "Gb", "Gr", "R"):
                self.assertIn(f"/pre/leds/amber/lit/e0/{ch}", f)
            self.assertIn("/pre/meta", f)
            # Nothing of a pre-image belongs at the root, where a reader
            # looking for the measurement would find it and use it as one.
            self.assertNotIn("/leds", f)
            self.assertNotIn("/dark", f)

    def test_the_measurement_adds_to_the_preimage_archive(self):
        """One strip, one .h5 — the whole reason for the section prefix."""
        pre = self.pre_runner()
        self.run_to_completion(pre)

        measurement = self.make_runner(mode="normal", exposure_group="2",
                                       archive_path=pre.out_path)
        self.run_to_completion(measurement)

        self.assertEqual(measurement.out_path, pre.out_path)
        with self.archive(measurement) as f:
            self.assertIn("/pre/leds/amber/lit/e0/B", f)   # survived the append
            self.assertIn("/leds/amber/lit/e0/B", f)       # and the reading is in
            self.assertIn("/leds/amber/lit/e1/B", f)
            self.assertIn("/meta", f)
            self.assertIn("/pre/meta", f)

    def test_the_root_keeps_the_suite_id_of_whoever_opened_the_file(self):
        """
        The archive is named by the run that created it, not the last one to
        write into it — otherwise suite_id would silently slide forward and
        stop meaning what the /meta records say it means.
        """
        pre = self.pre_runner()
        self.run_to_completion(pre)
        with self.archive(pre) as f:
            opened = f.attrs["suite_id"]

        measurement = self.make_runner(mode="normal", exposure_group="1",
                                       archive_path=pre.out_path)
        self.run_to_completion(measurement)
        with self.archive(measurement) as f:
            self.assertEqual(f.attrs["suite_id"], opened)

    def test_a_measurement_without_a_preimage_still_opens_its_own_archive(self):
        """A strip that was not pre-imaged is not a broken run."""
        runner = self.make_runner(mode="normal", exposure_group="1")
        self.run_to_completion(runner)
        with self.archive(runner) as f:
            self.assertIn("/leds/amber/lit/e0/B", f)
            self.assertNotIn("/pre", f)

    # ── how little light it uses ────────────────────────────────────────

    def test_takes_the_exposure_flagged_for_the_preimage(self):
        runner = self.pre_runner()
        self.run_to_completion(runner)
        self.assertEqual(runner.results[0].exposures_us, [30_000])

    def test_a_changed_flag_changes_the_exposures_with_no_new_calibration(self):
        self._db.write_one(
            "UPDATE exposure SET preimage = 1 WHERE LED = 'amber' "
            "AND active = 1 AND exposuregroup = '3'")
        runner = self.pre_runner()
        self.run_to_completion(runner)
        self.assertEqual(runner.results[0].exposures_us,
                         self._er.read_preimage_exposures("amber"))
        self.assertGreater(len(runner.results[0].exposures_us), 1)

    def test_with_nothing_flagged_it_uses_the_middle_of_group_one(self):
        self._db.write_one("UPDATE exposure SET preimage = 0")
        runner = self.pre_runner()
        self.run_to_completion(runner)
        self.assertEqual(runner.results[0].exposures_us, [30_000])

    def test_a_measurement_ignores_the_flag(self):
        runner = self.make_runner(mode="normal", exposure_group="3")
        self.run_to_completion(runner)
        self.assertEqual(runner.results[0].exposures_us,
                         self._er.read_exposures("amber", "3"))

    def test_no_closing_photograph(self):
        """
        The photo is taken under the white LED — broadband, and so more blue
        on an unreacted pad than the whole rest of this path.
        """
        runner = self.pre_runner()
        self.run_to_completion(runner)
        with self.archive(runner) as f:
            self.assertNotIn("photo", f)
        self.assertNotIn(capture_runner.WHITE_LED_CH,
                         [ch for ch, on in self.leds.history if on])

    def test_the_leds_it_sweeps_are_the_ones_it_was_given(self):
        """Nothing else comes on — the LED list is the whole light budget."""
        runner = self.pre_runner(led_colors=["amber", "blue"])
        self.run_to_completion(runner)
        lit = {ch for ch, on in self.leds.history if on}
        self.assertEqual(lit, {self.leds.channel_of("amber"),
                               self.leds.channel_of("blue")})

    # ── what the archive says about itself ──────────────────────────────

    def test_the_metadata_names_the_path_that_wrote_it(self):
        """
        A reader must be able to tell a pre-image's frames from a
        measurement's without inferring it from the exposure group.
        """
        runner = self.pre_runner()
        self.run_to_completion(runner)
        with self.archive(runner) as f:
            meta = json.loads(f["/pre/leds/amber/led_metadata"][()])
        self.assertEqual(meta["mode"], capture_runner.MODE_PRE)

    def test_the_two_sweeps_are_told_apart_in_one_archive(self):
        pre = self.pre_runner()
        self.run_to_completion(pre)
        measurement = self.make_runner(mode="normal", exposure_group="1",
                                       archive_path=pre.out_path)
        self.run_to_completion(measurement)

        with self.archive(measurement) as f:
            pre_meta  = json.loads(f["/pre/leds/amber/led_metadata"][()])
            meas_meta = json.loads(f["/leds/amber/led_metadata"][()])
        self.assertEqual(pre_meta["mode"], capture_runner.MODE_PRE)
        self.assertEqual(meas_meta["mode"], capture_runner.MODE_NORMAL)


# ---------------------------------------------------------------------------
# Normal path: prefetch/capture split (camera setup and focus during reagent2)
# ---------------------------------------------------------------------------

class TestNormalModePrefetchSplit(CalibratedExposures):
    """
    run_prefetch() and run_capture() are meant to be driven as two separate
    generators on the same CaptureRunner instance, with an arbitrary pause
    between them (the reagent2 countdown, then whenever CAPTURE is pressed).
    run() chains them back-to-back for a caller that never splits the run.
    """

    def make_runner(self, **kwargs):
        """Defaults to both calibrated colors — the split is about what runs
        before the sweep and what runs after it, so it needs more than one
        LED."""
        kwargs.setdefault("led_colors", ["amber", "blue"])
        return super().make_runner(**kwargs)

    def test_prefetch_takes_no_photographs(self):
        """
        The prefetch stage opens the archive and stops.

        It used to take a whole "before" batch of dark frames here as well,
        and both batches were kept so a reader could difference them for
        sensor drift.  That question was answered; the batch was not worth 30
        settled captures and 5.3 MB every run.  What is left is the work that
        genuinely can happen before the operator is ready: camera setup and
        focus.
        """
        runner = self.make_runner(mode="normal", exposure_group="1")
        list(runner.run_prefetch())
        # Prefetch must leave the archive open for run_capture(), not closed.
        self.assertIsNotNone(runner._writer)
        with self.archive(runner) as f:
            self.assertNotIn("/dark", f)
            self.assertNotIn("/leds", f)

    def test_capture_produces_one_dark_pair_after_the_sweep(self):
        runner = self.make_runner(mode="normal", exposure_group="1")
        list(runner.run_prefetch())
        list(runner.run_capture())
        longest = max(us for r in runner.results for us in r.exposures_us)
        with self.archive(runner) as f:
            self.assertEqual(
                sorted(f["/dark"]),
                sorted({f"after_{capture_runner.DARK_SHORT_US}us",
                        f"after_{longest}us"}))

    def test_run_chains_prefetch_and_capture_for_fallback_path(self):
        """run() (no split) must still produce the one dark batch."""
        runner = self.make_runner(mode="normal", exposure_group="1")
        self.run_to_completion(runner)
        with self.archive(runner) as f:
            dark_groups = sorted(f["/dark"])
            self.assertTrue(dark_groups)
            self.assertTrue(all(k.startswith("after_") for k in dark_groups))

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

    def test_led_colors_sets_the_sweep_order(self):
        """The sweep follows CaptureConfig.led_colors, order included."""
        runner = self.make_runner(mode="normal", exposure_group="1",
                                  led_colors=["blue", "amber"])
        self.run_to_completion(runner)
        self.assertEqual([r.color for r in runner.results], ["blue", "amber"])


# ---------------------------------------------------------------------------
# Normal path: two dark frames as statistics, white once and last
# ---------------------------------------------------------------------------

class TestDarkPairAndWhite(CalibratedExposures):
    """
    A sweep keeps two dark frames, as statistics only, and takes the white LED
    once, last — after the darks, because its phosphor can glow on — in a
    frame that is both its measurement and the colour photograph.  Changing the
    colour rendering is checked frame by frame, going in and coming out.
    """

    # Gains distinct from the neutral (1.0, 1.0), so a frame that still
    # reports the old rendering can be told from one that reports the new.
    GAINS = (1.8, 1.4)

    def setUp(self):
        super().setUp()
        patcher = unittest.mock.patch.object(
            capture_runner.config, "capture_photo_wb_gains", return_value=self.GAINS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_runner(self, **kwargs):
        kwargs.setdefault("led_colors", ["amber", "blue", "white"])
        kwargs.setdefault("mode", "normal")
        kwargs.setdefault("exposure_group", "3")
        return super().make_runner(**kwargs)

    def dark_frames(self):
        """Frames taken with every LED off, after the sweep began."""
        return [f for f in self.cam.frames if not f[1]]

    # ── darks ────────────────────────────────────────────────────────

    def test_darks_are_statistics_only(self):
        runner = self.make_runner()
        self.run_to_completion(runner)
        with self.archive(runner) as f:
            for group in f["/dark"]:
                for ch in ("R", "Gr", "Gb", "B"):
                    attrs = dict(f[f"/dark/{group}/{ch}"].attrs)
                    self.assertTrue(capture_h5.is_omitted(attrs), (group, ch))
                    self.assertEqual(f[f"/dark/{group}/{ch}"].size, 0)
                    for key in ("raw_min", "raw_mean", "raw_max"):
                        self.assertIn(key, attrs)
                    self.assertLessEqual(attrs["raw_min"], attrs["raw_mean"])
                    self.assertLessEqual(attrs["raw_mean"], attrs["raw_max"])

    def test_darks_are_the_shortest_tested_and_the_longest_lit(self):
        runner = self.make_runner()
        self.run_to_completion(runner)
        longest = max(us for r in runner.results for us in r.exposures_us)
        self.assertEqual(runner._dark_us,
                         sorted({capture_runner.DARK_SHORT_US, longest}))

    def test_dark_level_reads_the_statistics(self):
        """The analysis's one reader of the dark keeps working unchanged."""
        from hdf5read import Hdf5Reader
        runner = self.make_runner()
        self.run_to_completion(runner)
        with Hdf5Reader(str(runner.out_path)) as r:
            level, us = capture_h5.dark_level(r, "B")
        self.assertEqual(us, capture_runner.DARK_SHORT_US)
        self.assertGreater(level, 0)

    def test_darks_come_after_the_coloured_leds_and_before_white(self):
        runner = self.make_runner()
        self.run_to_completion(runner)
        white = capture_runner.WHITE_LED_CH
        on = [ch for ch, state in self.leds.history if state]
        self.assertEqual(on[-1], white, "white must be the last LED lit")
        self.assertEqual(on.count(white), 1, "white must be lit exactly once")
        # Index of the first frame lit by white, and of the dark frames.
        first_white = next(i for i, fr in enumerate(self.cam.frames) if white in fr[1])
        last_coloured = max(i for i, fr in enumerate(self.cam.frames)
                            if fr[1] and white not in fr[1])
        dark_idx = [i for i, fr in enumerate(self.cam.frames)
                    if not fr[1] and last_coloured < i < first_white]
        took = {self.cam.frames[i][0] for i in dark_idx}
        self.assertTrue(set(runner._dark_us) <= took, (runner._dark_us, took))

    def test_preimage_takes_its_darks_before_its_first_led(self):
        runner = self.make_runner(mode=capture_runner.MODE_PRE,
                                  exposure_group="1", led_colors=["amber"])
        self.run_to_completion(runner)
        first_lit = next(i for i, fr in enumerate(self.cam.frames) if fr[1])
        before = {fr[0] for fr in self.cam.frames[:first_lit]}
        self.assertTrue(set(runner._dark_us) <= before)
        with self.archive(runner) as f:
            self.assertTrue(all(g.startswith("before_") for g in f["/pre/dark"]))
            self.assertNotIn("/pre/photo", f)
            self.assertNotIn("/photo", f)

    # ── white ────────────────────────────────────────────────────────

    def test_white_is_one_frame_at_group_1(self):
        runner = self.make_runner()
        self.run_to_completion(runner)
        with self.archive(runner) as f:
            self.assertEqual(sorted(f["/leds/white/lit"]), ["e0"])
            self.assertIn("/photo", f)
        meta = self.led_metadata(runner, "white")
        self.assertEqual(meta["exposure_group"], "1")
        self.assertEqual(meta["exposure_group_requested"], "3")
        self.assertEqual(meta["rendering"], "colour-balanced")
        self.assertEqual([r.color for r in runner.results],
                         ["amber", "blue", "white"])

    def test_white_left_out_of_the_list_is_not_measured_but_photographed(self):
        runner = self.make_runner(led_colors=["amber"])
        self.run_to_completion(runner)
        with self.archive(runner) as f:
            self.assertNotIn("/leds/white", f)
            self.assertIn("/photo", f)

    def test_white_measured_without_photo_stays_objective(self):
        with unittest.mock.patch.object(
                capture_runner.config, "capture_photo_enabled", return_value=False):
            runner = self.make_runner()
            self.run_to_completion(runner)
        with self.archive(runner) as f:
            self.assertIn("/leds/white/lit/e0", f)
            self.assertNotIn("/photo", f)
        self.assertEqual(self.cam.rendering, [])

    # ── first-frame evidence, going in and coming out ───────────────

    def photo_attrs(self, runner):
        with self.archive(runner) as f:
            return dict(f["/photo"].attrs)

    def test_gains_are_waited_for_and_recorded(self):
        self.cam.gain_lag = 2
        runner = self.make_runner()
        self.run_to_completion(runner)
        a = self.photo_attrs(runner)
        want = self.GAINS
        self.assertTrue(a["gains_confirmed"])
        self.assertEqual(a["gain_frames"], 3)       # 2 frames late, kept the 3rd
        self.assertAlmostEqual(a["reported_gain_r"], want[0])
        self.assertNotIn("restore_frames", a)

    def test_first_frame_is_used_when_gains_apply_at_once(self):
        self.cam.gain_lag = 0
        runner = self.make_runner()
        self.run_to_completion(runner)
        self.assertEqual(self.photo_attrs(runner)["gain_frames"], 1)

    def test_no_frame_is_spent_putting_neutral_back(self):
        """Neutral is requested as white goes out; the white frame is the last."""
        runner = self.make_runner()
        self.run_to_completion(runner)
        white = capture_runner.WHITE_LED_CH
        self.assertIn(white, self.cam.frames[-1][1])
        self.assertEqual(self.cam.rendering[-1], ("objective", None))

    def test_the_next_runs_first_frame_waits_for_neutral(self):
        self.cam.gain_lag = 2
        self.run_to_completion(self.make_runner())
        # The neutral gains were requested but no frame has shown them yet.
        before = len(self.cam.frames)
        runner = self.make_runner()
        self.run_to_completion(runner)
        meta = self.led_metadata(runner, "amber")
        self.assertEqual(meta["render_evidence"],
                         {"neutral_frames": 3, "neutral_confirmed": True})
        amber = [fr[2] for fr in self.cam.frames[before:] if 1 in fr[1]]
        self.assertEqual(amber[:2], [self.GAINS, self.GAINS])  # discarded
        self.assertEqual(set(amber[2:]), {(1.0, 1.0)})         # every kept one

    def test_neutral_is_checked_once_and_costs_nothing_when_in_force(self):
        runner = self.make_runner()
        self.run_to_completion(runner)
        self.assertEqual(self.led_metadata(runner, "amber")["render_evidence"],
                         {"neutral_frames": 1, "neutral_confirmed": True})
        self.assertNotIn("render_evidence", self.led_metadata(runner, "blue"))

    def test_gains_that_never_arrive_are_recorded_not_fatal(self):
        self.cam.gain_lag = 10_000
        runner = self.make_runner()
        progress = self.run_to_completion(runner)
        self.assertEqual(progress[-1].phase, "done")
        a = self.photo_attrs(runner)
        self.assertFalse(a["gains_confirmed"])
        self.assertEqual(a["gain_frames"], capture_runner.MAX_SETTLE_FRAMES)
        with self.archive(runner) as f:
            self.assertIn("/leds/white/lit/e0", f)


    # ── status counts ──────────────────────────────────────────────

    def test_each_stored_image_says_which_of_its_kind_it_is(self):
        """'Capturing amber 2 of 3': counted per LED, and the darks apart."""
        runner = self.make_runner()
        stored = [(p.color if p.step != "dark" else "dark",
                   p.image_index, p.image_total)
                  for p in self.run_to_completion(runner) if p.image_total]
        lit = {r.color: len(r.exposures_us) for r in runner.results}
        want = [(c, k, lit[c]) for c in ("amber", "blue")
                for k in range(1, lit[c] + 1)]
        n_dark = len(runner._dark_us)
        want += [("dark", k, n_dark) for k in range(1, n_dark + 1)]
        want += [("white", 1, 1)]
        self.assertEqual(stored, want)


# ---------------------------------------------------------------------------
# Closing colour photograph
# ---------------------------------------------------------------------------

class TestClosingPhoto(RunnerTestCase):
    """
    The one colour-balanced frame a run takes, after all measurement is done.

    Two things matter and are checked separately: that the photograph is
    stored cropped to ROLE_BOUND (so it frames exactly what was measured, and
    costs a fraction of a frame), and that the neutral rendering every Bayer
    measurement depends on is put back afterwards.
    """

    def bound(self, runner):
        """The ROLE_BOUND rect the run actually used."""
        return roi.by_role(runner.rois, roi.ROLE_BOUND)

    def test_photo_is_stored_cropped_to_bound(self):
        runner = self.make_runner(led_colors=["amber"])
        self.run_to_completion(runner)
        bound = self.bound(runner)

        with self.archive(runner) as f:
            self.assertIn("photo", f, "the archive should hold a closing photo")
            attrs = dict(f["photo"].attrs)
            jpeg = f["photo"][()].tobytes()

        self.assertEqual(
            (attrs["crop_x"], attrs["crop_y"], attrs["crop_w"], attrs["crop_h"]),
            (bound.x, bound.y, bound.w, bound.h))

        # The stored bytes must really be the crop, not a full frame labelled
        # with a crop rect — decode and measure rather than trusting the attrs.
        import cv2
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual((img.shape[1], img.shape[0]), (bound.w, bound.h))

    def test_photo_is_smaller_than_the_frame_it_came_from(self):
        """The point of cropping: the stored image is a fraction of a frame."""
        runner = self.make_runner(led_colors=["amber"])
        self.run_to_completion(runner)
        bound = self.bound(runner)
        self.assertLess(bound.w * bound.h, 0.25 * FRAME_W * FRAME_H)

    def test_objective_rendering_is_restored(self):
        """
        The colour balance must not outlive the photograph.

        If it did, the next run's RGB stream would be tinted — so the last
        rendering switch of a run has to be back to objective.
        """
        runner = self.make_runner(led_colors=["amber"])
        self.run_to_completion(runner)
        self.assertIn(("objective", None), self.cam.rendering)
        self.assertEqual(self.cam.rendering[-1], ("objective", None))
        self.assertEqual(self.cam.rendering[0][0], "color")

    def test_photo_uses_the_configured_gains(self):
        runner = self.make_runner(led_colors=["amber"])
        self.run_to_completion(runner)
        expected = config_repo.capture_photo_wb_gains()
        self.assertEqual(self.cam.rendering[0], ("color", tuple(expected)))
        with self.archive(runner) as f:
            attrs = dict(f["photo"].attrs)
        self.assertAlmostEqual(attrs["colour_gain_r"], expected[0])
        self.assertAlmostEqual(attrs["colour_gain_b"], expected[1])

    def test_photo_is_taken_with_the_white_led_lit(self):
        runner = self.make_runner(led_colors=["amber"])
        self.run_to_completion(runner)
        white = capture_runner.WHITE_LED_CH
        self.assertIn((white, True), self.leds.history)
        self.assertFalse(self.leds.lit_channels(),
                         "every LED must be off once the run ends")

    def test_disabled_by_config(self):
        with unittest.mock.patch.object(
                capture_runner.config, "capture_photo_enabled", return_value=False):
            runner = self.make_runner(led_colors=["amber"])
            self.run_to_completion(runner)
        with self.archive(runner) as f:
            self.assertNotIn("photo", f)
        self.assertEqual(self.cam.rendering, [],
                         "a disabled photo should not touch rendering at all")

    def test_unreadable_config_does_not_abort_the_run(self):
        """
        An instrument whose config predates this feature still finishes.

        capture.photo_enabled is read from the database, so it can raise on a
        unit that has not been re-bootstrapped.  That must cost the photo and
        nothing else — the measurements are already written by then.
        """
        with unittest.mock.patch.object(
                capture_runner.config, "capture_photo_enabled",
                side_effect=config_repo.ConfigMissing("capture.photo_enabled")):
            runner = self.make_runner(led_colors=["amber"])
            progress = self.run_to_completion(runner)

        self.assertEqual(progress[-1].phase, "done")
        with self.archive(runner) as f:
            self.assertNotIn("photo", f)
            self.assertIn("/leds/amber", f)

    def test_failure_does_not_abort_the_run(self):
        """
        A photograph is the last and least important thing a run does.

        Everything measured is already written by the time it is attempted, so
        a camera that fails here must cost the photo and nothing else.
        """
        self.cam.set_color_rendering = lambda *_: (_ for _ in ()).throw(
            RuntimeError("ISP refused"))
        runner = self.make_runner(led_colors=["amber"])
        progress = self.run_to_completion(runner)

        self.assertEqual(progress[-1].phase, "done")
        with self.archive(runner) as f:
            self.assertNotIn("photo", f)
            self.assertIn("/leds/amber", f)   # the measurements survived
        self.assertFalse(self.leds.lit_channels())



# ---------------------------------------------------------------------------
# Pad finding (detour stage 1): one photo_red frame before the sweep
# ---------------------------------------------------------------------------

import padfind                                    # noqa: E402

PHOTO_RED_CH = 4


class LedsWithPhotoRed(FakeLeds):
    """FakeLeds plus a photo_red LED, which pad finding lights."""

    _COLORS = {1: "amber", 2: "blue", PHOTO_RED_CH: "photo_red", 10: "white"}


def _coverage(rows, cols, x0, x1, y0, y1, sub=10):
    """
    Fraction of each layer pixel inside x0 <= x < x1, y0 <= y < y1, with
    pixel (r, c) centered on (r, c) -- padfind's convention.
    """
    ys = (np.arange(rows * sub) + 0.5) / sub - 0.5
    xs = (np.arange(cols * sub) + 0.5) / sub - 0.5
    inside = (((ys >= y0) & (ys < y1))[:, None]
              & ((xs >= x0) & (xs < x1))[None, :])
    return inside.reshape(rows, sub, cols, sub).mean(axis=(1, 3))


class CassetteCam(FakeCam):
    """
    FakeCam whose R pixels show a cassette with a pad while photo_red is lit.

    The scene is drawn in the R Bayer layer of the bound crop, around the
    sample ROI's middle column, the way the cassette holders put it: dark
    frame (field 0.1), white window (1.6), dark bands beside the pad (0.2),
    the pad (1.2), and the two gray chips (1.0).  `shift` moves the pad, to
    stand for a strip that moved between the pre-image and the post-image.
    """

    WIN_TOP, WIN_ROWS = 20, 68           # window rows, layer px in the crop
    PAD_DOWN = 4.4                       # pad top below the window top

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.shift = (0.0, 0.0)          # (right, down), layer px
        self.truth = None                # (left, right, top, bottom)

    def _scene(self):
        rois = roi.load_active(FRAME_W, FRAME_H, None)
        bound = roi.by_role(rois, roi.ROLE_BOUND)
        rows, cols = bound.h // 2, bound.w // 2
        sample = roi.to_layer_rect_within(roi.by_role(rois, roi.ROLE_SAMPLE),
                                          bound)
        xc = sample.x + sample.w // 2
        left = xc - padfind.WIDTH_FIXED / 2 + 0.3 + self.shift[0]
        top = self.WIN_TOP + self.PAD_DOWN + self.shift[1]
        right, bot = left + padfind.WIDTH_FIXED, top + padfind.HEIGHT_FIXED
        window = _coverage(rows, cols, xc - 30, xc + 30, self.WIN_TOP,
                           self.WIN_TOP + self.WIN_ROWS)
        f = (0.1 + 1.5 * window
             - 1.4 * window * _coverage(rows, cols, 0, cols, top, bot)
             + 1.0 * _coverage(rows, cols, left, right, top, bot))
        for chip in roi.all_by_role(rois, roi.ROLE_WHITE_BALANCE):
            c = roi.to_layer_rect_within(chip, bound)
            f[c.y:c.y + c.h, c.x:c.x + c.w] = 1.0
        self.truth = np.array([left, right, top, bot])
        return bound, (64.0 + 300.0 * f).astype(np.uint16)

    def capture_frame(self):
        rgb, bayer, meta = super().capture_frame()
        if self.leds is not None and PHOTO_RED_CH in self.leds.lit_channels():
            bound, r_layer = self._scene()
            crop = bayer[bound.y:bound.y + bound.h, bound.x:bound.x + bound.w]
            crop[0::2, 0::2] = r_layer           # RGGB: R at even row/col
        return rgb, bayer, meta


def _edges(fit):
    """(left, right, top, bottom), the sides at the pad's middle row."""
    y = 0.5 * (fit.top + fit.bot)
    return np.array([fit.aL + fit.m * (y - fit.yc),
                     fit.aR + fit.m * (y - fit.yc), fit.top, fit.bot])


class TestPadFinding(CalibratedExposures):
    """
    Each sweep finds the pad from one photo_red frame before its own photos:
    the LED flashes once with no warm-up, the fit is stored in the sweep's
    `pad` group, and a post-image is guided by the pre-image's fit.
    """

    def setUp(self):
        super().setUp()
        self.cam = CassetteCam()
        self.leds = LedsWithPhotoRed()
        self.cam.leds = self.leds

    def make_runner(self, **kwargs):
        kwargs.setdefault("led_colors", ["amber"])
        kwargs.setdefault("exposure_group", "1")
        return super().make_runner(**kwargs)

    def test_pre_image_finds_and_stores_the_pad(self):
        runner = self.make_runner(mode=capture_runner.MODE_PRE)
        self.run_to_completion(runner)
        self.assertEqual(runner.pad_problem, "")
        np.testing.assert_allclose(_edges(runner.pad_before), self.cam.truth,
                                   atol=0.15)
        # The offset is the config row's, whose factory value is OFFSET_PRE.
        np.testing.assert_allclose(runner.pad.offset, padfind.OFFSET_PRE)
        np.testing.assert_allclose(runner.pad.offset,
                                   config_repo.capture_padfind_offset("pre"))
        with self.archive(runner) as f:
            g = f["/pre/pad"]
            self.assertEqual(g.attrs["led"], "photo_red")
            self.assertEqual(g["R"].shape, runner.pad.shape)
            for name in ("total_s", "led_on_s", "fit_s"):
                self.assertGreaterEqual(float(g.attrs[name]), 0.0)
            self.assertIn("before_offset", g)
            self.assertIn("after_offset", g)
            self.assertNotIn("/pad", f)          # nothing at the root

    def test_photo_red_flashes_once_before_the_sweep(self):
        """On, off, then the sweep's own LED: no warm-up wait is needed."""
        runner = self.make_runner(mode=capture_runner.MODE_PRE)
        self.run_to_completion(runner)
        switches = [h for h in self.leds.history if h[1]]
        self.assertEqual(switches[0], (PHOTO_RED_CH, True))
        self.assertEqual(sum(1 for h in switches if h[0] == PHOTO_RED_CH), 1)
        i_off = self.leds.history.index((PHOTO_RED_CH, False))
        i_amber = self.leds.history.index((1, True))
        self.assertLess(i_off, i_amber)
        # The exposure was settled in the dark, so every photo_red frame
        # was taken at the pad-finding exposure_us.
        lit = [fr for fr in self.cam.frames if PHOTO_RED_CH in fr[1]]
        self.assertTrue(lit)
        self.assertTrue(all(fr[0] == config_repo.capture_padfind_exposure_us()
                            for fr in lit))

    def test_led_is_off_before_the_frame_is_converted(self):
        """
        The kept frame is converted only after photo_red is off, the frame
        dropped while waiting is released unconverted, and none are left
        held.  FakeCam reports no SensorTimestamp, so the second frame is
        kept.
        """
        lit_at_convert = []
        convert = self.cam.bayer_of

        def bayer_of(request):
            lit_at_convert.append(PHOTO_RED_CH in self.leds.lit_channels())
            return convert(request)

        self.cam.bayer_of = bayer_of
        runner = self.make_runner(mode=capture_runner.MODE_PRE)
        self.run_to_completion(runner)
        self.assertEqual(lit_at_convert, [False])
        self.assertEqual(self.cam.held, 0)
        with self.archive(runner) as f:
            g = f["/pre/pad"]
            self.assertEqual(int(g.attrs["frames_lit"]), 2)
            self.assertEqual(g.attrs["lit_frame_rule"], "second frame")

    def test_post_image_is_guided_by_the_pre_image(self):
        pre = self.make_runner(mode=capture_runner.MODE_PRE)
        self.run_to_completion(pre)
        self.cam.shift = (1.0, 0.6)              # the strip moved a little
        post = self.make_runner(mode="normal", archive_path=pre.out_path)
        self.run_to_completion(post)
        self.assertEqual(post.pad_problem, "")
        self.assertEqual(post.pad_before.side_rule, "guided")
        np.testing.assert_allclose(_edges(post.pad_before), self.cam.truth,
                                   atol=0.15)
        np.testing.assert_allclose(post.pad.offset, padfind.OFFSET_POST)
        with self.archive(post) as f:
            self.assertEqual(int(f["/pad"].attrs["guided_by_pre"]), 1)
            self.assertIn("/pre/pad/before_offset", f)

    def test_a_pad_that_cannot_be_found_does_not_stop_the_run(self):
        self.cam = FakeCam()                     # no cassette in the frame
        self.cam.leds = self.leds
        runner = self.make_runner(mode=capture_runner.MODE_PRE)
        self.run_to_completion(runner)
        self.assertIsNone(runner.pad)
        self.assertTrue(runner.pad_problem.startswith("pad not found"))
        with self.archive(runner) as f:
            self.assertIn("error", f["/pre/pad"].attrs)
            self.assertIn("/pre/leds/amber/lit/e0/R", f)   # sweep went on
        self.assertFalse(self.leds.lit_channels())

    def test_no_photo_red_led_skips_pad_finding(self):
        self.leds = FakeLeds()
        self.cam.leds = self.leds
        runner = self.make_runner(mode=capture_runner.MODE_PRE)
        self.run_to_completion(runner)
        self.assertIn("no photo_red", runner.pad_problem)
        with self.archive(runner) as f:
            self.assertNotIn("/pre/pad", f)



# ---------------------------------------------------------------------------
# The strip exposure rule (detour stage 3; src/exposure_rule.py)
# ---------------------------------------------------------------------------

import exposure_rule                              # noqa: E402


class LinearCam(FakeCam):
    """
    FakeCam with a real dark level: raw = 64 + gain x exposure_us x _SCALE,
    clipped at 1023, as the sensor is.  `dim`, when set, is a full-frame
    array that multiplies the gain (to darken part of the scene).
    """

    DARK = 64.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dim = None

    def capture_frame(self):
        rgb, _, meta = super().capture_frame()
        g = self._gain if self.dim is None else self._gain * self.dim
        bayer = np.clip(self.DARK + g * self._exposure_us * _SCALE,
                        0, 1023).astype(np.uint16)
        return rgb, bayer, meta


def _sample_rect():
    """The sample ROI, full-frame px, from the test database."""
    rois = roi.load_active(FRAME_W, FRAME_H, None)
    return roi.by_role(rois, roi.ROLE_SAMPLE)


class TestStripRule(CalibratedExposures):
    """
    Each LED's exposure_us is re-aimed on the test region (pad + gray
    chips) until nothing there clips and the brightest pixel is at least
    80 % of 1023; then protocol.multiple_exposure_count frames at that
    Tus, and Bus frames only when an analysis channel falls below the noise
    floor.  Every setting is read from the test database's config table.

    With LinearCam the brightest test-region pixel is about
    64 + exposure_us x 0.0175 raw DN, so amber's group-1 seed of 30000 us
    reads about 590 (dim) and 90000 us clips.
    """

    def setUp(self):
        super().setUp()
        self.cam = LinearCam()
        self.leds = LedsWithPhotoRed()
        self.cam.leds = self.leds

    def make_runner(self, **kwargs):
        kwargs.setdefault("exposure_rule", capture_runner.RULE_STRIP)
        kwargs.setdefault("led_colors", ["amber"])
        return super().make_runner(**kwargs)

    def led_record(self, runner, color="amber", section=""):
        with self.archive(runner) as f:
            return json.loads(
                f[f"{section}/leds/{color}/led_metadata"][()])["strip_rule"]

    def test_a_dim_seed_is_scaled_up_once(self):
        runner = self.make_runner(mode="normal")
        self.run_to_completion(runner)
        rec = self.led_record(runner)
        self.assertEqual(rec["seed_source"], "group 1")
        self.assertEqual(rec["seed_us"], 30_000)
        verdicts = [a["verdict"] for a in rec["attempts"]]
        self.assertEqual(verdicts, ["dim", "accept"])
        self.assertEqual(rec["tus_status"], "ok")
        top = max(st["max_dn"] for st in rec["attempts"][-1]["stats"].values())
        self.assertGreaterEqual(top, 0.80 * 1023)
        self.assertLess(top, 1023)

    def test_a_clipped_seed_steps_down_by_0_7(self):
        self._er.write_all_groups("amber", {"1": [90_000]})
        runner = self.make_runner(mode="normal")
        self.run_to_completion(runner)
        rec = self.led_record(runner)
        tried = [a["exposure_us"] for a in rec["attempts"]]
        self.assertEqual(tried[:2], [90_000, 63_000])
        self.assertEqual(rec["attempts"][0]["verdict"], "clipped")
        self.assertEqual(rec["tus_status"], "ok")

    def test_still_clipped_after_four_tries_marks_the_led_failed(self):
        self.cam.dim = np.full((FRAME_H, FRAME_W), 100.0)   # always clips
        runner = self.make_runner(mode="normal")
        progress = self.run_to_completion(runner)
        rec = self.led_record(runner)
        self.assertEqual(len(rec["attempts"]),
                         config_repo.capture_max_exposure_tries())
        self.assertEqual(rec["tus_status"], "clipped")
        self.assertTrue(any("STILL CLIPPED" in p.message for p in progress))
        with self.archive(runner) as f:
            self.assertEqual(f["/leds/amber"].attrs["tus_status"], "clipped")
            self.assertIn("/leds/amber/lit/e0/R", f)       # still stored

    def test_n_frames_at_tus_are_stored_with_their_role(self):
        runner = self.make_runner(mode="normal")
        self.run_to_completion(runner)
        n = runner._rule.multiple_exposure_count
        rec = self.led_record(runner)
        with self.archive(runner) as f:
            labels = sorted(f["/leds/amber/lit"])
            self.assertEqual(labels, [f"e{i}" for i in range(n)])
            us = {int(f[f"/leds/amber/lit/{lb}/R"].attrs["exposure_us"])
                  for lb in labels}
            self.assertEqual(us, {rec["tus_us"]})
            self.assertEqual(
                {f[f"/leds/amber/lit/{lb}"].attrs["role"] for lb in labels},
                {"tus"})
            self.assertEqual(int(f["/leds/amber"].attrs["tus_us"]),
                             rec["tus_us"])
            self.assertNotIn("bus_us", f["/leds/amber"].attrs)

    def test_rejected_frames_are_not_stored(self):
        runner = self.make_runner(mode="normal")
        self.run_to_completion(runner)
        rec = self.led_record(runner)
        self.assertEqual(len(rec["attempts"]), 2)        # one rejected
        with self.archive(runner) as f:
            self.assertEqual(len(f["/leds/amber/lit"]),
                             runner._rule.multiple_exposure_count)

    def test_led_is_off_before_frames_are_written(self):
        written = []
        real = capture_h5.CaptureSuiteWriter.add_lit

        def add_lit(writer, color, *args, **kwargs):
            written.append(bool(self.leds.lit_channels()))
            return real(writer, color, *args, **kwargs)

        with unittest.mock.patch.object(capture_h5.CaptureSuiteWriter,
                                        "add_lit", add_lit):
            self.run_to_completion(self.make_runner(mode="normal"))
        self.assertTrue(written)
        self.assertFalse(any(written))

    def test_a_dark_analysis_pixel_calls_for_bus(self):
        """photo_red.R is an analysis channel: a dim pad there needs Bus."""
        s = _sample_rect()
        dim = np.ones((FRAME_H, FRAME_W))
        dim[s.y:s.y + s.h, s.x:s.x + s.w] = 0.2
        self.cam.dim = dim
        runner = self.make_runner(mode="normal", led_colors=["photo_red"])
        self.run_to_completion(runner)
        rec = self.led_record(runner, "photo_red")
        self.assertEqual(rec["decision_layers"], ["R"])
        self.assertIsNotNone(rec["bus_us"])
        self.assertGreater(rec["bus_us"], rec["tus_us"])
        self.assertEqual(rec["bus_layer"], "R")
        self.assertEqual(rec["frames_at_bus"],
                         runner._rule.bus_exposure_count)
        self.assertEqual(rec["coverage"]["R"]["n_below_floor_both"], 0)
        n = runner._rule.multiple_exposure_count
        with self.archive(runner) as f:
            self.assertEqual(f[f"/leds/photo_red/lit/e{n}"].attrs["role"],
                             "bus")
            self.assertEqual(int(f["/leds/photo_red"].attrs["bus_us"]),
                             rec["bus_us"])
        # The dark pair covers the longest exposure taken: Bus.
        self.assertEqual(runner._dark_us, [capture_runner.DARK_SHORT_US,
                                           rec["bus_us"]])

    def test_analysis_channels_are_kept_even_where_bus_clips(self):
        """
        The Bus frame clips the bright pixels by design: photo_red.R, an
        analysis channel, is stored anyway; the other layers, which clip
        too on LinearCam, are left out.
        """
        s = _sample_rect()
        dim = np.ones((FRAME_H, FRAME_W))
        dim[s.y:s.y + s.h, s.x:s.x + s.w] = 0.2
        self.cam.dim = dim
        runner = self.make_runner(mode="normal", led_colors=["photo_red"])
        self.run_to_completion(runner)
        rec = self.led_record(runner, "photo_red")
        bus = f"e{runner._rule.multiple_exposure_count}"
        self.assertEqual(rec["layers_why"][bus]["R"], "analysis channel")
        self.assertEqual(rec["layers_why"][bus]["Gb"], "clipped")
        self.assertGreater(rec["frame_stats"][bus]["R"]["n_clip_top"], 0)
        with self.archive(runner) as f:
            self.assertGreater(f[f"/leds/photo_red/lit/{bus}/R"].size, 0)
            self.assertEqual(f[f"/leds/photo_red/lit/{bus}/Gb"].size, 0)
            # At Tus nothing clips, so every layer with signal is kept.
            self.assertGreater(f["/leds/photo_red/lit/e0/Gb"].size, 0)

    def test_no_bus_for_an_led_without_analysis_channels(self):
        s = _sample_rect()
        dim = np.ones((FRAME_H, FRAME_W))
        dim[s.y:s.y + s.h, s.x:s.x + s.w] = 0.2
        self.cam.dim = dim
        runner = self.make_runner(mode="normal")         # amber
        self.run_to_completion(runner)
        rec = self.led_record(runner)
        self.assertIsNone(rec["bus_us"])
        self.assertEqual(rec["analysis_layers"], [])

    def test_the_measurement_starts_from_its_pre_image_tus(self):
        pre = self.make_runner(mode=capture_runner.MODE_PRE,
                               led_colors=["photo_red"])
        self.run_to_completion(pre)
        pre_rec = self.led_record(pre, "photo_red", "/pre")
        self.assertEqual(pre_rec["seed_source"], "D1 seed")
        self.assertEqual(pre_rec["seed_us"],
                         exposure_rule.SEED_US["photo_red"])
        post = self.make_runner(mode="normal", archive_path=pre.out_path,
                                led_colors=["photo_red", "amber"])
        self.run_to_completion(post)
        rec = self.led_record(post, "photo_red")
        self.assertEqual(rec["seed_source"], "pre-image Tus")
        self.assertEqual(rec["seed_us"], pre_rec["tus_us"])
        self.assertEqual(rec["attempts"][0]["verdict"], "accept")
        self.assertEqual(self.led_record(post)["seed_source"], "group 1")

    def test_the_test_region_falls_back_to_the_sample_roi(self):
        """LinearCam shows no cassette, so pad finding fails."""
        runner = self.make_runner(mode="normal")
        self.run_to_completion(runner)
        self.assertTrue(runner.pad_problem)
        rec = self.led_record(runner)
        self.assertEqual(rec["test_region"]["pad_source"], "sample ROI")

    def test_the_fitted_pad_is_the_region_when_found(self):
        self.cam = CassetteCam()
        self.cam.leds = self.leds
        runner = self.make_runner(mode=capture_runner.MODE_PRE)
        self.run_to_completion(runner)
        self.assertEqual(runner.pad_problem, "")
        rec = self.led_record(runner, section="/pre")
        self.assertEqual(rec["test_region"]["pad_source"], "fit")

    # ── Settings from the config table (stage 4a) ─────────────────────

    def _dark_pad(self):
        """Darken the sample ROI to 0.2 of its gain: photo_red.R needs Bus."""
        s = _sample_rect()
        dim = np.ones((FRAME_H, FRAME_W))
        dim[s.y:s.y + s.h, s.x:s.x + s.w] = 0.2
        self.cam.dim = dim

    def test_frame_counts_come_from_the_config_table(self):
        config_repo.save("protocol", "multiple_exposure_count", 3)
        config_repo.save("protocol", "bus_exposure_count", 2)
        self._dark_pad()
        runner = self.make_runner(mode="normal", led_colors=["photo_red"])
        self.run_to_completion(runner)
        rec = self.led_record(runner, "photo_red")
        self.assertEqual((rec["frames_at_tus"], rec["frames_at_bus"]), (3, 2))
        self.assertEqual(rec["settings"]["multiple_exposure_count"], 3)
        with self.archive(runner) as f:
            roles = [f[f"/leds/photo_red/lit/e{i}"].attrs["role"]
                     for i in range(5)]
            self.assertNotIn("/leds/photo_red/lit/e5", f)
        self.assertEqual(roles, ["tus"] * 3 + ["bus"] * 2)

    def test_a_changed_limit_changes_the_rule(self):
        """Accepting at half of 1023: amber's 30000 us seed (~590) passes."""
        config_repo.save("capture", "top_floor_fraction", 0.5)
        config_repo.save("capture", "top_target_fraction", 0.6)
        runner = self.make_runner(mode="normal")
        self.run_to_completion(runner)
        rec = self.led_record(runner)
        self.assertEqual([a["verdict"] for a in rec["attempts"]], ["accept"])
        self.assertEqual(rec["settings"]["top_floor_fraction"], 0.5)

    def test_a_setting_that_makes_no_sense_stops_the_run_at_once(self):
        """Before the camera starts or an archive is opened."""
        config_repo.save("capture", "noise_floor_dn", 2000)
        runner = self.make_runner(mode="normal")
        with self.assertRaises(ValueError) as cm:
            self.run_to_completion(runner)
        self.assertIn("capture.noise_floor_dn", str(cm.exception))
        self.assertEqual(self.cam.frames, [])
        self.assertFalse(list(self.out_root.rglob("*.h5")))

    def test_the_config_row_chooses_the_rule(self):
        self.assertEqual(capture_runner.CaptureConfig().exposure_rule,
                         capture_runner.RULE_STRIP)
        config_repo.save("capture", "exposure_rule", "group")
        self.assertEqual(capture_runner.CaptureConfig().exposure_rule,
                         capture_runner.RULE_GROUP)

    def test_layers_that_are_not_stored_are_not_counted_as_uncovered(self):
        """
        Amber's B layer is far too dark to store (as red.B is on the Pi):
        it is judged on, but neither stored nor counted by the coverage
        check, so no warning is logged for it.
        """
        r, c = divmod(self.cam.bayer_pattern.index("B"), 2)
        dim = np.ones((FRAME_H, FRAME_W))
        dim[r::2, c::2] = 0.01
        self.cam.dim = dim
        runner = self.make_runner(mode="normal")
        with self.assertLogs("capture_runner", level="INFO") as logs:
            self.run_to_completion(runner)
        rec = self.led_record(runner)
        self.assertIn("B", rec["decision_layers"])
        self.assertEqual(rec["layers_why"]["e0"]["B"], "below floor")
        self.assertNotIn("B", rec["coverage"])
        self.assertIn("R", rec["coverage"])
        self.assertFalse([m for m in logs.output if "below the floor" in m])

    def test_the_group_rule_is_still_there(self):
        runner = self.make_runner(mode="normal", exposure_group="3",
                                  exposure_rule=capture_runner.RULE_GROUP)
        self.run_to_completion(runner)
        self.assertEqual(runner.results[0].exposures_us,
                         self._er.read_exposures("amber", "3"))
        with self.archive(runner) as f:
            meta = json.loads(f["/leds/amber/led_metadata"][()])
            self.assertNotIn("strip_rule", meta)
            self.assertEqual(meta["exposure_group"], "3")


if __name__ == "__main__":
    unittest.main(verbosity=2)
