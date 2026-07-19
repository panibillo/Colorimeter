"""
hdf5read.py — generic HDF5 archive reader for image suites.
--------------------------------------------------------------
Used mainly on the desktop, also usable on the Rpi.

Provides Hdf5Reader, a thin context-manager wrapper around h5py.File that
mirrors the primitives written by hdf5write.Hdf5Writer: arrays, raw bytes,
JSON metadata dicts, attrs, and a printable tree summary. It has no
knowledge of any particular capture pipeline's file layout.

Usage:
    from hdf5read import Hdf5Reader

    with Hdf5Reader("suite.h5") as r:
        arr  = r.read_array("/leds/amber/lit/e3/R")
        meta = r.read_metadata("/leds/amber", "led_metadata")
        print(r.summary())

Command line:
    python3 hdf5read.py <path.h5>    # print a summary of the file's contents
"""

import json
import sys

import h5py
import numpy as np


class Hdf5Reader:
    """Generic HDF5 archive reader. Use as a context manager."""

    def __init__(self, path, mode: str = "r"):
        self._file = h5py.File(path, mode)

    def close(self) -> None:
        """Close the underlying HDF5 file. Safe to call more than once."""
        if self._file:
            self._file.close()
            self._file = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ── Public API ───────────────────────────────────────────────

    def read_array(self, dataset_path: str) -> np.ndarray:
        """Return the ndarray stored at dataset_path."""
        return self._file[dataset_path][()]

    def read_bytes(self, dataset_path: str) -> bytes:
        """Return the raw bytes stored at dataset_path (e.g. a JPEG)."""
        return self._file[dataset_path][()].tobytes()

    def read_metadata(self, group_path: str, name: str) -> dict:
        """Return the dict stored as a JSON-text dataset at group_path/name."""
        raw = self._file[group_path][name][()]
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        return json.loads(text)

    def attrs(self, path: str) -> dict:
        """Return a plain dict of the .attrs on the group or dataset at path."""
        return dict(self._file[path].attrs)

    def list_children(self, group_path: str = "/") -> list:
        """Return the names of immediate children (groups + datasets) of group_path."""
        return list(self._file[group_path].keys())

    def summary(self) -> str:
        """Return a printable summary of every group/dataset in the file."""
        lines = [f"HDF5 file: {self._file.filename}"]
        root_attrs = dict(self._file.attrs)
        if root_attrs:
            lines.append(f"  attrs: {root_attrs}")

        def _visit(name, obj):
            depth = name.count("/")
            indent = "  " * (depth + 1)
            if isinstance(obj, h5py.Group):
                lines.append(f"{indent}{name}/")
            else:
                if obj.dtype == np.uint8 and obj.ndim == 1:
                    desc = f"{obj.shape[0]} bytes"
                else:
                    desc = f"shape={obj.shape} dtype={obj.dtype}"
                lines.append(f"{indent}{name}  ({desc})")
            obj_attrs = dict(obj.attrs)
            if obj_attrs:
                lines.append(f"{indent}  attrs: {obj_attrs}")

        self._file.visititems(_visit)
        return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 hdf5read.py <path.h5>")
        sys.exit(1)

    with Hdf5Reader(sys.argv[1]) as r:
        print(r.summary())
