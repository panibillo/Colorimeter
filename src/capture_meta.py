"""
capture_meta.py — collect the metadata that makes an archive self-describing.
------------------------------------------------------------------------------
A capture archive stores Bayer layers cropped to the `bound` ROI. Until this
module existed the archive recorded no ROI geometry at all, so a reader had to
infer `bound` from whatever `roi` table it happened to have. That fails
silently the moment the two disagree: the desktop's colorimeter.sqlite is a
copy, `sync_captures_from_pi.sh` copies `captures/` but not the database, and
an ROI edited on the instrument never reaches the analysis machine. Worse,
`bound`'s width and height can be recovered from the layer shape but its x and
y cannot, and a uniform-target capture has no features to register against —
so the geometry was simply lost.

Everything a reader needs to interpret an archive without consulting any
database is gathered here, and only here.

Design for maintenance
----------------------
Adding a field should be a one-line edit in this file and nothing else:

  * Database fields are declared in `DEVICE_COLUMNS` / `SAMPLE_COLUMNS`.
    Add a column name to the tuple and it appears in the next archive.
  * Camera fields are declared in `CAMERA_FIELDS` as (key, accessor) pairs.
    Add a pair; the accessor is called with the BayerCam.
  * Run fields are declared in `RUN_FIELDS`, same shape, called with the
    CaptureConfig.
  * Whole blocks are declared in `BLOCKS`. Add a (name, builder) pair and the
    block appears under that name.

Each block is built independently and defensively. Metadata is never worth
failing a capture over, so a block that raises is replaced by
`{"error": "..."}` and the run continues — a partial record beats none, and
the error travels with the archive where it can be seen later.

Layout produced
---------------
    {
      "meta_version": 2,
      "written_dt":   "2026-08-03 20:15:04",
      "roi":    {"frame_w":…, "frame_h":…, "model":…, "bound": {...},
                 "rects": {"sample": {"x":…,"y":…,"w":…,"h":…,"role":…}, …}},
      "camera": {"model":…, "bayer_pattern":…, "stream_w":…, …},
      "config": {"capture": {"led_color_order":…, …}, "device": {…}, …},
      "device": {"hardware_sn":…, "software_ver":…, …},
      "sample": {"samp_no":…, "collect_dt":…, …}   (absent fields → None),
      "run":    {"mode":…, "exposure_group":…, …},
    }

`root_attrs()` flattens the handful of values every reader needs into plain
HDF5 attributes, so the common case costs no JSON parsing.

Usage:
    import capture_meta

    meta = capture_meta.collect(cam, cfg, sample_rowid=slot_rowid)
    writer.add_metadata(meta, capture_meta.root_attrs(meta))
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

import config_repo as config
import db_manager as db
import roi as roi_mod

log = logging.getLogger(__name__)

# Bumped when the *shape* of the dict below changes in a way a reader must
# know about. Independent of capture_h5.SCHEMA_VERSION, which versions the
# group/dataset layout.
META_VERSION = 2

# ── Declarative field lists — the maintenance surface ───────────────────────
# Add a column here and it appears in the next archive. Nothing else changes.

# The instrument's identity, now config parameters rather than a `device` row.
# Kept as its own block (and its own root attrs) because every reader wants the
# serial without walking the config snapshot.
DEVICE_NAMES: Tuple[str, ...] = (
    "hardware_sn", "software_ver", "description",
)

# The whole sample row travels with the archive: which reagent lot, the
# milestone timestamps the analysis needs to know the strip's age, the
# operator's manual reading, and the result once there is one.
SAMPLE_COLUMNS: Tuple[str, ...] = (
    "rowid", "samp_no", "user", "test_kit", "test_lot", "instrument_sn",
    "collect_dt",
    "samp_grams", "samp_degC", "reagent1_start", "reagent1_end",
    "reagent2_start", "reagent2_end", "capture_start", "capture_end",
    "manual_read", "txt_result", "num_result", "h5file", "remark",
)

# Static camera configuration. Per-frame values (exposure actually used,
# analogue gain, sensor temperature, black levels) are already recorded per
# LED in `capture_info`; this block is what does not vary within a run.
CAMERA_FIELDS: Tuple[Tuple[str, Callable[[Any], Any]], ...] = (
    ("model",            lambda c: c.model),
    ("model_description", lambda c: c.model_description),
    ("bayer_pattern",    lambda c: c.bayer_pattern),
    ("has_autofocus",    lambda c: c.has_autofocus),
    ("max_exposure_us",  lambda c: getattr(c, "MAX_EXPOSURE_US", None)),
    ("stream_w",         lambda c: c.config["size"][0]),
    ("stream_h",         lambda c: c.config["size"][1]),
    ("stream_format",    lambda c: c.config["format"]),
    ("config_size",      lambda c: list(c.config_size) if c.config_size
                                   else None),
    ("lens_position",    lambda c: c.lens_position),
)

# The parameters of this particular run.
RUN_FIELDS: Tuple[Tuple[str, Callable[[Any], Any]], ...] = (
    ("mode",            lambda g: g.mode),
    ("exposure_group",  lambda g: g.exposure_group),
    ("exposure_ms",     lambda g: g.exposure_ms),
    ("led_colors",      lambda g: list(g.led_colors)),
    ("focus_diopter",   lambda g: g.focus_diopter),
)


# ── Context passed to every block builder ───────────────────────────────────

class MetaContext:
    """
    Everything the block builders may need, gathered once.

    Attributes:
        cam: The live BayerCam, or None when no camera is involved.
        cfg: The CaptureConfig for this run, or None.
        sample_rowid: `samples.rowid` this capture belongs to, or None for a
            menu-launched capture that is not part of a protocol run.
    """

    def __init__(self, cam=None, cfg=None, sample_rowid: Optional[int] = None):
        self.cam = cam
        self.cfg = cfg
        self.sample_rowid = sample_rowid


# ── Generic helpers ─────────────────────────────────────────────────────────

def _row_dict(table: str, columns: Tuple[str, ...],
              rowid: Optional[int]) -> Dict[str, Any]:
    """
    Return one row of `table` as {column: value}, or {} if there is no row.

    Args:
        table: Table name; interpolated into the SQL, so it must be a literal
            from this module, never user input.
        columns: Column names to select, in order.
        rowid: Row to fetch. None returns {} without querying.

    Returns:
        {column: value}, or {} when rowid is None or the row is missing.

    Example:
        >>> _row_dict("device", ("rowid", "hardware_sn"), 1)   # doctest: +SKIP
        {'rowid': 1, 'hardware_sn': 'XCN-001'}
    """
    if rowid is None:
        return {}
    sql = f"SELECT {', '.join(columns)} FROM {table} WHERE rowid = ?"
    row = db.read_one(sql, (rowid,))
    if row is None:
        log.warning("capture_meta: %s has no row with rowid=%s", table, rowid)
        return {}
    return dict(zip(columns, row))


def _fields(spec: Tuple[Tuple[str, Callable[[Any], Any]], ...],
            source: Any) -> Dict[str, Any]:
    """
    Apply a declarative (key, accessor) spec to a source object.

    An accessor that raises yields None for that key rather than losing the
    whole block: one missing attribute on an older camera object should not
    cost the archive its camera metadata.

    Args:
        spec: Tuple of (key, callable) pairs.
        source: Object passed to each accessor.

    Returns:
        {key: value}. Empty dict when source is None.

    Example:
        >>> _fields((("a", lambda o: o.x),), type("T", (), {"x": 1})())
        {'a': 1}
    """
    if source is None:
        return {}
    out: Dict[str, Any] = {}
    for key, accessor in spec:
        try:
            out[key] = accessor(source)
        except Exception as e:      # noqa: BLE001 — one field, not the block
            log.warning("capture_meta: field %r unavailable: %s", key, e)
            out[key] = None
    return out


def _rect_dict(r, role: str = "") -> Dict[str, Any]:
    """Return a Rect as a plain JSON-safe dict, with its role."""
    return {"x": int(r.x), "y": int(r.y), "w": int(r.w), "h": int(r.h),
            "role": role or getattr(r, "role", "") or ""}


# ── Block builders ──────────────────────────────────────────────────────────

def _roi_block(ctx: MetaContext) -> Dict[str, Any]:
    """
    Record every active ROI in stream space, plus the space they live in.

    This is the block that closes the failure this module was written for.
    The ROI filling ROLE_BOUND is called out separately as well as appearing
    in `rects`, because it is the one rectangle a reader cannot do without: it
    is the crop the stored layers were taken from, so without its x and y no
    other ROI can be located within them.  Found by role, not by the name
    'bound', for the reason set out in roi.py.

    Geometry is stored in stream space, exactly as the `roi` table holds it,
    so a reader converts with the same roi.to_layer_rect_within() the capture
    path used rather than inventing arithmetic.

    Args:
        ctx: The MetaContext; uses ctx.cam for the live stream size and model.

    Returns:
        {"frame_w", "frame_h", "model", "bound", "rects"}.
    """
    frame_w = frame_h = None
    model = None
    if ctx.cam is not None:
        frame_w, frame_h = ctx.cam.config["size"]
        model = ctx.cam.model

    rois = roi_mod.load_active(frame_w, frame_h, model)
    bound = roi_mod.by_role(rois, roi_mod.ROLE_BOUND)
    return {
        "frame_w": frame_w,
        "frame_h": frame_h,
        "model":   model,
        "bound":   _rect_dict(bound) if bound is not None else None,
        "rects":   {name: _rect_dict(r) for name, r in rois.items()},
    }


def _camera_block(ctx: MetaContext) -> Dict[str, Any]:
    """Static camera configuration; see CAMERA_FIELDS."""
    return _fields(CAMERA_FIELDS, ctx.cam)


def _device_block(ctx: MetaContext) -> Dict[str, Any]:
    """The instrument's identity; see DEVICE_NAMES."""
    return {name: config.get("device", name) for name in DEVICE_NAMES}


