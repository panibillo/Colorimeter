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

  Command byte format: [A3 A2 A1 A0 PD1 PD0 M 0]
    M=0    → 12-bit resolution
    PD=11  → device on, PENIRQ disabled  (used during X/Y/Z1 sequence)
    PD=00  → power-down, PENIRQ enabled  (issued after each read sequence)
    X measurement : 0xCC  (A3:A0=1100, PD=11)
    Y measurement : 0xDC  (A3:A0=1101, PD=11)
    Z1 pressure   : 0xEC  (A3:A0=1110, PD=11)
    Z2 pressure   : 0xF0
    End-of-read   : 0x00  (TEMP0, PD=00) — re-enables PENIRQ

  Response: [D11:D4] [D3:D0 x x x x] → 12-bit value in upper 12 bits.

  Measuring Z=R_touch uses Z1, Z2, X, Y, Rx, Ry 
    where X = x posistion, and Rx = x resistance. same for y
  Method 1 requires Z1 and Z2
    Z = R_touch = R_x * X_position/4096*(Z2/Z1 - 1)
  Method 2 requires Z1 only
    Z = R_touch = (Rx * X)

Calibration:
  calibrate(display) runs a two-phase routine:
    Phase 1 — 10 free touches establish the z1 pressure threshold.
    Phase 2 — 4 corner taps produce an affine transform (6 coefficients)
              that maps any raw (rx, ry) to display pixel (px, py).
  Results are stored in ~/pi/Colorimeter/config/touch_cal.json.
  The affine transform handles any combination of axis orientation,
  mirroring, and scaling without requiring manual axis configuration.

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
import numpy as np

try:
    import smbus2
    from gpiozero import DigitalInputDevice
except:
    import mock_smbus2 as smbus2
    import mock_DigitalInputDevice as DigitalInputDevice

from geometry import Rect

DBG = True

# ── Default config path ───────────────────────────────────────────────────────
_DEFAULT_CAL_PATH = os.path.expanduser(
    "~/pi/Colorimeter/config/touch_cal.json")

# ── TSC2007 measurement commands ──────────────────────────────────────────────
# Constants copied from adafruit_tsc2007 copyright ladyada, SPDX-License-Identifier: MIT  
TSC2007_MEASURE_TEMP0 = 0
TSC2007_MEASURE_AUX = 2
TSC2007_MEASURE_TEMP1 = 4
TSC2007_ACTIVATE_X = 8
TSC2007_ACTIVATE_Y = 9
TSC2007_ACTIVATE_YPLUS_X = 10
TSC2007_SETUP_COMMAND = 11
TSC2007_MEASURE_X = 12
TSC2007_MEASURE_Y = 13
TSC2007_MEASURE_Z1 = 14
TSC2007_MEASURE_Z2 = 15

TSC2007_POWERDOWN_IRQON = 0
TSC2007_ADON_IRQOFF = 1
TSC2007_ADOFF_IRQON = 2

TSC2007_ADC_12BIT = 0
TSC2007_ADC_8BIT = 1

# PD=11 (0x0C) keeps the device on and disables PENIRQ during the read
# sequence; _CMD_END (PD=00) re-enables PENIRQ after all three channels.
# _CMD_X   = 0xCC   # X position,  PD=11 (device on, PENIRQ off)
# _CMD_Y   = 0xDC   # Y position,  PD=11
# _CMD_Z1  = 0xEC   # Z1 pressure, PD=11
# _CMD_END = 0x00   # TEMP0, PD=00 — power-down and re-enable PENIRQ


_CMD_X = ((TSC2007_MEASURE_X & 0x0F) << 4) | ((TSC2007_ADON_IRQOFF & 0x03) << 2) | ((TSC2007_ADC_12BIT & 0x01) << 1)
_CMD_Y = ((TSC2007_MEASURE_Y & 0x0F) << 4) | ((TSC2007_ADON_IRQOFF & 0x03) << 2) | ((TSC2007_ADC_12BIT & 0x01) << 1)
_CMD_Z1 = ((TSC2007_MEASURE_Z1 & 0x0F) << 4) | ((TSC2007_ADON_IRQOFF & 0x03) << 2) | ((TSC2007_ADC_12BIT & 0x01) << 1)
_CMD_END = ((TSC2007_MEASURE_TEMP0 & 0x0F) << 4) | ((TSC2007_POWERDOWN_IRQON & 0x03) << 2) | ((TSC2007_ADC_12BIT & 0x01) << 1)
        # self._cmd[0] |= (power & 0x03) << 2
        # self._cmd[0] |= (resolution & 0x01) << 1



