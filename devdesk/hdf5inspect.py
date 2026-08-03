"""
hdf5inspect.py — quick desktop inspection of a capture-suite HDF5 archive.
--------------------------------------------------------------------------
Desktop-only developer tool (matplotlib dependency). Not synced to the Pi
(see sync_pi_files.txt).

Three focused modes, more compact than Hdf5Reader.summary():

  -s / --summary       Compact table: color_name, e#, exposure_us,
                        jpegsize, roi_shape (one row per lit exposure
                        label present, plus a synthesized row for a jpeg
                        that exists without a matching lit label).
  -j / --jpeg_extract   Extract each color's embedded jpeg_e3 blob to
                        <captures_dir>/<file_stem>/<color>.jpg.
  -r / --roi_plot       Plot the R, Gr, B ROI layers (Gb is skipped) for
                        one LED color + exposure label as a matplotlib
                        image: an RGB reconstruction by default, or a
                        grayscale mean(R,Gr,B) with --gray. -o/--output
                        additionally saves just the image area (no
                        title/axes/colorbar) as a PNG.

If no mode flag is given, -s/--summary runs by default. Modes may be
combined in one invocation.

Usage:
    python3 devdesk/hdf5inspect.py FILE [-s] [-j] \\
        [-r [COLOR] [EXP]] [--gray] [-o PNG]

    NOTE: FILE must come before -r/--roi_plot on the command line, since
    -r accepts 0-2 trailing values and would otherwise swallow FILE too.

Examples:
    python3 devdesk/hdf5inspect.py 20260802_1404.h5 -s
    python3 devdesk/hdf5inspect.py 20260802_1404.h5 -j
    python3 devdesk/hdf5inspect.py 20260802_1404.h5 -r
    python3 devdesk/hdf5inspect.py 20260802_1404.h5 -r white 0
    python3 devdesk/hdf5inspect.py 20260802_1404.h5 -r red_orange 3 --gray
    python3 devdesk/hdf5inspect.py 20260802_1404.h5 -r white 0 -o roi.png
"""

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hdf5read import Hdf5Reader  # noqa: E402

DEFAULT_CAPTURES_DIR = Path(__file__).resolve().parent.parent / "captures"
RAW_MAX = 1023  # 10-bit sensor ceiling, even though stored as uint16

TEST_ROI = {
   'dgray' : (24, 100, 34, 34),   
   'mgray' : (64, 200, 34, 34),   
   'sampa' : (24, 166, 34, 34),   
   'sampb' : (24, 228, 34, 34),   
}
# ── Path resolution ─────────────────────────────────────────────────

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

    Example:
        >>> resolve_h5_path("20260802_1404.h5", DEFAULT_CAPTURES_DIR)
        PosixPath('.../captures/20260802_1404.h5')
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


# ── Schema-introspection helpers (built only on Hdf5Reader's public API) ──

def list_led_colors(reader: Hdf5Reader) -> list:
    """Return sorted LED color names present under /leds (possibly empty)."""
    try:
        return sorted(reader.list_children("/leds"))
    except KeyError:
        return []


def list_lit_labels(reader: Hdf5Reader, color: str) -> list:
    """Return exposure labels (e.g. ['e0','e1',...]) under one LED's /lit."""
    try:
        labels = reader.list_children(f"/leds/{color}/lit")
    except KeyError:
        return []
    return sorted(labels, key=lambda lab: int(lab[1:]))


def has_jpeg(reader: Hdf5Reader, color: str) -> bool:
    """Return True if /leds/<color>/jpeg_e3 exists."""
    try:
        return "jpeg_e3" in reader.list_children(f"/leds/{color}")
    except KeyError:
        return False


def lit_channel_shape(reader: Hdf5Reader, color: str, label: str):
    """Return the (rows, cols) shape shared by all 4 channel datasets."""
    return reader.read_array(f"/leds/{color}/lit/{label}/R").shape


def lit_exposure_us(reader: Hdf5Reader, color: str, label: str) -> int:
    """Return the exposure_us attr for one LED's lit exposure label."""
    return reader.attrs(f"/leds/{color}/lit/{label}/R")["exposure_us"]


# ── Mode 1: summary ─────────────────────────────────────────────────

