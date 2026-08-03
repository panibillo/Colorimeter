-- ============================================================================
-- db_DDL.sql  —  Complete schema and seed data for colorimeter.sqlite
-- ============================================================================
--
-- This file is the authoritative schema.  Running it against an empty file
-- produces a database the application can start on; running it against an
-- existing database is a no-op.
--
--     sqlite3 src/colorimeter.sqlite < src/db_DDL.sql
--
-- Re-run policy: idempotent.  Every object is CREATE ... IF NOT EXISTS and
-- every seed row is INSERT OR IGNORE with an explicit rowid, so re-running the
-- script never drops a table, never duplicates a seed row, and never disturbs
-- data the operator has since entered.  To start over from scratch, run
-- db_DDL_reset.sql first — that is the destructive one, and it is kept in a
-- separate file precisely so it cannot be triggered by accident.
--
-- Object inventory (11 tables + 1 view):
--
--     config      every tunable instrument parameter — see config_repo.py
--     device      one row per physical colorimeter (hardware sn + firmware ver)
--     users       operators
--     testkit     a commercial test kit product
--     testlot     a manufacturing lot of a test kit
--     samples     one row per test run — the main record
--     defaults    single row: what a new test pre-fills with
--     exposure    calibrated exposure ladders, per LED and group
--     roi         regions of interest in camera stream coordinates
--     colorcard   colour-calibration card patch geometry
--     data        generic timestamped key/value measurements
--
--     default_gui_vals   the `defaults` row resolved into display strings
--
-- Foreign keys are enabled for this connection so that the seed order below is
-- actually checked.  The application does not enable them at runtime.
-- ============================================================================

PRAGMA foreign_keys = ON;


-- ────────────────────────────────────────────────────────────────────────────
-- config — every tunable instrument parameter, in one place.  Replaced
-- config/instrument.yaml in 2026-08; src/config_repo.py is the only reader.
--
-- Versioned exactly like `exposure` and `roi`: `active` marks the row in
-- force, an edit flips the old row to active=0 and inserts a new one, so the
-- previous value is kept as history.  `isdefault=1` rows are the commissioned
-- fallback and are never edited — Restore copies one back into a new active
-- row.  Each (role, name, profile) therefore has two rows at seed time, and
-- WHERE active = 1 returns exactly one row per parameter.
--
-- `role` is the section the parameter used to sit under in the YAML, so
-- (role, name) is the same two-level name the code has always used:
-- config.reagent1_secs() reads ('protocol', 'reagent1_secs').
--
-- `value` is always TEXT.  `vtype` says how to read it, so a reader coerces
-- generically and an editor screen knows what to offer:
--     'int'  'float'  'bool'  'str'  'path'
-- A 'path' is resolved against the project root when relative.
--
-- `profile` is the simulation switch that `simulation_overrides` used to be:
-- '' applies always, and a 'simulation' row wins over its '' counterpart when
-- COLORIMETER_SIMULATION=1.  Nothing else selects a profile.
--
-- The seed block below is generated — `python src/config_repo.py --emit-sql`
-- writes it from that module's SEED_PARAMS, and its self-test checks the two
-- still agree.  Edit SEED_PARAMS, not this block.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS config (
    rowid     INTEGER PRIMARY KEY
                      NOT NULL,
    active    INTEGER NOT NULL DEFAULT 1,  -- 1 = in force, 0 = superseded
    isdefault INTEGER NOT NULL DEFAULT 0,  -- 1 = commissioned fallback
    add_dt    TEXT,                        -- local timestamp of the write
    role      TEXT NOT NULL,               -- 'capture','protocol','runtime',...
    name      TEXT NOT NULL,               -- 'reagent1_secs'
    vtype     TEXT NOT NULL,               -- int|float|bool|str|path
    value     TEXT,                        -- always TEXT; coerced by vtype
    profile   TEXT NOT NULL DEFAULT '',    -- '' = always, 'simulation' = sim only
    source    TEXT,                        -- 'seed','operator','commissioning'
    remark    TEXT
);

CREATE INDEX IF NOT EXISTS ix_config_role_name_active
    ON config (role, name, active);


