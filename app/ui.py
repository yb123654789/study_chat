#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面界面（Tkinter，零依赖）：所有后台都在这一个窗口里。"""
from __future__ import annotations

import datetime as dt
import json
import queue
import socket
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk, messagebox

import chat
import engine
import llm
import paths
from db import EVENTS, WEEK_CN, now, today_s

# ------------------------------------------------------------------ 配色
BG = "#eef1f6"
CARD = "#ffffff"
LINE = "#e2e7ee"
TEXT = "#1b2330"
MUTED = "#7a838f"
ACCENT = "#2f6fed"
ACCENT_D = "#2456bd"
SIDEBAR = "#1b2532"
SIDE_FG = "#aab5c4"
OK = "#1f9d55"
WARN = "#d97706"
ERR = "#d64545"

FONT = "Microsoft YaHei UI"


def F(size: int = 10, bold: bool = False) -> tuple:
    return (FONT, size, "bold" if bold else "normal")


# 换电脑时去哪儿下外部工具（设置页「外部工具」里一键打开）
DOWNLOADS = [
    ("tesseract（图片 OCR）", "https://github.com/UB-Mannheim/tesseract/wiki"),
    ("ffmpeg（抽音频）", "https://www.gyan.dev/ffmpeg/builds/"),
    ("whisper.cpp（转写）", "https://github.com/ggml-org/whisper.cpp/releases"),
    ("模型：中文 turbo", "https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"),
    ("模型：英文 small.en", "https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin"),
]


# ------------------------------------------------------------------ 小部件
def button(parent, text, cmd, kind="primary", width=None):
    palette = {
        "primary": (ACCENT, "#ffffff", ACCENT_D),
        "ghost": ("#f2f4f8", TEXT, "#e6eaf1"),
        "danger": ("#fdecec", ERR, "#f8d7d7"),
        "ok": ("#e8f7ee", OK, "#d5f0e0"),
    }
    bg, fg, active = palette.get(kind, palette["primary"])
    b = tk.Button(parent, text=text, command=cmd, font=F(10), bg=bg, fg=fg,
                  activebackground=active, activeforeground=fg, relief="flat", bd=0,
                  padx=14, pady=7, cursor="hand2", highlightthickness=0)
    if width:
        b.configure(width=width)
    return b


def entry(parent, width=26, show=None):
    return tk.Entry(parent, font=F(10), bg="#f7f9fc", fg=TEXT, relief="flat", bd=0,
                    insertbackground=TEXT, highlightthickness=1, highlightbackground=LINE,
                    highlightcolor=ACCENT, width=width, show=show or "")


def card(parent, pad=16):
    f = tk.Frame(parent, bg=CARD, highlightbackground=LINE, highlightthickness=1)
    inner = tk.Frame(f, bg=CARD)
    inner.pack(fill="both", expand=True, padx=pad, pady=pad)
    f.inner = inner
    return f


def label(parent, text, size=10, bold=False, fg=TEXT, bg=CARD, **kw):
    return tk.Label(parent, text=text, font=F(size, bold), fg=fg, bg=bg, **kw)


class ScrollHost(tk.Frame):
    """页面外壳：内容比窗口高时出现滚动条，滚轮能上下看（设置页 2100px 高，以前滚不到）。

    页面本身还是普通 Frame（各页的布局代码一行不用改），只是换了个「能滚的爹」。
    内层高度 = max(内容需要的高度, 窗口高度)：内容少时照样铺满；
    内容多时按内容高度铺，多出来的部分靠滚动。滚动条只在真需要时出现。
    """

    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0, takefocus=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self._sb_visible = False
        self.inner.bind("<Configure>", self._sync)
        self.canvas.bind("<Configure>", self._sync)

    def _sync(self, _ev=None) -> None:
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            return
        need = self.inner.winfo_reqheight()
        height = max(need, ch)
        self.canvas.itemconfigure(self._win, width=cw, height=height)
        self.canvas.configure(scrollregion=(0, 0, cw, height))
        show = need > ch + 1
        if show != self._sb_visible:        # 内容够高才挂滚动条，够不着时不留空条
            self._sb_visible = show
            if show:
                self.vsb.pack(side="right", fill="y", before=self.canvas)
            else:
                self.vsb.pack_forget()
                self.canvas.yview_moveto(0)

    def scrollable(self) -> bool:
        return self._sb_visible

    def wheel(self, delta: int) -> None:
        if not self._sb_visible or not delta:
            return
        steps = int(-delta / 120)
        if steps == 0:                      # 触摸板给的是小 delta，至少滚一行
            steps = -1 if delta > 0 else 1
        self.canvas.yview_scroll(steps, "units")


