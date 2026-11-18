"""
Module Name: eval_events.py
Description: Event-level evaluation of the pipeline: box AP on held-out images, event recall on synthetic clips, and false reports on real hazard-free footage (criterion 7.8)

Class Name: StepClock
Description: Fake clock advanced one inference step at a time

Class Name: GTBox
Description: Ground-truth box

Class Name: PredBox
Description: Predicted box

Class Name: EventCase
Description: One synthetic clip and the hazard it should report

Class Name: ValImage
Description: One held-out validation image

Inputs: An ONNX model, a YOLO-format dataset and optional real clips
Outputs: results.md and JSON metrics under runs/eval/
External Sources: Claude Fable 5.1
Attributions:

Creation 11/6/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the evaluation harness that scores reports rather than boxes
How you validated and revised the AI output: Proofreading, tests/test_eval_events.py, and comparing its numbers with Ultralytics val on the same split
The challenges or limitations you faced while using AI: Defining a fair event match when one hazard can produce several boxes over many frames
Authors: Blake Pennel
Creation Date: 11/6/2026

Ported to the SE2-P3 repository 11/17/2026 by Luke Reicherter with Claude Opus 5.5 for Sprint 2 (requirements 7 and 9); code unchanged from the team's wazeomatic development repository unless a Revision entry says otherwise
"""
from __future__ import annotations
"""Evaluate the detection pipeline at the level that matters: hazard report events.

Three views of the same model, in increasing order of realism:

1. **Boxes** - per-class AP50 and recall on the validation split, split by object size
   measured on the letterboxed network input (small < 24 px, medium 24-64 px, large > 64 px).
2. **Events on synthetic sequences** - every val image is turned into a short clip
   (random affine jitter + brightness drift, optionally CameraSim degradation) and pushed
   through ``OnnxDetector`` + a fresh ``ReportManager``. The image's labels imply a set of
   hazard types; an emitted event of a present type is a TP, of an absent type a FP, a
   present type with no event is a FN. Results are split by dataset source prefix.
3. **False positives on real hazard-free footage** - both ONNX models through one
   ``ReportManager`` over the clips in ``assets/videos``, with and without CameraSim,
   reported as events per minute. The target is zero.

``--sweep`` replays the cached per-frame detections of step 2 through a small grid of
``ReportConfig`` settings (min_confidence x min_hits) to pick an operating point.

Examples
    python tools/eval_events.py                                  # full val split, default model
    python tools/eval_events.py --limit 60                       # quick: 60 images per source
    python tools/eval_events.py --sweep --limit 40 --camera-sim
    python tools/eval_events.py --model models/hazard_yolo11n_320.int8.onnx --data F:/dev/datasets/wazeomatic_hazards_640
    python tools/eval_events.py --skip-videos --skip-boxes       # events only
    python tools/eval_events.py --ensemble-model models/hazard_yolo11n_416.onnx --zoom-crop --track-iou 0.3

Writes ``results.json`` and ``results.md`` to ``--out`` (default ``runs/eval/<timestamp>/``).
"""

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # allow running from the repo without installing

from wazeomatic.build import DetectorSpec, add_detector_args, build_detectors, build_hazard_detector  # noqa: E402
from wazeomatic.fusion import parse_class_thresholds  # noqa: E402
from wazeomatic.report_manager import ReportConfig, ReportManager  # noqa: E402
from wazeomatic.schema import DEFAULT_CLASS_TO_HAZARD, Detection  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = Path("F:/dev/datasets/wazeomatic_hazards")
SIZE_BUCKETS = (("small", 0.0, 24.0), ("medium", 24.0, 64.0), ("large", 64.0, float("inf")))
BUCKET_NAMES = tuple(b[0] for b in SIZE_BUCKETS) + ("all",)
# Known dataset source prefixes, longest first so "pothole_ivcnz" wins over "pothole".
KNOWN_SOURCES = ("pothole_ivcnz", "rdd2022_us", "cone_voc", "starline", "cone", "pothole")
SWEEP_MIN_CONF = (0.35, 0.45, 0.55)
SWEEP_MIN_HITS = (2, 3, 4)
GT_COMBO_NONE = "none"


# ---------------------------------------------------------------------------- small helpers
class StepClock:
    """Fake monotonic clock: ``tick()`` advances by a fixed step so cooldowns behave."""

    def __init__(self, start: float = 1000.0, step_s: float = 0.1):
        self.t = start
        self.step_s = step_s

    def __call__(self) -> float:
        return self.t

    def tick(self) -> None:
        self.t += self.step_s


