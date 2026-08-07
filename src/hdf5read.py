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


def _as_json_text(obj) -> "str | None":
    """
    Return a dataset's contents as indented JSON, or None if it is not JSON.

    Used by summary() to tell a stored metadata record apart from an image
    array. A JSON record is a scalar dataset holding one string, so anything
    with a shape, or anything whose text does not parse, is not one and is
    left alone.

    Args:
        obj: An h5py dataset.

    Returns:
        Pretty-printed JSON text, or None.
    """
    if obj.shape != ():
        return None
    try:
        raw = obj[()]
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        return json.dumps(json.loads(text), indent=2, sort_keys=True)
    except Exception:       # noqa: BLE001 — not JSON, so not our business
        return None


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

    def summary(self, show_json: bool = True) -> str:
        """
        Return a printable summary of every group/dataset in the file.

        JSON-text datasets — the ones write_metadata() produced, such as
        /meta and each LED's led_metadata — are printed decoded and indented,
        not as "shape=() dtype=object". Without that the sample record, the
        ROI geometry and the camera settings are all in the file but invisible
        to anyone reading this summary, which is exactly the confusion this
        argument exists to prevent.

        Args:
            show_json: False prints just the shape of the JSON datasets, for
                when the decoded records make the summary too long to scan.

        Returns:
            The summary as one string, ready to print.
        """
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
                text = _as_json_text(obj)
                if show_json and text is not None:
                    for line in text.splitlines():
                        lines.append(f"{indent}    {line}")
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
