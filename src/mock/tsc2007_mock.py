"""
Mock TSC2007 touch screen driver using mouse events.
"""

import logging
from typing import Optional, Tuple

log = logging.getLogger(__name__)


class TSC2007:
    """
    Mock touch screen driver.

    Receives mouse events from the display (ili9341_mock) and tracks
    touch position and is_touched state.
    """

    def __init__(self):
        self._is_touched = False
        self._last_position: Optional[Tuple[int, int]] = None
        log.info("TSC2007 (mock): initialized")

    def init(self) -> None:
        """Initialize the touch driver."""
        log.info("TSC2007 (mock): init")

    def cleanup(self) -> None:
        """Clean up resources."""
        log.info("TSC2007 (mock): cleanup")

    @property
    def is_touched(self) -> bool:
        """True if screen is currently touched."""
        return self._is_touched

    def position(self) -> Optional[Tuple[int, int]]:
        """Return (x, y) of current touch, or None if not touched."""
        return self._last_position

    def _on_touch(self, x: int, y: int) -> None:
        """Called by display mock when mouse is clicked."""
        self._is_touched = True
        self._last_position = (x, y)
        log.info(f"TSC2007 (mock): touch at ({x}, {y}) — is_touched={self._is_touched}")

    def _on_release(self) -> None:
        """Called by display mock when mouse is released."""
        self._is_touched = False
        log.info(f"TSC2007 (mock): touch released — is_touched={self._is_touched}")
