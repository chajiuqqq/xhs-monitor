# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# 小红书监控工程

LLM 驱动的小红书帖子监控系统：飞书 Bot 管理关键词 → Playwright 搜索 → SQLite 去重 → SSR 数据解析 → 飞书推送。

## 常用命令

### 任务管理（CLI）
```bash
python manage.py list                              # 查看所有监控关键词、任务组和数据库统计
python manage.py add "AI Agent 招聘"               # 添加关键词
python manage.py add "kw1" "kw2"                   # 批量添加
python manage.py remove "关键词"                   # 删除关键词
python manage.py config --max 15                   # 修改每词最大抓取数
python manage.py config --push-user ou_xxx         # 修改推送目标用户
python manage.py run                               # 立即执行监控 + 推送（全部）
python manage.py run --keyword "AI" --max 3        # 只跑该关键词
python manage.py run --task agent_jd_30w           # 只跑该任务组
python manage.py run --dry-run                     # 仅预览不实际推送
python manage.py run --force-push                  # 无新帖也推送
python manage.py run --no-llm                      # 强制跳过 LLM 过滤
python manage.py task list                         # 查看所有任务组
python manage.py task show <name>                  # 任务组详情
python manage.py task remove <name>                # 删除任务组
python manage.py status                            # 查看运行统计
python manage.py history --limit 20                # 查看历史记录
```

### 飞书 Bot
```bash
python bot.py                                      # 启动飞书 Bot 监听
# 飞书私聊 bot 命令:
#   /list                     查看所有关键词 + 任务组（统一编号）
#   /add 关键词               添加监控关键词
#   /delete 1 / 关键词        删除关键词
#   /task <自然语言描述>      LLM 拆解（自动命名）
#   /task 名称 | 描述         LLM 拆解（指定名称）
#   /tasks                    查看所有任务组
#   /task-remove <名称>       删除任务组
#   /status                   查看监控运行状态
#   /run                      立即执行监控（全部）
#   /run 1 / k1 / t1          跑指定项（全局编号 / 关键词 / 任务组）
#   /run <名称>               按文本运行
#   /help                     查看帮助
```

### 抓取 / 调试
```bash
python save_cookies.py                             # 有头浏览器手动登录保存 Cookie
python pipeline.py '["关键词1", "关键词2"]' --max 10  # 直接执行 pipeline（不推送）
python push_feishu.py --dry-run                    # 预览推送内容
python push_feishu.py                              # 实际推送
```

### 服务管理（部署机 dev@192.168.39.240）
```bash
systemctl --user status 'xhs-monitor*'             # 查看所有服务状态
systemctl --user start xhs-monitor-pipeline.service  # 手动触发一次 pipeline
journalctl --user -u xhs-monitor-bot.service -f    # 实时日志
```

## 架构总览

六个关键节点（详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)）：

1. **任务配置** — `tasks.json` 双轨并存：`keywords[]`（老路径，无 LLM 过滤）+ `tasks[]`（新路径，LLM 拆解 + 智能过滤）；飞书 Bot 命令（`/add`、`/task`）直接读写
2. **LLM 拆解** — `llm.py` 用 DeepSeek/openai 兼容端点，自然语言需求 → {name, keywords, filter}
3. **小红书搜索** — `searcher.py` 单浏览器实例，搜索 + 详情一体化（避免反复启动）
4. **增量去重** — `dedup.py` SQLite 双层去重：noteId 主键 + content_hash 内容指纹
5. **内容解析** — `content_parser.py` 从 Playwright 提取的 SSR 结构化数据（`window.__INITIAL_STATE__.note.noteDetailMap[noteId]`）整理字段
6. **LLM 二次过滤 + 飞书推送** — `llm.filter_posts()` 按 task.filter 二次筛选 → `push_feishu.py` 用 `lark-cli im +messages-send --as bot` 发送 Markdown 简报

数据流：飞书 Bot 命令 → tasks.json → pipeline（搜索→去重→解析→LLM 过滤）→ `output/latest_result.json` → push_feishu → 飞书 P2P 私聊

## 关键模块职责

