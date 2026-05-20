#!/usr/bin/env python3
"""
webtest2.py - Lighting-study web interface for the Colorimeter project.

Purpose: evaluate different lighting setups by capturing images with
controlled LED configurations and uploading results (JPG, NPY, data file)
to a desktop host via sftp.

Usage:
    python webtest2.py
    # Open http://<hostname>:5000 in a browser

Persistent state lives in webtest2_config.json (same directory as this
script).  It stores: named rectangles, global sequence number, per-camera
exposure settings, and UI checkbox/field state.

Upload uses Fabric (pip install fabric) with the pre-configured 'sunny'
ssh host alias.  Failed uploads are written to the SD card and logged in
webtest2_upload_failures.log.
"""

import atexit
import datetime
import json
import logging
import os
import signal
import sys
import time
from io import BytesIO
from pathlib import Path

# src/ on path for lighting, bayer_cam, and their dependencies
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
from flask import Flask, redirect, render_template, request, url_for

from lighting import LedController, LED_ORDER, LED_PINS
from bayer_cam import BayerCam

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE        = Path(__file__).parent
_CONFIG_PATH = _HERE / "webtest2_config.json"
_FAIL_LOG    = _HERE / "webtest2_upload_failures.log"
_FALLBACK_DIR = _HERE / "captures2"

# ---------------------------------------------------------------------------
# Persistent config
# ---------------------------------------------------------------------------

_CONFIG_DEFAULTS = {
    "sequence":  0,
    "rects": [
        {"name": "full", "left": 0, "top": 0, "width": 0, "height": 0},
    ],
    # Camera exposure stored per model so switching cameras keeps settings.
    "camera_settings": {},
    # Data fields persisted across restarts
    "data": {
        "title":       "",
        "basename":    "img",
        "led_desc":    "",
        "geometry":    "",
        "target":      "",
        "vcc":         "",
        "vled":        "",
        "vr":          "",
        "ma":          "",
        "r_ohm":       "",
        "remarks":     "",
        "focal_mm":    "",
        "box_dim_x":   "",
        "box_dim_y":   "",
        "box_lining":  "",
        "light_x":     "",
        "light_y":     "",
        "light_z":     "",
        "light_ax":    "",
        "light_ay":    "",
    },
    # Upload checkboxes
    "upload_jpg":  True,
    "upload_npy":  True,
    "upload_data": True,
    # Upload destination
    "sftp_host":   "sunny",
    "sftp_remote": "picam/", # /home/upload/picam
}


def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH) as f:
                saved = json.load(f)
            # Merge so new keys from defaults are always present
            cfg = dict(_CONFIG_DEFAULTS)
            cfg.update(saved)
            cfg["data"] = dict(_CONFIG_DEFAULTS["data"])
            cfg["data"].update(saved.get("data", {}))
            return cfg
        except Exception:
            pass
    return dict(_CONFIG_DEFAULTS)


