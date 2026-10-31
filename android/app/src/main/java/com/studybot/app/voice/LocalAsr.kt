package com.studybot.app.voice

import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.os.Handler
import android.os.Looper
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.OfflineModelConfig
import com.k2fsa.sherpa.onnx.OfflineRecognizer
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig
import com.k2fsa.sherpa.onnx.OfflineSenseVoiceModelConfig
import com.k2fsa.sherpa.onnx.SileroVadModelConfig
import com.k2fsa.sherpa.onnx.Vad
import com.k2fsa.sherpa.onnx.VadModelConfig
import java.util.concurrent.Executors
import kotlin.math.max

/**
 * 本地语音引擎：麦克风常开 → Silero VAD 断句 → SenseVoice 识别。
 *
 * 为什么不用系统识别：小米/HyperOS 的识别服务被第三方调用时拒录音
 * （ERROR_INSUFFICIENT_PERMISSIONS），而录音这件事只要我们自己的进程有权限就能做。
 *
 * 线程：
 * - 录音线程：一直 read → 每 512 点喂一次 VAD；VAD 交出整句时丢给解码线程。
 * - 解码线程：单线程串行跑 SenseVoice（一次 0.5–2 秒），不挡录音。
 * - 回调都 post 到主线程。
 *
 * 回声：用 VOICE_COMMUNICATION 音源 + AcousticEchoCanceler（硬件 AEC），
 * 这样朗读时麦克风还能开着听你插嘴。AEC 不可用的机器上 [echoCancelled] 为 false，
 * 上层就别自动打断（否则会被自己的朗读声触发）。
 */
