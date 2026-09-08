"""
ecalc_noise.py — Per-ROI SNR estimation from raw Bayer captures.

For each of 5 ROI rectangles and each of 4 Bayer channels (B, Gb, Gr, R):
  1. Subtract the per-channel black level (median of a dark frame).
  2. Fit a degree-2 polynomial surface (normalized coords for stability).
  3. Compute SNR_dB = 20 * log10(mean_signal_above_BL / std(residuals)).

Results are written to 'ecalc_data.sqlite' in this folder, table 'ledtrials'.
Columns (in order):
  file_basename
  [all capture fields:  timestamp, title, basename, remarks, ...]
  [all setup fields:    led_desc, geometry, vcc, vled, ...]
  [all camera fields:   camera_model, exposure_ms, actual_ms, ...]
  [all lights fields:   active_leds, ...]
  [stat_* fields from the max-p100 statistics row, excluding min_v/max_v]
  FocusFoM, Lux, ColourTemperature  (from picamera metadata section)
  {ch}rect{n}db, {ch}rect{n}mean    for ch in (B, Gb, Gr, R), n in 0..4
"""

from __future__ import annotations
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import sys
print (sys.executable)

from scipy.interpolate import RBFInterpolator
from scipy.ndimage import gaussian_filter

import sqlite3
from pathlib import Path
from typing import Optional


_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "mock_camera"))

from bayer_cam import BayerCam
from ecalc import _DARK_BASENAME, _read_bayer_pattern, resolve_paths

_CHANNELS   = ['B', 'Gb', 'Gr', 'R']
_STAT_SKIP  = {'min_v', 'max_v'}
_PICAM_WANT = ('FocusFoM', 'Lux', 'ColourTemperature')
_EXP_TARGETS = np.array([0.30, 0.48, 0.62, 0.78, 0.94])  # nominal exposure fractions
DB_PATH     = Path(__file__).parent / "ecalc_data.sqlite"
_TABLE      = 'ledtrials'

# Copied from ecalc.py __main__ block
rects_layer = [
    (405, 265, 491, 355),  # rect 0  (86 × 90 px)
    (622, 376, 711, 465),  # rect 1  (89 × 89 px)
    (176, 144, 265, 223),  # rect 2  (89 × 79 px)
    (627, 147, 716, 230),  # rect 3  (89 × 83 px)
    (174, 380, 266, 461),  # rect 4  (92 × 81 px)
]


# ---------------------------------------------------------------------------
# SNR core
# ---------------------------------------------------------------------------

def _poly2_snr(crop_raw: np.ndarray, bl: float) -> tuple[float, float]:
    """Fit degree-2 surface; return (snr_db, mean_raw_pixel_value).

    Uses normalized [-1, 1] coordinates for numerical stability (same approach
    as poly_smooth_bayer in analyze_SNR.py).  BL is subtracted before fitting
    so the polynomial models illumination, not the pedestal.

    snr_db = 20 * log10(mean_signal_above_BL / std(residuals))
    """
    H, W = crop_raw.shape
    signal = crop_raw.astype(np.float64) - bl

    ys = np.linspace(-1.0, 1.0, H)
    xs = np.linspace(-1.0, 1.0, W)
    xg, yg = np.meshgrid(xs, ys)
    xf, yf = xg.ravel(), yg.ravel()

    A = np.column_stack([np.ones(H * W), xf, yf, xf**2, xf * yf, yf**2])
    coeffs, _, _, _ = np.linalg.lstsq(A, signal.ravel(), rcond=None)
    residuals = signal - (A @ coeffs).reshape(H, W)

    std_noise   = float(residuals.std())
    mean_signal = float(signal.mean())
    mean_raw    = float(crop_raw.mean())

    if std_noise > 0 and mean_signal > 0:
        snr_db = 20.0 * np.log10(mean_signal / std_noise)
    else:
        snr_db = float('nan')

    return snr_db, mean_raw


# ---------------------------------------------------------------------------
# Txt file parsing — key:value sections
# ---------------------------------------------------------------------------

def _parse_txt_sections(txt_path: Path) -> dict[str, dict[str, str]]:
    """Parse a metadata txt file into named sections.

    Handles two marker styles:
      '=== name ===' → section name 'header'  (first block, non-standard marker)
      '--- name ---' → section name derived from the text between the dashes

    Pure-dash separator lines (e.g. '------...------' with no spaces) are NOT
    treated as section markers, which lets the --- statistics --- tabular rows
    pass through harmlessly.  Only lines containing ':' are stored as key:value;
    tabular rows in the statistics section are silently ignored here.

    Returns {section_name: {key: value}}.
    """
    sections: dict[str, dict[str, str]] = {}
    current = 'header'
    sections[current] = {}

    for raw in txt_path.read_text().splitlines():
        line = raw.strip()

        # '=== name ===' → capture section
        if line.startswith('===') and line.endswith('===') and ' ' in line:
            current = 'header'
            sections.setdefault(current, {})

        # '--- name ---' → named section  (' ' in line excludes pure-dash separators)
        elif line.startswith('---') and line.endswith('---') and ' ' in line:
            current = line.strip('-').strip().lower().replace(' ', '_')
            sections.setdefault(current, {})

        elif ':' in line:
            key, _, val = line.partition(':')
            sections[current][key.strip()] = val.strip()

    return sections


