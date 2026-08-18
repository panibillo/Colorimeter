# Project Overview

## Purpose and Platform

- The purpose is to build a scientific grade colorimeter dedicated to reading the color of a color indicator strip in a chemical testing kit.
- The code will run on a Raspberry Pi Zero W or Raspberry Pi Zero 2 W, operating a PiCamera v2 module
- Language: Python >= 3.10

## User Interfaces

This will be a standalone device with three interface types:
- **Physical interface**: Small character readout (16-64 chars), buttons, LEDs
- **Web interface**: Lightweight HTTP, accepts parameters, executes readings, reports results and metadata
- **Terminal interface**: Linux command-line interaction

All interfaces emphasize simplicity.

## Data Storage and Precision

The SQLite database on the Pi:
- Stores readings and metadata
- Reads/writes configuration values
- Records ongoing statistical measures of tool precision

## Problem Domain and Solution Strategy

### Measurement Principles

- The colorimeter reads 5 mm x 6.25 mm color indicators on chemical testing strips
- Color density measures the concentration of chemical in a sample
- Color space analyzed: white → yellow → brown
- Target precision: 2.5–3 digits; accuracy: 1.5–2.5 digits
- This matches the precision and accuracy limits of the chemical methods themselves

### Imaging and Calibration Hardware

- Each reading analyzes one or more raw Bayer images (3280×2264, BGGR, minimal color correction)
- Image stage: dark box with fully controlled internal lighting and fixed focal length
- Lighting: blue, green, white LEDs (individually or combined)

### Calibration Card Design

The imaging stage includes:
- Color calibration card with reference patches: black, white, brown, yellow, and 5 intermediate yellow shades
- Center opening (~250×250 px) reveals the target indicator strip
- Calibration patches are ~350×350 px each
- **Important**: Calibration card is imaged in every reading for image-by-image normalization

### Reading Strategy

- Capture multiple sequential images as needed to achieve requested precision
- Minimize captures: only take as many images as necessary
- Analyze both target area and calibration colors in each image
- Calibration: store color readings from indicator strips exposed to known chemical concentrations
- Support different chemical test strip types with stored parameters
- Optional: analyze test strip before chemical exposure if it improves accuracy

## Testing Strategy

Testing is split between RPi and desktop:
- **RPi**: Capture and save raw images
- **Desktop**: Debug algorithms using test image files
- **Desktop mock libraries**: Mock Picamera2 and cv2 to isolate hardware dependencies
- **Unit tests**: Verify analysis algorithms and workflows
- **Transfer**: Use `scp` to move files between systems

## Python Library Architecture

### Design Principle: Hardware Abstraction

All hardware access is organized into thin abstraction classes that hide
the underlying library backend. Application code calls the abstraction;
only the abstraction knows which library is being used.

```
Application code
      │
      ▼
Abstraction class  (e.g. Backlight, LEDBank, Buzzer)
      │
      ▼
Backend library    (gpiozero / lgpio / spidev / smbus2)
```

This separation allows the backend to be swapped — for portability,
Bookworm compatibility, or future microcontroller offload — without
touching application logic.

### Approved Libraries

| Purpose                  | Library       | Notes                                      |
|--------------------------|---------------|--------------------------------------------|
| General GPIO             | `gpiozero`    | Bookworm-native; replaces `RPi.GPIO`       |
| PWM (backlight, etc.)    | `gpiozero`    | `PWMOutputDevice`; test before assuming sufficient |
| Hardware PWM (buzzer)    | `lgpio`       | No daemon required; Bookworm-native        |
| SPI (display, 74HC595)   | `spidev`      | Retain as-is                               |
| I2C (TSC2007 touch)      | `smbus2`      | Retain as-is                               |

### Removed Dependencies

`pigpio` / `pigpiod` have been removed from this project.

Rationale:
- Requires a root-owned system daemon (`pigpiod`) to be running before
  any script starts, creating fragile boot ordering and install friction
- Not recommended for Raspberry Pi OS Bookworm
- Daemon-based architecture is an unnecessary onboarding burden for
  FOSS users

### Future Architecture Note

For a v2 design, consider offloading all timing-critical I/O (PWM, ADC
reads, LED sequencing) to an RP2040-class microcontroller, communicating
with the Pi over UART or USB. This eliminates Linux GPIO timing
constraints entirely and is the direction the broader Pi ecosystem is
moving. The abstraction layer described above makes this migration
straightforward: only the backend of each abstraction class changes.