-- ────────────────────────────────────────────────────────────────────────────
-- device — the instrument itself.  One row per hardware/firmware pairing.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS device (
    rowid         INTEGER PRIMARY KEY
                          NOT NULL,
    hardware_sn   TEXT NOT NULL COLLATE NOCASE,
    software_ver  TEXT NOT NULL COLLATE NOCASE,
    description   TEXT COLLATE NOCASE,
    dt_add        TEXT,
    dt_update     TEXT,
    remark        TEXT COLLATE NOCASE,
    CONSTRAINT un_device_sn_ver UNIQUE
      (hardware_sn, software_ver)
);


-- ────────────────────────────────────────────────────────────────────────────
-- users — operators.  `handle` is what the GUI shows and stores on a sample.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    rowid         INTEGER PRIMARY KEY
                          NOT NULL,
    handle        TEXT UNIQUE NOT NULL COLLATE NOCASE,
    fullname      TEXT COLLATE NOCASE,
    email         TEXT COLLATE NOCASE,
    userorg       TEXT COLLATE NOCASE,
    dt_add        TEXT,
    dt_update     TEXT,
    remark        TEXT COLLATE NOCASE
);


-- ────────────────────────────────────────────────────────────────────────────
-- testkit — a test kit product (vendor + part number), independent of lot.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS testkit (
    rowid         INTEGER PRIMARY KEY
                          NOT NULL,
    handle        TEXT UNIQUE NOT NULL COLLATE NOCASE,
    partno        TEXT COLLATE NOCASE,
    vendors       TEXT COLLATE NOCASE,
    dt_add        TEXT,
    dt_update     TEXT,
    remark        TEXT COLLATE NOCASE
);


-- ────────────────────────────────────────────────────────────────────────────
-- testlot — a manufacturing lot of a testkit.  Reagent chemistry (and so the
-- calibration curve) is lot-specific, which is why a sample records the lot
-- rather than the kit.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS testlot (
    rowid         INTEGER PRIMARY KEY
                          NOT NULL,
    testkitid     INTEGER,
    lotno         TEXT COLLATE NOCASE,
    expire_dt     TEXT,
    dt_add        TEXT,
    dt_update     TEXT,
    remark        TEXT COLLATE NOCASE,
    CONSTRAINT fk_testlot_testkitid FOREIGN KEY (
        testkitid
    )
    REFERENCES testkit (rowid)
      ON DELETE RESTRICT
      ON UPDATE CASCADE
);


-- ────────────────────────────────────────────────────────────────────────────
-- samples — one row per test run, written progressively as the run advances:
-- identity at entry, the four reagent timestamps as the countdowns run, the
-- capture window and h5 filename at capture, the result at analysis.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS samples (
    rowid         INTEGER PRIMARY KEY
                          NOT NULL,
    samp_no       TEXT UNIQUE NOT NULL COLLATE NOCASE,
    userid        INTEGER,
    testlotid     INTEGER,
    colorimeterid INTEGER,
    collect_dt    TEXT,
    samp_grams    REAL,
    samp_degC     REAL,
    reagent1_start INTEGER,     -- Timestamp
    reagent1_end   INTEGER,     -- Timestamp
    reagent2_start INTEGER,     -- Timestamp
    reagent2_end   INTEGER,     -- Timestamp
    capture_start  INTEGER,
    capture_end    INTEGER,
    manual_read    TEXT,
    txt_result     TEXT,
    num_result     REAL,
    h5file         TEXT,
    remark         TEXT COLLATE NOCASE,
    CONSTRAINT fk_samples_userid FOREIGN KEY (
        userid
        )
        REFERENCES users (rowid)
          ON DELETE RESTRICT
          ON UPDATE CASCADE,
    CONSTRAINT fk_samples_testlotid FOREIGN KEY (
        testlotid
        )
        REFERENCES testlot (rowid)
          ON DELETE RESTRICT
          ON UPDATE CASCADE
);


