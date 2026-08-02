#!/usr/bin/env python3
"""
Unit tests for db_manager's db_path() (src/db_manager.py).

The database location used to be resolved by config_manager against
instrument.yaml; it moved into db_manager itself so that config_repo (which
reads its parameters out of the database) does not have to say where the
database is. These tests cover the same resolution rules the old
config_manager tests did: environment override, relative versus absolute,
and ~ expansion.

Run from the project root:
    python test/test_db_manager.py
or with unittest discovery:
    python -m unittest test.test_db_manager
"""

import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import db_manager as db


class TestDbPath(unittest.TestCase):

    def setUp(self):
        self._saved_env = os.environ.pop(db.DB_PATH_ENV, None)

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop(db.DB_PATH_ENV, None)
        else:
            os.environ[db.DB_PATH_ENV] = self._saved_env

    def test_default_is_src_colorimeter_sqlite(self):
        self.assertEqual(db.db_path(), db.PROJECT_ROOT / "src" / "colorimeter.sqlite")

    def test_relative_env_resolves_against_project_root(self):
        os.environ[db.DB_PATH_ENV] = "data/colorimeter.sqlite"
        self.assertEqual(db.db_path(), db.PROJECT_ROOT / "data" / "colorimeter.sqlite")

    def test_absolute_env_is_used_as_given(self):
        os.environ[db.DB_PATH_ENV] = "/var/lib/colorimeter.sqlite"
        self.assertEqual(db.db_path(), Path("/var/lib/colorimeter.sqlite"))

    def test_env_expands_user(self):
        os.environ[db.DB_PATH_ENV] = "~/from_env.sqlite"
        self.assertEqual(db.db_path(), Path.home() / "from_env.sqlite")


if __name__ == "__main__":
    unittest.main(verbosity=2)