# ---------------------------------------------------------------------------
# Txt file parsing — statistics section (tabular)
# ---------------------------------------------------------------------------

def _parse_statistics(txt_path: Path) -> dict[str, str]:
    """Parse the '--- statistics ---' section; return the max-p100 row as stat_* dict.

    The section is a whitespace-aligned table.  The first content line is the
    header; the second is a pure-dash separator (skipped); remaining lines are
    data.  The row with the highest p100 value is selected.  Columns min_v and
    max_v are excluded (redundant with p0 and p100).  All keys are prefixed
    with 'stat_'.

    Returns {} if the section is absent or cannot be parsed.
    """
    in_stats = False
    header_cols: Optional[list[str]] = None
    rows: list[dict[str, str]] = []

    for raw in txt_path.read_text().splitlines():
        line = raw.strip()

        # Section boundary: '--- name ---' lines have spaces; pure dashes don't.
        is_section_marker = (
            line.startswith('---') and line.endswith('---') and ' ' in line
        )
        if is_section_marker:
            if 'statistics' in line.lower():
                in_stats = True
            elif in_stats:
                break           # left the statistics section
            continue

        if not in_stats:
            continue
        if not line or all(c == '-' for c in line):
            continue            # blank line or pure-dash separator row

        if header_cols is None:
            header_cols = line.split()
            continue

        parts = line.split()
        if parts and len(parts) == len(header_cols):
            rows.append(dict(zip(header_cols, parts)))

    if not rows or header_cols is None:
        return {}

    try:
        best = max(rows, key=lambda r: float(r.get('p100', float('-inf'))))
    except ValueError:
        return {}

    return {f'stat_{k}': v for k, v in best.items() if k not in _STAT_SKIP}


# ---------------------------------------------------------------------------
# Txt file parsing — picamera metadata section
# ---------------------------------------------------------------------------

def _picamera_fields(sections: dict[str, dict[str, str]]) -> dict[str, str]:
    """Extract FocusFoM, Lux, ColourTemperature from the picamera metadata section.

    Matches any section whose name contains 'picamera' or ends with '_metadata'
    to handle both picamera2_metadata and picamera3_metadata.
    """
    meta: dict[str, str] = {}
    for name, content in sections.items():
        if 'picamera' in name or name.endswith('_metadata'):
            meta = content
            break
    return {k: meta[k] for k in _PICAM_WANT if k in meta}


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

def snr_one_image(
    file_basename: str,
    dark_basename: str = _DARK_BASENAME,
    rects: list = rects_layer,
) -> dict:
    """Compute per-rect, per-channel SNR for one image.

    Returns a flat row dict ready for DB insertion.
    """
    _, npy_path, txt_path = resolve_paths(file_basename)
    if npy_path is None:
        raise FileNotFoundError(f"No .npy file for '{file_basename}'")

    bayer   = np.load(str(npy_path))
    pattern = _read_bayer_pattern(txt_path)

    _, dark_npy, dark_txt = resolve_paths(dark_basename)
    if dark_npy is None:
        raise FileNotFoundError(f"No .npy file for dark frame '{dark_basename}'")

    dark         = np.load(str(dark_npy))
    dark_pattern = _read_bayer_pattern(dark_txt)

    layers      = BayerCam.extract_bayer_layers(bayer, pattern)
    dark_layers = BayerCam.extract_bayer_layers(dark, dark_pattern)
    bl = {ch: float(np.median(dark_layers[ch])) for ch in _CHANNELS}

    sections = _parse_txt_sections(txt_path) if txt_path else {}

    # Build the flat row.  Order: identifier → txt sections → SNR columns.
    row: dict = {'file_basename': file_basename}

    for sec in ('header', 'setup', 'camera', 'lights'):
        row.update(sections.get(sec, {}))

    if txt_path:
        row.update(_parse_statistics(txt_path))

    row.update(_picamera_fields(sections))

    # SNR per channel and rect
    for ch in _CHANNELS:
        layer = layers[ch]
        for n, rect in enumerate(rects):
            x1, y1, x2, y2 = rect
            crop = layer[y1:y2, x1:x2]
            snr_db, mean_raw = _poly2_snr(crop, bl[ch])
            col = f'{ch}rect{n}'
            row[f'{col}db']   = f'{snr_db:.2f}' if not np.isnan(snr_db) else 'nan'
            row[f'{col}mean'] = f'{mean_raw:.1f}'

    return row


