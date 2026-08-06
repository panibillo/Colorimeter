-- ============================================================================
-- config_defaults.sql  —  Every commissioned value the application needs.
-- ============================================================================
--
-- Run straight after src/db_DDL.sql:
--
--     sqlite3 src/colorimeter.sqlite < src/db_DDL.sql
--     sqlite3 src/colorimeter.sqlite < provisioning/config_defaults.sql
--
-- Normally neither is run by hand — src/db_bootstrap.py executes both at every
-- launch, so a parameter that has gone missing repairs itself.
--
-- Re-run policy: idempotent, and non-destructive.  Every statement is
-- INSERT OR IGNORE, and the UNIQUE constraints on `config` do the rest: a
-- value already present is skipped, and a parameter the operator has since
-- changed keeps their value.  Nothing here ever overwrites a value, an
-- `active` flag or an `isdefault` flag.
--
-- HAND-MAINTAINED.  This file used to be generated from
-- config_repo.SEED_PARAMS by `python src/config_repo.py --emit-sql`.  That
-- generator and its Python copy of the values are gone: this file is now the
-- only place a default value is written down.  Add a parameter by adding a row
-- here.
--
-- `source` marks where a row came from and is never rewritten:
--     'seed'     this file — what the instrument shipped with, and what
--                "Restore Factory Defaults" goes back to.  Exactly one row per
--                parameter carries it.
--     'history'  a prior value carried in so the editor's Browse screen can
--                offer it without it being retyped.  Never a factory value.
--     'operator' created on the instrument.  Never written here.
--
-- It seeds `roi` as well as `config` — both hold commissioned settings, and
-- both have to be present before the application can start.
-- ============================================================================


-- ────────────────────────────────────────────────────────────────────────────
-- config
--
-- Rows are inserted with active=0, isdefault=1 — the Default-button value,
-- never in force on arrival.  The UPDATE at the end of this block then
-- activates each one *only* where the parameter has no active row yet.  That
-- ordering is what makes a re-run safe: on a fresh database every parameter
-- ends up as a single row that is both active and default, and on a database
-- the operator has edited their value stays in force and only genuinely
-- missing parameters get filled in.
--
-- No explicit rowids: un_config_value (role, name, value) is what makes the
-- re-run a no-op, so the rowids are free to be whatever SQLite assigns.
--
-- `editable` is 0 for the three rows the config editor must not let an
-- operator retype: the instrument serial, the version, and the GPIO driving
-- the backlight.
-- ────────────────────────────────────────────────────────────────────────────

-- ── capture ─────────────────────────────────────────────────────────────────
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     VALUES
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'directory', 'path', 'captures', 'seed',
   'capture archives; relative paths resolve against the project root'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'timestamp_format', 'str', '%Y%m%d_%H%M', 'seed',
   'strftime format naming each archive, and stored as its suite_id'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'extension', 'str', '.h5', 'seed',
   'archive filename extension'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'led_start', 'int', '1', 'seed',
   'first LED channel a capture run sweeps (inclusive)'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'led_end', 'int', '10', 'seed',
   'last LED channel a capture run sweeps (inclusive)'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'led_color_order', 'str',
   'photo_red,red,red_orange,amber,green,cyan,blue,royal_blue,violet,white', 'seed',
   'LED color-name sweep order for normal captures (comma-separated)'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'fallback_exposure_group', 'str', '3', 'seed',
   'exposure group preselected when the saved one is missing or inactive'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'inspection_exposure_us', 'int', '10000', 'seed',
   'operator-saved exposure from the viewer''s Img recapture dialog'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'default_focus_diopter', 'float', '0.0', 'seed',
   'operator-saved focus diopter from the viewer''s Img recapture dialog'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'camera_warmup_s', 'float', '5.0', 'seed',
   'seconds to warm the camera before captures (thermal-stability margin)'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'dark_prefetch_budget_s', 'float', '20.0', 'seed',
   'reserved time for the dark-frame prefetch batch during the reagent2 countdown');

