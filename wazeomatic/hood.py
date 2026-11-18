"""
Module Name: hood.py
Description: Learns how much of the bottom of the frame is the car itself (hood, dashboard) from persistent image edges, for the hood exclusion zone (criterion 7.6)

Class Name: HoodEstimator
Description: Running average of downscaled frames; returns the fraction of the frame height to ignore

Inputs: BGR frames from the source
Outputs: The hood fraction passed to ReportManager.set_hood_frac
External Sources: Claude Fable 5.1
Attributions:

Creation 11/6/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to replace the fixed 10 percent hood zone with one learned from the video after the dashboard vent was reported as a pothole
How you validated and revised the AI output: Proofreading, tests/test_hood.py on synthetic frames, and the band ratios measured on the CC0 test clips
The challenges or limitations you faced while using AI: Raw motion did not find the dashboard (it reflects the moving scene), so the method uses persistent edge structure instead
Authors: Blake Pennel
Creation Date: 11/6/2026

Ported to the SE2-P3 repository 11/17/2026 by Luke Reicherter with Claude Opus 5.5 for Sprint 2 (requirements 7 and 9); code unchanged from the team's wazeomatic development repository unless a Revision entry says otherwise
"""
from __future__ import annotations
"""Estimate how much of the bottom of the frame is the car itself (hood, dashboard, wipers).

A phone on the windscreen sees a different amount of dashboard on every car and mount, and the
fixed 10 % hood zone of ``ReportConfig`` turned out too small on the test clips: the dashboard
air vent at 82 % of the frame height was reported as a pothole. Raw frame-to-frame motion does
not find the dashboard (a glossy dash reflects the moving scene, and smooth near asphalt has
little texture), but *persistent structure* does: in a running average of the frames the
dashboard's edges (vents, dash rim, wiper rest) stay sharp while the road and the scenery blur
into a smooth gradient.

Per frame:

* the frame is reduced to a small grey image (default 160 x 90) and folded into an exponential
  moving average (``alpha``);
* the Sobel edge energy of the current frame is accumulated per row band (``rows`` bands).

The estimate (after ``min_frames`` frames) is the edge energy of the averaged image divided by
the mean instantaneous edge energy, per band: close to 1 where the picture never changes, low
where it streams past. Bands are scanned from the bottom up while the ratio is at least
``persist`` (0.45); the first band below it is the road. The result only ever grows beyond the
configured minimum and is clamped to ``max_frac``, and it is left unchanged while the scene
itself looks static (standing still, featureless night road). On the two CC0 test clips the
band ratios from the bottom are 0.87, 0.92, 0.73, 0.56 and then 0.31, i.e. a 22 % dashboard.

``hood_frac`` is the fraction of the frame height to ignore. The Android app has the same
logic in ``HoodEstimator.kt``.
"""

from dataclasses import dataclass, field

import numpy as np


@dataclass
class HoodEstimator:
    min_frac: float = 0.10       # never less than the configured hood zone
    max_frac: float = 0.40       # never more than this, whatever the picture says
    width: int = 160
    height: int = 90
    rows: int = 18               # row bands (5.6 % of the height each)
    alpha: float = 0.05          # EMA weight of a new frame
    persist: float = 0.45        # band is "car" when mean-image edges / instantaneous edges >= this
    min_frames: int = 30         # frames before the estimate is trusted (3 s at 10 fps)
    min_edge: float = 2.0        # mean instantaneous edge energy below this = too dark/flat to judge
    hood_frac: float = field(init=False)
    frames: int = field(init=False, default=0)
    _ema: np.ndarray | None = field(init=False, default=None, repr=False)
    _edge_sum: np.ndarray | None = field(init=False, default=None, repr=False)
    _band: int = field(init=False, default=5, repr=False)

    def __post_init__(self) -> None:
        self.hood_frac = self.min_frac
        self._band = self.height // self.rows
        self.height = self._band * self.rows

    def _reduce(self, frame_bgr: np.ndarray) -> np.ndarray:
        import cv2

        g = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        return cv2.resize(g, (self.width, self.height), interpolation=cv2.INTER_AREA).astype(np.float32)

    def _row_edges(self, img: np.ndarray) -> np.ndarray:
        import cv2

        e = np.abs(cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=3)) + np.abs(cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=3))
        return e.reshape(self.rows, self._band, self.width).mean(axis=(1, 2))

    def update(self, frame_bgr: np.ndarray) -> float:
        """Feed one frame; returns the current hood fraction."""
        small = self._reduce(frame_bgr)
        edges = self._row_edges(small)
        if self._ema is None:
            self._ema = small.copy()
            self._edge_sum = edges.copy()
        else:
            self._ema = (1 - self.alpha) * self._ema + self.alpha * small
            self._edge_sum += edges
        self.frames += 1
        if self.frames >= self.min_frames:
            self.hood_frac = self._estimate()
        return self.hood_frac

    def ratios(self) -> np.ndarray:
        """Per band, top to bottom: persistent-edge ratio (diagnostics)."""
        inst = self._edge_sum / max(self.frames, 1)
        return self._row_edges(self._ema) / (inst + 1e-6)

    def _estimate(self) -> float:
        inst = self._edge_sum / max(self.frames, 1)
        if float(inst.mean()) < self.min_edge:
            return self.hood_frac  # dark or featureless: nothing to learn from
        r = self.ratios()
        middle = r[self.rows // 3: (2 * self.rows) // 3]
        if float(np.median(middle)) >= self.persist:
            return self.hood_frac  # the scene itself is not moving (standing still): keep the estimate
        static_rows = 0
        for v in r[::-1]:  # bottom band first
            if v < self.persist:
                break
            static_rows += 1
        frac = static_rows / self.rows
        return float(min(self.max_frac, max(self.min_frac, frac)))
