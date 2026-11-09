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