class App(tk.Tk):
    NAV = [("home", "总览"), ("study", "学习"), ("plan", "计划"), ("goals", "目标"),
           ("quiz", "题库"), ("materials", "资料"), ("knowledge", "知识库"),
           ("review", "待确认"), ("settings", "设置"), ("log", "日志")]

    def __init__(self, db, svc):
        super().__init__()
        self.db = db
        self.svc = svc
        self.title("学习bot · 后台")
        self.geometry("1200x800")
        self.minsize(1040, 700)
        self.configure(bg=BG)
        self._pages: dict[str, tk.Frame] = {}
        self._hosts: dict[str, "ScrollHost"] = {}
        self._nav_buttons: dict[str, tk.Button] = {}
        self._current = ""
        self._toast = None
        self._bg_tags: set[str] = set()

        self._style()
        self._layout()
        self.load_courses()
        self.show("home")
        self.refresh_key_hint()
        self.update_pending_badge()
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.after(250, self._pump)

    # ---------------------------------------------------------------- 样式
    def _style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                    rowheight=32, font=F(10), borderwidth=0)
        s.configure("Treeview.Heading", font=F(9, True), background="#f4f6f9",
                    foreground=MUTED, relief="flat", padding=(6, 8))
        s.map("Treeview", background=[("selected", "#dce8ff")],
              foreground=[("selected", TEXT)])
        s.configure("Accent.Horizontal.TProgressbar", troughcolor="#eef1f6",
                    background=ACCENT, borderwidth=0, thickness=8)
        s.configure("Vertical.TScrollbar", background="#cdd5e0", troughcolor=BG,
                    borderwidth=0, arrowcolor=MUTED)

    # ---------------------------------------------------------------- 布局
    def _layout(self):
        side = tk.Frame(self, bg=SIDEBAR, width=196)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        brand = tk.Frame(side, bg=SIDEBAR)
        brand.pack(fill="x", padx=20, pady=(22, 18))
        label(brand, "学习bot", 15, True, "#ffffff", SIDEBAR).pack(anchor="w")
        label(brand, "多课程 · 每日闭环", 9, False, "#6d7a8a", SIDEBAR).pack(anchor="w")

        for key, name in self.NAV:
            b = tk.Button(side, text=f"  {name}", font=F(10), anchor="w", bd=0,
                          relief="flat", bg=SIDEBAR, fg=SIDE_FG, activebackground="#2a3547",
                          activeforeground="#ffffff", pady=9, padx=8, cursor="hand2",
                          highlightthickness=0,
                          command=lambda k=key: self.show(k))
            b.pack(fill="x", padx=12, pady=1)
            self._nav_buttons[key] = b

        foot = tk.Frame(side, bg=SIDEBAR)
        foot.pack(side="bottom", fill="x", padx=20, pady=18)
        self.pending_dot = label(foot, "● 没有待确认", 9, False, "#8b94a1", SIDEBAR,
                                 cursor="hand2")
        self.pending_dot.pack(anchor="w")
        self.pending_dot.bind("<Button-1>", lambda _e: self.show("review"))

        right = tk.Frame(self, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        head = tk.Frame(right, bg=BG)
        head.pack(fill="x", padx=26, pady=(20, 6))
        self.page_title = label(head, "", 17, True, TEXT, BG)
        self.page_title.pack(side="left")
        self.course_var = tk.StringVar(value="")
        self.course_box = ttk.Combobox(head, textvariable=self.course_var, width=16,
                                       state="readonly", font=F(9))
        self.course_box.pack(side="left", padx=(16, 0))
        self.course_box.bind("<<ComboboxSelected>>", self._course_changed)
        self.header_right = tk.Frame(head, bg=BG)
        self.header_right.pack(side="right")

        self.body = tk.Frame(right, bg=BG)
        self.body.pack(fill="both", expand=True, padx=26, pady=(4, 10))

        bar = tk.Frame(right, bg=CARD, height=34, highlightbackground=LINE, highlightthickness=1)
        bar.pack(fill="x", side="bottom")
        self.status = label(bar, "", 9, False, MUTED, CARD)
        self.status.pack(side="left", padx=14)
        # 没配模型 key 时这里的提示常驻：只影响聊天/出题，不挡看计划
        self.key_hint = label(bar, "", 9, False, WARN, CARD, cursor="hand2")
        self.key_hint.pack(side="right", padx=14)
        self.key_hint.bind("<Button-1>", lambda _e: self.show("settings"))

    # ---------------------------------------------------------------- 课程
    def courses(self) -> list[dict]:
        try:
            return self.db.courses()
        except Exception:              # 远端断了也不能让界面起不来
            return []

    def course_id(self) -> str:
        return getattr(self, "_cid", "") or "ds"

    def course_name(self, cid: str | None = None) -> str:
        cid = cid or self.course_id()
        for c in self.courses():
            if c["id"] == cid:
                return c.get("name") or cid
        return cid

    def load_courses(self) -> None:
        rows = self.courses()
        self._course_rows = rows
        names = [c.get("name") or c["id"] for c in rows]
        self.course_box.configure(values=names)
        try:
            cur = self.db.current_course() if rows else "ds"
        except Exception:
            cur = "ds"
        self._cid = cur
        for i, c in enumerate(rows):
            if c["id"] == cur:
                self.course_var.set(names[i])
                break

    def _course_changed(self, _ev=None) -> None:
        i = self.course_box.current()
        rows = getattr(self, "_course_rows", [])
        if not (0 <= i < len(rows)):
            return
        self._cid = rows[i]["id"]
        try:
            self.db.set("course", self._cid)
        except Exception:
            pass
        self.show(self._current)       # 页头按钮和内容都可能跟课程走，整页重建

    # ---------------------------------------------------------------- 页面
    def show(self, key: str):
        if key not in self._pages:
            self._pages[key] = self._build_page(key)
        for k, b in self._nav_buttons.items():
            b.configure(bg=ACCENT if k == key else SIDEBAR,
                        fg="#ffffff" if k == key else SIDE_FG)
        for k, h in self._hosts.items():
            if k != key:
                h.pack_forget()
        page = self._pages[key]
        self._hosts[key].pack(fill="both", expand=True)
        self._current = key
        titles = {"home": "总览", "study": "学习 · 带练", "plan": "学习计划",
                  "goals": "学习目标", "quiz": "题库", "materials": "资料",
                  "knowledge": "知识库 · 二级数据", "review": "待确认 · 手机与电脑不一致",
                  "settings": "设置", "log": "日志"}
        self.page_title.configure(text=titles.get(key, key))
        for w in self.header_right.winfo_children():
            w.destroy()
        if hasattr(page, "header"):
            page.header(self.header_right)  # type: ignore[attr-defined]
        if hasattr(page, "refresh"):
            page.refresh()  # type: ignore[attr-defined]
        # 页面刚建好时子控件还没排完版，reqheight 还是旧的；等这一轮布局结束再量一次滚动区
        self.after_idle(self._hosts[key]._sync)

    def _build_page(self, key: str) -> tk.Frame:
        host = ScrollHost(self.body)
        page = {
            "home": HomePage, "study": StudyPage, "plan": PlanPage, "goals": GoalsPage,
            "quiz": QuizPage, "materials": MaterialsPage, "knowledge": KnowledgePage,
            "review": ReviewPage, "settings": SettingsPage, "log": LogPage,
        }[key](host.inner, self)
        page.pack(fill="both", expand=True)
        self._hosts[key] = host
        return page

    def _on_wheel(self, ev):
        """鼠标滚轮滚当前页。指针在 Text/Treeview 里时交给它们自己滚（聊天框、表格）。"""
        try:
            if ev.widget.winfo_toplevel() is not self:   # 小测弹窗上的滚轮别滚到后面那页
                return
        except Exception:  # noqa: BLE001
            return
        host = self._hosts.get(self._current)
        if host is None or not host.scrollable():
            return
        if isinstance(ev.widget, (tk.Text, tk.Listbox, tk.Canvas, ttk.Treeview,
                                  ttk.Combobox)):
            return
        host.wheel(int(getattr(ev, "delta", 0) or 0))

    # ---------------------------------------------------------------- 工具
    def toast(self, text: str, level: str = "info"):
        color = {"info": MUTED, "ok": OK, "warn": WARN, "err": ERR}.get(level, MUTED)
        self.status.configure(text=f"{now().strftime('%H:%M:%S')}  {text}", fg=color)
        self.db.log(text, level)

    def report_callback_exception(self, exc, val, tb):
        """按钮回调里的异常别只往 stderr 扔。

        打包后没有控制台，Tk 的默认处理等于「点了没反应」——
        2026-10-31「点开始拆没反应」就是 ui.py 少了 `from pathlib import Path` 的
        NameError 被这么吞掉的。现在一律：写日志 + 状态栏 + 弹窗。
        """
        import traceback
        detail = "".join(traceback.format_exception(exc, val, tb))
        try:
            self.db.log(detail[-1500:], "err")
        except Exception:  # noqa: BLE001  远端断线时别在错误处理里再炸一次
            pass
        try:
            self.toast(f"界面出错：{val}", "err")
        except Exception:  # noqa: BLE001
            pass
        try:
            messagebox.showerror("界面出错",
                                 f"{type(val).__name__}: {val}\n\n"
                                 "详情已写进「日志」页，报告时把这段贴出来就行。")
        except Exception:  # noqa: BLE001
            pass

    def run_bg(self, fn, done=None, fail=None, tag: str = ""):
        """后台跑一个动作。给 tag 就防连点：同一件事没跑完再点直接忽略。"""
        if tag:
            if tag in self._bg_tags:
                self.toast("上一次还没跑完，别重复点", "warn")
                return
            self._bg_tags.add(tag)

        def finish():
            if tag:
                self._bg_tags.discard(tag)

        def worker():
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001
                self.after(0, finish)
                self.after(0, lambda: (fail or self._default_fail)(e))
                return
            self.after(0, finish)
            if done:
                self.after(0, lambda: done(res))
        threading.Thread(target=worker, daemon=True).start()

    def _default_fail(self, e):
        self.toast(f"{type(e).__name__}: {e}", "err")
        messagebox.showerror("出错了", f"{type(e).__name__}: {e}")

    def refresh_current(self):
        page = self._pages.get(self._current)
        if page is not None and hasattr(page, "refresh"):
            page.refresh()  # type: ignore[attr-defined]

    def open_study(self, task_id: int):
        """总览里点「去学」：切到学习页并选中这张卡。"""
        self.show("study")
        page = self._pages.get("study")
        t = self.db.task(int(task_id))
        if page is not None and t is not None:
            page.select(t)  # type: ignore[attr-defined]

    # ---------------------------------------------------------------- 心跳
    def _pump(self):
        # 当前页内容变了（任务列表、日志、处理进度）要重新量一次，滚动条才跟着出现/消失
        host = self._hosts.get(self._current)
        if host is not None:
            host._sync()
        # 日志总线 → 当前页面刷新 / 日志页追加
        lines = []
        while True:
            try:
                lines.append(EVENTS.get_nowait())
            except Exception:  # noqa: BLE001
                break
        if lines:
            log_page = self._pages.get("log")
            if log_page is not None and hasattr(log_page, "append"):
                for ts, level, text in lines:
                    log_page.append(ts, level, text)  # type: ignore[attr-defined]
        # 待确认条数 + 模型 key 提示：不用每秒查，5 秒左右刷一次就够了
        self._tick_n = getattr(self, "_tick_n", 0) + 1
        if self._tick_n % 12 == 0:
            self.refresh_key_hint()
            self.update_pending_badge()
        self.after(400, self._pump)

    def refresh_key_hint(self) -> None:
        """没 key 就在状态栏右侧提示一句；提示不挡任何页面。"""
        try:
            text = llm.key_hint(self.db)
        except Exception:            # 远端断线时别在状态栏刷红字
            return
        self.key_hint.configure(text=(text + "（点这里去「设置」填）") if text else "")

    def update_pending_badge(self, n: int | None = None) -> None:
        """左下角：有几条待确认没处理。点一下去那一页。"""
        if n is None:
            try:
                n = int(self.db.review_pending_count())
            except Exception:        # 远端断线时保持原样
                return
        if n:
            self.pending_dot.configure(text=f"● 待确认 {n} 条", fg="#fbbf24")
        else:
            self.pending_dot.configure(text="● 没有待确认", fg="#8b94a1")

    def _on_close(self):
        try:
            self.svc.shutdown()
        finally:
            self.destroy()


# ------------------------------------------------------------------ 总览
class HomePage(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self.db = app.db
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x")
        self.stats_frame = tk.Frame(top, bg=BG)
        self.stats_frame.pack(fill="x")

        mid = tk.Frame(self, bg=BG)
        mid.pack(fill="both", expand=True, pady=(14, 0))

        # 右侧固定列先占位（side=right），左边再吃掉剩余空间，否则窄窗口会把右列挤出去
        right = tk.Frame(mid, bg=BG, width=260)
        right.pack(side="right", fill="y", padx=(14, 0))
        right.pack_propagate(False)

        left = card(mid)
        left.pack(side="left", fill="both", expand=True)
        label(left.inner, "今日任务", 12, True).pack(anchor="w")
        self.sub = label(left.inner, "", 9, False, MUTED)
        self.sub.pack(anchor="w", pady=(2, 10))
        self.task_box = tk.Frame(left.inner, bg=CARD)
        self.task_box.pack(fill="both", expand=True)
        self.progress_card = card(right)
        self.progress_card.pack(fill="x")
        label(self.progress_card.inner, "总进度", 11, True).pack(anchor="w")
        self.pbar = ttk.Progressbar(self.progress_card.inner, style="Accent.Horizontal.TProgressbar",
                                    maximum=100, value=0)
        self.pbar.pack(fill="x", pady=(10, 6))
        self.ptext = label(self.progress_card.inner, "", 9, False, MUTED)
        self.ptext.pack(anchor="w")

        self.quick = card(right)
        self.quick.pack(fill="x", pady=(14, 0))
        label(self.quick.inner, "快捷操作", 11, True).pack(anchor="w", pady=(0, 10))
        button(self.quick.inner, "重新排计划", self.replan).pack(fill="x", pady=3)
        button(self.quick.inner, "看待确认", self.show_review, "ghost").pack(fill="x", pady=3)
        button(self.quick.inner, "扫描资料入库", self.ingest, "ghost").pack(fill="x", pady=3)

    def header(self, parent):
        button(parent, "重新排计划", self.replan).pack(side="right")

    def refresh(self):
        s = self.db.stats()
        try:
            per = self.db.stats_by_course()
        except Exception:
            per = []
        for w in self.stats_frame.winfo_children():
            w.destroy()
        nearest = min((p["days_left"] for p in per), default=s["days_left"])
        cards = [
            ("最近的考试", f"{nearest}", f"天 · {len(per) or 1} 门课"),
            ("今日任务", f"{s['today_done']}/{s['today_total']}", "张已完成"),
            ("目标通过", f"{s['done_goals']}/{s['total_goals']}", "个"),
            ("连续", f"{s['streak']}", "天有完成"),
        ]
        for i, (k, v, sub) in enumerate(cards):
            c = card(self.stats_frame, pad=14)
            c.pack(side="left", fill="both", expand=True, padx=(0 if i == 0 else 10, 0))
            label(c.inner, k, 9, False, MUTED).pack(anchor="w")
            label(c.inner, v, 22, True).pack(anchor="w", pady=(2, 0))
            label(c.inner, sub, 8, False, MUTED).pack(anchor="w")

        pct = round(100 * s["done_goals"] / max(1, s["total_goals"]))
        self.pbar.configure(value=pct)
        self.ptext.configure(text=f"{pct}% · 还剩 {s['total_goals'] - s['done_goals']} 个目标")

        for w in self.task_box.winfo_children():
            w.destroy()
        tasks = [t for t in self.db.today_tasks() if t["state"] != "dropped"]
        per_txt = "　".join(f"{self.app.course_name(p['course'])} {p['today_done']}/{p['today_total']}"
                            for p in per) if per else ""
        self.sub.configure(text=(f"{s['today_total']} 张 · "
                                 f"{sum(t['minutes'] for t in tasks)} 分钟 · "
                                 f"已完成 {s['today_minutes']} 分钟") + (f"\n{per_txt}" if per_txt else ""))
        if not tasks:
            label(self.task_box, "今天没有排任务。点右上角「重新排计划」。", 10, False, MUTED).pack(
                anchor="w", pady=16)
            return
        groups: dict[str, list[dict]] = {}
        for t in tasks:
            groups.setdefault(t.get("course") or "ds", []).append(t)
        for cid, items in groups.items():
            head = tk.Frame(self.task_box, bg=CARD)
            head.pack(fill="x", pady=(8, 2))
            label(head, self.app.course_name(cid), 10, True, ACCENT, CARD).pack(side="left")
            label(head, f"　{sum(t['minutes'] for t in items)} 分钟", 8, False, MUTED,
                  CARD).pack(side="left")
            for t in items:
                self._row(t)

    def _row(self, t):
        row = tk.Frame(self.task_box, bg=CARD)
        row.pack(fill="x", pady=1)
        done = t["state"] == "done"
        color = OK if done else (MUTED if t["state"] == "skipped" else ACCENT)
        tk.Frame(row, bg=color, width=3).pack(side="left", fill="y", padx=(0, 10))
        txt = tk.Frame(row, bg=CARD)
        txt.pack(side="left", fill="x", expand=True)
        label(txt, t["title"], 10, done, MUTED if done else TEXT).pack(anchor="w")
        label(txt, f"{t['kind']} · {t['minutes']} 分钟 · {t['goal_id']}", 8, False,
              MUTED).pack(anchor="w")
        acts = tk.Frame(row, bg=CARD)
        acts.pack(side="right")
        button(acts, "去学", lambda tid=t["id"]: self.app.open_study(tid)).pack(side="left", padx=2)
        if not done:
            button(acts, "强制通过", lambda tid=t["id"]: self.mark(tid), "ghost").pack(
                side="left", padx=2)
            button(acts, "跳过", lambda tid=t["id"]: self.skip(tid), "ghost").pack(
                side="left", padx=2)
        else:
            label(acts, "已完成", 9, False, OK).pack(padx=8)
        tk.Frame(self.task_box, bg=LINE, height=1).pack(fill="x")

    # --- 动作
    def mark(self, tid):
        if not messagebox.askyesno("确认", "绕过小测直接标记为完成？\n正常流程是去「学习」页答对 2 题。"):
            return
        self.db.set_task_state(tid, "done")
        self.db.log(f"手动标记完成 task#{tid}", "warn")
        self.app.toast("已标记完成", "ok")
        self.refresh()

    def skip(self, tid):
        self.db.set_task_state(tid, "skipped")
        self.app.toast("已跳过", "info")
        self.refresh()

    def replan(self):
        def work():
            return engine.regenerate(self.db)
        self.app.run_bg(work, lambda r: (self.app.toast(r["note"], "ok"), self.refresh()), tag="replan")

    def show_review(self):
        self.app.show("review")

    def ingest(self):
        self.app.toast("开始扫描 materials/inbox …", "info")
        self.app.run_bg(lambda: engine.ingest_inbox(self.db),
                        lambda r: self.app.toast(
                            f"入库完成：新增 {r['new']}，跳过 {r['skipped']}，失败 {r['failed']}",
                            "ok" if r["failed"] == 0 else "warn"), tag="ingest")


# ------------------------------------------------------------------ 学习（带练）
class StudyPage(tk.Frame):
    """左边今日任务，右边带练对话。消息落库，Telegram 那边接得上同一段。"""

    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self.db = app.db
        self.task = None
        self.conv = None

        left = tk.Frame(self, bg=BG, width=340)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        lc = card(left)
        lc.pack(fill="x")
        label(lc.inner, "今日任务", 11, True).pack(anchor="w")
        self.task_box = tk.Frame(lc.inner, bg=CARD)
        self.task_box.pack(fill="x", pady=(8, 0))
        label(left, "点一张卡进对话。说「出题」或按右下角小测，3 题对 2 题才算完成。",
              9, False, MUTED, BG, wraplength=315, justify="left").pack(anchor="w", pady=(10, 0))

        right = card(self)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))
        head = tk.Frame(right.inner, bg=CARD)
        head.pack(fill="x")
        self.title_lbl = label(head, "还没选卡", 12, True, TEXT, CARD, wraplength=760,
                               justify="left", anchor="w")
        self.title_lbl.pack(fill="x")
        self.state_lbl = label(head, "", 9, False, MUTED)
        self.state_lbl.pack(anchor="w", pady=(3, 0))

        wrap = tk.Frame(right.inner, bg=CARD)
        wrap.pack(fill="both", expand=True, pady=(10, 8))
        self.txt = tk.Text(wrap, font=F(10), bg="#fbfcfe", fg=TEXT, relief="flat", bd=0,
                           wrap="word", padx=14, pady=10, highlightthickness=0,
                           spacing1=2, spacing3=2, height=12)   # 12 行就够：多出来的高度靠 expand，别把下面的输入框挤出窗口
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.txt.tag_configure("me", foreground=ACCENT, font=F(10, True))
        self.txt.tag_configure("bot", foreground=TEXT)
        self.txt.tag_configure("src", foreground=MUTED, font=F(8))
        self.txt.tag_configure("err", foreground=ERR)
        self.txt.tag_configure("sys", foreground=MUTED)
        self.txt.configure(state="disabled")

        bottom = tk.Frame(right.inner, bg=CARD)
        bottom.pack(fill="x")
        self.input = tk.Text(bottom, font=F(10), bg="#f7f9fc", fg=TEXT, relief="flat", bd=0,
                             height=3, wrap="word", padx=10, pady=8, insertbackground=TEXT,
                             highlightthickness=1, highlightbackground=LINE,
                             highlightcolor=ACCENT)
        self.input.pack(fill="x")
        self.input.bind("<Return>", self._on_return)
        row = tk.Frame(bottom, bg=CARD)
        row.pack(fill="x", pady=(8, 0))
        button(row, "清空对话", self.clear, "ghost").pack(side="left")
        self.btn_quiz = button(row, "小测 3 题", self.quiz, "ghost")
        self.btn_quiz.pack(side="right")
        self.btn_send = button(row, "发送", self.send)
        self.btn_send.pack(side="right", padx=8)

    def header(self, parent):
        button(parent, "小测 3 题", self.quiz, "ghost").pack(side="right")

    # --- 列表
    def refresh(self):
        cid = self.app.course_id()
        if self.task and (self.task.get("course") or "ds") != cid:
            self.task, self.conv = None, None      # 切了课程，别把别的课的卡留在右边
        tasks = self.db.today_tasks(cid)
        for w in self.task_box.winfo_children():
            w.destroy()
        if not tasks:
            label(self.task_box, f"{self.app.course_name(cid)} 今天没有排任务。",
                  10, False, MUTED).pack(anchor="w", pady=8)
        for t in tasks:
            self._row(t)
        if self.task:
            self.task = self.db.task(int(self.task["id"])) or self.task
        elif tasks:
            self.task = tasks[0]
            self.conv = chat.conversation(self.db, tasks[0])
            self._render()
        self._head()

    def _row(self, t):
        bg = "#f2f7ff" if self.task and t["id"] == self.task["id"] else CARD
        row = tk.Frame(self.task_box, bg=bg, cursor="hand2")
        row.pack(fill="x", pady=1)
        mark = {"done": "✅", "skipped": "⏭"}.get(t["state"], "▶")
        lb = label(row, f"{mark} {t['title']}", 10, False, TEXT, bg, wraplength=290,
                   justify="left", anchor="w")
        lb.pack(anchor="w", padx=8, pady=(6, 0))
        sub = label(row, f"{t['kind']} · {t['minutes']} 分钟", 8, False, MUTED, bg)
        sub.pack(anchor="w", padx=8, pady=(0, 6))
        for w in (row, lb, sub):
            w.bind("<Button-1>", lambda e, tt=t: self.select(tt))

    def select(self, t):
        self.task = self.db.task(int(t["id"])) or t
        self.conv = chat.conversation(self.db, self.task)
        self.refresh()
        self._render()

    # --- 对话
    def _head(self):
        if not self.task:
            self.title_lbl.configure(text="还没选卡")
            self.state_lbl.configure(text="")
            return
        self.title_lbl.configure(text=self.task["title"])
        self.state_lbl.configure(text=chat.state_bar(self.db, self.task))

    def _render(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")
        if not self.conv:
            return
        msgs = chat.history(self.db, self.conv, 200)
        if not msgs:
            self._bubble("sys", "还没聊过。可以先问「这节考什么」，或者说说你卡在哪。")
        for m in msgs:
            if m["role"] == "me":
                self._bubble("me", m["text"])
            elif m["role"] == "bot":
                self._bubble("bot", m["text"], m.get("sources"))
            elif m["role"] == "tool":
                self._bubble("sys", m["text"])

    def _bubble(self, role: str, text: str, sources=None):
        self.txt.configure(state="normal")
        who = {"me": "你", "bot": "带练", "err": "系统", "sys": "系统"}.get(role, role)
        tag = {"me": "me", "bot": "bot", "err": "err"}.get(role, "sys")
        self.txt.insert("end", f"{who}　", tag)
        self.txt.insert("end", text + "\n")
        if sources:
            ref = "、".join(f"《{s['src'][:28]}》{s['anchor']}" for s in sources[:2])
            self.txt.insert("end", f"　参考：{ref}\n", "src")
        self.txt.insert("end", "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _on_return(self, e):
        if e.state & 0x1:          # Shift+Enter 换行
            return None
        self.send()
        return "break"

    def send(self):
        if not self.conv or not self.task:
            return self.app.toast("先在左边点一张卡", "warn")
        text = self.input.get("1.0", "end").strip()
        if not text:
            return
        self.input.delete("1.0", "end")
        self._bubble("me", text)
        self.btn_send.configure(state="disabled")
        conv, db = self.conv, self.db
        self.app.run_bg(lambda: chat.reply(db, conv, text, handlers=self._tools()),
                        self._on_reply, self._on_fail, tag="chat")

    def _on_reply(self, res):
        self.btn_send.configure(state="normal")
        tool = res.get("tool") or ""
        if tool and not res.get("handled"):
            # 服务器端已经改了状态（生成小测 / 跳过 / 计划），这里只把界面跟上
            if tool == "start_quiz":
                self.quiz()
            elif tool == "show_plan":
                self.app.show("plan")
        if not tool:
            self._bubble("bot", res["text"], res["sources"])
        self.refresh()

    def _on_fail(self, e):
        self.btn_send.configure(state="normal")
        self._bubble("err", f"模型没连上：{e}")
        self.app.toast(f"对话失败：{e}", "err")

    def _tools(self):
        """模型要动状态只能走这里（在后台线程里被调用，界面动作要 after 回主线程）。"""
        def run(name, args):
            if name == "start_quiz":
                self.app.after(0, self.quiz)
                return "小测已打开：3 题，答对 2 题算过。"
            if name == "skip_task":
                self.db.set_task_state(int(self.task["id"]), "skipped")
                self.app.after(0, self.refresh)
                return "已跳过这张卡，明天重排会挪回来。"
            if name == "show_plan":
                self.app.after(0, lambda: self.app.show("plan"))
                return "已打开计划页。"
            return "这个动作现在做不了"
        return run

    # --- 小测
    def quiz(self):
        if not self.task:
            return self.app.toast("先在左边点一张卡", "warn")
        tid = int(self.task["id"])
        gid = self.task["goal_id"]
        self.app.toast("正在准备小测…", "info")
        self.app.run_bg(lambda: engine.ensure_quiz(self.db, gid),
                        lambda qs: self._open_quiz(tid, qs),
                        lambda e: self.app.toast(f"出题失败：{e}", "err"), tag="quiz")

    def _open_quiz(self, tid, qs):
        t = self.db.task(tid)
        if not t:
            return self.app.toast("这张卡被重排换掉了", "warn")
        QuizDialog(self, t, qs)

    def quiz_regen(self, tid):
        t = self.db.task(tid)
        if not t:
            return
        if not engine.regen_allowed(self.db, t["goal_id"]):
            return self.app.toast("这个点 24 小时内换太多次了，明天再来", "warn")
        gid = t["goal_id"]
        self.app.run_bg(lambda: engine.ensure_quiz(self.db, gid, force_new=True),
                        lambda qs: self._open_quiz(tid, qs),
                        lambda e: self.app.toast(f"出题失败：{e}", "err"), tag="quiz")

    def clear(self):
        if not self.conv:
            return
        if not messagebox.askyesno("确认", "清空这条对话？只删聊天记录，任务和小测记录不受影响。"):
            return
        chat.reset(self.db, self.conv)
        self._render()


class QuizDialog(tk.Toplevel):
    """桌面端 3 题小测：判分和记账跟 Telegram 走同一个 engine。"""

    def __init__(self, page: StudyPage, task: dict, qs: list[dict]):
        super().__init__(page.app)
        self.page = page
        self.app = page.app
        self.db = page.app.db
        self.task = task
        self.qs = qs
        self.idx = 0
        self.right = 0

        self.title(f"小测 · {task['title'][:28]}")
        self.geometry("720x560")
        self.minsize(620, 480)
        self.configure(bg=BG)
        self.transient(page.app)
        self.grab_set()

        c = card(self)
        c.pack(fill="both", expand=True, padx=14, pady=14)
        self.count = label(c.inner, "", 9, False, MUTED)
        self.count.pack(anchor="w")
        self.stem = label(c.inner, "", 12, True, TEXT, CARD, wraplength=650, justify="left",
                          anchor="w")
        self.stem.pack(fill="x", pady=(6, 10))
        self.body = tk.Frame(c.inner, bg=CARD)
        self.body.pack(fill="both", expand=True)
        self.fb = label(c.inner, "", 10, False, MUTED, CARD, wraplength=650, justify="left",
                        anchor="w")
        self.fb.pack(fill="x", pady=(8, 0))
        row = tk.Frame(c.inner, bg=CARD)
        row.pack(fill="x", pady=(10, 0))
        button(row, "关闭", self.destroy, "ghost").pack(side="left")
        self.btn = button(row, "提交", self.submit)
        self.btn.pack(side="right")

        self.var = tk.StringVar(value="-1")
        self.ans: tk.Text | None = None
        self.grade_tag = "quiz-grade"
        self.show_question()

    def show_question(self):
        q = self.qs[self.idx]
        self.count.configure(text=f"第 {self.idx + 1}/{len(self.qs)} 题 · "
                                  f"{'选择' if q['type'] == 'choice' else '简答'}")
        self.stem.configure(text=q["stem"])
        for w in self.body.winfo_children():
            w.destroy()
        self.ans = None
        if q["type"] == "choice":
            self.var.set("-1")
            for i, opt in enumerate(q["options"]):
                tk.Radiobutton(self.body, text=f"{'ABCDEFGH'[i]}. {opt}", value=str(i),
                               variable=self.var, font=F(10), bg=CARD, fg=TEXT,
                               activebackground=CARD, anchor="w", justify="left",
                               wraplength=620, padx=4, pady=5, selectcolor="#ffffff",
                               highlightthickness=0).pack(fill="x", anchor="w")
        else:
            self.ans = tk.Text(self.body, height=5, font=F(10), bg="#f7f9fc", fg=TEXT,
                               relief="flat", bd=0, wrap="word", padx=10, pady=8,
                               highlightthickness=1, highlightbackground=LINE,
                               highlightcolor=ACCENT)
            self.ans.pack(fill="both", expand=True)
            self.ans.insert("1.0", "")
        self.fb.configure(text="", fg=MUTED)
        self.btn.configure(text="提交", command=self.submit, state="normal")

    def _answer(self) -> str:
        q = self.qs[self.idx]
        if q["type"] == "choice":
            return self.var.get()
        return self.ans.get("1.0", "end").strip() if self.ans else ""

    def submit(self):
        q = self.qs[self.idx]
        answer = self._answer()
        if not answer or answer == "-1":
            return self.app.toast("先作答再提交", "warn")
        self.btn.configure(state="disabled", text="判分中…")
        goal = self.db.goal(self.task["goal_id"]) or {}
        self.app.run_bg(lambda: engine.grade_one(self.db, goal, q, answer),
                        lambda res: self._graded(q, answer, res),
                        self._grade_failed, tag=self.grade_tag)

    def _grade_failed(self, e):
        if not self.winfo_exists():
            return
        self.btn.configure(state="normal", text="提交")
        self.fb.configure(text=f"判分失败：{e}", fg=ERR)

    def _graded(self, q: dict, answer: str, res: dict):
        if not self.winfo_exists():      # 判分途中被关掉了
            return
        self.db.add_attempt(int(self.task["id"]), self.task["goal_id"], q["id"], answer,
                            1 if res["pass"] else 0, res.get("feedback", ""))
        if res["pass"]:
            self.right += 1
        self.fb.configure(text=self._feedback(q, res),
                          fg=OK if res["pass"] else ERR)
        last = self.idx + 1 >= len(self.qs)
        self.btn.configure(text="看结果" if last else "下一题",
                           command=self.next_q, state="normal")

    def _feedback(self, q: dict, res: dict) -> str:
        if q["type"] == "choice":
            if res["pass"]:
                return "✅ 对"
            try:
                i = int(q["answer"])
                return f"❌ 不对。正确答案是 {'ABCDEFGH'[i]}. {q['options'][i]}"
            except (ValueError, IndexError):
                return "❌ 不对"
        extra = res.get("feedback") or ""
        missed = res.get("missed") or []
        if not res["pass"] and missed:
            extra = (extra + "；" if extra else "") + "缺：" + "；".join(missed[:2])
        return ("✅ 答到要点了" if res["pass"] else "❌ 没答到要点") + ("\n" + extra if extra else "")

    def next_q(self):
        self.idx += 1
        if self.idx >= len(self.qs):
            return self.finish()
        self.show_question()

    def finish(self):
        right, total = self.right, len(self.qs)
        if right >= 2:
            self.db.set_task_state(int(self.task["id"]), "done")
            self.db.log(f"小测通过 {self.task['goal_id']}（{right}/{total}）", "ok")
            self.app.toast(f"通过（{right}/{total}），这张卡记为完成", "ok")
            self.destroy()
            self.page.refresh()
            self.page._render()
            return
        info = engine.requeue_after_fail(self.db, self.task, right, total)
        streak = int(info.get("streak") or 0)
        for w in self.body.winfo_children():
            w.destroy()
        self.stem.configure(text=f"没过：{right}/{total}")
        if streak >= 2:
            self.fb.configure(text="连续两次都没过，别再换题了。明天排了一次回炉（先讲懂再考）。",
                              fg=ERR)
            self.btn.configure(text="关掉", command=self.destroy, state="normal")
            return
        tip = (f"这张卡不算完成。明天（{info['date']}）已排一张错题重做，"
               "也可以现在换一套再考。") if info.get("added") else \
            "这张卡不算完成。明天重排会补进来，也可以现在换一套再考。"
        self.fb.configure(text=tip, fg=ERR)
        self.btn.configure(text="换一套题", command=self.retry, state="normal")

    def retry(self):
        tid = int(self.task["id"])
        self.destroy()
        self.page.quiz_regen(tid)


# ------------------------------------------------------------------ 计划
class PlanPage(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self.db = app.db

        note = card(self, pad=12)
        note.pack(fill="x")
        self.note = label(note.inner, "", 9, False, MUTED)
        self.note.pack(anchor="w")

        wrap = tk.Frame(self, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        wrap.pack(fill="both", expand=True, pady=(12, 0))
        cols = ("date", "week", "kind", "title", "min", "state")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        for c, t, w, a in [("date", "日期", 100, "w"), ("week", "周", 46, "center"),
                           ("kind", "类型", 76, "center"), ("title", "任务", 520, "w"),
                           ("min", "分钟", 64, "center"), ("state", "状态", 80, "center")]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("done", foreground=MUTED)
        self.tree.tag_configure("today", background="#f2f7ff")

    def header(self, parent):
        button(parent, "重新排计划", self.replan).pack(side="right")
        button(parent, "只看本周", self.toggle, "ghost").pack(side="right", padx=6)

    def toggle(self):
        self._week = not getattr(self, "_week", False)
        self.refresh()

    def replan(self):
        cid = self.app.course_id()
        self.app.run_bg(lambda: engine.regenerate(self.db, course=cid),
                        lambda r: (self.app.toast(r["note"], "ok"), self.refresh()), tag="replan")

    def refresh(self):
        cid = self.app.course_id()
        note = self.db.get("plan_note:" + cid) or self.db.get("plan_note") \
            or "还没生成计划。点右上角「重新排计划」。"
        self.note.configure(text=note)
        for i in self.tree.get_children():
            self.tree.delete(i)
        t0 = today_s()
        if getattr(self, "_week", False):
            import datetime as dt
            d0 = dt.date.today()
            d1 = (d0 + dt.timedelta(days=7)).isoformat()
            rows = self.db.tasks_between(t0, d1, course=cid)
        else:
            rows = self.db.tasks_between("0000-01-01", "9999-12-31", course=cid)
        for r in rows:
            if r["state"] == "dropped":
                continue
            try:
                wk = WEEK_CN[__import__("datetime").date.fromisoformat(r["date"]).weekday()]
            except ValueError:
                wk = ""
            tags = []
            if r["state"] == "done":
                tags.append("done")
            if r["date"] == t0:
                tags.append("today")
            self.tree.insert("", "end", iid=str(r["id"]), values=(
                r["date"], wk, r["kind"], r["title"], r["minutes"],
                {"done": "已完成", "todo": "待做", "skipped": "已跳过"}.get(r["state"], r["state"])),
                tags=tuple(tags))


# ------------------------------------------------------------------ 目标
class GoalsPage(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self.db = app.db
        side = tk.Frame(self, bg=BG, width=300)
        side.pack(side="right", fill="y", padx=(12, 0))
        side.pack_propagate(False)

        wrap = tk.Frame(self, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        wrap.pack(side="left", fill="both", expand=True)
        cols = ("id", "ch", "p", "title", "m", "state")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        for c, t, w, a in [("id", "ID", 52, "w"), ("ch", "章节", 70, "center"),
                           ("p", "优先级", 66, "center"), ("title", "目标", 520, "w"),
                           ("m", "分钟", 60, "center"), ("state", "状态", 76, "center")]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.detail())
        self.tree.tag_configure("done", foreground=MUTED)

        self.dc = card(side)
        self.dc.pack(fill="both", expand=True)
        label(self.dc.inner, "验收点", 11, True).pack(anchor="w")
        self.dtext = tk.Text(self.dc.inner, font=F(9), bg=CARD, fg=TEXT, relief="flat",
                             wrap="word", height=16, bd=0, highlightthickness=0)
        self.dtext.pack(fill="both", expand=True, pady=(8, 8))
        self.dtext.configure(state="disabled")
        self.qbtn = button(self.dc.inner, "为这个目标出 3 题", self.make_quiz, "ghost")
        self.qbtn.pack(fill="x")

    def header(self, parent):
        button(parent, "全新出一批题", self.make_quiz).pack(side="right")
        button(parent, "导入这门课的 goals", self.import_goals, "ghost").pack(side="right", padx=6)

    def import_goals(self):
        cid = self.app.course_id()
        p = paths.goals_file(cid)
        if not p.exists():
            p = paths.goals_json()
        if not p.exists():
            return self.app.toast(f"数据目录里没有 goals_{cid}.json", "warn")
        self.app.run_bg(lambda: self.db.import_goals(p, course=cid),
                        lambda n: (self.app.toast(f"导入 {n} 个目标（{p.name}）", "ok"),
                                   self.refresh()), tag="import")

    def make_quiz(self):
        sel = self.tree.selection()
        if not sel:
            return self.app.toast("先选一个目标", "warn")
        gid = sel[0]
        self.app.toast(f"正在为 {gid} 出题…", "info")
        self.app.run_bg(lambda: engine.ensure_quiz(self.db, gid, force_new=True),
                        lambda qs: self.app.toast(f"{gid} 出题完成（{len(qs)} 道）", "ok"), tag="quiz")

    def detail(self):
        sel = self.tree.selection()
        if not sel:
            return
        g = self.db.goal(sel[0])
        if not g:
            return
        self.dtext.configure(state="normal")
        self.dtext.delete("1.0", "end")
        self.dtext.insert("1.0", f"{g['title']}\n\n")
        if g.get("cp"):
            self.dtext.insert("end", "学完要能回答：\n")
            for c in g["cp"]:
                self.dtext.insert("end", f"· {c}\n")
        if g.get("d"):
            self.dtext.insert("end", f"\n前置：{'、'.join(g['d'])}\n")
        hist = self.db.attempts_for_goal(g["id"], 5)
        if hist:
            self.dtext.insert("end", f"\n最近答题 {len(hist)} 条\n")
        self.dtext.configure(state="disabled")

    def refresh(self):
        cid = self.app.course_id()
        done = self.db.done_goal_ids(cid)
        keep = self.tree.selection()
        for i in self.tree.get_children():
            self.tree.delete(i)
        for g in self.db.goals(course=cid):
            self.tree.insert("", "end", iid=g["id"], values=(
                g["id"], g["ch"], {"core": "核心", "support": "辅助", "optional": "可选"}.get(g["p"], g["p"]),
                g["title"], g["m"], "已通过" if g["id"] in done else ""),
                tags=("done",) if g["id"] in done else ())
        if keep:
            self.tree.selection_set(keep[0])


# ------------------------------------------------------------------ 题库
class QuizPage(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self.db = app.db
        side = tk.Frame(self, bg=BG, width=320)
        side.pack(side="right", fill="y", padx=(12, 0))
        side.pack_propagate(False)

        wrap = tk.Frame(self, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        wrap.pack(side="left", fill="both", expand=True)
        cols = ("goal", "batch", "type", "stem")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        for c, t, w, a in [("goal", "目标", 60, "w"), ("batch", "批次", 50, "center"),
                           ("type", "类型", 60, "center"), ("stem", "题干", 620, "w")]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.preview())

        c = card(side)
        c.pack(fill="both", expand=True)
        label(c.inner, "题目预览", 11, True).pack(anchor="w")
        self.txt = tk.Text(c.inner, font=F(9), bg=CARD, fg=TEXT, relief="flat", wrap="word",
                           bd=0, highlightthickness=0)
        self.txt.pack(fill="both", expand=True, pady=8)
        self.txt.configure(state="disabled")

    def preview(self):
        sel = self.tree.selection()
        if not sel:
            return
        q = self.db.quiz(int(sel[0]))
        if not q:
            return
        lines = [f"[{q['type']}] {q['stem']}", ""]
        if q["options"]:
            for i, o in enumerate(q["options"]):
                mark = "✓" if str(i) == str(q["answer"]) else " "
                lines.append(f"{'ABCDEFGH'[i]}. {o} {mark}")
        if q["rubric"]:
            lines.append("\n要点：")
            lines += [f"· {r}" for r in q["rubric"]]
        lines.append(f"\n来源：{q['source']}　批次：{q['batch']}")
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", "\n".join(lines))
        self.txt.configure(state="disabled")

    def refresh(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        cid = self.app.course_id()
        gids = {g["id"] for g in self.db.goals(course=cid)}
        for r in self.db.all_quiz():
            if r["goal_id"] not in gids:
                continue
            self.tree.insert("", "end", iid=str(r["id"]), values=(
                r["goal_id"], r["batch"], r["type"], r["stem"][:90]))


# ------------------------------------------------------------------ 资料
class MaterialsPage(tk.Frame):
    """一级：原始资料进 inbox → 处理（抽文本、归档、原件进回收站）。
    二级：知识库/<课>/<章>/<目标>.md，只由「拆目标 / 重建」生成。
    """

    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self.db = app.db

        top = card(self, pad=12)
        top.pack(fill="x")
        self.summary = label(top.inner, "", 9, False, MUTED)
        self.summary.pack(anchor="w")
        self.total_lbl = label(top.inner, "", 9, True, TEXT, CARD)
        self.total_lbl.pack(anchor="w", pady=(6, 0))
        prow = tk.Frame(top.inner, bg=CARD)
        prow.pack(fill="x", pady=(6, 0))
        self.pbar = ttk.Progressbar(prow, maximum=100, length=300, mode="determinate")
        self.pbar.pack(side="left")
        row = tk.Frame(top.inner, bg=CARD)
        row.pack(fill="x", pady=(10, 0))
        button(row, "处理 inbox（抽文本→归档）", self.process).pack(side="left")
        button(row, "拆目标 + 建知识库", self.derive).pack(side="left", padx=8)
        button(row, "重建全库", self.rebuild_kb, "ghost").pack(side="left")
        self.cancel_btn = button(row, "取消", self.cancel, "ghost")
        self.cancel_btn.pack(side="left", padx=8)
        self.cancel_btn.configure(state="disabled")
        row2 = tk.Frame(top.inner, bg=CARD)
        row2.pack(fill="x", pady=(8, 0))
        button(row2, "打开 inbox", self.open_inbox, "ghost").pack(side="left")
        button(row2, "打开待拆", self.open_pending, "ghost").pack(side="left", padx=8)
        button(row2, "打开已拆", self.open_done, "ghost").pack(side="left")
        button(row2, "打开知识库", self.open_kb, "ghost").pack(side="left", padx=8)
        button(row2, "打开回收站", self.open_trash, "ghost").pack(side="left")
        button(row2, "清空回收站", self.clear_trash, "ghost").pack(side="left", padx=8)

        wrap = tk.Frame(self, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        wrap.pack(fill="both", expand=True, pady=(12, 0))
        cols = ("name", "kind", "chars", "status", "when")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings")
        for c, t, w, a in [("name", "文件", 460, "w"), ("kind", "类型", 70, "center"),
                           ("chars", "字符", 80, "center"), ("status", "状态", 260, "w"),
                           ("when", "处理时间", 120, "center")]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # 处理进度：后台线程发事件，界面线程每 0.4 秒收一次
        self._q: queue.Queue = queue.Queue()
        self._busy = False
        self._cancel: threading.Event | None = None
        self._rows: dict[str, str] = {}
        self._stat = {"done": 0, "kept": 0, "failed": 0}
        self._t0 = 0.0
        self._pct = 0.0

    def header(self, parent):
        button(parent, "处理 inbox", self.process).pack(side="right")

    # --- 动作
    def process(self):
        if self._busy:
            return self.app.toast("还在处理，等这一轮结束或点「取消」", "warn")
        cid = self.app.course_id()
        files = engine.inbox_files(cid)
        if not files:
            return self.app.toast("inbox 里没有文件", "warn")
        while not self._q.empty():                 # 清掉上一轮的残留
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._busy = True
        self._cancel = threading.Event()
        self._rows = {}
        self._stat = {"done": 0, "kept": 0, "failed": 0}
        self._t0 = time.time()
        self._pct = 0.0
        self.pbar.configure(value=0)
        self.cancel_btn.configure(state="normal")
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.total_lbl.configure(text=f"开始处理 {len(files)} 份…")
        self.app.toast(f"开始处理 {len(files)} 份资料（可并行，随时可取消）", "info")

        def done(r):
            self._finish(r)
            msg = (f"处理结果：归档 {r['done']} 份 / {r['chars']:,} 字，"
                   f"留原件 {r['kept']} 份，失败 {r['failed']} 份")
            if r.get("skipped"):
                msg += f"，跳过重复 {r['skipped']} 份"
            if r.get("cancelled"):
                msg += "\n\n（已被你取消，剩下的下次接着处理）"
            if r["hints"]:
                msg += "\n\n缺工具（这些原件先留着）：\n" + "\n".join(
                    f"· {k}：{v}" for k, v in r["hints"].items())
            messagebox.showinfo("处理完成", msg)

        self.app.run_bg(
            lambda: engine.process_inbox(self.db, course=cid,
                                         progress=self._q.put, cancel=self._cancel),
            done, self._process_fail)
        self.after(150, self._drain)

    def _process_fail(self, e):
        self._finish()
        self.app.toast(f"处理失败：{e}", "err")
        messagebox.showerror("处理失败", f"{type(e).__name__}: {e}")

    def _finish(self, r: dict | None = None):
        self._busy = False
        self.cancel_btn.configure(state="disabled")
        if r is not None:
            self.total_lbl.configure(
                text=f"上一轮：归档 {r['done']} / 留原件 {r['kept']} / 失败 {r['failed']}"
                     f"（{int(time.time() - self._t0)} 秒）")
        self.refresh()

    def cancel(self):
        if self._busy and self._cancel is not None:
            self._cancel.set()
            self.total_lbl.configure(text="正在取消：当前这份处理完就停…")
            self.app.toast("已发出取消，当前这份处理完就停", "warn")

    def _apply(self, ev: dict) -> None:
        for k in ("done", "kept", "failed"):
            if k in ev:
                self._stat[k] = ev[k]
        pct = ev.get("pct")
        if isinstance(pct, (int, float)):
            self._pct = float(pct)
            self.pbar.configure(value=self._pct)
        name = ev.get("name") or ""
        status = ev.get("status") or ""
        if not name or status in ("", "start"):
            return
        label = {"running": f"处理中… {self._pct:.1f}%", "done": "✓ 完成",
                 "kept": "留原件（缺工具）", "failed": "✗ 失败",
                 "skipped": "跳过（重复）"}.get(status, status)
        if ev.get("reason") and status in ("kept", "failed"):
            label += "：" + str(ev["reason"])[:60]
        elif ev.get("reason") and status == "running":
            label += "  " + str(ev["reason"])
        vals = (name, "", f"{ev.get('chars', 0):,}" if ev.get("chars") else "—",
                label, "" if status == "running" else time.strftime("%H:%M:%S"))
        iid = self._rows.get(name)
        if iid and self.tree.exists(iid):
            self.tree.item(iid, values=vals)
        else:
            iid = self.tree.insert("", "end", values=vals)
            self._rows[name] = iid
            self.tree.see(iid)

    def _drain(self):
        try:
            while True:
                self._apply(self._q.get_nowait())
        except queue.Empty:
            pass
        if self._busy:
            self.total_lbl.configure(
                text=f"处理中… {self._pct:.1f}%　已用 {int(time.time() - self._t0)} 秒　"
                     f"完成 {self._stat['done']} · 留原件 {self._stat['kept']} · "
                     f"失败 {self._stat['failed']}")
            self.after(400, self._drain)

    def derive(self):
        """打开「待拆」选择框：勾哪几份拆哪几份，先看预估费用。"""
        cid = self.app.course_id()
        files = engine.pending_files(cid)
        if not files:
            return self.app.toast("待拆文件夹是空的：先处理 inbox，或把 txt 放进待拆", "warn")
        DeriveDialog(self)

    def _run_derive(self, files):
        cid = self.app.course_id()
        self.app.toast(f"开始拆目标（{len(files)} 份）…", "info")

        def prog(msg):
            self.app.after(0, lambda m=msg: self.app.status.configure(text=m))

        def done(r):
            self.refresh()
            if not r.get("added"):
                messagebox.showinfo("拆目标", r.get("note") or "没有新增目标")
                return self.app.toast("没有新增目标（已拆的资料挪到「已拆」了）", "warn")
            titles = "\n".join(f"· {t}" for t in r["titles"][:12])
            messagebox.showinfo(
                "拆好了",
                f"新增 {r['added']} 个目标（{r['minutes']} 分钟，来自 {len(r['files'])} 份资料）：\n\n"
                f"{titles}\n\n本次 {r.get('chunks', 0)} 块 / {r.get('chars', 0):,} 字；"
                f"{r.get('moved', 0)} 份已挪到「已拆」。\n"
                f"知识库写了 {r.get('knowledge', 0)} 个文件。\n计划已重排：\n"
                f"{r.get('note', '')}")
            self.app.toast(f"新增 {r['added']} 个目标，知识库和计划都更新了", "ok")

        self.app.run_bg(lambda: engine.derive_goals(self.db, course=cid, files=files,
                                                      progress=prog),
                        done, tag="derive")

    def rebuild_kb(self):
        self.app.toast("正在重建整个知识库（不分科目）…", "info")
        self.app.run_bg(lambda: engine.write_knowledge(self.db),
                        lambda r: (self.app.toast(
                            f"知识库：写/更新 {r['written']} 个文件"
                            + (f"，{r['changed']} 个改版" if r.get("changed") else "")
                            + (f"，清理 {r['orphaned']} 个孤儿" if r.get("orphaned") else ""),
                            "ok"), self.refresh()), tag="kb")

    # --- 打开文件夹
    def _open(self, p):
        import os
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(p)  # noqa: S606

    def open_inbox(self):
        self._open(paths.materials_dir(self.app.course_id()) / "inbox")

    def open_pending(self):
        self._open(paths.pending_dir(self.app.course_id()))

    def open_done(self):
        self._open(paths.done_dir(self.app.course_id()))

    def open_kb(self):
        self._open(paths.knowledge_dir())

    def open_trash(self):
        self._open(paths.trash_dir())

    def clear_trash(self):
        if not messagebox.askyesno(
                "清空回收站",
                "回收站里的原始资料会被永久删除。\n"
                "已经抽出来的文本和知识库不受影响。\n确定？"):
            return
        self.app.run_bg(
            engine.clear_trash,
            lambda r: (self.app.toast(
                f"回收站已清空：{r['files']} 个文件 / {r['bytes'] / 1048576:.1f} MB", "ok"),
                self.refresh()), tag="trash")

    # --- 刷新
    def refresh(self):
        if getattr(self, "_busy", False):
            return                              # 处理中不覆盖进度表
        cid = self.app.course_id()
        files = engine.inbox_files(cid)
        manifest = engine._manifest_read()
        items = [i for i in manifest.get("items", []) if i.get("course") == cid]
        done = [i for i in items if i.get("status") == "done"]
        kb = engine.kb_count(self.db, cid)             # 一条 SQL，不扫目录
        kb_all = engine.kb_count(self.db)
        try:
            pending = len(engine.pending_files(cid))
        except Exception:  # noqa: BLE001
            pending = 0
        self.summary.configure(
            text=f"{self.app.course_name(cid)}：inbox 待处理 {len(files)} 份　"
                 f"已归档 {len(done)} 份　待拆 {pending} 份　"
                 + ("知识库（瘦客户端不显示）" if getattr(self.db, "remote", False)
                    else f"知识库本课 {kb} 个节点（全库 {kb_all}）")
                 + f"\n流程：资料丢进 materials/{cid}/inbox → 「处理 inbox」（抽文本、原件进回收站）"
                   f" → 产物进「待拆」→ 勾选要拆的 → 拆成功后挪到「已拆」")
        for i in self.tree.get_children():
            self.tree.delete(i)
        for m in sorted(items, key=lambda x: x.get("processed_at") or "", reverse=True)[:300]:
            self.tree.insert("", "end", values=(
                m.get("name", ""), m.get("kind", ""),
                f"{m.get('chars', 0):,}" if m.get("chars") else "—",
                {"done": "已归档", "kept": "留原件", "failed": "失败"}.get(
                    m.get("status", ""), m.get("status", "")),
                (m.get("processed_at") or "")[:16]))


# ------------------------------------------------------------------ 拆目标：选资料
class DeriveDialog(tk.Toplevel):
    """「待拆」清单：搜索 / 过滤 / 多选，底部实时显示字数和预估费用。

    默认一个都不勾；拆哪几份由你点。没有区块上限，只显示费用。
    """

    YUAN_PER_10K = 0.1          # 估算用；实际以 API 账单为准

    def __init__(self, page: "MaterialsPage"):
        super().__init__(page)
        self.page = page
        self.app = page.app
        self.db = page.db
        self.cid = self.app.course_id()
        self.title("拆目标 · 选择要拆的资料")
        self.geometry("900x640")
        self.minsize(760, 480)
        self.configure(bg=BG)
        self.transient(page.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.info: dict[str, tuple[int, float, float, str]] = {}
        base = paths.pending_dir(self.cid)
        for p in engine.pending_files(self.cid):
            try:
                t = p.read_text(encoding="utf-8", errors="replace")
                mt = p.stat().st_mtime
                rel = str(p.relative_to(base))
            except (OSError, ValueError):
                continue
            cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff") / max(1, len(t))
            self.info[str(p)] = (len(t.strip()), cjk, mt, rel)

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 8))
        label(head, "搜索", 9, False, MUTED, BG).pack(side="left")
        self.q = tk.StringVar()
        e = entry(head, width=30)
        e.configure(textvariable=self.q)
        e.pack(side="left", fill="x", expand=True, padx=(6, 14))
        e.bind("<KeyRelease>", lambda _e: self._reload())
        label(head, "过滤", 9, False, MUTED, BG).pack(side="left")
        self.flt = tk.StringVar(value="全部")
        cb = ttk.Combobox(head, textvariable=self.flt, width=10, state="readonly",
                          values=("全部", "中文为主", "英文为主", "最近 7 天"), font=F(9))
        cb.pack(side="left", padx=(6, 0))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._reload())

        wrap = tk.Frame(self, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        wrap.pack(fill="both", expand=True, padx=18)
        cols = ("name", "chars", "blocks", "yuan", "when")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                 selectmode="extended")
        for c, t, w, a in [("name", "文件（待拆里）", 430, "w"),
                           ("chars", "字数", 90, "center"),
                           ("blocks", "块（2.4万字）", 110, "center"),
                           ("yuan", "预估 ¥", 80, "center"),
                           ("when", "修改时间", 130, "center")]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._sum())

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=18, pady=(10, 14))
        self.sel_lbl = label(foot, "未选任何资料", 10, True, TEXT, BG)
        self.sel_lbl.pack(side="left")
        button(foot, "取消", self.destroy, "ghost").pack(side="right")
        self.btn_go = button(foot, "开始拆", self._go)
        self.btn_go.pack(side="right", padx=8)
        self.btn_go.configure(state="disabled")
        button(foot, "清空", self._none, "ghost").pack(side="right", padx=8)
        button(foot, "全选（当前过滤）", self._all, "ghost").pack(side="right")
        label(self, "费用按 ¥0.1/万字估（上限），实际以 API 账单为准；"
                    "拆成功只把选中的挪到「已拆」，失败/取消不动。",
              8, False, MUTED, BG).pack(anchor="w", padx=18, pady=(0, 12))

        self._reload()
        self.grab_set()
        e.focus_set()

    def _visible(self) -> list[str]:
        q = self.q.get().strip().lower()
        flt = self.flt.get()
        now = time.time()
        out = []
        for iid, (chars, cjk, mt, rel) in self.info.items():
            if q and q not in rel.lower():
                continue
            if flt == "中文为主" and cjk < 0.25:
                continue
            if flt == "英文为主" and cjk >= 0.25:
                continue
            if flt == "最近 7 天" and now - mt > 7 * 86400:
                continue
            out.append(iid)
        out.sort(key=lambda i: self.info[i][3].lower())
        return out

    def _reload(self):
        self.tree.delete(*self.tree.get_children())
        for iid in self._visible():
            chars, _cjk, mt, rel = self.info[iid]
            blocks = (chars + 23999) // 24000
            self.tree.insert("", "end", iid=iid, values=(
                rel, f"{chars:,}", blocks, f"¥{chars / 10000 * self.YUAN_PER_10K:.2f}",
                dt.datetime.fromtimestamp(mt).strftime("%m-%d %H:%M")))
        self._sum()

    def _sum(self):
        sel = self.tree.selection()
        chars = sum(self.info[i][0] for i in sel)
        blocks = (chars + 23999) // 24000
        yuan = chars / 10000 * self.YUAN_PER_10K
        if sel:
            self.sel_lbl.configure(
                text=f"已选 {len(sel)} 份 / {chars:,} 字 / {blocks} 块 / 约 ¥{yuan:.2f}")
        else:
            self.sel_lbl.configure(text="未选任何资料（勾几份再开始）")
        self.btn_go.configure(state="normal" if sel else "disabled")

    def _all(self):
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_set(kids)

    def _none(self):
        self.tree.selection_remove(*self.tree.selection())

    def _go(self):
        sel = self.tree.selection()
        if not sel:
            return self.app.toast("先在上面点一份资料（Ctrl+点可多选），再点开始拆", "warn")
        files = [Path(i) for i in sel]
        chars = sum(self.info[i][0] for i in sel)
        yuan = chars / 10000 * self.YUAN_PER_10K
        if yuan >= 5 and not messagebox.askyesno(
                "确认拆目标",
                f"这次 {len(files)} 份 / {chars:,} 字 / {((chars + 23999) // 24000)} 块\n"
                f"预估 ¥{yuan:.2f}\n\n继续？"):
            return
        self.destroy()
        self.page._run_derive(files)


# ------------------------------------------------------------------ 知识库（二级数据）
class KnowledgePage(tk.Frame):
    """知识库浏览器：**一个库，不分科目**，按章分组；每行标科目。

    `知识库/<章>/<id>-<标题>.md`，只读，改不了；`_树.json` 是给知识树读的。
    """

    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self.db = app.db
        self._by_iid: dict[str, dict] = {}

        self.summary = label(self, "", 9, False, MUTED, BG)
        self.summary.pack(anchor="w", pady=(0, 8))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        left = tk.Frame(body, bg=CARD, highlightbackground=LINE, highlightthickness=1, width=420)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.tree = ttk.Treeview(left, columns=("min",), show="tree headings",
                                 selectmode="browse")
        self.tree.heading("#0", text="章 / 节点（全库）")
        self.tree.heading("min", text="分钟")
        self.tree.column("#0", width=340)
        self.tree.column("min", width=56, anchor="center")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._pick)
        self.tree.bind("<<TreeviewOpen>>", self._on_open)
        self._ch_of: dict[str, str] = {}
        self._loaded: set[str] = set()

        right = tk.Frame(body, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self.txt = tk.Text(right, font=("Microsoft YaHei UI", 10), wrap="word",
                           bg="#ffffff", fg=TEXT, relief="flat", bd=0, padx=16, pady=12)
        sb2 = ttk.Scrollbar(right, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb2.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")
        self.txt.tag_configure("h", font=F(13, True), spacing1=8, spacing3=6)
        self.txt.tag_configure("q", foreground=MUTED, lmargin1=10, lmargin2=10)
        self.txt.configure(state="disabled")

    def header(self, parent):
        button(parent, "打开知识库文件夹", self.open_dir, "ghost").pack(side="right")
        button(parent, "重建全库", self.rebuild, "ghost").pack(side="right", padx=8)

    def rebuild(self):
        self.app.toast("正在重建整个知识库（不分科目）…", "info")
        self.app.run_bg(lambda: engine.write_knowledge(self.db),
                        lambda r: (self.app.toast(
                            f"知识库：写/更新 {r['written']} 个文件"
                            + (f"，{r['changed']} 个改版" if r.get("changed") else "")
                            + (f"，清理 {r['orphaned']} 个孤儿" if r.get("orphaned") else ""),
                            "ok"), self.refresh()), tag="kb-rebuild")

    def open_dir(self):
        import os
        p = paths.knowledge_dir()
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(p)  # noqa: S606

    def _pick(self, _ev=None):
        sel = self.tree.selection()
        if not sel:
            return
        row = self._by_iid.get(sel[0])
        if not row:
            return                                  # 点的是章节点
        try:
            self._render(engine.knowledge_read(row["path"]))
        except OSError as e:
            self._render(f"读不出来：{e}")

    def _on_open(self, _ev=None):
        """展开章节点时才去库里拉这一章的节点（2000 个节点也不怕）。"""
        iid = self.tree.focus()
        ch = self._ch_of.get(iid)
        if not ch or ch in self._loaded:
            return
        for r in engine.knowledge_list(self.db, chapter=ch):
            kid = self.tree.insert(
                iid, "end", text=f"[{r['course']}] {r['id']}  {r['title']}",
                values=(r["minutes"] or "",))
            self._by_iid[kid] = r
        self._loaded.add(ch)

    def _render(self, md: str):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        for line in md.splitlines():
            if line.startswith("#"):
                self.txt.insert("end", line.lstrip("#").strip() + "\n", "h")
            elif line.startswith(">"):
                self.txt.insert("end", line.lstrip(">").strip() + "\n", "q")
            else:
                self.txt.insert("end", line + "\n")
        self.txt.configure(state="disabled")
        self.txt.see("1.0")

    def refresh(self):
        if getattr(self.db, "remote", False):
            # 瘦客户端：知识库文件在服务器本机，桌面这端读不到；不显示成「空的」误导人
            self.tree.delete(*self.tree.get_children())
            self._by_iid, self._ch_of, self._loaded = {}, {}, set()
            self.summary.configure(
                text="瘦客户端模式：知识库只存在服务器那台机器的数据目录里。\n"
                     "在服务器上打开桌面程序才能看/重建；手机端本来就不提供知识库。")
            self._render("（瘦客户端模式，不加载知识库）")
            return
        chs = engine.kb_chapters(self.db)               # 先列章（一条 SQL），节点展开再拉
        self.tree.delete(*self.tree.get_children())
        self._by_iid = {}
        self._ch_of = {}
        self._loaded: set[str] = set()
        if not chs:
            self.summary.configure(
                text="知识库还是空的。去「资料」页点「拆目标 + 建知识库」，"
                     "或点右上角「重建全库」。")
            self._render("（空）")
            return
        for c in chs:
            ch = c["chapter"] or "未分章"
            iid = self.tree.insert("", "end", text=f"{ch}（{c['n']}）", open=False,
                                   values=("",))
            self._ch_of[iid] = ch
        total = sum(int(c["n"] or 0) for c in chs)
        self.summary.configure(
            text=f"全库 {total} 个节点 / {len(chs)} 章　{paths.knowledge_dir()}\n"
                 f"不分科目：按章排，点章展开；_树.json 是给知识树读的。只读，不手改。")


# ------------------------------------------------------------------ 待确认
class ReviewPage(tk.Frame):
    """手机和电脑判定不一致的卡：只有两个选择，听电脑 / 保留手机。

    条目由同步时的比对产生（P3 接上），这里只负责看和处理。
    不处理就一直挂着，不会自己消失；处理完写 resolved_at，下次同步手机才更新。
    """

    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self.db = app.db
        self._rows: dict[str, dict] = {}

        top = card(self)
        top.pack(fill="x")
        head = tk.Frame(top.inner, bg=CARD)
        head.pack(fill="x")
        label(head, "待确认", 11, True).pack(side="left")
        self.count_lbl = label(head, "", 9, False, MUTED)
        self.count_lbl.pack(side="left", padx=(10, 0))
        label(top.inner,
              "手机上报的判定和电脑算出来的不一致时，那张卡会出现在这里。"
              "只有两个选择：听电脑（按电脑的结果改这张卡）、保留手机（按手机上报名单里的结果转正）。"
              "处理完写一条日志，下次同步手机才更新；不处理就一直挂着。",
              9, False, MUTED, CARD, wraplength=1000, justify="left").pack(anchor="w", pady=(6, 0))

        wrap = tk.Frame(self, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        wrap.pack(fill="both", expand=True, pady=(12, 0))
        cols = ("day", "goal", "phone", "server", "reason")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        for key, text, width, anchor in (
                ("day", "日期", 100, "w"),
                ("goal", "目标 / 卡片", 300, "w"),
                ("phone", "手机判定", 100, "center"),
                ("server", "电脑判定", 100, "center"),
                ("reason", "原因", 380, "w")):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor=anchor,
                             stretch=(key in ("goal", "reason")))
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        foot = card(self)
        foot.pack(fill="x", pady=(12, 0))
        self.detail = label(foot.inner, "", 9, False, TEXT, CARD, wraplength=1000,
                            justify="left", anchor="w")
        self.detail.pack(anchor="w")
        row = tk.Frame(foot.inner, bg=CARD)
        row.pack(fill="x", pady=(10, 0))
        self.btn_server = button(row, "听电脑", lambda: self.resolve("follow_server"))
        self.btn_server.pack(side="left")
        self.btn_phone = button(row, "保留手机", lambda: self.resolve("keep_phone"), "ok")
        self.btn_phone.pack(side="left", padx=8)
        self._buttons(False)

    def header(self, parent):
        button(parent, "刷新", self.refresh, "ghost").pack(side="right")

    # --- 内部
    def _buttons(self, on: bool) -> None:
        state = "normal" if on else "disabled"
        for b in (self.btn_server, self.btn_phone):
            b.configure(state=state)

    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        return self._rows.get(sel[0]) if sel else None

    def _on_select(self, _ev=None) -> None:
        r = self._selected()
        if not r:
            self._buttons(False)
            self.detail.configure(text="")
            return
        self._buttons(True)
        who = f"#{r['id']} · {r.get('kind') or 'task_state'}"
        if r.get("goal_id"):
            who += f" · {r.get('goal_id')}"
        if r.get("task_id"):
            who += f" · 卡片 #{r['task_id']}"
        lines = [who,
                 f"手机：{r.get('phone_state') or '—'}　电脑：{r.get('server_state') or '—'}",
                 f"原因：{r.get('reason') or '—'}"]
        payload = r.get("payload_json") or ""
        if payload and payload not in ("{}", "null"):
            lines.append(f"明细：{payload[:800]}")
        self.detail.configure(text="\n".join(lines))

    # --- 刷新 / 处理
    def refresh(self) -> None:
        try:
            rows = self.db.review_items()
        except Exception as e:          # 远端断线别让整页炸
            self.app.toast(f"读待确认失败：{e}", "err")
            rows = []
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._rows = {}
        for r in rows:
            title = r.get("goal_title") or r.get("goal_id") or "（目标已删除）"
            if r.get("day"):
                title = f"{title}　{r['day']}"
            iid = self.tree.insert("", "end", values=(
                r.get("day") or (r.get("created_at") or "")[:10],
                title,
                r.get("phone_state") or "—",
                r.get("server_state") or "—",
                (r.get("reason") or "").replace("\n", " ")[:90]))
            self._rows[iid] = r
        n = len(rows)
        self.count_lbl.configure(text=f"{n} 条未处理" if n else "没有未处理的")
        self.detail.configure(
            text="选一条看看详情，然后决定听电脑还是保留手机。" if n else
            "这里还是空的。手机端的判定和电脑不一致时才会出现；不处理也不会自己消失。")
        self._buttons(False)
        self.app.update_pending_badge(n)

    def resolve(self, resolution: str) -> None:
        r = self._selected()
        if not r:
            return
        what = ("听电脑：按电脑算出来的结果改这张卡" if resolution == "follow_server"
                else "保留手机：按手机上报名单里的结果转正")
        if not messagebox.askyesno(
                "确认",
                f"{what}？\n\n#{r['id']}　{r.get('goal_title') or r.get('goal_id') or ''}\n\n"
                "会记一条日志；手机端要等下次同步才更新。"):
            return
        rid = int(r["id"])
        text = "听电脑" if resolution == "follow_server" else "保留手机"
        self.app.run_bg(lambda: self.db.review_resolve(rid, resolution),
                        lambda _row: (self.app.toast(f"待确认 #{rid} 已处理：{text}", "ok"),
                                      self.refresh()),
                        tag="review")


# ------------------------------------------------------------------ 设置
class SettingsPage(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self.db = app.db

        # 手机连接：地址和 token 放最上面。地址会随路由器变，token 存在文件里，
        # 别让人再去翻 server.json / desktop.json。
        cc = card(self)
        cc.pack(fill="x", pady=(0, 12))
        label(cc.inner, "手机连接（照抄到手机 App 的「设置」）", 11, True).pack(
            anchor="w", pady=(0, 8))
        rowu = tk.Frame(cc.inner, bg=CARD)
        rowu.pack(fill="x", pady=2)
        label(rowu, "地址", 9, False, MUTED, CARD, width=5, anchor="w").pack(side="left")
        self.conn_url = label(rowu, "…", 10, True, ACCENT, CARD, anchor="w")
        self.conn_url.pack(side="left")
        button(rowu, "复制地址", lambda: self._copy(self.conn_url.cget("text")),
               "ghost").pack(side="right")
        rowt = tk.Frame(cc.inner, bg=CARD)
        rowt.pack(fill="x", pady=2)
        label(rowt, "Token", 9, False, MUTED, CARD, width=5, anchor="w").pack(side="left")
        self.conn_token = label(rowt, "…", 10, True, TEXT, CARD, anchor="w")
        self.conn_token.pack(side="left")
        button(rowt, "复制 Token", lambda: self._copy(self.conn_token.cget("text")),
               "ghost").pack(side="right")
        self.conn_note = label(cc.inner, "", 8, False, MUTED, CARD, anchor="w",
                               justify="left")
        self.conn_note.pack(fill="x", pady=(6, 0))
        rowr = tk.Frame(cc.inner, bg=CARD)
        rowr.pack(fill="x", pady=(6, 0))
        button(rowr, "刷新", self.refresh_conn, "ghost").pack(side="left")

        # 当前课程（考核日 / 周末系数 / 每日上限都按课存；每天多少分钟由排程算，不存）
        c0 = card(self)
        c0.pack(fill="x")
        label(c0.inner, "这门课", 11, True).pack(anchor="w", pady=(0, 10))
        row = tk.Frame(c0.inner, bg=CARD)
        row.pack(fill="x")
        self.f_name = self._field_row(row, "课程名", 22)
        self.f_exam = self._field_row(row, "考核日 YYYY-MM-DD", 14)
        self.f_ratio = self._field_row(row, "周末多学 %（100 = 一样）", 10)
        self.f_cap = self._field_row(row, "每天上限分钟（0 = 不限）", 10)
        self.f_lang = self._field_row(row, "资料语言 zh/en", 8)
        row1 = tk.Frame(c0.inner, bg=CARD)
        row1.pack(fill="x", pady=(12, 0))
        label(c0.inner, "每天学多少不手填：按「没学完的分钟 ÷ 剩余天数」自动算，落后越久每天越长；"
                        "这里只调周末加多少和可选的硬上限", 8, False, MUTED, CARD).pack(
            anchor="w", pady=(8, 0))
        button(row1, "保存这门课", self.save_course).pack(side="left")
        button(row1, "重排这门课", self.replan_course, "ghost").pack(side="left", padx=8)

        # 跨课程总预算：几门课一起跑时，一天总共学多久
        ct = card(self)
        ct.pack(fill="x", pady=(12, 0))
        label(ct.inner, "每天总共学多久（所有课加起来）", 11, True).pack(
            anchor="w", pady=(0, 10))
        rowt = tk.Frame(ct.inner, bg=CARD)
        rowt.pack(fill="x")
        self.f_total = self._field_row(rowt, "每天总预算分钟（0 = 不限）", 14)
        rowt2 = tk.Frame(ct.inner, bg=CARD)
        rowt2.pack(fill="x", pady=(12, 0))
        button(rowt2, "保存并重排全部", self.save_total).pack(side="left")
        label(ct.inner,
              "设了以后：先算每门课每天需要多少；总和超了，就按离考核日远近分。"
              "单张卡不能拆，某天可能比预算多出一张卡的量。",
              8, False, MUTED, CARD).pack(anchor="w", pady=(8, 0))

        # 新增课程
        c1 = card(self)
        c1.pack(fill="x", pady=(12, 0))
        label(c1.inner, "加一门课", 11, True).pack(anchor="w", pady=(0, 10))
        row2 = tk.Frame(c1.inner, bg=CARD)
        row2.pack(fill="x")
        self.n_id = self._field_row(row2, "id（英文短名，如 math）", 12)
        self.n_name = self._field_row(row2, "课程名", 18)
        self.n_exam = self._field_row(row2, "考核日", 14)
        self.n_ratio = self._field_row(row2, "周末多学 %", 10)
        self.n_cap = self._field_row(row2, "每天上限分钟", 10)
        row3 = tk.Frame(c1.inner, bg=CARD)
        row3.pack(fill="x", pady=(12, 0))
        button(row3, "新建课程", self.add_course).pack(side="left")
        label(row3, "　建好后：顶层下拉切到这门课，资料放 数据目录/materials/<id>/，"
                    "目标用 goals_<id>.json", 8, False, MUTED, CARD).pack(side="left")

        # 模型
        c2 = card(self)
        c2.pack(fill="x", pady=(12, 0))
        label(c2.inner, "模型", 11, True).pack(anchor="w", pady=(0, 8))
        self.f_key = self._field(c2.inner, "DeepSeek API Key（必填；聊天、出题要用，看计划不需要）",
                                 show="•")
        self.f_model = self._field(c2.inner, "模型名")
        self.f_en = tk.BooleanVar(value=(self.db.get("en_query", "") == "1"))
        tk.Checkbutton(
            c2.inner,
            text="英文资料也参与检索（聊天时把中文问题翻成英文检索词）",
            variable=self.f_en, font=F(9), bg=CARD, fg=TEXT, activebackground=CARD,
            selectcolor=CARD, anchor="w", highlightthickness=0, bd=0,
            cursor="hand2").pack(anchor="w", pady=(6, 0))
        label(c2.inner,
              "打开后每次提问会多调一次小模型（约 +1 秒、<¥0.001）；只影响聊天检索，"
              "知识库仍按中文检索。关掉就是纯中文检索。",
              8, False, MUTED, CARD).pack(anchor="w", pady=(2, 0))
        row4 = tk.Frame(c2.inner, bg=CARD)
        row4.pack(fill="x", pady=(10, 0))
        button(row4, "保存模型设置", self.save_model).pack(side="left")

        # 外部工具（图片 OCR / 音视频转写）
        c2b = card(self)
        c2b.pack(fill="x", pady=(12, 0))
        label(c2b.inner, "外部工具（可选：图片 OCR、音视频转写）", 11, True).pack(
            anchor="w", pady=(0, 6))
        self.f_tess = self._field(c2b.inner, "tesseract 路径（装在 PATH 里可留空）")
        self.f_ff = self._field(c2b.inner, "ffmpeg 路径（装在 PATH 里可留空）")
        self.f_wh = self._field(c2b.inner, "whisper-cli 路径（whisper.cpp 的命令行）")
        self.f_wm = self._field(c2b.inner, "whisper 模型路径（如 ggml-base.bin）")
        self.f_wme = self._field(c2b.inner, "英文课模型路径（ggml-small.en.bin，快 3 倍）")
        self.f_td = self._field(c2b.inner, "tessdata 目录（中文语言包放哪；留空用 工具文件夹\\tessdata）")
        row4b = tk.Frame(c2b.inner, bg=CARD)
        row4b.pack(fill="x", pady=(10, 0))
        button(row4b, "保存工具设置", self.save_tools).pack(side="left")
        button(row4b, "检查工具", self.check_tools, "ghost").pack(side="left", padx=8)
        button(row4b, "打开工具文件夹", self.open_tools, "ghost").pack(side="left")
        label(c2b.inner, "没装也不影响文本类资料；图片/视频会留在 inbox 里等工具装好再处理。",
              8, False, MUTED).pack(anchor="w", pady=(8, 0))
        label(c2b.inner, "扫描版 PDF 会自动用 tesseract OCR（只对没文字的页）；"
                         ".doc/.ppt 用本机 Office 或 LibreOffice 另存为 docx/pptx 再读，"
                         "转换前请先关掉 Word/PowerPoint。",
              8, False, MUTED).pack(anchor="w", pady=(2, 0))
        label(c2b.inner, "换电脑时从这几个地方下（工具放 %LOCALAPPDATA%\\学习bot\\tools 就行）：",
              8, False, MUTED).pack(anchor="w", pady=(6, 2))
        row4c = tk.Frame(c2b.inner, bg=CARD)
        row4c.pack(fill="x")
        for text, url in DOWNLOADS:
            button(row4c, text, (lambda u=url: webbrowser.open(u)), "ghost").pack(
                side="left", padx=(0, 6))

        # 数据：库、资料、备份、导出
        c3 = card(self)
        c3.pack(fill="x", pady=(12, 0))
        label(c3.inner, "数据", 11, True).pack(anchor="w", pady=(0, 6))
        self.data_lbl = label(c3.inner, "", 9, False, MUTED, CARD)
        self.data_lbl.pack(anchor="w")
        row5 = tk.Frame(c3.inner, bg=CARD)
        row5.pack(fill="x", pady=(10, 0))
        button(row5, "立即备份", self.backup_now).pack(side="left")
        button(row5, "导出 JSON", self.export_now, "ghost").pack(side="left", padx=8)
        button(row5, "打开数据文件夹", self.open_data_dir, "ghost").pack(side="left")
        label(c3.inner, "备份在 backups/（每天自动一份，留最近 20 份）；"
                        "导出在 exports/（不含 API key 和 bot token）",
              8, False, MUTED).pack(anchor="w", pady=(8, 0))

        tip = card(self, pad=12)
        tip.pack(fill="x", pady=(12, 0))
        label(tip.inner, "规则（写死，模型不能改）", 10, True).pack(anchor="w")
        for line in [
            "· 完成 = 小测 3 题对 2 题，不是打卡",
            "· 日期、容量、缓冲全部由 planner.py 算，模型不输出日期",
            "· 每天学多少不写死：按「剩余量 ÷ 剩余天数」算，周末按系数加权，落后自动加量",
            "· 每门课各排各的（自己的考核日和周末系数），落后各算各的",
            "· 同一目标 24 小时内最多换 2 次题，换不动就回炉",
            "· Telegram 已摘：不起 bot、不推送、不用代理",
            "· 手机端判定和电脑不一致时进「待确认」，不自动改回，等你定",
        ]:
            label(tip.inner, line, 9, False, MUTED).pack(anchor="w", pady=1)

    def _field(self, parent, title, show=None):
        wrap = tk.Frame(parent, bg=CARD)
        wrap.pack(fill="x", pady=4)
        label(wrap, title, 9, False, MUTED).pack(anchor="w")
        e = entry(wrap, width=50, show=show)
        e.pack(fill="x", pady=(3, 0))
        return e

    def _field_row(self, parent, title, width):
        wrap = tk.Frame(parent, bg=CARD)
        wrap.pack(side="left", padx=(0, 16))
        label(wrap, title, 9, False, MUTED).pack(anchor="w")
        e = entry(wrap, width=width)
        e.pack(pady=(3, 0))
        return e

    def _fill(self, e, value) -> None:
        e.delete(0, "end")
        e.insert(0, str(value))

    # ---------------------------------------------------------------- 手机连接
    def _lan_ip(self) -> str:
        """本机在局域网里的地址，手机要填的就是它。"""
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

    def _conn_info(self) -> tuple[str, str, str]:
        """(地址, token, 来源说明)。

        本地模式优先用桌面端自己起的服务（`app.local_server`）；瘦客户端看 desktop.json；
        都没有再退回读 server.json。
        """
        ls = getattr(self.app, "local_server", None)
        if ls is not None:
            state = "运行中" if ls.running else ls.status_text()
            return ls.url, ls.token, f"本机服务（{state}）"
        p = paths.desktop_config()
        cfg: dict = {}
        if p.exists():
            try:
                cfg = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cfg = {}
        server = str(cfg.get("server") or "").rstrip("/")
        if server:
            return server, str(cfg.get("token") or ""), f"desktop.json：{p}"
        sp = paths.server_config()
        token = ""
        if sp.exists():
            try:
                token = str(json.loads(sp.read_text(encoding="utf-8")).get("token") or "")
            except (OSError, ValueError):
                token = ""
        return f"http://{self._lan_ip()}:8787", token, f"server.json：{sp}"

    def refresh_conn(self) -> None:
        url, token, src = self._conn_info()
        self.conn_url.configure(text=url)
        self.conn_token.configure(text=token or "（还没有 token：服务没起来，见下面原因）")
        self.conn_note.configure(
            text=f"来源：{src}\n"
                 "服务随桌面软件一起起停：**不用再单独开 server/run.py**。\n"
                 "地址会变：路由器重新分配 IP 时这里跟着变，手机填新的那串就行。\n"
                 "token 不会自己变：只有删掉数据目录里的 server.json 再重启，才会重新生成。\n"
                 "手机和电脑要同一个 WiFi，Windows 防火墙要放行 8787 入站。")

    def _copy(self, text: str) -> None:
        text = (text or "").strip()
        if not text or text.startswith("（"):
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()
        self.app.toast("已复制", "ok")

    def refresh(self):
        c = self.db.course(self.app.course_id()) or {}
        self._fill(self.f_name, c.get("name") or self.app.course_id())
        self._fill(self.f_exam, c.get("exam_date") or self.db.get("exam_date", "2027-01-04"))
        self._fill(self.f_ratio, c.get("weekend_ratio") or self.db.geti("weekend_ratio", 130))
        self._fill(self.f_cap, c.get("day_cap") if c.get("day_cap") is not None
                   else self.db.geti("day_cap", 0))
        self._fill(self.f_lang, self.db.get(f"lang:{self.app.course_id()}", "zh") or "zh")
        self._fill(self.f_total, self.db.geti("day_total_cap", 0))
        self._fill(self.f_key, self.db.get("api_key", ""))
        self._fill(self.f_model, self.db.get("model", "deepseek-flash"))
        self._fill(self.f_tess, self.db.get("tesseract_path", ""))
        self._fill(self.f_ff, self.db.get("ffmpeg_path", ""))
        self._fill(self.f_wh, self.db.get("whisper_path", ""))
        self._fill(self.f_wm, self.db.get("whisper_model", ""))
        self._fill(self.f_wme, self.db.get("whisper_model_en", ""))
        self._fill(self.f_td, self.db.get("tessdata_dir", ""))
        last = self.db.get("last_backup", "") or "还没备份过"
        self.data_lbl.configure(text=f"{paths._r()}\n上次备份：{last}")
        self.refresh_conn()

    def save_course(self):
        cid = self.app.course_id()
        name = self.f_name.get().strip() or cid
        exam = self.f_exam.get().strip()
        try:
            dt.date.fromisoformat(exam)
        except ValueError:
            return self.app.toast("考核日格式应为 YYYY-MM-DD", "warn")
        try:
            ratio = int(self.f_ratio.get().strip() or 130)
            cap = int(self.f_cap.get().strip() or 0)
        except ValueError:
            return self.app.toast("周末系数和每日上限都要填整数", "warn")
        ratio = max(100, min(200, ratio))
        cap = max(0, min(600, cap))
        lang = (self.f_lang.get().strip().lower() or "zh")
        if lang not in ("zh", "en"):
            return self.app.toast("资料语言只能填 zh 或 en", "warn")
        self.db.set(f"lang:{cid}", lang)
        self.app.run_bg(
            lambda: self.db.save_course(cid, name=name, exam_date=exam,
                                        weekend_ratio=ratio, day_cap=cap),
            lambda _c: (self.app.load_courses(), self.app.toast("这门课已保存", "ok"),
                        self.refresh()), tag="course-save")
        if cid == "ds":                 # 兼容：老设置页/推送还读全局这两项
            self.db.set("exam_date", exam)
            self.db.set("weekend_ratio", str(ratio))
            self.db.set("day_cap", str(cap))

    def save_total(self):
        try:
            cap = int(self.f_total.get().strip() or 0)
        except ValueError:
            return self.app.toast("总预算要填整数分钟，0 = 不限", "warn")
        cap = max(0, min(600, cap))
        self.db.set("day_total_cap", str(cap))
        self.app.run_bg(
            lambda: engine.regenerate(self.db),
            lambda r: (self.app.toast(r["note"], "ok"), self.refresh()),
            tag="replan-all")

    def add_course(self):
        cid = self.n_id.get().strip().lower()
        if not cid or not cid.isascii() or not cid.replace("-", "").replace("_", "").isalnum():
            return self.app.toast("id 用英文短名，比如 math / os", "warn")
        if self.db.course(cid):
            return self.app.toast(f"{cid} 已经存在", "warn")
        exam = self.n_exam.get().strip()
        try:
            dt.date.fromisoformat(exam)
        except ValueError:
            return self.app.toast("考核日格式应为 YYYY-MM-DD", "warn")
        try:
            ratio = int(self.n_ratio.get().strip() or 130)
            cap = int(self.n_cap.get().strip() or 0)
        except ValueError:
            return self.app.toast("周末系数和每日上限都要填整数", "warn")
        name = self.n_name.get().strip() or cid

        def done(_c):
            self.n_id.delete(0, "end")
            self.n_name.delete(0, "end")
            self.n_exam.delete(0, "end")
            self.n_ratio.delete(0, "end")
            self.n_cap.delete(0, "end")
            self.app.load_courses()
            self.app.show("settings")
            self.app.toast(f"已新建 {name}，切到这门课再导目标", "ok")

        self.app.run_bg(
            lambda: self.db.save_course(cid, name=name, exam_date=exam,
                                        weekend_ratio=ratio, day_cap=cap),
            done, tag="course-add")

    def save_model(self):
        key = self.f_key.get().strip()
        if key != "****":                # **** = 服务器上已有，别覆盖；空 = 清掉
            self.db.set("api_key", key)
        self.db.set("model", self.f_model.get().strip() or "deepseek-flash")
        self.db.set("en_query", "1" if self.f_en.get() else "0")
        self.app.refresh_key_hint()
        self.app.toast("模型设置已保存" if key else "已清空模型 key：聊天不可用，看计划不受影响",
                       "ok" if key else "warn")

    def save_tools(self):
        self.db.set("tesseract_path", self.f_tess.get().strip())
        self.db.set("ffmpeg_path", self.f_ff.get().strip())
        self.db.set("whisper_path", self.f_wh.get().strip())
        self.db.set("whisper_model", self.f_wm.get().strip())
        self.db.set("whisper_model_en", self.f_wme.get().strip())
        self.db.set("tessdata_dir", self.f_td.get().strip())
        self.app.toast("外部工具设置已保存", "ok")

    def check_tools(self):
        def done(r):
            lines = [("✓ " if v["ok"] else "✗ ") + k + "：" + str(v["detail"]) for k, v in r.items()]
            messagebox.showinfo("外部工具检查", "\n".join(lines))
        self.app.run_bg(lambda: engine.check_tools(self.db), done, tag="check-tools")

    def open_tools(self):
        import os
        p = paths.tools_dir()
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(p)  # noqa: S606

    def import_goals(self):
        cid = self.app.course_id()
        p = paths.goals_file(cid)
        if not p.exists():
            p = paths.goals_json()
        if not p.exists():
            return self.app.toast(f"数据目录里没有 goals_{cid}.json", "warn")
        self.app.run_bg(lambda: self.db.import_goals(p, course=cid),
                        lambda n: self.app.toast(f"导入 {n} 个目标（{p.name}）", "ok"),
                        tag="import")

    def replan_course(self):
        cid = self.app.course_id()
        self.app.run_bg(lambda: engine.regenerate(self.db, course=cid),
                        lambda r: self.app.toast(r["note"], "ok"), tag="replan")

    # --- 数据
    def backup_now(self):
        def work():
            p = self.db.backup()
            self.db.log(f"手动备份：{p.name}", "ok")
            return p
        self.app.run_bg(work, lambda p: (self.app.toast(f"已备份：{p.name}", "ok"), self.refresh()),
                        tag="backup")

    def export_now(self):
        self.app.run_bg(self.db.export_json,
                        lambda p: (self.app.toast(f"已导出：{p.name}", "ok"), self.refresh()),
                        tag="export")

    def open_data_dir(self):
        import os
        p = paths.ensure()
        os.startfile(p)  # noqa: S606


# ------------------------------------------------------------------ 日志
class LogPage(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self.db = app.db
        wrap = tk.Frame(self, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        wrap.pack(fill="both", expand=True)
        self.txt = tk.Text(wrap, font=("Consolas", 9), bg="#ffffff", fg=TEXT, relief="flat",
                           bd=0, wrap="word", padx=12, pady=10)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.txt.tag_configure("err", foreground=ERR)
        self.txt.tag_configure("warn", foreground=WARN)
        self.txt.tag_configure("ok", foreground=OK)
        self.txt.tag_configure("ts", foreground="#b3bac4")

    def header(self, parent):
        button(parent, "清空日志", self.clear_logs, "ghost").pack(side="right", padx=(6, 0))
        button(parent, "清空界面", lambda: self.txt.delete("1.0", "end"), "ghost").pack(side="right")

    def clear_logs(self):
        if not messagebox.askyesno("清空日志", "日志库里的记录会全部删掉（界面上的残留可以再点「清空界面」）。\n确定？"):
            return
        self.app.run_bg(self.db.clear_logs,
                        lambda n: (self.app.toast(f"已删掉 {n} 条日志", "ok"), self.refresh()),
                        tag="log-clear")

    def append(self, ts, level, text):
        self.txt.insert("end", f"{ts}  ", "ts")
        self.txt.insert("end", f"{text}\n", level if level in ("err", "warn", "ok") else ())
        self.txt.see("end")

    def refresh(self):
        self.txt.delete("1.0", "end")
        for r in reversed(self.db.logs(500)):
            self.append(r["ts"], r["level"], r["text"])
