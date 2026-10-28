package com.studybot.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
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
import com.studybot.app.data.Task
import com.studybot.app.databinding.ActivityChatBinding
import com.studybot.app.ui.color
import com.studybot.app.ui.dp
import com.studybot.app.voice.VoiceChat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray

class ChatActivity : AppCompatActivity() {

    private lateinit var binding: ActivityChatBinding
    private var conv: Conversation? = null
    private var task: Task? = null
    private var convId: Long = 0
    private var taskId: Long = 0
    private var goalId: String = ""

    private var voice: VoiceChat? = null
    private var ttsOn = true

    companion object {
        const val EXTRA_CONV = "conv_id"
        const val EXTRA_TASK = "task_id"
        const val EXTRA_GOAL = "goal_id"
        const val EXTRA_TITLE = "title"
    }

    /** 麦克风权限：给了就按刚才那一下开听，拒绝就说清楚退回打字。 */
    private val micPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (!granted) {
                toast("没给麦克风权限，只能用打字")
                return@registerForActivityResult
            }
            if (conv == null) {
                toast("会话还没准备好，等一下再点")
                return@registerForActivityResult
            }
            voice?.onMicTap()
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityChatBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        binding.toolbar.setNavigationOnClickListener { finish() }

        convId = intent.getLongExtra(EXTRA_CONV, 0L)
        taskId = intent.getLongExtra(EXTRA_TASK, 0L)
        goalId = intent.getStringExtra(EXTRA_GOAL).orEmpty()

        binding.toolbar.title = intent.getStringExtra(EXTRA_TITLE) ?: "带练"
        binding.sendButton.setOnClickListener { send(null) }
        binding.moreButton.setOnClickListener { showMenu() }
        initVoice()

