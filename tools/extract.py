#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 materials/ 下的资料抽成纯文本，供后续拆解目标使用。

PDF 有时会让解析库直接崩溃（段错误），所以每个 PDF 都放进子进程跑：
崩了就跳过并记下来，不影响其它文件。

用法：
    python tools/extract.py                 # 全部
    python tools/extract.py --one <path>    # 只处理一个（内部用）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# pypdf 不靠 pip：仓库里带了一份纯 Python 的，放在 tools/libs/pypdf。
# 开发时 __file__ 在 tools/ 下；打包后 __file__ 在 _internal/ 下（spec 的 DATAS
# 把 tools/libs 拷到 _internal/libs），所以两个位置都要试。
_HERE = Path(__file__).resolve().parent
for _p in (_HERE / "libs",) + ((Path(sys._MEIPASS) / "libs",) if getattr(sys, "_MEIPASS", "") else ()):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "materials" / "scut" / "大二上课程资料" / "数据结构"
OUT = BASE / "extracted"
BOOK_PAGE_CAP = 60          # 大部头教材只抽前 60 页，够看结构
PDF_PAGE_CAP = 400
OCR_DPI = 200               # 扫描版 PDF 渲染分辨率（tesseract 最舒服的档）
OCR_PAGE_CAP = 120          # 整份没文字层的，最多 OCR 这么多页（防止几百页扫描件跑一整天）
OCR_MISSING_CAP = 40        # 有文字层、只有少数页缺字的，最多补 OCR 这么多页
OCR_MIN_TEXT = 60           # 一页文字少于这么多字，就当「可能只有水印/图」补一次 OCR
OFFICE_TIMEOUT = 240        # Word/PowerPoint 另存为的等待上限（秒）

# 能直接读出文字的扩展名（.doc/.ppt 要先经 Office/LibreOffice 转成 docx/pptx）
TEXT_EXT = {".pdf", ".docx", ".pptx", ".md", ".txt", ".doc", ".ppt"}


def clean(s: str) -> str:
    return "\n".join(line.rstrip() for line in (s or "").splitlines()).strip()


