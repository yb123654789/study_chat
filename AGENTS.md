# 学习bot · 项目说明（给 AI 助手看）

## 这是什么

一个**AI 学习项目经理**：用户交资料和考核日期，它拆成学习目标、倒排到每天、每天提醒并小测验收、落后自动重排。
单人使用，手机优先。

## 当前阶段：手机 App（P0 已完成，2026-10-28；下一阶段 P1）

> **2026-10-28 P0 已落地**：Telegram 已摘（默认不跑 bot、不推送；`--bot` 只作应急），
> 桌面「机器人」页换成「待确认」页，key 只在「设置」里填（不再读 `.env`），`review_queue`（schema v5）。
> 手机端方向改为**原生 Android App**，方案 `手机App方案.md`（v5）、施工 `施工计划.md`（P0–P8）。
> 现状、坑、下一步一律以 `HANDOFF.md` 最新一节为准；本节以下关于 Telegram/推送的描述是 P0 之前的。

**形态**：不做网页，不做 PWA，不用 Supabase。
**后端跑在境外 VPS**（宿舍晚上断电断网，本机和家里小主机都会停），桌面软件只做显示和操作。

- 考核日已定：**2027-01-04**（课程表里 `ds` 这门课；目标在 `goals.json` 的 `exam_date`）
- 当前数据目录：**`C:\Users\yb123\Documents\学习bot`**（2026-10-27 搬的；exe 第一次运行选这个）
- 已完成：资料抽取（`tools/extract.py`）→ 59 个目标 → 确定性排程（`tools/planner.py`）
  → **桌面后台**（总览/学习/计划/目标/题库/资料/知识库/待确认/设置/日志 10 页）
  → **带练聊天**（`app/chat.py`：会话落库、状态条、检索引用、工具调用，两端共用）
  → **服务端**（`server/run.py` + `server/api.py`：HTTP API；Telegram 降为 `--bot` 应急）
  → **瘦客户端**（`app/remote.py`：桌面所有读写走 HTTP，10 页跑通）
- 自检：`app\selftest.py`（125 项）、`app\uismoke.py`（14 项：10 页 + 待确认 + key 提示 + 课程下拉）、`server\test_api.py`（35 项）、`tools/test_planner.py`
- 2026-10-27 补齐：落后自动重排（`engine.auto_replan`）、复习卡不许早于新课、错题回炉（连续两次没过停止换题）、带练上下文预算；细节见 HANDOFF
- 2026-10-27 多课程：`courses` 表（每课自己的考核日），`goals`/`tasks` 带 `course` 列（数据结构 = `ds`），
  按课排程/落后/过滤/推送分组；新课程目标放 `data/goals_<id>.json`，资料放 `materials/<id>/`（`tools/extract.py --course <id>`）
