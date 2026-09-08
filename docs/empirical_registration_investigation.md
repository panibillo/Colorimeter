# Empirical Pre-Image Registration Investigation

**Status:** Shelved (possible future revisit)  
**Date:** 2026-09-08  
**Conclusion:** Empirical edge-correlation registration shows conditional benefit but lacks predictive heuristic. Defer implementation pending investigation of success factors.

---

## Executive Summary

Implemented edge-correlation-based registration to measure and correct pixel shifts between pre-image and measurement. Testing on 11 pre-image samples (C1014–C1025) revealed:

- **Conditional benefit:** Improves 3/8 samples by +10–15%; degrades 5/8 by −1 to −10%
- **Root issue:** No clear heuristic to predict which samples benefit
- **Evaluation lesson:** Initial conclusion ("always bad") was artifact of measurement method; proper erosion (interior pad region only, erode=6) reveals true picture
- **Current recommendation:** Use arithmetic registration (`pre_to_post()`) as default. Revisit empirical when predictive heuristic identified.

---

## Implementation

**Module:** `desktop/padanalysis/padregistration.py` (numpy-only, RPi-compatible)

### Key Functions

- `measure_registration_shifts(samp_no, pre_layer, post_layer, max_shift=5)` — Extract edge strips, correlate, measure (dx, dy) per edge
- `apply_registration_with_shifts(pre_layer, shifts, shape_post)` — Resample via bilinear interpolation
- `register_pre_to_post_empirical(samp_no, pre_layer, post_layer, use_areas=False)` — High-level entry point
- `_register_with_areas(samp_no, pre_layer, post_layer, n_areas=3)` — Experimental per-area shifts (unsuccessful)

### Algorithm

1. Extract 4–5 pixel strips along pad edges (left, right, top, bottom)
2. For each edge, find (dx, dy) shift maximizing dot product (correlation)
3. Compute mean shift across all 4 edges
4. Resample pre-image using bilinear interpolation with global mean shift
5. (Tested but failed: divide image into 3×3 areas, compute per-area shifts for rotation)

---

## Test Results

### Dataset

- **Samples:** C1014–C1025 (11 total, pre-images available in all)
- **LED/channel:** royal_blue / R (most reliable channel)
- **Measurement:** R_inf std dev before vs after registration

### Evaluation Protocol Issue

**Initial (full-layer):** No erosion → included noisy edge regions
- Result: Empirical degraded by 8.6% on average (−28% to +6%)
- Conclusion (incorrect): Always worse than arithmetic

**Corrected (interior pad):** With erode=6 erosion (matches actual analysis)
- Result: Mixed; 3/8 improved, 5/8 degraded
- Corrected conclusion: Conditional benefit

### Results (with erode=6)

| Sample | Arithmetic | Empirical | Change | Tilt Δ | Edge Var |
|---|---|---|---|---|---|
| C1018 | 0.5785 | 0.5192 | **+10.2%** ✓ | 0.09° | dx=0 |
| C1019 | 0.6927 | 0.7117 | −2.7% | 0.49° | dx=0 |
| C1020 | 0.5798 | 0.6348 | −9.5% | 0.36° | dx=0 |
| C1021 | 0.9364 | 0.8251 | **+11.9%** ✓ | 0.25° | dx=0 |
| C1022 | 0.5998 | 0.6072 | −1.2% | 0.03° | dx=0 |
| C1023 | 0.9552 | 0.8171 | **+14.5%** ✓ | 0.07° | dx=0 |
| C1024 | 0.8181 | 0.8427 | −3.0% | 0.29° | dx=0 |
| C1025 | 0.8751 | 0.8875 | −1.4% | 0.53° | dx=0 |

**Summary:** Improves 3/8 (C1018, C1021, C1023); degrades 5/8

---

## Investigation of Success Factors

### What Didn't Explain It

1. **Edge shift agreement** — C1020 and C1021 have identical patterns (dx=0 on all edges) but opposite results (C1020 degrades, C1021 improves)

2. **Tilt angle difference** — C1020 has largest (0.36°) but degrades; C1021 helps with only 0.25°; no clear correlation

3. **Edge shift variance** — All samples show dx_Δ=0 (vertical edges perfectly aligned in correlation); top/bottom vary in dy (−5 to +4), but this doesn't predict outcome

4. **Wider edge buffers** — Tested edge_width ∈ {5, 10, 15}; wider buffers made results worse, not better

5. **Rotation correction** — Tested per-area (3×3) shifts to handle rotation; still degraded compared to arithmetic; rotation is not the root cause

### Edge Shift Details

**Successful samples:**
- C1018: L(0,0), R(0,0), T(0,-2), B(0,-5)
- C1021: L(0,0), R(0,0), T(0,-5), B(0,-5) ← perfect agreement
- C1023: L(0,0), R(0,0), T(0,-5), B(0,+4) ← large disagreement

**Failed samples:**
- C1020: L(0,0), R(0,0), T(0,-5), B(0,-5) ← perfect agreement (like C1021!)
- C1022: L(0,0), R(0,0), T(0,-5), B(0,0)
- C1024: L(0,0), R(0,0), T(0,-3), B(0,-5)

