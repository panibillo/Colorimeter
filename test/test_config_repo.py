#!/usr/bin/env python3
"""
Unit tests for config_repo (src/config_repo.py) and db_bootstrap.

config_repo is the only module that reads the database's `config` table, and
the only place that answers "where do capture archives go and what are they
called". These tests cover reading and coercion, the two independent kinds of
default (the Default button vs Restore Factory Defaults), the write
transactions and the invariants the schema enforces on them.

What is deliberately *not* here any more: tests that a missing table, an empty
table or a malformed value silently falls back to values compiled into Python.
That fallback is gone. db_bootstrap guarantees the rows exist before anything
reads them, and a genuinely missing parameter now raises ConfigMissing, which
is what TestMissingParameters checks instead.

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

import db_bootstrap
import db_manager as db
import config_repo as config
import version


class ConfigTestCase(unittest.TestCase):
    """
    Isolates each test in a throwaway database built the way the application
    builds its own — db_bootstrap.ensure() — so every test starts from the real
    seeded config table and the live database is never touched.
    """

    def setUp(self):
        self._saved_dbname = db.DBNAME
        self._tmpdir = tempfile.mkdtemp()
        self._tmpdb  = Path(self._tmpdir) / "config_test.sqlite"
        db.DBNAME = str(self._tmpdb)
        db_bootstrap.ensure(self._tmpdb)
        config.load(reload=True)

    def tearDown(self):
        db.DBNAME = self._saved_dbname
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        config.load(reload=True)

    def rows(self, sql, vals=()):
        """Query the test database directly, bypassing config_repo's cache."""
        con = sqlite3.connect(self._tmpdb)
        try:
            return con.execute(sql, vals).fetchall()
        finally:
            con.close()

    def assertOneActiveRowPerParameter(self):
        """The invariant every write has to leave the table in."""
        broken = self.rows(
            "SELECT role, name FROM config "
            "GROUP BY role, name HAVING SUM(active) <> 1")
        self.assertEqual(broken, [], "parameters without exactly one active row")
        for flag in ("active", "isdefault"):
            dupes = self.rows(
                f"SELECT role, name FROM config WHERE {flag} = 1 "
                "GROUP BY role, name HAVING COUNT(*) > 1")
            self.assertEqual(dupes, [], f"duplicate {flag} rows")


class TestSeededValues(ConfigTestCase):
    """A freshly bootstrapped database holds what config_defaults.sql says."""

    def test_protocol_durations(self):
        self.assertEqual(config.reagent1_secs(), 120)
        self.assertEqual(config.reagent2_secs(), 420)
        self.assertEqual(config.analysis_secs(), 1.5)

    def test_runtime_timing(self):
        self.assertEqual(config.main_loop_poll_s(), 0.02)
        self.assertEqual(config.shutdown_timeout_s(), 15.0)

    def test_capture_selection(self):
        self.assertEqual(config.capture_led_color_order()[0], "photo_red")
        self.assertEqual(len(config.capture_led_color_order()), 10)

    def test_backlight_pin(self):
        self.assertEqual(config.backlight_pin(), 13)

    def test_folded_identity_and_defaults(self):
        """device/users/testkit/testlot are config roles now, not tables."""
        self.assertEqual(config.get("device", "hardware_sn"), "XCN-001")
        self.assertEqual(config.get("defaults", "testkit"), "Ascel 458201")
        self.assertEqual(config.get("defaults", "testlot"), "0 12V601-HH")
        self.assertEqual(config.get("defaults", "user"), "William Olsen")

    def test_every_parameter_starts_as_its_own_default(self):
        self.assertEqual(config.overrides(), [])
        self.assertOneActiveRowPerParameter()


class TestCaptureDir(ConfigTestCase):
    """capture_dir() resolves capture.directory; there is no env override."""

    def test_relative_directory_resolves_against_project_root(self):
        config.save("capture", "directory", "captures")
        self.assertEqual(config.capture_dir(), config.PROJECT_ROOT / "captures")

    def test_absolute_directory_is_used_as_given(self):
        config.save("capture", "directory", "/var/tmp/shots")
        self.assertEqual(config.capture_dir(), Path("/var/tmp/shots"))

    def test_user_home_is_expanded(self):
        config.save("capture", "directory", "~/shots")
        self.assertEqual(config.capture_dir(), Path.home() / "shots")


