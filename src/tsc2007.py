"""
tsc2007.py  —  TSC2007 resistive touch controller driver
---------------------------------------------------------
Drives the TSC2007 touch ADC on the Adafruit 1651 shield via I2C bus 1.

Hardware connections (from as-built pin map):
  I2C SDA    GPIO2  pin 3
  I2C SCL    GPIO3  pin 5
  TSIRQ      GPIO4  pin 7   (active-low interrupt, optional)

TSC2007 I2C address: 0x48

Protocol notes:
  The TSC2007 uses a simple write-then-read I2C protocol, not SMBus.
  Each measurement: write 1 command byte, wait ~0.1 ms, read 2 bytes.
  Use smbus2.i2c_rdwr — do NOT use write_byte / read_word_data.

  Command byte format: [C3 C2 C1 C0 M -- PD1 PD0]
    M=0  → 12-bit resolution
    PD=00 → power-down between conversions
    X measurement : 0xC0
    Y measurement : 0xD0
    Z1 pressure   : 0xE0   (0xB0 is SETUP — no ACK, causes errno 121)

  Response: [D11:D4] [D3:D0 x x x x] → 12-bit value in upper 12 bits.

Calibration:
  Raw ADC values are mapped to pixel coordinates using per-unit calibration
  stored in ~/pi/Colorimeter/config/touch_cal.json.
  Run tsc2007.calibrate(display) once per unit to generate the file.
  Axis mirroring for display orientation is applied after calibration mapping.

Usage:
    from tsc2007 import TSC2007
    touch = TSC2007()
    touch.init()
    pos = touch.position()      # returns (px, py) or None
    touch.cleanup()
"""

import time
import json
import os
import RPi.GPIO as GPIO
import smbus2

from geometry import Rect


# ── Default config path ───────────────────────────────────────────────────────
_DEFAULT_CAL_PATH = os.path.expanduser(
    "~/pi/Colorimeter/config/touch_cal.json")

# ── TSC2007 measurement commands ──────────────────────────────────────────────
_CMD_X  = 0xC0
_CMD_Y  = 0xD0
_CMD_Z1 = 0xE0

# ── Default calibration (typical values; always override with measured) ───────
_DEFAULT_CAL = {
    "x_min": 300, "x_max": 3700,
    "y_min": 300, "y_max": 3700,
    "mirror_x": True,
    "mirror_y": True,
}


