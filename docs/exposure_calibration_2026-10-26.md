# Exposure calibration: tested brackets (2026-10-26)

## Why

Each LED used to be calibrated to one exposure (e*, the gray chips at half
scale) and bracketed by fixed ×1.5 steps that were never tested. On a real pad
many brackets were over- or under-exposed and got discarded. Now every stored
bracket is measured, and kept only if it passes.

## The test

A bracket passes when, at that exposure, every test-pad patch (white, yellow,
brown) and every gray chip has its **low percentile at or above Low DN** and
its **high percentile at or below High DN**. Signal means the raw value minus
the sensor's black level of 64, so the scale is 0 to about 960 (1023 − 64).
The defaults are 100 and 850 DN at the 10th and 90th percentiles. They are
placeholders. They have their own settings (`calibration.window_*`), separate
from the layer-keep and analysis rules.

Only the Bayer layers the final analysis uses for an LED must pass; the others
may be too dark or too bright. `calibration.required_cells` names them
(seeded `violet.B, royal_blue.B, cyan.B, green.Gb, green.Gr, photo_red.R`). An
LED the list does not name uses the layer closest to its color (red → R,
violet → B, green → Gb, white → Gr).

An LED may have its own window (`calibration.led_windows`,
`led=low_dn:high_dn:low_pctl:high_pctl;...`), which overrides the global one.

## Where the brackets go

The sensor is linear, so one measurement says how far exposure can move:

- `e_min`: the exposure at which the darkest patch just reaches Low DN.
- `e_max`: the exposure at which the brightest patch just reaches High DN.

Every bracket must lie between them, so all of them pass white and brown at
once. Between them, exposure is placed **log-spaced** at fixed positions
(0 is `e_min`, 1 is `e_max`, both pulled in 3%):

| Group (images per LED) | Positions |
|---|---|
| 1 | 1/2 |
| 2 | 0, 1 |
| 3 | 0, 1/2, 1 |
| 4 | 0, 1/3, 2/3, 1 |
| 5 | 0, 1/4, 1/2, 3/4, 1 |

The groups share positions, so seven exposures (0, 1/4, 1/3, 1/2, 2/3, 3/4, 1)
serve all five. Each is measured and nudged until it passes, then snapped to a
shared eighth-of-a-stop grid so that dark frames taken at identical exposures
can be stored once. A group is dropped if the range is too narrow to keep
neighbors at least 1.2× apart (5 brackets need a usable range of about 2.1×).
The log names the limiting patch.

## What is stored (`exposure` table)

One row per LED, group and exposure, with `active` marking the set in force
(older sets stay as history). New columns: `remark` (`low DN (pad_brown.Gb)`,
`high DN (...)`, or `log rung 1/4`), `low_dn` / `high_dn` (the measured signal,
only on the rung that fixed that limit), `layers` (the layers that had to pass,
e.g. `Gb Gr`) and `preimage`.

`preimage = 1` marks the exposures the pre-image takes: today group 1's single
exposure, the middle of the usable range. Changing the flag changes the
pre-image with no new calibration. If no row is flagged, the pre-image uses the
middle of group 1. The preview also starts from the middle exposure.

## Code

| Module | Role |
|---|---|
| `exposure_fit.py` | The search. Pure arithmetic; takes a `measure(exposure)` function. |
| `exposure_measure.py` | Reads the pad and chips out of a frame, per Bayer layer. |
| `required_cells.py` | Required layers, per-LED windows. |
| `exposure_fit_runner.py` | Lights the LED, grabs frames, runs the fit; one LED or all. |
| `exposure_show.py`, `show_plot.py` | The Show screen's capture and plot. |
| `exposure_repo.py` | Reads and writes the table. |
| `roi.py` | `load_test_pad()`: regions with `active = 2` and role `pad_white/yellow/brown`. |

A startup check warns if the window is wider than the layer-keep limits, which
would let a bracket pass calibration and then be discarded at capture.

## Open items

- The physical pad does not exist yet; its three regions are not seeded.
- The window values are placeholders to tune on real pads.
- Linear spacing instead of log is deferred.
- The old gray-card calibration path (`e3_final_us`) is still in
  `capture_runner.py` but nothing in the app starts it.
- `calibration.max_spacing_ratio` is no longer used.