class TestNaming(ConfigTestCase):
    """The archive's suite id and filename."""

    def test_timestamp_format_is_configurable(self):
        when = datetime(2026, 7, 19, 13, 43, 21)
        self.assertEqual(config.capture_suite_id(when), "20260719_1343")
        config.save("capture", "timestamp_format", "%Y-%m-%d")
        self.assertEqual(config.capture_suite_id(when), "2026-07-19")

    def test_extension_is_configurable(self):
        self.assertEqual(config.capture_archive_name("X"), "X.h5")
        config.save("capture", "extension", ".hdf5")
        self.assertEqual(config.capture_archive_name("X"), "X.hdf5")

    def test_suite_id_defaults_to_now(self):
        self.assertEqual(config.capture_suite_id(),
                         datetime.now().strftime("%Y%m%d_%H%M"))


class TestMissingParameters(ConfigTestCase):
    """
    A parameter with no active row is a bug, and says so.

    This replaces the old fallback tests. There is no longer a table of values
    in Python for a missing row to fall back to, which is the point: a value
    the operator cannot see in the database cannot be in force.
    """

    def test_unknown_parameter_raises(self):
        with self.assertRaises(config.ConfigMissing):
            config.get("protocol", "no_such_parameter")

    def test_the_exception_names_the_parameter(self):
        with self.assertRaises(config.ConfigMissing) as caught:
            config.get("nope", "missing")
        self.assertIn("nope.missing", str(caught.exception))

    def test_a_deactivated_parameter_raises(self):
        con = sqlite3.connect(self._tmpdb)
        con.execute("UPDATE config SET active = 0 WHERE role = 'protocol' "
                    "AND name = 'reagent1_secs'")
        con.commit()
        con.close()
        config.load(reload=True)
        with self.assertRaises(config.ConfigMissing):
            config.reagent1_secs()

    def test_a_malformed_value_raises_rather_than_being_guessed_at(self):
        con = sqlite3.connect(self._tmpdb)
        con.execute("UPDATE config SET value = 'later' WHERE role = 'protocol' "
                    "AND name = 'reagent1_secs'")
        con.commit()
        con.close()
        config.load(reload=True)
        with self.assertRaises(config.ConfigMissing):
            config.reagent1_secs()

    def test_saving_an_unknown_parameter_is_refused(self):
        self.assertEqual(config.save("nope", "missing", 1), 0)

    def test_restoring_an_unknown_parameter_is_refused(self):
        self.assertEqual(config.restore("nope", "missing"), 0)


class TestCoercion(ConfigTestCase):
    """vtype decides how the stored TEXT reads back."""

    def test_types_come_back_as_declared(self):
        self.assertIsInstance(config.reagent1_secs(), int)
        self.assertIsInstance(config.analysis_secs(), float)
        self.assertIsInstance(config.get("defaults", "exposuregroup"), str)

    def test_valid_accepts_what_parses(self):
        self.assertTrue(config.valid("42", "int"))
        self.assertTrue(config.valid("4.2", "float"))
        self.assertTrue(config.valid("anything at all", "str"))
        self.assertTrue(config.valid("yes", "bool"))

    def test_valid_rejects_what_does_not(self):
        self.assertFalse(config.valid("4.2", "int"))
        self.assertFalse(config.valid("later", "int"))
        self.assertFalse(config.valid("soon", "float"))


