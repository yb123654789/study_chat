# 交接单 · 新会话从这里读

> 给「下一个对话」的交接：这个对话里定的事，别重新讨论一遍。
> 需求全文在 `PRD.md`（v3），这里只放**状态、已定的事、下一步**。

---

## 一句话

你交资料和考核日，它每天告诉你就学什么，落后了自动重排；补资料时给计划打补丁，不是重开一版。

---

## 最新：2026-10-30 手机只留自由聊天（v0.2.0）+ 多课总预算 + 资料去重

**用户拍板**：手机只要聊天；一学期不止一门课；资料会分几批入库；语音在真机上要能用。

### 手机端（v0.2.0，APK 已出；MuMu 模拟器验过启动/菜单，**真机没验**）

- 启动页改成 `ChatActivity`（launcher），不再先进「今日」点卡片；旧 6 tab 主页代码还在，但没入口。
- 只留一个自由会话：`ChatService.freeConversation` 复用最近一条 `kind='free'`，不再每次新建。
- 自由聊天上下文：`stateBarAll`（全部课程合起来）+ 今天/明天/后天排好的卡 + 全库检索 `Retrieval.searchFree`；工具只留 `show_plan`。
- 「＋」：考我（先选今天一张卡）/ 系统语音输入 / 看计划 / 语音自检 / 清空；右上：同步 / 设置。
- **右上「同步 / 设置」原来不显示**：`toolbar.inflateMenu` 和 `setSupportActionBar` 一起用时菜单没挂上去；改成 `onCreateOptionsMenu` / `onOptionsItemSelected`，模拟器截图确认可见。
- **首句一堆 null 的根因**：Android `org.json` 对 `content:null` 调 `optString` 返回字符串 `"null"`；SSE 首块（只有 role）和 tool_calls 块都带 null。`DeepSeek.stream` 改 `textOf()` 判 `isNull`；旧消息里只有 null 的渲染时跳过。
- **语音**：原先 `ERROR_INSUFFICIENT_PERMISSIONS` 一律提示「没给麦克风权限」，但真正录音的是系统识别服务，本 App 开了也没用。现在：提示改成「系统语音识别服务没有录音权限（不是本 App 的开关）」+ 「＋ → 语音自检」（本 App 权限 / 识别服务 / TTS / 系统语音面板 / 机型）+ 系统语音输入回退（`RecognizerIntent` 弹系统面板，走输入法/语音助手）。「＋」里另有「系统语音输入」，不用等报错，直接走系统面板。
- 手机不再本地重排（`Syncer` 去掉 `Replan.maybeReplan`）：重排只在服务器做，避免本地插卡被快照覆盖。
- 同步不会再把手机关掉刚答完的卡盖回 todo：`Syncer` 先收集 outbox 里未上传的 `task_state`，写完服务器快照再盖回去，上传成功后才以服务器为准。

### 多课程：每天总预算

- 设置页新增「每天总共学多久」`day_total_cap`（0 = 不限）。
- 只有总和超了才分：按 `1/剩余天数` 权重，分到各课自己需要的日均为止；`planner.schedule(daily_budget=…)` 只压当天目标，预算模式下不再为了凑满多塞一张卡。
- `regenerate` 全部重排时分配；单课重排不抢别的课；`auto_replan` 也按同一预算。
- 实测（DB 副本，两门课，总预算 90）：ds 21 / math 69，实际日均加起来约 98（单张卡不能拆），note 里写明。

### 资料跨批次

- 同名不同内容不再互相覆盖：`第3章.txt` 已被别的 sha1 占用 → `第3章-<sha1前6>.txt`。
- 同名旧版让位：`_doc_key` 去掉 `v2 / 最终 / (1) / 副本` 等尾巴；旧文本改名 `*.old.txt`（不删、可翻回），台账记 `superseded_by`，检索跳过 old。
- 块级：`build_reading.load_blocks` 按正文 sha1 去重（PPT 和教材同一段只留一份），994 → 990 块。
- 目标级：拆目标提示词带已有验收点；`_clean_goals` 除归一化标题外，按 bigram 覆盖率 ≥0.85 判近似（刻意设高，避免把「顺序存储 / 链式存储」合并）；跳过的写日志和 note。
- **没做**：改了名、又完全不像的新版本（无法可靠判断同一份）；近重复块只做了完全相同，没做相似度合并。

### 桌面

- 设置页最上面「手机连接」：地址（有 `desktop.json` 用它，否则本机 LAN IP:8787）+ token + 复制 + 刷新；来源和「地址为什么会变、token 什么时候才会变」写在卡里。

### 验证 / 没验证

- `tools/test_planner.py`、`tools/test_budget.py`、`app/selftest.py`（129）、`server/test_api.py`（55）、`app/uismoke.py`（15）全过；预算分配和资料版本用临时库各跑了一次集成。
- APK 只编译通过 + `aapt2` 确认 launcher 是 `ChatActivity`、versionName 0.2.0；**MuMu 模拟器（Android 15 / 小米 12 机型）装过并启动：直接进聊天、没有 6 个 tab、右上「同步/设置」可见、首屏提示正常；真机没验**。
- 语音权限那条要用户装 0.2.0 后开「＋ → 语音自检」，把内容发回来再定下一步（系统识别 / 系统面板 / 云端 ASR）。

---

## 手机 App P0 完成（2026-10-28 晚）

**状态：P0 已完成并验收，P1 没开工。** 进度看板在 `施工计划.md` 末尾。

P0 做的就是「动手前先把电脑端收拾干净」：

1. **摘 Telegram（批一）**
   - `server/run.py` 默认**不跑 bot、不推送**；要应急用回 Telegram 加 `--bot`（会让 bot 和 07:00/21:30 一起回来）。
   - 桌面端删了「机器人」页和自动重连；`HomePage` 的「推送今日任务」按钮也拿掉了。
   - `Service(d)` 默认 `push=False`：只起长轮询（仅当有人手动 start）+ 一个每分钟的「落后 → 自动重排」线程，**不再起推送线程**。
   - `bot.py` / `bot_state` / `/api/bot/*` **都没删**（批二，等手机版验收后再动）。
2. **key 只认设置**
   - `llm.api_key(db)` 不再读 `.env` / 环境变量；`llm.key_hint(db)` 给状态栏用。
   - 没 key 时状态栏右侧常驻「未配置模型 key，聊天不可用（点这里去「设置」填）」，**不挡看计划/目标/题库**；设置里清空 key 立刻出现，填回立刻消失。
   - ⚠️ **真实库里 settings 没有 api_key**（以前靠 `Documents\学习bot\.env` 兜底），所以升级后聊天会不可用：
     去「设置 → 模型」把 key 粘一次。`.env` 文件还在、没被删，但程序不再读它。
