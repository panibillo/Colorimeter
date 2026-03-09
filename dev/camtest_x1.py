"""
scientific_colorimeter_capture.py

CONFIGS 4,5,6 do not work.  1,2,3 do work.

Captures a single, maximally objective image from the Raspberry Pi
Camera Module 2 (IMX219) for colorimetric analysis.
"""

import time
import sys
import numpy as np
from pathlib import Path
from picamera2 import Picamera2
import libcamera
import cv2

# ---------------------------------------------------------------------------
# Calibrated white-balance gains — run calibrate_wb_from_bayer() first
# ---------------------------------------------------------------------------
WB_GAINS_RB      = (1.0, 1.0)              # (R_gain, B_gain) for ISP stream
WB_GAINS_BGGR    = (1.0, 1.0, 1.0, 1.0)   # (B, Gb, Gr, R)  for raw stream

EXPOSURE_TIME_US = 10_000
ANALOGUE_GAIN    = 1.0

# ---------------------------------------------------------------------------
# Camera configuration menu
# ---------------------------------------------------------------------------
CONFIGS = {
    "1": {
        "label":       "640×480 RGB — ISP-processed, sensor-cropped",
        "description": (
            "The ISP crops the centre of the full sensor and downsamples to "
            "640×480. Fast to capture and transfer; useful for framing and "
            "focus checks. NOT full-frame — field of view is reduced. "
            "8-bit sRGB output with gamma ~2.2; not suitable for radiometric "
            "measurement without gamma correction."
        ),
        "main_size":   (640, 480),
        "main_format": "BGR888",
        "raw_size":    None,               # no raw stream
        "is_raw":      False,
    },
    "2": {
        "label":       "1640×1232 RGB — ISP-processed, 2×2 binned full-frame",
        "description": (
            "The sensor reads every pixel in 2×2 binned mode (4 photosites "
            "averaged into 1), giving the full field of view at half linear "
            "resolution. The ISP then outputs 1640×1232 BGR888. Binning "
            "improves SNR by ~6 dB vs full-res. Good balance of speed and "
            "coverage for development work. Still 8-bit sRGB with gamma."
        ),
        "main_size":   (1640, 1232),
        "main_format": "BGR888",
        "raw_size":    None,
        "is_raw":      False,
    },
    "3": {
        "label":       "3280×2464 RGB — ISP-processed, full-resolution",
        "description": (
            "Full native sensor resolution, every photosite read individually. "
            "ISP outputs 3280×2464 BGR888. Maximum spatial detail; largest "
            "file size (~23 MB uncompressed). Still 8-bit sRGB with gamma — "
            "use configs 4–6 if linear radiometric data is required."
        ),
        "main_size":   (3280, 2464),
        "main_format": "BGR888",
        "raw_size":    None,
        "is_raw":      False,
    },
    "4": {
        "label":       "640×480 RAW Bayer — sensor-cropped, 10-bit linear",
        "description": (
            "Raw 10-bit BGGR Bayer data from a centre crop of the sensor. "
            "No demosaicing, no gamma, no ISP processing — pixel values are "
            "linearly proportional to photon counts (after black-level "
            "subtraction). Smallest raw file. FOV is reduced vs full-frame "
            "modes. Use for algorithm development where fast iteration "
            "matters more than full coverage."
        ),
        "main_size":   (640, 480),
        "main_format": "BGR888",           # preview alongside raw
        "raw_size":    (640, 480),
        "raw_format":  "SBGGR10_CSI2P",
        "is_raw":      True,
    },
    "5": {
        "label":       "1640×1232 RAW Bayer — 2×2 binned full-frame, 10-bit linear",
        "description": (
            "Raw 10-bit BGGR Bayer data, 2×2 binned full-frame. Best "
            "trade-off for scientific work during development: full field of "
            "view, ~6 dB SNR improvement from binning, moderate file size, "
            "and linear pixel values. Recommended default for colorimetry "
            "calibration runs."
        ),
        "main_size":   (1640, 1232),
        "main_format": "BGR888",
        "raw_size":    (1640, 1232),
        "raw_format":  "SBGGR10_CSI2P",
        "is_raw":      True,
    },
    "6": {
        "label":       "3280×2464 RAW Bayer — full-resolution, 10-bit linear",
        "description": (
            "Raw 10-bit BGGR Bayer data at full native sensor resolution. "
            "Every photosite captured individually — maximum spatial and "
            "tonal information. Required for final production colorimetry. "
            "Large files (~16 MB raw array). Allow extra stabilisation time."
        ),
        "main_size":   (3280, 2464),
        "main_format": "BGR888",
        "raw_size":    (3280, 2464),
        "raw_format":  "SBGGR10_CSI2P",
        "is_raw":      True,
    },
}


