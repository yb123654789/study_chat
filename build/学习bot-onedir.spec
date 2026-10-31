# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包脚本（目录版）：不再解包到 %TEMP%。

    py -m PyInstaller --noconfirm --clean build/学习bot-onedir.spec

产物：dist/学习bot-app/学习bot.exe + 同目录的运行时文件。
和单文件版的区别：启动时不需要往临时目录解压 14MB，也就没有
"Could not create temporary directory!" / "Failed to extract ... Permission denied"
这类问题；启动也快。
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent          # build/ 的上一级 = 项目根
APP = ROOT / "app"
TOOLS = ROOT / "tools"
SERVER = ROOT / "server"

HIDDEN = [
    "db", "paths", "engine", "llm", "chat", "bot", "service", "remote", "ui", "main",
    "planner", "retrieval", "build_reading", "extract",
    "api",        # localserver 按名字延迟导入 server/api.py，静态分析看不到
]

DATAS = []
BINARIES = []
# pypdfium2：扫描版 PDF 渲染 + AES 加密 PDF 解密/抽字。它是二进制包，必须把
# pdfium.dll 一起带上；extract.py 里是延迟导入，所以 hiddenimports 也要补。
for _mod in ("pypdfium2", "pypdfium2_raw"):
    try:
        _d, _b, _h = collect_all(_mod)
    except Exception:                 # 没装就跳过，打包版退化为「PDF 只能读文字层」
        continue
    DATAS += _d
    BINARIES += _b
    HIDDEN += _h
HIDDEN += ["pypdfium2", "pypdfium2_raw"]
# pypdf 是 PDF 抽取的唯一第三方依赖，仓库里随 tools/libs 带了一份纯 Python 的。
# 不打包进去 → 打包版处理 PDF 会报 ModuleNotFoundError: No module named 'pypdf'
# （2026-10-31 用户遇到的 34 份 PDF 全失败就是这个）。
_libs = TOOLS / "libs"
if _libs.is_dir():
    DATAS.append((str(_libs), "libs"))
# 兜底：tools/libs/pypdf 这份仓库自带的已经不可用（2026-10-31 发现目录被删/读不了），
# 那就把 pip 装的那份 pypdf 当模块打进去（落在 _internal/pypdf），extract.py 照样能 import。
HIDDEN += ["pypdf"]
_seed = ROOT / "tests" / "fixtures" / "seed_quiz.json"
if _seed.exists():
    DATAS.append((str(_seed), "tests/fixtures"))   # 第一次运行导现成题库用

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
