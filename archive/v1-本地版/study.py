#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学习bot v1 · 本地学习流程引擎

一个不依赖网络、只读写本目录 data/study.json 的小引擎。
复习排程用简化版 SM-2；所有命令都可以在对话里通过 study xxx 调用。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys
import textwrap
import unicodedata

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
DATA = os.path.join(DATA_DIR, "study.json")
W = 64

GRADES = {"again": 0, "hard": 1, "good": 2, "easy": 3}
GRADE_LABEL = {0: "没想起来", 1: "勉强", 2: "记得", 3: "很简单"}
BAR = "▁▂▃▄▅▆▇█"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---------------------------------------------------------------- 基础工具
def today() -> dt.date:
    return dt.date.today()


def parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def days_since(d: str) -> int:
    return (today() - parse_date(d)).days


def load() -> dict:
    if not os.path.exists(DATA):
        return {"version": 1, "topics": [], "sessions": [], "notes": []}
    with open(DATA, "r", encoding="utf-8") as f:
        db = json.load(f)
    for k, v in (("topics", []), ("sessions", []), ("notes", [])):
        db.setdefault(k, v)
    return db


def save(db: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DATA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA)


def next_id(items: list, prefix: str) -> str:
    n = 0
    for it in items:
        i = str(it.get("id", ""))
        if i.startswith(prefix) and i[len(prefix):].isdigit():
            n = max(n, int(i[len(prefix):]))
    return f"{prefix}{n + 1:03d}"


def hline(ch="─") -> str:
    return ch * W


def wrap(text: str, width: int = W - 2) -> list[str]:
    out = []
    for para in str(text).splitlines():
        out.extend(textwrap.wrap(para, width) or [""])
    return out


