"""
Module Name: build.py
Description: Builds the detector stack (hazard model, zoom pass, ensemble, COCO model) from command-line options, shared by run_video.py and tools/eval_events.py

Class Name: DetectorSpec
Description: Which models, thresholds, zoom crop and fusion settings to build

Inputs: Command-line options and ONNX model files in models/
Outputs: A list of detectors for the pipeline; models that fail to load are logged in LOAD_ERRORS and skipped
External Sources: Claude Fable 5.1, Claude Opus 5.5
Attributions:

Creation 11/6/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to factor the detector construction out of run_video.py so evaluation and the live pipeline run the same stack
How you validated and revised the AI output: Proofreading, tests/test_ensemble_tracking.py and runs of run_video.py and eval_events.py with the same flags
The challenges or limitations you faced while using AI: Keeping the many flag combinations (ensemble, zoom, COCO on or off) consistent between the two entry points
Authors: Blake Pennel, Luke Reicherter
Creation Date: 11/6/2026

Ported to the SE2-P3 repository 11/17/2026 by Luke Reicherter with Claude Opus 5.5 for Sprint 2 (requirements 7 and 9); code unchanged from the team's wazeomatic development repository unless a Revision entry says otherwise

Revision 11/17/2026 - Luke Reicherter and Claude Opus 5.5 (Sprint 2):
A description of how and why AI was used: Claude Opus 5.5 used to log and skip a model that is missing, corrupt or has no labels file instead of crashing (criterion 7.7)
How you validated and revised the AI output: tests/test_sprint2_requirements.py::test_7_7 with a missing file, a corrupt file and a missing labels file, and a run_video.py run without the hazard model
The challenges or limitations you faced while using AI: labels_for raises SystemExit, which a plain except Exception does not catch
"""
from __future__ import annotations
"""Build the detector stack from command-line style options.

Shared by ``run_video.py`` (live pipeline) and ``tools/eval_events.py`` (evaluation) so both
run exactly the same models, fusion and thresholds::

    hazard models  -> [OnnxDetector] -> (ZoomDetector each) -> EnsembleDetector (WBF) -> ClassFilter
    coco model     -> OnnxDetector   -> (ZoomDetector)

A single hazard model without zoom stays a plain ``OnnxDetector`` (no fusion overhead).
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .detector import ClassFilter, EnsembleDetector, OnnxDetector, ZoomDetector
from .fusion import parse_class_thresholds

ROOT = Path(__file__).resolve().parents[1]


def labels_for(model_path: Path, explicit: str | None = None) -> list[str]:
    if explicit:
        return [ln.strip() for ln in Path(explicit).read_text(encoding="utf-8").splitlines() if ln.strip()]
    cand = model_path.with_suffix(".labels.txt")
    if cand.exists():
        return [ln.strip() for ln in cand.read_text(encoding="utf-8").splitlines() if ln.strip()]
    raise SystemExit(f"no labels file for {model_path} (expected {cand})")


def parse_crop(spec: str | None) -> tuple[float, float, float, float] | None:
    if not spec:
        return None
    crop = tuple(float(v) for v in spec.split(","))
    if len(crop) != 4:
        raise SystemExit("--zoom-crop wants x1,y1,x2,y2 as fractions, e.g. 0.25,0.35,0.75,0.75")
    return crop  # type: ignore[return-value]


@dataclass
class DetectorSpec:
    hazard_models: list[Path] = field(default_factory=list)   # one or more; >1 = ensemble
    hazard_labels: str | None = None
    coco_model: Path | None = None
    coco_labels: str | None = None
    conf: float = 0.35
    iou: float = 0.5
    threads: int = 4
    zoom_crop: tuple[float, float, float, float] | None = None
    zoom_coco: bool = False          # also zoom the COCO model (costly, rarely useful)
    fusion: str = "wbf"              # wbf | nms
    weights: list[float] | None = None
    conf_type: str = "avg"
    fusion_iou: float = 0.55
    class_conf: dict[str, float] = field(default_factory=dict)
    parallel: bool = True

    @classmethod
    def from_args(cls, args) -> "DetectorSpec":
        """Read the shared CLI flags added by ``add_detector_args``."""
        hz: list[Path] = []
        if not getattr(args, "no_hazard", False):
            hz.append(Path(args.hazard_model))
            hz += [Path(p) for p in (getattr(args, "ensemble_model", None) or [])]
        weights = None
        if getattr(args, "ensemble_weights", None):
            weights = [float(v) for v in args.ensemble_weights.split(",")]
        return cls(
            hazard_models=hz,
            hazard_labels=getattr(args, "hazard_labels", None),
            coco_model=None if getattr(args, "no_coco", False) else Path(args.coco_model),
            coco_labels=getattr(args, "coco_labels", None),
            conf=args.conf, iou=args.iou, threads=args.threads,
            zoom_crop=None if getattr(args, "no_zoom", False) else parse_crop(getattr(args, "zoom_crop", None)),
            zoom_coco=bool(getattr(args, "zoom_coco", False)),
            fusion=getattr(args, "fusion", "wbf"), weights=weights,
            conf_type=getattr(args, "conf_type", "avg"), fusion_iou=getattr(args, "fusion_iou", 0.55),
            class_conf=parse_class_thresholds(getattr(args, "class_conf", None)),
            parallel=not getattr(args, "no_parallel", False),
        )

    def describe(self) -> str:
        parts = [f"hazard={[p.name for p in self.hazard_models]}"]
        if len(self.hazard_models) > 1:
            parts.append(f"fusion={self.fusion}/{self.conf_type} weights={self.weights or 'equal'}")
        if self.zoom_crop:
            parts.append(f"zoom={self.zoom_crop}")
        if self.class_conf:
            parts.append(f"class_conf={self.class_conf}")
        if self.coco_model:
            parts.append(f"coco={self.coco_model.name}")
        return " ".join(parts)


def add_detector_args(ap, default_hazard: Path | None = None, default_coco: Path | None = None) -> None:
    """Attach the shared detector flags to an argparse parser."""
    ap.add_argument("--hazard-model", default=str(default_hazard or ROOT / "models" / "hazard_yolo11n.onnx"))
    ap.add_argument("--hazard-labels")
    ap.add_argument("--ensemble-model", action="append", default=[],
                    help="extra hazard ONNX model(s) fused with --hazard-model (repeatable)")
    ap.add_argument("--ensemble-weights", help="comma list, one weight per hazard model (default equal)")
    ap.add_argument("--fusion", default="wbf", choices=["wbf", "nms"], help="how ensemble boxes are merged")
    ap.add_argument("--conf-type", default="avg", choices=["avg", "max", "box_avg"],
                    help="WBF confidence: avg penalises boxes missing from a member (default)")
    ap.add_argument("--fusion-iou", type=float, default=0.55)
    ap.add_argument("--class-conf", help="per-label confidence floors after fusion, e.g. pothole=0.5,cone=0.4")
    ap.add_argument("--coco-model", default=str(default_coco or ROOT / "models" / "coco_yolo11n.onnx"))
    ap.add_argument("--coco-labels")
    ap.add_argument("--no-coco", action="store_true", help="skip the stock COCO detector")
    ap.add_argument("--no-hazard", action="store_true", help="skip the custom hazard detector")
    ap.add_argument("--conf", type=float, default=0.35, help="detector confidence threshold")
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--threads", type=int, default=4, help="ORT intra-op threads per model (phone-like: 4)")
    ap.add_argument("--zoom-crop", nargs="?", const="0.25,0.35,0.75,0.75", default=None,
                    help="also run the hazard model(s) on a zoomed crop of the road ahead (x1,y1,x2,y2 "
                         "fractions); one extra inference per model, finds small distant hazards. "
                         "run_video.py turns it on by default (pass --no-zoom to disable); eval_events.py "
                         "leaves it off unless asked, so old comparisons stay valid")
    ap.add_argument("--no-zoom", action="store_true", help="disable the zoom pass where it is on by default")
    ap.add_argument("--zoom-coco", action="store_true", help="apply the zoom pass to the COCO model too")
    ap.add_argument("--no-parallel", action="store_true", help="run models one after another (debugging)")


def build_hazard_detector(spec: DetectorSpec):
    """Hazard side only: plain detector, zoomed, ensemble, with class floors. None if no model."""
    members = []
    for p in spec.hazard_models:
        if not p.exists():
            _load_error(p, "file not found")
            continue
        det = _try_load(p, spec.hazard_labels, f"hazard:{p.stem}", spec)
        if det is None:
            continue
        if spec.zoom_crop:
            det = ZoomDetector(det, spec.zoom_crop, spec.iou)
        members.append(det)
    if not members:
        return None
    if len(members) == 1:
        det = members[0]
        det.name = "hazard"
        return ClassFilter(det, spec.class_conf) if spec.class_conf else det
    return EnsembleDetector(members, spec.weights, spec.fusion, spec.fusion_iou, spec.conf_type,
                            parallel=spec.parallel, class_conf=spec.class_conf, name="hazard")


def build_detectors(spec: DetectorSpec) -> list:
    """Everything the pipeline runs per frame: [hazard stack, coco]."""
    out = []
    hz = build_hazard_detector(spec)
    if hz is not None:
        out.append(hz)
    if spec.coco_model is not None:
        if not spec.coco_model.exists():
            _load_error(spec.coco_model, "file not found")
        else:
            coco = _try_load(spec.coco_model, spec.coco_labels, "coco", spec)
            if coco is not None:
                if spec.zoom_crop and spec.zoom_coco:
                    coco = ZoomDetector(coco, spec.zoom_crop, spec.iou)
                out.append(coco)
    return out


# models that could not be loaded in this process, as (path, reason); run_video.py prints them
LOAD_ERRORS: list[tuple[str, str]] = []


def _load_error(path: Path, reason: str) -> None:
    """Criterion 7.7: a missing or broken model is logged and skipped; the other models keep running."""
    LOAD_ERRORS.append((str(path), reason))
    print(f"[error] model {path} not loaded: {reason}; continuing with the other models", file=sys.stderr)


def _try_load(path: Path, labels: str | None, name: str, spec: DetectorSpec):
    try:
        return OnnxDetector(path, labels_for(path, labels), name, spec.conf, spec.iou, threads=spec.threads)
    except SystemExit as e:          # labels_for: no labels file next to the model
        _load_error(path, str(e))
    except Exception as e:           # noqa: BLE001 - corrupt file, wrong opset, class/label count mismatch
        _load_error(path, f"{type(e).__name__}: {e}")
    return None
