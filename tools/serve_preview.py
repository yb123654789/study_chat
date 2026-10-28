#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地预览服务：静态文件 + /api/chat 代理。

- 静态：web/
- GET  /api/health  -> {"ok": true, "model": "deepseek-flash", "key": "sk-****02bd"}
- POST /api/chat    -> {"text": "..."}

key 只从 .env 或环境变量读，永远不下发到前端。
用法：python tools/serve_preview.py [--port 8787]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WEB = BASE / "web"
ENV = BASE / ".env"
MODEL = "deepseek-flash"
API = "https://api.deepseek.com/chat/completions"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retrieval  # noqa: E402  按当前问题实时检索资料

SYSTEM = """你是这门课的学习带练，负责把一个 25~45 分钟的学习目标带用户真学会。

规则：
1. 先给结论，再给理由，一次回答不超过 6 行。用户问得具体就直接答，别绕。
2. 尽量用用户资料里的说法；引用原文时注明出处（资料名 + 页码/幻灯片）。
3. 每轮结尾用一句话检查他是否真懂（一个具体问题，别连着追问两个以上）。
4. 资料里没有的内容，直说「资料里没有」，可以补通用解释，但要标注「补充说明」。
5. 绝不输出日期、考试倒计时、进度数字——那些由程序算，你复述就会和数据库对不上。
6. 绝不说「你已经通过了 / 这个任务完成了」——是否通过只由小测判定。
7. 用户说「出题」时，只回一句「好，开始小测」，不要自己编题。
8. 用户明显没懂时，换一个更小的例子重讲，不要重复上一段话。"""


def load_env() -> None:
    if not ENV.exists():
        return
    for line in ENV.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def key_masked() -> str:
    k = os.environ.get("DEEPSEEK_API_KEY", "")
    return f"sk-****{k[-4:]}" if len(k) > 8 else ""


def context_block(goal: dict, reading: dict, hits: list[dict]) -> str:
    points = reading.get("points") or goal.get("cp") or []
    lines = [
        f"本节目标：{goal.get('title','')}",
        f"章节：{goal.get('ch','')}　预计 {goal.get('min','')} 分钟",
        "验收点：",
        *[f"- {p}" for p in points],
    ]
    if hits:
        lines.append("从学生资料里检索到的相关段落（回答时优先用这些，并注明《文件名》+页码）：")
        for e in hits:
            lines.append(f"- 《{e['src']}》{e['anchor']}：{e['text']}")
    else:
        lines.append("资料检索：无命中。只能讲通用知识，必须标注「补充说明」。")
    return "\n".join(lines)


def call_model(payload: dict) -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise RuntimeError("no key")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _json(self, obj: dict, code: int = 200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/health":
            return self._json({"ok": bool(os.environ.get("DEEPSEEK_API_KEY")),
                               "model": MODEL, "key": key_masked()})
        return super().do_GET()

    def do_POST(self):
        if self.path.split("?")[0] != "/api/chat":
            return self._json({"error": "not found"}, 404)

        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            return self._json({"error": f"bad request: {e}"}, 400)

        goal = req.get("goal") or {}
        reading = req.get("reading") or {}
        history = (req.get("history") or [])[-8:]
        text = (req.get("text") or "").strip()
        if not text:
            return self._json({"error": "empty"}, 400)

        hits = retrieval.search(goal, text, 3)
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": context_block(goal, reading, hits)}]
        for m in history:
            if m.get("role") in ("me", "bot") and m.get("text"):
                msgs.append({"role": "user" if m["role"] == "me" else "assistant",
                             "content": m["text"]})
        msgs.append({"role": "user", "content": text})

        try:
            answer = call_model({
                "model": MODEL,
                "messages": msgs,
                "max_tokens": 700,
                "temperature": 0.7,
                "stream": False,
            })
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            return self._json({"error": f"API {e.code}: {detail}"}, 502)
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 502)

        return self._json({
            "text": answer,
            "sources": [{"src": h["src"], "anchor": h["anchor"]} for h in hits],
        })


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--bind", default="127.0.0.1")
    args = ap.parse_args()

    load_env()
    handler = partial(Handler, directory=str(WEB))
    srv = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"http://127.0.0.1:{args.port}/  model={MODEL} key={key_masked() or '未配置'}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
