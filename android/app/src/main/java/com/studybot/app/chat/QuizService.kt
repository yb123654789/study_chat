package com.studybot.app.chat

import android.content.Context
import com.studybot.app.Secure
import com.studybot.app.data.AppDao
import com.studybot.app.data.AppDb
import com.studybot.app.data.Goal
import com.studybot.app.data.Task
import com.studybot.app.sync.Retrieval
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale

/**
 * 本机出题 + 判分（P3 手机端）。
 * 提示词与降级口径照抄电脑端 app/llm.py：
 * - generate：2 单选 + 1 简答；没 key / 模型挂了 / 解析不出来 → 用验收点出简答。
 * - grade：选择题直接比下标；简答先让模型看 rubric，模型失败按关键词命中判。
 * 题目只活在内存里，不进数据库（没有本地 quiz 表，也不打算加）。
 */
object QuizService {

    data class Q(
        val type: String,
        val stem: String,
        val options: List<String>,
        val answer: Int,
        val rubric: List<String>,
    )

    /**
     * 一次提交的结果：逐题对错 + 答对数（页面上要标哪题错）。
     * followUp："" 没排 / "review" 明天错题重做 / "learn" 明天回炉 / "exists" 明天已有同类卡（没重复插）。
     * failStreak：本次之后的连错次数（回炉后已清零）。
     */
    data class Outcome(
        val passed: Int,
        val total: Int,
        val results: List<Boolean>,
        val failStreak: Int = 0,
        val followUp: String = "",
    )

    /** 连错次数存本地 SharedPreferences，不建表（P3：题库表不动）。 */
    private const val PREFS_QUIZ = "studybot_quiz"

    // 与 app/llm.py 的 SYSTEM_QUIZ 一致
    private const val SYSTEM_QUIZ = """你是出题人。基于给定的学习目标，出 3 道题检验是否真的掌握。
规则：
- 2 道单选，每题 4 个选项，干扰项必须是"看起来合理但确实错的"，不能是明显荒谬的。
- 1 道简答，要求用 2~3 句话解释，不能是名词解释。
- 题目考理解与应用，不考背诵原文；不要问"资料里第几页写了什么"。
- 只输出 JSON，不要解释。"""

    // 与 app/llm.py 的 SYSTEM_GRADE 一致
    private const val SYSTEM_GRADE = """你是严格但公平的阅卷人。按 rubric 判断答案是否覆盖要点。
- 覆盖 2/3 以上要点 → pass
- 只覆盖 1/3，或出现事实错误 → fail
- 答非所问、空答 → fail
不要因为"字写得多"就给过。只输出 JSON。"""

    private val FENCE_HEAD = Regex("^```(?:json)?\\s*", RegexOption.IGNORE_CASE)
    private val FENCE_TAIL = Regex("\\s*```\\s*$")
    private val FRAGMENT = Regex("[\\u4e00-\\u9fa5A-Za-z0-9]{2,}")

    // ---------------------------------------------------------------- 出题
    suspend fun generate(ctx: Context, goal: Goal): List<Q> = withContext(Dispatchers.IO) {
        val fallback = fallbackQuestions(goal)
        val key = Secure.apiKey(ctx)
        if (key.isBlank()) return@withContext fallback

        val out = ArrayList<Q>()
        try {
            val messages = JSONArray()
                .put(JSONObject().put("role", "system").put("content", SYSTEM_QUIZ))
                .put(JSONObject().put("role", "user").put("content", quizUser(goal)))
            val raw = DeepSeek.complete(key, modelOf(ctx), messages, 1200, 0.8)
            val arr = questionsOf(raw) ?: return@withContext fallback
            for (i in 0 until arr.length()) {
                if (out.size >= 3) break
                val item = arr.optJSONObject(i) ?: continue
                normalize(item)?.let { out.add(it) }
            }
        } catch (_: Exception) {
            // 没网 / key 错 / 模型抽风：一律走降级，不打扰页面
            return@withContext fallback
        }
        fill(out, fallback)
        if (out.isEmpty()) fallback else out
    }

    private fun quizUser(goal: Goal): String {
        val cp = Retrieval.cpList(goal)
        val cps = if (cp.isEmpty()) "- （没有验收点，按标题出题）"
        else cp.joinToString("\n") { "- $it" }
        return """本节目标：${goal.title}
章节：${goal.ch}　预计 ${goal.m} 分钟
验收点：
$cps

输出 JSON：
{
  "questions": [
    {"type":"choice","stem":"...","options":["...","...","...","..."],"answer":1},
    {"type":"choice","stem":"...","options":["...","...","...","..."],"answer":0},
    {"type":"short","stem":"...","rubric":["必须提到的要点1","要点2","要点3"]}
  ]
}"""
    }

