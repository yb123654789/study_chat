#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""带练引擎：会话落库 + 状态条 + 检索 + 工具调用。

桌面端（ui.StudyPage）和 Telegram（bot._tutor）共用这一份。
两边只负责「把消息显示出来」和「把工具接上」，不再各写一套 prompt。

三条铁律落在这里：
  1. 数字由代码算（state_bar），模型只许引用；
  2. 模型不许宣布通过，改状态只能走工具；
  3. 每轮都基于资料检索，引用跟着消息一起存。
"""
from __future__ import annotations

import json
import re

import engine
import llm
from db import today_s

HISTORY_TURNS = 12          # 喂给模型的历史条数（不含本轮）
HISTORY_CHARS = 6000        # 历史部分的字符预算（中文约 1 字 ≈ 0.7 token；超了从最旧的往前砍）
HIT_CHARS = 1200            # 单段检索资料最多塞多少字，防止一段顶满上下文
MAX_ROUNDS = 2              # 工具调用后最多再补一轮，防死循环


class ChatError(RuntimeError):
    pass


TOOLS = [
    {"type": "function", "function": {
        "name": "start_quiz",
        "description": "用户想被考、说「出题 / 考考我 / 测一下」时调用：开始 3 题小测。"
                       "不要在回答里自己编题。",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "skip_task",
        "description": "用户明确说这张卡今天不学、先跳过时调用。",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "show_plan",
        "description": "用户问计划、进度、还要学多久、落后几天时调用，让程序把真实数字发给他。",
        "parameters": {"type": "object", "properties": {}},
    }},
]


# ------------------------------------------------------------------ 状态条
def lag_days(db, course: str | None = None) -> int:
    """过去日期还挂着 todo 就算落后；重排后自动归零。"""
    return int(db.lag_days(course))


def state_bar(db, task: dict | None = None) -> str:
    course = (task or {}).get("course") or None
    s = db.stats(course)
    bits = [f"今天 {s['today_done']}/{s['today_total']} 张", f"已学 {s['today_minutes']} 分钟"]
    lag = lag_days(db, course)
    if lag:
        bits.append(f"落后 {lag} 天")
    bits += [f"距考核 {s['days_left']} 天", f"目标 {s['done_goals']}/{s['total_goals']}"]
    if course:
        bits.insert(0, str(s.get("subject") or course))
    text = "｜".join(bits)
    if task:
        text += f"\n本卡：{task['title']}（{task['minutes']} 分钟 · {task['state']}）"
    return text


# ------------------------------------------------------------------ 会话
def conversation(db, task: dict | None) -> dict:
    """一张卡一条会话；task 为空时开一条自由会话（每次新建）。"""
    if not task:
        return db.conversation(None, kind="free", title="自由问答")
    return db.conversation(int(task["id"]), goal_id=task["goal_id"], title=task["title"])


def reset(db, conv: dict) -> None:
    db.clear_messages(int(conv["id"]))


def history(db, conv: dict, limit: int = 50) -> list[dict]:
    return db.conv_messages(int(conv["id"]), limit)


def trim_history(rows: list[dict], budget: int = HISTORY_CHARS) -> list[dict]:
    """按字符预算从头砍历史：最近的必留，最旧的先扔。

    只按条数截（12 条）挡不住「一轮回答写了 800 字 × 12 轮」，
    所以再按字数兜一层；至少留最后一问一答。
    """
    kept: list[dict] = []
    used = 0
    for m in reversed(rows):
        n = len(m.get("text") or "")
        if len(kept) >= 2 and used + n > budget:
            break
        kept.append(m)
        used += n
    return list(reversed(kept))


# ------------------------------------------------------------------ 提示词
def system_prompt(db, task: dict | None, goal: dict, hits: list[dict]) -> str:
    cp = "\n".join(f"- {c}" for c in (goal.get("cp") or [])) or "- （没有验收点，按标题讲）"
    if hits:
        src = "\n".join(f"- 《{h['src']}》{h['anchor']}：{h['text'][:HIT_CHARS]}" for h in hits)
    else:
        src = "（这轮没检索到资料段落。只能讲通用知识，并且必须标注「补充说明」。）"
    return f"""你是这门课的学习带练，带一张 25~45 分钟的学习卡。

课程：{goal.get('course_name') or goal.get('course') or '当前课程'}
程序算好的当前状态（要引用就直接用这里的数，不要自己算、不要编）：
{state_bar(db, task)}

本节目标：{goal.get('title', '')}
章节：{goal.get('ch', '')}
学完要能回答：
{cp}

这轮检索到的资料段落（优先用，引用时写《资料名》+ 页码/幻灯片）：
{src}

