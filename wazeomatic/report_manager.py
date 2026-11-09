"""
Module Name: report_manager.py
Description: Turns noisy per-frame detections into rare, trustworthy hazard events using persistence, confidence floors and cooldowns

Class Name: ReportConfig
Description: Thresholds for the report manager: window, minimum hits, confidence floors, cooldowns and heuristics

Class Name: _Track
Description: Recent hits for one label used by the spatial tracking check

Class Name: _Tracklet
Description: A chain of overlapping boxes followed from frame to frame

Class Name: ReportManager
Description: Applies persistence, low-confidence filtering, the hood zone and per-type cooldowns, then emits HazardEvents

Inputs: Per-frame detections and a clock
Outputs: HazardEvents for detections that pass every check; low-confidence and duplicate detections are dropped
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the persistence window and per-type cooldown so one real hazard produces one report and repeats inside a minute are suppressed
How you validated and revised the AI output: Proofreading, and the pytest unit tests in tests/ run on the desktop (tests/test_report_manager.py covers persistence, low confidence, cooldowns, unmapped labels and the hood zone)
The challenges or limitations you faced while using AI: Choosing thresholds that suppress false reports without missing real hazards, and keeping the rules identical to the Kotlin ReportManager
Authors: Blake Pennel
Creation Date: 11/5/2026
"""
from __future__ import annotations

