# Colorimeter Project Narrative

High-level chronological record of progress. Updated manually.
See [Narrative_detailed.md](Narrative_detailed.md) for file-level detail.

---

## 2026-03-05  Project Design

Defined the core goal: a scientific-grade colorimeter running on a Raspberry
Pi Zero 2 W that reads chemical indicator strips by imaging them with a Pi
Camera v2. Settled on key design choices: raw Bayer capture (minimal ISP
processing), dual-stream output (RGB for display + Bayer for analysis), a dark
imaging stage with controlled LED lighting, and a color calibration card
imaged alongside every reading.

---

## 2026-03-06  First Working Camera Capture

Got a dual-stream RGB + Bayer capture running on the RPi. Implemented
automatic exposure that scales the shutter time to bring a target channel to
70 % of full scale. Verified images could be saved to disk. Noted that some
Bayer decode and white-balance methods were not yet exercised.

---

## 2026-03-09  Capture Script Reorganization

Restructured the camera test code into separate scripts for different purposes
(interactive low-res capture, multi-config exploration). Discovered that the
packed Bayer format `SBGGR10_CSI2P` fails when placed in the camera's main
stream — it is only valid in the raw stream. This blocked saving Bayer data
in several configurations and drove the switch to the unpacked `SBGGR10`
format.

---

## 2026-03-10  Bayer Format Solved; Visual Verification

Resolved the Bayer unpacking issue: the camera must use `SBGGR10` (unpacked
uint16) in the raw stream, not the packed CSI2P variant. Confirmed that the
saved `.npy` arrays contain the correct 10-bit BGGR values and wrote a
visual test to display the four Bayer channels side by side as a sanity check.

---

## 2026-03-12  Project Architecture and Module Stubs

Established the full module structure for the production system: camera
abstraction (`color_cam.py`), ROI detection (`roi.py`), database interface
(`db_manager.py`), error handling, logging, and a desktop mock for Picamera2.
Wrote the four main project documentation files (CLAUDE.md, ARCHITECTURE.md,
CODING_GUIDELINES.md, PROJECT_OVERVIEW.md). Began ROI detection work and
investigated noise patterns that interfere with grid-line detection on the
calibration card.

---

## 2026-03-13  Calibration Image Processing Design

Designed `calibration_1.py` to process calibration images captured under each
LED color at controlled temperatures, storing results in HDF5. Key insight:
images captured under different LEDs are not interchangeable and must be
calibrated independently per `(led_color, led_temperature)` pair.

---

## 2026-03-18  Multi-Color LED Capture Tool

Rewrote the capture test script (`camtest4.py`) as a systematic tool for
generating proof-of-concept image sets. One run captures JPG + Bayer + metadata
for all 5 LED colors (White, Red, Green, Blue, Violet) with auto-exposure per
color. Optional bracketed exposures (`--over`, `--under`) support dynamic-range
exploration. GPIO LED control is integrated with safety guarantees (LEDs off on
any exit). For 5 colors and 3 exposures this produces 45 files in a named
subdirectory ready to `rsync` back to a desktop machine for analysis.

---

## 2026-03-19  Rect Class Refactor; Grid-Patch Finder

Replaced the `row` and `col` integer attributes on the `Rect` class with a
single `name: str` field, making the class general-purpose rather than grid-
specific. Updated `test/test_rect.py` to match (all 17 tests pass).

Created `dev/find_rects.py`: detects a 5×7 grid of colored patches in a JPEG
using Sobel edge projection and non-max suppression, trims each cell boundary
inward past edge-blending noise, applies an optional pixel buffer, and names
each patch by its 1-based `(row, col)` position encoded as `"RRCC"`. Plots
results over the image and prints all Rect strings.

---

## 2026-03-19 → 2026-03-22  camtest5 — Long-Exposure Investigation

Rewrote the capture script as `camtest5.py` and spent several sessions trying
to achieve exposures longer than ~1.24 seconds under the White LED. The full
investigation is recorded below; the outcome changed the project's constraints.

**Changes made to camtest5:**
- Switched auto-exposure from RGB888 ISP output to raw Bayer (linear, no
  gamma), eliminating the need for overrelaxation
- Fixed auto-exposure oscillation by using `capture_request()` for atomic
  metadata + pixel data
- Added proportional settle time (`frame_duration + 1 s`) for long exposures
- Added `LedController` GPIO-busy retry (handles Ctrl-Z from previous run)
- Added SIGTSTP handler so Ctrl-Z triggers clean GPIO/camera shutdown
- Added `VERSION` constant; printed at session header, per-color header, and
  auto-exposure output
- Added `--relax / -r` CLI flag for overrelaxation exponent (default 1.0)
- Multiple iterations of `FrameDurationLimits` strategies (v1.0, v1.1, v1.2)

**Root cause found (2026-03-22):**
`camtest6.py` was written as a minimal diagnostic — just one 4-second exposure
under the White LED with maximum debug output including `sensor_modes` and
`camera_controls` before and after start. The output revealed:

- `ExposureTime: (75, 1,238,765, 20000)` — the hardware ceiling is **1.24 s**
- `FrameDurationLimits: (23894, 1,238,841, ...)` — same ceiling
- **All eight sensor modes** have `exposure_limits` max of 619,382 µs or
  1,238,765 µs. No mode supports exposures longer than 1.24 s.
- The `FrameDurationLimits` configuration object correctly received
  `(4,000,000, 4,000,000)` but the hardware silently clamped it to its max.
  This was always a hardware constraint, not a software configuration bug.
- The Bayer array from `make_array("raw")` is packed CSI-2 uint8 bytes
  (`dtype=uint8, max=255`), not unpacked uint16 pixels. All prior Bayer
  channel means in camtest5 were operating on packed bytes, not pixel values.
  The correct fix is to use `"SBGGR10"` (without `_CSI2P`) as the raw format,
  which Picamera2 will unpack to uint16. This was confirmed in commit 5
  (2026-03-10) but was inadvertently undone when camtest5 was written.

**Design consequence:** The colorimeter must operate within a maximum exposure
of ~1.238 s. LED drive current or optics must deliver adequate signal within
this limit. `MAX_EXPOSURE_US` in camtest5 should be set to ≤ 1,238,000 µs.

---

## Status and Next Steps (as of 2026-03-22)

**Complete:**
- Dual-stream RGB + Bayer capture on RPi (Config 5, 1640×1232)
- Automatic exposure with SNR-guided channel selection
- 5-color LED lighting control
- Systematic per-color bracketed image capture (`camtest5.py`)
- Desktop mock for offline development
- Project architecture and documentation
- `Rect` class with `name` field; `find_rects.py` patch-grid detector
- Hardware exposure ceiling confirmed: 1,238,765 µs (~1.24 s) across all modes
- Bayer format issue identified: must use `"SBGGR10"` (not `"SBGGR10_CSI2P"`)
  in raw stream to get unpacked uint16 pixel values

**camtest5 v1.3 simplification** (commit `b5c083d`):
- `MAX_EXPOSURE_US` corrected to 1,200,000 µs
- `FrameDurationLimits` workaround code removed from `build_camera()` and
  `_set_exposure_and_settle()` — it could never extend past the hardware max

**In progress / not yet started:**
- ROI detection and calibration card grid recognition (`roi.py`)
- Color analysis pipeline (`color_analysis.py`)
- Calibration workflow and HDF5 data storage
- Database schema and `db_manager.py` integration
- Main event loop and state machine (`main.py`)
- Web and physical user interfaces