def pptx_text(p: Path) -> str:
    """只用标准库解析 pptx（zip + XML），避开 lxml 的稳定性问题。"""
    import re
    import zipfile
    from xml.etree import ElementTree as ET

    A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    z = zipfile.ZipFile(p)
    names = [n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
    names.sort(key=lambda n: int(re.findall(r"\d+", n)[-1]))
    out = []
    for i, n in enumerate(names, 1):
        root = ET.fromstring(z.read(n))
        texts = []
        for para in root.iter(A + "p"):
            t = "".join(x.text or "" for x in para.iter(A + "t")).strip()
            if t:
                texts.append(t)
        if texts:
            out.append(f"## Slide {i}\n" + "\n".join(texts))
    return "\n\n".join(out)


def docx_text(p: Path) -> str:
    """同样只用标准库解析 docx。"""
    import zipfile
    from xml.etree import ElementTree as ET

    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    z = zipfile.ZipFile(p)
    root = ET.fromstring(z.read("word/document.xml"))
    out = []
    for para in root.iter(W + "p"):
        t = "".join(x.text or "" for x in para.iter(W + "t")).strip()
        if t:
            out.append(t)
    return "\n".join(out)

def _pdfium():
    """PDFium（pypdfium2）：扫描版 PDF 渲染 + AES 加密 PDF 的文本/渲染。

    没装就抛 MissingTool（原件留着），不要让整份资料静默失败。
    """
    try:
        import pypdfium2 as pdfium
    except ImportError as e:
        raise MissingTool(
            "pdfium",
            "扫描版 / 加密 PDF 要 pypdfium2：打包版请重新打包（spec 已带）；"
            "源码运行 py -m pip install pypdfium2") from e
    return pdfium


def _render_ppm(page, dest: Path, dpi: int = OCR_DPI) -> bool:
    """PDF 页 → PPM（P6，tesseract 直接能读）。几乎全白的页返回 False，不浪费 OCR 时间。"""
    try:
        bmp = page.render(scale=dpi / 72, rev_byteorder=True)
    except TypeError:                 # 老版本 pypdfium2 没有 rev_byteorder
        bmp = page.render(scale=dpi / 72)
    try:
        buf = bytes(bmp.buffer)
        if not buf:
            return False
        if getattr(bmp, "mode", "RGB") != "RGB" and getattr(bmp, "n_channels", 3) == 3:
            arr = bytearray(buf)      # BGR → RGB（逐字节切片，C 速度）
            arr[0::3], arr[2::3] = arr[2::3], arr[0::3]
            buf = bytes(arr)
        if min(buf) > 240:            # 空白页
            return False
        with open(dest, "wb") as f:
            f.write(f"P6\n{bmp.width} {bmp.height}\n255\n".encode("ascii"))
            f.write(buf)
        return True
    finally:
        bmp.close()


def _ocr_ppm(ppm: Path, tesseract: str = "", tessdata: str = "") -> str:
    """对一张图跑 tesseract：先中英混排，缺中文语言包就退回英文。"""
    exe = _tool(tesseract, "tesseract")
    for lang in ("chi_sim+eng", "eng"):
        cmd = [exe, str(ppm), "stdout", "-l", lang]
        if tessdata and Path(tessdata).is_dir():
            cmd += ["--tessdata-dir", str(tessdata)]
        r = run_tool(cmd, timeout=300)
        if r.returncode == 0:
            got = clean(r.stdout or "")
            if got:
                return got
    return ""


def _ocr_pdf_page(page, tesseract: str = "", tessdata: str = "") -> str:
    tmp = Path(tempfile.mkdtemp(prefix="studybot-ocr-"))
    try:
        ppm = tmp / "p.ppm"
        if not _render_ppm(page, ppm):
            return ""
        return _ocr_ppm(ppm, tesseract, tessdata)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pdf_text(p: Path, tools: dict | None = None, on_progress=None) -> str:
    """PDF → 文本：先 pypdf；AES/解析不了就换 PDFium；没文字层的页再 OCR。

    - 正常 PDF 完全不碰 OCR（不慢）
    - 扫描版：PDFium 把页渲染成图 → tesseract（chi_sim+eng）
    - AES 加密（空口令）：PDFium 能解密并抽字；Word 打不开这种文件
    """
    t = tools or {}
    cap = BOOK_PAGE_CAP if p.stat().st_size > 15 * 1024 * 1024 else PDF_PAGE_CAP
    pages: dict[int, str] = {}
    total = 0
    doc = None
    try:
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(p))
            total = len(reader.pages)
            for i in range(min(total, cap)):
                try:
                    tx = clean(reader.pages[i].extract_text() or "")
                except Exception:  # noqa: BLE001 单页坏了不影响其它页
                    tx = ""
                if tx:
                    pages[i] = tx
        except ImportError as e:      # 打包漏了 tools/libs 时会走到这里
            raise MissingTool(
                "pypdf",
                "PDF 解析缺 pypdf：打包版要把 tools/libs 一起打进去（spec 已加）；"
                "源码运行可用 tools/libs/pypdf 或 py -m pip install pypdf") from e
        except Exception:             # noqa: BLE001  AES 加密 / 解析库报错 → PDFium
            doc = _pdfium().PdfDocument(str(p))
            total = len(doc)
            for i in range(min(total, cap)):
                try:
                    tp = doc[i].get_textpage()
                    tx = clean(tp.get_text_range() or "")
                    tp.close()
                except Exception:  # noqa: BLE001
                    tx = ""
                if tx:
                    pages[i] = tx

        # 文字很少的页也要补 OCR：扫描书常带一层「关注公众号」水印，
        # pypdf/PDFium 每页只能抽出那几十个字，正文其实全是图。
        missing = [i for i in range(min(total, cap))
                   if len(pages.get(i, "")) < OCR_MIN_TEXT]
        if missing:
            limit = OCR_PAGE_CAP if not pages else OCR_MISSING_CAP
            todo = missing[:limit]
            if doc is None:
                try:
                    doc = _pdfium().PdfDocument(str(p))
                except MissingTool:
                    if not pages:       # 一个字都没抽到，又没 PDFium：只能留原件
                        raise
                    doc = None          # 已有文字：跳过 OCR，别把整份判成缺工具
            for n, i in enumerate(todo, 1):
                if doc is None:
                    break
                if on_progress:
                    on_progress(n, len(todo))
                try:
                    tx = _ocr_pdf_page(doc[i], t.get("tesseract", ""), t.get("tessdata", ""))
                except MissingTool:
                    if not pages:       # 纯扫描件又没 tesseract：留原件
                        raise
                    break               # 有文字，OCR 不可用就到此为止
                except Exception:  # noqa: BLE001 单页 OCR 失败不拖垮整份
                    tx = ""
                if tx and len(tx) > len(pages.get(i, "")):   # 水印页用 OCR 的正文替换
                    pages[i] = tx
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:  # noqa: BLE001
                pass

    if not pages:
        raise ValueError("没抽出文字（扫描件/空文件，OCR 也没有结果）")
    out = [f"## p.{i + 1}\n{pages[i]}" for i in sorted(pages)]
    if total > cap:
        out.append(f"\n…（共 {total} 页，只抽了前 {cap} 页）")
    return "\n\n".join(out)


