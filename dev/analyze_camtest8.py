#!/usr/bin/env python3
"""
analyze_camtest8.py — Evaluate camtest8 ROI exposure captures.

Produces one figure per Bayer channel (B, Gb, R — skipping Gr):

  Top left : e3 image (axes equal) with ROI_SAMPLE rectangle overlay
  Top right: columns 30–60 of all 5 exposures concatenated side by side (axes equal)
  Bottom   : 5 histograms — one per exposure — with descriptive labels

File naming expected (camtest8.py v4+):
  lit : camtest8_BOUND_{color}_{e_label}_{ms}ms_{ch}.npy
  dark: camtest8_BOUND_black_{e_label}_{ms}ms_{ch}.npy
  meta: camtest8_{color}_metadata.json

Usage:
    python dev/analyze_camtest8.py <ts_dir> <color> [channel]

    ts_dir  : path to a camtest8 timestamped output directory
    color   : LED color name, e.g.  green  cyan  red_orange  royal_blue
    channel : (optional) restrict to a single channel: B, Gb, or R

Examples:
    python dev/analyze_camtest8.py ~/pi/Colorimeter/dev/camtest8/20260615_1430 violet
    python dev/analyze_camtest8.py /path/to/ts_dir royal_blue Gb
"""

import json
import re
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches

# ---------------------------------------------------------------------------
# Constants — must match camtest8.py
# ---------------------------------------------------------------------------

HALF_SAT   = 512
EXP_LABELS = ["e0", "e1", "e3", "e5", "e6"]   # ordered capture sequence

EXP_DESC = {
    "e0": "e3 ÷ 1.5  (under-exposed)",
    "e1": "p89 → 512  (high signal)",
    "e3": "p50 → 512  (median ref)",
    "e5": "p11 → 512  (low signal)",
    "e6": "e3 × 1.5  (over-exposed)",
}

CHANNELS = ["B", "Gb", "R"]    # skip Gr

# ROI_SAMPLE rectangle position within the ROI_BOUND npy array (channel space)
# ROI_BOUND image space : x=1090, y=360, w=160, h=630
# ROI_SAMPLE image space: x=1150, y=790, w=68,  h=68
# Relative offset (image space), then ÷2 for channel space:
_ROI_SAMPLE_X_REL = (1150 - 1090) // 2    # 30
_ROI_SAMPLE_Y_REL = (790  - 360)  // 2    # 215
_ROI_SAMPLE_W     = 68 // 2               # 34
_ROI_SAMPLE_H     = 68 // 2               # 34
ROI_SAMPLE_IN_BOUND = (
    _ROI_SAMPLE_X_REL, _ROI_SAMPLE_Y_REL,
    _ROI_SAMPLE_W, _ROI_SAMPLE_H,
)

STRIP_COLS  = slice(30, 61)    # x-columns for strip comparison (30 inclusive … 60 inclusive)
STRIP_SEP_W = 2                # white separator columns between exposure strips


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def _parse_lit_stem(stem: str, color: str):
    """Parse camtest8_BOUND_{color}_{e_label}_{ms}ms_{ch} stem → (label, exp_ms) or None."""
    m = re.match(
        rf'^camtest8_BOUND_{re.escape(color)}_(e\w+)_(\d+)ms_(B|Gb|Gr|R)$',
        stem,
    )
    return (m.group(1), int(m.group(2))) if m else None


def _parse_dark_stem(stem: str):
    """Parse camtest8_BOUND_black_{e_label}_{ms}ms_{ch} stem → (label, exp_ms) or None."""
    m = re.match(
        r'^camtest8_BOUND_black_(e\w+)_(\d+)ms_(B|Gb|Gr|R)$',
        stem,
    )
    return (m.group(1), int(m.group(2))) if m else None


def _find_lit_files(ts_dir: Path, color: str, channel: str) -> list:
    """Return list of (label, exp_ms, path) for lit frames, sorted by EXP_LABELS order."""
    result = []
    for p in ts_dir.glob(f"camtest8_BOUND_{color}_e*_*ms_{channel}.npy"):
        parsed = _parse_lit_stem(p.stem, color)
        if parsed and parsed[0] in EXP_LABELS:
            result.append((*parsed, p))
    label_order = {lbl: i for i, lbl in enumerate(EXP_LABELS)}
    return sorted(result, key=lambda x: label_order.get(x[0], 99))


