"""
hdf5inspect.py — quick desktop inspection of a capture-suite HDF5 archive.
--------------------------------------------------------------------------
Desktop-only developer tool (matplotlib dependency). Not synced to the Pi
(see sync_pi_files.txt).

Four focused modes, more compact than Hdf5Reader.summary():

  -s / --summary       Compact table: color_name, e#, exposure_us,
                        jpegsize, roi_shape (one row per lit exposure
                        label present, plus a synthesized row for a jpeg
                        that exists without a matching lit label).
  -j / --jpeg_extract   Extract each color's embedded jpeg_e3 blob to
                        <captures_dir>/<file_stem>/<color>.jpg, or
                        <file_stem>/<section>/<color>.jpg with --pre.
  -r / --roi_plot       Plot the R, Gr, B ROI layers (Gb is skipped) for
                        one LED color + exposure label as a matplotlib
                        image: an RGB reconstruction by default, or a
                        grayscale mean(R,Gr,B) with --gray. -o/--output
                        additionally saves just the image area (no
                        title/axes/colorbar) as a PNG. TEST_ROI boxes are
                        drawn and labeled on top.
                        --layers turns -r into one panel per Bayer layer
                        instead of a composite: all four (R, Gr, Gb, B) by
                        default, or the ones named, on a shared 0..1023
                        grayscale scale. This is the only view that shows
                        Gb, and it says which layers the capture stored.
                        With --layers, -o saves the whole figure (panels,
                        titles and colorbar), not a bare image.
  -w / --write_db      Dark-subtract every LED color/exposure/Bayer-layer
                        (R, Gr, Gb, B), compute mean/stdev over each
                        TEST_ROI box, and write the results to a sqlite
                        db (--db, default devdesk/devdesk.sqlite).
                        TEST_ROI is fixed but the frame is not, so boxes
                        are fitted to it first: one hanging off the edge is
                        measured over the part inside and written against
                        its own roi row (N and frame_w/h describe what was
                        averaged), one missing the frame entirely is
                        dropped, and either case also writes roi_fit.png
                        next to the archive showing where they fell.

If no mode flag is given, -s/--summary runs by default. Modes may be
combined in one invocation.

--pre reads the pre-image instead of the measurement. From software 0.7.0
an archive can hold two sweeps of one strip: the measurement at the root,
and the pre-image of the unreacted strip under "/pre" (see capture_h5's
"Sections"). Every mode works on either, because --pre only moves where the
paths start. The pre-image is a short sweep -- two illuminants, one exposure
each, two ladder rungs below the measurement's -- so -s is the way to find
out what -r can ask for. Messages and bayervals.h5file name a sectioned
archive "<file>.h5/pre", matching padanalysis's probe.split_label.

Usage:
    python3 devdesk/hdf5inspect.py FILE [--pre] [-s] [-j] \\
        [-r [COLOR] [EXP]] [--gray | --layers [CH ...]] [-o PNG] \\
        [-w] [--db PATH]

    NOTE: FILE must come before -r/--roi_plot on the command line, since
    -r accepts 0-2 trailing values and would otherwise swallow FILE too.

Examples:
    python3 devdesk/hdf5inspect.py 20260802_1404.h5 -s
    python3 devdesk/hdf5inspect.py 20260802_1404.h5 -j
    python3 devdesk/hdf5inspect.py 20260802_1404.h5 -r
    python3 devdesk/hdf5inspect.py 20260802_1404.h5 -r white 0
    python3 devdesk/hdf5inspect.py 20260802_1404.h5 -r red_orange 3 --gray
    python3 devdesk/hdf5inspect.py 20260802_1404.h5 -r white 0 -o roi.png
    python3 devdesk/hdf5inspect.py 20260802_1404.h5 -w
    python3 devdesk/hdf5inspect.py C1014.h5 --pre -s
    python3 devdesk/hdf5inspect.py C1014.h5 --pre -r royal_blue 0 -o pre.png
    python3 devdesk/hdf5inspect.py C1014.h5 -r white 0 --layers -o post.png
    python3 devdesk/hdf5inspect.py C1014.h5 --pre -r red 0 --layers R B
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import capture_h5  # noqa: E402
import version  # noqa: E402
from hdf5read import Hdf5Reader  # noqa: E402

DEFAULT_CAPTURES_DIR = Path(__file__).resolve().parent.parent / "captures"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "devdesk.sqlite"
RAW_MAX = 1023  # 10-bit sensor ceiling, even though stored as uint16
BAYER_CHANNELS = ("R", "Gr", "Gb", "B")

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


# ── Sections ────────────────────────────────────────────────────────

#: The section holding the pre-image, spelled as capture_h5 writes it.
PRE = "pre"


class SectionView:
    """
    An Hdf5Reader restricted to one section of an archive.

    Every path below /meta repeats under a section prefix (capture_h5,
    "Sections"), so reading the pre-image is reading the same layout at a
    different root. Wrapping the reader rather than threading a prefix
    through every helper keeps the modes written the one way: they still ask
    for "/leds/white/lit/e0/R" and get it out of whichever sweep the caller
    opened. Delegates the whole of Hdf5Reader's read API; a view on the
    empty section is the reader itself, one call deeper.

    Attributes:
        section: The section name without slashes, "" for the measurement.
    """

    def __init__(self, reader: Hdf5Reader, section: str = ""):
        """
        Args:
            reader: Open Hdf5Reader for the archive.
            section: Section name, e.g. PRE; "" for the root measurement.
        """
        self.reader = reader
        self.section = section.strip("/")
        self._at = f"/{self.section}" if self.section else ""

    def _p(self, path: str) -> str:
        """Return one absolute archive path rebased onto this section."""
        if not self._at:
            return path
        return (f"{self._at}/{path.strip('/')}").rstrip("/") or "/"

    def read_array(self, dataset_path: str) -> np.ndarray:
        return self.reader.read_array(self._p(dataset_path))

    def read_bytes(self, dataset_path: str) -> bytes:
        return self.reader.read_bytes(self._p(dataset_path))

    def read_metadata(self, group_path: str, name: str) -> dict:
        return self.reader.read_metadata(self._p(group_path), name)

    def attrs(self, path: str) -> dict:
        return self.reader.attrs(self._p(path))

    def exists(self, path: str) -> bool:
        return self.reader.exists(self._p(path))

    def list_children(self, group_path: str = "/") -> list:
        return self.reader.list_children(self._p(group_path))


def archive_label(h5_path: Path, reader) -> str:
    """
    Name an archive the way messages and db rows have to carry it.

    The section is part of the identity, not a detail of how the file was
    read: pre-image and measurement use the same LED names and the same
    exposure labels, so "C1014.h5" alone would have -w's pre rows overwrite
    its post rows (bayervals is UNIQUE on h5file+color+eno+layer+roi+
    processing). The "<file>/<section>" form is padanalysis's, so
    probe.split_label reads these back.

    Args:
        h5_path: Path to the archive.
        reader: SectionView, or a bare Hdf5Reader (always the measurement).

    Returns:
        "C1014.h5" for the measurement, "C1014.h5/pre" for the pre-image.
    """
    section = getattr(reader, "section", "")
    return f"{h5_path.name}/{section}" if section else h5_path.name


def section_out_dir(h5_path: Path, reader) -> Path:
    """
    Return the directory this archive's derived files belong in.

    <parent>/<stem> for the measurement, <parent>/<stem>/<section> for a
    section: both sweeps use the same LED names, so one <stem>/white.jpg
    cannot mean both. Not created here — callers that write make it.

    Args:
        h5_path: Path to the archive.
        reader: SectionView, or a bare Hdf5Reader (always the measurement).

    Returns:
        Directory path, e.g. captures/C1014/pre.
    """
    out_dir = h5_path.parent / h5_path.stem
    section = getattr(reader, "section", "")
    return out_dir / section if section else out_dir


# ── ROI geometry ────────────────────────────────────────────────────

def clip_roi(rect, frame_w: int, frame_h: int):
    """
    Return the part of one ROI box that lies inside the frame.

    TEST_ROI is a fixed list in pixel coordinates, and the frame it is read
    against is not fixed: the layer arrays are a crop of whatever `bound`
    was in force for that capture, so boxes written for a 315x98 crop hang
    off the edge of a 130x183 one. A box that hangs off is a real answer
    over fewer pixels, not an error — but a box that misses the frame
    entirely has no pixels to average and must be dropped, not clamped to
    a sliver at the edge.

    Args:
        rect: (x, y, w, h) as TEST_ROI stores it.
        frame_w: Frame width in px (layer array columns).
        frame_h: Frame height in px (layer array rows).

    Returns:
        (x, y, w, h) of the overlap, or None if there is no overlap.

    Example:
        >>> clip_roi((24, 100, 34, 34), 183, 130)
        (24, 100, 34, 30)
        >>> clip_roi((24, 166, 34, 34), 183, 130) is None
        True
    """
    x, y, w, h = rect
    x0, y0 = max(int(x), 0), max(int(y), 0)
    x1, y1 = min(int(x) + int(w), frame_w), min(int(y) + int(h), frame_h)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def fit_test_rois(frame_w: int, frame_h: int) -> tuple:
    """
    Sort TEST_ROI into what this frame can measure and what it cannot.

    Args:
        frame_w: Frame width in px.
        frame_h: Frame height in px.

    Returns:
        (usable, clipped, dropped): `usable` maps name -> the (x, y, w, h)
        actually inside the frame (the TEST_ROI box itself where it fits);
        `clipped` is the sorted names in `usable` that had to be cut down;
        `dropped` is the sorted names with no overlap at all.
    """
    usable, clipped, dropped = {}, [], []
    for name, rect in TEST_ROI.items():
        box = clip_roi(rect, frame_w, frame_h)
        if box is None:
            dropped.append(name)
            continue
        if tuple(box) != tuple(rect):
            clipped.append(name)
        usable[name] = box
    return usable, sorted(clipped), sorted(dropped)


def draw_test_rois(ax, frame_w: int = None, frame_h: int = None) -> None:
    """
    Outline and label the TEST_ROI boxes on an image axes.

    Args:
        ax: Matplotlib axes showing a layer image at 1 px per px.
        frame_w: Frame width; with frame_h, boxes that miss the frame
            entirely are left off instead of drawn where nothing is shown.
        frame_h: Frame height. See frame_w.
    """
    from matplotlib.patches import Rectangle

    for name, (x, y, w, h) in TEST_ROI.items():
        if (frame_w is not None and frame_h is not None
                and clip_roi((x, y, w, h), frame_w, frame_h) is None):
            continue
        ax.add_patch(Rectangle(
            (x, y), w, h, linewidth=1.5, edgecolor="yellow", facecolor="none",
        ))
        ax.text(
            x, y - 2, name, color="yellow", fontsize=8,
            ha="left", va="bottom",
        )


# ── Schema-introspection helpers (built only on Hdf5Reader's public API) ──

def list_led_colors(reader: "Hdf5Reader | SectionView") -> list:
    """Return sorted LED color names present under /leds (possibly empty)."""
    try:
        return sorted(reader.list_children("/leds"))
    except KeyError:
        return []


def list_lit_labels(reader: "Hdf5Reader | SectionView", color: str) -> list:
    """Return exposure labels (e.g. ['e0','e1',...]) under one LED's /lit."""
    try:
        labels = reader.list_children(f"/leds/{color}/lit")
    except KeyError:
        return []
    return sorted(labels, key=lambda lab: int(lab[1:]))


