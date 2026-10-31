package com.studybot.app.voice

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.ContextCompat
import java.io.File

/**
 * 语音自检：把「本 App 能录」「本地模型在不在」「能不能打断」分开显示。
 *
 * 背景：以前语音走系统识别服务，小米/HyperOS 上那个服务被第三方调用时拒录音
 * （ERROR_INSUFFICIENT_PERMISSIONS），本 App 权限没问题也没用。
 * 现在改成自己录音 + 本地 SenseVoice，所以自检也要跟着改。
 */
object VoiceSelfCheck {

    fun report(ctx: Context): String {
        val granted = ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        val modelReady = ModelFiles.ready(ctx)
        val modelFile = File(ModelFiles.paths(ctx).model)
        val state = VoiceCore.state.value

        return buildString {
            append("本 App 录音权限：").append(if (granted) "已授予" else "未授予").append('\n')
            append("语音状态：").append(
                when (state) {
                    VoiceCore.State.OFF -> "关着"
                    VoiceCore.State.PREPARING -> "正在准备"
                    VoiceCore.State.LISTENING -> "正在听"
                    VoiceCore.State.THINKING -> "在想"
                    VoiceCore.State.SPEAKING -> "正在说"
                }
            ).append('\n')
            append("本地模型：")
            if (modelReady) {
                append("已就绪（").append("%.1f".format(modelFile.length() / 1048576.0)).append(" MB）")
            } else {
                append("还没准备好（第一次点「开始」会自动拷，约 20 秒）")
            }
            append('\n')
            append("回声消除：")
            append(if (VoiceCore.bargeInAvailable) "可用（能插嘴打断）" else "不可用（只能点「打断」）")
            append('\n')
            append("识别方式：本地 SenseVoice（离线，不联网）\n")
            if (VoiceCore.lastError.isNotBlank()) {
                append("上次错误：").append(VoiceCore.lastError).append('\n')
            }
            append("系统：Android ").append(Build.VERSION.RELEASE)
            append("（SDK ").append(Build.VERSION.SDK_INT).append("）\n")
            append("机型：").append(Build.MANUFACTURER).append(' ').append(Build.MODEL).append('\n')
            append('\n')
            append(
                if (!granted) "→ 本 App 的麦克风没开：去设置 → 应用 → 学习bot → 权限 → 麦克风。"
                else if (!modelReady) "→ 点一次「开始」，让它把模型拷好；之后完全离线。"
                else "→ 都正常。点「开始」之后一直说话就行，说完停一下就发。"
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
