# YAML Screen/Widget Format — Proposal

*Draft for review. Answer the questions in Section 5 before implementation begins.*

---

## 1. File Structure

```
config/
  instrument.yaml        hardware config, display dimensions, sim mode
  palette.yaml           all named colors
  locale/
    en.yaml              English user-visible strings
screens/
  new_test_entry.yaml    one file per screen
  temperature_check.yaml
  reagent_phase.yaml     (may be parameterized — see Q9)
  capture.yaml
  ...
protocols/
  standard_colorimetric.yaml   phase sequence, periods, navigation targets
```

---

## 2. Instrument Config (`instrument.yaml`)

```yaml
display:
  width:         320
  height:        240
  header_height:  50
  status_height:  25
  # body_height is computed: height - header_height - status_height

simulation: false          # true → substitute all mock hardware modules

hardware:
  backlight_pin: 13
  spi_device:    0
  i2c_bus:       1
```

The screen builder reads `display.*` from this file — no hardcoded pixel
constants anywhere in screen YAML or Python.

---

## 3. Palette (`palette.yaml`)

Colors are referenced by name throughout screen YAML. Raw RGB values live
only here.

```yaml
# Static named colors
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

# Slot colors — one entry per slot letter
slots:
  A: { bg: [  0,  80, 200], text: [200, 220, 255], dim: [ 30,  50, 120] }
  B: { bg: [200, 150,   0], text: [255, 240, 180], dim: [120,  90,   0] }
  C: { bg: [ 20, 150,  50], text: [190, 255, 210], dim: [ 10,  90,  30] }
  D: { bg: [200,  50,  40], text: [255, 200, 190], dim: [120,  30,  25] }
```

In widget YAML, `color: slot_bg` resolves to the *active slot's* bg color at
render time. This is the mechanism for consistent slot color coding.

---

## 4. Screen YAML Format

### 4.1 Top-level structure

```yaml
screen:
  id: new_test_entry

  header:
    bg: white
    widgets:
      - ...

  body:
    bg: body_coral
    widgets:
      - ...

  # status_bar: omit to get the standard bar; include to override
  # status_bar:
  #   bg: status_bg
  #   widgets: [...]
```

The standard status bar (MENU / clock / NEXT) is generated automatically
when `status_bar:` is absent. Screens like the overdue widget that replace
the status bar provide their own `status_bar:` block.

Zone heights come from `instrument.yaml` (`header_height`, `status_height`).
The body fills the remaining height. No pixel values appear in zone
declarations.

### 4.2 Widget descriptor fields (all types)

| Field | Required | Description |
|---|---|---|
| `type` | yes | `label`, `textbox`, `button`, `rect`, `timer`, `selector`, `image` |
| `name` | yes | Identifier for `screen.find()` and data binding wiring |
| `rect` | yes | `[x, y, w, h]` in logical pixels (origin = zone top-left) |

Origin for `rect` is the **zone's** top-left corner (not the screen's).
The screen builder adds the zone offset when constructing widgets.

### 4.3 Widget types

#### `label` — static or bound text

```yaml
- type: label
  name: title
  text_key: screen.new_test.title    # localization key (preferred)
  # text: "New Test"                 # inline fallback
  rect: [8, 5, 178, 24]
  color: black
  bg: white
  size: 22
  align: left      # left | center | right  (default: left)
  valign: top      # top  | middle | bottom (default: top)
```

#### `textbox` — data-bound editable or read-only field

```yaml
- type: textbox
  name: id_field
  bind: record.id          # data binding key (see §4.4)
  rect: [82, 3, 230, 39]   # relative to body origin
  color: black
  bg: white
  size: 22
  align: left
  valign: middle
  border_color: field_blue
  editable: true
```

#### `button` — pressable, fires a named action

```yaml
- type: button
  name: start_btn
  text_key: btn.start
  rect: [193, 0, 74, 50]   # relative to header origin
  color: start_green
  text_color: white
  size: 22
  action: start_test        # named action; wired by AppController
```

#### `rect` — divider line or filled rectangle

```yaml
- type: rect
  rect: [0, 39, 320, 1]
  color: row_line
  fill: true
```

