@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 默认打「目录版」：启动时不解包到 %TEMP%，不会再出现
rem   Could not create temporary directory!
rem   Failed to extract VCRUNTIME140.dll: failed to open target file! (Permission denied)
rem 想打老的「单文件版」（13MB 一个 exe，但有解包隐患）：学习bot-打包.cmd single

set "SPEC=build\学习bot-onedir.spec"
set "OUT=dist\学习bot-app\学习bot.exe"
set "TAG=目录版"
if /i "%~1"=="single" (
  set "SPEC=build\学习bot.spec"
  set "OUT=dist\学习bot.exe"
  set "TAG=单文件版"
)

rem 打包用的 Python 固定 3.14（3.15 是别的东西装的，里面没有 PyInstaller）
set "PY314=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if exist "%PY314%" (set "PY=%PY314%") else (set "PY=py")

echo 正在打包%TAG%（第一次约 1 分钟）...
rem 先清掉旧字节码：用别的 Python 版本跑过测试会留下不兼容的 pyc，
rem 3.14 的 PyInstaller 分析阶段会直接崩（Fatal Python error: Executing a cache）
for /d /r "app" %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul
for /d /r "tools" %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul
"%PY%" -X utf8 -m PyInstaller --noconfirm --clean "%SPEC%"
if errorlevel 1 (
  echo.
  echo 第一次失败，自动重试一次（Python 3.14 + PyInstaller 偶尔会在分析阶段崩）...
  "%PY%" -X utf8 -m PyInstaller --noconfirm --clean "%SPEC%"
)
if errorlevel 1 (
  echo.
  echo 打包失败。看上面最后几行日志。
  pause
  exit /b 1
)

echo.
echo 好了：%OUT%

rem 关键一步：exe 留在工作区里跑不起来（写不了 %TEMP%、%LOCALAPPDATA%、文档），
rem 编译产物必须拷到工作区外再用。
if /i not "%~1"=="single" (
  set "DEPLOY=%USERPROFILE%\学习bot-app"
  echo 正在拷到工作区外：%DEPLOY%
  robocopy "dist\学习bot-app" "%DEPLOY%" /MIR /NFL /NDL /NJH /NJS /NP >nul
  if errorlevel 8 (
    echo 拷贝失败，请手动把 dist\学习bot-app 整个文件夹复制到工作区外再双击。
  ) else (
    echo 双击这个：%DEPLOY%\学习bot.exe
  )
)

echo.
echo 数据不会打包进去；第一次运行会让你选数据文件夹。
pause
