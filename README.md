# 小红书监控工程

LLM 驱动的小红书帖子监控系统。用户输入需求 → 飞书 Bot 管理关键词 → Playwright 定期搜索 → SQLite 去重 → 内容解析 → 飞书推送。

---

## 项目结构

```
xhs_monitor/
├── bot.py                 # 飞书 Bot（命令处理 + 事件监听）
├── config.py              # 全局配置（路径/选择器/浏览器）
├── content_parser.py      # SSR 数据 → 结构化字段
├── dedup.py               # SQLite 双层去重
├── manage.py              # CLI 任务管理（list/add/remove/run/status）
├── pipeline.py            # 主流程编排
├── push_feishu.py         # 飞书推送
├── save_cookies.py        # 小红书 Cookie 获取
├── tasks.json             # 监控关键词配置
├── cookies/               # 登录态
├── db/                    # SQLite 去重库
├── output/                # 抓取结果
└── docs/
    ├── OPS.md             # 运维手册（部署/排错/备份）
    ├── ARCHITECTURE.md    # 架构总览
    ├── architecture.svg   # 关键节点流程图
    └── deployment.svg     # 部署架构图
```

## 飞书 Bot 命令

在飞书私聊 bot 发送：

| 命令 | 作用 |
|------|------|
| `/list` | 查看所有监控关键词 |
| `/add 关键词1 关键词2` | 添加监控关键词 |
| `/delete 1` 或 `/delete 关键词` | 删除监控关键词 |
| `/status` | 查看监控运行状态 |
| `/run` | 立即执行监控 |
| `/help` | 查看帮助 |

授权用户：`ou_8ce2c84aa949a2b28cae7d7de3b0a0c6`

## 远程部署

- 主机：`dev@192.168.39.240` (Ubuntu 24.04)
- 项目目录：`~/xhs_monitor`
- systemd 服务：`xhs-monitor-bot.service` + `xhs-monitor-pipeline.timer`
- 启用 linger：`loginctl enable-linger dev`

详见 [docs/OPS.md](docs/OPS.md) 和 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 本地运行

```bash
# 安装依赖
python -m pip install playwright requests

# 获取 Cookie（有头浏览器手动登录）
python save_cookies.py

# 运行监控
python manage.py add "AI Agent 招聘"
python manage.py run
```

## 关键技术决策

- **搜索**：Playwright + 系统 Chrome（Windows channel=chrome，Linux executable_path=/snap/bin/chromium）
- **详情解析**：`page.evaluate` 解析 `window.__INITIAL_STATE__.note.noteDetailMap[noteId]`，不用 requests（被反爬），不用正则（匹配到备案信息）
- **xsec_token**：搜索结果链接 `/search_result/{noteId}?xsec_token=xxx` 必须带 token 访问
- **去重**：SQLite 双层（noteId 主键 + content_hash 内容指纹）
- **推送**：lark-cli im +messages-send，bot 身份 P2P 私聊
- **跨平台**：Windows 用 Git Bash 执行 lark-cli，Linux 直接执行
- **进程管理**：systemd --user + linger，支持主机重启后自动恢复
