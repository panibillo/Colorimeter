#!/usr/bin/env bash
# commissioning_status.sh — read-only report of which commissioning steps have
# actually been carried out on this unit, and where to re-enter the sequence.
#
# Run this ON the Raspberry Pi:
#   ssh <host>
#   ~/pi/Colorimeter/provisioning/commissioning_status.sh
#
# Nothing on a commissioned Pi records which steps were run, so this script
# infers the state from the artifacts each step leaves behind (see
# docs/commissioning/00_reentry.md § 1a for the same table in prose). It is the
# scripted form of the probe block in that document — identical checks, plus a
# verdict naming the step(s) to re-enter at.
#
# It changes nothing, needs no sudo, and is safe to run repeatedly on a unit in
# any state — including one that has never been commissioned at all.
#
# What it deliberately does NOT do:
#   • verify the venv's imports — that is make_venv.sh's job (safe to re-run)
#   • verify the peripherals   — that is verify_peripherals.sh's job
#   • compare code versions against the desktop — the Pi carries no version
#     stamp, so that check only exists on the desktop side:
#       ./push_pi_scripts.sh -n <host>     # provisioning scripts that differ
#       ./bulk_sync_to_pi.sh  -n <host>    # .py/.yaml/.csv that differ
#
# Exit status: 0 if no ✗, 1 otherwise.

# NOTE: no `set -e` — every check must run even when an earlier one fails, and
# on an uncommissioned unit most of them are expected to fail.
set -uo pipefail

PASS=0; FAIL=0; WARN=0
pass() { echo "  ✓ $1"; PASS=$((PASS+1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL+1)); }
warn() { echo "  ⚠ $1"; WARN=$((WARN+1)); }
info() { echo "    $1"; }

# Re-entry points accumulated as problems are found.
REENTRY=()
reenter() {
    local step="$1"
    for e in ${REENTRY+"${REENTRY[@]}"}; do [[ "$e" == "$step" ]] && return; done
    REENTRY+=("$step")
}

# Project root = the parent of this script's directory (provisioning/), matching
# how make_venv.sh derives it. Falls back to the conventional location so the
# script still reports usefully if it is run from a copy elsewhere.
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APP="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [[ ! -d "${APP}/src" && -d "${HOME}/pi/Colorimeter/src" ]]; then
    APP="${HOME}/pi/Colorimeter"
