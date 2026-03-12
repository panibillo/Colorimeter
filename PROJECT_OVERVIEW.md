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

- The colorimeter reads 8 mm square color indicators from chemical testing strips
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
