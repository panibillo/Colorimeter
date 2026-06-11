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
    PD=01  → ADC on, PENIRQ disabled  (used during X/Y/Z1 sequence)
    PD=00  → power-down, PENIRQ enabled  (issued after each read sequence)
    X measurement : 0xC4  (A3:A0=1100, PD=01)
    Y measurement : 0xD4  (A3:A0=1101, PD=01)
    Z1 pressure   : 0xE4  (A3:A0=1110, PD=01)
    Z2 pressure   : 0xF4
    End-of-read   : 0x00  (TEMP0, PD=00) — re-enables PENIRQ

  TSIRQ pin (GPIO4, active-low hardware):
    The TSC2007 pulls TSIRQ low when the panel is touched.
    gpiozero DigitalInputDevice(pull_up=True) inverts this: value=1 means
    the pin is pulled low (touched), value=0 means floating high (not touched).
    So is_touched checks value == 1, NOT value == 0.

  Axis orientation (Adafruit 1651 shield, confirmed by probe):
    Raw X channel tracks vertical position → maps to display Y
    Raw Y channel tracks horizontal position (inverted) → maps to display X
    Observed panel raw ranges: X ~808–926, Y ~2417–2532 (~115 counts each)
    Z1 > 100 during real touches; gate position reads on Z1 and TSIRQ together.

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

import logging
import time
import json
import os
import smbus2
from gpiozero import DigitalInputDevice

from geometry import Rect
from utils import install_exit_signals

log = logging.getLogger(__name__)


# ── Default config path ───────────────────────────────────────────────────────
_DEFAULT_CAL_PATH = os.path.expanduser(
    "~/pi/Colorimeter/config/touch_cal.json")

# ── TSC2007 measurement commands ──────────────────────────────────────────────
# PD=01 (0x04) keeps the ADC on and disables PENIRQ during the read sequence.
# _CMD_END (PD=00) powers down and re-enables PENIRQ after all three channels.
_CMD_X   = 0xC4   # X position,  PD=01 (ADC on, PENIRQ off)
_CMD_Y   = 0xD4   # Y position,  PD=01
_CMD_Z1  = 0xE4   # Z1 pressure, PD=01
_CMD_END = 0x00   # TEMP0, PD=00 — power-down and re-enable PENIRQ

# ── Noise / stability filter ──────────────────────────────────────────────────
# Two consecutive reads of the same channel must agree before the value is
# accepted: byte0 identical, |byte1_a − byte1_b| ≤ _STAB_TOL.
# _STAB_TOL=32 on byte1 corresponds to ≤2 counts in the 12-bit result.
_STAB_RETRIES = 12   # max attempts to get a stable pair
_STAB_TOL     = 32   # byte1 tolerance

# ── Minimum Z1 for a valid touch read ────────────────────────────────────────
_Z1_MIN = 10

# ── Default affine calibration ────────────────────────────────────────────────
# Derived from probe data: raw X → display Y (axis swap), raw Y inverted →
# display X.  Panel range ~115 counts; display 320×240.
# Run calibrate() for panel-specific accurate results.
#   px = a*rx + b*ry + c
#   py = d*rx + e*ry + f
# _DEFAULT_CAL = {
    # "a":  2.771,  "b": -2.402,  "c":  3790.4,
    # "d":  5.601,  "e":  1.779,  "f": -8955.7,
    # "threshold": 1,
