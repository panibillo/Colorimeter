"""
scientific_colorimeter_capture.py

Captures a single, maximally objective image from the Raspberry Pi
Camera Module 2 (IMX219) for colorimetric analysis.

Key design decisions:
  - Dual-stream capture: processed RGB (for grid analysis) + raw Bayer
    (for archival / offline recalibration)
  - All ISP "creative" processing disabled or neutralised
  - Fixed, validated exposure in the sensor's linear region
  - Output as 16-bit PNG (lossless, preserves full tonal depth)
  - White-balance gains must be calibrated against your white reference
    sample BEFORE production use (see calibrate_wb() below)
"""

import time
import numpy as np
from pathlib import Path
try:
    from picamera2 import Picamera2
    import libcamera                        # for typed enum constants
    import cv2                              # for saving 16-bit PNG
except:
    print ("Loading sim_picamera2")
    from sim_picamera2 import Picamera2
    from sim_picamera2 import libcamera      
    from sim_picamera2 import cv2                             


# ---------------------------------------------------------------------------
# Sensor constants — IMX219 native full resolution
# ---------------------------------------------------------------------------
SENSOR_WIDTH  = 3280
SENSOR_HEIGHT = 2464   # corrected: native is 2464, not 2264

# ---------------------------------------------------------------------------
# Calibrated white-balance gains.
# These MUST be measured against your actual illuminant + white reference.
# Procedure: capture your white card, read R/G/B means, set
#   WB_GAINS_RB = (G_mean / R_mean,  G_mean / B_mean)
# Start with (1.0, 1.0) for your first calibration run.
# ---------------------------------------------------------------------------
WB_GAINS_RB = (1.0, 1.0)   # ← REPLACE after calibration

# ---------------------------------------------------------------------------
# Exposure.  Tune so that the brightest sample (white reference) sits at
# ~70 % of full scale (i.e. ~178 out of 255 in 8-bit preview, or ~45900
# out of 65535 in 16-bit). This keeps you well inside the linear region.
# ---------------------------------------------------------------------------
EXPOSURE_TIME_US = 10_000   # microseconds — validate with check_exposure()
ANALOGUE_GAIN    = 1.0      # keep at 1.0 to minimise read-noise amplification


def build_camera() -> Picamera2:
    """
    Instantiate and fully configure the camera.
    Returns a *started* Picamera2 instance.
    """
    picam2 = Picamera2()

    # ------------------------------------------------------------------
    # Dual-stream configuration
    #   main   → demosaiced, ISP-output image used for grid analysis
    #   raw    → native Bayer data (RGGB, 10-bit packed) for archival
    #
    # Why keep the ISP stream at all?  The colour-matrix and white-point
    # transforms built into the ISP are well-characterised for the IMX219.
    # With AWB/AE off and a calibrated colour matrix you get a reliable
    # linear-ish RGB that is easier to work with than raw Bayer.
    # Keep the raw stream as your ground truth.
    # ------------------------------------------------------------------
    config = picam2.create_still_configuration(
        main={
            "size":   (SENSOR_WIDTH, SENSOR_HEIGHT),
            "format": "BGR888",          # OpenCV-native; we will promote to 16-bit
        },
        raw={
            "size":   (SENSOR_WIDTH, SENSOR_HEIGHT),
            "format": "SBGGR10_CSI2P",   # native 10-bit packed Bayer
        },
        buffer_count=1,
    )
    picam2.configure(config)

    # ------------------------------------------------------------------
    # Lock down every ISP parameter that could introduce inter-image
    # variability.  Explicit values beat implicit defaults.
    # ------------------------------------------------------------------
    picam2.set_controls({
        # --- Exposure / gain ---
        "AeEnable":       False,
        "ExposureTime":   EXPOSURE_TIME_US,
        "AnalogueGain":   ANALOGUE_GAIN,

        # --- White balance ---
        # AwbEnable=False alone is NOT sufficient on some libcamera builds;
        # also pin ColourGains explicitly.
        "AwbEnable":      False,
        "ColourGains":    WB_GAINS_RB,   # (R_gain, B_gain) relative to G

        # --- Noise / sharpening / tone ---
        # Use typed enums where available to avoid silent wrong-integer bugs.
        "NoiseReductionMode": libcamera.controls.draft.NoiseReductionModeEnum.Off,
        "Sharpness":      0.0,
        "Contrast":       1.0,           # 1.0 = unity (no S-curve added)
        "Saturation":     1.0,           # 1.0 = unity

        # NOTE: ColourCorrectionMatrix is NOT a runtime control on the
        # vc4/IMX219 pipeline — it lives in the libcamera tuning file at
        # /usr/share/libcamera/ipa/rpi/vc4/imx219.json and is applied
        # statically.  With AwbEnable=False this is exactly what we want:
        # a fixed, repeatable colour transform on every capture.
    })

    picam2.start()

    # Give the sensor, ISP, and illuminant time to reach thermal steady-state.
    # 2 s is a reasonable minimum; increase to 5 s if your LED illuminant
    # has a noticeable warm-up curve.
    time.sleep(2)
    return picam2


