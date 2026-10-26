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

-- ── Renames ─────────────────────────────────────────────────────────────────
-- A parameter that changed name, carried across in place so its value, its
-- history and its isdefault row all come with it.  Without this the INSERTs
-- below would seed the new name alongside the old, and the old would sit there
-- active and unread — visible in the config editor, meaning nothing.
--
-- Idempotent like everything else here: after the first run there is nothing
-- left to match.  A rename can be deleted from this list once every instrument
-- has been through it, but there is no harm in leaving it.
--
-- capture.dark_prefetch_budget_s -> capture.prefetch_budget_s (2026-08)
--   The stage it budgets no longer takes dark frames; they are all captured
--   after the LED sweep now.  What is left is camera setup and focus.
UPDATE config
   SET name   = 'prefetch_budget_s',
       remark = 'reserved time for camera setup and focus during the reagent2 countdown'
 WHERE role = 'capture' AND name = 'dark_prefetch_budget_s'
   AND NOT EXISTS (SELECT 1 FROM config
                    WHERE role = 'capture' AND name = 'prefetch_budget_s');

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
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'led_color_order', 'str',
   'photo_red,red,red_orange,amber,green,cyan,blue,royal_blue,violet,white', 'seed',
   'LEDs both capture paths sweep, in order (comma-separated color names)'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'inspection_exposure_us', 'int', '10000', 'seed',
   'operator-saved exposure from the viewer''s Img recapture dialog'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'default_focus_diopter', 'float', '0.0', 'seed',
   'operator-saved focus diopter from the viewer''s Img recapture dialog'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'camera_warmup_s', 'float', '5.0', 'seed',
   'seconds to warm the camera before captures (thermal-stability margin)'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'prefetch_budget_s', 'float', '20.0', 'seed',
   'reserved time for camera setup and focus during the reagent2 countdown'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'layer_keep_min_dn', 'int', '96', 'seed',
   'a Bayer layer is too dark to store when its scaled p10 falls below this (signal DN)'),
  (0, 1, 1, '2026-08-01 00:00:00', 'capture', 'layer_keep_max_dn', 'int', '863', 'seed',
   'a Bayer layer is too bright to store when its scaled p90 rises above this (signal DN)'),
  (0, 1, 1, '2026-08-12 00:00:00', 'capture', 'photo_enabled', 'bool', '1', 'seed',
   'take a colour-balanced JPEG under the white LED as the last step of a run'),
  (0, 1, 1, '2026-08-12 00:00:00', 'capture', 'photo_wb_gains', 'str', '1.0,1.0', 'seed',
   'white-LED colour gains (red,blue) rendering that photo; commission per unit'),
  (0, 1, 1, '2026-08-18 00:00:00', 'capture', 'preimage_enabled', 'bool', '1', 'seed',
   'offer a pre-image of the unreacted strip before reagent 1'),
  (0, 1, 1, '2026-08-18 00:00:00', 'capture', 'preimage_led_order', 'str',
   'royal_blue,red', 'seed',
   'LEDs the pre-image sweeps; keep it short — this light falls on an unreacted pad');
