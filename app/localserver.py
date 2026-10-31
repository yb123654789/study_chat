#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本机 HTTP 服务：桌面端自己在后台起一个 8787，手机同 WiFi 直接连。

以后不用再单独开终端跑 `python server/run.py`——只要桌面软件开着，服务就在
（`server/run.py` 留给 VPS 部署，走的是同一段代码）。

- token 存 `<数据目录>/server.json`，第一次自动生成，之后不变（删了才换）
- 端口被占用（比如旧进程还在）不会崩：`status` 变 error，界面能看见原因
- 只用标准库
"""
from __future__ import annotations

import json
import secrets
import socket
import sys
import threading
from pathlib import Path

import paths


def load_token(path: Path | None = None) -> str:
    """读/生成访问 token（写进 server.json，格式和以前一样：{"token": ...}）。"""
    p = Path(path or paths.server_config())
    if p.exists():
        try:
            tok = str(json.loads(p.read_text(encoding="utf-8")).get("token") or "")
            if tok:
                return tok
        except (OSError, ValueError):
            pass
    token = secrets.token_urlsafe(24)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"token": token}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass                          # 写不了也不挡服务（token 只在本次进程有效）
    return token


def lan_ip() -> str:
    """本机在局域网里的地址——手机要填的就是它。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.3)
            s.connect(("223.5.5.5", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def api_module():
    """server/api.py 在 server/ 目录里，按需加进 sys.path 再 import。"""
    d = paths.CODE_ROOT / "server"
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))
    import api  # noqa: PLC0415  延迟导入：起不来也只是这条服务没了
    return api


class LocalServer:
    """后台线程里的 HTTP 服务。start() 失败不抛，状态记在 status / error 里。"""

    def __init__(self, db, svc, port: int = 8787, bind: str = "0.0.0.0"):
        self.db = db
        self.svc = svc
        self.port = int(port)
        self.bind = bind
        self.token = load_token()
        self.status = "stopped"       # stopped / running / error
        self.error = ""
        self._http = None
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- 状态
    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def url(self) -> str:
        return f"http://{lan_ip()}:{self.port}"

    def status_text(self) -> str:
        if self.status == "running":
            return f"运行中 · {self.url}"
        if self.status == "error":
            return f"没起来：{self.error[:80]}"
        return "未启动"

    # ---------------------------------------------------------------- 启停
    def _port_busy(self) -> bool:
        """127.0.0.1:port 已经有人在监听？（旧的 run.py / 另一个桌面实例）

        Windows 上 SO_REUSEADDR 允许两个进程绑同一端口，绑得住但只有一个能收到连接，
        症状是手机时好时坏。所以先探一下，别抢。
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                return s.connect_ex(("127.0.0.1", self.port)) == 0
        except OSError:
            return False

    def start(self, quiet: bool = False) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        if self._port_busy():
            self.status = "error"
            self.error = f"端口 {self.port} 已被占用（旧的 server/run.py 或另一个实例在跑）"
            if not quiet:
                self.db.log(f"本机服务：{self.error}", "warn")
            return False
        try:
            api = api_module()
            api.configure(self.db, self.svc, self.token)
            self._http = api.serve(self.bind, self.port)
        except OSError as e:
            self.status, self.error = "error", f"端口 {self.port} 起不来（{e}）"
            if not quiet:
                self.db.log(f"本机服务：{self.error}", "warn")
            return False
        except Exception as e:        # noqa: BLE001
            self.status, self.error = "error", f"{type(e).__name__}: {e}"
            if not quiet:
                self.db.log(f"本机服务起不来：{self.error}", "warn")
            return False
        self._thread = threading.Thread(target=self._serve, daemon=True, name="local-api")
        self._thread.start()
        self.status = "running"
        return True

    def _serve(self) -> None:
        try:
            self._http.serve_forever()
        except Exception as e:        # noqa: BLE001  线程退出只影响手机，不影响桌面
            self.status, self.error = "error", f"服务线程退出：{e}"

    def stop(self) -> None:
        if self._http is not None:
            try:
                self._http.shutdown()
                self._http.server_close()
            except Exception:         # noqa: BLE001
                pass
            self._http = None
        self.status = "stopped"

    # ---------------------------------------------------------------- 自检
    def selfcheck(self, timeout: float = 1.5) -> dict:
        """自己连一下 /api/health，确认端口真通了（/api/health 不要 token）。"""
        if not self.running:
            return {"ok": False, "detail": self.status_text()}
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health",
                                        timeout=timeout) as r:
                return {"ok": True, "detail": r.read().decode("utf-8", "replace")[:80]}
        except Exception as e:        # noqa: BLE001
            return {"ok": False, "detail": f"{type(e).__name__}: {e}"}
