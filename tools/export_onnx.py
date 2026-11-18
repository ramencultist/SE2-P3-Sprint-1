"""
Module Name: export_onnx.py
Description: Exports a YOLO checkpoint (the custom hazard model or the stock COCO model) to a static-shape ONNX file with its labels file

Inputs: A .pt checkpoint or --coco; Ultralytics installed
Outputs: models/<name>.onnx and models/<name>.labels.txt
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the export wrapper around the Ultralytics exporter
How you validated and revised the AI output: Proofreading, and loading the exported files in OnnxDetector on the desktop and in the Android app
The challenges or limitations you faced while using AI: Choosing a static input shape and opset that both ONNX Runtime desktop and Android accept
Authors: Blake Pennel
Creation Date: 11/5/2026

Ported to the SE2-P3 repository 11/17/2026 by Luke Reicherter with Claude Opus 5.5 for Sprint 2 (requirements 7 and 9); code unchanged from the team's wazeomatic development repository unless a Revision entry says otherwise
"""
from __future__ import annotations
"""Export a YOLO checkpoint to a static-shape ONNX file for ONNX Runtime.

Examples
    python tools/export_onnx.py --coco                       # stock yolo11n, 80 classes
    python tools/export_onnx.py --weights runs/train/hazard/weights/best.pt --imgsz 416
"""

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"

COCO_LABELS = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", help="path to .pt checkpoint")
    ap.add_argument("--coco", action="store_true", help="export the stock COCO yolo11n instead")
    ap.add_argument("--imgsz", type=int, default=416)
    ap.add_argument("--opset", type=int, default=17)
    ap.add_argument("--out", help="output .onnx path (default models/<name>.onnx)")
    ap.add_argument("--half", action="store_true", help="fp16 weights (CPU EPs usually prefer fp32)")
    ap.add_argument("--no-stable", action="store_true", help="do not refresh models/<name>.onnx (the copy the apps use)")
    args = ap.parse_args()

    from ultralytics import YOLO

    if args.coco:
        weights = "yolo11n.pt"
        name = "coco_yolo11n"
        labels = COCO_LABELS
    else:
        if not args.weights:
            ap.error("--weights or --coco required")
        weights = args.weights
        name = "hazard_yolo11n"
        labels = None

    model = YOLO(weights)
    if labels is None:
        labels = [model.names[i] for i in range(len(model.names))]

    exported = model.export(format="onnx", imgsz=args.imgsz, opset=args.opset, simplify=True,
                            dynamic=False, half=args.half, nms=False)
    MODELS.mkdir(exist_ok=True)
    out = Path(args.out) if args.out else MODELS / f"{name}_{args.imgsz}.onnx"
    shutil.move(str(exported), str(out))
    (out.with_suffix(".labels.txt")).write_text("\n".join(labels) + "\n", encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB), {len(labels)} labels")
    if not args.no_stable:
        # convenience copies with stable names that the Android app and configs refer to
        stable = MODELS / f"{name}.onnx"
        shutil.copyfile(out, stable)
        shutil.copyfile(out.with_suffix(".labels.txt"), stable.with_suffix(".labels.txt"))
        print(f"refreshed {stable}")


if __name__ == "__main__":
    main()
