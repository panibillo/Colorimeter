#!/usr/bin/env python3
"""
ColorCam - Dual-stream color camera capture and processing.

Provides interface to capture RGB + Bayer raw data from the camera,
with support for configuration 6 (full resolution dual-stream).

Methods included:
  - setup_camera(): Initialize and configure camera for dual-stream capture
  - capture_frame(): Capture a single frame (RGB + raw Bayer)
  - extract_bayer_layers(): Decompose BGGR Bayer pattern into 4 color layers

License: SPDX-MIT
Copyright: William C Olsen
"""

import time
from pathlib import Path
from typing import Dict, Tuple

import numpy as np

import error_handler as err
import logging_manager as log

try:
    from picamera2 import Picamera2
    import libcamera
except ImportError:
    # Desktop testing with mock camera
    from mock_Picamera2 import Picamera2, libcamera


class ColorCam:
    """
    Dual-stream camera interface for RGB + Bayer raw capture.
    
    Configuration 6: Full resolution (3280×2464)
      - Main stream: RGB888 for display and processing
      - Raw stream: SBGGR10 (10-bit Bayer) for color analysis
    
    Attributes:
        camera: Picamera2 instance (None if not initialized)
        config_size: Tuple (width, height) of capture resolution
        exposure_us: Current exposure time in microseconds
        analog_gain: Current analog gain factor
    """
    
    # Configuration for full-resolution dual-stream capture (Config 6)
    CONFIG_6 = {
        "size":    (3280, 2464),
        "format":  "SBGGR10",
        "comment": "Full resolution dual-stream RGB + Bayer",
    }
    
    # Default control settings
    DEFAULT_EXPOSURE_US = 100_000
    DEFAULT_ANALOG_GAIN = 1.0
    DEFAULT_WB_GAINS = (1.0, 1.0)  # (Red, Blue)
    
    def __init__(self):
        """Initialize ColorCam with camera object (not yet configured)."""
        self.camera = None
        self.config_size = None
        self.exposure_us = self.DEFAULT_EXPOSURE_US
        self.analog_gain = self.DEFAULT_ANALOG_GAIN
    
    def setup_camera(self) -> None:
        """
        Initialize and configure camera for dual-stream capture (Config 6).
        
        Creates Picamera2 instance, configures RGB main stream + Bayer raw stream,
        applies control settings, and starts the camera. Camera must be started
        before capture_frame() can be called.
        
        Side effects:
            - Creates and starts camera object (self.camera)
            - Sets self.config_size to (3280, 2464)
            - Logs initialization steps
        
        Example:
            >>> cam = ColorCam()
            >>> cam.setup_camera()
            >>> rgb, bayer, meta = cam.capture_frame()
        """
        try:
            log.info("Initializing camera (Config 6: 3280×2464 dual-stream)")
            self.camera = Picamera2()
            
            cfg = self.CONFIG_6
            size = cfg["size"]
            
            # Configure dual-stream: RGB main + Bayer raw
            log.debug(f"Configuring dual-stream: {size[0]}×{size[1]} RGB888 + SBGGR10")
            config = self.camera.create_still_configuration(
                main={
                    "size":   size,
                    "format": "RGB888",
                },
                raw={
                    "size":   size,
                    "format": cfg["format"],
                },
                buffer_count=1,
            )
            self.camera.configure(config)
            
            # Apply control settings
            log.debug(f"Applying camera controls: exposure={self.exposure_us}µs, gain={self.analog_gain}")
            self.camera.set_controls({
                "AeEnable":       False,
                "ExposureTime":   self.exposure_us,
                "AnalogueGain":   self.analog_gain,
                "AwbEnable":      False,
                "ColourGains":    self.DEFAULT_WB_GAINS,
                "NoiseReductionMode": libcamera.controls.draft.NoiseReductionModeEnum.Off,
                "Sharpness":      0.0,
                "Contrast":       1.0,
                "Saturation":     1.0,
            })
            
            self.camera.start()
            self.config_size = size
            time.sleep(1.0)  # Allow camera to stabilize
            log.info("Camera ready for capture")
            
        except Exception as e:
            err.handle(e, "Camera initialization", recoverable=False)
            raise
    
    def capture_frame(self) -> Tuple[np.ndarray, np.ndarray, Dict]:
        """
        Capture a single frame with RGB and Bayer raw streams.
        
        Requires setup_camera() to have been called first.
        
        Returns:
            Tuple of:
                - rgb: numpy array, shape (H, W, 3), dtype uint8, BGR format
                - bayer: numpy array, shape (H, W), dtype uint16, SBGGR10 pattern
                - metadata: dict with capture metadata (e.g., exposure, gain)
        
        Example:
            >>> cam = ColorCam()
            >>> cam.setup_camera()
            >>> rgb, bayer, meta = cam.capture_frame()
            >>> rgb.shape
            (2464, 3280, 3)
            >>> bayer.shape
            (2464, 3280)
            >>> bayer.dtype
            dtype('uint16')
        """
        if self.camera is None:
            err.handle(
                RuntimeError("Camera not initialized"),
                "Attempted capture without calling setup_camera()",
                recoverable=True
            )
            raise RuntimeError("Camera not initialized. Call setup_camera() first.")
        
        try:
            log.debug("Capturing frame (RGB + Bayer)...")
            request = self.camera.capture_request()
            rgb = request.make_array("main")  # uint8 BGR (H×W×3)
            bayer = request.make_array("raw")  # uint16 SBGGR10 (H×W)
            metadata = request.get_metadata()
            request.release()
            
            log.debug(f"Frame captured: RGB {rgb.shape} {rgb.dtype}, Bayer {bayer.shape} {bayer.dtype}")
            return rgb, bayer, metadata
        
        except Exception as e:
            err.handle(e, "Frame capture", recoverable=True)
            raise
    
    def set_exposure(self, exposure_us: int) -> None:
        """
        Set camera exposure time and apply to camera.
        
        Args:
            exposure_us: Exposure time in microseconds
        
        Side effects:
            - Updates self.exposure_us
            - Applies setting to camera if initialized
            - Logs the change
        """
        self.exposure_us = exposure_us
        log.debug(f"Setting exposure to {exposure_us} µs")
        if self.camera is not None:
            try:
                self.camera.set_controls({"ExposureTime": exposure_us})
            except Exception as e:
                err.handle(e, f"Setting exposure to {exposure_us}µs", recoverable=True)
    
    def set_analog_gain(self, gain: float) -> None:
        """
        Set camera analog gain and apply to camera.
        
        Args:
            gain: Analog gain multiplier (typically 1.0-16.0)
        
        Side effects:
            - Updates self.analog_gain
            - Applies setting to camera if initialized
            - Logs the change
        """
        self.analog_gain = gain
        log.debug(f"Setting analog gain to {gain}")
        if self.camera is not None:
            try:
                self.camera.set_controls({"AnalogueGain": gain})
            except Exception as e:
                err.handle(e, f"Setting analog gain to {gain}", recoverable=True)
    
    def stop(self) -> None:
        """
        Stop and close camera, releasing resources.
        
        Safe to call multiple times or if camera not initialized.
        Logs cleanup activity.
        """
        if self.camera is not None:
            log.debug("Stopping camera...")
            try:
                self.camera.stop()
                self.camera.close()
                self.camera = None
                self.config_size = None
                log.info("Camera stopped and closed")
            except Exception as e:
                err.handle(e, "Camera cleanup", recoverable=True)
    
    def __del__(self):
        """Ensure camera is stopped when object is destroyed."""
        self.stop()
    
    @staticmethod
    def extract_bayer_layers(bayer_array: np.ndarray) -> Dict[str, np.ndarray]:
        """
        Extract the four color layers from BGGR Bayer pattern.
        
        Bayer mosaic layout in a (H, W) array:
            - even rows, even cols   → B  (Blue)
            - even rows, odd cols    → Gb (Green in blue row)
            - odd rows, even cols    → Gr (Green in red row)
            - odd rows, odd cols     → R  (Red)
        
        Args:
            bayer_array: numpy array, shape (H, W), dtype uint16
                         Raw Bayer sensor data, BGGR pattern
        
        Returns:
            dict with keys {'B', 'Gb', 'Gr', 'R'}, each containing
            the extracted color layer as (H//2, W//2) array
        
        Example:
            >>> bayer = np.random.randint(0, 1024, (480, 640), dtype=np.uint16)
            >>> layers = ColorCam.extract_bayer_layers(bayer)
            >>> layers['B'].shape
            (240, 320)
            >>> layers['R'].shape
            (240, 320)
        """
        layers = {
            'B':  bayer_array[0::2, 0::2],    # Blue: even rows, even cols
            'Gb': bayer_array[0::2, 1::2],    # Green (blue row): even rows, odd cols
            'Gr': bayer_array[1::2, 0::2],    # Green (red row): odd rows, even cols
            'R':  bayer_array[1::2, 1::2],    # Red: odd rows, odd cols
        }
        return layers


if __name__ == "__main__":
    # Simple test: initialize and verify camera interface
    log.info("Testing ColorCam initialization...")
    try:
        cam = ColorCam()
        log.info(f"✓ ColorCam instance created")
        log.debug(f"  Config 6 size: {cam.CONFIG_6['size']}")
        log.debug(f"  Default exposure: {cam.DEFAULT_EXPOSURE_US} µs")
        log.debug(f"  Default gain: {cam.DEFAULT_ANALOG_GAIN}")
    except Exception as e:
        err.handle(e, "ColorCam test", recoverable=False)
