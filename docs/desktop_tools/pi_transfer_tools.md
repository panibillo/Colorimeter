# Desktop tools: moving files and data between the desktop and the Pi

Last reviewed: 2026-11-11 (sync_from_pi.sh deleted the same day).

This document lists the scripts that copy files between this desktop and
the Colorimeter Pi (`rpizero3`): program files going to the Pi, and
captures and database rows coming back.

Companion documents:

- [inspection_tools.md](inspection_tools.md): looking inside the files, contact sheets, pad finding.
- [analysis_chain_tools.md](analysis_chain_tools.md): the analysis chain, step by step.

Each status below comes from reading the scripts and the git history on the
date above. Nothing was run to confirm it.

---

## Status key

| Status | Meaning |
|---|---|
| **Current** | In use now. Use it. |
| **Parked** | Working code kept for later, but not in use. |
| **Deprecated** | Don't use. Candidate to delete. |

---

## Where things live on each machine

| What | On the desktop | On the Pi |
|---|---|---|
| Project root | `~/Projects/Colorimeter/` | `~/pi/Colorimeter/` |
| Capture archives | `captures/<samp_no>.h5` | `~/pi/Colorimeter/captures/<samp_no>.h5` |
| Instrument database | `src/colorimeter.sqlite` | `~/pi/Colorimeter/src/colorimeter.sqlite` |
| Commissioning scripts | `provisioning/deploy/` | `~/pi/Colorimeter/provisioning/` (one level up; see below) |

All scripts use `ssh`, `scp` or `rsync` with the host name set up in
`~/.ssh/config` (for example `rpizero3`). Setup: `docs/commissioning/04_ssh.md`.

---

## 1. Bringing captures back to the desktop

**Purpose.** Copy capture archives (`.h5`) from the Pi to `captures/` here
so the desktop analysis can read them.

**Method.** `rsync` copies only files that are new or changed, and never
deletes anything on either side.

| Script | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `pull_captures.sh <host> <pattern>` | **Current** | Copies only the captures with the pattern's letter prefix and a number at or above it. `C1039` pulls C1039, C1040, …, but not D1001. Matches upper and lower case alike. | `.h5` files in `captures/`. |
| `sync_captures_from_pi.sh <host>` | **Current** | Copies every capture on the Pi. | `.h5` files in `captures/`. |

Example:

    ./pull_captures.sh rpizero3 C1039

---

## 2. Bringing database rows or the whole database back

**Purpose.** The analysis needs the Pi's rows for each capture: the
`samples` row (holder `cap_style`, arsenic `manual_read`), and sometimes
`roi`, `exposure`, `layermodels` or `colorcard`. The desktop database also
holds desktop-only tables (`sample_roi`), so it shouldn't simply be
replaced by the Pi's copy.

**Method.** `fetch_samplerows_from_pi.py` copies the Pi's database into a
scratch file, then copies selected rows into the local database. It backs
up the local database first. It works in one of two ways per table:

- **upsert**: matches rows on a natural key (`samp_no` for `samples`) and
  leaves other local rows alone.
- **mirror**: replaces the whole local table with the Pi's, keeping row
  numbers. It's the default for `roi`, `exposure`, `layermodels` and
  `colorcard`, which keep their history by row number.

It refuses `sample_roi`, which exists only on the desktop.

| Script | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `desktop/fetch_samplerows_from_pi.py` | **Current** | `C1039 C1040` copies those `samples` rows. `--table roi` copies a table. `--list-tables`, `--dry-run`, `--mode`, `--no-backup`, `--remote` (default `rpizero3`). | Database rows in `src/colorimeter.sqlite`; backup at `src/colorimeter.sqlite.bak-<date>`. |
| `pulldb.sh [dest]` | **Current** | Copies the Pi's whole database to a dated file. It never overwrites a file; it adds the minute to the name if needed. Host fixed to `rpizero3`. (The header comment calls it `pullpidb.sh`.) | `~/R/pi/colorimeter_<date>.sqlite` by default, or the path given. |

Examples:

    .venv/bin/python3 desktop/fetch_samplerows_from_pi.py --dry-run C1039 C1040
    .venv/bin/python3 desktop/fetch_samplerows_from_pi.py C1039 C1040
    .venv/bin/python3 desktop/fetch_samplerows_from_pi.py --table layermodels