def load_labels(model_path: Path) -> list[str]:
    cand = model_path.with_suffix(".labels.txt")
    if not cand.exists():
        raise SystemExit(f"no labels file for {model_path} (expected {cand})")
    return [ln.strip() for ln in cand.read_text(encoding="utf-8").splitlines() if ln.strip()]


def source_of(stem: str) -> str:
    """Dataset source prefix of a file stem (``pothole_ivcnz_img-1020`` -> ``pothole_ivcnz``)."""
    for p in KNOWN_SOURCES:
        if stem.startswith(p + "_") or stem == p:
            return p
    return stem.split("_", 1)[0]


def read_yolo_labels(path: Path) -> list[tuple[int, float, float, float, float]]:
    """YOLO txt -> list of (class_id, cx, cy, w, h), all normalized. Missing file = no boxes."""
    if not path.exists():
        return []
    rows = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        parts = ln.split()
        if len(parts) < 5:
            continue
        rows.append((int(float(parts[0])), *(float(v) for v in parts[1:5])))
    return rows


def letterbox_side_px(w_norm: float, h_norm: float, src_w: int, src_h: int, imgsz: int) -> float:
    """Longest box side in pixels after letterboxing the source image to ``imgsz``."""
    scale = min(imgsz / src_h, imgsz / src_w)
    return max(w_norm * src_w, h_norm * src_h) * scale


def size_bucket(side_px: float) -> str:
    for name, lo, hi in SIZE_BUCKETS:
        if lo <= side_px < hi:
            return name
    return SIZE_BUCKETS[-1][0]


def xywhn_to_xyxy(b: Sequence[float]) -> tuple[float, float, float, float]:
    cx, cy, w, h = b
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


def box_iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU matrix between ``a`` [N,4] and ``b`` [M,4], both xyxy."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float32)
    tl = np.maximum(a[:, None, :2], b[None, :, :2])
    br = np.minimum(a[:, None, 2:], b[None, :, 2:])
    inter = np.prod((br - tl).clip(0), axis=2)
    area_a = np.prod(a[:, 2:] - a[:, :2], axis=1)
    area_b = np.prod(b[:, 2:] - b[:, :2], axis=1)
    return inter / (area_a[:, None] + area_b[None, :] - inter + 1e-9)


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


def md_table(cols: Sequence[str], rows: Iterable[Sequence]) -> str:
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(_fmt(v) for v in r) + " |")
    return "\n".join(out)


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


# ---------------------------------------------------------------------------- 1. box metrics
@dataclass
class GTBox:
    cls: str
    xyxy: tuple[float, float, float, float]
    bucket: str


@dataclass
class PredBox:
    cls: str
    conf: float
    xyxy: tuple[float, float, float, float]
    bucket: str
    image: str


def average_precision(tp_flags: np.ndarray, confs: np.ndarray, n_gt: int) -> float:
    """All-point interpolated AP (VOC2010 style) from per-prediction TP flags."""
    if n_gt == 0 or len(tp_flags) == 0:
        return 0.0
    order = np.argsort(-confs, kind="stable")
    tp = tp_flags[order].astype(np.float64)
    fp = 1.0 - tp
    tp_c, fp_c = np.cumsum(tp), np.cumsum(fp)
    recall = tp_c / n_gt
    precision = tp_c / np.maximum(tp_c + fp_c, 1e-9)
    mrec = np.concatenate(([0.0], recall, [1.0]))
    mpre = np.concatenate(([0.0], precision, [0.0]))
    for i in range(len(mpre) - 2, -1, -1):  # precision envelope
        mpre[i] = max(mpre[i], mpre[i + 1])
    idx = np.where(mrec[1:] != mrec[:-1])[0]
    return float(np.sum((mrec[idx + 1] - mrec[idx]) * mpre[idx + 1]))