-- ── protocol ────────────────────────────────────────────────────────────────
-- These are the real durations.  A shorter pair used to be seeded under a
-- 'simulation' profile so a desktop run finished quickly; that profile is
-- gone, and a developer who wants short waits sets them in their own database
-- (COLORIMETER_DB) or from the config editor.
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     VALUES
  (0, 1, 1, '2026-08-01 00:00:00', 'protocol', 'reagent1_secs', 'int', '120', 'seed',
   'countdown for reagent step 1'),
  (0, 1, 1, '2026-08-01 00:00:00', 'protocol', 'reagent2_secs', 'int', '420', 'seed',
   'countdown for reagent step 2'),
  (0, 1, 1, '2026-08-01 00:00:00', 'protocol', 'analysis_secs', 'float', '1.5', 'seed',
   'placeholder analysis duration until the analysis engine lands');

-- ── runtime ─────────────────────────────────────────────────────────────────
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     VALUES
  (0, 1, 1, '2026-08-01 00:00:00', 'runtime', 'main_loop_poll_s', 'float', '0.02', 'seed',
   'main event-loop cadence'),
  (0, 1, 1, '2026-08-01 00:00:00', 'runtime', 'shutdown_timeout_s', 'float', '15.0', 'seed',
   'shutdown screen auto-picks Exit after this');

-- ── hardware ────────────────────────────────────────────────────────────────
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     VALUES
  (0, 1, 0, '2026-08-01 00:00:00', 'hardware', 'backlight_pin', 'int', '13', 'seed',
   'BCM GPIO driving the display backlight');

-- ── device ──────────────────────────────────────────────────────────────────
-- Was the `device` table.  One instrument, so one value per name.  The serial
-- and the version are editable=0: they identify the instrument and end up in
-- every archive, so they are commissioning data, not settings.
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     VALUES
  (0, 1, 0, '2026-07-21 00:00:00', 'device', 'hardware_sn', 'str', 'XCN-001', 'seed',
   'instrument hardware serial number'),
  (0, 1, 0, '2026-07-21 00:00:00', 'device', 'software_ver', 'str', '0.2', 'seed',
   'version this instrument is commissioned at'),
  (0, 1, 1, '2026-07-21 00:00:00', 'device', 'description', 'str', 'dev8 lighting, prototype', 'seed',
   'free-text description of this instrument');

-- ── defaults ────────────────────────────────────────────────────────────────
-- Was the `defaults` row plus the `users`, `testkit` and `testlot` tables:
-- what a new test pre-fills with.  Names follow '<entity>' / '<entity>_<attr>'
-- so an alphabetical listing keeps a lot beside its expiry date.
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     VALUES
  (0, 1, 1, '2026-07-21 00:00:00', 'defaults', 'samp_no_template', 'str', 'ZZ1000', 'seed',
   'pattern the next sample number is generated from'),
  (0, 1, 1, '2026-07-21 00:00:00', 'defaults', 'exposuregroup', 'str', '1', 'seed',
   'images per LED the Normal capture path uses; see the `exposure` table'),
  (0, 1, 1, '2026-07-21 00:00:00', 'defaults', 'user', 'str', 'William Olsen', 'seed',
   'operator recorded on a new test'),
  (0, 1, 1, '2026-07-21 00:00:00', 'defaults', 'user_email', 'str', 'panibillo@gmail.com', 'seed',
   'operator email'),
  (0, 1, 1, '2026-07-21 00:00:00', 'defaults', 'user_org', 'str', 'MGWWG', 'seed',
   'operator organisation'),
  (0, 1, 1, '2026-07-21 00:00:00', 'defaults', 'testkit', 'str', 'Ascel 458201', 'seed',
   'test kit handle and part number, as recorded on a test'),
  (0, 1, 1, '2026-07-21 00:00:00', 'defaults', 'testkit_vendor', 'str', 'Industrial Test Systems, Inc.', 'seed',
   'vendor of the test kit in force'),
  (0, 1, 1, '2026-07-21 00:00:00', 'defaults', 'testlot', 'str', '0 12V601-HH', 'seed',
   'reagent lot number; the calibration curve is lot-specific'),
  (0, 1, 1, '2026-07-21 00:00:00', 'defaults', 'testlot_expire_dt', 'str', '2027-07', 'seed',
   'expiry of the reagent lot in force');

-- The second kit from the old `testkit` table, carried in so it can be picked
-- from the editor's Browse screen instead of retyped.  active=0, isdefault=0
-- and source='history': a previously used value, neither in force nor
-- commissioned, and not what Restore Factory Defaults goes back to.
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     VALUES
  (0, 0, 1, '2026-07-21 00:00:00', 'defaults', 'testkit', 'str', 'Hach 1', 'history',
   'previously used test kit'),
  (0, 0, 1, '2026-07-21 00:00:00', 'defaults', 'testkit_vendor', 'str', 'Hach', 'history',
   'vendor of a previously used test kit');

