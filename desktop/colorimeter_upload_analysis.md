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

## Security Controls

The HTTP POST architecture supports layered defenses appropriate for a LAN instrument. The table below summarizes all controls. Full OS-specific firewall setup instructions are in `colorimeter_upload_security_addendum.md`.

| # | Control | Where | Effect |
|---|---|---|---|
| 1 | Custom port (`HTTP_POST_PORT = 51051`) + OS firewall restricted to RPi IP | Desktop OS | Port invisible to all LAN devices except the RPi |
| 2 | Receiver binds to LAN IP only | `colorimeter_receiver.py` | Not reachable via loopback or VPN adapters |
| 3 | IP allowlist in receiver | `colorimeter_receiver.py` | HTTP 403 returned to any non-RPi source before body is read |
| 4 | HMAC-SHA256 shared secret | Both scripts | Forged requests rejected even if IP is spoofed |
| 5 | Filename sanitization + path confinement | `colorimeter_receiver.py` | Prevents path traversal attacks |
| 6 | Dedicated low-privilege OS user | Desktop OS | Compromise limited to archive directory |
| 7 | File size cap (`MAX_CONTENT_LENGTH`) | `colorimeter_receiver.py` | Prevents disk exhaustion from oversized uploads |
| 8 | Optional TLS (self-signed cert) | Both scripts | Encrypts traffic in transit |

**Minimum for a home-lab deployment:** Controls 1–5 and 7 (firewall + IP allowlist + HMAC + filename check + size cap). Controls 6 and 8 add meaningful hardening for shared-network or multi-user environments.

**Setup prerequisite:** Assign the RPi a static DHCP lease in your router before applying any IP-based controls.

**Generating the HMAC secret (once, on any machine):**

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
# Store in COLORIMETER_SECRET environment variable on both devices
```

The scripts below incorporate all controls. Configuration values are collected at the top of each file. TLS is shown as an optional block clearly marked.

---

## Desktop Receiver (security-hardened)

```python
# colorimeter_receiver.py
# pip install flask h5py
#
# ENVIRONMENT VARIABLE REQUIRED:
#   COLORIMETER_SECRET — shared HMAC secret; generate with:
#   python3 -c "import secrets; print(secrets.token_hex(32))"

import datetime
import hashlib
import hmac
import logging
import os
import pathlib
import re
import sqlite3

from flask import Flask, abort, jsonify, request

# ── CONFIG ──────────────────────────────────────────────────────────────────
DESKTOP_LAN_IP   = "192.168.1.50"    # this desktop's LAN IP — bind here only
RPI_IP           = "192.168.1.101"   # RPi's static LAN IP — only source accepted
HTTP_POST_PORT   = 51051             # custom port (see security addendum for firewall rules)
MAX_UPLOAD_MB    = 500
ARCHIVE          = pathlib.Path.home() / "ColorimeterData"
# ARCHIVE        = pathlib.Path("C:/ColorimeterData")   # Windows — uncomment

# TLS — optional (Control 8). Set USE_TLS = True and provide cert/key paths.
USE_TLS          = False
TLS_CERT         = pathlib.Path("cert.pem")
TLS_KEY          = pathlib.Path("key.pem")
# ────────────────────────────────────────────────────────────────────────────

DB_PATH       = ARCHIVE / "colorimeter.db"
SAFE_FILENAME = re.compile(r'^[a-zA-Z0-9_\-]+\.h5$')
SECRET        = os.environ.get("COLORIMETER_SECRET", "").encode()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