def match_predictions(gts: list[GTBox], preds: list[PredBox], iou_thres: float = 0.5
                      ) -> list[tuple[PredBox, GTBox | None]]:
    """Greedy per-class matching, highest confidence first. Returns (pred, matched_gt or None)."""
    out: list[tuple[PredBox, GTBox | None]] = []
    by_cls: dict[str, list[GTBox]] = defaultdict(list)
    for g in gts:
        by_cls[g.cls].append(g)
    used: set[int] = set()
    for p in sorted(preds, key=lambda p: -p.conf):
        cands = by_cls.get(p.cls, [])
        best, best_iou = None, iou_thres
        if cands:
            ious = box_iou(np.array([p.xyxy]), np.array([g.xyxy for g in cands]))[0]
            for j, g in enumerate(cands):
                if id(g) in used:
                    continue
                if ious[j] >= best_iou:
                    best, best_iou = g, ious[j]
        if best is not None:
            used.add(id(best))
        out.append((p, best))
    return out


def box_metrics(gt_by_image: dict[str, list[GTBox]], pred_by_image: dict[str, list[PredBox]],
                class_names: Sequence[str], iou_thres: float = 0.5) -> dict:
    """AP50 + recall per class x size bucket. For a bucket, GTs outside it are ignored:
    predictions matched to them are dropped, unmatched predictions outside the bucket too
    (same convention as COCO area ranges)."""
    matches: list[tuple[PredBox, GTBox | None]] = []
    for img, gts in gt_by_image.items():
        matches.extend(match_predictions(gts, pred_by_image.get(img, []), iou_thres))
    for img, preds in pred_by_image.items():  # images with predictions but no labels file entry
        if img not in gt_by_image:
            matches.extend((p, None) for p in preds)
    all_gts = [g for gts in gt_by_image.values() for g in gts]

    result: dict[str, dict[str, dict]] = {}
    for cls in list(class_names) + ["all"]:
        result[cls] = {}
        for bucket in BUCKET_NAMES:
            def in_scope(c: str, b: str) -> bool:
                return (cls == "all" or c == cls) and (bucket == "all" or b == bucket)

            n_gt = sum(1 for g in all_gts if in_scope(g.cls, g.bucket))
            flags, confs = [], []
            for p, g in matches:
                if cls != "all" and p.cls != cls:
                    continue
                if g is not None:
                    if bucket != "all" and g.bucket != bucket:
                        continue  # matched an out-of-bucket GT: ignore
                    flags.append(1.0)
                else:
                    if bucket != "all" and p.bucket != bucket:
                        continue
                    flags.append(0.0)
                confs.append(p.conf)
            flags_a, confs_a = np.array(flags), np.array(confs)
            tp = int(flags_a.sum()) if len(flags_a) else 0
            result[cls][bucket] = {
                "n_gt": n_gt,
                "n_pred": len(flags),
                "tp": tp,
                "fp": len(flags) - tp,
                "recall": round(tp / n_gt, 4) if n_gt else None,
                "precision": round(tp / len(flags), 4) if flags else None,
                "ap50": round(average_precision(flags_a, confs_a, n_gt), 4) if n_gt else None,
            }
    return result


def box_metrics_markdown(res: dict, conf: float) -> str:
    rows = []
    for cls, buckets in res.items():
        for b in BUCKET_NAMES:
            m = buckets[b]
            rows.append([cls, b, m["n_gt"], m["n_pred"], _opt(m["ap50"]), _opt(m["recall"]), _opt(m["precision"])])
    return (f"Boxes on val split (IoU >= 0.5, detector conf {conf}, size = longest side on the "
            f"letterboxed input: small < 24 px, medium 24-64 px, large > 64 px)\n\n"
            + md_table(["class", "size", "gt", "pred", "AP50", f"recall@{conf}", f"precision@{conf}"], rows))


def _opt(v) -> str:
    return "-" if v is None else f"{v:.3f}"


# ---------------------------------------------------------------------------- 2. synthetic events
def jitter_frame(img: np.ndarray, rng: np.random.Generator, translate: float = 0.02,
                 scale_range: tuple[float, float] = (0.97, 1.03), brightness: float = 0.08) -> np.ndarray:
    """One consecutive-frame look-alike: small random affine + brightness change."""
    import cv2

    h, w = img.shape[:2]
    s = rng.uniform(*scale_range)
    tx = rng.uniform(-translate, translate) * w
    ty = rng.uniform(-translate, translate) * h
    cx, cy = w / 2, h / 2
    m = np.array([[s, 0.0, (1 - s) * cx + tx], [0.0, s, (1 - s) * cy + ty]], dtype=np.float32)
    out = cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    gain = 1.0 + rng.uniform(-brightness, brightness)
    return cv2.convertScaleAbs(out, alpha=gain, beta=0)


