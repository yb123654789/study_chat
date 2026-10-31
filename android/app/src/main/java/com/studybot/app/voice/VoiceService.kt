package com.studybot.app.voice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.studybot.app.ChatActivity
import com.studybot.app.R

/**
 * 语音会话的前台服务。
 *
 * 只在做两件事：① 让进程在锁屏/后台时活着（Android 12+ 后台不让开麦，只有前台服务可以）；
 * ② 通知栏一直显示当前在干什么，顺便给一个「停止」。
 *
 * 服务和页面共享同一个 [VoiceCore]（同进程单例），页面退出服务还在，
 * 再进来接上就行。
 */
class VoiceService : Service() {

    companion object {
        private const val CHANNEL = "voice_session"
        private const val NOTIF_ID = 41
        private const val ACTION_STOP = "com.studybot.app.voice.STOP"

        fun start(ctx: Context) {
            val i = Intent(ctx, VoiceService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                ctx.startForegroundService(i)
            } else {
                ctx.startService(i)
            }
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, VoiceService::class.java))
        }
    }

    private var lastText = "正在准备…"

    private val listener = object : VoiceCore.Listener {
        override fun onState(state: VoiceCore.State) {
            update(textOf(state))
        }

        override fun onProgress(pct: Int) {
            update("准备语音模型 $pct%")
        }

        override fun onNotice(text: String) {
            update(text)
        }
    }

    override fun onCreate() {
        super.onCreate()
        createChannel()
        VoiceCore.addListener(listener)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            VoiceCore.stop()
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(NOTIF_ID, build(lastText))
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        VoiceCore.removeListener(listener)
        VoiceCore.stop()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun update(text: String) {
        lastText = text
        val mgr = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        runCatching { mgr.notify(NOTIF_ID, build(text)) }
    }

    private fun textOf(state: VoiceCore.State): String = when (state) {
        VoiceCore.State.OFF -> "语音已关"
        VoiceCore.State.PREPARING -> "正在准备…"
        VoiceCore.State.LISTENING -> "正在听，直接说"
        VoiceCore.State.THINKING -> "在想…"
        VoiceCore.State.SPEAKING -> "正在说…（可以直接插嘴）"
    }

    private fun build(text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, ChatActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, VoiceService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle("学习bot 语音")
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(open)
            .addAction(0, "停止", stop)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val mgr = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        val ch = NotificationChannel(CHANNEL, "语音对话", NotificationManager.IMPORTANCE_LOW).apply {
            description = "语音对话进行中的提示"
            setShowBadge(false)
        }
        mgr.createNotificationChannel(ch)
    }
}
