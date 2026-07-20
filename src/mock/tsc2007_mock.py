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
        # A press not yet read via position(). Matplotlib delivers press and
        # release together in one flush_events() when the event loop is busy;
        # without this latch such a click would be over before the loop ever
        # saw is_touched.
        self._pending = False
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
        """True if screen is currently touched, or a press is still unread."""
        return self._is_touched or self._pending

    def position(self) -> Optional[Tuple[int, int]]:
        """
        Return (x, y) of current touch, or None if not touched.

        Reading the position consumes a pending press, so a click that was
        already released still delivers exactly one touch and then reads
        as released.
        """
        self._pending = False
        return self._last_position

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
