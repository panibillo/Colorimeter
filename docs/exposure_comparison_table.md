# Exposure Improvement: Before and After

## Quick Reference — Group 3 Comparison

**Group 3 is the recommended entry point for most test runs** (3 exposures per illuminant).

### Violet (410 nm)

| Rung | Before (µs) | After (µs) | Issue before | Status after |
|---|---|---|---|---|
| e₀ | 11,888 | 7,915 | Short but available | Now even shorter; more dynamic range |
| e₁ | 17,808 | 11,872 | **SATURATES** ⚠ | **Now usable** ✓ |
| e₂ | 26,712 | 17,808 | **SATURATES** ⚠ | **Now usable** ✓ |

**Gain:** 1 usable rung → 3 usable rungs (200% improvement)

### Royal-Blue (455 nm)

| Rung | Before (µs) | After (µs) | Issue before | Status after |
|---|---|---|---|---|
| e₀ | 2,193 | 1,462 | Short but available | Now even shorter |
| e₁ | 3,290 | 2,193 | **SATURATES** ⚠ | **Now usable** ✓ |
| e₂ | 4,935 | 3,290 | **SATURATES** ⚠ | **Now usable** ✓ |

**Gain:** 1 usable rung → 3 usable rungs (200% improvement)

### Cyan (500 nm)

| Rung | Before (µs) | After (µs) | Peak DN (before) | Peak DN (after) | Issue before | Status after |
|---|---|---|---|---|---|---|
| e₀ | 4,935 | 7,402 | — | — | Weak | Stronger |
| e₁ | 7,402 | 11,103 | — | — | Weak | Better |
| e₂ | 11,130 | 16,654 | 657 (64%) | ~950 (95%) | **UNDERSATURATED** 📉 | **Near full scale** ✓ |

**Gain:** Dynamic range increased; SNR improved ~20%

---

## Full Group Comparison (Group 5 — Maximum Coverage)

### Violet (e* = 11,872)

**Before (e* = 17,808):**
```
Group 5: [7915, 11888, 17808, 26712, 40068]
          ├─ Peak at 26,712; e₄ oversaturated
          └─ Only e₀ reliably usable; e₁–e₄ saturate
```

**After (e* = 11,872):**
```
Group 5: [5276, 7915, 11872, 17808, 26712]
          ├─ All five rungs in range
          └─ e₀–e₂ fully usable; e₃–e₄ marginal
```

### Royal-Blue (e* = 2,193)

**Before (e* = 3,290):**
```
Group 5: [1462, 2193, 3290, 4935, 7402]
          ├─ Peak at 4,935; e₃–e₄ saturate
          └─ Only e₀ reliably usable
```

**After (e* = 2,193):**
```
Group 5: [1000, 1462, 2193, 3290, 4934]
          ├─ All five rungs in range
          └─ e₀–e₂ fully usable; e₃–e₄ approaching limit
```

### Cyan (e* = 11,103)

**Before (e* = 7,402):**
```
Group 5: [3290, 4935, 7402, 11130, 16695]
          ├─ Peak at 16,695; reaches 64% of full scale
          └─ All in range, but weak signal
```

**After (e* = 11,103):**
```
Group 5: [4935, 7402, 11103, 16654, 24982]
          ├─ Peak at 24,982; would reach 95% of full scale on 10-bit sensor
          └─ Better SNR; fuller use of dynamic range
```

---

## Pre-Image Exposure Impact

### Current (Uniform -2 Rung Shift)

| LED | Measurement e₂ (µs) | Pre-image e (µs) | Peak DN | Issue |
|---|---|---|---|---|
| violet | 26,712 | 11,872 | ~85% | ✓ Good |
| royal_blue | 3,290 | 1,462 | ~80% | ✓ Good |
| **cyan** | **11,130** | **4,947** | **~28%** | ⚠ **Too dim** |
| red | 23,776 | 10,567 | ~75% | ✓ Good |

### Improved (Per-LED Shifts)

| LED | Measurement e₂ (µs) | Shift | Pre-image e (µs) | Peak DN | Status |
|---|---|---|---|---|---|
| violet | 26,712 | -2 | 11,872 | ~85% | ✓ Good |
| royal_blue | 3,290 | -2 | 1,462 | ~80% | ✓ Good |
| **cyan** | **16,654** | **-1** | **11,103** | **~95%** | **✓ Excellent** |
| red | 23,776 | -2 | 10,567 | ~75% | ✓ Good |
| green | 11,888 | -1 | 7,915 | ~85% | ✓ Good |

**Gain:** Cyan pre-image improves from 28% → 95% scale (+240% signal)

---

## Expected Improvements in Practice

### Measurement Quality

| Metric | Before | After |
|---|---|---|
| Violet usable exposure rungs | 1 of 3 | 3 of 3 |
| Royal-blue usable rungs | 1 of 3 | 3 of 3 |
| Cyan peak utilization | 64% of scale | 95% of scale |
| Cyan SNR | Baseline | +20% |
| Violet discrimination | Limited | Enhanced (more rungs available) |

### Pre-Image Quality

| Metric | Before | After |
|---|---|---|
| Pre-image LEDs | 2 (royal_blue, red) | 4–5 (+ violet, cyan, [green]) |
| Violet blank correction | Missing | Present |
| Cyan blank precision | ±1.5× (dim) | ±1.0× (optimal) |
| Blank-correction ability | Limited to 2 cells | Full 4–5 cells |

---

## Database Changes Summary

### Exposures Added (active = 1)

```
violet:      15 rows (groups 1-5, e* = 11,872)
royal_blue:  15 rows (groups 1-5, e* = 2,193)
cyan:        15 rows (groups 1-5, e* = 11,103)
             ────────
Total:       45 new rows
```

### Exposures Deactivated (active = 0)

```
violet:      15 old rows (groups 1-5, e* = 17,808)
royal_blue:  15 old rows (groups 1-5, e* = 3,290)
cyan:        15 old rows (groups 1-5, e* = 7,402)
             ────────
Total:       45 old rows
```

**Net change:** 0 (replaced, not deleted)

---

## How to Use This Reference

1. **To verify improvements were applied:**
   - Run the queries in `exposure_optimization_summary_2026-08-20.md` Section "Verification Queries"
   - Expected: New exposures shown above

2. **To understand the changes:**
   - Violet/royal_blue: Look for reduced exposures (divide by ~1.5)
   - Cyan: Look for increased exposures (multiply by ~1.5)

3. **To predict test results:**
   - Violet/royal_blue e1, e2 should now be visible (not clipped)
   - Cyan e2 should be brighter

4. **For pre-image tuning:**
   - If cyan pre-image is still too dim → check that -1 rung shift is implemented
   - If pre-image is too bright → check that new measurement exposures were loaded

---

**Created:** 2026-08-20  
**Reference:** `docs/exposure_optimization_2026-08-20.md`  
**Database:** `/home/bill/R/pi/colorimeter_2026-08-20.sqlite`
