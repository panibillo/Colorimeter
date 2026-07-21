#!/usr/bin/env python3
"""
Colorimeter application launcher (RPi hardware mode).

Run this on the Raspberry Pi to start the application with real hardware drivers.
For desktop simulation, use: run_simulation.py

Usage:
    python3 main.py

Signals:
    Ctrl-C (SIGINT)   → graceful shutdown
    Ctrl-Z (SIGTSTP)  → pause process (resume with fg command)
    SIGTERM           → graceful shutdown
"""

import sys
import signal

from arbiter import Arbiter


def main():
    """Launch the Colorimeter application."""
    app = None

    def handle_sigtstp(signum, frame):
        """Handle Ctrl-Z by ignoring it (allows pause/bg/fg)."""
        # Re-enable default SIGTSTP handling to allow pause/resume
        signal.signal(signal.SIGTSTP, signal.SIG_DFL)
        signal.raise_signal(signal.SIGTSTP)
        # When resumed, re-install our handler
        signal.signal(signal.SIGTSTP, handle_sigtstp)

    try:
        # Install handler for Ctrl-Z to allow pause/resume
        signal.signal(signal.SIGTSTP, handle_sigtstp)

        app = Arbiter()
        app.start()

    except KeyboardInterrupt:
        print("\nShutdown requested (Ctrl-C)")
        if app:
            app.cleanup()
        sys.exit(0)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        if app:
            app.cleanup()
        sys.exit(1)


if __name__ == "__main__":
    main()
