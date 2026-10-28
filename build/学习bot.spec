# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包脚本：把 app/ 打成一个 学习bot.exe（单文件，无控制台）。

    py -m PyInstaller --noconfirm --clean build/学习bot.spec

产物在 dist/学习bot.exe。数据不打包：第一次运行会让用户选数据目录。
"""
from pathlib import Path

ROOT = Path(SPECPATH).parent          # build/ 的上一级 = 项目根
APP = ROOT / "app"
TOOLS = ROOT / "tools"

# 这些是运行时按 sys.path 动态导入的，PyInstaller 静态分析看不到名字
HIDDEN = [
    "db", "paths", "engine", "llm", "chat", "bot", "service", "remote", "ui", "main",
    "planner", "retrieval", "build_reading", "extract",
]

DATAS = []
_quiz = ROOT / "web" / "data" / "quiz.json"
if _quiz.exists():
    DATAS.append((str(_quiz), "web/data"))     # 第一次运行导现成题库用

a = Analysis(
    [str(APP / "main.py")],
    pathex=[str(APP), str(TOOLS)],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=["unittest", "pydoc", "doctest", "tkinter.test", "sqlite3.test"],
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
