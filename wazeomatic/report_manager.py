"""
Module Name: report_manager.py
Description: Turns noisy per-frame detections into rare, trustworthy hazard events using persistence, confidence floors and cooldowns

Class Name: ReportConfig
Description: Thresholds for the report manager: window, minimum hits, confidence floors, cooldowns and heuristics

Class Name: _Track
Description: Recent hits for one label used by the spatial tracking check

Class Name: _Tracklet
Description: A chain of overlapping boxes followed from frame to frame

Class Name: Decision
Description: One logged reason a detection or confirmed hazard did not become a report (low confidence, hood zone, unconfirmed, cooldown, carried object)

Class Name: ReportManager
Description: Applies persistence, low-confidence filtering, the hood zone, the in-lane road-object rule and per-type cooldowns, then emits HazardEvents and logs every dropped detection

Inputs: Per-frame detections and a clock
Outputs: HazardEvents for detections that pass every check; a Decision log entry for each low-confidence, hood-zone, unconfirmed or suppressed detection
External Sources: Claude Fable 5.1, Claude Opus 5.5
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the persistence window and per-type cooldown so one real hazard produces one report and repeats inside a minute are suppressed
How you validated and revised the AI output: Proofreading, and the pytest unit tests in tests/ run on the desktop (tests/test_report_manager.py covers persistence, low confidence, cooldowns, unmapped labels and the hood zone)
The challenges or limitations you faced while using AI: Choosing thresholds that suppress false reports without missing real hazards, and keeping the rules identical to the Kotlin ReportManager
Authors: Blake Pennel
Creation Date: 11/5/2026

Revision 11/17/2026 - Luke Reicherter and Claude Opus 5.5 (Sprint 2, requirements 7 and 9):
A description of how and why AI was used: Claude Opus 5.5 used to add the decision log required by criteria 7.3-7.6 and 9.3-9.5 (every unconfirmed, low-confidence, hood-zone and suppressed detection is logged with its score) and the in-lane road-object rule for criterion 7.2 (HAZARD_ON_ROAD_OBJECT from stock COCO objects inside the driving lane)
How you validated and revised the AI output: Proofreading, tests/test_sprint2_requirements.py (one test per Sprint 2 acceptance criterion), and runs of run_video.py on the CC0 dashcam clip with the decision log written to JSONL
The challenges or limitations you faced while using AI: Logging every dropped detection without flooding the log in a long work zone, so cooldown suppressions are logged once per window; picking COCO classes that are plausible road debris
Authors: Blake Pennel, Luke Reicherter
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
5. **Road objects** (Sprint 2, criterion 7.2) - stock COCO objects that do not belong in a lane
   (a suitcase, a chair, a backpack ...) become ``HAZARD_ON_ROAD_OBJECT`` when their box centre is
   inside the driving-lane zone ahead and they are not held by a detected person.

Every detection that is dropped, and every confirmed hazard that is held back, is recorded as a
``Decision`` in ``ReportManager.decisions`` and written to the ``wazeomatic.report`` logger, so
the acceptance criteria "... and the detection is logged" can be checked from the log.

Everything here is pure Python so it can be unit tested without a model, and the
Kotlin ``ReportManager`` in the Android app implements the same rules.
"""

import logging
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, Optional

from .schema import DEFAULT_CLASS_TO_HAZARD, Detection, HazardEvent

log = logging.getLogger("wazeomatic.report")

DEFAULT_LABEL_FLOORS: dict[str, float] = {"pothole": 0.45}

# COCO classes that are debris when they sit in the lane ahead (criterion 7.2). Vehicles, people
# and animals are handled elsewhere; a custom model class named "debris" maps directly in schema.py.
DEFAULT_ROAD_OBJECT_LABELS: tuple[str, ...] = (
    "suitcase", "backpack", "handbag", "umbrella", "sports ball", "skateboard",
    "chair", "couch", "bench", "potted plant", "bed", "toilet", "refrigerator", "tv",
)

