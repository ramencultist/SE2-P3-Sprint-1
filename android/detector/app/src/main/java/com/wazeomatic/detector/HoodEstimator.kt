/*
Module Name: HoodEstimator.kt
Description: Kotlin port of wazeomatic/hood.py: learns the hood and dashboard band at the bottom of the camera picture (criterion 7.6)

Class Name: HoodEstimator
Description: Running average of small grey frames; returns the fraction of the frame height to ignore

Inputs: Downscaled grey frames
Outputs: The hood fraction for ReportManager.setHoodFrac
External Sources: Claude Fable 5.1
Attributions:

Creation 11/6/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to port hood.py to the phone
How you validated and revised the AI output: Proofreading, and HoodEstimatorTest.kt mirroring tests/test_hood.py
The challenges or limitations you faced while using AI: Keeping the Kotlin and Python versions identical
Authors: Blake Pennel
Creation Date: 11/6/2026

Ported to the SE2-P3 repository 11/17/2026 by Luke Reicherter with Claude Opus 5.5 for Sprint 2 (requirements 7 and 9); code unchanged from the team's wazeomatic development repository
*/

package com.wazeomatic.detector

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * Port of wazeomatic/hood.py: learns how much of the bottom of the frame is the car itself
 * (dashboard, hood, wiper rest) from persistent structure. A running average of the frames keeps
 * the dashboard's edges sharp while the road blurs away; per row band the ratio of edge energy in
 * the averaged image to the mean instantaneous edge energy is close to 1 for the car and low for
 * the scene. Bands are scanned from the bottom while the ratio is >= [persist].
 *
 * Works on a small grey image ([width] x [height] floats, row-major, top row first) so it can be
 * unit tested without Android bitmaps; [Pipeline] reduces the camera frame for it.
 */
class HoodEstimator(
    val minFrac: Float = 0.10f,
    val maxFrac: Float = 0.40f,
    val width: Int = 160,
    val height: Int = 90,
    val rows: Int = 18,
    private val alpha: Float = 0.05f,
    private val persist: Float = 0.45f,
    private val minFrames: Int = 30,
    private val minEdge: Float = 2.0f,
) {
    var hoodFrac: Float = minFrac
        private set
    var frames = 0
        private set
    private val band = height / rows
    private var ema: FloatArray? = null
    private val edgeSum = FloatArray(rows)
    private val scratch = FloatArray(rows)

    /** Feed one reduced grey frame ([width] x [height]); returns the current hood fraction. */
    fun update(grey: FloatArray): Float {
        require(grey.size == width * height) { "expected ${width}x$height grey values" }
        rowEdges(grey, scratch)
        val e = ema
        if (e == null) {
            ema = grey.copyOf()
        } else {
            for (i in e.indices) e[i] = (1 - alpha) * e[i] + alpha * grey[i]
        }
        for (r in 0 until rows) edgeSum[r] += scratch[r]
        frames++
        if (frames >= minFrames) hoodFrac = estimate()
        return hoodFrac
    }

    /** Per band, top to bottom: persistent-edge ratio (diagnostics). */
    fun ratios(): FloatArray {
        val out = FloatArray(rows)
        val e = ema ?: return out
        rowEdges(e, out)
        for (r in 0 until rows) out[r] = out[r] / (edgeSum[r] / max(frames, 1) + 1e-6f)
        return out
    }

    private fun estimate(): Float {
        var meanInst = 0f
        for (r in 0 until rows) meanInst += edgeSum[r] / max(frames, 1)
        meanInst /= rows
        if (meanInst < minEdge) return hoodFrac
        val r = ratios()
        val mid = r.copyOfRange(rows / 3, (2 * rows) / 3).sorted()
        val median = if (mid.size % 2 == 1) mid[mid.size / 2] else (mid[mid.size / 2 - 1] + mid[mid.size / 2]) / 2
        if (median >= persist) return hoodFrac
        var staticRows = 0
        for (i in rows - 1 downTo 0) {
            if (r[i] < persist) break
            staticRows++
        }
        return min(maxFrac, max(minFrac, staticRows.toFloat() / rows))
    }

    /** Mean |Sobel x| + |Sobel y| per row band (3x3 kernels, borders skipped). */
    private fun rowEdges(img: FloatArray, out: FloatArray) {
        java.util.Arrays.fill(out, 0f)
        val w = width
        for (y in 1 until height - 1) {
            var acc = 0f
            for (x in 1 until w - 1) {
                val i = y * w + x
                val gx = (img[i - w + 1] + 2 * img[i + 1] + img[i + w + 1]) - (img[i - w - 1] + 2 * img[i - 1] + img[i + w - 1])
                val gy = (img[i + w - 1] + 2 * img[i + w] + img[i + w + 1]) - (img[i - w - 1] + 2 * img[i - w] + img[i - w + 1])
                acc += abs(gx) + abs(gy)
            }
            out[min(rows - 1, y / band)] += acc / (w - 2)
        }
        for (r in 0 until rows) out[r] /= band
    }
}