def _find_dark_files(ts_dir: Path, channel: str) -> dict:
    """Return dict mapping (label, exp_ms) → path for dark frames."""
    dark = {}
    for p in ts_dir.glob(f"camtest8_BOUND_black_e*_*ms_{channel}.npy"):
        parsed = _parse_dark_stem(p.stem)
        if parsed:
            dark[parsed] = p
    return dark


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_pair(lit_path: Path, dark_path: "Path | None"):
    """Load lit + dark arrays; return (lit, dark, net) as float64."""
    lit  = np.load(str(lit_path)).astype(np.float64)
    dark = (np.load(str(dark_path)).astype(np.float64)
            if dark_path is not None else np.zeros_like(lit))
    net  = np.clip(lit - dark, 0.0, 1023.0)
    return lit, dark, net


# ---------------------------------------------------------------------------
# Plotting helpers
# ---------------------------------------------------------------------------

def _make_strip_composite(frames: list) -> np.ndarray:
    """Concatenate STRIP_COLS strip of each exposure, with white separator columns."""
    parts = []
    for i, (_label, _ms, lit, _dark, _net) in enumerate(frames):
        if i > 0:
            parts.append(np.full((lit.shape[0], STRIP_SEP_W), 1023.0))
        parts.append(lit[:, STRIP_COLS].astype(np.float64))
    return np.hstack(parts)


