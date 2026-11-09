/*
Module Name: ReportManager.kt
Description: Kotlin port of wazeomatic/report_manager.py with the same defaults

Class Name: ReportConfig
Description: Thresholds: window, minimum hits, confidence floors, cooldowns and heuristics

Class Name: ReportManager
Description: Applies persistence, low-confidence filtering, the hood zone and the 60 second per-type cooldown, then emits HazardEvents

Inputs: Per-frame detections and a clock
Outputs: HazardEvents for detections that pass every check; low-confidence and duplicate detections are dropped
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to port the report rules so the phone and the desktop behave identically
How you validated and revised the AI output: Proofreading, and ReportManagerTest.kt mirroring tests/test_report_manager.py
The challenges or limitations you faced while using AI: Keeping the Kotlin and Python rules and defaults in sync
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.detector

import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

// Port of wazeomatic/report_manager.py: persistence window, per-type cooldown, global
// cooldown, the (optional) stopped-vehicle heuristic, per-label confidence floors and the
// optional spatial tracking (hits must chain through overlapping boxes). Same defaults.

data class ReportConfig(
    val window: Int = 10,
    val minHits: Int = 3,
    val minConfidence: Float = 0.40f,
    /** Per-label overrides of [minConfidence]; potholes ship at 0.45 (see DEFAULT_LABEL_FLOORS). */
    val minConfidenceByLabel: Map<String, Float> = DEFAULT_LABEL_FLOORS,
    /**
     * > 0: the hits behind a report must come from boxes that overlap frame to frame (grown by
     * [trackGrow]) or whose centres lie within [trackGrow] box sizes; 0 = any box of the type counts.
     */
    val trackIou: Float = 0f,
    val trackGrow: Float = 1.5f,
    // ignore boxes whose bottom edge is in the lowest 10 % of the frame: the car's own bonnet reflection
    val hoodFrac: Float = 0.10f,
    val cooldownS: Double = 60.0,
    val globalCooldownS: Double = 5.0,
    val classToHazard: Map<String, String> = Schema.DEFAULT_CLASS_TO_HAZARD,
    val stoppedVehicleEnabled: Boolean = false,
    val shoulderXMin: Float = 0.62f,
    val shoulderYMin: Float = 0.40f,
    val vehicleLabels: Set<String> = setOf("car", "truck", "bus"),
    val vehicleMinArea: Float = 0.02f,
    val vehicleMinGrowth: Float = 1.35f,
)

/** Same value as DEFAULT_LABEL_FLOORS in wazeomatic/report_manager.py. */
val DEFAULT_LABEL_FLOORS: Map<String, Float> = mapOf("pothole" to 0.45f)

