"""
padfind_pi_check.py  --  Try pad finding on the Pi with the real camera and LEDs.
---------------------------------------------------------------------------------
Detour stage 1 of docs/work_plan_chain_poc_2026-11-09.md.

Runs a pre-image and then a post-image (a normal sweep appended to the same
archive) on whatever strip is in the cassette, and prints where the pad was
found and how long it took.  Each sweep takes only photo_red, the least light
that still exercises the whole path: dark frames, the pad-finding flash, one
stored frame.  The normal sweep also ends with its usual white-LED photo.

Archives go to ~/pi/Colorimeter/captures_padfind_test/, never captures/, so
they cannot be mistaken for a sample's.

Run on the Pi, with the Colorimeter program stopped (it holds the camera):

    # user, in an ssh session on the Pi, from ~/pi/Colorimeter
    sudo systemctl stop colorimeter
    timeout -k 10 300 .venv/bin/python3 -u dev/padfind_pi_check.py [repeats]
    sudo systemctl start colorimeter

`repeats` (default 2) is how many pre + post pairs to run, so the timings
show their spread.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import capture_runner as cr                                   # noqa: E402
from bayer_cam import BayerCam                                # noqa: E402
from capture_runner import CaptureConfig, CaptureRunner       # noqa: E402
from lighting_cal8 import LEDBank                             # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "captures_padfind_test"


def run_sweep(cam, leds, mode, archive_path=None):
    """
    Run one sweep to completion and report its pad.

    Args:
        cam, leds:    The open camera and LED bank (shared by both sweeps).
        mode:         cr.MODE_PRE or cr.MODE_NORMAL.
        archive_path: For the post-image, the pre-image's archive.

    Returns:
        The finished CaptureRunner (its out_path, pad, pad_problem, prof).
    """
    cfg = CaptureConfig(mode=mode, exposure_group="1",
                        led_colors=["photo_red"], out_dir=OUT_DIR,
                        archive_path=archive_path)
    runner = CaptureRunner(cam, leds, cfg)
    for _ in runner.run_prefetch():       # camera set-up, focus, archive
        pass
    for _ in runner.run_capture():        # darks (pre), pad, sweep
        pass
    label = "pre " if mode == cr.MODE_PRE else "post"
    if runner.pad is not None:
        print(f"  {label} pad: {runner.pad.describe()}")
    print(f"  {label} problem: {runner.pad_problem or '(none)'}")
    return runner


def main():
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    OUT_DIR.mkdir(exist_ok=True)
    cam, leds = BayerCam(), LEDBank()
    try:
        for k in range(1, repeats + 1):
            print(f"=== pair {k} of {repeats} ===", flush=True)
            pre = run_sweep(cam, leds, cr.MODE_PRE)
            run_sweep(cam, leds, cr.MODE_NORMAL, archive_path=pre.out_path)
            print(f"  archive: {pre.out_path}", flush=True)
    finally:
        try:
            leds.close()                  # every LED off
        finally:
            cam.stop()


if __name__ == "__main__":
    main()
