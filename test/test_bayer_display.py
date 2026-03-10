#!/usr/bin/env python3
"""
test_bayer_display.py

Visual verification of Bayer raw image data from mock_Picamera2.

Loads cfg5_sbggr1_raw_00.npy and displays:
  1. Four color layers (B, Gb, Gr, R) in a 2×2 subplot grid
  2. Frequency distribution curves for all layers in a single plot
  3. Detailed statistics including min, max, mean, std, and quartiles
     (p10, p25, p50/median, p75, p90)

Purpose:
  - Verify cfg5_sbggr1_raw_00.npy can be read
  - Verify file is uint16 with correct shape
  - Allow visual inspection of Bayer demosaicing pattern
  - Analyze pixel value distributions
  - Confirm data ranges and statistics

Usage:
  python test_bayer_display.py
  # Displays 2 matplotlib figures with 4 subplots + distribution curve
"""

import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path


def extract_bayer_layers(bayer_array):
    """
    Extract the four color layers from BGGR Bayer pattern.
    
    Bayer mosaic layout in a (H, W) array:
        even rows, even cols   → B  (Blue)
        even rows, odd cols    → Gb (Green in blue row)
        odd rows, even cols    → Gr (Green in red row)
        odd rows, odd cols     → R  (Red)
    
    Args:
        bayer_array: numpy array, shape (H, W), dtype uint16
                     Raw Bayer sensor data, BGGR pattern
    
    Returns:
        dict with keys 'B', 'Gb', 'Gr', 'R', each containing
        the extracted color layer as (H//2, W//2) array
    
    Example:
        >>> bayer = np.random.randint(0, 1024, (480, 640), dtype=np.uint16)
        >>> layers = extract_bayer_layers(bayer)
        >>> layers['B'].shape
        (240, 320)
    """
    layers = {
        'B':  bayer_array[0::2, 0::2],    # Blue: even rows, even cols
        'Gb': bayer_array[0::2, 1::2],    # Green (blue row): even rows, odd cols
        'Gr': bayer_array[1::2, 0::2],    # Green (red row): odd rows, even cols
        'R':  bayer_array[1::2, 1::2],    # Red: odd rows, odd cols
    }
    return layers


def compute_layer_statistics(layers):
    """
    Compute statistics for each Bayer layer.
    
    Args:
        layers: dict with keys 'B', 'Gb', 'Gr', 'R', each containing
                a numpy array
    
    Returns:
        dict with statistics for each layer:
        {
            'B': {'min': x, 'max': y, 'mean': z, 'p10': ..., 'p25': ..., 
                  'p50': ..., 'p75': ..., 'p90': ...},
            ...
        }
    """
    stats = {}
    percentiles = [10, 25, 50, 75, 90]
    
    for name, layer in layers.items():
        layer_flat = layer.flatten()
        stats[name] = {
            'min':    int(layer.min()),
            'max':    int(layer.max()),
            'mean':   float(layer.mean()),
            'median': float(np.median(layer)),
            'std':    float(layer.std()),
        }
        # Add percentiles
        for p in percentiles:
            key = f'p{p}'
            stats[name][key] = float(np.percentile(layer_flat, p))
        
        # Count clipped values
        stats[name]['Count'] = np.size(layer_flat)
        stats[name]['Nlo'] = np.sum(layer_flat == 0)
        stats[name]['Nhi'] = np.sum(layer_flat == 255)
        stats[name]['p0'] = stats[name]['Nlo'] / stats[name]['Count']
        stats[name]['p100'] = stats[name]['Nhi'] / stats[name]['Count']
    
    return stats


