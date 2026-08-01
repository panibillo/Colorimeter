-- ============================================================================
-- db_DDL_reset.sql  —  DESTRUCTIVE.  Drops every colorimeter object.
-- ============================================================================
--
-- This erases all data: samples, calibrated exposures, edited ROIs, everything.
-- It is a separate file from db_DDL.sql so that rebuilding the schema can never
-- destroy data by accident.  Back the database up first.
--
--     cp src/colorimeter.sqlite src/colorimeter.sqlite.bak
--     sqlite3 src/colorimeter.sqlite < src/db_DDL_reset.sql
--     sqlite3 src/colorimeter.sqlite < src/db_DDL.sql
--
-- Foreign keys stay OFF here so the drop order does not matter.
-- ============================================================================

PRAGMA foreign_keys = OFF;

DROP VIEW  IF EXISTS default_gui_vals;

DROP INDEX IF EXISTS ix_roi_name_active;

DROP TABLE IF EXISTS data;
DROP TABLE IF EXISTS colorcard;
DROP TABLE IF EXISTS roi;
DROP TABLE IF EXISTS exposure;
DROP TABLE IF EXISTS defaults;
DROP TABLE IF EXISTS samples;
DROP TABLE IF EXISTS testlot;
DROP TABLE IF EXISTS testkit;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS device;
