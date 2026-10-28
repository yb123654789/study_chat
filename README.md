# 学习bot · study_chat

一个**AI 学习项目经理**：交资料和考核日期，它拆成学习目标、倒排到每天、每天提醒并小测验收、落后自动重排。
单人使用，手机优先。

- 电脑端：Python + Tkinter 单窗口（同时可做服务端，HTTP API 给手机用）
- 手机端：原生 Android（Kotlin，View/XML + Room + OkHttp）
- 模型：DeepSeek（聊天/出题/批改；key 只在手机上加密存，或电脑「设置」里填）
- 数据：SQLite，一个数据目录；仓库**不含**任何个人数据、学习资料、数据库、备份和密钥

## 下载

最新安装包在 [Releases](https://github.com/yb123654789/study_chat/releases)：

- `StudyBot-desktop-win64.zip`：解压后双击 `学习bot-app\学习bot.exe`，第一次选数据目录
- `StudyBot-android-v0.1.0.apk`：Android 7+，安装时允许未知来源

## 手机上怎么连电脑

1. 电脑上跑服务端（默认只开 HTTP API，不跑 Telegram、不推送）：

   ```bash
   py -X utf8 server\run.py --port 8787 --bind 0.0.0.0
   ```

   首次启动会在数据目录生成 `server.json`，里面是访问 token。

2. 手机和电脑连同一个 WiFi，手机 App「设置」里填：

   - 服务器地址：`http://<电脑局域网IP>:8787`
   - 访问 token：`server.json` 里那串
   - DeepSeek key（聊天/出题/纠错才用，只存手机本地加密存储）

3. 点「保存并立即同步」。之后打开 App 会自动试同步，顶部显示「上次同步：x 分钟前」。

> 电脑防火墙要放行 8787 入站 TCP；如果同 WiFi 连不上，可以先用 USB 调试 + `adb reverse tcp:8787 tcp:8787`，手机上地址填 `http://127.0.0.1:8787`。

## 目录

| 路径 | 内容 |
|---|---|
| `app/` | 桌面软件（Tkinter）：界面、SQLite、排程、带练聊天、远程客户端、自检 |
| `server/` | 服务端：HTTP API、`/api/mobile/snapshot`（手机下行）、`/api/mobile/events`（手机上行）、部署说明 |
| `android/` | Android 工程：六个 tab（今日/计划/目标/题库/聊天/笔记）、本地 Room、检索、出题判分、语音、OCR、冲突裁定 |
| `tools/` | 资料抽取、检索、确定性排程（planner）、自检 |
| `prompts/` | 拆目标 / 出题 / 批改的提示词与降级策略 |
| `施工计划.md` | 手机 App P0–P8 的任务清单、验收与进度看板 |
| `HANDOFF.md` | 交接单：当前状态、已定的架构决定、坑、下一步 |

## 自己构建

桌面端（Windows）：

```powershell
py -3.14 -m pip install pyinstaller
学习bot-打包.cmd
```

Android：

```powershell
cd android
.\gradlew assembleRelease
# 或直接用本机 Gradle：gradle -p android assembleRelease
```

签名配置在 `android/keystore/keystore.properties`（不进仓库；没有这个文件时 release 构建不签名）。

## 自检

```powershell
py -X utf8 app\selftest.py        # 桌面端 + 数据层，125 项
py -X utf8 app\uismoke.py         # 10 个页面冒烟
py -X utf8 server\test_api.py     # 服务端/手机接口，54 项
py -X utf8 tools\test_planner.py  # 排程
```

## 已知限制

- **真机验收还没跑完**：方案 §16 的 13 条（飞行模式、待确认、语音、OCR 等）需要真手机，清单在 `HANDOFF.md`。
- 安卓 APK 约 48–50 MB（内置中文 OCR 模型）；要瘦身可只保留 `arm64-v8a`。
- 手机本地的出题题目存在内存里，退出 App 会重新出题。
- 服务端暂不存裁决表，冲突裁定只回传 `settings` + 日志，快照里不下发。
