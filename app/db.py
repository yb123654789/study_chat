#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据层：本地 SQLite。单人使用，不联网，所有后台数据都在这个文件里。

表：
  settings    键值对（考核日、周末系数、Telegram token、通知时间…）
  goals       学习目标（从 data/goals.json 导入）
  tasks       每天的任务卡（排程产物，也可以手动标记）
  quiz        题库（每个目标一批 3 题）
  attempts    每一次答题记录
  materials   资料入库记录（去重靠 hash）
  bot_state   Telegram 每个会话的当前状态（学哪张卡、第几题）
  review_queue 待确认：手机判定和电脑不一致的卡，等人逐条定
  events      软件内日志
"""
from __future__ import annotations

import hashlib
import json
import queue
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paths

BASE = paths.CODE_ROOT
TZ = timezone(timedelta(hours=8))          # 一律 Asia/Shanghai，不用本地时区

SCHEMA_VERSION = 6                         # 6=手机上行事件（uid 幂等）
                                           # 5=待确认队列 1=基础表 2=聊天 3=多课程 4=容量改成权重


def _data() -> Path:
    """数据目录可能第一次启动才定下来，所以每次现取。"""
    return paths._r()


def now() -> datetime:
    return datetime.now(TZ)


def now_s() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S")


def today_s() -> str:
    return now().date().isoformat()


WEEK_CN = "一二三四五六日"

DEFAULT_WEEKEND_RATIO = 130      # 周末比工作日多学 30%
RATIO_RANGE = (100, 200)         # 周末系数 100%~200%（100 = 工作日周末一样多）
DAY_CAP_MAX = 600                # 每日上限最多 10 小时，防止填错


def ratio_from_minutes(weekday_min, weekend_min) -> int:
    """老库的「工作日/周末分钟」→ 周末系数（%）。换算不出来就用默认 130。"""
    try:
        wd, we = int(weekday_min or 0), int(weekend_min or 0)
    except (TypeError, ValueError):
        return DEFAULT_WEEKEND_RATIO
    if wd > 0 and we > 0:
        return max(RATIO_RANGE[0], min(RATIO_RANGE[1], round(we * 100 / wd)))
    return DEFAULT_WEEKEND_RATIO

DEFAULTS = {
    "exam_date": "2027-01-04",
    "subject": "数据结构",
    "weekend_ratio": "130",   # 周末比工作日多学多少（%）；每天学多少由剩余量÷剩余天数算，不写死
    "day_cap": "0",           # 每天最多多少分钟，0 = 不限
    "notify_time": "07:00",
    "remind_time": "21:30",
    "tg_token": "",
    "chat_id": "",
    "model": "deepseek-flash",
    "plan_note": "",
    "plan_generated_at": "",
    "bot_enabled": "0",
}

# UI 日志总线：任何线程都可以往这里塞一行，界面定时取
EVENTS: "queue.Queue[tuple[str, str, str]]" = queue.Queue()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS goals (
    id    TEXT PRIMARY KEY,
    course TEXT DEFAULT 'ds',
    ch    TEXT DEFAULT '',
    title TEXT NOT NULL,
    p     TEXT DEFAULT 'core',
    m     INTEGER DEFAULT 30,
    d     TEXT DEFAULT '[]',
    cp    TEXT DEFAULT '[]',
    seq   INTEGER DEFAULT 0
);

-- 课程：每门课自己的考核日与容量；数据结构 = 第一门（ds）
-- 每天学多少分钟不存：排程时按「剩余量 ÷ 剩余天数」算。
-- 这里只存「周末比工作日多学多少」（%）和用户可选的每日硬上限（0 = 不限）。
CREATE TABLE IF NOT EXISTS courses (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL DEFAULT '',
    exam_date     TEXT DEFAULT '',
    weekend_ratio INTEGER DEFAULT 130,
    day_cap       INTEGER DEFAULT 0,
    archived      INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,
    course     TEXT DEFAULT 'ds',
    goal_id    TEXT NOT NULL,
    title      TEXT DEFAULT '',
    kind       TEXT DEFAULT 'learn',
    minutes    INTEGER DEFAULT 30,
    state      TEXT DEFAULT 'todo',
    created_at TEXT DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_task ON tasks(date, goal_id, kind);

CREATE TABLE IF NOT EXISTS quiz (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id    TEXT NOT NULL,
    batch      INTEGER DEFAULT 1,
    type       TEXT DEFAULT 'choice',
    stem       TEXT DEFAULT '',
    options    TEXT DEFAULT '[]',
    answer     TEXT DEFAULT '',
    rubric     TEXT DEFAULT '[]',
    keys       TEXT DEFAULT '[]',
    source     TEXT DEFAULT 'model',
    created_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS attempts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER,
    goal_id    TEXT,
    quiz_id    INTEGER,
    answer     TEXT DEFAULT '',
    passed     INTEGER DEFAULT 0,
    feedback   TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS materials (
    path        TEXT PRIMARY KEY,
    hash        TEXT DEFAULT '',
    chars       INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'ready',
    imported_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS bot_state (
    chat_id TEXT PRIMARY KEY,
    data    TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS events (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    TEXT DEFAULT '',
    level TEXT DEFAULT 'info',
    text  TEXT DEFAULT ''
);

-- 聊天：一张学习卡 = 一个会话；桌面端和 Telegram 共用同一条会话
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL DEFAULT 'task',
    task_id     INTEGER,
    goal_id     TEXT DEFAULT '',
    title       TEXT DEFAULT '',
    created_at  TEXT DEFAULT '',
    last_active TEXT DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_conv_task
    ON conversations(kind, task_id) WHERE task_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id    INTEGER NOT NULL,
    role       TEXT NOT NULL,              -- me / bot / tool
    text       TEXT DEFAULT '',
    sources    TEXT DEFAULT '[]',
    tokens     INTEGER DEFAULT 0,
    created_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_msg_conv ON messages(conv_id, id);

-- 待确认：手机上报的判定和电脑算出来的不一致，等人在桌面端定
-- resolution: follow_server（听电脑）/ keep_phone（保留手机）；resolved_at 空 = 还没处理
CREATE TABLE IF NOT EXISTS review_queue (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT DEFAULT 'task_state',
    task_id      INTEGER,
    goal_id      TEXT DEFAULT '',
    day          TEXT DEFAULT '',
    phone_state  TEXT DEFAULT '',
    server_state TEXT DEFAULT '',
    reason       TEXT DEFAULT '',
    payload_json TEXT DEFAULT '{}',
    created_at   TEXT DEFAULT '',
    resolved_at  TEXT DEFAULT '',
    resolution   TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_review_pending ON review_queue(resolved_at, id);

-- 手机上行事件：按 uid 幂等（同一条重传不重复写）
CREATE TABLE IF NOT EXISTS mobile_events (
    uid          TEXT PRIMARY KEY,
    device       TEXT DEFAULT '',
    kind         TEXT DEFAULT '',
    payload_json TEXT DEFAULT '{}',
    result       TEXT DEFAULT '',
    created_at   TEXT DEFAULT ''
);
"""


