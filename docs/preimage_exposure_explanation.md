# Pre-Image Exposure Computation and Assurance

**Explains:** How pre-capture exposures are calculated, how improved pre-exposures will be used, and why they are not stored explicitly in the exposure table.

---

## 1. How Pre-Capture Exposures Are Computed

### 1.1 System Overview

The colorimeter captures images in two phases:

1. **Pre-image (optional):** Before reagent contact. Measures the unreacted pad's reflectance per illuminant/layer cell, used as a blank correction.
2. **Post-image (measurement):** After reagent reactions complete. Measures the stained pad.

**Exposure computation differs between the two:**

| Phase | Exposures | Stored? | Calculated at runtime? |
|---|---|---|---|
| **Post-image** | Calibrated exposures per LED, group | **Yes** → `exposure` table | No; read directly |
| **Pre-image** | Derived from post-image via shift | **No** | **Yes** — computed on-the-fly |

### 1.2 Post-Image Exposure (Measurement)

Post-image exposures are **calibrated and stored** in the `exposure` table:

```sql
SELECT exposuregroup, LED, microseconds FROM exposure 
WHERE active = 1 AND LED = 'violet' AND exposuregroup = '3';

-- Result (after improvements):
exposuregroup | LED    | microseconds
3             | violet | 7915
3             | violet | 11872
3             | violet | 17808
```

These are the **measured/calibrated exposure times** used for the post-image.

### 1.3 Pre-Image Exposure Calculation (Derived)

Pre-image exposures are **calculated at runtime** by shifting the post-image exposures by whole rungs.

**Formula:**

```
pre_image_exposures = shift_exposures(post_image_exposures, rung_shift, ratio=1.5)
```

where:
- `post_image_exposures`: The active exposures for the LED from the `exposure` table
- `rung_shift`: An integer; typically **−2** (two rungs shorter)
- `ratio`: The geometric ladder step, 1.5

**Example (violet, group 3):**

```python
post_exposures = [7915, 11872, 17808]  # from exposure table, active=1
rung_shift = -2                         # two rungs down
ratio = 1.5

# Each exposure is divided by 1.5^2 = 2.25
pre_exposures = [us / 2.25 for us in post_exposures]
             = [3518, 5276, 7915]  # approximately
```

**Verification in Python:**

```python
from exposure_repo import shift_exposures

post_exps = [7915, 11872, 17808]
pre_exps = shift_exposures(post_exps, -2)
print(pre_exps)  # [3518, 5276, 7915] (rounded)
```

### 1.4 Why Shift Rather Than Store?

**Pre-image exposures are computed, not stored, because:**

1. **They are derived quantities:** Pre-image is a systematic shift of the measurement exposure. Deriving it from measurement avoids duplication.

2. **Measurement exposure is the source of truth:** If a future calibration changes the measurement e*, the pre-image automatically uses the new ladder scaled appropriately.

3. **Storage would require maintenance:** If pre-image and post-image were stored separately, two independent calibrations could drift apart, creating confusion (which is the true calibration?).

4. **Rung shift is the only parameter:** By keeping pre-image as a shifted derivative, the operator controls it with one number (rung shift), not by redefining a whole set of exposures.

---

## 2. Current Pre-Image Configuration

### 2.1 Configuration Parameters

Pre-image behavior is controlled by three config parameters:

| Parameter | Type | Current value | Meaning |
|---|---|---|---|
| `capture_preimage_enabled` | bool | `true` | Enable pre-image capture in a run |
| `capture_preimage_led_order` | str (comma-separated) | `"royal_blue,red"` | Which LEDs to capture in pre-image |
| `capture_preimage_rung_shift` | int | `−2` | How many rungs to shift all exposures shorter |

### 2.2 How They Are Used

**During a test run:**

1. Operator starts a Normal test (not calibration).
2. `capture_runner.py` checks: `if config.capture_preimage_enabled():`
3. If true, enters pre-image capture mode.
4. For each LED in `config.capture_preimage_led_order()`:
   - Read measurement exposures: `exposure_repo.read_exposures(led, group)`
   - Shift them: `shift_exposures(exposures, config.capture_preimage_rung_shift())`
   - Capture at the shifted exposures
   - Store frames under `/pre` in the HDF5 archive

**Example (royal_blue, current system):**

