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
  RST        GPIO17  pin 11   -> ILI9341 RESET via J1-Pin14
  I2C SDA    GPIO2   pin 3    -> TSC2007
  I2C SCL    GPIO3   pin 5    -> TSC2007
  TSIRQ      GPIO4   pin 7    <- TSC2007 active-low interrupt
  Backlight  GPIO13  pin 33      hardware PWM1 via pigpio

Design notes:
  - Landscape orientation: W=320, H=240. MADCTL=0x28 (BGR, MV set for
    row/col exchange). If display appears upside-down change to 0xE8.
  - Hardware reset on GPIO17 replaces software SWRESET (0x01). More
    reliable through the 74LVX245 level-shifters on the 1651 shield.
  - Backlight must be turned on BEFORE display init or screen is black.
  - SPI at 8 MHz. 32 MHz caused intermittent init failure on this board.
    Increase in steps (16, 24, 32) once stable operation is confirmed.
  - TSC2007 uses raw i2c_rdwr protocol (write cmd byte, read 2 bytes).
    SMBus write_byte/read_word_data caused OSError errno 121.
    Z1 measurement command is 0xE0. 0xB0 is SETUP — no ACK, no data.
  - 20 ms settle added before and after fill_screen to prevent display
    state loss during rapid successive fills.
  - 200 ms pause before opening I2C bus after SPI display writes.
    Fast SPI bursts can glitch I2C and cause errno 5 (EIO).

Usage:
  python3 display_test.py fill       # flood screen, cycle colours
  python3 display_test.py pattern    # colour bars + checkerboard
  python3 display_test.py text       # text rendering via Pillow/TrueType
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
DC_PIN    = 6       # TFT Data/Command
RST_PIN   = 17      # ILI9341 hardware reset, RPi pin 11 -> J1-Pin14
TSIRQ_PIN = 4       # TSC2007 interrupt, active-low
BL_PIN    = 13      # backlight hardware PWM1

SPI_BUS   = 0
SPI_DEV   = 0
SPI_HZ    = 8_000_000   # confirmed reliable on this board

I2C_BUS   = 1
TSC_ADDR  = 0x48

# ── Display geometry — landscape ──────────────────────────────────────────────
W, H = 320, 240     # landscape: 320 wide x 240 tall

# ── RGB565 colour helpers ─────────────────────────────────────────────────────
def rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

def _565_to_rgb(c):
    return (((c >> 11) & 0x1F) << 3,
            ((c >>  5) & 0x3F) << 2,
             (c        & 0x1F) << 3)

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
_SLPOUT  = 0x11
_DISPON  = 0x29
_CASET   = 0x2A
_PASET   = 0x2B
_RAMWR   = 0x2C
_MADCTL  = 0x36
_COLMOD  = 0x3A

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
    """
    Hardware reset followed by minimal proven init sequence.

    Hardware reset (GPIO17) is more reliable than software SWRESET through
    the 74LVX245 level-shifters on the Adafruit 1651 shield.
    After hard reset, SWRESET (0x01) is not needed.

    MADCTL 0x28: MV=1 (row/col exchange for landscape), BGR colour filter,
    no X or Y mirroring. If display appears upside-down change to 0xE8.
    """
    # Hardware reset pulse
    GPIO.setup(RST_PIN, GPIO.OUT, initial=GPIO.HIGH)
    time.sleep(0.01)
    GPIO.output(RST_PIN, GPIO.LOW)
    time.sleep(0.02)                # hold reset low 20 ms
    GPIO.output(RST_PIN, GPIO.HIGH)
    time.sleep(0.15)                # 150 ms recovery — do not shorten

    _cmd(_SLPOUT);  time.sleep(0.15)    # 150 ms — do not shorten
    _reg(_COLMOD, 0x55)                 # 16-bit RGB565
    _reg(_MADCTL, 0xE8)                 # landscape 180°, BGR, cable-right
    _cmd(_DISPON)


# ── ILI9341 drawing primitives ────────────────────────────────────────────────

def _set_window(x0, y0, x1, y1):
    _cmd(_CASET);  _data(struct.pack(">HH", x0, x1))
    _cmd(_PASET);  _data(struct.pack(">HH", y0, y1))
    _cmd(_RAMWR)

