"""
Module Name: test_eval_events.py
Description: Unit tests for the event-level evaluation harness in tools/eval_events.py

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
import importlib.util
import sys
from pathlib import Path

import numpy as np

from wazeomatic.report_manager import ReportConfig
from wazeomatic.schema import DEFAULT_CLASS_TO_HAZARD, Detection

_spec = importlib.util.spec_from_file_location(
    "eval_events", Path(__file__).resolve().parents[1] / "tools" / "eval_events.py")
ee = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ee  # dataclasses resolve string annotations through sys.modules
_spec.loader.exec_module(ee)


def det(label="pothole", conf=0.9, bbox=(0.5, 0.6, 0.2, 0.1)):
    return Detection(label=label, confidence=conf, bbox=bbox)


# ------------------------------------------------------------------ AP / matching
def test_average_precision_perfect_and_empty():
    flags = np.array([1.0, 1.0, 1.0])
    confs = np.array([0.9, 0.8, 0.7])
    assert ee.average_precision(flags, confs, n_gt=3) == 1.0
    assert ee.average_precision(flags, confs, n_gt=6) == 0.5     # half the GTs never found
    assert ee.average_precision(np.array([]), np.array([]), n_gt=3) == 0.0


def test_average_precision_interpolated_value():
    # ranked: TP, FP, TP, FP with 2 GTs -> precision 1.0 at recall 0.5, 2/3 at recall 1.0
    flags = np.array([1.0, 0.0, 1.0, 0.0])
    confs = np.array([0.9, 0.8, 0.7, 0.6])
    ap = ee.average_precision(flags, confs, n_gt=2)
    assert abs(ap - (0.5 * 1.0 + 0.5 * (2 / 3))) < 1e-6


def test_greedy_matching_and_size_buckets():
    gts = [ee.GTBox("pothole", (0.1, 0.1, 0.3, 0.3), "medium"),
           ee.GTBox("pothole", (0.6, 0.6, 0.8, 0.8), "small"),
           ee.GTBox("cone", (0.1, 0.6, 0.3, 0.8), "large")]
    preds = [ee.PredBox("pothole", 0.9, (0.1, 0.1, 0.3, 0.3), "medium", "a"),   # exact hit
             ee.PredBox("pothole", 0.8, (0.11, 0.1, 0.31, 0.3), "medium", "a"),  # duplicate -> FP
             ee.PredBox("cone", 0.7, (0.6, 0.6, 0.8, 0.8), "small", "a"),        # wrong class -> FP
             ee.PredBox("cone", 0.6, (0.1, 0.6, 0.3, 0.8), "large", "a")]
    m = ee.match_predictions(gts, preds)
    assert [g is not None for _, g in m] == [True, False, False, True]
    res = ee.box_metrics({"a": gts}, {"a": preds}, ["pothole", "cone"])
    assert res["pothole"]["all"]["n_gt"] == 2 and res["pothole"]["all"]["tp"] == 1
    assert res["pothole"]["all"]["recall"] == 0.5
    assert res["pothole"]["medium"]["ap50"] == 1.0          # TP ranked first, then the duplicate FP
    assert res["pothole"]["medium"]["fp"] == 1
    assert res["pothole"]["small"] == {"n_gt": 1, "n_pred": 0, "tp": 0, "fp": 0,
                                       "recall": 0.0, "precision": None, "ap50": 0.0}
    assert res["cone"]["large"]["ap50"] == 1.0
    assert res["all"]["all"]["n_gt"] == 3 and res["all"]["all"]["tp"] == 2


def test_letterbox_side_and_bucket():
    # 1280x720 source letterboxed to 416: scale 0.325; a 100x40 px box -> 32.5 px -> medium
    px = ee.letterbox_side_px(100 / 1280, 40 / 720, 1280, 720, 416)
    assert abs(px - 32.5) < 1e-6
    assert ee.size_bucket(px) == "medium"
    assert ee.size_bucket(10) == "small" and ee.size_bucket(64) == "large"


# ------------------------------------------------------------------ events
def test_replay_events_needs_persistence_and_respects_cooldown():
    cfg = ReportConfig(window=10, min_hits=3, min_confidence=0.45)
    steady = [[det("pothole", 0.8)] for _ in range(12)]
    assert ee.replay_events(steady, cfg) == ["HAZARD_ON_ROAD_POTHOLE"]    # once, then muted by cooldown
    flicker = [[det("pothole", 0.8)], [], [], [], [], [], [], [], [], [], [det("pothole", 0.8)], []]
    assert ee.replay_events(flicker, cfg) == []
    weak = [[det("cone", 0.40)] for _ in range(12)]
    assert ee.replay_events(weak, cfg) == []
    assert ee.replay_events(weak, ReportConfig(min_hits=3, min_confidence=0.35)) == ["HAZARD_ON_ROAD_CONSTRUCTION"]


def test_event_summary_counts():
    P, C = "HAZARD_ON_ROAD_POTHOLE", "HAZARD_ON_ROAD_CONSTRUCTION"
    cases = [
        ee.EventCase("a", "pothole_ivcnz", {P}, {P}),          # TP pothole
        ee.EventCase("b", "pothole_ivcnz", {P}, set()),        # FN pothole
        ee.EventCase("c", "starline", {C}, {C, P}),            # TP construction, FP pothole
        ee.EventCase("d", "rdd2022_us", set(), set()),         # true negative
        ee.EventCase("e", "rdd2022_us", set(), {C}),           # FP construction
    ]
    s = ee.summarize_events(cases, [P, C])
    assert s["per_type"][P] == {**ee.prf(1, 1, 1), "tn": 2}
    assert s["per_type"][C] == {**ee.prf(1, 1, 0), "tn": 3}
    assert s["overall"]["tp"] == 2 and s["overall"]["fp"] == 2 and s["overall"]["fn"] == 1
    assert s["overall"]["exact_set_match"] == 2
    assert s["confusion"]["none"] == {"none": 1, C: 1}
    assert s["confusion"][P] == {P: 1, "none": 1}
    by_src = ee.summarize_by_source(cases, [P, C])
    assert set(by_src) == {"pothole_ivcnz", "starline", "rdd2022_us"}
    assert by_src["pothole_ivcnz"]["overall"]["recall"] == 0.5


def test_hazard_types_and_source_prefix():
    assert ee.hazard_types_for(["cone", "delineator", "pothole"], DEFAULT_CLASS_TO_HAZARD) == {
        "HAZARD_ON_ROAD_CONSTRUCTION", "HAZARD_ON_ROAD_POTHOLE"}
    assert ee.hazard_types_for(["toothbrush"], DEFAULT_CLASS_TO_HAZARD) == set()
    assert ee.source_of("pothole_ivcnz_img-1020") == "pothole_ivcnz"
    assert ee.source_of("rdd2022_us_United_States_000004") == "rdd2022_us"
    assert ee.source_of("starline_0016ad13-493c") == "starline"
    assert ee.source_of("cone_voc_100") == "cone_voc"


def test_jitter_frame_keeps_shape_and_is_deterministic():
    img = np.random.default_rng(1).integers(0, 255, (90, 160, 3), dtype=np.uint8)
    a = ee.jitter_frame(img, np.random.default_rng(7))
    b = ee.jitter_frame(img, np.random.default_rng(7))
    assert a.shape == img.shape and a.dtype == np.uint8
    assert np.array_equal(a, b)
    assert not np.array_equal(a, img)
