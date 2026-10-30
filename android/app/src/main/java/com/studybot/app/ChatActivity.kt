package com.studybot.app

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.speech.RecognizerIntent
import android.view.Gravity
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.studybot.app.chat.ChatService
import com.studybot.app.data.AppDb
import com.studybot.app.data.Conversation
import com.studybot.app.databinding.ActivityChatBinding
import com.studybot.app.sync.Syncer
import com.studybot.app.ui.color
import com.studybot.app.ui.dp
import com.studybot.app.voice.VoiceChat
import com.studybot.app.voice.VoiceSelfCheck
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray

/**
 * 手机端唯一入口：一个自由聊天窗口。
 *
 * 不绑卡、不列计划：上下文里带上程序排好的今日任务和全部资料检索，
 * 让用户和 AI 商量学什么；要出题从「＋ → 考我」里选今天的卡。
 */
class ChatActivity : AppCompatActivity() {

    private lateinit var binding: ActivityChatBinding
    private var conv: Conversation? = null
    private var convId: Long = 0L

    private var voice: VoiceChat? = null
    private var ttsOn = true
    private var syncing = false
    private var fallbackShown = false
    private var micTouched = false

    private val LEGACY_NULL = Regex("(?i)^(?:null\\s*)+$")

    companion object {
        const val EXTRA_CONV = "conv_id"
        // 旧的多 tab 页面还引用这三个常量（入口已摘，留着只为编译兼容）
        const val EXTRA_TASK = "task_id"
        const val EXTRA_GOAL = "goal_id"
        const val EXTRA_TITLE = "title"
    }

