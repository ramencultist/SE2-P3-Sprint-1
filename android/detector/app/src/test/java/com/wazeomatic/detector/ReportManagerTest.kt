/*
Module Name: ReportManagerTest.kt
Description: JVM unit tests for ReportManager.kt

Class Name: ReportManagerTest
Description: Tests persistence, low confidence, cooldowns, unmapped labels and the event JSON with a controllable clock

Inputs: Synthetic detections and a fake clock
Outputs: JUnit pass or fail results
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to port the Python report manager tests to JUnit
How you validated and revised the AI output: Proofreading, and running gradlew test
The challenges or limitations you faced while using AI: Keeping the test cases in step with the Python tests
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.detector

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ReportManagerTest {
    private var now = 1000.0
    private fun rm(cfg: ReportConfig = ReportConfig()) = ReportManager(cfg, "phone") { now }

    private fun det(label: String, conf: Float = 0.8f, bbox: BBox = BBox(0.5f, 0.5f, 0.1f, 0.1f)) =
        Detection(label, conf, bbox, "coco")

    @Test
    fun needsThreeHitsInWindow() {
        val m = rm()
        assertTrue(m.update(listOf(det("dog"))).isEmpty())
        assertTrue(m.update(emptyList()).isEmpty())
        assertTrue(m.update(listOf(det("dog"))).isEmpty())
        val ev = m.update(listOf(det("dog", 0.9f)))
        assertEquals(1, ev.size)
        assertEquals("HAZARD_SHOULDER_ANIMALS", ev[0].hazardType)
        assertEquals(0.9, ev[0].confidence, 1e-6)
        assertEquals("dog", ev[0].label)
        assertEquals(4, ev[0].frameIndex)
        assertEquals("phone", ev[0].source)
    }

    @Test
    fun hitsOutsideWindowAreForgotten() {
        val m = rm()
        m.update(listOf(det("dog")), frameIndex = 1)
        m.update(listOf(det("dog")), frameIndex = 2)
        // frame 12: hits at 1 and 2 are older than the 10-frame window
        assertTrue(m.update(listOf(det("dog")), frameIndex = 12).isEmpty())
        assertTrue(m.update(listOf(det("dog")), frameIndex = 13).isEmpty())
        assertEquals(1, m.update(listOf(det("dog")), frameIndex = 14).size)
    }

    @Test
    fun lowConfidenceAndUnmappedLabelsIgnored() {
        val m = rm()
        repeat(10) { assertTrue(m.update(listOf(det("dog", 0.39f), det("person", 0.99f), det("car", 0.99f))).isEmpty()) }
    }

    @Test
    fun perTypeCooldown() {
        val m = rm()
        repeat(3) { m.update(listOf(det("dog"))) }
        now += 10
        repeat(20) { assertTrue(m.update(listOf(det("dog"))).isEmpty()) }
        now += 51 // 61 s after the first event
        var got = 0
        repeat(3) { got += m.update(listOf(det("dog"))).size }
        assertEquals(1, got)
    }

    @Test
    fun firstEventEmittedAfterThirdFrame() {
        val m = rm()
        val out = (1..3).flatMap { m.update(listOf(det("dog"))) }
        assertEquals(1, out.size)
    }

    @Test
    fun globalCooldownBetweenDifferentTypes() {
        val m = rm()
        val out = (1..3).flatMap { m.update(listOf(det("dog"), det("pothole", 0.7f))) }
        // both qualify on frame 3, but only one event per 5 s globally
        assertEquals(1, out.size)
        assertEquals("HAZARD_SHOULDER_ANIMALS", out[0].hazardType)
        now += 2
        assertTrue(m.update(listOf(det("pothole", 0.7f))).isEmpty())
        now += 3.5
        val later = m.update(listOf(det("pothole", 0.7f)))
        assertEquals(listOf("HAZARD_ON_ROAD_POTHOLE"), later.map { it.hazardType })
    }

    @Test
    fun customCooldownSetting() {
        val m = rm(ReportConfig(cooldownS = 1.0, globalCooldownS = 0.0))
        assertEquals(1, (1..3).flatMap { m.update(listOf(det("dog"))) }.size)
        now += 1.5
        assertEquals(1, (1..3).flatMap { m.update(listOf(det("dog"))) }.size)
    }

    @Test
    fun stoppedVehicleHeuristic() {
        val cfg = ReportConfig(stoppedVehicleEnabled = true)
        // car on the right shoulder that grows as we approach
        val m = rm(cfg)
        val growing = listOf(0.15f, 0.17f, 0.20f).flatMap { s ->
            m.update(listOf(det("car", 0.8f, BBox(0.8f, 0.6f, s, s))))
        }
        assertEquals(listOf("HAZARD_SHOULDER_CAR_STOPPED"), growing.map { it.hazardType })

        // same size every frame (moving with us) -> no event
        val m2 = rm(cfg)
        val steady = (1..6).flatMap { m2.update(listOf(det("car", 0.8f, BBox(0.8f, 0.6f, 0.15f, 0.15f)))) }
        assertTrue(steady.isEmpty())

        // left side of the frame never counts
        val m3 = rm(cfg)
        val left = listOf(0.15f, 0.17f, 0.20f).flatMap { s -> m3.update(listOf(det("car", 0.8f, BBox(0.3f, 0.6f, s, s)))) }
        assertTrue(left.isEmpty())

        // heuristic off by default
        val m4 = rm()
        val off = listOf(0.15f, 0.17f, 0.20f).flatMap { s -> m4.update(listOf(det("car", 0.8f, BBox(0.8f, 0.6f, s, s)))) }
        assertTrue(off.isEmpty())
    }

    @Test
    fun eventJsonMatchesSchemaShape() {
        val ev = HazardEvent("HAZARD_ON_ROAD_POTHOLE", 0.873, "phone", 1762345678.25, 42, BBox(0.5f, 0.25f, 0.1f, 0.2f), "pothole")
        val json = ev.toJson()
        assertTrue(json.startsWith("{\"hazard_type\":\"HAZARD_ON_ROAD_POTHOLE\",\"confidence\":0.873,\"source\":\"phone\",\"ts\":1762345678.25,"))
        val o = JSONObject(json)
        assertEquals("Hazard", o.getString("waze_category"))
        assertEquals("Pothole", o.getString("waze_subcategory"))
        assertTrue(o.isNull("lat"))
        assertEquals(1, o.getInt("schema"))
        val back = HazardEvent.fromJson(json)
        assertEquals(ev, back)

        val extras = ev.intentExtras()
        assertEquals("0.873", extras["confidence"])
        assertEquals("1762345678.250", extras["ts"])
        assertEquals(listOf("hazard_type", "confidence", "ts", "source", "waze_category", "waze_subcategory"), extras.keys.toList())
    }

    @Test
    fun parsesPythonJsonLine() {
        // what HazardEvent.to_json() in schema.py produces
        val line = "{\"hazard_type\":\"POLICE\",\"confidence\":0.61,\"source\":\"pi\",\"ts\":1762345678.123," +
            "\"frame_index\":null,\"bbox\":null,\"label\":null,\"lat\":null,\"lon\":null,\"schema\":1," +
            "\"waze_category\":\"Police\",\"waze_subcategory\":\"\"}"
        val ev = HazardEvent.fromJson(line)
        assertEquals("POLICE", ev.hazardType)
        assertEquals("pi", ev.source)
        assertNull(ev.bbox)
        assertEquals("", ev.intentExtras()["waze_subcategory"])
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsUnknownHazard() {
        HazardEvent.fromJson("{\"hazard_type\":\"NOPE\",\"confidence\":0.5}")
    }

    @Test
    fun hoodZoneIgnored() {
        val m = rm(ReportConfig(minHits = 1))
        // bottom edge at 0.93: inside the lowest 10 % of the frame (bonnet reflection)
        assertTrue(m.update(listOf(det("pothole", 0.9f, BBox(0.49f, 0.88f, 0.15f, 0.10f)))).isEmpty())
        assertEquals(1, m.update(listOf(det("pothole", 0.9f, BBox(0.49f, 0.80f, 0.15f, 0.10f)))).size)
        val off = rm(ReportConfig(minHits = 1, hoodFrac = 0f))
        assertEquals(1, off.update(listOf(det("pothole", 0.9f, BBox(0.49f, 0.88f, 0.15f, 0.10f)))).size)
    }

    @Test
    fun trackingRejectsSpatiallyInconsistentHits() {
        val m = rm(ReportConfig(trackIou = 0.3f, globalCooldownS = 0.0))
        val spots = listOf(0.1f to 0.1f, 0.9f to 0.1f, 0.5f to 0.8f)
        var events = 0
        for ((i, s) in spots.withIndex()) {
            events += m.update(listOf(det("pothole", 0.9f, BBox(s.first, s.second, 0.08f, 0.08f))), frameIndex = i).size
            now += 0.1
        }
        assertEquals(0, events)
        // the untracked manager reports after three hits anywhere
        val plain = rm(ReportConfig(globalCooldownS = 0.0))
        var plainEvents = 0
        for ((i, s) in spots.withIndex()) {
            plainEvents += plain.update(listOf(det("pothole", 0.9f, BBox(s.first, s.second, 0.08f, 0.08f))), frameIndex = i).size
        }
        assertEquals(1, plainEvents)
    }

    @Test
    fun trackingAcceptsAnApproachingObject() {
        val m = rm(ReportConfig(trackIou = 0.3f, globalCooldownS = 0.0))
        val steps = listOf(0.55f to 0.05f, 0.60f to 0.06f, 0.66f to 0.075f)
        var ev: List<HazardEvent> = emptyList()
        for ((i, s) in steps.withIndex()) {
            ev = m.update(listOf(det("pothole", 0.8f, BBox(0.5f, s.first, s.second, s.second))), frameIndex = i)
            now += 0.1
        }
        assertEquals(1, ev.size)
        assertEquals("HAZARD_ON_ROAD_POTHOLE", ev[0].hazardType)
    }

    @Test
    fun trackingKeepsSeparateObjectsApart() {
        val m = rm(ReportConfig(trackIou = 0.3f, globalCooldownS = 0.0))
        for (i in 0 until 2) {
            assertTrue(m.update(listOf(det("cone", 0.8f, BBox(0.2f, 0.5f, 0.1f, 0.1f)),
                det("cone", 0.8f, BBox(0.8f, 0.5f, 0.1f, 0.1f))), frameIndex = i).isEmpty())
        }
        assertEquals(1, m.update(listOf(det("cone", 0.8f, BBox(0.21f, 0.5f, 0.1f, 0.1f))), frameIndex = 2).size)
    }

    @Test
    fun perLabelFloorOverridesMinConfidence() {
        val m = rm(ReportConfig(minConfidenceByLabel = mapOf("cone" to 0.9f), globalCooldownS = 0.0))
        for (i in 0 until 3) assertTrue(m.update(listOf(det("cone", 0.8f)), frameIndex = i).isEmpty())
        var ev: List<HazardEvent> = emptyList()
        for (i in 3 until 6) ev = m.update(listOf(det("construction_sign", 0.5f)), frameIndex = i)
        assertEquals(1, ev.size)
    }

    @Test
    fun defaultPotholeFloorIs045() {
        assertEquals(mapOf("pothole" to 0.45f), ReportConfig().minConfidenceByLabel)
        val m = rm(ReportConfig(globalCooldownS = 0.0))
        for (i in 0 until 3) assertTrue(m.update(listOf(det("pothole", 0.42f)), frameIndex = i).isEmpty())
        var ev: List<HazardEvent> = emptyList()
        for (i in 3 until 6) ev = m.update(listOf(det("pothole", 0.46f)), frameIndex = i)
        assertEquals(1, ev.size)
    }
}