-- ── calibration ─────────────────────────────────────────────────────────────
-- The window every stored exposure bracket must fit on the test pad (white,
-- yellow and brown patches) and the gray chips.  Placeholders for Bill to tune.
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     VALUES
  (0, 1, 1, '2026-10-26 00:00:00', 'calibration', 'window_low_dn', 'int', '100', 'seed',
   'a bracket is too dark when a test-pad patch''s low percentile signal falls below this (DN)'),
  (0, 1, 1, '2026-10-26 00:00:00', 'calibration', 'window_high_dn', 'int', '850', 'seed',
   'a bracket is too bright when a test-pad patch''s high percentile signal rises above this (DN)'),
  (0, 1, 1, '2026-10-26 00:00:00', 'calibration', 'window_low_pctl', 'float', '10.0', 'seed',
   'percentile of a patch''s pixels compared with the low limit'),
  (0, 1, 1, '2026-10-26 00:00:00', 'calibration', 'window_high_pctl', 'float', '90.0', 'seed',
   'percentile of a patch''s pixels compared with the high limit'),
  (0, 1, 1, '2026-10-26 00:00:00', 'calibration', 'max_search_iter', 'int', '8', 'seed',
   'most nudges allowed to bring one bracket into the window before giving up'),
  (0, 1, 1, '2026-10-26 00:00:00', 'calibration', 'min_spacing_ratio', 'float', '1.2', 'seed',
   'closest allowed ratio between neighboring brackets'),
  (0, 1, 1, '2026-10-26 00:00:00', 'calibration', 'max_spacing_ratio', 'float', '2.0', 'seed',
   'widest allowed ratio between neighboring brackets (not used by the nested rungs)'),
  (0, 1, 1, '2026-10-26 00:00:00', 'calibration', 'max_exposure_us', 'int', '200000', 'seed',
   'longest exposure calibration may store, in microseconds'),
  (0, 1, 1, '2026-10-26 00:00:00', 'calibration', 'required_cells', 'str',
   'violet.B,royal_blue.B,cyan.B,green.Gb,green.Gr,photo_red.R', 'seed',
   'LED.channel pairs the final analysis uses; only these must fit the window (unnamed LEDs use their nearest channel)'),
  (0, 1, 1, '2026-10-26 00:00:00', 'calibration', 'led_windows', 'str', '', 'seed',
   'per-LED window overriding the calibration.window_* rows: led=low_dn:high_dn:low_pctl:high_pctl;led=...');
-- ── protocol ────────────────────────────────────────────────────────────────
-- These are the real durations.  A shorter pair used to be seeded under a
-- 'simulation' profile so a desktop run finished quickly; that profile is
-- gone, and a developer who wants short waits sets them in their own database
-- (COLORIMETER_DB) or from the config editor.
--
-- Reagent step 2 is not a fixed duration.  It is temperature-compensated:
--
--     t2(theta) = t2ref25_secs * q10 ** ((25 - theta) / 10)
--
-- so a cold sample is given proportionally longer.  Both constants are
-- calibratable per test kit; q10 = 1 removes the compensation entirely and
-- makes t2 a constant t2ref25_secs.  See config_repo.reagent2_secs().
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     VALUES
  (0, 1, 1, '2026-08-01 00:00:00', 'protocol', 'reagent1_secs', 'int', '120', 'seed',
   'countdown for reagent step 1'),
  (0, 1, 1, '2026-08-18 00:00:00', 'protocol', 't2ref25_secs', 'int', '720', 'seed',
   'reagent step 2 countdown at the 25 C reference temperature'),
  (0, 1, 1, '2026-08-18 00:00:00', 'protocol', 'q10', 'float', '2.0', 'seed',
   'factor the reagent 2 rate changes by per 10 C; 1 = no compensation'),
  (0, 1, 1, '2026-08-01 00:00:00', 'protocol', 'analysis_secs', 'float', '1.5', 'seed',
   'placeholder analysis duration until the analysis engine lands'),
  (0, 1, 1, '2026-08-19 00:00:00', 'protocol', 'reagent1_empty_packet_wt', 'float', '0.530', 'seed',
   'empty packet weight in grams, used to compute net reagent 1 added'),
  (0, 1, 1, '2026-08-19 00:00:00', 'protocol', 'reagent2_empty_packet_wt', 'float', '0.540', 'seed',
   'empty packet weight in grams, used to compute net reagent 2 added'),
  (0, 1, 1, '2026-08-19 00:00:00', 'protocol', 'reagent1_100ml_wt', 'float', '2.900', 'seed',
   'reagent 1 grams per 100 mL of sample; used to compute target weight'),
  (0, 1, 1, '2026-08-19 00:00:00', 'protocol', 'reagent2_100ml_wt', 'float', '1.300', 'seed',
   'reagent 2 grams per 100 mL of sample; used to compute target weight'),
  (0, 1, 1, '2026-08-21 00:00:00', 'protocol', 'reagent_wt_tolerance', 'float', '0.050', 'seed',
   'grams the weighed reagent may differ from target before the cell is flagged'),
  (0, 1, 1, '2026-08-19 00:00:00', 'protocol', 'reagent1_stir_seconds', 'int', '25', 'seed',
   'stir duration in seconds before sub-alarm beep in reagent 1 countdown'),
  (0, 1, 1, '2026-08-19 00:00:00', 'protocol', 'reagent2_stir_seconds', 'int', '0', 'seed',
   'stir duration in seconds at 25 C reference; 0 = stir continuously, no sub-alarm');