def synth_sequence(img: np.ndarray, n_frames: int, rng: np.random.Generator, camera_sim=None) -> Iterable[np.ndarray]:
    for _ in range(n_frames):
        fr = jitter_frame(img, rng)
        if camera_sim is not None:
            fr = camera_sim(fr)
        yield fr


def make_camera_sim(src_w: int, src_h: int, seed: int):
    """CameraSim sized to the image's aspect ratio (fit inside 1280x720) so it degrades
    without squashing; CameraSim itself always resizes to a fixed width x height."""
    from wazeomatic.sources import CameraSim

    k = min(1280 / src_w, 720 / src_h, 1.0)
    return CameraSim(width=max(32, int(round(src_w * k))), height=max(32, int(round(src_h * k))), seed=seed)


def replay_events(frames_dets: Sequence[Sequence[Detection]], cfg: ReportConfig,
                  step_s: float = 0.1) -> list[str]:
    """Push cached per-frame detections through a fresh ReportManager; return emitted hazard types."""
    clock = StepClock(step_s=step_s)
    rm = ReportManager(cfg, source="sim", clock=clock)
    emitted: list[str] = []
    for i, dets in enumerate(frames_dets):
        emitted.extend(ev.hazard_type for ev in rm.update(dets, frame_index=i))
        clock.tick()
    return emitted


def hazard_types_for(labels: Iterable[str], class_to_hazard: dict[str, str]) -> set[str]:
    return {class_to_hazard[lb] for lb in labels if lb in class_to_hazard}


def gt_combo(types: set[str]) -> str:
    return "+".join(sorted(types)) if types else GT_COMBO_NONE


@dataclass
class EventCase:
    """One synthetic clip: what was present and what the ReportManager emitted."""

    image: str
    source: str
    present: set[str]
    emitted: set[str]


def summarize_events(cases: Sequence[EventCase], hazard_types: Sequence[str]) -> dict:
    """Per-type TP/FP/FN/TN + precision/recall/F1, overall (micro), and a GT-set x emitted-set confusion."""
    per_type: dict[str, dict] = {}
    tot = Counter()
    for ht in hazard_types:
        c = Counter()
        for cs in cases:
            present, emitted = ht in cs.present, ht in cs.emitted
            c["tp" if present and emitted else "fp" if emitted else "fn" if present else "tn"] += 1
        per_type[ht] = {**prf(c["tp"], c["fp"], c["fn"]), "tn": c["tn"]}
        tot.update(c)
    # events of types outside the scored set (e.g. a COCO-mapped hazard) are always false positives
    extra_fp = sum(len(cs.emitted - set(hazard_types)) for cs in cases)
    overall = {**prf(tot["tp"], tot["fp"] + extra_fp, tot["fn"]), "tn": tot["tn"], "images": len(cases),
               "images_with_any_event": sum(1 for cs in cases if cs.emitted),
               "exact_set_match": sum(1 for cs in cases if cs.present == cs.emitted)}
    confusion: dict[str, Counter] = defaultdict(Counter)
    for cs in cases:
        confusion[gt_combo(cs.present)][gt_combo(cs.emitted)] += 1
    return {"per_type": per_type, "overall": overall,
            "confusion": {k: dict(v) for k, v in sorted(confusion.items())}}


def events_markdown(summary: dict, by_source: dict[str, dict], title: str) -> str:
    rows = [[ht, m["tp"], m["fp"], m["fn"], m["tn"], m["precision"], m["recall"], m["f1"]]
            for ht, m in summary["per_type"].items()]
    o = summary["overall"]
    rows.append(["**overall**", o["tp"], o["fp"], o["fn"], o["tn"], o["precision"], o["recall"], o["f1"]])
    parts = [title, "", md_table(["hazard type", "TP", "FP", "FN", "TN", "precision", "recall", "F1"], rows),
             "", f"{o['images']} synthetic clips, {o['images_with_any_event']} produced at least one event, "
             f"{o['exact_set_match']} emitted exactly the ground-truth set."]
    # confusion: rows = GT hazard set, cols = emitted hazard set
    combos = sorted({k for k in summary["confusion"]} | {c for v in summary["confusion"].values() for c in v},
                    key=lambda s: (s != GT_COMBO_NONE, s))
    conf_rows = [[f"gt: {g}"] + [summary["confusion"].get(g, {}).get(e, 0) for e in combos]
                 for g in sorted(summary["confusion"], key=lambda s: (s != GT_COMBO_NONE, s))]
    parts += ["", "Confusion (rows = hazard set in the image, columns = hazard set emitted):", "",
              md_table(["gt \\ emitted"] + combos, conf_rows)]
    src_rows = []
    for src, s in by_source.items():
        for ht, m in s["per_type"].items():
            if m["tp"] + m["fp"] + m["fn"] == 0:
                continue  # hazard type neither present nor emitted in this source
            src_rows.append([src, ht, s["overall"]["images"], m["tp"], m["fp"], m["fn"], m["precision"], m["recall"], m["f1"]])
        oo = s["overall"]
        src_rows.append([src, "**overall**", oo["images"], oo["tp"], oo["fp"], oo["fn"], oo["precision"], oo["recall"], oo["f1"]])
    parts += ["", "By dataset source:", "",
              md_table(["source", "hazard type", "clips", "TP", "FP", "FN", "precision", "recall", "F1"], src_rows)]
    return "\n".join(parts)


