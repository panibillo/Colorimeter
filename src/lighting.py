"""
lighting.py - LED lighting board controller for the colorimeter.

Controls individual LEDs via gpiozero GPIO pins.  Falls back to a
console-logging stub when gpiozero is unavailable (desktop testing).

Usage:
    from lighting import LedController, LED_ORDER

    leds = LedController()
    leds.on("Blue")
    leds.off("Blue")
    leds.close()
"""

try:
    from gpiozero import LED as GpioLED, Device as GpioDevice
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False

# LED GPIO pin numbers (BCM numbering), matching blink.py
LED_PINS = {
    "White":  22,
    "Red":     5,
    "Green":   6,
    "Blue":   26,
    "Violet": 27,
}

# Standard single-LED capture order
LED_ORDER = ["White", "Red", "Green", "Blue", "Violet"]

# Lightboard 3 capture sequence: (label, [led_names]).
# Blue appears twice; labels B1/B2 distinguish the two steps.
LIGHTBOARD_3_SEQUENCE = [
    ("B1",  ["Blue"]),
    ("GV",  ["Green", "Violet"]),
    ("BGV", ["Blue", "Green", "Violet"]),
    ("B2",  ["Blue"]),
    ("G",   ["Green"]),
    ("V",   ["Violet"]),
]


class LedController:
    """
    Controls the colorimeter LED lighting board via gpiozero.

    All LEDs are initialized to OFF and driven individually.
    Falls back to a console-logging stub when gpiozero is unavailable,
    so the rest of the code runs unchanged on a desktop.
    """

    def __init__(self) -> None:
        """
        Initialize all LED GPIO pins and ensure they start OFF.

        If any pin reports 'GPIO busy' (left claimed by a previous run that
        did not clean up), the gpiozero pin factory is closed to release
        stale lgpio handles and the initialization is retried once.

        Raises:
            RuntimeError: If any GPIO pin fails to initialize after retry
        """
        self._leds: dict = {}
        if GPIO_AVAILABLE:
            self._init_leds()
            print("  LED controller ready (GPIO)")
        else:
            print("  LED controller ready (stub — no GPIO detected)")

    def _init_leds(self) -> None:
        """Claim all LED pins, retrying once if any pin reports GPIO busy."""
        try:
            for name, pin in LED_PINS.items():
                led = GpioLED(pin)
                led.off()
                self._leds[name] = led
        except Exception as exc:
            if "GPIO busy" not in str(exc):
                raise
            # A previous run left one or more pins claimed without cleanup.
            # Close the gpiozero pin factory (which releases the lgpio
            # chip handle and all its claims) then retry.
            print("  WARNING: GPIO busy — closing pin factory and retrying "
                  "(previous run may not have exited cleanly)")
            try:
                GpioDevice.pin_factory.close()
                for led in self._leds.values():
                    led.close()
            except Exception:
                pass
            self._leds.clear()
            for name, pin in LED_PINS.items():
                led = GpioLED(pin)
                led.off()
                self._leds[name] = led

    def on(self, color: str) -> None:
        """
        Turn on the named LED.

        Args:
            color: One of "White", "Red", "Green", "Blue", "Violet"

        Raises:
            KeyError: If color is not a recognized LED name
        """
        if color not in LED_PINS:
            raise KeyError(f"Unknown LED color: {color!r}")
        if GPIO_AVAILABLE:
            self._leds[color].on()
            print(f"{color} LED ON")
        else:
            print(f"    [stub] {color} LED ON")

    def off(self, color: str) -> None:
        """
        Turn off the named LED.

        Args:
            color: One of "White", "Red", "Green", "Blue", "Violet"
        """
        if GPIO_AVAILABLE:
            self._leds[color].off()
            print(f"{color} LED OFF")
        else:
            print(f"    [stub] {color} LED OFF")

    def all_off(self) -> None:
        """Turn off all LEDs unconditionally."""
        if GPIO_AVAILABLE:
            for led in self._leds.values():
                led.off()
        else:
            print("    [stub] All LEDs OFF")

    def close(self) -> None:
        """Turn off all LEDs and release GPIO resources."""
        self.all_off()
        if GPIO_AVAILABLE:
            for led in self._leds.values():
                led.close()
