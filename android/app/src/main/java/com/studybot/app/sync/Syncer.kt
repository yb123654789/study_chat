package com.studybot.app.sync

import android.content.Context
import android.util.Log
import androidx.room.withTransaction
import com.studybot.app.data.AppDb
import com.studybot.app.data.Block
import com.studybot.app.data.Conversation
import com.studybot.app.data.Course
import com.studybot.app.data.Goal
import com.studybot.app.data.Message
import com.studybot.app.data.Meta
import com.studybot.app.data.QuizStat
import com.studybot.app.data.Review
import com.studybot.app.data.Task
import com.studybot.app.net.Api
import com.studybot.app.net.ApiException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * 下行同步：拉快照 → 一个事务写本地库 → 记 rev / blocks_rev / last_sync。
 *
 * 事务失败整批回滚，不会留下一半的新数据；块没变时服务器不下发，本地不覆盖。
 */
class Syncer(private val context: Context) {

    data class Result(
        val rev: String,
        val goals: Int,
        val tasks: Int,
        val blocks: Int,
        val blocksRev: String,
    )

    suspend fun sync(timeoutMs: Long = 60_000L): Result = withContext(Dispatchers.IO) {
        val db = AppDb.get(context)
        val dao = db.dao()
        val base = dao.meta("server_url")?.trim().orEmpty()
        val token = dao.meta("token")?.trim().orEmpty()
        if (base.isBlank()) throw ApiException("先在「设置」里填服务器地址")
        if (token.isBlank()) throw ApiException("先在「设置」里填访问 token")

        val prevRev = dao.meta("rev")?.takeIf { it.isNotBlank() }
        val prevBlocksRev = dao.meta("blocks_rev")?.takeIf { it.isNotBlank() }
        val json = Api.snapshot(base, token, prevRev, prevBlocksRev, timeoutMs)

        val done = parseIdSet(json.optJSONArray("done"))
        val courses = parseCourses(json.optJSONArray("courses"))
        val goals = parseGoals(json.optJSONArray("goals"), done)
        val tasks = parseTasks(json.optJSONArray("tasks"))
        val stats = parseQuizStats(json.optJSONArray("quiz_stats"))
        val reviews = parseReviews(json.optJSONArray("review"))
        val convs = parseConversations(json.optJSONArray("conversations"))
        val messages = parseMessages(json.optJSONArray("messages"))
        val blocks = json.optJSONArray("blocks")?.let { parseBlocks(it) }

        val newRev = json.optString("rev", prevRev ?: "0")
        val newBlocksRev = json.optString("blocks_rev", prevBlocksRev ?: "")
        val now = System.currentTimeMillis().toString()
        // 手机上刚答完、还没上传的卡片状态：下行快照会把它盖回 todo，
        // 这一轮先记下来，写完快照再盖回去，等 outbox 上传成功才以服务器为准。
        val pendingStates = pendingTaskStates(dao)

        db.withTransaction {
            dao.clearCourses(); dao.putCourses(courses)
            dao.clearGoals(); dao.putGoals(goals)
            dao.clearTasks(); dao.putTasks(tasks)
            for ((id, state) in pendingStates) dao.setTaskState(id, state)
            dao.clearQuizStats(); dao.putQuizStats(stats)
            dao.clearReviews(); dao.putReviews(reviews)
            // 会话/消息不清空：本地新聊的（id 为负）还要留着，P3 才上传
            dao.putConversations(convs)
            dao.putMessages(messages)
            if (blocks != null) {
                dao.clearBlocks()
                dao.putBlocks(blocks)
            }
            dao.putMeta(Meta("rev", newRev))
            dao.putMeta(Meta("blocks_rev", newBlocksRev))
            dao.putMeta(Meta("last_sync", now))
        }

        if (blocks != null) {
            // 首次同步建 bigram 索引；P2 检索才用，这里只建
            runCatching { BigramIndex.build(blocks) }
                .onFailure { Log.w("Syncer", "bigram 索引建失败：${it.message}") }
        }

        // 手机端已改成只读 + 自由聊天：重排交给服务器（每日 auto_replan），
        // 本地不再插卡（本地插的卡下次同步会被快照覆盖，反而误导）。

        // 上行：把 outbox 里的短期数据推给电脑（P3）；失败不影响下行同步
        runCatching { uploadOutbox(dao, base, token) }
            .onFailure { Log.w("Syncer", "上行失败，留着下次重传：${it.message}") }

        Result(newRev, goals.size, tasks.size, blocks?.size ?: 0, newBlocksRev)
    }

    /** outbox 里还没上传的卡片状态：同一张卡取最后一条。 */
    private suspend fun pendingTaskStates(
        dao: com.studybot.app.data.AppDao,
    ): Map<Long, String> {
        val out = HashMap<Long, String>()
        for (e in dao.pendingOutbox()) {
            if (e.kind != "task_state") continue
            try {
                val o = JSONObject(e.payload)
                val id = o.optLong("task_id", 0L)
                val state = o.optString("state")
                if (id > 0L && state.isNotBlank()) out[id] = state
            } catch (_: Exception) {
                // payload 坏了就跳过，别影响整轮同步
            }
        }
        return out
    }

    private suspend fun uploadOutbox(dao: com.studybot.app.data.AppDao, base: String, token: String) {
        val pending = dao.pendingOutbox().take(50)
        if (pending.isEmpty()) return
        val arr = JSONArray()
        for (e in pending) {
            try {
                arr.put(JSONObject(e.payload))
            } catch (_: Exception) {
            }
        }
        if (arr.length() == 0) return
        Api.events(base, token, arr, "android")
        for (e in pending) dao.markEventSent(e.uid)
    }

