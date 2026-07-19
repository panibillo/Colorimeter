"""
utils.py  —  shared utilities for Colorimeter scripts
"""

import signal

def get_pi_model():
    try:
        with open("/sys/firmware/devicetree/base/model", "r") as f:
            return f.read().strip("\x00\n")
    except Exception:
        return None

def install_exit_signals():
    """
    Map SIGTSTP (Ctrl-Z), SIGTERM, and SIGQUIT (Ctrl-\\) to KeyboardInterrupt
    so all three are handled by the same try/except as Ctrl-C (SIGINT).
    Called automatically by TSC2007.init(); also importable for scripts that
    do not instantiate TSC2007 (e.g. test_display.py).
    Safe to call more than once.
    """
    def _raise(signum, _):
        raise KeyboardInterrupt(f"signal {signal.Signals(signum).name}")

    for sig in (signal.SIGTSTP, signal.SIGTERM, signal.SIGQUIT):
        try:
            signal.signal(sig, _raise)
        except (OSError, ValueError):
            pass  # signal not available on this platform
