"""
Module Name: fusion.py
Description: Box fusion for model ensembles and two-pass inference: weighted boxes fusion, NMS fusion and per-class confidence floors

Inputs: Detections from several models or passes on the same frame
Outputs: One fused list of detections
External Sources: Claude Fable 5.1
Attributions:

Creation 11/6/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to implement Weighted Boxes Fusion (Solovyev et al. 2021) on normalized center-xywh boxes
How you validated and revised the AI output: Proofreading, and tests/test_fusion.py against hand-computed fused boxes; the Kotlin BoxFusion.kt is held to the same tests
The challenges or limitations you faced while using AI: Matching the reference WBF confidence rules while keeping the code small enough to port to Kotlin
Authors: Blake Pennel
Creation Date: 11/6/2026

Ported to the SE2-P3 repository 11/17/2026 by Luke Reicherter with Claude Opus 5.5 for Sprint 2 (requirements 7 and 9); code unchanged from the team's wazeomatic development repository unless a Revision entry says otherwise
"""
from __future__ import annotations
"""Box fusion for ensembles: combine detections from several models or passes.

Two strategies:

* ``fuse_nms`` - keep the highest-confidence box per cluster (what ``merge_detections`` does).
* ``fuse_wbf`` - Weighted Boxes Fusion (Solovyev et al. 2021): boxes of the same class that
  overlap are averaged, weighted by confidence and by a per-model weight, and the fused
  confidence is the weighted mean over *all* models, so a box that only one of three models
  saw is scored down (``conf_type="avg"``). This rewards agreement between models, which is
  exactly what a hazard reporter wants before it files a report.

Everything works on ``Detection`` objects with normalized center-xywh boxes, so it does not
care which model (hazard 416, hazard 640, zoom pass, COCO) produced them. The Kotlin app
mirrors ``fuse_wbf`` in ``BoxFusion.kt``; keep the two in sync.
"""

from typing import Sequence

import numpy as np

from .schema import Detection


def _xyxy(d: Detection) -> np.ndarray:
    cx, cy, w, h = d.bbox
    return np.array([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], dtype=np.float64)


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _to_detection(xyxy: np.ndarray, label: str, conf: float, model: str) -> Detection:
    x1, y1, x2, y2 = (float(v) for v in np.clip(xyxy, 0.0, 1.0))
    return Detection(label=label, confidence=float(conf),
                     bbox=((x1 + x2) / 2, (y1 + y2) / 2, max(x2 - x1, 0.0), max(y2 - y1, 0.0)), model=model)


def fuse_nms(det_lists: Sequence[Sequence[Detection]], iou_thr: float = 0.5) -> list[Detection]:
    """Per-class greedy NMS across all lists: the best box of each cluster survives unchanged."""
    from .detector import merge_detections

    return merge_detections([d for lst in det_lists for d in lst], iou_thr)


def fuse_wbf(det_lists: Sequence[Sequence[Detection]], weights: Sequence[float] | None = None,
             iou_thr: float = 0.55, skip_thr: float = 0.0, conf_type: str = "avg",
             model_name: str = "ensemble") -> list[Detection]:
    """Weighted Boxes Fusion over the detections of ``len(det_lists)`` models.

    conf_type:
      ``avg``  - sum(conf_i * w_i) / sum(w_all): a box missed by a model is penalised (default)
      ``max``  - highest member confidence of the cluster
      ``box_avg`` - mean confidence of the boxes in the cluster, no penalty for absent models
    Boxes below ``skip_thr`` are ignored. Clusters are per label.
    """
    n_models = len(det_lists)
    if n_models == 0:
        return []
    w = np.ones(n_models, dtype=np.float64) if weights is None else np.asarray(weights, dtype=np.float64)
    if len(w) != n_models:
        raise ValueError("one weight per model expected")
    w_sum = float(w.sum())

    # flatten: (label, conf*w, conf, xyxy, model_idx)
    boxes = []
    for mi, lst in enumerate(det_lists):
        for d in lst:
            if d.confidence < skip_thr:
                continue
            boxes.append((d.label, d.confidence * w[mi], d.confidence, _xyxy(d), mi))
    if not boxes:
        return []
    boxes.sort(key=lambda b: -b[1])

    # clusters: list of dict(label, members=[(wconf, conf, xyxy, mi)], fused=xyxy)
    clusters: list[dict] = []
    for label, wconf, conf, xyxy, mi in boxes:
        best, best_iou = None, iou_thr
        for c in clusters:
            if c["label"] != label:
                continue
            iou = _iou(c["fused"], xyxy)
            if iou >= best_iou:
                best, best_iou = c, iou
        if best is None:
            clusters.append({"label": label, "members": [(wconf, conf, xyxy, mi)], "fused": xyxy.copy()})
        else:
            best["members"].append((wconf, conf, xyxy, mi))
            ws = np.array([m[0] for m in best["members"]])
            xs = np.stack([m[2] for m in best["members"]])
            best["fused"] = (xs * ws[:, None]).sum(0) / ws.sum()

    out: list[Detection] = []
    for c in clusters:
        members = c["members"]
        if conf_type == "max":
            conf = max(m[1] for m in members)
        elif conf_type == "box_avg":
            conf = float(np.mean([m[1] for m in members]))
        else:  # avg: one vote per model, absent models count as zero
            per_model = {}
            for wconf, _, _, mi in members:
                per_model[mi] = max(per_model.get(mi, 0.0), wconf)  # best box of each model
            conf = sum(per_model.values()) / w_sum
        out.append(_to_detection(c["fused"], c["label"], min(conf, 1.0), model_name))
    out.sort(key=lambda d: -d.confidence)
    return out


def apply_class_thresholds(dets: Sequence[Detection], thresholds: dict[str, float] | None,
                           default: float = 0.0) -> list[Detection]:
    """Drop detections below a per-label confidence (labels not listed use ``default``)."""
    if not thresholds and default <= 0:
        return list(dets)
    thresholds = thresholds or {}
    return [d for d in dets if d.confidence >= thresholds.get(d.label, default)]


def parse_class_thresholds(spec: str | None) -> dict[str, float]:
    """``"pothole=0.5,cone=0.35"`` -> {"pothole": 0.5, "cone": 0.35}."""
    out: dict[str, float] = {}
    if not spec:
        return out
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"bad class threshold '{part}', expected label=value")
        k, v = part.split("=", 1)
        out[k.strip()] = float(v)
    return out
