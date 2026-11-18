#!/usr/bin/env bash
# Module Name: start_mock_waze_mac.sh
# Description: macOS/Linux setup for the Sprint 2 demo without a phone: boots an Android emulator, builds and installs
#   mock Waze, and starts it with its built-in auto macro so a HAZARD_DETECTED broadcast from the CV pipeline is
#   tapped through to Hazard > <subtype> on screen (stopping before Report, as a dry run)
#
# Inputs: An Android Studio AVD (first one listed, or AVD=<name>), the Android SDK (ANDROID_HOME or
#   ~/Library/Android/sdk), and a JDK 17-21 in JAVA_HOME for the first build
# Outputs: A running emulator with mock Waze in front and auto_macro on
# External Sources: Claude Opus 5.5
# Attributions: Mirrors the Windows PowerShell scripts in this folder and the not-responding dialog handling in run_e2e.py
#
# Creation 11/17/2026 - Luke Reicherter and Claude Opus 5.5:
# A description of how and why AI was used: Claude Opus 5.5 used to write a one-command Mac setup for the emulator demo,
#   since the existing emulator scripts are PowerShell and expect a Windows SDK path
# How you validated and revised the AI output: Ran it on a Mac (M2, 8 GB) with the Medium_Phone_API_37.0 AVD and checked
#   that tools/demo_sprint2.py --ping gives a dry_run_cancelled Hazard > Construction in mock Waze
# The challenges or limitations you faced while using AI: On 8 GB the emulator's System UI can hang after boot, and its
#   "isn't responding" dialog swallows the macro's taps, so the script restarts System UI; software rendering made it worse.
#   Turning off animator animations also breaks mock Waze (its auto-send countdown fires at once), so they are left on
# Authors: Luke Reicherter
# Creation Date: 11/17/2026
set -euo pipefail

SDK="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
ADB="$SDK/platform-tools/adb"
EMU="$SDK/emulator/emulator"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APK="$ROOT/android/mockwaze/app/build/outputs/apk/debug/app-debug.apk"
AVD="${AVD:-$("$EMU" -list-avds | head -1)}"

if [ ! -f "$APK" ]; then
  echo "Building mock Waze (needs JAVA_HOME pointing at JDK 17-21)..."
  echo "sdk.dir=$SDK" > "$ROOT/android/mockwaze/local.properties"
  (cd "$ROOT/android/mockwaze" && bash ./gradlew -q assembleDebug)
fi

if ! "$ADB" devices | grep -q "emulator-.*device$"; then
  echo "Starting emulator $AVD..."
  nohup "$EMU" -avd "$AVD" -gpu host -no-snapshot -no-boot-anim -no-audio -memory 2048 >/tmp/wazeomatic-emulator.log 2>&1 &
fi
echo -n "Waiting for boot"
until [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do echo -n .; sleep 3; done
echo " booted. Letting it settle..."
sleep 30

# "System UI isn't responding" dialogs swallow taps: restart whatever is not responding
for _ in 1 2 3; do
  focus="$("$ADB" shell dumpsys window | grep mCurrentFocus || true)"
  case "$focus" in
    *"Not Responding: "*)
      pkg="$(echo "$focus" | sed -n 's/.*Not Responding: \([a-zA-Z0-9_.]*\).*/\1/p')"
      echo "Restarting $pkg (not responding)"; "$ADB" shell am force-stop "$pkg"; sleep 10 ;;
    *) break ;;
  esac
done

"$ADB" install -r "$APK" >/dev/null
"$ADB" shell appops set com.wazeomatic.mockwaze SYSTEM_ALERT_WINDOW allow
"$ADB" shell pm grant com.wazeomatic.mockwaze android.permission.POST_NOTIFICATIONS 2>/dev/null || true
"$ADB" shell am start -n com.wazeomatic.mockwaze/.MainActivity \
  --ez auto_macro true --ez bring_to_front true --ez clear_reports true --ez reset_rate_limit true >/dev/null
sleep 5   # let the auto macro service register its receiver before the first broadcast
echo "Mock Waze is running with the auto macro on."
echo "Test it:   python tools/demo_sprint2.py --ping"
echo "Demo:      python tools/demo_sprint2.py 0 --camera -- --no-auto-hood"
echo "Reports:   $ADB logcat -s MockWazeMacro MockWazeReport"
