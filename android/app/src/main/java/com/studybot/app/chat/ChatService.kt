package com.studybot.app.chat

import android.content.Context
import com.studybot.app.Secure
import com.studybot.app.data.AppDb
import com.studybot.app.data.Arbitration
import com.studybot.app.data.Conversation
import com.studybot.app.data.Goal
import com.studybot.app.data.Message
import com.studybot.app.data.Note
import com.studybot.app.data.OutboxItem
import com.studybot.app.data.Task
import com.studybot.app.sync.Retrieval
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID
import java.util.concurrent.atomic.AtomicLong

/** 带练引擎的手机版：system prompt / 状态条 / 6000 字预算 / plain 全部照抄电脑端。 */
object ChatService {

    class ChatError(message: String) : Exception(message)

    /** 资料和笔记对不上的一次待裁定：由模型的 ask_conflict 调用带上来。 */
    data class Conflict(
        val concept: String,
        val materialClaim: String,
        val materialSrc: String,
        val noteClaim: String,
        val noteId: String,
    )

    data class Reply(
        val text: String,
        val sources: List<Pair<String, String>>,
        val tool: String,
        val tokens: Int,
        val conflict: Conflict? = null,
    )

    private const val HISTORY_TURNS = 12
    private const val HISTORY_CHARS = 6000
    private const val HIT_CHARS = 1200

    // 本地 id 从一个远离服务器 id 的基数往上加，避免同步时撞车
    private val ids = AtomicLong(4_000_000_000L)
    fun newLocalId(): Long = ids.getAndIncrement()

