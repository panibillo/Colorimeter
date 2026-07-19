#!/usr/bin/env bash
# sync_to_pi.sh — rsync listed project files FROM this machine TO the remote Pi.
#
# Usage:
#   ./sync_to_pi.sh <remote_name>
#
# Example:
#   ./sync_to_pi.sh rpi

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_ROOT="${HOME}/Projects/Colorimeter"
FILE_LIST="${PROJECT_ROOT}/sync_pi_files.txt"
REMOTE_DEST_DIR="~/pi/Colorimeter"

# ---------------------------------------------------------------------------
# Validate arguments
# ---------------------------------------------------------------------------
if [[ $# -ne 1 ]]; then
    echo "Usage: $(basename "$0") <remote_name>" >&2
    exit 1
fi

REMOTE="$1"

# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------
if [[ ! -d "${PROJECT_ROOT}" ]]; then
    echo "Error: project root not found: ${PROJECT_ROOT}" >&2
    exit 1
fi

if [[ ! -f "${FILE_LIST}" ]]; then
    echo "Error: file list not found: ${FILE_LIST}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Sync local → remote
# ---------------------------------------------------------------------------
echo ">>> Syncing TO ${REMOTE}:${REMOTE_DEST_DIR} ..."

rsync \
    --archive \
    --compress \
    --verbose \
    --checksum \
    --relative \
    --files-from="${FILE_LIST}" \
    "${PROJECT_ROOT}/" \
    "${REMOTE}:${REMOTE_DEST_DIR}/"

echo "Done."
