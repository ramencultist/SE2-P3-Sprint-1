# Emulator test suite

Scripts to run the Wazeomatic Android pieces on an emulator set up like the Pixel 9 Pro
(1280x2856, 495 dpi) against **mock Waze** (`android/mockwaze`) instead of real Waze.
Everything stays on F: (SDK, AVDs under `F:\dev\android-sdk\.android\avd`, outputs under
`runs\emulator\`).

Our emulator is AVD `pixel9pro_test` on port **5554** (`emulator-5554`). Every script only uses
that serial, so the detector team's `wazeomatic_dev` on 5560 is never touched.

## Quick start (PowerShell, repo root)

```powershell
$ps = 'powershell -NoProfile -ExecutionPolicy Bypass -File'
iex "$ps tools\emulator\create_avd.ps1"            # once; -Webcam for back cam = webcam0, -Force to recreate
iex "$ps tools\emulator\start_emulator.ps1 -Wait"  # -Headless (no window), -Gpu auto|host|swiftshader_indirect, -ColdBoot
iex "$ps tools\emulator\install_apps.ps1 -Build"   # mock Waze + detector APK if built
.venv\Scripts\python.exe tools\emulator\run_e2e.py  # Stage A (+ Stage B if the detector exists)
```

| script | what it does |
|---|---|
| `create_avd.ps1` | creates `pixel9pro_test` from the `pixel_9_pro` profile (falls back to the closest Pixel) and forces 1280x2856 @ 495 dpi, 4 GB emulator RAM, 6 cores, back camera `virtualscene` (or `webcam0` with `-Webcam`) in `config.ini` |
| `start_emulator.ps1` | starts it in the background on 5554 (`-gpu auto`), log in `runs\emulator\emulator_5554.log` |
| `wait_for_boot.ps1` | polls `sys.boot_completed`, then unlocks and keeps the screen on |
| `install_apps.ps1` | installs mock Waze and `android/detector/app/build/outputs/apk/debug/*.apk` if present |
| `push_video.ps1` | ffmpeg: `.ogv` -> H.264 720p 30 fps mp4 in `runs\emulator\`, pushed to `/sdcard/Android/data/com.wazeomatic.detector/files/test.mp4` and `/sdcard/Download/` (`-Seconds 60` to trim) |
| `screenshot.ps1` / `screenrecord.ps1` | PNG / mp4 into `runs\emulator\` |
| `run_e2e.py` | the end-to-end test (below) |

## run_e2e.py

```powershell
.venv\Scripts\python.exe tools\emulator\run_e2e.py --stage a        # mock Waze only
.venv\Scripts\python.exe tools\emulator\run_e2e.py --stage b --stage-b-wait 150
.venv\Scripts\python.exe tools\emulator\run_e2e.py --stage b --video-seconds 60 --stage-b-wait 100 --detector-extras "--es class_map car=HAZARD_ON_ROAD_OBJECT --es conf 0.4 --ez dry_run false"
# (the CC0 clips contain no real hazard; the class_map extra makes the detector treat COCO cars as a hazard so the whole chain is exercised)
```

If `adb install` of the detector fails with `INSTALL_FAILED_UPDATE_INCOMPATIBLE`, the APK already on the emulator was signed with a different debug keystore (another PC or user). Run `adb -s emulator-5554 uninstall com.wazeomatic.detector` once and install again.

Starts the emulator if it is not running (unless `--no-start`), installs mock Waze if missing
(`--install` forces it), and writes `logcat.txt`, screenshots, the pulled `reports.jsonl` /
`macro_events.jsonl` and `summary.txt` to `runs\emulator\<timestamp>\`. Exit code 0 = all PASS.

**Stage A** (mock Waze only):
* A1 taps Report > Hazard > Pothole > Cancel with `adb shell input tap` at the coordinates
  from `uiautomator dump` (same kind of taps the MacroDroid macro does) and expects a
  `dry_run=true` row; a manual Police send grabs the "Thanks!" toast screenshot.
* A2 broadcasts `HAZARD_ON_ROAD_POTHOLE` (`dry_run=false`) and expects exactly one
  `Hazard / Pothole` report from the auto macro.
* A3 broadcasts `CRASH` straight after: must be `suppressed_rate_limit` and nothing sent.
* A4 broadcasts `HAZARD_ON_ROAD_CONSTRUCTION` with `dry_run=true`: must reach the confirm
  sheet, cancel, and log `dry_run=true` with no extra send.
* A5 unknown hazard_type is ignored; A6 `MockWazeReport` lines show up in logcat.

The broadcasts look exactly like what the detector sends:

```
adb -s emulator-5554 shell am broadcast -a com.wazeomatic.HAZARD_DETECTED --es hazard_type HAZARD_ON_ROAD_POTHOLE --es confidence 0.87 --es dry_run false --es source e2e --es waze_category Hazard --es waze_subcategory Pothole
```

**Stage B** (skipped when no detector APK is built or installed): pushes the clip, starts mock
Waze with `bring_to_front=true` (+ `appops set ... SYSTEM_ALERT_WINDOW allow` so it may come to
the front from the background), launches
`am start -n com.wazeomatic.detector/.MainActivity --es source video --es video test.mp4`,
waits, reads logcat tag `WazeomaticEvent` and checks mock Waze received those events, sent
reports match the schema mapping and the one-per-minute limit held. If the detector logs no
events at all the stage is reported as SKIP (nothing to compare), not PASS.

Note: once mock Waze jumps to the front, the detector goes to the background. If the detector
pauses video playback when it is not visible, run it as a foreground service (its owner's call).

## Camera path instead of video mode (webcam0 + OBS)

The detector's in-app video mode is the easy way. To test the real camera code path instead:

1. Install OBS Studio, add a *Media Source* with `F:\dev\datasets\dashcam\dashcam_urban_1080p.ogv`
   (loop on), and click **Start Virtual Camera**.
2. Recreate or start the AVD with the webcam as back camera:
   `create_avd.ps1 -Webcam -Force` (permanent) or `start_emulator.ps1 -Webcam` (this run only;
   passes `-camera-back webcam0`). Check the name with
   `F:\dev\android-sdk\emulator\emulator.exe -webcam-list`; if OBS is not `webcam0`, use the
   name it shows (e.g. `webcam1`) in `config.ini` (`hw.camera.back`).
3. Start the detector in camera mode; the emulator back camera now shows the OBS clip.

The emulator only enumerates webcams at start-up, so start the OBS virtual camera first.
The default `virtualscene` camera is a 3D room, useful only to check the camera opens.

## Troubleshooting

* `adb devices` shows `emulator-5554 offline`: wait, or `adb -s emulator-5554 emu kill` and start again with `-ColdBoot`.
* Stuck at boot with `-gpu auto`: try `-Gpu swiftshader_indirect` (software, slower).
* `uiautomator dump` fails while an animation runs: A1 retries a few times.
* Reports file: `adb -s emulator-5554 shell cat /sdcard/Android/data/com.wazeomatic.mockwaze/files/reports.jsonl`.

## Real Waze on the emulator (pixel9pro_play)

`pixel9pro_test` uses the plain Google APIs image, which has no Play Store, so the real Waze
app cannot be installed on it. A second profile, `pixel9pro_play`, uses the Google Play image:

```powershell
powershell -ExecutionPolicy Bypass -File tools\emulator\create_avd.ps1 -Name pixel9pro_play -Image "system-images;android-35;google_apis_playstore;x86_64"
powershell -ExecutionPolicy Bypass -File tools\emulator\start_emulator.ps1 -Avd pixel9pro_play -Port 5556 -Wait
```

Then, in the emulator window, open the Play Store, sign in with a Google account and install
Waze. Nothing in this repo automates that step on purpose (it is a personal account).

**Known limit (checked 2026-11-05, Waze 5.26.5, API 35 Play image):** Waze installs and starts,
but both "Get started" and "Log in" request a Play Integrity token first and the emulator is not
a certified device, so the verdict has no device integrity and Waze refuses to continue
("Something went wrong. Try again later." on the guest path, a network error on the login path).
The emulator's network itself is fine (ping, DNS and the Play Store all work), so there is nothing
to fix on that side, and we do not try to work around the integrity check. Use the Play emulator
for look-and-feel comparisons of the installed app's resources only; real-app dry runs and
reference screenshots of the report sheets have to come from a real phone:

```powershell
adb -s <phone serial> exec-out screencap -p > runs\waze_ref\hazard_sheet.png
```

Once Waze runs somewhere (real phone):

* The real app is the reference for the mock: compare screenshots of the report sheets and
  adjust `WazeMenu.kt` / `Ui.kt` if a label, tile order or position differs.
* Macro rehearsals against the real app stay in dry-run: tap through to the confirm sheet and
  Cancel. Never let a test tap Report or wait out the auto-send countdown in real Waze, as that
  publishes a report to other drivers. Automated full sends stay on mock Waze (`pixel9pro_test`).
* The two emulators can run side by side (serials `emulator-5554` and `emulator-5556`); pass
  `--serial emulator-5556` to `run_e2e.py --stage b` to replay a clip into the detector app on
  the Play device while Waze is in front.
