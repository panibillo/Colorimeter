# Step 3 — Upload firmware, database, scripts; build directories + venv

**Status:** ✅ ready

Goal: put the application code (**firmware**), the **reference database**, the
**config**, and the **commissioning scripts** onto the Pi under
`~/pi/Colorimeter`, then create the Python virtual environment the app runs in.

Some commands run **on local** (the desktop, source of truth for the code) and
some **on the Pi**. Each block is marked.

← [02_peripherals.md](02_peripherals.md) · Back to [master guide](../RPi_commissioning.md) · Re-entering? [00_reentry.md](00_reentry.md) · Next → [04_ssh.md](04_ssh.md)

> **Repeating this step?** 3a/3b/3d are safe to re-run any time. 🔴 **3c
> overwrites the unit's database** — see the warning there. 🟡 **3e's
> `make_venv.sh` reuses an existing `.venv`** and will not repair a bad or
> relocated one; `rm -rf .venv` first if you need a clean rebuild.

---

## Directory layout produced

```
~/pi/Colorimeter/            (on the Pi; mirrors ~/Projects/Colorimeter on local)
├── src/                     firmware — the application
│   └── colorimeter.sqlite   reference database (copied, step 3c)
├── config/                  instrument.yaml
│                            (touch_cal.json / focus_cal.json are written on the Pi)
├── commissioning/           setup + verify scripts
├── captures/                capture archives land here (created below)
└── .venv/                   Python venv, --system-site-packages (step 3e)
```

---

## Step 3a — Create the app directory (on the Pi)

```bash
# On the Pi:
mkdir -p ~/pi/Colorimeter/captures
```

The `captures/` directory is where `config/instrument.yaml` writes archives
(`capture.directory: captures`, resolved relative to the project root). Creating
it up front avoids a first-run error.

---

## Step 3b — Upload the firmware (on local)

The curated file `sync_pi_files.txt` lists exactly the modules the two entry
points (`src/main.py`, `dev/camtest8.py`) import, plus the config they read.
`sync_to_pi.sh` rsyncs that list to the Pi:

```bash
# On local, from ~/Projects/Colorimeter:
./sync_to_pi.sh <host>          # e.g. ./sync_to_pi.sh rpizero2
```

- Re-run it any time you change code; it transfers only what changed (checksum).
- It deliberately **does not** send `config/touch_cal.json` (device-specific
  touchscreen calibration written on the Pi) — see step 6.
- Alternative bulk uploader: `./bulk_sync_to_pi.sh <host>` rsyncs all
  `*.py/*.yaml/*.csv` under `src/ dev/ config/` (excludes the mock drivers).
  Use `-n` for a dry run.

> Neither sync script copies `.venv/`, the `provisioning/deploy/` scripts, or the
> `.sqlite` database — those are handled explicitly below.

---

## Step 3c — Upload the reference database (on local)

The runtime database `src/colorimeter.sqlite` holds seed data the app needs —
the color-card ROI rectangles (`colorcard`), the device/user/testkit rows, and
the entry-screen defaults. It is **not** in Git (`.sqlite` is gitignored) and is
**not** in the sync list.

> **`src/db_DDL.sql` will build a runnable database** (all 10 tables, the
> `default_gui_vals` view, and seed rows) if you have no reference copy to hand:
> `sqlite3 src/colorimeter.sqlite < src/db_DDL.sql`. It is idempotent, so
> re-running it is safe. Prefer copying a working `colorimeter.sqlite` anyway —
> that is what carries this fleet's reference data. *(Before 2026-07-31 the DDL
> was incomplete and could not be used at all.)*

