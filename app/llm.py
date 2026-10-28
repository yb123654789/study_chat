#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型层：DeepSeek 调用、出题、批改、带练对话。

key 只从「设置」里填的那一项读（不再兜底数据目录的 .env，方案 §13）。
没有 key 时抛 LLMError，由上层降级（不给假答案）；看计划、看目标不受影响。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

import paths
BASE = paths.CODE_ROOT
API_URL = "https://api.deepseek.com/chat/completions"

KEY_HINT = "未配置模型 key，聊天不可用"


class LLMError(RuntimeError):
    pass


def api_key(db=None) -> str:
    """只认设置里填的 key。没填就是空，绝不偷偷读 .env / 环境变量。"""
    if db is None:
        return ""
    return (db.get("api_key", "") or "").strip()


def key_hint(db) -> str:
    """状态栏用：没 key 就提示一句，不挡看计划、看目标、看题库。"""
    return "" if api_key(db) else KEY_HINT


def _post(payload: dict, key: str, timeout: int = 90) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise LLMError(f"API {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"连不上模型服务：{e.reason}") from e
    except TimeoutError as e:
        raise LLMError("模型服务超时") from e


def chat_with_tools(messages: list[dict], tools: list[dict] | None = None, db=None,
                    max_tokens: int = 800, temperature: float = 0.7,
                    timeout: int = 90) -> dict:
    """调一次模型，把文本、工具调用和 token 用量一起拿回来。

    返回 {"text", "tool_calls", "finish", "tokens", "usage", "model"}。
    出题 / 批改 / 带练都走这里，带练额外传 tools 让模型能主动发起小测。
    """
    key = api_key(db)
    if not key:
        raise LLMError("没有配置 API key（在「设置」里填）")
    model = (db.get("model", "deepseek-flash") if db is not None else "deepseek-flash")
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
               "temperature": temperature, "stream": False}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    data = _post(payload, key, timeout)
    try:
        choice = data["choices"][0]
        msg = choice.get("message") or {}
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"模型返回格式不对：{str(data)[:200]}") from e
    usage = data.get("usage") or {}
    return {
        "text": (msg.get("content") or "").strip(),
        "tool_calls": msg.get("tool_calls") or [],
        "finish": choice.get("finish_reason") or "",
        "tokens": int(usage.get("total_tokens") or 0),
        "usage": usage,
        "model": data.get("model") or model,
    }


def chat(messages: list[dict], db=None, max_tokens: int = 800,
         temperature: float = 0.7, timeout: int = 90) -> str:
    r = chat_with_tools(messages, None, db=db, max_tokens=max_tokens,
                        temperature=temperature, timeout=timeout)
    if not r["text"]:
        raise LLMError("模型返回空")
    return r["text"]


# ------------------------------------------------------------------ JSON
def extract_json(text: str):
    """从模型输出里抠出第一个 JSON 对象。容忍 ```json 围栏和前后废话。"""
    if not text:
        raise LLMError("模型输出为空")
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    for start in (s.find("{"), s.find("[")):
        if start < 0:
            continue
        for end in range(len(s) - 1, start, -1):
            if s[end] not in "}]":
                continue
            try:
                return json.loads(s[start:end + 1])
            except ValueError:
                continue
        break
    raise LLMError("模型没有返回 JSON")


# ------------------------------------------------------------------ 提示词
SYSTEM_TUTOR = """你是这门课的学习带练，负责把一个 25~45 分钟的学习目标带用户真学会。

规则：
1. 先给结论，再给理由，一次回答不超过 8 行。用户问得具体就直接答，别绕。
2. 尽量用用户资料里的说法；引用原文时注明出处（资料名 + 页码/幻灯片）。
3. 每轮结尾用一句话检查他是否真懂（一个具体问题，别连着追问两个以上）。
4. 资料里没有的内容，直说「资料里没有」，可以补通用解释，但要标注「补充说明」。
5. 绝不输出日期、考试倒计时、进度数字——那些由程序算，你复述就会和数据库对不上。
6. 绝不说「你已经通过了 / 这个任务完成了」——是否通过只由小测判定。
7. 用户说「出题」时，只回一句「好，开始小测」，不要自己编题。
8. 用户明显没懂时，换一个更小的例子重讲，不要重复上一段话。"""

