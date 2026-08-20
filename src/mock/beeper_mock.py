"""
Mock beeper for audio feedback.

On the desktop, actually plays system sounds via canberra-gtk-play.
"""

import logging
import subprocess

log = logging.getLogger(__name__)


def click() -> None:
    """Play a click sound."""
    log.info("Beeper (mock): click")
    try:
        subprocess.run(["canberra-gtk-play", "--id=button-pressed"], timeout=1)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # canberra-gtk-play not available


def beep(duration: float = 0.6) -> None:
    """Play a beep sound (2-second variant for stir sub-alarms)."""
    log.info("Beeper (mock): beep (duration %.1f s)", duration)
    try:
        subprocess.run(["canberra-gtk-play", "--id=bell"], timeout=1)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # canberra-gtk-play not available


def alarm_on() -> None:
    """Start alarm sound."""
    log.info("Beeper (mock): alarm ON")
    try:
        subprocess.run(["canberra-gtk-play", "--id=bell"], timeout=1)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def alarm_off() -> None:
    """Stop alarm sound."""
    log.info("Beeper (mock): alarm OFF")
    # No sound on off


def cleanup() -> None:
    """Clean up audio resources."""
    log.info("Beeper (mock): cleanup")
