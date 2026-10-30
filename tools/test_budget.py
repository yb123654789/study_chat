"""跨课程总预算自检（engine._course_budgets）。

规则：没设总预算 / 各课自己排得下 → 不分；超了才按「离考核日远近」分，
分到这门课自己需要的日均为止，剩下的给别的课。
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import engine  # noqa: E402

START = date(2026, 10, 30)
fails = []


class FakeDB:
    def __init__(self, cap, exams, needs):
        self.cap = cap
        self.exams = exams          # {cid: 考核日}
        self.needs = needs          # {cid: 还要学多少分钟}

    def geti(self, key, default=0):
        return self.cap if key == "day_total_cap" else default

    def course(self, cid):
        return {"id": cid, "name": {"a": "近课", "b": "中课", "c": "远课"}.get(cid, cid),
                "exam_date": self.exams[cid]}

    def goals(self, course=None):
        n = max(1, self.needs[course] // 40)     # 每个目标 30 分钟 + 10 分钟小测
        return [{"id": f"{course}{i}", "m": 30} for i in range(n)]

    def done_goal_ids(self, cid):
        return set()


def exams(days):
    return {k: (START + timedelta(days=v)).isoformat() for k, v in days.items()}


# 1. 没设总预算 → 不分
db0 = FakeDB(0, exams({"a": 19}), {"a": 3000})
if engine._course_budgets(db0, ["a"], START) != {}:
    fails.append("没设总预算却分了")

# 2. 总预算够 → 不分（各排各的）
db1 = FakeDB(1000, exams({"a": 19, "b": 59}), {"a": 3000, "b": 3000})
if engine._course_budgets(db1, ["a", "b"], START) != {}:
    fails.append("总预算够却硬分")

# 3. 超了 → 按远近分，近的多；总和不超过预算（允许 1 分钟取整）
db2 = FakeDB(120, exams({"a": 19, "b": 59}), {"a": 3000, "b": 3000})
got = engine._course_budgets(db2, ["a", "b"], START)
if not got:
    fails.append("超预算却没有分配")
elif sum(got.values()) > 121:
    fails.append(f"分超了: {got}")
elif not got.get("a", 0) > got.get("b", 0):
    fails.append(f"近的课没分到更多: {got}")

# 4. 三门课：近 > 中 > 远
db3 = FakeDB(100, exams({"a": 19, "b": 39, "c": 79}),
             {"a": 3000, "b": 3000, "c": 3000})
got3 = engine._course_budgets(db3, ["a", "b", "c"], START)
if sum(got3.values()) > 101:
    fails.append(f"三门课分超了: {got3}")
if not (got3.get("a", 0) > got3.get("b", 0) > got3.get("c", 0)):
    fails.append(f"三门课没按远近: {got3}")

# 5. 一门课需求小：先满足它，剩下的给别人，不能浪费额度
db4 = FakeDB(120, exams({"a": 19, "b": 59}), {"a": 400, "b": 9000})
got4 = engine._course_budgets(db4, ["a", "b"], START)
if got4.get("a", 0) < 20 or got4.get("b", 0) < 60:
    fails.append(f"小需求课/另一门没分到: {got4}")

print("分配:", got, got3, got4)
print("FAILS:", fails if fails else "无 ✅")
sys.exit(1 if fails else 0)
