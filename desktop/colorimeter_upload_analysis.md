# Colorimeter LAN Upload: Technology Analysis

## Problem Statement

A Colorimeter application running on a Raspberry Pi Zero 2W needs to upload measurement data to a desktop on the same LAN. The desktop may run any OS (Windows, macOS, Linux). The target environment is single-user. The upload must handle:

- **Large image files** (bundled into HDF5)
- **Small metadata records** (a few KB each)
- **Local archiving** of HDF5 files on both devices
- **SQLite metadata databases** on both devices, with references to HDF5 files and the images they contain

The RPi already serves a web UI to the desktop. Configuration simplicity and cross-platform compatibility are primary requirements.

---

## Constraints Summary

| Constraint | Detail |
|---|---|
| Network | LAN only — no internet |
| Desktop OS | Unknown — must work on Windows, macOS, Linux |
| RPi already runs | A web server (Flask/FastAPI assumed) |
| Bundle format | HDF5 (images + metadata together) |
| Metadata latency | Should be reasonably fast |
| User technical level | Non-expert — minimal configuration |
| Environment | Single-user |

---

## Technology Options Evaluated

### 1. HTTP POST (REST) ✅ Recommended

The RPi's existing web framework is extended with a small companion HTTP server on the desktop that accepts uploads.

**How it works:**
- The RPi bundles images and metadata into an HDF5 file
- POSTs it as `multipart/form-data` to `http://<desktop-ip>:51051/upload`  (51051 is a custom port number. The standard port would be 5001)
- The desktop server unpacks the HDF5, archives it, and upserts metadata into SQLite
- Returns HTTP 200 on success; the RPi then updates its own SQLite

**Pros:**
- Zero OS-level configuration on any platform
- Pure Python on both sides (`flask`, `h5py`, `requests`)
- Reliable delivery with built-in error signaling (HTTP status codes)
- Handles large binary files via streaming multipart upload
- Reuses the same stack already running on the RPi
- Desktop receiver is a single ~60-line Python file
- Easily extended (status endpoint, retry logic, progress reporting)

**Cons:**
- Requires the desktop receiver script to be running (startup automation is optional but simple)
- Slightly more overhead than raw TCP for pure metadata — negligible in practice

**Configuration steps for user:**
1. `pip install flask h5py` on desktop
2. Run `python colorimeter_receiver.py`
3. Enter desktop LAN IP in the Colorimeter web UI once

---

### 2. SCP / SSH ❌ Not Recommended for General Users

**How it works:** RPi uses `scp` or `paramiko` (Python SSH library) to copy files directly to the desktop filesystem.

**Pros:**
- Excellent performance and reliability
- Encryption built in
- Familiar to Linux/macOS power users

**Cons:**
- Requires SSH server enabled on the desktop — non-trivial on Windows (OpenSSH server, firewall rules, service management)
- Requires SSH key exchange or stored credentials
- No built-in metadata unpacking — a separate process must watch for new files
- macOS disables Remote Login by default
- Significantly more configuration burden for non-technical users

**Verdict:** Excellent for the developer's own system (as noted), but poor choice for general distribution.

---

### 3. SMB Shared Folder (Windows File Sharing) ❌ Not Recommended

**How it works:** Desktop shares a folder; RPi mounts it and writes files directly.

**Pros:**
- No receiver script needed — filesystem write is the upload
- Familiar to Windows users

**Cons:**
- Requires OS-level share configuration on the desktop
- RPi must mount the share — credentials, permissions, mount persistence across reboots
- Cross-platform friction: SMB on macOS is functional but fussy; Linux requires `cifs-utils`
- No acknowledgment mechanism — RPi cannot confirm the desktop received and processed the file
- No natural hook for triggering SQLite updates on the desktop side

**Verdict:** Too much OS-level configuration; no delivery confirmation.

---

### 4. Raw TCP Socket ⚠️ Possible but Unnecessary

**How it works:** Custom protocol over a persistent TCP connection; RPi sends length-prefixed frames.

**Pros:**
- Minimal overhead
- Full control over protocol

**Cons:**
- Requires writing and maintaining a custom protocol on both ends
- No off-the-shelf tooling for debugging
- HTTP already runs over TCP — you get the same transport with far less code

**Verdict:** Reinvents HTTP without benefit. Use HTTP instead.

---

### 5. UDP ❌ Not Suitable

**How it works:** RPi sends datagrams to desktop.

**Pros:**
- Lowest latency and overhead for small metadata bursts

**Cons:**
- No delivery guarantee — packets can be silently dropped even on a LAN
- No ordering guarantee
- Maximum payload ~65KB — cannot carry HDF5 files without fragmentation and reassembly logic
- Requires building reliability on top (essentially reinventing TCP)

**Verdict:** Appropriate only for real-time telemetry where occasional loss is acceptable. Not suitable for archival uploads.

---

### 6. MQTT ⚠️ Good for Metadata Only

**How it works:** RPi publishes metadata to topics on an MQTT broker; desktop subscribes.

**Pros:**
- Very low overhead for small metadata messages
- Pub/sub decouples sender and receiver
- Well-supported in Python (`paho-mqtt`)

**Cons:**
- Requires a broker running somewhere (Mosquitto on RPi or desktop — extra service to configure)
- Poor fit for large binary payloads (HDF5 files) — MQTT messages have size limits and are not designed for file transfer
- Would require a separate mechanism for image/HDF5 uploads anyway

**Verdict:** Could serve as a fast metadata-only channel alongside HTTP for HDF5 files, but this splits the architecture without clear benefit at single-user scale.

---

### 7. WebSockets ⚠️ Overkill

**How it works:** Persistent bidirectional connection between RPi and desktop.

**Pros:**
- Low latency for streaming updates
- Good for real-time UI feedback

