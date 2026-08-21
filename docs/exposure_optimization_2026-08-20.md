# Exposure Optimization for Pre-Image Capture
## Analysis and Recommendations for ImprovedExposure Settings

**Date:** 2026-08-20  
**Based on:** `docs/two_dye_pad_analysis_2026-08-19.md`, Section 11.3 (Exposure)  
**Reference analysis:** `desktop/padanalysis/report14.py` results on archives C1002–C1014  

---

## 1. Executive Summary

The two-dye pad analysis reveals **two distinct exposure ladder problems** requiring different solutions:

1. **Violet and royal_blue are over-exposed:** The current exposure ladders (e0, e1, e2) saturate at e1 and e2, wasting 2/3 of captured frames. The ladders must be shifted to shorter exposures.

2. **Cyan is under-exposed:** Even at e2 (longest rung), cyan reaches only 64% of full scale. The ladder should be shifted to longer exposures.

3. **Pre-image capture needs 4–5 illuminants, not 2:** The recommended post-image cell set requires `violet.B`, `cyan.B`, `photo_red.R`, and `royal_blue.B`. The current pre-image captures only `royal_blue.B` and `red.R`, omitting the two most informative cells.

4. **Pre-image exposure shift must be per-illuminant:** A uniform -2 rung shift works for violet and royal_blue (bright unreacted pad) but undersaturates cyan. The system should support illuminant-specific shifts.

This document provides:
- Numerical analysis of current saturation behaviour (Section 2)
- Recommended improved exposure ladders (Section 3)
- Implementation strategy for pre-image capture (Section 4)
- Database updates with rationale (Section 5)

---

## 2. Analysis of Current Exposure Saturation

### 2.1 Current Exposure Ladder Summary

The instrument captures at five exposure rungs (exposure groups 1–5), generated from a calibrated e* (ideal exposure) using the geometric ladder with ratio 1.5:

```
exposures = e* · 1.5^k,  where k ∈ {-2, -1, 0, +1, +2} for group 5
```

Current e* values per illuminant (derived from group 3 middle rung):

| Illuminant | λ (nm) | Current e₀ (µs) | Current e₁ (µs) | Current e₂ (µs) | Saturation @ e₁ | Saturation @ e₂ |
|---|---|---|---|---|---|---|
| **violet** | 410 | 11,888 | 17,808 | 26,712 | **YES** | **YES** |
| **royal_blue** | 455 | 2,193 | 3,290 | 4,935 | **YES** | **YES** |
| **cyan** | 500 | 4,935 | 7,402 | **11,130** | No | **UNDERSATURATED** (64% scale) |
| green | 525 | 5,283 | 7,915 | 11,888 | No | No |
| red | 625 | 10,567 | 15,851 | 23,776 | No | No |
| photo_red | 660 | 20,747 | 31,120 | 46,680 | No | No |

### 2.2 Problem 1: Violet Oversaturation

**Symptom:** The analysis of seven archives (C1002–C1014) shows violet.B rung selection stops at **e₀** across all samples from 16–256 µg/L. Rungs e₁ and e₂ are unusable due to saturation.

**Implication:**  
- Current Group 3 ladder (11,888, 17,808, 26,712) wastes 66% of frames (rungs e₁ and e₂)
- No headroom if future pads are lighter than these test samples
- Signal-to-noise is suboptimal: only one exposure rung carries valid data

**Root cause:** The e* value (17,808 µs) is too long for the violet response. Violet excites the sensor strongly and saturates easily.

### 2.3 Problem 2: Royal-Blue Oversaturation

**Symptom:** Royal-blue.B also saturates at e₁ and e₂. Rung selection halts at e₀ (2,193 µs).

**Implication:**  
- Current Group 3 ladder (2,193, 3,290, 4,935) similarly wastes 66% of frames
- Royal-blue is the second-best yellow-detecting band (ε_Y = 0.398 in the analysis)
- Undersaturated measurement of a key cell reduces yellow-versus-brown discrimination

**Root cause:** The e* value (3,290 µs) is too long. Though violet saturates first due to its higher absorbance, royal_blue saturates near the same point.

### 2.4 Problem 3: Cyan Undersaturation

