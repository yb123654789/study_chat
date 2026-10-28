package com.studybot.app.ui

import androidx.lifecycle.lifecycleScope
import com.studybot.app.R
import com.studybot.app.data.AppDb
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 目标：按「课程 · 章节」分组，显示完成标记。 */
class GoalsFragment : BaseListFragment() {

    override fun reload() {
        val ctx = context ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            val data = withContext(Dispatchers.IO) {
                val dao = AppDb.get(ctx).dao()
                Pair(dao.goals(), dao.courses())
            }
            if (!viewAlive()) return@launch
            val content = binding.content
            content.removeAllViews()
            val goals = data.first
            if (goals.isEmpty()) {
                content.addEmpty(getString(R.string.empty_goals))
                return@launch
            }
            val courseNames = data.second.associate { it.id to it.name }
            content.addSection("目标 · ${goals.size} 个 · 完成 ${goals.count { it.done }} 个")

            var lastKey = ""
            for (g in goals) {
                val key = g.course + "|" + g.ch
                if (key != lastKey) {
                    content.addSection("${courseNames[g.course] ?: g.course} · ${g.ch.ifBlank { "未分章" }}")
                    lastKey = key
                }
                val mark = if (g.done) "✓" else "○"
                content.addCard(
                    title = "$mark ${g.title}",
                    lines = listOf("${g.m} 分钟 · ${if (g.p == "core") "核心" else "辅助"}"),
                    accent = if (g.done) ctx.color(R.color.secondary) else 0,
                )
            }
        }
    }
}