def _save_config(cfg: dict) -> None:
    try:
        with open(_CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        _fail_log(f"Could not save config: {e}")


def _fail_log(msg: str) -> None:
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    line = f"{ts}  {msg}\n"
    try:
        with open(_FAIL_LOG, "a") as f:
            f.write(line)
    except Exception:
        pass
    logging.warning(msg)


# ---------------------------------------------------------------------------
# Hardware init
# ---------------------------------------------------------------------------

cam  = BayerCam()
leds = LedController()

try:
    cam.setup_camera()
    _cam_ready = True
    _cam_error = None
except Exception as _e:
    _cam_ready = False
    _cam_error = str(_e)
    print(f"  WARNING: camera init failed: {_e}")

# ---------------------------------------------------------------------------
# Load config and reconcile rect "full" size with actual camera
# ---------------------------------------------------------------------------

cfg = _load_config()

IMAGE_W, IMAGE_H = cam.config["size"]

# Fix up the built-in "full" rect to match the actual camera size
for r in cfg["rects"]:
    if r["name"] == "full":
        r["width"]  = IMAGE_W
        r["height"] = IMAGE_H

# Apply persisted camera settings for this model (if any)
_cam_cfg = cfg["camera_settings"].get(cam.model, {})
_exposure_ms = float(_cam_cfg.get("exposure_ms", 100.0))

# ---------------------------------------------------------------------------
# Cleanup and signal handlers
# ---------------------------------------------------------------------------

_cleaned_up = False


def _cleanup():
    global _cleaned_up
    if _cleaned_up:
        return
    _cleaned_up = True
    print("\nCleaning up GPIO and camera...")
    try:
        leds.all_off()
        leds.close()
    except Exception:
        pass
    try:
        cam.stop()
    except Exception:
        pass
    print("Done.")


def _exit_handler(*_):
    _cleanup()
    sys.exit(0)


def _sigtstp_handler(*_):
    _cleanup()
    signal.signal(signal.SIGTSTP, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGTSTP)


atexit.register(_cleanup)
signal.signal(signal.SIGTERM,  _exit_handler)
signal.signal(signal.SIGHUP,   _exit_handler)
signal.signal(signal.SIGTSTP,  _sigtstp_handler)

# ---------------------------------------------------------------------------
# Shared runtime state  (not persisted — reset each run)
# ---------------------------------------------------------------------------

state = {
    "section":       "camera",
    "led_state":     {c: False for c in LED_ORDER},
    "exposure_ms":   _exposure_ms,
    "last_result":   None,   # dict from _do_capture()
    "last_bayer":    None,   # np.ndarray kept for histogram route
    "last_error":    _cam_error,
    "last_msg":      None,
}

# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

_PERCENTILES = [0, 10, 25, 50, 75, 90, 100]


def _channel_stats(arr: np.ndarray) -> dict:
    """
    Compute detailed statistics for a single-channel uint16 array.

    Returns a dict with keys:
        total, min_val, max_val, min_count, max_count, mean,
        p0, p10, p25, p50, p75, p90, p100
    """
    flat      = arr.ravel()
    total     = int(flat.size)
    min_val   = int(flat.min())
    max_val   = int(flat.max())
    min_count = int(np.sum(flat == min_val))
    max_count = int(np.sum(flat == max_val))
    mean      = float(flat.mean())
    pcts      = np.percentile(flat, _PERCENTILES)
    return {
        "total":     total,
        "min_val":   min_val,
        "max_val":   max_val,
        "min_count": min_count,
        "max_count": max_count,
        "mean":      round(mean, 2),
        "p0":   int(pcts[0]),
        "p10":  int(pcts[1]),
        "p25":  int(pcts[2]),
        "p50":  int(pcts[3]),
        "p75":  int(pcts[4]),
        "p90":  int(pcts[5]),
        "p100": int(pcts[6]),
    }


def _rect_stats(bayer: np.ndarray, rect: dict) -> dict:
    """
    Compute per-channel stats for a named rectangle of a Bayer array.

    Returns dict with keys: name, channels {B, Gb, Gr, R} → _channel_stats dict.
    """
    l, t = rect["left"], rect["top"]
    w, h = rect["width"], rect["height"]
    roi    = bayer[t:t + h, l:l + w]
    layers = BayerCam.extract_bayer_layers(roi, cam.bayer_pattern)
    return {
        "name":     rect["name"],
        "channels": {ch: _channel_stats(a) for ch, a in layers.items()},
    }


# ---------------------------------------------------------------------------
# Capture helper
# ---------------------------------------------------------------------------

def _do_capture(exposure_ms: float) -> dict:
    """
    Apply exposure, capture one frame, compute stats for all rects.

    Updates state["last_result"] and state["last_bayer"].
    Returns the result dict.
    """
    exposure_us = int(exposure_ms * 1000)
    cam.set_exposure(exposure_us)
    time.sleep(0.3)   # allow AGC/exposure to settle

    rgb, bayer, meta = cam.capture_frame()

    ts      = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    active  = [c for c, on in state["led_state"].items() if on]

    rect_stats = [_rect_stats(bayer, r) for r in cfg["rects"]]

    result = {
        "timestamp":    ts,
        "exposure_ms":  exposure_ms,
        "actual_ms":    meta.get("ExposureTime", 0) / 1000,
        "frame_dur_us": meta.get("FrameDuration", 0),
        "analog_gain":  meta.get("AnalogueGain", cam.analog_gain),
        "leds":         active,
        "lens_position": cam.lens_position,
        "rgb":          rgb,          # kept for JPEG encoding at upload time
        "bayer":        bayer,
        "meta":         meta,
        "rect_stats":   rect_stats,
    }
    state["last_result"] = result
    state["last_bayer"]  = bayer
    return result


# ---------------------------------------------------------------------------
# Data file assembly
# ---------------------------------------------------------------------------

def _assemble_txt(result: dict, filenames: dict) -> str:
    """
    Build the text data file content for a capture.

    Includes: timestamp, camera settings, data fields, circuit values,
    LED state, image filenames, and full per-rect channel statistics.

    Args:
        result:    dict from _do_capture()
        filenames: dict mapping type → filename, e.g. {"jpg": "...", "npy": "..."}

    Returns:
        Multi-line string suitable for writing to a .txt file.
    """
    d = cfg["data"]
    lines = [
        "=== webtest2 capture ===",
        f"timestamp:     {result['timestamp']}",
        f"title:         {d['title']}",
        f"basename:      {d['basename']}",
        f"remarks:       {d['remarks']}",
        "",
        "--- setup ---",
        f"led_desc:      {d['led_desc']}",
        f"geometry:      {d['geometry']}",
        f"target:        {d['target']}",
        f"focal_mm:      {d['focal_mm']}",
        f"box_dim:       ({d['box_dim_x']}, {d['box_dim_y']})",
        f"box_lining:    {d['box_lining']}",
        f"light_coords:  ({d['light_x']}, {d['light_y']}, {d['light_z']}, {d['light_ax']}, {d['light_ay']})",
        f"vcc:           {d['vcc']}",
        f"vled:          {d['vled']}",
        f"vr:            {d['vr']}",
        f"ma:            {d['ma']}",
        f"r_ohm:         {d['r_ohm']}",
        "",
        "--- camera ---",
        f"camera_model:  {cam.model_description}",
        f"exposure_ms:   {result['exposure_ms']}",
        f"actual_ms:     {result['actual_ms']:.3f}",
        f"frame_dur_us:  {result['frame_dur_us']}",
        f"analog_gain:   {result['analog_gain']}",
        f"bayer_pattern: {cam.bayer_pattern}",
    ]
    if result["lens_position"] is not None:
        lines.append(f"lens_position: {result['lens_position']:.4f}")
    lines += [
        "",
        "--- lights ---",
        f"active_leds:   {', '.join(result['leds']) if result['leds'] else 'none'}",
        "",
        "--- files ---",
    ]
    for ftype, fname in filenames.items():
        lines.append(f"{ftype}_file:  {fname}")
    lines.append("")
    lines.append("--- statistics ---")
    lines.append(
        f"{'rect':<12} {'ch':<4} {'total':>7} {'min_v':>6} {'max_v':>6} "
        f"{'min_n':>6} {'max_n':>6} {'mean':>8} "
        f"{'p0':>5} {'p10':>5} {'p25':>5} {'p50':>5} "
        f"{'p75':>5} {'p90':>5} {'p100':>5}"
    )
    lines.append("-" * 105)
    for rs in result["rect_stats"]:
        for ch in ["B", "Gb", "Gr", "R"]:
            s = rs["channels"][ch]
            lines.append(
                f"{rs['name']:<12} {ch:<4} {s['total']:>7} {s['min_val']:>6} "
                f"{s['max_val']:>6} {s['min_count']:>6} {s['max_count']:>6} "
                f"{s['mean']:>8.2f} "
                f"{s['p0']:>5} {s['p10']:>5} {s['p25']:>5} {s['p50']:>5} "
                f"{s['p75']:>5} {s['p90']:>5} {s['p100']:>5}"
            )
    lines.append("")
    lines.append("--- picamera2_metadata ---")
    for k, v in result["meta"].items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Upload helper
# ---------------------------------------------------------------------------

def _make_stem(sequence: int) -> str:
    """Build base filename stem from current data fields and sequence number."""
    d         = cfg["data"]
    basename  = d["basename"] or "img"
    color_ltr = (d["led_desc"][:1] or "N").upper()
    tgt2      = (d["target"][:2]   or "xx")
    geo0      = (d["geometry"].split() or ["--"])[0]
    return f"{basename}{color_ltr}{tgt2}_{geo0}_{sequence:04d}"


def _do_upload() -> dict:
    """
    Assign sequence number, build filenames, and upload checked items via sftp.

    Upload order: jpg → npy → txt (data file).
    On any sftp failure: writes pending files to SD card and logs the failure.

    Returns:
        dict with keys: stem, sequence, uploaded (list), written_local (list),
        sftp_ok (bool), error (str or None)
    """
    result = state["last_result"]
    if result is None:
        raise RuntimeError("No capture to upload — run Capture first.")

    cfg["sequence"] += 1
    seq  = cfg["sequence"]
    stem = _make_stem(seq)

    # Decide what to send
    upload_jpg  = cfg["upload_jpg"]
    upload_npy  = cfg["upload_npy"]
    upload_data = cfg["upload_data"]

    # Build in-memory payloads
    payloads = {}   # ext → BytesIO or bytes

    filenames = {}  # ext → remote filename (for embedding in data file)

    if upload_jpg:
        import cv2
        ok, buf = cv2.imencode(".jpg", result["rgb"])
        if ok:
            payloads["jpg"] = buf.tobytes()
            filenames["jpg"] = stem + ".jpg"

    if upload_npy:
        buf = BytesIO()
        np.save(buf, result["bayer"])
        payloads["npy"] = buf.getvalue()
        filenames["npy"] = stem + ".npy"

    if upload_data:
        txt = _assemble_txt(result, filenames)
        payloads["txt"] = txt.encode()
        filenames["txt"] = stem + ".txt"

    remote_dir = cfg["sftp_remote"].rstrip("/") + "/"
    sftp_ok    = False
    error      = None
    uploaded   = []
    written_local = []

    # Attempt sftp via Fabric
    try:
        from fabric import Connection
        with Connection(cfg["sftp_host"]) as conn:
            for ext, data in payloads.items():
                remote_path = remote_dir + filenames[ext]
                conn.put(BytesIO(data) if isinstance(data, bytes) else BytesIO(data),
                         remote=remote_path)
                uploaded.append(filenames[ext])
                print(f"Uploaded to /home/upload/{remote_path}")
        sftp_ok = True
    except Exception as e:
        error = str(e)
        _fail_log(f"sftp upload failed (seq={seq}, stem={stem}): {e}")

        # Write files to SD card as fallback
        _FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        for ext, data in payloads.items():
            local_path = _FALLBACK_DIR / filenames[ext]
            try:
                local_path.write_bytes(
                    data if isinstance(data, bytes) else data
                )
                written_local.append(str(local_path))
                _fail_log(f"  saved locally: {local_path}")
            except Exception as write_err:
                _fail_log(f"  also failed local write: {write_err}")

    _save_config(cfg)

    return {
        "stem":          stem,
        "sequence":      seq,
        "uploaded":      uploaded,
        "written_local": written_local,
        "sftp_ok":       sftp_ok,
        "error":         error,
    }


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__, template_folder="templates")


