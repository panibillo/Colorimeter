# DS18B20 One-Wire Troubleshooting Guide for Raspberry Pi

## This project's pin

The Colorimeter uses **GPIO16 (physical pin 36)** for the 1-Wire bus — a
non-default pin. This is documented in `src/DS18B20.py:6-8` (docstring only;
the driver never hardcodes a GPIO number — it just reads whatever the kernel
exposes under `/sys/bus/w1/devices/`). The pin assignment itself lives on the
Pi, in `/boot/firmware/config.txt`:
```
dtoverlay=w1-gpio,gpiopin=16
```
Requires a 4.7 kΩ pull-up resistor to 3.3V on the data line (PCB-fitted).

## Quick Diagnosis (run these first)

```bash
# 1. Is the one-wire module loaded?
lsmod | grep w1

# 2. Is the device file present?
ls -la /sys/bus/w1/devices/

# 3. Can you read the sensor?
cat /sys/bus/w1/devices/28-*/w1_slave  # Replace * with your device ID
```

If `/sys/bus/w1/devices/` is empty or shows only the bus master (`w1_bus_master1`), the sensor is not being detected.

---

## Troubleshooting Steps (in order)

### Step 1: Verify Hardware Connection

**Physical checks:**
- Sensor has three wires: **VDD (3.3V)**, **GND**, **DQ (data)**
- Confirm correct pin is wired to DQ (data line) — GPIO16 / physical pin 36 for this project
- Check for a **4.7 kΩ pull-up resistor** between VDD and DQ
  - DS18B20 requires this; without it, the bus won't work
  - Verify the resistor is actually soldered/connected (not loose)
- Verify VDD is getting **3.3V** (not 5V) and GND is solid ground

**Test with a multimeter:**
```bash
# Measure voltage on the data pin (should be ~3.3V pulled up)
# Measure voltage on VDD pin (should be 3.3V)
# Measure voltage between VDD and GND (should be 3.3V)
```

---

### Step 2: Check the Device Tree and Pin Configuration

**Check if one-wire is enabled in device tree:**
```bash
dtoverlay -l | grep w1
```

**If w1 is not listed, enable it temporarily (lost on reboot):**
```bash
sudo dtoverlay w1-gpio gpiopin=16
```

**Persistent setup (survives reboot):**
1. Edit `/boot/firmware/config.txt` (or `/boot/config.txt` on older Pi OS):
   ```bash
   sudo nano /boot/firmware/config.txt
   ```
2. Confirm this line is present:
   ```
   dtoverlay=w1-gpio,gpiopin=16
   ```
3. Reboot:
   ```bash
   sudo reboot
   ```

---

### Step 3: Verify the Pin State

**Check if the pin is configured as GPIO (not reserved) — use `pinctrl`, one
pin at a time (`raspi-gpio` is not available on this system):**
```bash
pinctrl get 16
```

Expected output should show the pin configured as an **input** with **pull-up enabled**.

**If pin is reserved or in use by something else:**
```bash
dtoverlay -l   # check for a conflicting overlay
```

---

### Step 4: Check the One-Wire Module

**Verify the w1 modules are loaded:**
```bash
lsmod | grep w1
```

Expected output (naming varies by kernel — Raspbian's `6.12.87+rpt-rpi-v6`
reports `w1_gpio` + `wire`, not the classic `w1_gpio`/`w1_therm`/`w1` trio):
```
w1_gpio                12288  0
wire                   36864  1 w1_gpio
```

**If missing, load manually:**
```bash
sudo modprobe w1-gpio
sudo modprobe wire
```

---

### Step 5: Check for the Device

**Look for the sensor in sysfs:**
```bash
ls -la /sys/bus/w1/devices/
```

Expected output (sample):
```
lrwxrwxrwx 1 root root 0 Jul 23 14:05 28-000001234567 -> ../../devices/w1_bus_master1/28-000001234567
lrwxrwxrwx 1 root root 0 Jul 23 14:05 w1_bus_master1 -> ../../devices/w1_bus_master1
```