-- Activate every commissioned default that has nothing in force yet.  On a
-- fresh database this promotes all of the above to active=1, leaving one row
-- per parameter that is both active and default.  On an existing database it
-- touches only parameters that were missing.
UPDATE config
   SET active = 1
 WHERE isdefault = 1
   AND active = 0
   AND NOT EXISTS (SELECT 1
                     FROM config AS c2
                    WHERE c2.role   = config.role
                      AND c2.name   = config.name
                      AND c2.active = 1);


-- ────────────────────────────────────────────────────────────────────────────
-- roi
--
-- Geometry as commissioned on the prototype: imx708, picamera2 CONFIG_5, so
-- the 2304 x 1296 stream.  Every x/y/w/h below is even, so the Bayer phase is
-- preserved and no snapping is needed on load.
--
-- Two rows per name — the isdefault fallback (active=0) and the row in force
-- (active=1), odd rowids being the fallbacks.  `roi` has no UNIQUE constraint,
-- so unlike `config` it still relies on explicit rowids to make a re-run a
-- no-op, and it keeps the two-row seed shape that roi.py's save/restore
-- expects.
--
--   sample  the reacted sample cuvette — the measurement
--   white   white reference patch
--   black1  black reference patch, above the sample
--   black2  black reference patch, below the sample
--   bound   the crop written to the h5 archive; contains all of the above
--
-- bound was w=160 as originally commissioned, which ended at x=1250 and so
-- clipped the white patch (x 1150..1286) by 36 px — 26% of its width. Widened
-- to w=196 on 2026-07-31 so the archive holds every reference patch whole.
-- This changes the archived crop shape from 315x80 to 315x98.
--
-- Note: black1/black2 are carried here and drawn by the viewer, but the
-- capture path does not yet read them.
-- ────────────────────────────────────────────────────────────────────────────
INSERT OR IGNORE INTO roi (rowid, active, isdefault, add_dt, name, role,
                           x, y, w, h, frame_w, frame_h, model, color, remark)
     VALUES
  ( 1, 0, 1, '2026-07-31 00:00:00', 'sample', 'sample', 1150, 790,  68,  68, 2304, 1296, 'imx708', NULL, 'commissioned default'),
  ( 2, 1, 0, '2026-07-31 00:00:00', 'sample', 'sample', 1150, 790,  68,  68, 2304, 1296, 'imx708', NULL, 'seed'),
  ( 3, 0, 1, '2026-07-31 00:00:00', 'white',  'white',  1150, 496, 136,  68, 2304, 1296, 'imx708', NULL, 'commissioned default'),
  ( 4, 1, 0, '2026-07-31 00:00:00', 'white',  'white',  1150, 496, 136,  68, 2304, 1296, 'imx708', NULL, 'seed'),
  ( 5, 0, 1, '2026-07-31 00:00:00', 'black1', 'black',  1150, 380,  68,  68, 2304, 1296, 'imx708', NULL, 'commissioned default'),
  ( 6, 1, 0, '2026-07-31 00:00:00', 'black1', 'black',  1150, 380,  68,  68, 2304, 1296, 'imx708', NULL, 'seed'),
  ( 7, 0, 1, '2026-07-31 00:00:00', 'black2', 'black',  1150, 900,  68,  68, 2304, 1296, 'imx708', NULL, 'commissioned default'),
  ( 8, 1, 0, '2026-07-31 00:00:00', 'black2', 'black',  1150, 900,  68,  68, 2304, 1296, 'imx708', NULL, 'seed'),
  ( 9, 0, 1, '2026-07-31 00:00:00', 'bound',  'bound',  1090, 360, 196, 630, 2304, 1296, 'imx708', NULL, 'commissioned default'),
  (10, 1, 0, '2026-07-31 00:00:00', 'bound',  'bound',  1090, 360, 196, 630, 2304, 1296, 'imx708', NULL, 'seed');


-- samples, exposure, colorcard and data are intentionally left empty: they
-- hold run data, calibration results, and card geometry, all produced by the
-- application rather than commissioned.
