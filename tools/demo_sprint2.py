"""
Module Name: demo_sprint2.py
Description: Sprint 2 demo: runs the CV pipeline on a road clip or a live camera, shows the detections, and sends each confirmed hazard to the phone over adb where the MacroDroid macro opens the matching Waze report

Inputs: A video path or camera index, ONNX models in models/, and optionally an Android phone connected over USB with MacroDroid and Waze
Outputs: A live window with boxes, a readable banner per report, the HAZARD_DETECTED broadcast to the phone (dry_run=true), and runs/demo/ with the annotated video, the event log and the decision log
External Sources: Claude Opus 5.5
Attributions: wazeomatic/run_video.py does the detection; this script only prepares the run

Creation 11/17/2026 - Luke Reicherter and Claude Opus 5.5:
A description of how and why AI was used: Claude Opus 5.5 used to write a one-command demo of the Sprint 2 goal: the CV pipeline detects a hazard and hands it to the macro that reports it in Waze
How you validated and revised the AI output: Proofreading, a run on the CC0 dashcam clip without a phone, and --ping / --check against adb
The challenges or limitations you faced while using AI: The custom hazard model is not in the repository, so the script reports which Sprint 2 criteria the models present can show; broadcasts are always dry runs because a recorded clip does not show a hazard at the phone's real location
Authors: Luke Reicherter
Creation Date: 11/17/2026
"""
from __future__ import annotations

_USAGE = """Sprint 2 demo: CV pipeline -> confirmed hazard -> adb broadcast -> MacroDroid -> Waze report menu.

Examples
    python tools/demo_sprint2.py --check                            # models, adb and phone status only
    python tools/demo_sprint2.py --ping                             # one test broadcast: does the macro react?
    python tools/demo_sprint2.py assets/videos/workzone.mp4         # clip -> phone
    python tools/demo_sprint2.py 0 --camera                         # webcam: put a bag or chair in the lane box
    python tools/demo_sprint2.py clip.mp4 --no-phone                # no phone: window and console only
    python tools/demo_sprint2.py clip.mp4 -- --start 30 --verbose   # anything after -- goes to run_video.py
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wazeomatic import run_video  # noqa: E402
from wazeomatic.schema import HazardEvent  # noqa: E402
from wazeomatic.sinks import AdbBroadcastSink  # noqa: E402

MODELS = ROOT / "models"
OUT = ROOT / "runs" / "demo"


def find_adb() -> str | None:
    cand = [shutil.which("adb")]
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(env):
            cand.append(str(Path(os.environ[env]) / "platform-tools" / "adb"))
    cand += [str(Path.home() / "Library/Android/sdk/platform-tools/adb"),                 # macOS
             str(Path.home() / "Android/Sdk/platform-tools/adb"),                         # Linux
             str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk/platform-tools/adb.exe")]  # Windows
    for c in cand:
        if c and Path(c).exists():
            return c
    return None


def devices(adb: str) -> list[str]:
    try:
        out = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip().endswith("device")]


def model_status() -> tuple[bool, bool]:
    hazard = (MODELS / "hazard_yolo11n.onnx").exists()
    coco = (MODELS / "coco_yolo11n.onnx").exists()
    print("Models")
    print(f"  hazard_yolo11n.onnx  {'found' if hazard else 'MISSING'}  -> 7.1 pothole, 9.x construction")
    print(f"  coco_yolo11n.onnx    {'found' if coco else 'MISSING'}  -> 7.2 objects in the lane (Hazard > Object)")
    if not hazard:
        print("  The custom hazard model is not committed. Copy hazard_yolo11n.onnx (and keep\n"
              "  hazard_yolo11n.labels.txt) into models/ to demo potholes and construction.")
    if not coco:
        print("  Make the COCO model with: python tools/export_onnx.py --coco --imgsz 640")
    return hazard, coco


def phone_status(args) -> tuple[str | None, str | None]:
    """Returns (adb path, serial) or (None, None) when the phone leg is off."""
    if args.no_phone:
        print("Phone: off (--no-phone)")
        return None, None
    adb = find_adb()
    if adb is None:
        print("Phone: adb not found (install Android platform-tools or set ANDROID_HOME); running without it")
        return None, None
    devs = devices(adb)
    if args.serial:
        serial = args.serial if args.serial in devs else None
    else:
        serial = devs[0] if len(devs) == 1 else None
    if serial is None:
        print(f"Phone: {'no device' if not devs else 'several devices, pass --serial: ' + ', '.join(devs)} "
              f"on {adb}; running without it")
        return None, None
    print(f"Phone: {serial} via {adb}")
    return adb, serial


def ping(adb: str, serial: str) -> None:
    ev = HazardEvent(hazard_type="HAZARD_ON_ROAD_CONSTRUCTION", confidence=0.9, source="sim", label="ping")
    sink = AdbBroadcastSink(adb=adb, serial=serial, dry_run=True)
    sink.send(ev)
    print("Sent one dry-run HAZARD_ON_ROAD_CONSTRUCTION broadcast. The macro should open Waze, tap\n"
          "Hazard (and Construction if the subtype branch from android_setup/macro_setup_guide.md is set up),\n"
          "and stop before submitting.")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=_USAGE, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="video path, or camera index with --camera")
    ap.add_argument("--camera", action="store_true", help="source is a camera index")
    ap.add_argument("--serial", help="adb serial when several devices are connected")
    ap.add_argument("--no-phone", action="store_true", help="do not broadcast to a phone")
    ap.add_argument("--no-window", action="store_true", help="no live window (still records runs/demo/annotated.mp4)")
    ap.add_argument("--check", action="store_true", help="only print model and phone status")
    ap.add_argument("--ping", action="store_true", help="send one test broadcast and exit")
    args = ap.parse_args(argv)

    hazard, coco = model_status()
    adb, serial = phone_status(args)
    if args.check:
        return 0
    if args.ping:
        if adb is None:
            print("--ping needs a connected phone")
            return 1
        ping(adb, serial)
        return 0
    if not args.source:
        ap.error("give a video path or a camera index (or --check / --ping)")
    if not (hazard or coco):
        print("No models to run.")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    rv = [args.source, "--sink", "pretty", "--sink", f"jsonl:{OUT / 'events.jsonl'}",
          "--decision-log", str(OUT / "decisions.jsonl"), "--record", str(OUT / "annotated.mp4"),
          "--stats-json", str(OUT / "stats.json")]
    if args.camera:
        rv.append("--camera")
    if not args.no_window:
        rv.append("--show")
    if adb is not None:
        # always a dry run: the macro stops one tap before Report (see docs/sprint2.md, Safety)
        rv += ["--sink", f"adb:{serial}", "--adb", adb, "--adb-dry-run"]
    rv += extra
    print("\nRunning: python -m wazeomatic.run_video " + " ".join(rv))
    print("Press q in the window to stop.\n")
    rc = run_video.main(rv)
    print(f"\nOutputs in {OUT}: annotated.mp4, events.jsonl, decisions.jsonl, stats.json")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
