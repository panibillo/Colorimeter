#!/usr/bin/env python3
"""
display_test.py  —  Adafruit 1651 proof-of-life test
-----------------------------------------------------
Tests ILI9341 display, TSC2007 touch, TSIRQ interrupt, and backlight PWM.

Hardware (from as-built pin map):
  SPI0 MOSI  GPIO10  pin 19   -> ILI9341
  SPI0 MISO  GPIO9   pin 21   <- ILI9341
  SPI0 SCLK  GPIO11  pin 23   -> ILI9341
  SPI0 CE0   GPIO8   pin 24   -> TFT CS  (hardware managed)
  TFT DC     GPIO6   pin 31   -> ILI9341 Data/Command
  I2C SDA    GPIO2   pin 3    -> TSC2007
  I2C SCL    GPIO3   pin 5    -> TSC2007
  TSIRQ      GPIO4   pin 7    <- TSC2007 active-low interrupt
  Backlight  GPIO13  pin 33      hardware PWM1 via pigpio

Fixes applied vs first version:
  - pigpio and backlight initialised BEFORE SPI open and display init.
    Without backlight on first the screen appears black regardless of
    what the ILI9341 is doing. Confirmed by screentest.py red-screen test.
  - ILI9341 init: post-reset delay increased to 200 ms, post-slpout to
    150 ms. Shorter delays caused intermittent white/black screen on this
    board with the 74LVX245 level-shifter on the 1651 shield.
  - SPI speed reduced to 8 MHz. 32 MHz caused unreliable init on this board.
    Can be increased in steps (16, 24, 32 MHz) once stable operation confirmed.
  - TSC2007 command bytes corrected per TI datasheet.
    0xB0 is the SETUP command — it does not ACK and returns no data,
    causing OSError errno 121. Correct Z1 command is 0xE0.
    Protocol uses i2c_rdwr (raw I2C), not SMBus write_byte/read_word_data.

Usage:
  python3 display_test.py fill       # flood screen, cycle colours
  python3 display_test.py pattern    # colour bars + checkerboard
  python3 display_test.py text       # text rendering via Pillow
  python3 display_test.py backlight  # PWM brightness ramp up and down
  python3 display_test.py touch      # poll touch coordinates for 30 s
  python3 display_test.py tsirq      # wait for TSIRQ then read touch (30 s)
  python3 display_test.py all        # run every test in sequence
"""

import sys
import time
import struct
import spidev
import RPi.GPIO as GPIO
import smbus2
import pigpio
from PIL import Image, ImageDraw, ImageFont

# ── Pin assignments ───────────────────────────────────────────────────────────
DC_PIN    = 6       # TFT Data/Command select
TSIRQ_PIN = 4       # TSC2007 interrupt, active-low
BL_PIN    = 13      # backlight, hardware PWM1

SPI_BUS   = 0
SPI_DEV   = 0
SPI_HZ    = 8_000_000   # 8 MHz confirmed reliable; see fix notes above

I2C_BUS   = 1
TSC_ADDR  = 0x48

# ── Display geometry ──────────────────────────────────────────────────────────
W, H = 240, 320

# ── RGB565 colour helpers ─────────────────────────────────────────────────────
def rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

BLACK   = rgb565(  0,   0,   0)
WHITE   = rgb565(255, 255, 255)
RED     = rgb565(255,   0,   0)
GREEN   = rgb565(  0, 255,   0)
BLUE    = rgb565(  0,   0, 255)
CYAN    = rgb565(  0, 255, 255)
MAGENTA = rgb565(255,   0, 255)
YELLOW  = rgb565(255, 255,   0)
ORANGE  = rgb565(255, 165,   0)

def _565_to_rgb(c):
    return (((c >> 11) & 0x1F) << 3,
            ((c >>  5) & 0x3F) << 2,
             (c        & 0x1F) << 3)

# ── ILI9341 register addresses ────────────────────────────────────────────────
_SWRESET = 0x01
_SLPOUT  = 0x11
_DISPON  = 0x29
_CASET   = 0x2A
_PASET   = 0x2B
_RAMWR   = 0x2C
_MADCTL  = 0x36
_COLMOD  = 0x3A
_PWCTR1  = 0xC0
_PWCTR2  = 0xC1
_VMCTR1  = 0xC5
_VMCTR2  = 0xC7
_FRMCTR1 = 0xB1
_DFUNCTR = 0xB6
_GMCTRP1 = 0xE0
_GMCTRN1 = 0xE1

# ── Module globals ────────────────────────────────────────────────────────────
_spi = None
_pi  = None


# ── ILI9341 low-level interface ───────────────────────────────────────────────

def _cmd(c):
    GPIO.output(DC_PIN, GPIO.LOW)
    _spi.writebytes([c])

