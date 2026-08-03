#!/usr/bin/env python3
"""
build_h5_from_camtest8.py — convert a camtest8.py capture directory to HDF5.
------------------------------------------------------------------------------
Converter: reads the files produced by dev/camtest8.py in a capture directory
(excluding .png files) and writes a single HDF5 archive using the schema in
src/capture_h5.py. Then re-opens the file with Hdf5Reader and prints a
summary, to demonstrate both paths.

All camtest8-specific *filename* knowledge lives in this script. The archive
*layout* lives in capture_h5.CaptureSuiteWriter, shared with the live capture
path so the two cannot drift apart.

Note on exposure precision: camtest8 filenames record exposure only in whole
milliseconds, so darks recovered from a legacy directory carry millisecond
precision. Exact microseconds are read from the per-LED metadata JSON where
available (lit frames and dark links).

Usage:
    python3 dev/build_h5_from_camtest8.py [capture_dir] [output_h5]

    capture_dir : directory of camtest8 output files (default:
                  dev/captures/20260615_2120)
    output_h5   : path to write (default: <capture_dir>.h5, a sibling of
                  capture_dir)
"""

import ast
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np

from capture_h5 import CaptureSuiteWriter
from hdf5read import Hdf5Reader

STEM = "camtest8"
EXP_LABELS = ["e0", "e1", "e2", "e3", "e4", "e5", "e6"]

_METADATA_RE = re.compile(rf"^{STEM}_(.+)_metadata\.json$")
_JPEG_RE     = re.compile(rf"^{STEM}_(.+)_e3\.jpg$")
_TXT_RE      = re.compile(rf"^{STEM}_(.+)_e3\.txt$")
_BOUND_RE    = re.compile(rf"^{STEM}_BOUND_(.+)_(e[0-6])_(\d+)ms_(B|Gb|Gr|R)\.npy$")

PICAMERA2_MARKER = "--- picamera2_metadata ---"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _coerce_number(val: str):
    """Best-effort int → float → str conversion for image_stats values."""
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        return val


def _coerce_literal(val: str):
    """Parse a Python repr() value (tuple, int, float, None, ...) safely."""
    try:
        return ast.literal_eval(val)
    except (ValueError, SyntaxError):
        return val


def parse_capture_info_txt(text: str) -> dict:
    """Parse a camtest8 *_e3.txt sidecar into {image_stats, picamera2_metadata}."""
    lines = text.splitlines()
    idx = lines.index(PICAMERA2_MARKER) if PICAMERA2_MARKER in lines else len(lines)

    image_stats = {}
    for line in lines[:idx]:
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        image_stats[key.strip()] = _coerce_number(val.strip())

    picamera2_metadata = {}
    for line in lines[idx + 1:]:
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        picamera2_metadata[key.strip()] = _coerce_literal(val.strip())

    return {"image_stats": image_stats, "picamera2_metadata": picamera2_metadata}


# ---------------------------------------------------------------------------
# Directory scan
# ---------------------------------------------------------------------------

def scan_capture_dir(capture_dir: Path) -> dict:
    """
    Classify every non-.png file in capture_dir.

    Returns:
        {
          "metadata": {color: Path},
          "jpeg":     {color: Path},
          "txt":      {color: Path},
          "lit":      {(color, label, ch): Path},
          "dark":     {(label, ms, ch): Path},   # color == "black"
        }

    The millisecond field in a lit filename is dropped: the exact exposure
    is read from the per-LED metadata instead. Dark filenames are the only
    record of their own exposure, so ms is kept in that key.
    """
    result = {"metadata": {}, "jpeg": {}, "txt": {}, "lit": {}, "dark": {}}

    for path in sorted(capture_dir.iterdir()):
        if path.suffix == ".png":
            continue

        m = _METADATA_RE.match(path.name)
        if m:
            result["metadata"][m.group(1)] = path
            continue

        m = _JPEG_RE.match(path.name)
        if m:
            result["jpeg"][m.group(1)] = path
            continue

        m = _TXT_RE.match(path.name)
        if m:
            result["txt"][m.group(1)] = path
            continue

        m = _BOUND_RE.match(path.name)
        if m:
            color, label, ms, ch = m.group(1), m.group(2), int(m.group(3)), m.group(4)
            if color == "black":
                result["dark"][(label, ms, ch)] = path
            else:
                result["lit"][(color, label, ch)] = path
            continue

        print(f"  (skipping unrecognized file: {path.name})")

    return result


# ---------------------------------------------------------------------------
# Build the HDF5 file
# ---------------------------------------------------------------------------

def _group_channels(entries: dict) -> dict:
    """
    Regroup a {(…, ch): path} mapping into {(…): {ch: path}}.

    The trailing element of each key is the Bayer channel name; everything
    before it identifies the frame. Used to gather the four per-channel .npy
    files that make up one capture into a single layers dict.
    """
    grouped: dict = {}
    for key, path in entries.items():
        frame_key, ch = key[:-1], key[-1]
        grouped.setdefault(frame_key, {})[ch] = path
    return grouped


def _load_layers(paths_by_ch: dict) -> dict:
    """Load a {ch: path} mapping into a {ch: ndarray} layers dict."""
    return {ch: np.load(path) for ch, path in paths_by_ch.items()}


def build(capture_dir: Path, output_h5: Path) -> None:
    print(f"Scanning {capture_dir} ...")
    files = scan_capture_dir(capture_dir)
    colors = sorted(files["metadata"])
    print(f"  found {len(colors)} LED color(s): {colors}")

    with CaptureSuiteWriter(output_h5, suite_id=capture_dir.name) as w:

        # ── Per-color led_metadata / capture_info / jpeg ───────────────
        led_metadata_by_color = {}
        for color in colors:
            meta = json.loads(files["metadata"][color].read_text())
            led_metadata_by_color[color] = meta

            w.add_led(color, meta, led_channel=meta["led_channel"])

            if color in files["txt"]:
                capture_info = parse_capture_info_txt(files["txt"][color].read_text())
                w.add_capture_info(color, capture_info)

            if color in files["jpeg"]:
                jpeg_path = files["jpeg"][color]
                w.add_jpeg(color, jpeg_path.read_bytes(), filename=jpeg_path.name)

        # ── Lit frames: exact microseconds come from the metadata ──────
        for (color, label), paths_by_ch in _group_channels(files["lit"]).items():
            exp = led_metadata_by_color[color]["computed_exposures"][label]
            w.add_lit(color, label, _load_layers(paths_by_ch),
                      exposure_us=exp["exposure_us"])

        # ── Shared dark frames, keyed by exposure — not by color or label ──
        # Legacy filenames carry only whole milliseconds, so scale back up.
        for (label, ms), paths_by_ch in _group_channels(files["dark"]).items():
            w.add_dark(_load_layers(paths_by_ch), exposure_us=ms * 1000)

        # Link every color+label whose own exposure matches a dark group.
        for color in colors:
            for label in EXP_LABELS:
                exp = led_metadata_by_color[color]["computed_exposures"].get(label)
                if exp is None:
                    continue
                w.link_dark(color, label, exposure_us=exp["exposure_us"])

    print(f"Wrote {output_h5}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    default_dir = Path(__file__).parent / "captures" / "20260615_2120"
    capture_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else default_dir
    output_h5 = Path(sys.argv[2]) if len(sys.argv) > 2 else capture_dir.with_suffix(".h5")

    build(capture_dir, output_h5)

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    with Hdf5Reader(output_h5) as r:
        print(r.summary())


if __name__ == "__main__":
    main()
