"""
config_manager.py — the single reader of the files in config/.
---------------------------------------------------------------
No other module opens a configuration file. Everything that is tunable
without editing code is declared in config/instrument.yaml and reached
through the accessors here, so there is one place to look when asking
"where is that configured?".

Settings are read once and cached; call load(reload=True) after editing a
file in a long-running process.

If the YAML file is missing or unreadable the built-in defaults in
_DEFAULTS apply and a warning is logged, so a capture on the instrument
never fails merely because a config file was mistyped.

Usage:
    import config_manager as config

    suite_id = config.capture_suite_id()             # "20260719_1343"
    path     = config.capture_dir() / config.capture_archive_name(suite_id)
"""

import copy
import logging
import os
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:                     # pragma: no cover - packaging issue
    yaml = None

log = logging.getLogger(__name__)

# The project root is the directory holding src/ and config/.
PROJECT_ROOT    = Path(__file__).resolve().parent.parent
CONFIG_DIR      = PROJECT_ROOT / "config"
INSTRUMENT_PATH = CONFIG_DIR / "instrument.yaml"

# Overrides config capture.directory for one process, for tests and ad-hoc runs.
CAPTURE_DIR_ENV = "COLORIMETER_CAPTURE_DIR"

# Used verbatim when instrument.yaml is missing a key, or cannot be read.
_DEFAULTS = {
    "capture": {
        "directory":        "captures",
        "timestamp_format": "%Y%m%d_%H%M",
        "extension":        ".h5",
    },
}

_cache: "dict | None" = None


# ── Loading ──────────────────────────────────────────────────────────

def load(reload: bool = False) -> dict:
    """
    Return the merged configuration, reading instrument.yaml on first use.

    Args:
        reload: Re-read the file instead of returning the cached settings.

    Returns:
        The configuration dict. Defaults fill in any section or key the
        file does not define.
    """
    global _cache
    if _cache is not None and not reload:
        return _cache

    settings = copy.deepcopy(_DEFAULTS)
    loaded = _read_instrument_yaml()
    for section, values in loaded.items():
        if isinstance(values, dict) and isinstance(settings.get(section), dict):
            settings[section].update(values)
        else:
            settings[section] = values

    _cache = settings
    return _cache


def _read_instrument_yaml() -> dict:
    """Read instrument.yaml, returning {} if it is absent or unreadable."""
    if yaml is None:
        log.warning("PyYAML not installed — using built-in configuration defaults")
        return {}
    try:
        with open(INSTRUMENT_PATH, "r") as handle:
            return yaml.safe_load(handle) or {}
    except FileNotFoundError:
        log.warning("No %s — using built-in configuration defaults", INSTRUMENT_PATH)
        return {}
    except (OSError, yaml.YAMLError):
        log.exception("Could not read %s — using built-in configuration defaults",
                      INSTRUMENT_PATH)
        return {}


def get(section: str, key: str):
    """Return one configuration value, falling back to the built-in default."""
    settings = load()
    if section in settings and key in settings[section]:
        return settings[section][key]
    return _DEFAULTS.get(section, {}).get(key)


# ── Capture output ───────────────────────────────────────────────────
# Everything about where an archive is written and what it is called.

def capture_dir() -> Path:
    """
    Return the directory capture archives are written to.

    $COLORIMETER_CAPTURE_DIR wins if set. Otherwise capture.directory from
    instrument.yaml, resolved against the project root when relative.
    """
    from_env = os.environ.get(CAPTURE_DIR_ENV)
    if from_env:
        return Path(from_env).expanduser()

    configured = Path(str(get("capture", "directory"))).expanduser()
    if configured.is_absolute():
        return configured
    return PROJECT_ROOT / configured


def capture_suite_id(when: "datetime | None" = None) -> str:
    """
    Return the identifier naming one capture set, e.g. "20260719_1343".

    Formatted with capture.timestamp_format. Also stored as the archive's
    suite_id attribute.

    Args:
        when: Timestamp to format; defaults to now.
    """
    when = when or datetime.now()
    return when.strftime(str(get("capture", "timestamp_format")))


def capture_archive_name(suite_id: str) -> str:
    """Return the archive filename for a suite id, e.g. "20260719_1343.h5"."""
    return f"{suite_id}{get('capture', 'extension')}"