@app.template_filter("enumerate")
def _enumerate_filter(iterable):
    return enumerate(iterable)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    sec = request.args.get("section", state["section"])
    state["section"] = sec
    return render_template(
        "webtest2.html",
        state=state,
        cfg=cfg,
        led_order=LED_ORDER,
        has_autofocus=cam.has_autofocus,
        image_w=IMAGE_W,
        image_h=IMAGE_H,
        cam_desc=cam.model_description,
        config_path=str(_CONFIG_PATH),
        fail_log=str(_FAIL_LOG),
    )


def _read_data_fields():
    """Read all data fields from the current request form into cfg['data']."""
    for key in cfg["data"]:
        val = request.form.get(key, cfg["data"][key])
        cfg["data"][key] = val


@app.route("/capture", methods=["POST"])
def capture():
    state["last_error"] = None
    state["last_msg"]   = None
    try:
        _read_data_fields()
        exp_ms = float(request.form.get("exposure_ms", state["exposure_ms"]))
        state["exposure_ms"] = exp_ms
        # Persist exposure per camera model
        cfg["camera_settings"].setdefault(cam.model, {})["exposure_ms"] = exp_ms
        _save_config(cfg)
        _do_capture(exp_ms)
        state["last_msg"] = f"Captured at {exp_ms:.1f} ms — {datetime.datetime.now().strftime('%H:%M:%S')}"
    except Exception as exc:
        state["last_error"] = str(exc)
    return redirect(url_for("index", section="camera"))


