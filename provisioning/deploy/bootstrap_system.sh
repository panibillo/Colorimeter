#!/usr/bin/env bash
# bootstrap_system.sh — install the system (apt) packages the Colorimeter app
# needs, and put the user in the hardware groups those packages need.
# Run this ON the Raspberry Pi.
#
#   ssh rpizero2
#   ~/pi/Colorimeter/provisioning/bootstrap_system.sh
#
# These heavy/hardware packages are installed system-wide via apt (not pip):
# building numpy / OpenCV / h5py / picamera2 from source on a Zero-class board
# is slow and often exhausts memory. The project venv then inherits them with
# --system-site-packages (see make_venv.sh).
#
# GPIO policy (see CODING_GUIDELINES.md — "GPIO and Hardware Library
# Guidelines"): the app uses gpiozero for general GPIO / backlight on-off and
# lgpio for the beeper's PWM (src/beeper.py). It does NOT use pigpio/pigpiod or
# RPi.GPIO — there is deliberately NO background daemon to install or enable.
#
# Safe to re-run: apt install is idempotent — it just verifies/upgrades, and the
# group add skips a user who is already a member.

set -euo pipefail

# ── Required packages (app + hardware buses + capture pipeline) ─────────────
# python3-lgpio provides both the `lgpio` module imported directly by
# src/beeper.py AND gpiozero's default pin factory on Bookworm/Trixie, so no
# daemon and no RPi.GPIO shim are needed.
PKGS=(
    python3-full
    python3-numpy         # import numpy
    python3-gpiozero      # import gpiozero  (backlight on/off, touch IRQ, CE pins)
    python3-lgpio         # import lgpio     (beeper PWM) + gpiozero pin factory
    python3-spidev        # import spidev    (SPI0 display, SPI1 shift register)
    python3-smbus2        # import smbus2     (I2C touch controller)
    i2c-tools             # i2cdetect
    python3-picamera2     # import picamera2  (camera)
    python3-opencv        # import cv2
    python3-h5py          # import h5py       (capture archives)
    python3-evdev         # import evdev      (USB keyboard, src/kb_reader.py)
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

# No daemon setup: backlight (gpiozero) and beeper (lgpio) PWM are daemonless.

# ── Hardware group membership ───────────────────────────────────────────────
# src/kb_reader.py reads the USB keyboard from /dev/input/eventX directly (evdev)
# rather than from stdin, so it works under systemd and over SSH. Those nodes are
# owned by group `input`. The Imager's first user is already in `input`, but a
# hand-created account is not — and the failure is silent: the app runs, the
# keyboard just never responds. Adding an existing member is a no-op.
if id -nG | tr ' ' '\n' | grep -qx input; then
    echo ">>> User $(whoami) is already in the 'input' group (keyboard OK)"
else
    echo ">>> Adding $(whoami) to the 'input' group (USB keyboard via evdev)…"
    sudo usermod -aG input "$(whoami)"
    echo "    ⚠ Log out and back in (or reboot) for this to take effect."
fi

echo ""
echo "Done. Next: build the virtual environment with make_venv.sh"
echo "  ~/pi/Colorimeter/provisioning/make_venv.sh"
