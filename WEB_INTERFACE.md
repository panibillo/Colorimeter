# Web Interface — Colorimeter

## Overview

A Flask web app runs on the Raspberry Pi Zero W / Zero 2 W and serves a
control page accessible from any browser on the LAN.  The server **owns**
the camera and LED objects for the lifetime of the process — they are
initialized at startup and released on shutdown.

The prototype app (`dev/webtest.py`) exercises all hardware paths and
establishes the page layout.  It will serve as the template for later
purpose-built apps (calibration, end-user measurement).

> **Status (2026-07):** `dev/webtest.py` and its `dev/templates/` exist and are
> the current prototype. This doc describes that prototype; several module paths
> below drifted and are corrected inline. Two things to keep straight:
> 1. **Two different LED abstractions.** The prototype uses a GPIO-pin
>    `LedController` from `dev/lighting_lightboard1.py`. The *application's*
>    capture pipeline (`capture_runner`/`capture_session`) uses a shift-register
>    `LEDBank` from `src/lighting_cal8.py`. These are not the same class; a
>    consolidation into one `src/lighting.py` is still pending.
> 2. **Two capture paths.** The prototype captures single frames via
>    `BayerCam.save_frame()` (`.jpg` + `.npy` + `.txt` sidecars). The
>    application's suite capture (`capture_runner`) instead streams one **HDF5**
>    archive per suite (`capture_h5.py`). The metadata fields below come from the
>    prototype's `.txt` sidecar, not the HDF5 pipeline.
>
> A production web UI is expected to be a Flask *frontend of the phase/slot
> model* (swap `layout.py` + the pool builder, keep phases/slots — see
> [ARCHITECTURE.md](ARCHITECTURE.md)), not a straight extension of this prototype.

---

## Framework

**Flask** (built-in dev server, single worker, `threaded=False`).

- Resident memory at idle: ~8 MB — acceptable on 512 MB Pi Zero 2 W
- Single-threaded server eliminates the need for locks around camera/GPIO
- `app.run(host="0.0.0.0", port=5000)` makes the page reachable at
  `http://<pi-hostname-or-ip>:5000` from the desktop browser
- No nginx, gunicorn, or reverse proxy needed for LAN use

---

## Startup Sequence

1. Import `LedController` from `dev/lighting_lightboard1.py` (not `src/lighting.py`,
   which does not exist; the app pipeline uses `LEDBank` in `src/lighting_cal8.py`)
2. Import `BayerCam` (or mock) from `src/bayer_cam.py`
3. Instantiate both at module level — they persist for the server lifetime
4. Register Flask routes
5. `app.run(host="0.0.0.0", port=5000, threaded=False)`

On shutdown, release camera and call `leds.close()`.  Because the script
runs in an SSH terminal during development, all of the following must be
handled — SSH adds SIGHUP which most scripts miss:

| Signal | Cause | Handler action |
|--------|-------|----------------|
| SIGINT | Ctrl-C in terminal | cleanup, exit |
| SIGTSTP | Ctrl-Z suspend | cleanup, re-raise to actually suspend |
| SIGTERM | `kill` / systemd stop | cleanup, exit |
| SIGHUP | SSH disconnect or terminal close | cleanup, exit |
| `atexit` | normal exit or uncaught exception | last-resort cleanup |

SIGHUP is critical for SSH use: if the SSH session drops, the shell sends
SIGHUP to the foreground process.  Without handling it the GPIO pins
remain claimed and the next run hits "GPIO busy".

`atexit` is registered as a safety net — it fires even when a signal
handler calls `sys.exit()`, so GPIO is released on any exit path.

```python
import atexit, os, signal, sys

def _cleanup():
    leds.all_off()
    leds.close()
    if cam is not None:
        cam.stop()

def _exit_handler(*_):
    _cleanup()
    sys.exit(0)

def _sigtstp_handler(*_):
    _cleanup()
    signal.signal(signal.SIGTSTP, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGTSTP)   # re-raise to suspend

atexit.register(_cleanup)
signal.signal(signal.SIGINT,  _exit_handler)
signal.signal(signal.SIGTERM, _exit_handler)
signal.signal(signal.SIGHUP,  _exit_handler)
signal.signal(signal.SIGTSTP, _sigtstp_handler)
```

