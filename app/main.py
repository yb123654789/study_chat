#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学习bot 桌面版入口：python app/main.py（或双击 学习bot.cmd）"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

APP = Path(__file__).resolve().parent
BASE = APP.parent
for p in (str(APP), str(BASE / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import paths  # noqa: E402


def set_dpi():
    """不声明 DPI 感知：布局按 96dpi 设计，交给系统整体缩放，避免 150% 下错位。"""
    return


def choose_data_dir(default: Path) -> Path:
    """第一次启动（打包后）：问一次数据放哪；已有数据的文件夹直接沿用。"""
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    has_db = (default / "studybot.db").exists()
    msg = (f"学习bot 第一次运行，需要确定数据放哪。\n\n默认：{default}\n\n"
           "数据包括：学习库（studybot.db）、资料、备份、导出。\n"
           "程序以后换版本，数据都留在这个文件夹里。")
    if has_db:
        msg += "\n\n这个位置已经有数据了，会直接用。"
    if messagebox.askyesno("选择数据文件夹", msg + "\n\n用这个默认位置吗？"):
        root.destroy()
        return default
    picked = filedialog.askdirectory(title="选择（或新建）一个文件夹放数据",
                                     initialdir=str(Path.home()))
    root.destroy()
    return Path(picked) if picked else default


def seed_quiz(db) -> int:
    """第一次运行：把 tests/fixtures/seed_quiz.json 里的现成题导进题库，免得一上来没题。"""
    if db.quiz_count() > 0:
        return 0
    p = paths.CODE_ROOT / "tests" / "fixtures" / "seed_quiz.json"
    if not p.exists():
        return 0
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for gid, items in raw.items():
        if gid.startswith("_") or not isinstance(items, list):
            continue
        for it in items:
            db.add_quiz(gid, 1, it, source="seed")
            n += 1
    if n:
        db.log(f"导入现成题库 {n} 道", "ok")
    return n


def single_instance():
    """Windows 上只允许开一个窗口。

    同一个 Bot Token 只能有一个进程在 getUpdates，开两个会互相踢下线
    （Telegram 返回 409 Conflict），症状就是消息时好时坏、答一半没反应。
    """
    import msvcrt
    lock_path = paths.lock_file()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        f = open(lock_path, "a+b")
    except OSError:
        return True                      # 连锁文件都建不了，就别拦着用户
    try:
        f.seek(0, 2)
        if f.tell() == 0:
            f.write(b"x")
            f.flush()
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        return f                          # 持有到进程结束，别关
    except OSError:
        f.close()
        return None


def warn_already_running() -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, "学习bot 已经开着一个了。\n\n同一个 bot 只能有一个窗口在收消息，"
               "开两个会互相踢下线。\n\n请先关掉那个窗口，或者直接用它。",
            "学习bot", 0x40)
    except Exception:  # noqa: BLE001
        pass


def desktop_config() -> dict:
    """<数据目录>/desktop.json：{"server": "http://1.2.3.4:8787", "token": "..."}"""
    p = paths.desktop_config()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    set_dpi()
    # 数据目录：第一次打包运行会问一次，之后按配置走（环境变量 STUDYBOT_HOME 可覆盖）
    paths.ensure_for_startup(lambda: choose_data_dir(paths.default_root()))
    lock = single_instance()
    if lock is None:
        warn_already_running()
        return 0

    cfg = desktop_config()
    if cfg.get("server"):
        return run_remote(cfg)

    import db as dbmod

    d = dbmod.DB()
    d.backup_before_upgrade()       # 结构要升级就先备一份，迁移出问题能回退
    d.init()
    d.log("软件启动", "info")
    # 老布局（extracted/inbox、extracted/书…）→ 待拆/<课>/：只挪 txt，不动原件
    try:
        import engine as _eng
        moved = _eng.migrate_pending_layout(d)
        if moved:
            d.log(f"待拆整理：{moved} 份文本挪进「待拆」", "info")
    except Exception as e:  # noqa: BLE001
        d.log(f"待拆整理失败：{type(e).__name__}: {e}", "warn")
    try:
        d.auto_backup()             # 每天第一次启动备一份，升级/误删都能回退
        if not d.goals():
            d.import_goals()
        seed_quiz(d)
    except Exception as e:  # noqa: BLE001
        d.log(f"初始化失败：{e}", "err")

    from service import Service
    from ui import App

    # 第一次打开：目标有了但还没有计划，直接排一份，别让用户面对空白页
    try:
        import engine
        if not d.q1("SELECT COUNT(*) AS n FROM tasks"):
            r = engine.regenerate(d)
            d.log(f"首次自动排计划：{r['note']}", "ok")
        # 落后了（电脑关机/没学）就把欠的卡并回队列重排；不落后什么都不做
        engine.auto_replan(d)
    except Exception as e:  # noqa: BLE001
        d.log(f"启动排计划失败：{e}", "warn")

    svc = Service(d)
    # 手机连接服务：桌面端自己起，不用再开终端跑 server/run.py
    local = None
    try:
        from localserver import LocalServer
        local = LocalServer(d, svc)
        if local.start():
            d.log(f"手机连接服务已启动：{local.url}", "ok")
        else:
            d.log(f"手机连接服务没起来：{local.error}", "warn")
    except Exception as e:  # noqa: BLE001  起不来只影响手机，别挡桌面
        d.log(f"手机连接服务没起来：{type(e).__name__}: {e}", "warn")
    try:
        return show_app(d, svc, local)
    finally:
        if local is not None:
            local.stop()


def run_remote(cfg: dict) -> int:
    """瘦客户端模式：数据在服务器上，本机只管显示。"""
    from remote import RemoteDB, RemoteError, RemoteService

    base = str(cfg.get("server") or "").rstrip("/")
    d = RemoteDB(base, cfg.get("token", ""), timeout=90)
    try:
        d.call("GET", "/api/health")
    except RemoteError as e:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, f"连不上服务器：\n{base}\n\n{e}\n\n"
               "检查：服务器是否在跑、地址端口对不对、数据目录里的 desktop.json 里的 token。",
            "学习bot", 0x30)
        return 1
    d.log("桌面端已连接服务器", "info")
    return show_app(d, RemoteService(d))


def show_app(d, svc, local=None) -> int:
    from ui import App
    app = App(d, svc)
    app.local_server = local            # 设置页要显示"手机连接服务"的状态
    # 调试用：py app/main.py plan → 直接打开某一页
    if len(sys.argv) > 1 and sys.argv[1] in dict(App.NAV):
        app.show(sys.argv[1])
    app.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                    # 打包后没控制台，至少留个日志 + 弹窗
        tb = traceback.format_exc()
        try:
            root = paths._r()
            root.mkdir(parents=True, exist_ok=True)
            (root / "crash.log").write_text(tb, encoding="utf-8")
        except Exception:                # noqa: BLE001
            pass
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, "程序出错了，详情见数据目录里的 crash.log：\n\n" + tb[-1200:],
                "学习bot", 0x10)
        except Exception:                # noqa: BLE001
            pass
        raise