# Decision reasons (also used by the Kotlin port and the run_video.py summary).
LOW_CONFIDENCE = "low_confidence"     # 7.4, 9.5: below the confidence floor for its label
HOOD_ZONE = "hood_zone"               # 7.6: box bottom inside the car's own hood / dashboard zone
UNCONFIRMED = "unconfirmed"           # 7.3, 9.4: seen in fewer than min_hits of the last window frames
COOLDOWN = "cooldown"                 # 7.5, 9.3: confirmed again inside the per-type cooldown
GLOBAL_COOLDOWN = "global_cooldown"   # confirmed, but another hazard was reported seconds ago
CARRIED_OBJECT = "carried_object"     # road-object candidate inside a person box (a bag being carried)


@dataclass(frozen=True)
class Decision:
    """Why a detection, or a confirmed hazard, did not turn into a report on this frame."""

    frame: int
    reason: str
    label: str
    hazard: Optional[str]
    confidence: float
    ts: float
    detail: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["confidence"] = round(self.confidence, 3)
        return d

    def __str__(self) -> str:
        return (f"frame {self.frame}: {self.reason} {self.label} ({self.confidence:.2f})"
                f"{' -> ' + self.hazard if self.hazard else ''}{': ' + self.detail if self.detail else ''}")


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
    # in-lane road-object rule (criterion 7.2): a COCO object whose box centre is in the lane ahead
    road_object_enabled: bool = True
    road_object_labels: tuple[str, ...] = DEFAULT_ROAD_OBJECT_LABELS
    lane_x_min: float = 0.30         # driving lane ahead, box centre (normalized)
    lane_x_max: float = 0.70
    lane_y_min: float = 0.45         # below the horizon: on the road surface, not in the sky or on a sign
    person_labels: tuple[str, ...] = ("person",)
    # decision log
    decision_log_size: int = 2000    # entries kept in ReportManager.decisions (oldest dropped)


@dataclass
class _Track:
    hits: deque = field(default_factory=deque)        # (frame_idx, confidence, bbox, label)
    last_emitted_ts: float = -1e9
    tracklets: list = field(default_factory=list)     # spatial mode: [_Tracklet]
    last_suppressed_frame: int = -10**9               # last frame a cooldown suppression was logged


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


def _center_inside(inner: tuple, outer: tuple) -> bool:
    """Centre of ``inner`` lies inside box ``outer`` (both normalized centre-xywh)."""
    return abs(inner[0] - outer[0]) <= outer[2] / 2 and abs(inner[1] - outer[1]) <= outer[3] / 2


def _associated(a: tuple, b: tuple, iou_thr: float, grow: float) -> bool:
    """Same object across two frames: grown boxes overlap enough, or the centres are within
    ``grow`` box sizes of each other (an object close to the car moves a lot per frame)."""
    if _grown_iou(a, b, grow) >= iou_thr:
        return True
    dist = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
    return dist <= grow * max(a[2], a[3], b[2], b[3])


