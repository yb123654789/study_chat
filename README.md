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
- `StudyBot-android-x.y.z.apk`：Android 7+，安装时允许未知来源（v0.3.0 约 323 MB，内置离线语音模型）

文档：[项目介绍](项目介绍.md)（功能、架构、怎么跑） · [开发计划](开发计划.md)（进度、待办、坑）

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
| `android/` | Android 工程：单窗口聊天（`ChatActivity`）、本地 Room、检索、出题判分、离线语音（sherpa-onnx + SenseVoice）、笔记/OCR、冲突裁定 |
| `tools/` | 资料抽取、检索、确定性排程（planner）、自检 |
| `prompts/` | 拆目标 / 出题 / 批改的提示词与降级策略 |
| `项目介绍.md` | 功能清单、架构、数据规则、怎么跑、自检 |
| `开发计划.md` | 进度、已验证/未验证、下一步、坑、不能推翻的决定 |

## 自己构建

桌面端（Windows）：

```powershell
py -3.14 -m pip install pyinstaller
学习bot-打包.cmd
```

Android：

```powershell
gradle -p android :app:assembleRelease
```

签名配置在 `android/keystore/keystore.properties`（不进仓库；没有这个文件时 release 构建不签名）。

> **语音模型和 sherpa-onnx AAR 不进仓库**（v0.3.0 起，约 275 MB）。编 APK 前要先放好，
> 下载命令在 [开发计划](开发计划.md) 的「构建前提」一节。没有它们 gradle 一样能编过，
> 但 App 里点「开始」会报模型缺失。

## 自检

```powershell
py -X utf8 app\selftest.py        # 桌面端 + 数据层
py -X utf8 app\uismoke.py         # 10 个页面冒烟
py -X utf8 server\test_api.py     # 服务端/手机接口
py -X utf8 tools\test_planner.py  # 排程
```

## 已知限制

- **真机验收还没跑完**：手机语音、笔记 OCR、待确认等要真手机，清单在 [开发计划](开发计划.md)。
- 安卓 APK **约 323 MB**（v0.3.0：内置 SenseVoice 228 MB + sherpa-onnx 两套 ABI）。语音完全离线，
  不联网、不花钱；代价是包大、首次启动要拷 228 MB 到应用私有目录（手机存储留 500 MB）。
- 语音只有 **arm64 + x86_64** 两个 ABI；x86_64 是给模拟器自测的，发版可去掉（省 45 MB）。
- 手机本地的出题题目存在内存里，退出 App 会重新出题。
- 服务端暂不存裁决表，冲突裁定只回传 `settings` + 日志，快照里不下发。
