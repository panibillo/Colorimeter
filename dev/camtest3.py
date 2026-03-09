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
EXPOSURE_TIME_US = 100_000   # microseconds — validate with check_exposure()
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
            "format": "RGB888",          # OpenCV-native is BR888; we will promote to 16-bit
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


def estimate_exposure(picam2: Picamera2,
                      target_fraction: float = 0.70,
                      reference_channel: str = "G",
                      max_exposure_us: int = 900_000) -> int:
    """
    Fire a test capture at the current ExposureTime and calculate the
    exposure needed to bring the reference channel to target_fraction
    of full scale.

    Returns the recommended ExposureTime in microseconds.

    Run this BEFORE your calibration captures, with your white reference
    card in place and all enclosure lighting active.
    """
    # Read what the camera is currently set to
    current_meta = picam2.capture_metadata()
    current_exposure = current_meta["ExposureTime"]

    # Capture a test frame
    bgr = picam2.capture_array()

    ch_idx = {"B": 0, "G": 1, "R": 2}[reference_channel]
    current_mean = float(bgr[:, :, ch_idx].mean())
    target_mean  = target_fraction * 255.0

    if current_mean < 1.0:
        print("WARNING: channel mean < 1.0 — image is essentially black. "
              "Check illumination and lens cap.")
        return current_exposure

    scale_factor     = target_mean / current_mean
    recommended_us   = int(current_exposure * scale_factor)
    recommended_us   = min(recommended_us, max_exposure_us)

    # Report
    print(f"\nExposure estimation:")
    print(f"    Blue  mean: {float(bgr[:, :, 0].mean()):4.1f} / 255")
    print(f"    Green mean: {float(bgr[:, :, 1].mean()):4.1f} / 255")
    print(f"    Red   mean: {float(bgr[:, :, 2].mean()):4.1f} / 255")
    print(f"  Current ExposureTime : {current_exposure:,} µs")
    print(f"  {reference_channel}-channel mean      : {current_mean:.1f} / 255")
    print(f"  Target mean          : {target_mean:.1f} / 255  ({target_fraction*100:.0f}%)")
    print(f"  Scale factor         : {scale_factor:.2f}×")
    print(f"  Recommended exposure : {recommended_us:,} µs  "
          f"({recommended_us/1e6:.3f} s)")

    if recommended_us >= max_exposure_us:
        print(f"  ⚠  Clamped recommended_us to {max_exposure_us:,} µs — your illumination "
              f"may be insufficient. Consider brighter LEDs.")

    return recommended_us


def apply_exposure_and_verify(picam2: Picamera2, exposure_us: int) -> dict:
    """
    Apply a new ExposureTime, wait for stabilisation, capture, and
    report the resulting channel statistics.
    """
    picam2.set_controls({"ExposureTime": exposure_us})
    time.sleep(1.0)   # allow AE-off sensor to settle to new shutter

    bgr   = picam2.capture_array()
    stats = check_exposure(bgr)   # reuse function from previous code

    return stats