def _config_block(ctx: MetaContext) -> Dict[str, Any]:
    """
    Every setting in force, as {role: {name: value}}.

    One query, and the archive becomes self-describing: whatever the analysis
    later needs to know about how this capture was configured is in here,
    without the reader having to guess at the instrument's state.
    """
    return config.snapshot()


def _sample_block(ctx: MetaContext) -> Dict[str, Any]:
    """
    The sample this capture belongs to; see SAMPLE_COLUMNS.

    Empty for a menu-launched calibration capture, which belongs to no
    protocol run. That is a normal state, not an error.
    """
    return _row_dict("samples", SAMPLE_COLUMNS, ctx.sample_rowid)


def _run_block(ctx: MetaContext) -> Dict[str, Any]:
    """This run's capture parameters; see RUN_FIELDS."""
    return _fields(RUN_FIELDS, ctx.cfg)


# The blocks that make up an archive's metadata. Add a pair to add a block.
BLOCKS: Tuple[Tuple[str, Callable[[MetaContext], Dict[str, Any]]], ...] = (
    ("roi",    _roi_block),
    ("camera", _camera_block),
    ("config", _config_block),
    ("device", _device_block),
    ("sample", _sample_block),
    ("run",    _run_block),
)


# ── Public surface ──────────────────────────────────────────────────────────

