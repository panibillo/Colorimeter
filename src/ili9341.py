"""
ili9341.py  —  ILI9341 display driver for Colorimeter prototype
---------------------------------------------------------------
Drives the ILI9341 240x320 TFT controller on the Adafruit 1651 shield
via SPI0 on a Raspberry Pi Zero W or Zero 2 W.

Hardware connections (from as-built pin map):
  SPI0 MOSI  GPIO10  pin 19
  SPI0 MISO  GPIO9   pin 21
  SPI0 SCLK  GPIO11  pin 23
  SPI0 CE0   GPIO8   pin 24   (hardware-managed chip select)
  DC         GPIO6   pin 31   (Data/Command select)
  RST        GPIO17  pin 11   (hardware reset)
  Backlight  GPIO13  pin 33   (wire to 3.3 V for always-on; not driven here)

Usage:
    from ili9341 import ILI9341
    from geometry import Rect

    display = ILI9341()
    display.init()
    display.fill_screen(display.BLACK)
    display.draw_text("Hello", Rect(10, 10, 200, 30), size=20)
    display.cleanup()

Or as a context manager:
    with ILI9341() as display:
        display.fill_screen(display.RED)
"""

import time
import struct
import spidev
from gpiozero import DigitalOutputDevice
from PIL import Image, ImageDraw, ImageFont

from geometry import Rect


# ── RPi model detection ───────────────────────────────────────────────────────

def _detect_model() -> str:
    """Read /proc/device-tree/model and return the model string."""
    try:
        with open("/proc/device-tree/model", "r") as f:
            return f.read().strip().rstrip('\x00')
    except OSError:
        return "Unknown"


# ── ILI9341 register addresses ────────────────────────────────────────────────
_SLPOUT  = 0x11
_DISPON  = 0x29
_CASET   = 0x2A
_PASET   = 0x2B
_RAMWR   = 0x2C
_MADCTL  = 0x36
_COLMOD  = 0x3A

# MADCTL orientation bytes for landscape 320x240
# 0xE8 = MV|MX|MY|BGR  (landscape, 180° — cable on right)
# 0x28 = MV|BGR         (landscape, 0°   — cable on left)
MADCTL_LANDSCAPE_180 = 0xE8
MADCTL_LANDSCAPE_0   = 0x28


