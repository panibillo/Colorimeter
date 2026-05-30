# Colorimeter Upload Security Addendum

**Addendum to:** `colorimeter_upload_analysis.md`  
**Scope:** Hardening the HTTP POST upload architecture against unauthorized access, while keeping cross-platform compatibility. Targets security-conscious users who restrict desktop access to known devices only.

---

## Threat Model

Even on a trusted home LAN, the following are reasonable concerns:

| Threat | Risk |
|---|---|
| Another LAN device impersonates the RPi | Unauthorized file upload |
| Malicious filename escapes archive directory | Path traversal / filesystem damage |
| Receiver process has broad filesystem access | Compromise spreads beyond archive |
| Port 5001 reachable by all LAN devices | Expanded attack surface |
| Compromised RPi sends oversized or malformed HDF5 | Disk fill / parser exploit |
| Data intercepted in transit on LAN | Confidentiality breach |

The controls below address each threat in layers — any one layer failing does not compromise the whole system.

---

## Defense-in-Depth Summary

| Layer | Control | SSH/SCP Equivalent |
|---|---|---|
| OS firewall | Assign custom HTTP_POST_PORT number = 51051 | obfuscates from casual scanners, evades conflicts |
| OS firewall | Port HTTP_POST_PORT open only to RPi IP | `sshd` bound to specific interface |
| Network bind | Receiver listens on LAN IP only | SSH not listening on all adapters |
| Authentication | IP allowlist + HMAC shared secret | `authorized_keys` — known key only |
| Input validation | Filename sanitization + path confinement | `chroot` / restricted `rsync` module |
| Process isolation | Dedicated low-privilege OS user | Restricted shell / `rrsync` wrapper |
| Resource cap | `MAX_CONTENT_LENGTH` | `rsync --max-size` |
| Transport encryption | Optional self-signed TLS | SSH encryption (built-in) |

All controls are implemented in the updated scripts at the end of this document. The sections below explain each control, where it lives, and when it is configured.

---

## Control 1 — OS Firewall: Assign custom port and Restrict it to RPi IP Only

**Device:** Desktop  
**When:** Once during initial setup. Re-run only if the RPi's LAN IP changes.  
**Effect:** The port is invisible to every LAN device except the RPi. Even a bug in the Python receiver cannot be reached by other devices. Port 5001 is the standard port number, assigning a custom port number only defends from casual threats, but does help evade conflicts with other allowed processes.

**Prerequisite:** Assign the RPi a static DHCP lease in your router so its LAN IP never changes. All controls below assume `192.168.1.101` as the RPi's IP and `192.168.1.50` as the desktop's LAN IP. Substitute your actual values throughout.

### Linux — ufw

```bash
# DEVICE: Desktop (Linux)
# FILE: none — ufw system configuration
# WHEN: Once during setup. Run as root or with sudo.

sudo ufw deny 5001                                          # block all by default
sudo ufw allow from 192.168.1.101 to any port 5001         # RPi only
sudo ufw reload
```

### macOS — pf

```bash
# DEVICE: Desktop (macOS)
# FILE: /etc/pf.conf  (append these two lines)
# WHEN: Once during setup. Requires admin privileges.
# NOTE: macOS resets pf rules on reboot unless you add a LaunchDaemon (see below).

# Add to /etc/pf.conf:
block in proto tcp to any port 5001
pass  in proto tcp from 192.168.1.101 to any port 5001
```

```bash
# Then reload pf:
sudo pfctl -f /etc/pf.conf -e
```

To make the rules survive reboot, save the following as `/Library/LaunchDaemons/com.colorimeter.pf.plist` and load it once:

```xml
<!-- DEVICE: Desktop (macOS) -->
<!-- FILE: /Library/LaunchDaemons/com.colorimeter.pf.plist -->
<!-- WHEN: Created once. Loaded once with launchctl. Runs automatically on every boot. -->

<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.colorimeter.pf</string>
  <key>ProgramArguments</key>
  <array>
    <string>/sbin/pfctl</string>
    <string>-f</string>
    <string>/etc/pf.conf</string>
    <string>-e</string>
  </array>
  <key>RunAtLoad</key>         <true/>
</dict>
</plist>
```

