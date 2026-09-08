# Reference Framework: Pre-images and Gray Chips

**Date:** 2026-09-07  
**Context:** Two-level reference system for reflectance measurement in the Gutzeit pad analysis pipeline.

---

## Problem Statement

Reflectance and its derivatives (R_inf, absorbance, K/S) require a white reference to be meaningful. A capture contains three potential references:

1. **Gray chips** — stage furniture, stable over months if kept clean, neutral color, common across all captures.
2. **Bare paper** — the strip's own bare substrate, unique to each strip, photographed the same day.
3. **Pre-image** — the same strip photographed before chemistry, same camera/optics/day, same pad footprint.

Each has different sources of drift and different scope.

---

## Key Observations

### Observation 1: Gray chips accumulate dust

Over weeks, ceramic chips accumulate dust and fingerprints. Reflectance drifts ~0.5% per month. This is **slow, gradual, and unrelated to the sample being measured**. It is a **device characteristic**, not a sample characteristic.

### Observation 2: Pre-images replace the flat-field divisor

A pre-image of the same strip, taken the same day before chemistry:
- Is photographed under the same optics, same camera, same illuminant as the measurement
- Carries the same spatial variation in signal level (lighting, vignetting, sensor response)
- Provides pixel-by-pixel correspondence (same pad footprint, same region, same coordinate system)
- Replaces the need for a flat-field divisor (the gray chips' surface model)

Comparison: `R_inf(y, x) = sig_measurement(y, x) / sig_pre(y, x)` — division is per-pixel, 
not against a scalar reference. This is a *change measurement*: we measure only the 
delta introduced by the chemistry, not the absolute reflectance level.

**Critical:** Pixel-to-pixel correspondence between pre-image and measurement requires 
registration (aligning the pad's location). This is a separate problem requiring careful 
thought about possible pad shift between the two captures.

### Observation 3: Gray chips detect device drift, not measurement error

Pre-images measure only the *change* (chemistry-induced delta), which is what we want for 
detecting arsenic. If the pre-image is slightly yellow or has a tint, that cancels out 
in the division — we measure only what *changed*, not the absolute baseline.

Gray chips serve a different role:

1. **Device stability monitor** — track whether the camera/LED/optics are drifting over time.
2. **QC flag** — if chip reflectance drifts >1%, something changed in the device (dust, LED aging, etc.).
3. **Historical record** — chip trends establish whether a measurement session was normal or anomalous.

---

## Two-Level Reference System

### Level 1: Per-Sample (Primary)

**Question:** What changed on this pad due to chemistry?

**Method:** Divide measurement by pre-image, pixel-by-pixel.

```
R_inf(y, x) = sig_measurement(y, x) / sig_pre(y, x)
```

This measures the *relative change* at each pixel. A yellow tint in the pre-image 
cancels out; we measure only the chemistry-induced change. This is fast, intrinsic 
to the strip, and drift-free within the capture session.

**Prerequisites:**
- Pre-images must exist (see `probe.sections()`, `Capture.is_pre`).
- Pad registration: pre-image and measurement must be spatially aligned so that 
  pixel (y, x) in the pre-image corresponds to the same physical location in the 
  measurement (see "Alignment and Registration" section below).

### Level 2: Per-Batch (Secondary, Validation)

**Question:** Has the device changed?

**Method:** Track gray-chip reflectance over time with a fixed reference target.

```
chip_reflectance_t = median(sig over gray chips at time t)

drift_factor = chip_reflectance_t / chip_reflectance_baseline

if drift_factor < 0.99 or drift_factor > 1.01:
    → Device may have drifted; investigate or recalibrate
```

**Cadence:** Weekly or per-batch (photograph same standard against chips each session).

**Action:** If drift > threshold, either:
- Clean/recalibrate the chips
- Apply a correction factor to all measurements from that session
- Flag the data as potentially suspect

---

## Practical Workflow

### For Sample Analysis

```python
# Read a cell's measurement and pre-image
c_meas = Capture(samp_no, sec="")
c_pre = Capture(samp_no, sec="pre")

sig_meas = c_meas.layer(led, rung, layer)
sig_pre = c_pre.layer(led, rung, layer)

# **Prerequisite: register pre-image to measurement**
# (Align pad location if it shifted; see "Alignment and Registration" section)
# dx, dy = measure_pad_shift(c_pre, c_meas)
# sig_pre = warp(sig_pre, dx, dy)

# Per-pixel division: measure only the chemistry-induced change
R_inf = sig_meas / sig_pre
```

The result is a map where:
- Pixels with no chemistry: `R_inf ≈ 1` (pre-image and measurement identical)
- Pixels with dye: `R_inf < 1` (measurement darker than pre-image)

Gray chips are **not consulted** for this sample's analysis.

### For Device Monitoring

```python
# Once per week (or per batch):
# Photograph a blank pad or ceramic reference card.
# Measure against gray chips.

chip_refl_this_week = median(sig_over_chips)

# Compare to baseline (e.g., from commissioning)
drift = chip_refl_this_week / chip_refl_baseline

# Log and alert if needed
log_device_drift(week, chip_id, drift)
if abs(drift - 1.0) > 0.01:
    alert("Device reflectance drifted >1%; investigate")
```

---

## Dark-Frame Spatial Structure: Verified Empirically

**Question:** Do pre and measurement use different dark frames, and does this matter?

**Answer:** Yes, they use different dark frames (because different exposure times). 
But it doesn't matter — dark-frame spatial structure is negligible.

**Evidence** (from `devdesk/roi_darkdiff.py` analysis on 2026-08-02 archives):
- Before/after dark-frame differences over 4 ROI regions × 4 Bayer channels:
  - **Range:** ±3–4 DN maximum per pixel
  - **Median:** 0.0 DN (no systematic drift)
  - **Std dev:** ~1 DN per pixel
- Typical signal levels: 100–1000+ DN
- **Dark spatial variation as % of signal: <1%**

**Implication:** The algebraic cancellation of the flat-field polynomial holds in 
practice. Even though pre uses a 5277µs dark and measurement uses a different dark 
from a different acquisition time, the spatial structure difference is ~1 DN — 
negligible compared to signal levels. Therefore:

$$\frac{\text{sig}_{\text{meas}} / \text{sig}_{\text{pre}}}{(\text{different darks})} = \frac{R_{\text{meas}}}{R_{\text{pre}}} \quad \text{to within} \approx 1\% \text{ error}$$

The flat-field polynomial cancels whether or not we apply it explicitly.

**Tool reference:** `devdesk/roi_darkdiff.py` — a comprehensive dark-frame analyzer 
with three modes: ROI statistics, full-array histograms, and per-pixel bias analysis. 
Applies only to pre-2026-08 archives (which have matched before/after dark batches).

---

## Alignment and Registration

**Solved via hybrid approach: arithmetic base + empirical edge-correlation refinement.**

Pre-image and measurement must be spatially registered: pixel (y, x) in the pre-image 
must correspond to the same physical location in the measurement. The strip is removed 
and reinserted between captures, so shifts of ±2–5 pixels and small rotations are common.

### Two-Stage Registration Strategy

**Stage 1: Arithmetic mapping (fast, from pad geometry)**

The fitted pad boundary in pre and post images defines a pixel-level coordinate transform:
- Convert post-image (x, y) → strip coordinate u
- Find corresponding position in pre-image via top/bottom and tilt parameters
- Resample pre-image using `scipy.ndimage.map_coordinates` with bilinear interpolation

This is implemented in `analysis_1.pre_to_post()` and works well for the average case, 
but does not account for actual mechanical shifts or local deformations.

**Stage 2: Empirical refinement (optional, higher precision)**

For cases where higher precision is needed, measure actual pixel shifts via edge correlation:

1. Extract edge strips (4–5 pixel rows/columns along pad perimeter) from pre and post
2. For each edge, find the (dx, dy) shift that maximizes dot product (correlation)
3. Compute mean shift across all edges
4. Apply shifts via resampling with scipy.ndimage.map_coordinates

**Results from C1014 test:**
- Measured edge shifts: left (−1, 1), right (+1, +5), top (−1, −3), bottom (−1, −2)
- Mean shift: dx = −0.50, dy = +0.25 pixels
- R_inf improvement: std reduced from 0.541 to 0.486 (10% noise reduction)
- Valid pixel count increased by 2% (better edge interpolation)

### Implementation

**For arithmetic registration only (standard approach):**
```python
from analysis_1 import _register

# Pre-image already registered to measurement grid
sig_pre_registered = _register(sig_pre, samp_no)
R_inf = sig_measurement / sig_pre_registered
```

**For empirical refinement (optional, recommended for critical applications):**
```python
from padregistration import register_pre_to_post_empirical

# Measure and apply edge-based shifts
sig_pre_registered, shifts = register_pre_to_post_empirical(
    samp_no, sig_pre, sig_measurement, max_shift=5
)
R_inf = sig_measurement / sig_pre_registered

# Log measured shifts for inspection
print(f"Shifts: mean_dx={shifts['mean_dx']:.2f}, mean_dy={shifts['mean_dy']:.2f}")
print(f"Per-edge: {shifts['edges']}")
```

### When to Use Each

| Scenario | Method | Reason |
|---|---|---|
| **Routine analysis** | Arithmetic only | Fast, sufficient for most applications |
| **High-precision dosimetry** | Empirical refinement | Measurable noise reduction (~10%) |
| **Rotation or skew observed** | Empirical refinement | Captures non-rigid deformations |
| **Batch QC** | Arithmetic only | Speed matters; precision less critical |

**Future enhancement:** Divide image into 4–9 areas and compute per-area shifts 
to handle rotation without explicit image rotation.

---

## Why This Works (with registration)

| Scenario | Pre-image | Gray Chips |
|---|---|---|
| **Pad-to-pad variation** | Measured (each strip's own baseline) | Invisible (common reference) |
| **Within-day camera drift** | Eliminated (same day) | Accumulates (stage furniture) |
| **Chemistry-only signal** | Yes (tint cancels in division) | No (captures absolute level) |
| **Absolute scale** | Not needed (we measure change) | Could track device state |
| **Device health check** | Not applicable | Detects optical degradation |

**Conclusion:** Pre-images measure the chemistry-induced change; gray chips monitor 
device drift.

---

## Implications for Code (Future Work)

### Pad Alignment and Registration

**Blocking issue:** Before `R_inf = sig_meas / sig_pre` can be used, we must solve 
the registration problem. Design decisions needed:

- Which registration method (fiducials, correlation, assume fixed)?
- How to detect when registration fails or is ambiguous?
- Error propagation: if pad shift is ±2 layer pixels, how does that affect the 
  per-pixel R_inf values?

This is a prerequisite for any pre-image-based workflow and should be prototyped 
before integrating into `analysis_1.py`.

### Device Drift Monitoring

- Add a new module (e.g., `desktop/padanalysis/device_monitor.py`) to:
  - Store baseline chip reflectance (from commissioning or calibration run).
  - Track measured chip reflectance over time.
  - Alert if drift exceeds threshold (e.g., >1%).
  - Suggest cleaning, recalibration, or data flagging.

### Documentation

- Update `tex/gutzeit_analysis.tex` to explain the two-level system.
- Document the pre-image protocol in `HOWTO.md`.
- Add field-use guide: "How to detect and respond to device drift."

---

## References

- `desktop/padanalysis/probe.py` — `Capture.is_pre`, `sections()`, pre-image storage.
- `desktop/padanalysis/R_inf_guide.py` — illustrates scalar divisor vs. per-pixel divisor on gray-chip-based reflectance.
- `docs/two_dye_pad_analysis_2026-08-19.md` — dye chemistry and dose-response.
- `tex/gutzeit_analysis.tex` — reflectance photometry and the Kubelka-Munk transform.

---

## Next Steps

1. **Verify pre-image availability** across the current archive (C1017–C1025, and beyond).
2. **Test the pre-image baseline** — does `R_inf = R_meas / R_pre` give more stable results than `R_inf = R_meas / R_white(paper)`?
3. **Design device drift monitor** — how often to recalibrate chips, what tolerance to use for alerts.
4. **Measure violet.B field repeatability** — if pre-images fix the per-pixel divisor question, does violet.B's unique field remain stable and trustworthy?
