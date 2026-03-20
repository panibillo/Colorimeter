# Colorimeter Project — Detailed Development Narrative

Chronological record of significant decisions, files, and git changes.
For the high-level story see [Narrative.md](Narrative.md).

---

## 2026-03-05 00:00  Project conception and design discussions

**Context:** Pre-git design phase.

**Files:** `docs/colorimeter_Chat_2026-03-05.odt`, `docs/chat.txt`

Defined the instrument goal: a colorimeter dedicated to reading 8 mm square
color-indicator patches on chemical test strips. Key design decisions made here:

- Platform: Raspberry Pi Zero 2 W + Pi Camera v2 (IMX219 sensor)
- Imaging: raw Bayer (BGGR) for analysis; RGB stream for display and debugging
- Stage: dark enclosure, fixed focal length, LED-only illumination
- Calibration card imaged in every reading for frame-by-frame normalization
- Precision target: 2.5–3 significant digits (matches chemical method limits)
- Storage: SQLite on-device; Python ≥ 3.10

Also discussed: Edmonds Optical polarization notes (`Edmonds_Optical_polarization.odt`),
electronics calculations (`Electronics_calcs.ods`), and early target image design
(`Target_image1.svg`).

---

## 2026-03-06 17:44  First concept code (pre-git)

**Files added:** `dev/colorimeter.py` (not committed)

Very early sketch: placeholder `lights` class (on/off stubs), bare `IMX219`
class wrapping Picamera2. Used as a scratchpad before the first real capture
attempt.

---

## 2026-03-06 17:49  Bayer CSV analysis sketch (pre-git)

**Files added:** `dev/analysis1.py`, `dev/b1.csv`

Explored how to read a saved Bayer CSV and extract the four RGGB channels
(R, G1, G2, B) from interleaved pixel positions. Informed the later channel
extraction design.

---

## 2026-03-06 21:36  **Git commit 1** — `First commit. Just testing the camera`

**Hash:** `c368487`
**Files:** `src/t1.py` (+242 lines)

`t1.py` (named `scientific_colorimeter_capture.py` internally) is the first
working capture script on the RPi. Key features at this point:

- Dual-stream configuration: RGB888 main + SBGGR10 raw
- All ISP processing disabled (AE off, AWB off, noise reduction off,
  sharpness 0)
- Fixed analogue gain = 1.0, neutral colour gains
- Saves RGB as 16-bit PNG and Bayer as `.npy`
- Uses `try/except` to fall back to `sim_picamera2` on desktop
- Placeholder `calibrate_wb()` method (not yet exercised)

---

## 2026-03-06 21:56  **Git commit 2** — `Works: added automatic exposure. 2 unused methods...`

**Hash:** `7c32468`
**Files:** `src/t1.py` (210 ins / 32 del)

Added `estimate_exposure()`: captures a probe frame, scales current exposure
so the reference channel (blue by default) reaches 70 % of full scale, clamps
to min/max limits. This function's logic is carried forward into every later
iteration of the capture scripts.

Two methods noted as unused at this stage: `decode_bayer_packed()` (for
CSI2P packed format) and a white-balance helper. Both were eventually dropped.

---

## 2026-03-09 17:45  **Git commit 3** — `camtest1 to 3 work, though 3 is not all tested`

**Hash:** `df553c5`
**Files:** `dev/camtest1.py`, `dev/camtest2.py`, `dev/camtest3.py`,
           `dev/camtest_x1.py`; `src/t1.py` renamed/moved to `dev/camtest3.py`

`t1.py` was reorganized into a suite of focused scripts:

| Script | Purpose |
|---|---|
| `camtest1.py` | Interactive 640×480 RGB capture; Ctrl-Z safe |
| `camtest2.py` | Multi-config interactive capture (configs 1–6 defined) |
| `camtest3.py` | Single scientific capture with auto-exposure (evolved from t1.py) |
| `camtest_x1.py` | **Abandoned.** Tried placing SBGGR10_CSI2P in the main stream |