# }
# DEFAULT_CAL from raw_pts={"TL":(500,3430),"TR":(570,265),"BR":(3460,442),"BL":(3480,3510)}
_DEFAULT_CAL = {
    "a": 0.0023650832335519894,
    "b": -0.08809935044981086,
    "c": 320.9982304260752,
    "d": 0.06673889777796616,
    "e": 0.0014760577707607044,
    "f": -18.432327042692293,
    "threshold": 3
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
    irq_pin     : BCM GPIO for TSIRQ interrupt (default 4)
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

        install_exit_signals()
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
        log.info("TSC2007 ready on I2C bus %d addr 0x%02X", self._bus_num, self._addr)

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

        Reads pairs until two consecutive reads agree (byte0 equal,
        |byte1 diff| ≤ _STAB_TOL), up to _STAB_RETRIES attempts.
        Returns the last reading if the limit is reached without agreement.
        """
        def _once():
            wr = smbus2.i2c_msg.write(self._addr, [cmd])
            rd = smbus2.i2c_msg.read(self._addr, 2)
            self._bus.i2c_rdwr(wr)
            time.sleep(self._t_conv)
            self._bus.i2c_rdwr(rd)
            return list(rd)

        prev = _once()
        for _ in range(_STAB_RETRIES - 1):
            curr = _once()
            if cmd == _CMD_END:
                return 0
            if prev[0] == curr[0] and abs(prev[1] - curr[1]) <= _STAB_TOL:
                return ((curr[0] << 4) | (curr[1] >> 4)) & 0xFFF
            prev = curr
        return ((prev[0] << 4) | (prev[1] >> 4)) & 0xFFF

    # ── Public reading interface ──────────────────────────────────────────────

    # Minimum z1 pressure for a reading to be accepted by read_raw().
    _Z1_ACCEPT = 40
    # Maximum wall-clock time (s) read_raw() will spend collecting samples.
    _READ_TIMEOUT = 0.3

    def read_raw(self):
        """
        Return raw (x, y, z1) ADC values from the TSC2007.

        Collects up to 3 readings where z1 > _Z1_ACCEPT, stopping early if
        _READ_TIMEOUT seconds elapse.  Returns the median of each channel
        across accepted readings.  If no reading passes the z1 threshold,
        returns the last reading taken regardless.

        Logs elapsed time from the first read to the last accepted reading.
        Returns (0, 0, 0) on I2C error.
        """
        try:
            x  = self._read_channel(_CMD_X)
            y  = self._read_channel(_CMD_Y)
            z1 = self._read_channel(_CMD_Z1)
            log.info("x=%4d  y=%4d  z1=%3d", x, y, z1)    
        except OSError:
            log.warning("I2C read failed in read_raw()")
            return 0, 0, 0
        finally:
            self._read_channel(_CMD_END)

    @property
    def is_touched(self) -> bool:
        """True when TSIRQ is asserted.
        gpiozero DigitalInputDevice(pull_up=True) reports value=1 when the
        TSC2007 pulls the pin low (touched), 0 when floating high (not touched).
        """
        return self._irq_dev.value == 1

    def position(self):
        """
        Return calibrated pixel coordinates (px, py) if the screen is
        being touched, or None if not.
        Requires both TSIRQ asserted and Z1 ≥ _Z1_MIN (100) to filter
        marginal or noisy reads.
        """
        if not self.is_touched:
            return None
        rx, ry, z1 = self.read_raw()
        if z1 < _Z1_MIN:
            log.debug("position: z1=%d below threshold — ignored", z1)
            return None
        px, py = self._to_pixels(rx, ry)
        log.debug("position: raw(%d,%d) z1=%d → px(%d,%d)", rx, ry, z1, px, py)
        return px, py

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
        x = max(0, min(self._disp_w - 1, round(px)))
        y = max(0, min(self._disp_h - 1, round(py)))
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
                log.info("cal loaded from %s  threshold=%d", self._cal_path, self._threshold)
            else:
                log.warning("cal file missing affine keys — using defaults. Run calibrate().")
        except (OSError, json.JSONDecodeError):
            log.info("no cal file found at %s — using defaults", self._cal_path)

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

    def calibrate(self, display, save: bool = True):
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

        Parameters
        ----------
        display : ILI9341 instance
        save    : if True (default), write calibration to JSON immediately.
                  Pass False to let the caller validate and save manually.

        Returns
        -------
        (cal_dict, err_x, err_y) on success, or None if the affine solve
        failed (collinear / coincident touch points).
        err_x, err_y are the BR verification residuals in display pixels.
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

        if save:
            self.save_cal()
            display.fill_screen(display.BLACK)
            display.draw_text("Calibration saved.",
                              Rect(0, 100, W, 40),
                              color=display.GREEN, size=18, align="center")
            time.sleep(1.5)

        return self._cal, err_x, err_y

    @staticmethod
    def _draw_crosshair(display, cx: int, cy: int, size: int = 15):
        """Draw a crosshair and small square at (cx, cy) on the display."""
        c = display.WHITE
        display.draw_line(cx - size, cy, cx + size, cy, c)
        display.draw_line(cx, cy - size, cx, cy + size, c)
        display.draw_rect(Rect(cx - 3, cy - 3, 7, 7), display.RED)
