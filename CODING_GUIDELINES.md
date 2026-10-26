# Coding Guidelines

## Language and Environment

- **Language**: Python >= 3.10
- **Target platform**: Raspberry Pi Zero W / Zero 2 W
- **Development platform**: Desktop (Linux/macOS/Windows) with mock libraries
- **Test platform**: Desktop with mock_Picamera2 and other hardware mocks
- **Real hardware**: Only on RPi; desktop uses mock implementations
- **Deprecated libraries and methods**
  - Detect and warn when deprecated libraries or methods are found in the code.
  - Do not use deprecated libraries or methods without first warning and getting permission.

## Code Style

### General Principles

- Use clear, descriptive function and variable names
- Keep lines ≤ 80 characters (general guideline; use judgment)
- Avoid memory-heavy features (small Pi)
- Use try/except defensively around network and hardware code
- Use comments to explain hardware interactions, naming conventions, and complex algorithms
  - Use plain English and avoid jargon.  
  - U.S. spelling.
- Organize code into multiple files by domain:
  - Camera operation (`colorcam.py`)
  - Analysis (`color_analysis.py`, `roi_analysis.py`, `color_map.py`)
  - Web interface (`web_interface.py`)
  - Configuration management (`config_repo.py`)
  - Database operations (`db_manager.py`)
  - Testing (`test/` directory)

### Error Handling

- All hardware and network operations must be wrapped in try/except
- Database modifications must wrap queries in try/except with rollback on error
- Use specific exception types; avoid bare `except:` clauses
- Always close resources in `finally` blocks or use context managers

### Hardware Abstraction

- Use platform detection to select real vs. mock implementations
- Encapsulate hardware dependencies in dedicated modules (`colorcam.py`, `lighting.py`)
- Use interface contracts (import mock on fallback) to ensure consistent API

## Documentation Style

### Docstrings

Use triple double-quoted docstrings in **Google style** for all public functions and classes.
Use plain English and avoid jargon.  
U.S. spelling.

**Format**:
```python
def function_name(param1: Type1, param2: Type2) -> ReturnType:
    """
    One-line summary of what the function does.

    Longer description if needed. Explain the purpose, behavior,
    and any important side effects.

    Args:
        param1: Description of param1 and expected type/values
        param2: Description of param2 and expected type/values

    Returns:
        Description of return value and type

    Raises:
        ExceptionType: Description of when this exception is raised

    Example:
        >>> result = function_name(arg1, arg2)
        >>> print(result)
        expected_output
    """
```

**Example: Example from Project**
```python
def send_data(data: str, ip: str, port: int) -> None:
    """
    Sends a UTF-8 string via UDP to the specified IP and port.

    Args:
        data: The string to send. 0 <= len(data) <= 1024
        ip: Target IPv4 address (e.g., '192.168.0.1')
        port: Target port number (e.g., 1234)

    Returns:
        None

    Raises:
        OSError: If socket transmission fails

    Example:
        >>> send_data("HELLO", "192.168.1.1", 5000)
    """
```

### Docstring Guidelines

- Include examples in docstrings for public functions
- Use clear argument descriptions with types and ranges
- Document raised exceptions explicitly
- Keep summaries to one line; save details for description

### Markdown Files

- Use `##` (level 2) headers for major sections
- Use `###` (level 3) for subsections
- Use numbered lists for procedures; bullet lists for collections
- Code blocks should include language identifier (e.g., ` ```python`)
- Code blocks should include context where the code is run,
  by whom and how invoked.
  (e.g. `# user run on RPi command line in ssh session.`
   e.g. `# called by GUI ...`)
- Keep line length reasonable for readability
- Use plain English and avoid jargon.  
- U.S. spelling.

### Markdown File Organization

**Recommended structure for module documentation**:
```markdown
# Module Name

## Purpose

One-line summary; what does this module do?

## Public API

### function_name()
Description

## Implementation

Details about internal design

## Example Usage

Code example showing typical use
```

## GPIO and Hardware Library Guidelines

### Rule 1 — Always abstract hardware access

Never call a GPIO library directly from application or UI code.
Wrap every hardware resource in a class with a stable public interface.

**Do this:**
```python
class Backlight:
    def set_percent(self, pct: float) -> None:
        """Set backlight brightness 0.0–100.0."""
        self._pwm.value = pct / 100.0
```

**Not this:**
```python
# Application code calling the library directly — do not do this
from gpiozero import PWMOutputDevice
_bl = PWMOutputDevice(13)
_bl.value = 0.75
```

The abstraction class is the only place that imports a hardware library.
This makes the backend swappable and keeps application code portable.

### Rule 2 — Use the approved library for each purpose

| Purpose               | Use              | Do not use                        |
|-----------------------|------------------|-----------------------------------|
| General GPIO          | `gpiozero`       | `RPi.GPIO`, direct `/sys/class/gpio` |
| PWM outputs           | `gpiozero`       | `pigpio`, `RPi.GPIO` software PWM |
| Hardware PWM (buzzer) | `lgpio`          | `pigpio` / `pigpiod`              |
| SPI                   | `spidev`         | —                                 |
| I2C                   | `smbus2`         | —                                 |

### Rule 3 — No daemon dependencies

Do not introduce any library that requires a background daemon
(e.g. `pigpiod`). Install instructions must work with a plain
`pip install` or `apt install` into a standard Bookworm environment
with no pre-conditions beyond enabling SPI and I2C in `raspi-config`.

### Rule 4 — No tight Raspberry Pi-only APIs in shared modules

Modules under `colorimeter/` must not import anything that prevents the
code from being imported on a non-Pi host (e.g. a developer laptop
running tests). Use the abstraction layer to inject hardware; keep
business logic and data processing hardware-free.