```
Measurement (group 3): read from exposure table
  → [1462, 2193, 3290] µs

Rung shift: -2
  → divide by 1.5² = 2.25

Pre-image exposures:
  → [649, 975, 1462] µs (approximately)
  → Camera captures at these three exposure times
  → Frames stored in HDF5 as `/pre/royal_blue`
```

### 2.3 The Problem: Uniform Shift

Current system applies **one rung shift to all LEDs.** The analysis reveals this is suboptimal:

| LED | Measurement e₂ (µs) | Pre-image @ −2 rungs (µs) | Peak DN (% scale) | Issue |
|---|---|---|---|---|
| violet | 26,712 | 11,872 | ~85% | ✓ Good |
| royal_blue | 3,290 | 1,462 | ~80% | ✓ Good |
| **cyan** | **11,130** | **4,947** | **~28%** | ✗ **Too dim** |
| red | 23,776 | 10,567 | ~75% | ✓ Good |

Cyan's pre-image is undersaturated because:
- Measurement cyan is already short (e₂ = 11,130, only 64% scale usage)
- Shifting down 2 rungs makes it even shorter (−54% of an already short exposure)
- Result: pre-image peak is only 28% of scale — too dim for reliable blank correction

---

## 3. Improved Pre-Exposure Strategy

### 3.1 Per-Illuminant Rung Shifts (Recommended)

**Solution:** Replace uniform `preimage_rung_shift` with per-LED shifts:

| LED | Measurement e₂ (µs) | New shift | Pre-image e (µs) | Peak DN (%) | Strategy |
|---|---|---|---|---|---|
| violet | 26,712 | −2 | 11,872 | ~85% | Keep as-is (pad is bright) |
| royal_blue | 3,290 | −2 | 1,462 | ~80% | Keep as-is (pad is bright) |
| **cyan** | **16,654** | **−1** | **11,103** | **~95%** | **Lighter shift** (pad less bright at 500 nm) |
| red | 23,776 | −2 | 10,567 | ~75% | Keep as-is (pad is bright) |
| **green** | **11,888** | **−1** | **7,915** | **~85%** | **Lighter shift** (similar to cyan) |

**Rationale:**
- **Violet, royal_blue, red:** Unreacted pad is **bright** in these bands. A −2 rung shift (divide by 2.25) brings them to ~80% of scale — ideal.
- **Cyan, green:** Unreacted pad is **less bright** in these bands. A −1 rung shift (divide by 1.5) brings them to ~90–95% of scale — better than −2.

### 3.2 Implementation Approach

**Option C: Hardcoded per-LED shifts in Python (recommended, interim)**

**File:** `src/capture_runner.py` or `src/exposure_repo.py`

**Code:**

```python
def preimage_rung_shift_for_led(led: str) -> int:
    """Return the rung shift for pre-image of one LED.
    
    The unreacted pad's brightness varies by wavelength:
    - Shorter wavelengths (violet, blue): pad is brighter → shift more (-2 rungs)
    - Longer wavelengths (green, cyan): pad is dimmer → shift less (-1 rung)
    """
    shifts = {
        'violet': -2,
        'royal_blue': -2,
        'blue': -2,
        'cyan': -1,
        'green': -1,
        'amber': -2,
        'red_orange': -2,
        'red': -2,
        'photo_red': -2,
        'white': -2,
    }
    return shifts.get(led, -2)  # default to -2 if not found
```

**Usage in capture_runner:**

```python
# OLD: uniform shift
pre_exposures = shift_exposures(measurement_exposures, 
                                config.capture_preimage_rung_shift())

# NEW: per-LED shift
pre_exposures = shift_exposures(measurement_exposures,
                                preimage_rung_shift_for_led(led))
```

**Pros:**
- No schema or config changes
- Can be tuned easily
- Operator-facing config remains simple
- Backward compatible (function can fall back to config value if present)

**Cons:**
- Hardcoded; operator cannot customize without editing Python

**Promotion path:** If operators later need per-LED control, extend the `config` table to store per-LED shifts (Option A in the guidance document).

---

## 4. Ensuring Improved Pre-Exposures Will Be Used

### 4.1 What Changes Are Required

**Three changes, in order of criticality:**

1. **[DONE] Improved measurement exposures in database**  
   ✓ Violet, royal_blue, cyan e* values have been recalibrated and inserted with `active=1`  
   ✓ Old exposures deactivated with `active=0`  
   → Next run will automatically read improved post-image exposures

