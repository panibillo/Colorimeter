#!/usr/bin/env python3
"""
lighting_cal8.py  —  LED channel control for Colorimeter cal8 PCB
------------------------------------------------------------------
Hardware:
  Channels 3-10 : 74HC595 shift register via SPI1
                  ch3(blue)=QA ... ch10(white)=QH
                  SPI1 MOSI = GPIO20 (pin 38)
                  SPI1 SCLK = GPIO21 (pin 40)
                  RCLK      = GPIO24 (pin 18) -- manual latch
  Channel  1    : GPIO25 (pin 22) direct -- violet    410 nm
  Channel  2    : GPIO7  (pin 26) direct -- royal_blue 455 nm

  Note: GPIO7 = SPI0_CE1. Requires dtoverlay=spi0-1cs in
  /boot/firmware/config.txt so the kernel only claims CE0 (GPIO8),
  leaving GPIO7 free for general use.

Channels 1-10 are ordered by increasing wavelength:

  Ch  Name         nm    Hardware
   1  violet       410   GPIO25 (direct)
   2  royal_blue   455   GPIO7  (direct)
   3  blue         475   shift reg bit 7
   4  cyan         500   shift reg bit 6
   5  green        525   shift reg bit 5
   6  amber        590   shift reg bit 4
   7  red_orange   615   shift reg bit 3
   8  red          625   shift reg bit 2
   9  photo_red    660   shift reg bit 1
  10  white         —    shift reg bit 0

Usage as a test script:
  python3 lighting_cal8.py <start> <end> <duration>
  python3 lighting_cal8.py 1 10 0.5       # walk all, 0.5 s each
  python3 lighting_cal8.py blue red 1.0   # walk by name
  python3 lighting_cal8.py 1 2 1.0        # direct-GPIO channels only
"""

import os
import sys
import time

from utils import install_exit_signals

# Use mock hardware if COLORIMETER_SIMULATION=1
_SIMULATION_MODE = os.environ.get('COLORIMETER_SIMULATION', '0') == '1'

if _SIMULATION_MODE:
    from mock.gpiozero_mock import DigitalOutputDevice
    from mock import spidev_mock as spidev
else:
    from gpiozero import DigitalOutputDevice
    import spidev

# ── Hardware pin assignments ──────────────────────────────────
_RCLK_PIN    = 24       # BCM — 74HC595 register clock (latch)
_DIRECT_PINS = {
    1: 25,   # violet     410 nm
    2:  7,   # royal_blue 455 nm
}


