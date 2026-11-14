"""
exposure_rule_pi_check.py  --  Try the strip exposure rule on the Pi.
--------------------------------------------------------------------
Detour stage 3 of docs/work_plan_chain_poc_2026-11-09.md.

Runs a pre-image and then a measurement (appended to the same archive) on
whatever strip is in the cassette, with the strip exposure rule
(src/exposure_rule.py), and prints for each LED: where it started, each try,
the Tus it settled on, Bus if one was needed, what Tus and Bus left
uncovered, and how long the LED was on.

The pre-image sweeps the LEDs in capture.preimage_led_order; the measurement
sweeps those too, unless `all` is given, in which case it sweeps every LED
in capture.led_color_order (white last, as always).

Archives go to ~/pi/Colorimeter/captures_exposure_test/, never captures/,
so they cannot be mistaken for a sample's.

Run on the Pi, with the Colorimeter program stopped (it holds the camera):

    # in an ssh session on the Pi, from ~/pi/Colorimeter
    sudo systemctl stop colorimeter
    timeout -k 10 300 .venv/bin/python3 -u dev/exposure_rule_pi_check.py [all]
    sudo systemctl start colorimeter
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import h5py

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import capture_runner as cr                                   # noqa: E402
import config_repo as config                                  # noqa: E402
from bayer_cam import BayerCam                                # noqa: E402
from capture_runner import CaptureConfig, CaptureRunner       # noqa: E402
from lighting_cal8 import LEDBank                             # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "captures_exposure_test"


def run_sweep(cam, leds, mode, colors, archive_path=None):
    """
    Run one sweep to completion with the strip rule.

    Args:
        cam, leds:    The open camera and LED bank (shared by both sweeps).
        mode:         cr.MODE_PRE or cr.MODE_NORMAL.
        colors:       LED color names to sweep, in order.
        archive_path: For the measurement, the pre-image's archive.

    Returns:
        The finished CaptureRunner.
    """
    cfg = CaptureConfig(mode=mode, exposure_group="1", led_colors=colors,
                        out_dir=OUT_DIR, archive_path=archive_path,
                        exposure_rule=cr.RULE_STRIP)
    runner = CaptureRunner(cam, leds, cfg)
    for _ in runner.run_prefetch():
        pass
    for _ in runner.run_capture():
        pass
    return runner


def _seconds(a, b):
    """Seconds from timestamp text a to b ('YYYY-mm-dd HH:MM:SS.fff')."""
    fmt = "%Y-%m-%d %H:%M:%S.%f"
    try:
        return (datetime.strptime(b, fmt) - datetime.strptime(a, fmt)
                ).total_seconds()
    except (TypeError, ValueError):
        return float("nan")


def report(path, section):
    """Print one line per try and a summary per LED for one section."""
    label = "pre " if section == "/pre" else "post"
    with h5py.File(path, "r") as f:
        base = f"{section}/leds"
        if base not in f:
            print(f"  {label}: no LEDs")
            return
        for color in f[base]:
            meta = json.loads(f[f"{base}/{color}/led_metadata"][()])
            rec = meta.get("strip_rule")
            if rec is None:
                continue
            on_s = _seconds(meta.get("led_on_dt"), meta.get("led_off_dt"))
            print(f"  {label} {color:<10} seed {rec['seed_us']:>6} µs "
                  f"({rec['seed_source']}), LED on {on_s:.2f} s, "
                  f"region {rec['test_region']['pad_source']}")
            for a in rec["attempts"]:
                st = a["stats"][a["decided_by"]]
                print(f"      try {a['attempt']}: {a['exposure_us']:>6} µs "
                      f"(applied {a['applied_us']}) {a['verdict']:<7} "
                      f"{a['decided_by']} max {st['max_dn']:.0f} "
                      f"({st['max_where']}), min {st['min_dn']:.0f}, "
                      f"clipped {st['n_clip_top']}, below floor "
                      f"{st['n_below_floor']}")
            bus = (f"Bus {rec['bus_us']} µs on {rec['bus_layer']} "
                   f"(darkest {rec['bus_min_dn']})" if rec["bus_us"]
                   else "no Bus")
            short = {ch: c["n_below_floor_both"]
                     for ch, c in rec["coverage"].items()
                     if c["n_below_floor_both"] or c["n_clip_top"]}
            print(f"      Tus {rec['tus_us']} µs {rec['tus_status']}, "
                  f"{rec['frames_at_tus']} frame(s); {bus}; "
                  f"uncovered {short or 'none'}")


def main():
    every = len(sys.argv) > 1 and sys.argv[1] == "all"
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    OUT_DIR.mkdir(exist_ok=True)
    pre_colors = config.capture_preimage_led_order()
    post_colors = (config.capture_led_color_order() if every
                   else list(pre_colors))
    cam, leds = BayerCam(), LEDBank()
    try:
        pre = run_sweep(cam, leds, cr.MODE_PRE, pre_colors)
        post = run_sweep(cam, leds, cr.MODE_NORMAL, post_colors,
                         archive_path=pre.out_path)
        print(f"archive: {pre.out_path}", flush=True)
        print(f"pad: pre {pre.pad_problem or 'found'}, "
              f"post {post.pad_problem or 'found'}")
        report(pre.out_path, "/pre")
        report(pre.out_path, "")
    finally:
        try:
            leds.close()                  # every LED off
        finally:
            cam.stop()


if __name__ == "__main__":
    main()
