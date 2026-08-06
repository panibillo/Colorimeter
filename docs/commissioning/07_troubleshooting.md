# Step 7 — Troubleshooting index

**Status:** ✅ ready

Where to look when a step fails. Peripheral-level detail lives in the two
reference documents linked below; this page is the fast triage table plus the
issues specific to commissioning (venv, services, DB).

← [06_calibration.md](06_calibration.md) · Back to [master guide](../RPi_commissioning.md) · Re-entering? [00_reentry.md](00_reentry.md)

> **Not sure the unit was ever commissioned correctly?** This page assumes the
> steps were run in order. If the unit's history is uncertain — or a username,
> password, hostname, or directory changed part-way through — start from
> [00_reentry.md](00_reentry.md), which probes what state the unit is actually in
> before you debug symptoms.

---

## Reference documents

- **1-Wire temperature probe:** [docs/DS18B20_troubleshooting.md](../DS18B20_troubleshooting.md)
  — the definitive guide for a missing/silent DS18B20 (modules, overlay,
  `28-*` device, CRC, and the board-specific pull-up checks).
- **All buses, deep dive:** [edocs/rpi_configuration_guide_for_cal8.md](../../edocs/rpi_configuration_guide_for_cal8.md)
  — per-peripheral verification, the GPIO safe-state service, and its own
  troubleshooting tables (SPI0/SPI1/I2C/PWM).

---

## Fast triage

| Symptom | First check |
|---|---|
| Can't SSH after first boot | Wi-Fi country set in Imager? Try the router's DHCP list for the IP; `ping <hostname>.local`. |
| `/dev/spidev0.0` missing | `grep spi=on /boot/firmware/config.txt` present and uncommented; rebooted after edit? |
| `/dev/spidev1.0` missing | `grep spi1 /boot/firmware/config.txt` → `dtoverlay=spi1-1cs,cs0_pin=23` (no spaces). |
| `/dev/i2c-1` missing | `grep i2c_arm=on /boot/firmware/config.txt`. |
| `i2cdetect -y 1` shows nothing at `0x48` | 1651 powered (5V at J1-1)? VddIO jumper at 3V? J1 wiring? |
| No `28-*` under `/sys/bus/w1/devices/` | See the DS18B20 guide — usually physical (pull-up/solder/pin), not config. |
| GPIO18 shows `ALT4` | `cs0_pin=23` missing or has a space in `config.txt`; fix and reboot. |
| LED stuck on for ~10–15 s at boot | Expected until `colorimeter-gpio-init.service` runs; confirm the service is enabled (below). |
| Backlight stays off | GPIO13 / J1-9 wiring; 1651 LITE jumper soldered? `gpiozero` importing (needs `python3-lgpio` as its pin factory)? |
| Beeper silent / `import lgpio` fails | `sudo apt install python3-lgpio` (src/beeper.py imports `lgpio` directly); GPIO12 / MMBT3904 wiring. |
| Camera not found | `rpicam-hello --list-cameras`; `camera_auto_detect=1` in `config.txt`; ribbon seated. |
| Keyboard ignored (touch still works) | `python3 -c 'import evdev'` and `groups \| grep input` — needs `python3-evdev` **and** the `input` group; both fail silently (below). |

Query one pin at a time: `pinctrl get <n>` (`raspi-gpio` is not installed).

---

## Commissioning-specific issues

### venv import fails (`make_venv.sh`)

- Re-run `provisioning/bootstrap_system.sh` — a system apt package is missing.
- The venv **must** have been created with `--system-site-packages`, or
  apt-installed numpy/cv2/h5py/picamera2 are invisible inside it. `make_venv.sh`
  does this; if you created a venv by hand without the flag, delete `.venv` and
  re-run the script.
- Verify inside the venv:
  `~/pi/Colorimeter/.venv/bin/python3 -c "import h5py, cv2, yaml; print('ok')"`.

### Keyboard does nothing (app runs, touch works, keystrokes ignored)

