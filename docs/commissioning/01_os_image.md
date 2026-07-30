# Step 1 — Flash the OS onto a new SD card

**Status:** ✅ ready · **Where:** your local desktop/laptop

Goal: a Zero 2 W that boots **headless**, joins Wi-Fi, and accepts SSH on first
power-up — no monitor or keyboard needed. We do this with Raspberry Pi Imager's
built-in customisation, which writes the Wi-Fi, hostname, user, and SSH settings
directly into the image.

← Back to [master guide](../RPi_commissioning.md) · Next → [02_peripherals.md](02_peripherals.md)

> **Took a different route?** If you flashed the **Desktop** image and clicked
> through the graphical first-boot wizard (user / password / timezone) instead
> of using the Imager headless preset below, skip to
> [Alternative path for the Desktop image wizard](#alternative-path-for-the-desktop-image-wizard).
> You reach the same commissioned state — you just enable SSH and drop to
> console by hand, then rejoin the main flow.

---

## What you need

- A microSD card, **16 GB or larger** (the app plus capture archives grow over
  time; 32 GB is comfortable).
- An SD card reader on your desktop.
- **Raspberry Pi Imager** v1.8 or newer (has the OS-customisation dialog).
  Install on Debian/Ubuntu with `sudo apt install rpi-imager`, or download from
  <https://www.raspberrypi.com/software/>.

---

## Step 1a — Launch Imager and choose the image

1. Insert the SD card and start Raspberry Pi Imager.
2. **Choose Device:** `Raspberry Pi Zero 2 W`.
3. **Choose OS:** `Raspberry Pi OS (other)` → **`Raspberry Pi OS Lite (64-bit)`**.
   - *Lite* (no desktop) is correct: this unit runs headless, and the app draws
     to the ILI9341 SPI display, not to an HDMI desktop.
   - Confirm the description says it is based on **Debian 13 (Trixie)** and is
     **64-bit**. If Imager only offers Bookworm, update Imager first.
4. **Choose Storage:** select the SD card. *Double-check this is the card and
   not another drive — it will be erased.*

---

## Step 1b — Customise the OS (the headless preset)

After clicking **Next**, choose **Edit Settings** when asked "Would you like to
apply OS customisation settings?". Fill in:

**General tab**

| Field | Value | Notes |
|---|---|---|
| Set hostname | `<hostname>` | e.g. `rpizero2`. Becomes `<hostname>.local` via mDNS. |
| Set username and password | user `mgwwg`, password `mgwwg` | **Default for a newly commissioned / give-away unit** — see note below. Any username works: the commissioning scripts auto-detect whoever runs them (`whoami`), nothing is hardcoded to a specific name. |
| Configure wireless LAN | your SSID + password | Set **Wireless LAN country** correctly (e.g. `US`) or Wi-Fi may stay disabled. |
| Set locale settings | your timezone + keyboard | Timezone matters: capture archives are named from local time. |

> **Why `mgwwg`/`mgwwg`, and why matching username=password?** This is the
> generic default used when commissioning a unit that may be **given away** to
> someone who isn't a developer — memorable, no secret to transcribe by hand.
> It is **not** a secure credential: anyone on the local network who knows (or
> guesses) the username can log in. That's an acceptable trade for a lab
> instrument on a trusted home/lab LAN with SSH not exposed to the internet —
> but if this unit will sit on a less-trusted network, **change the password
> after first login** (`passwd`) before handing it over. A reference/dev unit
> you keep for yourself (e.g. `rpizero1`) can keep using a personal account
> like `bill` instead — nothing here requires that specific name.

**Services tab**

| Field | Value |
|---|---|
| Enable SSH | ✅ **Use password authentication** (you can switch to keys later — [04_ssh.md](04_ssh.md)) |

> If you already have an SSH public key you want on the unit, choose **Allow
> public-key authentication only** and paste it here — that folds step 4 into
> the image and the unit never accepts password login.

**Options tab** — defaults are fine (eject media when finished is convenient).

Click **Save**, then **Yes** to apply customisation, then **Yes** to erase and
write. Writing + verify takes a few minutes on a Zero-class card.

---

## Step 1c — First boot

1. Eject the card from the desktop and insert it into the Zero 2 W.
2. Power the Pi. **First boot takes 1–3 minutes** — it resizes the filesystem
   and applies the customisation, then reboots once.
3. From your desktop, find and reach it:

```bash
# On local — resolve the mDNS name (works on most home networks):
ping -c1 <hostname>.local

# Log in (password auth, the password you set in Imager):
ssh mgwwg@<hostname>.local
```

At this point, the `hostname` is probably 'raspberrypi', but might be something assigned during burning.
If `<hostname>.local` does not resolve, find the Pi's IP from your router's
DHCP client list (look for the hostname), then `ssh mgwwg@<ip>`.

> **Tip — fix the address.** For a lab instrument you want a stable address.
> The cleanest way is a **DHCP reservation** in your router (bind the Pi's Wi-Fi
> MAC to a fixed IP) — no Pi-side change, survives re-imaging. Add a matching
> `Host` entry to your desktop `~/.ssh/config` (see [04_ssh.md](04_ssh.md)) so
> you can just `ssh <host>`.  

Once you are into the Pi's terminal you can type:

```bash
ip -brief link   # get the unique MAC address of the Pi
hostname - I     # get the current IP address assigned by the router
hostname         # get the current hostname
```
To change the hostname, see below section **C. (Optional) Give it a unique hostname** 

---

## Step 1d — Confirm you have the right machine

```bash
# On the Pi:
hostname
cat /proc/cpuinfo | grep Model      # → "Raspberry Pi Zero 2 W …"
uname -m                            # → aarch64  (confirms 64-bit)
cat /etc/os-release | grep PRETTY   # → Debian 13 (trixie)-based
```

If `uname -m` reports `armv7l` or `armv6l`, you flashed a 32-bit image — go back
to Step 1a and pick the **64-bit** Lite image.

Then update the system before installing anything:

```bash
# On the Pi:
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

---

## Alternative path for the Desktop image wizard

Perfectly fine if you already flashed **Raspberry Pi OS with desktop** (64-bit)
and completed the graphical first-boot wizard — the one that had you pick a
user + password, locale, and timezone, then booted to a desktop. You reach the
same commissioned state. You just do **by hand** the two things the Imager
headless preset ([Step 1b](#step-1b--customise-the-os-the-headless-preset))
would have baked in: **enable SSH**, and — strongly recommended on the 512 MB
Zero 2 W — **stop the desktop from running**.

Do this at the Pi's own terminal (the keyboard/monitor you used for the wizard);
afterwards you can disconnect them and work over SSH.

### A. Enable SSH — this is what makes it headless

The desktop wizard does **not** enable SSH (unlike the Imager "Enable SSH"
checkbox). Turn it on:

```bash
# On the Pi:
sudo systemctl enable --now ssh
systemctl is-enabled ssh      # → enabled
systemctl is-active  ssh      # → active
```

Then, from your desktop, confirm you can get in (accept the host key on first
connect):

```bash
# On local:
ssh <username>@<ip>           # e.g. ssh mgwwg@192.168.0.110
```

Find `<ip>` in your router's DHCP client list, or use `<hostname>.local` if mDNS
resolves. A **DHCP reservation** (see the Step 1c tip) gives you a stable
address and lets you add a `~/.ssh/config` alias ([04_ssh.md](04_ssh.md)).

### B. (Recommended) Boot to console instead of the desktop

The desktop image boots into `graphical.target`, whose Wayland compositor +
greeter (`labwc`/`wayfire`) hold ~150–250 MB of the Zero 2 W's 512 MB and idle
CPU the capture pipeline needs. The app draws to the SPI display and autostarts
via `colorimeter.service` (which lives under `multi-user.target`), so the
desktop buys you nothing on this unit. Drop to console:

```bash
# On the Pi:
sudo systemctl set-default multi-user.target
```

This gives you **effectively Lite runtime performance without re-flashing**.
Caveats — why it is ~95 %, not identical to a fresh Lite image: the desktop
**packages** still sit on the SD card (disk only, harmless at runtime), and a
few desktop-adjacent background daemons may still start. It is fully reversible
with `sudo systemctl set-default graphical.target` (handy while you are still
bench-testing with a monitor).

> **Re-image to Lite instead?** Only if you want a pristine minimal base or the
> card is space-constrained. Since Wi-Fi, SSH, hostname, and the user are already
> set, keeping this image + `multi-user.target` is the pragmatic choice. Do **not**
> try to `apt purge` the desktop down to Lite — dependency removal on Raspberry
> Pi OS is error-prone; a clean Lite flash beats purging if minimalism is the
> goal.

### C. (Optional) Give it a unique hostname

The wizard leaves the hostname as the default `raspberrypi`. For a lab
instrument a unique name avoids `.local` collisions and matches the `rpizeroN`
convention:

```bash
# On the Pi:
sudo hostnamectl set-hostname <hostname>          # e.g. rpizero3
sudo sed -i 's/\braspberrypi\b/<hostname>/g' /etc/hosts
```

### D. Reboot once, then rejoin the main flow

`set-default` takes effect on the next boot, so apply A–C, then reboot a single
time:

```bash
# On the Pi:
sudo reboot
```

After it comes back (SSH in), verify the desktop is gone and confirm the machine:

```bash
systemctl get-default                                 # → multi-user.target
free -h                                                # more "available" RAM than before
systemctl --type=service --state=running --no-pager    # no labwc / greeter / desktop session
```

Then run the same **confirm-machine** and **update** commands from
[Step 1d](#step-1d--confirm-you-have-the-right-machine) above (they apply to both
images) and continue to step 2.

---

Next → [02_peripherals.md](02_peripherals.md) — enable SPI, I2C, 1-Wire, SPI1,
and PWM in `config.txt`.
