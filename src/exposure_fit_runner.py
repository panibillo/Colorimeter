"""
exposure_fit_runner.py  —  Calibrate exposure brackets on the camera.
--------------------------------------------------------------------
Drives the test-pad exposure calibration: for each LED in turn it lights the
LED, measures the test pad and gray chips through the camera at whatever
exposures exposure_fit asks for, and keeps the brackets that pass.

The pieces, and where each lives:

    exposure_fit.py       decides where the brackets go (pure arithmetic)
    exposure_measure.py   reads the pad and chips out of a frame
    required_cells.py     which Bayer channels must pass for each LED
    this module           lights the LED, grabs frames, calls the others

This is a CaptureRunner, so it reuses its camera set-up, focus, LED switching
and exposure settling.  Run it like the capture run:

    runner = ExposureFitRunner(cam, leds)               # every LED
    runner = ExposureFitRunner(cam, leds, only="green") # just one
    for progress in runner.run():
        ...                                             # repaint, poll input
    runner.persist()                                    # save what passed

Nothing is saved until persist() is called, so a caller can look at runner.fits
first.  The LED is switched off when the run ends, however it ends.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Iterator, Optional, Tuple

import capture_runner as cr
import exposure_fit as ef
import exposure_measure as em
import exposure_repo
import required_cells
import roi
from capture_runner import CaptureConfig, CaptureRunner, Progress

log = logging.getLogger(__name__)

# The stored group whose middle bracket seeds the search, tried in order.  Any
# starting exposure converges; a good one saves captures.
SEED_GROUPS = ("3", "1")

# The group whose exposures are flagged for the pre-image: one image, at the
# middle of the usable range.
PREIMAGE_GROUP = "1"

# Called after each frame with (color, exposure_us, frames_so_far).  A fit takes
# many frames and the run yields only between LEDs, so a caller that must keep
# a display alive repaints from here.
CaptureHook = Callable[[str, int, int], None]

# Called with (color, text) as each LED warms up and as each rung is tested,
# e.g. ("green", "green calibrating rung 3 of 15 ...").
StatusHook = Callable[[str, str], None]


@dataclass(frozen=True)
class LedSettings:
    """What one LED is held to: the Bayer layers that must pass, and its window."""
    layers: Tuple[str, ...]
    window: required_cells.LedWindow


class ExposureFitRunner(CaptureRunner):
    """Finds tested exposure brackets for one LED or all of them."""

    def __init__(self, cam, leds, config: "CaptureConfig | None" = None,
                 only: Optional[str] = None,
                 params: Optional[ef.FitParams] = None,
                 on_capture: Optional[CaptureHook] = None,
                 on_status: Optional[StatusHook] = None,
                 settings: "Optional[Dict[str, LedSettings]]" = None):
        """
        Args:
            cam:        A BayerCam (or compatible), not yet set up.
            leds:       An LEDBank (or compatible), already open.
            config:     Run parameters.  `led_colors` lists the LEDs fitted
                        and `initial_us` is the fallback starting exposure.
            only:       Fit just this LED, whatever `config` lists.
            params:     Window and limits; default reads the config table.
            on_capture: See CaptureHook.
            on_status:  See StatusHook.
            settings:   {led: LedSettings} to use instead of the config's
                        layers and windows, for any LED it names.
        """
        super().__init__(cam, leds, config)
        self.only = only
        self.params = params
        self.on_capture = on_capture
        self.on_status = on_status
        self.settings = settings or {}
        # {color: LedFit}, filled in as the run goes.
        self.fits: Dict[str, ef.LedFit] = {}
        # {color: 'Gb Gr'}: the Bayer layers that had to pass for each LED.
        self.layers: Dict[str, str] = {}
        self._order: "list[tuple[int, str]]" = []
        self._frames = 0
        # Progress counts whole LEDs: a fit's frame count is not known ahead.
        self._captures_per_led = 1

    @property
    def ok(self) -> bool:
        """True when every LED fitted every group."""
        return bool(self.fits) and all(f.ok for f in self.fits.values())

    def _led_total(self) -> int:
        return len(self._order)

    # ── The run ──────────────────────────────────────────────────────

    def run(self) -> Iterator[Progress]:
        """
        Fit every chosen LED, yielding a Progress at each observable step.

        Raises (when first advanced, before any LED is lit):
            ValueError: `only` or a configured LED is not on this bank, or the
                        test pad or gray chips are not defined.
        """
        try:
            yield from self._run_fit()
        finally:
            self._all_off()

    def _prepare(self) -> Iterator[Progress]:
        """
        Resolve the LEDs, start the camera, check the pad and chips are
        defined, and focus.  Sets self.pad and self.pattern.  Nothing is lit
        until this has passed.
        """
        cfg, cam = self.config, self._cam
        self._frames = 0
        colors = [self.only] if self.only is not None else cfg.led_colors
        self._order = cr._resolve_color_order(self._leds, colors)

        cam.set_exposure(cfg.initial_us)
        with self.prof.time("camera.setup"):
            cam.setup_camera()
        self.pattern = cam.bayer_pattern
        cam_w, cam_h = cam.config_size

        self.rois = cr._load_rois(cam)
        model = getattr(cam, "model", None)
        self.pad = roi.load_test_pad(cam_w, cam_h, model)
        # Checked before any LED is lit, so a missing pad costs a message.
        em.calibration_patches(self.rois, self.pad)

        yield self._progress(
            "setup", f"Camera {cam.model_description}  pattern={self.pattern}  "
                     f"{cam_w}×{cam_h}")

        self.prof.set_section("focus")
        yield self._progress("focus", "Focus")
        with self.prof.time("focus"):
            focus_pos, focus_mode, _ = self._set_focus()
        yield self._progress("focus", f"Focus: {focus_pos:.2f} diopters ({focus_mode})")

    def _light(self, channel: int, color: str) -> Iterator[Progress]:
        """Select the LED, switch it on, and let it warm up."""
        self._led_index += 1
        self._captures_done = 0
        self._current_color = color
        self.prof.set_section(color)
        yield self._progress("calibrate", f"--- {self._leds.label(channel)} ---")
        self._led_on(channel)
        self._status(color, f"{color} warming up ...")
        yield from self._wait(cr.LED_ON_WARMUP_S, f"{color} LED on, warming up")

    def _grabber(self, color: str):
        """grab(exposure_us) -> the frame's Bayer layers, for the lit LED."""
        def grab(exposure_us: int):
            _, bayer, _ = self._settle(exposure_us)
            layers = self._extract(bayer, self.pattern)
            self._frames += 1
            if self.on_capture is not None:
                self.on_capture(color, exposure_us, self._frames)
            return layers
        return grab

    def _run_fit(self) -> Iterator[Progress]:
        self.fits = {}
        yield from self._prepare()
        params = self.params or ef.FitParams.from_config()
        pad = self.pad

        for channel, color in self._order:
            yield from self._light(channel, color)
            grab = self._grabber(color)

            led = self.settings_for(color)
            led_params = dataclasses.replace(
                params, low_dn=led.window.low_dn, high_dn=led.window.high_dn)
            measure = em.measure_for_led(
                color, grab, self.rois, pad,
                (led.window.low_pctl, led.window.high_pctl), led.layers)
            def on_rung(i: int, total: int, _color=color):
                self._status(_color, f"{_color} finding range ..." if i == 0 else
                             f"{_color} calibrating rung {i} of {total} ...")

            fit = ef.fit_led(color, measure, self._seed_us(color), led_params,
                             on_rung)
            self.fits[color] = fit
            self.layers[color] = " ".join(led.layers)

            self._led_off(channel)
            self._captures_done = 1
            yield self._progress("calibrate", self._summary(fit))

        yield self._progress("done", self._overall_summary())

    def settings_for(self, color: str) -> LedSettings:
        """The layers and window `color` is held to: given, else from config."""
        if color in self.settings:
            return self.settings[color]
        import config_repo as config
        return LedSettings(required_cells.required_channels(color),
                           config.calibration_window_for(color))

    def _status(self, color: str, text: str) -> None:
        if self.on_status is not None:
            self.on_status(color, text)

    def _seed_us(self, color: str) -> int:
        """Start from the LED's last calibration if it has one."""
        for group in SEED_GROUPS:
            stored = exposure_repo.middle_exposure(color, group)
            if stored:
                return stored
        return self.config.initial_us

    @staticmethod
    def _summary(fit: ef.LedFit) -> str:
        if fit.ok:
            return f"{fit.led}: all groups fitted ({fit.captures} frames)"
        if not fit.groups:
            first = next(iter(fit.failed.values()))
            return f"{fit.led}: not fitted — {first}"
        lost = ", ".join(sorted(fit.failed))
        return f"{fit.led}: groups {lost} not fitted ({fit.captures} frames)"

    def _overall_summary(self) -> str:
        good = sum(1 for f in self.fits.values() if f.ok)
        return f"{good} of {len(self.fits)} LEDs fully fitted"

    # ── Saving ───────────────────────────────────────────────────────

    def persist(self, add_dt: Optional[str] = None,
                remark: Optional[str] = None) -> int:
        """
        Store the tested brackets, replacing each fitted LED's previous ones.

        A LED with no group fitted at all is left as it was, and logged, since
        replacing a working calibration with nothing would only make things
        worse.  For a LED with some groups fitted, only those groups remain
        active afterward: a group that could not be fitted is not kept from
        the old calibration, because its brackets were never tested.

        Returns:
            The number of exposure rows written.
        """
        add_dt = add_dt or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        remark = remark or "window fit"
        rows = 0
        for color, fit in self.fits.items():
            if not fit.groups:
                log.warning("exposure fit: %s fitted no group; "
                            "its stored exposures are unchanged", color)
                continue
            notes = {us: (n.remark, n.low_dn, n.high_dn)
                     for us, n in fit.notes.items()}
            rows += exposure_repo.write_all_groups(
                color, fit.groups, remark=remark, add_dt=add_dt, notes=notes,
                layers=self.layers.get(color), preimage_group=PREIMAGE_GROUP)
        return rows