    private fun normalize(item: JSONObject): Q? {
        val stem = item.optString("stem", "").trim()
        if (stem.isBlank()) return null
        val type = item.optString("type", "choice").trim().lowercase()
        if (type == "choice") {
            val arr = item.optJSONArray("options") ?: return null
            val options = (0 until arr.length()).map { arr.optString(it, "").trim() }
            if (options.count { it.isNotBlank() } < 2) return null
            val answer = item.optInt("answer", 0).coerceIn(0, options.size - 1)
            return Q("choice", stem, options, answer, emptyList())
        }
        val arr = item.optJSONArray("rubric")
        val rubric = ArrayList<String>()
        if (arr != null) {
            for (i in 0 until arr.length()) {
                val s = arr.optString(i, "").trim()
                if (s.isNotBlank()) rubric.add(s)
            }
        }
        return Q("short", stem, emptyList(), -1, rubric)
    }

    /** 模型题不够 3 道时，用验收点补简答（电脑端 fallback_quiz 的口径）。 */
    private fun fill(out: MutableList<Q>, fallback: List<Q>) {
        for (q in fallback) {
            if (out.size >= 3) break
            if (out.any { it.stem == q.stem }) continue
            out.add(q)
        }
    }

    private fun fallbackQuestions(goal: Goal): List<Q> {
        val cps = Retrieval.cpList(goal)
        val pool = if (cps.isNotEmpty()) cps else listOf(goal.title.ifBlank { "这个目标" })
        val out = ArrayList<Q>()
        for (cp in pool) {
            if (out.size >= 3) break
            out.add(Q("short", "用两三句话回答：$cp", emptyList(), -1, listOf(cp)))
        }
        while (out.size < 3) {
            val title = goal.title.ifBlank { "这一节" }
            out.add(Q("short", "用两三句话说清「$title」的核心是什么。", emptyList(), -1, listOf(title)))
        }
        return out
    }

    // ---------------------------------------------------------------- 判分
    /** 选择题目比下标；简答先模型、失败按关键词。答案字符串：选择题是选项下标，简答是原文。 */
    suspend fun grade(ctx: Context, goal: Goal, q: Q, answer: String): Boolean {
        val text = answer.trim()
        if (q.type == "choice") return text.toIntOrNull() == q.answer
        if (text.length < 4) return false

        val key = Secure.apiKey(ctx)
        if (key.isNotBlank()) {
            try {
                val messages = JSONArray()
                    .put(JSONObject().put("role", "system").put("content", SYSTEM_GRADE))
                    .put(JSONObject().put("role", "user").put("content", gradeUser(goal, q, text)))
                val raw = DeepSeek.complete(key, modelOf(ctx), messages, 300, 0.2)
                objectOf(raw)?.let { return it.optBoolean("pass", false) }
            } catch (_: Exception) {
                // 模型不可用：退回关键词，别让判分卡死
            }
        }
        return byKeywords(q.rubric, text)
    }

    private fun gradeUser(goal: Goal, q: Q, answer: String): String {
        val rubric = JSONArray()
        for (r in q.rubric) rubric.put(r)
        return "目标：${goal.title}\n题目：${q.stem}\n" +
            "要点：$rubric\n学生答案：$answer\n\n" +
            "输出 JSON：{\"pass\": true}"
    }

    /** 降级判分：rubric 每条拆成 ≥2 字的片段，命中 ≥2 条或过半才算过（照 app/llm.py）。 */
    private fun byKeywords(rubric: List<String>, text: String): Boolean {
        if (text.length < 4) return false
        if (rubric.isEmpty()) return text.length >= 8
        var covered = 0
        for (r in rubric) {
            if (FRAGMENT.findAll(r).any { it.value in text }) covered++
        }
        return covered >= 2 || covered * 2 > rubric.size
    }