def _plot_channel_figure(
    channel: str,
    color: str,
    ts_dir: Path,
    frames: list,
) -> plt.Figure:
    """
    Build one figure for a single Bayer channel.

    Panels:
      Top left : e3 imshow (axes equal) + ROI_SAMPLE rectangle
      Top right: strip comparison (STRIP_COLS of all exposures), axes equal
      Bottom   : 5 histograms side by side
    """
    fig = plt.figure(figsize=(18, 12))
    fig.suptitle(
        f"camtest8  {color} / channel {channel}  —  {ts_dir.name}",
        fontsize=13, fontweight="bold", y=0.995,
    )

    # Outer grid: 2 rows (top visual panels, bottom histograms)
    gs_outer = gridspec.GridSpec(
        2, 1, figure=fig, height_ratios=[3, 2], hspace=0.50,
    )

    # Top row: e3 image (left) | strip comparison (right)
    strip_w = STRIP_COLS.stop - STRIP_COLS.start          # 31 columns per exposure
    n_exp   = len(frames)
    composite_w = n_exp * strip_w + max(n_exp - 1, 0) * STRIP_SEP_W  # 163
    npy_w   = 80   # ROI_BOUND channel width
    gs_top  = gridspec.GridSpecFromSubplotSpec(
        1, 2, subplot_spec=gs_outer[0],
        width_ratios=[npy_w, composite_w], wspace=0.08,
    )
    ax_e3    = fig.add_subplot(gs_top[0, 0])
    ax_strip = fig.add_subplot(gs_top[0, 1])

    # Bottom row: one histogram per exposure
    gs_bot = gridspec.GridSpecFromSubplotSpec(
        1, n_exp, subplot_spec=gs_outer[1], wspace=0.30,
    )
    ax_hists = [fig.add_subplot(gs_bot[0, i]) for i in range(n_exp)]

    # ── e3 imshow ────────────────────────────────────────────────────────────
    e3_frame = next((f for f in frames if f[0] == "e3"), frames[n_exp // 2])
    e3_arr, e3_ms = e3_frame[2], e3_frame[1]

    ax_e3.imshow(
        e3_arr, cmap="gray", vmin=0, vmax=1023,
        aspect="equal", interpolation="nearest", origin="upper",
    )

    sx, sy, sw, sh = ROI_SAMPLE_IN_BOUND
    rect = mpatches.Rectangle(
        (sx - 0.5, sy - 0.5), sw, sh,
        linewidth=1.5, edgecolor="red", facecolor="none", linestyle="--",
    )
    ax_e3.add_patch(rect)
    ax_e3.text(sx + sw + 1, sy + sh // 2, "ROI_SAMPLE",
               color="red", fontsize=6, va="center")

    ax_e3.set_title(f"e3  {e3_ms} ms  (median reference)", fontsize=9)
    ax_e3.set_xlabel("column (channel px)", fontsize=7)
    ax_e3.set_ylabel("row (channel px)", fontsize=7)
    ax_e3.tick_params(labelsize=6)

    # ── Strip comparison ─────────────────────────────────────────────────────
    composite = _make_strip_composite(frames)
    ax_strip.imshow(
        composite, cmap="gray", vmin=0, vmax=1023,
        aspect="equal", interpolation="nearest", origin="upper",
    )

    # x-ticks at the center of each strip
    centers, tick_labels = [], []
    for i, (lbl, ms, *_) in enumerate(frames):
        col_start = i * (strip_w + STRIP_SEP_W)
        centers.append(col_start + strip_w // 2)
        tick_labels.append(f"{lbl}\n{ms}ms")
    ax_strip.set_xticks(centers)
    ax_strip.set_xticklabels(tick_labels, fontsize=8)
    ax_strip.set_ylabel("row (channel px)", fontsize=7)
    ax_strip.set_title(
        f"Strip comparison — columns {STRIP_COLS.start}–{STRIP_COLS.stop - 1}  "
        f"(all {n_exp} exposures side by side)",
        fontsize=9,
    )
    ax_strip.tick_params(axis="y", labelsize=6)

    # ── Histograms (ROI_SAMPLE pixels only) ─────────────────────────────────
    sx, sy, sw, sh = ROI_SAMPLE_IN_BOUND

    def _sample_pix(lit):
        return lit[sy:sy + sh, sx:sx + sw].ravel()

    max_count = max(
        np.histogram(_sample_pix(f[2]), bins=64, range=(0, 1024))[0].max()
        for f in frames
    )

    for i, (label, exp_ms, lit, _dark, _net) in enumerate(frames):
        ax  = ax_hists[i]
        pix = _sample_pix(lit)
        desc = EXP_DESC.get(label, "")

        counts, bin_edges = np.histogram(pix, bins=64, range=(0, 1024))
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
        ax.bar(bin_centers, counts, width=bin_edges[1] - bin_edges[0],
               color="#777777", alpha=0.75, edgecolor="none")

        p50  = float(np.median(pix))
        mean = float(pix.mean())
        std  = float(pix.std())
        snr  = mean / std if std > 0 else 0.0

        ax.axvline(HALF_SAT, color="black", linestyle="--", linewidth=1.5,
                   label=f"½sat={HALF_SAT}")
        ax.axvline(p50,      color="#1976D2", linestyle="-",  linewidth=1.5,
                   label=f"p50={p50:.0f}")
        ax.axvline(mean,     color="#D32F2F", linestyle=":",  linewidth=1.2,
                   label=f"mean={mean:.0f}")

        ax.set_title(f"{label}  {exp_ms}ms\n{desc}", fontsize=8, pad=3)
        ax.set_xlabel("pixel value (10-bit, 0–1023)", fontsize=6.5)
        if i == 0:
            ax.set_ylabel("count", fontsize=7)
        ax.tick_params(labelsize=6)
        ax.set_xlim(0, 1023)
        ax.set_ylim(0, max_count * 1.10)
        ax.legend(fontsize=5.5, loc="upper left", framealpha=0.7)
        ax.text(
            0.97, 0.97,
            f"std={std:.1f}\nSNR={snr:.1f}",
            transform=ax.transAxes, fontsize=6,
            ha="right", va="top",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="white", alpha=0.8),
        )

    return fig


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 3:
        # print(__doc__)
        # sys.exit(1)
        ts_dir = Path('dev/captures/20260615_1332').expanduser()
        color = 'violet'
        filter_channel = None
    else:
        ts_dir         = Path(sys.argv[1]).expanduser()
        color          = sys.argv[2]
        filter_channel = sys.argv[3] if len(sys.argv) >= 4 else None

    if not ts_dir.is_dir():
        sys.exit(f"Error: directory not found: {ts_dir}")

    channels = [filter_channel] if filter_channel else CHANNELS

    for channel in channels:
        lit_entries = _find_lit_files(ts_dir, color, channel)
        if not lit_entries:
            print(f"  Skipping channel {channel}: no lit files for color={color!r}")
            continue

        dark_map = _find_dark_files(ts_dir, channel)
        print(f"\nChannel {channel}: {len(lit_entries)} lit files, "
              f"{len(dark_map)} dark files")

        frames = []
        for label, exp_ms, path in lit_entries:
            dark_path = dark_map.get((label, exp_ms))
            lit, dark, net = _load_pair(path, dark_path)
            frames.append((label, exp_ms, lit, dark, net))
            status = "dark ✓" if dark_path else "no dark"
            print(f"  {label}  {exp_ms:5d}ms  shape={lit.shape}  {status}")

        fig = _plot_channel_figure(channel, color, ts_dir, frames)

        out_path = ts_dir / f"analyze_{color}_{channel}.png"
        fig.savefig(str(out_path), dpi=120, bbox_inches="tight")
        print(f"Saved: {out_path}")

    plt.show()


if __name__ == "__main__":
    main()
