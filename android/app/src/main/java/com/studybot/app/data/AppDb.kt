package com.studybot.app.data

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase

@Dao
interface AppDao {
    // ---------------------------------------------------------------- 读
    @Query("SELECT * FROM tasks WHERE date = :date AND state != 'dropped' ORDER BY state, id")
    suspend fun tasksOn(date: String): List<Task>

    @Query("SELECT * FROM tasks WHERE date BETWEEN :from AND :to AND state != 'dropped' ORDER BY date, id")
    suspend fun tasksBetween(from: String, to: String): List<Task>

    @Query("SELECT * FROM goals ORDER BY course, seq")
    suspend fun goals(): List<Goal>

    @Query("SELECT * FROM courses ORDER BY id")
    suspend fun courses(): List<Course>

    @Query("SELECT * FROM quiz_stats")
    suspend fun quizStats(): List<QuizStat>

    @Query("SELECT * FROM review ORDER BY id DESC")
    suspend fun reviews(): List<Review>

    @Query("SELECT * FROM blocks")
    suspend fun blocks(): List<Block>

    @Query("SELECT value FROM meta WHERE `key` = :key")
    suspend fun meta(key: String): String?

    @Query("SELECT * FROM meta")
    suspend fun metaAll(): List<Meta>

    @Query("SELECT * FROM outbox WHERE pending = 1 ORDER BY createdAt")
    suspend fun pendingOutbox(): List<OutboxItem>

    // 笔记：和资料分开存，不进检索（P5）
    @Query("SELECT * FROM notes ORDER BY createdAt DESC")
    suspend fun notes(): List<Note>

    @Query("SELECT * FROM notes WHERE uid = :uid")
    suspend fun note(uid: String): Note?

    /** P7 上下文：当前目标最近 5 条笔记（只取绑了目标的）。 */
    @Query("SELECT * FROM notes WHERE goalId=:gid AND goalId!='' ORDER BY createdAt DESC LIMIT 5")
    suspend fun notesForGoal(gid: String): List<Note>

    /** P7 冲突裁定：同一目标同一概念只认最近一条。 */
    @Query("SELECT * FROM arbitrations WHERE goalId = :gid AND concept = :concept ORDER BY decidedAt DESC LIMIT 1")
    suspend fun arbitrationFor(gid: String, concept: String): Arbitration?

    @Query("SELECT * FROM arbitrations WHERE goalId = :gid ORDER BY decidedAt DESC LIMIT 20")
    suspend fun arbitrationsFor(gid: String): List<Arbitration>

    @Query("SELECT * FROM tasks WHERE id = :id")
    suspend fun task(id: Long): Task?

    @Query("SELECT * FROM tasks WHERE date = :date AND goalId = :goalId AND kind = :kind LIMIT 1")
    suspend fun taskAt(date: String, goalId: String, kind: String): Task?

    @Query("SELECT * FROM goals WHERE id = :id")
    suspend fun goal(id: String): Goal?

    @Query("SELECT * FROM conversations WHERE kind = 'task' AND taskId = :taskId LIMIT 1")
    suspend fun convForTask(taskId: Long): Conversation?

    @Query("SELECT * FROM conversations WHERE id = :id")
    suspend fun conv(id: Long): Conversation?

    @Query("SELECT * FROM conversations ORDER BY COALESCE(NULLIF(lastActive, ''), createdAt) DESC, id DESC LIMIT 100")
    suspend fun conversations(): List<Conversation>

    @Query("SELECT * FROM messages WHERE convId = :convId ORDER BY id DESC LIMIT :limit")
    suspend fun messages(convId: Long, limit: Int = 200): List<Message>

    @Query("SELECT * FROM quiz_stats WHERE goalId = :goalId")
    suspend fun quizStat(goalId: String): QuizStat?

    @Query("SELECT COUNT(*) FROM tasks WHERE date = :date AND state != 'dropped'")
    suspend fun countTasks(date: String): Int

    @Query("SELECT COUNT(*) FROM tasks WHERE date = :date AND state = 'done'")
    suspend fun countDone(date: String): Int

    @Query("SELECT COALESCE(SUM(minutes), 0) FROM tasks WHERE date = :date AND state = 'done'")
    suspend fun minutesDone(date: String): Int

    @Query("SELECT COUNT(DISTINCT date) FROM tasks WHERE date < :today AND state = 'todo' AND (course = :course OR :course = '')")
    suspend fun lagDays(today: String, course: String): Int

    @Query("SELECT COUNT(*) FROM goals WHERE course = :course OR :course = ''")
    suspend fun totalGoals(course: String): Int

    @Query("SELECT COUNT(*) FROM goals WHERE done = 1 AND (course = :course OR :course = '')")
    suspend fun doneGoals(course: String): Int

    // ---------------------------------------------------------------- 写
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putCourses(items: List<Course>)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putGoals(items: List<Goal>)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putTasks(items: List<Task>)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putTask(item: Task)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putQuizStats(items: List<QuizStat>)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putConversations(items: List<Conversation>)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putMessages(items: List<Message>)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putBlocks(items: List<Block>)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putReviews(items: List<Review>)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putConversation(item: Conversation)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putMessageList(items: List<Message>)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putOutbox(item: OutboxItem)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putNote(item: Note)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putArbitration(item: Arbitration)

    @Query("UPDATE tasks SET state = :state WHERE id = :id")
    suspend fun setTaskState(id: Long, state: String)

    @Query("UPDATE conversations SET lastActive = :ts WHERE id = :id")
    suspend fun touchConversation(id: Long, ts: String)

    @Query("UPDATE outbox SET pending = 0 WHERE uid = :uid")
    suspend fun markEventSent(uid: String)

    @Query("UPDATE outbox SET underReview = 1 WHERE uid = :uid")
    suspend fun markEventUnderReview(uid: String)

    @Query("DELETE FROM messages WHERE convId = :convId")
    suspend fun clearMessages(convId: Long)

    /** 重排前清掉「今天及以后」的未完成卡；done / skipped / 过去卡都保留（P4）。 */
    @Query("DELETE FROM tasks WHERE date >= :from AND state = 'todo' AND (:course = '' OR course = :course)")
    suspend fun deleteOpenTasksFrom(from: String, course: String): Int

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putMeta(item: Meta)

    @Query("DELETE FROM courses")
    suspend fun clearCourses()

    @Query("DELETE FROM goals")
    suspend fun clearGoals()

    @Query("DELETE FROM tasks")
    suspend fun clearTasks()

    @Query("DELETE FROM quiz_stats")
    suspend fun clearQuizStats()

    @Query("DELETE FROM conversations")
    suspend fun clearConversations()

    @Query("DELETE FROM messages")
    suspend fun clearMessages()

    @Query("DELETE FROM blocks")
    suspend fun clearBlocks()

    @Query("DELETE FROM review")
    suspend fun clearReviews()
}

@Database(
    entities = [
        Course::class, Goal::class, Task::class, QuizStat::class,
        Conversation::class, Message::class, Block::class,
        Note::class, Arbitration::class, OutboxItem::class, Review::class, Meta::class,
    ],
    version = 1,
    exportSchema = false,
)
abstract class AppDb : RoomDatabase() {
    abstract fun dao(): AppDao

    companion object {
        @Volatile
        private var instance: AppDb? = null

        fun get(context: Context): AppDb =
            instance ?: synchronized(this) {
                instance ?: Room.databaseBuilder(
                    context.applicationContext, AppDb::class.java, "studybot.db"
                ).build().also { instance = it }
            }
    }
}
