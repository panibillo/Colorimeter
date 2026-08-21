# Exposure Optimization — Summary and Next Steps

**Date:** 2026-08-20  
**Status:** Improved exposures inserted into database; pre-image strategy documented  
**Database:** `/home/bill/R/pi/colorimeter_2026-08-20.sqlite`

---

## What Was Done Today

### 1. Analysis Complete ✓

- Reviewed `docs/two_dye_pad_analysis_2026-08-19.md` Section 11.3
- Identified three exposure ladder problems:
  - **Violet oversaturation:** Current e* = 17,808 µs → improved to 11,872 µs (−1 rung)
  - **Royal-blue oversaturation:** Current e* = 3,290 µs → improved to 2,193 µs (−1 rung)
  - **Cyan undersaturation:** Current e* = 7,402 µs → improved to 11,103 µs (+1 rung)

### 2. Improved Exposures Written to Database ✓

**File:** `/home/bill/R/pi/colorimeter_2026-08-20.sqlite`

**Action taken:**
- Inserted improved exposure ladders for violet, royal_blue, cyan (groups 1–5)
- All new rows have `active=1` and `add_dt='2026-08-20 00:00:00'`
- Old exposures for these three LEDs have been deactivated (`active=0`)
- 45 new rows total (15 per LED × 3 LEDs)

**Verification query:**
```sql
SELECT LED, exposuregroup, COUNT(*) as count, MIN(microseconds) as min_us, MAX(microseconds) as max_us
FROM exposure 
WHERE active = 1 AND LED IN ('violet', 'royal_blue', 'cyan')
GROUP BY LED, exposuregroup
ORDER BY LED, exposuregroup;
```

**Expected output (45 active rows total):**
```
LED         exposuregroup  count  min_us   max_us
cyan        1              1      11103    11103
cyan        2              2      9066     13598
cyan        3              3      7402     16654
cyan        4              4      6044     20398
cyan        5              5      4935     24982
royal_blue  1              1      2193     2193
royal_blue  2              2      1791     2686
royal_blue  3              3      1462     3290
royal_blue  4              4      1194     4029
royal_blue  5              5      1000     4934
violet      1              1      11872    11872
violet      2              2      9693     14540
violet      3              3      7915     17808
violet      4              4      6462     21810
violet      5              5      5276     26712
```

### 3. Documentation Complete ✓

Two guidance documents created in `/home/bill/Projects/Colorimeter/docs/`:

1. **`exposure_optimization_2026-08-20.md`** (11 KB)
   - Complete analysis of current saturation issues
   - Numerical comparison table
   - Recommended improved exposures for all five groups
   - Pre-image LED order recommendations
   - Per-illuminant rung shift strategy
   - Database implementation SQL
   - Reasoning for design decisions
   - Appendix with calculation formulas

2. **`preimage_exposure_explanation.md`** (7 KB)
   - How pre-capture exposures are computed (formula + example)
   - Why they are calculated, not stored
   - Current config parameters and how they work
   - Per-LED shift implementation options
   - Step-by-step guide to update config and code
   - Verification checklist
   - Storage strategy rationale

---

## How Improved Exposures Will Be Used

### Automatically (No Action Required)

**Post-image (measurement) capture:**
- Next Normal test run will automatically use improved exposures
- No code change needed; system reads `WHERE active=1` from `exposure` table
- Old exposures remain in database as history (`active=0`)

**Visibility:**
- Camera captures violet/royal_blue at e1 and e2 without saturation
- Cyan captures use ~95% of full scale instead of 64%
- Signal-to-noise improved for all three LEDs

### Conditionally (Configuration Required)

**Pre-image capture requires two config changes:**

1. **Add violet and cyan to pre-image LED list** (config table edit)
   - Current: `preimage_led_order = "royal_blue,red"`
   - Recommended: `"violet,cyan,royal_blue,red"` (4 LEDs minimum)
   - Or: `"violet,cyan,royal_blue,red,green"` (5 LEDs recommended)
   - **Without this change:** Violet and cyan pre-images will not be captured

