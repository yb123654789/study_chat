#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性排程器：把 goals.json 排成到考核日的每日计划。

设计原则：模型只负责「拆目标/估时/定优先级」（结果落在 goals.json），
日期、容量、缓冲、复习轮次全部由这里的算法算，保证可复现、可解释。

**每天学多少不写死。** 先算「还没学完的分钟 ÷ 剩余可学天数」，周末只按系数加权
（默认 ×1.3），每天再留 15% 余量。落后多少，后面每天就自动长多少 —— 这样重排才有意义。
唯一的人为硬上限是 `day_cap`（每天最多多少分钟，0 = 不限）。

用法：
    python tools/planner.py --deadline 2027-01-08
    python tools/planner.py --deadline 2027-01-08 --weekend-ratio 1.5
    python tools/planner.py --deadline 2027-01-08 --day-cap 180
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WD = "一二三四五六日"

PRIORITY = {"core": 0, "support": 1, "optional": 2}
PRIORITY_CN = {"core": "核心", "support": "辅助", "optional": "可选"}

BUFFER_RATE = 0.15            # 每天在算出来的量上多留 15%，给「这天多学一点」用
WEEKEND_WEIGHT = 1.3          # 周末只加权：×1.3 = 比工作日多学 30%，不是固定分钟数
MIN_DAY_CAP = 45              # 兜底：再小的需求也留得下复习卡
REVIEW_FILL_MIN = 45          # 复习卡只是「填空」，一天最多占 45 分钟（3 张）


# ------------------------------------------------------------------ 数据
def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    ids = {g["id"] for g in data["goals"]}
    for g in data["goals"]:
        for d in g.get("d", []):
            if d not in ids:
                raise SystemExit(f"{g['id']} 依赖了不存在的目标 {d}")
    # 保持文件里的课程顺序（章节顺序），优先级只用于取舍和复习挑选
    return data


def topo(goals: list[dict]) -> list[dict]:
    """按依赖排序；同一批可做的目标里，按课程顺序（文件里的顺序）走。"""
    order = {g["id"]: i for i, g in enumerate(goals)}
    done: set[str] = set()
    out: list[dict] = []
    remaining = list(goals)
    while remaining:
        ready = [g for g in remaining if all(d in done for d in g.get("d", []))]
        if not ready:                      # 有环：按原顺序兜底
            out.extend(remaining)
            break
        ready.sort(key=lambda g: order[g["id"]])
        pick = ready[0]
        out.append(pick)
        done.add(pick["id"])
        remaining.remove(pick)
    return out


def day_weight(day: dt.date, weekday_weight: float = 1.0,
               weekend_weight: float = WEEKEND_WEIGHT) -> float:
    """周末的相对权重（不是分钟数）。周六日算周末。"""
    return float(weekend_weight if day.weekday() >= 5 else weekday_weight)


def task_minutes(g: dict) -> int:
    """一个目标 = 学习 + 当场小测（10 分钟）算一张卡。"""
    return int(g["m"]) + 10