def has_jpeg(reader: "Hdf5Reader | SectionView", color: str) -> bool:
    """Return True if /leds/<color>/jpeg_e3 exists."""
    try:
        return "jpeg_e3" in reader.list_children(f"/leds/{color}")
    except KeyError:
        return False


def lit_stored_channels(reader: "Hdf5Reader | SectionView", color: str, label: str) -> list:
    """
    Return the Bayer channels one lit exposure actually stored an array for.

    From capture_h5 schema 3 onward a layer with no usable signal is written
    as an empty placeholder carrying its statistics — see capture_h5's
    OMITTED_ATTR.  It is still a dataset, and still has its exposure_us, so
    anything that only wants attributes can go on reading it; anything that
    wants pixels must come through here first.

    Args:
        reader: Open reader (Hdf5Reader, or a SectionView on one).
        color: LED color name.
        label: Lit exposure label.

    Returns:
        Channel names in BAYER_CHANNELS order, possibly empty.
    """
    kept = []
    for ch in BAYER_CHANNELS:
        try:
            attrs = reader.attrs(f"/leds/{color}/lit/{label}/{ch}")
        except KeyError:
            continue
        if not capture_h5.is_omitted(attrs):
            kept.append(ch)
    return kept


def lit_channel_shape(reader: "Hdf5Reader | SectionView", color: str, label: str):
    """
    Return the (rows, cols) shape shared by the stored channel datasets.

    Reads the first channel that holds an array rather than always R: an
    under-exposed R — royal_blue's, say — is stored empty, and its (0, 0) would
    otherwise be reported as the archive's frame size.

    Returns:
        (rows, cols), or (0, 0) if this exposure stored no arrays at all.
    """
    kept = lit_stored_channels(reader, color, label)
    if not kept:
        return (0, 0)
    return reader.read_array(f"/leds/{color}/lit/{label}/{kept[0]}").shape