Pattern for testability:
```python
class LEDBank:
    def __init__(self, backend=None):
        if backend is None:
            from .backends.gpiozero_backend import GpiozeroLEDBackend
            backend = GpiozeroLEDBackend()
        self._backend = backend

    def set_channel(self, channel: int, on: bool) -> None:
        self._backend.set_channel(channel, on)
```

In tests, pass a mock backend. On hardware, let it default.

### Rule 5 — PWM: start with gpiozero, escalate if testing shows need

`gpiozero.PWMOutputDevice` uses software PWM by default. For backlight
dimming this is sufficient. For the passive buzzer (audio frequencies),
hardware PWM via `lgpio` is required for clean tones. Do not assume
software PWM is acceptable for new PWM uses — test audible or
timing-sensitive outputs before shipping.

### Installation baseline (Bookworm)

```bash
sudo apt install -y python3-gpiozero python3-spidev python3-smbus2 python3-lgpio
```

No daemon setup steps. No `sudo systemctl enable` required.

## Configuration and Import Patterns

### Configuration Loading

- All configuration loaded at startup via `config_repo.py`, from the database's
  `config` table
- Configuration is read-only after initialization, except through `config_repo`'s
  own `save()` / `restore()` (Menu -> Config)
- No other module should read the `config` table directly

### Import Pattern for Hardware Abstraction

```python
# Preferred: try-except with fallback
try:
    from picamera2 import Picamera2
    PLATFORM = "rpi"
except ImportError:
    from mock_Picamera2 import Picamera2
    PLATFORM = "desktop"
```

## Versioning and Releases

### One version number, and it lives in `src/version.py`

`__version__` there is the only release string in the project. Nothing else
hardcodes one — not the seed SQL, not a test fixture, not a docstring.
`config_repo.stamp_version()` copies it into the instrument's `config` table at
every launch (`device.software_ver`, `editable=0`, `source='code'`), and from
there it reaches every capture archive. Because the config table keeps
superseded rows, an instrument carries a dated log of every version it has run.

Plain semantic versioning, `MAJOR.MINOR.PATCH`, no suffix. Being on 0.x *is* the
alpha statement (SemVer clause 4: anything may change at any time), so there is
no second counter to also keep moving.

| Bump | When |
|------|------|
| **PATCH** `0.3.0 → 0.3.1` | Fixes and internal changes. Archives and the database keep the same shape, so a desktop tool at 0.3.0 reads 0.3.1 archives unchanged. |
| **MINOR** `0.3.0 → 0.4.0` | Anything a stored archive or database would notice: a config parameter added, renamed or retyped; a capture-path or workflow change; **and always whenever a format version below moves**. |
| **MAJOR** `0.x → 1.0.0` | Archive format frozen, instrument calibrated and validated. |

### The release number is not the compatibility contract

> `software_ver` tells you which build wrote a file. It never tells you whether
> you can read it. Readers branch on the format versions, never on
> `software_ver`.

Three integers are the real contract. Each lives beside the code that writes it,
carries its own `# 1 -> 2: what changed` comment, and moves independently:

| Constant | Module | Stamped as | Governs |
|---|---|---|---|
| `SCHEMA_VERSION` | `capture_h5.py` | `.h5` attr `schema_version` | group/dataset layout |
| `META_VERSION` | `capture_meta.py` | `.h5` attr `meta_version` | shape of the `/meta` record |
| `SCHEMA_VERSION` | `db_bootstrap.py` | `PRAGMA user_version` | the SQLite schema |

`src/version.py` holds the other half — `READS_SCHEMA`, `READS_META`, `READS_DB`,
the ranges *this* checkout accepts. Widening a range is how a desktop change
restores compatibility with an older instrument's archives; narrowing one is how
support is dropped. `test/test_version.py` fails if a writer is bumped without
the matching range being widened.

Desktop tools call `version.check_archive(reader.attrs("/"))` immediately after
opening an archive, and abort on the message it returns. It returns a message
rather than raising so a caller with a real fallback — `devdesk/flatfield.py`
copes with schema-1 archives that carry no ROI geometry — can decide for itself.

### Release checklist

1. Bump `__version__` in `src/version.py`.
2. Add the CHANGELOG entry, including its `Writes:` / `Reads:` pair. If a format
   version moved, say which and why a reader should care.
3. Commit, then `git tag -a v0.3.0 -m "..."` and push the tag.
4. Sync the Pi. The instrument stamps the new version into its own database on
   its next launch — confirm with:
   ```sh
   sqlite3 src/colorimeter.sqlite \
     "SELECT value, add_dt FROM config
       WHERE role='device' AND name='software_ver' ORDER BY add_dt"
   ```

## Testing Standards

- Unit tests in `test/` directory
- Test data files in `mock_camera/` directory
- Mock implementations mirror real API but load from stored data
- Desktop-runnable tests use mock libraries
- Test data format: `.npy` for binary arrays, `.txt` for metadata, `.jpg` for images

## Performance Considerations

- Minimize memory usage (small Pi)
- Use generators for large data flows where possible
- Avoid loading entire images into memory when processing regions
- Close database connections immediately after use
- Don't load configuration repeatedly; cache via singleton

## Consistency Checklist

Before submitting code:
- [ ] Function has docstring (public functions)
- [ ] Lines ≤ 80 characters (general)
- [ ] Error handling around hardware/network code
- [ ] Clear variable and function names
- [ ] Comments explain WHY not WHAT (code explains what)
- [ ] Hardware code isolated (`colorcam.py`, `lighting.py`)
- [ ] Database operations have rollback on error
- [ ] Resources properly closed (files, connections, GPIO)