```bash
sudo launchctl load /Library/LaunchDaemons/com.colorimeter.pf.plist
```

### Windows — Windows Firewall (PowerShell)

```powershell
# DEVICE: Desktop (Windows)
# FILE: none — Windows Firewall rules stored in system registry
# WHEN: Once during setup. Run PowerShell as Administrator.

# Allow RPi
New-NetFirewallRule `
  -DisplayName  "Colorimeter Receiver - Allow RPi" `
  -Direction    Inbound `
  -Protocol     TCP `
  -LocalPort    5001 `
  -RemoteAddress 192.168.1.101 `
  -Action       Allow

# Block everything else on 5001
New-NetFirewallRule `
  -DisplayName  "Colorimeter Receiver - Block All Others" `
  -Direction    Inbound `
  -Protocol     TCP `
  -LocalPort    5001 `
  -Action       Block
```

---

## Control 2 — Bind Receiver to LAN Interface Only

**Device:** Desktop  
**File:** `colorimeter_receiver.py`  
**When:** Set once in configuration; takes effect each time the receiver starts.  
**Effect:** The receiver does not listen on loopback, VPN adapters, or any other interface — only the physical LAN NIC. Combined with the firewall rule, this is belt-and-suspenders.

This is set in the `app.run()` call at the bottom of the receiver (see updated script):

```python
# DEVICE: Desktop
# FILE: colorimeter_receiver.py — app.run() call
# WHEN: Effective on every start of the receiver

DESKTOP_LAN_IP = "192.168.1.50"   # set once to your desktop's LAN IP

app.run(host=DESKTOP_LAN_IP, port=5001, ...)
```

---

## Control 3 — IP Allowlist in the Receiver

**Device:** Desktop  
**File:** `colorimeter_receiver.py`  
**When:** Set once in configuration; enforced on every incoming request.  
**Effect:** Any request not originating from the RPi's exact IP is rejected with HTTP 403 before any request body is read. This is the application-layer equivalent of SSH `AllowUsers`.

```python
# DEVICE: Desktop
# FILE: colorimeter_receiver.py — before_request hook (see full script below)
# WHEN: Runs on every incoming HTTP request

RPI_IP = "192.168.1.101"   # set once to your RPi's LAN IP

@app.before_request
def restrict_to_rpi():
    if request.remote_addr != RPI_IP:
        app.logger.warning(f"Rejected connection from {request.remote_addr}")
        abort(403)
```

---

## Control 4 — HMAC Shared Secret Authentication

**Device:** RPi (signs requests) and Desktop (verifies)  
**Files:** `upload_worker.py` and `colorimeter_receiver.py`  
**When:** Secret generated once during setup; signing/verification runs on every upload request.  
**Effect:** Even if another device spoofs the RPi's IP, it cannot forge a valid HMAC without the shared secret. This is the application-layer equivalent of SSH key authentication.

### Generating the secret (run once, on any machine)

```bash
# DEVICE: Any (run during setup)
# WHEN: Once. Copy the output to both devices.

python3 -c "import secrets; print(secrets.token_hex(32))"
# Example output: a3f8c2...  (64 hex chars)
# Store this value in the environment on both devices (see below).
```

### Storing the secret as an environment variable

**Linux / macOS — RPi and Desktop:**

```bash
# DEVICE: RPi — add to ~/.bashrc or ~/.profile, or to the systemd service (see Control 6)
# DEVICE: Desktop — add to ~/.bashrc, ~/.zshrc, or the launchd plist (see Control 6)
# WHEN: Once. Takes effect on next login or service restart.

export COLORIMETER_SECRET="a3f8c2..."   # paste your generated value
```

**Windows — Desktop:**

```powershell
# DEVICE: Desktop (Windows)
# WHEN: Once. Persists across reboots (system-level env var).
# Run PowerShell as Administrator.

