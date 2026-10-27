"""
Mock TSC2007 touch screen driver using mouse events.

Raw readings are the mouse position in pixels, so the starting transform is
the identity.  Its calibration file is in the system temp folder, never the
project's config/touch_cal.json, so saving a calibration in simulation cannot
overwrite real values.
"""

import logging
import os
import tempfile
from typing import Optional, Tuple

import touch_cal_store

log = logging.getLogger(__name__)

_IDENTITY = {"a": 1.0, "b": 0.0, "c": 0.0, "d": 0.0, "e": 1.0, "f": 0.0}
_MOCK_Z1 = 100     # a firm press


class TSC2007:
    """
    Mock touch screen driver.

    Receives mouse events from the display (ili9341_mock) and tracks
    touch position and is_touched state.
    """

    def __init__(self):
        self._is_touched = False
        # A press not yet read via position(). Matplotlib delivers press and
        # release together in one flush_events() when the event loop is busy;
        # without this latch such a click would be over before the loop ever
        # saw is_touched.
        self._pending = False
        self._last_position: Optional[Tuple[int, int]] = None
        self._cal = dict(_IDENTITY)
        self._cal_path = os.path.join(tempfile.gettempdir(), "colorimeter_mock",
                                      "touch_cal.json")
        log.info("TSC2007 (mock): initialized")

    @property
    def calibrated(self) -> bool:
        """
        Always True: mouse clicks arrive in display coordinates already, so
        there is no affine transform to calibrate.  Mirrors the real driver's
        property so the boot-time check needs no simulation special case.
        """
        return True

    def init(self) -> None:
        """Initialize the touch driver."""
        log.info("TSC2007 (mock): init")

    def cleanup(self) -> None:
        """Clean up resources."""
        log.info("TSC2007 (mock): cleanup")

    @property
    def is_touched(self) -> bool:
        """True if screen is currently touched, or a press is still unread."""
        return self._is_touched or self._pending

    def position(self) -> Optional[Tuple[int, int]]:
        """
        Return (x, y) of current touch, or None if not touched.

        Reading the position consumes a pending press, so a click that was
        already released still delivers exactly one touch and then reads
        as released.  The transform in use is applied, so a calibration being
        tried moves the touches as it would on the Pi.
        """
        self._pending = False
        if self._last_position is None:
            return None
        rx, ry = self._last_position
        c = self._cal
        return (round(c["a"] * rx + c["b"] * ry + c["c"]),
                round(c["d"] * rx + c["e"] * ry + c["f"]))

    def read_raw(self) -> Tuple[int, int, int]:
        """
        (x, y, z1) like the real driver: the mouse position and a firm z1,
        or (0, 0, 0) when not pressed.  Consumes a pending press.
        """
        if not self.is_touched or self._last_position is None:
            return 0, 0, 0
        self._pending = False
        return (*self._last_position, _MOCK_Z1)

    @property
    def cal(self) -> dict:
        """A copy of the transform in use."""
        return dict(self._cal)

    @property
    def cal_path(self) -> str:
        """The mock's calibration file, in the system temp folder."""
        return self._cal_path

    def apply_cal(self, cal: dict) -> None:
        """Use `cal` from now on, in memory only."""
        self._cal = {k: float(cal[k]) for k in touch_cal_store.AFFINE_KEYS}

    def save_cal(self):
        """Save the transform in use to the temp-folder file; returns the backup path."""
        return touch_cal_store.save(self._cal, self._cal_path)

    def _on_touch(self, x: int, y: int) -> None:
        """Called by display mock when mouse is clicked."""
        self._is_touched = True
        self._pending = True
        self._last_position = (x, y)
        log.info(f"TSC2007 (mock): touch at ({x}, {y}) — is_touched={self._is_touched}")

    def _on_release(self) -> None:
        """Called by display mock when mouse is released."""
        self._is_touched = False
        log.info(f"TSC2007 (mock): touch released — is_touched={self._is_touched}")
