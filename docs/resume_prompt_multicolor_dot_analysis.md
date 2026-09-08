# Resume Prompt: Multi-Color Dot Analysis Investigation

**Date Created:** 2026-09-08  
**Priority:** High — New measurement geometry requires careful step-by-step review  
**Approach:** Algebraic + visual investigation of each processing step

---

## Context & Motivation

Recent work (empirical registration, pre-image framework) moved quickly through implementation. The new sample exposure geometry changes the measurement problem fundamentally and requires **careful, deliberate investigation** of:

1. How the color dot forms and grows
2. How to separately quantify area vs. color
3. How multiple colors (yellow, brown) interact in measurement
4. How erosion affects measurement precision at different dot sizes

**Key insight:** The new geometry may be **more analyzable than the old spread-pattern**, but only if we understand each step carefully. Moving too fast risks missing the physics.

---

## New Measurement Geometry

### The Dot Pattern

- **Formation:** Color appears as a **dot in the center** of the pad (different from old spread pattern)
- **Growth:** As exposure increases (longer time), dot grows larger
- **Color separation:** At higher exposures, two distinct colors emerge:
  - **Yellow** (outer region) — quantify as (area_yellow, R_yellow)
  - **Brown** (inner region) — quantify as (area_brown, R_brown)
  - Possibly 3 colors, but likely not
- **Small dot regime:** When dot is small, noisy pad edges are irrelevant
- **Large dot regime:** When dot is large, erosion (background removal) loses critical pad edge information

### The Challenge

The old analysis used erosion (ERODE = 6 pixels) to remove noisy edges. This works fine when the color is spread across the whole pad. But with a **small central dot**, erosion may remove the dot itself or cut into it. With a **large dot**, erosion removes the only pixels we care about at the pad edge.

---

## Investigation Goals

### Primary Goals

1. **Algebraic understanding:** For each step in the processing chain:
   - Write the formula/operation in plain algebra
   - Explain why that step is needed
   - Identify assumptions (what could go wrong?)

2. **Visual understanding:** For each step:
   - Show example images (pre-image, measurement, intermediate stages)
   - Plot R_inf, K/S, or absorption maps
   - Identify where the dot appears and how it changes with exposure

3. **Multi-color quantification:** Develop method to:
   - Identify color boundaries (yellow ↔ brown)
   - Measure area of each region
   - Compute R (reflectance) for each color separately
   - Handle partial-pixel boundaries cleanly

4. **Erosion optimization:** For different dot sizes:
   - What erosion value preserves the dot without noise?
   - Can we use adaptive erosion based on dot size?
   - What is lost at the pad edge when dot is large?

### Secondary Goals

- Identify whether the old spread-pattern analysis code still applies
- Determine if K/S or absorption (A) is the right metric for the dot
- Check if existing `contact_sheet()`, `cell_table()` functions need modification
- Assess whether per-color analysis requires new database schema

---

## Starting Points

### Key Files to Review Carefully

1. **`desktop/padanalysis/analysis_1.py`**
   - Lines 680–708: `_measured_region()` — erosion logic
   - Lines 710–770: `rung_verdict()` — rung selection
   - Lines 1445+: `cell_table()` — reads cells; may need per-color extension
   - Start here: trace through for a new-geometry sample

2. **`desktop/padanalysis/R_inf_guide.py`** (if exists)
   - Visual examples of R_inf computation
   - May show old spread-pattern; compare to new dot pattern

3. **`desktop/padanalysis/HOWTO.md`** (if exists)
   - Old pipeline documented here; identify what changes

4. **`docs/reference_framework_preimage_and_chips.md`**
   - Pre-image division method is still valid
   - Erosion (erode=6) may need rethinking for dot geometry

### Sample Data

- **New geometry samples:** ? (ask which captures use new geometry)
- **Old geometry samples:** C1014–C1025 (pre-image samples documented earlier)
- **Analysis:** Load one new-geometry sample, step through `analysis_1.py` by hand

### Concepts to Clarify

- **Dot detection:** How do we identify the boundary between yellow/brown?
- **Area measurement:** Pixels in region vs. actual area (accounting for pixel size)
- **Color separation:** If colors are layered, do we measure mixed pixels or separate them?
- **Dose response:** Does the dot area track dose linearly? Does color shift (yellow→brown) correlate with dose?

---

## Investigation Checklist

### Phase 1: Understand Current Pipeline (Old Geometry)

- [ ] Pick one C1014–C1025 sample; load measurement and pre-image
- [ ] Step through `analysis_1.py:locate_pad()` → understand pad geometry
- [ ] Step through `pre_to_post()` → understand registration
- [ ] Step through `_measured_region(erode=6)` → what gets masked?
- [ ] Compute R_inf manually for one cell; verify against code
- [ ] Visualize: plot R_inf map, highlight measured region
- [ ] Write algebraic formula for each step (what goes in, what comes out?)

