/*
Module Name: VideoSource.kt
Description: Pre-recorded video frame source for the on-device mode

Class Name: VideoSource
Description: Decodes an MP4 with MediaExtractor and MediaCodec and passes every Nth frame to the pipeline

Inputs: An MP4 file in the app's files directory and a frame step
Outputs: Bitmaps for the detection pipeline, processed the same way as camera frames
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the MP4 decoder so test video goes through the same path as the camera
How you validated and revised the AI output: Proofreading, the JVM unit tests (gradlew test), and emulator Stage B: detector app in video mode sending broadcasts that mock Waze turns into reports with the matching hazard type
The challenges or limitations you faced while using AI: Converting MediaCodec YUV output to bitmaps without a display surface
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.detector

import android.graphics.Bitmap
import android.media.Image
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaExtractor
import android.media.MediaFormat
import android.util.Log
import java.io.File

/**
 * Decodes an MP4 with MediaExtractor + MediaCodec (ByteBuffer/Image output, no surface) and
 * hands every [frameStep]-th frame to [onFrame] on the decode thread. Runs as fast as
 * inference allows, which is what we want for repeatable tests.
 */
class VideoSource(
    private val file: File,
    private val frameStep: Int,
    private val loop: Boolean,
    private val onFrame: (Bitmap) -> Unit,
    private val onStatus: (String) -> Unit,
) {
    @Volatile private var running = false
    private var thread: Thread? = null

    fun start() {
        running = true
        thread = Thread({
            try {
                decodeLoop()
            } catch (e: Exception) {
                Log.e(TAG, "video decode failed", e)
                onStatus("video error: ${e.message}")
            }
        }, "video-decode").apply { start() }
    }

    fun stop() {
        running = false
        thread?.join(3000)
    }

    private fun decodeLoop() {
        if (!file.exists()) {
            onStatus("video not found: ${file.absolutePath}")
            return
        }
        val ex = MediaExtractor()
        ex.setDataSource(file.absolutePath)
        var track = -1
        var format: MediaFormat? = null
        for (i in 0 until ex.trackCount) {
            val f = ex.getTrackFormat(i)
            if (f.getString(MediaFormat.KEY_MIME)?.startsWith("video/") == true) {
                track = i
                format = f
                break
            }
        }
        if (track < 0 || format == null) {
            onStatus("no video track in ${file.name}")
            ex.release()
            return
        }
        ex.selectTrack(track)
        val mime = format.getString(MediaFormat.KEY_MIME)!!
        format.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible)
        val codec = MediaCodec.createDecoderByType(mime)
        codec.configure(format, null, null, 0)
        codec.start()
        Log.i(TAG, "video ${file.name}: $mime ${format.getInteger(MediaFormat.KEY_WIDTH)}x${format.getInteger(MediaFormat.KEY_HEIGHT)} step=$frameStep loop=$loop")
        onStatus("video ${file.name}")

        val info = MediaCodec.BufferInfo()
        var inputDone = false
        var decoded = 0L
        var pixels = IntArray(0)
        try {
            while (running) {
                if (!inputDone) {
                    val inIdx = codec.dequeueInputBuffer(10_000)
                    if (inIdx >= 0) {
                        val buf = codec.getInputBuffer(inIdx)!!
                        val n = ex.readSampleData(buf, 0)
                        if (n < 0) {
                            codec.queueInputBuffer(inIdx, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                            inputDone = true
                        } else {
                            codec.queueInputBuffer(inIdx, 0, n, ex.sampleTime, 0)
                            ex.advance()
                        }
                    }
                }
                val outIdx = codec.dequeueOutputBuffer(info, 10_000)
                if (outIdx < 0) continue
                var bmp: Bitmap? = null
                if (info.size > 0 && decoded % frameStep == 0L) {
                    val img = codec.getOutputImage(outIdx)
                    if (img != null) {
                        val r = img.cropRect
                        if (pixels.size != r.width() * r.height()) pixels = IntArray(r.width() * r.height())
                        yuvToArgb(img, pixels)
                        bmp = Bitmap.createBitmap(pixels, r.width(), r.height(), Bitmap.Config.ARGB_8888)
                        img.close()
                    }
                }
                if (info.size > 0) decoded++
                codec.releaseOutputBuffer(outIdx, false)
                bmp?.let { onFrame(it) }
                if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) {
                    if (!loop) {
                        onStatus("video finished (${decoded} frames)")
                        break
                    }
                    Log.i(TAG, "video looped after $decoded frames")
                    ex.seekTo(0, MediaExtractor.SEEK_TO_PREVIOUS_SYNC)
                    codec.flush()
                    inputDone = false
                }
            }
        } finally {
            try { codec.stop() } catch (_: Exception) {}
            codec.release()
            ex.release()
        }
    }

    companion object {
        private const val TAG = "Wazeomatic"

        /** YUV_420_888 (any plane layout) -> ARGB, BT.601 limited range. */
        fun yuvToArgb(img: Image, out: IntArray) {
            val r = img.cropRect
            val w = r.width()
            val h = r.height()
            val planes = img.planes
            val yArr = ByteArray(planes[0].buffer.remaining()).also { planes[0].buffer.get(it) }
            val uArr = ByteArray(planes[1].buffer.remaining()).also { planes[1].buffer.get(it) }
            val vArr = ByteArray(planes[2].buffer.remaining()).also { planes[2].buffer.get(it) }
            val yRs = planes[0].rowStride
            val yPs = planes[0].pixelStride
            val uRs = planes[1].rowStride
            val uPs = planes[1].pixelStride
            val vRs = planes[2].rowStride
            val vPs = planes[2].pixelStride
            var o = 0
            for (y in 0 until h) {
                val sy = y + r.top
                val yRow = sy * yRs
                val uRow = (sy shr 1) * uRs
                val vRow = (sy shr 1) * vRs
                for (x in 0 until w) {
                    val sx = x + r.left
                    val yy = ((yArr[yRow + sx * yPs].toInt() and 0xff) - 16).coerceAtLeast(0) * 1192
                    val u = (uArr[uRow + (sx shr 1) * uPs].toInt() and 0xff) - 128
                    val v = (vArr[vRow + (sx shr 1) * vPs].toInt() and 0xff) - 128
                    val rr = ((yy + 1634 * v) shr 10).coerceIn(0, 255)
                    val gg = ((yy - 833 * v - 400 * u) shr 10).coerceIn(0, 255)
                    val bb = ((yy + 2066 * u) shr 10).coerceIn(0, 255)
                    out[o++] = (0xff shl 24) or (rr shl 16) or (gg shl 8) or bb
                }
            }
        }
    }
}
