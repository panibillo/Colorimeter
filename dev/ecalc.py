"""
ecalc.py — Interactive rectangle definition on JPEG + Bayer layer images.

Draw rectangles on a JPEG; after each draw, copy-pasteable Python code is
printed for the accumulated set in JPEG coords, full-Bayer coords (if the
.npy dimensions differ), and per-BGGR-layer coords.

Analysis is isolated in analyze_rect() for iteration without touching the UI.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# Make src/ and mock_camera/ importable when running from dev/
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "mock_camera"))

from bayer_cam import BayerCam

import matplotlib.image as mpimg
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.widgets import RectangleSelector

from ecalc_rects import size_and_group_rects

UPLOAD_DIR = Path("/home/upload/picam")
_DARK_BASENAME = "Tst6ANP0_G61_0018"   # dark frame used for BL estimation
_DARK_BASENAME = "Tst6BlackBP0_B61_0038"  # 150 ms   min-max ~ 62-256
_DARK_BASENAME = "Tst6BlackBP0_B61_0039"  #  50 ms   min-max ~ 62-162
_DARK_BASENAME = "Tst6BlackBP0_B61_0042"  #  20 ms   min-max ~ 62-106
_DARK_BASENAME = "Tst6BlackBP0_B61_0041"  #  10 ms   min-max ~ 62- 84
_DARK_BASENAME = "Tst6BlackBP0_B61_0040"  #   5 ms   min-max ~ 62- 70
_SAT = 1023                             # 10-bit saturation ceiling
_TARGET = _SAT * 0.95                  # 5% headroom target



# ---------------------------------------------------------------------------
# Analysis  (isolated — iterate here without touching the UI)
# ---------------------------------------------------------------------------

def analyze_rect(
    img_crop: np.ndarray,
    bayer_crops: Optional[dict[str, np.ndarray]],
) -> dict:
    """Analyze one rectangle region.

    Args:
        img_crop:    HxWx3 uint8 RGB sub-image.
        bayer_crops: {'B', 'Gr', 'Gb', 'R'} uint16 sub-arrays, or None.

    Returns:
        dict of results (stub — implement in future iterations).
    """
    return {}


# ---------------------------------------------------------------------------
# Coordinate utilities
# ---------------------------------------------------------------------------

def _scale_rect(
    rect: tuple[int, int, int, int],
    src_shape: tuple,
    dst_shape: tuple,
) -> tuple[int, int, int, int]:
    """Scale rect from src image coordinates to dst image coordinates."""
    x1, y1, x2, y2 = rect
    sy = dst_shape[0] / src_shape[0]
    sx = dst_shape[1] / src_shape[1]
    return (round(x1 * sx), round(y1 * sy), round(x2 * sx), round(y2 * sy))


def _layer_rect(rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Halve rect coordinates for per-BGGR-layer indexing (2×2 subsampling)."""
    x1, y1, x2, y2 = rect
    return (x1 // 2, y1 // 2, x2 // 2, y2 // 2)


def extract_bayer_crops(
    bayer: np.ndarray,
    rect_full: tuple[int, int, int, int],
) -> dict[str, np.ndarray]:
    """Slice each BGGR layer for the given full-array (x1,y1,x2,y2) rect."""
    x1, y1, x2, y2 = rect_full
    x1, y1 = x1 & ~1, y1 & ~1     # snap to 2×2 boundary
    x2, y2 = x2 & ~1, y2 & ~1
    s = bayer[y1:y2, x1:x2]
    return {
        "B":  s[0::2, 0::2],
        "Gr": s[0::2, 1::2],
        "Gb": s[1::2, 0::2],
        "R":  s[1::2, 1::2],
    }


# ---------------------------------------------------------------------------
# Code printing
# ---------------------------------------------------------------------------

def _print_code(
    rects_jpg: list[tuple],
    jpg_path: Path,
    jpg_shape: tuple,
    bayer_shape: Optional[tuple],
) -> None:
    sep = "=" * 66
    print(f"\n{sep}")
    print(f"# file_basename = '{jpg_path.stem}'")
    print(f"# JPEG  : {jpg_shape[1]} × {jpg_shape[0]} px")

    same_dims = True
    rects_full = rects_jpg
    if bayer_shape is not None:
        print(f"# Bayer : {bayer_shape[1]} × {bayer_shape[0]} px")
        if bayer_shape[:2] != jpg_shape[:2]:
            same_dims = False
            rects_full = [_scale_rect(r, jpg_shape, bayer_shape) for r in rects_jpg]

    print()
    print("rects_jpg = [")
    for i, r in enumerate(rects_jpg):
        w, h = r[2] - r[0], r[3] - r[1]
        print(f"    {r},  # rect {i}  ({w} × {h} px)")
    print("]")

    if bayer_shape is not None:
        if not same_dims:
            print()
            print("# Bayer full-array coords (scaled from JPEG):")
            print("rects_bayer = [")
            for i, r in enumerate(rects_full):
                w, h = r[2] - r[0], r[3] - r[1]
                print(f"    {r},  # rect {i}  ({w} × {h} px)")
            print("]")
        else:
            print()
            print("rects_bayer = rects_jpg  # JPEG and Bayer are same dimensions")

        lw, lh = bayer_shape[1] // 2, bayer_shape[0] // 2
        layer_rects = [_layer_rect(r) for r in rects_full]
        print()
        print(f"# Per-layer coords — each BGGR plane is {lw} × {lh} px:")
        print("# usage: layer = bayer[0::2, 0::2];  crop = layer[y1:y2, x1:x2]")
        print("rects_layer = [")
        for i, r in enumerate(layer_rects):
            w, h = r[2] - r[0], r[3] - r[1]
            print(f"    {r},  # rect {i}  ({w} × {h} px)")
        print("]")

    print(sep + "\n")


# ---------------------------------------------------------------------------
# Interactive drawing
# ---------------------------------------------------------------------------

_PATCH_COLORS = ["red", "lime", "cyan", "magenta", "yellow",
                 "orange", "white", "deepskyblue"]


class _RectDrawer:
    def __init__(
        self,
        fig: plt.Figure,
        ax: plt.Axes,
        img: np.ndarray,
        bayer: Optional[np.ndarray],
        jpg_path: Path,
    ) -> None:
        self.fig = fig
        self.ax = ax
        self.img = img
        self.bayer = bayer
        self.jpg_path = jpg_path
        self.rects_jpg: list[tuple] = []

        self.selector = RectangleSelector(
            ax,
            self._on_select,
            useblit=True,
            button=[1],         # left mouse button only
            minspanx=5,
            minspany=5,
            spancoords="pixels",
            interactive=False,  # selector disappears after release; patch persists
        )

    def _on_select(self, eclick, erelease) -> None:
        x1 = int(round(min(eclick.xdata, erelease.xdata)))
        y1 = int(round(min(eclick.ydata, erelease.ydata)))
        x2 = int(round(max(eclick.xdata, erelease.xdata)))
        y2 = int(round(max(eclick.ydata, erelease.ydata)))
        rect = (x1, y1, x2, y2)
        self.rects_jpg.append(rect)

        color = _PATCH_COLORS[(len(self.rects_jpg) - 1) % len(_PATCH_COLORS)]
        self.ax.add_patch(
            mpatches.Rectangle(
                (x1, y1), x2 - x1, y2 - y1,
                linewidth=2, edgecolor=color, facecolor="none",
            )
        )
        n = len(self.rects_jpg)
        self.ax.set_title(
            f"{n} rect{'s' if n != 1 else ''} defined — draw more, or close to finish",
            fontsize=10,
        )
        self.fig.canvas.draw()

        _print_code(
            self.rects_jpg,
            self.jpg_path,
            self.img.shape,
            self.bayer.shape if self.bayer is not None else None,
        )

# ---------------------------------------------------------------------------
# File resolution
# ---------------------------------------------------------------------------

def resolve_paths(file_basename: str) -> tuple[Path, Optional[Path], Optional[Path]]:
    """Return (jpg_path, npy_path_or_None) for the given basename.

    If file_basename is empty, uses the most recently modified JPEG in UPLOAD_DIR.
    """
    if file_basename:
        for ext in (".jpg", ".jpeg"):
            p = UPLOAD_DIR / f"{file_basename}{ext}"
            if p.exists():
                jpg_path = p
                break
        else:
            raise FileNotFoundError(
                f"No JPEG found for '{file_basename}' in {UPLOAD_DIR}"
            )
    else:
        candidates = sorted(
            [*UPLOAD_DIR.glob("*.jpg"), *UPLOAD_DIR.glob("*.jpeg")],
            key=lambda p: p.stat().st_mtime,
        )
        if not candidates:
            raise FileNotFoundError(f"No JPEG files found in {UPLOAD_DIR}")
        jpg_path = candidates[-1]
        print(f"Using most recent JPEG: {jpg_path.name}")

    npy_path = jpg_path.with_suffix(".npy")
    txt_path = jpg_path.with_suffix(".txt")
    return (jpg_path, 
            (npy_path if npy_path.exists() else None),
            (txt_path if txt_path.exists() else None))


# ---------------------------------------------------------------------------
# Slice label formatter
# ---------------------------------------------------------------------------

def _s(s: slice) -> str:
    """Compact [start:stop] or [start:stop:step] string for a slice."""
    step = f":{s.step}" if s.step is not None else ""
    return f"[{s.start}:{s.stop}{step}]"


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run2(file_basename: str, rects_layer: list) -> None:
    group0, group1 = size_and_group_rects(rects_layer, buffer=-5)

    _, npy_path, txt_path = resolve_paths(file_basename)
    if npy_path is None:
        raise FileNotFoundError("No .npy Bayer file found — cannot plot Bayer layers.")
    bayer = np.load(str(npy_path))
    pattern = _read_bayer_pattern(txt_path)
    ch, layer = _brightest_layer(bayer, pattern)

    g0_source_map = {
        'lr': layer,
        'ul': layer[::-1, ::-1],
        'ur': layer[:, ::-1],
        'll': layer[::-1, :],
    }

    fig, axes = plt.subplots(2, 4, figsize=(16, 8))
    fig.suptitle(f"Bayer {ch}-layer crops — {npy_path.name}", fontsize=10)

    for col, (key, (rs, cs)) in enumerate(group0.items()):
        crop = g0_source_map[key][rs, cs]
        ax = axes[0, col]
        ax.imshow(crop, cmap="gray")
        ax.set_title(f"G0:{key}\nrow={_s(rs)}\ncol={_s(cs)}", fontsize=7)
        ax.axis("off")

    for col, (key, (rs, cs)) in enumerate(group1.items()):
        crop = layer[rs, cs]
        ax = axes[1, col]
        ax.imshow(crop, cmap="gray")
        ax.set_title(f"G1:{key}\nrow={_s(rs)}\ncol={_s(cs)}", fontsize=7)
        ax.axis("off")

    plt.tight_layout()
    plt.show()




def _read_exposure_ms(txt_path: Optional[Path]) -> float:
    """Read actual camera exposure in ms from a .txt metadata file."""
    if txt_path is None:
        raise FileNotFoundError("No .txt metadata file found alongside the image.")
    text = txt_path.read_text()
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("actual_ms:"):
            return float(s.split(":", 1)[1].strip())
    # fallback: picamera2 ExposureTime is in microseconds
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("ExposureTime:"):
            return float(s.split(":", 1)[1].strip()) / 1000.0
    raise ValueError(f"Exposure time not found in {txt_path.name}")


def _read_bayer_pattern(txt_path: Optional[Path]) -> str:
    """Read bayer_pattern from the camera section of a .txt metadata file."""
    if txt_path is not None:
        for line in txt_path.read_text().splitlines():
            s = line.strip()
            if s.startswith("bayer_pattern:"):
                return s.split(":", 1)[1].strip().upper()
    return "BGGR"   # safe default for Camera Module 2


def _brightest_layer(bayer: np.ndarray, pattern: str) -> tuple[str, np.ndarray]:
    """Return (channel_name, layer) for the BGGR channel with the highest max value."""
    layers = BayerCam.extract_bayer_layers(bayer, pattern)
    ch = max(layers, key=lambda k: int(layers[k].max()))
    print (f"The brightest layer is {ch}. Max pixel value is {layers[ch].max()}")
    return ch, layers[ch]


def run3(file_basename: str, rects_layer: list) -> None:
    """Simulate 4-LED illumination by summing 4 symmetric Bayer layer crops.

    Physical model
    --------------
    Each raw pixel encodes:   raw = BL + signal
    With 4 simultaneous LEDs: raw_4LED = BL + signal_1 + signal_2 + signal_3 + signal_4
    Substituting signal_i = raw_i - BL:
        raw_4LED = sum(raw_i) - 3·BL   (BL appears once, not four times)
    Implemented as: sim = sum(crop_i - BL) + BL  (identical result)

    Exposure estimate
    -----------------
    The simulated signal above BL tells us the scene brightness under 4 LEDs
    at the current exposure.  Scaling linearly gives the exposure needed to
    land the peak at 95% of sensor saturation.
    """
    group0, group1 = size_and_group_rects(rects_layer, buffer=0)

    # -------------------------------------------------------------------------
    # Load image; inspect all 4 BGGR channels; choose the brightest
    # -------------------------------------------------------------------------
    _, npy_path, txt_path = resolve_paths(file_basename)
    if npy_path is None:
        raise FileNotFoundError("No .npy Bayer file found.")

    bayer   = np.load(str(npy_path))
    pattern = _read_bayer_pattern(txt_path)
    exp_ms  = _read_exposure_ms(txt_path)

    all_layers = BayerCam.extract_bayer_layers(bayer, pattern)

    print(f"\nFile:    {npy_path.name}")
    print(f"Pattern: {pattern}   Exposure: {exp_ms:.3f} ms")
    print("Channel maxima (10-bit raw counts):")
    for name, lyr in all_layers.items():
        print(f"  {name:2s}  max={int(lyr.max()):4d}  mean={lyr.mean():.1f}")

    # Use the channel with the highest maximum — this determines exposure headroom.
    # If we optimised for a dimmer channel, the brightest would saturate first.
    ch    = max(all_layers, key=lambda k: int(all_layers[k].max()))
    layer = all_layers[ch]
    print(f"\nSelected channel: {ch}  (highest max = {int(layer.max())})")

    # -------------------------------------------------------------------------
    # Black level from dark frame, using the same channel as the main image
    # -------------------------------------------------------------------------
    _, dark_npy, dark_txt = resolve_paths(_DARK_BASENAME)
    if dark_npy is None:
        raise FileNotFoundError(f"Dark frame not found: {_DARK_BASENAME}")

    dark         = np.load(str(dark_npy))
    dark_pattern = _read_bayer_pattern(dark_txt)
    dark_ch      = BayerCam.extract_bayer_layers(dark, dark_pattern)[ch]
    bl           = float(np.median(dark_ch))

    print(f"\nDark frame: {_DARK_BASENAME}.npy  (pattern={dark_pattern})")
    print(f"  {ch}-channel: min={int(dark_ch.min())}  median={bl:.1f}  max={int(dark_ch.max())}")

    # -------------------------------------------------------------------------
    # Build 4 oriented views of the full layer for Group 0
    #
    # Group 0 simulates 4 LEDs at symmetric positions around rect 0.
    # Each dict value is a numpy view (zero-copy) of 'layer'.
    # group0['lr'] slice is designed to index into the original orientation,
    # group0['ul'] into the 180°-rotated view, etc. — see ecalc_rects.py for
    # the derivation of these slice coordinates.
    # -------------------------------------------------------------------------
    g0_src = {
        'lr': layer,               # LED at the original position
        'ul': layer[::-1, ::-1],  # LED at 180° opposite
        'ur': layer[:, ::-1],     # LED mirrored left-right
        'll': layer[::-1, :],     # LED mirrored top-bottom
    }

    # -------------------------------------------------------------------------
    # Extract per-key crops and print their statistics
    # -------------------------------------------------------------------------
    print("\nGroup 0 crops (each from a differently-oriented source image):")
    g0_crops = []
    for key, (rs, cs) in group0.items():
        # g0_src[key] is the oriented image; (rs, cs) locates the rect inside it
        crop = g0_src[key][rs, cs]
        g0_crops.append(crop)
        print(f"  [{key}]  shape={crop.shape}  min={int(crop.min())}  "
              f"max={int(crop.max())}  mean={crop.mean():.1f}")

    # Group 1: slices are applied to the original (un-transformed) layer.
    # The in-place orientation variants are encoded in the slice step direction
    # (step=-1 reverses rows or cols), so no source image flipping is needed.
    print("\nGroup 1 crops (all from original layer; orientation in slice step):")
    g1_crops = []
    for key, (rs, cs) in group1.items():
        crop = layer[rs, cs]
        g1_crops.append(crop)
        print(f"  [{key}]  shape={crop.shape}  min={int(crop.min())}  "
              f"max={int(crop.max())}  mean={crop.mean():.1f}")

    # -------------------------------------------------------------------------
    # Simulate 4-LED signal
    #   sim[pixel] = (crop0[p] - BL) + (crop1[p] - BL) + (crop2[p] - BL)
    #              + (crop3[p] - BL) + BL
    #             = sum(crop_i[p]) - 3·BL
    # Accumulate in int32 to prevent uint16 overflow (4 × 1023 = 4092 > 1023).
    # -------------------------------------------------------------------------
    bl_int = int(round(bl))

    def _simulate(crops: list[np.ndarray]) -> np.ndarray:
        acc = np.zeros(crops[0].shape, dtype=np.int32)
        for crop in crops:
            acc += crop.astype(np.int32) - bl_int   # strip BL, add signal only
        return acc + bl_int                          # restore one BL offset

    g0_sim = _simulate(g0_crops)
    g1_sim = _simulate(g1_crops)

    print(f"\nSimulated 4-LED (BL = {bl_int}):")
    print(f"  Group 0  min={int(g0_sim.min())}  max={int(g0_sim.max())}  "
          f"mean={g0_sim.mean():.1f}")
    print(f"  Group 1  min={int(g1_sim.min())}  max={int(g1_sim.max())}  "
          f"mean={g1_sim.mean():.1f}")

    # -------------------------------------------------------------------------
    # Exposure estimation
    #
    # target_signal: how many counts above BL we want the peak to be (95% sat)
    # sim_max_signal: how many counts above BL the simulated peak currently is
    # scale = target_signal / sim_max_signal  →  est_exposure = actual × scale
    # -------------------------------------------------------------------------
    target_signal = _TARGET - bl
    g0_max_signal = float(g0_sim.max()) - bl
    g1_max_signal = float(g1_sim.max()) - bl

    print(f"\nExposure estimation:")
    print(f"  Saturation = {_SAT}   target = {_TARGET:.0f} ({_TARGET/_SAT*100:.0f}%)"
          f"   BL = {bl:.1f}")
    print(f"  Target signal above BL = {target_signal:.1f} counts")

    if g0_max_signal > 0:
        g0_scale  = target_signal / g0_max_signal
        g0_exp_ms = exp_ms * g0_scale
        print(f"  Group 0: sim_max_signal = {g0_max_signal:.1f}  "
              f"scale = {g0_scale:.4f}  →  {g0_exp_ms:.3f} ms")
    else:
        g0_exp_ms = float("nan")
        print(f"  Group 0: sim_max_signal = {g0_max_signal:.1f}  "
              f"(at or below BL — cannot estimate)")

    if g1_max_signal > 0:
        g1_scale  = target_signal / g1_max_signal
        g1_exp_ms = exp_ms * g1_scale
        print(f"  Group 1: sim_max_signal = {g1_max_signal:.1f}  "
              f"scale = {g1_scale:.4f}  →  {g1_exp_ms:.3f} ms")
    else:
        g1_exp_ms = float("nan")
        print(f"  Group 1: sim_max_signal = {g1_max_signal:.1f}  "
              f"(at or below BL — cannot estimate)")

    print(f"\nEstimated exposure for 4-LED capture (95% of saturation):")
    print(f"  Group 0 (rect 0):    {g0_exp_ms:.3f} ms")
    print(f"  Group 1 (rects 1–4): {g1_exp_ms:.3f} ms")

    # -------------------------------------------------------------------------
    # Plot
    # -------------------------------------------------------------------------
    fig, axes = plt.subplots(1, 2, figsize=(10, 5))
    fig.suptitle(
        f"Simulated 4-LED · {ch} channel · {npy_path.stem}\n"
        f"BL = {bl:.0f}   actual exp = {exp_ms:.1f} ms",
        fontsize=9,
    )
    for ax, sim, label, e in [
        (axes[0], g0_sim, "Group 0 (rect 0)",    g0_exp_ms),
        (axes[1], g1_sim, "Group 1 (rects 1–4)", g1_exp_ms),
    ]:
        ax.imshow(sim, cmap="gray")
        ax.set_title(
            f"{label}\nmax = {int(sim.max())}   est. exp = {e:.2f} ms",
            fontsize=8,
        )
        ax.axis("off")

    plt.tight_layout()
    plt.show()


def run(file_basename: str) -> None:
    jpg_path, npy_path, txt_path = resolve_paths(file_basename)
    img = mpimg.imread(str(jpg_path))
    bayer = np.load(str(npy_path)) if npy_path else None

    if bayer is not None:
        print(f"Bayer: {npy_path.name}  shape={bayer.shape}  dtype={bayer.dtype}")
    else:
        print("No .npy Bayer file found alongside JPEG — layer coords omitted.")

    fig, ax = plt.subplots(figsize=(12, 8))
    ax.imshow(img)
    ax.set_title("Left-click drag to draw a rectangle  |  close window when done")
    fig.suptitle(str(jpg_path), fontsize=9, color="gray")
    plt.tight_layout()

    _drawer = _RectDrawer(fig, ax, img, bayer, jpg_path)  # keep ref alive during plt.show()
    plt.show()


if __name__ == "__main__":
    rects_layer = [
        (405, 265, 491, 355),  # rect 0  (86 × 90 px)
        (622, 376, 711, 465),  # rect 1  (89 × 89 px)
        (176, 144, 265, 223),  # rect 2  (89 × 79 px)
        (627, 147, 716, 230),  # rect 3  (89 × 83 px)
        (174, 380, 266, 461),  # rect 4  (92 × 81 px)
    ]
    
    file_basename = "Tst6ARP0_R61_0024"          # ← set basename here; blank = most recent in /home/upload
    file_basename = ""
    run(file_basename)
    # run2(file_basename, rects_layer)
    run3(file_basename, rects_layer)