3. **「待确认」页**
   - `review_queue` 表（`SCHEMA_VERSION = 5`），字段按方案 §5.4：`kind/task_id/goal_id/day/phone_state/server_state/reason/payload_json/created_at/resolved_at/resolution`。
   - 桌面「待确认」页：列表（日期 / 目标 / 手机判定 / 电脑判定 / 原因）+ 选中看详情 + **只听电脑 / 保留手机**两个按钮；处理写 `resolved_at` + 按选的那边改卡片状态，不处理就一直挂着。
   - 左下角常显「待确认 N 条」，点一下进页；总览快捷操作也有「看待确认」。
   - 接口：`GET /api/review?pending=1`、`POST /api/review/resolve`；`RemoteDB.review_items / review_pending_count / review_resolve`。
   - **现在还没数据来源**——P3 的手机事件比对才会往里写；空状态写明了这一点。

**验收（都真跑过）**

- `py -X utf8 app\selftest.py`：**125 项全绿**（新增：无 key 提示 / `.env` 不再兜底 / v5 迁移前热备且建表 / 待确认进库与两种处理 / 页数 10）
- `py -X utf8 app\uismoke.py`：**14 项全绿**（10 页 + 待确认页空状态/列表/详情/按钮/处理完清空 + 清 key 提示 + 课程下拉）
- `py -X utf8 server\test_api.py`：**35 项全绿**（含远端待确认列表/计数/处理）
- `py -X utf8 tools\test_planner.py`：全绿
- 另用**真实库的只读副本**实测：v4 → v5 迁移无损（59 目标 / 78 卡），10 页都能开
- **没做的**：没真开一整天（「不重连、不 409」是核代码路径：桌面端已无 `_auto_start`，`Service(d)._threads == []`）

**这次踩的坑**

- `tempfile.mkdtemp()` 在 Windows 上按 0700 建目录，在 DSH 沙箱里连自己的子目录都建不进去（`WinError 5`），
  `paths.ensure()` 直接炸。三个测试脚本改成自己拼 `%TEMP%/<前缀><pid>-<ns>` 再 `mkdir()`。**不是程序问题**，你自己机器上跑不受影响。