def lit_exposure_us(reader: "Hdf5Reader | SectionView", color: str, label: str) -> int:
    """
    Return the exposure_us attr for one LED's lit exposure label.

    Reads R unconditionally, and correctly so: an omitted layer keeps every
    attribute a stored one has, exposure_us included.  Only pixels go missing.
    """
    return reader.attrs(f"/leds/{color}/lit/{label}/R")["exposure_us"]


def find_dark_link(reader: "Hdf5Reader | SectionView", color: str, label: str) -> str:
    """
    Return the name of one LED's dark link matching a lit exposure label,
    or None if it has no dark frame at all.

    A dark link is written by the same call site that wrote the lit
    exposure for this label, so matching on the link name is equivalent to
    matching on exposure time, and it works across both archive schema
    generations seen in practice: current captures name links
    "before_e0"/"after_e0" (two prefetch batches sharing a shared /dark
    group keyed by exposure); older camtest8-converted archives name the
    link bare, e.g. "e0". When both a "before" and "after" batch exist,
    "after" is preferred as the one taken closest to the lit capture.
    """
    try:
        children = reader.list_children(f"/leds/{color}/dark")
    except KeyError:
        return None
    matches = [c for c in children if c == label or c.endswith(f"_{label}")]
    if not matches:
        return None

    def _rank(name: str) -> int:
        if name.startswith("after_"):
            return 0
        if name.startswith("before_"):
            return 1
        return 2

    matches.sort(key=_rank)
    return matches[0]


# ── Mode 1: summary ─────────────────────────────────────────────────

def summary_rows(reader: "Hdf5Reader | SectionView", color: str) -> list:
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
        return [(color, "-", "-", "-", "-", "-")]

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
            # Which layers hold pixels. An archive from before capture_h5
            # schema 3 always holds all four, so this reads as "R Gr Gb B"
            # there and needs no version test.
            kept = lit_stored_channels(reader, color, label)
            layers_s = "+".join(kept) if kept else "(none)"
        else:
            exp = "-"
            shape_s = "-"
            layers_s = "-"
        jpeg_s = str(jpeg_bytes) if (jpeg_present and label == "e3") else "-"
        rows.append((color, label, exp, jpeg_s, shape_s, layers_s))
    return rows


