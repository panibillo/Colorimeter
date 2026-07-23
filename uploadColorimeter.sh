#!/usr/bin/env bash
# up2py.sh — upload new and modified source files to the Pi.
#
# Usage:
#   ./up2py.sh [-n] <host>
#
#   <host>   SSH host alias or IP address  (e.g. rpizero1 or 192.168.1.42)
#   -n       Dry run — show what would be sent without transferring anything
#
# Syncs src/, dev/, and config/ from the project root.
# Transfers only *.py, *.yaml, and *.csv files; skips unmodified files.
# Never deletes files on the remote.
#
# Includes hardware drivers (DS18B20.py, ili9341.py, etc.) from src/.
# Excludes mock hardware drivers (DS18B20_mock.py, etc.) from src/mock/.
#
# On the RPi, launch the application with:
#   python3 ~/pi/Colorimeter/src/main.py
#
# Examples:
#   ./up2py.sh rpizero1
#   ./up2py.sh -n 192.168.1.42

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────

PROJECT_ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
REMOTE_BASE="~/pi/Colorimeter"
SYNC_DIRS=(src dev config)

# ── Argument parsing ──────────────────────────────────────────────────────────

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
    echo "  <host>  SSH host alias or IP (e.g. rpizero1 or 192.168.1.42)" >&2
    echo "  -n      dry run — show what would be transferred" >&2
    exit 1
fi

# ── rsync options ─────────────────────────────────────────────────────────────

# Filter order matters: first match wins.
#   1. Exclude __pycache__ dirs and compiled bytecode before include rules.
#   2. Include the file types we want.
#   3. Include directories so rsync recurses into them.
#   4. Exclude everything else.

RSYNC_OPTS=(
    --archive
    --compress
    --human-readable
    --itemize-changes
    # --delete                            # uncomment to remove files on the Pi that are absent locally
    --exclude='mock/'                     # exclude mock hardware drivers (src/mock/*.py)
    --exclude='mock*.py'                  # exclude mock-pattern files (e.g. test_mock_rendering.py)
    --exclude='run_simulation.py'         # exclude desktop simulation launcher
    --exclude='onewire_temp.py'           # exclude DS18B20 development/test script
    --exclude='__pycache__/'
    --exclude='*.pyc'
    --exclude='*.pyo'
    --include='*.py'
    --include='*.yaml'
    --include='*.csv'
    --include='*/'
    --exclude='*'
    --exclude='config/touch_cal.json'
    --prune-empty-dirs
)

[[ $DRY_RUN -eq 1 ]] && RSYNC_OPTS+=(--dry-run)

# ── Sync each directory ───────────────────────────────────────────────────────

[[ $DRY_RUN -eq 1 ]] && echo "(dry run)" || true

any=0
for dir in "${SYNC_DIRS[@]}"; do
    src="${PROJECT_ROOT}/${dir}/"
    dst="${REMOTE}:${REMOTE_BASE}/${dir}/"
    [[ -d "$src" ]] || continue
    echo ""
    echo "=== ${dir}/ → ${dst} ==="
    rsync "${RSYNC_OPTS[@]}" "$src" "$dst"
    any=1
done

if [[ $any -eq 0 ]]; then
    echo "No sync directories found under ${PROJECT_ROOT}." >&2
    exit 1
fi

echo ""
[[ $DRY_RUN -eq 1 ]] \
    && echo "(dry run complete — nothing transferred)" \
    || echo "Done."