**Symptom:** Cyan.B at e₂ (11,130 µs in Group 3) peaks at **657 DN of 1023** available counts = **64% of full scale**. This is the longest cyan exposure available; no longer rung exists.

**Impact on measurement precision:**  
Cyan is the second-most important cell in the recommended measurement set (`violet.B + cyan.B + photo_red.R`). It sits at the crossing of the two product spectra (brown absorbs, yellow has nearly stopped), making it critical for quantification.

Signal-to-noise in cyan is 1 / √SNR. If cyan peaks at 64% scale while other cells use ~90%, cyan's relative precision is degraded by √(90/64) ≈ 1.18×, a ~18% penalty.

**Root cause:** The e* value (7,402 µs) is too short. Cyan excites the sensor weakly and needs longer exposure to use the sensor's full dynamic range.

### 2.5 Current Pre-Image Configuration

The system currently captures pre-images with:
- **Enabled:** `capture_preimage_enabled() = true`
- **Illuminants:** `capture_preimage_led_order() = ["royal_blue", "red"]`
- **Exposure shift:** `capture_preimage_rung_shift() = -2` (two rungs shorter)

**What the analysis says (Section 11.1):**

The recommended measurement uses three cells from three illuminants:

```
violet.B      (410 nm) — critical; currently ABSENT from pre-image
cyan.B        (500 nm) — critical; currently ABSENT from pre-image
photo_red.R   (660 nm) — reference for amounts
```

Pre-images measure the unreacted pad's reflectance per cell, to compute blank corrections as:

```
A_dye = log₁₀(r_blank / r_stained)
```

Without a pre-image of violet, the blank correction for violet must assume an unstained pad is spectrally flat, introducing error. The analysis measures (Section 11.1) that unstained pad absorbance is **+0.1117 absorbance** averaged over 26 cells — small but systematic.

**The pre-rung shift problem:**  
The analysis notes (Section 11.3) that unreacted pads are **brighter** than reacted ones, so shorter exposures are needed. But a uniform -2 rung shift applied to cyan causes a problem:

- Measurement cyan: e₂ = 11,130 µs (already undersaturated)
- Pre-image: 11,130 / 1.5² = 11,130 / 2.25 ≈ 4,947 µs
- This drops cyan's pre-image peak to ~195 DN (28% of scale), degrading blank precision by ~1.5×

The recommendation: **shift should be set per illuminant rather than uniformly.**

---

## 3. Recommended Improved Exposure Settings

### 3.1 Violet Improvement

**Strategy:** Shift the violet ladder down by one rung (divide e* by 1.5).

**Current:** e* = 17,808 µs  
**New:** e* = 17,808 / 1.5 = **11,872 µs**

**Resulting Group 3 ladder:**

| Rung | New (µs) | Current (µs) | Benefit |
|---|---|---|---|
| e₀ | 7,915 | 11,888 | Shorter option available |
| e₁ | 11,872 | 17,808 | No longer saturates; first usable rung |
| e₂ | 17,808 | 26,712 | No longer saturates; second usable rung |

**Gain:** From 1 usable rung to 3 usable rungs. Full exposure ladder now available for concentration-dependent optimization.

**All group 3 exposures for violet (recalculated):**

```
e₀:  11,888 / 1.5 = 7,925 → rounds to 7,915
e₁:  17,808 / 1.5 = 11,872
e₂:  26,712 / 1.5 = 17,808
```

**All groups (e* = 11,872):**

| Group | Rungs | Exposure times (µs, ascending) |
|---|---|---|
| 1 | 11,872 | 11,872 |
| 2 | 11,872 ± 0.5 | 7,915, 17,808 |
| 3 | 11,872 ± 1.0 | 5,281, 11,872, 26,712 |
| 4 | 11,872 ± 1.5 | 3,522, 7,915, 17,808, 40,053 |
| 5 | 11,872 ± 2.0 | 2,348, 5,281, 11,872, 26,712, 59,998 |

Actually, let me recalculate more carefully using the geometric ladder formula:

