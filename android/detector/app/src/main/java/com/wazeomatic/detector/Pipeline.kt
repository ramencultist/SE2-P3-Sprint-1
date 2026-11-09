/*
Module Name: Pipeline.kt
Description: On-device pipeline: frame to models to merged detections to the report manager to the reporter

Class Name: Pipeline
Description: Loads the configured models and runs each frame through inference, the report manager and the reporter

Inputs: Frames from the camera or video source and the app Settings
Outputs: Detections and timings for the screen, and HazardEvents passed to the Reporter
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the on-device equivalent of the desktop pipeline
How you validated and revised the AI output: Proofreading, the JVM unit tests (gradlew test), and emulator Stage B: detector app in video mode sending broadcasts that mock Waze turns into reports with the matching hazard type
The challenges or limitations you faced while using AI: Making sure process() is only called from one thread at a time
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.detector

import ai.onnxruntime.OrtEnvironment
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Rect
import android.util.Log
import java.io.Closeable
import java.util.Locale
import java.util.concurrent.Callable
import java.util.concurrent.Executors

/**
 * Frame -> all loaded models -> merged detections -> ReportManager -> Reporter.
 * process() must only be called from one thread at a time (the frame source's thread).
 */
class Pipeline(ctx: Context, private val settings: Settings, private val reporter: Reporter) : Closeable {
    val detectors: List<OnnxDetector>
    /** Hazard models that are fused into one result (hazard.onnx [+ hazard2.onnx]). */
    val hazardMembers: List<OnnxDetector>
    val modelStatus: String
    private val fusionWeights: FloatArray?
    private val classFloors: Map<String, Float> = settings.classFloors
    private val pool = Executors.newFixedThreadPool(3) { r -> Thread(r, "detect").apply { isDaemon = true } }
    /** Learns the dashboard / hood band at the bottom of the picture (null = fixed zone). */
    val hood: HoodEstimator? = if (settings.autoHood) HoodEstimator(minFrac = settings.hoodFrac) else null
    private val hoodBmp: Bitmap? = hood?.let { Bitmap.createBitmap(it.width, it.height, Bitmap.Config.ARGB_8888) }
    private val hoodCanvas = hoodBmp?.let { android.graphics.Canvas(it) }
    private val hoodPaint = android.graphics.Paint(android.graphics.Paint.FILTER_BITMAP_FLAG)
    private val hoodPixels: IntArray? = hood?.let { IntArray(it.width * it.height) }
    private val hoodGrey: FloatArray? = hood?.let { FloatArray(it.width * it.height) }
    /** Current hood fraction (for the overlay). */
    val hoodFrac: Float get() = reportManager.hoodFrac
    private val reportManager = ReportManager(settings.reportConfig(), source = "phone")
    /** Second inference pass on a zoomed region of the road ahead; null = single pass. */
    val zoomCrop: ZoomCrop? = settings.zoomCropOrNull
    private val cropRect = Rect()
    private var frameIndex = 0
    private var lastFrameNs = 0L
    private var fps = 0.0

    init {
        val env = OrtEnvironment.getEnvironment()
        val list = ArrayList<OnnxDetector>()
        val notes = ArrayList<String>()
        val names = ArrayList<String>()
        names.add("hazard")
        if (settings.ensemble && assetExists(ctx, "hazard2.onnx")) names.add("hazard2")
        names.add("coco")
        for (name in names) {
            val bytes = readAsset(ctx, "$name.onnx")
            if (bytes == null) {
                notes.add("$name: missing")
                continue
            }
            val labels = readAsset(ctx, "${name}_labels.txt")?.toString(Charsets.UTF_8)
                ?.lines()?.map { it.trim() }?.filter { it.isNotEmpty() }
            try {
                val d = OnnxDetector(env, bytes, labels, name, settings.effectiveDetConf, settings.iou)
                list.add(d)
                notes.add("$name: ${d.inW}x${d.inH} ${d.labels.size} cls ${d.backend}")
            } catch (e: Exception) {
                Log.e(TAG, "failed to load $name.onnx", e)
                notes.add("$name: load failed (${e.message})")
            }
        }
        detectors = list
        hazardMembers = list.filter { it.name.startsWith("hazard") }
        fusionWeights = BoxFusion.parseWeights(settings.ensembleWeights, hazardMembers.size)
        if (hazardMembers.size > 1) notes.add("fusion ${settings.fusion}" + (fusionWeights?.let { " w=${it.toList()}" } ?: ""))
        if (classFloors.isNotEmpty()) notes.add("floors $classFloors")
        if (settings.trackIou > 0f) notes.add("track ${settings.trackIou}")
        if (hood != null) notes.add("auto hood")
        if (zoomCrop != null) notes.add("zoom crop $zoomCrop")
        modelStatus = if (list.isEmpty()) "model missing" else notes.joinToString(" | ")
        Log.i(TAG, "models: $modelStatus")
    }

