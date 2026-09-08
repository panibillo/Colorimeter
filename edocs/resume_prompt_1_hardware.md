# Resume Prompt 1 — Colorimeter Prototype: As-Built Hardware State
## For use when: beginning step-wise component testing, writing test code, or continuing hardware bring-up

---

## Project
A colorimeter instrument built around a Raspberry Pi Zero 2W on a custom PCB.
The full pin mapping reference document is **pinmap_v2.pdf / pinmap_v2.tex**.
The as-built component notes are in **Colorimeter_As_built_components.txt**.

---

## RPi Zero 2W — Complete As-Built Pin Assignment

### Odd-side pins — Adafruit 1651 display/touch shield

| Phys | BCM | Dir | Assignment | Connector |
|------|-----|-----|------------|-----------|
| 1 | — | PWR | 3.3V (not used for 1651) | — |
| 3 | GPIO2 | IO | I2C SDA → TSC2007 touch controller | J1-Pin2 |
| 5 | GPIO3 | IO | I2C SCL → TSC2007 | J1-Pin4 |
| 7 | GPIO4 | I | TSIRQ — touch interrupt, active-low | J1-Pin10 |
| 9 | — | GND | Ground (1651 return) | J1-Pin3,5,15 |
| 11 | GPIO17 | — | Spare, routed to J1 | J1-Pin14 |
| 13 | GPIO27 | — | No connect | — |
| 15 | GPIO22 | — | No connect | — |
| 17 | — | PWR | 3.3V (not used for 1651) | — |
| 19 | GPIO10 | O | SPI0 MOSI → ILI9341 display | J1-Pin13 |
| 21 | GPIO9 | I | SPI0 MISO ← ILI9341 | J1-Pin12 |
| 23 | GPIO11 | O | SPI0 SCLK → ILI9341 | J1-Pin11 |
| 25 | — | GND | Ground | — |
| 27 | GPIO0 | — | **PIN REMOVED — DO NOT USE** (EEPROM) | — |
| 29 | GPIO5 | — | Spare / SD Card CS (unassigned) | J1-Pin8 |
| 31 | GPIO6 | O | TFT DC (Data/Command select) | J1-Pin7 |
| 33 | GPIO13 | PWM | **HW PWM1** — backlight → 1651 LITE | J1-Pin9 |
| 35 | GPIO19 | I | SPI1 MISO breakout (no device connected) | J4-Pin4 |
| 37 | GPIO26 | — | Spare, routed to J1 | J1-Pin16 |
| 39 | — | GND | Ground | — |

### Even-side pins — general I/O

| Phys | BCM | Dir | Assignment | Connector |
|------|-----|-----|------------|-----------|
| 2 | — | PWR | 5V → 1651 VIN via J1 | J1-Pin1 |
| 4 | — | PWR | 5V → LED load supply | J5-J8 Pin1 |
| 6 | — | GND | Ground | — |
| 8 | GPIO14 | — | **UART TX — DO NOT USE** | — |
| 10 | GPIO15 | — | **UART RX — DO NOT USE** | — |
| 12 | GPIO18 | — | JP2(closed)→J3-Pin4 / JP1(open)→J4-Pin6 | JP1, JP2 |
| 14 | — | GND | Ground | — |
| 16 | GPIO23 | — | Spare → JP2-Pin3 / J3-Pin3 (function TBD) | J3-Pin3 |
| 18 | GPIO24 | O | 74HC595 RCLK (latch pulse, manual GPIO) | 74HC595 pin12 |
| 20 | — | GND | Ground | — |
| 22 | GPIO25 | O | MOSFET direct — LED channel 10 | — |
| 24 | GPIO8 | O | **SPI0 CE0** — TFT CS → ILI9341 | J1 ICSP |
| 26 | GPIO7 | O | MOSFET direct — LED channel 9 | — |
| 28 | GPIO1 | — | **PIN REMOVED — DO NOT USE** (EEPROM) | — |
| 30 | — | GND | Ground | — |
| 32 | GPIO12 | PWM | **HW PWM0** — buzzer via MMBT3904 BJT | — |
| 34 | — | GND | Ground | — |
| 36 | GPIO16 | IO | **1-Wire data bus** (non-default pin) | J2-Pin2, J5-J8 Pin8 |
| 38 | GPIO20 | O | **SPI1 MOSI** → 74HC595 SER (pin14) | J4-Pin5 |
| 39 | — | GND | Ground | — |
| 40 | GPIO21 | O | **SPI1 SCLK** → 74HC595 SRCLK (pin11) | J4-Pin3 |

