"""
capture_profile.py — lightweight wall-clock instrumentation for a capture run.
------------------------------------------------------------------------------
A capture suite spends its time in a handful of distinct places: waiting inside
the camera for a frame to expose and read out, sleeping out LED warmups, doing
numpy work on the raw frame, and writing the HDF5 archive.  CaptureRunner is a
generator pumped one step at a time, so an external sampling profiler mostly
sees the event loop rather than the capture — this records the capture from the
inside instead.

The profiler accumulates (count, total-seconds) per named category, split by
"section" (the current LED colour, or a phase such as "phase0"/"focus"), and
renders a per-category and per-section summary at the end of a run.  Overhead is
one perf_counter() pair plus a dict add per timed block — negligible next to a
camera frame — so it is left on by default; disable with COLORIMETER_PROFILE=0.

Categories are dotted and grouped by prefix in the per-section table:

    camera.*  — frame captures and camera setup (the irreducible sensor floor)
    wait.*    — LED warmup / settle sleeps
    cpu.*     — numpy work (bayer extraction, percentile analysis)
    io.*      — HDF5 writes and JPEG encoding

Usage:
    prof = CaptureProfiler()
    prof.set_section("amber")
    layers = prof_extract(...)               # caller times cpu.* / io.* blocks
    with prof.time("io.hdf5"):
        writer.add_lit(...)
    prof.record_frame(wall_s, exposure_us)   # each camera frame
    ...
    log.info("capture profile:\n%s", prof.summary())
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Iterator, List


def _enabled_by_default() -> bool:
    """Profiling is on unless COLORIMETER_PROFILE names a false-y value."""
    val = os.environ.get("COLORIMETER_PROFILE", "1").lower()
    return val not in ("0", "false", "no", "off", "")


@dataclass
class _Accum:
    """Running (count, total seconds) for one timed category."""

    count:   int = 0
    total_s: float = 0.0

    def add(self, seconds: float) -> None:
        """Record one timed sample."""
        self.count += 1
        self.total_s += seconds


class CaptureProfiler:
    """
    Accumulate wall-clock time per category and per section for one capture run.

    A "section" is a coarse phase label — the current LED colour during Phase 2,
    or "setup"/"phase0"/"focus" — set by the runner as it advances.  Every timing
    is added both to a run-wide total and to the current section, so the summary
    shows where the whole suite spent its time and breaks it down per LED.

    Attributes:
        enabled: When False, every record/time call is a cheap no-op and
                 summary() reports that profiling was disabled.
    """

    def __init__(self, enabled: "bool | None" = None):
        """
        Args:
            enabled: Force profiling on/off.  Defaults to the COLORIMETER_PROFILE
                     environment variable (on unless set to a false-y value).
        """
        self.enabled = _enabled_by_default() if enabled is None else enabled
        self._overall:  Dict[str, _Accum] = {}
        self._sections: Dict[str, Dict[str, _Accum]] = {}
        self._order:    List[str] = []
        self._section:  str = ""
        self._exposure_floor_s: float = 0.0
        self._t_start = time.perf_counter()
        self.set_section("setup")

    # ── Context ──────────────────────────────────────────────────────────────

    def set_section(self, name: str) -> None:
        """Attribute subsequent timings to `name` (an LED colour or phase)."""
        self._section = name
        if name not in self._sections:
            self._sections[name] = {}
            self._order.append(name)

    # ── Recording ────────────────────────────────────────────────────────────

    def add(self, category: str, seconds: float) -> None:
        """Add a timed sample to a category (run-wide and current section)."""
        if not self.enabled:
            return
        self._overall.setdefault(category, _Accum()).add(seconds)
        self._sections[self._section].setdefault(category, _Accum()).add(seconds)

    def count(self, category: str, n: int = 1) -> None:
        """Bump a category's count without adding time (e.g. settle runs)."""
        if not self.enabled:
            return
        self._overall.setdefault(category, _Accum()).count += n
        self._sections[self._section].setdefault(category, _Accum()).count += n

    @contextmanager
    def time(self, category: str) -> Iterator[None]:
        """Time the wrapped block into `category`.  A no-op when disabled."""
        if not self.enabled:
            yield
            return
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.add(category, time.perf_counter() - t0)

    def record_frame(self, wall_s: float, exposure_us: int) -> None:
        """
        Record one camera frame.

        Its wall time lands under 'camera.frame'; its applied exposure adds to the
        run's irreducible exposure floor — the time the sensor must spend exposing
        regardless of any software overhead — so the summary can separate the two.

        Args:
            wall_s:      Measured wall time of the capture_frame() call.
            exposure_us: Applied ExposureTime from the frame metadata (µs).
        """
        if not self.enabled:
            return
        self.add("camera.frame", wall_s)
        self._exposure_floor_s += max(0, int(exposure_us)) / 1_000_000.0

    # ── Reporting ──────────────────────────────────────────────────────────────

    def summary(self) -> str:
        """
        Render a multi-line report: a per-category table (biggest first), the
        camera floor-vs-overhead line, and a per-section (per-LED) breakdown.

        Returns:
            A formatted multi-line string, ready to pass to a logger.
        """
        if not self.enabled:
            return "capture profile: disabled"

        wall = time.perf_counter() - self._t_start
        lines: List[str] = [f"─ capture profile ─ wall {wall:.1f}s"]

        # Per-category table, biggest total first.
        lines.append(f"  {'category':<15}{'count':>7}{'total_s':>10}"
                     f"{'avg_ms':>9}{'%wall':>7}")
        for cat, a in sorted(self._overall.items(),
                             key=lambda kv: kv[1].total_s, reverse=True):
            avg_ms = (a.total_s / a.count * 1000.0) if a.count else 0.0
            pct    = (a.total_s / wall * 100.0) if wall else 0.0
            lines.append(f"  {cat:<15}{a.count:>7}{a.total_s:>10.3f}"
                         f"{avg_ms:>9.1f}{pct:>7.1f}")

        # Camera floor vs overhead: how much frame wall was unavoidable exposure.
        frame  = self._overall.get("camera.frame", _Accum())
        settle = self._overall.get("camera.settle", _Accum())
        floor  = self._exposure_floor_s
        cam_line = f"  camera: {frame.count} frames"
        if settle.count:
            cam_line += (f" over {settle.count} settle-runs "
                         f"(avg {frame.count / settle.count:.1f}/run)")
        if frame.total_s > 0:
            if floor <= frame.total_s:
                cam_line += (f"; exposure floor {floor:.1f}s "
                             f"({floor / frame.total_s * 100:.0f}% of frame wall) "
                             f"→ ~{frame.total_s - floor:.1f}s overhead")
            else:
                # floor > measured frame wall means the frames never actually
                # blocked for their exposure — the desktop mock returns instantly.
                # The floor is meaningless there, so say so rather than report a
                # negative overhead. On real hardware wall ≥ exposure always holds.
                cam_line += (f"; exposure floor {floor:.1f}s "
                             f"(frames did not block for exposure — mock/instant)")
        lines.append(cam_line)

        # Per-section (per-LED) breakdown, grouped by category prefix.
        lines.append(f"  {'section':<12}{'frames':>7}{'camera_s':>10}"
                     f"{'wait_s':>8}{'cpu_s':>8}{'io_s':>8}")
        for name in self._order:
            cats = self._sections[name]

            def _sum(prefix: str) -> float:
                return sum(a.total_s for c, a in cats.items()
                           if c.startswith(prefix))

            frames = cats.get("camera.frame", _Accum()).count
            lines.append(f"  {name:<12}{frames:>7}{_sum('camera.'):>10.3f}"
                         f"{_sum('wait.'):>8.3f}{_sum('cpu.'):>8.3f}"
                         f"{_sum('io.'):>8.3f}")
        return "\n".join(lines)
