# provisioning/ — build a Colorimeter Pi unit

This directory holds the **Pi-bound artifacts** for commissioning a new
colorimeter unit. The full step-by-step lives in the docs:

➡ **[docs/RPi_commissioning.md](../docs/RPi_commissioning.md)** — the master
commissioning guide (OS image → peripherals → firmware/venv → SSH → test →
calibration → troubleshooting).

## What's here

| Path | Runs on | What |
|---|---|---|
| `deploy/` | the **Pi** | The exact set of scripts uploaded to `~/pi/Colorimeter/provisioning/` and run there: `bootstrap_system.sh`, `make_venv.sh`, `verify_peripherals.sh`, `commissioning_status.sh`, `install_colorimeter_service.sh`, `uninstall_colorimeter_service.sh`, `install_gpio_init_service.sh`, and the `colorimeter-gpio-init.sh` it installs to `/usr/local/bin`. |
| `reference/` | — | Stock `config.txt` skeletons for reference only; **not** uploaded. The canonical cal8 `[all]` block is in docs step 2. |

> **Flatten note:** on the desktop the scripts sit at `provisioning/deploy/`, but
> on the Pi they must land **one** level under the project root
> (`~/pi/Colorimeter/provisioning/`) — `make_venv.sh` derives the project root as
> its own parent directory. `push_pi_scripts.sh` handles this mapping.

## Desktop-run helpers (stay at the repo root)

Development shares these, so they live at the repo root, not here:

| Script (repo root) | What |
|---|---|
| `sync_to_pi.sh` | Curated firmware push (the `sync_pi_files.txt` list) → Pi. |
| `bulk_sync_to_pi.sh` | Blanket push of every `*.py/*.yaml/*.csv` under `src/ dev/ config/`. |
| `push_pi_scripts.sh` | Upload `provisioning/deploy/` → `~/pi/Colorimeter/provisioning/`. |
| `sync_from_pi.sh` | Pi → desktop pull (development convenience). |

The prose docs stay under `docs/commissioning/` (linked above), including the two
local-only `ssh-*.md` guide symlinks.

## Typical order

1. Flash + configure the Pi — docs steps 1–2.
2. `./sync_to_pi.sh <host>` and copy the reference DB — docs step 3a–c.
3. `./push_pi_scripts.sh <host>` — uploads `deploy/` — docs step 3d.
4. On the Pi: `provisioning/bootstrap_system.sh`, then `provisioning/make_venv.sh` — docs step 3e.
5. On the Pi: `provisioning/verify_peripherals.sh`, `provisioning/install_gpio_init_service.sh`, `provisioning/install_colorimeter_service.sh` — docs step 5.

## Re-running these on an already-commissioned unit

Start with `commissioning_status.sh` — it reports which steps this unit has
actually had and names the step(s) to re-enter at:

```bash
./push_pi_scripts.sh <host>                              # on local
~/pi/Colorimeter/provisioning/commissioning_status.sh    # on the Pi
```

All the `deploy/` scripts are written to be re-run, with two caveats worth
knowing: `make_venv.sh` **reuses** an existing `.venv` (so it cannot repair a bad
or relocated one — `rm -rf .venv` first), and `verify_peripherals.sh` probes live
buses (so stop `colorimeter.service` first). The steps that are *not* safe to
repeat blindly are all outside this directory: the SD flash, the `config.txt`
edit, and the database copy.

➡ **[docs/commissioning/00_reentry.md](../docs/commissioning/00_reentry.md)** —
how to work out what state an unknown unit is in, where to rejoin the sequence,
the full repeat-safety classification, and what to fix after a username,
password, hostname, or app-directory change.