@app.route("/upload", methods=["POST"])
def upload():
    state["last_error"] = None
    state["last_msg"]   = None
    # Read checkboxes from form (unchecked boxes are absent from POST)
    cfg["upload_jpg"]  = "upload_jpg"  in request.form
    cfg["upload_npy"]  = "upload_npy"  in request.form
    cfg["upload_data"] = "upload_data" in request.form
    # Read data fields
    _read_data_fields()
    try:
        info = _do_upload()
        if info["sftp_ok"]:
            state["last_msg"] = (
                f"Uploaded {len(info['uploaded'])} file(s) as {info['stem']} "
                f"(seq {info['sequence']})"
            )
        else:
            state["last_error"] = (
                f"sftp failed — saved {len(info['written_local'])} file(s) locally. "
                f"Error: {info['error']}"
            )
    except Exception as exc:
        state["last_error"] = str(exc)
    return redirect(url_for("index", section="camera"))


@app.route("/leds", methods=["POST"])
def led_control():
    action = request.form.get("action", "")
    color  = request.form.get("color", "")
    if action == "all_off":
        leds.all_off()
        state["led_state"] = {c: False for c in LED_ORDER}
    elif color in LED_PINS:
        new_state = not state["led_state"].get(color, False)
        if new_state:
            leds.on(color)
        else:
            leds.off(color)
        state["led_state"][color] = new_state
    return redirect(url_for("index", section="lights"))


