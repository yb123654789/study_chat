package com.studybot.app.sync

import android.content.Context
import android.util.Log
import androidx.room.withTransaction
import com.studybot.app.chat.ChatService
import com.studybot.app.data.AppDb
import com.studybot.app.data.Meta
import com.studybot.app.data.Task
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 落后自动重排（手机端）：口径照 `app/engine.py` 的 `overdue` / `auto_replan`。
 *
 * - 落后 = 今天之前还有 `state == "todo"` 的卡；或今天起一张卡都没有；
 * - 只改本地计划：删掉「今天及以后、todo」的卡（done / skipped / 过去卡保留），
 *   再按 [Planner.schedule] 重新插；插入前按 (date, goalId, kind) 查重（AppDb.taskAt）；
 * - 重排结果**不直接覆盖电脑**，写成一条 `replan` outbox，等电脑「待确认」决定；
 * - 同一天只重排一次（meta.replan_day），否则每次同步都会删了又插、反复进待确认；
 * - 返回说明文本（"剩 N 分钟摊到 M 天，每天约 X 分钟" + 落后几天 + 牺牲了什么），
 *   没重排返回 null。
 */
object Replan {

    private const val TAG = "Replan"

    suspend fun maybeReplan(ctx: Context): String? {
        val db = AppDb.get(ctx)
        val dao = db.dao()
        val today = today()
        val courses = dao.courses()
        val tasks = dao.tasksBetween("0000-01-01", "9999-12-31")

        // 当前课程：第一门课（没有课程时退回任务上的课）；截止日 = 课程的考核日
        val course = courses.firstOrNull()?.id ?: tasks.firstOrNull()?.course
        if (course.isNullOrBlank()) return null
        val c = courses.firstOrNull { it.id == course }
        val deadline = c?.examDate?.trim().orEmpty()
        if (deadline.isBlank()) return null
        if (dao.meta("replan_day") == today) return null

        val goals = dao.goals().filter { it.course == course && !it.done }
        if (goals.isEmpty()) return null

        val mine = tasks.filter { it.course == course }
        val open = mine.filter { it.state == "todo" }
        val overdue = open.filter { it.date < today }
        val future = open.filter { it.date >= today }
        // 不落后、未来还有卡：不动（engine.auto_replan 也是都不落后就返回 None）
        if (overdue.isEmpty() && future.isNotEmpty()) return null

        val beforeReview = future.count { it.kind == "review" }
        val (cards, planNote) = Planner.schedule(
            goals = goals,
            startDay = today,
            deadline = deadline,
            weekendRatio = c?.weekendRatio ?: 0,
            dayCap = c?.dayCap ?: 0,
        )
        if (cards.isEmpty()) {
            Log.w(TAG, "重排没排出卡：$planNote")
            return null
        }

        // 只改本地：一个事务里删旧卡 + 插新卡 + 记「今天已重排」
        val nowStr = ChatService.nowStr()
        db.withTransaction {
            dao.deleteOpenTasksFrom(today, course)
            for (card in cards) {
                // 插入前查重：同一天/同一目标/同一类型已经有卡（比如 done、skipped）就不再插
                if (dao.taskAt(card.day, card.goalId, card.kind) != null) continue
                // 本地 id 从固定基数起算，重启后可能和老卡撞号；撞了就取下一个
                var id = ChatService.newLocalId()
                while (dao.task(id) != null) id = ChatService.newLocalId()
                dao.putTask(
                    Task(
                        id = id,
                        date = card.day,
                        course = course,
                        goalId = card.goalId,
                        title = card.title,
                        kind = card.kind,
                        minutes = card.minutes,
                        state = "todo",
                        createdAt = nowStr,
                    )
                )
            }
            dao.putMeta(Meta("replan_day", today))
            dao.putMeta(Meta("replan_note", planNote))
        }

        // 说明：照 engine.auto_replan 的文案口径（落后几天 / 每天多少 / 复习卡牺牲 / 考核日不变）
        val lagDays = overdue.map { it.date }.distinct().size
        val lagDates = overdue.map { it.date }.distinct().sorted()
            .joinToString("、") { it.takeLast(5) }
        val name = c?.name?.ifBlank { course } ?: course
        val todayCards = cards.filter { it.day == today }
        val afterReview = cards.count { it.kind == "review" }
        val head = if (overdue.isNotEmpty()) {
            "${name}落后 $lagDays 天（$lagDates，${overdue.size} 张卡没做）"
        } else {
            "${name}今天起没有排卡"
        }
        var text = "$head，$planNote；今天 ${todayCards.size} 张 / " +
            "${todayCards.sumOf { it.minutes }} 分钟；复习卡 $beforeReview → $afterReview 张"
        if (afterReview < beforeReview) {
            text += "（砍掉 ${beforeReview - afterReview} 张复习，先保新课）"
        }
        text += "；考核日不变"

        // 重排结果作为建议上传，等电脑「待确认」决定；不直接覆盖电脑
        val arr = JSONArray()
        for (card in cards) {
            arr.put(
                JSONObject()
                    .put("day", card.day)
                    .put("goal_id", card.goalId)
                    .put("title", card.title)
                    .put("kind", card.kind)
                    .put("minutes", card.minutes)
                    .put("course", course)
            )
        }
        ChatService.putOutbox(
            ctx, "replan",
            JSONObject().put("note", text).put("tasks", arr)
        )
        Log.i(TAG, text)
        return text
    }

    private fun today(): String =
        SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
}