def summarize_by_source(cases: Sequence[EventCase], hazard_types: Sequence[str]) -> dict[str, dict]:
    srcs = sorted({c.source for c in cases})
    return {s: summarize_events([c for c in cases if c.source == s], hazard_types) for s in srcs}


# ---------------------------------------------------------------------------- 3. video false positives
def video_false_positives(video: Path, detectors: Sequence, cfg: ReportConfig, step: int,
                          camera_sim: bool, max_frames: int | None, seed: int, auto_hood: bool = True) -> dict:
    from wazeomatic.hood import HoodEstimator
    from wazeomatic.sources import CameraSim, VideoSource

    sim = CameraSim(seed=seed) if camera_sim else None
    src = VideoSource(video, step=step, max_frames=max_frames, sim=sim)
    ts_holder = {"t": 0.0}
    rm = ReportManager(cfg, source="sim", clock=lambda: ts_holder["t"])
    hood = HoodEstimator(min_frac=cfg.hood_frac) if auto_hood else None
    events: Counter = Counter()
    det_labels: Counter = Counter()
    details: list[dict] = []
    frames = 0
    last_ts = 0.0
    for fr in src:
        ts_holder["t"] = fr.ts
        last_ts = fr.ts
        dets: list[Detection] = []
        for d in detectors:
            dets.extend(d(fr.image))
        det_labels.update(d.label for d in dets if d.label in cfg.class_to_hazard)
        if hood is not None:
            rm.set_hood_frac(hood.update(fr.image))
        for ev in rm.update(dets, frame_index=fr.index):
            events[ev.hazard_type] += 1
            if len(details) < 50:  # where and when, so a false event can be looked at in the clip
                details.append({"hazard_type": ev.hazard_type, "label": ev.label, "confidence": round(ev.confidence, 3),
                                "frame": fr.index, "t_s": round(fr.ts, 1),
                                "bbox": [round(v, 4) for v in ev.bbox] if ev.bbox else None})
        frames += 1
    src.release()
    minutes = max(last_ts, 1e-6) / 60.0
    return {
        "video": video.name,
        "camera_sim": camera_sim,
        "frames": frames,
        "duration_s": round(last_ts, 1),
        "events": dict(events),
        "events_total": sum(events.values()),
        "events_per_min": {k: round(v / minutes, 3) for k, v in events.items()},
        "events_per_min_total": round(sum(events.values()) / minutes, 3),
        "mapped_detections_by_label": dict(det_labels),
        "event_details": details,
        "hood_frac_final": round(rm.hood_frac, 3),
    }


def video_markdown(results: Sequence[dict]) -> str:
    types = sorted({t for r in results for t in r["events"]})
    cols = ["clip", "camera-sim", "frames", "seconds", "events", "events/min"] + [t for t in types]
    rows = [[r["video"], "yes" if r["camera_sim"] else "no", r["frames"], r["duration_s"], r["events_total"],
             r["events_per_min_total"]] + [r["events_per_min"].get(t, 0.0) for t in types] for r in results]
    note = "\n".join(f"- {r['video']} (sim={r['camera_sim']}): detections above detector conf that map to a "
                     f"hazard: {r['mapped_detections_by_label'] or 'none'}" for r in results)
    return "False positives on hazard-free clips (hazard + COCO model, one ReportManager, events per minute; target 0)\n\n" \
        + md_table(cols, rows) + "\n\n" + note


