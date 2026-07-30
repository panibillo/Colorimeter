# RPi Zero 2W Configuration Guide
## Colorimeter Prototype — Headless SSH Setup
## Configuring SPI0, SPI1, I2C, 1-Wire, and Hardware PWM

---

## Before You Begin

You will be editing system files over SSH. A typo in config.txt can prevent
the RPi from booting. Two precautions:

- Keep a second SSH session open while editing, so if you accidentally
  break the first session you are not locked out.
- Know how to recover: if the RPi fails to boot after a config change,
  mount the SD card on another computer and revert the change.
  The config file is on the FAT32 boot partition, readable on any OS.

---

## Step 1 — Connect and confirm you have the right machine

```bash
ssh pi@<your-rpi-ip>
```

Confirm hostname and OS version:

```bash
hostname
uname -a
cat /etc/os-release | grep PRETTY
```

Confirm this is a Zero 2W (RP3A0 SoC):

```bash
cat /proc/cpuinfo | grep Model
```

Expected output contains: `Raspberry Pi Zero 2`

---

## Step 2 — Locate the correct config file

Raspberry Pi OS Bookworm (Debian 12, released late 2023) moved the boot
config file. Older releases used `/boot/config.txt`. Check which you have:

```bash
# Check OS version
cat /etc/debian_version
```

- **Debian 12 (Bookworm) or later:** use `/boot/firmware/config.txt`
- **Debian 11 (Bullseye) or earlier:** use `/boot/config.txt`

Confirm the file exists at the expected path:

```bash
# For Bookworm:
ls -l /boot/firmware/config.txt

# For Bullseye:
ls -l /boot/config.txt
```

The rest of this guide uses `/boot/firmware/config.txt`.
Substitute `/boot/config.txt` if you are on Bullseye.

---

## Step 3 — Examine the existing configuration

Read the full current file before changing anything:

```bash
cat /boot/firmware/config.txt
```

Look for any lines already present that touch the peripherals you need.
Common pre-existing entries and what to do with each:

```bash
# Check for existing SPI, I2C, 1-Wire, overlay entries:
grep -n "spi\|i2c\|w1\|dtoverlay\|dtparam\|pwm" /boot/firmware/config.txt
```

**What you may find:**

| Pre-existing line | Action |
|---|---|
| `dtparam=spi=on` | Already correct for SPI0. Leave it. |
| `dtparam=i2c_arm=on` | Already correct. Leave it. |
| `dtparam=i2c_arm=off` | Must change to `on`. |
| `dtoverlay=w1-gpio` (no gpiopin) | Uses default GPIO4. Must add `,gpiopin=16`. |
| `dtoverlay=w1-gpio,gpiopin=4` | Wrong pin. Must change to `gpiopin=16`. |
| `dtoverlay=spi1-1cs` (no cs0_pin) | Will claim GPIO18 as CE0. Must add `,cs0_pin=23`. |
| `enable_uart=1` | Fine — does not conflict. Leave it. |
| `dtoverlay=disable-bt` | Fine — does not conflict. Leave it. |

Also check for anything that configures GPIO18 explicitly, as that pin
has special routing in this design:

```bash
grep -n "gpio18\|GPIO18\|18" /boot/firmware/config.txt
```

---

## Step 4 — Check currently loaded overlays (running system)

Before editing the file, check what is actually active right now:

```bash
# List loaded overlays:
dtoverlay -l

# Check which SPI devices are present:
ls /dev/spi*

# Check which I2C buses are present:
ls /dev/i2c*

# Check if 1-Wire is already active:
ls /sys/bus/w1/devices/
```

If `/dev/spidev0.0` exists, SPI0 is already enabled.
If `/dev/i2c-1` exists, I2C bus 1 is already enabled.
If `/sys/bus/w1/devices/` contains entries, 1-Wire is active —
check whether it is on the right pin (GPIO16).

Check the 1-Wire pin if it is already active:

```bash
dtoverlay -l | grep w1
# Then check the param:
dtoverlay -h w1-gpio 2>/dev/null | head -5
```

---

## Step 5 — Check for conflicts with SPI1 and GPIO18

SPI1 requires special care because the default overlay claims GPIO18.
Check whether SPI1 is already enabled and how:

```bash
# Look for spi1 in config:
grep -n "spi1" /boot/firmware/config.txt
# expected output: 72:dtoverlay=spi1-1cs,cs0_pin=23

# Check if spi1 devices exist:
ls /dev/spidev1*
# expected output: No such file or directory (It will appear after reboot)
```