################# NOT YET USED ######################
def decode_bayer_to_linear_rgb(
    bayer_uint16: np.ndarray,
    sensor_black_level: int = 64,       # IMX219 typical black level (ADU)
    sensor_white_level: int = 1023,     # 10-bit max
    wb_gains_bggr: tuple = (1.0, 1.0, 1.0, 1.0),  # (B, G_r, G_b, R) — tune these
    output_bits: int = 16,
) -> np.ndarray:
    """
    Convert a raw BGGR Bayer uint16 array to a linear (gamma=1.0) RGB image.

    Parameters
    ----------
    bayer_uint16     : raw array as saved by np.save — shape (H, W), uint16,
                       values in range [0, 1023] (10-bit, lower bits valid)
    sensor_black_level: ADU offset subtracted before any scaling.
                       The IMX219 datasheet quotes 64 ADU; verify from a
                       dark-frame capture if precision matters.
    sensor_white_level: maximum linear ADU (1023 for 10-bit)
    wb_gains_bggr    : per-channel multiplicative gains applied BEFORE
                       demosaicing to equalise the four Bayer channels.
                       Calibrate against your white reference (see below).
    output_bits      : 16 → uint16 output (0–65535), 8 → uint8 (0–255)

    Returns
    -------
    linear_bgr : np.ndarray  shape (H, W, 3), dtype uint16 or uint8
                 Pixel values are LINEAR — proportional to photon counts.
                 No gamma, no tone mapping, no sharpening.
    """

    # ------------------------------------------------------------------
    # Step 1: Black-level subtract and normalise to float [0.0, 1.0]
    # ------------------------------------------------------------------
    bayer_f = bayer_uint16.astype(np.float32)
    bayer_f = (bayer_f - sensor_black_level) / (sensor_white_level - sensor_black_level)
    bayer_f = np.clip(bayer_f, 0.0, 1.0)

    # ------------------------------------------------------------------
    # Step 2: Apply per-channel white-balance gains in-place on the
    # Bayer mosaic.  BGGR layout:
    #   even rows, even cols → B
    #   even rows, odd  cols → Gb  (green in blue row)
    #   odd  rows, even cols → Gr  (green in red row)
    #   odd  rows, odd  cols → R
    # ------------------------------------------------------------------
    g_b, g_gr, g_gb, g_r = wb_gains_bggr
    bayer_f[0::2, 0::2] *= g_b    # B
    bayer_f[0::2, 1::2] *= g_gb   # Gb
    bayer_f[1::2, 0::2] *= g_gr   # Gr
    bayer_f[1::2, 1::2] *= g_r    # R
    bayer_f = np.clip(bayer_f, 0.0, 1.0)

    # ------------------------------------------------------------------
    # Step 3: Demosaic — convert BGGR mosaic to full-colour BGR.
    # OpenCV's Bayer conversion code for BGGR → BGR is COLOR_BayerBG2BGR.
    # Using the higher-quality variable-number-of-gradients (VNG) algorithm
    # reduces colour fringing at edges — worth the small extra cost.
    # ------------------------------------------------------------------
    # OpenCV demosaic expects uint8 or uint16 input
    bayer_u16 = (bayer_f * 65535).astype(np.uint16)
    linear_bgr = cv2.cvtColor(bayer_u16, cv2.COLOR_BayerBG2BGR_VNG)

    # ------------------------------------------------------------------
    # Step 4: Scale to requested output bit depth
    # ------------------------------------------------------------------
    if output_bits == 8:
        linear_bgr = (linear_bgr / 256).astype(np.uint8)
    # else: already uint16

    return linear_bgr


def calibrate_wb_from_bayer(bayer_uint16: np.ndarray,
                             black_level: int = 64) -> tuple:
    """
    Compute white-balance gains from a Bayer frame of your WHITE reference.

    Point the camera at your white card, capture, save the raw .npy,
    load it, and pass it here.  Returns wb_gains_bggr to paste into
    decode_bayer_to_linear_rgb() calls.
    """
    b_f  = bayer_uint16[0::2, 0::2].astype(float) - black_level   # B
    gb_f = bayer_uint16[0::2, 1::2].astype(float) - black_level   # Gb
    gr_f = bayer_uint16[1::2, 0::2].astype(float) - black_level   # Gr
    r_f  = bayer_uint16[1::2, 1::2].astype(float) - black_level   # R

    b_mean  = b_f.mean()
    gb_mean = gb_f.mean()
    gr_mean = gr_f.mean()
    r_mean  = r_f.mean()
    g_mean  = (gb_mean + gr_mean) / 2.0   # average both green channels

    # Normalise so green = 1.0
    gains = (g_mean/b_mean, g_mean/gr_mean, g_mean/gb_mean, g_mean/r_mean)
    print(f"Channel means (post black-subtract):  "
          f"B={b_mean:.1f}  Gr={gr_mean:.1f}  Gb={gb_mean:.1f}  R={r_mean:.1f}")
    print(f"WB gains (B, Gr, Gb, R): "
          f"({gains[0]:.4f}, {gains[1]:.4f}, {gains[2]:.4f}, {gains[3]:.4f})")
    return gains
####################################

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    picam2 = build_camera()
    try:
        input ("Press any key when ready - but NOT CTL-Z!")
        # --- Step 1: find the right exposure ---
        print("=== Exposure calibration ===")
        time.sleep(2.0) 
        recommended = estimate_exposure(picam2, reference_channel="B", target_fraction=0.70)

        print(f"\n=== Applying recommended exposure ({recommended}) and verifying ===")
        picam2.set_controls({"ExposureTime": recommended})
        time.sleep(1.5)
        verify_stats = {}
        bgr_check = picam2.capture_array()
        check_exposure(bgr_check)

        # --- Step 2: full scientific capture ---
        print("\n=== Final capture ===")
        result = capture(picam2, output_dir="./colorimeter_output")

    finally:
        picam2.stop()
        picam2.close()

#---------------------------------- #
# Color calibration card # 1        #
# --------------------------------- #
ROI_CARDS = {
    'Calibration card 1':{
        'bounds':  [(   0,   0, 3280, 2464)]
        'blue':    [(   0,   0,  400,  400)],
        'white':   [( 440,   0,  400,  400),
                    ( 880, 320,  410,  390)]
    }
}
