/*
Module Name: Reporter.kt
Description: Hands confirmed hazards to the reporting macro

Class Name: Reporter
Description: Sends one HAZARD_DETECTED broadcast per HazardEvent and logs it to logcat and files/events.jsonl

Inputs: HazardEvents from the Pipeline or the TCP server
Outputs: Broadcast Intents for the macro and a JSONL event log
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the broadcast that passes each detection to the reporting macro
How you validated and revised the AI output: Proofreading, the JVM unit tests (gradlew test), and emulator Stage B: detector app in video mode sending broadcasts that mock Waze turns into reports with the matching hazard type
The challenges or limitations you faced while using AI: Making sure the broadcast reaches the macro app even when it is stopped, with an optional target package
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.detector

import android.content.Context
import android.content.Intent
import android.util.Log
import java.io.File

/**
 * Sends one broadcast per HazardEvent for the MacroDroid macro, and records it in logcat
 * (tag WazeomaticEvent, one JSON line) and in files/events.jsonl.
 */
class Reporter(
    private val ctx: Context,
    private val dryRun: Boolean,
    private val targetPackage: String,
) {
    val eventsFile: File = File(ctx.getExternalFilesDir(null) ?: ctx.filesDir, "events.jsonl")

    @Synchronized
    fun report(ev: HazardEvent) {
        val intent = Intent(INTENT_ACTION).apply {
            for ((k, v) in ev.intentExtras()) putExtra(k, v)
            putExtra("dry_run", if (dryRun) "true" else "false")
            addFlags(Intent.FLAG_INCLUDE_STOPPED_PACKAGES)
            if (targetPackage.isNotBlank()) setPackage(targetPackage.trim())
        }
        try {
            ctx.sendBroadcast(intent)
        } catch (e: Exception) {
            Log.e(TAG, "broadcast failed", e)
        }
        val json = ev.toJson()
        Log.i(EVENT_TAG, json)
        try {
            eventsFile.appendText(json + "\n")
        } catch (e: Exception) {
            Log.e(TAG, "could not append to $eventsFile", e)
        }
        PipelineBus.postEvent(ev, dryRun)
    }

    companion object {
        const val EVENT_TAG = "WazeomaticEvent"
        private const val TAG = "Wazeomatic"
    }
}