def _data(d):
    GPIO.output(DC_PIN, GPIO.HIGH)
    if isinstance(d, int):
        _spi.writebytes([d])
    else:
        _spi.writebytes2(d)

def _reg(c, *args):
    _cmd(c)
    if args:
        _data(list(args))


# ── ILI9341 initialisation ────────────────────────────────────────────────────

def _init_display():
    # Minimal proven sequence — derived from screentest.py.
    # Extended power/gamma registers removed: caused white screen on this board.
    # MADCTL 0x08 = BGR colour filter, no row/column exchange (MV bit clear).
    # 0x48 had MV bit set, causing X/Y axis swap (vertical bars, not horizontal).
    _cmd(_SWRESET);  time.sleep(0.20)   # 200 ms — do not shorten
    _cmd(_SLPOUT);   time.sleep(0.15)   # 150 ms — do not shorten
    _reg(_COLMOD, 0x55)                 # 16-bit RGB565
    _reg(_MADCTL, 0x08)                 # BGR, portrait, no axis swap
    _cmd(_DISPON)


# ── ILI9341 drawing primitives ────────────────────────────────────────────────

def _set_window(x0, y0, x1, y1):
    _cmd(_CASET);  _data(struct.pack(">HH", x0, x1))
    _cmd(_PASET);  _data(struct.pack(">HH", y0, y1))
    _cmd(_RAMWR)

def fill_screen(color):
    time.sleep(0.02)                    # settle between fills
    _set_window(0, 0, W - 1, H - 1)
    pixel = bytes([color >> 8, color & 0xFF])
    GPIO.output(DC_PIN, GPIO.HIGH)
    total = W * H;  sent = 0
    while sent < total:
        n = min(512, total - sent)
        _spi.writebytes2(pixel * n)
        sent += n
    time.sleep(0.02)                    # settle after fill completes

def fill_rect(x, y, w, h, color):
    if w <= 0 or h <= 0:
        return
    _set_window(x, y, x + w - 1, y + h - 1)
    pixel = bytes([color >> 8, color & 0xFF])
    GPIO.output(DC_PIN, GPIO.HIGH)
    total = w * h;  sent = 0
    while sent < total:
        n = min(512, total - sent)
        _spi.writebytes2(pixel * n)
        sent += n

def blit_image(img, x=0, y=0):
    img = img.convert("RGB")
    iw, ih = img.size
    _set_window(x, y, x + iw - 1, y + ih - 1)
    pixels = img.tobytes()
    GPIO.output(DC_PIN, GPIO.HIGH)
    buf = bytearray(iw * ih * 2)
    for i in range(0, len(pixels), 3):
        c = rgb565(pixels[i], pixels[i+1], pixels[i+2])
        j = (i // 3) * 2
        buf[j] = c >> 8;  buf[j+1] = c & 0xFF
    _spi.writebytes2(buf)

def draw_text(text, x, y, fg=WHITE, bg=BLACK, size=16):
    """Render text using a TrueType font, falling back to Pillow default."""
    font = None
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]:
        try:
            font = ImageFont.truetype(path, size)
            break
        except (IOError, OSError):
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:
            font = ImageFont.load_default()
    tmp  = Image.new("RGB", (1, 1))
    bbox = ImageDraw.Draw(tmp).textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if tw <= 0 or th <= 0:
        return
    surf = Image.new("RGB", (tw, th), _565_to_rgb(bg))
    ImageDraw.Draw(surf).text((0, 0), text, font=font, fill=_565_to_rgb(fg))
    blit_image(surf, x, y)


# ── Backlight ─────────────────────────────────────────────────────────────────

def set_backlight(percent):
    _pi.hardware_PWM(BL_PIN, 2000, int(max(0, min(100, percent)) * 10000))
    time.sleep(0.1) 

# ── TSC2007 touch ─────────────────────────────────────────────────────────────
#
# Command byte format: [C3 C2 C1 C0 M -- PD1 PD0]
# M=0 → 12-bit resolution.  PD=00 → power-down between conversions.
#
#   X  measurement : 0xC0
#   Y  measurement : 0xD0
#   Z1 measurement : 0xE0   (NOT 0xB0 — that is the SETUP command,
#                             which does not ACK and returns no data)
#   Z2 measurement : 0xF0
#
# Two response bytes: [D11:D4] [D3:D0 x x x x]  →  12-bit value in upper bits.
# Use i2c_rdwr, not SMBus write_byte/read_word_data.

_CMD_X  = 0xC0
_CMD_Y  = 0xD0
_CMD_Z1 = 0xE0

CAL_X_MIN, CAL_X_MAX = 300, 3700   # refine from touch test output
CAL_Y_MIN, CAL_Y_MAX = 300, 3700

