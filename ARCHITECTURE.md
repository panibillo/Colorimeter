# Architecture

## Initialization Sequence

- Power on event
    - systemd launches main.py --ili9341

- Commandline launch:  
    - python -m main

- Detect platform and configure mock vs. real hardware
- Initialize logging (`logging_manager.py`)
- Load configuration from `config_manager.py`
- Branch 1: (running on desktop: test environment)
    - initialize mock_GPIO
    - import ui_terminal as ui
    - initialize mock_camera
    - call mainloop(ui)

- Branch 2: (running on Rpi from remote terminal)
    - Initialize GPIO pins (for physical_interface or lighting)
    - import ui_terminal as ui
    - Initialize camera (`colorcam.py`)
    - call mainloop(ui)


- Branch 3: (running on Rpi with physical interface)
    (Adafruit ILI9341 2.8" Color TFT Touchscreen Breakout v2)
    - Initialize GPIO pins (for physical_interface or lighting)
    - import ui_ILI9341 as ui
    - Initialize camera (`colorcam.py`)
    - call mainloop(ui)

- mainloop    
    - Connect to database (lightweight, on-demand per interaction)
    - while True: try:
      - ui.read_input()      : read_touch() / input()
      - update state machine
      - dispatch()
      - ui.update()          : redraw screen / print()
 
      
      
3.    
    
7. Start main event loop

## Program Flow

**Launch and Initialize**
- User opens terminal and launches program (during dev)
- Final product auto-launches and auto-shuts down

**Main Event Loop (non-blocking)**
- Monitor all interfaces for user input (terminal, physical, web)
- Update interface states and displays
- Dispatch to appropriate workflow (capture, calibration, config, reporting, etc.)
- Return to loop when task completes, closing any open connections
- Exit cleanly on shutdown signal

## Module Dependencies and Data Flow

```
Main data flow:
  reading → colorcam → color_analysis → [repeat] → color_map → report → interface_manager

Config layer (read-only, singleton):
  config_manager.py → {colorcam, color_analysis, color_map, lighting, roi_analysis}

Database layer (on-demand connection):
  db_interface.py → {config_manager, color_map, report, interface_manager, color_analysis, roi_analysis}

Error and state handling:
  error_handler.py ← called by any module on exception
  state_machine.py ← main.py tracks workflow states

Logging (available to all modules):
  logging_manager.py ← all modules log errors, warnings, debug info
```

## Module Organization

### Core Modules

**main.py**
- Initialize program
- Run main event loop
- Orchestrate high-level workflow state machine
- Handle graceful shutdown

**config_manager.py**
- Load and cache configuration at startup (singleton)
- Provide read-only config API to all modules
- No direct file I/O; only config_manager reads files
- Config writing handled through `db_interface.py`
- Schema: camera, lighting, calibration, db, interface settings

**interface_manager.py**
- High-level abstract interface dispatcher
- Route user input from:
  - `terminal_interface.py`: stdin/stdout
  - `physical_interface.py`: GPIO buttons, LED & beep indicators, character display (TODO)
  - `web_interface.py`: Flask/lightweight HTTP (TODO)
- Provide unified interface for all modules to display results

### Hardware/Imaging Modules

**lighting.py**
- Turn image illumination on/off via GPIO pins
- Send commands to specific LEDs (blue, green, white)
- Monitor for GPIO failures (TODO)

**colorcam.py**
- Abstract camera interface (isolates hardware-specific calls)
- Platform detection: select real or mock implementation
  - Real: `import picamera2` (try-except with fallback)
  - Mock: `import mock_Picamera2` on desktop
- Configure camera based on `config_manager` settings
- Capture raw Bayer images (BGGR, minimal color correction)
- Save and/or return images to caller

### Analysis Modules

**color_analysis.py**
- Accept image(s) from memory or file paths
- Read config from `config_manager`
- Analyze color values in target ROI
- Analyze calibration card color patches
- Return analyzed color values and statistics

**roi_analysis.py**
- Define and locate ROI (regions of interest) on calibration card
- Read ROI definitions from `config_manager`
- Accept raw image; confirm or auto-detect ROI boundaries
- Save refined ROI definitions via `db_interface` for future use

**color_map.py**
- Manage mapping from analyzed color values → concentration values
- Store and retrieve calibration curves from `db_interface`
- Interpolate concentration from measured color
- Support different chemical test strip types

### Data and Workflow Modules

**db_interface.py**
- Lightweight direct SQL wrapper (no ORM)
- Dedicated wrapper functions for each query (SQL statements encapsulated)
- **Lifecycle**: Create connection on-demand, close when task completes
- **Error handling**: All modifications wrapped in try-except, rollback on error
- Schema: 
    - colorcard : describes colors and geometry of color calibration cards
    - cameraconfig : camera parameters
    - meterconfig : hardware and software parameters
    - data : readings and metadata

- No connection pooling; one connection per interaction

**report.py**
- Format results for display (concentration, stats, metadata)
- Dispatch results to `interface_manager`
- Store results via `db_interface` (include timestamp, image refs, calibration used, etc.)

**state_machine.py**
- Track workflow states: idle, capturing, analyzing, reporting, configuring, calibrating
- Validate state transitions
- Enforce valid transitions (e.g., cannot analyze before capture)

### Workflow Modules

**calibration_workflow.py**
- High-level orchestration for calibration process
- Prompt user for calibration inputs (known concentrations, test strip types)
- Orchestrate image capture and color analysis
- Store results via `db_interface`

### Support Modules

**error_handler.py**
- Catch and log exceptions from all modules
- Provide recovery/retry strategies (e.g., retry image capture)
- Notify user via `interface_manager` on recoverable errors
- Exit gracefully on fatal errors
- Close open resources (camera, GPIO, db) on shutdown

**logging_manager.py**
- Unified logging for all modules
- Log levels: DEBUG, INFO, WARNING, ERROR
- Destinations: console, file (configurable)
- Include timestamp and module name in output

**utils.py**
- Shared utilities: file I/O helpers, math functions, conversion functions
- **No direct hardware or database access**

## Platform Abstraction Strategy

### Preferred: Platform Detection at Startup

Detection approach:
- At startup, detect platform (RPi vs. desktop) via `try-except` or `sys.platform` check
- Branch imports in `colorcam.py` and `lighting.py` accordingly
- Example:
  ```python
  try:
      import picamera2
      PLATFORM = "rpi"
  except ImportError:
      import mock_Picamera2 as picamera2
      PLATFORM = "desktop"
  ```
- For GPIO: detect RPi GPIO library availability

### Alternative: Distinctive Module Naming

- **Real modules**: `picamera2`, `RPi.GPIO` (available only on RPi)
- **Mock modules**: `mock_Picamera2`, `mock_gpio` (available only on desktop)
- Import via try-except; program auto-selects available implementation

## Database Connection Lifecycle

**Key principle**: No persistent connections; connections are request-scoped.

- **At startup**: Do NOT open persistent connection
- **User initiates task** (e.g., "save reading", "retrieve calibration"):
  1. `db_interface` creates connection
  2. Execute query(es) in transaction
  3. On success: commit and close connection
  4. On error: rollback, close connection, re-raise to `error_handler`
- **Return to main loop**: No open connection
- **Advantages**: Minimal resource usage, clean state between interactions, no stale connections
