"""
Colorimeter Test Suite

Unit tests and verification scripts for the colorimeter project.
"""

import os

# Default to mock hardware for `unittest discover`, matching the "desktop
# tests use mock libraries" standard (CODING_GUIDELINES.md). setdefault()
# so an explicit override (e.g. COLORIMETER_SIMULATION=0 on real Pi
# hardware) still wins.
os.environ.setdefault("COLORIMETER_SIMULATION", "1")

__version__ = "0.1.0"
__all__ = []
