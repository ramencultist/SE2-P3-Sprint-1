"""
Module Name: pipeline.py
Description: Connects frames, detectors, the report manager and the sinks, with optional on-screen display and recording

Class Name: PipelineStats
Description: Collects per-frame latency and counts and summarizes them at the end of a run

Class Name: Pipeline
Description: Runs each frame through the detectors and report manager and sends resulting events to the sinks

Inputs: Frames from a frame source, detectors, a report manager and sinks
Outputs: Detections and HazardEvents per frame, annotated video frames and run statistics
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the glue loop that ties preprocessing, inference and reporting together
How you validated and revised the AI output: Proofreading, and end-to-end runs of the desktop simulator on the CC0 dashcam test clips
The challenges or limitations you faced while using AI: Keeping the loop fast enough for real time while still drawing overlays and recording
Authors: Blake Pennel
Creation Date: 11/5/2026
"""
from __future__ import annotations

"""Glue: frames -> detectors -> ReportManager -> sinks, with optional display/recording."""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

import cv2
import numpy as np

from .detector import OnnxDetector
from .hood import HoodEstimator
from .report_manager import ReportManager
from .schema import Detection, HazardEvent
from .sources import Frame

COLORS = {
    "pothole": (0, 0, 255),
    "cone": (0, 140, 255),
    "construction_sign": (0, 200, 255),
    "delineator": (0, 255, 255),
    "barricade": (0, 170, 255),
    "worker": (60, 255, 160),
    "car": (200, 200, 200),
    "truck": (200, 200, 200),
    "bus": (200, 200, 200),
    "person": (255, 0, 255),
}


def draw(frame: np.ndarray, dets: Iterable[Detection], events: Iterable[HazardEvent], info: str = "",
         hood_frac: float = 0.0) -> np.ndarray:
    out = frame.copy()
    h, w = out.shape[:2]
    if hood_frac > 0:  # boxes below this line are ignored (car body)
        y = int((1 - hood_frac) * h)
        cv2.line(out, (0, y), (w, y), (80, 80, 255), 1)
    for d in dets:
        cx, cy, bw, bh = d.bbox
        x1, y1 = int((cx - bw / 2) * w), int((cy - bh / 2) * h)
        x2, y2 = int((cx + bw / 2) * w), int((cy + bh / 2) * h)
        col = COLORS.get(d.label, (0, 255, 0))
        cv2.rectangle(out, (x1, y1), (x2, y2), col, 2)
        cv2.putText(out, f"{d.label} {d.confidence:.2f}", (x1, max(14, y1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2)
    y = 22
    if info:
        cv2.putText(out, info, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        y += 24
    for ev in events:
        cv2.putText(out, f"REPORT {ev.hazard_type} ({ev.confidence:.2f})", (8, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        y += 26
    return out


@dataclass
class PipelineStats:
    frames: int = 0
    detections: int = 0
    events: int = 0
    infer_ms: list = field(default_factory=list)      # summed model time per frame (compute)
    latency_ms: list = field(default_factory=list)    # wall time of one process() call
    started: float = field(default_factory=time.time)

    def summary(self) -> dict:
        ms = sorted(self.infer_ms)
        lat = sorted(self.latency_ms)

        def pct(arr: list, p: float) -> float:
            return arr[min(len(arr) - 1, int(p * len(arr)))] if arr else 0.0

        wall = time.time() - self.started
        return {
            "frames": self.frames,
            "detections": self.detections,
            "events": self.events,
            "infer_ms_mean": round(sum(ms) / len(ms), 2) if ms else 0.0,
            "infer_ms_p50": round(pct(ms, 0.5), 2),
            "infer_ms_p90": round(pct(ms, 0.9), 2),
            "latency_ms_mean": round(sum(lat) / len(lat), 2) if lat else 0.0,
            "latency_ms_p90": round(pct(lat, 0.9), 2),
            "wall_s": round(wall, 1),
            "pipeline_fps": round(self.frames / wall, 2) if wall else 0.0,
        }


class Pipeline:
    def __init__(self, detectors: Sequence[OnnxDetector], manager: ReportManager, sinks: Sequence,
                 show: bool = False, record_path: Optional[str | Path] = None, record_fps: float = 10.0,
                 on_frame=None, parallel: bool = True, hood: Optional[HoodEstimator] = None):
        self.detectors = list(detectors)
        # learns how much of the bottom of the frame is the car itself (dashboard, hood)
        self.hood = hood
        # independent models (hazard, COCO) run side by side; ONNX Runtime releases the GIL
        self._pool = None
        if parallel and len(self.detectors) > 1:
            from concurrent.futures import ThreadPoolExecutor

            self._pool = ThreadPoolExecutor(max_workers=len(self.detectors), thread_name_prefix="detect")
        self.manager = manager
        self.sinks = list(sinks)
        self.show = show
        self.record_path = Path(record_path) if record_path else None
        self.record_fps = record_fps
        self._writer = None
        self.on_frame = on_frame
        self.stats = PipelineStats()
        self.recent_events: list[tuple[float, HazardEvent]] = []

    def process(self, frame: Frame) -> tuple[list[Detection], list[HazardEvent]]:
        t0 = time.perf_counter()
        dets: list[Detection] = []
        if self._pool is not None:
            for part in self._pool.map(lambda d: d(frame.image), self.detectors):
                dets.extend(part)
        else:
            for det in self.detectors:
                dets.extend(det(frame.image))
        ms = float(sum(det.last_ms for det in self.detectors))
        if self.hood is not None:
            self.manager.set_hood_frac(self.hood.update(frame.image))
        events = self.manager.update(dets, frame_index=frame.index)
        self.stats.latency_ms.append((time.perf_counter() - t0) * 1000.0)
        for ev in events:
            for s in self.sinks:
                s.send(ev)
        self.stats.frames += 1
        self.stats.detections += len(dets)
        self.stats.events += len(events)
        self.stats.infer_ms.append(ms)
        now = time.time()
        self.recent_events += [(now, e) for e in events]
        self.recent_events = [(t, e) for t, e in self.recent_events if now - t < 3.0]
        if self.show or self.record_path or self.on_frame:
            info = f"frame {frame.index}  t={frame.ts:6.1f}s  infer {ms:5.1f} ms  dets {len(dets)}"
            vis = draw(frame.image, dets, [e for _, e in self.recent_events], info, hood_frac=self.manager.hood_frac)
            if self.record_path:
                if self._writer is None:
                    self.record_path.parent.mkdir(parents=True, exist_ok=True)
                    h, w = vis.shape[:2]
                    self._writer = cv2.VideoWriter(str(self.record_path), cv2.VideoWriter_fourcc(*"mp4v"),
                                                   self.record_fps, (w, h))
                self._writer.write(vis)
            if self.show:
                cv2.imshow("wazeomatic", vis)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    raise KeyboardInterrupt
            if self.on_frame:
                self.on_frame(vis, dets, events)
        return dets, events

    def run(self, source: Iterable[Frame]) -> PipelineStats:
        try:
            for frame in source:
                self.process(frame)
        except KeyboardInterrupt:
            pass
        finally:
            self.close()
        return self.stats

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None
        for d in self.detectors:
            if hasattr(d, "close"):
                d.close()
        if self._writer is not None:
            self._writer.release()
            self._writer = None
        for s in self.sinks:
            s.close()
        if self.show:
            cv2.destroyAllWindows()