# ------------------------------------------------------------------ .doc / .ppt
# 老格式（Word 97-2003 / PowerPoint 97-2003）没有可用的纯 Python 解析器：
#   - 有 LibreOffice：headless 转 docx/pptx；
#   - Windows 上有 Office：后台起隐藏的 Word/PowerPoint 另存为 docx/pptx；
# 转出来的文件用现成的标准库解析（docx_text / pptx_text），然后删临时文件。
_PS_CONV = r"""
param([string]$Src, [string]$Dst, [string]$Kind)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$app = $null
$doc = $null
try {
  if ($Kind -eq "doc") {
    $app = New-Object -ComObject Word.Application
    $app.Visible = $false
    $app.DisplayAlerts = 0
    try { $app.AutomationSecurity = 3 } catch { }
    $doc = $app.Documents.Open($Src, $false, $true, $false)
    $doc.SaveAs2($Dst, 12)
    $doc.Close(0)
    $doc = $null
  } else {
    $app = New-Object -ComObject PowerPoint.Application
    $doc = $app.Presentations.Open($Src, $true, $false, $false)
    $doc.SaveAs($Dst, 24)
    $doc.Close()
    $doc = $null
  }
  Write-Output "OK"
} finally {
  if ($null -ne $doc) { try { $doc.Close() } catch { } }
  if ($null -ne $app) { try { $app.Quit() } catch { } }
}
"""


def find_soffice() -> str:
    """找 LibreOffice 的 soffice.exe（设置里没填也不要紧，装了就能用）。"""
    found = shutil.which("soffice") or shutil.which("soffice.exe")
    if found:
        return found
    bases = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
             os.environ.get("LOCALAPPDATA")]
    rels = (r"LibreOffice\program\soffice.exe", r"Programs\LibreOffice\program\soffice.exe")
    for base in bases:
        if not base:
            continue
        for rel in rels:
            cand = Path(base) / rel
            if cand.is_file():
                return str(cand)
    return ""


def office_available() -> str:
    """本机能不能转 .doc/.ppt：先 LibreOffice，再 Windows 上的 Office。返回一句说明。"""
    soffice = find_soffice()
    if soffice:
        return f"LibreOffice：{soffice}"
    if os.name == "nt":
        import winreg
        for exe in ("winword.exe", "powerpnt.exe"):
            key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        p = winreg.QueryValue(k, "")
                    if p and Path(p).is_file():
                        return f"Office：{p}"
                except OSError:
                    continue
    return ""


def _office_pids(image: str) -> set[int]:
    """当前 WINWORD/POWERPNT 进程号（只用来判断「用户是不是开着」和清理自己起的）。"""
    try:
        r = run_tool(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                     timeout=20)
    except Exception:  # noqa: BLE001
        return set()
    out: set[int] = set()
    for line in (r.stdout or "").splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            out.add(int(parts[1]))
    return out


def _kill_pids(pids: set[int]) -> None:
    for pid in pids:
        try:
            run_tool(["taskkill", "/PID", str(pid), "/F"], timeout=20)
        except Exception:  # noqa: BLE001
            pass