Also confirm GPIO18 is not already being driven by something:

```bash
# Check GPIO18 current state (use raspi-gpio instead of pinctrl on older OS):
pinctrl get 18       # if using Raspberry Pi OS Bookworm
# expected output: 18: ip    -- | lo // GPIO18 = input
```

Expected output when GPIO18 is free: `GPIO 18: level=0 fsel=0 func=INPUT`
If it shows `func=ALT5` that means SPI1 is already claiming it.

---

## Step 6 — Make a backup before editing

```bash
sudo cp /boot/firmware/config.txt /boot/firmware/config.txt.backup
```

Verify the backup:

```bash
diff /boot/firmware/config.txt /boot/firmware/config.txt.backup
# Should produce no output (files are identical)
```

---

## Step 7 — Edit the config file

```bash
sudo nano /boot/firmware/config.txt
```

Navigate to the end of the file (Ctrl+End in nano) and add the following
block. 

- Do not duplicate any lines already present from Step 3.
- If a line already exists correctly, leave it where it is.
- Do not add spaces.  e.g. 'y=6,z=7', not 'y = 6, z = 7'

```
# ── Colorimeter prototype configuration ──────────────────────
# SPI0: ILI9341 display (MOSI=GPIO10, MISO=GPIO9, SCLK=GPIO11, CE0=GPIO8)
dtparam=spi=on

# I2C bus 1: TSC2007 touch controller (SDA=GPIO2, SCL=GPIO3)
dtparam=i2c_arm=on

# 1-Wire: GPIO16 (non-default pin; default would be GPIO4)
# gpiopin= takes the BCM number (16), NOT the physical pin number (36)
dtoverlay=w1-gpio,gpiopin=16

# SPI1: 74HC595 shift register (MOSI=GPIO20, SCLK=GPIO21)
# cs0_pin=23 redirects CE0 away from GPIO18, which is used for JP2/J3 routing.
# The 74HC595 does not use hardware CE; RCLK is driven manually by GPIO24.
dtoverlay=spi1-1cs,cs0_pin=23

# SPI0: Do not let spi0 claim GPIO7 claim 1cs: GPIO7 controls a LED (royal_blue, ch 1)
dtoverlay=spi0-1cs
# ─────────────────────────────────────────────────────────────
```

Save and exit: **Ctrl+O**, Enter, **Ctrl+X**

---

## Step 8 — Verify the edit looks correct before rebooting

```bash
# View the lines you just added:
tail -20 /boot/firmware/config.txt

# Run a quick sanity check — these should each appear exactly once:
grep "dtparam=spi=on"            /boot/firmware/config.txt | wc -l
grep "dtparam=i2c_arm=on"        /boot/firmware/config.txt | wc -l
grep "dtoverlay=w1-gpio"         /boot/firmware/config.txt | wc -l
grep "dtoverlay=spi1"            /boot/firmware/config.txt | wc -l
```

Each command should print `1`. If any prints `2` or more, you have a
duplicate — edit the file again and remove the older copy.

Also confirm the 1-Wire line has the correct BCM pin number:

```bash
grep "w1-gpio" /boot/firmware/config.txt
```

Expected: `dtoverlay=w1-gpio,gpiopin=16`
Not: `gpiopin=4`, `gpiopin=7`, or `gpiopin=36`

---

## Step 9 — Python libraries and virtual environment

### Step 9a Install required Python libraries (before rebooting)
While still connected, ensure the libraries needed for this design are
installed. Do this before the reboot so you can test immediately after.
The libraries covered here are installed via apt to get architecture-optimised builds for the Pi.

- gpiozero — general GPIO and backlight on/off, GPIO13 (`src/arbiter.py`)
- lgpio — beeper PWM on GPIO12 (`src/beeper.py`); also gpiozero's pin factory
- spidev — SPI access from Python (usually pre-installed)
- smbus2 — I2C access for TSC2007
- i2c-tools —

> GPIO policy (see `CODING_GUIDELINES.md`): the app uses **gpiozero** for
> general GPIO / backlight and **lgpio** for the beeper's PWM. It does **not**
> use `pigpio`/`pigpiod` or `RPi.GPIO` — there is deliberately **no background
> daemon**. `python3-lgpio` supplies both the `lgpio` module and gpiozero's
> default pin factory on Bookworm/Trixie.

