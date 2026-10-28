package com.studybot.app.net

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

class ApiException(message: String) : Exception(message)

/** 服务器就一个接口要读：下行快照。上行事件到 P3 再加。 */
object Api {

    fun snapshot(
        baseUrl: String,
        token: String,
        rev: String?,
        blocksRev: String?,
        timeoutMs: Long,
    ): JSONObject {
        val base = normalize(baseUrl)
        val client = OkHttpClient.Builder()
            .callTimeout(timeoutMs, TimeUnit.MILLISECONDS)
            .connectTimeout(minOf(timeoutMs, 4_000L), TimeUnit.MILLISECONDS)
            .readTimeout(timeoutMs, TimeUnit.MILLISECONDS)
            .build()

        val query = ArrayList<String>()
        if (!rev.isNullOrBlank()) query.add("rev=" + enc(rev))
        if (!blocksRev.isNullOrBlank()) query.add("blocks_rev=" + enc(blocksRev))
        val url = buildString {
            append(base).append("/api/mobile/snapshot")
            if (query.isNotEmpty()) append("?").append(query.joinToString("&"))
        }

        val request = Request.Builder()
            .url(url)
            .header("Authorization", "Bearer $token")
            .get()
            .build()

        client.newCall(request).execute().use { resp ->
            val body = resp.body?.string() ?: ""
            if (!resp.isSuccessful) {
                val detail = try {
                    JSONObject(body).optString("error")
                } catch (_: Exception) {
                    ""
                }
                throw ApiException("HTTP ${resp.code} " + detail.ifBlank { resp.message })
            }
            return try {
                JSONObject(body)
            } catch (e: Exception) {
                throw ApiException("服务器返回的不是 JSON：${body.take(80)}")
            }
        }
    }

    fun events(baseUrl: String, token: String, events: org.json.JSONArray, device: String): JSONObject {
        val base = normalize(baseUrl)
        val body = JSONObject().put("device", device).put("events", events)
        val request = Request.Builder()
            .url("$base/api/mobile/events")
            .header("Authorization", "Bearer $token")
            .header("Content-Type", "application/json")
            .post(body.toString().toRequestBody("application/json".toMediaType()))
            .build()
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .build()
            .newCall(request).execute().use { resp ->
                val text = resp.body?.string() ?: ""
                if (!resp.isSuccessful) throw ApiException("HTTP ${resp.code} ${text.take(120)}")
                return JSONObject(text)
            }
    }

    fun normalize(url: String): String {
        var u = url.trim().trimEnd('/')
        if (u.isEmpty()) throw ApiException("还没填服务器地址")
        if (!u.startsWith("http://") && !u.startsWith("https://")) u = "http://$u"
        return u
    }

    private fun enc(s: String): String = URLEncoder.encode(s, "UTF-8")
}
