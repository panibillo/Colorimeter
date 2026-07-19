"""
Mock gpiozero for GPIO control (e.g., backlight).
"""

import logging

log = logging.getLogger(__name__)


class DigitalOutputDevice:
    """Mock GPIO output device."""

    def __init__(self, pin: int, initial_value: bool = False):
        self.pin = pin
        self.value = initial_value
        log.info(f"DigitalOutputDevice (mock): pin {pin}, initial_value={initial_value}")

    def on(self) -> None:
        """Turn on (set high)."""
        self.value = True
        log.debug(f"DigitalOutputDevice (mock): pin {self.pin} ON")

    def off(self) -> None:
        """Turn off (set low)."""
        self.value = False
        log.debug(f"DigitalOutputDevice (mock): pin {self.pin} OFF")

    def close(self) -> None:
        """Release the device."""
        log.info(f"DigitalOutputDevice (mock): pin {self.pin} closed")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
