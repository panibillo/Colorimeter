-- ============================================================================
-- db_DDL.sql  —  Schema for colorimeter.sqlite.  Structure only, no data.
-- ============================================================================
--
-- This file is the authoritative schema.  Running it against an empty file
-- produces every table the application needs; running it against an existing
-- database is a no-op.  Seed rows live in provisioning/config_defaults.sql,
-- which is run straight after this one.
--
--     sqlite3 src/colorimeter.sqlite < src/db_DDL.sql
--     sqlite3 src/colorimeter.sqlite < provisioning/config_defaults.sql
--
-- Normally neither is run by hand: src/db_bootstrap.py executes both at every
-- launch, so a missing table or a missing parameter repairs itself.
--
-- Re-run policy: idempotent.  Every object is CREATE ... IF NOT EXISTS, so a
-- re-run never drops a table and never disturbs data.  To start over from
-- scratch, run db_DDL_reset.sql first — that is the destructive one, and it is
-- kept in a separate file precisely so it cannot be triggered by accident.
--
-- Object inventory (8 tables):
--
--     colorcard    colour-calibration card patch geometry
--     config       every instrument setting — see config_repo.py
--     data         generic timestamped key/value measurements
--     exposure     calibrated exposure ladders, per LED and group
--     hotpixels    known hot pixels, per camera context and Bayer layer
--     layermodels  illumination correction, per LED and Bayer layer
--     roi          regions of interest in camera stream coordinates
--     samples      one row per test run — the main record
--
-- `device`, `users`, `testkit`, `testlot`, `defaults` and the
-- `default_gui_vals` view were folded into `config` (see the note there);
-- db_DDL_reset.sql still drops them so an old database cleans up properly.
--
-- This file does not set PRAGMA user_version.  db_bootstrap.py stamps it after
-- a successful create, and provisioning/migrate_v2.sql stamps it after a
-- migration; stamping here would let a hand-run mark an unmigrated database as
-- current, because CREATE TABLE IF NOT EXISTS silently leaves the old shape in
-- place.
-- ============================================================================


