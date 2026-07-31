#!/usr/bin/env bash
# install_colorimeter_service.sh — install and enable the systemd service
# that launches src/main.py (via the project .venv) automatically at boot.
#
# Run this ON the Raspberry Pi, as the account that owns the app directory
# (e.g. "mgwwg" on a newly commissioned unit, or "bill" on rpizero1):
#   ssh <host>
#   ~/pi/Colorimeter/provisioning/install_colorimeter_service.sh
#
# The service runs as whichever user invokes this script (via `whoami`) — it
# is not tied to one hardcoded username.
#
# Safe to re-run: it overwrites the unit file and reloads/restarts the
# service, so editing this script and re-running picks up the change.
#
# Undo with: uninstall_colorimeter_service.sh (same directory).

set -euo pipefail

APP_USER="$(whoami)"
if [[ "${APP_USER}" == "root" ]]; then
    echo "Error: run this as your normal login user, not root/sudo — it" >&2
    echo "escalates internally (via 'sudo tee' / 'systemctl') where needed." >&2
    exit 1
fi
APP_DIR="/home/${APP_USER}/pi/Colorimeter"
VENV_PYTHON="${APP_DIR}/.venv/bin/python3"
MAIN_PY="${APP_DIR}/src/main.py"
SERVICE_NAME="colorimeter.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

# ── Sanity checks ──────────────────────────────────────────────────────────
if [[ ! -x "${VENV_PYTHON}" ]]; then
    echo "Error: venv interpreter not found at ${VENV_PYTHON}" >&2
    exit 1
fi
if [[ ! -f "${MAIN_PY}" ]]; then
    echo "Error: main.py not found at ${MAIN_PY}" >&2
    exit 1
fi

# ── Write the unit file ─────────────────────────────────────────────────────
# WorkingDirectory is the project root, not src/ — src/logging_manager.py
# opens its log with the relative path "src/colorimeter.log", which only
# resolves correctly when the process cwd is the project root.
sudo tee "${SERVICE_PATH}" > /dev/null <<EOF
[Unit]
Description=Colorimeter application (main.py)
After=multi-user.target colorimeter-gpio-init.service
Wants=colorimeter-gpio-init.service

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${VENV_PYTHON} ${MAIN_PY}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

echo ""
echo "Installed and started ${SERVICE_NAME}. Status:"
sudo systemctl status "${SERVICE_NAME}" --no-pager
echo ""
echo "Follow logs with: journalctl -u ${SERVICE_NAME%.service} -f"
