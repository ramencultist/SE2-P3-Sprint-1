/*
Module Name: Schema.kt
Description: Kotlin mirror of wazeomatic/schema.py: hazard to Waze menu mapping and the event JSON

Class Name: HazardType
Description: One reportable hazard and the Waze category and subcategory it maps to

Class Name: Schema
Description: The hazard type table and the default model class to hazard mapping

Class Name: BBox
Description: Normalized center x, y, width and height of a box

Class Name: Detection
Description: One model detection: label, confidence, box and model name

Class Name: HazardEvent
Description: A confirmed hazard with type, confidence, source and timestamp, serialized to JSON and Intent extras

Inputs: Model labels and JSON lines from the TCP server
Outputs: Hazard events and their JSON and Intent extras
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to mirror the Python schema so both pipelines send the same message
How you validated and revised the AI output: Proofreading, ReportManagerTest.kt JSON checks, and the schema check in tools/emulator/run_e2e.py
The challenges or limitations you faced while using AI: Keeping hazard_type strings identical to the Python side and the macro
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.detector

import org.json.JSONObject
import java.math.BigDecimal
import java.util.Locale

// Kotlin mirror of wazeomatic/schema.py. hazard_type strings are the contract with
// the MacroDroid macro, keep them identical to the Python side.

const val SCHEMA_VERSION = 1
const val INTENT_ACTION = "com.wazeomatic.HAZARD_DETECTED"

data class HazardType(
    val key: String,
    val wazeCategory: String,
    val wazeSubcategory: String,
    val description: String,
)

object Schema {
    val HAZARD_TYPES: Map<String, HazardType> = listOf(
        HazardType("HAZARD_ON_ROAD_POTHOLE", "Hazard", "Pothole", "Pothole / road damage"),
        HazardType("HAZARD_ON_ROAD_CONSTRUCTION", "Hazard", "Construction", "Cones, work-zone signs, delineators"),
        HazardType("HAZARD_ON_ROAD_OBJECT", "Hazard", "Object", "Debris or object on the road"),
        HazardType("HAZARD_SHOULDER_CAR_STOPPED", "Hazard", "Car on shoulder", "Vehicle stopped on the shoulder"),
        HazardType("HAZARD_SHOULDER_ANIMALS", "Hazard", "", "Animal on or near the road (plain Hazard; Waze removed the Animals subtype)"),
        HazardType("POLICE", "Police", "", "Police vehicle visible"),
        HazardType("CRASH", "Crash", "", "Crash / collision"),
    ).associateBy { it.key }

    val DEFAULT_CLASS_TO_HAZARD: Map<String, String> = linkedMapOf(
        // custom hazard model
        "pothole" to "HAZARD_ON_ROAD_POTHOLE",
        "cone" to "HAZARD_ON_ROAD_CONSTRUCTION",
        "construction_sign" to "HAZARD_ON_ROAD_CONSTRUCTION",
        "delineator" to "HAZARD_ON_ROAD_CONSTRUCTION",
        "barricade" to "HAZARD_ON_ROAD_CONSTRUCTION",
        "worker" to "HAZARD_ON_ROAD_CONSTRUCTION",
        "police_vehicle" to "POLICE",
        // COCO classes used from the stock detector
        "dog" to "HAZARD_SHOULDER_ANIMALS",
        "horse" to "HAZARD_SHOULDER_ANIMALS",
        "sheep" to "HAZARD_SHOULDER_ANIMALS",
        "cow" to "HAZARD_SHOULDER_ANIMALS",
        "bear" to "HAZARD_SHOULDER_ANIMALS",
        // car/truck/bus only become a hazard through the stopped-vehicle heuristic
    )
}

/** Normalized center xywh in the source frame. */
data class BBox(val cx: Float, val cy: Float, val w: Float, val h: Float)

data class Detection(
    val label: String,
    val confidence: Float,
    val bbox: BBox,
    val model: String = "hazard",
)

fun nowSeconds(): Double = System.currentTimeMillis() / 1000.0

