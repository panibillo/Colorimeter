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
    A touch counts when TSIRQ is asserted AND Z1 > _Z1_MIN (10).  There is no
    other pressure threshold.

  Response: [D11:D4] [D3:D0 x x x x] → 12-bit value in upper 12 bits.

  Measuring Z=R_touch uses Z1, Z2, X, Y, Rx, Ry 
    where X = x posistion, and Rx = x resistance. same for y
  Method 1 requires Z1 and Z2
    Z = R_touch = R_x * X_position/4096*(Z2/Z1 - 1)
  Method 2 requires Z1 only
    Z = R_touch = (Rx * X)

Calibration:
  Taps on 4 corner crosshairs produce an affine transform (6 coefficients)
  that maps any raw (rx, ry) to display pixel (px, py).  TL, TR and BL give
  an exact solve; BR checks it.  The affine transform handles any combination
  of axis orientation, mirroring, and scaling without manual axis settings.
  The results live in ~/pi/Colorimeter/config/touch_cal.json, which is only
  ever replaced through touch_cal_store.save() (backup first, atomic write).

  Two ways to run it:
    Menu → Advanced → Calibrate Touchpad   (the app; tries before it saves)
    src/calibrate_screen.py                (command line, calls calibrate())
  Both use corner_targets(), average_raw() and solve_affine() below.

Usage:
    from tsc2007 import TSC2007
    touch = TSC2007()
    touch.init()
    pos = touch.position()      # returns (px, py) or None
    touch.cleanup()