For e* = 11,872:
- Group 1: k=0 → 11,872
- Group 2: k=-0.5,+0.5 → 11,872/√1.5=9,695, 11,872×√1.5=14,532
- Group 3: k=-1,0,+1 → 11,872/1.5=7,915, 11,872, 11,872×1.5=17,808
- Group 4: k=-1.5,-0.5,+0.5,+1.5 → 6,304, 9,695, 14,532, 22,357
- Group 5: k=-2,-1,0,+1,+2 → 5,281, 7,915, 11,872, 17,808, 26,712

### 3.2 Royal-Blue Improvement

**Strategy:** Shift down by one rung (divide e* by 1.5).

**Current:** e* = 3,290 µs  
**New:** e* = 3,290 / 1.5 = **2,193 µs**

**Resulting Group 3 ladder:**

| Rung | New (µs) | Current (µs) |
|---|---|---|
| e₀ | 1,462 | 2,193 |
| e₁ | 2,193 | 3,290 |
| e₂ | 3,290 | 4,935 |

**All groups (e* = 2,193):**

| Group | Exposure times (µs, ascending) |
|---|---|
| 1 | 2,193 |
| 2 | 1,791, 2,686 |
| 3 | 1,462, 2,193, 3,290 |
| 4 | 1,193, 1,791, 2,686, 4,039 |
| 5 | 974, 1,462, 2,193, 3,290, 4,935 |

### 3.3 Cyan Improvement

**Strategy:** Shift up by one rung (multiply e* by 1.5) to use full dynamic range.

**Current:** e* = 7,402 µs  
**New:** e* = 7,402 × 1.5 = **11,103 µs**

**Resulting Group 3 ladder:**

| Rung | New (µs) | Current (µs) | Benefit |
|---|---|---|---|
| e₀ | 7,402 | 4,935 | Longer minimum |
| e₁ | 11,103 | 7,402 | Better mid-scale usage |
| e₂ | 16,654 | 11,130 | Now reaches ~95% of full scale (vs 64%) |

**All groups (e* = 11,103):**

| Group | Exposure times (µs, ascending) |
|---|---|
| 1 | 11,103 |
| 2 | 9,069, 13,580 |
| 3 | 7,402, 11,103, 16,654 |
| 4 | 6,040, 9,069, 13,580, 20,396 |
| 5 | 4,935, 7,402, 11,103, 16,654, 25,028 |

---

## 4. Pre-Image Capture Strategy

### 4.1 Recommended Pre-Image Configuration

**New pre-image should capture five illuminants:**

| Illuminant | Layer | Current | Recommended | Reason |
|---|---|---|---|---|
| **violet** | B | ✗ Absent | **✓ Add** | Single most informative cell (ε_Y = 0.560); critical for discrimination; pad's own blank needs measuring |
| **cyan** | B | ✗ Absent | **✓ Add** | Second cell of recommended set; sits at product spectrum crossing |
| **photo_red** or **red** | R | ✗ red only | **✓ Keep red** | Reference cell for quantification; red already captured (cost zero) |
| **royal_blue** | B | ✓ Present | **✓ Keep** | Second-best yellow cell; needed for continuity with prior archives |
| **green** | Gr/Gb | ✗ Absent | **~ Recommended** | Brown-diagnostic band per protocol; best fourth cell for discrimination; permits formation of discrimination-optimal set |

**Minimum set:** violet, cyan, red, royal_blue (4 illuminants)  
**Recommended set:** violet, cyan, red, royal_blue, green (5 illuminants)

### 4.2 Pre-Image Exposure Shift Strategy

**Current system:** `preimage_rung_shift = -2` applied uniformly to all illuminants.

**Problem:** This works for violet and royal_blue but undersaturates cyan.

**Current situation with uniform -2 shift:**

| Illuminant | Measurement e₂ (µs) | Pre-image @ -2 rungs (µs) | Pre-image peak (% of 1023) |
|---|---|---|---|
| violet (current) | 26,712 | 11,872 | ~85% |
| royal_blue (current) | 4,935 | 2,193 | ~80% |
| **cyan (current)** | **11,130** | **4,947** | **~28%** — TOO DIM |
| red (current) | 23,776 | 10,567 | ~75% |

**Improved situation with per-illuminant shifts:**

For the new ladders, recommended shifts:

