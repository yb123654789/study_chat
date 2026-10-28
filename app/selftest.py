#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线自检：不碰网络、不开窗口，验证建库 / 排计划 / 出题 / 答题判分 / 记账。

    py app/selftest.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
BASE = APP.parent
for p in (str(APP), str(BASE / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import bot as botmod          # noqa: E402
import chat as chatmod        # noqa: E402
import db as dbmod            # noqa: E402
import engine                 # noqa: E402
import llm                    # noqa: E402
import paths                  # noqa: E402
import planner                # noqa: E402

REPO_GOALS = BASE / "data" / "goals.json"      # 读真实的 goals，但不碰真实数据目录

OK, BAD = "  ✓", "  ✗"
fails: list[str] = []


def temp_dir(prefix: str) -> Path:
    """测试用临时目录。

    不用 tempfile.mkdtemp：它按 0700 建目录，Windows 上（尤其 DSH 沙箱里）
    这个目录连自己的子目录都建不进去（WinError 5），paths.ensure() 立刻会炸。
    """
    p = Path(tempfile.gettempdir()) / f"{prefix}{os.getpid()}-{time.time_ns()}"
    p.mkdir(parents=True, exist_ok=False)
    return p


def check(name: str, cond: bool, detail: str = "") -> None:
    print((OK if cond else BAD), name, ("" if cond else f"  → {detail}"))
    if not cond:
        fails.append(name)


# ---------------------------------------------------------------- 假模型
QUIZ_JSON = json.dumps({"questions": [
    {"type": "choice", "stem": "Q1 哪个是对的？", "options": ["甲", "乙", "丙", "丁"], "answer": 1},
    {"type": "choice", "stem": "Q2 复杂度是？", "options": ["O(1)", "O(n)", "O(n²)", "O(log n)"],
     "answer": 1},
    {"type": "short", "stem": "用两句话解释一下。", "rubric": ["要点一", "要点二", "要点三"]},
]}, ensure_ascii=False)


def fake_chat(messages, db=None, max_tokens=800, temperature=0.7, timeout=90):
    sys_prompt = messages[0]["content"]
    if "出题人" in sys_prompt:
        return QUIZ_JSON
    if "结构化专家" in sys_prompt:          # 拆目标
        return json.dumps({"goals": [
            {"id": "n1", "ch": "测试章", "title": "新资料里的第一个目标", "priority": "core",
             "est_minutes": 30, "deps": [], "checkpoints": ["能说清定义"]},
            {"id": "n2", "ch": "测试章", "title": "新资料里的第二个目标", "priority": "support",
             "est_minutes": 20, "deps": ["n1"], "checkpoints": ["能举一个例子"]},
        ]}, ensure_ascii=False)
    if "阅卷人" in sys_prompt:
        return json.dumps({"pass": True, "covered": ["要点一"], "missed": [],
                           "feedback": "答到了"}, ensure_ascii=False)
    return "讲解：先看定义，再看代价。"


def fake_tools(messages, tools=None, db=None, max_tokens=800, temperature=0.7, timeout=90):
    """带练用的假模型：说「测」就调 start_quiz，其余直接答。"""
    if any(m.get("role") == "tool" for m in messages):
        return {"text": "好，开始小测。", "tool_calls": [], "finish": "stop",
                "tokens": 20, "usage": {}, "model": "fake"}
    last = (messages[-1].get("content") or "") if messages else ""
    if "测" in last:
        return {"text": "", "finish": "tool_calls", "tokens": 30, "usage": {},
                "model": "fake",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "start_quiz", "arguments": "{}"}}]}
    return {"text": "先看定义：ADT 只规定操作和语义，不规定实现。", "tool_calls": [],
            "finish": "stop", "tokens": 40, "usage": {}, "model": "fake"}


