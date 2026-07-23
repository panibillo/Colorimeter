"""
Mock DS18B20 1-Wire temperature sensor.

Returns a simulated near-room temperature so the temperature-entry flow can be
exercised on the desktop without a real sensor on the bus.  Presents the same
interface as the real `DS18B20.DS18B20` driver (`init()` / `read_c()` /
`cleanup()`), so the arbiter swaps it in unchanged under COLORIMETER_SIMULATION.
"""

import logging
import random

log = logging.getLogger(__name__)

_MOCK_ID = "28-mock00000001"     # a plausible DS18B20 device-id string
_BASELINE_C = 21.5               # simulated room temperature (°C)


class DS18B20:
    """Mock DS18B20 driver: one simulated sensor, always connected."""

    def __init__(self):
        self._sensors = [_MOCK_ID]

    def init(self):
        """Pretend to discover the (single) simulated sensor."""
        log.info("DS18B20 (mock): initialized with 1 simulated sensor")

    @property
    def connected(self):
        return True

    def read_c(self):
        """Return a simulated temperature near room temperature (°C)."""
        temp = _BASELINE_C + random.uniform(-0.3, 0.3)
        log.info("DS18B20 (mock): read %.3f °C", temp)
        return temp

    def read_all(self):
        """Return one (device_id, temp_c) tuple, mirroring the real driver."""
        return [(_MOCK_ID, self.read_c())]

    def cleanup(self):
        log.info("DS18B20 (mock): cleanup")
