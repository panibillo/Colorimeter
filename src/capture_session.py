"""
capture_session.py — drives a CaptureRunner from a non-blocking event loop.
---------------------------------------------------------------------------
CaptureRunner.run() is a generator: it yields between camera operations so a
caller can do something else in the gaps. This module is the something else —
it owns the camera and LED bank for the duration of one capture, advances the
generator one step per call, and turns any failure into inspectable state
rather than an exception escaping into the event loop.

A UI polls step() from its main loop and repaints from the returned Progress.
Nothing here knows about screens or widgets.

Usage (a full run in one go):
    session = CaptureSession(CaptureConfig(led_colors=['amber', 'blue']))
    session.start()
    while session.active:            # in reality, once per event-loop pass
        progress = session.step()
        if progress:
            display(progress.message, progress.fraction)
    if session.error:
        display(f"Capture failed: {session.error}")
    else:
        display(f"Saved {session.out_path}")

Usage (split across a pause — the normal path's dark-frame prefetch):
    session.start_prefetch()
    while session.active:
        session.step()               # drives run_prefetch() only
    # session.prefetch_done is now True; camera/archive stay open.
    # ... arbitrary pause here (a reagent countdown, a user's own pace) ...
    session.start_capture()          # resumes the SAME runner
    while session.active:
        session.step()               # drives run_capture() to completion
    if session.error is None:
        display(f"Saved {session.out_path}")
    # Cancelling instead of resuming, at any point after start_prefetch():
    #     session.cancel()

Hardware is created through factories so tests can substitute fakes; the
defaults build the real BayerCam and LEDBank, both of which fall back to
their desktop mocks when picamera2 or the GPIO backends are unavailable.
"""

import logging

from capture_runner import CaptureConfig, CaptureRunner

log = logging.getLogger(__name__)


def _default_cam():
    """Build the real camera (mock-backed on the desktop)."""
    from bayer_cam import BayerCam
    return BayerCam()


def _default_leds():
    """Build the real LED bank (mock-backed on the desktop)."""
    from lighting_cal8 import LEDBank
    return LEDBank()


