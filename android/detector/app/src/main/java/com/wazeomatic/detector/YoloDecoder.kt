/*
Module Name: YoloDecoder.kt
Description: Kotlin port of the desktop preprocessing and YOLO output decoding

Class Name: Letterbox
Description: Scale and padding used to fit a frame into the model input, kept so boxes can be mapped back

Class Name: YoloDecoder
Description: Decodes raw YOLO output into detections, applies NMS and maps boxes back to the source frame

Inputs: Raw model output arrays and the letterbox used for the frame
Outputs: Detections in normalized source-frame coordinates
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to port the Python decoding to pure Kotlin so it can be unit tested on the JVM
How you validated and revised the AI output: Proofreading, and YoloDecoderTest.kt matching the Python test cases
The challenges or limitations you faced while using AI: Keeping the port numerically identical to wazeomatic/detector.py
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.detector

import kotlin.math.max
import kotlin.math.min

// Port of the pre/post-processing in wazeomatic/detector.py. Pure Kotlin so it can be
// unit tested on the JVM.

data class Letterbox(
    val scale: Float,
    val padX: Float,
    val padY: Float,
    val inW: Int,
    val inH: Int,
    val srcW: Int,
    val srcH: Int,
    val newW: Int,
    val newH: Int,
) {
    companion object {
        /** Keep aspect ratio, center, pad the rest (pad colour 114 is applied by the caller). */
        fun compute(srcW: Int, srcH: Int, inW: Int, inH: Int): Letterbox {
            val scale = min(inH.toDouble() / srcH, inW.toDouble() / srcW)
            // python round() is round-half-even, Math.rint does the same
            val nw = Math.rint(srcW * scale).toInt()
            val nh = Math.rint(srcH * scale).toInt()
            val px = (inW - nw) / 2
            val py = (inH - nh) / 2
            return Letterbox(scale.toFloat(), px.toFloat(), py.toFloat(), inW, inH, srcW, srcH, nw, nh)
        }
    }
}

object YoloDecoder {
    const val PAD_VALUE = 114

    /** Greedy NMS. boxes is flat [n*4] xyxy. Returns kept indices, best score first. */
    fun nms(boxes: FloatArray, scores: FloatArray, iouThres: Float): List<Int> {
        val n = scores.size
        if (n == 0) return emptyList()
        val areas = FloatArray(n) { i ->
            max(boxes[i * 4 + 2] - boxes[i * 4], 0f) * max(boxes[i * 4 + 3] - boxes[i * 4 + 1], 0f)
        }
        var order = (0 until n).sortedByDescending { scores[it] }
        val keep = ArrayList<Int>()
        while (order.isNotEmpty()) {
            val i = order[0]
            keep.add(i)
            if (order.size == 1) break
            val next = ArrayList<Int>(order.size - 1)
            for (k in 1 until order.size) {
                val j = order[k]
                val xx1 = max(boxes[i * 4], boxes[j * 4])
                val yy1 = max(boxes[i * 4 + 1], boxes[j * 4 + 1])
                val xx2 = min(boxes[i * 4 + 2], boxes[j * 4 + 2])
                val yy2 = min(boxes[i * 4 + 3], boxes[j * 4 + 3])
                val inter = max(xx2 - xx1, 0f) * max(yy2 - yy1, 0f)
                val iou = inter / (areas[i] + areas[j] - inter + 1e-9f)
                if (iou <= iouThres) next.add(j)
            }
            order = next
        }
        return keep
    }

    /**
     * Decode a [1, d1, d2] YOLO output: either [1, 4+nc, N] (Ultralytics default) or
     * [1, N, 4+nc]. Boxes are xywh in letterboxed pixels. Returned bboxes are
     * normalized center-xywh in the source frame.
     */
    fun decode(
        raw: FloatArray,
        d1: Int,
        d2: Int,
        lb: Letterbox,
        labels: List<String>,
        confThres: Float,
        iouThres: Float,
        modelName: String = "hazard",
        classAgnostic: Boolean = false,
    ): List<Detection> {
        require(raw.size >= d1 * d2) { "tensor too small: ${raw.size} < $d1 x $d2" }
        val channelsFirst = d1 < d2 && d1 == 4 + labels.size
        val n = if (channelsFirst) d2 else d1
        val ch = if (channelsFirst) d1 else d2
        val nc = ch - 4
        fun v(i: Int, c: Int): Float = if (channelsFirst) raw[c * d2 + i] else raw[i * d2 + c]

        val selBoxes = ArrayList<Float>()
        val selConf = ArrayList<Float>()
        val selCls = ArrayList<Int>()
        for (i in 0 until n) {
            var best = 0
            var bestScore = v(i, 4)
            for (c in 1 until nc) {
                val s = v(i, 4 + c)
                if (s > bestScore) {
                    bestScore = s
                    best = c
                }
            }
            if (bestScore < confThres) continue
            val cx = v(i, 0)
            val cy = v(i, 1)
            val w = v(i, 2)
            val h = v(i, 3)
            selBoxes.add(cx - w / 2); selBoxes.add(cy - h / 2)
            selBoxes.add(cx + w / 2); selBoxes.add(cy + h / 2)
            selConf.add(bestScore)
            selCls.add(best)
        }
        val m = selConf.size
        if (m == 0) return emptyList()
        val xyxy = selBoxes.toFloatArray()
        // per-class NMS via coordinate offset trick
        val shifted = FloatArray(m * 4) { k ->
            val off = if (classAgnostic) 0f else selCls[k / 4] * 4096f
            xyxy[k] + off
        }
        val keep = nms(shifted, selConf.toFloatArray(), iouThres)
        val out = ArrayList<Detection>()
        val sw = lb.srcW.toFloat()
        val sh = lb.srcH.toFloat()
        for (i in keep) {
            val bx1 = ((xyxy[i * 4] - lb.padX) / lb.scale).coerceIn(0f, sw)
            val by1 = ((xyxy[i * 4 + 1] - lb.padY) / lb.scale).coerceIn(0f, sh)
            val bx2 = ((xyxy[i * 4 + 2] - lb.padX) / lb.scale).coerceIn(0f, sw)
            val by2 = ((xyxy[i * 4 + 3] - lb.padY) / lb.scale).coerceIn(0f, sh)
            val w = bx2 - bx1
            val h = by2 - by1
            if (w <= 1f || h <= 1f) continue
            val cls = selCls[i]
            out.add(
                Detection(
                    label = labels.getOrElse(cls) { "class$cls" },
                    confidence = selConf[i],
                    bbox = BBox((bx1 + w / 2) / sw, (by1 + h / 2) / sh, w / sw, h / sh),
                    model = modelName,
                )
            )
        }
        return out
    }

    /** Ultralytics stores class names in metadata as "{0: 'person', 1: 'bicycle', ...}". */
    fun parseNamesMetadata(s: String): List<String> {
        val re = Regex("""(\d+)\s*:\s*(?:'([^']*)'|"([^"]*)")""")
        val map = sortedMapOf<Int, String>()
        for (mt in re.findAll(s)) {
            val name = mt.groups[2]?.value ?: mt.groups[3]?.value ?: continue
            map[mt.groupValues[1].toInt()] = name
        }
        if (map.isEmpty()) return emptyList()
        val n = map.lastKey() + 1
        return List(n) { map[it] ?: "class$it" }
    }
}
