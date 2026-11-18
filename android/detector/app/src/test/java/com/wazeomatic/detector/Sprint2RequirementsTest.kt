/*
Module Name: Sprint2RequirementsTest.kt
Description: JVM tests of the phone's ReportManager against the Sprint 2 acceptance criteria for requirements 7 (in-road hazards) and 9 (construction); the Kotlin twin of tests/test_sprint2_requirements.py

Class Name: Sprint2RequirementsTest
Description: One test per criterion that the report manager decides on its own (7.1-7.6, 9.1-9.5)

Inputs: Synthetic detections and a controllable clock
Outputs: JUnit pass or fail results
External Sources: Claude Opus 5.5
Attributions:

Creation 11/17/2026 - Luke Reicherter and Claude Opus 5.5:
A description of how and why AI was used: Claude Opus 5.5 used to mirror the Python Sprint 2 criterion tests so the phone and the desktop are held to the same rules
How you validated and revised the AI output: Proofreading against tests/test_sprint2_requirements.py, and gradlew testDebugUnitTest
The challenges or limitations you faced while using AI: Model loading (7.7) and the end-to-end criteria (7.8, 9.6, 9.7) need the app or the emulator, so they are not repeated here
Authors: Luke Reicherter
Creation Date: 11/17/2026
*/

