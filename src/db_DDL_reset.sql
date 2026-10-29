-- ============================================================================
-- db_DDL_reset.sql  —  DESTRUCTIVE.  Drops every colorimeter object.
-- ============================================================================
--
-- This erases all data: settings, samples, calibrated exposures, edited ROIs,
-- everything.  It is a separate file from db_DDL.sql so that rebuilding the
-- schema can never destroy data by accident.  Back the database up first.
--
--     cp src/colorimeter.sqlite src/colorimeter.sqlite.bak
--     sqlite3 src/colorimeter.sqlite < src/db_DDL_reset.sql
--     sqlite3 src/colorimeter.sqlite < src/db_DDL.sql
--     sqlite3 src/colorimeter.sqlite < provisioning/config_defaults.sql
--
-- The last two are what db_bootstrap.py runs at every launch, so in practice
-- just running this file and starting the application rebuilds everything.
--
-- Foreign keys stay OFF here so the drop order does not matter.
--
-- The v1 objects are still listed: `device`, `users`, `testkit`, `testlot`,
-- `defaults` and the `default_gui_vals` view were folded into `config` in
-- 2026-08, and a database predating that migration still has them.
-- ============================================================================

-- These are destructive. They should be hard to run accidentally
/*
PRAGMA foreign_keys = OFF;

DROP VIEW  IF EXISTS default_gui_vals;

DROP INDEX IF EXISTS ix_config_role_name_active;
DROP INDEX IF EXISTS ux_config_one_active;
DROP INDEX IF EXISTS ux_config_one_default;
DROP INDEX IF EXISTS ix_roi_name_active;
DROP INDEX IF EXISTS ix_layermodels_led_layer_active;

DROP TABLE IF EXISTS data;
DROP TABLE IF EXISTS treatments;
DROP TABLE IF EXISTS test_equip;
DROP TABLE IF EXISTS colorcard;
DROP TABLE IF EXISTS layermodels;
DROP TABLE IF EXISTS roi;
DROP TABLE IF EXISTS exposure;
DROP TABLE IF EXISTS samples;
DROP TABLE IF EXISTS config;

-- v1 only — folded into `config`.
DROP TABLE IF EXISTS defaults;
DROP TABLE IF EXISTS testlot;
DROP TABLE IF EXISTS testkit;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS device;

-- Left over if a migration aborted part way.
DROP TABLE IF EXISTS config_v1;
DROP TABLE IF EXISTS samples_v1;
DROP TABLE IF EXISTS device_v1;

PRAGMA user_version = 0;
*/
