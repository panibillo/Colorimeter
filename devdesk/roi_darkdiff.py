"""
roi_darkdiff.py — dark before/after diff stats over hardcoded ROI rects.
--------------------------------------------------------------------------
Desktop-only developer tool. Not synced to the Pi (see sync_pi_files.txt).

Knows about four fixed regions of interest, defined in pixel coordinates
(x, y, w, h) relative to the per-channel ROI layer arrays stored under
/dark/<when>_<us>us/<ch> (typically 315x98 or 330x98, rows x cols):

    dgray   x,y,w,h = 24, 100, 34, 34
    mgray   x,y,w,h = 64, 200, 34, 34
    sampa   x,y,w,h = 24, 166, 34, 34
    sampb   x,y,w,h = 24, 228, 34, 34

-d / --dark_diff: for each distinct dark exposure seen under /dark (the
union of before_<us>us and after_<us>us group names) — if both a before
and after group exist for that exposure, for each of the four ROI rects
and each Bayer channel (B, Gb, Gr, R), compute diff = after - before and
print min/max/median/stdev, plus the difference between the two groups'
recorded exposure_us (expected to be 0 by construction; included as a
sanity check on the schema, not an assumption). If an exposure has only
a before or only an after group (a calibration-path run takes only
"after"), one blank row is printed for that exposure instead of guessing.

-e / --exposures: ignores the ROI rects and diffs the *entire* array
(all channels aggregated into one distribution per exposure). For each
matched before/after exposure pair, plots the pixel-count histogram of
diff values as a line (x = diff value, y = pixel count at that value),
one line per exposure, colored along the 'jet' colormap by exposure_us
(longer exposures further along the colormap). Meant to visualize dark
current drift and its sensitivity to exposure length.

-p / --pixel_by_pixel: asks whether individual pixels drift consistently
in one direction across the N matched exposure pairs, or randomly.
Treats the N pairs as repeated trials per pixel. Per Bayer channel (kept
separate — B/Gb/Gr/R are physically distinct photosites), computes the
per-pixel mean diff across all N pairs (mean_map) and compares its
pixel-to-pixel spread to what pure random per-trial noise would predict
after averaging N trials (single-pair spread / sqrt(N)). A ratio near 1
means the drift looks random; well above 1 means some pixels carry a
persistent bias that survives averaging. Plots mean_map as a 2x2 grid
of heatmaps (one per channel), diverging colormap centered on zero, so
any spatially consistent hot/cold pixels are visible directly.

Usage:
    python3 devdesk/roi_darkdiff.py FILE [-d] [-e] [-p]

Examples:
    python3 devdesk/roi_darkdiff.py 20260802_2034.h5 -d
    python3 devdesk/roi_darkdiff.py 20260802_2034.h5 -e
    python3 devdesk/roi_darkdiff.py 20260802_2034.h5 -p
"""

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hdf5read import Hdf5Reader  # noqa: E402

DEFAULT_CAPTURES_DIR = Path(__file__).resolve().parent.parent / "captures"
CHANNELS = ("B", "Gb", "Gr", "R")

RECTS = {
    "dgray": (24, 100, 34, 34),
    "mgray": (64, 200, 34, 34),
    "sampa": (24, 166, 34, 34),
    "sampb": (24, 228, 34, 34),
}

BLANK = "-"


# ── Path resolution (mirrors devdesk/hdf5inspect.py) ────────────────

def resolve_h5_path(filename: str, captures_dir: Path) -> Path:
    """
    Resolve a user-supplied filename/path to an existing .h5 file.

    Args:
        filename: Bare filename (resolved against captures_dir) or a
            relative/absolute path used as-is if it exists.
        captures_dir: Directory bare filenames resolve against.

    Returns:
        Resolved, existing Path to the .h5 file.

    Raises:
        FileNotFoundError: Neither interpretation resolves to a file.
    """
    direct = Path(filename)
    if direct.exists():
        return direct.resolve()

    candidate = captures_dir / filename
    if candidate.exists():
        return candidate.resolve()

    raise FileNotFoundError(
        f"'{filename}' not found as a path, nor as '{candidate}' "
        f"under captures dir '{captures_dir}'"
    )


# ── ROI cropping ─────────────────────────────────────────────────────

