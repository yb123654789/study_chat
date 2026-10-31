#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""服务器入口（VPS / 无桌面时用）：HTTP API + 应急 Telegram bot，一个进程。

    python server/run.py --port 8787 --bind 0.0.0.0

⚠️ **本机日常用不用它**：桌面软件的启动流程自己会起同一个服务
（`app/localserver.py`，端口 8787），手机同 WiFi 直接连。
这个脚本留给 VPS 部署 / 无头运行；两边是同一段代码。

默认**不跑 bot、不推送**（手机版 P0 起摘掉 Telegram）；要应急加 `--bot`。
首次启动会在数据目录的 server.json 里生成 token，桌面端和手机填同一个。
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "app"))
sys.path.insert(0, str(BASE / "tools"))

import db as dbmod              # noqa: E402
import paths                    # noqa: E402
from localserver import LocalServer  # noqa: E402
from service import Service     # noqa: E402


def bot_keeper(svc: Service, stop: threading.Event) -> None:
    """启动失败 / 掉线后自己重连，服务器没人看着也能恢复。"""
    while not stop.is_set():
        try:
            if svc.db.get("bot_enabled") == "1" and not svc.running:
                svc.start(quiet=True)
                svc.db.log(f"Bot 已启动 @{svc.username}（自动重连）", "ok")
        except Exception as e:  # noqa: BLE001
            svc.db.log(f"Bot 连接失败，60 秒后重试：{e}", "warn")
        stop.wait(60)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--bot", action="store_true",
                    help="应急：顺手把 Telegram bot 和 07:00/21:30 推送跑起来（默认不跑）")
    ap.add_argument("--no-bot", action="store_true",
                    help="（现在的默认行为，留着兼容老启动脚本）")
    ap.add_argument("--db", default="", help="默认 <数据目录>/studybot.db；也可用 STUDYBOT_HOME 指定数据目录")
    args = ap.parse_args()
    use_bot = bool(args.bot) and not args.no_bot

    paths.ensure_for_startup()
    d = dbmod.DB(args.db or paths.db_file())
    d.backup_before_upgrade()       # 结构要升级就先备一份
    d.init()
    try:
        d.auto_backup()
    except Exception as e:  # noqa: BLE001
        d.log(f"自动备份失败：{e}", "warn")
    if not d.goals():
        try:
            d.import_goals()
        except Exception as e:  # noqa: BLE001
            d.log(f"导入目标失败：{e}", "warn")

    svc = Service(d, push=use_bot)
    local = LocalServer(d, svc, port=args.port, bind=args.bind)
    if not local.start():
        print(f"服务没起来：{local.error}", flush=True)
        return 1

    stop = threading.Event()
    if use_bot:
        threading.Thread(target=bot_keeper, args=(svc, stop), daemon=True,
                         name="bot-keeper").start()

    print(f"学习bot 服务端已启动：{local.url}  db={d.path}", flush=True)
    print(f"token（手机/瘦客户端要填的）：{local.token}", flush=True)
    if use_bot:
        print("应急模式：Telegram bot 与 07:00/21:30 推送已开启。", flush=True)
    else:
        print("Telegram 已摘：不起 bot、不推送。需要时加 --bot。", flush=True)
    try:
        while not stop.is_set():
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        local.stop()
        svc.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