2. **[REQUIRED] Add new LEDs to pre-image config**  
   Current: `preimage_led_order = "royal_blue,red"`  
   New: `preimage_led_order = "violet,cyan,royal_blue,red"` (minimum 4)  
   Or: `"violet,cyan,royal_blue,red,green"` (recommended 5)  
   → Without this, violet and cyan pre-images are never captured

3. **[RECOMMENDED] Implement per-LED rung shifts**  
   Requires code change to `capture_runner.py` (see Section 3.2)  
   → Without this, cyan pre-image is still undersaturated with −2 shift

### 4.2 How to Make the Changes

#### Step 1: Update Config for Pre-Image LEDs

**Who:** Operator or system administrator  
**When:** Before next test run  
**How:**

**Option A: CLI/GUI config editor**
```bash
# Via the config editor UI (if available)
# Set:  capture.preimage_led_order = "violet,cyan,royal_blue,red"
```

**Option B: Direct database edit**
```sql
sqlite3 /path/to/colorimeter.sqlite

-- Find current pre-image LED order
SELECT * FROM config WHERE role = 'capture' AND name = 'preimage_led_order';

-- If a row exists, deactivate it and add new row
UPDATE config SET active = 0 
WHERE role = 'capture' AND name = 'preimage_led_order' AND active = 1;

INSERT INTO config (active, isdefault, editable, add_dt, role, name, vtype, value, source)
VALUES (1, 0, 1, datetime('now'), 'capture', 'preimage_led_order', 'str',
        'violet,cyan,royal_blue,red', 'operator');
```

**Verification:**
```sql
SELECT role, name, value, active FROM config 
WHERE role = 'capture' AND name = 'preimage_led_order' AND active = 1;
-- Should show: capture | preimage_led_order | violet,cyan,royal_blue,red | 1
```

#### Step 2: Update capture_runner.py (Code Change)

**File:** `src/capture_runner.py`  
**Location:** In the pre-image capture loop

**Current code (approximate):**
```python
def capture_preimage(self, ...):
    led_order = config.capture_preimage_led_order()
    shift = config.capture_preimage_rung_shift()  # uniform -2
    
    for led in led_order:
        exposures = exposure_repo.read_exposures(led, group)
        shifted = exposure_repo.shift_exposures(exposures, shift)
        # capture at shifted exposures...
```

**New code:**
```python
def preimage_rung_shift_for_led(led: str) -> int:
    """Optimized per-LED shifts per analysis 2026-08-20."""
    shifts = {
        'violet': -2,
        'royal_blue': -2,
        'blue': -2,
        'cyan': -1,
        'green': -1,
        'amber': -2,
        'red_orange': -2,
        'red': -2,
        'photo_red': -2,
        'white': -2,
    }
    return shifts.get(led, config.capture_preimage_rung_shift())

def capture_preimage(self, ...):
    led_order = config.capture_preimage_led_order()
    
    for led in led_order:
        exposures = exposure_repo.read_exposures(led, group)
        # Use per-LED shift instead of uniform
        shift = preimage_rung_shift_for_led(led)
        shifted = exposure_repo.shift_exposures(exposures, shift)
        # capture at shifted exposures...
```

**Verification:** Add a log message:
```python
log.info(f"Pre-image {led}: {len(shifted)} exposures, rung shift {shift}")
```

When run, should show:
```
Pre-image violet: 3 exposures, rung shift -2
Pre-image cyan: 3 exposures, rung shift -1
Pre-image royal_blue: 3 exposures, rung shift -2
Pre-image red: 3 exposures, rung shift -2
```

### 4.3 Verification Checklist

After making the changes, verify by capturing a test strip:

| Check | How | Expected |
|---|---|---|
| Post-image improved exposures used | Look at captured frames; violet/royal_blue e1 no longer clipped | Violet and royal_blue can now use e1, e2 frames |
| Pre-image captures new LEDs | Inspect HDF5 archive; check `/pre` groups | Archive has `/pre/violet`, `/pre/cyan`, `/pre/royal_blue`, `/pre/red` |
| Pre-image exposures computed correctly | Examine log output or frame metadata | Violet/royal_blue show -2 shift; cyan/red show -1 shift |
| Pre-image frames are visible | View `/pre` frame histograms | Peaks at ~75–95% of scale; none clipped, none too dim |