    /** 麦克风权限：拒绝就指路去系统设置；给了一样的按刚才那一下开听。 */
    private val micPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (!granted) {
                showPermissionHelp()
                return@registerForActivityResult
            }
            micTouched = true
            voice?.onMicTap()
        }

    /** 系统语音输入面板（输入法 / 语音助手）：内置识别用不了时的退路。 */
    private val systemVoice =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
            if (res.resultCode != RESULT_OK) return@registerForActivityResult
            val text = res.data
                ?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)
                ?.firstOrNull()
                ?.trim()
                .orEmpty()
            if (text.isBlank()) {
                toast("没听清，再说一次或打字")
                return@registerForActivityResult
            }
            if (voice?.mode == VoiceChat.Mode.CONTINUOUS) {
                binding.input.setText("")
                send(text)
            } else {
                binding.input.setText(text)
                binding.input.setSelection(binding.input.text?.length ?: 0)
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityChatBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(false)
        binding.toolbar.title = "学习bot"

        binding.toolbar.inflateMenu(R.menu.menu_main)
        binding.toolbar.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                R.id.action_sync -> {
                    syncNow(silent = false)
                    true
                }
                R.id.action_settings -> {
                    startActivity(Intent(this, SettingsActivity::class.java))
                    true
                }
                else -> false
            }
        }

        binding.sendButton.setOnClickListener { send(null) }
        binding.moreButton.setOnClickListener { showMenu() }
        initVoice()

        convId = intent.getLongExtra(EXTRA_CONV, 0L)
        lifecycleScope.launch {
            val dao = AppDb.get(this@ChatActivity).dao()
            conv = if (convId > 0) dao.conv(convId) else ChatService.freeConversation(this@ChatActivity)
            convId = conv?.id ?: 0
            renderHistory()
            val hasKey = runCatching { Secure.apiKey(this@ChatActivity).isNotBlank() }
                .getOrDefault(true)
            if (!hasKey) {
                addBubble("sys", "还没配模型 key：点右上「设置」填 DeepSeek key，回来就能聊。")
            }
            refreshBar()
            syncNow(silent = true)
        }
    }

    override fun onResume() {
        super.onResume()
        voice?.resume()
        if (!this::binding.isInitialized || conv == null) return
        refreshBar()
    }

    override fun onPause() {
        super.onPause()
        voice?.pause()
    }

    override fun onDestroy() {
        super.onDestroy()
        voice?.shutdown()
        voice = null
    }

    // ---------------------------------------------------------------- 同步 / 状态
    private fun syncNow(silent: Boolean) {
        if (syncing) return
        syncing = true
        if (!silent) binding.stateBar.text = "正在同步…"
        lifecycleScope.launch {
            try {
                val res = withContext(Dispatchers.IO) {
                    Syncer(this@ChatActivity).sync(if (silent) 3_000L else 60_000L)
                }
                if (!silent) {
                    val msg = buildString {
                        append("同步完成：${res.tasks} 张卡")
                        if (res.blocks > 0) append(" · ${res.blocks} 块资料")
                    }
                    toast(msg)
                }
            } catch (e: Exception) {
                if (!silent) {
                    toast("同步失败：${e.message ?: e.javaClass.simpleName}")
                }
            } finally {
                syncing = false
                refreshBar()
            }
        }
    }

    private fun refreshBar() {
        lifecycleScope.launch {
            val pair = withContext(Dispatchers.IO) {
                val bar = ChatService.stateBarAll(this@ChatActivity)
                val last = AppDb.get(this@ChatActivity).dao().meta("last_sync")
                bar to last
            }
            if (isFinishing || isDestroyed) return@launch
            val ts = pair.second?.toLongOrNull() ?: 0L
            val syncLine = if (ts <= 0L) {
                "还没同步 · 右上「设置」填地址和 token"
            } else {
                "上次同步：" + ago(ts)
            }
            binding.stateBar.text = pair.first + "\n" + syncLine
        }
    }

    private fun ago(ts: Long): String {
        val diff = System.currentTimeMillis() - ts
        return when {
            diff < 60_000L -> "刚刚"
            diff < 3_600_000L -> "${diff / 60_000L} 分钟前"
            diff < 86_400_000L -> "${diff / 3_600_000L} 小时前"
            else -> "${diff / 86_400_000L} 天前"
        }
    }

    // ---------------------------------------------------------------- 语音
    private fun initVoice() {
        val prefs = getSharedPreferences("voice", MODE_PRIVATE)
        ttsOn = prefs.getBoolean("tts", true)
        val startMode = if (prefs.getString("mode", "continuous") == "manual") {
            VoiceChat.Mode.MANUAL
        } else {
            VoiceChat.Mode.CONTINUOUS
        }

        val v = VoiceChat(
            this,
            object : VoiceChat.Callbacks {
                override fun onPartial(text: String) {
                    if (text.isBlank()) return
                    binding.input.setText(text)
                    binding.input.setSelection(binding.input.text?.length ?: 0)
                }

                override fun onFinal(text: String) {
                    if (text.isBlank()) return
                    if (voice?.mode == VoiceChat.Mode.CONTINUOUS) {
                        binding.input.setText("")
                        send(text)
                    } else {
                        binding.input.setText(text)
                        binding.input.setSelection(binding.input.text?.length ?: 0)
                    }
                }

                override fun onModeChanged(mode: VoiceChat.Mode, reason: String) {
                    binding.modeButton.text = modeLabel(mode)
                    prefs.edit()
                        .putString(
                            "mode",
                            if (mode == VoiceChat.Mode.CONTINUOUS) "continuous" else "manual"
                        )
                        .apply()
                }

                override fun onNotice(text: String) {
                    toast(text)
                }

                override fun onUnavailable(reason: String) {
                    offerSystemVoice(reason)
                }
            },
            startMode,
            ttsOn,
        )
        voice = v

        binding.modeButton.text = modeLabel(v.mode)
        binding.ttsButton.text = ttsLabel(ttsOn)
        binding.micButton.setOnClickListener { onMicClick() }
        binding.modeButton.setOnClickListener {
            val vv = voice ?: return@setOnClickListener
            vv.setMode(
                if (vv.mode == VoiceChat.Mode.CONTINUOUS) VoiceChat.Mode.MANUAL
                else VoiceChat.Mode.CONTINUOUS
            )
        }
        binding.ttsButton.setOnClickListener {
            ttsOn = !ttsOn
            voice?.setTtsEnabled(ttsOn)
            binding.ttsButton.text = ttsLabel(ttsOn)
            prefs.edit().putBoolean("tts", ttsOn).apply()
            toast(if (ttsOn) "会朗读回复" else "不朗读，只看文字")
        }

        lifecycleScope.launch {
            v.state.collect { renderVoiceState(it) }
        }
        renderVoiceState(v.state.value)
    }

    private fun onMicClick() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            micPermission.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        if (conv == null) {
            toast("会话还没准备好，等一下再点")
            return
        }
        micTouched = true
        voice?.onMicTap()
    }

    /** 系统设置里给学习bot开了麦克风，这里仍可能没生效：直接指路。 */
    private fun showPermissionHelp() {
        AlertDialog.Builder(this)
            .setTitle("麦克风没开")
            .setMessage(
                "本 App 没拿到录音权限。\n\n" +
                    "去「设置 → 应用 → 学习bot → 权限 → 麦克风」选「使用应用时允许」，回来再点语音。\n\n" +
                    "也可以先用打字。"
            )
            .setPositiveButton("去设置") { _, _ -> VoiceSelfCheck.openAppSettings(this) }
            .setNegativeButton("打字", null)
            .show()
    }

    /** 内置识别彻底用不了：给一个「系统语音输入」的退路，别让人以为只能打字。 */
    private fun offerSystemVoice(reason: String) {
        if (fallbackShown || isFinishing || !micTouched) return
        fallbackShown = true
        AlertDialog.Builder(this)
            .setTitle("系统语音识别用不了")
            .setMessage(
                "$reason\n\n" +
                    "可以改用手机自己的语音输入（会弹系统录音面板，走输入法或语音助手），" +
                    "或者直接打字。"
            )
            .setPositiveButton("用系统语音输入") { _, _ -> launchSystemVoice() }
            .setNegativeButton("打字", null)
            .setOnDismissListener { fallbackShown = false }
            .show()
    }

    private fun launchSystemVoice() {
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
            putExtra(RecognizerIntent.EXTRA_PROMPT, "说一句，转成文字")
        }
        try {
            systemVoice.launch(intent)
        } catch (_: ActivityNotFoundException) {
            toast("这台手机没有系统语音输入，只能打字")
        }
    }

    private fun renderVoiceState(state: VoiceChat.State) {
        binding.voiceState.text = when (state) {
            VoiceChat.State.LISTENING -> "正在听…"
            VoiceChat.State.THINKING -> "在想…"
            VoiceChat.State.SPEAKING -> "正在说…"
            VoiceChat.State.IDLE -> "空闲"
        }
        binding.micButton.text = if (state == VoiceChat.State.LISTENING) "停止" else "语音"
    }

    private fun modeLabel(mode: VoiceChat.Mode): String =
        if (mode == VoiceChat.Mode.CONTINUOUS) "免手" else "手动"

    private fun ttsLabel(on: Boolean): String = if (on) "朗读开" else "朗读关"

    private fun toast(text: String) {
        Toast.makeText(this, text, Toast.LENGTH_SHORT).show()
    }

    // ---------------------------------------------------------------- 渲染
    private suspend fun renderHistory() {
        val msgs = withContext(Dispatchers.IO) { ChatService.history(this@ChatActivity, convId) }
            // 旧版本把 SSE 的 null 拼进了正文；只有 null 的旧消息不再显示
            .filterNot { it.text.trim().replace("null", "").isBlank() }
        binding.messages.removeAllViews()
        if (msgs.isEmpty()) {
            addBubble("sys", "直接问就行：今天学什么、这题不懂、帮我理一下思路。")
        }
        for (m in msgs) {
            when (m.role) {
                "me" -> addBubble("me", m.text)
                "bot" -> {
                    addBubble("bot", stripLegacyNull(m.text))
                    addSources(parseSources(m.sources))
                }
                "tool" -> addBubble("sys", "· ${m.text.take(200)}")
            }
        }
        scrollDown()
    }

    /** 旧会话里可能夹着整行 "null"（老版本 SSE 解析留下的），显示时去掉。 */
    private fun stripLegacyNull(text: String): String =
        text.lines()
            .filterNot { LEGACY_NULL.matches(it.trim()) }
            .joinToString("\n")
            .trim()

    private fun parseSources(raw: String): List<Pair<String, String>> {
        val out = ArrayList<Pair<String, String>>()
        try {
            val arr = JSONArray(raw.ifBlank { "[]" })
            for (i in 0 until arr.length()) {
                val o = arr.optJSONObject(i) ?: continue
                out.add(o.optString("src") to o.optString("anchor"))
            }
        } catch (_: Exception) {
        }
        return out
    }

    private fun addBubble(role: String, text: String): TextView {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, dp(4), 0, dp(4))
        }
        if (role == "me") row.gravity = Gravity.END
        val tv = TextView(this).apply {
            this.text = text
            textSize = if (role == "sys") 12f else 14f
            setTextColor(color(if (role == "sys") R.color.muted else R.color.text))
            setPadding(dp(12), dp(8), dp(12), dp(8))
            maxWidth = (resources.displayMetrics.widthPixels * 0.82).toInt()
        }
        if (role == "me" || role == "bot") {
            val bg = GradientDrawable().apply {
                cornerRadius = dp(14).toFloat()
                setColor(color(if (role == "me") R.color.line else R.color.bg))
            }
            tv.background = bg
        }
        row.addView(tv, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT))
        binding.messages.addView(row)
        scrollDown()
        return tv
    }

    private fun addSources(sources: List<Pair<String, String>>) {
        if (sources.isEmpty()) return
        val ref = sources.take(2).joinToString("、") { "《${it.first.take(28)}》${it.second}" }
        val tv = TextView(this).apply {
            text = "参考：$ref"
            textSize = 10f
            setTextColor(color(R.color.muted))
            setPadding(dp(12), 0, dp(12), dp(8))
        }
        binding.messages.addView(tv)
        scrollDown()
    }

    private fun scrollDown() {
        binding.scroll.post { binding.scroll.fullScroll(android.view.View.FOCUS_DOWN) }
    }

    // ---------------------------------------------------------------- 发送
    private fun send(preset: String?) {
        val input: EditText = binding.input
        val msg = (preset ?: input.text?.toString().orEmpty()).trim()
        if (msg.isEmpty() || conv == null) {
            voice?.onTurnFailed()
            return
        }
        input.setText("")
        addBubble("me", msg)
        binding.sendButton.isEnabled = false
        voice?.onThinking()
        var streamView: TextView? = null

        lifecycleScope.launch {
            try {
                // 自由聊天：不带 task / goal
                val reply = ChatService.reply(this@ChatActivity, convId, null, "", msg) { chunk ->
                    runOnUiThread {
                        if (streamView == null) {
                            streamView = addBubble("bot", chunk)
                        } else {
                            streamView?.append(chunk)
                            scrollDown()
                        }
                    }
                }
                runOnUiThread {
                    streamView?.text = reply.text
                    addSources(reply.sources)
                    scrollDown()
                    binding.sendButton.isEnabled = true
                    voice?.onReply(reply.text)
                }
                refreshBar()
            } catch (e: Exception) {
                runOnUiThread {
                    streamView?.text = "（没说完就断了）"
                    addBubble("sys", "发送失败：${e.message ?: e.javaClass.simpleName}")
                    if (binding.input.text.isNullOrBlank()) binding.input.setText(msg)
                    binding.sendButton.isEnabled = true
                    voice?.onTurnFailed()
                }
            }
        }
    }

    // ---------------------------------------------------------------- 菜单
    private fun showMenu() {
        val popup = PopupMenu(this, binding.moreButton)
        val items = listOf("考我（选今天的卡）", "系统语音输入", "看计划", "语音自检", "清空会话")
        items.forEachIndexed { i, s -> popup.menu.add(0, 100 + i, i, s) }
        popup.setOnMenuItemClickListener { item ->
            when (item.itemId - 100) {
                0 -> pickQuizTask()
                1 -> launchSystemVoice()
                2 -> runTool("show_plan")
                3 -> showVoiceCheck()
                4 -> clearConversation()
            }
            true
        }
        popup.show()
    }

    /** 自由聊天不绑卡：出题前先让用户从今天的卡里选一张。 */
    private fun pickQuizTask() {
        lifecycleScope.launch {
            val tasks = withContext(Dispatchers.IO) {
                AppDb.get(this@ChatActivity).dao().tasksOn(todayStr())
                    .filter { it.state != "done" && it.state != "dropped" }
            }
            if (tasks.isEmpty()) {
                addBubble("sys", "今天没有待做的卡。去电脑端看看计划，或者先让我给你讲讲。")
                return@launch
            }
            val titles = tasks.map { "${it.title}（${it.minutes} 分钟）" }.toTypedArray()
            AlertDialog.Builder(this@ChatActivity)
                .setTitle("考哪张卡")
                .setItems(titles) { _, which ->
                    startActivity(
                        Intent(this@ChatActivity, QuizActivity::class.java)
                            .putExtra(QuizActivity.EXTRA_TASK, tasks[which].id)
                    )
                }
                .show()
        }
    }

    private fun runTool(name: String) {
        lifecycleScope.launch {
            val out = ChatService.runTool(this@ChatActivity, convId, null, name, "{}", "")
            addBubble("sys", out)
            refreshBar()
        }
    }

    private fun showVoiceCheck() {
        val report = VoiceSelfCheck.report(this)
        AlertDialog.Builder(this)
            .setTitle("语音自检")
            .setMessage(report)
            .setPositiveButton("去系统设置") { _, _ -> VoiceSelfCheck.openAppSettings(this) }
            .setNeutralButton("复制") { _, _ ->
                val cm = getSystemService(CLIPBOARD_SERVICE) as android.content.ClipboardManager
                cm.setPrimaryClip(
                    android.content.ClipData.newPlainText("语音自检", report)
                )
                toast("已复制，发给我就能定位")
            }
            .setNegativeButton("好", null)
            .show()
    }

    private fun clearConversation() {
        AlertDialog.Builder(this)
            .setMessage("清空这条会话？只删聊天记录，任务和小测记录不受影响。")
            .setPositiveButton("清空") { _, _ ->
                lifecycleScope.launch {
                    withContext(Dispatchers.IO) { ChatService.clear(this@ChatActivity, convId) }
                    renderHistory()
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun todayStr(): String =
        java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US).format(java.util.Date())
}