- 2026-10-27 应用化：`app/paths.py` 是**数据目录的唯一入口**（第一次启动选目录；库/资料/备份/导出全在里面），
  `db.SCHEMA_VERSION` + 自动迁移 + 每天自动备份 + 导出 JSON；`build/学习bot-onedir.spec` 打 `dist\学习bot-app\`（PyInstaller 目录版，只打包不跑）
- 2026-10-27 晚**容量不再写死**（用户指出：写死工作日/周末分钟会让重排失效）：
  每天学多少 = 「剩余量 ÷ 剩余天数」，周末只按系数加权（默认 130%）；`courses` 只有
  `weekend_ratio`(100~200) + `day_cap`(0~600，0 = 不限)，schema v4；落后越多每天自动越长。
  **别再往库里存「每天多少分钟」，也别在代码里写死容量。**
- 2026-10-27 晚查清 exe 报错的真因：**编译出的 exe 留在工作区里跑会被工作区限制**，只能写工作区内，
  所以写 `%TEMP%` / `%LOCALAPPDATA%` / `文档` 全被拒（表现为 `Could not create temporary directory!`、
  `Failed to extract VCRUNTIME140.dll: Permission denied`、`PermissionError: location.json`、`unable to open database file`）。
  **编译产物必须拷到工作区外再用**：`学习bot-打包.cmd` 打完自动拷到 `%USERPROFILE%\学习bot-app`。
  打包形式同时改成**目录版** `dist\学习bot-app\`（启动不解包，快）；单文件 spec 留着备用
- **还没验证**：VPS 上的部署、Android App（P1 起）—— 本地联调已通过；Telegram 真机联调已随 P0 取消
- 下一步：按 `施工计划.md` 做 P1（Android 骨架 + 同步 + 四页只读）；开工前缺手机型号/安卓版本、同 WiFi 可达 + 防火墙 8787（JDK 17 + Android SDK 已装好：`C:\Android\Sdk`）
- 大作业（C++ 排序对比实验）用户已确认不进计划，不要自作主张加

## 目录

| 路径 | 内容 |
|---|---|
| `HANDOFF.md` | **新会话先读这个**：当前状态、已定的架构决定、下次开工第一件事 |
| `PRD.md` | 需求定稿 v3（形态部分已被 HANDOFF 的「形态已改」覆盖） |
| `app/` | **桌面软件本体（瘦客户端）**：`main.py` 入口、`ui.py` 界面、`remote.py` 远程客户端、`db.py` SQLite、`engine.py` 业务、`chat.py` 带练引擎、`llm.py` 模型、`bot.py` Telegram、`service.py` 线程 |
| `server/` | **服务端（跑在 VPS）**：`run.py` 入口、`api.py` HTTP 接口、`test_api.py` 自检、`README.md` 部署步骤、`studybot.service`、`backup.sh` |
| `app/README.md` | 怎么开、第一次怎么配、数据在哪、已知限制 |
| `学习bot.cmd` | 开发用启动器（pythonw）；日常用 `%USERPROFILE%\学习bot-app\学习bot.exe`（工作区外那份） |
| `学习bot-打包.cmd` + `build/学习bot-onedir.spec` | 打 exe（PyInstaller 目录版，默认）；`学习bot-打包.cmd single` 走 `build/学习bot.spec` 打单文件版 |
| `prompts/plan_v1.md` | 拆目标 / 出题 / 批改的提示词与降级策略 |
| `materials/` | 用户放的原始资料（只读，不要改动或删除）；`materials/inbox/` 是补料入口 |
| `web/` | 旧 PWA 路线，**不再维护**，别再往上加功能 |
| `archive/v1-本地版/` | 上一版桌面工具，**不要再改**，只作参考 |

## 铁律

1. **模型负责语义，代码负责时间**：排程、顺延、重排一律用算法算，绝不让模型输出日期。
2. **完成 = 小测通过**，不是打卡。批改要严格，答不出别给过。
3. **每次重排必须说明牺牲了什么**，写进 `plan_note` / 日志，并出现在当天推送里。
4. 改动数据库结构、删除数据、覆盖 `materials/` 内容前，先问用户。
5. 手机端（Android App，P1 起）：一条消息、一个按钮就要能懂，不要求用户记命令。Telegram 已在 P0 摘掉。

## 技术栈（已定）

- 电脑端：**Python 3.10+ / Tkinter 单窗口**，只用标准库（打包用 PyInstaller，只是构建工具）
- 数据：**SQLite，一个数据目录**（`app/paths.py` 决定；桌面端/服务器都走它），资料、备份、导出都在同一目录
- 分发：**`%USERPROFILE%\学习bot-app\学习bot.exe`**（目录版，exe + `_internal` 共 30MB，整文件夹拷/压 zip）。
  重新打包双击 `学习bot-打包.cmd`，它会自动拷到工作区外——**别在工作区里直接双击 exe，会被限制写不了 %TEMP%/文档**。
  单文件版（`build/学习bot.spec` + `学习bot-打包.cmd single`）保留但不推荐
- 手机端：**原生 Android App**（方案 `手机App方案.md`，P1 起）；Telegram 只留 `--bot` 应急，webhook 不需要、域名不需要
- 定时：**P0 起不推送**；只有 `server/run.py --bot` 应急模式才恢复 07:00 / 21:30
- 模型：DeepSeek 级（带练、出题、批改），**key 只认「设置」里填的**（P0 起不再读 `.env`）
- 处理位置：PDF 解析在电脑上做（`tools/extract.py`），抽好的文本传到服务器；检索（992 块）、排程在服务器

## 说话方式

- 中文，短句，先给结论再给理由。
- 不吹进度：没验证的东西说"还没验证"。
- 涉及钱（API、服务器）要给数字区间，不要含糊说"不贵"。