# ------------------------------------------------------------------ 排程
def schedule(goals: list[dict], start: dt.date, deadline: dt.date,
             weekday_weight: float = 1.0, weekend_weight: float = WEEKEND_WEIGHT,
             buffer_rate: float = BUFFER_RATE, lost_days: int = 0,
             day_cap: int = 0) -> dict:
    """排到 deadline。

    weekday_weight / weekend_weight：周末相对工作日多学多少（权重，不是分钟）。
    day_cap：每天最多多少分钟，0 = 不限（唯一写死的「上限」由人决定，不由代码编）。
    """
    goals = topo(goals)
    ordinary = [g for g in goals if g["ch"] != "应试"]
    exam = [g for g in goals if g["ch"] == "应试"]

    start = start + dt.timedelta(days=lost_days)     # 模拟「已经浪费掉几天」
    total_days = (deadline - start).days + 1
    if total_days < 3:
        raise SystemExit("离考核日太近了，至少留 3 天。")

    # 前 80% 学新内容，后 20% 留给模考与总复习
    split = max(1, min(total_days - 1, round(total_days * 0.8)))
    phase_a = [start + dt.timedelta(days=i) for i in range(split)]
    phase_b = [start + dt.timedelta(days=i) for i in range(split, total_days)]

    def w(day: dt.date) -> float:
        return day_weight(day, weekday_weight, weekend_weight)

    card_max = max((task_minutes(g) for g in ordinary), default=0)

    def cap_of(day: dt.date, target: float) -> int:
        """当天的分钟上限。

        卡是按目标整体切的、不能拆，所以上限要在「当天该学多少」之外再放一张最大的卡，
        否则一张大卡就整天装不下（旧版写死 90/150 时，大卡直接导致目标排不进去）。
        day_cap 是用户设的硬上限，设了就严格守。
        """
        cap = int(target / (1 - buffer_rate)) + card_max
        if day_cap:
            cap = min(cap, int(day_cap))
            cap = max(min(MIN_DAY_CAP, int(day_cap)), cap)
        return max(MIN_DAY_CAP, cap)

    plan: list[dict] = []
    cap_total = 0
    queue = list(ordinary)
    rot_rev = 0
    learned_ids: set[str] = set()          # 只复习「已经学过」的，别让复习卡抢在新课前面

    # 均匀铺开：每份「权重 1」该分到多少分钟 = 总需求 ÷ 剩余学习日的权重之和
    learning_days = [d for i, d in enumerate(phase_a, 1) if i % 4 != 0]
    weight_total = sum(w(d) for d in learning_days) or 1.0
    unit = sum(task_minutes(g) for g in queue) / weight_total

    for i, day in enumerate(phase_a, 1):
        # 剩下的还没学的量 ÷ 剩下的学习日权重：前面没排满，后面的天自己补上
        left_days = [d for d in learning_days if d >= day]
        left_weight = sum(w(d) for d in left_days) or 1.0
        target = max(unit * w(day),
                     sum(task_minutes(g) for g in queue) / left_weight * w(day))
        cap = cap_of(day, target)
        cap_total += cap
        if i % 4 == 0:                                # 每 4 天一个纯复习日
            learned = [(d["date"], t) for d in plan for t in d["tasks"] if t["kind"] == "learn"]
            older = [t for when, t in learned if (day - when).days >= 4]
            pool = older or [t for _, t in learned]
            picks = pool[-3:]
            tasks = [{"id": t["id"], "title": t["title"], "kind": "review",
                      "min": 15, "p": t["p"]} for t in picks]
            plan.append({"date": day, "kind": "复习日", "cap": cap, "tasks": tasks})
            continue

        tasks, used = [], 0
        # 不再限「一天最多 3 张」：卡数由分钟决定（当天配额 / 每日上限），
        # 落后时一天自然会多排几张，而不是把目标挤到排不下。
        while queue and (used < target or not tasks):
            g = queue[0]
            need = task_minutes(g)
            # 卡是按目标整体切的，不能拆：没设每日上限时，单张比当天配额大也得排得下，
            # 否则卡一大就整天卡死、最后剩一堆目标排不进去（重排会一直「排不下」）。
            if used + need > cap and (tasks or day_cap):
                break
            queue.pop(0)
            tasks.append({"id": g["id"], "title": g["title"], "kind": "learn",
                          "min": need, "p": g["p"], "cp": g.get("cp", [])})
            learned_ids.add(g["id"])
            used += need
        if not tasks and not queue and day.weekday() != 6:   # 内容学完了才用滚动复习填空，周日留白
            pool = [g for g in ordinary if g["id"] in learned_ids] or ordinary
            # 复习是「填空」：最多 3 张 / 45 分钟，不再按当天剩余预算无限加
            while used + 15 <= cap and used < REVIEW_FILL_MIN:
                g = pool[rot_rev % len(pool)]
                rot_rev += 1
                tasks.append({"id": g["id"], "title": g["title"], "kind": "review",
                              "min": 15, "p": g["p"]})
                used += 15
        plan.append({"date": day, "kind": "学习日", "cap": cap, "tasks": tasks})

    # 后 20%：模考 + 轮着回顾各章核心（不重复只盯前两章）
    core_ids = [g for g in ordinary if g["p"] == "core"]
    rot = 0
    # 冲刺日也按剩余量摊：模考的量 ÷ 剩余天数 + 课后复习的位置
    mean_w = (weekday_weight * 5 + weekend_weight * 2) / 7 or 1.0
    b_room = sum(task_minutes(g) for g in exam) / max(1, len(phase_b)) + 45
    exam_max = max((task_minutes(g) for g in exam), default=0)
    for day in phase_b:
        # 冲刺日也要放得下一整张模考卡（卡不能拆）
        cap = int(b_room * (w(day) / mean_w) / (1 - buffer_rate)) + exam_max
        if day_cap:
            cap = min(cap, int(day_cap))
        floor = min(MIN_DAY_CAP, int(day_cap)) if day_cap else MIN_DAY_CAP
        cap = max(floor, cap)
        cap_total += cap
        tasks, used = [], 0
        while exam:                                   # 也按分钟，不按张数
            need = task_minutes(exam[0])
            if used + need > cap and (tasks or day_cap):
                break
            g = exam.pop(0)
            tasks.append({"id": g["id"], "title": g["title"], "kind": "exam",
                          "min": need, "p": g["p"], "cp": g.get("cp", [])})
            used += need
        while core_ids and used + 15 <= cap and used < REVIEW_FILL_MIN:
            g = core_ids[rot % len(core_ids)]
            rot += 1
            tasks.append({"id": g["id"], "title": g["title"], "kind": "review",
                          "min": 15, "p": g["p"]})
            used += 15
        plan.append({"date": day, "kind": "冲刺日", "cap": cap, "tasks": tasks})

    uncovered = [g for g in (queue + exam)]
    study_days = [d for d in plan if d["tasks"]]
    planned = sum(t["min"] for d in plan for t in d["tasks"])
    return {"plan": plan, "uncovered": uncovered, "cap_total": cap_total,
            "split": split, "total_days": total_days,
            "daily_avg": round(planned / max(1, len(study_days))),
            "day_cap": int(day_cap or 0),
            "weekday_weight": weekday_weight, "weekend_weight": weekend_weight}


