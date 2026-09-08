# Adafruit 1651 Interface and Control Reference

May 17, 2026  14:45

## Purpose

This document describes how the custom Raspberry Pi Zero 2W on mainboard_cal8 interfaces with the Adafruit 1651 2.8-inch TFT Shield v2.

The document is organized:

- By hardware component
- By communications interface
- By signal path
- By software control method
- By verification and troubleshooting method

It is intended for:

- Hardware bring-up
- PCB verification
- Continuity testing
- Linux configuration
- Python driver development
- Debugging and troubleshooting
- Future maintenance

---

# System Overview

## Main Components on the Adafruit 1651

| Component | Function | Interface |
|---|---|---|
| ILI9341 TFT controller | LCD graphics display | SPI0 |
| TSC2007 | Resistive touch controller | I2C |
| microSD card socket | External storage | SPI0 |
| 74LVX245 level shifters | Logic-level translation | Hardware support |
| *TFT backlight | *LED backlight brightness control | *GPIO PWM |


PWM for backlight control is deprecated because of electrical noise interference.

---

# Important Shield Configuration

## Required Solder Jumpers

The following jumpers on the Adafruit 1651 must be soldered closed:

| Jumper | Purpose | Solder |
|---|---|---|
| TSIRQ / #2 | Routes touch interrupt to Arduino D2 | closed |
| ICSP SI / 11 | SPI MOSI routing to Arduino D11 | closed |
| ICSP SO / 12 | SPI MISO routing to Arduino D12 | closed |
| ICSP SCK / 13 | SPI clock routing to Arduino D13 | closed |
| *LITE / #3 | *Routes backlight control to Arduino D3 | open |


Backlight is left in default ON state: no jumper, no wiring.

---

# Critical Voltage Configuration

## VddIO Jumper

__The VddIO jumper on the Adafruit 1651 MUST be configured for 3V operation.__

Required configuration:

- Center pad soldered to 3V pad
- NOT connected to 5V

Reason:

The Raspberry Pi Zero 2W uses 3.3V GPIO logic.
The Adafruit 1651 contains 74LVX245 level shifters.
Improper VddIO configuration may expose Raspberry Pi GPIO pins to 5V logic levels.

---

# Communications Architecture

## SPI0 Bus

Shared by:

- ILI9341 TFT controller
- microSD card

Signals:

| Signal | Raspberry Pi GPIO |
|---|---|
| MOSI | GPIO10 |
| MISO | GPIO9 |
| SCLK | GPIO11 |
| TFT CS | GPIO8 |
| SD CS | GPIO5 |
| TFT DC | GPIO6 |

---

## I2C Bus

Used by:

- TSC2007 touch controller

Signals:

| Signal | Raspberry Pi GPIO |
|---|---|
| SDA | GPIO2 |
| SCL | GPIO3 |
| TSIRQ | GPIO4 |

TSC2007 I2C address:

```text
0x48
```

---

## PWM and GPIO

| Function | GPIO |
|---|---|
| Touch IRQ | GPIO4 |
| TFT DC | GPIO6 |
| TFT CS | GPIO8 |
| SD Card CS | GPIO5 |

---

# Component Reference

---

# 1. TFT Display — ILI9341

## Function

The ILI9341 controls the 320x240 TFT graphics display.

The display communicates over the Raspberry Pi SPI0 peripheral.

---

## SPI0 Signal Paths

### MOSI — Display Data Output

| Item | Value |
|---|---|
| Raspberry Pi GPIO | GPIO10 |
| Raspberry Pi Physical Pin | Pin 19 |
| Raspberry Pi Peripheral | SPI0 MOSI |
| PCB Header | J1 Pin 13 |
| Adafruit 1651 Signal | ICSP MOSI / D11 |
| Destination Device | ILI9341 |
| PCB Protection | 33Ω series resistor |

### Signal Path

```text
RPi GPIO10/SPI0 MOSI
    -> RPi pin 19
    -> 33Ω resistor
    -> PCB trace
    -> J1 pin 13
    -> Adafruit 1651 ICSP MOSI / D11
    -> ILI9341 SPI input
```

### Software Verification

Linux:

```bash
ls /dev/spidev0.0
```

Python:

```python
import spidev
spi = spidev.SpiDev()
spi.open(0,0)
```

### Hardware Verification

Continuity:

- Verify continuity from RPi pin 19 to J1 pin 13
- Verify continuity from J1 pin 13 to ICSP MOSI jumper