The `28-` prefix is the DS18B20 family code; the rest of the name is the
sensor's unique ROM code.

**If only `w1_bus_master1` (and/or stray `00-*` entries) are present, with no
`28-*` device:** the bus is alive but the sensor is not responding. This is
the exact failure seen on `rpizero1` on 2026-07-23 — modules loaded, config.txt
correct, but no `28-*` device appeared. Go back to **Step 1**: check hardware
wiring, voltage, and pull-up resistor on the physical connection to GPIO16 /
pin 36 on that specific board.

---

### Step 6: Read the Sensor

```bash
cat /sys/bus/w1/devices/28-000001234567/w1_slave   # substitute your device ID
```

Expected output:
```
01 4b 46 00 1f ff 0c 10 f2 : crc=f2 YES
4b 01 4b 46 00 1f ff 0c 10 : temperature=20687
```

- `YES` = CRC is valid, sensor is healthy
- Temperature is in millidegrees Celsius (e.g., `20687` = 20.687°C)

**If output shows `NO` (CRC failed):**
- Weak pull-up resistor or noisy signal
- Try a stronger resistor (2.2 kΩ)
- Check for EMI near the wiring

---

## Common Issues & Fixes

| Symptom | Cause | Fix |
|---------|-------|-----|
| Only `w1_bus_master1` (+ maybe `00-*`) in `/sys/bus/w1/devices/`, no `28-*` | Sensor not detected on bus despite bus being up | Check wiring, voltage, pull-up resistor on GPIO16/pin 36 for this specific board |
| `w1_gpio`/`wire` module not loading | Overlay not applied or pin conflict | Reboot after editing `config.txt`; check for GPIO pin collisions |
| Reading gives `NO` CRC response | Weak pull-up or signal noise | Strengthen pull-up resistor (try 2.2 kΩ); shorten cable; check for EMI |
| Wrong GPIO pin detected | Wrong pin in overlay config | Verify `gpiopin=16` in `config.txt` matches actual wiring |
| Works on one Pi but not another (this project's exact symptom) | Board-specific wiring/resistor/solder joint difference — the config and driver are identical across boards | Physically inspect this board's connection: resistor present and soldered, correct pin, no cold joint |

---

## Board-specific note (this project)

The driver and `config.txt` entry are software/firmware, and are the same
across every Pi running this codebase. If one board works and another
doesn't with identical config, the difference is almost always **physical**:
a missing/loose pull-up resistor, a bad solder joint, wrong physical pin, or
a marginal 3.3V/GND connection on that particular board. Re-check Step 1
hardware before suspecting software on a board that has never worked.

---

## Quick Test Script

```bash
#!/bin/bash
echo "=== DS18B20 One-Wire Diagnostic ==="

echo "1. Kernel modules:"
lsmod | grep w1 || echo "  ⚠ No w1 modules loaded"

echo ""
echo "2. Device tree overlays:"
dtoverlay -l | grep w1 || echo "  ⚠ w1-gpio not in active overlays"

echo ""
echo "3. Devices in sysfs:"
devices=$(ls /sys/bus/w1/devices/ 2>/dev/null | grep "^28-")
if [ -z "$devices" ]; then
  echo "  ⚠ No DS18B20 devices found (28-* prefix)"
else
  for dev in $devices; do
    echo "  Found: $dev"
    cat /sys/bus/w1/devices/$dev/w1_slave 2>/dev/null | tail -1
  done
fi

echo ""
echo "4. Checking /boot config:"
grep -i "dtoverlay.*w1" /boot/firmware/config.txt 2>/dev/null || \
  echo "  ⚠ w1-gpio overlay not in /boot/firmware/config.txt"

echo ""
echo "5. Pin state (GPIO16):"
pinctrl get 16 2>/dev/null || echo "  ⚠ pinctrl not available"
```
