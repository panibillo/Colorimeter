# camtest5.py — Usage Guide

## Purpose

`camtest5.py` captures proof-of-concept image sets on the Raspberry Pi using
all 5 LED colors of the colorimeter lighting board. For each color it
auto-exposes and saves a Bayer image (`.npy`), a JPEG (`.jpg`), and a metadata
file (`.txt`). Optional bracketed (over / under) exposures are added via
command-line flags.

With the default 5 colors and no brackets, one run produces **15 files**
(5 colors × 3 file types). Adding 2 over + 1 under bracket yields **60 files**
(5 × 4 × 3).

---

## Hardware Setup

1. Connect the colorimeter lighting board (mainboard_C + colorboard_1) to the
   Raspberry Pi as described in `blink.py`.
2. Confirm GPIO pins are pre-configured in `/boot/firmware/config.txt`:

   ```
   gpio=5=op,dl
   gpio=6=op,dl
   gpio=22=op,dl
   gpio=26=op,dl
   gpio=27=op,dl
   ```

3. Attach the Raspberry Pi Camera Module and enable it via `raspi-config`.
4. Place the test subject on the stage. The stage should be otherwise dark —
   the LEDs are the only illumination source.

---

## Installing Dependencies (RPi, first time only)

```bash
sudo apt update
sudo apt install python3-picamera2 python3-gpiozero python3-numpy python3-opencv
```

---

## Copying the Script to the RPi

From your desktop:

```bash (desktop)
scp dev/camtest5.py rpizero2:~/pi/Colorimeter/camtest5.py

```

Or using rsync:

```bash (desktop)
rsync -avz dev/camtest5.py rpizero2:~/  
```

---

## Running the Script

SSH into the Pi, then:

```bash (pi)
python3 camtest5.py <test_name> [--over PCT [PCT ...]] [--under PCT [PCT ...]] [-j] [-w]
```

### Arguments

| Argument | Required | Description |
|---|---|---|
| `test_name` | Yes | Short identifier for this session. Used in all filenames and as the output directory name. No spaces. |
| `--over PCT ...` | No | One or more over-exposure percentages. `--over 50` captures an image at 1.5× the nominal exposure. |
| `--under PCT ...` | No | One or more under-exposure percentages. `--under 50` captures an image at 0.5× the nominal exposure. |

### Examples

```bash
# Nominal only (5 colors × 1 exposure × 3 files = 15 files)
python3 camtest5.py vial_A

# Nominal + two over brackets (5 × 3 × 3 = 45 files)
python3 camtest5.py vial_A --over 50 100

# Nominal + one over + one under (5 × 3 × 3 = 45 files)
python3 camtest5.py test1 --over 50 --under 50

# Full bracket set (5 × 5 × 3 = 75 files)
python3 camtest5.py sample1 --over 25 50 100 --under 25 50
```

---

## What the Script Does

For each LED color in order (White → Red → Green → Blue → Violet):

1. Turns the LED on and waits 0.5 s for it to stabilize.
2. Auto-exposes: captures a probe frame and scales the exposure so the
   highest-SNR channel reaches 70 % of full scale (mean ≈ 178 / 255).
   Repeats until the recommendation is within 1 % of the applied exposure
   (up to 5 iterations). Reports each iteration's channel means and SNR values.
3. Applies the converged nominal exposure, then captures and saves one image set.
4. Captures and saves any over-exposure brackets (lowest percentage first).
5. Captures and saves any under-exposure brackets (lowest percentage first).
6. Turns the LED off immediately.

The camera is stopped and all LEDs are turned off in a `finally` block,
guaranteeing they are off even if the script crashes or is interrupted with
Ctrl-C.

---

## Output File Naming

```
{test_name}_{color}_{ms}ms[_over|_under].{ext}
```

| Example filename | Meaning |
|---|---|
| `vial_A_Blue_483ms.jpg` | Nominal exposure, Blue LED, 483 ms |
| `vial_A_Blue_483ms.npy` | Same capture — raw Bayer data |
| `vial_A_Blue_483ms.txt` | Same capture — metadata |
| `vial_A_Blue_724ms_over.jpg` | 50 % over-exposed bracket |
| `vial_A_Blue_241ms_under.jpg` | 50 % under-exposed bracket |

All files are placed in a subdirectory named after `test_name`:

```
vial_A/
├── vial_A_White_312ms.jpg
├── vial_A_White_312ms.npy
├── vial_A_White_312ms.txt
├── vial_A_Red_198ms.jpg
├── vial_A_Red_198ms.npy
├── vial_A_Red_198ms.txt
...
```

---

## Metadata File Contents

Each `.txt` file records:

- Session identifiers: `test_name`, `led_color`, `bracket`
- Requested exposure: `requested_exposure_us`, `requested_exposure_ms`
- Image dimensions: `image_width`, `image_height`
- Per-channel BGR statistics: mean and std for B, G, R channels
- Bayer array statistics: `bayer_dtype`, shape, min, max, mean
- Full Picamera2 metadata block (actual `ExposureTime`, `AnalogueGain`, etc.)

---

## Copying Results Back to Desktop

After the run completes, retrieve the files from your desktop:

```bash
# Using scp (copies the whole directory in one step)
scp -r rpizero2:~/pi/colorimeter/vial_A/ images/

# Using rsync (faster on re-runs; skips unchanged files)
rsync -avz rpizero2:~/pi/colorimeter/vial_A/ images/vial_A/
```

The `images/` directory on your desktop is the standard location for test
images in this project.

---

## Camera Configuration

The script uses Config 5 only: **dual-stream RGB + Bayer, 1640 × 1232**.

| Setting | Value |
|---|---|
| Main stream | RGB888, 1640 × 1232 |
| Raw stream | SBGGR10 (10-bit, unpacked uint16) |
| Auto-exposure | Off (manual, converged) |
| Analogue gain | 1.0 |
| White balance | Off (gains 1.0, 1.0) |
| Noise reduction | Off |
| Sharpness | 0.0 |

---

## LED Pin Assignments

| Color | GPIO pin |
|---|---|
| White | 22 |
| Red | 5 |
| Green | 6 |
| Blue | 26 |
| Violet | 27 |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `WARNING: image nearly black` | LED not lighting / lens cap on | Check wiring and GPIO config |
| Exposure clamped to 900 000 µs | Illumination too dim | Add more light or accept the clamp |
| Exposure clamped to 1 000 µs | Illumination too bright | Reduce drive current or add ND filter |
| `picamera2 not available` | Not running on RPi | Run on the Pi |
| JPEG files are empty placeholders | `opencv-python` not installed | `sudo apt install python3-opencv` |
| `gpiozero` import warning in IDE | Desktop lacks gpiozero | Expected — handled by try/except; safe to ignore |
