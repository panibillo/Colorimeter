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

Four layers are merged, each winning over the one before it:

    _DEFAULTS            built in, always complete
    instrument.yaml      what the instrument ships with
    simulation_overrides only while simulation is active
    user_settings.json   what the operator changed on the Menu -> Config
                         screen (save_user_setting / clear_user_setting)

The operator's on-screen edits come last so a value set on the instrument
holds whichever mode it is running in.

Usage:
    import config_manager as config

    suite_id = config.capture_suite_id()             # "20260719_1343"
    path     = config.capture_dir() / config.capture_archive_name(suite_id)
"""

import copy
import json
import logging
import os
import tempfile
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
# Settings the operator changed on the instrument itself (Menu -> Config).
# Written by save_user_setting(); absent until something is changed, and safe
# to delete at any time — doing so simply restores the instrument.yaml values.
USER_SETTINGS_PATH = CONFIG_DIR / "user_settings.json"

# Overrides config capture.directory for one process, for tests and ad-hoc runs.
CAPTURE_DIR_ENV = "COLORIMETER_CAPTURE_DIR"
# "1" → run against mock hardware and select the simulation_overrides profile.
# Set by run_simulation.py; it is the single switch the hardware selection and
# this module both key off, so the two never disagree.
SIMULATION_ENV = "COLORIMETER_SIMULATION"
# Overrides database.path for one process (tests, ad-hoc runs).
DB_PATH_ENV = "COLORIMETER_DB"

# Used verbatim when instrument.yaml is missing a key, or cannot be read, so the
# instrument still runs with sensible values when a config file is absent.
_DEFAULTS = {
    "capture": {
        "directory":        "captures",
        "timestamp_format": "%Y%m%d_%H%M",
        "extension":        ".h5",
        # Inclusive LED channel range a capture run sweeps (1..10 = every LED).
        "led_start":        1,
        "led_end":          10,
        # Exposure group preselected when the saved one is missing or has no
        # active rows left in the exposure table.
        "fallback_exposure_group": "3",
    },
    # Timed protocol steps. Durations are in seconds; the simulation profile
    # shortens the reagent waits so a run is quick to test (see below).
    "protocol": {
        "reagent1_secs": 120,
        "reagent2_secs": 420,
        "analysis_secs": 1.5,        # placeholder until the analysis engine lands
    },
    # Event-loop and screen timing.
    "runtime": {
        "main_loop_poll_s":   0.02,  # main-loop cadence
        "shutdown_timeout_s": 15.0,  # shutdown screen auto-picks Exit after this
    },
    # Which default_gui_vals row seeds the entry screens.
    "defaults": {
        "rowid": 1,
    },
    "hardware": {
        "backlight_pin": 13,
        "spi_device":    0,
        "i2c_bus":       1,
    },
    # Relative paths resolve against the project root; src/colorimeter.sqlite is
    # the historical location.
    "database": {
        "path": "src/colorimeter.sqlite",
    },
    # Persistent simulation default; the COLORIMETER_SIMULATION env var overrides
    # it. When active, the simulation_overrides profile is merged on top.
    "simulation": False,
}

_cache: "dict | None" = None
# The same settings without the user layer — what `configured()` reports.
_base_cache: "dict | None" = None


# ── Loading ──────────────────────────────────────────────────────────

def load(reload: bool = False) -> dict:
    """
    Return the merged configuration, reading instrument.yaml on first use.

    The built-in defaults are the base; instrument.yaml is merged on top key by
    key, so overriding one setting leaves its siblings intact. When simulation
    is active (see is_simulation) the file's simulation_overrides profile is
    merged next, and the operator's own user_settings.json last of all.

    Args:
        reload: Re-read the files instead of returning the cached settings.

    Returns:
        The configuration dict. Defaults fill in any section or key the
        files do not define.
    """
    global _cache, _base_cache
    if _cache is not None and not reload:
        return _cache

    loaded = _read_instrument_yaml()
    # simulation_overrides is a profile applied on top when simulation is
    # active, not a normal section — take it out before merging the rest.
    overrides = loaded.pop("simulation_overrides", None) or {}

    settings = copy.deepcopy(_DEFAULTS)
    _deep_merge(settings, loaded)
    if _simulation_from(settings):
        _deep_merge(settings, overrides)
    _base_cache = settings                        # the config files alone

    # Last word: what the operator changed on the instrument itself.
    merged = copy.deepcopy(settings)
    _deep_merge(merged, _read_user_settings())

    _cache = merged
    return _cache


def _deep_merge(base: dict, overlay: dict) -> None:
    """
    Recursively merge overlay into base, in place.

    A dict value is merged key by key so overriding one setting leaves its
    siblings intact; any other value (scalar, list) replaces base's value.
    """
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _simulation_from(settings: dict) -> bool:
    """
    Whether the simulation profile is active, given a merged settings dict.

    The COLORIMETER_SIMULATION env var is authoritative when set — it is what
    also selects mock hardware, so the two never disagree. Otherwise the
    top-level `simulation` flag from instrument.yaml decides.
    """
    env = os.environ.get(SIMULATION_ENV)
    if env is not None:
        return env == "1"
    return bool(settings.get("simulation", False))


def is_simulation() -> bool:
    """True when running with mock hardware / the simulation profile."""
    return _simulation_from(load())


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


# ── User settings (edited on the instrument) ─────────────────────────
# One small JSON file with the same section/key shape as instrument.yaml,
# holding only what the operator changed on the Menu -> Config screen.  It is
# kept separate from instrument.yaml so writing it can never reformat or drop
# the comments in the file the instrument ships with.

def _read_user_settings() -> dict:
    """Read user_settings.json, returning {} if it is absent or unreadable."""
    try:
        with open(USER_SETTINGS_PATH, "r") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.exception("Could not read %s — ignoring operator settings",
                      USER_SETTINGS_PATH)
        return {}
    return data if isinstance(data, dict) else {}


def _write_user_settings(data: dict) -> bool:
    """
    Replace user_settings.json with `data`, then reload the cache.

    Written to a temporary file in the same directory and renamed over the
    target, so an interrupted write cannot leave a half-written settings file
    behind.  Returns whether the write succeeded; a failure is logged and the
    in-memory settings are left untouched.
    """
    path = Path(USER_SETTINGS_PATH)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=path.name,
                                         suffix=".tmp", delete=False) as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            tmp_name = handle.name
        os.replace(tmp_name, path)
    except OSError:
        log.exception("Could not write %s — setting not saved", path)
        return False
    load(reload=True)
    return True


def user_settings() -> dict:
    """The operator's saved settings, as stored (a copy; may be empty)."""
    return copy.deepcopy(_read_user_settings())


