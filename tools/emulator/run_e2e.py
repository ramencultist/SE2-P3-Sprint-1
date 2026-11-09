"""
Module Name: run_e2e.py
Description: End-to-end test on the Pixel 9 Pro emulator for the reporting flow and the detector app

Class Name: Runner
Description: Drives the emulator over adb: injects broadcasts, taps, pulls logs and screenshots, and records pass or fail checks

Inputs: A running emulator, the mock Waze and detector APKs and a test video
Outputs: Pass or fail checks for Stage A and Stage B and a run folder with logs, screenshots and a summary
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the automated checks for reporting once, the 60 s duplicate guard, dry run, unknown hazard types and the detector to mock Waze path
How you validated and revised the AI output: Proofreading, and repeated runs until all 18 checks passed
The challenges or limitations you faced while using AI: Emulator not-responding dialogs and slow boots interrupting the taps
Authors: Blake Pennel
Creation Date: 11/5/2026
"""
from __future__ import annotations

_USAGE = """End-to-end test on the pixel9pro_test emulator.

Stage A (mock Waze only): start mock Waze with auto macro on, inject HAZARD_DETECTED
broadcasts with adb, pull reports.jsonl / macro_events.jsonl and check
  - a manual rehearsal through `adb shell input tap` (like MacroDroid coordinate taps) works,
  - a real event is reported exactly once with the right category/subcategory,
  - a second event inside 60 s is suppressed,
  - dry_run=true reaches the confirm sheet, cancels and is logged with dry_run=true.

Stage B (needs the detector APK, skipped otherwise): push a dashcam clip, run the detector in
video mode, read its WazeomaticEvent logcat lines and check mock Waze received them.

Usage (from the repo root):
  .venv\\Scripts\\python.exe tools\\emulator\\run_e2e.py              # Stage A + B (B skipped if no detector)
  .venv\\Scripts\\python.exe tools\\emulator\\run_e2e.py --stage a
  .venv\\Scripts\\python.exe tools\\emulator\\run_e2e.py --stage b --video-seconds 90

Everything (logcat, screenshots, pulled jsonl, summary) goes to runs/emulator/<timestamp>/.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SDK = Path(os.environ.get("ANDROID_HOME", r"F:\dev\android-sdk"))
ADB = str(SDK / "platform-tools" / "adb.exe")
MOCK_PKG = "com.wazeomatic.mockwaze"
DET_PKG = "com.wazeomatic.detector"
MOCK_APK = REPO / "android" / "mockwaze" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
DET_APK_GLOB = str(REPO / "android" / "detector" / "app" / "build" / "outputs" / "apk" / "debug" / "*.apk")
REMOTE_DIR = f"/sdcard/Android/data/{MOCK_PKG}/files"
HAZARD_ACTION = "com.wazeomatic.HAZARD_DETECTED"

# Expected menu paths, must match wazeomatic/schema.py (checked at start-up if importable).
EXPECTED = {
    "HAZARD_ON_ROAD_POTHOLE": ("Hazard", "Pothole"),
    "HAZARD_ON_ROAD_CONSTRUCTION": ("Hazard", "Construction"),
    "HAZARD_ON_ROAD_OBJECT": ("Hazard", "Object"),
    "HAZARD_SHOULDER_CAR_STOPPED": ("Hazard", "Car on shoulder"),
    "HAZARD_SHOULDER_ANIMALS": ("Hazard", ""),
    "POLICE": ("Police", ""),
    "CRASH": ("Crash", ""),
}

# Timings used for the run (ms). macro_wait_ms must be longer than sheet_ms/page_ms.
TIMING = {
    "sheet_ms": 300,
    "page_ms": 200,
    "toast_ms": 1500,
    # Waze sends by itself after a few seconds (mock default 5000). The manual rehearsal in
    # Stage A is slowed down by uiautomator dumps, so give it more room here.
    "auto_send_ms": 10000,
    "macro_start_delay_ms": 500,
    "macro_wait_ms": 1000,
    "dry_run_hold_ms": 1500,
    "rate_limit_ms": 60000,
}


class Runner:
    def __init__(self, serial: str, out: Path):
        self.serial = serial
        self.detector_extras = ""
        self.out = out
        self.results: list[tuple[str, str, str]] = []  # (status, name, detail)
        self.logcat_proc: subprocess.Popen | None = None
        self.log_lines: list[str] = []

    # ------------------------------------------------------------ adb helpers
    def adb(self, *args: str, timeout: float = 60, check: bool = False) -> str:
        cmd = [ADB, "-s", self.serial, *args]
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
        if check and p.returncode != 0:
            raise RuntimeError(f"adb {' '.join(args)} failed: {out.strip()}")
        return out

    def sh(self, cmd: str, timeout: float = 60) -> str:
        return self.adb("shell", cmd, timeout=timeout)

    def log(self, msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        print(line, flush=True)
        self.log_lines.append(line)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append(("PASS" if ok else "FAIL", name, detail))
        self.log(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
        return ok

    def skip(self, name: str, why: str) -> None:
        self.results.append(("SKIP", name, why))
        self.log(f"SKIP  {name}  ({why})")

    def screenshot(self, name: str) -> Path:
        remote = f"/sdcard/e2e_{name}.png"
        self.sh(f"screencap -p {remote}")
        local = self.out / f"{name}.png"
        self.adb("pull", remote, str(local))
        self.sh(f"rm {remote}")
        return local

    def start_logcat(self) -> None:
        self.adb("logcat", "-c")
        f = open(self.out / "logcat.txt", "wb")
        self.logcat_proc = subprocess.Popen([ADB, "-s", self.serial, "logcat", "-v", "time"],
                                            stdout=f, stderr=subprocess.STDOUT)

    def stop_logcat(self) -> None:
        if self.logcat_proc:
            self.logcat_proc.terminate()
            try:
                self.logcat_proc.wait(5)
            except subprocess.TimeoutExpired:
                self.logcat_proc.kill()

    def logcat_text(self) -> str:
        try:
            return (self.out / "logcat.txt").read_bytes().decode("utf-8", "replace")
        except FileNotFoundError:
            return ""

    def pull_jsonl(self, name: str) -> list[dict]:
        text = self.sh(f"cat {REMOTE_DIR}/{name} 2>/dev/null")
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return rows

    def save_jsonl(self, suffix: str = "") -> None:
        for name in ("reports.jsonl", "macro_events.jsonl"):
            rows = self.pull_jsonl(name)
            (self.out / name.replace(".jsonl", f"{suffix}.jsonl")).write_text(
                "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def broadcast(self, hazard_type: str, confidence: str = "0.87", dry_run: bool = False,
                  source: str = "e2e") -> None:
        ts = f"{time.time():.3f}"
        cat, sub = EXPECTED.get(hazard_type, ("", ""))
        cmd = (f"am broadcast -a {HAZARD_ACTION} --es hazard_type {hazard_type} "
               f"--es confidence {confidence} --es ts {ts} --es source {source} "
               f"--es dry_run {'true' if dry_run else 'false'}")
        if cat:
            cmd += f" --es waze_category {cat}"
        if sub:
            cmd += f" --es waze_subcategory '{sub}'"
        out = self.sh(cmd)
        self.log(f"broadcast {hazard_type} dry_run={dry_run}: {out.strip().splitlines()[-1] if out.strip() else ''}")

    def wait_macro_outcome(self, hazard_type: str, outcomes: set[str], after_ts: float,
                           timeout: float = 25) -> dict | None:
        end = time.time() + timeout
        while time.time() < end:
            for row in self.pull_jsonl("macro_events.jsonl"):
                if (row.get("hazard_type") == hazard_type and row.get("outcome") in outcomes
                        and float(row.get("ts", 0)) >= after_ts - 2):
                    return row
            time.sleep(0.5)
        return None

    def device_time(self) -> float:
        out = self.sh("date +%s").strip().splitlines()
        try:
            return float(out[-1])
        except (ValueError, IndexError):
            return time.time()

    # ------------------------------------------------------------ UI helpers (uiautomator)
    def ui_nodes(self) -> list[dict]:
        self.sh("uiautomator dump /sdcard/e2e_ui.xml", timeout=30)
        xml = self.sh("cat /sdcard/e2e_ui.xml")
        start = xml.find("<?xml")
        if start < 0:
            return []
        try:
            root = ET.fromstring(xml[start:].strip())
        except ET.ParseError:
            return []
        nodes = []
        for n in root.iter("node"):
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds", ""))
            if not m:
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            nodes.append({"text": n.get("text", ""), "desc": n.get("content-desc", ""),
                          "pkg": n.get("package", ""), "clickable": n.get("clickable") == "true",
                          "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2, "bounds": (x1, y1, x2, y2)})
        return nodes

    def tap_label(self, label: str, retries: int = 6) -> tuple[int, int] | None:
        """Find a view by text or content description and tap its centre with `input tap`."""
        for _ in range(retries):
            nodes = [n for n in self.ui_nodes() if n["pkg"] == MOCK_PKG and label in (n["text"], n["desc"])]
            nodes.sort(key=lambda n: not n["clickable"])
            if nodes:
                n = nodes[0]
                self.sh(f"input tap {n['cx']} {n['cy']}")
                self.log(f"tap '{label}' at ({n['cx']},{n['cy']})")
                return n["cx"], n["cy"]
            time.sleep(0.7)
        self.log(f"could not find '{label}' on screen")
        return None

    # ------------------------------------------------------------ setup
    def ensure_installed(self, install: bool) -> bool:
        have = MOCK_PKG in self.sh(f"pm path {MOCK_PKG}")
        if install or not have:
            if not MOCK_APK.exists():
                self.log(f"mock Waze APK missing: {MOCK_APK} (build it with gradlew.bat assembleDebug)")
                return False
            out = self.adb("install", "-r", "-g", str(MOCK_APK), timeout=180)
            self.log(f"install mock Waze: {out.strip().splitlines()[-1] if out.strip() else '?'}")
        return MOCK_PKG in self.sh(f"pm path {MOCK_PKG}")

    def launch_mock(self, **extra) -> None:
        cfg = dict(TIMING)
        cfg.update(extra)
        parts = [f"am start -W -n {MOCK_PKG}/.MainActivity"]
        for k, v in cfg.items():
            if isinstance(v, bool):
                parts.append(f"--ez {k} {'true' if v else 'false'}")
            else:
                parts.append(f"--ei {k} {int(v)}")
        out = self.sh(" ".join(parts), timeout=60)
        self.log("launch mock Waze: " + " | ".join(l.strip() for l in out.splitlines() if "Status" in l or "Error" in l))

    def service_running(self) -> bool:
        return "AutoMacroService" in self.sh(f"dumpsys activity services {MOCK_PKG}")

    def dismiss_anr_dialogs(self) -> None:
        """A host overload (training, a second emulator) leaves "<app> isn't responding" dialogs
        on top of everything; they swallow every tap of the stages below. Kill the offending
        process so the dialog goes away (the launcher restarts by itself)."""
        focus = self.sh("dumpsys window | grep -E 'mCurrentFocus'")
        if "Not Responding" in focus:
            import re
            m = re.search(r"Not Responding: ([\w.]+)", focus)
            if m:
                self.log(f"dismissing not-responding dialog of {m.group(1)}")
                self.sh(f"am force-stop {m.group(1)}")
                time.sleep(2)

    # ------------------------------------------------------------ stage A
    def stage_a(self) -> None:
        self.log("=== Stage A: mock Waze auto macro ===")
        self.sh(f"am force-stop {MOCK_PKG}")
        # a detector left running by an earlier run would keep broadcasting its own events
        # into this stage and the mock would report "skipped_busy" for ours
        self.sh(f"am force-stop {DET_PKG}")
        self.sh("input keyevent 82")  # make sure the lock screen is gone
        self.sh("svc power stayon true")
        self.dismiss_anr_dialogs()
        self.launch_mock(auto_macro=True, require_foreground=True, bring_to_front=False,
                         clear_reports=True, reset_rate_limit=True)
        time.sleep(2.5)
        self.check("A0 auto macro service running", self.service_running())
        self.screenshot("A0_map")

        # --- A1: manual rehearsal with input taps, the way the MacroDroid macro taps ------
        self.log("A1: manual rehearsal with adb input tap (Report button > Hazard > Pothole > Cancel)")
        t0 = self.device_time()
        ok = self.tap_label("Report button") is not None
        time.sleep(0.8)
        self.screenshot("A1_report_grid")
        ok = ok and self.tap_label("Hazard") is not None
        time.sleep(0.8)
        self.screenshot("A1_hazard_sheet")
        ok = ok and self.tap_label("Pothole") is not None
        time.sleep(0.6)
        self.screenshot("A1_pothole_selected")
        ok = ok and self.tap_label("Cancel") is not None
        time.sleep(1.0)
        rows = [r for r in self.pull_jsonl("reports.jsonl") if r.get("via") == "touch" and r.get("ts", 0) >= t0 - 2]
        good = [r for r in rows if r.get("category") == "Hazard" and r.get("subcategory") == "Pothole"
                and r.get("dry_run") is True and r.get("action") == "cancel"]
        self.check("A1 manual tap rehearsal logged as dry_run=true", ok and len(good) == 1,
                   f"{len(rows)} touch row(s): {rows}")

        # also screenshot a one-step category and the Thanks! toast via a manual Police send
        self.tap_label("Report button"); time.sleep(0.8)
        self.tap_label("Police"); time.sleep(0.8)
        self.screenshot("A1_police_confirm")
        self.tap_label("Report"); time.sleep(0.4)
        self.screenshot("A1_thanks_toast")
        time.sleep(1.5)

        # --- A2: real event via broadcast -> exactly one report ---------------------------
        self.log("A2: HAZARD_ON_ROAD_POTHOLE dry_run=false")
        t_a2 = self.device_time()
        self.broadcast("HAZARD_ON_ROAD_POTHOLE", "0.87", dry_run=False)
        # timeline: start delay 0.5 s, then a tap every 1.0 s -> confirm sheet at ~2.5-3.4 s
        time.sleep(3.0)
        self.screenshot("A2_macro_confirm_sheet")
        row = self.wait_macro_outcome("HAZARD_ON_ROAD_POTHOLE", {"sent", "failed"}, t_a2)
        self.check("A2 macro finished with outcome=sent", bool(row and row["outcome"] == "sent"), str(row))
        time.sleep(0.5)
        self.screenshot("A2_after_send")
        sends = [r for r in self.pull_jsonl("reports.jsonl") if r.get("via") == "auto_macro" and r.get("action") == "send"]
        pothole = [r for r in sends if r.get("category") == "Hazard" and r.get("subcategory") == "Pothole"
                   and r.get("dry_run") is False and r.get("hazard_type") == "HAZARD_ON_ROAD_POTHOLE"]
        self.check("A2 Hazard > Pothole reported exactly once", len(pothole) == 1 and len(sends) == 1,
                   f"{len(sends)} auto send(s): {sends}")

        # --- A3: second event inside 60 s is suppressed ------------------------------------
        self.log("A3: CRASH dry_run=false inside the 60 s window")
        t_a3 = self.device_time()
        self.broadcast("CRASH", "0.91", dry_run=False)
        row = self.wait_macro_outcome("CRASH", {"suppressed_rate_limit", "accepted", "sent", "failed"}, t_a3, 10)
        self.check("A3 second event inside 60 s suppressed", bool(row and row["outcome"] == "suppressed_rate_limit"),
                   str(row))
        time.sleep(4.5)  # long enough that a non-suppressed macro would have sent
        sends = [r for r in self.pull_jsonl("reports.jsonl") if r.get("via") == "auto_macro" and r.get("action") == "send"]
        self.check("A3 still exactly one real report", len(sends) == 1, f"{len(sends)} send(s)")

        # --- A4: dry run -> confirm sheet, wait, cancel, logged dry_run=true ----------------
        self.log("A4: HAZARD_ON_ROAD_CONSTRUCTION dry_run=true (inside the window, dry runs are not limited)")
        t_a4 = self.device_time()
        self.broadcast("HAZARD_ON_ROAD_CONSTRUCTION", "0.66", dry_run=True)
        time.sleep(3.6)  # confirm sheet is held for dry_run_hold_ms before Cancel
        self.screenshot("A4_dry_run_confirm_sheet")
        row = self.wait_macro_outcome("HAZARD_ON_ROAD_CONSTRUCTION", {"dry_run_cancelled", "failed",
                                                                       "suppressed_rate_limit"}, t_a4)
        self.check("A4 dry run finished with outcome=dry_run_cancelled",
                   bool(row and row["outcome"] == "dry_run_cancelled"), str(row))
        rows = [r for r in self.pull_jsonl("reports.jsonl") if r.get("via") == "auto_macro"
                and r.get("hazard_type") == "HAZARD_ON_ROAD_CONSTRUCTION"]
        self.check("A4 dry run logged dry_run=true, action=cancel, Hazard > Construction",
                   len(rows) == 1 and rows[0].get("dry_run") is True and rows[0].get("action") == "cancel"
                   and rows[0].get("subcategory") == "Construction", str(rows))
        sends = [r for r in self.pull_jsonl("reports.jsonl") if r.get("action") == "send" and r.get("via") == "auto_macro"]
        self.check("A4 no real send from the dry run", len(sends) == 1, f"{len(sends)} auto send(s)")

        # --- A5: unknown hazard type is ignored ------------------------------------------
        t_a5 = self.device_time()
        self.broadcast("NOT_A_REAL_HAZARD", "0.5", dry_run=False)
        row = self.wait_macro_outcome("NOT_A_REAL_HAZARD", {"unknown_hazard_type"}, t_a5, 8)
        self.check("A5 unknown hazard_type ignored", row is not None, str(row))

        # --- logcat ------------------------------------------------------------------------
        time.sleep(1)
        text = self.logcat_text()
        n_report = len(re.findall(r"MockWazeReport", text))
        self.check("A6 MockWazeReport lines in logcat", n_report >= 3, f"{n_report} line(s)")
        self.save_jsonl("_stage_a")

    # ------------------------------------------------------------ stage B
    def stage_b(self, video: str, video_seconds: int, wait_s: int) -> None:
        self.log("=== Stage B: detector video mode -> mock Waze ===")
        apks = sorted(glob.glob(DET_APK_GLOB), key=os.path.getmtime)
        installed = DET_PKG in self.sh(f"pm path {DET_PKG}")
        if not apks and not installed:
            self.skip("Stage B", "detector APK not built (android/detector/app/build/outputs/apk/debug/*.apk)")
            return
        if apks:
            out = self.adb("install", "-r", "-g", apks[-1], timeout=300)
            if not self.check("B0 detector installed", "Success" in out, out.strip().splitlines()[-1] if out else ""):
                return
        ps = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "push_video.ps1"),
              "-InputFile", video, "-Port", self.serial.split("-")[-1]]
        if video_seconds:
            ps += ["-Seconds", str(video_seconds)]
        p = subprocess.run(ps, capture_output=True, text=True, timeout=900)
        (self.out / "push_video.log").write_text(p.stdout + p.stderr, encoding="utf-8")
        if not self.check("B1 video pushed", p.returncode == 0, (p.stdout + p.stderr).strip().splitlines()[-1:][0]
                          if (p.stdout + p.stderr).strip() else ""):
            return

        # Mock Waze must take events while the detector is on screen; bring_to_front needs the
        # "display over other apps" app-op to start an activity from the background.
        self.sh(f"appops set {MOCK_PKG} SYSTEM_ALERT_WINDOW allow")
        self.sh(f"am force-stop {MOCK_PKG}")
        self.launch_mock(auto_macro=True, require_foreground=True, bring_to_front=True,
                         clear_reports=True, reset_rate_limit=True)
        time.sleep(2)
        mark = self.logcat_text().count("\n")
        self.sh(f"am force-stop {DET_PKG}")
        out = self.sh(f"am start -n {DET_PKG}/.MainActivity --es source video --es video test.mp4 "
                      f"--ez reset_settings true {self.detector_extras}".strip())
        self.log("start detector: " + out.strip().replace("\n", " | "))
        self.screenshot("B_detector_start")
        # On a phone Waze is in front and the detector works from its foreground service; reproduce that
        time.sleep(5)
        self.launch_mock(auto_macro=True, require_foreground=True, bring_to_front=True)
        self.screenshot("B_mock_front")
        self.log(f"waiting {wait_s} s for the detector to run through the clip...")
        t_end = time.time() + wait_s
        shot = 0
        while time.time() < t_end:
            time.sleep(min(30, max(1, t_end - time.time())))
            shot += 1
            self.screenshot(f"B_progress_{shot:02d}")
        lines = self.logcat_text().splitlines()[mark:]
        ev_lines = [l for l in lines if "WazeomaticEvent" in l]
        (self.out / "wazeomatic_events.txt").write_text("\n".join(ev_lines), encoding="utf-8")
        det_types = set()
        for l in ev_lines:
            det_types.update(re.findall(r"\b(HAZARD_[A-Z_]+|POLICE|CRASH)\b", l))
        macro = self.pull_jsonl("macro_events.jsonl")
        reports = self.pull_jsonl("reports.jsonl")
        self.save_jsonl("_stage_b")
        received = [m for m in macro if m.get("hazard_type") in EXPECTED]
        self.log(f"detector event lines: {len(ev_lines)} types={sorted(det_types)}; mock received {len(received)}; "
                 f"reports {len(reports)}")
        if not ev_lines:
            self.skip("B2 detector emitted events", "no WazeomaticEvent lines (nothing detected or tag differs)")
            return
        self.check("B2 detector emitted WazeomaticEvent", True, f"{len(ev_lines)} line(s)")
        self.check("B3 mock Waze received the detector broadcasts", len(received) > 0,
                   f"{len(received)} received, outcomes={sorted({m.get('outcome') for m in received})}")
        sends = [r for r in reports if r.get("action") == "send" and r.get("via") == "auto_macro"]
        mismatched = [r for r in sends if (r.get("category"), r.get("subcategory")) !=
                      EXPECTED.get(r.get("hazard_type", ""), ("?", "?"))]
        self.check("B4 sent reports match the hazard_type mapping", len(sends) > 0 and not mismatched,
                   f"{len(sends)} send(s), {len(mismatched)} mismatched")
        unknown = [r for r in sends if r.get("hazard_type") not in det_types]
        self.check("B5 every report traces back to a detector event", not unknown, f"{len(unknown)} untraced")
        limit_ok = len(sends) <= int(wait_s / 60) + 1
        self.check("B6 one-per-minute limit held", limit_ok, f"{len(sends)} sends in {wait_s} s")

    # ------------------------------------------------------------ summary
    def summary(self) -> int:
        fails = [r for r in self.results if r[0] == "FAIL"]
        lines = ["", "=" * 64, "E2E SUMMARY  " + ("PASS" if not fails else "FAIL"), "=" * 64]
        for status, name, detail in self.results:
            lines.append(f"{status:5} {name}")
        lines.append(f"artifacts: {self.out}")
        for l in lines:
            print(l)
        (self.out / "summary.txt").write_text("\n".join(self.log_lines + lines) + "\n", encoding="utf-8")
        return 1 if fails else 0


def check_schema() -> None:
    """Fail fast if schema.py changed and EXPECTED here is stale."""
    sys.path.insert(0, str(REPO))
    try:
        from wazeomatic.schema import HAZARD_TYPES, INTENT_ACTION  # type: ignore
    except Exception as e:  # pragma: no cover - schema optional for the runner
        print(f"(could not import wazeomatic.schema: {e}; using built-in table)")
        return
    assert INTENT_ACTION == HAZARD_ACTION, INTENT_ACTION
    for k, h in HAZARD_TYPES.items():
        if EXPECTED.get(k) != (h.waze_category, h.waze_subcategory):
            raise SystemExit(f"run_e2e.py EXPECTED is out of date for {k}: schema says "
                             f"{(h.waze_category, h.waze_subcategory)}")


def ensure_device(serial: str, start: bool) -> bool:
    out = subprocess.run([ADB, "devices"], capture_output=True, text=True).stdout
    if re.search(rf"^{re.escape(serial)}\s+device$", out, re.M):
        return True
    if not start:
        return False
    port = serial.split("-")[-1]
    print(f"{serial} not running, starting it...")
    p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(HERE / "start_emulator.ps1"), "-Port", port, "-Wait"], text=True)
    return p.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=_USAGE, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serial", default="emulator-5554")
    ap.add_argument("--stage", choices=["a", "b", "all"], default="all")
    ap.add_argument("--install", action="store_true", help="reinstall mock Waze even if present")
    ap.add_argument("--no-start", action="store_true", help="do not start the emulator if it is not running")
    ap.add_argument("--video", default=r"F:\dev\datasets\dashcam\dashcam_urban_1080p.ogv")
    ap.add_argument("--video-seconds", type=int, default=0, help="trim the clip (0 = whole 120 s)")
    ap.add_argument("--stage-b-wait", type=int, default=150, help="seconds to let the detector run")
    ap.add_argument("--detector-extras", default="",
                    help="extra `am start` arguments for the detector in Stage B, e.g. "
                         "\"--es class_map car=HAZARD_ON_ROAD_OBJECT --es conf 0.4\" to force events on a hazard-free clip")
    args = ap.parse_args()

    check_schema()
    out = REPO / "runs" / "emulator" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    if not ensure_device(args.serial, not args.no_start):
        print(f"FAIL: {args.serial} is not online")
        return 2
    r = Runner(args.serial, out)
    r.detector_extras = args.detector_extras
    r.start_logcat()
    try:
        if not r.ensure_installed(args.install):
            r.check("mock Waze installed", False)
            return r.summary()
        if args.stage in ("a", "all"):
            r.stage_a()
        if args.stage in ("b", "all"):
            r.stage_b(args.video, args.video_seconds, args.stage_b_wait)
    except Exception as e:  # keep the summary even if something blows up
        r.check("runner crashed", False, repr(e))
    finally:
        r.stop_logcat()
    return r.summary()


if __name__ == "__main__":
    sys.exit(main())