Oscilloscope or logic analyzer:

- Observe SPI traffic during display updates
- Signal should idle low and show clocked data bursts

---

## MISO — Display / SD Return Data

| Item | Value |
|---|---|
| Raspberry Pi GPIO | GPIO9 |
| Raspberry Pi Physical Pin | Pin 21 |
| Raspberry Pi Peripheral | SPI0 MISO |
| PCB Header | J1 Pin 12 |
| Adafruit 1651 Signal | ICSP MISO / D12 |
| Destination Device | SD card and optional ILI9341 readback |
| PCB Protection | 33Ω series resistor |

### Signal Path

```text
ILI9341 or SD card SPI output
    -> Adafruit ICSP MISO / D12
    -> J1 pin 12
    -> PCB trace
    -> 33Ω resistor
    -> RPi pin 21
    -> GPIO9 / SPI0 MISO
```

### Verification

Continuity:

- Verify continuity from RPi pin 21 to J1 pin 12
- Verify continuity from J1 pin 12 to ICSP SO jumper

Logic analyzer:

- Data activity should appear during SD card reads
- Some display libraries may show limited MISO activity

---

## SCLK — SPI Clock

| Item | Value |
|---|---|
| Raspberry Pi GPIO | GPIO11 |
| Raspberry Pi Physical Pin | Pin 23 |
| Raspberry Pi Peripheral | SPI0 SCLK |
| PCB Header | J1 Pin 11 |
| Adafruit 1651 Signal | ICSP SCK / D13 |
| PCB Protection | 33Ω series resistor |

### Signal Path

```text
RPi GPIO11/SPI0 SCLK
    -> RPi pin 23
    -> 33Ω resistor
    -> PCB trace
    -> J1 pin 11
    -> Adafruit ICSP SCK / D13
    -> ILI9341 and SD card clock input
```

### Verification

Continuity:

- Verify RPi pin 23 to J1 pin 11
- Verify J1 pin 11 to ICSP SCK jumper

Oscilloscope:

- Observe stable SPI clock bursts during display updates

---

## TFT Chip Select — TFT CS

| Item | Value |
|---|---|
| Raspberry Pi GPIO | GPIO8 |
| Raspberry Pi Physical Pin | Pin 24 |
| Raspberry Pi Peripheral | SPI0 CE0 |
| PCB Header | J1 Pin 6 |
| Adafruit Signal | TFT CS / D10 |
| Function | Active-low TFT select |
| PCB Protection | 33Ω series resistor |

### Signal Path

```text
RPi GPIO8 / CE0
    -> RPi pin 24
    -> 33Ω resistor
    -> PCB trace
    -> J1 pin 6
    -> Adafruit TFT CS / D10
    -> ILI9341 chip select
```

### Verification

Software:

```python
spi.open(0,0)
```

Oscilloscope:

- CS should pulse low during display transfers

---

## TFT Data/Command — TFT DC

| Item | Value |
|---|---|
| Raspberry Pi GPIO | GPIO6 |
| Raspberry Pi Physical Pin | Pin 31 |
| PCB Header | J1 Pin 7 |
| Adafruit Signal | TFT DC / D9 |
| Function | Data/Command mode select |
| PCB Protection | 33Ω series resistor |

### Function

The DC line determines whether SPI bytes are interpreted as:

- Commands
- Display data

---

### Signal Path

```text
RPi GPIO6
    -> RPi pin 31
    -> 33Ω resistor
    -> PCB trace
    -> J1 pin 7
    -> TFT DC / D9
    -> ILI9341 D/C input
```

### Verification

Logic analyzer:

- DC changes state during initialization commands
- DC remains high during framebuffer transfers

---

## TFT Reset

No dedicated Raspberry Pi GPIO is assigned to TFT reset.

The Adafruit shield handles TFT reset internally.

---

# 2. TFT Backlight Control (deprecated)

## Function

Controls brightness of the TFT LED backlight.

Implemented using Raspberry Pi hardware PWM.

---

## Backlight Signal Path

| Item | Value |
|---|---|
| Raspberry Pi GPIO | GPIO13 |
| Raspberry Pi Physical Pin | Pin 33 |
| PWM Channel | Hardware PWM1 |
| PCB Header | J1 Pin 9 |
| Adafruit Signal | LITE / D3 |
| PCB Protection | 33Ω series resistor |

Note: GPIO13 is now free for other uses.
---

## Required Shield Configuration

