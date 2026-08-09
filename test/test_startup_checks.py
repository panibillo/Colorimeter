#!/usr/bin/env python3
"""
Unit tests for startup_checks (src/startup_checks.py).

The boot-time completeness check is where an instrument says whether it is
commissioned enough to run, so the thing worth pinning down is the severity
rule it applies:

    A check may only FAIL if the operator can fix it from the Menu.
    Everything else must either load a default, or be a warning.

Which is to say: a missing config parameter or image region may stop a boot,
because Menu → Config and Menu → View Image can put it right; a missing
flat-field model, exposure ladder or touch calibration may not, because each
needs a desktop tool the operator does not have in front of them.

Run from the project root:
    python test/test_startup_checks.py
or with unittest discovery:
    python -m unittest test.test_startup_checks
"""

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import config_repo as config
import db_bootstrap
import db_manager as db
import roi
import startup_checks
from startup_checks import (FAIL, OK, RECOVERED, WARN, CheckReport,
                            CheckResult)


class ChecksTestCase(unittest.TestCase):
    """
    Isolates each test in a throwaway database built the way the application
    builds its own — db_bootstrap.ensure() — so every test starts from the
    real seeded tables and the live database is never touched.
    """

    def setUp(self):
        self._saved_dbname = db.DBNAME
        self._tmpdir = tempfile.mkdtemp()
        self._tmpdb  = Path(self._tmpdir) / "checks_test.sqlite"
        db.DBNAME = str(self._tmpdb)
        db_bootstrap.ensure(self._tmpdb)
        config.load(reload=True)

    def tearDown(self):
        db.DBNAME = self._saved_dbname
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        config.load(reload=True)

    def sql(self, statement, vals=()):
        """Edit the test database directly, then drop config_repo's cache."""
        con = sqlite3.connect(self._tmpdb)
        try:
            con.execute(statement, vals)
            con.commit()
        finally:
            con.close()
        config.load(reload=True)


class TestReport(unittest.TestCase):
    """What the splash screen reads off a set of verdicts."""

    def test_a_clean_report_is_ready(self):
        self.assertEqual(CheckReport().summary(), "Ready")
        self.assertTrue(CheckReport().ok)

    def test_warnings_are_counted_but_do_not_stop_a_boot(self):
        one = CheckReport([CheckResult("A", WARN, "hmm")])
        self.assertEqual(one.summary(), "Ready — 1 warning")
        self.assertTrue(one.ok)

        two = CheckReport([CheckResult("A", WARN), CheckResult("B", WARN)])
        self.assertEqual(two.summary(), "Ready — 2 warnings")

    def test_the_first_failure_becomes_the_message(self):
        report = CheckReport([CheckResult("A", WARN, "hmm"),
                              CheckResult("B", FAIL, "no image region"),
                              CheckResult("C", FAIL, "and another")])
        self.assertEqual(report.summary(), "no image region")
        self.assertFalse(report.ok)

    def test_notices_are_collected_in_check_order(self):
        report = CheckReport([CheckResult("A", RECOVERED, "", ("one", "two")),
                              CheckResult("B", RECOVERED, "", ("three",))])
        self.assertEqual(report.notices(), ["one", "two", "three"])


class TestConfigCheck(ChecksTestCase):
    """Every parameter has a value, and something to Restore back to."""

    PARAM = "role='protocol' AND name='reagent1_secs'"

    def test_a_seeded_database_is_clean(self):
        self.assertEqual(startup_checks.check_config().status, OK)

    def test_a_restored_parameter_earns_its_own_notice(self):
        self.sql(f"UPDATE config SET active = 0 WHERE {self.PARAM}")
        result = db_bootstrap.ensure(self._tmpdb)     # the seed heals it
        check = startup_checks.check_config(restored=result.restored)

        self.assertEqual(check.status, RECOVERED)
        self.assertEqual(len(check.notices), 1)
        self.assertTrue(check.notices[0].startswith(
            "Loading protocol.reagent1_secs = 120 (rowid "))

    def test_a_parameter_with_nothing_to_fall_back_on_fails(self):
        # Rows present, none in force, and no default to promote — past what
        # the seed file can heal.  A parameter with no rows at all is not this
        # case: db_bootstrap reseeds that one.
        self.sql(f"UPDATE config SET active = 0, isdefault = 0 "
                 f"WHERE {self.PARAM}")
        result = startup_checks.check_config()

        self.assertEqual(result.status, FAIL)
        self.assertIn("protocol.reagent1_secs", result.message)
        self.assertIn("Menu", result.message)

    def test_a_parameter_restore_cannot_reach_is_only_a_warning(self):
        self.sql(f"UPDATE config SET isdefault = 0 WHERE {self.PARAM}")
        result = startup_checks.check_config()

        self.assertEqual(result.status, WARN)
        self.assertIn("protocol.reagent1_secs", result.message)

    def test_it_never_asks_for_a_parameter_by_name(self):
        # db_bootstrap deliberately keeps no list of required parameter names,
        # and a list here would be a second copy of it.  Removing a parameter
        # outright must therefore not be what this check notices.
        self.sql(f"DELETE FROM config WHERE {self.PARAM}")
        self.assertEqual(startup_checks.check_config().status, OK)


