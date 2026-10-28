package com.studybot.app.ui

import android.content.Context
import android.graphics.Typeface
import android.view.Gravity
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import com.google.android.material.card.MaterialCardView
import com.studybot.app.R
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

// 页面上的小积木：标题、卡片、空状态。全部用代码拼，省掉一堆 XML。

fun Context.dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

fun Context.color(id: Int): Int = ContextCompat.getColor(this, id)

fun LinearLayout.addSection(text: String) {
    addView(
        TextView(context).apply {
            this.text = text
            textSize = 13.5f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(context.color(R.color.text))
            setPadding(context.dp(4), context.dp(12), context.dp(4), context.dp(6))
        }
    )
}

fun LinearLayout.addCard(title: String, lines: List<String>, accent: Int = 0, onClick: (() -> Unit)? = null) {
    val c = context
    val card = MaterialCardView(c).apply {
        layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply {
            bottomMargin = c.dp(8)
        }
        radius = c.dp(12).toFloat()
        cardElevation = 0f
        strokeWidth = c.dp(1)
        strokeColor = c.color(R.color.line)
        setCardBackgroundColor(c.color(R.color.card))
        if (onClick != null) {
            isClickable = true
            isFocusable = true
            setOnClickListener { onClick() }
        }
    }
    val box = LinearLayout(c).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(c.dp(14), c.dp(11), c.dp(14), c.dp(11))
    }
    box.addView(
        TextView(c).apply {
            text = title
            textSize = 15f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(if (accent != 0) accent else c.color(R.color.text))
        }
    )
    for (line in lines) {
        if (line.isBlank()) continue
        box.addView(
            TextView(c).apply {
                text = line
                textSize = 12f
                setTextColor(c.color(R.color.muted))
                setPadding(0, c.dp(2), 0, 0)
            }
        )
    }
    card.addView(box)
    addView(card)
}

fun LinearLayout.addEmpty(text: String) {
    addView(
        TextView(context).apply {
            this.text = text
            textSize = 13f
            gravity = Gravity.CENTER_HORIZONTAL
            setTextColor(context.color(R.color.muted))
            setPadding(16, context.dp(40), 16, context.dp(40))
        }
    )
}

fun LinearLayout.addMuted(text: String) {
    addView(
        TextView(context).apply {
            this.text = text
            textSize = 12f
            setTextColor(context.color(R.color.muted))
            setPadding(context.dp(8), context.dp(2), context.dp(8), context.dp(10))
        }
    )
}

/** 日期都按手机本地时区；服务器用 Asia/Shanghai，国内一致。 */
object Dates {
    private fun fmt() = SimpleDateFormat("yyyy-MM-dd", Locale.US)

    fun today(): String = fmt().format(Date())

    fun parse(date: String): Date? = try {
        fmt().parse(date)
    } catch (_: Exception) {
        null
    }

    fun addDays(date: String, days: Int): String {
        val c = Calendar.getInstance()
        c.time = parse(date) ?: Date()
        c.add(Calendar.DAY_OF_MONTH, days)
        return fmt().format(c.time)
    }

    fun label(date: String): String {
        if (date == today()) return "今天"
        if (date == addDays(today(), 1)) return "明天"
        val c = Calendar.getInstance()
        c.time = parse(date) ?: Date()
        val m = c.get(Calendar.MONTH) + 1
        val d = c.get(Calendar.DAY_OF_MONTH)
        val week = "日一二三四五六"[c.get(Calendar.DAY_OF_WEEK) - 1]
        return "${m}月${d}日 周$week"
    }
}
