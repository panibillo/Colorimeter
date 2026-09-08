# Prompt — duotone feature extraction

*Working prompt. Edit freely; this is the version meant to be revised.*
*Companion: `duotone_features_explained.md` — same content, nothing abbreviated.*

---

## Task

In `desktop/padanalysis/analysis_1.py`, add per-sample feature extraction from
the two-dye separation, so the yellow/brown pixel clouds can later be regressed
against `samples.manual_read` arsenic concentration. Six features, pooled basis.
Do not build the regression yet.

## Findings that set the approach

Measured on C1017–C1025 (2–160 ppb) before writing this.

**1. `cell_PCA` currently fits endmembers per pad, and that dominates the shapes.**

`run_sampno` calls `cell_PCA(SAMP, figs=True, savefigs=True)` with no
`endmembers=`, so each pad gets its own yellow/brown basis. PC2 holds only
2–4 % of the energy, so on a pad with little brown the second endmember is
fitted from noise. Angle between the two fitted endmember spectra, per pad:

```
 C1022    2ppb  PC2 0.018   141.0°        C1021   20ppb  PC2 0.039    44.8°
 C1020    5ppb  PC2 0.032   146.3°        C1023   40ppb  PC2 0.041    46.1°
 C1017   10ppb  PC2 0.024    37.0°        C1024   80ppb  PC2 0.045    47.7°
 C1018   10ppb  PC2 0.027    37.4°        C1025  160ppb  PC2 0.022    22.4°
 C1019   10ppb  PC2 0.031    38.3°
```

141° is two "dye spectra" pointing nearly opposite. The straight slope-1.5 ray
in the 2/5 ppb density plots, and the vertical wall at brown = 0 at 160 ppb, are
pictures of that wedge — not of the chemistry. Features computed off the stored
maps behave accordingly: plateau total runs *backwards* with dose (1.56, 1.53,
1.03, 1.03, 0.97) and integrated dye is non-monotone.

HOWTO.md already records the underlying fact ("One strip alone is rank-1").
Pooling over all nine gives one sensible basis: 17 shared cells, PC1 0.933 /
PC2 0.050, wedge **51.3°**, propagated noise sigma_tot = 0.0064 A.

**2. With a shared basis, all nine pads lie on one curve.**

Let `T = y + b` (total dye) and `f = b/T` (brown fraction). Median `T` per `f`
bin, dyed pixels (`T > 0.25`):

```
  f bin       2      5     10     10     10     20     40     80    160  ppb
0.1-0.2    0.42   0.51   0.37   0.40   0.40   0.34   0.34   0.38   0.32
0.2-0.3    0.73   0.72   0.55   0.64   0.63   0.50   0.52   0.57   0.45
0.3-0.4    0.86      .   0.77   0.84   0.85   0.72   0.72   0.70   0.57
0.4-0.5       .      .   0.93   0.97   0.98   0.91   0.90   0.88   0.57
0.5-0.6       .      .   1.03   1.06   1.05   1.03   1.03   0.94   0.83
0.6-0.7       .      .   1.11   1.12   1.07   1.08   1.05   1.02   0.96
0.7-0.8       .      .   1.12      .      .   1.11   1.08   1.07   1.04
0.8-0.9       .      .      .      .      .   1.04   0.98   1.05   1.01
0.9-1.0       .      .      .      .      .   0.97   0.83   0.95   0.88
```

Columns agree to ~±0.05 A. **`T(f)` is one universal curve** — rising from ~0.3
to a crest of ~1.11 near `f ≈ 0.75`, then easing off. That crest is the arch
seen at 10–80 ppb; the "straight ray" at 2/5 ppb and the "anti-diagonal" at
160 ppb are the low-`f` and high-`f` ends of the same curve.

The curve is not per-sample information. What varies with dose is **where the
pixels sit along it**:

```
share of dyed pixels per f bin
  f bin       2      5     10     10     10     20     40     80    160  ppb
0.0-0.2    0.32   0.61   0.18   0.26   0.25   0.10   0.09   0.11   0.02
0.6-0.7    0.01   0.00   0.36   0.27   0.31   0.09   0.08   0.09   0.07
0.9-1.0    0.00   0.00   0.00   0.00   0.00   0.18   0.23   0.20   0.48
```

So a 2-D shape problem collapses to a 1-D location problem.

**3. Both findings were predicted by notes already in the project.**

- `docs/two_dye_pad_analysis_2026-08-19.md` §6.4: brown fraction rises
  monotonically with dose; yellow **rises then falls**, peaking between 64 and
  128 µg/L — "the behaviour of an intermediate species in a sequential
  reaction". That sequential reaction *is* the `T(f)` curve. Each pixel sits at
  its own point along it, set by its own local deposited dose.
- `tex/gutzeit_analysis.tex` §"Deposition profile and local saturation":
  saturation onset at ~14 ppb, above which "the stain does not darken further
  at its centre; it widens", and the widening is **logarithmic**,
  `A_sat(n) = A* ln(n/n0)`.

Measurement matches both: plateau total saturates by 10 ppb (1.07 → 1.01 from
10 to 160 ppb), and dyed area correlates +0.944 with **log** concentration.
The area feature is not a curve-fitting accident — it is the predicted regime.

**Blind spot.** `gutzeit_analysis.tex` §"Step 3" also notes that at extreme
loading the pad forms mercury arsenide and metallic deposits that are
*spectrally neutral*. Contrast absorbance removes the shading direction, so a
spectrally flat absorber is invisible to this method. If C1025 (160 ppb) is
forming any, every feature here under-reports it. Treat the top of the range
with caution.

## The six features

Computed with the pooled basis, over dyed pixels (`T > 0.25`):

| feature | meaning | corr. w/ log ppb |
|---|---|---|
| `f50` | median brown fraction | +0.919 |
| `f90` | 90th pct brown fraction — plateau hue | +0.884 |
| `P(f>0.9)` | fraction of dyed px fully converted | +0.892 |
| `n_dyed` | dyed pad area, px | **+0.944** |
| `Sum_f` | integrated brown — extensive dose | +0.931 |
| `Sum_T` | integrated total dye | +0.940 |

```
  samp  ppb |    f50   f90 P(f>.9) |  n_dyed  Sum_f  Sum_T
 C1022    2 |  0.247 0.309   0.000 |     398   90.1  244.3
 C1020    5 |  0.171 0.251   0.000 |     465   73.9  247.0
 C1017   10 |  0.502 0.685   0.000 |     658  296.0  548.3
 C1018   10 |  0.411 0.665   0.000 |     795  322.4  643.0
 C1019   10 |  0.412 0.677   0.000 |     697  288.9  555.9
 C1021   20 |  0.631 0.920   0.177 |     964  576.9  820.9
 C1023   40 |  0.773 1.000   0.327 |    1204  812.8  990.2
 C1024   80 |  0.641 0.966   0.235 |    1000  603.7  835.1
 C1025  160 |  0.904 0.973   0.515 |    1438 1127.8 1213.7
```

Three things to read off:

- **The 10 ppb triplicate is tight** — `n_dyed` 658/795/697, `f50`
  0.502/0.411/0.412. Within ~6 % on the extensive features.
- **C1024 (80 ppb) reads low on every feature**, between the 20 and 40 ppb
  strips. Sample-level outlier; no feature choice fixes it.
- **No single feature spans 2–160 ppb.** Intensive features (`f50`, `f90`)
  saturate above ~40 ppb; extensive ones (`n_dyed`, `Sum_f`) keep climbing.
  Pair one of each. `f90 + P(f>0.9)` fits log ppb at R² = 0.901 — in-sample,
  9 points / 3 params, so read that as "real signal", not a calibration.

## Rejected alternatives

- **Per-sample curve fitting** (quadratic `y = a·b + c·b²`, ridge extraction
  `r(θ)`, cloud moments). Most literal reading of "characterise the curve", but
  Finding 2 shows the curve is shared — it spends parameters re-estimating a
  constant.