[System.Environment]::SetEnvironmentVariable(
  "COLORIMETER_SECRET", "a3f8c2...",
  [System.EnvironmentVariableTarget]::Machine
)
```

The HMAC logic in both scripts reads this variable at startup (see full scripts below). The secret never appears in source code.

---

## Control 5 — Filename Sanitization and Path Confinement

**Device:** Desktop  
**File:** `colorimeter_receiver.py`  
**When:** Runs on every upload request, before any file is written to disk.  
**Effect:** Prevents path traversal attacks (`../../.bashrc`, `/etc/passwd`, etc.). Only filenames matching `[a-zA-Z0-9_-]+.h5` are accepted. The resolved destination path is also asserted to remain inside `ARCHIVE` before writing — a second independent check.

```python
# DEVICE: Desktop
# FILE: colorimeter_receiver.py — inside the upload() route (see full script below)
# WHEN: On every upload request

import re
SAFE_FILENAME = re.compile(r'^[a-zA-Z0-9_\-]+\.h5$')

safe_name = pathlib.Path(f.filename or "").name   # strip any directory component
if not SAFE_FILENAME.match(safe_name):
    abort(400)

dest = (dest_dir / safe_name).resolve()
if not str(dest).startswith(str(ARCHIVE.resolve())):
    abort(400)   # paranoid second check — should never be reached if regex passes
```

---

## Control 6 — Run Receiver as a Dedicated Low-Privilege User

**Device:** Desktop  
**When:** Once during setup. The receiver then always runs as this restricted user.  
**Effect:** Even if the receiver is compromised, the attacker gains access only to the archive directory — not the rest of the desktop filesystem. This is the equivalent of a walled-off partition.

### Linux

```bash
# DEVICE: Desktop (Linux)
# WHEN: Once during setup. Run as root or with sudo.

sudo useradd -r -s /usr/sbin/nologin colorimeter-recv
sudo mkdir -p /var/colorimeter-data
sudo chown colorimeter-recv:colorimeter-recv /var/colorimeter-data
```

To run the receiver automatically on boot as this user, create a systemd service:

```ini
# DEVICE: Desktop (Linux)
# FILE: /etc/systemd/system/colorimeter-receiver.service
# WHEN: Created once. Enabled once. Starts automatically on every boot thereafter.

[Unit]
Description=Colorimeter Upload Receiver
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=colorimeter-recv
WorkingDirectory=/var/colorimeter-data
Environment="COLORIMETER_SECRET=a3f8c2..."
ExecStart=/usr/bin/python3 /opt/colorimeter/colorimeter_receiver.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
# Enable and start once:
sudo systemctl daemon-reload
sudo systemctl enable colorimeter-receiver
sudo systemctl start colorimeter-receiver
```

### macOS

```bash
# DEVICE: Desktop (macOS)
# WHEN: Once during setup.

sudo dscl . -create /Users/colorimeter-recv
sudo dscl . -create /Users/colorimeter-recv UserShell /usr/bin/false
sudo dscl . -create /Users/colorimeter-recv UniqueID 401
sudo dscl . -create /Users/colorimeter-recv PrimaryGroupID 20
sudo mkdir -p /var/colorimeter-data
sudo chown colorimeter-recv /var/colorimeter-data
```

Create a LaunchDaemon to run on boot:

```xml
<!-- DEVICE: Desktop (macOS) -->
<!-- FILE: /Library/LaunchDaemons/com.colorimeter.receiver.plist -->
<!-- WHEN: Created once. Loaded once with launchctl. Starts on every boot. -->

<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.colorimeter.receiver</string>
  <key>UserName</key>          <string>colorimeter-recv</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/opt/colorimeter/colorimeter_receiver.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>COLORIMETER_SECRET</key>
    <string>a3f8c2...</string>
  </dict>
  <key>RunAtLoad</key>         <true/>
  <key>KeepAlive</key>         <true/>
  <key>StandardOutPath</key>   <string>/var/colorimeter-data/receiver.log</string>
  <key>StandardErrorPath</key> <string>/var/colorimeter-data/receiver.log</string>
</dict>
</plist>
```

```bash
sudo launchctl load /Library/LaunchDaemons/com.colorimeter.receiver.plist
```

### Windows

Create a local user with no interactive login rights and restricted filesystem access:

```powershell
# DEVICE: Desktop (Windows)
# WHEN: Once during setup. Run PowerShell as Administrator.

