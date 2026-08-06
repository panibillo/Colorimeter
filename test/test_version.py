#!/usr/bin/env python3
"""
Unit tests for version (src/version.py).

Two separable things are checked, and they fail for different reasons:

  * check_archive()/check_database() — the message a desktop tool shows when
    it is handed an archive it cannot read.  A wrong answer here means a tool
    either refuses a file it could have read, or crashes deep inside on one it
    could not.

  * the drift guard — every format version this checkout *writes* must fall
    inside the range it says it can *read*.  Bumping capture_h5.SCHEMA_VERSION
    without widening version.READS_SCHEMA would leave the instrument writing
    archives its own desktop tools reject, which is exactly the confusion the
    version model exists to prevent.  That one fails the moment a writer moves.

Run from the project root:
    python test/test_version.py
or with unittest discovery:
    python -m unittest test.test_version
"""

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import version


class TestArchiveCompatibility(unittest.TestCase):
    """check_archive() is what stands between a devdesk tool and a bad file."""

    def test_a_current_archive_is_readable(self):
        self.assertIsNone(version.check_archive(
            {"schema_version": 2, "meta_version": 2, "software_ver": "0.3.0"}))

    def test_the_oldest_supported_archive_is_readable(self):
        oldest_schema = version.READS_SCHEMA[0]
        oldest_meta   = version.READS_META[0]
        self.assertIsNone(version.check_archive(
            {"schema_version": oldest_schema, "meta_version": oldest_meta}))

    def test_a_missing_attribute_reads_as_version_one(self):
        """An archive predating an attribute is that attribute's version 1."""
        self.assertIsNone(version.check_archive({}))

    def test_a_newer_archive_says_to_update_this_checkout(self):
        newer = version.READS_SCHEMA[1] + 1
        msg = version.check_archive({"schema_version": newer})
        self.assertIsNotNone(msg)
        self.assertIn(str(newer), msg)
        self.assertIn(version.__version__, msg)
        self.assertIn("newer", msg)

    def test_the_message_names_the_software_that_wrote_it(self):
        """The first thing you want to know is which build to go look at."""
        msg = version.check_archive({"schema_version": 99,
                                     "software_ver": "0.9.1"})
        self.assertIn("0.9.1", msg)

    def test_metadata_is_checked_as_well_as_layout(self):
        msg = version.check_archive({"schema_version": 2,
                                     "meta_version": version.READS_META[1] + 1})
        self.assertIsNotNone(msg)
        self.assertIn("metadata", msg)

    def test_a_dropped_old_format_says_to_use_an_older_checkout(self):
        oldest = version.READS_SCHEMA[0]
        if oldest <= 1:
            self.skipTest("no archive format has been dropped yet")
        msg = version.check_archive({"schema_version": oldest - 1})
        self.assertIn("older", msg)


class TestDatabaseCompatibility(unittest.TestCase):
    """db_bootstrap gates the app; this is for tools opening a database directly."""

    def test_a_current_database_is_runnable(self):
        self.assertIsNone(version.check_database(version.READS_DB[1]))

    def test_a_pre_migration_database_is_refused(self):
        msg = version.check_database(0)
        self.assertIsNotNone(msg)
        self.assertIn("older", msg)

    def test_a_newer_database_is_refused(self):
        msg = version.check_database(version.READS_DB[1] + 1)
        self.assertIn("newer", msg)


class TestNoDrift(unittest.TestCase):
    """
    What this checkout writes, it must also be able to read.

    Each of these imports the module that owns the constant, so the assertion
    is against the live value rather than a copy of it.
    """

    def assertInRange(self, wrote, range_, what):
        oldest, newest = range_
        self.assertTrue(
            oldest <= wrote <= newest,
            f"{what} writes version {wrote} but src/version.py says this "
            f"checkout reads {oldest}-{newest}. Widen the range there, bump "
            f"__version__'s MINOR, and add a CHANGELOG entry.")

    def test_the_archive_layout_it_writes_is_one_it_reads(self):
        import capture_h5
        self.assertInRange(capture_h5.SCHEMA_VERSION, version.READS_SCHEMA,
                           "capture_h5.SCHEMA_VERSION")

    def test_the_metadata_it_writes_is_a_shape_it_reads(self):
        import capture_meta
        self.assertInRange(capture_meta.META_VERSION, version.READS_META,
                           "capture_meta.META_VERSION")

    def test_the_database_schema_it_builds_is_one_it_runs_on(self):
        import db_bootstrap
        self.assertInRange(db_bootstrap.SCHEMA_VERSION, version.READS_DB,
                           "db_bootstrap.SCHEMA_VERSION")


class TestVersionString(unittest.TestCase):

    def test_it_is_plain_semver(self):
        """
        MAJOR.MINOR.PATCH, no suffix — being on 0.x is the alpha statement, so
        there is no second counter to keep moving.  See src/version.py.
        """
        parts = version.__version__.split(".")
        self.assertEqual(len(parts), 3, version.__version__)
        for part in parts:
            self.assertTrue(part.isdigit(), version.__version__)


if __name__ == "__main__":
    unittest.main(verbosity=2)
