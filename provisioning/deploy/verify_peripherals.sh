#!/usr/bin/env bash
# verify_peripherals.sh — read-only post-reboot check of the cal8 mainboard
# peripherals. Run this ON the Raspberry Pi after configuring config.txt
# (step 2) and rebooting.
#
#   ssh rpizero2
#   ~/pi/Colorimeter/provisioning/verify_peripherals.sh
#
# Checks SPI0, SPI1, I2C bus 1 (+ TSC2007 @0x48), 1-Wire (+ a DS18B20 and a
# temperature read), the GPIO character device (gpiozero/lgpio backends —
# there is no daemon), the SPI1 pin function, and the camera.
# It changes nothing and is safe to run repeatedly. A ✗ means investigate;
# see docs/commissioning/07_troubleshooting.md.

# NOTE: no `set -e` — we want every check to run even if an earlier one fails.

PASS=0; FAIL=0; WARN=0
pass() { echo "  ✓ $1"; PASS=$((PASS+1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL+1)); }
warn() { echo "  ⚠ $1"; WARN=$((WARN+1)); }

echo "=== Colorimeter peripheral check ($(hostname), $(date '+%Y-%m-%d %H:%M')) ==="

# ── SPI0 (display) ──────────────────────────────────────────────────────────
echo "SPI0 (ILI9341 display):"
if [[ -e /dev/spidev0.0 ]]; then pass "/dev/spidev0.0 present"
else fail "/dev/spidev0.0 missing — check 'dtparam=spi=on' in config.txt"; fi

# ── SPI1 (74HC595 LED driver) ───────────────────────────────────────────────
echo "SPI1 (74HC595 LED shift register):"
if [[ -e /dev/spidev1.0 ]]; then pass "/dev/spidev1.0 present"
else fail "/dev/spidev1.0 missing — check 'dtoverlay=spi1-1cs,cs0_pin=23'"; fi

if command -v pinctrl >/dev/null 2>&1; then
    line=$(pinctrl get 20 2>/dev/null || true)
    if [[ -n "$line" && "$line" != *" ip "* ]]; then pass "GPIO20 in SPI function: ${line}"
    else warn "GPIO20 not in an alt (SPI1) function: ${line:-<no output>}"; fi
else
    warn "pinctrl not found — skipping GPIO20 function check"
fi

# ── I2C bus 1 (+ TSC2007 touch @ 0x48) ──────────────────────────────────────
echo "I2C bus 1 (TSC2007 touch @ 0x48):"
if [[ -e /dev/i2c-1 ]]; then pass "/dev/i2c-1 present"
else fail "/dev/i2c-1 missing — check 'dtparam=i2c_arm=on' in config.txt"; fi

if command -v i2cdetect >/dev/null 2>&1; then
    scan=$(i2cdetect -y 1 2>/dev/null || sudo -n i2cdetect -y 1 2>/dev/null || true)
    if [[ -z "$scan" ]]; then
        warn "i2cdetect could not read bus 1 (permission? try: sudo i2cdetect -y 1)"
    elif echo "$scan" | grep -qiE '(^|[^0-9a-f])48([^0-9a-f]|$)'; then
        pass "device found at 0x48 (TSC2007)"
    else
        fail "no device at 0x48 — check 1651 power (5V), VddIO=3V jumper, J1 wiring"
    fi
else
    warn "i2cdetect not installed (apt install i2c-tools)"
fi

# ── 1-Wire (DS18B20 temperature probe on GPIO16) ────────────────────────────
echo "1-Wire (DS18B20 temperature probe, GPIO16):"
probe=$(ls -d /sys/bus/w1/devices/28-* 2>/dev/null | head -1)
if [[ -n "$probe" ]]; then
    pass "probe present: $(basename "$probe")"
    t=$(grep -o 't=[0-9-]*' "${probe}/w1_slave" 2>/dev/null | head -1 | cut -d= -f2)
    if [[ -n "$t" ]]; then
        # millidegrees C → degrees C
        printf "  ✓ temperature: %s.%03d °C\n" "$((t/1000))" "$((t%1000))"; PASS=$((PASS+1))
    else
        warn "probe present but no temperature read yet (retry, or check CRC)"
    fi
else
    fail "no 28-* device — see docs/DS18B20_troubleshooting.md (usually physical: pull-up/solder/pin)"
fi

# ── GPIO (gpiozero backlight GPIO13, lgpio beeper GPIO12 — daemonless) ──────
echo "GPIO (gpiozero + lgpio backends; no daemon):"
if [[ -e /dev/gpiochip0 ]]; then pass "/dev/gpiochip0 present"
else fail "/dev/gpiochip0 missing — kernel GPIO char device unavailable"; fi

if python3 -c 'import lgpio' 2>/dev/null; then pass "python3 can import lgpio (beeper PWM)"
else warn "python3 cannot import lgpio — sudo apt install python3-lgpio (src/beeper.py needs it)"; fi

if python3 -c 'import gpiozero' 2>/dev/null; then pass "python3 can import gpiozero (backlight/CE pins)"
else warn "python3 cannot import gpiozero — sudo apt install python3-gpiozero"; fi

# ── Camera ──────────────────────────────────────────────────────────────────
echo "Camera (IMX219 / IMX708):"
if command -v rpicam-hello >/dev/null 2>&1; then
    cams=$(rpicam-hello --list-cameras 2>/dev/null || true)
    if echo "$cams" | grep -qiE 'imx|Available cameras'; then
        pass "camera detected$(echo "$cams" | grep -oiE 'imx[0-9]+' | head -1 | sed 's/^/: /')"
    else
        fail "no camera reported by rpicam-hello — check ribbon and camera_auto_detect=1"
    fi
else
    warn "rpicam-hello not installed — check manually with picamera2, or install rpicam-apps"
fi

# ── Summary ─────────────────────────────────────────────────────────────────
echo "-------------------------------------------------------------------"
echo "  passed: ${PASS}   failed: ${FAIL}   warnings: ${WARN}"
[[ ${FAIL} -eq 0 ]] && echo "  All hard checks passed." \
                    || echo "  Investigate the ✗ items above before proceeding."
exit $(( FAIL > 0 ? 1 : 0 ))