- 这个目录**不是 git 仓库**，施工计划里「每阶段打 tag」做不到；要做得先 `git init`（我没动）。
- 真实数据目录里 `.env` 还有 key，但 P0 起不再读；这是方案 §13 定的，不是漏读。
- **打包后的 exe 还是旧的**：`%USERPROFILE%\学习bot-app\学习bot.exe` 仍是 P0 之前的版本（有机器人页、还会连 Telegram）。
  P0 没重新打包；要日常用新版就双击 `学习bot-打包.cmd` 重打一次（产物在 `dist\学习bot-app\`，脚本会拷到工作区外）。

**下一步：P1**（先等下面 4 件事，见「开工前」）

1. `android/` 工程（Kotlin + View/XML，minSdk 24 / targetSdk 34，OkHttp + Room，不引 GMS）
2. `GET /api/mobile/snapshot?rev&blocks_rev` + 块指纹 `sha1(text)[:16]` + `server/test_api.py` 补 6 条
3. 手机端四个只读页（今日 / 计划 / 目标 / 题库）+ 手动/自动同步
4. 验收：飞行模式四页可看、电脑改卡 → 同步可见、第二次同步不重传块

开工前还缺用户提供：手机型号 + 安卓版本、手机和电脑同一 WiFi 且 8787 可达、Windows 防火墙入站放行、
DeepSeek key（P2 前给到即可）。
**JDK 17 和 Android SDK 已经有了**（2026-10-28 确认）：JDK 17.0.20、SDK `C:\Android\Sdk`
（platform 34/35/36、build-tools 36.0.0、adb、licenses 已接受；**没有 emulator / 系统镜像**，所以要真机）。

---

## 手机 App P1 / P2（2026-10-28 晚，代码完成，验收还没走完）

**P1（Android 骨架 + 同步 + 四页只读）**

- `android/`：Kotlin + View/XML，minSdk 24 / targetSdk 34 / compileSdk 34，Gradle 8.4 + AGP 8.3.1 + Kotlin 2.0.0
- 12 张 Room 表（courses/goals/tasks/quiz_stats/conversations/messages/blocks/notes/arbitrations/outbox/review/meta）
- 下行快照：`GET /api/mobile/snapshot`，块指纹 `id=sha1(rel|anchor)[:16]`、`fp=sha1(text)[:16]`；
  994 块首次 883 KB，第二次（带 `blocks_rev`）66 KB，块不重传（`server/test_api.py` 42 项全绿）
- 自动同步（3 秒超时静默）+ 手动同步 + 下拉刷新 + 事务写库 + 「上次同步」，首次同步建 bigram 索引
- **真机验证**（模拟器 Android 14，用真实库副本）：同步成功，今日 11 张/500 分钟、计划 59 张、目标 59 个、题库 6 个目标有记录
- **没验**：飞行模式四页（模拟器软件模拟在切飞行模式时卡死，重启失败）、电脑改一张卡→手机同步、断网 5 分钟重开
- 签名：`android/keystore/release.jks`（口令在 `android/keystore/keystore.properties`，目录已 gitignore）；
  `assembleRelease` 出 `app-release.apk` 6.1 MB，`apksigner` 校验通过

**P2（聊天 + 检索 + 会话 + 命令菜单）**

- `sync/Retrieval.kt`：照抄 `tools/retrieval.py`（bigram、STOP、df、idf、章节加权），只查 blocks
- `sync/BigramIndex.kt`：加 `ensure()`（重启后从 Room 懒重建）、`df()`、`terms()` 带 STOP
- `chat/DeepSeek.kt`：OkHttp SSE 流式 + tool_calls 聚合
- `chat/ChatService.kt`：system prompt / 状态条 / 6000 字预算 / `plain()` 全照抄电脑端；
  `start_quiz` 占位，`skip_task` 真改本地状态并进 outbox，`show_plan` 用本地统计
- `ChatActivity`：气泡流式显示、引用、《＋》六项（考我/看计划/跳过/换卡/清空/看错题）、断网恢复草稿
- 会话：一张卡一条（按 task_id 找），本地 id 从 4e9 起（负空间留给服务器 id），同步改成 upsert 会话/消息，**不再清空本地聊天**
- 消息和任务状态都进 `outbox`（pending=1），P3 才上传
- **没验**：还没真跑过一轮聊天（设备 + key 没就位）；`assembleDebug` 编译通过

**P3 电脑端（2026-10-28 晚，已做完一半）**

- `POST /api/mobile/events`：收 `attempt` / `message` / `task_state` / `quiz`，`uid` 幂等（新表 `mobile_events`，schema **v6**，迁移前热备）
- `task_state` 分歧规则：
  - 报 done 但 `quiz_passed < 2` → 进 `review_queue`
  - 卡已被电脑改成 skipped/dropped → 进 `review_queue`
  - 其余直接落库
- `server/test_api.py` 47 项全绿（新增：事件判完成、uid 重传、只对 1 题进待确认、电脑已跳过进待确认、手机消息落库）
- 手机端还没写：本机出题/判分、上传成功后清 pending、待确认卡片弹一次

**下一步（P3 手机端 + P4）**：本机出题/判分 → 上传 outbox → 待确认展示；再把 planner 移到 Kotlin。

**P3 手机端进展（2026-10-28 晚）**：`Syncer` 现在会在下行后用同一把 token 把 `outbox` 里最多 50 条事件 POST 到 `/api/mobile/events`，成功才清 `pending`；失败留着下次重传，不影响看计划。聊天消息、跳过卡片已经在队列里了。**本机出题/判分已补（2026-10-28 晚）**：`QuizService` + `QuizActivity`，3 题（2 选 1 简答），无 key/模型挂/JSON 坏都降级成验收点简答；对 2 题置 done 并写 attempt/task_state 到 outbox。限制：题目只在内存（退出重出题要再花钱）、attempt 没存题干、今日列表要下拉刷新、未实机验证。

**P7 可信度（2026-10-28 晚，代码完成）**：聊天上下文分标【资料】/【我的笔记·标签】，system prompt 规则 9 要求冲突时调 `ask_conflict`；聊天里弹两个按钮，点了写 `arbitrations` + outbox `arbitration`，同一 concept 下次直接按裁定。**服务端没有 arbitrations 表**：裁定只存 `settings` 的 `arb:<uid>` + 日志，快照仍返回 `arbitrations: []`，不下发手机。concept 去重是字面相等；冲突按钮只在当场，重进不能补点。

**P6 语音聊天（2026-10-28 晚，代码完成）**：`voice/VoiceChat.kt`（TTS 中文 + SpeechRecognizer 连续/手动 + IDLE/LISTENING/THINKING/SPEAKING 状态机）；TTS onDone 后 300ms 开麦，连续模式用「上一句前 12 字」启发式防自激；识别不可用或连续报错 2 次自动切手动。ChatActivity 加麦克风/模式/朗读三个按钮和状态行。**没做真·免手插话**（朗读时麦克风关闭）。TTS/ASR 只能真机验。

**P5 笔记（2026-10-28 晚，代码完成）**：`NoteEditActivity`（打字/语音 SpeechRecognizer/拍照）、`FixActivity`（原/纠对照、用纠正的/用原文）、`NotesFragment`（第 6 个 tab）、`NoteRepo`（notes + outbox note 事件）；ML Kit `text-recognition-chinese:16.0.1` bundled，无需 GMS，APK 涨到 **50MB**。坑：Material `BottomNavigationView` 硬上限 5 项，第 6 个 tab 要 `SixTabBottomNavView` 把 `getMaxItemCount()` 抬到 8，否则启动崩。真机未验（语音/OCR/拍照权限）。

**P4 planner 移植（2026-10-28 晚，代码完成）**：`android/.../sync/Planner.kt` 纯函数（剩余÷天数、周末系数、15% 缓冲、day_cap、复习填空、排不下写说明）；`Replan.maybeReplan` 落后/空计划时只改本地、同一天只做一次，并写一条 `replan` outbox 当建议；电脑侧 `_apply_event` 只进 `review_queue`，`keep_phone` 才按 payload 重建服务器 tasks，`follow_server` 不动。test_api 52 项、selftest 125 项全绿。限制：只移植核心排程（没做 80/20 分段、每 4 天纯复习日、依赖拓扑）；只重排第一门课；真机未验。

**P3 手机端补齐（2026-10-28 晚）**：没过 → 明天插「错题重做」（连错 1 次）或「回炉」（连错 ≥2 次并清零）；`review` 表里的卡在今日页加「⚠ 待确认」并**只弹一次**说明。**已知问题**：本地插的明天卡会被下次同步的服务器快照覆盖（P4 做「本地重排当建议上传」时一起解决）。

---



## P8 打包检查 + 真机验收清单（2026-10-28 晚）

**已发布**：仓库 <https://github.com/yb123654789/study_chat>（只推源码/文档/构建配置；`data/`、`materials/`、`extracted/`、`shots/`、`android/keystore/` 都已 ignore）。
Release **v0.1.0**：`StudyBot-android-v0.1.0.apk`（47.8 MB）、`StudyBot-desktop-win64.zip`（13.8 MB）。

**已完成（不需要手机）**

- `assembleRelease` 出 `android/app/build/outputs/apk/release/app-release.apk`，51 MB，`apksigner` 校验通过
- APK 全 entry 扫 `sk-` 开头的串：**0 命中**
- `aapt2 dump badging`：package `com.studybot.app`、minSdk 24、targetSdk 34、label 学习bot
- 签名 keystore：`android/keystore/release.jks`（口令在 `android/keystore/keystore.properties`，不进版本库）

**要你在手机上照着点的（方案 §16，13 条）**

1. 装 APK（允许未知来源）→ 设置里填 `http://<电脑IP>:8787` + token → 保存并同步
2. 飞行模式：四个页面都能开、有数据；顶部显示「上次同步 x 分钟前」
3. 有网聊一轮：回答下面有《资料名》+ 位置
4. 「＋」六个动作都能点，点了有反应
5. 手机出 3 题、对 2 题 → 卡片当场变完成
6. 同步后电脑「学习/题库」能看到这次答题
7. 电脑先把一张卡改成 skipped → 手机答完同步 → 电脑「待确认」出现一条，手机弹一次
8. 电脑点「保留手机」→ 再同步，手机状态不变；点「听电脑」→ 手机变电脑结果
9. 落后 3 天 → 手机自己重排，提示每天加量；重排结果在电脑「待确认」里
10. 语音记一条笔记 → 能纠错 → 保存；**不出现**在资料检索结果里
11. 拍照记一条笔记 → 中文能识别、可编辑
12. 免手模式：说完自动发、念完自动听；切手动变点一下说一句；关朗读没声音；朗读中点一下立刻停
13. 造一条和资料矛盾的笔记 → 聊天弹两边说法 + 两个按钮；选一次后再聊同一概念不再问

