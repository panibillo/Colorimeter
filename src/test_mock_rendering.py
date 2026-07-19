#!/usr/bin/env python3
"""
Test script to verify mock rendering and event handling.
"""

import os
import sys
import logging
import time

os.environ['COLORIMETER_SIMULATION'] = '1'

logging.basicConfig(
    level=logging.DEBUG,
    format='%(levelname)-8s [%(name)s] %(message)s'
)

log = logging.getLogger(__name__)

# Test 1: Load screens
print("\n=== Test 1: Load screens from CSV ===")
from csv_to_widgets import load_all_screens

try:
    screens = load_all_screens('widget_defs.csv', 'screen_defs.csv', 'screen_widgets.csv')
    print(f"✓ Loaded {len(screens)} screens")
    for screen_id, screen in screens.items():
        print(f"  {screen_id}: {len(screen.tabbed_widgets)} tabbed, {len(screen.untabbed_widgets)} untabbed")
except Exception as e:
    print(f"✗ Failed to load: {e}")
    sys.exit(1)

# Test 2: Initialize display and render
print("\n=== Test 2: Initialize display and render ===")
from mock.ili9341_mock import ILI9341
from screen_manager import ScreenManager

try:
    display = ILI9341()
    display.init()
    print("✓ Display initialized")

    mgr = ScreenManager(display)
    se_screen = screens['SE']
    print(f"✓ Rendering SE screen with {len(se_screen.tabbed_widgets) + len(se_screen.untabbed_widgets)} widgets")
    mgr.switch_to(se_screen)
    print("✓ Screen rendered")
except Exception as e:
    print(f"✗ Failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 3: Check event handlers
print("\n=== Test 3: Check event handlers ===")
print(f"Display has register_key_handler: {hasattr(display, 'register_key_handler')}")
print(f"Display has register_mouse_handler: {hasattr(display, 'register_mouse_handler')}")

# Test 4: Simulate a click
print("\n=== Test 4: Simulate mouse click and keyboard ===")
from mock.tsc2007_mock import TSC2007
from mock.kb_reader_mock import KbReader

touch = TSC2007()
kb = KbReader()

touch.init()
kb.open()

# Wire event handlers
if hasattr(display, 'register_key_handler'):
    display.register_key_handler(kb._on_key_event)
    print("✓ Keyboard handler registered")

if hasattr(display, 'register_mouse_handler'):
    display.register_mouse_handler(touch._on_touch)
    print("✓ Mouse handler registered")

if hasattr(display, 'register_touch_release_handler'):
    display.register_touch_release_handler(touch._on_release)
    print("✓ Touch release handler registered")

# Simulate events
print("\nSimulating touch event at (100, 50)...")
touch._on_touch(100, 50)
print(f"  touch.is_touched = {touch.is_touched}")
print(f"  touch.position() = {touch.position()}")

print("\nSimulating Tab key...")
kb._on_key_event('tab')
print(f"  kb.read_one() = {kb.read_one()}")

print("\nSimulating touch release...")
touch._on_release()
print(f"  touch.is_touched = {touch.is_touched}")

print("\n=== All tests passed ===")
print("\nNow showing matplotlib window for 10 seconds...")
print("(Click in the window to test events)")

import matplotlib.pyplot as plt
for i in range(10):
    plt.pause(1)
    print(f"  {10-i} seconds remaining... (touch: {touch.is_touched}, keys: {list(kb._queue)})")

print("\nClosing...")
display.cleanup()