---

## Components and Their Control

### Adafruit 1651 — 2.8" TFT display + touch (ILI9341 + TSC2007)
- Display: SPI0 — MOSI/MISO/SCLK on GPIO10/9/11, CE0 (TFT CS) on GPIO8, DC on GPIO6
- Touch: I2C bus 1 — SDA/SCL on GPIO2/3. I2C address 0x48
- Touch interrupt: GPIO4, active-low, internal pull-up in software
- Backlight: GPIO13, hardware PWM1
- **1651 VddIO jumper must be set to 3V before power-on**
- LITE/#3 and TSIRQ/#2 jumpers must be soldered on the shield
- Wiring via J1 (16-pin header), point-to-point

### 74HC595 shift register — controls LED channels 1–8
- SPI1 MOSI (GPIO20/pin38) → 74HC595 SER (pin14)
- SPI1 SCLK (GPIO21/pin40) → 74HC595 SRCLK (pin11)
- GPIO24/pin18 → 74HC595 RCLK (pin12) — manual latch, pulse after each SPI write
- 74HC595 VCC and !SRCLR tied to 3.3V; !OE tied to GND (always enabled)
- Outputs QA–QH (pins 15,1–7) → LED channels 1–8 via AO3400A MOSFETs
- 500kΩ gate resistors + 10kΩ bleed resistors on each MOSFET gate

### LED channels — 10 total, all MOSFET-switched
- Channels 1–8: via 74HC595 (QA=ch1 through QH=ch8), AO3400A MOSFETs
- Channel 9: GPIO7/pin26 direct GPIO, AO3400A, 500Ω gate + 10kΩ bleed
- Channel 10: GPIO25/pin22 direct GPIO, AO3400A, 500Ω gate + 10kΩ bleed
- LED supply: 5V from RPi pin4 via J5–J8 pin1
- Channels 1–5 route through J5 and J6 (identical); channels 6–10 through J7 and J8
- J5–J8 pinout: pin1=5V, pins2–6=LED channels, pin7=GND, pin8=1-Wire data

### Buzzer — CEM-1203(42) passive magnetic buzzer, 5V rated, 2048Hz resonance
- GPIO12/pin32, hardware PWM0-A
- Driven via MMBT3904 NPN BJT: GPIO12 → 1kΩ → BJT base
- BJT collector → buzzer(−); buzzer(+) → 5V rail
- Flyback diode fitted: cathode at 5V/buzzer(+), anode at collector/buzzer(−)
- Use pigpio for hardware PWM; frequency tunable 1Hz steps in real time

### 1-Wire temperature probe
- GPIO16/pin36 — non-default pin
- 4.7kΩ pull-up to 3.3V on PCB (hard requirement)
- Connects to J2 (3-pin polarised: pin1=3.3V, pin2=data, pin3=GND)
- Also routed to J5–J8 pin8 for in-situ 1-Wire devices; 100nF cap at each device end
- J2 connects to ~1m cable to external temperature probe

