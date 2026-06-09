#!/usr/bin/env python3
"""
proto_main.py  —  Entry point for the prototype application.

All hardware, screen building, navigation, and the event loop
live in src/app_controller.py.

    python3 dev/proto_main.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from app_controller import AppController

if __name__ == "__main__":
    AppController().start()
