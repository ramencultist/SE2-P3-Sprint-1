/*
Module Name: ReportLog.kt
Description: Logs every report and macro outcome so tests can verify them

Class Name: ReportLog
Description: Writes reports.jsonl and macro_events.jsonl to the app's external files directory and logcat

Inputs: Report and macro outcomes from MainActivity and MacroEngine
Outputs: JSONL log files pulled by the emulator tests
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the outcome logs the end-to-end test reads
How you validated and revised the AI output: Proofreading, and the emulator suite (tools/emulator/run_e2e.py Stage A) checking send once, the 60 s rate limit, dry-run cancel and unknown hazard types on the Pixel 9 Pro AVD
The challenges or limitations you faced while using AI: None notable
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.mockwaze

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.io.File

/**
 * reports.jsonl      - one line per report that reached the confirm sheet (send or cancel)
 * macro_events.jsonl - one line per HAZARD_DETECTED broadcast and what the auto macro did with it
 * Both live in /sdcard/Android/data/com.wazeomatic.mockwaze/files/ so adb pull can read them.
 */
object ReportLog {
    const val TAG = "MockWazeReport"
    const val MACRO_TAG = "MockWazeMacro"

    fun reportsFile(ctx: Context) = File(ctx.getExternalFilesDir(null), "reports.jsonl")
    fun macroFile(ctx: Context) = File(ctx.getExternalFilesDir(null), "macro_events.jsonl")

    private val lock = Any()

    private fun append(file: File, obj: JSONObject) {
        synchronized(lock) {
            try {
                file.parentFile?.mkdirs()
                file.appendText(obj.toString() + "\n")
            } catch (e: Exception) {
                Log.e(TAG, "could not write ${file.path}", e)
            }
        }
    }

    fun now(): Double = System.currentTimeMillis() / 1000.0

    /**
     * action is "send" (dry_run=false) or "cancel" (dry_run=true, nothing was reported).
     * trigger says what ended the confirm sheet: "button" (Report/Cancel tapped), "timeout"
     * (Waze-style auto send after auto_send_ms) or "close" (X / back / scrim).
     */
    fun report(ctx: Context, path: MenuPath, action: String, via: String, job: MacroJob?, trigger: String = "button") {
        val obj = JSONObject()
            .put("category", path.category)
            .put("subcategory", path.subcategory)
            .put("ts", now())
            .put("dry_run", action != "send")
            .put("via", via)
            .put("action", action)
            .put("trigger", trigger)
        if (job != null) {
            obj.put("hazard_type", job.hazardType)
                .put("confidence", job.confidence)
                .put("source", job.source)
                .put("event_ts", job.eventTs)
        }
        append(reportsFile(ctx), obj)
        Log.i(TAG, obj.toString())
    }

    fun macro(ctx: Context, outcome: String, job: MacroJob?, rawHazardType: String? = null, detail: String = "") {
        val obj = JSONObject()
            .put("ts", now())
            .put("outcome", outcome)
            .put("hazard_type", job?.hazardType ?: rawHazardType ?: "")
        if (job != null) {
            obj.put("category", job.path.category)
                .put("subcategory", job.path.subcategory)
                .put("dry_run", job.dryRun)
                .put("confidence", job.confidence)
                .put("source", job.source)
                .put("event_ts", job.eventTs)
        }
        if (detail.isNotEmpty()) obj.put("detail", detail)
        append(macroFile(ctx), obj)
        Log.i(MACRO_TAG, obj.toString())
    }

    fun clear(ctx: Context) {
        synchronized(lock) {
            reportsFile(ctx).delete()
            macroFile(ctx).delete()
        }
        Log.i(TAG, "reports cleared")
    }
}