def cmd_summary(reader: "Hdf5Reader | SectionView", h5_path: Path) -> None:
    """Print a compact table: color_name, e#, exposure_us, jpegsize, roi_shape."""
    colors = list_led_colors(reader)
    print(f"Summary: {archive_label(h5_path, reader)}")
    if not colors:
        print("  (no /leds group found)")
        return

    all_rows = []
    for color in colors:
        all_rows.extend(summary_rows(reader, color))

    col_w = max(len("color_name"), max(len(r[0]) for r in all_rows)) + 1
    header = (
        f"{'color_name':<{col_w}}{'e#':<4}{'exposure_us':>12}  "
        f"{'jpegsize':>10}  {'roi_shape':>10}  {'layers':<12}"
    )
    print(header)
    print("-" * len(header))
    for color, label, exp, jpeg, shape, layers in all_rows:
        print(f"{color:<{col_w}}{label:<4}{exp:>12}  {jpeg:>10}  "
              f"{shape:>10}  {layers:<12}")


# ── Mode 2: jpeg extraction ──────────────────────────────────────────

def cmd_jpeg_extract(reader: "Hdf5Reader | SectionView", h5_path: Path) -> None:
    """
    Extract each color's embedded jpeg_e3 blob to <stem>/<color>.jpg.

    Output directory is created next to h5_path (its stem), e.g.
    captures/20260802_2034.h5 -> captures/20260802_2034/white.jpg, ...
    """
    label = archive_label(h5_path, reader)
    out_dir = section_out_dir(h5_path, reader)
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
        print(f"{label}: no embedded JPEGs found.")
    else:
        print(f"{label}: extracted {written} JPEG(s) to {out_dir}")


# ── Mode 3: ROI plot ─────────────────────────────────────────────────