Caution: mirroring `config` overwrites desktop-local settings
(`HOWTO.md` §8.9).

---

## 3. Sending the database to the Pi

**Purpose.** Put a database prepared on the desktop (for example new
`layermodels` rows from a flat-field fit) onto the Pi.

**Method.** Back up the Pi's database on the Pi first, then overwrite it
with the local file.

| Script | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `pushdb.sh [src]` | **Current**, use with care | Backs up the Pi's database as `colorimeter.sqlite.bak-<date>` on the Pi (falls back to `pulldb.sh` if that fails), then copies the local file over it. Host fixed to `rpizero3`. | On the Pi: `~/pi/Colorimeter/src/colorimeter.sqlite` replaced, and the backup beside it. |

**This replaces the whole database on the Pi.** Any `samples` rows the Pi
recorded since the last pull are lost from the live file (they survive in
the backup). Pull them first with `fetch_samplerows_from_pi.py`, and stop
the Colorimeter program on the Pi before pushing. The desktop-only
`sample_roi` table travels along; the Pi ignores it.

---

## 4. Sending program files to the Pi

**Purpose.** Install or update the Colorimeter program on the Pi.

**Method.** `rsync` sends new or changed files. None of these scripts
delete files on the Pi.

| Script | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `sync_to_pi.sh <host>` | **Current** | Sends exactly the files listed in `sync_pi_files.txt` (the program's import list). A new module must be added to that list, or it never reaches the Pi. The project root is fixed as `~/Projects/Colorimeter`. | Files under `~/pi/Colorimeter/` on the Pi. |
| `bulk_sync_to_pi.sh [-n] <host>` | **Current** | Sends every `.py`, `.yaml` and `.csv` file under `src/`, `dev/` and `config/`, except mock drivers. `-n` shows what would be sent. | Files under `~/pi/Colorimeter/src/`, `dev/`, `config/`. |
| `push_pi_scripts.sh [-n] <host>` | **Current** | Sends the commissioning scripts. They sit in `provisioning/deploy/` here but must land one level higher on the Pi, because `make_venv.sh` finds the project root from its own folder. | Files in `~/pi/Colorimeter/provisioning/` on the Pi. |
| `sync_pi_files.txt` | **Current** (data) | The list `sync_to_pi.sh` reads. | — |

What to run on the Pi after `push_pi_scripts.sh` is in
`provisioning/README.md` and `docs/RPi_commissioning.md`.

---

## 5. Not in use, or not for the Pi

| File | Status | What it is |
|---|---|---|
| `desktop/colorimeter_receiver.py` | **Parked** | A small Flask web server (version 0.1) that would receive `.h5` uploads from the Pi over the local network. The Pi side isn't written: `arbiter.post_results()` logs "upload not implemented". It would store files and a database under `~/ColorimeterData/`. Design notes: `desktop/colorimeter_upload_analysis.md`, `colorimeter_upload_security_addendum.md`. |
| `sync_lenore.md` | **Current** (not Pi) | Hand-typed `rsync` commands to copy the project between this desktop and the laptop `lenore`. Listed here only so it isn't mistaken for a Pi script. |

---

## Typical round trip after a session of captures

1. `./pull_captures.sh rpizero3 C10nn` — the new archives.
2. `.venv/bin/python3 desktop/fetch_samplerows_from_pi.py C10nn …` — their
   `samples` rows.
3. If the Pi's ROIs or models changed:
   `fetch_samplerows_from_pi.py --table roi` (or `layermodels`).
4. Analyze on the desktop.
5. Only if the desktop made rows the Pi needs: stop the program on the Pi,
   then `./pushdb.sh`.

---

## Removed

- `sync_from_pi.sh` (deleted 2026-11-11). Its paths were still placeholders,
  so it never ran. `pull_captures.sh` and `fetch_samplerows_from_pi.py`
  cover what is pulled back from the Pi.

---

## How to keep this document current

When a transfer script is added, changed or retired, or a path on either
machine moves:

1. Edit its row, with status and where the files land.
2. Update "Where things live" if a path changed.
3. Change the "Last reviewed" date at the top.
