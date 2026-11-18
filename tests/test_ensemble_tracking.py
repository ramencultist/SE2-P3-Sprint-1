"""
Module Name: test_ensemble_tracking.py
Description: Unit tests for ensemble detector, spatial tracking, per-label floors and the prefetching frame source

Class Name: FakeDetector
Description: Stand-in detector returning fixed boxes

Class Name: _ListSource
Description: Frame source over a list of frames

Inputs: Synthetic detections and frames
Outputs: pytest pass or fail results
External Sources: Claude Fable 5.1
Attributions:

Creation 11/6/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the unit tests alongside the module they cover
How you validated and revised the AI output: Proofreading, and running pytest
The challenges or limitations you faced while using AI: Making the tests deterministic without a model file
Authors: Blake Pennel
Creation Date: 11/6/2026

Ported to the SE2-P3 repository 11/17/2026 by Luke Reicherter with Claude Opus 5.5 for Sprint 2 (requirements 7 and 9); code unchanged from the team's wazeomatic development repository unless a Revision entry says otherwise
"""
import numpy as np
import pytest

from wazeomatic.detector import ClassFilter, EnsembleDetector
from wazeomatic.report_manager import ReportConfig, ReportManager
from wazeomatic.schema import Detection
from wazeomatic.sources import Frame, Prefetch


class FakeDetector:
    def __init__(self, name, labels, dets, size=416, ms=1.0):
        self.name, self.labels, self.size = name, list(labels), size
        self.conf_thres, self.iou_thres = 0.25, 0.5
        self._dets, self.last_ms = dets, ms
        self.calls = 0

    def __call__(self, frame):
        self.calls += 1
        return list(self._dets)


def det(label, conf, cx, cy, w=0.1, h=0.1, model="m"):
    return Detection(label=label, confidence=conf, bbox=(cx, cy, w, h), model=model)


FRAME = np.zeros((72, 128, 3), dtype=np.uint8)


def test_ensemble_runs_members_and_fuses():
    a = FakeDetector("a", ["pothole", "cone"], [det("pothole", 0.9, 0.5, 0.5)], ms=3)
    b = FakeDetector("b", ["pothole"], [det("pothole", 0.7, 0.51, 0.5)], ms=5)
    ens = EnsembleDetector([a, b])
    out = ens(FRAME)
    assert a.calls == 1 and b.calls == 1
    assert len(out) == 1 and out[0].label == "pothole" and out[0].model == "hazard" or out[0].model == "ensemble"
    assert out[0].confidence == pytest.approx(0.8)
    assert ens.labels == ["pothole", "cone"]
    assert ens.last_ms == 8
    ens.close()


def test_ensemble_sequential_matches_parallel():
    a = FakeDetector("a", ["cone"], [det("cone", 0.6, 0.2, 0.2)])
    b = FakeDetector("b", ["cone"], [])
    par = EnsembleDetector([a, b], parallel=True)(FRAME)
    seq = EnsembleDetector([a, b], parallel=False)(FRAME)
    assert par == seq
    assert par[0].confidence == pytest.approx(0.3)


def test_ensemble_nms_mode_and_class_floor():
    a = FakeDetector("a", ["cone"], [det("cone", 0.6, 0.2, 0.2, model="a")])
    b = FakeDetector("b", ["cone"], [det("cone", 0.8, 0.2, 0.2, model="b")])
    out = EnsembleDetector([a, b], fusion="nms")(FRAME)
    assert len(out) == 1 and out[0].model == "b"
    assert EnsembleDetector([a, b], fusion="nms", class_conf={"cone": 0.9})(FRAME) == []
    with pytest.raises(ValueError):
        EnsembleDetector([a, b], weights=[1])


def test_class_filter_wrapper():
    a = FakeDetector("a", ["cone", "pothole"], [det("cone", 0.5, 0.2, 0.2), det("pothole", 0.5, 0.6, 0.6)])
    f = ClassFilter(a, {"cone": 0.7})
    assert [d.label for d in f(FRAME)] == ["pothole"]
    assert f.last_ms == 1.0


