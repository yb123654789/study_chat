package com.studybot.app

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/** 模型 key 只存手机本地、系统加密；不进日志、不进同步、不上传电脑（方案 §13）。 */
object Secure {
    private const val FILE = "secure_prefs"
    private const val KEY_API = "api_key"

    @Volatile
    private var cache: SharedPreferences? = null

    private fun prefs(context: Context): SharedPreferences =
        cache ?: synchronized(this) {
            cache ?: run {
                val master = MasterKey.Builder(context)
                    .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                    .build()
                EncryptedSharedPreferences.create(
                    context,
                    FILE,
                    master,
                    EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                    EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
                ).also { cache = it }
            }
        }

    fun apiKey(context: Context): String =
        prefs(context).getString(KEY_API, "").orEmpty()

    fun setApiKey(context: Context, value: String) {
        prefs(context).edit().putString(KEY_API, value.trim()).apply()
    }
}
