#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram bot：长轮询 + 状态机（学 → 出题 → 判分 → 记账）。

传输层和业务层分开：
  TgClient  只负责调 API（可替换成假客户端做离线自检）
  Bot       只负责决定「回什么、按钮是什么」，不碰网络
"""
from __future__ import annotations

import html
import json
import time
import urllib.error
import urllib.request

import engine
import chat
import llm
from db import now, today_s

API = "https://api.telegram.org/bot{token}/{method}"

QUIZ_WORDS = ("出题", "小测", "考我", "测验", "开始测试", "测试一下", "做题")


# ------------------------------------------------------------------ 传输
class TgError(RuntimeError):
    pass


class TgClient:
    def __init__(self, token: str, proxy: str = ""):
        self.token = token
        self.proxy = proxy or None
        self._opener = None
        if self.proxy:
            handler = urllib.request.ProxyHandler({"https": self.proxy, "http": self.proxy})
            self._opener = urllib.request.build_opener(handler)

    def call(self, method: str, params: dict | None = None, timeout: int = 40) -> dict:
        if not self.token:
            raise TgError("没有配置 Bot Token")
        url = API.format(token=self.token, method=method)
        body = json.dumps(params or {}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            op = self._opener.open if self._opener else urllib.request.urlopen
            with op(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise TgError(f"{method} 失败 {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise TgError(f"连不上 Telegram（{e.reason}）——检查代理") from e
        except TimeoutError as e:
            raise TgError(f"{method} 超时") from e
        if not data.get("ok"):
            raise TgError(f"{method} 返回：{str(data)[:300]}")
        return data.get("result")

    # --- 高层封装
    def get_me(self) -> dict:
        return self.call("getMe", timeout=15)

    def send(self, chat_id, text: str, buttons=None, force_reply: bool = False,
             reply_to: int | None = None, preview: bool = False) -> dict:
        markup = None
        if buttons:
            markup = {"inline_keyboard": [
                [{"text": b[0], "callback_data": b[1]} for b in row] for row in buttons]}
        elif force_reply:
            markup = {"force_reply": True, "input_field_placeholder": "回答在这里"}
        parts = split_text(text)
        last = None
        for i, part in enumerate(parts):
            params = {"chat_id": chat_id, "text": part, "parse_mode": "HTML",
                      "disable_web_page_preview": not preview}
            if i == len(parts) - 1 and markup:
                params["reply_markup"] = markup
            if reply_to and i == 0:
                params["reply_to_message_id"] = reply_to
            last = self.call("sendMessage", params)
            if i < len(parts) - 1:
                time.sleep(0.35)
        return last or {}

    def edit(self, chat_id, message_id: int, text: str) -> None:
        try:
            self.call("editMessageText", {"chat_id": chat_id, "message_id": message_id,
                                          "text": text, "parse_mode": "HTML",
                                          "disable_web_page_preview": True})
        except TgError:
            pass

    def ack(self, callback_id: str, text: str = "") -> None:
        try:
            self.call("answerCallbackQuery",
                      {"callback_query_id": callback_id, "text": text[:180]})
        except TgError:
            pass

    def updates(self, offset: int, timeout: int = 25) -> list[dict]:
        return self.call("getUpdates", {"offset": offset, "timeout": timeout,
                                        "allowed_updates": ["message", "callback_query"]},
                         timeout=timeout + 10) or []


def split_text(text: str, limit: int = 3800) -> list[str]:
    if len(text) <= limit:
        return [text]
    out, buf = [], ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > limit:
            out.append(buf)
            buf = line
        else:
            buf += line
    if buf:
        out.append(buf)
    return out or [text[:limit]]


class FakeClient:
    """离线自检用：把发出去的消息记下来。"""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, chat_id, text, buttons=None, force_reply=False, reply_to=None, preview=False):
        self.sent.append({"chat_id": chat_id, "text": text, "buttons": buttons,
                          "force_reply": force_reply})
        return {"message_id": len(self.sent)}

    def edit(self, *a, **k):
        pass

    def ack(self, *a, **k):
        pass

    def get_me(self):
        return {"username": "fake_bot"}

    def last(self) -> dict:
        return self.sent[-1] if self.sent else {}


# ------------------------------------------------------------------ 业务
def esc(s: str) -> str:
    return html.escape(str(s or ""), quote=False)


class Bot:
    def __init__(self, db, client):
        self.db = db
        self.tg = client
        self.bound: str = db.get("chat_id", "")

    # --- 入口
    def handle_update(self, upd: dict) -> None:
        try:
            if "callback_query" in upd:
                self._on_callback(upd["callback_query"])
            elif "message" in upd:
                self._on_message(upd["message"])
        except Exception as e:  # noqa: BLE001
            self.db.log(f"处理消息出错：{type(e).__name__}: {e}", "err")
            try:
                chat_id = (upd.get("message") or {}).get("chat", {}).get("id") \
                    or (upd.get("callback_query") or {}).get("message", {}).get("chat", {}).get("id")
                if chat_id:
                    self.tg.send(chat_id, f"⚠️ 出错了：{esc(e)}")
            except Exception:  # noqa: BLE001
                pass

    # --- 权限：单人白名单
    def _chat_ok(self, chat_id) -> bool:
        cid = str(chat_id)
        if not self.bound:
            self.bound = cid
            self.db.set("chat_id", cid)
            self.db.log(f"绑定 Telegram 会话 {cid}", "ok")
            return True
        return cid == self.bound

    # --- 消息
    def _on_message(self, msg: dict) -> None:
        chat_id = msg.get("chat", {}).get("id")
        text = (msg.get("text") or "").strip()
        if not chat_id:
            return
        if not self._chat_ok(chat_id):
            self.tg.send(chat_id, "这个 bot 只服务一个人，不接受别的会话。")
            return
        if not text:
            self.tg.send(chat_id, "现在只能处理文字（语音/图片先不支持）。")
            return

        cmd = text.split()[0].lower() if text.startswith("/") else ""
        if cmd in ("/start", "/today", "/d"):
            return self.cmd_today(chat_id)
        if cmd == "/plan":
            return self.cmd_plan(chat_id)
        if cmd in ("/stats", "/me"):
            return self.cmd_stats(chat_id)
        if cmd == "/help":
            return self.cmd_help(chat_id)
        if cmd == "/skip":
            return self.cmd_skip(chat_id)
        if cmd in ("/reset", "/cancel"):
            self.db.set_bot_state(chat_id, {})
            return self.tg.send(chat_id, "已退出当前任务。发 /today 回到今日任务。")
        if cmd == "/id":
            return self.tg.send(chat_id, f"chat_id = <code>{chat_id}</code>")

        st = self.db.bot_state(chat_id)
        if st.get("mode") == "quiz" and st.get("awaiting"):
            if any(w in text for w in QUIZ_WORDS):
                return self.tg.send(chat_id, "已经在小测里了，先把这道题答完。想重来发 /reset。")
            if self._looks_question(text):
                return self._quiz_side_question(chat_id, st, text)   # 提问不能当成答案判
            return self._on_short_answer(chat_id, text, st)
        if st.get("mode") == "learn":
            if any(w in text for w in QUIZ_WORDS):
                return self.start_quiz(chat_id, st["task_id"])
            intent = self._intent(text)
            if intent == "today":
                return self.cmd_today(chat_id)
            if intent == "plan":
                return self.cmd_plan(chat_id)
            if intent == "stats":
                return self.cmd_stats(chat_id)
            if intent == "quit":
                self.db.set_bot_state(chat_id, {})
                return self.tg.send(chat_id, "已退出这张卡。发 /today 重新选。")
            return self._tutor(chat_id, text, st)
        if st.get("mode") == "quiz":
            return self.tg.send(chat_id, "这题是选择题，点上面的 A/B/C/D 按钮。想放弃发 /reset。")
        if any(w in text for w in QUIZ_WORDS):
            return self.tg.send(chat_id, "先在 /today 里选一张卡，我才知道考哪一节。")
        intent = self._intent(text)
        if intent == "today":
            return self.cmd_today(chat_id)
        if intent == "plan":
            return self.cmd_plan(chat_id)
        if intent == "stats":
            return self.cmd_stats(chat_id)
        return self.tg.send(chat_id, "我没太懂。发 /today 看今天的任务，或 /help 看能做什么。")

    @staticmethod
    def _intent(text: str) -> str:
        """把「今天要学什么」这类大白话接到命令上，别让人记斜杠。"""
        t = text.replace(" ", "")
        today = ("今天学什么", "今天要学", "今天学啥", "今日任务", "今天的任务", "今天做什么",
                 "今天干啥", "今天该学", "学什么", "学啥", "任务呢", "今日安排")
        plan = ("计划", "安排到哪", "排到哪", "排期")
        stats = ("进度", "还有多少", "剩多少", "几天考试", "还有几天", "掌握多少")
        quit_ = ("退出", "不学了", "算了", "结束", "换个任务")
        for w in today:
            if w in t:
                return "today"
        for w in plan:
            if w in t:
                return "plan"
        for w in stats:
            if w in t:
                return "stats"
        for w in quit_:
            if w in t:
                return "quit"
        return ""

    # --- 命令
    def cmd_today(self, chat_id) -> None:
        text = engine.today_digest(self.db)
        tasks = [t for t in self.db.today_tasks() if t["state"] == "todo"]
        rows = [[(f"▶ {t['title'][:32]}", f"t:{t['id']}")] for t in tasks[:6]]
        self.tg.send(chat_id, text, buttons=rows or None)

    def cmd_plan(self, chat_id) -> None:
        db = self.db
        lines = ["<b>计划</b>"]
        for c in db.courses():
            cid = c["id"]
            note = db.get("plan_note:" + cid) or "还没排"
            lines.append(f"\n<b>{esc(c['name'] or cid)}</b>"
                         f"（考 {c.get('exam_date') or '—'}）：{note}")
        t0 = today_s()
        rows = db.q("SELECT date, course, COUNT(*) n, SUM(minutes) m FROM tasks "
                    "WHERE date>=? AND state!='dropped' GROUP BY date, course "
                    "ORDER BY date LIMIT 20", (t0,))
        if rows:
            names = {c["id"]: c["name"] or c["id"] for c in db.courses()}
            lines.append("")
            for r in rows:
                lines.append(f"{r['date'][5:]}　{names.get(r['course'], r['course'])}"
                             f"　{r['n']} 张 · {r['m'] or 0} 分钟")
        self.tg.send(chat_id, "\n".join(lines))

    def cmd_stats(self, chat_id) -> None:
        db = self.db
        parts = []
        for c in db.courses():
            s = db.stats(c["id"])
            parts.append(f"<b>{esc(c['name'] or c['id'])}</b>　距考 {s['days_left']} 天　"
                         f"目标 {s['done_goals']}/{s['total_goals']}　"
                         f"今天 {s['today_done']}/{s['today_total']}")
        s = db.stats()
        parts.append(f"\n合计：今天 {s['today_done']}/{s['today_total']} 张，"
                     f"{s['today_minutes']} 分钟；连续 {s['streak']} 天有完成")
        self.tg.send(chat_id, "\n".join(parts))

    def cmd_help(self, chat_id) -> None:
        self.tg.send(chat_id, (
            "<b>能做什么</b>\n"
            "/today 今日任务\n"
            "/plan 计划概览\n"
            "/stats 进度\n"
            "/skip 跳过今天的当前任务\n"
            "/reset 退出当前任务\n\n"
            "选一张卡后直接提问就行，说「出题」开始小测。"
            "3 题对 2 题才算完成。"))

    def cmd_skip(self, chat_id) -> None:
        st = self.db.bot_state(chat_id)
        tid = st.get("task_id")
        if not tid:
            return self.tg.send(chat_id, "现在没有进行中的任务。")
        self.db.set_task_state(tid, "skipped")
        self.db.set_bot_state(chat_id, {})
        self.tg.send(chat_id, "已跳过这张卡，明天重排会把它挪回来。")

    # --- 选卡
    def open_task(self, chat_id, task_id: int) -> None:
        t = self.db.task(int(task_id))
        if not t:
            return self.tg.send(chat_id, "这张卡找不到了，发 /today 刷新。")
        goal = self.db.goal(t["goal_id"]) or {"title": t["title"], "cp": [], "ch": ""}
        prev = self.db.bot_state(chat_id)
        st = {"mode": "learn", "task_id": t["id"], "goal_id": t["goal_id"],
              "since": now().isoformat()}
        self.db.set_bot_state(chat_id, st)
        conv = chat.conversation(self.db, t)
        turns = len([m for m in chat.history(self.db, conv, 50) if m["role"] == "me"])
        cp = goal.get("cp") or []
        tips = ("\n学完这些就算学会：\n" + "\n".join(f"· {esc(c)}" for c in cp)) if cp else ""
        back = f"\n（接着上次的 {turns} 轮聊）" if turns else ""
        if prev.get("task_id") != t["id"]:      # 反复点同一张卡不重复记日志
            self.db.log(f"开始学习 {t['goal_id']}", "info")
        self.tg.send(chat_id,
                     f"<b>{esc(t['title'])}</b>\n"
                     f"{esc(goal.get('ch',''))} · {t['minutes']} 分钟{tips}{back}\n\n"
                     "直接问我，或者说说你卡在哪。说「出题」开始小测。",
                     buttons=[[("🎯 出题", f"q:{t['id']}"), ("← 今日", "m:today")]])

    # --- 带练
    def _chat_tools(self, chat_id, st: dict):
        """模型要动状态时只能走这里：代码判断能不能做、做完回报一句。"""
        def run(name: str, args: dict) -> str:
            if name == "start_quiz":
                self.start_quiz(chat_id, int(st["task_id"]))
                return "小测已发出：3 题，答对 2 题算过。"
            if name == "skip_task":
                self.db.set_task_state(int(st["task_id"]), "skipped")
                self.db.set_bot_state(chat_id, {})
                self.tg.send(chat_id, "已跳过这张卡，明天重排会挪回来。")
                return "已跳过这张卡"
            if name == "show_plan":
                self.cmd_plan(chat_id)
                return "计划已经发给他了"
            return "这个动作现在做不了"

        return run

    def _tutor(self, chat_id, text: str, st: dict) -> None:
        t = self.db.task(int(st["task_id"]))
        if not t:
            self.db.set_bot_state(chat_id, {})
            return self.tg.send(chat_id, "这张卡已经被重排换掉了，发 /today 重新选。")
        conv = chat.conversation(self.db, t)
        try:
            res = chat.reply(self.db, conv, text, handlers=self._chat_tools(chat_id, st))
        except llm.LLMError as e:
            self.db.log(f"对话失败：{e}", "err")
            return self.tg.send(chat_id, f"⚠️ 模型没连上：{esc(e)}\n（检查设置里的 API key 和网络）")
        if res["tool"]:
            return                      # 工具自己已经把消息发出去了
        msg = esc(res["text"])
        if res["sources"]:
            src = "、".join(f"《{esc(s['src'][:24])}》{esc(s['anchor'])}"
                            for s in res["sources"][:2])
            msg += f"\n\n<i>参考：{src}</i>"
        self.tg.send(chat_id, msg,
                     buttons=[[("🎯 出题", f"q:{st['task_id']}"), ("← 今日", "m:today")]])

    # --- 出题
    def start_quiz(self, chat_id, task_id: int, regen: bool = False) -> None:
        t = self.db.task(int(task_id))
        if not t:
            return self.tg.send(chat_id, "这张卡找不到了，发 /today 刷新。")
        force = False
        if regen:
            if engine.regen_allowed(self.db, t["goal_id"]):
                force = True
            elif engine.fail_streak(self.db, t["goal_id"]) >= 2:
                return self.tg.send(chat_id, "这个点连续两次没过，别再换题了 —— "
                                             "明天排了一次回炉，今晚先把讲解再看一遍。")
            else:
                self.tg.send(chat_id, "这个点 24 小时内已经换过两次题了，同一套再来一遍。")
        self.tg.send(chat_id, "好，开始小测。3 题对 2 题就算过。")
        try:
            qs = engine.ensure_quiz(self.db, t["goal_id"], force_new=force)
        except Exception as e:  # noqa: BLE001
            self.db.log(f"出题失败：{e}", "err")
            return self.tg.send(chat_id, f"⚠️ 出题失败：{esc(e)}")

        st = {"mode": "quiz", "task_id": t["id"], "goal_id": t["goal_id"],
              "ids": [q["id"] for q in qs], "idx": 0, "right": 0, "total": len(qs),
              "awaiting": None, "batch": qs[0].get("batch", 1)}
        self.db.set_bot_state(chat_id, st)
        self._next_question(chat_id, st)

    def _next_question(self, chat_id, st: dict) -> None:
        idx = st["idx"]
        if idx >= len(st["ids"]):
            return self._finish(chat_id, st)
        q = self.db.quiz(st["ids"][idx])
        if not q:
            st["idx"] += 1
            return self._next_question(chat_id, st)
        head = f"<b>第 {idx + 1}/{st['total']} 题</b>\n{esc(q['stem'])}"
        if q["type"] == "choice":
            st["awaiting"] = None
            self.db.set_bot_state(chat_id, st)
            letters = "ABCDEFGH"
            rows = [[(f"{letters[i]}. {opt[:40]}", f"a:{q['id']}:{i}")]
                    for i, opt in enumerate(q["options"])]
            self.tg.send(chat_id, head, buttons=rows)
        else:
            st["awaiting"] = q["id"]
            self.db.set_bot_state(chat_id, st)
            self.tg.send(chat_id, head + "\n\n<i>用两三句话回答，我按要点判。</i>", force_reply=True)

    # --- 判分
    def _record(self, chat_id, st: dict, q: dict, answer: str) -> dict:
        goal = self.db.goal(st["goal_id"]) or {"title": ""}
        res = engine.grade_one(self.db, goal, q, answer)
        self.db.add_attempt(st["task_id"], st["goal_id"], q["id"], answer,
                            1 if res["pass"] else 0, res.get("feedback", ""))
        if res["pass"]:
            st["right"] += 1
        self.db.set_bot_state(chat_id, st)
        return res

    def _feedback(self, goal: dict, q: dict, res: dict, answer: str) -> str:
        if q["type"] == "choice":
            if res["pass"]:
                return "✅ 对"
            letters = "ABCDEFGH"
            try:
                idx = int(str(q["answer"]))
                if not 0 <= idx < len(q["options"]):
                    raise IndexError(idx)
                right = letters[idx]
                right_text = q["options"][idx]
            except (ValueError, IndexError):
                idx, right, right_text = -1, "?", ""
            head = f"❌ 不对。正确答案是 <b>{right}</b>"
            if right_text:
                head += f"：{esc(right_text)}"
            # 错题要讲「你选的那个为什么不对」，否则下次还错
            why = ""
            try:
                pick = int(answer)
                why = llm.explain_wrong(goal, q, idx, pick, self.db)
            except (ValueError, llm.LLMError) as e:
                self.db.log(f"错题讲解降级：{e}", "warn")
            return head + (f"\n<i>{esc(why)}</i>" if why else "")
        head = "✅ 答到要点了" if res["pass"] else "❌ 没答到要点"
        extra = res.get("feedback") or ""
        missed = res.get("missed") or []
        if not res["pass"] and missed:
            extra = (extra + "；" if extra else "") + "缺：" + "；".join(missed[:2])
        return head + ("\n" + esc(extra) if extra else "")

    def _on_choice(self, chat_id, qid: int, pick: int) -> None:
        st = self.db.bot_state(chat_id)
        if st.get("mode") != "quiz" or qid not in (st.get("ids") or []):
            return self.tg.send(chat_id, "这题已经过期了，发 /today 重新开始。")
        q = self.db.quiz(qid)
        goal = self.db.goal(st["goal_id"]) or {"title": ""}
        res = self._record(chat_id, st, q, str(pick))
        self.tg.send(chat_id, self._feedback(goal, q, res, str(pick)))
        st["idx"] = st["ids"].index(qid) + 1
        st["awaiting"] = None
        self.db.set_bot_state(chat_id, st)
        self._next_question(chat_id, st)

    def _on_short_answer(self, chat_id, text: str, st: dict) -> None:
        qid = st["awaiting"]
        q = self.db.quiz(qid)
        if not q:
            st["awaiting"] = None
            self.db.set_bot_state(chat_id, st)
            return self._next_question(chat_id, st)
        goal = self.db.goal(st["goal_id"]) or {"title": ""}
        res = self._record(chat_id, st, q, text)
        self.tg.send(chat_id, self._feedback(goal, q, res, text))
        st["awaiting"] = None
        st["idx"] = st["ids"].index(qid) + 1
        self.db.set_bot_state(chat_id, st)
        self._next_question(chat_id, st)

    @staticmethod
    def _looks_question(text: str) -> bool:
        """短、带问号或疑问词 —— 大概率是在问，不是在答。"""
        t = (text or "").strip()
        if not t or len(t) > 40:
            return False
        if t.endswith(("？", "?")):
            return True
        return any(w in t for w in ("什么", "为什么", "怎么", "如何", "啥意思",
                                    "解释", "不懂", "没懂", "区别", "是不是", "吗"))

    def _quiz_side_question(self, chat_id, st: dict, text: str) -> None:
        """答简答题时插问一句：先答疑，再把原题递回去，不把提问记成答案。"""
        t = self.db.task(int(st["task_id"]))
        conv = chat.conversation(self.db, t) if t else None
        try:
            res = chat.reply(self.db, conv, text) if conv else {"text": "", "sources": []}
        except llm.LLMError as e:
            return self.tg.send(chat_id, f"⚠️ 模型没连上：{esc(e)}")
        msg = esc(res.get("text") or "")
        if res.get("sources"):
            src = "、".join(f"《{esc(s['src'][:24])}》{esc(s['anchor'])}"
                            for s in res["sources"][:2])
            msg += f"\n<i>参考：{src}</i>"
        q = self.db.quiz(st["awaiting"])
        if q:
            msg += (f"\n\n<i>第 {st['idx'] + 1}/{st['total']} 题还在等你："
                    f"{esc(q['stem'])}</i>")
        self.tg.send(chat_id, msg, force_reply=True)

    def _resolve_task(self, task_id: int | None, goal_id: str) -> dict | None:
        """答题途中计划被重排，原来的 task 行会被删掉（id 会变）。
        这里按「今天 + 目标」重新找一次，实在找不到就补一条已完成的记录，
        绝不能让一次通过的考试因为 id 变了而白考。"""
        t = self.db.task(int(task_id)) if task_id else None
        if t:
            return t
        t = self.db.q1("SELECT * FROM tasks WHERE date=? AND goal_id=? ORDER BY id DESC LIMIT 1",
                       (today_s(), goal_id))
        if t:
            return t
        cid = (self.db.goal(goal_id) or {}).get("course") or "ds"
        self.db.x("INSERT OR IGNORE INTO tasks(date,course,goal_id,title,kind,minutes,state,"
                  "created_at) VALUES(?,?,?,?,?,?,'done',?)",
                  (today_s(), cid, goal_id, goal_id, "learn", 0,
                   now().strftime("%Y-%m-%d %H:%M:%S")))
        return self.db.q1("SELECT * FROM tasks WHERE date=? AND goal_id=? ORDER BY id DESC LIMIT 1",
                          (today_s(), goal_id))

    def _finish(self, chat_id, st: dict) -> None:
        right, total = st["right"], st["total"]
        t = self._resolve_task(st.get("task_id"), st["goal_id"])
        self.db.set_bot_state(chat_id, {})
        if right >= 2:
            if t:
                self.db.set_task_state(t["id"], "done")
            self.db.log(f"通过 {st['goal_id']}（{right}/{total}）", "ok")
            s = self.db.stats()
            self.tg.send(chat_id, (
                f"<b>✅ 通过（{right}/{total}）</b>\n"
                f"「{esc(t['title'] if t else st['goal_id'])}」记为完成。\n"
                f"今天还剩 {s['pending']} 张。"),
                buttons=[[("← 今日任务", "m:today")]])
        else:
            info = engine.requeue_after_fail(self.db, t, right, total) if t else {}
            streak = int(info.get("streak") or 0)
            if streak >= 2:
                tail = "连续两次都没过，别再刷题了：明天排了一次回炉，先把这段讲懂再考。"
                btns = [[("← 今日", "m:today")]]
            elif info.get("added"):
                tail = "明天已排一张错题重做（15 分钟），想现在就再来一次也行。"
                btns = [[("🔁 换套题再考", f"r:{t['id']}"), ("← 今日", "m:today")]]
            else:
                tail = "这张卡不算完成，明天重排会把它补进来。"
                btns = [[("🔁 换套题再考", f"r:{t['id'] if t else st.get('task_id', 0)}"),
                         ("← 今日", "m:today")]]
            self.tg.send(chat_id, f"<b>❌ 没过（{right}/{total}）</b>\n{tail}", buttons=btns)

    # --- 按钮
    def _on_callback(self, cb: dict) -> None:
        chat_id = cb.get("message", {}).get("chat", {}).get("id")
        data = cb.get("data") or ""
        self.tg.ack(cb.get("id", ""))
        if not chat_id or not self._chat_ok(chat_id):
            return
        try:
            kind, rest = data.split(":", 1)
        except ValueError:
            return
        if kind == "t":
            self.open_task(chat_id, int(rest))
        elif kind == "q":
            self.start_quiz(chat_id, int(rest))
        elif kind == "r":
            self.start_quiz(chat_id, int(rest), regen=True)
        elif kind == "a":
            qid, pick = rest.split(":")
            self._on_choice(chat_id, int(qid), int(pick))
        elif kind == "m":
            self.cmd_today(chat_id)

    # --- 主动推送
    def push_today(self) -> bool:
        chat_id = self.db.get("chat_id", "")
        if not chat_id:
            self.db.log("还没绑定 Telegram：先在手机上和 bot 说一句 /start", "warn")
            return False
        tasks = [t for t in self.db.today_tasks() if t["state"] == "todo"]
        rows = [[(f"▶ {t['title'][:32]}", f"t:{t['id']}")] for t in tasks[:6]]
        self.tg.send(chat_id, engine.today_digest(self.db), buttons=rows or None)
        self.db.set("last_push", now().strftime("%Y-%m-%d %H:%M:%S"))
        self.db.log(f"已推送今日任务（{len(tasks)} 张）", "ok")
        return True

    def push_reminder(self) -> bool:
        chat_id = self.db.get("chat_id", "")
        if not chat_id:
            return False
        s = self.db.stats()
        if s["today_total"] == 0 or s["pending"] == 0:
            return False            # 今天没排卡，或者已经全做完了，不用催
        pending = s["pending"]
        self.tg.send(chat_id, f"今天还剩 {pending} 张没做。要不要挑最短的先做掉？",
                     buttons=[[("▶ 开始", "m:today")]])
        self.db.log(f"已发晚间提醒（还剩 {pending} 张）", "ok")
        return True
