#!/usr/bin/env python3
"""
Unit tests for config_repo (src/config_repo.py).

config_repo is the only module that reads the database's `config` table, and
the only place that answers "where do capture archives go and what are they
called". These tests cover the resolution rules — environment override, the
simulation profile, operator overrides via save()/restore(), and the
defaults that keep a capture working when a row is missing or malformed.

Run from the project root:
    python test/test_config_repo.py
or with unittest discovery:
    python -m unittest test.test_config_repo
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import db_manager as db
import config_repo as config

_DDL_PATH = _ROOT / "src" / "db_DDL.sql"


class ConfigTestCase(unittest.TestCase):
    """
    Isolates each test in a throwaway database built from db_DDL.sql, so
    every test starts from the real, fully-seeded config table and the live
    database is never touched.
    """

    _MANAGED_ENV = (
        config.CAPTURE_DIR_ENV,
        config.SIMULATION_ENV,
    )

    def setUp(self):
        self._saved_env = {k: os.environ.pop(k, None) for k in self._MANAGED_ENV}
        self._saved_dbname = db.DBNAME
        self._tmpdir = tempfile.mkdtemp()
        self._tmpdb  = Path(self._tmpdir) / "config_test.sqlite"
        sqlite3.connect(self._tmpdb).executescript(_DDL_PATH.read_text())
        db.DBNAME = str(self._tmpdb)
        config.load(reload=True)

    def tearDown(self):
        db.DBNAME = self._saved_dbname
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        config.load(reload=True)

    def use_rows(self, rows) -> None:
        """
        Replace the config table's contents with `rows`.

        Each row is (role, name, vtype, value, profile). Two rows are written
        per entry — the isdefault fallback and the row in force — exactly as
        the real seed does, so save()/restore() behave the same way under test
        as they do against the shipped table.
        """
        db.write_one("DELETE FROM config")
        rowid = 0
        for role, name, vtype, value, profile in rows:
            for isdefault, active in ((1, 0), (0, 1)):
                rowid += 1
                db.write_one(
                    "INSERT INTO config (rowid, active, isdefault, add_dt, role, "
                    "name, vtype, value, profile, source, remark) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (rowid, active, isdefault, "2026-01-01 00:00:00",
                     role, name, vtype, value, profile, "seed", None))
        config.load(reload=True)


class TestShippedConfig(ConfigTestCase):
    """The real db_DDL.sql seed must be complete and agree with SEED_PARAMS."""

    def test_seed_block_matches_seed_params(self):
        self.assertEqual(config._ddl_seed_block(), config.seed_sql())

    def test_shipped_table_loads(self):
        settings = config.load(reload=True)
        self.assertIn("capture", settings)

    def test_shipped_capture_directory_is_captures(self):
        self.assertEqual(config.capture_dir(), config.PROJECT_ROOT / "captures")

    def test_shipped_archive_name_is_timestamp_dot_h5(self):
        suite_id = config.capture_suite_id(datetime(2026, 7, 19, 13, 43))
        self.assertEqual(suite_id, "20260719_1343")
        self.assertEqual(config.capture_archive_name(suite_id), "20260719_1343.h5")


class TestCaptureDir(ConfigTestCase):

    def test_relative_directory_resolves_against_project_root(self):
        self.use_rows([("capture", "directory", "path", "captures", "")])
        self.assertEqual(config.capture_dir(), config.PROJECT_ROOT / "captures")

    def test_absolute_directory_is_used_as_given(self):
        self.use_rows([("capture", "directory", "path",
                        "/srv/colorimeter/archives", "")])
        self.assertEqual(config.capture_dir(), Path("/srv/colorimeter/archives"))

    def test_user_home_is_expanded(self):
        self.use_rows([("capture", "directory", "path", "~/archives", "")])
        self.assertEqual(config.capture_dir(), Path.home() / "archives")

    def test_environment_variable_wins_over_table(self):
        self.use_rows([("capture", "directory", "path", "/srv/from_table", "")])
        os.environ[config.CAPTURE_DIR_ENV] = "/tmp/from_env"
        self.assertEqual(config.capture_dir(), Path("/tmp/from_env"))

    def test_environment_variable_expands_user(self):
        os.environ[config.CAPTURE_DIR_ENV] = "~/from_env"
        self.assertEqual(config.capture_dir(), Path.home() / "from_env")


class TestNaming(ConfigTestCase):

    def test_timestamp_format_is_configurable(self):
        self.use_rows([("capture", "timestamp_format", "str",
                        "%Y-%m-%d_%H%M%S", "")])
        self.assertEqual(
            config.capture_suite_id(datetime(2026, 7, 19, 13, 43, 7)),
            "2026-07-19_134307")

    def test_extension_is_configurable(self):
        self.use_rows([("capture", "extension", "str", ".hdf5", "")])
        self.assertEqual(config.capture_archive_name("abc"), "abc.hdf5")

    def test_suite_id_defaults_to_now(self):
        before = datetime.now().strftime("%Y%m%d")
        self.assertTrue(config.capture_suite_id().startswith(before))


class TestFallbacks(ConfigTestCase):
    """A missing table, empty table, or a typo must not stop a capture."""

    def test_missing_table_falls_back_to_seed_params(self):
        db.write_one("DROP TABLE config")
        config.load(reload=True)
        self.assertEqual(config.capture_dir(), config.PROJECT_ROOT / "captures")
        self.assertEqual(config.capture_archive_name("x"), "x.h5")

    def test_empty_table_falls_back_to_seed_params(self):
        db.write_one("DELETE FROM config")
        config.load(reload=True)
        self.assertEqual(config.capture_dir(), config.PROJECT_ROOT / "captures")
        self.assertEqual(config.reagent1_secs(), 120)

    def test_malformed_value_falls_back_to_seed_params(self):
        self.use_rows([("runtime", "main_loop_poll_s", "float", "abc", "")])
        self.assertEqual(config.main_loop_poll_s(), 0.02)

    def test_partial_row_set_keeps_other_defaults(self):
        """One row must not blank out its neighbours' seed values."""
        self.use_rows([("capture", "directory", "path", "/srv/only_this", "")])
        self.assertEqual(config.capture_dir(), Path("/srv/only_this"))
        self.assertEqual(config.capture_archive_name("x"), "x.h5")