- **Two-cluster / Gaussian mixture on the bimodal histograms.** The low mode is
  unstained pad inside the fitted rectangle, the high mode is the stain plateau,
  and the valley between them is the diffuse rim — a bridge, not a gap. Useful
  half survives as `n_dyed`.
- **Plateau depth as primary feature.** Saturates by 10 ppb, exactly as
  `gutzeit_analysis.tex` predicts.
- **3-sigma noise floor as the dyed/undyed cut.** `3 × 0.0064 = 0.019 A` calls
  96–98 % of every pad "dyed"; unstained pad sits near `T ≈ 0.09`, far above
  sensor noise. `T > 0.25` separates the modes.

## Implementation

Small, incremental. Nothing here changes capture data.

1. **`pooled_endmembers(names=None)`** in `analysis_1.py`, cached beside
   `models()`. Body is `duotone.build(names)` then `duotone.fit(cubes)` — both
   exist, used this way in `report14.py:137` and `look3.py:74`. `build` reads
   nine archives; it is the slow step. Add
   `DOSED = ["C1017"..."C1025"]`, with a note that concentration comes from
   `samples.manual_read` in the sqlite database — `samples.py`'s hand-written
   table covers C1002–C1014 only and does not know these captures.

2. **`duotone_features(samp_no, endmembers, floor=0.25)`** returning the six
   features plus QC (`n_px`, `residual_rms`, `wedge_deg`). Reuse
   `duotone.unmix`, `duotone.residual_rms`, `duotone.amount_sigma`. Follow the
   `cell_table` (analysis_1.py:1413) summariser pattern for the printed form.

3. **Store the basis with the maps.** `writeh5_sampcell` records `quantity` and
   `erode` but not the endmember vectors, so a stored map cannot be told from
   another pad's basis or reprojected. Write `eps_y`/`eps_b` under `duotone/`
   and add a `basis` attribute naming the pool. This is the one change that
   stops Finding 1 recurring.

4. **`run_sampno` step 9**, guarded like the rest: fit the pooled basis once,
   re-run `cell_PCA(SAMP, endmembers=(eps_y, eps_b))` for all nine, rewrite the
   interim store, print the feature table.
   ⚠ This **overwrites** `analysis/samples/*.h5` for C1020, C1022–C1025 and the
   `fig/analysis1_*_pca.png` figures, replacing per-pad-basis results with
   pooled-basis ones. Intended, but destructive to existing artifacts.

5. **`fig/analysis1_duotone_curve.png`**: the pooled `T(f)` curve with all nine
   pads' binned medians overplotted, beside the nine `f` histograms on shared
   axes. This is the approval figure for the shared-curve claim.

## Verification

```bash
cd /home/bill/Projects/Colorimeter/desktop/padanalysis
timeout -k 10 600 ../../.venv/bin/python3 -u analysis_1.py
```

1. `pooled_endmembers()` prints 17 shared cells, PC1 ≈ 0.933, PC2 ≈ 0.050,
   wedge ≈ 51.3°. A wedge far from that means `build` took a different set.
2. Per-pad two-dye residual stays near the per-pad-fit value (0.009–0.020 A).
   A large jump means the pool is mixing incompatible captures.
3. Feature table reproduces the numbers above.
4. `read_duotone("C1020")` still returns two (130, 183) masked arrays with 2173
   valid pixels; `sampcell_attrs("C1020", "duotone/yellow")` now carries `basis`.
5. Eyeball `fig/analysis1_duotone_curve.png`: the nine traces should lie on top
   of each other; the `f` histograms should march left-to-right with dose.
6. `pgrep -af 'python3 .*analysis_1'` comes back empty.

## References

- `desktop/padanalysis/HOWTO.md` §8 — driving `analysis_1.py`; the rank-1 note
- `docs/two_dye_pad_analysis_2026-08-19.md` §5.6–5.8, §6.1, §6.4, §6.5
- `tex/gutzeit_analysis.tex` — "Deposition profile and local saturation";
  "Reflectance photometry"
- `docs/gutzeit_protocol.md`
