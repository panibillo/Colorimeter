-- ============================================================================
-- migrate_v2.sql  —  ONE-TIME.  Schema v1 -> v2: fold every settings table
--                    into `config`, and record sample provenance by value.
-- ============================================================================
--
-- Back the database up first, then run with -bail so that the first error
-- aborts the script and the open transaction is rolled back:
--
--     cp src/colorimeter.sqlite src/colorimeter.sqlite.v1.bak
--     sqlite3 -bail src/colorimeter.sqlite < provisioning/migrate_v2.sql
--     sqlite3       src/colorimeter.sqlite < provisioning/config_defaults.sql
--
-- The second command is not optional: it fills in any parameter this database
-- never had a row for.  Two are known to be missing on the prototype
-- (capture.inspection_exposure_us and capture.default_focus_diopter have an
-- operator row but no commissioned fallback), and config_defaults.sql is what
-- gives them one.  From then on db_bootstrap.py runs it at every launch.
--
-- Re-run policy: NOT idempotent, and it does not need to be.  It is guarded:
-- the first statement renames `device`, which exists only before the
-- migration, so a second run aborts before touching anything.
--
-- What changes:
--   * `config` gains `editable`, a UNIQUE on (role, name, value), and partial
--     unique indexes enforcing one active and one default row per parameter.
--     The seed pairs collapse — a row may now be both active and default,
--     which is what a freshly commissioned parameter actually is.
--   * `config.profile` is dropped along with the two 'simulation' rows that
--     were its only users.  Reagent durations are editable on the instrument,
--     so a profile bought nothing and cost a tier-merging resolver.
--     COLORIMETER_SIMULATION still selects mock hardware.
--   * `device`, `users`, `testkit`, `testlot` and `defaults` become config
--     roles.  Values that were reachable but not selected (the second test
--     kit) are carried in as inactive history, so they can be picked from the
--     editor's Browse screen rather than retyped.
--   * `samples` stores provenance as text — `user`, `test_kit`, `test_lot`,
--     `instrument_sn` — instead of rowids into tables that no longer exist.
--     A run stays readable after the settings that produced it have moved on.
--   * `default_gui_vals` is dropped; nothing joins any more.
--
-- The CREATE statements below are duplicated from src/db_DDL.sql on purpose.
-- A migration is a point-in-time snapshot: it has to build the shape that was
-- current when it was written, and must not follow later schema changes.
-- ============================================================================

PRAGMA foreign_keys = OFF;

BEGIN;

-- ── Move aside ──────────────────────────────────────────────────────────────
-- The view goes first: SQLite refuses to rename a table a view still refers to.
DROP VIEW IF EXISTS default_gui_vals;

-- Guard.  `device` exists only in v1, so a second run stops here with
-- "no such table: device" and -bail rolls everything back.
ALTER TABLE device RENAME TO device_v1;

ALTER TABLE config  RENAME TO config_v1;
ALTER TABLE samples RENAME TO samples_v1;

-- The old index followed `config` through the rename; free the name.
DROP INDEX IF EXISTS ix_config_role_name_active;


-- ── The v2 shape (snapshot of src/db_DDL.sql) ───────────────────────────────
CREATE TABLE config (
    rowid     INTEGER PRIMARY KEY
                      NOT NULL,
    active    INTEGER NOT NULL DEFAULT 1,
    isdefault INTEGER NOT NULL DEFAULT 0,
    editable  INTEGER NOT NULL DEFAULT 1,
    add_dt    TEXT,
    role      TEXT NOT NULL COLLATE NOCASE,
    name      TEXT NOT NULL COLLATE NOCASE,
    vtype     TEXT NOT NULL,
    value     TEXT NOT NULL DEFAULT '',
    source    TEXT,
    remark    TEXT,
    CONSTRAINT un_config_value UNIQUE
      (role, name, value)
);

CREATE INDEX ix_config_role_name_active
    ON config (role, name, active);

CREATE UNIQUE INDEX ux_config_one_active
    ON config (role, name) WHERE active = 1;

CREATE UNIQUE INDEX ux_config_one_default
    ON config (role, name) WHERE isdefault = 1;