    // ---------------------------------------------------------------- 提交
    /**
     * 逐题判分 → 每题一条 attempt 事件；答对 ≥2 把卡置 done，并写一条 task_state。
     * 返回答对题数（Outcome 里多给逐题对错，页面用来标红）。
     */
    suspend fun submitResults(
        ctx: Context,
        task: Task,
        questions: List<Q>,
        answers: List<String>,
    ): Outcome = withContext(Dispatchers.IO) {
        val dao = AppDb.get(ctx).dao()
        val goal = dao.goal(task.goalId) ?: Goal(
            id = task.goalId,
            course = task.course,
            ch = "",
            title = task.title,
            p = "",
            m = 0,
            d = "",
            cp = "[]",
            seq = 0,
            done = false,
        )
        val results = ArrayList<Boolean>()
        for ((i, q) in questions.withIndex()) {
            val answer = answers.getOrElse(i) { "" }.trim()
            val ok = grade(ctx, goal, q, answer)
            results.add(ok)
            ChatService.putOutbox(
                ctx, "attempt", JSONObject()
                    .put("task_id", task.id)
                    .put("goal_id", task.goalId)
                    .put("quiz_id", 0)
                    .put("answer", answer)
                    .put("passed", if (ok) 1 else 0)
            )
        }
        val passed = results.count { it }
        if (passed >= 2 && task.id > 0) {
            dao.setTaskState(task.id, "done")
            ChatService.putOutbox(
                ctx, "task_state", JSONObject()
                    .put("task_id", task.id)
                    .put("goal_id", task.goalId)
                    .put("state", "done")
                    .put("quiz_passed", passed)
                    .put("quiz_total", questions.size)
            )
        }

        // 连错计数：答对 ≥2 清零；没过 +1，并按次数给明天排一张卡
        val prefs = ctx.getSharedPreferences(PREFS_QUIZ, Context.MODE_PRIVATE)
        val failKey = "fail_${task.goalId}"
        var streak = prefs.getInt(failKey, 0)
        var followUp = ""
        if (passed >= 2) {
            if (streak != 0) prefs.edit().putInt(failKey, 0).apply()
            streak = 0
        } else {
            streak += 1
            prefs.edit().putInt(failKey, streak).apply()
            if (task.goalId.isNotBlank()) {
                val scheduled = scheduleCatchUp(ctx, dao, task, goal, streak)
                followUp = scheduled.first
                streak = scheduled.second
            }
        }
        Outcome(passed, questions.size, results, streak, followUp)
    }

    /**
     * 没过时排明天：连错 1 次插「错题重做」（min(目标分钟,15)），
     * ≥2 次插「回炉」（20 分钟）并把连错清零（回炉后重新算）。
     * 已有同日期/同目标/同类型的卡就不重复插。返回 (排了什么, 排后的连错次数)。
     */
    private suspend fun scheduleCatchUp(
        ctx: Context,
        dao: AppDao,
        task: Task,
        goal: Goal,
        streak: Int,
    ): Pair<String, Int> {
        val relearn = streak >= 2
        val kind = if (relearn) "learn" else "review"
        val title = if (relearn) {
            "回炉：${goal.title.ifBlank { task.title }}"
        } else {
            "错题重做：${goal.title.ifBlank { task.title }}"
        }
        val minutes = if (relearn) {
            20
        } else {
            val base = if (goal.m > 0) goal.m else task.minutes
            if (base > 0) minOf(base, 15) else 15
        }
        // 回炉后重新计数：不管插没插，连错都清零
        if (relearn) {
            ctx.getSharedPreferences(PREFS_QUIZ, Context.MODE_PRIVATE).edit()
                .putInt("fail_${task.goalId}", 0).apply()
        }
        val tomorrow = tomorrow()
        val after = if (relearn) 0 else streak
        if (dao.taskAt(tomorrow, task.goalId, kind) != null) return "exists" to after

        dao.putTask(
            Task(
                id = ChatService.newLocalId(),
                date = tomorrow,
                course = goal.course.ifBlank { task.course },
                goalId = task.goalId,
                title = title,
                kind = kind,
                minutes = minutes,
                state = "todo",
                createdAt = ChatService.nowStr(),
            )
        )
        return (if (relearn) "learn" else "review") to after
    }

    /** 明天 = 今天 +1 天（手机本地时区）。 */
    private fun tomorrow(): String {
        val c = Calendar.getInstance()
        c.add(Calendar.DAY_OF_MONTH, 1)
        return SimpleDateFormat("yyyy-MM-dd", Locale.US).format(c.time)
    }

    suspend fun submit(
        ctx: Context,
        task: Task,
        questions: List<Q>,
        answers: List<String>,
    ): Int = submitResults(ctx, task, questions, answers).passed

    // ---------------------------------------------------------------- 小工具
    private suspend fun modelOf(ctx: Context): String =
        AppDb.get(ctx).dao().meta("model")?.ifBlank { null } ?: "deepseek-flash"

    private fun stripFence(raw: String): String =
        FENCE_TAIL.replace(FENCE_HEAD.replace(raw.trim(), ""), "")

    /** 从模型输出里抠第一个 JSON 对象（容忍 ```json 围栏和前后废话）。 */
    private fun objectOf(raw: String): JSONObject? {
        val s = stripFence(raw)
        val start = s.indexOf('{')
        if (start < 0) return null
        for (end in s.length - 1 downTo start + 1) {
            if (s[end] != '}') continue
            try {
                return JSONObject(s.substring(start, end + 1))
            } catch (_: Exception) {
            }
        }
        return null
    }

    /** 出题结果可能是 {"questions":[...]}，也可能直接是数组。 */
    private fun questionsOf(raw: String): JSONArray? {
        objectOf(raw)?.optJSONArray("questions")?.let { return it }
        val s = stripFence(raw)
        val start = s.indexOf('[')
        if (start < 0) return null
        for (end in s.length - 1 downTo start + 1) {
            if (s[end] != ']') continue
            try {
                return JSONArray(s.substring(start, end + 1))
            } catch (_: Exception) {
            }
        }
        return null
    }
}