> 🔴 **On a unit that has already been used, this step destroys data.** The DB is
> not just seed rows once the instrument has run: it holds measurement results,
> the `exposure` calibration table, and the rows that index the HDF5 files in
> `captures/`. Copying the reference DB over it discards all of that and orphans
> the archives. On a re-entry, run the row-count check *first* and only recopy if
> `colorcard` is empty — [00_reentry.md § 1c](00_reentry.md#1c--is-the-database-real).
> If unsure, back up first:
> `cp ~/pi/Colorimeter/src/colorimeter.sqlite{,.bak-$(date +%F)}`

Copy a known-good copy onto the new unit:

```bash
# On local — from a desktop copy that has the seed data:
scp src/colorimeter.sqlite <host>:~/pi/Colorimeter/src/colorimeter.sqlite

# …or pull it straight from the reference unit if the desktop copy is stale:
# scp rpizero1:~/pi/Colorimeter/src/colorimeter.sqlite /tmp/ref.sqlite
# scp /tmp/ref.sqlite <host>:~/pi/Colorimeter/src/colorimeter.sqlite
```

Confirm it arrived intact (on the Pi):

```bash
# On the Pi:
python3 - <<PY
import sqlite3
c = sqlite3.connect("$HOME/pi/Colorimeter/src/colorimeter.sqlite")
for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
    print(f"  {t:12} rows={c.execute(f'select count(*) from {t}').fetchone()[0]}")
c.close()
PY
```

Expect `colorcard` to have rows (the ROI patches) and `device`/`defaults` to be
populated. An empty `colorcard` means the wrong/blank DB was copied.

---

## Step 3d — Upload the Pi-run scripts (on local)

The setup/verify scripts live in `provisioning/deploy/`. Push that exact set to
`~/pi/Colorimeter/provisioning/` on the Pi with the bundled helper (flattened
one level, which `make_venv.sh` relies on):

```bash
# On local, from ~/Projects/Colorimeter:
./push_pi_scripts.sh <host>            # add -n for a dry run
```

The two `ssh-*.md` guides are local-only symlinks under `docs/commissioning/`,
not part of this set, so they are never uploaded.

---

## Step 3e — Install system packages, then build the venv (on the Pi)

Heavy/hardware packages (numpy, OpenCV, h5py, picamera2, GPIO/SPI/I2C) are
installed **system-wide via apt** — building them from source on a Zero-class
board is slow and often runs out of memory. The venv then inherits them with
`--system-site-packages`.

```bash
# On the Pi:
cd ~/pi/Colorimeter
./provisioning/bootstrap_system.sh     # apt packages + hardware groups (no daemon)
./provisioning/make_venv.sh            # create .venv, verify key imports
```

`make_venv.sh` finishes by importing `numpy`, `cv2`, `h5py`, `yaml`, `spidev`,
`gpiozero`, `lgpio`, `smbus2`, `picamera2`, and `evdev` **from inside the venv**
and printing their versions. If any import fails, fix it before continuing — the
app will not start otherwise.

### Group membership (the `input` group)

`bootstrap_system.sh` also adds you to the **`input`** group if you are not
already in it. `src/kb_reader.py` reads the USB keyboard from `/dev/input/eventX`
via `evdev` rather than stdin — that is what lets it work under systemd (no TTY)
and over SSH (stdin is the remote terminal, not the local keyboard) — and those
nodes are group-`input`.

The Imager's first user is in `input` already, so this is usually a no-op; a
**hand-created account is not** (see [00_reentry.md § 4b](00_reentry.md)). Both
this and a missing `python3-evdev` fail *silently*: the app runs normally and the
keyboard is simply dead.

```bash
groups | tr ' ' '\n' | grep -x input    # → input
```

If the group was just added, **log out and back in** (or reboot) before it takes
effect — group membership is fixed at login.

### Interactive-login convenience (automatic)

`make_venv.sh` also appends a small, **idempotent** block to `~/.bashrc` so that
each interactive SSH login drops you straight into the project with the venv
already active:

```bash
# >>> colorimeter login env >>>
if [[ $- == *i* ]]; then
    _colorimeter_home="$HOME/pi/Colorimeter"
    if [[ -d "$_colorimeter_home" ]]; then
        cd "$_colorimeter_home" || true
        [[ -f "$_colorimeter_home/.venv/bin/activate" ]] && source "$_colorimeter_home/.venv/bin/activate"
    fi
    unset _colorimeter_home
fi
# <<< colorimeter login env <<<
```

- Guarded on the interactive flag and on the paths existing, so it is harmless
  before the venv is built (re-run `make_venv.sh` afterward — it will not add a
  duplicate block).
- **To disable:** delete the block from `~/.bashrc` (the two `>>>`/`<<<` marker
  lines and everything between).

> The boot service does **not** rely on any of this — `colorimeter.service`
> calls the venv interpreter by absolute path
> (`~/pi/Colorimeter/.venv/bin/python3`) and never sources `.bashrc`.

---

## What's next

- Optional SSH hardening / convenience → [04_ssh.md](04_ssh.md)
- Verify every peripheral and run the app → [05_testing.md](05_testing.md)
- The boot-launch service (`install_colorimeter_service.sh`) is set up in
  [05_testing.md](05_testing.md), after a manual smoke test confirms the app
  runs.