class CaptureSession:
    """
    One capture run, advanced a step at a time.

    Attributes:
        active:        True between a successful start()/start_prefetch()/
                       start_capture() and that stage finishing.
        progress:      The most recent Progress, or None before the first step.
        error:         The exception that ended the run, or None.
        out_path:      Path of the archive written, set once the run completes.
        runner:        The underlying CaptureRunner, or None before start().
        prefetch_done: True once run_prefetch() has completed and parked,
                       waiting for start_capture() to resume it. False after
                       start()/start_capture(), or before any start.
    """

    def __init__(self, config: "CaptureConfig | None" = None,
                 cam=None, leds=None,
                 cam_factory=_default_cam, leds_factory=_default_leds):
        """
        Args:
            config:       Run parameters; defaults to a single-LED run.
            cam:          A pre-opened camera to *borrow* for this run (the
                          Arbiter's persistent camera).  When given, the session
                          does not stop it at the end — the owner disposes of it.
                          None builds a fresh one from cam_factory and owns it.
            leds:         A pre-opened LED bank to borrow, mirroring `cam`.
            cam_factory:  Zero-arg callable returning a camera (used when
                          `cam` is None).
            leds_factory: Zero-arg callable returning an LED bank (used when
                          `leds` is None).
        """
        self.config       = config or CaptureConfig()
        self._cam_factory  = cam_factory
        self._leds_factory = leds_factory

        self.active        = False
        self.progress       = None
        self.error: "Exception | None" = None
        self.out_path       = None
        self.runner: "CaptureRunner | None" = None
        self.prefetch_done  = False

        # Borrowed hardware is used as-is and never released here; only hardware
        # this session builds itself (cam/leds left None) is stopped at the end.
        self._cam  = cam
        self._leds = leds
        self._owns_cam  = cam is None
        self._owns_leds = leds is None
        self._gen  = None
        # True while _gen is driving run_prefetch() rather than run()/
        # run_capture() — changes how step() treats that generator's
        # StopIteration (park, don't release hardware) and _finish()'s
        # bookkeeping. See start_prefetch()/start_capture().
        self._in_prefetch = False

    # ── Lifecycle ────────────────────────────────────────────────────

    def _start(self, make_gen, in_prefetch: bool) -> bool:
        """
        Shared setup for start()/start_prefetch(): acquire hardware, build
        the runner, and point the generator at whichever entry point the
        caller asked for. No frames are captured yet; the first step() does.

        Args:
            make_gen:    Callable(runner) -> generator, e.g. CaptureRunner.run
                         or CaptureRunner.run_prefetch.
            in_prefetch: Whether the generator is run_prefetch() — see
                         self._in_prefetch.

        Returns:
            True if the session is ready to step, False if setup failed —
            in which case .error holds the reason and hardware is released.
        """
        if self.active:
            log.warning("CaptureSession: start ignored, already active")
            return False

        self.progress = None
        self.error    = None
        self.out_path = None

        try:
            if self._cam is None:
                self._cam = self._cam_factory()
            if self._leds is None:
                self._leds = self._leds_factory()
            self.runner = CaptureRunner(self._cam, self._leds, self.config)
            self._gen = make_gen(self.runner)
            self._in_prefetch = in_prefetch
        except Exception as exc:      # hardware may be missing or in use
            log.exception("CaptureSession: failed to start")
            self.error = exc
            self._release()
            return False

        self.active = True
        return True

    def start(self) -> bool:
        """
        Acquire hardware and prepare a full, unsplit run. No frames are
        captured yet; the first step() does that.

        Returns:
            True if the session is ready to step, False if setup failed —
            in which case .error holds the reason and hardware is released.
        """
        ok = self._start(lambda r: r.run(), in_prefetch=False)
        if ok:
            log.info("CaptureSession: started (LEDs %s, mode=%s, group=%s)",
                     ", ".join(self.config.led_colors),
                     self.config.mode, self.config.exposure_group)
        return ok

    def start_prefetch(self) -> bool:
        """
        Acquire hardware and begin the normal path's "before" dark-frame
        prefetch (CaptureRunner.run_prefetch()) — camera init, focus, and
        the shared dark batch, with no LED lit. On completion the camera,
        LED bank, and archive are deliberately left open (see step()) for
        start_capture() to resume into after however long the caller pauses.

        Returns:
            True if the session is ready to step, False if setup failed —
            in which case .error holds the reason and hardware is released.
        """
        self.prefetch_done = False
        ok = self._start(lambda r: r.run_prefetch(), in_prefetch=True)
        if ok:
            log.info("CaptureSession: prefetch started (mode=%s, group=%s)",
                     self.config.mode, self.config.exposure_group)
        return ok

    def start_capture(self) -> bool:
        """
        Resume a completed prefetch into the lit-LED sweep and "after" dark
        batch (CaptureRunner.run_capture()), reusing the same runner/camera/
        LED bank/archive run_prefetch() left open.

        Returns:
            True if capture resumed, False if there is no completed prefetch
            to resume — the caller should fall back to a fresh start().
        """
        if self.runner is None or not self.prefetch_done:
            log.warning("CaptureSession: start_capture ignored, "
                        "no completed prefetch to resume")
            return False

        self.progress      = None
        self.error         = None
        self.prefetch_done = False
        self._gen          = self.runner.run_capture()
        self._in_prefetch  = False
        self.active        = True
        log.info("CaptureSession: resuming into capture (mode=%s, group=%s)",
                 self.config.mode, self.config.exposure_group)
        return True

    def step(self):
        """
        Advance the run by one step.

        Returns:
            The Progress just reached, or None if the session is not active
            or has just finished. Check .active and .error afterwards to tell
            a normal completion from a failure — and .prefetch_done to tell
            a parked prefetch (call start_capture() next) from either.
        """
        if not self.active or self._gen is None:
            return None

        try:
            self.progress = next(self._gen)
            return self.progress
        except StopIteration:
            if self._in_prefetch:
                # run_prefetch() returned normally: it deliberately left the
                # camera/LED bank/archive open for start_capture() to resume
                # into, so — unlike a full run's completion — nothing here
                # is released.
                log.info("CaptureSession: prefetch complete, parked — %s",
                         self.runner.out_path)
                self.active        = False
                self.prefetch_done = True
                self._gen          = None
                return None
            log.info("CaptureSession: complete — %s", self.runner.out_path)
            self.out_path = self.runner.out_path
            self._finish()
            return None
        except Exception as exc:
            # A capture failure must not take the event loop down with it.
            log.exception("CaptureSession: run failed")
            self.error = exc
            self._finish()
            return None

    def cancel(self) -> None:
        """
        Abandon the run — whether currently stepping, or parked after a
        completed prefetch awaiting start_capture(). The runner turns off
        any lit LED and closes the archive, leaving whatever was captured so
        far readable.
        """
        if not self.active and not self.prefetch_done:
            return
        log.info("CaptureSession: cancelled")
        if self.runner is not None:
            self.out_path = self.runner.out_path
            if self.prefetch_done:
                # run_prefetch() already returned normally — there is no
                # live generator left to abandon via _gen.close(), so close
                # the parked archive/LED state directly instead.
                self.runner.close_prefetch()
        self._finish()

    # ── Internals ────────────────────────────────────────────────────

    def _finish(self) -> None:
        """End the run and release hardware. Never raises."""
        self.active        = False
        self.prefetch_done = False
        self._in_prefetch  = False
        if self._gen is not None:
            try:
                self._gen.close()     # runs the runner's own cleanup
            except Exception:
                log.exception("CaptureSession: error closing the run")
            self._gen = None
        self._release()

    def _release(self) -> None:
        """
        Release hardware this session owns. Borrowed camera/LED bank (passed in
        by the Arbiter) are left running for the owner to dispose of; only
        hardware the session built itself is stopped here. Never raises.
        """
        if self._cam is not None:
            if self._owns_cam:
                try:
                    self._cam.stop()
                except Exception:
                    log.exception("CaptureSession: error stopping the camera")
            self._cam = None
        if self._leds is not None:
            if self._owns_leds:
                try:
                    self._leds.close()
                except Exception:
                    log.exception("CaptureSession: error closing the LED bank")
            self._leds = None
