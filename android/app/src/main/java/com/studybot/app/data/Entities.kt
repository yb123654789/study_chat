package com.studybot.app.data

import androidx.room.Entity
import androidx.room.PrimaryKey

/** 课程：每门课自己的考核日与容量（手机端只读镜像）。 */
@Entity(tableName = "courses")
data class Course(
    @PrimaryKey val id: String,
    val name: String,
    val examDate: String,
    val weekendRatio: Int,
    val dayCap: Int,
    val archived: Int,
)

@Entity(tableName = "goals")
data class Goal(
    @PrimaryKey val id: String,
    val course: String,
    val ch: String,
    val title: String,
    val p: String,
    val m: Int,
    val d: String,
    val cp: String,
    val seq: Int,
    val done: Boolean,
)

@Entity(tableName = "tasks")
data class Task(
    @PrimaryKey val id: Long,
    val date: String,
    val course: String,
    val goalId: String,
    val title: String,
    val kind: String,
    val minutes: Int,
    val state: String,
    val createdAt: String,
)

/** 每个目标最新一批的对错与连错次数（不含题干）。 */
@Entity(tableName = "quiz_stats")
data class QuizStat(
    @PrimaryKey val goalId: String,
    val batch: Int,
    val answered: Int,
    val passed: Int,
    val failStreak: Int,
)

@Entity(tableName = "conversations")
data class Conversation(
    @PrimaryKey val id: Long,
    val kind: String,
    val taskId: Long?,
    val goalId: String,
    val title: String,
    val createdAt: String,
    val lastActive: String,
)

@Entity(tableName = "messages")
data class Message(
    @PrimaryKey val id: Long,
    val convId: Long,
    val role: String,
    val text: String,
    val sources: String,
    val tokens: Int,
    val createdAt: String,
)

/** 资料块：id 由「文件+锚点」定，fp = sha1(text)[:16]（服务器算好给手机）。 */
@Entity(tableName = "blocks")
data class Block(
    @PrimaryKey val id: String,
    val course: String,
    val src: String,
    val anchor: String,
    val text: String,
    val fp: String,
)

/** 笔记：和资料分开存，不进资料检索索引（P5 用）。 */
@Entity(tableName = "notes")
data class Note(
    @PrimaryKey val uid: String,
    val id: Long? = null,
    val course: String = "",
    val goalId: String = "",
    val taskId: Long? = null,
    val text: String = "",
    val rawText: String = "",
    val src: String = "text",
    val mediaPath: String = "",
    val createdAt: String = "",
    val tag: String = "self",
    val trust: Int = 0,
    val pending: Boolean = true,
)

/** 冲突裁定：存当时的说法快照（P7 用）。 */
@Entity(tableName = "arbitrations")
data class Arbitration(
    @PrimaryKey val uid: String,
    val id: Long? = null,
    val course: String = "",
    val goalId: String = "",
    val concept: String = "",
    val materialClaim: String = "",
    val materialSrc: String = "",
    val noteId: String = "",
    val noteClaim: String = "",
    val verdict: String = "",
    val decidedAt: String = "",
    val decidedBy: String = "",
    val hits: Int = 0,
)

/** 短期数据队列：手机产生的操作先放这儿，P3 才开始上传。 */
@Entity(tableName = "outbox")
data class OutboxItem(
    @PrimaryKey val uid: String,
    val kind: String,
    val payload: String,
    val createdAt: String,
    val pending: Boolean = true,
    val underReview: Boolean = false,
)

/** 下行带来的待确认项（只读展示；处理在电脑上）。 */
@Entity(tableName = "review")
data class Review(
    @PrimaryKey val id: Long,
    val kind: String,
    val taskId: Long?,
    val goalId: String,
    val day: String,
    val phoneState: String,
    val serverState: String,
    val reason: String,
    val payloadJson: String,
    val createdAt: String,
    val resolvedAt: String,
    val resolution: String,
)

/** rev / blocks_rev / last_sync / server_url / token（方案 §P1）。 */
@Entity(tableName = "meta")
data class Meta(
    @PrimaryKey val key: String,
    val value: String,
)