-- ────────────────────────────────────────────────────────────────────────────
-- defaults — a single row (rowid 1) holding what a new test pre-fills with:
-- which device, operator, and lot, the sample-number template, and the
-- exposure group (images per LED) the Normal capture path uses.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS defaults (
    rowid            INTEGER PRIMARY KEY
                             NOT NULL,
    deviceid         INTEGER NOT NULL,
    userid           INTEGER NOT NULL,
    testlotid        INTEGER NOT NULL,
    samp_no_template TEXT COLLATE NOCASE NOT NULL,
    dt_update        TEXT,
    remark           TEXT COLLATE NOCASE,
    exposuregroup    TEXT,      -- '1'..'5' — images per LED, see `exposure`
    CONSTRAINT fk_samples_deviceid FOREIGN KEY (
        deviceid
        )
        REFERENCES device (rowid)
          ON DELETE RESTRICT
          ON UPDATE CASCADE,
    CONSTRAINT fk_samples_userid FOREIGN KEY (
        userid
        )
        REFERENCES users (rowid)
          ON DELETE RESTRICT
          ON UPDATE CASCADE,
    CONSTRAINT fk_samples_testlotid FOREIGN KEY (
        testlotid
        )
        REFERENCES testlot (rowid)
          ON DELETE RESTRICT
          ON UPDATE CASCADE
);


-- ────────────────────────────────────────────────────────────────────────────
-- exposure — calibrated exposure ladders.  One row per (LED, group, exposure).
--
-- `active` is the current-vs-history switch: a recalibration flips the old
-- rows to active=0 and inserts fresh active=1 rows, so nothing is lost and the
-- operator can hand-edit which calibration is in force.  See exposure_repo.py.
--
-- Seeded empty — rows are written by the Calibration capture path.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS exposure (
    rowid           INTEGER PRIMARY KEY
                            NOT NULL,
    active          INTEGER,        -- 1 = in force, 0 = superseded (history)
    add_dt          TEXT,           -- calibration timestamp (local time)
    exposuregroup   TEXT,           -- '1'..'5' = images per LED
    LED             TEXT NOT NULL,  -- matches LEDBank colour names
    microseconds    INTEGER,        -- one exposure time
    remark          TEXT
);


-- ────────────────────────────────────────────────────────────────────────────
-- roi — regions of interest, in camera stream pixel coordinates.
--
-- Versioned exactly like `exposure`: `active` marks the row in force, an edit
-- flips the old row to active=0 and inserts a new one, so prior geometry is
-- kept.  `isdefault=1` rows are the commissioned fallback and are never edited
-- — "Restore" copies one back into a new active row.  Each `name` therefore
-- has two rows at seed time, and `WHERE active = 1` returns exactly one row
-- per name.
--
-- Coordinates are absolute, top-left origin, in the *stream* the capture ran
-- at — which depends on the sensor and the picamera2 config.  frame_w/frame_h/
-- model record which stream, so a reader can detect the mismatch instead of
-- silently misindexing.  x, y, w and h should all be even: an odd origin
-- shifts the Bayer phase and relabels the colour layers.  See roi.py.
--
-- color is NULL for "use the overlay's state colour" (not-selected / selected
-- / editable / editable-side); a palette name here overrides that.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS roi (
    rowid     INTEGER PRIMARY KEY
                      NOT NULL,
    active    INTEGER NOT NULL DEFAULT 1,  -- 1 = in force, 0 = superseded
    isdefault INTEGER NOT NULL DEFAULT 0,  -- 1 = commissioned fallback
    add_dt    TEXT,                        -- local timestamp of the write
    name      TEXT NOT NULL,               -- 'sample','white','black1',...
    role      TEXT,                        -- what analysis does with it
    x         INTEGER NOT NULL,
    y         INTEGER NOT NULL,
    w         INTEGER NOT NULL,
    h         INTEGER NOT NULL,
    frame_w   INTEGER NOT NULL,            -- stream size these coords assume
    frame_h   INTEGER NOT NULL,
    model     TEXT,                        -- 'imx708' | 'imx219'
    color     TEXT,                        -- palette name, or NULL
    remark    TEXT
);

CREATE INDEX IF NOT EXISTS ix_roi_name_active ON roi (name, active);