def save_user_setting(section: str, key: str, value) -> bool:
    """
    Persist one operator-set value and apply it immediately.

    The value survives restarts and wins over both instrument.yaml and the
    simulation profile.  Returns whether it was saved.
    """
    data = _read_user_settings()
    data.setdefault(section, {})[key] = value
    if not _write_user_settings(data):
        return False
    log.info("config: saved user setting %s.%s = %r", section, key, value)
    return True


def clear_user_setting(section: str, key: str) -> bool:
    """
    Forget one operator-set value, restoring whatever the config files say.

    Returns whether the settings file is now free of it (True when there was
    nothing to clear).
    """
    data = _read_user_settings()
    if key not in data.get(section, {}):
        return True
    del data[section][key]
    if not data[section]:
        del data[section]
    if not _write_user_settings(data):
        return False
    log.info("config: cleared user setting %s.%s", section, key)
    return True


def get(section: str, key: str):
    """Return one configuration value, falling back to the built-in default."""
    return _lookup(load(), section, key)


def configured(section: str, key: str):
    """
    Return one value as the config files alone define it — what `get` would
    return had the operator never changed it on the Config screen.  The Config
    screen's Restore offers these values.
    """
    load()
    return _lookup(_base_cache or {}, section, key)


def _lookup(settings: dict, section: str, key: str):
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


# ── Capture selection ────────────────────────────────────────────────
# Which LEDs a run sweeps, and the exposure group used when none is saved.

def capture_led_start() -> int:
    """First (inclusive) LED channel a capture run sweeps."""
    return int(get("capture", "led_start"))


def capture_led_end() -> int:
    """Last (inclusive) LED channel a capture run sweeps."""
    return int(get("capture", "led_end"))


def fallback_exposure_group() -> str:
    """Exposure group to preselect when the saved one is missing or inactive."""
    return str(get("capture", "fallback_exposure_group"))


# ── Protocol timing ──────────────────────────────────────────────────
# Durations of the timed steps, in seconds. The simulation profile shortens
# the reagent waits, and the Menu -> Config screen overrides both (saved
# through save_user_setting, so a custom wait holds on real hardware too).

def reagent1_secs() -> int:
    """Countdown for reagent step 1, in seconds."""
    return int(get("protocol", "reagent1_secs"))


def reagent2_secs() -> int:
    """Countdown for reagent step 2, in seconds."""
    return int(get("protocol", "reagent2_secs"))


def analysis_secs() -> float:
    """Placeholder analysis duration, in seconds (until the engine lands)."""
    return float(get("protocol", "analysis_secs"))


# ── Runtime timing ───────────────────────────────────────────────────

def main_loop_poll_s() -> float:
    """Main event-loop cadence, in seconds."""
    return float(get("runtime", "main_loop_poll_s"))


def shutdown_timeout_s() -> float:
    """Seconds before the shutdown screen auto-selects Exit."""
    return float(get("runtime", "shutdown_timeout_s"))


# ── Reference data ───────────────────────────────────────────────────

def default_rowid() -> int:
    """The default_gui_vals rowid that seeds the entry screens."""
    return int(get("defaults", "rowid"))


# ── Hardware ─────────────────────────────────────────────────────────

def backlight_pin() -> int:
    """GPIO pin driving the display backlight."""
    return int(get("hardware", "backlight_pin"))


# ── Database ─────────────────────────────────────────────────────────

def db_path() -> Path:
    """
    Return the SQLite database path.

    $COLORIMETER_DB wins if set. Otherwise database.path from instrument.yaml,
    resolved against the project root when relative.
    """
    from_env = os.environ.get(DB_PATH_ENV)
    if from_env:
        return Path(from_env).expanduser()

    configured = Path(str(get("database", "path"))).expanduser()
    if configured.is_absolute():
        return configured
    return PROJECT_ROOT / configured