def collect(cam=None, cfg=None,
            sample_rowid: Optional[int] = None) -> Dict[str, Any]:
    """
    Gather every metadata block into one JSON-serializable dict.

    Never raises. A block that fails is stored as {"error": "..."} so the
    capture completes and the failure is visible in the archive afterwards —
    losing a run because the `samples` table was locked would be a poor trade.

    Args:
        cam: The live BayerCam, or None.
        cfg: The CaptureConfig for this run, or None.
        sample_rowid: `samples.rowid` this capture belongs to, or None for a
            capture launched from the menu rather than a protocol run.

    Returns:
        The metadata dict described in the module docstring.

    Example:
        >>> meta = collect(cam, cfg, sample_rowid=7)     # doctest: +SKIP
        >>> meta["roi"]["bound"]["w"]                    # doctest: +SKIP
        196
    """
    ctx = MetaContext(cam=cam, cfg=cfg, sample_rowid=sample_rowid)

    meta: Dict[str, Any] = {
        "meta_version": META_VERSION,
        "written_dt":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    for name, builder in BLOCKS:
        try:
            meta[name] = builder(ctx)
        except Exception as e:          # noqa: BLE001 — never fail a capture
            log.error("capture_meta: block %r failed: %s", name, e)
            meta[name] = {"error": str(e)}
    return meta


# The values every reader needs before it can interpret a single array, kept
# as plain attributes so the common case costs no JSON parsing. Each entry is
# (attr name, path through the meta dict).
ROOT_ATTR_PATHS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("bound_x",       ("roi", "bound", "x")),
    ("bound_y",       ("roi", "bound", "y")),
    ("bound_w",       ("roi", "bound", "w")),
    ("bound_h",       ("roi", "bound", "h")),
    ("frame_w",       ("roi", "frame_w")),
    ("frame_h",       ("roi", "frame_h")),
    ("sensor_model",  ("camera", "model")),
    ("bayer_pattern", ("camera", "bayer_pattern")),
    ("hardware_sn",   ("device", "hardware_sn")),
    ("software_ver",  ("device", "software_ver")),
    ("samp_no",       ("sample", "samp_no")),
    ("meta_version",  ("meta_version",)),
)


