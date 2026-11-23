"""
Module Name: test_sprint2_requirements.py
Description: Acceptance tests for the Sprint 2 requirements, one test per criterion: 7 (in-road hazard detection) and 9 (construction identification), as written in the Sprint 2 Requirements Artifacts of the architecture document

Class Name: FakeClock
Description: Controllable clock so cooldowns and the persistence window can be tested without waiting

Class Name: ScriptedDetector
Description: Stands in for the ONNX model: returns the detections a labelled test clip script says are visible on each frame

Inputs: Synthetic detections, scripted test clips, a generated ONNX model and deliberately broken model files
Outputs: pytest pass or fail results, one per acceptance criterion
External Sources: Claude Opus 5.5
Attributions:

Creation 11/17/2026 - Luke Reicherter and Claude Opus 5.5:
A description of how and why AI was used: Claude Opus 5.5 used to turn each Given/When/Then criterion for requirements 7 and 9 into a test against the report manager, the detector builder and the pipeline
How you validated and revised the AI output: Proofreading each test against the criteria table, running pytest, and checking that each test fails when the rule it covers is removed from report_manager.py or build.py
The challenges or limitations you faced while using AI: Criteria 9.6 (MacroDroid retry) and 9.7 (emulator run) need a phone or the emulator, so they are covered by the demo and run_e2e.py, not here; 7.8 is checked with a scripted clip because the labelled real footage is not in the repository
Authors: Luke Reicherter
Creation Date: 11/17/2026
Revision 11/22/2026: Codex added checks for the emulator runner's construction evidence.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from tools.emulator.run_e2e import CONSTRUCTION, Runner
from wazeomatic.build import LOAD_ERRORS, DetectorSpec, build_detectors
from wazeomatic.pipeline import Pipeline
from wazeomatic.report_manager import (CARRIED_OBJECT, COOLDOWN, HOOD_ZONE, LOW_CONFIDENCE, UNCONFIRMED,
                                       ReportConfig, ReportManager)
from wazeomatic.schema import Detection
from wazeomatic.sinks import JsonlSink
from wazeomatic.sources import Frame

AHEAD = (0.5, 0.55, 0.08, 0.06)       # small box on the road ahead, well above the hood zone
SIDEWALK = (0.92, 0.6, 0.05, 0.08)    # right edge of the frame, outside the driving lane


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


def make(**kw):
    kw.setdefault("global_cooldown_s", 0.0)
    clock = FakeClock()
    return ReportManager(ReportConfig(**kw), source="sim", clock=clock), clock


def det(label: str, conf: float = 0.8, bbox=AHEAD, model: str = "hazard") -> Detection:
    return Detection(label, conf, bbox, model)


def feed(rm, clock, frames, dt: float = 0.1):
    """Feed a list of per-frame detection lists, 10 inferences a second; return all events."""
    events = []
    for dets in frames:
        events += rm.update(dets)
        clock.advance(dt)
    return events


def reasons(rm) -> list[str]:
    return [d.reason for d in rm.decisions]


# ----------------------------------------------------------------- requirement 7: in-road hazards

def test_7_1_pothole_in_3_of_10_frames_reports_hazard_pothole():
    rm, clock = make()
    frames = [[det("pothole", 0.7)], [], [det("pothole", 0.7)], [], [det("pothole", 0.7)]]
    events = feed(rm, clock, frames)
    assert [e.hazard_type for e in events] == ["HAZARD_ON_ROAD_POTHOLE"]
    ev = events[0]
    extras = ev.intent_extras()
    assert (extras["waze_category"], extras["waze_subcategory"]) == ("Hazard", "Pothole")
    # the message carries category, confidence and timestamp (the JSON contract from 2.2)
    msg = json.loads(ev.to_json())
    assert msg["hazard_type"] == "HAZARD_ON_ROAD_POTHOLE" and msg["confidence"] == pytest.approx(0.7)
    assert msg["ts"] > 0


def test_7_2_object_in_lane_reports_hazard_object():
    rm, clock = make()
    events = feed(rm, clock, [[det("suitcase", 0.6, model="coco")]] * 3)
    assert [e.hazard_type for e in events] == ["HAZARD_ON_ROAD_OBJECT"]
    assert events[0].intent_extras()["waze_subcategory"] == "Object"


def test_7_2_debris_model_class_reports_hazard_object():
    rm, clock = make()
    events = feed(rm, clock, [[det("debris", 0.6)]] * 3)
    assert [e.hazard_type for e in events] == ["HAZARD_ON_ROAD_OBJECT"]


def test_7_2_object_outside_lane_or_carried_is_not_debris():
    rm, clock = make()
    assert feed(rm, clock, [[det("suitcase", 0.6, bbox=SIDEWALK, model="coco")]] * 5) == []
    # a bag inside a person box is being carried, not lying in the road
    person = det("person", 0.9, bbox=(0.5, 0.55, 0.2, 0.5), model="coco")
    rm2, clock2 = make()
    assert feed(rm2, clock2, [[person, det("backpack", 0.6, model="coco")]] * 5) == []
    assert CARRIED_OBJECT in reasons(rm2)


def test_7_2_road_objects_can_be_turned_off():
    rm, clock = make(road_object_enabled=False)
    assert feed(rm, clock, [[det("suitcase", 0.6, model="coco")]] * 5) == []


def test_7_3_single_frame_flicker_is_logged_not_reported():
    rm, clock = make()
    frames = [[det("pothole")], [], [], [], [], [det("object_never_mapped")], [], [], [], [], [det("pothole")]]
    assert feed(rm, clock, frames) == []
    unconfirmed = [d for d in rm.decisions if d.reason == UNCONFIRMED]
    assert len(unconfirmed) == 2 and unconfirmed[0].label == "pothole"
    assert "1/3 hits in last 10 frames" in unconfirmed[0].detail


def test_7_4_below_threshold_is_logged_with_its_score():
    rm, clock = make()
    assert feed(rm, clock, [[det("pothole", 0.42)]] * 5) == []   # pothole floor is 0.45
    low = [d for d in rm.decisions if d.reason == LOW_CONFIDENCE]
    assert len(low) == 5
    assert low[0].confidence == pytest.approx(0.42) and "0.45" in low[0].detail
    assert low[0].to_dict()["confidence"] == 0.42


def test_7_5_same_type_within_60s_is_suppressed_and_logged():
    rm, clock = make(cooldown_s=60)
    first = feed(rm, clock, [[det("pothole")]] * 3)
    assert len(first) == 1
    # the pothole stays in view for another 10 s: no duplicate event
    assert feed(rm, clock, [[det("pothole")]] * 100) == []
    suppressed = [d for d in rm.decisions if d.reason == COOLDOWN]
    assert suppressed and all(d.hazard == "HAZARD_ON_ROAD_POTHOLE" for d in suppressed)
    # logged once per 10-frame window, not on every one of the 100 frames
    assert len(suppressed) <= 10


def test_7_6_box_in_hood_zone_is_ignored_and_logged():
    rm, clock = make(hood_frac=0.2)
    on_hood = (0.5, 0.92, 0.3, 0.1)   # bottom edge at 0.97, inside the lowest 20 %
    assert feed(rm, clock, [[det("pothole", 0.9, bbox=on_hood)]] * 5) == []
    assert reasons(rm) == [HOOD_ZONE] * 5


def _tiny_onnx(path, n_classes: int = 1) -> None:
    """A valid YOLO-shaped ONNX file that never detects anything (all outputs zero)."""
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper

    size = 40
    cells = 3 * size * size // (4 + n_classes)
    graph = helper.make_graph(
        [helper.make_node("Reshape", ["images", "shape"], ["r"]),
         helper.make_node("Mul", ["r", "zero"], ["output0"])],
        "tiny",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, size, size])],
        [helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 4 + n_classes, cells])],
        initializer=[helper.make_tensor("shape", TensorProto.INT64, [3], [1, 4 + n_classes, cells]),
                     helper.make_tensor("zero", TensorProto.FLOAT, [], [0.0])])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    onnx.save(model, str(path))


@pytest.mark.parametrize("problem", ["missing", "corrupt", "no_labels"])
def test_7_7_broken_hazard_model_is_logged_and_others_keep_running(tmp_path, problem):
    hazard = tmp_path / "hazard_yolo11n.onnx"
    if problem == "corrupt":
        hazard.write_bytes(b"this is not an onnx file")
        hazard.with_suffix(".labels.txt").write_text("pothole\n")
    elif problem == "no_labels":
        _tiny_onnx(hazard)
    coco = tmp_path / "coco_yolo11n.onnx"
    _tiny_onnx(coco)
    coco.with_suffix(".labels.txt").write_text("person\n")

    LOAD_ERRORS.clear()
    dets = build_detectors(DetectorSpec(hazard_models=[hazard], coco_model=coco))   # must not raise
    assert [d.name for d in dets] == ["coco"]
    assert len(LOAD_ERRORS) == 1 and LOAD_ERRORS[0][0] == str(hazard)
    # and the remaining model still runs on a frame
    assert dets[0](np.zeros((72, 128, 3), np.uint8)) == []


class ScriptedDetector:
    """Plays back what a labelled clip script says is visible on each frame."""

    name = "scripted"
    last_ms = 0.0

    def __init__(self, script):
        self.script = script   # frame index -> list[Detection]
        self.i = 0

    def __call__(self, image):
        out = self.script.get(self.i, [])
        self.i += 1
        return out


def test_7_8_labelled_clip_gives_exactly_one_event_per_hazard(tmp_path):
    """A 90 s clip at 10 inferences/s: a pothole at 5-7 s, debris at 40-41 s, a work zone at
    70-85 s, plus single-frame flickers. The labels say three hazards, so three events."""
    script = {}
    for i in range(50, 70, 2):
        script[i] = [det("pothole", 0.7)]
    for i in range(400, 410):
        script[i] = [det("suitcase", 0.6, model="coco")]
    for i in range(700, 850):
        script[i] = [det("cone", 0.6), det("construction_sign", 0.7)]
    for i in (120, 300, 600):
        script[i] = [det("pothole", 0.9)]     # flickers
    clock = FakeClock()
    rm = ReportManager(ReportConfig(), source="sim", clock=clock)
    out = tmp_path / "events.jsonl"
    pipe = Pipeline([ScriptedDetector(script)], rm, [JsonlSink(out)], parallel=False)

    def frames():
        for i in range(900):
            yield Frame(np.zeros((72, 128, 3), np.uint8), i, i / 10)
            clock.advance(0.1)

    pipe.run(frames())
    events = [json.loads(ln) for ln in out.read_text().splitlines()]
    assert [e["hazard_type"] for e in events] == [
        "HAZARD_ON_ROAD_POTHOLE", "HAZARD_ON_ROAD_OBJECT", "HAZARD_ON_ROAD_CONSTRUCTION"]
    assert [e["waze_subcategory"] for e in events] == ["Pothole", "Object", "Construction"]


# ------------------------------------------------------------ requirement 9: construction zones

@pytest.mark.parametrize("label", ["cone", "construction_sign", "delineator", "barricade", "worker"])
def test_9_1_each_construction_class_reports_hazard_construction(label):
    rm, clock = make()
    events = feed(rm, clock, [[det(label, 0.6)]] * 3)
    assert [e.hazard_type for e in events] == ["HAZARD_ON_ROAD_CONSTRUCTION"]
    assert events[0].intent_extras()["waze_subcategory"] == "Construction"


def test_9_2_several_construction_classes_merge_into_one_event():
    rm, clock = make()
    frame = [det("cone", 0.55, bbox=(0.4, 0.6, 0.03, 0.05)), det("construction_sign", 0.8),
             det("worker", 0.6, bbox=(0.65, 0.55, 0.04, 0.1))]
    events = feed(rm, clock, [frame] * 5)
    assert len(events) == 1
    assert events[0].hazard_type == "HAZARD_ON_ROAD_CONSTRUCTION"
    assert events[0].label == "construction_sign"   # the strongest box is the one reported


def test_9_3_work_zone_gives_one_report_per_cooldown():
    rm, clock = make(cooldown_s=60)
    zone = [[det("cone", 0.6), det("barricade", 0.7)]]
    # 100 s of work zone at 10 inferences/s
    events = feed(rm, clock, zone * 1000)
    assert len(events) == 2                       # at t=0 and again once the 60 s cooldown ends
    assert events[1].ts - events[0].ts >= 60
    assert any(d.reason == COOLDOWN and d.hazard == "HAZARD_ON_ROAD_CONSTRUCTION" for d in rm.decisions)


def test_9_4_stray_cone_seen_briefly_is_logged_not_reported():
    rm, clock = make()
    frames = [[det("cone", 0.7, bbox=SIDEWALK)], [det("cone", 0.7, bbox=SIDEWALK)]] + [[]] * 10
    assert feed(rm, clock, frames) == []
    assert reasons(rm) == [UNCONFIRMED, UNCONFIRMED]


def test_9_5_low_light_low_confidence_construction_is_logged():
    rm, clock = make()
    assert feed(rm, clock, [[det("cone", 0.37), det("construction_sign", 0.38)]] * 4) == []
    low = [d for d in rm.decisions if d.reason == LOW_CONFIDENCE]
    assert {d.label for d in low} == {"cone", "construction_sign"}
    assert all(d.confidence < 0.40 for d in low)


def test_decision_callback_receives_every_entry():
    seen = []
    rm = ReportManager(ReportConfig(global_cooldown_s=0), clock=FakeClock(), on_decision=seen.append)
    rm.update([det("pothole", 0.1)])
    assert len(seen) == 1 and seen[0].reason == LOW_CONFIDENCE


@pytest.mark.parametrize(("event_count", "report_count", "report_ts", "expected"), [
    (1, 1, "1730000000.123", 0),
    (0, 1, "1730000000.123", 1),
    (1, 0, "1730000000.123", 1),
    (2, 1, "1730000000.123", 1),
    (1, 2, "1730000000.123", 1),
    (1, 1, "1730000000.124", 1),
])
def test_9_7_runner_requires_one_matching_report(tmp_path, event_count, report_count, report_ts, expected):
    runner = Runner("emulator-5554", tmp_path)
    event = {"hazard_type": CONSTRUCTION, "ts": 1730000000.12345}
    line = f"11-22 20:00:00.123 111 222 I WazeomaticEvent: {json.dumps(event)}"
    report = {"hazard_type": CONSTRUCTION, "category": "Hazard", "subcategory": "Construction",
              "action": "send", "via": "auto_macro", "event_ts": report_ts}

    runner.check_construction_replay([line] * event_count, [report] * report_count)

    assert runner.summary() == expected
