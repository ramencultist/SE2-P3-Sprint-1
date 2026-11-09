"""
Module Name: detector.py
Description: ONNX Runtime wrapper for YOLO detection models: resizes and normalizes frames, runs inference and decodes the boxes

Class Name: Letterbox
Description: Scale and padding used to fit a frame into the model input, kept so boxes can be mapped back

Class Name: OnnxDetector
Description: Loads one ONNX model and turns a frame into a list of detections

Class Name: ZoomDetector
Description: Runs a second pass on a zoomed crop of the road ahead and merges it with the full-frame pass

Class Name: EnsembleDetector
Description: Runs several detectors on the same frame and fuses their boxes

Class Name: ClassFilter
Description: Drops detections below a per-class confidence threshold

Inputs: BGR frames from the frame sources and an ONNX model file with its labels
Outputs: Detections (label, confidence, normalized box) for the report manager
External Sources: Claude Fable 5.1, Ultralytics YOLO ONNX export format
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the letterbox preprocessing, NMS and YOLO output decoding around ONNX Runtime
How you validated and revised the AI output: Proofreading, and the pytest unit tests in tests/ run on the desktop (tests/test_detector.py checks letterbox geometry, tensor layout, NMS and box mapping)
The challenges or limitations you faced while using AI: Keeping the pre and post-processing identical to the Kotlin port in the Android app
Authors: Blake Pennel
Creation Date: 11/5/2026
"""
from __future__ import annotations