```bash
# Update package list:
sudo apt update
sudo apt upgrade

# Python system packages

sudo apt install -y \
    python3-full \
    python3-numpy \
    python3-gpiozero \
    python3-lgpio \
    python3-spidev \
    python3-smbus2 \
    i2c-tools

# No daemon to enable — backlight (gpiozero) and beeper (lgpio) are daemonless.

# Sanity checks:

python3 -c "import spidev;   print('spidev ok')"
python3 -c "import gpiozero; print('gpiozero ok')"
python3 -c "import lgpio;    print('lgpio ok')"
python3 -c "import smbus2;   print('smbus2 ok')"
```

If any import fails, install with **BUT FIRST DOUBLE CHECK THAT THIS IS OK!!**:

```bash
pip3 install <packagename> --break-system-packages
```

### Step 9b - Add camera and image processing packages 

These are large downloads:
- picamera2 — operate the Pi Camera modules
- opencv — manipulate image files

```bash
sudo apt install -y \
    python3-picamera2 \
    python3-opencv
```

### Step 9c - Create the python virual environment

The above libraries were installed with apt and were installed in the system Python.
But we still want a virtual environment for other packages we may need.

Use the --system-site-packages flag to give the venv access to all apt-installed
packages above without copying them, avoiding long pip compilation times.

First check to see if it's already created

```bash
cd ~/pi/Colorimeter
ls -a
```

Look for "venv" or ".venv".  If not there, then create it: 

```bash
cd ~/pi/Colorimeter
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
```

Confirm key packages are visible inside the venv:
```bash
python3 -c "import spidev;    print('spidev ok')"
python3 -c "import gpiozero;  print('gpiozero ok')"
python3 -c "import lgpio;     print('lgpio ok')"
python3 -c "import smbus2;    print('smbus2 ok')"
python3 -c "import numpy;     print('numpy ok')"
```

To activate the venv automatically on each SSH login:

```
echo 'source ~/pi/Colorimeter/.venv/bin/activate' >> ~/.bashrc
```

Note: when running scripts at boot via systemd, call the venv Python
directly by path rather than relying on shell activation:

```bash
/home/bill/pi/Colorimeter/.venv/bin/python3 your_script.py
```
---

## Step 10 — Reboot

```bash
sudo reboot
```

Wait 30–60 seconds for the Zero 2W to complete its boot, then reconnect:

```bash
ssh pi@<your-rpi-ip>
```

---

## Step 11 — Verify each peripheral after reboot

Work through these checks in order. Stop and investigate if any check fails
before proceeding to the next.

### 11a — SPI0 (ILI9341 display)

```bash
ls -l /dev/spidev0.*
```

Expected: `/dev/spidev0.0` present (CE0 for display).
`/dev/spidev0.1` may also appear (CE1) — that is normal.

```bash
# Confirm GPIO8 (CE0) is configured for SPI:
pinctrl get 8 9 10 11
```

Expected: all four show `func=ALT0` (SPI0 function).

### 11b — I2C bus 1 (TSC2007)

```bash
ls -l /dev/i2c-1
```

Expected: `/dev/i2c-1` present.

```bash
# Scan for devices on I2C bus 1:
sudo i2cdetect -y 1
```

Expected: a device at address **0x48** (TSC2007).
If 0x48 is absent: check J1 wiring, confirm 1651 is powered (5V at J1-Pin1),
confirm VddIO jumper is set to 3V on the shield.  
Measure 3.3V on the VddIO jumper.

### 11c — 1-Wire (temperature probe)

```bash
# Check the overlay loaded with correct pin:
dtoverlay -l | grep w1
```

```bash
# Check for devices (probe must be connected):
ls /sys/bus/w1/devices/
```

Expected: a directory named `28-xxxxxxxxxxxx` (DS18B20 family).
If empty: check J2 wiring, confirm 4.7kΩ pull-up is present on PCB,
confirm probe is connected and powered.

```bash
# Read temperature (substitute actual device ID):
cat /sys/bus/w1/devices/28-*/w1_slave
```

Expected output (last line): `... t=23500` (temperature in millidegrees C).

### 11d — SPI1 (74HC595)

```bash
ls -l /dev/spidev1.*
```

Expected: `/dev/spidev1.0` present.

```bash
# Confirm GPIO20, GPIO21 are in SPI1 function:
pinctrl get 20 21
```

Expected: both show `func=ALT4` (SPI1 function on Zero 2W).

```bash
# Confirm GPIO18 is NOT claimed by SPI1 (it should be free):
pinctrl get 18
```