def _manager(track_iou=0.0, **kw):
    cfg = ReportConfig(window=10, min_hits=3, min_confidence=0.45, cooldown_s=60, global_cooldown_s=0,
                       track_iou=track_iou, **kw)
    clock = {"t": 1000.0}
    rm = ReportManager(cfg, clock=lambda: clock["t"])
    return rm, clock


def test_tracking_rejects_spatially_inconsistent_hits():
    rm, clock = _manager(track_iou=0.3)
    # three potholes in three corners: same class, never the same object
    spots = [(0.1, 0.1), (0.9, 0.1), (0.5, 0.8)]
    events = []
    for i, (x, y) in enumerate(spots):
        events += rm.update([det("pothole", 0.9, x, y, 0.08, 0.08)], frame_index=i)
        clock["t"] += 0.1
    assert events == []
    # the untracked manager would have reported
    rm2, clock2 = _manager()
    ev2 = []
    for i, (x, y) in enumerate(spots):
        ev2 += rm2.update([det("pothole", 0.9, x, y, 0.08, 0.08)], frame_index=i)
    assert len(ev2) == 1


def test_tracking_accepts_an_approaching_object():
    rm, clock = _manager(track_iou=0.3)
    events = []
    # a pothole drifting down and growing as the car approaches
    for i, (y, s) in enumerate([(0.55, 0.05), (0.60, 0.06), (0.66, 0.075)]):
        events += rm.update([det("pothole", 0.8, 0.5, y, s, s)], frame_index=i)
        clock["t"] += 0.1
    assert len(events) == 1 and events[0].hazard_type == "HAZARD_ON_ROAD_POTHOLE"


def test_tracking_keeps_separate_objects_apart_and_trims_window():
    rm, clock = _manager(track_iou=0.3)
    events = []
    # two cones far apart, each seen twice: neither reaches min_hits 3
    for i in range(2):
        events += rm.update([det("cone", 0.8, 0.2, 0.5), det("cone", 0.8, 0.8, 0.5)], frame_index=i)
    assert events == []
    # the left cone seen a third time -> one report
    events += rm.update([det("cone", 0.8, 0.21, 0.5)], frame_index=2)
    assert len(events) == 1
    # window trimming: a single old hit far back must not help a new tracklet
    rm2, _ = _manager(track_iou=0.3)
    rm2.update([det("cone", 0.8, 0.5, 0.5)], frame_index=0)
    assert rm2.update([det("cone", 0.8, 0.5, 0.5)], frame_index=20) == []
    assert rm2.update([det("cone", 0.8, 0.5, 0.5)], frame_index=21) == []
    assert len(rm2.update([det("cone", 0.8, 0.5, 0.5)], frame_index=22)) == 1


def test_per_label_confidence_floor():
    rm, _ = _manager(min_confidence_by_label={"cone": 0.9})
    for i in range(3):
        assert rm.update([det("cone", 0.8, 0.5, 0.5)], frame_index=i) == []
    for i in range(3, 6):
        ev = rm.update([det("construction_sign", 0.5, 0.5, 0.5)], frame_index=i)
    assert len(ev) == 1


class _ListSource:
    width, height, fps, frame_count = 128, 72, 30.0, 5

    def __init__(self):
        self.released = False

    @property
    def effective_fps(self):
        return 10.0

    def __iter__(self):
        for i in range(5):
            yield Frame(index=i, ts=i / 10, image=FRAME)

    def release(self):
        self.released = True


def test_prefetch_yields_all_frames_in_order_and_releases():
    src = _ListSource()
    pf = Prefetch(src, depth=2)
    assert pf.width == 128 and pf.effective_fps == 10.0
    assert [f.index for f in pf] == [0, 1, 2, 3, 4]
    pf.release()
    assert src.released