## Desktop Mock

`webtest.py` runs on the desktop without modification — same
`try/except ImportError` platform detection used by `dev/camtest5.py`:

- **Camera**: `mock.picamera2_mock` (in `src/mock/`) is loaded automatically;
  point its mock source at a real captured file
- **GPIO**: `LedController` stubs all calls when gpiozero is absent
- **Flask**: runs identically on desktop; page is reachable at
  `http://localhost:5000`

No separate launcher needed — `python dev/webtest.py` works on both
platforms.  `test/` is reserved for automated unit tests; interactive
development tools belong in `dev/`.

---

## Page Structure

Single-page layout.  Three collapsible sections, each toggled by a button
at the top.  Only one section is expanded at a time.

```
[ Camera ]   [ Lights ]   [ Settings ]
─────────────────────────────────────
< active section content >
─────────────────────────────────────
Status bar:  last action / error message
```

---

## Routes

| Method | URL | Purpose |
|--------|-----|---------|
| GET | `/` | Serve the main page |
| POST | `/capture` | Trigger capture; blocks until done; returns updated page |
| POST | `/leds` | Set LED state; returns updated Lights section |
| GET | `/status` | JSON — current state, last result summary |
| POST | `/settings` | Update settings; returns updated Settings section |

All POST routes return a full page reload (simple redirect-after-POST)
for the prototype.  Ajax partials can be added later if needed.

---

## Camera Section

Controls and state for image capture.

### Controls

- **File type**: radio or checkbox — JPEG / Bayer (.npy) / Both
- **Collect one image**: captures with current exposure settings
- **Collect suite**: cycles through all lighting combinations
  (delegates to the lightboard sequence from `lighting.py`)
- **Exposure table**: one row per LED color

  | Color | Mode | Exposure (ms) |
  |-------|------|---------------|
  | White | Manual \| Saved \| Auto | _editable_ |
  | Blue  | Manual \| Saved \| Auto | _editable_ |
  | …     | … | … |

  - **Manual**: use the value typed in the box. 
  - **Saved**: load from `cam5exp.csv`
  - **Auto**: run convergence loop at capture time
- **Target rect**: 1 or more rows.  Dimensons in pixels.  Default is image size.
  (A target rect is an area over which image statistics are computed.
   Each target rect has a name.  In future multiple rects may be defined.)

  | Name | Left  | Top | Width | Height |
  |------|-------|-----|-------|--------|
  | Name \| Left \| Top \| Width \| Height | _editable_ |

### Result Display

After a successful capture, selected fields from the `.txt` metadata file
are shown below the controls.  
Compute Target rect stats from the Target rect array range:

- Filename stem
- Requested exposure (ms)
- Actual exposure reported by camera (ms)
- Frame duration (µs)
- Capture timestamp
- Bayer channel means: B / GB / GR / R
- Bayer channel std devs: B / GB / GR / R
- Target rect(s): Name, Bayer channel means : B / GB / GR / R
- Target rect(s): Name, Bayer channel std devs : B / GB / GR / R