#### `timer` — countdown or countup display

```yaml
- type: timer
  name: reagent_timer
  source: phase.remaining_seconds    # runtime source (see §4.4)
  direction: countdown               # countdown | countup
  rect: [60, 30, 200, 70]
  size: 48
  color: white
  bg: body_coral
  align: center
  thresholds:                        # color changes as time runs low
    - at_seconds: 60
      color: timer_warn
    - at_seconds: 10
      color: timer_critical
```

#### `selector` — scrolling list bound to a record field

```yaml
- type: selector
  name: volume_selector
  bind: record.volume
  options_key: config.reagent_volumes   # key into instrument.yaml list
  rect: [10, 10, 300, 140]
  item_color: white
  item_bg: btn_gray
  selected_color: white
  selected_bg: start_green
```

#### `image` — pre-downsampled pixel array viewer

```yaml
- type: image
  name: capture_preview
  source: capture.rgb_thumb    # runtime source
  rect: [0, 0, 320, 165]
```

### 4.4 Data binding and runtime sources

`bind:` connects a textbox (or label) to a field in the controller's data
model. The controller reads and writes these at navigation time.

`source:` connects a widget to a runtime value maintained by the controller.
The controller pushes updates; the widget calls `set_value()` and redraws.

| Key | Meaning |
|---|---|
| `record.id` | `controller.record["id"]` — editable test field |
| `record.user` | `controller.record["user"]` |
| `clock` | Current time string, updated every second |
| `slot.letter` | Active slot letter: "A", "B", "C", "D" |
| `slot_bg` | Active slot background color (palette reference) |
| `phase.remaining_seconds` | Seconds left in current period |
| `phase.elapsed_seconds` | Seconds since current period started |
| `capture.rgb_thumb` | Downsampled RGB array from last capture |

The full list grows as screens are built. New source keys are added to the
controller and documented here.

### 4.5 Actions

`action:` on a button names a callback registered by `AppController`. The
YAML does not define what actions do — only that they exist. The controller
wires each name to a method.

Standard action names:

| Name | Meaning |
|---|---|
| `nav_next` | Advance to next phase per protocol |
| `nav_back` | Return to previous phase |
| `nav_menu` | Go to opening menu |
| `start_test` | Start the test for this slot |
| `toggle_slot` | Cycle active test slot (A→B→C→D→A) |
| `confirm_added` | Mark reagent-addition period complete |
| `start_capture` | Initiate camera capture sequence |
| `overdue_priority` | Jump to highest-priority overdue task |
| `overdue_next` | Advance overdue widget cursor |

---

## 5. Protocol YAML Format

The protocol defines the phase sequence, period structure, and navigation.
Screens are reusable; the protocol determines what comes next.

```yaml
protocol:
  id: standard_colorimetric

  phases:
    - id: sample_entry
      screen_id: new_test_entry
      nav_next: temperature_check     # phase id to go to on nav_next action
      periods:
        - name: data_entry
          modal: false
          duration_seconds: 0       # deadline window; 0 = no deadline
          completion_action: all_required_fields_filled

    - id: temperature_check          # second sample entry screen
      screen_id: temperature_check
      nav_next: reagent_phase_1
      periods:
        - name: sensor_read
          modal: false
          duration_seconds: 0

    - id: reagent_phase_1
      screen_id: reagent_phase
      params:                         # Q9: parameterized screen instance
        phase_label_key: phase.reagent_1.label
        incubation_seconds: 600
      nav_next: measurement
      periods:
        - name: reagent_addition
          modal: true
          duration_seconds: 30
          alert_repeat_seconds: 30
          completion_action: confirm_added
        - name: reagent_agitate
          modal: true
          duration_seconds: 15
          completion_action: null
        - name: incubation_timer
          modal: false
          duration_seconds: 600       # confirm

    - id: reagent_phase_2
      screen_id: reagent_phase
      params:                         # Q9: parameterized screen instance
        phase_label_key: phase.reagent_2.label
        #incubation_seconds: 420
      nav_next: measurement
      periods:
        - name: reagent_addition
          modal: true
          duration_seconds: 20
          alert_repeat_seconds: 10
          completion_action: null
        - name: reagent_agitate
          modal: true
          duration_seconds: 5
          completion_action: confirm_added
        - name: incubation_timer
          modal: false
          duration_seconds: 600       # confirm
    
    - id: measurement
      screen_id: capture
      nav_next: results_summary
      periods:
        - name: manual_classify
          modal: true
          duration_seconds: 100
          alert_repeat_seconds: 30
          completion_action: classified
        - name: initiate_capture
          modal: true
          duration_seconds: 60
          alert_repeat_seconds: 60
          completion_action: capture_started
        - name: capture_in_progress
          modal: false
          duration_seconds: 120
          completion_action: capture_ended

    - id: results_summary
      screen_id: results_summary
      nav_next: null                  # end of protocol
      periods:
        - name: view_results
          modal: false
          duration_seconds: 0         # no time limit
```