def run() -> int:
    tmp = temp_dir("studybot-selftest-")
    paths.use_root(tmp, save=False)          # 测试数据单独一个目录，不碰真实库
    d = dbmod.DB(paths.db_file())
    d.init()
    print(f"\n临时数据目录：{tmp}\n")

    # 1. 导入目标
    n = d.import_goals(REPO_GOALS)
    check("导入目标", n > 40, f"只导入 {n} 个")
    check("考核日", d.get("exam_date") == "2027-01-04", d.get("exam_date"))

    # 2. 排计划
    r = engine.regenerate(d)
    check("生成计划", r["inserted"] > 50, str(r))
    check("计划说明非空", bool(r["note"]), r["note"])
    t_today = d.today_tasks()
    check("今天有任务", len(t_today) > 0, f"{len(t_today)} 张")

    # 每天学多少不是写死的：计划里每天的 cap 由「剩余量 ÷ 剩余天数」算，
    # 且任何一天都不能超过当天 cap；截止日越近，日均越大。
    data = json.loads(REPO_GOALS.read_text(encoding="utf-8-sig"))
    today = dt.date.today()
    sim = planner.schedule(data["goals"], today, dt.date(2027, 1, 4))
    over = [(str(x["date"]), sum(t["min"] for t in x["tasks"]), x["cap"])
            for x in sim["plan"] if sum(t["min"] for t in x["tasks"]) > x["cap"]]
    check("每天不超当天上限", not over, str(over[:3]))
    # 卡数不再有「一天最多 3 张」的硬顶：赶进度时按分钟摊，一天可以多排
    tight = planner.schedule(data["goals"], today, today + dt.timedelta(days=10))
    most = max(len(x["tasks"]) for x in tight["plan"])
    check("紧截止日一天能排超过 3 张", most > 3, f"最多 {most} 张")
    near = planner.schedule(data["goals"], today, dt.date(2026, 12, 10))
    check("截止日越近日均越大", near["daily_avg"] > sim["daily_avg"],
          f"{near['daily_avg']} vs {sim['daily_avg']}")
    lagged = planner.schedule(data["goals"], today, dt.date(2027, 1, 4), lost_days=14)
    check("落后后重排每天加量", lagged["daily_avg"] > sim["daily_avg"],
          f"{lagged['daily_avg']} vs {sim['daily_avg']}")
    capped = planner.schedule(data["goals"], today, dt.date(2027, 1, 4), day_cap=50)
    check("每日上限守住并报排不下",
          not [1 for x in capped["plan"] if sum(t["min"] for t in x["tasks"]) > 50]
          and bool(capped["uncovered"]), str(len(capped["uncovered"])))

    # 3. 重排幂等（不产生重复卡）
    before = len(d.q("SELECT * FROM tasks"))
    engine.regenerate(d)
    after = len(d.q("SELECT * FROM tasks"))
    check("重排不重复", before == after, f"{before} → {after}")

    # 重排会重建未来任务，重新取一次今天的卡
    t_today = d.today_tasks()

    # 4. 出题（打桩，不走网络）
    llm.chat = fake_chat
    gid = t_today[0]["goal_id"]
    qs = engine.ensure_quiz(d, gid)
    check("出题 3 道", len(qs) == 3, str(len(qs)))
    check("题里有 2 选择 + 1 简答",
          sum(1 for q in qs if q["type"] == "choice") == 2, str([q["type"] for q in qs]))
    again = engine.ensure_quiz(d, gid)
    check("同一批题不重复生成", [q["id"] for q in again] == [q["id"] for q in qs])

    # 5. 走一遍完整答题：/start → 点卡 → 出题 → 2 对 1 错
    client = botmod.FakeClient()
    b = botmod.Bot(d, client)
    task_id = t_today[0]["id"]

    b.handle_update({"message": {"chat": {"id": 42}, "text": "/start"}})
    check("绑定会话", d.get("chat_id") == "42", d.get("chat_id"))
    check("/start 有按钮", bool(client.last().get("buttons")), str(client.last()))

    b.handle_update({"callback_query": {"id": "1", "data": f"t:{task_id}",
                                        "message": {"chat": {"id": 42}}}})
    st = d.bot_state("42")
    check("进入学习态", st.get("mode") == "learn" and st.get("task_id") == task_id, str(st))

    b.handle_update({"message": {"chat": {"id": 42}, "text": "出题"}})
    st = d.bot_state("42")
    check("进入答题态", st.get("mode") == "quiz" and len(st.get("ids", [])) == 3, str(st))

    # 第 1 题：选对
    q1 = d.quiz(st["ids"][0])
    right1 = int(q1["answer"])
    b.handle_update({"callback_query": {"id": "2", "data": f"a:{q1['id']}:{right1}",
                                        "message": {"chat": {"id": 42}}}})
    check("第 1 题判对", d.bot_state("42").get("right") == 1, str(d.bot_state("42")))

    # 第 2 题：选错
    st = d.bot_state("42")
    q2 = d.quiz(st["ids"][1])
    wrong = (int(q2["answer"]) + 1) % len(q2["options"])
    b.handle_update({"callback_query": {"id": "3", "data": f"a:{q2['id']}:{wrong}",
                                        "message": {"chat": {"id": 42}}}})
    check("第 2 题判错", d.bot_state("42").get("right") == 1, str(d.bot_state("42")))

    # 第 3 题：简答
    st = d.bot_state("42")
    q3 = d.quiz(st["ids"][2])
    check("简答要求 force_reply", client.last().get("force_reply") is True, str(client.last()))
    b.handle_update({"message": {"chat": {"id": 42}, "text": "要点一和要点二都写了，所以是对的。"}})
    left = d.bot_state("42")
    check("答完清状态", left == {}, str(left))
    t = d.task(task_id)
    check("2 对 → 任务完成", t["state"] == "done", str(t["state"]))
    check("答对题数记账", d.bot_state("42") == {} and
          len(d.q("SELECT * FROM attempts WHERE task_id=?", (task_id,))) == 3,
          str(len(d.q("SELECT * FROM attempts WHERE task_id=?", (task_id,)))))

    # 6. 全错 → 不过，且不标记完成
    tasks = [x for x in d.today_tasks() if x["state"] == "todo"]
    if not tasks:
        engine.regenerate(d)
        tasks = [x for x in d.today_tasks() if x["state"] == "todo"]
    t2 = tasks[0]
    b.handle_update({"callback_query": {"id": "4", "data": f"t:{t2['id']}",
                                        "message": {"chat": {"id": 42}}}})
    b.handle_update({"message": {"chat": {"id": 42}, "text": "出题"}})
    for i, qid in enumerate(d.bot_state("42")["ids"]):
        q = d.quiz(qid)
        if q["type"] == "choice":
            bad = (int(q["answer"]) + 1) % len(q["options"])
            b.handle_update({"callback_query": {"id": f"x{i}", "data": f"a:{q['id']}:{bad}",
                                                "message": {"chat": {"id": 42}}}})
        else:
            llm.chat = lambda *a, **k: json.dumps({"pass": False, "covered": [], "missed": ["要点"],
                                                   "feedback": "没答到"})
            b.handle_update({"message": {"chat": {"id": 42}, "text": "不知道"}})
    check("3 错 → 任务仍是待做", d.task(t2["id"])["state"] == "todo", str(d.task(t2["id"])["state"]))
    check("没过时给「换套题」按钮",
          any("换套题" in (btn[0] if isinstance(btn, tuple) else str(btn))
              for row in (client.last().get("buttons") or []) for btn in row),
          str(client.last()))

    # 7. 模型挂了 → 降级
    llm.chat = lambda *a, **k: (_ for _ in ()).throw(llm.LLMError("假装断网"))
    fb = engine.ensure_quiz(d, gid, force_new=True)
    check("出题失败降级为简答", len(fb) == 3 and all(q["type"] == "short" for q in fb),
          str([q["type"] for q in fb]))

    # 8. 大白话路由（用户不会记斜杠命令）
    llm.chat = fake_chat
    b.handle_update({"message": {"chat": {"id": 42}, "text": "/reset"}})
    b.handle_update({"callback_query": {"id": "r1", "data": f"t:{t2['id']}",
                                        "message": {"chat": {"id": 42}}}})
    client.sent.clear()
    b.handle_update({"message": {"chat": {"id": 42}, "text": "今天要学什么"}})
    check("「今天要学什么」→ 今日任务", "📅" in client.last()["text"],
          client.last()["text"][:40])
    check("学了一半也能被叫回来", d.bot_state("42").get("mode") == "learn",
          str(d.bot_state("42"))[:60])
    client.sent.clear()
    b.handle_update({"message": {"chat": {"id": 42}, "text": "进度怎么样了"}})
    check("「进度」→ 统计", "距考" in client.last()["text"] and "目标" in client.last()["text"],
          client.last()["text"][:40])
    client.sent.clear()
    b.handle_update({"message": {"chat": {"id": 42}, "text": "我的计划"}})
    check("「计划」→ 计划页", "计划" in client.last()["text"][:20], client.last()["text"][:40])

    # 选择题阶段打字，应该提示点按钮，不是当答案吞掉
    b.handle_update({"message": {"chat": {"id": 42}, "text": "出题"}})
    client.sent.clear()
    b.handle_update({"message": {"chat": {"id": 42}, "text": "这个选项我不确定"}})
    check("选择题阶段打字有提示", "按钮" in client.last()["text"], client.last()["text"][:40])

    # 9. 答错要讲「为什么不对」，不是只报答案
    client.sent.clear()
    st = d.bot_state("42")
    q = d.quiz(st["ids"][0])
    bad = (int(q["answer"]) + 1) % len(q["options"])
    b.handle_update({"callback_query": {"id": "w1", "data": f"a:{q['id']}:{bad}",
                                        "message": {"chat": {"id": 42}}}})
    fb = client.sent[0]["text"] if client.sent else ""
    check("答错给出正确答案", "正确答案" in fb, fb[:50])
    check("答错附一句讲解", "讲解" in fb or "定义" in fb, fb[:80])

    # 讲解模型挂掉时，不能把整条反馈也搞没
    llm.chat = lambda *a, **k: (_ for _ in ()).throw(llm.LLMError("断网"))
    st = d.bot_state("42")
    q2 = d.quiz(st["ids"][st["idx"]]) if st.get("idx", 3) < len(st.get("ids", [])) else None
    if q2 and q2["type"] == "choice":
        client.sent.clear()
        bad = (int(q2["answer"]) + 1) % len(q2["options"])
        b.handle_update({"callback_query": {"id": "w2", "data": f"a:{q2['id']}:{bad}",
                                            "message": {"chat": {"id": 42}}}})
        check("讲解失败仍有反馈", bool(client.sent and "正确答案" in client.sent[0]["text"]),
              str(client.sent[:1])[:80])
    llm.chat = fake_chat
    b.handle_update({"message": {"chat": {"id": 42}, "text": "/reset"}})

    # 10. 答题途中重排（卡被删、id 变了），结果仍要落到今天这张卡上
    b.handle_update({"message": {"chat": {"id": 42}, "text": "/reset"}})
    engine.regenerate(d)
    todo = [t for t in d.today_tasks() if t["state"] == "todo"]
    t3 = todo[0]
    b.handle_update({"callback_query": {"id": "y0", "data": f"t:{t3['id']}",
                                        "message": {"chat": {"id": 42}}}})
    b.handle_update({"message": {"chat": {"id": 42}, "text": "出题"}})
    gone = d.bot_state("42")["task_id"]
    d.x("DELETE FROM tasks WHERE id=?", (gone,))      # 模拟旧版重排把卡删掉
    check("卡确实没了", d.task(gone) is None, str(gone))
    for qid in d.bot_state("42")["ids"]:
        q = d.quiz(qid)
        if q["type"] == "choice":
            b.handle_update({"callback_query": {"id": f"y{q['id']}",
                                                "data": f"a:{q['id']}:{q['answer']}",
                                                "message": {"chat": {"id": 42}}}})
        else:
            b.handle_update({"message": {"chat": {"id": 42}, "text": "要点一、要点二都写到了。"}})
    check("重排后仍记为完成", t3["goal_id"] in d.done_goal_ids(), str(t3["goal_id"]))

    # 12. 带练引擎：会话落库 / 状态条 / 工具调用（桌面端和 Telegram 共用）
    llm.chat_with_tools = fake_tools
    llm.chat = fake_chat
    b.handle_update({"message": {"chat": {"id": 42}, "text": "/reset"}})
    todos = [t for t in d.today_tasks() if t["state"] == "todo"]
    if not todos:
        engine.regenerate(d)
        todos = [t for t in d.today_tasks() if t["state"] == "todo"]
    t4 = todos[0]
    b.handle_update({"callback_query": {"id": "z0", "data": f"t:{t4['id']}",
                                        "message": {"chat": {"id": 42}}}})
    conv = chatmod.conversation(d, t4)
    check("会话建好", conv["task_id"] == t4["id"], str(conv))
    check("同一张卡只有一条会话", chatmod.conversation(d, t4)["id"] == conv["id"],
          f"{conv['id']} vs {chatmod.conversation(d, t4)['id']}")
    bar = chatmod.state_bar(d, t4)
    check("状态条是代码算的", "距考核" in bar and str(d.stats()["days_left"]) in bar, bar)

    b.handle_update({"message": {"chat": {"id": 42}, "text": "这节到底在讲什么？"}})
    msgs = d.conv_messages(conv["id"], 50)
    check("提问和回答都落库", len(msgs) >= 2 and msgs[-2]["role"] == "me"
          and msgs[-1]["role"] == "bot", str([m["role"] for m in msgs][-4:]))
    check("token 记在消息上", d.conv_tokens(conv["id"]) > 0, str(d.conv_tokens(conv["id"])))
    check("历史按时间正序", [m["id"] for m in msgs] == sorted(m["id"] for m in msgs))
    check("历史能截断", len(d.conv_messages(conv["id"], 2)) == 2)

    # 模型主动调工具：说「测一下」不该由模型自己出题，而是走 start_quiz
    client.sent.clear()
    b.handle_update({"message": {"chat": {"id": 42}, "text": "那我测一下"}})
    st = d.bot_state("42")
    check("工具调用 → 进入答题态", st.get("mode") == "quiz" and len(st.get("ids", [])) == 3,
          str(st)[:80])
    check("工具调用有回执", any(m["role"] == "tool" for m in d.conv_messages(conv["id"], 20)),
          str([m["role"] for m in d.conv_messages(conv["id"], 20)]))
    b.handle_update({"message": {"chat": {"id": 42}, "text": "/reset"}})

    # 答题中插问：不能让提问被当成答案判掉
    d.set_bot_state("42", {"mode": "quiz", "task_id": t4["id"], "goal_id": t4["goal_id"],
                           "ids": [1, 2, 3], "idx": 2, "right": 1, "total": 3,
                           "awaiting": 3, "batch": 1})
    n_before = len(d.q("SELECT * FROM attempts WHERE task_id=?", (t4["id"],)))
    client.sent.clear()
    b.handle_update({"message": {"chat": {"id": 42}, "text": "什么是 ADT？"}})
    n_after = len(d.q("SELECT * FROM attempts WHERE task_id=?", (t4["id"],)))
    check("答题中插问不算作答", n_before == n_after, f"{n_before} → {n_after}")
    check("插问后把原题递回去",
          bool(client.sent) and "还在等你" in client.sent[-1]["text"],
          str(client.sent[-1:])[:100])
    check("插问后仍在等这道题", d.bot_state("42").get("awaiting") == 3,
          str(d.bot_state("42"))[:80])
    b.handle_update({"message": {"chat": {"id": 42}, "text": "/reset"}})

    # 桌面端和 Telegram 读的是同一条会话
    check("两端共用会话", chatmod.history(d, conv, 50)[0]["conv_id"] == conv["id"],
          str(conv["id"]))

    # 13. 推送窗口 + 晚间提醒条件
    from service import Service
    check("07:30 在早间窗口内", Service._in_window("07:30", "07:00", 4))
    check("22:30 不在早间窗口内", not Service._in_window("22:30", "07:00", 4))
    check("21:40 在晚间窗口内", Service._in_window("21:40", "21:30", 2))

    d.set("chat_id", "42")
    b2 = botmod.Bot(d, client)
    for x in d.today_tasks():
        d.set_task_state(x["id"], "done")
    client.sent.clear()
    check("全做完不催", b2.push_reminder() is False and not client.sent,
          str(client.sent[:1]))
    if d.today_tasks():
        td = d.today_tasks()[0]
        d.set_task_state(td["id"], "todo")
        client.sent.clear()
        ok = b2.push_reminder()
        check("还剩没做就催", ok is True and bool(client.sent) and "还剩" in client.sent[0]["text"],
              str(client.sent[:1])[:80])
        d.set_task_state(td["id"], "done")

    # 11. 统计
    s = d.stats()
    check("统计有数", s["total_goals"] > 40 and s["days_left"] > 0, str(s))
    check("今日计数", s["today_total"] >= 1, str(s["today_total"]))

    # 14. 落后 → 自动重排（欠的卡并回队列，牺牲写进日志和当天推送）
    from datetime import date as _date, timedelta as _td
    yday = (_date.fromisoformat(dbmod.today_s()) - _td(days=1)).isoformat()
    engine.add_task(d, yday, "g01", "欠下的卡", "learn", 40)
    check("造出落后", chatmod.lag_days(d) >= 1, str(chatmod.lag_days(d)))
    r14 = engine.auto_replan(d)
    check("落后触发自动重排", bool(r14) and r14["lag"] >= 1, str(r14))
    check("重排后落后归零", chatmod.lag_days(d) == 0, str(chatmod.lag_days(d)))
    brief = d.get("replan_brief", "")
    check("重排写清牺牲了什么", "落后" in brief and "复习卡" in brief, brief)
    check("当天推送带重排说明", "计划重排" in engine.today_digest(d),
          engine.today_digest(d)[-90:])
    check("同一天不重复重排", engine.auto_replan(d) is None)
    check("重排写进日志", any("自动重排" in (e.get("text") or "") for e in d.logs(30)),
          str([e.get("text") for e in d.logs(5)]))

    # 15. 错题回炉：没过 → 明天重做；连续两次 → 停止换题，改回炉
    gt = "g06"
    d.x("DELETE FROM attempts WHERE goal_id=?", (gt,))
    d.x("DELETE FROM tasks WHERE goal_id=? AND date>?", (gt, dbmod.today_s()))

    def fake_fail_batch(gid: str) -> None:
        no = d.next_batch_no(gid)
        for i in range(3):
            qid = d.add_quiz(gid, no, {"type": "choice", "stem": f"替身题 {i}",
                                       "options": ["甲", "乙"], "answer": 0})
            d.add_attempt(None, gid, qid, "1", 0, "")

    fake_fail_batch(gt)
    check("连续没过计数", d.fail_streak(gt) == 1, str(d.fail_streak(gt)))
    card = {"id": 0, "goal_id": gt, "title": "测试卡", "minutes": 30}
    i15 = engine.requeue_after_fail(d, card, 1, 3)
    check("没过 → 明天排错题重做", bool(i15["added"]) and i15["kind"] == "review", str(i15))
    tomorrow = [t for t in d.tasks_between(i15["date"], i15["date"]) if t["goal_id"] == gt]
    check("回炉卡真的进了库", len(tomorrow) == 1 and "错题重做" in tomorrow[0]["title"],
          str(tomorrow))
    fake_fail_batch(gt)
    check("连续两次没过", d.fail_streak(gt) == 2, str(d.fail_streak(gt)))
    check("连续两次不再换题", engine.regen_allowed(d, gt) is False, str(d.fail_streak(gt)))
    d.x("DELETE FROM tasks WHERE date=? AND goal_id=?", (i15["date"], gt))
    i15b = engine.requeue_after_fail(d, card, 1, 3)
    check("两次没过改成回炉学", i15b["added"] and i15b["kind"] == "learn", str(i15b))

    # 16. 带练上下文按预算截断（12 条挡不住长回答）
    rows16 = [{"role": "me" if i % 2 else "bot", "text": f"第{i}轮" + "字" * 3000}
              for i in range(10)]
    kept = chatmod.trim_history(rows16, budget=6000)
    check("历史按字数截断", 0 < len(kept) < 10, str(len(kept)))
    check("截断后不超预算", sum(len(m["text"]) for m in kept) <= 6000 + len(rows16[-1]["text"]),
          str(sum(len(m["text"]) for m in kept)))
    check("截断保留最新的", kept[-1] is rows16[-1], str(kept[-1]["text"][:6]) if kept else "")

    # 17. 多课程：建课 → 导入 → 各排各的
    math_goals = tmp / "goals_math.json"
    math_goals.write_text(json.dumps({
        "subject": "高等数学", "exam_date": "2026-12-20",
        "goals": [
            {"id": "g01", "ch": "极限", "title": "极限的定义与 ε-δ 写法", "p": "core",
             "m": 30, "d": [], "cp": ["能写出 ε-δ 定义"]},
            {"id": "g02", "ch": "导数", "title": "导数定义与求导法则", "p": "core",
             "m": 25, "d": ["g01"], "cp": ["会求导"]},
        ]}, ensure_ascii=False), encoding="utf-8")
    d.save_course("math", name="高等数学", exam_date="2026-12-20",
                  weekend_ratio=150, day_cap=0)
    mc = d.course("math") or {}
    check("课程存周末系数/每日上限", int(mc.get("weekend_ratio") or 0) == 150
          and int(mc.get("day_cap") or 0) == 0, str(dict(mc)))
    check("课程表不再存固定分钟", "weekday_min" not in mc and "weekend_min" not in mc,
          str(sorted(mc)))
    n17 = d.import_goals(math_goals, course="math")
    check("新课程导入目标", n17 == 2, str(n17))
    mg = [g["id"] for g in d.goals(course="math")]
    check("撞车的 id 自动改名", len(mg) == 2 and all(x.startswith("math-") for x in mg), str(mg))
    check("原课程目标没被覆盖", d.goal("g01")["course"] == "ds", d.goal("g01")["course"])
    r17 = engine.regenerate(d, course="math")
    check("新课程单独排了计划", r17["inserted"] >= 2, str(r17["inserted"]))
    mt = d.tasks_between("0000-01-01", "9999-12-31", course="math")
    check("新课程的卡带课程标记", bool(mt) and all(t["course"] == "math" for t in mt),
          str(len(mt)))
    check("按课程过滤今日任务",
          all(t["course"] == "math" for t in d.today_tasks("math")), str(len(d.today_tasks("math"))))
    check("统计按课程分开", d.stats("math")["total_goals"] == 2, str(d.stats("math")))
    dig = engine.today_digest(d)
    check("推送按课程分组", "【高等数学】" in dig and "【数据结构】" in dig, dig[:120])
    check("资料按目录归课", engine.course_of_path("materials/math/x.pdf") == "math"
          and engine.course_of_path(r"materials\scut\数据结构\y.pdf") == "ds")

    # 18. 落后按课程各算各的
    d.set("auto_replan_day", "")
    engine.add_task(d, yday, "math-g02", "欠的数学卡", "review", 15)
    check("数学落后独立计数", d.lag_days("math") >= 1, str(d.lag_days("math")))
    check("数据结构不受影响", d.lag_days("ds") == 0, str(d.lag_days("ds")))
    r18 = engine.auto_replan(d)
    check("自动重排只提落后的课", bool(r18) and "高等数学" in r18["brief"], str(r18))
    check("重排后数学落后归零", d.lag_days("math") == 0, str(d.lag_days("math")))

    # 19. 数据层规则：schema 版本 / 热备份 / 导出 / 数据目录
    check("schema 带版本号", d.get("schema_version") == str(dbmod.SCHEMA_VERSION),
          d.get("schema_version"))
    # 结构要升级时先备份（把版本号退回旧版模拟老库）
    d.set("schema_version", str(dbmod.SCHEMA_VERSION - 1))
    bp0 = d.backup_before_upgrade()
    d.set("schema_version", str(dbmod.SCHEMA_VERSION))
    check("升级前先备份", bp0 is not None and bp0.exists(), str(bp0))
    bp = d.backup(keep=2)
    check("能热备份", bp.exists() and bp.parent == paths.backups_dir(), str(bp))
    d.set("api_key", "sk-should-not-leak")
    d.set("tg_token", "123456:should-not-leak")
    ep = d.export_json()
    j = json.loads(ep.read_text(encoding="utf-8"))
    check("导出把密钥打成掩码",
          j["settings"].get("api_key") == "****" and j["settings"].get("tg_token") == "****"
          and "should-not-leak" not in ep.read_text(encoding="utf-8"),
          str({k: j["settings"].get(k) for k in ("api_key", "tg_token")}))
    check("导出含课程/目标/任务", bool(j["courses"]) and bool(j["goals"]) and bool(j["tasks"]),
          str(sorted(j.keys())))
    check("数据文件都在数据目录里", paths.db_file().parent == paths._r(), str(paths._r()))
    check("资料目录在数据目录下", paths.materials_dir().parent.parent == paths._r()
          or paths.materials_dir().name == "materials", str(paths.materials_dir()))

    # 20. 日志能清（原来只增不减，只能「清空界面」，库里一直堆）
    for i in range(4):
        d.log(f"测试日志 {i}", "info")
    n_before = len(d.logs(1000))
    removed = d.clear_logs()
    check("日志能清空", removed >= 4 and len(d.logs(1000)) == 0, f"删了 {removed} 条 / 原有 {n_before}")
    for i in range(5):
        d.log(f"保留 {i}", "info")
    d.clear_logs(keep=2)
    left = [e["text"] for e in d.logs(10)]
    check("清空可以只留最近几条", len(left) == 2 and left[0].endswith("4"), str(left))
    d.log("清空后还能写", "ok")
    check("清空后日志还能继续记", any("清空后还能写" in (e["text"] or "") for e in d.logs(10)),
          str([e["text"] for e in d.logs(3)]))

    # 21. 资料 → 目标：程序自己把新入库的资料拆成目标并重排（不再由人在外面写 goals.json）
    d.add_material("materials/inbox/补料.pdf", "hash-test", 1234, "ready")
    ext = paths.extracted_dir("ds") / "inbox"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / "补料.txt").write_text("二分查找：定义、循环不变量、边界条件。", encoding="utf-8")
    g_before = len(d.goals(course="ds"))
    r21 = engine.derive_goals(d, course="ds")
    g_after = len(d.goals(course="ds"))
    check("新资料自动拆出目标", r21["added"] == 2 and g_after == g_before + 2,
          f'{r21["added"]} / {g_before}→{g_after}')
    check("拆出的目标写回了 goals.json",
          (paths.goals_file("ds")).exists()
          and any(x["title"] == "新资料里的第一个目标"
                  for x in json.loads(paths.goals_file("ds").read_text(encoding="utf-8"))["goals"]),
          str(r21["titles"]))
    check("拆完自动重排了计划", bool(d.get("plan_generated_at", "")), d.get("plan_generated_at"))
    r21b = engine.derive_goals(d, course="ds")
    check("没有新资料就不再拆（不重复花钱）", r21b["added"] == 0, str(r21b["note"])[:60])

    # 22. 一级 → 二级：处理 inbox（文本归档、缺工具留原件），再把目标写成知识库
    inbox = paths.materials_dir("math") / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "导数.md").write_text("# 导数\n定义与几何意义。", encoding="utf-8")
    (inbox / "板书.png").write_bytes(b"\x89PNG\r\n\x1a\nx")
    r22 = engine.process_inbox(d, course="math")
    check("处理 inbox：文本归档、图片缺工具留原件",
          r22["done"] >= 1 and r22["kept"] >= 1 and "tesseract" in r22["hints"],
          str(r22)[:120])
    check("处理成功的原件进了回收站",
          any(p.name == "导数.md" for p in (paths.trash_dir() / "math").iterdir()),
          str(len(list((paths.trash_dir() / "math").iterdir()))))
    check("台账记了原件去向",
          any(i.get("trashed") and i.get("status") == "done"
              for i in engine._manifest_read()["items"]), str(engine._manifest_read())[:80])
    kb22 = engine.write_knowledge(d, course="math")
    check("知识库写出 Markdown", kb22["written"] >= 2, str(kb22))
    mds = [p for p in paths.knowledge_dir("math").rglob("*.md") if p.name != "_索引.md"]
    check("每个目标一个 md 且带版本号",
          len(mds) >= 2 and all("version:" in p.read_text(encoding="utf-8")[:300] for p in mds),
          str(len(mds)))
    kb22b = engine.write_knowledge(d, course="math")
    check("知识库没变就不重写", kb22b["written"] == 0, str(kb22b))

    # 23. 处理资料：逐份进度 / 断点续跑 / 取消
    events: list[dict] = []
    (inbox / "线性表.md").write_text("# 线性表\n顺序表与链表。", encoding="utf-8")
    r23 = engine.process_inbox(d, course="math", progress=events.append)
    check("处理时逐份报进度",
          any(e.get("status") == "running" for e in events)
          and any(e.get("status") == "done" for e in events), str(events[:4])[:130])
    (inbox / "线性表.md").write_text("# 线性表\n顺序表与链表。", encoding="utf-8")
    r23b = engine.process_inbox(d, course="math")
    check("台账里有的资料再来一次会跳过（不重来）", r23b["skipped"] >= 1 and r23b["done"] == 0,
          str(r23b))
    stop = threading.Event()
    stop.set()
    (inbox / "图.md").write_text("# 图\nBFS 与 DFS。", encoding="utf-8")
    r23c = engine.process_inbox(d, course="math", cancel=stop)
    check("点取消后不再开始新文件", r23c["done"] == 0 and r23c["cancelled"], str(r23c))

    # 24. 知识库浏览 + 手动清空回收站
    kl = engine.knowledge_list("math")
    check("知识库能列出章节和目标",
          len(kl) >= 2 and all(r.get("chapter") for r in kl), str(kl[:2]))
    body = engine.knowledge_read(kl[0]["path"])
    check("知识库正文去掉了 front matter",
          "要掌握" in body and not body.startswith("---"), body[:40])
    ct = engine.clear_trash()
    check("回收站能手动清空", ct["files"] >= 1 and not paths.trash_dir().exists(), str(ct))

    # 25. 手机版 P0：摘 Telegram 时补的几件事
    # 25.1 key 只认设置里填的，不再偷偷读 .env（方案 §13）
    envp = paths.env_file()
    envp.parent.mkdir(parents=True, exist_ok=True)
    envp.write_text("DEEPSEEK_API_KEY=sk-from-env-should-not-be-used\n", encoding="utf-8")
    d.set("api_key", "")
    check("没 key 时给「聊天不可用」提示", llm.key_hint(d) == llm.KEY_HINT
          and bool(llm.KEY_HINT), llm.key_hint(d))
    check("key 不再从 .env 兜底", llm.api_key(d) == "", llm.api_key(d))
    d.set("api_key", "sk-test-p0")
    check("填回 key 提示消失", llm.key_hint(d) == "", llm.key_hint(d))

    # 25.2 老库 v4 → v5：先热备，再建出 review_queue
    d.set("schema_version", "4")
    d.x("DROP TABLE IF EXISTS review_queue")
    bp25 = d.backup_before_upgrade()
    check("迁移前自动热备（v5）", bp25 is not None and bp25.exists(), str(bp25))
    d.init()
    check("迁移建出 review_queue 且版本到 5",
          bool(d.q("SELECT name FROM sqlite_master WHERE type='table' AND name='review_queue'"))
          and d.schema_version == dbmod.SCHEMA_VERSION == 6, str(d.schema_version))

    # 25.3 待确认：进库 / 只列未处理 / 听电脑 / 保留手机
    if not d.today_tasks():
        engine.regenerate(d)
    t25 = d.today_tasks()[0]
    d.set_task_state(t25["id"], "done")          # 模拟手机报「完成」
    rid25 = d.review_add(kind="task_state", task_id=t25["id"], goal_id=t25["goal_id"],
                         day=dbmod.today_s(), phone_state="done", server_state="todo",
                         reason="测试：手机记为完成，电脑按规则判未完成")
    check("待确认进库且只列未处理",
          [x["id"] for x in d.review_items()] == [rid25]
          and d.review_pending_count() == 1, str([x["id"] for x in d.review_items()]))
    row25 = d.review_resolve(rid25, "follow_server")
    check("听电脑 → 卡片改回未完成并写 resolved_at",
          d.task(t25["id"])["state"] == "todo" and row25["resolved_at"]
          and row25["resolution"] == "follow_server", str(row25))
    check("处理过的不再挂 pending",
          all(x["id"] != rid25 for x in d.review_items()) and d.review_pending_count() == 0,
          str(d.review_pending_count()))
    rid25b = d.review_add(kind="task_state", task_id=t25["id"], goal_id=t25["goal_id"],
                          day=dbmod.today_s(), phone_state="done", server_state="todo",
                          reason="测试：保留手机")
    d.set_task_state(t25["id"], "todo")
    row25b = d.review_resolve(rid25b, "keep_phone")
    check("保留手机 → 卡片按手机上报的状态转正",
          d.task(t25["id"])["state"] == "done" and row25b["resolution"] == "keep_phone",
          str(row25b))

    # 25.4 桌面页数不变：去掉「机器人」，换成「待确认」（方案 §5.2）
    import ui as uimod
    labels = [name for _, name in uimod.App.NAV]
    check("页数仍是 10 页", len(uimod.App.NAV) == 10, str(len(uimod.App.NAV)))
    check("没有「机器人」页、有「待确认」页", "机器人" not in labels and "待确认" in labels,
          str(labels))

    print("\n" + ("全部通过" if not fails else f"失败 {len(fails)} 项：{fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(run())
