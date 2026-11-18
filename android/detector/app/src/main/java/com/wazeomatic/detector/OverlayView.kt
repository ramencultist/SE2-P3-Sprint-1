/*
Module Name: OverlayView.kt
Description: Draws detection boxes, labels and the hood line over the camera or video preview

Class Name: OverlayView
Description: Custom view that paints the latest FrameResult

Inputs: FrameResults from PipelineBus
Outputs: Boxes and labels drawn on screen
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the preview overlay
How you validated and revised the AI output: Proofreading, and checking the overlay on the emulator in video mode
The challenges or limitations you faced while using AI: Keeping the Kotlin and Python versions identical
Authors: Blake Pennel
Creation Date: 11/5/2026

Ported to the SE2-P3 repository 11/17/2026 by Luke Reicherter with Claude Opus 5.5 for Sprint 2 (requirements 7 and 9); code unchanged from the team's wazeomatic development repository
*/

package com.wazeomatic.detector

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.view.View
import java.util.Locale

/** Shows the last analysed frame (fit-center) with detection boxes on top. */
class OverlayView(ctx: Context) : View(ctx) {
    private var frame: Bitmap? = null
    private var dets: List<Detection> = emptyList()
    var message: String = ""
        set(v) { field = v; invalidate() }

    private val box = Paint().apply { style = Paint.Style.STROKE; strokeWidth = 4f; isAntiAlias = true }
    private val text = Paint().apply { color = Color.WHITE; textSize = 32f; isAntiAlias = true }
    private val textBg = Paint().apply { style = Paint.Style.FILL }
    private val bmpPaint = Paint(Paint.FILTER_BITMAP_FLAG)
    private val dst = RectF()

    fun show(bmp: Bitmap, detections: List<Detection>) {
        frame = bmp
        dets = detections
        invalidate()
    }

    override fun onDraw(c: Canvas) {
        c.drawColor(Color.BLACK)
        val b = frame
        if (b == null) {
            if (message.isNotEmpty()) c.drawText(message, 24f, height / 2f, text)
            return
        }
        val s = minOf(width.toFloat() / b.width, height.toFloat() / b.height)
        val w = b.width * s
        val h = b.height * s
        val ox = (width - w) / 2
        val oy = (height - h) / 2
        dst.set(ox, oy, ox + w, oy + h)
        c.drawBitmap(b, null, dst, bmpPaint)
        for (d in dets) {
            val col = if (d.model == "hazard") Color.rgb(255, 140, 0) else Color.rgb(0, 200, 255)
            box.color = col
            textBg.color = col
            val l = ox + (d.bbox.cx - d.bbox.w / 2) * w
            val t = oy + (d.bbox.cy - d.bbox.h / 2) * h
            val r = ox + (d.bbox.cx + d.bbox.w / 2) * w
            val btm = oy + (d.bbox.cy + d.bbox.h / 2) * h
            c.drawRect(l, t, r, btm, box)
            val label = String.format(Locale.US, "%s %.2f", d.label, d.confidence)
            val tw = text.measureText(label)
            val ty = maxOf(t, 36f)
            c.drawRect(l, ty - 34f, l + tw + 12f, ty, textBg)
            c.drawText(label, l + 6f, ty - 8f, text)
        }
    }
}
