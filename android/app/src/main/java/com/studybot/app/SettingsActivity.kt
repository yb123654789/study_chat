package com.studybot.app

import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.studybot.app.data.AppDb
import com.studybot.app.data.Meta
import com.studybot.app.databinding.ActivitySettingsBinding
import com.studybot.app.net.Api
import com.studybot.app.sync.Syncer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        binding.toolbar.setNavigationOnClickListener { finish() }

        lifecycleScope.launch {
            val pair = withContext(Dispatchers.IO) {
                val dao = AppDb.get(this@SettingsActivity).dao()
                Pair(dao.meta("server_url").orEmpty(), dao.meta("token").orEmpty())
            }
            binding.serverInput.setText(pair.first)
            binding.tokenInput.setText(pair.second)
            binding.keyInput.setText(Secure.apiKey(this@SettingsActivity))
        }

        binding.saveButton.setOnClickListener { save(andSync = false) }
        binding.syncButton.setOnClickListener { save(andSync = true) }
    }

    private fun save(andSync: Boolean) {
        val rawServer = binding.serverInput.text?.toString()?.trim().orEmpty()
        val token = binding.tokenInput.text?.toString()?.trim().orEmpty()
        val key = binding.keyInput.text?.toString()?.trim().orEmpty()

        val server = try {
            Api.normalize(rawServer)
        } catch (e: Exception) {
            ""
        }
        if (server.isBlank()) {
            binding.serverInput.error = "请填服务器地址"
            return
        }
        if (token.isBlank()) {
            binding.tokenInput.error = "请填访问 token"
            return
        }

        Secure.setApiKey(this, key)
        lifecycleScope.launch {
            withContext(Dispatchers.IO) {
                val dao = AppDb.get(this@SettingsActivity).dao()
                dao.putMeta(Meta("server_url", server))
                dao.putMeta(Meta("token", token))
            }
            binding.statusText.text = "已保存"
            if (andSync) {
                doSync()
            } else {
                Toast.makeText(this@SettingsActivity, "已保存，回主页会自动同步", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private suspend fun doSync() {
        binding.syncButton.isEnabled = false
        binding.statusText.text = "正在同步…"
        try {
            val res = withContext(Dispatchers.IO) {
                Syncer(this@SettingsActivity).sync(60_000L)
            }
            binding.statusText.text = buildString {
                append("同步完成：${res.goals} 个目标 · ${res.tasks} 张卡")
                if (res.blocks > 0) append(" · ${res.blocks} 块资料")
                append("\nrev=${res.rev}  blocks_rev=${res.blocksRev}")
            }
        } catch (e: Exception) {
            binding.statusText.text = "同步失败：${e.message ?: e.javaClass.simpleName}"
        } finally {
            binding.syncButton.isEnabled = true
        }
    }
}