SYSTEM_QUIZ = """你是出题人。基于给定的学习目标，出 3 道题检验是否真的掌握。
规则：
- 2 道单选，每题 4 个选项，干扰项必须是"看起来合理但确实错的"，不能是明显荒谬的。
- 1 道简答，要求用 2~3 句话解释，不能是名词解释。
- 题目考理解与应用，不考背诵原文；不要问"资料里第几页写了什么"。
- 只输出 JSON，不要解释。"""

SYSTEM_GRADE = """你是严格但公平的阅卷人。按 rubric 判断答案是否覆盖要点。
- 覆盖 2/3 以上要点 → pass
- 只覆盖 1/3，或出现事实错误 → fail
- 答非所问、空答 → fail
不要因为"字写得多"就给过。只输出 JSON。"""


def goal_block(goal: dict, hits: list[dict] | None = None) -> str:
    lines = [
        f"本节目标：{goal.get('title', '')}",
        f"章节：{goal.get('ch', '')}　预计 {goal.get('m', 30)} 分钟",
        "验收点：",
    ]
    lines += [f"- {p}" for p in (goal.get("cp") or [])] or ["- （无）"]
    if hits:
        lines.append("从学生资料里检索到的相关段落（回答/出题优先用这些）：")
        for e in hits:
            lines.append(f"- 《{e['src']}》{e['anchor']}：{e['text']}")
    elif hits is not None:
        lines.append("资料检索：无命中。只能讲通用知识，必须标注「补充说明」。")
    return "\n".join(lines)


# ------------------------------------------------------------------ 出题
def generate_quiz(goal: dict, hits: list[dict], db=None) -> list[dict]:
    """返回 3 题；结构不合格就抛 LLMError。"""
    msgs = [
        {"role": "system", "content": SYSTEM_QUIZ},
        {"role": "user", "content": goal_block(goal, hits) + """

输出 JSON：
{
  "questions": [
    {"type":"choice","stem":"...","options":["A...","B...","C...","D..."],"answer":1},
    {"type":"choice","stem":"...","options":["...","...","...","..."],"answer":0},
    {"type":"short","stem":"...","rubric":["必须提到的要点1","要点2","要点3"]}
  ]
}"""},
    ]
    text = chat(msgs, db=db, max_tokens=1200, temperature=0.8)
    obj = extract_json(text)
    qs = obj.get("questions") if isinstance(obj, dict) else obj
    if not isinstance(qs, list) or len(qs) < 3:
        raise LLMError("题目数量不足 3 道")
    out = []
    for q in qs[:3]:
        t = q.get("type") or "choice"
        if t == "choice":
            opts = q.get("options") or []
            if len(opts) < 2:
                continue
            try:
                ans = int(q.get("answer", 0))
            except (TypeError, ValueError):
                ans = 0
            out.append({"type": "choice", "stem": q.get("stem", ""), "options": opts,
                        "answer": max(0, min(len(opts) - 1, ans))})
        else:
            out.append({"type": "short", "stem": q.get("stem", ""),
                        "rubric": q.get("rubric") or [], "options": [], "answer": ""})
    if len(out) < 3:
        raise LLMError("可用题目不足 3 道")
    return out[:3]


def fallback_quiz(goal: dict) -> list[dict]:
    """没 key / 模型挂了时的降级题：用验收点出简答，不冒充选择题。"""
    cps = (goal.get("cp") or [])[:3] or [goal.get("title", "这个目标")]
    out = []
    for i, cp in enumerate(cps):
        out.append({"type": "short", "stem": f"用两三句话回答：{cp}", "options": [],
                    "rubric": [cp], "answer": "", "keys": []})
    while len(out) < 3:
        out.append({"type": "short", "stem": f"用一句话说清「{goal.get('title','')}」的核心是什么。",
                    "options": [], "rubric": [goal.get("title", "")], "answer": "", "keys": []})
    return out