def dwidth(s: str) -> int:
    """终端显示宽度：中文按 2 列算，保证表格对齐。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in str(s))


def padw(s: str, width: int) -> str:
    return str(s) + " " * max(0, width - dwidth(s))


def short(s: str, n: int) -> str:
    s = str(s)
    return s if len(s) <= n else s[: max(1, n - 1)] + "…"


def find_topic(db: dict, key: str) -> dict | None:
    key = key.strip()
    for t in db["topics"]:
        if t["id"].lower() == key.lower():
            return t
    cands = [t for t in db["topics"] if key.lower() in t["title"].lower()]
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1:
        print(f"「{key}」匹配到多个条目，请用 ID 指定：")
        for t in cands:
            print(f"  {t['id']}  {t['title']}")
        sys.exit(2)
    return None


def ensure_due(t: dict) -> dict:
    t.setdefault("review", {"due": today().isoformat(), "interval": 0,
                            "reps": 0, "ease": 2.5, "lapses": 0, "last": None})
    return t["review"]


def bucket(t: dict) -> str:
    """把条目按到期时间分桶。"""
    due = ensure_due(t)
    d = parse_date(due["due"])
    delta = (d - today()).days
    if t.get("status") == "archived":
        return "archived"
    if delta < 0:
        return "overdue"
    if delta == 0:
        return "today"
    if delta <= 7:
        return "week"
    return "later"


# ---------------------------------------------------------------- 排程
def schedule(t: dict, grade: int) -> dict:
    rv = ensure_due(t)
    reps, ease, interval = rv["reps"], rv["ease"], rv["interval"]
    if grade == 0:                      # 忘了：重来，缩短间隔
        interval = 1
        ease = max(1.3, ease - 0.2)
        rv["lapses"] = rv.get("lapses", 0) + 1
    elif grade == 1:                    # 勉强：小步前进
        interval = 1 if reps == 0 else max(2, round(interval * 1.2))
    elif grade == 2:                    # 记得：正常增长
        interval = 1 if reps == 0 else (3 if reps == 1 else max(4, round(interval * ease)))
    else:                               # 很简单：跳级
        interval = 3 if reps == 0 else max(5, round(interval * ease * 1.3))
    interval = min(interval, 180)
    rv["interval"] = interval
    rv["reps"] = reps + 1
    rv["ease"] = round(ease, 2)
    rv["last"] = today().isoformat()
    rv["due"] = (today() + dt.timedelta(days=interval)).isoformat()
    return rv


# ---------------------------------------------------------------- 渲染
def render_topic(t: dict, show_due: bool = True) -> str:
    rv = ensure_due(t)
    tag = f"  [{', '.join(t.get('tags') or [])}]" if t.get("tags") else ""
    line = f"  {t['id']}  {short(t['title'], 40)}{tag}"
    if show_due:
        if rv["reps"] == 0:
            line += f"   → 待首次学习（{rv['due']}）"
        else:
            line += f"   → {rv['due']}（间隔{rv['interval']}天 / 第{rv['reps']}次）"
    return line


def cmd_today(db: dict, args) -> None:
    groups = {"overdue": [], "today": [], "week": []}
    for t in db["topics"]:
        b = bucket(t)
        if b in groups:
            groups[b].append(t)

    print(hline("═"))
    print(f" 学习bot · {today().isoformat()}  周{'一二三四五六日'[today().weekday()]}")
    print(hline("═"))

    streak = calc_streak(db)
    mins7 = sum(s["minutes"] for s in db["sessions"] if days_since(s["date"][:10]) < 7)
    print(f" 连续学习 {streak} 天   近7天 {mins7} 分钟   待复习 "
          f"{len(groups['overdue']) + len(groups['today'])} 条")
    print()

    if groups["overdue"]:
        print(f"● 已逾期（{len(groups['overdue'])}）— 先清这些，别再加新的")
        for t in sorted(groups["overdue"], key=lambda x: ensure_due(x)["due"]):
            print(render_topic(t))
        print()
    if groups["today"]:
        print(f"● 今天到期（{len(groups['today'])}）")
        for t in groups["today"]:
            print(render_topic(t))
        print()
    if not groups["overdue"] and not groups["today"]:
        print("● 今天没有到期的复习 ✅")
        print()

    # 建议：最近没碰过的、非归档条目
    touched = {s.get("topic") for s in db["sessions"]}
    idle = [t for t in db["topics"] if bucket(t) == "later" and t["id"] not in touched]
    if idle:
        picks = random.sample(idle, min(2, len(idle)))
        print("● 可以顺带推进（很久没碰）")
        for t in picks:
            print(render_topic(t))
        print()

    if not db["topics"]:
        print("还没有任何学习条目。直接说「我学了 xxx」，或运行：")
        print('  study learn "牛顿第二定律" --subject 物理 --minutes 25')
        print()

    planned = [s for s in db["sessions"] if s["date"][:10] == today().isoformat()]
    if planned:
        print(f"● 今天已记录 {len(planned)} 次，共 {sum(s['minutes'] for s in planned)} 分钟")
    elif db["topics"]:
        print("● 今天还没开始。最小的第一步：选上面的任意一条，学 25 分钟。")
    print(hline())


def cmd_add(db: dict, args) -> None:
    t = {
        "id": next_id(db["topics"], "t"),
        "subject": args.subject,
        "title": args.title,
        "source": args.source or "",
        "tags": [x.strip() for x in (args.tags or "").split(",") if x.strip()],
        "created": today().isoformat(),
        "status": "learning",
        "note": args.note or "",
        "review": {"due": args.due or today().isoformat(), "interval": 0,
                   "reps": 0, "ease": 2.5, "lapses": 0, "last": None},
    }
    db["topics"].append(t)
    save(db)
    print(f"已加入 {t['id']}  [{t['subject']}] {t['title']}")
    print(f" 首次复习：{t['review']['due']}   （学完后运行 review {t['id']} -g good）")


def cmd_list(db: dict, args) -> None:
    ts = [t for t in db["topics"]
          if (not args.subject or t["subject"] == args.subject)
          and (not args.status or t.get("status") == args.status)]
    if not ts:
        print("没有符合条件的条目。")
        return
    by_subj: dict[str, list] = {}
    for t in ts:
        by_subj.setdefault(t["subject"], []).append(t)
    for subj, items in by_subj.items():
        print(f"[{subj}]  {len(items)} 条")
        for t in sorted(items, key=lambda x: ensure_due(x)["due"]):
            mark = "✓" if t.get("status") == "mastered" else " "
            print(f" {mark} {t['id']}  {padw(short(t['title'], 34), 40)} 下次 {ensure_due(t)['due']}")
        print()


def cmd_search(db: dict, args) -> None:
    kw = args.keyword.lower()
    hits_t = [t for t in db["topics"]
              if kw in t["title"].lower() or kw in (t.get("note") or "").lower()
              or kw in " ".join(t.get("tags") or []).lower()
              or kw in (t.get("source") or "").lower()]
    hits_n = [n for n in db["notes"] if kw in n["text"].lower()]
    print(f"搜索「{args.keyword}」：{len(hits_t)} 条学习条目，{len(hits_n)} 条随手记录")
    for t in hits_t:
        print(f"  {t['id']}  [{t['subject']}] {short(t['title'], 44)}")
    for n in hits_n:
        print(f"  {n['id']}  {n['date']}  {short(n['text'], 44)}")


def cmd_review(db: dict, args) -> None:
    t = find_topic(db, args.id)
    if not t:
        print(f"找不到条目：{args.id}")
        sys.exit(2)
    grade = GRADES[args.grade]
    rv = schedule(t, grade)
    if args.minutes:
        add_session(db, t, args.minutes, grade=grade + 1, note=args.note or "")
    if rv["interval"] >= 60:
        t["status"] = "mastered"
    save(db)
    print(f"{t['id']} {t['title']}")
    print(f"  评价：{GRADE_LABEL[grade]}  →  下次复习 {rv['due']}（{rv['interval']} 天后，"
          f"第 {rv['reps']} 次）")
    if grade == 0:
        print("  别灰心，间隔缩短是设计的一部分：明天再见一次就记住了。")
    elif rv["interval"] >= 60:
        print("  已进入长期记忆区（≥60 天），保持偶尔回顾即可。")


def add_session(db: dict, t: dict, minutes: int, grade: int | None = None, note: str = "") -> dict:
    s = {
        "id": next_id(db["sessions"], "s"),
        "date": dt.datetime.now().isoformat(timespec="minutes"),
        "topic": t["id"],
        "subject": t["subject"],
        "title": t["title"],
        "minutes": int(minutes),
        "quality": grade,
        "note": note,
    }
    db["sessions"].append(s)
    return s


def cmd_learn(db: dict, args) -> None:
    """一次搞定：新条目 + 本次学习记录 + 明天复习。"""
    t = {
        "id": next_id(db["topics"], "t"),
        "subject": args.subject,
        "title": args.title,
        "source": args.source or "",
        "tags": [x.strip() for x in (args.tags or "").split(",") if x.strip()],
        "created": today().isoformat(),
        "status": "learning",
        "note": args.note or "",
        "review": {"due": (today() + dt.timedelta(days=1)).isoformat(), "interval": 1,
                   "reps": 1, "ease": 2.5, "lapses": 0, "last": today().isoformat()},
    }
    db["topics"].append(t)
    add_session(db, t, args.minutes, grade=args.quality, note=args.note or "")
    save(db)
    print(f"✓ 已记录 {t['id']}  [{t['subject']}] {t['title']}  ·  {args.minutes} 分钟")
    print(f"  下次复习：{t['review']['due']}（明天）")
    st = db["sessions"]
    m = sum(x["minutes"] for x in st if x["date"][:10] == today().isoformat())
    print(f"  今日累计：{m} 分钟 · 连续 {calc_streak(db)} 天")


def cmd_log(db: dict, args) -> None:
    t = find_topic(db, args.id)
    if not t:
        print(f"找不到条目：{args.id}")
        sys.exit(2)
    add_session(db, t, args.minutes, grade=args.quality, note=args.note or "")
    save(db)
    m = sum(x["minutes"] for x in db["sessions"] if x["date"][:10] == today().isoformat())
    print(f"✓ {t['id']} {t['title']} +{args.minutes} 分钟   今日累计 {m} 分钟")


def cmd_note(db: dict, args) -> None:
    n = {"id": next_id(db["notes"], "n"), "date": today().isoformat(),
         "subject": args.subject or "", "text": args.text}
    db["notes"].append(n)
    save(db)
    print(f"✓ 已收进随手记 {n['id']}（{len(db['notes'])} 条）")


def calc_streak(db: dict) -> int:
    days = {s["date"][:10] for s in db["sessions"]}
    if not days:
        return 0
    d = today()
    if d.isoformat() not in days:
        d -= dt.timedelta(days=1)      # 今天还没学，不打断连续
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= dt.timedelta(days=1)
    return n


def cmd_stats(db: dict, args) -> None:
    n = args.days
    start = today() - dt.timedelta(days=n - 1)
    sess = [s for s in db["sessions"] if days_since(s["date"][:10]) < n]
    total = sum(s["minutes"] for s in sess)
    by_day = {start + dt.timedelta(days=i): 0 for i in range(n)}
    for s in sess:
        d = parse_date(s["date"][:10])
        if d in by_day:
            by_day[d] += s["minutes"]
    peak = max(by_day.values()) or 1

    print(hline("═"))
    print(f" 学习bot · 近 {n} 天")
    print(hline("═"))
    print(f" 总时长 {total} 分钟（{total / 60:.1f} 小时）  日均 {total / n:.0f} 分钟"
          f"  连续 {calc_streak(db)} 天")
    print()
    for d, m in by_day.items():
        bar = "█" * max(0, round(m / peak * 24))
        label = f"{'一二三四五六日'[d.weekday()]}"
        print(f"  {d.strftime('%m-%d')} {label}  {bar:<24}{m if m else ''}")
    print()

    subj: dict[str, int] = {}
    for s in sess:
        subj[s["subject"]] = subj.get(s["subject"], 0) + s["minutes"]
    if subj:
        print(" 科目分布")
        for k, v in sorted(subj.items(), key=lambda x: -x[1]):
            print(f"  {k:<8} {v:>5} 分钟  {'█' * max(1, round(v / max(subj.values()) * 20))}")
        print()

    buckets = {"overdue": 0, "today": 0, "week": 0, "later": 0}
    for t in db["topics"]:
        b = bucket(t)
        if b in buckets:
            buckets[b] += 1
    revs = [s for s in sess if s.get("quality")]
    good = sum(1 for s in revs if s["quality"] >= 3)
    rate = f"{good / len(revs) * 100:.0f}%" if revs else "—"
    print(f" 条目 {len([t for t in db['topics'] if t.get('status') != 'archived'])} 条"
          f"  逾期 {buckets['overdue']} / 今日 {buckets['today']} / 本周 {buckets['week']}")
    print(f" 复习通过率 {rate}（评价≥记得），随手记 {len(db['notes'])} 条")
    weak = [t for t in db["topics"] if ensure_due(t).get("lapses")]
    if weak:
        print()
        print(" 反复忘的条目（lapses>0）")
        for t in sorted(weak, key=lambda x: -ensure_due(x)["lapses"])[:5]:
            print(f"  {t['id']} {short(t['title'], 34)}  遗忘 {ensure_due(t)['lapses']} 次")
    print(hline())


# ---------------------------------------------------------------- 看板
def build_dashboard(db: dict) -> str:
    n = 35                                      # 5 个整周，从周一开始对齐
    monday = today() - dt.timedelta(days=today().weekday())
    start = monday - dt.timedelta(days=n - 7)
    by_day = {start + dt.timedelta(days=i): 0 for i in range(n)}
    for s in db["sessions"]:
        d = parse_date(s["date"][:10])
        if d in by_day:
            by_day[d] += s["minutes"]
    total28 = sum(by_day.values())

    subj: dict[str, int] = {}
    for s in db["sessions"]:
        if days_since(s["date"][:10]) < n:
            subj[s["subject"]] = subj.get(s["subject"], 0) + s["minutes"]

    due_list = []
    for t in db["topics"]:
        b = bucket(t)
        if b in ("overdue", "today"):
            rv = ensure_due(t)
            due_list.append({"id": t["id"], "title": t["title"], "subject": t["subject"],
                             "due": rv["due"], "overdue": b == "overdue",
                             "days": (parse_date(rv["due"]) - today()).days,
                             "reps": rv["reps"], "lapses": rv.get("lapses", 0)})
    due_list.sort(key=lambda x: x["days"])

    heat = [{"d": d.isoformat(), "wd": d.weekday(), "m": m} for d, m in by_day.items()]
    active_days = [h for h in heat if h["m"] > 0]
    if active_days:                     # 把开头整周没记录的格子裁掉，网格更干净
        first = parse_date(active_days[0]["d"])
        first -= dt.timedelta(days=first.weekday())
        heat = [h for h in heat if parse_date(h["d"]) >= first]
    recent = sorted(db["sessions"], key=lambda s: s["date"], reverse=True)[:8]
    notes = sorted(db["notes"], key=lambda x: x["date"], reverse=True)[:6]

    payload = {
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "streak": calc_streak(db),
        "total28": total28,
        "avg": round(total28 / n),
        "todayMin": sum(s["minutes"] for s in db["sessions"]
                        if s["date"][:10] == today().isoformat()),
        "due": due_list,
        "heat": heat,
        "subjects": sorted([{"name": k, "min": v} for k, v in subj.items()],
                           key=lambda x: -x["min"]),
        "recent": recent,
        "notes": notes,
        "topics": len([t for t in db["topics"] if t.get("status") != "archived"]),
    }
    js = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return DASH_TEMPLATE.replace("__DATA__", js)


def cmd_dashboard(db: dict, args) -> None:
    out = os.path.join(ROOT, "dashboard.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(build_dashboard(db))
    print(f"✓ 已更新看板：{out}")


DASH_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>学习bot · 看板</title>
<style>
  :root{--bg:#0f1216;--card:#171c23;--line:#232b36;--fg:#e6edf5;--dim:#8b98a9;
        --acc:#5aa9ff;--ok:#3ddc97;--warn:#ffb454;--bad:#ff6b6b}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:15px/1.6 "Segoe UI","Microsoft YaHei",system-ui,sans-serif;padding:28px}
  .wrap{max-width:1080px;margin:0 auto}
  header{display:flex;align-items:baseline;gap:14px;margin-bottom:6px}
  h1{font-size:22px;margin:0;letter-spacing:.5px}
  .sub{color:var(--dim);font-size:13px}
  .grid{display:grid;gap:16px;margin-top:18px}
  .g4{grid-template-columns:repeat(4,1fr)}
  .g2{grid-template-columns:1.15fr .85fr}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
  .k{color:var(--dim);font-size:12px;letter-spacing:.4px}
  .v{font-size:26px;font-weight:650;margin-top:4px}
  .v small{font-size:12px;color:var(--dim);font-weight:400;margin-left:4px}
  .heat{display:grid;grid-template-columns:repeat(7,1fr);gap:5px;margin-top:12px}
  .cell{height:18px;border-radius:4px;background:#1d242e;position:relative}
  .wd{font-size:10px;color:var(--dim);text-align:center;line-height:1}
  .cell:hover::after{content:attr(data-tip);position:absolute;bottom:130%;left:50%;
    transform:translateX(-50%);background:#000d;color:#fff;font-size:11px;padding:4px 7px;
    border-radius:6px;white-space:nowrap;z-index:9}
  .due li{list-style:none;display:flex;gap:10px;align-items:center;padding:8px 0;
          border-bottom:1px dashed var(--line)}
  .due{padding:0;margin:8px 0 0}
  .due li:last-child{border:0}
  .pill{font-size:11px;padding:1px 8px;border-radius:99px;white-space:nowrap}
  .od{background:#3a1b1b;color:var(--bad)} .td{background:#12303f;color:var(--acc)}
  .t{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .m{color:var(--dim);font-size:12px}
  .bar{height:8px;border-radius:4px;background:#1d242e;overflow:hidden;margin-top:6px}
  .bar i{display:block;height:100%;background:linear-gradient(90deg,#2f7fd4,#5aa9ff)}
  .row{display:flex;justify-content:space-between;gap:10px;padding:5px 0;font-size:13px}
  .empty{color:var(--dim);font-size:13px;padding:8px 0}
  footer{margin-top:22px;color:var(--dim);font-size:12px}
  code{background:#0b0f14;border:1px solid var(--line);border-radius:5px;padding:1px 6px;font-size:12px}
  @media(max-width:800px){.g4{grid-template-columns:repeat(2,1fr)}.g2{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>学习bot</h1>
    <span class="sub" id="gen"></span>
  </header>
  <div class="grid g4" id="kpis"></div>

  <div class="grid g2">
    <div class="card">
      <div class="k">今天该复习</div>
      <ul class="due" id="due"></ul>
    </div>
    <div class="card">
      <div class="k">学习节奏（按周）</div>
      <div class="heat" id="heat"></div>
      <div class="sub" style="margin-top:10px" id="heatNote"></div>
    </div>
  </div>

  <div class="grid g2">
    <div class="card">
      <div class="k">科目分布（近 5 周）</div>
      <div id="subjects" style="margin-top:10px"></div>
    </div>
    <div class="card">
      <div class="k">最近记录</div>
      <div id="recent" style="margin-top:6px"></div>
    </div>
  </div>

  <div class="card" style="margin-top:16px">
    <div class="k">随手记（最近）</div>
    <div id="notes" style="margin-top:6px"></div>
  </div>

  <footer>数据来自 <code>data/study.json</code> · 由 <code>study dashboard</code> 重新生成 ·
  这个页面完全离线，可以双击直接用浏览器打开</footer>
</div>
<script>
const D = __DATA__;
const el = (id) => document.getElementById(id);
document.getElementById('gen').textContent = '更新于 ' + D.generated;

const kpis = [
  ['连续学习', D.streak, '天'],
  ['今天已学', D.todayMin, '分钟'],
  ['近5周', D.total28, '分钟'],
  ['条目', D.topics, '条'],
];
el('kpis').innerHTML = kpis.map(([k,v,u]) =>
  `<div class="card"><div class="k">${k}</div><div class="v">${v}<small>${u}</small></div></div>`).join('');

if (D.due.length) {
  el('due').innerHTML = D.due.map(t => `
    <li><span class="pill ${t.overdue?'od':'td'}">${t.overdue? '逾期'+Math.abs(t.days)+'天':'今天'}</span>
      <span class="t" title="${t.title}">${t.title}</span>
      <span class="m">${t.subject} · ${t.id}</span></li>`).join('');
} else {
  el('due').innerHTML = '<div class="empty">今天没有到期的复习 ✅ 挑一个新条目推进吧</div>';
}

const maxM = Math.max(1, ...D.heat.map(h => h.m));
const pad = D.heat[0].wd;                       // 让第一格落在正确的星期列
const wdHead = ['一','二','三','四','五','六','日']
  .map(w => `<div class="wd">${w}</div>`).join('');
const padCells = Array(pad).fill('<div></div>').join('');
el('heat').innerHTML = wdHead + padCells + D.heat.map(h => {
  const a = h.m / maxM;
  const bg = h.m === 0 ? '#1d242e' : `rgba(90,169,255,${0.18 + a * 0.82})`;
  return `<div class="cell" style="background:${bg}" data-tip="${h.d} · ${h.m} 分钟"></div>`;
}).join('');
const active = D.heat.filter(h => h.m > 0).length;
el('heatNote').textContent = `${active}/${D.heat.length} 天有记录 · 日均 ${D.avg} 分钟`;

if (D.subjects.length) {
  const mx = Math.max(...D.subjects.map(s => s.min));
  el('subjects').innerHTML = D.subjects.map(s => `
    <div class="row"><span>${s.name}</span><span class="m">${s.min} 分钟</span></div>
    <div class="bar"><i style="width:${s.min / mx * 100}%"></i></div>`).join('');
} else { el('subjects').innerHTML = '<div class="empty">还没有记录</div>'; }

el('recent').innerHTML = D.recent.length ? D.recent.map(s => `
  <div class="row"><span>${s.title}</span>
    <span class="m">${s.date.slice(5,10).replace('-','/')} · ${s.minutes}′</span></div>`).join('')
  : '<div class="empty">还没有学习记录</div>';

el('notes').innerHTML = D.notes.length ? D.notes.map(n => `
  <div class="row"><span>${n.text}</span><span class="m">${n.date.slice(5)}</span></div>`).join('')
  : '<div class="empty">还没有随手记</div>';
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- 示例数据
def cmd_seed(db: dict, args) -> None:
    if db["topics"] and not args.force:
        print("已有数据，不想覆盖就用 --force；或者先备份 data/study.json。")
        sys.exit(2)
    db["topics"], db["sessions"], db["notes"] = [], [], []
    seed = [
        ("英语", "雅思核心词 list 3（60 词）", ["词汇"]),
        ("英语", "雅思听力 Section 2 地图题技巧", ["听力"]),
        ("数学", "拉格朗日中值定理", ["高数", "定理"]),
        ("数学", "矩阵特征值求法", ["线代"]),
        ("编程", "SQL 窗口函数 row_number / rank", ["SQL"]),
        ("编程", "Python 生成器与 yield 执行过程", ["Python"]),
        ("物理", "牛顿第二定律的适用条件", ["力学"]),
    ]
    for i, (subj, title, tags) in enumerate(seed):
        t = {
            "id": next_id(db["topics"], "t"), "subject": subj, "title": title,
            "source": "", "tags": tags,
            "created": (today() - dt.timedelta(days=9 - i)).isoformat(),
            "status": "learning", "note": "",
            "review": {"due": today().isoformat(), "interval": 0, "reps": 0,
                       "ease": 2.5, "lapses": 0, "last": None},
        }
        db["topics"].append(t)
    # 造 3 周的学习记录
    random.seed(7)
    for back in range(20, 0, -1):
        d = today() - dt.timedelta(days=back)
        if random.random() < 0.7:
            for _ in range(random.randint(1, 2)):
                t = random.choice(db["topics"])
                db["sessions"].append({
                    "id": next_id(db["sessions"], "s"),
                    "date": f"{d.isoformat()}T{random.randint(8,22):02d}:{random.randint(0,59):02d}",
                    "topic": t["id"], "subject": t["subject"], "title": t["title"],
                    "minutes": random.choice([25, 25, 30, 45, 50]),
                    "quality": random.choice([2,3,3,4]), "note": "",
                })
    for i, (subj, txt) in enumerate([
        ("英语", "听力地图题：先看方位词 north/south，再抓 relative to 的参照物"),
        ("数学", "中值定理三兄弟的区别老是混，要画图对比"),
        ("编程", "yield 的本质：函数冻结在 yield 处，next 才继续"),
    ]):
        db["notes"].append({"id": next_id(db["notes"], "n"),
                            "date": (today() - dt.timedelta(days=3 - i)).isoformat(),
                            "subject": subj, "text": txt})
    # 让部分条目有复习历史
    for t, g in zip(db["topics"], [2, 0, 2, 1, 2, 0, 2]):
        rv = t["review"]
        rv.update({"reps": 2, "interval": 3, "ease": 2.4, "last": (today() - dt.timedelta(days=3)).isoformat()})
        if g == 0:
            rv["lapses"] = 1
            rv["due"] = (today() - dt.timedelta(days=1)).isoformat()
        elif g == 1:
            rv["due"] = today().isoformat()
        else:
            rv["due"] = (today() + dt.timedelta(days=2)).isoformat()
    save(db)
    print(f"✓ 已写入示例数据：{len(db['topics'])} 个条目 / {len(db['sessions'])} 条记录 / {len(db['notes'])} 条随手记")
    print("  满意就保留；想从零开始：study reset --yes")


def cmd_reset(db: dict, args) -> None:
    if not args.yes:
        print("这会删掉全部条目、记录和随手记。确认后运行：study reset --yes")
        sys.exit(2)
    save({"version": 1, "topics": [], "sessions": [], "notes": []})
    print("✓ 已清空。今天开始记第一条吧。")


# ---------------------------------------------------------------- 入口
def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="study.py", description="学习bot v1 本地学习引擎")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("today", help="今天该做什么（打开对话先看这个）")
    s.set_defaults(fn=cmd_today)

    s = sub.add_parser("add", help="加入一个学习条目")
    s.add_argument("title")
    s.add_argument("--subject", "-s", default="未分类")
    s.add_argument("--source", default="")
    s.add_argument("--tags", default="")
    s.add_argument("--note", default="")
    s.add_argument("--due", default=None)
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("learn", help="我学完了：记一条学习 + 自动安排明天复习")
    s.add_argument("title")
    s.add_argument("--subject", "-s", default="未分类")
    s.add_argument("--minutes", "-m", type=int, default=25)
    s.add_argument("--quality", "-q", type=int, choices=[1, 2, 3, 4], default=3)
    s.add_argument("--source", default="")
    s.add_argument("--tags", default="")
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_learn)

    s = sub.add_parser("list", help="列出条目")
    s.add_argument("--subject", "-s", default=None)
    s.add_argument("--status", default=None)
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("search", help="在条目和随手记里查找")
    s.add_argument("keyword")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("review", help="记录一次复习结果，自动安排下次")
    s.add_argument("id")
    s.add_argument("--grade", "-g", choices=list(GRADES), required=True)
    s.add_argument("--minutes", "-m", type=int, default=None)
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("log", help="补记学习时长（不改排程）")
    s.add_argument("id")
    s.add_argument("--minutes", "-m", type=int, required=True)
    s.add_argument("--quality", "-q", type=int, choices=[1, 2, 3, 4, 5], default=None)
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("note", help="随手记一句话")
    s.add_argument("text")
    s.add_argument("--subject", "-s", default=None)
    s.set_defaults(fn=cmd_note)

    s = sub.add_parser("stats", help="时间段统计")
    s.add_argument("--days", "-d", type=int, default=7)
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("dashboard", help="生成离线看板 dashboard.html")
    s.set_defaults(fn=cmd_dashboard)

    s = sub.add_parser("seed", help="写入示例数据（--force 强制覆盖）")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_seed)

    s = sub.add_parser("reset", help="清空全部数据（需要 --yes）")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_reset)

    args = p.parse_args(argv)
    args.fn(load(), args)


if __name__ == "__main__":
    main()