规则：
1. 中文短句，先给结论再给理由；一次回答不超过 8 行。
2. 只用上面的资料和验收点回答。资料里没有的，直说「资料里没有」，补充内容必须标「补充说明」。
3. 不要输出任何日期、倒计时、进度数字（除了状态条里已经有的）。
4. 绝不说「你通过了 / 这张卡完成了」——是否通过只由小测判定。
5. 用户想被考，调用 start_quiz；说先跳过，调用 skip_task；问计划/进度，调用 show_plan。
   不要自己出题，出题由程序做。
6. 每轮结尾用一个具体问题检查他是否真懂，别连着问两个以上。
7. 用户明显没懂时，换一个更小的例子重讲，别重复上一段话。
8. 输出是纯文本：不要用 Markdown（**加粗**、## 标题、`代码`），要强调就用「」。"""


_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_HEAD = re.compile(r"(?m)^#{1,4}\s*")


def plain(text: str) -> str:
    """去掉模型爱加的 Markdown 记号：Telegram 是 HTML 模式，桌面端也不渲染 md。"""
    t = _MD_BOLD.sub(r"\1", text or "")
    t = _MD_HEAD.sub("", t)
    return t.replace("`", "")


# ------------------------------------------------------------------ 一轮对话
def reply(db, conv: dict, text: str, handlers=None) -> dict:
    """跑一轮带练。返回 {text, sources, tool, tokens}。

    handlers(name, args) -> str 由调用方提供，负责真正执行动作
    （Telegram 发小测、桌面端弹小测窗口）。不提供就不给模型工具。
    """
    text = (text or "").strip()
    if not text:
        raise ChatError("空消息")

    if getattr(db, "remote", False):
        # 瘦客户端：整轮对话在服务器上跑（检索、模型、落库、改状态都在那边）
        res = db.action("chat",
                        task_id=int(conv["task_id"]) if conv.get("task_id") else None,
                        text=text)
        return {"text": res.get("text", ""), "sources": res.get("sources") or [],
                "tool": res.get("tool", ""), "tokens": res.get("tokens", 0)}

    task = db.task(int(conv["task_id"])) if conv.get("task_id") else None
    goal = db.goal(conv.get("goal_id") or "") or {
        "title": (task or {}).get("title", ""), "cp": [], "ch": ""}
    if goal.get("course"):
        goal = {**goal, "course_name": (db.course(goal["course"]) or {}).get("name", "")}
    hits = engine.retrieve(goal, text, 3)

    msgs: list[dict] = [{"role": "system", "content": system_prompt(db, task, goal, hits)}]
    for m in trim_history(db.conv_messages(int(conv["id"]), HISTORY_TURNS)):
        if m["role"] == "me" and m["text"]:
            msgs.append({"role": "user", "content": m["text"]})
        elif m["role"] == "bot" and m["text"]:
            msgs.append({"role": "assistant", "content": m["text"]})
    msgs.append({"role": "user", "content": text})

    db.add_message(int(conv["id"]), "me", text)

    tokens = 0
    tool_used = ""
    r = llm.chat_with_tools(msgs, TOOLS if handlers else None, db=db,
                            max_tokens=800, temperature=0.7)
    tokens += r["tokens"]
    out = r["text"]

    for _ in range(MAX_ROUNDS - 1):
        calls = r["tool_calls"] if handlers else []
        if not calls:
            break
        results = []
        for call in calls[:2]:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            try:
                res_text = handlers(name, args) or ""
            except Exception as e:      # 工具失败不能把整轮对话搞没
                res_text = f"执行失败：{type(e).__name__}: {e}"
            tool_used = name or tool_used
            db.add_message(int(conv["id"]), "tool", f"{name}: {res_text}")
            results.append({"role": "tool", "tool_call_id": call.get("id", ""),
                            "content": str(res_text)[:1500]})
        msgs.append({"role": "assistant", "content": r["text"] or "",
                     "tool_calls": r["tool_calls"]})
        msgs.extend(results)
        r = llm.chat_with_tools(msgs, None, db=db, max_tokens=500, temperature=0.4)
        tokens += r["tokens"]
        out = r["text"] or out

    if not out:
        out = "（这轮没说出内容，换个问法试试）"
    out = plain(out)
    sources = [{"src": h["src"], "anchor": h["anchor"]} for h in hits]
    db.add_message(int(conv["id"]), "bot", out, sources=sources, tokens=tokens)
    db.touch_conv(int(conv["id"]))
    return {"text": out, "sources": sources, "tool": tool_used, "tokens": tokens,
            "handled": bool(tool_used)}
