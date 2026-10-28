#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检索层：按「目标 + 当前问题」从 extracted/ 的块里取最相关段落。

和 build_reading.py 共用同一套切块与分词，避免两套口径。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_reading as br  # noqa: E402

_BLOCKS: list[dict] | None = None
_DF: dict[str, int] = {}

# 数据结构那批资料按「资料类型」分目录，不按课程；这些顶层目录名不算课程
LEGACY_DIRS = frozenset({"", "inbox", "PPT", "书", "笔记", "试卷", "大作业",
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
    数据结构那批历史资料按「资料类型」分目录（笔记/书/PPT/试卷…），都归 ds。"""
    top = rel.split("/", 1)[0] if "/" in rel else ""
    return "ds" if top in LEGACY_DIRS else top


def _in_chapter(src: str, keys: list[str]) -> bool:
    for k in keys:
        if k in src:
            return True
    return False


def search(goal: dict, query: str, k: int = 3) -> list[dict]:
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

    keys = br.CHAPTER_FILES.get(goal.get("ch", ""), [])
    items = [(t, w) for t, w in weights.items()
             if df.get(t, 0) / max(1, n) < 0.25]

    scored = []
    for b in blocks:
        text = b["text"]
        s, hit = 0.0, []
        for t, w in items:
            c = text.count(t)
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
        pos = min((b["text"].find(h) for h in hit if b["text"].find(h) >= 0), default=0)
        start = max(0, pos - 80)
        snippet = ("…" if start > 0 else "") + b["text"][start:start + 460]
        out.append({"src": b["src"], "anchor": b["anchor"], "score": round(s, 1), "text": snippet})
        if len(out) == k:
            break
    return out


if __name__ == "__main__":
    import json
    g = json.loads(Path(__file__).resolve().parent.parent.joinpath("data/goals.json").read_text(encoding="utf-8-sig"))
    goal = [x for x in g["goals"] if x["id"] == "g04"][0]
    for r in search(goal, "为什么 log n 比 √n 慢"):
        print(r["score"], r["src"][:30], r["anchor"])
