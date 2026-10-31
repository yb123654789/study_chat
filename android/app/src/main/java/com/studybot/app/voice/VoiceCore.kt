package com.studybot.app.voice

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import com.studybot.app.chat.ChatService
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.Locale
import java.util.concurrent.CopyOnWriteArrayList

/**
 * 语音会话的核心状态机（进程内单例，由 [VoiceService] 保活）：
 *
 *   OFF → PREPARING（拷模型/加载）→ LISTENING ⇄ THINKING → SPEAKING → LISTENING…
 *
 * 和以前最大的区别：**麦克风一直开着**。VAD 断句，识别出一句就交给页面去问模型；
 * 朗读时麦克风也不关，靠硬件 AEC 压掉自己的声音，你插嘴就打断朗读（[bargeIn]）。
 *
 * 职责边界：
 * - 这里只管「听、说、状态」；调模型（DeepSeek）是页面的活。
 * - 页面通过 [Listener.onUtterance] 拿到识别文字，自己发请求；
 *   再调 [speakChunk] 把流式回复一句句喂进来，最后 [endReply] 收尾。
 *
 * 线程：所有方法都在主线程调；内部把模型加载/录音放后台。
 */
object VoiceCore {

    enum class State { OFF, PREPARING, LISTENING, THINKING, SPEAKING }

    interface Listener {
        fun onState(state: State) {}
        fun onUtterance(text: String) {}
        fun onNotice(text: String) {}
        fun onProgress(pct: Int) {}
    }

    private val main = Handler(Looper.getMainLooper())
    private val listeners = CopyOnWriteArrayList<Listener>()
    private val _state = MutableStateFlow(State.OFF)
    val state: StateFlow<State> = _state.asStateFlow()

    private var appContext: Context? = null
    private var asr: LocalAsr? = null
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private var ttsMissing = false

    private var ttsEnabled = true
    private var bargeInEnabled = true

    private var replyOpen = false
    private var pendingSeg = 0
    private var seq = 0
    private var lastSpokenFrag = ""
    private var speakingSince = 0L

    /** 最近一次错误（自检用）。 */
    var lastError: String = ""
        private set

    val isOn: Boolean get() = _state.value != State.OFF
    val isPreparing: Boolean get() = _state.value == State.PREPARING
    var bargeInAvailable: Boolean = false
        private set

    private val NO_WORD = Regex("[\\p{P}\\p{S}\\s]")

    fun addListener(l: Listener) {
        listeners.addIfAbsent(l)
    }

    fun removeListener(l: Listener) {
        listeners.remove(l)
    }

    fun setTtsEnabled(on: Boolean) {
        ttsEnabled = on
        if (!on && _state.value == State.SPEAKING) {
            stopSpeaking()
            finishTurn()
        }
    }

    fun setBargeInEnabled(on: Boolean) {
        bargeInEnabled = on
    }

    /** 开始：拷模型（首次）→ 加载 → 开麦。页面负责把前台服务先拉起来。 */
    fun start(ctx: Context) {
        if (_state.value != State.OFF) return
        val app = ctx.applicationContext
        appContext = app
        setState(State.PREPARING)
        Thread({
            try {
                ModelFiles.prepare(app) { pct -> main.post { listeners.forEach { it.onProgress(pct) } } }
                val engine = LocalAsr(
                    app,
                    ModelFiles.paths(app),
                    onSegment = { text -> main.post { onRecognized(text) } },
                    onSpeechStarted = { main.post { onSpeechStarted() } },
                    onError = { msg -> main.post { notice(msg) } },
                )
                engine.load()
                main.post {
                    asr = engine
                    bargeInAvailable = engine.echoCancelled
                    initTts(app)
                    engine.accepting = true
                    engine.start()
                    setState(State.LISTENING)
                    if (!engine.echoCancelled) {
                        notice("这台手机没有硬件回声消除，朗读时不能自动插嘴（可以点麦克风打断）")
                    } else {
                        notice("开始听了，直接说话；说完停一下就会发出去")
                    }
                }
            } catch (e: Throwable) {
                main.post {
                    lastError = e.message ?: e.javaClass.simpleName
                    notice("语音起不来：$lastError")
                    stop()
                }
            }
        }, "voice-start").start()
    }

    fun stop() {
        val engine = asr
        asr = null
        pendingSeg = 0
        replyOpen = false
        try {
            tts?.stop()
            tts?.shutdown()
        } catch (_: Exception) {
        }
        tts = null
        ttsReady = false
        setState(State.OFF)
        if (engine != null) {
            Thread({ engine.release() }, "voice-stop").start()
        }
    }

    // ------------------------------------------------------------ 听
    private fun onRecognized(text: String) {
        if (_state.value != State.LISTENING) return
        val clean = text.trim()
        if (clean.isBlank()) return
        // 防自激：内容里带着刚朗读过的片段 → 是听回了自己
        if (lastSpokenFrag.isNotEmpty() && clean.replace(NO_WORD, "").contains(lastSpokenFrag)) return

        setState(State.THINKING)
        asr?.accepting = false
        listeners.forEach { it.onUtterance(clean) }
    }