def _office_convert(p: Path, target: str) -> Path:
    """把 .doc/.ppt 另存成 .docx/.pptx，返回临时文件（调用方负责删它的目录）。"""
    tmp = Path(tempfile.mkdtemp(prefix="studybot-office-"))
    dst = tmp / (p.stem + target)
    try:
        soffice = find_soffice()
        if soffice:
            fmt = target.lstrip(".")
            try:
                r = run_tool([soffice, "--headless", "--norestore", "--convert-to", fmt,
                              "--outdir", str(tmp), str(p)], timeout=OFFICE_TIMEOUT)
            except subprocess.TimeoutExpired as e:
                raise MissingTool("办公软件", "LibreOffice 转换超时（文档可能有密码/弹窗）") from e
            if dst.is_file():
                return dst
            raise RuntimeError(f"LibreOffice 没转出来：{(r.stderr or r.stdout or '')[:200]}")
        if os.name != "nt":
            raise MissingTool("办公软件", "读 .doc/.ppt 需要 LibreOffice（Linux/macOS 请先安装）")

        kind = "doc" if target == ".docx" else "ppt"
        image = "WINWORD.EXE" if kind == "doc" else "POWERPNT.EXE"
        before = _office_pids(image)
        if before:
            name = "Word" if kind == "doc" else "PowerPoint"
            raise MissingTool(
                "Office 正在运行",
                f"先关掉 {name} 再处理 .doc/.ppt（不关的话可能把你正在编辑的文档一起关掉）")
        script = tmp / "conv.ps1"
        script.write_text(_PS_CONV, encoding="utf-8")
        try:
            r = run_tool(["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
                          "-File", str(script), "-Src", str(p), "-Dst", str(dst),
                          "-Kind", kind], timeout=OFFICE_TIMEOUT)
        except subprocess.TimeoutExpired as e:
            _kill_pids(_office_pids(image) - before)
            raise MissingTool("办公软件", "Word/PowerPoint 转换超时（可能有弹窗/密码）") from e
        if dst.is_file():
            return dst
        _kill_pids(_office_pids(image) - before)
        msg = ((r.stderr or "") + (r.stdout or "")).strip().replace("\r", " ")
        raise RuntimeError(f"Office 转换失败：{msg[:200] or '没有输出'}")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def doc_text(p: Path, tools: dict | None = None) -> str:
    """.doc（Word 97-2003）→ docx → 文本。"""
    f = _office_convert(p, ".docx")
    try:
        return docx_text(f)
    finally:
        shutil.rmtree(f.parent, ignore_errors=True)


def ppt_text(p: Path, tools: dict | None = None) -> str:
    """.ppt（PowerPoint 97-2003）→ pptx → 文本。"""
    f = _office_convert(p, ".pptx")
    try:
        return pptx_text(f)
    finally:
        shutil.rmtree(f.parent, ignore_errors=True)


# ------------------------------------------------------------------ 图片 / 音视频
# 这两类靠本机外部程序，不引入 Python 依赖：
#   图片 → tesseract（OCR）
#   音视频 → ffmpeg 抽音 + whisper.cpp 转写
# 没装就抛 MissingTool，由上层把原件留着、记成「缺工具」，不删、不瞎猜。
class MissingTool(RuntimeError):
    """本机缺某个外部程序（tesseract / ffmpeg / whisper）。"""

    def __init__(self, tool: str, hint: str):
        super().__init__(f"缺工具 {tool}：{hint}")
        self.tool = tool
        self.hint = hint


IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".webm", ".m4v", ".ts"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".opus"}


def kind_of(p: Path) -> str:
    """text / image / video / audio / other。"""
    ext = p.suffix.lower()
    if ext in TEXT_EXT:
        return "text"
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return "other"


def _tool(cmd: str | None, name: str) -> str:
    """找外部程序：设置里填了就用，没填就按 PATH 找。"""
    if cmd and Path(cmd).is_file():
        return str(cmd)
    import shutil
    found = shutil.which(cmd or name)
    if not found:
        raise MissingTool(name, f"没找到 {name}，先装上，或在「设置」里填它的完整路径")
    return found


# Windows 下不弹黑框：打包成无控制台的 exe 后，子进程默认会弹一个控制台窗口，
# 用户一关就把任务杀了（表现为「工具缺失」）。CREATE_NO_WINDOW 让它完全隐藏。
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def run_tool(cmd: list[str], timeout: float | None = None):
    """统一跑外部工具：抓输出、隐藏窗口、不设超时（传 None）。"""
    kw = {}
    if _NO_WINDOW:
        kw["creationflags"] = _NO_WINDOW
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw["startupinfo"] = si
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, **kw)


def image_text(p: Path, tesseract: str = "", tessdata: str = "") -> str:
    """OCR：tesseract <图片> stdout（先试中英混排，不行退回英文）。

    tessdata：自定义语言包目录（装不到 Program Files 时，中文包可以放用户目录）。
    """
    exe = _tool(tesseract, "tesseract")
    for lang in ("chi_sim+eng", "eng"):
        cmd = [exe, str(p), "stdout", "-l", lang]
        if tessdata and Path(tessdata).is_dir():
            cmd += ["--tessdata-dir", str(tessdata)]
        r = run_tool(cmd, timeout=300)
        if r.returncode == 0 and (r.stdout or "").strip():
            return f"## OCR {p.name}\n" + clean(r.stdout)
        if r.returncode == 0:
            return ""
    raise MissingTool("tesseract", "OCR 跑失败（多半是缺 chi_sim 中文语言包）")


