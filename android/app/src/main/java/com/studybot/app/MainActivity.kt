package com.studybot.app

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.fragment.app.Fragment
import androidx.fragment.app.commit
import androidx.lifecycle.lifecycleScope
import com.studybot.app.data.AppDb
import com.studybot.app.databinding.ActivityMainBinding
import com.studybot.app.sync.Syncer
import com.studybot.app.ui.ChatListFragment
import com.studybot.app.ui.GoalsFragment
import com.studybot.app.ui.NotesFragment
import com.studybot.app.ui.PlanFragment
import com.studybot.app.ui.QuizFragment
import com.studybot.app.ui.Refreshable
import com.studybot.app.ui.TodayFragment
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private var syncing = false
    private var currentTab = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.toolbar.inflateMenu(R.menu.menu_main)
        binding.toolbar.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                R.id.action_sync -> {
                    syncNow(manual = true)
                    true
                }
                R.id.action_settings -> {
                    startActivity(Intent(this, SettingsActivity::class.java))
                    true
                }
                else -> false
            }
        }

        binding.bottomNav.setOnItemSelectedListener { item ->
            showTab(item.itemId)
            true
        }
        binding.bottomNav.selectedItemId = R.id.nav_today

        // 打开 App 自动试一次：3 秒超时，失败静默（方案 §7.3）
        lifecycleScope.launch {
            delay(400)
            syncNow(manual = false)
        }
    }

    override fun onResume() {
        super.onResume()
        refreshLastSync()
        reloadCurrent()
    }

    // ---------------------------------------------------------------- 页面
    private fun showTab(id: Int) {
        if (id == currentTab && supportFragmentManager.findFragmentById(R.id.container) != null) {
            return
        }
        currentTab = id
        val fragment: Fragment = when (id) {
            R.id.nav_plan -> PlanFragment()
            R.id.nav_goals -> GoalsFragment()
            R.id.nav_quiz -> QuizFragment()
            R.id.nav_chat -> ChatListFragment()
            R.id.nav_note -> NotesFragment()
            else -> TodayFragment()
        }
        supportFragmentManager.commit { replace(R.id.container, fragment) }
    }

    private fun reloadCurrent() {
        (supportFragmentManager.findFragmentById(R.id.container) as? Refreshable)?.reload()
    }

    // ---------------------------------------------------------------- 同步
    fun syncFromPull(onDone: () -> Unit) {
        syncNow(manual = true, onDone = onDone)
    }

    private fun syncNow(manual: Boolean, onDone: (() -> Unit)? = null) {
        if (syncing) {
            onDone?.invoke()
            return
        }
        syncing = true
        if (manual) binding.syncStatus.text = getString(R.string.syncing)
        lifecycleScope.launch {
            var message: String? = null
            try {
                val res = withContext(Dispatchers.IO) {
                    Syncer(this@MainActivity).sync(if (manual) 60_000L else 3_000L)
                }
                if (manual) {
                    message = buildString {
                        append("同步完成：${res.goals} 个目标 · ${res.tasks} 张卡")
                        if (res.blocks > 0) append(" · ${res.blocks} 块资料")
                    }
                }
                reloadCurrent()
            } catch (e: Exception) {
                if (manual) {
                    message = getString(R.string.sync_failed, e.message ?: e.javaClass.simpleName)
                }
            } finally {
                syncing = false
                refreshLastSync()
                onDone?.invoke()
                message?.let { Toast.makeText(this@MainActivity, it, Toast.LENGTH_LONG).show() }
            }
        }
    }

    private fun refreshLastSync() {
        lifecycleScope.launch {
            val pair = withContext(Dispatchers.IO) {
                val dao = AppDb.get(this@MainActivity).dao()
                Pair(dao.meta("server_url"), dao.meta("last_sync"))
            }
            val configured = !pair.first.isNullOrBlank()
            val ts = pair.second?.toLongOrNull() ?: 0L
            binding.syncStatus.text = when {
                !configured -> "还没同步 · 点右上「设置」填服务器"
                ts <= 0L -> getString(R.string.never_synced)
                else -> "上次同步：" + ago(ts)
            }
        }
    }

    private fun ago(ts: Long): String {
        val diff = System.currentTimeMillis() - ts
        return when {
            diff < 60_000L -> "刚刚"
            diff < 3_600_000L -> "${diff / 60_000L} 分钟前"
            diff < 86_400_000L -> "${diff / 3_600_000L} 小时前"
            else -> "${diff / 86_400_000L} 天前"
        }
    }
}