# ---------------------------------------------------------------------------- 4. sweep
def sweep_markdown(rows: Sequence[dict]) -> str:
    table = [[r["min_confidence"], r["min_hits"], r["tp"], r["fp"], r["fn"], r["precision"], r["recall"], r["f1"]]
             for r in rows]
    return "Threshold sweep on the synthetic clips (overall, micro-averaged over hazard types)\n\n" + \
        md_table(["min_confidence", "min_hits", "TP", "FP", "FN", "precision", "recall", "F1"], table)


# ---------------------------------------------------------------------------- dataset
@dataclass
class ValImage:
    path: Path
    stem: str
    source: str
    boxes: list[tuple[int, float, float, float, float]] = field(default_factory=list)


def list_val_images(data: Path, split: str, limit: int | None, seed: int) -> tuple[list[ValImage], dict]:
    img_dir, lbl_dir = data / "images" / split, data / "labels" / split
    if not img_dir.is_dir():
        raise SystemExit(f"no images at {img_dir}")
    images = []
    for p in sorted(img_dir.iterdir()):
        if p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp"}:
            continue
        images.append(ValImage(p, p.stem, source_of(p.stem), read_yolo_labels(lbl_dir / (p.stem + ".txt"))))
    counts = {"total": len(images), "per_source": dict(Counter(i.source for i in images)),
              "negatives": sum(1 for i in images if not i.boxes)}
    if limit:
        rng = np.random.default_rng(seed)
        keep: list[ValImage] = []
        for src in sorted({i.source for i in images}):
            pos = [i for i in images if i.source == src and i.boxes]
            neg = [i for i in images if i.source == src and not i.boxes]
            for group in (pos, neg):
                if len(group) > limit:
                    idx = sorted(rng.choice(len(group), size=limit, replace=False))
                    group = [group[k] for k in idx]
                keep.extend(group)
        images = sorted(keep, key=lambda i: i.stem)
    counts["used"] = len(images)
    counts["used_per_source"] = dict(Counter(i.source for i in images))
    return images, counts


# ---------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", dest="hazard_model_alias", help="alias of --hazard-model")
    add_detector_args(ap)
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="YOLO dataset root with images/<split>, labels/<split>")
    ap.add_argument("--split", default="val")
    ap.add_argument("--classes", default=str(ROOT / "configs" / "classes.yaml"))
    ap.set_defaults(conf=0.25)  # metrics want the low detector threshold; the pipeline default is 0.35
    ap.add_argument("--frames", type=int, default=12, help="synthetic frames per image")
    ap.add_argument("--limit", type=int, help="cap images per source (positives and negatives separately)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--camera-sim", action="store_true", help="degrade synthetic frames like a phone camera")
    ap.add_argument("--sweep", action="store_true", help="grid over ReportConfig min_confidence x min_hits")
    ap.add_argument("--videos", default=str(ROOT / "assets" / "videos"), help="folder of hazard-free clips")
    ap.add_argument("--video-step", type=int, default=3)
    ap.add_argument("--video-max-frames", type=int, help="cap inference frames per clip (debugging)")
    ap.add_argument("--skip-boxes", action="store_true")
    ap.add_argument("--skip-events", action="store_true")
    ap.add_argument("--skip-videos", action="store_true")
    ap.add_argument("--out", help="output folder (default runs/eval/<timestamp>)")
    # ReportConfig for the non-sweep runs
    ap.add_argument("--min-hits", type=int, default=3)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--report-conf", type=float, default=0.40)
    ap.add_argument("--cooldown", type=float, default=60.0)
    ap.add_argument("--global-cooldown", type=float, default=5.0)
    ap.add_argument("--report-class-conf", help="per-label hit floors for the report manager, e.g. cone=0.55")
    ap.add_argument("--track-iou", type=float, default=0.0, help="> 0: spatial tracking in the report manager")
    ap.add_argument("--track-grow", type=float, default=1.5)
    ap.add_argument("--no-auto-hood", action="store_true",
                    help="videos: keep the fixed hood zone instead of learning the static bottom rows")
    return ap


def load_class_to_hazard(classes_yaml: Path) -> dict[str, str]:
    import yaml

    m = dict(DEFAULT_CLASS_TO_HAZARD)
    if classes_yaml.exists():
        cfg = yaml.safe_load(classes_yaml.read_text(encoding="utf-8")) or {}
        m.update(cfg.get("hazard_map", {}))
    return m


