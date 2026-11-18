/*
Module Name: BoxFusion.kt
Description: Kotlin port of wazeomatic/fusion.py: weighted boxes fusion for the on-device ensemble and zoom pass

Class Name: BoxFusion
Description: WBF and NMS fusion plus per-class floor parsing

Inputs: Detections from several models or passes
Outputs: Fused detections
External Sources: Claude Fable 5.1
Attributions:

Creation 11/6/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to port fusion.py so the phone fuses boxes exactly like the desktop
How you validated and revised the AI output: Proofreading, and BoxFusionTest.kt mirroring tests/test_fusion.py
The challenges or limitations you faced while using AI: Keeping the Kotlin and Python versions identical
Authors: Blake Pennel
Creation Date: 11/6/2026

Ported to the SE2-P3 repository 11/17/2026 by Luke Reicherter with Claude Opus 5.5 for Sprint 2 (requirements 7 and 9); code unchanged from the team's wazeomatic development repository
*/

package com.wazeomatic.detector

import kotlin.math.max
import kotlin.math.min

// Port of wazeomatic/fusion.py: Weighted Boxes Fusion across several models (or passes) and
// per-label confidence floors. Pure Kotlin so it can be unit tested on the JVM.

object BoxFusion {

    /** How the fused confidence is formed; see fuse_wbf in fusion.py. */
    enum class ConfType { AVG, MAX, BOX_AVG }

    private class Member(val wconf: Float, val conf: Float, val box: FloatArray, val model: Int)

    private class Cluster(val label: String, first: Member) {
        val members = ArrayList<Member>().apply { add(first) }
        var fused: FloatArray = first.box.copyOf()

        fun add(m: Member) {
            members.add(m)
            var ws = 0f
            val acc = FloatArray(4)
            for (x in members) {
                ws += x.wconf
                for (k in 0 until 4) acc[k] += x.box[k] * x.wconf
            }
            for (k in 0 until 4) acc[k] /= ws
            fused = acc
        }
    }

    private fun xyxy(b: BBox) = floatArrayOf(b.cx - b.w / 2, b.cy - b.h / 2, b.cx + b.w / 2, b.cy + b.h / 2)

    private fun iou(a: FloatArray, b: FloatArray): Float {
        val iw = max(0f, min(a[2], b[2]) - max(a[0], b[0]))
        val ih = max(0f, min(a[3], b[3]) - max(a[1], b[1]))
        val inter = iw * ih
        val ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
        return if (ua > 0f) inter / ua else 0f
    }

    /**
     * Weighted Boxes Fusion over the detections of [lists].size models. Boxes of the same label
     * that overlap by at least [iouThr] are averaged (weighted by confidence x model weight);
     * with [ConfType.AVG] the fused confidence is the weighted mean over all models, so a box
     * only one model saw is scored down. Result sorted by confidence, best first.
     */
    fun fuseWbf(
        lists: List<List<Detection>>,
        weights: FloatArray? = null,
        iouThr: Float = 0.55f,
        skipThr: Float = 0f,
        confType: ConfType = ConfType.AVG,
        modelName: String = "ensemble",
    ): List<Detection> {
        val n = lists.size
        if (n == 0) return emptyList()
        val w = weights ?: FloatArray(n) { 1f }
        require(w.size == n) { "one weight per model expected" }
        val wSum = w.sum()
        val all = ArrayList<Pair<String, Member>>()
        for ((mi, lst) in lists.withIndex()) {
            for (d in lst) {
                if (d.confidence < skipThr) continue
                all.add(d.label to Member(d.confidence * w[mi], d.confidence, xyxy(d.bbox), mi))
            }
        }
        if (all.isEmpty()) return emptyList()
        all.sortByDescending { it.second.wconf }
        val clusters = ArrayList<Cluster>()
        for ((label, m) in all) {
            var best: Cluster? = null
            var bestIou = iouThr
            for (c in clusters) {
                if (c.label != label) continue
                val v = iou(c.fused, m.box)
                if (v >= bestIou) { best = c; bestIou = v }
            }
            if (best == null) clusters.add(Cluster(label, m)) else best.add(m)
        }
        val out = ArrayList<Detection>(clusters.size)
        for (c in clusters) {
            val conf = when (confType) {
                ConfType.MAX -> c.members.maxOf { it.conf }
                ConfType.BOX_AVG -> c.members.map { it.conf }.average().toFloat()
                ConfType.AVG -> {
                    val perModel = HashMap<Int, Float>()
                    for (m in c.members) perModel[m.model] = max(perModel[m.model] ?: 0f, m.wconf)
                    perModel.values.sum() / wSum
                }
            }
            val f = c.fused
            val x1 = f[0].coerceIn(0f, 1f); val y1 = f[1].coerceIn(0f, 1f)
            val x2 = f[2].coerceIn(0f, 1f); val y2 = f[3].coerceIn(0f, 1f)
            out.add(Detection(c.label, min(conf, 1f), BBox((x1 + x2) / 2, (y1 + y2) / 2, max(x2 - x1, 0f), max(y2 - y1, 0f)), modelName))
        }
        out.sortByDescending { it.confidence }
        return out
    }

    /** Plain per-class NMS across lists: the best box of each cluster survives unchanged. */
    fun fuseNms(lists: List<List<Detection>>, iouThr: Float = 0.5f): List<Detection> =
        ZoomCrop.merge(lists.flatten(), iouThr)

    /** Drop detections below a per-label floor; labels not listed use [default]. */
    fun applyClassFloors(dets: List<Detection>, floors: Map<String, Float>, default: Float = 0f): List<Detection> {
        if (floors.isEmpty() && default <= 0f) return dets
        return dets.filter { it.confidence >= (floors[it.label] ?: default) }
    }

    /** "pothole=0.5,cone=0.4" -> map; malformed parts are skipped. */
    fun parseFloors(s: String?): Map<String, Float> {
        if (s.isNullOrBlank()) return emptyMap()
        val out = LinkedHashMap<String, Float>()
        for (part in s.split(',', ';')) {
            val kv = part.split('=', ':').map { it.trim() }
            if (kv.size == 2 && kv[0].isNotEmpty()) kv[1].toFloatOrNull()?.let { out[kv[0]] = it }
        }
        return out
    }

    fun parseWeights(s: String?, n: Int): FloatArray? {
        if (s.isNullOrBlank()) return null
        val v = s.split(',').mapNotNull { it.trim().toFloatOrNull() }
        return if (v.size == n) v.toFloatArray() else null
    }
}
