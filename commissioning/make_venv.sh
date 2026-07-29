#!/usr/bin/env bash
# make_venv.sh — create the project virtual environment on the Raspberry Pi and
# verify that the key packages import from inside it. Run this ON the Pi, after
# bootstrap_system.sh has installed the apt packages.
#
#   ssh rpizero2
#   ~/pi/Colorimeter/commissioning/make_venv.sh
#
# The venv is created with --system-site-packages so it inherits the apt-built
# numpy / OpenCV / h5py / picamera2 / GPIO / SPI / I2C packages without slow,
# memory-hungry source compiles. Pure-Python extras (if any) can then be pip
# installed into the venv normally.
#
# Safe to re-run: if .venv already exists it is reused, only the import checks
# run.

set -euo pipefail

# Project root = the directory that contains this script's parent (commissioning/).
PROJECT_ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
VENV_PY="${VENV_DIR}/bin/python3"

echo ">>> Project root: ${PROJECT_ROOT}"

# ── Create the venv if it isn't there ───────────────────────────────────────
if [[ -x "${VENV_PY}" ]]; then
    echo ">>> Reusing existing venv at ${VENV_DIR}"
else
    echo ">>> Creating venv (--system-site-packages) at ${VENV_DIR}"
    python3 -m venv --system-site-packages "${VENV_DIR}"
fi

# ── Verify the imports the app depends on, FROM INSIDE the venv ──────────────
# --system-site-packages must be in effect for the apt packages to be visible.
echo ">>> Verifying imports inside the venv…"
"${VENV_PY}" - <<'PY'
import importlib, sys

# module name : friendly label (for the apt package it comes from)
mods = [
    ("numpy",     "numpy"),
    ("cv2",       "opencv"),
    ("h5py",      "h5py"),
    ("yaml",      "PyYAML"),
    ("spidev",    "spidev"),
    ("RPi.GPIO",  "RPi.GPIO (rpi-lgpio)"),
    ("smbus2",    "smbus2"),
    ("pigpio",    "pigpio"),
    ("picamera2", "picamera2"),
]

ok = True
for name, label in mods:
    try:
        m = importlib.import_module(name)
        ver = getattr(m, "__version__", "")
        print(f"  ✓ {label:22} {ver}")
    except Exception as e:
        ok = False
        print(f"  ✗ {label:22} FAILED: {e}")

if not ok:
    print("\nOne or more imports failed. Re-run bootstrap_system.sh (a system "
          "package is missing), and confirm the venv was created with "
          "--system-site-packages.", file=sys.stderr)
    sys.exit(1)
print("\nAll imports OK.")
PY

echo ""
echo "Done. venv ready at ${VENV_DIR}"
echo "Run the app by hand with:"
echo "  cd ${PROJECT_ROOT} && .venv/bin/python3 src/main.py"
