"""
logging_manager.py — Simple logging to console and file.

Provides debug(), info(), warning(), error() functions for all modules.
Each message includes a timestamp and optional caller module name.

Usage:
    import logging_manager as log
    log.info("Camera initialized")
    log.error(f"Failed to open config: {e}")
"""

import sys
from datetime import datetime

# Configuration
LOG_FILE = "src/colorimeter.log"
ENABLE_FILE = True
LOG_LEVEL = "DEBUG"  # DEBUG, INFO, WARNING, ERROR

LEVELS = {
    "DEBUG": 0,
    "INFO": 1,
    "WARNING": 2,
    "ERROR": 3,
}


def _timestamp() -> str:
    """Return current time as formatted string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _should_log(level: str) -> bool:
    """Check if message at this level should be logged."""
    return LEVELS.get(level, 0) >= LEVELS.get(LOG_LEVEL, 0)


def _format_message(level: str, msg: str, location: str = None) -> str:
    """
    Format message with timestamp, level, and optional location.
    
    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR)
        msg: Main message text
        location: Optional location string (e.g., "function_name:L42" or "module.function")
    
    Returns:
        Formatted message string
    """
    if location:
        return f"{_timestamp()} [{level:8}] [{location}] {msg}"
    else:
        return f"{_timestamp()} [{level:8}] {msg}"


def _write_log(message: str):
    """Write message to console and optionally to file."""
    print(message)
    if ENABLE_FILE:
        try:
            with open(LOG_FILE, "a") as f:
                f.write(message + "\n")
        except IOError:
            print(f"Warning: Could not write to log file {LOG_FILE}")


def debug(msg: str, location: str = None):
    """
    Log debug message.
    
    Args:
        msg: Message text
        location: Optional location string (e.g., "function_name" or "module:function")
    """
    if _should_log("DEBUG"):
        _write_log(_format_message("DEBUG", msg, location))


def info(msg: str, location: str = None):
    """
    Log info message.
    
    Args:
        msg: Message text
        location: Optional location string (e.g., "function_name" or "module:function")
    """
    if _should_log("INFO"):
        _write_log(_format_message("INFO", msg, location))


def warning(msg: str, location: str = None):
    """
    Log warning message.
    
    Args:
        msg: Message text
        location: Optional location string (e.g., "function_name" or "module:function")
    """
    if _should_log("WARNING"):
        _write_log(_format_message("WARNING", msg, location))


def error(msg: str, location: str = None):
    """
    Log error message.
    
    Args:
        msg: Message text
        location: Optional location string (e.g., "function_name" or "module:function")
    """
    if _should_log("ERROR"):
        _write_log(_format_message("ERROR", msg, location))


def set_level(level: str):
    """
    Set minimum log level.

    Args:
        level: "DEBUG", "INFO", "WARNING", or "ERROR"
    """
    global LOG_LEVEL
    if level in LEVELS:
        LOG_LEVEL = level
    else:
        error(f"Unknown log level: {level}")


def set_file_logging(enabled: bool):
    """Enable or disable file logging."""
    global ENABLE_FILE
    ENABLE_FILE = enabled