# ------------------------------------------------------------------ 批改
def grade_short(goal: dict, stem: str, rubric: list[str], answer: str, db=None) -> dict:
    """返回 {pass, covered, missed, feedback}。失败时抛 LLMError，上层用关键词判。"""
    msgs = [
        {"role": "system", "content": SYSTEM_GRADE},
        {"role": "user", "content":
            f"目标：{goal.get('title','')}\n题目：{stem}\n"
            f"要点：{json.dumps(rubric, ensure_ascii=False)}\n"
            f"学生答案：{answer}\n\n"
            '输出 JSON：{"pass": true, "covered": ["要点1"], "missed": ["要点2"], "feedback": "一句话，指出缺什么"}'},
    ]
    text = chat(msgs, db=db, max_tokens=400, temperature=0.2)
    obj = extract_json(text)
    if not isinstance(obj, dict):
        raise LLMError("批改结果不是对象")
    return {
        "pass": bool(obj.get("pass")),
        "covered": obj.get("covered") or [],
        "missed": obj.get("missed") or [],
        "feedback": (obj.get("feedback") or "").strip(),
    }


def grade_keywords(rubric: list[str], answer: str) -> dict:
    """降级批改：按要点里的关键词命中率判，命中 ≥2 或 ≥2/3 才算过。"""
    text = (answer or "").strip()
    if len(text) < 4:
        return {"pass": False, "covered": [], "missed": rubric,
                "feedback": "答案太短，等于没答。"}
    covered, missed = [], []
    for r in rubric or []:
        hit = False
        for kw in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}", r):
            if kw and kw in text:
                hit = True
                break
        (covered if hit else missed).append(r)
    need = 2 if len(rubric or []) >= 3 else max(1, len(rubric or []))
    return {"pass": len(covered) >= need, "covered": covered, "missed": missed,
            "feedback": "" if len(covered) >= need
                        else ("还缺：" + "；".join(missed[:2]) if missed else "答非所问。")}


# ------------------------------------------------------------------ 带练
def tutor_reply(goal: dict, hits: list[dict], history: list[dict], text: str, db=None) -> str:
    msgs = [{"role": "system", "content": SYSTEM_TUTOR},
            {"role": "user", "content": goal_block(goal, hits)}]
    for m in history[-8:]:
        role = "assistant" if m.get("role") == "bot" else "user"
        if m.get("text"):
            msgs.append({"role": role, "content": m["text"]})
    msgs.append({"role": "user", "content": text})
    return chat(msgs, db=db, max_tokens=700, temperature=0.7)


# ------------------------------------------------------------------ 错题讲解
SYSTEM_WHY = """你是辅导老师。学生刚把这道选择题选错了。
用一句话（不超过 40 字）点出「他选的那个为什么不对」或者「正确选项的判断依据」。
- 不重复题干，不复述选项原文，不说客套话，不用「同学」这种称呼。
- 只输出这一句解释，不要 JSON，不要前缀。"""


def explain_wrong(goal: dict, q: dict, right_idx: int, pick_idx: int, db=None) -> str:
    letters = "ABCDEFGH"
    opts = q.get("options") or []
    lines = [f"{letters[i]}. {o}" for i, o in enumerate(opts)]
    try:
        right = f"{letters[right_idx]}. {opts[right_idx]}"
        picked = f"{letters[pick_idx]}. {opts[pick_idx]}"
    except IndexError:
        right, picked = "?", "?"
    msgs = [
        {"role": "system", "content": SYSTEM_WHY},
        {"role": "user", "content":
            f"知识点：{goal.get('title','')}\n题目：{q.get('stem','')}\n"
            f"选项：\n" + "\n".join(lines) +
            f"\n正确答案：{right}\n学生选了：{picked}"},
    ]
    return chat(msgs, db=db, max_tokens=120, temperature=0.3, timeout=20).strip()