class ReportManager:
    def __init__(self, config: ReportConfig | None = None, source: str = "sim",
                 clock: Callable[[], float] | None = None,
                 on_decision: Callable[[Decision], None] | None = None):
        import time

        self.cfg = config or ReportConfig()
        self.source = source
        self.clock = clock or time.time
        self._tracks: dict[str, _Track] = defaultdict(_Track)
        self._last_global_ts = -1e9
        self._frame = 0
        self._hood_frac_dynamic = 0.0
        # every dropped detection / held-back hazard, newest last (criteria 7.3-7.6, 9.3-9.5)
        self.decisions: deque[Decision] = deque(maxlen=self.cfg.decision_log_size)
        self.decision_counts: dict[str, int] = defaultdict(int)
        self.on_decision = on_decision

    # ------------------------------------------------------------------ public
    def update(self, detections: Iterable[Detection], frame_index: Optional[int] = None,
               lat: float | None = None, lon: float | None = None) -> list[HazardEvent]:
        """Feed one inference frame; returns zero or more events to report."""
        self._frame = self._frame + 1 if frame_index is None else frame_index
        now = self.clock()
        detections = list(detections)
        persons = [d.bbox for d in detections if d.label in self.cfg.person_labels]
        best: dict[str, Detection] = {}
        accepted: dict[str, list[Detection]] = defaultdict(list)
        for d in detections:
            hazard = self._hazard_for(d)
            if hazard is None:
                continue
            floor = self._min_conf(d.label)
            if d.confidence < floor:
                self._record(LOW_CONFIDENCE, d.label, hazard, d.confidence, now, f"below floor {floor:.2f}")
                continue
            if self._in_hood_zone(d):
                self._record(HOOD_ZONE, d.label, hazard, d.confidence, now,
                             f"box bottom {d.bbox[1] + d.bbox[3] / 2:.2f} inside hood zone {self.hood_frac:.2f}")
                continue
            if hazard == "HAZARD_ON_ROAD_OBJECT" and any(_center_inside(d.bbox, p) for p in persons):
                self._record(CARRIED_OBJECT, d.label, hazard, d.confidence, now, "inside a person box")
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
                if hazard in best:  # seen this frame, but not (yet) in enough frames to trust
                    d = best[hazard]
                    self._record(UNCONFIRMED, d.label, hazard, d.confidence, now,
                                 f"{self._hit_count(tr)}/{self.cfg.min_hits} hits in last {self.cfg.window} frames")
                continue
            if now - tr.last_emitted_ts < self.cfg.cooldown_s:
                self._log_suppressed(COOLDOWN, hazard, tr, hits, now,
                                     f"reported {now - tr.last_emitted_ts:.1f}s ago, cooldown {self.cfg.cooldown_s:.0f}s")
                continue
            if now - self._last_global_ts < self.cfg.global_cooldown_s:
                self._log_suppressed(GLOBAL_COOLDOWN, hazard, tr, hits, now,
                                     f"another hazard reported {now - self._last_global_ts:.1f}s ago")
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

    def _hit_count(self, tr: _Track) -> int:
        if self.cfg.track_iou <= 0:
            return len(tr.hits)
        return max((len(tl.hits) for tl in tr.tracklets), default=0)

    # ------------------------------------------------------------- decision log
    def _record(self, reason: str, label: str, hazard: Optional[str], confidence: float, ts: float,
                detail: str = "") -> None:
        dec = Decision(self._frame, reason, label, hazard, float(confidence), ts, detail)
        self.decisions.append(dec)
        self.decision_counts[reason] += 1
        log.info("%s", dec)
        if self.on_decision is not None:
            self.on_decision(dec)

    def _log_suppressed(self, reason: str, hazard: str, tr: _Track, hits, now: float, detail: str) -> None:
        """A confirmed hazard held back by a cooldown. The hits stay in the window (so the report
        goes out as soon as the cooldown ends), so log it once per window, not on every frame."""
        if self._frame - tr.last_suppressed_frame < self.cfg.window:
            return
        tr.last_suppressed_frame = self._frame
        _, conf, _, label = max(hits, key=lambda h: h[1])
        self._record(reason, label, hazard, conf, now, detail)

    # ----------------------------------------------------------------- helpers
    def _hazard_for(self, d: Detection) -> Optional[str]:
        if d.label in self.cfg.class_to_hazard:
            return self.cfg.class_to_hazard[d.label]
        if self.cfg.road_object_enabled and d.label in self.cfg.road_object_labels and self._in_lane(d):
            return "HAZARD_ON_ROAD_OBJECT"
        if self.cfg.stopped_vehicle_enabled and d.label in self.cfg.vehicle_labels and self._in_shoulder(d):
            return "HAZARD_SHOULDER_CAR_STOPPED"
        return None

    def _in_lane(self, d: Detection) -> bool:
        cx, cy = d.bbox[0], d.bbox[1]
        return self.cfg.lane_x_min <= cx <= self.cfg.lane_x_max and cy >= self.cfg.lane_y_min

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
