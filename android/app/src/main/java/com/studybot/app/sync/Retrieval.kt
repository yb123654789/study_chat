package com.studybot.app.sync

import android.content.Context
import com.studybot.app.data.Block
import com.studybot.app.data.Goal
import org.json.JSONArray
import kotlin.math.ln

/**
 * 检索：照抄 tools/retrieval.py 的口径 —— bigram 分词、停用词、df、章节加权。
 * 只查资料块（blocks 表），**不查笔记**。
 *
 * 笔记不索引（P5 硬规则）：笔记存 notes 表，只有资料进 bigram 索引；
 * 别把笔记塞进 blocks，也别在 BigramIndex 里加 notes 的数据源。
 */
object Retrieval {

    data class Hit(val src: String, val anchor: String, val text: String, val score: Double)

    // 章节 -> 资料文件名关键词（只加权，不硬过滤）
    private val CHAPTER_FILES = mapOf(
        "导论" to listOf("Introduction", "1 引言", "chapter2"),
        "复杂度" to listOf("Algorithm Analysis", "2 复杂度分析", "chapter2"),
        "线性表" to listOf("List Stack", "3 线性表", "3.1 栈", "chapter3"),
        "散列" to listOf("Hashing", "5 散列", "Relations"),
        "堆" to listOf("Heap", "6 堆"),
        "排序" to listOf("Sorting", "7 排序算法"),
        "并查集" to listOf("Disjoint Set", "8 不相关集"),
        "图" to listOf("Graph Algorithms", "9 图", "Lec6 Graphs"),
        "树" to listOf("Trees", "4 树", "树 详解"),
        "应试" to listOf("试卷", "复习提纲", "常考12题", "查缺补漏", "内容回忆"),
    )

    // 有题库的前 6 个目标给一组更精确的检索词
    private val CURATED = mapOf(
        "g01" to listOf("抽象数据类型", "ADT", "List"),
        "g02" to listOf("递归树", "递归关系", "Hanoi", "递归"),
        "g03" to listOf("大O", "大", "定义", "证明", "上界"),
        "g04" to listOf("增长率", "阶", "排序", "log"),
        "g05" to listOf("双重循环", "嵌套", "循环"),
        "g06" to listOf("递归子程序", "主定理", "递归式", "递推"),
    )

    suspend fun search(context: Context, goal: Goal, query: String, k: Int = 3): List<Hit> {
        BigramIndex.ensure(context)
        val blocks: List<Block> = BigramIndex.all().filter { it.course == goal.course }
        if (blocks.isEmpty()) return emptyList()
        val n = blocks.size

        val weights = termsOf(goal).toMutableMap()
        for (t in BigramIndex.terms(query)) {
            weights[t] = maxOf(weights[t] ?: 0.0, 2.5)
        }
        val keys = CHAPTER_FILES[goal.ch].orEmpty()
        val items = weights.filter { (t, _) -> BigramIndex.df(t).toDouble() / n < 0.25 }
        if (items.isEmpty()) return emptyList()

        val scored = ArrayList<Triple<Double, Block, List<String>>>()
        for (b in blocks) {
            var s = 0.0
            val hit = ArrayList<String>()
            for ((t, w) in items) {
                val c = countOf(b.text, t)
                if (c == 0) continue
                val idf = ln((n + 1.0) / (BigramIndex.df(t) + 1.0)) + 0.5
                s += minOf(c, 3) * idf * w * (if (t.length >= 3) 2 else 1)
                hit.add(t)
            }
            if (s <= 0.0) continue
            if (keys.isNotEmpty() && keys.any { b.src.contains(it) }) s = s * 1.4 + 2
            scored.add(Triple(s, b, hit))
        }
        scored.sortByDescending { it.first }

        val out = ArrayList<Hit>()
        val seen = HashSet<String>()
        for ((s, b, hit) in scored) {
            val key = b.src + "|" + b.anchor
            if (!seen.add(key)) continue
            val pos = hit.map { b.text.indexOf(it) }.filter { it >= 0 }.minOrNull() ?: 0
            val start = maxOf(0, pos - 80)
            val snippet = (if (start > 0) "…" else "") +
                b.text.substring(start, minOf(b.text.length, start + 460))
            out.add(Hit(b.src, b.anchor, snippet, s))
            if (out.size == k) break
        }
        return out
    }

    /** 标题词权重 2、验收点和章节词 1、人工检索词 3（照抄 build_reading.terms_of）。 */
    fun termsOf(goal: Goal): Map<String, Double> {
        val w = HashMap<String, Double>()
        for (t in BigramIndex.terms(goal.ch)) w[t] = 1.0
        for (t in BigramIndex.terms(goal.title)) w[t] = 2.0
        for (cp in cpList(goal)) for (t in BigramIndex.terms(cp)) w.putIfAbsent(t, 1.0)
        for (q in CURATED[goal.id].orEmpty()) w[q] = 3.0
        return w
    }

    fun cpList(goal: Goal): List<String> {
        val arr: JSONArray = try {
            JSONArray(goal.cp.ifBlank { "[]" })
        } catch (_: Exception) {
            JSONArray()
        }
        val out = ArrayList<String>(arr.length())
        for (i in 0 until arr.length()) {
            val s = arr.optString(i)
            if (s.isNotBlank()) out.add(s)
        }
        return out
    }

    private fun countOf(text: String, term: String): Int {
        var c = 0
        var i = text.indexOf(term)
        while (i >= 0) {
            c++
            i = text.indexOf(term, i + term.length)
        }
        return c
    }
}
