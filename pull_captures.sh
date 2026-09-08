#!/usr/bin/env bash
# pull_captures.sh — rsync a range of captures FROM the Pi TO local captures/
#
# Like sync_captures_from_pi.sh, but instead of pulling everything it pulls
# only files whose samp_no (see sample_repo.py's _parse_template rule: a
# letters prefix followed by a zero-padded number, e.g. 'ZP1007') matches the
# given pattern's prefix and whose number is >= the pattern's number.
#
# Usage:
#   ./pull_captures.sh <remote_name> <pattern>
#
# Example:
#   ./pull_captures.sh rpizero3 C1007
#   # If C1007.h5 .. C1012.h5 and D1001.h5 exist on the remote, this pulls
#   # C1007.h5 through C1012.h5 but not D1001.h5.

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
if [[ $# -ne 2 ]]; then
    echo "Usage: $(basename "$0") <remote_name> <pattern>" >&2
    echo "  <pattern>  a samp_no or samp_no prefix, e.g. 'C1007' or 'C'" >&2
    echo "             pulls every file with that prefix numbered >= it" >&2
    exit 1
fi

REMOTE="$1"
PATTERN="${2%.[Hh]5}"   # drop a trailing .h5, if given

# ---------------------------------------------------------------------------
# Split PATTERN into prefix (letters) and start number (trailing digits),
# following the samp_no rule in sample_repo.py's _parse_template().
# ---------------------------------------------------------------------------
if [[ "${PATTERN}" =~ ^([A-Za-z]*)([0-9]*)$ ]]; then
    PREFIX="${BASH_REMATCH[1]}"
    START="${BASH_REMATCH[2]}"
else
    echo "Pattern must be letters followed by digits, e.g. C1007 or C" >&2
    exit 1
fi
START=$((10#${START:-0}))   # numeric value; 10# avoids octal on leading zeros

mkdir -p "${CAPTURES_DIR}"

# ---------------------------------------------------------------------------
# Expand REMOTE_CAPTURES_DIR on the remote (~ is not expanded by rsync)
# ---------------------------------------------------------------------------
EXPANDED_REMOTE_PATH=$(ssh "${REMOTE}" "echo ${REMOTE_CAPTURES_DIR}")

# ---------------------------------------------------------------------------
# List remote files, keep those matching PREFIX (case-insensitive, like
# samp_no's UNIQUE COLLATE NOCASE) with a numeric suffix >= START.
# ---------------------------------------------------------------------------
echo ">>> Listing ${REMOTE}:${REMOTE_CAPTURES_DIR} for ${PREFIX}<n>.h5 >= ${PATTERN} ..."

MATCHES="$(ssh "${REMOTE}" "ls -1 ${REMOTE_CAPTURES_DIR}" | awk -v prefix="${PREFIX}" -v start="${START}" '
    BEGIN { IGNORECASE = 1 }
    {
        fname = $0
        base = fname
        sub(/\.[Hh]5$/, "", base)
        if (toupper(substr(base, 1, length(prefix))) != toupper(prefix)) next
        rest = substr(base, length(prefix) + 1)
        if (rest !~ /^[0-9]+$/) next
        if (rest + 0 >= start) print fname
    }
')"

if [[ -z "${MATCHES}" ]]; then
    echo "No files matched pattern '${PATTERN}'."
    exit 0
fi

echo "Matched:"
echo "${MATCHES}"

# ---------------------------------------------------------------------------
# Pull only the matched files
# ---------------------------------------------------------------------------
echo ">>> Pulling matched files FROM ${REMOTE}:${REMOTE_CAPTURES_DIR} ..."

rsync \
    --archive \
    --compress \
    --verbose \
    --progress \
    --files-from=<(printf '%s\n' "${MATCHES}") \
    "${REMOTE}:${EXPANDED_REMOTE_PATH}/" \
    "${CAPTURES_DIR}/"

echo "Done."
