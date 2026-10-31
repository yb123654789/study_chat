package com.studybot.app.voice

import android.content.Context
import java.io.File

/**
 * 语音模型文件：打包在 APK 的 assets 里，第一次用时拷到 filesDir。
 *
 * 为什么不直接从 assets 读：sherpa-onnx 的 native 层要文件路径（能 mmap），
 * 从 assets 读要整份读进内存（228MB），手机上容易吃紧。拷一次以后都走本地文件。
 */
object ModelFiles {

    /** 换模型/换量化版本时改这里，旧文件会被重拷。 */
    private const val VERSION = "sensevoice-int8-2024-07-17-v1"
    private const val DIR = "asr"
    private const val ASSET_DIR = "sensevoice"

    data class Paths(val model: String, val tokens: String, val vad: String)

    fun dir(ctx: Context): File = File(ctx.filesDir, DIR)

    fun paths(ctx: Context): Paths {
        val d = dir(ctx)
        return Paths(
            model = File(d, "model.int8.onnx").absolutePath,
            tokens = File(d, "tokens.txt").absolutePath,
            vad = File(d, "silero_vad.onnx").absolutePath,
        )
    }

    fun ready(ctx: Context): Boolean {
        val d = dir(ctx)
        val marker = File(d, "VERSION")
        if (!marker.isFile || marker.readText().trim() != VERSION) return false
        val p = paths(ctx)
        return File(p.model).isFile && File(p.tokens).isFile && File(p.vad).isFile
    }

    /** 首次使用：从 assets 拷到 filesDir。[onProgress] 给 0..100。 */
    fun prepare(ctx: Context, onProgress: (Int) -> Unit) {
        if (ready(ctx)) {
            onProgress(100)
            return
        }
        val d = dir(ctx)
        d.mkdirs()
        // 先写临时文件再改名：中途被杀不会留下半个模型
        copyAsset(ctx, "$ASSET_DIR/model.int8.onnx", File(d, "model.int8.onnx"), 97, onProgress)
        copyAsset(ctx, "$ASSET_DIR/tokens.txt", File(d, "tokens.txt"), 98, onProgress)
        copyAsset(ctx, "$ASSET_DIR/silero_vad.onnx", File(d, "silero_vad.onnx"), 99, onProgress)
        File(d, "VERSION").writeText(VERSION)
        onProgress(100)
    }

    private fun copyAsset(
        ctx: Context,
        assetPath: String,
        dest: File,
        weight: Int,
        onProgress: (Int) -> Unit,
    ) {
        val tmp = File(dest.parentFile, dest.name + ".tmp")
        val total = runCatching { ctx.assets.openFd(assetPath).use { it.length } }.getOrDefault(0L)
        ctx.assets.open(assetPath).use { input ->
            tmp.outputStream().use { out ->
                val buf = ByteArray(1 shl 20)
                var done = 0L
                var last = -1
                while (true) {
                    val n = input.read(buf)
                    if (n <= 0) break
                    out.write(buf, 0, n)
                    done += n
                    if (total > 0) {
                        val pct = (done * weight / total).toInt()
                        if (pct != last) {
                            last = pct
                            onProgress(pct)
                        }
                    }
                }
            }
        }
        if (dest.exists()) dest.delete()
        if (!tmp.renameTo(dest)) {
            tmp.copyTo(dest, overwrite = true)
            tmp.delete()
        }
    }
}