def crop_rect(array: np.ndarray, rect: tuple) -> np.ndarray:
    """
    Crop array[y:y+h, x:x+w] for a hardcoded (x, y, w, h) rect.

    Args:
        array: 2-D (rows, cols) channel-layer array.
        rect: (x, y, w, h) in column/row pixel coordinates.

    Returns:
        The cropped 2-D view.

    Raises:
        ValueError: The rect does not fit within array's shape.
    """
    x, y, w, h = rect
    rows, cols = array.shape
    if y + h > rows or x + w > cols:
        raise ValueError(
            f"rect (x={x},y={y},w={w},h={h}) exceeds array shape {array.shape}"
        )
    return array[y:y + h, x:x + w]


# ── Dark exposure discovery ──────────────────────────────────────────

def list_dark_exposures(reader: Hdf5Reader) -> list:
    """
    Return sorted [(exposure_us, before_group_or_None, after_group_or_None), ...]
    for every exposure seen under /dark with the before_<us>us / after_<us>us
    naming (the union of both sides — an exposure missing one side is still
    included, with None in that slot). Groups using the legacy camtest8
    naming (no before/after split) are ignored entirely.
    """
    try:
        names = reader.list_children("/dark")
    except KeyError:
        return []

    before_us, after_us = {}, {}
    for name in names:
        when, _, us_part = name.partition("_")
        if when not in ("before", "after") or not us_part.endswith("us"):
            continue
        us = int(us_part[:-2])
        (before_us if when == "before" else after_us)[us] = name

    all_us = sorted(set(before_us) | set(after_us))
    return [(us, before_us.get(us), after_us.get(us)) for us in all_us]


# ── Mode 1: dark before/after diff ──────────────────────────────────

def dark_diff_rows(
    reader: Hdf5Reader, exposure_us: int, before_group: str, after_group: str,
) -> list:
    """Return one formatted row dict per rect/channel for a matched pair."""
    rows = []
    for rect_name, rect in RECTS.items():
        for channel in CHANNELS:
            before_path = f"/dark/{before_group}/{channel}"
            after_path = f"/dark/{after_group}/{channel}"

            before = crop_rect(
                reader.read_array(before_path).astype(np.int32), rect
            )
            after = crop_rect(
                reader.read_array(after_path).astype(np.int32), rect
            )
            diff = after - before

            before_us = reader.attrs(before_path).get("exposure_us")
            after_us = reader.attrs(after_path).get("exposure_us")
            delta_us = (
                str(after_us - before_us)
                if before_us is not None and after_us is not None else BLANK
            )

            rows.append({
                "exposure_us": str(exposure_us),
                "rect": rect_name,
                "layer": channel,
                "min": str(int(diff.min())),
                "max": str(int(diff.max())),
                "median": f"{np.median(diff):.1f}",
                "stdev": f"{np.std(diff):.2f}",
                "delta_us": delta_us,
            })
    return rows


def blank_row(exposure_us: int) -> dict:
    """One blank row for an exposure missing its before or after group."""
    return {
        "exposure_us": str(exposure_us),
        "rect": BLANK,
        "layer": BLANK,
        "min": BLANK,
        "max": BLANK,
        "median": BLANK,
        "stdev": BLANK,
        "delta_us": BLANK,
    }


def cmd_dark_diff(reader: Hdf5Reader, h5_path: Path) -> None:
    """Print after-before dark diff stats for every exposure/rect/channel."""
    exposures = list_dark_exposures(reader)
    print(f"Dark before/after diff: {h5_path.name}")
    if not exposures:
        print("  (no dark exposures found using the before/after naming)")
        return

    rows = []
    for exposure_us, before_group, after_group in exposures:
        if before_group is None or after_group is None:
            rows.append(blank_row(exposure_us))
        else:
            rows.extend(dark_diff_rows(reader, exposure_us, before_group, after_group))

    header = (
        f"{'exposure_us':>12}  {'rect':<7}{'Bayer_Layer':<13}"
        f"{'min':>6}{'max':>6}{'median':>9}{'stdev':>9}{'delta_us':>10}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['exposure_us']:>12}  {row['rect']:<7}{row['layer']:<13}"
            f"{row['min']:>6}{row['max']:>6}{row['median']:>9}"
            f"{row['stdev']:>9}{row['delta_us']:>10}"
        )


# ── Mode 2: full-array diff distribution by exposure ────────────────

