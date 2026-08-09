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

# Enable simulation mode.  This is read at import time by the driver modules
# to choose mock hardware, so it has to be set before they are imported.
os.environ['COLORIMETER_SIMULATION'] = '1'

# Import and run the app
import db_bootstrap
import splash_screen
from arbiter import Arbiter

if __name__ == "__main__":
    app = None
    try:
        # Create, repair and verify the database before anything reads it.
        # Timed into a BootLog because it runs before there is any display to
        # report it on; the splash screen replays it once there is.
        boot = splash_screen.BootLog()
        with boot.step("Database"):
            result = db_bootstrap.ensure()
        boot.restored = result.restored

        app = Arbiter()
        app.start(boot=boot)
    except db_bootstrap.DatabaseError as e:
        print(f"Database error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        # Normally unreachable once Arbiter._init_hardware() has installed its
        # own SIGINT handler (which calls app.shutdown() directly); this stays
        # as a fallback for Ctrl-C arriving before that point.
        print("\nShutdown requested")
        if app:
            app.shutdown()
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        if app:
            app.shutdown(error=e)
        sys.exit(1)