CHUNK_SEC = 600          # 音视频按 10 分钟切片：WAV 有 4GB 上限，长视频一口气抽会失败


def duration_sec(p: Path, ffmpeg: str) -> float:
    """用 ffprobe（和 ffmpeg 同目录）读时长；读不到返回 0。"""
    ff = Path(ffmpeg)
    probe = str(ff.with_name("ffprobe.exe")) if ff.name.lower() == "ffmpeg.exe" else "ffprobe"
    if not Path(probe).is_file():
        import shutil
        probe = shutil.which("ffprobe") or ""
    if not probe:
        return 0.0
    r = run_tool([probe, "-v", "error", "-show_entries", "format=duration",
                   "-of", "default=nw=1:nk=1", str(p)], timeout=120)
    try:
        return float((r.stdout or "").strip() or 0)
    except ValueError:
        return 0.0


def media_text(p: Path, ffmpeg: str = "", whisper: str = "", model: str = "",
               parts_dir: Path | None = None, on_progress=None, lang: str = "") -> str:
    """视频/音频：切成 10 分钟一段，每段 ffmpeg 抽 16k wav → whisper 转写。

    - 切片：WAV 超 4GB 会写不出来（76 小时的课能到 8GB），所以不能一口气抽
    - 断点续跑：每段转写结果立刻写进 parts_dir/<序号>.txt，重跑只补没做的段
    - 进度：on_progress(第几段, 总段数)
    """
    import math
    import tempfile

    ffe = _tool(ffmpeg, "ffmpeg")
    wse = _tool(whisper, "whisper-cli")
    if not model or not Path(model).is_file():
        raise MissingTool("whisper 模型", "缺 whisper 的 ggml 模型文件（例如 ggml-base.bin），在「设置」里填路径")
    threads = str(max(1, min(16, (os.cpu_count() or 4))))

    total = duration_sec(p, ffe)
    n = max(1, math.ceil(total / CHUNK_SEC)) if total > 0 else 1
    tmp_owner = None
    if parts_dir is None:
        tmp_owner = tempfile.TemporaryDirectory(prefix="studybot-media-")
        parts_dir = Path(tmp_owner.name)
    parts_dir = Path(parts_dir)
    parts_dir.mkdir(parents=True, exist_ok=True)
    try:
        texts: list[str] = []
        for i in range(n):
            done = parts_dir / f"{i:04d}.txt"
            if done.exists():                        # 这段以前转过（断了续跑）
                texts.append(done.read_text(encoding="utf-8", errors="replace"))
                continue
            if on_progress:
                on_progress(i + 1, n)
            wav = parts_dir / f"{i:04d}.wav"
            cmd = [ffe, "-y", "-loglevel", "error", "-ss", str(i * CHUNK_SEC),
                   "-t", str(CHUNK_SEC), "-i", str(p), "-vn", "-ac", "1", "-ar", "16000",
                   str(wav)]
            # 每段都不设超时：单段最长 10 分钟音频，慢也不会无限久
            r = run_tool(cmd)
            if r.returncode != 0 or not wav.exists():
                raise MissingTool("ffmpeg", f"第 {i + 1}/{n} 段抽音频失败：{clean(r.stderr)[:160]}")
            out_base = parts_dir / f"{i:04d}.out"
            cmd = [wse, "-m", model, "-f", str(wav), "-t", threads]
            if lang:                                  # .en 模型必须显式 -l en
                cmd += ["-l", lang]
            cmd += ["-otxt", "-of", str(out_base)]
            run_tool(cmd)
            got = out_base.with_suffix(".out.txt")
            txt = got.read_text(encoding="utf-8", errors="replace") if got.exists() else ""
            done.write_text(txt, encoding="utf-8")   # 空也写：这段就是静音，别再重跑
            wav.unlink(missing_ok=True)
            got.unlink(missing_ok=True)
            texts.append(txt)
        body = clean("".join(texts))
        if not body:
            raise MissingTool("whisper", "整段没转出文字（可能全是静音/没对白）")
        return f"## 转写 {p.name}\n" + body
    finally:
        if tmp_owner is not None:
            tmp_owner.cleanup()


