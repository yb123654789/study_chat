package com.studybot.app.sync

import com.studybot.app.data.Goal
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/** 一张计划卡：哪天、哪个目标、什么类型、多少分钟。 */
data class PlanCard(
    val day: String,
    val goalId: String,
    val title: String,
    val kind: String,
    val minutes: Int,
)

/**
 * 移植 `tools/planner.py` 的排程核心（手机端，纯函数，不碰数据库）。
 *
 * 口径：
 * - 剩余分钟 = 未完成目标的 `m` + 每张卡 10 分钟小测（planner.task_minutes）；
 * - 日期从 startDay 到 deadline，周末按 `weekendRatio/100` 加权（默认 1.3，只加权、不写死分钟）；
 * - 每天配额 = 剩余分钟 ÷ 剩余天数的权重和（落后的部分自动摊到后面的天）；
 * - 当天上限 = 配额 ÷ 0.85 + 最大一张卡（15% 缓冲 + 卡不能拆），`dayCap > 0` 时严格不超过；
 * - 按目标顺序切卡，不拆目标、一天不重复同一个目标；
 * - 当天装不下就顺延到下一天，到 deadline 还没排进去的算「排不下」，写进 note；
 * - 内容全排完后，空白天用复习卡填空（每天最多 3 张 / 45 分钟，周日留白）。
 */
object Planner {

    /** 15% 缓冲：当天配额再放宽一点（同 planner.BUFFER_RATE）。 */
    const val BUFFER_RATE = 0.15

    /** 一张学习卡 = 目标分钟 + 10 分钟当场小测（同 planner.task_minutes）。 */
    const val QUIZ_MIN = 10

    /** 每天至少留得下一张复习卡（同 planner.MIN_DAY_CAP）。 */
    const val MIN_DAY_CAP = 45

    /** 复习卡只是「填空」：一天最多 45 分钟 / 3 张（同 planner.REVIEW_FILL_MIN）。 */
    const val REVIEW_FILL_MIN = 45
    const val REVIEW_MIN = 15

    /** 周末系数缺省（同 db.DEFAULT_WEEKEND_RATIO）。 */
    private const val DEFAULT_WEEKEND_RATIO = 130

    /** 一张卡要多少分钟：学习 + 当场小测。应试目标的模考卡用同一口径。 */
    fun taskMinutes(g: Goal): Int = g.m + QUIZ_MIN

