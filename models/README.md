# Model files

Sprint 2 uses the six-class `hazard_yolo11n_v3.onnx` from the
[team SharePoint model folder](../README.md#sprint-2). Save it here as `hazard_yolo11n.onnx`
beside the checked-in `hazard_yolo11n.labels.txt`. The desktop runner reads that path by
default, and the Android build packages it when present.

The expected SHA-256 is `4202cdcdcad00ee3778fe1586fe26bb1f6f7942bc0635afd23774f792fa07b77`.
Check it with `Get-FileHash models/hazard_yolo11n.onnx -Algorithm SHA256` on Windows
or `shasum -a 256 models/hazard_yolo11n.onnx` on macOS or Linux.
The SharePoint package's `NOTICE.md` documents the training-data terms and the hold on
public release of the weights. Model binaries are ignored by Git.

| File | What | How to produce |
|------|------|----------------|
| `hazard_yolo11n.onnx` | Fine-tuned road hazard detector, v3 (pothole, cone, construction sign, delineator, barricade, worker; a copy of `hazard_yolo11n_v3.onnx`, trained on the v3 set, 640 px) | `python tools/train.py --data C:/dev/datasets/wazeomatic_hazards_v3/data_train.yaml --imgsz 640` then `python tools/export_onnx.py --imgsz 640` |
| `hazard_yolo11s_v3.onnx` | YOLO11s trained on the same v3 set (desktop or ensemble partner; about 3x the compute of the nano) | same with `--model yolo11s.pt` |
| `hazard_yolo11n_640c.onnx`, `_640b`, `_416` | earlier 4-class models kept for comparison and as ensemble members | see docs/model-research.md |
| `coco_yolo11n.onnx` | Stock COCO detector used for vehicles, people, animals | `python tools/export_onnx.py --coco` |
| `*.int8.onnx` | Quantized copies for the phone | `python tools/quantize_onnx.py <model>` |

See `docs/model-research.md` for the reasoning behind the model choice and the
measured sizes and latencies.