class TestRoiCheck(ChecksTestCase):
    """The image regions a run cannot proceed without."""

    ROLE = "white_balance"

    def missing(self):
        return roi.missing_roles(roi.load_active(), roi.required_roles())

    def test_the_requirement_is_commissioned_not_compiled(self):
        # The list lives in the config table (roi.required_roles, editable=0),
        # so an instrument built around a different card can be given a
        # different requirement without a code change.
        self.assertEqual(roi.required_roles(),
                         ("bound", "sample", "white_balance"))

    def test_a_seeded_database_has_every_required_role(self):
        self.assertEqual(self.missing(), [])

    def test_a_missing_region_is_loaded_from_its_default(self):
        self.sql(f"UPDATE roi SET active = 0 WHERE role = '{self.ROLE}'")
        self.assertEqual(self.missing(), [self.ROLE])

        result = startup_checks.check_rois()

        self.assertIn(result.status, (RECOVERED, WARN))
        self.assertEqual(len(result.notices), 1)
        self.assertTrue(
            result.notices[0].startswith(f"Loading roi.{self.ROLE} = "))
        self.assertIn("rowid", result.notices[0])
        self.assertEqual(self.missing(), [])

    def test_every_row_of_a_role_comes_back_not_just_the_first(self):
        # A role may be filled by more than one region — this card balances on
        # a pair of gray chips — so restoring one of them is not restoring the
        # role.
        self.sql("INSERT INTO roi (active, isdefault, name, role, x, y, w, h, "
                 "frame_w, frame_h, model) "
                 f"SELECT 0, 1, name || '2', '{self.ROLE}', x, y + 100, w, h, "
                 "frame_w, frame_h, model FROM roi "
                 f"WHERE role = '{self.ROLE}' AND isdefault = 1")
        self.sql(f"UPDATE roi SET active = 0 WHERE role = '{self.ROLE}'")

        result = startup_checks.check_rois()

        self.assertEqual(len(result.notices), 2)
        self.assertEqual(self.missing(), [])

    def test_a_region_with_no_default_to_load_fails(self):
        self.sql(f"DELETE FROM roi WHERE role = '{self.ROLE}'")
        result = startup_checks.check_rois()

        self.assertEqual(result.status, FAIL)
        self.assertIn(self.ROLE, result.message)
        self.assertIn("Menu", result.message)
        self.assertIn("View Image", result.detail)

    def test_a_second_sample_warns_and_names_the_one_that_wins(self):
        self.sql("INSERT INTO roi (active, isdefault, name, role, x, y, w, h, "
                 "frame_w, frame_h, model) SELECT 1, 0, 'sample2', 'sample', "
                 "x, y + 100, w, h, frame_w, frame_h, model FROM roi "
                 "WHERE name = 'sample' AND active = 1")

        result = startup_checks.check_rois()

        self.assertEqual(result.status, WARN)
        self.assertIn("2 regions have the role 'sample'", result.message)
        self.assertIn("using 'sample'", result.message)

    def test_more_than_one_white_balance_is_normal(self):
        self.sql("INSERT INTO roi (active, isdefault, name, role, x, y, w, h, "
                 "frame_w, frame_h, model) "
                 f"SELECT 1, 0, name || '2', '{self.ROLE}', x, y + 100, w, h, "
                 "frame_w, frame_h, model FROM roi "
                 f"WHERE role = '{self.ROLE}' AND active = 1")

        self.assertEqual(startup_checks.check_rois().status, OK)

    def test_a_required_region_outside_the_bound_warns(self):
        self.sql(f"UPDATE roi SET x = x + 4000 "
                 f"WHERE role = '{self.ROLE}' AND active = 1")

        result = startup_checks.check_rois()

        self.assertEqual(result.status, WARN)
        self.assertIn("right by", result.message)

    def test_a_region_in_an_unrequired_role_is_left_alone(self):
        # "roi in other roles may be active, but are not checked" — a card may
        # carry as many reference patches as its operator finds useful.
        self.sql("UPDATE roi SET x = x + 4000 "
                 "WHERE role = 'black' AND active = 1")

        self.assertEqual(startup_checks.check_rois().status, OK)

    def test_two_active_rows_sharing_a_name_are_reported(self):
        # load_active() keys on name, so the second one does not raise — it
        # disappears, and whatever it was drawn around is measured by nothing.
        self.sql("INSERT INTO roi (active, isdefault, name, role, x, y, w, h, "
                 "frame_w, frame_h, model) SELECT 1, 0, name, role, x, y, w, "
                 "h, frame_w, frame_h, model FROM roi "
                 "WHERE name = 'sample' AND active = 1")

        result = startup_checks.check_rois()

        self.assertEqual(result.status, WARN)
        self.assertTrue(any("2 active rows" in m for m in result.messages()))


