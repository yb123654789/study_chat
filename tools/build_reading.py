#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切块 + 分词：检索层（`tools/retrieval.py`）和知识库共用这一套口径。

不是模型总结，是关键词检索 + 出处引用：宁可少而准，也不要编。
- 过滤 PDF 字体编码坏掉的乱码块（汉字占比过低）
- 长章节按 ~700 字切窗，锚点取「p.N / slide N / markdown 标题」
- 打分：标题词权重 2、checkpoint 词权重 1，再乘 IDF；标题词一个都没命中就打折

（以前还负责生成 web/data/reading.json 给旧 PWA 用；那条线已删，现在只留 load_blocks / terms_of。）
"""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

try:                          # 抽好的文本在数据目录里，不跟着代码走
    import paths  # type: ignore

    EXTRACTED = paths.extracted_dir()
except Exception:             # noqa: BLE001 独立跑工具时退回老路径
    EXTRACTED = BASE / "extracted"

CJK = re.compile(r"[\u4e00-\u9fff]")
CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")
LATIN_CHARS = re.compile(r"[A-Za-z]")
ANCHOR = re.compile(r"^##\s*(p\.\d+|Slide\s*\d+)\s*$")
HEAD = re.compile(r"^#{1,4}\s*(\S.*)$")

STOP = {
    "的是", "什么", "一个", "以及", "可以", "这个", "那个", "我们", "就是", "不是",
    "如果", "因为", "所以", "能够", "应该", "需要", "进行", "通过", "对于", "没有",
    "已经", "这些", "那些", "一些", "如何", "怎样", "为什", "么样", "并且", "而是",
    "自己", "时候", "问题", "方法", "使用", "实现", "数据", "结构", "算法",
}

# 章节 -> 资料文件名关键词。只作加权，不硬性过滤。
CHAPTER_FILES = {
    "导论": ["Introduction", "1 引言", "chapter2"],
    "复杂度": ["Algorithm Analysis", "2 复杂度分析", "chapter2"],
    "线性表": ["List Stack", "3 线性表", "3.1 栈", "chapter3"],
    "散列": ["Hashing", "5 散列", "Relations"],
    "堆": ["Heap", "6 堆"],
    "排序": ["Sorting", "7 排序算法"],
    "并查集": ["Disjoint Set", "8 不相关集"],
    "图": ["Graph Algorithms", "9 图", "Lec6 Graphs"],
    "树": ["Trees", "4 树", "树 详解"],
    "应试": ["试卷", "复习提纲", "常考12题", "查缺补漏", "内容回忆"],
}

# 有题库的目标给一组更精确的检索词（其他目标靠标题+checkpoint 自动出词）
CURATED = {
    "g01": ["抽象数据类型", "ADT", "List"],
    "g02": ["递归树", "递归关系", "Hanoi", "递归"],
    "g03": ["大O", "大", "定义", "证明", "上界"],
    "g04": ["增长率", "阶", "排序", "log"],
    "g05": ["双重循环", "嵌套", "循环"],
    "g06": ["递归子程序", "主定理", "递归式", "递推"],
}


def cjk_ratio(s: str) -> float:
    return len(CJK.findall(s)) / max(1, len(s))


def window(src: str, anchor: str, text: str, size: int = 700) -> list[dict]:
    text = re.sub(r"\s+", "", text).replace("```", "").replace("---", "")
    if len(text) <= size:
        return [{"src": src, "anchor": anchor, "text": text}]
    out, start, part = [], 0, 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = max(text.rfind("。", start, end), text.rfind("；", start, end))
            if cut > start + size // 3:
                end = cut + 1
        out.append({"src": src, "anchor": anchor + ("" if part == 0 else f"·{part + 1}"),
                    "text": text[start:end]})
        start, part = end, part + 1
    return out


def load_blocks() -> list[dict]:
    blocks = []
    for p in sorted(EXTRACTED.rglob("*.txt")):
        # 旧版本让位后改名成 *.old.txt：留着能翻回来，但不进检索
        if p.name.endswith(".old.txt"):
            continue
        # 音视频的分段转写（.media/0000.txt）是中间产物，别当资料块
        if ".media" in p.parts:
            continue
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if len(raw) < 200:
            continue
        name = p.stem[:60]
        rel = str(p.relative_to(EXTRACTED)).replace("\\", "/")
        before = len(blocks)
        buf, anchor = [], "开头"
        for line in raw.splitlines():
            s = line.strip()
            m = ANCHOR.match(s)
            if m:
                if buf:
                    blocks.extend(window(name, anchor, "".join(buf)))
                buf, anchor = [], m.group(1).replace("Slide", "slide ")
                continue
            h = HEAD.match(s)
            if h:
                if buf:
                    blocks.extend(window(name, anchor, "".join(buf)))
                buf, anchor = [], re.sub(r"[*#`]", "", h.group(1))[:22]
                continue
            buf.append(s)
            if sum(len(x) for x in buf) > 1400:
                blocks.extend(window(name, anchor, "".join(buf)))
                buf = []
        if buf:
            blocks.extend(window(name, anchor, "".join(buf)))
        for b in blocks[before:]:          # 记下文件相对路径，检索层按课程过滤用
            b["rel"] = rel

    out = []
    seen_text: set[str] = set()
    for b in blocks:
        t = re.sub(r"\s+", "", b["text"])
        cjk = cjk_ratio(t)
        latin = len(LATIN_CHARS.findall(t)) / max(1, len(t))
        # 中文块照旧；英文/中英混排的块也收（以前只收中文占比 ≥25%，
        # 导致英文讲义和视频转写整份进不了检索）
        if not (80 <= len(t) <= 900) or (cjk < 0.25 and latin < 0.5):
            continue
        # 同一段话在 PPT 和教材里各有一份（或同一份资料重复入库）：只留一份，
        # 否则检索会把两份几乎一样的段落都塞给模型。
        key = hashlib.sha1(t.encode("utf-8")).hexdigest()
        if key in seen_text:
            continue
        seen_text.add(key)
        out.append({**b, "text": t})
    return out


def _terms(text: str) -> set[str]:
    terms = set()
    for run in CJK_RUN.findall(text):
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg not in STOP:
                terms.add(bg)
    for w in LATIN.findall(text):
        terms.add(w.lower())          # 英文大小写不敏感（Heap / heap 同一个词）
    return terms


def terms_of(goal: dict) -> dict[str, float]:
    """{词: 权重}，标题词 2、checkpoint 和章节词 1。"""
    w: dict[str, float] = {}
    for t in _terms(goal.get("ch", "")):
        w[t] = 1.0
    for t in _terms(goal.get("title", "")):
        w[t] = 2.0
    for cp in goal.get("cp", []):
        for t in _terms(cp):
            w.setdefault(t, 1.0)
    for q in CURATED.get(goal.get("id", ""), []):
        w[q] = 3.0
    return w


def pick(goal: dict, blocks: list[dict], df: dict, n_blocks: int, keys: list[str]) -> list[dict]:
    weights = terms_of(goal)
    title_terms = _terms(goal.get("title", ""))
    items = [(t, w) for t, w in weights.items()
             if df.get(t, 0) / max(1, n_blocks) < 0.25]

    scored = []
    for b in blocks:
        text = b["text"]
        score, hit = 0.0, []
        for t, w in items:
            c = text.count(t)
            if not c:
                continue
            idf = math.log((n_blocks + 1) / (df.get(t, 0) + 1)) + 0.5
            score += min(c, 3) * idf * w * (2 if len(t) >= 3 else 1)
            hit.append(t)
        if not hit:
            continue
        if not (title_terms & set(hit)):
            score *= 0.35                      # 标题词一个都没中，多半跑偏了
        in_chapter = False
        for k in keys:
            if k in b["src"]:
                in_chapter = True
                break
        if in_chapter:
            score = score * 1.4 + 2            # 同章节的资料加权，不硬性过滤
        if score >= 3:
            scored.append((score, b, hit))

    scored.sort(key=lambda x: -x[0])
    top = scored[0][0] if scored else 0.0

    def snippet_of(b, hit):
        pos = min((b["text"].find(h) for h in hit if b["text"].find(h) >= 0), default=0)
        start = max(0, pos - 100)
        s = b["text"][start:start + 300]
        return ("…" if start > 0 else "") + s + ("…" if start + 300 < len(b["text"]) else "")

    picked, seen_anchor, seen_src = [], set(), set()
    for score, b, hit in scored:                       # 第一轮：不同资料各取 1 段
        if score < top * 0.45:                         # 相关性差太多就不给，宁少不滥
            break
        if b["src"] in seen_src or (b["src"], b["anchor"]) in seen_anchor:
            continue
        picked.append({"src": b["src"], "anchor": b["anchor"], "text": snippet_of(b, hit)})
        seen_src.add(b["src"])
        seen_anchor.add((b["src"], b["anchor"]))
        if len(picked) == 2:
            break
    if len(picked) < 2:                                # 第二轮：同源换锚点补齐
        for score, b, hit in scored:
            if score < top * 0.6:
                break
            if (b["src"], b["anchor"]) in seen_anchor:
                continue
            picked.append({"src": b["src"], "anchor": b["anchor"], "text": snippet_of(b, hit)})
            seen_anchor.add((b["src"], b["anchor"]))
            if len(picked) == 2:
                break
    return picked