### Connectors
- **J1**: 16-pin, 1651 shield interface (see full table in pinmap_v2.pdf section 4.1)
- **J2**: 3-pin polarised, 1-Wire probe
- **J3**: 4-pin polarised — pin1=GND, pin2=3.3V, pin3=GPIO23(spare), pin4=GPIO18(via JP2)
- **J4**: 6-pin polarised — pin1=3.3V, pin2=GND, pin3=GPIO21(SPI1 SCLK), pin4=GPIO19(SPI1 MISO), pin5=GPIO20(SPI1 MOSI), pin6=GPIO18(via JP1, open)
- **J5–J8**: 8-pin ribbon, LED banks (J5/J6 identical for channels 1–5; J7/J8 for 6–10)
- **JP1**: solder jumper, currently **open** — GPIO18 not connected to J4-Pin6
- **JP2**: solder jumper, currently **closed** — GPIO18 connected to J3-Pin4

---

## Required /boot/firmware/config.txt entries

```
dtparam=spi=on                        # SPI0 for ILI9341 display
dtparam=i2c_arm=on                    # I2C1 for TSC2007 touch
dtoverlay=w1-gpio,gpiopin=16          # 1-Wire on GPIO16 (BCM number, not physical pin)
dtoverlay=spi1-1cs,cs0_pin=X         # SPI1 for 74HC595 (replace X with spare GPIO, e.g. 23)
```

Enable pigpio daemon for hardware PWM:
```
sudo systemctl enable pigpiod
sudo systemctl start pigpiod
```

---

## Key software patterns

### Drive 74HC595 (shift one byte, then latch)
```python
import spidev, RPi.GPIO as GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(24, GPIO.OUT, initial=GPIO.LOW)   # RCLK

spi1 = spidev.SpiDev()
spi1.open(1, 0)
spi1.no_cs = True
spi1.max_speed_hz = 1_000_000

def led_update(byte_val):
    """byte_val: bit7=ch1(QA) ... bit0=ch8(QH). Set bit HIGH to turn LED ON."""
    spi1.writebytes([byte_val])
    GPIO.output(24, GPIO.HIGH)   # latch rising edge
    GPIO.output(24, GPIO.LOW)
```

### Hardware PWM — buzzer and backlight (pigpio)
```python
import pigpio, time
pi = pigpio.pi()

def set_backlight(percent):       # 0–100
    pi.hardware_PWM(13, 2000, int(percent * 10000))

def buzzer_on(freq_hz):           # e.g. 2048
    pi.hardware_PWM(12, freq_hz, 500000)   # 50% duty

def buzzer_off():
    pi.hardware_PWM(12, 2000, 0)

# Always set backlight first, buzzer second
set_backlight(80)
buzzer_on(2048)
```

### 1-Wire temperature read
```python
import glob
sensor = glob.glob('/sys/bus/w1/devices/28-*')[0] + '/w1_slave'
with open(sensor) as f:
    lines = f.readlines()
temp_c = int(lines[1].split('t=')[1]) / 1000.0
```

### TSIRQ touch interrupt (GPIO4)
```python
GPIO.setup(4, GPIO.IN, pull_up_down=GPIO.PUD_UP)
# TSC2007 I2C address 0x48 — read coordinates after interrupt fires
```

### Direct MOSFET control (channels 9 and 10)
```python
GPIO.setup(7,  GPIO.OUT, initial=GPIO.LOW)   # ch9
GPIO.setup(25, GPIO.OUT, initial=GPIO.LOW)   # ch10
GPIO.output(7,  GPIO.HIGH)   # ch9 on
GPIO.output(25, GPIO.HIGH)   # ch10 on
```

---

## Suggested step-wise test order
1. Power on — verify 3.3V and 5V rails with multimeter before connecting any peripherals
2. `i2cdetect -y 1` — should show device at 0x48 (TSC2007)
3. SPI0 — display: fill screen a solid colour, confirm backlight on/off with GPIO13 PWM
4. Touch: tap screen, read TSC2007 coordinates via I2C
5. SPI1 + 74HC595: shift 0x01 through 0x80, confirm each LED channel lights in order
6. Direct MOSFET channels 9 and 10 via GPIO7 and GPIO25
7. Buzzer: pigpio PWM on GPIO12, sweep 1500–2500Hz, find preferred tone
8. 1-Wire: `cat /sys/bus/w1/devices/28-*/w1_slave`, verify temperature reading
