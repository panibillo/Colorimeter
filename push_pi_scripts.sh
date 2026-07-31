#!/usr/bin/env bash
# push_pi_scripts.sh — upload the Pi-run commissioning scripts to the Pi.
#
# Usage:
#   ./push_pi_scripts.sh [-n] <host>
#
#   <host>   SSH host alias or IP  (e.g. rpizero3 or 192.168.0.110)
#   -n       Dry run — show what would be sent without transferring anything
#
# Copies the exact contents of provisioning/deploy/ (bootstrap_system.sh,
# make_venv.sh, verify_peripherals.sh, install/uninstall_colorimeter_service.sh,
# install_gpio_init_service.sh, colorimeter-gpio-init.sh) to
#   <host>:~/pi/Colorimeter/provisioning/
#
# NOTE the flatten: on the desktop these live under provisioning/deploy/, but on
# the Pi they must sit exactly ONE level under the project root
# (~/pi/Colorimeter/provisioning/) — make_venv.sh derives the project root as
# its own parent directory, so nesting them deeper on the Pi would break it.
#
# This handles only the *scripts*. Firmware/config/DB are sent separately
# (sync_to_pi.sh / bulk_sync_to_pi.sh + the DB copy in docs/commissioning step 3).
#
# After uploading, run them ON the Pi in order (see docs/commissioning/03,05):
#   ~/pi/Colorimeter/provisioning/bootstrap_system.sh
#   ~/pi/Colorimeter/provisioning/make_venv.sh
#   ~/pi/Colorimeter/provisioning/verify_peripherals.sh
#   ~/pi/Colorimeter/provisioning/install_colorimeter_service.sh
#   ~/pi/Colorimeter/provisioning/install_gpio_init_service.sh

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SRC_DIR="${PROJECT_ROOT}/provisioning/deploy"
REMOTE_DEST_DIR="~/pi/Colorimeter/provisioning"

# ── Argument parsing ───────────────────────────────────────────────────────
DRY_RUN=0
REMOTE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--dry-run) DRY_RUN=1 ;;
        -*)
            echo "Unknown option: $1" >&2
            echo "Usage: $(basename "$0") [-n] <host>" >&2
            exit 1
            ;;
        *)
            if [[ -n "$REMOTE" ]]; then
                echo "Error: unexpected argument '$1'" >&2
                exit 1
            fi
            REMOTE="$1"
            ;;
    esac
    shift
done

if [[ -z "$REMOTE" ]]; then
    echo "Usage: $(basename "$0") [-n] <host>" >&2
    echo "" >&2
    echo "  <host>  SSH host alias or IP (e.g. rpizero3 or 192.168.0.110)" >&2
    echo "  -n      dry run — show what would be transferred" >&2
    exit 1
fi

# ── Sanity checks ──────────────────────────────────────────────────────────
if [[ ! -d "$SRC_DIR" ]]; then
    echo "Error: deploy directory not found: $SRC_DIR" >&2
    exit 1
fi

# ── Upload ─────────────────────────────────────────────────────────────────
# --archive preserves the executable bits git already tracks (bootstrap,
# make_venv, verify, install/uninstall are +x; colorimeter-gpio-init.sh is not
# — it is installed to /usr/local/bin with +x by install_gpio_init_service.sh).
RSYNC_OPTS=(--archive --compress --verbose --checksum)
[[ $DRY_RUN -eq 1 ]] && RSYNC_OPTS+=(--dry-run)

echo ">>> Uploading provisioning/deploy/ -> ${REMOTE}:${REMOTE_DEST_DIR}/ ..."
rsync "${RSYNC_OPTS[@]}" "${SRC_DIR}/" "${REMOTE}:${REMOTE_DEST_DIR}/"

echo ""
[[ $DRY_RUN -eq 1 ]] \
    && echo "(dry run complete — nothing transferred)" \
    || echo "Done. Now run the scripts on the Pi (see docs/commissioning/03,05)."
