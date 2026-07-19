#!/usr/bin/env python3
"""
BayerCam - Dual-stream camera interface: configuration, capture, and file I/O.

Supports Raspberry Pi Camera Module 2 (IMX219) and Module 3 (IMX708).
The attached camera model is detected automatically at init time via
Picamera2.global_camera_info(), and CONFIG_5/CONFIG_6 sizes, maximum
exposure, Bayer pattern, and autofocus availability are set accordingly.

Camera details (including spectral response curves!)
https://www.raspberrypi.com/documentation/accessories/camera.html#camera-module-3

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
  - focus()              Restore saved focus or run AF sweep; save result (IMX708)
  - save_focus_cal()     Write current lens_position to focus_cal.json
  - save_frame()         Save RGB JPG + Bayer NPY + metadata TXT to disk
  - load_bayer()         Load a Bayer NPY file, return uint16 array

License: SPDX-MIT
Copyright: William C Olsen
"""

import json
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
    from mock.picamera2_mock import Picamera2, libcamera


_DEFAULT_FOCUS_CAL_PATH = (
    Path.home() / "pi" / "Colorimeter" / "config" / "focus_cal.json"
)


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
        "bayer_format":    "SBGGR10_CSI2P",  # 10-bit packed CSI-2; hardware delivers BGGR
        "bayer_pattern":   "BGGR",
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

    def __init__(
        self,
        focus_cal_path: Union[str, Path] = _DEFAULT_FOCUS_CAL_PATH,
    ):
        """
        Initialize BayerCam: detect the attached camera model and set
        model-specific parameters (resolutions, max exposure, Bayer pattern).
        Camera is not opened or configured here; call setup_camera() next.

        Args:
            focus_cal_path: Path to the focus calibration JSON file.
                            Default: ~/pi/Colorimeter/config/focus_cal.json
        """
        self.camera           = None
        self.config_size      = None
        self.exposure_us      = self.DEFAULT_EXPOSURE_US
        self.analog_gain      = self.DEFAULT_ANALOG_GAIN
        self.lens_position    = None   # diopters; set by focus() / autofocus_once()
        self._focus_cal_path  = Path(focus_cal_path)

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

            log.info(f"raw_bytes [{self.config['format']}]  dtype={raw_bytes.dtype}"  # type: ignore[index]
                     f"  shape={raw_bytes.shape}  min={int(raw_bytes.min())}"
                     f"  mean={raw_bytes.mean():.1f}  max={int(raw_bytes.max())}")

            # Unpack raw bytes → uint16 pixel values (0–1023).
            bayer = BayerCam.unpack_raw10(
                raw_bytes, self.config_size[0], self.config["format"]  # type: ignore[index]
            )

            log.info(f"bayer after unpack  config_size={self.config_size}  dtype={bayer.dtype}"
                     f"  shape={bayer.shape}  min={int(bayer.min())}"
                     f"  mean={bayer.mean():.1f}  max={int(bayer.max())}")

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

    # ── Focus save / restore ──────────────────────────────────────────────────

    def _load_focus_cal(self) -> "float | None":
        """Return saved lens_position from JSON, or None if absent/unreadable."""
        try:
            with open(self._focus_cal_path) as f:
                data = json.load(f)
            return float(data["lens_position"])
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            return None

    def save_focus_cal(self) -> None:
        """
        Write the current lens_position to the focus calibration JSON file.

        Creates the config directory if it does not exist.  Does nothing if
        lens_position is None (no successful focus has been performed yet).
        """
        if self.lens_position is None:
            return
        self._focus_cal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._focus_cal_path, "w") as f:
            json.dump(
                {"lens_position": self.lens_position, "model": self.model},
                f, indent=2,
            )
        log.info(f"Focus cal saved: {self.lens_position:.4f} diopters → "
                 f"{self._focus_cal_path}")

    def focus(self, force: bool = False, leds=None) -> float:
        """
        Focus the camera, using a saved position when available.

        On IMX219 (no autofocus): no-op, returns 0.0.

        On IMX708:
          - If force=False and focus_cal.json exists: restore the saved
            LensPosition instantly (no sweep, no LED needed).
          - If force=True, or no saved file: turn on white LED (ch 10) if
            leds is provided, run autofocus_once(), turn LED off, then
            save the result to focus_cal.json for next time.

        Must be called after setup_camera().

        Args:
            force: If True, always run the full AF sweep and overwrite
                   any saved position.  Default False.
            leds:  Optional object with set_channel(channel, on: bool).
                   When provided, ch 10 (white) is turned on for the AF
                   sweep and off immediately after (guaranteed via
                   try/finally).  Not used when restoring a saved position.
                   No import of lighting_cal8 is required here.

        Returns:
            Lens position in diopters (0.0 on IMX219).

        Example:
            >>> cam = BayerCam()
            >>> cam.setup_camera()
            >>> with LEDBank() as leds:
            ...     pos = cam.focus(leds=leds)
            >>> print(f"Focus: {pos:.4f} diopters")
        """
        print(f"bayer_cam::BayerCam.focus({force}):  Auto?={self.has_autofocus}")
        if not self.has_autofocus:
            return 0.0

        if self.camera is None:
            raise RuntimeError("Camera not initialized. Call setup_camera() first.")

        if not force:
            saved = self._load_focus_cal()
            if saved is not None:
                log.info(f"Focus: restoring saved position "
                         f"{saved:.4f} diopters")
                self.camera.set_controls({
                    "AfMode":       libcamera.controls.AfModeEnum.Manual,
                    "LensPosition": saved,
                })
                self.lens_position = saved
                time.sleep(0.3)   # allow VCM to settle
                return saved

        log.info("Focus: running AF sweep ...")
        if leds is not None:
            leds.set_channel('white', True)
        try:
            pos = self.autofocus_once()
        finally:
            if leds is not None:
                leds.set_channel('white', False)
        self.save_focus_cal()
        return pos

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
    def unpack_raw10(raw_bytes: np.ndarray, pixel_width: int,
                     fmt: str = "SBGGR10") -> np.ndarray:
        """
        Unpack a raw10 byte array from make_array("raw") into uint16 pixels.

        Two sub-formats are handled:

        Unpacked (SBGGR10, SRGGB10, …):
            Each 10-bit pixel is stored as a 16-bit little-endian word (2 bytes).
            Unicam may pad rows to an alignment boundary; pixel_width trims that.
            Values are in the range 0–1023.
            Used by IMX219 (Module 2).

        CSI-2 packed (*_CSI2P):
            4 pixels are packed into 5 bytes.  Bytes 0–3 are the 8 MSBs of
            pixels 0–3; byte 4 packs the 2 LSBs of all four pixels:
                b4[1:0]  = pixel 0 LSBs
                b4[3:2]  = pixel 1 LSBs
                b4[5:4]  = pixel 2 LSBs
                b4[7:6]  = pixel 3 LSBs
            pixel_width must be a multiple of 4.
            Values are in the range 0–1023.
            Used by IMX708 (Module 3, SBGGR10_CSI2P).

        Args:
            raw_bytes:   uint8 array returned by make_array("raw"),
                         shape (H, W_bytes).
            pixel_width: True pixel width of the image, used to trim row padding
                         (unpacked) or to slice the correct packed byte count.
            fmt:         Format string from the camera config, e.g.
                         "SBGGR10" or "SBGGR10_CSI2P".  Default "SBGGR10".

        Returns:
            uint16 array, shape (H, pixel_width), values 0–1023.
        """
        if "CSI2P" in fmt:
            h = raw_bytes.shape[0]
            row_bytes = pixel_width * 5 // 4          # exact packed bytes per row
            b = raw_bytes[:, :row_bytes].reshape(h, pixel_width // 4, 5)
            out = np.empty((h, pixel_width), dtype=np.uint16)
            lsb = b[:, :, 4].astype(np.uint16)
            out[:, 0::4] = (b[:, :, 0].astype(np.uint16) << 2) | (lsb & 0x03)
            out[:, 1::4] = (b[:, :, 1].astype(np.uint16) << 2) | ((lsb >> 2) & 0x03)
            out[:, 2::4] = (b[:, :, 2].astype(np.uint16) << 2) | ((lsb >> 4) & 0x03)
            out[:, 3::4] = (b[:, :, 3].astype(np.uint16) << 2) | ((lsb >> 6) & 0x03)
            return out
        else:
            # Unpacked: each pixel stored as a 16-bit little-endian word
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
    def to_display_image(
        rgb_bgr: np.ndarray,
        w: int = 320,
        h: int = 240,
    ):
        """
        Downsample a full BGR camera frame to a PIL RGB Image sized for ILI9341.

        Converts BGR → RGB, then LANCZOS-resizes to (w, h) so the result can
        be passed directly to ILI9341.draw_image().

        Args:
            rgb_bgr: uint8 array (H, W, 3), BGR channel order from capture_frame()
            w:       target display width in pixels  (default 320)
            h:       target display height in pixels (default 240)

        Returns:
            PIL Image, mode "RGB", size (w, h)

        Example:
            >>> rgb, bayer, meta = cam.capture_frame()
            >>> img = BayerCam.to_display_image(rgb)
            >>> d.draw_image(img, 0, 0)
        """
        from PIL import Image
        pil = Image.fromarray(rgb_bgr[:, :, ::-1])   # BGR → RGB
        return pil.resize((w, h), Image.Resampling.LANCZOS)
        

    @staticmethod
    def to_display_layer(
        layer: np.ndarray,
        w: int = 320,
        h: int = 240,
    ):
        """
        Scale a Bayer channel (10-bit uint16) to a PIL RGB Image for ILI9341.

        Maps the absolute 10-bit ADC range (0–1023) to 8-bit (0–255) by
        dividing by 4 — preserving absolute intensity so different captures
        and different channels can be compared directly.  Does NOT stretch
        min/max, so a dim scene appears dim and a bright scene appears bright.

        Args:
            layer: uint16 array (H//2, W//2) — one channel from extract_bayer_layers()
            w:     target display width  (default 320)
            h:     target display height (default 240)

        Returns:
            PIL Image, mode "RGB", size (w, h)

        Example:
            >>> layers = BayerCam.extract_bayer_layers(bayer, cam.bayer_pattern)
            >>> img = BayerCam.to_display_layer(layers['B'])
            >>> d.draw_image(img, 0, 0)
        """
        from PIL import Image
        log.info(f"to_display_layer: input  dtype={layer.dtype}  shape={layer.shape}"
                 f"  min={int(layer.min())}  mean={layer.mean():.1f}  max={int(layer.max())}")
        scaled = (layer >> 2).clip(0, 255).astype(np.uint8)  # 10-bit → 8-bit, fixed scale
        log.info(f"to_display_layer: scaled  dtype={scaled.dtype}  shape={scaled.shape}"
                 f"  min={int(scaled.min())}  mean={scaled.mean():.1f}  max={int(scaled.max())}")
        rv = Image.fromarray(scaled, mode="L").resize((w, h), Image.Resampling.LANCZOS).convert("RGB")
        return rv

    @staticmethod
    def crop_rgb(rgb_bgr: np.ndarray, rect) -> np.ndarray:
        """
        Extract a rectangular region of interest from a BGR camera frame.

        Args:
            rgb_bgr: uint8 array (H, W, 3), BGR channel order from capture_frame()
            rect:    geometry.Rect with attributes x, y, w, h (pixel coordinates)

        Returns:
            uint8 array (rect.h, rect.w, 3), BGR, clipped to array bounds

        Example:
            >>> from geometry import Rect
            >>> roi = BayerCam.crop_rgb(rgb, Rect(100, 80, 200, 150))
            >>> img = BayerCam.to_display_image(roi)
            >>> d.draw_image(img, 0, 0)
        """
        H, W = rgb_bgr.shape[:2]
        x1 = max(0, rect.x)
        y1 = max(0, rect.y)
        x2 = min(W, rect.x + rect.w)
        y2 = min(H, rect.y + rect.h)
        return rgb_bgr[y1:y2, x1:x2]

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
