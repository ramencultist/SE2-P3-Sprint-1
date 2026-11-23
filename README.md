Sprint assignments are recorded in the [requirements stack](EECS%20581-Project%203%20Requirements%20Group%201.xlsx).

The [architecture document](EECS%20581%20Project%203%20Architecture.docx) includes the Sprint 2 acceptance criteria and Sprint 3 criteria for police detection (#3) and the Android UI (#6).

The sprint 1 branch contains all the information for sprint 1 frozen for the 11/8/2026 deadline at 11:59 PM.


## Sprint 2

Sprint 2 adds requirements 7 (in-road hazard detection) and 9 (construction identification). Each acceptance
criterion is traced to its code and tests, and the demo steps are listed, in [docs/sprint2.md](docs/sprint2.md).

Quick start:

```bash
pip install -r requirements.txt
python -m pytest -q tests
python tools/demo_sprint2.py --check            # models and phone status
python tools/demo_sprint2.py <clip.mp4>         # CV pipeline -> phone -> MacroDroid -> Waze (dry run)
```

Trained models are hosted at: https://kansas-my.sharepoint.com/:f:/g/personal/b035p624_home_ku_edu/IgBagvT6lkGVSZIOVxojNN41AUTROiLM0rebVrMMA14Icok?e=ayQU8v

For the Sprint 2 release, copy `models/hazard_yolo11n_v3.onnx` from SharePoint into the checkout as
`models/hazard_yolo11n.onnx`. Use the checked-in six-class `hazard_yolo11n.labels.txt`. The unversioned
hazard model in SharePoint is the seven-class v4 export. Copy `models/coco_yolo11n.onnx` alongside it.
The labeled work-zone clip is in `Video Data/assets/videos/labelled/argo_2ff4f798_workzone.mp4` on SharePoint.
See the [Sprint 2 runbook](docs/sprint2.md#construction-replay) for desktop and emulator verification.