data class HazardEvent(
    val hazardType: String,
    val confidence: Double,
    val source: String = "phone",
    val ts: Double = nowSeconds(),
    val frameIndex: Int? = null,
    val bbox: BBox? = null,
    val label: String? = null,
    val lat: Double? = null,
    val lon: Double? = null,
    val schema: Int = SCHEMA_VERSION,
) {
    val waze: HazardType get() = Schema.HAZARD_TYPES.getValue(hazardType)

    /** Same keys and order as HazardEvent.to_json() in schema.py. */
    fun toJson(): String {
        val sb = StringBuilder("{")
        fun key(k: String) {
            if (sb.length > 1) sb.append(',')
            sb.append(quote(k)).append(':')
        }
        key("hazard_type"); sb.append(quote(hazardType))
        key("confidence"); sb.append(num(confidence))
        key("source"); sb.append(quote(source))
        key("ts"); sb.append(num(ts))
        key("frame_index"); sb.append(frameIndex?.toString() ?: "null")
        key("bbox")
        if (bbox == null) {
            sb.append("null")
        } else {
            sb.append('[')
            sb.append(listOf(bbox.cx, bbox.cy, bbox.w, bbox.h).joinToString(",") { num(it.toString().toDouble()) })
            sb.append(']')
        }
        key("label"); sb.append(label?.let { quote(it) } ?: "null")
        key("lat"); sb.append(lat?.let { num(it) } ?: "null")
        key("lon"); sb.append(lon?.let { num(it) } ?: "null")
        key("schema"); sb.append(schema)
        key("waze_category"); sb.append(quote(waze.wazeCategory))
        key("waze_subcategory"); sb.append(quote(waze.wazeSubcategory))
        return sb.append('}').toString()
    }

    /** String extras for the broadcast (MacroDroid reads strings). */
    fun intentExtras(): Map<String, String> = linkedMapOf(
        "hazard_type" to hazardType,
        "confidence" to String.format(Locale.US, "%.3f", confidence),
        "ts" to String.format(Locale.US, "%.3f", ts),
        "source" to source,
        "waze_category" to waze.wazeCategory,
        "waze_subcategory" to waze.wazeSubcategory,
    )

    companion object {
        /** Parses one JSON line as produced by schema.py (extra keys are ignored). */
        fun fromJson(line: String, defaultSource: String = "pi"): HazardEvent {
            val o = JSONObject(line)
            val type = o.getString("hazard_type")
            require(type in Schema.HAZARD_TYPES) { "unknown hazard_type $type" }
            fun has(k: String) = o.has(k) && !o.isNull(k)
            val bbox = if (has("bbox")) {
                val a = o.getJSONArray("bbox")
                BBox(a.getDouble(0).toFloat(), a.getDouble(1).toFloat(), a.getDouble(2).toFloat(), a.getDouble(3).toFloat())
            } else null
            return HazardEvent(
                hazardType = type,
                confidence = if (has("confidence")) o.getDouble("confidence") else 1.0,
                source = if (has("source")) o.getString("source") else defaultSource,
                ts = if (has("ts")) o.getDouble("ts") else nowSeconds(),
                frameIndex = if (has("frame_index")) o.getInt("frame_index") else null,
                bbox = bbox,
                label = if (has("label")) o.getString("label") else null,
                lat = if (has("lat")) o.getDouble("lat") else null,
                lon = if (has("lon")) o.getDouble("lon") else null,
                schema = if (has("schema")) o.getInt("schema") else SCHEMA_VERSION,
            )
        }

        private fun num(d: Double): String =
            if (d.isNaN() || d.isInfinite()) "null" else BigDecimal(d.toString()).toPlainString()

        private fun quote(s: String): String {
            val sb = StringBuilder("\"")
            for (c in s) {
                when {
                    c == '"' -> sb.append("\\\"")
                    c == '\\' -> sb.append("\\\\")
                    c == '\n' -> sb.append("\\n")
                    c == '\r' -> sb.append("\\r")
                    c == '\t' -> sb.append("\\t")
                    c < ' ' -> sb.append(String.format(Locale.US, "\\u%04x", c.code))
                    else -> sb.append(c)
                }
            }
            return sb.append('"').toString()
        }
    }
}