@app.route("/autofocus", methods=["POST"])
def autofocus():
    state["last_error"] = None
    state["last_msg"]   = None
    try:
        pos = cam.autofocus_once()
        state["last_msg"] = f"Autofocus complete — lens position {pos:.3f}"
    except Exception as exc:
        state["last_error"] = str(exc)
    return redirect(url_for("index", section="camera"))


@app.route("/settings", methods=["POST"])
def settings():
    state["last_error"] = None
    state["last_msg"]   = None
    cfg["sftp_host"]   = request.form.get("sftp_host",   cfg["sftp_host"])
    cfg["sftp_remote"] = request.form.get("sftp_remote", cfg["sftp_remote"])
    _save_config(cfg)
    state["last_msg"] = "Settings saved."
    return redirect(url_for("index", section="settings"))


@app.route("/terminate", methods=["POST"])
def terminate():
    """Clean up hardware and exit the Flask process."""
    _cleanup()
    os.kill(os.getpid(), signal.SIGTERM)
    return "Terminating…", 200   # response may not reach browser


def _is_mock() -> bool:
    """True when running without real GPIO (desktop/stub mode)."""
    return not (hasattr(leds, "_leds") and leds._leds)


@app.route("/shutdown", methods=["POST"])
def shutdown():
    """Clean up hardware and power off the Raspberry Pi."""
    _cleanup()
    if _is_mock():
        logging.warning("Shutdown requested but running in stub/mock mode — skipped.")
        state["last_msg"] = "Shutdown skipped (mock mode — no real GPIO)."
        return redirect(url_for("index", section="settings"))
    os.system("sudo shutdown -h now")
    return "Shutting down…", 200


