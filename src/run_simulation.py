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
from arbiter import Arbiter

if __name__ == "__main__":
    app = None
    try:
        app = Arbiter()
        app.start()
    except KeyboardInterrupt:
        print("\nShutdown requested")
        if app:
            app.cleanup()
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
