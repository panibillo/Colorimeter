# Colorimeter — Raspberry Pi Commissioning

Master guide for building a **new colorimeter unit** from a blank SD card to a
headless, auto-starting instrument — configured the same way as the reference
unit `rpizero1`.

> **Status:** first draft. Development of the instrument is ongoing, so some
> sections (calibration in particular) are stubs that will be filled in as the
> corresponding software lands. Each companion page carries a status marker.

---

## Scope

This guide targets **one** hardware/software configuration:

| Item | Value |
|---|---|
| Board | Raspberry Pi **Zero 2 W** (RP3A0, quad-core Cortex-A53) |
| OS | Raspberry Pi OS, **64-bit**, based on Debian 13 **Trixie** |
| Boot mode | **Headless** (no monitor/keyboard; managed over SSH) |
| Mainboard | **cal8** (SPI display + touch + 1-Wire temp + 74HC595 LED driver + camera) |
| App user | `mgwwg` *(default for new/give-away units — [note](commissioning/01_os_image.md#step-1b--customise-the-os-the-headless-preset)). Scripts auto-detect whichever user actually runs them, so existing units (e.g. `bill` on rpizero1) need no changes.* |
| App directory (on Pi) | `~/pi/Colorimeter` |
| Entry point | `src/main.py`, auto-launched at boot by `colorimeter.service` |

Older boards (original Pi Zero W, the 5-LED `mainboard_C` stripboard) and 32-bit
/ Bullseye / Bookworm images are **out of scope** here — see
[Superseded documentation](#superseded-and-out-of-date-documentation) for how
the older material relates.

> **Why "cal8"?** The active lighting driver in the codebase is
> `src/lighting_cal8.py`, and it is the driver listed in `sync_pi_files.txt`.
> The cal8 mainboard drives its LEDs through a 74HC595 shift register on SPI1,
> not through direct GPIO pins like the `mainboard_C` test board.

---

## The commissioning sequence

Work through the companion pages in order. Each is self-contained but assumes
the previous ones are done.

| # | Step | Page | Status |
|---|------|------|--------|
| 0 | **Re-entry & refresh** — *not* a fresh unit? Start here | [00_reentry.md](commissioning/00_reentry.md) | ✅ ready |
| 1 | Flash the OS onto a new SD card (Imager headless preset, or the Desktop-wizard alt path) | [01_os_image.md](commissioning/01_os_image.md) | ✅ ready |
| 2 | Configure peripherals in `config.txt` (SPI0, I2C, 1-Wire, SPI1, PWM) | [02_peripherals.md](commissioning/02_peripherals.md) | ✅ ready |
| 3 | Upload firmware, database, and setup scripts; create directories + venv | [03_upload_firmware_db_venv.md](commissioning/03_upload_firmware_db_venv.md) | ✅ ready |
| 4 | *(Optional)* SSH keypair + colored-terminal login helper | [04_ssh.md](commissioning/04_ssh.md) | ✅ ready |
| 5 | Test — verify each peripheral, then a full app smoke test | [05_testing.md](commissioning/05_testing.md) | 🟡 mostly ready |
| 6 | Calibration — touchscreen, focus, exposure, color card | [06_calibration.md](commissioning/06_calibration.md) | 🚧 partial (dev ongoing) |
| 7 | Troubleshooting index | [07_troubleshooting.md](commissioning/07_troubleshooting.md) | ✅ ready |

**Status legend:** ✅ ready · 🟡 usable, some gaps · 🚧 stub, awaiting software.

Steps 1–6 describe a **linear first build**. If the unit in front of you is
partly commissioned, of uncertain history, or was disrupted by a username /
password / hostname change, go to
**[00_reentry.md](commissioning/00_reentry.md)** instead — it tells you how to
work out what state the unit is actually in, where to rejoin the sequence, and
which steps are safe to repeat.

---

## What "commissioning" installs, at a glance

```
New SD card
  └─ Raspberry Pi OS 64-bit (Trixie), headless preset
     ├─ /boot/firmware/config.txt   ← peripheral overlays (step 2)
     ├─ ~/pi/Colorimeter/           ← app dir (step 3)
     │    ├─ src/…                   ← firmware (rsync'd from desktop)
     │    ├─ config/…                ← focus_cal.json, touch_cal.json (per-unit calibration)
     │    ├─ src/colorimeter.sqlite  ← reference DB, incl. the `config` table (copied, not built from DDL)
     │    ├─ provisioning/*.sh       ← setup/verify scripts (uploaded)
     │    └─ .venv/                  ← --system-site-packages venv (step 3)
     └─ systemd services
          ├─ colorimeter-gpio-init.service  ← clears 74HC595 at boot (LEDs off)
          └─ colorimeter.service            ← launches src/main.py at boot
```

Two systemd units cooperate at boot: `colorimeter-gpio-init.service` runs first
and forces the LED shift register to a known-off state; `colorimeter.service`
then starts the application (it is ordered `After=` the gpio-init unit).

---

## Conventions used throughout

- **Local** = your desktop/laptop (`~/Projects/Colorimeter`). **Pi** = the unit
  being commissioned (`~/pi/Colorimeter`).
- Commands prefixed with a comment `# On the Pi:` or `# On local:` state where to
  run them. When unmarked, context makes it clear.
- Replace `<host>` with the Pi's SSH alias or IP (e.g. `rpizero1`), and
  `<hostname>` with the new unit's chosen hostname.
- GPIO numbers are **BCM** unless a "physical pin N" is named.
- Query pin state with **`pinctrl get <n>`**, one pin at a time (`raspi-gpio` is
  not installed on this OS).

---

## Peripheral map (cal8 mainboard)

Quick reference; wiring detail lives in the electronics docs under `edocs/`.

| Function | Bus / pin | Device | Verify with |
|---|---|---|---|
| Display | SPI0 (CE0=GPIO8, MOSI=GPIO10, MISO=GPIO9, SCLK=GPIO11) | ILI9341 (Adafruit 1651) | `/dev/spidev0.0` |
| Touch | I2C bus 1 (SDA=GPIO2, SCL=GPIO3) | TSC2007 @ `0x48` | `i2cdetect -y 1` |
| Temp probe | 1-Wire on **GPIO16** (phys 36) | DS18B20 (`28-*`) | `/sys/bus/w1/devices/` |
| LED driver | SPI1 (MOSI=GPIO20, SCLK=GPIO21), RCLK=GPIO24 | 74HC595 shift register | `/dev/spidev1.0` |
| Backlight | GPIO13 (on/off via `gpiozero`) | 1651 LITE | `/dev/gpiochip0`, backlight toggles |
| Buzzer | GPIO12 (PWM via `lgpio`) | magnetic buzzer (`src/beeper.py`) | audible |
| Camera | CSI | IMX219 (Cam Module 2) / IMX708 (Module 3) | `rpicam-hello --list-cameras` |
| Keyboard | USB (no `config.txt` entry) | any USB keyboard, read via `evdev` (`src/kb_reader.py`) | `python3 -c 'import evdev; print(evdev.list_devices())'` — needs `python3-evdev` **and** the `input` group |

---

## Automation

Low-risk, repeatable steps are provided as scripts. The **Pi-run** set lives in
`provisioning/deploy/` in the repo and is uploaded to
`~/pi/Colorimeter/provisioning/` on the Pi by `push_pi_scripts.sh`:

| Script (in `provisioning/deploy/`) | Does | Repeat? |
|---|---|---|
| `bootstrap_system.sh` | Install apt system packages (`gpiozero`/`lgpio`/`spidev`/`smbus2`/`evdev`/camera/…) and add the user to the hardware groups (incl. `input`); **no daemon** | 🟢 |
| `make_venv.sh` | Create `.venv` (`--system-site-packages`), verify imports, add the `~/.bashrc` login helper | 🟡 reuses an existing `.venv` |
| `verify_peripherals.sh` | Check SPI0/I2C/1-Wire/SPI1/GPIO/camera/keyboard after reboot | 🟡 stop the app first |
| `commissioning_status.sh` | Report which commissioning steps this unit has had, and where to re-enter | 🟢 |
| `install_colorimeter_service.sh` | Install + enable the boot-launch service | 🟢 |
| `uninstall_colorimeter_service.sh` | Remove the boot-launch service | 🟢 |
| `install_gpio_init_service.sh` | Install + enable the boot-time LED safe-state service | 🟢 |
| `colorimeter-gpio-init.sh` | The safe-state script itself (installed to `/usr/local/bin`) | — |

🟢 safe to re-run any time · 🟡 re-run with a caveat. Full classification of
*every* commissioning step — including the three that are **not** safe to repeat
blindly (SD flash, the `config.txt` edit, the database copy) — is in
[00_reentry.md, Part 3](commissioning/00_reentry.md#part-3--repeatability-reference).

**Desktop-run** helpers stay at the repo root: `sync_to_pi.sh` (curated firmware
push), `bulk_sync_to_pi.sh` (blanket `src/dev/config` push), `push_pi_scripts.sh`
(upload the `provisioning/deploy/` set), and `sync_from_pi.sh` (Pi→desktop pull).

The **`/boot/firmware/config.txt` edit is deliberately *not* scripted** — a bad
`config.txt` can stop the Pi from booting, so step 2 walks through it manually
with a backup.

There is intentionally **no `seed_db.sh`**. Since 2026-07-31 `src/db_DDL.sql`
*can* build a complete, seeded, runnable database in one command — but
commissioning still copies a working `colorimeter.sqlite` so the new unit
starts from the same reference data as the others; see step 3.

---

## Superseded and out-of-date documentation

Findings from the existing docs, recorded here so they can be corrected or
retired deliberately. **None of these files have been edited by this guide.**

> ⚠ **Backup note:** the commissioning docs under `docs/` and the
> `provisioning/` scripts + config skeletons **are** tracked in Git now, but
> most of `edocs/` is not. Commit anything under `edocs/` you care about
> *before* editing or replacing it.

| File | Finding | Recommendation |
|---|---|---|
| `edocs/rpi_configuration_guide_for_cal8.md` | Excellent deep reference for cal8 peripherals and the `colorimeter-gpio-init` service, but written for the **original Zero W / Bookworm**; carries Bullseye/Bookworm branching this guide drops. Also its Step 7 adds `dtoverlay=spi0-1cs` (to free GPIO7 for LED channel 9) which the committed cal8 config file omits. ⚠ Its PWM sections (11e, install step) install **`pigpio`/`pigpiod`** for GPIO12/13 — **obsolete**: the code now uses `gpiozero` (backlight) and `lgpio` (beeper) with **no daemon**, per [CODING_GUIDELINES.md](../CODING_GUIDELINES.md). | Keep as the authoritative reference for the **gpio-init service** (step 5 links to it), but **ignore its pigpio/pigpiod steps**. Reconcile the `spi0-1cs` discrepancy on real hardware (step 2). |
| `provisioning/reference/Rpi-zero-W-2-for-mainboard_C-config.txt` | A **different board** (5-LED stripboard, `spi=off`, direct-GPIO LEDs), not the colorimeter. | Keep only as the stock Zero 2 W `config.txt` skeleton. Not the cal8 config. |
| `provisioning/reference/Rpi-zero-W-for-main_board_cal8-config.txt` | The cal8 `[all]` block, but for the **original Zero W** and missing `spi0-1cs`. | Basis for step 2's canonical block; verify `spi0-1cs`. |
| `src/db_DDL.sql` | ✅ **Fixed 2026-07-31.** Was incomplete: 6 tables against the live DB's 9, and every seed `INSERT` plus the `default_gui_vals` view sat inside an unterminated `/* … */`, so a DB built from it could not run the app. Now defines all 10 tables (incl. the new `roi`), the view, and the seed rows, and is idempotent on re-run. Verified: the app boots on a DB built from it alone. The redundant `src/db_manager.py:TABLE_DEFINITIONS` string has been removed. | Usable as the schema source. `src/db_DDL_reset.sql` is the separate destructive drop script. Commissioning still copies a working `colorimeter.sqlite` (step 3) to carry reference data across units. |
| `project_setup.md` (repo root) | Older dual-environment notes assuming Pi Zero **v1** / ARMv6, `raspi-config nonint do_*`, and `/etc/dhcpcd.conf` static IP. | Superseded by this guide for the Zero 2 W / Trixie target. Retain for the `sync_pi_files.txt` maintenance notes. |

---

## Related documents

- [edocs/rpi_configuration_guide_for_cal8.md](../edocs/rpi_configuration_guide_for_cal8.md) — step-by-step peripheral setup + gpio-init service (deep reference)
- [docs/DS18B20_troubleshooting.md](DS18B20_troubleshooting.md) — 1-Wire temperature probe diagnostics
- [docs/commissioning/ssh-key-setup-guide.md](commissioning/ssh-key-setup-guide.md) — SSH keypair setup *(local-only symlink)*
- [docs/commissioning/ssh-terminal-color.md](commissioning/ssh-terminal-color.md) — colored SSH terminal helper *(local-only symlink)*
- [ARCHITECTURE.md](../ARCHITECTURE.md), [PROJECT_OVERVIEW.md](../PROJECT_OVERVIEW.md) — how the app is structured and why