---

## 6. `new_test_entry.yaml` — Worked Example

The full YAML equivalent of the existing `proto_screen1.py`:

```yaml
screen:
  id: new_test_entry

  header:
    bg: white
    widgets:
      - type: label
        name: title
        text_key: screen.new_test.title
        rect: [8, 5, 178, 24]
        color: black
        bg: white
        size: 22

      - type: label
        name: hdr_datetime
        source: clock
        rect: [8, 31, 178, 17]
        color: black
        bg: white
        size: 14

      # - type: button
      #   name: start_btn
      #   text_key: btn.start
      #   rect: [193, 0, 74, 50]
      #   color: start_green
      #   text_color: btn_text
      #   size: 22
      #   action: start_test

      - type: button
        name: slot_btn
        source: slot.letter
        rect: [260, 0, 60, 50]
        color: slot_bg
        text_color: slot_dim
        size: 34
        action: toggle_slot

  body:
    bg: body_coral
    widgets:
      - type: label
        name: id_lbl
        text_key: field.id
        rect: [8, 2, 68, 35]
        color: white
        bg: label_coral
        size: 13

      - type: textbox
        name: id_field
        bind: record.id
        rect: [82, 0, 230, 39]
        color: black
        bg: white
        size: 22
        align: left
        valign: middle
        border_color: field_blue
        editable: true

      - type: rect
        rect: [0, 39, 320, 1]
        color: row_line
        fill: true

      - type: label
        name: user_lbl
        text_key: field.user
        rect: [8, 42, 68, 35]
        color: white
        bg: label_coral
        size: 13

      - type: textbox
        name: user_field
        bind: record.user
        rect: [82, 40, 230, 39]
        color: black
        bg: white
        size: 18
        align: left
        valign: middle
        border_color: field_blue
        editable: true

      - type: rect
        rect: [0, 79, 320, 1]
        color: row_line
        fill: true

      - type: label
        name: test_lbl
        text_key: field.test
        rect: [8, 82, 68, 35]
        color: white
        bg: label_coral
        size: 13

      - type: textbox
        name: test_field
        bind: record.test
        rect: [82, 80, 230, 39]
        color: black
        bg: white
        size: 18
        align: left
        valign: middle
        border_color: field_blue
        editable: true

      - type: rect
        rect: [0, 119, 320, 1]
        color: row_line
        fill: true

      - type: label
        name: collect_lbl
        text_key: field.collect
        rect: [8, 122, 68, 35]
        color: white
        bg: label_coral
        size: 13

      - type: textbox
        name: collect_field
        bind: record.collect
        rect: [82, 120, 230, 39]
        color: black
        bg: white
        size: 18
        align: left
        valign: middle
        border_color: field_blue
        editable: true
```

The worked example is longer than the Python equivalent. That's expected —
YAML is more explicit. The screen builder removes all repetition.

---

## 7. Open Questions

### Q1 — Pixel coordinate origin per zone

Proposed: `rect: [x, y, w, h]` uses the **zone's top-left** as origin (so
body widgets start at [0, 0] meaning "top-left of body", not [0, 50]).
The screen builder adds zone offsets automatically.

**Alternative:** Screen-absolute coordinates throughout (widgets know their
absolute position). Simpler in the builder; harder to author (must
manually add zone offsets when reading/editing YAML).

