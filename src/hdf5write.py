"""
hdf5write.py — generic HDF5 archive writer for image suites.
--------------------------------------------------------------
Used mainly on the Rpi (to archive a completed capture+analysis suite),
also usable on the desktop during development.

Provides Hdf5Writer, a thin context-manager wrapper around h5py.File
exposing a few generic primitives (array, bytes, metadata dict, attrs,
soft link). It has no knowledge of any particular capture pipeline's file
layout — callers decide group/dataset names and schema.

Usage:
    from hdf5write import Hdf5Writer

    with Hdf5Writer("suite.h5") as w:
        w.set_attrs("/", {"suite_id": "20260615_2120"})
        w.write_metadata("/leds/amber", "led_metadata", metadata_dict)
        w.write_array("/leds/amber/lit/e3", "R", roi_array, attrs={"exposure_us": 45691})
        w.write_bytes("/leds/amber", "jpeg_e3", jpeg_bytes, attrs={"format": "jpeg"})
        w.link("/dark/e3_45ms", "/leds/amber/dark/e3")
"""

import json

import h5py
import numpy as np


class Hdf5Writer:
    """Generic HDF5 archive writer. Use as a context manager."""

    def __init__(self, path, mode: str = "w"):
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

    # ── Group access ─────────────────────────────────────────────

    def _require_group(self, group_path: str) -> h5py.Group:
        """Return the group at group_path, creating intermediate groups as needed."""
        return self._file.require_group(group_path)

    # ── Public API ───────────────────────────────────────────────

    def write_array(
        self, group_path: str, name: str, array: np.ndarray,
        attrs: dict = None, compression: str = "gzip",
    ) -> None:
        """
        Write an ndarray dataset under group_path, creating groups as needed.

        Args:
            group_path: HDF5 group path (e.g. "/leds/amber/lit/e3").
            name:       Dataset name within that group (e.g. "R").
            array:      Array to store.
            attrs:      Optional dict of attributes to set on the dataset.
            compression: Passed to h5py.Group.create_dataset (default gzip).
        """
        grp = self._require_group(group_path)
        ds = grp.create_dataset(name, data=array, compression=compression)
        if attrs:
            for key, val in attrs.items():
                ds.attrs[key] = val

    def write_bytes(
        self, group_path: str, name: str, data: bytes, attrs: dict = None,
    ) -> None:
        """
        Write raw bytes (e.g. a JPEG file's contents) as an opaque uint8 dataset.

        Args:
            group_path: HDF5 group path.
            name:       Dataset name within that group.
            data:       Raw byte content.
            attrs:      Optional dict of attributes to set on the dataset.
        """
        grp = self._require_group(group_path)
        ds = grp.create_dataset(name, data=np.frombuffer(data, dtype=np.uint8))
        if attrs:
            for key, val in attrs.items():
                ds.attrs[key] = val

    def write_metadata(self, group_path: str, name: str, data: dict) -> None:
        """
        Write a (possibly nested) dict as a JSON-text dataset.

        Args:
            group_path: HDF5 group path.
            name:       Dataset name within that group.
            data:       JSON-serializable dict.
        """
        grp = self._require_group(group_path)
        grp.create_dataset(name, data=json.dumps(data))

    def set_attrs(self, path: str, attrs: dict) -> None:
        """
        Set attributes on a group or dataset, creating the group if needed.

        Args:
            path:  HDF5 path to a group or dataset.
            attrs: Dict of attributes to set.
        """
        target = self._file[path] if path in self._file else self._require_group(path)
        for key, val in attrs.items():
            target.attrs[key] = val

    def link(self, target_path: str, link_path: str) -> None:
        """
        Create an HDF5 soft link at link_path pointing to target_path.

        Args:
            target_path: Existing HDF5 path the link resolves to.
            link_path:   New path to create as a soft link.
        """
        self._file[link_path] = h5py.SoftLink(target_path)