class TSC2007:
    """
    Driver for the TSC2007 resistive touch controller.

    Parameters
    ----------
    i2c_bus     : I2C bus number (default 1)
    i2c_addr    : TSC2007 I2C address (default 0x48)
    irq_pin     : BCM GPIO for TSIRQ interrupt (default 4, active-low)
    display_w   : display width in pixels (default 320)
    display_h   : display height in pixels (default 240)
    cal_path    : path to calibration JSON file
    pressure_threshold : minimum z1 reading to count as a touch (default 200)
    t_convert   : seconds to wait between write and read (default 0.0001)
    """

    def __init__(
        self,
        i2c_bus:            int   = 1,
        i2c_addr:           int   = 0x48,
        irq_pin:            int   = 4,
        display_w:          int   = 320,
        display_h:          int   = 240,
        cal_path:           str   = _DEFAULT_CAL_PATH,
        pressure_threshold: int   = 200,
        t_convert:          float = 0.0001,
    ):
        self._bus_num   = i2c_bus
        self._addr      = i2c_addr
        self._irq       = irq_pin
        self._disp_w    = display_w
        self._disp_h    = display_h
        self._cal_path  = cal_path
        self._threshold = pressure_threshold
        self._t_conv    = t_convert

        self._bus       = None
        self._cal       = dict(_DEFAULT_CAL)
        self._initialised = False

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self):
        self.init()
        return self

    def __exit__(self, *_):
        self.cleanup()

    # ── Initialisation ────────────────────────────────────────────────────────

    def init(self):
        """
        Open I2C bus, configure TSIRQ pin, and load calibration.
        Verifies the TSC2007 is responding before returning.
        """
        if self._initialised:
            return

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self._irq, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        self._bus = smbus2.SMBus(self._bus_num)

        # Verify device is present
        try:
            self._read_channel(_CMD_X)
        except OSError as e:
            raise RuntimeError(
                f"TSC2007 not responding on I2C bus {self._bus_num} "
                f"address 0x{self._addr:02X}: {e}") from e

        self._load_cal()
        self._initialised = True

    def cleanup(self):
        """Close I2C bus and release GPIO."""
        if not self._initialised:
            return
        if self._bus:
            self._bus.close()
        # Do not call GPIO.cleanup() here — the display driver owns GPIO
        self._initialised = False

    # ── Low-level I2C ─────────────────────────────────────────────────────────

    def _read_channel(self, cmd: int) -> int:
        """
        Send one TSC2007 measurement command and return the 12-bit result.
        Write command byte, wait for ADC conversion, read 2 bytes.
        """
        wr = smbus2.i2c_msg.write(self._addr, [cmd])
        rd = smbus2.i2c_msg.read(self._addr, 2)
        self._bus.i2c_rdwr(wr)
        time.sleep(self._t_conv)
        self._bus.i2c_rdwr(rd)
        d = list(rd)
        return ((d[0] << 4) | (d[1] >> 4)) & 0xFFF

    # ── Public reading interface ──────────────────────────────────────────────

    def read_raw(self):
        """
        Return raw (x, y, z1) ADC values from the TSC2007.
        z1 > pressure_threshold indicates a touch is present.
        Returns (0, 0, 0) on I2C error.
        """
        try:
            x  = self._read_channel(_CMD_X)
            y  = self._read_channel(_CMD_Y)
            z1 = self._read_channel(_CMD_Z1)
            return x, y, z1
        except OSError:
            return 0, 0, 0

    @property
    def is_touched(self) -> bool:
        """True if TSIRQ pin is asserted (active-low)."""
        return GPIO.input(self._irq) == GPIO.LOW

    def position(self):
        """
        Return calibrated pixel coordinates (px, py) if the screen is
        being touched, or None if not.

        Applies calibration mapping and axis mirroring for display orientation.
        """
        if not self.is_touched:
            return None
        rx, ry, z1 = self.read_raw()
        if z1 < self._threshold:
            return None
        return self._to_pixels(rx, ry)

    def wait_for_touch(self, timeout: float = 0.0):
        """
        Block until a touch is detected and released.
        Returns the average pixel position during the press, or None on timeout.
        timeout=0 means wait indefinitely.
        """
        deadline = (time.monotonic() + timeout) if timeout > 0 else float("inf")
        while time.monotonic() < deadline:
            if self.is_touched:
                positions = []
                while self.is_touched:
                    pos = self.position()
                    if pos:
                        positions.append(pos)
                    time.sleep(0.02)
                if positions:
                    ax = sum(p[0] for p in positions) // len(positions)
                    ay = sum(p[1] for p in positions) // len(positions)
                    return ax, ay
            time.sleep(0.02)
        return None

    def track(self):
        """
        Generator that yields (px, py) positions while the finger is held.
        Use for drag operations.

        Example:
            for px, py in touch.track():
                widget.update(px, py)
        """
        while self.is_touched:
            pos = self.position()
            if pos:
                yield pos
            time.sleep(0.02)

    # ── Calibration ───────────────────────────────────────────────────────────

    def _to_pixels(self, rx: int, ry: int):
        """Map raw ADC values to pixel coordinates using stored calibration."""
        cal = self._cal
        px = int((rx - cal["x_min"]) /
                 (cal["x_max"] - cal["x_min"]) * self._disp_w)
        py = int((ry - cal["y_min"]) /
                 (cal["y_max"] - cal["y_min"]) * self._disp_h)
        px = max(0, min(self._disp_w  - 1, px))
        py = max(0, min(self._disp_h - 1, py))
        if cal.get("mirror_x", False):
            px = self._disp_w  - 1 - px
        if cal.get("mirror_y", False):
            py = self._disp_h - 1 - py
        return px, py

    def _load_cal(self):
        """Load calibration from JSON file. Falls back to defaults silently."""
        try:
            with open(self._cal_path) as f:
                loaded = json.load(f)
            self._cal.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass    # defaults remain in effect

    def save_cal(self):
        """Save current calibration to JSON file."""
        os.makedirs(os.path.dirname(self._cal_path), exist_ok=True)
        with open(self._cal_path, "w") as f:
            json.dump(self._cal, f, indent=2)

    def set_cal(self, x_min, x_max, y_min, y_max,
                mirror_x=True, mirror_y=True):
        """Set calibration values directly and save to file."""
        self._cal = {
            "x_min": x_min, "x_max": x_max,
            "y_min": y_min, "y_max": y_max,
            "mirror_x": mirror_x, "mirror_y": mirror_y,
        }
        self.save_cal()

    def calibrate(self, display):
        """
        Interactive 4-corner calibration routine.

        Draws a crosshair at each corner of the display and asks the user
        to tap it. Saves the resulting calibration to the JSON file.

        Parameters
        ----------
        display : ILI9341 instance — used to draw calibration targets

        Returns the calibration dict.
        """
        from ili9341 import ILI9341

        MARGIN = 20
        targets = [
            (MARGIN,                       MARGIN,                       "top-left"),
            (display.WIDTH - MARGIN - 1,   MARGIN,                       "top-right"),
            (display.WIDTH - MARGIN - 1,   display.HEIGHT - MARGIN - 1,  "bottom-right"),
            (MARGIN,                       display.HEIGHT - MARGIN - 1,  "bottom-left"),
        ]

        samples_x = []
        samples_y = []

        for tx, ty, label in targets:
            display.fill_screen(display.BLACK)
            display.draw_text(
                f"Tap {label}",
                Rect(0, display.HEIGHT // 2 - 20, display.WIDTH, 40),
                color=display.WHITE, size=18, align="center")
            self._draw_crosshair(display, tx, ty)

            print(f"Tap {label} ({tx}, {ty})...")

            # Wait for touch
            while not self.is_touched:
                time.sleep(0.05)
            time.sleep(0.05)    # debounce

            rx, ry, z1 = self.read_raw()
            samples_x.append(rx)
            samples_y.append(ry)
            print(f"  raw: ({rx}, {ry})  z1={z1}")

            while self.is_touched:
                time.sleep(0.05)
            time.sleep(0.1)

        x_min = min(samples_x)
        x_max = max(samples_x)
        y_min = min(samples_y)
        y_max = max(samples_y)

        print(f"\nCalibration: x=[{x_min},{x_max}] y=[{y_min},{y_max}]")
        self.set_cal(x_min, x_max, y_min, y_max,
                     mirror_x=True, mirror_y=True)

        display.fill_screen(display.BLACK)
        display.draw_text("Calibration saved.",
                          Rect(0, 100, display.WIDTH, 40),
                          color=display.GREEN, size=18, align="center")
        time.sleep(1.5)
        return self._cal

    @staticmethod
    def _draw_crosshair(display, cx: int, cy: int, size: int = 15):
        """Draw a small crosshair + circle at (cx, cy) on the display."""
        from ili9341 import ILI9341
        c = display.WHITE
        display.draw_line(cx - size, cy, cx + size, cy, c)
        display.draw_line(cx, cy - size, cx, cy + size, c)
        # Small circle approximated as a filled 5x5 rect
        display.draw_rect(Rect(cx - 3, cy - 3, 7, 7), display.RED)
