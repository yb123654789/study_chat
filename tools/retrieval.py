#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检索层：按「目标 + 当前问题」从 extracted/ 的块里取最相关段落。

和 build_reading.py 共用同一套切块与分词，避免两套口径。
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_reading as br  # noqa: E402

_BLOCKS: list[dict] | None = None
_DF: dict[str, int] = {}

# 数据结构那批资料按「资料类型」分目录，不按课程；这些顶层目录名不算课程
LEGACY_DIRS = frozenset({"", "inbox", "PPT", "书", "作业", "笔记", "试卷", "大作业",
                         "考试题型或复习大纲"})


def _ensure():
    global _BLOCKS, _DF
    if _BLOCKS is None:
        _BLOCKS = br.load_blocks()
        df: dict[str, int] = {}
        for b in _BLOCKS:
            b["course"] = block_course(b.get("rel") or "")
            for t in br._terms(b["text"]):
                df[t] = df.get(t, 0) + 1
        _DF = df
        print(f"[retrieval] {len(_BLOCKS)} 块就绪", flush=True)
    return _BLOCKS, _DF


def block_course(rel: str) -> str:
    """资料属于哪门课：extracted/<课程>/** → 这门课；
    数据结构那批历史资料按「资料类型」分目录（笔记/书/PPT/试卷…），都归 ds。
    待拆/<课程>/… 和 已拆/<课程>/… 取第二段当课程。"""
    parts = rel.split("/")
    if len(parts) > 1 and parts[0] in ("待拆", "已拆"):
        return parts[1]
    top = parts[0] if len(parts) > 1 else ""
    return "ds" if top in LEGACY_DIRS else top


def _in_chapter(src: str, keys: list[str]) -> bool:
    for k in keys:
        if k in src:
            return True
    return False


def search(goal: dict, query: str, k: int = 3,
           extra_terms: list[str] | None = None) -> list[dict]:
    """extra_terms：中文问题翻出来的英文检索词（聊天里可选开启），
    和中文词一起打分；中文资料照旧，英文段落也能被命中。"""
    blocks, df = _ensure()
    course = goal.get("course") or "ds"
    blocks = [b for b in blocks if b.get("course") == course]
    n = len(blocks)
    if not blocks:
        return []
    weights = br.terms_of(goal)
    for t in br._terms(query):                 # 当前问题里的词权重更高
        w = weights.get(t, 0)
        weights[t] = max(w, 2.5)
    for raw in extra_terms or []:              # 翻译出来的英文词
        t = re.sub(r"\s+", "", str(raw or "")).strip().lower()
        if len(t) < 3 or not re.search(r"[a-z]", t):
            continue
        weights[t] = max(weights.get(t, 0), 1.8)

    keys = br.CHAPTER_FILES.get(goal.get("ch", ""), [])
    items = [(t, w) for t, w in weights.items()
             if df.get(t, 0) / max(1, n) < 0.25]

    scored = []
    for b in blocks:
        text = b["text"]
        low = text.lower()                     # 英文大小写不敏感
        s, hit = 0.0, []
        for t, w in items:
            c = low.count(t)
            if not c:
                continue
            s += min(c, 3) * (math.log((n + 1) / (df.get(t, 0) + 1)) + 0.5) * w * (2 if len(t) >= 3 else 1)
            hit.append(t)
        if s <= 0:
            continue
        if keys and _in_chapter(b["src"], keys):
            s = s * 1.4 + 2
        scored.append((s, b, hit))

    scored.sort(key=lambda x: -x[0])

    out, seen = [], set()
    for s, b, hit in scored[:60]:
        key = (b["src"], b["anchor"])
        if key in seen:
            continue
        seen.add(key)
        low = b["text"].lower()
        pos = min((low.find(h) for h in hit if low.find(h) >= 0), default=0)
        start = max(0, pos - 80)
        snippet = ("…" if start > 0 else "") + b["text"][start:start + 460]
        out.append({"src": b["src"], "anchor": b["anchor"], "score": round(s, 1), "text": snippet})
        if len(out) == k:
            break
    return out


def has_english(course: str) -> bool:
    """这门课有没有英文块（决定聊天要不要多调一次翻译）。"""
    blocks, _ = _ensure()
    return any(b.get("course") == course and br.cjk_ratio(b["text"]) < 0.25
               for b in blocks)


if __name__ == "__main__":
    import json
    g = json.loads(Path(__file__).resolve().parent.parent.joinpath("data/goals.json").read_text(encoding="utf-8-sig"))
    goal = [x for x in g["goals"] if x["id"] == "g04"][0]
    for r in search(goal, "为什么 log n 比 √n 慢"):
        print(r["score"], r["src"][:30], r["anchor"])