class TestSaving(ConfigTestCase):
    """save() supersedes without losing what was there."""

    def test_saved_value_is_in_force(self):
        self.assertGreater(config.save("protocol", "reagent1_secs", 45), 0)
        self.assertEqual(config.reagent1_secs(), 45)

    def test_saved_value_survives_a_reload(self):
        config.save("protocol", "reagent1_secs", 45)
        config.load(reload=True)
        self.assertEqual(config.reagent1_secs(), 45)

    def test_saving_one_parameter_leaves_its_siblings_alone(self):
        config.save("protocol", "reagent1_secs", 45)
        self.assertEqual(config.reagent2_secs(), 420)

    def test_the_superseded_value_is_kept_as_history(self):
        config.save("protocol", "reagent1_secs", 45)
        values = [r[0] for r in config.history("protocol", "reagent1_secs")]
        self.assertEqual(values, ["45", "120"])

    def test_reusing_a_former_value_reuses_its_row(self):
        """
        Toggling between two values must not grow the table, and Browse must
        show each distinct value once. un_config_value is what guarantees it.
        """
        for value in (45, 120, 45, 120, 45):
            config.save("protocol", "reagent1_secs", value)
        self.assertEqual(len(config.history("protocol", "reagent1_secs")), 2)
        self.assertEqual(config.reagent1_secs(), 45)
        self.assertOneActiveRowPerParameter()

    def test_source_records_creation_and_is_never_rewritten(self):
        """
        `source` is an add-user, not an edit-user. Re-activating a row the seed
        created must leave it saying 'seed' — resolution does not consult it,
        and Restore Factory Defaults depends on it staying put.
        """
        config.save("protocol", "reagent1_secs", 45)
        config.save("protocol", "reagent1_secs", 120)     # back to the seed row
        sources = dict((r[0], r[2])
                       for r in config.history("protocol", "reagent1_secs"))
        self.assertEqual(sources["120"], config.SOURCE_SEED)
        self.assertEqual(sources["45"], config.SOURCE_OPERATOR)

    def test_the_invariants_hold_after_many_writes(self):
        for value in (45, 60, 45, 90, 120):
            config.save("protocol", "reagent1_secs", value)
        config.save("capture", "extension", ".hdf5")
        self.assertOneActiveRowPerParameter()


class TestDefaultButton(ConfigTestCase):
    """
    isdefault is what the editor's Default button offers, and only that.

    It is movable: an operator may commission a value for this site. That is a
    different thing from what the unit shipped with — see TestFactoryReset.
    """

    def test_default_reports_the_commissioned_value_regardless(self):
        config.save("protocol", "reagent1_secs", 45)
        self.assertEqual(config.reagent1_secs(), 45)
        self.assertEqual(config.default("protocol", "reagent1_secs"), 120)

    def test_restore_puts_it_back(self):
        config.save("protocol", "reagent1_secs", 45)
        self.assertGreater(config.restore("protocol", "reagent1_secs"), 0)
        self.assertEqual(config.reagent1_secs(), 120)
        self.assertOneActiveRowPerParameter()

    def test_restoring_what_was_never_changed_writes_nothing(self):
        self.assertEqual(config.restore("protocol", "reagent1_secs"), 0)

    def test_overrides_lists_what_restore_would_change(self):
        self.assertEqual(config.overrides(), [])
        config.save("protocol", "reagent1_secs", 45)
        self.assertEqual(config.overrides(), [("protocol", "reagent1_secs")])
        config.restore("protocol", "reagent1_secs")
        self.assertEqual(config.overrides(), [])

    def test_set_default_commissions_the_value(self):
        config.set_default("protocol", "reagent1_secs", 45)
        self.assertEqual(config.reagent1_secs(), 45)
        self.assertEqual(config.default("protocol", "reagent1_secs"), 45)
        self.assertEqual(config.overrides(), [],
                         "a commissioned value is not an override")
        self.assertOneActiveRowPerParameter()

    def test_set_default_does_not_rewrite_source(self):
        """The factory row must stay identifiable after Default is pressed."""
        config.set_default("protocol", "reagent1_secs", 45)
        sources = dict((r[0], r[2])
                       for r in config.history("protocol", "reagent1_secs"))
        self.assertEqual(sources["120"], config.SOURCE_SEED)