**Key discovery — `camtest_x1.py` abandoned:**
Configs 4–6 in `camtest_x1.py` used `SBGGR10_CSI2P` (packed 10-bit) in the
`main` stream. The camera rejected this with:
```
Bad format SBGGR10_CSI2P in stream main
```
The packed format is only valid in the `raw` stream. The correct approach
(confirmed later) is to use a dual-stream config with `RGB888` in `main`
and `SBGGR10` (unpacked) in `raw`.

---

## 2026-03-10 09:23  **Git commit 4** — `working on figuring out unpacking of Bayer images`

**Hash:** `9729312`
**Files:** `dev/camtest4.py` (+624 lines)

Introduced `camtest4.py` as a comprehensive test-suite generator covering
all six camera configurations (RGB-only and dual-stream). Purpose was to
capture reference images for the desktop mock and to diagnose the Bayer
unpacking problem.

At this commit the script loops all 6 configs, estimates exposure per config,
and saves RGB (`.jpg`), Bayer (`.npy`), and metadata (`.txt`). The Bayer
data format question was still being resolved.

---

## 2026-03-10 11:45  **Git commit 5** — `Now saving unpacked bayer (16bit). visual check with test_bayer_display()`

**Hash:** `21d037a`
**Files:** `dev/camtest4.py` (restructured), `test/test_bayer_display.py` (+358 lines)

**Bayer format resolved:** Confirmed that `SBGGR10` in the raw stream delivers
already-unpacked uint16 values (0–1023). No manual bit-shift unpacking needed.
The earlier `SBGGR10_CSI2P` packed format required manual unpacking and only
worked in the raw stream, not the main stream.

`test_bayer_display.py` was added as a visual sanity check: loads a saved
`.npy` Bayer array and displays the four channels (B, Gr, Gb, R) side by side
using matplotlib. This became the primary tool for verifying that saved Bayer
data is meaningful.

---

## 2026-03-11 17:43  Main-loop architecture prototype (not committed)

**Files:** `dev/main_pattern1.py`

A ~120-line architecture sketch demonstrating: event loop, simple state
machine, manager pattern, button handling, CLI compatibility, and clean
shutdown — no frameworks, no threads, no asyncio. Written as a reference
pattern for the eventual `main.py`.

---

## 2026-03-12 08:32  **Git commit 6** — `Added test/*.py, early versions of src/*.py, most .md`

**Hash:** `8669e86`
**Files:** 15 files added (+2449 lines total)

Major commit establishing the production module structure and documentation:

| File | Role |
|---|---|
| `CLAUDE.md` | AI collaboration guidelines; points to 3 doc files |
| `ARCHITECTURE.md` | Module org, data flow, DB lifecycle, platform abstraction |
| `CODING_GUIDELINES.md` | Style, docstrings (Google format), error handling rules |
| `PROJECT_OVERVIEW.md` | Purpose, measurement principles, calibration design |
| `QUICK_START_CHECKLIST.md` | Developer onboarding checklist |
| `src/color_cam.py` | Camera abstraction; dual-stream capture; `try/except` import |
| `src/mock_Picamera2.py` | Desktop mock; loads stored `.npy`/`.jpg` files |
| `src/roi.py` | ROI detection and calibration card grid recognition |
| `src/db_manager.py` | Lightweight SQLite wrapper (no ORM) |
| `src/error_handler.py` | Exception catching, logging, graceful shutdown |
| `src/logging_manager.py` | Unified logging to console + file |
| `dev/CAMTEST4_GUIDE.md` | Usage guide for camtest4.py (later superseded) |
| `test/__init__.py` | Test package init |

---

## 2026-03-12 10:00–10:34  Noise and ROI gap analysis (not committed)

**Files:** `dev/analyze_noise.py`, `dev/analyze_gaps.py`,
           `dev/test_preprocessing.py`, `dev/test_interpolation.py`,
           `dev/test_params.py`, `dev/test_simple.py`, `dev/verify_solution.py`,
           `dev/NOISE_HANDLING_IMPLEMENTATION.md`

