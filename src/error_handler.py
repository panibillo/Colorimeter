"""
error_handler.py — Simple exception handling and logging.

Provides centralized error handling for the application. Catches exceptions,
logs them, and optionally notifies the user interface.

Usage:
    import error_handler as err
    try:
        do_something()
    except Exception as e:
        err.handle(e, "Failed to do something", recoverable=True)
"""

import sys
import traceback
import logging_manager as log


def handle(exception: Exception, context: str = "", recoverable: bool = False):
    """
    Handle an exception: log it and optionally allow recovery.

    Args:
        exception: The exception object that was caught.
        context: Optional description of what was happening (e.g., "loading config").
        recoverable: If True, error is recoverable and program can continue.
                    If False, consider shutting down gracefully.

    Returns:
        None (always dies or logs error).
    """
    # Format error message
    error_type = type(exception).__name__
    error_msg = str(exception)
    if context:
        full_msg = f"{context}: {error_type}: {error_msg}"
    else:
        full_msg = f"{error_type}: {error_msg}"

    # Log with full traceback at DEBUG level, simple message at ERROR level
    log.error(full_msg)
    log.debug(f"Traceback:\n{traceback.format_exc()}")

    # If error is recoverable, return and let caller decide next step
    if recoverable:
        log.warning(f"Recoverable error (caller should retry or continue): {full_msg}")
        return

    # If not recoverable, log and exit (caller's choice to catch this)
    log.error(f"FATAL: {full_msg} — Program should shut down")


def shutdown(exit_code: int = 1):
    """
    Log shutdown and exit cleanly.

    Args:
        exit_code: Exit code for the process. 0 = success, 1+ = error.
    """
    if exit_code == 0:
        log.info("Program shutting down normally")
    else:
        log.error(f"Program shutting down with error code {exit_code}")
    sys.exit(exit_code)