class TestFactoryReset(ConfigTestCase):
    """
    restore_all() goes back to what the unit shipped with, not to whatever the
    operator has since commissioned. It keys on source='seed' because that is
    the only thing an operator pressing Default cannot move.
    """

    def test_puts_every_parameter_back(self):
        config.save("protocol", "reagent1_secs", 45)
        config.save("capture", "extension", ".hdf5")
        self.assertEqual(config.restore_all(), 2)
        self.assertEqual(config.reagent1_secs(), 120)
        self.assertEqual(config.capture_archive_name("X"), "X.h5")
        self.assertOneActiveRowPerParameter()

    def test_is_a_noop_on_an_untouched_instrument(self):
        self.assertEqual(config.restore_all(), 0)
        self.assertEqual(config.reagent1_secs(), 120)

    def test_beats_a_commissioned_site_value(self):
        config.set_default("protocol", "reagent1_secs", 45)
        self.assertEqual(config.reagent1_secs(), 45)
        config.restore_all()
        self.assertEqual(config.reagent1_secs(), 120,
                         "factory reset must undo a commissioned value")

    def test_leaves_the_commissioned_value_on_the_default_button(self):
        """Restoring the factory undoes what is in force, not what Default offers."""
        config.set_default("protocol", "reagent1_secs", 45)
        config.restore_all()
        self.assertEqual(config.default("protocol", "reagent1_secs"), 45)
        self.assertGreater(config.restore("protocol", "reagent1_secs"), 0)
        self.assertEqual(config.reagent1_secs(), 45)

    def test_never_activates_a_history_row(self):
        """
        Values seeded only so Browse can offer them carry source='history' and
        must never be mistaken for what the unit shipped with.
        """
        config.save("defaults", "testkit", "Hach 1")
        config.restore_all()
        self.assertEqual(config.get("defaults", "testkit"), "Ascel 458201")
        self.assertOneActiveRowPerParameter()


class TestEditorReads(ConfigTestCase):
    """roles() / items() / history() feed the config editor's three screens."""

    def test_roles_are_the_screens_worth_offering(self):
        self.assertEqual(config.roles(),
                         ["capture", "defaults", "device", "hardware",
                          "protocol", "roi", "runtime"])

    def test_items_are_alphabetical_with_type_and_lock(self):
        names = [row[0] for row in config.items("device")]
        self.assertEqual(names, ["description", "hardware_sn", "software_ver"])
        by_name = {row[0]: row for row in config.items("device")}
        self.assertEqual(by_name["hardware_sn"][2], "XCN-001")
        self.assertEqual(by_name["hardware_sn"][3], 0, "serial must be locked")
        self.assertEqual(by_name["description"][3], 1)
        self.assertEqual(by_name["software_ver"][3], 0,
                         "the version must be locked")


class TestVersionStamp(ConfigTestCase):
    """
    device.software_ver is the one parameter the source tree owns rather than
    config_defaults.sql.  db_bootstrap.ensure() stamps it at every launch, so
    setUp has already run it once.
    """

    def active_version_rows(self):
        return self.rows("SELECT value, editable, source, isdefault FROM config"
                         " WHERE role='device' AND name='software_ver'"
                         " AND active=1")

    def test_the_seed_file_does_not_carry_a_version(self):
        """A value in the SQL would be a second version number to keep in step."""
        seeded = self.rows("SELECT value FROM config WHERE role='device' "
                           "AND name='software_ver' AND source='seed'")
        self.assertEqual(seeded, [])

    def test_launch_stamps_the_running_version(self):
        self.assertEqual(self.active_version_rows(),
                         [(version.__version__, 0, "code", 1)])

    def test_restamping_the_same_version_writes_nothing(self):
        self.assertEqual(config.stamp_version(), 0)
        self.assertEqual(len(self.rows(
            "SELECT 1 FROM config WHERE role='device' AND name='software_ver'")),
            1)

    def test_a_bump_supersedes_and_keeps_the_commissioned_row(self):
        was = version.__version__
        try:
            version.__version__ = "9.9.9"
            self.assertTrue(config.stamp_version())
        finally:
            version.__version__ = was

        self.assertEqual(config.get("device", "software_ver"), "9.9.9")
        self.assertEqual(
            self.rows("SELECT value, active, isdefault FROM config "
                      "WHERE role='device' AND name='software_ver' "
                      "ORDER BY value"),
            [(was, 0, 1), ("9.9.9", 1, 0)],
            "the commissioned-at row stays the default and stays in the table")
        self.assertOneActiveRowPerParameter()

    def test_restore_all_restamps_rather_than_reverting(self):
        """
        The regression this design is most exposed to: software_ver has no
        source='seed' row for restore_all to raise, so without its restamp the
        parameter would be left with nothing active at all.
        """
        was = version.__version__
        try:
            version.__version__ = "9.9.9"
            config.stamp_version()
            config.restore_all()
            self.assertEqual(self.active_version_rows(),
                             [("9.9.9", 0, "code", 0)])
            self.assertEqual(config.get("device", "software_ver"), "9.9.9")
        finally:
            version.__version__ = was
        self.assertOneActiveRowPerParameter()

    def test_restore_all_does_not_count_the_version(self):
        """It is restamped, not restored, so it is not a parameter 'put back'."""
        config.save("protocol", "reagent1_secs", 45)
        self.assertEqual(config.restore_all(), 1)

    def test_entity_attributes_sort_beside_their_entity(self):
        """The '<entity>_<attr>' naming is what keeps a lot next to its expiry."""
        names = [row[0] for row in config.items("defaults")]
        self.assertEqual(names[names.index("testlot")
                               :names.index("testlot") + 2],
                         ["testlot", "testlot_expire_dt"])

    def test_history_offers_a_carried_over_value(self):
        values = [r[0] for r in config.history("defaults", "testkit")]
        self.assertCountEqual(values, ["Ascel 458201", "Hach 1"])

    def test_history_puts_the_row_in_force_first(self):
        config.save("protocol", "reagent1_secs", 45)
        first = config.history("protocol", "reagent1_secs")[0]
        self.assertEqual(first[0], "45")
        self.assertEqual(first[3], 1, "active row sorts first")


