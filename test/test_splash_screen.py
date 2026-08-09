#!/usr/bin/env python3
"""
Unit tests for splash_screen (src/splash_screen.py).

The splash is the first thing an operator sees and the only screen that runs
before the main event loop exists, so what is tested here is the part that has
to hold without a display: the step-list model, the scroll window, the
arming of the two buttons, and the promise that nothing in here can raise or
hang a boot.

Drawing itself is not asserted beyond "the right strings reached the driver" —
pixels are the display driver's tests to keep.

Run from the project root:
    python test/test_splash_screen.py
or with unittest discovery:
    python -m unittest test.test_splash_screen
"""

import sys
import time
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from splash_screen import (BootLog, FAIL, MAX_LINES, OK, PROBLEM_LINES,
                           RUNNING, START_BTN, Splash, WARN, _NOTE_CHARS,
                           _problem_lines)


class RecordingDisplay:
    """Records the strings drawn, which is all these tests need to see."""

    def __init__(self):
        self.texts = []

    def fill_screen(self, c): pass
    def fill_rect(self, *a, **k): pass
    def draw_rect(self, *a, **k): pass
    def draw_text(self, text, rect, **k): self.texts.append(text)


class BrokenDisplay:
    """Every call fails, the way a dead SPI bus would."""

    def __getattr__(self, _name):
        def boom(*a, **k):
            raise RuntimeError("SPI is on fire")
        return boom


class ScriptedKb:
    def __init__(self, keys=()): self._keys = list(keys)
    def read_one(self): return self._keys.pop(0) if self._keys else None


class ScriptedTouch:
    def __init__(self, points=()): self._points = list(points)
    @property
    def is_touched(self): return bool(self._points)
    def position(self): return self._points.pop(0) if self._points else None


class SplashTestCase(unittest.TestCase):
    """A splash on a recording display, with scriptable input."""

    def splash(self, keys=(), points=(), disp=None):
        self.disp = disp or RecordingDisplay()
        s = Splash(self.disp, version="0.4.0")
        s.attach_input(touch=ScriptedTouch(points), kb=ScriptedKb(keys))
        s.open()
        return s


class TestChrome(SplashTestCase):
    """The title and version line, which never change during a boot."""

    def test_names_the_instrument_and_release(self):
        s = self.splash()
        self.assertEqual(self.disp.texts[0], "Colorimeter")
        self.assertEqual(self.disp.texts[1], "MGWWG v0.4.0")

    def test_repaint_redraws_from_the_model_not_the_screen(self):
        s = self.splash()
        s.step("Touch")
        s.done()
        self.disp.texts.clear()
        s.repaint()
        self.assertIn("Colorimeter", self.disp.texts)
        self.assertIn("Touch", self.disp.texts)
        self.assertIn(OK, self.disp.texts)


class TestStepList(SplashTestCase):
    """Appending steps and closing them out."""

    def test_step_returns_an_index_and_starts_running(self):
        s = self.splash()
        self.assertEqual(s.step("Touch"), 0)
        self.assertEqual(s.step("Keyboard"), 1)
        self.assertEqual(s._steps[0].state, RUNNING)

    def test_a_quick_step_reads_ok_and_a_slow_one_reads_its_duration(self):
        s = self.splash()
        i = s.step("Touch")
        s.done(i)
        self.assertEqual(s._steps[i].state, OK)

        j = s.step("Temperature")
        s._steps[j].t0 -= 1.4
        s.done(j)
        self.assertEqual(s._steps[j].state, "1.4s")

    def test_setters_default_to_the_last_step(self):
        s = self.splash()
        s.step("Touch")
        s.step("Keyboard")
        s.warn()
        self.assertEqual(s._steps[0].state, RUNNING)
        self.assertEqual(s._steps[1].state, WARN)

    def test_setters_on_an_empty_list_do_nothing(self):
        s = self.splash()
        s.done()
        s.fail()
        self.assertEqual(s._steps, [])

    def test_the_window_follows_the_newest_step(self):
        s = self.splash()
        for n in range(MAX_LINES):
            s.step(f"step{n}")
        self.assertEqual(s._scroll, 0)

        s.step("overflow1")
        s.step("overflow2")
        self.assertEqual(s._scroll, 2)
        self.assertEqual(s._steps[s._scroll].label, "step2")
        self.assertEqual(len(s._steps) - s._scroll, MAX_LINES)


