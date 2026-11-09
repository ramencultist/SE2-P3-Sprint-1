"""
Module Name: fetch_test_videos.py
Description: Downloads the public-domain dashcam clips used as pre-recorded test input and converts them to MP4

Inputs: Clip URLs on Wikimedia Commons; ffmpeg on PATH
Outputs: MP4 test videos in assets/videos/
External Sources: Claude Fable 5.1, CC0 dashcam clips on Wikimedia Commons
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the download and conversion script for the test clips
How you validated and revised the AI output: Proofreading, and running it to produce the clips used by the simulator and the emulator
The challenges or limitations you faced while using AI: Android cannot play the source .ogv files, so the clips have to be converted with ffmpeg
Authors: Blake Pennel
Creation Date: 11/5/2026
"""
from __future__ import annotations

"""Download the public-domain dashcam clips used by the simulator and convert to MP4.

Both clips are CC0 on Wikimedia Commons. Output goes to assets/videos/ (git-ignored).
Requires ffmpeg on PATH (or set FFMPEG env var).
"""

import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "videos"

CLIPS = {
    "dashcam_urban_1080p": (
        "https://upload.wikimedia.org/wikipedia/commons/e/ea/Dashcam_Recording_%28urban%29.ogv",
        "https://commons.wikimedia.org/wiki/File:Dashcam_Recording_(urban).ogv",
    ),
    "dashcam_daylight_720p": (
        "https://upload.wikimedia.org/wikipedia/commons/e/eb/Dashcam_recording_example_-_Panorama_II_BlackBox_Dashcam_daylight.mp4.ogv",
        "https://commons.wikimedia.org/wiki/File:Dashcam_recording_example_-_Panorama_II_BlackBox_Dashcam_daylight.mp4.ogv",
    ),
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ffmpeg = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    for name, (url, page) in CLIPS.items():
        ogv = OUT / f"{name}.ogv"
        mp4 = OUT / f"{name}.mp4"
        if not ogv.exists() and not mp4.exists():
            print(f"downloading {name} from {page}")
            urllib.request.urlretrieve(url, ogv)
        if not mp4.exists():
            if not ffmpeg:
                print("ffmpeg not found; keeping .ogv (OpenCV can read it, Android cannot)", file=sys.stderr)
                continue
            print(f"converting {ogv.name} -> {mp4.name} (h264, 720p, 30 fps)")
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(ogv), "-vf", "scale=-2:720",
                            "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "23",
                            "-an", str(mp4)], check=True)
    (OUT / "LICENSES.txt").write_text(
        "".join(f"{n}: CC0 1.0, source {p}\n" for n, (_, p) in CLIPS.items()), encoding="utf-8")
    print("done:", ", ".join(p.name for p in sorted(OUT.glob('*.mp4'))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