        lifecycleScope.launch {
            val dao = AppDb.get(this@ChatActivity).dao()
            task = if (taskId > 0) dao.task(taskId) else null
            conv = when {
                convId > 0 -> dao.conv(convId)
                task != null -> ChatService.conversationFor(this@ChatActivity, task!!)
                else -> ChatService.freeConversation(this@ChatActivity)
            }
            convId = conv?.id ?: 0
            if (task != null) {
                goalId = task!!.goalId
                binding.toolbar.title = task!!.title
            } else if (goalId.isBlank()) {
                goalId = conv?.goalId.orEmpty()
            }
            renderHistory()
            binding.stateBar.text = withContext(Dispatchers.IO) {
                ChatService.stateBar(this@ChatActivity, task)
            }
        }
    }

    /** 从小测页回来时刷新状态条（答对 2 题后卡片已是 done）。 */
    override fun onResume() {
        super.onResume()
        voice?.resume()
        if (!this::binding.isInitialized || conv == null) return
        lifecycleScope.launch {
            binding.stateBar.text = withContext(Dispatchers.IO) {
                ChatService.stateBar(this@ChatActivity, task)
            }
        }
    }

    /** 切走就闭嘴、停麦：别在后台说话或录音。 */
    override fun onPause() {
        super.onPause()
        voice?.pause()
    }

    override fun onDestroy() {
        super.onDestroy()
        voice?.shutdown()
        voice = null
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
                    // 边说边显示，先不进气泡，等最终结果再决定发不发
                    binding.input.setText(text)
                    binding.input.setSelection(binding.input.text?.length ?: 0)
                }

                override fun onFinal(text: String) {
                    if (text.isBlank()) return
                    if (voice?.mode == VoiceChat.Mode.CONTINUOUS) {
                        // 免手：说完直接发；回复读完会自己再开麦
                        binding.input.setText("")
                        send(text)
                    } else {
                        // 手动：只填输入框，点「发送」才发
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
        voice?.onMicTap()
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
        binding.messages.removeAllViews()
        if (msgs.isEmpty()) {
            addBubble("sys", "还没聊过。可以先问「这节考什么」，或者说说你卡在哪。")
        }
        for (m in msgs) {
            when (m.role) {
                "me" -> addBubble("me", m.text)
                "bot" -> {
                    addBubble("bot", m.text)
                    addSources(parseSources(m.sources))
                }
                "tool" -> addBubble("sys", "· ${m.text.take(200)}")
            }
        }
        scrollDown()
    }

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
            textSize = if (role == "sys" || role == "tool") 12f else 14f
            setTextColor(color(if (role == "sys" || role == "tool") R.color.muted else R.color.text))
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

    // ---------------------------------------------------------------- 冲突裁定
    /** P7：资料和笔记说法打架 → 一条系统消息 + 两个按钮；点完禁用，按裁定接着答。 */
    private fun showConflict(conflict: ChatService.Conflict) {
        addBubble("sys", ChatService.conflictText(conflict))

        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(dp(12), 0, dp(12), dp(8))
        }
        fun button(label: String): MaterialButton = MaterialButton(this).apply {
            text = label
            textSize = 12f
            isAllCaps = false
        }
        val byMaterial = button("按资料")
        val byNote = button("按我的笔记")
        row.addView(
            byMaterial,
            LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { rightMargin = dp(8) }
        )
        row.addView(byNote, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT))
        binding.messages.addView(row)
        scrollDown()

        fun pick(verdict: String) {
            byMaterial.isEnabled = false      // 点完禁用：一次只记一个裁定
            byNote.isEnabled = false
            val label = ChatService.verdictCn(verdict)
            lifecycleScope.launch {
                try {
                    withContext(Dispatchers.IO) {
                        ChatService.decide(
                            this@ChatActivity, convId, task, goalId, conflict, verdict
                        )
                    }
                    addBubble("sys", "已${label}记下。")
                    // 把裁定当下一轮 user 消息，模型带着「已裁定」的 system prompt 继续答
                    send(if (verdict == "note") "按我的笔记，继续。" else "按资料，继续。")
                } catch (e: Exception) {
                    addBubble("sys", "没记上：${e.message ?: e.javaClass.simpleName}")
                }
            }
        }
        byMaterial.setOnClickListener { pick("material") }
        byNote.setOnClickListener { pick("note") }
    }

    // ---------------------------------------------------------------- 发送
    private fun send(preset: String?) {
        val input: EditText = binding.input
        val msg = (preset ?: input.text?.toString().orEmpty()).trim()
        if (msg.isEmpty() || conv == null) {
            // 语音进来的空话/会话没就绪：别把状态卡在「在想」
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
                val reply = ChatService.reply(this@ChatActivity, convId, task?.id, goalId, msg) { chunk ->
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
                    reply.conflict?.let { showConflict(it) }
                    scrollDown()
                    binding.sendButton.isEnabled = true
                    // 回复完成：TTS 朗读纯文本；读完按模式自动开麦 / 回空闲
                    voice?.onReply(reply.text)
                }
                binding.stateBar.text = withContext(Dispatchers.IO) {
                    ChatService.stateBar(this@ChatActivity, task)
                }
            } catch (e: Exception) {
                runOnUiThread {
                    streamView?.text = "（没说完就断了）"
                    addBubble("sys", "发送失败：${e.message ?: e.javaClass.simpleName}")
                    if (binding.input.text.isNullOrBlank()) binding.input.setText(msg)  // 不丢草稿
                    binding.sendButton.isEnabled = true
                    voice?.onTurnFailed()
                }
            }
        }
    }

    // ---------------------------------------------------------------- 菜单
    private fun showMenu() {
        val popup = PopupMenu(this, binding.moreButton)
        val items = listOf("考我", "看计划", "跳过这张卡", "换一张卡", "清空会话", "看错题")
        items.forEachIndexed { i, s -> popup.menu.add(0, 100 + i, i, s) }
        popup.setOnMenuItemClickListener { item ->
            when (item.itemId - 100) {
                0 -> startQuiz()
                1 -> runTool("show_plan")
                2 -> runTool("skip_task")
                3 -> switchTask()
                4 -> clearConversation()
                5 -> showWrong()
            }
            true
        }
        popup.show()
    }

    /** 「考我」：打开本机小测页（本机出题 + 判分，通过才算完成）。 */
    private fun startQuiz() {
        val t = task
        if (t == null) {
            addBubble("sys", "先打开一张学习卡")
            return
        }
        startActivity(
            Intent(this, QuizActivity::class.java)
                .putExtra("task_id", t.id)
        )
    }

    private fun runTool(name: String) {
        lifecycleScope.launch {
            val out = ChatService.runTool(this@ChatActivity, convId, task, name)
            addBubble("sys", out)
            binding.stateBar.text = withContext(Dispatchers.IO) {
                ChatService.stateBar(this@ChatActivity, task)
            }
        }
    }

    private fun showWrong() {
        if (goalId.isBlank()) {
            addBubble("sys", "自由问答没有绑定目标，先打开一张学习卡再看错题。")
            return
        }
        lifecycleScope.launch {
            addBubble("sys", withContext(Dispatchers.IO) {
                ChatService.wrongStats(this@ChatActivity, goalId)
            })
        }
    }

    private fun switchTask() {
        lifecycleScope.launch {
            val tasks = withContext(Dispatchers.IO) {
                AppDb.get(this@ChatActivity).dao().tasksOn(todayStr())
            }
            if (tasks.isEmpty()) {
                addBubble("sys", "今天没有别的卡了。")
                return@launch
            }
            val titles = tasks.map { "${it.title}（${it.minutes} 分钟）" }.toTypedArray()
            AlertDialog.Builder(this@ChatActivity)
                .setTitle("换一张卡")
                .setItems(titles) { _, which ->
                    val t = tasks[which]
                    startActivity(
                        Intent(this@ChatActivity, ChatActivity::class.java)
                            .putExtra(EXTRA_TASK, t.id)
                            .putExtra(EXTRA_GOAL, t.goalId)
                            .putExtra(EXTRA_TITLE, t.title)
                    )
                }
                .show()
        }
    }

    private fun todayStr(): String =
        java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US).format(java.util.Date())

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
}
