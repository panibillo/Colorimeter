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

import os
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

    def setUp(self):
        self._saved_env  = os.environ.pop(config_manager.CAPTURE_DIR_ENV, None)
        self._saved_path = config_manager.INSTRUMENT_PATH
        config_manager.load(reload=True)

    def tearDown(self):
        config_manager.INSTRUMENT_PATH = self._saved_path
        os.environ.pop(config_manager.CAPTURE_DIR_ENV, None)
        if self._saved_env is not None:
            os.environ[config_manager.CAPTURE_DIR_ENV] = self._saved_env
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
