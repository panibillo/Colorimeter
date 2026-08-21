# Modified Gutzeit Arsenic Test — Working Protocol

Companion to `gutzeit_analysis.tex`. This file contains only actions.
Rationale, derivations, and references are in the analysis document.

**Status:** several steps depend on constants you have not yet measured
(`Q10`, `t25`, `n0`, `Q_max`). Run Part 2 (Commissioning) before relying on
Part 3 (Routine Run) for reported results.

---

## Part 0 — Safety checklist (before any run)

- [ ] Face velocity at the hood aperture measured with a hot-wire anemometer, **≥ 0.4 m/s**. A smoke test confirms direction only, not velocity.
- [ ] Working aperture kept as small as practical.
- [ ] Reaction vessel **not gas-tight**. A sealed 73 mL headspace would reach ~6.4 atm.
- [ ] Standards capped at 500 µg/L; dilute anything above and re-run.
- [ ] Reagent 1 (contains permanganate oxidiser) stored separately from Reagent 2 (flammable zinc dust).
- [ ] Zinc dust stored over desiccant, away from ignition sources.
- [ ] Mercury waste container available for used pads.
- [ ] Vessel positioned away from the duct fan.

Exposure limits for reference: ACGIH TLV-TWA 0.005 ppm (~16 µg/m³);
OSHA PEL 0.05 ppm; NIOSH IDLH 3 ppm.

---

## Part 1 — One-time setup

### 1.1 Reagent pooling and dispensing

1. Pool ~20 sachets of Reagent 1 into a single container. Tumble-mix 5 min.
2. Pool ~20 sachets of Reagent 2 likewise. Store over desiccant, sealed.
3. Dispense by mass, not by sachet:
   - Reagent 1: **2.90 ± 0.02 g** (interim; see §2.4)
   - Reagent 2: **1.30 ± 0.02 g** (interim; see §2.4)
4. Verify pooling did not segregate the catalyst: run a 50 µg/L standard in triplicate from individual sachets and from the pooled stock. Results should agree within replicate scatter.

> Measured sachet variation before pooling: Reagent 1 CV 3.8% (2.0% excluding one outlier); **Reagent 2 CV 19.9%, range 0.553–1.678 g, a 3.0× spread.** Pooling targets ~1.5%.

### 1.2 Gas path build

| Feature | Specification |
|---|---|
| Pad back face | Sealed flat against channel floor — no bypass path |
| Channel width | ~5.3 mm (strip is 5.0 mm) |
| Gap over pad face | **150 µm**, set by shim or milled step — not by spring force |
| Inlet | Transverse slot ~5 × 0.3 mm, not discrete round holes |
| Channel length | Extend 10–15 mm past the pad before exit |
| Flow restriction | **Downstream** of the pad only |
| Venting | One fixed open orifice. Remove any second floating element (inverted vial). |
| Closure | Silicone or butyl stopper. Not natural cork. |
| Manometer | Clear tube tee'd off the vessel, partly water-filled, mm scale behind |

Design rule if you change flow rate: `h ≤ A_p · D / (3 · Q_max)`
with `A_p ≈ 25 mm²`, `D ≈ 4×10⁻⁵ m²/s`. At `Q_max = 1.5 mL/s` this gives
`h ≤ 222 µm`.

### 1.3 Optical bench

Minimum four LED bands, preferably eight:

| λ (nm) | Priority | Bayer channel | Purpose |
|---|---|---|---|
| 415–430 | Essential | B | Core yellow absorption |
| 450–470 | Essential | B | Edge position |
| 495–505 | Preferred | B/G | Edge shoulder |
| 525–535 | Essential | G | Brown / trisubstituted |
| 565–590 | Preferred | G/R | Far tail, neutral absorbers |
| 620–630 | **Essential** | R | Reference — wetting correction |
| 655–670 | Preferred | R | Second reference |
| 730–780 | Optional | R | Pure scattering baseline |

- [ ] Direct-emission narrow-band LEDs only. **No phosphor-converted white.**
- [ ] Nothing below ~410 nm until you have tested a blank pad for optical-brightener fluorescence.
- [ ] Measure each LED's actual peak and FWHM. A 20 nm centroid error near the absorption edge is a large photometric error.
- [ ] Characterise Bayer crosstalk: response of each LED in all three channels on a neutral target.
- [ ] Confirm the pad is optically thick — readings over white and black backings must agree.
- [ ] Stable diffuse white reference standard mounted and measured every session.

---

## Part 2 — Commissioning experiments

Do these in order. Each answers a specific question.

### 2.1 Gas volumetry — is the engine reproducible?

Route the vent to an inverted graduated cylinder over water. Record volume
vs time.