The LITE/#3 jumper on the Adafruit 1651 must be soldered closed.

Continuity should exist:

```text
LITE jumper <-> Arduino D3
```

---

## Signal Path

```text
RPi GPIO13 / PWM1
    -> RPi pin 33
    -> 33Ω resistor
    -> PCB trace
    -> J1 pin 9
    -> Adafruit LITE / D3
    -> *backlight driver circuit  (deprecated)
```

---

## Software Setup

Python example ALL DEPRECATED!

```python
import pigpio
pi = pigpio.pi()  
pi.hardware_PWM(13, 2000, 800000)
```

Parameters:

- GPIO13
- 2kHz PWM frequency
- 80% brightness

---

## Verification

### Linux

Verify pigpio daemon:

```bash
sudo systemctl status pigpiod
```

### Oscilloscope

Observe PWM waveform:

- Frequency approximately 2kHz
- Duty cycle changes with brightness setting

### Functional Test

- Screen brightness should vary smoothly
- No flicker should be visible

---

# 3. Resistive Touch Controller — TSC2007

## Function

The TSC2007 converts resistive touch coordinates into digital I2C data.

The touch controller communicates over I2C1.

Important:

This shield uses:

- TSC2007 over I2C

NOT:

- XPT2046 over SPI

---

# I2C Signal Paths

## SDA — I2C Data

| Item | Value |
|---|---|
| Raspberry Pi GPIO | GPIO2 |
| Raspberry Pi Physical Pin | Pin 3 |
| Raspberry Pi Peripheral | I2C1 SDA |
| PCB Header | J1 Pin 2 |
| Adafruit Signal | SDA |
| Destination Device | TSC2007 SDA |
| PCB Protection | 33Ω series resistor |
| Pull-up | 4.7kΩ pull-up on shield |

---

## Important Pull-Up Note

The Adafruit 1651 already contains I2C pull-up resistors.

Do NOT add additional pull-ups on the PCB.

---

## Signal Path

```text
RPi GPIO2 / SDA1
    -> RPi pin 3
    -> 33Ω resistor
    -> PCB trace
    -> J1 pin 2
    -> Adafruit SDA
    -> TSC2007 SDA
```

---

## Verification

Linux:

```bash
i2cdetect -y 1
```

Expected device:

```text
48
```

Oscilloscope:

- Idle high at 3.3V
- Clocked bidirectional traffic during touch reads

---

## SCL — I2C Clock

| Item | Value |
|---|---|
| Raspberry Pi GPIO | GPIO3 |
| Raspberry Pi Physical Pin | Pin 5 |
| Raspberry Pi Peripheral | I2C1 SCL |
| PCB Header | J1 Pin 4 |
| Adafruit Signal | SCL |
| Destination Device | TSC2007 SCL |
| PCB Protection | 33Ω series resistor |

---

## Signal Path

```text
RPi GPIO3 / SCL1
    -> RPi pin 5
    -> 33Ω resistor
    -> PCB trace
    -> J1 pin 4
    -> Adafruit SCL
    -> TSC2007 SCL
```

---

## Verification

Linux:

```bash
i2cdetect -y 1
```

Oscilloscope:

- Observe I2C clock bursts during touch polling

---

# Touch Interrupt — TSIRQ

## Function

Signals that the touch screen is currently being pressed.

The TSC2007 IRQ output is:

- active-low
- open-drain

The Raspberry Pi internal pull-up resistor must be enabled.

---

## Signal Path

| Item | Value |
|---|---|
| Raspberry Pi GPIO | GPIO4 |
| Raspberry Pi Physical Pin | Pin 7 |
| PCB Header | J1 Pin 10 |
| Adafruit Signal | TSIRQ / D2 |
| Function | Active-low touch interrupt |
| PCB Protection | 33Ω series resistor |

---

## Required Shield Configuration

The TSIRQ/#2 jumper on the shield must be soldered closed.

Continuity should exist:

```text
TSIRQ jumper <-> Arduino D2
```

---

## Signal Path

```text
TSC2007 IRQ output
    -> Adafruit TSIRQ / D2
    -> J1 pin 10
    -> PCB trace
    -> 33Ω resistor
    -> RPi pin 7
    -> GPIO4
```

---

## Software Setup

Python:

```python
GPIO.setup(4, GPIO.IN, pull_up_down=GPIO.PUD_UP)
```

---

## Verification

Multimeter:

- Idle state approximately 3.3V
- Touch event pulls line low