During initial testing of `roi.py` against real calibration card images,
two problems appeared:

1. **Noise in line detection:** Edge-detection found spurious lines in
   low-contrast regions of the calibration card.
2. **Gap interpolation:** Some expected grid lines were missing, causing
   incorrect row/column counts.

Several analysis and test scripts were written to diagnose and fix these.
`NOISE_HANDLING_IMPLEMENTATION.md` records what was changed in `roi.py`:
- `interpolate_missing_lines()` — detects anomalously large gaps (> 1.8×
  median spacing) and inserts interpolated lines to recover split rows
- Adjustments to preprocessing thresholds

The scripts in `dev/test_*.py` and `dev/verify_solution.py` are desktop-
runnable and load saved images from `mock_camera/`. They are not part of
the committed test suite but remain available for further debugging.

---

## 2026-03-13 18:01  Calibration design (not committed)

**Files:** `dev/calibration_1.py`

Designed the calibration processing pipeline. Core design principle: images
captured under different LED colors or at different LED drive temperatures are
**not interchangeable** and must be calibrated independently. Each
`(led_color, led_temperature)` pair defines its own colour space and is stored
as a separate group in an HDF5 file.

This file is not yet integrated with the rest of the system.

---

## 2026-03-18 22:05  LED control and multi-color capture (`blink.py` + `camtest4.py` rewrite)

**Files modified/added:** `dev/blink.py`, `dev/camtest4.py` (full rewrite),
                           `dev/CAMTEST4_GUIDE.md` (full rewrite)

**`blink.py`** established the GPIO pin assignments for the 5-color LED board
on `mainboard_C`:

| Color | GPIO |
|---|---|
| White | 22 |
| Red | 5 |
| Green | 6 |
| Blue | 26 |
| Violet | 27 |

**`camtest4.py` rewritten** from the all-configs test-suite generator into a
focused multi-color POC image capture tool. The old version (commit 5) served
its purpose of validating Bayer format and populating `mock_camera/`. The new
version:

- Captures only Config 5 (1640×1232 dual-stream) — the chosen production config
- Cycles through all 5 LED colors, one at a time
- Auto-exposes per color using highest-SNR channel as guide (iterates until
  < 1 % change)
- Supports bracketed over/under exposures via `--over` / `--under` CLI flags
- LEDs on 0.5 s before first capture; off immediately after last capture for
  that color
- `LedController` class with `try/except` fallback for desktop (no GPIO)
- `try/finally` guarantees camera stopped and LEDs off on any exit path
- Naming: `{test_name}_{color}_{ms}ms[_over|_under].{ext}`
- All files written into `{test_name}/` subdirectory

For 5 colors × 3 exposures (1 nominal + 1 over + 1 under): 45 files.

**`CAMTEST4_GUIDE.md`** rewritten to match the new tool: hardware setup,
dependency install, CLI examples, output structure, file naming table,
metadata contents, and `scp`/`rsync` retrieval instructions.

---

## Status as of 2026-03-19

### Committed and working
- Dual-stream RGB + Bayer capture (Config 5, 1640×1232, SBGGR10)
- Auto-exposure with SNR-guided channel selection and convergence loop
- 5-color LED GPIO control with safety fallback
- Systematic multi-color bracketed image capture tool
- Desktop mock camera (`mock_Picamera2.py`) for offline algorithm development
- Full project documentation and architecture

### In development (not committed or incomplete)
- `roi.py` — ROI detection, calibration card grid recognition, gap interpolation
- `calibration_1.py` — Per-LED calibration processing pipeline
- `color_cam.py` — Camera abstraction module (stub committed, not fully integrated)
- `db_manager.py` — SQLite wrapper (stub committed, no schema applied yet)

### Not yet started
- Color analysis pipeline (`color_analysis.py`, `color_map.py`)
- Main event loop and state machine (`main.py`, `state_machine.py`)
- Web interface (`web_interface.py`)
- Physical interface (GPIO buttons, character display)
- Full calibration workflow (`calibration_workflow.py`)
- Report generation (`report.py`)
