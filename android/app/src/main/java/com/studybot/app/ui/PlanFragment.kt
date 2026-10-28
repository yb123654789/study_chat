package com.studybot.app.ui

import androidx.lifecycle.lifecycleScope
import com.studybot.app.R
import com.studybot.app.data.AppDb
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 计划：未来 14 天，按天分组。 */
class PlanFragment : BaseListFragment() {

    override fun reload() {
        val ctx = context ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            val today = Dates.today()
            val data = withContext(Dispatchers.IO) {
                val dao = AppDb.get(ctx).dao()
                Pair(
                    dao.tasksBetween(today, Dates.addDays(today, 13)),
                    dao.courses(),
                )
            }
            if (!viewAlive()) return@launch
            val content = binding.content
            content.removeAllViews()
            val tasks = data.first
            val courseNames = data.second.associate { it.id to it.name }
            val byDate = tasks.groupBy { it.date }

            content.addSection("未来 14 天 · ${tasks.size} 张 · ${tasks.sumOf { it.minutes }} 分钟")
            for (i in 0..13) {
                val date = Dates.addDays(today, i)
                val list = byDate[date].orEmpty()
                content.addSection("${Dates.label(date)} · ${list.size} 张")
                if (list.isEmpty()) {
                    if (i == 0) content.addEmpty(getString(R.string.empty_plan)) else content.addMuted("没有排课")
                    continue
                }
                for (t in list) {
                    val state = when (t.state) {
                        "done" -> "✅ 已完成"
                        "skipped" -> "⏭ 已跳过"
                        else -> "▶ 待做"
                    }
                    content.addCard(
                        title = t.title,
                        lines = listOf(
                            "$state · ${courseNames[t.course] ?: t.course} · ${t.minutes} 分钟",
                        ),
                    )
                }
            }
        }
    }
}