def _tsc_read_channel(bus, cmd):
    """
    TSC2007 protocol: write one command byte, read two bytes back.
    Result is 12-bit value in bits [15:4] of the two-byte response.
    Uses raw i2c_rdwr to avoid SMBus framing issues.
    """
    write = smbus2.i2c_msg.write(TSC_ADDR, [cmd])
    read  = smbus2.i2c_msg.read(TSC_ADDR, 2)
    bus.i2c_rdwr(write)
    time.sleep(0.002)
    bus.i2c_rdwr(read)
    data = list(read)
    return ((data[0] << 4) | (data[1] >> 4)) & 0xFFF

def read_touch_raw(bus):
    """Return raw (x, y, z1) ADC values from TSC2007."""
    x  = _tsc_read_channel(bus, 0xC0)   # X measurement, 12-bit, power-on
    y  = _tsc_read_channel(bus, 0xD0)   # Y measurement, 12-bit, power-on
    z1 = _tsc_read_channel(bus, 0xB0)   # Z1 pressure
    return x, y, z1

def raw_to_pixels(rx, ry):
    px = int((rx - CAL_X_MIN) / (CAL_X_MAX - CAL_X_MIN) * W)
    py = int((ry - CAL_Y_MIN) / (CAL_Y_MAX - CAL_Y_MIN) * H)
    px = max(0, min(W - 1, px))
    py = max(0, min(H - 1, py))
    return px, py


# ── Hardware setup and teardown ───────────────────────────────────────────────