class TestSnapshot(ConfigTestCase):
    """snapshot() is what makes a capture archive self-describing."""

    def test_groups_every_active_value_by_role(self):
        snap = config.snapshot()
        self.assertEqual(snap["protocol"]["reagent1_secs"], "120")
        self.assertEqual(snap["device"]["hardware_sn"], "XCN-001")

    def test_values_are_the_stored_text(self):
        snap = config.snapshot()
        self.assertIsInstance(snap["capture"]["led_color_order"], str)

    def test_follows_what_is_in_force(self):
        config.save("protocol", "reagent1_secs", 45)
        self.assertEqual(config.snapshot()["protocol"]["reagent1_secs"], "45")


class TestCaching(ConfigTestCase):
    """Values are read once; the write functions invalidate for you."""

    def test_values_are_cached_until_reloaded(self):
        self.assertEqual(config.reagent1_secs(), 120)
        con = sqlite3.connect(self._tmpdb)
        con.execute("UPDATE config SET value = '99' WHERE role = 'protocol' "
                    "AND name = 'reagent1_secs' AND active = 1")
        con.commit()
        con.close()
        self.assertEqual(config.reagent1_secs(), 120, "should still be cached")
        config.load(reload=True)
        self.assertEqual(config.reagent1_secs(), 99)

    def test_writes_invalidate_the_cache(self):
        config.reagent1_secs()
        config.save("protocol", "reagent1_secs", 45)
        self.assertEqual(config.reagent1_secs(), 45)