class TestShippedTunables(ConfigTestCase):
    """The values migrated out of the code keep their historical defaults."""

    def test_shipped_protocol_durations(self):
        self.assertEqual(config.reagent1_secs(), 120)
        self.assertEqual(config.reagent2_secs(), 420)
        self.assertEqual(config.analysis_secs(), 1.5)

    def test_shipped_runtime_timing(self):
        self.assertEqual(config.main_loop_poll_s(), 0.02)
        self.assertEqual(config.shutdown_timeout_s(), 15.0)

    def test_shipped_capture_selection(self):
        self.assertEqual(config.capture_led_start(), 1)
        self.assertEqual(config.capture_led_end(), 10)
        self.assertEqual(config.fallback_exposure_group(), "3")

    def test_shipped_default_rowid(self):
        self.assertEqual(config.default_rowid(), 1)

    def test_shipped_backlight_pin(self):
        self.assertEqual(config.backlight_pin(), 13)


class TestSimulationProfile(ConfigTestCase):
    """A 'simulation' row wins over its '' counterpart only while active."""

    _ROWS = [
        ("protocol", "reagent1_secs", "int", "120", ""),
        ("protocol", "reagent1_secs", "int", "10",  config.SIM_PROFILE),
        ("protocol", "reagent2_secs", "int", "420", ""),
        ("protocol", "reagent2_secs", "int", "10",  config.SIM_PROFILE),
    ]

    def test_env_unset_is_not_simulation(self):
        self.use_rows(self._ROWS)
        self.assertFalse(config.is_simulation())
        self.assertEqual(config.reagent1_secs(), 120)

    def test_env_off_keeps_the_shipped_durations(self):
        self.use_rows(self._ROWS)
        os.environ[config.SIMULATION_ENV] = "0"
        config.load(reload=True)
        self.assertFalse(config.is_simulation())
        self.assertEqual(config.reagent1_secs(), 120)

    def test_env_on_applies_the_profile(self):
        self.use_rows(self._ROWS)
        os.environ[config.SIMULATION_ENV] = "1"
        config.load(reload=True)
        self.assertTrue(config.is_simulation())
        self.assertEqual(config.reagent1_secs(), 10)
        self.assertEqual(config.reagent2_secs(), 10)

    def test_profile_leaves_unprofiled_params_alone(self):
        self.use_rows(self._ROWS + [
            ("protocol", "analysis_secs", "float", "99", "")])
        os.environ[config.SIMULATION_ENV] = "1"
        config.load(reload=True)
        self.assertEqual(config.reagent1_secs(), 10)    # profiled
        self.assertEqual(config.analysis_secs(), 99)    # untouched


