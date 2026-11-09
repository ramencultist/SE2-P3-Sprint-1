/*
Module Name: Ui.kt
Description: Views and helpers that make the mock app look like Waze

Class Name: Palette
Description: Waze colours sampled from the 2024 app screenshots

Class Name: Tile
Description: Round report tile with an icon, label and selected state

Class Name: ReportButton
Description: Report button that fills while the auto-send countdown runs

Class Name: SpeedometerView
Description: Round speedometer shown on the map

Class Name: FakeMapView
Description: Static look-alike of the idle Waze map

Inputs: Layout parameters from MainActivity
Outputs: The views on the mock Waze screen
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to draw the Waze-style views in code
How you validated and revised the AI output: Proofreading, and comparison with reference screenshots of the real app
The challenges or limitations you faced while using AI: Matching tile size, spacing and colours from screenshots
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.mockwaze

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView

fun Context.dp(v: Int): Int = (v * resources.displayMetrics.density + 0.5f).toInt()
fun Context.dpf(v: Float): Float = v * resources.displayMetrics.density

/** Waze palette (sampled from the 2024 app screenshots). */
/** Waze palette, sampled pixel by pixel from the 2024 Android app screenshots. */
object Palette {
    const val INK = 0xFF202124.toInt()          // titles and labels
    const val BLUE = 0xFF038BE6.toInt()         // Report button
    const val BLUE_DARK = 0xFF0268B8.toInt()    // auto-send progress fill (left part of Report)
    const val SELECT = 0xFF0275E4.toInt()       // selection ring, check badge, Cancel text
    const val TEAL = 0xFF00B5CD.toInt()         // car arrow
    const val DISC = 0xFFCCD5DD.toInt()         // disc behind the arrow
    const val TILE_BG = 0xFFF2F4F7.toInt()      // round tile background
    const val CANCEL_BG = 0xFFF2F4F7.toInt()
    const val MAP_BG = 0xFFFAFAFA.toInt()
    const val ROAD = 0xFFCDD0C8.toInt()
    const val ROAD_MAJOR = 0xFFCDD0C8.toInt()
    const val PINK = 0xFFFF4499.toInt()
    const val SPEED_BG = 0xFF3D434A.toInt()
    const val SPEED_RING = 0xFF5B626A.toInt()
}

fun roundRect(color: Int, radiusPx: Float) = GradientDrawable().apply {
    setColor(color)
    cornerRadius = radiusPx
}

fun oval(color: Int) = GradientDrawable().apply {
    shape = GradientDrawable.OVAL
    setColor(color)
}

/**
 * Round light-grey tile with an icon and a label underneath, like the Waze report tiles.
 * Selected tiles get the blue ring and the blue check badge in the top-right corner.
 */
class Tile(ctx: Context, val label: String, icon: Int, circleDp: Float = 73.4f, iconDp: Int = 66) : LinearLayout(ctx) {
    private val ring: View
    private val badge: ImageView
    private val circle: View
    private val img: ImageView

    init {
        orientation = VERTICAL
        gravity = Gravity.CENTER_HORIZONTAL
        setPadding(0, ctx.dp(6), 0, ctx.dp(9))   // 120 dp row pitch like Waze
        isClickable = true
        isFocusable = true
        contentDescription = label
        // no ripple: Waze's tiles only show the blue ring, and the borderless ripple would
        // draw a grey disc the size of the whole tile

        val d = (ctx.dpf(circleDp) + 0.5f).toInt()
        val holder = FrameLayout(ctx)
        circle = View(ctx).apply { background = oval(Palette.TILE_BG) }
        holder.addView(circle, FrameLayout.LayoutParams(d, d, Gravity.CENTER))
        img = ImageView(ctx).apply {
            setImageResource(icon)
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        }
        holder.addView(img, FrameLayout.LayoutParams(ctx.dp(iconDp), ctx.dp(iconDp), Gravity.CENTER))
        ring = View(ctx).apply {
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(Color.TRANSPARENT)
                setStroke(ctx.dp(2), Palette.SELECT)
            }
            visibility = View.INVISIBLE
        }
        holder.addView(ring, FrameLayout.LayoutParams(d, d, Gravity.CENTER))
        badge = ImageView(ctx).apply {
            setImageResource(R.drawable.ic_check)
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(Palette.SELECT)
                setStroke(ctx.dp(2), Color.WHITE)
            }
            setPadding(ctx.dp(5), ctx.dp(5), ctx.dp(5), ctx.dp(5))
            visibility = View.INVISIBLE
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        }
        // badge centre on the ring at 45 degrees up-right
        val b = ctx.dp(22)
        val off = (d / 2f * 0.707f).toInt()
        // FrameLayout shifts a centred child by its full margin, so the margin is the offset itself
        holder.addView(badge, FrameLayout.LayoutParams(b, b, Gravity.CENTER).apply {
            leftMargin = off; bottomMargin = off
        })
        val holderSize = d + ctx.dp(8)
        addView(holder, LayoutParams(holderSize, holderSize))

        val text = TextView(ctx).apply {
            this.text = label
            gravity = Gravity.CENTER
            setTextColor(Palette.INK)
            textSize = 13f
            maxLines = 2
            includeFontPadding = false
        }
        addView(text, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply { topMargin = ctx.dp(6) })
    }

    /** Waze keeps the ring at the tile size and shrinks the grey disc and icon inside it. */
    fun setChosen(chosen: Boolean) {
        isSelected = chosen
        ring.visibility = if (chosen) View.VISIBLE else View.INVISIBLE
        badge.visibility = if (chosen) View.VISIBLE else View.INVISIBLE
        val k = if (chosen) 0.86f else 1f
        circle.scaleX = k; circle.scaleY = k
        img.scaleX = k; img.scaleY = k
    }
}