**一天记录**：等上面跑完，把时间线（几点同步、聊了什么、哪条没过、待确认怎么处理）写回本节。

## 形态已改（2026-10-24 晚，用户拍板）

> ⏹ 这一节的「手机端只有 Telegram / 07:00 推送」已被 **2026-10-28 的手机 App 方案（v5）和 P0** 覆盖：
> Telegram 已摘（默认不起、不推送），手机端改为 Android App。下面留作历史，不要再按它做。

**不做网页了。** 电脑上一个桌面软件（`app/`，Python + Tkinter，只用标准库），
手机端只有 Telegram bot，bot 在本机长轮询跑。

- 入库、排计划、看目标/题库/进度、启停 bot、看日志，全在这一个窗口里
- 推送不用 Web Push、不用邮件：**07:00 / 21:30 由软件内的定时线程直接发 Telegram**
- 数据不上 Supabase：**本地 SQLite（`data/studybot.db`）**，单人一份，不跨设备
- 代价：**电脑关机就没有推送**。这条是唯一真正的妥协，用户已知情

这一节推翻了下面第 1、7、8、9、10 条里的 PWA / Web Push / Supabase / Cloudflare 部分，
以及 `PRD.md` §一 的「形态 / 入口 / 提醒 / 部署」四行。改需求先看这一节。

## 形态再改一次（2026-10-24 深夜，用户拍板）：后端上云

**原因**：住宿舍，晚上断电断网。电脑关机就没有 bot、没有推送，「家里放小主机」同样断电，所以只能上云。

现在的分工：

- **服务器（境外 VPS）**：SQLite + Telegram bot + 07:00/21:30 定时 + HTTP API。入口 `server/run.py`。
- **桌面软件**：瘦客户端。不跑 bot、不建库，所有数据读写都走 `app/remote.py` → HTTP。
- **开关**：桌面端 `data/desktop.json` 里写了 `server` 就走远程，没写就退回本地模式（老行为保留）。
- **密钥**：`DEEPSEEK_API_KEY` / bot token 只存在服务器上；桌面端读到的是 `****`，保存时 `****` 表示「不改」。
- **token**：服务器首次启动生成 `data/server.json` 并打印，桌面端要填同一个。
- **两条铁律**：① 切换后**只能在一处写数据**（要么本地库、要么服务器库），否则会分叉；② API 明文 HTTP 只用于先跑通，稳定后必须换 Tailscale / HTTPS / SSH 隧道（见 `server/README.md`）。

## 暂缓上云，先用本地（2026-10-24 23:50，用户决定）

- **原因**：用户手头紧，24 元/月也先缓一缓。云机器、`server/` 部署整体推迟，代码不动。
- **当前形态**：`data/desktop.json` **不存在** → `main.py` 走本地分支。代码一行没改。
- **实测（这次真测的）**：本机直连 `api.telegram.org` **超时**（宿舍网），Clash 的 7890 端口通；
  已把 `tg_proxy` 设为 `http://127.0.0.1:7890`，用库里真 token 调 `getMe` 成功（`@yb12348_bot`）。
- **本地模式的前提**：Clash 开着 + 软件开着。07:00 推送在 **11:00 前**开机补发，21:30 提醒在 **23:30 前**补发；窗口外不补。
- 开机自启**没装**（用户选的），断电重启后要手动双击 `学习bot.cmd`。
- `data/server.json` 是早先本地测 `server/run.py` 留下的，无害，先别删。
- 以后真要上云：`data/` 整份带走，桌面端**只能一处写**，别本地和服务器同时写。

## 2026-10-27 补齐当初没做的四件事（用户："把当初没做的几个功能做了吧"）

1. **落后自动重排真接上了**（`engine.auto_replan`）：启动时 + 每天第一次 tick，检查「过去日期还挂着 todo」的卡
   → 全部并回队列重排，旧的标 `dropped`；`plan_note` 和**当天推送**里写明「落后几天 / 今天几张几分 /
   复习卡砍到几张」，日志记一条「自动重排」。同一天只做一次（设置 `auto_replan_day`）。
   - 以前只有「库里一张卡都没有」才自动排，之后全靠手点「重新排计划」—— 这就是欠 5 张卡、状态条一直
     显示「落后 3 天」不归零的原因。
2. **复习卡不许抢在新课前面**：`planner.schedule` 的滚动复习只从「已经排过 learn」的目标里挑
   （原来从全部目标里挑，g03–g05 就是「没学先复习、还通过了」）。
3. **错题回炉**：没过 → 明天排一张「错题重做」（15 分钟）；**连续两批（不同题）都没过** → 不再换题
   （`regen_allowed` 直接 False，按钮也收掉），改排「回炉：」（20 分钟 learn）。
   `db.fail_streak()` 从最近一批往前数，答满 2 题且对 <2 算一次没过。
4. **带练上下文预算**：`chat.trim_history` 按 6000 字砍最旧的历史（只按 12 条截挡不住长回答），
   单段检索资料最多 1200 字。

两端同步：`/api/task/add`、`/api/fail_streak` 加在 `server/api.py`，`RemoteDB` 有 `add_task` / `fail_streak`。

## 多课程（2026-10-27 下午，用户要求：能加别的课）

- **数据**：新表 `courses(id, name, exam_date, weekend_ratio, day_cap, archived)`；
  `goals` / `tasks` 加 `course` 列（默认 `ds`），启动时 `DB._migrate()` 把老数据全部记成 ds，
  数据结构那 59 个目标、3 张已过、135 张待做**一行没动**。
- **排程**：`engine.regenerate(db, course=...)` 传课程就只排那门，不传就逐门排；
  各门用自己的考核日和周末系数，互不挤占。`plan_note:<课程>` 分开存，`plan_note` 存汇总。
- **落后**：`overdue/lag_days/auto_replan` 都按课程各算各的，欠卡的课才重排，
  推送里的重排说明带课程名。
- **界面**：顶栏课程下拉（`App.course_id()`）；学习 / 计划 / 目标 / 题库 / 资料按课过滤，
  总览分块显示全部课程；设置页拆成「这门课 / 加一门课 / 模型」三块。
