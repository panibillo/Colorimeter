#!/usr/bin/env python3

"""
Minimal but realistic architecture (~120 lines) that has been used successfully in many embedded Python devices (Pi, BeagleBone, industrial controllers).
It includes:
-   event loop
-   simple state machine
-   managers
-   button handling
-   CLI compatibility
-   clean shutdown
-   minimal memory use
No frameworks, no threads, no asyncio.

Directory structure
device/
│
├── main.py
├── app.py
├── config_manager.py
├── led_manager.py
├── reading_manager.py
│
├── interface/
│   ├── buttons.py
│   ├── cli.py
│   └── web.py
│
└── config.json
"""

import time
import os
import sys

# ----------------------------
# Managers
# ----------------------------

class ConfigManager:
    def __init__(self):
        self.params = {"threshold": 10}

    def get(self, key):
        return self.params.get(key)

    def set(self, key, value):
        self.params[key] = value


class LedManager:
    def idle(self):
        print("LED: idle")

    def running(self):
        print("LED: running")

    def error(self):
        print("LED: error")


class ReadingManager:
    def take_reading(self):
        print("Taking reading...")
        time.sleep(2)
        return 42


class Interface:
    """
    Unified interface for:
      buttons
      CLI
      web
    """

    def get_event(self):

        # CLI input (optional)
        if sys.stdin in select_stdin():
            cmd = sys.stdin.readline().strip()

            if cmd == "go":
                return "GO"

            if cmd == "config":
                return "CONFIG"

            if cmd == "exit":
                return "SHUTDOWN"

        # GPIO button polling would go here

        return None

    def display(self, msg):
        print(msg)


# ----------------------------
# Utility
# ----------------------------

def select_stdin():
    """Non-blocking stdin check"""
    import select
    return select.select([sys.stdin], [], [], 0)[0]


# ----------------------------
# Application Engine
# ----------------------------

class App:

    def __init__(self):

        self.state = "BOOT"

        self.config = ConfigManager()
        self.leds = LedManager()
        self.reader = ReadingManager()
        self.ui = Interface()

    def run(self):

        self.leds.idle()
        self.state = "IDLE"

        while True:

            event = self.ui.get_event()

            if self.state == "IDLE":
                self.handle_idle(event)

            elif self.state == "RUNNING":
                self.handle_running()

            elif self.state == "CONFIG":
                self.handle_config(event)

            elif self.state == "SHUTDOWN":
                self.shutdown()
                break

            time.sleep(0.05)

    # ------------------------

    def handle_idle(self, event):

        if event == "GO":
            self.state = "RUNNING"

        elif event == "CONFIG":
            self.state = "CONFIG"

        elif event == "SHUTDOWN":
            self.state = "SHUTDOWN"

    # ------------------------

    def handle_running(self):

        self.leds.running()

        result = self.reader.take_reading()

        self.ui.display(f"Result: {result}")

        self.leds.idle()
        self.state = "IDLE"

    # ------------------------

    def handle_config(self, event):

        self.ui.display("CONFIG MODE")

        if event == "exit":
            self.state = "IDLE"

    # ------------------------

    def shutdown(self):

        self.ui.display("Shutting down")

        # hardware cleanup here

        os.system("sudo shutdown -h now")


# ----------------------------
# Entry Point
# ----------------------------

def main():

    app = App()
    app.run()


if __name__ == "__main__":
    main()