class TestBootstrap(ConfigTestCase):
    """db_bootstrap builds and repairs; setUp has already run it once."""

    def test_running_again_changes_nothing(self):
        result = db_bootstrap.ensure(self._tmpdb)
        self.assertEqual(result.added, 0)
        self.assertEqual(result.state, "current")

    def test_a_deleted_parameter_comes_back(self):
        con = sqlite3.connect(self._tmpdb)
        con.execute("DELETE FROM config WHERE role = 'runtime' "
                    "AND name = 'shutdown_timeout_s'")
        con.commit()
        con.close()
        self.assertEqual(db_bootstrap.ensure(self._tmpdb).added, 1)
        config.load(reload=True)
        self.assertEqual(config.shutdown_timeout_s(), 15.0)

    def test_an_operator_value_is_not_overwritten(self):
        config.save("protocol", "reagent1_secs", 45)
        db_bootstrap.ensure(self._tmpdb)
        config.load(reload=True)
        self.assertEqual(config.reagent1_secs(), 45)

    def test_a_deactivated_parameter_is_healed(self):
        """
        The seed file's closing UPDATE reactivates any row still flagged
        isdefault, without inserting anything.
        """
        con = sqlite3.connect(self._tmpdb)
        con.execute("UPDATE config SET active = 0 WHERE role = 'capture' "
                    "AND name = 'extension'")
        con.commit()
        con.close()
        self.assertEqual(db_bootstrap.ensure(self._tmpdb).added, 0)
        config.load(reload=True)
        self.assertEqual(config.capture_archive_name("X"), "X.h5")

    def test_a_parameter_beyond_repair_is_fatal(self):
        con = sqlite3.connect(self._tmpdb)
        con.execute("UPDATE config SET active = 0, isdefault = 0 "
                    "WHERE role = 'capture' AND name = 'extension'")
        con.commit()
        con.close()
        with self.assertRaises(db_bootstrap.DatabaseError) as caught:
            db_bootstrap.ensure(self._tmpdb)
        self.assertIn("capture.extension", str(caught.exception))

    def test_a_pre_migration_database_is_refused(self):
        legacy = Path(self._tmpdir) / "legacy.sqlite"
        con = sqlite3.connect(legacy)
        con.executescript(
            "CREATE TABLE config (rowid INTEGER PRIMARY KEY NOT NULL, "
            "active INTEGER, isdefault INTEGER, add_dt TEXT, role TEXT, "
            "name TEXT, vtype TEXT, value TEXT, profile TEXT, source TEXT, "
            "remark TEXT);")
        con.commit()
        con.close()
        db.DBNAME = str(legacy)          # ensure() writes through db_manager
        with self.assertRaises(db_bootstrap.DatabaseError) as caught:
            db_bootstrap.ensure(legacy)
        self.assertIn("migrat", str(caught.exception).lower())

    def test_a_path_db_manager_does_not_know_about_is_refused(self):
        """
        ensure() builds the file it is given, but the version stamp goes
        through config_repo and so through db_manager.  Stamping one database
        while building another is a bug that would surface much later.
        """
        elsewhere = Path(self._tmpdir) / "elsewhere.sqlite"
        with self.assertRaises(db_bootstrap.DatabaseError) as caught:
            db_bootstrap.ensure(elsewhere)
        self.assertIn("DBNAME", str(caught.exception))
        self.assertFalse(elsewhere.exists())


class TestRestoredReporting(ConfigTestCase):
    """
    What ensure() says about parameters it had to bring back from a default.

    The recovery itself is not new — provisioning/config_defaults.sql has
    always ended by reactivating any isdefault row with nothing in force.  It
    used to happen silently, so an instrument could come up on a commissioned
    default with nobody told.  These pin down the reporting the boot splash
    shows, one dialog and one OK press per parameter.
    """

    PARAM = "role='protocol' AND name='reagent1_secs'"

    def test_a_healthy_relaunch_reports_nothing(self):
        result = db_bootstrap.ensure(self._tmpdb)
        self.assertEqual(result.added, 0)
        self.assertEqual(result.restored, [])

    def test_a_new_database_restores_nothing_despite_every_row_being_new(self):
        # setUp already created this one.  Naming all 28 parameters on a first
        # boot would be one OK press each and would say nothing.
        fresh = Path(self._tmpdir) / "brand_new.sqlite"
        db.DBNAME = str(fresh)
        result = db_bootstrap.ensure(fresh)
        self.assertEqual(result.state, "created")
        self.assertEqual(result.restored, [])

    def test_a_deactivated_parameter_is_named_as_reactivated(self):
        con = sqlite3.connect(self._tmpdb)
        con.execute(f"UPDATE config SET active = 0 WHERE {self.PARAM}")
        con.commit()
        con.close()

        result = db_bootstrap.ensure(self._tmpdb)

        self.assertEqual(result.added, 0)      # nothing was inserted to do it
        self.assertEqual([(r.role, r.name, r.value, r.how)
                          for r in result.restored],
                         [("protocol", "reagent1_secs", "120",
                           db_bootstrap.REACTIVATED)])

    def test_a_deleted_parameter_is_named_as_reseeded(self):
        con = sqlite3.connect(self._tmpdb)
        con.execute(f"DELETE FROM config WHERE {self.PARAM}")
        con.commit()
        con.close()

        result = db_bootstrap.ensure(self._tmpdb)

        reseeded = [r for r in result.restored
                    if r.how == db_bootstrap.RESEEDED]
        self.assertEqual([(r.role, r.name) for r in reseeded],
                         [("protocol", "reagent1_secs")])
        # Every insert is accounted for by a named record.
        self.assertEqual(len(reseeded), result.added)

    def test_the_message_names_the_row_an_operator_would_look_at(self):
        con = sqlite3.connect(self._tmpdb)
        con.execute(f"UPDATE config SET active = 0 WHERE {self.PARAM}")
        con.commit()
        con.close()

        restored = db_bootstrap.ensure(self._tmpdb).restored[0]
        rowid = self.rows(f"SELECT rowid FROM config "
                          f"WHERE {self.PARAM} AND active = 1")[0][0]

        self.assertEqual(restored.message(),
                         f"Loading protocol.reagent1_secs = 120 "
                         f"(rowid {rowid})")