- **目标来源**：`data/goals_<id>.json`（数据结构仍是 `data/goals.json`）。
  id 和别的课撞了自动改成 `<id>-g01`，依赖一起改，不会覆盖原来那门课（`db.import_goals` 里做的）。
- **资料**：新课程放 `materials/<id>/inbox/`（或整目录跑 `py tools/extract.py --course <id>`），
  抽到 `extracted/<id>/`；`retrieval.block_course()` 按目录分课，`LEGACY_DIRS`（笔记/书/PPT/试卷…）
  归数据结构。课程 id 不许用这些名字（`db.save_course` 会拒）。
- **顺带修的 planner 坑**：容量比一张卡还小的课（比如 40 分钟/天）原来整天排不出来（配额被 15% 缓冲挤没），
  现在「当天至少排一张新课」；滚动复习只在内容学完后才填空，复习卡不会再抢在学之前。
- **接口**：`/api/courses`、`/api/course`、`/api/import_goals`（带 course）、`/api/replan`（带 course）、
  `/api/task/add`、`/api/fail_streak`；`/api/state` 多了 `courses` / `stats_by_course`。

## 2026-10-27 晚：数据分三级（用户定的架构）

**一级（原始资料）**：`materials/<课>/inbox/` 是唯一入口。资料页「处理 inbox（抽文本→归档）」把它变成文本：
- PDF/PPTX/DOCX/MD/TXT → 标准库/pypdf 抽文本
- 图片 → tesseract OCR；音视频 → ffmpeg 抽音 + whisper.cpp 转写（`tools/extract.py` 里 `extract_any`）
- **成功才动原件**：文本写 `extracted/<课>/inbox/`，原件**挪进 `回收站/<课>/`**（不是硬删），
  台账 `原始资料清单.json` 记 名字/大小/哈希/字数/去向；缺工具或失败 → **原件留在 inbox**，台账记原因
- 工具路径在设置页「外部工具」（也可装到 PATH 里自动找）

**二级（课程内容）**：`知识库/<课>/<章>/<id>-<标题>.md`，每个目标一个文件，
front matter 带 `version/tags/source`，正文 = 从原文检索出来的摘录（不额外调模型，忠于资料），
另有 `_索引.md`。只在「拆目标 / 重建知识库」时写；内容变了才升版本，旧版进 `知识库/.history/<时间>/`。
**不手改**——要改就重新处理资料。

**三级（日常可改）**：`tasks`/`quiz`/`attempts`/`conversations`/`plan_note` 仍在 SQLite 里，随便改。

**界面**：新加「知识库」页（左树 章节→目标，右显示 md 正文，只读；顶部「重建知识库 / 打开文件夹」）。
回收站**只手动清**：资料页「清空回收站」（有确认框）→ `engine.clear_trash()`，不做定时自动清。

**没验证**：~~tesseract / whisper / ffmpeg 本机都没装~~（2026-10-27 晚已装，见下）；
**云端模式**下「处理资料」还没接（会直接报错，目前只有本地能跑）。

**外部工具（2026-10-27 晚已在本机装好）**：tesseract 5.4 + chi_sim、ffmpeg 9.0.2、
whisper.cpp b5454（BLAS 构建）、模型 `ggml-large-v3-turbo.bin`（1.62GB）。
- **统一放 `%LOCALAPPDATA%\studybot-tools`**（`paths.tools_dir()`），**目录名必须纯英文**：
  whisper.cpp 在 Windows 打不开中文路径（模型和音频都会 `failed to open`，踩过）。
- 设置页「外部工具」有 4 个下载链接按钮 + 「检查工具」+「打开工具文件夹」；路径都存在 settings：
  `tesseract_path / ffmpeg_path / whisper_path / whisper_model / tessdata_dir`。
- 转写传 `-t min(16, 核数)`，比默认 4 线程快 2–3 倍。
- 实测：中英文混排图片 OCR 读对了；whisper 转写跑通（TTS 音频「二叉树…」转成「二差数…」，错字是同音，
  合成音本来就含糊，真人网课会好很多）。
- **打包踩坑（重要）**：用默认 `py`（3.15）跑过测试会留下 3.15 的 `__pycache__`，
  3.14 的 PyInstaller 分析阶段直接崩（`Fatal Python error: _PyEval_EvalFrameDefault: Executing a cache`）。
  `学习bot-打包.cmd` 现在打包前会清 `app/`、`tools/` 下的 `__pycache__`；手动打包也要先清。

**处理资料是并行的、能续跑、有进度表**（2026-10-27 晚补）：
- 文本/图片并行（默认 `min(4, CPU 核数)` 路），音视频串行（whisper 自己吃满多核，并行更慢）；长视频**不设超时**
- **每份处理完立刻写台账**，中途关软件/断电，下次只处理没 done 的；同一份再放进来会被跳过并挪进回收站
- 资料页有逐份状态表（处理中带已用秒数）+ 顶部「已用 N 秒 / 完成 / 留原件 / 失败」+「取消」按钮；
  取消只保证「不再开始新的」，正在跑的那份会跑完（不强杀子进程）

## 2026-10-27 晚：容量不写死（用户指出「写死工作日/周末分钟，容易使重排失效」）

**问题**：`courses` 存 `weekday_min/weekend_min`，ds 那门课被调成 **480/480**；而 `planner` 每天的配额本来就是
「剩余量 ÷ 剩余天数」，那两个分钟数只当硬顶。结果是：落后多少，重排出来的「今天」都还是那 2 张 / 85 分钟，
旧卡只是被标成 `dropped`，进度被悄悄吃掉 —— 重排看起来天天一样，等于失效。

**现在（schema v4）**：

- `planner.schedule(goals, start, deadline, weekday_weight=1.0, weekend_weight=1.3, buffer_rate=0.15, lost_days=0, day_cap=0)`
  - 每天的量 = 剩余量 ÷ 剩余可学天数（含周末权重），**落后越多每天自动越长**；
  - 周末只按系数加权（默认 1.3），**不再有「工作日多少分钟 / 周末多少分钟」**；
  - 当天上限 = 配额 ÷ (1−15%) + 一张最大卡（卡按目标整体切、不能拆，大卡不会把一天卡死）；
  - `day_cap` 是唯一的人为硬上限（0 = 不限，最多 600）。