    fun nowStr(): String =
        SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())

    private fun today(): String =
        SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())

    // ---------------------------------------------------------------- 会话
    suspend fun conversationFor(ctx: Context, task: Task): Conversation {
        val dao = AppDb.get(ctx).dao()
        dao.convForTask(task.id)?.let { return it }
        val conv = Conversation(
            id = newLocalId(),
            kind = "task",
            taskId = task.id,
            goalId = task.goalId,
            title = task.title,
            createdAt = nowStr(),
            lastActive = nowStr(),
        )
        dao.putConversation(conv)
        return conv
    }

    /** 手机只留一个自由聊天窗：有就复用，没有才建。 */
    suspend fun freeConversation(ctx: Context): Conversation {
        val dao = AppDb.get(ctx).dao()
        dao.latestFreeConv()?.let { return it }
        val conv = Conversation(
            id = newLocalId(),
            kind = "free",
            taskId = null,
            goalId = "",
            title = "自由问答",
            createdAt = nowStr(),
            lastActive = nowStr(),
        )
        dao.putConversation(conv)
        return conv
    }

    suspend fun history(ctx: Context, convId: Long, limit: Int = 200): List<Message> =
        AppDb.get(ctx).dao().messages(convId, limit).reversed()

    // ---------------------------------------------------------------- 状态条
    suspend fun stateBar(ctx: Context, task: Task?): String {
        val dao = AppDb.get(ctx).dao()
        val day = today()
        val total = dao.countTasks(day)
        val done = dao.countDone(day)
        val minutes = dao.minutesDone(day)
        val course = task?.course ?: ""
        val lag = dao.lagDays(day, course)
        val courses = dao.courses()
        val c = courses.firstOrNull { it.id == course }
        val exam = c?.examDate.orEmpty()
        val daysLeft = daysBetween(day, exam)
        val doneGoals = dao.doneGoals(course)
        val totalGoals = dao.totalGoals(course)

        val bits = ArrayList<String>()
        if (course.isNotBlank()) bits.add(c?.name?.ifBlank { course } ?: course)
        bits.add("今天 $done/$total 张")
        bits.add("已学 $minutes 分钟")
        if (lag > 0) bits.add("落后 $lag 天")
        bits.add("距考核 $daysLeft 天")
        bits.add("目标 $doneGoals/$totalGoals")
        var text = bits.joinToString("｜")
        if (task != null) text += "\n本卡：${task.title}（${task.minutes} 分钟 · ${task.state}）"
        return text
    }

    /** 自由聊天顶部的状态：全部课程合起来。 */
    suspend fun stateBarAll(ctx: Context): String {
        val dao = AppDb.get(ctx).dao()
        val day = today()
        val total = dao.countTasks(day)
        val done = dao.countDone(day)
        val minutes = dao.minutesDone(day)
        val lag = dao.lagDays(day, "")
        val bits = ArrayList<String>()
        bits.add("今天 $done/$total 张")
        bits.add("已学 $minutes 分钟")
        if (lag > 0) bits.add("落后 $lag 天")
        for (c in dao.courses().filter { it.archived == 0 }.take(3)) {
            val left = daysBetween(day, c.examDate)
            if (left >= 0) bits.add("${c.name.ifBlank { c.id }} 距考 $left 天")
        }
        return bits.joinToString("｜")
    }

    /** 今天 + 后面几天排好的卡，给自由聊天当"可推荐的范围"。 */
    private suspend fun taskContext(ctx: Context): String {
        val dao = AppDb.get(ctx).dao()
        val day = today()
        val rows = dao.upcoming(day)
        if (rows.isEmpty()) return "（今天和以后都没有排卡：让用户去电脑端重排计划。）"
        val sb = StringBuilder()
        var groups = 0
        for ((d, items) in rows.groupBy { it.date }) {
            if (groups >= 3) break
            sb.append(relDay(day, d)).append("（").append(items.size).append(" 张）：")
            sb.append(
                items.take(4).joinToString("；") {
                    "${it.title}（${it.minutes} 分钟，${stateCn(it.state)}）"
                }
            )
            sb.append('\n')
            groups++
        }
        return sb.toString().trim()
    }

    private fun stateCn(state: String): String = when (state) {
        "done" -> "已完成"
        "skipped" -> "已跳过"
        else -> "没做"
    }

    private fun relDay(today: String, d: String): String = when (val diff = daysBetween(today, d)) {
        0 -> "今天"
        1 -> "明天"
        2 -> "后天"
        else -> "再过 $diff 天"
    }

    private fun daysBetween(from: String, to: String): Int {        if (to.isBlank()) return 0
        val fmt = SimpleDateFormat("yyyy-MM-dd", Locale.US)
        return try {
            val a = fmt.parse(from)!!.time
            val b = fmt.parse(to)!!.time
            Math.round((b - a) / 86_400_000.0).toInt()
        } catch (_: Exception) {
            0
        }
    }

    // ---------------------------------------------------------------- 提示词
    /** 笔记标签的中文（和笔记页一致：老师说 / 抄课件 / 自己理解）。 */
    fun noteTagCn(tag: String): String = when (tag) {
        "teacher" -> "老师说"
        "slide" -> "抄课件"
        else -> "自己理解"
    }

    /** 裁定方向的中文：material = 按资料，note = 按我的笔记。 */
    fun verdictCn(verdict: String): String =
        if (verdict == "note") "按我的笔记" else "按资料"

    private fun systemPrompt(
        courseName: String,
        state: String,
        goalTitle: String,
        goalCh: String,
        cp: List<String>,
        hits: List<Retrieval.Hit>,
        notes: List<Note>,
        arbs: List<Arbitration>,
    ): String {
        val cpText = cp.joinToString("\n") { "- $it" }.ifBlank { "- （没有验收点，按标题讲）" }
        val src = if (hits.isEmpty()) {
            "（这轮没检索到资料段落。只能讲通用知识，并且必须标注「补充说明」。）"
        } else {
            hits.joinToString("\n") {
                "【资料】《${it.src}》${it.anchor}：${it.text.take(HIT_CHARS)}"
            }
        }
        val notesText = if (notes.isEmpty()) {
            "（这个目标还没有笔记。）"
        } else {
            notes.joinToString("\n") {
                "【我的笔记·${noteTagCn(it.tag)}】${it.text.replace("\n", " ").take(400)}（note_id=${it.uid}）"
            }
        }
        val arbText = if (arbs.isEmpty()) {
            "（还没有裁定。）"
        } else {
            arbs.joinToString("\n") { "- ${it.concept}：已裁定 → ${verdictCn(it.verdict)}" }
        }
        return """你是这门课的学习带练，带一张 25~45 分钟的学习卡。

课程：$courseName
程序算好的当前状态（要引用就直接用这里的数，不要自己算、不要编）：
$state

本节目标：$goalTitle
章节：$goalCh
学完要能回答：
$cpText

这轮检索到的资料段落（优先用，引用时写《资料名》+ 页码/幻灯片）：
$src

我的笔记（这个目标最近 5 条，用户自己记的，可能记错，不等于资料；末尾括号里的 note_id 调 ask_conflict 时原样传）：
$notesText

已经裁定过的矛盾（同一个概念按这里讲，不许再问用户）：
$arbText

规则：
1. 中文短句，先给结论再给理由；一次回答不超过 8 行。
2. 只用上面的资料和验收点回答。资料里没有的，直说「资料里没有」，补充内容必须标「补充说明」。
3. 不要输出任何日期、倒计时、进度数字（除了状态条里已经有的）。
4. 绝不说「你通过了 / 这张卡完成了」——是否通过只由小测判定。
5. 用户想被考，调用 start_quiz；说先跳过，调用 skip_task；问计划/进度，调用 show_plan。
   不要自己出题，出题由程序做。
6. 每轮结尾用一个具体问题检查他是否真懂，别连着问两个以上。
7. 用户明显没懂时，换一个更小的例子重讲，别重复上一段话。
8. 输出是纯文本：不要用 Markdown（**加粗**、## 标题、`代码`），要强调就用「」。
9. 资料和笔记对同一个概念说法矛盾时，不许自己选边（不要判谁对），必须调用 ask_conflict，
   concept / material_claim / material_src / note_claim / note_id 都填；一轮最多问 1 次，
   问完继续答（先按资料讲，等用户裁定）。已经有裁定的概念不要问，直接按裁定讲。"""
    }

    /** 自由聊天的 system prompt：没有目标/卡片，只给状态、任务和检索到的资料。 */
    private fun freeSystemPrompt(state: String, tasks: String, hits: List<Retrieval.Hit>): String {
        val src = if (hits.isEmpty()) {
            "（这轮没检索到资料段落。只能讲通用知识，并且必须标注「补充说明」。）"
        } else {
            hits.joinToString("\n") {
                "【资料】《${it.src}》${it.anchor}：${it.text.take(HIT_CHARS)}"
            }
        }
        return """你是学习bot，用户在手机上的学习搭子。现在是自由聊天，没有绑定某一张卡。

程序算好的状态（要引用就直接用，不要自己算、不要编）：
$state

程序排好的任务（推荐学习内容时只能从这里挑，不许自己编日期或改计划）：
$tasks

这轮检索到的资料段落（优先用；引用时写《资料名》+ 页码/幻灯片）：
$src

规则：
1. 中文短句，先给结论再给理由；一次回答不超过 8 行。
2. 只用上面的资料回答。资料里没有的，直说「资料里没有」，补充内容必须标「补充说明」。
3. 不要输出任何日期、倒计时、进度数字（除了状态里已经有的）。
4. 绝不说「你通过了 / 这张卡完成了」——是否通过只由小测判定。
5. 用户问「学什么 / 先学哪张」：从上面的任务里挑一张，说明为什么先学它，再问他要不要开始；一次只推荐一张。
6. 用户想被考：告诉他点输入框左边的「＋ → 考我」，从今天的卡里选一张；你不要自己出题。
7. 用户想改计划：告诉他去电脑端改，手机端只读。
8. 输出纯文本：不要用 Markdown（**加粗**、## 标题、`代码`），要强调就用「」。
9. 每轮结尾用一个具体问题检查他是否真懂，别连着问两个以上。"""
    }

    /** 自由聊天只留一个工具：让程序报真实进度，省得模型自己算。 */
    private fun toolsFree(): JSONArray = JSONArray().put(
        JSONObject().put("type", "function").put(
            "function", JSONObject().put("name", "show_plan")
                .put("description", "用户问计划、进度、还剩多少、落后几天时调用，让程序把真实数字发给他。")
                .put(
                    "parameters", JSONObject().put("type", "object")
                        .put("properties", JSONObject()).put("required", JSONArray())
                )
        )
    )

    private val BOLD = Regex("\\*\\*(.+?)\\*\\*")
    private val HEAD = Regex("(?m)^#{1,4}\\s*")

    fun plain(text: String): String =
        HEAD.replace(BOLD.replace(text, "$1"), "").replace("`", "")

    private fun trim(rows: List<Message>): List<Message> {
        val kept = ArrayList<Message>()
        var used = 0
        for (m in rows.reversed()) {
            val n = m.text.length
            if (kept.size >= 2 && used + n > HISTORY_CHARS) break
            kept.add(m)
            used += n
        }
        return kept.reversed()
    }

    private fun toolsJson(): JSONArray {
        fun str(desc: String): JSONObject =
            JSONObject().put("type", "string").put("description", desc)

        fun fn(
            name: String,
            desc: String,
            props: JSONObject = JSONObject(),
            required: List<String> = emptyList(),
        ): JSONObject {
            val req = JSONArray()
            for (r in required) req.put(r)
            return JSONObject()
                .put("type", "function")
                .put(
                    "function", JSONObject().put("name", name).put("description", desc)
                        .put(
                            "parameters", JSONObject().put("type", "object")
                                .put("properties", props).put("required", req)
                        )
                )
        }

        val conflictProps = JSONObject()
            .put("concept", str("对不上的概念，一句话，比如「二叉树中序遍历的顺序」"))
            .put("material_claim", str("资料里的说法，用原话或紧贴原文的概括"))
            .put("material_src", str("资料名，从【资料】《…》里抄"))
            .put("note_claim", str("我的笔记里的说法"))
            .put("note_id", str("笔记 id，从【我的笔记】末尾的 note_id= 抄"))

        return JSONArray()
            .put(fn("start_quiz", "用户想被考、说「出题 / 考考我 / 测一下」时调用：开始 3 题小测。不要在回答里自己编题。"))
            .put(fn("skip_task", "用户明确说这张卡今天不学、先跳过时调用。"))
            .put(fn("show_plan", "用户问计划、进度、还要学多久、落后几天时调用，让程序把真实数字发给他。"))
            .put(
                fn(
                    "ask_conflict",
                    "资料和我的笔记对同一个概念说法矛盾时调用：把两边各一句传上来，让用户在聊天里裁定。" +
                        "不许自己选边；一轮最多调 1 次，问完继续回答。",
                    conflictProps,
                    listOf("concept", "material_claim", "material_src", "note_claim", "note_id"),
                )
            )
    }

    // ---------------------------------------------------------------- 一轮
    suspend fun reply(
        ctx: Context,
        convId: Long,
        taskId: Long?,
        goalId: String,
        userText: String,
        onDelta: (String) -> Unit,
    ): Reply {
        val text = userText.trim()
        if (text.isEmpty()) throw ChatError("空消息")
        val dao = AppDb.get(ctx).dao()
        val task = taskId?.let { dao.task(it) }
        val goal: Goal? = dao.goal(goalId)
        val key = Secure.apiKey(ctx)
        if (key.isBlank()) throw ChatError("没配模型 key，去「设置」里填")

        // 自由聊天：不绑卡、不绑目标，检索走全部资料，上下文给今日任务
        val free = task == null && goalId.isBlank()
        val hits = when {
            free -> Retrieval.searchFree(ctx, text, 3)
            goal != null -> Retrieval.search(ctx, goal, text, 3)
            else -> emptyList()
        }
        val cp = if (free) emptyList() else goal?.let { Retrieval.cpList(it) }.orEmpty()
        val gid = goalId.ifBlank { task?.goalId.orEmpty() }
        val notes = if (free || gid.isBlank()) emptyList() else dao.notesForGoal(gid)
        val arbs = if (free || gid.isBlank()) emptyList() else dao.arbitrationsFor(gid)
        val courseName = task?.let { t -> dao.courses().firstOrNull { it.id == t.course }?.name }
            ?: goal?.let { g -> dao.courses().firstOrNull { it.id == g.course }?.name }
            ?: "当前课程"
        val sys = if (free) {
            freeSystemPrompt(stateBarAll(ctx), taskContext(ctx), hits)
        } else {
            systemPrompt(
                courseName,
                stateBar(ctx, task),
                goal?.title ?: task?.title ?: "",
                goal?.ch ?: "",
                cp,
                hits,
                notes,
                arbs,
            )
        }

        val messages = JSONArray().put(JSONObject().put("role", "system").put("content", sys))
        for (m in trim(dao.messages(convId, 50).reversed())) {
            when (m.role) {
                "me" -> if (m.text.isNotBlank())
                    messages.put(JSONObject().put("role", "user").put("content", m.text))
                "bot" -> if (m.text.isNotBlank())
                    messages.put(JSONObject().put("role", "assistant").put("content", m.text))
            }
        }
        messages.put(JSONObject().put("role", "user").put("content", text))

        saveMessage(ctx, convId, "me", text)
        putOutbox(ctx, "message", JSONObject()
            .put("task_id", taskId ?: JSONObject.NULL)
            .put("conv_id", convId)
            .put("text", text))

        val model = dao.meta("model")?.ifBlank { null } ?: "deepseek-flash"
        var result = DeepSeek.stream(key, model, messages, if (free) toolsFree() else toolsJson(), onDelta)
        var tokens = result.tokens
        var out = result.text
        var toolUsed = ""
        var conflict: Conflict? = null

        var rounds = 0
        while (result.calls.isNotEmpty() && rounds < 1) {
            val assistant = JSONObject().put("role", "assistant").put("content", out)
            val tcOut = JSONArray()
            for (c in result.calls) {
                tcOut.put(
                    JSONObject().put("id", c.id).put("type", "function")
                        .put("function", JSONObject().put("name", c.name).put("arguments", c.arguments))
                )
            }
            assistant.put("tool_calls", tcOut)
            messages.put(assistant)
            for (c in result.calls.take(2)) {
                // 一轮最多问 1 次：第二个 ask_conflict 不再弹按钮
                if (!free && c.name == "ask_conflict" && conflict == null) {
                    conflict = conflictOf(ctx, gid, c.arguments)
                }
                val resText = runTool(ctx, convId, task, c.name, c.arguments, gid)
                toolUsed = c.name
                saveMessage(ctx, convId, "tool", "${c.name}: $resText")
                messages.put(
                    JSONObject().put("role", "tool").put("tool_call_id", c.id)
                        .put("content", resText.take(1500))
                )
            }
            result = DeepSeek.stream(key, model, messages, null, onDelta)
            tokens += result.tokens
            if (result.text.isNotBlank()) out = result.text
            rounds++
        }

        if (out.isBlank()) out = "（这轮没说出内容，换个问法试试）"
        out = plain(out)
        val sources = hits.map { it.src to it.anchor }
        saveMessage(ctx, convId, "bot", out, sources, tokens)
        dao.touchConversation(convId, nowStr())
        return Reply(out, sources, toolUsed, tokens, conflict)
    }

    // ---------------------------------------------------------------- 工具
    suspend fun runTool(
        ctx: Context,
        convId: Long,
        task: Task?,
        name: String,
        args: String = "{}",
        goalId: String = "",
    ): String {
        val dao = AppDb.get(ctx).dao()
        val free = task == null && goalId.isBlank()
        if (free) {
            // 自由聊天不绑卡：出题/跳过要用户先选一张卡，别让模型瞎调
            return when (name) {
                "show_plan" -> "现在：${stateBarAll(ctx).replace("\n", "；")}"
                "start_quiz" ->
                    "手机端是自由聊天：点输入框左边「＋ → 考我」，从今天的卡里选一张再出题。"
                "skip_task" ->
                    "自由聊天不绑卡；要跳过某张卡，去电脑端，或先从「＋ → 考我」选一张。"
                else -> "这个动作现在做不了"
            }
        }
        return when (name) {
            "start_quiz" -> "出题在右上「＋ → 考我」里打开小测页（3 题，答对 2 题才算完成），你只管继续讲。"
            "skip_task" -> {
                if (task == null) {
                    "这张卡已经不在计划里了。"
                } else {
                    dao.setTaskState(task.id, "skipped")
                    putOutbox(ctx, "task_state", JSONObject()
                        .put("task_id", task.id).put("state", "skipped"))
                    "已跳过这张卡，明天重排会挪回来。"
                }
            }
            "show_plan" -> {
                val bar = stateBar(ctx, task).replace("\n", "；")
                "现在：$bar"
            }
            "ask_conflict" -> {
                // 不执行：真正的裁定在聊天里由用户点按钮（reply 会带 conflict 对象给界面）
                val a = try {
                    JSONObject(args.ifBlank { "{}" })
                } catch (_: Exception) {
                    JSONObject()
                }
                val concept = a.optString("concept").trim()
                val gid = goalId.ifBlank { task?.goalId.orEmpty() }
                if (concept.isBlank()) {
                    "没听清是哪个概念对不上，先按资料继续讲。"
                } else {
                    val prev = if (gid.isBlank()) null else dao.arbitrationFor(gid, concept)
                    if (prev != null) {
                        "已裁定：${verdictCn(prev.verdict)}。按这个继续讲，不要再问。"
                    } else {
                        "concept=$concept（已问用户，等裁定；先按资料讲，别自己选边）"
                    }
                }
            }
            else -> "这个动作现在做不了"
        }
    }

    /** 模型报了矛盾、且这个概念还没有裁定时，取出来给界面弹按钮；否则 null。 */
    private suspend fun conflictOf(ctx: Context, gid: String, args: String): Conflict? {
        val a = try {
            JSONObject(args.ifBlank { "{}" })
        } catch (_: Exception) {
            return null
        }
        val concept = a.optString("concept").trim()
        if (concept.isBlank()) return null
        if (gid.isNotBlank() && AppDb.get(ctx).dao().arbitrationFor(gid, concept) != null) {
            return null          // 已经裁定过：runTool 会回「已裁定」，不再弹按钮
        }
        return Conflict(
            concept = concept,
            materialClaim = a.optString("material_claim").trim(),
            materialSrc = a.optString("material_src").trim(),
            noteClaim = a.optString("note_claim").trim(),
            noteId = a.optString("note_id").trim(),
        )
    }

    /** 冲突消息的正文（聊天里那条系统消息）。 */
    fun conflictText(c: Conflict): String {
        val src = c.materialSrc.ifBlank { "资料" }
        return "资料和你的笔记说法对不上：${c.concept}\n" +
            "资料：《$src》${c.materialClaim}\n" +
            "我的笔记：${c.noteClaim}\n" +
            "按哪边？点下面按钮，我只记这一次，以后这个概念都按你选的讲。"
    }

    /** 用户点了「按资料 / 按我的笔记」：写 arbitrations + outbox + 聊天留一条「已按…记下」。 */
    suspend fun decide(
        ctx: Context,
        convId: Long,
        task: Task?,
        goalId: String,
        conflict: Conflict,
        verdict: String,
    ): Arbitration {
        val dao = AppDb.get(ctx).dao()
        val gid = goalId.ifBlank { task?.goalId.orEmpty() }
        val course = task?.course?.ifBlank { null } ?: dao.goal(gid)?.course.orEmpty()
        val v = if (verdict == "note") "note" else "material"
        val uid = newLocalId().toString()
        val at = nowStr()
        val row = Arbitration(
            uid = uid,
            course = course,
            goalId = gid,
            concept = conflict.concept,
            materialClaim = conflict.materialClaim,
            materialSrc = conflict.materialSrc,
            noteId = conflict.noteId,
            noteClaim = conflict.noteClaim,
            verdict = v,
            decidedAt = at,
            decidedBy = "user",
            hits = 1,
        )
        dao.putArbitration(row)
        // payload 和 arbitrations 行同字段；事件 uid 用同一个，服务端按 uid 幂等
        putOutbox(
            ctx, "arbitration", JSONObject()
                .put("uid", uid)
                .put("course", course)
                .put("goal_id", gid)
                .put("concept", conflict.concept)
                .put("material_claim", conflict.materialClaim)
                .put("material_src", conflict.materialSrc)
                .put("note_id", conflict.noteId)
                .put("note_claim", conflict.noteClaim)
                .put("verdict", v)
                .put("decided_at", at)
                .put("decided_by", "user")
                .put("hits", 1),
            uid = uid,
        )
        val label = verdictCn(v)
        saveMessage(ctx, convId, "tool", "已${label}记下：${conflict.concept}")
        dao.touchConversation(convId, nowStr())
        return row
    }

    /** 命令菜单里的「看错题」：只查本地题库统计，不花钱。 */
    suspend fun wrongStats(ctx: Context, goalId: String): String {
        val stat = AppDb.get(ctx).dao().quizStat(goalId) ?: return "这个目标还没有答题记录。"
        val tail = if (stat.failStreak > 0) "连续没过 ${stat.failStreak} 次" else "没有连续没过"
        return "第 ${stat.batch} 批 · 答对 ${stat.passed}/${stat.answered} · $tail"
    }

    // ---------------------------------------------------------------- 落库
    private suspend fun saveMessage(
        ctx: Context,
        convId: Long,
        role: String,
        text: String,
        sources: List<Pair<String, String>> = emptyList(),
        tokens: Int = 0,
    ) {
        val arr = JSONArray()
        for ((s, a) in sources) arr.put(JSONObject().put("src", s).put("anchor", a))
        AppDb.get(ctx).dao().putMessageList(
            listOf(
                Message(
                    id = newLocalId(),
                    convId = convId,
                    role = role,
                    text = text.take(8000),
                    sources = arr.toString(),
                    tokens = tokens,
                    createdAt = nowStr(),
                )
            )
        )
    }

    /** uid 可传：裁定事件要沿用 arbitrations 行的 uid（默认仍是新 UUID）。 */
    suspend fun putOutbox(ctx: Context, kind: String, payload: JSONObject, uid: String? = null) {
        val eventUid = uid?.ifBlank { null } ?: UUID.randomUUID().toString()
        val body = JSONObject(payload.toString()).put("uid", eventUid).put("kind", kind)
        AppDb.get(ctx).dao().putOutbox(
            OutboxItem(
                uid = eventUid,
                kind = kind,
                payload = body.toString(),
                createdAt = nowStr(),
                pending = true,
                underReview = false,
            )
        )
    }

    suspend fun clear(ctx: Context, convId: Long) {
        val dao = AppDb.get(ctx).dao()
        dao.clearMessages(convId)
        dao.touchConversation(convId, nowStr())
    }
}
