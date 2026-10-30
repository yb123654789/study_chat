#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP API：桌面端通过它读写云端的库。

设计约束：
- 只用标准库（http.server / json），不装依赖。
- 所有写操作都落到服务器上的同一个 SQLite，bot 和桌面看到的是同一份数据。
- token 走 Authorization: Bearer，未配置 token 时拒绝服务（不裸奔）。
"""
from __future__ import annotations

import hashlib
import json
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import chat
import engine
import llm
import paths

DB = None
SVC = None
TOKEN = ""

# 可以下发给桌面端的设置项（token / api_key 永不下发）
SAFE_KEYS = ("exam_date", "subject", "weekend_ratio", "day_cap",
             "notify_time", "remind_time", "model", "chat_id", "plan_generated_at",
             "course")


def configure(db, svc, token: str) -> None:
    global DB, SVC, TOKEN
    DB = db
    SVC = svc
    TOKEN = token


# ------------------------------------------------------------------ 读
def _settings(db) -> dict:
    out = {k: db.get(k) for k in SAFE_KEYS}
    out["plan_note"] = db.get("plan_note")
    out["api_key_set"] = bool(llm.api_key(db))
    out["tg_token_set"] = bool(db.get("tg_token"))
    return out


def _bot_status() -> dict:
    if not SVC:
        return {"status": "stopped", "username": "", "text": "服务端没启动 bot 模块"}
    return {"status": SVC.status, "username": SVC.username,
            "text": SVC.status_text(), "error": SVC.last_error}


def _state(db) -> dict:
    courses = db.courses()
    return {"settings": _settings(db), "stats": db.stats(),
            "stats_by_course": db.stats_by_course(), "courses": courses,
            "bot": _bot_status(), "tasks": db.today_tasks(),
            "plan_note": db.get("plan_note"),
            "review_pending": db.review_pending_count()}


def _courses(db, _req) -> dict:
    return {"courses": db.courses(include_archived=True),
            "notes": {c["id"]: db.get(f"plan_note:{c['id']}") for c in db.courses()}}


def _save_course(db, req) -> dict:
    cid = str(req.get("id") or "").strip()
    if not cid:
        raise ValueError("课程 id 不能为空")
    fields = {k: req[k] for k in ("name", "exam_date", "weekend_ratio", "day_cap",
                                  "archived") if k in req}
    return {"course": db.save_course(cid, **fields), "courses": db.courses()}


# ------------------------------------------------------------------ 动作
def _import_goals(db, req) -> dict:
    """桌面端把 goals.json 的内容传上来，服务器负责落库（资料在用户电脑上）。"""
    raw = req.get("goals") or req.get("data")
    if not raw:
        raise ValueError("没有 goals 内容")
    course = str(req.get("course") or "ds")
    import tempfile
    from pathlib import Path
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False)
        tmp = Path(f.name)
    try:
        n = db.import_goals(tmp, course=course)
    finally:
        tmp.unlink(missing_ok=True)
    return {"n": n}


def _upload_material(db, req) -> dict:
    """桌面端在本地抽好文本传上来，服务器只存 + 让检索缓存失效。"""
    from pathlib import Path
    rel = str(req.get("path") or "").strip()
    text = req.get("text") or ""
    h = str(req.get("hash") or "")
    course = str(req.get("course") or "")
    if not rel or not text.strip():
        raise ValueError("path / text 不能为空")
    base_out = paths.extracted_dir(course) if course and course != "ds" \
        else paths.extracted_dir()
    out = base_out / "inbox"
    out.mkdir(parents=True, exist_ok=True)
    dst = out / (Path(rel).stem + ".txt")
    dst.write_text(text, encoding="utf-8")
    db.add_material(rel, h, len(text), "ready")
    db.log(f"入库 {Path(rel).name}：{len(text):,} 字符", "ok")
    try:
        import retrieval as _r
        _r._BLOCKS = None          # 新资料立刻可检索
    except Exception:  # noqa: BLE001
        pass
    return {"chars": len(text)}


def _chat(db, req) -> dict:
    tid = req.get("task_id")
    t = db.task(int(tid)) if tid else None
    if not t:
        raise ValueError("这张卡找不到了")
    conv = chat.conversation(db, t)

    def run(name: str, args: dict) -> str:
        if name == "start_quiz":
            engine.ensure_quiz(db, t["goal_id"])
            return "小测已生成：3 题，答对 2 题算过。"
        if name == "skip_task":
            db.set_task_state(t["id"], "skipped")
            return "已跳过这张卡，明天重排会挪回来。"
        if name == "show_plan":
            return db.get("plan_note") or "还没生成计划"
        return "这个动作现在做不了"

    res = chat.reply(db, conv, req.get("text", ""), handlers=run)
    res["conv_id"] = conv["id"]
    res["handled"] = True          # 状态已经由服务器改了，客户端只管显示
    return res


def _grade(db, req) -> dict:
    q = db.quiz(int(req["quiz_id"]))
    if not q:
        raise ValueError("没有这道题")
    goal = db.goal(req.get("goal_id") or q["goal_id"]) or {}
    return engine.grade_one(db, goal, q, req.get("answer", ""))


def _regen_allowed(db, req) -> dict:
    return {"ok": engine.regen_allowed(db, req.get("goal_id", ""))}


def _missing(db, _req) -> dict:
    return {"text": engine.missing_material(db)}


def _lag(db, req) -> dict:
    return {"n": chat.lag_days(db, req.get("course") or None)}


def _review(db, req) -> dict:
    """待确认列表：默认只给没处理的；pending 是总数（桌面左下角显示用）。"""
    pending_only = str(req.get("pending", "1")).lower() not in ("0", "false", "no")
    return {"review": db.review_items(pending_only=pending_only),
            "pending": db.review_pending_count()}


def _review_resolve(db, req) -> dict:
    """resolution 只能是 follow_server（听电脑）/ keep_phone（保留手机）。

    写完立刻生效（卡片状态按选的那边改），下次同步才带回手机。
    """
    return {"row": db.review_resolve(int(req["id"]),
                                     str(req.get("resolution") or "").strip()),
            "pending": db.review_pending_count()}


# ------------------------------------------------------------------ 手机同步
# 手机端下行快照（方案 §7.1）：四页数据 + 会话 + 待确认 + 资料块增量。
# rev 只在内容变了才前进；blocks_rev 是全体块指纹的指纹，块没变就不重发。

def _json_sig(obj) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _block_index() -> tuple[str, list[dict]]:
    """资料块：id 由「文件+锚点」定（位置稳定），fp = sha1(text)[:16]。

    块内容改了 → 同 id 新 fp；块没了 → 客户端按 id 删。blocks_rev = 全体指纹的 sha1。
    """
    try:
        import retrieval
        raw = retrieval._ensure()[0]
    except Exception:                 # 没资料 / 抽文本工具不可用：空块，别让同步 500
        raw = []
    pairs, out = [], []
    for b in raw:
        rel = b.get("rel") or ""
        anchor = b.get("anchor") or ""
        text = b.get("text") or ""
        bid = hashlib.sha1(f"{rel}|{anchor}".encode("utf-8")).hexdigest()[:16]
        fp = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        pairs.append(f"{bid}:{fp}")
        out.append({"id": bid, "fp": fp, "course": b.get("course") or "ds",
                    "src": b.get("src") or "", "anchor": anchor, "text": text})
    pairs.sort()
    out.sort(key=lambda x: x["id"])
    rev = hashlib.sha1("|".join(pairs).encode("utf-8")).hexdigest()[:16]
    return rev, out


def _quiz_stats(db) -> list[dict]:
    """每目标最新一批的对错与连错次数（不含题干，方案 §6）。"""
    out = []
    for r in db.q("SELECT goal_id, MAX(batch) AS batch FROM quiz GROUP BY goal_id"):
        gid = r["goal_id"]
        batch = int(r["batch"] or 1)
        a = db.q1("SELECT COUNT(*) AS n, COALESCE(SUM(a.passed),0) AS s "
                  "FROM attempts a JOIN quiz q ON q.id=a.quiz_id "
                  "WHERE q.goal_id=? AND q.batch=?", (gid, batch)) or {}
        out.append({"goal_id": gid, "batch": batch,
                    "answered": int(a.get("n") or 0),
                    "passed": int(a.get("s") or 0),
                    "fail_streak": db.fail_streak(gid)})
    out.sort(key=lambda x: x["goal_id"])
    return out


def _apply_event(db, ev) -> dict:
    """一条手机事件落库；返回 {ok, needs_review?, review_id?}。"""
    kind = str(ev.get("kind") or "")
    if kind == "task_state":
        tid = ev.get("task_id")
        state = str(ev.get("state") or "")
        row = db.task(int(tid)) if tid else None
        if not row:
            return {"ok": True, "note": "这张卡已经不在计划里"}
        if row["state"] in ("skipped", "dropped") and state and state != row["state"]:
            rid = db.review_add(
                kind="task_state", task_id=row["id"], goal_id=row["goal_id"], day=row["date"],
                phone_state=state, server_state=row["state"],
                reason=f"这张卡电脑已改成 {row['state']}，手机报 {state}", payload=ev)
            return {"ok": True, "needs_review": True, "review_id": rid}
        if state == "done":
            passed = int(ev.get("quiz_passed") or 0)
            if passed < 2:
                rid = db.review_add(
                    kind="task_state", task_id=row["id"], goal_id=row["goal_id"], day=row["date"],
                    phone_state="done", server_state=row["state"],
                    reason=f"手机判完成，但只答对 {passed} 题；按规则不能算完成", payload=ev)
                return {"ok": True, "needs_review": True, "review_id": rid}
            db.set_task_state(row["id"], "done")
            return {"ok": True}
        db.set_task_state(row["id"], state)
        return {"ok": True}

    if kind == "message":
        tid = ev.get("task_id")
        t = db.task(int(tid)) if tid else None
        if t:
            conv = chat.conversation(db, t)
        else:
            # 手机只剩一个自由聊天窗：全部并到同一条会话，别每条消息新建一条
            conv = db.q1("SELECT * FROM conversations WHERE kind='free' ORDER BY id LIMIT 1")
            if not conv:
                conv = db.conversation(None, kind="free", title="自由问答")
        db.add_message(int(conv["id"]), "me", str(ev.get("text") or ""))
        return {"ok": True, "conv_id": conv["id"]}

    if kind == "attempt":
        db.add_attempt(ev.get("task_id"), ev.get("goal_id"), ev.get("quiz_id"),
                       str(ev.get("answer") or ""), int(ev.get("passed") or 0),
                       str(ev.get("feedback") or ""))
        return {"ok": True}

    if kind == "quiz":
        gid = str(ev.get("goal_id") or "")
        if not gid:
            return {"ok": False, "note": "quiz 事件缺 goal_id"}
        batch = int(ev.get("batch") or db.next_batch_no(gid))
        n = 0
        for q in (ev.get("questions") or []):
            if isinstance(q, dict):
                db.add_quiz(gid, batch, q, source="phone")
                n += 1
        return {"ok": True, "n": n}

    if kind == "replan":
        # 手机落后重排的结果只是「建议」：进待确认，**不动服务器 tasks**
        tasks = [t for t in (ev.get("tasks") or []) if isinstance(t, dict)]
        if not tasks:
            return {"ok": False, "note": "replan 事件没有可应用的卡"}
        from db import today_s        # 和 db 的「今天」一致（固定 UTC+8，不跟着 VPS 时区）
        note = str(ev.get("note") or "手机重排")
        gid = str(tasks[0].get("goal_id") or "")     # 只用于待确认页显示目标名
        rid = db.review_add(
            kind="replan", goal_id=gid, day=today_s(),
            phone_state="replan", server_state="current",
            reason=note, payload={"tasks": tasks})
        return {"ok": True, "needs_review": True, "review_id": rid}

    if kind == "arbitration":
        # 手机聊天里的冲突裁定（P7）。服务器没有 arbitrations 表，用 settings 的
        # arb:<uid> 存整条 payload；同一个 uid 重传不重复写。
        uid = str(ev.get("uid") or "").strip()
        payload = {
            "uid": uid,
            "course": str(ev.get("course") or ""),
            "goal_id": str(ev.get("goal_id") or ""),
            "concept": str(ev.get("concept") or ""),
            "material_claim": str(ev.get("material_claim") or ""),
            "material_src": str(ev.get("material_src") or ""),
            "note_id": str(ev.get("note_id") or ""),
            "note_claim": str(ev.get("note_claim") or ""),
            "verdict": str(ev.get("verdict") or ""),
            "decided_at": str(ev.get("decided_at") or ""),
            "decided_by": str(ev.get("decided_by") or "user"),
            "hits": int(ev.get("hits") or 1),
        }
        key = f"arb:{uid}"
        if db.get(key):
            return {"ok": True, "dup": True, "key": key}
        db.set(key, json.dumps(payload, ensure_ascii=False))
        db.log(f"手机裁定：{payload['concept'] or '（无概念）'} → "
               f"{'按我的笔记' if payload['verdict'] == 'note' else '按资料'}", "info")
        return {"ok": True, "key": key}

    return {"ok": True, "note": f"未知事件类型 {kind}，只记下来"}


def _mobile_events(db, req) -> dict:
    device = str(req.get("device") or "")
    events = req.get("events") or []
    accepted, needs, ids = 0, 0, []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        uid = str(ev.get("uid") or "").strip()
        if not uid or db.event_seen(uid):
            continue                    # 同一条重传：不重复写
        try:
            res = _apply_event(db, ev)
        except Exception as e:          # noqa: BLE001 单条坏事件不能拖垮整批
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        db.event_save(uid, device, str(ev.get("kind") or ""), ev,
                      json.dumps(res, ensure_ascii=False))
        accepted += 1
        if res.get("needs_review"):
            needs += 1
            ids.append(res.get("review_id"))
    return {"accepted": accepted, "needs_review": needs, "review_ids": ids,
            "rev": int(db.geti("mobile_rev", 0))}


def _mobile_snapshot(db, req) -> dict:
    from datetime import date, timedelta
    today = date.today()
    # 方案 §6：计划卡片给过去 7 天 + 未来 14 天；不推全部历史
    tasks = db.tasks_between((today - timedelta(days=7)).isoformat(),
                             (today + timedelta(days=14)).isoformat())
    goals = db.goals()
    courses = db.courses(include_archived=True)
    done = sorted(db.done_goal_ids())
    quiz_stats = _quiz_stats(db)
    review = db.review_items()
    convs = db.q("SELECT * FROM conversations "
                 "ORDER BY COALESCE(last_active,created_at) DESC, id DESC LIMIT 20")
    messages: list[dict] = []
    for c in convs:
        for m in db.conv_messages(int(c["id"]), 50):
            m["conv_id"] = c["id"]
            messages.append(m)

    rev = db.mobile_rev(_json_sig([courses, goals, done, tasks, quiz_stats,
                                   review, convs, messages]))
    blocks_rev, blocks = _block_index()
    client_blocks = str(req.get("blocks_rev") or "")
    full = not str(req.get("rev") or "")
    out = {"rev": rev, "full": full, "blocks_rev": blocks_rev,
           "courses": courses, "goals": goals, "done": done, "tasks": tasks,
           "quiz_stats": quiz_stats, "conversations": convs, "messages": messages,
           "review": review, "notes": [], "arbitrations": [],
           "blocks_deleted": []}
    if full or client_blocks != blocks_rev:
        out["blocks"] = blocks       # 首次全量；块没变就不带，手机不用重传
    return out


# ------------------------------------------------------------------ 路由
def routes() -> dict:
    d = DB
    return {
        ("GET", "/api/health"): lambda r: {"ok": True, "ts": d.get("plan_generated_at")},
        ("GET", "/api/state"): lambda r: _state(d),
        ("GET", "/api/settings"): lambda r: _settings(d),
        ("POST", "/api/settings"): lambda r: _set_settings(d, r),
        ("GET", "/api/today"): lambda r: {"tasks": d.today_tasks()},
        ("GET", "/api/tasks"): lambda r: {"tasks": d.tasks_between(
            r.get("from") or "0000-01-01", r.get("to") or "9999-12-31")},
        ("GET", "/api/task"): lambda r: {"task": d.task(int(r["id"]))},
        ("POST", "/api/task/state"): lambda r: _task_state(d, r),
        ("POST", "/api/task/add"): lambda r: {"id": engine.add_task(
            d, r["date"], r["goal_id"], r.get("title", ""), r.get("kind", "learn"),
            int(r.get("minutes") or 15))},
        ("GET", "/api/fail_streak"): lambda r: {"n": d.fail_streak(r["goal_id"])},
        ("GET", "/api/goals"): lambda r: {"goals": d.goals(), "done": sorted(d.done_goal_ids())},
        ("GET", "/api/goal"): lambda r: {"goal": d.goal(r["id"])},
        ("GET", "/api/courses"): lambda r: _courses(d, r),
        ("POST", "/api/course"): lambda r: _save_course(d, r),
        ("GET", "/api/quiz"): lambda r: {"quiz": d.quiz(int(r["id"]))},
        ("GET", "/api/quiz/all"): lambda r: {"quiz": d.all_quiz()},
        ("GET", "/api/quiz/batch"): lambda r: {"quiz": d.quiz_batch(r["goal_id"])},
        ("POST", "/api/quiz/ensure"): lambda r: {"quiz": engine.ensure_quiz(
            d, r["goal_id"], bool(r.get("force_new")))},
        ("POST", "/api/quiz/grade"): lambda r: _grade(d, r),
        ("GET", "/api/quiz/regen_allowed"): lambda r: _regen_allowed(d, r),
        ("POST", "/api/attempt"): lambda r: _attempt(d, r),
        ("GET", "/api/attempts"): lambda r: {"attempts": d.attempts_for_goal(
            r["goal_id"], int(r.get("limit") or 20))},
        ("POST", "/api/replan"): lambda r: engine.regenerate(d, course=r.get("course") or None),
        ("POST", "/api/import_goals"): lambda r: _import_goals(d, r),
        ("POST", "/api/goals/derive"): lambda r: {"result": engine.derive_goals(
            d, course=r.get("course") or None)},
        ("GET", "/api/logs"): lambda r: {"logs": d.logs(int(r.get("limit") or 200))},
        ("POST", "/api/logs/clear"): lambda r: {"cleared": d.clear_logs(int(r.get("keep") or 0))},
        ("POST", "/api/log"): lambda r: _log(d, r),
        ("GET", "/api/materials"): lambda r: {"materials": d.materials()},
        ("GET", "/api/material/known"): lambda r: {"row": d.material_known(r["path"])},
        ("POST", "/api/materials/upload"): lambda r: _upload_material(d, r),
        ("GET", "/api/missing_material"): lambda r: _missing(d, r),
        ("GET", "/api/lag_days"): lambda r: _lag(d, r),
        ("GET", "/api/review"): lambda r: _review(d, r),
        ("POST", "/api/review/resolve"): lambda r: _review_resolve(d, r),
        ("GET", "/api/mobile/snapshot"): lambda r: _mobile_snapshot(d, r),
        ("POST", "/api/mobile/events"): lambda r: _mobile_events(d, r),
        ("GET", "/api/conv"): lambda r: {"conv": _conv(d, r)},
        ("GET", "/api/conv/messages"): lambda r: {"messages": d.conv_messages(
            int(r["id"]), int(r.get("limit") or 50))},
        ("POST", "/api/conv/reset"): lambda r: _conv_reset(d, r),
        ("POST", "/api/chat"): lambda r: _chat(d, r),
        ("POST", "/api/bot/start"): lambda r: _bot(d, r, "start"),
        ("POST", "/api/bot/stop"): lambda r: _bot(d, r, "stop"),
        ("POST", "/api/bot/test"): lambda r: _bot_test(d, r),
        ("POST", "/api/bot/push"): lambda r: {"ok": bool(SVC and SVC.bot
                                                       and SVC.bot.push_today())},
        ("GET", "/api/bot/status"): lambda r: _bot_status(),
    }


def _set_settings(db, req) -> dict:
    changed = []
    for k, v in (req.get("settings") or {}).items():
        if k in ("api_key", "tg_token") and v == "****":
            continue                      # 掩码表示「不改」
        db.set(k, v)
        changed.append(k)
    db.log(f"桌面端改了设置：{'、'.join(changed) or '无'}", "info")
    return {"saved": changed}


def _task_state(db, req) -> dict:
    db.set_task_state(int(req["id"]), req["state"])
    return {"ok": True}


def _attempt(db, req) -> dict:
    db.add_attempt(req.get("task_id"), req.get("goal_id"), req.get("quiz_id"),
                   req.get("answer", ""), int(req.get("passed") or 0),
                   req.get("feedback", ""))
    return {"ok": True}


def _log(db, req) -> dict:
    db.log(req.get("text", ""), req.get("level", "info"))
    return {"ok": True}


def _conv(db, req) -> dict:
    t = db.task(int(req["task_id"])) if req.get("task_id") else None
    return chat.conversation(db, t) if t else {}

def _conv_reset(db, req) -> dict:
    c = db.conv(int(req["id"]))
    if c:
        chat.reset(db, c)
    return {"ok": True}


def _bot(db, req, what: str) -> dict:
    if not SVC:
        raise ValueError("服务端没有 bot 模块")
    if what == "start":
        SVC.start()
    else:
        SVC.stop()
    return _bot_status()


def _bot_test(db, req) -> dict:
    from bot import TgClient
    tok = req.get("token") or db.get("tg_token")
    if not tok or tok == "****":
        tok = db.get("tg_token")
    proxy = req.get("proxy") or db.get("tg_proxy") or ""
    return TgClient(tok, proxy=proxy).get_me()


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "StudyBot/1.0"

    def log_message(self, fmt, *args):        # 别把每个请求都刷到 stderr
        pass

    def _json(self, obj, code: int = 200) -> None:
        raw = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _auth(self) -> bool:
        got = (self.headers.get("Authorization") or "").strip()
        if TOKEN and got == f"Bearer {TOKEN}":
            return True
        self._json({"error": "未授权：token 不对"}, 401)
        return False

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError as e:
            raise ValueError(f"JSON 解析失败：{e}") from e

    def _dispatch(self, method: str) -> None:
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        if path == "/api/health":
            return self._json({"ok": True, "service": "studybot"})
        if not self._auth():
            return
        try:
            req = self._body() if method == "POST" else {}
            req.update({k: v[0] for k, v in parse_qs(u.query).items()})
            fn = routes().get((method, path))
            if not fn:
                return self._json({"error": f"没有这个接口：{method} {path}"}, 404)
            return self._json(fn(req))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")


def serve(bind: str, port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((bind, port), Handler)
    return srv
