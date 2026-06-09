# Colorimeter — What Has Been Built

A summary of the working infrastructure as of 2026-06-02. Intended as a
quick-start context for a new conversation.

---

## Hardware drivers (`src/`)

| Module | What it does | Status |
|---|---|---|
| `ili9341.py` | SPI TFT display driver, 320×240, RGB565. `fill_screen`, `fill_rect`, `draw_rect`, `draw_text`. | Working |
| `tsc2007.py` | I2C resistive touchscreen. `init()`, `is_touched`, `position()` → (x, y). | Working |
| `beeper.py` | lgpio software-PWM audio, 10 kHz carrier, sine-table modulated. Singleton. Public API: `beep()`, `click()`, `set_beep_volume()`, `set_click_volume()`, `cleanup()`. | Working |
| `kb_reader.py` | evdev USB keyboard reader. Non-blocking `read_one()` returns key strings: printable chars, `'tab'`, `'shift-tab'`, `'enter'`, `'backspace'`, `'escape'`, arrow keys. Works over SSH and under systemd. | Working |
| `bayer_cam.py` | Picamera2 wrapper. Dual-stream (RGB preview + raw Bayer). Auto-detects IMX219 / IMX708. `setup_camera()`, `capture_frame()` → (rgb, bayer, metadata), `autofocus_once()`. | Working (Pi only) |
| `lighting_cal8.py` | LED controller via gpiozero. Individual LEDs addressable by color name. Falls back to console stub when gpiozero absent. | Working |

## Mock modules

| Module | Substitutes for |
|---|---|
| `mock_beeper.py` | `beeper.py` — plays terminal bell `\a` |
| `mock_Picamera2.py` | Picamera2 — returns pre-captured test images |

---

## UI layer (`src/`)

### `geometry.py` / `rect.py`
`Rect(x, y, w, h)` — position/size primitive used by every widget.

### `widgets.py`
Widget library. All widgets draw using `display.fill_rect / draw_rect / draw_text`.

| Widget | Description |
|---|---|
| `Screen` | Root container. `render(display)`, `find(name)`, `focusable_widgets()`. |
| `Area` | Grouped sub-region with background fill; routes touch to children. |
| `Label` | Static text. |
| `TextBox` | Updatable text. Editable mode: touch to start editing (green border), Enter to toggle, Tab moves focus and carries editing state, focus loss stops editing. `set_value()`, `handle_char()`. |
| `Button` | Colored rect + label. `on_press` callback. Press flash + click sound. Focus ring when focused. |
| `RectWidget` | Filled or bordered rectangle. |

Widget base has: `name`, `enabled`, `focusable`, `focused`, `on_focus_change` hook,
`set_focused(bool, display)`, `handle_touch()`, `hit_test()`.

Module-level `set_button_sound(fn)` decouples click audio from hardware.

### `screen_manager.py`
Owns the active `Screen` and keyboard focus list.

- `switch_to(screen)` — full render + focus reset
- `on_touch(x, y)` — dispatches to screen; syncs focus to any TextBox that started editing
- `on_key(key)` — Tab/Shift-Tab (carry editing state), Enter (toggle TextBox editing or activate Button), printable chars → focused editing TextBox
- `focus_widget(widget)` — direct focus jump (used by controller)

### `screen_manager.py` self-test
Run `python3 src/screen_manager.py` — no hardware required.

---

## Application layer

### `app_controller.py`
Central controller. One instance per run. Owns:
- Hardware lifetime (display, touch, keyboard, beeper, backlight)
- Both prototype screens (`proto_screen.py`, `proto_screen1.py`)
- NEXT-button navigation between screens
- Data record `{"id", "user", "test", "collect"}`
- Main event loop: 20 ms poll, 1-second clock tick, touch debounce

### Prototype screens (`dev/`)

| File | Screen |
|---|---|
| `proto_screen1.py` | "New Test" data-entry form. Four TextBox fields, dumb Label row labels, header with Start + slot-B button, status bar. |
| `proto_screen.py` | Placeholder "measure" screen. |
| `proto_main.py` | 17-line entry point: `AppController().start()`. |

---

## Analysis / data layer (`src/`, `dev/`)

| Module | Status |
|---|---|
| `db_manager.py` | SQLite wrapper: `read_all`, `read_one`, `read_val`, `writevals`. Schema and path TBD. |
| `roi.py` | ROI object definition; read/write ROIs to SQLite. Partial. |
| `dev/analysis1.py`, `ecalc.py`, `ecalc_rects.py` | Bayer extraction, noise analysis, color analysis experiments. Research stage. |
| `dev/calibration_1.py` | Calibration experiments. Research stage. |

---

## What is NOT yet built

- YAML screen/config system
- Multiple test slots (A/B/C/D) and slot-switching navigation
- All production screens (only 2 of ~15 exist)
- Timer widget (countdown/countup)
- Scrolling selector widget
- Numeric keypad overlay
- Image viewer widget
- DS18B20 temperature sensor integration
- LED sequencing for measurement phase
- Production camera capture pipeline
- Image analysis pipeline (Bayer → ROI → result)
- Full SQLite test record schema
- HDF5 archival storage
- Flask web interface
- HTTP upload / desktop receiver
- Pub/sub state bus
- Phase scheduling / resource locking
- Localization
- Error overlay system
- Boot sequence / splash screen
- Simulation mode (unified config flag)
