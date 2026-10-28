package com.studybot.app.ui

import android.content.Intent
import androidx.lifecycle.lifecycleScope
import com.studybot.app.NoteEditActivity
import com.studybot.app.data.Note
import com.studybot.app.data.NoteRepo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** P5 笔记列表：按记下的时间倒序，一张卡一条；点开可改。 */
class NotesFragment : BaseListFragment() {

    override fun reload() {
        val ctx = context ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            val notes = withContext(Dispatchers.IO) { NoteRepo.list(ctx) }
            if (!viewAlive()) return@launch
            val content = binding.content
            content.removeAllViews()
            content.addSection(if (notes.isEmpty()) "笔记" else "笔记（${notes.size} 条）")

            content.addCard(
                title = "＋ 新笔记",
                lines = listOf("打字、说话、拍课件都行"),
                onClick = {
                    ctx.startActivity(Intent(ctx, NoteEditActivity::class.java))
                },
            )

            if (notes.isEmpty()) {
                content.addMuted("还没有笔记。老师说的、自己想到的、课件上的，都可以先记下来。")
                return@launch
            }
            for (n in notes) {
                content.addCard(
                    title = titleOf(n),
                    lines = listOf(metaOf(n), "时间：${n.createdAt.ifBlank { "未知" }}"),
                    onClick = {
                        ctx.startActivity(
                            Intent(ctx, NoteEditActivity::class.java)
                                .putExtra(NoteEditActivity.EXTRA_UID, n.uid)
                        )
                    },
                )
            }
        }
    }

    /** 正文前 60 字做标题（换行压成空格，免得卡片被撑开）。 */
    private fun titleOf(n: Note): String {
        val body = n.text.replace(Regex("\\s+"), " ").trim()
        if (body.isEmpty()) return "（空白笔记）"
        return if (body.length <= 60) body else body.take(60) + "…"
    }

    private fun metaOf(n: Note): String {
        val tag = when (n.tag) {
            "teacher" -> "老师说"
            "slide" -> "抄课件"
            else -> "自己理解"
        }
        val src = when (n.src) {
            "voice" -> "语音"
            "photo" -> "拍照"
            else -> "打字"
        }
        val state = if (n.pending) " · 待同步" else ""
        return "$tag · $src$state"
    }
}
