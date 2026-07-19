"""
Mock spidev for SPI bus control (e.g., 74HC595 shift registers).
"""

import logging

log = logging.getLogger(__name__)


class SpiDev:
    """Mock SPI device."""

    def __init__(self):
        self.no_cs = False
        self.max_speed_hz = 0
        self.mode = 0
        self._bus = None
        self._device = None

    def open(self, bus: int, device: int) -> None:
        """Open the (simulated) SPI bus/device."""
        self._bus = bus
        self._device = device
        log.info(f"SpiDev (mock): opened bus {bus}, device {device}")

    def writebytes(self, data: list) -> None:
        """Write a list of bytes to the (simulated) SPI bus."""
        log.debug(f"SpiDev (mock): writebytes {data}")

    def close(self) -> None:
        """Release the (simulated) SPI device."""
        log.info(f"SpiDev (mock): bus {self._bus} device {self._device} closed")
