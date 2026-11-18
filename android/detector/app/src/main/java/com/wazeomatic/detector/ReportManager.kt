/*
Module Name: ReportManager.kt
Description: Kotlin port of wazeomatic/report_manager.py with the same defaults

Class Name: ReportConfig
Description: Thresholds: window, minimum hits, confidence floors, cooldowns and heuristics

Class Name: Decision
Description: One logged reason a detection or confirmed hazard did not become a report (same reasons as report_manager.py)

Class Name: ReportManager
Description: Applies persistence, low-confidence filtering, the hood zone, the in-lane road-object rule and the 60 second per-type cooldown, then emits HazardEvents and logs every dropped detection

Inputs: Per-frame detections and a clock
Outputs: HazardEvents for detections that pass every check; a Decision for each low-confidence, hood-zone, unconfirmed or suppressed detection
External Sources: Claude Fable 5.1, Claude Opus 5.5
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to port the report rules so the phone and the desktop behave identically
How you validated and revised the AI output: Proofreading, and ReportManagerTest.kt mirroring tests/test_report_manager.py
The challenges or limitations you faced while using AI: Keeping the Kotlin and Python rules and defaults in sync
Authors: Blake Pennel, Luke Reicherter
Creation Date: 11/5/2026

Revision 11/17/2026 - Luke Reicherter and Claude Opus 5.5 (Sprint 2, requirements 7 and 9):
A description of how and why AI was used: Claude Opus 5.5 used to port the Sprint 2 decision log (criteria 7.3-7.6, 9.3-9.5) and the in-lane road-object rule (criterion 7.2) from report_manager.py, and to make a per-label floor only raise the global floor, as it already did in Python
How you validated and revised the AI output: Proofreading against report_manager.py, and Sprint2RequirementsTest.kt mirroring tests/test_sprint2_requirements.py
The challenges or limitations you faced while using AI: Keeping android.util.Log out of this class so the JVM unit tests run; Pipeline.kt sends the decisions to logcat and the status line instead
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
    // in-lane road-object rule (criterion 7.2): a COCO object whose box centre is in the lane ahead
    val roadObjectEnabled: Boolean = true,
    val roadObjectLabels: Set<String> = DEFAULT_ROAD_OBJECT_LABELS,
    val laneXMin: Float = 0.30f,
    val laneXMax: Float = 0.70f,
    val laneYMin: Float = 0.45f,
    val personLabels: Set<String> = setOf("person"),
)

/** Same value as DEFAULT_LABEL_FLOORS in wazeomatic/report_manager.py. */
val DEFAULT_LABEL_FLOORS: Map<String, Float> = mapOf("pothole" to 0.45f)

/** Same list as DEFAULT_ROAD_OBJECT_LABELS in wazeomatic/report_manager.py. */
val DEFAULT_ROAD_OBJECT_LABELS: Set<String> = setOf(
    "suitcase", "backpack", "handbag", "umbrella", "sports ball", "skateboard",
    "chair", "couch", "bench", "potted plant", "bed", "toilet", "refrigerator", "tv",
)

/** Why a detection, or a confirmed hazard, did not become a report (reasons as in report_manager.py). */
data class Decision(
    val frame: Int,
    val reason: String,
    val label: String,
    val hazard: String?,
    val confidence: Float,
    val ts: Double,
    val detail: String = "",
) {
    override fun toString(): String =
        "frame $frame: $reason $label (${"%.2f".format(java.util.Locale.US, confidence)})" +
            (hazard?.let { " -> $it" } ?: "") + (if (detail.isNotEmpty()) ": $detail" else "")

    companion object {
        const val LOW_CONFIDENCE = "low_confidence"
        const val HOOD_ZONE = "hood_zone"
        const val UNCONFIRMED = "unconfirmed"
        const val COOLDOWN = "cooldown"
        const val GLOBAL_COOLDOWN = "global_cooldown"
        const val CARRIED_OBJECT = "carried_object"
    }
}

