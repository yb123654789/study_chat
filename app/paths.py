#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路径规则：代码只读，数据只在一个地方。

    <数据目录>/
        studybot.db          SQLite（唯一可写数据库）
        desktop.json         瘦客户端连服务器用的地址+token（可选）
        .env                 （旧的 DEEPSEEK_API_KEY 位置；P0 起不再读，key 只认「设置」）
        goals.json           数据结构的目标来源（导入用）
        goals_<课程>.json     其他课程的目标来源
        materials/           原始资料（用户放，程序只读，只在 inbox 下建目录）
        extracted/           抽出来的文本（程序写，检索读）
        backups/             每天自动备份的库
        exports/             导出给人看的 JSON
        .app.lock            单实例锁

数据目录的确定顺序：
    1. 环境变量 STUDYBOT_HOME（测试/脚本用）
    2. 本机配置 %LOCALAPPDATA%\\学习bot\\location.json（第一次启动时选的那次）
    3. 开发模式（没打包）：项目里的 data/（沿用老路径，不打扰开发）
    4. 以上都没有（打包后第一次跑）：让用户选，见 ensure_for_startup()
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "学习bot"
FROZEN = bool(getattr(sys, "frozen", False))

# 代码/资源所在目录：打包后是 PyInstaller 解包目录，开发时是仓库根
CODE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))

_cached: Path | None = None
_asked = False


# ------------------------------------------------------------------ 配置
def config_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_NAME


def config_file() -> Path:
    return config_dir() / "location.json"


def saved_root() -> Path | None:
    p = config_file()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    v = str(raw.get("data") or "").strip()
    return Path(v) if v else None


def default_root() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.exists() else Path.home()) / "学习bot"


def save_root(path: Path) -> None:
    """记住数据目录。**存不了也不能拦住程序**：大不了下次再选一次。

    （踩过的坑：%LOCALAPPDATA% 写不进去时，这里抛 PermissionError 直接把整个启动打断，
    明明数据目录本身是可写的。）
    """
    p = config_file()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"data": str(path)}, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except OSError:
        pass


# ------------------------------------------------------------------ 解析
def data_root() -> Path | None:
    """当前数据目录；还没定（打包后第一次跑）时返回 None。"""
    global _cached
    if _cached is not None:
        return _cached
    env = os.environ.get("STUDYBOT_HOME", "").strip()
    if env:
        _cached = Path(env)
        return _cached
    saved = saved_root()
    if saved is not None:
        _cached = saved
        return _cached
    if not FROZEN:                       # 开发：还用仓库里的 data/
        _cached = CODE_ROOT / "data"
        return _cached
    return None


def use_root(path: str | Path, save: bool = True) -> Path:
    """切换数据目录（第一次启动选的、或者脚本里临时指定）。"""
    global _cached
    p = Path(path).expanduser().resolve()
    _cached = p
    if save:
        save_root(p)
    ensure()
    return p


def ensure(root: Path | None = None) -> Path:
    r = Path(root or data_root() or default_root())
    for sub in ("materials", "extracted", "backups", "exports", "知识库", "回收站"):
        (r / sub).mkdir(parents=True, exist_ok=True)
    return r


def ensure_for_startup(ask_ui=None) -> Path:
    """开窗前把数据目录定下来：老用户直接用，新用户问一次。"""
    root = data_root()
    if root is not None:
        return ensure(root)
    chosen = None
    if ask_ui is not None:
        chosen = ask_ui()             # 界面对话框：返回 Path 或 None
    return use_root(chosen or default_root())


# ------------------------------------------------------------------ 子路径
def _r() -> Path:
    r = data_root()
    return r if r is not None else default_root()


def db_file() -> Path:
    return _r() / "studybot.db"


def goals_file(course: str = "ds") -> Path:
    p = _r() / f"goals_{course}.json"
    return p if course != "ds" or p.exists() else _r() / "goals.json"


def goals_json() -> Path:
    return _r() / "goals.json"


def goals_out(course: str = "ds") -> Path:
    """写目标时该落在哪个文件：数据结构 = goals.json，其他课 = goals_<id>.json。

    和 goals_file() 的区别：那个是「读」，没有本课文件时会退到 goals.json（导入用）；
    这个是「写」，课程一开始就要有自己的文件，不能把别的课的文件覆盖掉。
    """
    return _r() / ("goals.json" if course == "ds" else f"goals_{course}.json")


def materials_dir(course: str | None = None) -> Path:
    """原始资料目录。开发模式下沿用仓库根的 materials/（老布局），
    打包后的应用统一放在数据目录里。"""
    base = _r() / "materials"
    if not base.exists() and _r() == CODE_ROOT / "data" and (CODE_ROOT / "materials").exists():
        base = CODE_ROOT / "materials"
    return base / course if course and course != "ds" else base


def extracted_dir(course: str | None = None) -> Path:
    base = _r() / "extracted"
    if not base.exists() and _r() == CODE_ROOT / "data" and (CODE_ROOT / "extracted").exists():
        base = CODE_ROOT / "extracted"
    return base / course if course and course != "ds" else base


def pending_dir(course: str | None = None) -> Path:
    """待拆：处理 inbox 之后、还没拆目标的 txt（按课程分，一个文件夹就是队列）。"""
    return _r() / "extracted" / "待拆" / (course or "ds")


def done_dir(course: str | None = None) -> Path:
    """已拆：拆过目标的 txt 挪到这里；检索仍然扫得到。"""
    return _r() / "extracted" / "已拆" / (course or "ds")


# ---------------------------------------------------------------- 一级 / 二级
def knowledge_dir(course: str | None = None) -> Path:
    """二级数据：**一个知识库根，不分科目**（2026-10-31 改）。

    每个目标一个 .md，按「章」分目录；科目只写在 front matter 的 `course:` 里，
    为以后的跨课程知识树留位置。course 参数保留只是为了调用点不用全改，
    已经不参与路径——传什么都返回同一个根。
    """
    return _r() / "知识库"


def trash_dir() -> Path:
    """一级原始资料处理成功后挪到这里（不是硬删，能翻回来）。"""
    return _r() / "回收站"


def tools_dir() -> Path:
    """可选外部工具（tesseract / ffmpeg / whisper / 模型）放这。

    放 LOCALAPPDATA 而不是数据目录：这些是「程序的一部分」，换数据目录不用重下。
    **目录名必须是纯英文**：whisper.cpp 在 Windows 打不开中文路径（模型/音频都会 load 失败）。
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) if base else _r()
    return root / "studybot-tools"


def manifest_file() -> Path:
    """一级资料台账：进过什么、处理成什么、原件挪哪去了。纯 JSON。"""
    return _r() / "原始资料清单.json"


def backups_dir() -> Path:
    return _r() / "backups"


def exports_dir() -> Path:
    return _r() / "exports"


def env_file() -> Path:
    p = _r() / ".env"
    if not p.exists() and (CODE_ROOT / ".env").exists():
        return CODE_ROOT / ".env"        # 老布局：key 放在项目根
    return p


def desktop_config() -> Path:
    return _r() / "desktop.json"


def server_config() -> Path:
    return _r() / "server.json"


def lock_file() -> Path:
    return _r() / ".app.lock"


def rel(p: Path | str) -> str:
    """把路径变成库里存的相对路径（materials/... / extracted/...）。"""
    p = Path(str(p))
    for base in (_r(), CODE_ROOT):
        try:
            return str(p.resolve().relative_to(base.resolve()))
        except ValueError:
            continue
    return str(p)