class DB:
    """每线程一个连接的轻量封装。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or paths.db_file())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._tls = threading.local()
        self._init_lock = threading.Lock()

    # ---------------------------------------------------------------- 连接
    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._tls, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, check_same_thread=False, timeout=15)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._tls.conn = c
        return c

    def init(self) -> None:
        with self._init_lock:
            self.conn.executescript(SCHEMA)
            for k, v in DEFAULTS.items():
                self.conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
            self.conn.commit()
            self._migrate()
            self.set("schema_version", SCHEMA_VERSION)

    @property
    def schema_version(self) -> int:
        try:
            return int(self.get("schema_version", "0") or 0)
        except ValueError:
            return 0

    def _migrate(self) -> None:
        """按 schema_version 补结构：1 基础表 → 2 聊天 → 3 多课程 → 4 容量改成权重 → 5 待确认。

        每一步都可重复跑（PRAGMA 检查 + INSERT OR IGNORE），所以老库直接跳到最新也不会坏。
        """
        v = self.schema_version
        if v >= SCHEMA_VERSION:
            return
        # v2：聊天两表早就在 SCHEMA 里（CREATE IF NOT EXISTS），这里不用多做
        # v3：课程表 + goals/tasks 的 course 列
        if v < 3:
            for table in ("goals", "tasks"):
                cols = {r["name"] for r in self.q(f"PRAGMA table_info({table})")}
                if "course" not in cols:
                    self.x(f"ALTER TABLE {table} ADD COLUMN course TEXT DEFAULT 'ds'")
            if not self.q1("SELECT 1 FROM courses LIMIT 1"):
                self.x("INSERT OR IGNORE INTO courses(id,name,exam_date,created_at) "
                       "VALUES('ds',?,?,?)",
                       (self.get("subject") or "数据结构",
                        self.get("exam_date") or "2027-01-04", now_s()))
            # 历史任务的课程从目标补；目标也缺的记成第一门课
            self.x("UPDATE tasks SET course=(SELECT course FROM goals WHERE goals.id=tasks.goal_id) "
                   "WHERE (course IS NULL OR course='') AND goal_id IN (SELECT id FROM goals)")
            self.x("UPDATE tasks SET course='ds' WHERE course IS NULL OR course=''")
            self.x("UPDATE goals SET course='ds' WHERE course IS NULL OR course=''")

        # v4：不再存「工作日/周末多少分钟」（写死会让重排失效）。
        # 只留 weekend_ratio（周末多学 %）和 day_cap（每日硬上限，0 = 不限）。
        if v < 4:
            cols = {r["name"] for r in self.q("PRAGMA table_info(courses)")}
            if "weekend_ratio" not in cols:
                self.x("ALTER TABLE courses ADD COLUMN weekend_ratio INTEGER DEFAULT 130")
            if "day_cap" not in cols:
                self.x("ALTER TABLE courses ADD COLUMN day_cap INTEGER DEFAULT 0")
            if "weekday_min" in cols and "weekend_min" in cols:
                for r in self.q("SELECT id, weekday_min, weekend_min FROM courses"):
                    ratio = ratio_from_minutes(r["weekday_min"], r["weekend_min"])
                    self.x("UPDATE courses SET weekend_ratio=? WHERE id=?", (ratio, r["id"]))
                self.log("老库的「工作日/周末分钟」已换算成「周末系数」，每日上限先设为不限。",
                         "info")
            # 旧列留着没用，能删就删（老 SQLite 删不了也不影响，代码不再读）
            for col in ("weekday_min", "weekend_min"):
                if col in cols:
                    try:
                        self.x(f"ALTER TABLE courses DROP COLUMN {col}")
                    except sqlite3.OperationalError:
                        pass
            self.x("DELETE FROM settings WHERE key IN ('weekday_min','weekend_min')")

        # v5：待确认队列（手机版 P0）。表本身在 SCHEMA 里，
        # 这里重跑一遍是为了老库即使 SCHEMA 没跑也能补上，并留一条日志。
        if v < 5:
            self.x("""CREATE TABLE IF NOT EXISTS review_queue (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                kind         TEXT DEFAULT 'task_state',
                task_id      INTEGER,
                goal_id      TEXT DEFAULT '',
                day          TEXT DEFAULT '',
                phone_state  TEXT DEFAULT '',
                server_state TEXT DEFAULT '',
                reason       TEXT DEFAULT '',
                payload_json TEXT DEFAULT '{}',
                created_at   TEXT DEFAULT '',
                resolved_at  TEXT DEFAULT '',
                resolution   TEXT DEFAULT ''
            )""")
            self.x("CREATE INDEX IF NOT EXISTS ix_review_pending "
                   "ON review_queue(resolved_at, id)")
            self.log("已加「待确认」队列：手机判定和电脑不一致的卡进这里人工定。", "info")

        # v6：手机上行事件表（uid 幂等）
        if v < 6:
            self.x("""CREATE TABLE IF NOT EXISTS mobile_events (
                uid          TEXT PRIMARY KEY,
                device       TEXT DEFAULT '',
                kind         TEXT DEFAULT '',
                payload_json TEXT DEFAULT '{}',
                result       TEXT DEFAULT '',
                created_at   TEXT DEFAULT ''
            )""")
            self.log("已加「手机事件」表：重传同一条 uid 不会重复写。", "info")

        if v:
            self.log(f"数据库结构升级：v{v} → v{SCHEMA_VERSION}", "ok")

    # ---------------------------------------------------------------- 备份 / 导出
    def backup(self, keep: int = 20) -> Path:
        """热备一份库到 <数据目录>/backups（用 SQLite backup API，开着也能备）。"""
        out_dir = paths.backups_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        dst_path = out_dir / f"studybot-{now().strftime('%Y%m%d-%H%M%S')}.db"
        dst = sqlite3.connect(dst_path)
        try:
            self.conn.backup(dst)
        finally:
            dst.close()
        olds = sorted(out_dir.glob("studybot-*.db"))
        for p in olds[:-keep] if keep > 0 else []:
            try:
                p.unlink()
            except OSError:
                pass
        self.set("last_backup", now_s())
        return dst_path

    def auto_backup(self) -> Path | None:
        """每天第一次启动备一份；已经有今天的就不再备。"""
        today = now().date().isoformat()
        if self.get("last_backup", "")[:10] == today:
            return None
        # 全新的库（还没目标）不值得备份
        if not self.q1("SELECT 1 FROM goals LIMIT 1"):
            return None
        p = self.backup()
        self.log(f"已自动备份数据库：{p.name}", "info")
        return p

    def backup_before_upgrade(self) -> Path | None:
        """结构要升级（迁移会改表/删列）时，先备一份再 init。

        init() 里的迁移是就地改库；先备份，出问题能回退。
        全新的空库不用备。
        """
        try:
            if self.schema_version >= SCHEMA_VERSION:
                return None
            if not self.q1("SELECT 1 FROM goals LIMIT 1"):
                return None
            p = self.backup()
            self.log(f"升级数据库结构前已备份：{p.name}", "info")
            return p
        except sqlite3.Error:
            return None          # 备份失败也别拦住启动（老库可能连表都还没有）

    def export_json(self) -> Path:
        """导出一份人能直接看的 JSON（不含 API key / bot token）。"""
        out_dir = paths.exports_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f"export-{now().strftime('%Y%m%d-%H%M%S')}.json"
        secret = {"api_key", "tg_token"}
        data = {
            "schema_version": SCHEMA_VERSION,
            "exported_at": now_s(),
            "courses": self.courses(include_archived=True),
            "settings": {r["key"]: ("****" if r["key"] in secret else r["value"])
                         for r in self.q("SELECT key, value FROM settings")},
            "goals": self.q("SELECT * FROM goals ORDER BY course, seq"),
            "tasks": self.q("SELECT id, date, course, goal_id, title, kind, minutes, state "
                            "FROM tasks ORDER BY date, id"),
            "quiz": self.q("SELECT id, goal_id, batch, type, stem, options, answer, rubric, "
                           "source FROM quiz ORDER BY goal_id, batch, id"),
            "attempts": self.q("SELECT task_id, goal_id, quiz_id, answer, passed, created_at "
                               "FROM attempts ORDER BY id"),
            "review_queue": self.q("SELECT * FROM review_queue ORDER BY id"),
        }
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log(f"已导出数据：{p.name}", "info")
        return p

    # ---------------------------------------------------------------- 基础
    def q(self, sql: str, args: tuple | list = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def q1(self, sql: str, args: tuple | list = ()) -> dict | None:
        r = self.conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    def x(self, sql: str, args: tuple | list = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur

    # ---------------------------------------------------------------- 设置
    def get(self, key: str, default: str = "") -> str:
        r = self.q1("SELECT value FROM settings WHERE key=?", (key,))
        return r["value"] if r else default

    def geti(self, key: str, default: int = 0) -> int:
        try:
            return int(float(self.get(key, str(default))))
        except (TypeError, ValueError):
            return default

    def set(self, key: str, value) -> None:
        self.x("INSERT INTO settings(key,value) VALUES(?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    # ---------------------------------------------------------------- 日志
    def log(self, text: str, level: str = "info") -> None:
        ts = now_s()
        try:
            self.x("INSERT INTO events(ts,level,text) VALUES(?,?,?)", (ts, level, text))
        except sqlite3.Error:
            pass
        EVENTS.put((ts, level, text))

    def logs(self, limit: int = 300) -> list[dict]:
        return self.q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))

    def clear_logs(self, keep: int = 0) -> int:
        """删日志。keep=0 全删；keep=N 只留最近 N 条。返回删掉几条。"""
        before = int((self.q1("SELECT COUNT(*) n FROM events") or {}).get("n") or 0)
        if keep > 0:
            self.x("DELETE FROM events WHERE id NOT IN "
                   "(SELECT id FROM events ORDER BY id DESC LIMIT ?)", (int(keep),))
        else:
            self.x("DELETE FROM events")
        after = int((self.q1("SELECT COUNT(*) n FROM events") or {}).get("n") or 0)
        return before - after

    # ---------------------------------------------------------------- 待确认
    # 手机上报的判定和电脑算出来的不一致时进这张表，等人在桌面端逐条定。
    # 不自动改回、不自己消失（方案 §5）。
    RESOLUTIONS = ("follow_server", "keep_phone")

    def review_add(self, kind: str = "task_state", task_id=None, goal_id: str = "",
                   day: str = "", phone_state: str = "", server_state: str = "",
                   reason: str = "", payload: dict | None = None) -> int:
        cur = self.x(
            "INSERT INTO review_queue(kind,task_id,goal_id,day,phone_state,server_state,"
            "reason,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (kind, task_id, goal_id, day, phone_state, server_state, reason,
             json.dumps(payload or {}, ensure_ascii=False), now_s()),
        )
        self.log(f"新待确认 #{cur.lastrowid}：{goal_id or '—'} {reason[:60]}", "warn")
        return int(cur.lastrowid)

    def review_items(self, pending_only: bool = True, limit: int = 200) -> list[dict]:
        """带目标标题/课程，界面不用再查一遍。默认只列没处理的。"""
        sql = ("SELECT q.*, g.title AS goal_title, g.course AS course "
               "FROM review_queue q LEFT JOIN goals g ON g.id=q.goal_id")
        if pending_only:
            sql += " WHERE q.resolved_at IS NULL OR q.resolved_at=''"
        sql += " ORDER BY q.id DESC LIMIT ?"
        return self.q(sql, (int(limit),))

    def review_pending_count(self) -> int:
        r = self.q1("SELECT COUNT(*) AS n FROM review_queue "
                    "WHERE resolved_at IS NULL OR resolved_at=''")
        return int((r or {"n": 0})["n"])

    def review_resolve(self, rid: int, resolution: str) -> dict:
        """定一条：听电脑 → 卡片按 server_state 改；保留手机 → 按 phone_state 改。

        `kind="replan"`（手机整份计划的重排建议）另走一支：
        保留手机 = 按 payload 里的卡重建「今天及以后」的 todo 卡；
        听电脑 = 什么都不改（服务器上的计划本来就是它）。
        只写库，不调用模型；能不能发回手机上，由下次同步决定。
        """
        if resolution not in self.RESOLUTIONS:
            raise ValueError(f"只能选 {' / '.join(self.RESOLUTIONS)}")
        row = self.q1("SELECT * FROM review_queue WHERE id=?", (int(rid),))
        if not row:
            raise ValueError("这条待确认不存在")
        if row.get("resolved_at"):
            return self.q1("SELECT * FROM review_queue WHERE id=?", (int(rid),)) or row
        extra = ""
        if row.get("kind") == "replan" and resolution == "keep_phone":
            inserted, deleted = self.review_apply_replan(row.get("payload_json") or "{}")
            extra = f"，按手机重排重建：删 {deleted} 张、插 {inserted} 张"
        else:
            state = row.get("server_state") if resolution == "follow_server" else row.get("phone_state")
            tid = row.get("task_id")
            if tid and state and self.task(int(tid)):
                self.set_task_state(int(tid), str(state))
                extra = f"，卡片 #{tid} 改为 {state}"
        self.x("UPDATE review_queue SET resolved_at=?, resolution=? WHERE id=?",
               (now_s(), resolution, int(rid)))
        self.log(f"待确认 #{rid} 已处理："
                 + ("听电脑" if resolution == "follow_server" else "保留手机") + extra, "ok")
        return self.q1("SELECT * FROM review_queue WHERE id=?", (int(rid),)) or row

    def review_apply_replan(self, payload_json: str) -> tuple[int, int]:
        """把手机重排的整份计划落库：先删「今天及以后」的 todo 卡，再按清单插。

        重排结果本身就是全量，不按 (date,goal_id,kind) 去重；唯一索引撞上
        （比如同一天同一张卡已经 done/skipped）就跳过，不算失败。
        返回 (插入张数, 删掉张数)。
        """
        try:
            payload = json.loads(payload_json or "{}")
        except (TypeError, ValueError):
            payload = {}
        today = today_s()
        cur = self.x("DELETE FROM tasks WHERE date>=? AND state='todo'", (today,))
        deleted = max(0, int(cur.rowcount))
        ts = now_s()
        n = 0
        for t in (payload.get("tasks") or []):
            if not isinstance(t, dict):
                continue
            day = str(t.get("day") or t.get("date") or "").strip()[:10]
            gid = str(t.get("goal_id") or t.get("goalId") or "").strip()
            if not day or not gid or day < today:
                continue
            course = str(t.get("course") or "").strip()
            if not course:                      # 手机没给课程名时按目标查，查不到算 ds
                g = self.q1("SELECT course FROM goals WHERE id=?", (gid,))
                course = str((g or {}).get("course") or "ds")
            try:
                minutes = int(t.get("minutes") or 30)
            except (TypeError, ValueError):
                minutes = 30
            cur2 = self.x(
                "INSERT OR IGNORE INTO tasks(date,course,goal_id,title,kind,minutes,"
                "state,created_at) VALUES(?,?,?,?,?,?,'todo',?)",
                (day, course, gid, str(t.get("title") or ""),
                 str(t.get("kind") or "learn"), minutes, ts),
            )
            n += max(0, int(cur2.rowcount))
        return n, deleted

    # ---------------------------------------------------------------- 手机同步
    def event_seen(self, uid: str) -> bool:
        return bool(self.q1("SELECT 1 FROM mobile_events WHERE uid=?", (uid,)))

    def event_save(self, uid: str, device: str, kind: str, payload, result: str = "ok") -> None:
        self.x("INSERT OR IGNORE INTO mobile_events(uid,device,kind,payload_json,result,created_at) "
               "VALUES(?,?,?,?,?,?)",
               (uid, device, kind,
                json.dumps(payload, ensure_ascii=False, default=str)[:4000],
                str(result)[:1000], now_s()))

    def event_count(self) -> int:
        r = self.q1("SELECT COUNT(*) AS n FROM mobile_events")
        return int((r or {"n": 0})["n"])

    def mobile_rev(self, signature: str) -> int:
        """手机快照的 rev：内容签名没变就不动，变了就前进（单调递增）。

        手机拿 rev 判断「要不要重写本地库」；rev 只用比较大小，不当时间用。
        """
        rev = self.geti("mobile_rev", 0)
        if signature != self.get("mobile_sig", ""):
            rev = max(rev + 1, int(time.time()))
            self.set("mobile_sig", signature)
            self.set("mobile_rev", str(rev))
        return rev

    # ---------------------------------------------------------------- 课程
    def courses(self, include_archived: bool = False) -> list[dict]:
        sql = "SELECT * FROM courses"
        if not include_archived:
            sql += " WHERE archived=0"
        sql += " ORDER BY created_at, id"
        return self.q(sql)

    def course(self, cid: str) -> dict | None:
        return self.q1("SELECT * FROM courses WHERE id=?", (cid,))

    def save_course(self, cid: str, **kw) -> dict:
        """建课或改课。

        字段：name / exam_date / weekend_ratio（周末多学 %）/ day_cap（每日上限分钟，0=不限）/ archived。
        注意：**不存每天多少分钟** —— 每天学多少由排程按「剩余量 ÷ 剩余天数」算。
        """
        cid = (cid or "").strip()
        if not cid:
            raise ValueError("课程 id 不能为空")
        if cid.lower() in {"ppt", "书", "笔记", "试卷", "大作业", "考试题型或复习大纲", "inbox"}:
            raise ValueError(f"{cid} 会和资料目录重名，换个 id（如 math / os）")
        cur = self.course(cid)
        fields = {k: kw[k] for k in ("name", "exam_date", "weekend_ratio", "day_cap",
                                     "archived") if k in kw}
        if "weekend_ratio" in fields:
            try:
                v = int(fields["weekend_ratio"] or DEFAULT_WEEKEND_RATIO)
            except (TypeError, ValueError):
                raise ValueError("周末系数要填整数（100 = 和工作日一样）") from None
            fields["weekend_ratio"] = max(RATIO_RANGE[0], min(RATIO_RANGE[1], v))
        if "day_cap" in fields:
            try:
                v = int(fields["day_cap"] or 0)
            except (TypeError, ValueError):
                raise ValueError("每日上限要填整数分钟（0 = 不限）") from None
            fields["day_cap"] = max(0, min(DAY_CAP_MAX, v))
        if cur is None:
            fields.setdefault("name", cid)
            cols = ", ".join(["id", "created_at", *fields])
            marks = ", ".join(["?"] * (2 + len(fields)))
            self.x(f"INSERT INTO courses({cols}) VALUES({marks})",
                   [cid, now_s(), *fields.values()])
            self.log(f"新增课程 {cid}（{fields.get('name', cid)}）", "ok")
        elif fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            self.x(f"UPDATE courses SET {sets} WHERE id=?", [*fields.values(), cid])
            self.log(f"改了课程 {cid}：{'、'.join(fields)}", "info")
        return self.course(cid) or {}

    def current_course(self) -> str:
        """桌面端当前选中的课程；没选过就用第一门。"""
        cid = self.get("course", "")
        if cid and self.course(cid):
            return cid
        rows = self.courses()
        return rows[0]["id"] if rows else "ds"

    # ---------------------------------------------------------------- 目标
    def import_goals(self, path: Path | None = None, course: str = "ds") -> int:
        """把 goals.json 读进来（可重复调用，目标按 id 覆盖）。

        多门课 id 会撞：本课程里和别的课程重名的目标自动改成「课程id-原id」，
        依赖也一起改，避免把数据结构的目标覆盖掉。
        """
        p = Path(path or paths.goals_file(course))
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
        rows = raw.get("goals") or []
        taken = {r["id"] for r in self.q("SELECT id FROM goals WHERE course<>?", (course,))}
        mapping: dict[str, str] = {}
        for g in rows:
            if g["id"] in taken and g["id"] not in mapping:
                mapping[g["id"]] = f"{course}-{g['id']}"
        for i, g in enumerate(rows, 1):
            gid = mapping.get(g["id"], g["id"])
            deps = [mapping.get(d, d) for d in (g.get("d") or [])]
            self.x(
                "INSERT INTO goals(id,course,ch,title,p,m,d,cp,seq) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET course=excluded.course, ch=excluded.ch, "
                "title=excluded.title, p=excluded.p, m=excluded.m, d=excluded.d, "
                "cp=excluded.cp, seq=excluded.seq",
                (gid, course, g.get("ch", ""), g.get("title", ""), g.get("p", "core"),
                 int(g.get("m") or 30), json.dumps(deps, ensure_ascii=False),
                 json.dumps(g.get("cp") or [], ensure_ascii=False), i),
            )
        # 考核日 / 科目：有课程就写到课程上，settings 里保留一份兼容老页面
        exam = raw.get("exam_date") or ""
        if exam:
            cur = self.course(course)
            if cur is None:
                self.save_course(course, name=raw.get("subject") or course, exam_date=exam)
            elif not cur.get("exam_date"):
                self.save_course(course, exam_date=exam)
            self.set("exam_date", exam)
        if course == "ds":
            self.set("subject", raw.get("subject") or self.get("subject"))
            self.set("exam_format", raw.get("exam_format", ""))
        self.log(f"导入目标 {len(rows)} 个（{p.name} · {course}）"
                 + (f"，改名 {len(mapping)} 个避免撞车" if mapping else ""), "ok")
        return len(rows)

    @staticmethod
    def _goal_row(r: dict) -> dict:
        r = dict(r)
        r["d"] = json.loads(r.get("d") or "[]")
        r["cp"] = json.loads(r.get("cp") or "[]")
        return r

    def goals(self, include_done: bool = True, course: str | None = None) -> list[dict]:
        if course:
            rows = self.q("SELECT * FROM goals WHERE course=? ORDER BY seq", (course,))
        else:
            rows = self.q("SELECT * FROM goals ORDER BY course, seq")
        rows = [self._goal_row(r) for r in rows]
        if not include_done:
            done = self.done_goal_ids(course)
            rows = [g for g in rows if g["id"] not in done]
        return rows

    def goal(self, gid: str) -> dict | None:
        r = self.q1("SELECT * FROM goals WHERE id=?", (gid,))
        return self._goal_row(r) if r else None

    def done_goal_ids(self, course: str | None = None) -> set[str]:
        if course:
            rows = self.q("SELECT DISTINCT goal_id FROM tasks WHERE state='done' AND course=?",
                          (course,))
        else:
            rows = self.q("SELECT DISTINCT goal_id FROM tasks WHERE state='done'")
        return {r["goal_id"] for r in rows}

    # ---------------------------------------------------------------- 任务
    def tasks_between(self, date_from: str, date_to: str,
                      states: tuple[str, ...] | None = None,
                      course: str | None = None) -> list[dict]:
        sql = "SELECT * FROM tasks WHERE date BETWEEN ? AND ?"
        args: list = [date_from, date_to]
        if course:
            sql += " AND course=?"
            args.append(course)
        if states:
            sql += f" AND state IN ({','.join('?' * len(states))})"
            args += list(states)
        sql += " ORDER BY date, id"
        return self.q(sql, args)

    def tasks_on(self, date: str, course: str | None = None) -> list[dict]:
        if course:
            return self.q("SELECT * FROM tasks WHERE date=? AND course=? ORDER BY state, id",
                          (date, course))
        return self.q("SELECT * FROM tasks WHERE date=? ORDER BY state, id", (date,))

    def task(self, tid: int) -> dict | None:
        return self.q1("SELECT * FROM tasks WHERE id=?", (tid,))

    def set_task_state(self, tid: int, state: str) -> None:
        self.x("UPDATE tasks SET state=? WHERE id=?", (state, tid))

    def today_tasks(self, course: str | None = None) -> list[dict]:
        return [t for t in self.tasks_on(today_s(), course) if t["state"] != "dropped"]

    def pending_today(self, course: str | None = None) -> list[dict]:
        return [t for t in self.today_tasks(course) if t["state"] == "todo"]

    # ---------------------------------------------------------------- 题库
    def quiz_batch(self, goal_id: str) -> list[dict]:
        r = self.q1("SELECT MAX(batch) AS b FROM quiz WHERE goal_id=?", (goal_id,))
        b = (r or {}).get("b")
        if not b:
            return []
        rows = self.q("SELECT * FROM quiz WHERE goal_id=? AND batch=? ORDER BY id", (goal_id, b))
        for row in rows:
            row["options"] = json.loads(row.get("options") or "[]")
            row["rubric"] = json.loads(row.get("rubric") or "[]")
            row["keys"] = json.loads(row.get("keys") or "[]")
        return rows

    def next_batch_no(self, goal_id: str) -> int:
        r = self.q1("SELECT MAX(batch) AS b FROM quiz WHERE goal_id=?", (goal_id,))
        return int((r or {}).get("b") or 0) + 1

    def quiz(self, qid: int) -> dict | None:
        r = self.q1("SELECT * FROM quiz WHERE id=?", (qid,))
        if not r:
            return None
        r["options"] = json.loads(r.get("options") or "[]")
        r["rubric"] = json.loads(r.get("rubric") or "[]")
        r["keys"] = json.loads(r.get("keys") or "[]")
        return r

    def quiz_count(self) -> int:
        return int((self.q1("SELECT COUNT(*) AS n FROM quiz") or {"n": 0})["n"])

    def add_quiz(self, goal_id: str, batch: int, item: dict, source: str = "model") -> int:
        cur = self.x(
            "INSERT INTO quiz(goal_id,batch,type,stem,options,answer,rubric,keys,source,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (goal_id, batch, item.get("type", "choice"), item.get("stem", ""),
             json.dumps(item.get("options") or [], ensure_ascii=False),
             str(item.get("answer", "")),
             json.dumps(item.get("rubric") or [], ensure_ascii=False),
             json.dumps(item.get("keys") or [], ensure_ascii=False),
             source, now_s()),
        )
        return int(cur.lastrowid)

    def quiz_batches_today(self, goal_id: str) -> int:
        since = (now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        r = self.q1("SELECT COUNT(DISTINCT batch) AS n FROM quiz WHERE goal_id=? AND created_at>=?",
                    (goal_id, since))
        return int((r or {}).get("n") or 0)

    def fail_streak(self, goal_id: str) -> int:
        """连续没过几批：从最近一批往前数，答了 ≥2 题且答对 <2 算一批没过。

        用来判断「是不是该停止换题、改成回炉重讲」。
        """
        rows = self.q("SELECT q.batch AS b, COUNT(*) AS n, SUM(a.passed) AS s "
                      "FROM attempts a JOIN quiz q ON q.id=a.quiz_id "
                      "WHERE a.goal_id=? GROUP BY q.batch ORDER BY q.batch DESC", (goal_id,))
        k = 0
        for r in rows:
            if int(r["n"] or 0) >= 2 and int(r["s"] or 0) < 2:
                k += 1
            else:
                break
        return k

    def all_quiz(self, limit: int = 500) -> list[dict]:
        rows = self.q("SELECT * FROM quiz ORDER BY goal_id, batch DESC, id LIMIT ?", (limit,))
        for r in rows:
            r["options"] = json.loads(r.get("options") or "[]")
            r["rubric"] = json.loads(r.get("rubric") or "[]")
        return rows

    def lag_days(self, course: str | None = None) -> int:
        """过去日期还挂着 todo 就算落后；重排后自动归零。"""
        sql = "SELECT COUNT(DISTINCT date) n FROM tasks WHERE date<? AND state='todo'"
        args: list = [today_s()]
        if course:
            sql += " AND course=?"
            args.append(course)
        r = self.q1(sql, args)
        return int((r or {"n": 0})["n"])

    # ---------------------------------------------------------------- 答题
    def add_attempt(self, task_id: int | None, goal_id: str, quiz_id: int | None,
                    answer: str, passed: int, feedback: str = "") -> None:
        self.x("INSERT INTO attempts(task_id,goal_id,quiz_id,answer,passed,feedback,created_at) "
               "VALUES(?,?,?,?,?,?,?)",
               (task_id, goal_id, quiz_id, answer[:2000], passed, feedback[:1000], now_s()))

    def attempts_for_goal(self, goal_id: str, limit: int = 20) -> list[dict]:
        return self.q("SELECT * FROM attempts WHERE goal_id=? ORDER BY id DESC LIMIT ?",
                      (goal_id, limit))

    # ---------------------------------------------------------------- 资料
    def material_known(self, path: str) -> dict | None:
        return self.q1("SELECT * FROM materials WHERE path=?", (path,))

    def add_material(self, path: str, h: str, chars: int, status: str = "ready") -> None:
        self.x("INSERT INTO materials(path,hash,chars,status,imported_at) VALUES(?,?,?,?,?) "
               "ON CONFLICT(path) DO UPDATE SET hash=excluded.hash, chars=excluded.chars, "
               "status=excluded.status, imported_at=excluded.imported_at",
               (path, h, chars, status, now_s()))

    def materials(self) -> list[dict]:
        return self.q("SELECT * FROM materials ORDER BY imported_at DESC")

    # ---------------------------------------------------------------- 会话状态
    def bot_state(self, chat_id: str) -> dict:
        r = self.q1("SELECT data FROM bot_state WHERE chat_id=?", (str(chat_id),))
        try:
            return json.loads(r["data"]) if r else {}
        except (TypeError, ValueError):
            return {}

    def set_bot_state(self, chat_id: str, data: dict) -> None:
        self.x("INSERT INTO bot_state(chat_id,data) VALUES(?,?) "
               "ON CONFLICT(chat_id) DO UPDATE SET data=excluded.data",
               (str(chat_id), json.dumps(data, ensure_ascii=False)))

    # ---------------------------------------------------------------- 聊天
    def conversation(self, task_id: int | None = None, goal_id: str = "",
                     kind: str = "task", title: str = "") -> dict:
        """拿这张卡对应的会话；没有就建。桌面端和 Telegram 共用同一条。"""
        if task_id is not None:
            r = self.q1("SELECT * FROM conversations WHERE kind=? AND task_id=?",
                        (kind, int(task_id)))
            if r:
                self.touch_conv(r["id"])
                return r
        ts = now_s()
        try:
            cur = self.x("INSERT INTO conversations(kind,task_id,goal_id,title,"
                         "created_at,last_active) VALUES(?,?,?,?,?,?)",
                         (kind, task_id, goal_id, title, ts, ts))
        except sqlite3.IntegrityError:      # 另一个线程刚建过，别重复建
            r = self.q1("SELECT * FROM conversations WHERE kind=? AND task_id=?",
                        (kind, task_id))
            if not r:
                raise
            return r
        return self.conv(int(cur.lastrowid))  # type: ignore[arg-type]

    def conv(self, conv_id: int) -> dict | None:
        return self.q1("SELECT * FROM conversations WHERE id=?", (conv_id,))

    def touch_conv(self, conv_id: int) -> None:
        self.x("UPDATE conversations SET last_active=? WHERE id=?", (now_s(), conv_id))

    def conv_messages(self, conv_id: int, limit: int = 20) -> list[dict]:
        rows = self.q("SELECT * FROM messages WHERE conv_id=? ORDER BY id DESC LIMIT ?",
                      (conv_id, limit))
        rows.reverse()
        for r in rows:
            try:
                r["sources"] = json.loads(r.get("sources") or "[]")
            except (TypeError, ValueError):
                r["sources"] = []
        return rows

    def add_message(self, conv_id: int, role: str, text: str,
                    sources: list | None = None, tokens: int = 0) -> int:
        cur = self.x("INSERT INTO messages(conv_id,role,text,sources,tokens,created_at) "
                     "VALUES(?,?,?,?,?,?)",
                     (conv_id, role, (text or "")[:8000],
                      json.dumps(sources or [], ensure_ascii=False), int(tokens or 0), now_s()))
        return int(cur.lastrowid)

    def clear_messages(self, conv_id: int) -> None:
        self.x("DELETE FROM messages WHERE conv_id=?", (conv_id,))
        self.touch_conv(conv_id)

    def conv_tokens(self, conv_id: int) -> int:
        r = self.q1("SELECT COALESCE(SUM(tokens),0) n FROM messages WHERE conv_id=?",
                    (conv_id,))
        return int((r or {"n": 0})["n"])

    def conversations(self, limit: int = 100) -> list[dict]:
        return self.q("SELECT * FROM conversations ORDER BY last_active DESC LIMIT ?", (limit,))

    # ---------------------------------------------------------------- 统计
    def stats(self, course: str | None = None) -> dict:
        t = today_s()
        c = self.course(course) if course else None
        gsql = "SELECT COUNT(*) n FROM goals"
        gargs: list = []
        if course:
            gsql += " WHERE course=?"
            gargs.append(course)
        total = int((self.q1(gsql, gargs) or {"n": 0})["n"])
        done = len(self.done_goal_ids(course))
        tt = self.today_tasks(course)
        done_today = [x for x in tt if x["state"] == "done"]
        minutes = sum(int(x["minutes"] or 0) for x in done_today)
        exam = (c or {}).get("exam_date") or self.get("exam_date")
        try:
            days_left = (datetime.fromisoformat(exam).date() - now().date()).days
        except ValueError:
            days_left = 0
        streak = self._streak(course)
        return {
            "course": course or "",
            "subject": (c or {}).get("name") or self.get("subject"),
            "total_goals": total,
            "done_goals": done,
            "today_total": len(tt),
            "today_done": len(done_today),
            "today_minutes": minutes,
            "days_left": days_left,
            "exam_date": exam,
            "streak": streak,
            "pending": len([x for x in tt if x["state"] == "todo"]),
        }

    def stats_by_course(self) -> list[dict]:
        return [self.stats(c["id"]) for c in self.courses()]

    def _streak(self, course: str | None = None) -> int:
        """连续有完成记录的天数（从今天或昨天往前数）。"""
        sql = "SELECT DISTINCT date FROM tasks WHERE state='done'"
        args: list = []
        if course:
            sql += " AND course=?"
            args.append(course)
        rows = self.q(sql + " ORDER BY date DESC", args)
        days = [r["date"] for r in rows]
        if not days:
            return 0
        cur = now().date()
        if days[0] != cur.isoformat():
            cur = cur - timedelta(days=1)
        n = 0
        for d in days:
            if d == cur.isoformat():
                n += 1
                cur -= timedelta(days=1)
            elif d < cur.isoformat():
                break
        return n


def sha1_file(p: Path) -> str:
    h = hashlib.sha1()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# 简单的重试（网络抖动）
def retry(fn, times: int = 3, delay: float = 1.0, on_error=None):
    last = None
    for i in range(times):
        try:
            return fn()
        except Exception as e:      # noqa: BLE001
            last = e
            if on_error:
                on_error(e)
            time.sleep(delay * (i + 1))
    raise last  # type: ignore[misc]
