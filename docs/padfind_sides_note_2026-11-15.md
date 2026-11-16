# Pad sides: rectangle kept, curved sides shelved (2026-11-15)

**Decision (Bill, 2026-11-15):** keep the pad as a rectangle (two parallel
straight sides). Don't fit curved or row-by-row sides for now: keep things
simple.

## Background

On the same day the pad's top began to be measured instead of placed
(commit d64900f, `src/padfind.py` step 5, desktop `padrect._measure_top`).
After that fix the question was whether the sides needed the same attention.
On the strip loaded on rpizero3 that day, the mask looked about half a pixel
to the right of the pad in the photo_red picture, and its left edge bowed.

## What was measured

"Row edge" means each row's own side edge: the place where the photo_red
signal changes most steeply from one column to the next, refined to a
fraction of a pixel (the rule the fit itself uses). The numbers compare it
with the fitted straight lines, in layer px, over the 30 F/G/H strips, pre-
and post-images (60 sweeps). Script: the session's scratch `sides.py`, not
kept. Rerun it from this description if the question comes back.

| What | Size |
|---|---|
| Scatter of row edges about the fitted line | sd 0.25-0.29 px |
| Of that, row-to-row noise | sd 0.13 px |
| How much wider the pad is mid-pad than at its ends (a parabola fitted to each side's row edges) | median +0.19 px, range -1.54 to +0.96 px |
| The rpizero3 strip of 2026-11-15 | +1.08 px |
| Pad pixels that a row-by-row side would assign differently from the rectangle | median 0.7 % of the pad, at most 1.5 % |

Nearly all of those pixels are edge pixels that are partly lit and partly
dark either way.

**The half-pixel shift is not a fitting error.** Before the offset is added,
the fitted lines sit on the row edges with no average shift (least squares).
The pre-image offset `capture.padfind_offset_pre` moves the left side
0.10 px and the right side 0.44 px to the right, to where the desktop's
all-channel fit puts them. That's the same per-LED question as the top's
0.43 px offset: blue light sees the pad's edges in slightly different
places from photo_red.

## Why it stopped

Following the bow would change under 1 % of the pad's pixels. Replicate
strips at the same concentration disagree by about 1.9x, so this is polish.
The per-LED offsets matter more: they move a whole side by up to 0.44 px on
every row.

## What would reopen it

- A strip whose sides bow by more than about 1.5 px, or a row-edge scatter
  well above 0.3 px.
- **Bill's idea for after yellow and brown can be fitted:** classify each
  edge pixel by whether its color is more like the row inside it (pad) or
  the row outside it (frame or backing). That handles the fuzzy edge and part
  of the tilt, and may need a tweak at the corners. Since color is to be read
  as mass, a partly colored edge pixel could count as a fraction of a pixel's
  mass, but only if the fraction can be told apart from the noise.
- Per-LED calibration: one offset per LED relating its pad edges to the
  photo_red fit. The LED geometry is fixed and repeatable, so these should
  be steady (Bill, 2026-11-15).
