#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""远程层自检：在本进程起一个 API 服务，用瘦客户端把它跑一遍。

    py -X utf8 server\test_api.py

不碰真实库、不连 Telegram、不花钱（模型被打桩）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "app"))
sys.path.insert(0, str(BASE / "tools"))

import api                      # noqa: E402
import chat                     # noqa: E402
import db as dbmod              # noqa: E402
import engine                   # noqa: E402
import llm                      # noqa: E402
import paths                    # noqa: E402
from remote import RemoteDB, RemoteError  # noqa: E402

OK, BAD = "  ✓", "  ✗"
fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print((OK if cond else BAD), name, ("" if cond else f"  → {detail}"))
    if not cond:
        fails.append(name)


def temp_dir(prefix: str) -> Path:
    """临时目录：不用 tempfile.mkdtemp（按 0700 建，沙箱里子目录建不进去）。"""
    p = Path(tempfile.gettempdir()) / f"{prefix}{os.getpid()}-{time.time_ns()}"
    p.mkdir(parents=True, exist_ok=False)
    return p


QUIZ_JSON = json.dumps({"questions": [
    {"type": "choice", "stem": "选哪个？", "options": ["甲", "乙", "丙", "丁"], "answer": 1},
    {"type": "choice", "stem": "复杂度？", "options": ["O(1)", "O(n)", "O(n²)"], "answer": 1},
    {"type": "short", "stem": "解释一下。", "rubric": ["要点一", "要点二"]},
]}, ensure_ascii=False)


def fake_chat(messages, db=None, max_tokens=800, temperature=0.7, timeout=90,
              extra=None):
    sys_prompt = messages[0]["content"]
    if "出题人" in sys_prompt:
        return QUIZ_JSON
    if "阅卷人" in sys_prompt:
        return json.dumps({"pass": True, "covered": ["要点一"], "missed": [],
                           "feedback": "答到了"}, ensure_ascii=False)
    return "讲解：先看定义，再看代价。"


def fake_tools(messages, tools=None, db=None, max_tokens=800, temperature=0.7, timeout=90,
               extra=None):
    if any(m.get("role") == "tool" for m in messages):
        return {"text": "好，开始小测。", "tool_calls": [], "finish": "stop",
                "tokens": 10, "usage": {}, "model": "fake"}
    last = (messages[-1].get("content") or "") if messages else ""
    if "测" in last:
        return {"text": "", "finish": "tool_calls", "tokens": 12, "usage": {},
                "model": "fake",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "start_quiz", "arguments": "{}"}}]}
    return {"text": "ADT 规定操作，实现规定代价。", "tool_calls": [], "finish": "stop",
            "tokens": 20, "usage": {}, "model": "fake"}