def full_frame_diff_values(
    reader: Hdf5Reader, before_group: str, after_group: str,
) -> np.ndarray:
    """
    Return the flattened after-before diff values for one exposure,
    aggregated across all 4 Bayer channels, over the *entire* stored
    array (no ROI cropping).
    """
    diffs = []
    for channel in CHANNELS:
        before = reader.read_array(f"/dark/{before_group}/{channel}").astype(np.int32)
        after = reader.read_array(f"/dark/{after_group}/{channel}").astype(np.int32)
        diffs.append((after - before).ravel())
    return np.concatenate(diffs)


def cmd_exposures(reader: Hdf5Reader, h5_path: Path) -> None:
    """
    Plot, per matched dark exposure pair, the pixel-count histogram of
    after-before diff values over the full (uncropped) array, all 4
    Bayer channels aggregated into one distribution per exposure. One
    line per exposure, colored along the 'jet' colormap by exposure_us,
    to visualize dark-current drift and its sensitivity to exposure
    length.
    """
    exposures = list_dark_exposures(reader)
    paired = [(us, b, a) for us, b, a in exposures if b and a]
    skipped = [us for us, b, a in exposures if not (b and a)]

    print(f"Exposure diff distributions: {h5_path.name}")
    if not paired:
        print("  (no matched before/after dark pairs found)")
        return
    if skipped:
        print(f"  skipping {len(skipped)} unpaired exposure(s): {skipped}")

    import matplotlib.pyplot as plt  # deferred; only this mode needs it

    us_values = [us for us, _, _ in paired]
    norm = plt.Normalize(vmin=min(us_values), vmax=max(us_values))
    cmap = plt.get_cmap("jet")

    fig, ax = plt.subplots(figsize=(9, 6))
    for us, before_group, after_group in paired:
        combined = full_frame_diff_values(reader, before_group, after_group)
        values, counts = np.unique(combined, return_counts=True)
        ax.plot(values, counts, color=cmap(norm(us)), label=f"{us} us")

    ax.set_xlabel("diff value (after - before)")
    ax.set_ylabel("pixel count")
    ax.set_title(
        f"{h5_path.name}: full-array dark diff distribution by exposure\n"
        f"(all 4 Bayer layers aggregated, ROIs ignored)"
    )
    ax.legend(title="exposure_us", fontsize="small", ncol=2)
    plt.show()


# ── Mode 3: per-pixel drift consistency across exposures ────────────

def channel_diff_stack(
    reader: Hdf5Reader, channel: str, paired: list,
) -> np.ndarray:
    """
    Return the after-before diff arrays for one channel across all N
    matched pairs, stacked as shape (N, H, W) (full array, uncropped).
    """
    return np.stack([
        reader.read_array(f"/dark/{after_group}/{channel}").astype(np.int32)
        - reader.read_array(f"/dark/{before_group}/{channel}").astype(np.int32)
        for _, before_group, after_group in paired
    ])


def pixel_bias_stats(stack: np.ndarray) -> dict:
    """
    Compare the per-pixel mean diff across N trials to what pure random
    per-trial noise predicts after averaging N trials.

    Args:
        stack: Diff arrays, shape (N, H, W) — N repeated trials (here,
            different exposure pairs) over the same pixel grid.

    Returns:
        dict with n, mean_map (H,W), single_pair_std (average per-trial
        pixel-to-pixel spread), expected_std (single_pair_std / sqrt(n),
        i.e. the spread pure random noise would leave after averaging),
        actual_std (the real spread of mean_map), ratio (actual /
        expected — near 1 implies random, >>1 implies persistent
        per-pixel bias), and implied_bias_std (the portion of actual_std
        not explained by residual averaging noise).
    """
    n = stack.shape[0]
    mean_map = stack.mean(axis=0)
    single_pair_std = float(np.mean([np.std(frame) for frame in stack]))
    expected_std = single_pair_std / np.sqrt(n)
    actual_std = float(np.std(mean_map))
    ratio = actual_std / expected_std if expected_std > 0 else float("nan")
    implied_bias_std = float(
        np.sqrt(max(0.0, actual_std ** 2 - expected_std ** 2))
    )
    return {
        "n": n,
        "mean_map": mean_map,
        "single_pair_std": single_pair_std,
        "expected_std": expected_std,
        "actual_std": actual_std,
        "ratio": ratio,
        "implied_bias_std": implied_bias_std,
    }


