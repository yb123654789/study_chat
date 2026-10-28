package com.studybot.app.sync

import android.content.Context
import android.util.Log
import com.studybot.app.data.AppDb
import com.studybot.app.data.Block

/**
 * bigram 倒排索引：只索引资料块，不索引笔记（P5 的硬规则）。
 *
 * P1 只建不用；P2 检索直接查这里。App 重启后从 Room 懒加载重建一次。
 */
object BigramIndex {

    @Volatile
    var ready: Boolean = false
        private set

    @Volatile
    var blockCount: Int = 0
        private set

    @Volatile
    var termCount: Int = 0
        private set

    private var index: Map<String, List<Int>> = emptyMap()
    private var blocks: List<Block> = emptyList()

    private val LATIN = Regex("[A-Za-z][A-Za-z0-9]{2,}")

    // 和 tools/build_reading.py 的 STOP 一致，防止「数据/结构」这类泛词拉高分
    private val STOP = setOf(
        "的是", "什么", "一个", "以及", "可以", "这个", "那个", "我们", "就是", "不是",
        "如果", "因为", "所以", "能够", "应该", "需要", "进行", "通过", "对于", "没有",
        "已经", "这些", "那些", "一些", "如何", "怎样", "为什", "么样", "并且", "而是",
        "自己", "时候", "问题", "方法", "使用", "实现", "数据", "结构", "算法",
    )

    /** App 重启后索引是空的，从本地库重建一次（约 3–5 秒，放后台线程调）。 */
    suspend fun ensure(context: Context) {
        if (ready) return
        val rows = AppDb.get(context).dao().blocks()
        if (rows.isNotEmpty()) build(rows)
    }

    fun build(items: List<Block>) {
        val map = HashMap<String, MutableList<Int>>(1 shl 16)
        items.forEachIndexed { i, b ->
            for (t in terms(b.text)) {
                map.getOrPut(t) { ArrayList(2) }.add(i)
            }
        }
        index = map
        blocks = items
        blockCount = items.size
        termCount = map.size
        ready = true
        Log.i("BigramIndex", "建好：$blockCount 块 / $termCount 个词")
    }

    fun lookup(term: String): List<Int> = index[term] ?: emptyList()

    fun df(term: String): Int = index[term]?.size ?: 0

    fun all(): List<Block> = blocks

    fun blockAt(position: Int): Block? = blocks.getOrNull(position)

    fun terms(text: String): Set<String> {
        val out = HashSet<String>(text.length / 2 + 8)
        val run = StringBuilder()
        for (ch in text) {
            if (ch in '\u4e00'..'\u9fff') {
                run.append(ch)
            } else {
                emit(run, out)
                run.setLength(0)
            }
        }
        emit(run, out)
        for (m in LATIN.findAll(text)) out.add(m.value.lowercase())
        return out
    }

    private fun emit(run: StringBuilder, out: MutableSet<String>) {
        if (run.length < 2) return
        for (i in 0 until run.length - 1) {
            val t = run.substring(i, i + 2)
            if (t !in STOP) out.add(t)
        }
    }
}
