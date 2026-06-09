#!/usr/bin/env python3
"""
buzzer.py  —  Magnetic buzzer driver with sine-table PWM modulation.
--------------------------------------------------------------------
Drives a 42 Ω CEM-1203 passive magnetic buzzer via an MMBT3904 NPN transistor
on GPIO 12, using lgpio software PWM at a 10 kHz carrier (lgpio maximum).  The
duty cycle is modulated by a pre-built 256-entry sine table to produce audio in
the 110–2200 Hz range with smooth 10–20 ms attack and decay envelopes.

Hardware
--------
  GPIO 12 (BCM, pin 32) → base resistor → MMBT3904 base
  MMBT3904 collector → buzzer → 3.3 V
  MMBT3904 emitter  → GND

PWM principle
-------------
  Carrier at 10 kHz (lgpio software PWM ceiling): the buzzer diaphragm cannot
  follow 10 kHz and responds instead to the time-averaged duty cycle.  Modulating
  the duty cycle sinusoidally at audio frequency (110–2200 Hz) creates the
  audible tone.  Note: 10 kHz is at the upper edge of hearing; a faint carrier
  tone may be audible at high volume.

  Duty = 50 % (quiescent, no AC component) → near-silent.
  Duty = 50 ± 40 % (full swing) → maximum audio output.

  Attack and decay ramp the ± swing linearly 0 → 1 and 1 → 0, eliminating
  clicks at onset and offset.

Usage
-----
    from buzzer import sound_alert, cleanup

    sound_alert(freq_hz=440.0, volume=1.0, duration=1.0)
    cleanup()                    # call at program exit

    python3 dev/buzzer.py        # runs built-in test sequence
    python3 dev/buzzer.py --freq 880 --volume 0.5 --duration 2.0
"""

import lgpio
import math
import time

# ── Hardware ──────────────────────────────────────────────────────────────────
BUZZER_PIN = 12        # BCM GPIO — MMBT3904 base driver
CHIP       = 0         # /dev/gpiochip0
CARRIER_HZ = 10_000    # PWM carrier frequency (Hz) — lgpio software PWM max

# ── Sine table (256 entries, built once at module load) ───────────────────────
# Stores amplitude only (no DC offset).  Range: ±40.0 (percent of duty cycle).
# Actual duty at each step = 50.0 + envelope × volume × SINE_TABLE[idx]
_N         = 256
SINE_TABLE: list[float] = [
    40.0 * math.sin(2.0 * math.pi * i / _N)
    for i in range(_N)
]

# ── lgpio handle (opened at import time) ──────────────────────────────────────
_h = lgpio.gpiochip_open(CHIP)
lgpio.gpio_claim_output(_h, BUZZER_PIN)


# ── Public API ────────────────────────────────────────────────────────────────

def sound_alert(
    freq_hz:   float = 440.0,
    volume:    float = 1.0,
    duration:  float = 1.0,
    attack_ms: float = 15.0,
    decay_ms:  float = 15.0,
) -> None:
    """
    Sound the buzzer with a sine-modulated 20 kHz PWM carrier.

    Blocks the calling thread for duration seconds.  Wrap in
    threading.Thread if non-blocking behaviour is needed.

    Args:
        freq_hz:   Audio frequency in Hz.  Clamped to 110–2200 Hz.
        volume:    Amplitude 0.0 (silent) to 1.0 (full ±40 % duty swing).
        duration:  Total duration in seconds.
        attack_ms: Linear attack ramp in milliseconds (10–20 ms recommended).
        decay_ms:  Linear decay ramp in milliseconds (10–20 ms recommended).

    Examples:
        sound_alert()                               # 440 Hz, 1 s, full volume
        sound_alert(880.0, volume=0.5, duration=0.5)
    """
    volume  = max(0.0, min(1.0, volume))
    freq_hz = max(110.0, min(2_200.0, freq_hz))

    attack_s = attack_ms * 0.001
    decay_s  = decay_ms  * 0.001

    # Adaptive stepping: advance the sine table by 'step' entries per update,
    # targeting ~8 000 duty-cycle updates per second.
    # step_s ≈ 125 µs regardless of audio frequency.
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

            duty = 50.0 + max(0.0, min(1.0, env)) * volume * SINE_TABLE[idx]
            lgpio.tx_pwm(_h, BUZZER_PIN, CARRIER_HZ, duty)

            idx    = (idx + step) % _N
            t_next += step_s

    lgpio.tx_pwm(_h, BUZZER_PIN, CARRIER_HZ, 0.0)   # transistor fully off


def beep(freq=2200, volume = 0.3, duration = 0.5, attack=20, decay=20):
    # make an alarm sound
    sound_alert(freq, volume, duration,
                        attack, decay)
def click(freq=880, volume = 0.2, duration = 0.1, attack=15, decay=40):
    # make an alarm sound
    sound_alert(freq, volume, duration,
                        attack, decay)

def cleanup() -> None:
    """Silence the buzzer and release the lgpio chip handle."""
    lgpio.tx_pwm(_h, BUZZER_PIN, CARRIER_HZ, 0.0)
    lgpio.gpiochip_close(_h)


# ── Self-test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Test the sine-table PWM buzzer.")
    parser.add_argument("--freq",     type=float, default=0.0,
                        help="Single frequency Hz (0 = run test sequence)")
    parser.add_argument("--volume",   type=float, default=1.0,
                        help="Volume 0.0–1.0 (default 1.0)")
    parser.add_argument("--duration", type=float, default=1.0,
                        help="Duration in seconds (default 1.0)")
    parser.add_argument("--attack",   type=float, default=15.0,
                        help="Attack ms (default 15)")
    parser.add_argument("--decay",    type=float, default=15.0,
                        help="Decay ms (default 15)")
    args = parser.parse_args()

    try:
        if args.freq > 0:
            print(f"{args.freq:.0f} Hz  vol={args.volume}  {args.duration:.1f}s")
            sound_alert(args.freq, args.volume, args.duration,
                        args.attack, args.decay)
        else:
            tests = [ # freq, vol, duration, label
                # (110.0,  0.5, 0.1, "110 Hz — low A, full volume"),
                # (440.0,  0.5, 0.1, "440 Hz — concert A, full volume"),
                (880.0,  0.5, 0.05, "880 Hz — octave up, half volume"),
                (1760.0, 0.5, 0.1, "1800 Hz — near buzzer resonance"),
                #(1880.0, 0.5, 0.1, "1900 Hz — near buzzer resonance"),
                # (2000.0, 0.5, 0.1, "2000 Hz — near buzzer resonance"),
                (2200.0, 0.2, 0.5, "2200 Hz — near buzzer resonance"),
            ]
            for freq, vol, dur, label in tests:
                print(f"  {label}")
                sound_alert(freq, vol, dur, args.attack, args.decay)
                time.sleep(1.25)
    finally:
        cleanup()