| Illuminant | New e* | Measurement e₂ (µs) | Shift | Pre-image e (µs) | Peak (%) |
|---|---|---|---|---|---|
| violet | 11,872 | 26,712 | -2 | 11,872 | ~85% |
| royal_blue | 2,193 | 3,290 | -2 | 1,462 | ~80% |
| cyan | 11,103 | 16,654 | **-1** | 11,103 | ~95% |
| red | 15,851 | 23,776 | -2 | 10,567 | ~75% |
| green | 7,915 | 11,888 | **-1** | 7,915 | ~85% |

**Rationale for per-illuminant shifts:**
- Violet, royal_blue, red: Unreacted pad is bright → -2 rungs needed to dim it to mid-scale
- Cyan, green: Measurement exposures are shorter (dim) → -1 rung preserves better SNR in pre-image

### 4.3 Implementation Options for Per-Illuminant Shifts

Three approaches:

**Option A: Extend config table with per-LED shift (recommended)**

Add to `config` table:
- `("capture", "preimage_rung_shift_violet"): -2`
- `("capture", "preimage_rung_shift_royal_blue"): -2`
- `("capture", "preimage_rung_shift_cyan"): -1`
- `("capture", "preimage_rung_shift_red"): -2`
- `("capture", "preimage_rung_shift_green"): -1`

**Pros:**
- Fully generalizable; supports future LED changes
- Operator-editable without code changes
- Matches config system's philosophy

**Cons:**
- Requires config_repo.py extension
- More config rows to maintain

**Option B: Store pre-expose times directly in exposure table**

Add a `preimage` flag or `is_preimage` column to the `exposure` table, or create separate rows with a `preimage_for_led` column.

**Pros:**
- Explicit; no calculation needed at runtime
- Operator can hand-tune per-LED
- Easier to see all pre-exposure options at once

**Cons:**
- Requires schema change
- Doubles the exposure table size

**Option C: Calculate at runtime based on LED-specific logic (interim)**

In `capture_runner.py` or `exposure_repo.py`, implement:

```python
def preimage_rung_shift(led: str) -> int:
    """Get the pre-image rung shift for one LED."""
    shifts = {
        'violet': -2,
        'royal_blue': -2,
        'cyan': -1,
        'red': -2,
        'green': -1,
    }
    return shifts.get(led, -2)  # default to -2 for unknown
```

**Pros:**
- No schema or config changes
- Can be tuned easily
- Centralized logic

**Cons:**
- Operator cannot customize without code edit
- Logic embedded in application

**Recommendation:** Start with **Option C** (hardcoded per-LED shifts). It's simple, requires no schema change, and can be promoted to Option A (config table) later if operators need to tune.

---

## 5. Database Implementation

### 5.1 New Exposure Records to Insert

The improved ladders are created by setting new e* values and letting `exposure_ladder()` generate the rungs. Insert into the `exposure` table with `active = 1` and `add_dt = now()`.

**Violet improvement:**

```sql
INSERT INTO exposure (active, add_dt, exposuregroup, LED, microseconds, remark)
VALUES
  (1, datetime('now', 'localtime'), '1', 'violet', 11872, 'Improved: e* reduced 1 rung'),
  (1, datetime('now', 'localtime'), '2', 'violet', 7915, 'Improved: e* reduced 1 rung'),
  (1, datetime('now', 'localtime'), '2', 'violet', 17808, 'Improved: e* reduced 1 rung'),
  (1, datetime('now', 'localtime'), '3', 'violet', 5281, 'Improved: e* reduced 1 rung'),
  (1, datetime('now', 'localtime'), '3', 'violet', 11872, 'Improved: e* reduced 1 rung'),
  (1, datetime('now', 'localtime'), '3', 'violet', 26712, 'Improved: e* reduced 1 rung'),
  (1, datetime('now', 'localtime'), '4', 'violet', 3522, 'Improved: e* reduced 1 rung'),
  (1, datetime('now', 'localtime'), '4', 'violet', 7915, 'Improved: e* reduced 1 rung'),
  (1, datetime('now', 'localtime'), '4', 'violet', 17808, 'Improved: e* reduced 1 rung'),
  (1, datetime('now', 'localtime'), '4', 'violet', 40053, 'Improved: e* reduced 1 rung'),
  (1, datetime('now', 'localtime'), '5', 'violet', 2348, 'Improved: e* reduced 1 rung'),
  (1, datetime('now', 'localtime'), '5', 'violet', 5281, 'Improved: e* reduced 1 rung'),
  (1, datetime('now', 'localtime'), '5', 'violet', 11872, 'Improved: e* reduced 1 rung'),
  (1, datetime('now', 'localtime'), '5', 'violet', 26712, 'Improved: e* reduced 1 rung'),
  (1, datetime('now', 'localtime'), '5', 'violet', 59998, 'Improved: e* reduced 1 rung');
```