def root_attrs(meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    Flatten the essential values out of `meta` for storage as HDF5 attributes.

    HDF5 attributes cannot hold None, so a missing value is omitted rather
    than written as a placeholder a reader would have to recognize. Callers
    should therefore treat every one of these as optional.

    Args:
        meta: The dict returned by collect().

    Returns:
        {attr name: scalar}, containing only the values actually present.

    Example:
        >>> root_attrs({"roi": {"bound": {"x": 1082}}})
        {'bound_x': 1082}
    """
    out: Dict[str, Any] = {}
    for name, path in ROOT_ATTR_PATHS:
        node: Any = meta
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if node is not None:
            out[name] = node
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Self-test — the pure logic runs without a camera; the table reads run
# against the live database read-only.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL,
                        format="%(levelname)-8s [%(name)s] %(message)s")

    def check(label, got, want):
        ok = "OK " if got == want else "XX "
        print(f"  {ok}{label:50s} got={got!r}")
        assert got == want, f"{label}: {got!r} != {want!r}"

    class FakeCam:
        model = "imx708"
        model_description = "Camera Module 3 (IMX708)"
        has_autofocus = True
        bayer_pattern = "RGGB"
        MAX_EXPOSURE_US = 112_000_000
        lens_position = 7.45
        config_size = (2304, 1296)
        config = {"size": (2304, 1296), "format": "SBGGR10_CSI2P"}

    class FakeCfg:
        mode = "calibration"
        exposure_group = "3"
        exposure_ms = 20.0
        led_colors = ["amber", "blue"]
        focus_diopter = None

    print("== _fields applies the spec, and survives a bad accessor ==")
    spec = (("good", lambda o: o.model), ("bad", lambda o: o.nope))
    check("good field read", _fields(spec, FakeCam())["good"], "imx708")
    check("bad field is None", _fields(spec, FakeCam())["bad"], None)
    check("None source gives {}", _fields(spec, None), {})

    print("== camera block ==")
    cam_block = _camera_block(MetaContext(cam=FakeCam()))
    check("model", cam_block["model"], "imx708")
    check("stream size split out", (cam_block["stream_w"],
                                    cam_block["stream_h"]), (2304, 1296))
    check("every declared field present",
          sorted(cam_block) == sorted(k for k, _ in CAMERA_FIELDS), True)

    print("== run block ==")
    run_block = _run_block(MetaContext(cfg=FakeCfg()))
    check("mode", run_block["mode"], "calibration")
    check("exposure_group", run_block["exposure_group"], "3")

    print("== roi block reads the live table ==")
    roi_block = _roi_block(MetaContext(cam=FakeCam()))
    check("frame size from the camera",
          (roi_block["frame_w"], roi_block["frame_h"]), (2304, 1296))
    check("bound is called out", roi_block["bound"] is not None, True)
    check("bound has all four values",
          sorted(roi_block["bound"]), ["h", "role", "w", "x", "y"])
    check("bound also in rects", "bound" in roi_block["rects"], True)
    check("sample roi carries its role",
          roi_block["rects"]["sample"]["role"], "sample")

    print("== device block reads the config table ==")
    dev = _device_block(MetaContext())
    check("device names match the spec", sorted(dev), sorted(DEVICE_NAMES))
    check("serial is a non-empty string", bool(dev["hardware_sn"]), True)

    print("== config block is a full snapshot ==")
    cfgblk = _config_block(MetaContext())
    check("roles present", "capture" in cfgblk and "device" in cfgblk, True)
    check("values are text",
          all(isinstance(v, str)
              for names in cfgblk.values() for v in names.values()), True)
    check("agrees with the device block",
          cfgblk["device"]["hardware_sn"], dev["hardware_sn"])

    print("== sample block ==")
    check("row shape matches the spec",
          sorted(_row_dict("samples", SAMPLE_COLUMNS, 1)) in
          ([], sorted(SAMPLE_COLUMNS)), True)
    check("no sample is a normal state",
          _sample_block(MetaContext(sample_rowid=None)), {})

    print("== collect assembles every block ==")
    meta = collect(cam=FakeCam(), cfg=FakeCfg(), sample_rowid=1)
    check("every block present",
          all(name in meta for name, _ in BLOCKS), True)
    check("meta_version stamped", meta["meta_version"], META_VERSION)
    check("written_dt stamped", len(meta["written_dt"]), 19)
    import json
    check("round-trips through JSON",
          json.loads(json.dumps(meta))["camera"]["model"], "imx708")

    print("== collect never raises ==")
    class Exploding:
        @property
        def config(self):
            raise RuntimeError("camera is gone")
        model = "imx708"
    broken = collect(cam=Exploding())
    check("the failing block is marked",
          "error" in broken["roi"], True)
    check("other blocks still built", "device" in broken, True)

    print("== root_attrs ==")
    attrs = root_attrs(meta)
    check("bound x is flattened", attrs["bound_x"], meta["roi"]["bound"]["x"])
    check("hardware_sn is flattened",
          attrs["hardware_sn"], meta["device"]["hardware_sn"])
    check("no None values survive",
          all(v is not None for v in attrs.values()), True)
    check("missing values are omitted, not None",
          "samp_no" in root_attrs({"sample": {"samp_no": None}}), False)
    check("empty meta gives empty attrs", root_attrs({}), {})

    print("\n== all capture_meta self-tests passed ==")
