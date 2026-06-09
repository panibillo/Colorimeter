#!/usr/bin/env python3
"""
webtest.py - Colorimeter web interface prototype.

Serves a single-page control interface on the LAN.
Runs unmodified on RPi (real hardware) or desktop (mock camera/GPIO).

Usage:
    python webtest.py
    # Open http://<hostname>:5000 in a browser

Signal handling ensures GPIO and camera are released on all exit paths,
including SSH disconnect (SIGHUP) and Ctrl-Z (SIGTSTP).
"""

import atexit
import datetime
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

from dev.lighting_lightboard1 import LedController, LED_ORDER, LED_PINS
from bayer_cam import BayerCam

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
    os.kill(os.getpid(), signal.SIGTSTP)   # re-raise to actually suspend

atexit.register(_cleanup)
signal.signal(signal.SIGTERM,  _exit_handler)
signal.signal(signal.SIGHUP,   _exit_handler)
signal.signal(signal.SIGTSTP,  _sigtstp_handler)
# SIGINT (Ctrl-C): Flask raises KeyboardInterrupt; Python exits; atexit fires.

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

IMAGE_W, IMAGE_H = cam.config["size"]   # (1640, 1232) for Config 5

state = {
    "section":     "camera",
    "led_state":   {c: False for c in LED_ORDER},
    "last_result": None,    # dict of capture stats (see _capture_one)
    "last_bayer":  None,    # np.ndarray kept for plot routes
    "last_error":  _cam_error,
    "last_msg":    None,
    "output_dir":  str(Path(__file__).parent / "captures"),
    "file_type":   "both",          # "jpeg" | "bayer" | "both"
    "exposure_ms": 100.0,           # single exposure for one-shot captures
    "target_rects": [
        {"name": "full", "left": 0, "top": 0,
         "width": IMAGE_W, "height": IMAGE_H},
    ],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rect_stats(bayer: np.ndarray, rect: dict) -> dict:
    """
    Compute per-channel mean and std for a named region of a Bayer array.

    Args:
        bayer: uint16 array (H, W), BGGR Bayer pattern
        rect:  dict with keys name, left, top, width, height

    Returns:
        dict with "name", "means" {B,Gb,Gr,R}, "stds" {B,Gb,Gr,R}
    """
    l, t = rect["left"], rect["top"]
    w, h = rect["width"], rect["height"]
    roi = bayer[t:t + h, l:l + w]
    layers = BayerCam.extract_bayer_layers(roi)
    return {
        "name":  rect["name"],
        "means": {ch: float(a.mean()) for ch, a in layers.items()},
        "stds":  {ch: float(a.std())  for ch, a in layers.items()},
    }


def _capture_one(exposure_ms: float, stem_prefix: str = "cap") -> dict:
    """
    Capture one frame with the current LED state and given exposure.

    Saves files to state["output_dir"]. Updates state["last_result"]
    and state["last_bayer"].  Returns result dict or raises on failure.

    Args:
        exposure_ms:   Exposure in milliseconds
        stem_prefix:   Prefix for saved filenames

    Returns:
        Result dict (also stored in state["last_result"])
    """
    out_dir = Path(state["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    exposure_us = int(exposure_ms * 1000)
    cam.set_exposure(exposure_us)
    time.sleep(0.5)   # allow camera to settle

    rgb, bayer, meta = cam.capture_frame()

    ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    active = [c for c, on in state["led_state"].items() if on]
    led_tag = "_".join(active) if active else "noled"
    stem = f"{stem_prefix}_{led_tag}_{int(exposure_ms)}ms_{ts}"

    jpeg_only = (state["file_type"] == "jpeg")
    if state["file_type"] == "bayer":
        # Save npy + txt only (no jpg)
        npy_path = out_dir / f"{stem}.npy"
        np.save(str(npy_path), bayer)
        saved = {"npy": npy_path}
    else:
        saved = BayerCam.save_frame(out_dir, stem, rgb, bayer, meta,
                                    jpeg_only=jpeg_only)

    # Full-image Bayer channel stats
    full_layers = BayerCam.extract_bayer_layers(bayer)
    full_means  = {ch: float(a.mean()) for ch, a in full_layers.items()}
    full_stds   = {ch: float(a.std())  for ch, a in full_layers.items()}

    # Per-rect stats
    rect_stats = [_rect_stats(bayer, r) for r in state["target_rects"]
                  if r["name"] != "full"]

    result = {
        "stem":         stem,
        "exposure_ms":  exposure_ms,
        "actual_ms":    meta.get("ExposureTime", 0) / 1000,
        "frame_dur_us": meta.get("FrameDuration", 0),
        "timestamp":    ts,
        "leds":         active,
        "full_means":   full_means,
        "full_stds":    full_stds,
        "rect_stats":   rect_stats,
        "saved_files":  [str(p) for p in saved.values()],
    }
    state["last_result"] = result
    state["last_bayer"]  = bayer
    return result


# ---------------------------------------------------------------------------
# Flask app and routes
# ---------------------------------------------------------------------------

app = Flask(__name__)

@app.template_filter("enumerate")
def _enumerate_filter(iterable):
    return enumerate(iterable)


@app.route("/")
def index():
    sec = request.args.get("section", state["section"])
    state["section"] = sec
    return render_template("index.html", state=state, led_order=LED_ORDER,
                           has_autofocus=cam.has_autofocus)


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


@app.route("/capture", methods=["POST"])
def capture():
    state["last_error"] = None
    state["last_msg"]   = None
    try:
        exp_ms = float(request.form.get("exposure_ms", state["exposure_ms"]))
        state["exposure_ms"] = exp_ms
        state["file_type"]   = request.form.get("file_type", state["file_type"])
        _capture_one(exp_ms)
        state["last_msg"] = f"Captured: {state['last_result']['stem']}"
    except Exception as exc:
        state["last_error"] = str(exc)
    return redirect(url_for("index", section="camera"))


@app.route("/capture_suite", methods=["POST"])
def capture_suite():
    state["last_error"] = None
    state["last_msg"]   = None
    try:
        exp_ms = float(request.form.get("exposure_ms", state["exposure_ms"]))
        state["exposure_ms"] = exp_ms
        state["file_type"]   = request.form.get("file_type", state["file_type"])
        captured = []
        for color in LED_ORDER:
            # Turn on this LED only
            leds.all_off()
            state["led_state"] = {c: (c == color) for c in LED_ORDER}
            leds.on(color)
            time.sleep(0.5)   # warm-up

            result = _capture_one(exp_ms, stem_prefix="suite")
            captured.append(color)

        leds.all_off()
        state["led_state"] = {c: False for c in LED_ORDER}
        state["last_msg"] = f"Suite complete: {', '.join(captured)}"
    except Exception as exc:
        state["last_error"] = str(exc)
        leds.all_off()
        state["led_state"] = {c: False for c in LED_ORDER}
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


@app.route("/settings", methods=["POST"])
def settings():
    state["output_dir"] = request.form.get("output_dir", state["output_dir"])
    state["last_msg"]   = "Settings saved."
    return redirect(url_for("index", section="settings"))


@app.route("/update_rects", methods=["POST"])
def update_rects():
    """Update target rect definitions from the Camera section form."""
    rects = []
    # Form sends: rect_name_0, rect_left_0, rect_top_0, rect_width_0, rect_height_0
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
        state["target_rects"] = rects
    state["last_msg"] = "Rects updated."
    return redirect(url_for("index", section="camera"))


@app.route("/add_rect", methods=["POST"])
def add_rect():
    state["target_rects"].append(
        {"name": f"rect{len(state['target_rects'])}",
         "left": 0, "top": 0, "width": IMAGE_W, "height": IMAGE_H}
    )
    return redirect(url_for("index", section="camera"))


@app.route("/del_rect/<int:idx>", methods=["POST"])
def del_rect(idx):
    if 0 <= idx < len(state["target_rects"]) and len(state["target_rects"]) > 1:
        state["target_rects"].pop(idx)
    return redirect(url_for("index", section="camera"))


# ---------------------------------------------------------------------------
# Plot routes
# ---------------------------------------------------------------------------

def _png_response(fig):
    """Render a matplotlib figure to a PNG HTTP response, then close it."""
    import matplotlib.pyplot as plt
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=90, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read(), 200, {"Content-Type": "image/png"}


@app.route("/plot/means")
def plot_means():
    """Bar chart of full-image Bayer channel means from last capture."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(5, 3))
    if state["last_result"] is None:
        ax.text(0.5, 0.5, "No data", ha="center", va="center",
                transform=ax.transAxes)
    else:
        means = state["last_result"]["full_means"]
        stds  = state["last_result"]["full_stds"]
        colors_map = {"B": "steelblue", "Gb": "seagreen",
                      "Gr": "mediumseagreen", "R": "firebrick"}
        channels = list(means.keys())
        vals = [means[ch] for ch in channels]
        errs = [stds[ch]  for ch in channels]
        cols = [colors_map.get(ch, "gray") for ch in channels]
        ax.bar(channels, vals, yerr=errs, color=cols, capsize=4, width=0.5)
        ax.set_ylabel("Mean pixel value (0–1023)")
        ax.set_title(f"Bayer channel means — {state['last_result']['stem'][:30]}")
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", alpha=0.4)
    return _png_response(fig)


@app.route("/plot/hist")
def plot_hist():
    """Histogram of each Bayer channel from last capture."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 3))
    if state["last_bayer"] is None:
        ax.text(0.5, 0.5, "No data", ha="center", va="center",
                transform=ax.transAxes)
    else:
        layers = BayerCam.extract_bayer_layers(state["last_bayer"])
        colors_map = {"B": "steelblue", "Gb": "seagreen",
                      "Gr": "mediumseagreen", "R": "firebrick"}
        bins = np.linspace(0, 1023, 128)
        for ch, arr in layers.items():
            ax.hist(arr.ravel(), bins=bins, alpha=0.5,
                    color=colors_map.get(ch, "gray"), label=ch,
                    histtype="step", linewidth=1.2)
        ax.set_xlabel("Pixel value (0–1023)")
        ax.set_ylabel("Count")
        ax.set_title("Bayer channel histogram")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
    return _png_response(fig)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import socket
    host_ip = socket.gethostbyname(socket.gethostname())
    print(f"\nColorimeter web interface")
    print(f"  http://localhost:5000")
    print(f"  http://{host_ip}:5000")
    print(f"  Output dir : {state['output_dir']}")
    print(f"  Camera     : {'ready' if _cam_ready else 'FAILED — ' + str(_cam_error)}")
    print(f"  GPIO       : {'real' if hasattr(leds, '_leds') and leds._leds else 'stub'}")
    print()
    app.run(host="0.0.0.0", port=5000, threaded=False, debug=False)