-- ── runtime ─────────────────────────────────────────────────────────────────
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     VALUES
  (0, 1, 1, '2026-08-01 00:00:00', 'runtime', 'main_loop_poll_s', 'float', '0.02', 'seed',
   'main event-loop cadence'),
  (0, 1, 1, '2026-08-01 00:00:00', 'runtime', 'shutdown_timeout_s', 'float', '15.0', 'seed',
   'shutdown screen auto-picks Exit after this');

-- ── roi ─────────────────────────────────────────────────────────────────────
-- Which ROI roles the instrument refuses to run without.  A *role* is what a
-- region is for; its *name* is an operator-editable label, so the requirement
-- is stated in roles (see src/roi.py's "Roles" section).
--
-- editable=0: this is not a preference.  Changing it changes what the capture
-- and analysis paths are entitled to assume exists, so it moves with the code,
-- not with the operator.  It lives here rather than in roi.py so a unit
-- commissioned for a different card can be given a different requirement
-- without a code change, and so the boot check and the capture path read one
-- list instead of two.
--
--   bound          the crop written to the h5 archive; exactly one
--   sample         the reacted cuvette — the measurement; one or more
--   white_balance  reference patch the layers are balanced against; one or more
--
-- Any other role ('black', 'gray1', ...) may be active and is simply not
-- checked at boot.
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     VALUES
  (0, 1, 0, '2026-08-08 00:00:00', 'roi', 'required_roles', 'str',
   'bound,sample,white_balance', 'seed',
   'ROI roles a run cannot proceed without (comma-separated role names)');

-- ── hardware ────────────────────────────────────────────────────────────────
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     VALUES
  (0, 1, 0, '2026-08-01 00:00:00', 'hardware', 'backlight_pin', 'int', '13', 'seed',
   'BCM GPIO driving the display backlight');

-- ── device ──────────────────────────────────────────────────────────────────
-- Was the `device` table.  One instrument, so one value per name.  The serial
-- is editable=0: it identifies the instrument and ends up in every archive, so
-- it is commissioning data, not a setting.
--
-- `software_ver` is deliberately NOT here, and is the one exception to this
-- file being the only place a value is written down.  It is not a setting at
-- all — it is a fact about the code that is running, so src/version.py owns it
-- and config_repo.stamp_version() writes it into the table at every launch
-- (editable=0, source='code').  A value here would be a second version number
-- to keep in step, and would be wrong the moment the software was updated
-- without the database being reseeded.
INSERT OR IGNORE INTO config (active, isdefault, editable, add_dt, role, name,
                              vtype, value, source, remark)
     VALUES
  (0, 1, 0, '2026-07-21 00:00:00', 'device', 'hardware_sn', 'str', 'XCN-001', 'seed',
   'instrument hardware serial number'),
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
  (0, 1, 1, '2026-07-21 00:00:00', 'defaults', 'exposuregroup', 'str', '3', 'seed',
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
   'expiry of the reagent lot in force'),
  (1, 1, 1, '2026-08-09 00:20:00', 'defaults', 'remark', 'str','', 'seed',
   'Default remark is an empty string.'),
  (0, 1, 1, '2026-08-11 00:00:00', 'defaults', 'default_temperature', 'float', '21.0', 'seed',
   'sample temperature (degrees C) the entry2 Temp button fills in when no thermometer is attached'),
  (0, 1, 1, '2026-08-21 00:00:00', 'defaults', 'default_sample_wt', 'str', '100.', 'seed',
   'sample weight (grams) the entry2 Weight button fills in; the reagent targets scale from it');

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
--   white   white reference patch (role 'white_balance')
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
  ( 3, 0, 1, '2026-07-31 00:00:00', 'white',  'white_balance', 1150, 496, 136,  68, 2304, 1296, 'imx708', NULL, 'commissioned default'),
  ( 4, 1, 0, '2026-07-31 00:00:00', 'white',  'white_balance', 1150, 496, 136,  68, 2304, 1296, 'imx708', NULL, 'seed'),
  ( 5, 0, 1, '2026-07-31 00:00:00', 'black1', 'black',  1150, 380,  68,  68, 2304, 1296, 'imx708', NULL, 'commissioned default'),
  ( 6, 1, 0, '2026-07-31 00:00:00', 'black1', 'black',  1150, 380,  68,  68, 2304, 1296, 'imx708', NULL, 'seed'),
  ( 7, 0, 1, '2026-07-31 00:00:00', 'black2', 'black',  1150, 900,  68,  68, 2304, 1296, 'imx708', NULL, 'commissioned default'),
  ( 8, 1, 0, '2026-07-31 00:00:00', 'black2', 'black',  1150, 900,  68,  68, 2304, 1296, 'imx708', NULL, 'seed'),
  ( 9, 0, 1, '2026-07-31 00:00:00', 'bound',  'bound',  1090, 360, 196, 630, 2304, 1296, 'imx708', NULL, 'commissioned default'),
  (10, 1, 0, '2026-07-31 00:00:00', 'bound',  'bound',  1090, 360, 196, 630, 2304, 1296, 'imx708', NULL, 'seed');

-- ── Role renames ────────────────────────────────────────────────────────────
-- 'white' -> 'white_balance' (2026-08).  The role names what a region is FOR,
-- and this one is not "the white patch" but "the patch the Bayer layers are
-- balanced against" — which on this card is a pair of gray chips, not a white
-- one.  Renaming the role rather than adding a second one keeps a single
-- requirement to satisfy.
--
-- INSERT OR IGNORE above cannot carry this: the seed rows are pinned to
-- explicit rowids, so on an existing database they are skipped and keep their
-- old role.  This UPDATE is what actually migrates a commissioned unit, and
-- is a no-op once no row is left matching.
UPDATE roi SET role = 'white_balance' WHERE role = 'white';

-- ── Retired parameters ──────────────────────────────────────────────────────
-- protocol.reagent2_secs -> protocol.t2ref25_secs + protocol.q10 (2026-08).
-- Reagent step 2 stopped being a fixed duration when it became temperature-
-- compensated, so the old parameter has no reader left.
--
-- Deleted rather than deactivated, and deleted rather than renamed:
--
--   deactivating is not available — db_bootstrap._verify() treats a parameter
--   with no active row as fatal, so leaving the rows in place inactive would
--   refuse to launch;
--   renaming would be a lie — 420 s meant "how long step 2 runs", while
--   t2ref25_secs means "how long step 2 runs at 25 C", and carrying the old
--   number across would silently commission a reference that was never
--   measured as one;
--   leaving it active would put a parameter in the config editor that nothing
--   reads, which is the same failure the seeded-fallback purge removed.
--
-- The cost is this parameter's history.  Accepted: its values only ever
-- answered a question that is no longer asked.  A no-op once nothing matches.
DELETE FROM config WHERE role = 'protocol' AND name = 'reagent2_secs';


-- samples, exposure, colorcard and data are intentionally left empty: they
-- hold run data, calibration results, and card geometry, all produced by the
-- application rather than commissioned.
