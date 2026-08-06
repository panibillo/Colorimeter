#!/usr/bin/env bash
# sync_captures_from_pi.sh — rsync captured data FROM the Pi TO local captures/
#
# Usage:
#   ./sync_captures_from_pi.sh <remote_name>
#
# Example:
#   ./sync_captures_from_pi.sh rpizero3

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
CAPTURES_DIR="${PROJECT_ROOT}/captures"
REMOTE_CAPTURES_DIR="~/pi/Colorimeter/captures"

# ---------------------------------------------------------------------------
# Validate arguments
# ---------------------------------------------------------------------------
if [[ $# -ne 1 ]]; then
    echo "Usage: $(basename "$0") <remote_name>" >&2
    exit 1
fi

REMOTE="$1"

# ---------------------------------------------------------------------------
# Create local captures directory if it doesn't exist
# ---------------------------------------------------------------------------
mkdir -p "${CAPTURES_DIR}"

# ---------------------------------------------------------------------------
# Sync remote captures → local
# ---------------------------------------------------------------------------
echo ">>> Syncing FROM ${REMOTE}:${REMOTE_CAPTURES_DIR} ..."

rsync \
    --archive \
    --compress \
    --verbose \
    --progress \
    "${REMOTE}:${REMOTE_CAPTURES_DIR}/" \
    "${CAPTURES_DIR}/"

echo "Done."
