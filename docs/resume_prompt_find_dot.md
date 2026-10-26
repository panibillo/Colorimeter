# Resume Prompt: Finding the Dot (`find_dot`)

**Date Created:** 2026-09-27
**Scope:** Only the circle/dot recognition step.  Everything upstream of it
(dark level, hot pixels, gray-chip divisor) is settled and committed
(`a887dd9`, branch `preimage-capture`).  Everything downstream of it (yellow
vs brown areas, dose response) is out of scope here.
**Parent document:** `docs/resume_prompt_multicolor_dot_analysis.md`

---

## How to work with Bill on this

Bill's words: *"Your job is to help me through those steps, not to perform
those steps yourself unless asked to, and then only as far as asked."*

- Plain language, no jargon.  Everything clear, complete and double checked.
- Small steps.  Explain, propose, then **wait**.  Do not write `find_dot`
  until Bill asks for it, and then only as far as he asks.
- Show figures when judging a result by eye.  Raw next to interpreted.
- The open questions below are Bill's to decide.  Offer a recommendation,
  do not settle them yourself.

---

## The samples

- **C1028–C1036** are the new geometry: a circular exposed dot on the
  rectangular pad.  In these images the dot does **not** reach the pad
  edge, and there is plenty of unexposed white pad around it.
- Existing figures that show the dots:
  `desktop/padanalysis/fig/analysis1_C10xx_cells_R_inf.png` (every cell's
  R_inf) and `analysis1_C10xx_pca.png` (the yellow and brown layers).
  These are untracked; they were made with the new pre-processing.

---

## What is already in place (do not redo)

The data `find_dot` will look at comes from this chain.  Details and numbers
are in `desktop/padanalysis/HOWTO.md` §8.11.

1. `sig = lit - dark_level`.  One dark number per Bayer layer per sweep
   (the shortest-exposure dark frame's mean, about 64.0 DN).
2. Hot pixels (73 of them, `hotpixels` table) are **masked**, not repaired.
   The union over the four Bayer layers is used, so every cell has the same
   pixels missing: 0–10 on a pad.  `find_dot` must tolerate a few masked
   pixels inside the dot.
3. `R = sig / div_map`, where `div_map` is the gray-chip flat-field
   polynomial placed per pixel (`padphot.divisor_map`).
4. `R_inf = R / median(R on bare paper)`, then `A = -log10(R_inf)`.
5. PCA / duotone gives per-pixel yellow and brown amounts
   (`analysis_1.cell_PCA` returns `(y, n)`).

The pre-image is **not used** yet (a separate topic, to explore soon).

### Where to get a layer

All on the bound-crop Bayer-layer grid, 130 × 183 layer pixels, as
`numpy.ma.MaskedArray` (masked = outside the region):

```python
import analysis_1 as a1
a  = a1.get_balanced_cell_data("C1032", "royal_blue", "B", quantity="R_inf")
y, n = a1.cell_PCA("C1032", figs=False)       # yellow, brown
pad  = a1.pad_of("C1032").mask(erode)          # boolean pad, eroded
```

`quantity` may be `'sig'`, `'R'`, `'R_inf'`, `'A'`, `'KS'` or `'KS_signed'`.
The pad holds about 1240 layer pixels (roughly 35 × 35).

---

## Bill's specification (2026-09-27, as given)

```
def find_dot(samp_no, cell_name, data, tol, buff)
```

Returns a circle that contains the exposed dot.  First for Bayer-layer
cells; later for PCA layers too.  
Also return: the count of white pixels inside, the count of dot pixels 
outside, and a boolean mask of the circle.

- **samp_no** (text): at least for debugging messages, 
  possibly to access some other information or label plots.
- **cell_name** (text): ditto.
- **data** (ndarray): the extracted pad rect without erosion, with hot 
  pixels as NaN (or include hot pixel mask in the arg list).  For now,
  data will be from one cell layer read from the .h5.
- **tol** (real): the minimum difference from the surrounding unexposed
  white pad that marks a pixel as part of the dot.  
  - The dot is darker in R_inf and higher in A and in the PCA layers.  
  - Initially use unsigned test (`|value - white| >= tol`).  Note 
    in the code and documentation to evaluate whether signed is better.
  - tol is dimensionless, and is multiplied by white pad's, or pre-image
    sample pad's, own pixel-to-pixel noise. 
- **buff** (real): how much room the circle has for pixels that are not
  dot, because the dot may not be perfectly round:
  `count(pixels inside the circle within tol of white) <= buff * pi * D`.
  Read this way, `buff` is the thickness, in layer pixels, of a white band
  allowed around the inside of the circle (π·D·buff is the area of such a
  band).
  	Special values of buff:
  	- buff = 0: no white included
  	- buff = None: no colored pixels excluded 
- **Pad edge:** the current dots do not reach it, so eroding the pad by
  3–6 pixels is safe for now.  Later dots will reach the edge and this will
  not be safe; leave a note in the code and docs to deal with that then.
- **White reference:** could come from the matching pre-image, but the
  pre-image is not used yet.  For now there is plenty of white pad around
  the dot and within the eroded edge, especially toward top and bottom
  and in corners.  
  - Bill suggests to use the median of values inside of the pad eroded by 
  4 pixels and outside of cv2.minEnclosingCircle(). 

### Suggested algorithm:
	`cv2.minEnclosingCircle(contour)`
   Where contour is the white value plus tol, and the algorithm finds 
   the minimum enclosing circle.  
    - If buff = None, solution is the minEnclosing Circle.
    - a small search over (centre, radius) scored directly by the
      `tol`/`buff` rules.  Use binary search for after it is 
      bracketed.  End search when buff pixel count stabilizes.

### Bill's suggested deterministic start, which may not be needed.

1. Take a horizontal bar several pixels high through the middle of the pad.
   Average each column down to one number, giving a profile across the pad.
2. Smooth this (e.g. 3 or 5 point moving average) and take its first derivative.  
   The max and min should be just inside the left and right (right and left)
   edges of the dot: x1, x2
3. Do the same with a vertical bar, finding y1, y2
4. From those 4 points, estimate a center and a radius.
5. Then, with a ready-made method from OpenCV or numpy, vary the circle's
   size and position until `tol` and `buff` are satisfied.

Other candidates to keep in mind but not use now:
- `cv2.HoughCircles`
- `cv2.fitEllipse`.  (also tells how round the dot is)

---

## Data prep method

A data prep method probably exists that will prepare the data for passing
to find_dot()
   - obtain the array data identified by samp_no and cell
   - preprocess it: dark correction, gray scaling, hot pixel removal.
   
   
---

## Suggested first steps (each one waits for Bill)

1. Show one dot sample (e.g. C1032), one clear cell, as R_inf, with the
   eroded pad outline drawn on it.
2. Show the horizontal and vertical profiles and their second derivatives,
   with the proposed edges marked on the profile and the image.
   Let Bill judge them by eye.
3. Run cv2.minEnclosingCircle(contour) using several values for contour, 
   and draw the circles on the image.  
   Let Bill judge them by eye.
4. Only then write `find_dot`, with tests, and a figure drawing the circle
   over the cell for every C1028–C1036 sample.

---

## Out of scope here

- Separating yellow from brown inside the dot; areas of each.
- Using the pre-image.
- Dots that reach the pad edge (note only).
