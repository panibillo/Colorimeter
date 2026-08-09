"""
DS18B20.py  —  1-Wire temperature sensing driver
-------------------------------------------------
Hardware:
  Dallas DS18B20 temperature sensor(s)
  1-Wire data bus on GPIO16 (pin 36)  —  non-default pin
  Requires in /boot/firmware/config.txt:
      dtoverlay=w1-gpio,gpiopin=16
  Requires 4.7kΩ pull-up resistor to 3.3V on the data line (PCB-fitted).

The kernel w1 driver exposes each sensor as a directory under:
  /sys/bus/w1/devices/<device_id>/
Temperature is read from the w1_slave file in that directory.

The app uses this through the `DS18B20` class (see the arbiter's
`_init_hardware()`): `init()` once, `read_c()` for the primary sensor's
temperature, `cleanup()` on shutdown.  The module-level functions below are the
core logic the class is built on, and are also what the `__main__` CLI uses.

Usage as a test script:
  python3 DS18B20.py                   # sample every 2 seconds (default)
  python3 DS18B20.py 5                 # sample every 5 seconds
  python3 DS18B20.py 0.5               # sample every 0.5 seconds
  Ctrl+C to stop.
"""

import glob
import logging
import time
import sys

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────
# Public: the boot-time completeness check reads it to tell "the 1-Wire bus
# is not enabled in config.txt" apart from "the bus is up but no probe is
# wired to it" — two different repairs, and only one of them needs a
# screwdriver.  See provisioning/deploy/commissioning_status.sh, step 2.
W1_ROOT      = "/sys/bus/w1/devices/"
_DS18B20_PREFIX = "28-"          # DS18B20 family code
_W1_SLAVE_FILE  = "w1_slave"

_DEFAULT_INTERVAL = 2.0          # seconds


# ── Device discovery ──────────────────────────────────────────

def find_sensors():
    """
    Return a list of device ID strings for all DS18B20 sensors found
    on the 1-Wire bus.  Returns an empty list if none are found.

    Example return value: ['28-0123456789ab', '28-0123456789cd']
    """
    paths = glob.glob(W1_ROOT + _DS18B20_PREFIX + "*")
    return [p.split("/")[-1] for p in paths]


# ── Temperature reading ───────────────────────────────────────

def read_raw(device_id):
    """
    Read the raw w1_slave file for the given device ID string.
    Returns the file contents as a list of two strings (lines),
    or raises OSError if the device is not accessible.
    """
    path = W1_ROOT + device_id + "/" + _W1_SLAVE_FILE
    with open(path, "r") as f:
        return f.readlines()


def read_temp_c(device_id):
    """
    Read temperature in Celsius from one DS18B20 sensor.

    Returns the temperature as a float, or None if the CRC check
    failed or the device is not readable.

    The w1_slave file format is:
      50 05 4b 46 7f ff 0c 10 1c : crc=1c YES
      50 05 4b 46 7f ff 0c 10 1c t=21312
    Temperature in millidegrees C follows 't='.
    """
    try:
        lines = read_raw(device_id)
    except OSError as e:
        log.warning("DS18B20: could not read %s: %s", device_id, e)
        return None

    # Line 1 ends with YES if CRC passed, NO if it failed
    if len(lines) < 2 or "YES" not in lines[0]:
        log.warning("DS18B20: CRC check failed for %s", device_id)
        return None

    # Parse temperature from line 2
    try:
        t_str = lines[1].split("t=")[-1].strip()
        return int(t_str) / 1000.0
    except (IndexError, ValueError) as e:
        log.warning("DS18B20: could not parse temperature for %s: %s", device_id, e)
        return None


def read_all_sensors():
    """
    Read all DS18B20 sensors on the bus.

    Returns a list of (device_id, temp_c) tuples.
    temp_c is None if a sensor could not be read.
    """
    sensors = find_sensors()
    return [(dev_id, read_temp_c(dev_id)) for dev_id in sensors]


# ── Formatted output ──────────────────────────────────────────

def format_reading(device_id, temp_c):
    """Return a formatted string for one sensor reading."""
    if temp_c is None:
        return f"  {device_id}  ERROR"
    return f"  {device_id}  {temp_c:+.3f} °C"

def txt_val(device_id, temp_c):
    """Return a formatted string for one sensor reading."""
    if device_id is None:
        return f"NOT CONNECTED"
    elif temp_c is None:
        return f"ERROR"
    return f"{temp_c:+.3f}"


# ── Driver class (what the app instantiates) ──────────────────

class DS18B20:
    """
    One-wire DS18B20 temperature sensor driver, one instance per run.

    Mirrors the shape of the other hardware drivers (`init()` / … /
    `cleanup()`) so the arbiter can swap in `mock.DS18B20_mock.DS18B20`
    unchanged.  The app reads a single sensor; `read_c()` returns the primary
    (first-discovered) sensor's temperature.
    """

    def __init__(self):
        self._sensors = []

    def init(self):
        """Discover sensors on the bus.  Safe to call even if none are wired."""
        self._sensors = find_sensors()
        if self._sensors:
            log.info("DS18B20: found %d sensor(s): %s",
                     len(self._sensors), ", ".join(self._sensors))
        else:
            log.warning("DS18B20: no sensors found on the 1-Wire bus")

    @property
    def connected(self):
        """True if at least one sensor was discovered."""
        return bool(self._sensors)

    def read_c(self):
        """
        Return the primary sensor's temperature in Celsius, or None if no
        sensor is connected or the reading failed.  Re-probes the bus when no
        sensor is currently known, so a sensor plugged in after start is picked
        up on the next read.
        """
        if not self._sensors:
            self._sensors = find_sensors()
        if not self._sensors:
            return None
        return read_temp_c(self._sensors[0])

    def read_all(self):
        """Return a list of (device_id, temp_c) tuples for every sensor."""
        return read_all_sensors()

    def cleanup(self):
        """No resources to release (sysfs is read-only); present for symmetry."""
        log.info("DS18B20: cleanup")


# ── Command-line entry point ──────────────────────────────────

def _usage():
    print("Usage: python3 DS18B20.py [interval_seconds]")
    print("  interval_seconds : sampling interval, default 2.0, minimum 0.1")
    print("  Ctrl+C to stop.")
    sys.exit(1)


if __name__ == "__main__":
    # Parse optional interval argument
    interval = _DEFAULT_INTERVAL
    if len(sys.argv) == 2:
        try:
            interval = float(sys.argv[1])
            if interval < 0.1:
                print("Error: minimum interval is 0.1 seconds")
                _usage()
        except ValueError:
            _usage()
    elif len(sys.argv) > 2:
        _usage()

    # Check that the w1 bus is available
    try:
        import os
        os.listdir(W1_ROOT)
    except FileNotFoundError:
        print("Error: /sys/bus/w1/devices/ not found.")
        print("Check that dtoverlay=w1-gpio,gpiopin=16 is in config.txt")
        print("and that the RPi has been rebooted since the overlay was added.")
        sys.exit(1)

    # Discover sensors
    sensors = find_sensors()
    if not sensors:
        print("No DS18B20 sensors found on the 1-Wire bus.")
        print("Check wiring and 4.7kΩ pull-up resistor.")
        sys.exit(1)

    print(f"Found {len(sensors)} sensor(s).  Sampling every {interval}s.  Ctrl+C to stop.\n")

    sample = 1
    try:
        while True:
            print(f"Sample {sample}  [{time.strftime('%H:%M:%S')}]")
            for dev_id, temp_c in read_all_sensors():
                print(format_reading(dev_id, temp_c))
            print()
            sample += 1
            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nStopped.")
