# Setup guide for the Android Macro on Waze
***
## Installation:
***
The program used to write the macros is [MacroDroid](https://macrodroid.com/) downloaded from the Google Playstore.

The macro has 3 distinct parts
* The logic that checks which report to make
* The actions that interact with the UI elements
* The variable that needs updated with the report type

To setup the variable, a local variable named ```hazard_type``` is declared at the bottom of the app inside the macro.

For actions on the device, they are classified as device interactions that click at the location of the report button, after checking its location.
Waits are also inserted to allow for dynamic element movements.

The conditional logic checks which UI element to press based on the ```hazard_type``` variable. It also constrains the user to only
 1 report per minute to prevent triggering Waze timeouts. It also checks that the foreground focus is on the Waze app before making
any clicks.
## Usage:
***
The Macro is performed automatically when it is called from the CV pipeline.


***
Authored by Drew Medlock

Created: 11/3/2026

Last Edited: 11/8/2026

## Sprint 2: subtypes and retries
***
*Added 11/17/2026 by Luke Reicherter with Claude Opus 5.5. These are the changes the macro needs for requirements 7 and 9.
The macro owners still need to build them in MacroDroid.*

The CV pipeline sends the same `com.wazeomatic.HAZARD_DETECTED` broadcast as the intent tester. The only difference is
that `waze_subcategory` is filled in:

| `hazard_type` | `waze_category` | `waze_subcategory` |
|---------------|-----------------|--------------------|
| `HAZARD_ON_ROAD_CONSTRUCTION` | `Hazard` | `Construction` |
| `HAZARD_ON_ROAD_POTHOLE` | `Hazard` | `Pothole` |
| `HAZARD_ON_ROAD_OBJECT` | `Hazard` | `Object` |

The existing macro branches on `waze_category`, so it already opens the Hazard menu for these events. To reach the subtype:

1. Copy the `waze_subcategory` extra into a local variable, the same way `hazard_type` is copied.
2. After the Hazard tap and its wait, add an If clause for `waze_subcategory` not empty. Inside it, use a
   UI Interaction click on the element whose text equals `waze_subcategory`, then the usual wait.
3. **Retry (criterion 9.6).** If the subtype element is not found, go back and repeat the Hazard tap and the
   subtype click up to 2 more times.
4. If it still fails, show a notification ("Waze report failed: Construction") and write the failure to the macro log.
   Do not tap anything else.
5. Keep honoring `dry_run`: when it is `true`, stop before the final Report button.

Test it with `python tools/demo_sprint2.py --ping` (one Construction broadcast), then with a clip or the live camera.