`src/kb_reader.py` reads `/dev/input/eventX` directly via `evdev` — not stdin —
so it works under systemd and over SSH. It has two prerequisites, and **both
fail silently**: `KbReader.open()` returns `False`, the app carries on, and no
key is ever delivered.

```bash
# On the Pi — the two-line diagnosis:
python3 -c 'import evdev; print(evdev.list_devices())'
groups | tr ' ' '\n' | grep -x input
```

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: evdev` | package never installed | `sudo apt install -y python3-evdev` |
| imports, but list is `[]`, `grep -x input` prints nothing | user not in the `input` group | `sudo usermod -aG input "$(whoami)"`, then **log out and back in** |
| imports, in `input`, list still `[]` | keyboard genuinely absent | check the USB cable / OTG adapter; `lsusb`, `dmesg \| tail` |

`evdev.list_devices()` needs r/w on the nodes and silently skips ones it cannot
open, which is why a permissions problem looks identical to unplugged hardware —
always check `groups` first. Confirm from the app's own side in the log:

```bash
journalctl -u colorimeter -b --no-pager | grep KbReader
# healthy:  KbReader: selected <name> (/dev/input/eventN)
# broken:   KbReader.open: no keyboard device found — check: …
```

Group membership is fixed at login, so a `usermod` while the service is running
does nothing until the unit restarts — `sudo reboot` is the reliable move.
Both prerequisites are handled by `provisioning/bootstrap_system.sh`
(step [3e](03_upload_firmware_db_venv.md)) and checked by
`verify_peripherals.sh` and `commissioning_status.sh`.

### Database looks empty / app errors on missing rows

- Every setting lives in the `config` table now; the `defaults`, `device`,
  `users`, `testkit` and `testlot` tables and the `default_gui_vals` view are
  gone. `src/db_bootstrap.py` runs at every launch and re-seeds any parameter
  that has no active row, so this class of failure should repair itself. If it
  does not, the message names the parameter.
- `colorcard` (colour-card patch geometry) is *not* read at runtime — an empty
  `colorcard` is normal and is not the cause of a startup error. It is still a
  useful tell that you copied a blank DB.
- To rebuild a working schema + seed rows by hand:
  `sqlite3 src/colorimeter.sqlite < src/db_DDL.sql` then
  `sqlite3 src/colorimeter.sqlite < provisioning/config_defaults.sql` (both
  idempotent; neither overwrites a value already there). Recopying the
  reference DB (step 3c) is still preferred, for the calibration data.
- Validate: the schema-count snippet in [03_upload_firmware_db_venv.md](03_upload_firmware_db_venv.md#step-3c--upload-the-reference-database-on-local).

### App won't start under systemd but runs by hand

- `colorimeter.service` sets `WorkingDirectory=~/pi/Colorimeter` (project root,
  **not** `src/`) because `src/logging_manager.py` opens its log at the relative
  path `src/colorimeter.log`. Don't change the working directory.
- Check logs: `journalctl -u colorimeter -b --no-pager` (add `-f` to follow).
- `colorimeter.service` is ordered `After=colorimeter-gpio-init.service`; if the
  gpio-init unit failed, check it too: `systemctl status colorimeter-gpio-init`.

### Services not enabled at boot

```bash
systemctl is-enabled colorimeter.service colorimeter-gpio-init.service
# both should say "enabled"
```

Re-install the launch service with
`~/pi/Colorimeter/provisioning/install_colorimeter_service.sh` (idempotent).

---

## Recovery: Pi won't boot after a `config.txt` edit

The boot config is on the FAT32 firmware partition, readable on any OS. Mount
the SD card on another computer and restore the backup you made in step 2a:

```
cp /media/<you>/bootfs/config.txt.backup /media/<you>/bootfs/config.txt
```

Re-insert, boot, and re-apply the edit more carefully.

---

← [06_calibration.md](06_calibration.md) · Back to [master guide](../RPi_commissioning.md)