2. **Implement per-LED rung shifts** (code edit, `src/capture_runner.py`)
   - Current: Uniform `preimage_rung_shift = -2` for all LEDs
   - Improved: Per-LED shifts (cyan/green: -1; others: -2)
   - **Without this change:** Cyan pre-image will still be undersaturated

---

## Verification Queries

### Query 1: Confirm Improved Violet Exposures

```sql
SELECT exposuregroup, microseconds FROM exposure 
WHERE LED = 'violet' AND active = 1 
ORDER BY exposuregroup, microseconds;
```

**Expected group 3:** 7915, 11872, 17808  
**Old group 3:** 11888, 17808, 26712 (now `active=0`)

### Query 2: Confirm Improved Royal_blue Exposures

```sql
SELECT exposuregroup, microseconds FROM exposure 
WHERE LED = 'royal_blue' AND active = 1 
ORDER BY exposuregroup, microseconds;
```

**Expected group 3:** 1462, 2193, 3290  
**Old group 3:** 2193, 3290, 4935 (now `active=0`)

### Query 3: Confirm Improved Cyan Exposures

```sql
SELECT exposuregroup, microseconds FROM exposure 
WHERE LED = 'cyan' AND active = 1 
ORDER BY exposuregroup, microseconds;
```

**Expected group 3:** 7402, 11103, 16654  
**Old group 3:** 4935, 7402, 11130 (now `active=0`)

### Query 4: Count All Active Exposures (Should Be Unchanged Total)

```sql
SELECT COUNT(*) as total_active_exposures FROM exposure WHERE active = 1;
```

**Expected:** Same as before. We replaced 45 rows (3 LEDs × 15 rows each), so total count unchanged.

### Query 5: Show Old Exposures (Now Inactive)

```sql
SELECT LED, exposuregroup, microseconds, active 
FROM exposure 
WHERE LED IN ('violet', 'royal_blue', 'cyan') AND active = 0
ORDER BY LED, exposuregroup, microseconds
LIMIT 20;
```

**Expected:** Old exposures with `active=0`

---

## Next Steps (By Priority)

### Phase 1: Validate Post-Image Improvement (1–2 hours)

**[IMMEDIATE] Capture a test strip with improved exposures**

1. Start a Normal test run (not calibration)
2. Use exposure group 3 (the recommended group)
3. Let the post-image capture complete (~5 min for full sweep)
4. Check the HDF5 archive

**What to look for:**
- Violet and royal_blue e1 and e2 frames are now visible (not clipped)
- Cyan e2 frame is brighter (~95% scale instead of 64%)
- No frames are under-exposed

**How to inspect:**
```python
import h5py
import numpy as np

f = h5py.File('path/to/archive.h5', 'r')

# Check violet e0, e1, e2
for rung in ['e0', 'e1', 'e2']:
    data = f[f'/violet/{rung}/B'][()]  # B layer
    print(f"violet {rung}: min={data.min()}, max={data.max()}, mean={data.mean():.0f}")
    # e0, e1, e2 should all be usable (max < 1000, mean 100-500)

# Check cyan e2
data = f['/cyan/e2/B'][()]
print(f"cyan e2: max={data.max()}")  # Should be ~950-1000 (95%+ scale)
```

### Phase 2: Enable Pre-Image Capture (2–3 hours)

**[WEEK 1] Add violet and cyan to pre-image LEDs**

1. Edit config to add new LEDs:
   ```sql
   UPDATE config SET active = 0 
   WHERE role = 'capture' AND name = 'preimage_led_order' AND active = 1;
   
   INSERT INTO config (active, isdefault, editable, add_dt, role, name, vtype, value, source)
   VALUES (1, 0, 1, datetime('now'), 'capture', 'preimage_led_order', 'str',
           'violet,cyan,royal_blue,red', 'operator');
   ```

2. Capture another test strip

3. Verify `/pre` section contains all four LEDs:
   ```python
   f = h5py.File('path/to/archive.h5', 'r')
   print(list(f['/pre'].keys()))  # Should show ['cyan', 'red', 'royal_blue', 'violet']
   ```

### Phase 3: Implement Per-LED Rung Shifts (4–6 hours)

**[WEEK 2] Code change to support cyan/green -1 shift**

1. Locate pre-image capture logic in `src/capture_runner.py`