*Which do you prefer?*

### Q2 — Color format in palette

Proposed: RGB tuples `[r, g, b]`. The screen builder converts to RGB565.

**Alternative:** Hex strings `"#F05A46"` (more familiar to anyone who has
done web work). RGB tuples are more readable for numeric adjustment.

*Any preference?*

### Q3 — Strings: `text_key` vs. `text`

Proposed: use `text_key` for all user-visible strings from the start; a
minimal `locale/en.yaml` provides values. This costs almost nothing now
and avoids a full-file refactor when localization is added.

**Alternative:** Use inline `text:` now, switch to keys in Step 16.

*Preference? Or: use `text_key` only for strings that will definitely need
translation, `text` for things like "MENU" that are universal?*

### Q4 — Standard status bar: implicit or included?

Proposed: omitting `status_bar:` in a screen YAML generates the standard bar
automatically. Providing `status_bar:` overrides (used for the overdue footer
state and any non-standard screens).

**Alternative:** Every screen explicitly includes the status bar, either by
repeating it or via a YAML anchor (`*standard_status_bar`).

*Implicit default or explicit everywhere?*

### Q5 — Navigation targets: screen YAML or protocol YAML?

Proposed: navigation targets (`nav_next`, `nav_back`) live in the **protocol
YAML**, not the screen YAML. The NEXT button action in screen YAML is the
generic `action: nav_next`; the protocol tells the controller what that means
for each phase. This keeps screens reusable across protocols.

**Implication:** A screen YAML never knows what comes after it. The
controller wires the actual target at phase-transition time.

**Alternative:** Each screen hardcodes its next screen in its own YAML.
Simpler controller; screens are not reusable across different protocol orders.

*Reusable screens (protocol-driven nav) or simpler controller (screen-driven nav)?*

### Q6 — Parameterized / template screens

The reagent timer screen is used for each incubation phase but shows a
different label (Phase 1, Phase 2) and a different timer duration.

Proposed: a `params:` block in the protocol YAML provides values that
override specific widget fields in the referenced screen. The screen YAML
uses `{{ param_name }}` placeholders where protocol-specific values go.

This is a mini-template system (Jinja2-style substitution on YAML load).

**Alternative:** Separate YAML files for each phase instance
(`reagent_phase_1.yaml`, `reagent_phase_2.yaml`). More files; no
templating complexity. Fine if there are only 2 phases.

*How many reagent phases does the standard protocol have?*
*Parameterized template, or duplicate files?*

### Q7 — `source:` key namespace

The proposed source keys (`clock`, `slot.letter`, `phase.remaining_seconds`,
etc.) are freeform strings interpreted by the controller. There's no
schema validation — a typo is a runtime error, not a load-time error.

**Alternative:** Enumerate all valid source keys in a schema and validate
at load time. More upfront work; better error messages.

*Accept runtime errors for now, or define the schema?*

### Q8 — Flask template generation

The spec says Flask templates should be generated from the same YAML screen
definitions. The YAML format above is written for the touchscreen renderer.

The Flask equivalent of a `textbox` is an `<input type="text">`. The
equivalent of a `label` is a `<label>`. Positions (`rect:`) are irrelevant
for HTML — layout is CSS-driven.

**Implication:** The YAML can be used as the *data contract* (what fields
exist, what they're bound to, what text they show) but the Flask template
generator ignores `rect:` and translates widget types to HTML elements by
type name. This is valid structural parity even if the visual layout differs.

*Is this acceptable, or does "generated from the same YAML" mean something
stricter?*

### Q9 — Body sub-areas

The body zone currently has a flat list of widgets. Some screens may want
to divide the body into sub-zones (left panel / right panel, or a scrollable
list area + a fixed action area).

Proposed: allow `area:` as a widget type in the body, with its own `rect`
and `widgets` list. This maps to the existing `Area` widget class.

```yaml
- type: area
  name: left_panel
  rect: [0, 0, 160, 165]
  bg: panel_dark
  widgets:
    - ...
```

*Is this needed now, or add it when a screen requires it?*