    // ---------------------------------------------------------------- 解析
    private fun parseIdSet(arr: JSONArray?): Set<String> {
        if (arr == null) return emptySet()
        val out = HashSet<String>(arr.length())
        for (i in 0 until arr.length()) out.add(arr.optString(i))
        return out
    }

    private fun parseCourses(arr: JSONArray?): List<Course> {
        val out = ArrayList<Course>()
        for (i in 0 until (arr?.length() ?: 0)) {
            val o = arr!!.optJSONObject(i) ?: continue
            out.add(
                Course(
                    id = o.optString("id"),
                    name = o.optString("name"),
                    examDate = o.optString("exam_date"),
                    weekendRatio = o.optInt("weekend_ratio", 130),
                    dayCap = o.optInt("day_cap", 0),
                    archived = o.optInt("archived", 0),
                )
            )
        }
        return out
    }

    private fun parseGoals(arr: JSONArray?, done: Set<String>): List<Goal> {
        val out = ArrayList<Goal>()
        for (i in 0 until (arr?.length() ?: 0)) {
            val o = arr!!.optJSONObject(i) ?: continue
            val id = o.optString("id")
            out.add(
                Goal(
                    id = id,
                    course = o.optString("course", "ds"),
                    ch = o.optString("ch"),
                    title = o.optString("title"),
                    p = o.optString("p", "core"),
                    m = o.optInt("m", 30),
                    d = o.optJSONArray("d")?.toString() ?: "[]",
                    cp = o.optJSONArray("cp")?.toString() ?: "[]",
                    seq = o.optInt("seq", 0),
                    done = done.contains(id),
                )
            )
        }
        return out
    }

    private fun parseTasks(arr: JSONArray?): List<Task> {
        val out = ArrayList<Task>()
        for (i in 0 until (arr?.length() ?: 0)) {
            val o = arr!!.optJSONObject(i) ?: continue
            out.add(
                Task(
                    id = o.optLong("id"),
                    date = o.optString("date"),
                    course = o.optString("course", "ds"),
                    goalId = o.optString("goal_id"),
                    title = o.optString("title"),
                    kind = o.optString("kind", "learn"),
                    minutes = o.optInt("minutes", 0),
                    state = o.optString("state", "todo"),
                    createdAt = o.optString("created_at"),
                )
            )
        }
        return out
    }

    private fun parseQuizStats(arr: JSONArray?): List<QuizStat> {
        val out = ArrayList<QuizStat>()
        for (i in 0 until (arr?.length() ?: 0)) {
            val o = arr!!.optJSONObject(i) ?: continue
            val gid = o.optString("goal_id")
            if (gid.isBlank()) continue
            out.add(
                QuizStat(
                    goalId = gid,
                    batch = o.optInt("batch", 1),
                    answered = o.optInt("answered", 0),
                    passed = o.optInt("passed", 0),
                    failStreak = o.optInt("fail_streak", 0),
                )
            )
        }
        return out
    }

    private fun parseReviews(arr: JSONArray?): List<Review> {
        val out = ArrayList<Review>()
        for (i in 0 until (arr?.length() ?: 0)) {
            val o = arr!!.optJSONObject(i) ?: continue
            out.add(
                Review(
                    id = o.optLong("id"),
                    kind = o.optString("kind", "task_state"),
                    taskId = if (o.isNull("task_id")) null else o.optLong("task_id"),
                    goalId = o.optString("goal_id"),
                    day = o.optString("day"),
                    phoneState = o.optString("phone_state"),
                    serverState = o.optString("server_state"),
                    reason = o.optString("reason"),
                    payloadJson = o.optString("payload_json", "{}"),
                    createdAt = o.optString("created_at"),
                    resolvedAt = o.optString("resolved_at"),
                    resolution = o.optString("resolution"),
                )
            )
        }
        return out
    }

    private fun parseConversations(arr: JSONArray?): List<Conversation> {
        val out = ArrayList<Conversation>()
        for (i in 0 until (arr?.length() ?: 0)) {
            val o = arr!!.optJSONObject(i) ?: continue
            out.add(
                Conversation(
                    id = o.optLong("id"),
                    kind = o.optString("kind", "task"),
                    taskId = if (o.isNull("task_id")) null else o.optLong("task_id"),
                    goalId = o.optString("goal_id"),
                    title = o.optString("title"),
                    createdAt = o.optString("created_at"),
                    lastActive = o.optString("last_active"),
                )
            )
        }
        return out
    }

    private fun parseMessages(arr: JSONArray?): List<Message> {
        val out = ArrayList<Message>()
        for (i in 0 until (arr?.length() ?: 0)) {
            val o = arr!!.optJSONObject(i) ?: continue
            out.add(
                Message(
                    id = o.optLong("id"),
                    convId = o.optLong("conv_id"),
                    role = o.optString("role"),
                    text = o.optString("text"),
                    sources = o.optJSONArray("sources")?.toString() ?: "[]",
                    tokens = o.optInt("tokens", 0),
                    createdAt = o.optString("created_at"),
                )
            )
        }
        return out
    }

    private fun parseBlocks(arr: JSONArray): List<Block> {
        val out = ArrayList<Block>(arr.length())
        for (i in 0 until arr.length()) {
            val o: JSONObject = arr.optJSONObject(i) ?: continue
            out.add(
                Block(
                    id = o.optString("id"),
                    course = o.optString("course", "ds"),
                    src = o.optString("src"),
                    anchor = o.optString("anchor"),
                    text = o.optString("text"),
                    fp = o.optString("fp"),
                )
            )
        }
        return out
    }
}