Fields are read by parsing the `key: value` lines in the `.txt` sidecar written
by `BayerCam.save_frame()` (the prototype's single-frame path). The application's
suite capture does not write these sidecars — it stores per-frame metadata inside
the HDF5 archive (`capture_h5.py`).

---

## Lights Section

Manual LED control for setup and diagnostics.

- One button per channel: **V  B  G  R  W**
- Buttons are visually distinct when ON vs OFF (CSS class toggle)
- Clicking a lit button turns it off; clicking an unlit button turns it on
  and leaves others as-is (additive, unlike the capture sequence)
- **All Off** button always present

State is reflected in the page on every reload.

---

## Settings Section

- **Output directory**: path where captures are saved (default `./captures`)
- **Auto-upload** *(bookmarked — not implemented in prototype)*:
  After each capture, `scp` or `rsync` the new files to a desktop path.
  Will need: desktop IP/hostname, destination path, SSH key configured.
  Placeholder toggle in the UI; greyed out until implemented.

---

## Implementation Notes

### Shared State

A small module-level dict holds mutable state between requests:

```python
state = {
    "led_state":      {},        # color → True/False
    "last_result":    None,      # parsed metadata dict from last capture
    "last_error":     None,      # error string or None
    "output_dir":     "captures",
    "file_type":      "both",    # "jpeg" | "bayer" | "both"
    "exposures":      {},        # color → {"mode": "manual"|"saved"|"auto", "ms": float}
}
```

### HTML Templates

Jinja2 templates (Flask's default) in a `templates/` subdirectory next to
`webtest.py`.  A single `index.html` with `{% if %}` blocks for the active
section.  No JavaScript framework required — plain HTML forms and a small
amount of inline CSS.

### Error Handling

Hardware errors (camera failure, GPIO error) are caught in the route
handler, written to `state["last_error"]`, and shown in the status bar.
The page remains functional for LED control even if the camera fails.

---

## Plots Section

Graphs are generated server-side with **matplotlib (Agg backend)** and
served as PNG images embedded in the page.

### Why matplotlib

- Arrays from the analysis code feed directly into matplotlib — no format
  conversion
- `savefig()` to a `BytesIO` buffer avoids temp files entirely
- Agg backend loads no GUI toolkit; safe on a headless Pi
- Peak RAM during `savefig()`: ~35–60 MB, returned after `plt.close(fig)`
- If memory pressure becomes critical later, routes can be swapped for
  Chart.js JSON endpoints with no HTML changes

### Usage rules on Pi Zero

```python
import matplotlib
matplotlib.use('Agg')          # must be set before importing pyplot
import matplotlib.pyplot as plt
```

- Import lazily (inside the route function) so memory is only consumed
  when a plot is actually requested
- Always call `plt.close(fig)` after saving — figures accumulate in
  memory otherwise
- Use `dpi=90` and `figsize=(6, 3)` or similar — keeps PNG size small
  for LAN transfer (~30–60 KB)

### Serving a plot

```python
from io import BytesIO

@app.route('/plot/<name>')
def serve_plot(name):
    fig, ax = plt.subplots(figsize=(6, 3))
    # ... populate ax from state or metadata ...
    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=90, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf.read(), 200, {'Content-Type': 'image/png'}
```

In the HTML template: `<img src="/plot/bayer_means">` — fetched as a
separate browser request after the page loads.

### Planned plots

| Name | X axis | Y series | When shown |
|------|--------|----------|------------|
| `bayer_means` | Capture index or color step | B, G, R channel means | After suite capture |
| `bayer_hist` | Pixel value (0–1023) | Count per channel | After single capture |
| `calibration_curve` | Known concentration | Measured color value | Calibration app |

---

## File Layout

```
dev/
  webtest.py          Flask app entry point
  templates/
    index.html        Single page template
  captures/           Default output directory (created on first capture)
dev/
  lighting_lightboard1.py   LedController, LED_PINS, LED_ORDER (prototype LEDs)
src/
  bayer_cam.py        BayerCam (real; mock via src/mock/picamera2_mock.py)
  lighting_cal8.py    LEDBank — the application pipeline's LED control
  mock/               platform mocks (picamera2_mock, gpiozero_mock, …)
```

---

## Future

- Auto-upload captured files to desktop via `scp` / `rsync` after capture
- Ajax partial updates (avoid full page reload on LED toggle)
- A production web UI as a **Flask frontend of the phase/slot model** (reuse
  `phases.py` + `slot.py`; provide a web `layout` + pool builder in place of
  `layout.py`), rather than extending this hardware-test prototype
- Consolidate the two LED classes (`dev/lighting_lightboard1.LedController` and
  `src/lighting_cal8.LEDBank`) into one `src/lighting.py`
- Authentication (simple shared secret) if exposed beyond LAN