def _combine_gray(r: np.ndarray, gr: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Unweighted mean of R/Gr/B raw counts (linear data; no luma bias)."""
    return (r + gr + b) / 3.0


def cmd_roi_plot(
    reader: "Hdf5Reader | SectionView",
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
        reader: Open reader (Hdf5Reader, or a SectionView on one).
        h5_path: Path to the archive (used for the plot title).
        color: LED color name, e.g. "white".
        label: Exposure label, e.g. "e0".
        gray: If True, plot mean(R,Gr,B) grayscale; else RGB.
        output: If given, also save the image area alone (no title,
            axes, or colorbar) as a PNG at this path.

    Raises:
        SystemExit: color or label is not present in this file.
    """
    archive = archive_label(h5_path, reader)
    colors = list_led_colors(reader)
    if color not in colors:
        raise SystemExit(
            f"color '{color}' not in {archive}. "
            f"Available: {', '.join(colors) or '(none)'}"
        )
    labels = list_lit_labels(reader, color)
    if label not in labels:
        raise SystemExit(
            f"exposure '{label}' not present for '{color}' in "
            f"{archive}. Available: {', '.join(labels) or '(none)'}"
        )

    base = f"/leds/{color}/lit/{label}"
    r = reader.read_array(f"{base}/R").astype(np.float32)
    gr = reader.read_array(f"{base}/Gr").astype(np.float32)
    b = reader.read_array(f"{base}/B").astype(np.float32)
    exp_us = lit_exposure_us(reader, color, label)

    import matplotlib.pyplot as plt  # deferred; only this mode needs it

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

    draw_test_rois(ax, image.shape[1], image.shape[0])

    ax.set_title(
        f"{archive}  {color} {label}  {mode_desc}\nexposure={exp_us} us"
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


def cmd_layer_plot(
    reader: "Hdf5Reader | SectionView",
    h5_path: Path,
    color: str,
    label: str,
    channels: list,
    output: str = None,
) -> None:
    """
    Plot each Bayer layer of one color+exposure on its own panel.

    The composite plot (cmd_roi_plot) answers "what does the strip look
    like"; this one answers "what does each layer hold", which is the
    question a layer-by-layer analysis actually rests on — and it is the
    only way to see Gb at all, since the composite never uses it. Panels
    share one 0..RAW_MAX grayscale scale, so a layer that is darker than
    its neighbour looks darker rather than being stretched to match it.

    A layer stored empty (capture_h5's OMITTED_ATTR: too little signal to
    be worth keeping) gets a panel saying so rather than being left out,
    because which layers were dropped is itself the finding — the
    pre-image's red exposure keeps only R and B.

    Args:
        reader: Open reader (Hdf5Reader, or a SectionView on one).
        h5_path: Path to the archive (used for the plot title).
        color: LED color name, e.g. "royal_blue".
        label: Exposure label, e.g. "e0".
        channels: Bayer channel names to plot, in BAYER_CHANNELS order.
        output: If given, save the whole figure to this PNG path. Unlike
            -r, this cannot be the bare image area: several panels, their
            titles and a shared colorbar are the point of it.

    Raises:
        SystemExit: color or label is not present in this file.
    """
    archive = archive_label(h5_path, reader)
    colors = list_led_colors(reader)
    if color not in colors:
        raise SystemExit(
            f"color '{color}' not in {archive}. "
            f"Available: {', '.join(colors) or '(none)'}"
        )
    labels = list_lit_labels(reader, color)
    if label not in labels:
        raise SystemExit(
            f"exposure '{label}' not present for '{color}' in "
            f"{archive}. Available: {', '.join(labels) or '(none)'}"
        )

    stored = lit_stored_channels(reader, color, label)
    base = f"/leds/{color}/lit/{label}"
    planes = {
        ch: reader.read_array(f"{base}/{ch}").astype(np.float32)
        for ch in channels if ch in stored
    }
    exp_us = lit_exposure_us(reader, color, label)
    rows_px, cols_px = lit_channel_shape(reader, color, label)

    import matplotlib.pyplot as plt  # deferred; only this mode needs it

    # A tall crop (315x98) reads better side by side; a squarish one
    # (130x183) reads better as a block.
    ncols = len(channels) if rows_px > cols_px else min(len(channels), 2)
    nrows = -(-len(channels) // ncols)
    # Size each panel to the frame's own aspect, plus room for its title:
    # a 2x2 of 130x183 crops padded to squares is mostly whitespace.
    panel_w = 4.2
    panel_h = panel_w * (rows_px / cols_px if cols_px else 1.0) + 0.9
    fig, axes = plt.subplots(
        nrows, ncols, figsize=(panel_w * ncols, panel_h * nrows),
        squeeze=False, layout="constrained",
    )
    flat = [ax for row in axes for ax in row]

    im = None
    for ax, ch in zip(flat, channels):
        plane = planes.get(ch)
        if plane is None:
            ax.text(
                0.5, 0.5, f"{ch}\nnot stored\n(too little signal)",
                ha="center", va="center", transform=ax.transAxes, fontsize=10,
                color="dimgray",
            )
            ax.set_facecolor("whitesmoke")
            ax.set_xticks([])
            ax.set_yticks([])
            continue
        im = ax.imshow(
            plane, cmap="gray", vmin=0, vmax=RAW_MAX,
            aspect="equal", interpolation="nearest", origin="upper",
        )
        draw_test_rois(ax, plane.shape[1], plane.shape[0])
        ax.set_title(
            f"{ch}   mean {plane.mean():.0f}   max {plane.max():.0f} DN",
            fontsize=10,
        )
        ax.set_xlabel("column (channel px)")
        ax.set_ylabel("row (channel px)")
    for ax in flat[len(channels):]:
        ax.axis("off")

    if im is not None:
        fig.colorbar(
            im, ax=axes, fraction=0.03, label=f"raw DN (0..{RAW_MAX})",
        )
    fig.suptitle(
        f"{archive}  {color} {label}  Bayer layers\n"
        f"exposure={exp_us} us   frame={rows_px}x{cols_px}"
    )

    if output:
        fig.savefig(output, dpi=120, bbox_inches="tight")
        print(f"Saved layer plot to {output}")

    plt.show()


# ── Mode 4: dark-subtracted ROI means -> sqlite ──────────────────────

ROI_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS roi (
    rowid     INTEGER PRIMARY KEY NOT NULL,
    active    INTEGER NOT NULL DEFAULT 1,
    isdefault INTEGER NOT NULL DEFAULT 0,
    add_dt    TEXT,
    name      TEXT NOT NULL,
    role      TEXT,
    x         INTEGER NOT NULL,
    y         INTEGER NOT NULL,
    w         INTEGER NOT NULL,
    h         INTEGER NOT NULL,
    frame_w   INTEGER NOT NULL,
    frame_h   INTEGER NOT NULL,
    model     TEXT,
    color     TEXT,
    N         INTEGER NOT NULL,
    remark    TEXT
);
"""

BAYERVALS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS bayervals (
    h5file      TEXT NOT NULL,
    exposure_us INTEGER NOT NULL,
    eno         TEXT NOT NULL,
    color       TEXT NOT NULL,
    bayerlayer  TEXT NOT NULL,
    roi         INTEGER NOT NULL REFERENCES roi (rowid),
    processing  TEXT NOT NULL,
    mean        REAL NOT NULL,
    stdev       REAL NOT NULL,
    UNIQUE (h5file, color, eno, bayerlayer, roi, processing)
);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the roi/bayervals tables if this is a fresh db."""
    conn.execute(ROI_TABLE_DDL)
    conn.execute(BAYERVALS_TABLE_DDL)
    conn.commit()


def _ensure_test_rois(
    conn: sqlite3.Connection, rois: dict, frame_w: int, frame_h: int
) -> dict:
    """
    Return {name: rowid} for the boxes about to be measured, inserting any
    the db does not already hold.

    A row is looked up by its whole geometry — name, box and frame — not by
    name alone, because the box a name stands for is not the same region in
    every archive: a TEST_ROI written for a 315x98 crop is clipped when it
    is read against a 130x183 one, and the mean then covers fewer pixels
    than the unclipped row's N claims. Giving the clipped box its own row
    keeps N honest and keeps `bayervals.roi` pointing at the region that
    was actually averaged, rather than encoding "this one was cut down"
    somewhere else for a reader to infer.

    Args:
        conn: Open sqlite connection with the roi table present.
        rois: {name: (x, y, w, h)} — the effective boxes, post-clipping.
        frame_w: Frame width the boxes were fitted to.
        frame_h: Frame height the boxes were fitted to.

    Returns:
        {name: rowid} covering every entry in `rois`.
    """
    ids = {}
    for name, (x, y, w, h) in rois.items():
        clipped = tuple(TEST_ROI[name]) != (x, y, w, h)
        row = conn.execute(
            "SELECT rowid FROM roi WHERE name = ? AND x = ? AND y = ? AND "
            "w = ? AND h = ? AND frame_w = ? AND frame_h = ?",
            (name, x, y, w, h, frame_w, frame_h),
        ).fetchone()
        if row:
            ids[name] = row[0]
            continue
        remark = "hdf5inspect.py TEST_ROI"
        if clipped:
            rx, ry, rw, rh = TEST_ROI[name]
            remark += f" clipped to frame from ({rx},{ry},{rw},{rh})"
        cur = conn.execute(
            "INSERT INTO roi (active, isdefault, add_dt, name, role, x, y, w, h, "
            "frame_w, frame_h, model, color, N, remark) VALUES "
            "(1, 0, ?, ?, 'test', ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
            (
                datetime.now().isoformat(timespec="seconds"), name,
                x, y, w, h, frame_w, frame_h, w * h, remark,
            ),
        )
        ids[name] = cur.lastrowid
    conn.commit()
    return ids


def plot_roi_fit(
    reader: "Hdf5Reader | SectionView",
    h5_path: Path,
    frame_w: int,
    frame_h: int,
    out_path: Path,
) -> None:
    """
    Draw where the TEST_ROI boxes fall against this archive's frame.

    Written only when at least one box does not fit, and it is the answer
    to the question that failure raises: *which* boxes, and by how much.
    The frame is shown with a real layer behind it so the reader can see
    what the surviving part of a clipped box is actually averaging.

        green   box fits; measured whole
        orange  clipped; solid = the part measured, dashed = as written
        red     no overlap with the frame; dropped, nothing measured

    Args:
        reader: Open reader (Hdf5Reader, or a SectionView on one).
        h5_path: Path to the archive (used for the title).
        frame_w: Frame width in px.
        frame_h: Frame height in px.
        out_path: PNG to write; its directory is created if needed.
    """
    import matplotlib
    matplotlib.use("Agg")  # a diagnostic file, never an interactive window
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    # Any lit layer will do — this is about geometry, not photometry — but a
    # real one makes the surviving part of a clipped box readable.
    backdrop = None
    for color in list_led_colors(reader):
        for lab in list_lit_labels(reader, color):
            kept = lit_stored_channels(reader, color, lab)
            if kept:
                backdrop = reader.read_array(
                    f"/leds/{color}/lit/{lab}/{kept[0]}"
                ).astype(np.float32)
                break
        if backdrop is not None:
            break

    fig, ax = plt.subplots(figsize=(7, 7))
    if backdrop is not None:
        ax.imshow(
            np.clip(backdrop / RAW_MAX, 0, 1), cmap="gray", vmin=0, vmax=1,
            aspect="equal", interpolation="nearest", origin="upper",
        )
    ax.add_patch(Rectangle(
        (-0.5, -0.5), frame_w, frame_h,
        linewidth=2, edgecolor="deepskyblue", facecolor="none",
    ))

    usable, clipped, dropped = fit_test_rois(frame_w, frame_h)
    for name, (x, y, w, h) in TEST_ROI.items():
        if name in dropped:
            ax.add_patch(Rectangle(
                (x, y), w, h, linewidth=1.5, edgecolor="red",
                facecolor="red", alpha=0.25, hatch="xx",
            ))
            note = "dropped"
            tint = "red"
        elif name in clipped:
            ax.add_patch(Rectangle(
                (x, y), w, h, linewidth=1.2, edgecolor="orange",
                facecolor="none", linestyle="--",
            ))
            cx, cy, cw, chh = usable[name]
            ax.add_patch(Rectangle(
                (cx, cy), cw, chh, linewidth=1.8, edgecolor="orange",
                facecolor="orange", alpha=0.25,
            ))
            note = f"clipped {w * h - cw * chh} px"
            tint = "orange"
        else:
            ax.add_patch(Rectangle(
                (x, y), w, h, linewidth=1.5, edgecolor="lime", facecolor="none",
            ))
            note = "fits"
            tint = "lime"
        ax.text(x, y - 2, f"{name} ({note})", color=tint, fontsize=8,
                ha="left", va="bottom")

    # Show the whole of every box, including the parts off the frame —
    # seeing how far outside they land is the point.
    xs = [-0.5, frame_w] + [x for x, _, _, _ in TEST_ROI.values()] \
        + [x + w for x, _, w, _ in TEST_ROI.values()]
    ys = [-0.5, frame_h] + [y for _, y, _, _ in TEST_ROI.values()] \
        + [y + h for _, y, _, h in TEST_ROI.values()]
    pad = 12
    ax.set_xlim(min(xs) - pad, max(xs) + pad)
    ax.set_ylim(max(ys) + pad, min(ys) - pad)  # origin upper
    ax.set_xlabel("column (channel px)")
    ax.set_ylabel("row (channel px)")
    ax.set_title(
        f"{archive_label(h5_path, reader)}: TEST_ROI vs frame\n"
        f"{len(clipped)} clipped, {len(dropped)} dropped, "
        f"{len(usable) - len(clipped)} fit"
    )

    # Say what the colours mean here rather than in whoever reads the file.
    keys = [Rectangle((0, 0), 1, 1, edgecolor="deepskyblue", facecolor="none",
                      linewidth=2,
                      label=f"frame {frame_h}x{frame_w} (rows x cols)")]
    if len(usable) > len(clipped):
        keys.append(Rectangle((0, 0), 1, 1, edgecolor="lime", facecolor="none",
                              label="fits: measured whole"))
    if clipped:
        keys.append(Rectangle((0, 0), 1, 1, edgecolor="orange",
                              facecolor="orange", alpha=0.25,
                              label="clipped: solid measured, dashed as written"))
    if dropped:
        keys.append(Rectangle((0, 0), 1, 1, edgecolor="red", facecolor="red",
                              alpha=0.25, hatch="xx",
                              label="dropped: no overlap, not measured"))
    ax.legend(handles=keys, loc="lower right", fontsize=8)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def _roi_mean_stdev(channel: np.ndarray, x: int, y: int, w: int, h: int):
    """Sample mean and stdev (ddof=1) of one ROI block, (x, y, w, h) in px."""
    block = channel[y:y + h, x:x + w]
    return float(block.mean()), float(block.std(ddof=1))


def cmd_write_db(reader: "Hdf5Reader | SectionView", h5_path: Path, db_path: Path) -> None:
    """
    Dark-subtract every LED color/exposure/Bayer-layer and write per-ROI
    mean/stdev rows to a sqlite db, creating it if needed.

    For each LED color and each lit exposure label, the matching dark
    frame is found via find_dark_link() (same exposure, "after" batch
    preferred over "before"); a label with no dark frame at all is
    skipped and reported. R/Gr/Gb/B are all subtracted (unlike -r/--roi_plot,
    which only ever combines R/Gr/B for display).

    TEST_ROI is fitted to the frame first (see clip_roi): a box hanging off
    the edge is measured over the part that is inside and written against
    its own roi row, a box that misses the frame entirely is dropped, and
    either way a diagnostic PNG is written showing where the boxes fell.
    Before this, such a box averaged an empty slice and the NaN it produced
    failed bayervals.mean's NOT NULL constraint partway through the run.

    Args:
        reader: Open reader (Hdf5Reader, or a SectionView on one).
        h5_path: Path to the archive (recorded as bayervals.h5file, with
            the section appended when reading one -- see archive_label).
        db_path: sqlite db path; created with the roi/bayervals schema if
            it doesn't already have it.
    """
    archive = archive_label(h5_path, reader)
    colors = list_led_colors(reader)
    if not colors:
        print(f"{archive}: no /leds group found, nothing to write.")
        return

    frame_w = frame_h = None
    for color in colors:
        labels = list_lit_labels(reader, color)
        if labels:
            frame_h, frame_w = lit_channel_shape(reader, color, labels[0])
            break
    if frame_w is None:
        print(f"{archive}: no lit exposures found, nothing to write.")
        return

    usable, clipped, dropped = fit_test_rois(frame_w, frame_h)
    if clipped or dropped:
        for name in clipped:
            x, y, w, h = usable[name]
            rx, ry, rw, rh = TEST_ROI[name]
            print(f"  clip {name}: ({rx},{ry},{rw},{rh}) -> ({x},{y},{w},{h}) "
                  f"in a {frame_h}x{frame_w} frame, "
                  f"{100 * w * h / (rw * rh):.0f}% of the box")
        for name in dropped:
            print(f"  drop {name}: {TEST_ROI[name]} lies outside the "
                  f"{frame_h}x{frame_w} frame entirely")
        fit_png = section_out_dir(h5_path, reader) / "roi_fit.png"
        plot_roi_fit(reader, h5_path, frame_w, frame_h, fit_png)
        print(f"  wrote {fit_png}")
    if not usable:
        print(f"{archive}: no TEST_ROI box overlaps the frame, "
              f"nothing to write.")
        return

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_schema(conn)
        roi_ids = _ensure_test_rois(conn, usable, frame_w, frame_h)

        written = skipped = 0
        for color in colors:
            for label in list_lit_labels(reader, color):
                dark_link = find_dark_link(reader, color, label)
                if dark_link is None:
                    print(f"  skip {color}/{label}: no matching dark frame")
                    skipped += 1
                    continue

                exposure_us = int(lit_exposure_us(reader, color, label))
                dark_group = f"/leds/{color}/dark/{dark_link}"
                stored = lit_stored_channels(reader, color, label)
                if len(stored) < len(BAYER_CHANNELS):
                    gone = [c for c in BAYER_CHANNELS if c not in stored]
                    print(f"  {color}/{label}: {'+'.join(gone)} not stored "
                          f"(too little signal)")
                for ch in stored:
                    # One dark level per Bayer layer -- the mean of the
                    # archive's shortest-exposure dark -- not the matching
                    # frame pixel by pixel.  See the comment above
                    # capture_h5.dark_level.  Rows get processing
                    # 'dark_level' so they are never mistaken for the older
                    # per-pixel 'dark_sub' rows.
                    try:
                        dark = capture_h5.dark_level(
                            reader, ch, "", fallback=f"{dark_group}/{ch}")[0]
                    except KeyError:
                        print(f"  skip {color}/{label}/{ch}: dark not stored")
                        skipped += 1
                        continue
                    lit = reader.read_array(
                        f"/leds/{color}/lit/{label}/{ch}"
                    ).astype(np.float64)
                    sub = lit - dark
                    for roi_name, (x, y, w, h) in usable.items():
                        mean, stdev = _roi_mean_stdev(sub, x, y, w, h)
                        conn.execute(
                            "INSERT OR REPLACE INTO bayervals (h5file, "
                            "exposure_us, eno, color, bayerlayer, roi, "
                            "processing, mean, stdev) VALUES (?,?,?,?,?,?,?,?,?)",
                            (
                                archive, exposure_us, label, color, ch,
                                roi_ids[roi_name], "dark_level", mean, stdev,
                            ),
                        )
                        written += 1
        conn.commit()
    finally:
        conn.close()

    msg = f"{archive}: wrote {written} bayervals row(s) to {db_path}"
    if skipped:
        msg += f" ({skipped} color/exposure combo(s) skipped: no dark match)"
    if clipped:
        msg += f"; {len(clipped)} ROI(s) clipped to the frame ({', '.join(clipped)})"
    if dropped:
        msg += f"; {len(dropped)} ROI(s) dropped ({', '.join(dropped)})"
    print(msg)


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
            "  hdf5inspect.py C1014.h5 --pre -s\n"
            "  hdf5inspect.py C1014.h5 --pre -r royal_blue 0 -o pre.png\n"
            "  hdf5inspect.py C1014.h5 -r white 0 --layers -o post.png\n"
            "  hdf5inspect.py C1014.h5 --pre -r red 0 --layers R B\n"
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
        "--pre", action="store_true",
        help="read the pre-image under /pre (the unreacted strip) instead "
             "of the measurement at the root; applies to every mode",
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
        "--layers", nargs="*", default=None, metavar="CH",
        help="with -r: plot each Bayer layer on its own panel instead of "
             "a composite. No values means all of " + " ".join(BAYER_CHANNELS)
             + "; or name the ones you want, e.g. --layers Gr Gb",
    )
    parser.add_argument(
        "-o", "--output", default=None, metavar="PNG",
        help="save the ROI image alone (no title/axes/colorbar) to this "
             "PNG path (with -r only)",
    )
    parser.add_argument(
        "-w", "--write_db", action="store_true",
        help="dark-subtract every LED/exposure/Bayer-layer, compute "
             "TEST_ROI mean/stdev, and write the results to --db",
    )
    parser.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), metavar="PATH",
        help="sqlite db path for -w/--write_db (default: %(default)s)",
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

    if args.layers is not None:
        if args.roi_plot is None:
            parser.error("--layers goes with -r/--roi_plot")
        if args.gray:
            parser.error(
                "--gray describes the composite plot; --layers replaces it"
            )
        # Accept any capitalization ("gb"), reject anything that is not a
        # Bayer channel, and keep BAYER_CHANNELS order however they arrive.
        wanted = {c.lower(): c for c in BAYER_CHANNELS}
        chosen = []
        for raw in args.layers:
            if raw.lower() not in wanted:
                parser.error(
                    f"unknown Bayer layer {raw!r}; "
                    f"choose from {', '.join(BAYER_CHANNELS)}"
                )
            chosen.append(wanted[raw.lower()])
        args.layers = [c for c in BAYER_CHANNELS
                       if c in (chosen or BAYER_CHANNELS)]

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
    run_write_db = args.write_db
    if not (run_summary or run_jpeg or run_roi or run_write_db):
        run_summary = True  # default when no mode flag given

    with Hdf5Reader(str(h5_path)) as reader:
        # Fail at the door rather than somewhere deep in a mode. `software_ver`
        # says which build wrote the archive; the format versions say whether
        # these tools can read it. See src/version.py.
        problem = version.check_archive(reader.attrs("/"))
        if problem:
            print(f"ERROR: {h5_path.name}: {problem}", file=sys.stderr)
            return 1

        # Ask before reading rather than let each mode fail its own way: an
        # archive with no /pre is a strip that was not pre-imaged, which is
        # a normal archive and worth saying plainly.
        view = SectionView(reader, PRE if args.pre else "")
        if args.pre and not reader.exists(f"/{PRE}"):
            print(f"ERROR: {h5_path.name} holds no /{PRE} section "
                  f"(this strip was not pre-imaged)", file=sys.stderr)
            return 1

        if run_summary:
            cmd_summary(view, h5_path)
        if run_jpeg:
            cmd_jpeg_extract(view, h5_path)
        if run_roi and args.layers is not None:
            cmd_layer_plot(
                view, h5_path, args.roi_color, args.roi_label, args.layers,
                args.output,
            )
        elif run_roi:
            cmd_roi_plot(
                view, h5_path, args.roi_color, args.roi_label, args.gray,
                args.output,
            )
        if run_write_db:
            cmd_write_db(view, h5_path, Path(args.db).expanduser().resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