# Create restricted local user
$pw = ConvertTo-SecureString "ColorimeterRecv2024!" -AsPlainText -Force
New-LocalUser "colorimeter-recv" -Password $pw -PasswordNeverExpires `
  -UserMayNotChangePassword -Description "Colorimeter receiver service account"

# Remove from Users group (optional, extra hardening)
Remove-LocalGroupMember -Group "Users" -Member "colorimeter-recv"

# Create and restrict archive directory
New-Item -ItemType Directory -Path "C:\ColorimeterData" -Force
$acl = Get-Acl "C:\ColorimeterData"
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
  "colorimeter-recv", "Modify", "ContainerInherit,ObjectInherit", "None", "Allow")
$acl.SetAccessRule($rule)
Set-Acl "C:\ColorimeterData" $acl
```

Register as a Windows Service using `NSSM` (Non-Sucking Service Manager, free):

```powershell
# DEVICE: Desktop (Windows)
# WHEN: Once during setup. Service starts automatically on every boot.
# Requires nssm.exe — download from https://nssm.cc

nssm install ColorimeterReceiver python "C:\opt\colorimeter\colorimeter_receiver.py"
nssm set ColorimeterReceiver ObjectName ".\colorimeter-recv" "ColorimeterRecv2024!"
nssm set ColorimeterReceiver AppEnvironmentExtra "COLORIMETER_SECRET=a3f8c2..."
nssm set ColorimeterReceiver AppDirectory "C:\ColorimeterData"
nssm start ColorimeterReceiver
```

---

## Control 7 — File Size Cap

**Device:** Desktop  
**File:** `colorimeter_receiver.py`  
**When:** Set once in configuration; enforced by Flask on every upload before the file is read.  
**Effect:** Prevents disk exhaustion from an oversized or malicious upload. Tune `MAX_UPLOAD_MB` to comfortably exceed your largest expected HDF5 bundle.

```python
# DEVICE: Desktop
# FILE: colorimeter_receiver.py — app configuration (see full script below)
# WHEN: Enforced on every upload request by Flask before any data is written

MAX_UPLOAD_MB = 500
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

@app.errorhandler(413)
def too_large(e):
    return jsonify(error="upload exceeds size limit"), 413
```

---

## Control 8 — Optional TLS (Transport Encryption)

**Device:** Desktop (generates cert); RPi (trusts cert)  
**When:** Cert generated once. Loaded by receiver on every start.  
**Effect:** Encrypts all traffic between RPi and desktop in transit, closing the one gap HTTP has versus SCP. On a home LAN this is defence against a LAN-level packet capture. A self-signed certificate is sufficient — no CA needed.

### Generate a self-signed certificate (run once on the desktop)

```bash
# DEVICE: Desktop
# WHEN: Once during setup. The cert is valid for 10 years.
# Store cert.pem and key.pem alongside colorimeter_receiver.py

openssl req -x509 -newkey rsa:4096 \
  -keyout key.pem -out cert.pem \
  -days 3650 -nodes \
  -subj "/CN=colorimeter-desktop" \
  -addext "subjectAltName=IP:192.168.1.50"
```

Copy `cert.pem` to the RPi (one time, via USB or scp):

```bash
# DEVICE: RPi
# WHEN: Once during setup
# FILE: /opt/colorimeter/desktop_cert.pem

scp user@192.168.1.50:/path/to/cert.pem /opt/colorimeter/desktop_cert.pem
```

TLS is enabled by passing `ssl_context` to `app.run()` in the receiver and `verify=` in `requests.post()` on the RPi — both shown in the updated scripts below.

---

## Updated Scripts

Both scripts below incorporate all security controls. Configuration values are collected at the top of each file. TLS is shown as an optional block clearly marked.

---

### `colorimeter_receiver.py` — Desktop (updated, security-hardened)

