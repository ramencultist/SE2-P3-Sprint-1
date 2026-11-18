<!--
Module Name: sprint2.md
Description: Sprint 2 status: requirements 7 (in-road hazard detection) and 9 (construction identification), each acceptance criterion traced to code, tests and status, and the demo steps

Inputs: EECS 581-Project 3 Requirements Group 1.xlsx (sprint 2 selection) and the Sprint 2 Requirements Artifacts in EECS 581 Project 3 Architecture.docx
Outputs: Traceability table, demo runbook and open items
External Sources: Claude Opus 5.5
Attributions:

Creation 11/17/2026 - Luke Reicherter and Claude Opus 5.5:
A description of how and why AI was used: Claude Opus 5.5 used to map every Sprint 2 criterion to the code and test that covers it and to write the demo runbook
How you validated and revised the AI output: Checked each row against tests/test_sprint2_requirements.py, Sprint2RequirementsTest.kt and a pipeline run on the CC0 dashcam clip
The challenges or limitations you faced while using AI: Criteria that depend on the MacroDroid macro or the real phone cannot be verified from the repository and are marked as such
Authors: Luke Reicherter
Creation Date: 11/17/2026
-->

# Sprint 2

Sprint 2 implements requirements **7, in-road hazard detection** (8 points) and **9, construction identification** (13 points).
The goal from the 11/11 meeting: show the model detecting a hazard and the macro reporting it in Waze.

## What is new in this sprint

| Area | Files |
| --- | --- |
| Six-class hazard model schema (pothole, cone, construction sign, delineator, barricade, worker) | `configs/classes.yaml`, `models/hazard_yolo11n.labels.txt` |
| Learnt hood zone, so the car's own dashboard is never reported (7.6) | `wazeomatic/hood.py`, `HoodEstimator.kt` |
| Zoom pass for small, distant cones and potholes, plus box fusion | `wazeomatic/detector.py` (`ZoomDetector`), `wazeomatic/fusion.py`, `ZoomCrop.kt`, `BoxFusion.kt` |
| Detector builder that logs and skips a broken model instead of crashing (7.7) | `wazeomatic/build.py` |
| Decision log: every low-confidence, unconfirmed, hood-zone and suppressed detection is recorded with its score | `wazeomatic/report_manager.py` (`Decision`), `ReportManager.kt`, `run_video.py --decision-log / -v`, `adb logcat -s WazeomaticDecision` |
| Road objects: stock COCO objects in the driving lane become Hazard > Object (7.2) | `report_manager.py` (`road_object_*`), `ReportManager.kt` |
| Event-level evaluation (7.8) | `tools/eval_events.py` |
| One-command demo | `tools/demo_sprint2.py` |
| The Android apps build again (manifests, resources and Gradle wrappers were missing from the sprint 1 copy) | `android/detector/`, `android/mockwaze/` |

## Acceptance criteria

`py` = `tests/test_sprint2_requirements.py`, `kt` = `android/detector/app/src/test/.../Sprint2RequirementsTest.kt`.

