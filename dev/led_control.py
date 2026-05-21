"""
led_control.py  —  LED channel control for Colorimeter prototype
----------------------------------------------------------------
Hardware:
  Channels 1-8 : 74HC595 shift register via SPI1
                 QA=ch1 ... QH=ch8
                 SPI1 MOSI = GPIO20 (pin 38)
                 SPI1 SCLK = GPIO21 (pin 40)
                 RCLK      = GPIO24 (pin 18)  -- manual latch
  Channel  9   : GPIO7  (pin 26) direct output
  Channel 10   : GPIO25 (pin 22) direct output

Usage as a test script:
  python3 led_control.py <start> <end> <duration>
  python3 led_control.py 1 5 2.0    # light channels 1-5 for 2 seconds
  python3 led_control.py 9 10 1.0   # light channels 9-10 for 1 second
  python3 led_control.py 1 10 0.5   # walk all channels, 0.5s each
"""

import spidev
import RPi.GPIO as GPIO
import time
import sys

# ── Pin assignments ───────────────────────────────────────────
_RCLK_PIN   = 24    # BCM — 74HC595 latch (manual GPIO)
_DIRECT_PINS = {
    9:  7,    # BCM GPIO7
    10: 25,   # BCM GPIO25
}

# ── Module-level state ────────────────────────────────────────
_spi  = None
_initialised = False


# ── Initialisation and cleanup ────────────────────────────────

def init():
    """Initialise SPI1 and GPIO.  Call once before any other function."""
    global _spi, _initialised
    if _initialised:
        return

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    # RCLK latch pin
    GPIO.setup(_RCLK_PIN, GPIO.OUT, initial=GPIO.LOW)

    # Direct-GPIO MOSFET channels
    for ch, pin in _DIRECT_PINS.items():
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

    # SPI1 for shift register
    _spi = spidev.SpiDev()
    _spi.open(1, 0)
    _spi.no_cs       = True
    _spi.max_speed_hz = 500_000
    _spi.mode        = 0

    # Ensure all shift-register outputs start off
    _shift_out(0x00)

    _initialised = True


def cleanup():
    """Turn off all LEDs and release hardware resources."""
    global _spi, _initialised
    if not _initialised:
        return
    all_off()
    _spi.close()
    _spi = None
    GPIO.cleanup()
    _initialised = False


# ── Low-level primitives ──────────────────────────────────────

def _shift_out(byte_val):
    """Write one byte to the 74HC595 and pulse RCLK to latch outputs."""
    _spi.writebytes([byte_val & 0xFF])
    GPIO.output(_RCLK_PIN, GPIO.HIGH)
    GPIO.output(_RCLK_PIN, GPIO.LOW)


def _channels_to_byte(channels):
    """
    Convert a collection of channel numbers (1-8) to a shift-register byte.
    Channel 1 = QA = bit 7 (MSB sent first).
    Channel 8 = QH = bit 0.
    """
    byte_val = 0
    for ch in channels:
        if 1 <= ch <= 8:
            byte_val |= (1 << (8 - ch))
    return byte_val


# ── Public API ────────────────────────────────────────────────

def set_channels(channels, state):
    """
    Set one or more LED channels on or off.

    Parameters
    ----------
    channels : int or iterable of int
        Channel number(s) 1-10.
    state : bool
        True = on, False = off.
    """
    if isinstance(channels, int):
        channels = [channels]
    channels = list(channels)

    # Separate shift-register (1-8) and direct-GPIO (9-10) channels
    sr_channels  = [ch for ch in channels if 1 <= ch <= 8]
    dir_channels = [ch for ch in channels if ch in _DIRECT_PINS]

    if sr_channels:
        # Read-modify-write: get current byte, set/clear requested bits
        # We track state in a module variable rather than reading back from HW
        current = _get_sr_state()
        for ch in sr_channels:
            if state:
                current |= (1 << (8 - ch))
            else:
                current &= ~(1 << (8 - ch))
        _shift_out(current)
        _sr_state[0] = current

    for ch in dir_channels:
        GPIO.output(_DIRECT_PINS[ch], GPIO.HIGH if state else GPIO.LOW)


def all_off():
    """Turn off all 10 LED channels."""
    _shift_out(0x00)
    _sr_state[0] = 0x00
    for pin in _DIRECT_PINS.values():
        GPIO.output(pin, GPIO.LOW)


def all_on():
    """Turn on all 10 LED channels."""
    _shift_out(0xFF)
    _sr_state[0] = 0xFF
    for pin in _DIRECT_PINS.values():
        GPIO.output(pin, GPIO.HIGH)


def pulse_channel(channel, duration):
    """Turn one channel on for duration seconds, then off."""
    set_channels(channel, True)
    time.sleep(duration)
    set_channels(channel, False)


def walk_channels(start, end, duration):
    """
    Light each channel from start to end in sequence.
    Each channel is on for duration seconds before moving to the next.
    All channels off at completion.

    Parameters
    ----------
    start    : int  first channel (1-10)
    end      : int  last channel  (1-10, inclusive)
    duration : float  seconds per channel
    """
    start = max(1, min(10, start))
    end   = max(1, min(10, end))
    step  = 1 if end >= start else -1

    all_off()
    for ch in range(start, end + step, step):
        print(f"  Channel {ch:2d}  on")
        set_channels(ch, True)
        time.sleep(duration)
        set_channels(ch, False)
        print(f"  Channel {ch:2d}  off")
    all_off()


# Internal shift-register state tracker (list so it is mutable in closures)
_sr_state = [0x00]

def _get_sr_state():
    return _sr_state[0]


# ── Command-line entry point ──────────────────────────────────

def _usage():
    print("Usage: python3 led_control.py <start> <end> <duration_seconds>")
    print("  start, end : channel numbers 1-10 inclusive")
    print("  duration   : seconds each channel stays on (float)")
    print("Example: python3 led_control.py 1 5 1.0")
    sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        _usage()

    try:
        start    = int(sys.argv[1])
        end      = int(sys.argv[2])
        duration = float(sys.argv[3])
    except ValueError:
        _usage()

    if not (1 <= start <= 10 and 1 <= end <= 10 and duration > 0):
        print("Error: channels must be 1-10, duration must be > 0")
        _usage()

    print(f"LED walk: channels {start} to {end}, {duration}s each")
    init()
    try:
        walk_channels(start, end, duration)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        cleanup()
        print("Done.")