fi
# Optional override, mainly for inspecting a tree from somewhere else:
#   commissioning_status.sh /path/to/Colorimeter
[[ $# -ge 1 ]] && APP="$(cd "$1" 2>/dev/null && pwd || echo "$1")"

VENV="${APP}/.venv"
CONFIG_TXT="/boot/firmware/config.txt"
SVC="/etc/systemd/system/colorimeter.service"
GPIO_SVC="/etc/systemd/system/colorimeter-gpio-init.service"
GPIO_BIN="/usr/local/bin/colorimeter-gpio-init.sh"
ME="$(whoami)"

# Count active (non-commented) config.txt lines matching a pattern.
cfg_count() {
    local n
    n="$(grep -cE "^[[:space:]]*$1" "${CONFIG_TXT}" 2>/dev/null)"
    echo "${n:-0}"
}

echo "==================================================================="
echo " Colorimeter commissioning status — $(hostname), $(date '+%Y-%m-%d %H:%M')"
echo " user: ${ME}   home: ${HOME}   app: ${APP}"
echo "==================================================================="

# ── Step 1 — OS image ───────────────────────────────────────────────────────
echo "Step 1 — OS image:"
arch="$(uname -m)"
if [[ "${arch}" == "aarch64" ]]; then pass "64-bit userland (${arch})"
else fail "not 64-bit (${arch}) — reflash with the 64-bit image"; reenter "1 (reflash)"; fi

osname="$(grep -m1 PRETTY_NAME /etc/os-release 2>/dev/null | cut -d'"' -f2)"
if [[ "${osname}" == *[Tt]rixie* ]]; then pass "OS: ${osname}"
else warn "OS is '${osname:-unknown}', not Trixie — this guide targets Debian 13"; fi
info "board: $(grep -m1 Model /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ //')"

# ── Step 2 — peripherals in config.txt ──────────────────────────────────────
echo "Step 2 — peripherals (${CONFIG_TXT}):"
if [[ -r "${CONFIG_TXT}" ]]; then
    step2_bad=0
    for spec in \
        "dtparam=spi=on|SPI0 (display)" \
        "dtparam=i2c_arm=on|I2C bus 1 (touch)" \
        "dtoverlay=w1-gpio|1-Wire (temperature)" \
        "dtoverlay=spi1-1cs|SPI1 (LED shift register)"
    do
        pat="${spec%%|*}"; label="${spec##*|}"
        n="$(cfg_count "${pat}")"
        if   [[ "${n}" -eq 1 ]]; then pass "${label}: ${pat}"
        elif [[ "${n}" -eq 0 ]]; then fail "${label}: '${pat}' absent"; step2_bad=1
        else fail "${label}: '${pat}' appears ${n}× — DUPLICATE, may stop the boot"; step2_bad=1
        fi
    done

    w1="$(grep -m1 -E '^[[:space:]]*dtoverlay=w1-gpio' "${CONFIG_TXT}" 2>/dev/null)"
    if [[ -n "${w1}" ]]; then
        if [[ "${w1}" == *gpiopin=16* ]]; then pass "1-Wire on BCM 16"
        else fail "1-Wire not on BCM 16: ${w1}"; step2_bad=1; fi
    fi

    n="$(cfg_count 'dtoverlay=spi0-1cs')"
    if   [[ "${n}" -eq 1 ]]; then pass "spi0-1cs present (frees GPIO7 for LED ch 9)"
    elif [[ "${n}" -eq 0 ]]; then warn "spi0-1cs absent — known discrepancy, verify against the reference unit (docs step 2b)"
    else fail "spi0-1cs appears ${n}× — DUPLICATE"; step2_bad=1; fi

    [[ "${step2_bad}" -eq 1 ]] && reenter "2 (config.txt — EDIT BY HAND, do not re-paste the block)"
else
    warn "${CONFIG_TXT} not readable — skipping"
fi

echo "  bus device nodes:"
for dev in /dev/spidev0.0 /dev/spidev1.0 /dev/i2c-1 /dev/gpiochip0; do
    if [[ -e "${dev}" ]]; then pass "${dev}"
    else fail "${dev} missing"; reenter "2 (config.txt) then reboot"; fi
done
probe="$(ls -d /sys/bus/w1/devices/28-* 2>/dev/null | head -1)"
if [[ -n "${probe}" ]]; then pass "1-Wire probe $(basename "${probe}")"
else warn "no 28-* 1-Wire device (usually physical — see docs/DS18B20_troubleshooting.md)"; fi

# ── Step 3a/3b — directories and firmware ───────────────────────────────────
echo "Step 3a/3b — app tree and firmware:"
if [[ -d "${APP}" ]]; then pass "${APP}/"
else fail "${APP}/ missing"; reenter "3a (mkdir -p ~/pi/Colorimeter/captures)"; fi
if [[ -d "${APP}/captures" ]]; then pass "${APP}/captures/"
else fail "${APP}/captures/ missing — first capture will error"
     reenter "3a (mkdir -p ~/pi/Colorimeter/captures)"; fi
for d in "${APP}/src" "${APP}/config"; do
    if [[ -d "${d}" ]]; then pass "${d}/"
    else fail "${d}/ missing"; reenter "3b (./sync_to_pi.sh <host>)"; fi
done

if [[ -f "${APP}/src/main.py" ]]; then
    pass "src/main.py present ($(find "${APP}/src" -maxdepth 1 -name '*.py' | wc -l) .py files in src/)"
else
    fail "src/main.py missing"; reenter "3b (./sync_to_pi.sh <host>)"
fi
for y in instrument.yaml palette.yaml; do
    if [[ -f "${APP}/config/${y}" ]]; then pass "config/${y}"
    else fail "config/${y} missing"; reenter "3b (./sync_to_pi.sh <host>)"; fi
done

# ── Step 3c — database ──────────────────────────────────────────────────────
echo "Step 3c — database:"
DB="${APP}/src/colorimeter.sqlite"
if [[ -f "${DB}" ]]; then
    pass "colorimeter.sqlite present ($(du -h "${DB}" | cut -f1))"
    db_report="$(python3 - "${DB}" <<'PY' 2>&1
import sqlite3, sys
try:
    c = sqlite3.connect(sys.argv[1])
    tables = [r[0] for r in c.execute(
        "select name from sqlite_master where type='table'")]
    n = c.execute("select count(*) from colorcard").fetchone()[0] \
        if "colorcard" in tables else -1
    print(f"OK {len(tables)} {n}")
except Exception as e:
    print(f"ERR {e}")
PY
)"
    read -r tag a b <<<"${db_report}"
    if [[ "${tag}" == "OK" ]]; then
        if [[ "${a}" -eq 9 ]]; then pass "${a} tables (expected 9)"
        else warn "${a} tables — the live schema has 9"; fi
        if   [[ "${b}" -gt 0 ]]; then pass "colorcard seeded (${b} rows)"
        elif [[ "${b}" -eq 0 ]]; then fail "colorcard is EMPTY — blank or wrong DB was copied"
                                    reenter "3c (⚠ overwrites data — back up first)"
        else fail "no colorcard table — wrong DB"; reenter "3c (⚠ overwrites data — back up first)"; fi
    else
        fail "cannot read the database: ${a} ${b}"; reenter "3c (⚠ overwrites data — back up first)"
    fi
else
    fail "colorimeter.sqlite missing"; reenter "3c"
fi

# ── Step 3d — provisioning scripts ──────────────────────────────────────────
echo "Step 3d — provisioning scripts:"
missing=0
for s in bootstrap_system.sh make_venv.sh verify_peripherals.sh \
         install_colorimeter_service.sh uninstall_colorimeter_service.sh \
         install_gpio_init_service.sh colorimeter-gpio-init.sh; do
    if [[ -f "${APP}/provisioning/${s}" ]]; then pass "${s}"
    else fail "${s} missing"; missing=1; fi
done
[[ "${missing}" -eq 1 ]] && reenter "3d (./push_pi_scripts.sh <host>)"
info "note: colorimeter-gpio-init.sh is intentionally not +x here — the"
info "installer gives it +x when copying it to /usr/local/bin."
info "to check these match the desktop: ./push_pi_scripts.sh -n <host>"

# ── Step 3e — venv ──────────────────────────────────────────────────────────
echo "Step 3e — virtual environment:"
if [[ -x "${VENV}/bin/python3" ]]; then
    pass ".venv/bin/python3 present"
    prefix="$("${VENV}/bin/python3" -c 'import sys; print(sys.prefix)' 2>&1)"
    if [[ "${prefix}" == "${VENV}" ]]; then pass "sys.prefix matches: ${prefix}"
    else fail "sys.prefix is '${prefix}', expected '${VENV}'"
         reenter "3e (rm -rf .venv first — see 00_reentry.md § 4b)"; fi

    # A venv bakes absolute paths into activate and into console-script
    # shebangs. After a username / home / app-dir move these still point at the
    # old location, which half-works and is more confusing than an absent venv.
    stale=0
    while read -r p; do
        [[ -z "${p}" ]] && continue
        if [[ "${p}" != "${VENV}" ]]; then
            fail "activate references a stale path: ${p}"; stale=1
        fi
    done < <(grep -o '/[^ ")]*/\.venv' "${VENV}/bin/activate" 2>/dev/null | sort -u)
    [[ "${stale}" -eq 0 ]] && pass "activate references only ${VENV}"

    if [[ -f "${VENV}/bin/pip" ]]; then
        sb="$(head -1 "${VENV}/bin/pip")"
        if [[ "${sb}" == "#!${VENV}/bin/python3"* ]]; then pass "console-script shebangs point at this venv"
        else fail "pip shebang is stale: ${sb}"; stale=1; fi
    fi
    [[ "${stale}" -eq 1 ]] && reenter "3e (rm -rf .venv — make_venv.sh REUSES an existing venv)"

    if [[ -f "${VENV}/pyvenv.cfg" ]] && \
       grep -qi '^include-system-site-packages *= *true' "${VENV}/pyvenv.cfg"; then
        pass "built with --system-site-packages"
    else
        fail "NOT built with --system-site-packages — apt numpy/cv2/h5py/picamera2 are invisible"
        reenter "3e (rm -rf .venv, then make_venv.sh)"
    fi
    info "imports are verified by make_venv.sh (safe to re-run), not here."
else
    fail "no venv at ${VENV}"; reenter "3e (./provisioning/make_venv.sh)"
fi

# ── Step 3e — hardware group membership ─────────────────────────────────────
# bootstrap_system.sh puts the user in these; the Imager's first user already is.
# A hand-created account is NOT (see 00_reentry.md § 4a), and every one of these
# fails silently — the app starts and the affected hardware is simply dead.
# `input` is the one that bites: src/kb_reader.py reads /dev/input/eventX via
# evdev, and a non-member gets an EMPTY device list rather than an error.
echo "Step 3e — hardware group membership (${ME}):"
mygroups="$(id -nG)"
for spec in \
    "input|USB keyboard (kb_reader/evdev)" \
    "spi|SPI0 display + SPI1 LED driver" \
    "i2c|TSC2007 touch" \
    "gpio|backlight, beeper, touch IRQ" \
    "video|camera"
do
    g="${spec%%|*}"; label="${spec##*|}"
    if ! getent group "${g}" >/dev/null 2>&1; then
        info "'${g}' group does not exist on this system — skipping (${label})"
    elif echo "${mygroups}" | tr ' ' '\n' | grep -qx "${g}"; then
        pass "${g} — ${label}"
    else
        fail "NOT in '${g}' — ${label} will not work"
        reenter "3e (sudo usermod -aG input,spi,i2c,gpio,video,dialout,plugdev ${ME}; then log out/in)"
    fi
done
info "evdev/numpy/cv2 imports are verified by make_venv.sh, not here."

n="$(grep -c 'colorimeter login env' "${HOME}/.bashrc" 2>/dev/null)"
case "${n:-0}" in
    2) pass "~/.bashrc login-env block present" ;;
    0) warn "~/.bashrc login-env block absent (cosmetic; make_venv.sh adds it)" ;;
    *) warn "~/.bashrc login-env markers appear ${n}× — expected 2" ;;