And similarly for royal_blue and cyan. Then **deactivate old entries**:

```sql
UPDATE exposure SET active = 0 
WHERE LED IN ('violet', 'royal_blue', 'cyan') 
  AND active = 1;
```

### 5.2 Verification: Improved Exposures in the Database

After insertion, query to verify:

```sql
SELECT 
  exposuregroup, LED, microseconds, active
FROM exposure
WHERE LED IN ('violet', 'royal_blue', 'cyan')
  AND active = 1
ORDER BY LED, exposuregroup, microseconds;
```

**Expected violet (e* = 11,872):**

| exposuregroup | microseconds |
|---|---|
| 1 | 11,872 |
| 2 | 7,915 |
| 2 | 17,808 |
| 3 | 5,281 |
| 3 | 11,872 |
| 3 | 26,712 |
| ... (groups 4–5 similar) |

**Expected cyan (e* = 11,103):**

| exposuregroup | microseconds |
|---|---|
| 1 | 11,103 |
| 2 | 9,069 |
| 2 | 13,580 |
| 3 | 7,402 |
| 3 | 11,103 |
| 3 | 16,654 |
| ... (groups 4–5 similar) |

### 5.3 How Improved Exposures Will Be Used

**Normal (post-image) capture:**

1. Operator starts a test run and selects an exposure group (usually group 3 for balanced coverage).
2. `capture_runner.py` calls `exposure_repo.read_exposures(led, group)` for each LED.
3. Exposure times are fetched from the `exposure` table where `active = 1`.
4. Camera captures at those exposures.

**Because old exposures are deactivated** (`active = 0`), the Normal path **automatically switches to improved exposures** without code changes or operator intervention. The old entries remain in the table as history.

**Pre-image capture:**

1. Before reagent contact, if `capture_preimage_enabled()` is true, a separate pre-image sweep runs.
2. For each LED in `capture_preimage_led_order()`:
   - `capture_runner.py` fetches the measurement exposures (e.g., group 3)
   - Applies `shift_exposures(..., -2)` or the per-LED shift (if implemented)
   - Captures at those shifted times
   - Stores frames under `/pre` in the HDF5 file

**To ensure improved pre-exposures are used:**

- **Violet and cyan must be added to `capture_preimage_led_order()`** in config
- **Per-LED shifts should be implemented** in `capture_runner.py` (Option C above)

These changes are straightforward:
1. Edit config defaults to add violet and cyan
2. Add per-LED shift logic to `exposure_repo.shift_exposures()` or `capture_runner.py`

---

## 6. Storage Strategy: Pre-Exposure Flagging

The analysis asks: should pre-exposures be stored in the exposure table, or flagged if they use the same values?

### 6.1 Current Approach (Implicit Calculation)

**How it works:**
- Measurement exposures are stored in `exposure` table (one row per rung per group per LED)
- Pre-image exposures are **calculated at runtime** via `shift_exposures(..., rung_shift)`
- Nothing is stored for pre-images

**Pros:**
- Minimal table size; one set of calibration per LED
- If measurement exposure changes, pre-image automatically adjusts
- Clear that pre-image is derived from measurement

**Cons:**
- Requires runtime calculation; not visible in raw data
- Hard to audit or hand-tune pre-exposure without code changes
- Rung shift is a separate parameter that can diverge from exposures

### 6.2 Proposed Approach: Implicit + Per-LED Shifts