def plot_layer_distributions(layers, output_file=None, imagename=''):
    """
    Plot frequency distributions of all four Bayer layers as separate vertical subplots.
    
    Uses descriptive colors matching the layer types:
      - B:  blue
      - Gb: green
      - Gr: green (darker shade)
      - R:  red
    
    All subplots share a common y-axis scale for easy comparison.
    
    Args:
        layers: dict with keys 'B', 'Gb', 'Gr', 'R', each containing
                a numpy array (from extract_bayer_layers())
        output_file: optional path to save figure
    
    Returns:
        fig, axes (matplotlib figure and axes)
    """
    # Color mapping for layers
    colors = {
        'B':  '#0000FF',      # Blue
        'Gb': "#00AA96",      # Green
        'Gr': "#BF8F00",      # Dark green
        'R':  '#FF0000',      # Red
    }
    
    # Create figure with 4 vertically stacked subplots
    fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)
    
    # Find max frequency across all layers for common y-axis scale
    max_freq = 0
    for name, layer in layers.items():
        layer_flat = layer.flatten()
        hist, _ = np.histogram(layer_flat, bins=64)
        max_freq = max(max_freq, hist.max())
    
    # Plot histogram for each layer
    for idx, name in enumerate(['B', 'Gb', 'Gr', 'R']):
        layer = layers[name]
        layer_flat = layer.flatten()
        axes[idx].hist(layer_flat, bins=254, histtype='step', linewidth=2.5,
                        label=name, color=colors[name])
        
        axes[idx].set_ylabel('Frequency', fontsize=10, fontweight='bold')
        axes[idx].set_title(f'{name} Layer Distribution  {imagename}', fontsize=11, fontweight='bold')
        # axes[idx].set_ylim(0, max_freq * 1.1)  # Common y-axis with 10% headroom
        axes[idx].grid(True, alpha=0.3, linestyle='--')
        axes[idx].legend(loc='upper right', fontsize=10, framealpha=0.95)
    
    # Set xlabel only on the bottom plot
    axes[3].set_xlabel('Pixel Value (10-bit: 0-1023)', fontsize=11, fontweight='bold')
    
    fig.suptitle('Bayer Layer Frequency Distributions', fontsize=13, fontweight='bold')
    plt.tight_layout()
    
    if output_file:
        fig.savefig(output_file, dpi=150, bbox_inches='tight')
        print(f"✓ Distribution plot saved to: {output_file}")
    
    return fig, axes


def print_layer_statistics(stats):
    """
    Print formatted statistics table for all layers.
    
    Args:
        stats: dict returned from compute_layer_statistics()
    """
    print(f"\nLayer Statistics Summary:")
    print(f"{'Layer':<6} {'Min':>6} {'Max':>6} {'Mean':>8} {'Median':>8} {'Std':>8}")
    print(f"{'-'*50}")
    
    for name in ['B', 'Gb', 'Gr', 'R']:
        s = stats[name]
        print(f"{name:<6} {s['max']:>6d} {s['mean']:>8.1f} "
              f"{s['median']:>8.1f} {s['std']:>8.1f}")
    
    print(f"\n{'Layer':<6} {'Nlo':>8} {'p0':>9} {'p10':>8} {'p25':>8} {'p50':>8} "
          f"{'p75':>8} {'p90':>8}  {'p100':>9} {'Nhi':>8}")
    print(f"{'-'*50}")
    
    for name in ['B', 'Gb', 'Gr', 'R']:
        s = stats[name]
        print(f"{name:<6} {s['Nlo']:>8d} {s['p0']:>9.3f} {s['p10']:>8.1f} {s['p25']:>8.1f} {s['p50']:>8.1f} "
              f"{s['p75']:>8.1f} {s['p90']:>8.1f} {s['p100']:>9.3f} {s['Nhi']:>8d}")


def plot_bayer_layers(bayer_array, output_file=None, imagename=''):
    """
    Display Bayer layers in a 2×2 subplot grid with color-matched colormaps.
    
    Args:
        bayer_array: numpy array, shape (H, W), dtype uint16
                     Raw Bayer sensor data
        output_file: optional path to save figure (e.g., 'bayer_display.png')
    
    Returns:
        fig, axes (matplotlib figure and axes objects)
    """
    layers = extract_bayer_layers(bayer_array)
    
    # Create 2×2 subplot grid
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    fig.suptitle(
        f'Bayer BGGR Pattern Decomposition {imagename}\n'
        f'Shape: {bayer_array.shape}, dtype: {bayer_array.dtype}, '
        f'min: {bayer_array.min()}, max: {bayer_array.max()}',
        fontsize=14, fontweight='bold'
    )
    
    # Top-left: Blue layer (blue colormap)
    im_b = axes[0, 0].imshow(layers['B'], cmap='Blues', aspect='auto')
    axes[0, 0].set_title(
        f"Blue (B) - {layers['B'].shape}\n"
        f"Range: [{layers['B'].min()}, {layers['B'].max()}]"
    )
    plt.colorbar(im_b, ax=axes[0, 0], label='Value')
    
    # Top-right: Green (blue row) - (green colormap)
    im_gb = axes[0, 1].imshow(layers['Gb'], cmap='Greens', aspect='auto')
    axes[0, 1].set_title(
        f"Green-Blue (Gb) - {layers['Gb'].shape}\n"
        f"Range: [{layers['Gb'].min()}, {layers['Gb'].max()}]"
    )
    plt.colorbar(im_gb, ax=axes[0, 1], label='Value')
    
    # Bottom-left: Green (red row) - (green colormap)
    im_gr = axes[1, 0].imshow(layers['Gr'], cmap='Greens', aspect='auto')
    axes[1, 0].set_title(
        f"Green-Red (Gr) - {layers['Gr'].shape}\n"
        f"Range: [{layers['Gr'].min()}, {layers['Gr'].max()}]"
    )
    plt.colorbar(im_gr, ax=axes[1, 0], label='Value')
    
    # Bottom-right: Red layer (red colormap)
    im_r = axes[1, 1].imshow(layers['R'], cmap='Reds', aspect='auto')
    axes[1, 1].set_title(
        f"Red (R) - {layers['R'].shape}\n"
        f"Range: [{layers['R'].min()}, {layers['R'].max()}]"
    )
    plt.colorbar(im_r, ax=axes[1, 1], label='Value')
    
    plt.tight_layout()
    
    if output_file:
        fig.savefig(output_file, dpi=150, bbox_inches='tight')
        print(f"✓ Figure saved to: {output_file}")
    
    return fig, axes