```python
# colorimeter_receiver.py
# DEVICE: Desktop
# RUNS: On every boot (via systemd / LaunchDaemon / NSSM — see Control 6)
#       or manually: python3 colorimeter_receiver.py
# INSTALL: pip install flask h5py
#
# ENVIRONMENT VARIABLE REQUIRED:
#   COLORIMETER_SECRET — shared HMAC secret (see Control 4 for generation)
#
# CONFIGURATION: edit the values in the CONFIG section below before first run.

import hashlib
import hmac
import logging
import os
import pathlib
import re
import sqlite3
import datetime

from flask import Flask, abort, jsonify, request

# ── CONFIG ──────────────────────────────────────────────────────────────────
DESKTOP_LAN_IP  = "192.168.1.50"    # this desktop's LAN IP — bind to this only
RPI_IP          = "192.168.1.101"   # RPi's static LAN IP — only source accepted
PORT            = 5001
MAX_UPLOAD_MB   = 500               # reject uploads larger than this
ARCHIVE         = pathlib.Path("/var/colorimeter-data")   # Linux/macOS
# ARCHIVE       = pathlib.Path("C:/ColorimeterData")      # Windows — uncomment

# TLS — optional (Control 8). Set USE_TLS = True and provide cert/key paths.
USE_TLS         = False
TLS_CERT        = pathlib.Path("cert.pem")
TLS_KEY         = pathlib.Path("key.pem")
# ────────────────────────────────────────────────────────────────────────────

DB_PATH        = ARCHIVE / "colorimeter.db"
SAFE_FILENAME  = re.compile(r'^[a-zA-Z0-9_\-]+\.h5$')
SECRET         = os.environ.get("COLORIMETER_SECRET", "").encode()

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


# ── Startup ──────────────────────────────────────────────────────────────────

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


# ── Security hooks ───────────────────────────────────────────────────────────

@app.before_request
def restrict_to_rpi():
    """Control 2+3: reject any connection not from the RPi's IP."""
    if request.remote_addr != RPI_IP:
        app.logger.warning("Rejected connection from %s", request.remote_addr)
        abort(403)


@app.before_request
def verify_hmac():
    """Control 4: verify HMAC signature on every request."""
    if not SECRET:
        app.logger.error("COLORIMETER_SECRET not set — refusing all requests")
        abort(500)
    auth     = request.headers.get("X-Colorimeter-Auth", "")
    filename = request.headers.get("X-Filename", "")
    expected = hmac.new(SECRET, filename.encode(), hashlib.sha256).hexdigest()
    token    = f"HMAC-SHA256 {expected}"
    if not hmac.compare_digest(auth, token):
        app.logger.warning("HMAC verification failed from %s", request.remote_addr)
        abort(401)


@app.errorhandler(413)
def too_large(e):
    """Control 7: upload exceeded MAX_CONTENT_LENGTH."""
    return jsonify(error="upload exceeds size limit"), 413


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/upload", methods=["POST"])
def upload():
    import h5py  # imported here so missing lib gives a clear error at call time

    f = request.files.get("hdf5")
    if not f:
        return jsonify(error="no file field named 'hdf5'"), 400

    # Control 5: sanitize filename and confine to ARCHIVE
    raw_name  = f.filename or ""
    safe_name = pathlib.Path(raw_name).name          # strip any directory prefix
    if not SAFE_FILENAME.match(safe_name):
        app.logger.warning("Rejected unsafe filename: %r", raw_name)
        return jsonify(error="invalid filename"), 400

    month    = datetime.date.today().strftime("%Y-%m")
    dest_dir = ARCHIVE / month
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest     = (dest_dir / safe_name).resolve()

    # Paranoid path-confinement check (second independent guard)
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
        con.close()
        dest.unlink(missing_ok=True)   # don't keep a corrupt/malicious file
        app.logger.error("HDF5 processing failed: %s", exc)
        return jsonify(error="HDF5 processing failed"), 422
    finally:
        con.close()

    app.logger.info("Received %s — %d samples — sha256 %s", safe_name, len(samples), sha[:12])
    return jsonify(ok=True, samples=len(samples), sha256=sha)


@app.route("/status")
def status():
    return jsonify(ok=True)


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not SECRET:
        raise RuntimeError(
            "COLORIMETER_SECRET environment variable is not set.\n"
            "Generate one with:  python3 -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "Then export it before starting the receiver.")
    init_db()
    ssl_ctx = (str(TLS_CERT), str(TLS_KEY)) if USE_TLS else None
    app.run(host=DESKTOP_LAN_IP, port=PORT, ssl_context=ssl_ctx)
```

