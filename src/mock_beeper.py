"""
mock_beeper.py  —  Desktop stand-in for beeper.py.
---------------------------------------------------
Provides the same public API as beeper.py but uses the terminal bell
character instead of lgpio PWM.  Imported automatically by widgets.py
when lgpio / the real beeper is not available (e.g. desktop self-tests).
"""

import sys


def beep(duration: float = 0.5) -> None:
    sys.stdout.write('\a')
    sys.stdout.flush()


def click(duration: float = 0.1) -> None:
    sys.stdout.write('\a')
    sys.stdout.flush()


def set_beep_volume(volume: float) -> None:
    pass


def set_click_volume(volume: float) -> None:
    pass


def cleanup() -> None:
    pass
