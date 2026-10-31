#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务层：计划生成、今日任务、出题与判分、资料入库。

复用 tools/ 里已经跑通的东西（planner / retrieval / extract），不重写算法。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import shutil
import sys
from pathlib import Path

# app 目录里有 paths.py，先把自己所在的目录放进来（打包/独立运行都能找到）
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

BASE = paths.CODE_ROOT
APP_DIR = Path(__file__).resolve().parent
TOOLS = BASE / "tools"
for _p in (str(APP_DIR), str(TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import planner  # noqa: E402
from db import today_s, now  # noqa: E402
import llm  # noqa: E402

_retrieval = None
_retrieval_failed = ""

# 只用来说一句「这天太重了」，不参与排程（排程不设张数上限，也不写死分钟）
HEAVY_DAY_MIN = 240


def retrieval_mod():
    """懒加载检索层（要读 992 个块，第一次约 0.3 秒）。"""
    global _retrieval, _retrieval_failed
    if _retrieval is None and not _retrieval_failed:
        try:
            import retrieval  # noqa: F401
            _retrieval = retrieval
        except Exception as e:      # noqa: BLE001
            _retrieval_failed = f"{type(e).__name__}: {e}"
    return _retrieval


def retrieve(goal: dict, query: str, k: int = 3,
             extra_terms: list[str] | None = None) -> list[dict]:
    r = retrieval_mod()
    if not r:
        return []
    try:
        return r.search(goal, query or goal.get("title", ""), k, extra_terms=extra_terms)
    except Exception:  # noqa: BLE001
        return []


# ------------------------------------------------------------------ 计划
def _in_flight(db) -> set[int]:
    """正在答题、或今天已经答过题的任务 id —— 重排时必须原样保留。"""
    keep: set[int] = set()
    for r in db.q("SELECT DISTINCT task_id FROM attempts WHERE task_id IS NOT NULL"):
        if r["task_id"]:
            keep.add(int(r["task_id"]))
    for r in db.q("SELECT data FROM bot_state"):
        try:
            tid = json.loads(r["data"] or "{}").get("task_id")
            if tid:
                keep.add(int(tid))
        except (ValueError, TypeError):
            continue
    return keep


def _course_budgets(db, cids: list[str], start_d: dt.date) -> dict[str, int]:
    """跨课程总预算怎么分：每天总共多少分钟，按「离考核日远近」分给各课。

    没设 day_total_cap（0）或各课自己都排得下 → 返回 {}（保持各排各的）。
    超了才分：离考核越近权重越大（1 / 剩余天数），分到「这门课本来需要的日均」为止，
    剩下的再分给别的课。单张卡不能拆，所以某天可能比预算多出一张卡的量。
    """
    cap = int(db.geti("day_total_cap", 0) or 0)
    if cap <= 0:
        return {}
    rows = []
    for cid in cids:
        c = db.course(cid) or {}
        try:
            exam = dt.date.fromisoformat(str(c.get("exam_date") or ""))
        except ValueError:
            continue
        days = (exam - start_d).days + 1
        if days < 3:
            continue
        done = db.done_goal_ids(cid)
        need = sum(int(g["m"]) + 10 for g in db.goals(course=cid) if g["id"] not in done)
        if need <= 0:
            continue
        rate = need / days                      # 这门课本来每天要学多少
        rows.append({"cid": cid, "days": days, "left": float(rate), "rate": rate,
                     "name": c.get("name") or cid})
    if not rows:
        return {}
    if sum(r["rate"] for r in rows) <= cap:      # 都排得下，不用互相挤
        return {}

    alloc = {r["cid"]: 0.0 for r in rows}
    left = float(cap)
    active = list(rows)
    for _ in range(len(rows) + 3):
        if not active or left <= 0.5:
            break
        wsum = sum(1.0 / r["days"] for r in active) or 1.0
        shares = {r["cid"]: left * (1.0 / r["days"]) / wsum for r in active}
        moved = False
        for r in list(active):
            give = min(r["left"], shares[r["cid"]])
            if give <= 0.5:
                active.remove(r)
                continue
            alloc[r["cid"]] += give
            r["left"] -= give
            left -= give
            moved = True
            if r["left"] <= 0.5:             # 这门课已经分够它需要的量
                active.remove(r)
        if not moved:
            break
    return {cid: max(1, int(round(v))) for cid, v in alloc.items() if v >= 1}


def _budget_note(db, budgets: dict[str, int], cap: int) -> str:
    if cap <= 0:
        return ""
    if not budgets:
        return f"每天总预算 {cap} 分钟：各门课各自都排得下，不用互相挤"
    bits = []
    for cid, minutes in budgets.items():
        c = db.course(cid) or {}
        bits.append(f"{c.get('name') or cid} {minutes}")
    return f"每天总共 {cap} 分钟，按考核日远近分：{' / '.join(bits)}"


def regenerate(db, start: dt.date | None = None, course: str | None = None) -> dict:
    """重新排程：已完成的不动，未完成的从今天起重排。

    course 为空 = 所有课程各排各的（每门课用自己的考核日和周末系数，每天的量按剩余量 ÷ 剩余天数算）。
    """
    if getattr(db, "remote", False):        # 瘦客户端：排程在服务器上跑
        return db.action("replan", course=course or "")

    cids = [course] if course else [c["id"] for c in db.courses()]
    if not cids:
        raise RuntimeError("还没有课程：先在「设置」页加一门课")
    start_d = start or now().date()
    # 只有「重排全部」时才按总预算分；单课重排不抢别的课的量
    budgets = _course_budgets(db, cids, start_d) if not course else {}
    parts = [p for p in (_regen_one(db, cid, start, budget=budgets.get(cid, 0))
                         for cid in cids) if p]
    if not parts:
        note = "；".join(db.get(f"plan_note:{cid}", "") for cid in cids) or "没有可排的课程"
        db.set("plan_note", note)
        return {"inserted": 0, "uncovered": 0, "note": note, "minutes": 0,
                "days": 0, "replaced": 0, "parts": []}

    note = "；".join(p["note"] for p in parts)
    cap = int(db.geti("day_total_cap", 0) or 0)
    budget_line = _budget_note(db, budgets, cap) if not course else ""
    if budget_line:
        note = budget_line + "；" + note
    if cap and budgets:
        actual = sum(int(p.get("daily_avg") or 0) for p in parts)
        if actual > cap * 1.1:
            note += (f"；实际各课日均加起来约 {actual} 分钟"
                     f"（单张卡不能拆，会比预算多一点）")
    db.set("plan_note", note)
    db.set("plan_generated_at", now().strftime("%Y-%m-%d %H:%M:%S"))
    db.log(f"生成计划：{note}", "ok")
    return {"inserted": sum(p["inserted"] for p in parts),
            "uncovered": sum(p["uncovered"] for p in parts),
            "minutes": sum(p["minutes"] for p in parts),
            "days": max(p["days"] for p in parts),
            "replaced": sum(p["replaced"] for p in parts),
            "note": note, "parts": parts}


def _regen_one(db, cid: str, start: dt.date | None, budget: int = 0) -> dict | None:
    """只重排一门课。没有目标就只写一句说明，不报错。

    budget：跨课程总预算分给这门课的日均分钟（0 = 不设）。
    """
    c = db.course(cid) or {}
    name = c.get("name") or cid
    goals = db.goals(course=cid)
    if not goals:
        note = f"{name}：还没有目标，先导入这门课的 goals（data/goals_{cid}.json）"
        db.set(f"plan_note:{cid}", note)
        db.log(note, "warn")
        return None

    done = db.done_goal_ids(cid)
    active = [g for g in goals if g["id"] not in done]
    start_d = start or now().date()
    try:
        exam = dt.date.fromisoformat(c.get("exam_date") or db.get("exam_date"))
    except ValueError as e:
        raise RuntimeError(f"{name} 的考核日格式不对，应为 YYYY-MM-DD") from e
    if (exam - start_d).days < 3:
        raise RuntimeError(f"{name} 离考核日不足 3 天，排不出计划")

    # 每天学多少分钟不存也不写死：planner 按「剩余量 ÷ 剩余天数」自动摊，
    # 这里只给「周末多学多少」和用户设的每日硬上限（0 = 不限）。
    ratio = int(c.get("weekend_ratio") or db.geti("weekend_ratio", 130))
    ratio = max(100, min(200, ratio))
    day_cap = int(c.get("day_cap") or db.geti("day_cap", 0))
    res = planner.schedule(active, start_d, exam, weekend_weight=ratio / 100.0,
                           day_cap=max(0, day_cap), daily_budget=max(0, int(budget)))

    today = start_d.isoformat()
    before = db.q1("SELECT COUNT(*) n FROM tasks WHERE date>=? AND state='todo' AND course=?",
                   (today, cid))
    # 正在答的卡不能删：删了 id 会变，小测结果就记不上
    keep = _in_flight(db)
    # 冻结历史：今天往前的未完成任务不再保留（目标会重新排进来）
    if keep:
        ph = ",".join("?" * len(keep))
        db.x(f"UPDATE tasks SET state='dropped' WHERE date<? AND state='todo' AND course=? "
             f"AND id NOT IN ({ph})", [today, cid, *keep])
        db.x(f"DELETE FROM tasks WHERE date>=? AND state='todo' AND course=? "
             f"AND id NOT IN ({ph})", [today, cid, *keep])
    else:
        db.x("UPDATE tasks SET state='dropped' WHERE date<? AND state='todo' AND course=?",
             (today, cid))
        db.x("DELETE FROM tasks WHERE date>=? AND state='todo' AND course=?", (today, cid))

    inserted = 0
    for day in res["plan"]:
        ds = day["date"].isoformat()
        for t in day["tasks"]:
            try:
                db.x("INSERT OR IGNORE INTO tasks(date,course,goal_id,title,kind,minutes,"
                     "state,created_at) VALUES(?,?,?,?,?,?,'todo',?)",
                     (ds, cid, t["id"], t.get("title", ""), t.get("kind", "learn"),
                      int(t.get("min") or 30), now().strftime("%Y-%m-%d %H:%M:%S")))
                inserted += 1
            except Exception:  # noqa: BLE001
                pass

    uncovered = res["uncovered"]
    total_min = sum(int(t.get("min") or 0) for d in res["plan"] for t in d["tasks"])
    study_days = len([d for d in res["plan"] if d["tasks"]])
    per_day = round(total_min / max(1, study_days))
    need_min = sum(int(g["m"]) + 10 for g in active)      # 还没学完的分钟（含每张 10 分钟小测）
    left_days = max(1, (exam - start_d).days + 1)
    note = (f"{name}：排入 {inserted} 张卡 / {total_min} 分钟，"
            f"日均 {per_day} 分钟（剩 {need_min} 分钟 ÷ {left_days} 天，周末 ×{ratio / 100:.1f}"
            + (f"，每日上限 {day_cap} 分钟" if day_cap else "")
            + (f"，总预算分到 {budget} 分钟/天" if budget else "")
            + f"），覆盖到 {exam}；已完成 {len(done)} 个目标不再重排")
    if uncovered:
        core = [g["id"] for g in uncovered if g["p"] == "core"]
        if budget:
            note += (f"；⚠️ 总预算只分到 {budget} 分钟/天，还有 {len(uncovered)} 个目标排不下"
                     f"（核心 {len(core)} 个），要保全部就调高总预算或砍范围")
        elif day_cap:
            note += (f"；⚠️ 还有 {len(uncovered)} 个目标排不下（核心 {len(core)} 个），"
                     f"每天 {day_cap} 分钟的上限压住了，先调高上限或砍范围")
        else:
            note += (f"；⚠️ 还有 {len(uncovered)} 个目标排不下（核心 {len(core)} 个），"
                     f"需要加时间或砍范围")
    elif per_day > HEAVY_DAY_MIN:
        # 不限张数了就没人拦着：分钟数会一路堆上去。这里只提醒，不改计划。
        note += (f"；⚠️ 日均 {per_day} 分钟（≈{per_day / 60:.1f} 小时/天），"
                 f"时间太紧，建议砍范围或把考核日往后挪")
    db.set(f"plan_note:{cid}", note)
    if uncovered:
        db.log(f"重排预警（{name}）：{len(uncovered)} 个目标排不下，核心 {len(core)} 个", "warn")
    return {"course": cid, "inserted": inserted, "uncovered": len(uncovered), "note": note,
            "minutes": total_min, "days": len(res["plan"]), "daily_avg": per_day,
            "need_minutes": need_min, "left_days": left_days,
            "replaced": int((before or {}).get("n") or 0)}


def _open_cards(db, date_from: str, date_to: str, course: str | None = None) -> list[dict]:
    """某段日期里还没完成的卡（本地库和远端代理是同一组方法）。"""
    return [t for t in db.tasks_between(date_from, date_to, course=course)
            if t["state"] == "todo"]


def _profile(rows: list[dict]) -> dict:
    kinds: dict[str, int] = {}
    for t in rows:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    return {"cards": len(rows),
            "minutes": sum(int(t["minutes"] or 0) for t in rows),
            "learn": kinds.get("learn", 0),
            "review": kinds.get("review", 0)}


def overdue(db, course: str | None = None) -> list[dict]:
    """日期已经过去、还挂着 todo 的卡。"""
    yday = (now().date() - dt.timedelta(days=1)).isoformat()
    return _open_cards(db, "0000-01-01", yday, course)


def auto_replan(db) -> dict | None:
    """落后就重排（每门课各算各的）：把欠的卡并回队列，把「牺牲了什么」写进日志和当天推送。

    同一天只做一次（设置 auto_replan_day）；都不落后就什么都不做，返回 None。
    """
    today = today_s()
    if db.get("auto_replan_day", "") == today:
        return None

    briefs: list[str] = []
    total_lag = 0
    courses = db.courses()
    budgets = _course_budgets(db, [c["id"] for c in courses], now().date())
    for c in courses:
        cid = c["id"]
        late = overdue(db, cid)
        if not late:
            continue
        lag = len({t["date"] for t in late})
        before = _profile(_open_cards(db, today, "9999-12-31", cid))
        res = _regen_one(db, cid, None, budget=budgets.get(cid, 0))
        if res is None:
            continue
        after_rows = _open_cards(db, today, "9999-12-31", cid)
        after = _profile(after_rows)
        today_after = _profile([t for t in after_rows if t["date"] == today])
        days = "、".join(sorted({t["date"][5:] for t in late}))
        brief = (f"{c.get('name') or cid}落后 {lag} 天（{days}，{len(late)} 张卡没做），"
                 f"剩 {res['need_minutes']} 分钟摊到 {res['left_days']} 天，"
                 f"每天约 {res['daily_avg']} 分钟；今天 {today_after['cards']} 张 / "
                 f"{today_after['minutes']} 分钟；复习卡 {before['review']} → {after['review']} 张")
        if after["review"] < before["review"]:
            brief += f"（砍掉 {before['review'] - after['review']} 张复习，先保新课）"
        briefs.append(brief)
        total_lag += lag

    if not briefs:
        return None

    brief_all = "；".join(briefs) + "；考核日不变"
    db.set("auto_replan_day", today)
    db.set("replan_day", today)                # 当天推送里要提这一句
    db.set("replan_brief", brief_all)
    db.set("plan_note", brief_all)
    db.log(f"自动重排：{brief_all}", "info")
    return {"lag": total_lag, "brief": brief_all, "courses": len(briefs)}


# ------------------------------------------------------------------ 出题
def ensure_quiz(db, goal_id: str, force_new: bool = False) -> list[dict]:
    """拿到这个目标当前这一批 3 题；没有就生成（失败降级为验收点简答）。"""
    if getattr(db, "remote", False):
        return db.action("quiz/ensure", goal_id=goal_id, force_new=bool(force_new))["quiz"]
    batch = db.quiz_batch(goal_id)
    if batch and not force_new:
        return batch

    goal = db.goal(goal_id)
    if not goal:
        raise RuntimeError(f"没有目标 {goal_id}")
    hits = retrieve(goal, goal.get("title", ""), 3)
    source = "model"
    try:
        items = llm.generate_quiz(goal, hits, db)
    except llm.LLMError as e:
        db.log(f"{goal_id} 出题失败，降级为验收点简答：{e}", "warn")
        items = llm.fallback_quiz(goal)
        source = "fallback"

    no = db.next_batch_no(goal_id)
    for it in items:
        db.add_quiz(goal_id, no, it, source)
    db.log(f"{goal_id} 出题 3 道（{source}）", "ok")
    return db.quiz_batch(goal_id)


def regen_allowed(db, goal_id: str) -> bool:
    """同一目标 24 小时内最多重出 2 次；连续两次没过就别再换题了，先回炉。"""
    if getattr(db, "remote", False):
        return bool(db.call("GET", "/api/quiz/regen_allowed", query={"goal_id": goal_id})["ok"])
    if db.fail_streak(goal_id) >= 2:
        return False
    return db.quiz_batches_today(goal_id) < 3


# ------------------------------------------------------------------ 错题回炉
def add_task(db, date: str, goal_id: str, title: str, kind: str, minutes: int) -> int:
    """插一张卡（本地写库 / 远端走 API），两端同一入口。课程从目标上取。"""
    if getattr(db, "remote", False):
        return int(db.add_task(date=date, goal_id=goal_id, title=title,
                               kind=kind, minutes=int(minutes)).get("id") or 0)
    cid = (db.goal(goal_id) or {}).get("course") or "ds"
    cur = db.x("INSERT INTO tasks(date,course,goal_id,title,kind,minutes,state,created_at) "
               "VALUES(?,?,?,?,?,?,'todo',?)",
               (date, cid, goal_id, title, kind, int(minutes),
                now().strftime("%Y-%m-%d %H:%M:%S")))
    return int(cur.lastrowid)


def requeue_after_fail(db, task: dict, right: int, total: int) -> dict:
    """一张卡没考过：明天排一张回炉。

    连续两次（不同题目）都没过 → 不再换题，第二天改成 learn 回炉（先讲懂再考），
    免得一直刷题一直错。
    """
    gid = task["goal_id"]
    streak = int(db.fail_streak(gid))            # 本次已经落库，所以包含这一次
    day = (now().date() + dt.timedelta(days=1)).isoformat()
    if any(t["goal_id"] == gid for t in db.tasks_between(day, day)):
        return {"added": False, "streak": streak, "date": day, "kind": "",
                "reason": "明天已经有这个目标的卡了"}
    kind = "learn" if streak >= 2 else "review"
    title = ("回炉：" if kind == "learn" else "错题重做：") + str(task.get("title") or gid)
    add_task(db, day, gid, title, kind, 20 if kind == "learn" else 15)
    db.log(f"{gid} 没过（{right}/{total}，连续 {streak} 次）：{day} 排一张"
           f"{'回炉' if kind == 'learn' else '错题重做'}", "warn" if streak < 2 else "info")
    return {"added": True, "streak": streak, "date": day, "kind": kind, "reason": ""}


# ------------------------------------------------------------------ 判分
def grade_one(db, goal: dict, q: dict, answer: str) -> dict:
    """判一题。choice 直接比；short 先交给模型，失败退关键词。"""
    if getattr(db, "remote", False):
        return db.action("quiz/grade", quiz_id=q["id"],
                         goal_id=goal.get("id") or q.get("goal_id"), answer=answer)
    if q["type"] == "choice":
        try:
            ok = int(str(answer).strip()) == int(str(q["answer"]).strip())
        except ValueError:
            ok = False
        return {"pass": ok, "feedback": "" if ok else "答案不对"}
    try:
        res = llm.grade_short(goal, q["stem"], q.get("rubric") or [], answer, db)
    except llm.LLMError as e:
        db.log(f"批改降级为关键词：{e}", "warn")
        res = llm.grade_keywords(q.get("rubric") or [], answer)
    return res


# ------------------------------------------------------------------ 文案
def task_line(t: dict, idx: int | None = None) -> str:
    mark = {"learn": "学", "review": "复", "exam": "考", "drill": "练"}.get(t["kind"], "学")
    state = {"done": " ✅", "dropped": " ⏭", "skipped": " ·"}.get(t["state"], "")
    prefix = f"{idx}. " if idx else ""
    title = html.escape(str(t["title"] or ""), quote=False)   # 推送是 HTML 模式，标题里的 & < 要转义
    return f"{prefix}[{mark}] {title}（{t['minutes']} 分钟）{state}"


def today_digest(db) -> str:
    ts = db.today_tasks()
    head = f"📅 {now().strftime('%m月%d日')}"
    if not ts:
        text = head + "\n\n今天没有排任务 —— 可能是计划还没生成，或者已经全完成了。"
    else:
        groups: dict[str, list[dict]] = {}
        for t in ts:
            groups.setdefault(t.get("course") or "ds", []).append(t)
        blocks = []
        for cid, items in groups.items():
            c = db.course(cid) or {}
            try:
                left = (dt.date.fromisoformat(c.get("exam_date") or "") - now().date()).days
            except (TypeError, ValueError):
                left = 0
            lines = [task_line(t, i) for i, t in enumerate(items, 1)]
            blocks.append(f"【{c.get('name') or cid}】距考 {left} 天\n" + "\n".join(lines))
        text = head + "\n\n" + "\n\n".join(blocks) + \
            "\n\n点下面的按钮开始，或者直接说「学哪一个」。"
    # 今天重排过：牺牲了什么必须让用户看到（铁律 3）
    if db.get("replan_day", "") == today_s() and db.get("replan_brief", ""):
        text += f"\n\n⚠️ 计划重排：{db.get('replan_brief')}"
    return text


# ------------------------------------------------------------------ 资料入库
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
TEXT_EXT = {".pdf", ".pptx", ".docx", ".md", ".txt"}


def inbox_files(course: str | None = None) -> list[Path]:
    """补料入口：数据结构用 materials/inbox，其他课用 materials/<课程>/inbox。"""
    inbox = paths.materials_dir(course) / "inbox"
    if not inbox.exists():
        return []
    return sorted(p for p in inbox.rglob("*") if p.is_file())


def ingest_inbox(db, progress=None, course: str | None = None) -> dict:
    """扫 inbox → 抽文本 → 写 extracted → 记库。已入库的跳过。"""
    if getattr(db, "remote", False):
        return _ingest_remote(db, progress, course)
    import extract  # tools/extract.py

    files = inbox_files(course)
    new, skipped, failed = 0, 0, 0
    out_dir = paths.pending_dir(course)
    for p in files:
        if p.suffix.lower() not in TEXT_EXT:
            skipped += 1
            continue
        rel = paths.rel(p)
        h = db_sha1(p)
        known = db.material_known(rel)
        if known and known["hash"] == h:
            skipped += 1
            continue
        if progress:
            progress(f"解析 {p.name}…")
        try:
            txt = extract.extract_one(p)
        except Exception as e:  # noqa: BLE001
            failed += 1
            db.add_material(rel, h, 0, "failed")
            db.log(f"入库失败 {p.name}：{type(e).__name__}: {e}", "err")
            continue
        if not txt.strip():
            failed += 1
            db.add_material(rel, h, 0, "failed")
            db.log(f"{p.name} 没有文字（可能是扫描件），跳过", "warn")
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / (p.stem + ".txt")
        if dst.exists():
            try:
                old = dst.read_text(encoding="utf-8", errors="replace")
            except OSError:
                old = ""
            if old.strip() and old.strip() != txt.strip():
                dst = out_dir / f"{p.stem}-{h[:6]}.txt"
        dst.write_text(txt, encoding="utf-8")
        db.add_material(rel, h, len(txt), "ready")
        db.log(f"入库 {p.name}：{len(txt):,} 字符", "ok")
        new += 1

    # 检索层缓存作废，新资料立刻可用
    try:
        import retrieval as _r
        _r._BLOCKS = None
    except Exception:  # noqa: BLE001
        pass

    if new:
        db.log(f"入库完成：新增 {new} 份资料。要进计划，需要重拆目标。", "ok")
    return {"new": new, "skipped": skipped, "failed": failed, "files": len(files)}


def _ingest_remote(db, progress=None, course: str | None = None) -> dict:
    """瘦客户端：文本在本地抽（PDF 解析留在电脑上），只把文本传服务器。"""
    import extract

    files = inbox_files(course)
    new, skipped, failed = 0, 0, 0
    for p in files:
        if p.suffix.lower() not in TEXT_EXT:
            skipped += 1
            continue
        rel = paths.rel(p)
        h = db_sha1(p)
        known = db.material_known(rel)
        if known and known["hash"] == h:
            skipped += 1
            continue
        if progress:
            progress(f"解析 {p.name}…")
        try:
            txt = extract.extract_one(p)
        except Exception as e:  # noqa: BLE001
            failed += 1
            db.log(f"入库失败 {p.name}：{type(e).__name__}: {e}", "err")
            continue
        if not txt.strip():
            failed += 1
            db.log(f"{p.name} 没有文字（可能是扫描件），跳过", "warn")
            continue
        db.action("materials/upload", path=rel, hash=h, text=txt, course=course or "")
        new += 1
    if new:
        db.log(f"入库完成：新增 {new} 份资料。要进计划，需要重拆目标。", "ok")
    return {"new": new, "skipped": skipped, "failed": failed, "files": len(files)}


def db_sha1(p: Path) -> str:
    from db import sha1_file
    return sha1_file(p)


def course_of_path(p: str) -> str:
    """资料属于哪门课：materials/<课程>/… → 这门课；scut/ 老路径和 inbox → 数据结构。"""
    parts = Path(str(p).replace("\\", "/")).parts
    if len(parts) >= 2 and parts[0] == "materials" and parts[1] not in ("scut", "inbox"):
        return parts[1]
    return "ds"


# ------------------------------------------------------------------ 资料 → 目标
# 资料进来之后由程序自己拆成目标，不用人（或助手）先在外面写好 goals.json。
DERIVE_CHARS = 24000        # 一次喂给模型的字数上限（约 ¥0.1/万字 → 一块约 ¥0.1）
DERIVE_MAX_GOALS = 12       # 一块资料最多拆几个目标；宁可少而准
DERIVE_MAX_CHUNKS = 8       # 一次最多拆几块（再大就让人分批，免得一口气烧钱）
GOAL_MINUTES = (10, 120)    # 单个目标估时的合法范围

SYSTEM_SPLIT = """你是学习内容的结构化专家。给你一门课的**新资料**片段，以及这门课**已有目标的标题和验收点**。
把新资料拆成可执行、可验收的学习目标，只输出已有目标没覆盖到的新目标。

规则：
1. 每个目标必须能在 25~45 分钟内学完，学完能被追问 3 个问题而不倒。
2. 粒度：一个定义 + 一个推导 + 一个应用场景 = 1 个目标；不要"第三章"这种书本单位。
3. 覆盖资料里的核心概念；铺垫/背景标 support；与考核无关的细枝末节标 optional。
4. 有先后依赖的写进 deps，用本次输出的本地 id（n1、n2…）；依赖已有目标的留空。
5. 估时宁多勿少：你估 20 分钟的内容，真实往往要 35 分钟。
6. **语义重复的不要再输出**：哪怕换了说法（「二叉树遍历」和「二叉树的遍历方式」算同一个），
   只要已有目标已经覆盖，就跳过；不要为了凑数换标题重出一遍。
7. 最多 {max_goals} 个，宁可少而准，不要凑数。
8. 只输出 JSON，不要任何解释文字。"""


def _derive_user(subject: str, exam: str, existing: list[dict], text: str) -> str:
    lines = []
    for g in existing[:200]:
        cps = "；".join(str(x) for x in (g.get("cp") or [])[:3])
        lines.append(f"- {g.get('title', '')}" + (f"（验收点：{cps}）" if cps else ""))
    titles = "\n".join(lines) or "（还没有目标）"
    return (f"科目：{subject}\n考核日：{exam}\n\n"
            f"已有目标（这些不要再拆，换说法的重复项也不要）：\n{titles}\n\n"
            f"新资料：\n---\n{text}\n---\n\n"
            "输出 JSON：\n"
            '{"goals":[{"id":"n1","ch":"章节","title":"动词开头，一句话说清要掌握什么",'
            '"priority":"core|support|optional","est_minutes":30,"deps":[],'
            '"checkpoints":["能被问住的点1","能被问住的点2"]}]}')


def _text_for(course: str, material_path: str) -> Path | None:
    """材料相对路径 → 抽出来的 .txt（待拆/已拆/inbox 都找一遍）。"""
    p = Path(str(material_path).replace("\\", "/"))
    stem = p.stem
    base = paths.extracted_dir(course)
    roots = (paths.pending_dir(course), paths.done_dir(course), base)
    parts = list(p.parts)
    if parts and parts[0] == "materials":
        parts = parts[1:]
    if parts and parts[0] in (course, "scut"):
        parts = parts[1:]
    mirror = (base / Path(*parts)).with_suffix(".txt") if parts else None
    for root in roots:
        for t in (root / "inbox" / f"{stem}.txt", root / f"{stem}.txt"):
            if t.is_file():
                return t
    if mirror is not None and mirror.is_file():
        return mirror
    for root in roots:
        if not root.exists():
            continue
        for t in root.rglob(f"{stem}.txt"):
            if ".media" in t.parts or t.name.endswith(".old.txt"):
                continue
            return t
    return None


def _norm_title(s: str) -> str:
    return re.sub(r"[\s，。：、；（）()【】\[\]·\-—_/,.]", "", str(s or "")).lower()


def _title_grams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def _title_similar(a: str, b: str) -> bool:
    """标题近似：同一知识点被换了个更长的说法（「递归的写法与递归式」/ 后面再缀一章）。

    用字符 bigram 的覆盖率，不用编辑距离：中文换个说法字面差很多，但二字组重合高。
    阈值定得高（0.85）是有意的：「线性表顺序存储」和「线性表链式存储」只差一个词，
    但它们是两个目标，不能合并。太短的标题（<5 字）只认完全相同。
    """
    if a == b:
        return True
    if min(len(a), len(b)) < 5:
        return False
    ga, gb = _title_grams(a), _title_grams(b)
    inter = len(ga & gb)
    return inter / max(1, min(len(ga), len(gb))) >= 0.85


def _clean_goals(items: list, seen_titles: list[str]) -> tuple[list[dict], list[str]]:
    """把模型给的目标收拾成内部结构：去重、夹估时、补验收点、记本地 id。

    返回 (目标列表, 被判定为重复而跳过的标题)。重复判断 = 归一化后完全相同，
    或标题近似（换了个说法）。模型偶尔会无视提示重出一遍，这一层兜底。
    """
    seen = [_norm_title(t) for t in seen_titles if str(t).strip()]
    out: list[dict] = []
    dups: list[str] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        if len(title) < 4:
            continue
        key = _norm_title(title)
        if any(_title_similar(key, s) for s in seen):
            dups.append(title)
            continue
        try:
            m = int(it.get("est_minutes") or it.get("m") or 30)
        except (TypeError, ValueError):
            m = 30
        p = str(it.get("priority") or it.get("p") or "core")
        if p not in ("core", "support", "optional"):
            p = "core"
        cps = [str(x).strip()[:60] for x in (it.get("checkpoints") or it.get("cp") or [])
               if str(x).strip()][:5] or [title]
        out.append({"local": str(it.get("id") or f"n{len(out) + 1}"),
                    "ch": str(it.get("ch") or "").strip()[:20], "title": title, "p": p,
                    "m": max(GOAL_MINUTES[0], min(GOAL_MINUTES[1], m)), "cp": cps,
                    "deps": [str(d) for d in (it.get("deps") or [])]})
        seen.append(key)
    return out, dups


def pending_files(course: str) -> list[Path]:
    """待拆文件夹里的 txt（按课程）。文件夹就是队列：挪进来=要拆，挪出去=不拆。"""
    base = paths.pending_dir(course)
    if not base.exists():
        return []
    out = []
    for p in sorted(base.rglob("*.txt")):
        if p.name.endswith(".old.txt") or ".media" in p.parts:
            continue
        out.append(p)
    return out


def pending_stats(course: str) -> dict:
    files = pending_files(course)
    chars = 0
    for p in files:
        try:
            chars += len(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return {"files": len(files), "chars": chars,
            "blocks": (chars + DERIVE_CHARS - 1) // DERIVE_CHARS}


def _move_to_done(course: str, files: list[Path]) -> int:
    """拆成功后把用到的 txt 从 待拆/ 挪到 已拆/（保留子目录；检索仍然扫得到）。"""
    base = paths.pending_dir(course)
    done = paths.done_dir(course)
    changed: dict[str, str] = {}
    for p in files:
        try:
            rel = p.relative_to(base)
        except ValueError:
            rel = Path(p.name)
        dst = done / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst = dst.with_name(f"{dst.stem}-{now().strftime('%H%M%S')}{dst.suffix}")
        try:
            shutil.move(str(p), str(dst))
        except OSError:
            continue
        changed[str(p)] = str(dst)
    if changed:
        m = _manifest_read()
        for it in m["items"]:
            t = str(it.get("text") or "")
            if t in changed:
                it["text"] = changed[t]
        _manifest_write(m)
        try:
            import retrieval as _r
            _r._BLOCKS = None          # 路径变了，检索块要重算
        except Exception:  # noqa: BLE001
            pass
    return len(changed)


def migrate_pending_layout(db=None) -> int:
    """把老布局的 txt 挪进 待拆/<课>/（保留子目录）。

    老布局：extracted/inbox、extracted/书、extracted/笔记…（都归 ds）；
    新布局：extracted/待拆/ds/inbox/…、extracted/已拆/ds/…
    幂等：待拆/已拆 下面的不再动。
    """
    root = paths.extracted_dir(None)
    if not root.exists():
        return 0
    cids = {c["id"] for c in db.courses()} if db is not None else {"ds"}
    legacy = {"inbox", "PPT", "书", "作业", "笔记", "试卷", "大作业", "考试题型或复习大纲"}
    changed: dict[str, str] = {}

    def take(p: Path, cid: str, rel: Path) -> None:
        if p.name.endswith(".old.txt") or ".media" in p.parts:
            return
        dst = paths.pending_dir(cid) / rel
        if dst.exists():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(p), str(dst))
        except OSError:
            return
        changed[str(p)] = str(dst)

    for top in sorted(root.iterdir()):
        if top.is_file():
            if top.suffix.lower() == ".txt":
                take(top, "ds", Path(top.name))
            continue
        if top.name in ("待拆", "已拆"):
            continue
        if top.name in cids:                 # extracted/<课>/…
            for p in sorted(top.rglob("*.txt")):
                take(p, top.name, p.relative_to(top))
        elif top.name in legacy:             # extracted/inbox、extracted/书…（都归 ds）
            for p in sorted(top.rglob("*.txt")):
                take(p, "ds", p.relative_to(root))
    if changed:
        m = _manifest_read()
        for it in m["items"]:
            t = str(it.get("text") or "")
            if t in changed:
                it["text"] = changed[t]
        _manifest_write(m)
        try:
            import retrieval as _r
            _r._BLOCKS = None
        except Exception:  # noqa: BLE001
            pass
    return len(changed)


def new_materials(db, course: str) -> list[dict]:
    """上次拆目标之后新入库、且还没被拆过的资料。"""
    since = db.get(f"goals_derived_at:{course}", "")
    rows = [m for m in db.materials()
            if (m.get("status") or "") == "ready" and course_of_path(m["path"]) == course]
    return [m for m in rows if not since or (m.get("imported_at") or "") > since]


def derive_goals(db, course: str | None = None, files: list[Path] | None = None,
                 progress=None) -> dict:
    """把「待拆」里选中的 txt 拆成目标 → 写 goals_<课>.json 和库 → 重排 → 挪到已拆。

    files 为 None 时拆待拆文件夹里全部；传了只拆这几份（UI 勾选用）。
    模型失败就抛错、**一个目标都不写**；拆成功才挪 txt。
    """
    if getattr(db, "remote", False):              # 瘦客户端：拆目标在服务器上跑
        return db.action("goals/derive", course=course or "")["result"]

    cid = course or db.current_course()
    c = db.course(cid) or {}
    name = c.get("name") or cid
    chosen = [Path(x) for x in files] if files else pending_files(cid)
    chosen = [p for p in chosen if p.is_file()]
    if not chosen:
        return {"added": 0, "titles": [], "minutes": 0, "chars": 0, "files": [],
                "moved": 0, "chunks": 0,
                "note": f"{name}：待拆文件夹是空的（先处理 inbox，或把 txt 放进待拆）"}

    # 按 DERIVE_CHARS 切块，一块一次模型调用；不再设「一次最多几块」，
    # 由 UI 里勾选哪几份 + 显示预估费用来控制体量。
    chunks: list[str] = []
    cur: list[str] = []
    cur_chars = 0
    names: list[str] = []
    used_files: list[Path] = []
    chars = 0
    for p in chosen:
        try:
            body = p.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as e:  # noqa: BLE001
            db.log(f"拆目标跳过 {p.name}：{e}", "warn")
            continue
        if not body:
            continue
        names.append(p.name)
        used_files.append(p)
        chars += len(body)
        while body:
            if cur_chars >= DERIVE_CHARS:
                chunks.append("\n\n".join(cur))
                cur, cur_chars = [], 0
            room = DERIVE_CHARS - cur_chars
            piece, body = body[:room], body[room:]
            cur.append(piece)
            cur_chars += len(piece)
    if cur:
        chunks.append("\n\n".join(cur))
    if not chunks:
        raise RuntimeError("选中的资料里没有可用文本（可能是扫描件或抽取失败）")

    known = db.goals(course=cid)
    subject = c.get("name") or db.get("subject") or cid
    exam = c.get("exam_date") or db.get("exam_date") or ""
    found: list[dict] = []
    dups: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        if progress:
            progress(f"拆目标 {i}/{len(chunks)}（{len(chunk):,} 字）…")
        msgs = [{"role": "system", "content": SYSTEM_SPLIT.format(max_goals=DERIVE_MAX_GOALS)},
                {"role": "user", "content": _derive_user(subject, exam, known, chunk)}]
        text = llm.chat(msgs, db=db, max_tokens=4000, temperature=0.3, timeout=180,
                        extra={"thinking": {"type": "disabled"}})
        obj = llm.extract_json(text)
        items = obj.get("goals") if isinstance(obj, dict) else obj
        if not isinstance(items, list):
            raise llm.LLMError("拆目标没有返回 goals 数组")
        cleaned, skipped = _clean_goals(items, [g["title"] for g in known])
        found.extend(cleaned)
        known.extend(cleaned)          # 后面几块也能看见这次新拆的
        dups.extend(skipped)

    # 分配 id（接着现有 gNN 往后排），再把本地依赖映射过去
    used = {g["id"] for g in db.goals(course=cid)}
    nums = [int(x[1:]) for x in used if re.fullmatch(r"g\d+", x)]
    nxt = max(nums or [0]) + 1
    id_of: dict[str, str] = {}
    for g in found:
        gid = f"g{nxt:02d}"
        while gid in used:
            nxt += 1
            gid = f"g{nxt:02d}"
        used.add(gid)
        nxt += 1
        id_of[g["local"]] = gid
        g["id"] = gid
    for g in found:
        g["d"] = [id_of[d] for d in g["deps"] if d in id_of]

    # 写回 goals_<课>.json（数据是纯文本，程序只是读写它），再导进库
    fp = paths.goals_out(cid)
    data: dict = {}
    if fp.exists():
        try:
            data = json.loads(fp.read_text(encoding="utf-8-sig"))
        except ValueError:
            data = {}
    data.setdefault("subject", subject)
    data.setdefault("exam_date", exam)
    data["goals"] = [{k: g.get(k) for k in ("id", "ch", "title", "p", "m", "d", "cp")}
                     for g in db.goals(course=cid)] + \
                    [{"id": g["id"], "ch": g["ch"], "title": g["title"], "p": g["p"],
                      "m": g["m"], "d": g["d"], "cp": g["cp"]} for g in found]
    fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    db.import_goals(fp, course=cid)
    db.set(f"goals_derived_at:{cid}", now().strftime("%Y-%m-%d %H:%M:%S"))

    plan = regenerate(db, course=cid)
    moved = _move_to_done(cid, used_files)
    kb = write_knowledge(db, course=cid) if found else {"written": 0, "changed": 0}
    db.log(f"拆目标（{name}）：{chars:,} 字 → 新增 {len(found)} 个目标，"
           f"来自 {len(names)} 份资料；{moved} 份挪到已拆", "ok")
    if dups:
        db.log(f"拆目标（{name}）：跳过 {len(dups)} 个疑似重复："
               f"{'、'.join(dups[:8])}", "info")
    note = plan["note"] if found else f"{name}：模型认为选中的资料没有需要新增的目标"
    if dups:
        note += f"；跳过 {len(dups)} 个与已有目标重复的（换说法的也算）"
    return {"added": len(found), "titles": [g["title"] for g in found],
            "minutes": sum(g["m"] + 10 for g in found), "chars": chars,
            "files": names, "moved": moved, "chunks": len(chunks),
            "note": note, "uncovered": plan.get("uncovered", 0),
            "duplicates": len(dups),
            "knowledge": kb.get("written", 0)}


# ------------------------------------------------------------------ 一级：原始资料 → 文本 → 归档
MANIFEST_VERSION = 1

# 文件名里的版本尾巴：「第3章 v2」「第3章-最终」「第3章(1)」都算同一份资料的新版
_VER_TAIL = re.compile(
    r"(?i)(?:v\d+(?:\.\d+)*|final|最终版?|修改版?|修订版?|新版|旧版|副本|copy|\(\d+\)|（\d+）)$"
)


def _doc_key(name: str) -> str:
    """资料名去掉版本后缀，用来判断「这是同一份资料的新版本」。"""
    s = Path(str(name or "")).stem.strip().lower()
    for _ in range(3):
        nxt = _VER_TAIL.sub("", s).strip()
        if nxt == s:
            break
        s = nxt
    return re.sub(r"[\s_\-—·（）()\[\]【】]+", "", s)


def _manifest_read() -> dict:
    p = paths.manifest_file()
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(m, dict) and isinstance(m.get("items"), list):
            return m
    except (OSError, ValueError):
        pass
    return {"version": MANIFEST_VERSION, "items": []}


def _manifest_write(m: dict) -> None:
    p = paths.manifest_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    m["version"] = MANIFEST_VERSION
    m["updated_at"] = now().strftime("%Y-%m-%d %H:%M:%S")
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def _to_trash(p: Path, course: str) -> Path:
    """原件挪进「回收站/课程/」，不是硬删；重名加时间戳。"""
    dst_dir = paths.trash_dir() / (course or "ds")
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / p.name
    if dst.exists():
        dst = dst_dir / f"{p.stem}-{now().strftime('%Y%m%d-%H%M%S')}{p.suffix}"
    shutil.move(str(p), str(dst))
    return dst


def _tool_settings(db, course: str | None = None) -> dict:
    """外部工具路径：设置页填了就用，没填就按默认位置 / PATH 找。

    课程语言设成 en 且有英文模型时，自动换 `.en` 模型并传 `-l en`（英文模型只认英文）。
    否则用通用模型、语言自动检测。
    """
    td = db.get("tessdata_dir", "")
    if not td:
        cand = paths.tools_dir() / "tessdata"
        td = str(cand) if cand.is_dir() else ""
    model = db.get("whisper_model", "")
    lang = db.get(f"lang:{course}", "") if course else ""
    en_model = db.get("whisper_model_en", "")
    if lang == "en" and en_model:
        model, use_lang = en_model, "en"
    else:
        use_lang = ""
    return {"tesseract": db.get("tesseract_path", ""),
            "ffmpeg": db.get("ffmpeg_path", ""),
            "whisper": db.get("whisper_path", ""),
            "whisper_model": model,
            "whisper_lang": use_lang,
            "tessdata": td}


def check_tools(db) -> dict:
    """检查外部工具装没装好（设置页「检查工具」用）。"""
    import shutil as _sh

    import extract as ex

    t = _tool_settings(db)

    def probe(name: str, cmd: str, args: list[str]) -> dict:
        exe = cmd if cmd and Path(cmd).is_file() else (_sh.which(cmd) or "")
        if not exe:
            return {"ok": False, "detail": f"没找到 {name}（PATH 里没有，设置里也没填）"}
        try:
            r = ex.run_tool([exe, *args], timeout=15)
            first = (r.stdout or r.stderr or "").strip().splitlines()
            return {"ok": True, "detail": first[0][:80] if first else exe}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": f"{type(e).__name__}: {e}"}

    out = {
        "tesseract": probe("tesseract", t["tesseract"] or "tesseract", ["--version"]),
        "ffmpeg": probe("ffmpeg", t["ffmpeg"] or "ffmpeg", ["-version"]),
    }
    try:
        import pypdfium2 as _pdfium
        out["pdfium"] = {"ok": True,
                         "detail": f"PDFium {getattr(_pdfium, 'PDFIUM_INFO', '')}（扫描版/加密 PDF）"}
    except Exception as e:  # noqa: BLE001
        out["pdfium"] = {"ok": False,
                         "detail": f"没装 pypdfium2（扫描版/加密 PDF 用不了）：{e}"}
    office = ex.office_available()
    out["office"] = {"ok": bool(office),
                     "detail": office or "没找到 Office/LibreOffice（.doc/.ppt 读不了）"}
    wse = t["whisper"] if t["whisper"] and Path(t["whisper"]).is_file() else (_sh.which("whisper-cli") or "")
    out["whisper"] = {"ok": bool(wse), "detail": wse or "没找到 whisper-cli（whisper.cpp 的命令行）"}
    model = t["whisper_model"]
    out["model"] = {"ok": bool(model and Path(model).is_file()),
                    "detail": model or "没填 whisper 模型路径",
                    "size": round(Path(model).stat().st_size / 1e6, 1) if model and Path(model).is_file() else 0}
    en = db.get("whisper_model_en", "")
    out["en_model"] = {"ok": bool(en and Path(en).is_file()),
                       "detail": en or "没填英文模型（英文课会退回通用模型，慢很多）"}
    td = t["tessdata"]
    has_cn = bool(td) and (Path(td) / "chi_sim.traineddata").is_file()
    out["chi_sim"] = {"ok": has_cn, "detail": (str(Path(td) / "chi_sim.traineddata") if has_cn
                                               else "缺中文语言包 chi_sim.traineddata")}
    return out


def process_inbox(db, course: str | None = None, progress=None, cancel=None,
                  workers: int = 0) -> dict:
    """一级资料 → 文本 → 归档（可并行、能中断、断了能续）。

    - 文本/图片并行（默认最多 4 路，按 CPU 核数），音视频单独串行（whisper 自己就吃满多核）
    - **每份处理完立刻落台账**：中途关软件/断电，下次只用处理还没 done 的
    - 成功才动原件：文本写 `extracted/<课>/inbox/`，原件挪 `回收站/<课>/`；缺工具/失败留着
    - progress(dict) 每份给一次：{total, done, name, status, reason}
    - cancel 是 threading.Event：置位后不再开始新的（正在跑的那份会跑完）
    """
    if getattr(db, "remote", False):
        raise RuntimeError("云端模式下「处理资料」还没接（现在只能在本地处理）")
    import concurrent.futures as cf
    import os as _os

    import extract as ex

    cid = course or db.current_course()
    files = [p for p in inbox_files(cid) if p.is_file()]
    tools = _tool_settings(db, cid)
    manifest = _manifest_read()
    done_keys = {(i.get("sha1"), i.get("course")) for i in manifest["items"]
                 if i.get("status") == "done"}
    out_dir = paths.pending_dir(cid)
    # 文本文件名 → 它属于哪份原件（sha1）。同名不同内容不能互相覆盖：
    # 比如两份都叫「第3章.pdf」，第二份会写成 第3章-<sha1前6位>.txt。
    text_owner = {str(i.get("text") or ""): i.get("sha1")
                  for i in manifest["items"] if i.get("text")}
    st = {"done": 0, "kept": 0, "failed": 0, "skipped": 0, "chars": 0,
          "moved": [], "hints": {}, "cancelled": False}
    total = len(files)

    def emit(name: str = "", status: str = "", reason: str = "",
             frac: float | None = None) -> None:
        if not progress:
            return
        finished = st["done"] + st["kept"] + st["failed"] + st["skipped"]
        pct = 100.0 if total == 0 else (finished + (frac or 0.0)) / total * 100
        try:
            progress({"total": total, "name": name, "status": status, "reason": reason,
                      "frac": frac or 0.0, "pct": round(min(100.0, pct), 1),
                      "finished": finished, "done": st["done"], "kept": st["kept"],
                      "failed": st["failed"], "chars": st["chars"]})
        except Exception:  # noqa: BLE001  UI 回调出错不能拖垮处理
            pass

    def settle(entry: dict) -> None:
        # 同一份文件重试多次只留最新一条（原来会一条条堆成好几行）
        items = manifest["items"]
        for i, old in enumerate(items):
            if old.get("course") == entry.get("course") and old.get("sha1") == entry.get("sha1"):
                items[i] = entry
                break
        else:
            items.append(entry)
        _manifest_write(manifest)            # 立刻落盘：断了也能续

    def entry_of(p: Path, h: str) -> dict:
        return {"course": cid, "path": paths.rel(p), "name": p.name,
                "size": p.stat().st_size if p.exists() else 0, "sha1": h,
                "kind": ex.kind_of(p), "processed_at": now().strftime("%Y-%m-%d %H:%M:%S")}

    def do_one(p: Path, h: str, res) -> None:
        """把一份的结果收尾：写文本、挪原件、记台账、报进度。res 是 (文本, 类型) 或异常。"""
        e = entry_of(p, h)
        if isinstance(res, BaseException):
            if isinstance(res, ex.MissingTool):
                e.update(status="kept", reason=str(res))
                st["kept"] += 1
                st["hints"][res.tool] = res.hint
            else:
                e.update(status="failed", reason=f"{type(res).__name__}: {res}")
                st["failed"] += 1
                db.log(f"处理失败 {p.name}：{e['reason']}", "err")
            settle(e)
            emit(p.name, e["status"], e["reason"])
            return
        text, kind = res
        text = (text or "").strip()
        if not text:
            e.update(status="failed", reason="没抽出文字（扫描件/没字幕/空文件）")
            st["failed"] += 1
            settle(e)
            emit(p.name, "failed", e["reason"])
            return
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / (p.stem + ".txt")
        owner = text_owner.get(str(dst))
        if owner and owner != h:                  # 同名文件、内容不同 → 别覆盖
            dst = out_dir / f"{p.stem}-{h[:6]}.txt"
        text_owner[str(dst)] = h
        dst.write_text(text, encoding="utf-8")

        # 同名旧版让位：旧文本改名 *.old.txt（不删，能翻回来），检索只读新版。
        # 只在「同一份资料换了个版本」时做；换个名字的新资料不会被当成版本。
        for old in manifest["items"]:
            if old.get("course") != cid or old.get("sha1") == h:
                continue
            if old.get("status") != "done" or not old.get("text"):
                continue
            if old.get("superseded_by"):       # 已经让过位，别再叠一层
                continue
            if _doc_key(old.get("name") or "") != _doc_key(p.name):
                continue
            old_path = Path(str(old["text"]))
            if old_path == dst or not old_path.is_file():
                continue
            old_new = old_path.with_name(old_path.stem + ".old.txt")
            try:
                if old_new.exists():
                    old_new.unlink()
                old_path.rename(old_new)
            except OSError:
                continue
            old["text"] = str(old_new)
            old["superseded_by"] = h
            old["superseded_at"] = now().strftime("%Y-%m-%d %H:%M:%S")
            db.log(f"旧版让位：{old.get('name')}（检索只用新版 {p.name}）", "info")
        try:
            db.add_material(paths.rel(p), h, len(text), "ready")   # 台账之外，库里也记一笔
        except Exception:  # noqa: BLE001
            pass                                # 库写失败也不能把已抽好的文本扔掉
        trash = ""
        try:
            trash = str(_to_trash(p, cid))
        except OSError as err:                  # 文件被占用：文本已存，原件先留着
            db.log(f"{p.name} 挪不进回收站（{err}），原件先留着", "warn")
        e.update(status="done", kind=kind, chars=len(text), text=str(dst), trashed=trash)
        st["done"] += 1
        st["chars"] += len(text)
        st["moved"].append(p.name)
        settle(e)
        emit(p.name, "done")

    def safe(p: Path):
        # 音视频按 10 分钟切片，片段结果存在 .media/<文件名>/，断了能接着转
        t = dict(tools)
        t["parts_dir"] = out_dir / ".media" / p.stem
        try:
            return ex.extract_any(
                p, t,
                on_progress=lambda i, n: emit(
                    p.name, "running", f"第 {i}/{n} 段",
                    frac=((i - 1) / n) if n else 0.0))
        except BaseException as err:            # noqa: BLE001  交给 do_one 分类
            return err

    def pending(p: Path) -> str | None:
        h = db_sha1(p)
        if (h, cid) in done_keys:
            try:
                _to_trash(p, cid)               # 同一份处理过，重复的原件不用留
            except OSError:
                pass
            st["skipped"] += 1
            emit(p.name, "skipped", "处理过，重复")
            return None
        return h

    fast, slow = [], []
    for p in files:
        # 音视频串行（whisper 吃满多核）；.doc/.ppt 也串行（Word/PowerPoint 是单实例，
        # 4 路并行开会互相抢 COM）
        serial = ex.kind_of(p) in ("video", "audio") or p.suffix.lower() in (".doc", ".ppt")
        (slow if serial else fast).append(p)

    workers = workers or max(1, min(4, (_os.cpu_count() or 4)))
    emit("", "start")
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {}
        for p in fast:
            if cancel is not None and cancel.is_set():
                st["cancelled"] = True
                break
            h = pending(p)
            if not h:
                continue
            emit(p.name, "running")
            futs[pool.submit(safe, p)] = (p, h)
        for fut in cf.as_completed(futs):
            p, h = futs[fut]
            do_one(p, h, fut.result())

    for p in slow:                              # 音视频：串行，且不设超时
        if cancel is not None and cancel.is_set():
            st["cancelled"] = True
            break
        h = pending(p)
        if not h:
            continue
        emit(p.name, "running")
        do_one(p, h, safe(p))

    try:
        import retrieval as _r
        _r._BLOCKS = None                       # 新文本立刻可检索
    except Exception:  # noqa: BLE001
        pass
    db.log(f"处理资料（{cid}）：成功 {st['done']} 份 / {st['chars']:,} 字，"
           f"留原件 {st['kept']}，失败 {st['failed']}，跳过 {st['skipped']}"
           + ("，已取消" if st["cancelled"] else ""), "ok")
    return {"done": st["done"], "kept": st["kept"], "failed": st["failed"],
            "skipped": st["skipped"], "chars": st["chars"], "moved": st["moved"],
            "hints": st["hints"], "files": total, "cancelled": st["cancelled"]}


# ------------------------------------------------------------------ 二级：知识库（Markdown）
def _slug(s: str, n: int = 36) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "-", str(s or "").strip())
    return (s[:n] or "goal").strip("-")


def _goal_md(db, goal: dict, version: int) -> str:
    hits = retrieve(goal, goal.get("title", ""), 4)
    cps = goal.get("cp") or []
    deps = goal.get("d") or []
    cid = goal.get("course") or "ds"
    cname = (db.course(cid) or {}).get("name") or "资料"
    lines = [
        "---",
        f"id: {goal['id']}",
        f"course: {cid}",
        f"course_name: {cname}",
        f"chapter: {goal.get('ch') or ''}",
        f"title: {goal.get('title') or ''}",
        f"priority: {goal.get('p') or 'core'}",
        f"minutes: {int(goal.get('m') or 30)}",
        f"version: {version}",
        f"source: {cname}",
        # 知识树的边：先修用 deps，父节点 parent 先留空（以后换骨架只改这一行）
        f"deps: {json.dumps(deps, ensure_ascii=False)}",
        f"parent: {goal.get('parent') or ''}",
        "tags: []",
        "---",
        "",
        f"# {goal.get('title') or goal['id']}",
        "",
        f"> {cname} · {goal.get('ch') or '未分章'} · {int(goal.get('m') or 30)} 分钟",
        "",
        "## 要掌握（验收点）",
        "",
    ]
    lines += [f"- {c}" for c in cps] or ["- （还没有验收点）"]
    if deps:
        lines += ["", "## 先修", "", *[f"- {d}" for d in deps]]
    lines += ["", "## 资料摘录", ""]
    if hits:
        for e in hits:
            body = "\n".join(f"> {x}" for x in str(e.get("text") or "").splitlines() if x.strip())
            lines += [f"### 《{e.get('src', '')}》{e.get('anchor', '')}", "", body, ""]
    else:
        lines += ["（这门课还没有能检索到的原文）", ""]
    lines += ["## 说明", "",
              "全库知识库的一个节点：按「章」放在同一个库里，科目写在 `course:`。",
              "只由「拆目标 / 重建知识库」生成，平时不手改；改过的内容重建时会被覆盖。",
              ""]
    return "\n".join(lines)


def _goal_hash(g: dict) -> str:
    """目标字段指纹：章/标题/验收点/依赖/估时/优先级/科目变了才算变。"""
    raw = json.dumps([g.get("id"), g.get("ch"), g.get("title"), g.get("p"),
                      int(g.get("m") or 30), g.get("cp") or [], g.get("d") or [],
                      g.get("course") or "ds"], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


_blocks_rev_cache: str | None = None


def blocks_rev() -> str:
    """检索块的版本：变了说明原文/切块变了，知识库的摘录要重渲染。

    资料一入库 `retrieval._BLOCKS` 会被置空，这里跟着重算，不会用旧版本。
    """
    global _blocks_rev_cache
    r = retrieval_mod()
    if r is None:
        return ""
    if _blocks_rev_cache is not None and getattr(r, "_BLOCKS", None) is not None:
        return _blocks_rev_cache
    try:
        blocks, _ = r._ensure()
    except Exception:     # noqa: BLE001
        return _blocks_rev_cache or ""
    h = hashlib.sha1()
    for b in blocks:
        h.update((b.get("rel") or "").encode())
        h.update(b"|")
        h.update((b.get("anchor") or "").encode())
        h.update(b"|")
        h.update(str(len(b.get("text") or "")).encode())
    _blocks_rev_cache = h.hexdigest()[:16]
    return _blocks_rev_cache


def _kb_archive(base: Path, path: Path, rel: Path | None = None) -> None:
    """旧文件留一份到 `知识库/.history/<时间戳>/`（不是删）。"""
    try:
        rel = rel or path.relative_to(base)
    except ValueError:
        rel = Path(path.name)
    hist = base / ".history" / now().strftime("%Y%m%d-%H%M%S") / rel
    hist.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(path, hist)
    except OSError:
        pass


def _mastery_map(db) -> dict[str, dict]:
    """每个目标的掌握状态，给 `_树.json` 用（不进 md：状态一变就升版本太吵）。

    done  = 有卡完成过（小测过了）
    attempts / passed = 累计答题次数 / 通过次数；last = 最近一次答题时间
    quiz  = 题库里出了几道题
    """
    out: dict[str, dict] = {}
    try:
        for r in db.q("SELECT goal_id, COUNT(*) AS n, SUM(passed) AS p, "
                      "MAX(created_at) AS last FROM attempts "
                      "WHERE goal_id IS NOT NULL AND goal_id<>'' GROUP BY goal_id"):
            out[r["goal_id"]] = {"attempts": int(r["n"] or 0), "passed": int(r["p"] or 0),
                                 "last": r["last"] or ""}
    except Exception:  # noqa: BLE001 老库/远端没有 attempts 也不该把树写挂
        pass
    try:
        for r in db.q("SELECT goal_id, COUNT(*) AS n FROM quiz GROUP BY goal_id"):
            out.setdefault(r["goal_id"], {})["quiz"] = int(r["n"] or 0)
    except Exception:  # noqa: BLE001
        pass
    try:
        for gid in db.done_goal_ids():
            out.setdefault(gid, {})["done"] = True
    except Exception:  # noqa: BLE001
        pass
    for v in out.values():
        v.setdefault("attempts", 0)
        v.setdefault("passed", 0)
        v.setdefault("last", "")
        v.setdefault("quiz", 0)
        v.setdefault("done", v["passed"] > 0)
    return out


def _kb_disk_paths(base: Path) -> set[str]:
    """一次扫一遍目录名（**不读文件内容**），给增量重建判断「文件还在不在」。

    比逐个 `dst.is_file()` 快一个量级：2000 个节点 10 个目录，几次 scandir 就够。
    """
    out: set[str] = set()
    if not base.exists():
        return out
    try:
        for d in base.iterdir():
            if not d.is_dir() or d.name.startswith("_"):
                continue
            for f in d.iterdir():
                if f.suffix == ".md":
                    out.add(f"{d.name}/{f.name}")
    except OSError:
        pass
    return out


def write_knowledge(db, course: str | None = None, goals: list[dict] | None = None) -> dict:
    """**增量**重建全库知识库：只重写变了的那几个节点。

    跳过条件（三条全满足）：目标指纹没变、检索块版本没变、md 文件还在。
    表 `kb_nodes` 是索引，md 是导出物——所以「什么都没变」时是 **0 次检索、0 次写文件**。
    """
    if getattr(db, "remote", False):
        return {"written": 0, "changed": 0, "orphaned": 0, "skipped": 0, "moved": 0}
    base = paths.knowledge_dir()
    base.mkdir(parents=True, exist_ok=True)
    rows = goals if goals is not None else db.goals(course=course)
    brev = blocks_rev()
    existing = {r["id"]: r for r in db.kb_nodes()}
    on_disk = _kb_disk_paths(base)
    written = changed = moved = skipped = 0
    order: list[str] = []
    for g in rows:
        gid = g["id"]
        ch = g.get("ch") or "未分章"
        if ch not in order:
            order.append(ch)
        gh = _goal_hash(g)
        rel = f"{_slug(ch, 20)}/{gid}-{_slug(g.get('title') or 'goal')}.md"
        dst = base / rel
        old = existing.get(gid)
        if (old and old["goal_hash"] == gh and old["blocks_rev"] == brev
                and old["path"] == rel and rel in on_disk):
            skipped += 1
            continue
        old_ver = int(old["version"] or 1) if old else 0
        if old and old["path"] != rel:
            # 章名或标题变了：旧文件进历史、按新路径重写，版本接着涨
            old_path = base / old["path"]
            if old_path.is_file():
                _kb_archive(base, old_path, Path(old["path"]))
                try:
                    old_path.unlink()
                except OSError:
                    pass
            moved += 1
        elif old and dst.is_file():
            _kb_archive(base, dst, Path(rel))
        ver = old_ver + 1 if old_ver else 1
        text = _goal_md(db, g, ver)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")
        db.kb_save_node(gid, course=g.get("course") or "ds", chapter=ch,
                        title=g.get("title") or gid, minutes=int(g.get("m") or 30),
                        path=rel, version=ver, goal_hash=gh, blocks_rev=brev,
                        body_hash=hashlib.sha1(text.encode("utf-8")).hexdigest())
        written += 1
        if old_ver:
            changed += 1

    # 孤儿：索引里有、目标已经没了。只在目标非空时清理（防止库空了被当「全删了」）。
    all_rows = db.goals()
    all_ids = {g["id"] for g in all_rows}
    orphaned = 0
    if all_ids:
        dead: list[str] = []
        for k in db.kb_nodes():
            if k["id"] in all_ids:
                continue
            p = base / k["path"]
            if p.is_file():
                _kb_archive(base, p, Path(k["path"]))
                try:
                    p.unlink()
                except OSError:
                    pass
            dead.append(k["id"])
        orphaned = len(dead)
        db.kb_delete_nodes(dead)

    # 章节顺序按「目标在课程里的出现顺序」，不是章名字典序
    if order:
        db.kb_set_meta("chapter_order", json.dumps(order, ensure_ascii=False))
    _write_kb_index(db)
    if written or orphaned:
        db.log(f"知识库（增量）：重写 {written} 个节点（{skipped} 个没变跳过"
               + (f"，{moved} 个换章/改名" if moved else "")
               + (f"，清理 {orphaned} 个孤儿" if orphaned else "") + "）", "ok")
    return {"written": written, "changed": changed, "orphaned": orphaned,
            "skipped": skipped, "moved": moved, "dir": str(base)}


def _write_kb_index(db) -> None:
    """从 `kb_nodes` 生成全库 `_索引.md` + `_树.json`。内容没变（rev 一样）就不写文件。"""
    base = paths.knowledge_dir()
    base.mkdir(parents=True, exist_ok=True)
    nodes_db = db.kb_nodes()
    goals = {g["id"]: g for g in db.goals()}
    mastery = _mastery_map(db)
    names = {c["id"]: (c.get("name") or c["id"]) for c in db.courses()}
    try:
        order = json.loads(db.kb_meta("chapter_order", "[]"))
    except ValueError:
        order = []
    rank = {ch: i for i, ch in enumerate(order)}

    def node_of(n: dict) -> dict:
        g = goals.get(n["id"]) or {}
        st = mastery.get(n["id"], {})
        return {"id": n["id"], "course": n["course"] or "ds",
                "course_name": names.get(n["course"] or "ds", n["course"] or "ds"),
                "chapter": n["chapter"] or "未分章", "title": n["title"] or n["id"],
                "priority": g.get("p") or "core", "minutes": int(n["minutes"] or 30),
                "deps": [d for d in (g.get("d") or []) if d],
                "parent": g.get("parent") or "",
                "path": n["path"], "version": int(n["version"] or 1),
                "done": bool(st.get("done")), "attempts": st.get("attempts", 0),
                "passed": st.get("passed", 0), "quiz": st.get("quiz", 0),
                "last_attempt": st.get("last", "")}

    nodes = [node_of(n) for n in sorted(
        nodes_db, key=lambda x: (rank.get(x["chapter"] or "未分章", 999),
                                 x["course"] or "", x["id"]))]
    id_set = {n["id"] for n in nodes}
    edges = [{"from": d, "to": n["id"], "kind": "dep"}
             for n in nodes for d in n["deps"] if d]
    rev = hashlib.sha1("|".join(
        f'{n["id"]}:{n["version"]}:{int(n["done"])}' for n in nodes).encode()).hexdigest()[:16]
    if db.kb_meta("index_rev") == rev and (base / "_索引.md").is_file():
        return                                     # 没变化，别重写 1MB 的 JSON
    by_ch: dict[str, list[dict]] = {}
    for n in nodes:
        by_ch.setdefault(n["chapter"], []).append(n)
    lines = [f"# 全库知识库 · 索引", "",
             f"> 生成于 {now().strftime('%Y-%m-%d %H:%M')}，共 {len(nodes)} 个节点 / "
             f"{len(by_ch)} 章 / {len({n['course'] for n in nodes})} 门课。", "",
             "> 不分科目：同一个库里按「章」排，科目写在每篇的 `course:` 里。", ""]
    for ch in by_ch:
        lines.append(f"## {ch}")
        lines.append("")
        for n in by_ch[ch]:
            lines.append(f"- [{n['id']} {n['title']}]({n['path']}) · "
                         f"{n['minutes']} 分钟 · `{n['course']}`")
        lines.append("")
    (base / "_索引.md").write_text("\n".join(lines), encoding="utf-8")

    tree = {"generated_at": now().strftime("%Y-%m-%d %H:%M:%S"),
            "root": "知识库",
            "note": "一个知识库：节点按章归组，course 只是属性；edges 是先修边。parent 留空给以后换骨架。",
            "chapter_order": list(by_ch),
            "stats": {"nodes": len(nodes), "edges": len(edges), "chapters": len(by_ch),
                      "courses": len({n["course"] for n in nodes}),
                      "done": sum(1 for n in nodes if n["done"])},
            "nodes": nodes, "edges": edges,
            "missing_deps": sorted({e["from"] for e in edges} - id_set)}
    (base / "_树.json").write_text(json.dumps(tree, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    db.kb_set_meta("index_rev", rev)


def _front_matter(text: str) -> dict:
    """读 Markdown 头部的 --- 键值（只认 key: value）。"""
    out: dict[str, str] = {}
    if not text.startswith("---"):
        return out
    for line in text.splitlines()[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def knowledge_list(db, course: str | None = None,
                   chapter: str | None = None) -> list[dict]:
    """知识库有哪些节点——**查 `kb_nodes` 索引**，不扫文件（2000 节点毫秒级）。

    course / chapter 给定就只返回那一块（知识库页按章懒加载用）。
    路径返回绝对路径，方便直接读 md。
    """
    if getattr(db, "remote", False):
        return []
    base = paths.knowledge_dir()
    rank = _chapter_rank(db)
    rows = db.kb_nodes(chapter=chapter, course=course)
    out = [{"id": r["id"], "title": r["title"], "chapter": r["chapter"] or "未分章",
            "minutes": int(r["minutes"] or 0), "version": r["version"],
            "course": r["course"] or "ds", "course_name": r["course"] or "ds",
            "path": str(base / r["path"])} for r in rows]
    out.sort(key=lambda r: (rank.get(r["chapter"], 999), r["chapter"], r["course"], r["id"]))
    return out


def kb_chapters(db) -> list[dict]:
    """先列章（每章几个节点），知识库页展开时才拉节点。"""
    if getattr(db, "remote", False):
        return []
    rank = _chapter_rank(db)
    rows = db.kb_chapters()
    rows.sort(key=lambda r: (rank.get(r["chapter"], 999), r["chapter"]))
    return rows


def kb_count(db, course: str | None = None) -> int:
    """知识库节点数（资料页统计用，一条 SQL）。"""
    if getattr(db, "remote", False):
        return 0
    return db.kb_count(course)


def _chapter_rank(db) -> dict[str, int]:
    """章节显示顺序：`kb_meta.chapter_order`（目标在课程里的出现顺序）。

    中文按字典序是「图 / 堆 / 复杂度」，跟学习顺序完全不一样。
    """
    try:
        order = json.loads(db.kb_meta("chapter_order", "[]"))
    except (TypeError, ValueError):
        return {}
    return {ch: i for i, ch in enumerate(order) if isinstance(ch, str)}


def knowledge_read(path: str | Path) -> str:
    """读一份知识库 md（去掉 front matter，正文给人看）。"""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].lstrip("\n")
    return text


def clear_trash() -> dict:
    """清空 回收站/（界面里点、确认过才调）。返回清掉几个文件、多少字节。"""
    base = paths.trash_dir()
    files = 0
    size = 0
    if not base.exists():
        return {"files": 0, "bytes": 0}
    for p in base.rglob("*"):
        if p.is_file():
            files += 1
            try:
                size += p.stat().st_size
            except OSError:
                pass
    shutil.rmtree(base, ignore_errors=True)
    return {"files": files, "bytes": size}


def missing_material(db) -> str:
    """粗略的缺料清单：没有题库、或题库是降级题的目标。"""
    if getattr(db, "remote", False):
        return db.call("GET", "/api/missing_material")["text"]
    rows = db.q("SELECT g.id, g.title FROM goals g "
                "LEFT JOIN (SELECT goal_id, MAX(batch) b FROM quiz GROUP BY goal_id) q "
                "ON q.goal_id=g.id WHERE q.b IS NULL")
    if not rows:
        return ""
    return "缺题库：" + "、".join(r["id"] for r in rows[:20]) + ("…" if len(rows) > 20 else "")
