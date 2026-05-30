# Lab Colorimetric Strip Analyzer — Software Specification
## Version 0.2 Draft  |  Status: For Review

---

## 1. Purpose and Scope

This document specifies the software architecture, interface design, and operational behavior of a colorimetric test-strip analyzer instrument. The instrument captures and analyzes photographs of chemical test strips under controlled LED illumination, records results and metadata, and guides a laboratory user through timed processing phases.

This specification is intentionally high-level. It establishes architecture, module boundaries, data contracts, and behavioral requirements without pre-determining implementation details within each module.

---

## 2. Hardware Platform

| Component | Specification |
|---|---|
| Compute | Raspberry Pi Zero W or Zero W 2 |
| Display | 240 × 330 px resistive touchscreen, landscape orientation |
| Camera | Pi Camera Module 2 or 3 |
| Temperature sensors | Up to 5 × DS18B20 (1-wire bus); component IDs recorded |
| Illumination | 1–10 LED colors, sequentially addressable |
| Input | USB keyboard (primary navigation fallback; primary data entry) |
| Physical controls | Optional: a small number of pushbuttons, status LEDs, piezo beeper |
| Network | Wi-Fi (LAN only) |
| Optional future output | HDMI monitor (architecture must not preclude this) |

**Resource constraints:** The RPi Zero W has ~512 MB RAM shared with the GPU. All software design decisions must treat memory and CPU as scarce. Profile before optimizing; prefer lazy loading and minimal dependencies.

---

## 3. Instrument Function

The instrument performs the following sequence, which may run concurrently for up to 4 independent samples (labeled A, B, C, D):

1. Accept sample and metadata input from the user.
2. Record sample temperature via DS18B20.
3. Guide the user through one or more timed reagent phases (phase durations typically 2-10 minutes; duration may be temperature dependent - a function to be determined; precision requirements on the order of 10-120 seconds).
4. At each designated measurement phase, illuminate the sample with each LED color sequentially and capture one photograph per color.
5. Analyze photographs: extract Bayer layer sub-regions, compute color channel statistics.
6. Display results; store results and metadata to persistent storage.

**Shared physical resources:** Camera + LED array, and 1-wire sensor bus are shared across all active tests. Only one test at a time may use the camera + LED array. Only one test at a time may use the external temperature sensor. A scheduling / resource-lock mechanism is required. Timing precision requirements are loose enough (10–120 s phases) that cooperative scheduling is sufficient; hard real-time is not required. Precise camera exposures are handled by the camera.

**Practical concurrency:** While up to 4 simultaneous tests are architecturally supported, 2 simultaneous tests is the expected working maximum for a single operator.

**Phase interleaving:** Each phase of each test has an allowable time window, and there are time windows in each phase that phase must have focus for user attention and action.  Simultaneous tests must be timed so that *focus windows* will not be scheduled concurrently.  Attention windows are defined in one of the config files (probably the screen YAML definitions because 'in focus' will apply to particular screens).  The test 'Start' button can only be active when the new test's focus windows will not conflict with currently running tests' focus windows. 

---

## 4. Data Model

### 4.1 Test Record

Each test record contains:

- **Identity:** Test ID (UUID), test label (A/B/C/D), 
- **Sample metadata:** Sample ID / name, collection timestamp, operator ID
- **Hardware identifiers:** instrument serial number, software version, DS18B20 sensor ID(s), LED ids, Reference color tile ids and config vars.
- **Environmental:** ambient temperature readings at each phase
- **Phase log:** Phase name, scheduled start, actual start, actual end, status (complete / incomplete / skipped)
- **Image records:** Per LED color per phase: image file path or embedded sub-array, exposure settings, camera model, capture timestamp, processing data.
- **Bayer metadata:** Sets of Bayer layer fragments (NumPy arrays), stored in accompanying __HDF5__ file -- one file per test. (Numpy .npy binary files are not archival.)
- **Analysis results:** Per LED color: mean, standard deviation, and other statistical measures per Bayer channel; final computed result value; result status (in-range / out-of-range / error)
- **Completion status:** complete / incomplete / error
- Accompanying __HDF5__ file could hold the entire test record with images.

