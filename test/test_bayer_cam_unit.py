#!/usr/bin/env python3
"""
Unit tests for BayerCam (src/bayer_cam.py).

All tests run without a physical camera.  Camera-dependent methods are
tested by injecting a MagicMock for self.camera.  Static methods are
tested with synthetic numpy arrays.

Run from the project root:
    python test/test_bayer_cam_unit.py
or with unittest discovery:
    python -m unittest test.test_bayer_cam_unit
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bayer_cam import BayerCam


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestInit(unittest.TestCase):
    """BayerCam construction and default values."""

    def test_camera_none(self):
        cam = BayerCam()
        self.assertIsNone(cam.camera)

    def test_config_size_none(self):
        cam = BayerCam()
        self.assertIsNone(cam.config_size)

    def test_defaults(self):
        cam = BayerCam()
        self.assertEqual(cam.exposure_us, BayerCam.DEFAULT_EXPOSURE_US)
        self.assertEqual(cam.analog_gain, BayerCam.DEFAULT_ANALOG_GAIN)

    def test_default_config_is_config5(self):
        cam = BayerCam()
        self.assertEqual(cam.config, BayerCam.CONFIG_5)
        self.assertEqual(cam.config["size"], (1640, 1232))


# ---------------------------------------------------------------------------
# unpack_raw10
# ---------------------------------------------------------------------------

class TestUnpackRaw10(unittest.TestCase):
    """BayerCam.unpack_raw10() with synthetic packed byte arrays."""

    def _pack(self, pixels_uint16: np.ndarray) -> np.ndarray:
        """View a uint16 array as uint8 (simulates camera raw bytes)."""
        return pixels_uint16.view(np.uint8)

    def test_dtype_is_uint16(self):
        pixels = np.array([[0, 512, 1023]], dtype=np.uint16)
        result = BayerCam.unpack_raw10(self._pack(pixels), 3)
        self.assertEqual(result.dtype, np.uint16)

    def test_shape_matches_pixel_width(self):
        pixels = np.zeros((4, 8), dtype=np.uint16)
        result = BayerCam.unpack_raw10(self._pack(pixels), 8)
        self.assertEqual(result.shape, (4, 8))

    def test_values_round_trip(self):
        pixels = np.array([[0, 100, 512, 1023],
                           [1, 200, 700,   50]], dtype=np.uint16)
        result = BayerCam.unpack_raw10(self._pack(pixels), 4)
        np.testing.assert_array_equal(result, pixels)

    def test_padding_trimmed(self):
        # Padded row: 6 real pixels + 2 padding pixels
        row = np.array([[10, 20, 30, 40, 50, 60, 999, 999]], dtype=np.uint16)
        result = BayerCam.unpack_raw10(self._pack(row), 6)
        self.assertEqual(result.shape, (1, 6))
        np.testing.assert_array_equal(result, row[:, :6])

    def test_multi_row_padding_trimmed(self):
        pixels = np.arange(20, dtype=np.uint16).reshape(4, 5)
        padded = np.hstack([pixels,
                            np.full((4, 3), 9999, dtype=np.uint16)])
        result = BayerCam.unpack_raw10(self._pack(padded), 5)
        np.testing.assert_array_equal(result, pixels)

    def test_zero_values(self):
        pixels = np.zeros((2, 4), dtype=np.uint16)
        result = BayerCam.unpack_raw10(self._pack(pixels), 4)
        self.assertTrue((result == 0).all())

    def test_max_10bit_values(self):
        pixels = np.full((2, 4), 1023, dtype=np.uint16)
        result = BayerCam.unpack_raw10(self._pack(pixels), 4)
        self.assertTrue((result == 1023).all())


# ---------------------------------------------------------------------------
# extract_bayer_layers
# ---------------------------------------------------------------------------

class TestExtractBayerLayers(unittest.TestCase):
    """BayerCam.extract_bayer_layers() with synthetic BGGR arrays."""

    def _make_bayer(self, h, w, b=100, gb=200, gr=300, r=400):
        """Create a synthetic BGGR array with distinct constant per channel."""
        arr = np.zeros((h, w), dtype=np.uint16)
        arr[0::2, 0::2] = b
        arr[0::2, 1::2] = gb
        arr[1::2, 0::2] = gr
        arr[1::2, 1::2] = r
        return arr

    def test_keys(self):
        layers = BayerCam.extract_bayer_layers(self._make_bayer(4, 4))
        self.assertEqual(set(layers.keys()), {'B', 'Gb', 'Gr', 'R'})

    def test_shapes(self):
        layers = BayerCam.extract_bayer_layers(self._make_bayer(8, 12))
        for name, arr in layers.items():
            with self.subTest(layer=name):
                self.assertEqual(arr.shape, (4, 6))

    def test_channel_values(self):
        layers = BayerCam.extract_bayer_layers(self._make_bayer(4, 4))
        self.assertTrue((layers['B']  == 100).all())
        self.assertTrue((layers['Gb'] == 200).all())
        self.assertTrue((layers['Gr'] == 300).all())
        self.assertTrue((layers['R']  == 400).all())

    def test_pixel_positions(self):
        # Verify one pixel from each channel by position in the full array
        arr = np.zeros((4, 4), dtype=np.uint16)
        arr[0, 0] = 1   # B  [even row, even col]
        arr[0, 1] = 2   # Gb [even row, odd col]
        arr[1, 0] = 3   # Gr [odd row,  even col]
        arr[1, 1] = 4   # R  [odd row,  odd col]
        layers = BayerCam.extract_bayer_layers(arr)
        self.assertEqual(layers['B'][0, 0],  1)
        self.assertEqual(layers['Gb'][0, 0], 2)
        self.assertEqual(layers['Gr'][0, 0], 3)
        self.assertEqual(layers['R'][0, 0],  4)

    def test_returns_views_not_copies(self):
        arr = self._make_bayer(4, 4)
        layers = BayerCam.extract_bayer_layers(arr)
        # Mutating the source array should update the layer (views share memory)
        arr[0, 0] = 999
        self.assertEqual(layers['B'][0, 0], 999)


# ---------------------------------------------------------------------------
# set_exposure
# ---------------------------------------------------------------------------

class TestSetExposure(unittest.TestCase):

    def test_updates_attribute_without_camera(self):
        cam = BayerCam()
        cam.set_exposure(75_000)
        self.assertEqual(cam.exposure_us, 75_000)

    def test_no_error_without_camera(self):
        cam = BayerCam()
        cam.set_exposure(200_000)  # must not raise

    def test_calls_set_controls_when_camera_present(self):
        cam = BayerCam()
        cam.camera = MagicMock()
        cam.set_exposure(50_000)
        cam.camera.set_controls.assert_called_once_with({"ExposureTime": 50_000})

    def test_attribute_updated_even_with_camera(self):
        cam = BayerCam()
        cam.camera = MagicMock()
        cam.set_exposure(123_456)
        self.assertEqual(cam.exposure_us, 123_456)


# ---------------------------------------------------------------------------
# set_analog_gain
# ---------------------------------------------------------------------------

class TestSetAnalogGain(unittest.TestCase):

    def test_updates_attribute_without_camera(self):
        cam = BayerCam()
        cam.set_analog_gain(2.0)
        self.assertEqual(cam.analog_gain, 2.0)

    def test_no_error_without_camera(self):
        cam = BayerCam()
        cam.set_analog_gain(4.0)  # must not raise

    def test_calls_set_controls_when_camera_present(self):
        cam = BayerCam()
        cam.camera = MagicMock()
        cam.set_analog_gain(3.5)
        cam.camera.set_controls.assert_called_once_with({"AnalogueGain": 3.5})

    def test_attribute_updated_even_with_camera(self):
        cam = BayerCam()
        cam.camera = MagicMock()
        cam.set_analog_gain(1.5)
        self.assertEqual(cam.analog_gain, 1.5)


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------

class TestStop(unittest.TestCase):

    def test_no_error_when_camera_none(self):
        cam = BayerCam()
        cam.stop()  # must not raise

    def test_safe_to_call_twice(self):
        cam = BayerCam()
        cam.stop()
        cam.stop()  # must not raise

    def test_calls_stop_and_close(self):
        cam = BayerCam()
        mock_cam = MagicMock()
        cam.camera = mock_cam
        cam.config_size = (1640, 1232)
        cam.stop()
        mock_cam.stop.assert_called_once()
        mock_cam.close.assert_called_once()

    def test_camera_set_to_none_after_stop(self):
        cam = BayerCam()
        cam.camera = MagicMock()
        cam.config_size = (1640, 1232)
        cam.stop()
        self.assertIsNone(cam.camera)
        self.assertIsNone(cam.config_size)


# ---------------------------------------------------------------------------
# capture_frame
# ---------------------------------------------------------------------------

class TestCaptureFrame(unittest.TestCase):

    def test_raises_when_camera_none(self):
        cam = BayerCam()
        with self.assertRaises(RuntimeError):
            cam.capture_frame()

    def _make_mock_camera(self, pixel_w=6, pixel_h=2):
        """Return (cam, rgb_arr, pixels_uint16) with an injected mock camera."""
        cam = BayerCam()
        cam.config_size = (pixel_w, pixel_h)

        rgb_arr = np.zeros((pixel_h, pixel_w, 3), dtype=np.uint8)
        pixels = np.arange(pixel_h * pixel_w,
                           dtype=np.uint16).reshape(pixel_h, pixel_w)
        raw_bytes = pixels.view(np.uint8)
        meta = {"ExposureTime": 50_000, "AnalogueGain": 1.0}

        request = MagicMock()
        request.make_array.side_effect = (
            lambda s: rgb_arr if s == "main" else raw_bytes
        )
        request.get_metadata.return_value = meta

        cam.camera = MagicMock()
        cam.camera.capture_request.return_value = request

        return cam, rgb_arr, pixels, meta

    def test_returns_three_values(self):
        cam, _, _, _ = self._make_mock_camera()
        result = cam.capture_frame()
        self.assertEqual(len(result), 3)

    def test_rgb_shape_and_dtype(self):
        cam, rgb_arr, _, _ = self._make_mock_camera(pixel_w=6, pixel_h=2)
        rgb, _, _ = cam.capture_frame()
        self.assertEqual(rgb.shape, (2, 6, 3))
        self.assertEqual(rgb.dtype, np.uint8)

    def test_bayer_shape_and_dtype(self):
        cam, _, _, _ = self._make_mock_camera(pixel_w=6, pixel_h=2)
        _, bayer, _ = cam.capture_frame()
        self.assertEqual(bayer.shape, (2, 6))
        self.assertEqual(bayer.dtype, np.uint16)

    def test_bayer_values_match_unpacked_pixels(self):
        cam, _, pixels, _ = self._make_mock_camera(pixel_w=6, pixel_h=2)
        _, bayer, _ = cam.capture_frame()
        np.testing.assert_array_equal(bayer, pixels)

    def test_metadata_returned(self):
        cam, _, _, meta = self._make_mock_camera()
        _, _, returned_meta = cam.capture_frame()
        self.assertEqual(returned_meta, meta)

    def test_request_released(self):
        cam, _, _, _ = self._make_mock_camera()
        cam.capture_frame()
        request = cam.camera.capture_request.return_value
        request.release.assert_called_once()


# ---------------------------------------------------------------------------
# save_frame
# ---------------------------------------------------------------------------

class TestSaveFrame(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self._tmpdir.name)
        self.rgb   = np.zeros((4, 6, 3), dtype=np.uint8)
        self.bayer = np.arange(24, dtype=np.uint16).reshape(4, 6)
        self.meta  = {"ExposureTime": 50_000, "AnalogueGain": 1.0}

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_jpg_written(self):
        BayerCam.save_frame(self.out_dir, "test", self.rgb, self.bayer, self.meta)
        self.assertTrue((self.out_dir / "test.jpg").exists())

    def test_npy_written(self):
        BayerCam.save_frame(self.out_dir, "test", self.rgb, self.bayer, self.meta)
        self.assertTrue((self.out_dir / "test.npy").exists())

    def test_txt_written(self):
        BayerCam.save_frame(self.out_dir, "test", self.rgb, self.bayer, self.meta)
        self.assertTrue((self.out_dir / "test.txt").exists())

    def test_jpeg_only_skips_npy(self):
        BayerCam.save_frame(self.out_dir, "test", self.rgb, self.bayer, self.meta,
                            jpeg_only=True)
        self.assertTrue((self.out_dir / "test.jpg").exists())
        self.assertFalse((self.out_dir / "test.npy").exists())

    def test_returns_paths_dict(self):
        paths = BayerCam.save_frame(self.out_dir, "test", self.rgb, self.bayer, self.meta)
        self.assertIn("jpg", paths)
        self.assertIn("npy", paths)
        self.assertIn("txt", paths)

    def test_returns_paths_dict_jpeg_only(self):
        paths = BayerCam.save_frame(self.out_dir, "test", self.rgb, self.bayer, self.meta,
                                    jpeg_only=True)
        self.assertIn("jpg", paths)
        self.assertNotIn("npy", paths)
        self.assertIn("txt", paths)

    def test_txt_contains_metadata_keys(self):
        BayerCam.save_frame(self.out_dir, "test", self.rgb, self.bayer, self.meta)
        txt = (self.out_dir / "test.txt").read_text()
        for key in ("image_width", "image_height", "bayer_min", "bayer_max",
                    "bayer_mean", "picamera2_metadata", "ExposureTime"):
            self.assertIn(key, txt)

    def test_txt_bayer_stats_correct(self):
        BayerCam.save_frame(self.out_dir, "test", self.rgb, self.bayer, self.meta)
        txt = (self.out_dir / "test.txt").read_text()
        self.assertIn(f"bayer_min: {int(self.bayer.min())}", txt)
        self.assertIn(f"bayer_max: {int(self.bayer.max())}", txt)

    def test_npy_round_trips(self):
        BayerCam.save_frame(self.out_dir, "test", self.rgb, self.bayer, self.meta)
        loaded = np.load(str(self.out_dir / "test.npy"))
        np.testing.assert_array_equal(loaded, self.bayer)

    def test_accepts_string_out_dir(self):
        BayerCam.save_frame(str(self.out_dir), "test", self.rgb, self.bayer, self.meta)
        self.assertTrue((self.out_dir / "test.jpg").exists())


# ---------------------------------------------------------------------------
# load_bayer
# ---------------------------------------------------------------------------

class TestLoadBayer(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_loads_saved_array(self):
        arr = np.arange(24, dtype=np.uint16).reshape(4, 6)
        npy_path = self.out_dir / "frame.npy"
        np.save(str(npy_path), arr)
        loaded = BayerCam.load_bayer(npy_path)
        np.testing.assert_array_equal(loaded, arr)

    def test_dtype_is_uint16(self):
        arr = np.zeros((4, 6), dtype=np.uint16)
        npy_path = self.out_dir / "frame.npy"
        np.save(str(npy_path), arr)
        loaded = BayerCam.load_bayer(npy_path)
        self.assertEqual(loaded.dtype, np.uint16)

    def test_accepts_string_path(self):
        arr = np.zeros((4, 6), dtype=np.uint16)
        npy_path = self.out_dir / "frame.npy"
        np.save(str(npy_path), arr)
        loaded = BayerCam.load_bayer(str(npy_path))
        self.assertEqual(loaded.dtype, np.uint16)

    def test_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            BayerCam.load_bayer(self.out_dir / "missing.npy")

    def test_raises_on_wrong_dtype(self):
        arr = np.zeros((4, 6), dtype=np.uint8)
        npy_path = self.out_dir / "bad.npy"
        np.save(str(npy_path), arr)
        with self.assertRaises(ValueError):
            BayerCam.load_bayer(npy_path)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