def cmd_pixel_by_pixel(reader: Hdf5Reader, h5_path: Path) -> None:
    """
    Test whether individual pixels drift consistently in one direction
    across the matched exposure pairs, or randomly, per Bayer channel.
    """
    exposures = list_dark_exposures(reader)
    paired = [(us, b, a) for us, b, a in exposures if b and a]
    skipped = [us for us, b, a in exposures if not (b and a)]

    print(f"Pixel-by-pixel drift consistency: {h5_path.name}")
    if len(paired) < 2:
        print("  (need at least 2 matched before/after pairs; found "
              f"{len(paired)})")
        return
    if skipped:
        print(f"  skipping {len(skipped)} unpaired exposure(s): {skipped}")

    stats_by_channel = {}
    header = (
        f"{'channel':<8}{'n_pairs':>8}{'single_pair_std':>17}"
        f"{'expected_std':>14}{'actual_std':>12}{'ratio':>8}"
        f"{'implied_bias_std':>18}"
    )
    print(header)
    print("-" * len(header))
    for channel in CHANNELS:
        stack = channel_diff_stack(reader, channel, paired)
        stats = pixel_bias_stats(stack)
        stats_by_channel[channel] = stats
        print(
            f"{channel:<8}{stats['n']:>8}{stats['single_pair_std']:>17.3f}"
            f"{stats['expected_std']:>14.3f}{stats['actual_std']:>12.3f}"
            f"{stats['ratio']:>8.2f}{stats['implied_bias_std']:>18.3f}"
        )
    print(
        "  ratio near 1 => drift looks random per pixel; ratio >> 1 "
        "=> some pixels carry a persistent directional bias"
    )

    import matplotlib.pyplot as plt  # deferred; only this mode needs it

    vmax = max(
        np.abs(stats_by_channel[ch]["mean_map"]).max() for ch in CHANNELS
    )
    fig, axes = plt.subplots(2, 2, figsize=(8, 10))
    for ax, channel in zip(axes.ravel(), CHANNELS):
        stats = stats_by_channel[channel]
        im = ax.imshow(
            stats["mean_map"], cmap="coolwarm", vmin=-vmax, vmax=vmax,
            aspect="equal", interpolation="nearest", origin="upper",
        )
        ax.set_title(f"{channel}  ratio={stats['ratio']:.2f}")
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    fig.suptitle(
        f"{h5_path.name}: per-pixel mean diff across {len(paired)} "
        f"exposure pairs (full array, ROIs ignored)"
    )
    fig.tight_layout()
    plt.show()


# ── CLI ──────────────────────────────────────────────────────────────

def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  roi_darkdiff.py 20260802_2034.h5 -d\n"
            "  roi_darkdiff.py 20260802_2034.h5 -e\n"
            "  roi_darkdiff.py 20260802_2034.h5 -p\n"
        ),
    )
    parser.add_argument(
        "filename",
        help="capture-suite .h5 file: bare name (resolved against "
             "--captures-dir) or a path used as-is if it exists",
    )
    parser.add_argument(
        "--captures-dir", default=str(DEFAULT_CAPTURES_DIR), metavar="DIR",
        help="directory bare filenames resolve against (default: %(default)s)",
    )
    parser.add_argument(
        "-d", "--dark_diff", action="store_true",
        help="print before/after dark-frame diff stats per ROI rect/layer",
    )
    parser.add_argument(
        "-e", "--exposures", action="store_true",
        help="plot per-exposure diff-value histograms over the full array "
             "(all layers aggregated, ROIs ignored), colored by "
             "exposure_us using the jet colormap",
    )
    parser.add_argument(
        "-p", "--pixel_by_pixel", action="store_true",
        help="test whether individual pixels drift consistently in one "
             "direction across exposure pairs, or randomly, per channel",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = _parse_args()
    captures_dir = Path(args.captures_dir).expanduser().resolve()

    try:
        h5_path = resolve_h5_path(args.filename, captures_dir)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    run_dark_diff = args.dark_diff
    run_exposures = args.exposures
    run_pixel_by_pixel = args.pixel_by_pixel
    if not (run_dark_diff or run_exposures or run_pixel_by_pixel):
        run_dark_diff = True  # default when no mode flag given

    with Hdf5Reader(str(h5_path)) as reader:
        if run_dark_diff:
            cmd_dark_diff(reader, h5_path)
        if run_exposures:
            cmd_exposures(reader, h5_path)
        if run_pixel_by_pixel:
            cmd_pixel_by_pixel(reader, h5_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
