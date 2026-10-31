# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包脚本：把 app/ 打成一个 学习bot.exe（单文件，无控制台）。

    py -m PyInstaller --noconfirm --clean build/学习bot.spec

产物在 dist/学习bot.exe。数据不打包：第一次运行会让用户选数据目录。
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent          # build/ 的上一级 = 项目根
APP = ROOT / "app"
TOOLS = ROOT / "tools"
SERVER = ROOT / "server"

# 这些是运行时按 sys.path 动态导入的，PyInstaller 静态分析看不到名字
HIDDEN = [
    "db", "paths", "engine", "llm", "chat", "bot", "service", "remote", "ui", "main",
    "planner", "retrieval", "build_reading", "extract",
    "api",        # localserver 延迟导入 server/api.py
]

DATAS = []
BINARIES = []
for _mod in ("pypdfium2", "pypdfium2_raw"):   # 扫描版/加密 PDF（pdfium.dll 要带上）
    try:
        _d, _b, _h = collect_all(_mod)
    except Exception:
        continue
    DATAS += _d
    BINARIES += _b
    HIDDEN += _h
HIDDEN += ["pypdfium2", "pypdfium2_raw"]
_libs = ROOT / "tools" / "libs"      # 打包版处理 PDF 要 pypdf（见 onedir spec 注释）
if _libs.is_dir():
    DATAS.append((str(_libs), "libs"))
HIDDEN += ["pypdf"]                  # tools/libs 里的那份不可用时，退到 pip 装的 pypdf
_quiz = ROOT / "web" / "data" / "quiz.json"
if _quiz.exists():
    DATAS.append((str(_quiz), "web/data"))     # 第一次运行导现成题库用

a = Analysis(
    [str(APP / "main.py")],
    pathex=[str(APP), str(TOOLS), str(SERVER)],
    binaries=BINARIES,
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=["unittest", "pydoc", "doctest", "tkinter.test", "sqlite3.test",
              "numpy"],       # pypdfium2 的 to_numpy() 会拖进 26MB numpy，桌面端用不到
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="学习bot",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