"""ONNX Runtime wrapper for Ultralytics-style YOLO detection models.

Works with any model exported by ``tools/export_onnx.py`` (output shape
``[1, 4 + num_classes, num_anchors]``, boxes in letterboxed pixel xywh). The same
pre/post-processing is mirrored in Kotlin in the Android app, so keep them in sync.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from .schema import Detection

try:  # onnxruntime is optional at import time so tests can run without a model
    import onnxruntime as ort
except ImportError:  # pragma: no cover
    ort = None


@dataclass
class Letterbox:
    scale: float
    pad_x: float
    pad_y: float
    in_w: int
    in_h: int
    src_w: int
    src_h: int


def letterbox(img: np.ndarray, size: int, fill: int = 114) -> tuple[np.ndarray, Letterbox]:
    """Resize keeping aspect ratio and pad to ``size`` x ``size`` (BGR uint8 in/out)."""
    h, w = img.shape[:2]
    scale = min(size / h, size / w)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    import cv2

    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    out = np.full((size, size, 3), fill, dtype=np.uint8)
    px, py = (size - nw) // 2, (size - nh) // 2
    out[py : py + nh, px : px + nw] = resized
    return out, Letterbox(scale, px, py, size, size, w, h)


def to_input_tensor(img_bgr: np.ndarray) -> np.ndarray:
    """HWC BGR uint8 -> NCHW RGB float32 in [0, 1]."""
    x = img_bgr[:, :, ::-1].astype(np.float32) / 255.0
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None])


def nms(boxes_xyxy: np.ndarray, scores: np.ndarray, iou_thres: float) -> list[int]:
    """Plain greedy NMS. boxes: [N,4] xyxy, scores: [N]. Returns kept indices."""
    if len(boxes_xyxy) == 0:
        return []
    x1, y1, x2, y2 = boxes_xyxy.T
    areas = (x2 - x1).clip(0) * (y2 - y1).clip(0)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = (xx2 - xx1).clip(0) * (yy2 - yy1).clip(0)
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_thres]
    return keep


def decode_yolo_output(
    raw: np.ndarray,
    lb: Letterbox,
    labels: Sequence[str],
    conf_thres: float,
    iou_thres: float,
    model_name: str = "hazard",
    class_agnostic: bool = False,
) -> list[Detection]:
    """Turn a ``[1, 4+nc, N]`` (or ``[1, N, 4+nc]``) tensor into Detections.

    Returned bboxes are normalized center-xywh in the *source* image frame.
    """
    out = raw[0]
    if out.shape[0] == 4 + len(labels):  # channel-first export [4+nc, N] -> [N, 4+nc]
        out = out.T  # -> [N, 4+nc]
    boxes = out[:, :4]
    cls_scores = out[:, 4:]
    cls_id = cls_scores.argmax(1)
    conf = cls_scores[np.arange(len(cls_id)), cls_id]
    m = conf >= conf_thres
    boxes, cls_id, conf = boxes[m], cls_id[m], conf[m]
    if len(boxes) == 0:
        return []
    # xywh (letterboxed px) -> xyxy
    xyxy = np.empty_like(boxes)
    xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
    xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
    # per-class NMS via coordinate offset trick
    offs = np.zeros_like(cls_id, dtype=np.float32) if class_agnostic else cls_id.astype(np.float32) * 4096.0
    keep = nms(xyxy + offs[:, None], conf, iou_thres)
    dets: list[Detection] = []
    for i in keep:
        bx1 = (xyxy[i, 0] - lb.pad_x) / lb.scale
        by1 = (xyxy[i, 1] - lb.pad_y) / lb.scale
        bx2 = (xyxy[i, 2] - lb.pad_x) / lb.scale
        by2 = (xyxy[i, 3] - lb.pad_y) / lb.scale
        bx1, bx2 = np.clip([bx1, bx2], 0, lb.src_w)
        by1, by2 = np.clip([by1, by2], 0, lb.src_h)
        w, h = bx2 - bx1, by2 - by1
        if w <= 1 or h <= 1:
            continue
        dets.append(
            Detection(
                label=labels[int(cls_id[i])],
                confidence=float(conf[i]),
                bbox=(
                    float((bx1 + w / 2) / lb.src_w),
                    float((by1 + h / 2) / lb.src_h),
                    float(w / lb.src_w),
                    float(h / lb.src_h),
                ),
                model=model_name,
            )
        )
    return dets


class OnnxDetector:
    """Loads a YOLO ONNX file and runs it on BGR frames."""

    def __init__(
        self,
        model_path: str | Path,
        labels: Sequence[str],
        name: str = "hazard",
        conf_thres: float = 0.35,
        iou_thres: float = 0.5,
        providers: Sequence[str] | None = None,
        threads: int | None = None,
    ):
        if ort is None:
            raise RuntimeError("onnxruntime is not installed")
        so = ort.SessionOptions()
        if threads:
            so.intra_op_num_threads = threads
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(model_path), sess_options=so, providers=list(providers or ["CPUExecutionProvider"])
        )
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        shape = inp.shape
        self.size = int(shape[2]) if isinstance(shape[2], int) else 640
        self.labels = list(labels)
        self.name = name
        self.conf_thres = conf_thres
        self.iou_thres = iou_thres
        self.last_ms = 0.0
        nc = self.session.get_outputs()[0].shape[1]
        if isinstance(nc, int) and nc - 4 != len(self.labels):
            raise ValueError(f"{model_path}: model has {nc - 4} classes but {len(self.labels)} labels given")

    def __call__(self, frame_bgr: np.ndarray) -> list[Detection]:
        padded, lb = letterbox(frame_bgr, self.size)
        x = to_input_tensor(padded)
        t0 = time.perf_counter()
        raw = self.session.run(None, {self.input_name: x})[0]
        self.last_ms = (time.perf_counter() - t0) * 1000.0
        return decode_yolo_output(raw, lb, self.labels, self.conf_thres, self.iou_thres, self.name)


class ZoomDetector:
    """Two-pass wrapper for small, distant hazards: runs the detector on the whole frame and a
    second time on a zoomed crop of the road ahead, then merges both with per-class NMS.

    With a 1280x720 frame and a 640 px model the full pass sees the scene at half resolution;
    the default crop (middle 50 % of the width, 35-75 % of the height) is 640x288 px, so the
    second pass sees that region at full resolution: a 20 px cone becomes 40 px to the network.
    Costs one extra inference per frame. The crop stays above the hood zone on purpose.
    """

    def __init__(self, base: "OnnxDetector", crop: tuple[float, float, float, float] = (0.25, 0.35, 0.75, 0.75),
                 iou_thres: float = 0.5):
        self.base = base
        self.crop = crop
        self.iou_thres = iou_thres
        self.labels = base.labels
        self.name = base.name
        self.size = base.size
        self.conf_thres = base.conf_thres
        self.last_ms = 0.0

    def __call__(self, frame_bgr: np.ndarray) -> list[Detection]:
        h, w = frame_bgr.shape[:2]
        full = self.base(frame_bgr)
        ms = self.base.last_ms
        cx1, cy1, cx2, cy2 = self.crop
        x1, y1 = int(round(cx1 * w)), int(round(cy1 * h))
        x2, y2 = int(round(cx2 * w)), int(round(cy2 * h))
        if x2 - x1 < 32 or y2 - y1 < 32:
            self.last_ms = ms
            return full
        sub = frame_bgr[y1:y2, x1:x2]
        zoomed = self.base(sub)
        ms += self.base.last_ms
        self.last_ms = ms
        sw, sh = (x2 - x1) / w, (y2 - y1) / h
        mapped = [
            Detection(
                label=d.label,
                confidence=d.confidence,
                bbox=(cx1 + d.bbox[0] * sw, cy1 + d.bbox[1] * sh, d.bbox[2] * sw, d.bbox[3] * sh),
                model=d.model,
            )
            for d in zoomed
        ]
        return merge_detections(full + mapped, self.iou_thres)


def merge_detections(dets: list[Detection], iou_thres: float = 0.5) -> list[Detection]:
    """Per-class NMS over detections from several passes (normalized xywh boxes)."""
    if len(dets) <= 1:
        return list(dets)
    labels = sorted({d.label for d in dets})
    xyxy = np.array([[d.bbox[0] - d.bbox[2] / 2, d.bbox[1] - d.bbox[3] / 2,
                      d.bbox[0] + d.bbox[2] / 2, d.bbox[1] + d.bbox[3] / 2] for d in dets], dtype=np.float32)
    conf = np.array([d.confidence for d in dets], dtype=np.float32)
    offs = np.array([labels.index(d.label) * 4.0 for d in dets], dtype=np.float32)
    keep = nms(xyxy + offs[:, None], conf, iou_thres)
    return [dets[i] for i in sorted(keep, key=lambda i: -conf[i])]


class EnsembleDetector:
    """Runs several detectors on the same frame (in parallel threads; ONNX Runtime releases the
    GIL) and fuses their boxes, by default with Weighted Boxes Fusion (see ``fusion.py``).

    Typical members: the 640 px hazard model plus the 416 px one, or a model plus its
    ``ZoomDetector`` wrapper. Members may have different label sets; fusion is per label, so a
    label only one member knows simply gets that member's votes against the full weight sum.
    ``class_conf`` applies a per-label confidence floor after fusion (``{"pothole": 0.5}``).
    """

    def __init__(self, members: Sequence, weights: Sequence[float] | None = None, fusion: str = "wbf",
                 iou_thres: float = 0.55, conf_type: str = "avg", parallel: bool = True,
                 class_conf: dict[str, float] | None = None, name: str = "ensemble"):
        if not members:
            raise ValueError("EnsembleDetector needs at least one member")
        self.members = list(members)
        self.weights = list(weights) if weights is not None else [1.0] * len(self.members)
        if len(self.weights) != len(self.members):
            raise ValueError("one weight per member expected")
        if fusion not in ("wbf", "nms"):
            raise ValueError("fusion must be 'wbf' or 'nms'")
        self.fusion = fusion
        self.iou_thres = iou_thres
        self.conf_type = conf_type
        self.parallel = parallel and len(self.members) > 1
        self.class_conf = dict(class_conf or {})
        self.name = name
        seen: list[str] = []
        for m in self.members:
            for lb in m.labels:
                if lb not in seen:
                    seen.append(lb)
        self.labels = seen
        self.size = max(m.size for m in self.members)
        self.conf_thres = min(m.conf_thres for m in self.members)
        self.last_ms = 0.0          # summed member inference time (compute cost)
        self.last_wall_ms = 0.0     # wall time of the parallel run (latency)
        self._pool = None
        if self.parallel:
            from concurrent.futures import ThreadPoolExecutor

            self._pool = ThreadPoolExecutor(max_workers=len(self.members), thread_name_prefix="ensemble")

    def __call__(self, frame_bgr: np.ndarray) -> list[Detection]:
        from .fusion import apply_class_thresholds, fuse_nms, fuse_wbf

        t0 = time.perf_counter()
        if self._pool is not None:
            results = list(self._pool.map(lambda m: m(frame_bgr), self.members))
        else:
            results = [m(frame_bgr) for m in self.members]
        self.last_ms = float(sum(m.last_ms for m in self.members))
        self.last_wall_ms = (time.perf_counter() - t0) * 1000.0
        if self.fusion == "nms":
            fused = fuse_nms(results, self.iou_thres)
        else:
            fused = fuse_wbf(results, self.weights, self.iou_thres, conf_type=self.conf_type, model_name=self.name)
        return apply_class_thresholds(fused, self.class_conf)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None


class ClassFilter:
    """Wraps any detector and applies per-label confidence floors to its output."""

    def __init__(self, base, class_conf: dict[str, float], default: float = 0.0):
        self.base = base
        self.class_conf = dict(class_conf)
        self.default = default
        self.labels = base.labels
        self.name = base.name
        self.size = base.size
        self.conf_thres = base.conf_thres

    @property
    def last_ms(self) -> float:
        return self.base.last_ms

    def __call__(self, frame_bgr: np.ndarray) -> list[Detection]:
        from .fusion import apply_class_thresholds

        return apply_class_thresholds(self.base(frame_bgr), self.class_conf, self.default)