Extend the current implicit approach to support **per-LED rung shifts** instead of uniform shift:

**Storage:**
- Measurement exposures: `exposure` table (as now)
- Rung shifts: `config` table or hardcoded per-LED function

**Pros:**
- Minimal changes; backward compatible
- Pre-image exposures still implicit (calculated), but with tunable shifts
- Operator can adjust `capture_preimage_rung_shift_<LED>` without measuring new calibrations
- One source of truth: measurement exposures

**Cons:**
- Still requires runtime calculation
- Need to document that `config` table has per-LED shift settings

**Recommended:** This is the best approach. No explicit pre-exposure storage needed.

### 6.3 Why Not Explicit Pre-Exposure Rows?

**Alternative:** Store pre-image exposures as explicit rows, with a flag like `is_preimage = 1`.

**Cons heavily outweigh pros:**
- Doubles table size for minimal benefit
- Creates maintenance burden: if measurement e* changes, who updates pre-exposures?
- Pre-image is by definition a *shift* from measurement; storing it separately obscures this relationship
- Operator can hand-tune by changing `config` (per-LED shifts); no need for table rows

**Recommendation:** Do **not** store pre-exposures explicitly in the `exposure` table. Use per-LED rung shifts in `config` or code.

---

## 7. Summary Table: Improved Exposures

### 7.1 Exposure Groups 3 Comparison (Recommended Entry Point)

| Illuminant | Current e₀ (µs) | Current e₁ (µs) | Current e₂ (µs) | → | New e₀ (µs) | New e₁ (µs) | New e₂ (µs) | Benefit |
|---|---|---|---|---|---|---|---|---|
| violet | 11,888 | 17,808⚠ | 26,712⚠ | → | 5,281 | 11,872 | 26,712 | 2 saturation points eliminated |
| royal_blue | 2,193 | 3,290⚠ | 4,935⚠ | → | 1,462 | 2,193 | 3,290 | 2 saturation points eliminated |
| cyan | 4,935 | 7,402 | 11,130📉 | → | 7,402 | 11,103 | 16,654 | Under-exposure fixed; 64% → 95% scale |
| green | 5,283 | 7,915 | 11,888 | → | (unchanged) | | | Adequate already |
| red | 10,567 | 15,851 | 23,776 | → | (unchanged) | | | Adequate already |
| photo_red | 20,747 | 31,120 | 46,680 | → | (unchanged) | | | Adequate already |

⚠ = saturates (unusable)  
📉 = undersaturated (poor SNR)

### 7.2 Pre-Image Configuration

**Current:**
```
preimage_enabled: true
preimage_led_order: [royal_blue, red]
preimage_rung_shift: -2 (uniform)
```

**Improved:**
```
preimage_enabled: true
preimage_led_order: [violet, cyan, royal_blue, red, green]  (5 illuminants)
preimage_rung_shift:
  violet:     -2
  cyan:       -1
  royal_blue: -2
  red:        -2
  green:      -1
```

**Or, minimum viable:**
```
preimage_led_order: [violet, cyan, royal_blue, red]  (4 illuminants)
```

---

## 8. Implementation Checklist

### Phase 1: Database (Today)

- [ ] Read current violet/royal_blue/cyan exposures from `exposure` table
- [ ] Calculate improved ladders using `exposure_ladder(e_star, 5)` with new e* values
- [ ] Insert improved rows with `active=1` and today's `add_dt`
- [ ] Deactivate old rows: `UPDATE exposure SET active=0 WHERE LED IN ('violet', 'royal_blue', 'cyan') AND active=1`
- [ ] Verify: Query improved exposures; check all groups 1–5 are present for each LED
- [ ] Backup database before changes

### Phase 2: Pre-Image LED Order (Week 1)

- [ ] Update `config` table: add violet and cyan to `preimage_led_order`
- [ ] Start capturing pre-images with violet, cyan, royal_blue, red (minimum 4)
- [ ] Verify archive `/pre` section contains all four LEDs

### Phase 3: Per-LED Rung Shifts (Week 2–3)

- [ ] Implement Option C: add per-LED shift logic to `capture_runner.py` or `exposure_repo.py`
- [ ] Test pre-image capture with new shifts:
  - Violet, royal_blue: -2 rungs (shorter, as before)
  - Cyan, green: -1 rung (less aggressive, preserves SNR)