**Conclusion:** Edge shift pattern does not predict success.

---

## Root Causes Identified

### Why Empirical Improves Interior

Edge-based correlation successfully measures real shifts on interior pad where measurement happens. Pre-image has surface roughness (paper grain) that provides strong correlation peaks for alignment.

### Why Empirical Fails on Edges

Pad edge regions are noisier (boundary effects, chip glints, stage artifacts). Correlation picks up spurious shifts rather than true misalignment. When full layer is measured, edge noise dominates and overwhelms interior improvement.

### Why Wider Buffers Hurt

Larger edge strips include more noise and more transition region between pad and background. Correlation becomes less reliable, not more. Maximum benefit at edge_width=5.

### Why Per-Area Didn't Help

Not a rotation problem. The arithmetic approach already uses fitted tilt angles (pre.m and post.m). Per-area shifts introduce discontinuities and interpolation artifacts at region boundaries without fixing the core issue (which we still don't understand).

---

## Unknowns: What Distinguishes Winners?

Three hypotheses for why C1018, C1021, C1023 benefit while others don't:

### 1. Pre-Image Quality (RMS Residual)

Hypothesis: Well-fitted pre-image geometries (low RMS) correlate with success.

**To test:** Compare pre-image fit RMS residuals between winners and losers.

```
Winners (C1018, C1021, C1023): RMS_pre = ?
Losers (C1020, C1022, C1024): RMS_pre = ?
```

### 2. Signal Quality (Noise Floor / SNR)

Hypothesis: Noisier measurements benefit from empirical refinement (more accurate than arithmetic on noisy data).

**To test:** Measure noise floor (dark noise std) and signal SNR per sample.

### 3. Pre-Image Exposure Level

Hypothesis: Pre-image exposure affects correlation quality. Over/underexposed pre-images provide weaker correlation peaks.

**To test:** Check pre-image exposure time and signal level statistics across winners vs losers.

---

## Lessons Learned

### Measurement Protocol Matters

Testing "full-layer R_inf" vs "interior-only R_inf" gave opposite conclusions. Always measure on the region actually used in analysis (erode=6 for production).

### Edge-Based Correlation Has Limits

Correlation-based registration works on pad interior (good SNR, surface texture) but fails on edges (noise, artifacts). This is a fundamental limitation, not fixable by wider buffers or per-area tricks.

### Arithmetic Approach's Strength

The arithmetic pre_to_post() function uses fitted pad geometry (aL, m, yc from both pre and post). This is robust because it doesn't rely on noisy edge measurements—it uses the overall pad boundary fit. For most samples, this is sufficient.

---

## Why Shelve Now

1. **No clear path forward** — None of the obvious hypotheses explain success pattern
2. **Conditional benefit too small** — 3/8 samples with +10–15% vs 5/8 degraded; not compelling enough without heuristic
3. **Arithmetic is "good enough"** — Arithmetic registration is reliable default; empirical is optional "nice to have"
4. **Investigation time** — Identifying success factor would require analysis of fit quality, noise, exposure across all samples
5. **Risk** — Using empirical without heuristic could make results worse on unknown samples

---

## Future Revisit Checklist

When returning to empirical registration, follow this order:

- [ ] Extract pre-image RMS residuals for all 11 samples (success factor hypothesis #1)
- [ ] If strong correlation: use RMS threshold as heuristic; test on new samples
- [ ] If no correlation: measure SNR and noise floor (hypothesis #2)
- [ ] If SNR shows pattern: validate with exposure time analysis (hypothesis #3)
- [ ] Once heuristic found: integrate into `register_pre_to_post_empirical()` with automatic selection
- [ ] Re-evaluate on full archive (C1014–C1025) with heuristic enabled
- [ ] Only deploy if consistent +5% or better on >70% of samples

---

## Current Codebase State

### Active

- `desktop/padanalysis/padregistration.py` — Fully functional, numpy-only, documented
- `desktop/padanalysis/REGISTRATION.md` — Practical guide with erosion note and investigation summary

### Documentation

- `docs/reference_framework_preimage_and_chips.md` — Updated with empirical findings
- This file — Investigation summary (future reference)

### Usage (Production)

Use arithmetic only:
```python
from analysis_1 import _register

sig_pre_registered = _register(sig_pre, samp_no)
R_inf = sig_measurement / sig_pre_registered
```

Optional empirical (research only):
```python
from padregistration import register_pre_to_post_empirical

sig_pre_registered, shifts = register_pre_to_post_empirical(
    samp_no, sig_pre, sig_measurement, max_shift=5, use_areas=False
)
# Compare results; log shifts for investigation
```

---

## References

- `desktop/padanalysis/padregistration.py` — Implementation
- `desktop/padanalysis/REGISTRATION.md` — User guide
- `desktop/padanalysis/analysis_1.py:635–667` — `pre_to_post()` arithmetic registration
- Test results: See `empirical_registration_investigation.md` (this file) for detailed tables
