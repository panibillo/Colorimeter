'''
Created on Aug 5, 2026

@author: bill
'''



#python3 - <<'PY'
import ast
from pathlib import Path
ROOT = Path("..").resolve(); SRC, DEV = ROOT/"src", ROOT/"dev"
def imports(p):
    out = set()
    for n in ast.walk(ast.parse(p.read_text())):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            out.add(n.module.split(".")[0])
    return out
def find(name):
    for b in (SRC, DEV):
        if (b/f"{name}.py").exists(): return b/f"{name}.py"
        if (b/name/"__init__.py").exists(): return b/name/"__init__.py"
seen, queue = set(), [SRC/"main.py", DEV/"camtest8.py"]
while queue:
    p = queue.pop().resolve()
    if p in seen: continue
    seen.add(p)
    queue += [f for n in imports(p) if (f := find(n)) and f.resolve() not in seen]
for f in sorted(p.relative_to(ROOT).as_posix() for p in seen): print(f)
#PY
"""
        dev/camtest8.py
        src/DS18B20.py
        src/arbiter.py
        src/bayer_cam.py
        src/beeper.py
        src/capture_h5.py
        src/capture_meta.py
        src/capture_profile.py
        src/capture_runner.py
        src/capture_session.py
        src/config_repo.py
        src/db_bootstrap.py
        src/db_manager.py
        src/error_handler.py
        src/exposure_repo.py
        src/geometry.py
        src/hdf5write.py
        src/ili9341.py
        src/image_view.py
        src/kb_reader.py
        src/layout.py
        src/lighting_cal8.py
        src/logging_manager.py
        src/main.py
        src/mock/__init__.py
        src/palette.py
        src/phases.py
        src/pool.py
        src/rect.py
        src/roi.py
        src/sample_repo.py
        src/shutdown_screen.py
        src/slot.py
        src/timer.py
        src/tsc2007.py
        src/utils.py
        src/version.py
        src/widgets.py
"""
"""
HDF5 file: captures/ZP1012.h5
  attrs: {'bayer_pattern': 'BGGR', 'bound_h': np.int64(260), 'bound_w': np.int64(366), 'bound_x': np.int64(992), 'bound_y': np.int64(588), 'description': 'Colorimeter camtest8 capture suite', 'frame_h': np.int64(1296), 'frame_w': np.int64(2304), 'hardware_sn': 'XCN-001', 'meta_version': np.int64(2), 'samp_no': 'ZP1012', 'schema_version': np.int64(2), 'sensor_model': 'imx708', 'software_ver': '0.3.0', 'suite_id': '20260806_2211'}
  dark/
    dark/after_11888us/
      dark/after_11888us/B  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:34.796', 'exposure_us': np.int64(11888)}
      dark/after_11888us/Gb  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:34.796', 'exposure_us': np.int64(11888)}
      dark/after_11888us/Gr  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:34.796', 'exposure_us': np.int64(11888)}
      dark/after_11888us/R  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:34.796', 'exposure_us': np.int64(11888)}
    dark/after_17808us/
      dark/after_17808us/B  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:35.313', 'exposure_us': np.int64(17808)}
      dark/after_17808us/Gb  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:35.313', 'exposure_us': np.int64(17808)}
      dark/after_17808us/Gr  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:35.313', 'exposure_us': np.int64(17808)}
      dark/after_17808us/R  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:35.313', 'exposure_us': np.int64(17808)}
    dark/before_11888us/
      dark/before_11888us/B  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:11.432', 'exposure_us': np.int64(11888)}
      dark/before_11888us/Gb  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:11.432', 'exposure_us': np.int64(11888)}
      dark/before_11888us/Gr  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:11.432', 'exposure_us': np.int64(11888)}
      dark/before_11888us/R  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:11.432', 'exposure_us': np.int64(11888)}
    dark/before_17808us/
      dark/before_17808us/B  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:11.949', 'exposure_us': np.int64(17808)}
      dark/before_17808us/Gb  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:11.949', 'exposure_us': np.int64(17808)}
      dark/before_17808us/Gr  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:11.949', 'exposure_us': np.int64(17808)}
      dark/before_17808us/R  (shape=(130, 183) dtype=uint16)
        attrs: {'captured_dt': '2026-08-06 22:11:11.949', 'exposure_us': np.int64(17808)}
  leds/
    leds/violet/
      attrs: {'led_channel': np.int64(1), 'led_color': 'violet'}
      leds/violet/capture_info  (shape=() dtype=object)
          {
            "image_stats": {
              "bayer_dtype": "uint16",
              "bayer_max": 1023,
              "bayer_mean": 76.67,
              "bayer_min": 62,
              "bayer_shape": "1296x2304",
              "bgr_mean_B": 35.088,
              "bgr_mean_G": 0.833,
              "bgr_mean_R": 0.018,
              "bgr_std_B": 44.917,
              "bgr_std_G": 10.005,
              "bgr_std_R": 0.303,
              "image_height": 1296,
              "image_width": 2304,
              "lens_position": 7.4475
            },
            "picamera2_metadata": {
              "AeState": 0,
              "AfPauseState": 0,
              "AfState": 0,
              "AnalogueGain": 1.1228070259094238,
              "ColourCorrectionMatrix": [
                1.5398586988449097,
                -0.37055307626724243,
                -0.16930311918258667,
                -0.28007498383522034,
                1.651968240737915,
                -0.37188586592674255,
                0.004583357833325863,
                -0.5279855728149414,
                1.5234122276306152
              ],
              "ColourGains": [
                1.0,
                1.0
              ],
              "ColourTemperature": 4965,
              "ControlListSequence": 327,
              "DigitalGain": 1.0006521940231323,
              "ExposureTime": 17769,
              "FocusFoM": 954,
              "FrameDuration": 18437,
              "FrameWallClock": 1786072288990307328,
              "LensPosition": 7.44746732711792,
              "Lux": 20.704423904418945,
              "ScalerCrop": [
                0,
                0,
                4608,
                2592
              ],
              "SensorBlackLevels": [
                4096,
                4096,
                4096,
                4096
              ],
              "SensorTemperature": 72.0,
              "SensorTimestamp": 15195224777000
            }
          }
      leds/violet/dark/
      leds/violet/jpeg_e3  (217493 bytes)
        attrs: {'filename': 'camtest8_violet_e0.jpg', 'format': 'jpeg'}
      leds/violet/led_metadata  (shape=() dtype=object)
          {
            "applied_exposures_us": {
              "e0": 17769
            },
            "autofocus_raw_diopters": 7.44746732711792,
            "bayer_pattern": "BGGR",
            "camera_model": "Camera Module 3 (IMX708)",
            "config_size": [
              2304,
              1296
            ],
            "exposure_group": "1",
            "exposure_source": "exposure_table",
            "exposures_us": {
              "e0": 17808
            },
            "focus_diopters": 7.44746732711792,
            "focus_mode": "autofocus",
            "led_channel": 1,
            "led_color": "violet",
            "led_label": "ch 1 (violet, 410 nm)",
            "led_off_dt": "2026-08-06 22:11:29.304",
            "led_on_dt": "2026-08-06 22:11:26.960",
            "mode": "normal",
            "timestamp": "20260806_2211"
          }
      leds/violet/lit/
        leds/violet/lit/e0/
          leds/violet/lit/e0/B  (shape=(130, 183) dtype=uint16)
            attrs: {'captured_dt': '2026-08-06 22:11:29.231', 'exposure_us': np.int64(17808)}
          leds/violet/lit/e0/Gb  (shape=(130, 183) dtype=uint16)
            attrs: {'captured_dt': '2026-08-06 22:11:29.231', 'exposure_us': np.int64(17808)}
          leds/violet/lit/e0/Gr  (shape=(130, 183) dtype=uint16)
            attrs: {'captured_dt': '2026-08-06 22:11:29.231', 'exposure_us': np.int64(17808)}
          leds/violet/lit/e0/R  (shape=(130, 183) dtype=uint16)
            attrs: {'captured_dt': '2026-08-06 22:11:29.231', 'exposure_us': np.int64(17808)}
    leds/white/
      attrs: {'led_channel': np.int64(10), 'led_color': 'white'}
      leds/white/capture_info  (shape=() dtype=object)
          {
            "image_stats": {
              "bayer_dtype": "uint16",
              "bayer_max": 1023,
              "bayer_mean": 80.559,
              "bayer_min": 62,
              "bayer_shape": "1296x2304",
              "bgr_mean_B": 2.999,
              "bgr_mean_G": 18.108,
              "bgr_mean_R": 8.937,
              "bgr_std_B": 12.543,
              "bgr_std_G": 43.541,
              "bgr_std_R": 29.045,
              "image_height": 1296,
              "image_width": 2304,
              "lens_position": 7.4475
            },
            "picamera2_metadata": {
              "AeState": 0,
              "AfPauseState": 0,
              "AfState": 0,
              "AnalogueGain": 1.1228070259094238,
              "ColourCorrectionMatrix": [
                1.5398586988449097,
                -0.37055307626724243,
                -0.16930311918258667,
                -0.28007498383522034,
                1.651968240737915,
                -0.37188586592674255,
                0.004583357833325863,
                -0.5279855728149414,
                1.5234122276306152
              ],
              "ColourGains": [
                1.0,
                1.0
              ],
              "ColourTemperature": 4965,
              "ControlListSequence": 407,
              "DigitalGain": 1.0008736848831177,
              "ExposureTime": 11864,
              "FocusFoM": 3754,
              "FrameDuration": 17849,
              "FrameWallClock": 1786072293465971456,
              "LensPosition": 7.44746732711792,
              "Lux": 65.45181274414062,
              "ScalerCrop": [
                0,
                0,
                4608,
                2592
              ],
              "SensorBlackLevels": [
                4096,
                4096,
                4096,
                4096
              ],
              "SensorTemperature": 72.0,
              "SensorTimestamp": 15199700441000
            }
          }
      leds/white/dark/
      leds/white/jpeg_e3  (547178 bytes)
        attrs: {'filename': 'camtest8_white_e0.jpg', 'format': 'jpeg'}
      leds/white/led_metadata  (shape=() dtype=object)
          {
            "applied_exposures_us": {
              "e0": 11864
            },
            "autofocus_raw_diopters": 7.44746732711792,
            "bayer_pattern": "BGGR",
            "camera_model": "Camera Module 3 (IMX708)",
            "config_size": [
              2304,
              1296
            ],
            "exposure_group": "1",
            "exposure_source": "exposure_table",
            "exposures_us": {
              "e0": 11888
            },
            "focus_diopters": 7.44746732711792,
            "focus_mode": "autofocus",
            "led_channel": 10,
            "led_color": "white",
            "led_label": "ch 10 (white, broadband)",
            "led_off_dt": "2026-08-06 22:11:33.831",
            "led_on_dt": "2026-08-06 22:11:31.179",
            "mode": "normal",
            "timestamp": "20260806_2211"
          }
      leds/white/lit/
        leds/white/lit/e0/
          leds/white/lit/e0/B  (shape=(130, 183) dtype=uint16)
            attrs: {'captured_dt': '2026-08-06 22:11:33.733', 'exposure_us': np.int64(11888)}
          leds/white/lit/e0/Gb  (shape=(130, 183) dtype=uint16)
            attrs: {'captured_dt': '2026-08-06 22:11:33.733', 'exposure_us': np.int64(11888)}
          leds/white/lit/e0/Gr  (shape=(130, 183) dtype=uint16)
            attrs: {'captured_dt': '2026-08-06 22:11:33.733', 'exposure_us': np.int64(11888)}
          leds/white/lit/e0/R  (shape=(130, 183) dtype=uint16)
            attrs: {'captured_dt': '2026-08-06 22:11:33.733', 'exposure_us': np.int64(11888)}
  meta  (shape=() dtype=object)
      {
        "camera": {
          "bayer_pattern": "BGGR",
          "config_size": [
            2304,
            1296
          ],
          "has_autofocus": true,
          "lens_position": 7.44746732711792,
          "max_exposure_us": 112000000,
          "model": "imx708",
          "model_description": "Camera Module 3 (IMX708)",
          "stream_format": "SBGGR10_CSI2P",
          "stream_h": 1296,
          "stream_w": 2304
        },
        "config": {
          "capture": {
            "camera_warmup_s": "5.0",
            "dark_prefetch_budget_s": "20.0",
            "default_focus_diopter": "7.44746732711792",
            "directory": "captures",
            "extension": ".h5",
            "inspection_exposure_us": "10000",
            "led_color_order": "violet,white",
            "timestamp_format": "%Y%m%d_%H%M"
          },
          "defaults": {
            "exposuregroup": "3",
            "samp_no_template": "ZP1007",
            "testkit": "Dummy test strip",
            "testkit_vendor": "Industrial Test Systems, Inc.",
            "testlot": "",
            "testlot_expire_dt": "2027-07",
            "user": "William Olsen",
            "user_email": "panibillo@gmail.com",
            "user_org": "MGWWG"
          },
          "device": {
            "description": "dev8 lighting, prototype",
            "hardware_sn": "XCN-001",
            "software_ver": "0.3.0"
          },
          "hardware": {
            "backlight_pin": "13"
          },
          "protocol": {
            "analysis_secs": "1.5",
            "reagent1_secs": "1",
            "reagent2_secs": "11"
          },
          "runtime": {
            "main_loop_poll_s": "0.02",
            "shutdown_timeout_s": "15.0"
          }
        },
        "device": {
          "description": "dev8 lighting, prototype",
          "hardware_sn": "XCN-001",
          "software_ver": "0.3.0"
        },
        "meta_version": 2,
        "roi": {
          "bound": {
            "h": 260,
            "role": "bound",
            "w": 366,
            "x": 992,
            "y": 588
          },
          "frame_h": 1296,
          "frame_w": 2304,
          "model": "imx708",
          "rects": {
            "bound": {
              "h": 260,
              "role": "bound",
              "w": 366,
              "x": 992,
              "y": 588
            },
            "chip1": {
              "h": 68,
              "role": "gray1",
              "w": 68,
              "x": 1012,
              "y": 640
            },
            "chip2": {
              "h": 68,
              "role": "gray2",
              "w": 68,
              "x": 1280,
              "y": 638
            },
            "sample": {
              "h": 68,
              "role": "sample",
              "w": 68,
              "x": 1136,
              "y": 640
            },
            "white": {
              "h": 68,
              "role": "white",
              "w": 66,
              "x": 1140,
              "y": 772
            }
          }
        },
        "run": {
          "exposure_group": "1",
          "exposure_ms": 10,
          "focus_diopter": null,
          "led_colors": [
            "violet",
            "white"
          ],
          "mode": "normal"
        },
        "sample": {
          "capture_end": 1786072295,
          "capture_start": 1786072286,
          "collect_dt": "2026-08-06 22:10:43",
          "h5file": "/home/bill/pi/Colorimeter/captures/ZP1012.h5",
          "instrument_sn": "XCN-001",
          "manual_read": "",
          "num_result": null,
          "reagent1_end": 1786072267,
          "reagent1_start": 1786072265,
          "reagent2_end": 1786072280,
          "reagent2_start": 1786072268,
          "remark": null,
          "rowid": 17,
          "samp_degC": null,
          "samp_grams": null,
          "samp_no": "ZP1012",
          "test_kit": "Dummy test strip",
          "test_lot": "",
          "txt_result": "Analysis not yet implemented",
          "user": "William Olsen"
        },
        "written_dt": "2026-08-06 22:11:10"
"""