def check_exposure(bgr_image: np.ndarray, clip_fraction: float = 0.001) -> dict:
    """
    Sanity-check the exposure level.

    Returns a dict with per-channel mean/max and a warning if any channel
    is clipped or under-exposed.
    """
    stats = {}
    channels = {"B": bgr_image[:, :, 0],
                "G": bgr_image[:, :, 1],
                "R": bgr_image[:, :, 2]}
    for name, ch in channels.items():
        clipped = np.mean(ch >= 254)
        stats[name] = {
            "mean":    float(ch.mean()),
            "max":     int(ch.max()),
            "clipped": float(clipped),
        }
        if clipped > clip_fraction:
            print(f"  ⚠  Channel {name}: {clipped*100:.2f}% pixels clipped — "
                  f"reduce ExposureTime")
        elif ch.mean() < 30:
            print(f"  ⚠  Channel {name}: mean={ch.mean():.1f} — "
                  f"image may be under-exposed, increase ExposureTime")
        else:
            print(f"  ✓  Channel {name}: mean={ch.mean():.1f}, "
                  f"max={ch.max()}, clipped={clipped*100:.3f}%")
    return stats


def capture(picam2: Picamera2, output_dir: str = ".") -> dict:
    """
    Capture one frame, save outputs, return file paths.

    Outputs
    -------
    <output_dir>/colorimeter_rgb.png   — lossless 16-bit RGB (promoted from 8-bit)
    <output_dir>/colorimeter_raw.npy   — raw Bayer array (uint16, native 10-bit values)
    <output_dir>/colorimeter_meta.txt  — camera metadata at moment of capture
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Capture both streams + metadata atomically
    request = picam2.capture_request()
    bgr8   = request.make_array("main")      # uint8  H×W×3  BGR
    bayer  = request.make_array("raw")       # uint16 H×W    packed 10-bit Bayer
    meta   = request.get_metadata()
    request.release()

    # ------------------------------------------------------------------
    # Promote 8-bit → 16-bit.
    # This does NOT recover lost precision, but it future-proofs the file
    # and is consistent with downstream 16-bit processing pipelines.
    # If you need true 16-bit depth, decode from the Bayer array instead.
    # ------------------------------------------------------------------
    bgr16 = bgr8.astype(np.uint16) * 257    # 255→65535, linear scale

    rgb_path  = out / "colorimeter_rgb.png"
    raw_path  = out / "colorimeter_raw.npy"
    meta_path = out / "colorimeter_meta.txt"

    # Save 16-bit PNG (lossless; readable by OpenCV, ImageJ, GIMP)
    cv2.imwrite(str(rgb_path), bgr16)

    # Save raw Bayer as NumPy array (compact, lossless, easy to reload)
    np.save(str(raw_path), bayer)

    # Save metadata (actual exposure, gain, colour temps, etc.)
    with open(meta_path, "w") as f:
        for k, v in meta.items():
            f.write(f"{k}: {v}\n")

    print(f"\nCapture complete:")
    print(f"  RGB  → {rgb_path}  ({bgr16.shape}, uint16)")
    print(f"  Bayer→ {raw_path}  ({bayer.shape}, uint16)")
    print(f"  Meta → {meta_path}")
    print(f"\nActual camera parameters at capture time:")
    for key in ("ExposureTime", "AnalogueGain", "ColourGains",
                "Lux", "ColourTemperature"):
        print(f"  {key}: {meta.get(key, 'n/a')}")

    return {"rgb": rgb_path, "raw": raw_path, "meta": meta_path,
            "bgr8": bgr8, "bgr16": bgr16, "bayer": bayer}


def calibrate_wb(picam2: Picamera2):
    """
    ONE-TIME white-balance calibration helper.

    Point the camera at your WHITE reference sample alone (fill the frame),
    run this function, and it will print the WB_GAINS_RB values to paste
    into this file.
    """
    print("WB calibration: capturing white reference frame...")
    bgr = picam2.capture_array()
    b_mean = bgr[:, :, 0].mean()
    g_mean = bgr[:, :, 1].mean()
    r_mean = bgr[:, :, 2].mean()
    r_gain = g_mean / r_mean
    b_gain = g_mean / b_mean
    print(f"\n  B_mean={b_mean:.1f}  G_mean={g_mean:.1f}  R_mean={r_mean:.1f}")
    print(f"\n  Set WB_GAINS_RB = ({r_gain:.4f}, {b_gain:.4f})")
    return r_gain, b_gain


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    picam2 = build_camera()
    try:
        result = capture(picam2, output_dir="./colorimeter_output")
        print("\nExposure check on captured image:")
        check_exposure(result["bgr8"])
    finally:
        picam2.stop()
        picam2.close()