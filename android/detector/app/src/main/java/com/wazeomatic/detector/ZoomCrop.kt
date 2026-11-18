/*
Module Name: ZoomCrop.kt
Description: Kotlin port of the zoom pass: a second inference on a crop of the road ahead to find small distant cones and potholes

Class Name: CropRect
Description: Pixel rectangle of the crop in a frame

Class Name: ZoomCrop
Description: Crop fractions and the mapping of crop boxes back to the full frame

Inputs: Frames and the crop setting
Outputs: Crop rectangles and remapped detections
External Sources: Claude Fable 5.1
Attributions:

Creation 11/6/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to port the ZoomDetector mapping from detector.py
How you validated and revised the AI output: Proofreading, and ZoomCropTest.kt mirroring tests/test_zoom_detector.py
The challenges or limitations you faced while using AI: Keeping the Kotlin and Python versions identical
Authors: Blake Pennel
Creation Date: 11/6/2026

Ported to the SE2-P3 repository 11/17/2026 by Luke Reicherter with Claude Opus 5.5 for Sprint 2 (requirements 7 and 9); code unchanged from the team's wazeomatic development repository
*/

package com.wazeomatic.detector

import java.util.Locale
import kotlin.math.roundToInt

// Port of ZoomDetector / merge_detections in wazeomatic/detector.py. Pure Kotlin so the
// coordinate mapping, the merge and the setting parser can be unit tested on the JVM.

/** Crop rectangle in pixels, [x1, x2) x [y1, y2). */
data class CropRect(val x1: Int, val y1: Int, val x2: Int, val y2: Int) {
    val width: Int get() = x2 - x1
    val height: Int get() = y2 - y1
}

/**
 * Second-pass region as normalized frame fractions. With a 1280x720 frame and a 640 px model
 * the full pass sees the scene at half resolution; the default crop (middle 50 % of the width,
 * 35-75 % of the height) is 640x288 px, so the second pass sees the road ahead at full
 * resolution. Costs one extra inference per model and frame.
 */
data class ZoomCrop(val x1: Float, val y1: Float, val x2: Float, val y2: Float) {

    /** Pixel rectangle for a frame, or null when the crop would be smaller than [MIN_PX]. */
    fun pixelRect(frameW: Int, frameH: Int): CropRect? {
        val r = CropRect(
            (x1 * frameW).roundToInt(), (y1 * frameH).roundToInt(),
            (x2 * frameW).roundToInt(), (y2 * frameH).roundToInt(),
        )
        return if (r.width < MIN_PX || r.height < MIN_PX) null else r
    }

    /** Map a detection whose bbox is normalized to the crop back to full-frame coordinates. */
    fun toFrame(d: Detection): Detection {
        val sw = x2 - x1
        val sh = y2 - y1
        val b = d.bbox
        return d.copy(bbox = BBox(x1 + b.cx * sw, y1 + b.cy * sh, b.w * sw, b.h * sh))
    }

    fun toFrame(dets: List<Detection>): List<Detection> = dets.map { toFrame(it) }

    override fun toString(): String = String.format(Locale.US, "%.2f,%.2f,%.2f,%.2f", x1, y1, x2, y2)

    companion object {
        const val MIN_PX = 32
        val DEFAULT = ZoomCrop(0.25f, 0.35f, 0.75f, 0.75f)

        /**
         * Setting / launch-extra parser. "" (or false/off) -> null (disabled), "true"/"1"/"on"
         * -> [DEFAULT], "x1,y1,x2,y2" fractions -> that crop. Anything malformed -> null.
         */
        fun parse(s: String?): ZoomCrop? {
            val t = s?.trim()?.lowercase() ?: return null
            if (t.isEmpty() || t == "false" || t == "0" || t == "off" || t == "none") return null
            if (t == "true" || t == "1" || t == "on" || t == "default") return DEFAULT
            val parts = t.split(',', ';', ' ').filter { it.isNotEmpty() }
            if (parts.size != 4) return null
            val v = parts.map { it.toFloatOrNull() ?: return null }
            if (v.any { it.isNaN() || it < 0f || it > 1f }) return null
            if (v[2] <= v[0] || v[3] <= v[1]) return null
            return ZoomCrop(v[0], v[1], v[2], v[3])
        }

        /** The fractions actually covered by a rounded pixel rectangle. */
        fun fromRect(r: CropRect, frameW: Int, frameH: Int) = ZoomCrop(
            r.x1.toFloat() / frameW, r.y1.toFloat() / frameH, r.x2.toFloat() / frameW, r.y2.toFloat() / frameH,
        )

        /**
         * Per-class NMS over detections from several passes (normalized xywh boxes); duplicates
         * keep the higher confidence. Result is sorted by confidence, best first.
         */
        fun merge(dets: List<Detection>, iouThres: Float = 0.5f): List<Detection> {
            if (dets.size <= 1) return dets.toList()
            val labels = dets.map { it.label }.distinct().sorted()
            val boxes = FloatArray(dets.size * 4)
            val conf = FloatArray(dets.size)
            for ((i, d) in dets.withIndex()) {
                val off = labels.indexOf(d.label) * 4f
                val b = d.bbox
                boxes[i * 4] = b.cx - b.w / 2 + off
                boxes[i * 4 + 1] = b.cy - b.h / 2 + off
                boxes[i * 4 + 2] = b.cx + b.w / 2 + off
                boxes[i * 4 + 3] = b.cy + b.h / 2 + off
                conf[i] = d.confidence
            }
            return YoloDecoder.nms(boxes, conf, iouThres).map { dets[it] }
        }
    }
}
