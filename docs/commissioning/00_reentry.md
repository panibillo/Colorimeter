# Step 0 — Re-entry and refresh

**Status:** ✅ ready · **Where:** local + on the Pi

For units that are **not** starting from a blank SD card: a partially
commissioned Pi, a unit whose history is uncertain, a working unit that needs a
code refresh, or one that was disrupted by a username / password / hostname
change.

Back to [master guide](../RPi_commissioning.md) · Straight to the fresh-unit path → [01_os_image.md](01_os_image.md)

The commissioning steps 1–6 are written as a **linear first build**. This page
covers the other three cases:

| Situation | Go to |
|---|---|
| "I don't know what has been run on this unit, or which versions" | [Part 1 — Take stock](#part-1--take-stock) → [Part 2 — Where to re-enter](#part-2--where-to-re-enter) |
| "I know what I want to redo — is it safe to run again?" | [Part 3 — Repeatability reference](#part-3--repeatability-reference) |
| "I changed the username / password / hostname / app directory" | [Part 4 — Disruptive changes](#part-4--disruptive-changes) |
| "It works; I just want to push new code / re-verify it" | [Part 5 — Refresh recipes](#part-5--refresh-recipes) |

---

## The one rule that applies to all of it

> ### ⚠ Stop the application service before touching the hardware.
>
> ```bash
> # On the Pi:
> sudo systemctl stop colorimeter
> ```
>
> Once step 5d has been done, `colorimeter.service` is running `main.py`, which
> **holds the SPI display, the camera, and the I2C touch bus**. Anything else
> that wants those devices — a hand-run `main.py` (5c), `calibrate_screen.py`
> (6a), `verify_peripherals.sh`'s `i2cdetect` and `rpicam-hello` probes (5a), the
> bench tooling in `dev/` — will contend with it and produce confusing failures
> that look like hardware faults.
>
> `Restart=on-failure` also means a crashed instance comes **back** on its own.
> Stopping is not optional; it is the difference between a clean re-entry and an
> afternoon chasing a phantom.
>
> When you are done: `sudo systemctl start colorimeter` (or `sudo reboot`, which
> is the better proof — see [5e](05_testing.md#5e--full-reboot-test-the-real-proof)).

---

## Part 1 — Take stock

Nothing on the Pi records which commissioning steps have been run. There is no
version stamp, no manifest, no log of the process. So you do not *ask* the unit
what state it is in — you **observe the artifacts** each step leaves behind.

### 1a — What each step leaves behind

| Step | Durable evidence on the Pi |
|---|---|
| 1 — OS image | `uname -m` = `aarch64`; Trixie in `/etc/os-release`; the hostname and login account |
| 2 — Peripherals | `dtparam`/`dtoverlay` lines in `/boot/firmware/config.txt`; `/dev/spidev0.0`, `/dev/spidev1.0`, `/dev/i2c-1`, `/sys/bus/w1/devices/28-*` |
| 3a — App dir | `~/pi/Colorimeter/`, `~/pi/Colorimeter/captures/` |
| 3b — Firmware | `~/pi/Colorimeter/src/*.py`, `config/*.yaml` |
| 3c — Database | `~/pi/Colorimeter/src/colorimeter.sqlite` **with a populated `colorcard` table** |
| 3d — Scripts | `~/pi/Colorimeter/provisioning/*.sh` |
| 3e — System pkgs + venv | apt packages installed; `~/pi/Colorimeter/.venv/`; the `colorimeter login env` block in `~/.bashrc` |
| 4 — SSH | `~/.ssh/authorized_keys` on the Pi; a `Host` stanza in **your local** `~/.ssh/config` |
| 5b — gpio-init service | `/usr/local/bin/colorimeter-gpio-init.sh` + `/etc/systemd/system/colorimeter-gpio-init.service`, enabled |
| 5d — App service | `/etc/systemd/system/colorimeter.service`, enabled |
| 6a — Touch cal | `~/pi/Colorimeter/config/touch_cal.json` |
| 6b — Focus cal | `~/pi/Colorimeter/config/focus_cal.json` (IMX708 only) |

### 1b — Probe the unit (read-only, safe any time)

The same checks are available two ways. **Prefer the script** — it adds a verdict
naming the step(s) to re-enter at, and there is no paste to mangle:

```bash
# On local — push the current script set first (safe on any unit):
./push_pi_scripts.sh <host>

# On the Pi:
~/pi/Colorimeter/provisioning/commissioning_status.sh
```

It needs no `sudo`, changes nothing, works on a unit that has never been
commissioned at all, and exits non-zero if it found gaps. It reports on steps
1–6, flags **duplicated** `config.txt` overlay lines, and specifically detects
the two artifacts a username or home-directory change invalidates — a relocated
`.venv` and a stale `colorimeter.service`. It deliberately leaves venv imports to
`make_venv.sh` and peripheral health to `verify_peripherals.sh`.

Use the paste-able block below instead when `provisioning/` on the Pi is itself
missing or of unknown vintage — which is the case the script cannot bootstrap
itself out of. It changes nothing either.

> **If you land on a `>` prompt, press Ctrl-C.** That is bash's continuation
> prompt: a pasted line arrived with an unclosed quote, and everything you type
> afterwards — including `exit` — is swallowed as more of the string. Ctrl-C
> abandons the line. The block below is written in short, independent lines
> precisely so a mangled paste costs you one command instead of the whole run;
> if a long block ever misbehaves, paste it one `=====` section at a time.

```bash
# On the Pi - read-only commissioning state report.
APP="$HOME/pi/Colorimeter"

echo "===== identity ====================================="
hostname
whoami
echo "HOME=$HOME"
id

echo "===== os / board ==================================="
uname -m
grep PRETTY /etc/os-release
grep -m1 Model /proc/cpuinfo

echo "===== step 2 - config.txt =========================="
grep -nE '^(dtparam|dtoverlay|camera_auto_detect)' /boot/firmware/config.txt
echo "--- buses actually present ---"
ls -d /dev/spidev0.0 /dev/spidev1.0 /dev/i2c-1 2>&1
ls -d /sys/bus/w1/devices/28-* 2>&1

echo "===== step 3 - app tree ============================"
ls -d "$APP" "$APP/src" "$APP/config" "$APP/captures" 2>&1
ls -d "$APP/provisioning" "$APP/.venv" 2>&1

echo "===== step 3c - database ==========================="
ls -l "$APP/src/colorimeter.sqlite" 2>&1

echo "===== step 3d - provisioning scripts ==============="
ls -l "$APP/provisioning/" 2>&1

echo "===== step 3e - venv ==============================="
"$APP/.venv/bin/python3" -c 'import sys; print(sys.prefix)' 2>&1
echo "--- path baked into activate ---"
grep VIRTUAL_ENV= "$APP/.venv/bin/activate" 2>&1
echo "--- bashrc login block (expect 2) ---"
grep -c 'colorimeter login env' "$HOME/.bashrc" 2>&1

echo "===== step 5 - services ============================"
systemctl is-enabled colorimeter.service 2>&1
systemctl is-enabled colorimeter-gpio-init.service 2>&1
systemctl is-active colorimeter.service 2>&1
systemctl is-active colorimeter-gpio-init.service 2>&1
grep -E '^(User|WorkingDirectory|ExecStart)=' \
     /etc/systemd/system/colorimeter.service 2>&1
ls -l /usr/local/bin/colorimeter-gpio-init.sh 2>&1

echo "===== step 6 - calibration ========================="
ls -l "$APP/config/touch_cal.json" 2>&1
ls -l "$APP/config/focus_cal.json" 2>&1
```

Read the output against the table in [1a](#1a--what-each-step-leaves-behind).
Two lines deserve a second look:

- **The `step 3e - venv` paths** — the interpreter's own prefix and every
  `VIRTUAL_ENV=` line from `activate` — must all name the **current**
  `$HOME/pi/Colorimeter/.venv`. If any shows a *different* path, the venv was
  built for a home directory that no longer applies — see
  [Part 4](#4b--the-pi-username-or-home-directory-changed).
- **`User=` / `WorkingDirectory=` / `ExecStart=`** in the service unit must name
  the account you are logged in as. A mismatch is the classic symptom of a
  username change ([Part 4](#4b--the-pi-username-or-home-directory-changed)).

### 1c — Is the database real?

Presence is not enough — a blank DB looks identical to a seeded one from `ls`.

```bash
# On the Pi:
python3 - <<PY
import sqlite3, os
p = os.path.expanduser("~/pi/Colorimeter/src/colorimeter.sqlite")
c = sqlite3.connect(p)
for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
    print(f"  {t:12} rows={c.execute(f'select count(*) from {t}').fetchone()[0]}")
c.close()
PY
```

Expect **9 tables**, with `colorcard` populated (the ROI patches) and
`device`/`defaults` non-empty. An empty `colorcard` means a blank or wrong
`colorimeter.sqlite` — re-enter at [step 3c](03_upload_firmware_db_venv.md#step-3c--upload-the-reference-database-on-local),
**after** reading the overwrite warning in [Part 3](#part-3--repeatability-reference).

### 1d — Which versions are on the Pi?

The Pi has no Git checkout and no version file, so **file content is the only
ground truth**. `rsync --checksum --dry-run` compares the desktop against the Pi
and lists exactly the files that differ:

```bash
# On local, from ~/Projects/Colorimeter:
git log --oneline -3          # what the desktop is at
git status --short            # …and whether it is dirty

./push_pi_scripts.sh -n <host>    # provisioning scripts that differ
./bulk_sync_to_pi.sh   -n <host>  # .py/.yaml/.csv under src/ dev/ config/ that differ
```

Anything **listed** is out of date on the Pi. An empty file list means the Pi
matches your working tree.

> `sync_to_pi.sh` (the curated `sync_pi_files.txt` list) has **no `-n` flag** —
> use `bulk_sync_to_pi.sh -n`, which covers a superset of the same tree, as the
> version probe.
>
> ⚠ **Neither sync script uses `--delete`.** A module you renamed or deleted on
> the desktop is still sitting on the Pi, and can still be imported. If the unit
> behaves like it is running code you no longer have, suspect a stale file:
> compare `ls src/` on both sides, or uncomment the `--delete` line in
> `bulk_sync_to_pi.sh` deliberately.

---

## Part 2 — Where to re-enter

Read the first row that matches and enter there. Everything **after** that entry
point should then be replayed in order.

| What the probe showed | Re-enter at | Why |
|---|---|---|
| `uname -m` ≠ `aarch64`, or not Trixie | **Step 1** — reflash | Nothing downstream is salvageable on a 32-bit / wrong-OS image |
| Peripheral lines missing, duplicated, or a `/dev/spidev*` absent | **Step 2** | Everything above the bus layer will fail confusingly |
| `~/pi/Colorimeter` missing, or `src/` sparse | **Step 3a–3b** | |
| DB missing, or `colorcard` empty | **Step 3c** ⚠ | Read the overwrite warning first |
| `provisioning/` missing, or scripts differ from the repo (1d) | **Step 3d** | Always cheap and safe |
| No `.venv`; or its path ≠ current `$HOME`; or imports fail | **Step 3e** | Delete `.venv` first if the path is wrong |
| `is-enabled` ≠ `enabled`, or `User=`/`ExecStart=` name the wrong account | **Step 5b + 5d** | |
| All artifacts present, but the unit misbehaves | **Step 5a → 5c** | Verify buses, then hand-run for a real traceback |
| Touch lands off-target, or `touch_cal.json` missing | **Step 6a** | Device-specific; never synced between units |
| Username / home / hostname changed | **[Part 4](#part-4--disruptive-changes)** | Follow that checklist, then re-enter where it says |
| Genuinely no idea | **[Recipe B](#b--full-re-verify-of-a-suspect-unit)** | A safe end-to-end replay |

> **When in doubt, replay more rather than less.** Every step except **1**
> (reflash), **2** (hand edit of `config.txt`) and **3c** (database copy) is
> designed to be re-run — see Part 3. Those three are the only ones where a
> blind repeat can cost you something.

---

## Part 3 — Repeatability reference

🟢 repeat freely · 🟡 repeat, but know the side effect · 🔴 do not repeat blindly

### Steps 1–2 — image and peripherals

| Step / action | Repeat? | Notes |
|---|---|---|
| **1** Flash the SD card | 🔴 | **Erases the card.** Rescue the DB, `config/*_cal.json`, and `captures/` first — see [4f](#4f--re-flashing-the-card). |
| **1d** `apt update && full-upgrade` | 🟢 | Always safe, always worth doing on re-entry. |
| **1-alt A** `systemctl enable --now ssh` | 🟢 | |
| **1-alt B** `set-default multi-user.target` | 🟢 | Reversible with `graphical.target`. |
| **1-alt C** `hostnamectl set-hostname` | 🟡 | Idempotent on the Pi, but breaks your local `.local` name and `~/.ssh/config` — see [4c](#4c--the-hostname-or-ip-changed). |
| **2b** Hand-edit `config.txt` | 🔴 | **The #1 duplicate hazard.** Re-running the edit *appends a second copy* of every overlay line. Do not paste the block again — read the file first, and use the [2c](02_peripherals.md#step-2c--sanity-check-the-edit-before-rebooting) `grep -c` check to confirm each line appears exactly once. A duplicate `dtoverlay` can stop the boot. |
| **2a** `cp config.txt config.txt.backup` | 🟡 | Re-running **overwrites your good backup with the current (possibly broken) file**. Back up to a dated name instead: `config.txt.$(date +%F)`. |
| **2c** `grep -c` sanity checks | 🟢 | Read-only. Run these *instead of* re-editing, to confirm step 2 is already correct. |

### Step 3 — firmware, database, venv

| Step / action | Repeat? | Notes |
|---|---|---|
| **3a** `mkdir -p ~/pi/Colorimeter/captures` | 🟢 | `-p` makes it a no-op. |
| **3b** `./sync_to_pi.sh <host>` | 🟢 | rsync + checksum; transfers only differences. ⚠ no `--delete` — stale files persist (see [1d](#1d--which-versions-are-on-the-pi)). |
| **3b-alt** `./bulk_sync_to_pi.sh <host>` | 🟢 | Same; `-n` for dry run. |
| **3c** `scp src/colorimeter.sqlite …` | 🔴 | **Destroys the unit's database.** See the box below. |
| **3d** `./push_pi_scripts.sh <host>` | 🟢 | Pure rsync of `provisioning/deploy/`. Do this early on any re-entry so the scripts you are about to run are the current ones. |
| **3e** `bootstrap_system.sh` | 🟢 | `apt install` is idempotent; re-running just verifies/upgrades. No daemon is installed or enabled. |
| **3e** `make_venv.sh` | 🟡 | Idempotent, and the `~/.bashrc` block is guarded against duplication — **but it *reuses* any existing `.venv`.** It will not repair a venv that was built without `--system-site-packages`, or one built for a different home directory. To force a clean rebuild you must `rm -rf .venv` yourself first. |

> ### 🔴 Step 3c overwrites the instrument's data
>
> `src/colorimeter.sqlite` is not just seed data once the unit has been used. It
> holds **measurement results**, the **exposure calibration** table, and the rows
> that the HDF5 files in `captures/` are indexed against. Copying the reference
> DB over it discards all of that and orphans the capture archives.
>
> Only re-run 3c when [1c](#1c--is-the-database-real) showed the DB **missing or
> unseeded**. If you are unsure, back it up first — it costs seconds:
>
> ```bash
> # On the Pi:
> cp ~/pi/Colorimeter/src/colorimeter.sqlite \
>    ~/pi/Colorimeter/src/colorimeter.sqlite.bak-$(date +%F)
> ```

### Step 4 — SSH

| Step / action | Repeat? | Notes |
|---|---|---|
| **4a** `ssh-keygen -t ed25519` | 🔴 | **Do not regenerate over an existing key.** It replaces the identity every other host already trusts. If `~/.ssh/id_ed25519` exists, skip straight to `ssh-copy-id`. |
| **4a** `ssh-copy-id` | 🟢 | It checks before appending, so a repeat is a no-op. Harmless even if it did duplicate the line. |
| **4a** `cat >> ~/.ssh/config <<EOF` | 🔴 | Re-running **appends a second `Host` stanza**. SSH uses the *first* match per keyword, so a stale earlier stanza silently wins and you connect as the old user or to the old address. **Edit the existing stanza instead of appending.** Check with `grep -n '^Host ' ~/.ssh/config`. |
| **4a** `PasswordAuthentication no` | 🟡 | Lockout risk. Only after key login is confirmed **from a second terminal you keep open**. |
| **4b** Colored-terminal wrapper | 🟢 | Local-only convenience. |

### Steps 5–6 — services, testing, calibration

| Step / action | Repeat? | Notes |
|---|---|---|
| **5a** `verify_peripherals.sh` | 🟡 | Makes no system changes, but it *does* touch live buses (`i2cdetect`, `rpicam-hello`). **Stop `colorimeter.service` first** or the camera/I2C checks may fail against a running app. |
| **5b** `install_gpio_init_service.sh` | 🟢 | Overwrites the script + unit, reloads, re-enables, restarts. Re-run it after any edit to `colorimeter-gpio-init.sh`. **Independent of the login account** — nothing in it references a user or a home directory, so a username change does *not* invalidate it. |
| **5c** Hand-run `.venv/bin/python3 src/main.py` | 🔴 | **Stop the service first.** Two instances fighting over the display, camera, and touch bus is the single most common self-inflicted re-entry failure. |
| **5d** `install_colorimeter_service.sh` | 🟢 | Overwrites the unit and restarts. This is the **correct fix for a stale `User=`/path** — it rebuilds the unit from `whoami` and `$HOME`. Requires `.venv/bin/python3` and `src/main.py` to exist (it sanity-checks both). ⚠ it ends with `systemctl restart`, so it will start a second app instance if you left a hand-run one going. |
| **5d-undo** `uninstall_colorimeter_service.sh` | 🟢 | No-op if already removed. Leaves the gpio-init unit and all files alone. |
| **5e** `sudo reboot` + checks | 🟢 | |
| **6a** `calibrate_screen.py` | 🟡 | Overwrites `config/touch_cal.json` — which is the point. **Stop the service first.** Never synced between units. |
| **6b** Focus sweep | 🟡 | Overwrites `config/focus_cal.json`. Stop the service first. |

---

## Part 4 — Disruptive changes

These are the cases where re-entry is not simply "run step N again", because a
change to identity or paths invalidates artifacts that earlier steps produced.

### What is keyed to what

Knowing where the couplings actually are makes each case short:

| Artifact | Keyed to the **username / home path**? | Keyed to the **hostname**? | Stores the **password**? |
|---|---|---|---|
| `/boot/firmware/config.txt` | no | no | no |
| `~/pi/Colorimeter/` contents | path only | no | no |
| `.venv/` | **yes** — absolute paths baked in | no | no |
| `~/.bashrc` login block | no — it is `$HOME`-relative | no | no |
| `colorimeter.service` | **yes** — `User=`, `WorkingDirectory=`, `ExecStart=` | no | no |
| `colorimeter-gpio-init.service` | **no** — runs as root, absolute `/usr/local/bin` path | no | no |
| `colorimeter.sqlite`, `touch_cal.json` | no | no | no |
| Your local `~/.ssh/config` | **yes** — the `User` line | **yes** — the `HostName` line | no |
| Pi `~/.ssh/authorized_keys` | **yes** — lives in the user's home | no | no |

Three useful consequences fall out of that table:

1. **No commissioned artifact ever stores the login password.** A password
   change invalidates nothing.
2. **The gpio-init service survives everything** except a reflash.
3. **The venv and the app service are the only two things a username or
   home-directory change breaks** — and both have a one-command fix.

### 4a — The Pi login password changed

Nothing to re-run. No script, unit file, or config file on the unit holds the
password. What changes is only how *you* authenticate:

- **SSH with password auth** — use the new password. If your local
  `~/.ssh/config` or a keyring cached the old one, update it there.
- **SSH with keys** — unaffected. Key login does not consult the password.
- **`sudo`** — prompts for the account's *own* password, so the new one is what
  `install_*.sh` and `bootstrap_system.sh` will ask for. This is the only place
  a forgotten password stalls a re-entry.
- **`PasswordAuthentication no` is set** — SSH ignores passwords entirely; only
  `sudo` is affected.

> ⚠ If password auth was your **only** access and the new password is lost, SSH
> cannot help you. Recovery means a keyboard + monitor on the Pi, or mounting the
> SD card's root partition on a Linux machine. Change a password only while you
> still have a second working session open.

### 4b — The Pi username or home directory changed

The consequential one. Decide the route first:

| Route | When it is right |
|---|---|
| **Re-flash** (step 1, letting Imager set the username) | Usually faster and always cleaner. A commissioned unit's only irreplaceable state is the DB, the two `*_cal.json` files, and `captures/` — rescue those ([4f](#4f--re-flashing-the-card)) and you lose nothing. |
| **Rename in place** | When the unit holds data you would rather not move, or you cannot re-image right now. Fiddly: you cannot rename the account you are logged in as, so you need a second admin account or a root console. |

Renaming in place, from a *different* admin session:

```bash
sudo usermod -l <newuser> -d /home/<newuser> -m <olduser>
sudo groupmod -n <newuser> <olduser>     # if a matching user-group exists
```

**Then run this checklist, in order, whichever route you took.** Order matters:
ownership and groups must be right before the venv is rebuilt, and the venv must
exist before the service installer will accept it.

```bash
# ── On the Pi, logged in as the NEW user ────────────────────────────────

# 1. Ownership — needed if files were COPIED rather than moved by `usermod -m`.
sudo chown -R "$(whoami):$(whoami)" ~/pi/Colorimeter

# 2. Group membership — a hand-created account is NOT in the hardware groups
#    that the Imager's first user gets automatically. Compare against a known
#    good account (or the old one, if it still exists) and fix:
groups
sudo usermod -aG sudo,spi,i2c,gpio,video,dialout,plugdev "$(whoami)"
#    Log out and back in for new groups to take effect.

# 3. Rebuild the venv — REQUIRED. make_venv.sh will NOT do this for you.
cd ~/pi/Colorimeter
rm -rf .venv
./provisioning/make_venv.sh

# 4. Rebuild the app service — it re-derives User= and the paths from whoami.
./provisioning/install_colorimeter_service.sh

# 5. gpio-init service: nothing to do. It is root-run and user-independent.
#    Confirm only:
systemctl is-enabled colorimeter-gpio-init.service      # → enabled
```

Why step 3 is not optional: a venv records absolute paths. `.venv/bin/activate`
exports `VIRTUAL_ENV=<old home>/pi/Colorimeter/.venv`, and every console script
in `.venv/bin/` carries an absolute shebang pointing into the old path. Some of
it appears to work after a move — `.venv/bin/python3` is a symlink to the system
interpreter, so it still starts — which makes a half-broken venv far more
confusing than an absent one. Delete and rebuild; with
`--system-site-packages` there is nothing to compile, so it costs a minute.

```bash
# ── On local ────────────────────────────────────────────────────────────

# 6. Update the User line of the EXISTING ~/.ssh/config stanza — edit it,
#    do not append a second one (see Part 3, step 4a).
#    Then re-authorise your key for the new account:
ssh-copy-id -i ~/.ssh/id_ed25519.pub <newuser>@<host>

# 7. Confirm where the sync scripts will actually land.
ssh <host> 'whoami; echo $HOME; ls -d ~/pi/Colorimeter'
```

> ⚠ **The silent-wrong-tree trap.** `sync_to_pi.sh` and `push_pi_scripts.sh`
> write to the literal path `~/pi/Colorimeter`, which the *SSH server* expands to
> the home of whichever account you logged in as. If your `~/.ssh/config` still
> names the old user, and the old home directory still exists, the push
> **succeeds** — into the old tree. You then debug a unit that never received
> your changes. Step 7 above is the check that catches this.

Finally, re-enter the linear path at **[step 5a](05_testing.md#5a--verify-peripherals-scripted)**
and run 5a → 5e.

### 4c — The hostname or IP changed

Only your local machine cares; nothing on the Pi is keyed to the hostname.

```bash
# On local:
grep -n '^Host ' ~/.ssh/config     # find and EDIT the stanza (do not append)
ping -c1 <newhostname>.local
```

You will be asked to accept the host key again under the new name — normal, the
key is per-machine and `known_hosts` is keyed by name.

A **`REMOTE HOST IDENTIFICATION HAS CHANGED`** warning is different: it means the
machine's key itself changed, i.e. the card was re-flashed or a different Pi took
that address. After confirming that is what happened:

```bash
ssh-keygen -R <host>
```

### 4d — The Wi-Fi network or its password changed

The unit simply disappears. Nothing is re-run; it just has to be told the new
credentials, which needs local access:

```bash
# At the Pi's own console (keyboard + monitor):
sudo nmcli device wifi list
sudo nmcli device wifi connect "<SSID>" password "<password>"
hostname -I                                  # confirm it got an address
```

Then reconnect over SSH and re-check your `~/.ssh/config` `HostName` if the
address changed ([4c](#4c--the-hostname-or-ip-changed)).

### 4e — The app directory moved

Same coupling as a username change, minus the identity work. Do items **3, 4,
and 5** of the [4b checklist](#4b--the-pi-username-or-home-directory-changed):
rebuild the venv, rebuild the app service, leave gpio-init alone.

Note that `~/.bashrc`'s login block hard-codes `$HOME/pi/Colorimeter` and simply
goes quiet if that path is gone (it is guarded on the directory existing). If you
moved the app somewhere else entirely, delete the block — the two marker lines
and everything between — and adjust it by hand.

### 4f — Re-flashing the card

Rescue the device-specific state **before** you erase anything. None of it is in
Git, and none of it can be regenerated from the desktop:

```bash
# On local — pull the irreplaceable files off the unit first:
mkdir -p ~/rescue/<host>
scp <host>:~/pi/Colorimeter/src/colorimeter.sqlite   ~/rescue/<host>/
scp <host>:~/pi/Colorimeter/config/touch_cal.json    ~/rescue/<host>/   # may not exist
scp <host>:~/pi/Colorimeter/config/focus_cal.json    ~/rescue/<host>/   # IMX708 only
rsync -av <host>:~/pi/Colorimeter/captures/          ~/rescue/<host>/captures/
```

Then start at [step 1](01_os_image.md) and work straight through. At
[3c](03_upload_firmware_db_venv.md#step-3c--upload-the-reference-database-on-local),
restore the **rescued** DB rather than the reference one if the unit had real
data. Copy the `*_cal.json` files back after 3b — the touch calibration belongs
to that display, so restoring it saves re-running 6a.

---

## Part 5 — Refresh recipes

### A — Code refresh on a working unit

The routine case: new firmware from the desktop, everything else already right.

```bash
# On local:
./sync_to_pi.sh <host>

# On the Pi:
sudo systemctl restart colorimeter
journalctl -u colorimeter -f          # watch it come up; Ctrl-C to stop watching
```

Add `./push_pi_scripts.sh <host>` if you also changed anything in
`provisioning/deploy/`. If the change touched a `provisioning/` script that
writes a unit file, re-run that installer too (both are 🟢).

### B — Full re-verify of a suspect unit

**This is the answer to "I don't know what has been run."** Every command below
is safe to run against a unit in *any* state, in this order. It deliberately
excludes step 1 (reflash), step 2 (the `config.txt` edit) and step 3c (the DB
copy) — the three that can cost you something.

```bash
# ── On local — make sure the Pi has the current code AND scripts ─────────
./push_pi_scripts.sh <host>
./sync_to_pi.sh <host>

# ── On the Pi ────────────────────────────────────────────────────────────
sudo systemctl stop colorimeter          # ← the one rule
cd ~/pi/Colorimeter

# 1. config.txt — VERIFY, do not re-edit (step 2c checks; expect 1 1 1 1)
grep -c '^dtparam=spi=on'     /boot/firmware/config.txt && \
grep -c '^dtparam=i2c_arm=on' /boot/firmware/config.txt && \
grep -c '^dtoverlay=w1-gpio'  /boot/firmware/config.txt && \
grep -c '^dtoverlay=spi1'     /boot/firmware/config.txt

# 2. System packages + venv
./provisioning/bootstrap_system.sh
rm -rf .venv && ./provisioning/make_venv.sh      # drop the rm if the path is unchanged

# 3. Peripherals
./provisioning/verify_peripherals.sh

# 4. Database — check only; copy ONLY if colorcard is empty (see Part 1c)
python3 -c "import sqlite3,os;p=os.path.expanduser('~/pi/Colorimeter/src/colorimeter.sqlite');print('colorcard rows:',sqlite3.connect(p).execute('select count(*) from colorcard').fetchone()[0])"

# 5. Services
./provisioning/install_gpio_init_service.sh

# 6. Hand-run smoke test — Ctrl-C when the entry screen is up and touch responds
.venv/bin/python3 src/main.py

# 7. Boot wiring, then the real proof
./provisioning/install_colorimeter_service.sh
sudo reboot
```

After it comes back, run the [5e](05_testing.md#5e--full-reboot-test-the-real-proof)
checks. A unit that passes this sweep is commissioned, regardless of what its
history was.

### C — Rebuild the venv only

For import errors, a venv built without `--system-site-packages`, or any move of
the home/app directory:

```bash
# On the Pi:
sudo systemctl stop colorimeter
cd ~/pi/Colorimeter
rm -rf .venv
./provisioning/make_venv.sh                       # prints ✓/✗ per import
./provisioning/install_colorimeter_service.sh     # re-point the unit at the new venv
```

### D — Reset the services

When systemd state is the suspect and nothing else:

```bash
# On the Pi:
./provisioning/uninstall_colorimeter_service.sh
./provisioning/install_gpio_init_service.sh
./provisioning/install_colorimeter_service.sh
systemctl is-enabled colorimeter.service colorimeter-gpio-init.service   # both "enabled"
```

---

Back to [master guide](../RPi_commissioning.md) · Fresh build → [01_os_image.md](01_os_image.md) · Failures → [07_troubleshooting.md](07_troubleshooting.md)