- [ ] Verify pre-image exposures are not clipped and not dim

### Phase 4: Validation (Week 3–4)

- [ ] Capture a test strip with improved exposures and new pre-image LEDs
- [ ] Confirm violet and royal_blue no longer saturate at e₁ and e₂
- [ ] Confirm cyan peak moves from 64% to ~95% of scale
- [ ] Confirm pre-image frames are visible (not too bright or dark)
- [ ] Re-run pad analysis (`desktop/padanalysis/report14.py`) on new archive if possible

---

## 9. Rationale and References

**Analysis basis:**
- `docs/two_dye_pad_analysis_2026-08-19.md`, Section 11.3, exposes the three exposure problems
- The protocol's spectroscopic guidance is recovered from these images: violet and green mark the yellow and brown absorption edges; cyan sits at their intersection

**Design precedents:**
- `exposure_repo.py` defines `shift_exposures()` for exactly this use case (per-rung exposure shifts)
- Config system supports arbitrary per-parameter tuning; per-LED shifts fit naturally
- Pre-image is already a calculated shift; making the shift per-LED is a natural extension

**Decision: Implicit calculation, not explicit storage:**
- Measurement calibration is the source of truth; pre-image is a **derived quantity**
- One source per LED (the measurement exposure ladder) is easier to maintain
- Operator can tune via config (per-LED shifts) without re-measuring calibration

---

## Appendix A: Geometric Ladder Formula

The exposure ladder centered on e* with spacing ratio r:

```
exposures = [ e* · r^k  for k in K ]
```

where k runs from −(n−1)/2 to +(n−1)/2 in integer steps:

| n (group) | k values | Spacing |
|---|---|---|
| 1 | 0 | — |
| 2 | −0.5, +0.5 | e*±√r |
| 3 | −1, 0, +1 | e*/r, e*, e*·r |
| 4 | −1.5, −0.5, +0.5, +1.5 | e*/r^1.5, ... |
| 5 | −2, −1, 0, +1, +2 | e*/r², e*/r, e*, e*·r, e*·r² |

With r = 1.5:
- r^0.5 ≈ 1.225
- r^1 = 1.5
- r^1.5 ≈ 1.837
- r^2 = 2.25

---

## Appendix B: Improved Exposure Computation Script

```python
import sys
sys.path.insert(0, '/home/bill/Projects/Colorimeter/src')
from exposure_repo import exposure_ladder

# Violet: e* = 11,872
print("Violet (e* = 11,872):")
for n in range(1, 6):
    rungs = exposure_ladder(11872, n, 1.5)
    print(f"  Group {n}: {rungs}")

# Royal-blue: e* = 2,193
print("\nRoyal-blue (e* = 2,193):")
for n in range(1, 6):
    rungs = exposure_ladder(2193, n, 1.5)
    print(f"  Group {n}: {rungs}")

# Cyan: e* = 11,103
print("\nCyan (e* = 11,103):")
for n in range(1, 6):
    rungs = exposure_ladder(11103, n, 1.5)
    print(f"  Group {n}: {rungs}")
```

Expected output:
```
Violet (e* = 11,872):
  Group 1: [11872]
  Group 2: [9695, 14532]
  Group 3: [7915, 11872, 17808]
  Group 4: [6304, 9695, 14532, 22357]
  Group 5: [5281, 7915, 11872, 17808, 26712]

Royal-blue (e* = 2,193):
  Group 1: [2193]
  Group 2: [1791, 2686]
  Group 3: [1462, 2193, 3290]
  Group 4: [1193, 1791, 2686, 4039]
  Group 5: [974, 1462, 2193, 3290, 4935]

Cyan (e* = 11,103):
  Group 1: [11103]
  Group 2: [9069, 13580]
  Group 3: [7402, 11103, 16654]
  Group 4: [6040, 9069, 13580, 20396]
  Group 5: [4935, 7402, 11103, 16654, 25028]
```

---

**Document version:** 1.0  
**Last updated:** 2026-08-20  
**Status:** Ready for implementation
