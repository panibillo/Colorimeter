#!/usr/bin/env python3
"""
test_beeper.py  —  Manual hardware tests for src/beeper.py.
------------------------------------------------------------
Run on the Raspberry Pi:
    python3 test/test_beeper.py

Each function tests one aspect of the beeper.  They run sequentially when
this file is executed directly.
"""

import sys
import time
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from beeper import beep, click, set_beep_volume, set_click_volume, cleanup


def test_default_beep():
    print("beep() — default volume, 0.5 s")
    beep()
    time.sleep(1.0)


def test_default_click():
    print("click() — default volume, 0.1 s")
    click()
    time.sleep(0.5)


def test_beep_volume():
    print("set_beep_volume(0.1) — quiet beep")
    set_beep_volume(0.1)
    beep()
    time.sleep(0.5)
    print("set_beep_volume(0.3) — restore default")
    set_beep_volume(0.3)
    time.sleep(0.3)


def test_click_volume():
    print("set_click_volume(0.05) — quiet click")
    set_click_volume(0.05)
    click()
    time.sleep(0.3)
    print("set_click_volume(0.2) — restore default")
    set_click_volume(0.2)
    time.sleep(0.3)


def test_freq_sweep():
    """Direct _sound calls via the private instance — for tuning only."""
    from beeper import _instance
    tests = [
        (880,  0.5, 0.05, "880 Hz"),
        (1760, 0.5, 0.10, "1760 Hz"),
        (2200, 0.2, 0.50, "2200 Hz"),
    ]
    for freq, vol, dur, label in tests:
        print(f"  {label}")
        _instance._sound(freq, vol, dur, attack_ms=15, decay_ms=15)
        time.sleep(1.25)


if __name__ == "__main__":
    try:
        test_default_beep()
        test_default_click()
        test_beep_volume()
        test_click_volume()
    finally:
        cleanup()