# ---------------------------------------------------------------------------
# User selection
# ---------------------------------------------------------------------------

def select_config() -> dict:
    print("\n=== Colorimeter — Camera Configuration ===\n")
    for key, cfg in CONFIGS.items():
        print(f"  [{key}]  {cfg['label']}")
        print(f"         {cfg['description']}\n")

    valid = set(CONFIGS.keys())
    while True:
        choice = input(f"Select configuration [{'/'.join(sorted(valid))}]: ").strip()
        if choice in valid:
            selected = CONFIGS[choice]
            print(f"\nSelected: [{choice}] {selected['label']}\n")
            return selected
        print(f"  Invalid choice '{choice}'. Please enter one of: {', '.join(sorted(valid))}")


# ---------------------------------------------------------------------------
# Camera setup
# ---------------------------------------------------------------------------

def build_camera(cfg: dict) -> Picamera2:
    picam2 = Picamera2()

    if cfg["is_raw"]:
        config = picam2.create_still_configuration(
            main={
                "size":   cfg["main_size"],
                "format": cfg["main_format"],
            },
            raw={
                "size":   cfg["raw_size"],
                "format": cfg["raw_format"],
            },
            buffer_count=1,
        )
    else:
        config = picam2.create_still_configuration(
            main={
                "size":   cfg["main_size"],
                "format": cfg["main_format"],
            },
            buffer_count=1,
        )

    picam2.configure(config)

    picam2.set_controls({
        "AeEnable":           False,
        "ExposureTime":       EXPOSURE_TIME_US,
        "AnalogueGain":       ANALOGUE_GAIN,
        "AwbEnable":          False,
        "ColourGains":        WB_GAINS_RB,
        "NoiseReductionMode": libcamera.controls.draft.NoiseReductionModeEnum.Off,
        "Sharpness":          0.0,
        "Contrast":           1.0,
        "Saturation":         1.0,
    })

    picam2.start()
    stabilise = 3.0 if cfg["main_size"][0] >= 3280 else 2.0
    print(f"Stabilising for {stabilise}s...")
    time.sleep(stabilise)
    return picam2


# ---------------------------------------------------------------------------
# Exposure estimation
# ---------------------------------------------------------------------------

def estimate_exposure(picam2: Picamera2,
                      target_fraction: float = 0.70,
                      reference_channel: str = "G",
                      max_exposure_us: int = 500_000) -> int:
    current_meta     = picam2.capture_metadata()
    current_exposure = current_meta["ExposureTime"]
    bgr              = picam2.capture_array()

    ch_idx       = {"B": 0, "G": 1, "R": 2}[reference_channel]
    current_mean = float(bgr[:, :, ch_idx].mean())
    target_mean  = target_fraction * 255.0

    if current_mean < 1.0:
        print("WARNING: channel mean < 1 — check illumination.")
        return current_exposure

    recommended = min(int(current_exposure * target_mean / current_mean),
                      max_exposure_us)

    print(f"Exposure estimation:")
    print(f"  Current : {current_exposure:,} µs,  G-mean = {current_mean:.1f}/255")
    print(f"  Target  : {target_mean:.0f}/255  ({target_fraction*100:.0f}% full scale)")
    print(f"  Recommended : {recommended:,} µs  ({recommended/1e6:.3f} s)")
    if recommended >= max_exposure_us:
        print(f"  ⚠  Clamped to {max_exposure_us:,} µs — consider brighter illumination.")

    return recommended


# ---------------------------------------------------------------------------
# Exposure check
# ---------------------------------------------------------------------------

