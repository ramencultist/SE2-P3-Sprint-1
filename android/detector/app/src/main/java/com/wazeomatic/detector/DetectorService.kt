/*
Module Name: DetectorService.kt
Description: Foreground service that keeps detection running while Waze is in front

Class Name: DetectorService
Description: Owns the frame source, Pipeline, Reporter and TCP server, and posts status and errors to the screen

Inputs: Settings and start or stop requests from MainActivity
Outputs: A running pipeline, a persistent notification and status messages
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the foreground service so detection survives switching to Waze
How you validated and revised the AI output: Proofreading, the JVM unit tests (gradlew test), and emulator Stage B: detector app in video mode sending broadcasts that mock Waze turns into reports with the matching hazard type
The challenges or limitations you faced while using AI: Choosing the right foreground service type (camera or data sync) so Android lets the service start
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.detector

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.util.Log
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import java.io.File

/**
 * Foreground service that owns the whole pipeline so detection keeps running while Waze is
 * in front. Started and stopped from MainActivity; reads its config from Settings.
 */
class DetectorService : LifecycleService() {
    @Volatile private var pipeline: Pipeline? = null
    private var camera: CameraSource? = null
    private var video: VideoSource? = null
    private var tcp: TcpEventServer? = null
    private var reporter: Reporter? = null
    private var worker: HandlerThread? = null

    override fun onBind(intent: Intent): IBinder? {
        super.onBind(intent)
        return null
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        val s = Settings.load(this)
        val camOk = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        val useCamera = s.source == "camera" && camOk
        try {
            goForeground(s, useCamera)
        } catch (e: Exception) {
            Log.e(TAG, "startForeground failed", e)
            PipelineBus.postStatus("could not start foreground service: ${e.message}")
            stopSelf()
            return START_NOT_STICKY
        }
        stopPipeline()
        startPipeline(s, camOk)
        return START_NOT_STICKY
    }

    private fun goForeground(s: Settings, useCamera: Boolean) {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Detector", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val n: Notification = Notification.Builder(this, CHANNEL)
            .setContentTitle("Wazeomatic is watching the road")
            .setContentText("source: ${s.source}" + if (s.dryRun) " (dry run)" else "")
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
        val type = if (useCamera) ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA else ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
        ServiceCompat.startForeground(this, NOTIF_ID, n, type)
    }

    private fun startPipeline(s: Settings, camOk: Boolean) {
        PipelineBus.running = true
        val rep = Reporter(this, s.dryRun, s.targetPackage)
        reporter = rep
        if (s.tcpEnabled) {
            tcp = TcpEventServer(s.tcpPort) { ev -> rep.report(ev) }.also { it.start() }
        }
        if (s.source == "tcp") {
            PipelineBus.postStatus("tcp only, listening on ${s.tcpPort}")
            return
        }
        PipelineBus.postStatus("loading models...")
        // model loading can take a second, keep it off the main thread
        val ht = HandlerThread("pipeline-init").also { it.start() }
        worker = ht
        Handler(ht.looper).post {
            val p = try {
                Pipeline(this, s, rep)
            } catch (e: Throwable) {
                Log.e(TAG, "pipeline init failed", e)
                PipelineBus.postStatus("init failed: ${e.message}")
                return@post
            }
            pipeline = p
            val base = p.modelStatus + if (s.tcpEnabled) " | tcp:${s.tcpPort}" else ""
            PipelineBus.postStatus(base)
            when (s.source) {
                "video" -> {
                    val dir = getExternalFilesDir(null) ?: filesDir
                    val f = if (s.video.startsWith("/")) File(s.video) else File(dir, s.video)
                    video = VideoSource(f, s.frameStep, s.loop, { bmp -> p.process(bmp) }) { st ->
                        PipelineBus.postStatus("$base | $st")
                    }.also { it.start() }
                }
                else -> {
                    if (!camOk) {
                        PipelineBus.postStatus("$base | camera permission missing")
                        return@post
                    }
                    Handler(mainLooper).post {
                        if (pipeline !== p) return@post
                        camera = CameraSource(this, this, s.maxFps) { bmp -> p.process(bmp) }.also {
                            it.start { err -> PipelineBus.postStatus("$base | $err") }
                        }
                    }
                }
            }
        }
    }

    private fun stopPipeline() {
        worker?.quitSafely()
        worker?.join(5000)
        worker = null
        camera?.stop(); camera = null
        video?.stop(); video = null
        tcp?.stop(); tcp = null
        pipeline?.close(); pipeline = null
        reporter = null
    }

    override fun onDestroy() {
        stopPipeline()
        PipelineBus.running = false
        PipelineBus.postStatus("stopped")
        super.onDestroy()
    }

    companion object {
        private const val TAG = "Wazeomatic"
        private const val CHANNEL = "detector"
        private const val NOTIF_ID = 1

        fun start(ctx: Context) {
            ContextCompat.startForegroundService(ctx, Intent(ctx, DetectorService::class.java))
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, DetectorService::class.java))
        }
    }
}
