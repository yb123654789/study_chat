#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 goals.json + planner 的确定性排程导出成网页用的 plan.json。

只读 data/，只写 web/data/plan.json，不碰 materials/。
用法：python tools/export_web.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))

import planner  # noqa: E402

START = dt.date(2026, 10, 24)


def main() -> None:
    data = planner.load(BASE / "data" / "goals.json")
    deadline = dt.date.fromisoformat(data["exam_date"])
    res = planner.schedule(data["goals"], START, deadline)

    by_id = {g["id"]: g for g in data["goals"]}

    days = []
    for d in res["plan"]:
        tasks = []
        for t in d["tasks"]:
            g = by_id.get(t["id"], {})
            tasks.append({
                "id": t["id"],
                "title": t["title"],
                "ch": g.get("ch", ""),
                "p": t["p"],
                "kind": t["kind"],
                "min": t["min"],
                "cp": g.get("cp", []),
            })
        days.append({"date": d["date"].isoformat(), "kind": d["kind"],
                     "cap": d["cap"], "tasks": tasks})

    payload = {
        "subject": data["subject"],
        "exam_date": data["exam_date"],
        "exam_format": data.get("exam_format", ""),
        "generated_at": START.isoformat(),
        "total_goals": len(data["goals"]),
        "core_goals": sum(1 for g in data["goals"] if g["p"] == "core"),
        "total_minutes": sum(planner.task_minutes(g) for g in data["goals"]),
        "uncovered": [g["id"] for g in res["uncovered"]],
        "days": days,
    }

    out = BASE / "web" / "data" / "plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(days)} 天 -> {out}")
    print(f"今天 {START}: {[t['id'] for d in days if d['date'] == START.isoformat() for t in d['tasks']]}")


if __name__ == "__main__":
    main()
