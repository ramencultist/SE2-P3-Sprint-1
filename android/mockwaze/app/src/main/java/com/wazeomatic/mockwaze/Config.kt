/*
Module Name: Config.kt
Description: Tunable timings for the mock Waze app and its macro

Class Name: Config
Description: Reads and writes timing settings (including the 60 s rate limit) from preferences, launch extras and broadcasts

Inputs: Settings screen values, launch extras and SET_TIMING broadcasts
Outputs: Timing values used by MacroEngine and MainActivity
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to make the macro timings adjustable from the test scripts
How you validated and revised the AI output: Proofreading, and the emulator suite (tools/emulator/run_e2e.py Stage A) checking send once, the 60 s rate limit, dry-run cancel and unknown hazard types on the Pixel 9 Pro AVD
The challenges or limitations you faced while using AI: None notable
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.mockwaze

import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * All the tunable timings. Set them from the Settings screen, with launch extras
 * (am start ... --ei sheet_ms 300) or with
 * am broadcast -a com.wazeomatic.mockwaze.SET_TIMING --ei sheet_ms 300 ...
 */
object Config {
    const val ACTION_SET_TIMING = "com.wazeomatic.mockwaze.SET_TIMING"
    private const val PREFS = "mockwaze"
    private const val TAG = "MockWazeConfig"

    val INT_DEFAULTS = linkedMapOf(
        "sheet_ms" to 300,             // bottom sheet slide up/down
        "page_ms" to 200,              // fade between sheet pages (grid -> hazard -> confirm)
        "toast_ms" to 2500,            // how long "Thanks!" stays up
        "auto_send_ms" to 5000,        // confirm sheet sends by itself after this (0 = never), like Waze
        "macro_start_delay_ms" to 500, // auto macro: delay between event and first tap
        "macro_wait_ms" to 1000,       // auto macro: wait after every tap (macro "Wait" action)
        "dry_run_hold_ms" to 1500,     // auto macro: how long the confirm sheet stays before Cancel
        "rate_limit_ms" to 60000,      // auto macro: one real report per this many ms
    )

    val BOOL_DEFAULTS = linkedMapOf(
        "auto_macro" to false,         // listen for com.wazeomatic.HAZARD_DETECTED
        "require_foreground" to true,  // like the macro: only tap when Waze is on screen
        "bring_to_front" to false,     // test helper: pull mock Waze to the front first
    )

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun int(ctx: Context, key: String): Int = prefs(ctx).getInt(key, INT_DEFAULTS[key] ?: 0)
    fun bool(ctx: Context, key: String): Boolean = prefs(ctx).getBoolean(key, BOOL_DEFAULTS[key] ?: false)

    fun setInt(ctx: Context, key: String, v: Int) = prefs(ctx).edit().putInt(key, v).commit()
    fun setBool(ctx: Context, key: String, v: Boolean) = prefs(ctx).edit().putBoolean(key, v).commit()

    fun dump(ctx: Context): String =
        (INT_DEFAULTS.keys.map { "$it=${int(ctx, it)}" } + BOOL_DEFAULTS.keys.map { "$it=${bool(ctx, it)}" })
            .joinToString(" ")

    /**
     * Apply config keys found in intent extras. Accepts ints/booleans or their string forms
     * (so --ei, --ez and --es all work). Also handles the one-shot actions
     * reset_rate_limit and clear_reports. Returns true if anything was applied.
     */
    @Suppress("DEPRECATION")
    fun applyExtras(ctx: Context, intent: Intent?): Boolean {
        val ex = intent?.extras ?: return false
        var changed = false
        for (k in INT_DEFAULTS.keys) {
            if (!ex.containsKey(k)) continue
            val v = when (val raw = ex.get(k)) {
                is Int -> raw
                is Long -> raw.toInt()
                is String -> raw.trim().toIntOrNull()
                else -> null
            }
            if (v != null && v >= 0) { setInt(ctx, k, v); changed = true }
        }
        for (k in BOOL_DEFAULTS.keys) {
            if (!ex.containsKey(k)) continue
            parseBool(ex.get(k))?.let { setBool(ctx, k, it); changed = true }
        }
        if (parseBool(ex.get("reset_rate_limit")) == true) { MacroEngine.resetRateLimit(); changed = true }
        if (parseBool(ex.get("clear_reports")) == true) { ReportLog.clear(ctx); changed = true }
        if (changed) Log.i(TAG, "config: ${dump(ctx)}")
        return changed
    }

    fun parseBool(raw: Any?): Boolean? = when (raw) {
        is Boolean -> raw
        is String -> when (raw.trim().lowercase()) {
            "true", "1", "yes" -> true
            "false", "0", "no" -> false
            else -> null
        }
        is Int -> raw != 0
        else -> null
    }
}