def summary_rows(reader: Hdf5Reader, color: str) -> list:
    """
    Build (color, label, exposure_us, jpeg_bytes, shape) rows for one color.

    The row set is the union of lit labels present and {"e3"} if a jpeg
    exists, since jpeg_e3 can exist even when lit/e3 itself was never
    captured (seen in real partial-run archives). Missing pieces render
    as "-".
    """
    lit_labels = set(list_lit_labels(reader, color))
    jpeg_present = has_jpeg(reader, color)
    labels = set(lit_labels)
    if jpeg_present:
        labels.add("e3")
    if not labels:
        return [(color, "-", "-", "-", "-")]

    jpeg_bytes = (
        len(reader.read_bytes(f"/leds/{color}/jpeg_e3"))
        if jpeg_present else None
    )

    rows = []
    for label in sorted(labels, key=lambda lab: int(lab[1:])):
        is_lit = label in lit_labels
        if is_lit:
            exp = str(lit_exposure_us(reader, color, label))
            shape = lit_channel_shape(reader, color, label)
            shape_s = f"{shape[0]}x{shape[1]}"
        else:
            exp = "-"
            shape_s = "-"
        jpeg_s = str(jpeg_bytes) if (jpeg_present and label == "e3") else "-"
        rows.append((color, label, exp, jpeg_s, shape_s))
    return rows


def cmd_summary(reader: Hdf5Reader, h5_path: Path) -> None:
    """Print a compact table: color_name, e#, exposure_us, jpegsize, roi_shape."""
    colors = list_led_colors(reader)
    print(f"Summary: {h5_path.name}")
    if not colors:
        print("  (no /leds group found)")
        return

    all_rows = []
    for color in colors:
        all_rows.extend(summary_rows(reader, color))

    col_w = max(len("color_name"), max(len(r[0]) for r in all_rows)) + 1
    header = (
        f"{'color_name':<{col_w}}{'e#':<4}{'exposure_us':>12}  "
        f"{'jpegsize':>10}  {'roi_shape':>10}"
    )
    print(header)
    print("-" * len(header))
    for color, label, exp, jpeg, shape in all_rows:
        print(f"{color:<{col_w}}{label:<4}{exp:>12}  {jpeg:>10}  {shape:>10}")


# ── Mode 2: jpeg extraction ──────────────────────────────────────────

def cmd_jpeg_extract(reader: Hdf5Reader, h5_path: Path) -> None:
    """
    Extract each color's embedded jpeg_e3 blob to <stem>/<color>.jpg.

    Output directory is created next to h5_path (its stem), e.g.
    captures/20260802_2034.h5 -> captures/20260802_2034/white.jpg, ...
    """
    out_dir = h5_path.parent / h5_path.stem
    colors = list_led_colors(reader)
    written = 0
    for color in colors:
        if not has_jpeg(reader, color):
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        data = reader.read_bytes(f"/leds/{color}/jpeg_e3")
        out_path = out_dir / f"{color}.jpg"
        out_path.write_bytes(data)
        print(f"  wrote {out_path}  ({len(data)} bytes)")
        written += 1

    if written == 0:
        print(f"{h5_path.name}: no embedded JPEGs found.")
    else:
        print(f"{h5_path.name}: extracted {written} JPEG(s) to {out_dir}")


# ── Mode 3: ROI plot ─────────────────────────────────────────────────