@app.route("/rects", methods=["POST"])
def update_rects():
    rects = []
    i = 0
    while f"rect_name_{i}" in request.form:
        try:
            rects.append({
                "name":   request.form[f"rect_name_{i}"],
                "left":   int(request.form.get(f"rect_left_{i}",   0)),
                "top":    int(request.form.get(f"rect_top_{i}",    0)),
                "width":  int(request.form.get(f"rect_width_{i}",  IMAGE_W)),
                "height": int(request.form.get(f"rect_height_{i}", IMAGE_H)),
            })
        except ValueError:
            pass
        i += 1
    if rects:
        cfg["rects"] = rects
        _save_config(cfg)
    state["last_msg"] = "Rects updated."
    return redirect(url_for("index", section="rects"))


@app.route("/add_rect", methods=["POST"])
def add_rect():
    cfg["rects"].append(
        {"name": f"rect{len(cfg['rects'])}",
         "left": 0, "top": 0, "width": IMAGE_W, "height": IMAGE_H}
    )
    _save_config(cfg)
    return redirect(url_for("index", section="rects"))


@app.route("/del_rect/<int:idx>", methods=["POST"])
def del_rect(idx):
    if 0 <= idx < len(cfg["rects"]) and len(cfg["rects"]) > 1:
        cfg["rects"].pop(idx)
        _save_config(cfg)
    return redirect(url_for("index", section="rects"))


# ---------------------------------------------------------------------------
# Plot routes
# ---------------------------------------------------------------------------

def _png_response(fig):
    import matplotlib.pyplot as plt
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=90, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read(), 200, {"Content-Type": "image/png"}


@app.route("/plot/hist")
def plot_hist():
    """
    Four side-by-side filled histograms, one per Bayer channel (B, Gb, Gr, R).
    20 bins, independent Y axes, filled bars in channel color.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bayer = state["last_bayer"]
    if bayer is None:
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.text(0.5, 0.5, "No data", ha="center", va="center",
                transform=ax.transAxes)
        return _png_response(fig)

    # Extract the rect named "full" if present, otherwise use whole array
    full_rect = next((r for r in cfg["rects"] if r["name"] == "full"), None)
    if full_rect:
        l, t = full_rect["left"], full_rect["top"]
        roi = bayer[t:t + full_rect["height"], l:l + full_rect["width"]]
    else:
        roi = bayer

    layers     = BayerCam.extract_bayer_layers(roi, cam.bayer_pattern)
    ch_colors  = {"B": "#2b6cef", "Gb": "#08d295", "Gr": "#51c52e", "R": "#f12b15"}
    ch_order   = ["B", "Gb", "Gr", "R"]
    bins       = np.linspace(0, 1023, 21)   # 20 bins

    fig, axes = plt.subplots(1, 4, figsize=(10, 3), sharey=False)
    for ax, ch in zip(axes, ch_order):
        data  = layers[ch].ravel()
        color = ch_colors[ch]
        ax.hist(data, bins=bins, color=color, alpha=0.85, edgecolor="none")
        ax.set_title(ch, fontsize=11, color=color)
        ax.set_xlabel("value", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(axis="y", alpha=0.3)
    axes[0].set_ylabel("count", fontsize=8)
    fig.tight_layout(pad=0.8)
    return _png_response(fig)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import socket
    host_ip = socket.gethostbyname(socket.gethostname())
    print(f"\nColorimeter web interface — lighting study")
    print(f"  http://localhost:5000")
    print(f"  http://{host_ip}:5000")
    print(f"  Config     : {_CONFIG_PATH}")
    print(f"  Sequence   : {cfg['sequence']}")
    print(f"  Camera     : {'ready — ' + cam.model_description if _cam_ready else 'FAILED — ' + str(_cam_error)}")
    print(f"  GPIO       : {'real' if hasattr(leds, '_leds') and leds._leds else 'stub'}")
    print()
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False)