class LocalAsr(
    private val app: Context,
    private val paths: ModelFiles.Paths,
    private val onSegment: (String) -> Unit,
    private val onSpeechStarted: () -> Unit,
    private val onError: (String) -> Unit,
) {

    companion object {
        const val SAMPLE_RATE = 16000
        private const val WINDOW = 512          // Silero VAD 固定窗口（16k）
        private const val READ_SAMPLES = 1600   // 一次读 100ms
    }

    /** 只在「该收音」时为 true；想事/说话时置 false，丢掉的句子不进识别。 */
    @Volatile
    var accepting: Boolean = false

    /** 硬件回声消除是否启用了。false 说明这台机器上自动打断不可靠。 */
    @Volatile
    var echoCancelled: Boolean = false
        private set

    private var recognizer: OfflineRecognizer? = null
    private var vad: Vad? = null
    private var record: AudioRecord? = null
    private var aec: AcousticEchoCanceler? = null
    private var thread: Thread? = null

    @Volatile
    private var running = false

    private val main = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor { r ->
        Thread(r, "voice-decode").apply { isDaemon = true }
    }

    /** 加载模型（228MB mmap，约 1–3 秒）。阻塞，别在主线程调。 */
    fun load() {
        val cfg = OfflineRecognizerConfig(
            featConfig = FeatureConfig(sampleRate = SAMPLE_RATE, featureDim = 80),
            modelConfig = OfflineModelConfig(
                senseVoice = OfflineSenseVoiceModelConfig(
                    model = paths.model,
                    language = "auto",
                    useInverseTextNormalization = true,
                ),
                tokens = paths.tokens,
                numThreads = 2,
                provider = "cpu",
                debug = false,
                modelType = "sensevoice",
            ),
            decodingMethod = "greedy_search",
        )
        recognizer = OfflineRecognizer(null, cfg)
        vad = Vad(
            null,
            VadModelConfig(
                sileroVadModelConfig = SileroVadModelConfig(
                    model = paths.vad,
                    threshold = 0.5f,
                    minSilenceDuration = 0.8f,   // 停 0.8 秒算一句说完
                    minSpeechDuration = 0.25f,
                    windowSize = WINDOW,
                    maxSpeechDuration = 15.0f,   // 一口气说太久就切段
                ),
                sampleRate = SAMPLE_RATE,
                numThreads = 1,
                provider = "cpu",
            ),
        )
    }

    fun start() {
        if (running) return
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        if (minBuf <= 0) {
            onError("这台手机不支持 16k 录音")
            return
        }
        val r = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                max(minBuf, READ_SAMPLES * 2 * 4),
            )
        } catch (_: Exception) {
            null
        }
        if (r == null || r.state != AudioRecord.STATE_INITIALIZED) {
            try {
                r?.release()
            } catch (_: Exception) {
            }
            onError("麦克风起不来（可能被别的应用占用）")
            return
        }
        record = r
        aec = try {
            if (AcousticEchoCanceler.isAvailable()) {
                AcousticEchoCanceler.create(r.audioSessionId)?.also { it.enabled = true }
            } else {
                null
            }
        } catch (_: Exception) {
            null
        }
        echoCancelled = aec?.enabled == true

        running = true
        thread = Thread({ loop(r) }, "voice-mic").apply {
            priority = Thread.MAX_PRIORITY
            start()
        }
    }

    private fun loop(r: AudioRecord) {
        val buf = ShortArray(READ_SAMPLES)
        val frame = FloatArray(WINDOW)
        var filled = 0
        var speechActive = false
        try {
            r.startRecording()
            while (running) {
                val n = r.read(buf, 0, buf.size)
                if (n <= 0) {
                    if (n == AudioRecord.ERROR_DEAD_OBJECT) {
                        main.post { onError("录音设备被系统回收了，请重新开始语音") }
                        break
                    }
                    continue
                }
                for (i in 0 until n) {
                    frame[filled++] = buf[i] / 32768f
                    if (filled < WINDOW) continue
                    filled = 0
                    val v = vad ?: return
                    v.acceptWaveform(frame)
                    val speech = v.isSpeechDetected()
                    if (speech && !speechActive) {
                        speechActive = true
                        main.post { onSpeechStarted() }
                    } else if (!speech) {
                        speechActive = false
                    }
                    while (!v.empty()) {
                        val seg = v.front()
                        v.pop()
                        submit(seg.samples)
                    }
                }
            }
        } catch (e: Exception) {
            if (running) {
                main.post { onError("录音出错：${e.message ?: e.javaClass.simpleName}") }
            }
        } finally {
            try {
                r.stop()
            } catch (_: Exception) {
            }
        }
    }

    private fun submit(samples: FloatArray) {
        if (!accepting) return
        if (samples.size < SAMPLE_RATE / 5) return       // < 0.2 秒，多半是噪音
        val copy = samples.copyOf()
        worker.execute {
            val text = try {
                recognize(copy)
            } catch (_: Exception) {
                ""
            }
            if (text.isNotBlank()) {
                main.post { if (accepting) onSegment(text) }
            }
        }
    }

    private fun recognize(samples: FloatArray): String {
        val rec = recognizer ?: return ""
        val s = rec.createStream()
        return try {
            s.acceptWaveform(samples, SAMPLE_RATE)
            rec.decode(s)
            rec.getResult(s).text.trim()
        } finally {
            s.release()
        }
    }

    fun stop() {
        running = false
        try {
            record?.stop()
        } catch (_: Exception) {
        }
        try {
            thread?.join(800)
        } catch (_: InterruptedException) {
        }
        thread = null
        try {
            record?.release()
        } catch (_: Exception) {
        }
        record = null
        try {
            aec?.enabled = false
            aec?.release()
        } catch (_: Exception) {
        }
        aec = null
    }

    /** 释放模型（stop 之后再调）。 */
    fun release() {
        stop()
        accepting = false
        worker.execute {
            try {
                recognizer?.release()
            } catch (_: Exception) {
            }
            try {
                vad?.release()
            } catch (_: Exception) {
            }
            recognizer = null
            vad = null
        }
        worker.shutdown()
    }
}
