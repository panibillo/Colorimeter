#!/usr/bin/env python3
"""
proto_main.py  —  Entry point for the prototype application.

All hardware, screen building, navigation, and the event loop
live in src/app_controller.py.

    python3 dev/proto_main.py [--debug | --info | --warning]
"""

import argparse
import logging
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from app_controller import AppController

_LOG_FORMAT = "%(levelname)-8s %(name)s:%(lineno)d  %(message)s"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Colorimeter prototype")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--debug",   action="store_true", help="set log level to DEBUG")
    g.add_argument("--info",    action="store_true", help="set log level to INFO")
    g.add_argument("--warning", action="store_true", help="set log level to WARNING (default)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.debug:
        level = logging.DEBUG
    elif args.info:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(level=level, format=_LOG_FORMAT)
    AppController().start()