-- ────────────────────────────────────────────────────────────────────────────
-- config — every instrument setting, in one place.  src/config_repo.py is the
-- only reader.  Replaced config/instrument.yaml in 2026-08, and absorbed the
-- device / users / testkit / testlot / defaults tables in 2026-08.
--
-- Versioned like `exposure` and `roi`: `active` marks the row in force, an
-- edit deactivates the old row and activates a new one, so every value the
-- parameter has ever held is kept as history and can be browsed and restored.
--
-- `active` and `isdefault` are independent properties, not two states of one
-- switch.  A freshly seeded parameter is legitimately both — the value in
-- force AND the commissioned fallback — and is stored as a single row.  An
-- operator edit adds a second row (active=1, isdefault=0) and clears `active`
-- on the first, which keeps isdefault=1.  Restore just swaps them back.
--
-- `role` groups parameters, and is what the config editor pages by.  (role,
-- name) is the two-level name the code uses: config.reagent1_secs() reads
-- ('protocol', 'reagent1_secs').  A folded entity is a role whose names follow
-- '<entity>' / '<entity>_<attr>', so an alphabetical listing keeps a lot next
-- to its expiry date: 'testlot', 'testlot_expire_dt'.
--
-- `value` is always TEXT.  `vtype` says how to read it, so a reader coerces
-- generically and the editor knows what to validate against:
--     'int'  'float'  'bool'  'str'  'path'
-- A 'path' is resolved against the project root when relative.
--
-- The four columns that carry meaning are deliberately independent, and none
-- of them is ever inferred from another:
--
--   active     use this row.  Exactly one per parameter.
--   isdefault  the value the editor's Default button offers.  The operator can
--              move it to commission a new site value.
--   source     who created the row — 'seed' (from config_defaults.sql),
--              'operator', or 'history' (a prior value carried in for the
--              editor's Browse screen).  A creation fact: nothing rewrites it,
--              so it is an add-user and never doubles as an edit-user.  It is
--              also what "Restore Factory Defaults" keys on, that being the
--              only way to find what shipped after the operator has moved
--              isdefault.
--   editable   0 for rows the config editor shows read-only — the instrument
--              serial, the version, the backlight pin.  Policy lives in the
--              database with the data instead of as a list of role names
--              hardcoded in Python.
--
-- There is no `profile` column.  One existed to let a 'simulation' row shorten
-- the reagent waits under COLORIMETER_SIMULATION=1; it was the only thing that
-- ever used a profile, and the only reason reading a value needed to merge
-- tiers rather than just read the active row.  Reagent durations are editable
-- on the instrument now, so it earned nothing and cost a column, a join
-- condition on every query, and a three-tier resolver.  COLORIMETER_SIMULATION
-- still selects mock hardware — that is a launch mode, not a setting.
--
-- The three constraints below are what used to be application convention:
--   un_config_value       one row per distinct value — makes the seed file's
--                         INSERT OR IGNORE idempotent, and makes "reuse the
--                         existing row if this value was used before" a plain
--                         lookup.  `value` must be NOT NULL for this to bite,
--                         because SQLite treats NULLs as distinct in a UNIQUE.
--   ux_config_one_active  exactly one row in force per parameter.
--   ux_config_one_default exactly one Default-button value per parameter.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS config (
    rowid     INTEGER PRIMARY KEY
                      NOT NULL,
    active    INTEGER NOT NULL DEFAULT 1,  -- 1 = in force, 0 = superseded
    isdefault INTEGER NOT NULL DEFAULT 0,  -- 1 = what Default offers
    editable  INTEGER NOT NULL DEFAULT 1,  -- 1 = operator may edit, 0 = locked
    add_dt    TEXT,                        -- local timestamp the row was made
    role      TEXT NOT NULL COLLATE NOCASE,-- 'capture','protocol','device',...
    name      TEXT NOT NULL COLLATE NOCASE,-- 'reagent1_secs'
    vtype     TEXT NOT NULL,               -- int|float|bool|str|path
    value     TEXT NOT NULL DEFAULT '',    -- always TEXT; coerced by vtype
    source    TEXT,                        -- 'seed','operator','history'
    remark    TEXT,
    CONSTRAINT un_config_value UNIQUE
      (role, name, value)
);

CREATE INDEX IF NOT EXISTS ix_config_role_name_active
    ON config (role, name, active);

CREATE UNIQUE INDEX IF NOT EXISTS ux_config_one_active
    ON config (role, name) WHERE active = 1;

CREATE UNIQUE INDEX IF NOT EXISTS ux_config_one_default
    ON config (role, name) WHERE isdefault = 1;


-- ────────────────────────────────────────────────────────────────────────────
-- samples — one row per test run, written progressively as the run advances:
-- identity at entry, the four reagent timestamps as the countdowns run, the
-- capture window and h5 filename at capture, the result at analysis.
--
-- Provenance is stored by value, not by rowid: `user`, `test_kit`, `test_lot`
-- and `instrument_sn` hold the strings themselves, so a run stays readable
-- after the settings that produced it have moved on, and so an archive does
-- not depend on this database to be understood.  slot.py already carries these
-- strings alongside the record, so nothing extra has to be looked up.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS samples (
    rowid         INTEGER PRIMARY KEY
                          NOT NULL,
    samp_no       TEXT UNIQUE NOT NULL COLLATE NOCASE,
    user          TEXT COLLATE NOCASE,     -- operator, by name
    test_kit      TEXT COLLATE NOCASE,     -- kit handle + part no
    test_lot      TEXT COLLATE NOCASE,     -- reagent lot number
    instrument_sn TEXT COLLATE NOCASE,     -- device hardware serial
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
    reagent1_g     REAL,          -- Net grams of reagent 1 added (initial - final)
    reagent2_g     REAL,          -- Net grams of reagent 2 added (initial - final)
    reagent1_stir_end INTEGER,    -- Epoch seconds when reagent 1 stir sub-alarm fired
    reagent2_stir_end INTEGER     -- Epoch seconds when reagent 2 stir sub-alarm fired
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
    role      TEXT,                        -- what analysis does with it;
                                           -- 'bound','sample','white_balance'
                                           -- are required, see config
                                           -- roi.required_roles
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
-- layermodels — the illumination correction, one row per (LED, Bayer layer).
--
-- What a measurement actually needs from the flat-field analysis.  The sample
-- ROI is imaged between two gray chips, all three collinear in x, so the
-- illumination at the sample is a fixed linear combination of the two chip
-- readings:
--
--     divisor = w1 * mean(roi1) + w2 * mean(roi2)
--
-- and the corrected sample mean is divided by it.  The polynomial that
-- produced w1 and w2 is kept alongside (coef_json, norm_mean, deg_y, deg_x)
-- as provenance — the instrument never evaluates it — so a moved ROI can be
-- re-anchored without repeating the whole analysis.  See devdesk/flatfield.py
-- and devdesk/flatfield_pool.py, which fit the field and write these rows, and
-- src/layermodel_repo.py, which reads them.
--
-- There is deliberately no exposure key.  w1 and w2 are properties of the
-- illumination geometry, and vary less than 0.2 % across an exposure ladder
-- whose own repeatability floor is 0.4-0.8 %.  `exposure_us` is the reference
-- exposure p10 and p90 were measured at, nothing more: a caller judging a
-- different exposure scales them by (its own µs / exposure_us), which the
-- sensor's linearity (better than 1.7 % across a ladder) supports.  Keying on
-- exposure instead would break the moment the `exposure` table is
-- recalibrated.
--
-- `active` and `usable` are independent, and neither may be read as the other:
--   active = 1  this row is the one in force; 0 = superseded history, exactly
--               as in `exposure`, `roi` and `config`.
--   usable = 1  this LED/layer carries enough signal to be worth capturing and
--               analysing; 0 = it never does at any exposure in the ladder, and
--               w1/w2 are NULL.  The p10/p90 of an unusable row are kept as the
--               evidence for that verdict.
-- The capture path's keep-test is `WHERE active = 1 AND usable = 1`, plus the
-- scaled percentile check against config capture.layer_keep_min_dn /
-- layer_keep_max_dn.
--
-- p10, p90 and black_dn are in DN of the *dark-subtracted* signal, pooled over
-- roisamp, roi1 and roi2 together.  (The `omitted` layer attributes an archive
-- carries are raw DN — a different quantity, named differently on purpose.)
--
-- Seeded empty — rows are written by the offline flat-field analysis.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS layermodels (
    rowid       INTEGER PRIMARY KEY
                        NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,  -- 1 = in force, 0 = superseded
    usable      INTEGER NOT NULL DEFAULT 1,  -- 1 = worth capturing/analysing
    add_dt      TEXT,                        -- local timestamp of the write
    led_color   TEXT NOT NULL,               -- matches LEDBank colour names
    bayer_layer TEXT NOT NULL,               -- 'R' | 'Gr' | 'Gb' | 'B'
    exposure_us INTEGER NOT NULL,            -- reference exposure for p10/p90
    roisamp     INTEGER,                     -- roi.rowid of the sample patch
    roi1        INTEGER,                     -- roi.rowid of the first chip
    w1          REAL,                        -- weight on mean(roi1)
    roi2        INTEGER,                     -- roi.rowid of the second chip
    w2          REAL,                        -- weight on mean(roi2)
    p10         REAL,                        -- signal DN over the three
    p90         REAL,                        --   patches, at exposure_us
    black_dn    REAL,                        -- dark level they were taken from
    roibound    INTEGER,                     -- roi.rowid of the fitted bound
    deg_y       INTEGER,                     -- polynomial basis, y then x
    deg_x       INTEGER,
    coef_json   TEXT,                        -- coefficients, terms and domain
    norm_mean   REAL,                        -- makes the surface unit-mean
    anchor_form TEXT,                        -- '1anchor' | '2anchor'
    rho         REAL,                        -- curvature correction (evidence)
    loo_pct     REAL,                        -- leave-one-placement-out error %
    analysisref TEXT,                        -- report set that produced it
    remark      TEXT
);

CREATE INDEX IF NOT EXISTS ix_layermodels_led_layer_active
    ON layermodels (led_color, bayer_layer, active);


-- ────────────────────────────────────────────────────────────────────────────
-- hotpixels — sensor defects found by src/darkframe.py from a batch of dark
-- frames, one row per flagged pixel.  Read by src/hotpixel_repo.py; applied by
-- src/darkframe.py's correct_dark() as part of dark-correcting a raw frame.
-- Full derivation, the outlier test's math, and the choice to correct by
-- neighbor-median replacement rather than by a stored per-pixel offset are
-- written up in tex/dark_signal_characterization.tex (see especially §9,
-- "Combining it all: the best per-pixel dark estimator", and the module
-- docstring of src/darkframe.py, which cites it section by section).
--
-- Coordinates (x, y) are in **whole-frame Bayer-layer space** — the same
-- space roi.to_layer_rect() produces from a stream-space rectangle — not
-- stream-space mosaic coordinates and not a bound-crop's own layer
-- coordinates (roi.to_layer_rect_within()).  That is what survives a `bound`
-- ROI edit: frame_w/frame_h (the sensor's configured stream size) do not
-- change when `bound` is moved, so a row keyed on them stays valid.
-- Fair alternative not taken: storing stream-space (pre-Bayer-split)
-- coordinates.  That would also survive a `bound` edit, but a hot pixel is a
-- property of one photosite behind one Bayer channel, so per-channel layer
-- coordinates are the natural, smaller-integer unit, and they read directly
-- against the layer arrays every caller already has in hand.
--
-- A hot pixel is specific to one physical photosite behind one Bayer colour
-- filter, so the four layers of one sensor have four independent hot-pixel
-- populations — a coordinate found hot in R says nothing about Gr, Gb or B at
-- that same site.  bayer_layer is therefore part of the row, not a separate
-- table per layer, and every read/write here is scoped to one layer at a
-- time (see hotpixel_repo.read_active()'s bayer_layer argument).
--
-- Versioned like `exposure`, `roi`, `config` and `layermodels`: active = 1 is
-- the row in force, 0 is superseded history, never deleted by a normal write.
-- A write supersedes per (model, hardware_sn, frame_w, frame_h, bayer_layer)
-- — the same granularity layermodels uses for (led_color, bayer_layer) — so
-- recalibrating one layer (as of this writing, only channel B has been run
-- at all; see tex/dark_signal_characterization.tex) leaves the other three
-- layers' history alone.  See hotpixel_repo.write_hotpixels().
--
-- exposure_us and n_frames are provenance, not a lookup key — the same
-- reasoning layermodels applies to its own w1/w2 (see that section's
-- comment): the hot-pixel population is a property of the sensor, not of one
-- particular calibration exposure, so identification at one reference
-- exposure is taken to hold generally rather than demanding a row per
-- exposure actually used in service.
--
-- score is the deviation found at detection time (in units of the
-- identification test's own predictive sigma — see darkframe.py); it is kept
-- as evidence for the verdict but the corrector does not read it, since
-- correction is by neighbor-median replacement, not by subtracting a stored
-- offset (a fair alternative considered and rejected — see darkframe.py's
-- module docstring for why).
--
-- Seeded empty — a freshly commissioned instrument has no known hot pixels
-- until the desktop analysis is run and hotpixel_repo.write_hotpixels() is
-- called.  An empty/missing context is a normal state, not an error: it means
-- "correct nothing but the frame mean," the same fallback layermodels uses
-- for an LED/layer combination that has never been fitted.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hotpixels (
    rowid         INTEGER PRIMARY KEY
                          NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,  -- 1 = in force, 0 = superseded
    add_dt        TEXT,                        -- local timestamp of the write
    model         TEXT    NOT NULL,            -- camera type, e.g. 'imx708'
    hardware_sn   TEXT,                        -- camera identifier; this
                                                --   project's camera and
                                                --   instrument are one unit,
                                                --   so the instrument serial
                                                --   (config role='device')
                                                --   stands in for a camera id
    frame_w       INTEGER NOT NULL,            -- stream size the coordinates
    frame_h       INTEGER NOT NULL,            --   below assume
    bayer_pattern TEXT,                        -- 'BGGR' | 'RGGB', informational
    bayer_layer   TEXT    NOT NULL,            -- 'R' | 'Gr' | 'Gb' | 'B'
    x             INTEGER NOT NULL,            -- whole-frame Bayer-layer
    y             INTEGER NOT NULL,            --   coordinate — see note above
    score         REAL,                        -- deviation at detection time,
                                                --   in predictive-sigma units
    exposure_us   INTEGER,                     -- reference exposure used to
                                                --   identify it (provenance)
    n_frames      INTEGER,                     -- dark frames pooled
    analysisref   TEXT,                        -- tag naming the analysis run
    remark        TEXT
);

CREATE INDEX IF NOT EXISTS ix_hotpixels_context_active
    ON hotpixels (model, hardware_sn, frame_w, frame_h, bayer_layer, active);


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
