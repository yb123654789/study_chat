#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知识库规模测试：2000 个节点下，「切页 / 重建 / 列表」各要多久。

    py tests\\bench_kb.py

全部在临时目录里造（10 章 × 200 节点 = 2000），不碰真实数据。
验收线：切页 < 100ms、没变化的增量重建 < 1s。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
for p in (str(BASE / "app"), str(BASE / "tools")):
    sys.path.insert(0, p)

import db as dbmod   # noqa: E402
import engine        # noqa: E402
import paths         # noqa: E402

CHAPTERS = [f"章{c:02d}" for c in range(10)]
PER_CH = 200


def main() -> int:
    tmp = Path(tempfile.gettempdir()) / f"kb-bench-{os.getpid()}-{time.time_ns()}"
    tmp.mkdir(parents=True, exist_ok=False)
    paths.use_root(tmp, save=False)

    d = dbmod.DB()
    d.init()
    d.save_course("ds", name="规模测试课", exam_date="2027-01-04")
    brev = engine.blocks_rev()

    goals, nodes = [], []
    for c, ch in enumerate(CHAPTERS):
        folder = tmp / "知识库" / ch
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(PER_CH):
            gid = f"g{c:02d}{i:03d}"
            title = f"第{c}章第{i}个知识点"
            rel = f"{ch}/{gid}-{title}.md"
            body = (f"---\nid: {gid}\ncourse: ds\nchapter: {ch}\ntitle: {title}\n"
                    f"priority: core\nminutes: 30\nversion: 1\n---\n\n# {title}\n\n" + "正文" * 200)
            (tmp / "知识库" / rel).write_text(body, encoding="utf-8")
            gd = {"id": gid, "course": "ds", "ch": ch, "title": title, "p": "core",
                  "m": 30, "d": [], "cp": []}
            goals.append((gid, "ds", ch, title, "core", 30, "[]", "[]", c * PER_CH + i))
            nodes.append((gid, "ds", ch, title, 30, rel, 1, engine._goal_hash(gd), brev,
                          hashlib.sha1(body.encode()).hexdigest(), ""))
    d.conn.executemany(
        "INSERT INTO goals(id,course,ch,title,p,m,d,cp,seq) VALUES(?,?,?,?,?,?,?,?,?)", goals)
    d.conn.executemany(
        "INSERT INTO kb_nodes(id,course,chapter,title,minutes,path,version,goal_hash,"
        "blocks_rev,body_hash,written_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", nodes)
    d.conn.commit()
    d.kb_set_meta("chapter_order", json.dumps(CHAPTERS, ensure_ascii=False))
    engine.write_knowledge(d)                     # 先真建一次（含索引/树），后面才测增量

    print(f"造好 {len(nodes)} 个节点（{len(CHAPTERS)} 章）")

    t = time.perf_counter()
    chs = engine.kb_chapters(d)
    t_ch = (time.perf_counter() - t) * 1000

    t = time.perf_counter()
    eng = engine.knowledge_list(d, chapter=CHAPTERS[3])
    t_open = (time.perf_counter() - t) * 1000

    t = time.perf_counter()
    allrows = engine.knowledge_list(d)
    t_all = (time.perf_counter() - t) * 1000

    t = time.perf_counter()
    n = engine.kb_count(d)
    t_cnt = (time.perf_counter() - t) * 1000

    t = time.perf_counter()
    r = engine.write_knowledge(d)          # 什么都没变 → 应该全跳过
    t_inc = (time.perf_counter() - t) * 1000

    print(f"列章（{len(chs)} 行）        : {t_ch:7.2f} ms")
    print(f"展开一章（{len(eng)} 个节点）    : {t_open:7.2f} ms")
    print(f"全库列表（{len(allrows)} 行）  : {t_all:7.2f} ms")
    print(f"COUNT(*)（{n}）          : {t_cnt:7.2f} ms")
    print(f"增量重建（全跳过）      : {t_inc:7.2f} ms  written={r['written']} skipped={r['skipped']}")

    ok = (t_ch < 100 and t_open < 100 and t_inc < 1000 and r["written"] == 0)
    print("\n验收线（切页 <100ms / 增量重建 <1s）：" + ("通过" if ok else "没过"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