# ------------------------------------------------------------------ 输出
def fmt_plan(res: dict, data: dict, deadline: dt.date, start: dt.date,
             weekend_weight: float = WEEKEND_WEIGHT, day_cap: int = 0) -> str:
    plan = res["plan"]
    learn = [t for d in plan for t in d["tasks"] if t["kind"] == "learn"]
    total_min = sum(t["min"] for d in plan for t in d["tasks"])
    study_days = len([d for d in plan if d["tasks"]])
    lines = []
    A = lines.append

    A(f"# {data['subject']} · 到 {deadline} 的学习计划")
    A("")
    A(f"> 生成于 {start}，考试形式：{data.get('exam_format','')}")
    A("")
    A("## 一、总量对不对得上")
    A("")
    A(f"- 目标 {len(data['goals'])} 个，其中核心 {sum(1 for g in data['goals'] if g['p']=='core')} 个")
    A(f"- 学习总量 {sum(task_minutes(g) for g in data['goals'])} 分钟（含每个目标 10 分钟小测）")
    A(f"- 计划共 {total_min} 分钟：新学 {sum(t['min'] for t in learn)}，"
      f"复习与模考 {total_min - sum(t['min'] for t in learn)}")
    A(f"- {study_days} 天有任务，跨度 {res['total_days']} 天，"
      f"日均 {round(total_min / max(1, study_days))} 分钟")
    A(f"- **每天学多少不写死**：按「没学完的分钟 ÷ 剩余可学天数」自动摊，"
      f"周末 ×{weekend_weight:.1f}"
      + (f"，每天硬上限 {day_cap} 分钟" if day_cap else "，不设硬上限"))
    A(f"- 前 {res['split']} 天学新内容，后 {res['total_days'] - res['split']} 天模考 + 总复习")
    A("")
    if res["uncovered"]:
        unc = res["uncovered"]
        opt = [g for g in unc if g["p"] == "optional"]
        sup = [g for g in unc if g["p"] == "support"]
        core = [g for g in unc if g["p"] == "core"]
        A(f"**⚠️ 按现在的时间，有 {len(unc)} 个目标排不进去。**")
        A("")
        if opt:
            A(f"- 可以先砍（可选）：{'、'.join(g['id'] for g in opt)}")
        if sup:
            A(f"- 其次砍（辅助）：{'、'.join(g['id'] for g in sup)}")
        if core:
            shown = '、'.join(g['id'] for g in core[:6])
            A(f"- 必须取舍（核心）：{shown}" + ("…" if len(core) > 6 else ""))
            A("  > 核心目标缺一块会直接掉分，优先加时间，而不是砍。")
        A("")
        need = sum(task_minutes(g) for g in unc)
        A(f"- 方案 A：每天再多 **{math.ceil(need / max(1, res['total_days']))} 分钟**，全部排得下")
        if day_cap:
            A(f"  > 但你现在设了每天上限 {day_cap} 分钟，先把它调高（或设 0 = 不限）")
        later = None
        for back in range(1, 181):
            d2 = deadline + dt.timedelta(days=back)
            if not schedule(data["goals"], start, d2, weekend_weight=weekend_weight,
                            day_cap=day_cap)["uncovered"]:
                later = d2
                break
        if later:
            A(f"- 方案 B：考核日往后挪到 **{later}**，全部排得下")
        trimmed = [g for g in data["goals"] if g["p"] == "core" or g not in unc]
        r2 = schedule(trimmed, start, deadline, weekend_weight=weekend_weight,
                      day_cap=day_cap)
        if not r2["uncovered"]:
            A(f"- 方案 C：砍掉上面 {len(opt) + len(sup)} 个可选/辅助目标，核心全部排得下")
        else:
            A(f"- 方案 C：即使砍掉可选/辅助，仍有 {len(r2['uncovered'])} 个核心排不下"
              f" → 只能靠方案 A 或 B")
        A("")
    else:
        A(f"**✅ 全部目标都排进去了，每天还留了 {int(BUFFER_RATE * 100)}% 余量。**")
        A("")

    A("## 二、逐日计划")
    A("")
    A("| 日期 | 星期 | 类型 | 任务 | 分钟 |")
    A("|---|---|---|---|---|")
    for d in plan:
        if not d["tasks"]:
            A(f"| {d['date']} | {WD[d['date'].weekday()]} | {d['kind']} | 缓冲（没排任务，用来吸收落后） | — |")
            continue
        for j, t in enumerate(d["tasks"]):
            day = str(d["date"]) if j == 0 else ""
            wk = WD[d["date"].weekday()] if j == 0 else ""
            kind = d["kind"] if j == 0 else ""
            mark = {"learn": "学", "review": "复", "exam": "考"}[t["kind"]]
            A(f"| {day} | {wk} | {kind} | {mark} · {t['title']} | {t['min']} |")
    A("")
    return "\n".join(lines)


