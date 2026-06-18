# 小红书监控工程 - 运维手册 (OPS)

> 部署目标服务器：`dev@192.168.39.240` （Ubuntu 24.04）
> 项目目录：`/home/dev/xhs_monitor`
> 启用用户：`dev`（已配置 `loginctl enable-linger dev`，登出后服务仍运行）

---

## 1. 服务清单

| 服务 | 作用 | 触发方式 | 重启策略 |
|------|------|---------|---------|
| `xhs-monitor-bot.service` | 飞书 Bot 监听，响应用户命令 | 开机自启 | 异常自动重启 |
| `xhs-monitor-pipeline.timer` | 定时器，每 6 小时触发一次 | 开机后 2 分钟 + 每 6 小时 | - |
| `xhs-monitor-pipeline.service` | 抓取+去重+解析+推送 | 由 timer 触发 | oneshot |

---

## 2. 快速命令

```bash
# 查看所有服务状态
systemctl --user status 'xhs-monitor*'

# 查看定时器下次触发时间
systemctl --user list-timers 'xhs-monitor*'

# 立即执行一次监控（手动触发）
systemctl --user start xhs-monitor-pipeline.service

# 重启飞书 Bot
systemctl --user restart xhs-monitor-bot.service

# 停止定时器（暂停监控）
systemctl --user stop xhs-monitor-pipeline.timer

# 重新启用定时器
systemctl --user start xhs-monitor-pipeline.timer

# 查看 Bot 实时日志
journalctl --user -u xhs-monitor-bot.service -f

# 查看最近一次 pipeline 执行日志
journalctl --user -u xhs-monitor-pipeline.service -n 50

# 重置失败状态（如果 service 进入 failed）
systemctl --user reset-failed xhs-monitor-pipeline.service
```

---

## 3. 部署步骤（首次或重装）

### 3.1 准备环境

```bash
# 远程登录
ssh dev@192.168.39.240

# 安装 Python 依赖（用户级，避免污染系统）
python3 -m pip install --user --break-system-packages playwright requests

# 安装飞书 CLI
mkdir -p ~/.npm-global
npm config set prefix ~/.npm-global
npm install -g @larksuite/cli

# 配置 PATH（加到 ~/.bashrc）
echo 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

### 3.2 配置 lark-cli 认证

```bash
# 方式 A：用户提供 App Secret（需要用户提供）
echo "<APP_SECRET>" | lark-cli config init \
  --app-id cli_aab9e9b37f3bdcfc \
  --app-secret-stdin \
  --brand feishu

