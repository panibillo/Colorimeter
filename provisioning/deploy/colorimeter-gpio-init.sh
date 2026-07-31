#!/bin/bash
# Colorimeter GPIO safe state initialisation. version 1.1 2026-07-31
# Waits for /dev/spidev1.0 to appear, then clears the 74HC595 shift register
# and forces all direct MOSFET control pins low.
# Save this script as /usr/local/bin/colorimeter-gpio-init.sh

for i in $(seq 1 20); do
    [ -e /dev/spidev1.0 ] && break
    sleep 0.5
done

if [ ! -e /dev/spidev1.0 ]; then
    echo "colorimeter-gpio-init: spidev1.0 not found after 10s" >&2
    exit 1
fi

# Force direct MOSFET channels low (channels 9 and 10)
pinctrl set 7 op dl # GPIO7 channel 9
pinctrl set 25 op dl # GPIO25 channel 10
pinctrl set 24 op dl # GPIO24 RCLK

# Clear shift register (channels 1-8) via SPI1
python3 - << 'PYEOF'
import spidev
from gpiozero import DigitalOutputDevice

rclk = DigitalOutputDevice(24, initial_value=False) # 74HC595 RCLK

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

