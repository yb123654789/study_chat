package com.studybot.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.net.Uri
import android.os.Bundle
import android.provider.MediaStore
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.TextRecognizer
import com.google.mlkit.vision.text.chinese.ChineseTextRecognizerOptions
import com.studybot.app.chat.ChatService
import com.studybot.app.data.Note
import com.studybot.app.data.NoteRepo
import com.studybot.app.databinding.ActivityNoteEditBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/**
 * P5 记笔记：打字 / 语音（SpeechRecognizer，边识别边填）/ 拍照（系统相机 + 中文 OCR）。
 * 语音和 OCR 出字后先进 [FixActivity] 看纠错稿，再回这里保存。
 */
class NoteEditActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_UID = "note_uid"
    }

    private lateinit var binding: ActivityNoteEditBinding

    private var existing: Note? = null
    private var rawText = ""
    private var src = "text"
    private var mediaPath = ""
    private var pendingPhoto: File? = null
    private var pendingPhotoUri: Uri? = null
    private var listening = false

    private var recognizer: SpeechRecognizer? = null
    private var ocr: TextRecognizer? = null

    // ---------------------------------------------------------------- 系统回调
    private val micPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) startVoice() else toast("没给麦克风权限，改用打字吧")
        }

    private val photoLauncher =
        registerForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
            val file = pendingPhoto
            pendingPhoto = null
            revokePhotoUri()
            if (!ok || file == null || !file.exists() || file.length() <= 0L) {
                file?.delete()
                toast("没拍照")
                return@registerForActivityResult
            }
            mediaPath = file.absolutePath
            src = "photo"
            updateSource()
            runOcr(file)
        }

    private val fixLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
            if (res.resultCode != RESULT_OK) return@registerForActivityResult
            val mode = res.data?.getStringExtra(FixActivity.EXTRA_MODE).orEmpty()
            val text = res.data?.getStringExtra(FixActivity.EXTRA_TEXT).orEmpty()
            when (mode) {
                "fixed" -> {
                    binding.input.setText(text)
                    setStatus("已用纠正稿。还能接着改，最后点「保存」。")
                }
                "manual" -> {
                    binding.input.setText(rawText)
                    binding.input.requestFocus()
                    binding.input.setSelection(binding.input.text?.length ?: 0)
                    setStatus("原文已放回输入框，自己改吧。")
                }
                else -> {
                    binding.input.setText(rawText)
                    setStatus("用回原文了。")
                }
            }
            updateSource()
        }

    // ---------------------------------------------------------------- 生命周期
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityNoteEditBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.toolbar.title = "新笔记"

        binding.btnType.setOnClickListener { toTyping() }
        binding.btnVoice.setOnClickListener { onVoiceClick() }
        binding.btnPhoto.setOnClickListener { onPhotoClick() }
        binding.btnSave.setOnClickListener { save(rawOnly = false) }
        binding.btnShowRaw.setOnClickListener { showRaw() }
        binding.btnRevert.setOnClickListener { revert() }

        setStatus("打字直接写；语音/拍照出字后会先给你看纠错稿，可改用原文。")
        val uid = intent.getStringExtra(EXTRA_UID).orEmpty()
        if (uid.isNotBlank()) load(uid)
    }

    override fun onDestroy() {
        super.onDestroy()
        try {
            recognizer?.destroy()
        } catch (_: Exception) {
        }
        recognizer = null
        try {
            ocr?.close()
        } catch (_: Exception) {
        }
        ocr = null
    }

    // ---------------------------------------------------------------- 读已有笔记
    private fun load(uid: String) {
        lifecycleScope.launch {
            val n = withContext(Dispatchers.IO) { NoteRepo.get(this@NoteEditActivity, uid) }
            if (n == null) {
                toast("这条笔记不见了")
                finish()
                return@launch
            }
            existing = n
            rawText = n.rawText
            src = n.src
            mediaPath = n.mediaPath
            binding.input.setText(n.text)
            when (n.tag) {
                "teacher" -> binding.tagTeacher.isChecked = true
                "slide" -> binding.tagSlide.isChecked = true
                else -> binding.tagSelf.isChecked = true
            }
            binding.toolbar.title = "编辑笔记"
            updateSource()
            setStatus(
                if (hasRaw()) "这条有识别原文，点「看原文」能对照。"
                else "直接改，保存后就更新。"
            )
        }
    }

    // ---------------------------------------------------------------- 打字
    private fun toTyping() {
        src = "text"
        rawText = ""
        updateSource()
        binding.input.requestFocus()
        setStatus("按打字记：保存时输入框里的字就是正文（原来识别的原文不再保留）。")
    }

    // ---------------------------------------------------------------- 语音
    private fun onVoiceClick() {
        if (listening) {
            stopVoice()
            setStatus("停下了。还想说就再点一次「语音」。")
            return
        }
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            toast("这台手机不带语音识别，改用打字吧")
            return
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            micPermission.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        startVoice()
    }

    private fun startVoice() {
        val r = recognizer ?: try {
            SpeechRecognizer.createSpeechRecognizer(this).also {
                it.setRecognitionListener(listener)
                recognizer = it
            }
        } catch (e: Exception) {
            toast("语音识别起不来（${e.message ?: "未知原因"}），改用打字吧")
            return
        }
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM
            )
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
            // 边识别边往输入框塞，不用等整段说完
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
        }
        listening = true
        binding.btnVoice.text = "停止"
        setStatus("在听了，说完停一下…")
        try {
            r.startListening(intent)
        } catch (e: Exception) {
            listening = false
            binding.btnVoice.text = "语音"
            toast("语音识别起不来（${e.message ?: "未知原因"}），改用打字吧")
        }
    }

    private fun stopVoice() {
        listening = false
        binding.btnVoice.text = "语音"
        try {
            recognizer?.stopListening()
        } catch (_: Exception) {
        }
    }

    private val listener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {
            setStatus("在听了，说完停一下…")
        }

        override fun onBeginningOfSpeech() {}

        override fun onRmsChanged(rmsdB: Float) {}

        override fun onBufferReceived(buffer: ByteArray?) {}

        override fun onEndOfSpeech() {
            setStatus("识别中…")
        }

        override fun onError(error: Int) {
            listening = false
            binding.btnVoice.text = "语音"
            val msg = when (error) {
                SpeechRecognizer.ERROR_AUDIO -> "录音出错"
                SpeechRecognizer.ERROR_CLIENT -> "识别器被中断"
                SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "没给麦克风权限"
                SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT ->
                    "网络没连上（语音识别要联网）"
                SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT ->
                    "没听清"
                SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "识别器忙，等一下再点"
                SpeechRecognizer.ERROR_SERVER -> "识别服务出错"
                else -> "识别失败（$error）"
            }
            setStatus("语音：$msg。改用打字吧。")
            toast("$msg，改用打字吧")
        }

        override fun onPartialResults(partialResults: Bundle?) {
            val best = partialResults
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
                .orEmpty()
            if (best.isNotBlank()) {
                binding.input.setText(best)
                if (src != "voice") {
                    src = "voice"
                    updateSource()
                }
            }
        }

        override fun onResults(results: Bundle?) {
            listening = false
            binding.btnVoice.text = "语音"
            val best = results
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
                .orEmpty()
                .trim()
            if (best.isBlank()) {
                setStatus("没听清，再说一次或改用打字。")
                return
            }
            src = "voice"
            rawText = best
            binding.input.setText(best)
            updateSource()
            openFix(best)
        }

        override fun onEvent(eventType: Int, params: Bundle?) {}
    }

    // ---------------------------------------------------------------- 拍照 + OCR
    private fun onPhotoClick() {
        val dir = File(filesDir, "notes")
        if (!dir.exists() && !dir.mkdirs()) {
            toast("存照片的目录建不出来")
            return
        }
        val file = File(dir, "note_${System.currentTimeMillis()}.jpg")
        if (!file.exists()) {
            try {
                file.createNewFile()
            } catch (_: Exception) {
            }
        }
        val uri = try {
            FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
        } catch (e: Exception) {
            toast("照片路径配错了：${e.message ?: e.javaClass.simpleName}")
            return
        }
        pendingPhoto = file
        pendingPhotoUri = uri
        grantCameraWrite(uri)
        try {
            photoLauncher.launch(uri)
        } catch (e: Exception) {
            pendingPhoto = null
            revokePhotoUri()
            toast("这台手机打不开相机，试试语音或打字")
        }
    }

    /**
     * TakePicture 契约不带 FLAG_GRANT_WRITE_URI_PERMISSION，个别机型的相机会写不进来。
     * 照官方相机示例的做法：启动前把读写权限授给所有能拍照的应用，回来再撤销。
     */
    private fun grantCameraWrite(uri: Uri) {
        val flags = Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION
        val apps = try {
            packageManager.queryIntentActivities(
                Intent(MediaStore.ACTION_IMAGE_CAPTURE),
                PackageManager.MATCH_DEFAULT_ONLY
            )
        } catch (_: Exception) {
            emptyList()
        }
        for (app in apps) {
            try {
                grantUriPermission(app.activityInfo.packageName, uri, flags)
            } catch (_: Exception) {
            }
        }
    }

    private fun revokePhotoUri() {
        val uri = pendingPhotoUri ?: return
        pendingPhotoUri = null
        try {
            revokeUriPermission(
                uri,
                Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION
            )
        } catch (_: Exception) {
        }
    }

    private fun runOcr(file: File) {
        setStatus("正在认图里的字…")
        binding.btnPhoto.isEnabled = false
        lifecycleScope.launch {
            val bitmap = withContext(Dispatchers.IO) { loadBitmap(file) }
            if (bitmap == null) {
                binding.btnPhoto.isEnabled = true
                setStatus("图片读不出来，可以自己打。")
                toast("图片读不出来")
                return@launch
            }
            val client = ocr ?: try {
                TextRecognition.getClient(ChineseTextRecognizerOptions.Builder().build())
                    .also { ocr = it }
            } catch (e: Exception) {
                binding.btnPhoto.isEnabled = true
                setStatus("OCR 起不来：${e.message ?: "未知原因"}，可以自己打。")
                return@launch
            }
            client.process(InputImage.fromBitmap(bitmap, 0))
                .addOnSuccessListener { result ->
                    if (isFinishing || isDestroyed) return@addOnSuccessListener
                    binding.btnPhoto.isEnabled = true
                    val text = result.textBlocks.joinToString("\n") { it.text }.trim()
                    if (text.isBlank()) {
                        setStatus("图里没认出字（可能太糊），可以自己打。")
                        toast("图里没认出字")
                    } else {
                        src = "photo"
                        rawText = text
                        binding.input.setText(text)
                        updateSource()
                        openFix(text)
                    }
                }
                .addOnFailureListener { e ->
                    if (isFinishing || isDestroyed) return@addOnFailureListener
                    binding.btnPhoto.isEnabled = true
                    setStatus("OCR 失败：${e.message ?: "未知原因"}，可以自己打。")
                }
        }
    }

    /** 大图先降采样再认，顺便按 EXIF 摆正（相机常见 90°）。 */
    private fun loadBitmap(file: File): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.absolutePath, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 2400) sample *= 2
        val raw = BitmapFactory.decodeFile(
            file.absolutePath,
            BitmapFactory.Options().apply { inSampleSize = sample }
        ) ?: return null
        val degrees = try {
            when (
                ExifInterface(file.absolutePath)
                    .getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
            ) {
                ExifInterface.ORIENTATION_ROTATE_90 -> 90
                ExifInterface.ORIENTATION_ROTATE_180 -> 180
                ExifInterface.ORIENTATION_ROTATE_270 -> 270
                else -> 0
            }
        } catch (_: Exception) {
            0
        }
        if (degrees == 0) return raw
        val matrix = Matrix().apply { postRotate(degrees.toFloat()) }
        return Bitmap.createBitmap(raw, 0, 0, raw.width, raw.height, matrix, true)
    }

    // ---------------------------------------------------------------- 纠错预览
    private fun openFix(text: String) {
        val i = Intent(this, FixActivity::class.java)
            .putExtra(FixActivity.EXTRA_RAW, text)
            .putExtra(FixActivity.EXTRA_COURSE, existing?.course.orEmpty())
            .putExtra(FixActivity.EXTRA_GOAL, existing?.goalId.orEmpty())
        fixLauncher.launch(i)
    }

    // ---------------------------------------------------------------- 原文 / 还原
    /** 只有语音/拍照记的才有「识别原文」；打字记的 rawText 就等于正文。 */
    private fun hasRaw(): Boolean = src != "text" && rawText.isNotBlank()

    private fun showRaw() {
        if (!hasRaw()) {
            toast("这条没有识别原文（打字记的）")
            return
        }
        binding.input.setText(rawText.trim())
        setStatus("已显示识别原文。想存成原文就点「还原」。")
    }

    private fun revert() {
        if (!hasRaw()) {
            toast("这条没有识别原文，没什么可还原的")
            return
        }
        binding.input.setText(rawText.trim())
        save(rawOnly = true)
    }

    // ---------------------------------------------------------------- 保存
    private fun save(rawOnly: Boolean) {
        if (rawOnly && !hasRaw()) {
            toast("这条没有识别原文，没什么可还原的")
            return
        }
        val typed = binding.input.text?.toString().orEmpty().trim()
        val text = if (rawOnly) rawText.trim() else typed
        if (text.isBlank()) {
            toast("先写点内容")
            return
        }
        val tag = when (binding.tagGroup.checkedRadioButtonId) {
            R.id.tagTeacher -> "teacher"
            R.id.tagSlide -> "slide"
            else -> "self"
        }
        val now = ChatService.nowStr()
        val old = existing
        val note = Note(
            uid = old?.uid ?: ChatService.newLocalId().toString(),
            id = old?.id,
            course = old?.course.orEmpty(),
            goalId = old?.goalId.orEmpty(),
            taskId = old?.taskId,
            text = text,
            rawText = when {
                src == "text" -> text
                rawOnly -> rawText.trim()
                rawText.isNotBlank() -> rawText.trim()
                else -> text
            },
            src = src.ifBlank { "text" },
            mediaPath = mediaPath,
            createdAt = old?.createdAt?.ifBlank { now } ?: now,
            tag = tag,
            trust = old?.trust ?: 0,
            pending = true,
        )
        binding.btnSave.isEnabled = false
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { NoteRepo.save(this@NoteEditActivity, note) }
                toast(if (rawOnly) "已还原成原文" else "已保存")
                finish()
            } catch (e: Exception) {
                binding.btnSave.isEnabled = true
                toast("保存失败：${e.message ?: e.javaClass.simpleName}")
            }
        }
    }

    // ---------------------------------------------------------------- 小工具
    private fun updateSource() {
        val label = when (src) {
            "voice" -> "语音"
            "photo" -> "拍照"
            else -> "打字"
        }
        val bits = ArrayList<String>()
        bits.add("来源：$label")
        if (mediaPath.isNotBlank()) bits.add("照片已存本地")
        if (src != "text" && rawText.isNotBlank()) bits.add("有识别原文")
        binding.sourceHint.text = bits.joinToString(" · ")
    }

    private fun setStatus(text: String) {
        binding.statusText.text = text
    }

    private fun toast(text: String) {
        Toast.makeText(this, text, Toast.LENGTH_SHORT).show()
    }
}
