# Project Setup & Maintenance

A dual-environment Python project: developed and tested on a desktop Linux machine,
deployed and run on a Raspberry Pi Zero.

---

## Project Initiation

### Desktop

Create the project directory and a virtual environment:

```bash
mkdir ~/path/to/project && cd ~/path/to/project
python3 -m venv venv/
source venv/bin/activate
pip install -r requirements.txt
```

### Raspberry Pi Zero

The Pi Zero (ARMv6) cannot compile many scientific or hardware packages from source.
Install those via `apt` first, then create the venv inheriting them:

*Pi:*
```bash
# On the Pi:
# 1. Install heavy/hardware packages system-wide
sudo apt install python3-numpy python3-rpi.gpio python3-gpiozero
sudo apt install python3-opencv

# 2. Create venv that can see system packages
python3 -m venv --system-site-packages venv/
source venv/bin/activate

# 3. Install remaining pure-Python dependencies normally
pip install requests pyyaml

# 4. Enable GPIO pins and buses
sudo raspi-config nonint do_i2c 0
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_serial_hw 0
```

> **Why `--system-site-packages`?**  The Pi Zero has 512 MB RAM and no ARMv6 wheels on PyPI.
> Inheriting `apt`-installed packages avoids multi-hour source compiles that often fail.
> The trade-off is reduced isolation — see Project Maintenance below for how to handle this.

---

## Project Configuration

### SSH Access (Passwordless)

Generate an Ed25519 key on the desktop and copy it to the Pi,
Then add a host alias forthe Pi to `~/.ssh/config`:

```bash
# On the Desktop:
ssh-keygen -t ed25519 -C "my-desktop"
ssh-copy-id -i ~/.ssh/id_ed25519.pub <user_name_on_pi>@<pi-ip>
sudo nano /.ssh/config

|Host rpi
|    HostName 192.168.1.42
|    User pi
|    IdentityFile ~/.ssh/id_ed25519
```

Connect with: `ssh rpi`

### Fix the Pi's IP Address

In your router's admin interface, create a **DHCP reservation** binding the Pi's MAC address
to a fixed IP. This is the most reliable approach — no Pi-side config required.

If router access is unavailable, set a static IP on the Pi itself in `/etc/dhcpcd.conf`:

```
interface wlan0
static ip_address=192.168.1.42/24
static routers=192.168.1.1
static domain_name_servers=192.168.1.1
```

Pick an address **outside** the router's DHCP pool range to avoid conflicts.

### File Sync

Two scripts in the project root handle syncing:

| Script | Direction |
|---|---|
| `sync_to_pi.sh` | Desktop → Pi |
| `sync_from_pi.sh` | Pi → Desktop |

Usage:

```bash
./sync_to_pi.sh rpi
./sync_from_pi.sh rpi
```

`sync_pi_files.txt` (also in the project root) controls exactly which files are transferred.
See Project Maintenance for how to keep it current.

---

## Project Maintenance

### Managing `sync_pi_files.txt`

This file is the single source of truth for what gets synced between desktop and Pi.
Only files explicitly listed here are ever transferred — nothing else is touched.

**Format:** one relative path per line; blank lines and `#` comments are ignored.

```
# source files
src/main.py
src/utils.py

# tests
test/test1.py
test/test2.py
```

**When to update it:**

- **New file added to the project** — add its relative path to the list before the next sync.
- **File moved or renamed** — update the path in the list; the old path on the remote will
  *not* be deleted automatically (rsync won't remove files not in the list).
- **File deleted from the project** — remove it from the list, then manually delete it on the
  remote: `ssh rpi "rm ~/path/to/project/<file>"`.

> Do not use wildcards (e.g. `src/*.py`). List each file explicitly so the sync set is always
> auditable and intentional.

### Tracking Dependencies

Because `--system-site-packages` is used on the Pi, `pip freeze` will not capture `apt`-installed
packages. Maintain two dependency records:

- **`requirements.txt`** — pure-Python packages installed via `pip` (works on both desktop and Pi).
- **`system_requirements.txt`** — `apt` packages required on the Pi, documented manually.

Example `system_requirements.txt`:

```
# Install on Pi Zero with: sudo apt install <package>
python3-numpy
python3-rpi.gpio
python3-gpiozero
```

When the Pi OS is updated (`sudo apt upgrade`), verify that system package versions haven't
changed in a way that affects the project, since the venv inherits them silently.
