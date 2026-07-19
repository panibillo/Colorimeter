# Screen and Widget Format

Defines how screens and widgets are configured. See [TERMINOLOGY.md](TERMINOLOGY.md)
for definitions and [PHASE_SCHEDULING_DESIGN.md](PHASE_SCHEDULING_DESIGN.md) for
the scheduling model.

---

## 1. File Structure

```
config/
  instrument.yaml      hardware config, display dimensions, simulation mode
  palette.yaml         named color definitions
  locale/
    en.yaml            English user-visible strings (added when needed)
src/
  widget_defs.csv      all widget instances with visual properties
  screen_defs.csv      all screen definitions with timing and sequence
  screen_widgets.csv   maps each screen to its widgets with tab ordering
```

`instrument.yaml` and `palette.yaml` remain YAML — they are hardware/style
config, not layout data. Screens and widgets use CSV for compact, tabular
definitions suited to a spreadsheet or text editor on a small device.

---

## 2. Instrument Config (`instrument.yaml`)

```yaml
display:
  width:         320
  height:        240
  header_height:  50
  status_height:  25

simulation: false      # true → use mock hardware modules

hardware:
  backlight_pin: 13
  spi_device:    0
  i2c_bus:       1
```

---

## 3. Palette (`palette.yaml`)

Colors are referenced by name in CSV files. Raw RGB values live only here.
The screen builder converts to RGB565 on load.

```yaml
white:          [255, 255, 255]
black:          [  0,   0,   0]
body_coral:     [255, 238, 232]
row_line:       [220, 180, 168]
label_coral:    [180,  55,  35]
field_blue:     [ 60, 130, 200]
start_green:    [ 25, 150,  25]
status_bg:      [ 28,  38,  58]
btn_gray:       [ 65,  85, 115]
btn_text:       [210, 225, 240]
timer_warn:     [255, 180,   0]
timer_critical: [220,  40,  40]

slots:
  A: { bg: [  0,  80, 200], text: [200, 220, 255], dim: [ 30,  50, 120] }
  B: { bg: [200, 150,   0], text: [255, 240, 180], dim: [120,  90,   0] }
```

---

## 4. Widget Definitions (`widget_defs.csv`)

One row per widget. Each widget is a **singleton** — widget id `101` is one
Python object that may appear on multiple screens and always holds the same
value. This eliminates explicit data-sync on screen navigation.

### Columns

| Column | Type | Description |
|--------|------|-------------|
| `widget_id` | int (100–999) | Hardcoded permanent identifier |
| `widget_name` | str | Type name: `Button`, `TextBox`, `Label`, `RectWidget`, `Timer` |
| `x` | int | Left edge in screen-absolute pixels |
| `y` | int | Top edge in screen-absolute pixels |
| `w` | int | Width in pixels |
| `h` | int | Height in pixels |
| `label` | str | On Button: button text. On TextBox/Label: label to left of field. |
| `fgcolor` | str | Foreground color name (from palette) |
| `bgcolor` | str | Background color name (from palette) |
| `default` | str | Initial value; empty string if none |

Coordinates are **screen-absolute** (origin = screen top-left, y increases downward).
The screen builder does not add zone offsets; the author places widgets by final position.

### Example rows

```
widget_id, widget_name, x,   y,  w,   h,  label,    fgcolor,     bgcolor,     default
101,        TextBox,     82,  53, 230, 39, "",        black,       white,       ""
102,        TextBox,     82,  93, 230, 39, "",        black,       white,       ""
103,        TextBox,     82, 133, 230, 39, "",        black,       white,       ""
104,        TextBox,     82, 173, 230, 39, "",        black,       white,       ""
111,        Label,        8,  52,  68, 39, "ID",      white,       label_coral, ""
112,        Label,        8,  92,  68, 39, "User",    white,       label_coral, ""
113,        Label,        8, 132,  68, 39, "Test",    white,       label_coral, ""
114,        Label,        8, 172,  68, 39, "Collect", white,       label_coral, ""
201,        Button,       0, 215, 107, 25, "MENU",    btn_text,    btn_gray,    ""
202,        Label,       107, 215, 106, 25, "",        btn_text,    status_bg,   ""
203,        Button,      213, 215, 107, 25, "NEXT",    btn_text,    btn_gray,    ""
```

### Wiring (assigned in Python)

Behavioral wiring is assigned at startup in Python, keyed by `widget_id`.
The CSV defines layout only; callbacks and data bindings are in code.

```python
# After loading widget_defs.csv into widgets dict:
widgets[101].editable = True
widgets[101].on_change = lambda v: record.update({'id': v})

widgets[203].on_press = self.nav_next
```

This keeps the CSV simple and wiring explicit. Roughly 1–2 lines per
interactive widget (~20–30 lines total across all screens).

---

## 5. Screen Definitions (`screen_defs.csv`)

