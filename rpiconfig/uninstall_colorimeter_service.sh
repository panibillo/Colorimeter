#!/usr/bin/env bash
# uninstall_colorimeter_service.sh — stop, disable, and remove the
# colorimeter.service systemd unit installed by install_colorimeter_service.sh.
#
# Run this ON the Raspberry Pi:
#   ssh rpizero1
#   ~/pi/Colorimeter/rpiconfig/uninstall_colorimeter_service.sh
#
# Does NOT touch colorimeter-gpio-init.service (a separate, independent
# unit) or any project files — only removes the boot-launch wiring for
# main.py. Safe to re-run; a no-op if the service is already gone.

set -euo pipefail

SERVICE_NAME="colorimeter.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

if [[ ! -f "${SERVICE_PATH}" ]]; then
    echo "${SERVICE_NAME} is not installed (${SERVICE_PATH} not found). Nothing to do."
    exit 0
fi

sudo systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
sudo systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
sudo rm -f "${SERVICE_PATH}"
sudo systemctl daemon-reload
sudo systemctl reset-failed "${SERVICE_NAME}" 2>/dev/null || true

echo "Removed ${SERVICE_NAME}. main.py will no longer launch automatically at boot."