"""Turns noisy per-frame detections into rare, trustworthy HazardEvents.

Three layers, in order:

1. **Persistence** - a hazard class must be seen in at least ``min_hits`` of the last
   ``window`` inference frames (per class). Single-frame flickers never report.
2. **Cooldown / de-duplication** - after a hazard type is emitted it is muted for
   ``cooldown_s`` seconds. This mirrors the macro's own one-report-per-minute guard and
   stops the same pothole being reported ten times while we drive over it.
3. **Heuristics** - a hood exclusion zone (boxes touching the bottom edge of the frame are
   the car's own bonnet reflection, not road), and the stopped-vehicle-on-shoulder check
   (experimental, off by default).
4. **Spatial consistency** (``track_iou`` > 0) - the hits that make up a report must come from
   boxes that overlap frame to frame, i.e. from one object the car is approaching, not from a
   flicker here and a flicker there. Per-label confidence floors (``min_confidence_by_label``)
   let a noisy class such as cone demand more than a clean one such as construction sign.

Everything here is pure Python so it can be unit tested without a model, and the
Kotlin ``ReportManager`` in the Android app implements the same rules.
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from .schema import DEFAULT_CLASS_TO_HAZARD, Detection, HazardEvent


DEFAULT_LABEL_FLOORS: dict[str, float] = {"pothole": 0.45}


@dataclass
class ReportConfig:
    window: int = 10                 # inference frames considered
    min_hits: int = 3                # hits required inside the window
    min_confidence: float = 0.40     # per-detection confidence to count as a hit
    # per-label floors on top of min_confidence (the higher of the two applies). Potholes need
    # more: the v3 model is clean on 12 minutes of hazard-free footage at 0.45 (the older 640c
    # needed 0.55; docs/model-research.md)
    min_confidence_by_label: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_LABEL_FLOORS))
    track_iou: float = 0.0           # > 0: hits must chain through boxes with at least this IoU
                                     # (frame to frame, boxes grown by track_grow), or whose centres
                                     # lie within track_grow box sizes of each other
    track_grow: float = 1.5          # box growth for the IoU test / centre-distance tolerance
    hood_frac: float = 0.10          # ignore boxes whose bottom edge is in the lowest 10 % of the frame
                                     # (the car's own hood / dashboard reflection looks like a pothole)
    cooldown_s: float = 60.0         # per hazard type
    global_cooldown_s: float = 5.0   # between any two events
    class_to_hazard: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CLASS_TO_HAZARD))
    # stopped-vehicle heuristic
    stopped_vehicle_enabled: bool = False
    shoulder_x_min: float = 0.62     # right-hand shoulder region of interest (normalized)
    shoulder_y_min: float = 0.40
    vehicle_labels: tuple[str, ...] = ("car", "truck", "bus")
    vehicle_min_area: float = 0.02   # bbox area fraction - close enough to matter
    vehicle_min_growth: float = 1.35 # area must grow by this factor across the window


@dataclass
class _Track:
    hits: deque = field(default_factory=deque)        # (frame_idx, confidence, bbox, label)
    last_emitted_ts: float = -1e9
    tracklets: list = field(default_factory=list)     # spatial mode: [_Tracklet]


@dataclass
class _Tracklet:
    bbox: tuple
    hits: deque = field(default_factory=deque)        # (frame_idx, confidence, bbox, label)


def _grown_iou(a: tuple, b: tuple, grow: float) -> float:
    ax1, ay1 = a[0] - a[2] * grow / 2, a[1] - a[3] * grow / 2
    ax2, ay2 = a[0] + a[2] * grow / 2, a[1] + a[3] * grow / 2
    bx1, by1 = b[0] - b[2] * grow / 2, b[1] - b[3] * grow / 2
    bx2, by2 = b[0] + b[2] * grow / 2, b[1] + b[3] * grow / 2
    iw, ih = max(0.0, min(ax2, bx2) - max(ax1, bx1)), max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def _associated(a: tuple, b: tuple, iou_thr: float, grow: float) -> bool:
    """Same object across two frames: grown boxes overlap enough, or the centres are within
    ``grow`` box sizes of each other (an object close to the car moves a lot per frame)."""
    if _grown_iou(a, b, grow) >= iou_thr:
        return True
    dist = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
    return dist <= grow * max(a[2], a[3], b[2], b[3])


class ReportManager:
    def __init__(self, config: ReportConfig | None = None, source: str = "sim",
                 clock: Callable[[], float] | None = None):
        import time

        self.cfg = config or ReportConfig()
        self.source = source
        self.clock = clock or time.time
        self._tracks: dict[str, _Track] = defaultdict(_Track)
        self._last_global_ts = -1e9
        self._frame = 0
        self._hood_frac_dynamic = 0.0

    # ------------------------------------------------------------------ public
    def update(self, detections: Iterable[Detection], frame_index: Optional[int] = None,
               lat: float | None = None, lon: float | None = None) -> list[HazardEvent]:
        """Feed one inference frame; returns zero or more events to report."""
        self._frame = self._frame + 1 if frame_index is None else frame_index
        now = self.clock()
        best: dict[str, Detection] = {}
        accepted: dict[str, list[Detection]] = defaultdict(list)
        for d in detections:
            hazard = self._hazard_for(d)
            if hazard is None or d.confidence < self._min_conf(d.label):
                continue
            if self._in_hood_zone(d):
                continue
            accepted[hazard].append(d)
            if hazard not in best or d.confidence > best[hazard].confidence:
                best[hazard] = d
        # record hits (one per hazard per frame) and trim the window
        for hazard, d in best.items():
            self._tracks[hazard].hits.append((self._frame, d.confidence, d.bbox, d.label))
        for hazard, tr in self._tracks.items():
            while tr.hits and tr.hits[0][0] <= self._frame - self.cfg.window:
                tr.hits.popleft()
        if self.cfg.track_iou > 0:
            self._update_tracklets(accepted)

        events: list[HazardEvent] = []
        for hazard, tr in self._tracks.items():
            hits = self._qualifying_hits(tr)
            if hits is None:
                continue
            if now - tr.last_emitted_ts < self.cfg.cooldown_s:
                continue
            if now - self._last_global_ts < self.cfg.global_cooldown_s:
                continue
            if hazard == "HAZARD_SHOULDER_CAR_STOPPED" and not self._stopped_vehicle_confirmed(tr):
                continue
            _, conf, bbox, label = max(hits, key=lambda h: h[1])
            ev = HazardEvent(hazard_type=hazard, confidence=conf, source=self.source, ts=now,
                             frame_index=self._frame, bbox=bbox, label=label, lat=lat, lon=lon)
            events.append(ev)
            tr.last_emitted_ts = now
            self._last_global_ts = now
            tr.hits.clear()
            tr.tracklets.clear()
        return events

    # ------------------------------------------------------------ spatial tracking
    def _min_conf(self, label: str) -> float:
        """A label floor only ever raises the bar: the higher of min_confidence and the label value."""
        return max(self.cfg.min_confidence, self.cfg.min_confidence_by_label.get(label, 0.0))

    def _update_tracklets(self, accepted: dict[str, list[Detection]]) -> None:
        """Greedy IoU association of this frame's accepted boxes to per-hazard tracklets."""
        for hazard, dets in accepted.items():
            tr = self._tracks[hazard]
            used: set[int] = set()
            for d in sorted(dets, key=lambda x: -x.confidence):
                best_i, best_iou = -1, -1.0
                for i, tl in enumerate(tr.tracklets):
                    if i in used or not _associated(tl.bbox, d.bbox, self.cfg.track_iou, self.cfg.track_grow):
                        continue
                    iou = _grown_iou(tl.bbox, d.bbox, self.cfg.track_grow)
                    if iou > best_iou:
                        best_i, best_iou = i, iou
                hit = (self._frame, d.confidence, d.bbox, d.label)
                if best_i < 0:
                    tr.tracklets.append(_Tracklet(bbox=d.bbox, hits=deque([hit])))
                    used.add(len(tr.tracklets) - 1)
                else:
                    tl = tr.tracklets[best_i]
                    tl.bbox = d.bbox
                    tl.hits.append(hit)
                    used.add(best_i)
        # trim windows and drop stale tracklets (for every hazard, not only those seen now)
        for tr in self._tracks.values():
            for tl in tr.tracklets:
                while tl.hits and tl.hits[0][0] <= self._frame - self.cfg.window:
                    tl.hits.popleft()
            tr.tracklets = [tl for tl in tr.tracklets if tl.hits]

    def _qualifying_hits(self, tr: _Track):
        """The hits that justify a report now, or None."""
        if self.cfg.track_iou <= 0:
            return tr.hits if len(tr.hits) >= self.cfg.min_hits else None
        ready = [tl for tl in tr.tracklets if len(tl.hits) >= self.cfg.min_hits]
        if not ready:
            return None
        return max(ready, key=lambda tl: max(h[1] for h in tl.hits)).hits

    # ----------------------------------------------------------------- helpers
    def _hazard_for(self, d: Detection) -> Optional[str]:
        if d.label in self.cfg.class_to_hazard:
            return self.cfg.class_to_hazard[d.label]
        if self.cfg.stopped_vehicle_enabled and d.label in self.cfg.vehicle_labels and self._in_shoulder(d):
            return "HAZARD_SHOULDER_CAR_STOPPED"
        return None

    @property
    def hood_frac(self) -> float:
        """Active hood zone: the configured minimum, or a larger value learnt from the video
        (see ``hood.HoodEstimator``; the pipeline calls ``set_hood_frac``)."""
        return max(self.cfg.hood_frac, self._hood_frac_dynamic)

    def set_hood_frac(self, frac: float) -> None:
        self._hood_frac_dynamic = float(frac)

    def _in_hood_zone(self, d: Detection) -> bool:
        cy, h = d.bbox[1], d.bbox[3]
        hood = self.hood_frac
        return hood > 0 and (cy + h / 2) > (1.0 - hood)

    def _in_shoulder(self, d: Detection) -> bool:
        cx, cy, w, h = d.bbox
        return cx >= self.cfg.shoulder_x_min and cy >= self.cfg.shoulder_y_min and w * h >= self.cfg.vehicle_min_area

    def _stopped_vehicle_confirmed(self, tr: _Track) -> bool:
        """A parked car on the shoulder grows in the frame as we approach it and stays
        on the right edge; a car in the next lane moving with us does neither."""
        if len(tr.hits) < 2:
            return False
        first, last = tr.hits[0][2], tr.hits[-1][2]
        a0, a1 = first[2] * first[3], last[2] * last[3]
        return a1 >= a0 * self.cfg.vehicle_min_growth and last[0] >= first[0] - 0.02