def init_db():
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS hdf5_files (
            id           INTEGER PRIMARY KEY,
            filename     TEXT,
            sha256       TEXT,
            received_at  TEXT,
            sample_count INTEGER
        );
        CREATE TABLE IF NOT EXISTS measurements (
            id        INTEGER PRIMARY KEY,
            hdf5_id   INTEGER REFERENCES hdf5_files(id),
            sample_id TEXT,
            timestamp TEXT,
            metadata  TEXT
        );
    """)
    con.commit()
    con.close()


@app.before_request
def restrict_to_rpi():
    if request.remote_addr != RPI_IP:
        app.logger.warning("Rejected connection from %s", request.remote_addr)
        abort(403)


@app.before_request
def verify_hmac():
    if not SECRET:
        app.logger.error("COLORIMETER_SECRET not set — refusing all requests")
        abort(500)
    auth     = request.headers.get("X-Colorimeter-Auth", "")
    filename = request.headers.get("X-Filename", "")
    expected = hmac.new(SECRET, filename.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(auth, f"HMAC-SHA256 {expected}"):
        app.logger.warning("HMAC verification failed from %s", request.remote_addr)
        abort(401)


@app.errorhandler(413)
def too_large(e):
    return jsonify(error="upload exceeds size limit"), 413


@app.route("/upload", methods=["POST"])
def upload():
    import h5py

    f = request.files.get("hdf5")
    if not f:
        return jsonify(error="no file field named 'hdf5'"), 400

    raw_name  = f.filename or ""
    safe_name = pathlib.Path(raw_name).name
    if not SAFE_FILENAME.match(safe_name):
        app.logger.warning("Rejected unsafe filename: %r", raw_name)
        return jsonify(error="invalid filename"), 400

    month    = datetime.date.today().strftime("%Y-%m")
    dest_dir = ARCHIVE / month
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest     = (dest_dir / safe_name).resolve()

    if not str(dest).startswith(str(ARCHIVE.resolve())):
        app.logger.error("Path traversal blocked: %s", dest)
        return jsonify(error="invalid filename"), 400

    f.save(dest)
    sha = hashlib.file_digest(open(dest, "rb"), "sha256").hexdigest()

    con = sqlite3.connect(DB_PATH)
    try:
        with h5py.File(dest, "r") as h5:
            samples = list(h5.keys())
            cur = con.execute(
                "INSERT INTO hdf5_files(filename,sha256,received_at,sample_count)"
                " VALUES(?,?,?,?)",
                (str(dest), sha, datetime.datetime.now().isoformat(), len(samples)))
            fid = cur.lastrowid
            for s in samples:
                meta = dict(h5[s].attrs)
                con.execute(
                    "INSERT INTO measurements(hdf5_id,sample_id,timestamp,metadata)"
                    " VALUES(?,?,?,?)",
                    (fid, s, meta.get("timestamp", ""), str(meta)))
        con.commit()
    except Exception as exc:
        dest.unlink(missing_ok=True)
        app.logger.error("HDF5 processing failed: %s", exc)
        return jsonify(error="HDF5 processing failed"), 422
    finally:
        con.close()

    app.logger.info("Received %s — %d samples — sha256 %s", safe_name, len(samples), sha[:12])
    return jsonify(ok=True, samples=len(samples), sha256=sha)


@app.route("/status")
def status():
    return jsonify(ok=True)


if __name__ == "__main__":
    if not SECRET:
        raise RuntimeError(
            "COLORIMETER_SECRET environment variable is not set.\n"
            "Generate one with:  python3 -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "Then export it before starting the receiver.")
    init_db()
    ssl_ctx = (str(TLS_CERT), str(TLS_KEY)) if USE_TLS else None
    app.run(host=DESKTOP_LAN_IP, port=HTTP_POST_PORT, ssl_context=ssl_ctx)
```

---

## RPi Uploader (security-hardened)

```python
# upload_worker.py (runs on RPi)
#
# ENVIRONMENT VARIABLE REQUIRED:
#   COLORIMETER_SECRET — same shared secret as on the desktop

import hashlib
import hmac
import logging
import os
import pathlib

import requests

# ── CONFIG ──────────────────────────────────────────────────────────────────
DESKTOP_IP     = "192.168.1.50"    # desktop's LAN IP (or "colorimeter-desktop.local"
                                   #   if mDNS is configured)
HTTP_POST_PORT = 51051
TIMEOUT_SEC    = 120               # generous — large HDF5 files over Wi-Fi

# TLS — must match the receiver. Set USE_TLS = True and point to the copy of
# the desktop's self-signed cert copied to the RPi (see Control 8 in addendum).
USE_TLS        = False
DESKTOP_CERT   = pathlib.Path("/opt/colorimeter/desktop_cert.pem")
# ────────────────────────────────────────────────────────────────────────────

SECRET = os.environ.get("COLORIMETER_SECRET", "").encode()
log    = logging.getLogger(__name__)


def _make_auth_header(filename: str) -> str:
    if not SECRET:
        raise RuntimeError("COLORIMETER_SECRET environment variable is not set on the RPi.")
    sig = hmac.new(SECRET, filename.encode(), hashlib.sha256).hexdigest()
    return f"HMAC-SHA256 {sig}"


def upload_hdf5(h5_path: pathlib.Path) -> dict:
    """Upload a single HDF5 bundle to the desktop receiver.

    Returns the parsed JSON response on success.
    Raises requests.HTTPError on a non-2xx response.
    Raises requests.RequestException on network failure.
    """
    scheme   = "https" if USE_TLS else "http"
    url      = f"{scheme}://{DESKTOP_IP}:{HTTP_POST_PORT}/upload"
    filename = h5_path.name

    headers = {
        "X-Colorimeter-Auth": _make_auth_header(filename),
        "X-Filename":          filename,
    }
    tls_verify = str(DESKTOP_CERT) if USE_TLS else True

    log.info("Uploading %s to %s", filename, url)
    with open(h5_path, "rb") as fh:
        response = requests.post(
            url,
            headers=headers,
            files={"hdf5": (filename, fh, "application/x-hdf5")},
            timeout=TIMEOUT_SEC,
            verify=tls_verify,
        )
    response.raise_for_status()
    result = response.json()
    log.info("Upload confirmed: %s — %d samples", filename, result.get("samples", -1))
    return result
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
