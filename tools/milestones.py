import datetime as dt
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
goals = {g["id"]: g for g in json.loads((BASE / "data/goals.json").read_text(encoding="utf-8"))["goals"]}
text = (BASE / "plan.md").read_text(encoding="utf-8")

cur_date = None
last_learn = {}
all_dates = []
for line in text.splitlines():
    m = re.match(r"\| (\d{4}-\d{2}-\d{2}) \|", line)
    if m:
        cur_date = m.group(1)
    for gid, g in goals.items():
        if g["title"][:12] in line and cur_date and ("学 ·" in line or "考 ·" in line):
            all_dates.append(cur_date)
            last_learn.setdefault(g["ch"], cur_date)
            last_learn[g["ch"]] = max(last_learn[g["ch"]], cur_date)

print("章节完成日：")
for ch, d in last_learn.items():
    print(f"  {ch:<6} {d}")

print("\n模考安排：")
for line in text.splitlines():
    if "模考" in line or "12 题" in line:
        print(" ", line.strip()[:90])

# 每周负载
wk = {}
for line in text.splitlines():
    m = re.match(r"\| (\d{4}-\d{2}-\d{2}) \| .*? \| .*? \| .* \| (\d+) \|", line)
    if m:
        d = dt.date.fromisoformat(m.group(1))
        wk[d] = wk.get(d, 0) + int(m.group(2))
if wk:
    vals = sorted(wk.values())
    print(f"\n有任务的天数 {len(wk)}，日均 {sum(wk.values())//len(wk)} 分钟，"
          f"最重 {vals[-1]} 分钟，最轻 {vals[0]} 分钟")
    busiest = max(wk, key=wk.get)
    print("最重的一天：", busiest, wk[busiest], "分钟")
