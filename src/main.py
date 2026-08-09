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

import db_bootstrap
import splash_screen
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

        # Create, repair and verify the database before anything reads it.
        # The Arbiter loads settings in its constructor, so this has to come
        # first — which also means it runs before there is any display to
        # report it on, so it is timed into a BootLog for the splash screen to
        # replay once there is.
        boot = splash_screen.BootLog()
        with boot.step("Database"):
            result = db_bootstrap.ensure()
        boot.restored = result.restored

        app = Arbiter()
        app.start(boot=boot)

    except db_bootstrap.DatabaseError as e:
        # A schema problem needs an operator decision (migrate, restore a
        # backup), not a stack trace.
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)

    except KeyboardInterrupt:
        # Normally unreachable once Arbiter._init_hardware() has installed its
        # own SIGINT handler (which calls app.shutdown() directly); this stays
        # as a fallback for Ctrl-C arriving before that point.
        print("\nShutdown requested (Ctrl-C)")
        if app:
            app.shutdown()
        sys.exit(0)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        if app:
            app.shutdown(error=e)
        sys.exit(1)


if __name__ == "__main__":
    main()