-- ────────────────────────────────────────────────────────────────────────────
-- colorcard — patch geometry of a colour-calibration card, one row per patch.
-- Populated by roi.register_colorcard(); seeded empty.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS colorcard (
    cardid     INTEGER,         -- Each color card has a unique ID
    cardname   TEXT,            -- Each color card has a unique NAME
    rectid     INTEGER,         -- Identifier of an individual rect patch
    colorname  TEXT,            -- Name of an individual rect patch
    x          INTEGER,
    y          INTEGER,
    w          INTEGER,
    h          INTEGER,
    CONSTRAINT um_colorcard_cardid_rectid UNIQUE (cardid, rectid),
    CONSTRAINT un_colorcard_cardid_x_y UNIQUE (cardid, x, y)
);


-- ────────────────────────────────────────────────────────────────────────────
-- data — generic timestamped key/value measurements, for values that do not
-- warrant a column of their own.  Seeded empty.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS data (
    timestamp  INTEGER,  -- seconds since epoch
    label      TEXT,     -- user known GUID for the msmt
    param      TEXT,     -- each msmt may have many params
    numval     REAL,
    txtval     TEXT      -- in case there are any text vals
);


-- ────────────────────────────────────────────────────────────────────────────
-- default_gui_vals — the single `defaults` row with its foreign keys resolved
-- into the strings the entry screen displays.  sample_repo reads this view,
-- so the application will not start without it.
--
-- Uses `||` for concatenation; SQLite has no CONCAT().
-- ────────────────────────────────────────────────────────────────────────────
CREATE VIEW IF NOT EXISTS default_gui_vals AS
  SELECT E.rowid, E.deviceid, E.userid, E.testlotid, E.samp_no_template,
         K.handle || ' ' || K.partno AS testkit, L.lotno,
         D.hardware_sn, D.software_ver, U.fullname
    FROM defaults E
    LEFT JOIN device D
         ON E.deviceid = D.rowid
    LEFT JOIN users U
         ON E.userid = U.rowid
    LEFT JOIN testlot L
         ON E.testlotid = L.rowid
    LEFT JOIN testkit K
         ON L.testkitid = K.rowid;


-- ============================================================================
-- Seed data
--
-- Explicit rowids everywhere, so INSERT OR IGNORE makes a re-run a no-op and
-- the foreign keys below stay stable.  Insert order follows the dependencies:
-- device, users, testkit -> testlot -> defaults.
--
-- samples, exposure, colorcard and data are intentionally left empty: they
-- hold run data, calibration results, and card geometry, all produced by the
-- application rather than commissioned.
-- ============================================================================

INSERT OR IGNORE INTO device (rowid, hardware_sn, software_ver, description, dt_add)
     VALUES (1, 'XCN-001', '0.2', 'dev8 lighting, prototype', '2026-07-21');

INSERT OR IGNORE INTO users (rowid, handle, fullname, email, userorg, dt_add)
     VALUES (1, 'Bill', 'William Olsen', 'panibillo@gmail.com', 'MGWWG', '2026-07-21');

INSERT OR IGNORE INTO testkit (rowid, handle, partno, vendors, dt_add)
     VALUES (1, 'Ascel', '458201', 'Industrial Test Systems, Inc.', '2026-07-21'),
            (2, 'Hach',  '1',      'Hach',                          '2026-07-21');

INSERT OR IGNORE INTO testlot (rowid, testkitid, lotno, expire_dt)
     VALUES (1, 1, '0 12V601-HH', '2027-07');

INSERT OR IGNORE INTO defaults (rowid, deviceid, userid, testlotid, samp_no_template,
                                dt_update, exposuregroup, remark)
     VALUES (1, 1, 1, 1, 'ZZ1000', '2026-07-21', '1',
             'development and debugging, not real readings.');


-- ── roi seed ────────────────────────────────────────────────────────────────
-- Geometry as commissioned on the prototype: imx708, picamera2 CONFIG_5, so
-- the 2304 x 1296 stream.  Every x/y/w/h below is even, so the Bayer phase is
-- preserved and no snapping is needed on load.
--
-- Two rows per name: the isdefault fallback (active=0) and the row in force
-- (active=1).  Odd rowids are the fallbacks, even rowids the active rows.
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