esac

# ── Step 4 — SSH (optional) ─────────────────────────────────────────────────
echo "Step 4 — SSH (optional):"
if [[ -s "${HOME}/.ssh/authorized_keys" ]]; then
    pass "authorized_keys holds $(grep -cE '^(ssh|ecdsa)-' "${HOME}/.ssh/authorized_keys") key(s)"
else
    warn "no authorized_keys — password login only (fine; step 4 is optional)"
fi

# ── Step 5 — services ───────────────────────────────────────────────────────
echo "Step 5 — systemd services:"
if [[ -f "${GPIO_SVC}" ]]; then
    pass "colorimeter-gpio-init.service installed"
    en="$(systemctl is-enabled colorimeter-gpio-init.service 2>&1)"
    if [[ "${en}" == "enabled" ]]; then pass "  enabled at boot"
    else fail "  not enabled (${en})"; reenter "5b"; fi
    if [[ -x "${GPIO_BIN}" ]]; then pass "  ${GPIO_BIN} installed +x"
    else fail "  ${GPIO_BIN} missing or not executable"; reenter "5b"; fi
else
    fail "colorimeter-gpio-init.service not installed — LEDs may latch on at boot"
    reenter "5b (./provisioning/install_gpio_init_service.sh)"
fi

if [[ -f "${SVC}" ]]; then
    pass "colorimeter.service installed"
    en="$(systemctl is-enabled colorimeter.service 2>&1)"
    if [[ "${en}" == "enabled" ]]; then pass "  enabled at boot"
    else fail "  not enabled (${en})"; reenter "5d"; fi

    # The unit bakes in User= and absolute paths, so it is the other artifact
    # (besides the venv) that a username or home-directory change invalidates.
    svc_user="$(grep -m1 '^User=' "${SVC}" | cut -d= -f2-)"
    svc_wd="$(grep -m1 '^WorkingDirectory=' "${SVC}" | cut -d= -f2-)"
    svc_exec="$(grep -m1 '^ExecStart=' "${SVC}" | cut -d= -f2-)"
    if [[ "${svc_user}" == "${ME}" ]]; then pass "  User=${svc_user}"
    else fail "  User=${svc_user} but you are ${ME} — username changed?"
         reenter "5d (./provisioning/install_colorimeter_service.sh, as ${ME})"; fi
    if [[ "${svc_wd}" == "${APP}" ]]; then pass "  WorkingDirectory=${svc_wd}"
    else fail "  WorkingDirectory=${svc_wd}, expected ${APP}"
         reenter "5d (./provisioning/install_colorimeter_service.sh, as ${ME})"; fi
    if [[ "${svc_exec}" == "${VENV}/bin/python3 ${APP}/src/main.py" ]]; then
        pass "  ExecStart uses this venv"
    else
        fail "  ExecStart=${svc_exec}"
        reenter "5d (./provisioning/install_colorimeter_service.sh, as ${ME})"
    fi

    act="$(systemctl is-active colorimeter.service 2>&1)"
    if [[ "${act}" == "active" ]]; then
        pass "  currently active (running)"
        warn "  the app holds the display, camera and I2C bus — run"
        info "  'sudo systemctl stop colorimeter' before any bench work"
        info "  (hand-running main.py, calibrate_screen.py, verify_peripherals.sh)"
    else
        info "  currently ${act}"
    fi