class ReportManager(
    val cfg: ReportConfig = ReportConfig(),
    val source: String = "phone",
    private val clock: () -> Double = ::nowSeconds,
) {
    private class Hit(val frame: Int, val conf: Float, val bbox: BBox, val label: String)

    private class Tracklet(var bbox: BBox) {
        val hits = ArrayDeque<Hit>()
    }

    private class Track {
        val hits = ArrayDeque<Hit>()
        var lastEmittedTs = -1e9
        val tracklets = ArrayList<Tracklet>()
    }

    private val tracks = LinkedHashMap<String, Track>()
    private var lastGlobalTs = -1e9
    private var frame = 0
    private var hoodFracDynamic = 0f

    /** Active hood zone: the configured minimum or a larger value learnt by [HoodEstimator]. */
    val hoodFrac: Float get() = maxOf(cfg.hoodFrac, hoodFracDynamic)

    fun setHoodFrac(frac: Float) {
        hoodFracDynamic = frac
    }

    /** Feed one inference frame; returns zero or more events to report. */
    fun update(
        detections: Iterable<Detection>,
        frameIndex: Int? = null,
        lat: Double? = null,
        lon: Double? = null,
    ): List<HazardEvent> {
        frame = frameIndex ?: (frame + 1)
        val now = clock()
        val best = LinkedHashMap<String, Detection>()
        val accepted = LinkedHashMap<String, ArrayList<Detection>>()
        for (d in detections) {
            val hazard = hazardFor(d) ?: continue
            if (d.confidence < minConf(d.label)) continue
            if (inHoodZone(d)) continue
            accepted.getOrPut(hazard) { ArrayList() }.add(d)
            val prev = best[hazard]
            if (prev == null || d.confidence > prev.confidence) best[hazard] = d
        }
        // one hit per hazard per frame, then trim the window
        for ((hazard, d) in best) {
            tracks.getOrPut(hazard) { Track() }.hits.addLast(Hit(frame, d.confidence, d.bbox, d.label))
        }
        for (tr in tracks.values) {
            while (tr.hits.isNotEmpty() && tr.hits.first().frame <= frame - cfg.window) tr.hits.removeFirst()
        }
        if (cfg.trackIou > 0f) updateTracklets(accepted)

        val events = ArrayList<HazardEvent>()
        for ((hazard, tr) in tracks) {
            val hits = qualifyingHits(tr) ?: continue
            if (now - tr.lastEmittedTs < cfg.cooldownS) continue
            if (now - lastGlobalTs < cfg.globalCooldownS) continue
            if (hazard == "HAZARD_SHOULDER_CAR_STOPPED" && !stoppedVehicleConfirmed(tr)) continue
            val top = hits.maxByOrNull { it.conf } ?: continue
            events.add(
                HazardEvent(
                    hazardType = hazard,
                    confidence = top.conf.toString().toDouble(),
                    source = source,
                    ts = now,
                    frameIndex = frame,
                    bbox = top.bbox,
                    label = top.label,
                    lat = lat,
                    lon = lon,
                )
            )
            tr.lastEmittedTs = now
            lastGlobalTs = now
            tr.hits.clear()
            tr.tracklets.clear()
        }
        return events
    }

    fun hazardFor(d: Detection): String? {
        cfg.classToHazard[d.label]?.let { return it }
        if (cfg.stoppedVehicleEnabled && d.label in cfg.vehicleLabels && inShoulder(d)) {
            return "HAZARD_SHOULDER_CAR_STOPPED"
        }
        return null
    }

    private fun minConf(label: String): Float = cfg.minConfidenceByLabel[label] ?: cfg.minConfidence

    private fun inHoodZone(d: Detection): Boolean {
        val hood = hoodFrac
        return hood > 0f && (d.bbox.cy + d.bbox.h / 2f) > (1f - hood)
    }

    private fun inShoulder(d: Detection): Boolean {
        val b = d.bbox
        return b.cx >= cfg.shoulderXMin && b.cy >= cfg.shoulderYMin && b.w * b.h >= cfg.vehicleMinArea
    }

    // ------------------------------------------------------------------ spatial tracking

    /** Greedy association of this frame's accepted boxes to per-hazard tracklets. */
    private fun updateTracklets(accepted: Map<String, List<Detection>>) {
        for ((hazard, dets) in accepted) {
            val tr = tracks.getOrPut(hazard) { Track() }
            val used = HashSet<Int>()
            for (d in dets.sortedByDescending { it.confidence }) {
                var bestI = -1
                var bestIou = -1f
                for ((i, tl) in tr.tracklets.withIndex()) {
                    if (i in used || !associated(tl.bbox, d.bbox)) continue
                    val v = grownIou(tl.bbox, d.bbox, cfg.trackGrow)
                    if (v > bestIou) { bestI = i; bestIou = v }
                }
                val hit = Hit(frame, d.confidence, d.bbox, d.label)
                if (bestI < 0) {
                    tr.tracklets.add(Tracklet(d.bbox).also { it.hits.addLast(hit) })
                    used.add(tr.tracklets.size - 1)
                } else {
                    val tl = tr.tracklets[bestI]
                    tl.bbox = d.bbox
                    tl.hits.addLast(hit)
                    used.add(bestI)
                }
            }
        }
        for (tr in tracks.values) {
            for (tl in tr.tracklets) {
                while (tl.hits.isNotEmpty() && tl.hits.first().frame <= frame - cfg.window) tl.hits.removeFirst()
            }
            tr.tracklets.removeAll { it.hits.isEmpty() }
        }
    }

    /** The hits that justify a report now, or null. */
    private fun qualifyingHits(tr: Track): Collection<Hit>? {
        if (cfg.trackIou <= 0f) return if (tr.hits.size >= cfg.minHits) tr.hits else null
        val ready = tr.tracklets.filter { it.hits.size >= cfg.minHits }
        if (ready.isEmpty()) return null
        return ready.maxByOrNull { tl -> tl.hits.maxOf { it.conf } }!!.hits
    }

    private fun associated(a: BBox, b: BBox): Boolean {
        if (grownIou(a, b, cfg.trackGrow) >= cfg.trackIou) return true
        val dx = a.cx - b.cx
        val dy = a.cy - b.cy
        return sqrt(dx * dx + dy * dy) <= cfg.trackGrow * maxOf(a.w, a.h, b.w, b.h)
    }

    /** A parked car grows in the frame as we approach and stays on the right edge. */
    private fun stoppedVehicleConfirmed(tr: Track): Boolean {
        if (tr.hits.size < 2) return false
        val first = tr.hits.first().bbox
        val last = tr.hits.last().bbox
        val a0 = first.w * first.h
        val a1 = last.w * last.h
        return a1 >= a0 * cfg.vehicleMinGrowth && last.cx >= first.cx - 0.02f
    }

    companion object {
        /** IoU of two boxes after scaling both by [grow] around their centres. */
        fun grownIou(a: BBox, b: BBox, grow: Float): Float {
            val ax1 = a.cx - a.w * grow / 2; val ay1 = a.cy - a.h * grow / 2
            val ax2 = a.cx + a.w * grow / 2; val ay2 = a.cy + a.h * grow / 2
            val bx1 = b.cx - b.w * grow / 2; val by1 = b.cy - b.h * grow / 2
            val bx2 = b.cx + b.w * grow / 2; val by2 = b.cy + b.h * grow / 2
            val iw = max(0f, min(ax2, bx2) - max(ax1, bx1))
            val ih = max(0f, min(ay2, by2) - max(ay1, by1))
            val inter = iw * ih
            val union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
            return if (union > 0f) inter / union else 0f
        }
    }
}
