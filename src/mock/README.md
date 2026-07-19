# Mock Hardware Drivers

This directory contains mock implementations of the hardware drivers used by the Colorimeter application. These allow the interface to be tested and developed without physical hardware.

## Modules

### `gpiozero_mock.py`
Mock GPIO control (e.g., backlight pin). Logs all on/off events.

### `ili9341_mock.py`
Mock display driver using matplotlib. Renders the 320×240 display in a matplotlib window:
- Widgets drawn as bounding boxes
- Text labels and values displayed
- Full matplotlib toolkit for zoom/pan

### `tsc2007_mock.py`
Mock touchscreen driver. Responds to mouse clicks in the matplotlib window.
- Mouse clicks → touch events
- Position tracking
- `is_touched` state

### `kb_reader_mock.py`
Mock keyboard reader. Captures keyboard events from matplotlib window and queues them.

### `beeper_mock.py`
Mock audio feedback. Logs all beep/alarm events (no actual sound).

### `spidev_mock.py`
Mock SPI bus (`spidev.SpiDev`). Used by lighting hardware drivers that drive
shift registers (e.g. `lighting_cal8.py`'s 74HC595 board). Logs `open`/
`writebytes`/`close` calls; no real bus is opened.

## Usage

### Enable Simulation Mode

Set the environment variable on the command line using `export` before running the app.
This only has to be set once on the desktop or laptop:

```bash
export COLORIMETER_SIMULATION=1
python3 src/run_simulation.py
```

Or run in one command:

```bash
COLORIMETER_SIMULATION=1 python3 src/run_simulation.py
```

### Interaction in Simulation

**Display**: matplotlib window showing the 320×240 screen
- Use standard matplotlib zoom/pan tools
- Close window to exit the application

**Touch/Click**: Use mouse clicks on the display window
- Left-click to trigger touch events at that location
- Click on buttons to activate them

**Keyboard**:
- `Tab` / `Shift+Tab`: navigate between focusable widgets
- `Enter`: activate focused button or toggle TextBox editing
- `Backspace` / `Delete`: delete character in editing TextBox
- Printable characters: type into editing TextBox

## Development Tips

### Logging

The mock drivers log all events at INFO level. To see detailed debug output:

```bash
export COLORIMETER_SIMULATION=1
PYTHONUNBUFFERED=1 python3 src/run_simulation.py 2>&1 | grep -E "(mock|AppController)"
```

### Testing Specific Screens

To navigate to a specific screen, modify `app_controller.start()` to call:

```python
self._navigate_to("CA")  # Jump to Capture screen instead of SE
```

### Extending the Mocks

To add new functionality to a mock (e.g., mouse drag for RectWidget):

1. Extend the mock class with a new method
2. Call the method from the corresponding event handler in `ili9341_mock.py`
3. Test in simulation mode

## Limitations

- **Display**: Text rendering at matplotlib fontsize may look different from hardware
- **Touch**: No multi-touch or pressure sensitivity
- **Keyboard**: Only standard ASCII + special keys (Tab, Enter, Backspace, Delete)
- **Audio**: No sound; events logged only

## Lighting Drivers

Lighting hardware drivers (e.g. `lighting_cal8.py`) are not switched by
`app_controller.py`. They check `COLORIMETER_SIMULATION` themselves and
self-select `gpiozero_mock`/`spidev_mock` vs the real libraries, so any script
that imports them (its own CLI, `camtest7.py`, `camtest8.py`, etc.) gets
simulation support automatically. Future lighting boards should follow the
same convention rather than adding a new whole-module mock, reusing
`gpiozero_mock.py`/`spidev_mock.py` wherever possible.

## Real Hardware

To use real hardware drivers instead of mocks:

```bash
# Either unset the env var:
unset COLORIMETER_SIMULATION
python3 src/app_controller.py

# Or explicitly set it to 0:
COLORIMETER_SIMULATION=0 python3 src/app_controller.py
```