package com.wazeomatic.detector

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class Sprint2RequirementsTest {
    private var now = 1000.0
    private val ahead = BBox(0.5f, 0.55f, 0.08f, 0.06f)
    private val sidewalk = BBox(0.92f, 0.6f, 0.05f, 0.08f)

    private fun rm(cfg: ReportConfig = ReportConfig(globalCooldownS = 0.0)) = ReportManager(cfg, "phone") { now }

    private fun det(label: String, conf: Float = 0.8f, bbox: BBox = ahead) = Detection(label, conf, bbox, "hazard")

    private fun feed(m: ReportManager, frames: List<List<Detection>>): List<HazardEvent> {
        val out = ArrayList<HazardEvent>()
        for (f in frames) {
            out += m.update(f)
            now += 0.1
        }
        return out
    }

    private fun reasons(m: ReportManager) = m.decisions.map { it.reason }

    @Test
    fun c7_1_potholeIn3Of10FramesReportsHazardPothole() {
        val ev = feed(rm(), listOf(listOf(det("pothole", 0.7f)), emptyList(), listOf(det("pothole", 0.7f)),
            emptyList(), listOf(det("pothole", 0.7f))))
        assertEquals(listOf("HAZARD_ON_ROAD_POTHOLE"), ev.map { it.hazardType })
        val extras = ev[0].intentExtras()
        assertEquals("Hazard", extras["waze_category"])
        assertEquals("Pothole", extras["waze_subcategory"])
    }

    @Test
    fun c7_2_objectInLaneReportsHazardObject() {
        val ev = feed(rm(), List(3) { listOf(det("suitcase", 0.6f)) })
        assertEquals(listOf("HAZARD_ON_ROAD_OBJECT"), ev.map { it.hazardType })
        assertEquals("Object", ev[0].intentExtras()["waze_subcategory"])
        assertEquals(1, feed(rm(), List(3) { listOf(det("debris", 0.6f)) }).size)
    }

    @Test
    fun c7_2_objectOutsideLaneOrCarriedIsNotDebris() {
        assertTrue(feed(rm(), List(5) { listOf(det("suitcase", 0.6f, sidewalk)) }).isEmpty())
        val m = rm()
        val person = det("person", 0.9f, BBox(0.5f, 0.55f, 0.2f, 0.5f))
        assertTrue(feed(m, List(5) { listOf(person, det("backpack", 0.6f)) }).isEmpty())
        assertTrue(Decision.CARRIED_OBJECT in reasons(m))
    }

    @Test
    fun c7_3_singleFrameFlickerIsLoggedNotReported() {
        val m = rm()
        val frames = listOf(listOf(det("pothole"))) + List(9) { emptyList<Detection>() } + listOf(listOf(det("pothole")))
        assertTrue(feed(m, frames).isEmpty())
        assertEquals(listOf(Decision.UNCONFIRMED, Decision.UNCONFIRMED), reasons(m))
        assertTrue(m.decisions.first().detail.contains("1/3 hits in last 10 frames"))
    }

    @Test
    fun c7_4_belowThresholdIsLoggedWithItsScore() {
        val m = rm()
        assertTrue(feed(m, List(5) { listOf(det("pothole", 0.42f)) }).isEmpty())
        assertEquals(List(5) { Decision.LOW_CONFIDENCE }, reasons(m))
        assertEquals(0.42f, m.decisions.first().confidence, 1e-6f)
        assertTrue(m.decisions.first().detail.contains("0.45"))
    }

    @Test
    fun c7_5_sameTypeWithin60sIsSuppressedAndLogged() {
        val m = rm()
        assertEquals(1, feed(m, List(3) { listOf(det("pothole")) }).size)
        assertTrue(feed(m, List(100) { listOf(det("pothole")) }).isEmpty())
        val suppressed = m.decisions.filter { it.reason == Decision.COOLDOWN }
        assertTrue(suppressed.isNotEmpty() && suppressed.size <= 10)
    }

    @Test
    fun c7_6_boxInHoodZoneIsIgnoredAndLogged() {
        val m = rm(ReportConfig(globalCooldownS = 0.0, hoodFrac = 0.2f))
        assertTrue(feed(m, List(5) { listOf(det("pothole", 0.9f, BBox(0.5f, 0.92f, 0.3f, 0.1f))) }).isEmpty())
        assertEquals(List(5) { Decision.HOOD_ZONE }, reasons(m))
    }

    @Test
    fun c9_1_eachConstructionClassReportsHazardConstruction() {
        for (label in listOf("cone", "construction_sign", "delineator", "barricade", "worker")) {
            val ev = feed(rm(), List(3) { listOf(det(label, 0.6f)) })
            assertEquals(label, listOf("HAZARD_ON_ROAD_CONSTRUCTION"), ev.map { it.hazardType })
            assertEquals("Construction", ev[0].intentExtras()["waze_subcategory"])
        }
    }

    @Test
    fun c9_2_severalConstructionClassesMergeIntoOneEvent() {
        val frame = listOf(det("cone", 0.55f, BBox(0.4f, 0.6f, 0.03f, 0.05f)), det("construction_sign", 0.8f),
            det("worker", 0.6f, BBox(0.65f, 0.55f, 0.04f, 0.1f)))
        val ev = feed(rm(), List(5) { frame })
        assertEquals(1, ev.size)
        assertEquals("construction_sign", ev[0].label)
    }

    @Test
    fun c9_3_workZoneGivesOneReportPerCooldown() {
        val m = rm()
        val ev = feed(m, List(1000) { listOf(det("cone", 0.6f), det("barricade", 0.7f)) })
        assertEquals(2, ev.size)
        assertTrue(ev[1].ts - ev[0].ts >= 60.0)
        assertTrue(m.decisions.any { it.reason == Decision.COOLDOWN })
    }

    @Test
    fun c9_4_strayConeSeenBrieflyIsLoggedNotReported() {
        val m = rm()
        val frames = List(2) { listOf(det("cone", 0.7f, sidewalk)) } + List(10) { emptyList<Detection>() }
        assertTrue(feed(m, frames).isEmpty())
        assertEquals(listOf(Decision.UNCONFIRMED, Decision.UNCONFIRMED), reasons(m))
    }

    @Test
    fun c9_5_lowConfidenceConstructionIsLogged() {
        val m = rm()
        assertTrue(feed(m, List(4) { listOf(det("cone", 0.37f), det("construction_sign", 0.38f)) }).isEmpty())
        assertEquals(setOf("cone", "construction_sign"), m.decisions.map { it.label }.toSet())
    }

    @Test
    fun labelFloorNeverLowersTheGlobalFloor() {
        // parity with report_manager.py: max(min_confidence, label floor)
        val m = rm(ReportConfig(globalCooldownS = 0.0, minConfidence = 0.5f, minConfidenceByLabel = mapOf("cone" to 0.3f)))
        assertTrue(feed(m, List(5) { listOf(det("cone", 0.4f)) }).isEmpty())
    }
}