/**
 * The blue "Report" button of the confirm sheet. Waze sends the report by itself after a few
 * seconds; while it waits, the left part of the button fills with a darker blue. progress is
 * 0..1 of that countdown.
 */
class ReportButton(ctx: Context) : TextView(ctx) {
    private val base = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Palette.BLUE }
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Palette.BLUE_DARK }
    private val clip = Path()
    private val rect = RectF()
    var progress = 0f
        set(v) { field = v.coerceIn(0f, 1f); invalidate() }

    init {
        text = "Report"
        contentDescription = "Report"
        textSize = 17f
        typeface = Typeface.DEFAULT_BOLD
        gravity = Gravity.CENTER
        setTextColor(Color.WHITE)
        isClickable = true
        isFocusable = true
    }

    override fun onDraw(c: Canvas) {
        val r = height / 2f
        rect.set(0f, 0f, width.toFloat(), height.toFloat())
        clip.reset()
        clip.addRoundRect(rect, r, r, Path.Direction.CW)
        c.save()
        c.clipPath(clip)
        c.drawRect(rect, base)
        if (progress > 0f) c.drawRect(0f, 0f, width * progress, height.toFloat(), fill)
        c.restore()
        super.onDraw(c)
    }
}

/** Dark round speedometer, bottom-left of the Waze map. */
class SpeedometerView(ctx: Context) : View(ctx) {
    private val bg = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Palette.SPEED_BG }
    private val ticks = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Palette.SPEED_RING; style = Paint.Style.STROKE
    }
    private val big = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE; typeface = Typeface.DEFAULT_BOLD; textAlign = Paint.Align.CENTER
    }
    private val small = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE; textAlign = Paint.Align.CENTER }

    init {
        contentDescription = "0 MPH"
        elevation = ctx.dpf(6f)
        outlineProvider = null
    }

    override fun onDraw(c: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val r = width / 2f
        c.drawCircle(cx, cy, r, bg)
        ticks.strokeWidth = context.dpf(3f)
        ticks.pathEffect = DashPathEffect(floatArrayOf(context.dpf(1.6f), context.dpf(2.6f)), 0f)
        c.drawCircle(cx, cy, r - context.dpf(5f), ticks)
        big.textSize = context.dpf(27f)
        small.textSize = context.dpf(10.5f)
        c.drawText("0", cx, cy + context.dpf(3f), big)
        c.drawText("MPH", cx, cy + context.dpf(17f), small)
    }
}

/**
 * Static look-alike of the idle Waze map: near-white background, soft grey roads without
 * outlines, blurred-looking street names along the roads and the teal arrow in a grey disc.
 */
class FakeMapView(ctx: Context) : View(ctx) {
    private val bg = Paint().apply { color = Palette.MAP_BG }
    private val road = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Palette.ROAD; style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND
    }
    private val major = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Palette.ROAD_MAJOR; style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND
    }
    private val label = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFF6F7378.toInt(); textAlign = Paint.Align.CENTER
    }
    private val disc = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Palette.DISC }
    private val arrow = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Palette.TEAL }

    init {
        contentDescription = "Map"
    }

    private fun roadLabel(c: Canvas, text: String, x: Float, y: Float, angle: Float) {
        c.save()
        c.rotate(angle, x, y)
        c.drawText(text, x, y + label.textSize / 3f, label)
        c.restore()
    }

    override fun onDraw(c: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        c.drawRect(0f, 0f, w, h, bg)
        road.strokeWidth = context.dpf(13.9f)
        major.strokeWidth = context.dpf(19.7f)

        // a loose grid like a residential area: two verticals, a few horizontals, one curve
        val v1 = w * 0.23f
        val v2 = w * 0.62f
        c.drawLine(v1, h * 0.28f, v1 + w * 0.02f, h, road)
        c.drawLine(v2, 0f, v2 - w * 0.01f, h, major)
        c.drawLine(0f, h * 0.10f, w, h * 0.12f, road)
        val curve = Path().apply {
            moveTo(0f, h * 0.29f)
            quadTo(w * 0.5f, h * 0.30f, w, h * 0.265f)
        }
        c.drawPath(curve, major)
        c.drawLine(v2, h * 0.55f, w * 0.80f, h * 0.55f, road)
        val hook = Path().apply {
            moveTo(w * 0.80f, h * 0.55f)
            quadTo(w * 0.86f, h * 0.55f, w * 0.84f, h * 0.585f)
        }
        c.drawPath(hook, road)
        c.drawLine(0f, h * 0.62f, v1 + w * 0.02f, h * 0.62f, road)
        c.drawLine(v1 + w * 0.02f, h * 0.84f, w, h * 0.86f, road)

        label.textSize = context.dpf(11f)
        roadLabel(c, "N Mississippi St", v2 - w * 0.005f, h * 0.18f, -90f)
        roadLabel(c, "Crestline Dr", v1 + w * 0.005f, h * 0.47f, -90f)
        roadLabel(c, "W 9th St", w * 0.50f, h * 0.283f, 0f)
        roadLabel(c, "Stratford Rd", w * 0.72f, h * 0.44f, 0f)
        roadLabel(c, "W 15th St", w * 0.40f, h * 0.85f, 1f)

        // car arrow
        val cx = w * 0.5f
        val cy = h * 0.55f
        val r = context.dpf(25f)
        c.drawCircle(cx, cy, r, disc)
        val a = Path().apply {
            moveTo(cx, cy - r * 0.62f)
            lineTo(cx + r * 0.45f, cy + r * 0.42f)
            lineTo(cx, cy + r * 0.18f)
            lineTo(cx - r * 0.45f, cy + r * 0.42f)
            close()
        }
        c.drawPath(a, arrow)
    }
}
