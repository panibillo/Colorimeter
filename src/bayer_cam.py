#!/usr/bin/env python3
"""
BayerCam - Dual-stream camera interface: configuration, capture, and file I/O.

Provides a base class for capturing RGB + raw Bayer data from a Pi Camera v2
(IMX219).  Intended to be used directly or inherited by higher-level classes
that add LED control or auto-exposure.

Methods:
  - setup_camera()       Configure and start dual-stream RGB + Bayer capture
  - capture_frame()      Capture one frame; returns (rgb, bayer, metadata)
  - set_exposure()       Set exposure time
  - set_analog_gain()    Set analogue gain
  - stop()               Stop and release camera
  - unpack_raw10()       Unpack CSI-2 packed bytes → uint16 pixel array
  - extract_bayer_layers() Split BGGR mosaic into {B, Gb, Gr, R} sub-arrays
  - save_frame()         Save RGB JPG + Bayer NPY + metadata TXT to disk
  - load_bayer()         Load a Bayer NPY file, return uint16 array

License: SPDX-MIT
Copyright: William C Olsen
"""

import time
from pathlib import Path
from typing import Dict, Tuple, Union

import numpy as np

import error_handler as err
import logging_manager as log

try:
    from picamera2 import Picamera2
    import libcamera
except ImportError:
    # Desktop testing with mock camera
    from mock_Picamera2 import Picamera2, libcamera