### 4.2 Operational Log

A separate log stream records:

- All major lifecycle events (boot, test start, phase transitions, ¿user interactions?, test complete, shutdown)
- All warnings and errors (see Section 9)
- Periodic environmental snapshots (temperature, Wi-Fi status)
- Software version and configuration hash at boot

The operational log is stored separately from test results (different SQLite table or file) due to size and access pattern differences.

### 4.3 Persistence Requirements

- Results are written and committed to the db incrementally as they are generated.
- The system must be able to open and continue an incomplete test after a power loss.
- SQLite is the on-device persistent store. Write-ahead logging (WAL mode) should be enabled to reduce contention between concurrent readers and writers. __This needs study__
- Results are uploaded by UDP when a receiver address is configured.
- Whole photographs may be stored on-device or referenced by path; storage policy (embed vs. reference) is configurable.

### 4.4 Archival storage of results

- On board SQLite db stores all metadata from all tests, plus a reference to a __MDF5__ file for each test.
- Server side SQLite db mirrors the onboard db. 
- __MDF5__ 


---

## 5. Architecture

### 5.1 Process Model — Decision Guidance

**Recommendation: single Python process with threads.**

*Rationale:* On the RPi Zero W, spawning two processes doubles the Python interpreter overhead and complicates shared state. A single process with:
- A main thread owning the touchscreen renderer and the event loop
- A Flask thread (using Flask's built-in threaded server or Waitress)
- Worker threads for camera capture and LED control (one at a time via a shared lock)
- A background thread for SQLite writes and UDP upload

...is simpler to reason about, uses less RAM, and shares Python objects directly without IPC overhead.

*When to choose two processes instead:* If the touchscreen renderer proves to block the event loop (e.g., a slow image scaling operation), or if Flask instability risks crashing the display, a second process becomes justified. Design the shared-state mechanism (Section 5.3) in a way that supports migration to two processes later without rewriting interfaces.

*RPi Zero W 2 note:* The W 2 has a quad-core CPU, making threading genuinely parallel. The W is single-core; threading there is cooperative (GIL). Design accordingly — no thread should hold the GIL for more than ~50 ms.

### 5.2 Module Boundaries

```
┌─────────────────────────────────────────────────────────┐
│                    Application Core                     │
│                                                         │
│  ┌────────────────┐  ┌───────────┐  ┌────────────────┐  │
│  │ Analysis engine│  │Config &   │  │ Instrument I/O │  │
│  │ (tests A–B)    │  │data store │  │ (camera, LEDs, │  │
│  │                │  │(SQLite)   │  │  sensors)      │  │
│  └────────────────┘  └───────────┘  └────────────────┘  │
│                                                         │
│              Shared state bus (pub/sub)                 │
└──────────────┬──────────────────────┬───────────────────┘
               │                      │
  ┌────────────▼──────┐    ┌──────────▼───────────┐
  │ Touchscreen       │    │ Web interface        │
  │ interface         │    │ (Flask)              │
  │                   │◄──►│                      │
  │ Screen manager    │sync│ Routes / views       │
  │ Widget library    │    │ HTML templates       │
  └───────────────────┘    └──────────────────────┘
```

Each module exposes a defined interface. No module imports directly from an interface module; interfaces subscribe to state and call core APIs.

### 5.3 Shared State Mechanism

**Recommendation: PyDispatcher / pypubsub (in-process pub/sub), augmented by SQLite for durable state.**

- Transient UI state (which screen is active, current timer countdown) lives in pub/sub events.
- Durable state (test phase completion, results) is always written to SQLite first, then published as an event.
- This means any subscriber (touchscreen renderer, Flask view) can reconstruct current state by reading SQLite on startup — resilient to restarts.
- If migration to two processes is needed, replace the in-process pub/sub with a Unix domain socket or a named pipe carrying the same event schema. The SQLite layer remains unchanged.

**State awareness in on-device display screens when > 1 test is running**

- 1 test at a time holds the focus and owns most of the screen.
- Other tests have some kind of countdown indicator on the screen that gives the user awareness of the other upcoming tasks.

**State awareness in web screens when > 1 test is running**

- Each test has its own tab.
- Each tab displays its own timers
- Each tab also displays an awarness timer for other running tests.

**Input ownership rule:** 
- Last write wins. Each interface publishes its edits to the shared bus; the other interface updates on receipt. 
- User is responsible to use one interface at a time. 
  - ¿Possibly give user ability to lock out other interface?
- The currently displayed test/screen is controlled locally on each interface independently — the touchscreen displays whatever test the physical user has selected; the web page displays whatever the browser user has selected. There is no display lock across interfaces.

### 5.4 Screen and Widget Definition Format

Use **YAML** for screen and widget layout definitions.

*Rationale:* YAML is more Pythonic and less verbose than JSON, supports comments (useful in config files), and maps naturally to Python dicts and lists via PyYAML or ruamel.yaml. Flask's Jinja2 templates are separate from this — they render the web equivalent of the same logical screen.

Each screen definition specifies:
- Screen ID, title, task type (setup / information / analysis-phase)
- List of widget descriptors (type, position, size, data binding key, style properties)
- Enabled navigation actions (next, back, menu)
- Associated test slot (A/B/C/D or global)

The YAML definitions are the **canonical description** of each screen. Both the touchscreen renderer and the Flask template generator read from them. This ensures the two interfaces remain structurally synchronized even when their visual rendering differs.

### 5.5 HDMI Monitor Path

The touchscreen renderer must:
- Reference display dimensions from a config value, not hardcoded constants.
- Target a logical framebuffer object, not `/dev/fb0` directly.
- Accept a display backend as a dependency-injected parameter at startup.

This allows a future HDMI backend to be substituted with no changes to the rendering logic.

### 5.6 Data Upload 

- RPi Zero 2W (Colorimeter)
  - Bundles captured images and metadata into a single **HDF5 file** per upload session
  - POSTs the HDF5 file to the desktop receiver via HTTP multipart upload
  - On confirmed receipt (HTTP 200), updates its local **SQLite database** with metadata and a reference to the archived HDF5 file
  - Retains a local copy of all HDF5 files in a dated archive directory
  - Uploads run when HDF5s are ready, and run in the background at lower priority than running tests or user interactions.

-Desktop Receiver (`colorimeter_receiver.py`)
  - A small **Flask HTTP server** (port 5001) that runs on the desktop — any OS
  - Accepts HDF5 uploads at `POST /upload`; exposes `GET /status` for heartbeat checks
  - Unpacks each HDF5 file, extracts per-sample metadata, and upserts records into a local **SQLite database**
  - Unpacking and upserts are security consious.
  - Archives the HDF5 file into a `~/ColorimeterData/YYYY-MM/` directory
  - Requires only `pip install flask h5py` 
  — OS-level higher security configuration of port is optional

---

## 6. Interfaces

### 6.1 Touchscreen Interface

**Rendering:** A lightweight custom renderer written from scratch, extending the existing prototype rectangle/font/color module. No external UI framework.

**Orientation:** Landscape (330 px wide × 240 px tall in logical coordinates).

**Screen structure:** Every screen has three zones:
- **Header** (fixed height ~30 px): Sample ID, collection time, Test# identifier button (A/B/C/D) with distinct background color per test slot. Large letter identifier (A/B/C/D) in header for focus awareness.
- **Body** (variable): Screen-specific widget layout.
- **Status bar** (fixed height ~20 px): Left corner: Back/Menu button when applicable. Center: datetime (YY-MM-DD HH:MM:SS). Right corner: Next button when applicable.

**Test slot color coding:**

| Slot | Color |
|---|---|
| A | Blue |
| B | Gold / Amber |
| C | Green |
| D | Red / Coral |

These colors appear in the header background when that slot is in focus. They are used consistently across both interfaces.

**Navigation model:** The Test# button in the header toggles focus between active test slots. Toggling between slots is analogous to switching virtual desktops — each slot maintains independent screen state. A new test slot is initiated from the opening menu.

**Keyboard fallback:** Because resistive touch input is not fully reliable, the USB keyboard must be capable of driving all navigation and data entry:
- Tab / Shift-Tab: cycle widget focus; focused widget is visually highlighted.
- Enter: activate focused button.
- Up,Down  Arrow keys: TBD.
- Alphanumeric keys, Left-Right Arrows, Backspace, Delete: fill focused data entry fields.
- Backspace: delete character in data entry fields.

**Image display:** Full or cropped photographs are downsampled to screen resolution using OpenCV or picamera2 before display. The renderer treats images as pre-scaled pixel arrays.
- In some contexts, ROI rectangles may be drawn over the images, and labeled by rect_id.
- In some contexts, ROI rectangles may be edited (adjust sides while superimposed on image)


### 6.2 Widget Library (Touchscreen)

All widgets are composed from rectangles and text (single font family, multiple sizes and colors).

| Widget | Description |
|---|---|
| Rectangle | Colored border, optional fill; optional animated fill (progress indicator) |
| Text label | Static text; color; optional animated property |
| Button | Label + color; changes appearance on press; disabled state; may trigger beep on activation |
| Data entry / display box | Editable or read-only value field; activates numpad or keyboard on focus |
| Countdown / countup timer | Numeric display; configurable warning thresholds; audible alert when over-long |
| Status bar | Composite: back/menu left, datetime center, next right |
| Scrolling selector | 8–16 entries loaded from config; background color of selector or adjacent rect changes with selection value |
| Numeric keypad | 12-key overlay: 0–9, decimal point, backspace |
| Image viewer | Downsampled photo or sub-region display |

### 6.3 Screen Inventory (Touchscreen — ~12–20 screens)

*Preliminary; subject to revision when instrument workflow is fully specified.*

| # | Screen | Type |
|---|---|---|
| 1 | Splash / boot | Information |
| 2 | Opening menu | Setup |
| 3 | New test — sample entry | Setup |
| 4 | Temperature / sensor check | Setup / Information |
| 5 | Phase N — reagent timer (repeatable) | Analysis phase |
| 6 | Phase N — capture in progress | Analysis phase |
| 7 | Phase N — capture review | Information |
| 8 | Results summary | Information |
| 9 | Results detail (statistical measures) | Information |
| 10 | Results image viewer | Information |
| 11 | Past results browser | Information |
| 12 | Past result detail | Information |
| 13 | System status / diagnostics | Information |
| 14 | Configuration (limited on-device) | Setup |
| 15 | Error / warning overlay | Information |

### 6.4 Web Interface (Flask)

- Single-user, LAN-only access. Authentication: simple session token or HTTP Basic Auth over LAN (TBD).
- Each logical screen maps to one or more Flask routes. Screens that are sequential phases on the device may be presented as a single scrolling page or tabbed view on the web.
- Widget equivalents use standard HTML form elements and Bootstrap (or similar lightweight CSS framework) components wherever possible.
- Templates are generated from the same YAML screen definitions as the touchscreen renderer, via a Jinja2 template generator, ensuring structural parity.
- The web interface is the primary path for: sample metadata entry, configuration editing, past results review, and firmware/config upload via scp (out of band).
- WCAG 2.1 AA accessibility is the target for the web interface. Required: sufficient color contrast, keyboard navigability, screen-reader labels on all interactive elements, no information conveyed by color alone.

---

## 7. Configuration

- Configuration is stored in one or more YAML files on the device filesystem.
- Editable over SSH by a technical user at any time.
- A subset is editable via the web interface (user-facing parameters).
- On-device editing is minimal and TBD.
- Configuration scope includes:
  - Instrument serial number and calibration coefficients
  - DS18B20 sensor IDs and roles
  - LED color assignments and exposure parameters
  - Scrolling selector value lists (e.g., reagent volumes, time steps)
  - Temperature calibration range and warning thresholds
  - Timer warning levels (configurable beep cadence / persistence)
  - UDP upload receiver address (optional)
  - Storage policy (embed images vs. reference by path)
  - Display backend (touchscreen / HDMI) and resolution
  - Locale: date format, decimal separator, UI language

---

## 8. Localization

The software must support multiple languages. All user-visible strings are externalized to per-language resource files (YAML or GNU gettext `.po` format — TBD). The display and web interfaces load strings by key at render time. Date/time format and decimal separator are locale-configurable. RTL language support is desirable but may be deferred.

---

## 9. Error and Warning States

Errors and warnings are published on the shared state bus and rendered on both interfaces. Persistent warnings appear in the status bar. Critical errors may overlay the current screen.

| ID | Severity | Condition | Behavior |
|---|---|---|---|
| E-CAM-01 | Error | Camera not responding | Block capture phases; display error overlay |
| E-CAM-02 | Warning/Error | Camera exposure out of range | Flag measurement; log; display warning |
| E-CAM-03 | Warning/Error | Image component not as expected | Flag analysis result; log |
| E-SAMP-01 | Warning | Sample missing from image | Audible alert; prompt user to insert sample |
| E-SENS-01 | Warning | DS18B20 sensor not responding | Log; display warning; continue if non-critical |
| E-TEMP-01 | Warning | Temperature out of calibration range | Flag result; display warning |
| E-TIMER-01 | Warning | Phase timer over-long | Audible alert; escalating cadence per config |
| E-SCHED-01 | Warning | Resource conflict — wait to start | Display hold message; retry automatically |
| E-NET-01 | Warning | Cannot connect to Wi-Fi | Results written locally only; display warning |
| E-NET-02 | Warning | UDP upload error | Retry with backoff; log; display warning |
| E-BOOT-01 | Critical | Unable to boot | Status LED pattern (hardware fallback) |

---

## 10. Startup and Shutdown

**Boot sequence:**
1. Hardware self-check (camera ping, sensor enumeration, LED test).
2. Splash screen displayed during initialization.
3. Check SQLite database for incomplete tests.
4. Display opening menu. If incomplete tests exist, "Resume Tests" option is enabled.
5. Flask server starts in background thread.

**Shutdown:** Graceful shutdown commits any pending SQLite writes, closes camera, turns off LEDs, writes shutdown event to operational log.

**Power loss recovery:** On next boot, incomplete tests are detected and offered for resumption. Phases already completed are marked complete in the DB and skipped.

---

## 11. Simulation / Development Mode

All hardware-dependent modules have mock implementations:
- Mock camera: returns pre-captured test images.
- Mock DS18B20: returns configurable synthetic temperature values.
- Mock LED controller: logs commands; no hardware output.
- Mock touchscreen: renders using matplotlib with mouse-click input (for desktop development).
- Mock beeper: logs beep events.

Simulation mode is activated by a config flag or command-line argument. The mock modules conform to the same interface contracts as the real hardware modules, enabling full UI and logic testing without physical hardware.

---

## 12. Software Update

Software is updated by copying new files to the device over SSH/SCP. No over-the-air update mechanism is required. Version is recorded in the operational log at each boot. A helper script may be provided to restart services after update.

---

## 13. Open Questions

The following items require further decision before architecture is finalized:

| # | Question | Notes |
|---|---|---|
| OQ-1 | Flask authentication method | HTTP Basic Auth vs. session token vs. no auth (LAN-only trust) |
| OQ-2 | Image storage policy | **HDF5** each significant image or layer part |
| OQ-3 | Localization format | YAML resource files vs. GNU gettext `.po` |
| OQ-4 | Web CSS framework | Bootstrap vs. Pico CSS vs. custom (must be lightweight, offline-capable) |
| OQ-5 | Numpad overlay behavior | **Modal overlay** - not inline replacement of body area |
| OQ-6 | Maximum concurrent tests | **Hard cap at 2 for now**, or leave at 4 with scheduling warning? |
| OQ-7 | On-device config editing | **LAN router ip when booting**  Which parameters (if any) are editable on the touchscreen? |
| OQ-8 | RTL language support | **Design in from the start** or defer?|
| OQ-9 | Past results browser scope | How many records? Filter/sort options? Export? |

---

*End of specification v0.2*