def fill_screen(color):
    time.sleep(0.02)                    # settle before
    _set_window(0, 0, W - 1, H - 1)
    pixel = bytes([color >> 8, color & 0xFF])
    GPIO.output(DC_PIN, GPIO.HIGH)
    total = W * H;  sent = 0
    while sent < total:
        n = min(512, total - sent)
        _spi.writebytes2(pixel * n)
        sent += n
    time.sleep(0.02)                    # settle after

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
    """Blit a Pillow RGB image to the display at pixel position (x, y)."""
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
    """
    Render text using a TrueType font and blit to the display.
    Falls back to Pillow bitmap default if no TrueType font is found.

    Height is measured from reference string "Agpqjy|" which contains
    full ascenders and descenders, preventing bottom clipping regardless
    of which characters appear in the actual text string.
    Text is drawn at -text_bbox[1] to correct for any positive top offset
    some fonts return, preventing top clipping.
    """
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
    draw = ImageDraw.Draw(tmp)

    text_bbox = draw.textbbox((0, 0), text, font=font)
    tw = text_bbox[2] - text_bbox[0]

    ref_bbox  = draw.textbbox((0, 0), "Agpqjy|", font=font)
    th = ref_bbox[3] - ref_bbox[1] + 2     # full line height + 2 px margin

    if tw <= 0 or th <= 0:
        return

    surf = Image.new("RGB", (tw, th), _565_to_rgb(bg))
    ImageDraw.Draw(surf).text((0, -text_bbox[1]), text,
                               font=font, fill=_565_to_rgb(fg))
    blit_image(surf, x, y)


# ── Backlight ─────────────────────────────────────────────────────────────────

def set_backlight(percent):
    """Set backlight brightness 0-100 % via hardware PWM on GPIO13."""
    _pi.hardware_PWM(BL_PIN, 2000, int(max(0, min(100, percent)) * 10000))


# ── TSC2007 touch ─────────────────────────────────────────────────────────────
#
# TSC2007 command byte: [C3 C2 C1 C0 M -- PD1 PD0]
#   M=0 → 12-bit.  PD=00 → power-down between conversions.
#
#   X  measurement : 0xC0
#   Y  measurement : 0xD0
#   Z1 measurement : 0xE0   ← correct Z1 command
#                             0xB0 is SETUP — no ACK, no data, causes errno 121
#
# Protocol: write one command byte (i2c_rdwr write), then read 2 bytes
# (i2c_rdwr read). Do NOT use SMBus write_byte / read_word_data.
#
# Response: byte0=[D11:D4], byte1=[D3:D0 x x x x] → 12-bit in upper bits.

_CMD_X  = 0xC0
_CMD_Y  = 0xD0
_CMD_Z1 = 0xE0

# Calibration: raw ADC → pixel coordinates.
# Run touch test and note printed min/max values, then update these.
CAL_X_MIN, CAL_X_MAX = 300, 3700
CAL_Y_MIN, CAL_Y_MAX = 300, 3700

def _tsc_read(bus, cmd):
    """Send one TSC2007 command and return the 12-bit ADC result."""
    wr = smbus2.i2c_msg.write(TSC_ADDR, [cmd])
    rd = smbus2.i2c_msg.read(TSC_ADDR, 2)
    bus.i2c_rdwr(wr)
    time.sleep(0.002)           # ADC conversion time
    bus.i2c_rdwr(rd)
    d = list(rd)
    return ((d[0] << 4) | (d[1] >> 4)) & 0xFFF

def read_touch_raw(bus):
    """Return raw (x, y, z1) ADC values. z1 > ~200 indicates a touch."""
    x  = _tsc_read(bus, _CMD_X)
    y  = _tsc_read(bus, _CMD_Y)
    z1 = _tsc_read(bus, _CMD_Z1)
    return x, y, z1

