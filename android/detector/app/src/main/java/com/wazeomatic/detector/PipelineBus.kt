/*
Module Name: PipelineBus.kt
Description: In-process bridge between the detector service and the screen

Class Name: FrameResult
Description: The latest processed frame with its detections, latency and frame rate

Class Name: PipelineBus
Description: Delivers frames and status messages (including camera errors) from the service to the activity on the main thread

Inputs: Frame results and status strings from DetectorService
Outputs: Callbacks to the registered screen listener
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write a small main-thread bridge so the UI can show pipeline status
How you validated and revised the AI output: Proofreading, and watching status updates on the emulator during Stage B runs
The challenges or limitations you faced while using AI: Delivering every update on the main thread so screens can attach and detach safely while the service keeps running
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.detector

import android.graphics.Bitmap
import android.os.Handler
import android.os.Looper

/** Latest processed frame plus the numbers shown on screen. */
class FrameResult(
    val bitmap: Bitmap,
    val detections: List<Detection>,
    val latencyMs: Map<String, Double>,
    val fps: Double,
    val frameIndex: Int,
)

/**
 * Small in-process bridge between the foreground service (producer) and the activity
 * (consumer). Everything is delivered on the main thread.
 */
object PipelineBus {
    interface Listener {
        fun onFrame(r: FrameResult) {}
        fun onEvent(ev: HazardEvent, dryRun: Boolean) {}
        fun onStatus(status: String) {}
    }

    private val main = Handler(Looper.getMainLooper())
    private val listeners = mutableListOf<Listener>()

    @Volatile var status: String = "stopped"
        private set
    @Volatile var running: Boolean = false

    val recentEvents = ArrayDeque<Pair<HazardEvent, Boolean>>()

    fun addListener(l: Listener) = main.post { listeners.add(l) }
    fun removeListener(l: Listener) = main.post { listeners.remove(l) }

    fun postStatus(s: String) {
        status = s
        main.post { listeners.toList().forEach { it.onStatus(s) } }
    }

    fun postFrame(r: FrameResult) {
        main.post { listeners.toList().forEach { it.onFrame(r) } }
    }

    fun postEvent(ev: HazardEvent, dryRun: Boolean) {
        main.post {
            recentEvents.addFirst(ev to dryRun)
            while (recentEvents.size > 200) recentEvents.removeLast()
            listeners.toList().forEach { it.onEvent(ev, dryRun) }
        }
    }
}
