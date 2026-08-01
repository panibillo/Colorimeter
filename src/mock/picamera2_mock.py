"""
picamera2_mock: Mock library emulating Picamera2 for desktop testing

Loads test images and metadata from the mock_camera directory.
Naming convention:
    Single-stream RGB configs (1-3):
        cfg{1,2,3}_rgb888_00.jpg     → RGB image
        cfg{1,2,3}_rgb888_00.txt     → metadata

    Dual-stream RAW configs (4-6):
        cfg{4,5,6}_sbggr1_rgb_00.jpg    → RGB main stream
        cfg{4,5,6}_sbggr1_raw_00.npy    → Bayer raw stream (uint16, 10-bit unpacked)
        cfg{4,5,6}_sbggr1_raw_00.txt    → metadata

Usage:
    try:
        from picamera2 import Picamera2
    except ImportError:
        from mock.picamera2_mock import Picamera2
"""

import os

import numpy as np
from pathlib import Path
from types import SimpleNamespace as sns

# Mock libcamera enums.  Covers every attribute bayer_cam/capture_runner reach
# for, including the autofocus enums the imx708 path needs — without them the
# mock could only ever stand in for a fixed-focus imx219.
libcamera = sns(controls=sns(
    draft=sns(NoiseReductionModeEnum=sns(Off=0)),
    AfModeEnum=sns(Manual=0, Auto=1, Continuous=2),
    AfTriggerEnum=sns(Start=0, Cancel=1),
))

_REPO = Path(__file__).resolve().parent.parent.parent

# Which image the mock camera returns.  Set $COLORIMETER_MOCK_IMAGE to a stem
# or a .jpg path to override; otherwise the first of these that exists is used.
#
# Repo-relative, because an absolute path baked in here goes stale the moment
# the project is checked out anywhere else — which is exactly what happened to
# the previous value (a path under an old eclipse-workspace that no longer
# exists), leaving the mock camera raising FileNotFoundError on every capture.
#
# The first candidate is a real 2304x1296 prototype capture under the white
# LED, so the mock returns a frame the stored ROI rectangles actually apply
# to — which is what lets the image viewer be exercised in simulation.
_MOCK_CANDIDATES = (
    _REPO / "dev/captures/20260615_2120/camtest8_white_e3",   # 2304x1296, white LED
    _REPO / "mock_camera/cfg5_sbggr1_rgb_00ccB",              # dual-stream, cfg5
    _REPO / "mock_camera/cfg2_rgb888_00checkerB",             # colour-card checkerboard
    _REPO / "images/cfg3_rgb888_02-f135",
)

MOCKIMAGEFILE = os.environ.get("COLORIMETER_MOCK_IMAGE", "")


def _mock_stem() -> Path:
    """
    Return the mock image path as a stem (no extension).

    Resolution order: MOCKIMAGEFILE (which $COLORIMETER_MOCK_IMAGE sets) if it
    is non-empty, then the first existing entry in _MOCK_CANDIDATES.  If none
    exists the first candidate is returned anyway, so the caller raises its own
    FileNotFoundError naming a path that is at least meaningful.

    Both styles are supported for any of them:
      - Stem (no suffix):  used as-is, e.g. ".../camtest8_white_e3"
      - .jpg path:         suffix stripped
    """
    if MOCKIMAGEFILE:
        p = Path(MOCKIMAGEFILE)
        return p.with_suffix("") if p.suffix else p
    for cand in _MOCK_CANDIDATES:
        if cand.with_suffix(".jpg").exists():
            return cand
    return _MOCK_CANDIDATES[0]


class MockRequest:
    """Represents a captured request with main and/or raw stream data."""
    
    def __init__(self, main_array=None, raw_array=None, metadata=None):
        """
        Args:
            main_array: RGB888 data (uint8, H×W×3)
            raw_array: Raw Bayer data (uint16, H×W)
            metadata: dict of camera parameters at capture time
        """
        self.main_array = main_array
        self.raw_array = raw_array
        self.metadata = metadata or {}
        self._released = False
    
    def make_array(self, stream_name):
        """
        Extract data from a named stream.
        
        Args:
            stream_name: "main" (RGB888) or "raw" (Bayer SBGGR10)
        
        Returns:
            numpy array of appropriate dtype
        """
        if stream_name == "main":
            if self.main_array is None:
                raise ValueError("No main stream data in this request")
            return self.main_array
        elif stream_name == "raw":
            if self.raw_array is None:
                raise ValueError("No raw stream data in this request")
            return self.raw_array
        else:
            raise ValueError(f"Unknown stream: {stream_name}")
    
    def get_metadata(self):
        """Return metadata dict from capture time."""
        return self.metadata.copy()
    
    def release(self):
        """Release request resources (no-op in mock)."""
        self._released = True