def main():
    """Load test file and display Bayer layers interactively."""
    
    fname = "cfg5_sbggr1_raw_00.npy"

    # Find mock_camera directory
    test_dir = Path(__file__).parent
    colorimeter_dir = test_dir.parent
    mock_dir = colorimeter_dir / "mock_camera"
    
    npy_file = mock_dir / fname
    
    print(f"Test: Bayer Raw Image Display")
    print(f"================================")
    print(f"Mock directory: {mock_dir}")
    print(f"Target file:   {npy_file}")
    print()
    
    # Check file existence
    if not npy_file.exists():
        print(f"⚠  ERROR: File not found: {npy_file}")
        return False
    
    # Load file
    print(f"Loading: {fname}")
    try:
        bayer = np.load(npy_file)
        print(f"✓ Loaded successfully")
    except Exception as e:
        print(f"✗ Failed to load: {e}")
        return False
    
    # Verify format
    print(f"\nFile Format Verification:")
    print(f"  Shape:  {bayer.shape}")
    print(f"  Dtype:  {bayer.dtype}")
    print(f"  Min:    {bayer.min()}")
    print(f"  Max:    {bayer.max()}")
    print(f"  Mean:   {bayer.mean():.1f}")
    print(f"  Median: {np.median(bayer):.1f}")
    
    # Verify shape is correct for config 5 (1640×1232)
    expected_shape = (1232, 1640)
    if bayer.shape != expected_shape:
        print(f"\n⚠  WARNING: Shape mismatch!")
        print(f"    Expected: {expected_shape}")
        print(f"    Got:      {bayer.shape}")
    else:
        print(f"  ✓ Shape matches config 5 (1640×1232)")
    
    # Verify dtype is uint16
    if bayer.dtype != np.uint16:
        print(f"\n⚠  WARNING: Dtype mismatch!")
        print(f"    Expected: uint16")
        print(f"    Got:      {bayer.dtype}")
    else:
        print(f"  ✓ Dtype is uint16 (10-bit Bayer in 16-bit container)")
    
    # Extract and verify layers
    print(f"\nExtracting Bayer layers...")
    layers = extract_bayer_layers(bayer)
    
    for name, layer in layers.items():
        print(f"  {name:2s} (BGGR['{name}']): {layer.shape}, "
              f"min={layer.min():5d}, max={layer.max():5d}, "
              f"mean={layer.mean():7.1f}")
    
    if 1:
        # Display plot
        print(f"\nDisplaying 2×2 subplot grid...")
        fig, axes = plot_bayer_layers(bayer, imagename=fname)
    
        # Optionally save
        output_path = test_dir / "bayer_display.png"
        fig.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"✓ Saved to: {output_path}")
    
    # Compute and display statistics
    print(f"\nComputing layer statistics...")
    stats = compute_layer_statistics(layers)
    print_layer_statistics(stats)
    
    # Plot frequency distributions
    print(f"\nDisplaying frequency distributions (4 subplots, common y-axis)...")
    dist_fig, dist_axes = plot_layer_distributions(layers, imagename=fname)
    
    dist_output_path = test_dir / "bayer_distributions.png"
    dist_fig.savefig(dist_output_path, dpi=150, bbox_inches='tight')
    print(f"✓ Saved to: {dist_output_path}")
    
    plt.show()
    
    print(f"\n✓ Test completed successfully!")
    return True


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
