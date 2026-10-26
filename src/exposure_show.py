"""
exposure_show.py  —  Look at the test pad's signal at chosen exposures.
-----------------------------------------------------------------------
Backs the Capture page's Show screen.  It lights one LED, takes a frame at
each requested exposure, and keeps the whole pad's sorted pixel signal for
each Bayer layer asked for, so the operator can see how a bracket sits in the
window.  The pad is the sample region, the same one the capture's analysis
reads, not the test pad's three patches.  Two shapes:

    one exposure, all four layers     (what each layer reads at this exposure)
    one layer, a list of exposures    (how one layer moves across the ladder)

Nothing is stored in the database.  The results are in `runner.curves`.
"""

from __future__ import annotations

from typing import Dict, Iterator, List, NamedTuple, Optional, Sequence

import numpy as np

import exposure_measure as em
import roi
from capture_runner import CaptureConfig, Progress
from exposure_fit_runner import ExposureFitRunner

LAYERS = ("R", "Gr", "Gb", "B")


class Curve(NamedTuple):
    exposure_us: int
    layer: str
    signal: np.ndarray        # sorted ascending, black level removed


class ExposureShowRunner(ExposureFitRunner):
    """Captures one LED at the given exposures and collects pad curves."""

    def __init__(self, cam, leds, led: str, exposures: Sequence[int],
                 layers: Sequence[str] = LAYERS,
                 config: "CaptureConfig | None" = None, on_status=None):
        """
        Args:
            led:       The LED's color name.
            exposures: Exposure times (µs) to photograph, in order.
            layers:    The Bayer layers whose curves to keep.
        """
        super().__init__(cam, leds, config, only=led, on_status=on_status)
        self.exposures = [int(e) for e in exposures]
        self.layer_names = tuple(layers)
        self.curves: List[Curve] = []

    def _check_regions(self) -> None:
        """Raise ValueError unless the sample region is defined."""
        em.sample_rect(self.rois)

    def run(self) -> Iterator[Progress]:
        try:
            yield from self._run_show()
        finally:
            self._all_off()

    def _run_show(self) -> Iterator[Progress]:
        self.curves = []
        yield from self._prepare()
        pad = em.sample_rect(self.rois)
        for channel, color in self._order:
            yield from self._light(channel, color)
            grab = self._grabber(color)
            for i, us in enumerate(self.exposures, 1):
                self._status(color, f"{color} capturing {i} of "
                                    f"{len(self.exposures)} ...")
                layers = grab(us)
                for name in self.layer_names:
                    self.curves.append(Curve(
                        us, name, em.pad_samples(layers, pad, name)))
                yield self._progress("calibrate", f"{color} {us} us")
            self._led_off(channel)
        yield self._progress("done", f"{len(self.curves)} curves")
