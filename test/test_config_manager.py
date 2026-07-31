#!/usr/bin/env python3
"""
Unit tests for config_manager (src/config_manager.py).

config_manager is the only module that reads config/instrument.yaml, and the
only place that answers "where do capture archives go and what are they
called". These tests cover the resolution rules — environment override,
relative versus absolute paths, and the defaults that keep a capture working
when the file is missing or malformed.

Run from the project root:
    python test/test_config_manager.py
or with unittest discovery:
    python -m unittest test.test_config_manager
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import config_manager


class ConfigTestCase(unittest.TestCase):
    """Isolates each test from the environment and the module-level cache."""

    # Env vars that steer config_manager; each is saved and cleared for the
    # test, then restored, so tests never leak into one another.
    _MANAGED_ENV = (
        config_manager.CAPTURE_DIR_ENV,
        config_manager.SIMULATION_ENV,
        config_manager.DB_PATH_ENV,
    )

    def setUp(self):
        self._saved_env  = {k: os.environ.pop(k, None) for k in self._MANAGED_ENV}
        self._saved_path = config_manager.INSTRUMENT_PATH
        self._saved_user = config_manager.USER_SETTINGS_PATH
        # Operator settings saved on a real instrument must not colour the
        # tests, so every case starts from an empty (not yet written) file in a
        # temporary directory.
        self._user_dir = tempfile.mkdtemp()
        config_manager.USER_SETTINGS_PATH = Path(self._user_dir) / "user_settings.json"
        config_manager.load(reload=True)

    def tearDown(self):
        config_manager.INSTRUMENT_PATH    = self._saved_path
        config_manager.USER_SETTINGS_PATH = self._saved_user
        shutil.rmtree(self._user_dir, ignore_errors=True)
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        config_manager.load(reload=True)

    def use_config(self, text: str) -> None:
        """Point config_manager at a temporary instrument.yaml."""
        tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        tmp.write(text)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        config_manager.INSTRUMENT_PATH = Path(tmp.name)
        config_manager.load(reload=True)


class TestShippedConfig(ConfigTestCase):
    """The real config/instrument.yaml must parse and be complete."""

    def test_real_config_file_loads(self):
        settings = config_manager.load(reload=True)
        self.assertIn("capture", settings)

    def test_shipped_capture_directory_is_captures(self):
        self.assertEqual(config_manager.capture_dir(),
                         config_manager.PROJECT_ROOT / "captures")

    def test_shipped_archive_name_is_timestamp_dot_h5(self):
        suite_id = config_manager.capture_suite_id(datetime(2026, 7, 19, 13, 43))
        self.assertEqual(suite_id, "20260719_1343")
        self.assertEqual(config_manager.capture_archive_name(suite_id),
                         "20260719_1343.h5")


class TestCaptureDir(ConfigTestCase):

    def test_relative_directory_resolves_against_project_root(self):
        self.use_config("capture:\n  directory: captures\n")
        self.assertEqual(config_manager.capture_dir(),
                         config_manager.PROJECT_ROOT / "captures")

    def test_absolute_directory_is_used_as_given(self):
        self.use_config("capture:\n  directory: /srv/colorimeter/archives\n")
        self.assertEqual(config_manager.capture_dir(),
                         Path("/srv/colorimeter/archives"))

    def test_user_home_is_expanded(self):
        self.use_config("capture:\n  directory: ~/archives\n")
        self.assertEqual(config_manager.capture_dir(), Path.home() / "archives")

    def test_environment_variable_wins_over_config(self):
        self.use_config("capture:\n  directory: /srv/from_config\n")
        os.environ[config_manager.CAPTURE_DIR_ENV] = "/tmp/from_env"
        self.assertEqual(config_manager.capture_dir(), Path("/tmp/from_env"))

    def test_environment_variable_expands_user(self):
        os.environ[config_manager.CAPTURE_DIR_ENV] = "~/from_env"
        self.assertEqual(config_manager.capture_dir(), Path.home() / "from_env")


class TestNaming(ConfigTestCase):

    def test_timestamp_format_is_configurable(self):
        self.use_config('capture:\n  timestamp_format: "%Y-%m-%d_%H%M%S"\n')
        self.assertEqual(
            config_manager.capture_suite_id(datetime(2026, 7, 19, 13, 43, 7)),
            "2026-07-19_134307")

    def test_extension_is_configurable(self):
        self.use_config("capture:\n  extension: .hdf5\n")
        self.assertEqual(config_manager.capture_archive_name("abc"), "abc.hdf5")

    def test_suite_id_defaults_to_now(self):
        before = datetime.now().strftime("%Y%m%d")
        self.assertTrue(config_manager.capture_suite_id().startswith(before))


class TestFallbacks(ConfigTestCase):
    """A mistyped or missing config must not stop the instrument capturing."""

    def test_missing_file_falls_back_to_defaults(self):
        config_manager.INSTRUMENT_PATH = Path("/nonexistent/instrument.yaml")
        config_manager.load(reload=True)
        self.assertEqual(config_manager.capture_dir(),
                         config_manager.PROJECT_ROOT / "captures")
        self.assertEqual(config_manager.capture_archive_name("x"), "x.h5")

    def test_malformed_yaml_falls_back_to_defaults(self):
        self.use_config("capture:\n  directory: [unclosed\n")
        self.assertEqual(config_manager.capture_dir(),
                         config_manager.PROJECT_ROOT / "captures")

    def test_partial_section_keeps_other_defaults(self):
        """Overriding one key must not blank out its neighbours."""
        self.use_config("capture:\n  directory: /srv/only_this\n")
        self.assertEqual(config_manager.capture_dir(), Path("/srv/only_this"))
        self.assertEqual(config_manager.capture_archive_name("x"), "x.h5")

    def test_unrelated_sections_are_preserved(self):
        self.use_config("display:\n  width: 640\ncapture:\n  extension: .h5\n")
        self.assertEqual(config_manager.get("display", "width"), 640)


class TestShippedTunables(ConfigTestCase):
    """The values migrated out of the code keep their historical defaults."""

    def test_shipped_protocol_durations(self):
        self.assertEqual(config_manager.reagent1_secs(), 120)
        self.assertEqual(config_manager.reagent2_secs(), 420)
        self.assertEqual(config_manager.analysis_secs(), 1.5)

    def test_shipped_runtime_timing(self):
        self.assertEqual(config_manager.main_loop_poll_s(), 0.02)
        self.assertEqual(config_manager.shutdown_timeout_s(), 15.0)

    def test_shipped_capture_selection(self):
        self.assertEqual(config_manager.capture_led_start(), 1)
        self.assertEqual(config_manager.capture_led_end(), 10)
        self.assertEqual(config_manager.fallback_exposure_group(), "3")

    def test_shipped_default_rowid(self):
        self.assertEqual(config_manager.default_rowid(), 1)

    def test_shipped_backlight_pin(self):
        self.assertEqual(config_manager.backlight_pin(), 13)

    def test_shipped_db_path_is_src_sqlite(self):
        self.assertEqual(config_manager.db_path(),
                         config_manager.PROJECT_ROOT / "src" / "colorimeter.sqlite")


class TestDeepMerge(ConfigTestCase):
    """Overriding a nested key must not blank out its siblings."""

    def test_nested_override_keeps_sibling_keys(self):
        self.use_config("protocol:\n  reagent1_secs: 5\n")
        self.assertEqual(config_manager.reagent1_secs(), 5)     # overridden
        self.assertEqual(config_manager.reagent2_secs(), 420)   # default kept
        self.assertEqual(config_manager.analysis_secs(), 1.5)   # default kept

    def test_missing_file_falls_back_to_all_defaults(self):
        config_manager.INSTRUMENT_PATH = Path("/nonexistent/instrument.yaml")
        config_manager.load(reload=True)
        self.assertEqual(config_manager.reagent1_secs(), 120)
        self.assertEqual(config_manager.capture_led_end(), 10)


class TestDatabasePath(ConfigTestCase):

    def test_relative_path_resolves_against_project_root(self):
        self.use_config("database:\n  path: data/colorimeter.sqlite\n")
        self.assertEqual(config_manager.db_path(),
                         config_manager.PROJECT_ROOT / "data" / "colorimeter.sqlite")

    def test_absolute_path_is_used_as_given(self):
        self.use_config("database:\n  path: /var/lib/colorimeter.sqlite\n")
        self.assertEqual(config_manager.db_path(),
                         Path("/var/lib/colorimeter.sqlite"))

    def test_environment_variable_wins_over_config(self):
        self.use_config("database:\n  path: /var/from_config.sqlite\n")
        os.environ[config_manager.DB_PATH_ENV] = "/tmp/from_env.sqlite"
        self.assertEqual(config_manager.db_path(), Path("/tmp/from_env.sqlite"))


class TestSimulationOverlay(ConfigTestCase):
    """The simulation_overrides profile applies only when simulation is active."""

    _SIM_CONFIG = (
        "protocol:\n"
        "  reagent1_secs: 120\n"
        "  reagent2_secs: 420\n"
        "simulation: false\n"
        "simulation_overrides:\n"
        "  protocol:\n"
        "    reagent1_secs: 10\n"
        "    reagent2_secs: 10\n"
    )

    def test_env_off_keeps_real_durations(self):
        self.use_config(self._SIM_CONFIG)
        os.environ[config_manager.SIMULATION_ENV] = "0"
        config_manager.load(reload=True)
        self.assertFalse(config_manager.is_simulation())
        self.assertEqual(config_manager.reagent1_secs(), 120)

    def test_env_on_applies_overrides(self):
        self.use_config(self._SIM_CONFIG)
        os.environ[config_manager.SIMULATION_ENV] = "1"
        config_manager.load(reload=True)
        self.assertTrue(config_manager.is_simulation())
        self.assertEqual(config_manager.reagent1_secs(), 10)
        self.assertEqual(config_manager.reagent2_secs(), 10)

    def test_overrides_leave_unoverridden_keys_alone(self):
        # analysis_secs is set in the base protocol but absent from the
        # overrides, so it keeps its value even in simulation.
        self.use_config(
            "protocol:\n"
            "  analysis_secs: 99\n"
            "simulation_overrides:\n"
            "  protocol:\n"
            "    reagent1_secs: 10\n"
        )
        os.environ[config_manager.SIMULATION_ENV] = "1"
        config_manager.load(reload=True)
        self.assertEqual(config_manager.reagent1_secs(), 10)   # overridden
        self.assertEqual(config_manager.analysis_secs(), 99)   # untouched

    def test_yaml_flag_applies_when_env_unset(self):
        self.use_config(self._SIM_CONFIG.replace("simulation: false",
                                                 "simulation: true"))
        self.assertTrue(config_manager.is_simulation())
        self.assertEqual(config_manager.reagent1_secs(), 10)

    def test_env_wins_over_yaml_flag(self):
        self.use_config(self._SIM_CONFIG.replace("simulation: false",
                                                 "simulation: true"))
        os.environ[config_manager.SIMULATION_ENV] = "0"
        config_manager.load(reload=True)
        self.assertFalse(config_manager.is_simulation())
        self.assertEqual(config_manager.reagent1_secs(), 120)


class TestUserSettings(ConfigTestCase):
    """
    Values the operator changes on the instrument (Menu -> Config) are saved to
    user_settings.json and win over every other layer.
    """

    _CONFIG = (
        "protocol:\n"
        "  reagent1_secs: 120\n"
        "  reagent2_secs: 420\n"
        "simulation: false\n"
        "simulation_overrides:\n"
        "  protocol:\n"
        "    reagent1_secs: 10\n"
    )

    def test_nothing_saved_leaves_the_config_file_in_charge(self):
        self.use_config(self._CONFIG)
        self.assertEqual(config_manager.reagent1_secs(), 120)
        self.assertEqual(config_manager.user_settings(), {})

    def test_saved_value_wins_over_the_config_file(self):
        self.use_config(self._CONFIG)
        self.assertTrue(
            config_manager.save_user_setting("protocol", "reagent1_secs", 45))
        self.assertEqual(config_manager.reagent1_secs(), 45)

    def test_saved_value_survives_a_reload(self):
        self.use_config(self._CONFIG)
        config_manager.save_user_setting("protocol", "reagent1_secs", 45)
        config_manager.load(reload=True)
        self.assertEqual(config_manager.reagent1_secs(), 45)

    def test_saved_value_wins_over_the_simulation_profile(self):
        """The point of the Config screen: a custom wait holds in either mode."""
        self.use_config(self._CONFIG)
        os.environ[config_manager.SIMULATION_ENV] = "1"
        config_manager.load(reload=True)
        self.assertEqual(config_manager.reagent1_secs(), 10)   # profile applies
        config_manager.save_user_setting("protocol", "reagent1_secs", 45)
        self.assertEqual(config_manager.reagent1_secs(), 45)

    def test_saving_one_key_leaves_its_siblings_alone(self):
        self.use_config(self._CONFIG)
        config_manager.save_user_setting("protocol", "reagent1_secs", 45)
        self.assertEqual(config_manager.reagent2_secs(), 420)
        self.assertEqual(config_manager.analysis_secs(), 1.5)

    def test_configured_reports_the_file_value_regardless(self):
        self.use_config(self._CONFIG)
        config_manager.save_user_setting("protocol", "reagent1_secs", 45)
        self.assertEqual(config_manager.configured("protocol", "reagent1_secs"), 120)

    def test_clearing_restores_the_file_value(self):
        self.use_config(self._CONFIG)
        config_manager.save_user_setting("protocol", "reagent1_secs", 45)
        self.assertTrue(
            config_manager.clear_user_setting("protocol", "reagent1_secs"))
        self.assertEqual(config_manager.reagent1_secs(), 120)
        self.assertEqual(config_manager.user_settings(), {})

    def test_clearing_what_was_never_set_is_a_no_op(self):
        self.use_config(self._CONFIG)
        self.assertTrue(
            config_manager.clear_user_setting("protocol", "reagent1_secs"))
        self.assertEqual(config_manager.reagent1_secs(), 120)

    def test_deleting_the_file_restores_the_file_values(self):
        self.use_config(self._CONFIG)
        config_manager.save_user_setting("protocol", "reagent1_secs", 45)
        os.unlink(config_manager.USER_SETTINGS_PATH)
        config_manager.load(reload=True)
        self.assertEqual(config_manager.reagent1_secs(), 120)

    def test_corrupt_settings_file_is_ignored(self):
        self.use_config(self._CONFIG)
        Path(config_manager.USER_SETTINGS_PATH).write_text("{not json")
        config_manager.load(reload=True)
        self.assertEqual(config_manager.reagent1_secs(), 120)

    def test_saved_file_holds_only_what_changed(self):
        self.use_config(self._CONFIG)
        config_manager.save_user_setting("protocol", "reagent2_secs", 30)
        saved = json.loads(Path(config_manager.USER_SETTINGS_PATH).read_text())
        self.assertEqual(saved, {"protocol": {"reagent2_secs": 30}})


class TestCaching(ConfigTestCase):

    def test_settings_are_cached_until_reloaded(self):
        self.use_config("capture:\n  extension: .one\n")
        self.assertEqual(config_manager.capture_archive_name("x"), "x.one")
        config_manager.INSTRUMENT_PATH.write_text("capture:\n  extension: .two\n")
        self.assertEqual(config_manager.capture_archive_name("x"), "x.one")
        config_manager.load(reload=True)
        self.assertEqual(config_manager.capture_archive_name("x"), "x.two")


if __name__ == "__main__":
    unittest.main(verbosity=2)
