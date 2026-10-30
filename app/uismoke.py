#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面冒烟测试：把十个页面都建一遍，抓出 Tk 层面的异常。不开长期窗口。

    py app/uismoke.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

APP = Path(__file__).resolve().parent
BASE = APP.parent
for p in (str(APP), str(BASE / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import db as dbmod   # noqa: E402
import paths         # noqa: E402
import ui           # noqa: E402
from service import Service  # noqa: E402


def main() -> int:
    # 用真实库的副本建界面：调试不能改到用户的数据
    real = BASE / "data"
    # 不用 tempfile.mkdtemp：它按 0700 建目录，DSH 沙箱里子目录都建不进去
    tmp = Path(tempfile.gettempdir()) / f"studybot-ui-{os.getpid()}-{time.time_ns()}"
    tmp.mkdir(parents=True, exist_ok=False)
    paths.use_root(tmp, save=False)
    if (real / "studybot.db").exists():
        import sqlite3
        src = sqlite3.connect(real / "studybot.db")
        dst = sqlite3.connect(paths.db_file())
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
    for name in ("goals.json",):
        if (real / name).exists():
            (tmp / name).write_bytes((real / name).read_bytes())
    d = dbmod.DB(paths.db_file())
    d.init()
    if not d.goals():
        d.import_goals(paths.goals_json())
    svc = Service(d)
    app = ui.App(d, svc)
    app.update()
    bad = 0
    for key, name in app.NAV:
        try:
            app.show(key)
            app.update()
            print(f"  ✓ {name:<6} 页面建好")
        except Exception:  # noqa: BLE001
            bad += 1
            print(f"  ✗ {name} 页面炸了")
            traceback.print_exc()
    # 再切回总览，确认来回切不炸
    try:
        for key, _ in app.NAV:
            app.show(key)
            app.update()
        print("  ✓ 反复切换页面")
    except Exception:  # noqa: BLE001
        bad += 1
        traceback.print_exc()

    # 「待确认」页（P0 新加）：先看空状态，再插一条临时数据看列表/详情/按钮
    try:
        app.show("review")
        app.update()
        page = app._pages["review"]
        assert "未处理" in page.count_lbl.cget("text"), page.count_lbl.cget("text")
        assert page.detail.cget("text"), "待确认页没有空状态说明"
        t = d.today_tasks()[0] if d.today_tasks() else {}
        rid = d.review_add(kind="task_state", task_id=t.get("id"), goal_id=t.get("goal_id", ""),
                           day=dbmod.today_s(), phone_state="done", server_state="todo",
                           reason="界面冒烟：手机判完成、电脑判未完成")
        page.refresh()
        app.update()
        kids = page.tree.get_children()
        assert len(kids) == 1, f"列表里有 {len(kids)} 条"
        page.tree.selection_set(kids[0])
        page._on_select()
        assert str(page.btn_server.cget("state")) == "normal", page.btn_server.cget("state")
        assert "手机：done" in page.detail.cget("text"), page.detail.cget("text")
        d.review_resolve(rid, "follow_server")
        page.refresh()
        app.update()
        assert not page.tree.get_children(), "处理完还挂在列表里"
        print("  ✓ 待确认页：空状态 / 列表 / 详情 / 按钮 / 处理完清空")
    except Exception:  # noqa: BLE001
        bad += 1
        print("  ✗ 待确认页")
        traceback.print_exc()

    # 无 key 提示（验收项）：清空 → 状态栏出现；填回 → 消失
    try:
        old_key = app.db.get("api_key", "")
        app.db.set("api_key", "")
        app.refresh_key_hint()
        app.update()
        assert "未配置模型 key" in app.key_hint.cget("text"), app.key_hint.cget("text")
        app.db.set("api_key", old_key or "sk-ui-smoke-only")
        app.refresh_key_hint()
        app.update()
        assert not app.key_hint.cget("text"), app.key_hint.cget("text")
        app.db.set("api_key", old_key)
        print("  ✓ 无 key 有提示，填回就消失")
    except Exception:  # noqa: BLE001
        bad += 1
        print("  ✗ 无 key 提示")
        traceback.print_exc()

    # 设置页：手机连接（地址/token）+ 每天总预算控件在
    try:
        app.show("settings")
        app.update()
        page = app._pages["settings"]
        assert page.conn_url.cget("text"), "手机连接地址为空"
        assert page.conn_token.cget("text"), "手机连接 token 为空"
        assert page.f_total.get() != "", "每天总预算输入框为空"
        print("  ✓ 设置页：手机连接 / 每天总预算")
    except Exception:  # noqa: BLE001
        bad += 1
        print("  ✗ 设置页：手机连接 / 每天总预算")
        traceback.print_exc()

    # 课程下拉：至少数据结构这门；有第二门就切一下
    try:
        app.load_courses()
        assert app.course_id(), "当前课程为空"
        print(f"  ✓ 课程下拉（{app.course_name()}）")
        if len(app.courses()) > 1:
            app.course_box.current(1)
            app._course_changed()
            app.update()
            for key, _ in app.NAV:
                app.show(key)
                app.update()
            print(f"  ✓ 切到 {app.course_name()} 后 10 页都能开")
            app.course_box.current(0)
            app._course_changed()
            app.update()
    except Exception:  # noqa: BLE001
        bad += 1
        traceback.print_exc()
    app.destroy()
    print("\n界面冒烟：" + ("通过" if not bad else f"{bad} 处失败"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