    private fun onSpeechStarted() {
        if (_state.value != State.SPEAKING) return
        if (!bargeInEnabled || !bargeInAvailable) return
        // 刚开口不足 250ms 的抖动不算插嘴
        if (System.currentTimeMillis() - speakingSince < 250) return
        stopSpeaking()
        replyOpen = false
        asr?.accepting = true
        setState(State.LISTENING)
        listeners.forEach { it.onNotice("好，你说") }
    }

    // ------------------------------------------------------------ 说
    /**
     * 喂一句回复（页面按标点切好，一句一句来）。TTS 关着时什么也不做，
     * 由 [endReply] 直接收尾回到听。
     */
    fun speakChunk(chunk: String) {
        if (_state.value == State.OFF || _state.value == State.PREPARING) return
        val say = speakable(chunk).trim()
        if (say.isBlank()) return
        if (!ttsEnabled || ttsMissing || !ttsReady) return

        if (_state.value != State.SPEAKING) {
            setState(State.SPEAKING)
            speakingSince = System.currentTimeMillis()
            lastSpokenFrag = ""
            asr?.accepting = false
        }
        replyOpen = true
        if (lastSpokenFrag.isEmpty()) lastSpokenFrag = fragmentOf(say)
        val id = "seg-" + (seq++)
        pendingSeg++
        try {
            tts?.speak(say, TextToSpeech.QUEUE_ADD, null, id)
        } catch (_: Exception) {
            pendingSeg--
        }
    }

    /** 页面回复播完（含出错）调这个。 */
    fun endReply() {
        replyOpen = false
        if (_state.value != State.SPEAKING) {
            finishTurn()
            return
        }
        if (pendingSeg <= 0) finishTurn()
    }

    /** 模型请求失败：别把状态卡在「在想」。 */
    fun onTurnFailed() {
        if (_state.value == State.THINKING) finishTurn()
    }

    /** 用户点「打断」：立刻闭嘴，回到听。 */
    fun interruptSpeaking() {
        if (_state.value != State.SPEAKING) return
        stopSpeaking()
        replyOpen = false
        asr?.accepting = true
        setState(State.LISTENING)
    }

    private fun finishTurn() {
        if (_state.value == State.OFF) return
        pendingSeg = 0
        replyOpen = false
        asr?.accepting = true
        setState(State.LISTENING)
    }

    private fun stopSpeaking() {
        try {
            tts?.stop()
        } catch (_: Exception) {
        }
        pendingSeg = 0
        lastSpokenFrag = ""
    }

    // ------------------------------------------------------------ TTS
    private fun initTts(app: Context) {
        if (tts != null) return
        try {
            tts = TextToSpeech(app) { status -> main.post { onTtsInit(status) } }
        } catch (e: Exception) {
            ttsMissing = true
            notice("朗读起不来（${e.message ?: "未知原因"}），只显示文字")
        }
    }

    private fun onTtsInit(status: Int) {
        val t = tts ?: return
        if (status != TextToSpeech.SUCCESS) {
            ttsMissing = true
            notice("这台手机没有朗读引擎，回复只显示文字")
            return
        }
        val lang = try {
            t.setLanguage(Locale.SIMPLIFIED_CHINESE)
        } catch (_: Exception) {
            TextToSpeech.LANG_NOT_SUPPORTED
        }
        ttsReady = lang != TextToSpeech.LANG_MISSING_DATA && lang != TextToSpeech.LANG_NOT_SUPPORTED
        if (!ttsReady) {
            ttsMissing = true
            notice("这台手机没有中文朗读，回复只显示文字")
        }
        t.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {}

            override fun onDone(utteranceId: String?) {
                main.post {
                    if (pendingSeg > 0) pendingSeg--
                    if (pendingSeg <= 0 && !replyOpen) finishTurn()
                }
            }

            override fun onError(utteranceId: String?) {
                main.post {
                    if (pendingSeg > 0) pendingSeg--
                    if (pendingSeg <= 0 && !replyOpen) finishTurn()
                }
            }

            override fun onStop(utteranceId: String?, interrupted: Boolean) {}
        })
    }

    // ------------------------------------------------------------ 工具
    private fun setState(s: State) {
        if (_state.value == s) return
        _state.value = s
        listeners.forEach { it.onState(s) }
    }

    private fun notice(msg: String) {
        lastError = if (msg.contains("起不来")) msg else lastError
        listeners.forEach { it.onNotice(msg) }
    }

    /** 只念正文：去掉「参考：」行和 Markdown 残留。 */
    private fun speakable(text: String): String {
        val body = text.lines().filterNot { line ->
            val t = line.trimStart()
            t.startsWith("参考：") || t.startsWith("参考:")
        }.joinToString("\n")
        return ChatService.plain(body)
    }

    private fun fragmentOf(text: String): String {
        val clean = text.replace(NO_WORD, "")
        return if (clean.length >= 8) clean.take(12) else ""
    }
}
