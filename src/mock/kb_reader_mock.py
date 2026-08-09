"""
Mock keyboard reader using matplotlib event system.
"""

import logging
from typing import Optional, Deque
from collections import deque

log = logging.getLogger(__name__)


class KbReader:
    """
    Mock keyboard reader.

    Receives key events from the display (ili9341_mock) and queues them
    for synchronous polling via read_one().
    """

    def __init__(self):
        self._queue: Deque[str] = deque()
        self._display = None
        log.info("KbReader (mock): initialized")

    def open(self) -> bool:
        """
        Open the keyboard input device.

        Returns True like the real driver does — the simulated keyboard is
        always present, and the boot-time check reads this answer.
        """
        log.info("KbReader (mock): open")
        return True

    def cleanup(self) -> None:
        """Clean up resources."""
        self._queue.clear()
        log.info("KbReader (mock): cleanup")

    def read_one(self) -> Optional[str]:
        """
        Return the next queued key event, or None if no keys are pending.

        Recognized keys: 'tab', 'shift-tab', 'enter', 'backspace', 'delete',
        or single printable characters.
        """
        if self._queue:
            key = self._queue.popleft()
            log.debug(f"KbReader (mock): read_one() → {key!r}")
            return key
        return None

    def _on_key_event(self, key: str) -> None:
        """Called by display mock when a key event occurs."""
        self._queue.append(key)
        log.debug(f"KbReader (mock): queued key {key!r} (queue size: {len(self._queue)})")
