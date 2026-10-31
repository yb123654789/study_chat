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
import engine        # noqa: E402
import paths         # noqa: E402
import ui           # noqa: E402
from service import Service  # noqa: E402

sys.path.insert(0, str(BASE / "tests"))
import make_demo     # noqa: E402


def main() -> int:
    # 用 tests/fixtures 的最小测试集建界面：不读真实资料、不复制用户库
    # 不用 tempfile.mkdtemp：它按 0700 建目录，DSH 沙箱里子目录都建不进去
    tmp = Path(tempfile.gettempdir()) / f"studybot-ui-{os.getpid()}-{time.time_ns()}"
    tmp.mkdir(parents=True, exist_ok=False)
    paths.use_root(tmp, save=False)
    make_demo.build(tmp)
    d = dbmod.DB(paths.db_file())
    d.init()
    d.import_goals(make_demo.goals_file())
    make_demo.seed_quiz(d)
    try:
        engine.regenerate(d)          # 有今日任务，卡片页才有东西
    except Exception:                 # noqa: BLE001
        pass
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

    # 「待拆」选择框：搜索 / 过滤 / 多选 / 费用
    try:
        pend = paths.pending_dir("ds")
        pend.mkdir(parents=True, exist_ok=True)
        (pend / "冒烟-中文.txt").write_text("二分查找：定义、循环不变量、边界条件。" * 50,
                                              encoding="utf-8")
        (pend / "冒烟-english.txt").write_text("Divide and conquer is a strategy. " * 50,
                                                 encoding="utf-8")
        app.show("materials")
        app.update()
        page = app._pages["materials"]
        page.refresh()
        app.update()
        assert "待拆 2 份" in page.summary.cget("text"), page.summary.cget("text")
        dlg = ui.DeriveDialog(page)
        dlg.update()
        assert len(dlg.tree.get_children()) == 2, len(dlg.tree.get_children())
        dlg._all()
        dlg.update()
        assert "已选 2 份" in dlg.sel_lbl.cget("text"), dlg.sel_lbl.cget("text")
        dlg.q.set("english")
        dlg._reload()
        dlg.update()
        assert len(dlg.tree.get_children()) == 1, len(dlg.tree.get_children())

        # 真正点一次「开始拆」：2026-10-31 这里 NameError 被 Tk 吞掉，用户只看到没反应
        dlg.q.set("")
        dlg._reload()
        dlg._all()
        dlg.update()
        sent: list = []
        real_run = page._run_derive
        page._run_derive = lambda files: sent.append(list(files))
        try:
            dlg.btn_go.invoke()
            app.update()
        finally:
            page._run_derive = real_run
        assert sent, "点「开始拆」没有走到拆目标（回调被吞了？）"
        assert len(sent[0]) == 2, f"只该拆选中的 2 份，实际 {len(sent[0])} 份"
        print("  ✓ 待拆选择框：搜索 / 多选 / 预估费用 / 点开始拆把选中的交出去")
    except Exception:  # noqa: BLE001
        bad += 1
        print("  ✗ 待拆选择框")
        traceback.print_exc()

    # 设置页：手机连接（地址/token）+ 每天总预算控件在
    try:
        app.show("settings")
        app.update()
        page = app._pages["settings"]
        assert page.conn_url.cget("text"), "手机连接地址为空"
        assert page.conn_token.cget("text"), "手机连接 token 为空"
        assert page.f_total.get() != "", "每天总预算输入框为空"
        old_en = app.db.get("en_query", "")
        assert hasattr(page, "f_en"), "设置页没有「英文资料也参与检索」开关"
        page.f_en.set(True)
        page.save_model()
        assert app.db.get("en_query") == "1", app.db.get("en_query")
        page.f_en.set(False)
        page.save_model()
        assert app.db.get("en_query") == "0", app.db.get("en_query")
        app.db.set("en_query", old_en)
        print("  ✓ 设置页：手机连接 / 每天总预算 / 英文检索开关")
    except Exception:  # noqa: BLE001
        bad += 1
        print("  ✗ 设置页：手机连接 / 每天总预算")
        traceback.print_exc()

    # 设置页比窗口高 3 倍：必须能滚到底（2026-10-31 用户报「下面滚不到」）
    try:
        app.show("settings")
        app.update()
        host = app._hosts["settings"]
        assert host.scrollable(), "设置页内容比窗口高，滚动条没出来"
        assert host.vsb.winfo_ismapped(), "滚动条控件没有显示"
        top = host.canvas.yview()
        host.canvas.yview_moveto(1.0)
        app.update()
        bottom = host.canvas.yview()
        assert bottom[1] > 0.99 and bottom[0] > top[0], f"滚不到底：{top} → {bottom}"
        host.canvas.yview_moveto(0.0)
        app.update()
        print(f"  ✓ 设置页能滚到底（内容 {host.inner.winfo_reqheight()}px，可见 {host.canvas.winfo_height()}px）")
    except Exception:  # noqa: BLE001
        bad += 1
        print("  ✗ 设置页滚动")
        traceback.print_exc()

    # 页面都没超出时不该出现滚动条（学习页原来 817px，被裁掉底部的输入框）
    try:
        app.show("study")
        app.update()
        host = app._hosts["study"]
        assert host.inner.winfo_reqheight() <= host.canvas.winfo_height() + 1, \
            f"学习页还是比窗口高：{host.inner.winfo_reqheight()} > {host.canvas.winfo_height()}"
        assert not host.scrollable(), "学习页不需要滚动却挂了滚动条"
        assert app._pages["study"].btn_send.winfo_viewable(), "发送按钮不可见"
        print("  ✓ 学习页不用滚，发送按钮可见")
    except Exception:  # noqa: BLE001
        bad += 1
        print("  ✗ 学习页布局")
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
