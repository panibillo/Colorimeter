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

### Observation 2: Pre-images are intrinsically superior for per-sample measurement

A pre-image of the same strip, taken the same day before chemistry:
- Eliminates inter-day camera drift (same sensor, same optics, same day)
- Eliminates inter-strip pad variability (same physical substrate)
- Provides pixel-by-pixel correspondence (same pad footprint, same region)
- Is specific to *this strip*, not generic to the device

Comparison: `R_inf = (sig_measurement) / (sig_pre)` — no gray chips needed.

### Observation 3: Gray chips provide absolute scale and drift detection

Pre-images measure *relative* change (measurement vs. pre), but not *absolute* reflectance level. Gray chips provide:

1. **Absolute reflectance scale** — the true numerical value of R_inf across all samples.
2. **Device drift detection** — a monitor of camera/LED/optical stability over time.
3. **Cross-strip comparison** — a common reference for comparing different strips on an absolute scale.
4. **QC flag** — if chip reflectance drifts >1%, something changed in the device.

---

## Two-Level Reference System

### Level 1: Per-Sample (Primary)

**Question:** What happened to this pad?

**Method:** Use pre-image as the white reference.

```
R_inf = (sig_measurement) / (R_pre)

where R_pre = median(sig_pre over bare paper region)
```

This is fast, intrinsic to the strip, and drift-free within the capture session.

**Requirement:** Pre-images must exist (see `probe.sections()`, `Capture.is_pre`).

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

# Compute reflectance against pre-image
R = sig_meas / sig_pre

# White reference: bare paper in pre-image
R_white = median(R_pre[paper_mask])

# Paper-referenced reflectance
R_inf = R / R_white
```

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

## Why This Works

| Scenario | Pre-image | Gray Chips |
|---|---|---|
| **Pad-to-pad variation** | Measured (each strip's own baseline) | Invisible (common reference) |
| **Within-day camera drift** | Eliminated (same day) | Accumulates (stage furniture) |
| **Inter-day consistency** | Preserved (each strip self-referential) | Drifts with dust/age |
| **Absolute scale** | Relative only | Provides absolute level |
| **Cross-strip comparison** | Not comparable without chips | Comparable via chips |
| **Device health check** | Not applicable | Detects optical degradation |

**Conclusion:** Pre-images are the measurement reference; gray chips are the system monitor.

---

## Implications for Code

### `analysis_1.py`

- `get_balanced_cell_data(samp_no, ...)` already computes R_inf using `median(R[paper])` on the *measurement* section.
- **Should** compute it using pre-image when available: `R_inf = R_meas / R_pre`.
- Requires opening both sections: `Capture(samp_no, "")` and `Capture(samp_no, "pre")`.

### Device Drift Monitoring

- Add a new module (e.g., `desktop/padanalysis/device_monitor.py`) to:
  - Store baseline chip reflectance (from commissioning or calibration run).
  - Track measured chip reflectance over time.
  - Alert if drift exceeds threshold.
  - Suggest recalibration or data correction.

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
