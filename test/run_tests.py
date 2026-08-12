#!/usr/bin/env python3
"""
Self-stopping runner for the stdlib-unittest suite.

Why this exists: a wedged test pins a core until somebody notices.  One did,
for four and a half days — an orphaned `arbiter.py` re-rasterising an
ever-growing matplotlib scene graph at 100% CPU — so nothing here is allowed
to run without a deadline.

The deadline is enforced by `faulthandler.dump_traceback_later(..., exit=True)`,
whose timer runs on a dedicated thread that does not hold the GIL.  That is the
whole point: `signal.alarm` would not do, because a thread stuck inside a C
extension (numpy, matplotlib, a camera driver) never returns to the interpreter
to run a Python-level signal handler.  When the deadline passes, faulthandler
writes every thread's stack to stderr and exits the process non-zero.

Usage, from the project root:
    python test/run_tests.py                     # every test/test_*.py
    python test/run_tests.py test_config_repo    # one module
    python test/run_tests.py test_roi test_rect  # several

Environment:
    COLORIMETER_TEST_TIMEOUT     seconds allowed per test  (default 60)
    COLORIMETER_SUITE_TIMEOUT    seconds allowed per run   (default 900)

Exit status is 0 only when every test passed.  A timeout exits 1, with a
"Timeout (h:mm:ss)!" banner directly beneath the name of the test that hung.
"""

import faulthandler
import os
import sys
import time
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Keep the suite off an interactive backend.  A test that opens a real window
# can outlive the run, and the render loop behind one is exactly what wedged
# the process this runner exists to prevent.  setdefault(), so an explicit
# override still wins.
os.environ.setdefault("MPLBACKEND", "Agg")


# ── Configuration ────────────────────────────────────────────────────────────

def _seconds(name: str, default: float) -> float:
    """Read a positive number of seconds from the environment."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        sys.exit(f"{name}={raw!r} is not a number")
    if value <= 0:
        sys.exit(f"{name} must be positive, got {value}")
    return value


PER_TEST_S = _seconds("COLORIMETER_TEST_TIMEOUT", 60.0)
WHOLE_RUN_S = _seconds("COLORIMETER_SUITE_TIMEOUT", 900.0)


# ── Watchdog ─────────────────────────────────────────────────────────────────

def _expire(why: str) -> None:
    """Dump every thread and leave, without running exit handlers."""
    print(f"\nTIMEOUT: {why}", file=sys.stderr)
    faulthandler.dump_traceback(file=sys.stderr)
    sys.stderr.flush()
    os._exit(1)


class _Watchdog:
    """
    One faulthandler timer serving both caps.

    faulthandler keeps a single pending dump, so each arm() asks for the
    smaller of "this test's share" and "what is left of the whole run".  The
    run cap therefore still fires mid-test, and names the test that overran it.
    """

    def __init__(self, per_test: float, whole_run: float):
        self._per_test = per_test
        self._ends_at = time.monotonic() + whole_run

    def arm(self) -> None:
        left = self._ends_at - time.monotonic()
        if left <= 0:
            _expire(f"whole-run cap of {WHOLE_RUN_S:.0f}s reached")
        # A timeout exits via _exit(), which skips buffered output; flush now
        # so the name of the running test is on its way out before then.
        sys.stderr.flush()
        faulthandler.dump_traceback_later(min(self._per_test, left), exit=True)

    def disarm(self) -> None:
        faulthandler.cancel_dump_traceback_later()


_watchdog = _Watchdog(PER_TEST_S, WHOLE_RUN_S)


class _TimeBoxedResult(unittest.TextTestResult):
    """
    Re-arms the watchdog around every test.

    At verbosity 2 TextTestResult.startTest has already written and flushed the
    test's name, so a dump lands directly beneath whichever test hung.
    """

    def startTest(self, test):
        super().startTest(test)
        _watchdog.arm()

    def stopTest(self, test):
        _watchdog.disarm()
        super().stopTest(test)


# ── Loading ──────────────────────────────────────────────────────────────────

def _module_name(arg: str) -> str:
    """Accept `test_roi`, `test.test_roi` or `test/test_roi.py` alike."""
    name = arg.removesuffix(".py").replace(os.sep, ".").replace("/", ".")
    return f"test.{name.removeprefix('test.')}"


def _load(names) -> unittest.TestSuite:
    """The named modules, or the whole suite when none are named."""
    loader = unittest.TestLoader()
    if not names:
        return loader.discover(start_dir=str(_ROOT / "test"),
                               top_level_dir=str(_ROOT))
    return loader.loadTestsFromNames([_module_name(n) for n in names])


# ── Entry point ──────────────────────────────────────────────────────────────

def main(argv) -> int:
    faulthandler.enable()          # a segfault should print a stack too

    # Cover import time as well: a module-level hang is still a hang.
    _watchdog.arm()
    suite = _load(argv)
    _watchdog.disarm()

    started = time.monotonic()
    result = unittest.TextTestRunner(
        verbosity=2, stream=sys.stderr, resultclass=_TimeBoxedResult,
    ).run(suite)
    elapsed = time.monotonic() - started

    print(f"ran {result.testsRun} tests in {elapsed:.1f}s "
          f"(caps: {PER_TEST_S:.0f}s per test, {WHOLE_RUN_S:.0f}s per run)",
          file=sys.stderr)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