Expected: `func=INPUT` or `func=OUTPUT` — anything except `func=ALT4`.
If GPIO18 shows ALT4, the cs0_pin remapping did not work.
Check the config.txt line carefully — it must be `cs0_pin=23` with no spaces.

```bash
# Confirm GPIO23 is now the remapped CE0:
pinctrl get 23
```

Expected: `func=ALT4` — GPIO23 is now SPI1 CE0.
Note: in software you will use `spi1.no_cs = True` anyway, so this
GPIO23 CE0 assignment is mainly to keep it away from GPIO18.

### 11e — Backlight (gpiozero) and beeper (lgpio)

There is **no PWM daemon**. The backlight (GPIO13) is driven on/off with
`gpiozero` (as `src/arbiter.py` does), and the beeper (GPIO12) uses `lgpio`
software PWM (as `src/beeper.py` does). Nothing to enable or start.

Quick functional test — toggle the backlight:
```bash
python3 - << 'EOF'
import time
from gpiozero import DigitalOutputDevice

# Backlight on GPIO13 (matches src/arbiter.py — on/off, not dimming)
bl = DigitalOutputDevice(13)
bl.on();  print("Backlight ON");  time.sleep(2)
bl.off(); print("Backlight OFF")
bl.close()
EOF
```

If the backlight visibly changes on the 1651 display, GPIO13 is working. (For
dimming rather than on/off, `gpiozero.PWMOutputDevice(13)` gives software PWM —
see `CODING_GUIDELINES.md` Rule 5; the app currently uses on/off.)

Quick functional test — chirp the beeper:
```bash
python3 - << 'EOF'
import lgpio, time

PIN, CHIP, CARRIER = 12, 0, 10_000   # GPIO12, /dev/gpiochip0, 10 kHz (lgpio max)
h = lgpio.gpiochip_open(CHIP)
lgpio.gpio_claim_output(h, PIN)
lgpio.tx_pwm(h, PIN, CARRIER, 50.0)  # 50% duty → audible carrier
time.sleep(0.3)
lgpio.tx_pwm(h, PIN, CARRIER, 0.0)   # transistor fully off
lgpio.gpiochip_close(h)
print("Beeper chirped")
EOF
```

A short tone confirms GPIO12 / the MMBT3904 / beeper path is working.

---

## Step 12 — Boot-time GPIO safe state service

### Background

At boot, before any application code runs, the SPI1 kernel driver initialises
GPIO20 (MOSI) and GPIO21 (SCLK). During this initialisation the 74HC595 shift
register clocks in whatever it sees on SER, potentially latching a spurious
bit and turning on an LED. On this prototype, LED channel 2 lights at boot
and stays on until application code calls led_control.init().

The fix is a systemd service that runs after boot is complete, writes 0x00
to the shift register via SPI1, and pulses RCLK to latch all outputs low.

Note: the LED will remain on for approximately 10-15 seconds during boot
while the system reaches multi-user.target and the service executes.
This is a known limitation of the software-only fix. A hardware fix
(connecting 74HC595 pin 10 /SRCLR to a GPIO with a pull-down resistor
instead of tying it permanently to 3.3V) would clear the register
instantaneously at power-on and is recommended for the next PCB revision.

### Create the initialisation script

```bash
sudo tee /usr/local/bin/colorimeter-gpio-init.sh << 'EOF'
#!/bin/bash
# Colorimeter GPIO safe state initialisation
# Waits for /dev/spidev1.0 to appear, then clears the 74HC595 shift register
# and forces all direct MOSFET control pins low.

for i in $(seq 1 20); do
    [ -e /dev/spidev1.0 ] && break
    sleep 0.5
done

if [ ! -e /dev/spidev1.0 ]; then
    echo "colorimeter-gpio-init: spidev1.0 not found after 10s" >&2
    exit 1
fi

# Force direct MOSFET channels low (channels 9 and 10)
pinctrl set 7  op dl    # GPIO7  channel 9
pinctrl set 25 op dl    # GPIO25 channel 10
pinctrl set 24 op dl    # GPIO24 RCLK

# Clear shift register (channels 1-8) via SPI1
python3 - << 'PYEOF'
import spidev
from gpiozero import DigitalOutputDevice

rclk = DigitalOutputDevice(24, initial_value=False)   # 74HC595 RCLK

spi = spidev.SpiDev()
spi.open(1, 0)
spi.no_cs = True
spi.max_speed_hz = 500000
spi.mode = 0

for _ in range(3):
    spi.writebytes([0x00])
    rclk.on()
    rclk.off()

spi.close()
rclk.close()
print("colorimeter-gpio-init: shift register cleared")
PYEOF

EOF

sudo chmod +x /usr/local/bin/colorimeter-gpio-init.sh
```

