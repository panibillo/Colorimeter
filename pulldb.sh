#!/usr/bin/env bash
# pullpidb.sh — copy the colorimeter database FROM the Pi TO this machine.
#
# Usage:
#   ./pullpidb.sh [dest]
#
# Example:
#   ./pullpidb.sh
#   ./pullpidb.sh ~/backups/colorimeter.sqlite
#
# The current date is inserted into the destination filename before its
# extension (e.g. colorimeter.sqlite -> colorimeter_2026-08-09.sqlite).
# An existing file at the computed path is never overwritten.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REMOTE="rpizero3"
REMOTE_DB="~/pi/Colorimeter/src/colorimeter.sqlite"
DEST="${1:-${HOME}/R/pi/colorimeter.sqlite}"

# ---------------------------------------------------------------------------
# Compute a dated destination filename
# ---------------------------------------------------------------------------
DEST_DIR="$(dirname "${DEST}")"
DEST_BASE="$(basename "${DEST}")"
STEM="${DEST_BASE%.*}"
EXT="${DEST_BASE##*.}"
DATE_STAMP="$(date +%Y-%m-%d)"
DATE_STAMPM="$(date +%Y-%m-%d-%M)"

if [[ "${STEM}" == "${EXT}" ]]; then
    # DEST_BASE has no extension
    DATED_NAME="${DEST_BASE}_${DATE_STAMP}"
    DATED_NAMEM="${DEST_BASE}_${DATE_STAMPM}"
else
    DATED_NAME="${STEM}_${DATE_STAMP}.${EXT}"
    DATED_NAMEM="${STEM}_${DATE_STAMPM}.${EXT}"
fi

DATED_DEST="${DEST_DIR}/${DATED_NAME}"

# ---------------------------------------------------------------------------
# Fall back to using Minute in the filename if needed
# ---------------------------------------------------------------------------
if [[ -e "${DATED_DEST}" ]]; then
    DATED_DEST="${DEST_DIR}/${DATED_NAMEM}"
fi

# ---------------------------------------------------------------------------
# Refuse to overwrite an existing file
# ---------------------------------------------------------------------------
if [[ -e "${DATED_DEST}" ]]; then
    echo "Error: ${DATED_DEST} already exists, not overwriting." >&2
    exit 1
fi

mkdir -p "${DEST_DIR}"

# ---------------------------------------------------------------------------
# Pull remote -> local
# ---------------------------------------------------------------------------
echo ">>> Pulling ${REMOTE}:${REMOTE_DB} -> ${DATED_DEST} ..."

scp "${REMOTE}:${REMOTE_DB}" "${DATED_DEST}"

echo "Done: ${DATED_DEST}"