2. Add per-LED shift function:
   ```python
   def preimage_rung_shift_for_led(led: str) -> int:
       shifts = {
           'violet': -2, 'royal_blue': -2, 'cyan': -1,
           'red': -2, 'green': -1, 'white': -2,
           'blue': -2, 'amber': -2, 'red_orange': -2, 'photo_red': -2
       }
       return shifts.get(led, -2)
   ```

3. Update pre-image capture loop to use per-LED shift

4. Add logging to verify shifts:
   ```python
   log.info(f"Pre-image {led}: rung_shift {preimage_rung_shift_for_led(led)}")
   ```

5. Test by capturing a strip and checking log output

### Phase 4: Validation (Ongoing)

**[WEEK 3–4] Verify end-to-end performance**

1. Capture multiple test strips with improved exposures and full pre-image set
2. Run pad analysis on archives (`desktop/padanalysis/report14.py`)
3. Verify:
   - No saturation in violet, royal_blue
   - Cyan SNR improved (higher peak in pre-image)
   - Blank correction quality improved (better pre-image)

---

## Files Modified / Created

### Created

- **`docs/exposure_optimization_2026-08-20.md`** — Full analysis and recommendations
- **`docs/preimage_exposure_explanation.md`** — How pre-exposure calc works
- **`docs/exposure_optimization_summary_2026-08-20.md`** — This file

### Modified

- **`/home/bill/R/pi/colorimeter_2026-08-20.sqlite`**
  - 45 rows inserted into `exposure` table (active=1)
  - Old violet/royal_blue/cyan rows deactivated (active=0)
  - No schema changes

### Unchanged (For Later)

- `src/capture_runner.py` — Will need per-LED shift implementation
- `src/config_repo.py` — Config read functions unchanged
- `src/exposure_repo.py` — `shift_exposures()` unchanged; already supports per-rung shifts

---

## Key Decision Points

### Storage of Pre-Exposures: Implicit (Calculated) vs. Explicit (Stored)

**Decision:** Keep pre-exposures **implicit and calculated** at runtime.

**Rationale:**
- Pre-image is a derived quantity (measurement shift ÷ 1.5^n)
- Storing both would duplicate data and create maintenance burden
- Rung shift is simpler than storing separate sets of exposures
- Operator controls via config (preimage_led_order, preimage_rung_shift)

**Implementation:** Per-LED shifts hardcoded in function; can promote to config table later if needed.

### Per-Illuminant vs. Uniform Rung Shift

**Decision:** Implement **per-illuminant rung shifts** to fix cyan undersaturation.

**Current (uniform -2):**
```
violet    -2 rungs → 85% scale ✓
cyan      -2 rungs → 28% scale ✗ (too dim)
```

**Improved (per-LED):**
```
violet    -2 rungs → 85% scale ✓
cyan      -1 rung  → 95% scale ✓
```

**Cost:** 20 lines of code to add per-LED shift function.

---

## Important Notes

### Backward Compatibility

- Old exposures remain in database (`active=0`) for reference
- Existing test runs won't be affected; they read `WHERE active=1`
- Operator can restore old exposures by flipping active flags if needed

### Pre-Image is Optional

- Pre-image capture is controlled by config flag `capture_preimage_enabled`
- Tests can proceed without pre-image (operator can disable if needed for speed)
- Recommended to leave enabled for accurate blank correction

### Geometric Ladder Assumption

- All exposure calculations assume ratio 1.5 (the current standard, locked in 2026-07)
- If ratio changes, ladder formulas must be recalculated
- All three improved e* values are based on ratio 1.5

---

## Contact and Questions

**Analysis basis:** `docs/two_dye_pad_analysis_2026-08-19.md`  
**Technical reference:** `src/exposure_repo.py` (ladder math)  
**Pre-image logic:** `src/capture_runner.py` (look for `MODE_PRE`)

**For questions about:**
- Exposure optimization → See `docs/exposure_optimization_2026-08-20.md` Sections 2–3
- Pre-image computation → See `docs/preimage_exposure_explanation.md` Section 1
- Implementation steps → See Phase 1–4 in this document

---

**Document version:** 1.0  
**Created:** 2026-08-20  
**Status:** Complete — ready for Phase 1 validation
