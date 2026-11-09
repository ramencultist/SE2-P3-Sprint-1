/*
Module Name: MacroEngine.kt
Description: In-app copy of the MacroDroid macro logic used to test the reporting flow

Class Name: MacroJob
Description: One accepted HAZARD_DETECTED event and the Waze menu path it maps to

Class Name: MacroEngine
Description: Maps the hazard type to a menu path, ignores and logs unknown types, allows one report per minute and runs the taps only when the app is in front

Inputs: HAZARD_DETECTED events from AutoMacroService
Outputs: Report taps in MainActivity and a log entry for every outcome
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to reproduce the macro rules in code so they can be tested automatically
How you validated and revised the AI output: Proofreading, and the emulator suite (tools/emulator/run_e2e.py Stage A) checking send once, the 60 s rate limit, dry-run cancel and unknown hazard types on the Pixel 9 Pro AVD
The challenges or limitations you faced while using AI: Matching the timing and behaviour of the real MacroDroid macro
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.mockwaze

import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.os.SystemClock

/** One HAZARD_DETECTED event that the auto macro accepted. */
data class MacroJob(
    val path: MenuPath,
    val dryRun: Boolean,
    val hazardType: String,
    val confidence: String,
    val source: String,
    val eventTs: String,
)

/**
 * In-app copy of the MacroDroid macro logic (see android_setup/macro_setup_guide.md):
 *  1. map hazard_type -> Waze menu path
 *  2. only one real report per minute (rate_limit_ms)
 *  3. only tap when Waze is in the foreground
 *  4. tap report button, wait, tap category, wait, tap sub item, wait, tap Send
 *  5. dry_run=true: go all the way to the confirm sheet, wait, tap Cancel instead
 * The taps themselves are done by MainActivity.runMacro (real touch events at view centres).
 * Everything runs on the main thread.
 */
object MacroEngine {
    const val ACTION_HAZARD = "com.wazeomatic.HAZARD_DETECTED"

    private val main = Handler(Looper.getMainLooper())

    /** The activity while it is resumed (i.e. "Waze is in the foreground"). */
    var foreground: MainActivity? = null
        private set

    private var lastSendElapsed = -1L
    private var busy = false
    private var pending: MacroJob? = null
    private var pendingTimeout: Runnable? = null

    fun resetRateLimit() {
        lastSendElapsed = -1L
    }

    fun onHazardIntent(ctx: Context, intent: Intent) {
        val app = ctx.applicationContext
        val ex = intent.extras
        val hazardType = ex?.getString("hazard_type")
        val path = WazeMenu.pathFor(hazardType, ex?.getString("waze_category"), ex?.getString("waze_subcategory"))
        if (path == null) {
            ReportLog.macro(app, "unknown_hazard_type", null, hazardType)
            return
        }
        @Suppress("DEPRECATION")
        val job = MacroJob(
            path = path,
            dryRun = Config.parseBool(ex?.get("dry_run")) ?: false,
            hazardType = hazardType ?: "",
            confidence = ex?.get("confidence")?.toString() ?: "",
            source = ex?.getString("source") ?: "",
            eventTs = ex?.get("ts")?.toString() ?: "",
        )
        if (!Config.bool(app, "auto_macro")) {
            ReportLog.macro(app, "ignored_auto_macro_off", job)
            return
        }
        if (busy) {
            ReportLog.macro(app, "skipped_busy", job)
            return
        }
        // Dry runs never reach Waze, so they neither count against nor are blocked by the limit.
        val now = SystemClock.elapsedRealtime()
        val limit = Config.int(app, "rate_limit_ms")
        if (!job.dryRun && lastSendElapsed >= 0 && now - lastSendElapsed < limit) {
            ReportLog.macro(app, "suppressed_rate_limit", job,
                detail = "next allowed in ${limit - (now - lastSendElapsed)} ms")
            return
        }
        val act = foreground
        if (act == null && Config.bool(app, "require_foreground")) {
            if (Config.bool(app, "bring_to_front")) {
                bringToFront(app, job)
            } else {
                ReportLog.macro(app, "skipped_not_foreground", job)
            }
            return
        }
        if (act == null) {
            // require_foreground=false: open the app and run as soon as it is resumed.
            bringToFront(app, job)
            return
        }
        start(app, act, job)
    }

    private fun start(app: Context, act: MainActivity, job: MacroJob) {
        busy = true
        if (!job.dryRun) lastSendElapsed = SystemClock.elapsedRealtime()
        ReportLog.macro(app, "accepted", job)
        act.runMacro(job)
    }

    private fun bringToFront(app: Context, job: MacroJob) {
        busy = true
        pending = job
        ReportLog.macro(app, "bringing_to_front", job)
        // Starting an activity from the background needs the "display over other apps"
        // app-op on Android 10+ (run_e2e.py grants it with appops).
        try {
            app.startActivity(Intent(app, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
        } catch (e: Exception) {
            pending = null
            busy = false
            ReportLog.macro(app, "skipped_not_foreground", job, detail = "startActivity failed: ${e.message}")
            return
        }
        val r = Runnable {
            if (pending === job) {
                pending = null
                busy = false
                ReportLog.macro(app, "skipped_not_foreground", job, detail = "activity did not come to front in 5 s")
            }
        }
        pendingTimeout = r
        main.postDelayed(r, 5000)
    }

    fun onActivityResumed(act: MainActivity) {
        foreground = act
        val job = pending ?: return
        pending = null
        pendingTimeout?.let { main.removeCallbacks(it) }
        busy = false
        start(act.applicationContext, act, job)
    }

    fun onActivityPaused(act: MainActivity) {
        if (foreground === act) foreground = null
    }

    fun finished(ctx: Context, job: MacroJob, outcome: String, detail: String = "") {
        busy = false
        ReportLog.macro(ctx.applicationContext, outcome, job, detail = detail)
    }
}