**Cons:**
- Adds complexity (connection management, reconnect logic)
- HTTP POST already handles the upload pattern efficiently without a persistent connection
- No benefit over HTTP for the infrequent, complete-bundle upload pattern here

**Verdict:** Better suited to real-time data streaming. HTTP POST covers the upload use case more simply.

---

## Comparison Matrix

| Technology | Cross-Platform | Config Burden | Large Files | Metadata Speed | Delivery Guarantee | Recommended |
|---|---|---|---|---|---|---|
| **HTTP POST** | ✅ | Minimal | ✅ | Good | ✅ | **✅ Yes** |
| SCP/SSH | ⚠️ Hard on Windows | High | ✅ | Good | ✅ | For devs only |
| SMB | ⚠️ Fussy | High | ✅ | Good | ❌ | No |
| Raw TCP | ✅ | Moderate | ✅ | Excellent | ✅ | No |
| UDP | ✅ | Moderate | ❌ | Excellent | ❌ | No |
| MQTT | ✅ | Moderate | ❌ | Excellent | ✅ | Metadata-only niche |
| WebSockets | ✅ | Moderate | ✅ | Good | ✅ | No |

---

## Recommended Architecture

### Upload Flow

```
Colorimeter (RPi)                     Desktop (any OS)
─────────────────                     ────────────────
Capture images & metadata
        │
Bundle into HDF5
        │
POST /upload ──────── LAN ──────────► Receive HDF5
                                             │
                                      Unpack & archive
                                      ~/ColorimeterData/YYYY-MM/
                                             │
                                      Upsert → SQLite
        │
HTTP 200 OK ◄────────────────────────
        │
Update local SQLite
Archive local HDF5
```

### Storage Layout (both devices)

```
~/ColorimeterData/
  colorimeter.db          ← SQLite
  2025-06/
    sample_042.h5
    sample_043.h5
  2025-07/
    sample_044.h5
```

### SQLite Schema (both sides)

```sql
CREATE TABLE hdf5_files (
    id          INTEGER PRIMARY KEY,
    filename    TEXT,
    sha256      TEXT,
    received_at TEXT,
    sample_count INTEGER
);

CREATE TABLE measurements (
    id        INTEGER PRIMARY KEY,
    hdf5_id   INTEGER REFERENCES hdf5_files(id),
    sample_id TEXT,
    timestamp TEXT,
    metadata  TEXT    -- JSON string of HDF5 attrs
);
```

---

## Desktop Receiver (complete implementation)

```python
# colorimeter_receiver.py
# pip install flask h5py
from flask import Flask, request, jsonify
import h5py, sqlite3, pathlib, datetime, hashlib

ARCHIVE = pathlib.Path.home() / "ColorimeterData"
DB_PATH = ARCHIVE / "colorimeter.db"
HTTP_POST_PORT = 51051
app = Flask(__name__)

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
    """)
    con.commit()
    con.close()

@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("hdf5")
    if not f:
        return jsonify(error="no file"), 400

    month = datetime.date.today().strftime("%Y-%m")
    dest_dir = ARCHIVE / month
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f.filename
    f.save(dest)

    sha = hashlib.file_digest(open(dest, "rb"), "sha256").hexdigest()

    con = sqlite3.connect(DB_PATH)
    with h5py.File(dest, "r") as h5:
        samples = list(h5.keys())
        cur = con.execute(
            "INSERT INTO hdf5_files(filename,sha256,received_at,sample_count) VALUES(?,?,?,?)",
            (str(dest), sha, datetime.datetime.now().isoformat(), len(samples)))
        fid = cur.lastrowid
        for s in samples:
            meta = dict(h5[s].attrs)
            con.execute(
                "INSERT INTO measurements(hdf5_id,sample_id,timestamp,metadata) VALUES(?,?,?,?)",
                (fid, s, meta.get("timestamp", ""), str(meta)))
    con.commit()
    con.close()
    return jsonify(ok=True, samples=len(samples))

@app.route("/status")
def status():
    return jsonify(ok=True)

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=HTTP_POST_PORT)
```

---

## RPi Uploader (minimal)

```python
# upload_worker.py (runs on RPi)
import requests, pathlib
HTTP_POST_PORT = 51051

def upload_hdf5(h5_path: pathlib.Path, desktop_ip: str, port: int = HTTP_POST_PORT):
    url = f"http://{desktop_ip}:{port}/upload"
    with open(h5_path, "rb") as f:
        r = requests.post(
            url,
            files={"hdf5": (h5_path.name, f, "application/x-hdf5")},
            timeout=120   # generous timeout for large files
        )
    r.raise_for_status()
    return r.json()
```

---

## Optional Enhancement: mDNS Auto-Discovery

Add `zeroconf` to the desktop receiver to advertise as `colorimeter-desktop.local`. This eliminates manual IP entry entirely.

```python
# Add to colorimeter_receiver.py startup
from zeroconf import ServiceInfo, Zeroconf
import socket
HTTP_POST_PORT = 51051

info = ServiceInfo(
    "_http._tcp.local.",
    "Colorimeter Desktop._http._tcp.local.",
    addresses=[socket.inet_aton(socket.gethostbyname(socket.gethostname()))],
    port=HTTP_POST_PORT,
)
zc = Zeroconf()
zc.register_service(info)
# zc.unregister_service(info) / zc.close() on shutdown
```

The RPi then resolves `colorimeter-desktop.local` without any user configuration.

---

## Summary

HTTP POST is the right choice for this application. It requires no OS-level setup on any desktop platform, reuses the Python web stack already present on the RPi, handles both large binary files and small metadata reliably, and reduces the entire desktop-side integration to a single runnable Python script. The optional mDNS layer can eliminate the one remaining manual step (IP entry) for the smoothest possible user experience.
