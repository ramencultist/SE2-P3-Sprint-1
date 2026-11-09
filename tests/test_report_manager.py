"""
Module Name: test_report_manager.py
Description: Unit tests for the report manager's persistence, confidence, cooldown and event rules

Class Name: FakeClock
Description: Controllable clock so cooldown and window timing can be tested without waiting

Inputs: Synthetic detections and a fake clock
Outputs: pytest pass or fail results
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write tests for single-frame flicker, low confidence, duplicate suppression, unmapped labels and the event JSON
How you validated and revised the AI output: Proofreading, and running pytest against the report manager
The challenges or limitations you faced while using AI: Making timing-dependent behaviour deterministic with a fake clock
Authors: Blake Pennel
Creation Date: 11/5/2026
"""
from wazeomatic.report_manager import DEFAULT_LABEL_FLOORS, ReportConfig, ReportManager
from wazeomatic.schema import Detection


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def det(label="pothole", conf=0.9, bbox=(0.5, 0.6, 0.2, 0.1)):
    return Detection(label=label, confidence=conf, bbox=bbox)


def make(**kw):
    clock = FakeClock()
    cfg = ReportConfig(**kw)
    return ReportManager(cfg, source="sim", clock=clock), clock


def test_single_frame_does_not_report():
    rm, _ = make(window=10, min_hits=3)
    assert rm.update([det()]) == []
    assert rm.update([]) == []


def test_persistence_then_report_once():
    rm, _ = make(window=10, min_hits=3)
    assert rm.update([det(conf=0.6)]) == []     # above the 0.45 pothole floor, one hit
    assert rm.update([det(conf=0.7)]) == []
    events = rm.update([det(conf=0.9)])
    assert len(events) == 1
    ev = events[0]
    assert ev.hazard_type == "HAZARD_ON_ROAD_POTHOLE"
    assert ev.confidence == 0.9           # best hit in the window
    assert ev.waze.waze_category == "Hazard"
    # continued detections inside the cooldown stay silent
    for _ in range(20):
        assert rm.update([det()]) == []


def test_low_confidence_hits_do_not_count():
    rm, _ = make(window=10, min_hits=3, min_confidence=0.6)
    for _ in range(10):
        assert rm.update([det(conf=0.55)]) == []


def test_window_expires_old_hits():
    rm, _ = make(window=5, min_hits=3)
    rm.update([det()])
    rm.update([det()])
    for _ in range(5):
        rm.update([])
    assert rm.update([det()]) == []  # old two hits fell out of the window


def test_cooldown_per_type_and_global():
    rm, clock = make(window=10, min_hits=1, cooldown_s=60, global_cooldown_s=5)
    assert len(rm.update([det("pothole")])) == 1
    # different type blocked by the global cooldown
    assert rm.update([det("cone")]) == []
    clock.advance(6)
    assert len(rm.update([det("cone")])) == 1
    clock.advance(6)
    assert rm.update([det("pothole")]) == []   # still in per-type cooldown
    clock.advance(60)
    assert len(rm.update([det("pothole")])) == 1


def test_unmapped_labels_ignored():
    rm, _ = make(window=10, min_hits=1)
    assert rm.update([det("toothbrush"), det("car")]) == []


def test_stopped_vehicle_heuristic():
    rm, clock = make(window=10, min_hits=3, global_cooldown_s=0, stopped_vehicle_enabled=True)
    # car in the right shoulder region growing as we approach it
    assert rm.update([det("car", 0.9, (0.8, 0.6, 0.15, 0.15))]) == []
    assert rm.update([det("car", 0.9, (0.82, 0.62, 0.18, 0.18))]) == []
    events = rm.update([det("car", 0.9, (0.85, 0.65, 0.22, 0.22))])
    assert [e.hazard_type for e in events] == ["HAZARD_SHOULDER_CAR_STOPPED"]
    # a car ahead in our lane (centre of frame) is never a shoulder hazard
    rm2, _ = make(window=10, min_hits=3, global_cooldown_s=0, stopped_vehicle_enabled=True)
    for _ in range(5):
        assert rm2.update([det("car", 0.9, (0.5, 0.6, 0.2, 0.2))]) == []


def test_event_json_and_intent_extras():
    rm, _ = make(window=10, min_hits=1)
    ev = rm.update([det("cone", 0.77)])[0]
    d = ev.to_dict()
    assert d["hazard_type"] == "HAZARD_ON_ROAD_CONSTRUCTION"
    assert d["waze_subcategory"] == "Construction"
    assert d["schema"] == 1
    extras = ev.intent_extras()
    assert extras["hazard_type"] == "HAZARD_ON_ROAD_CONSTRUCTION"
    assert extras["confidence"] == "0.770"
    assert all(isinstance(v, str) for v in extras.values())


def test_hood_zone_ignored():
    rm, _ = make(window=10, min_hits=1)
    # bottom edge at 0.93 -> inside the lowest 10 % of the frame: the bonnet reflection
    assert rm.update([det("pothole", 0.9, (0.49, 0.88, 0.15, 0.10))]) == []
    # same box a little higher is a real road detection
    assert len(rm.update([det("pothole", 0.9, (0.49, 0.80, 0.15, 0.10))])) == 1
    rm2, _ = make(window=10, min_hits=1, hood_frac=0.0)
    assert len(rm2.update([det("pothole", 0.9, (0.49, 0.88, 0.15, 0.10))])) == 1


def test_default_pothole_floor_is_045():
    from wazeomatic.report_manager import ReportConfig, ReportManager
    from wazeomatic.schema import Detection

    assert DEFAULT_LABEL_FLOORS == {"pothole": 0.45}
    cfg = ReportConfig(global_cooldown_s=0)
    assert cfg.min_confidence_by_label == {"pothole": 0.45}
    rm = ReportManager(cfg, clock=lambda: 1000.0)
    weak = Detection("pothole", 0.42, (0.5, 0.5, 0.1, 0.1))
    for i in range(3):
        assert rm.update([weak], frame_index=i) == []
    strong = Detection("pothole", 0.46, (0.5, 0.5, 0.1, 0.1))
    events = []
    for i in range(3, 6):
        events += rm.update([strong], frame_index=i)
    assert len(events) == 1
    # the default dict is copied per config, not shared
    cfg.min_confidence_by_label["cone"] = 0.9
    assert "cone" not in ReportConfig().min_confidence_by_label