# ── Default affine calibration ────────────────────────────────────────────────
# Corresponds to: raw X → display X, both axes mirrored, raw range [300,3700],
# display 320×240.  These are approximations only — run calibrate() for
# accurate results.
#   px = a*rx + b*ry + c
#   py = d*rx + e*ry + f
_DEFAULT_CAL = {
    "a": -0.09412, "b":  0.0,      "c": 347.24,
    "d":  0.0,     "e": -0.07059,  "f": 260.18,
    "threshold": 50,
}


def _solve3(M, b):
    """
    Solve the 3×3 linear system M·x = b using Gaussian elimination
    with partial pivoting.

    Args:
        M : list of 3 rows, each a list of 3 floats
        b : list of 3 right-hand-side values

    Returns:
        Tuple (x0, x1, x2) — the solution.

    Raises:
        ValueError: If M is singular (calibration points are collinear).
    """
    aug = [list(M[i]) + [float(b[i])] for i in range(3)]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(aug[r][col]))
        aug[col], aug[piv] = aug[piv], aug[col]
        if abs(aug[col][col]) < 1e-9:
            raise ValueError(
                "Singular matrix in affine solve — "
                "calibration points may be collinear or coincident")
        for row in range(col + 1, 3):
            f = aug[row][col] / aug[col][col]
            for k in range(col, 4):
                aug[row][k] -= f * aug[col][k]
    x = [0.0] * 3
    for i in range(2, -1, -1):
        x[i] = aug[i][3]
        for j in range(i + 1, 3):
            x[i] -= aug[i][j] * x[j]
        x[i] /= aug[i][i]
    return tuple(x)


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
    pressure_threshold : fallback z1 threshold used before calibrate() has
        been run (default 50).  After calibrate(), the measured threshold
        is loaded from the JSON file and this parameter is ignored.
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
        pressure_threshold: int   = 50,
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
        self._irq_dev   = None
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

        self._irq_dev = DigitalInputDevice(self._irq, pull_up=True)
        self._bus = smbus2.SMBus(self._bus_num)

        try:
            self._read_channel(_CMD_X)
            self._read_channel(_CMD_END)
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
        if self._irq_dev:
            self._irq_dev.close()
            self._irq_dev = None
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
        return ((d[0] << 4) | (d[1] >> 4)) & 0xFFF, d

    # ── Public reading interface ──────────────────────────────────────────────

    def read_raw(self):
        """
        Return raw (x, y, z1) ADC values from the TSC2007.
        Ends the sequence with _CMD_END to re-enable PENIRQ.
        Returns (0, 0, 0) on I2C error.
        """
        R = lambda v: f"{v[0]:08b}:{v[1]:08b}"
        try:
            if DBG: x ,rx= self._read_channel(_CMD_X)
            if DBG: print(f"read_raw1 x({_CMD_X})  {x}  [{R(rx)}]")
            if DBG: x ,rx= self._read_channel(0xCC)
            if DBG: print(f"read_raw2 x({0xCC})  {x}  [{R(rx)}]")
            x ,rx= self._read_channel(_CMD_X)
            y ,ry = self._read_channel(_CMD_Y)
            z1,rz = self._read_channel(_CMD_Z1)
            self._read_channel(_CMD_END)
            if DBG: print(f"read_raw  ({x:5d}  {y:5d}  {z1:5d}) <- [{R(rx)}  {R(ry)}  {R(rz)}]")
            return x, y, z1
        except OSError:
            return 0, 0, 0

    @property
    def is_touched(self) -> bool:
        """True if TSIRQ pin is asserted (active-low)."""
        return self._irq_dev.value == 0

    def position(self):
        """
        Return calibrated pixel coordinates (px, py) if the screen is
        being touched, or None if not.
        Touch detection relies solely on the TSIRQ pin (is_touched);
        z1 is not used to gate position reads.
        """
        if not self.is_touched:
            return None
        rx, ry, _ = self.read_raw()
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

    # ── Coordinate mapping ────────────────────────────────────────────────────

    def _to_pixels(self, rx: int, ry: int):
        """
        Apply the affine transform to map raw ADC values to display pixels.

        px = a*rx + b*ry + c
        py = d*rx + e*ry + f
        """
        cal = self._cal
        px = cal["a"] * rx + cal["b"] * ry + cal["c"]
        py = cal["d"] * rx + cal["e"] * ry + cal["f"]
        x, y = (max(0, min(self._disp_w  - 1, round(px))),
                max(0, min(self._disp_h - 1, round(py))))
        if DBG: print(f"_to_pixl  ({x}  {y}) <- ({px}, {py}) <- [{rx}  {ry}]", type(x), type(px), type(rx))
        return (x, y)

    # ── Calibration persistence ───────────────────────────────────────────────

    def _load_cal(self):
        """
        Load calibration from JSON file.  Falls back to defaults silently
        if the file is missing, unreadable, or lacks affine keys.
        """
        try:
            with open(self._cal_path) as f:
                loaded = json.load(f)
            required = {"a", "b", "c", "d", "e", "f"}
            if required.issubset(loaded.keys()):
                self._cal.update(loaded)
                if "threshold" in loaded:
                    self._threshold = int(loaded["threshold"])
            else:
                print("tsc2007: cal file missing affine keys — "
                      "using defaults. Run calibrate().")
        except (OSError, json.JSONDecodeError):
            pass

    def save_cal(self):
        """Save current calibration (affine coefficients + threshold) to JSON."""
        os.makedirs(os.path.dirname(self._cal_path), exist_ok=True)
        with open(self._cal_path, "w") as f:
            json.dump(self._cal, f, indent=2)

    def set_transform(self, a, b, c, d, e, f, threshold=50):
        """
        Set affine transform coefficients and threshold directly and save.

        px = a*rx + b*ry + c
        py = d*rx + e*ry + f
        """
        self._threshold = threshold
        self._cal = {
            "a": a, "b": b, "c": c,
            "d": d, "e": e, "f": f,
            "threshold": threshold,
        }
        self.save_cal()

    # ── Calibration routine ───────────────────────────────────────────────────

    def calibrate(self, display):
        """
        Two-phase interactive calibration routine.

        Phase 1 — Z threshold:
            Asks for 10 touches anywhere with typical (lightest normal)
            pressure.  Prints raw (x, y, z1) for each touch.  Sets the
            z1 threshold to the minimum z1 observed.

        Phase 2 — Affine transform:
            Draws a crosshair at each of 4 display corners and asks the
            user to tap it.  Collects up to 8 averaged readings per corner.
            Uses TL, TR, BL for an exact 3-point affine solve; verifies
            with BR and prints the residual error.

        Saves calibration to JSON and returns the calibration dict.

        Parameters
        ----------
        display : ILI9341 instance
        """
        W      = display.WIDTH
        H      = display.HEIGHT
        MARGIN = 20

        # ── Phase 1: Z threshold ──────────────────────────────────────────────
        display.fill_screen(display.BLACK)
        display.draw_text("Phase 1: Pressure",
                          Rect(0, 15, W, 35),
                          color=display.CYAN, size=20, align="center")
        display.draw_text("Tap anywhere 10x",
                          Rect(0, 60, W, 28),
                          color=display.WHITE, size=16, align="center")
        display.draw_text("lightest normal touch",
                          Rect(0, 90, W, 28),
                          color=display.LTGRAY, size=14, align="center")

        print("\n── Phase 1: Z threshold ─────────────────────────────────")
        print("  Tap anywhere 10 times with your lightest normal pressure.")
        print("  raw X    raw Y    z1")

        z_samples = []
        for i in range(1, 11):
            display.draw_text(f"{i}/10",
                              Rect(0, 140, W, 40),
                              color=display.YELLOW, size=28, align="center")
            while not self.is_touched:
                time.sleep(0.02)
            time.sleep(0.05)    # debounce
            rx, ry, z1 = self.read_raw()
            print(f"ztest  {rx:5d}    {ry:5d}    {z1:4d}")
            z_samples.append(z1)
            while self.is_touched:
                time.sleep(0.02)
            time.sleep(0.15)    # lift-off settle

        threshold = max(1, min(z_samples))
        self._threshold = threshold
        print(f"  z1 values : {z_samples}")
        print(f"  Threshold : {threshold}  (informational — position() uses TSIRQ pin)")

        # ── Phase 2: Affine calibration ───────────────────────────────────────
        corners = [
            ("TL", MARGIN,     MARGIN    ),
            ("TR", W-MARGIN-1, MARGIN    ),
            ("BR", W-MARGIN-1, H-MARGIN-1),
            ("BL", MARGIN,     H-MARGIN-1),
        ]

        raw_pts = {}    # label → (avg_rx, avg_ry)

        print("\n── Phase 2: Corner taps ─────────────────────────────────")
        for label, tx, ty in corners:
            display.fill_screen(display.BLACK)
            display.draw_text(f"Tap  {label}",
                              Rect(0, H // 2 - 24, W, 40),
                              color=display.WHITE, size=20, align="center")
            self._draw_crosshair(display, tx, ty)
            print(f"  Tap {label} at display ({tx},{ty}) ...")

            while not self.is_touched:
                time.sleep(0.05)
            time.sleep(0.05)

            readings = []
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline and self.is_touched:
                rx, ry, _ = self.read_raw()
                readings.append((rx, ry))
                time.sleep(0.02)

            if not readings:
                rx, ry, _ = self.read_raw()
                readings = [(rx, ry)]

            avg_rx = sum(r[0] for r in readings) // len(readings)
            avg_ry = sum(r[1] for r in readings) // len(readings)
            raw_pts[label] = (avg_rx, avg_ry)
            print(f"    raw=({avg_rx:4d},{avg_ry:4d})  ({len(readings)} samples)")

            while self.is_touched:
                time.sleep(0.05)
            time.sleep(0.10)

        # ── Solve affine from TL, TR, BL; verify with BR ──────────────────────
        #
        # px = a*rx + b*ry + c     py = d*rx + e*ry + f
        #
        # 3-point exact solution using TL, TR, BL:
        A = [
            [raw_pts["TL"][0], raw_pts["TL"][1], 1],
            [raw_pts["TR"][0], raw_pts["TR"][1], 1],
            [raw_pts["BL"][0], raw_pts["BL"][1], 1],
        ]
        tx3 = [float(MARGIN),     float(W - MARGIN - 1), float(MARGIN)    ]
        ty3 = [float(MARGIN),     float(MARGIN),          float(H - MARGIN - 1)]

        try:
            a, b, c = _solve3(A, tx3)
            d, e, f = _solve3(A, ty3)
        except ValueError as err:
            print(f"  ERROR: {err}")
            print("  Calibration aborted — please retry.")
            return None

        # Verify with BR
        rx_br, ry_br = raw_pts["BR"]
        px_pred = a * rx_br + b * ry_br + c
        py_pred = d * rx_br + e * ry_br + f
        tgt_br  = (W - MARGIN - 1, H - MARGIN - 1)
        err_x   = px_pred - tgt_br[0]
        err_y   = py_pred - tgt_br[1]

        print(f"\n  Affine X:  a={a:+.5f}  b={b:+.5f}  c={c:+.2f}")
        print(f"  Affine Y:  d={d:+.5f}  e={e:+.5f}  f={f:+.2f}")
        print(f"  BR check:  predicted ({px_pred:.1f},{py_pred:.1f})"
              f"  target {tgt_br}"
              f"  error ({err_x:+.1f},{err_y:+.1f})")

        self._cal = {
            "a": a, "b": b, "c": c,
            "d": d, "e": e, "f": f,
            "threshold": threshold,
        }
        self.save_cal()

        display.fill_screen(display.BLACK)
        display.draw_text("Calibration saved.",
                          Rect(0, 100, W, 40),
                          color=display.GREEN, size=18, align="center")
        time.sleep(1.5)
        return self._cal

    @staticmethod
    def _draw_crosshair(display, cx: int, cy: int, size: int = 15):
        """Draw a crosshair and small square at (cx, cy) on the display."""
        c = display.WHITE
        display.draw_line(cx - size, cy, cx + size, cy, c)
        display.draw_line(cx, cy - size, cx, cy + size, c)
        display.draw_rect(Rect(cx - 3, cy - 3, 7, 7), display.RED)
