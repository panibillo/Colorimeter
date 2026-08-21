# colorimeter_receiver.py  — run this on the desktop
# pip install flask h5py
"""
Runs on desktop
see colorimeter_upload_analysis.md
"""

from flask import Flask, request, jsonify
import h5py, sqlite3, pathlib, datetime, shutil, hashlib, json
import numpy as np

version = '0.1' 

ARCHIVE = pathlib.Path.home() / "ColorimeterData"
DB_PATH = ARCHIVE / "colorimeter.db"
app = Flask(__name__)

def _plain(v):
    """HDF5 attribute -> something json can serialise."""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, np.ndarray):
        return v.tolist()
    return v


def init_db():
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS hdf5_files (
            id INTEGER PRIMARY KEY, filename TEXT, sha256 TEXT,
            received_at TEXT, sample_count INTEGER);
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY, hdf5_id INTEGER, sample_id TEXT,
            timestamp TEXT, metadata TEXT,
            FOREIGN KEY(hdf5_id) REFERENCES hdf5_files(id));
    """); con.commit(); con.close()

@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("hdf5")
    if not f: return jsonify(error="no file"), 400
    
    month = datetime.date.today().strftime("%Y-%m")
    dest_dir = ARCHIVE / month
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f.filename
    f.save(dest)

    sha = hashlib.file_digest(open(dest,"rb"), "sha256").hexdigest()
    
    con = sqlite3.connect(DB_PATH)
    with h5py.File(dest, "r") as h5:
        # One archive holds one sample.  It used to be read as though the root
        # keys were a list of samples; they are the layout -- "leds", "dark",
        # "meta", "photo", and (from schema 4) "pre", the pre-image of the
        # unreacted strip -- so that counted the shape of the file, not what
        # was in it.  The sample's identity is a root attribute.
        attrs = {k: _plain(v) for k, v in h5.attrs.items()}
        attrs["has_preimage"] = "pre" in h5
        samples = [attrs.get("samp_no") or dest.stem]
        cur = con.execute(
            "INSERT INTO hdf5_files(filename,sha256,received_at,sample_count) VALUES(?,?,?,?)",
            (str(dest), sha, datetime.datetime.now().isoformat(), len(samples)))
        fid = cur.lastrowid
        con.execute(
            "INSERT INTO measurements(hdf5_id,sample_id,timestamp,metadata) VALUES(?,?,?,?)",
            (fid, samples[0], attrs.get("suite_id", ""), json.dumps(attrs)))
    con.commit(); con.close()
    return jsonify(ok=True, samples=len(samples))

@app.route("/status")
def status(): return jsonify(ok=True)

if __name__ == "__main__":
    init_db()
    # 0.0.0.0 makes it reachable from RPi on LAN
    app.run(host="0.0.0.0", port=5001)

#############################################################################
# upload_worker.py (runs on RPi)
import requests, pathlib

def upload_hdf5(h5_path: pathlib.Path, desktop_ip: str, port: int = 5001):
    url = f"http://{desktop_ip}:{port}/upload"
    with open(h5_path, "rb") as f:
        r = requests.post(
            url,
            files={"hdf5": (h5_path.name, f, "application/x-hdf5")},
            timeout=120   # generous timeout for large files
        )
    r.raise_for_status()
    return r.json()