# 方式 B：创建新应用
lark-cli config init --new   # 会输出验证 URL，引导用户浏览器授权
```

### 3.3 部署项目代码

```bash
# 在本地打包并传输
cd "C:\Users\chaji\WorkBuddy\2026-06-18-10-11-38"
tar czf /tmp/xhs_monitor.tar.gz \
  --exclude='xhs_monitor/venv' \
  --exclude='xhs_monitor/db' \
  --exclude='xhs_monitor/output' \
  xhs_monitor/*.py xhs_monitor/*.md xhs_monitor/*.txt \
  xhs_monitor/cookies xhs_monitor/tasks.json

scp /tmp/xhs_monitor.tar.gz dev@192.168.39.240:/tmp/

# 远程解压
ssh dev@192.168.39.240 'mkdir -p ~/xhs_monitor && \
  tar xzf /tmp/xhs_monitor.tar.gz -C ~/xhs_monitor --strip-components=1'
```

### 3.4 创建 systemd 服务

`~/.config/systemd/user/xhs-monitor-bot.service`:

```ini
[Unit]
Description=XiaoHongShu Monitor - Feishu Bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/dev/xhs_monitor
Environment=PATH=/home/dev/.npm-global/bin:/home/dev/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUTF8=1
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /home/dev/xhs_monitor/bot.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

`~/.config/systemd/user/xhs-monitor-pipeline.service`:

```ini
[Unit]
Description=XiaoHongShu Monitor - Pipeline (Search + Push)

[Service]
Type=oneshot
WorkingDirectory=/home/dev/xhs_monitor
Environment=PATH=/home/dev/.npm-global/bin:/home/dev/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUTF8=1
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /home/dev/xhs_monitor/manage.py run
StandardOutput=journal
StandardError=journal
TimeoutStartSec=900
```

`~/.config/systemd/user/xhs-monitor-pipeline.timer`:

```ini
[Unit]
Description=XiaoHongShu Monitor - Pipeline Timer (every 6h)

[Timer]
OnBootSec=2min
OnUnitActiveSec=6h
AccuracySec=1min

[Install]
WantedBy=timers.target
```

启用：

```bash
systemctl --user daemon-reload
systemctl --user enable xhs-monitor-bot.service xhs-monitor-pipeline.timer
systemctl --user start xhs-monitor-bot.service xhs-monitor-pipeline.timer
loginctl enable-linger dev   # 用户登出后服务不停止
```

---

## 4. 维护操作

### 4.1 修改监控关键词

**方式 A：飞书私聊 bot**

```
/add AI Agent招聘 RAG工程师
/list
/delete 1
```

**方式 B：编辑配置文件**

```bash
ssh dev@192.168.39.240
nano ~/xhs_monitor/tasks.json
# 立即生效，无需重启 bot
```

### 4.2 修改推送目标

```bash
# 推送给指定用户（默认是 AUTHORIZED_USER）
python3 manage.py config --push-user ou_xxxxxxxxxxxxxxx

# 推送到群聊
python3 manage.py config --push-chat oc_xxxxxxxxxxxxxxx

# 推送到默认（自己）
python3 manage.py config --push-user "" --push-chat ""
```

### 4.3 修改每词最大抓取数

```bash
# 单次运行覆盖
python3 manage.py run --max 15

# 永久修改
python3 manage.py config --max 15
```

### 4.4 更换小红书登录 Cookie

```bash
ssh dev@192.168.39.240
cd ~/xhs_monitor
# 必须用有头浏览器（Linux 无 GUI 时可临时用 X11 forward 或 VNC）
DISPLAY=:0 python3 save_cookies.py
# 手动登录后回车，Cookie 自动保存
```

> Cookie 失效时会话过期。**建议用 `playwright install firefox` 备选或保持 Cookie 不超过 7 天。**

### 4.5 修改推送频率

```bash
# 查看当前
systemctl --user cat xhs-monitor-pipeline.timer

# 修改为每 3 小时
sed -i 's/OnUnitActiveSec=6h/OnUnitActiveSec=3h/' \
  ~/.config/systemd/user/xhs-monitor-pipeline.timer
systemctl --user daemon-reload
systemctl --user restart xhs-monitor-pipeline.timer
```

---

## 5. 常见故障排除

### 5.1 Bot 一直重启

**症状**：journalctl 持续输出 "异常退出(返回码 3)，5秒后重启"

**根因**：lark-cli 拿不到飞书 token（DNS 解析或 App Secret 失效）

**排查**：

```bash
# 1. 测试网络
nslookup accounts.feishu.cn

# 2. 测试 token 获取
lark-cli im +messages-send --user-id ou_xxx --text "test" --as bot

# 3. 重新配置
echo "<NEW_SECRET>" | lark-cli config init \
  --app-id cli_aab9e9b37f3bdcfc --app-secret-stdin --brand feishu

# 4. 重启
systemctl --user restart xhs-monitor-bot.service
```

### 5.2 Pipeline 失败（搜索不到结果）

**症状**：pipeline 日志出现 `Page.goto: Timeout 30000ms exceeded`

**根因**：网络波动或小红书反爬

**解决**：

```bash
# 1. 单独测试一个关键词
cd ~/xhs_monitor
python3 manage.py run --keyword "测试词" --max 3

# 2. 如果持续失败，重试（每次间隔 30 秒）
sleep 30
python3 manage.py run

# 3. 检查 Cookie 是否过期
ls -la cookies/xhs_cookies.json
# 超过 7 天建议重新获取
```

### 5.3 服务无法启动

**症状**：`systemctl --user status` 显示 `failed`

**排查**：

```bash
# 1. 详细日志
journalctl --user -u xhs-monitor-bot.service -n 100

# 2. 手动运行看错误
python3 ~/xhs_monitor/bot.py

# 3. 重置失败状态
systemctl --user reset-failed xhs-monitor-bot.service
systemctl --user start xhs-monitor-bot.service
```

### 5.4 主机重启后服务没起

**原因**：`loginctl enable-linger dev` 未设置，SSH 断开后 user systemd 停止

**解决**：

```bash
loginctl enable-linger dev
```

### 5.5 推送失败（消息没收到）

**排查**：

```bash
# 1. 测试 bot 身份
lark-cli im +messages-send --user-id ou_xxx --text "测试" --as bot

# 2. 检查 scope（需要在开放平台开启 im:message.p2p_msg 权限）
lark-cli auth scopes --as bot

# 3. 查看 push 错误
cd ~/xhs_monitor
python3 push_feishu.py --dry-run   # 先预览不发送
python3 push_feishu.py             # 实际发送
```

---

## 6. 数据管理

### 6.1 备份关键数据

```bash
# 备份 Cookie、tasks.json、SQLite 数据库
cd ~/xhs_monitor
tar czf ~/xhs_monitor_backup_$(date +%Y%m%d).tar.gz \
  cookies/ tasks.json db/ output/
```

### 6.2 清空去重记录（强制重新抓取所有帖子）

```bash
# 警告：这会清空所有历史记录，下次运行会全部推送
rm ~/xhs_monitor/db/monitor.db
```

### 6.3 导出历史记录

```bash
cd ~/xhs_monitor
python3 manage.py history --limit 100 > ~/history_$(date +%Y%m%d).txt
```

---

## 7. 监控指标

| 指标 | 查看方式 | 告警阈值 |
|------|---------|---------|
| Bot 状态 | `systemctl --user is-active xhs-monitor-bot.service` | 非 active |
| Pipeline 状态 | `systemctl --user status xhs-monitor-pipeline.service` | failed |
| 最近推送时间 | `python3 manage.py status` | 超过 7 小时 |
| 数据库大小 | `ls -la db/monitor.db` | > 100MB 需清理 |

---

## 8. 升级 / 回滚

```bash
# 升级代码
cd ~/xhs_monitor
# 备份
cp -r ~/xhs_monitor ~/xhs_monitor.bak
# 拉新代码（如果有 git）或 scp 传入
scp ... .

# 回滚
systemctl --user stop xhs-monitor-bot.service xhs-monitor-pipeline.timer
rm -rf ~/xhs_monitor
mv ~/xhs_monitor.bak ~/xhs_monitor
systemctl --user start xhs-monitor-bot.service xhs-monitor-pipeline.timer
```

---

## 9. 联系与升级流程

1. 用户通过飞书 Bot 发送 `/add xxx` 添加新关键词
2. Bot 立即把关键词写入 `tasks.json`
3. 下次 pipeline 触发（最多 6 小时）即生效
4. 紧急执行：飞书发 `/run`
5. 关键词修改无需重启服务

---

*最后更新：2026-06-18*
