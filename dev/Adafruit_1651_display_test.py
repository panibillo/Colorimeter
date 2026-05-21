#!/usr/bin/env python3
"""
display_test.py  —  Adafruit 1651 proof-of-life test
-----------------------------------------------------
Tests 
  ILI9341 display (TFT)
  TSC2007 touch
  TSIRQ interrupt
  backlight PWM

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

Usage:
  python3 display_test.py fill       # flood screen, cycle colours
  python3 display_test.py pattern    # colour bars + basic shapes
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

SPI_BUS  = 0
SPI_DEV  = 0
SPI_HZ   = 8_000_000

I2C_BUS  = 1
TSC_ADDR = 0x48

# ── Display geometry ──────────────────────────────────────────────────────────
W, H = 240, 320

# ── RGB565 colour helpers ─────────────────────────────────────────────────────
def rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

def rgb565_bytes(r, g, b):
    c = rgb565(r, g, b)
    return bytes([c >> 8, c & 0xFF])

BLACK   = rgb565(  0,   0,   0)
WHITE   = rgb565(255, 255, 255)
RED     = rgb565(255,   0,   0)
GREEN   = rgb565(  0, 255,   0)
BLUE    = rgb565(  0,   0, 255)
CYAN    = rgb565(  0, 255, 255)
MAGENTA = rgb565(255,   0, 255)
YELLOW  = rgb565(255, 255,   0)
ORANGE  = rgb565(255, 165,   0)

# ── ILI9341 register addresses ────────────────────────────────────────────────
_SWRESET  = 0x01
_SLPOUT   = 0x11
_DISPON   = 0x29
_CASET    = 0x2A
_PASET    = 0x2B
_RAMWR    = 0x2C
_MADCTL   = 0x36
_COLMOD   = 0x3A
_PWCTR1   = 0xC0
_PWCTR2   = 0xC1
_VMCTR1   = 0xC5
_VMCTR2   = 0xC7
_FRMCTR1  = 0xB1
_DFUNCTR  = 0xB6
_GMCTRP1  = 0xE0
_GMCTRN1  = 0xE1

# ── SPI and GPIO globals ──────────────────────────────────────────────────────
_spi = None
_pi  = None   # pigpio instance


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
    """Full ILI9341 init sequence."""
    _cmd(_SWRESET);  time.sleep(0.20)
    _reg(_PWCTR1,  0x23)
    _reg(_PWCTR2,  0x10)
    _reg(_VMCTR1,  0x3E, 0x28)
    _reg(_VMCTR2,  0x86)
    _reg(_MADCTL,  0x48)          # row/col order, BGR
    _reg(0x37,     0x00)          # vertical scroll zero
    _reg(_COLMOD,  0x55)          # 16-bit colour
    _reg(_FRMCTR1, 0x00, 0x18)
    _reg(_DFUNCTR, 0x08, 0x82, 0x27)
    _reg(0xF2,     0x00)          # gamma function disable
    _reg(0x26,     0x01)          # gamma curve 1
    _reg(_GMCTRP1,
         0x0F, 0x31, 0x2B, 0x0C, 0x0E,
         0x08, 0x4E, 0xF1, 0x37, 0x07,
         0x10, 0x03, 0x0E, 0x09, 0x00)
    _reg(_GMCTRN1,
         0x00, 0x0E, 0x14, 0x03, 0x11,
         0x07, 0x31, 0xC1, 0x48, 0x08,
         0x0F, 0x0C, 0x31, 0x36, 0x0F)
    _cmd(_SLPOUT); time.sleep(0.15)
    _cmd(_DISPON)


# ── ILI9341 drawing primitives ────────────────────────────────────────────────

def _set_window(x0, y0, x1, y1):
    _cmd(_CASET);  _data(struct.pack(">HH", x0, x1))
    _cmd(_PASET);  _data(struct.pack(">HH", y0, y1))
    _cmd(_RAMWR)

def fill_screen(color):
    _set_window(0, 0, W - 1, H - 1)
    pixel = bytes([color >> 8, color & 0xFF])
    GPIO.output(DC_PIN, GPIO.HIGH)
    chunk = pixel * 512
    total = W * H
    sent  = 0
    while sent < total:
        n = min(512, total - sent)
        _spi.writebytes2(pixel * n)
        sent += n

def fill_rect(x, y, w, h, color):
    _set_window(x, y, x + w - 1, y + h - 1)
    pixel = bytes([color >> 8, color & 0xFF])
    GPIO.output(DC_PIN, GPIO.HIGH)
    total = w * h
    sent  = 0
    while sent < total:
        n = min(512, total - sent)
        _spi.writebytes2(pixel * n)
        sent += n

def blit_image(img, x=0, y=0):
    """Blit a Pillow RGB image to the display."""
    img = img.convert("RGB")
    iw, ih = img.size
    _set_window(x, y, x + iw - 1, y + ih - 1)
    pixels = img.tobytes()
    GPIO.output(DC_PIN, GPIO.HIGH)
    buf = bytearray(iw * ih * 2)
    for i in range(0, len(pixels), 3):
        r, g, b = pixels[i], pixels[i+1], pixels[i+2]
        c = rgb565(r, g, b)
        j = (i // 3) * 2
        buf[j]   = c >> 8
        buf[j+1] = c & 0xFF
    _spi.writebytes2(buf)

def draw_text(text, x, y, fg=WHITE, bg=BLACK, size=16):
    """Render text using Pillow and blit to display."""
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:
        font = ImageFont.load_default()
    tmp  = Image.new("RGB", (1, 1))
    bbox = ImageDraw.Draw(tmp).textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    surf = Image.new("RGB", (tw, th), _565_to_rgb(bg))
    ImageDraw.Draw(surf).text((0, 0), text, font=font, fill=_565_to_rgb(fg))
    blit_image(surf, x, y)

def _565_to_rgb(c):
    r = ((c >> 11) & 0x1F) << 3
    g = ((c >>  5) & 0x3F) << 2
    b =  (c        & 0x1F) << 3
    return (r, g, b)


# ── Backlight control ─────────────────────────────────────────────────────────

def set_backlight(percent):
    """Set backlight brightness 0-100 via hardware PWM."""
    duty = int(max(0, min(100, percent)) * 10000)
    _pi.hardware_PWM(BL_PIN, 2000, duty)


# ── TSC2007 touch reading ─────────────────────────────────────────────────────

# TSC2007 command bytes: C[7:4]=function, C[3:2]=power, C[1]=ADC res, C[0]=STS
_TSC_READ_X  = 0xC4   # measure X,  12-bit, power-on
_TSC_READ_Y  = 0xD4   # measure Y,  12-bit, power-on
_TSC_READ_Z1 = 0xB4   # measure Z1, 12-bit
_TSC_READ_Z2 = 0xC4   # measure Z2, 12-bit  (same byte as X — intentional)

# Calibration: map raw ADC range to pixel range
# These are typical values; run touch test and note min/max to refine
CAL_X_MIN, CAL_X_MAX = 300, 3700
CAL_Y_MIN, CAL_Y_MAX = 300, 3700

# def _tsc_read_channel(bus, cmd):
#     """Send one TSC2007 command, return 12-bit result."""
#     bus.write_byte(TSC_ADDR, cmd)
#     time.sleep(0.001)
#     raw = bus.read_word_data(TSC_ADDR, 0)
#     # Result is in upper 12 bits of the 16-bit word, big-endian
#     return ((raw & 0xFF) << 4) | ((raw >> 12) & 0x0F)

# def read_touch_raw(bus):
#     """Return raw (x, y, z1) ADC values from TSC2007."""
#     x  = _tsc_read_channel(bus, _TSC_READ_X)
#     y  = _tsc_read_channel(bus, _TSC_READ_Y)
#     z1 = _tsc_read_channel(bus, _TSC_READ_Z1)
#     return x, y, z1

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
    """Map raw ADC values to display pixel coordinates."""
    px = int((rx - CAL_X_MIN) / (CAL_X_MAX - CAL_X_MIN) * W)
    py = int((ry - CAL_Y_MIN) / (CAL_Y_MAX - CAL_Y_MIN) * H)
    px = max(0, min(W - 1, px))
    py = max(0, min(H - 1, py))
    return px, py


# ── Hardware setup and teardown ───────────────────────────────────────────────

def setup():
    global _spi, _pi

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(DC_PIN,    GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(TSIRQ_PIN, GPIO.IN,  pull_up_down=GPIO.PUD_UP)

    _spi = spidev.SpiDev()
    _spi.open(SPI_BUS, SPI_DEV)
    _spi.max_speed_hz = SPI_HZ
    _spi.mode = 0

    _pi = pigpio.pi()
    if not _pi.connected:
        print("ERROR: pigpio daemon not running. Run: sudo systemctl start pigpiod")
        sys.exit(1)

    set_backlight(80)
    _init_display()
    print("Display initialised.")

def teardown():
    fill_screen(BLACK)
    set_backlight(0)
    _spi.close()
    _pi.stop()
    GPIO.cleanup()
    print("Done.")


# ── Individual tests ──────────────────────────────────────────────────────────

def test_fill():
    """Flood screen with solid colours in sequence."""
    print("\n── test_fill ────────────────────────────")
    colours = [
        (RED,     "Red"),
        (GREEN,   "Green"),
        (BLUE,    "Blue"),
        (WHITE,   "White"),
        (CYAN,    "Cyan"),
        (MAGENTA, "Magenta"),
        (YELLOW,  "Yellow"),
        (BLACK,   "Black"),
    ]
    for color, name in colours:
        print(f"  {name}")
        fill_screen(color)
        time.sleep(0.6)
    print("  fill test complete.")


def test_pattern():
    """Draw colour bars and a centred circle."""
    print("\n── test_pattern ─────────────────────────")
    fill_screen(BLACK)
    bar_w = W // 8
    bars  = [RED, ORANGE, YELLOW, GREEN, CYAN, BLUE, MAGENTA, WHITE]
    for i, color in enumerate(bars):
        fill_rect(i * bar_w, 0, bar_w, H // 2, color)
    # Horizontal divider
    fill_rect(0, H // 2 - 1, W, 2, WHITE)
    # Checkerboard in lower half
    sq = 20
    for row in range(H // 2 // sq + 1):
        for col in range(W // sq + 1):
            color = WHITE if (row + col) % 2 == 0 else BLACK
            fill_rect(col * sq, H // 2 + row * sq, sq, sq, color)
    print("  pattern drawn. Pausing 3 s...")
    time.sleep(3)
    print("  pattern test complete.")


def test_text():
    """Render several lines of text at different sizes."""
    print("\n── test_text ────────────────────────────")
    fill_screen(BLACK)
    lines = [
        ("Colorimeter", 10,  10, CYAN,    24),
        ("ILI9341 display", 10,  44, WHITE,   16),
        ("RPi Zero W",  10,  68, GREEN,   16),
        ("SPI0 @ 32 MHz", 10,  92, YELLOW,  14),
        ("Pillow text OK", 10, 114, ORANGE,  14),
        ("GPIO6 = DC pin",  10, 136, WHITE,   12),
    ]
    for text, x, y, fg, size in lines:
        draw_text(text, x, y, fg=fg, bg=BLACK, size=size)
        print(f"  drew: '{text}'")
    time.sleep(3)
    print("  text test complete.")


def test_backlight():
    """Ramp backlight brightness up and down using hardware PWM."""
    print("\n── test_backlight ───────────────────────")
    fill_screen(WHITE)
    draw_text("Backlight PWM", 20, 140, fg=BLACK, bg=WHITE, size=20)
    print("  ramping up...")
    for pct in range(0, 101, 5):
        set_backlight(pct)
        print(f"  {pct}%")
        time.sleep(0.08)
    print("  ramping down...")
    for pct in range(100, -1, -5):
        set_backlight(pct)
        time.sleep(0.08)
    set_backlight(80)
    print("  backlight test complete.")


def test_touch():
    """Poll TSC2007 over I2C and print touch coordinates for 30 seconds."""
    print("\n── test_touch (30 s) ────────────────────")
    print("  Touch the screen. Raw ADC values and mapped pixels shown.")
    print("  Note min/max raw values to refine CAL_X/Y_MIN/MAX constants.\n")

    fill_screen(BLACK)
    draw_text("Touch the screen", 10, 140, fg=CYAN, bg=BLACK, size=18)

    bus      = smbus2.SMBus(I2C_BUS)
    PRESSURE_THRESHOLD = 100
    deadline = time.monotonic() + 30

    try:
        last_dot = (None, None)
        while time.monotonic() < deadline:
            rx, ry, z1 = read_touch_raw(bus)
            if z1 > PRESSURE_THRESHOLD:
                px, py = raw_to_pixels(rx, ry)
                print(f"  raw=({rx:4d},{ry:4d})  z1={z1:4d}  pixel=({px:3d},{py:3d})")
                # Draw a dot where touched
                if (px, py) != last_dot:
                    fill_rect(max(0, px-3), max(0, py-3), 6, 6, YELLOW)
                    last_dot = (px, py)
            time.sleep(0.05)
    finally:
        bus.close()

    print("  touch test complete.")


def test_tsirq():
    """Wait for TSIRQ (GPIO4) to assert, then read one touch sample."""
    print("\n── test_tsirq (30 s) ────────────────────")
    print("  Waiting for TSIRQ interrupt on GPIO4 (active-low).")
    print("  Tap the screen to trigger.\n")

    fill_screen(BLACK)
    draw_text("Waiting for TSIRQ", 5, 130, fg=MAGENTA, bg=BLACK, size=16)
    draw_text("Tap screen...",      5, 155, fg=WHITE,   bg=BLACK, size=16)

    bus      = smbus2.SMBus(I2C_BUS)
    deadline = time.monotonic() + 30
    hit      = 0

    try:
        while time.monotonic() < deadline:
            irq = GPIO.input(TSIRQ_PIN)
            if irq == GPIO.LOW:
                hit += 1
                rx, ry, z1 = read_touch_raw(bus)
                px, py     = raw_to_pixels(rx, ry)
                print(f"  TSIRQ #{hit:3d}  raw=({rx:4d},{ry:4d})  z1={z1:4d}"
                      f"  pixel=({px:3d},{py:3d})")
                fill_screen(BLACK)
                draw_text(f"TSIRQ #{hit}", 60, 100, fg=GREEN,  bg=BLACK, size=24)
                draw_text(f"px={px} py={py}", 40, 135, fg=WHITE, bg=BLACK, size=18)
                # Debounce — wait for release
                while GPIO.input(TSIRQ_PIN) == GPIO.LOW:
                    time.sleep(0.02)
                time.sleep(0.05)
            else:
                time.sleep(0.02)
    finally:
        bus.close()

    if hit == 0:
        print("  No TSIRQ events detected.")
        print("  Check: TSIRQ/#2 jumper soldered on 1651? GPIO4 wired to J1-Pin10?")
    else:
        print(f"  {hit} TSIRQ event(s) detected. Test complete.")


# ── Main ──────────────────────────────────────────────────────────────────────

TESTS = {
    "fill":      test_fill,
    "pattern":   test_pattern,
    "text":      test_text,
    "backlight": test_backlight,
    "touch":     test_touch,
    "tsirq":     test_tsirq,
}

def usage():
    print(__doc__)
    sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        usage()

    cmd = sys.argv[1].lower()
    if cmd not in TESTS and cmd != "all":
        print(f"Unknown test: '{cmd}'")
        usage()

    setup()
    try:
        if cmd == "all":
            for name, fn in TESTS.items():
                print(f"\nRunning: {name}")
                fn()
                time.sleep(0.5)
        else:
            TESTS[cmd]()
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        teardown()
