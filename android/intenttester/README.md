Module Name: MacroDroid intent tester  
Description: Standalone Android app for testing Traffic, Police, and Hazard reports without subcategories.

Inputs: Report category selection, Send and Cancel taps, Android lifecycle events, system time, and a configured MacroDroid receiver.  
Outputs: Dry-run broadcast string extras, countdown and delivery status, sender logs, and a buildable Android APK.  
External Sources: ChatGPT 6.1 Sol  
Attributions: Android SDK and Gradle build tooling. Gradle launcher scripts retain their original Apache License 2.0 notices.

Creation 11/10/2026 - Drew and ChatGPT:  
A description of how and why AI was used: AI assisted with creating the tester to category-only Traffic, Police, and Hazard reports, and updating the setup documentation.

How you validated and revised the AI output: Built the APK, installed it on a device. Used the different categories of reports and verified that it worked with the macro on Android.

The challenges or limitations you faced while using AI: Aligning the intent of AI generated content with the purpose of the project and desired categories of Police, Traffic, and Hazard. Attempted to generate other categories/subcategories. 
Authors: Drew Medlock  
Creation Date: 11/10/2026

# MacroDroid intent tester

Standalone Android app for testing the real MacroDroid macro without actual inference. Uses the broadcast action and string-extra format from
the original Wazeomatic integration contract (the intent format is documented below), with category-only
Traffic, Police, and Hazard reports for your installed Waze menu.

## Build and install

Requires JDK 17+ and Android SDK platform 35. Set `ANDROID_HOME` to your SDK,
or put `sdk.dir` in a local `local.properties` file.

From this directory in PowerShell:

```powershell
.\gradlew.bat assembleDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

You can also open this directory as a project in Android Studio, then Run on
your connected phone.

## Use

1. Configure MacroDroid's Intent Received trigger with action
   `com.wazeomatic.HAZARD_DETECTED`.
2. Open this app and select Traffic, Police, or Hazard.
3. Tap Send dry-run intent in 10 seconds.
4. Observe the macro opening Waze, pressing the report button, then asking the user to submit. Return to the tester
   to see its sent status. This status confirms only that `sendBroadcast` ran.


Tap Cancel
to cancel a pending test while the app is open.

Broadcast action: `com.wazeomatic.HAZARD_DETECTED`. All extras are strings:
`hazard_type`, `confidence=0.900`, `source=sim`, `ts` (Unix seconds),
`waze_category`, `waze_subcategory=""` (empty string), `dry_run=true`.

| Selection | `hazard_type` | `waze_category` |
|-----------|---------------|-----------------|
| Traffic | `TRAFFIC` | `Traffic` |
| Police | `POLICE` | `Police` |
| Hazard | `HAZARD` | `Hazard` |

In MacroDroid, the macro branches on these waze_categories.  The tester always sends an empty
`waze_subcategory`, in case Waze changes to add different catgories in their 
more advanced menu for experienced users.

To inspect sender logs: `adb logcat -s WazeomaticIntentTester`.