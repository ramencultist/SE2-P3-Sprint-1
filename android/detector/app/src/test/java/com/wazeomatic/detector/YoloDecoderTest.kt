/*
Module Name: YoloDecoderTest.kt
Description: JVM unit tests for YoloDecoder.kt

Class Name: YoloDecoderTest
Description: Tests letterbox geometry, output decoding, NMS and box mapping

Inputs: Synthetic model outputs
Outputs: JUnit pass or fail results
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to port the Python decoder tests to JUnit
How you validated and revised the AI output: Proofreading, and running gradlew test
The challenges or limitations you faced while using AI: Matching float tolerances between Kotlin and Python
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.detector

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class YoloDecoderTest {
    private val labels = listOf("pothole", "cone", "dog")

    @Test
    fun letterboxLandscape720pInto640() {
        val lb = Letterbox.compute(1280, 720, 640, 640)
        assertEquals(0.5f, lb.scale, 1e-6f)
        assertEquals(640, lb.newW)
        assertEquals(360, lb.newH)
        assertEquals(0f, lb.padX, 0f)
        assertEquals(140f, lb.padY, 0f)
    }

    @Test
    fun letterboxUsesRoundHalfEven() {
        // 333 * (416/1000) = 138.528 -> 139; 1000 * 0.416 = 416
        val lb = Letterbox.compute(1000, 333, 416, 416)
        assertEquals(416, lb.newW)
        assertEquals(139, lb.newH)
        assertEquals(138f, lb.padY, 0f) // integer floor division like python //
    }

    /** rows: cx, cy, w, h, score per class. Builds a channels-first [4+nc, N] tensor. */
    private fun channelsFirst(rowsIn: List<FloatArray>): FloatArray {
        // real models have N (anchors) >> 4+nc, pad with empty anchors so the layout check sees that
        val rows = rowsIn + List(N_TOTAL - rowsIn.size) { FloatArray(rowsIn[0].size) }
        val ch = rows[0].size
        val n = rows.size
        val out = FloatArray(ch * n)
        for (i in 0 until n) for (c in 0 until ch) out[c * n + i] = rows[i][c]
        return out
    }

    private val rows = listOf(
        // A: pothole, strong
        floatArrayOf(320f, 320f, 100f, 50f, 0.90f, 0.01f, 0.0f),
        // B: pothole, overlaps A heavily -> suppressed
        floatArrayOf(325f, 322f, 100f, 50f, 0.80f, 0.02f, 0.0f),
        // C: cone at the same spot as A -> different class, survives per-class NMS
        floatArrayOf(322f, 320f, 100f, 50f, 0.10f, 0.70f, 0.0f),
        // D: dog below threshold
        floatArrayOf(100f, 200f, 40f, 40f, 0.0f, 0.0f, 0.20f),
        // E: dog, separate box
        floatArrayOf(500f, 400f, 60f, 80f, 0.0f, 0.05f, 0.60f),
        // F: tiny box inside the top padding only -> clipped to < 1 px, dropped
        floatArrayOf(300f, 50f, 20f, 10f, 0.95f, 0.0f, 0.0f),
    )

    private fun checkDecoded(dets: List<Detection>) {
        assertEquals(listOf("pothole", "cone", "dog"), dets.map { it.label })
        val p = dets[0]
        assertEquals(0.90f, p.confidence, 1e-6f)
        // 640x640 input, 1280x720 source: scale 0.5, pad y 140
        // A: x 270..370 -> 540..740 px, y 295..345 -> 310..410 px
        assertEquals(640f / 1280f, p.bbox.cx, 1e-5f)
        assertEquals(360f / 720f, p.bbox.cy, 1e-5f)
        assertEquals(200f / 1280f, p.bbox.w, 1e-5f)
        assertEquals(100f / 720f, p.bbox.h, 1e-5f)
        val dog = dets[2]
        assertEquals(1000f / 1280f, dog.bbox.cx, 1e-5f)
        assertEquals(520f / 720f, dog.bbox.cy, 1e-5f)
        assertTrue(dets.all { it.model == "test" })
    }

    @Test
    fun decodeChannelsFirstWithPerClassNms() {
        val lb = Letterbox.compute(1280, 720, 640, 640)
        val raw = channelsFirst(rows)
        val dets = YoloDecoder.decode(raw, 7, N_TOTAL, lb, labels, 0.35f, 0.5f, "test")
        checkDecoded(dets)
    }

    @Test
    fun decodeRowMajorLayout() {
        val lb = Letterbox.compute(1280, 720, 640, 640)
        // [N, 4+nc]; N must be large enough that the layout check picks row-major
        val padded = rows + List(10) { FloatArray(7) }
        val raw = padded.flatMap { it.toList() }.toFloatArray()
        val dets = YoloDecoder.decode(raw, padded.size, 7, lb, labels, 0.35f, 0.5f, "test")
        checkDecoded(dets)
    }

    @Test
    fun classAgnosticNmsDropsOverlappingOtherClass() {
        val lb = Letterbox.compute(1280, 720, 640, 640)
        val dets = YoloDecoder.decode(channelsFirst(rows), 7, N_TOTAL, lb, labels, 0.35f, 0.5f, "test", classAgnostic = true)
        assertEquals(listOf("pothole", "dog"), dets.map { it.label })
    }

    @Test
    fun nothingAboveThreshold() {
        val lb = Letterbox.compute(640, 480, 320, 320)
        val dets = YoloDecoder.decode(channelsFirst(rows), 7, N_TOTAL, lb, labels, 0.99f, 0.5f)
        assertTrue(dets.isEmpty())
    }

    companion object {
        const val N_TOTAL = 16
    }

    @Test
    fun nmsKeepsBestAndNonOverlapping() {
        val boxes = floatArrayOf(
            0f, 0f, 10f, 10f,
            1f, 1f, 11f, 11f,
            20f, 20f, 30f, 30f,
        )
        val keep = YoloDecoder.nms(boxes, floatArrayOf(0.5f, 0.9f, 0.3f), 0.5f)
        assertEquals(listOf(1, 2), keep)
    }

    @Test
    fun parsesUltralyticsNames() {
        val names = YoloDecoder.parseNamesMetadata("{0: 'person', 1: 'bicycle', 2: \"traffic light\"}")
        assertEquals(listOf("person", "bicycle", "traffic light"), names)
    }
}