- Triplicate at fixed zinc mass → do duplicate curves superimpose?
- Across the zinc range (0.7, 1.0, 1.3, 1.6 g) → how much does `Q(t)` move?
- At 20, 25, 30 °C → temperature sensitivity of the engine.

**Extract `Q_max`** and feed it into the gap design rule in §1.2.

### 2.2 Tandem pad breakthrough — is capture quantitative?

Place a second pad downstream of the first. Run a 100 µg/L standard.

- Downstream pad should stay **white**.
- Colour ratio downstream/upstream = `1 − η_capture`.
- Repeat at 0.7 g and 1.6 g zinc. If the downstream pad stays clean at both, the geometry has decoupled the reading from gas rate.

If the downstream pad colours: check for a bypass leak before doubting the
capture theory.

### 2.3 Time course — where is the plateau?

50 µg/L As(V), triplicate, read at 5, 7, 10, 15, 20, 30 min.

**Flatness test:**

```
(R20 − R10) / R10  ≤  0.040   →  ±5 °C costs <2%; skip the time correction
                   >  0.040   →  apply the correction, push t25 toward 15 min
```

Set **`t25`** = the shortest time satisfying the criterion, in the range
10–15 min.

### 2.4 Reagent series — find the plateau operating point

Fixed 50 µg/L standard, at the new `t25`, triplicate:

- **Zinc:** 0.4, 0.7, 1.0, 1.3, 1.7 g at fixed acid
- **Acid:** 2.9, 4.0, 5.0, 6.0 g at fixed zinc

Choose a point where the response is flat against both. Expected landing
zone: **5–6 g Reagent 1, 0.7–1.0 g Reagent 2**. Update §1.1 accordingly.

### 2.5 Temperature — measure your own Q10

50 µg/L standard, full time course at two temperatures as far apart as
ambient allows (e.g. 18 and 30 °C), triplicate.

1. **Check plateau heights match.** If they differ, time correction alone will not work — increase Reagent 1 to widen the buffer window and repeat.
2. Find times `t1`, `t2` giving equal colour.
3. `Q10 = (t1/t2)^(10 / (θ2 − θ1))`
4. Optional validation: plot response vs `ln t` for both temperatures and shift horizontally. Curves should collapse onto one master curve.

### 2.6 Dose series — linearity and spectral rank

0, 8, 16, 32, 64, 128, 256 µg/L, triplicate, all LED bands, full-frame
images.

- Plot integrated `Δ(K/S)` vs concentration → find the saturation onset `n0`.
- Normalise each spectrum to unit sum and overlay:
  - **Superimpose** → rank 1, shape carries no extra information
  - **Fan out** → composition varies; look for an isosbestic point
- Rigorous: SVD of the (samples × bands) matrix. Count singular values above noise. If rank ≥ 2, apply MCR-ALS with non-negativity.

### 2.7 Wetting calibration

Deliberately wet blank pads to several levels. Fit `β_λ` in:

```
Δ(K/S)_corrected(λ) = Δ(K/S)(λ) − β(λ) · Δ(K/S)(red reference)
```

### 2.8 Interference and speciation checks

- 0 µg/L + 2 mg/L sulfide → does the spectral shape separate from a real arsenic stain?
- As(III) vs As(V) standards at equal concentration → do they read the same?

---

## Part 3 — Routine run

### Step 1 — Prepare

1. Weigh sample: **100.0 ± 0.1 g** into the flask.
2. Record water temperature (±1 °C).
3. Pre-warm the pad holder to **5–8 °C above** the sample. Insulate the tube above the closure.
4. Load a fresh pad. Confirm the back face seats flat.

### Step 2 — Acidify and oxidise

5. Add Reagent 1 (weighed, per §1.1).
6. Stir 25–30 s at fixed rpm. **Confirm complete dissolution** — no visible crystals.
7. Stand **≥ 2 min**, or until the permanganate colour has visibly discharged, whichever is longer. Do not shorten this.

### Step 3 — Generate and collect

8. Add Reagent 2 (weighed, per §1.1). Close immediately.
9. **Record temperature at this moment** — this is `T_start`.
10. Stir continuously at fixed rpm for the whole reaction period. Do not stir-then-settle.
11. Note the manometer reading. Watch for chatter or a sudden drop (leak).

### Step 4 — Time the reaction

Compute the read time from measured temperature:

```
t(T) = t25 · exp[ (Ea/R) · (1/T − 1/298.15) ]        T in kelvin

equivalently

t(θ) = t25 · Q10^((25 − θ)/10)                       θ in °C
```

Example table for `t25 = 12 min`, `Q10 = 2` — **replace with your measured
Q10 from §2.5**:

