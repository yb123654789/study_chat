package com.studybot.app

import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.studybot.app.chat.QuizService
import com.studybot.app.data.AppDb
import com.studybot.app.data.Goal
import com.studybot.app.data.Task
import com.studybot.app.ui.color
import com.studybot.app.ui.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 小测页：打开即后台出 3 题（本机调模型），提交后逐题判分。
 * 答对 ≥2 把卡置 done（并写 outbox），弹一句「这张卡已完成」后关页。
 * 页面全用代码拼，题目只活在内存里，退出就没了。
 */
class QuizActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_TASK = "task_id"
        private val LETTERS = arrayOf("A", "B", "C", "D", "E", "F")
    }

    private var taskId = 0L
    private var task: Task? = null
    private var goal: Goal? = null
    private var questions: List<QuizService.Q> = emptyList()
    private val inputs = ArrayList<View>()
    private val tags = ArrayList<TextView>()
    private var submitted = false
    private var busy = false

    private lateinit var toolbar: MaterialToolbar
    private lateinit var status: TextView
    private lateinit var content: LinearLayout
    private lateinit var submitButton: MaterialButton

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        taskId = intent.getLongExtra(EXTRA_TASK, 0L)
        setContentView(buildUi())
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        toolbar.setNavigationOnClickListener { finish() }

        lifecycleScope.launch {
            val loaded = withContext(Dispatchers.IO) {
                val dao = AppDb.get(this@QuizActivity).dao()
                val t = if (taskId > 0) dao.task(taskId) else null
                t to t?.let { dao.goal(it.goalId) }
            }
            task = loaded.first
            goal = loaded.second
            val t = task
            val g = goal
            if (t == null || g == null) {
                status.setTextColor(color(R.color.err))
                status.text = "找不到这张卡。先回「今日」打开一张学习卡。"
                return@launch
            }
            toolbar.title = "小测 · ${t.title}"
            val noKey = Secure.apiKey(this@QuizActivity).isBlank()

            val qs = withContext(Dispatchers.IO) {
                QuizService.generate(this@QuizActivity, g)
            }
            questions = qs
            if (qs.isEmpty()) {
                status.setTextColor(color(R.color.err))
                status.text = "没出成题，回上一页再试。"
                return@launch
            }
            renderQuestions(qs)
            status.text = if (noKey) {
                "没配模型 key：用验收点出题，简答按关键词判。"
            } else {
                "共 ${qs.size} 题 · 答对 2 题算通过"
            }
            submitButton.isEnabled = true
        }
    }

    // ---------------------------------------------------------------- 拼界面
    private fun buildUi(): LinearLayout {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(color(R.color.bg))
        }
        toolbar = MaterialToolbar(this).apply {
            setBackgroundColor(color(R.color.card))
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, dp(56))
            title = "小测"
        }
        root.addView(toolbar)

        status = TextView(this).apply {
            textSize = 12f
            setTextColor(color(R.color.muted))
            setPadding(dp(16), dp(8), dp(16), dp(4))
            text = "正在出题…"
        }
        root.addView(status)

        val scroll = ScrollView(this).apply {
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f)
            isFillViewport = true
        }
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(4), dp(12), dp(4))
        }
        scroll.addView(content)
        root.addView(scroll)

        submitButton = MaterialButton(this).apply {
            text = "提交"
            isEnabled = false
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply {
                setMargins(dp(16), dp(6), dp(16), dp(12))
            }
            setOnClickListener { onSubmit() }
        }
        root.addView(submitButton)
        return root
    }

    private fun renderQuestions(qs: List<QuizService.Q>) {
        content.removeAllViews()
        inputs.clear()
        tags.clear()
        for ((i, q) in qs.withIndex()) {
            val card = MaterialCardView(this).apply {
                layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply {
                    bottomMargin = dp(8)
                }
                radius = dp(12).toFloat()
                cardElevation = 0f
                strokeWidth = dp(1)
                strokeColor = color(R.color.line)
                setCardBackgroundColor(color(R.color.card))
            }
            val box = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(14), dp(11), dp(14), dp(11))
            }
            val kind = if (q.type == "choice") "单选" else "简答"
            val tag = TextView(this).apply {
                text = "第 ${i + 1} 题 · $kind"
                textSize = 11f
                setTextColor(color(R.color.muted))
            }
            box.addView(tag)
            tags.add(tag)
            box.addView(
                TextView(this).apply {
                    text = q.stem
                    textSize = 14.5f
                    setTextColor(color(R.color.text))
                    setPadding(0, dp(4), 0, dp(8))
                }
            )
            val input: View = if (q.type == "choice") {
                val group = RadioGroup(this).apply {
                    orientation = RadioGroup.VERTICAL
                }
                for ((oi, opt) in q.options.withIndex()) {
                    val label = if (oi < LETTERS.size) "${LETTERS[oi]}. $opt" else opt
                    group.addView(
                        RadioButton(this).apply {
                            id = 1000 * (i + 1) + oi
                            text = label
                            textSize = 13.5f
                            setTextColor(color(R.color.text))
                            setPadding(0, dp(2), 0, dp(2))
                        }
                    )
                }
                group
            } else {
                EditText(this).apply {
                    inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
                    minLines = 3
                    gravity = Gravity.TOP or Gravity.START
                    textSize = 13.5f
                    setTextColor(color(R.color.text))
                    hint = "用自己的话写两三句（不用抄原文）"
                    setPadding(dp(8), dp(8), dp(8), dp(8))
                }
            }
            box.addView(input, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
            card.addView(box)
            content.addView(card)
            inputs.add(input)
        }
        content.requestLayout()
    }

    // ---------------------------------------------------------------- 提交
    private fun collectAnswers(): List<String> {
        val out = ArrayList<String>(questions.size)
        for ((i, q) in questions.withIndex()) {
            val v = inputs.getOrNull(i)
            when {
                q.type == "choice" && v is RadioGroup -> {
                    val checked = v.checkedRadioButtonId
                    out.add(if (checked == -1) "" else (checked - 1000 * (i + 1)).toString())
                }
                v is EditText -> out.add(v.text?.toString().orEmpty().trim())
                else -> out.add("")
            }
        }
        return out
    }

    /** 逐题标对错：判分后每题小标题变「✓ / ✗」。 */
    private fun markResults(results: List<Boolean>) {
        for ((i, ok) in results.withIndex()) {
            val tag = tags.getOrNull(i) ?: continue
            val kind = if (questions.getOrNull(i)?.type == "choice") "单选" else "简答"
            tag.text = "第 ${i + 1} 题 · $kind · ${if (ok) "对" else "错"}"
            tag.setTextColor(color(if (ok) R.color.secondary else R.color.err))
        }
    }

    private fun onSubmit() {
        val t = task ?: return
        if (submitted) {
            finish()
            return
        }
        if (busy || questions.isEmpty()) return
        val answers = collectAnswers()
        busy = true
        submitButton.isEnabled = false
        status.setTextColor(color(R.color.muted))
        status.text = "正在判分…"

        lifecycleScope.launch {
            try {
                val outcome = withContext(Dispatchers.IO) {
                    QuizService.submitResults(this@QuizActivity, t, questions, answers)
                }
                busy = false
                submitted = true
                submitButton.text = "关闭"
                submitButton.isEnabled = true
                markResults(outcome.results)
                if (outcome.passed >= 2) {
                    status.setTextColor(color(R.color.secondary))
                    status.text = "答对 ${outcome.passed}/${outcome.total} · 这张卡已完成"
                    AlertDialog.Builder(this@QuizActivity)
                        .setTitle("这张卡已完成")
                        .setMessage("${outcome.total} 题答对 ${outcome.passed} 题。明天排下一张。")
                        .setCancelable(false)
                        .setPositiveButton("好") { _, _ -> finish() }
                        .show()
                } else {
                    val tail = when (outcome.followUp) {
                        "review" -> " · 明天排了错题重做"
                        "learn" -> " · 连续两次没过，明天回炉重学"
                        "exists" -> " · 明天已有同类卡，没重复排"
                        else -> ""
                    }
                    status.setTextColor(color(R.color.warn))
                    status.text = "答对 ${outcome.passed}/${outcome.total} · 没过$tail"
                }
            } catch (e: Exception) {
                busy = false
                submitButton.isEnabled = true
                status.setTextColor(color(R.color.err))
                status.text = "判分失败：${e.message ?: e.javaClass.simpleName}"
            }
        }
    }
}