class Picamera2:
    """Mock Picamera2 class emulating the real library's interface."""
    
    # Config mapping: config_key → (file_basename, is_raw)
    CONFIG_MAP = {
        "1": ("cfg1_rgb888", False),
        "2": ("cfg2_rgb888", False),
        "3": ("cfg3_rgb888", False),
        "4": ("cfg4_sbggr1", True),
        "5": ("cfg5_sbggr1", True),
        "6": ("cfg6_sbggr1", True),
    }
    
    @classmethod
    def global_camera_info(cls):
        """
        Return mock camera info list, simulating Picamera2.global_camera_info().

        Reports an IMX219 (Camera Module 2).  $COLORIMETER_MOCK_MODEL
        overrides — e.g. "imx708" to exercise the Camera Module 3 paths.

        **Known limitation.** The mock synthesizes an *unpacked* uint16 Bayer
        array when the served image has no .npy beside it, which matches
        imx219's SBGGR10 but not imx708's packed SBGGR10_CSI2P: selecting
        imx708 makes `unpack_raw10()` fail on the reshape.  So while the
        default mock image is a 2304x1296 imx708 capture, the mock still
        reports imx219, and `roi.load_active()` logs a cross-model mismatch
        for every ROI in simulation.  That warning is correct and worth
        leaving loud — the geometry really is being applied to a stream it was
        not defined against.  Making the mock serve packed raw would fix both,
        and is the right fix when the mock is next worked on.

        Returns:
            List of dicts, one per camera, with keys Id, Model, Location,
            Rotation, Num — the same structure as the real Picamera2.
        """
        return [{"Id": "mock",
                 "Model": os.environ.get("COLORIMETER_MOCK_MODEL", "imx219"),
                 "Location": 2, "Rotation": 180, "Num": 0}]

    def __init__(self):
        """Initialize mock camera."""
        self.started = False
        self.config = None
        self.controls = {}
        self.current_config_key = None
        self.current_basename = None
        self.is_raw_config = False
        self.main_array = None
        self.raw_array = None
        self.metadata = {}
        self.mock_stem = _mock_stem()
    
    def create_still_configuration(self, main=None, raw=None, **kwargs):
        """
        Create camera configuration.
        
        Args:
            main: dict with "size" and "format" for main stream
            raw: dict with "size" and "format" for raw stream (optional)
            **kwargs: other config parameters (ignored)
        
        Returns:
            config dict (returned for compatibility, not directly used in mock)
        """
        config = {
            "main": main or {},
            "raw": raw or {},
        }
        config.update(kwargs)
        return config
    
    def configure(self, config):
        """
        Apply configuration to camera.
        
        Args:
            config: dict returned from create_still_configuration()
        """
        self.config = config
        main_cfg = config.get("main", {})
        size = main_cfg.get("size")
        fmt = main_cfg.get("format")
        
        # Determine which config (1-6) based on size and format
        self.current_config_key = self._infer_config_key(size, fmt)

        # Dual-stream when a non-empty "raw" dict was passed to configure()
        self.is_raw_config = bool(config.get("raw"))

        if self.current_config_key:
            basename, _ = self.CONFIG_MAP[self.current_config_key]
            self.current_basename = basename
            print(f"[mock] Configured as config {self.current_config_key}: "
                  f"{size} {fmt} → {basename}"
                  f"{'  +raw' if self.is_raw_config else ''}")
        else:
            print(f"⚠  Could not infer config from size={size}, format={fmt}")
    
    def _infer_config_key(self, size, fmt):
        """
        Infer which config (1-6) based on size and format.
        
        Args:
            size: (width, height) tuple
            fmt: format string like "RGB888" or "SBGGR10_CSI2P"
        
        Returns:
            config key "1"-"6" or None if not recognized
        """
        if fmt == "RGB888":
            if size == (640, 480):
                return "1"
            elif size == (1640, 1232):
                return "2"
            elif size == (3280, 2464):
                return "3"
        elif fmt in ("SBGGR10", "SBGGR10_CSI2P"):    # IMX219 BGGR formats
            if size == (640, 480):
                return "4"
            elif size == (1640, 1232):
                return "5"
            elif size == (3280, 2464):
                return "6"
        elif fmt in ("SRGGB10", "SRGGB10_CSI2P"):    # IMX708 RGGB formats
            if size == (2304, 1296):
                return "5"
            elif size == (4608, 2592):
                return "6"
        return None
    
    def set_controls(self, controls):
        """
        Set camera controls (e.g., exposure, gain, white balance).
        
        Args:
            controls: dict of control parameters (stored but not enforced)
        """
        self.controls.update(controls)
        print(f"[mock] set_controls: {controls}")

    def _current_metadata(self):
        """
        Metadata for a capture: the .txt file values with any values set via
        set_controls() overlaid.  The real pipeline echoes applied controls
        (ExposureTime, AnalogueGain, …) back in capture metadata; without
        this overlay a requested exposure never appears applied and
        settle loops run to exhaustion.
        """
        meta = dict(self.metadata)
        meta.update(self.controls)
        return meta
    
    def start(self):
        """Start the camera (no-op in mock, but tracks state)."""
        self.started = True
        print("[mock] Camera started")
    
    def stop(self):
        """Stop the camera."""
        self.started = False
        print("[mock] Camera stopped")
    
    def close(self):
        """Close and release camera resources."""
        self.started = False
        print("[mock] Camera closed")
    
    def _load_images_and_metadata(self):
        """
        Load image data and metadata from MOCKIMAGEFILE.

        Uses self.mock_stem (MOCKIMAGEFILE with any extension stripped) to
        locate files.  Two layouts are supported:

        Stem layout (e.g. MOCKIMAGEFILE = ".../t11_White_960ms_under"):
            <stem>.jpg   → RGB main stream
            <stem>.npy   → Bayer raw stream (uint16, 10-bit; optional)
            <stem>.txt   → metadata (optional)

        JPEG-only layout (e.g. MOCKIMAGEFILE = ".../cfg3_rgb888_02-f135.jpg"):
            <stem>.jpg   → RGB main stream
            No .npy present; a minimal synthetic Bayer array is generated
            so dual-stream callers don't crash.

        Populates self.main_array, self.raw_array, self.metadata.

        The files never change during a run, so they are read from disk
        once and cached; every later capture reuses the same arrays.
        """
        if self.main_array is not None:
            return

        stem = self.mock_stem
        jpg_file  = stem.with_suffix(".jpg")
        npy_file  = stem.with_suffix(".npy")
        txt_file  = stem.with_suffix(".txt")

        if not jpg_file.exists():
            raise FileNotFoundError(f"Mock image not found: {jpg_file}")

        self.main_array = self._load_jpg(jpg_file)
        print(f"[mock] Loaded RGB: {jpg_file.name} "
              f"→ shape {self.main_array.shape}, dtype {self.main_array.dtype}")

        if npy_file.exists():
            self.raw_array = np.load(str(npy_file))
            print(f"[mock] Loaded Bayer: {npy_file.name} "
                  f"→ shape {self.raw_array.shape}, dtype {self.raw_array.dtype}")
        else:
            # Synthesize a minimal Bayer array from the RGB image so that
            # dual-stream callers receive valid data even with a JPEG-only mock.
            h, w = self.main_array.shape[:2]
            self.raw_array = np.zeros((h, w), dtype=np.uint16)
            print(f"[mock] No .npy found — synthesized zero Bayer array "
                  f"({h}×{w})")

        self.metadata = self._load_metadata(txt_file) if txt_file.exists() else {}
        if not txt_file.exists():
            print(f"[mock] No .txt found — using empty metadata")
    
    def _load_jpg(self, fpath):
        """
        Load JPG image using OpenCV (or PIL fallback).
        
        Args:
            fpath: Path object to .jpg file
        
        Returns:
            numpy array (uint8, H×W×3, BGR format from cv2)
        """
        try:
            import cv2
            img = cv2.imread(str(fpath))
            if img is None:
                raise FileNotFoundError(f"cv2 could not load: {fpath}")
            return img
        except ImportError:
            # Fallback to PIL
            from PIL import Image
            img = Image.open(fpath)
            return np.array(img)
    
    def _load_metadata(self, fpath):
        """
        Parse metadata from text file (key: value format, one per line).
        
        Args:
            fpath: Path object to metadata .txt file
        
        Returns:
            dict of metadata
        """
        metadata = {}
        try:
            with open(fpath, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if ':' in line:
                        key, val = line.split(':', 1)
                        key = key.strip()
                        val = val.strip()
                        # Try to parse as number
                        try:
                            if '.' in val:
                                metadata[key] = float(val)
                            else:
                                metadata[key] = int(val)
                        except ValueError:
                            metadata[key] = val
        except FileNotFoundError:
            print(f"⚠  Metadata file not found: {fpath}")
        
        return metadata
    
    def capture_request(self):
        """
        Capture a request with main and/or raw stream data.
        
        Returns:
            MockRequest object with main_array, raw_array, metadata
        """
        if not self.started:
            raise RuntimeError("Camera not started; call start() first")
        
        self._load_images_and_metadata()
        
        request = MockRequest(
            main_array=self.main_array,
            raw_array=self.raw_array if self.is_raw_config else None,
            metadata=self._current_metadata()
        )
        return request
    
    def capture_array(self, stream="main"):
        """
        Capture and return a single stream as array (convenience method).
        
        Args:
            stream: "main" (default) or "raw"
        
        Returns:
            numpy array
        """
        if not self.started:
            raise RuntimeError("Camera not started; call start() first")
        
        self._load_images_and_metadata()
        
        if stream == "main":
            return self.main_array
        elif stream == "raw":
            if not self.is_raw_config:
                raise ValueError("Raw stream not available in single-stream config")
            return self.raw_array
        else:
            raise ValueError(f"Unknown stream: {stream}")
    
    def capture_metadata(self):
        """
        Capture and return metadata only (for exposure checking).
        
        Returns:
            dict of metadata
        """
        if not self.started:
            raise RuntimeError("Camera not started; call start() first")

        self._load_images_and_metadata()
        return self._current_metadata()