def _combine_gray(r: np.ndarray, gr: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Unweighted mean of R/Gr/B raw counts (linear data; no luma bias)."""
    return (r + gr + b) / 3.0


def cmd_roi_plot(
    reader: Hdf5Reader,
    h5_path: Path,
    color: str,
    label: str,
    gray: bool,
    output: str = None,
) -> None:
    """
    Plot the R/Gr/B ROI layers for one color+exposure as one imshow figure.

    Combines R, Gr, B (never Gb, per spec) either as a 3-channel RGB
    image (default) or as a single grayscale plane via gray=True.

    Args:
        reader: Open Hdf5Reader for the archive.
        h5_path: Path to the archive (used for the plot title).
        color: LED color name, e.g. "white".
        label: Exposure label, e.g. "e0".
        gray: If True, plot mean(R,Gr,B) grayscale; else RGB.
        output: If given, also save the image area alone (no title,
            axes, or colorbar) as a PNG at this path.

    Raises:
        SystemExit: color or label is not present in this file.
    """
    colors = list_led_colors(reader)
    if color not in colors:
        raise SystemExit(
            f"color '{color}' not in {h5_path.name}. "
            f"Available: {', '.join(colors) or '(none)'}"
        )
    labels = list_lit_labels(reader, color)
    if label not in labels:
        raise SystemExit(
            f"exposure '{label}' not present for '{color}' in "
            f"{h5_path.name}. Available: {', '.join(labels) or '(none)'}"
        )

    base = f"/leds/{color}/lit/{label}"
    r = reader.read_array(f"{base}/R").astype(np.float32)
    gr = reader.read_array(f"{base}/Gr").astype(np.float32)
    b = reader.read_array(f"{base}/B").astype(np.float32)
    exp_us = lit_exposure_us(reader, color, label)

    import matplotlib.pyplot as plt  # deferred; only this mode needs it
    from matplotlib.patches import Rectangle

    fig, ax = plt.subplots(figsize=(6, 8))
    if gray:
        image = np.clip(_combine_gray(r, gr, b) / RAW_MAX, 0, 1)
        im = ax.imshow(
            image, cmap="gray", vmin=0, vmax=1,
            aspect="equal", interpolation="nearest", origin="upper",
        )
        plt.colorbar(im, ax=ax, label="normalized intensity")
        mode_desc = "grayscale mean(R,Gr,B)"
    else:
        image = np.clip(np.stack([r, gr, b], axis=-1) / RAW_MAX, 0, 1)
        ax.imshow(image, aspect="equal", interpolation="nearest", origin="upper")
        mode_desc = "RGB (R, Gr, B)"

    for name, (x, y, w, h) in TEST_ROI.items():
        ax.add_patch(Rectangle(
            (x, y), w, h, linewidth=1.5, edgecolor="yellow", facecolor="none",
        ))
        ax.text(
            x, y - 2, name, color="yellow", fontsize=8,
            ha="left", va="bottom",
        )

    ax.set_title(
        f"{h5_path.name}  {color} {label}  {mode_desc}\nexposure={exp_us} us"
    )
    ax.set_xlabel("column (channel px)")
    ax.set_ylabel("row (channel px)")

    if output:
        # Image area alone: plt.imsave writes exactly the array's pixels,
        # with no title/axes/colorbar, unlike fig.savefig on this figure.
        if gray:
            plt.imsave(output, image, cmap="gray", vmin=0, vmax=1)
        else:
            plt.imsave(output, image)
        print(f"Saved image to {output}")

    plt.show()


# ── CLI ──────────────────────────────────────────────────────────────

def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  hdf5inspect.py 20260802_1404.h5 -s\n"
            "  hdf5inspect.py 20260802_1404.h5 -j\n"
            "  hdf5inspect.py 20260802_1404.h5 -r\n"
            "  hdf5inspect.py 20260802_1404.h5 -r white 0\n"
            "  hdf5inspect.py 20260802_1404.h5 -r red_orange 3 --gray\n"
            "  hdf5inspect.py 20260802_1404.h5 -s -j\n"
            "\n"
            "NOTE: FILE must come before -r/--roi_plot, since -r accepts\n"
            "0-2 values and would otherwise swallow it.\n"
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
        "-s", "--summary", action="store_true",
        help="print a compact lit-exposure summary table",
    )
    parser.add_argument(
        "-j", "--jpeg_extract", action="store_true",
        help="extract embedded JPEGs to <stem>/<color>.jpg",
    )
    parser.add_argument(
        "-r", "--roi_plot", nargs="*", default=None,
        metavar=("COLOR", "EXP"),
        help="plot ROI R/Gr/B layers for one LED/exposure "
             "(default: white 0; at most 2 values)",
    )
    parser.add_argument(
        "--gray", action="store_true",
        help="use grayscale mean(R,Gr,B) instead of RGB (with -r only)",
    )
    parser.add_argument(
        "-o", "--output", default=None, metavar="PNG",
        help="save the ROI image alone (no title/axes/colorbar) to this "
             "PNG path (with -r only)",
    )
    args = parser.parse_args(argv)

    if args.roi_plot is not None:
        if len(args.roi_plot) > 2:
            parser.error(
                "-r/--roi_plot takes at most 2 values: [COLOR] [EXPOSURE]"
            )
        color = args.roi_plot[0] if len(args.roi_plot) >= 1 else "white"
        exp_raw = args.roi_plot[1] if len(args.roi_plot) >= 2 else "0"
        if not exp_raw.isdigit():
            parser.error(
                f"exposure index must be a plain digit (0-6), got {exp_raw!r}"
            )
        args.roi_color = color
        args.roi_label = f"e{int(exp_raw)}"

    return args


def main() -> int:
    args = _parse_args()
    captures_dir = Path(args.captures_dir).expanduser().resolve()

    try:
        h5_path = resolve_h5_path(args.filename, captures_dir)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    run_summary = args.summary
    run_jpeg = args.jpeg_extract
    run_roi = args.roi_plot is not None
    if not (run_summary or run_jpeg or run_roi):
        run_summary = True  # default when no mode flag given

    with Hdf5Reader(str(h5_path)) as reader:
        if run_summary:
            cmd_summary(reader, h5_path)
        if run_jpeg:
            cmd_jpeg_extract(reader, h5_path)
        if run_roi:
            cmd_roi_plot(
                reader, h5_path, args.roi_color, args.roi_label, args.gray,
                args.output,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
