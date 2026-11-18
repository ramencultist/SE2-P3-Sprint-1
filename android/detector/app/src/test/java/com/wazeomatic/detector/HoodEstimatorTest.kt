/*
Module Name: HoodEstimatorTest.kt
Description: JVM unit tests for HoodEstimator.kt

Class Name: HoodEstimatorTest
Description: Static band and moving scene tests

Inputs: Synthetic grey frames
Outputs: JUnit results
External Sources: Claude Fable 5.1
Attributions:

Creation 11/6/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to mirror tests/test_hood.py
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
import kotlin.random.Random

class HoodEstimatorTest {
    private val w = 160
    private val h = 90

    /** Scrolling noise texture (road) with an optional static band at the bottom. */
    private fun frames(n: Int, dashFrac: Float, still: Boolean = false): Sequence<FloatArray> {
        val rnd = Random(0)
        val texture = FloatArray(h * 3 * w) { rnd.nextInt(0, 255).toFloat() }
        val dash = FloatArray(h * w) { rnd.nextInt(60, 120).toFloat() }
        val cut = (h * (1 - dashFrac)).toInt()
        return (0 until n).asSequence().map { i ->
            val off = if (still) 0 else (i * 7) % (h * 2)
            val img = texture.copyOfRange(off * w, (off + h) * w)
            if (dashFrac > 0f) for (y in cut until h) for (x in 0 until w) img[y * w + x] = dash[y * w + x]
            img
        }
    }

    @Test
    fun findsStaticDashboardBand() {
        val est = HoodEstimator()
        var frac = 0f
        for (f in frames(60, 0.25f)) frac = est.update(f)
        assertTrue("got $frac", frac in 0.20f..0.30f)
    }

    @Test
    fun clampsToMinimumAndMaximum() {
        val est = HoodEstimator()
        var frac = 0f
        for (f in frames(60, 0f)) frac = est.update(f)
        assertEquals(0.10f, frac, 1e-6f)
        val est2 = HoodEstimator(maxFrac = 0.30f)
        for (f in frames(60, 0.4f)) frac = est2.update(f)
        assertEquals(0.30f, frac, 1e-6f)
    }

    @Test
    fun waitsForMinFramesAndIgnoresStandstill() {
        val est = HoodEstimator()
        for (f in frames(20, 0.25f)) assertEquals(0.10f, est.update(f), 1e-6f)
        val est2 = HoodEstimator()
        var frac = 0f
        for (f in frames(60, 0.25f, still = true)) frac = est2.update(f)
        assertEquals(0.10f, frac, 1e-6f)
    }
}