One row per screen.

### Columns

| Column | Type | Description |
|--------|------|-------------|
| `screen_id` | str (2 chars) | Hardcoded permanent identifier (e.g. `MM`, `SE`, `R1`) |
| `phase` | int | Phase group id if timed; `0` if this screen has no phase |
| `seq` | int | Sequence order within phase (1, 2, 3…); `0` if not phased |
| `duration` | int | Timer duration in seconds; `0` = no time limit |
| `modal` | int | `1` = navigation locked while active; `0` = free navigation |
| `beginactions` | str | Semicolon-separated action list: `alarm_on`, `alarm_off`, `start_timer` |
| `next` | str | `screen_id` of the screen NEXT button navigates to; empty = no NEXT |

### Example rows

```
screen_id, phase, seq, duration, modal, beginactions,        next
MM,        0,     0,   0,        0,     "",                  SE
SE,        1,     1,   0,        1,     "",                  TC
TC,        1,     2,   0,        0,     "",                  R1
R1,        1,     3,   30,       1,     "alarm_off;start_timer", I1
I1,        1,     4,   600,      0,     "alarm_off;start_timer", CA
CA,        1,     5,   60,       1,     "alarm_off;start_timer", CR
CR,        1,     6,   0,        0,     "",                  RS
RS,        1,     7,   0,        0,     "",                  MM
```

Screen ids used above:
`MM`=Main Menu, `SE`=Sample Entry, `TC`=Temperature Check,
`R1`=Reagent 1, `I1`=Incubation 1, `CA`=Capture, `CR`=Capture Review, `RS`=Results Summary

---

## 6. Screen-Widget Mapping (`screen_widgets.csv`)

Maps which widgets appear on each screen, and whether they are in the tab order.

### Columns

| Column | Type | Description |
|--------|------|-------------|
| `screen_id` | str | Foreign key to `screen_defs.csv` |
| `widget_id` | int | Foreign key to `widget_defs.csv` |
| `tabbed` | int | `1` = included in tab/focus order; `0` = non-focusable (display only) |
| `tab_order` | int | Position in tab sequence (1, 2, 3…); `0` if not tabbed |

### Example rows

```
screen_id, widget_id, tabbed, tab_order
SE,        111,       0,      0          # "ID" label — display only
SE,        101,       1,      1          # ID TextBox — first tabbed
SE,        112,       0,      0          # "User" label
SE,        102,       1,      2          # User TextBox
SE,        113,       0,      0          # "Test" label
SE,        103,       1,      3          # Test TextBox
SE,        114,       0,      0          # "Collect" label
SE,        104,       1,      4          # Collect TextBox
SE,        201,       1,      5          # MENU button
SE,        202,       0,      0          # clock label
SE,        203,       1,      6          # NEXT button
```

---

## 7. Loading Sequence

At startup, `AppController`:

1. Reads `palette.yaml` → color name→RGB dict
2. Reads `instrument.yaml` → display dimensions and hardware config
3. Reads `widget_defs.csv` → constructs all Widget objects into `widgets` dict keyed by id
4. Reads `screen_defs.csv` → constructs all Screen objects into `screens` dict keyed by id
5. Reads `screen_widgets.csv` → populates each screen's `tabbed_widgets` and `untabbed_widgets` lists
6. Calls `_wire()` → assigns callbacks and data bindings by widget_id
7. Starts event loop with initial screen (`MM`)

---

## 8. Standard Action Names

Button `on_press` callbacks registered by `AppController`:

| Name | Meaning |
|------|---------|
| `nav_next` | Advance to screen defined by `next` in screen_defs |
| `nav_back` | Return to previous screen (history stack) |
| `nav_menu` | Go to `MM` (main menu) |
| `start_test` | Start test for active slot |
| `toggle_slot` | Toggle active slot A ↔ B |
| `confirm_added` | Mark reagent addition complete |
| `start_capture` | Initiate camera capture |
| `skip_screen` | Skip current modal screen (marks result degraded) |

---

## 9. Resolved Design Decisions

| Question | Resolution |
|----------|------------|
| Pixel coordinates | Screen-absolute throughout; author places widgets at final position |
| Color format | Named strings from palette.yaml; builder converts to RGB565 |
| Strings / localization | Inline `label` in CSV now; migrate to locale keys later |
| Standard status bar | Three widgets (201 MENU, 202 clock, 203 NEXT) appear on every phased screen via screen_widgets.csv |
| Navigation targets | `next` field in screen_defs.csv; no protocol-level indirection |
| Parameterized screens | Not needed now; separate rows for each phase instance (R1, R2, I1, I2 etc.) |
| Source key validation | Runtime for now; validate at load time when schema is stable |
| Flask template generation | Deferred; screen_widgets.csv provides the data contract when needed |
| Body sub-areas | Use RectWidget for visual dividers; Area widget added when a screen requires it |