"""

import logging
import os
import time

import touch_cal_store
from geometry import Rect
from utils import install_exit_signals

# Hardware-only imports.  Guarded so this module stays importable on a
# desktop for testing; init() raises a clear error if actually used
# without them.  On a Pi both are present and nothing changes.
try:
    import smbus2
except ImportError:                                  # pragma: no cover
    smbus2 = None
try:
    from gpiozero import DigitalInputDevice
except ImportError:                                  # pragma: no cover
    DigitalInputDevice = None

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
# read_raw() compares consecutive (x, y, z1) triples.  A pair is accepted when
# |dx| < _STAB_TOL and |dy| < _STAB_TOL and z1 > _Z1_MIN.
# _STAB_TOL=32 in 12-bit counts; panel range ~115 counts.
_STAB_RETRIES = 12   # max (x,y,z1) reads before declaring "not touched"
_STAB_TOL     = 32   # max channel-to-channel delta to accept a pair

# ── Minimum Z1 for a valid touch read ────────────────────────────────────────
_Z1_MIN = 10

# ── Default affine calibration ────────────────────────────────────────────────
# Used only when there is no usable calibration file: good enough to press a
# button with.  Run a calibration for panel-specific accurate results.
#   px = a*rx + b*ry + c
#   py = d*rx + e*ry + f
# From raw_pts={"TL":(500,3430),"TR":(570,265),"BR":(3460,442),"BL":(3480,3510)}
_DEFAULT_CAL = {
    "a": 0.0023650832335519894,
    "b": -0.08809935044981086,
    "c": 320.9982304260752,
    "d": 0.06673889777796616,
    "e": 0.0014760577707607044,
    "f": -18.432327042692293,
}

# ── Corner calibration ────────────────────────────────────────────────────────
CAL_MARGIN = 20        # crosshair distance from the screen edges, pixels
CAL_MAX_ERR_PX = 10    # largest BR check error a calibration may have


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


def corner_targets(w: int, h: int, margin: int = CAL_MARGIN):
    """
    The four crosshair positions, in the order they are tapped.

    Returns:
        [(label, x, y), ...] for TL, TR, BR, BL.

    Example:
        >>> corner_targets(320, 240)[2]
        ('BR', 299, 219)
    """
    return [("TL", margin,         margin),
            ("TR", w - margin - 1, margin),
            ("BR", w - margin - 1, h - margin - 1),
            ("BL", margin,         h - margin - 1)]


def average_raw(readings):
    """
    The mean raw (rx, ry) of one tap, or None if no reading was a touch.

    read_raw() reports (0, 0, 0) when it found no steady pair; those are
    dropped rather than averaged in, since one of them drags the mean far
    toward the raw origin.

    Example:
        >>> average_raw([(900, 2500, 40), (0, 0, 0), (910, 2510, 42)])
        (905, 2505)
    """
    good = [(x, y) for x, y, z1 in readings if z1 > _Z1_MIN]
    if not good:
        return None
    return (sum(x for x, _ in good) // len(good),
            sum(y for _, y in good) // len(good))


def solve_affine(raw_pts: dict, w: int, h: int, margin: int = CAL_MARGIN):
    """
    The affine transform from four corner taps, and how well it fits.

    TL, TR and BL fix the six coefficients exactly; BR is the check.

    Args:
        raw_pts: {"TL": (rx, ry), "TR": ..., "BR": ..., "BL": ...}
        w, h:    display size in pixels.
        margin:  the crosshair margin the taps were made with.

    Returns:
        (cal, err_x, err_y): cal is {"a".."f"}; err_x, err_y are where the
        transform puts the BR tap minus where BR's crosshair was, in pixels.

    Raises:
        ValueError: the taps are collinear or coincident.
    """
    tgt = {label: (x, y) for label, x, y in corner_targets(w, h, margin)}
    A = [[raw_pts[k][0], raw_pts[k][1], 1] for k in ("TL", "TR", "BL")]
    a, b, c = _solve3(A, [tgt[k][0] for k in ("TL", "TR", "BL")])
    d, e, f = _solve3(A, [tgt[k][1] for k in ("TL", "TR", "BL")])
    rx, ry = raw_pts["BR"]
    err_x = a * rx + b * ry + c - tgt["BR"][0]
    err_y = d * rx + e * ry + f - tgt["BR"][1]
    return {"a": a, "b": b, "c": c, "d": d, "e": e, "f": f}, err_x, err_y


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
        t_convert:          float = 0.0001,
    ):
        self._bus_num   = i2c_bus
        self._addr      = i2c_addr
        self._irq       = irq_pin
        self._disp_w    = display_w
        self._disp_h    = display_h
        self._cal_path  = cal_path
        self._t_conv    = t_convert

        self._bus       = None
        self._irq_dev   = None
        self._cal       = dict(_DEFAULT_CAL)
        # False until _load_cal finds a file with a full affine set.  The
        # defaults are a guess good enough to press a button with, not a
        # calibration; the boot-time check reports the difference.
        self._calibrated = False
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

        missing = [n for n, mod in (("smbus2", smbus2),
                                    ("gpiozero", DigitalInputDevice))
                   if mod is None]
        if missing:
            raise RuntimeError(
                f"TSC2007 needs {' and '.join(missing)}, which "
                f"{'is' if len(missing) == 1 else 'are'} not installed. "
                "This driver only runs on the Pi; set COLORIMETER_SIMULATION=1 "
                "to use the mock touch driver instead.")

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
        """Send one TSC2007 command and return the 12-bit result (single read)."""
        wr = smbus2.i2c_msg.write(self._addr, [cmd])
        rd = smbus2.i2c_msg.read(self._addr, 2)
        self._bus.i2c_rdwr(wr)
        time.sleep(self._t_conv)
        self._bus.i2c_rdwr(rd)
        if cmd == _CMD_END:
            return 0
        b = list(rd)
        return ((b[0] << 4) | (b[1] >> 4)) & 0xFFF

    # ── Public reading interface ──────────────────────────────────────────────

    def read_raw(self):
        """
        Return stable (x, y, z1) ADC values, or (0, 0, 0) if none found.

        Takes an initial reading as prev, then loops up to _STAB_RETRIES more
        times.  A pair is accepted when |dx| < _STAB_TOL, |dy| < _STAB_TOL,
        and z1 > _Z1_MIN.  Returns the mean of the accepted pair.
        Falls through to (0, 0, 0) after _STAB_RETRIES — interpreted as not touched.
        Read with _CMD_END powers down the sensor after the final reading.
        """
        try:
            t_start = time.monotonic()
            px = self._read_channel(_CMD_X)
            py = self._read_channel(_CMD_Y)
            pz1 = self._read_channel(_CMD_Z1)
            log.debug("raw[0]  x=%4d  y=%4d  z1=%3d", px, py, pz1)

            for i in range(1, _STAB_RETRIES + 1):
                cx = self._read_channel(_CMD_X)
                cy = self._read_channel(_CMD_Y)
                cz1 = self._read_channel(_CMD_Z1)
                
                dx, dy = abs(cx - px), abs(cy - py)
                log.debug("raw[%d]  x=%4d  y=%4d  z1=%3d  dx=%d  dy=%d",
                          i, cx, cy, cz1, dx, dy)
                if dx < _STAB_TOL and dy < _STAB_TOL and cz1 > _Z1_MIN:
                    mx, my, mz1 = (px + cx) // 2, (py + cy) // 2, (pz1 + cz1) // 2
                    log.debug("stable  x=%4d  y=%4d  z1=%3d  elapsed=%.3fs  iters=%d",
                              mx, my, mz1, time.monotonic() - t_start, i + 1)
                    return mx, my, mz1
                px, py, pz1 = cx, cy, cz1

            log.debug("read_raw: no stable pair in %d retries — not touched", _STAB_RETRIES)
            return 0, 0, 0
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
        Requires both TSIRQ asserted and Z1 ≥ _Z1_MIN (10) to filter
        marginal or noisy reads.
        """
        if not self.is_touched:
            return None
        rx, ry, z1 = self.read_raw()
        if z1 < _Z1_MIN:
            log.debug("position: z1=%d below _Z1_MIN — ignored", z1)
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
        Load calibration from the JSON file.  Falls back to the defaults if the
        file is missing, unreadable, or lacks any of the six numbers.  Any
        other keys in the file (an old 'threshold') are ignored.
        """
        loaded = touch_cal_store.load(self._cal_path)
        if loaded is not None:
            self._cal = loaded
            self._calibrated = True
            log.info("cal loaded from %s", self._cal_path)
        elif os.path.exists(self._cal_path):
            log.warning("cal file %s unusable — using defaults", self._cal_path)
        else:
            log.info("no cal file found at %s — using defaults", self._cal_path)

    @property
    def calibrated(self) -> bool:
        """
        True if this driver is running on a saved calibration.

        False means _load_cal fell back to the built-in affine defaults —
        touch still works, but positions are only approximately right.  Ask
        the driver rather than looking for the file: only it knows whether
        what it found was usable.

        Example:
            >>> TSC2007().calibrated                 # doctest: +SKIP
            True
        """
        return self._calibrated

    @property
    def cal(self) -> dict:
        """A copy of the transform in use: {"a".."f"}."""
        return dict(self._cal)

    @property
    def cal_path(self) -> str:
        """The calibration file this driver loads from and saves to."""
        return self._cal_path

    def apply_cal(self, cal: dict) -> None:
        """
        Use `cal` for position() from now on, in memory only.  The file and
        `calibrated` are untouched; save_cal() writes it.  This is what lets a
        new calibration be tried, and the old one put back, without any risk
        to the file.
        """
        self._cal = {k: float(cal[k]) for k in touch_cal_store.AFFINE_KEYS}

    def save_cal(self):
        """
        Write the transform in use to the calibration file, keeping a backup
        of the old file.  See touch_cal_store.save().

        Returns:
            The backup's path, or None if there was no file before.

        Raises:
            touch_cal_store.CalWriteError: the file is unchanged.
        """
        bak = touch_cal_store.save(self._cal, self._cal_path)
        self._calibrated = True
        return bak

    # ── Calibration routine ───────────────────────────────────────────────────

    def calibrate(self, display, save: bool = True):
        """
        Interactive corner calibration (blocking; for the command line).

        Draws a crosshair at each of 4 display corners and asks the user to
        tap it, averaging up to 0.8 s of readings per tap.  Uses TL, TR, BL
        for an exact 3-point affine solve; verifies with BR and prints the
        residual error.

        The transform in use is not changed unless `save` is True.

        Parameters
        ----------
        display : ILI9341 instance
        save    : if True (default), use and save the result at once (the old
                  file is backed up).  Pass False to let the caller validate
                  and save.

        Returns
        -------
        (cal_dict, err_x, err_y) on success, or None if a corner got no
        reading or the affine solve failed (collinear / coincident taps).
        err_x, err_y are the BR verification residuals in display pixels.
        """
        W = display.WIDTH
        H = display.HEIGHT

        raw_pts = {}    # label → (avg_rx, avg_ry)

        print("\n── Corner taps ──────────────────────────────────────────")
        for label, tx, ty in corner_targets(W, H):
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
                readings.append(self.read_raw())
                time.sleep(0.02)
            if not readings:
                readings = [self.read_raw()]

            avg = average_raw(readings)
            if avg is None:
                print("  ERROR: no steady reading — press firmer and retry.")
                return None
            raw_pts[label] = avg
            print(f"    raw=({avg[0]:4d},{avg[1]:4d})  ({len(readings)} samples)")

            while self.is_touched:
                time.sleep(0.05)
            time.sleep(0.10)

        try:
            cal, err_x, err_y = solve_affine(raw_pts, W, H)
        except ValueError as err:
            print(f"  ERROR: {err}")
            print("  Calibration aborted — please retry.")
            return None

        print(f"\n  Affine X:  a={cal['a']:+.5f}  b={cal['b']:+.5f}  c={cal['c']:+.2f}")
        print(f"  Affine Y:  d={cal['d']:+.5f}  e={cal['e']:+.5f}  f={cal['f']:+.2f}")
        print(f"  BR check:  error ({err_x:+.1f},{err_y:+.1f}) px")

        if save:
            self.apply_cal(cal)
            self.save_cal()
            display.fill_screen(display.BLACK)
            display.draw_text("Calibration saved.",
                              Rect(0, 100, W, 40),
                              color=display.GREEN, size=18, align="center")
            time.sleep(1.5)

        return cal, err_x, err_y

    @staticmethod
    def _draw_crosshair(display, cx: int, cy: int, size: int = 15):
        """Draw a crosshair and small square at (cx, cy) on the display."""
        c = display.WHITE
        display.draw_line(cx - size, cy, cx + size, cy, c)
        display.draw_line(cx, cy - size, cx, cy + size, c)
        display.draw_rect(Rect(cx - 3, cy - 3, 7, 7), display.RED)
