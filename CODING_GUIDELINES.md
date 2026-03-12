# Coding Guidelines

## Language and Environment

- **Language**: Python >= 3.10
- **Target platform**: Raspberry Pi Zero W / Zero 2 W
- **Development platform**: Desktop (Linux/macOS/Windows) with mock libraries
- **Test platform**: Desktop with mock_Picamera2 and other hardware mocks
- **Real hardware**: Only on RPi; desktop uses mock implementations

## Code Style

### General Principles

- Use clear, descriptive function and variable names
- Keep lines ≤ 80 characters (general guideline; use judgment)
- Avoid memory-heavy features (small Pi)
- Use try/except defensively around network and hardware code
- Use comments to explain hardware interactions, naming conventions, and complex algorithms
- Organize code into multiple files by domain:
  - Camera operation (`colorcam.py`)
  - Analysis (`color_analysis.py`, `roi_analysis.py`, `color_map.py`)
  - Web interface (`web_interface.py`)
  - Configuration management (`config_manager.py`)
  - Database operations (`db_interface.py`)
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
- Keep line length reasonable for readability

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

## Configuration and Import Patterns

### Configuration Loading

- All configuration loaded at startup via `config_manager.py`
- Configuration is read-only after initialization
- Writing configuration changes uses `db_interface.py`
- No module should read configuration files directly

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
