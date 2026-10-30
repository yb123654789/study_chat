package com.studybot.app.voice

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.speech.SpeechRecognizer
import androidx.core.content.ContextCompat

/**
 * 语音自检：把「本 App 权限」和「系统识别服务」分开显示。
 *
 * 常见误判：系统设置里给学习bot开了麦克风，但真正录音的是手机自带的识别服务，
 * 那个服务没有权限 / 没有实现，就会报 ERROR_INSUFFICIENT_PERMISSIONS。
 * 这时提示「没给麦克风权限」会让人一直去翻本 App 的开关。
 */
object VoiceSelfCheck {

    fun report(ctx: Context): String {
        val granted = ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        val available = try {
            SpeechRecognizer.isRecognitionAvailable(ctx)
        } catch (_: Exception) {
            false
        }
        val recognizers = runCatching {
            ctx.packageManager
                .queryIntentServices(Intent("android.speech.RecognitionService"), 0)
                .map { "${it.serviceInfo.packageName}/${it.serviceInfo.name}" }
        }.getOrDefault(emptyList())
        val ttsEngines = runCatching {
            ctx.packageManager
                .queryIntentServices(Intent("android.intent.action.TTS_SERVICE"), 0)
                .map { it.serviceInfo.packageName }
                .distinct()
        }.getOrDefault(emptyList())
        // 系统语音输入面板（输入法/语音助手）；内置识别坏了就靠它
        val voiceApps = runCatching {
            ctx.packageManager
                .queryIntentActivities(
                    Intent(android.speech.RecognizerIntent.ACTION_RECOGNIZE_SPEECH), 0
                )
                .map { it.activityInfo.packageName }
                .distinct()
        }.getOrDefault(emptyList())

        return buildString {
            append("本 App 录音权限：").append(if (granted) "已授予" else "未授予").append('\n')
            append("系统语音识别：").append(if (available) "可用" else "不可用").append('\n')
            append("识别服务：")
            append(if (recognizers.isEmpty()) "（没找到）" else recognizers.joinToString("、"))
            append('\n')
            append("朗读引擎：")
            append(if (ttsEngines.isEmpty()) "（没找到）" else ttsEngines.joinToString("、"))
            append('\n')
            append("系统语音面板：")
            append(if (voiceApps.isEmpty()) "（没找到）" else voiceApps.joinToString("、"))
            append('\n')
            append("系统：Android ").append(Build.VERSION.RELEASE)
            append("（SDK ").append(Build.VERSION.SDK_INT).append("）\n")
            append("机型：").append(Build.MANUFACTURER).append(' ').append(Build.MODEL).append('\n')
            append('\n')
            append(
                if (!granted) "→ 本 App 的麦克风没开：去设置 → 应用 → 学习bot → 权限 → 麦克风。"
                else if (!available) "→ 本 App 有权限，但手机没有系统识别服务：用「系统语音输入」或打字。"
                else "→ 两边都在。若仍报没权限，多半是识别服务自己的开关，换「系统语音输入」试试。"
            )
        }
    }

    /** 打开本 App 的系统设置页（权限在里面）。 */
    fun openAppSettings(ctx: Context) {
        runCatching {
            ctx.startActivity(
                Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                    .setData(Uri.fromParts("package", ctx.packageName, null))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }
    }
}
