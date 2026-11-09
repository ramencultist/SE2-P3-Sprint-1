/*
Module Name: CameraSource.kt
Description: Back camera frame source for the on-device mode

Class Name: CameraSource
Description: Captures the back camera at 1280x720 with CameraX and drops frames instead of queueing them while inference is busy

Inputs: The phone's back camera and the frame interval from Settings
Outputs: Bitmaps for the detection pipeline, and an error message when the camera fails
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the CameraX capture so on-device inference always sees the newest frame
How you validated and revised the AI output: Proofreading, and the live camera path run on the emulator through CameraX
The challenges or limitations you faced while using AI: Rotating and converting camera images to bitmaps while keeping only the latest frame
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.detector

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Matrix
import android.util.Log
import android.util.Size
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Back camera at 1280x720 through CameraX ImageAnalysis. KEEP_ONLY_LATEST plus a time gate
 * means frames are dropped, never queued, while inference is busy.
 */
class CameraSource(
    private val ctx: Context,
    private val owner: LifecycleOwner,
    maxFps: Float,
    private val onFrame: (Bitmap) -> Unit,
) {
    private val minIntervalNs = (1e9 / maxFps.coerceIn(0.5f, 30f)).toLong()
    private var lastNs = 0L
    private val executor: ExecutorService = Executors.newSingleThreadExecutor { Thread(it, "camera-analysis") }
    private var provider: ProcessCameraProvider? = null

    fun start(onError: (String) -> Unit) {
        val future = ProcessCameraProvider.getInstance(ctx)
        future.addListener({
            try {
                val p = future.get()
                provider = p
                val selector = ResolutionSelector.Builder()
                    .setResolutionStrategy(
                        ResolutionStrategy(Size(1280, 720), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER)
                    ).build()
                val analysis = ImageAnalysis.Builder()
                    .setResolutionSelector(selector)
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                    .build()
                analysis.setAnalyzer(executor) { proxy ->
                    val now = System.nanoTime()
                    if (now - lastNs < minIntervalNs) {
                        proxy.close()
                        return@setAnalyzer
                    }
                    lastNs = now
                    val bmp = try {
                        val raw = proxy.toBitmap()
                        val rot = proxy.imageInfo.rotationDegrees
                        if (rot == 0) raw else {
                            val m = Matrix().apply { postRotate(rot.toFloat()) }
                            Bitmap.createBitmap(raw, 0, 0, raw.width, raw.height, m, true)
                        }
                    } finally {
                        proxy.close()
                    }
                    try {
                        onFrame(bmp)
                    } catch (e: Exception) {
                        Log.e(TAG, "frame processing failed", e)
                    }
                }
                p.unbindAll()
                p.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, analysis)
                Log.i(TAG, "camera started")
            } catch (e: Exception) {
                Log.e(TAG, "camera start failed", e)
                onError("camera error: ${e.message}")
            }
        }, ContextCompat.getMainExecutor(ctx))
    }

    /** Call on the main thread. */
    fun stop() {
        try { provider?.unbindAll() } catch (_: Exception) {}
        executor.shutdown()
        try { executor.awaitTermination(2, java.util.concurrent.TimeUnit.SECONDS) } catch (_: Exception) {}
    }

    companion object {
        private const val TAG = "Wazeomatic"
    }
}