### Phase 2: Compare to New Geometry

- [ ] Load one new-geometry sample (ask which one)
- [ ] Run `locate_pad()` on new sample; does it work?
- [ ] Visualize: measurement image, pre-image, R_inf map
- [ ] Question: Is the dot visible in R_inf? What does it look like?
- [ ] Question: Does `_measured_region(erode=6)` cut into the dot?
- [ ] Sketch: draw pad boundary, erosion boundary, dot location, and dot size

### Phase 3: Multi-Color Analysis

- [ ] At high exposure: can you visually identify yellow vs brown in raw image?
- [ ] Question: Are colors spatially separated (regions) or overlapped (mixed pixels)?
- [ ] If regions: what's the boundary? Sharp or gradual?
- [ ] Hypothesis: write out how you would separate yellow and brown (algorithm sketch)
- [ ] Test: implement color separation on one high-exposure image
- [ ] Measure: area of each region, R (reflectance) of each region
- [ ] Plot: how do area_yellow, area_brown, R_yellow, R_brown change across exposure ladder?

### Phase 4: Erosion Strategy

- [ ] For small dot (low exposure): what's the minimum erosion to avoid pad edge noise?
- [ ] For large dot (high exposure): what erosion removes pixels we care about?
- [ ] Hypothesis: adaptive erosion based on dot size
- [ ] Alternative: mask the dot region explicitly (don't rely on erosion)
- [ ] Compare: does explicit masking preserve information better than erosion?

### Phase 5: Integration

- [ ] Identify which functions in `analysis_1.py` need modification
- [ ] Update `cell_table()` to return per-color data (if applicable)
- [ ] Update database schema (if new columns needed)
- [ ] Test on full archive (new geometry samples)

---

## Questions to Answer (Step by Step)

### Algebraic Questions

1. Why do we divide by pre-image? What assumption does this make about illumination?
2. When we compute R_inf pixel-by-pixel, are we assuming R_inf is constant over each pixel? Why?
3. How does erosion change the measured region? What information is kept, what is lost?
4. If we separate yellow and brown regions, how do we handle partial-pixel boundaries?

### Visual Questions

1. In the new geometry, where is the dot? How big is it at lowest exposure? Highest?
2. At what exposure does the dot become bicolor (yellow + brown)?
3. Does the yellow region stay fixed while brown grows inside? Or does yellow shrink as brown grows?
4. What does R_inf look like for yellow? For brown? Different values or same?

### Practical Questions

1. Can we use the old `cell_table()` code, or do we need a new function?
2. Should we store (area_yellow, R_yellow) and (area_brown, R_brown) separately, or compute a combined metric?
3. How sensitive is the measurement to erosion value? Test with ERODE ∈ {0, 2, 4, 6}?
4. At what dot size does erosion become problematic? Can we quantify this?

---

## Deliverables (for each phase)

- [ ] **Algebraic notes:** Formula for each step; assumptions stated
- [ ] **Visual outputs:** Plots of R_inf, K/S, or absorption; marked with dot location/size
- [ ] **Code traces:** Hand-traced execution through `analysis_1.py` for one sample (comments in margins)
- [ ] **Hypothesis document:** Sketch of multi-color separation algorithm
- [ ] **Test results:** Quantified area and R for yellow/brown across exposure ladder
- [ ] **Integration plan:** List of functions to modify, new database columns, test cases

---

## Resources

- **Existing code:** `desktop/padanalysis/analysis_1.py`, `probe.py`, `padrect.py`
- **Prior work:** `docs/reference_framework_preimage_and_chips.md`, `docs/two_dye_pad_analysis_*.md`
- **Visuals:** `desktop/padanalysis/R_inf_guide.py` (examples of R_inf visualization)
- **Shelved investigation:** `docs/empirical_registration_investigation.md` (don't revisit; use arithmetic)

---

## Tempo Note

**Move slowly.** Each step should be understood algebraically and visualized. Don't skip steps to "move fast." If something doesn't make sense, stop and diagram it. The new geometry is simpler than the old spread-pattern *if* we understand the physics. Understanding takes time.

---

## Next Session

When resuming:
1. Pick one sample (new geometry, if available; otherwise C1014 from old geometry)
2. Load measurement and pre-image
3. Start with Phase 1, Checkpoint 1: run `locate_pad()`, visualize
4. Work through algebraic + visual understanding of each function
5. Only after understanding the full chain: propose modifications for multi-color analysis
