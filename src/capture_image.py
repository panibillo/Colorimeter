"""
capture_image.py  —  Take one test image: one LED at chosen exposure rungs.
--------------------------------------------------------------------------
Backs Menu → Advanced → Capture Image.  It lights one LED, photographs the pad
at each chosen rung of that LED's stored exposures (exposure_repo.RUNGS), and
writes the image types the operator ticked into one archive:

    jpg_whole     /leds/<color>/jpeg/<label>_whole     the RGB frame as a JPEG
    jpg_bound     /leds/<color>/jpeg/<label>_bound     its bound crop
    bayer_whole   /leds/<color>/lit_whole/<label>/     whole-frame Bayer layers
    bayer_bound   /leds/<color>/lit/<label>/           bound Bayer layers, where
                                                       normal captures put them
    darkframes    /dark/ and /dark_whole/, one per exposure, LEDs off, in
                  whichever Bayer forms were ticked, linked from each LED

The label names the rung: '0' -> 'r0', '1/4' -> 'r1_4'.  Saving is the last
step: the archive is written as <name>.h5.part and renamed to <name>.h5 only
once it is complete, then its row goes into the test_image table.  A run that
fails leaves no file under the real name and no row.

    runner = CaptureImageRunner(cam, leds, choice)
    for progress in runner.run():
        ...                                    # repaint, poll input
    runner.out_path                            # the saved file
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional

import capture_runner as cr
import config_repo as config
import exposure_repo
import roi
import test_image_repo
from bayer_cam import BayerCam
from capture_h5 import CaptureSuiteWriter
from capture_runner import CaptureConfig, CaptureRunner, Progress

log = logging.getLogger(__name__)

IMAGE_TYPES = ("jpg_whole", "jpg_bound", "bayer_whole", "bayer_bound", "darkframes")
DESCRIPTION = "colorimeter test image"


def rung_label(rung: str) -> str:
    """'0' -> 'r0', '1/4' -> 'r1_4': a rung as an HDF5 group name."""
    return "r" + rung.replace("/", "_")


@dataclass
class ImageChoice:
    """What the operator chose on the Capture Image screen."""
    led: str
    rungs: List[str]                       # in exposure_repo.RUNGS order
    types: List[str]                       # some of IMAGE_TYPES
    name: str = test_image_repo.TEMPLATE   # file name without .h5
    description: str = ""
    directory: Optional[Path] = None       # None: config.capture_test_image_dir()


def choice_problem(choice: ImageChoice, exposures: "dict[str, int]") -> Optional[str]:
    """
    Say why `choice` cannot be captured, or None if it can.  Reads only what
    is passed in, so the screen can call it on every repaint.

    Args:
        exposures: exposure_repo.rung_exposures(choice.led).
    """
    if not choice.rungs:
        return "Tap one or more exposure rungs"
    missing = [r for r in choice.rungs if r not in exposures]
    if missing:
        return f"{choice.led} has no stored rung {' '.join(missing)}"
    images = [t for t in choice.types if t != "darkframes"]
    if not images:
        return "Tick at least one image type"
    if "darkframes" in choice.types and not any(t.startswith("bayer") for t in images):
        return "Darkframes needs Bayers whole or bound"
    return None


class CaptureImageRunner(CaptureRunner):
    """Captures and saves one test image."""

    def __init__(self, cam, leds, choice: ImageChoice,
                 capture_config: "CaptureConfig | None" = None):
        super().__init__(cam, leds,
                         capture_config or CaptureConfig(led_colors=[choice.led]))
        self.choice = choice
        self.exposures: "dict[str, int]" = {}
        self._part: "Path | None" = None

    def _led_total(self) -> int:
        return 1

    def run(self) -> Iterator[Progress]:
        """Capture and save, yielding progress.  LEDs off and no part file after."""
        saved = False
        try:
            yield from self._run_image()
            saved = True
        finally:
            self._all_off()
            self._close_writer()
            if not saved and self._part is not None and self._part.exists():
                self._part.unlink()
                log.info("test image: removed the unfinished %s", self._part)

    def _run_image(self) -> Iterator[Progress]:
        ch = self.choice
        directory = ch.directory or config.capture_test_image_dir()
        problem = test_image_repo.name_problem(ch.name, directory)
        if problem is None:
            self.exposures = exposure_repo.rung_exposures(ch.led)
            problem = choice_problem(ch, self.exposures)
        if problem is not None:
            raise ValueError(problem)
        channel = self._leds.channel_of(ch.led)
        exposures = [self.exposures[r] for r in ch.rungs]
        labels = [rung_label(r) for r in ch.rungs]
        types = set(ch.types)

        cam = self._cam
        cam.set_exposure(exposures[0])
        with self.prof.time("camera.setup"):
            cam.setup_camera()
        pattern = cam.bayer_pattern
        cam_w, cam_h = cam.config_size
        self.rois = cr._load_rois(cam)
        bound = roi.by_role(self.rois, roi.ROLE_BOUND)
        yield self._progress("setup", f"Camera {cam.model_description}")

        focus_pos, focus_mode, _ = self._set_focus()
        yield self._progress("focus", f"Focus: {focus_pos:.2f} diopters ({focus_mode})")

        directory.mkdir(parents=True, exist_ok=True)
        self.suite_id = config.capture_suite_id()
        self.out_path = directory / test_image_repo.file_name(ch.name)
        self._part = self.out_path.with_name(self.out_path.name + ".part")
        w = self._writer = CaptureSuiteWriter(self._part, suite_id=self.suite_id,
                                              description=DESCRIPTION)
        self._write_metadata(w)

        self._led_index, self._current_color = 1, ch.led
        self._captures_per_led = len(exposures)
        self._led_on(channel)
        yield from self._wait(cr.LED_ON_WARMUP_S, f"{ch.led} LED on, warming up")
        applied = {}
        for rung, label, us in zip(ch.rungs, labels, exposures):
            rgb, bayer, meta = self._settle(us)
            applied[label] = int(meta.get("ExposureTime", 0))
            stamp = self._frame_captured_dt
            if "bayer_bound" in types:
                w.add_lit(ch.led, label, self._bound_layers(bayer, pattern),
                          exposure_us=us, captured_dt=stamp)
            if "bayer_whole" in types:
                w.add_lit(ch.led, label, self._extract(bayer, pattern),
                          exposure_us=us, captured_dt=stamp, whole=True)
            attrs = {"exposure_us": us, "rung": rung, "captured_dt": stamp}
            if "jpg_whole" in types:
                w.add_rung_jpeg(ch.led, label, "whole", cr._encode_jpeg(rgb), attrs)
            if "jpg_bound" in types:
                w.add_rung_jpeg(ch.led, label, "bound",
                                cr._encode_jpeg(BayerCam.crop_rgb(rgb, bound)), attrs)
            self._captures_done += 1
            yield self._progress("capture", f"{ch.led} rung {rung} ({us} us)")
        self._led_off(channel)

        w.add_led(ch.led, {
            "mode": "test_image",
            "timestamp": self.suite_id,
            "name": ch.name,
            "description": ch.description,
            "led_channel": channel,
            "led_color": ch.led,
            "camera_model": cam.model_description,
            "bayer_pattern": pattern,
            "config_size": [cam_w, cam_h],
            "focus_diopters": focus_pos,
            "focus_mode": focus_mode,
            "led_on_dt": self._led_on_dt,
            "led_off_dt": self._led_off_dt,
            "rungs": {lbl: r for lbl, r in zip(labels, ch.rungs)},
            "exposures_us": {lbl: us for lbl, us in zip(labels, exposures)},
            "applied_exposures_us": applied,
            "image_types": [t for t in IMAGE_TYPES if t in types],
        }, led_channel=channel)

        if "darkframes" in types:
            yield from self._wait(cr.LED_OFF_SETTLE_S, "LED off, settling before darks")
            forms = [whole for whole, t in ((False, "bayer_bound"), (True, "bayer_whole"))
                     if t in types]
            for us in sorted(set(exposures)):
                _, bayer, _ = self._settle(us)
                for whole in forms:
                    layers = (self._extract(bayer, pattern) if whole
                              else self._bound_layers(bayer, pattern))
                    w.add_dark(layers, exposure_us=us,
                               captured_dt=self._frame_captured_dt, whole=whole)
                yield self._progress("capture", f"dark ({us} us)", step="dark")
            for label, us in zip(labels, exposures):
                for whole in forms:
                    w.link_dark(ch.led, label, exposure_us=us, whole=whole)

        self._close_writer()
        self._part.rename(self.out_path)
        test_image_repo.record(ch.name, ch.led, ch.rungs, exposures,
                               [t for t in IMAGE_TYPES if t in types], ch.description)
        log.info("test image: saved %s", self.out_path)
        yield Progress(phase="done", message=f"Saved {ch.name}", led_index=1,
                       led_total=1, fraction=1.0)
