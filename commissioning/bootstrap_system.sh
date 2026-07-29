#!/usr/bin/env bash
# bootstrap_system.sh — install the system (apt) packages the Colorimeter app
# needs, and enable the pigpio daemon. Run this ON the Raspberry Pi.
#
#   ssh rpizero2
#   ~/pi/Colorimeter/commissioning/bootstrap_system.sh
#
# These heavy/hardware packages are installed system-wide via apt (not pip):
# building numpy / OpenCV / h5py / picamera2 from source on a Zero-class board
# is slow and often exhausts memory. The project venv then inherits them with
# --system-site-packages (see make_venv.sh).
#
# Safe to re-run: apt install is idempotent — it just verifies/upgrades.

set -euo pipefail

# ── Required packages (app + hardware buses + capture pipeline) ─────────────
# python3-rpi-lgpio provides the RPi.GPIO API on current Raspberry Pi OS
# (the classic python3-rpi.gpio is deprecated on Bookworm/Trixie).
PKGS=(
    python3-full
    python3-numpy         # import numpy
    python3-rpi-lgpio     # import RPi.GPIO  (lgpio-backed shim)
    python3-gpiozero      # import gpiozero
    python3-spidev        # import spidev    (SPI0 display, SPI1 shift register)
    python3-smbus2        # import smbus2     (I2C touch controller)
    python3-pigpio        # import pigpio     (hardware PWM backlight)
    pigpio                # the pigpiod daemon itself
    i2c-tools             # i2cdetect
    python3-picamera2     # import picamera2  (camera)
    python3-opencv        # import cv2
    python3-h5py          # import h5py       (capture archives)
    python3-yaml          # import yaml       (config/instrument.yaml)
)

echo ">>> Updating package lists…"
sudo apt update

echo ">>> Installing ${#PKGS[@]} required packages…"
sudo apt install -y "${PKGS[@]}"

# ── Optional: camera test tool (package name varies across releases) ────────
# rpicam-hello is only used for verification (verify_peripherals.sh); the app
# itself uses picamera2. Don't fail the whole bootstrap if it's unavailable.
echo ">>> Installing camera test tools (optional)…"
sudo apt install -y rpicam-apps 2>/dev/null \
    || sudo apt install -y libcamera-apps 2>/dev/null \
    || echo "    (rpicam-apps/libcamera-apps not available — skipping; app is unaffected)"

# ── pigpio daemon: needed for hardware PWM on GPIO12/GPIO13 ──────────────────
echo ">>> Enabling and starting pigpiod…"
sudo systemctl enable pigpiod
sudo systemctl start pigpiod

echo ""
echo "Done. Next: build the virtual environment with make_venv.sh"
echo "  ~/pi/Colorimeter/commissioning/make_venv.sh"
