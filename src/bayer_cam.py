#!/usr/bin/env python3
"""
BayerCam - Dual-stream camera interface: configuration, capture, and file I/O.

Supports Raspberry Pi Camera Module 2 (IMX219) and Module 3 (IMX708).
The attached camera model is detected automatically at init time via
Picamera2.global_camera_info(), and CONFIG_5/CONFIG_6 sizes, maximum
exposure, Bayer pattern, and autofocus availability are set accordingly.

Methods:
  - setup_camera()       Configure and start dual-stream RGB + Bayer capture
  - capture_frame()      Capture one frame; returns (rgb, bayer, metadata)
  - autofocus_once()     Trigger AF sweep, lock focus, return LensPosition (IMX708 only)
  - set_exposure()       Set exposure time
  - set_analog_gain()    Set analogue gain
  - stop()               Stop and release camera
  - detect_model()       Query Picamera2 for the attached camera model string
  - unpack_raw10()       Unpack CSI-2 packed bytes → uint16 pixel array
  - extract_bayer_layers() Split Bayer mosaic into {B, Gb, Gr, R} sub-arrays
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


# ---------------------------------------------------------------------------
# Per-model sensor profiles
# ---------------------------------------------------------------------------

# Known camera models and their sensor-specific parameters.
# Model strings match the lowercase value returned by Picamera2.global_camera_info().
_CAMERA_PROFILES = {
    "imx219": {                         # Raspberry Pi Camera Module 2
        "full_size":       (3280, 2464),
        "half_size":       (1640, 1232),
        "bayer_format":    "SBGGR10",   # 10-bit unpacked, BGGR pattern
        "bayer_pattern":   "BGGR",
        "max_exposure_us": 1_200_000,   # hardware cap ~1,238,765 µs; margin left
        "has_autofocus":   False,
        "description":     "Camera Module 2 (IMX219)",
    },
    "imx708": {                         # Raspberry Pi Camera Module 3
        "full_size":       (4608, 2592),
        "half_size":       (2304, 1296),
        "bayer_format":    "SRGGB10_CSI2P",  # 10-bit packed CSI-2, RGGB pattern
        "bayer_pattern":   "RGGB",
        "max_exposure_us": 112_000_000, # 112 seconds maximum
        "has_autofocus":   True,
        "description":     "Camera Module 3 (IMX708)",
    },
}
_FALLBACK_PROFILE = "imx219"


class BayerCam:
    """
    Dual-stream camera interface for RGB + Bayer raw capture.

    Supports Camera Module 2 (IMX219) and Module 3 (IMX708).  The attached
    model is detected automatically; CONFIG_5 (half-resolution) and CONFIG_6
    (full-resolution) sizes are set accordingly.

    IMX219: CONFIG_5 = 1640×1232,  CONFIG_6 = 3280×2464,  BGGR pattern
    IMX708: CONFIG_5 = 2304×1296,  CONFIG_6 = 4608×2592,  RGGB pattern

    Attributes:
        camera:            Picamera2 instance (None if not initialized)
        model:             Detected camera model string, e.g. "imx219"
        model_description: Human-readable model label
        has_autofocus:     True for IMX708 (Module 3); False for IMX219
        bayer_pattern:     "BGGR" or "RGGB" — pass to extract_bayer_layers()
        MAX_EXPOSURE_US:   Hardware exposure ceiling for the detected sensor
        CONFIG_5:          Half-resolution config dict
        CONFIG_6:          Full-resolution config dict
        config:            Active config dict (default CONFIG_5)
        config_size:       (width, height) of the active config
        exposure_us:       Current exposure time in microseconds
        analog_gain:       Current analog gain factor
        lens_position:     Current lens position in diopters (float), or None.
                           Updated by autofocus_once() and by capture_frame()
                           on IMX708.  Always None on IMX219.
    """

    # Default control settings (model-independent)
    DEFAULT_EXPOSURE_US = 100_000
    DEFAULT_ANALOG_GAIN = 1.0
    DEFAULT_WB_GAINS = (1.0, 1.0)  # (Red, Blue)

    @staticmethod
    def detect_model() -> str:
        """
        Query Picamera2 for the attached camera model string.

        Calls Picamera2.global_camera_info() (a class method that does not
        require the camera to be opened) and returns the lowercase model
        name from the first camera listed.

        Returns:
            Lowercase model string, e.g. "imx219" or "imx708".
            Falls back to _FALLBACK_PROFILE ("imx219") if the call fails
            or returns no cameras.
        """
        try:
            fn = getattr(Picamera2, "global_camera_info", None)
            if fn is not None:
                info = fn()
                if info:
                    model = info[0].get("Model", "").lower()
                    if model:
                        return model
        except Exception:
            pass
        return _FALLBACK_PROFILE

    def __init__(self):
        """
        Initialize BayerCam: detect the attached camera model and set
        model-specific parameters (resolutions, max exposure, Bayer pattern).
        Camera is not opened or configured here; call setup_camera() next.
        """
        self.camera        = None
        self.config_size   = None
        self.exposure_us   = self.DEFAULT_EXPOSURE_US
        self.analog_gain   = self.DEFAULT_ANALOG_GAIN
        self.lens_position = None   # diopters; set by autofocus_once() / capture_frame()

        # Detect camera model and apply profile
        model   = BayerCam.detect_model()
        profile = _CAMERA_PROFILES.get(model, _CAMERA_PROFILES[_FALLBACK_PROFILE])

        self.model             = model
        self.model_description = profile["description"]
        self.has_autofocus     = profile["has_autofocus"]
        self.bayer_pattern     = profile["bayer_pattern"]
        self.MAX_EXPOSURE_US   = profile["max_exposure_us"]

        fmt = profile["bayer_format"]
        self.CONFIG_5 = {
            "size":    profile["half_size"],
            "format":  fmt,
            "comment": (f"Half resolution dual-stream RGB + Bayer "
                        f"({profile['half_size'][0]}×{profile['half_size'][1]})"),
        }
        self.CONFIG_6 = {
            "size":    profile["full_size"],
            "format":  fmt,
            "comment": (f"Full resolution dual-stream RGB + Bayer "
                        f"({profile['full_size'][0]}×{profile['full_size'][1]})"),
        }
        self.config = self.CONFIG_5   # Default to half-resolution
    
    def setup_camera(self) -> None:
        """
        Initialize and configure camera for dual-stream capture.

        Creates Picamera2 instance, configures RGB main stream + Bayer raw stream,
        applies control settings, and starts the camera.  On Camera Module 3
        (IMX708), autofocus is explicitly set to Manual so it does not interfere
        with captures.  Camera must be started before capture_frame() can be called.

        Side effects:
            - Creates and starts camera object (self.camera)
            - Sets self.config_size to the active config size
            - Logs initialization steps

        Example:
            >>> cam = BayerCam()
            >>> cam.setup_camera()
            >>> rgb, bayer, meta = cam.capture_frame()
        """
        try:
            size = self.config["size"]
            log.info(f"Initializing {self.model_description} "
                     f"({size[0]}×{size[1]} dual-stream, "
                     f"pattern={self.bayer_pattern})")
            self.camera = Picamera2()

            # Configure dual-stream: RGB main + Bayer raw
            log.debug(f"Configuring dual-stream: {size[0]}×{size[1]} RGB888 "
                      f"+ {self.config['format']}")
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
            log.debug(f"Applying camera controls: exposure={self.exposure_us}µs, "
                      f"gain={self.analog_gain}")
            controls = {
                "AeEnable":       False,
                "ExposureTime":   self.exposure_us,
                "AnalogueGain":   self.analog_gain,
                "AwbEnable":      False,
                "ColourGains":    self.DEFAULT_WB_GAINS,
                "NoiseReductionMode": libcamera.controls.draft.NoiseReductionModeEnum.Off,
                "Sharpness":      0.0,
                "Contrast":       1.0,
                "Saturation":     1.0,
            }
            if self.has_autofocus:
                # Disable autofocus on Module 3 — fixed focus needed for colorimetry
                controls["AfMode"] = libcamera.controls.AfModeEnum.Manual
            self.camera.set_controls(controls)
            
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

            # Keep lens_position in sync with what the hardware reports.
            if self.has_autofocus:
                lp = metadata.get("LensPosition")
                if lp is not None:
                    self.lens_position = float(lp)

            log.debug(f"Frame captured: RGB {rgb.shape} {rgb.dtype}, "
                      f"Bayer {bayer.shape} {bayer.dtype} max={bayer.max()}")
            return rgb, bayer, metadata
        
        except Exception as e:
            err.handle(e, "Frame capture", recoverable=True)
            raise
    
    def autofocus_once(self, timeout_s: float = 5.0) -> float:
        """
        Trigger a single autofocus sweep, wait for completion, then lock focus.

        Switches the IMX708 to Auto AfMode, starts a focus sweep, polls
        AfState until scanning completes or timeout_s elapses, then returns
        to Manual mode locked at the settled lens position.

        Only valid on cameras with has_autofocus == True (IMX708).
        Camera must be started (setup_camera() called) before calling this.

        Args:
            timeout_s: Maximum seconds to wait for AF to settle (default 5.0).

        Returns:
            LensPosition at which focus was locked (diopters; 0.0 = infinity).

        Raises:
            RuntimeError: If camera is not initialized.
            RuntimeError: If camera does not support autofocus.

        Example:
            >>> cam = BayerCam()
            >>> cam.setup_camera()
            >>> pos = cam.autofocus_once()
            >>> print(f"Focused at lens position {pos:.2f}")
        """
        if self.camera is None:
            raise RuntimeError("Camera not initialized. Call setup_camera() first.")
        if not self.has_autofocus:
            raise RuntimeError(
                f"Camera {self.model_description} does not support autofocus."
            )

        log.info("Starting autofocus sweep...")
        self.camera.set_controls({
            "AfMode":    libcamera.controls.AfModeEnum.Auto,
            "AfTrigger": libcamera.controls.AfTriggerEnum.Start,
        })

        deadline = time.monotonic() + timeout_s
        meta = {}
        while time.monotonic() < deadline:
            time.sleep(0.1)
            meta = self.camera.capture_metadata()
            af_state = meta.get("AfState", 1)
            if af_state != 1:   # 1 = Scanning; 0 = Idle, 2 = Focused, 3 = Failed
                break

        af_state   = meta.get("AfState", -1)
        lens_pos   = meta.get("LensPosition", 1.0)
        af_outcome = {0: "idle", 2: "focused", 3: "failed"}.get(af_state, f"state={af_state}")
        log.info(f"Autofocus complete: {af_outcome}, LensPosition={lens_pos:.3f}")

        # Lock focus at the settled position
        self.camera.set_controls({
            "AfMode":       libcamera.controls.AfModeEnum.Manual,
            "LensPosition": lens_pos,
        })
        self.lens_position = float(lens_pos)
        return self.lens_position

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
    def extract_bayer_layers(
        bayer_array: np.ndarray,
        pattern: str = "BGGR",
    ) -> Dict[str, np.ndarray]:
        """
        Extract the four color layers from a Bayer mosaic array.

        Supports BGGR (Camera Module 2, IMX219) and RGGB (Camera Module 3,
        IMX708).  Pass cam.bayer_pattern when calling on a BayerCam instance
        so the correct layout is used automatically.

        BGGR layout (IMX219):
            - even rows, even cols → B   (Blue)
            - even rows, odd cols  → Gb  (Green in blue row)
            - odd rows,  even cols → Gr  (Green in red row)
            - odd rows,  odd cols  → R   (Red)

        RGGB layout (IMX708):
            - even rows, even cols → R   (Red)
            - even rows, odd cols  → Gr  (Green in red row)
            - odd rows,  even cols → Gb  (Green in blue row)
            - odd rows,  odd cols  → B   (Blue)

        Args:
            bayer_array: numpy array, shape (H, W), dtype uint16
            pattern:     "BGGR" (default) or "RGGB"

        Returns:
            dict with keys {'B', 'Gb', 'Gr', 'R'}, each a (H//2, W//2) array

        Raises:
            ValueError: If pattern is not "BGGR" or "RGGB"

        Example:
            >>> bayer = np.random.randint(0, 1024, (480, 640), dtype=np.uint16)
            >>> layers = BayerCam.extract_bayer_layers(bayer, "BGGR")
            >>> layers['B'].shape
            (240, 320)
        """
        if pattern == "BGGR":
            return {
                'B':  bayer_array[0::2, 0::2],
                'Gb': bayer_array[0::2, 1::2],
                'Gr': bayer_array[1::2, 0::2],
                'R':  bayer_array[1::2, 1::2],
            }
        elif pattern == "RGGB":
            return {
                'R':  bayer_array[0::2, 0::2],
                'Gr': bayer_array[0::2, 1::2],
                'Gb': bayer_array[1::2, 0::2],
                'B':  bayer_array[1::2, 1::2],
            }
        else:
            raise ValueError(
                f"Unknown Bayer pattern {pattern!r}. Use 'BGGR' or 'RGGB'."
            )

    @staticmethod
    def save_frame(
        out_dir:       Union[str, Path],
        stem:          str,
        rgb:           np.ndarray,
        bayer:         np.ndarray,
        meta:          Dict,
        jpeg_only:     bool = False,
        lens_position: "float | None" = None,
    ) -> Dict[str, Path]:
        """
        Save a captured frame to disk as JPG + NPY + TXT.

        Writes three files into out_dir (which must already exist):
            {stem}.jpg   — RGB image (JPEG, BGR channel order from cv2)
            {stem}.npy   — Bayer array (uint16, BGGR, 10-bit values 0–1023)
            {stem}.txt   — Metadata: image stats + Picamera2 metadata block

        Args:
            out_dir:       Directory to write files into (must already exist)
            stem:          Base filename without extension (e.g. "run1_White_50ms")
            rgb:           uint8 array (H, W, 3), BGR channel order
            bayer:         uint16 array (H, W), BGGR Bayer values
            meta:          Picamera2 metadata dict from capture_frame()
            jpeg_only:     If True, skip the .npy file (default False)
            lens_position: Lens position in diopters (IMX708 only); written to
                           the .txt file when provided (default None)

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
        ]
        if lens_position is not None:
            lines.append(f"lens_position: {lens_position:.4f}")
        lines.append("--- picamera2_metadata ---")
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