class TestOperatorOverrides(ConfigTestCase):
    """
    Values the operator changes on the instrument (Menu -> Config) are saved
    as a new active row with source='operator' and win over every other tier.
    """

    _ROWS = [
        ("protocol", "reagent1_secs", "int", "120", ""),
        ("protocol", "reagent1_secs", "int", "10",  config.SIM_PROFILE),
        ("protocol", "reagent2_secs", "int", "420", ""),
        ("protocol", "analysis_secs", "float", "1.5", ""),
    ]

    def test_nothing_saved_leaves_the_commissioned_value_in_charge(self):
        self.use_rows(self._ROWS)
        self.assertEqual(config.reagent1_secs(), 120)
        self.assertEqual(config.overrides(), [])

    def test_saved_value_wins_over_the_commissioned_value(self):
        self.use_rows(self._ROWS)
        self.assertGreater(config.save("protocol", "reagent1_secs", 45), 0)
        self.assertEqual(config.reagent1_secs(), 45)

    def test_saved_value_survives_a_reload(self):
        self.use_rows(self._ROWS)
        config.save("protocol", "reagent1_secs", 45)
        config.load(reload=True)
        self.assertEqual(config.reagent1_secs(), 45)

    def test_saved_value_wins_over_the_simulation_profile(self):
        """The point of the Config screen: a custom wait holds in either mode."""
        self.use_rows(self._ROWS)
        os.environ[config.SIMULATION_ENV] = "1"
        config.load(reload=True)
        self.assertEqual(config.reagent1_secs(), 10)   # profile applies
        config.save("protocol", "reagent1_secs", 45)
        self.assertEqual(config.reagent1_secs(), 45)

    def test_saving_one_param_leaves_its_siblings_alone(self):
        self.use_rows(self._ROWS)
        config.save("protocol", "reagent1_secs", 45)
        self.assertEqual(config.reagent2_secs(), 420)
        self.assertEqual(config.analysis_secs(), 1.5)

    def test_default_reports_the_commissioned_value_regardless(self):
        self.use_rows(self._ROWS)
        config.save("protocol", "reagent1_secs", 45)
        self.assertEqual(config.default("protocol", "reagent1_secs"), 120)

    def test_restoring_returns_to_the_commissioned_value(self):
        self.use_rows(self._ROWS)
        config.save("protocol", "reagent1_secs", 45)
        self.assertGreater(config.restore("protocol", "reagent1_secs"), 0)
        self.assertEqual(config.reagent1_secs(), 120)
        self.assertEqual(config.overrides(), [])

    def test_restoring_what_was_never_set_writes_nothing(self):
        self.use_rows(self._ROWS)
        before = db.read_val("SELECT COUNT(*) FROM config")
        self.assertEqual(config.restore("protocol", "reagent1_secs"), 0)
        self.assertEqual(db.read_val("SELECT COUNT(*) FROM config"), before)

    def test_history_is_kept_not_overwritten(self):
        self.use_rows(self._ROWS)
        before = db.read_val(
            "SELECT COUNT(*) FROM config WHERE role='protocol' "
            "AND name='reagent1_secs'")
        config.save("protocol", "reagent1_secs", 45)
        after = db.read_val(
            "SELECT COUNT(*) FROM config WHERE role='protocol' "
            "AND name='reagent1_secs'")
        self.assertEqual(after, before + 1)

    def test_saving_an_unknown_parameter_is_refused(self):
        self.use_rows(self._ROWS)
        self.assertEqual(config.save("protocol", "nonesuch", 1), 0)

    def test_restoring_an_unknown_parameter_is_refused(self):
        self.use_rows(self._ROWS)
        self.assertEqual(config.restore("protocol", "nonesuch"), 0)


class TestSeedDefaults(ConfigTestCase):

    def test_is_a_noop_on_a_complete_table(self):
        self.assertEqual(config.seed_defaults(), 0)

    def test_refills_a_missing_parameter(self):
        db.write_one("DELETE FROM config WHERE role = 'hardware' "
                     "AND name = 'backlight_pin'")
        config.load(reload=True)
        self.assertEqual(config.seed_defaults(), 2)
        self.assertEqual(config.backlight_pin(), 13)

    def test_never_overwrites_an_operator_value(self):
        config.save("protocol", "reagent1_secs", 45)
        config.seed_defaults()
        self.assertEqual(config.reagent1_secs(), 45)


class TestCaching(ConfigTestCase):

    def test_values_are_cached_until_reloaded(self):
        self.use_rows([("capture", "extension", "str", ".one", "")])
        self.assertEqual(config.capture_archive_name("x"), "x.one")
        db.write_one("UPDATE config SET value = '.two' WHERE role = 'capture' "
                     "AND name = 'extension' AND active = 1")
        self.assertEqual(config.capture_archive_name("x"), "x.one")
        config.load(reload=True)
        self.assertEqual(config.capture_archive_name("x"), "x.two")


if __name__ == "__main__":
    unittest.main(verbosity=2)
