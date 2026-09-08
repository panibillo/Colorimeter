import spidev, RPi.GPIO as GPIO, pigpio, time

GPIO.setmode(GPIO.BCM)
GPIO.setup(6, GPIO.OUT)   # DC pin

pi = pigpio.pi()
pi.hardware_PWM(13, 2000, 900000)         # 90% brightness
time.sleep(0.1)                           # let it stabilise

spi = spidev.SpiDev()
spi.open(0, 0)
spi.max_speed_hz = 2_000_000   # drop to 2MHz
spi.mode = 0

def cmd(c):
    GPIO.output(6, GPIO.LOW)
    spi.writebytes([c])

def dat(d):
    GPIO.output(6, GPIO.HIGH)
    spi.writebytes([d] if isinstance(d, int) else d)

# Minimal init: just enough to turn display on and fill red
cmd(0x01); time.sleep(0.2)   # software reset
cmd(0x11); time.sleep(0.15)  # sleep out
cmd(0x3A); dat(0x55)         # 16-bit colour
cmd(0x29)                    # display on
time.sleep(0.1)

# Fill screen red: set full window then flood with red pixels
cmd(0x2A); dat([0x00, 0x00, 0x00, 0xEF])   # column 0-239
cmd(0x2B); dat([0x00, 0x00, 0x01, 0x3F])   # row 0-319
cmd(0x2C)                                   # write to RAM
GPIO.output(6, GPIO.HIGH)
red = bytes([0xF8, 0x00]) * (240 * 320)
spi.writebytes2(red)

print("Done. Screen should be red.")
time.sleep(1)
cmd(0x2C)
GPIO.output(6, GPIO.HIGH)
spi.writebytes2(bytes([0x07, 0xE0]) * (240 * 320))  # green
print("Green screen")
time.sleep(1)
cmd(0x2C)
GPIO.output(6, GPIO.HIGH)
spi.writebytes2(bytes([0x00, 0x1F]) * (240 * 320))  # blue
print("Blue screen")
time.sleep(2)
spi.close()
GPIO.cleanup()

pi.hardware_PWM(13, 2000, 0)             # backlight off
pi.stop()