-- ── config seed ─── generated by config_repo.py --emit-sql ──
-- Two rows per parameter: the isdefault fallback (active=0) and the row
-- in force (active=1).  Odd rowids are the fallbacks, even the active ones.
-- Edit config_repo.SEED_PARAMS and re-emit; do not hand-edit this block.
INSERT OR IGNORE INTO config (rowid, active, isdefault, add_dt, role, name,
                              vtype, value, profile, source, remark)
     VALUES
       ( 1, 0, 1, '2026-08-01 00:00:00', 'capture', 'directory', 'path', 'captures', '', 'seed', 'capture archives; relative paths resolve against the project root'),
       ( 2, 1, 0, '2026-08-01 00:00:00', 'capture', 'directory', 'path', 'captures', '', 'seed', 'capture archives; relative paths resolve against the project root'),
       ( 3, 0, 1, '2026-08-01 00:00:00', 'capture', 'timestamp_format', 'str', '%Y%m%d_%H%M', '', 'seed', 'strftime format naming each archive, and stored as its suite_id'),
       ( 4, 1, 0, '2026-08-01 00:00:00', 'capture', 'timestamp_format', 'str', '%Y%m%d_%H%M', '', 'seed', 'strftime format naming each archive, and stored as its suite_id'),
       ( 5, 0, 1, '2026-08-01 00:00:00', 'capture', 'extension', 'str', '.h5', '', 'seed', 'archive filename extension'),
       ( 6, 1, 0, '2026-08-01 00:00:00', 'capture', 'extension', 'str', '.h5', '', 'seed', 'archive filename extension'),
       ( 7, 0, 1, '2026-08-01 00:00:00', 'capture', 'led_start', 'int', '1', '', 'seed', 'first LED channel a capture run sweeps (inclusive)'),
       ( 8, 1, 0, '2026-08-01 00:00:00', 'capture', 'led_start', 'int', '1', '', 'seed', 'first LED channel a capture run sweeps (inclusive)'),
       ( 9, 0, 1, '2026-08-01 00:00:00', 'capture', 'led_end', 'int', '10', '', 'seed', 'last LED channel a capture run sweeps (inclusive)'),
       (10, 1, 0, '2026-08-01 00:00:00', 'capture', 'led_end', 'int', '10', '', 'seed', 'last LED channel a capture run sweeps (inclusive)'),
       (11, 0, 1, '2026-08-01 00:00:00', 'capture', 'fallback_exposure_group', 'str', '3', '', 'seed', 'exposure group preselected when the saved one is missing or inactive'),
       (12, 1, 0, '2026-08-01 00:00:00', 'capture', 'fallback_exposure_group', 'str', '3', '', 'seed', 'exposure group preselected when the saved one is missing or inactive'),
       (13, 0, 1, '2026-08-01 00:00:00', 'capture', 'inspection_exposure_us', 'int', '10000', '', 'seed', 'operator-saved exposure from the viewer''s Img recapture dialog'),
       (14, 1, 0, '2026-08-01 00:00:00', 'capture', 'inspection_exposure_us', 'int', '10000', '', 'seed', 'operator-saved exposure from the viewer''s Img recapture dialog'),
       (15, 0, 1, '2026-08-01 00:00:00', 'capture', 'default_focus_diopter', 'float', '0.0', '', 'seed', 'operator-saved focus diopter from the viewer''s Img recapture dialog'),
       (16, 1, 0, '2026-08-01 00:00:00', 'capture', 'default_focus_diopter', 'float', '0.0', '', 'seed', 'operator-saved focus diopter from the viewer''s Img recapture dialog'),
       (17, 0, 1, '2026-08-01 00:00:00', 'capture', 'led_color_order', 'str', 'photo_red,red,red_orange,amber,green,cyan,blue,royal_blue,violet,white', '', 'seed', 'LED color-name sweep order for normal captures (comma-separated, user-editable)'),
       (18, 1, 0, '2026-08-01 00:00:00', 'capture', 'led_color_order', 'str', 'photo_red,red,red_orange,amber,green,cyan,blue,royal_blue,violet,white', '', 'seed', 'LED color-name sweep order for normal captures (comma-separated, user-editable)'),
       (19, 0, 1, '2026-08-01 00:00:00', 'capture', 'camera_warmup_s', 'float', '5.0', '', 'seed', 'seconds to warm the camera before captures (thermal-stability margin)'),
       (20, 1, 0, '2026-08-01 00:00:00', 'capture', 'camera_warmup_s', 'float', '5.0', '', 'seed', 'seconds to warm the camera before captures (thermal-stability margin)'),
       (21, 0, 1, '2026-08-01 00:00:00', 'capture', 'dark_prefetch_budget_s', 'float', '20.0', '', 'seed', 'reserved time for dark-frame prefetch batch to run during reagent2 countdown'),
       (22, 1, 0, '2026-08-01 00:00:00', 'capture', 'dark_prefetch_budget_s', 'float', '20.0', '', 'seed', 'reserved time for dark-frame prefetch batch to run during reagent2 countdown'),
       (23, 0, 1, '2026-08-01 00:00:00', 'protocol', 'reagent1_secs', 'int', '120', '', 'seed', 'countdown for reagent step 1'),
       (24, 1, 0, '2026-08-01 00:00:00', 'protocol', 'reagent1_secs', 'int', '120', '', 'seed', 'countdown for reagent step 1'),
       (25, 0, 1, '2026-08-01 00:00:00', 'protocol', 'reagent1_secs', 'int', '10', 'simulation', 'seed', 'short wait so a simulated run is quick to test'),
       (26, 1, 0, '2026-08-01 00:00:00', 'protocol', 'reagent1_secs', 'int', '10', 'simulation', 'seed', 'short wait so a simulated run is quick to test'),
       (27, 0, 1, '2026-08-01 00:00:00', 'protocol', 'reagent2_secs', 'int', '420', '', 'seed', 'countdown for reagent step 2'),
       (28, 1, 0, '2026-08-01 00:00:00', 'protocol', 'reagent2_secs', 'int', '420', '', 'seed', 'countdown for reagent step 2'),
       (29, 0, 1, '2026-08-01 00:00:00', 'protocol', 'reagent2_secs', 'int', '10', 'simulation', 'seed', 'short wait so a simulated run is quick to test'),
       (30, 1, 0, '2026-08-01 00:00:00', 'protocol', 'reagent2_secs', 'int', '10', 'simulation', 'seed', 'short wait so a simulated run is quick to test'),
       (31, 0, 1, '2026-08-01 00:00:00', 'protocol', 'analysis_secs', 'float', '1.5', '', 'seed', 'placeholder analysis duration until the analysis engine lands'),
       (32, 1, 0, '2026-08-01 00:00:00', 'protocol', 'analysis_secs', 'float', '1.5', '', 'seed', 'placeholder analysis duration until the analysis engine lands'),
       (33, 0, 1, '2026-08-01 00:00:00', 'runtime', 'main_loop_poll_s', 'float', '0.02', '', 'seed', 'main event-loop cadence'),
       (34, 1, 0, '2026-08-01 00:00:00', 'runtime', 'main_loop_poll_s', 'float', '0.02', '', 'seed', 'main event-loop cadence'),
       (35, 0, 1, '2026-08-01 00:00:00', 'runtime', 'shutdown_timeout_s', 'float', '15.0', '', 'seed', 'shutdown screen auto-picks Exit after this'),
       (36, 1, 0, '2026-08-01 00:00:00', 'runtime', 'shutdown_timeout_s', 'float', '15.0', '', 'seed', 'shutdown screen auto-picks Exit after this'),
       (37, 0, 1, '2026-08-01 00:00:00', 'defaults', 'rowid', 'int', '1', '', 'seed', 'the default_gui_vals row that seeds the entry screens'),
       (38, 1, 0, '2026-08-01 00:00:00', 'defaults', 'rowid', 'int', '1', '', 'seed', 'the default_gui_vals row that seeds the entry screens'),
       (39, 0, 1, '2026-08-01 00:00:00', 'hardware', 'backlight_pin', 'int', '13', '', 'seed', 'BCM GPIO driving the display backlight'),
       (40, 1, 0, '2026-08-01 00:00:00', 'hardware', 'backlight_pin', 'int', '13', '', 'seed', 'BCM GPIO driving the display backlight');
-- ── end config seed ──
