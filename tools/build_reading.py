#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为每个目标从 extracted/ 里检索相关段落，生成 web/data/reading.json。

不是模型总结，是关键词检索 + 出处引用：宁可少而准，也不要编。
- 过滤 PDF 字体编码坏掉的乱码块（汉字占比过低）
- 长章节按 ~700 字切窗，锚点取「p.N / slide N / markdown 标题」
- 打分：标题词权重 2、checkpoint 词权重 1，再乘 IDF；标题词一个都没命中就打折

用法：python tools/build_reading.py
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "web" / "data" / "reading.json"

try:                          # 抽好的文本在数据目录里，不跟着代码走
    import paths  # type: ignore

    EXTRACTED = paths.extracted_dir()
except Exception:             # noqa: BLE001 独立跑工具时退回老路径
    EXTRACTED = BASE / "extracted"

CJK = re.compile(r"[\u4e00-\u9fff]")
CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")
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

# 手写导读：只给有题库的前 6 个目标，其余留空由 checkpoints 兜底
LEADS = {
    "g01": "先把「怎么存」和「能做什么」分开。ADT 只约定操作和语义，实现方式留给你换——换实现时代价会变，但接口不变。",
    "g02": "递归的难点不是写出来，是算复杂度。会画递归树、会展开递归式，后面所有分治算法都省一半力气。",
    "g03": "O / Ω / Θ 不是「大概多快」，是三句话的数学定义。考试要你写出 c 和 n₀，写不出就等于没证。",
    "g04": "增长率排序是送分题，但「为什么 log n 比 √n 慢」要能说出理由：任何正幂次最终都会超过对数。",
    "g05": "给一段代码，先数最内层语句执行几次，再决定取最好、最坏还是平均——三个答案都可能对，看题目问哪个。",
    "g06": "主定理不用背公式，只要会做一件事：把 f(n) 和 n^(log_b a) 比大小，谁大听谁的。",
}

POINTS = {
    "g01": ["ADT = 数据 + 操作 + 语义，不含实现", "同一 ADT 可以有多种实现，代价不同", "先定接口，实现可替换，程序才改得动"],
    "g02": ["递归式写成 T(n) = aT(n/b) + f(n)", "递归树：每层代价 × 层数", "Hanoi 是 2ⁿ−1，指数级"],
    "g03": ["O 是上界，Ω 是下界，Θ 是两者都成立", "证明要给出具体的 c 和 n₀", "低阶项和常数在渐近分析里丢掉"],
    "g04": ["n! > 2ⁿ > n³ > n² > n log n > n > √n > log n", "n^ε 最终超过 log n（ε > 0）", "化简时只留最高阶"],
    "g05": ["嵌套循环先看内层执行次数", "最坏/平均差别在输入分布假设", "平均情况要先说清「等概率」假设"],
    "g06": ["比较 f(n) 与 n^(log_b a)", "f 小 → Θ(n^(log_b a))", "相等 → 多乘一个 log n"],
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
        if not (80 <= len(t) <= 900) or cjk_ratio(t) < 0.25:
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
        terms.add(w)
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


def main() -> None:
    data = json.loads((BASE / "data" / "goals.json").read_text(encoding="utf-8-sig"))
    blocks = load_blocks()

    df: dict[str, int] = {}
    for b in blocks:
        for t in _terms(b["text"]):
            df[t] = df.get(t, 0) + 1

    out = {}
    for g in data["goals"]:
        keys = CHAPTER_FILES.get(g["ch"], [])
        out[g["id"]] = {
            "lead": LEADS.get(g["id"], ""),
            "points": POINTS.get(g["id"], g.get("cp", [])),
            "chapter": g["ch"],
            "excerpts": pick(g, blocks, df, len(blocks), keys),
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    n_ok = sum(1 for v in out.values() if v["excerpts"])
    print(f"块 {len(blocks)} 个 -> {OUT}")
    print(f"有出处的目标 {n_ok}/{len(out)}")
    for gid in list(out)[:6]:
        e = out[gid]["excerpts"]
        print(f"  {gid}: {len(e)} 段", (e[0]['src'][:30] + ' · ' + e[0]['anchor']) if e else "-")


if __name__ == "__main__":
    main()
