/*
Module Name: SettingsActivity.kt
Description: Settings screen for the mock Waze timings and auto macro switches

Class Name: SettingsActivity
Description: Edits the same timing keys as the SET_TIMING broadcast and can reset the rate limit

Inputs: User input
Outputs: Updated Config values
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write a manual settings screen for the mock app
How you validated and revised the AI output: Proofreading, and manual checks on the emulator
The challenges or limitations you faced while using AI: None notable
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.mockwaze

import android.app.Activity
import android.graphics.Color
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.WindowInsets
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast

/** Timings and auto macro switches. Same keys as the SET_TIMING broadcast / launch extras. */
class SettingsActivity : Activity() {

    private val intFields = LinkedHashMap<String, EditText>()
    private val boolFields = LinkedHashMap<String, Switch>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(16), dp(20), dp(24))
            setBackgroundColor(Color.WHITE)
        }
        col.addView(TextView(this).apply {
            text = "Mock Waze settings"
            textSize = 22f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(0xFF111827.toInt())
            setPadding(0, 0, 0, dp(12))
        })

        for (key in Config.BOOL_DEFAULTS.keys) {
            val sw = Switch(this).apply {
                text = key
                textSize = 16f
                isChecked = Config.bool(this@SettingsActivity, key)
                setPadding(0, dp(6), 0, dp(6))
            }
            boolFields[key] = sw
            col.addView(sw)
        }
        for (key in Config.INT_DEFAULTS.keys) {
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            row.addView(TextView(this).apply {
                text = key
                textSize = 16f
                setTextColor(0xFF111827.toInt())
            }, LinearLayout.LayoutParams(0, -2, 1f))
            val et = EditText(this).apply {
                inputType = InputType.TYPE_CLASS_NUMBER
                setText(Config.int(this@SettingsActivity, key).toString())
                gravity = Gravity.END
                contentDescription = key
            }
            intFields[key] = et
            row.addView(et, LinearLayout.LayoutParams(dp(120), -2))
            col.addView(row)
        }

        col.addView(Button(this).apply {
            text = "Save"
            setOnClickListener { save() }
        })
        col.addView(Button(this).apply {
            text = "Clear reports"
            setOnClickListener {
                ReportLog.clear(this@SettingsActivity)
                MacroEngine.resetRateLimit()
                Toast.makeText(this@SettingsActivity, "Reports cleared", Toast.LENGTH_SHORT).show()
            }
        })
        col.addView(TextView(this).apply {
            textSize = 12f
            setTextColor(0xFF4B5563.toInt())
            setPadding(0, dp(16), 0, 0)
            text = "Reports: ${ReportLog.reportsFile(this@SettingsActivity).path}\n" +
                "Macro log: ${ReportLog.macroFile(this@SettingsActivity).path}\n\n" +
                "From adb:\nam broadcast -a ${Config.ACTION_SET_TIMING} -p $packageName --ei sheet_ms 300 --ez auto_macro true"
        })

        val scroll = ScrollView(this).apply {
            setBackgroundColor(Color.WHITE)
            addView(col)
        }
        scroll.setOnApplyWindowInsetsListener { v, insets ->
            if (Build.VERSION.SDK_INT >= 30) {
                val i = insets.getInsets(WindowInsets.Type.systemBars())
                v.setPadding(0, i.top, 0, i.bottom)
            }
            insets
        }
        setContentView(scroll)
    }

    private fun save() {
        for ((k, et) in intFields) {
            et.text.toString().trim().toIntOrNull()?.takeIf { it >= 0 }?.let { Config.setInt(this, k, it) }
        }
        for ((k, sw) in boolFields) Config.setBool(this, k, sw.isChecked)
        AutoMacroService.sync(this)
        Toast.makeText(this, "Saved", Toast.LENGTH_SHORT).show()
        finish()
    }
}