CREATE TABLE samples (
    rowid         INTEGER PRIMARY KEY
                          NOT NULL,
    samp_no       TEXT UNIQUE NOT NULL COLLATE NOCASE,
    user          TEXT COLLATE NOCASE,
    test_kit      TEXT COLLATE NOCASE,
    test_lot      TEXT COLLATE NOCASE,
    instrument_sn TEXT COLLATE NOCASE,
    collect_dt    TEXT,
    samp_grams    REAL,
    samp_degC     REAL,
    reagent1_start INTEGER,
    reagent1_end   INTEGER,
    reagent2_start INTEGER,
    reagent2_end   INTEGER,
    capture_start  INTEGER,
    capture_end    INTEGER,
    manual_read    TEXT,
    txt_result     TEXT,
    num_result     REAL,
    h5file         TEXT,
    remark         TEXT COLLATE NOCASE
);


-- ── 1. Carry `config` over, collapsing the seed pairs ───────────────────────
-- Grouping on the new unique key and OR-ing the flags is what does the
-- collapse: a seed pair (same value, one row active, the other default)
-- becomes a single row that is both.  A parameter the operator has changed
-- has two distinct values, so it stays two rows — their edit active, the
-- commissioned value still flagged default.
--
-- `defaults.rowid` is dropped: it pointed at the `defaults` table row, and
-- there is no longer a row to point at.  The 'simulation' rows go with the
-- profile column; the reagent durations they shortened are editable on the
-- instrument now.
INSERT INTO config (active, isdefault, editable, add_dt, role, name,
                    vtype, value, source, remark)
     SELECT MAX(active), MAX(isdefault), 1, MIN(add_dt), role, name,
            MIN(vtype), COALESCE(value, ''), MIN(source), MIN(remark)
       FROM config_v1
      WHERE NOT (role = 'defaults' AND name = 'rowid')
        -- fallback_exposure_group duplicated defaults.exposuregroup; the
        -- surviving parameter's isdefault row is now the fallback.
        AND NOT (role = 'capture' AND name = 'fallback_exposure_group')
        -- led_start/led_end duplicated led_color_order; both capture paths
        -- sweep the color list now.
        AND NOT (role = 'capture' AND name IN ('led_start', 'led_end'))
        AND COALESCE(profile, '') = ''
      GROUP BY role, name, COALESCE(value, '');

-- Lock the rows that identify the instrument rather than tune it.
UPDATE config SET editable = 0
 WHERE (role = 'hardware' AND name = 'backlight_pin');


-- ── 2. Fold `device` ────────────────────────────────────────────────────────
-- source='seed': these came from commissioned tables, so they are what this
-- instrument shipped with and what Restore Factory Defaults must go back to.
-- config_defaults.sql carries the same values, and un_config_value makes its
-- insert a no-op once these are in.
--
-- `software_ver` is the exception, and carries source='code'.  It is not a
-- setting and has no factory value to restore to: src/version.py owns it, and
-- config_repo.stamp_version() supersedes whatever is here at the next launch.
-- The row is kept, and keeps isdefault, because the value the old `device`
-- table held is a real fact — the version this instrument was commissioned at.
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     SELECT 1, 1, 0, COALESCE(dt_add, '2026-07-21') || ' 00:00:00',
            'device', 'hardware_sn', 'str', hardware_sn, 'seed',
            'migrated from the device table'
       FROM device_v1 WHERE rowid = 1
      UNION ALL
     SELECT 1, 1, 0, COALESCE(dt_add, '2026-07-21') || ' 00:00:00',
            'device', 'software_ver', 'str', software_ver, 'code',
            'version this instrument was commissioned at'
       FROM device_v1 WHERE rowid = 1
      UNION ALL
     SELECT 1, 1, 1, COALESCE(dt_add, '2026-07-21') || ' 00:00:00',
            'device', 'description', 'str', COALESCE(description, ''),
            'seed', 'migrated from the device table'
       FROM device_v1 WHERE rowid = 1;