---

### `upload_worker.py` — RPi (updated, security-hardened)

```python
# upload_worker.py
# DEVICE: RPi Zero 2W
# RUNS: Called from the Colorimeter application each time an HDF5 bundle is ready.
#       Not a standalone service — it is a module imported by the main app.
# INSTALL: pip install requests
#
# ENVIRONMENT VARIABLE REQUIRED:
#   COLORIMETER_SECRET — same shared secret as on the desktop (see Control 4)
#
# CONFIGURATION: edit the values in the CONFIG section below before first use.

import hashlib
import hmac
import logging
import os
import pathlib

import requests

# ── CONFIG ──────────────────────────────────────────────────────────────────
DESKTOP_IP  = "192.168.1.50"    # desktop's LAN IP (or "colorimeter-desktop.local"
                                #   if mDNS is configured)
PORT        = 5001
TIMEOUT_SEC = 120               # generous — large HDF5 files over WiFi

# TLS — must match the receiver. Set USE_TLS = True and point to the copy of
# the desktop's self-signed cert that was copied to the RPi (see Control 8).
USE_TLS     = False
DESKTOP_CERT = pathlib.Path("/opt/colorimeter/desktop_cert.pem")
# ────────────────────────────────────────────────────────────────────────────

SECRET = os.environ.get("COLORIMETER_SECRET", "").encode()

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _make_auth_header(filename: str) -> str:
    """Compute HMAC-SHA256 signature for the given filename."""
    if not SECRET:
        raise RuntimeError(
            "COLORIMETER_SECRET environment variable is not set on the RPi.")
    sig = hmac.new(SECRET, filename.encode(), hashlib.sha256).hexdigest()
    return f"HMAC-SHA256 {sig}"


def upload_hdf5(h5_path: pathlib.Path) -> dict:
    """
    Upload a single HDF5 bundle to the desktop receiver.

    Returns the parsed JSON response on success.
    Raises requests.HTTPError on a non-2xx response.
    Raises requests.RequestException on network failure.
    """
    scheme   = "https" if USE_TLS else "http"
    url      = f"{scheme}://{DESKTOP_IP}:{PORT}/upload"
    filename = h5_path.name

    headers = {
        "X-Colorimeter-Auth": _make_auth_header(filename),
        "X-Filename":          filename,
    }

    # TLS verification: use the desktop's self-signed cert, or disable (False)
    # Never use verify=False in production — use the copied cert instead.
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

## Setup Checklist

Use this checklist for a complete secure deployment. Steps are ordered; each depends on the previous.

**Once, during initial setup:**

- [ ] Assign RPi a static DHCP lease in your router (note the IP)
- [ ] Generate `COLORIMETER_SECRET` and record it securely
- [ ] Set `COLORIMETER_SECRET` environment variable on both devices
- [ ] Edit `DESKTOP_LAN_IP`, `RPI_IP`, `DESKTOP_IP` in both scripts
- [ ] Apply OS firewall rule on desktop (Control 1 — Linux/macOS/Windows section above)
- [ ] Create `colorimeter-recv` restricted OS user and archive directory (Control 6)
- [ ] *(Optional)* Generate TLS cert on desktop, copy `cert.pem` to RPi; set `USE_TLS = True` in both scripts (Control 8)
- [ ] Install receiver as a system service (Control 6 — systemd / LaunchDaemon / NSSM)

**On every desktop boot (automatic if service is installed):**

- [ ] `colorimeter-receiver` service starts, binds to `DESKTOP_LAN_IP:5001`

**On every upload (automatic, no user action):**

- [ ] RPi signs request with HMAC, POSTs HDF5 to desktop
- [ ] Desktop verifies IP, HMAC, filename, size, path — then writes to archive