- `courses` 表：`weekday_min/weekend_min` → `weekend_ratio`（100~200，%）+ `day_cap`（0~600）。
  老库按 `weekend÷weekday` 比例换算，每日上限一律先设 0（不限）；`settings` 里两个旧键删掉。
  迁移在 `DB._migrate()` 的 v4 段，删列失败（老 SQLite）也不影响，代码不再读旧列。
- 界面：设置页两个框改成「周末多学 %」「每天上限分钟（0 = 不限）」，新建课程同样；提示文案写明
  「每天学多少不手填：按剩余量 ÷ 剩余天数算」。
- 重排说明（铁律 3）改成报「剩 N 分钟摊到 M 天，每天约 X 分钟」，不再拿「昨天排的今天」跟「今天重排的今天」比。
- 实测（真实 59 个目标，ds 剩 2915 分钟）：落后 14 天 → 每天约 76 分钟；再拖到剩 41 天 → 每天约 93 分钟、
  今天 3 张 135 分钟；没有目标掉出计划。
- 自检：`tools/test_planner.py` 新增 4 条（日均随截止日变、落后必加量、day_cap 必须守住、day_cap 压住要报排不下）；
  `app/selftest.py` 新增 6 条（当天上限、截止日越近日均越大、落后加量、day_cap、课程不再存固定分钟、升级前先备份）。全绿。
- **还没验证**：打包后的 exe（`%USERPROFILE%\学习bot-app` 那份还是旧逻辑），要重新跑 `学习bot-打包.cmd`。
- **追加（用户要求「把卡片的每日上限去掉」）**：`schedule` 里「一天最多 3 张卡」的硬顶删了，张数只由分钟决定；
  复习卡仍限 45 分钟 / 3 张（它只是填空，放开会一天刷出十几张）。
  没设 `day_cap` 时，截止日再紧也不会报「排不下」，而是日均一路堆上去（8 天塞 2915 分钟 ≈ 6–9 小时/天）；
  `plan_note` 里日均 > 240 分钟会加一句「时间太紧，建议砍范围或延后考核日」（只提醒，不改计划）。
- **追加：日志能清了**。之前日志页只有「清空界面」（只清文本框），库里的 `events` 一直堆。
  现在 `db.clear_logs(keep=0)`（keep=N 只留最近 N 条）+ 日志页「清空日志」按钮（有确认框）+ 远端
  `POST /api/logs/clear`；`RemoteDB.clear_logs` 同步。
- **追加：资料自己拆目标**（用户要求「程序本身具有处理原始资料的能力，而不是我把资料给你处理」）。
  `engine.derive_goals(db, course)`：把 `goals_derived_at:<课>` 之后新入库的资料按 2.4 万字切块 →
  `llm.chat`（`SYSTEM_SPLIT`）→ 校验/去重/夹估时 → 写回 `goals_<课>.json`（`paths.goals_out()`，
  非 ds 课程不会覆盖 goals.json）→ `import_goals` → 自动重排。
  资料页按钮「把新资料拆成目标」（费用确认 ¥0.1/万字），远端 `POST /api/goals/derive`。
  没新资料直接返回 0（不花钱）；模型失败**一个目标都不写**（不编）。
  **真模型还没跑过**：自检里是假模型（`fake_chat` 认「结构化专家」）。

## 应用化 + 数据层规则（2026-10-27 傍晚，用户要求「做成应用、数据剥离并封装」）

- **数据目录只有一个入口**：新文件 `app/paths.py`。
  - 解析顺序：`STUDYBOT_HOME` 环境变量 → `%LOCALAPPDATA%\学习bot\location.json` → 开发模式用项目 `data/` → 都没有就**第一次启动弹窗选目录**。
  - 目录里固定这些：`studybot.db`、`goals*.json`、`materials/`（只读）、`extracted/`、`backups/`、`exports/`、`.env`、`desktop.json`、`.app.lock`。
  - `db / engine / llm / main / ui / remote / server / tools` 全部改成走 paths，不再 `BASE/“data”` 写死。
  - 开发布局兼容：`data/` 下没有 materials/extracted 时，仍用项目根的 `materials/`、`extracted/`，`paths.rel()` 两个基目录都会试，所以老库里的 `materials/...` 相对路径不用改。
- **库结构版本化**：`db.SCHEMA_VERSION = 4`，`_migrate()` 按版本走（v3 = 课程表 + course 列，v4 = 容量改成周末系数/每日上限），每次 `init()` 盖上 `schema_version`；`init()` 之前先 `backup_before_upgrade()`（老库结构要变时自动热备一份，2026-10-27 补的，之前是先迁移后备份）。
- **备份 / 导出**：`db.backup()`（SQLite 热备，留最近 20 份，每天第一次启动自动一次）、`db.export_json()`（课程/目标/任务/题库/答题 + 设置，key 和 token 打 `****`）。设置页有「立即备份 / 导出 JSON / 打开数据文件夹」。
- **打包**：`build/学习bot-onedir.spec` + `学习bot-打包.cmd`，PyInstaller **目录版** `dist\学习bot-app\学习bot.exe`（exe + `_internal`，30MB）。
  `console=False`，崩溃写 `crash.log` + 弹窗。打包只需 `py -m pip install pyinstaller`，运行时不依赖。
  - **真正的原因（2026-10-27 晚查清）**：不是单文件/目录版的差别，是 **exe 留在 DSH 工作区里运行时被工作区限制**。
    受限制的进程只能写工作区内，于是：
    `%TEMP%` 写不了 → 单文件版报 `Could not create temporary directory!` / `Failed to extract VCRUNTIME140.dll: ... Permission denied`；
    `%LOCALAPPDATA%` 写不了 → 目录版报 `PermissionError: location.json`；
    `文档` 写不了 → 数据库打不开（`sqlite3.OperationalError: unable to open database file`）。
    **验证方式**：同一个 exe 从资源管理器启动、数据目录放工作区内 → 正常；数据目录一放到工作区外 → 立刻 PermissionError；
    把整个 `学习bot-app` 拷到 `%USERPROFILE%\学习bot-app` 再双击 + 数据目录指回 `Documents\学习bot` → 完全正常
    （`.app.lock`、`studybot.db-wal`、`backups/` 都在写，窗口活着，无 crash.log）。
    **结论：编译产物必须拷到工作区外用**，`学习bot-打包.cmd` 现在打完自动 robocopy 到 `%USERPROFILE%\学习bot-app`。
    目录版仍然保留（启动不解包、快、少一层风险），单文件版默认不用。
  - 还有个健壮性修复：`paths.save_root()` 现在存不了配置不再抛异常（之前 `%LOCALAPPDATA%` 写不进去直接把启动打断）。
  - 单文件 spec（`build/学习bot.spec`）留着，`学习bot-打包.cmd single` 仍可打，但默认不用。
  - 打包环境提醒：Python **3.14.0** + PyInstaller **6.22.3** 在分析阶段会偶发崩
    （`TypeError: 'int' object is not an iterator`，modulegraph 读字节码）；打包脚本会**自动重试一次**。
    根治办法是换 Python 3.12 打包环境，但 2026-10-27 晚尝试装 3.12 被审批拦下（改系统超出工作区），先搁置。