class ILI9341:
    """
    Driver for the ILI9341 TFT display controller.

    Parameters
    ----------
    dc_pin      : BCM GPIO for Data/Command (default 6)
    rst_pin     : BCM GPIO for hardware reset (default 17)
    spi_bus     : SPI bus number (default 0)
    spi_device  : SPI device/CE number (default 0)
    orientation : MADCTL byte — use MADCTL_LANDSCAPE_180 or MADCTL_LANDSCAPE_0
    font_path   : path to a TrueType .ttf file, or None for Pillow default
    """

    # Landscape dimensions
    WIDTH  = 320
    HEIGHT = 240

    # Colour palette (RGB565)
    BLACK   = ((  0 & 0xF8) << 8) | ((  0 & 0xFC) << 3) | (  0 >> 3)
    WHITE   = ((255 & 0xF8) << 8) | ((255 & 0xFC) << 3) | (255 >> 3)
    RED     = ((255 & 0xF8) << 8) | ((  0 & 0xFC) << 3) | (  0 >> 3)
    GREEN   = ((  0 & 0xF8) << 8) | ((255 & 0xFC) << 3) | (  0 >> 3)
    BLUE    = ((  0 & 0xF8) << 8) | ((  0 & 0xFC) << 3) | (255 >> 3)
    CYAN    = ((  0 & 0xF8) << 8) | ((255 & 0xFC) << 3) | (255 >> 3)
    MAGENTA = ((255 & 0xF8) << 8) | ((  0 & 0xFC) << 3) | (255 >> 3)
    YELLOW  = ((255 & 0xF8) << 8) | ((255 & 0xFC) << 3) | (  0 >> 3)
    ORANGE  = ((255 & 0xF8) << 8) | ((165 & 0xFC) << 3) | (  0 >> 3)
    GRAY    = ((128 & 0xF8) << 8) | ((128 & 0xFC) << 3) | (128 >> 3)
    DKGRAY  = (( 64 & 0xF8) << 8) | (( 64 & 0xFC) << 3) | ( 64 >> 3)
    LTGRAY  = ((192 & 0xF8) << 8) | ((192 & 0xFC) << 3) | (192 >> 3)

    def __init__(
        self,
        dc_pin:      int   = 6,
        rst_pin:     int   = 17,
        spi_bus:     int   = 0,
        spi_device:  int   = 0,
        orientation: int   = MADCTL_LANDSCAPE_180,
        font_path:   str   = None,
    ):
        self._dc          = dc_pin
        self._rst         = rst_pin
        self._spi_bus     = spi_bus
        self._spi_dev     = spi_device
        self._orientation = orientation
        self._font_path   = font_path or self._default_font_path()

        self._spi         = None
        self._dc_dev      = None
        self._rst_dev     = None
        self._font_cache  = {}      # {size: ImageFont}
        self._model       = _detect_model()
        self._initialised = False

        # Choose timing constants based on RPi model
        if "Zero 2" in self._model:
            self._spi_hz        = 32_000_000
            self._spi_chunk     = 4096
            self._t_tsc_poll    = 0.010     # touch poll interval (s)
        else:
            # Zero W or unknown — conservative
            self._spi_hz        = 16_000_000
            self._spi_chunk     = 4096
            self._t_tsc_poll    = 0.020

        # Init timing — hardware minimums from ILI9341 datasheet
        self._t_rst_pre   = 0.001   # before reset pulse
        self._t_rst_low   = 0.001   # reset hold low  (min 10 µs)
        self._t_rst_recov = 0.120   # post-reset recovery (min 120 ms)
        self._t_slpout    = 0.060   # sleep-out delay (min 60 ms)

        # I2C/SPI coexistence — pause after heavy SPI before I2C
        self.t_spi_i2c_gap = 0.200  # public: TSC2007 driver reads this

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self):
        self.init()
        return self

    def __exit__(self, *_):
        self.cleanup()

    # ── Initialisation ────────────────────────────────────────────────────────

    def init(self):
        """
        Initialise GPIO, SPI, and the ILI9341.
        Must be called before any drawing method.
        """
        if self._initialised:
            return

        self._dc_dev  = DigitalOutputDevice(self._dc,  initial_value=True)
        self._rst_dev = DigitalOutputDevice(self._rst, initial_value=True)
        time.sleep(0.10)    # 100 ms settle before display init

        # SPI
        self._spi = spidev.SpiDev()
        self._spi.open(self._spi_bus, self._spi_dev)
        self._spi.max_speed_hz = self._spi_hz
        self._spi.mode = 0

        # ILI9341 init
        self._hw_reset()
        self._cmd(_SLPOUT);  time.sleep(self._t_slpout)
        self._reg(_COLMOD, 0x55)            # 16-bit RGB565
        self._reg(_MADCTL, self._orientation)
        self._cmd(_DISPON)

        self._initialised = True

    def cleanup(self):
        """Clear screen and release all hardware resources."""
        if not self._initialised:
            return
        try:
            self.fill_screen(self.BLACK)
        except Exception:
            pass
        if self._spi:
            self._spi.close()
        for dev in (self._dc_dev, self._rst_dev):
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass
        self._initialised = False

    # ── Low-level SPI ─────────────────────────────────────────────────────────

    def _cmd(self, c: int):
        self._dc_dev.off()
        self._spi.writebytes([c])

    def _data(self, d):
        self._dc_dev.on()
        if isinstance(d, int):
            self._spi.writebytes([d])
        else:
            self._spi.writebytes2(d)

    def _reg(self, c: int, *args):
        self._cmd(c)
        if args:
            self._data(list(args))

    def _hw_reset(self):
        """Issue a hardware reset pulse to the ILI9341."""
        self._rst_dev.on()
        time.sleep(self._t_rst_pre)
        self._rst_dev.off()
        time.sleep(self._t_rst_low)
        self._rst_dev.on()
        time.sleep(self._t_rst_recov)

    def _set_window(self, x0: int, y0: int, x1: int, y1: int):
        self._cmd(_CASET);  self._data(struct.pack(">HH", x0, x1))
        self._cmd(_PASET);  self._data(struct.pack(">HH", y0, y1))
        self._cmd(_RAMWR)

    def _flood(self, pixel: bytes, count: int):
        """Flood count pixels with the given 2-byte RGB565 pixel value."""
        self._dc_dev.on()
        sent = 0
        while sent < count:
            n = min(self._spi_chunk, count - sent)
            self._spi.writebytes2(pixel * n)
            sent += n

    # ── Colour helpers ────────────────────────────────────────────────────────

    @staticmethod
    def rgb(r: int, g: int, b: int) -> int:
        """Pack (r, g, b) 0-255 into an RGB565 word."""
        return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

    @staticmethod
    def _to_rgb(c: int):
        """Unpack RGB565 word to (r, g, b) tuple for Pillow."""
        return (((c >> 11) & 0x1F) << 3,
                ((c >>  5) & 0x3F) << 2,
                 (c        & 0x1F) << 3)

    @staticmethod
    def _pixel_bytes(color: int) -> bytes:
        return bytes([color >> 8, color & 0xFF])

    # ── Drawing primitives ────────────────────────────────────────────────────

    def fill_screen(self, color: int):
        """Fill the entire display with a single RGB565 colour."""
        self._set_window(0, 0, self.WIDTH - 1, self.HEIGHT - 1)
        self._flood(self._pixel_bytes(color), self.WIDTH * self.HEIGHT)

    def fill_rect(self, rect: Rect, color: int, widget_id: str = ""):
        """Fill a rectangular region with a single RGB565 colour."""
        if rect.w <= 0 or rect.h <= 0:
            return
        x1 = min(rect.right  - 1, self.WIDTH  - 1)
        y1 = min(rect.bottom - 1, self.HEIGHT - 1)
        self._set_window(rect.x, rect.y, x1, y1)
        self._flood(self._pixel_bytes(color), rect.w * rect.h)

    def draw_rect(self, rect: Rect, color: int, fill: bool = False,
                  thickness: int = 1, widget_id: str = ""):
        """
        Draw a rectangle outline (fill=False) or filled rectangle (fill=True).
        thickness applies to the outline only; filled rects ignore it.
        """
        if fill:
            self.fill_rect(rect, color)
            return
        for t in range(thickness):
            r = rect.inflated(-t, -t) if t > 0 else rect
            if r.w <= 0 or r.h <= 0:
                break
            # Top and bottom edges
            self.fill_rect(Rect(r.x, r.y,          r.w, 1), color)
            self.fill_rect(Rect(r.x, r.bottom - 1, r.w, 1), color)
            # Left and right edges (excluding corners already drawn)
            self.fill_rect(Rect(r.x,          r.y + 1, 1, r.h - 2), color)
            self.fill_rect(Rect(r.right - 1,  r.y + 1, 1, r.h - 2), color)

    def draw_line(self, x0: int, y0: int, x1: int, y1: int, color: int):
        """Draw a line from (x0,y0) to (x1,y1) using Bresenham's algorithm."""
        steep = abs(y1 - y0) > abs(x1 - x0)
        if steep:
            x0, y0 = y0, x0;  x1, y1 = y1, x1
        if x0 > x1:
            x0, x1 = x1, x0;  y0, y1 = y1, y0
        dx = x1 - x0
        dy = abs(y1 - y0)
        err = dx // 2
        y = y0
        ystep = 1 if y0 < y1 else -1
        pixel = self._pixel_bytes(color)
        for x in range(x0, x1 + 1):
            px, py = (y, x) if steep else (x, y)
            if 0 <= px < self.WIDTH and 0 <= py < self.HEIGHT:
                self._set_window(px, py, px, py)
                self._data(pixel)
            err -= dy
            if err < 0:
                y   += ystep
                err += dx

    def draw_pixel(self, x: int, y: int, color: int):
        """Draw a single pixel."""
        if 0 <= x < self.WIDTH and 0 <= y < self.HEIGHT:
            self._set_window(x, y, x, y)
            self._data(self._pixel_bytes(color))

    # ── Image display ─────────────────────────────────────────────────────────

    def draw_image(self, img: Image.Image, x: int = 0, y: int = 0):
        """
        Blit a Pillow Image to the display at position (x, y).

        The image is converted to RGB if necessary and packed to RGB565.
        The image must already be the correct size for the target area —
        scaling is the caller's responsibility (use img.resize() or
        img.thumbnail() before calling this method).

        The image is clipped to the display boundary automatically.
        """
        img = img.convert("RGB")
        iw, ih = img.size

        # Clip to display
        x0 = max(0, x);  y0 = max(0, y)
        x1 = min(x + iw, self.WIDTH)  - 1
        y1 = min(y + ih, self.HEIGHT) - 1
        if x1 < x0 or y1 < y0:
            return

        # Crop image if partially off-screen
        crop_x = x0 - x
        crop_y = y0 - y
        crop_w = x1 - x0 + 1
        crop_h = y1 - y0 + 1
        if crop_x > 0 or crop_y > 0 or crop_w < iw or crop_h < ih:
            img = img.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))

        self._set_window(x0, y0, x1, y1)
        pixels = img.tobytes()
        self._dc_dev.on()
        buf = bytearray(len(pixels) // 3 * 2)
        for i in range(0, len(pixels), 3):
            c       = self.rgb(pixels[i], pixels[i+1], pixels[i+2])
            j       = (i // 3) * 2
            buf[j]  = c >> 8
            buf[j+1]= c & 0xFF
        self._spi.writebytes2(buf)

    # ── Text rendering ────────────────────────────────────────────────────────

    def _get_font(self, size: int) -> ImageFont.ImageFont:
        """Return a cached font at the requested size."""
        if size not in self._font_cache:
            try:
                self._font_cache[size] = ImageFont.truetype(
                    self._font_path, size)
            except (IOError, OSError):
                try:
                    self._font_cache[size] = ImageFont.load_default(size=size)
                except TypeError:
                    self._font_cache[size] = ImageFont.load_default()
        return self._font_cache[size]

    def draw_text(
        self,
        text:     str,
        rect:     Rect,
        color:    int  = None,
        bg:       int  = None,
        size:     int  = 16,
        align:    str  = "left",    # "left" | "center" | "right"
        valign:   str  = "top",     # "top"  | "middle" | "bottom"
        clip:     bool = True,
        widget_id: str = "",
    ):
        """
        Render text within a Rect, with alignment and optional background.

        Parameters
        ----------
        text   : string to render
        rect   : bounding Rect on the display
        color  : RGB565 foreground — defaults to WHITE
        bg     : RGB565 background — defaults to BLACK; pass None to skip
                 background fill (transparent — text renders over whatever
                 is already on screen, but glyph bounding box is still filled)
        size   : font size in points
        align  : horizontal alignment within rect
        valign : vertical alignment within rect
        clip   : if True, clip text to rect boundary (default True)
        """
        if color is None:
            color = self.WHITE
        if bg is None:
            bg = self.BLACK

        font = self._get_font(size)
        tmp  = Image.new("RGB", (1, 1))
        draw = ImageDraw.Draw(tmp)

        text_bbox = draw.textbbox((0, 0), text, font=font)
        tw = text_bbox[2] - text_bbox[0]

        # Full line height from reference string (prevents descender clipping)
        ref_bbox = draw.textbbox((0, 0), "Agpqjy|", font=font)
        th = ref_bbox[3] - ref_bbox[1] + 2

        # Horizontal alignment within rect
        if align == "center":
            tx = (rect.w - tw) // 2
        elif align == "right":
            tx = rect.w - tw
        else:
            tx = 0

        # Vertical alignment within rect
        if valign == "middle":
            ty = (rect.h - th) // 2
        elif valign == "bottom":
            ty = rect.h - th
        else:
            ty = 0

        # Render into a surface sized to the rect (for clipping)
        surf_w = rect.w if clip else max(rect.w, tx + tw)
        surf_h = rect.h if clip else max(rect.h, ty + th)
        surf = Image.new("RGB", (surf_w, surf_h), self._to_rgb(bg))
        ImageDraw.Draw(surf).text(
            (tx, ty - text_bbox[1]),
            text, font=font, fill=self._to_rgb(color))

        if clip:
            surf = surf.crop((0, 0, rect.w, rect.h))

        self.draw_image(surf, rect.x, rect.y)

    # ── Utility ───────────────────────────────────────────────────────────────

    @property
    def model(self) -> str:
        return self._model

    @property
    def spi_hz(self) -> int:
        return self._spi_hz

    @staticmethod
    def _default_font_path() -> str:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
        for p in candidates:
            try:
                open(p).close()
                return p
            except OSError:
                continue
        return ""   # Pillow default will be used