    /**
     * 排到 deadline。
     *
     * @param lostDays 模拟「已经浪费掉几天」（同 planner 的 lost_days），默认 0。
     * @return 卡片清单 + 一句说明；离考核日不足 3 天时清单为空。
     */
    fun schedule(
        goals: List<Goal>,
        startDay: String,
        deadline: String,
        weekendRatio: Int,
        dayCap: Int,
        lostDays: Int = 0,
    ): Pair<List<PlanCard>, String> {
        val start0 = parseDay(startDay)
            ?: return emptyList<PlanCard>() to "开始日期格式不对：$startDay"
        val end = parseDay(deadline)
            ?: return emptyList<PlanCard>() to "考核日格式不对：$deadline"
        val start = addDays(start0, lostDays.coerceAtLeast(0))
        val totalDays = daysInclusive(start, end)
        if (totalDays < 3) {
            return emptyList<PlanCard>() to "离考核日太近（只剩 $totalDays 天，至少留 3 天）"
        }
        if (totalDays > 1500) {
            return emptyList<PlanCard>() to "考核日太远（$totalDays 天），先检查日期"
        }

        val ratio = (if (weekendRatio <= 0) DEFAULT_WEEKEND_RATIO
        else weekendRatio.coerceIn(100, 200)) / 100.0

        // 目标顺序照文件/库里的顺序走；应试目标排到最后出「模考卡」。
        val active = goals.filter { !it.done }
        val queue = ArrayDeque<Goal>()
        for (g in active) if (g.ch != "应试") queue.addLast(g)
        for (g in active) if (g.ch == "应试") queue.addLast(g)
        if (queue.isEmpty()) return emptyList<PlanCard>() to "没有要排的目标"

        val days = (0 until totalDays).map { addDays(start, it) }
        val weights = days.map { if (isWeekend(it)) ratio else 1.0 }
        val weightAll = weights.sum().coerceAtLeast(0.0001)
        val unit = queue.sumOf { taskMinutes(it) } / weightAll
        val cardMax = queue.maxOf { taskMinutes(it) }

        val cards = ArrayList<PlanCard>()
        val learned = ArrayList<Goal>()
        var rot = 0
        var leftNeed = queue.sumOf { taskMinutes(it) }

        for (i in days.indices) {
            val day = days[i]
            val w = weights[i]
            // 剩下的还没学的量 ÷ 剩下的学习日权重：前面没排满，后面的天自己补上
            val leftWeight = weights.subList(i, weights.size).sum().coerceAtLeast(0.0001)
            val target = maxOf(unit * w, leftNeed / leftWeight * w)
            var cap = (target / (1.0 - BUFFER_RATE)).toInt() + cardMax
            if (dayCap > 0) {
                cap = minOf(cap, dayCap)
                cap = maxOf(minOf(MIN_DAY_CAP, dayCap), cap)
            }
            cap = maxOf(MIN_DAY_CAP, cap)

            val dayCards = ArrayList<PlanCard>()
            var used = 0
            // 排到「当天配额」就停（不是排到上限）；一张都没排时至少排一张
            while (queue.isNotEmpty() && (used.toDouble() < target || dayCards.isEmpty())) {
                val g = queue.first()
                val need = taskMinutes(g)
                // 卡不能拆：没设每日上限时单张比配额大也得排得下，否则会一直排不下
                if (used + need > cap && (dayCards.isNotEmpty() || dayCap > 0)) break
                queue.removeFirst()
                val kind = if (g.ch == "应试") "exam" else "learn"
                dayCards.add(PlanCard(dayStr(day), g.id, g.title, kind, need))
                used += need
                leftNeed -= need
                if (kind == "learn") learned.add(g)
            }

            // 内容全学完了才用滚动复习填空；周日留白
            if (dayCards.isEmpty() && queue.isEmpty() && !isSunday(day) && learned.isNotEmpty()) {
                var rused = 0
                while (rused < REVIEW_FILL_MIN && rused + REVIEW_MIN <= cap) {
                    val g = learned[rot % learned.size]
                    rot++
                    dayCards.add(PlanCard(dayStr(day), g.id, g.title, "review", REVIEW_MIN))
                    rused += REVIEW_MIN
                }
            }
            cards.addAll(dayCards)
        }

        val needMin = active.sumOf { taskMinutes(it) }
        val planned = cards.sumOf { it.minutes }
        val studyDays = cards.map { it.day }.distinct().size
        val perDay = if (studyDays > 0) Math.round(planned.toDouble() / studyDays).toInt() else 0
        val ratioText = String.format(Locale.US, "%.1f", ratio)
        val capText = if (dayCap > 0) "，每日上限 $dayCap 分钟" else ""
        var note = "剩 $needMin 分钟摊到 $totalDays 天，每天约 $perDay 分钟" +
            "（周末 ×${ratioText}${capText}，留 15% 余量）"
        if (queue.isNotEmpty()) {
            val core = queue.count { it.p == "core" }
            note += "；⚠️ 还有 ${queue.size} 个目标排不下（核心 $core 个），需要加时间或砍范围"
        }
        return cards to note
    }

    // ---------------------------------------------------------------- 日期
    // minSdk 24，不用 java.time；日历都挪到中午，避免夏令时边界把日期挪前后一天。
    private fun parser(): SimpleDateFormat =
        SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { isLenient = false }

    private fun parseDay(s: String): Date? = try {
        parser().parse(s.trim())
    } catch (_: Exception) {
        null
    }

    private fun dayStr(d: Date): String = parser().format(d)

    private fun addDays(d: Date, days: Int): Date {
        val c = Calendar.getInstance()
        c.time = d
        c.set(Calendar.HOUR_OF_DAY, 12)
        c.set(Calendar.MINUTE, 0)
        c.set(Calendar.SECOND, 0)
        c.set(Calendar.MILLISECOND, 0)
        c.add(Calendar.DAY_OF_MONTH, days)
        return c.time
    }

    private fun daysInclusive(from: Date, to: Date): Int {
        val a = Calendar.getInstance().apply {
            time = from
            set(Calendar.HOUR_OF_DAY, 12)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
        }
        val b = Calendar.getInstance().apply {
            time = to
            set(Calendar.HOUR_OF_DAY, 12)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
        }
        var n = 0
        while (!a.after(b) && n <= 1500) {
            n++
            a.add(Calendar.DAY_OF_MONTH, 1)
        }
        return n
    }

    private fun dow(d: Date): Int =
        Calendar.getInstance().apply { time = d }.get(Calendar.DAY_OF_WEEK)

    private fun isWeekend(d: Date): Boolean =
        dow(d) == Calendar.SATURDAY || dow(d) == Calendar.SUNDAY

    private fun isSunday(d: Date): Boolean = dow(d) == Calendar.SUNDAY
}
