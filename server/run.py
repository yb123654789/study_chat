#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""服务器入口：HTTP API（+ 应急时的 Telegram bot、定时推送），一个进程。

    python server/run.py --port 8787 --bind 0.0.0.0

默认**不跑 bot、不推送**（手机版 P0 起摘掉 Telegram，方案 §12 批一）；
要临时用回 Telegram，加 `--bot` 应急，推送窗口 07:00 / 21:30 也会一起恢复。

首次启动会在 data/server.json 里生成一个 token，桌面端要填同一个。
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "app"))
sys.path.insert(0, str(BASE / "tools"))

import api                      # noqa: E402
import db as dbmod              # noqa: E402
import paths                    # noqa: E402
from service import Service     # noqa: E402


def load_token(path: Path) -> str:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("token", "")
        except ValueError:
            pass
    token = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token}, ensure_ascii=False), encoding="utf-8")
    print(f"已生成新的访问 token（写进 {path}）：\n  {token}", flush=True)
    return token


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

    token = load_token(paths.server_config())
    svc = Service(d, push=use_bot)
    api.configure(d, svc, token)

    stop = threading.Event()
    if use_bot:
        threading.Thread(target=bot_keeper, args=(svc, stop), daemon=True,
                         name="bot-keeper").start()

    srv = api.serve(args.bind, args.port)
    print(f"学习bot 服务端已启动：http://{args.bind}:{args.port}  "
          f"db={d.path}", flush=True)
    if use_bot:
        print("应急模式：Telegram bot 与 07:00/21:30 推送已开启。", flush=True)
    else:
        print("Telegram 已摘：不起 bot、不推送。需要时加 --bot。", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        svc.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
