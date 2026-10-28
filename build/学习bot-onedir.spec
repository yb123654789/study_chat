# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包脚本（目录版）：不再解包到 %TEMP%。

    py -m PyInstaller --noconfirm --clean build/学习bot-onedir.spec

产物：dist/学习bot-app/学习bot.exe + 同目录的运行时文件。
和单文件版的区别：启动时不需要往临时目录解压 14MB，也就没有
"Could not create temporary directory!" / "Failed to extract ... Permission denied"
这类问题；启动也快。
"""
from pathlib import Path

ROOT = Path(SPECPATH).parent          # build/ 的上一级 = 项目根
APP = ROOT / "app"
TOOLS = ROOT / "tools"

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
    [],
    exclude_binaries=True,
    name="学习bot",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="学习bot-app",
)
