#!/usr/bin/env bash
# install_gpio_init_service.sh — install and enable the systemd service that
# clears the cal8 74HC595 LED shift register (and forces the direct-control
# MOSFET pins low) at boot, BEFORE the application starts.
#
# Run this ON the Raspberry Pi, as the account that owns the app directory
# (e.g. "mgwwg" on a newly commissioned unit, or "bill" on rpizero1):
#   ssh <host>
#   ~/pi/Colorimeter/provisioning/install_gpio_init_service.sh
#
# It copies colorimeter-gpio-init.sh (shipped alongside this installer) to
# /usr/local/bin/, writes /etc/systemd/system/colorimeter-gpio-init.service,
# then enables + starts it. colorimeter.service is ordered After=/Wants= this
# unit, so the LEDs are guaranteed off before main.py runs.
#
# This just AUTOMATES the manual recipe documented in
# edocs/rpi_configuration_guide_for_cal8.md (Step 12) and
# docs/commissioning/05_testing.md (Step 5b) — either path is fine.
#
# Safe to re-run: it overwrites the script + unit and reloads/re-enables.
#
# Undo (if ever needed):
#   sudo systemctl disable --now colorimeter-gpio-init.service
#   sudo rm /etc/systemd/system/colorimeter-gpio-init.service \
#           /usr/local/bin/colorimeter-gpio-init.sh
#   sudo systemctl daemon-reload

set -euo pipefail

if [[ "$(whoami)" == "root" ]]; then
    echo "Error: run this as your normal login user, not root/sudo — it" >&2
    echo "escalates internally (via 'sudo') where needed." >&2
    exit 1
fi

# The source script ships next to this installer (uploaded to
# ~/pi/Colorimeter/provisioning/ by push_pi_scripts.sh).
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SRC_SCRIPT="${SCRIPT_DIR}/colorimeter-gpio-init.sh"
BIN_SCRIPT="/usr/local/bin/colorimeter-gpio-init.sh"
SERVICE_NAME="colorimeter-gpio-init.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

# ── Sanity checks ──────────────────────────────────────────────────────────
if [[ ! -f "${SRC_SCRIPT}" ]]; then
    echo "Error: colorimeter-gpio-init.sh not found next to this installer:" >&2
    echo "       ${SRC_SCRIPT}" >&2
    echo "Upload the provisioning/deploy/ scripts first (push_pi_scripts.sh)." >&2
    exit 1
fi

# ── Install the script to /usr/local/bin (forces mode 0755) ─────────────────
sudo install -m 0755 "${SRC_SCRIPT}" "${BIN_SCRIPT}"

# ── Write the unit file ─────────────────────────────────────────────────────
# Definition mirrors the cal8 reference guide (Step 12): a oneshot with
# RemainAfterExit so systemd reports "active (exited)" once the register is
# cleared. colorimeter.service orders itself After=/Wants= this unit.
sudo tee "${SERVICE_PATH}" > /dev/null <<'EOF'
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
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl start "${SERVICE_NAME}"

echo ""
echo "Installed and started ${SERVICE_NAME}. Status:"
sudo systemctl status "${SERVICE_NAME}" --no-pager
echo ""
echo "A short LED-on flash during boot (until this service runs) is expected"
echo "with the software-only fix; see the cal8 guide's note on the hardware fix."
