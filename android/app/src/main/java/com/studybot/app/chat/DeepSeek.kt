package com.studybot.app.chat

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** DeepSeek 直连（SSE 流式）。key 只在手机上，不上传电脑。 */
object DeepSeek {

    class ApiError(message: String) : Exception(message)

    data class ToolCall(val id: String, val name: String, val arguments: String)

    data class StreamResult(val text: String, val calls: List<ToolCall>, val tokens: Int)

    private const val URL = "https://api.deepseek.com/chat/completions"

    private val client: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(180, TimeUnit.SECONDS)
            .callTimeout(240, TimeUnit.SECONDS)
            .build()
    }

    suspend fun stream(
        key: String,
        model: String,
        messages: JSONArray,
        tools: JSONArray?,
        onDelta: (String) -> Unit,
    ): StreamResult = withContext(Dispatchers.IO) {
        val payload = JSONObject()
            .put("model", model)
            .put("messages", messages)
            .put("stream", true)
            .put("temperature", 0.7)
            .put("max_tokens", 800)
            .put("stream_options", JSONObject().put("include_usage", true))
        if (tools != null && tools.length() > 0) {
            payload.put("tools", tools)
            payload.put("tool_choice", "auto")
        }
        val request = Request.Builder()
            .url(URL)
            .header("Authorization", "Bearer $key")
            .header("Content-Type", "application/json")
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()

        val text = StringBuilder()
        val calls = LinkedHashMap<Int, MutableMap<String, String>>()
        var tokens = 0

        client.newCall(request).execute().use { resp ->
            if (!resp.isSuccessful) {
                val detail = resp.body?.string()?.take(200) ?: ""
                throw ApiError("模型服务 HTTP ${resp.code} ${detail}")
            }
            val source = resp.body?.source() ?: throw ApiError("模型没有返回内容")
            while (true) {
                val line = source.readUtf8Line() ?: break
                if (!line.startsWith("data:")) continue
                val data = line.removePrefix("data:").trim()
                if (data.isEmpty() || data == "[DONE]") continue
                val obj = try {
                    JSONObject(data)
                } catch (_: Exception) {
                    continue
                }
                obj.optJSONObject("usage")?.let { tokens = it.optInt("total_tokens", tokens) }
                val choice = obj.optJSONArray("choices")?.optJSONObject(0) ?: continue
                val delta = choice.optJSONObject("delta") ?: continue
                // 注意：Android 的 org.json 对 JSON null 调 optString 会得到字符串 "null"。
                // 首块（只有 role）和带 tool_calls 的块 content 都是 null，
                // 不判空就会把一串 "null" 直接拼进回复里。
                val content = textOf(delta, "content")
                if (content.isNotEmpty()) {
                    text.append(content)
                    onDelta(content)
                }
                val tcs = delta.optJSONArray("tool_calls") ?: continue
                for (i in 0 until tcs.length()) {
                    val tc = tcs.optJSONObject(i) ?: continue
                    val idx = tc.optInt("index", 0)
                    val slot = calls.getOrPut(idx) { mutableMapOf("id" to "", "name" to "", "args" to "") }
                    if (tc.has("id")) slot["id"] = textOf(tc, "id")
                    val fn = tc.optJSONObject("function")
                    if (fn != null) {
                        if (fn.has("name")) slot["name"] = textOf(fn, "name")
                        slot["args"] = (slot["args"] ?: "") + textOf(fn, "arguments")
                    }
                }
            }
        }

        val outCalls = calls.entries.sortedBy { it.key }
            .mapNotNull { (_, v) ->
                val name = v["name"].orEmpty()
                if (name.isBlank()) null
                else ToolCall(v["id"].orEmpty(), name, v["args"].orEmpty().ifBlank { "{}" })
            }
        StreamResult(text.toString(), outCalls, tokens)
    }

    /** 取字符串字段；JSON null 当空串（见上面 stream 里的注释）。 */
    private fun textOf(o: JSONObject, key: String): String =
        if (o.isNull(key)) "" else o.optString(key, "")

    /** 非流式一次要完整回答：出题、判分用（不用 SSE，省得拼 delta）。 */
    suspend fun complete(
        key: String,
        model: String,
        messages: JSONArray,
        maxTokens: Int = 1200,
        temperature: Double = 0.8,
    ): String = withContext(Dispatchers.IO) {
        val payload = JSONObject()
            .put("model", model)
            .put("messages", messages)
            .put("stream", false)
            .put("temperature", temperature)
            .put("max_tokens", maxTokens)
        val request = Request.Builder()
            .url(URL)
            .header("Authorization", "Bearer $key")
            .header("Content-Type", "application/json")
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()

        client.newCall(request).execute().use { resp ->
            val body = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                throw ApiError("模型服务 HTTP ${resp.code} ${body.take(200)}")
            }
            val obj = try {
                JSONObject(body)
            } catch (_: Exception) {
                throw ApiError("模型返回的不是 JSON")
            }
            val message = obj.optJSONArray("choices")?.optJSONObject(0)?.optJSONObject("message")
            val content = if (message == null || message.isNull("content")) {
                ""
            } else {
                message.optString("content", "")
            }
            if (content.isBlank()) throw ApiError("模型没有返回内容")
            content
        }
    }
}
