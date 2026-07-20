#!/usr/bin/env python3
"""
Unit tests for CaptureSession (src/capture_session.py).

Uses the synthetic camera and LED bank from test_capture_runner, so a whole
capture is driven a step at a time with no hardware. The behaviour that
matters here is what a UI depends on: the session never raises into the
event loop, always releases hardware, and reports how the run ended.

Run from the project root:
    python test/test_capture_session.py
or with unittest discovery:
    python -m unittest test.test_capture_session
"""

import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "test"))

import capture_runner
from capture_runner import CaptureConfig
from capture_session import CaptureSession
from test_capture_runner import FakeCam, FakeLeds


class CaptureSessionTestCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out_root = Path(self._tmp.name)
        self.cam  = FakeCam()
        self.leds = FakeLeds()
        self._real_on_warmup  = capture_runner.LED_ON_WARMUP_S
        self._real_off_settle = capture_runner.LED_OFF_SETTLE_S
        capture_runner.LED_ON_WARMUP_S  = 0.0
        capture_runner.LED_OFF_SETTLE_S = 0.0

    def tearDown(self):
        capture_runner.LED_ON_WARMUP_S  = self._real_on_warmup
        capture_runner.LED_OFF_SETTLE_S = self._real_off_settle
        self._tmp.cleanup()

    def make_session(self, **kwargs) -> CaptureSession:
        config = CaptureConfig(out_dir=self.out_root, led_start=1, led_end=1,
                               **kwargs)
        return CaptureSession(config,
                              cam_factory=lambda: self.cam,
                              leds_factory=lambda: self.leds)

    def drain(self, session: CaptureSession, limit: int = 100_000) -> int:
        """Step until the session finishes; return the number of steps."""
        steps = 0
        while session.active and steps < limit:
            session.step()
            steps += 1
        self.assertFalse(session.active, "session did not finish")
        return steps


class TestNormalRun(CaptureSessionTestCase):

    def test_start_then_drain_completes_and_reports_archive(self):
        session = self.make_session()
        self.assertTrue(session.start())
        self.drain(session)
        self.assertIsNone(session.error)
        self.assertIsNotNone(session.out_path)
        self.assertTrue(session.out_path.exists())

    def test_step_before_start_is_a_no_op(self):
        session = self.make_session()
        self.assertIsNone(session.step())
        self.assertFalse(session.active)

    def test_step_after_completion_is_a_no_op(self):
        session = self.make_session()
        session.start()
        self.drain(session)
        self.assertIsNone(session.step())

    def test_progress_is_exposed_while_running(self):
        session = self.make_session()
        session.start()
        first = session.step()
        self.assertIsNotNone(first)
        self.assertIs(session.progress, first)

    def test_hardware_released_after_completion(self):
        session = self.make_session()
        session.start()
        self.drain(session)
        self.assertTrue(self.cam.stopped)
        self.assertTrue(self.leds.closed)
        self.assertEqual(self.leds.lit_channels(), set())

    def test_second_start_while_active_is_refused(self):
        session = self.make_session()
        session.start()
        session.step()
        self.assertFalse(session.start())


class TestCancellation(CaptureSessionTestCase):

    def test_cancel_stops_the_run_and_releases_hardware(self):
        session = self.make_session()
        session.start()
        for _ in range(20):
            session.step()
        session.cancel()
        self.assertFalse(session.active)
        self.assertTrue(self.cam.stopped)
        self.assertEqual(self.leds.lit_channels(), set())

    def test_cancel_reports_the_partial_archive(self):
        session = self.make_session()
        session.start()
        while session.progress is None or session.progress.phase != "capture":
            session.step()
        for _ in range(30):
            session.step()
        session.cancel()
        self.assertIsNotNone(session.out_path)

    def test_cancel_before_start_is_harmless(self):
        session = self.make_session()
        session.cancel()
        self.assertFalse(session.active)


class TestFailures(CaptureSessionTestCase):

    def test_failure_to_build_hardware_is_reported_not_raised(self):
        session = CaptureSession(
            CaptureConfig(out_dir=self.out_root),
            cam_factory=lambda: (_ for _ in ()).throw(OSError("no camera")),
            leds_factory=lambda: self.leds)
        self.assertFalse(session.start())
        self.assertIsInstance(session.error, OSError)
        self.assertFalse(session.active)

    def test_failure_mid_run_is_reported_not_raised(self):
        """A camera dying mid-sweep must not escape into the event loop."""
        session = self.make_session()
        session.start()
        session.step()

        def boom():
            raise OSError("camera vanished")
        self.cam.capture_frame = boom

        steps = 0
        while session.active and steps < 100:
            session.step()
            steps += 1

        self.assertFalse(session.active)
        self.assertIsInstance(session.error, OSError)
        self.assertTrue(self.cam.stopped)
        self.assertEqual(self.leds.lit_channels(), set())

    def test_leds_released_even_when_run_fails_mid_sweep(self):
        session = self.make_session()
        session.start()
        while session.progress is None or session.progress.phase != "capture":
            session.step()

        def boom():
            raise OSError("camera vanished")
        self.cam.capture_frame = boom
        while session.active:
            session.step()

        self.assertEqual(self.leds.lit_channels(), set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
