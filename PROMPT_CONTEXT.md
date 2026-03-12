# Goal

The goal is to implement simple, clear, well documented scripts that perform individual steps in data processing and solving.

# Expectations

Use a writing tone that is friendly and kind, constructively critical, not sycophantic.  

Be rather explicit about steps I need to take, and how to take them.

Never read any file named secret*.*.

Propose minimal changes to files.  

Propose changes that affect fewer files.

Use custom project methods when they are available

When proposing changes that are larger, first present an overview description, with at least one more constrained alternative, before continuing.

# IDE

Program using vscode IDE (version 1.100.2)

Assume that the program run on a Raspberry Pi Zero W, but the testing environment is a Linux desktop: Ubuntu or Pop!_OS

# Project Overview

This is a Python 3.10 project that will run in Python > 3.10. 

Do not use deprecated Python features.  The code should be futurproof.

Open source MIT license, Copywrite William C Olsen

# Libraries
- Do not use Pandas or SQLAlchemy
- Use numpy, picamera2, libcamera, cv2, sqlite3, and standard library classes.

# Code Style

- Use PEP8-style naming (snake_case for functions and variables)
- Use snake_case for files and ProperCase for classes
- One class per file if applicable
- Include docstrings for all functions and classes

# Licensing

- Make sure that software licensing requirements are respected when external resources are used.
- Use SPDX license identifiers in order to be more concise. reference: https://spdx.org/licenses/
- License new code under SPDX MIT when possible.
- Prefer SPDX MIT before SPDX GPL-xxx