def main() -> int:
    tmp = temp_dir("studybot-api-")
    paths.use_root(tmp, save=False)          # 上传/检索都落在这个临时目录里
    # 造一块资料文本，给手机快照的「块指纹 / 增量」用（真实资料不进来）
    ext = paths.extracted_dir() / "inbox"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / "快照测试.txt").write_text(
        "二叉树是每个节点最多有两个子节点的树结构，左子树和右子树有顺序之分。" * 12,
        encoding="utf-8")
    d = dbmod.DB(tmp / "test.db")
    d.init()
    d.import_goals(BASE / "tests" / "fixtures" / "mini_goals.json")
    engine.regenerate(d)
    d.set("api_key", "sk-test-only")

    api.configure(d, None, "testtoken")
    srv = api.serve("127.0.0.1", 0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    llm.chat_with_tools = fake_tools
    llm.chat = fake_chat

    db = RemoteDB(f"http://127.0.0.1:{port}", "testtoken", timeout=60)
    try:
        check("健康检查", db.call("GET", "/api/health")["ok"] is True)
        check("状态里有统计", db.stats()["total_goals"] == 31, str(db.stats()["total_goals"]))
        local_ids = [t["id"] for t in d.today_tasks()]
        check("今日任务和服务器一致", [t["id"] for t in db.today_tasks()] == local_ids)
        check("目标列表", len(db.goals()) == 31, str(len(db.goals())))
        check("题库列表", len(db.all_quiz()) == len(d.all_quiz()))
        check("密钥不下发", db.get("api_key") == "****", db.get("api_key"))
        check("token 掩码", db.get("tg_token") in ("", "****"), db.get("tg_token"))

        db.set("notify_time", "06:45")
        check("设置写回后立刻读到", db.get("notify_time") == "06:45", db.get("notify_time"))

        # 日志能清（原来是只增不减）
        db.log("远端测试日志", "info")
        cleared = db.clear_logs()
        check("远端日志能清空", cleared >= 1 and len(db.logs(10)) == 0, str(cleared))

        # 拆目标接口：没有新资料时不该调模型、不该花钱
        rd = db.action("goals/derive", course="ds")
        check("远端拆目标接口（没新资料就返回 0）", rd["result"]["added"] == 0,
              str(rd)[:90])

        tid = local_ids[0]
        db.set_task_state(tid, "skipped")
        check("改任务状态", db.task(tid)["state"] == "skipped", db.task(tid)["state"])
        db.set_task_state(tid, "todo")

        r = db.action("replan")
        check("远程重排", r["inserted"] >= 50, str(r.get("inserted")))

        # 重排会重建今天的卡，id 会变，后面的用例必须重新取
        task = db.today_tasks()[0]
        tid, gid = task["id"], task["goal_id"]
        qs = db.action("quiz/ensure", goal_id=gid)["quiz"]
        check("远程出题 3 道", len(qs) == 3, str(len(qs)))
        q = next(x for x in qs if x["type"] == "choice")
        g = db.action("quiz/grade", quiz_id=q["id"], goal_id=gid, answer=str(q["answer"]))
        check("远程判分", g["pass"] is True, str(g))

        res = db.action("chat", task_id=tid, text="ADT 是什么？")
        check("远程聊天有回答", bool(res["text"]), str(res)[:80])
        check("聊天落库", len(db.conv_messages(res["conv_id"], 10)) >= 2,
              str(len(db.conv_messages(res["conv_id"], 10))))

        res2 = db.action("chat", task_id=tid, text="测测我")
        check("远程工具调用", res2.get("tool") == "start_quiz", str(res2.get("tool")))
        msgs = db.conv_messages(res2["conv_id"], 20)
        check("工具回执落库", any(m["role"] == "tool" for m in msgs),
              str([m["role"] for m in msgs]))

        # 错题回炉 / 落后重排这两个动作，桌面端在远端模式下也要能触发
        from datetime import date, timedelta
        yday = (date.today() - timedelta(days=1)).isoformat()
        rid = db.add_task(date=yday, goal_id=gid, title="欠下的卡", kind="review", minutes=15)["id"]
        check("远端插卡接口", int(rid) > 0, str(rid))
        check("远端看到落后", db.lag_days() >= 1, str(db.lag_days()))
        r2 = db.action("replan")
        check("远程重排吸收落后", r2["inserted"] >= 50 and db.lag_days() == 0,
              f'{r2.get("inserted")} / {db.lag_days()}')
        check("远端失败计数接口", db.fail_streak(gid) == 0, str(db.fail_streak(gid)))

        # 多课程：建课 / 按课程导入 / 按课程重排 / 状态里带课程
        check("课程列表有数据结构", any(c["id"] == "ds" for c in db.courses()), str(db.courses()))
        db.save_course("os", name="操作系统", exam_date="2026-12-18",
                       weekend_ratio=150, day_cap=0)
        check("远端建课", any(c["id"] == "os" for c in db.courses()), str(len(db.courses())))
        check("远端课程存的是周末系数/每日上限",
              int(db.course("os")["weekend_ratio"]) == 150 and int(db.course("os")["day_cap"]) == 0,
              str(dict(db.course("os"))))
        gp = temp_dir("studybot-goals-") / "goals_os.json"
        gp.write_text(json.dumps({
            "subject": "操作系统", "exam_date": "2026-12-18",
            "goals": [{"id": "g01", "ch": "进程", "title": "进程与线程的区别",
                       "p": "core", "m": 30, "d": [], "cp": ["能说清区别"]}],
        }, ensure_ascii=False), encoding="utf-8")
        n2 = db.import_goals(gp, course="os")
        check("远端按课程导入", n2 == 1, str(n2))
        check("远端按课程统计", db.stats("os")["total_goals"] == 1, str(db.stats("os")))
        r3 = db.action("replan", course="os")
        check("远端按课程重排", r3["inserted"] >= 1, str(r3.get("inserted")))
        check("远端按课程过滤任务",
              all((t.get("course") or "ds") == "os" for t in db.today_tasks("os")),
              str(len(db.today_tasks("os"))))
        check("状态里带课程", len(db.state()["courses"]) >= 2, str(len(db.state()["courses"])))

        # 待确认队列（手机版 P0）：手机上报的分歧，远端列表 + 处理写库
        t25 = d.today_tasks()[0]
        d.set_task_state(t25["id"], "done")          # 模拟手机判完成
        rid = d.review_add(kind="task_state", task_id=t25["id"], goal_id=t25["goal_id"],
                           day=dbmod.today_s(), phone_state="done", server_state="todo",
                           reason="远端测试：手机判完成、电脑按规则判未完成")
        check("远端待确认列表", [x["id"] for x in db.review_items()] == [rid],
              str([x["id"] for x in db.review_items()]))
        # 计数走 /api/state 的缓存（1.5 秒）；这里刚在服务器端直接插了一条，先失效再读
        db.invalidate()
        check("远端待确认计数", db.review_pending_count() == 1, str(db.review_pending_count()))
        row = db.review_resolve(rid, "follow_server")
        check("远端处理：听电脑 → 卡片改回未完成",
              row["resolution"] == "follow_server" and row["resolved_at"]
              and db.task(t25["id"])["state"] == "todo", str(row))
        check("处理后不再挂待确认",
              db.review_pending_count() == 0 and not db.review_items(),
              str(db.review_pending_count()))

        # 手机下行快照（P1）：四页数据 + 块指纹/增量 + rev 前进 + 空库
        snap = db.call("GET", "/api/mobile/snapshot")
        check("快照四页数据齐全",
              len(snap["goals"]) >= 31 and bool(snap["courses"]) and bool(snap["tasks"])
              and "quiz_stats" in snap,
              f'{len(snap["goals"])} / {len(snap["courses"])} / {len(snap["tasks"])}')
        blocks = snap.get("blocks") or []
        check("快照带资料块和指纹",
              bool(blocks) and len(snap["blocks_rev"]) == 16
              and all(len(b["id"]) == 16 and len(b["fp"]) == 16 for b in blocks),
              f'{len(blocks)} 块 / {snap["blocks_rev"]}')
        snap2 = db.call("GET", "/api/mobile/snapshot",
                        query={"rev": snap["rev"], "blocks_rev": snap["blocks_rev"]})
        check("块没变就不重传", "blocks" not in snap2, str(sorted(snap2.keys())))
        check("rev 不变时不动", snap2["rev"] == snap["rev"],
              f'{snap["rev"]} → {snap2["rev"]}')

        t_snap = d.today_tasks()[0]
        d.set_task_state(t_snap["id"], "skipped")
        snap3 = db.call("GET", "/api/mobile/snapshot",
                        query={"rev": snap["rev"], "blocks_rev": snap["blocks_rev"]})
        check("数据变了 rev 前进且带新状态",
              snap3["rev"] > snap["rev"]
              and any(x["id"] == t_snap["id"] and x["state"] == "skipped"
                      for x in snap3["tasks"]),
              f'{snap["rev"]} → {snap3["rev"]}')

        (ext / "快照测试.txt").write_text(
            "二叉树是每个节点最多有两个子节点的树结构，左子树和右子树有顺序之分。"
            "遍历分前序、中序、后序三种，层序遍历用队列。" * 10, encoding="utf-8")
        import retrieval
        retrieval._BLOCKS = None
        snap4 = db.call("GET", "/api/mobile/snapshot",
                        query={"rev": snap3["rev"], "blocks_rev": snap["blocks_rev"]})
        check("块变了才重推（blocks_rev 变）",
              snap4["blocks_rev"] != snap["blocks_rev"] and bool(snap4.get("blocks")),
              f'{snap["blocks_rev"]} → {snap4["blocks_rev"]}')

        from datetime import date as _d, timedelta as _td
        far = (_d.today() + _td(days=40)).isoformat()
        engine.add_task(d, far, snap["goals"][0]["id"], "很久以后", "learn", 10)
        snap5 = db.call("GET", "/api/mobile/snapshot")
        edge = (_d.today() + _td(days=14)).isoformat()
        check("只推过去7天+未来14天",
              all(t["date"] <= edge for t in snap5["tasks"])
              and not any(t["date"] == far for t in snap5["tasks"]), far)

        empty = dbmod.DB(tmp / "empty.db")
        empty.init()
        snap_empty = api._mobile_snapshot(empty, {})
        check("空库快照不崩",
              snap_empty["goals"] == [] and snap_empty["tasks"] == []
              and snap_empty["quiz_stats"] == [] and snap_empty["rev"] > 0,
              str(snap_empty["rev"]))

        # 手机上行事件（P3）：uid 幂等 / 判完成 / 分歧进待确认
        todo = [x for x in d.today_tasks() if x["state"] == "todo"]
        t_ev = todo[0]
        ev_done = {"uid": "ev-1", "kind": "task_state", "task_id": t_ev["id"],
                   "state": "done", "quiz_passed": 2}
        r_ev = db.mobile_events([ev_done], device="test-phone")
        check("手机事件：对 2 题判完成", r_ev["accepted"] == 1
              and d.task(t_ev["id"])["state"] == "done", str(r_ev))
        r_dup = db.mobile_events([ev_done], device="test-phone")
        check("同一 uid 重传不重复写", r_dup["accepted"] == 0 and d.event_count() == 1,
              f'{r_dup} / {d.event_count()}')

        t_ev2 = [x for x in d.today_tasks() if x["state"] == "todo"][0]
        ev_weak = {"uid": "ev-2", "kind": "task_state", "task_id": t_ev2["id"],
                   "state": "done", "quiz_passed": 1}
        r_weak = db.mobile_events([ev_weak], device="test-phone")
        pending = d.review_items()
        check("手机判完成但只对 1 题 → 进待确认",
              r_weak["needs_review"] == 1 and any(x["id"] == r_weak["review_ids"][0]
                                                  for x in pending)
              and d.task(t_ev2["id"])["state"] == "todo", str(r_weak))

        t_ev3 = [x for x in d.today_tasks() if x["state"] == "todo"][0]
        d.set_task_state(t_ev3["id"], "skipped")
        ev_conflict = {"uid": "ev-3", "kind": "task_state", "task_id": t_ev3["id"],
                       "state": "done", "quiz_passed": 2}
        r_conf = db.mobile_events([ev_conflict], device="test-phone")
        check("电脑已跳过 → 手机报完成进待确认",
              r_conf["needs_review"] == 1 and d.task(t_ev3["id"])["state"] == "skipped",
              str(r_conf))

        before_msgs = len(d.conv_messages(chat.conversation(d, t_ev)["id"], 50))
        r_msg = db.mobile_events([{"uid": "ev-4", "kind": "message",
                                   "task_id": t_ev["id"], "text": "手机上的一句"}],
                                 device="test-phone")
        conv_msg = d.conv_messages(chat.conversation(d, t_ev)["id"], 50)
        check("手机上行的消息落库",
              r_msg["accepted"] == 1 and len(conv_msg) == before_msgs + 1
              and conv_msg[-1]["text"] == "手机上的一句", str(r_msg))

        # 自由聊天：没有 task_id 的消息全部并到同一条 free 会话（不再每条新建）
        before_free = int((d.q1("SELECT COUNT(*) AS n FROM conversations WHERE kind='free'")
                           or {}).get("n") or 0)
        db.mobile_events([{"uid": "ev-free-1", "kind": "message", "task_id": None,
                           "text": "今天学什么"}], device="test-phone")
        db.mobile_events([{"uid": "ev-free-2", "kind": "message",
                           "text": "先学排序"}], device="test-phone")
        after_free = int((d.q1("SELECT COUNT(*) AS n FROM conversations WHERE kind='free'")
                          or {}).get("n") or 0)
        free = d.q1("SELECT * FROM conversations WHERE kind='free' ORDER BY id LIMIT 1")
        free_msgs = d.conv_messages(int(free["id"]), 50) if free else []
        check("自由消息不再每条新建会话",
              after_free == max(1, before_free)
              and any(m["text"] == "先学排序" for m in free_msgs),
              f"{before_free} -> {after_free} / {len(free_msgs)} 条")

        # 手机内冲突裁定（P7）：服务器没有 arbitrations 表，落 settings 的 arb:<uid>
        arb_ev = {"uid": "ev-arb-1", "kind": "arbitration", "course": "ds", "goal_id": gid,
                  "concept": "二叉树中序遍历的顺序",
                  "material_claim": "中序是左根右",
                  "material_src": "数据结构 7 排序算法", "note_id": "note-1",
                  "note_claim": "中序是根左右", "verdict": "material",
                  "decided_at": dbmod.now_s(), "decided_by": "user", "hits": 1}
        r_arb = db.mobile_events([arb_ev], device="test-phone")
        stored = json.loads(d.get("arb:ev-arb-1") or "{}")
        check("手机裁定落库（settings arb:<uid>）",
              r_arb["accepted"] == 1 and stored.get("verdict") == "material"
              and stored.get("concept") == arb_ev["concept"]
              and stored.get("note_claim") == arb_ev["note_claim"]
              and int(stored.get("hits") or 0) == 1
              and any("手机裁定" in x["text"] for x in d.logs(20)),
              str(stored)[:90])
        r_arb2 = db.mobile_events([arb_ev], device="test-phone")
        n_arb = d.q1("SELECT COUNT(*) AS n FROM settings WHERE `key` LIKE 'arb:%'")["n"]
        check("同一 uid 的裁定不重复写",
              r_arb2["accepted"] == 0 and int(n_arb) == 1, f'{r_arb2} / {n_arb}')

        try:
            bad = RemoteDB(f"http://127.0.0.1:{port}", "wrong", timeout=10)
            bad.call("GET", "/api/state")
            check("错 token 被拒", False, "居然通了")
        except RemoteError:
            check("错 token 被拒", True)

        # P4 手机重排：手机自己重排后只传建议（进待确认），不直接覆盖服务器 tasks
        today = dbmod.today_s()
        tomorrow = (_d.today() + _td(days=1)).isoformat()
        pick = [g for g in d.goals(include_done=False) if g["ch"] != "应试"][:2]
        replan_tasks = [
            {"day": tomorrow, "goal_id": pick[0]["id"], "title": "重排卡一",
             "kind": "learn", "minutes": 40},
            {"day": tomorrow, "goal_id": pick[1]["id"], "title": "重排卡二",
             "kind": "review", "minutes": 15},
        ]
        replan_note = "落后 2 天，剩 300 分钟摊到 50 天，每天约 6 分钟；考核日不变"
        before_ids = {t["id"] for t in d.tasks_between(today, "9999-12-31")}
        r_rep = db.mobile_events(
            [{"uid": "ev-replan-1", "kind": "replan",
              "note": replan_note, "tasks": replan_tasks}], device="test-phone")
        rid_rep = int(r_rep["review_ids"][0])
        check("手机重排只进待确认（kind=replan）",
              r_rep["accepted"] == 1 and r_rep["needs_review"] == 1
              and d.q1("SELECT * FROM review_queue WHERE id=?", (rid_rep,))["kind"] == "replan"
              and d.q1("SELECT * FROM review_queue WHERE id=?", (rid_rep,))["reason"] == replan_note,
              str(r_rep))
        check("重排建议不改服务器 tasks",
              {t["id"] for t in d.tasks_between(today, "9999-12-31")} == before_ids,
              str(len(before_ids)))

        db.review_resolve(rid_rep, "keep_phone")
        after = [t for t in d.tasks_between(today, "9999-12-31") if t["state"] == "todo"]
        check("保留手机 → 按 payload 重建服务器 tasks",
              len(after) == 2 and {t["title"] for t in after} == {"重排卡一", "重排卡二"}
              and all(t["state"] == "todo" and t["date"] == tomorrow
                      and t["course"] == pick[0]["course"] for t in after)
              and all(t["goal_id"] in (pick[0]["id"], pick[1]["id"]) for t in after),
              str([(t["title"], t["date"], t["course"], t["state"]) for t in after]))

        # 同一份 payload 再来一次（新 uid）：可重复应用，不崩
        r_rep2 = db.mobile_events(
            [{"uid": "ev-replan-2", "kind": "replan",
              "note": replan_note, "tasks": replan_tasks}], device="test-phone")
        row_rep2 = db.review_resolve(int(r_rep2["review_ids"][0]), "keep_phone")
        again = sorted(t["title"] for t in d.tasks_between(today, "9999-12-31")
                       if t["state"] == "todo")
        check("重排可重复应用（幂等，不崩）",
              r_rep2["needs_review"] == 1 and bool(row_rep2["resolved_at"])
              and again == ["重排卡一", "重排卡二"],
              f'{r_rep2} / {again}')
    finally:
        srv.shutdown()
        srv.server_close()

    print("\n" + ("全部通过" if not fails else f"失败 {len(fails)} 项：{fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
