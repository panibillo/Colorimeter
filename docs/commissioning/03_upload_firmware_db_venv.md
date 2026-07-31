# Step 3 — Upload firmware, database, scripts; build directories + venv

**Status:** ✅ ready

Goal: put the application code (**firmware**), the **reference database**, the
**config**, and the **commissioning scripts** onto the Pi under
`~/pi/Colorimeter`, then create the Python virtual environment the app runs in.

Some commands run **on local** (the desktop, source of truth for the code) and
some **on the Pi**. Each block is marked.

← [02_peripherals.md](02_peripherals.md) · Back to [master guide](../RPi_commissioning.md) · Next → [04_ssh.md](04_ssh.md)

---

## Directory layout produced

```
~/pi/Colorimeter/            (on the Pi; mirrors ~/Projects/Colorimeter on local)
├── src/                     firmware — the application
│   └── colorimeter.sqlite   reference database (copied, step 3c)
├── config/                  instrument.yaml, palette.yaml
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

> **Do not build it from `src/db_DDL.sql`.** That DDL is incomplete — it defines
> 6 of the 9 live tables (no `colorcard`, `exposure`, or `data`) and carries no
> seed rows. The authoritative database is a working `colorimeter.sqlite`.

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
./provisioning/bootstrap_system.sh     # apt packages (no daemon)
./provisioning/make_venv.sh            # create .venv, verify key imports
```

`make_venv.sh` finishes by importing `numpy`, `cv2`, `h5py`, `yaml`, `spidev`,
`gpiozero`, `lgpio`, `smbus2`, and `picamera2` **from inside the venv** and
printing their versions. If any import fails, fix it before continuing — the app
will not start otherwise.

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
