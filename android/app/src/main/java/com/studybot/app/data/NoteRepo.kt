package com.studybot.app.data

import android.content.Context
import com.studybot.app.chat.ChatService
import org.json.JSONObject

/**
 * P5 笔记落库：一条 note 既写 notes 表（本地立刻可见），也往 outbox 塞一条 note 事件
 * （等 P3 的上行通道把它带走，同步失败也不丢）。
 *
 * 事件 uid 刻意用笔记 uid：同一条笔记反复保存只会覆盖同一条待发事件，不会堆一串。
 */
object NoteRepo {

    suspend fun list(ctx: Context): List<Note> = AppDb.get(ctx).dao().notes()

    suspend fun get(ctx: Context, uid: String): Note? = AppDb.get(ctx).dao().note(uid)

    suspend fun save(ctx: Context, note: Note): Note {
        val dao = AppDb.get(ctx).dao()
        val createdAt = note.createdAt.ifBlank { ChatService.nowStr() }
        val saved = note.copy(createdAt = createdAt, pending = true)
        dao.putNote(saved)

        val payload = JSONObject()
            .put("uid", saved.uid)
            .put("kind", "note")
            .put("course", saved.course)
            .put("goal_id", saved.goalId)
            .put("text", saved.text)
            .put("raw_text", saved.rawText)
            .put("src", saved.src)
            .put("tag", saved.tag)
        dao.putOutbox(
            OutboxItem(
                uid = saved.uid,
                kind = "note",
                payload = payload.toString(),
                createdAt = createdAt,
                pending = true,
                underReview = false,
            )
        )
        return saved
    }
}