| 文件 | 职责 |
|------|------|
| [bot.py](bot.py) | 飞书 Bot：lark-cli 事件监听 + 命令处理；权限校验（`AUTHORIZED_USER`）；`/run [id]` 异步启动 pipeline |
| [manage.py](manage.py) | CLI 任务管理（list/add/remove/run/status/history/task）+ subprocess 调用 pipeline + push；`--keyword` / `--task` 单选模式 |
| [pipeline.py](pipeline.py) | 主流程编排：双路径（keywords 走 A，tasks 走 B+LLM）→ 搜索→去重→解析→(LLM 过滤)→ 写 `output/latest_result.json` |
| [searcher.py](searcher.py) | Playwright 搜索结果页 + 详情页（单浏览器实例、SSR 解析） |
| [content_parser.py](content_parser.py) | SSR 数据 → 结构化字段（title/desc/tags/imageList/video） |
| [dedup.py](dedup.py) | SQLite 表 `seen_notes`（noteId PRIMARY KEY + content_hash 索引；status 支持 new/new_llm_pending/filtered_by_llm/dup_content/parse_error） |
| [llm.py](llm.py) | OpenAI 兼容 LLM 封装：`decompose_task(description)` → {name, keywords, filter}；`filter_posts(posts, criteria)` → 命中帖子；异常降级返回原列表 |
| [push_feishu.py](push_feishu.py) | Markdown 简报渲染 + lark-cli 发送（长文本走临时文件 + `$(cat)`；LLM 命中帖子追加匹配理由） |
| [config.py](config.py) | 全局配置：路径/选择器/浏览器/header/LLM 端点；跨平台浏览器检测（Win/Linux） |
| [save_cookies.py](save_cookies.py) | 有头浏览器手动登录保存 Cookie 到 `cookies/xhs_cookies.json` |

## 重要约定

- **跨平台 Python 解释器**：[bot.py:42-46](bot.py#L42-L46) 和 [manage.py:39-43](manage.py#L39-L43) 分别硬编码 Windows venv 路径和优先使用 Linux `venv/bin/python`（fallback `sys.executable`）
- **跨平台 lark-cli 调用**：[bot.py:78-102](bot.py#L78-L102) 和 [push_feishu.py:105-130](push_feishu.py#L105-L130) — Windows 必须走 Git Bash，Linux 直接 shell 执行
- **PATH 注入**：[manage.py:46-68](manage.py#L46-L68) `_build_subprocess_env()` 显式追加 `~/.npm-global/bin`、`~/.local/bin` 等路径（subprocess 不会继承 systemd 启动时的完整 PATH）
- **xsec_token 必须保留**：[searcher.py:106-118](searcher.py#L106-L118) 搜索结果 URL 形如 `/search_result/{noteId}?xsec_token=xxx`，必须带 token 访问详情页
- **SSR 解析 ≠ 正则**：[searcher.py:159-183](searcher.py#L159-L183) 用 `page.evaluate` 直接读 `window.__INITIAL_STATE__.note.noteDetailMap[noteId]`，避免正则误匹配页面底部备案信息
- **DOM 选择器**：[config.py:60-66](config.py#L60-L66) `SELECTORS` 集中管理，小红书改版只需改这里
- **推送条件**：[manage.py:240-242](manage.py#L240-L242) 仅在有新增时推送，无新增静默（`--force-push` 覆盖）

## 数据存储

- [tasks.json](tasks.json) — 双轨配置（`keywords[]` + `tasks[]`），含 `max_per_keyword`、`push_user_id`
- [db/monitor.db](db/) — SQLite 去重库（表 `seen_notes`）
- [cookies/xhs_cookies.json](cookies/) — 小红书登录态
- [output/latest_result.json](output/) — 每次 pipeline 输出
- [output/_feishu_msg.md](output/) — 飞书推送临时文件（避免 shell 参数截断）
- [output/_bot_reply.txt](output/) — Bot 回复临时文件
- [.env](.env) — LLM API key（不提交；systemd EnvironmentFile 注入）

## 部署

- 主机：`dev@192.168.39.240`（Ubuntu 24.04）
- 目录：`~/xhs_monitor`
- systemd --user 服务：`xhs-monitor-bot.service`（常驻）+ `xhs-monitor-pipeline.timer`（每 6h）
- 已启用 `loginctl enable-linger dev`（登出后服务不停止）

完整部署/排错/备份见 [docs/OPS.md](docs/OPS.md)；架构图见 [docs/architecture.svg](docs/architecture.svg) / [docs/deployment.svg](docs/deployment.svg)。

## 故障速查

| 现象 | 原因 | 解决 |
|------|------|------|
| 搜索结果为空 | Cookie 过期 | 重新运行 `python save_cookies.py` |
| Bot 一直重启 | lark-cli token 失效 | 重跑 `lark-cli config init` |
| Pipeline timeout | 网络/反爬 | 单独 `manage.py run --keyword "测试" --max 3` 重试 |
| 小红书改版 | DOM 选择器失效 | 更新 [config.py:60-66](config.py#L60-L66) `SELECTORS` |
| `lark-cli: command not found` | 子进程 PATH 不全 | 确认 systemd service unit 显式设置 `Environment=PATH=...` |