Verify the shebang line has no Windows line-ending corruption
(output must end with $ and not ^M$):
```bash
head -1 /usr/local/bin/colorimeter-gpio-init.sh | cat -A
```

Test the script runs cleanly before creating the service:
```bash
sudo /usr/local/bin/colorimeter-gpio-init.sh
echo "Exit code: $?"
```

Expected: exit code 0 and the message "shift register cleared".

### Create the systemd service

```bash
sudo nano /etc/systemd/system/colorimeter-gpio-init.service
```

```ini
[Unit]
Description=Colorimeter GPIO safe state initialisation
After=multi-user.target
Wants=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/colorimeter-gpio-init.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable colorimeter-gpio-init.service
sudo systemctl start colorimeter-gpio-init.service
sudo systemctl status colorimeter-gpio-init.service
```

Expected status: active (exited) with exit-code 0/SUCCESS.

### Verify after reboot

```bash
sudo reboot
# reconnect via SSH, then:
sudo systemctl status colorimeter-gpio-init.service
sudo journalctl -b | grep colorimeter
```

The journal entry should show the service completing successfully and the
message "shift register cleared". The LED will light briefly during boot
(~10-15 seconds) until the service runs — this is expected behaviour
with the software-only fix.

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| status=203/EXEC | Script not found or not executable | Check filename spelling; re-run chmod +x |
| status=203/EXEC | Windows line endings in script | Recreate using tee heredoc as shown above |
| Dependency failed, device timeout | systemd cannot track /dev/spidev1.0 as a device unit | Use After=multi-user.target as shown above; do not use After=dev-spidev1.0.device |
| Script runs but LED still on | SPI1 driver initialised after service ran | Check journal timestamps; increase seq limit in wait loop from 20 to 40 |
| "spidev1.0 not found after 10s" | SPI1 not enabled in config.txt | Verify dtoverlay=spi1-1cs,cs0_pin=23 is present under [all] in config.txt |

## Step 13 — Quick functional smoke test: shift register

With the above checks passing, run this to confirm the 74HC595
wiring before connecting LEDs:

```bash
python3 - << 'EOF'
import spidev, time
from gpiozero import DigitalOutputDevice

rclk = DigitalOutputDevice(24, initial_value=False)   # RCLK

spi1 = spidev.SpiDev()
spi1.open(1, 0)
spi1.no_cs = True
spi1.max_speed_hz = 500_000

def shift_out(byte_val):
    spi1.writebytes([byte_val])
    rclk.on()
    rclk.off()

print("Shifting 0x00 (all off)...")
shift_out(0x00)
time.sleep(1)

print("Shifting 0xFF (all on)...")
shift_out(0xFF)
time.sleep(2)

print("Walking bits (each channel in turn)...")
for i in range(8):
    shift_out(1 << (7 - i))
    print(f"  Channel {i+1} (bit {7-i})")
    time.sleep(0.5)

shift_out(0x00)
print("Done.")
rclk.close()
spi1.close()
EOF
```

---

## Troubleshooting quick reference

| Symptom | First check |
|---|---|
| `/dev/spidev0.0` missing | `grep spi=on /boot/firmware/config.txt` — must be present and not commented |
| `/dev/i2c-1` missing | `grep i2c_arm=on /boot/firmware/config.txt` — must be present |
| `i2cdetect` shows nothing at 0x48 | 1651 powered? VddIO jumper set to 3V? J1 wiring correct? |
| `/dev/spidev1.0` missing | Check `grep spi1 /boot/firmware/config.txt` — overlay line correct? |
| GPIO18 shows ALT4 (SPI1 CE0) | `cs0_pin=23` missing or has a space — edit config.txt and reboot |
| 1-Wire devices directory empty | Probe connected? 4.7kΩ pull-up present? `gpiopin=16` in config? |
| `import lgpio` / `import gpiozero` fails | `sudo apt install python3-lgpio python3-gpiozero` (no daemon needed) |
| Backlight test does nothing | Check GPIO13 / J1-Pin9 wiring; confirm LITE/#3 jumper soldered on 1651; `gpiozero` importing? |
| Beeper silent | Check GPIO12 / MMBT3904 wiring; `import lgpio` OK? (`src/beeper.py`) |
| Shift register all-on/all-off works, walking bits wrong order | Bit order reversed — change `1 << (7-i)` to `1 << i` in software |
