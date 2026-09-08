# Pre-Image Registration: Practical Guide

When computing reflectance `R_inf = sig_measurement / sig_pre`, the pre-image and 
measurement must be spatially aligned. This document explains when to use arithmetic 
vs. empirical registration.

**Note:** All code uses numpy only (no scipy/opencv dependencies) for RPi compatibility.

## Quick Start

### Standard Approach (Recommended for Most Uses)

```python
from analysis_1 import _register, Capture
from probe import PRE

samp_no = "C1014"

# Load measurement and pre-image
c_meas = Capture(samp_no, sec="")
c_pre = Capture(samp_no, sec=PRE)

# Get raw signal (already dark-subtracted by Capture.layer)
sig_meas, _ = c_meas.layer("royal_blue", "e2", "R")
sig_pre, _ = c_pre.layer("royal_blue", "e0", "R")

# Register and compute reflectance
sig_pre_aligned = _register(sig_pre, samp_no)
R_inf = sig_meas / sig_pre_aligned
```

This uses the arithmetic pre_to_post() transform (O(1) speed, ~nm-class accuracy).

### Empirical Refinement (Experimental — Not Recommended)

```python
from padregistration import register_pre_to_post_empirical

# Measure shifts via edge correlation (not recommended)
sig_pre_aligned, shifts = register_pre_to_post_empirical(
    samp_no, sig_pre, sig_meas, max_shift=5
)

R_inf = sig_meas / sig_pre_aligned
```

**Status:** Testing on 11 samples showed empirical registration **degrades** R_inf
by 8.6% on average (−28% to +6%). Arithmetic registration is superior.

**Why it fails:** Edge-based correlation measures shifts from pad boundary regions,
which are noisier and less representative than the interior pad used for actual
measurements. Global mean of edges also fails under rotation (8/11 samples had
misaligned edge shifts).

**Recommendation:** Use arithmetic registration (standard approach). Keep empirical
code as reference for future work (e.g., interior strip selection).

## Decision Tree

Choose based on your use case:

```
Does your analysis need:
├─ Speed > precision?
│  └─ Use arithmetic (_register) only
│
├─ Routine reflectance measurement?
│  └─ Use arithmetic (_register) only
│
├─ Precision dosimetry or dose-response calibration?
│  └─ Use empirical registration
│
├─ Investigating R_inf variability?
│  └─ Try both and compare shifts dict
│
└─ Screening for mechanical/optical issues?
   └─ Use empirical registration; log shifts over time
```

## What the Shifts Tell You

Edge shifts from `measure_registration_shifts()` reveal pad motion and deformation:

| Pattern | Interpretation |
|---|---|
| All edges ≈ 0 | Perfect alignment (uncommon) |
| Left ≈ Right, Top ≈ Bottom | Pure translation (common) |
| Left ≠ Right in x | Possible rotation around y-axis |
| Top ≠ Bottom in y | Possible rotation around x-axis |
| Large scatter between edges | Non-rigid deformation or optical distortion |

## Performance Characteristics

Measurements on C1014 (royal_blue R channel):

| Metric | Arithmetic | Empirical |
|---|---|---|
| **Compute time** | <1 ms | 50–100 ms |
| **R_inf mean** | 1.4522 | 1.4252 |
| **R_inf std** | 0.5406 | 0.4864 |
| **Noise reduction** | — | 10% |
| **Valid pixels** | 19695 | 20081 |
| **Pixel gain** | — | +2% |

The empirical approach adds ~100 ms per layer and reduces noise by ~10%.

## Batch Processing

For processing multiple samples with empirical registration:

```python
from analysis_1 import sections, Capture, locate_pad
from padregistration import register_pre_to_post_empirical
from probe import PRE
import sqlite3
from pathlib import Path

samp_list = ["C1014", "C1016", "C1018"]
db = Path("analysis.sqlite")

for samp_no in samp_list:
    if PRE not in sections(samp_no):
        print(f"{samp_no}: no pre-image, skipping")
        continue
    
    locate_pad(samp_no, write=True)
    
    c_meas = Capture(samp_no, sec="")
    c_pre = Capture(samp_no, sec=PRE)
    
    for led in c_meas.leds:
        for label in c_meas.labels(led):
            for ch in "RGB":
                layer_meas = c_meas.layer(led, label, ch)
                layer_pre = c_pre.layer(led, c_pre.labels(led)[0], ch)
                
                if layer_meas is None or layer_pre is None:
                    continue
                
                sig_meas, _ = layer_meas
                sig_pre, _ = layer_pre
                
                # Empirical registration
                sig_pre_aligned, shifts = register_pre_to_post_empirical(
                    samp_no, sig_pre, sig_meas, max_shift=5
                )
                
                R_inf = sig_meas / sig_pre_aligned
                
                # Log shifts for QC
                print(f"{samp_no}/{led:12s} {ch}: "
                      f"dx={shifts['mean_dx']:+.2f}, dy={shifts['mean_dy']:+.2f}")
    
    c_meas.close()
    c_pre.close()
```

## Troubleshooting

**Q: R_inf has NaN or Inf values**
- Check that `sig_pre` has positive values (>1 DN) where measurements are made.
- Dark-subtracted layers can be negative in low-signal regions; mask them before division.

**Q: Measured shifts seem wrong (>±5 pixels)**
- Verify that pad was located (run `locate_pad(samp_no)` first).
- Check that pre-image and measurement are from the same archive.
- Increase `max_shift` if systematic shifts are larger than 5 pixels.

**Q: Shifts vary a lot between edges**
- May indicate rotation. Check `abs(dx_left - dx_right)` and `abs(dy_top - dy_bottom)`.
- Future enhancement: use per-area shifts to handle rotation.

**Q: R_inf with empirical registration looks worse than arithmetic**
- Unusual but can happen if edges are corrupted (dust, specular glints).
- Use arithmetic as fallback; log which samples showed this behavior.
