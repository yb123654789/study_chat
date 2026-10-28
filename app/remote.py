#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""瘦客户端：把 DB / Service 变成对服务器 HTTP API 的代理。

桌面端其它代码（ui / engine / chat）不用知道数据在远端，
它们看到的还是同一组方法，只是底下走网络。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class RemoteError(RuntimeError):
    pass


class RemoteDB:
    remote = True
    path = "remote"

    def __init__(self, base: str, token: str = "", timeout: int = 120):
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._state_cache: dict | None = None
        self._state_at = 0.0

    # ---------------------------------------------------------------- 传输
    def call(self, method: str, path: str, data: dict | None = None,
             query: dict | None = None) -> dict:
        url = f"{self.base}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        body = json.dumps(data or {}, ensure_ascii=False).encode("utf-8") if method == "POST" else None
        req = urllib.request.Request(url, data=body, method=method, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RemoteError(f"{method} {path} → {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RemoteError(f"连不上服务器（{e.reason}）——检查地址和网络") from e
        except TimeoutError as e:
            raise RemoteError(f"{path} 超时") from e
        try:
            j = json.loads(raw)
        except ValueError as e:
            raise RemoteError(f"服务器返回的不是 JSON：{raw[:120]}") from e
        if isinstance(j, dict) and j.get("error"):
            raise RemoteError(str(j["error"]))
        if method == "POST" and path.rstrip("/") == "/api/settings":
            self.invalidate()          # 设置改完立刻能读回新值
        return j

    def action(self, name: str, **kw) -> dict:
        return self.call("POST", f"/api/{name}", kw)

    # ---------------------------------------------------------------- 状态
    def state(self, max_age: float = 1.5) -> dict:
        if self._state_cache is None or time.time() - self._state_at > max_age:
            self._state_cache = self.call("GET", "/api/state")
            self._state_at = time.time()
        return self._state_cache

    def invalidate(self) -> None:
        self._state_cache = None

    # ---------------------------------------------------------------- 设置
    def get(self, key: str, default: str = "") -> str:
        s = self.state()["settings"]
        if key in ("api_key", "tg_token"):
            flag = "api_key_set" if key == "api_key" else "tg_token_set"
            return "****" if s.get(flag) else ""
        v = s.get(key, default)
        return default if v is None else str(v)

    def geti(self, key: str, default: int = 0) -> int:
        try:
            return int(float(self.get(key, str(default))))
        except (TypeError, ValueError):
            return default

    def set(self, key: str, value) -> None:
        self.call("POST", "/api/settings", {"settings": {key: str(value)}})
        self.invalidate()

    # ---------------------------------------------------------------- 日志
    def log(self, text: str, level: str = "info") -> None:
        try:
            self.call("POST", "/api/log", {"text": text, "level": level})
        except RemoteError:
            pass          # 日志写不进去不能影响界面

    def logs(self, limit: int = 300) -> list[dict]:
        return self.call("GET", "/api/logs", query={"limit": limit})["logs"]

    def clear_logs(self, keep: int = 0) -> int:
        return int(self.call("POST", "/api/logs/clear", {"keep": int(keep)})["cleared"])

    # ---------------------------------------------------------------- 课程
    def courses(self, include_archived: bool = False) -> list[dict]:
        rows = self.state()["courses"]
        return rows if include_archived else [c for c in rows if not c.get("archived")]

    def course(self, cid: str) -> dict | None:
        return next((c for c in self.courses(True) if c["id"] == cid), None)

    def save_course(self, cid: str, **kw) -> dict:
        r = self.call("POST", "/api/course", {"id": cid, **kw})
        self.invalidate()
        return r["course"]

    def current_course(self) -> str:
        cid = self.get("course", "")
        if cid and self.course(cid):
            return cid
        rows = self.courses()
        return rows[0]["id"] if rows else "ds"

    def stats_by_course(self) -> list[dict]:
        return self.state()["stats_by_course"]

    # ---------------------------------------------------------------- 目标
    def import_goals(self, path: Path | None = None, course: str = "ds") -> int:
        import paths
        p = Path(path or paths.goals_file(course))
        if not p.exists():
            p = paths.goals_json()
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
        r = self.call("POST", "/api/import_goals", {"goals": raw, "course": course})
        self.invalidate()
        return int(r.get("n") or 0)

    def goals(self, include_done: bool = True, course: str | None = None) -> list[dict]:
        j = self.call("GET", "/api/goals")
        rows = j["goals"]
        if course:
            rows = [g for g in rows if (g.get("course") or "ds") == course]
        if not include_done:
            done = set(j.get("done") or [])
            rows = [g for g in rows if g["id"] not in done]
        return rows

    def goal(self, gid: str) -> dict | None:
        return self.call("GET", "/api/goal", query={"id": gid})["goal"]

    def done_goal_ids(self, course: str | None = None) -> set[str]:
        j = self.call("GET", "/api/goals")
        done = set(j.get("done") or [])
        if not course:
            return done
        return {g["id"] for g in j["goals"]
                if (g.get("course") or "ds") == course and g["id"] in done}

    # ---------------------------------------------------------------- 任务
    def tasks_between(self, date_from: str, date_to: str,
                      states: tuple[str, ...] | None = None,
                      course: str | None = None) -> list[dict]:
        rows = self.call("GET", "/api/tasks",
                         query={"from": date_from, "to": date_to})["tasks"]
        if course:
            rows = [t for t in rows if (t.get("course") or "ds") == course]
        return rows

    def today_tasks(self, course: str | None = None) -> list[dict]:
        rows = self.state()["tasks"]
        if course:
            rows = [t for t in rows if (t.get("course") or "ds") == course]
        return rows

    def task(self, tid: int) -> dict | None:
        return self.call("GET", "/api/task", query={"id": int(tid)})["task"]

    def set_task_state(self, tid: int, state: str) -> None:
        self.call("POST", "/api/task/state", {"id": int(tid), "state": state})
        self.invalidate()

    def add_task(self, date: str, goal_id: str, title: str,
                 kind: str = "learn", minutes: int = 15) -> dict:
        r = self.call("POST", "/api/task/add", {"date": date, "goal_id": goal_id,
                                                "title": title, "kind": kind,
                                                "minutes": int(minutes)})
        self.invalidate()
        return r

    def fail_streak(self, goal_id: str) -> int:
        return int(self.call("GET", "/api/fail_streak", query={"goal_id": goal_id})["n"])

    def lag_days(self, course: str | None = None) -> int:
        q = {"course": course} if course else None
        return int(self.call("GET", "/api/lag_days", query=q)["n"])

    # ---------------------------------------------------------------- 待确认
    def review_items(self, pending_only: bool = True, limit: int = 200) -> list[dict]:
        rows = self.call("GET", "/api/review",
                         query={"pending": "1" if pending_only else "0"})["review"]
        return rows[:limit]

    def review_pending_count(self) -> int:
        return int(self.state()["review_pending"])

    def review_resolve(self, rid: int, resolution: str) -> dict:
        r = self.call("POST", "/api/review/resolve",
                      {"id": int(rid), "resolution": resolution})
        self.invalidate()           # 卡片状态可能变了，下次读要新数据
        return r["row"]

    # ---------------------------------------------------------------- 手机事件
    def mobile_events(self, events: list[dict], device: str = "desktop") -> dict:
        r = self.call("POST", "/api/mobile/events", {"device": device, "events": events})
        self.invalidate()
        return r

    # ---------------------------------------------------------------- 题库
    def quiz(self, qid: int) -> dict | None:
        return self.call("GET", "/api/quiz", query={"id": int(qid)})["quiz"]

    def quiz_batch(self, goal_id: str) -> list[dict]:
        return self.call("GET", "/api/quiz/batch", query={"goal_id": goal_id})["quiz"]

    def all_quiz(self, limit: int = 500) -> list[dict]:
        return self.call("GET", "/api/quiz/all")["quiz"][:limit]

    def add_attempt(self, task_id, goal_id, quiz_id, answer: str,
                    passed: int, feedback: str = "") -> None:
        self.call("POST", "/api/attempt", {"task_id": task_id, "goal_id": goal_id,
                                           "quiz_id": quiz_id, "answer": answer,
                                           "passed": passed, "feedback": feedback})

    def attempts_for_goal(self, goal_id: str, limit: int = 20) -> list[dict]:
        return self.call("GET", "/api/attempts",
                         query={"goal_id": goal_id, "limit": limit})["attempts"]

    # ---------------------------------------------------------------- 资料
    def materials(self) -> list[dict]:
        return self.call("GET", "/api/materials")["materials"]

    def material_known(self, path: str) -> dict | None:
        return self.call("GET", "/api/material/known", query={"path": path})["row"]

    # ---------------------------------------------------------------- 统计
    def stats(self, course: str | None = None) -> dict:
        if course:
            for s in self.state()["stats_by_course"]:
                if s.get("course") == course:
                    return s
        return self.state()["stats"]

    # ---------------------------------------------------------------- 聊天
    def conversation(self, task_id: int | None = None, goal_id: str = "",
                     kind: str = "task", title: str = "") -> dict:
        return self.call("GET", "/api/conv", query={"task_id": task_id})["conv"]

    def conv(self, conv_id: int) -> dict | None:
        return self.conversation(None) if not conv_id else self._conv_by_id(conv_id)

    def _conv_by_id(self, conv_id: int) -> dict | None:
        return None                     # 目前只按 task 找会话

    def conv_messages(self, conv_id: int, limit: int = 20) -> list[dict]:
        return self.call("GET", "/api/conv/messages",
                         query={"id": int(conv_id), "limit": limit})["messages"]

    def clear_messages(self, conv_id: int) -> None:
        self.call("POST", "/api/conv/reset", {"id": int(conv_id)})

    def add_message(self, *a, **k):
        raise RemoteError("聊天消息由服务器写，客户端不要直接写")

    def conv_tokens(self, conv_id: int) -> int:
        return 0

    def touch_conv(self, conv_id: int) -> None:
        pass


class RemoteService:
    """桌面端的 bot 控制面板：所有启停都打到服务器上。"""

    def __init__(self, db: RemoteDB):
        self.db = db
        self.bot = self                # 兼容旧调用（如 svc.bot.push_today()，P0 后界面已不调用）
        self.username = ""
        self._st: dict = {}
        self._at = 0.0

    @property
    def status(self) -> str:  # type: ignore[override]
        return self._status().get("status", "error")

    @property
    def running(self) -> bool:
        return self.status == "running"
    def _status(self) -> dict:
        if time.time() - self._at > 2:
            try:
                self._st = self.db.call("GET", "/api/bot/status")
                self.username = self._st.get("username", "")
                self._at = time.time()
            except RemoteError:
                self._st = {"status": "error", "text": "连不上服务器"}
        return self._st

    def status_text(self) -> str:
        return self._status().get("text", "")

    def start(self, quiet: bool = False) -> None:
        self.db.call("POST", "/api/bot/start")
        self._at = 0

    def stop(self) -> None:
        self.db.call("POST", "/api/bot/stop")
        self._at = 0

    def shutdown(self) -> None:
        pass                            # 桌面关掉不影响服务器上的 bot

    def push_today(self) -> bool:
        return bool(self.db.call("POST", "/api/bot/push").get("ok"))

    def test(self, token: str, proxy: str = "") -> dict:
        return self.db.call("POST", "/api/bot/test", {"token": token, "proxy": proxy})