class ReportManager(
    val cfg: ReportConfig = ReportConfig(),
    val source: String = "phone",
    private val clock: () -> Double = ::nowSeconds,
) {
    /** Called for every logged [Decision]; Pipeline.kt sends them to logcat. A property, not a
     *  constructor parameter, so a trailing lambda in ReportManager(cfg, src) { now } stays the clock. */
    var onDecision: ((Decision) -> Unit)? = null

    private class Hit(val frame: Int, val conf: Float, val bbox: BBox, val label: String)

    private class Tracklet(var bbox: BBox) {
        val hits = ArrayDeque<Hit>()
    }

    private class Track {
        val hits = ArrayDeque<Hit>()
        var lastEmittedTs = -1e9
        val tracklets = ArrayList<Tracklet>()
        var lastSuppressedFrame = Int.MIN_VALUE / 2
    }

    private val tracks = LinkedHashMap<String, Track>()
    private var lastGlobalTs = -1e9
    private var frame = 0
    private var hoodFracDynamic = 0f

    /** Most recent decisions, oldest first (criteria 7.3-7.6, 9.3-9.5). */
    val decisions = ArrayDeque<Decision>()
    val decisionCounts = LinkedHashMap<String, Int>()
    var decisionLogSize = 2000

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
        val dets = detections.toList()
        val persons = dets.filter { it.label in cfg.personLabels }.map { it.bbox }
        val best = LinkedHashMap<String, Detection>()
        val accepted = LinkedHashMap<String, ArrayList<Detection>>()
        for (d in dets) {
            val hazard = hazardFor(d) ?: continue
            val floor = minConf(d.label)
            if (d.confidence < floor) {
                record(Decision.LOW_CONFIDENCE, d.label, hazard, d.confidence, now, "below floor ${fmt(floor)}")
                continue
            }
            if (inHoodZone(d)) {
                record(Decision.HOOD_ZONE, d.label, hazard, d.confidence, now,
                    "box bottom ${fmt(d.bbox.cy + d.bbox.h / 2f)} inside hood zone ${fmt(hoodFrac)}")
                continue
            }
            if (hazard == "HAZARD_ON_ROAD_OBJECT" && persons.any { centerInside(d.bbox, it) }) {
                record(Decision.CARRIED_OBJECT, d.label, hazard, d.confidence, now, "inside a person box")
                continue
            }
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
            val hits = qualifyingHits(tr)
            if (hits == null) {
                best[hazard]?.let {
                    record(Decision.UNCONFIRMED, it.label, hazard, it.confidence, now,
                        "${hitCount(tr)}/${cfg.minHits} hits in last ${cfg.window} frames")
                }
                continue
            }
            if (now - tr.lastEmittedTs < cfg.cooldownS) {
                logSuppressed(Decision.COOLDOWN, hazard, tr, hits, now,
                    "reported ${"%.1f".format(java.util.Locale.US, now - tr.lastEmittedTs)}s ago, cooldown ${cfg.cooldownS.toInt()}s")
                continue
            }
            if (now - lastGlobalTs < cfg.globalCooldownS) {
                logSuppressed(Decision.GLOBAL_COOLDOWN, hazard, tr, hits, now,
                    "another hazard reported ${"%.1f".format(java.util.Locale.US, now - lastGlobalTs)}s ago")
                continue
            }
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
        if (cfg.roadObjectEnabled && d.label in cfg.roadObjectLabels && inLane(d)) return "HAZARD_ON_ROAD_OBJECT"
        if (cfg.stoppedVehicleEnabled && d.label in cfg.vehicleLabels && inShoulder(d)) {
            return "HAZARD_SHOULDER_CAR_STOPPED"
        }
        return null
    }

    /** A label floor only ever raises the bar, as in report_manager.py. */
    private fun minConf(label: String): Float = maxOf(cfg.minConfidence, cfg.minConfidenceByLabel[label] ?: 0f)

    private fun inLane(d: Detection): Boolean =
        d.bbox.cx in cfg.laneXMin..cfg.laneXMax && d.bbox.cy >= cfg.laneYMin

    private fun centerInside(inner: BBox, outer: BBox): Boolean =
        kotlin.math.abs(inner.cx - outer.cx) <= outer.w / 2f && kotlin.math.abs(inner.cy - outer.cy) <= outer.h / 2f

    private fun hitCount(tr: Track): Int =
        if (cfg.trackIou <= 0f) tr.hits.size else (tr.tracklets.maxOfOrNull { it.hits.size } ?: 0)

    private fun fmt(v: Float) = "%.2f".format(java.util.Locale.US, v)

    private fun record(reason: String, label: String, hazard: String?, conf: Float, ts: Double, detail: String) {
        val dec = Decision(frame, reason, label, hazard, conf, ts, detail)
        decisions.addLast(dec)
        while (decisions.size > decisionLogSize) decisions.removeFirst()
        decisionCounts[reason] = (decisionCounts[reason] ?: 0) + 1
        onDecision?.invoke(dec)
    }

    /** Confirmed but held back by a cooldown: logged once per window, not on every frame. */
    private fun logSuppressed(reason: String, hazard: String, tr: Track, hits: Collection<Hit>, now: Double, detail: String) {
        if (frame - tr.lastSuppressedFrame < cfg.window) return
        tr.lastSuppressedFrame = frame
        val top = hits.maxByOrNull { it.conf } ?: return
        record(reason, top.label, hazard, top.conf, now, detail)
    }

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
