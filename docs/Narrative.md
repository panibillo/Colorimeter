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

## Status and Next Steps (as of 2026-03-19)

**Complete:**
- Dual-stream RGB + Bayer capture on RPi (Config 5, 1640×1232)
- Automatic exposure with SNR-guided channel selection
- 5-color LED lighting control
- Systematic per-color bracketed image capture (`camtest4.py`)
- Desktop mock for offline development
- Project architecture and documentation

**In progress / not yet started:**
- ROI detection and calibration card grid recognition (`roi.py`)
- Color analysis pipeline (`color_analysis.py`)
- Calibration workflow and HDF5 data storage
- Database schema and `db_manager.py` integration
- Main event loop and state machine (`main.py`)
- Web and physical user interfaces
