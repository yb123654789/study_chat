package com.studybot.app.ui

import androidx.lifecycle.lifecycleScope
import com.studybot.app.R
import com.studybot.app.data.AppDb
import com.studybot.app.data.Goal
import com.studybot.app.data.QuizStat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 题库：每个目标的批次 / 对错 / 连错（不含题干）。 */
class QuizFragment : BaseListFragment() {

    override fun reload() {
        val ctx = context ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            val data = withContext(Dispatchers.IO) {
                val dao = AppDb.get(ctx).dao()
                Triple(dao.quizStats(), dao.goals(), dao.courses())
            }
            if (!viewAlive()) return@launch
            val content = binding.content
            content.removeAllViews()
            val goalsById: Map<String, Goal> = data.second.associateBy { it.id }
            val stats: List<QuizStat> = data.first
            val rows = stats.mapNotNull { s -> goalsById[s.goalId]?.let { it to s } }
                .sortedWith(compareBy<Pair<Goal, QuizStat>>({ it.first.course }, { it.first.seq }))
            if (rows.isEmpty()) {
                content.addEmpty(getString(R.string.empty_quiz))
                return@launch
            }
            val courseNames = data.third.associate { it.id to it.name }
            content.addSection("题库 · ${rows.size} 个目标有记录")
            for ((g, s) in rows) {
                val lines = mutableListOf(
                    "第 ${s.batch} 批 · 答对 ${s.passed}/${s.answered}",
                    "${courseNames[g.course] ?: g.course} · ${g.ch}",
                )
                lines.add(if (s.failStreak > 0) "连续没过 ${s.failStreak} 次" else "没有连续没过")
                content.addCard(
                    title = g.title,
                    lines = lines,
                    accent = if (s.failStreak >= 2) ctx.color(R.color.err) else 0,
                )
            }
        }
    }
}