def sensitivity(goals, data, start, deadline, weekend_weight=WEEKEND_WEIGHT,
                day_cap: int = 0) -> str:
    rows = []
    for lost in (1, 3, 7):
        r = schedule(goals, start, deadline, weekend_weight=weekend_weight,
                     lost_days=lost, day_cap=day_cap)
        rows.append((lost, len(r["uncovered"]), r["daily_avg"]))
    out = ["## 三、落后的代价（重排模拟）", "",
           "| 已经浪费 | 排不进去的目标 | 重排后日均 | 说明 |", "|---|---|---|---|"]
    for lost, n, avg in rows:
        note = "缓冲能吸收，不用改计划" if n == 0 else f"需要砍掉 {n} 个目标，或把每天上限调高"
        out.append(f"| {lost} 天 | {n} 个 | {avg} 分钟 | {note} |")
    out.append("")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--goals", default=str(BASE / "data" / "goals.json"))
    ap.add_argument("--deadline", help="考核日 YYYY-MM-DD（默认读 goals.json 的 exam_date）")
    ap.add_argument("--start", default=dt.date.today().isoformat())
    ap.add_argument("--weekend-ratio", type=float, default=WEEKEND_WEIGHT,
                    help="周末比工作日多学多少（1.3 = 多 30%%；不写死分钟数）")
    ap.add_argument("--day-cap", type=int, default=0,
                    help="每天最多多少分钟，0 = 不限")
    ap.add_argument("--out", default=str(BASE / "plan.md"))
    args = ap.parse_args()

    data = load(Path(args.goals))
    dl = args.deadline or data.get("exam_date")
    if not dl:
        raise SystemExit("没有考核日：加 --deadline 2027-01-04，或在 goals.json 里写 exam_date")
    start = dt.date.fromisoformat(args.start)
    deadline = dt.date.fromisoformat(dl)
    res = schedule(data["goals"], start, deadline,
                   weekend_weight=args.weekend_ratio, day_cap=args.day_cap)
    text = fmt_plan(res, data, deadline, start, args.weekend_ratio, args.day_cap)
    text += sensitivity(data["goals"], data, start, deadline,
                        args.weekend_ratio, args.day_cap)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text[:1500])
    print(f"\n…完整计划已写入 {args.out}（{len(res['plan'])} 天）")


if __name__ == "__main__":
    main()
