package com.studybot.app.voice

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.widget.Toast
import com.studybot.app.chat.ChatService
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.Locale

/**
 * P6 语音聊天：TTS（朗读回复）+ ASR（听用户说）。
 *
 * 状态机：IDLE → LISTENING → THINKING → SPEAKING →（连续模式）LISTENING …
 *
 * 防自激（自己的朗读被麦克风听回去）：
 * 1. TTS onDone 后等 [RESUME_DELAY_MS] 再开麦，避开声音尾巴；
 * 2. 连续模式下，本轮最终文字如果把上一句朗读的前 12 字（去标点、长度≥8 才取）
 *    包含进去，就判定是听回了自己的声音：丢弃、不发送，重听。
 * 3. 朗读中点麦克风 = 立刻闭嘴（[onMicTap]），连续模式回到 LISTENING，手动模式回 IDLE。
 *
 * 系统不支持连续识别（没有识别服务 / 连续模式下 ERROR_CLIENT、ERROR_RECOGNIZER_BUSY 反复）
 * 时自动切 [Mode.MANUAL] 并只 Toast 一次。
 *
 * 线程：所有方法都在主线程调用；SpeechRecognizer 回调也在主线程；TTS 回调切回主线程。
 */
class VoiceChat(
    context: Context,
    private val cb: Callbacks,
    initialMode: Mode = Mode.CONTINUOUS,
    ttsInitiallyOn: Boolean = true,
) {

    interface Callbacks {
        /** 边听边出的文字（只在 LISTENING 时有）。 */
        fun onPartial(text: String)

        /** 一句说完的最终文字。连续模式由页面直接发送；手动模式只填输入框。 */
        fun onFinal(text: String)

        /** 模式被自动/手动切换。reason 为空表示用户自己切的。 */
        fun onModeChanged(mode: Mode, reason: String)

        /** 给用户看的提示（Toast 文案），页面可以转成 Toast / 状态行。 */
        fun onNotice(text: String) {}
    }

    enum class Mode { CONTINUOUS, MANUAL }

    enum class State { IDLE, LISTENING, THINKING, SPEAKING }

    private companion object {
        const val UTT_ID = "reply"
        const val RESUME_DELAY_MS = 300L // TTS 收尾后等 300ms 再开麦
        const val RETRY_DELAY_MS = 500L
        const val SWITCH_AFTER_FAULTS = 2
        const val MAX_TTS_CHARS = 3500
        const val MIN_FRAGMENT = 8
        const val FRAGMENT_TAKE = 12

        // 去标点 / 符号 / 空白，只留字和数字，拿来比对「是不是自己说的」
        val NO_WORD = Regex("[\\p{P}\\p{S}\\s]")
    }

    private val app: Context = context.applicationContext
    private val handler = Handler(Looper.getMainLooper())
    private val _state = MutableStateFlow(State.IDLE)
    val state: StateFlow<State> = _state.asStateFlow()

    var mode: Mode = initialMode
        private set

    var ttsEnabled: Boolean = ttsInitiallyOn
        private set

    /** 连续模式的一轮是否还在连着（用户点一下停止就变 false）。 */
    var handsFree: Boolean = false
        private set

    val isListening: Boolean get() = _state.value == State.LISTENING

    fun isSpeaking(): Boolean = tts?.isSpeaking == true || _state.value == State.SPEAKING

    // ------------------------------------------------------------ TTS
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private var ttsMissing = false
    private var lastSpokenFragment = ""

    // ------------------------------------------------------------ ASR
    private var recognizer: SpeechRecognizer? = null
    private var aborted = false
    private var released = false
    private var paused = false
    private var pending: Runnable? = null
    private var continuousFaults = 0
    private var autoManualNotified = false

    private val progress = object : UtteranceProgressListener() {
        override fun onStart(utteranceId: String?) {}

        override fun onDone(utteranceId: String?) {
            handler.post {
                if (released || utteranceId != UTT_ID) return@post
                if (_state.value != State.SPEAKING) return@post
                // 防自激第 1 步：等声音尾巴过去再开麦
                endTurn()
            }
        }

        override fun onError(utteranceId: String?) {
            handler.post {
                if (released || utteranceId != UTT_ID) return@post
                if (_state.value != State.SPEAKING) return@post
                lastSpokenFragment = ""
                endTurn()
            }
        }

        override fun onStop(utteranceId: String?, interrupted: Boolean) {
            // 用户打断：状态已经由 stopSpeaking() / onMicTap() 改好，这里不再动
        }
    }

    init {
        startTts()
        if (!SpeechRecognizer.isRecognitionAvailable(app)) {
            switchToManual("这台手机没装语音识别，已改成手动")
        }
    }

    private fun startTts() {
        try {
            tts = TextToSpeech(app) { status -> onTtsInit(status) }
        } catch (e: Exception) {
            ttsMissing = true
            tts = null
            cb.onNotice("朗读起不来（${e.message ?: "未知原因"}），回复只显示文字")
        }
    }

    private fun onTtsInit(status: Int) {
        if (released) return
        if (status != TextToSpeech.SUCCESS) {
            ttsMissing = true
            cb.onNotice("朗读起不来（系统 TTS 不可用），先只用来听")
            return
        }
        val lang = try {
            tts?.setLanguage(Locale.SIMPLIFIED_CHINESE)
        } catch (_: Exception) {
            TextToSpeech.LANG_NOT_SUPPORTED
        }
        if (lang == TextToSpeech.LANG_MISSING_DATA || lang == TextToSpeech.LANG_NOT_SUPPORTED) {
            ttsMissing = true
            ttsReady = false
            cb.onNotice("这台手机没有中文朗读，回复只显示文字")
        } else {
            ttsReady = true
        }
        tts?.setOnUtteranceProgressListener(progress)
    }

    /**
     * 朗读一句回复。TTS 关着 / 没有中文引擎 / 文本为空时直接收尾
     * （连续免手模式下会接着开麦，手动模式下回到 IDLE）。
     */
    fun onReply(text: String) {
        if (released || paused) return
        val say = speakable(text)
        if (!ttsEnabled || ttsMissing || !ttsReady || say.isBlank()) {
            endTurn()
            return
        }
        lastSpokenFragment = fragmentOf(say)
        _state.value = State.SPEAKING
        val ok = try {
            tts?.speak(say.take(MAX_TTS_CHARS), TextToSpeech.QUEUE_FLUSH, null, UTT_ID)
        } catch (_: Exception) {
            TextToSpeech.ERROR
        }
        if (ok != TextToSpeech.SUCCESS) {
            lastSpokenFragment = ""
            endTurn()
        }
    }

    /** 回复失败 / 会话没就绪：按当前模式收尾，别把状态卡在「在想」。 */
    fun onTurnFailed() {
        if (released) return
        endTurn()
    }

    /** 进入「在想」：请求发出去了，等模型回复。会先掐掉朗读和还在听的麦克风。 */
    fun onThinking() {
        if (released) return
        if (_state.value == State.SPEAKING) {
            try {
                tts?.stop()
            } catch (_: Exception) {
            }
            lastSpokenFragment = ""
        }
        cancelPending()
        abortListening()
        _state.value = State.THINKING
    }

    private fun endTurn() {
        cancelPending()
        if (mode == Mode.CONTINUOUS && handsFree) {
            schedule(RESUME_DELAY_MS) { startListening() }
        } else {
            _state.value = State.IDLE
        }
    }

    /** 朗读立刻停下（关掉 TTS 开关、退出页面时用）；不自动接着听。 */
    fun stopSpeaking() {
        cancelPending()
        try {
            tts?.stop()
        } catch (_: Exception) {
        }
        lastSpokenFragment = ""
        if (_state.value == State.SPEAKING) _state.value = State.IDLE
    }

    // ------------------------------------------------------------ 麦克风
    /** 麦克风按钮：按当前状态决定开始听 / 停止 / 打断朗读。 */
    fun onMicTap() {
        if (released) return
        when (_state.value) {
            State.LISTENING -> {
                // 再点一下：停止这一轮，也停掉连续
                handsFree = false
                abortListening()
                cancelPending()
                _state.value = State.IDLE
            }
            State.SPEAKING -> {
                // 朗读中点一下：立刻闭嘴
                try {
                    tts?.stop()
                } catch (_: Exception) {
                }
                lastSpokenFragment = ""
                cancelPending()
                if (mode == Mode.CONTINUOUS) {
                    handsFree = true
                    startListening()
                } else {
                    _state.value = State.IDLE
                }
            }
            State.THINKING -> cb.onNotice("这轮还在想，等它说完再点")
            State.IDLE -> {
                handsFree = mode == Mode.CONTINUOUS
                // 用户主动开麦：上一句朗读的片段作废，别误丢这一轮
                lastSpokenFragment = ""
                startListening()
            }
        }
    }

    fun startListening() {
        if (released) return
        cancelPending()
        if (_state.value == State.THINKING) return
        if (tts?.isSpeaking == true) stopSpeaking()
        if (!SpeechRecognizer.isRecognitionAvailable(app)) {
            switchToManual("这台手机没装语音识别，已改成手动")
            return
        }
        val r = ensureRecognizer()
        if (r == null) {
            _state.value = State.IDLE
            cb.onNotice("语音识别起不来，改用打字吧")
            return
        }
        aborted = false
        _state.value = State.LISTENING
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
        }
        try {
            r.startListening(intent)
        } catch (e: Exception) {
            aborted = true
            releaseRecognizer()
            _state.value = State.IDLE
            handsFree = false
            cb.onNotice("语音识别起不来（${e.message ?: "未知原因"}），改用打字吧")
        }
    }

    /** 停止听（页面退出 / 切到后台用）。 */
    fun stopListening() {
        handsFree = false
        cancelPending()
        abortListening()
        if (_state.value == State.LISTENING) _state.value = State.IDLE
    }

    private fun abortListening() {
        aborted = true
        try {
            recognizer?.cancel()
        } catch (_: Exception) {
        }
    }

    private fun ensureRecognizer(): SpeechRecognizer? {
        recognizer?.let { return it }
        return try {
            SpeechRecognizer.createSpeechRecognizer(app).also {
                it.setRecognitionListener(recognitionListener)
                recognizer = it
            }
        } catch (_: Exception) {
            recognizer = null
            null
        }
    }

    private fun releaseRecognizer() {
        val r = recognizer ?: return
        recognizer = null
        try {
            r.cancel()
        } catch (_: Exception) {
        }
        try {
            r.destroy()
        } catch (_: Exception) {
        }
    }

    private fun rebuildRecognizer() {
        // 先标记作废，免得 destroy 时迟到的 ERROR_CLIENT 被当成新错误再数一次
        aborted = true
        releaseRecognizer()
    }

    // ------------------------------------------------------------ 识别回调
    private val recognitionListener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {}

        override fun onBeginningOfSpeech() {
            // 极少见：开麦时还在朗读（正常流程朗读时不开麦）。真碰上就闭嘴，别自我循环。
            if (_state.value == State.SPEAKING) {
                try {
                    tts?.stop()
                } catch (_: Exception) {
                }
                lastSpokenFragment = ""
                _state.value = State.LISTENING
            }
        }

        override fun onRmsChanged(rmsdB: Float) {}

        override fun onBufferReceived(buffer: ByteArray?) {}

        override fun onEndOfSpeech() {}

        override fun onPartialResults(partialResults: Bundle?) {
            if (aborted || released) return
            val text = best(partialResults)
            if (text.isNotBlank() && _state.value == State.LISTENING) cb.onPartial(text)
        }

        override fun onResults(results: Bundle?) {
            if (aborted || released) return
            handleFinal(best(results))
        }

        override fun onError(error: Int) {
            if (aborted || released) return
            handleError(error)
        }

        override fun onEvent(eventType: Int, params: Bundle?) {}
    }

    private fun best(bundle: Bundle?): String = bundle
        ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
        ?.firstOrNull()
        ?.trim()
        .orEmpty()

    private fun handleFinal(text: String) {
        val frag = lastSpokenFragment
        lastSpokenFragment = ""

        if (text.isBlank()) {
            if (mode == Mode.CONTINUOUS && handsFree) retryListening(RETRY_DELAY_MS)
            else _state.value = State.IDLE
            return
        }
        // 真的听出一句，之前的连续故障才算过去
        continuousFaults = 0

        if (mode == Mode.CONTINUOUS) {
            // 防自激第 2 步：这句里有自己刚朗读过的片段 → 丢掉、重听
            if (frag.isNotEmpty() && text.replace(NO_WORD, "").contains(frag)) {
                _state.value = State.LISTENING
                schedule(RETRY_DELAY_MS) { startListening() }
                return
            }
            _state.value = State.THINKING
            cb.onFinal(text)
        } else {
            handsFree = false
            _state.value = State.IDLE
            cb.onFinal(text)
        }
    }

    private fun handleError(error: Int) {
        if (mode == Mode.MANUAL) {
            handsFree = false
            _state.value = State.IDLE
            if (error == SpeechRecognizer.ERROR_CLIENT || error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY) {
                rebuildRecognizer()
            }
            cb.onNotice(errorText(error))
            return
        }

        when (error) {
            SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> {
                // 没人说话 / 没听清：连续模式下继续听
                if (handsFree) retryListening(RETRY_DELAY_MS) else _state.value = State.IDLE
            }
            SpeechRecognizer.ERROR_CLIENT, SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> {
                continuousFaults++
                rebuildRecognizer()
                if (continuousFaults >= SWITCH_AFTER_FAULTS) {
                    switchToManual("这台手机的语音识别撑不住连续听，已改成手动")
                } else {
                    retryListening(RETRY_DELAY_MS * 2)
                }
            }
            SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> {
                handsFree = false
                _state.value = State.IDLE
                cb.onNotice("没给麦克风权限，改用打字吧")
            }
            SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT,
            SpeechRecognizer.ERROR_SERVER -> {
                handsFree = false
                _state.value = State.IDLE
                cb.onNotice(errorText(error))
            }
            else -> {
                if (handsFree) retryListening(RETRY_DELAY_MS) else _state.value = State.IDLE
                cb.onNotice(errorText(error))
            }
        }
    }

    private fun retryListening(delay: Long) {
        if (released) return
        if (!handsFree) {
            _state.value = State.IDLE
            return
        }
        _state.value = State.LISTENING
        schedule(delay) { startListening() }
    }

    private fun switchToManual(reason: String) {
        handsFree = false
        mode = Mode.MANUAL
        _state.value = State.IDLE
        cancelPending()
        abortListening()
        cb.onModeChanged(Mode.MANUAL, reason)
        if (!autoManualNotified) {
            autoManualNotified = true
            Toast.makeText(app, reason, Toast.LENGTH_LONG).show()
        }
    }

    // ------------------------------------------------------------ 模式 / 开关
    fun setMode(next: Mode) {
        if (mode == next) return
        mode = next
        if (next == Mode.MANUAL) {
            // 切手动：别再自动开麦，当前这句听完就填输入框
            handsFree = false
            cancelPending()
        } else {
            handsFree = _state.value == State.LISTENING
        }
        cb.onModeChanged(next, "")
    }

    fun setTtsEnabled(on: Boolean) {
        ttsEnabled = on
        if (!on) stopSpeaking()
    }

    // ------------------------------------------------------------ 生命周期
    /** onPause：停止听、停止朗读，拆掉识别器（下一次点麦克风会重建）。 */
    fun pause() {
        paused = true
        handsFree = false
        cancelPending()
        abortListening()
        try {
            tts?.stop()
        } catch (_: Exception) {
        }
        lastSpokenFragment = ""
        if (_state.value != State.IDLE) _state.value = State.IDLE
        releaseRecognizer()
    }

    /** onResume：回到页面，允许朗读和开麦。 */
    fun resume() {
        paused = false
    }

    /** onDestroy：全部释放，避免泄漏。 */
    fun shutdown() {
        released = true
        handsFree = false
        cancelPending()
        abortListening()
        releaseRecognizer()
        try {
            tts?.stop()
        } catch (_: Exception) {
        }
        try {
            tts?.shutdown()
        } catch (_: Exception) {
        }
        tts = null
        ttsReady = false
        _state.value = State.IDLE
    }

    // ------------------------------------------------------------ 小工具
    private fun schedule(delay: Long, block: () -> Unit) {
        cancelPending()
        val r = Runnable {
            pending = null
            if (!released) block()
        }
        pending = r
        handler.postDelayed(r, delay)
    }

    private fun cancelPending() {
        pending?.let { handler.removeCallbacks(it) }
        pending = null
    }

    /** 只念正文：去掉「参考：」引用行和 Markdown 残留（与电脑端 plain 一致）。 */
    private fun speakable(text: String): String {
        val body = text.lines().filterNot { line ->
            val t = line.trimStart()
            t.startsWith("参考：") || t.startsWith("参考:")
        }.joinToString("\n")
        return ChatService.plain(body).trim()
    }

    /** 上一句去标点后长度≥8 才取，取前 12 字。 */
    private fun fragmentOf(text: String): String {
        val clean = text.replace(NO_WORD, "")
        return if (clean.length >= MIN_FRAGMENT) clean.take(FRAGMENT_TAKE) else ""
    }

    private fun errorText(error: Int): String = when (error) {
        SpeechRecognizer.ERROR_AUDIO -> "录音出错"
        SpeechRecognizer.ERROR_CLIENT -> "识别器被中断"
        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "没给麦克风权限"
        SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT ->
            "网络没连上（语音识别要联网）"
        SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "没听清"
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "识别器忙，等一下再点"
        SpeechRecognizer.ERROR_SERVER -> "识别服务出错"
        else -> "识别失败（$error）"
    }
}