class TestDeadAndOrphanParams(ConfigTestCase):
    """
    The two structural queries db_bootstrap._verify and the boot-time
    completeness check share, so there is one copy of the SQL.
    """

    PARAM = "role='protocol' AND name='reagent1_secs'"

    def test_a_seeded_database_has_neither(self):
        self.assertEqual(db_bootstrap.dead_params(), [])
        self.assertEqual(db_bootstrap.orphan_params(), [])

    def test_a_parameter_with_no_active_row_is_dead(self):
        con = sqlite3.connect(self._tmpdb)
        con.execute(f"UPDATE config SET active = 0 WHERE {self.PARAM}")
        con.commit()
        con.close()
        self.assertIn(("protocol", "reagent1_secs"), db_bootstrap.dead_params())

    def test_a_parameter_with_no_default_row_is_an_orphan(self):
        con = sqlite3.connect(self._tmpdb)
        con.execute(f"UPDATE config SET isdefault = 0 WHERE {self.PARAM}")
        con.commit()
        con.close()
        self.assertIn(("protocol", "reagent1_secs"),
                      db_bootstrap.orphan_params())

    def test_they_read_the_same_database_with_or_without_a_connection(self):
        con = sqlite3.connect(self._tmpdb)
        try:
            self.assertEqual(db_bootstrap.dead_params(con),
                             db_bootstrap.dead_params())
            self.assertEqual(db_bootstrap.orphan_params(con),
                             db_bootstrap.orphan_params())
        finally:
            con.close()


class TestSchemaConstraints(ConfigTestCase):
    """
    The invariants are the schema's job now, not the application's.

    They used to be enforced only by config_repo.save() remembering to, which
    is how the prototype ended up with operator rows that had no commissioned
    fallback and could never be restored.
    """

    def _insert(self, **over):
        row = dict(active=0, isdefault=0, editable=1, add_dt="now",
                   role="protocol", name="reagent1_secs", vtype="int",
                   value="777", source="operator")
        row.update(over)
        con = sqlite3.connect(self._tmpdb)
        try:
            con.execute(
                "INSERT INTO config (active, isdefault, editable, add_dt, "
                "role, name, vtype, value, source) "
                "VALUES (:active, :isdefault, :editable, :add_dt, :role, "
                ":name, :vtype, :value, :source)", row)
            con.commit()
        finally:
            con.close()

    def test_a_second_active_row_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(active=1)

    def test_a_second_default_row_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(isdefault=1)

    def test_a_duplicate_value_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(value="120")

    def test_role_and_name_are_case_insensitive(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(role="PROTOCOL", name="REAGENT1_SECS", value="120")

    def test_a_history_row_is_allowed(self):
        self._insert()
        self.assertOneActiveRowPerParameter()

    def test_there_is_no_profile_column(self):
        """
        Dropped with the simulation tier. Its absence is what lets reading a
        value be a lookup instead of a merge.
        """
        columns = {r[1] for r in self.rows("PRAGMA table_info(config)")}
        self.assertNotIn("profile", columns)
        self.assertIn("editable", columns)


if __name__ == "__main__":
    unittest.main(verbosity=2)