else
    fail "colorimeter.service not installed — the app will not start at boot"
    reenter "5d (./provisioning/install_colorimeter_service.sh)"
fi

# ── Step 6 — calibration ────────────────────────────────────────────────────
echo "Step 6 — calibration artifacts:"
if [[ -f "${APP}/config/touch_cal.json" ]]; then pass "touch_cal.json present"
else fail "touch_cal.json missing — touch will be uncalibrated"; reenter "6a (calibrate_screen.py)"; fi
if [[ -f "${APP}/config/focus_cal.json" ]]; then pass "focus_cal.json present"
else warn "focus_cal.json absent — expected on IMX219 (fixed focus); needed for IMX708"; fi

# ── Verdict ─────────────────────────────────────────────────────────────────
echo "-------------------------------------------------------------------"
echo "  passed: ${PASS}   failed: ${FAIL}   warnings: ${WARN}"
if [[ "${FAIL}" -eq 0 ]]; then
    echo "  No gaps found — this unit looks fully commissioned."
    echo "  Peripheral health is a separate check: ./provisioning/verify_peripherals.sh"
else
    echo ""
    echo "  Re-enter the commissioning sequence at:"
    for e in ${REENTRY+"${REENTRY[@]}"}; do echo "    • step ${e}"; done
    echo ""
    echo "  Work from the LOWEST step listed and replay forward."
    echo "  Steps 1, 2 and 3c are the only ones that are NOT safe to repeat"
    echo "  blindly — read docs/commissioning/00_reentry.md Part 3 first."
fi
echo "  Full guide: docs/commissioning/00_reentry.md"
exit $(( FAIL > 0 ? 1 : 0 ))
