#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小测试集：一条命令造出一个能跑的数据目录。

    py tests\\make_demo.py                # 造到 tests\\demo\\
    py tests\\make_demo.py D:\\my-demo     # 造到指定位置

然后：
    $env:STUDYBOT_HOME='<造出来的目录>'; py app\\main.py

`selftest` / `uismoke` 也只用这里的 fixtures：**不读真实资料、不复制真实库**。

fixtures 里是什么（一共几十 KB）：
    mini_goals.json     31 个目标 / 6 章 / 考核日 2027-01-04
    seed_quiz.json      6 套现成题（key 是测试课的目标 id t01..t06）
    mini_course/*.md    3 份几百字的资料（导论 / 线性结构 / 树与图）
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"


def goals_file() -> Path:
    """测试课的 goals（格式和真实 goals.json 一样）。"""
    return FIX / "mini_goals.json"


def quiz_file() -> Path:
    """现成题库种子。"""
    return FIX / "seed_quiz.json"


def course_files() -> list[Path]:
    """3 份小资料（放进 inbox 后被抽成文本）。"""
    return sorted(p for p in (FIX / "mini_course").glob("*") if p.is_file())


def build(root: Path) -> Path:
    """造一个完整的最小数据目录：goals.json + materials/inbox/ 里的 3 份资料。"""
    root = Path(root)
    (root / "materials" / "inbox").mkdir(parents=True, exist_ok=True)
    for src in course_files():
        shutil.copy2(src, root / "materials" / "inbox" / src.name)
    shutil.copy2(goals_file(), root / "goals.json")
    return root


def seed_quiz(db) -> int:
    """把 fixtures 里的题库导进库（和程序第一次启动做的事一致）。"""
    if db.quiz_count() > 0:
        return 0
    raw = json.loads(quiz_file().read_text(encoding="utf-8-sig"))
    n = 0
    for gid, items in raw.items():
        if gid.startswith("_") or not isinstance(items, list):
            continue
        for it in items:
            db.add_quiz(gid, 1, it, source="seed")
            n += 1
    return n


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else HERE / "demo"
    build(root)
    print(f"测试数据目录：{root}")
    print("跑起来（PowerShell）：")
    print(f"  $env:STUDYBOT_HOME='{root}'")
    print("  py app\\main.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