class TestDesktopOnlyWorkOnlyWarns(ChecksTestCase):
    """
    The severity rule's other half: an instrument the operator has no way to
    un-break from here must still start.
    """

    def test_no_exposure_ladders(self):
        self.assertEqual(startup_checks.check_exposures().status, WARN)

    def test_no_flat_field_models(self):
        self.assertEqual(startup_checks.check_layermodels().status, WARN)

    def test_an_uncalibrated_touchscreen(self):
        class Uncalibrated:
            calibrated = False
        self.assertEqual(
            startup_checks.check_touch_cal(touch=Uncalibrated()).status, WARN)

    def test_none_of_them_stops_a_boot(self):
        report = CheckReport([startup_checks.check_exposures(),
                              startup_checks.check_layermodels()])
        self.assertTrue(report.ok)


class TestCaptureFolderCheck(ChecksTestCase):
    """Somewhere to write archives to — a Config-editor problem, so it fails."""

    def test_an_unreachable_folder_fails_and_names_itself(self):
        self.sql("UPDATE config SET value = '/nonexistent/nowhere/here' "
                 "WHERE role='capture' AND name='directory' AND active=1")
        result = startup_checks.check_capture_dir()

        self.assertEqual(result.status, FAIL)
        self.assertIn("/nonexistent/nowhere/here", result.message)
        self.assertIn("Menu", result.message)

    def test_a_folder_that_does_not_exist_yet_is_fine(self):
        target = Path(self._tmpdir) / "captures-to-be"
        self.sql("UPDATE config SET value = ? "
                 "WHERE role='capture' AND name='directory' AND active=1",
                 (str(target),))
        self.assertEqual(startup_checks.check_capture_dir().status, OK)


class TestAbsentDriversAreNeverFatal(unittest.TestCase):
    """Hardware that did not answer is reported, never a reason to refuse."""

    def test_no_temperature_probe(self):
        class Absent:
            connected = False
        self.assertEqual(
            startup_checks.check_temperature(temp=Absent()).status, WARN)

    def test_no_keyboard(self):
        self.assertEqual(
            startup_checks.check_keyboard(keyboard=False).status, WARN)

    def test_a_driver_that_was_never_passed_is_not_a_finding(self):
        self.assertEqual(startup_checks.check_temperature().status, OK)
        self.assertEqual(startup_checks.check_touch_cal().status, OK)
        self.assertEqual(startup_checks.check_keyboard().status, OK)


class TestRunAll(ChecksTestCase):
    """The aggregator itself."""

    def test_a_check_that_raises_warns_rather_than_stopping_the_boot(self):
        def explodes(**_):
            raise RuntimeError("the bus is on fire")

        saved = startup_checks.CHECKS
        startup_checks.CHECKS = (startup_checks.check_config, explodes)
        try:
            report = startup_checks.run_all()
        finally:
            startup_checks.CHECKS = saved

        self.assertTrue(report.ok)
        self.assertEqual(report.results[1].status, WARN)
        self.assertIn("on fire", report.results[1].message)

    def test_every_check_is_narrated_as_it_starts(self):
        seen = []
        startup_checks.run_all(progress=seen.append)
        self.assertEqual(len(seen), len(startup_checks.CHECKS))
        self.assertEqual(seen[:3], ["database", "config", "rois"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