class BayerCam:
    """
    Dual-stream camera interface for RGB + Bayer raw capture.
    
    Supports Config 5 (1640×1232, default) and Config 6 (3280×2464):
      - Main stream: RGB888 for display and processing
      - Raw stream: SBGGR10 (10-bit Bayer) for color analysis
    
    Attributes:
        camera: Picamera2 instance (None if not initialized)
        config_size: Tuple (width, height) of capture resolution
        exposure_us: Current exposure time in microseconds
        analog_gain: Current analog gain factor
    """
    
    # Configuration for half-resolution dual-stream capture (Config 5)
    CONFIG_5 = {
        "size":    (1640, 1232),
        "format":  "SBGGR10",
        "comment": "Half resolution dual-stream RGB + Bayer",
    }
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
        """Initialize BayerCam with camera object (not yet configured)."""
        self.camera = None
        self.config_size = None
        self.exposure_us = self.DEFAULT_EXPOSURE_US
        self.analog_gain = self.DEFAULT_ANALOG_GAIN
        self.config = self.CONFIG_5
    
    def setup_camera(self) -> None:
        """
        Initialize and configure camera for dual-stream capture.
        
        Creates Picamera2 instance, configures RGB main stream + Bayer raw stream,
        applies control settings, and starts the camera. Camera must be started
        before capture_frame() can be called.
        
        Side effects:
            - Creates and starts camera object (self.camera)
            - Sets self.config_size to (3280, 2464)
            - Logs initialization steps
        
        Example:
            >>> cam = BayerCam()
            >>> cam.setup_camera()
            >>> rgb, bayer, meta = cam.capture_frame()
        """
        try:
            log.info(f"Initializing camera ({self.config['size'][0]}×{self.config['size'][1]} dual-stream)")
            self.camera = Picamera2()
            
            # cfg = self.CONFIG_6
            size = self.config["size"]
            
            # Configure dual-stream: RGB main + Bayer raw
            log.debug(f"Configuring dual-stream: {size[0]}×{size[1]} RGB888 + SBGGR10")
            config = self.camera.create_still_configuration(
                main={
                    "size":   size,
                    "format": "RGB888",
                },
                raw={
                    "size":   size,
                    "format": self.config["format"],
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
            >>> cam = BayerCam()
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
            rgb       = request.make_array("main")   # uint8 BGR (H×W×3)
            raw_bytes = request.make_array("raw")    # uint8 packed CSI-2 bytes
            metadata  = request.get_metadata()
            request.release()

            # Unpack CSI-2 bytes → uint16 pixel values (0–1023).
            bayer = BayerCam.unpack_raw10(raw_bytes, self.config_size[0])

            log.debug(f"Frame captured: RGB {rgb.shape} {rgb.dtype}, "
                      f"Bayer {bayer.shape} {bayer.dtype} max={bayer.max()}")
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
        Set camera analogue gain and apply to camera.

        Args:
            gain: Analogue gain factor (1.0 = unity)

        Side effects:
            - Updates self.analog_gain
            - Applies setting to camera if initialized
            - Logs the change
        """
        self.analog_gain = gain
        log.debug(f"Setting analogue gain to {gain}")
        if self.camera is not None:
            try:
                self.camera.set_controls({"AnalogueGain": gain})
            except Exception as e:
                err.handle(e, f"Setting analogue gain to {gain}", recoverable=True)

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
    def unpack_raw10(raw_bytes: np.ndarray, pixel_width: int) -> np.ndarray:
        """
        Unpack a MIPI CSI-2 raw10 byte array into a uint16 pixel array.

        Picamera2's make_array("raw") returns packed bytes for SBGGR10 streams:
        each 10-bit pixel occupies 2 bytes (little-endian uint16), but the row
        width is padded by Unicam to an alignment boundary, producing extra
        columns.  This method reinterprets the bytes as uint16 and trims the
        padding so the result is a true (H, pixel_width) array of 10-bit values
        in the range 0–1023.

        Args:
            raw_bytes:   uint8 array returned by make_array("raw"),
                         shape (H, W_packed).
            pixel_width: True pixel width of the image (e.g. 1640), used to
                         trim padding columns introduced by Unicam alignment.

        Returns:
            uint16 array, shape (H, pixel_width), values 0–1023.

        Example:
            >>> raw_bytes = request.make_array("raw")   # uint8 (1232, 3296)
            >>> bayer = BayerCam.unpack_raw10(raw_bytes, 1640)
            >>> bayer.shape
            (1232, 1640)
            >>> bayer.dtype
            dtype('uint16')
            >>> bayer.max() <= 1023
            True
        """
        return raw_bytes.view(np.uint16)[:, :pixel_width]

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
            >>> layers = BayerCam.extract_bayer_layers(bayer)
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

    @staticmethod
    def save_frame(
        out_dir:   Union[str, Path],
        stem:      str,
        rgb:       np.ndarray,
        bayer:     np.ndarray,
        meta:      Dict,
        jpeg_only: bool = False,
    ) -> Dict[str, Path]:
        """
        Save a captured frame to disk as JPG + NPY + TXT.

        Writes three files into out_dir (which must already exist):
            {stem}.jpg   — RGB image (JPEG, BGR channel order from cv2)
            {stem}.npy   — Bayer array (uint16, BGGR, 10-bit values 0–1023)
            {stem}.txt   — Metadata: image stats + Picamera2 metadata block

        Args:
            out_dir:   Directory to write files into (must already exist)
            stem:      Base filename without extension (e.g. "run1_White_50ms")
            rgb:       uint8 array (H, W, 3), BGR channel order
            bayer:     uint16 array (H, W), BGGR Bayer values
            meta:      Picamera2 metadata dict from capture_frame()
            jpeg_only: If True, skip the .npy file (default False)

        Returns:
            Dict mapping extension to Path for each file written,
            e.g. {"jpg": Path(...), "npy": Path(...), "txt": Path(...)}

        Raises:
            ImportError: If cv2 is not available
            OSError: If any file write fails
        """
        try:
            import cv2
        except ImportError:
            raise ImportError("cv2 is required for save_frame(); install opencv-python")

        out_dir = Path(out_dir)
        paths: Dict[str, Path] = {}

        jpg_path = out_dir / f"{stem}.jpg"
        cv2.imwrite(str(jpg_path), rgb)
        paths["jpg"] = jpg_path
        log.debug(f"Saved RGB:   {jpg_path.name}")

        if not jpeg_only:
            npy_path = out_dir / f"{stem}.npy"
            np.save(str(npy_path), bayer)
            paths["npy"] = npy_path
            log.debug(f"Saved Bayer: {npy_path.name}")

        txt_path = out_dir / f"{stem}.txt"
        lines = [
            f"image_width: {rgb.shape[1]}",
            f"image_height: {rgb.shape[0]}",
            f"bgr_mean_B: {rgb[:, :, 0].mean():.3f}",
            f"bgr_mean_G: {rgb[:, :, 1].mean():.3f}",
            f"bgr_mean_R: {rgb[:, :, 2].mean():.3f}",
            f"bgr_std_B:  {rgb[:, :, 0].std():.3f}",
            f"bgr_std_G:  {rgb[:, :, 1].std():.3f}",
            f"bgr_std_R:  {rgb[:, :, 2].std():.3f}",
            f"bayer_dtype: {bayer.dtype}",
            f"bayer_shape: {bayer.shape[0]}x{bayer.shape[1]}",
            f"bayer_min: {int(bayer.min())}",
            f"bayer_max: {int(bayer.max())}",
            f"bayer_mean: {bayer.mean():.3f}",
            "--- picamera2_metadata ---",
        ]
        for key, val in meta.items():
            lines.append(f"{key}: {val}")
        txt_path.write_text("\n".join(lines) + "\n")
        paths["txt"] = txt_path
        log.debug(f"Saved meta:  {txt_path.name}")

        return paths

    @staticmethod
    def load_bayer(npy_path: Union[str, Path]) -> np.ndarray:
        """
        Load a saved Bayer array from a .npy file.

        Args:
            npy_path: Path to a .npy file written by save_frame()

        Returns:
            uint16 array, shape (H, W), BGGR Bayer values 0–1023

        Raises:
            FileNotFoundError: If npy_path does not exist
            ValueError: If the loaded array is not uint16

        Example:
            >>> bayer = BayerCam.load_bayer("run1_White_50ms.npy")
            >>> bayer.dtype
            dtype('uint16')
        """
        npy_path = Path(npy_path)
        if not npy_path.exists():
            raise FileNotFoundError(f"Bayer file not found: {npy_path}")
        arr = np.load(str(npy_path))
        if arr.dtype != np.uint16:
            raise ValueError(
                f"Expected uint16 Bayer array, got {arr.dtype} in {npy_path.name}"
            )
        log.debug(f"Loaded Bayer: {npy_path.name}  shape={arr.shape} max={arr.max()}")
        return arr