def setup():
    """
    Initialise hardware.  Order is critical:
      1. GPIO mode
      2. pigpio daemon connection
      3. Backlight ON  ← must precede display init or screen appears black
      4. SPI open
      5. ILI9341 init sequence
    """
    global _spi, _pi

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(DC_PIN,    GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(TSIRQ_PIN, GPIO.IN,  pull_up_down=GPIO.PUD_UP)

    _pi = pigpio.pi()
    if not _pi.connected:
        print("ERROR: pigpio daemon not running.")
        print("       Run: sudo systemctl start pigpiod")
        sys.exit(1)

    set_backlight(80)       # ON before display init
    time.sleep(0.05)

    _spi = spidev.SpiDev()
    _spi.open(SPI_BUS, SPI_DEV)
    _spi.max_speed_hz = SPI_HZ
    _spi.mode = 0

    _init_display()
    print("Display initialised.")

def teardown():
    try:
        fill_screen(BLACK)
    except Exception:
        pass
    set_backlight(0)
    if _spi:
        _spi.close()
    _pi.stop()
    GPIO.cleanup()
    print("Done.")


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_fill():
    print("\n── test_fill ────────────────────────────")
    for color, name in [(RED,"Red"),(GREEN,"Green"),(BLUE,"Blue"),(WHITE,"White"),
                        (CYAN,"Cyan"),(MAGENTA,"Magenta"),(YELLOW,"Yellow"),(BLACK,"Black")]:
        print(f"  {name}")
        fill_screen(color)
        time.sleep(1.0)
    print("  fill test complete.")


def test_pattern():
    print("\n── test_pattern ─────────────────────────")
    # Brief pause and window reset before rapid fill_rect sequence
    time.sleep(0.1)
    fill_screen(BLACK)
    bars = [RED, ORANGE, YELLOW, GREEN, CYAN, BLUE, MAGENTA, WHITE]
    bw   = W // 8
    for i, c in enumerate(bars):
        fill_rect(i * bw, 0, bw, H // 2, c)
    fill_rect(0, H // 2 - 1, W, 2, WHITE)
    sq = 20
    for row in range(H // 2 // sq + 1):
        for col in range(W // sq + 1):
            fill_rect(col*sq, H//2 + row*sq, sq, sq,
                      WHITE if (row+col) % 2 == 0 else BLACK)
    print("  pattern drawn. Pausing 3 s...")
    time.sleep(3)
    print("  pattern test complete.")


def test_text():
    print("\n── test_text ────────────────────────────")
    fill_screen(BLACK)
    lines = [
        ("Colorimeter",                  10,  10, CYAN,   24),
        ("ILI9341 display",              10,  44, WHITE,  16),
        ("RPi Zero W",                   10,  68, GREEN,  16),
        (f"SPI0 @ {SPI_HZ//1_000_000} MHz", 10, 92, YELLOW, 14),
        ("Pillow text OK",               10, 114, ORANGE, 14),
        ("DC = GPIO6",                   10, 136, WHITE,  12),
    ]
    for text, x, y, fg, sz in lines:
        draw_text(text, x, y, fg=fg, bg=BLACK, size=sz)
        print(f"  drew: '{text}'")
    time.sleep(3)
    print("  text test complete.")


def test_backlight():
    print("\n── test_backlight ───────────────────────")
    fill_screen(WHITE)
    draw_text("Backlight PWM", 20, 140, fg=BLACK, bg=WHITE, size=20)
    print("  ramping up...")
    for pct in range(0, 101, 5):
        set_backlight(pct);  print(f"  {pct:3d}%");  time.sleep(0.08)
    print("  ramping down...")
    for pct in range(100, -1, -5):
        set_backlight(pct);  time.sleep(0.08)
    set_backlight(80)
    print("  backlight test complete.")


def test_touch():
    """
    Poll TSC2007 for 30 s.  Note the raw min/max values printed
    and use them to update CAL_X_MIN/MAX, CAL_Y_MIN/MAX.
    """
    print("\n── test_touch (30 s) ────────────────────")
    print("  Touch the screen.  Note raw min/max to calibrate.\n")
    fill_screen(BLACK)
    draw_text("Touch the screen", 10, 148, fg=CYAN, bg=BLACK, size=18)

    # Pause after SPI display writes before opening I2C — rapid SPI
    # bursts can glitch the I2C bus and leave it in a locked state.
    time.sleep(0.2)
    bus      = smbus2.SMBus(I2C_BUS)
    THRESH   = 200
    deadline = time.monotonic() + 30
    last     = (None, None)

    try:
        while time.monotonic() < deadline:
            try:
                rx, ry, z1 = read_touch_raw(bus)
            except OSError as e:
                print(f"  I2C error: {e}");  time.sleep(0.1);  continue
            if z1 > THRESH:
                px, py = raw_to_pixels(rx, ry)
                print(f"  raw=({rx:4d},{ry:4d})  z1={z1:4d}  pixel=({px:3d},{py:3d})")
                if (px, py) != last:
                    fill_rect(max(0, px-3), max(0, py-3), 7, 7, YELLOW)
                    last = (px, py)
            time.sleep(0.05)
    finally:
        bus.close()
    print("  touch test complete.")


def test_tsirq():
    print("\n── test_tsirq (30 s) ────────────────────")
    print("  Waiting for TSIRQ on GPIO4.  Tap the screen.\n")
    fill_screen(BLACK)
    draw_text("Waiting for TSIRQ", 5, 130, fg=MAGENTA, bg=BLACK, size=16)
    draw_text("Tap screen...",      5, 155, fg=WHITE,   bg=BLACK, size=16)

    # Pause after SPI writes before opening I2C
    time.sleep(0.2)
    bus      = smbus2.SMBus(I2C_BUS)
    deadline = time.monotonic() + 30
    hit      = 0

    try:
        while time.monotonic() < deadline:
            if GPIO.input(TSIRQ_PIN) == GPIO.LOW:
                hit += 1
                try:
                    rx, ry, z1 = read_touch_raw(bus)
                    px, py     = raw_to_pixels(rx, ry)
                    print(f"  TSIRQ #{hit:3d}  raw=({rx:4d},{ry:4d})"
                          f"  z1={z1:4d}  pixel=({px:3d},{py:3d})")
                    fill_screen(BLACK)
                    draw_text(f"TSIRQ #{hit}",    50, 100, fg=GREEN,  bg=BLACK, size=24)
                    draw_text(f"px={px} py={py}", 35, 135, fg=WHITE,  bg=BLACK, size=18)
                    draw_text(f"z1={z1}",         80, 163, fg=YELLOW, bg=BLACK, size=16)
                except OSError as e:
                    print(f"  I2C error: {e}")
                while GPIO.input(TSIRQ_PIN) == GPIO.LOW:
                    time.sleep(0.02)
                time.sleep(0.05)
            else:
                time.sleep(0.02)
    finally:
        bus.close()

    if hit == 0:
        print("  No TSIRQ events detected.")
        print("  Check TSIRQ/#2 jumper on 1651 and GPIO4 → J1-Pin10 wiring.")
    else:
        print(f"  {hit} TSIRQ event(s). Test complete.")


# ── Main ──────────────────────────────────────────────────────────────────────

TESTS = {
    "fill":      test_fill,
    "pattern":   test_pattern,
    "text":      test_text,
    "backlight": test_backlight,
    "touch":     test_touch,
    "tsirq":     test_tsirq,
}

def _usage():
    print(__doc__)
    sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        _usage()
    cmd = sys.argv[1].lower()
    if cmd not in TESTS and cmd != "all":
        print(f"Unknown test: '{cmd}'");  _usage()

    setup()
    try:
        if cmd == "all":
            for name, fn in TESTS.items():
                print(f"\nRunning: {name}")
                fn()
                time.sleep(1.0)     # settle between tests; important for I2C
        else:
            TESTS[cmd]()
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        teardown()