    val hasModel: Boolean get() = detectors.isNotEmpty()

    fun process(frame: Bitmap) {
        frameIndex++
        val lat = LinkedHashMap<String, Double>()
        val dets = ArrayList<Detection>()
        // crop region in pixels; null when off or when the frame is too small for it
        val crop = zoomCrop?.pixelRect(frame.width, frame.height)
        val effCrop = crop?.let { ZoomCrop.fromRect(it, frame.width, frame.height) }
        if (crop != null) cropRect.set(crop.x1, crop.y1, crop.x2, crop.y2)
        val t0 = System.nanoTime()
        // every model (and its zoom pass) runs on its own thread; ORT releases the Java monitor
        val tasks = detectors.map { d ->
            Callable {
                try {
                    val full = d.detect(frame)
                    val msFull = d.lastMs
                    if (effCrop == null) {
                        Triple(d, full, msFull to 0.0)
                    } else {
                        val zoomed = d.detect(frame, cropRect)
                        Triple(d, ZoomCrop.merge(full + effCrop.toFrame(zoomed), d.iouThres), msFull to d.lastMs)
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "inference failed in ${d.name}", e)
                    Triple(d, emptyList(), 0.0 to 0.0)
                }
            }
        }
        val perModel = LinkedHashMap<OnnxDetector, List<Detection>>()
        for (f in pool.invokeAll(tasks)) {
            val (d, out, ms) = f.get()
            perModel[d] = out
            lat[d.name] = ms.first
            if (effCrop != null) lat["${d.name}_zoom"] = ms.second
        }
        lat["wall"] = (System.nanoTime() - t0) / 1e6
        val hazardLists = hazardMembers.map { perModel[it] ?: emptyList() }
        val hazardDets = when {
            hazardLists.isEmpty() -> emptyList()
            hazardLists.size == 1 -> hazardLists[0]
            settings.fusion == "nms" -> BoxFusion.fuseNms(hazardLists, hazardMembers[0].iouThres)
            else -> BoxFusion.fuseWbf(hazardLists, fusionWeights, modelName = "hazard")
        }
        dets.addAll(BoxFusion.applyClassFloors(hazardDets, classFloors))
        for (d in detectors) if (d !in hazardMembers) dets.addAll(perModel[d] ?: emptyList())
        val now = System.nanoTime()
        if (lastFrameNs != 0L) {
            val inst = 1e9 / (now - lastFrameNs)
            fps = if (fps == 0.0) inst else fps * 0.8 + inst * 0.2
        }
        lastFrameNs = now
        if (lat.isNotEmpty()) {
            Log.d(PERF_TAG, String.format(Locale.US, "frame=%d %s fps=%.1f dets=%d", frameIndex,
                lat.entries.joinToString(" ") { String.format(Locale.US, "%s=%.1fms", it.key, it.value) }, fps, dets.size))
        }
        updateHood(frame)
        if (hasModel) {
            for (ev in reportManager.update(dets, frameIndex)) reporter.report(ev)
        }
        PipelineBus.postFrame(FrameResult(frame, dets, lat, fps, frameIndex))
    }

    /** Reduce the frame to the estimator's grey image and feed it (a few hundred microseconds). */
    private fun updateHood(frame: Bitmap) {
        val est = hood ?: return
        val bmp = hoodBmp ?: return
        val canvas = hoodCanvas ?: return
        val px = hoodPixels ?: return
        val grey = hoodGrey ?: return
        canvas.drawBitmap(frame, null, Rect(0, 0, bmp.width, bmp.height), hoodPaint)
        bmp.getPixels(px, 0, bmp.width, 0, 0, bmp.width, bmp.height)
        for (i in px.indices) {
            val c = px[i]
            grey[i] = 0.299f * ((c shr 16) and 0xFF) + 0.587f * ((c shr 8) and 0xFF) + 0.114f * (c and 0xFF)
        }
        reportManager.setHoodFrac(est.update(grey))
    }

    override fun close() {
        pool.shutdownNow()
        detectors.forEach { try { it.close() } catch (_: Exception) {} }
    }

    companion object {
        private const val TAG = "Wazeomatic"
        const val PERF_TAG = "WazeomaticPerf"

        fun readAsset(ctx: Context, name: String): ByteArray? = try {
            ctx.assets.open(name).use { it.readBytes() }
        } catch (e: Exception) {
            null
        }

        fun assetExists(ctx: Context, name: String): Boolean =
            try { ctx.assets.list("")?.contains(name) == true } catch (e: Exception) { false }
    }
}
