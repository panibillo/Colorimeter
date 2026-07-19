#!/usr/bin/env python3
"""
Launch the Colorimeter application in simulation mode.

Simulation mode uses mock hardware drivers:
- Display: matplotlib window
- Touch: mouse clicks on the matplotlib window
- Keyboard: keyboard events in the matplotlib window

Usage:
    python3 src/run_simulation.py
"""

import os
import sys

# Enable simulation mode
os.environ['COLORIMETER_SIMULATION'] = '1'

# Import and run the app
from app_controller import AppController

if 1:
    from csv_to_widgets import load_all_screens

    screens = load_all_screens('src/widget_defs.csv', 'src/screen_defs.csv', 'src/screen_widgets.csv')
    se_screen = screens['SE']

    print(f"SE screen has {len(se_screen.tabbed_widgets)} tabbed, {len(se_screen.untabbed_widgets)} untabbed widgets\n")

    print("Label widgets in SE:")
    for w in se_screen.untabbed_widgets:
        if w.widget_type == "Label":
            print(f"  {w.widget_id}: text='{w.label}' fgcolor={w.fgcolor} bgcolor={w.bgcolor}")

if __name__ == "__main__":
    try:
        app = AppController()
        app.start()
    except KeyboardInterrupt:
        print("\nShutdown requested")
        app.cleanup()
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
