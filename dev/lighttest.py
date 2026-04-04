#!/usr/bin/env python3
"""
lighttest.py - Interactive LED lighting board controller.

Imports the lighting module and provides a simple command loop to turn
individual LEDs or combinations on and off.  No camera involved.

Commands (case-insensitive):
    B V R G W          Turn on that single LED (all others off)
    BV  BGR  BVRGW     Turn on that combination (all others off)
    O or OFF           Turn all LEDs off
    exit or quit       Exit cleanly

Usage:
    python lighttest.py
"""

import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lighting import LedController, LED_PINS

# Map single-letter shorthand to LED names
_LETTER = {
    "B": "Blue",
    "V": "Violet",
    "R": "Red",
    "G": "Green",
    "W": "White",
}


def _parse_command(raw: str) -> list[str] | None:
    """
    Parse one line of user input into a list of LED names to turn on.

    Args:
        raw: Raw input string from the user

    Returns:
        List of LED names (may be empty = all off),
        or None if the input is not recognized.
    """
    token = raw.strip().upper()

    if token in ("EXIT", "QUIT"):
        return "exit"

    if token in ("O", "OFF"):
        return []

    # Accept any combination of B V R G W letters
    if token and all(ch in _LETTER for ch in token):
        # Deduplicate while preserving order, then map to names
        seen: set[str] = set()
        names: list[str] = []
        for ch in token:
            if ch not in seen:
                seen.add(ch)
                names.append(_LETTER[ch])
        return names

    return None


def main() -> int:
    leds = LedController()

    # Ctrl-Z (SIGTSTP): release GPIO before suspending so the next run
    # does not encounter "GPIO busy".
    def _sigtstp_handler(*_):
        try:
            leds.all_off()
            leds.close()
        except Exception:
            pass
        signal.signal(signal.SIGTSTP, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTSTP)   # re-raise to actually suspend

    signal.signal(signal.SIGTSTP, _sigtstp_handler)

    print()
    print("LED lighting board test")
    print("  Enter: B V R G W  (single or combined, e.g. BV BGV)")
    print("         O or OFF   (all off)")
    print("         exit       (quit)")
    print()

    try:
        while True:
            try:
                raw = input("leds> ")
            except EOFError:
                # Ctrl-D
                print()
                break

            result = _parse_command(raw)

            if result == "exit":
                break
            if result is None:
                valid = " ".join(_LETTER.keys())
                print(f"  Unknown command. Use letters {valid}, OFF, or exit.")
                continue

            # Turn all off, then turn on the requested set
            leds.all_off()
            for name in result:
                leds.on(name)

        return 0

    except KeyboardInterrupt:
        # Ctrl-C: fall through to finally for cleanup
        print()
        return 0

    finally:
        leds.all_off()
        leds.close()
        print("All LEDs off. GPIO released.")


if __name__ == "__main__":
    sys.exit(main())