| Water °C | 15 | 18 | 20 | 22 | 24 | 25 | 26 | 28 | 30 | 32 | 35 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Read at (min) | 24.0 | 19.5 | 17.0 | 14.8 | 12.9 | **12.0** | 11.2 | 9.7 | 8.5 | 7.4 | 6.0 |

If temperature drifts during the run, integrate instead:

```
t_eq = ∫ exp[ (Ea/R)·(1/298.15 − 1/T(τ)) ] dτ     stop when t_eq = t25
```

12. **Record temperature again at the read.** Use the mean of `T_start` and `T_read` in the equation above. A 1.5 K rise ignored costs ~10% of effective time. (Zinc dissolution is exothermic, ~+1.7 K for 0.3 g; acid dissolution is endothermic, ~−1 K.)

### Step 5 — Read

13. Remove the pad. Measure within a **fixed** short window, shielded from ambient light. Use the same interval every time — the yellow product fades.
14. Photograph the full pad in every band.

### Step 6 — Reduce

15. Convert every band to Kubelka–Munk **per pixel**:

```
f(R) = (1 − R)² / (2R) = K/S

Δ(K/S) = f(R_exposed) − f(R_blank pad)
```

16. Apply the wetting correction from §2.7.
17. Compute both readouts:
    - **Low range:** `Σ Δ(K/S)_blue × pixel area` — linear below `n0`
    - **High range:** saturated pixel area — grows as `A* · ln(n/n0)`
    - Splice at `n0` from §2.6
18. **Inspect the stain shape.** Reject the run if it is a crescent, comet tail, or off-centre blob — that is a seal leak or a tilted pad, not chemistry.

---

## Part 4 — Quality assurance

### Every session

- [ ] Reagent blank
- [ ] Standards at 0, 10, 25, 50, 100 µg/L
- [ ] Duplicates on 10% of samples
- [ ] White reference measured

### Tracked over time

Fit the Thompson–Howarth error model to accumulated duplicates:

```
s_c = s0 + k · c
```

`s0` = standard deviation at zero (detection limit = 3·s0);
`k` = constant relative error. Plot both against date as the primary
process-control metric.

Benchmarks for context:

| Method | s0 (µg/L) | k |
|---|---|---|
| Laboratory procedures (various) | 0.30 | 0.088 |
| Hand-held instrumental Gutzeit | 1.4 | 0.14 |
| Visual field kits | 5–7 | 0.2–0.3 |
| Instrumental reflectance, best case | 0.3–0.7 | — |

### Bias

Precision is not accuracy. Calibrate against prepared standards, never
against the supplied colour chart — one widely used commercial kit was found
to be repeatable while overestimating by roughly a factor of two.

---

## Quick reference — key numbers

| Quantity | Value |
|---|---|
| Tartaric acid in 100 mL at 2.88 g | 0.190 M, pH 1.87 |
| Total titratable H⁺ | 37.9 mmol |
| Productive pH window (patent) | 1.8 – 2.5 |
| **Zinc dissolvable before pH 2.5** | **0.148 g** |
| Zinc dissolvable before pH 3.0 | 0.336 g |
| Zinc excess over As at 10 µg/L | ~10⁵-fold |
| Max H₂ if acid fully consumed | 465 mL |
| Pad HgBr₂ | 2.774 µmol → 0.925 µmol As capacity |
| Pad areal capacity | 28.5 nmol As/mm² |
| Whole-pad saturation | ~693 µg/L |
| **Local saturation onset, 150 µm gap** | **~72 µg/L** at nominal flow |
| Local saturation onset, 29 µm gap | ~14 µg/L |
| Capture efficiency, 150 µm gap | ≥98.8% across 0.3–1.5 mL/s |
| Ea for Q10 = 2 at 25 °C | 53 kJ/mol |

---

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Duplicates differ ~2× | Flow-dependent capture; leak path | Tandem pad test (§2.2); check manometer for chatter |
| Stain asymmetric | Seal leak or pad tilt | Reject run; inspect flat face for burr or scratch |
| Stain pale, diffuse, soft-edged | Pad flooded with condensate | Increase holder pre-warm; insulate; shorten gas path |
| Reading high at low As, flat at high As | Local indicator saturation | Dilute; use the high-range readout |
| Grey or dark stain | Sulfide interference | Restore/lengthen the permanganate rest; verify with §2.8 |
| Blank reads above zero | Arsenic in the zinc, or brightener fluorescence | Run reagent blank; test LED bands below 450 nm on blank pad |
| Damp blank reads as arsenic | No wetting correction | Apply §2.7 with the red reference band |
| Results drift over a session | Read-interval drift; pad fading | Fix removal-to-measurement interval |
| Reaction stalls early | Acid buffer exhausted (high zinc sachet) | Increase Reagent 1; reduce Reagent 2 |
