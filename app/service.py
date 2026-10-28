#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后台服务：Telegram 长轮询线程 + 每日维护线程（+ 应急的定时推送线程）。

界面只负责显示和按按钮，网络与定时都在这里。

手机版 P0 起 **默认不推送**：07:00 / 21:30 两条都取消了（方案 §12 批一）。
Telegram 只是留着应急（`server/run.py --bot`），默认不起 bot、不起推送线程。
"""
from __future__ import annotations

import threading
import time

from bot import Bot, TgClient, TgError
import engine
from db import now, today_s


class Service:
    def __init__(self, db, push: bool = False):
        """push=True 只在应急的 `--bot` 模式下用，恢复 07:00 / 21:30 推送。"""
        self.db = db
        self.push = bool(push)
        self.bot: Bot | None = None
        self.client: TgClient | None = None
        self.status = "stopped"          # stopped / running / error
        self.last_error = ""
        self.username = ""
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._offset = 0
        self._last_push_day = ""
        self._last_remind_day = ""
        self._missed_push = ""
        self._missed_remind = ""
        self._last_try = 0.0
        self._last_try_remind = 0.0
        self._auto_day = ""

    # ---------------------------------------------------------------- 状态
    @property
    def running(self) -> bool:
        return self.status == "running"

    def status_text(self) -> str:
        if self.status == "running":
            name = f"@{self.username}" if self.username else "bot"
            return f"{name} 运行中"
        if self.status == "error":
            return f"出错：{self.last_error[:60]}"
        return "未启动"

    # ---------------------------------------------------------------- 启停
    def start(self, quiet: bool = False) -> None:
        if self.running and self.bot:
            # 重复点「启动」不能再起一条长轮询，否则两条抢 getUpdates → 409
            self.db.log("Bot 已经在跑，忽略重复启动", "info")
            return
        token = self.db.get("tg_token", "").strip()
        if not token:
            raise RuntimeError("先填 Bot Token（在 Telegram 里找 @BotFather 要）")
        self.shutdown()          # 只停线程，别把「上次是开着」的标记清掉
        self.client = TgClient(token, proxy=self.db.get("tg_proxy", ""))
        try:
            me = self.client.get_me()
            self.username = me.get("username", "")
        except TgError as e:
            self.status = "error"
            self.last_error = str(e)
            if not quiet:        # 自动重试不刷日志，手动点「启动」才记
                self.db.log(f"Bot 连接失败：{e}", "err")
            raise RuntimeError(str(e)) from e

        self.bot = Bot(self.db, self.client)
        # 每次启动换一个新事件：旧线程拿到的是旧事件（已 set），不会被 clear 复活
        self._stop = threading.Event()
        stop = self._stop
        self.status = "running"
        self.db.set("bot_enabled", "1")
        self.db.log(f"Bot 已启动 @{self.username}", "ok")

        t1 = threading.Thread(target=self._poll_loop, args=(stop,), name="tg-poll", daemon=True)
        t2 = threading.Thread(target=self._daily_loop, args=(stop,), name="daily", daemon=True)
        threads = [t1, t2]
        if self.push:                # 只有应急 --bot 模式才恢复推送
            threads.append(threading.Thread(target=self._sched_loop, args=(stop,),
                                            name="scheduler", daemon=True))
        self._threads = threads
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self.shutdown()
        if self.db.get("bot_enabled") != "0":
            self.db.log("Bot 已停止", "info")
        self.db.set("bot_enabled", "0")

    def shutdown(self) -> None:
        """关窗口专用：只停线程，保留「上次是开着」的标记。

        P0 起桌面端不再自动重连；重连只发生在 `--bot` 模式的 bot-keeper 里。
        """
        was_running = self.status == "running"
        self._stop.set()
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=2.0)      # 轮询可能在 getUpdates 里等，超时就让它自己退
        self._threads = []
        self.status = "stopped"
        if was_running:
            self.db.log("程序退出，bot 线程已停", "info")

    # ---------------------------------------------------------------- 轮询
    def _poll_loop(self, stop: threading.Event) -> None:
        errors = 0
        while not stop.is_set():
            try:
                ups = self.client.updates(self._offset, timeout=20)
                errors = 0
                if self.status == "error":
                    self.status = "running"
                    self.last_error = ""
                for u in ups:
                    if stop.is_set():        # 已经要求停了，别再处理积压消息
                        break
                    self._offset = max(self._offset, int(u.get("update_id", 0)) + 1)
                    self.bot.handle_update(u)
            except TgError as e:
                errors += 1
                self.last_error = str(e)
                msg = str(e)
                if "409" in msg or "Conflict" in msg:
                    self.db.log("检测到另一个窗口也在跑这个 bot（409）。"
                                "关掉多余的窗口，只留一个，否则消息会被抢走。", "err")
                    self.status = "error"
                    stop.wait(30)
                    continue
                self.db.log(f"轮询失败（第 {errors} 次）：{e}", "err" if errors > 2 else "warn")
                self.status = "error" if errors >= 3 else self.status
                stop.wait(min(30, 2 * errors))
            except Exception as e:  # noqa: BLE001
                self.db.log(f"轮询异常：{type(e).__name__}: {e}", "err")
                stop.wait(5)

    # ---------------------------------------------------------------- 定时
    def _daily_loop(self, stop: threading.Event) -> None:
        """每天第一次 tick 只做「落后 → 自动重排」，不推送（Telegram 已摘）。"""
        while not stop.is_set():
            try:
                self._auto()
            except Exception as e:  # noqa: BLE001
                self.db.log(f"每日维护异常：{type(e).__name__}: {e}", "err")
            stop.wait(60)

    def _sched_loop(self, stop: threading.Event) -> None:
        """应急 `--bot` 模式的推送线程；手机版 P0 起默认不启动。"""
        while not stop.is_set():
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001
                self.db.log(f"定时任务异常：{type(e).__name__}: {e}", "err")
            stop.wait(20)

    @staticmethod
    def _minutes(hhmm: str) -> int:
        try:
            h, m = (int(x) for x in str(hhmm).split(":", 1))
            return h * 60 + m
        except (ValueError, AttributeError):
            return -1

    @classmethod
    def _in_window(cls, hhmm: str, start: str, hours: int) -> bool:
        """到点后 N 小时内算有效窗口。

        电脑 22:30 才开机时，不该把 07:00 的「今日任务」补发一遍——
        那不是提醒，是打扰。错过了就记一条日志，今天不补。
        """
        begin = cls._minutes(start)
        if begin < 0:
            begin = 7 * 60
        cur = cls._minutes(hhmm)
        return begin <= cur < begin + hours * 60

    def _auto(self) -> None:
        """每天第一次 tick 检查落后：落后就重排（重排自己会记日志、写当天推送说明）。"""
        day = today_s()
        if self._auto_day == day:
            return
        self._auto_day = day
        try:
            engine.auto_replan(self.db)
        except Exception as e:  # noqa: BLE001
            self.db.log(f"自动重排失败：{type(e).__name__}: {e}", "err")

    def _tick(self) -> None:
        if not self.bot:
            return
        self._auto()
        n = now()
        day = n.date().isoformat()
        clock = n.strftime("%H:%M")
        pushed = self.db.get("last_push", "")[:10]
        notified = self.db.get("last_remind", "")[:10]

        notify_at = self.db.get("notify_time", "07:00")
        remind_at = self.db.get("remind_time", "21:30")

        # 没绑定时别每 20 秒刷一条警告：失败后 30 分钟内不再重试
        if pushed != day:
            if self._in_window(clock, notify_at, 4) \
                    and n.timestamp() - self._last_try > 1800:
                self._last_try = n.timestamp()
                try:
                    if self.bot.push_today():
                        self.db.set("last_push", n.strftime("%Y-%m-%d %H:%M:%S"))
                except TgError as e:
                    self.db.log(f"推送失败：{e}", "warn")
            elif self._missed_push != day and self._minutes(clock) >= self._minutes(notify_at):
                self._missed_push = day
                self.db.log(f"已过 {notify_at} 的推送窗口（{clock}），今天不补发", "warn")

        if notified != day:
            if self._in_window(clock, remind_at, 2) \
                    and n.timestamp() - self._last_try_remind > 1800:
                self._last_try_remind = n.timestamp()
                try:
                    if self.bot.push_reminder():
                        self.db.set("last_remind", n.strftime("%Y-%m-%d %H:%M:%S"))
                    else:
                        self.db.set("last_remind", n.strftime("%Y-%m-%d %H:%M:%S"))
                except TgError as e:
                    self.db.log(f"提醒失败：{e}", "warn")
            elif self._missed_remind != day and self._minutes(clock) >= self._minutes(remind_at):
                self._missed_remind = day
                self.db.log(f"已过 {remind_at} 的提醒窗口（{clock}），今天不补发", "warn")
