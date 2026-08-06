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

## 6c — Exposure calibration 🚧

The capture pipeline has two paths — a **Calibration path** that measures the
exposure ladder and a **Normal path** that reuses saved exposures (stored in the
`exposure` table, seeded per exposure group). On a fresh DB the `exposure` table
is empty and the capture screen opens on the config table's
`defaults.exposuregroup` row, whose seeded value is group `"3"`.

The operator-facing "Calibrate Exposures" flow is **still being wired up** (UI +
persistence). Until it lands, a new unit runs on the seeded group, which is
adequate for bring-up but not final metrology.

*Placeholder — fill in once the exposure-calibration UI/runner is complete.*

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
| Exposure ladder | `exposure` table | 🚧 UI/runner in progress |
| Color/measurement | analysis output | 🚧 engine not implemented |
| Illumination flat field | `flatfield` table + `.npy` gain maps | 🟡 analysis works; delivery to instrument undesigned |

← [05_testing.md](05_testing.md) · Next → [07_troubleshooting.md](07_troubleshooting.md)
