"""
Mock hardware modules for development and testing.

Import from this package to use mock drivers instead of real hardware.
"""

from .gpiozero_mock import DigitalOutputDevice
from .ili9341_mock import ILI9341
from .tsc2007_mock import TSC2007
from .kb_reader_mock import KbReader
from .DS18B20_mock import DS18B20
from . import beeper_mock as beeper
from . import spidev_mock as spidev

__all__ = [
    'DigitalOutputDevice',
    'ILI9341',
    'TSC2007',
    'KbReader',
    'DS18B20',
    'beeper',
    'spidev',
]