Oscilloscope:

- Negative-going pulse during touch

---

# 4. microSD Card Interface

## Function

The microSD card socket shares the SPI0 bus with the TFT display.

The SD card has its own chip select line.

---

# Shared SPI0 Signals

The SD card shares:

- MOSI
- MISO
- SCLK

with the TFT controller.

These signal paths are described in the TFT section.

---

# SD Card Chip Select — SD CS

| Item | Value |
|---|---|
| Raspberry Pi GPIO | GPIO5 |
| Raspberry Pi Physical Pin | Pin 29 |
| PCB Header | J1 Pin 8 |
| Adafruit Signal | Card CS / D4 |
| Function | Active-low SD card select |
| PCB Protection | 33Ω series resistor |

---

## Signal Path

```text
RPi GPIO5
    -> RPi pin 29
    -> 33Ω resistor
    -> PCB trace
    -> J1 pin 8
    -> Adafruit Card CS / D4
    -> microSD card chip select
```

---

## Verification

Continuity:

- Verify RPi pin 29 to J1 pin 8
- Verify continuity to SD CS pad on shield

Logic analyzer:

- CS should pulse low during SD card accesses

---

# Linux Configuration

## Required /boot/firmware/config.txt Entries

```text
dtparam=spi=on
dtparam=i2c_arm=on
```

---

# Python Software Architecture

## SPI Display Initialization

```python
import spidev
spi = spidev.SpiDev()
spi.open(0,0)
```

---

## I2C Touch Detection

```bash
i2cdetect -y 1
```

Expected:

```text
48
```

---

## Backlight PWM  (deprecated)

```python
import pigpio
pi = pigpio.pi()
pi.hardware_PWM(13, 2000, 800000)
```

---

## Touch IRQ

```python
GPIO.setup(4, GPIO.IN, pull_up_down=GPIO.PUD_UP)
```

---

# Recommended Bring-Up Procedure

## 1. Power Verification

Before connecting the shield:

Verify:

- 5V rail
- 3.3V rail
- Ground continuity

---

## 2. Verify VddIO Jumper

Ensure shield configured for:

```text
3V logic
```

before connecting to Raspberry Pi.

---

## 3. Verify Continuity

Check all J1 signal paths:

- SPI lines
- I2C lines
- TSIRQ
- LITE
- SD CS
- Ground
- VIN

---

## 4. Verify I2C Touch Controller

```bash
i2cdetect -y 1
```

Expected:

```text
0x48
```

---

## 5. Verify TFT Backlight (deprecated)

Run PWM test.

Confirm:

- brightness changes
- no visible flicker

---

## 6. Verify TFT Display

Display solid-color test patterns.

Verify:

- stable image
- no flicker
- no corruption

---

## 7. Verify Touch Interrupt

Touch screen.

Observe:

- GPIO4 transitions low
- interrupt detected in software

---

## 8. Verify SD Card

Mount and read SD card.

Confirm:

- SPI bus stable
- card detection reliable

---

# Known Design Notes

## SPI1 Is Unrelated to the Adafruit 1651

SPI1 is used elsewhere on the custom PCB for the 74HC595 LED controller.

The Adafruit 1651 uses only:

- SPI0
- I2C1
- GPIO
- PWM

---

# Final Hardware Summary

| Function | RPi GPIO | Physical Pin | J1 Pin | 1651 Signal |
|---|---|---|---|---|
| SPI0 MOSI | GPIO10 | 19 | J1-13 | ICSP MOSI / D11 |
| SPI0 MISO | GPIO9 | 21 | J1-12 | ICSP MISO / D12 |
| SPI0 SCLK | GPIO11 | 23 | J1-11 | ICSP SCK / D13 |
| TFT CS | GPIO8 | 24 | J1-6 | TFT CS / D10 |
| TFT DC | GPIO6 | 31 | J1-7 | TFT DC / D9 |
| SD CS | GPIO5 | 29 | J1-8 | Card CS / D4 |
| I2C SDA | GPIO2 | 3 | J1-2 | SDA |
| I2C SCL | GPIO3 | 5 | J1-4 | SCL |
| TSIRQ | GPIO4 | 7 | J1-10 | TSIRQ / D2 |
| *Backlight PWM | GPIO13 | 33 | J1-9 | LITE / D3 |
| VIN / 5V | 5V | 2 | J1-1 | VIN |
| Ground | GND | multiple | J1-3,5 | GND |

*  (deprecated)
