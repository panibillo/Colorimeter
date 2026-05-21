#!/usr/bin/env python3
"""
shutdown_button_demo.py
-----------------------
Demonstration script for a GPIO-based shutdown button on Raspberry Pi Zero W.
Button is wired between GPIO26 and GND, using the internal pull-up resistor.

VIRTUAL ENVIRONMENT NOTE:
    This script must be run by the Python interpreter INSIDE your venv, or by
    the venv's python3 directly (e.g. /home/pi/myenv/bin/python3).
    The systemd service file handles this — do NOT call 'deactivate' before
    shutdown; just let the service invoke the venv interpreter directly.

FUTURE PERIPHERALS (not yet implemented — stubs are marked with TODO):
    - Pi Camera (picamera2)
    - I2C devices (smbus2 / board)
    - SPI devices
    - ILI9341 display (adafruit-circuitpython-ili9341)
    Each of these needs an orderly teardown before os-level shutdown to avoid
    corrupted state, partial frames, or kernel warnings.  Stub functions are
    provided below so you know where to add that logic later.

Author: you
"""

import logging
import subprocess
import time

import RPi.GPIO as GPIO

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Logs go to the systemd journal (visible with: journalctl -u shutdown-button)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pin assignment
# ---------------------------------------------------------------------------
BUTTON_PIN = 26          # BCM numbering — the pin your button wire is on
# GPIO23 is available for a future use (LED status indicator, reboot button, etc.)
# LED_PIN = 23

# ---------------------------------------------------------------------------
# How long the button must be held before shutdown is triggered (seconds).
# A short accidental press won't do anything.
# ---------------------------------------------------------------------------
HOLD_SECONDS = 2.0

# ---------------------------------------------------------------------------
# Peripheral teardown stubs
# ---------------------------------------------------------------------------
# Each function below will eventually contain real cleanup code.
# For now they just log that they were called, so you can see the call order.

def teardown_camera():
    """
    TODO: Stop picamera2 capture, close the camera object.
    Example:
        camera.stop()
        camera.close()
    """
    log.info("teardown_camera() called — no-op for now")

def teardown_lighting():
    """
    TODO: Turn of LEDs
    """
    log.info("teardown_LED() called — no-op for now")


def teardown_i2c():
    """
    TODO: Close any open I2C bus handles / sensor objects.
    Example:
        sensor.close()
        i2c_bus.close()
    """
    log.info("teardown_i2c() called — no-op for now")


def teardown_spi():
    """
    TODO: Close SPI device handles.
    Example:
        spi.close()
    """
    log.info("teardown_spi() called — no-op for now")


def teardown_display():
    """
    TODO: Clear the ILI9341 display and release its SPI bus / DC / CS pins.
    Leaving the display mid-frame can cause visual artifacts on next boot.
    Example:
        display.fill(0)          # black screen
        display.show()
        display_spi.deinit()
    """
    log.info("teardown_display() called — no-op for now")


def teardown_gpio():
    """
    Release all GPIO claims so the kernel isn't left with orphaned exports.
    Always call this last, after all peripherals that use GPIO are done.
    """
    log.info("teardown_gpio() — releasing all GPIO lines")
    GPIO.cleanup()


# ---------------------------------------------------------------------------
# Ordered shutdown sequence
# ---------------------------------------------------------------------------

def full_shutdown():
    """
    Perform an orderly teardown of all peripherals, then issue a system halt.
    Call order matters:
      1. Camera first (DMA-heavy; stop before touching SPI/I2C)
      2. Lighting (LED banks)
      3. Display (SPI peripheral)
      4. SPI bus
      5. I2C bus
      6. GPIO (must be last)
      7. os-level shutdown
    """
    log.info("--- Shutdown sequence initiated ---")

    teardown_camera()
    teardown_lighting()
    teardown_display()
    teardown_spi()
    teardown_i2c()
    teardown_gpio()

    log.info("All peripherals released. Issuing system halt now.")

    # 'shutdown -h now' halts cleanly; systemd will SIGTERM remaining services.
    subprocess.run(["sudo", "shutdown", "-h", "now"], check=False)


# ---------------------------------------------------------------------------
# Button callback (runs in RPi.GPIO's internal thread)
# ---------------------------------------------------------------------------

def button_pressed(channel):
    """
    Called on the FALLING edge of GPIO26 (button push pulls pin LOW).
    We re-check the pin after HOLD_SECONDS to confirm a deliberate press,
    not a noise spike or an accidental bump.
    """
    log.info("Button event detected on GPIO%d — checking hold time …", channel)

    time.sleep(HOLD_SECONDS)

    if GPIO.input(BUTTON_PIN) == GPIO.LOW:
        # Still held down after the hold period — this is intentional.
        log.info("Hold confirmed (%.1f s). Proceeding with shutdown.", HOLD_SECONDS)
        full_shutdown()
    else:
        log.info("Button released before hold threshold — ignoring.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("Shutdown button service starting up.")
    log.info("Using BCM pin numbering. Button on GPIO%d.", BUTTON_PIN)

    # BCM mode: pin numbers match the GPIO number, not the physical pin number.
    GPIO.setmode(GPIO.BCM)

    # PUD_UP: internal pull-up resistor keeps pin HIGH until button pulls to GND.
    GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    # Detect FALLING edge (HIGH→LOW = button pressed).
    # bouncetime=300 ms ignores mechanical contact bounce after the initial edge.
    GPIO.add_event_detect(
        BUTTON_PIN,
        GPIO.FALLING,
        callback=button_pressed,
        bouncetime=300,
    )

    log.info(
        "Listening. Hold button for %.1f s to trigger shutdown.", HOLD_SECONDS
    )

    try:
        # Keep the main thread alive; the callback runs in a background thread.
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        # Graceful exit when run interactively (Ctrl+C).
        log.info("KeyboardInterrupt received — cleaning up GPIO and exiting.")
        GPIO.cleanup()


if __name__ == "__main__":
    main()
