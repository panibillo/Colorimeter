PRAGMA Foreignkeys = off;


DROP TABLE IF EXISTS device;
CREATE TABLE IF NOT EXISTS device(
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

DROP TABLE IF EXISTS users;
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

DROP TABLE IF EXISTS testkit;
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

DROP TABLE IF EXISTS testlot;
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

DROP TABLE IF EXISTS samples;
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

DROP TABLE IF EXISTS defaults;
CREATE TABLE IF NOT EXISTS defaults(
    rowid            INTEGER PRIMARY KEY
                             NOT NULL,
    deviceid         INTEGER NOT NULL,
    userid           INTEGER NOT NULL,
    testlotid        INTEGER NOT NULL,
    samp_no_template TEXT,
    dt_update        TEXT,
    remark           TEXT COLLATE NOCASE,
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

/*
INSERT INTO device (rowid, hardware_sn, software_ver, description, dt_add)
     VALUES (1, 'XCN-001', '0.2', 'dev8 lighting, prototype', '2026-07-21');
--
INSERT INTO users (handle, fullname, email, userorg, dt_add)
     VALUES ('Bill', 'William Olsen', 'panibillo@gmail.com', 'MGWWG', '2026-07-21');
--
INSERT INTO testkit (rowid, handle, partno, vendors, dt_add)
     VALUES (1, 'Ascel', '458201', 'Industrial Test Systems, Inc.', '2026-07-21');
--
INSERT INTO testlot (testkitid, lotno, expire_dt)
     VALUES (1, '0 12V601-HH', '2027-07');
--
INSERT INTO defaults (deviceid, userid, testlotid, samp_no_prefix, dt_update, remark)
     VALUES (1, 1, 1, 'ZZ', '2027-07-21', 'development and debugging, not real readings.');
--
--
DROP VIEW IF EXISTS default_gui_vals;
--CREATE VIEW IF NOT EXISTS default_gui_vals AS
  SELECT E.rowid, E.deviceid, E.userid, E.testlotid, E.samp_no_template,
         CONCAT(K.handle,' ',K.partno) as testkit, L.lotno, 
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
*/