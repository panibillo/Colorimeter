# Resume Prompt 2 — Colorimeter Prototype: Tricky Bits and Design Details
## For use when: debugging a specific component failure, unexpected behaviour, or planning software workarounds

---

## Reference documents
- **pinmap_v2.pdf / pinmap_v2.tex** — full pin mapping, connector tables, OS config, hardware cautions
- **Colorimeter_As_built_components.txt** — original as-built notes
- **resume_prompt_1_hardware.md** — quick-reference hardware state and test patterns

---

## Tricky Bit 1 — SPI1 and GPIO18 conflict

**The problem:** The standard `dtoverlay=spi1-1cs` kernel overlay claims **GPIO18 as SPI1 CE0** by default. GPIO18 in this design is independently used: it routes to J3-Pin4 via solder jumper JP2 (currently closed), intended for a future button or interface signal.

If the overlay is loaded without remapping CE0, the kernel SPI driver will drive GPIO18 as a chip-select, conflicting with the JP2 routing.

**The solution:** The 74HC595 does not use a hardware chip select. RCLK (latch) is driven manually by GPIO24. Load the overlay with CE0 remapped to a spare pin, or use `no_cs=True` in spidev to disable hardware CE entirely:

```
# /boot/firmware/config.txt:
dtoverlay=spi1-1cs,cs0_pin=23    # redirect CE0 to GPIO23 if it remains spare
```

```python
spi1 = spidev.SpiDev()
spi1.open(1, 0)
spi1.no_cs = True                # disables hardware CE; RCLK handled manually
```

**GPIO23 (pin16) is currently spare** and the best candidate for the remapped CE0 pin. If GPIO23 gets assigned to a button later, choose a different spare.

---

## Tricky Bit 2 — Shared PWM clock: GPIO12 (buzzer) and GPIO13 (backlight)

**The problem:** On the RP3A0 (RPi Zero 2W), GPIO12 (PWM0-A) and GPIO13 (PWM1) share a single hardware clock divider. Their frequencies are not independently arbitrary — both must be derivable from the same base clock.

**What this means in practice:**
- Independent **duty cycles** are always possible.
- Independent **frequencies** are only possible if a common base clock satisfies both.
- `pigpio.hardware_PWM()` handles the arithmetic automatically and minimises error.
- Setting both to the same frequency (e.g. 2000Hz) always works perfectly.
- Setting nearby frequencies (e.g. buzzer=2048Hz, backlight=2000Hz) works with small error on one channel — imperceptible on the backlight.

**Rule:** Always call `hardware_PWM(13, ...)` (backlight) **before** `hardware_PWM(12, ...)` (buzzer) in your code. pigpio optimises the shared clock for the last call, so the buzzer — the frequency-critical channel — gets priority.

**Buzzer frequency range for CEM-1203(42):**
- Rated resonant frequency: 2048Hz (loudest)
- Useful audible range: 800–4000Hz
- Best efficiency zone: 1500–2500Hz
- pigpio supports 1Hz steps throughout; real-time tuning works cleanly

**To silence buzzer without affecting backlight:**
```python
pi.hardware_PWM(12, 2000, 0)   # duty=0 silences; frequency value is ignored when duty=0
```

---

## Tricky Bit 3 - Resolved

Gate resistors are 500 Ohms, not 500,000 Ohms

## Tricky Bit 4 — 74HC595 decoupling ground path is circuitous

**The problem:** The 100nF decoupling capacitor at the 74HC595 VCC pin (pin16) has a long, indirect ground path back to pin8 (GND). A decoupling cap's effectiveness depends on its ground return inductance — a long path raises inductance and reduces effectiveness at SPI clock frequencies.

**Fix before testing:** Solder a short jumper wire directly from the capacitor's ground pad to pin8 of the 74HC595. This provides a low-inductance local return.

**Without the fix:** marginal — the SPI clock is not fast enough for this to cause outright failure, but noise immunity is reduced.

---
 
## Tricky Bit 5 — 1-Wire on non-default pin GPIO16

**The dtoverlay parameter is the BCM number, not the physical pin number.**

Correct: `dtoverlay=w1-gpio,gpiopin=16`  (BCM GPIO16 = physical pin 36)
Wrong:   `dtoverlay=w1-gpio,gpiopin=36`  (would configure wrong pin)
Wrong:   `dtoverlay=w1-gpio,gpiopin=7`   (BCM GPIO7 = physical pin 26 = SPI0 CE1, completely wrong pin)

This error appeared in the original as-built notes and was corrected in pinmap_v2.

**The 4.7kΩ pull-up to 3.3V on the PCB is mandatory.** The RPi internal pull-up (~50kΩ) is too weak for 1-Wire protocol timing. Do not attempt to substitute it.

