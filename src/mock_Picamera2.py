"""
mock_Picamera2: Mock library emulating Picamera2 for desktop testing

Loads test images and metadata from ../mock_camera directory.
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
        from mock_Picamera2 import Picamera2
"""

import os
import numpy as np
from pathlib import Path
from types import SimpleNamespace as sns

# Mock libcamera enums
libcamera = sns(controls=sns(draft=sns(NoiseReductionModeEnum=sns(Off=0))))


def _get_mock_camera_dir():
    """Find the mock_camera directory relative to this file."""
    this_file = Path(__file__)
    mock_dir = this_file.parent.parent / "mock_camera"
    if not mock_dir.exists():
        print(f"⚠  Mock camera directory not found: {mock_dir}")
    return mock_dir


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
        self.mock_dir = _get_mock_camera_dir()
    
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
        
        if self.current_config_key:
            basename, is_raw = self.CONFIG_MAP[self.current_config_key]
            self.current_basename = basename
            self.is_raw_config = is_raw
            print(f"[mock] Configured as config {self.current_config_key}: "
                  f"{size} {fmt} → {basename}")
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
        elif fmt == "SBGGR10_CSI2P":
            if size == (640, 480):
                return "4"
            elif size == (1640, 1232):
                return "5"
            elif size == (3280, 2464):
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
        Load image data and metadata from mock_camera directory.
        Populates self.main_array, self.raw_array, self.metadata.
        """
        if not self.current_basename:
            raise RuntimeError("No configuration set; call configure() first")
        
        if not self.mock_dir.exists():
            raise FileNotFoundError(f"Mock camera directory not found: {self.mock_dir}")
        
        try:
            if self.is_raw_config:
                # Dual-stream: load RGB and Bayer
                rgb_file = self.mock_dir / f"{self.current_basename}_rgb_00.jpg"
                raw_file = self.mock_dir / f"{self.current_basename}_raw_00.npy"
                meta_file = self.mock_dir / f"{self.current_basename}_raw_00.txt"
                
                self.main_array = self._load_jpg(rgb_file)
                self.raw_array = np.load(raw_file)
                self.metadata = self._load_metadata(meta_file)
                
                print(f"[mock] Loaded dual-stream:")
                print(f"  RGB: {rgb_file.name} → shape {self.main_array.shape}, dtype {self.main_array.dtype}")
                print(f"  Raw: {raw_file.name} → shape {self.raw_array.shape}, dtype {self.raw_array.dtype}")
            else:
                # Single-stream: load RGB only
                rgb_file = self.mock_dir / f"{self.current_basename}_00.jpg"
                meta_file = self.mock_dir / f"{self.current_basename}_00.txt"
                
                self.main_array = self._load_jpg(rgb_file)
                self.metadata = self._load_metadata(meta_file)
                
                print(f"[mock] Loaded single-stream:")
                print(f"  RGB: {rgb_file.name} → shape {self.main_array.shape}, dtype {self.main_array.dtype}")
        
        except FileNotFoundError as e:
            print(f"⚠  File not found: {e}")
            raise
    
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
            metadata=self.metadata
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
        return self.metadata