- **注意（踩过的坑）**：在 DSH 里直接运行工作区内的 exe 会被沙箱挡住 `%TEMP%` 写入（报 `Could not create temporary directory`），**不是程序问题**；把 exe 拷到工作区外运行就正常（已验证：首启建库、目录结构、窗口存活，无 crash.log）。
- **验证状态**：`dist\学习bot.exe` 13.4MB，在工作区外跑通启动；**没验证**的是用你的真实数据（`data\`）跑一遍——因为旧进程还占着库，两个进程同时开 bot 会 409。
- **数据已经搬过一次**（2026-10-27 14:00）：`C:\Users\yb123\Documents\学习bot\` 是新的数据目录，
  里面是 `studybot.db` + `goals.json` + `server.json` + `.env` + `materials/`（254MB）+ `extracted/`。
  验证过：59 目标 / 143 任务 / 992 块检索命中 / schema=3 / 落后 0。**旧的 `data/`、`materials/`、`extracted/` 原样没删**，
  确认 exe 用着没问题后再删。exe 第一次运行选这个目录；之后**只用一个入口**（别再用 `py app\main.py`，会写回旧 `data/`，两边分叉）。
- 自检：selftest 114 项（新增 schema 版本 / 热备份 / 导出掩码 / 数据目录归属）。

## 现在到哪了（2026-10-24）

能跑的（已自检）：

| 环节 | 产物 | 状态 |
|---|---|---|
| 资料 → 文本 | `tools/extract.py` → `extracted/`（98 文件 / 88 万字） | ✅ |
| 拆目标 | `data/goals.json`（59 个，47 核心，考核日 2027-01-04） | ✅ |
| 确定性排程 | `tools/planner.py` → `plan.md` | ✅ |
| 排程自检 | `python tools/test_planner.py` | ✅ |
| **桌面软件** | `app/`（10 页：总览/学习/计划/目标/题库/资料/知识库/**待确认**/设置/日志，顶栏课程下拉）→ `data/studybot.db` | ✅ 自检通过（P0 后） |
| **带练引擎** | `app/chat.py` + `conversations`/`messages` 两表：会话落库、状态条、检索引用、工具调用 | ✅ 自检 + **真模型跑通一轮** |
| **服务端** | `server/run.py` + `server/api.py`：HTTP API；Telegram 降为 `--bot` 应急 | ✅ 远程层自检 35 项，**还没上真机器** |
| **瘦客户端** | `app/remote.py`：桌面端所有读写走 HTTP，10 页全部跑通 | ✅ 本机联调通过 |
| **Telegram bot** | `app/bot.py` + `app/service.py`：**P0 起默认不起**，代码留到批二 | ⏸ 摘掉（未删） |
| 资料入库 | 「资料」页扫描 `materials/inbox/` → 抽文本 → `extracted/inbox/` | ✅ 哈希去重 |
| 网页预览 | `web/`（旧路线） | ⛔ 不再维护，别再往这上面加东西 |

**还没验证的**：真的连上 Telegram 收消息、定时推送在真机上的到达时间 —— **P0 起不做了**（推送取消，Telegram 只留 `--bot` 应急）。

自检命令：`py -X utf8 app\selftest.py`（125 项，离线）、`py -X utf8 app\uismoke.py`（14 项：10 个页面 + 待确认页 + key 提示 + 课程下拉，用真库副本）、
`py -X utf8 server\test_api.py`（35 项，远程层，打桩不花钱）、`py -X utf8 tools\test_planner.py`。

## 这次对话定下来的事（别推翻）

1. **科目 = 一个对话组**，组内三种窗口：资料窗口（长期一个）/ 今日窗口（每天一个）/ 自由窗口（随时开）。
2. 窗口**共享数据库，不共享上下文**；新窗口不重读原始资料，只读产物。
3. **状态条由代码渲染**（今天已学 X 分钟、落后 Y 天），模型不许复述数字；改状态必须走工具调用。
4. **完成判定不变**：小测答对 ≥2 才算完成，聊天窗口不能代替交卷。
5. **资料要支持后续增量添加**（用户没法一次找全）：
   - `content_hash` 去重；老资料的 embedding 永不重算
   - 增量重拆只喂「新资料 + 已有目标标题」，成本 = 新增字数 × ¥0.04/万字，不随累计总量涨
   - 合并三分类：补充（不新增目标）/ 新目标（追加 id）/ `included=false`（只存切片，不排进计划）
   - 增量重排冻结已完成部分；`Δ > R` 才走 PRD §五 的降级阶梯；**先出 diff，确认后落版**
6. **成本按体量报，不按课报**：拆目标 ¥0.04/万字、出题 ¥0.008/目标、批改 ¥0.004/天/科、对话窗口 ¥0.005–0.02/会话（¥3–10/月）。
7. **P0 拆两步**：P0-A 每日闭环 → P0-B 对话组 + 补料。先 A 后 B。
8. **提醒走 Web Push 优先 + 邮件兜底**，确认不用纯应用内通知 —— 应用内通知只能在「已经打开」之后起作用，正好错过要提醒的时刻。主力机安卓，iOS 换机时只需加「添加到主屏幕」引导，VAPID 不用改。
9. **电脑加料、手机日用**，只有一份数据（Supabase），不做同步。重活（PDF 解析 / embedding / 增量重拆）在电脑上用 `tools/` Python 跑，Worker 只做读库、推送、出题、批改；资料异步有状态机；diff 在电脑确认；**手机端计划数据一律 network-only，不许缓存**；时区一律 Asia/Shanghai。
10. **先做 PWA 装主屏幕，不包 APK**（用户 2026-10-24 确认）。一周后按三条标准复评：① 打开仍会看到浏览器 UI；② 通知丢过 2 次以上；③ 需要原生能力（蓝牙/后台定位）。命中任一条再上 Capacitor，代价是推送改 FCM、每次改动重装。
11. **电脑端入库不用网页**：资料丢进 `materials/inbox/`，双击 `学习bot-入库.cmd` 跑本地 Python（去重 → 抽文本 → 写库 → 增量重拆 → 出 diff）；网页只负责看 diff、确认落版、看缺料清单。不做网页上传 + 云端解析（P1 再说）。
12. **补资料的唯一入口是 `materials/inbox/`**（README 写了命名和规则）。P0-B 之前没有自动入库流程，助手在会话里代跑：改 `goals.json` + 重跑 `planner.py`。注意 `tools/extract.py` 的 `SRC` 目前写死成 `materials/scut/大二上课程资料/数据结构`，**扫不到 inbox**——补料时要手动处理或先改这个常量。
13. **支持局部出题**（用户 2026-10-24 确认）：错题可只对某个目标重出 3 题，不重排整盘。限制：同一目标 24h 内最多重出 2 次；连续两次不同题目都没过 → 停止换题，改为明天回炉 learn + `lapses+1`。通过条件仍是 3 题对 ≥2。
14. **「学习」= 聊天，不是读资料**（用户 2026-10-24 纠正）：点开任务先进聊天窗口，在对话里学会，用户说「出题」才出题，小测通过才算完成。资料原文只作为聊天上下文和引用来源，不做独立阅读页。**别再把内容页做成静态摘录。**
15. **聊天已接真模型**：`tools/serve_preview.py` 提供 `/api/chat` 代理，key 在根目录 `.env`（`DEEPSEEK_API_KEY`，已 gitignore，永不下发前端）；没有 key 自动退回本地模拟。服务只绑 `127.0.0.1`，别绑 0.0.0.0（等于把 key 借给整个局域网）。实测一轮 3–4 秒、约 ¥0.008。（桌面端现在走 `app/chat.py`，`serve_preview` 只留给旧网页预览。）
16. **资料不是拿来喂模型的，是拿来约束模型的**（用户 2026-10-24 追问）：每轮按用户的问题现检索 992 个块（`tools/retrieval.py`），把命中的 3 段连出处塞进上下文，回答下面显示引用。下一步要把**出题和批改**也基于资料——讲解错了用户能看出来，题目跑偏要到考完才知道。
17. **带练聊天已实装（2026-10-24 晚）**，`app/chat.py` 是唯一入口，桌面「学习」页和 Telegram `_tutor` 共用：
    - 会话落库：`conversations`（一张卡一条，两端同一条）+ `messages`（me / bot / tool，带 sources 与 tokens）
    - 状态条由 `chat.state_bar` 算好塞进 system prompt，模型只许引用
    - 工具调用：`start_quiz` / `skip_task` / `show_plan`；实测 `deepseek-flash` 支持 function calling
    - 模型输出统一过 `chat.plain()` 去 Markdown（Telegram 是 HTML 模式，`**` 会原样露出来）
18. **推送只在窗口内补发**：早间到点后 4 小时内、晚间 2 小时内才补，错过就记日志当天不补。
    `Service.start()` 重复点不再起第二条轮询——旧实现 `_stop.clear()` 会把旧线程复活，两条抢 getUpdates → 409。

## 待确认

- 旧路线留下的 `push_subscriptions` 不建了；`conversations` / `messages` 按聊天引擎的新设计建了（一张卡一条会话，两端共用），现在共 10 张本地表。
- **增量重拆目标**还没做：新资料入库后不会自动变成新目标。要动 `data/goals.json`（59 个目标是人工核过的），动手前先问用户。
- **PRD §六 的三种窗口只落地了一种**：现在只有「一张卡一条会话」，资料窗口 / 自由窗口还没有界面。
- 错题现在能回炉了（见上面第 3 条），但**不会自动回到「题库页」做专项练习**，只在第二天排卡。
- **多课程已支持，但新课程的内容还得手动准备**：`data/goals_<id>.json` 要人写（或助手代写），
  「增量重拆（新资料→新目标）」仍然没做，所以加一门课目前是「建课 + 自己给目标 + 给资料」。
- **Telegram 没有「当前课程」概念**：推送和 `/today` 是所有课程一起列（按课程分组），
  没有 `/course` 切换。够用，但课多了消息会变长。
- **换数据目录没有界面**：现在只能改 `%LOCALAPPDATA%\学习bot\location.json` 或设 `STUDYBOT_HOME`。
  加「搬数据到别处」要先停程序、复制整个目录，别在跑的时候搬。
- **打包在 DSH 里跑不起来是环境问题**：工作区里的 exe 被沙箱挡 `%TEMP%`，拷出去就正常。
  用户自己双击 `dist\学习bot.exe` 没这个问题。
- `web/` 目录里还有静态预览（plan.json / quiz.json / reading.json）。用户说「网页不用留着」，
  但删文件要确认，先留着当参照，别再维护。

## 下次开工第一件事

**A. 买机器、把服务端跑起来（最优先）** —— ⏸ 2026-10-24 暂缓，见上面「暂缓上云」一节

1. 买香港/日本 1核1G（¥25–45/月），Ubuntu 24.04
2. 照 `server/README.md` 传文件、跑 `server/run.py`、装 systemd、装备份
3. 桌面端填 `data/desktop.json`（server + token），双击打开，确认 10 页正常
4. 网络别裸跑 HTTP：上 Tailscale，或 Caddy + 域名

**B. 然后才是真机验证**
1. 手机上 `/start`、点卡、问一句、出题、答 3 题
2. 看 07:00 推送是否真的到（服务器不掉电，这条终于能真验）

**C. 再往后才是增量重拆**（P0-B 剩下的那一半）
输入 = 新资料文本 + 已有目标标题清单，输出 = 新目标 / 补充 / 明确不排；
先出 diff 给人确认再落版；`Δ > R` 才动排程。

## 别做的事

- 不要把大作业（C++ 排序对比实验）排进计划 —— 用户明确说不进
- 不要让模型输出任何日期或覆盖率数字
- 不要在用户确认前改 `materials/`、删 `web/`、删 `data/studybot.db`
- 不要用假数据糊一个「看起来像」的界面来凑验收
- 不要在没接 token 的情况下宣布「Telegram 已打通」——**它现在是未验证状态**

## 提醒自己

排程算法能跑，不等于产品能用。现在窗口能开、逻辑自检过了，
但**从「本地能跑」到「手机每天真的收到、真的学会」这一段，仍然要在真机上验一遍才算数**。
