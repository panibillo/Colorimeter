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

### Database looks empty / app errors on missing rows

- `colorcard` (ROI patches) must have rows. If it is empty you copied a blank or
  wrong `colorimeter.sqlite` — recopy the reference DB (step 3c). Do **not**
  rebuild from `src/db_DDL.sql` (incomplete).
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
