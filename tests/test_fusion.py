"""
Module Name: test_fusion.py
Description: Unit tests for weighted boxes fusion, NMS fusion and per-class threshold parsing

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
import pytest

from wazeomatic.fusion import apply_class_thresholds, fuse_nms, fuse_wbf, parse_class_thresholds
from wazeomatic.schema import Detection


def det(label, conf, cx, cy, w=0.1, h=0.1, model="m"):
    return Detection(label=label, confidence=conf, bbox=(cx, cy, w, h), model=model)


def test_wbf_averages_overlapping_boxes_and_rewards_agreement():
    a = [det("pothole", 0.9, 0.50, 0.50)]
    b = [det("pothole", 0.7, 0.52, 0.50)]
    fused = fuse_wbf([a, b])
    assert len(fused) == 1
    f = fused[0]
    # weighted centre between 0.50 and 0.52, closer to the more confident box
    assert 0.505 < f.bbox[0] < 0.512
    assert f.confidence == pytest.approx((0.9 + 0.7) / 2)
    assert f.model == "ensemble"


def test_wbf_penalises_boxes_seen_by_one_model_only():
    a = [det("cone", 0.8, 0.2, 0.2)]
    b = []  # second model saw nothing
    fused = fuse_wbf([a, b])
    assert len(fused) == 1
    assert fused[0].confidence == pytest.approx(0.4)   # 0.8 * 1 / (1 + 1)
    assert fuse_wbf([a, b], conf_type="max")[0].confidence == pytest.approx(0.8)
    assert fuse_wbf([a, b], conf_type="box_avg")[0].confidence == pytest.approx(0.8)


def test_wbf_respects_model_weights():
    a = [det("cone", 0.6, 0.2, 0.2)]
    b = [det("cone", 0.6, 0.2, 0.2)]
    f = fuse_wbf([a, b], weights=[3, 1])
    assert f[0].confidence == pytest.approx(0.6)
    only_a = fuse_wbf([a, []], weights=[3, 1])
    assert only_a[0].confidence == pytest.approx(0.6 * 3 / 4)


def test_wbf_keeps_different_labels_and_far_boxes_apart():
    a = [det("pothole", 0.9, 0.5, 0.5), det("cone", 0.9, 0.5, 0.5)]
    b = [det("pothole", 0.9, 0.9, 0.9)]
    fused = fuse_wbf([a, b])
    assert sorted((d.label, round(d.bbox[0], 2)) for d in fused) == [("cone", 0.5), ("pothole", 0.5), ("pothole", 0.9)]


def test_wbf_uses_best_box_per_model_inside_a_cluster():
    # two near-duplicate boxes from the same model must not count as two models agreeing
    a = [det("cone", 0.8, 0.5, 0.5), det("cone", 0.6, 0.505, 0.5)]
    f = fuse_wbf([a, []])
    assert len(f) == 1
    assert f[0].confidence == pytest.approx(0.4)


def test_wbf_skip_threshold_and_empty():
    assert fuse_wbf([]) == []
    assert fuse_wbf([[det("cone", 0.1, 0.5, 0.5)]], skip_thr=0.2) == []


def test_nms_fusion_keeps_best_unchanged():
    a = [det("cone", 0.9, 0.50, 0.50, model="a")]
    b = [det("cone", 0.7, 0.52, 0.50, model="b")]
    f = fuse_nms([a, b])
    assert len(f) == 1 and f[0].model == "a" and f[0].bbox == a[0].bbox


def test_class_thresholds():
    dets = [det("pothole", 0.5, 0.5, 0.5), det("cone", 0.5, 0.5, 0.5), det("delineator", 0.3, 0.5, 0.5)]
    kept = apply_class_thresholds(dets, {"pothole": 0.6, "cone": 0.4}, default=0.35)
    assert [d.label for d in kept] == ["cone"]
    assert parse_class_thresholds("pothole=0.6, cone=0.4") == {"pothole": 0.6, "cone": 0.4}
    assert parse_class_thresholds(None) == {}
    with pytest.raises(ValueError):
        parse_class_thresholds("pothole")
