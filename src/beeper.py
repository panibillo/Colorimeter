#!/usr/bin/env python3
"""
beeper.py  —  Magnetic beeper driver with sine-table PWM modulation.
--------------------------------------------------------------------
Drives a 42 Ω CEM-1203 passive magnetic beeper via an MMBT3904 NPN transistor
on GPIO 12, using lgpio software PWM at a 10 kHz carrier (lgpio maximum).  The
duty cycle is modulated by a pre-built 256-entry sine table to produce audio in
the 110–2200 Hz range with smooth 10–20 ms attack and decay envelopes.

Hardware
--------
  GPIO 12 (BCM, pin 32) → base resistor → MMBT3904 base
  MMBT3904 collector → beeper → 3.3 V
  MMBT3904 emitter  → GND

PWM principle
-------------
  Carrier at 10 kHz (lgpio software PWM ceiling): the beeper diaphragm cannot
  follow 10 kHz and responds instead to the time-averaged duty cycle.  Modulating
  the duty cycle sinusoidally at audio frequency (110–2200 Hz) creates the
  audible tone.  Note: 10 kHz is at the upper edge of hearing; a faint carrier
  tone may be audible at high volume.

  Duty = 50 % (quiescent, no AC component) → near-silent.
  Duty = 50 ± 40 % (full swing) → maximum audio output.

  Attack and decay ramp the ± swing linearly 0 → 1 and 1 → 0, eliminating
  clicks at onset and offset.

Public API
----------
    from beeper import beep, click, set_beep_volume, set_click_volume, cleanup

    set_beep_volume(0.5)
    beep()              # 2200 Hz, 0.6 s
    click()             # 880 Hz, 0.2 s
    cleanup()           # call at program exit

Run tests:
    python3 test/test_beeper.py
"""

import math
import time

# Hardware-only import.  Guarded so this module stays importable on a
# desktop for testing; _open() raises a clear error if actually used
# without it.  On a Pi it is present and nothing changes.
try:
    import lgpio
except ImportError:                                  # pragma: no cover
    lgpio = None

# ── Hardware / table constants (private) ──────────────────────────────────────
_PIN            = 12       # BCM GPIO — MMBT3904 base driver
_CHIP           = 0        # /dev/gpiochip0
_CARRIER        = 10_000   # PWM carrier frequency (Hz) — lgpio software PWM max
_N              = 256
_BEEP_DURATION  = 0.6      # seconds
_CLICK_DURATION = 0.1      # seconds
_SINE_TABLE: list[float] = [
    40.0 * math.sin(2.0 * math.pi * i / _N)
    for i in range(_N)
]


# ── Beeper class ──────────────────────────────────────────────────────────────

class Beeper:
    """
    Singleton audio driver.  GPIO is claimed lazily on first sound call.

    Do not instantiate directly — use the module-level functions instead.
    """

    def __init__(self) -> None:
        self._h            = None   # lgpio chip handle; None until first use
        self._beep_volume  = 0.3
        self._click_volume = 0.2
        
    def _open(self) -> None:
        if self._h is None:
            if lgpio is None:
                raise RuntimeError(
                    "Beeper needs lgpio, which is not installed. This "
                    "driver only runs on the Pi; set COLORIMETER_SIMULATION=1 "
                    "to use the mock beeper instead.")
            self._h = lgpio.gpiochip_open(_CHIP)
            lgpio.gpio_claim_output(self._h, _PIN)

    def _sound(
        self,
        freq_hz:   float,
        volume:    float,
        duration:  float,
        attack_ms: float,
        decay_ms:  float,
    ) -> None:
        """Sine-table PWM tone. Blocks the calling thread for duration seconds."""
        self._open()

        volume  = max(0.0, min(1.0, volume))
        freq_hz = max(110.0, min(2_200.0, freq_hz))

        attack_s = attack_ms * 0.001
        decay_s  = decay_ms  * 0.001

        # Adaptive stepping: advance the sine table by 'step' entries per update,
        # targeting ~8 000 duty-cycle updates per second (~125 µs per step).
        step   = max(1, round(freq_hz * _N / 8_000))
        step_s = step / (freq_hz * _N)

        t_start       = time.monotonic()
        t_end         = t_start + duration
        t_attack_end  = t_start + attack_s
        t_decay_start = t_end   - decay_s

        idx    = 0
        t_next = t_start

        # Tight busy-poll — no time.sleep() to avoid scheduler jitter.
        while True:
            now = time.monotonic()
            if now >= t_end:
                break
            if now >= t_next:
                if now < t_attack_end:
                    env = (now - t_start) / attack_s
                elif now > t_decay_start:
                    env = (t_end - now) / decay_s
                else:
                    env = 1.0

                duty = 50.0 + max(0.0, min(1.0, env)) * volume * _SINE_TABLE[idx]
                lgpio.tx_pwm(self._h, _PIN, _CARRIER, duty)

                idx    = (idx + step) % _N
                t_next += step_s

        lgpio.tx_pwm(self._h, _PIN, _CARRIER, 0.0)   # transistor fully off

    def beep(self, duration: float = _BEEP_DURATION) -> None:
        self._sound(2200, self._beep_volume, duration, attack_ms=20, decay_ms=20)

    def click(self, duration: float = _CLICK_DURATION) -> None:
        self._sound(880, self._click_volume, duration, attack_ms=15, decay_ms=40)

    def set_beep_volume(self, volume: float) -> None:
        self._beep_volume = max(0.0, min(1.0, volume))

    def set_click_volume(self, volume: float) -> None:
        self._click_volume = max(0.0, min(1.0, volume))

    def cleanup(self) -> None:
        if self._h is not None:
            lgpio.tx_pwm(self._h, _PIN, _CARRIER, 0.0)
            lgpio.gpiochip_close(self._h)
            self._h = None


# ── Module-level singleton + public wrapper functions ─────────────────────────

_instance = Beeper()


def beep(duration: float = _BEEP_DURATION) -> None:
    """Sound the beeper at 2200 Hz for duration seconds."""
    _instance.beep(duration)


def click(duration: float = _CLICK_DURATION) -> None:
    """Sound a short click at 880 Hz for duration seconds."""
    _instance.click(duration)


def set_beep_volume(volume: float) -> None:
    """Set beep volume: 0.0 (silent) to 1.0 (full amplitude)."""
    _instance.set_beep_volume(volume)


def set_click_volume(volume: float) -> None:
    """Set click volume: 0.0 (silent) to 1.0 (full amplitude)."""
    _instance.set_click_volume(volume)


def cleanup() -> None:
    """Silence the beeper and release the lgpio chip handle."""
    _instance.cleanup()
