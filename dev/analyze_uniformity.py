#!/usr/bin/env python3
"""
Analyze spatial luminance uniformity of a Picamera2 bayer image.

Reads a .npy bayer file, extracts the four color layers (B, Gb, Gr, R),
then plots the mean pixel value across:
  - 5 horizontal strips (full width × 100 px tall), evenly spaced top-to-bottom
  - 7 vertical   strips (100 px wide × full height), evenly spaced left-to-right

Produces two figures, each with 2×2 subplots (one per bayer layer):
  Figure 1: Horizontal strip profiles — mean along columns, x-axis = column
  Figure 2: Vertical   strip profiles — mean along rows,    x-axis = row

Usage:
    python analyze_uniformity.py <path/to/image.npy>
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

# Allow importing sibling modules from the dev/ directory
sys.path.insert(0, str(Path(__file__).parent))
from analyze_SNR import gaussian_smooth_bayer, poly_smooth_bayer

# ---------------------------------------------------------------------------
# Camera detection
# ---------------------------------------------------------------------------

# Map (width, height) original image size → bayer pattern
_SHAPE_TO_PATTERN = {
    (3280, 2464): "BGGR",   # IMX219 full
    (1640, 1232): "BGGR",   # IMX219 half
    (4608, 2592): "RGGB",   # IMX708 full
    (2304, 1296): "RGGB",   # IMX708 half
}


def detect_pattern(bayer_shape: tuple, txt_path: Path) -> str:
    """Return 'BGGR' or 'RGGB' from the .txt sidecar or image shape."""
    if txt_path.exists():
        text = txt_path.read_text()
        if "BGGR" in text:
            return "BGGR"
        if "RGGB" in text:
            return "RGGB"
    H, W = bayer_shape
    key = (W, H)
    if key in _SHAPE_TO_PATTERN:
        return _SHAPE_TO_PATTERN[key]
    raise ValueError(
        f"Cannot determine bayer pattern for shape {bayer_shape}. "
        f"No matching camera profile and no sidecar .txt file."
    )


# ---------------------------------------------------------------------------
# Bayer layer extraction
# ---------------------------------------------------------------------------

def extract_layers(bayer: np.ndarray, pattern: str) -> dict:
    """Return dict of B, Gb, Gr, R arrays each shape (H//2, W//2)."""
    if pattern == "BGGR":
        return {
            'B':  bayer[0::2, 0::2],
            'Gb': bayer[0::2, 1::2],
            'Gr': bayer[1::2, 0::2],
            'R':  bayer[1::2, 1::2],
        }
    elif pattern == "RGGB":
        return {
            'R':  bayer[0::2, 0::2],
            'Gr': bayer[0::2, 1::2],
            'Gb': bayer[1::2, 0::2],
            'B':  bayer[1::2, 1::2],
        }
    raise ValueError(f"Unknown pattern {pattern!r}")


# ---------------------------------------------------------------------------
# Strip analysis
# ---------------------------------------------------------------------------

N_H_STRIPS   = 5      # horizontal strips
N_V_STRIPS   = 7      # vertical strips
STRIP_THICK  = 100    # strip thickness in bayer-layer pixels


def make_h_strips(layer: np.ndarray):
    """
    Return list of (center_row, profile) for N_H_STRIPS horizontal strips.
    Each strip spans the full width and STRIP_THICK rows.
    Centers are evenly distributed from top edge to bottom edge.
    profile shape: (W_layer,) — mean over the 100-row axis.
    """
    H, _W = layer.shape
    half = STRIP_THICK // 2
    centers = np.linspace(half, H - half, N_H_STRIPS).astype(int)
    strips = []
    for cy in centers:
        r0, r1 = cy - half, cy + half
        profile = layer[r0:r1, :].mean(axis=0)
        strips.append((cy, profile))
    return strips


def make_v_strips(layer: np.ndarray):
    """
    Return list of (center_col, profile) for N_V_STRIPS vertical strips.
    Each strip spans the full height and STRIP_THICK columns.
    Centers are evenly distributed from left edge to right edge.
    profile shape: (H_layer,) — mean over the 100-col axis.
    """
    _H, W = layer.shape
    half = STRIP_THICK // 2
    centers = np.linspace(half, W - half, N_V_STRIPS).astype(int)
    strips = []
    for cx in centers:
        c0, c1 = cx - half, cx + half
        profile = layer[:, c0:c1].mean(axis=1)
        strips.append((cx, profile))
    return strips


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

LAYER_ORDER  = ['B', 'Gb', 'Gr', 'R']
LAYER_COLORS = {'B': 'blue', 'Gb': 'green', 'Gr': 'limegreen', 'R': 'red'}


def _setup_fig(title: str) -> tuple:
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    fig.suptitle(title, fontsize=13)
    return fig, axes


def plot_h_strips(layers: dict, img_shape: tuple, pattern: str, stem: str):
    """Figure 1: horizontal strip profiles for each bayer layer."""
    H, W = img_shape
    fig, axes = _setup_fig(
        f"Horizontal strip uniformity — {stem}  ({W}×{H} px, {pattern})\n"
        f"{N_H_STRIPS} strips × {STRIP_THICK} px tall, full width — mean along height axis"
    )
    cmap = plt.cm.get_cmap('cool', N_H_STRIPS)

    for idx, name in enumerate(LAYER_ORDER):
        ax = axes[idx // 2][idx % 2]
        layer = layers[name]
        _, W_l = layer.shape

        strips = make_h_strips(layer)
        x = np.arange(W_l)

        for i, (cy, profile) in enumerate(strips):
            ax.plot(x, profile, color=cmap(i), linewidth=1.2,
                    label=f"H{i+1} (row≈{cy*2})")

        ax.set_title(f"Layer {name}", color=LAYER_COLORS[name], fontweight='bold')
        ax.set_xlabel("Column (bayer layer pixels)")
        ax.set_ylabel("Mean pixel value")
        ax.legend(fontsize=8, loc='upper right')
        ax.grid(True, alpha=0.3)

    fig.tight_layout()


def plot_v_strips(layers: dict, img_shape: tuple, pattern: str, stem: str):
    """Figure 2: vertical strip profiles for each bayer layer."""
    H, W = img_shape
    fig, axes = _setup_fig(
        f"Vertical strip uniformity — {stem}  ({W}×{H} px, {pattern})\n"
        f"{N_V_STRIPS} strips × {STRIP_THICK} px wide, full height — mean along width axis"
    )
    cmap = plt.cm.get_cmap('plasma', N_V_STRIPS)

    for idx, name in enumerate(LAYER_ORDER):
        ax = axes[idx // 2][idx % 2]
        layer = layers[name]
        H_l, _ = layer.shape

        strips = make_v_strips(layer)
        x = np.arange(H_l)

        for i, (cx, profile) in enumerate(strips):
            ax.plot(x, profile, color=cmap(i), linewidth=1.2,
                    label=f"V{i+1} (col≈{cx*2})")

        ax.set_title(f"Layer {name}", color=LAYER_COLORS[name], fontweight='bold')
        ax.set_xlabel("Row (bayer layer pixels)")
        ax.set_ylabel("Mean pixel value")
        ax.legend(fontsize=8, loc='upper right')
        ax.grid(True, alpha=0.3)

    fig.tight_layout()


# ---------------------------------------------------------------------------
# Symmetric lighting simulation + contour plot
# ---------------------------------------------------------------------------

def symmetric_sum(layer: np.ndarray) -> np.ndarray:
    """
    Simulate four-corner symmetric illumination from a single-corner source.

    Adds the original layer to its three symmetric counterparts:
      - 180° rotation      → opposite corner
      - vertical flip      → horizontal mirror corner
      - horizontal flip    → vertical mirror corner

    The result is a float64 array with the same shape as the input.
    """
    orig   = layer.astype(np.float64)
    rot180 = np.rot90(orig, 2)         # 180° rotation (opposite corner)
    flip_v = np.flipud(orig)           # flip top↔bottom (horizontal mirror axis)
    flip_h = np.fliplr(orig)           # flip left↔right (vertical mirror axis)
    return orig + rot180 + flip_v + flip_h


def plot_contour(smoothed_layers: dict, img_shape: tuple, pattern: str, stem: str,
                 method: str = ""):
    """Figure 3: contour map of simulated 4-corner symmetric illumination (smoothed)."""
    H, W = img_shape
    method_label = f"{method}-smoothed  |  " if method else ""
    fig, axes = _setup_fig(
        f"4-corner symmetric illumination (contour) — {stem}  ({W}×{H} px, {pattern})\n"
        f"{method_label}original + 180° rot + horiz mirror + vert mirror"
    )

    for idx, name in enumerate(LAYER_ORDER):
        ax = axes[idx // 2][idx % 2]
        layer = smoothed_layers[name]

        combined = symmetric_sum(layer)

        H_l, W_l = combined.shape
        x = np.arange(W_l)
        y = np.arange(H_l)

        ax.set_facecolor('white')
        cf = ax.contour(x, y, combined, levels=20, cmap='plasma', linewidths=1.0)
        fig.colorbar(cf, ax=ax, fraction=0.046, pad=0.04)

        ax.set_title(f"Layer {name}", color=LAYER_COLORS[name], fontweight='bold')
        ax.set_xlabel("Column (bayer layer pixels)")
        ax.set_ylabel("Row (bayer layer pixels)")
        ax.set_aspect('equal')
        # y=0 at top to match image convention
        ax.invert_yaxis()

    fig.tight_layout()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

PICAM_DIR = Path("/home/upload/picam")


def resolve_npy_path(arg) -> Path:
    """
    Resolve a .npy file path from a command-line argument.

    Rules:
      - No argument   → most recent .npy in PICAM_DIR
      - Numeric string (e.g. '14') → file in PICAM_DIR whose name ends in '14.npy'
      - Anything else → treat as a direct file path
    """
    if arg is None:
        candidates = sorted(PICAM_DIR.glob("*.npy"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            print(f"Error: no .npy files found in {PICAM_DIR}")
            sys.exit(1)
        path = candidates[-1]
        print(f"Using most recent: {path.name}")
        return path

    if arg.isdigit():
        matches = [p for p in PICAM_DIR.glob("*.npy") if p.stem.endswith(arg)]
        if not matches:
            print(f"Error: no .npy file ending in '{arg}' found in {PICAM_DIR}")
            sys.exit(1)
        if len(matches) > 1:
            print(f"Warning: multiple matches for '{arg}', using most recent:")
            matches = sorted(matches, key=lambda p: p.stat().st_mtime)
            for m in matches:
                print(f"  {m.name}")
        return matches[-1]

    return Path(arg)


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if len(sys.argv) > 2:
        print(f"Usage: {sys.argv[0]} [<image.npy> | <number> | (blank for latest)]")
        sys.exit(1)

    npy_path = resolve_npy_path(arg)
    if not npy_path.exists():
        print(f"Error: file not found: {npy_path}")
        sys.exit(1)

    bayer = np.load(str(npy_path))
    if bayer.dtype != np.uint16:
        print(f"Warning: expected uint16, got {bayer.dtype}. Proceeding anyway.")

    H, W = bayer.shape
    print(f"Loaded : {npy_path.name}  shape=({H}, {W})  dtype={bayer.dtype}"
          f"  max={bayer.max()}  min={bayer.min()}")

    txt_path = npy_path.with_suffix('.txt')
    pattern  = detect_pattern(bayer.shape, txt_path)
    print(f"Pattern: {pattern}")

    layers = extract_layers(bayer, pattern)
    for name, arr in layers.items():
        print(f"  {name}: shape={arr.shape}  mean={arr.mean():.1f}  max={arr.max()}")

    # Gaussian smooth — SNR analysis
    gauss_layers, gauss_info = gaussian_smooth_bayer(bayer)

    print("\n--- SNR analysis (Gaussian smooth) ---")
    print(f"  image_size  : {gauss_info['image_size']}")
    print(f"  kernel_size : {gauss_info['kernel_size']}")
    print(f"  sigma       : {gauss_info['sigma']}")
    print(f"  black_level : {gauss_info['black_level']}")
    print(f"  {'Chan':>4}  {'mean':>8}  {'signal':>8}  {'noise_std':>10}  {'rel_noise':>10}  {'SNR':>8}")
    for ch, m in gauss_info['channels'].items():
        print(f"  {ch:>4}  {m['mean']:8.1f}  {m['signal']:8.1f}  "
              f"{m['noise_stdev']:10.3f}  {m['relative_noise']:10.6f}  {m['snr']:8.2f}")

    # Polynomial smooth — surface fit for contour plots
    poly_layers, poly_info = poly_smooth_bayer(bayer, poly_degree=4)

    print("\n--- SNR analysis (poly smooth, degree 4) ---")
    print(f"  poly_degree       : {poly_info['poly_degree']}")
    print(f"  fit_downsampled   : {poly_info['poly_fit_downsampled']}")
    if poly_info['poly_fit_downsampled']:
        print(f"  fit_grid (w×h)    : {poly_info['poly_fit_grid']}")
    print(f"  black_level       : {poly_info['black_level']}")
    print(f"  {'Chan':>4}  {'mean':>8}  {'signal':>8}  {'noise_std':>10}  {'rel_noise':>10}  {'SNR':>8}")
    for ch, m in poly_info['channels'].items():
        print(f"  {ch:>4}  {m['mean']:8.1f}  {m['signal']:8.1f}  "
              f"{m['noise_stdev']:10.3f}  {m['relative_noise']:10.6f}  {m['snr']:8.2f}")

    stem     = npy_path.stem
    out_dir  = Path("/home/bill/R")
    out_dir.mkdir(parents=True, exist_ok=True)

    plot_h_strips(layers, (H, W), pattern, stem)
    plt.savefig(out_dir / f"{stem}_hbalance.png", dpi=150, bbox_inches='tight')

    plot_v_strips(layers, (H, W), pattern, stem)
    plt.savefig(out_dir / f"{stem}_vbalance.png", dpi=150, bbox_inches='tight')

    plot_contour(poly_layers, (H, W), pattern, stem, method="poly")
    plt.savefig(out_dir / f"{stem}_contours.png", dpi=150, bbox_inches='tight')

    print(f"\nSaved plots to {out_dir}/")
    plt.show()


if __name__ == "__main__":
    main()
