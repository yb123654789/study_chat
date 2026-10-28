package com.studybot.app

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.studybot.app.chat.ChatService
import com.studybot.app.chat.DeepSeek
import com.studybot.app.data.AppDb
import com.studybot.app.databinding.ActivityNoteFixBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * P5 纠错预览：原/纠对照，三选一（用纠正的 / 用原文 / 手动改）。
 * 没 key、没网、模型报错都直接回原文，不挡记笔记。
 */
class FixActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_RAW = "raw"
        const val EXTRA_COURSE = "course"
        const val EXTRA_GOAL = "goal"
        const val EXTRA_TEXT = "text"
        const val EXTRA_MODE = "mode"   // fixed / raw / manual
    }

    /** 写死：只许改错字，不许替用户写内容。 */
    private val systemPrompt = """
        你是中文笔记的纠错工具，只做校对。
        只改同音字、近形字、漏字、标点、分段；不许扩写、不许总结、不许补内容、不许改术语的意思。
        只输出改好的正文：不要解释、不要加引号、不要 Markdown、不要「纠正稿：」这类前缀。
    """.trimIndent()

    private lateinit var binding: ActivityNoteFixBinding
    private var raw = ""
    private var fixed = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityNoteFixBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        binding.toolbar.setNavigationOnClickListener { finish() }

        raw = intent.getStringExtra(EXTRA_RAW).orEmpty().trim()
        binding.rawText.text = raw.ifBlank { "（空）" }

        binding.btnUseFixed.setOnClickListener {
            if (fixed.isNotBlank()) finishWith("fixed", fixed)
        }
        binding.btnUseRaw.setOnClickListener { finishWith("raw", raw) }
        binding.btnManual.setOnClickListener { finishWith("manual", raw) }

        if (raw.isBlank()) {
            finishWith("raw", "")
            return
        }
        fix()
    }

    // ---------------------------------------------------------------- 调模型
    private fun fix() {
        val key = Secure.apiKey(this)
        if (key.isBlank()) {
            toast("没配模型 key，直接用原文")
            finishWith("raw", raw)
            return
        }
        binding.statusText.text = "正在请模型纠错…（没网会直接用原文）"
        lifecycleScope.launch {
            val out = try {
                withContext(Dispatchers.IO) { callModel(key) }
            } catch (e: Exception) {
                null
            }
            if (isFinishing || isDestroyed) return@launch
            if (out == null) {
                binding.statusText.text = "纠错没成功，直接用原文。"
                toast("纠错没成功，直接用原文")
                finishWith("raw", raw)
                return@launch
            }
            val clean = tidy(out)
            if (clean.isBlank()) {
                binding.statusText.text = "模型没给出内容，直接用原文。"
                toast("模型没给出内容，直接用原文")
                finishWith("raw", raw)
                return@launch
            }
            fixed = clean
            binding.fixedText.text = clean
            binding.btnUseFixed.isEnabled = true
            binding.statusText.text = "改好了：原 ${raw.length} 字 → 纠 ${clean.length} 字"
            val ratio = changeRatio(raw, clean)
            if (ratio > 0.30) {
                binding.warnText.visibility = View.VISIBLE
                binding.warnText.text =
                    "改动较大，建议自己看一眼（约 ${(ratio * 100).toInt()}% 的字变了）"
            }
        }
    }

    private suspend fun callModel(key: String): String {
        val dao = AppDb.get(this).dao()
        val model = dao.meta("model")?.ifBlank { null } ?: "deepseek-flash"
        val messages = JSONArray()
            .put(JSONObject().put("role", "system").put("content", systemPrompt))
            .put(JSONObject().put("role", "user").put("content", userPrompt()))
        return DeepSeek.complete(
            key = key,
            model = model,
            messages = messages,
            maxTokens = minOf(2400, maxOf(600, raw.length * 2)),
            temperature = 0.2,
        )
    }

    private fun userPrompt(): String {
        val course = intent.getStringExtra(EXTRA_COURSE).orEmpty().ifBlank { "（未指定）" }
        val goal = intent.getStringExtra(EXTRA_GOAL).orEmpty().ifBlank { "（未指定）" }
        return "课程：$course\n当前目标：$goal\n\n识别原文：\n$raw"
    }

    /** 去掉模型可能带的 Markdown 和整段引号（prompt 已经要求纯正文，这里兜底）。 */
    private fun tidy(text: String): String {
        var t = ChatService.plain(text).trim()
        val pairs = listOf("“" to "”", "\"" to "\"", "「" to "」", "『" to "』")
        for ((left, right) in pairs) {
            if (t.length >= 2 && t.startsWith(left) && t.endsWith(right)) {
                t = t.substring(1, t.length - 1).trim()
            }
        }
        return t
    }

    /** 按字数算改动比例：编辑距离 ÷ 较长那段的字数。 */
    private fun changeRatio(a: String, b: String): Double {
        if (a == b) return 0.0
        if (a.isEmpty() || b.isEmpty()) return 1.0
        if (a.length > 2000 || b.length > 2000) return 1.0
        val n = a.length
        val m = b.length
        var prev = IntArray(m + 1) { it }
        var cur = IntArray(m + 1)
        for (i in 1..n) {
            cur[0] = i
            for (j in 1..m) {
                val cost = if (a[i - 1] == b[j - 1]) 0 else 1
                cur[j] = minOf(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            }
            val swap = prev
            prev = cur
            cur = swap
        }
        return prev[m].toDouble() / maxOf(n, m)
    }

    private fun finishWith(mode: String, text: String) {
        setResult(
            RESULT_OK,
            Intent().putExtra(EXTRA_MODE, mode).putExtra(EXTRA_TEXT, text)
        )
        finish()
    }

    private fun toast(text: String) {
        Toast.makeText(this, text, Toast.LENGTH_SHORT).show()
    }
}
