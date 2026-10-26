# Step 6 — Calibration

**Status:** 🚧 partial — the instrument's analysis/exposure calibration is still
under active development. Sections below are marked ✅ (usable now), 🟡 (present,
evolving), or 🚧 (not implemented yet — placeholder).

Each colorimeter unit needs several distinct calibrations. They are independent;
do the ones that are ready and revisit this page as the software lands.

← [05_testing.md](05_testing.md) · Back to [master guide](../RPi_commissioning.md) · Re-entering? [00_reentry.md](00_reentry.md) · Next → [07_troubleshooting.md](07_troubleshooting.md)

> ⚠ **Stop the app first:** `sudo systemctl stop colorimeter`. Both calibrations
> below drive the display/camera directly and will contend with a running
> `main.py`. Restart it (or reboot) when you are done.

---

## 6a — Touchscreen calibration ✅

Maps raw TSC2007 touch coordinates to display pixels. **Device-specific** — it
must be run on each unit and is never synced between units (`sync_pi_files.txt`
deliberately excludes `config/touch_cal.json`).

```bash
# On the Pi:
cd ~/pi/Colorimeter
.venv/bin/python3 src/calibrate_screen.py
```

Follow the on-screen targets. The result is written to
`config/touch_cal.json` on the Pi, which the app reads at startup. Re-run it if
touches land off-target or after replacing the display.

---

## 6b — Camera focus calibration 🟡

For the IMX708 (Camera Module 3, autofocus), `src/bayer_cam.py` can run an
autofocus sweep, lock the lens, and save the position to
`config/focus_cal.json` (also device-specific, written on the Pi, never synced).
The IMX219 (Module 2) is fixed-focus and needs no software step — it is focused
mechanically.

- IMX708: the app restores the saved focus on start; trigger/save a fresh sweep
  via the capture tooling (`bayer_cam.focus()` / `autofocus_once()`).
- A dedicated operator-facing focus routine is still being finalized; until then,
  set focus through `dev/camtest8.py` on the bench.

*(This section will get exact commands once the focus workflow is fixed.)*

---

## 6c — Exposure calibration 🟡

Every stored exposure bracket is **tested** on a calibration test pad and the
gray chips, so none of them is over- or under-exposed. When the pad is too
contrasty for one exposure to suit every patch, the brackets are **spread**
across the pad instead (see "Spread brackets" below). The method, the window
and the storage are described in
[docs/exposure_calibration_2026-10-26.md](../exposure_calibration_2026-10-26.md).
This section is the procedure.

**Before the first run**

1. Put the **calibration test pad** (white, yellow and brown patches) in the
   card holder next to the gray chips.
2. Place its three regions: **Menu → Capture → Pad areas**. The first time,
   the viewer opens with the sample region split into three equal bands (top
   yellow, middle brown, bottom white). Select a patch, press Edit, and move its
   edges onto the real patch as in Menu → View Image; Save keeps it (the old
   rows stay as history). Exit returns to the Capture page. Until all three
   exist a run stops at once with a message naming the missing ones, before any
   LED is lit.
3. Check the window under **Menu → Capture** (High DN, Low DN, High %, Low %).
   The seeded 100 / 850 DN at the 10th / 90th percentile are placeholders.

**Running it** (Menu → Capture)

| Control | Effect |
|---|---|
| LED color bar | Choose an LED. The boxes then show that LED's own window and the Bayer layers that must pass for it light up. |
| Bayer layer column (R, Gr, Gb, B) | Tap to add or drop a layer for the chosen LED. |
| Calibrate suite | Fits every LED with its own layers and window. Clears the chosen LED. |
| Calibrate color | Fits only the chosen LED (one to four layers). |
| Show | Photographs the test pad and plots its sorted signal against the window. |
| Pad areas | Opens the image viewer on the three test-pad patches. |
| SAVE | Writes the settings and the fitted brackets. A single-LED fit saved this way is used in operation; a later suite run replaces it. |
| CANCEL | Discards everything on the page. |

While a fit runs, the line above the buttons reports the LED and the rung
(`green calibrating rung 3 of 7 ...`) and the LED being fitted has a black
border. The screen does not take touches during one rung, so CANCEL acts
between LEDs.

**Spread brackets.** If white and brown differ by more than the window allows
(for example blue on layer B, where brown needs 12177 µs and white allows
7673 µs), no single exposure can put every patch in range. The fit then runs
the brackets from the white-limited exposure (nothing clips, brown may be dim)
to the brown-limited one (brown is in range, white may clip). Each bracket
passes only the patches it can see; together they cover the whole pad. The
page ends with `x of y fitted. n spread. SAVE to keep.`, and the stored
remarks end in `spread`. This is normal for high-contrast channels, not a
failure.

**If an LED does not fit.** The page ends with the reason for the first LED
that failed. A group is dropped from that LED when:

- a patch shows no signal even at the longest exposure (`pad_brown shows no
  signal ...`): check the pad is in place and the patch region is on it;
- the band between the limits is too narrow to keep neighbors 1.2× apart for
  that many brackets (`too narrow for 5 brackets`): smaller groups may still
  be kept. Widen the window or use a pad with less contrast.

The log names the limiting patch.

---

## 6d — Color-card / measurement calibration 🚧

Converts LED-illuminated color-card readings into a reportable result. The ROI
rectangles come from the `colorcard` table (seeded in the reference database,
step 3c), but the **analysis engine that turns pixels into a measurement is not
implemented yet**. There is nothing to calibrate here until it exists.

*Placeholder — depends on the analysis engine. Do not attempt to document a
procedure until the engine defines what is being calibrated against what
standard.*

---

## 6e — Illumination flat field 🟡

Measures each LED's illumination pattern across the imaging area and fits a
polynomial, so a sample ROI and a reference ROI at different places in the
frame can be compared without the gradient biasing the result. **Per-unit** —
the lightboard, lens and stage geometry differ between units.

Captures are taken on the Pi; the analysis runs on the desktop from the synced
archives. Full procedure, including the capture protocol and how to read the
results:

→ **[../flatfield_calibration.md](../flatfield_calibration.md)**

```bash
# desktop, from the repo root — verify the harness first
.venv/bin/python3 devdesk/flatfield.py --selftest
```

Result lands in `devdesk/devdesk.sqlite` table `flatfield` plus `.npy` gain maps
under `devdesk/flatfield/<suite_id>/`. Getting those onto the instrument is not
yet designed.

---

## Summary

| Calibration | Artifact | State |
|---|---|---|
| Touchscreen | `config/touch_cal.json` (on Pi) | ✅ run `calibrate_screen.py` |
| Camera focus | `config/focus_cal.json` (on Pi, IMX708) | 🟡 via bench tooling |
| Exposure brackets | `exposure` table | 🟡 working; needs the physical test pad and tuned window |
| Color/measurement | analysis output | 🚧 engine not implemented |
| Illumination flat field | `flatfield` table + `.npy` gain maps | 🟡 analysis works; delivery to instrument undesigned |

← [05_testing.md](05_testing.md) · Next → [07_troubleshooting.md](07_troubleshooting.md)