class TestRunning(SplashTestCase):
    """The context manager that narrates a step for the length of a block."""

    def test_a_clean_block_marks_the_step_done(self):
        s = self.splash()
        with s.running("Screens"):
            pass
        self.assertEqual(s._steps[0].state, OK)

    def test_a_failing_block_marks_it_and_re_raises(self):
        s = self.splash()
        with self.assertRaises(RuntimeError):
            with s.running("Touch"):
                raise RuntimeError("no I2C")
        self.assertEqual(s._steps[0].state, FAIL)


class TestBootLog(SplashTestCase):
    """Steps from before the display existed."""

    def test_it_times_a_step_and_re_raises_a_failure(self):
        boot = BootLog()
        with boot.step("Database"):
            pass
        self.assertEqual(boot.steps[0].label, "Database")
        self.assertEqual(boot.steps[0].state, OK)

        with self.assertRaises(RuntimeError):
            with boot.step("Display"):
                raise RuntimeError("no SPI")
        self.assertEqual(boot.steps[1].state, FAIL)

    def test_replay_shows_them_already_finished(self):
        boot = BootLog()
        with boot.step("Database"):
            pass
        s = self.splash()
        s.replay(boot)
        self.assertEqual(len(s._steps), 1)
        self.assertNotEqual(s._steps[0].state, RUNNING)


class TestNotice(SplashTestCase):
    """The 'Loading X = Y' dialog, dismissed by OK."""

    MESSAGE = "Loading protocol.reagent1_secs = 120 (rowid 27)"

    def test_it_wraps_to_the_panel_and_returns_on_ok(self):
        s = self.splash(keys=["enter"])
        s.notice(self.MESSAGE)
        wrapped = [t for t in self.disp.texts if t.startswith("Loading")][0]
        widest = max(len(line) for line in wrapped.split("\n"))
        self.assertLessEqual(widest, _NOTE_CHARS)

    def test_it_puts_the_step_list_back_afterwards(self):
        s = self.splash(keys=["enter"])
        s.step("Configuration")
        s.notice(self.MESSAGE)
        self.assertEqual(self.disp.texts[-2:], ["Configuration", RUNNING])

    def test_with_no_input_attached_it_returns_instead_of_hanging(self):
        s = Splash(RecordingDisplay(), version="0.4.0")
        s.open()
        started = time.monotonic()
        s.notice(self.MESSAGE)
        self.assertLess(time.monotonic() - started, 10.0)

    def test_it_honours_a_timeout(self):
        s = self.splash()          # no keys queued, nobody presses OK
        started = time.monotonic()
        s.notice(self.MESSAGE, timeout_s=0.05)
        self.assertLess(time.monotonic() - started, 10.0)