def main(argv=None) -> int:
    import cv2

    from wazeomatic.detector import OnnxDetector

    args = build_parser().parse_args(argv)
    if args.hazard_model_alias:
        args.hazard_model = args.hazard_model_alias
    t_start = time.perf_counter()
    out_dir = Path(args.out) if args.out else ROOT / "runs" / "eval" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    class_to_hazard = load_class_to_hazard(Path(args.classes))
    base_cfg = dict(window=args.window, min_hits=args.min_hits, min_confidence=args.report_conf,
                    min_confidence_by_label=parse_class_thresholds(args.report_class_conf),
                    track_iou=args.track_iou, track_grow=args.track_grow,
                    cooldown_s=args.cooldown, global_cooldown_s=args.global_cooldown, class_to_hazard=class_to_hazard)
    spec = DetectorSpec.from_args(args)
    spec.coco_model = None  # the hazard stack alone for boxes/events; videos add COCO below
    model_path = Path(args.hazard_model)
    detector = build_hazard_detector(spec)
    if detector is None:
        raise SystemExit(f"hazard model not found: {model_path}")
    labels = list(detector.labels)
    # ground-truth class ids follow the dataset, which may have more classes than an older model
    gt_labels = labels
    data_yaml = Path(args.data) / "data.yaml"
    if data_yaml.exists():
        names = (yaml.safe_load(data_yaml.read_text(encoding="utf-8")) or {}).get("names") or {}
        if names:
            gt_labels = [names[i] for i in sorted(names)] if isinstance(names, dict) else list(names)
    imgsz = detector.size
    print(f"[info] detector stack: {spec.describe()}", file=sys.stderr)
    hazard_types = sorted({class_to_hazard[lb] for lb in labels if lb in class_to_hazard})
    data = Path(args.data)
    images, counts = list_val_images(data, args.split, args.limit, args.seed)

    results: dict = {
        "model": str(model_path), "stack": spec.describe(), "imgsz": imgsz, "labels": labels, "data": str(data),
        "split": args.split, "detector_conf": args.conf, "detector_iou": args.iou, "threads": args.threads,
        "report_config": {k: v for k, v in base_cfg.items() if k != "class_to_hazard"},
        "hazard_types": hazard_types, "dataset": counts, "frames_per_clip": args.frames,
        "camera_sim": args.camera_sim, "seed": args.seed, "timings_s": {},
    }
    md: list[str] = [f"# Event-level evaluation: {model_path.name} (input {imgsz}) on {data.name}/{args.split}", "",
                     f"Detector stack: {spec.describe()}", "",
                     f"{counts['used']} of {counts['total']} images used ({counts['used_per_source']}); "
                     f"detector conf {args.conf}, ReportConfig {results['report_config']}", ""]
    print(md[0], file=sys.stderr)
    print(f"[info] {counts['used']} images, per source {counts['used_per_source']}", file=sys.stderr)

    # ---- 1. boxes + 2. events share one pass over the images -------------------------------
    if not (args.skip_boxes and args.skip_events):
        t0 = time.perf_counter()
        gt_by_image: dict[str, list[GTBox]] = {}
        pred_by_image: dict[str, list[PredBox]] = {}
        cached: list[tuple[ValImage, list[list[Detection]]]] = []
        infer_ms: list[float] = []
        for n, vi in enumerate(images):
            img = cv2.imread(str(vi.path))
            if img is None:
                print(f"[warn] cannot read {vi.path}", file=sys.stderr)
                continue
            h, w = img.shape[:2]
            gt_by_image[vi.stem] = [
                GTBox(gt_labels[c] if c < len(gt_labels) else str(c), xywhn_to_xyxy(b),
                      size_bucket(letterbox_side_px(b[2], b[3], w, h, imgsz)))
                for c, *b in vi.boxes
            ]
            if not args.skip_boxes:
                dets = detector(img)
                infer_ms.append(detector.last_ms)
                pred_by_image[vi.stem] = [
                    PredBox(d.label, d.confidence, xywhn_to_xyxy(d.bbox),
                            size_bucket(letterbox_side_px(d.bbox[2], d.bbox[3], w, h, imgsz)), vi.stem)
                    for d in dets
                ]
            if not args.skip_events:
                rng = np.random.default_rng([args.seed, n])
                sim = make_camera_sim(w, h, args.seed + n) if args.camera_sim else None
                frames_dets = [detector(fr) for fr in synth_sequence(img, args.frames, rng, sim)]
                cached.append((vi, frames_dets))
            if (n + 1) % 100 == 0:
                print(f"[info] {n + 1}/{len(images)} images, {time.perf_counter() - t0:.0f}s", file=sys.stderr)
        results["timings_s"]["images_pass"] = round(time.perf_counter() - t0, 1)
        if infer_ms:
            results["infer_ms_mean"] = round(float(np.mean(infer_ms)), 2)

        if not args.skip_boxes:
            bm = box_metrics(gt_by_image, pred_by_image, list(dict.fromkeys(gt_labels + labels)))
            results["boxes"] = bm
            md += ["## 1. Box metrics by size", "", box_metrics_markdown(bm, args.conf), ""]

        if not args.skip_events:
            cfg = ReportConfig(**base_cfg)
            cases = [EventCase(vi.stem, vi.source, hazard_types_for((gt_labels[c] for c, *_ in vi.boxes), class_to_hazard),
                               set(replay_events(fd, cfg))) for vi, fd in cached]
            summary = summarize_events(cases, hazard_types)
            by_source = summarize_by_source(cases, hazard_types)
            results["events"] = {"overall": summary, "by_source": by_source,
                                 "cases": [{"image": c.image, "source": c.source, "present": sorted(c.present),
                                            "emitted": sorted(c.emitted)} for c in cases]}
            md += ["## 2. Event metrics on synthetic clips", "",
                   events_markdown(summary, by_source,
                                   f"{args.frames} jittered frames per image, 0.1 s per frame"
                                   f"{', CameraSim on' if args.camera_sim else ''}; "
                                   f"min_hits {args.min_hits}/{args.window}, min_confidence {args.report_conf}"), ""]

            if args.sweep:
                t0 = time.perf_counter()
                rows = []
                for mc in SWEEP_MIN_CONF:
                    for mh in SWEEP_MIN_HITS:
                        scfg = ReportConfig(**{**base_cfg, "min_confidence": mc, "min_hits": mh})
                        scases = [EventCase(c.image, c.source, c.present, set(replay_events(fd, scfg)))
                                  for c, (vi, fd) in zip(cases, cached)]
                        s = summarize_events(scases, hazard_types)
                        per_src = {src: v["overall"]["f1"] for src, v in summarize_by_source(scases, hazard_types).items()}
                        rows.append({"min_confidence": mc, "min_hits": mh, **s["overall"],
                                     "per_type": s["per_type"], "f1_by_source": per_src})
                results["sweep"] = rows
                results["timings_s"]["sweep"] = round(time.perf_counter() - t0, 1)
                md += ["## 4. Threshold sweep", "", sweep_markdown(rows), "",
                       "F1 by source per setting:", "",
                       md_table(["min_confidence", "min_hits"] + sorted(rows[0]["f1_by_source"]),
                                [[r["min_confidence"], r["min_hits"]] + [r["f1_by_source"][s] for s in sorted(r["f1_by_source"])]
                                 for r in rows]), ""]

    # ---- 3. hazard-free clips ----------------------------------------------------------------
    if not args.skip_videos:
        t0 = time.perf_counter()
        detectors = [detector]
        coco = Path(args.coco_model)
        if coco.exists() and not args.no_coco:
            detectors.append(OnnxDetector(coco, load_labels(coco), "coco", args.conf, args.iou, threads=args.threads))
        else:
            print(f"[warn] COCO model not found at {coco}; videos use the hazard model only", file=sys.stderr)
        clips = sorted(Path(args.videos).glob("*.mp4"))
        vres = []
        for clip in clips:
            for sim in (False, True):
                print(f"[info] video {clip.name} camera_sim={sim}", file=sys.stderr)
                vres.append(video_false_positives(clip, detectors, ReportConfig(**base_cfg), args.video_step,
                                                  sim, args.video_max_frames, args.seed, not args.no_auto_hood))
        results["videos"] = vres
        results["timings_s"]["videos"] = round(time.perf_counter() - t0, 1)
        if vres:
            md += ["## 3. False positives on hazard-free clips", "", video_markdown(vres), ""]

    results["timings_s"]["total"] = round(time.perf_counter() - t_start, 1)
    md += [f"Timings (s): {results['timings_s']}"]
    text = "\n".join(md)
    print(text)
    (out_dir / "results.md").write_text(text, encoding="utf-8")
    (out_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n[info] wrote {out_dir / 'results.json'} and results.md", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
