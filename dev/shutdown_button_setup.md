# Shutdown Button — systemd Service Setup

## Overview

This guide sets up `shutdown_button_demo.py` as a systemd service so it runs
automatically at boot, even with no user logged in.  
It assumes you are running Python inside a **virtual environment** (venv).

---

## Prerequisites

| Item | Detail |
|------|--------|
| Script location | `/home/pi/shutdown_button_demo.py` (adjust if different) |
| Virtual environment | e.g. `/home/pi/myenv/` |
| Button wiring | GPIO26 → GND (internal pull-up used; no external resistor needed) |
| OS | Raspberry Pi OS (Bookworm or Bullseye), headless |

> **Virtual environment note:** Do **not** activate/deactivate the venv in the
> service file. Instead, point `ExecStart` directly at the venv's `python3`
> binary. This is the correct, systemd-friendly approach.

---

## Step 1 — Install RPi.GPIO inside the venv

SSH into the Pi and run:

```bash
source /home/pi/myenv/bin/activate
pip install RPi.GPIO
deactivate
```

---

## Step 2 — Place the script

Copy the script to a stable location (not inside a user's Downloads folder):

```bash
sudo cp shutdown_button_demo.py /usr/local/bin/shutdown_button.py
sudo chmod +x /usr/local/bin/shutdown_button.py
```

---

## Step 3 — Allow passwordless shutdown

The script calls `sudo shutdown`. Grant the `pi` user permission to run it
without a password by adding a sudoers rule:

```bash
sudo visudo -f /etc/sudoers.d/shutdown-button
```

Add this single line and save:

```
pi ALL=(ALL) NOPASSWD: /sbin/shutdown
```

---

## Step 4 — Create the service file

```bash
sudo nano /etc/systemd/system/shutdown-button.service
```

Paste the following, adjusting the venv path if yours differs:

```ini
[Unit]
Description=GPIO Shutdown Button
# Start after networking is up and multi-user targets are reached.
# This also ensures I2C, SPI, and camera kernel modules are loaded first,
# which matters once those peripherals are active.
After=multi-user.target

[Service]
Type=simple
User=pi

# Point directly at the venv interpreter — no 'source activate' needed.
ExecStart=/home/pi/myenv/bin/python3 /usr/local/bin/shutdown_button.py

# Restart automatically if the script crashes (but not on clean exit / shutdown).
Restart=on-failure
RestartSec=5

# Send stdout/stderr to the systemd journal.
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

---

## Step 5 — Enable and start the service

```bash
sudo systemctl daemon-reload
sudo systemctl enable shutdown-button.service
sudo systemctl start shutdown-button.service
```

Check that it started cleanly:

```bash
sudo systemctl status shutdown-button.service
```

You should see `Active: active (running)`.

---

## Viewing logs

```bash
# Last 50 lines
journalctl -u shutdown-button -n 50

# Live / follow mode
journalctl -u shutdown-button -f
```

---

## Testing without rebooting

With the service running, press and **hold** the button for 2 seconds.  
Watch the journal in a second SSH session (`journalctl -u shutdown-button -f`)
to see the teardown sequence log before the Pi halts.

---

## Future peripherals — what changes when they're added

When the camera, I2C, SPI, and ILI9341 display are active, you will need to:

1. **Fill in the stub functions** in `shutdown_button.py`:
   - `teardown_camera()` — stop picamera2 and close the camera object
   - `teardown_display()` — blank the ILI9341 and deinit its SPI bus
   - `teardown_spi()` — close any remaining SPI handles
   - `teardown_i2c()` — close smbus2 / CircuitPython I2C objects

2. **Update `After=`** in the service file if those peripherals have their own
   services (e.g. a camera streaming service), so the shutdown script starts
   *after* them and can therefore also stop them cleanly.

3. **Consider `ExecStop=`** — for a more robust design, you can move the
   teardown logic into an `ExecStop` handler so systemd itself invokes cleanup
   during `systemctl stop` or system shutdown, not just on button press.

---

## Quick-reference: useful commands

| Action | Command |
|--------|---------|
| Start service | `sudo systemctl start shutdown-button` |
| Stop service | `sudo systemctl stop shutdown-button` |
| Restart service | `sudo systemctl restart shutdown-button` |
| Disable autostart | `sudo systemctl disable shutdown-button` |
| View live logs | `journalctl -u shutdown-button -f` |