**J2 cable (1-Wire probe, ~1m):** No decoupling cap at J2 on the PCB. Nearest caps are ~5 inches away via J5–J8. This is expected to work at standard 1-Wire speed (15.4kbps). If communication errors occur during testing, fit a 100nF ceramic cap between J2 pin1 (3.3V) and pin3 (GND) as the first corrective step.

---

## Tricky Bit 6 — JP1 and JP2 solder jumpers: GPIO18 routing

GPIO18 is physically connected to two places via solder jumpers on the PCB:
- **JP2 (currently closed):** GPIO18 → J3-Pin4
- **JP1 (currently open):** GPIO18 → J4-Pin6

**Warning:** Closing both JP1 and JP2 simultaneously shorts J3-Pin4 to J4-Pin6 through GPIO18. This would be a hard short between two connector pins. Do not close both at the same time.

Current intended use of GPIO18 is undecided — probably a button or On/Off switch via J3. GPIO23 (J3-Pin3) is also available for a second button signal.

---

## Tricky Bit 7 — SPI0 CE0 and TFT CS: confirmed clean, but important to understand

Physical pin 24 = GPIO8 = SPI0 CE0 = TFT CS to ILI9341.
This is hardware-managed by the SPI0 peripheral — it asserts automatically during every SPI0 transaction. You do not drive it manually.

An earlier version of the design notes confused pin 24 (GPIO8, TFT CS) with pin 18 (GPIO24, 74HC595 RCLK). These are on different physical pins and different GPIOs. They do not interact. The final as-built is clean.

---

## Tricky Bit 8 — Buzzer circuit: BJT, not direct GPIO drive

The CEM-1203(42) buzzer is rated 5V. It cannot be driven directly from a 3.3V GPIO.

Circuit: GPIO12 → 1kΩ → MMBT3904 base. Collector → buzzer(−). Buzzer(+) → 5V rail.

The BJT saturates when GPIO12 is HIGH, pulling the collector (buzzer−) to ~0.2V, applying nearly 5V across the buzzer. PWM on GPIO12 modulates the BJT switching.

**Flyback diode orientation:** cathode (line-marked end) at buzzer(+) / 5V rail; anode at buzzer(−) / BJT collector. Verify before first power-on. Reversed diode would short the 5V rail when the BJT conducts.

---

## Tricky Bit 9 — AO3400A gate drive from 74HC595 at 3.3V

The 74HC595 VCC is 3.3V. Output high is ~3.0–3.3V.
The AO3400A gate threshold is 1.0V minimum, 1.5V typical.
Gate drive of 3.0–3.3V fully enhances the AO3400A. ✓

However: the 74HC595 output current capability at 3.3V is ~±25mA. With 500kΩ gate resistors, gate charge current is <6µA — well within spec. No risk to the 74HC595 outputs.

---

## Key conflict / error history (resolved but worth knowing)

| Issue | Status | Notes |
|-------|--------|-------|
| 1-Wire dtoverlay gpiopin=7 in as-built notes | Fixed | Correct value is gpiopin=16 |
| J1 MOSI/MISO pin references swapped in draft table | Fixed | J1-Pin12=MISO, J1-Pin13=MOSI |
| J3-Pin3/Pin4 label swapped in as-built notes | Fixed | Pin3=GPIO23, Pin4=GPIO18 |
| SPI1 CE0 overlay claiming GPIO18 | Managed by software | Use no_cs=True or remap cs0_pin |
| Pin 24 / pin 18 confusion in RCLK notes | Resolved | RCLK=pin18/GPIO24; TFT CS=pin24/GPIO8 |

---

## Decoupling capacitors — as fitted

| Location | Value | Purpose |
|----------|-------|---------|
| Near J1 (1651 power) | 100nF ceramic + 10µF tantalum | Decouple 5V supply to display shield |
| 74HC595 VCC pin | 100nF ceramic | Local bypass; ground path circuitous — add jumper wire |
| Near each LED bank (J5–J8) | 100nF ceramic | Decouple 5V LED supply |
| At each 1-Wire device (J5–J8 device end) | 100nF ceramic | Local bypass at sensor |
| J2 (1-Wire probe plug) | **None fitted** | Add 100nF if comms errors occur |

---

## Notes on the 1651 shield jumpers (must check before power-on)

| Jumper | Required state | Effect |
|--------|---------------|--------|
| VddIO | Solder left pad to centre (3V setting) | Sets logic level for 74LVX245 level shifters to 3.3V. Wrong setting damages GPIOs. |
| LITE/#3 | Soldered | Connects backlight to Arduino D3 / J1-Pin9 / GPIO13 PWM |
| TSIRQ/#2 | Soldered | Connects touch interrupt to Arduino D2 / J1-Pin10 / GPIO4 |
