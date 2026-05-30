# Data Upload Architecture

## Overview

The Colorimeter uploads measurement data (images and metadata) to a desktop on the same LAN using **HTTP POST over a lightweight REST interface**. Both devices maintain a local archive of HDF5 bundle files and a SQLite database of measurement metadata.

---

## Components

### RPi Zero 2W (Colorimeter)
- Bundles captured images and metadata into a single **HDF5 file** per upload session
- POSTs the HDF5 file to the desktop receiver via HTTP multipart upload
- On confirmed receipt (HTTP 200), updates its local **SQLite database** with metadata and a reference to the archived HDF5 file
- Retains a local copy of all HDF5 files in a dated archive directory

### Desktop Receiver (`colorimeter_receiver.py`)
- A small **Flask HTTP server** (port 5001) that runs on the desktop — any OS
- Accepts HDF5 uploads at `POST /upload`; exposes `GET /status` for heartbeat checks
- Unpacks each HDF5 file, extracts per-sample metadata, and upserts records into a local **SQLite database**
- Archives the HDF5 file into a `~/ColorimeterData/YYYY-MM/` directory
- Requires only `pip install flask h5py` — no OS-level configuration

---

## Data Flow

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
                                             │
                                      Upsert → SQLite
        │
HTTP 200 OK ◄────────────────────────
        │
Update local SQLite
Archive local HDF5
```

---

## Storage Schema (both sides)

| Table | Key Columns |
|---|---|
| `hdf5_files` | `id`, `filename`, `sha256`, `received_at`, `sample_count` |
| `measurements` | `id`, `hdf5_id` (FK), `sample_id`, `timestamp`, `metadata` |

HDF5 files are archived under `~/ColorimeterData/YYYY-MM/` on the desktop and a parallel path on the RPi.

---

## Configuration

| Step | Action |
|---|---|
| 1 | `pip install flask h5py` on the desktop |
| 2 | Run `python colorimeter_receiver.py` |
| 3 | Enter the desktop's LAN IP in the Colorimeter web UI (one time) |

**Optional:** Add `zeroconf` mDNS advertisement on the desktop so the RPi discovers it automatically as `colorimeter-desktop.local`, eliminating manual IP entry.

---

## Technology Choice Rationale

HTTP POST was selected over alternatives (SCP/SSH, SMB, UDP, MQTT, WebSockets) because:
- No OS-level configuration required on the desktop (works on Windows, macOS, Linux)
- Reuses the same Python/web stack already present on the RPi
- Reliable delivery with error feedback (HTTP status codes)
- Handles both large binary files (multipart) and metadata efficiently
- The entire desktop receiver is a single runnable Python file