# ---------------------------------------------------------------------------
# SQLite output
# ---------------------------------------------------------------------------

def _init_db(conn: sqlite3.Connection) -> None:
    """Create ledtrials table if absent."""
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            file_basename TEXT UNIQUE
        )
    """)
    conn.commit()


def _ensure_columns(conn: sqlite3.Connection, row: dict) -> None:
    """ALTER TABLE to add any columns present in row but absent in ledtrials."""
    cur = conn.execute(f"PRAGMA table_info({_TABLE})")
    existing = {r[1] for r in cur.fetchall()}
    for col in row:
        if col not in existing:
            conn.execute(f'ALTER TABLE {_TABLE} ADD COLUMN "{col}" TEXT')


def upsert_to_db(db_path: Path, row: dict) -> None:
    """INSERT OR REPLACE the row into ledtrials, adding columns as needed."""
    with sqlite3.connect(db_path) as conn:
        _init_db(conn)
        _ensure_columns(conn, row)
        cols         = ', '.join(f'"{k}"' for k in row)
        placeholders = ', '.join('?' for _ in row)
        conn.execute(
            f'INSERT OR REPLACE INTO {_TABLE} ({cols}) VALUES ({placeholders})',
            list(row.values()),
        )
        conn.commit()
    print(f"  → {db_path.name}  (ledtrials: {row.get('file_basename')})")


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------

def print_snr_table(row: dict) -> None:
    """Print SNR results as a readable grid."""
    exp  = row.get('actual_ms', row.get('exposure_ms', '?'))
    leds = row.get('active_leds', '?')
    print(f"\n{row.get('file_basename')}  "
          f"model={row.get('camera_model', '?')}  "
          f"exp={exp} ms  leds={leds}")

    col_w  = 20
    header = f"{'Rect':>4}  " + '  '.join(f"{ch:<{col_w}}" for ch in _CHANNELS)
    print(header)
    print('-' * len(header))
    for n in range(len(rects_layer)):
        parts = []
        for ch in _CHANNELS:
            col  = f'{ch}rect{n}'
            db   = row.get(f'{col}db',   '?')
            mean = row.get(f'{col}mean', '?')
            parts.append(f'{db:>6} dB ({mean:>6})')
        print(f"  {n:2d}   " + '  '.join(parts))

# ---------------------------------------------------------------------------
# Analysis / plotting  (sqlite3 + numpy + matplotlib, no pandas)
# ---------------------------------------------------------------------------

def _load_trials(db_path: Path = DB_PATH) -> dict:
    """Load ledtrials into a dict of numpy arrays; add exp_frac, exp_target, and per-rect expfrac.

    Returns {} if the table is empty or missing required columns.
    exp_frac          = (stat_p100 - 63) / 960   (normalised whole-image fill level)
    exp_target        = nearest value in _EXP_TARGETS
    {ch}rect{n}expfrac = ({ch}rect{n}mean - 63) / 960  (per-rect fill level)
    """
    RCHANNELS = [''] + _CHANNELS
    snr_cols  = [f'{ch}rect{n}db'   for ch in RCHANNELS for n in range(5)]
    mean_cols = [f'{ch}rect{n}mean' for ch in RCHANNELS for n in range(5)]
    fexp_cols = [f'rect{n}fexp' for n in range(5)]
    want = ['led_desc', 'r_ohm', 'stat_p100', 'exp_frac', 'exp_target'] + snr_cols + mean_cols

    with sqlite3.connect(db_path) as conn:
        existing = {r[1] for r in conn.execute(f'PRAGMA table_info({_TABLE})')}
        cols = [c for c in want if c in existing]
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {_TABLE} "
            f"WHERE led_desc IS NOT NULL AND r_ohm IS NOT NULL"
        ).fetchall()

    if not rows:
        return {}

    data: dict = {c: [] for c in cols}
    for row in rows:
        for i, c in enumerate(cols):
            data[c].append(row[i])

    for c in cols:
        if c == 'led_desc':
            data[c] = np.array(data[c], dtype=object)
        else:
            data[c] = np.array(
                [float(v) if v is not None else np.nan for v in data[c]]
            )

    # p100 = data.get('stat_p100', np.full(len(data['led_desc']), np.nan))
    # data['exp_frac']   = np.round((p100 - 63.0) / (1023.0 - 63.0), 4)
    # data['exp_target'] = np.array([
    #     float(_EXP_TARGETS[np.argmin(np.abs(_EXP_TARGETS - ef))])
    #     if not np.isnan(ef) else np.nan
    #     for ef in data['exp_frac']
    # ])

    for ch in _CHANNELS:
        for n in range(5):
            mean_col = f'{ch}rect{n}mean'
            expfrac_col = f'{ch}rect{n}expfrac'
            if mean_col in data:
                data[expfrac_col] = np.round((data[mean_col] - 63.0) / (1023.0 - 63.0), 4)
            else:
                data[expfrac_col] = np.full(len(data['led_desc']), np.nan)

    return data


def _pivot(data: dict, led: str, col: str,
           r_vals: np.ndarray, t_vals: np.ndarray) -> np.ndarray:
    """Build (n_r × n_t) grid of mean SNR for one LED and one column.

    Rows = r_vals ascending, columns = t_vals ascending.  Missing = NaN.
    """
    grid = np.full((len(r_vals), len(t_vals)), np.nan)
    if col not in data:
        return grid
    mask_led = data['led_desc'] == led
    for i, r in enumerate(r_vals):
        mask_r = np.abs(data['r_ohm'] - r) < 1.0
        for j, t in enumerate(t_vals):
            mask_t = np.abs(data['exp_target'] - t) < 1e-6
            sel = mask_led & mask_r & mask_t
            if sel.any():
                grid[i, j] = float(np.nanmean(data[col][sel]))
    return grid


def _bilinear(z: np.ndarray, ny: int = 80, nx: int = 80) -> np.ndarray:
    """Bilinear interpolation of a 2-D array to ny×nx (pure numpy)."""
    H, W = z.shape
    yf, xf = np.meshgrid(
        np.linspace(0, H - 1, ny),
        np.linspace(0, W - 1, nx),
        indexing='ij',
    )
    y0 = np.clip(yf.astype(int), 0, H - 2)
    x0 = np.clip(xf.astype(int), 0, W - 2)
    dy, dx = yf - y0, xf - x0
    return ((1-dy)*(1-dx)*z[y0,   x0  ] + (1-dy)*dx*z[y0,   x0+1] +
                dy*(1-dx)*z[y0+1, x0  ] +     dy*dx*z[y0+1, x0+1])


def contour_plot(fig, ax, x, y, z, s=0.0, *, grid_res=40, levels=8, cmap="viridis",
                 title="Contour Plot", figsize=(7, 5)):
    """
    Create a filled contour plot from scattered (x, y, z) data.
 
    Parameters
    ----------
    fig : matplotlib figure object
    ax : matplotlib axis object
    x : array-like, shape (N,)
        Randomly spaced x-coordinates of the sample points.
    y : array-like, shape (N,)
        Randomly spaced y-coordinates of the sample points.
    z : array-like, shape (N,)
        Function values z(x, y) at each sample point.
    s : float, optional (default 0.0)
        Smoothing factor.
          s = 0  → no smoothing; exact RBF interpolation through data points.
          s > 0  → smoothed surface; larger values produce broader smoothing.
                   Internally, s is used as the standard deviation (in grid
                   cells) for a Gaussian filter applied to the interpolated
                   grid.  Typical useful range: 0 – 10.
    grid_res : int, optional (default 40)
        Number of points along each axis of the regular interpolation grid.
    levels : int or array-like, optional (default 15)
        Number of contour levels, or an explicit list of level values.
    cmap : str, optional (default "viridis")
        Matplotlib colormap name.
    title : str, optional
        Plot title.
    figsize : tuple, optional
        Figure size passed to plt.figure().
 
    Returns
    -------
    fig, ax : matplotlib Figure and Axes objects.
 
    Examples
    --------
    >>> rng = np.random.default_rng(42)
    >>> x = rng.uniform(-3, 3, 300)
    >>> y = rng.uniform(-3, 3, 300)
    >>> z = np.sin(x) * np.cos(y)
    >>> fig, ax = contour_plot(x, y, z, s=0)    # raw interpolation
    >>> fig, ax = contour_plot(x, y, z, s=3)    # smoothed
    >>> plt.show()
    """
    x, y, z = np.asarray(x, float), np.asarray(y, float), np.asarray(z, float)
    if not (x.shape == y.shape == z.shape and x.ndim == 1):
        raise ValueError("x, y, and z must be 1-D arrays of equal length.")
    if s < 0:
        raise ValueError("Smoothing factor s must be >= 0.")
 
    # ── 1. Build a regular grid spanning the data extent ─────────────────────
    xi = np.linspace(x.min(), x.max(), grid_res)
    yi = np.linspace(y.min(), y.max(), grid_res)
    Xi, Yi = np.meshgrid(xi, yi)                    # shape (grid_res, grid_res)
 
    # ── 2. Interpolate scattered data onto the grid via RBF ──────────────────
    #   RBFInterpolator expects an (N, 2) array of coordinates.
    interp = RBFInterpolator(
        np.column_stack([x, y]),
        z,
        kernel="thin_plate_spline",   # smooth, well-behaved for scattered data
        smoothing=0.0,                # RBF passes through data points exactly
    )
    Zi = interp(np.column_stack([Xi.ravel(), Yi.ravel()])).reshape(Xi.shape)
 
    # ── 3. Optional post-interpolation Gaussian smoothing ────────────────────
    if s > 0:
        Zi = gaussian_filter(Zi, sigma=s)
 
    # # ── 4. Plot ───────────────────────────────────────────────────────────────
    # fig, ax = plt.subplots(figsize=figsize)
 
    cf = ax.contourf(Xi, Yi, Zi, levels=levels, cmap=cmap)
    cs = ax.contour(Xi, Yi, Zi, levels=levels, colors="white",
                    linewidths=0.4, alpha=0.5)
    ax.clabel(cs, inline=True, fontsize=7, fmt="%.2g")
 
    cbar = fig.colorbar(cf, ax=ax, pad=0.02)
    cbar.set_label("z", rotation=0, labelpad=10)
 
    # Scatter the original sample points for reference
    ax.scatter(x, y, c="white", s=6, alpha=0.4, linewidths=0, zorder=5)
 
    # ax.set_xlabel("x")
    # ax.set_ylabel("y")
    # smooth_label = f"  (s={s})" if s > 0 else "  (no smoothing)"
    # ax.set_title(title + smooth_label)
 
    # fig.tight_layout()
    # return fig, ax

def plot_snr_on_mean_vs_fexp():
    pass
    
def plot_snr_lines(
    data,
    channel:  str  = 'B',
    rect_idx: int  = 0,
    db_path:  Path = DB_PATH,
) -> None:
    """Line plot: SNR (dB) vs exposure target, one line per resistor, faceted by LED.

    Y-axis is auto-scaled tightly per panel so small trends are legible.
    Call with different channel / rect_idx values to explore all combinations.
    """

    # data = _load_trials(db_path)
    if not data:
        print('No data in ledtrials.')
        return

    col      = f'{channel}rect{rect_idx}db'
    led_list = sorted(set(data['led_desc']))
    r_vals   = np.sort(np.unique(data['r_ohm'][~np.isnan(data['r_ohm'])]))
    t_vals   = np.sort(_EXP_TARGETS)
    palette  = matplotlib.colormaps['plasma'](np.linspace(0.1, 0.85, len(r_vals)))
    markers  = ['o', 's', '^', 'D'][:len(r_vals)]

    ncols = 2
    nrows = int(np.ceil(len(led_list) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(11, 4 * nrows))
    axes = np.asarray(axes).ravel()

    for ax, led in zip(axes, led_list):
        grid = _pivot(data, led, col, r_vals, t_vals)  # (n_r, n_t)
        for i, (r, color, marker) in enumerate(zip(r_vals, palette, markers)):
            row_vals = grid[i]
            valid = ~np.isnan(row_vals)
            if not valid.any():
                continue
            ax.plot(t_vals[valid], row_vals[valid],
                    color=color, marker=marker, markersize=6, linewidth=1.5,
                    label=f'{r:.0f} Ω')

        panel_snr = grid[~np.isnan(grid)]
        if panel_snr.size:
            pad = max((panel_snr.max() - panel_snr.min()) * 0.2, 0.5)
            ax.set_ylim(panel_snr.min() - pad, panel_snr.max() + pad)

        ax.set_title(led, fontsize=11)
        ax.set_xlabel('Exposure fraction')
        ax.set_ylabel(f'SNR  (dB)')
        ax.set_xticks(t_vals)
        ax.legend(title='r_ohm', fontsize=8, loc='best')
        ax.grid(True, alpha=0.3)

    for ax in axes[len(led_list):]:
        ax.set_visible(False)

    fig.suptitle(
        f'SNR vs exposure — channel {channel}  rect {rect_idx}', fontsize=12
    )
    plt.tight_layout()
    plt.savefig(f'/home/bill/R/SNR_lines{channel}.png')
    plt.show()


def plot_snr_heatmap(
    data,
    channel:  str  = 'B',
    rect_idx: int  = 0,
    db_path:  Path = DB_PATH,
) -> None:
    """Contour/heatmap: SNR as a function of r_ohm × exposure target, faceted by LED.

    The 4×5 measured grid is bilinearly interpolated for smooth display.
    Contour lines and measured values are annotated over the colour image.
    A single colour scale spans all four LED panels for direct comparison.
    """
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize

    # data = _load_trials(db_path)
    if not data:
        print('No data in ledtrials.')
        return

    col      = f'{channel}rect{rect_idx}db'
    led_list = sorted(set(data['led_desc']))
    r_vals   = np.sort(np.unique(data['r_ohm'][~np.isnan(data['r_ohm'])]))
    t_vals   = np.sort(_EXP_TARGETS)
    n_r, n_t = len(r_vals), len(t_vals)

    all_snr = data[col][~np.isnan(data[col])] if col in data else np.array([])
    vmin = float(all_snr.min()) if all_snr.size else 0.0
    vmax = float(all_snr.max()) if all_snr.size else 1.0
    norm = Normalize(vmin=vmin, vmax=vmax)
    mid  = (vmin + vmax) / 2

    ncols = 2
    nrows = int(np.ceil(len(led_list) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(11, 4.5 * nrows))
    axes = np.asarray(axes).ravel()

    for ax, led in zip(axes, led_list):
        grid = _pivot(data, led, col, r_vals, t_vals)  # (n_r, n_t)

        if np.all(np.isnan(grid)):
            ax.set_title(f'{led}  (no data)')
            continue

        # Smooth background via bilinear interpolation
        fine = _bilinear(grid, ny=80, nx=80)
        ax.imshow(
            fine,
            origin='lower', aspect='auto',
            extent=[-0.5, n_t - 0.5, -0.5, n_r - 0.5],
            cmap='viridis', norm=norm, interpolation='bilinear',
        )

        # Contour lines on the fine grid
        cx = np.linspace(-0.5, n_t - 0.5, 80)
        cy = np.linspace(-0.5, n_r - 0.5, 80)
        cX, cY = np.meshgrid(cx, cy)
        if vmax > vmin:
            try:
                cs = ax.contour(cX, cY, fine, levels=5,
                                colors='white', alpha=0.5, linewidths=0.8)
                ax.clabel(cs, fmt='%.1f', fontsize=6, inline=True)
            except Exception:
                pass

        # Measured value annotations
        for i in range(n_r):
            for j in range(n_t):
                v = grid[i, j]
                if not np.isnan(v):
                    ax.text(j, i, f'{v:.1f}', ha='center', va='center',
                            fontsize=8, fontweight='bold',
                            color='white' if v < mid else 'black')

        ax.set_xticks(range(n_t))
        ax.set_xticklabels([f'{t:.2f}' for t in t_vals])
        ax.set_yticks(range(n_r))
        ax.set_yticklabels([f'{r:.0f}' for r in r_vals])
        ax.set_xlabel('Exposure fraction')
        ax.set_ylabel('r_ohm (Ω)')
        ax.set_title(led, fontsize=11)

    sm = plt.cm.ScalarMappable(cmap='viridis', norm=norm)
    sm.set_array([])
    fig.colorbar(sm, ax=axes[:len(led_list)].tolist(), label=f'{col}  (dB)', shrink=0.6)

    for ax in axes[len(led_list):]:
        ax.set_visible(False)

    fig.suptitle(
        f'SNR heatmap — channel {channel}  rect {rect_idx}', fontsize=12
    )
    plt.tight_layout()
    plt.savefig(f'/home/bill/R/SNR_heatmap{channel}.png')
    plt.show()

def plot_snr_heatmap2(
    data,
    channel:  str  = 'B',
    rect_idx: int  = 0,
    db_path:  Path = DB_PATH,
) -> None:
    """Contour/heatmap: SNR as a function of rect_mean x rect_fexp, faceted by LED.

    The 4×5 measured grid is bilinearly interpolated for smooth display.
    Contour lines and measured values are annotated over the colour image.
    A single colour scale spans all four LED panels for direct comparison.
    """
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize

    # data = _load_trials(db_path)
    if not data:
        print('No data in ledtrials.')
        return

    col      = f'{channel}rect{rect_idx}db'
    led_list = sorted(set(data['led_desc']))
    # r_vals   = np.sort(np.unique(data['r_ohm'][~np.isnan(data['r_ohm'])]))
    # t_vals   = np.sort(_EXP_TARGETS)
    # n_r, n_t = len(r_vals), len(t_vals)

    all_snr = data[col][~np.isnan(data[col])] if col in data else np.array([])
    vmin = float(all_snr.min()) if all_snr.size else 0.0
    vmax = float(all_snr.max()) if all_snr.size else 1.0
    norm = Normalize(vmin=vmin, vmax=vmax)
    mid  = (vmin + vmax) / 2

    ncols = 2
    nrows = int(np.ceil(len(led_list) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(11, 4.5 * nrows))
    axes = np.asarray(axes).ravel()

    for ax, led in zip(axes, led_list):
        grid = _pivot(data, led, col, r_vals, t_vals)  # (n_r, n_t)

        if np.all(np.isnan(grid)):
            ax.set_title(f'{led}  (no data)')
            continue

        # Smooth background via bilinear interpolation
        fine = _bilinear(grid, ny=80, nx=80)
        ax.imshow(
            fine,
            origin='lower', aspect='auto',
            extent=[-0.5, n_t - 0.5, -0.5, n_r - 0.5],
            cmap='viridis', norm=norm, interpolation='bilinear',
        )

        # Contour lines on the fine grid
        cx = np.linspace(-0.5, n_t - 0.5, 80)
        cy = np.linspace(-0.5, n_r - 0.5, 80)
        cX, cY = np.meshgrid(cx, cy)
        if vmax > vmin:
            try:
                cs = ax.contour(cX, cY, fine, levels=5,
                                colors='white', alpha=0.5, linewidths=0.8)
                ax.clabel(cs, fmt='%.1f', fontsize=6, inline=True)
            except Exception:
                pass

        # Measured value annotations
        for i in range(n_r):
            for j in range(n_t):
                v = grid[i, j]
                if not np.isnan(v):
                    ax.text(j, i, f'{v:.1f}', ha='center', va='center',
                            fontsize=8, fontweight='bold',
                            color='white' if v < mid else 'black')

        ax.set_xticks(range(n_t))
        ax.set_xticklabels([f'{t:.2f}' for t in t_vals])
        ax.set_yticks(range(n_r))
        ax.set_yticklabels([f'{r:.0f}' for r in r_vals])
        ax.set_xlabel('Exposure fraction')
        ax.set_ylabel('r_ohm (Ω)')
        ax.set_title(led, fontsize=11)

    sm = plt.cm.ScalarMappable(cmap='viridis', norm=norm)
    sm.set_array([])
    fig.colorbar(sm, ax=axes[:len(led_list)].tolist(), label=f'{col}  (dB)', shrink=0.6)

    for ax in axes[len(led_list):]:
        ax.set_visible(False)

    fig.suptitle(
        f'SNR heatmap — channel {channel}  rect {rect_idx}', fontsize=12
    )
    plt.tight_layout()
    plt.savefig(f'/home/bill/R/SNR_heatmap{channel}.png')
    plt.show()


def plot_mean_vs_snr(
    data,
    channel:  str  = 'B',
    rect_idx: int  = 0,
    db_path:  Path = DB_PATH,
) -> None:
    """Scatter: mean pixel value vs SNR for all (LED, r_ohm, exposure) combinations.

    Each point is one image.  Colour = LED colour; marker shape = r_ohm.
    As exposure increases, points move right (higher mean) and typically up
    (higher SNR); the r_ohm lines show whether resistor choice shifts the curve.
    """
    import matplotlib
    import matplotlib.pyplot as plt

    # data = _load_trials(db_path)
    if not data:
        print('No data in ledtrials.')
        return

    col_db   = f'{channel}rect{rect_idx}db'
    col_mean = f'{channel}rect{rect_idx}mean'

    if col_db not in data or col_mean not in data:
        print(f"Columns '{col_db}' or '{col_mean}' not in database.")
        return

    led_list = sorted(set(data['led_desc']))
    r_vals   = np.sort(np.unique(data['r_ohm'][~np.isnan(data['r_ohm'])]))
    palette  = matplotlib.colormaps['tab10'](np.linspace(0, 0.5, len(led_list)))
    markers  = ['o', 's', '^', 'D'][:len(r_vals)]

    ax = plt.subplots(figsize=(9, 6))[1]

    for led, color in zip(led_list, palette):
        for r, marker in zip(r_vals, markers):
            mask  = (data['led_desc'] == led) & (np.abs(data['r_ohm'] - r) < 1.0)
            xv    = data[col_mean][mask]
            yv    = data[col_db][mask]
            valid = ~np.isnan(xv) & ~np.isnan(yv)
            if not valid.any():
                continue
            # Sort by exposure so connecting lines follow the exposure ramp
            order = np.argsort(xv[valid])
            ax.plot(xv[valid][order], yv[valid][order],
                    color=color, marker=marker, markersize=6,
                    linewidth=0.8, alpha=0.85, label=f'{led}  {r:.0f}Ω')

    ax.set_xlabel(f'Mean pixel value  ({col_mean})')
    ax.set_ylabel(f'SNR  (dB)')
    ax.set_title(
        f'Mean pixel vs SNR — channel {channel}  rect {rect_idx}\n'
        f'colour = LED,  marker = r_ohm'
    )
    ax.legend(fontsize=7, ncol=2, loc='best')
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(f'/home/bill/R/mean_vs_SNR{channel}.png')
    plt.show()


def plot_snr_vs_rect_expfrac(
    data,
    channel: str  = 'B',
    db_path: Path = DB_PATH,
) -> None:
    """Scatter SNR vs per-rect exposure fraction, faceted by LED color.

    Each point is one (image, rect) pair.  The per-rect exposure fraction
    measures the local illumination level independently of the overall camera
    exposure setting, so points from different rects, resistors, and exposure
    targets that happen to share the same local illumination level land at the
    same x-position.  If the resulting curves collapse across rects and
    resistors, SNR is driven purely by local illumination, not by the
    electrical settings that produced it.

    X-axis  : {channel}rect{n}expfrac = (rect_mean - 63) / 960
    Y-axis  : {channel}rect{n}db  (SNR in dB)
    Colour  : rect index (0–4)
    Marker  : r_ohm (4 shapes)
    Facets  : one subplot per LED colour
    """
    import matplotlib
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.lines as mlines

    # data = _load_trials(db_path)
    if not data:
        print('No data in ledtrials.')
        return

    led_list    = sorted(set(data['led_desc']))
    r_vals      = np.sort(np.unique(data['r_ohm'][~np.isnan(data['r_ohm'])]))
    rect_colors = matplotlib.colormaps['tab10'](np.linspace(0.0, 0.45, 5))
    markers     = ['o', 's', '^', 'D'][:len(r_vals)]

    ncols = 2
    nrows = int(np.ceil(len(led_list) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(11, 4.5 * nrows))
    axes = np.asarray(axes).ravel()

    for ax, led in zip(axes, led_list):
        mask_led = data['led_desc'] == led

        for n in range(5):
            expfrac_col = f'{channel}rect{n}expfrac'
            db_col      = f'{channel}rect{n}db'
            if expfrac_col not in data or db_col not in data:
                continue

            for r, marker in zip(r_vals, markers):
                mask  = mask_led & (np.abs(data['r_ohm'] - r) < 1.0)
                xv    = data[expfrac_col][mask]
                yv    = data[db_col][mask]
                valid = ~np.isnan(xv) & ~np.isnan(yv)
                if not valid.any():
                    continue
                ax.scatter(xv[valid], yv[valid],
                           color=rect_colors[n], marker=marker, s=40,
                           alpha=0.85, linewidths=0.5, edgecolors='k')

        ax.set_xlabel('Per-rect exposure fraction')
        ax.set_ylabel('SNR (dB)')
        ax.set_title(led, fontsize=11)
        ax.grid(True, alpha=0.3)

    # Build figure-level legend: rect colours + r_ohm marker shapes
    rect_handles = [
        mpatches.Patch(color=rect_colors[n], label=f'rect {n}')
        for n in range(5)
    ]
    r_handles = [
        mlines.Line2D([], [], color='gray', marker=m, linestyle='None',
                      markersize=7, label=f'{r:.0f} Ω')
        for r, m in zip(r_vals, markers)
    ]
    fig.legend(handles=rect_handles + r_handles,
               fontsize=8, ncol=5, loc='lower center',
               bbox_to_anchor=(0.5, 0.0), title='rect index / r_ohm',
               title_fontsize=8)

    for ax in axes[len(led_list):]:
        ax.set_visible(False)

    fig.suptitle(
        f'SNR vs per-rect exposure fraction — channel {channel}\n'
        f'colour = rect index,  marker shape = r_ohm',
        fontsize=12,
    )
    plt.tight_layout(rect=(0, 0.08, 1, 1))
    plt.savefig(f'/home/bill/R/snr_vs_rect_expfrac{channel}.png')
    plt.show()


def list_files_in_range(dirpath, istart, iend):
    import os, re
    results = []
    for fname in os.listdir(dirpath):
        if not fname.endswith('.npy'):
            continue
        m = re.search(r'(\d{4})\.npy$', fname)
        if m and istart <= int(m.group(1)) <= iend:
            results.append(os.path.splitext(fname)[0])
    return results

def read_files(numfrom, numto=1000):
    dark_basename='Tst6BlackBP0_B61_0040'
    file_basenames = [
        "",   # ← set basenames here; blank = most recent file in UPLOAD_DIR
    ]
    file_basenames = list_files_in_range('/home/upload/picam', numfrom, numto)

    for basename in file_basenames:
        row = snr_one_image(basename, dark_basename=dark_basename)
        print_snr_table(row)
        upsert_to_db(DB_PATH, row)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # read_files(43, 132)
    data = _load_trials(DB_PATH)
    for channel in ['','B','Gb','Gr','R']:
        plot_snr_heatmap(data, channel, rect_idx=0)
        plot_snr_lines(data, channel, rect_idx=2)
        plot_mean_vs_snr(data, channel, rect_idx=0)
        plot_snr_vs_rect_expfrac(data, channel)

