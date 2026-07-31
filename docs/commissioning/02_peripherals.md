# Step 2 — Configure peripherals in `config.txt`

**Status:** ✅ ready · **Where:** on the Pi, over SSH

Goal: enable the buses the cal8 mainboard needs — **SPI0** (display), **I2C bus
1** (touch), **1-Wire on GPIO16** (temperature), **SPI1** (74HC595 LED driver),
and leave hardware **PWM** (backlight) available. This is a single edit to
`/boot/firmware/config.txt`, done manually with a backup.

← [01_os_image.md](01_os_image.md) · Back to [master guide](../RPi_commissioning.md) · Re-entering? [00_reentry.md](00_reentry.md) · Next → [03_upload_firmware_db_venv.md](03_upload_firmware_db_venv.md)

> 🔴 **Already been through this step once?** Do **not** paste the block from
> [2b](#step-2b--add-the-cal8-peripheral-block) again — re-running the edit
> appends a *second* copy of every overlay line, and a duplicate `dtoverlay` can
> stop the Pi booting. Run the read-only
> [2c](#step-2c--sanity-check-the-edit-before-rebooting) checks *instead*, and
> only edit what they flag. Same for [2a](#step-2a--locate-and-back-up-the-file):
> re-running the `cp` overwrites your good backup with the current file — back up
> to a dated name instead.

> **Why not scripted?** A malformed `config.txt` can prevent boot. Editing it by
> hand, with a backup, keeps recovery simple: if the Pi won't boot, mount the SD
> card's FAT32 boot partition on any computer and revert the file.
>
> **Deep reference:** the original, heavily annotated walkthrough is
> [edocs/rpi_configuration_guide_for_cal8.md](../../edocs/rpi_configuration_guide_for_cal8.md).
> It covers the same buses for the original Zero W and is the authority for the
> **boot-time GPIO safe-state service** (linked from step 5). This page is the
> shorter Zero 2 W / Trixie version.

---

## Step 2a — Locate and back up the file

On Trixie (Debian 13) the boot config lives on the firmware partition:

```bash
# On the Pi:
ls -l /boot/firmware/config.txt          # confirm the path exists
sudo cp /boot/firmware/config.txt /boot/firmware/config.txt.backup
diff /boot/firmware/config.txt /boot/firmware/config.txt.backup   # no output = identical
```

Inspect what is already present so you don't create duplicates:

```bash
grep -nE 'spi|i2c|w1|dtoverlay|dtparam|pwm|gpio' /boot/firmware/config.txt
```

A stock Zero 2 W image typically already has `dtparam=spi=off` (or `=on`),
`camera_auto_detect=1`, `dtoverlay=vc4-kms-v3d`, `arm_64bit=1`, and an `[all]`
section — that is the skeleton shown in
`provisioning/reference/Rpi-zero-W-2-for-mainboard_C-config.txt`. Leave those lines;
we only add/adjust the peripheral overlays.

---

## Step 2b — Add the cal8 peripheral block

Edit the file:

```bash
sudo nano /boot/firmware/config.txt
```

Make sure there is an `[all]` section (add one at the end if missing) and place
this block under it. **Do not duplicate** any line already present — if
`dtparam=spi=off` exists, change it to `on` rather than adding a second line.
Use no spaces around `=` (write `cs0_pin=23`, never `cs0_pin = 23`).

```ini
[all]
# ── Colorimeter cal8 mainboard ───────────────────────────────
# SPI0: ILI9341 display (MOSI=GPIO10, MISO=GPIO9, SCLK=GPIO11, CE0=GPIO8)
dtparam=spi=on

# I2C bus 1: TSC2007 touch controller (SDA=GPIO2, SCL=GPIO3)
dtparam=i2c_arm=on

# 1-Wire: GPIO16 (non-default; the default would be GPIO4).
# gpiopin= takes the BCM number (16), NOT the physical pin (36).
dtoverlay=w1-gpio,gpiopin=16

# SPI1: 74HC595 LED shift register (MOSI=GPIO20, SCLK=GPIO21).
# cs0_pin=23 moves CE0 off GPIO18 (reserved for JP2/J3 routing).
# The 74HC595 has no hardware CE; RCLK is pulsed manually from GPIO24.
dtoverlay=spi1-1cs,cs0_pin=23

# SPI0 with a single CE, so GPIO7 is NOT claimed as SPI0 CE1 —
# GPIO7 drives LED channel 9 on the cal8 board. See the ⚠ note below.
dtoverlay=spi0-1cs
# ─────────────────────────────────────────────────────────────
```

Save and exit: **Ctrl+O**, Enter, **Ctrl+X**.

> ⚠ **`spi0-1cs` — verify against the running reference unit.** The committed
> cal8 config file
> (`provisioning/reference/Rpi-zero-W-for-main_board_cal8-config.txt`) **omits**
> `dtoverlay=spi0-1cs`, but the detailed cal8 guide **includes** it, because a
> default `dtparam=spi=on` claims **GPIO7 as SPI0 CE1** — and GPIO7 is LED
> channel 9 on this board. Before trusting either, check the working `rpizero1`:
> ```bash
> ssh rpizero1 'grep -n spi0-1cs /boot/firmware/config.txt; pinctrl get 7'
> ```
> If GPIO7 there is a plain input/output (not `a0`/ALT0), the reference unit
> frees it — keep `spi0-1cs`. If it is ALT0 and channel 9 still works, the LED
> is driven a different way and you can drop the line. Match the reference; do
> not change how the shipped hardware behaves.

---

## Step 2c — Sanity-check the edit before rebooting

```bash
# Each of these should print exactly 1 (no duplicates). The leading ^
# anchors to start-of-line so it does NOT match the stock image's commented-
# out placeholders (e.g. "#dtparam=spi=on") — a plain, unanchored grep will
# count those too and falsely report 2.  Expected output: 1 1 1 1 (column-wise)
grep -c '^dtparam=spi=on'        /boot/firmware/config.txt && \
grep -c '^dtparam=i2c_arm=on'    /boot/firmware/config.txt && \
grep -c '^dtoverlay=w1-gpio'     /boot/firmware/config.txt && \
grep -c '^dtoverlay=spi1'        /boot/firmware/config.txt

# 1-Wire must be on BCM 16, not 4/7/36:
grep 'w1-gpio' /boot/firmware/config.txt      # → dtoverlay=w1-gpio,gpiopin=16
```

If any count is `2` or more, re-open the file and delete the older/duplicate
**active** (non-`#`) line — a `2` caused by an unrelated commented-out stock
placeholder is not a problem; see the note above.

---

## Step 2d — Reboot and confirm the buses came up

```bash
sudo reboot
# wait ~30–60 s, reconnect, then:
ls /dev/spidev0.*    # → /dev/spidev0.0  (display, SPI0)
ls /dev/spidev1.*    # → /dev/spidev1.0  (LED driver, SPI1)
ls /dev/i2c-1        # → /dev/i2c-1      (touch bus)
ls /sys/bus/w1/devices/   # → a 28-* entry if the probe is connected
```

Full per-peripheral verification (with expected `pinctrl` states, `i2cdetect`,
temperature read, PWM blink, and the shift-register walk test) is in
[05_testing.md](05_testing.md) and the scripted
`provisioning/verify_peripherals.sh`.

---

## Note — boot-time LED safe state

On the cal8 board the LEDs sit behind the 74HC595, so `config.txt` `gpio=` lines
cannot clear them — a spurious bit can latch an LED on during boot. That is
fixed by a small **systemd service** (`colorimeter-gpio-init.service`) that runs
after boot, writes `0x00` to the shift register, and pulses RCLK. It is created
once during commissioning — see [05_testing.md](05_testing.md) (and the full
recipe in the [cal8 guide, Step 12](../../edocs/rpi_configuration_guide_for_cal8.md)).
`colorimeter.service` is ordered to start **after** it.

---

Next → [03_upload_firmware_db_venv.md](03_upload_firmware_db_venv.md) — put the
code, database, and scripts on the Pi and build the venv.