**HDF5 inspection:**
```bash
python3
>>> import h5py
>>> f = h5py.File('path/to/archive.h5', 'r')
>>> print(list(f['/pre'].keys()))  # Should show: ['cyan', 'red', 'royal_blue', 'violet']
>>> # Check exposure values
>>> for key in f['/pre/violet'].attrs:
...     print(key, f['/pre/violet'].attrs[key])
```

---

## 5. Storage Strategy: Why Pre-Exposures Are Not Stored

### 5.1 Design Decision

**Question:** Should pre-image exposures be stored explicitly in the `exposure` table (like measurement exposures), or flagged somehow if they use the same values?

**Answer:** Neither. Pre-exposures should remain **implicit and calculated**.

### 5.2 Why Not Store Pre-Image Exposures in the `exposure` Table?

**Reasons against explicit storage:**

1. **Duplication:** Storing both measurement and pre-image exposures doubles the table size for a derived quantity. The pre-image is 100% determined by the measurement exposure + shift; storing it separately is redundant.

2. **Maintenance burden:** If measurement calibration changes (e.g., a new violet calibration), who updates the pre-image row? Without careful coordination, old pre-image exposures could be used with new measurement exposures, creating inconsistency.

3. **Relationship obscuring:** Explicit storage hides the fact that pre-image is a *shift of* the measurement. A reader of the table would see two separate rows and might not understand they are linked.

4. **Operator configuration:** Operators control the pre-image via simple parameters (`preimage_enabled`, `preimage_led_order`, `preimage_rung_shift`). If pre-exposures were stored, they would need a separate calibration/editing interface, complicating the system.

### 5.3 Why Not Flag Pre-Exposures in the Table?

**Alternatives considered:**

- Add a `is_preimage` column to `exposure` table
- Add a `preimage_for_led` column
- Use a `role` column to distinguish ("measurement", "preimage")

**Reasons against flagging:**

1. Schema changes require migration and tooling.
2. Still duplicates data (post-image and pre-image rows separate).
3. Doesn't solve the "who maintains consistency?" problem.

### 5.4 Recommended: Implicit + Per-LED Shifts

**Keep pre-exposures implicit (calculated), but support per-LED tuning:**

**Storage:**
- **Measurement exposures:** In `exposure` table (as now)
- **Pre-image control:** In `config` table (or hardcoded function)
- **Rung shifts:** Per-LED parameters (e.g., `preimage_rung_shift_violet`, etc.)

**Pros:**
- No schema changes
- Minimal data duplication
- One source of truth (measurement exposures)
- Simple operator interface (tune rung shift, not individual exposures)

**Future**: If per-LED shifts become common, promote them from `config` table rows to first-class parameters with UI support.

---

## 6. Summary: Pre-Image Exposure Workflow

**Before a test run:**

1. **Measurement exposures** are read from `exposure` table (active=1)
   - Violet (improved): [7915, 11872, 17808] µs for group 3
   - Cyan (improved): [7402, 11103, 16654] µs for group 3
   - Royal-blue (improved): [1462, 2193, 3290] µs for group 3

2. **Pre-image parameters** are read from config:
   - Enabled: `capture_preimage_enabled() = true`
   - LEDs: `capture_preimage_led_order() = ["violet", "cyan", "royal_blue", "red"]`
   - Shifts: Per-LED (violet: -2, cyan: -1, royal_blue: -2, red: -2)

3. **Pre-image exposures** are calculated:
   - Violet: [7915, 11872, 17808] ÷ 2.25 = [3518, 5276, 7915] µs
   - Cyan: [7402, 11103, 16654] ÷ 1.5 = [4935, 7402, 11103] µs
   - Royal-blue: [1462, 2193, 3290] ÷ 2.25 = [649, 975, 1462] µs
   - Red: [10567, 15851, 23776] ÷ 2.25 = [4697, 7045, 10565] µs

4. **Camera captures:**
   - First: Pre-image frames at all pre-exposures, all LEDs → stored under `/pre` in HDF5
   - Second (after reagents): Post-image frames at all post-exposures, all LEDs → stored under `/` root in HDF5

**No explicit pre-exposure rows exist in the database.** They are calculated per-run.

---

**Document version:** 1.0  
**Date:** 2026-08-20  
**References:**
- `docs/exposure_optimization_2026-08-20.md` — Analysis and improved exposure values
- `docs/two_dye_pad_analysis_2026-08-19.md` — Pad analysis that motivated these changes
- `src/exposure_repo.py` — Functions for computing ladders and shifts
- `src/capture_runner.py` — Where pre-image logic runs