| ID | Criterion (short) | Where | Test | Status |
| --- | --- | --- | --- | --- |
| 7.1 | Pothole in 3 of 10 frames -> `HAZARD_ON_ROAD_POTHOLE`, Hazard > Pothole | `ReportManager.update` | py `test_7_1`, kt `c7_1` | Pipeline done. Needs the hazard model and the macro subtype branch for Waze |
| 7.2 | Debris or object in the lane -> `HAZARD_ON_ROAD_OBJECT`, Hazard > Object | `_hazard_for` / `_in_lane`, `debris` class in `schema.py` | py `test_7_2_*`, kt `c7_2_*` | Done with COCO objects (suitcase, bag, chair, ...). The custom model has no debris class yet |
| 7.3 | Fewer than 3 of 10 frames -> no event, logged | `UNCONFIRMED` decision | py `test_7_3`, kt `c7_3` | Done |
| 7.4 | Below threshold -> no event, logged with score | `LOW_CONFIDENCE` decision | py `test_7_4`, kt `c7_4` | Done (only scores above the detector's 0.35 cut reach the log) |
| 7.5 | Same type within 60 s -> no duplicate, logged | `COOLDOWN` decision | py `test_7_5`, kt `c7_5` | Done (logged once per 10-frame window while the hazard stays in view) |
| 7.6 | Box in the hood zone -> ignored, logged | `HOOD_ZONE` decision, `hood.py` | py `test_7_6`, kt `c7_6` | Done |
| 7.7 | Model missing or fails to load -> logged, shown, no crash, other models keep running | `build.py` `_try_load`, `Pipeline.kt` model status | py `test_7_7` (missing, corrupt, no labels) | Done. Checked on a real run without the hazard model |
| 7.8 | Labelled clip -> exactly one event per hazard, correct type | `Pipeline`, `tools/eval_events.py` | py `test_7_8` (scripted 90 s clip) | Done on a scripted clip. A labelled real pothole/debris clip is still needed |
| 9.1 | Cones, signs, delineators, barricades or workers in 3 of 10 frames -> `HAZARD_ON_ROAD_CONSTRUCTION` | `classes.yaml`, `schema.py` | py `test_9_1` (all five classes), kt `c9_1` | Pipeline done. Needs the hazard model |
| 9.2 | Several construction classes -> one event | one hit per hazard type per frame | py `test_9_2`, kt `c9_2` | Done |
| 9.3 | Still in the work zone after a report -> nothing until the cooldown ends, suppressions logged | `COOLDOWN` decision | py `test_9_3`, kt `c9_3` | Done |
| 9.4 | Stray cone seen briefly -> no report, logged | `UNCONFIRMED` decision | py `test_9_4`, kt `c9_4` | Done |
| 9.5 | Low light or partly hidden -> below threshold, logged with scores | `LOW_CONFIDENCE` decision | py `test_9_5`, kt `c9_5` | Done |
| 9.6 | Macro cannot find Construction -> retry, notify the user, log | MacroDroid macro | n/a | To do in the macro, see [the macro guide](../android_setup/macro_setup_guide.md#sprint-2-subtypes-and-retries) |
| 9.7 | Work-zone clip on the emulator -> exactly one Hazard > Construction in mock Waze | `tools/emulator/run_e2e.py` | emulator Stage B | Needs the hazard model and a work-zone clip on the emulator |

Run the tests:

```bash
pip install -r requirements.txt
python -m pytest -q tests                                   # 74 tests, 23 of them for sprint 2
cd android/detector && ./gradlew testDebugUnitTest          # 53 tests, 13 of them for sprint 2 (JDK 17-21)
```

## Demo

The laptop stands in for the Raspberry Pi: it runs the same Python pipeline and sends each report to the phone over
USB with adb. That is the Pi-to-phone path from the architecture document. The phone receives the same
`com.wazeomatic.HAZARD_DETECTED` broadcast the sprint 1 intent tester sent, so the sprint 1 macro picks it up unchanged.

**Before the demo**

1. `pip install -r requirements.txt`
2. Models in `models/`:
   - `coco_yolo11n.onnx`: `pip install ultralytics` then `python tools/export_onnx.py --coco --imgsz 640` (enough for Hazard > Object)
   - `hazard_yolo11n.onnx`: the trained six-class model, needed for potholes and construction. It is not committed; copy it from whoever trained it.
3. A clip in `assets/videos/`: `python tools/fetch_test_videos.py` downloads the CC0 dashcam clips. They are
   hazard-free apart from one parking-garage cone, so they show the pipeline staying quiet. For a positive demo use
   team dashcam footage of a work zone or a pothole, or a live camera.
4. The phone: USB debugging on, MacroDroid with the sprint 1 macro, Waze open. Run `python tools/demo_sprint2.py --check`.
5. `python tools/demo_sprint2.py --ping` sends one test Construction broadcast. The macro should open Waze's report menu.

**The demo**

```bash
python tools/demo_sprint2.py assets/videos/<workzone clip>.mp4      # construction (needs the hazard model)
python tools/demo_sprint2.py 0 --camera                              # live: a bag or chair inside the blue "lane" box
```

The window shows the boxes, the hood line and the lane box. Each confirmed hazard prints a `REPORT` banner, and
the broadcast goes to the phone, where the macro opens Waze and taps through to the report. `runs/demo/` keeps the
annotated video, `events.jsonl` (what was sent) and `decisions.jsonl` (everything that was not sent, and why).
Add `-- --verbose` to print the decision log live.

**Without a phone (Mac or Linux, emulator).** Mock Waze (`android/mockwaze`) copies the Waze report menu and has a
built-in auto macro that makes the same taps as the MacroDroid macro:

```bash
./tools/emulator/start_mock_waze_mac.sh            # boots the first AVD, installs and starts mock Waze
python tools/demo_sprint2.py --ping                # emulator: Hazard > Construction, countdown, dry-run cancel
python tools/demo_sprint2.py 0 --camera -- --no-auto-hood    # webcam -> pipeline -> emulator
```

`demo_sprint2.py` picks up the emulator as the phone automatically. Mock Waze's log of what the macro did:
`adb logcat -s MockWazeMacro MockWazeReport`. Leave the emulator's animation settings on: with animator duration
at 0, the confirm sheet sends by itself before the macro can tap.

**Safety.** The demo always sends `dry_run=true`, so the macro stops before Report. A recorded clip does not show
a hazard at the phone's real location, so submitting would put a false report on the public map.

## Open items

- Get `hazard_yolo11n.onnx` into the hands of everyone running the demo (a GitHub release asset or shared drive; it is 10 MB).
- Add the subtype branch and the retry/notify path (9.6) to the MacroDroid macro.
- Record team dashcam footage of a real work zone and a pothole, and label it, for 7.8 and 9.7.
- Train a `debris` class into the hazard model; until then 7.2 relies on COCO objects.
- Run the emulator suite (`tools/emulator/run_e2e.py`, Windows PowerShell scripts) with a work-zone clip for 9.7.