class LEDBank:
    """
    Controls 10 LED channels (1-10) ordered by wavelength.

    Channels 1-2 are driven directly from GPIO pins.
    Channels 3-10 are driven through a 74HC595 shift register
    on SPI1.  All channels are off on construction.

    Supports channel selection by number (int 1-10) or name (str).
    Use as a context manager for automatic resource cleanup.

    Example:
        >>> with LEDBank() as leds:
        ...     leds.set_channel('green', True)
        ...     time.sleep(1.0)
    """

    # (name, wavelength_nm)  index 0 = channel 1
    _LED_DEFS = [
        ('violet',      410),   # ch  1 — GPIO25
        ('royal_blue',  455),   # ch  2 — GPIO7
        ('blue',        475),   # ch  3 — shift reg bit 7
        ('cyan',        500),   # ch  4 — shift reg bit 6
        ('green',       525),   # ch  5 — shift reg bit 5
        ('amber',       590),   # ch  6 — shift reg bit 4
        ('red_orange',  615),   # ch  7 — shift reg bit 3
        ('red',         625),   # ch  8 — shift reg bit 2
        ('photo_red',   660),   # ch  9 — shift reg bit 1
        ('white',      None),   # ch 10 — shift reg bit 0
    ]

    _NAME_TO_CH = {
        name: i + 1 for i, (name, _) in enumerate(_LED_DEFS)
    }

    def __init__(self):
        """
        Initialise SPI1, RCLK latch, and direct GPIO outputs.

        Raises:
            RuntimeError: If SPI1 cannot be opened or a GPIO pin
                is already claimed (check spi0-1cs overlay).
        """
        if _SIMULATION_MODE:
            print("[SIMULATION] LEDBank: using mock spidev/gpiozero drivers")
        self._rclk = DigitalOutputDevice(
            _RCLK_PIN, initial_value=False
        )
        self._direct = {
            ch: DigitalOutputDevice(pin, initial_value=False)
            for ch, pin in _DIRECT_PINS.items()
        }
        self._spi = spidev.SpiDev()
        self._spi.open(1, 0)
        self._spi.no_cs        = True
        self._spi.max_speed_hz = 500_000
        self._spi.mode         = 0
        self._sr_byte          = 0x00
        self._write_sr(0x00)    # ensure all SR outputs start off

    def close(self) -> None:
        """
        Turn off all LEDs and release all hardware resources.

        Safe to call more than once.
        """
        self.all_off()
        self._spi.close()
        self._rclk.close()
        for dev in self._direct.values():
            dev.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ── Channel resolution ────────────────────────────────────

    def _resolve(self, channel) -> int:
        """
        Return the integer channel number for an int or name.

        Args:
            channel: int 1-10, or str color name (case-insensitive).

        Returns:
            Integer channel number 1-10.

        Raises:
            ValueError: If the name is unknown or int is out of range.
        """
        if isinstance(channel, str):
            ch = self._NAME_TO_CH.get(channel.lower())
            if ch is None:
                names = list(self._NAME_TO_CH)
                raise ValueError(
                    f"Unknown LED name {channel!r}. "
                    f"Valid names: {names}"
                )
            return ch
        if not 1 <= channel <= 10:
            raise ValueError(f"Channel must be 1-10, got {channel}")
        return channel

    def label(self, channel) -> str:
        """
        Return a human-readable label for a channel.

        Args:
            channel: int 1-10 or str color name.

        Returns:
            String like 'ch 5 (green, 525 nm)' or 'ch 10 (white)'.

        Example:
            >>> leds.label(5)
            'ch 5 (green, 525 nm)'
            >>> leds.label('white')
            'ch 10 (white)'
        """
        ch = self._resolve(channel)
        name, wl = self._LED_DEFS[ch - 1]
        wl_str = f"{wl} nm" if wl is not None else "broadband"
        return f"ch {ch} ({name}, {wl_str})"

    def color_name(self, channel) -> str:
        """
        Return color name for a channel.

        Args:
            channel: int 1-10 or str color name.

        Returns:
            String like 'green' or 'white'.
        """
        ch = self._resolve(channel)
        color_name, _ = self._LED_DEFS[ch - 1]
        return color_name

    def channel_of(self, name: str) -> int:
        """
        Return the channel number for a color name (reverse of color_name()).

        Args:
            name: str color name (case-insensitive).

        Returns:
            Integer channel number 1-10.

        Raises:
            ValueError: If the name is unknown.
        """
        return self._resolve(name)

    # ── Low-level SPI ─────────────────────────────────────────

    def _write_sr(self, byte_val: int) -> None:
        """Write one byte to the 74HC595 and pulse RCLK to latch."""
        self._spi.writebytes([byte_val & 0xFF])
        self._rclk.on()
        self._rclk.off()

    # ── Public API ────────────────────────────────────────────

    def set_channel(self, channel, on: bool) -> None:
        """
        Set one LED channel on or off.

        Args:
            channel: int 1-10 or str color name.
            on: True to turn on, False to turn off.

        Raises:
            ValueError: If channel is unknown or out of range.

        Example:
            >>> leds.set_channel('green', True)
            >>> leds.set_channel(5, False)
        """
        ch = self._resolve(channel)
        if ch in self._direct:
            if on:
                self._direct[ch].on()
            else:
                self._direct[ch].off()
        else:
            # ch 3 → bit 7 ... ch 10 → bit 0
            bit = 10 - ch
            if on:
                self._sr_byte |=  (1 << bit)
            else:
                self._sr_byte &= ~(1 << bit)
            self._write_sr(self._sr_byte)

    def all_off(self) -> None:
        """
        Turn off all 10 LED channels.

        Example:
            >>> leds.all_off()
        """
        self._sr_byte = 0x00
        self._write_sr(0x00)
        for dev in self._direct.values():
            dev.off()

    def all_on(self) -> None:
        """
        Turn on all 10 LED channels simultaneously.

        Example:
            >>> leds.all_on()
        """
        self._sr_byte = 0xFF
        self._write_sr(0xFF)
        for dev in self._direct.values():
            dev.on()

    def pulse(self, channel, duration: float) -> None:
        """
        Turn one channel on for a fixed duration, then off.

        Args:
            channel: int 1-10 or str color name.
            duration: On-time in seconds.

        Example:
            >>> leds.pulse('red', 0.5)
        """
        self.set_channel(channel, True)
        time.sleep(duration)
        self.set_channel(channel, False)

    def walk(self, start, end, duration: float) -> None:
        """
        Light each channel from start to end in sequence.

        Each channel is on for duration seconds before advancing
        to the next.  Prints label when turned on and off.
        All channels are off on entry and on return.

        Args:
            start: First channel, int 1-10 or str color name.
            end: Last channel inclusive, int 1-10 or str name.
            duration: Seconds each channel stays on.

        Example:
            >>> leds.walk(1, 10, 0.5)          # all channels
            >>> leds.walk('blue', 'red', 1.0)   # by name
        """
        start_ch = self._resolve(start)
        end_ch   = self._resolve(end)
        step     = 1 if end_ch >= start_ch else -1
        self.all_off()
        for ch in range(start_ch, end_ch + step, step):
            lbl = self.label(ch)
            print(f"  {lbl}  on", end = '...  ')
            self.set_channel(ch, True)
            time.sleep(duration)
            self.set_channel(ch, False)
            print(f"off")
        self.all_off()


# ── Command-line entry point ──────────────────────────────────

def _usage():
    print(
        "Usage: python3 lighting_cal8.py <start> <end> <duration_s>\n"
        "  start, end : channel 1-10 or color name\n"
        "  duration   : seconds each channel stays on\n"
        "Examples:\n"
        "  python3 lighting_cal8.py 1 10 0.5\n"
        "  python3 lighting_cal8.py blue red 1.0"
    )
    sys.exit(1)


def _parse_ch(s: str):
    """Return int if s is a digit string, else leave as str for LEDBank."""
    try:
        return int(s)
    except ValueError:
        return s


if __name__ == "__main__":
    install_exit_signals()

    if len(sys.argv) != 4:
        _usage()

    raw_start, raw_end, raw_dur = sys.argv[1], sys.argv[2], sys.argv[3]

    try:
        duration = float(raw_dur)
    except ValueError:
        print(f"Error: duration must be a number, got {raw_dur!r}")
        _usage()

    if duration <= 0:
        print("Error: duration must be > 0")
        _usage()

    start = _parse_ch(raw_start)
    end   = _parse_ch(raw_end)

    try:
        with LEDBank() as leds:
            try:
                print(
                    f"LED walk: {leds.label(start)}"
                    f" → {leds.label(end)}, {duration} s each"
                )
                leds.walk(start, end, duration)
            except KeyboardInterrupt:
                print("\nInterrupted.")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    print("Done.")
