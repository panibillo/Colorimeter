# Step 5 — Test

**Status:** 🟡 mostly ready

Goal: prove each peripheral works, install the two systemd services, run the
application by hand, then confirm it auto-starts at boot. Do these in order —
stop and fix anything that fails before moving on.

← [04_ssh.md](04_ssh.md) · Back to [master guide](../RPi_commissioning.md) · Next → [06_calibration.md](06_calibration.md)

---

## 5a — Verify peripherals (scripted)

Run the bundled checker on the Pi. It confirms SPI0, SPI1, I2C (+ the TSC2007 at
`0x48`), 1-Wire (+ a `28-*` probe and a temperature read), the pigpio daemon,
and the camera, printing a ✓/✗ line for each.

```bash
# On the Pi:
~/pi/Colorimeter/commissioning/verify_peripherals.sh
```

If any line is ✗, jump to [07_troubleshooting.md](07_troubleshooting.md) or the
[cal8 reference guide](../../edocs/rpi_configuration_guide_for_cal8.md) for that
bus, fix it, and re-run. The script is read-only and safe to run repeatedly.

Manual spot checks it wraps (run individually if you want detail):

```bash
ls /dev/spidev0.0 /dev/spidev1.0 /dev/i2c-1       # buses present
sudo i2cdetect -y 1                                # touch at 0x48
cat /sys/bus/w1/devices/28-*/w1_slave              # probe temperature (…t=NNNNN)
pinctrl get 20                                     # SPI1 MOSI → ALT-func (not input)
systemctl is-active pigpiod                        # → active
rpicam-hello --list-cameras                        # camera detected
```

---

## 5b — Boot-time LED safe-state service

On the cal8 board the LEDs hang off the 74HC595 shift register, which can latch
a stray bit **on** during boot. `colorimeter-gpio-init.service` clears it after
boot (writes `0x00`, pulses RCLK) and forces the direct-control pins low. The
application service is ordered to start **after** it.

Create it once using the full recipe in the cal8 reference guide:

➡ **[edocs/rpi_configuration_guide_for_cal8.md — Step 12](../../edocs/rpi_configuration_guide_for_cal8.md)**
(creates `/usr/local/bin/colorimeter-gpio-init.sh` and the matching
`colorimeter-gpio-init.service`, then enables it).

Confirm it is active:

```bash
systemctl status colorimeter-gpio-init.service    # → active (exited), SUCCESS
```

> A short (~10–15 s) LED-on flash during boot, until this service runs, is
> expected with the software-only fix. See the guide's note on the hardware fix
> for the next PCB revision.
>
> *Candidate for automation:* this service is not yet captured as a committed
> script under `commissioning/` — it is created inline from the guide. A future
> `install_gpio_init_service.sh` would close that gap.

---

## 5c — Run the application by hand (smoke test)

Before wiring it into boot, confirm `main.py` starts on real hardware:

```bash
# On the Pi:
cd ~/pi/Colorimeter
.venv/bin/python3 src/main.py
```

Expect: the ILI9341 shows the entry screen, the backlight is on, touch responds,
and there are no traceback/import errors in the console. The DB line
(`… db_manager.py using db: …/src/colorimeter.sqlite`) should point at the DB
you copied in step 3c. Exit with **Ctrl-C** (graceful shutdown).

If it fails on imports, the venv is the usual cause — see
[07_troubleshooting.md](07_troubleshooting.md#venv-import-fails-make_venvsh).

---

## 5d — Install the auto-start service

Once the hand-run works, install the boot-launch unit (idempotent — safe to
re-run after code changes):

```bash
# On the Pi:
~/pi/Colorimeter/commissioning/install_colorimeter_service.sh
```

This writes `/etc/systemd/system/colorimeter.service` (User=*whoever runs the
script* — e.g. `mgwwg` on a new unit, `bill` on rpizero1 — auto-detected via
`whoami`, not hardcoded; `WorkingDirectory=~/pi/Colorimeter`, launching via the
venv interpreter),
`daemon-reload`s, enables, and starts it. Watch it:

```bash
systemctl status colorimeter --no-pager
journalctl -u colorimeter -f          # live logs; Ctrl-C to stop watching
```

To undo: `~/pi/Colorimeter/commissioning/uninstall_colorimeter_service.sh`
(leaves the gpio-init service and all files untouched).

---

## 5e — Full reboot test (the real proof)

```bash
# On the Pi:
sudo reboot
```

After it comes back (~30–60 s), from a fresh SSH session:

```bash
systemctl is-enabled colorimeter.service colorimeter-gpio-init.service   # both "enabled"
systemctl status colorimeter --no-pager                                  # active (running)
```

And look at the instrument itself: the display should reach the entry screen on
its own, with **no LED stuck on** after the initial boot flash, and touch
working. That is a commissioned unit.

---

Next → [06_calibration.md](06_calibration.md) — touchscreen, focus, exposure,
and measurement calibration (partly awaiting software).
