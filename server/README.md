# 学习bot 服务端（VPS）

服务器上跑两件事：**SQLite + 给桌面端/手机端用的 HTTP API**。
2026-10-28（手机版 P0）起 **Telegram bot 和 07:00/21:30 推送默认不跑**，要应急才加 `--bot`。
桌面软件是瘦客户端——不建库，只显示和操作。

```
server/
  run.py        入口：起 API；默认不跑 bot（--bot 应急才起 bot + 推送）
  api.py        HTTP JSON 接口（Bearer token 鉴权）
  test_api.py   远程层自检（本机就能跑，不花钱）
  studybot.service  systemd 单元
  backup.sh     每日备份 SQLite
```

---

## 一、买机器

- 位置：**香港 / 日本 / 新加坡**。大陆机房连不上 Telegram，得再挂代理，别选。
- 配置：1 核 1G、10G 盘就够（数据总共不到 2 MB）。¥25–45/月。
- 系统：Ubuntu 22.04 / 24.04。

## 二、传东西上去

本机在项目根目录执行（把 `IP` 换成服务器地址）：

```bash
ssh root@IP 'mkdir -p /opt/studybot'
scp -r app server tools data extracted prompts .env IP:/opt/studybot/
```

`data/` 里带过去的是 `studybot.db` 和 `goals.json`；`.env` 里是 `DEEPSEEK_API_KEY`。
**不要**把 `materials/`（原始 PDF）传上去，检索只用到 `extracted/` 的文本（约 1.4 MB）。

## 三、装 Python 和依赖

只用标准库，所以没有 pip 依赖：

```bash
sudo apt update && sudo apt install -y python3 sqlite3
python3 --version        # 需要 3.10+
```

## 四、先手动跑一次

```bash
cd /opt/studybot
python3 server/run.py --port 8787 --bind 0.0.0.0
```

第一次启动会生成 **`data/server.json`** 里的访问 token，并打印出来：

```
已生成新的访问 token（写进 /opt/studybot/data/server.json）：
  OvoL9QLM3KtUkEDGBNlsk3tt4hWN3lJ7
```

把这个 token 和 `http://IP:8787` 填到**桌面端**的 `data/desktop.json`：

```json
{"server": "http://IP:8787", "token": "OvoL9QLM3KtUkEDGBNlsk3tt4hWN3lJ7"}
```

然后桌面端双击 `学习bot.cmd`，左下角应显示「待确认」条数；能打开 10 页就说明连上了。

> 现在默认**不跑 bot、不推送**（手机版 P0 摘掉了 Telegram）。
> 应急要用回 Telegram：库里先有 token，再 `python3 server/run.py --bot` 启动；
> 桌面端已经没有「机器人」页，启停只能走 `/api/bot/*` 接口。

## 五、开机自启

```bash
sudo cp /opt/studybot/server/studybot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now studybot
systemctl status studybot --no-pager
journalctl -u studybot -f          # 看日志
```

## 六、每日备份

```bash
chmod +x /opt/studybot/server/backup.sh
(crontab -l 2>/dev/null; echo "10 4 * * * /opt/studybot/server/backup.sh") | crontab -
```

备份写到 `/opt/studybot/backups/`，保留最近 14 天。数据才 0.1 MB，恢复就是 `scp` 回来覆盖 `data/studybot.db`。

## 七、网络怎么连（Tailscale，已选定）

API 里能读到你的学习记录、聊天内容，**不要用明文 HTTP 暴露到公网**。
用 Tailscale：两端组成一个加密私网，端口不用对外开，也不用买域名。

### 1. 服务器上装

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4          # 记下这个 100.x.y.z，桌面端要用
```

### 2. 电脑上装

Windows 装 Tailscale 客户端，登录**同一个账号**。装完任务栏里能看到，状态是 Connected。

### 3. 服务器只允许 tailnet 访问 API

```bash
sudo ufw default deny incoming
sudo ufw allow OpenSSH
sudo ufw allow in on tailscale0 to any port 8787
sudo ufw enable
sudo ufw status verbose
```

这样公网连 8787 是关的，只有 Tailscale 私网里能连。

### 4. 桌面端填地址

```json
{"server": "http://100.x.y.z:8787", "token": "服务器 data/server.json 里的那串"}
```

验证（电脑上）：

```powershell
curl http://100.x.y.z:8787/api/health
# {"ok": true, "service": "studybot"}
```

### 备选（没有 Tailscale 时）

| 方案 | 加密 | 成本 | 说明 |
|---|---|---|---|
| 域名 + Caddy + Let's Encrypt | ✅ | 域名 ¥30/年 | 反代到 127.0.0.1:8787 |
| SSH 隧道 | ✅ | 免费 | `ssh -L 8787:127.0.0.1:8787 root@IP`，断了要重开 |
| 明文 HTTP + token | ❌ | 免费 | 只能临时用，token 和聊天内容会裸奔 |


## 八、自检

```bash
python3 server/test_api.py     # 远程层：起临时服务 + 瘦客户端跑一遍（打桩，不花钱）
```

本机桌面端也有一套：`py -X utf8 app\selftest.py`、`app\uismoke.py`。

## 九、常见问题

- **桌面端报「连不上服务器」**：先 `curl http://IP:8787/api/health`（不需要 token），能通再看 token。
- **bot 日志里一直 409**：两个进程在抢 `getUpdates`（只有加了 `--bot` 才会发生）。`systemctl status studybot` 只能有一个 active。
- **改了代码怎么更新**：`scp` 覆盖后 `sudo systemctl restart studybot`。
- **想换机器**：把 `data/` 整个拷走就行，token 也在里面。