class TestFinish(SplashTestCase):
    """Revealing the buttons and waiting for a choice."""

    CENTRE_OF_START = (START_BTN.x + START_BTN.w // 2,
                       START_BTN.y + START_BTN.h // 2)

    def test_start_is_focused_by_default(self):
        self.assertEqual(self.splash(keys=["enter"]).finish("Ready"), "start")

    def test_tab_reaches_menu(self):
        self.assertEqual(
            self.splash(keys=["tab", "enter"]).finish("Ready"), "menu")

    def test_start_is_touchable_once_armed(self):
        s = self.splash(points=[self.CENTRE_OF_START])
        self.assertEqual(s.finish("Ready"), "start")

    def test_buttons_are_inert_before_finish(self):
        s = self.splash(keys=["enter"], points=[self.CENTRE_OF_START])
        s.notice("Loading capture.extension = .h5 (rowid 9)")
        self.assertFalse(s._armed)
        self.assertFalse(s._start_btn.visible)

    def test_a_failure_greys_start_and_focuses_menu(self):
        s = self.splash(keys=["enter"])
        self.assertEqual(s.finish("No image region for white", ok=False),
                         "menu")
        self.assertFalse(s._start_btn.enabled)
        self.assertTrue(s._menu_btn.enabled)

    def test_a_greyed_start_cannot_be_reached_by_touch_or_tab(self):
        s = self.splash(keys=["tab", "tab", "enter"],
                        points=[self.CENTRE_OF_START])
        self.assertEqual(s.finish("broken", ok=False), "menu")

    def test_it_honours_a_timeout(self):
        s = self.splash()
        self.assertEqual(s.finish("Ready", timeout_s=0.05), "start")


class TestProblemList(SplashTestCase):
    """
    What replaces the step list once the checks are done.

    Every step that ran, ran — a list of them is worth nothing next to the
    list of what should not have happened, and a 320x240 panel has room for
    only one of the two.
    """

    FOUND = [("fail", "Database: missing table roi"),
             ("warn", "Temperature probe: no device detected")]

    def test_it_shows_the_problems_and_drops_the_steps(self):
        s = self.splash()
        s.step("Database")
        s.done()
        s.step("Configuration")
        s.warn()
        self.disp.texts.clear()

        s.problems(self.FOUND)

        shown = " ".join(self.disp.texts)
        self.assertIn("missing table roi", shown)
        self.assertIn("no device detected", shown)
        self.assertNotIn("Configuration", shown)

    def test_nothing_wrong_leaves_the_region_empty(self):
        s = self.splash()
        s.step("Database")
        s.done()
        self.disp.texts.clear()

        s.problems([])

        self.assertEqual(self.disp.texts, [])

    def test_it_survives_a_repaint(self):
        # An aborted shutdown screen wipes the panel; what comes back has to
        # be the problems, not the step list they replaced.
        s = self.splash()
        s.step("Database")
        s.done()
        s.problems(self.FOUND)
        self.disp.texts.clear()

        s.repaint()

        shown = " ".join(self.disp.texts)
        self.assertIn("missing table roi", shown)
        self.assertNotIn("Database ", shown.replace("Database:", ""))

    def test_a_notice_afterwards_still_puts_the_problems_back(self):
        s = self.splash(keys=["enter"])
        s.problems(self.FOUND)
        self.disp.texts.clear()

        s.notice("Loading capture.extension = .h5 (rowid 9)")

        self.assertIn("missing table roi", " ".join(self.disp.texts))

    def test_a_long_list_is_truncated_rather_than_overflowed(self):
        many = [("warn", f"Check {i}: something worth knowing that runs well "
                         f"past one line of a 320 pixel panel")
                for i in range(9)]

        lines = _problem_lines(many)

        self.assertLessEqual(len(lines), PROBLEM_LINES)
        self.assertIn("more", lines[-1][1])

    def test_a_failure_is_never_what_gets_dropped(self):
        many = [("warn", f"Check {i}: a warning") for i in range(20)]

        lines = _problem_lines([("fail", "Database: broken")] + many)

        self.assertEqual(lines[0], ("fail", "Database: broken"))


class TestNeverRaises(unittest.TestCase):
    """
    A splash must not be the thing that prevents a boot.

    Every public method swallows and logs, and finish() still answers, so a
    display fault degrades to a blank panel rather than an instrument that
    will not start.
    """

    def test_a_dead_display_still_lets_the_boot_finish(self):
        s = Splash(BrokenDisplay(), version="0.4.0")
        s.attach_input(kb=ScriptedKb(["enter"]))
        s.open()
        s.replay(BootLog())
        s.step("Touch")
        s.done()
        s.warn()
        s.fail()
        s.status("checking")
        s.notice("Loading x.y = 1 (rowid 2)")
        s.problems([("fail", "Database: broken")])
        s.repaint()
        self.assertEqual(s.finish("Ready"), "start")

    def test_running_still_re_raises_the_real_error(self):
        s = Splash(BrokenDisplay(), version="0.4.0")
        s.open()
        with self.assertRaises(ValueError):
            with s.running("Touch"):
                raise ValueError("the real problem")


if __name__ == "__main__":
    unittest.main(verbosity=2)