def extract_any(p: Path, tools: dict | None = None, on_progress=None) -> tuple[str, str]:
    """按类型抽文本，返回 (文本, 类型)。缺工具时抛 MissingTool，由上层保留原件。"""
    t = tools or {}
    kind = kind_of(p)
    if kind == "text":
        return extract_one(p, t, on_progress), kind
    if kind == "image":
        return image_text(p, t.get("tesseract", ""), t.get("tessdata", "")), kind
    if kind in ("video", "audio"):
        return media_text(p, t.get("ffmpeg", ""), t.get("whisper", ""),
                          t.get("whisper_model", ""), t.get("parts_dir"), on_progress,
                          t.get("whisper_lang", "")), kind
    raise MissingTool("文件类型", f"{p.suffix} 目前不支持（支持 PDF/PPTX/DOCX/MD/TXT/图片/音视频）")


def extract_one(p: Path, tools: dict | None = None, on_progress=None) -> str:
    ext = p.suffix.lower()
    if ext == ".pptx":
        return pptx_text(p)
    if ext == ".docx":
        return docx_text(p)
    if ext == ".pdf":
        return pdf_text(p, tools, on_progress)
    if ext == ".doc":
        return doc_text(p, tools)
    if ext == ".ppt":
        return ppt_text(p, tools)
    if ext in (".md", ".txt"):
        return clean(p.read_text(encoding="utf-8", errors="replace"))
    raise ValueError(f"unsupported {ext}")


def course_dirs(cid: str) -> tuple[Path, Path]:
    """课程资料目录：ds 沿用老路径（materials/scut/... → extracted/），
    其他课程用 <数据目录>/materials/<cid>/ → <数据目录>/extracted/<cid>/。

    数据目录由 app/paths.py 决定（打包后在用户选的位置；开发时是仓库 data/）。
    """
    cid = (cid or "").strip()
    if not cid or cid == "ds":
        return SRC, OUT
    try:
        sys.path.insert(0, str(BASE / "app"))
        import paths      # noqa: E402
        return paths.materials_dir(cid), paths.extracted_dir(cid)
    except Exception:     # noqa: BLE001 没装 app 的环境里退回老布局
        return BASE / "materials" / cid, OUT / cid


def main() -> None:
    argv = sys.argv[1:]
    cid = ""
    if "--course" in argv:
        i = argv.index("--course")
        cid = argv[i + 1]
        del argv[i:i + 2]
    src, out = course_dirs(cid)

    if argv and argv[0] == "--one":
        p = Path(argv[1])
        txt = extract_one(p)
        rel = p.relative_to(src)
        dst = out / rel.with_suffix(".txt")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(txt, encoding="utf-8")
        print(len(txt), flush=True)
        return

    out.mkdir(parents=True, exist_ok=True)
    total, rows, skipped = 0, [], []
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        ext = p.suffix.lower()
        if ext not in TEXT_EXT:
            skipped.append(str(rel))
            continue

        try:
            if ext == ".pdf":
                # 子进程隔离：解析库崩了也不会拖垮整轮
                cmd = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--one", str(p)]
                if cid:
                    cmd += ["--course", cid]
                r = run_tool(cmd, timeout=600)
                if r.returncode != 0:
                    skipped.append(f"{rel}  (解析崩溃/超时: {r.returncode})")
                    continue
                n = int((r.stdout or "0").strip().splitlines()[-1] or 0)
                txt = "" if n == 0 else None
                dst = out / rel.with_suffix(".txt")
                if n == 0:
                    skipped.append(f"{rel}  (无文字，可能是扫描件)")
                    continue
                rows.append({"file": str(rel), "chars": n, "out": str(dst.relative_to(out))})
                total += n
                print(f"{n:>8}  {rel}", flush=True)
                continue
            txt = extract_one(p)
        except Exception as e:
            skipped.append(f"{rel}  (失败: {type(e).__name__}: {e})")
            continue

        if not txt.strip():
            skipped.append(f"{rel}  (无文字，可能是扫描件)")
            continue
        dst = out / rel.with_suffix(".txt")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(txt, encoding="utf-8")
        rows.append({"file": str(rel), "chars": len(txt), "out": str(dst.relative_to(out))})
        total += len(txt)
        print(f"{len(txt):>8}  {rel}", flush=True)

    (out / "index.json").write_text(
        json.dumps({"course": cid or "ds", "files": rows, "skipped": skipped,
                    "total_chars": total}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n合计 {total:,} 字符，{len(rows)} 个文件成功，{len(skipped)} 个跳过")
    if skipped:
        print("跳过：")
        for s in skipped:
            print("  -", s)


if __name__ == "__main__":
    main()
