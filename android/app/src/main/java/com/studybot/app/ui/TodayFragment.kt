package com.studybot.app.ui

import android.content.Context
import androidx.appcompat.app.AlertDialog
import androidx.lifecycle.lifecycleScope
import com.studybot.app.R
import com.studybot.app.data.AppDb
import com.studybot.app.data.Review
import com.studybot.app.data.Task
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 今日：卡片列表（标题、分钟、状态、目标）；有未处理的待确认项时卡片标 ⚠ 并弹一次。 */
class TodayFragment : BaseListFragment() {

    private var reviewDialogShowing = false

    override fun onResume() {
        super.onResume()
        // 从小测页回来要能看到 done 和待确认（MainActivity 也会 reload，重复查库无害）
        reload()
    }

    override fun reload() {
        val ctx = context ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            // 查库一律后台线程，回主线程再拼 UI
            val data = withContext(Dispatchers.IO) {
                val dao = AppDb.get(ctx).dao()
                TodayData(
                    tasks = dao.tasksOn(Dates.today()),
                    courseNames = dao.courses().associate { it.id to it.name },
                    goalTitles = dao.goals().associate { it.id to it.title },
                    // 只有挂在具体卡片上的待确认项才和今日列表对得上
                    reviews = dao.reviews().filter { it.taskId != null },
                )
            }
            if (!viewAlive()) return@launch
            val content = binding.content
            content.removeAllViews()

            val pendingTaskIds = data.reviews
                .filter { it.resolvedAt.isBlank() }
                .mapNotNull { it.taskId }
                .toSet()

            val tasks = data.tasks
            if (tasks.isEmpty()) {
                content.addEmpty(getString(R.string.empty_today))
            } else {
                content.addSection("今天 · ${tasks.size} 张 · ${tasks.sumOf { it.minutes }} 分钟")
                for (t in tasks) {
                    val pending = t.id in pendingTaskIds
                    val (state, accent) = when (t.state) {
                        "done" -> "✅ 已完成" to ctx.color(R.color.secondary)
                        "skipped" -> "⏭ 已跳过" to ctx.color(R.color.muted)
                        else -> "▶ 待做" to ctx.color(R.color.primary)
                    }
                    content.addCard(
                        title = t.title,
                        lines = listOf(
                            "$state · ${data.courseNames[t.course] ?: t.course} · ${t.kind} · ${t.minutes} 分钟" +
                                if (pending) " · ⚠ 待确认" else "",
                            data.goalTitles[t.goalId] ?: t.goalId,
                        ),
                        accent = if (pending) ctx.color(R.color.warn) else accent,
                        onClick = {
                            ctx.startActivity(
                                android.content.Intent(ctx, com.studybot.app.ChatActivity::class.java)
                                    .putExtra(com.studybot.app.ChatActivity.EXTRA_TASK, t.id)
                                    .putExtra(com.studybot.app.ChatActivity.EXTRA_GOAL, t.goalId)
                                    .putExtra(com.studybot.app.ChatActivity.EXTRA_TITLE, t.title)
                            )
                        },
                    )
                }
            }

            maybeShowReview(ctx, data.reviews)
        }
    }

    /** 有未处理且没弹过的 review：只弹最早一条；点掉后写 SharedPreferences，不再弹。 */
    private fun maybeShowReview(ctx: Context, reviews: List<Review>) {
        if (reviewDialogShowing) return
        val prefs = ctx.getSharedPreferences(PREFS_UI, Context.MODE_PRIVATE)
        val next = reviews
            .filter { it.resolvedAt.isBlank() }
            .sortedBy { it.id }
            .firstOrNull { !prefs.getBoolean(keyOf(it.id), false) } ?: return
        reviewDialogShowing = true
        val reason = next.reason.ifBlank { "（没有写明原因）" }
        AlertDialog.Builder(ctx)
            .setTitle("这张卡待确认")
            .setMessage("$reason\n\n手机/电脑判定不一致，去电脑「待确认」页定")
            .setCancelable(false)
            .setPositiveButton("知道了") { _, _ ->
                prefs.edit().putBoolean(keyOf(next.id), true).apply()
            }
            .setOnDismissListener { reviewDialogShowing = false }
            .show()
    }

    private fun keyOf(id: Long) = "review_$id"

    companion object {
        private const val PREFS_UI = "studybot_ui"
    }
}

private data class TodayData(
    val tasks: List<Task>,
    val courseNames: Map<String, String>,
    val goalTitles: Map<String, String>,
    val reviews: List<Review>,
)
