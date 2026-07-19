#!/usr/bin/env bash
# sync_from_pi.sh — rsync listed project files FROM the remote Pi TO this machine.
#
# Usage:
#   ./sync_from_pi.sh <remote_name>
#
# Example:
#   ./sync_from_pi.sh rpi

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_ROOT="${HOME}/path/to/project"
FILE_LIST="${PROJECT_ROOT}/sync_pi_files.txt"
REMOTE_SRC_DIR="~/path/to/project"

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
# Sync remote → local
#
# Note: --files-from with a remote source requires the file list to be on
# the remote, OR we use a workaround. The cleanest approach for keeping the
# list local is to pass each file as an explicit rsync source path.
#
# We read sync_pi_files.txt line by line, skip blank lines and comments,
# and build the remote source paths manually.
# ---------------------------------------------------------------------------
echo ">>> Syncing FROM ${REMOTE}:${REMOTE_SRC_DIR} ..."

# Build array of remote source paths from the file list
SOURCES=()
while IFS= read -r line; do
    # Skip blank lines and comment lines (starting with #)
    [[ -z "${line}" || "${line}" == \#* ]] && continue
    SOURCES+=("${REMOTE}:${REMOTE_SRC_DIR}/${line}")
done < "${FILE_LIST}"

if [[ ${#SOURCES[@]} -eq 0 ]]; then
    echo "Error: no files found in ${FILE_LIST}" >&2
    exit 1
fi

rsync \
    --archive \
    --compress \
    --verbose \
    --checksum \
    --relative \
    "${SOURCES[@]}" \
    "${PROJECT_ROOT}/"

echo "Done."
