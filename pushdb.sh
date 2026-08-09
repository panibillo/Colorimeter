#!/usr/bin/env bash
# pushdb.sh — back up the database on the Pi, then copy the local db TO the Pi.
#
# Usage:
#   ./pushdb.sh [src]
#
# Example:
#   ./pushdb.sh
#   ./pushdb.sh src/colorimeter.sqlite.v1.bak
#
# Before overwriting, the Pi's live colorimeter.sqlite is backed up in place
# as colorimeter.sqlite.bak-<date> (same convention already used by hand in
# src/). If that backup can't be made, pulldb.sh is used as a fallback to
# back the db up on this machine instead. The push is aborted if neither
# backup succeeds.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
REMOTE="rpizero3"
REMOTE_DB="~/pi/Colorimeter/src/colorimeter.sqlite"
SRC="${1:-${PROJECT_ROOT}/src/colorimeter.sqlite}"
DATE_STAMP="$(date +%Y-%m-%d)"

# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------
if [[ ! -f "${SRC}" ]]; then
    echo "Error: source file not found: ${SRC}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 1: back up the live db on the Pi before overwriting it.
# cp -n (no-clobber) is a no-op if today's backup already exists.
# ---------------------------------------------------------------------------
echo ">>> Backing up ${REMOTE}:${REMOTE_DB} ..."

if ssh "${REMOTE}" "cp -n ${REMOTE_DB} ${REMOTE_DB}.bak-${DATE_STAMP}"; then
    echo "Backed up on ${REMOTE} as ${REMOTE_DB}.bak-${DATE_STAMP}"
else
    echo "Warning: could not back up db on ${REMOTE}; falling back to pulldb.sh ..." >&2
    if ! "${PROJECT_ROOT}/pulldb.sh"; then
        echo "Error: fallback backup via pulldb.sh also failed; aborting push." >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Step 2: copy the local db to the Pi
# ---------------------------------------------------------------------------
echo ">>> Pushing ${SRC} -> ${REMOTE}:${REMOTE_DB} ..."

scp "${SRC}" "${REMOTE}:${REMOTE_DB}"

echo "Done."
