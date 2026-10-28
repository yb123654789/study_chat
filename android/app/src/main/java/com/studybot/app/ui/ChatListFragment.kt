package com.studybot.app.ui

import android.content.Intent
import androidx.lifecycle.lifecycleScope
import com.studybot.app.ChatActivity
import com.studybot.app.R
import com.studybot.app.data.AppDb
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 会话列表：一张卡一条 + 自由问答（和电脑同一份库，P3 才回传）。 */
class ChatListFragment : BaseListFragment() {

    override fun reload() {
        val ctx = context ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            val convs = withContext(Dispatchers.IO) { AppDb.get(ctx).dao().conversations() }
            if (!viewAlive()) return@launch
            val content = binding.content
            content.removeAllViews()
            content.addSection("会话")

            content.addCard(
                title = "＋ 自由问答",
                lines = listOf("不绑定学习卡，随便问"),
                onClick = {
                    ctx.startActivity(
                        Intent(ctx, ChatActivity::class.java)
                            .putExtra(ChatActivity.EXTRA_TITLE, "自由问答")
                    )
                },
            )
            if (convs.isEmpty()) {
                content.addMuted("还没有会话。打开一张卡，或从上面的「自由问答」开始。")
            }
            for (c in convs) {
                val sub = if (c.kind == "task") "学习卡会话" else "自由问答"
                content.addCard(
                    title = c.title.ifBlank { "会话 #${c.id}" },
                    lines = listOf(sub, "最近：${c.lastActive.ifBlank { c.createdAt }}"),
                    onClick = {
                        val i = Intent(ctx, ChatActivity::class.java)
                            .putExtra(ChatActivity.EXTRA_CONV, c.id)
                            .putExtra(ChatActivity.EXTRA_TASK, c.taskId ?: 0L)
                            .putExtra(ChatActivity.EXTRA_GOAL, c.goalId)
                            .putExtra(ChatActivity.EXTRA_TITLE, c.title)
                        ctx.startActivity(i)
                    },
                )
            }
        }
    }
}