def raw_to_pixels(rx, ry):
    """
    Map raw TSC2007 ADC values to display pixel coordinates.
    TSC2007 returns native orientation regardless of MADCTL.
    Both axes mirrored to match 180° rotated landscape (MADCTL 0xE8).
    """
    px = int((rx - CAL_X_MIN) / (CAL_X_MAX - CAL_X_MIN) * W)
    py = int((ry - CAL_Y_MIN) / (CAL_Y_MAX - CAL_Y_MIN) * H)
    px = max(0, min(W - 1, px))
    py = max(0, min(H - 1, py))
    # Mirror both axes to match 180° display rotation
    px = W - 1 - px
    py = H - 1 - py
    return px, py


# ── Hardware setup and teardown ───────────────────────────────────────────────

def setup():
    """
    Initialise all hardware. Order is critical:
      1. GPIO mode and pin setup
      2. pigpio daemon connection
      3. Backlight ON  — must come before display init; screen is black without it
      4. SPI open
      5. ILI9341 hardware reset and init sequence
    """
    global _spi, _pi

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(DC_PIN,    GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(TSIRQ_PIN, GPIO.IN,  pull_up_down=GPIO.PUD_UP)
    # RST_PIN is set up inside _init_display() with initial=HIGH

    _pi = pigpio.pi()
    if not _pi.connected:
        print("ERROR: pigpio daemon not running.")
        print("       Run: sudo systemctl start pigpiod")
        sys.exit(1)

    set_backlight(80)       # backlight ON before display init
    time.sleep(0.10)        # 100 ms — confirmed necessary from screentest.py

    _spi = spidev.SpiDev()
    _spi.open(SPI_BUS, SPI_DEV)
    _spi.max_speed_hz = SPI_HZ
    _spi.mode = 0

    _init_display()
    print(f"Display initialised. {W}x{H} landscape, SPI {SPI_HZ//1_000_000} MHz.")

def teardown():
    """Clear screen, backlight off, release all hardware resources."""
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
    """Cycle through 8 solid colours to confirm full display response."""
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
        time.sleep(1.0)
    print("  fill test complete.")


def test_pattern():
    """
    8 colour bars across the top half, checkerboard on the bottom half.
    Confirms window addressing, fill_rect, and colour accuracy.
    """
    print("\n── test_pattern ─────────────────────────")
    time.sleep(0.1)
    fill_screen(BLACK)
    bars = [RED, ORANGE, YELLOW, GREEN, CYAN, BLUE, MAGENTA, WHITE]
    bw   = W // 8                       # bar width in landscape = 40 px
    for i, c in enumerate(bars):
        fill_rect(i * bw, 0, bw, H // 2, c)
    fill_rect(0, H // 2 - 1, W, 2, WHITE)   # divider line
    sq = 20
    for row in range(H // 2 // sq + 1):
        for col in range(W // sq + 1):
            fill_rect(col * sq, H // 2 + row * sq, sq, sq,
                      WHITE if (row + col) % 2 == 0 else BLACK)
    print("  pattern drawn. Pausing 3 s...")
    time.sleep(3)
    print("  pattern test complete.")


def test_text():
    """
    Render text at several sizes and colours using DejaVu TrueType font.
    Confirms Pillow blit path and font rendering at landscape coordinates.
    """
    print("\n── test_text ────────────────────────────")
    fill_screen(BLACK)
    lines = [
        ("Colorimeter",                     10,  10, CYAN,   28),
        ("ILI9341  320x240  landscape",      10,  48, WHITE,  16),
        ("RPi Zero W",                       10,  72, GREEN,  16),
        (f"SPI0 @ {SPI_HZ//1_000_000} MHz", 10,  96, YELLOW, 14),
        ("TrueType font via Pillow",         10, 118, ORANGE, 14),
        ("GPIO6=DC  GPIO17=RST  GPIO13=BL",  10, 140, WHITE,  12),
    ]
    for text, x, y, fg, sz in lines:
        draw_text(text, x, y, fg=fg, bg=BLACK, size=sz)
        print(f"  drew: '{text}'")
    time.sleep(4)
    print("  text test complete.")


def test_backlight():
    """Ramp backlight 0→100→80 % using hardware PWM on GPIO13."""
    print("\n── test_backlight ───────────────────────")
    fill_screen(WHITE)
    draw_text("Backlight PWM — GPIO13", 10, 105, fg=BLACK, bg=WHITE, size=20)
    print("  ramping up 0->100%...")
    for pct in range(0, 101, 5):
        set_backlight(pct)
        print(f"  {pct:3d}%")
        time.sleep(0.08)
    print("  ramping down 100->0%...")
    for pct in range(100, -1, -5):
        set_backlight(pct)
        time.sleep(0.08)
    set_backlight(80)
    print("  backlight test complete.")


def test_touch():
    """
    Poll TSC2007 over I2C for 30 s. Prints raw ADC values and mapped
    pixel coordinates on each touch, and draws a dot on screen.

    Record the raw min/max X and Y values from this output and update
    CAL_X_MIN, CAL_X_MAX, CAL_Y_MIN, CAL_Y_MAX at the top of this file
    for accurate touch-to-pixel mapping.
    """
    print("\n── test_touch (30 s) ────────────────────")
    print("  Touch the screen. Note raw min/max values for calibration.\n")
    fill_screen(BLACK)
    draw_text("Touch the screen", 80, 105, fg=CYAN, bg=BLACK, size=20)

    # Pause after SPI display writes before opening I2C.
    # Rapid SPI at 8 MHz can glitch SDA/SCL and lock the I2C bus (errno 5).
    time.sleep(0.2)
    bus     = smbus2.SMBus(I2C_BUS)
    THRESH  = 200
    deadline = time.monotonic() + 30
    last    = (None, None)

    try:
        while time.monotonic() < deadline:
            try:
                rx, ry, z1 = read_touch_raw(bus)
            except OSError as e:
                print(f"  I2C error: {e}")
                time.sleep(0.1)
                continue
            if z1 > THRESH:
                px, py = raw_to_pixels(rx, ry)
                print(f"  raw=({rx:4d},{ry:4d})  z1={z1:4d}"
                      f"  pixel=({px:3d},{py:3d})")
                if (px, py) != last:
                    fill_rect(max(0, px - 3), max(0, py - 3), 7, 7, YELLOW)
                    last = (px, py)
            time.sleep(0.05)
    finally:
        bus.close()
    print("  touch test complete.")


def test_tsirq():
    """
    Wait for TSIRQ (GPIO4) active-low interrupt for 30 s.
    Each tap prints coordinates and redraws the screen with results.
    Confirms the TSIRQ/#2 jumper and GPIO4 wiring are correct.
    """
    print("\n── test_tsirq (30 s) ────────────────────")
    print("  Waiting for TSIRQ on GPIO4 (active-low). Tap the screen.\n")
    fill_screen(BLACK)
    draw_text("Waiting for TSIRQ...", 40, 95,  fg=MAGENTA, bg=BLACK, size=18)
    draw_text("Tap screen",           95, 125, fg=WHITE,   bg=BLACK, size=16)

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
                    draw_text(f"TSIRQ #{hit}",
                              90, 70,  fg=GREEN,  bg=BLACK, size=28)
                    draw_text(f"px={px}  py={py}",
                              75, 110, fg=WHITE,  bg=BLACK, size=20)
                    draw_text(f"z1 = {z1}",
                              110, 143, fg=YELLOW, bg=BLACK, size=16)
                except OSError as e:
                    print(f"  I2C error after TSIRQ: {e}")
                # Wait for finger lift
                while GPIO.input(TSIRQ_PIN) == GPIO.LOW:
                    time.sleep(0.02)
                time.sleep(0.05)    # debounce
            else:
                time.sleep(0.02)
    finally:
        bus.close()

    if hit == 0:
        print("  No TSIRQ events detected.")
        print("  Check: TSIRQ/#2 jumper soldered on 1651?")
        print("         GPIO4 wired to J1-Pin10?")
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

def _usage():
    print(__doc__)
    sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        _usage()
    cmd = sys.argv[1].lower()
    if cmd not in TESTS and cmd != "all":
        print(f"Unknown test: '{cmd}'")
        _usage()

    setup()
    try:
        if cmd == "all":
            for name, fn in TESTS.items():
                print(f"\nRunning: {name}")
                fn()
                time.sleep(1.0)     # settle between tests — important for I2C
        else:
            TESTS[cmd]()
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        teardown()