def check_exposure(bgr: np.ndarray) -> dict:
    stats = {}
    for name, idx in (("B", 0), ("G", 1), ("R", 2)):
        ch      = bgr[:, :, idx]
        clipped = float(np.mean(ch >= 254))
        mean    = float(ch.mean())
        stats[name] = {"mean": mean, "max": int(ch.max()), "clipped": clipped}
        if clipped > 0.001:
            print(f"  ⚠  {name}: {clipped*100:.2f}% pixels clipped — reduce ExposureTime")
        elif mean < 30:
            print(f"  ⚠  {name}: mean={mean:.1f} — under-exposed, increase ExposureTime")
        else:
            print(f"  ✓  {name}: mean={mean:.1f}, max={stats[name]['max']}, "
                  f"clipped={clipped*100:.3f}%")
    return stats


# ---------------------------------------------------------------------------
# Raw Bayer decode
# ---------------------------------------------------------------------------

def decode_bayer_to_linear_rgb(
    bayer_uint16: np.ndarray,
    black_level:  int   = 64,
    white_level:  int   = 1023,
    wb_gains:     tuple = (1.0, 1.0, 1.0, 1.0),  # (B, Gb, Gr, R)
    output_bits:  int   = 16,
) -> np.ndarray:
    bayer_f = bayer_uint16.astype(np.float32)
    bayer_f = np.clip((bayer_f - black_level) / (white_level - black_level),
                      0.0, 1.0)

    g_b, g_gb, g_gr, g_r = wb_gains
    bayer_f[0::2, 0::2] *= g_b
    bayer_f[0::2, 1::2] *= g_gb
    bayer_f[1::2, 0::2] *= g_gr
    bayer_f[1::2, 1::2] *= g_r
    bayer_f = np.clip(bayer_f, 0.0, 1.0)

    bayer_u16   = (bayer_f * 65535).astype(np.uint16)
    linear_bgr  = cv2.cvtColor(bayer_u16, cv2.COLOR_BayerBG2BGR_VNG)

    if output_bits == 8:
        linear_bgr = (linear_bgr / 256).astype(np.uint8)
    return linear_bgr


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def capture(picam2: Picamera2, cfg: dict, output_dir: str = ".") -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    request  = picam2.capture_request()
    bgr8     = request.make_array("main")
    bayer    = request.make_array("raw") if cfg["is_raw"] else None
    meta     = request.get_metadata()
    request.release()

    # Always save a 16-bit PNG of the ISP stream for visual reference
    bgr16    = bgr8.astype(np.uint16) * 257
    rgb_path = out / "colorimeter_rgb.png"
    cv2.imwrite(str(rgb_path), bgr16)

    paths = {"rgb": rgb_path}

    if cfg["is_raw"] and bayer is not None:
        raw_path  = out / "colorimeter_raw.npy"
        lin_path  = out / "colorimeter_linear.png"
        np.save(str(raw_path), bayer)
        linear = decode_bayer_to_linear_rgb(bayer, wb_gains=WB_GAINS_BGGR)
        cv2.imwrite(str(lin_path), linear)
        paths.update({"raw": raw_path, "linear": lin_path})
        print(f"  Bayer  → {raw_path}  {bayer.shape} uint16")
        print(f"  Linear → {lin_path}  {linear.shape} uint16")

    meta_path = out / "colorimeter_meta.txt"
    with open(meta_path, "w") as f:
        f.write(f"Config : {cfg['label']}\n\n")
        for k, v in meta.items():
            f.write(f"{k}: {v}\n")
    paths["meta"] = meta_path

    print(f"Capture complete:")
    print(f"  RGB    → {rgb_path}  {bgr16.shape} uint16")
    print(f"  Meta   → {meta_path}")
    print(f"\nActual camera parameters:")
    for key in ("ExposureTime", "AnalogueGain", "ColourGains",
                "Lux", "ColourTemperature"):
        print(f"  {key}: {meta.get(key, 'n/a')}")

    return {**paths, "bgr8": bgr8, "bgr16": bgr16, "bayer": bayer}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg    = select_config()
    picam2 = build_camera(cfg)

    try:
        print("\n=== Exposure estimation ===")
        recommended = estimate_exposure(picam2)
        picam2.set_controls({"ExposureTime": recommended})
        time.sleep(1.5)

        print("\n=== Exposure verification ===")
        check_exposure(picam2.capture_array())

        print("\n=== Final capture ===")
        result = capture(picam2, cfg, output_dir="./colorimeter_output")

    finally:
        picam2.stop()
        picam2.close()