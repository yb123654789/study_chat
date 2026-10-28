"""planner 自检：依赖顺序、每日上限、无重复、日期连续、落后自动加量。

重点：容量不是写死的分钟数，而是「剩余量 ÷ 剩余天数」算出来的；
       落后越多，重排后每天越大；设了 day_cap 就必须守住。
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import planner as P

data = P.load(Path(P.BASE / "data" / "goals.json"))
goals = data["goals"]
start = date(2026, 10, 24)
deadline = date(2027, 1, 8)

fails = []

# 1. 每个目标只出现一次
res = P.schedule(goals, start, deadline)
scheduled = [t["id"] for d in res["plan"] for t in d["tasks"] if t["kind"] in ("learn", "exam")]
dups = {i for i in scheduled if scheduled.count(i) > 1}
if dups:
    fails.append(f"重复排入: {dups}")

# 2. 日期连续覆盖
dates = [d["date"] for d in res["plan"]]
if dates[0] != start or dates[-1] != deadline:
    fails.append(f"日期范围不对: {dates[0]}..{dates[-1]}")
if any((dates[i + 1] - dates[i]).days != 1 for i in range(len(dates) - 1)):
    fails.append("日期不连续")

# 3. 依赖必须先出现
pos = {gid: i for i, gid in enumerate(scheduled)}
by_id = {g["id"]: g for g in goals}
for g in goals:
    if g["id"] not in pos:
        continue
    for dep in g.get("d", []):
        if dep in pos and pos[dep] > pos[g["id"]]:
            fails.append(f"依赖倒置: {g['id']} 早于 {dep}")

# 4. 每天不超当天上限（上限也是算出来的，不写死）
for d in res["plan"]:
    used = sum(t["min"] for t in d["tasks"])
    if used > d["cap"]:
        fails.append(f"{d['date']} 超上限 {used}>{d['cap']}")

# 5. 不再限「一天最多 3 张」：卡片数由分钟决定，赶进度时一天可以多排
ruff = P.schedule(goals, start, date(2026, 11, 20))
most = max(len(d["tasks"]) for d in ruff["plan"])
if most <= 3:
    fails.append(f"紧截止日还是一天最多 3 张（上限没去掉）: {most}")

# 6. 截止日压到 25 天：不限张数后不再靠「排不下」提醒，而是日均分钟数飙上去
tight = P.schedule(goals, start, date(2026, 11, 20))
if tight["uncovered"]:
    fails.append(f"没设每日上限时不该排不下: {len(tight['uncovered'])} 个")
if tight["daily_avg"] <= res["daily_avg"]:
    fails.append(f"压缩截止日后日均没变大: {tight['daily_avg']} vs {res['daily_avg']}")

# 7. 复习卡不能抢在新课前面（没学过就先复习＝白考）
learn_at = {}
for d in res["plan"]:
    for t in d["tasks"]:
        if t["kind"] == "learn":
            learn_at.setdefault(t["id"], d["date"])
early = [(t["id"], d["date"]) for d in res["plan"] for t in d["tasks"]
         if t["kind"] == "review" and t["id"] in learn_at and d["date"] < learn_at[t["id"]]]
if early:
    fails.append(f"复习排在学之前: {early[:3]}")

# 8. 小课程（每天只放得下一张卡）：第一天不能是空的，复习也不能跑在学之前
small = [{"id": f"s{i}", "ch": "x", "title": f"小目标{i}", "p": "core", "m": 30,
          "d": [], "cp": []} for i in range(1, 4)]
rs = P.schedule(small, start, date(2026, 12, 20))
learn_s = {}
for d in rs["plan"]:
    for t in d["tasks"]:
        if t["kind"] == "learn":
            learn_s.setdefault(t["id"], d["date"])
early_s = [(t["id"], d["date"]) for d in rs["plan"] for t in d["tasks"]
           if t["kind"] == "review" and t["id"] in learn_s and d["date"] < learn_s[t["id"]]]
if early_s:
    fails.append(f"小课程复习跑在学之前: {early_s[:3]}")
if not rs["plan"][0]["tasks"]:
    fails.append("小课程第一天排空了")
if len(learn_s) != 3:
    fails.append(f"小课程没学完: {sorted(learn_s)}")

# 9. 容量不是写死的：截止日越近，每天该学的越多
near = P.schedule(goals, start, date(2026, 12, 10))
if near["daily_avg"] <= res["daily_avg"]:
    fails.append(f"截止日近了日均没变大: {near['daily_avg']} vs {res['daily_avg']}")

# 10. 落后越多，重排后每天越长（写死分钟数的话这条永远过不了）
for lost in (7, 30):
    lag = P.schedule(goals, start, deadline, lost_days=lost)
    if lag["plan"][0]["cap"] <= res["plan"][0]["cap"]:
        fails.append(f"落后 {lost} 天，第一天上限没变大: "
                     f"{lag['plan'][0]['cap']} vs {res['plan'][0]['cap']}")
    if lag["daily_avg"] < res["daily_avg"]:
        fails.append(f"落后 {lost} 天日均反而变小了: {lag['daily_avg']}")

# 11. day_cap 是硬上限：每天都得守住，卡住了要明说排不下
capped = P.schedule(goals, start, deadline, day_cap=55)
over = [(str(d["date"]), sum(t["min"] for t in d["tasks"])) for d in capped["plan"]
        if sum(t["min"] for t in d["tasks"]) > 55]
if over:
    fails.append(f"设了 day_cap 还超: {over[:3]}")
if not capped["uncovered"]:
    fails.append("每日上限压不住时没有报「排不下」")

print("目标数", len(goals), "| 排入", len(scheduled), "| 未排入", len(res["uncovered"]),
      "| 日均", res["daily_avg"], "分钟")
print("FAILS:", fails if fails else "无 ✅")
sys.exit(1 if fails else 0)