-- ── 3. Fold `defaults` + `users` + `testkit` + `testlot` ────────────────────
-- One role, 'defaults': what a new test pre-fills with.  Names follow
-- '<entity>' / '<entity>_<attr>' so an alphabetical listing keeps a lot next
-- to its expiry date.  `defaults` is a single row (rowid 1).
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     SELECT 1, 1, 1, '2026-07-21 00:00:00', 'defaults', n, 'str', v,
            'seed', 'migrated from the ' || src || ' table'
       FROM (
         SELECT 'samp_no_template'  AS n, d.samp_no_template               AS v, 'defaults' AS src
           FROM defaults d WHERE d.rowid = 1
          UNION ALL
         -- A NULL here meant "operator has never chosen"; the v1 code then
         -- used capture.fallback_exposure_group, whose seeded value was 3.
         SELECT 'exposuregroup',     COALESCE(d.exposuregroup, '3'),          'defaults'
           FROM defaults d WHERE d.rowid = 1
          UNION ALL
         SELECT 'user',              COALESCE(u.fullname, u.handle),          'users'
           FROM defaults d JOIN users u ON u.rowid = d.userid WHERE d.rowid = 1
          UNION ALL
         SELECT 'user_email',        COALESCE(u.email, ''),                   'users'
           FROM defaults d JOIN users u ON u.rowid = d.userid WHERE d.rowid = 1
          UNION ALL
         SELECT 'user_org',          COALESCE(u.userorg, ''),                 'users'
           FROM defaults d JOIN users u ON u.rowid = d.userid WHERE d.rowid = 1
          UNION ALL
         SELECT 'testlot',           COALESCE(l.lotno, ''),                   'testlot'
           FROM defaults d JOIN testlot l ON l.rowid = d.testlotid WHERE d.rowid = 1
          UNION ALL
         SELECT 'testlot_expire_dt', COALESCE(l.expire_dt, ''),               'testlot'
           FROM defaults d JOIN testlot l ON l.rowid = d.testlotid WHERE d.rowid = 1
          UNION ALL
         SELECT 'testkit',           k.handle || ' ' || COALESCE(k.partno,''),'testkit'
           FROM defaults d JOIN testlot l ON l.rowid = d.testlotid
                           JOIN testkit k ON k.rowid = l.testkitid WHERE d.rowid = 1
          UNION ALL
         SELECT 'testkit_vendor',    COALESCE(k.vendors, ''),                 'testkit'
           FROM defaults d JOIN testlot l ON l.rowid = d.testlotid
                           JOIN testkit k ON k.rowid = l.testkitid WHERE d.rowid = 1
       );

-- Every other kit on file becomes inactive history, so it can be picked from
-- the editor's Browse screen instead of retyped.  active=0, isdefault=0 and
-- source='history': a known value, neither in force nor commissioned, and not
-- something Restore Factory Defaults should ever go back to.  The kit that IS
-- in force was inserted above with source='seed', and un_config_value keeps
-- this from overwriting it.
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     SELECT 0, 0, 1, '2026-07-21 00:00:00', 'defaults', 'testkit', 'str',
            k.handle || ' ' || COALESCE(k.partno, ''), 'history',
            'previously used test kit'
       FROM testkit k
      UNION ALL
     SELECT 0, 0, 1, '2026-07-21 00:00:00', 'defaults', 'testkit_vendor', 'str',
            COALESCE(k.vendors, ''), 'history',
            'vendor of a previously used test kit'
       FROM testkit k;


-- ── 4. Carry `samples` over, resolving the rowids to values ─────────────────
INSERT INTO samples (rowid, samp_no, user, test_kit, test_lot, instrument_sn,
                     collect_dt, samp_grams, samp_degC,
                     reagent1_start, reagent1_end, reagent2_start, reagent2_end,
                     capture_start, capture_end, manual_read,
                     txt_result, num_result, h5file, remark)
     SELECT s.rowid, s.samp_no,
            COALESCE(u.fullname, u.handle),
            k.handle || ' ' || COALESCE(k.partno, ''),
            l.lotno,
            d.hardware_sn,
            s.collect_dt, s.samp_grams, s.samp_degC,
            s.reagent1_start, s.reagent1_end, s.reagent2_start, s.reagent2_end,
            s.capture_start, s.capture_end, s.manual_read,
            s.txt_result, s.num_result, s.h5file, s.remark
       FROM samples_v1 s
       LEFT JOIN users     u ON u.rowid = s.userid
       LEFT JOIN testlot   l ON l.rowid = s.testlotid
       LEFT JOIN testkit   k ON k.rowid = l.testkitid
       LEFT JOIN device_v1 d ON d.rowid = s.colorimeterid;


-- ── 5. Drop what has been folded ────────────────────────────────────────────
DROP TABLE config_v1;
DROP TABLE samples_v1;
DROP TABLE defaults;
DROP TABLE testlot;
DROP TABLE testkit;
DROP TABLE users;
DROP TABLE device_v1;

COMMIT;

PRAGMA user_version = 2;

-- Reclaim the space the dropped tables held.
VACUUM;
