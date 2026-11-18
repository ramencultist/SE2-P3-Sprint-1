/*
Module Name: BoxFusionTest.kt
Description: JVM unit tests for BoxFusion.kt

Class Name: BoxFusionTest
Description: Fusion and floor parsing tests

Inputs: Synthetic detections
Outputs: JUnit results
External Sources: Claude Fable 5.1
Attributions:

Creation 11/6/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to mirror tests/test_fusion.py
How you validated and revised the AI output: Running gradlew testDebugUnitTest
The challenges or limitations you faced while using AI: Keeping the Kotlin and Python versions identical
Authors: Blake Pennel
Creation Date: 11/6/2026

Ported to the SE2-P3 repository 11/17/2026 by Luke Reicherter with Claude Opus 5.5 for Sprint 2 (requirements 7 and 9); code unchanged from the team's wazeomatic development repository
*/

package com.wazeomatic.detector

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BoxFusionTest {
    private fun det(label: String, conf: Float, cx: Float, cy: Float, w: Float = 0.1f, h: Float = 0.1f, model: String = "m") =
        Detection(label, conf, BBox(cx, cy, w, h), model)

    @Test
    fun averagesOverlappingBoxesAndRewardsAgreement() {
        val a = listOf(det("pothole", 0.9f, 0.50f, 0.50f))
        val b = listOf(det("pothole", 0.7f, 0.52f, 0.50f))
        val f = BoxFusion.fuseWbf(listOf(a, b))
        assertEquals(1, f.size)
        assertTrue(f[0].bbox.cx > 0.505f && f[0].bbox.cx < 0.512f)
        assertEquals(0.8f, f[0].confidence, 1e-5f)
        assertEquals("ensemble", f[0].model)
    }

    @Test
    fun penalisesBoxesSeenByOneModelOnly() {
        val a = listOf(det("cone", 0.8f, 0.2f, 0.2f))
        val f = BoxFusion.fuseWbf(listOf(a, emptyList()))
        assertEquals(0.4f, f[0].confidence, 1e-5f)
        assertEquals(0.8f, BoxFusion.fuseWbf(listOf(a, emptyList()), confType = BoxFusion.ConfType.MAX)[0].confidence, 1e-5f)
        assertEquals(0.8f, BoxFusion.fuseWbf(listOf(a, emptyList()), confType = BoxFusion.ConfType.BOX_AVG)[0].confidence, 1e-5f)
    }

    @Test
    fun respectsWeightsAndBestBoxPerModel() {
        val a = listOf(det("cone", 0.6f, 0.2f, 0.2f))
        assertEquals(0.45f, BoxFusion.fuseWbf(listOf(a, emptyList()), floatArrayOf(3f, 1f))[0].confidence, 1e-5f)
        val dup = listOf(det("cone", 0.8f, 0.5f, 0.5f), det("cone", 0.6f, 0.505f, 0.5f))
        val f = BoxFusion.fuseWbf(listOf(dup, emptyList()))
        assertEquals(1, f.size)
        assertEquals(0.4f, f[0].confidence, 1e-5f)
    }

    @Test
    fun keepsLabelsAndFarBoxesApart() {
        val a = listOf(det("pothole", 0.9f, 0.5f, 0.5f), det("cone", 0.9f, 0.5f, 0.5f))
        val b = listOf(det("pothole", 0.9f, 0.9f, 0.9f))
        val f = BoxFusion.fuseWbf(listOf(a, b))
        assertEquals(3, f.size)
    }

    @Test
    fun nmsFusionKeepsBestUnchanged() {
        val a = listOf(det("cone", 0.9f, 0.50f, 0.50f, model = "a"))
        val b = listOf(det("cone", 0.7f, 0.52f, 0.50f, model = "b"))
        val f = BoxFusion.fuseNms(listOf(a, b))
        assertEquals(1, f.size)
        assertEquals("a", f[0].model)
    }

    @Test
    fun floorsAndParsers() {
        val dets = listOf(det("pothole", 0.5f, 0.5f, 0.5f), det("cone", 0.5f, 0.5f, 0.5f), det("delineator", 0.3f, 0.5f, 0.5f))
        val kept = BoxFusion.applyClassFloors(dets, mapOf("pothole" to 0.6f, "cone" to 0.4f), 0.35f)
        assertEquals(listOf("cone"), kept.map { it.label })
        assertEquals(mapOf("pothole" to 0.6f, "cone" to 0.4f), BoxFusion.parseFloors("pothole=0.6, cone=0.4"))
        assertTrue(BoxFusion.parseFloors(null).isEmpty())
        assertEquals(null, BoxFusion.parseWeights("1,2", 3))
        assertEquals(2, BoxFusion.parseWeights("1,2", 2)!!.size)
    }
}
